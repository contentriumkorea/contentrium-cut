import hashlib
import tempfile
import threading
import unittest
from unittest.mock import patch
from contentrium_cut.service import CutService
from contentrium_cut.contract import canonical_hash
from test_authenticated_http import ActivationHeartbeatTests
from test_private_auth import BOOTSTRAP, CONFIG
from test_policy import fixture,speech


class SinglePanelTests(unittest.TestCase):
    session=ActivationHeartbeatTests.session
    signed_request=ActivationHeartbeatTests.signed_request

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        from collections import namedtuple
        usage=namedtuple('Usage','total used free')(100*1024**3,20*1024**3,80*1024**3)
        for target,value in [('contentrium_cut.resource.available_memory',8*1024**3),('contentrium_cut.resource.shutil.disk_usage',usage)]:
            boundary=patch(target,return_value=value);boundary.start();self.addCleanup(boundary.stop)
        self.host=dict(pid=17,createTime='123',alive=True,processName='Adobe Premiere Pro.exe')
        from test_s2_workers import ThreadContext,Scope
        self.service=CutService(self.tmp.name,dict(CONFIG,privateBootstrap=BOOTSTRAP,sourceReader=lambda p:open(p,'rb'),validationContext=ThreadContext(),validationScope=Scope),process_probe=lambda instance,claim:dict(self.host))
        self.addCleanup(self.service.close)
        self.headers={'Host':'127.0.0.1:41737','Content-Type':'application/json'}
        self.client=self.session();self.snapshot,self.analysis,self.mapping,self.policy=fixture()
        from pathlib import Path
        source=Path(self.tmp.name)/'analysis.wav';source.write_bytes(b'original analysis media')
        self.snapshot['sources'][0]['canonicalPath']=str(source)
        for asset in self.snapshot['sources'][1:]:
            media=Path(self.tmp.name)/(asset['assetId']+'.mov');media.write_bytes(b'original camera media');asset['canonicalPath']=str(media)
        self.analysis['sourceInputs']=[dict(assetId='CA-0',instanceKey='CA-0',path=str(source),sha256=hashlib.sha256(source.read_bytes()).hexdigest(),sessionOriginSeconds=0,sourceStartSeconds=0,sourceSampleRate=48000)]
        self.snapshot['supportFlags']['hostApplyVerified']=True
        self.snapshot['snapshotHash']=canonical_hash({k:v for k,v in self.snapshot.items() if k!='snapshotHash'})
        self.analysis['intervals']=[speech(0,100,'A'),speech(100,200,'B')]
        self.analysis.update(evidence={},validAudioRanges=[dict(assetId='CA-0',startFrame=0,endFrame=200,startSample=0,endSample=320000,sampleRate=48000)])

    def request(self,path,body=None,method='POST',client=None):
        from test_source_validation import finish_validation
        call=lambda m,p,b:self.signed_request(client or self.client,m,p,b)
        return finish_validation(call,call(method,path,body))

    def heartbeat(self,**extra):
        return self.request('/heartbeat',dict(dict(hostIdentity=self.host,epoch=0,batchRunning=False,quiescent=True,
                                                 appVersion=CONFIG['appVersion'],bundleId=CONFIG['bundleId'],protocolVersion=1),**extra))

    def bind(self):
        from pathlib import Path
        self.analysis.update(mediaSnapshotHash=self.snapshot['snapshotHash'],mediaInputs=[dict(assetId=s['assetId'],path=s['canonicalPath'],sha256=hashlib.sha256(Path(s['canonicalPath']).read_bytes()).hexdigest()) for s in self.snapshot['sources']])
        self.assertEqual(self.heartbeat()[0],200)
        status,value=self.request('/project',dict(snapshot=self.snapshot,epoch=0,hostIdentity=self.host));self.assertEqual(status,200,value)
        owner=self.service.auth.owner_id(self.client['id']);job='seed'
        self.service.jobs.jobs[job]=dict(jobId=job,kind='analysis',status='completed',epoch=0,result=self.analysis)
        self.service.record_job(job,owner,self.snapshot['projectRef'],self.snapshot['snapshotHash'])
        return job

    def test_missing_stale_conflicting_and_wrong_protocol_heartbeat_deny_work(self):
        self.assertNotEqual(self.request('/project',dict(snapshot=self.snapshot,epoch=0))[0],200)
        for value in [None,True,2]:
            self.assertNotEqual(self.heartbeat(protocolVersion=value)[0],200)
        self.assertNotEqual(self.heartbeat(panelVersion='wrong')[0],200)
        self.assertEqual(self.heartbeat()[0],200)
        panel=next(iter(self.service.panels.values()));panel['heartbeatAt']-=11
        status,value=self.request('/resources',{'settings':{'device':'cpu'},'epoch':0})
        self.assertEqual(status,409,value)
        state=self.request('/state',method='GET')[1]
        self.assertFalse(state['compatible']);self.assertEqual(state['admissionError']['code'],'HEARTBEAT_REQUIRED')

    def test_corrected_state_required_for_plan_and_revision_rechecked(self):
        job=self.bind();status,state=self.request('/analyses/register',{'jobId':job,'epoch':0});self.assertEqual(status,200,state)
        status,corrected=self.request('/analyses/'+state['analysisId']+'/correct',dict(expectedRevision=0,operation={'type':'merge','speakerIds':['A','B'],'targetSpeakerId':'A'},requestId='correct',epoch=0))
        self.assertEqual(status,200,corrected)
        body=dict(jobId=job,analysisId=state['analysisId'],analysisRevision=0,mapping=self.mapping,policy=self.policy,epoch=0)
        self.assertEqual(self.request('/plan',body)[1]['error']['code'],'CORRECTION_REVISION_CONFLICT')
        body['analysisRevision']=1
        status,plan=self.request('/plan',body);self.assertEqual(status,200,plan)

    def test_resource_prune_holds_gate_and_does_not_reopen_an_update_stop(self):
        self.bind();observed=[]
        self.service.coordinator.resource_settings({'cacheBudgetBytes':1024})
        def prune(root,budget,guard,cancel=None):
            with guard() as held:
                observed.append((held,self.service.gate_open,self.service.jobs.gate_open))
                self.service.stop_all(0)  # Even a same-epoch stop owns a different gate closure.
            return {'budgetMet':True}
        with patch('contentrium_cut.resource.prune_completed_cache',side_effect=prune):
            status,value=self.request('/resources/prune',{'epoch':0})
        self.assertNotEqual(status,200,value);self.assertEqual(observed,[(True,False,False)])
        self.assertFalse(self.service.gate_open)

    def test_old_request_cannot_borrow_reconnected_heartbeat_at_commit(self):
        self.bind();entered=threading.Event();resume=threading.Event()
        original=self.service._host
        def waiting(panel,claim):
            if threading.current_thread().name=='old-work':entered.set();resume.wait(3)
            return original(panel,claim)
        self.service._host=waiting;results=[]
        worker=threading.Thread(target=lambda:results.append(self.request('/resources',{'settings':{'device':'cpu'},'epoch':0})),name='old-work')
        worker.start();self.assertTrue(entered.wait(2))
        fresh=self.session()  # No new /state is needed to displace ordinary work authority.
        resume.set();worker.join(3)
        self.assertEqual(results[0][0],401,results)
        self.assertEqual(results[0][1]['error']['code'],'SESSION_EXPIRED')

    def test_model_revision_fixed_provider_redacts_failure_and_install_token_is_transient(self):
        self.heartbeat();secret='hf_private_test_token'
        with patch('contentrium_cut.model_setup.community_revision',return_value='a'*40):
            status,value=self.request('/models/community-1/revision',dict(token=secret,termsAccepted=True,epoch=0))
        self.assertEqual((status,value),(200,{'revision':'a'*40}))
        captured=[]
        def submit(kind,payload,expected_epoch):
            captured.append((kind,payload));value=dict(jobId='model',kind=kind,status='running',epoch=expected_epoch)
            self.service.jobs.jobs['model']=value;return value
        with patch.object(self.service.jobs,'submit',side_effect=submit):
            status,value=self.request('/models/community-1/install',dict(token=secret,termsAccepted=True,revision='a'*40,epoch=0))
        self.assertEqual(status,200,value);self.assertEqual(captured[0][1]['token'],secret)
        import json
        self.assertNotIn(secret,json.dumps(self.service.jobs.jobs));self.assertNotIn(secret,self.service.ownership_path.read_text())

    def test_input_creation_without_active_sequence_and_first_batch_rechecks_files(self):
        from pathlib import Path
        from contentrium_cut.input_capabilities import probe_sources
        self.heartbeat();path=Path(self.tmp.name)/'clip.mov';path.write_bytes(b'media')
        sources=[dict(assetId='one',path=str(path),name='one',bounds={'VIDEO':None,'AUDIO':None})]
        status,selected=self.request('/input/sources',dict(projectRef='project',sources=sources,epoch=0));self.assertEqual(status,200,selected)
        owner=self.service.auth.owner_id(self.client['id'])
        payload=self.service.inputs.payload(selected['selectionId'],owner,'project')
        result=probe_sources(payload,probe=lambda p,c:{'streams':[{'index':0,'codec_type':'video'},{'index':1,'codec_type':'audio','channels':2}]})
        self.service.jobs.jobs['probe']=dict(jobId='probe',kind='input-probe',status='completed',epoch=0,result=result)
        self.service.record_job('probe',owner,None,None);self.service.owners['probe'].update(selectionId=selected['selectionId'],inputProject='project')
        status,cap=self.request('/input/capabilities/result',dict(jobId='probe',epoch=0));self.assertEqual(status,200,cap)
        status,apply=self.request('/input/begin',dict(capabilityId=cap['capabilityId'],choices=[dict(assetId='one',role='camera',outputAudio=True)],requestId='input',epoch=0));self.assertEqual(status,200,apply)
        path.write_bytes(b'replaced')
        status,value=self.request('/apply/check',dict(applyId=apply['applyId'],epoch=0,batchId=1,operationDigest='c'*64,resultSequenceRef=None))
        self.assertEqual(value['error']['code'],'SOURCE_CHANGED')

    def test_example_job_derives_private_payload_and_hides_result_after_correction(self):
        from pathlib import Path
        path=Path(self.tmp.name)/'example.wav';path.write_bytes(b'media')
        self.snapshot['sources'][0]['canonicalPath']=str(path)
        self.snapshot['snapshotHash']=canonical_hash({k:v for k,v in self.snapshot.items() if k!='snapshotHash'})
        self.analysis['validAudioRanges'][0]['inputKey']='mic'
        self.analysis['sourceInputs']=[dict(assetId='CA-0',instanceKey='CA-0',inputKey='mic',path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest(),streamIndex=0,channelIndex=0,sessionOriginSeconds=0,sourceStartSeconds=0,sourceSampleRate=16000)]
        job=self.bind();state=self.request('/analyses/register',dict(jobId=job,epoch=0))[1]
        example=state['examples'][0];captured=[]
        def submit(kind,payload,expected_epoch):
            captured.append(payload);value=dict(jobId='example',kind=kind,status='running',epoch=expected_epoch)
            self.service.jobs.jobs['example']=value;return value
        body=dict(exampleId=example['exampleId'],expectedRevision=0,epoch=0)
        route='/analyses/'+state['analysisId']+'/example'
        self.assertEqual(self.request(route,dict(body,path='C:/untrusted'))[0],400)
        with patch.object(self.service.jobs,'submit',side_effect=submit):status,value=self.request(route,body)
        self.assertEqual(status,200,value);self.assertEqual(captured[0]['root'],self.tmp.name)
        self.assertEqual(captured[0]['snapshot'],self.snapshot);self.assertEqual(captured[0]['owner'],self.service.auth.owner_id(self.client['id']))
        self.service.jobs.jobs['example'].update(status='completed',result=dict(path='private-preview.wav',analysisId=state['analysisId'],revision=0))
        self.assertEqual(self.request('/jobs/example',method='GET')[0],200)
        status,value=self.request('/analyses/'+state['analysisId']+'/correct',dict(expectedRevision=0,operation={'type':'name','speakerId':'A','name':'Renamed'},requestId='name',epoch=0))
        self.assertEqual(status,200,value)
        self.assertEqual(self.request('/jobs/example',method='GET')[1]['error']['code'],'EXAMPLE_SCOPE')

    def test_host_replaced_while_waiting_for_admission_guard_cannot_commit(self):
        from contextlib import contextmanager
        self.bind();entered=threading.Event();resume=threading.Event();results=[]
        @contextmanager
        def guarded(epoch):entered.set();resume.wait(2);yield
        self.service._guard=guarded
        worker=threading.Thread(target=lambda:results.append(self.request('/resources',dict(settings={'device':'cpu'},epoch=0))))
        worker.start();self.assertTrue(entered.wait(1));self.host['createTime']='new-host';resume.set();worker.join(3)
        self.assertEqual(results[0][0],409,results)
        self.assertEqual(results[0][1]['error']['code'],'HOST_IDENTITY_REQUIRED')
