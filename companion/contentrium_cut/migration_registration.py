"""Attempt-bound rollback of the legacy migration's two owned registry values."""
from pathlib import Path
from .contract import CutError, canonical_hash
from .integration import _registered_command, protocol_command, startup_command, LAUNCHER_NAME
from .updater import _json
from .windows_install import _guard_path, _atomic_json

PROTOCOL = r'Software\Classes\contentrium-cut\shell\open\command'
RUN = r'Software\Microsoft\Windows\CurrentVersion\Run'
RUN_NAME = 'Contentrium CUT'


def _fail():
    raise CutError('MIGRATION_REGISTRATION', 'Owned migration registration differs; preserve it for explicit recovery.')


def _read(path):
    _guard_path(path)
    if path.stat().st_size > 16 * 1024 * 1024: _fail()
    return _json(path.read_bytes())


def _path(root, snapshot):
    path = root/'updates/migration-registration'/canonical_hash(snapshot)/'migration-registration.json'
    _guard_path(path)
    return path


def _binding(root, snapshot):
    journal = _read(root/'updates/journal.json')
    if (journal.get('snapshot') != snapshot or snapshot.get('previousVersion') != '0.1.0'
            or journal.get('oldVersion') != '0.1.0' or journal.get('newVersion') != '0.1.1'):
        _fail()
    return dict(schemaVersion=1, productId='com.contentrium.cut', canonicalRoot=str(root),
        snapshotHash=canonical_hash(snapshot), updateId=journal['updateId'], updateEpoch=journal['updateEpoch'],
        manifestDigest=journal['manifestDigest']), journal['updateState']


def _validate(root, value, binding):
    fields = set(binding) | {'beforeProtocol','afterProtocol','beforeRun','afterRun'}
    if (set(value) != fields or any(value.get(key) != item for key,item in binding.items())
            or value['beforeProtocol'] != '"'+str(root/LAUNCHER_NAME)+'" --open'
            or value['afterProtocol'] != protocol_command(root) or value['afterRun'] != startup_command(root)
            or value['beforeRun'] not in (None, value['afterRun'])):
        _fail()


def _current(registry, value):
    protocol = _registered_command(registry, PROTOCOL, '')
    startup = _registered_command(registry, RUN, RUN_NAME)
    if protocol not in (value['beforeProtocol'], value['afterProtocol']) or startup not in (value['beforeRun'], value['afterRun']):
        _fail()
    return protocol, startup


def prepare(root, snapshot, *, registry=None):
    """Persist owned before/after intent before the first integration write."""
    root = Path(root)
    binding, phase = _binding(root, snapshot)
    if phase != 'PENDING_ACTIVATION': _fail()
    if registry is None:
        import winreg as registry
    path = _path(root, snapshot)
    if path.exists():
        value = _read(path); _validate(root, value, binding); _current(registry, value)
        return value
    value = dict(binding, beforeProtocol=_registered_command(registry, PROTOCOL, ''),
        beforeRun=_registered_command(registry, RUN, RUN_NAME),
        afterProtocol=protocol_command(root), afterRun=startup_command(root))
    _validate(root, value, binding)
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_json(path, value)
    return value


def restore(root, snapshot, *, registry=None):
    """No history from a different snapshot can affect an ordinary later update.

    Both values are checked before either is touched. Each write also rechecks
    its value using the same writable handle. StartupApproved is never modified.
    The immutable intent permits retry after either individual write interrupted.
    """
    root = Path(root); path = _path(root, snapshot)
    if not path.exists(): return None
    binding, phase = _binding(root, snapshot)
    if phase not in {'ROLLING_BACK','ROLLED_BACK'}: _fail()
    value = _read(path); _validate(root, value, binding)
    if registry is None:
        import winreg as registry
    protocol, startup = _current(registry, value)
    for key, name, current, before, after in [
            (PROTOCOL, '', protocol, value['beforeProtocol'], value['afterProtocol']),
            (RUN, RUN_NAME, startup, value['beforeRun'], value['afterRun'])]:
        if current == before: continue
        with registry.CreateKey(registry.HKEY_CURRENT_USER, key) as handle:
            actual, kind = registry.QueryValueEx(handle, name)
            if actual != after or kind != registry.REG_SZ: _fail()
            if before is None: registry.DeleteValue(handle, name)
            else: registry.SetValueEx(handle, name, 0, registry.REG_SZ, before)
    if _current(registry, value) != (value['beforeProtocol'], value['beforeRun']): _fail()
    return dict(restored=True, snapshotHash=binding['snapshotHash'], updateId=binding['updateId'])
