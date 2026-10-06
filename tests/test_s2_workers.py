"""Real job/coordinator routing with thread-backed process and decoder boundaries."""
import hashlib
import json
from pathlib import Path
import queue
import tempfile
import threading
import time
import unittest
import wave
from unittest.mock import patch
import numpy as np
from contentrium_cut.contract import CutError,canonical_hash
from contentrium_cut.coordinator import Coordinator
from contentrium_cut.jobs import JobManager
from contentrium_cut.models import check_cancel
from test_policy import fixture,speech


class ThreadQueue(queue.Queue):
    def close(self):pass


class ThreadProcess:
    def __init__(self,target,args,name):self.thread=threading.Thread(target=target,args=args,daemon=True);self.pid=None
    def start(self):self.pid=17;self.thread.start()
    def is_alive(self):return self.thread.is_alive()
    def join(self,timeout=None):self.thread.join(timeout)
    def close(self):pass
    def terminate(self):raise AssertionError('Cooperative fixture must finish without termination')


class ThreadContext:
    Event=staticmethod(threading.Event)
    Queue=staticmethod(ThreadQueue)
    Process=staticmethod(ThreadProcess)


class Scope:
    assigned=False
    def assign(self,process):self.process=process;Scope.assigned=True
    def active_count(self):return 0
    def close(self):pass


class WorkerRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        boundary=patch('contentrium_cut.resource.available_memory',return_value=8*1024**3);boundary.start();self.addCleanup(boundary.stop)
        for target,value in [('contentrium_cut.jobs.mp.get_context',lambda name:ThreadContext()),('contentrium_cut.jobs.ProcessScope',Scope)]:
            context=patch(target,value);context.start();self.addCleanup(context.stop)
        self.jobs=JobManager(self.root);self.addCleanup(self.jobs.close);Scope.assigned=False

    def wait(self,job):
        deadline=time.monotonic()+3
        while not self.jobs.quiescent() and time.monotonic()<deadline:threading.Event().wait(.02)
        self.assertTrue(self.jobs.quiescent());return self.jobs.get(job['jobId'])

    def example_payload(self):
        path=self.root/'source.wav';path.write_bytes(b'isolated decoder input')
        snap,raw,_,_=fixture();snap['sources'][0]['canonicalPath']=str(path)
        snap['snapshotHash']=canonical_hash({k:v for k,v in snap.items() if k!='snapshotHash'})
        raw.update(evidence={},mode='separate',intervals=[speech(0,90,'A')],
                   validAudioRanges=[dict(assetId='CA-0',inputKey='mic',startFrame=0,endFrame=90,startSample=0,endSample=48000,sampleRate=16000)],
                   sourceInputs=[dict(assetId='CA-0',instanceKey='CA-0',inputKey='mic',path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest(),streamIndex=0,channelIndex=0,sessionOriginSeconds=0,sourceStartSeconds=0,sourceSampleRate=16000)])
        coordinator=Coordinator(self.root);coordinator.bind('owner',snap);state=coordinator.register_analysis('owner','job',raw)
        self.assertTrue(state['examples'])
        return dict(root=str(self.root),owner='owner',snapshot=snap,analysisId=state['analysisId'],revision=0,exampleId=state['examples'][0]['exampleId'])

    def test_example_uses_assigned_scope_real_coordinator_and_no_result_cache(self):
        payload=self.example_payload()
        def decode(source,directory,number,settings,cancel,start,duration,mixed):
            self.assertTrue(Scope.assigned);check_cancel(cancel)
            target=Path(directory)/'decoded.pcm';np.zeros(round(duration*16000),dtype='<f4').tofile(target)
            return {'samples':np.memmap(target,dtype='<f4',mode='r')}
        with patch('contentrium_cut.audio._decode',side_effect=decode):
            result=self.wait(self.jobs.submit('example',payload))
        self.assertEqual(result['status'],'completed',result);self.assertIsNone(result['cacheKey'])
        with wave.open(result['result']['path']) as wav:self.assertEqual(wav.getnchannels(),1);self.assertEqual(wav.getframerate(),16000);self.assertEqual(wav.getnframes(),48000)
        self.assertFalse(list((self.root/'cache').iterdir()))

    def test_example_cancel_reaps_owned_worker_and_cannot_promote_output(self):
        payload=self.example_payload();entered=threading.Event()
        def decode(*args,**kwargs):
            self.assertTrue(Scope.assigned);entered.set();deadline=time.monotonic()+2
            while not kwargs['cancel']() and time.monotonic()<deadline:threading.Event().wait(.01)
            check_cancel(kwargs['cancel']);raise AssertionError('cancel missing')
        with patch('contentrium_cut.audio._decode',side_effect=decode):
            job=self.jobs.submit('example',payload);self.assertTrue(entered.wait(1));self.jobs.cancel(job['jobId']);result=self.wait(job)
        self.assertEqual(result['status'],'canceled');self.assertNotIn('result',result);self.assertFalse(list((self.root/'cache').iterdir()))

    def test_input_worker_has_no_cache_and_model_provider_errors_cannot_persist_token(self):
        from contentrium_cut.input_capabilities import InputCapabilities
        path=self.root/'source.mov';path.write_bytes(b'media');inputs=InputCapabilities(self.root)
        selection=inputs.select('owner','project',[dict(assetId='asset',path=str(path),name='clip',bounds={'VIDEO':None,'AUDIO':None})])
        def probe(*args):self.assertTrue(Scope.assigned);return {'streams':[{'index':0,'codec_type':'video'}]}
        with patch('contentrium_cut.input_capabilities.trusted_probe',return_value='fixed-fixture'),patch('contentrium_cut.input_capabilities.run_probe',side_effect=probe):
            result=self.wait(self.jobs.submit('input-probe',inputs.payload(selection['selectionId'],'owner','project')))
        self.assertEqual(result['status'],'completed',result);self.assertTrue(result['result']['assets'][0]['hasVideo']);self.assertIsNone(result['cacheKey'])
        secret='hf_do_not_persist'
        with patch('contentrium_cut.models.ModelManager.install_community',side_effect=CutError('MODEL_NOT_READY',secret,{'token':secret})):
            result=self.wait(self.jobs.submit('model-setup',dict(modelRoot=str(self.root/'models'),token=secret,termsAccepted=True,revision='a'*40)))
        self.assertEqual(result['status'],'failed')
        for path in (self.root/'jobs').glob('*.json'):self.assertNotIn(secret,path.read_text())
