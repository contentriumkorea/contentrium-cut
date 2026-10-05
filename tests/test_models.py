import hashlib
import importlib
import json
import tempfile
import unittest
from pathlib import Path


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.models = importlib.import_module('contentrium_cut.models')

    def test_missing_model_describes_terms_and_offline_boundary(self):
        status = self.models.ModelManager(self.tmp.name).state('community-1')
        self.assertEqual(status['status'], 'not_installed')
        self.assertTrue(status['setup']['requiresTermsAcceptance'])
        self.assertFalse(status['setup']['audioUploadRequired'])

    def test_community_uses_decoded_waveform_and_preserves_regular_overlap(self):
        import numpy as np
        from scipy.io import wavfile
        from types import SimpleNamespace
        path=Path(self.tmp.name)/'decoded.wav'
        wavfile.write(path,16000,np.array([0,16384,-16384],dtype=np.int16))
        diarizer=object.__new__(self.models.CommunityDiarizer)
        calls=[]
        def pipeline(audio,**kwargs):
            calls.append(audio)
            self.assertEqual(tuple(audio['waveform'].shape),(1,3))
            self.assertEqual(audio['sample_rate'],16000)
            np.testing.assert_allclose(audio['waveform'].numpy(),[[0,.5,-.5]])
            regular=SimpleNamespace(itertracks=lambda **kwargs:iter([(SimpleNamespace(start=0,end=2),None,'A'),(SimpleNamespace(start=1,end=3),None,'B')]))
            return SimpleNamespace(speaker_diarization=regular)
        diarizer.pipeline=pipeline
        self.assertEqual(diarizer.turns(path),[(0.,2.,'A'),(1.,3.,'B')])
        self.assertEqual(len(calls),1)

    def test_verified_manifest_required_and_hash_tamper_detected(self):
        root = Path(self.tmp.name) / 'silero'
        root.mkdir()
        model = root / 'silero_vad.onnx'
        model.write_bytes(b'model-fixture')
        manifest = {'modelId': 'silero', 'revision': 'fixture-pinned', 'entrypoint': 'silero_vad.onnx',
                    'source': 'https://github.com/snakers4/silero-vad',
                    'files': {'silero_vad.onnx': hashlib.sha256(model.read_bytes()).hexdigest()}}
        (root / 'manifest.json').write_text(json.dumps(manifest))
        manager = self.models.ModelManager(self.tmp.name)
        self.assertEqual(manager.state('silero')['status'], 'ready')
        model.write_bytes(b'corruption')
        self.assertEqual(manager.state('silero')['status'], 'error')

    def test_manifest_cannot_escape_model_directory(self):
        root = Path(self.tmp.name) / 'silero'
        root.mkdir()
        (root / 'manifest.json').write_text(json.dumps({'modelId': 'silero', 'revision': 'x',
                 'entrypoint': '../secret.onnx', 'files': {'../secret.onnx': '0'*64}}))
        self.assertEqual(self.models.ModelManager(self.tmp.name).state('silero')['status'], 'error')

    def test_token_absence_blocks_setup_without_network(self):
        from contentrium_cut.contract import CutError
        with self.assertRaises(CutError) as cm:
            self.models.ModelManager(self.tmp.name).install_community(token=None, terms_accepted=True, revision='abc')
        self.assertEqual(cm.exception.code, 'MODEL_NOT_READY')
        self.assertNotIn('token', str(cm.exception.details).lower())

    def test_partial_community_config_is_not_ready(self):
        root=Path(self.tmp.name)/'community-1';root.mkdir()
        config=root/'config.yaml'; config.write_text('pipeline: incomplete')
        (root/'manifest.json').write_text(json.dumps({'modelId':'community-1','revision':'fixture',
            'entrypoint':'config.yaml','files':{'config.yaml':hashlib.sha256(config.read_bytes()).hexdigest()}}))
        self.assertEqual(self.models.ModelManager(self.tmp.name).state('community-1')['status'],'error')

    def test_download_exception_does_not_expose_credentials(self):
        from contentrium_cut.contract import CutError
        from unittest.mock import patch
        import sys
        from types import SimpleNamespace
        def download(**kwargs): raise RuntimeError('credential-should-never-appear')
        with patch.dict(sys.modules,{'huggingface_hub':SimpleNamespace(snapshot_download=download)}):
            with self.assertRaises(CutError) as cm:
                self.models.ModelManager(self.tmp.name).install_community(token='credential-should-never-appear',terms_accepted=True,revision='a'*40)
        self.assertNotIn('credential-should-never-appear',str(cm.exception))
        self.assertNotIn('credential-should-never-appear',str(cm.exception.details))

    def test_incomplete_setup_does_not_damage_existing_model(self):
        from contentrium_cut.contract import CutError
        from unittest.mock import patch
        import sys
        from types import SimpleNamespace
        root=Path(self.tmp.name)/'community-1'; root.mkdir()
        existing=root/'segmentation.bin'; existing.write_bytes(b'original')
        def download(**kwargs):
            path=Path(kwargs['local_dir']);(path/'segmentation.bin').write_bytes(b'partial')
            raise RuntimeError('download interrupted')
        with patch.dict(sys.modules,{'huggingface_hub':SimpleNamespace(snapshot_download=download)}):
            with self.assertRaises(CutError):
                self.models.ModelManager(self.tmp.name).install_community(token='fixture-access',terms_accepted=True,revision='a'*40)
        self.assertEqual(existing.read_bytes(),b'original')

    def test_malformed_manifest_containers_and_field_types_are_actionable_errors(self):
        from contentrium_cut.contract import CutError
        root = Path(self.tmp.name) / 'silero'
        root.mkdir()
        (root / 'x.onnx').write_bytes(b'fixture')
        valid = {'modelId': 'silero', 'revision': 'pinned', 'entrypoint': 'x.onnx',
                 'files': {'x.onnx': hashlib.sha256(b'fixture').hexdigest()}}
        malformed = [[], None, 'manifest', 1, dict(valid, files=['x.onnx']),
                     dict(valid, files='x.onnx'), dict(valid, revision=['pinned']),
                     dict(valid, entrypoint=['x.onnx']), dict(valid, files={'x.onnx': 5})]
        manager = self.models.ModelManager(self.tmp.name)
        for manifest in malformed:
            with self.subTest(manifest=manifest):
                (root / 'manifest.json').write_text(json.dumps(manifest))
                self.assertEqual(manager.state('silero')['status'], 'error')
                with self.assertRaises(CutError) as caught:
                    manager.resolve('silero')
                self.assertEqual(caught.exception.code, 'MODEL_NOT_READY')
                self.assertIn('setup', caught.exception.details)


if __name__ == '__main__':
    unittest.main()
