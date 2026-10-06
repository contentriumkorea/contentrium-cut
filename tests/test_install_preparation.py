"""Installer enrollment: real temporary files; explicit native/Adobe boundaries."""
import importlib
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from contentrium_cut import bootstrap
from contentrium_cut.contract import CutError


class PreparationFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name); self.root = self.base / 'physical-install'
        self.roaming = self.base / 'roaming'
        self.data = self.roaming / 'Adobe/UXP/PluginsStorage/PPRO/26/External/com.contentrium.cut/PluginData'
        self.config = dict(productId='com.contentrium.cut', appVersion='0.2.0', bundleId='bundle-new')
        self.now = 1000
        self.code = self.root / 'app/versions/0.2.0'
        model = self.code / '_internal/silero'; model.mkdir(parents=True)
        (model / 'model.onnx').write_bytes(b'exact signed fixture model')
        (model / 'manifest.json').write_text(json.dumps(dict(modelId='silero', revision='fixture', entrypoint='model.onnx',
            files={'model.onnx': hashlib.sha256(b'exact signed fixture model').hexdigest()})))
        self.verified = []
        owner = self
        class Installed:
            def verify_application(self, version, bundle):
                owner.verified.append((version, bundle)); return (version, bundle) == ('0.2.0', 'bundle-new')
        self.installation = Installed()
        physical = patch.object(bootstrap, '_physical_path', lambda path: path.resolve())
        physical.start(); self.addCleanup(physical.stop)
        # Inject write-through native commit; ordinary temporary-file writes remain real.
        class Kernel:
            def MoveFileExW(self, source, target, flags):
                if flags & 1: Path(source).replace(target)
                else:
                    if Path(target).exists(): return False
                    Path(source).rename(target)
                return True
        for name in ['contentrium_cut.bootstrap._kernel','contentrium_cut.windows_install._kernel']:
            native = patch(name, return_value=Kernel()); native.start(); self.addCleanup(native.stop)

    def prepare(self, **kwargs):
        try: module = importlib.import_module('contentrium_cut.installer_preparation')
        except ModuleNotFoundError: self.fail('installer preparation helper is missing')
        return module.prepare_installation(self.root, self.config, installation=self.installation,
            owner_sid='S-1-5-21-123', roaming_root=self.roaming, protect=lambda *a: None,
            clock=lambda: self.now, **kwargs)

    def receipt(self, **changes):
        challenge = json.loads((self.data / 'contentrium-install-challenge.json').read_text())
        receipt = dict(challenge, verifiedBy='installed-uxp-getDataFolder', nativePath=str(self.data))
        receipt.update(changes)
        (self.data / 'contentrium-install-receipt.json').write_text(json.dumps(receipt))
        return receipt


class PreparationTests(PreparationFixture):
    def test_missing_receipt_is_durable_pending_then_exact_receipt_provisions_and_copies_model(self):
        first = self.prepare()
        self.assertEqual(first['status'], 'PENDING_PROVISIONING')
        self.assertFalse((self.root / 'private/auth-bootstrap.json').exists())
        self.assertEqual(self.prepare(), first)
        self.receipt()
        result = self.prepare()
        self.assertEqual(result['status'], 'PREPARED')
        self.assertEqual((self.root / 'models/silero/model.onnx').read_bytes(), b'exact signed fixture model')
        secret = (self.root / 'private/auth-bootstrap.json').read_bytes()
        self.assertEqual((self.data / 'contentrium-bootstrap.json').read_bytes(), secret)
        self.assertEqual(result['mappingEvidence']['canonicalPluginData'], str(self.data))
        self.assertTrue(self.verified)
        self.assertNotIn('secret', json.dumps(result))
        challenge = (self.data / 'contentrium-install-challenge.json').read_bytes()
        self.now += 1000
        self.assertEqual(self.prepare()['status'], 'PREPARED')
        self.assertEqual((self.data / 'contentrium-install-challenge.json').read_bytes(), challenge)
        self.assertEqual((self.root / 'private/auth-bootstrap.json').read_bytes(), secret)

    def test_wrong_receipt_never_provisions_or_replaces_evidence(self):
        self.prepare()
        for changes in [dict(nonce='a'*64), dict(appVersion='0.3.0'), dict(bundleId='other-bundle'),
                        dict(hostMajor=27), dict(nativePath=str(self.data).replace('External', 'Developer')),
                        dict(nativePath=str(self.data).replace('PPRO\\26', 'PPRO\\27')), dict(expiresAt=999),
                        dict(productId='other.product'), dict(extra='outside')]:
            with self.subTest(changes=changes):
                self.receipt(**changes)
                before = (self.data / 'contentrium-install-receipt.json').read_bytes()
                with self.assertRaises(CutError): self.prepare()
                self.assertFalse((self.root / 'private/auth-bootstrap.json').exists())
                self.assertEqual((self.data / 'contentrium-install-receipt.json').read_bytes(), before)

    def test_expired_owned_challenge_refresh_and_replay_are_bounded(self):
        self.prepare(); old = self.receipt()
        self.now += 601
        self.assertEqual(self.prepare()['status'], 'PENDING_PROVISIONING')
        new = json.loads((self.data / 'contentrium-install-challenge.json').read_text())
        self.assertNotEqual(old['nonce'], new['nonce'])
        (self.data / 'contentrium-install-receipt.json').write_text(json.dumps(old))
        with self.assertRaises(CutError): self.prepare()
        self.receipt(); self.prepare()
        for path in [self.root / 'private/auth-bootstrap.json', self.root / 'install-location.json', self.data / 'contentrium-bootstrap.json']:
            path.unlink()
        with self.assertRaises(CutError): self.prepare()

    def test_unknown_public_or_private_files_are_preserved(self):
        self.data.mkdir(parents=True)
        for name in ['contentrium-install-challenge.json', 'contentrium-install-receipt.json', 'contentrium-bootstrap.json']:
            path = self.data / name; path.write_text('unknown')
            with self.assertRaises(CutError): self.prepare()
            self.assertEqual(path.read_text(), 'unknown'); path.unlink()

    def test_conflicting_model_fails_and_valid_user_model_is_retained(self):
        self.prepare(); self.receipt()
        user = self.root / 'models/silero'; user.mkdir(parents=True)
        (user / 'unknown.onnx').write_bytes(b'user model')
        with self.assertRaises(CutError) as error: self.prepare()
        self.assertEqual(error.exception.code, 'INSTALL_MODEL_CONFLICT')
        self.assertEqual((user / 'unknown.onnx').read_bytes(), b'user model')
        (user / 'manifest.json').write_text(json.dumps(dict(modelId='silero', revision='user', entrypoint='unknown.onnx',
            files={'unknown.onnx': hashlib.sha256(b'user model').hexdigest()})))
        result = self.prepare()
        self.assertEqual(result['modelPreparation']['action'], 'retained')
        self.assertFalse((user / 'model.onnx').exists())

    def test_corrupt_bundled_model_and_unverified_code_never_copy(self):
        self.prepare(); self.receipt()
        (self.code / '_internal/silero/model.onnx').write_bytes(b'corrupt')
        with self.assertRaises(CutError) as error: self.prepare()
        self.assertEqual(error.exception.code, 'INSTALL_MODEL_BUNDLE')
        self.assertFalse((self.root / 'models/silero').exists())
        self.installation.verify_application = lambda *a: False
        with self.assertRaises(CutError) as error: self.prepare()
        self.assertEqual(error.exception.code, 'INSTALL_PREPARATION_VERIFY')

    def test_target_change_while_pending_and_unknown_state_preserve_original(self):
        self.prepare(); state=self.root/'updates/install-preparation.json';before=state.read_bytes()
        self.config['bundleId']='different-bundle';self.installation.verify_application=lambda *a:True
        with self.assertRaises(CutError):self.prepare()
        self.assertEqual(state.read_bytes(),before)
        self.config['bundleId']='bundle-new'
        state.write_text('{}')
        with self.assertRaises(CutError):self.prepare()
        self.assertEqual(state.read_text(),'{}')

    def test_consumed_intent_before_any_private_write_resumes_only_its_exact_receipt(self):
        self.prepare();self.receipt()
        with patch.object(bootstrap,'provision',side_effect=OSError('interruption')):
            with self.assertRaises(OSError):self.prepare()
        self.assertFalse((self.root/'private/auth-bootstrap.json').exists())
        self.assertEqual(self.prepare()['status'],'PREPARED')

    def test_refresh_interruption_after_new_public_receipt_preserves_current_nonce(self):
        self.prepare();self.receipt();self.now+=601
        module=importlib.import_module('contentrium_cut.installer_preparation');original=module._atomic_json
        def interrupt(path,value):
            if path.name=='contentrium-install-challenge.json':
                original(path,value);self.receipt();raise OSError('interruption after panel receipt')
            return original(path,value)
        with patch.object(module,'_atomic_json',interrupt):
            with self.assertRaises(OSError):self.prepare()
        before=(self.data/'contentrium-install-receipt.json').read_bytes()
        self.assertEqual(self.prepare()['status'],'PREPARED')
        self.assertEqual((self.data/'contentrium-install-receipt.json').read_bytes(),before)

    def test_retained_bootstrap_update_does_not_reenroll_or_rewrite_key(self):
        self.prepare();self.receipt();self.prepare()
        before={p:p.read_bytes() for p in [self.root/'private/auth-bootstrap.json',self.data/'contentrium-install-challenge.json',self.root/'updates/install-preparation.json']}
        import shutil
        shutil.copytree(self.code,self.root/'app/versions/0.3.0')
        self.config.update(appVersion='0.3.0',bundleId='bundle-updated');self.installation.verify_application=lambda *a:True
        self.assertEqual(self.prepare()['status'],'PREPARED')
        self.assertEqual({p:p.read_bytes() for p in before},before)

    def test_real_js_receipt_preserves_unicode_native_path_and_exact_utf8_hash(self):
        import subprocess
        import time
        self.roaming=self.base/'한글 사용자 공간'/'Roaming'
        self.data=self.roaming/'Adobe/UXP/PluginsStorage/PPRO/26/External/com.contentrium.cut/PluginData'
        self.now=int(time.time());self.prepare()
        subprocess.run(['node',str(Path(__file__).with_name('enrollment_receipt.js')),str(self.data),json.dumps(self.config)],
            check=True,capture_output=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        raw=(self.data/'contentrium-install-receipt.json').read_bytes()
        self.assertIn('한글 사용자 공간'.encode(),raw)
        self.assertEqual(json.loads(raw)['nativePath'],str(self.data))
        self.assertEqual(self.prepare()['mappingEvidence']['receiptHash'],hashlib.sha256(raw).hexdigest())

    def test_staged_model_copy_corruption_never_commits(self):
        import shutil
        self.prepare();self.receipt();copy=shutil.copytree
        def corrupt(source,target,*args,**kwargs):
            result=copy(source,target,*args,**kwargs)
            (target/'model.onnx').write_bytes(b'copy corruption');return result
        with patch.object(shutil,'copytree',corrupt):
            with self.assertRaises(CutError) as error:self.prepare()
        self.assertEqual(error.exception.code,'INSTALL_MODEL_BUNDLE')
        self.assertFalse((self.root/'models/silero').exists())
        self.assertFalse(list((self.root/'models').iterdir()))


if __name__ == '__main__': unittest.main()
