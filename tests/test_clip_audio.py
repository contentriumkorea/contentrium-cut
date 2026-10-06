import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch
import numpy as np
from contentrium_cut.audio import analyze_audio
from contentrium_cut.contract import canonical_hash
from contentrium_cut.coordinator import Coordinator

class SpeakingVAD:
    revision='injected-speaking-boundary'
    def __init__(self,manager):pass
    def probabilities(self,samples,cancel=None):
        for first in range(0,len(samples),512):yield first,min(first+512,len(samples)),.99

class ClipAudioTests(unittest.TestCase):
    def test_real_ffmpeg_trim_keeps_analysis_within_selected_timeline_clip(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'input.wav'
            with wave.open(str(path),'wb') as f:
                f.setnchannels(1);f.setsampwidth(2);f.setframerate(16000);f.writeframes((np.sin(np.arange(240000)*.07)*10000).astype('<i2').tobytes())
            s={'schemaVersion':1,'projectRef':'p','sequenceRef':'s','fps':{'num':30,'den':1},'range':{'startFrame':0,'endFrame':450},'tracks':[],'clips':[{'instanceKey':'mic','assetId':'a','mediaType':'audio','startTicks':'508032000000','endTicks':'3048192000000','inTicks':'254016000000','outTicks':'2794176000000','speed':1,'disabled':False}],'sources':[{'assetId':'a','canonicalPath':str(path)}]}
            s['snapshotHash']=canonical_hash(s);c=Coordinator(d);c.bind('panel',s);payload=c.audio_payload('panel',{'mode':'separate','microphones':[{'instanceKey':'mic','speakerId':'A'}]})
            payload['settings']['ffmpeg']=r'C:\Program Files (x86)\Common Files\AutoPod\ffmpeg\bin\ffmpeg.exe'
            # Match the worker's DSP call; media identity belongs to its job envelope.
            with patch('contentrium_cut.audio.SileroVAD',SpeakingVAD):result=analyze_audio(payload['mode'],payload['sources'],payload['settings'])
            valid=result['validAudioRanges'][0];self.assertEqual((valid['startFrame'],valid['endFrame']),(60,360));self.assertEqual(valid['endSample'],160000)
            for interval in result['intervals']:self.assertGreaterEqual(interval['startFrame'],60);self.assertLessEqual(interval['endFrame'],360)
