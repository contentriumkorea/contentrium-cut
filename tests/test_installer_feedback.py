"""Installer bytes, startup reports and child boundary without native installation."""
import importlib
import io
import json
import os
from pathlib import Path
import tempfile
import queue
import threading
import unittest
from unittest.mock import patch
from contentrium_cut.contract import CutError
from contentrium_cut.updater import GitHubTransport


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)

    def reporting(self):
        try:return importlib.import_module('contentrium_cut.installer_reporting')
        except ModuleNotFoundError:self.fail('Structured installer feedback is missing')

    def test_real_transport_emits_actual_bytes_and_completion(self):
        class Response(io.BytesIO):code=200
        transport=GitHubTransport();points=[];target=self.root/'payload'
        with patch.object(transport,'_open',return_value=Response(b'x'*(1024*1024+19))):
            try:transport.download('https://github.com/contentriumkorea/contentrium-cut/releases/download/v0.1.109/a',target,{},lambda:None,1024*1024+19,progress=lambda a,b:points.append((a,b)))
            except TypeError:self.fail('Streaming transport does not report actual download bytes')
        self.assertEqual(points,[(0,1048595),(1048576,1048595),(1048595,1048595)])
        self.assertEqual(target.stat().st_size,1048595)

    def test_failed_child_retains_error_code_and_action(self):
        module=self.reporting();path=self.root/'result.json'
        module.write_report(path,module.failure(CutError('INSTALL_REGISTRATION','Existing installation is invalid.'),'location',version='0.1.109'))
        report=module.read_outcome(path,1,expected_version='0.1.109')
        self.assertEqual(report['status'],'FAILED');self.assertEqual(report['error']['code'],'INSTALL_REGISTRATION')
        self.assertTrue(report['error']['hint'])

    def test_missing_or_malformed_child_result_never_implies_install_success(self):
        module=self.reporting();path=self.root/'result.json'
        for data in [None,b'{',b'{"status":"READY_TO_OPEN","appVersion":"other"}']:
            if data is not None:path.write_bytes(data)
            report=module.read_outcome(path,0,expected_version='0.1.109')
            self.assertEqual(report['status'],'FAILED');self.assertEqual(report['error']['code'],'SETUP_RESULT_MISSING')

    def test_import_failure_report_excludes_sensitive_exception_text(self):
        module=self.reporting();report=module.failure(RuntimeError('hf_private_secret C:/media/private.wav'),'startup',version='0.1.109')
        path=module.save_diagnostic(report,directory=self.root)
        raw=path.read_text(encoding='utf-8')
        self.assertNotIn('hf_private_secret',raw);self.assertNotIn('private.wav',raw)
        self.assertIn('RuntimeError',raw)

    def test_independent_child_environment_does_not_inherit_frozen_tcl_python_paths(self):
        module=self.reporting()
        env=module.child_environment({'PATH':'safe','TCL_LIBRARY':'old/_MEI/tcl','TK_LIBRARY':'old/_MEI/tk','PYTHONPATH':'old','PYTHONHOME':'old','_PYI_APPLICATION_HOME_DIR':'old'})
        self.assertEqual(env['PATH'],'safe');self.assertEqual(env['PYINSTALLER_RESET_ENVIRONMENT'],'1')
        for name in ['TCL_LIBRARY','TK_LIBRARY','PYTHONPATH','PYTHONHOME','_PYI_APPLICATION_HOME_DIR']:self.assertNotIn(name,env)

    def test_progress_meter_reports_real_percentage_and_resets_between_assets(self):
        module=self.reporting();meter=module.ProgressMeter(clock=lambda:1)
        first=meter.update('engine',0,100,now=1);second=meter.update('engine',50,100,now=3)
        self.assertEqual(first['percent'],0);self.assertEqual(second['percent'],50)
        self.assertEqual(second['bytesPerSecond'],25);self.assertEqual(second['remainingSeconds'],2)
        reset=meter.update('ffmpeg',10,None,now=4);self.assertIsNone(reset['percent']);self.assertIsNone(reset['remainingSeconds'])

    def test_readonly_setup_diagnosis_never_enters_install_or_gui(self):
        entry=importlib.import_module('tools.install_entry');config=self.root/'config.json';config.write_text('{"appVersion":"0.1.109"}')
        result=self.root/'diagnose.json'
        with patch.object(entry,'install',side_effect=AssertionError('No installation')),patch.object(entry,'show_gui',side_effect=AssertionError('No GUI')):
            try:code=entry.main(['--diagnose','--config',str(config),'--root',str(self.root/'new-install'),'--result',str(result)])
            except SystemExit:self.fail('Read-only startup diagnosis is not supported')
        self.assertEqual(code,0);report=json.loads(result.read_text(encoding='utf-8'))
        self.assertEqual(report['status'],'DIAGNOSED');self.assertFalse(report['installationChanged']);self.assertFalse((self.root/'new-install').exists())

    def test_entry_captures_import_failure_before_install_main(self):
        try:entry=importlib.import_module('tools.setup_entry')
        except ModuleNotFoundError:self.fail('Setup startup wrapper is missing')
        result=self.root/'startup.json'
        with patch.object(entry.importlib,'import_module',side_effect=ImportError('private path')):
            code=entry.main(['--diagnose','--tag','v0.1.109','--result',str(result)])
        self.assertEqual(code,1);report=json.loads(result.read_text(encoding='utf-8'))
        self.assertEqual(report['error']['exceptionType'],'ImportError');self.assertNotIn('private path',result.read_text(encoding='utf-8'))

    def test_unwritable_optional_log_does_not_hide_startup_result(self):
        entry=importlib.import_module('tools.setup_entry');result=self.root/'startup.json'
        with patch.object(entry.importlib,'import_module',side_effect=ImportError('private')),patch.object(entry,'save_diagnostic',side_effect=PermissionError('log unavailable')):
            code=entry.main(['--diagnose','--tag','v0.1.109','--result',str(result)])
        self.assertEqual(code,1);self.assertTrue(result.is_file(),'Parent outcome must survive an optional logging failure')

    def test_gui_opens_before_potentially_slow_registered_root_verification(self):
        entry=importlib.import_module('tools.install_entry');config=self.root/'config.json'
        config.write_text('{"appVersion":"0.1.109"}',encoding='utf-8')
        with patch.object(entry,'resolve_setup_root',side_effect=CutError('INSTALL_REGISTRATION','fixture')) as resolve,patch.object(entry,'show_gui',return_value={'status':'CANCELED'}) as gui,patch('contentrium_cut.installer_ui.InstallerWindow'):
            code=entry.main(['--config',str(config)])
        resolve.assert_not_called();self.assertEqual(code,0);self.assertIsNone(gui.call_args.args[1])

    def test_root_worker_preserves_failure_and_cancel_without_installing(self):
        entry=importlib.import_module('tools.install_entry');worker=getattr(entry,'run_root_worker',None)
        self.assertIsNotNone(worker,'Root verification must run outside the UI thread')
        events=queue.Queue();cancel=threading.Event();cancel.set()
        with patch.object(entry,'resolve_setup_root',return_value=self.root),patch.object(entry,'install',side_effect=AssertionError('No install')):
            worker({'appVersion':'0.1.109'},events,cancel)
        kind,report=events.get_nowait();self.assertEqual((kind,report['status']),('error','CANCELED'))
        with patch.object(entry,'resolve_setup_root',side_effect=CutError('INSTALL_REGISTRATION','bad registration')):
            worker({'appVersion':'0.1.109'},events,threading.Event())
        kind,report=events.get_nowait();self.assertEqual(report['phase'],'location');self.assertEqual(report['error']['code'],'INSTALL_REGISTRATION')

    def test_same_asset_retry_resets_speed_baseline(self):
        meter=self.reporting().ProgressMeter()
        meter.update('engine',0,100,now=1);meter.update('engine',50,100,now=3)
        reset=meter.update('engine',0,100,now=9)
        resumed=meter.update('engine',20,100,now=11)
        self.assertEqual(reset['bytesPerSecond'],0);self.assertEqual(resumed['bytesPerSecond'],10)

    def test_canceled_panel_wait_reports_cancel_and_preserves_pending_result(self):
        entry=importlib.import_module('tools.install_entry');events=queue.Queue()
        pending={'status':'PENDING_PROVISIONING','canceled':True,'appVersion':'0.1.109'}
        with patch.object(entry,'install',return_value=pending):entry.run_setup_worker({'appVersion':'0.1.109'},self.root,events,threading.Event())
        kind,report=events.get_nowait()
        self.assertEqual(kind,'error');self.assertEqual(report['status'],'CANCELED');self.assertTrue(pending['canceled'])

    def test_non_object_child_result_is_rejected(self):
        module=self.reporting();path=self.root/'result.json'
        for value in [[],None,{'schemaVersion':True,'appVersion':'0.1.109','status':'READY_TO_OPEN'}]:
            path.write_text(json.dumps(value),encoding='utf-8')
            self.assertEqual(module.read_outcome(path,0,expected_version='0.1.109')['status'],'FAILED')

    def test_ffmpeg_provider_reports_actual_bytes(self):
        from contentrium_cut.dependencies import ProviderTransport,FFMPEG_URL
        class Response(io.BytesIO):
            status=200;headers={'Content-Length':'5'}
            def geturl(self):return FFMPEG_URL
        class Opener:
            def open(self,*a,**kw):return Response(b'bytes')
        points=[]
        with patch('contentrium_cut.dependencies.build_opener',return_value=Opener()):
            try:ProviderTransport().download(self.root/'provider',progress=lambda a,b:points.append((a,b)),cancel=lambda:False)
            except TypeError:self.fail('Provider download progress/cancel is missing')
        self.assertEqual(points,[(0,5),(5,5)])


if __name__=='__main__':unittest.main()
