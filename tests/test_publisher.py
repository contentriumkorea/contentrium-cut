"""Real publisher staging with temporary signing keys and a bounded GitHub double."""
import base64
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


SPEC = importlib.util.spec_from_file_location('cut_publisher', Path(__file__).resolve().parents[1]/'tools/publish_release.py')
publisher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publisher)


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.output = self.root/'output/0.1.1'; self.output.mkdir(parents=True)
        self.key = Ed25519PrivateKey.generate()
        public = self.key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.config = dict(productId='com.contentrium.cut',appVersion='0.1.1',bundleId='contentrium-cut-0.1.1',
                           signingKeyId='test-key',publicKey=base64.b64encode(public).decode())
        (self.root/'config.json').write_text(json.dumps(self.config))
        key_file = self.root/'Contentrium CUT Publisher/ed25519.pem'; key_file.parent.mkdir()
        key_file.write_bytes(self.key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        self.notes = self.root/'notes.md'; self.notes.write_text('Reviewed fixture notes.',encoding='utf-8')
        for name in ['Contentrium-CUT-Windows-x64.zip','Contentrium-CUT.ccx','Contentrium-CUT-Setup.exe']:
            (self.output/name).write_bytes(('fixture '+name).encode())
        self.target = 'a'*40
        self.release = dict(id=71,tag_name='v0.1.1',draft=True,name='Contentrium CUT',target_commitish=self.target,
                            assets=[],html_url='https://example.invalid/fixture-release')
        self.remote = {}; self.interrupt = True; self.corrupt_digest = False; self.corrupt_documents = False; self.calls = []

    def gh(self,*args,capture=False):
        self.calls.append(args)
        if args == ('api','repos/contentriumkorea/contentrium-cut/releases'):
            return json.dumps([self.release]).encode()
        if args[:2] == ('release','upload'):
            for raw in args[5:]:
                path=Path(raw); data=path.read_bytes()
                self.assertNotIn(path.name,self.remote,'Publisher must never replace a remote asset.')
                self.remote[path.name]=data
                corrupt=self.corrupt_digest or self.corrupt_documents and path.name.startswith('update-manifest.')
                self.release['assets'].append(dict(id=100+len(self.remote),name=path.name,size=len(data),
                    state='uploaded',digest='sha256:'+('0'*64 if corrupt else hashlib.sha256(data).hexdigest())))
            return None
        if args[:2] == ('release','edit'):
            if self.interrupt:
                self.interrupt=False
                raise RuntimeError('Simulated interruption before publish')
            self.release['draft']=False
            return None
        raise AssertionError('Unexpected external command: '+repr(args))

    def run_publisher(self,target=None):
        with patch.object(publisher,'ROOT',self.root), patch.object(publisher,'gh',side_effect=self.gh), \
                patch.dict(publisher.os.environ,{'LOCALAPPDATA':str(self.root)}), \
                patch('sys.argv',['publish_release.py','--notes',str(self.notes),'--target',target or self.target]), \
                contextlib.redirect_stdout(io.StringIO()):
            publisher.main()

    def staged(self):
        with self.assertRaisesRegex(RuntimeError,'Simulated interruption'):
            self.run_publisher()
        return {name:(self.output/name).read_bytes() for name in ['update-manifest.json','update-manifest.sig']}

    def test_resume_preserves_signed_manifest_and_asset_identity(self):
        before=self.staged(); asset_ids=[a['id'] for a in self.release['assets']]
        self.run_publisher()
        self.assertFalse(self.release['draft'])
        self.assertEqual(asset_ids,[a['id'] for a in self.release['assets']])
        for name,data in before.items():
            self.assertEqual((self.output/name).read_bytes(),data)
            self.assertEqual(self.remote[name],data)

    def test_changed_notes_on_resume_preserve_original_documents(self):
        before=self.staged(); self.notes.write_text('Different unreviewed notes.',encoding='utf-8')
        with self.assertRaises(RuntimeError): self.run_publisher()
        self.assertTrue(self.release['draft'])
        self.assertEqual(before,{name:(self.output/name).read_bytes() for name in before})

    def test_tampered_signature_is_not_silently_replaced(self):
        self.staged(); path=self.output/'update-manifest.sig'
        value=json.loads(path.read_text());value['signature']=base64.b64encode(b'x'*64).decode();path.write_text(json.dumps(value))
        tampered=path.read_bytes()
        with self.assertRaises(RuntimeError):self.run_publisher()
        self.assertEqual(path.read_bytes(),tampered);self.assertTrue(self.release['draft'])

    def test_missing_signature_resumes_the_same_staged_manifest(self):
        before=self.staged(); (self.output/'update-manifest.sig').unlink()
        self.run_publisher()
        self.assertFalse(self.release['draft'])
        self.assertEqual((self.output/'update-manifest.json').read_bytes(),before['update-manifest.json'])
        self.assertEqual((self.output/'update-manifest.sig').read_bytes(),before['update-manifest.sig'])

    def test_branch_target_is_rejected_before_external_commands(self):
        self.release['target_commitish']='main'
        with self.assertRaises(RuntimeError):self.run_publisher('main')
        self.assertEqual(self.calls,[])

    def test_new_upload_digest_mismatch_never_reaches_signing_or_publication(self):
        self.corrupt_digest=True
        with self.assertRaises(RuntimeError):self.run_publisher()
        self.assertFalse((self.output/'update-manifest.json').exists())
        self.assertTrue(self.release['draft'])

    def test_staged_manifest_requires_exact_json_types_before_resigning(self):
        fields=dict(schemaVersion=1,signingKeyId='test-key',assets=[dict(size=1)])
        publisher.signed_documents(self.output,fields,self.key)
        manifest=self.output/'update-manifest.json';signature=self.output/'update-manifest.sig'
        value=json.loads(manifest.read_bytes());value['schemaVersion']=True
        manifest.write_text(json.dumps(value));signature.unlink();before=manifest.read_bytes()
        with self.assertRaises(RuntimeError):publisher.signed_documents(self.output,fields,self.key)
        self.assertEqual(manifest.read_bytes(),before);self.assertFalse(signature.exists())

    def test_concurrent_manifest_creation_cannot_adopt_a_mismatched_signature(self):
        fields=dict(schemaVersion=1,signingKeyId='test-key')
        manifest=self.output/'update-manifest.json'
        concurrent=json.dumps(dict(fields,builtAt='2020-01-01T00:00:00+00:00')).encode()
        original_exists=Path.exists;checks=[]
        def exists(path):
            if path==manifest:
                checks.append(1)
                if len(checks)==2:manifest.write_bytes(concurrent)
            return original_exists(path)
        with patch.object(Path,'exists',exists):
            with self.assertRaises(RuntimeError):publisher.signed_documents(self.output,fields,self.key)
        self.assertEqual(manifest.read_bytes(),concurrent)

    def test_uploaded_signed_document_digest_is_checked_before_publication(self):
        self.corrupt_documents=True
        with self.assertRaises(RuntimeError):self.run_publisher()
        self.assertTrue(self.release['draft'])
        self.assertFalse(any(call[:2]==('release','edit') for call in self.calls))

    def test_new_draft_visibility_delay_retries_without_duplicate_creation(self):
        original=self.gh; absent=[True,True,True]; created=[]
        def delayed(*args,capture=False):
            if args==('api','repos/contentriumkorea/contentrium-cut/releases') and absent:
                absent.pop();return b'[]'
            if args[:2]==('release','create'):
                created.append(args);return None
            return original(*args,capture=capture)
        self.gh=delayed;self.interrupt=False
        with patch('time.sleep') as sleep:
            self.run_publisher()
        self.assertEqual(len(created),1)
        self.assertEqual(sleep.call_count,2)
        self.assertEqual(len(self.remote),5)
        self.assertFalse(self.release['draft'])

    def test_missing_created_draft_stops_bounded_before_upload_or_signing(self):
        calls=[]
        def invisible(*args,capture=False):
            calls.append(args)
            if args==('api','repos/contentriumkorea/contentrium-cut/releases'):return b'[]'
            if args[:2]==('release','create'):return None
            self.fail('Must not upload or publish an unobserved draft.')
        self.gh=invisible
        with patch('time.sleep') as sleep:
            with self.assertRaisesRegex(RuntimeError,'not yet visible'):
                self.run_publisher()
        self.assertEqual(sum(call[:2]==('release','create') for call in calls),1)
        self.assertEqual(sleep.call_count,5)
        self.assertFalse((self.output/'update-manifest.json').exists())


if __name__=='__main__':unittest.main()
