"""Stable native launch/protocol integration; incoming URI content is never executed."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid
from .contract import CutError
from .updater import SemVer
from .windows_install import WindowsInstallation, _guard_path, _atomic_json, _hash

LAUNCHER_NAME = 'Contentrium CUT Launcher.exe'

def protocol_command(root):
    return '"' + str(Path(root).resolve() / LAUNCHER_NAME) + '" --open'

def open_active(root, *, installation=None, popen=None):
    root = Path(root).resolve()
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
        if (installation or WindowsInstallation(root)).verify_application(version, bundle) is not True:
            raise CutError('LAUNCH_VERIFY', 'Installed Contentrium CUT could not be verified.')
        if not exe.is_file(): raise ValueError('Missing runtime')
    except CutError: raise
    except Exception as error:
        raise CutError('LAUNCH_VERIFY', 'Installed Contentrium CUT could not be verified.') from error
    return (popen or subprocess.Popen)([str(exe)], cwd=str(directory), close_fds=True)

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

def install_integration(root, config, *, launcher_source=None, resource_root=None, registry=None, menu_root=None):
    root = Path(root).resolve()
    source = Path(launcher_source) if launcher_source else (Path(sys.executable) if getattr(sys, 'frozen', False) else None)
    if source is None or source.suffix.lower() != '.exe' or not source.is_file():
        raise CutError('LAUNCHER_SOURCE', 'A built Contentrium CUT Setup executable is required for the stable launcher.')
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
    if registry is None:
        import winreg as registry
    with registry.CreateKey(registry.HKEY_CURRENT_USER, r'Software\Classes\contentrium-cut') as key:
        registry.SetValueEx(key, '', 0, registry.REG_SZ, 'URL:Contentrium CUT')
        registry.SetValueEx(key, 'URL Protocol', 0, registry.REG_SZ, '')
    with registry.CreateKey(registry.HKEY_CURRENT_USER, r'Software\Classes\contentrium-cut\shell\open\command') as key:
        registry.SetValueEx(key, '', 0, registry.REG_SZ, protocol_command(root))
    menu = Path(menu_root) if menu_root else Path(os.environ['APPDATA']) / 'Microsoft/Windows/Start Menu/Programs/Contentrium CUT'
    _guard_path(menu); menu.mkdir(parents=True, exist_ok=True)
    (menu / 'Contentrium CUT.url').write_text('[InternetShortcut]\nURL=contentrium-cut://open\n', encoding='utf-8')
    return {'launcher': str(root / LAUNCHER_NAME), 'shortcut': str(menu / 'Contentrium CUT.url')}
