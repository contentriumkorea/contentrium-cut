"""An unnamed Windows job owns one worker and every native descendant.

The controller retains the only job handle. Workers must wait at a start barrier
until assignment succeeds; no breakaway limits are enabled. A process PID is
never reopened for assignment: multiprocessing's pinned process handle is used.
"""
import ctypes
import os
import threading
from ctypes import wintypes

from .contract import CutError


class _BasicLimits(ctypes.Structure):
    _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64),
                ('PerJobUserTimeLimit', ctypes.c_int64), ('LimitFlags', wintypes.DWORD),
                ('MinimumWorkingSetSize', ctypes.c_size_t), ('MaximumWorkingSetSize', ctypes.c_size_t),
                ('ActiveProcessLimit', wintypes.DWORD), ('Affinity', ctypes.c_size_t),
                ('PriorityClass', wintypes.DWORD), ('SchedulingClass', wintypes.DWORD)]


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in (
        'ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount',
        'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [('BasicLimitInformation', _BasicLimits), ('IoInfo', _IoCounters),
                ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t),
                ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]


class _Accounting(ctypes.Structure):
    _fields_ = [(name, ctypes.c_int64) for name in (
        'TotalUserTime', 'TotalKernelTime', 'ThisPeriodTotalUserTime', 'ThisPeriodTotalKernelTime')]
    _fields_ += [(name, wintypes.DWORD) for name in (
        'TotalPageFaultCount', 'TotalProcesses', 'ActiveProcesses', 'TotalTerminatedProcesses')]


class ProcessScope:
    def __init__(self):
        self.lifetime = threading.RLock()
        if os.name != 'nt':
            raise CutError('PROCESS_SCOPE_UNAVAILABLE', 'Owned worker scopes require Windows.')
        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        self.api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.api.CreateJobObjectW.restype = wintypes.HANDLE
        self.api.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.api.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
        self.api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.api.GetProcessId.argtypes = [wintypes.HANDLE]
        self.api.GetProcessId.restype = wintypes.DWORD
        self.api.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle: self._error()
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE; no breakaway.
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.get_last_error(); self.close(); self._error(error)

    def _error(self, error=None):
        raise CutError('PROCESS_SCOPE_FAILED', 'Unable to establish or drain the owned worker scope.',
                       {'winError': ctypes.get_last_error() if error is None else error})

    def assign(self, process):
        if not self.handle or self.api.GetProcessId(process.sentinel) != process.pid:
            self._error()
        if not self.api.AssignProcessToJobObject(self.handle, process.sentinel): self._error()

    def active_count(self):
        if not self.handle:
            raise CutError('PROCESS_SCOPE_CLOSED', 'A closed scope cannot prove quiescence.')
        info = _Accounting()
        if not self.api.QueryInformationJobObject(self.handle, 1, ctypes.byref(info), ctypes.sizeof(info), None):
            self._error()
        return info.ActiveProcesses

    def terminate(self):
        with self.lifetime:
            if not self.handle or not self.api.TerminateJobObject(self.handle, 1): self._error()

    def terminate_if_owned(self):
        # The scope object pins this one unnamed Job Object. Closing and timer
        # termination share only this native-handle lock, never persistence I/O.
        with self.lifetime:
            if not self.handle:return False
            if not self.api.TerminateJobObject(self.handle, 1):self._error()
            return True

    def close(self):
        with self.lifetime:
            if self.handle:
                if not self.api.CloseHandle(self.handle): self._error()
                self.handle = None
