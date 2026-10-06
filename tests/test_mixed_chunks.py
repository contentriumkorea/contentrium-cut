import tempfile
import unittest
from pathlib import Path
import numpy as np
from scipy.io import wavfile
from contentrium_cut.mixed_chunks import diarize_chunks

class ChunkTests(unittest.TestCase):
    def fixture(self, directory, seconds=12, origin=0, asset='a'):
        path=Path(directory)/(asset+'.pcm');np.zeros(seconds*16000,dtype='<f4').tofile(path)
        return {'assetId':asset,'inputKey':asset,'path':'unused'}, {'samples':np.memmap(path,dtype='<f4',mode='r'),'pcmPath':str(path),'sessionOrigin':origin,'origin':0,'sampleRate':48000,'channelSelection':'explicit'}
    def test_overlap_evidence_links_changed_local_labels_and_owns_each_sample_once(self):
        class Engine:
            revision='fake';calls=0
            def turns(self,path,speaker_count=None,cancel=None):
                rate,x=wavfile.read(path);self.calls+=1
                self_outer.assertLessEqual(len(x),6*16000)
                return [(0,len(x)/rate,'old' if self.calls==1 else 'different')]
        self_outer=self
        with tempfile.TemporaryDirectory() as d:
            source,info=self.fixture(d)
            try:
                turns,valid,reviews,unknown,evidence=diarize_chunks([source],[info],Engine(),{'mixedChunkSeconds':6,'mixedOverlapSeconds':1}, {'num':30,'den':1},d)
                self.assertEqual({s for a,b,s in turns},{'A'})
                self.assertEqual(sum(b-a for a,b,s in turns),12)
                self.assertFalse(unknown);self.assertFalse(reviews)
                self.assertEqual(len(evidence['chunks']),3)
            finally:info['samples']._mmap.close()
    def test_disjoint_recording_never_merges_by_same_local_label(self):
        class Engine:
            revision='fake'
            def turns(self,path,speaker_count=None,cancel=None):return [(0,2,'SPEAKER_00')]
        with tempfile.TemporaryDirectory() as d:
            a,ia=self.fixture(d,2,0,'a');b,ib=self.fixture(d,2,4,'b')
            try:
                turns,valid,reviews,unknown,evidence=diarize_chunks([a,b],[ia,ib],Engine(),{}, {'num':30,'den':1},d)
                self.assertEqual({s for _,_,s in turns},{'A'})
                self.assertEqual(unknown,[(120,180)])
                self.assertTrue(any(r['code']=='CHUNK_IDENTITY_UNRESOLVED' for r in reviews))
                self.assertEqual(evidence['ownedTurns'][-1]['candidateSpeakerId'],'U1')
            finally:ia['samples']._mmap.close();ib['samples']._mmap.close()
    def test_boundary_membership_conflict_preserves_review_on_both_owned_sides(self):
        class Engine:
            revision='fake';calls=0
            def turns(self,path,speaker_count=None,cancel=None):
                rate,x=wavfile.read(path);self.calls+=1
                result=[(0,len(x)/rate,'one')]
                if self.calls==2:result.append((0,2,'other'))
                return result
        with tempfile.TemporaryDirectory() as d:
            source,info=self.fixture(d,8)
            try:
                turns,valid,reviews,unknown,evidence=diarize_chunks([source],[info],Engine(),{'mixedChunkSeconds':6,'mixedOverlapSeconds':1},{'num':30,'den':1},d)
                self.assertIn((90,150),unknown)
                self.assertTrue(any(r['code']=='CHUNK_BOUNDARY_CONFLICT' and r['startFrame']==90 for r in reviews))
            finally:info['samples']._mmap.close()
