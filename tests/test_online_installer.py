"""Real signed release fixtures; network and child process are the only doubles."""
import base64
import hashlib
import importlib
import json
import queue
from pathlib import Path
import threading
import unittest
from unittest.mock import patch
import test_updater
from contentrium_cut.contract import CutError


class OnlineInstallerTests(unittest.TestCase):
    def setUp(self):
        self.fixture=test_updater.UpdaterTests('runTest'); self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        try: self.module=importlib.import_module('contentrium_cut.online_installer')
        except ModuleNotFoundError: self.fail('Small online installer is not implemented')
        self.config={'publicKey':base64.b64encode(self.fixture.public).decode(),'signingKeyId':'contentrium-cut-2026-01'}
        self.add_installer()
        self.client=self.module.OnlineInstaller(self.config,self.fixture.root/'online',transport=self.fixture.network)
        self.target=self.fixture.root/'download.exe'

    def add_installer(self):
        f=self.fixture
        # Update the real signed fixture with an installer asset.
        release=f.network.release
        manifest_url=next(a['browser_download_url'] for a in release['assets'] if a['name']=='update-manifest.json')
        signature_url=next(a['browser_download_url'] for a in release['assets'] if a['name']=='update-manifest.sig')
        manifest=json.loads(f.network.documents[manifest_url])
        payload=b'MZ signed setup fixture';url='https://github.com/contentriumkorea/contentrium-cut/releases/download/v0.2.0/Setup.exe'
        asset=dict(role='installer',assetId=777,name='Setup.exe',size=len(payload),sha256=hashlib.sha256(payload).hexdigest())
        manifest['assets'].append(asset)
        release['assets'].append(dict(id=777,name='Setup.exe',size=len(payload),state='uploaded',browser_download_url=url))
        raw=json.dumps(manifest).encode();sig=json.dumps(dict(algorithm='Ed25519',keyId='contentrium-cut-2026-01',signature=base64.b64encode(f.key.sign(raw)).decode())).encode()
        f.network.documents[manifest_url]=raw
        f.network.documents[signature_url]=sig
        for a in release['assets']:
            if a['name']=='update-manifest.json':a['size']=len(raw)
            if a['name']=='update-manifest.sig':a['size']=len(sig)
        f.network.release_by_id[release['id']]=json.loads(json.dumps(release))
        self.payload_url=url;self.payload=payload;self.release=release;self.signature_url=signature_url
        f.network.documents[url]=payload

    def test_latest_signed_setup_download_and_exact_tag_command(self):
        selected=self.client.check()
        self.assertEqual(selected['manifest']['appVersion'],'0.2.0')
        self.client.download(selected,self.target)
        self.assertEqual(self.target.read_bytes(),self.payload)
        with patch.object(self.module.subprocess,'run') as child:
            child.return_value.returncode=0
            self.client.launch(selected,self.target)
        self.assertEqual(child.call_args.args[0],[str(self.target),'--tag','v0.2.0'])
        self.assertFalse(child.call_args.kwargs.get('shell',False))

    def test_readonly_online_diagnosis_uses_normal_root_unless_explicitly_isolated(self):
        import tempfile,sys
        from contentrium_cut.installer_reporting import write_report
        entry=importlib.import_module('tools.online_install_entry')
        with tempfile.TemporaryDirectory() as directory:
            resource=Path(directory);(resource/'online-config.json').write_text(json.dumps(self.config),encoding='utf-8')
            def launch(selected,path,**kwargs):
                write_report(kwargs['result_path'],dict(schemaVersion=1,status='DIAGNOSED',appVersion='0.2.0',installationChanged=False))
                return 0
            with patch.object(sys,'_MEIPASS',str(resource),create=True),patch.object(entry,'OnlineInstaller',return_value=self.client),patch.object(self.client,'launch',side_effect=launch) as child:
                code=entry.main(['--diagnose-setup','--result',str(resource/'proof.json')])
            self.assertEqual(code,0);self.assertIsNone(child.call_args.kwargs['diagnostic_root'])

    def test_cancel_during_final_confirmation_has_no_premature_handoff_or_child(self):
        entry=importlib.import_module('tools.online_install_entry');events=queue.Queue();cancel=threading.Event()
        confirm=self.client.confirm;count=[0];premature=[]
        def checked(selected):
            count[0]+=1
            if count[0]==3:
                premature.extend(kind for kind,_ in list(events.queue) if kind=='handoff');cancel.set()
            return confirm(selected)
        with patch.object(self.client,'confirm',side_effect=checked),patch.object(self.module.subprocess,'run') as child:
            entry.run_online_worker(self.config,events,cancel,client_factory=lambda *a:self.client)
        self.assertEqual(count[0],3);self.assertEqual(premature,[]);child.assert_not_called()
        reports=[value['report'] for kind,value in list(events.queue) if kind=='failed']
        self.assertEqual(reports[0]['status'],'CANCELED')

    def test_tampered_signature_cannot_select(self):
        self.fixture.network.documents[self.signature_url]=b'{}'
        with self.assertRaises(Exception):self.client.check()
        self.assertFalse(self.target.exists())

    def test_payload_hash_mismatch_leaves_no_executable(self):
        selected=self.client.check();self.fixture.network.documents[self.payload_url]=b'x'*len(self.payload)
        with self.assertRaises(CutError):self.client.download(selected,self.target)
        self.assertFalse(self.target.exists())

    def test_withdrawn_metadata_blocks_download(self):
        selected=self.client.check()
        self.fixture.network.release_by_id[self.release['id']]=dict(self.release,prerelease=True)
        with self.assertRaises(CutError):self.client.download(selected,self.target)
        self.assertFalse(self.target.exists())

    def test_canceled_download_does_not_execute_or_leave_file(self):
        selected=self.client.check();cancel=threading.Event();cancel.set()
        with self.assertRaises(CutError):self.client.download(selected,self.target,cancel.is_set)
        self.assertFalse(self.target.exists())

    def test_existing_destination_is_preserved(self):
        selected=self.client.check();self.target.write_bytes(b'user file')
        with self.assertRaises(CutError):self.client.download(selected,self.target)
        self.assertEqual(self.target.read_bytes(),b'user file')

    def test_file_changed_after_download_is_never_launched(self):
        selected=self.client.check();self.client.download(selected,self.target);self.target.write_bytes(b'changed')
        with patch.object(self.module.subprocess,'run') as child:
            with self.assertRaises(CutError):self.client.launch(selected,self.target)
            child.assert_not_called()

    def test_new_latest_does_not_replace_selected_release(self):
        selected=self.client.check();self.fixture.network.release=dict(self.release,tag_name='v9.0.0')
        self.client.download(selected,self.target)
        self.assertEqual(self.target.read_bytes(),self.payload)

    def test_cancel_during_final_confirmation_does_not_launch(self):
        selected=self.client.check();self.client.download(selected,self.target)
        cancel=threading.Event();original=self.client.confirm
        def confirmation(value):original(value);cancel.set()
        with patch.object(self.client,'confirm',side_effect=confirmation),patch.object(self.module.subprocess,'run') as child:
            with self.assertRaises(CutError):self.client.launch(selected,self.target,cancel=cancel.is_set)
            child.assert_not_called()

    def test_online_worker_surfaces_structured_child_error_instead_of_generic_exit(self):
        entry=importlib.import_module('tools.online_install_entry');events=queue.Queue();cancel=threading.Event()
        self.assertTrue(hasattr(entry,'run_online_worker'),'Online worker must preserve the child result')
        from contentrium_cut.installer_reporting import failure,write_report
        def child(command,**kwargs):
            result=command[command.index('--result')+1]
            write_report(result,failure(CutError('INSTALL_REGISTRATION','등록 확인 실패'),'location',version='0.2.0'))
            return type('Exit',(),{'returncode':1})()
        with patch.object(self.module.subprocess,'run',side_effect=child):
            entry.run_online_worker(self.config,events,cancel,client_factory=lambda config,root:self.client)
        outputs=[]
        while not events.empty():outputs.append(events.get())
        report=next(value for kind,value in outputs if kind=='failed')['report']
        self.assertEqual(report['error']['code'],'INSTALL_REGISTRATION');self.assertTrue(report['error']['hint'])


if __name__=='__main__':unittest.main()
