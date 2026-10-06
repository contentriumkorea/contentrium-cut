import importlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from contentrium_cut.bootstrap import InstallLocation
from contentrium_cut.contract import CutError


class LifecycleTests(unittest.TestCase):
    def test_probe_failure_returns_machine_readable_error_without_an_uncaught_gui_exception(self):
        from contentrium_cut import launcher
        with tempfile.TemporaryDirectory() as directory:
            report=Path(directory)/'probe.json'
            with patch.object(launcher,'read_config',side_effect=RuntimeError('sensitive internal path')):
                status=launcher.main(['--probe','--probe-output',str(report)])
            self.assertEqual(status,1)
            self.assertEqual(json.loads(report.read_text()),{'runtimeReady':False,'errorCode':'RUNTIME_PROBE_FAILED','errorType':'RuntimeError'})

    def setUp(self):
        self.module = importlib.import_module('contentrium_cut.lifecycle')
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.location = InstallLocation(self.root, 'installation-test', 'key-test', 'S-1-5-21-123')
        directory = self.root / 'app/versions/0.1.0'; directory.mkdir(parents=True)
        (directory / 'Contentrium CUT.exe').write_bytes(b'verified fixture')
        (directory / 'config.json').write_text(json.dumps(dict(appVersion='0.1.0', bundleId='fixture')))
        (self.root / 'app/active.json').write_text(json.dumps(dict(schemaVersion=1, productId='com.contentrium.cut', appVersion='0.1.0', bundleId='fixture', versionDirectory='versions/0.1.0')))
        self.installation = type('Installed', (), {'verify_application':lambda _,v,b:True, 'verify_code':lambda _,v,b:True})()

    def test_launch_pins_root_config_and_only_verified_code(self):
        verified = self.module.verify_runtime(self.location, self.installation)
        calls=[]
        self.module.spawn_verified(self.location, verified, supervisor=True, popen=lambda args, **kw:calls.append((args,kw)))
        args, kwargs = calls[0]
        self.assertEqual(args[1:3], ['--supervisor','--root'])
        self.assertEqual(args[3],str(self.root))
        self.assertEqual(args[4:6],['--config',str(verified.config_path)])
        self.assertEqual(kwargs['stdin'], subprocess.DEVNULL)
        self.installation.verify_application=lambda *a:False
        with self.assertRaises(CutError): self.module.verify_runtime(self.location,self.installation)
        with self.assertRaises(CutError): self.module.verify_runtime(self.location,self.installation,recovery=True)
        (self.root/'updates').mkdir();(self.root/'updates/journal.json').write_text('{"updateState":"RECOVERY_REQUIRED"}')
        self.assertTrue(self.module.verify_runtime(self.location,self.installation,recovery=True).recovery)
        self.installation.verify_code=lambda *a:False
        with self.assertRaises(CutError):self.module.verify_runtime(self.location,self.installation,recovery=True)

    def test_supervisor_backoff_cap_distinct_lease_and_no_unowned_kill(self):
        now=[100]; launches=[]; state=[]
        class Child:
            pid=123;returncode=None
            def poll(self): return self.returncode
        def launch(*args,**kwargs):
            child=Child();launches.append(child);return child
        supervisor=self.module.Supervisor(self.location, installation=self.installation, premiere_probe=lambda:True,
            spawn=launch, clock=lambda:now[0], write_state=lambda value:state.append(value), child_identity=lambda child:{'pid':child.pid,'createdAtTicks':'1'})
        self.assertNotEqual(supervisor.lease.name, self.module.WindowsNamedMutex(self.root).name)
        supervisor.step();self.assertEqual(len(launches),1)
        for index,delay in enumerate([1,2,4,8,16]):
            launches[-1].returncode=1;supervisor.step()
            self.assertEqual(state[-1]['retryAt'],now[0]+delay)
            supervisor.step();self.assertEqual(len(launches),index+1)
            now[0]+=delay;supervisor.step();self.assertEqual(len(launches),index+2)
        launches[-1].returncode=1;supervisor.step();self.assertEqual(state[-1]['errorCode'],'RUNTIME_CRASH_LIMIT')
        now[0]+=30;supervisor.step();self.assertEqual(len(launches),6)
        self.assertFalse(hasattr(launches[0],'terminate'))

    def test_handoff_exit_never_restarts_without_one_use_ticket(self):
        class Child:
            pid=123
            def poll(self):return 75
        states=[]
        supervisor=self.module.Supervisor(self.location, installation=self.installation,premiere_probe=lambda:True,
            spawn=lambda *a,**kw:Child(), write_state=states.append,child_identity=lambda child:{'pid':123,'createdAtTicks':'1'})
        supervisor.step();supervisor.step();supervisor.step()
        self.assertEqual(states[-1]['errorCode'],'HANDOFF_REQUIRED')
        self.assertIsNone(supervisor.child)

    def test_normal_launcher_imports_no_gui_or_model_runtime(self):
        result=subprocess.run([sys.executable,'-c',"import sys; import contentrium_cut.launcher; print(int(any(m in sys.modules for m in ('tkinter','torch','pyannote.audio','contentrium_cut.models','contentrium_cut.service'))))"],capture_output=True,text=True,check=True)
        self.assertEqual(result.stdout.strip(),'0')

    def test_headless_keeps_updater_after_host_exit_and_orders_ticket_after_drain(self):
        events=[]
        class Service:
            applies={};resources_closed=False;maintenance=False
            jobs=type('Jobs',(),{'quiescent':lambda _:True})()
            validations=type('Validations',(),{'quiescent':lambda _:True})()
            updater=type('Updater',(),{'create_activation_handoff':lambda _:events.append('ticket') or {'token':'a'*32,'updateId':'b'*32}})()
            update={'updateState':'DOWNLOADING','newVersion':'0.2.0'}
            def update_state(self):return self.update
            def _host_exited(self):return True
            def close(self):events.append('close')
            def quiesce_for_handoff(self):events.append('drain')
        mutex=type('Mutex',(),{'close':lambda _:events.append('release')})()
        service=Service();lifecycle=self.module.HeadlessLifecycle(self.location,service,mutex,{'appVersion':'0.1.0'},emit_handoff=lambda value:events.append('emit'))
        self.assertIsNone(lifecycle.tick());self.assertFalse(events)
        service.update['updateState']='RECOVERY_REQUIRED';self.assertIsNone(lifecycle.tick())
        service.update['updateState']='PENDING_ACTIVATION';self.assertEqual(lifecycle.tick(),75)
        self.assertEqual(events,['drain','ticket','release','emit'])

    def test_pending_activation_waits_without_starting_handoff_deadline_while_metadata_drains(self):
        events=[];alive=[True]
        class Service:
            update_check=type('Check',(),{'is_alive':lambda _:alive[0]})()
            updater=type('Updater',(),{'create_activation_handoff':lambda _:events.append('ticket') or {'token':'a'*32,'updateId':'b'*32}})()
            def update_state(self):return {'updateState':'PENDING_ACTIVATION','newVersion':'0.1.1'}
            def quiesce_for_handoff(self):
                if alive[0]:raise CutError('HANDOFF_DRAIN_FAILED','metadata is still running')
                events.append('drain')
        mutex=type('Mutex',(),{'close':lambda _:events.append('release')})()
        service=Service();lifecycle=self.module.HeadlessLifecycle(self.location,service,mutex,{'appVersion':'0.1.0'},emit_handoff=lambda value:events.append('emit'))
        for _ in range(30):self.assertIsNone(lifecycle.tick())
        self.assertEqual(events,[]);alive[0]=False
        self.assertEqual(lifecycle.tick(),75);self.assertEqual(events,['drain','ticket','release','emit'])

    def test_activation_ticket_only_uses_inherited_pipe_not_args_or_state(self):
        import io
        class Pipe(io.BytesIO):
            def close(self):self.saved=self.getvalue()
        class Child:stdin=Pipe()
        calls=[];verified=self.module.verify_runtime(self.location,self.installation)
        token='private-one-use-ticket-12345';packet={'token':token,'updateId':'update-123'}
        child=self.module.spawn_verified(self.location,verified,activation=packet,popen=lambda args,**kw:calls.append((args,kw)) or Child())
        self.assertNotIn(token,str(calls));self.assertEqual(json.loads(child.stdin.saved),packet)

    def test_disappearing_snapshot_row_during_session_or_sid_query_is_skipped(self):
        rows = [{'pid':17,'imageName':'Adobe Premiere Pro.exe'}, {'pid':23,'imageName':'Adobe Premiere Pro.exe'}]
        identity = dict(pid=23, imagePath=str(self.root/'Adobe Premiere Pro.exe'), createdAtTicks='456')
        for lookup in ['session', 'sid']:
            with self.subTest(lookup=lookup):
                def session(pid):
                    if pid == 17 and lookup == 'session': raise CutError('HOST_IDENTITY_REQUIRED', 'Fixture exited.')
                    return 2
                def sid(pid):
                    if pid == 17 and lookup == 'sid': raise CutError('HOST_IDENTITY_REQUIRED', 'Fixture exited.')
                    return self.location.owner_sid
                with patch.object(self.module, 'windows_process_snapshot', return_value=rows), \
                        patch.object(self.module, '_session_id', side_effect=session), \
                        patch.object(self.module, '_process_sid', side_effect=sid), \
                        patch.object(self.module, 'process_identity', side_effect=lambda pid:None if pid == 17 else identity):
                    self.assertEqual([row['pid'] for row in self.module.allowed_premiere(self.location)], [23])

    def test_same_supervisor_survives_snapshot_exit_race_and_later_spawns_once(self):
        now=[100]; launches=[]; states=[]
        class Child:
            pid=123
            def poll(self):return None
        class Lease:
            def __enter__(self):return self
            def __exit__(self,*args):pass
        class Stop:
            ticks=0
            def wait(self, delay):
                now[0]=100+self.ticks; self.ticks+=1
                return self.ticks>4
        snapshots = [[{'pid':17,'imageName':'Adobe Premiere Pro.exe'}],
                     [{'pid':23,'imageName':'Adobe Premiere Pro.exe'}]]
        def session(pid):
            if pid==17:raise CutError('HOST_IDENTITY_REQUIRED', 'Fixture exited.')
            return 2
        identity = dict(pid=23, imagePath=str(self.root/'Adobe Premiere Pro.exe'), createdAtTicks='456')
        supervisor=self.module.Supervisor(self.location, installation=self.installation, lease=Lease(),
            clock=lambda:now[0], spawn=lambda *a,**kw:launches.append(Child()) or launches[-1],
            write_state=states.append, child_identity=lambda child:{'pid':child.pid,'createdAtTicks':'1'})
        supervisor.failures.append(99); supervisor.consecutive=1
        with patch.object(self.module,'windows_process_snapshot',side_effect=snapshots), \
                patch.object(self.module,'_session_id',side_effect=session), \
                patch.object(self.module,'_process_sid',return_value=self.location.owner_sid), \
                patch.object(self.module,'process_identity',side_effect=lambda pid:None if pid==17 else identity):
            supervisor.run(Stop())
        self.assertFalse(supervisor.blocked)
        self.assertEqual(len(launches),1)
        self.assertEqual(list(supervisor.failures),[99]); self.assertEqual(supervisor.consecutive,1)
        self.assertEqual(states[-1]['state'],'RUNNING')

    def test_uncertain_identity_retries_with_backoff_and_preserves_crash_budget(self):
        now=[100]; observed=[]; launches=[]; states=[]
        class Child:
            pid=123
            def poll(self):return None
        class Lease:
            def __enter__(self):return self
            def __exit__(self,*args):pass
        class Stop:
            ticks=iter([100,100,101,102,103,104,107,108])
            def wait(self,delay):
                try:now[0]=next(self.ticks); return False
                except StopIteration:return True
        def probe():
            observed.append(now[0])
            if len(observed)<=3:raise CutError('HOST_IDENTITY_REQUIRED','Fixture access unavailable.')
            return True
        supervisor=self.module.Supervisor(self.location, installation=self.installation, lease=Lease(),
            premiere_probe=probe, clock=lambda:now[0], spawn=lambda *a,**kw:launches.append(Child()) or launches[-1],
            write_state=states.append, child_identity=lambda child:{'pid':child.pid,'createdAtTicks':'1'})
        supervisor.failures.extend([98,99]);supervisor.consecutive=2
        supervisor.run(Stop())
        self.assertEqual(observed,[100,101,103,107])
        self.assertFalse(supervisor.blocked);self.assertEqual(len(launches),1)
        self.assertTrue(any(state['state']=='RECOVERY_REQUIRED' and state['errorCode']=='HOST_IDENTITY_REQUIRED' for state in states))
        self.assertEqual(list(supervisor.failures),[98,99]);self.assertEqual(supervisor.consecutive,2)

    def test_native_activation_predicate_uses_authenticated_heartbeat_authority(self):
        verified=self.module.verify_runtime(self.location,self.installation)
        calls=[]
        class Mutex:
            def __enter__(self):return self
            def close(self):pass
        class Service:
            resources_closed=False
            # Matching body claims must not override the shared auth predicate.
            panels={'spoof':dict(appVersion='0.1.0',bundleId='fixture',host={'pid':17},heartbeatAt=0)}
            def activation_heartbeat(self,version,bundle):calls.append((version,bundle));return False
            def start(self):pass
            def close(self):self.resources_closed=True
        stopped=self.module.threading.Event();stopped.set()
        with patch.object(self.module,'WindowsNamedMutex',return_value=Mutex()), \
                patch.object(self.module,'WindowsInstallation',return_value=self.installation), \
                patch.object(self.module,'load_bootstrap',return_value={}), \
                patch.object(self.module.sys,'frozen',True,create=True), \
                patch.object(self.module.sys,'executable',str(verified.executable)), \
                patch.object(self.module.time,'monotonic',return_value=0):
            self.module.run_headless(self.location,verified,service_factory=lambda *args:Service(),stop=stopped)
            self.assertFalse(self.installation.activation_probe('0.1.0','fixture',{}))
        self.assertEqual(calls,[('0.1.0','fixture')])

    def test_persistent_host_identity_fault_has_capped_retry_and_never_admits_runtime(self):
        now=[100];states=[];launches=[]
        def probe():raise CutError('PROCESS_IDENTITY','Fixture access denied.')
        supervisor=self.module.Supervisor(self.location,installation=self.installation,premiere_probe=probe,
            clock=lambda:now[0],spawn=lambda *a,**kw:launches.append(a),write_state=states.append)
        for observed in [100,101,103,107,115,131,161,191]:
            now[0]=observed;supervisor.step()
        self.assertFalse(supervisor.blocked);self.assertFalse(launches)
        self.assertEqual(states[-1]['state'],'RECOVERY_REQUIRED')
        self.assertEqual(states[-1]['errorCode'],'PROCESS_IDENTITY')
        self.assertEqual(states[-1]['retryAt'],221)
        self.assertEqual(states[-1]['failureCount'],0)



if __name__=='__main__':unittest.main()
