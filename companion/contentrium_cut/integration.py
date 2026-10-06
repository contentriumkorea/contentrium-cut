"""Stable native launch/protocol integration; incoming URI content is never executed."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid
from .contract import CutError
from .updater import SemVer
from .windows_install import WindowsInstallation, _guard_path, _atomic_json, _hash
from .bootstrap import canonical_root, resolve_installation, provision

LAUNCHER_NAME = 'Contentrium CUT Launcher.exe'

def _registered_command(registry, key, name):
    try:
        with registry.OpenKey(registry.HKEY_CURRENT_USER,key) as entry:
            value,kind=registry.QueryValueEx(entry,name)
    except FileNotFoundError:return None
    if kind!=registry.REG_SZ or not isinstance(value,str):
        raise CutError('INSTALL_REGISTRATION','기존 설치 등록을 확인할 수 없습니다. 원래 설치를 복구해 주세요.')
    return value

def _owned_launcher(root, config):
    from . import launcher_transaction as launcher
    descriptor=launcher.read(root/'launcher-config.json')
    if (descriptor.get('publicKey')!=config.get('publicKey') or
            descriptor.get('signingKeyId','contentrium-cut-2026-01')!=config.get('signingKeyId','contentrium-cut-2026-01')):
        raise CutError('INSTALL_REGISTRATION','등록된 설치의 서명 키가 이 Setup과 다릅니다.')
    value=launcher.ownership(root)
    if not value['present']:raise CutError('INSTALL_REGISTRATION','등록된 설치 파일을 검증하지 못했습니다.')
    return value

def resolve_setup_root(config, *, explicit=None, registry=None, known_local=None, owner_sid=None, installation_factory=None):
    """Parse only the two exact owned registrations; never execute their text.

    Legacy 0.1.0 has signed Launcher/journal evidence but no private bootstrap.
    Existing private identities, when present, must also pass their DACL boundary.
    """
    if explicit is not None:return canonical_root(explicit)
    if registry is None:
        import winreg as registry
    protocol=_registered_command(registry,r'Software\Classes\contentrium-cut\shell\open\command','')
    startup=_registered_command(registry,r'Software\Microsoft\Windows\CurrentVersion\Run','Contentrium CUT')
    roots=[]
    try:
        for command,mode in [(protocol,'open'),(startup,'supervise')]:
            if command is None:continue
            match=re.fullmatch(r'"([^"\r\n]+)" --'+mode+r'(?: --root "([^"\r\n]+)")?',command)
            if match is None or (mode=='supervise' and match[2] is None):raise ValueError()
            source=Path(match[1]);root=canonical_root(source.parent)
            if source.name!=LAUNCHER_NAME or str(source)!=str(root/LAUNCHER_NAME):raise ValueError()
            if match[2] is not None and (canonical_root(match[2])!=root or match[2]!=str(root)):raise ValueError()
            roots.append(root)
        if roots:
            if any(path!=roots[0] for path in roots):raise ValueError()
            root=roots[0];_owned_launcher(root,config)
            if (root/'install-location.json').exists() or (root/'private/auth-bootstrap.json').exists():
                resolve_installation(root,launcher=root/LAUNCHER_NAME,owner_sid=owner_sid)
            installed=(installation_factory or WindowsInstallation)(root)
            # Installer-optional updates retain an older signed stable Launcher.
            # Its ownership proof and the current active code identity are distinct.
            active,_=installed._active_descriptor()
            if installed.verify_code(active['appVersion'],active['bundleId']) is not True:raise ValueError()
            return root
    except Exception as error:
        raise CutError('INSTALL_REGISTRATION','기존 등록 설치를 검증하지 못했습니다. 별도 설치를 만들지 않고 복구가 필요합니다.') from error
    from .bootstrap import known_folder
    return canonical_root(Path(known_local or known_folder('local'))/'Contentrium CUT')

def protocol_command(root):
    root = canonical_root(root)
    return '"' + str(root / LAUNCHER_NAME) + '" --open --root "' + str(root) + '"'

def startup_command(root):
    root = canonical_root(root)
    command = '"' + str(root / LAUNCHER_NAME) + '" --supervise --root "' + str(root) + '"'
    if len(command) > 260: raise CutError('STARTUP_COMMAND', 'The fixed logon command exceeds the Windows Run limit.')
    return command

def register_startup(root, *, registry=None):
    """Logon start only. Never claim Run recovers a killed in-session supervisor."""
    command = startup_command(root)
    if registry is None:
        import winreg as registry
    for view in ['Run','Run32']:
        try:
            with registry.OpenKey(registry.HKEY_CURRENT_USER,r'Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved'+'\\'+view) as approved:
                status,_ = registry.QueryValueEx(approved,'Contentrium CUT')
        except FileNotFoundError:continue
        if isinstance(status,bytes) and len(status)==12 and status[0] in [3,7]:
            raise CutError('STARTUP_DISABLED','Windows has disabled this logon startup entry.')
        if not isinstance(status,bytes) or len(status)!=12 or status[0] not in [2,6]:
            raise CutError('STARTUP_STATUS_UNKNOWN','Windows startup approval needs native verification.')
    with registry.CreateKey(registry.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Run') as key:
        try: existing, kind = registry.QueryValueEx(key, 'Contentrium CUT')
        except FileNotFoundError: existing, kind = None, registry.REG_SZ
        if existing is not None and (existing != command or kind != registry.REG_SZ):
            raise CutError('STARTUP_CONFLICT', 'Existing startup registration cannot be overwritten.')
        registry.SetValueEx(key, 'Contentrium CUT', 0, registry.REG_SZ, command)
    return command

def open_active(root, *, installation=None, popen=None):
    root = canonical_root(root)
    # The injected installation is a trusted isolated adapter; production must
    # prove protected owner/location identity before any child dispatch.
    if installation is None:
        resolve_installation(root, launcher=Path(sys.executable) if getattr(sys, 'frozen', False) else None)
    active = root / 'app' / 'active.json'
    _guard_path(active)
    try:
        descriptor = json.loads(active.read_text(encoding='utf-8'))
        version, bundle = descriptor['appVersion'], descriptor['bundleId']
        SemVer(version)
        if (descriptor.get('schemaVersion') != 1 or descriptor.get('productId') != 'com.contentrium.cut'
                or descriptor.get('versionDirectory') != 'versions/' + version):
            raise ValueError('Invalid active pointer')
        directory = root / 'app' / 'versions' / version
        exe = directory / 'Contentrium CUT.exe'
        _guard_path(exe)
        concrete = installation or WindowsInstallation(root)
        recovery = False
        if concrete.verify_application(version, bundle) is not True:
            from .lifecycle import pending_update
            if pending_update(root) and getattr(concrete,'verify_code',lambda *a:False)(version,bundle) is True:
                recovery = True
            else: raise CutError('LAUNCH_VERIFY', 'Installed Contentrium CUT could not be verified.')
        if not exe.is_file(): raise ValueError('Missing runtime')
    except CutError: raise
    except Exception as error:
        raise CutError('LAUNCH_VERIFY', 'Installed Contentrium CUT could not be verified.') from error
    config_path = directory / 'config.json'
    _guard_path(config_path)
    if not config_path.is_file(): raise CutError('LAUNCH_VERIFY', 'Exact installed config is required.')
    args = [str(exe), '--supervisor', '--root', str(root), '--config', str(config_path)]
    if recovery: args += ['--recovery-only']
    return (popen or subprocess.Popen)(args,
        cwd=str(directory), close_fds=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def _copy(source, target):
    _guard_path(source); _guard_path(target)
    if source.resolve() == target.resolve(): return
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with source.open('rb') as src, temporary.open('xb') as dst:
            shutil.copyfileobj(src, dst, 1024 * 1024)
            dst.flush(); os.fsync(dst.fileno())
        if _hash(source) != _hash(temporary):
            raise CutError('LAUNCHER_COPY', 'Stable launcher copy could not be verified.')
        os.replace(temporary, target)
    finally: temporary.unlink(missing_ok=True)

def install_integration(root, config, *, launcher_source=None, resource_root=None, registry=None, menu_root=None,
                        mapping_evidence=None, owner_sid=None, roaming_root=None, protect=None, popen=None, launch=True):
    root = canonical_root(root)
    source = Path(launcher_source) if launcher_source else (Path(sys.executable) if getattr(sys, 'frozen', False) else None)
    if source is None or source.suffix.lower() != '.exe' or not source.is_file():
        raise CutError('LAUNCHER_SOURCE', 'A built Contentrium CUT Setup executable is required for the stable launcher.')
    existing_config=root/'launcher-config.json';existing_launcher=root/LAUNCHER_NAME
    _guard_path(existing_config);_guard_path(existing_launcher)
    if existing_config.exists():
        try:matches=json.loads(existing_config.read_text(encoding='utf-8'))==config
        except (ValueError,OSError):matches=False
        if not matches:raise CutError('INTEGRATION_CONFLICT','Existing launcher descriptor requires a verified replacement transaction.')
    if existing_launcher.exists() and _hash(existing_launcher)!=_hash(source):
        raise CutError('INTEGRATION_CONFLICT','Existing launcher requires a verified replacement transaction.')
    if registry is None:
        import winreg as registry
    previous=_registered_command(registry,r'Software\Classes\contentrium-cut\shell\open\command','')
    legacy='"'+str(root/LAUNCHER_NAME)+'" --open'
    if previous is not None and previous!=protocol_command(root):
        if previous!=legacy:raise CutError('INTEGRATION_CONFLICT','Existing protocol descriptor cannot be overwritten.')
        _owned_launcher(root,config)  # Only the exactly signed, same-root 0.1.0 command can migrate.
    if mapping_evidence is None:
        raise CutError('BOOTSTRAP_MAPPING', 'Verified installed panel data-folder mapping is required before integration.')
    options = dict(owner_sid=owner_sid, roaming_root=roaming_root)
    if protect is not None: options['protect'] = protect
    location = provision(root, mapping_evidence, **options)
    _copy(source, root / LAUNCHER_NAME)
    _atomic_json(root / 'launcher-config.json', config)
    resources = Path(resource_root) if resource_root else Path(getattr(sys, '_MEIPASS', source.parent))
    for dirname in ('licenses', 'third-party-licenses'):
        directory = resources / dirname
        if directory.is_dir():
            _guard_path(directory)
            for path in directory.rglob('*'):
                _guard_path(path)
                if path.is_file(): _copy(path, root / 'licenses' / dirname / path.relative_to(directory))
    for path in resources.iterdir():
        if path.is_file() and path.name.upper().startswith(('LICENSE', 'NOTICE', 'THIRD_PARTY')):
            _copy(path, root / 'licenses' / path.name)
    if previous==legacy:
        record=dict(schemaVersion=1,productId='com.contentrium.cut',canonicalRoot=str(root),
                    previousProtocolCommand=previous,protocolCommand=protocol_command(root))
        path=root/'updates/integration-registration.json';_guard_path(path)
        if path.exists() and json.loads(path.read_text(encoding='utf-8'))!=record:
            raise CutError('INTEGRATION_CONFLICT','Existing protocol migration evidence cannot be overwritten.')
        if not path.exists():_atomic_json(path,record)
    with registry.CreateKey(registry.HKEY_CURRENT_USER, r'Software\Classes\contentrium-cut') as key:
        registry.SetValueEx(key, '', 0, registry.REG_SZ, 'URL:Contentrium CUT')
        registry.SetValueEx(key, 'URL Protocol', 0, registry.REG_SZ, '')
    with registry.CreateKey(registry.HKEY_CURRENT_USER, r'Software\Classes\contentrium-cut\shell\open\command') as key:
        registry.SetValueEx(key, '', 0, registry.REG_SZ, protocol_command(root))
    from .bootstrap import known_folder
    menu = Path(menu_root) if menu_root else known_folder('roaming') / 'Microsoft/Windows/Start Menu/Programs/Contentrium CUT'
    _guard_path(menu); menu.mkdir(parents=True, exist_ok=True)
    (menu / 'Contentrium CUT.url').write_text('[InternetShortcut]\nURL=contentrium-cut://open\n', encoding='utf-8')
    command = register_startup(root, registry=registry)
    # Start the SAME stable one-shot dispatcher once in the already logged-on
    # session. It verifies code and launches the versioned supervisor, then exits.
    if launch:
        (popen or subprocess.Popen)([str(root / LAUNCHER_NAME), '--supervise', '--root', str(root)],
            close_fds=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {'launcher': str(root / LAUNCHER_NAME), 'launcherSha256':_hash(root / LAUNCHER_NAME),
            'launcherConfigSha256':_hash(root / 'launcher-config.json'), 'shortcut': str(menu / 'Contentrium CUT.url'),
            'installationId': location.installation_id, 'keyId': location.key_id, 'startupCommand': command,
            'bootstrapProvisioned': True, 'logonStartOnly': True, 'dispatchStarted': bool(launch)}
