import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from ctypes import wintypes
from pathlib import Path
from unittest.mock import patch

from contentrium_cut.jobs import JobManager
from contentrium_cut.contract import CutError
from contentrium_cut.process_scope import ProcessScope


def stubborn_worker(kind, payload, cancel, result, cache_root):
    if payload.get('ffmpeg'):
        if payload.get('blocked'):
            child = subprocess.Popen([payload['ffmpeg'], '-v', 'error', '-nostdin', '-f', 'lavfi',
                                      '-i', 'anullsrc=r=16000:cl=mono', '-f', 'f32le', 'pipe:1'],
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            # Keep an unread pipe reader in the controller. Without this duplicate,
            # killing the worker closes the last reader and FFmpeg dies from EOF,
            # producing a false-positive descendant regression even without a job.
            import msvcrt
            api = ctypes.WinDLL('kernel32', use_last_error=True)
            api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]; api.OpenProcess.restype = wintypes.HANDLE
            api.GetCurrentProcess.restype = wintypes.HANDLE
            api.DuplicateHandle.argtypes = [wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
                                            ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            api.CloseHandle.argtypes = [wintypes.HANDLE]
            parent = api.OpenProcess(0x0040, False, payload['controllerPid']); reader = wintypes.HANDLE()
            try:
                if not parent or not api.DuplicateHandle(api.GetCurrentProcess(), msvcrt.get_osfhandle(child.stdout.fileno()), parent,
                                                         ctypes.byref(reader), 0, False, 2):
                    raise ctypes.WinError(ctypes.get_last_error())
            finally:
                if parent: api.CloseHandle(parent)
            Path(payload['marker']).write_text(json.dumps({'pids': [child.pid], 'readerHandle': reader.value}))
        else:
            child = subprocess.Popen([payload['ffmpeg'], '-v', 'error', '-nostdin', '-re', '-f', 'lavfi',
                                      '-i', 'anullsrc=r=16000:cl=mono', '-f', 'null', '-'],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            marker = Path(payload['marker']); pending = marker.with_suffix('.pending')
            pending.write_text(json.dumps([child.pid])); pending.replace(marker)
    else:
        script = ('import subprocess,sys,json,time;from pathlib import Path;'
                  'child=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"]);'
                  'Path(sys.argv[1]).write_text(json.dumps([__import__("os").getpid(),child.pid]));'
                  'time.sleep(60)')
        child = subprocess.Popen([sys.executable, '-c', script, payload['marker']])
    time.sleep(60)  # Deliberately ignores cancellation like a blocked native call.


def marker_worker(kind, payload, cancel, result, cache_root):
    Path(payload['marker']).write_text('worker executed')


def crashing_controller(marker, worker_marker, crash):
    import contentrium_cut.jobs as module
    module._worker = stubborn_worker
    jobs = JobManager(str(Path(marker).parent / 'controller-data'))
    state = jobs.submit('sync', {'marker': marker})
    Path(worker_marker).write_text(str(state['workerPid']))
    crash.wait(15)
    os._exit(0)  # No finally/manager.close: only kernel kill-on-close can drain.


def breakaway_worker(kind, payload, cancel, result, cache_root):
    try:
        child = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(60)'],
                                 creationflags=0x01000000)
    except OSError as error:
        Path(payload['marker']).write_text(json.dumps({'blocked': True, 'winError': error.winerror}))
    else:
        # Always clean up an unexpected escaped test process by its own handle.
        child.terminate(); child.wait(5)
        Path(payload['marker']).write_text(json.dumps({'blocked': False}))


def result_then_stubborn_worker(kind, payload, cancel, result, cache_root):
    from test_cache import analysis_fixture
    child = subprocess.Popen([payload['ffmpeg'], '-v', 'error', '-nostdin', '-re', '-f', 'lavfi',
                              '-i', 'anullsrc=r=16000:cl=mono', '-f', 'null', '-'],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    Path(payload['marker']).write_text(json.dumps([child.pid]))
    result.put({'ok': True, 'value': analysis_fixture(), 'cacheKey': 'a' * 64})
    time.sleep(60)


class NativeProcess:
    """A pinned native identity, including cleanup after a failing regression."""
    def __init__(self, pid):
        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        self.api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.api.OpenProcess.restype = wintypes.HANDLE
        self.api.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        self.api.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = self.api.OpenProcess(0x100001 | 0x1000, False, pid)
        if not self.handle: raise ctypes.WinError(ctypes.get_last_error())

    def alive(self):
        code = wintypes.DWORD()
        if not self.api.GetExitCodeProcess(self.handle, ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
        return code.value == 259

    def close(self):
        if self.alive():
            self.api.TerminateProcess(self.handle, 1)
            self.api.WaitForSingleObject(self.handle, 5000)
        self.api.CloseHandle(self.handle)


@unittest.skipUnless(os.name == 'nt', 'Windows Job Object integration')
class ScopeTests(unittest.TestCase):
    def test_canceled_state_persistence_cannot_delay_native_termination(self):
        from contentrium_cut.jobs import atomic_json
        with tempfile.TemporaryDirectory() as directory:
            jobs=JobManager(directory);marker=Path(directory)/'child.json';children=[]
            staged=threading.Event();release_cache=threading.Event()
            saving=threading.Event();release_state=threading.Event()
            def stalled_write(path,value):
                if str(path).endswith('.pending.json'):
                    staged.set();release_cache.wait(10)
                elif value.get('status')=='canceled':
                    saving.set();release_state.wait(10)
                atomic_json(path,value)
            try:
                with patch('contentrium_cut.jobs.atomic_json',side_effect=stalled_write),patch('contentrium_cut.jobs._worker',result_then_stubborn_worker):
                    jobs.submit('analysis',{'marker':str(marker),'ffmpeg':shutil.which('ffmpeg')})
                    self.assertTrue(staged.wait(5))
                    children=[NativeProcess(pid) for pid in json.loads(marker.read_text())]
                    jobs.stop_all(1);release_cache.set();self.assertTrue(saving.wait(2))
                    deadline=time.monotonic()+4
                    while any(c.alive() for c in children) and time.monotonic()<deadline:time.sleep(.02)
                    self.assertFalse(any(c.alive() for c in children),'job-state persistence delayed owned FFmpeg termination')
                    release_state.set()
            finally:
                release_cache.set();release_state.set()
                try:jobs.close()
                finally:
                    for child in children:child.close()

    def test_cache_staging_cannot_delay_forced_descendant_shutdown(self):
        from contentrium_cut.jobs import atomic_json
        with tempfile.TemporaryDirectory() as directory:
            jobs = JobManager(directory); marker = Path(directory) / 'child.json'; children = []
            staged = threading.Event(); resume = threading.Event()
            def stalled_write(path, value):
                if str(path).endswith('.pending.json'): staged.set(); resume.wait(10)
                atomic_json(path, value)
            try:
                with patch('contentrium_cut.jobs.atomic_json', side_effect=stalled_write), patch('contentrium_cut.jobs._worker', result_then_stubborn_worker):
                    jobs.submit('analysis', {'marker': str(marker), 'ffmpeg': shutil.which('ffmpeg')})
                    self.assertTrue(staged.wait(5)); children = [NativeProcess(pid) for pid in json.loads(marker.read_text())]
                    jobs.stop_all(1); deadline = time.monotonic() + 4
                    while any(c.alive() for c in children) and time.monotonic() < deadline: time.sleep(.02)
                    self.assertFalse(any(c.alive() for c in children), 'cache staging delayed the cancellation grace limit')
                    self.assertFalse(jobs.quiescent(), 'pending completion must also drain before quiescence')
                    resume.set()
            finally:
                resume.set()
                try: jobs.close()
                finally:
                    for child in children: child.close()
            self.assertEqual(list((Path(directory) / 'cache').iterdir()), [])
    def run_descendants(self, ffmpeg=None, blocked=False):
        with tempfile.TemporaryDirectory() as directory:
            jobs = JobManager(directory); children = []; reader = None
            unrelated = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(60)'])
            try:
                marker = Path(directory) / 'owned-tree.json'
                with patch('contentrium_cut.jobs._worker', stubborn_worker):
                    job = jobs.submit('sync', {'marker': str(marker), 'ffmpeg': ffmpeg,
                                               'blocked': blocked, 'controllerPid': os.getpid()})
                deadline = time.monotonic() + 10
                while not marker.exists() and time.monotonic() < deadline: time.sleep(.02)
                self.assertTrue(marker.exists(), 'owned worker did not reach its decoder')
                data = json.loads(marker.read_text())
                if isinstance(data, dict): pids = data['pids']; reader = data['readerHandle']
                else: pids = data
                children = [NativeProcess(pid) for pid in pids]
                jobs.stop_all(1); deadline = time.monotonic() + 7
                while not jobs.quiescent() and time.monotonic() < deadline: time.sleep(.02)
                self.assertTrue(jobs.quiescent())
                self.assertEqual(jobs.get(job['jobId'])['status'], 'canceled')
                self.assertFalse(any(child.alive() for child in children), 'quiescence left owned descendants alive')
                self.assertIsNone(unrelated.poll(), 'owned scope killed an unrelated process')
            finally:
                try: jobs.close()
                finally:
                    for child in children: child.close()
                    if reader:
                        api = ctypes.WinDLL('kernel32'); api.CloseHandle.argtypes = [wintypes.HANDLE]; api.CloseHandle(reader)
                    if unrelated.poll() is None: unrelated.terminate()
                    unrelated.wait(5)

    def test_forced_cancel_drains_real_child_and_grandchild(self):
        self.run_descendants()

    def test_forced_cancel_drains_actual_ffmpeg_with_stubborn_worker(self):
        executable = os.environ.get('CONTENTRIUM_FFMPEG') or shutil.which('ffmpeg')
        self.assertTrue(executable, 'actual FFmpeg regression requires configured decoder')
        self.run_descendants(executable)

    def test_forced_cancel_drains_actual_ffmpeg_blocked_on_retained_pipe(self):
        executable = os.environ.get('CONTENTRIUM_FFMPEG') or shutil.which('ffmpeg')
        self.assertTrue(executable, 'actual FFmpeg regression requires configured decoder')
        self.run_descendants(executable, blocked=True)

    def test_assignment_start_barrier_prevents_work_until_parent_attaches_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'execution.txt'; jobs = JobManager(directory)
            assigned = threading.Event(); release = threading.Event(); outcomes = []
            original = ProcessScope.assign
            def delayed(scope, process):
                assigned.set(); release.wait(5); original(scope, process)
            def submit():
                try: outcomes.append(jobs.submit('sync', {'marker': str(marker)}))
                except Exception as error: outcomes.append(error)
            try:
                with patch('contentrium_cut.jobs._worker', marker_worker), patch.object(ProcessScope, 'assign', delayed):
                    thread = threading.Thread(target=submit); thread.start()
                    self.assertTrue(assigned.wait(3)); time.sleep(.5)
                    self.assertFalse(marker.exists(), 'worker ran before scope assignment')
                    release.set(); thread.join(5)
                self.assertEqual(len(outcomes), 1); self.assertIsInstance(outcomes[0], dict)
                deadline = time.monotonic() + 5
                while not marker.exists() and time.monotonic() < deadline: time.sleep(.02)
                self.assertTrue(marker.exists())
            finally:
                release.set(); jobs.close()

    def test_assignment_failure_never_releases_worker_or_leaks_admission_state(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'execution.txt'; jobs = JobManager(directory)
            try:
                with patch('contentrium_cut.jobs._worker', marker_worker), patch.object(ProcessScope, 'assign', side_effect=CutError('PROCESS_SCOPE_FAILED', 'fixture')):
                    with self.assertRaises(CutError): jobs.submit('sync', {'marker': str(marker)})
                self.assertFalse(marker.exists()); self.assertTrue(jobs.quiescent()); self.assertEqual(jobs.list(), [])
            finally: jobs.close()

    def test_kernel_kill_on_close_drains_tree_after_controller_crash(self):
        import multiprocessing as mp
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'tree.json'; worker_marker = Path(directory) / 'worker.txt'
            context = mp.get_context('spawn'); crash = context.Event(); children = []
            controller = context.Process(target=crashing_controller, args=(str(marker), str(worker_marker), crash))
            controller.start()
            try:
                deadline = time.monotonic() + 10
                while (not marker.exists() or not worker_marker.exists()) and time.monotonic() < deadline: time.sleep(.02)
                self.assertTrue(marker.exists()); self.assertTrue(worker_marker.exists())
                children = [NativeProcess(pid) for pid in [int(worker_marker.read_text())] + json.loads(marker.read_text())]
                crash.set(); controller.join(5); self.assertFalse(controller.is_alive())
                deadline = time.monotonic() + 5
                while any(child.alive() for child in children) and time.monotonic() < deadline: time.sleep(.02)
                self.assertFalse(any(child.alive() for child in children))
            finally:
                crash.set()
                if controller.is_alive(): controller.terminate(); controller.join(5)
                controller.close()
                for child in children: child.close()

    def test_native_child_cannot_break_away_from_owned_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'breakaway.json'; jobs = JobManager(directory)
            try:
                with patch('contentrium_cut.jobs._worker', breakaway_worker): jobs.submit('sync', {'marker': str(marker)})
                deadline = time.monotonic() + 5
                while not marker.exists() and time.monotonic() < deadline: time.sleep(.02)
                self.assertTrue(marker.exists()); self.assertTrue(json.loads(marker.read_text())['blocked'])
            finally: jobs.close()
