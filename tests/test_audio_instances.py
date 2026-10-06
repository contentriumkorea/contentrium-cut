import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_audio as media_fixtures
import test_coordinator as coordinator_fixtures
from contentrium_cut.audio import analyze_audio
from contentrium_cut.coordinator import Coordinator
from contentrium_cut.contract import canonical_hash, TICKS_PER_SECOND


class ActiveVAD:
    revision = 'fixture-vad'
    def probabilities(self, samples, cancel=None):
        return [(i, min(i + 512, len(samples)), .95) for i in range(0, len(samples), 512)]


class InstanceTests(unittest.TestCase):
    def setUp(self):
        self.media = media_fixtures.AudioTests(); self.media.setUp(); self.addCleanup(self.media.doCleanups)

    def bind_repeated(self, second_start=12, second_in=11):
        snapshot = coordinator_fixtures.CoordinatorTests().fixture(); second = copy.deepcopy(snapshot['clips'][0])
        second.update(instanceKey='mic2', startTicks=str(second_start * TICKS_PER_SECOND),
                      endTicks=str((second_start + 10) * TICKS_PER_SECOND),
                      inTicks=str(second_in * TICKS_PER_SECOND), outTicks=str((second_in + 10) * TICKS_PER_SECOND))
        snapshot['clips'].append(second); snapshot.pop('snapshotHash'); snapshot['snapshotHash'] = canonical_hash(snapshot)
        coordinator = Coordinator(self.media.tmp.name); coordinator.bind('p', snapshot)
        return coordinator

    def test_equal_offset_distinct_instance_trims_do_not_collapse(self):
        payload = self.bind_repeated().audio_payload('p', {'mode': 'separate', 'microphones': [
            {'instanceKey': 'mic', 'speakerId': 'A'}, {'instanceKey': 'mic2', 'speakerId': 'A'}]})
        self.assertEqual(len(payload['sources']), 2)
        self.assertEqual([r['sourceStartSeconds'] for r in payload['sources']], [1., 11.])
        self.assertEqual([r['instanceKey'] for r in payload['sources']], ['mic', 'mic2'])

    def test_replayed_asset_instances_keep_distinct_timeline_offsets(self):
        payload = self.bind_repeated(22, 1).audio_payload('p', {'mode': 'separate', 'microphones': [
            {'instanceKey': 'mic', 'speakerId': 'A'}, {'instanceKey': 'mic2', 'speakerId': 'A'}]})
        self.assertEqual([r['sequenceStartSeconds'] for r in payload['sources']], [2., 22.])
        self.assertEqual(len(set(r['inputKey'] for r in payload['sources'])), 2)

    def test_same_speaker_split_recordings_preserve_activity_and_gap(self):
        first = self.media.source('a', self.media.signal(2)); second = self.media.source('b', self.media.signal(2))
        first.update(instanceKey='part1', inputKey='part1', sequenceStartSeconds=0.)
        second.update(instanceKey='part2', inputKey='part2', sequenceStartSeconds=4.)
        settings = {'fps': media_fixtures.FPS, 'range': {'startFrame': 0, 'endFrame': 180}, 'channels': [
            {'assetId': 'a', 'inputKey': 'part1', 'speakerId': 'A'},
            {'assetId': 'b', 'inputKey': 'part2', 'speakerId': 'A'}]}
        with patch('contentrium_cut.audio.SileroVAD', return_value=ActiveVAD()):
            result = analyze_audio('separate', [first, second], settings)
        self.assertEqual(result['intervals'], [
            {'startFrame': 0, 'endFrame': 60, 'speakers': ['A'], 'unknown': False},
            {'startFrame': 120, 'endFrame': 180, 'speakers': ['A'], 'unknown': False}])
        self.assertEqual([r['instanceKey'] for r in result['validAudioRanges']], ['part1', 'part2'])
    def test_bleed_calibration_does_not_cross_microphone_segment_replacement(self):
        import numpy as np
        x,y=self.media.signal(2),self.media.signal(2)
        a=self.media.source('a',x);a.update(inputKey='first',sequenceStartSeconds=0.)
        b=self.media.source('b',y);b.update(inputKey='second',sequenceStartSeconds=2.)
        other=self.media.source('other',np.concatenate((.1*x,.1*y))+.01*self.media.signal(4));other['inputKey']='other'
        settings={'fps':media_fixtures.FPS,'range':{'startFrame':0,'endFrame':120},'channels':[{'assetId':'a','inputKey':'first','speakerId':'A'},{'assetId':'b','inputKey':'second','speakerId':'A'},{'assetId':'other','inputKey':'other','speakerId':'B'}],'calibration':[{'speakerId':'A','startFrame':0,'endFrame':60}]}
        with patch('contentrium_cut.audio.SileroVAD',return_value=ActiveVAD()):result=analyze_audio('separate',[a,b,other],settings)
        self.assertTrue(all(r['speakers']==['A'] and not r['unknown'] for r in result['intervals'] if r['endFrame']<=60))
        later=[r for r in result['intervals'] if r['endFrame']>60]
        self.assertTrue(later and all(r['unknown'] for r in later))
    def test_selected_mov_keeps_leading_audio_pts_gap_and_seek_does_not_double_pts(self):
        import subprocess
        wav=self.media.source('original',self.media.signal(3));movie=Path(self.media.tmp.name)/'delayed.mov'
        subprocess.run([media_fixtures.FFMPEG,'-v','error','-f','lavfi','-i','color=c=black:s=16x16:r=30','-itsoffset','0.5','-i',wav['path'],'-t','3.5','-map','0:v','-map','1:a','-c:v','mpeg4','-c:a','pcm_s16le',str(movie)],check=True,capture_output=True)
        source=dict(wav,path=str(movie),assetId='delayed',inputKey='delayed',sourceStartSeconds=0.,durationSeconds=3.,sequenceStartSeconds=0.)
        settings={'fps':media_fixtures.FPS,'range':{'startFrame':0,'endFrame':90},'channels':[{'assetId':'delayed','inputKey':'delayed','speakerId':'A'}]}
        with patch('contentrium_cut.audio.SileroVAD',return_value=ActiveVAD()):result=analyze_audio('separate',[source],settings)
        self.assertEqual(result['intervals'],[{'startFrame':15,'endFrame':90,'speakers':['A'],'unknown':False}])
        self.assertEqual(result['sourceInputs'][0]['sourceStartSeconds'],.5)
        info=self.media.audio._decode(dict(source,sourceStartSeconds=1.,durationSeconds=2.),self.media.tmp.name,'seek')
        try:self.assertAlmostEqual(info['origin'],1.);self.assertEqual(len(info['samples']),32000)
        finally:info['samples']._mmap.close()
