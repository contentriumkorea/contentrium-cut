import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch
import numpy as np
import test_audio as fixtures
from contentrium_cut.audio import analyze_audio
from contentrium_cut.coordinator import Coordinator
from contentrium_cut.contract import CutError,canonical_hash,TICKS_PER_SECOND

class ExampleTests(unittest.TestCase):
    def test_real_mixed_solo_preview_is_bound_to_original_hash_range_and_revision(self):
        media=fixtures.AudioTests();media.setUp();self.addCleanup(media.doCleanups)
        source=media.source('a',media.signal(3));source.update(inputKey='mic',instanceKey='mic',sequenceStartSeconds=0.,sourceStartSeconds=0.,durationSeconds=3.)
        class Engine:
            revision='adapter-fixture'
            def turns(self,*args):return [(0,1,'first'),(1,2,'second'),(2,3,'first')]
        with patch('contentrium_cut.audio.CommunityDiarizer',return_value=Engine()):raw=analyze_audio('mixed',[source],{'fps':fixtures.FPS})
        snapshot={'schemaVersion':1,'projectRef':'p','sequenceRef':'s','fps':fixtures.FPS,'range':{'startFrame':0,'endFrame':90},'sources':[{'assetId':'a','canonicalPath':source['path'],'offline':False}],'clips':[{'assetId':'a','instanceKey':'mic','startTicks':'0','endTicks':str(3*TICKS_PER_SECOND),'inTicks':'0','outTicks':str(3*TICKS_PER_SECOND),'speed':1,'disabled':False}]}
        snapshot['snapshotHash']=canonical_hash(snapshot)
        with tempfile.TemporaryDirectory() as directory:
            c=Coordinator(directory);c.bind('panel',snapshot);state=c.register_analysis('panel','job',raw)
            examples=state['examples'];self.assertEqual({e['speakerId'] for e in examples},{'A','B'})
            example=next(e for e in examples if e['speakerId']=='B');rendered=c.render_example('panel',state['analysisId'],example['exampleId'])
            with wave.open(rendered['path']) as f:self.assertEqual(f.getnframes(),16000);self.assertEqual(f.getnchannels(),1)
            state=c.correct_analysis('panel',state['analysisId'],0,{'type':'merge','speakerIds':['A','B'],'targetSpeakerId':'A'})
            self.assertNotIn(example['exampleId'],[e['exampleId'] for e in state['examples']])
            Path(source['path']).write_bytes(b'replaced media')
            with self.assertRaises(CutError) as caught:c.render_example('panel',state['analysisId'],state['examples'][0]['exampleId'])
            self.assertEqual(caught.exception.code,'SOURCE_CHANGED')
    def test_chunked_engine_output_is_cache_valid_and_unknown_candidate_has_solo_example(self):
        from contentrium_cut.cache import validate_result
        media=fixtures.AudioTests();media.setUp();self.addCleanup(media.doCleanups)
        a=media.source('a',media.signal(2));a.update(inputKey='a',sequenceStartSeconds=0.)
        b=media.source('b',media.signal(2));b.update(inputKey='b',sequenceStartSeconds=4.)
        class Engine:
            revision='adapter-fixture'
            def turns(self,*args):return [(0,2,'same-label')]
        with patch('contentrium_cut.audio.CommunityDiarizer',return_value=Engine()):raw=analyze_audio('mixed',[a,b],{'fps':fixtures.FPS})
        validate_result('analysis',raw)
        from contentrium_cut.corrections import solo_examples
        examples=solo_examples(raw,fixtures.FPS)
        self.assertTrue(any(e['candidateSpeakerId']=='U1' and e['speakerId'] is None for e in examples))
    def test_unknown_candidate_examples_stay_inside_immutable_analysis_range(self):
        from contentrium_cut.corrections import solo_examples
        raw={'intervals':[{'startFrame':0,'endFrame':30,'speakers':['A'],'unknown':False}],'validAudioRanges':[{'assetId':'a','startFrame':0,'endFrame':150}], 'sourceInputs':[{'assetId':'a','sessionOriginSeconds':0,'sourceStartSeconds':0,'sha256':'a'*64}],'evidence':{'ownedTurns':[{'startFrame':90,'endFrame':150,'candidateSpeakerId':'U1','sessionSpeakerId':None}]}}
        self.assertFalse(any(e['candidateSpeakerId'] for e in solo_examples(raw,fixtures.FPS)))
    def test_candidate_from_two_context_overlap_disagreement_has_no_solo_example(self):
        from contentrium_cut.audio import _intervals
        from contentrium_cut.corrections import solo_examples
        from contentrium_cut.mixed_chunks import diarize_chunks
        class Engine:
            revision='two-context-fixture'
            def __init__(self):self.results=iter([[(0,5,'one'),(3.5,4.9,'other')],[(1,1.6,'new-label')]])
            def turns(self,*args):return next(self.results)
        with tempfile.TemporaryDirectory() as directory:
            pcm=Path(directory)/'a.pcm';np.zeros(8*16000,dtype='<f4').tofile(pcm)
            source={'assetId':'a','inputKey':'a','path':'unused'}
            info={'samples':np.memmap(pcm,dtype='<f4',mode='r'),'pcmPath':str(pcm),'sessionOrigin':0,'origin':0,'sampleRate':48000,'channelSelection':'explicit'}
            try:
                turns,valid,reviews,unknown,evidence=diarize_chunks([source],[info],Engine(),{'mixedChunkSeconds':6,'mixedOverlapSeconds':1},fixtures.FPS,directory)
                self.assertEqual([(r['startFrame'],r['endFrame']) for r in evidence['boundaryConflicts']],[(90,150)])
                self.assertEqual([(r['startFrame'],r['endFrame']) for r in evidence['ownedTurns'] if r['candidateSpeakerId']=='U1'],[(120,138)])
                raw={'intervals':_intervals(turns,valid,fixtures.FPS,{'startFrame':0,'endFrame':240},unknown),'validAudioRanges':valid,'sourceInputs':[dict(source,sessionOriginSeconds=0,sourceStartSeconds=0,sha256='a'*64)],'evidence':evidence}
                self.assertEqual([e for e in solo_examples(raw,fixtures.FPS) if e['candidateSpeakerId']=='U1'],[])
            finally:info['samples']._mmap.close()
    def test_candidate_evidence_is_split_before_minimum_solo_duration_is_applied(self):
        from contentrium_cut.corrections import solo_examples
        for conflict_start,conflict_end,want in [(30,60,[(0,30),(60,90)]),(15,75,[(0,15),(75,90)]),(14,76,[])]:
            with self.subTest(conflict=(conflict_start,conflict_end)):
                raw={'intervals':[{'startFrame':0,'endFrame':90,'speakers':[],'unknown':True,'reason':'CHANNEL_IDENTITY_UNCERTAIN'}],'validAudioRanges':[{'assetId':'a','startFrame':0,'endFrame':90}], 'sourceInputs':[{'assetId':'a','sessionOriginSeconds':0,'sourceStartSeconds':0,'sha256':'a'*64}],'evidence':{'ownedTurns':[{'startFrame':0,'endFrame':90,'candidateSpeakerId':'U1','sessionSpeakerId':None}],'boundaryConflicts':[{'startFrame':conflict_start,'endFrame':conflict_end}]}}
                examples=solo_examples(raw,fixtures.FPS)
                self.assertEqual([(e['startFrame'],e['endFrame']) for e in examples],want)
                self.assertTrue(all(e['speakerId'] is None and e['candidateSpeakerId']=='U1' for e in examples))
