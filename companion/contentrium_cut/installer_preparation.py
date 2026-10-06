"""Explicit installer-only own-folder enrollment and verified bundled Silero.

The receipt records native API provenance, not proof against same-user malware.
No process start, network, provider terms, model inference or activation ticket.
"""
from pathlib import Path
import re
import secrets
import shutil
import tempfile
import time

from . import bootstrap
from .contract import CutError
from .updater import SemVer
from .windows_install import WindowsInstallation, _atomic_json, _guard_path, _hash

CHALLENGE = 'contentrium-install-challenge.json'
RECEIPT = 'contentrium-install-receipt.json'
STATE = 'install-preparation.json'
LIFETIME = 600


def _fail(code='INSTALL_ENROLLMENT'):
    raise CutError(code, '설치 준비 정보를 검증하지 못했습니다. 원래 설치의 복구가 필요합니다.')


def _read(path):
    try: return bootstrap._read(path)
    except CutError: _fail()


def _mapping(data, receipt_hash):
    return dict(schemaVersion=1, productId=bootstrap.PRODUCT, hostMajor=26,
                verifiedBy='installed-uxp-getDataFolder', canonicalPluginData=str(data), receiptHash=receipt_hash)


def _receipt(path, challenge, data, *, incomplete=False):
    try: value = bootstrap._read(path)
    except CutError as error:
        # UXP creates an empty File before awaiting write(). Only the current
        # owned challenge may grant a bounded GUI read grace, never a rewrite.
        if incomplete and error.code == 'BOOTSTRAP_INVALID' and path.is_file() and path.stat().st_size <= 4096:
            raw = path.read_bytes().strip()
            if not raw or (raw.startswith(b'{') and not raw.endswith(b'}')):
                raise CutError('INSTALL_RECEIPT_INCOMPLETE','패널의 설치 확인 기록을 작성하고 있습니다.') from None
        _fail()
    if (not isinstance(value, dict) or set(value) != set(challenge) | {'verifiedBy', 'nativePath'} or
            any(type(value.get(key)) is not type(item) or value.get(key) != item for key, item in challenge.items()) or
            value.get('verifiedBy') != 'installed-uxp-getDataFolder' or
            not isinstance(value.get('nativePath'), str) or bootstrap.canonical_root(value['nativePath']) != data):
        _fail()
    return value


def _challenge(value, target, data):
    fields = {'schemaVersion', 'productId', 'hostMajor', 'appVersion', 'bundleId',
              'nonce', 'issuedAt', 'expiresAt', 'canonicalPluginData'}
    if (not isinstance(value, dict) or set(value) != fields or type(value.get('schemaVersion')) is not int or value['schemaVersion'] != 1 or
            value.get('productId') != bootstrap.PRODUCT or type(value.get('hostMajor')) is not int or value['hostMajor'] != 26 or
            any(value.get(key) != target[key] for key in ('appVersion', 'bundleId')) or
            not isinstance(value.get('nonce'), str) or not re.fullmatch('[0-9a-f]{64}', value['nonce']) or
            type(value.get('issuedAt')) is not int or type(value.get('expiresAt')) is not int or
            value['expiresAt'] - value['issuedAt'] != LIFETIME or value.get('canonicalPluginData') != str(data)):
        _fail()
    return value


def prepare_silero(root, version, *, verify):
    """Only the fixed PyInstaller bundled directory; ModelManager is lazy here."""
    from .models import ModelManager
    source = root / 'app' / 'versions' / version / '_internal' / 'silero'
    destination = root / 'models' / 'silero'
    _guard_path(source); _guard_path(destination)
    if verify() is not True: _fail('INSTALL_PREPARATION_VERIFY')
    actual = set()
    for path in source.rglob('*'):
        _guard_path(path)
        if path.is_file(): actual.add(path.relative_to(source).as_posix())
    bundled = ModelManager(source.parent).state('silero')
    if bundled['status'] != 'ready': _fail('INSTALL_MODEL_BUNDLE')
    expected = set(bundled['manifest']['files']) | {'manifest.json'}
    if actual != expected: _fail('INSTALL_MODEL_BUNDLE')
    hashes = {name: _hash(source / name) for name in actual}
    if destination.exists():
        for path in destination.rglob('*'): _guard_path(path)
        current = ModelManager(destination.parent).state('silero')
        if current['status'] != 'ready': _fail('INSTALL_MODEL_CONFLICT')
        return dict(modelId='silero', action='retained', revision=current['revision'], manifestSha256=_hash(destination / 'manifest.json'))
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.silero-prepare-', dir=destination.parent) as temporary:
        staging = Path(temporary) / 'silero'
        shutil.copytree(source, staging)
        if (ModelManager(Path(temporary)).state('silero')['status'] != 'ready' or
                any(_hash(staging / name) != digest for name, digest in hashes.items()) or verify() is not True):
            _fail('INSTALL_MODEL_BUNDLE')
        if destination.exists(): _fail('INSTALL_MODEL_CONFLICT')
        # On the supported Windows host rename refuses any existing directory.
        staging.rename(destination)
    return dict(modelId='silero', action='installed', revision=bundled['revision'], manifestSha256=hashes['manifest.json'])


def prepare_installation(root, config, *, installation=None, mapping_evidence=None,
                         owner_sid=None, roaming_root=None, protect=None, clock=time.time):
    """One bounded step: PENDING_PROVISIONING or PREPARED with mappingEvidence.

Caller owns the installation/update lease and the signed target selection. This
step is also usable after UpdateManager PENDING_ACTIVATION, without first-install.
"""
    root = bootstrap.canonical_root(root)
    try:
        version, bundle = config['appVersion'], config['bundleId']; SemVer(version)
        if not isinstance(bundle, str) or not re.fullmatch('[A-Za-z0-9._-]{3,100}', bundle): raise ValueError()
        if config.get('productId') != bootstrap.PRODUCT: raise ValueError()
    except (KeyError, ValueError, TypeError): _fail('INSTALL_PREPARATION_VERIFY')
    installed = installation or WindowsInstallation(root)
    verify = lambda: installed.verify_application(version, bundle)
    if verify() is not True: _fail('INSTALL_PREPARATION_VERIFY')
    data = bootstrap.canonical_root(roaming_root or bootstrap.known_folder('roaming')) / 'Adobe/UXP/PluginsStorage/PPRO/26/External/com.contentrium.cut/PluginData'
    _guard_path(data)
    challenge_path, receipt_path = data / CHALLENGE, data / RECEIPT
    state_path = root / 'updates' / STATE
    for path in (challenge_path, receipt_path, state_path): _guard_path(path)
    target = dict(schemaVersion=1, productId=bootstrap.PRODUCT, appVersion=version, bundleId=bundle)
    state = _read(state_path) if state_path.exists() else None
    if state is not None:
        if (not isinstance(state, dict) or state.get('schemaVersion') != 1 or state.get('productId') != bootstrap.PRODUCT or
                state.get('state') not in {'PENDING', 'PROVISIONING', 'PREPARED'}): _fail()
        if any(state.get(key) != value for key, value in target.items()) and state['state'] != 'PREPARED': _fail()
    options = dict(owner_sid=owner_sid, roaming_root=roaming_root)
    if protect is not None: options['protect'] = protect
    private_present = (root / bootstrap.PRIVATE_PATH).exists() or (root / 'install-location.json').exists()
    if private_present:
        # Valid retained bootstrap skips enrollment. Missing/conflicting copies
        # remain a repair error, never authority to replace private material.
        location = bootstrap.resolve_installation(root, owner_sid=owner_sid)
        value = bootstrap.load_bootstrap(location)
        if not (data / bootstrap.BOOTSTRAP_NAME).exists(): _fail('BOOTSTRAP_CONFLICT')
        if owner_sid is None: bootstrap.verify_private_protection(data / bootstrap.BOOTSTRAP_NAME, location.owner_sid)
        if bootstrap._read(data / bootstrap.BOOTSTRAP_NAME) != value: _fail('BOOTSTRAP_CONFLICT')
        evidence = _mapping(data, _hash(data / bootstrap.BOOTSTRAP_NAME))
    elif mapping_evidence is not None:
        # Explicit maintenance/test provenance boundary; normal Setup never needs it.
        evidence = mapping_evidence
        bootstrap.provision(root, evidence, **options)
    else:
        if (data / bootstrap.BOOTSTRAP_NAME).exists(): _fail('BOOTSTRAP_CONFLICT')
        if state and state['state'] == 'PREPARED': _fail('INSTALL_ENROLLMENT_REPLAY')
        if state is None:
            if challenge_path.exists() or receipt_path.exists(): _fail()
            state = dict(target, state='PENDING')
        challenge = state.get('challenge')
        now = int(clock())
        if state['state'] == 'PROVISIONING':
            if (not challenge or not challenge_path.exists() or not receipt_path.exists() or
                    state.get('receiptHash') != _hash(receipt_path) or challenge.get('expiresAt',0) <= now):
                _fail('INSTALL_ENROLLMENT_REPLAY')
        if challenge:
            _challenge(challenge, target, data)
            previous = state.get('previousChallenge')
            if previous: _challenge(previous, target, data)
            if challenge_path.exists() and _read(challenge_path) not in [challenge, previous]: _fail()
            if previous and receipt_path.exists():
                received = _read(receipt_path)
                if isinstance(received,dict) and received.get('nonce') == challenge['nonce']:
                    _receipt(receipt_path, challenge, data)
                else:
                    _receipt(receipt_path, previous, data); receipt_path.unlink()
            if challenge_path.exists() and _read(challenge_path) != challenge:
                _atomic_json(challenge_path, challenge)
            if previous:
                state.pop('previousChallenge'); _atomic_json(state_path, state)
            if receipt_path.exists():
                _receipt(receipt_path, challenge, data, incomplete=state['state']=='PENDING' and challenge['issuedAt'] <= now < challenge['expiresAt'])
            if challenge['issuedAt'] > now: _fail()
        if challenge is None or challenge['expiresAt'] <= now:
            old = challenge
            challenge = dict(target, hostMajor=26, nonce=secrets.token_hex(32), issuedAt=now,
                             expiresAt=now + LIFETIME, canonicalPluginData=str(data))
            state = dict(state, challenge=challenge)
            if old: state['previousChallenge'] = old
            _atomic_json(state_path, state)  # Intent precedes any public replacement.
            data.mkdir(parents=True, exist_ok=True)
            if receipt_path.exists():
                _receipt(receipt_path, old, data); receipt_path.unlink()
            _atomic_json(challenge_path, challenge)
            state.pop('previousChallenge', None); _atomic_json(state_path, state)
        elif not challenge_path.exists():
            data.mkdir(parents=True, exist_ok=True); _atomic_json(challenge_path, challenge)
        if not receipt_path.exists(): return dict(target, status='PENDING_PROVISIONING', step='OPEN_PREMIERE_PANEL')
        _receipt(receipt_path, challenge, data)
        evidence = _mapping(data, _hash(receipt_path))
        state = dict(state, state='PROVISIONING', receiptHash=evidence['receiptHash'])
        _atomic_json(state_path, state)  # Consume once before private side effects.
        bootstrap.provision(root, evidence, **options)
    model = prepare_silero(root, version, verify=verify)
    result = dict(target, status='PREPARED', mappingEvidence=evidence, modelPreparation=model)
    if state is None or all(state.get(key) == value for key, value in target.items()):
        _atomic_json(state_path, dict(state or target, state='PREPARED', modelPreparation=model))
    return result
