"""Isolated final-review regressions; no provider, native host or OS process."""
import copy
import hashlib
import json
import os
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch
from contentrium_cut.contract import canonical_hash


class FinalSourceTests(unittest.TestCase):
    def setUp(self):
        from test_single_panel_service import SinglePanelTests
        self.case=SinglePanelTests('runTest');self.case.setUp();self.addCleanup(self.case.doCleanups)
        self.service=self.case.service;self.media=[]
        for index,source in enumerate(self.case.snapshot['sources']):
            path=Path(self.case.tmp.name)/('media-'+str(index));path.write_bytes(b'original media')
            source['canonicalPath']=str(path)
            self.media.append(dict(assetId=source['assetId'],path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        self.case.snapshot['snapshotHash']=canonical_hash({k:v for k,v in self.case.snapshot.items() if k!='snapshotHash'})
        self.case.analysis['sourceInputs'][0].update(path=self.media[0]['path'],sha256=self.media[0]['sha256'])
        self.case.analysis.update(mediaInputs=self.media,mediaSnapshotHash=self.case.snapshot['snapshotHash'])
        self.job=self.case.bind()

    def replace(self,index):
        path=Path(self.media[index]['path']);stat=path.stat();path.write_bytes(b'X'*stat.st_size);os.utime(path,ns=(stat.st_atime_ns,stat.st_mtime_ns))

    def plan(self):
        state=self.case.request('/analyses/register',dict(jobId=self.job,epoch=0))[1]
        body=dict(jobId=self.job,analysisId=state['analysisId'],analysisRevision=0,mapping=self.case.mapping,policy=self.case.policy,epoch=0)
        code,plan=self.case.request('/plan',body);self.assertEqual(code,200,plan)
        return plan,body

    def test_camera_only_same_stat_change_rejects_plan_begin_and_first_batch(self):
        plan,body=self.plan();self.assertTrue(any(s['sourceClipInstanceKey']=='CB-0' for s in plan['segments']))
        self.replace(1)
        for route,payload in [('/plan',body),('/apply/begin',dict(planHash=plan['planHash'],snapshotHash=plan['snapshotHash'],requestId='camera',epoch=0))]:
            self.assertEqual(self.case.request(route,payload)[1].get('error',{}).get('code'),'SOURCE_CHANGED')
        Path(self.media[1]['path']).write_bytes(b'original media')
        begin=self.case.request('/apply/begin',dict(planHash=plan['planHash'],snapshotHash=plan['snapshotHash'],requestId='camera',epoch=0))[1]
        self.replace(1)
        response=self.case.request('/apply/check',dict(applyId=begin['applyId'],epoch=0,batchId=1,operationDigest='a'*64,resultSequenceRef=None))[1]
        self.assertEqual(response.get('error',{}).get('code'),'SOURCE_CHANGED')
        self.assertFalse(self.service.journal.records[begin['applyId']]['batches'])

    def test_sync_same_stat_change_rejects_reuse_plan_begin_and_first_batch(self):
        owner=self.service.auth.owner_id(self.case.client['id']);assets=[s['assetId'] for s in self.media[:2]]
        result=dict(schemaVersion=1,referenceAssetId=assets[0],offsets={a:0 for a in assets},sources={a:{'status':'accepted','mtimeNs':time.time_ns()} for a in assets},reviews=[],mediaInputs=self.media,mediaSnapshotHash=self.case.snapshot['snapshotHash'])
        self.service.jobs.jobs['sync']=dict(jobId='sync',kind='sync',status='completed',epoch=0,result=result)
        self.service.record_job('sync',owner,self.case.snapshot['projectRef'],self.case.snapshot['snapshotHash'])
        body=dict(jobId='sync',selectedClipInstanceKeys=[c['instanceKey'] for c in self.case.snapshot['clips'][:2]],epoch=0)
        code,plan=self.case.request('/sync-plan',body);self.assertEqual(code,200,plan)
        self.replace(1)
        for route,payload,method in [('/jobs/sync',None,'GET'),('/sync-plan',body,'POST'),('/apply/begin',dict(planHash=plan['planHash'],snapshotHash=plan['snapshotHash'],requestId='sync',epoch=0),'POST')]:
            self.assertEqual(self.case.request(route,payload,method=method)[1].get('error',{}).get('code'),'SOURCE_CHANGED')
        Path(self.media[1]['path']).write_bytes(b'original media')
        begin=self.case.request('/apply/begin',dict(planHash=plan['planHash'],snapshotHash=plan['snapshotHash'],requestId='sync',epoch=0))[1]
        self.replace(1)
        response=self.case.request('/apply/check',dict(applyId=begin['applyId'],epoch=0,batchId=1,operationDigest='a'*64,resultSequenceRef=None))[1]
        self.assertEqual(response.get('error',{}).get('code'),'SOURCE_CHANGED')

    def model(self):
        directory=Path(self.case.tmp.name)/'models'/'silero';directory.mkdir(parents=True)
        (directory/'model.onnx').write_bytes(b'weights')
        (directory/'manifest.json').write_text(json.dumps(dict(modelId='silero',revision='pinned',entrypoint='model.onnx',files={'model.onnx':hashlib.sha256(b'weights').hexdigest()})))

    def test_status_and_job_admission_do_not_read_weights(self):
        self.model();calls=[]
        def sha(path,*args,**kwargs):calls.append(threading.current_thread().name);return hashlib.sha256(b'weights').hexdigest()
        def submit(kind,payload,expected_epoch):
            value=dict(jobId='pending',kind=kind,status='running',epoch=expected_epoch);self.service.jobs.jobs['pending']=value;return value
        with patch('contentrium_cut.models._sha',side_effect=sha),patch.object(self.service.jobs,'submit',side_effect=submit):
            self.assertEqual(self.case.request('/state',method='GET')[0],200)
            self.assertEqual(self.case.request('/jobs',dict(kind='analysis',epoch=0,options={'mode':'separate','microphones':[{'instanceKey':'CA-0','speakerId':'A'}]}))[0],200)
        self.assertEqual(calls,[],'physical requests must not hash model weights')

    def test_prune_scan_does_not_block_concurrent_stop(self):
        entered=threading.Event();release=threading.Event();stopped=threading.Event();responses=[]
        self.service.coordinator.resource_settings({'cacheBudgetBytes':1024})
        def slow(*args,**kwargs):entered.set();release.wait(2);return {'removed':[],'budgetMet':True}
        with patch('contentrium_cut.resource.prune_completed_cache',side_effect=slow):
            requester=threading.Thread(target=lambda:responses.append(self.case.signed_request(self.case.client,'POST','/resources/prune',{'epoch':0})))
            requester.start();self.assertTrue(entered.wait(1))
            stopper=threading.Thread(target=lambda:(self.service.stop_all(1),stopped.set()));stopper.start()
            try:self.assertTrue(stopped.wait(.25),'concurrent stop blocked on prune filesystem work')
            finally:release.set();requester.join(2);stopper.join(2)
        self.assertFalse(self.service.gate_open)

    def test_legacy_result_without_media_scope_requires_explicit_reanalysis(self):
        self.service.jobs.jobs[self.job]['result'].pop('mediaInputs')
        response=self.case.request('/jobs/'+self.job,method='GET')[1]
        self.assertEqual(response['error']['code'],'SOURCE_REANALYSIS_REQUIRED')

    def test_slow_model_verification_has_owned_job_and_responsive_status_and_stop(self):
        from test_s2_workers import ThreadContext,Scope
        from contentrium_cut.models import ModelManager,check_cancel
        self.model();entered=threading.Event();release=threading.Event();observed=[]
        def slow(path,cancel=None):
            observed.append(threading.current_thread().name);entered.set();release.wait(2);check_cancel(cancel)
            return hashlib.sha256(b'weights').hexdigest()
        def analyze(mode,sources,settings,cancel):
            ModelManager(settings['modelRoot'],cancel=cancel).resolve('silero')
            self.fail('canceled verification reached inference')
        self.service.jobs.context=ThreadContext()
        with patch('contentrium_cut.jobs.ProcessScope',Scope),patch('contentrium_cut.models._sha',side_effect=slow),patch('contentrium_cut.audio.analyze_audio',side_effect=analyze):
            code,job=self.case.request('/jobs',dict(kind='analysis',epoch=0,options={'mode':'separate','microphones':[{'instanceKey':'CA-0','speakerId':'A'}]}))
            self.assertEqual(code,200,job);self.assertTrue(entered.wait(1));self.assertTrue(self.service.jobs.processes)
            started=time.monotonic();state=self.case.request('/state',method='GET')[1]
            self.assertLess(time.monotonic()-started,.25);self.assertEqual(state['models']['silero']['status'],'installed')
            self.assertEqual(self.case.heartbeat()[0],200)
            stopped=threading.Event();stopper=threading.Thread(target=lambda:(self.service.stop_all(1),stopped.set()));stopper.start()
            try:self.assertTrue(stopped.wait(.25))
            finally:release.set();stopper.join(1)
            self.service.jobs.close()
        self.assertEqual(len(observed),1);self.assertNotEqual(observed[0],'MainThread')
        self.assertEqual(self.service.jobs.get(job['jobId'])['status'],'canceled');self.assertFalse(self.service.gate_open)

    def test_model_metadata_never_authorizes_changed_same_stat_weights(self):
        from contentrium_cut.models import ModelManager
        from contentrium_cut.contract import CutError
        self.model();manager=ModelManager(Path(self.case.tmp.name)/'models');self.assertEqual(manager.resolve('silero')['status'],'ready')
        path=manager.root/'silero'/'model.onnx';before=path.stat();path.write_bytes(b'changed');os.utime(path,ns=(before.st_atime_ns,before.st_mtime_ns))
        self.assertEqual(manager.state('silero',verify=False)['status'],'installed')
        with self.assertRaises(CutError) as caught:manager.resolve('silero')
        self.assertEqual(caught.exception.code,'MODEL_NOT_READY')

    def test_prune_blocked_unlink_stops_without_later_delete_or_promotion(self):
        from contentrium_cut.cache import envelope
        from contentrium_cut.jobs import atomic_json
        self.service.coordinator.resource_settings({'cacheBudgetBytes':1})
        paths=[Path(self.case.tmp.name)/'cache'/(char*64+'.json') for char in ['a','b']]
        for path in paths:atomic_json(path,envelope('analysis',path.stem,self.case.analysis))
        protected=paths[0].parent/'corrections.json';protected.write_bytes(b'preserve')
        entered=threading.Event();release=threading.Event();calls=[];actual=Path.unlink
        def unlink(path,*args,**kwargs):
            if path in paths:calls.append(path);entered.set();release.wait(2)
            return actual(path,*args,**kwargs)
        with patch.object(Path,'unlink',unlink):
            code,pending=self.case.signed_request(self.case.client,'POST','/resources/prune',{'epoch':0});self.assertEqual(code,200,pending)
            self.assertTrue(entered.wait(1));self.assertTrue(self.service.maintenance)
            stopped=threading.Event();stopper=threading.Thread(target=lambda:(self.service.stop_all(1),stopped.set()));stopper.start()
            try:self.assertTrue(stopped.wait(.25));self.assertFalse(self.service.validations.quiescent())
            finally:release.set();stopper.join(1)
            self.service.validations.close()
        self.assertEqual(len(calls),1);self.assertTrue(protected.exists());self.assertTrue(paths[1].exists());self.assertFalse(self.service.gate_open)
        continuation='/continuations/'+pending['continuation']['id']
        self.assertNotEqual(self.case.request(continuation+'/take',{'epoch':0})[0],200)

    def test_successful_prune_is_consumed_once_and_only_explicit_take_reopens(self):
        self.service.coordinator.resource_settings({'cacheBudgetBytes':1024})
        code,pending=self.case.signed_request(self.case.client,'POST','/resources/prune',{'epoch':0});self.assertEqual(code,200,pending)
        path='/continuations/'+pending['continuation']['id'];deadline=time.monotonic()+2
        while time.monotonic()<deadline:
            state=self.case.request(path,method='GET')[1]
            if state['status']!='pending':break
            time.sleep(.01)
        self.assertEqual(state['status'],'ready');self.assertFalse(self.service.gate_open)
        self.assertEqual(self.case.request(path+'/take',{'epoch':0})[0],200);self.assertTrue(self.service.gate_open)
        self.assertNotEqual(self.case.request(path+'/take',{'epoch':0})[0],200)

    def test_owned_worker_observes_every_snapshot_media_before_analysis_and_rejects_change(self):
        from contentrium_cut.jobs import _worker
        from test_s2_workers import ThreadQueue
        owner=self.service.auth.owner_id(self.case.client['id'])
        payload=self.service.coordinator.audio_payload(owner,{'mode':'separate','microphones':[{'instanceKey':'CA-0','speakerId':'A'}]})
        self.assertEqual(len(payload['mediaSources']),len(self.media))
        raw=copy.deepcopy(self.case.analysis);raw.pop('mediaInputs');raw.pop('mediaSnapshotHash');payload['settings'].pop('modelRevision',None)
        queue=ThreadQueue()
        with patch('contentrium_cut.audio.analyze_audio',return_value=raw):_worker('analysis',payload,threading.Event(),queue,str(Path(self.case.tmp.name)/'cache'))
        message=queue.get_nowait();self.assertTrue(message['ok'],message);self.assertEqual(message['value']['mediaInputs'],self.media)
        self.assertEqual(message['value']['mediaSnapshotHash'],self.case.snapshot['snapshotHash'])
        def replace(*args,**kwargs):self.replace(1);return raw
        with patch('contentrium_cut.audio.analyze_audio',side_effect=replace):_worker('analysis',payload,threading.Event(),queue,str(Path(self.case.tmp.name)/'cache'))
        self.assertEqual(queue.get_nowait()['error']['code'],'SOURCE_CHANGED')

    def test_prune_uncooperative_scan_is_force_drained_and_cannot_reopen(self):
        from test_s2_workers import Scope
        entered=threading.Event();forced=threading.Event()
        class ForceScope(Scope):
            def terminate_if_owned(inner):forced.set();return True
        self.service.validations.scope_factory=ForceScope;self.service.validations.STOP_GRACE=.05
        self.service.coordinator.resource_settings({'cacheBudgetBytes':1024})
        def blocked(*args,**kwargs):entered.set();forced.wait(2);return {'removed':[],'budgetMet':True}
        with patch('contentrium_cut.resource.prune_completed_cache',side_effect=blocked):
            code,pending=self.case.signed_request(self.case.client,'POST','/resources/prune',{'epoch':0});self.assertEqual(code,200,pending);self.assertTrue(entered.wait(1))
            stopper=threading.Thread(target=lambda:self.service.stop_all(1));stopper.start();stopper.join(.25);self.assertFalse(stopper.is_alive())
            self.assertTrue(forced.wait(.5));self.service.validations.close()
        self.assertFalse(self.service.gate_open);self.assertTrue(self.service.validations.quiescent())

    def aborted_maintenance(self,failed=False):
        from contentrium_cut.contract import CutError
        self.service.coordinator.resource_settings({'cacheBudgetBytes':1024})
        if failed:
            with patch('contentrium_cut.resource.prune_completed_cache',side_effect=CutError('RESOURCE_TEST_FAILURE','fixture')):
                code,pending=self.case.signed_request(self.case.client,'POST','/resources/prune',{'epoch':0})
                self.assertEqual(code,200,pending);self.wait_drain()
        else:
            code,pending=self.case.signed_request(self.case.client,'POST','/resources/prune',{'epoch':0});self.assertEqual(code,200,pending)
            self.case.request('/continuations/'+pending['continuation']['id']+'/cancel',{})
            self.wait_drain()
        return pending

    def wait_drain(self):
        deadline=time.monotonic()+2
        while not self.service.validations.quiescent() and time.monotonic()<deadline:time.sleep(.01)
        self.assertTrue(self.service.validations.quiescent())

    def test_canceled_and_failed_maintenance_can_only_be_released_explicitly(self):
        for failed in [False,True]:
            with self.subTest(failed=failed):
                self.aborted_maintenance(failed)
                state=self.case.request('/state',method='GET')[1]
                self.assertFalse(state['gateOpen']);self.assertTrue(state.get('maintenance',{}).get('canRelease'))
                code,value=self.case.request('/resources/prune',{'epoch':0,'action':'release'})
                self.assertEqual(code,200,value);self.assertTrue(value['released']);self.assertTrue(self.service.gate_open)
                self.assertNotEqual(self.case.request('/resources/prune',{'epoch':0,'action':'release'})[0],200)

    def test_maintenance_release_rejects_live_drain_then_accepts_fresh_same_owner_session(self):
        entered=threading.Event();release=threading.Event()
        self.service.coordinator.resource_settings({'cacheBudgetBytes':1024})
        def slow(*args,**kwargs):entered.set();release.wait(2);return {'removed':[],'budgetMet':True}
        with patch('contentrium_cut.resource.prune_completed_cache',side_effect=slow):
            code,pending=self.case.signed_request(self.case.client,'POST','/resources/prune',{'epoch':0});self.assertEqual(code,200,pending);self.assertTrue(entered.wait(1))
            self.case.request('/continuations/'+pending['continuation']['id']+'/cancel',{})
            try:self.assertNotEqual(self.case.request('/resources/prune',{'epoch':0,'action':'release'})[0],200)
            finally:release.set();self.wait_drain()
        old=self.case.client;self.case.client=self.case.session()
        self.assertNotEqual(self.case.request('/resources/prune',{'epoch':0,'action':'release'},client=old)[0],200)
        self.assertEqual(self.case.heartbeat()[0],200)
        status,value=self.case.request('/resources/prune',{'epoch':0,'action':'release'});self.assertEqual(status,200,value)

    def test_maintenance_release_cannot_override_host_epoch_stop_or_update(self):
        from contextlib import contextmanager
        from contentrium_cut.contract import CutError
        self.aborted_maintenance()
        captured_owner=self.service.maintenance['owner'];self.service.maintenance['owner']='foreign-owner'
        self.assertNotEqual(self.case.request('/resources/prune',{'epoch':0,'action':'release'})[0],200)
        self.assertIsNone(self.case.request('/state',method='GET')[1]['maintenance']);self.service.maintenance['owner']=captured_owner
        self.case.host['createTime']='replacement'
        self.assertNotEqual(self.case.request('/resources/prune',{'epoch':0,'action':'release'})[0],200)
        self.case.host['createTime']='123'
        self.assertNotEqual(self.case.request('/resources/prune',{'epoch':1,'action':'release'})[0],200)
        panel=next(iter(self.service.panels.values()));heartbeat=panel['heartbeatAt'];panel['heartbeatAt']-=11
        self.assertNotEqual(self.case.request('/resources/prune',{'epoch':0,'action':'release'})[0],200);panel['heartbeatAt']=heartbeat
        self.service.config['recoveryOnly']=True
        self.assertNotEqual(self.case.request('/resources/prune',{'epoch':0,'action':'release'})[0],200);self.service.config['recoveryOnly']=False
        @contextmanager
        def update_closed(epoch):raise CutError('UPDATE_IN_PROGRESS','fixture');yield
        with patch.object(self.service,'_guard',update_closed):
            self.assertNotEqual(self.case.request('/resources/prune',{'epoch':0,'action':'release'})[0],200)
        self.assertFalse(self.service.gate_open)
        self.service.stop_all(0)
        self.assertNotEqual(self.case.request('/resources/prune',{'epoch':0,'action':'release'})[0],200);self.assertFalse(self.service.gate_open)


class CapacityTests(unittest.TestCase):
    def test_idle_exit_waits_for_owned_maintenance_and_validation_drain(self):
        from contentrium_cut.lifecycle import HeadlessLifecycle
        from types import SimpleNamespace
        from unittest.mock import Mock
        service=SimpleNamespace(update_state=lambda:{'updateState':'IDLE'},jobs=SimpleNamespace(quiescent=lambda:True),
                                validations=SimpleNamespace(quiescent=lambda:False),maintenance=False,_host_exited=lambda:True,applies={},close=Mock())
        with patch('contentrium_cut.lifecycle.pending_update',return_value=False):
            lifecycle=HeadlessLifecycle(SimpleNamespace(root=Path('.')),service,Mock(),{'appVersion':'0.1.0'})
            self.assertIsNone(lifecycle.tick());service.close.assert_not_called()
            service.validations.quiescent=lambda:True;service.maintenance={'id':'pending'}
            self.assertEqual(lifecycle.tick(),0);service.close.assert_called_once()

    def test_dense_hour_plan_counts_outside_fragments_and_rejects_before_begin(self):
        from test_policy import fixture,speech
        from contentrium_cut.coordinator import Coordinator
        from contentrium_cut.apply_journal import edit_batch_count,MAX_BATCHES,MAX_BATCH_BYTES,MAX_STORAGE_BYTES
        from contentrium_cut.contract import CutError
        import tempfile
        snapshot,analysis,mapping,policy=fixture(108000)
        snapshot['snapshotHash']=canonical_hash({k:v for k,v in snapshot.items() if k!='snapshotHash'})
        analysis['intervals']=[speech(i,i+60,'A' if i//60%2==0 else 'B') for i in range(0,108000,60)]
        with tempfile.TemporaryDirectory() as directory:
            coordinator=Coordinator(directory);coordinator.bind('owner',snapshot)
            plan=coordinator.plan('owner',analysis,mapping,policy)
            self.assertEqual(len(plan['segments']),1800)
            tracks=[t['trackRef'] for t in snapshot['tracks']]
            self.assertEqual(edit_batch_count(snapshot,plan,tracks),5404)
            from test_apply_journal import JournalTests
            journal_case=JournalTests('runTest');journal_case.setUp();self.addCleanup(journal_case.doCleanups)
            journal_case.binding['batchLimit']=5405
            identity=journal_case.journal.begin(journal_case.binding,'dense',plan)['applyId']
            record=journal_case.journal.records[identity]
            record['batches']=[dict(batchId=i,operationDigest='c'*64,resultSequenceRef=None,confirmed=True) for i in range(1,5404)]
            self.assertTrue(journal_case.journal.batch('owner',identity,0,5404,'c'*64,None)['execute'])
            journal_case.journal.batch_end('owner',identity,0,5404,{'transactionReturned':True})
            restored=journal_case.module.ApplyJournal(journal_case.tmp.name)
            self.assertFalse(restored.corrupt);self.assertEqual(len(restored.records[identity]['batches']),5404)
            outside=copy.deepcopy(snapshot);outside['range']={'startFrame':30,'endFrame':107970}
            outside['snapshotHash']=canonical_hash({k:v for k,v in outside.items() if k!='snapshotHash'})
            coordinator.bind('outside',outside);outside_plan=coordinator.plan('outside',analysis,mapping,policy)
            self.assertEqual(len(outside_plan['segments']),1799)
            self.assertEqual(edit_batch_count(outside,outside_plan,tracks),5419)
            oversized=copy.deepcopy(plan);oversized['segments']*=2
            with self.assertRaises(CutError) as caught:edit_batch_count(snapshot,oversized,tracks)
            self.assertEqual(caught.exception.code,'APPLY_CAPACITY_EXCEEDED');self.assertIn('Split',caught.exception.message)
        worst=dict(batchId=MAX_BATCHES,operationDigest='f'*64,resultSequenceRef='\U0001f600'*512,confirmed=False)
        self.assertLessEqual(len(json.dumps(worst,ensure_ascii=True))+1,MAX_BATCH_BYTES)
        self.assertLess(MAX_BATCH_BYTES*MAX_BATCHES+1024*1024,MAX_STORAGE_BYTES)

    def test_former_ordinal_boundary_accepts_durable_one_use_permit_and_restart(self):
        from test_apply_journal import JournalTests
        case=JournalTests('runTest');case.setUp();self.addCleanup(case.doCleanups)
        identity=case.begin()['applyId'];value=case.journal.records[identity]
        value['batches']=[dict(batchId=i,operationDigest='c'*64,resultSequenceRef=None,confirmed=True) for i in range(1,4097)]
        permit=case.journal.batch('owner',identity,0,4097,'c'*64,None)
        self.assertTrue(permit['execute']);self.assertFalse(case.journal.batch('owner',identity,0,4097,'c'*64,None)['execute'])
        case.journal.batch_end('owner',identity,0,4097,{'transactionReturned':True})
        restored=case.module.ApplyJournal(case.tmp.name)
        self.assertFalse(restored.corrupt);self.assertEqual(len(restored.records[identity]['batches']),4097)
