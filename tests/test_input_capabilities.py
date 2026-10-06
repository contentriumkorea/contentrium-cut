import importlib
import tempfile
import unittest
from pathlib import Path
from contentrium_cut.contract import CutError


class InputTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        try:self.module=importlib.import_module('contentrium_cut.input_capabilities')
        except ModuleNotFoundError:self.fail('Input capability verification is missing')
        self.root=Path(self.tmp.name);self.path=self.root/'audio.wav';self.path.write_bytes(b'original audio')
        self.sources=[dict(assetId='audio',path=str(self.path),name='Audio',bounds={'VIDEO':None,'AUDIO':{'in':'0','out':'100'}})]
        self.manager=self.module.InputCapabilities(self.root)

    def test_probe_every_source_and_reject_audio_only_camera(self):
        selection=self.manager.select('owner','project',self.sources)
        payload=self.manager.payload(selection['selectionId'],'owner','project')
        seen=[]
        def probe(path,cancel):seen.append(path);return {'streams':[{'index':0,'codec_type':'audio','channels':2}]}
        result=self.module.probe_sources(payload,probe=probe)
        cap=self.manager.promote(selection['selectionId'],'owner','project',result)
        self.assertEqual(seen,[str(self.path)]);self.assertFalse(cap['assets'][0]['hasVideo'])
        with self.assertRaises(CutError):self.manager.plan(cap['capabilityId'],'owner','project',[dict(assetId='audio',role='camera',outputAudio=True)])

    def test_source_replacement_foreign_owner_expiry_and_duplicate_selection_fail(self):
        selected=self.manager.select('owner','project',self.sources)
        payload=self.manager.payload(selected['selectionId'],'owner','project')
        result=self.module.probe_sources(payload,probe=lambda path,cancel:{'streams':[{'index':0,'codec_type':'video'},{'index':1,'codec_type':'audio','channels':2}]})
        cap=self.manager.promote(selected['selectionId'],'owner','project',result)
        choices=[dict(assetId='audio',role='camera',outputAudio=True)]
        self.assertEqual(self.manager.plan(cap['capabilityId'],'owner','project',choices)[0]['kind'],'input')
        with self.assertRaises(CutError):self.manager.plan(cap['capabilityId'],'foreign','project',choices)
        self.path.write_bytes(b'new audio')
        with self.assertRaises(CutError):self.manager.recheck(cap['capabilityId'],'owner','project')
        with self.assertRaises(CutError):self.manager.select('owner','project',self.sources*2)
        self.manager.clock=lambda:float('inf')
        with self.assertRaises(CutError):self.manager.recheck(cap['capabilityId'],'owner','project')

    def test_probe_cancellation_and_changed_file_reject_promotion(self):
        selected=self.manager.select('owner','project',self.sources)
        payload=self.manager.payload(selected['selectionId'],'owner','project')
        with self.assertRaises(CutError):self.module.probe_sources(payload,cancel=lambda:True,probe=lambda p,c:None)
        def replacing(path,cancel):self.path.write_bytes(b'replaced');return {'streams':[]}
        with self.assertRaises(CutError):self.module.probe_sources(payload,probe=replacing)
