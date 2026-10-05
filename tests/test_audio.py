import hashlib
import importlib
import os
import subprocess
import sys
import time
import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

FFMPEG = r'C:\Program Files (x86)\Common Files\AutoPod\ffmpeg\bin\ffmpeg.exe'
FPS = {'num': 30, 'den': 1}


class AudioTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.audio = importlib.import_module('contentrium_cut.audio')
        self.rng = np.random.default_rng(44)

    def source(self, name, samples, channel=0):
        path = Path(self.tmp.name) / (name + '.wav')
        samples = np.asarray(samples)
        with wave.open(str(path), 'wb') as f:
            f.setnchannels(1 if samples.ndim == 1 else samples.shape[1])
            f.setsampwidth(2)
            f.setframerate(16000)
            f.writeframes((np.clip(samples, -1, 1) * 30000).astype('<i2').tobytes())
        return {'assetId': name, 'path': str(path), 'channelIndex': channel, 'ffmpeg': FFMPEG}

    def signal(self, seconds):
        # Aperiodic broadband common audio with a changing amplitude envelope.
        x = self.rng.normal(0, .15, round(seconds * 16000))
        return x * (.5 + .4 * np.sin(np.arange(len(x)) / 6000) ** 2)

    def test_offset_distributed_evidence_and_source_preservation(self):
        x = self.signal(15)
        a = self.source('a', x)
        b = self.source('b', x[32000:])
        digest = hashlib.sha256(Path(b['path']).read_bytes()).hexdigest()
        plan = self.audio.sync_sources([a, b], 'a', FPS)
        self.assertAlmostEqual(plan['offsets']['b'], 2, delta=1 / 30)
        self.assertEqual(plan['sources']['b']['status'], 'accepted')
        self.assertGreaterEqual(len(plan['edges'][0]['windows']), 3)
        self.assertEqual(hashlib.sha256(Path(b['path']).read_bytes()).hexdigest(), digest)

    def test_indirect_overlap_connects_disjoint_reference(self):
        x = self.signal(24)
        sources = [self.source('a', x[:160000]), self.source('b', x[80000:304000]),
                   self.source('c', x[224000:])]
        plan = self.audio.sync_sources(sources, 'a', FPS)
        self.assertAlmostEqual(plan['offsets']['c'], 14, delta=1 / 30)
        self.assertEqual(plan['sources']['c']['path'], ['a', 'b', 'c'])

    def test_drift_not_accepted_as_offset(self):
        from scipy.signal import resample
        x = self.signal(30)
        y = resample(x, round(len(x) * 1.01))
        plan = self.audio.sync_sources([self.source('a', x), self.source('b', y)], 'a', FPS)
        self.assertNotEqual(plan['sources']['b']['status'], 'accepted')
        self.assertTrue(any(r['code'] in ('SYNC_DRIFT', 'SYNC_UNRESOLVED') for r in plan['reviews']))

    def test_silence_is_unresolved_not_perfect_sync(self):
        plan = self.audio.sync_sources([self.source('a', np.zeros(160000)),
                                        self.source('b', np.zeros(160000))], 'a', FPS)
        self.assertEqual(plan['sources']['b']['status'], 'unresolved')
        self.assertNotIn('b', plan['offsets'])

    def test_graph_conflict_invalidates_descendant_path(self):
        from unittest.mock import patch
        values=iter([1.,8.,None,1.,None,1.])
        def edge(*args):
            offset=next(values)
            return None if offset is None else {'offsetSeconds':offset,'status':'accepted','windows':[],
                  'uncertaintySeconds':.001,'driftSlope':0.,'maxResidualSeconds':0.}
        sources=[self.source(s,self.signal(2)) for s in ('a','b','c','d')]
        with patch('contentrium_cut.audio._pair',side_effect=edge):
            plan=self.audio.sync_sources(sources,'a',FPS)
        self.assertNotEqual(plan['sources']['d']['status'],'accepted')
        self.assertNotIn('d',plan['offsets'])

    def test_missing_audio_is_blocking(self):
        from contentrium_cut.contract import CutError
        with self.assertRaises(CutError) as cm:
            self.audio.sync_sources([{'assetId': 'a', 'path': 'does-not-exist.wav'}], 'a', FPS)
        self.assertEqual(cm.exception.code, 'MISSING_AUDIO')

    def test_cancellation_blocks_decode(self):
        from contentrium_cut.contract import CutError
        with self.assertRaises(CutError) as cm:
            self.audio.sync_sources([self.source('a', self.signal(2))], 'a', FPS, lambda: True)
        self.assertEqual(cm.exception.code, 'CANCELED')

    def test_regular_mixed_overlap_and_session_ids(self):
        # Engine dependency is injected only at model boundary; PCM and timeline are real.
        from unittest.mock import patch
        class Engine:
            revision = 'fixture-regular'
            def turns(self, path, speaker_count=None, cancel=None):
                return [(0., 2., 'SPEAKER_07'), (1., 3., 'SPEAKER_02')]
        a = self.source('a', self.signal(4))
        with patch('contentrium_cut.audio.CommunityDiarizer', return_value=Engine()):
            result = self.audio.analyze_audio('mixed', [a], {'fps': FPS, 'range': {'startFrame': 0, 'endFrame': 120}})
        self.assertEqual(result['sessionSpeakerIds'], ['A', 'B'])
        self.assertEqual(result.get('evidence',{}).get('sourceIntervals',[]),[
            {'startSample':0,'endSample':32000,'localSpeakerId':'SPEAKER_07','sessionSpeakerId':'A'},
            {'startSample':16000,'endSample':48000,'localSpeakerId':'SPEAKER_02','sessionSpeakerId':'B'}])
        self.assertIn({'startFrame': 30, 'endFrame': 60, 'speakers': ['A', 'B'], 'unknown': False}, result['intervals'])
        self.assertEqual(result['intervals'][-1]['speakers'], [])

    def test_separate_bleed_calibration_and_true_overlap(self):
        from unittest.mock import patch
        # A-only, B-only calibration and independent simultaneous signals.
        x, y = self.signal(4), self.signal(4)
        left = np.concatenate([x, .1*y, x])
        right = np.concatenate([.1*x, y, y])
        a, b = self.source('a', left), self.source('b', right)
        class VAD:
            revision = 'fixture-vad'
            def probabilities(self, samples, cancel=None):
                return [(i, min(i+512, len(samples)), .95) for i in range(0, len(samples), 512)]
        settings = {'fps': FPS, 'range': {'startFrame': 0, 'endFrame': 360},
                    'channels': [{'assetId': 'a', 'speakerId': 'A'}, {'assetId': 'b', 'speakerId': 'B'}],
                    'calibration': [{'speakerId': 'A', 'startFrame': 0, 'endFrame': 120},
                                    {'speakerId': 'B', 'startFrame': 120, 'endFrame': 240}]}
        with patch('contentrium_cut.audio.SileroVAD', return_value=VAD()):
            result = self.audio.analyze_audio('separate', [a, b], settings)
        self.assertEqual(result['intervals'][0]['speakers'], ['A'])
        self.assertTrue(any(i['speakers'] == ['A', 'B'] for i in result['intervals'] if i['startFrame'] >= 240))
        self.assertTrue(any(r['code'] == 'BLEED_SUPPRESSED' for r in result['reviews']))
        self.assertTrue(all(r.get('speechSampleRanges') for r in result['evidence']))

    def test_duplicate_channels_not_silently_removed(self):
        from unittest.mock import patch
        x = self.signal(3)
        class VAD:
            revision = 'fixture-vad'
            def probabilities(self, samples, cancel=None):
                return [(i, min(i+512, len(samples)), .95) for i in range(0, len(samples), 512)]
        settings = {'fps': FPS, 'range': {'startFrame': 0, 'endFrame': 90},
                    'channels': [{'assetId': 'a', 'speakerId': 'A'}, {'assetId': 'b', 'speakerId': 'B'}]}
        with patch('contentrium_cut.audio.SileroVAD', return_value=VAD()):
            result = self.audio.analyze_audio('separate', [self.source('a', x), self.source('b', -x*.5)], settings)
        self.assertTrue(any(r['code'] == 'DUPLICATE_CHANNELS' for r in result['reviews']))
        self.assertTrue(any(i['unknown'] for i in result['intervals']))

    def test_missing_model_is_actionable(self):
        from contentrium_cut.contract import CutError
        with self.assertRaises(CutError) as cm:
            self.audio.analyze_audio('mixed', [self.source('a', self.signal(2))], {'fps': FPS, 'modelRoot': self.tmp.name})
        self.assertEqual(cm.exception.code, 'MODEL_NOT_READY')
        self.assertIn('setup', cm.exception.details)

    def test_missing_channel_coverage_is_unknown_not_silence(self):
        from unittest.mock import patch
        class VAD:
            revision='fixture-silent'
            def probabilities(self,samples,cancel=None):
                return [(i,min(i+512,len(samples)),.01) for i in range(0,len(samples),512)]
        a=self.source('a',np.zeros(16000*4)); b=self.source('b',np.zeros(16000*2))
        settings={'fps':FPS,'range':{'startFrame':0,'endFrame':120},'offsets':{'b':2},
                  'channels':[{'assetId':'a','speakerId':'A'},{'assetId':'b','speakerId':'B'}]}
        with patch('contentrium_cut.audio.SileroVAD',return_value=VAD()):
            result=self.audio.analyze_audio('separate',[a,b],settings)
        self.assertTrue(result['intervals'][0]['unknown'])
        self.assertFalse(result['intervals'][-1]['unknown'])
        self.assertTrue(any(r['code']=='AUDIO_COVERAGE_GAP' for r in result['reviews']))

    def test_streamed_ranged_decode_keeps_sample_origin(self):
        source=self.source('a',self.signal(4))
        with tempfile.TemporaryDirectory() as directory:
            info=self.audio._decode(source,directory,0,start=1.25,duration=.5)
            try:
                self.assertEqual(len(info['samples']),8000)
                self.assertAlmostEqual(info['origin'],1.25)
            finally: info['samples']._mmap.close()

    def test_decoder_metadata_capture_cannot_deadlock_on_full_pipe(self):
        from contentrium_cut.contract import CutError
        started=time.monotonic()
        try:
            result=self.audio._run([sys.executable,'-c',"import sys;sys.stdout.write('x'*200000)"],lambda:time.monotonic()-started>2)
        except CutError as e:
            self.fail('Bounded child output capture canceled instead of completing: '+e.code)
        self.assertEqual(len(result),200000)

    def test_overlap_membership_changes_at_actual_boundary(self):
        valid=[{'startFrame':0,'endFrame':120}]
        result=self.audio._intervals([(0,2,'A'),(1,3,'B'),(2,4,'C')],valid,FPS,{'startFrame':0,'endFrame':120})
        self.assertEqual([r['speakers'] for r in result],[['A'],['A','B'],['B','C'],['C']])

    def test_mixed_explicit_channel_selection_preserves_opposite_polarity_signal(self):
        x = self.signal(2)
        source = self.source('opposite-selected', np.column_stack([x, -x]), channel=0)
        before = hashlib.sha256(Path(source['path']).read_bytes()).hexdigest()
        info = self.audio._decode(source, self.tmp.name, 'selected', mixed=True)
        try:
            self.assertEqual(len(info['samples']), 32000)
            self.assertGreater(float(np.sqrt(np.mean(np.square(info['samples'], dtype=np.float64)))), .03)
        finally:
            info['samples']._mmap.close()
        self.assertEqual(hashlib.sha256(Path(source['path']).read_bytes()).hexdigest(), before)

    def test_mixed_unselected_opposite_channels_block_instead_of_confirming_silence(self):
        from contentrium_cut.contract import CutError
        from unittest.mock import patch
        class EmptyEngine:
            revision = 'fixture-empty'
            def turns(self, path, speaker_count=None, cancel=None):
                return []
        x = self.signal(2)
        source = self.source('opposite-unselected', np.column_stack([x, -x]))
        source.pop('channelIndex')
        with patch('contentrium_cut.audio.CommunityDiarizer', return_value=EmptyEngine()):
            with self.assertRaises(CutError) as caught:
                self.audio.analyze_audio('mixed', [source], {'fps': FPS})
        self.assertEqual(caught.exception.code, 'AUDIO_CHANNEL_SELECTION_REQUIRED')

    def test_mixed_unselected_distinct_channels_require_a_selection(self):
        from contentrium_cut.contract import CutError
        source = self.source('distinct-unselected', np.column_stack([self.signal(2), self.signal(2)]))
        source.pop('channelIndex')
        with self.assertRaises(CutError) as caught:
            self.audio._decode(source, self.tmp.name, 'distinct', mixed=True)
        self.assertEqual(caught.exception.code, 'AUDIO_CHANNEL_SELECTION_REQUIRED')

    def test_mixed_unselected_compatible_channels_keep_signal_and_duration(self):
        x = self.signal(2)
        source = self.source('compatible-unselected', np.column_stack([x, x]))
        source.pop('channelIndex')
        info = self.audio._decode(source, self.tmp.name, 'compatible', mixed=True)
        try:
            self.assertEqual(len(info['samples']), 32000)
            self.assertGreater(float(np.sqrt(np.mean(np.square(info['samples'], dtype=np.float64)))), .03)
        finally:
            info['samples']._mmap.close()

    def test_partial_bleed_calibration_does_not_confirm_uncalibrated_overlap(self):
        from unittest.mock import patch
        class VAD:
            revision = 'fixture-vad'
            def probabilities(self, samples, cancel=None):
                return [(i, min(i+512, len(samples)), .95) for i in range(0, len(samples), 512)]
        x, y = self.signal(4), self.signal(4)
        a = self.source('partial-a', np.concatenate([x, .1*y]))
        b = self.source('partial-b', np.concatenate([.1*x, y]))
        settings = {'fps': FPS, 'range': {'startFrame': 0, 'endFrame': 240},
                    'channels': [{'assetId': a['assetId'], 'speakerId': 'A'}, {'assetId': b['assetId'], 'speakerId': 'B'}],
                    'calibration': [{'speakerId': 'A', 'startFrame': 0, 'endFrame': 120}]}
        with patch('contentrium_cut.audio.SileroVAD', return_value=VAD()):
            result = self.audio.analyze_audio('separate', [a, b], settings)
        self.assertTrue(all(not row['unknown'] and row['speakers'] == ['A'] for row in result['intervals'] if row['endFrame'] <= 120))
        last = [row for row in result['intervals'] if row['endFrame'] > 120]
        self.assertTrue(last and all(row['unknown'] for row in last))
        self.assertTrue(any(r['code'] == 'BLEED_CALIBRATION_REQUIRED' and r.get('startFrame') == 120 and r.get('endFrame') == 240 and 'B' in r.get('speakers', []) for r in result['reviews']))

    @unittest.skipUnless(os.environ.get('CONTENTRIUM_TEST_MODEL_ROOT'), 'Explicit verified local model fixture required')
    def test_real_silero_korean_sapi_speech_and_silence(self):
        # Windows' local Korean TTS creates a reproducible speech fixture; no network or ASR.
        speech=Path(self.tmp.name)/'korean-sapi.wav'
        env=dict(os.environ,CONTENTRIUM_FIXTURE_WAV=str(speech))
        script="Add-Type -AssemblyName System.Speech; $s=New-Object System.Speech.Synthesis.SpeechSynthesizer; $s.SelectVoice('Microsoft Heami Desktop'); $s.SetOutputToWaveFile($env:CONTENTRIUM_FIXTURE_WAV); $s.Speak('안녕하세요. 이것은 로컬 음성 감지 시험입니다. 원본 녹음은 이 컴퓨터에 보존됩니다.'); $s.Dispose()"
        subprocess.run(['powershell.exe','-NoProfile','-Command',script],env=env,check=True,capture_output=True)
        info=self.audio._decode({'assetId':'tts','path':str(speech),'ffmpeg':FFMPEG},self.tmp.name,'tts')
        try: samples=np.concatenate((np.zeros(2*16000),np.array(info['samples']),np.zeros(2*16000)))
        finally: info['samples']._mmap.close()
        source=self.source('padded-korean',samples)
        result=self.audio.analyze_audio('separate',[source],{'fps':FPS,'ffmpeg':FFMPEG,
                         'modelRoot':os.environ['CONTENTRIUM_TEST_MODEL_ROOT'],
                         'channels':[{'assetId':source['assetId'],'speakerId':'A'}]})
        self.assertTrue(any(r['speakers']==['A'] for r in result['intervals']))
        self.assertEqual(result['intervals'][0]['speakers'],[])
        self.assertGreaterEqual(result['intervals'][0]['endFrame'],45)
        self.assertEqual(result['intervals'][-1]['speakers'],[])
        print('REAL_SILERO_KOREAN_SAPI',{'modelRevision':result['modelRevision'],'durationSamples':len(samples),
             'speechFrames':sum(r['endFrame']-r['startFrame'] for r in result['intervals'] if r['speakers']),
             'intervalCount':len(result['intervals'])})


if __name__ == '__main__':
    unittest.main()
