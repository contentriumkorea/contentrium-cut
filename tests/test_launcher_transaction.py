import base64
import hashlib
import json
import unittest
from unittest.mock import patch
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
from test_windows_install import WindowsInstallationTests


class LauncherTests(unittest.TestCase):
    setUp=WindowsInstallationTests.setUp
    payload=WindowsInstallationTests.payload
    old_install=WindowsInstallationTests.old_install
    def setup_signed(self):
        self.key=Ed25519PrivateKey.generate();self.public=base64.b64encode(self.key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)).decode()
        self.old_install()
        launcher=self.root/'Contentrium CUT Launcher.exe';launcher.write_bytes(b'old signed setup')
        config=dict(publicKey=self.public,appVersion='0.1.0',bundleId='old',signingKeyId='contentrium-cut-2026-01')
        (self.root/'launcher-config.json').write_text(json.dumps(config))
        old=dict(productId='com.contentrium.cut',appVersion='0.1.0',bundleId='old',signingKeyId='contentrium-cut-2026-01',
                 assets=[dict(role='installer',assetId=33,sha256=hashlib.sha256(launcher.read_bytes()).hexdigest(),size=launcher.stat().st_size)])
        raw=json.dumps(old).encode();sig=json.dumps(dict(algorithm='Ed25519',keyId=old['signingKeyId'],signature=base64.b64encode(self.key.sign(raw)).decode())).encode()
        (self.root/'updates'/'first-install.json').write_text(json.dumps(dict(manifest=base64.b64encode(raw).decode(),signature=base64.b64encode(sig).decode())))
        manifest,assets=self.payload('0.2.0','new');path=self.inputs/'new-setup.exe';path.write_bytes(b'new signed setup')
        assets['installer']=path;manifest['signingKeyId']='contentrium-cut-2026-01'
        manifest['assets'].append(dict(role='installer',assetId=34,name=path.name,sha256=hashlib.sha256(path.read_bytes()).hexdigest(),size=path.stat().st_size))
        raw=json.dumps(manifest).encode();sig=json.dumps(dict(algorithm='Ed25519',keyId=manifest['signingKeyId'],signature=base64.b64encode(self.key.sign(raw)).decode())).encode()
        (self.root/'updates'/'journal.json').write_text(json.dumps({'_attemptCandidate':dict(raw=base64.b64encode(raw).decode(),signature=base64.b64encode(sig).decode())}))
        return manifest,assets,launcher,config

    def test_signed_installer_replaces_and_rollback_restores_exact_launcher_config(self):
        manifest,assets,launcher,config=self.setup_signed();old=launcher.read_bytes()
        snapshot=self.hooks.snapshot(manifest);result=self.hooks.install(manifest,assets,snapshot)
        self.assertEqual(launcher.read_bytes(),assets['installer'].read_bytes())
        self.assertEqual(result['launcher']['sha256'],hashlib.sha256(launcher.read_bytes()).hexdigest())
        self.assertTrue(self.hooks.verify_application('0.2.0','new'))
        launcher.write_bytes(b'tampered');self.assertFalse(self.hooks.verify_application('0.2.0','new'))
        launcher.write_bytes(assets['installer'].read_bytes());self.hooks.rollback(snapshot)
        self.assertEqual(launcher.read_bytes(),old);self.assertEqual(json.loads((self.root/'launcher-config.json').read_text()),config)
        self.assertEqual((self.root/'models'/'large.bin').read_bytes(),b'keep model')

    def test_unknown_or_wrong_signed_launcher_refuses_snapshot_without_mutation(self):
        manifest,assets,launcher,config=self.setup_signed();launcher.write_bytes(b'unknown')
        before=len(self.adobe.calls)
        with self.assertRaises(Exception):self.hooks.snapshot(manifest)
        self.assertEqual(launcher.read_bytes(),b'unknown');self.assertFalse(any(a[0][1]=='/install' for a in self.adobe.calls[before:]))

    def test_interruption_after_launcher_replace_resumes_exact_owned_transaction(self):
        manifest,assets,launcher,config=self.setup_signed();snapshot=self.hooks.snapshot(manifest)
        original=self.w._atomic_json
        def fail_pointer(path,value):
            if path==self.hooks.active and value.get('appVersion')=='0.2.0':raise OSError('interrupted pointer')
            return original(path,value)
        with patch.object(self.w,'_atomic_json',side_effect=fail_pointer):
            with self.assertRaises(OSError):self.hooks.install(manifest,assets,snapshot)
        restarted=self.w.WindowsInstallation(self.root,upia_path=self.upia,runner=self.adobe,process_probe=lambda:[])
        result=restarted.install(manifest,assets,snapshot)
        self.assertEqual(result['status'],'PENDING_ACTIVATION');self.assertEqual(launcher.read_bytes(),assets['installer'].read_bytes())

    def test_every_owned_phase_can_resume_or_rollback_without_losing_old_bytes(self):
        for phase in ['REGISTERING','REGISTERED','PROMOTING','LAUNCHER_REPLACING','LAUNCHER_REPLACED','POINTER_COMMITTING','PENDING_ACTIVATION']:
            for action in ['resume','rollback']:
                with self.subTest(phase=phase,action=action):
                    isolated=LauncherTests('runTest');isolated.setUp()
                    try:
                        manifest,assets,launcher,config=isolated.setup_signed();snapshot=isolated.hooks.snapshot(manifest);writer=isolated.w._atomic_json
                        def interrupted(path,value):
                            writer(path,value)
                            if path==isolated.hooks.launcher_journal and value.get('state')==phase:raise OSError('phase interruption')
                        with patch.object(isolated.w,'_atomic_json',side_effect=interrupted):
                            with self.assertRaises(OSError):isolated.hooks.install(manifest,assets,snapshot)
                        if action=='resume':
                            isolated.hooks.install(manifest,assets,snapshot);self.assertEqual(launcher.read_bytes(),assets['installer'].read_bytes())
                        else:
                            isolated.hooks.rollback(snapshot);self.assertEqual(launcher.read_bytes(),b'old signed setup')
                        self.assertEqual((isolated.root/'models'/'large.bin').read_bytes(),b'keep model')
                    finally:isolated.doCleanups()

    def test_locked_launcher_wrong_asset_and_changed_descriptor_fail_closed(self):
        from contentrium_cut import launcher_transaction as transaction
        manifest,assets,launcher,config=self.setup_signed();snapshot=self.hooks.snapshot(manifest)
        original=transaction.replace
        def locked(source,target,expected):
            if target==launcher:raise PermissionError('locked')
            return original(source,target,expected)
        with patch.object(transaction,'replace',side_effect=locked):
            with self.assertRaises(PermissionError):self.hooks.install(manifest,assets,snapshot)
        self.assertEqual(launcher.read_bytes(),b'old signed setup')

    def test_unknown_config_after_interruption_cannot_be_overwritten_by_resume(self):
        manifest,assets,launcher,config=self.setup_signed();snapshot=self.hooks.snapshot(manifest);writer=self.w._atomic_json
        def interrupted(path,value):
            writer(path,value)
            if path==self.hooks.launcher_journal and value.get('state')=='REGISTERED':raise OSError('interruption')
        with patch.object(self.w,'_atomic_json',side_effect=interrupted):
            with self.assertRaises(OSError):self.hooks.install(manifest,assets,snapshot)
        path=self.root/'launcher-config.json';path.write_text(json.dumps(dict(config,unknownChange=True)))
        before=path.read_bytes()
        with self.assertRaises(Exception):self.hooks.install(manifest,assets,snapshot)
        self.assertEqual(path.read_bytes(),before)
        self.hooks.rollback(snapshot)
        assets['installer'].write_bytes(b'wrong signed bytes')
        with self.assertRaises(Exception):self.hooks.install(manifest,assets,snapshot)
        self.assertEqual(launcher.read_bytes(),b'old signed setup')

    def test_signed_then_optional_update_rolls_back_without_adopting_old_journal(self):
        manifest,assets,launcher,_=self.setup_signed()
        first=self.hooks.snapshot(manifest);self.hooks.install(manifest,assets,first)
        history=self.hooks.launcher_journal.read_bytes()
        optional,prepared=self.payload('0.3.0','third')
        second=self.hooks.snapshot(optional);self.hooks.install(optional,prepared,second)
        self.hooks.rollback(second)
        self.assertTrue(self.hooks.verify_previous(second))
        self.assertEqual(self.hooks.launcher_journal.read_bytes(),history)
        self.assertEqual(launcher.read_bytes(),assets['installer'].read_bytes())

    def test_failure_before_next_launcher_intent_does_not_poison_rollback(self):
        manifest,assets,launcher,_=self.setup_signed()
        first=self.hooks.snapshot(manifest);self.hooks.install(manifest,assets,first)
        history=self.hooks.launcher_journal.read_bytes()
        next_manifest,prepared=self.payload('0.3.0','third')
        next_manifest['assets'].append(dict(manifest['assets'][-1]));prepared['installer']=assets['installer']
        second=self.hooks.snapshot(next_manifest)
        with self.assertRaises(Exception):self.hooks.install(next_manifest,prepared,second)
        self.hooks.rollback(second)
        self.assertTrue(self.hooks.verify_previous(second))
        self.assertEqual(self.hooks.launcher_journal.read_bytes(),history)

    def test_same_signed_release_retry_after_verified_rollback_keeps_history(self):
        manifest,assets,launcher,_=self.setup_signed()
        first=self.hooks.snapshot(manifest);self.hooks.install(manifest,assets,first);self.hooks.rollback(first)
        old=json.loads(self.hooks.launcher_journal.read_text())
        second=self.hooks.snapshot(manifest);self.hooks.install(manifest,assets,second)
        self.assertTrue(self.hooks.verify_application('0.2.0','new'))
        self.assertNotEqual(json.loads(self.hooks.launcher_journal.read_text())['snapshotHash'],old['snapshotHash'])
        saved=self.root/'updates'/'launcher-history'/old['snapshotHash']/'launcher-transaction.json'
        self.assertEqual(json.loads(saved.read_text()),old)
        self.hooks.rollback(second);self.assertTrue(self.hooks.verify_previous(second))
        self.assertEqual(json.loads(saved.read_text()),old)
