"""Concrete Windows/Adobe installation hooks; no Premiere termination or fake proof."""
from __future__ import annotations
import ctypes
from contextlib import closing
from ctypes import wintypes
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import subprocess
import time
import uuid
import zipfile

from .contract import CutError, canonical_hash
from .updater import SemVer, validate_zip

PRODUCT_ID = 'com.contentrium.cut'
DEFAULT_UPIA = Path(r'C:\Program Files\Common Files\Adobe\Adobe Desktop Common\RemoteComponents\UPI\UnifiedPluginInstallerAgent\UnifiedPluginInstallerAgent.exe')
PREMIERE_NAMES = {'adobe premiere pro.exe', 'premiere pro.exe'}
DATA_DIRECTORIES = ('settings', 'jobs', 'mappings', 'reviews')
DATA_FILES = ('settings.json',)
RECEIPT_NAME = 'install.json'
PANEL_NAME = 'panel.ccx'


def _kernel():
    if os.name != 'nt':
        raise CutError('WINDOWS_REQUIRED', 'Windows process and mutex APIs are required')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    signatures = {
        'CloseHandle': ([wintypes.HANDLE], wintypes.BOOL),
        'OpenProcess': ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
        'QueryFullProcessImageNameW': ([wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
        'GetExitCodeProcess': ([wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
        'GetProcessTimes': ([wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4, wintypes.BOOL),
        'CreateToolhelp32Snapshot': ([wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE),
        'CreateMutexW': ([ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
        'ReleaseMutex': ([wintypes.HANDLE], wintypes.BOOL),
        'MoveFileExW': ([wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD], wintypes.BOOL),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(kernel, name)
        function.argtypes, function.restype = arguments, result
    return kernel


def process_identity(pid):
    """Return exact PID/image/creation-time identity, None only for proven exit."""
    if type(pid) is not int or pid <= 0:
        raise CutError('PROCESS_IDENTITY', 'Invalid process ID')
    kernel = _kernel()
    handle = kernel.OpenProcess(0x1000 | 0x100000, False, pid)
    if not handle:
        error = ctypes.get_last_error()
        if error == 87:  # ERROR_INVALID_PARAMETER: this PID no longer exists.
            return None
        raise CutError('PROCESS_IDENTITY', 'Process identity could not be inspected', {'winerror': error})
    try:
        code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
            raise CutError('PROCESS_IDENTITY', 'Process liveness could not be verified')
        if code.value != 259:
            return None
        buffer = ctypes.create_unicode_buffer(32768)
        length = wintypes.DWORD(len(buffer))
        created, exited, system, user = (wintypes.FILETIME() for _ in range(4))
        if (not kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length))
                or not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(system), ctypes.byref(user))):
            raise CutError('PROCESS_IDENTITY', 'Process image or creation time could not be verified')
        return {'pid': pid, 'imagePath': str(Path(buffer.value).resolve()),
                'createdAtTicks': str((created.dwHighDateTime << 32) | created.dwLowDateTime)}
    finally:
        kernel.CloseHandle(handle)


def process_has_exited(identity, *, probe=process_identity):
    """PID reuse proves the OLD process exited; access denial/partial identity does not."""
    if (not isinstance(identity, dict) or type(identity.get('pid')) is not int
            or not isinstance(identity.get('imagePath'), str) or not Path(identity['imagePath']).is_absolute()
            or not re.fullmatch('[0-9]+', str(identity.get('createdAtTicks', '')))):
        return False
    try:
        actual = probe(identity['pid'])
        if actual is None:
            return True
        if not actual.get('imagePath') or not actual.get('createdAtTicks'):
            return False
        return (actual['createdAtTicks'] != identity['createdAtTicks']
                or os.path.normcase(actual['imagePath']) != os.path.normcase(identity['imagePath']))
    except Exception:
        return False


def windows_process_snapshot():
    """Enumerate all sessions' process names; inability to inspect Premiere blocks exit."""
    class Entry(ctypes.Structure):
        _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD), ('th32ProcessID', wintypes.DWORD),
                    ('th32DefaultHeapID', ctypes.c_size_t), ('th32ModuleID', wintypes.DWORD), ('cntThreads', wintypes.DWORD),
                    ('th32ParentProcessID', wintypes.DWORD), ('pcPriClassBase', wintypes.LONG),
                    ('dwFlags', wintypes.DWORD), ('szExeFile', wintypes.WCHAR * 260)]
    kernel = _kernel()
    kernel.Process32FirstW.argtypes = kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.Process32FirstW.restype = kernel.Process32NextW.restype = wintypes.BOOL
    handle = kernel.CreateToolhelp32Snapshot(0x2, 0)
    if handle == ctypes.c_void_p(-1).value:
        raise CutError('PROCESS_SNAPSHOT', 'Windows process snapshot failed')
    rows = []
    try:
        entry = Entry(); entry.dwSize = ctypes.sizeof(Entry)
        success = kernel.Process32FirstW(handle, ctypes.byref(entry))
        if not success and ctypes.get_last_error() != 18:
            raise CutError('PROCESS_SNAPSHOT', 'Windows process enumeration failed')
        while success:
            row = {'pid': entry.th32ProcessID, 'imageName': entry.szExeFile}
            if entry.szExeFile.casefold() in PREMIERE_NAMES:
                try:
                    identity = process_identity(entry.th32ProcessID)
                    if identity is None:
                        success = kernel.Process32NextW(handle, ctypes.byref(entry))
                        continue
                    row.update(identity)
                except CutError:
                    row['identityKnown'] = False
            rows.append(row)
            success = kernel.Process32NextW(handle, ctypes.byref(entry))
        if ctypes.get_last_error() != 18:
            raise CutError('PROCESS_SNAPSHOT', 'Windows process enumeration was incomplete')
    finally:
        kernel.CloseHandle(handle)
    return rows


class WindowsNamedMutex:
    """A named installation owner; close releases only this process's handle."""
    def __init__(self, root=None, *, name=None):
        identity = str(Path(root).resolve()).casefold() if root is not None else ''
        self.name = name or 'Global\\ContentriumCUT-' + hashlib.sha256(identity.encode()).hexdigest()
        self.handle = None

    def __enter__(self):
        if self.handle is not None:
            raise CutError('UPDATER_ALREADY_RUNNING', 'Installation mutex is already owned')
        kernel = _kernel()
        ctypes.set_last_error(0)
        handle = kernel.CreateMutexW(None, True, self.name)
        error = ctypes.get_last_error()
        if not handle:
            raise CutError('UPDATER_MUTEX', 'Installation mutex could not be acquired', {'winerror': error})
        if error == 183:
            kernel.CloseHandle(handle)
            raise CutError('UPDATER_ALREADY_RUNNING', 'Another updater owns this installation')
        self.handle = handle
        return self

    def close(self):
        if self.handle is not None:
            kernel = _kernel()
            kernel.ReleaseMutex(self.handle)
            kernel.CloseHandle(self.handle)
            self.handle = None

    def __exit__(self, *args):
        self.close()


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


class CommandRunner:
    def run(self, args, timeout):
        # subprocess.run times out/kills only the specific UPIA child it created.
        result = subprocess.run(args, capture_output=True, text=True, encoding='utf-8', errors='replace',
                                timeout=timeout, shell=False, creationflags=0x08000000 if os.name == 'nt' else 0)
        return CommandResult(result.returncode, result.stdout, result.stderr)


def parse_upia_list(text):
    """Parse captured ID records or actual product tables; never infer an ID from name."""
    text = text.strip().lstrip('\ufeff')
    if not text:
        raise CutError('UPIA_LIST_UNREADABLE', 'Adobe plugin registration output is empty')
    if re.match(r'[0-9]+ extensions? installed for ', text):
        return _parse_upia_table(text)
    rows = None
    if text[0] in '[{':
        document = json.loads(text)
        rows = document if isinstance(document, list) else document.get('plugins')
        if rows == []:
            return []
    else:
        rows, current = [], {}
        for line in text.splitlines():
            match = re.fullmatch(r'\s*(Plugin\s+ID|Extension\s+ID|ID|Identifier|Version)\s*:\s*(\S+)\s*', line, re.I)
            if not match:
                continue
            key = 'version' if match[1].casefold() == 'version' else 'id'
            if key == 'id' and current.get('id'):
                rows.append(current); current = {}
            current[key] = match[2]
        if current:
            rows.append(current)
    if not isinstance(rows, list) or not rows:
        raise CutError('UPIA_LIST_UNREADABLE', 'Adobe plugin registration output could not be read')
    normalized = []
    for item in rows:
        if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not isinstance(item.get('version'), str):
            raise CutError('UPIA_LIST_UNREADABLE', 'Adobe plugin registration lacks ID/version')
        if item['id'] == PRODUCT_ID:
            SemVer(item['version'])
        elif not re.fullmatch('[0-9A-Za-z.+_-]{1,100}', item['version']):
            raise CutError('UPIA_LIST_UNREADABLE', 'Adobe plugin version could not be read')
        normalized.append({'id': item['id'], 'version': item['version'], 'status': item.get('status', 'Enabled')})
    if sum(row['id'] == PRODUCT_ID for row in normalized) > 1:
        raise CutError('UPIA_REGISTRATION', 'Duplicate Contentrium CUT registrations')
    return normalized


class _TableRows(list):
    """Table rows have display names only; identity must come from UPI records."""
    pass


def _parse_upia_table(text):
    lines = text.splitlines()
    index, rows, found_premiere = 0, _TableRows(), False
    rows.host_versions = set()
    while index < len(lines):
        if not lines[index].strip():
            index += 1
            continue
        section = re.fullmatch(r'([0-9]+) extensions? installed for (.*?)(?: \(ver ([0-9.]+)\))?\s*', lines[index])
        if not section or index + 2 >= len(lines):
            raise CutError('UPIA_LIST_UNREADABLE', 'Adobe product table is incomplete')
        count, product, host_version = int(section[1]), section[2].strip(), section[3]
        if (count > 100000 or not re.fullmatch(r'\s*Status\s+Extension Name\s+Version\s*', lines[index + 1])
                or not re.fullmatch(r'\s*=+\s+=+\s+=+\s*', lines[index + 2])):
            raise CutError('UPIA_LIST_UNREADABLE', 'Adobe product table headers are invalid')
        index += 3
        entries = []
        while index < len(lines) and lines[index].strip():
            if re.match(r'[0-9]+ extensions? installed for ', lines[index]):
                break
            entry = re.fullmatch(r'\s*(Enabled|Disabled)\s{2,}(.+?)\s{2,}([0-9A-Za-z.+_-]{1,100})\s*', lines[index])
            if not entry:
                raise CutError('UPIA_LIST_UNREADABLE', 'Adobe extension table row is invalid')
            entries.append({'name': entry[2].strip(), 'version': entry[3], 'status': entry[1],
                            'product': product, 'hostVersion': host_version})
            index += 1
        if len(entries) != count:
            raise CutError('UPIA_LIST_UNREADABLE', 'Adobe extension table count does not match')
        if product == 'Premiere Pro':
            if not host_version:
                raise CutError('UPIA_LIST_UNREADABLE', 'Premiere product version is missing')
            found_premiere = True
            rows.host_versions.add(host_version)
            rows.extend(entries)
    if not found_premiere:
        raise CutError('UPIA_LIST_UNREADABLE', 'Premiere product table is missing')
    return rows


def upia_registration_records(databases):
    """Read exact local/cross-database Adobe identity mappings without writes.

    User extensions may reference a system product ID while their local product
    table is empty. Local definitions win; absent ones require one consistent
    definition from the other captured databases. The caller still matches UPIA.
    """
    snapshots = []
    for database in databases:
        database = Path(database)
        if not database.is_file():
            continue
        try:
            _guard_path(database)
            with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True, timeout=3)) as connection:
                connection.execute('PRAGMA query_only=ON')
                connection.execute('BEGIN')
                products = connection.execute('SELECT ProdID,DisplayName,ProdVersion FROM Tb_ProductInfo').fetchmany(100001)
                captured = connection.execute('SELECT e.ExtIDInFile,e.ExtName,e.ExtVersion,m.State,m.ProdID '
                    'FROM Tb_ExtBasicInfo e JOIN Tb_ExtProductMap m ON e.ExtID=m.ExtID').fetchmany(100001)
            if len(captured) > 100000 or len(products) > 100000:
                raise ValueError('Too many Adobe registrations')
            product_map={}
            for product_id, product, host_version in products:
                if (type(product_id) is not int or not isinstance(product,str) or not product
                        or not isinstance(host_version,str) or not host_version):
                    raise ValueError('Incomplete Adobe product identity')
                product_map.setdefault(product_id,set()).add((product,host_version))
            snapshots.append((product_map,captured))
        except Exception as error:
            raise CutError('UPIA_REGISTRATION', 'Adobe registration identity database could not be read') from error
    records=[]
    try:
        for index,(local,captured) in enumerate(snapshots):
            for plugin_id,name,version,status,product_id in captured:
                if type(product_id) is not int or not all(isinstance(value,str) and value for value in (plugin_id,name,version,status)):
                    raise ValueError('Incomplete Adobe registration')
                definitions=local.get(product_id)
                if definitions is None:
                    definitions=set().union(*(other.get(product_id,set()) for position,(other,_) in enumerate(snapshots) if position!=index))
                if len(definitions)!=1:
                    raise ValueError('Missing or conflicting Adobe product reference')
                product,host_version=next(iter(definitions))
                if product=='Premiere Pro':
                    records.append({'id':plugin_id,'name':name,'version':version,'status':status,
                                    'product':product,'hostVersion':host_version})
    except Exception as error:
        raise CutError('UPIA_REGISTRATION','Adobe cross-database product identity could not be verified') from error
    return records


def _hash(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _reject_links(path):
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if path.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise CutError('INSTALL_PATH', 'Linked/reparse installation paths are not allowed')


def _guard_path(path):
    for ancestor in reversed([path] + list(path.parents)):
        _reject_links(ancestor)


def _tree_hashes(directory, exclude=()):
    _guard_path(directory)
    hashes = {}
    total = 0
    for path in directory.rglob('*'):
        _reject_links(path)
        if path.is_file() and path.relative_to(directory).as_posix() not in exclude:
            total += path.stat().st_size
            if len(hashes) >= 100000 or total > 4 * 1024**3:
                raise CutError('INSTALL_SIZE', 'Installation or backup tree exceeds limits')
            hashes[path.relative_to(directory).as_posix()] = _hash(path)
    return hashes


def _atomic_json(path, value):
    _guard_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _reject_links(path.parent); _reject_links(path)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False)
            stream.flush(); os.fsync(stream.fileno())
        if os.name == 'nt':
            if not _kernel().MoveFileExW(str(temporary), str(path), 0x1 | 0x8):
                raise ctypes.WinError(ctypes.get_last_error())
        else:
            os.replace(temporary, path)
            descriptor = os.open(path.parent, os.O_RDONLY)
            try: os.fsync(descriptor)
            finally: os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


class WindowsInstallation:
    def __init__(self, root, *, upia_path=DEFAULT_UPIA, runner=None, process_probe=None,
                 registration_adapter=None, registration_databases=None, activation_probe=None, command_timeout=120, clock=time.time,
                 migration_registry=None):
        self.root = Path(root).resolve()
        self.app = self.root / 'app'
        self.versions = self.app / 'versions'
        self.active = self.app / 'active.json'
        self.backups = self.root / 'updates' / 'backups'
        self.bootstrap_journal = self.root / 'updates' / 'bootstrap.json'
        self.launcher_journal = self.root / 'updates' / 'launcher-transaction.json'
        self.upia_path = Path(upia_path).resolve()
        self.runner = runner or CommandRunner()
        self.process_probe = process_probe or windows_process_snapshot
        self.registration_adapter = registration_adapter
        self.migration_registry = migration_registry
        self.registration_databases = list(registration_databases) if registration_databases is not None else [
            Path(os.environ.get('PROGRAMDATA', r'C:\ProgramData')) / 'Adobe/UPI/Configuration/DB/UPISys.db',
            Path(os.environ.get('APPDATA', '')) / 'Adobe/UPI/Configuration/DB/UPI.db']
        if type(command_timeout) not in (int, float) or not 1 <= command_timeout <= 600:
            raise ValueError('Adobe command timeout must be between 1 and 600 seconds')
        self.activation_probe, self.command_timeout, self.clock = activation_probe, command_timeout, clock

    def compatibility(self, manifest):
        reasons = []
        if manifest.get('productId') != PRODUCT_ID:
            reasons.append('PRODUCT_ID')
        if not self.upia_path.is_file():
            reasons.append('ADOBE_INSTALLER_MISSING')
        if manifest.get('dataSchemaTo') != 1 or manifest.get('migrationId') != 'none' or 1 not in manifest.get('dataSchemaFrom', []):
            reasons.append('DATA_MIGRATION')
        return reasons

    def host_exited(self):
        try:
            return not any(row.get('imageName', '').casefold() in PREMIERE_NAMES
                           or Path(row.get('imagePath', '')).name.casefold() in PREMIERE_NAMES
                           for row in self.process_probe())
        except Exception:
            return False

    def host_identity_exited(self, identity):
        """Prove the original PID/image/creation instance exited, even after reopen."""
        if not isinstance(identity,dict):return False
        actual=dict(identity)
        if 'createdAtTicks' not in actual and 'createTime' in actual:
            actual['createdAtTicks']=str(actual['createTime'])
        return process_has_exited(actual)

    def _require_host_exit(self):
        if not self.host_exited():
            raise CutError('HOST_RUNNING', 'Save and close all Premiere Pro instances before installation')

    def _command(self, action, argument):
        if not self.upia_path.is_file():
            raise CutError('UPIA_MISSING', 'Adobe Unified Plugin Installer Agent was not found')
        try:
            result = self.runner.run([str(self.upia_path), action, str(argument)],
                                     min(self.command_timeout, 30) if action == '/list' else self.command_timeout)
        except subprocess.TimeoutExpired as error:
            raise CutError('UPIA_TIMEOUT', 'Adobe installer command timed out') from error
        if result.returncode != 0:
            raise CutError('UPIA_FAILED', 'Adobe installer command failed', {'operation': action, 'exitCode': result.returncode})
        return result

    def registrations(self):
        result = self._command('/list', 'Premiere Pro')
        rows = self.registration_adapter(result) if self.registration_adapter else parse_upia_list(result.stdout)
        if isinstance(rows, _TableRows):
            identities = upia_registration_records(self.registration_databases)
            resolved = []
            for row in rows:
                matches = {record['id'] for record in identities
                           if all(record.get(key) == row.get(key) for key in ('name', 'version', 'status', 'product', 'hostVersion'))}
                if len(matches) != 1:
                    raise CutError('UPIA_REGISTRATION', 'Adobe table entry lacks unique plugin identity')
                resolved.append(dict(row, id=matches.pop()))
            for record in identities:
                if (record['id'] == PRODUCT_ID and record['hostVersion'] in rows.host_versions
                        and not any(all(record.get(key) == row.get(key) for key in
                                        ('id', 'name', 'version', 'status', 'product', 'hostVersion')) for row in resolved)):
                    raise CutError('UPIA_REGISTRATION', 'Adobe CUT identity and product table disagree')
            rows = resolved
        # Validate adapters too; they consume captured command output, never expected-version guesses.
        return parse_upia_list(json.dumps({'plugins': rows})) if rows else []

    def _registered(self, version):
        entries = [row for row in self.registrations() if row['id'] == PRODUCT_ID]
        return len(entries) == 1 and entries[0]['version'] == version and entries[0]['status'] == 'Enabled'

    def _active_descriptor(self):
        _guard_path(self.active); _guard_path(self.versions)
        descriptor = json.loads(self.active.read_text(encoding='utf-8'))
        version = descriptor['appVersion']; SemVer(version)
        directory = self.versions / version
        if (descriptor.get('schemaVersion') != 1 or descriptor.get('productId') != PRODUCT_ID
                or descriptor.get('versionDirectory') != 'versions/' + version):
            raise CutError('INSTALL_METADATA', 'Active installation descriptor is invalid')
        return descriptor, directory

    def _verify_directory(self, directory, version, bundle):
        _reject_links(directory)
        receipt = json.loads((directory / RECEIPT_NAME).read_text(encoding='utf-8'))
        config = json.loads((directory / 'config.json').read_text(encoding='utf-8'))
        self._check_bundle(config, version, bundle)
        if (receipt.get('productId') != PRODUCT_ID or receipt.get('appVersion') != version
                or receipt.get('bundleId') != bundle or receipt.get('files') != _tree_hashes(directory, (RECEIPT_NAME,))
                or not (directory / 'Contentrium CUT.exe').is_file() or not (directory / PANEL_NAME).is_file()):
            raise CutError('INSTALL_HASH', 'Installed application files could not be verified')
        self._check_panel(directory / PANEL_NAME, version, bundle)
        return receipt

    def _check_bundle(self, config, version, bundle):
        if (config.get('productId') != PRODUCT_ID or config.get('appVersion') != version
                or config.get('panelVersion') != version or config.get('companionVersion') != version
                or config.get('bundleId') != bundle or type(config.get('dataSchemaVersion')) is not int
                or config['dataSchemaVersion'] != 1):
            raise CutError('INSTALL_BUNDLE', 'Package components do not match the selected version/bundle')

    def _check_panel(self, path, version, bundle):
        validate_zip(path)
        with zipfile.ZipFile(path) as archive:
            manifest = json.loads(archive.read('manifest.json'))
            config = json.loads(archive.read('bundle.json'))
        if (manifest.get('id') != PRODUCT_ID or manifest.get('version') != version
                or manifest.get('name') != 'Contentrium CUT' or manifest.get('host', {}).get('app') != 'premierepro'):
            raise CutError('INSTALL_PANEL', 'CCX does not match the fixed Premiere plugin identity/version')
        self._check_bundle(config, version, bundle)

    def _check_assets(self, manifest, prepared):
        if self.compatibility(manifest):
            raise CutError('INSTALL_COMPATIBILITY', 'Installer cannot apply this manifest')
        version = manifest['appVersion']; SemVer(version)
        paths = {}
        for asset in manifest['assets']:
            supplied = prepared[asset['role']]
            path = Path(supplied['path'] if isinstance(supplied, dict) else supplied).absolute()
            _guard_path(path)
            path = path.resolve()
            if (path.stat().st_size != asset['size'] or _hash(path) != asset['sha256']
                    or (isinstance(supplied, dict) and (supplied.get('sha256') != asset['sha256'] or supplied.get('assetId') != asset['assetId']))):
                raise CutError('INSTALL_HASH', 'Installation asset does not match signed manifest')
            paths[asset['role']] = path
        if not {'panel', 'companion'} <= paths.keys():
            raise CutError('INSTALL_ASSETS', 'Matched panel and Companion are required')
        self._check_panel(paths['panel'], version, manifest['bundleId'])
        return paths

    def _extract(self, package, destination):
        validate_zip(package)
        with zipfile.ZipFile(package) as archive:
            for member in archive.infolist():
                path = destination.joinpath(*PurePosixPath(member.filename).parts)
                if destination not in path.resolve().parents:
                    raise CutError('INSTALL_PATH', 'Package member escapes staging directory')
                if member.is_dir():
                    path.mkdir(parents=True, exist_ok=True)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    _reject_links(path.parent)
                    with archive.open(member) as source, path.open('xb') as target:
                        shutil.copyfileobj(source, target, 1024 * 1024)
                        target.flush(); os.fsync(target.fileno())

    def _stage(self, manifest, paths):
        _guard_path(self.versions)
        self.versions.mkdir(parents=True, exist_ok=True)
        _reject_links(self.app); _reject_links(self.versions)
        stage = self.versions / ('.stage-' + uuid.uuid4().hex)
        stage.mkdir()
        self._extract(paths['companion'], stage)
        if (stage / PANEL_NAME).exists() or (stage / RECEIPT_NAME).exists():
            raise CutError('INSTALL_PATH', 'Runtime package contains reserved installer files')
        config = json.loads((stage / 'config.json').read_text(encoding='utf-8'))
        self._check_bundle(config, manifest['appVersion'], manifest['bundleId'])
        if not (stage / 'Contentrium CUT.exe').is_file():
            raise CutError('INSTALL_RUNTIME', 'Standalone Contentrium CUT executable is missing')
        shutil.copy2(paths['panel'], stage / PANEL_NAME)
        receipt = {'schemaVersion': 1, 'productId': PRODUCT_ID, 'appVersion': manifest['appVersion'],
                   'bundleId': manifest['bundleId'], 'assets': manifest['assets'],
                   'files': _tree_hashes(stage), 'createdAt': self.clock()}
        _atomic_json(stage / RECEIPT_NAME, receipt)
        self._verify_directory(stage, manifest['appVersion'], manifest['bundleId'])
        return stage

    def bootstrap(self, manifest, prepared_assets):
        """First install with durable, exact-bundle recovery; no untracked adoption."""
        self._require_host_exit()
        _guard_path(self.bootstrap_journal)
        identity = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        destination = self.versions / manifest['appVersion']
        if self.bootstrap_journal.exists():
            try:
                journal = json.loads(self.bootstrap_journal.read_text(encoding='utf-8'))
                if (journal.get('schemaVersion') != 1 or journal.get('productId') != PRODUCT_ID
                        or journal.get('manifestDigest') != identity or journal.get('appVersion') != manifest['appVersion']
                        or journal.get('bundleId') != manifest['bundleId']
                        or journal.get('state') not in {'STAGED','REGISTERING','REGISTERED','PROMOTING','PENDING_ACTIVATION'}
                        or not re.fullmatch(r'\.stage-[0-9a-f]{32}', journal.get('stageDirectory',''))
                        or journal.get('destinationDirectory') != 'versions/' + manifest['appVersion']
                        or not re.fullmatch('[0-9a-f]{64}', journal.get('receiptHash',''))):
                    raise ValueError('Bootstrap identity/phase changed')
                stage = self.versions / journal['stageDirectory']
            except Exception as error:
                raise CutError('BOOTSTRAP_RECOVERY', 'First-install journal does not match this exact bundle.') from error
        else:
            if self.active.exists() or destination.exists():
                raise CutError('INSTALL_EXISTS', 'Use verified update hooks for an existing installation')
            if any(row['id'] == PRODUCT_ID for row in self.registrations()):
                raise CutError('INSTALL_EXISTS', 'Existing Adobe CUT registration needs a verified application recovery point')
            paths = self._check_assets(manifest, prepared_assets)
            stage = self._stage(manifest, paths)
            journal = {'schemaVersion':1, 'productId':PRODUCT_ID, 'manifestDigest':identity,
                       'appVersion':manifest['appVersion'], 'bundleId':manifest['bundleId'],
                       'state':'STAGED', 'stageDirectory':stage.name,
                       'destinationDirectory':'versions/' + manifest['appVersion'],
                       'receiptHash':_hash(stage / RECEIPT_NAME)}
            _atomic_json(self.bootstrap_journal, journal)
        paths = self._check_assets(manifest, prepared_assets)
        _guard_path(stage); _guard_path(destination)
        if stage.exists() and destination.exists():
            raise CutError('BOOTSTRAP_RECOVERY', 'First-install staging and destination conflict.')
        directory = destination if destination.exists() else stage
        if not directory.is_dir() or _hash(directory / RECEIPT_NAME) != journal['receiptHash']:
            raise CutError('BOOTSTRAP_RECOVERY', 'First-install receipt or staged files changed.')
        receipt = self._verify_directory(directory, manifest['appVersion'], manifest['bundleId'])
        if receipt.get('assets') != manifest['assets'] or _hash(directory / PANEL_NAME) != _hash(paths['panel']):
            raise CutError('BOOTSTRAP_RECOVERY', 'First-install files differ from the selected signed assets.')
        # Derive runtime contents from the caller's hash-verified ZIP, not a self-authored receipt alone.
        expected = {PANEL_NAME:_hash(paths['panel'])}
        with zipfile.ZipFile(paths['companion']) as package:
            for member in package.infolist():
                if member.is_dir(): continue
                digest = hashlib.sha256()
                with package.open(member) as source:
                    for chunk in iter(lambda:source.read(1024*1024),b''): digest.update(chunk)
                expected[member.filename] = digest.hexdigest()
        if expected != receipt['files']:
            raise CutError('BOOTSTRAP_RECOVERY', 'First-install runtime differs from the selected signed archive.')
        registrations = [row for row in self.registrations() if row['id'] == PRODUCT_ID]
        if registrations:
            if (len(registrations) != 1 or registrations[0]['version'] != manifest['appVersion']
                    or registrations[0].get('status','Enabled') != 'Enabled' or journal['state'] == 'STAGED'):
                raise CutError('BOOTSTRAP_RECOVERY', 'Adobe registration does not match an owned install intent.')
        else:
            if self.active.exists():
                raise CutError('BOOTSTRAP_RECOVERY', 'Active first-install Adobe registration disappeared.')
            self._require_host_exit()
            journal = dict(journal,state='REGISTERING')
            _atomic_json(self.bootstrap_journal,journal)  # Must be durable before UPIA may register.
            self._command('/install',directory / PANEL_NAME)
            if not self._registered(manifest['appVersion']):
                raise CutError('UPIA_REGISTRATION','Adobe has not registered the installed panel version')
        journal = dict(journal,state='REGISTERED')
        _atomic_json(self.bootstrap_journal,journal)
        self._require_host_exit()  # Reopening Premiere waits safely; intent/files survive a new installer process.
        if directory == stage:
            journal = dict(journal,state='PROMOTING')
            _atomic_json(self.bootstrap_journal,journal)
            os.replace(stage,destination)
        descriptor = {'schemaVersion':1, 'productId':PRODUCT_ID, 'appVersion':manifest['appVersion'],
                      'bundleId':manifest['bundleId'], 'versionDirectory':'versions/' + manifest['appVersion']}
        if self.active.exists():
            if json.loads(self.active.read_text(encoding='utf-8')) != descriptor:
                raise CutError('INSTALL_EXISTS','Active installation differs; use verified update hooks.')
        else:
            self._require_host_exit()
            _atomic_json(self.active,descriptor)
        journal = dict(journal,state='PENDING_ACTIVATION')
        _atomic_json(self.bootstrap_journal,journal)
        return {'status':'PENDING_ACTIVATION', 'appVersion':manifest['appVersion'], 'bundleId':manifest['bundleId'],
                'versionDirectory':str(destination), 'adobeRegistered':True}

    def install(self, manifest, prepared_assets, snapshot):
        self._require_host_exit()
        self._verify_snapshot(snapshot)
        if any(asset['role']=='installer' for asset in manifest['assets']):
            return self._install_launcher(manifest,prepared_assets,snapshot)
        if not self.verify_previous(snapshot):
            raise CutError('INSTALL_PREVIOUS', 'Current application no longer matches its recovery snapshot')
        return self._install(manifest, prepared_assets)

    def _install_launcher(self,manifest,prepared,snapshot):
        from . import launcher_transaction as launcher
        paths=self._check_assets(manifest,prepared)
        evidence,verified,asset=launcher.evidence_for(self.root,manifest)
        identity=canonical_hash(manifest);snapshot_hash=canonical_hash(snapshot)
        destination=self.versions/manifest['appVersion']
        prior=snapshot['launcher'];old_hash=prior.get('sha256')
        if not prior['present']:
            raise CutError('LAUNCHER_OWNERSHIP','A signed update requires a verified existing stable launcher.')
        old_config=launcher.read(Path(prior['backup'])/'launcher-config.json')
        config=dict(old_config,appVersion=manifest['appVersion'],panelVersion=manifest['appVersion'],companionVersion=manifest['appVersion'],bundleId=manifest['bundleId'])
        new_config_hash=hashlib.sha256(json.dumps(config,ensure_ascii=False,allow_nan=False).encode('utf-8')).hexdigest()
        if _hash(self.root/'launcher-config.json') not in {prior['configHash'],new_config_hash}:
            raise CutError('LAUNCHER_OWNERSHIP','Unknown integration descriptor cannot be overwritten.')
        journal=None;historical=None
        if self.launcher_journal.exists():
            old=launcher.read(self.launcher_journal)
            if old.get('snapshotHash')==snapshot_hash:
                journal=old
                if (journal.get('schemaVersion')!=1 or journal.get('manifestHash')!=identity or journal.get('snapshotHash')!=snapshot_hash or
                    journal.get('asset')!=asset or journal.get('destination')!=str(destination) or
                    journal.get('state') not in {'STAGED','REGISTERING','REGISTERED','PROMOTING','LAUNCHER_REPLACING','LAUNCHER_REPLACED','POINTER_COMMITTING','PENDING_ACTIVATION'} or
                    not re.fullmatch(r'\.stage-[0-9a-f]{32}',journal.get('stage',''))):
                    raise CutError('LAUNCHER_RECOVERY','Replacement journal belongs to another transaction.')
            else:
                launcher.historical(self.root,old,snapshot);historical=old
        if journal is None:
            if not self.verify_previous(snapshot):raise CutError('INSTALL_PREVIOUS','Current installation differs from recovery snapshot.')
            if destination.exists():
                # Explicit retry may reuse only the exact signed directory left by
                # this release's verified rollback, never arbitrary version bytes.
                if (not historical or historical.get('state')!='ROLLED_BACK' or historical.get('manifestHash')!=identity or
                    historical.get('destination')!=str(destination) or historical.get('asset')!=asset or
                    historical.get('receiptHash')!=_hash(destination/RECEIPT_NAME) or
                    self._verify_directory(destination,manifest['appVersion'],manifest['bundleId'])['assets']!=manifest['assets']):
                    raise CutError('INSTALL_EXISTS','Target application already exists without exact rolled-back ownership.')
                stage=self.versions/('.stage-'+uuid.uuid4().hex);receipt_directory=destination
            else:stage=self._stage(manifest,paths);receipt_directory=stage
            launcher_stage=stage.parent/(stage.name+'-launcher.exe')
            launcher.replace(paths['installer'],launcher_stage,asset['sha256'])
            journal=dict(schemaVersion=1,state='STAGED',manifestHash=identity,snapshotHash=snapshot_hash,
                         stage=stage.name,destination=str(destination),receiptHash=_hash(receipt_directory/RECEIPT_NAME),
                         asset=asset,evidence=evidence,previousSha256=old_hash)
            if historical:launcher.archive(self.root,historical)
            _atomic_json(self.launcher_journal,journal)
        stage=self.versions/journal['stage'];launcher_stage=stage.parent/(stage.name+'-launcher.exe')
        _guard_path(stage);_guard_path(destination);_guard_path(launcher_stage)
        if stage.exists() and destination.exists():raise CutError('LAUNCHER_RECOVERY','Conflicting staged application directories.')
        directory=destination if destination.exists() else stage
        if _hash(directory/RECEIPT_NAME)!=journal['receiptHash']:raise CutError('LAUNCHER_RECOVERY','Staged application receipt changed.')
        receipt=self._verify_directory(directory,manifest['appVersion'],manifest['bundleId'])
        if receipt['assets']!=manifest['assets'] or _hash(launcher_stage)!=asset['sha256']:raise CutError('LAUNCHER_RECOVERY','Signed staging identity changed.')
        current_hash=_hash(self.root/launcher.NAME)
        if current_hash not in {old_hash,asset['sha256']}:raise CutError('LAUNCHER_OWNERSHIP','Unknown stable launcher cannot be overwritten.')
        def phase(state):
            nonlocal journal
            journal=dict(journal,state=state);_atomic_json(self.launcher_journal,journal)
        rows=[r for r in self.registrations() if r['id']==PRODUCT_ID]
        new_registered=len(rows)==1 and rows[0]['version']==manifest['appVersion'] and rows[0]['status']=='Enabled'
        if new_registered and journal['state']=='STAGED':raise CutError('LAUNCHER_RECOVERY','Registration predates this owned intent.')
        if not new_registered:
            if len(rows)!=1 or rows[0]['version']!=snapshot['previousVersion']:raise CutError('LAUNCHER_RECOVERY','Unexpected Adobe registration.')
            self._require_host_exit();phase('REGISTERING');self._command('/install',directory/PANEL_NAME)
            if not self._registered(manifest['appVersion']):raise CutError('UPIA_REGISTRATION','Adobe target registration was not verified.')
        phase('REGISTERED');self._require_host_exit()
        if directory==stage:phase('PROMOTING');os.replace(stage,destination)
        phase('LAUNCHER_REPLACING');self._require_host_exit()
        launcher.replace(launcher_stage,self.root/launcher.NAME,asset['sha256'])
        _atomic_json(self.root/'launcher-config.json',config)
        _atomic_json(self.root/'launcher-ownership.json',dict(schemaVersion=1,evidence=evidence,sha256=asset['sha256'],assetId=asset['assetId']))
        if launcher.ownership(self.root)['sha256']!=asset['sha256']:raise CutError('LAUNCHER_VERIFY','Stable launcher replacement could not be verified.')
        phase('LAUNCHER_REPLACED');self._require_host_exit();phase('POINTER_COMMITTING')
        _atomic_json(self.active,dict(schemaVersion=1,productId=PRODUCT_ID,appVersion=manifest['appVersion'],bundleId=manifest['bundleId'],versionDirectory='versions/'+manifest['appVersion']))
        if not self.verify_application(manifest['appVersion'],manifest['bundleId']):raise CutError('LAUNCHER_VERIFY','Application/launcher pair could not be verified.')
        phase('PENDING_ACTIVATION')
        return dict(status='PENDING_ACTIVATION',appVersion=manifest['appVersion'],bundleId=manifest['bundleId'],versionDirectory=str(destination),adobeRegistered=True,
                    launcher=dict(assetId=asset['assetId'],sha256=asset['sha256'],path=str(self.root/launcher.NAME),previousSha256=old_hash))

    def _install(self, manifest, prepared_assets):
        paths = self._check_assets(manifest, prepared_assets)
        stage = self._stage(manifest, paths)
        destination = self.versions / manifest['appVersion']
        if destination.exists():
            raise CutError('INSTALL_EXISTS', 'A version directory already exists; it will not be overwritten')
        self._require_host_exit()
        self._command('/install', stage / PANEL_NAME)
        if not self._registered(manifest['appVersion']):
            raise CutError('UPIA_REGISTRATION', 'Adobe has not registered the installed panel version')
        self._require_host_exit()
        os.replace(stage, destination)
        self._verify_directory(destination, manifest['appVersion'], manifest['bundleId'])
        descriptor = {'schemaVersion': 1, 'productId': PRODUCT_ID, 'appVersion': manifest['appVersion'],
                      'bundleId': manifest['bundleId'], 'versionDirectory': 'versions/' + manifest['appVersion']}
        _atomic_json(self.active, descriptor)
        from .launcher_transaction import ownership
        return {'status': 'PENDING_ACTIVATION', 'appVersion': manifest['appVersion'], 'bundleId': manifest['bundleId'],
                'versionDirectory': str(destination), 'adobeRegistered': True,
                'launcher':dict(ownership(self.root),retained=True)}

    def snapshot(self, manifest):
        self._require_host_exit()
        from . import launcher_transaction as launcher
        launcher.ownership(self.root)  # Refuse unknown bytes before creating a recovery snapshot.
        descriptor, current = self._active_descriptor()
        self._verify_directory(current, descriptor['appVersion'], descriptor['bundleId'])
        if not self._registered(descriptor['appVersion']):
            raise CutError('INSTALL_PREVIOUS', 'Adobe previous panel registration does not match the application')
        backup = self.backups / uuid.uuid4().hex
        data = backup / 'data'
        _guard_path(data)
        data.mkdir(parents=True)
        _tree_hashes(current)
        shutil.copytree(current, backup / 'application')
        for name in DATA_DIRECTORIES:
            source = self.root / name
            if source.exists():
                self._copy_metadata(source, data / name)
        for name in DATA_FILES:
            source = self.root / name
            if source.exists():
                _reject_links(source); shutil.copy2(source, data / name)
        snapshot = {'schemaVersion': 1, 'productId': PRODUCT_ID, 'previousVersion': descriptor['appVersion'],
                    'previousBundleId': descriptor['bundleId'], 'previousVerified': True,
                    'previousInstall': str(current), 'applicationBackup': str(backup / 'application'),
                    'dataSnapshot': str(data), 'dataHashes': _tree_hashes(data), 'activeDescriptor': descriptor,
                    'launcher':launcher.snapshot(self.root,backup)}
        _atomic_json(backup / 'snapshot.json', snapshot)
        self._verify_snapshot(snapshot)
        return snapshot

    def _copy_metadata(self, source, destination):
        """Back up known JSON state only; never copy media/model/cache payloads."""
        _guard_path(source)
        count = 0
        for path in source.rglob('*'):
            _reject_links(path)
            count += 1
            if count > 100000:
                raise CutError('INSTALL_SIZE', 'User-state backup exceeds file-count limit')
            if not path.is_file() or path.suffix.casefold() not in {'.json', '.jsonl'}:
                continue
            raw = path.read_bytes()
            if len(raw) > 64 * 1024 * 1024:
                raise CutError('INSTALL_SIZE', 'User-state file exceeds backup limit')
            if path.suffix.casefold() == '.json':
                documents = [json.loads(raw)]
            else:
                documents = [json.loads(line) for line in raw.splitlines() if line.strip()]
            if any(isinstance(item, dict) and item.get('schemaVersion', 1) != 1 for item in documents):
                raise CutError('INSTALL_DATA_SCHEMA', 'User-state schema is not supported for recovery')
            target = destination / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            with target.open('r+b') as stream: os.fsync(stream.fileno())
            if _hash(target) != hashlib.sha256(raw).hexdigest() or _hash(path) != hashlib.sha256(raw).hexdigest():
                raise CutError('INSTALL_DATA_CHANGED', 'User state changed while being backed up')

    def _verify_snapshot(self, snapshot):
        backup = Path(snapshot['applicationBackup']).parent
        if (self.backups not in backup.resolve().parents or not re.fullmatch('[0-9a-f]{32}', backup.name)
                or Path(snapshot['applicationBackup']) != backup / 'application'
                or Path(snapshot['dataSnapshot']) != backup / 'data' or snapshot.get('previousVerified') is not True
                or json.loads((backup / 'snapshot.json').read_text(encoding='utf-8')) != snapshot):
            raise CutError('INSTALL_SNAPSHOT', 'Recovery descriptor is not the recorded installation backup')
        self._verify_directory(backup / 'application', snapshot['previousVersion'], snapshot['previousBundleId'])
        from . import launcher_transaction as launcher
        launcher.verify_snapshot(self.root,backup,snapshot['launcher'])
        if _tree_hashes(backup / 'data') != snapshot['dataHashes']:
            raise CutError('INSTALL_SNAPSHOT', 'Recovery data snapshot hash does not match')
        for relative in snapshot['dataHashes']:
            parts = PurePosixPath(relative).parts
            if not parts or parts[0] not in DATA_DIRECTORIES + DATA_FILES:
                raise CutError('INSTALL_SNAPSHOT', 'Recovery snapshot includes an unapproved data path')

    def rollback(self, snapshot):
        self._require_host_exit()
        self._verify_snapshot(snapshot)
        from . import launcher_transaction as launcher
        replacement=None
        if self.launcher_journal.exists():
            replacement=launcher.read(self.launcher_journal)
            if replacement.get('snapshotHash')!=canonical_hash(snapshot):
                launcher.historical(self.root,replacement,snapshot);replacement=None
            else:_atomic_json(self.launcher_journal,dict(replacement,state='ROLLING_BACK'))
        backup = Path(snapshot['applicationBackup'])
        registrations = [item for item in self.registrations() if item['id'] == PRODUCT_ID]
        if registrations and registrations[0]['version'] != snapshot['previousVersion']:
            self._command('/remove', PRODUCT_ID)
        self._require_host_exit()
        self._command('/install', backup / PANEL_NAME)
        if not self._registered(snapshot['previousVersion']):
            raise CutError('UPIA_REGISTRATION', 'Adobe previous panel restoration was not verified')
        self._require_host_exit()
        previous = self.versions / snapshot['previousVersion']
        if previous.exists():
            self._verify_directory(previous, snapshot['previousVersion'], snapshot['previousBundleId'])
        else:
            shutil.copytree(backup, previous)
        data = Path(snapshot['dataSnapshot'])
        displaced = data.parent / ('displaced-' + uuid.uuid4().hex)
        for relative in snapshot['dataHashes']:
            target, source = self.root / relative, data / relative
            for parent in target.parents:
                if parent == self.root: break
                _reject_links(parent)
            _reject_links(target)
            if target.exists() and _hash(target) != snapshot['dataHashes'][relative]:
                preserved = displaced / relative
                preserved.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(target, preserved)
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + '.' + uuid.uuid4().hex + '.restore')
            shutil.copy2(source, temporary)
            with temporary.open('r+b') as stream: os.fsync(stream.fileno())
            os.replace(temporary, target)
            if _hash(target) != snapshot['dataHashes'][relative]:
                raise CutError('INSTALL_DATA_CHANGED', 'Restored user-state file could not be verified')
        launcher.restore(self.root,snapshot['launcher'],replacement['asset']['sha256'] if replacement else None)
        _atomic_json(self.active, snapshot['activeDescriptor'])
        if not self.verify_previous(snapshot):
            raise CutError('INSTALL_ROLLBACK', 'Previous application/panel pair could not be verified after restore')
        from .migration_registration import restore
        integration = restore(self.root, snapshot, registry=self.migration_registry)
        if replacement:_atomic_json(self.launcher_journal,dict(replacement,state='ROLLED_BACK'))
        return {'restored': True, 'previousVersion': snapshot['previousVersion'], 'dataRestored': True,
                'migrationRegistration': integration}

    def verify_previous(self, snapshot):
        try:
            descriptor, directory = self._active_descriptor()
            if descriptor['appVersion'] != snapshot['previousVersion']:
                return False
            if snapshot.get('previousBundleId') and descriptor['bundleId'] != snapshot['previousBundleId']:
                return False
            self._verify_directory(directory, descriptor['appVersion'], descriptor['bundleId'])
            from . import launcher_transaction as launcher
            if not launcher.required(self.root,descriptor['appVersion'],descriptor['bundleId']):return False
            if snapshot.get('launcher') and launcher.ownership(self.root).get('sha256')!=snapshot['launcher'].get('sha256'):return False
            return self._registered(descriptor['appVersion'])
        except Exception:
            return False

    def verify_application(self, version, bundle_id):
        """Prove the active files and Adobe registration without claiming host load."""
        try:
            descriptor, directory = self._active_descriptor()
            if descriptor['appVersion'] != version or descriptor['bundleId'] != bundle_id:
                return False
            self._verify_directory(directory, version, bundle_id)
            from . import launcher_transaction as launcher
            if not launcher.required(self.root,version,bundle_id):return False
            return self._registered(version)
        except Exception:
            return False

    def verify_code(self, version, bundle_id):
        """Recovery-only code receipt check; never attests Adobe registration.

        Admission stays closed and real activation still calls verify().
        """
        try:
            descriptor, directory = self._active_descriptor()
            if descriptor['appVersion'] != version or descriptor['bundleId'] != bundle_id:
                return False
            self._verify_directory(directory, version, bundle_id)
            from . import launcher_transaction as launcher
            if not launcher.required(self.root,version,bundle_id):return False
            return True
        except Exception:
            return False

    def verify(self, version, bundle_id, receipt):
        try:
            descriptor, directory = self._active_descriptor()
            if (descriptor['appVersion'] != version or descriptor['bundleId'] != bundle_id
                    or receipt.get('panelVersion') != version or receipt.get('companionVersion') != version
                    or receipt.get('bundleId') != bundle_id or receipt.get('handshake') is not True
                    or receipt.get('dataReadable') is not True or self.activation_probe is None):
                return False
            self._verify_directory(directory, version, bundle_id)
            from . import launcher_transaction as launcher
            if not launcher.required(self.root,version,bundle_id):return False
            return self._registered(version) and self.activation_probe(version, bundle_id, receipt) is True
        except Exception:
            return False
