import unittest
import tempfile,json
from pathlib import Path
from contextlib import contextmanager
from unittest.mock import patch
import test_audio as fixtures
from contentrium_cut.contract import CutError


class ResourceTests(unittest.TestCase):
    def setUp(self):
        self.media=fixtures.AudioTests();self.media.setUp();self.addCleanup(self.media.doCleanups)

    def test_pcm_budget_blocks_before_actual_decoder_launch(self):
        source=self.media.source('a',self.media.signal(2));native=self.media.audio._run;decoders=[]
        def observed(argv,*args,**kwargs):
            if argv[0].lower().endswith('ffmpeg.exe'):decoders.append(argv)
            return native(argv,*args,**kwargs)
        with patch('contentrium_cut.audio._run',side_effect=observed):
            with self.assertRaises(CutError) as error:
                self.media.audio._decode(source,self.media.tmp.name,0,{'maxDecodedBytes':1000})
        self.assertEqual(error.exception.code,'AUDIO_RESOURCE_LIMIT');self.assertEqual(decoders,[])
    def test_available_disk_and_memory_fail_before_decoding_mixed_pcm(self):
        from contentrium_cut.resource import preflight_pcm
        from collections import namedtuple
        Disk=namedtuple('Disk','total used free')
        with patch('contentrium_cut.resource.shutil.disk_usage',return_value=Disk(100,99,1)):
            with self.assertRaises(CutError):preflight_pcm(2,{},self.media.tmp.name)
        with patch('contentrium_cut.resource.available_memory',return_value=100):
            with self.assertRaises(CutError):preflight_pcm(2,{},self.media.tmp.name,mixed=True)
    def test_resource_settings_are_persisted_validated_and_injected_into_job_payload(self):
        from contentrium_cut.coordinator import Coordinator
        import test_coordinator as fixture
        c=Coordinator(self.media.tmp.name);c.bind('p',fixture.CoordinatorTests().fixture())
        status=c.resource_settings({'cacheBudgetBytes':1024,'maxDecodedBytes':2048,'device':'cpu'})
        self.assertEqual(status['settings']['cacheBudgetBytes'],1024)
        fresh=Coordinator(self.media.tmp.name);self.assertEqual(fresh.resource_settings()['settings']['maxDecodedBytes'],2048)
        self.assertEqual(c.audio_payload('p',{'mode':'mixed','microphones':[{'instanceKey':'mic','channelIndex':0}]})['settings']['maxDecodedBytes'],2048)
        for update in [{'maxDecodedBytes':True},{'cacheRoot':'arbitrary'},{'mixedChunkSeconds':601},{'device':'cloud'}]:
            with self.assertRaises(CutError):c.resource_settings(update)
    def test_cache_pruning_requires_held_guard_and_only_deletes_completed_envelopes(self):
        from contentrium_cut.resource import prune_completed_cache
        from contentrium_cut.cache import envelope
        import test_corrections as fixture
        root=Path(self.media.tmp.name)/'cache';root.mkdir();key='a'*64
        cache=root/(key+'.json');cache.write_text(json.dumps(envelope('analysis',key,fixture.CorrectionTests().raw())),encoding='utf-8')
        protected=root/'corrections.json';protected.write_text('durable history',encoding='utf-8')
        incomplete=root/('b'*64+'.json');incomplete.write_text('{"status":"running"}',encoding='utf-8')
        @contextmanager
        def denied():yield False
        with self.assertRaises(CutError):prune_completed_cache(root,1,denied)
        self.assertTrue(cache.exists())
        @contextmanager
        def allowed(entry=None):yield True
        result=prune_completed_cache(root,1,allowed)
        self.assertEqual(result['removed'],[cache.name]);self.assertTrue(protected.exists());self.assertTrue(incomplete.exists());self.assertFalse(result['budgetMet'])
    def test_partial_chunk_update_uses_persisted_overlap_and_survives_reload(self):
        from contentrium_cut.coordinator import Coordinator
        c=Coordinator(self.media.tmp.name)
        saved=c.resource_settings({'mixedChunkSeconds':10,'mixedOverlapSeconds':1})['settings']
        self.assertEqual((saved['mixedChunkSeconds'],saved['mixedOverlapSeconds']),(10,1))
        reopened=Coordinator(self.media.tmp.name)
        updated=reopened.resource_settings({'mixedChunkSeconds':20})['settings']
        self.assertEqual((updated['mixedChunkSeconds'],updated['mixedOverlapSeconds']),(20,1))
        self.assertEqual(Coordinator(self.media.tmp.name).resource_settings()['settings'],updated)
    def test_partial_resource_update_rejects_bad_shape_keys_types_and_merged_conflicts(self):
        from contentrium_cut.coordinator import Coordinator
        c=Coordinator(self.media.tmp.name);c.resource_settings({'mixedChunkSeconds':10,'mixedOverlapSeconds':1})
        path=Path(self.media.tmp.name)/'settings'/'audio-resources.json';before=path.read_bytes()
        for update in [[],['mixedChunkSeconds'],'invalid',42,False,{'cacheRoot':'arbitrary'},{'mixedChunkSeconds':'20'},{'mixedOverlapSeconds':True},{'maxDecodedBytes':1.5},{'device':[]},{'mixedChunkSeconds':2},{'mixedOverlapSeconds':5}]:
            with self.subTest(update=update):
                with self.assertRaises(CutError) as caught:c.resource_settings(update)
                self.assertEqual(caught.exception.code,'INVALID_AUDIO_SETTINGS')
                self.assertEqual(path.read_bytes(),before)
        self.assertEqual(Coordinator(self.media.tmp.name).resource_settings()['settings']['mixedOverlapSeconds'],1)
    def test_actual_running_ffmpeg_resource_limit_and_cancel_reap_owned_decoder(self):
        import subprocess,time
        native=subprocess.Popen
        for budget,seconds,code in [(4,None,'AUDIO_RESOURCE_LIMIT'),(10*1024*1024,.15,'CANCELED')]:
            children=[]
            def spawn(*a,**kw):
                child=native(*a,**kw);children.append(child);return child
            began=time.monotonic()
            cancel=(lambda:time.monotonic()-began>seconds) if seconds else None
            with patch('contentrium_cut.audio.subprocess.Popen',side_effect=spawn), tempfile.TemporaryFile() as output:
                with self.assertRaises(CutError) as caught:self.media.audio._run([fixtures.FFMPEG,'-v','error','-nostdin','-re','-f','lavfi','-i','anullsrc=r=16000:cl=mono','-f','f32le','pipe:1'],cancel,stdout=output,max_output_bytes=budget)
            self.assertEqual(caught.exception.code,code);self.assertEqual(len(children),1);self.assertIsNotNone(children[0].poll())
