"""Verified hidden supervisor and runtime lifecycle; no GUI/model imports.

The stable signed dispatcher exits. A versioned supervisor owns exact Popen
handles and inherited anonymous pipes. Handoff tokens never enter argv/state.
"""
import ctypes
from ctypes import wintypes
from collections import deque
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time

from .bootstrap import canonical_root, load_bootstrap, current_user_sid
from .contract import CutError
from .windows_install import WindowsInstallation, WindowsNamedMutex, windows_process_snapshot, process_identity, _guard_path, _atomic_json
from .updater import SemVer

HANDOFF_EXIT = 75
UPDATE_TERMINAL = {'COMPLETE', 'CANCELED', 'FAILED_BEFORE_REPLACE', 'ROLLED_BACK', 'IDLE', 'UNAVAILABLE'}


def pending_update(root):
    journal = Path(root) / 'updates/journal.json'
    _guard_path(journal)
    if not journal.exists(): return False
    try: return json.loads(journal.read_text(encoding='utf-8')).get('updateState') not in UPDATE_TERMINAL
    except Exception: return True


@dataclass(frozen=True)
class VerifiedRuntime:
    executable: Path
    config_path: Path
    config: dict
    recovery: bool = False


def verify_runtime(location, installation=None, *, recovery=False):
    root = canonical_root(location.root)
    installation = installation or WindowsInstallation(root)
    try:
        pointer = root / 'app/active.json'; _guard_path(pointer)
        descriptor = json.loads(pointer.read_text(encoding='utf-8'))
        version, bundle = descriptor['appVersion'], descriptor['bundleId']; SemVer(version)
        if (descriptor.get('schemaVersion') != 1 or descriptor.get('productId') != 'com.contentrium.cut' or
                descriptor.get('versionDirectory') != 'versions/' + version): raise ValueError()
        directory = root / 'app/versions' / version
        executable, config_path = directory / 'Contentrium CUT.exe', directory / 'config.json'
        _guard_path(executable); _guard_path(config_path)
        if not executable.is_file() or not config_path.is_file(): raise ValueError()
        if recovery:
            if not pending_update(root) or installation.verify_code(version, bundle) is not True: raise ValueError()
        elif installation.verify_application(version, bundle) is not True: raise ValueError()
        config = json.loads(config_path.read_text(encoding='utf-8-sig'))
        if config.get('appVersion') != version or config.get('bundleId') != bundle: raise ValueError()
        return VerifiedRuntime(executable, config_path, config, recovery)
    except Exception as error:
        raise CutError('LAUNCH_VERIFY', 'Installed runtime code could not be verified.') from error


def _packet(value):
    if (not isinstance(value, dict) or set(value) != {'token', 'updateId'} or
            not isinstance(value['token'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{20,256}', value['token']) or
            not isinstance(value['updateId'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{3,100}', value['updateId'])):
        raise CutError('UPDATE_HANDOFF', 'Protected handoff packet is invalid.')
    return value


def spawn_verified(location, verified, *, supervisor=False, activation=None, popen=None):
    args = [str(verified.executable), '--supervisor' if supervisor else '--headless', '--root', str(location.root), '--config', str(verified.config_path)]
    if verified.recovery: args += ['--recovery-only']
    if not supervisor: args += ['--supervised']
    if activation:
        _packet(activation)
        args += ['--activation-update-id', activation['updateId'], '--activation-stdin']
    child = (popen or subprocess.Popen)(args, cwd=str(verified.executable.parent), close_fds=True,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        stdin=subprocess.PIPE if activation else subprocess.DEVNULL,
        stdout=subprocess.DEVNULL if supervisor else subprocess.PIPE, stderr=subprocess.DEVNULL)
    if activation:
        try:
            child.stdin.write(json.dumps(activation, separators=(',', ':')).encode() + b'\n')
            child.stdin.flush(); child.stdin.close()
        except Exception:
            raise CutError('UPDATE_HANDOFF', 'Protected handoff delivery failed.') from None
    return child


def _session_id(pid):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    session = wintypes.DWORD()
    if not kernel.ProcessIdToSessionId(pid, ctypes.byref(session)): raise CutError('HOST_IDENTITY_REQUIRED', 'Native session could not be verified.')
    return session.value


def _process_sid(pid):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    api = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]; kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]; kernel.LocalFree.argtypes = [ctypes.c_void_p]
    api.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    api.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    api.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    handle = kernel.OpenProcess(0x1000, False, pid)
    token = wintypes.HANDLE()
    if not handle: raise CutError('HOST_IDENTITY_REQUIRED', 'Host ownership could not be verified.')
    try:
        if not api.OpenProcessToken(handle, 8, ctypes.byref(token)): raise CutError('HOST_IDENTITY_REQUIRED', 'Host ownership could not be verified.')
        size = wintypes.DWORD(); api.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        buffer = ctypes.create_string_buffer(size.value)
        if not api.GetTokenInformation(token, 1, buffer, size, ctypes.byref(size)): raise CutError('HOST_IDENTITY_REQUIRED', 'Host ownership could not be verified.')
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]; text = wintypes.LPWSTR()
        if not api.ConvertSidToStringSidW(sid, ctypes.byref(text)): raise CutError('HOST_IDENTITY_REQUIRED', 'Host ownership could not be verified.')
        try: return text.value
        finally: kernel.LocalFree(text)
    finally:
        if token: kernel.CloseHandle(token)
        kernel.CloseHandle(handle)


def child_process_identity(child):
    """Inspect the exact Popen process handle, never a reopened/reused PID."""
    if os.name != 'nt' or not getattr(child,'_handle',None):
        raise CutError('PROCESS_IDENTITY','A pinned native child handle is required.')
    kernel = ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE]+[ctypes.POINTER(wintypes.FILETIME)]*4
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE,wintypes.DWORD,wintypes.LPWSTR,ctypes.POINTER(wintypes.DWORD)]
    created, exited, system, user = (wintypes.FILETIME() for _ in range(4))
    buffer = ctypes.create_unicode_buffer(32768); length=wintypes.DWORD(len(buffer))
    if (not kernel.GetProcessTimes(int(child._handle),ctypes.byref(created),ctypes.byref(exited),ctypes.byref(system),ctypes.byref(user)) or
            not kernel.QueryFullProcessImageNameW(int(child._handle),0,buffer,ctypes.byref(length))):
        raise CutError('PROCESS_IDENTITY','Pinned runtime identity could not be verified.')
    return dict(pid=child.pid,imagePath=str(canonical_root(buffer.value)),createdAtTicks=str((created.dwHighDateTime<<32)|created.dwLowDateTime))


def allowed_premiere(location):
    session = _session_id(os.getpid()); result = []
    for row in windows_process_snapshot():
        if row.get('imageName', '').casefold() not in {'adobe premiere pro.exe', 'premiere pro.exe'}: continue
        try:
            if _session_id(row['pid']) != session or _process_sid(row['pid']) != location.owner_sid: continue
        except CutError:
            # A snapshot is not a lifetime guarantee. Skip only proven exit;
            # access denial/unknown identity still closes this observation.
            if process_identity(row['pid']) is None: continue
            raise
        actual = process_identity(row['pid'])
        if actual is None: continue
        if Path(actual['imagePath']).name.casefold() not in {'adobe premiere pro.exe', 'premiere pro.exe'}:
            raise CutError('HOST_IDENTITY_REQUIRED', 'Host image identity does not match.')
        result.append(dict(actual, createTime=actual['createdAtTicks'], alive=True, processName='Adobe Premiere Pro'))
    return result


def supervisor_lease(root, owner_sid):
    """The same ownership namespace for supervision and maintenance handoff."""
    identity = str(root).casefold() + '|' + owner_sid
    return WindowsNamedMutex(name='Local\\ContentriumCUT-Supervisor-' + hashlib.sha256(identity.encode()).hexdigest())


class Supervisor:
    def __init__(self, location, *, installation=None, premiere_probe=None, spawn=None, clock=time.time,
                 write_state=None, child_identity=None, activation=None, lease=None):
        self.location = location; self.installation = installation or WindowsInstallation(location.root)
        self.premiere_probe = premiere_probe or (lambda: bool(allowed_premiere(location)))
        self.spawn = spawn or spawn_verified; self.clock = clock
        self.write_state = write_state or (lambda value: _atomic_json(location.root / 'runtime/lifecycle.json', value))
        self.child_identity = child_identity or child_process_identity
        self.lease = lease or supervisor_lease(location.root, location.owner_sid)
        self.child = None; self.started_at = None; self.failures = deque(); self.retry_at = None
        self.activation = activation; self.handoff = None; self.control_lock = threading.Lock()
        self.blocked = False; self.exiting = False; self.consecutive = 0
        self.host_failures = 0; self.host_retry_at = None

    def _state(self, state, error=None):
        value = dict(schemaVersion=1, installationId=self.location.installation_id, state=state,
                     runtimeIdentity=self.child_identity(self.child) if self.child else None,
                     retryAt=self.host_retry_at if self.host_retry_at is not None else self.retry_at,
                     failureCount=len(self.failures), errorCode=error)
        self.write_state(value)

    def _read_control(self, child):
        # Read ONLY our child's inherited pipe; not a PID/name-reopened channel.
        try:
            line = child.stdout.readline(2049)
            if len(line) > 2048 or not line.endswith(b'\n'): return
            packet = _packet(json.loads(line))
            with self.control_lock:
                if self.child is child: self.handoff = packet
        except Exception: pass  # Exit 75 without a packet enters explicit recovery.

    def step(self):
        if self.exiting or self.blocked: return
        now = self.clock()
        while self.failures and self.failures[0] <= now - 600: self.failures.popleft()
        if self.child:
            result = self.child.poll()
            if result is None:
                if now-self.started_at >= 60: self.consecutive = 0
                return
            if getattr(self.child, 'stdout', None) is not None:
                self.control_thread.join(timeout=1)
            self.child = None
            if result == HANDOFF_EXIT:
                with self.control_lock: packet = self.handoff
                if not packet:
                    self.blocked = True; self._state('RECOVERY_REQUIRED', 'HANDOFF_REQUIRED'); return
                journal = json.loads((self.location.root / 'updates/journal.json').read_text(encoding='utf-8'))
                if journal.get('updateId') != packet['updateId']:
                    self.blocked = True; self._state('RECOVERY_REQUIRED', 'HANDOFF_REQUIRED'); return
                verified = verify_runtime(self.location, self.installation)
                self._state('SUPERVISOR_HANDOFF')
                self.lease.close()  # New version acquires the distinct lease.
                self.exiting = True
                self.spawn(self.location, verified, supervisor=True, activation=packet)
                return
            if result != 0:
                self.failures.append(now); self.consecutive += 1
                if len(self.failures) >= 6:
                    self.blocked = True; self.retry_at = None; self._state('CRASH_LIMIT', 'RUNTIME_CRASH_LIMIT'); return
                self.retry_at = now + [1, 2, 4, 8, 16, 30][min(self.consecutive-1, 5)]
                self._state('BACKOFF', 'RUNTIME_EXIT'); return
            self.retry_at = None; self._state('IDLE')
        if self.retry_at is not None and now < self.retry_at: return
        if not self.activation and not pending_update(self.location.root):
            if self.host_retry_at is not None and now < self.host_retry_at: return
            try:
                host_present = self.premiere_probe()
            except CutError as error:
                if error.code not in {'HOST_IDENTITY_REQUIRED', 'PROCESS_IDENTITY', 'PROCESS_SNAPSHOT'}: raise
                # Host observation faults never spend/reset runtime crash budget
                # or permanently consume this supervisor's singleton lease.
                self.host_failures = min(self.host_failures + 1, 6)
                self.host_retry_at = now + [1, 2, 4, 8, 16, 30][self.host_failures-1]
                self._state('RECOVERY_REQUIRED' if self.host_failures >= 3 else 'WAITING_FOR_HOST', error.code)
                return
            recovered_observation = self.host_failures > 0
            self.host_failures = 0; self.host_retry_at = None
            if not host_present:
                if recovered_observation: self._state('IDLE')
                return
        try:
            verified = verify_runtime(self.location, self.installation)
        except CutError:
            verified = verify_runtime(self.location, self.installation, recovery=True)
        self.child = self.spawn(self.location, verified, activation=self.activation)
        self.activation = None; self.handoff = None; self.started_at = now; self.retry_at = None
        self.host_failures = 0; self.host_retry_at = None
        if getattr(self.child, 'stdout', None) is not None:
            self.control_thread = threading.Thread(target=self._read_control, args=(self.child,), daemon=True)
            self.control_thread.start()
        self._state('RECOVERY_RUNNING' if verified.recovery else 'RUNNING')

    def run(self, stop=None):
        stop = stop or threading.Event()
        with self.lease:
            while not self.exiting and not stop.wait(.5):
                try: self.step()
                except CutError as error:
                    self.blocked = True; self._state('RECOVERY_REQUIRED', error.code)
                except Exception:
                    self.blocked = True; self._state('RECOVERY_REQUIRED', 'LIFECYCLE_FAILED')
        # Never terminate Premiere, unowned processes or the exact child on a
        # supervisor exit. Runtime independently drains on proven host exit.
        return 0


def inherited_pipe(which):
    """Recover inherited handles for PyInstaller --windowed (sys.std* is None)."""
    stream = sys.stdin if which == 'input' else sys.stdout
    if os.name != 'nt': return stream
    import msvcrt
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetStdHandle.argtypes = [wintypes.DWORD]; kernel.GetStdHandle.restype = wintypes.HANDLE
    kernel.GetFileType.argtypes = [wintypes.HANDLE]; kernel.GetFileType.restype = wintypes.DWORD
    handle = kernel.GetStdHandle(-10 if which == 'input' else -11)
    if not handle or kernel.GetFileType(handle) != 3: raise CutError('UPDATE_HANDOFF', 'An inherited protected pipe is required.')
    descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY if which == 'input' else os.O_WRONLY)
    return os.fdopen(descriptor, 'r' if which == 'input' else 'w', encoding='utf-8', newline='\n')


def read_activation(update_id):
    line = inherited_pipe('input').readline(2049)
    if len(line) > 2048 or not line.endswith('\n'): raise CutError('UPDATE_HANDOFF', 'Protected handoff is incomplete.')
    try: value = _packet(json.loads(line))
    except (ValueError, TypeError): raise CutError('UPDATE_HANDOFF', 'Protected handoff is invalid.') from None
    if value['updateId'] != update_id: raise CutError('UPDATE_HANDOFF', 'Update identity does not match.')
    return value


class HeadlessLifecycle:
    def __init__(self, location, service, mutex, config, *, emit_handoff=None):
        self.location, self.service, self.mutex, self.config = location, service, mutex, config
        self.emit_handoff = emit_handoff

    def tick(self):
        update = self.service.update_state()
        if update.get('updateState') == 'PENDING_ACTIVATION':
            if update.get('newVersion') != self.config['appVersion']:
                if self.emit_handoff is None: raise CutError('UPDATE_HANDOFF', 'Protected supervisor control is required.')
                # Metadata reads are bounded but may outlive the shorter native
                # drain deadline. Keep the pending handoff retryable until the
                # owned read finishes; never issue a ticket before full drain.
                check=getattr(self.service,'update_check',None)
                if check is not None and check.is_alive():return None
                self.service.quiesce_for_handoff()
                ticket = self.service.updater.create_activation_handoff()
                self.mutex.close()
                self.emit_handoff(_packet(dict(token=ticket['token'], updateId=ticket['updateId'])))
                return HANDOFF_EXIT
            try: self.service.finalize_activation()
            except CutError as error:
                if error.code != 'UPDATE_ACTIVATION': raise
            update = self.service.update_state()
        unresolved = update.get('updateState') not in UPDATE_TERMINAL or pending_update(self.location.root)
        # A drained recovery marker keeps live editing closed, but must not
        # strand the engine after the actual host has exited.
        drained = self.service.jobs.quiescent() and self.service.validations.quiescent()
        if self.config.get('recoveryOnly') and not unresolved and drained:
            # Explicit recovery resolved; restart verified normal code for new
            # auth/heartbeat instead of reopening this recovery-only process.
            self.service.close(); self.mutex.close(); return 0
        if (self.service._host_exited() and not unresolved and drained and
                not any(value.get('active') for value in self.service.applies.values())):
            self.service.close(); self.mutex.close(); return 0
        return None


def run_headless(location, verified, *, activation=None, supervised=False, service_factory=None, stop=None):
    from .service import CutService
    mutex = WindowsNamedMutex(location.root).__enter__()
    service = None
    try:
        if not getattr(sys, 'frozen', False) or canonical_root(sys.executable) != verified.executable:
            raise CutError('LAUNCH_VERIFY', 'The actual verified frozen runtime is required.')
        config = dict(verified.config, privateBootstrap=load_bootstrap(location), recoveryOnly=verified.recovery)
        saved = location.root/'settings.json'
        _guard_path(saved)
        if saved.is_file():
            settings=json.loads(saved.read_text(encoding='utf-8'))
            ffmpeg=settings.get('ffmpeg')
            if isinstance(ffmpeg,str) and Path(ffmpeg).is_file():os.environ['CONTENTRIUM_FFMPEG']=ffmpeg
        if activation:
            journal = json.loads((location.root / 'updates/journal.json').read_text(encoding='utf-8'))
            if journal.get('updateId') != activation['updateId']: raise CutError('UPDATE_HANDOFF', 'Update identity does not match.')
            config['activationHandoff'] = activation['token']
        installation = WindowsInstallation(location.root)
        def probe(instance, claim):
            candidates = allowed_premiere(location)
            if claim: candidates = [row for row in candidates if row['pid'] == claim.get('pid') and row['createTime'] == claim.get('createTime')]
            return candidates[0] if len(candidates) == 1 else None
        def activation_probe(version, bundle, receipt):
            return (not verified.recovery and config['appVersion'] == version and config['bundleId'] == bundle and
                    canonical_root(sys.executable) == verified.executable and
                    service.activation_heartbeat(version, bundle))
        installation.activation_probe = activation_probe
        service = (service_factory or CutService)(location.root, config, installation, probe)
        if verified.recovery: service.stop_all(service.epoch)
        service.start()
        output = inherited_pipe('output') if supervised else None
        def emit(value): output.write(json.dumps(value, separators=(',', ':'))+'\n'); output.flush()
        lifecycle = HeadlessLifecycle(location, service, mutex, config, emit_handoff=emit if output else None)
        stop = stop or threading.Event()
        while not stop.wait(.5):
            result = lifecycle.tick()
            if result is not None: return result
        return 0
    finally:
        if service and not service.resources_closed: service.close()
        mutex.close()
