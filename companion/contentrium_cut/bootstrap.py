"""Private installer provisioning and canonical installation identity.

No environment-directory discovery, Developer seeding or implicit migration.
Mapping evidence must come from an authorized installed UXP getDataFolder probe.
"""
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import secrets
import base64
import uuid

from .auth import validate_bootstrap
from .contract import CutError
from .windows_install import _guard_path, _kernel

PRODUCT = 'com.contentrium.cut'
BOOTSTRAP_NAME = 'contentrium-bootstrap.json'
PRIVATE_PATH = Path('private/auth-bootstrap.json')


def current_user_sid():
    if os.name != 'nt': raise CutError('WINDOWS_REQUIRED', 'Native user identity is required.')
    api = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    api.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    api.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    api.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    token = wintypes.HANDLE()
    if not api.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)): raise CutError('INSTALL_IDENTITY', 'Native user identity is unavailable.')
    try:
        size = wintypes.DWORD()
        api.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        data = ctypes.create_string_buffer(size.value)
        if not api.GetTokenInformation(token, 1, data, size, ctypes.byref(size)): raise CutError('INSTALL_IDENTITY', 'Native user identity is unavailable.')
        sid = ctypes.cast(data, ctypes.POINTER(ctypes.c_void_p))[0]
        result = wintypes.LPWSTR()
        if not api.ConvertSidToStringSidW(sid, ctypes.byref(result)): raise CutError('INSTALL_IDENTITY', 'Native user identity is unavailable.')
        try: return result.value
        finally: kernel.LocalFree(result)
    finally: kernel.CloseHandle(token)


def known_folder(kind):
    """SHGetKnownFolderPath for current user; never trust redirected env vars."""
    if os.name != 'nt': raise CutError('WINDOWS_REQUIRED', 'Native known folders are required.')
    ids = {'local': 'F1B32785-6FBA-4FCF-9D55-7B8E7F157091', 'roaming': '3EB685DB-65F9-4CF6-A03A-E3EF65729F3D'}
    class GUID(ctypes.Structure):
        _fields_ = [('Data1', wintypes.DWORD), ('Data2', wintypes.WORD), ('Data3', wintypes.WORD), ('Data4', ctypes.c_ubyte * 8)]
    guid = GUID.from_buffer_copy(uuid.UUID(ids[kind]).bytes_le)
    shell = ctypes.WinDLL('shell32')
    shell.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(GUID), wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(wintypes.LPWSTR)]
    shell.SHGetKnownFolderPath.restype = ctypes.c_long
    ole = ctypes.WinDLL('ole32')
    ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    result = wintypes.LPWSTR()
    if shell.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(result)) != 0:
        raise CutError('INSTALL_LOCATION', 'Native known folder is unavailable.')
    try: return Path(result.value)
    finally: ole.CoTaskMemFree(result)


def protect_user_system(path, sid):
    """Replace DACL on our own file with protected current-user/SYSTEM access."""
    if os.name != 'nt': raise CutError('WINDOWS_REQUIRED', 'Native private file protection is required.')
    if not re.fullmatch(r'S-1-[0-9-]+', sid): raise CutError('INSTALL_IDENTITY', 'Native user identity is invalid.')
    api = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    api.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD)]
    api.GetSecurityDescriptorDacl.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL), ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.BOOL)]
    api.SetNamedSecurityInfoW.argtypes = [wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    api.SetNamedSecurityInfoW.restype = wintypes.DWORD
    descriptor = ctypes.c_void_p()
    inherit = 'OICI' if Path(path).is_dir() else ''
    sddl = f'D:P(A;{inherit};FA;;;{sid})(A;{inherit};FA;;;SY)'
    if not api.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None):
        raise CutError('INSTALL_PROTECTION', 'Private file protection failed.')
    try:
        present, defaulted, acl = wintypes.BOOL(), wintypes.BOOL(), ctypes.c_void_p()
        if (not api.GetSecurityDescriptorDacl(descriptor, ctypes.byref(present), ctypes.byref(acl), ctypes.byref(defaulted)) or
                not present or api.SetNamedSecurityInfoW(str(path), 1, 0x80000004, None, None, acl, None)):
            raise CutError('INSTALL_PROTECTION', 'Private file protection failed.')
    finally: kernel.LocalFree(descriptor)


def _physical_path(path):
    """Resolve Windows virtualization aliases using the actual open handle."""
    if os.name != 'nt': return path.resolve()
    ancestor = path; remaining = []
    while not ancestor.exists():
        if ancestor == ancestor.parent: raise CutError('INSTALL_LOCATION', 'Native installation ancestor is unavailable.')
        remaining.append(ancestor.name); ancestor = ancestor.parent
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE,wintypes.LPWSTR,wintypes.DWORD,wintypes.DWORD]
    kernel.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateFileW(str(ancestor), 0, 7, None, 3, 0x02000000, None)
    if handle == ctypes.c_void_p(-1).value: raise CutError('INSTALL_LOCATION', 'Native physical installation location is unavailable.')
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        size = kernel.GetFinalPathNameByHandleW(handle,buffer,len(buffer),0)
        if not size or size >= len(buffer): raise CutError('INSTALL_LOCATION', 'Native physical installation location is unavailable.')
        value = buffer.value
        if value.startswith('\\\\?\\UNC\\'): value = '\\\\' + value[8:]
        elif value.startswith('\\\\?\\'): value = value[4:]
        return Path(value).joinpath(*reversed(remaining))
    finally: kernel.CloseHandle(handle)


def canonical_root(value):
    path = Path(value)
    if not path.is_absolute() or '..' in path.parts:
        raise CutError('INSTALL_LOCATION', 'An explicit canonical installation root is required.')
    _guard_path(path)  # Reject original reparse ancestors BEFORE resolving them.
    result = _physical_path(path)
    _guard_path(result)
    return result


def _read(path):
    _guard_path(path)
    if not path.is_file(): raise CutError('BOOTSTRAP_MISSING', 'Private installation bootstrap is required.')
    if path.stat().st_size > 4096: raise CutError('BOOTSTRAP_INVALID', 'Private installation bootstrap is invalid.')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result: raise ValueError()
            result[key] = value
        return result
    try: return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=pairs)
    except (ValueError, OSError, UnicodeError): raise CutError('BOOTSTRAP_INVALID', 'Private installation bootstrap is invalid.') from None


@dataclass(frozen=True)
class InstallLocation:
    root: Path
    installation_id: str
    key_id: str
    owner_sid: str


def resolve_installation(root=None, *, launcher=None, owner_sid=None, known_local=None):
    sid = owner_sid or current_user_sid()
    selected = root if root is not None else Path(known_local or known_folder('local')) / 'Contentrium CUT'
    path = canonical_root(selected)
    descriptor = _read(path / 'install-location.json')
    bootstrap, _ = validate_bootstrap(_read(path / PRIVATE_PATH))
    if owner_sid is None:
        verify_private_protection(path / 'install-location.json', sid)
        verify_private_protection(path / PRIVATE_PATH, sid)
    fields = {'schemaVersion', 'productId', 'canonicalRoot', 'ownerSid', 'installationId', 'keyId'}
    if (not isinstance(descriptor, dict) or set(descriptor) != fields or type(descriptor['schemaVersion']) is not int or
            descriptor['schemaVersion'] != 1 or descriptor['productId'] != PRODUCT or descriptor['ownerSid'] != sid or
            canonical_root(descriptor['canonicalRoot']) != path or descriptor['installationId'] != bootstrap['installationId'] or
            descriptor['keyId'] != bootstrap['keyId']):
        raise CutError('INSTALL_LOCATION', 'Protected installation identity does not match.')
    if launcher is not None and canonical_root(Path(launcher).parent) != path:
        raise CutError('INSTALL_LOCATION', 'Stable dispatcher location does not match.')
    return InstallLocation(path, descriptor['installationId'], descriptor['keyId'], sid)


def verify_private_protection(path, sid):
    """Native DACL check: protected, only full-access current user and SYSTEM."""
    if os.name != 'nt': raise CutError('WINDOWS_REQUIRED', 'Native private file protection is required.')
    api = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    api.GetNamedSecurityInfoW.argtypes = [wintypes.LPWSTR,ctypes.c_int,wintypes.DWORD,ctypes.c_void_p,ctypes.c_void_p,ctypes.POINTER(ctypes.c_void_p),ctypes.c_void_p,ctypes.POINTER(ctypes.c_void_p)]
    api.GetNamedSecurityInfoW.restype = wintypes.DWORD
    api.GetSecurityDescriptorControl.argtypes = [ctypes.c_void_p,ctypes.POINTER(wintypes.WORD),ctypes.POINTER(wintypes.DWORD)]
    api.GetAclInformation.argtypes = [ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.c_int]
    api.GetAce.argtypes = [ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(ctypes.c_void_p)]
    api.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p,ctypes.POINTER(wintypes.LPWSTR)]
    acl, descriptor = ctypes.c_void_p(), ctypes.c_void_p()
    if api.GetNamedSecurityInfoW(str(path),1,4,None,None,ctypes.byref(acl),None,ctypes.byref(descriptor)):
        raise CutError('INSTALL_PROTECTION','Private installation protection could not be verified.')
    try:
        control, revision = wintypes.WORD(), wintypes.DWORD()
        class ACLSize(ctypes.Structure):
            _fields_ = [('AceCount',wintypes.DWORD),('AclBytesInUse',wintypes.DWORD),('AclBytesFree',wintypes.DWORD)]
        size = ACLSize()
        if (not api.GetSecurityDescriptorControl(descriptor,ctypes.byref(control),ctypes.byref(revision)) or
                not control.value & 0x1000 or not acl or not api.GetAclInformation(acl,ctypes.byref(size),ctypes.sizeof(size),2) or size.AceCount != 2):
            raise CutError('INSTALL_PROTECTION','Private installation protection could not be verified.')
        identities = set()
        for index in range(size.AceCount):
            ace = ctypes.c_void_p()
            if not api.GetAce(acl,index,ctypes.byref(ace)): raise CutError('INSTALL_PROTECTION','Private installation protection could not be verified.')
            data = ctypes.cast(ace,ctypes.POINTER(ctypes.c_ubyte))
            mask = ctypes.c_uint32.from_address(ace.value+4).value
            if data[0] != 0 or mask != 0x1f01ff: raise CutError('INSTALL_PROTECTION','Private installation protection could not be verified.')
            text = wintypes.LPWSTR()
            if not api.ConvertSidToStringSidW(ace.value+8,ctypes.byref(text)): raise CutError('INSTALL_PROTECTION','Private installation protection could not be verified.')
            try: identities.add(text.value)
            finally: kernel.LocalFree(text)
        if identities != {sid,'S-1-5-18'}: raise CutError('INSTALL_PROTECTION','Private installation protection could not be verified.')
    finally: kernel.LocalFree(descriptor)


def load_bootstrap(location):
    bootstrap, _ = validate_bootstrap(_read(location.root / PRIVATE_PATH))
    if bootstrap['installationId'] != location.installation_id or bootstrap['keyId'] != location.key_id:
        raise CutError('BOOTSTRAP_INVALID', 'Private installation identity does not match.')
    return bootstrap


def _write_private(path, value, sid, protect):
    _guard_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('xb') as stream:
            protect(temporary, sid)  # Empty file becomes private before key bytes are written.
            stream.write(json.dumps(value, separators=(',', ':'), ensure_ascii=True).encode())
            stream.flush(); os.fsync(stream.fileno())
        if os.name == 'nt':
            if not _kernel().MoveFileExW(str(temporary), str(path), 0x8):
                raise CutError('INSTALL_PROTECTION', 'Private write-through commit failed.')
        else:
            # Never replace a preexisting file, even during an unexpected concurrent provision.
            os.link(temporary, path); temporary.unlink()
    finally: temporary.unlink(missing_ok=True)


def provision(root, mapping_evidence, *, owner_sid=None, roaming_root=None, protect=protect_user_system):
    """Testable installer hook. Native mapping collection is deliberately separate."""
    path = canonical_root(root)
    sid = owner_sid or current_user_sid()
    roaming = canonical_root(roaming_root or known_folder('roaming'))
    expected = roaming / 'Adobe/UXP/PluginsStorage/PPRO/26/External/com.contentrium.cut/PluginData'
    fields = {'schemaVersion', 'productId', 'hostMajor', 'verifiedBy', 'canonicalPluginData', 'receiptHash'}
    evidence = mapping_evidence
    if (not isinstance(evidence, dict) or set(evidence) != fields or type(evidence['schemaVersion']) is not int or
            evidence['schemaVersion'] != 1 or evidence['productId'] != PRODUCT or type(evidence['hostMajor']) is not int or
            evidence['hostMajor'] != 26 or evidence['verifiedBy'] != 'installed-uxp-getDataFolder' or
            not isinstance(evidence['receiptHash'], str) or not re.fullmatch('[0-9a-f]{64}', evidence['receiptHash']) or
            canonical_root(evidence['canonicalPluginData']) != expected or not expected.is_dir()):
        raise CutError('BOOTSTRAP_MAPPING', 'Supported installed panel storage mapping is required.')
    target = expected / BOOTSTRAP_NAME
    _guard_path(target)
    descriptor_path, private = path / 'install-location.json', path / PRIVATE_PATH
    if descriptor_path.exists() or private.exists():
        # Only a caller-supplied isolated SID is a test adapter. An internally
        # resolved native SID must retain resolve_installation's DACL boundary.
        location = resolve_installation(path, owner_sid=owner_sid)
        value = load_bootstrap(location)
    else:
        if target.exists(): raise CutError('BOOTSTRAP_CONFLICT', 'Existing bootstrap cannot be adopted or overwritten.')
        value = dict(schemaVersion=1, productId=PRODUCT, installationId=uuid.uuid4().hex, keyId=uuid.uuid4().hex,
                     authProtocol=1, endpoint='http://127.0.0.1:41737', secret=base64.b64encode(secrets.token_bytes(32)).decode())
        location = InstallLocation(path, value['installationId'], value['keyId'], sid)
    if target.exists():
        if owner_sid is None: verify_private_protection(target, sid)
        if _read(target) != value:
            raise CutError('BOOTSTRAP_CONFLICT', 'Existing bootstrap cannot be adopted or overwritten.')
    # Unknown files remain untouched. Partial owned commits fail closed for explicit repair.
    if not private.exists():
        private.parent.mkdir(parents=True, exist_ok=True); protect(private.parent, sid)
        _write_private(private, value, sid, protect)
        descriptor = dict(schemaVersion=1, productId=PRODUCT, canonicalRoot=str(path), ownerSid=sid,
                          installationId=location.installation_id, keyId=location.key_id)
        _write_private(descriptor_path, descriptor, sid, protect)
    if not target.exists(): _write_private(target, value, sid, protect)
    return location
