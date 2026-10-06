import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from scipy.io import wavfile
from contentrium_cut.models import CommunityDiarizer
from contentrium_cut.contract import CutError

class ModelResourceTests(unittest.TestCase):
    def test_cuda_oom_retries_cpu_and_reports_fallback_preserving_overlap(self):
        class Pipeline:
            calls=0;devices=[]
            def to(self,device):self.devices.append(str(device))
            def __call__(self,audio,**kwargs):
                self.calls+=1
                if self.calls==1:raise RuntimeError('CUDA out of memory')
                rows=[(SimpleNamespace(start=0,end=1),None,'one'),(SimpleNamespace(start=.5,end=1),None,'two')]
                return SimpleNamespace(speaker_diarization=SimpleNamespace(itertracks=lambda **kwargs:iter(rows)))
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'chunk.wav';wavfile.write(path,16000,np.zeros(16000,dtype=np.int16))
            model=object.__new__(CommunityDiarizer);model.pipeline=Pipeline()
            with patch('torch.cuda.is_available',return_value=True),patch('torch.cuda.mem_get_info',return_value=(2**32,2**33)),patch('torch.cuda.empty_cache'):
                model.configure({'device':'cuda','mixedChunkSeconds':1,'modelMemoryReserveBytes':1024})
                rows=model.turns(path)
            self.assertEqual(rows,[(0.,1.,'one'),(.5,1.,'two')]);self.assertEqual(model.pipeline.devices,['cuda','cpu']);self.assertEqual(model.reviews[-1]['code'],'DEVICE_FALLBACK')
    def test_oversized_chunk_rejected_before_float_conversion_and_cancel_skips_retry(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'chunk.wav';wavfile.write(path,16000,np.zeros(2*16000,dtype=np.int16))
            model=object.__new__(CommunityDiarizer);model.pipeline=lambda *a,**kw:self.fail('oversized waveform reached model')
            model.configure({'mixedChunkSeconds':1,'modelMemoryReserveBytes':1024})
            with self.assertRaises(CutError) as caught:model.turns(path)
            self.assertEqual(caught.exception.code,'AUDIO_RESOURCE_LIMIT')
            with self.assertRaises(CutError) as caught:model.turns(path,cancel=lambda:True)
            self.assertEqual(caught.exception.code,'CANCELED')
    def test_cancel_during_gpu_oom_prevents_cpu_retry(self):
        class Pipeline:
            calls=0
            def to(self,device):pass
            def __call__(self,*args,**kwargs):
                self.calls+=1;raise RuntimeError('CUDA out of memory')
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'chunk.wav';wavfile.write(path,16000,np.zeros(16000,dtype=np.int16))
            model=object.__new__(CommunityDiarizer);model.pipeline=Pipeline()
            with patch('torch.cuda.is_available',return_value=True),patch('torch.cuda.mem_get_info',return_value=(2**32,2**33)):
                model.configure({'device':'cuda','mixedChunkSeconds':1,'modelMemoryReserveBytes':1024})
                with self.assertRaises(CutError) as error:model.turns(path,cancel=lambda:model.pipeline.calls>0)
            self.assertEqual(error.exception.code,'CANCELED');self.assertEqual(model.pipeline.calls,1)
