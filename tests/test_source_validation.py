import hashlib
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest


def finish_validation(request,response):
    status,value=response
    if status!=200 or 'continuation' not in value:return response
    pending=value['continuation'];path='/continuations/'+pending['id'];deadline=time.monotonic()+3
    while time.monotonic()<deadline:
        code,progress=request('GET',path,None)
        if code!=200:return code,progress
        if progress['status']=='ready':return request('POST',path+'/take',{'epoch':pending['epoch']})
        if progress['status']!='pending':return 409,{'error':progress.get('error',{'code':'VALIDATION_FAILED'})}
        time.sleep(.005)
    raise AssertionError('isolated validation did not finish')


class ValidationTests(unittest.TestCase):
    def setUp(self):
        from contentrium_cut.source_validation import Validations
        from test_s2_workers import ThreadContext,Scope
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'source';self.path.write_bytes(b'first')
        self.manager=Validations(open_reader=lambda path:open(path,'rb'),context=ThreadContext(),scope_factory=Scope)
        self.addCleanup(self.manager.close)
        self.sources=[{'path':str(self.path),'sha256':hashlib.sha256(b'first').hexdigest()}]

    def wait(self,key):
        deadline=time.monotonic()+2
        while time.monotonic()<deadline:
            value=self.manager.status(key,'owner','generation')
            if value['status']!='pending':return value
            time.sleep(.005)
        self.fail('validation did not finish')

    def test_every_request_rehashes_same_stat_content_and_consumes_once(self):
        scope={'epoch':0};key=self.manager.start('owner','generation',scope,self.sources)['continuation']['id']
        self.assertEqual(self.wait(key)['status'],'ready')
        def consume(scope,proof):
            self.assertEqual(self.manager.status(key,'owner','generation')['status'],'consumed')
            return 'accepted'
        self.assertEqual(self.manager.take(key,'owner','generation',consume),'accepted')
        with self.assertRaises(Exception):self.manager.take(key,'owner','generation',lambda s,p:'twice')
        before=self.path.stat();self.path.write_bytes(b'other');os.utime(self.path,ns=(before.st_atime_ns,before.st_mtime_ns))
        key=self.manager.start('owner','generation',scope,self.sources)['continuation']['id']
        self.assertEqual(self.wait(key)['error']['code'],'SOURCE_CHANGED')

    def test_slow_read_has_cancel_signal_and_never_late_promotes(self):
        entered=threading.Event();release=threading.Event();handles=[]
        class Slow:
            def __init__(inner,path):inner.stream=open(path,'rb');handles.append(inner.stream)
            def read(inner,n):entered.set();release.wait(2);return inner.stream.read(n)
            def fileno(inner):return inner.stream.fileno()
            def close(inner):inner.stream.close()
        self.manager.open_reader=Slow
        key=self.manager.start('owner','generation',{'epoch':0},self.sources)['continuation']['id']
        self.assertTrue(entered.wait(1));started=time.monotonic();self.manager.cancel_all()
        self.assertLess(time.monotonic()-started,.1)
        self.assertEqual(self.manager.status(key,'owner','generation')['status'],'canceled')
        release.set();self.manager.close()
        self.assertTrue(handles[0].closed)
        with self.assertRaises(Exception):self.manager.take(key,'owner','generation',lambda s,p:self.fail('late commit'))

    def test_ready_proof_keeps_read_handle_and_rejects_lost_lease_before_take(self):
        opened=[]
        def reader(path):
            stream=open(path,'rb');opened.append(stream);return stream
        self.manager.open_reader=reader
        key=self.manager.start('owner','generation',{'epoch':0},self.sources)['continuation']['id']
        self.assertEqual(self.wait(key)['status'],'ready');self.assertFalse(opened[0].closed)
        # An injected process exit loses the protected source handle. A dead
        # lease is never replaced by an old hash/stat assertion at final take.
        self.manager.items[key]['release'].set()
        deadline=time.monotonic()+1
        while not self.manager.quiescent() and time.monotonic()<deadline:time.sleep(.005)
        with self.assertRaises(Exception):self.manager.take(key,'owner','generation',lambda s,p:p.require())
        deadline=time.monotonic()+1
        while not opened[0].closed and time.monotonic()<deadline:time.sleep(.005)
        self.assertTrue(opened[0].closed)

    def test_bytes_replaced_during_hash_are_rejected_despite_same_stat(self):
        entered=threading.Event();release=threading.Event()
        class Slow:
            def __init__(inner,path):inner.stream=open(path,'rb')
            def read(inner,size):entered.set();release.wait(2);return inner.stream.read(size)
            def fileno(inner):return inner.stream.fileno()
            def close(inner):inner.stream.close()
        self.manager.open_reader=Slow
        key=self.manager.start('owner','generation',{'epoch':0},self.sources)['continuation']['id']
        self.assertTrue(entered.wait(1));before=self.path.stat();self.path.write_bytes(b'other')
        os.utime(self.path,ns=(before.st_atime_ns,before.st_mtime_ns));release.set()
        self.assertEqual(self.wait(key)['error']['code'],'SOURCE_CHANGED')

    def test_scope_force_deadline_reaps_an_uncooperative_read(self):
        from test_s2_workers import Scope
        entered=threading.Event();forced=threading.Event();handles=[]
        class Stuck:
            def __init__(inner,path):inner.stream=open(path,'rb');handles.append(inner.stream)
            def read(inner,size):entered.set();forced.wait(2);raise OSError('simulated owned process termination')
            def fileno(inner):return inner.stream.fileno()
            def close(inner):inner.stream.close()
        class ForceScope(Scope):
            def terminate_if_owned(inner):forced.set();return True
        self.manager.open_reader=Stuck;self.manager.scope_factory=ForceScope;self.manager.STOP_GRACE=.05
        key=self.manager.start('owner','generation',{'epoch':0},self.sources)['continuation']['id']
        self.assertTrue(entered.wait(1));self.manager.cancel_all();self.assertTrue(forced.wait(.5))
        self.manager.close();self.assertTrue(self.manager.quiescent());self.assertTrue(handles[0].closed)
        self.assertEqual(self.manager.status(key,'owner','generation')['status'],'canceled')

    def test_consumed_worker_with_stuck_close_is_also_force_reaped(self):
        from test_s2_workers import Scope
        entered=threading.Event();forced=threading.Event()
        class StuckClose:
            def __init__(inner,path):inner.stream=open(path,'rb')
            def read(inner,size):return inner.stream.read(size)
            def fileno(inner):return inner.stream.fileno()
            def close(inner):entered.set();forced.wait(2);inner.stream.close()
        class ForceScope(Scope):
            def terminate_if_owned(inner):forced.set();return True
        self.manager.open_reader=StuckClose;self.manager.scope_factory=ForceScope;self.manager.STOP_GRACE=.05
        key=self.manager.start('owner','generation',{'epoch':0},self.sources)['continuation']['id']
        self.assertEqual(self.wait(key)['status'],'ready')
        self.manager.take(key,'owner','generation',lambda s,p:p.require())
        self.assertTrue(entered.wait(.5));self.assertTrue(forced.wait(.5))
        self.manager.close();self.assertTrue(self.manager.quiescent())

    def test_expired_ready_scope_is_not_consumable_and_drains(self):
        key=self.manager.start('owner','generation',{'epoch':0},self.sources)['continuation']['id']
        self.assertEqual(self.wait(key)['status'],'ready')
        self.manager.items[key]['deadline']=time.monotonic()-1
        with self.assertRaises(Exception):self.manager.take(key,'owner','generation',lambda s,p:self.fail('expired commit'))
        self.manager.close();self.assertTrue(self.manager.quiescent())

    def test_windows_reader_requests_no_write_or_delete_sharing(self):
        from contentrium_cut.source_validation import read_lease
        from unittest.mock import Mock,patch
        api=Mock();api.CreateFileW.return_value=123
        descriptor=os.open(self.path,os.O_RDONLY)
        with patch('contentrium_cut.source_validation.ctypes.WinDLL',return_value=api),patch('msvcrt.open_osfhandle',return_value=descriptor):
            with read_lease(self.path) as stream:self.assertEqual(stream.read(),b'first')
        args=api.CreateFileW.call_args.args
        self.assertEqual(args[1],0x80000000);self.assertEqual(args[2],1);self.assertEqual(args[4],3)


class ServiceValidationTests(unittest.TestCase):
    def setUp(self):
        from test_single_panel_service import SinglePanelTests
        self.case=SinglePanelTests('runTest');self.case.setUp();self.addCleanup(self.case.doCleanups)
        self.service=self.case.service;self.job=self.case.bind()
        self.state=self.case.request('/analyses/register',{'jobId':self.job,'epoch':0})[1]
        self.path=Path(self.case.analysis['sourceInputs'][0]['path'])

    def raw(self,path,body=None,method='POST',client=None):
        return self.case.signed_request(client or self.case.client,method,path,body)

    def ready(self,initial):
        self.assertEqual(initial[0],200,initial);pending=initial[1]['continuation']
        path='/continuations/'+pending['id'];deadline=time.monotonic()+2
        while time.monotonic()<deadline:
            progress=self.raw(path,method='GET')[1]
            if progress['status']!='pending':return path,progress
            time.sleep(.005)
        self.fail('validation did not finish')

    def plan(self):
        body=dict(jobId=self.job,analysisId=self.state['analysisId'],analysisRevision=self.state['revision'],mapping=self.case.mapping,policy=self.case.policy,epoch=0)
        result=self.case.request('/plan',body);self.assertEqual(result[0],200,result);return result[1],body

    def replace(self):
        old=self.path.stat();self.path.write_bytes(b'X'*old.st_size);os.utime(self.path,ns=(old.st_atime_ns,old.st_mtime_ns))

    def test_replaced_same_stat_sources_reject_read_plan_begin_and_first_permit(self):
        plan,body=self.plan();self.replace()
        for path,payload,method in [('/jobs/'+self.job,None,'GET'),('/analyses/'+self.state['analysisId'],None,'GET'),('/plan',body,'POST'),
                                    ('/apply/begin',dict(planHash=plan['planHash'],snapshotHash=self.case.snapshot['snapshotHash'],requestId='begin',epoch=0),'POST')]:
            with self.subTest(path=path):self.assertEqual(self.case.request(path,payload,method=method)[1]['error']['code'],'SOURCE_CHANGED')
        self.assertFalse(self.service.journal.records)
        self.path.write_bytes(b'original analysis media')
        begin=self.case.request('/apply/begin',dict(planHash=plan['planHash'],snapshotHash=self.case.snapshot['snapshotHash'],requestId='begin',epoch=0))[1]
        self.replace()
        result=self.case.request('/apply/check',dict(applyId=begin['applyId'],epoch=0,batchId=1,operationDigest='a'*64,resultSequenceRef=None))
        self.assertEqual(result[1]['error']['code'],'SOURCE_CHANGED')
        self.assertEqual(self.service.journal.records[begin['applyId']]['batches'],[])

    def test_ready_validation_rejects_correction_host_reconnect_and_epoch_changes(self):
        plan,body=self.plan()
        for change in ['correction','host','reconnect','stop']:
            with self.subTest(change=change):
                path,status=self.ready(self.raw('/plan',body));self.assertEqual(status['status'],'ready')
                if change=='correction':
                    changed=self.case.request('/analyses/'+self.state['analysisId']+'/correct',dict(expectedRevision=self.state['revision'],operation={'type':'name','speakerId':'A','name':'changed'},requestId='rename',epoch=0))
                    self.assertEqual(changed[0],200,changed)
                elif change=='host':self.case.host['createTime']='replaced'
                elif change=='reconnect':self.case.session()
                else:self.service.stop_all(1)
                code,value=self.raw(path+'/take',{'epoch':0})
                self.assertNotEqual(code,200,value)
                if change=='correction':
                    self.state=self.case.request('/analyses/'+self.state['analysisId'],method='GET')[1];body['analysisRevision']=self.state['revision']
                elif change=='host':self.case.host['createTime']='123'
                elif change=='reconnect':
                    # Use a fresh same-owner session and rebind only native scope.
                    self.case.client=self.case.session();self.case.heartbeat()
                    self.case.request('/project',{'snapshot':self.case.snapshot,'epoch':0,'hostIdentity':self.case.host})

    def test_slow_validation_does_not_hold_admission_auth_or_stop_locks(self):
        entered=threading.Event();release=threading.Event()
        admission=threading.RLock();self.service._guard=lambda epoch:admission
        class Slow:
            def __init__(inner,path):inner.stream=open(path,'rb')
            def read(inner,size):entered.set();release.wait(2);return inner.stream.read(size)
            def fileno(inner):return inner.stream.fileno()
            def close(inner):inner.stream.close()
        self.service.validations.open_reader=Slow
        code,initial=self.raw('/analyses/'+self.state['analysisId'],method='GET')
        self.assertEqual(code,200,initial);self.assertTrue(entered.wait(1))
        guard_free=threading.Event()
        def enter_guard():
            with admission:guard_free.set()
        guard_thread=threading.Thread(target=enter_guard);guard_thread.start();self.assertTrue(guard_free.wait(.25));guard_thread.join(1)
        self.assertEqual(self.case.heartbeat()[0],200)
        stopped=threading.Event();worker=threading.Thread(target=lambda:(self.service.stop_all(1),stopped.set()))
        worker.start();self.assertTrue(stopped.wait(.25));release.set();worker.join(1)
        path='/continuations/'+initial['continuation']['id']
        self.assertEqual(self.raw(path,method='GET')[1]['status'],'canceled')
        self.assertNotEqual(self.raw(path+'/take',{'epoch':0})[0],200)

    def test_lost_final_take_response_never_returns_a_second_permit(self):
        plan,_=self.plan()
        body=dict(planHash=plan['planHash'],snapshotHash=self.case.snapshot['snapshotHash'],requestId='lost-response',epoch=0)
        path,status=self.ready(self.raw('/apply/begin',body));self.assertEqual(status['status'],'ready')
        first=self.raw(path+'/take',{'epoch':0});self.assertTrue(first[1]['execute'])
        self.assertEqual(self.raw(path,method='GET')[1]['status'],'consumed')
        self.assertNotEqual(self.raw(path+'/take',{'epoch':0})[0],200)
        replay=self.raw('/apply/begin',body);self.assertFalse(replay[1]['execute']);self.assertEqual(replay[1]['applyId'],first[1]['applyId'])

    def test_all_three_input_hash_passes_are_async_and_cancel_without_late_permit(self):
        from test_single_panel_service import SinglePanelTests
        from contentrium_cut.input_capabilities import probe_sources
        for boundary in ['promotion','begin','first-batch']:
            with self.subTest(boundary=boundary):
                case=SinglePanelTests('runTest');case.setUp()
                try:
                    service=case.service;case.heartbeat();source=Path(case.tmp.name)/'input.mov';source.write_bytes(b'input media')
                    owner=service.auth.owner_id(case.client['id'])
                    selected=case.request('/input/sources',dict(projectRef='input',sources=[dict(assetId='one',path=str(source),name='one',bounds={'VIDEO':None,'AUDIO':None})],epoch=0))[1]
                    record=service.inputs.payload(selected['selectionId'],owner,'input')
                    result=probe_sources(record,probe=lambda p,c:{'streams':[{'index':0,'codec_type':'video'},{'index':1,'codec_type':'audio','channels':2}]})
                    service.jobs.jobs['probe']=dict(jobId='probe',kind='input-probe',status='completed',epoch=0,result=result)
                    service.record_job('probe',owner,None,None);service.owners['probe'].update(selectionId=selected['selectionId'],inputProject='input')
                    path='/input/capabilities/result';body={'jobId':'probe','epoch':0}
                    if boundary!='promotion':
                        status,cap=case.request(path,body);self.assertEqual(status,200,cap)
                        path='/input/begin';body=dict(capabilityId=cap['capabilityId'],choices=[dict(assetId='one',role='camera',outputAudio=True)],requestId='input',epoch=0)
                    if boundary=='first-batch':
                        status,begin=case.request(path,body);self.assertEqual(status,200,begin)
                        path='/apply/check';body=dict(applyId=begin['applyId'],epoch=0,batchId=1,operationDigest='a'*64,resultSequenceRef=None)
                    entered=threading.Event();release=threading.Event()
                    class Slow:
                        def __init__(inner,path):inner.stream=open(path,'rb')
                        def read(inner,size):entered.set();release.wait(2);return inner.stream.read(size)
                        def fileno(inner):return inner.stream.fileno()
                        def close(inner):inner.stream.close()
                    service.validations.open_reader=Slow
                    status,value=case.signed_request(case.client,'POST',path,body);self.assertEqual(status,200,value)
                    self.assertTrue(entered.wait(1));self.assertEqual(case.heartbeat()[0],200)
                    started=time.monotonic();service.stop_all(1);self.assertLess(time.monotonic()-started,.25);release.set()
                    key=value['continuation']['id'];self.assertNotEqual(case.signed_request(case.client,'POST','/continuations/'+key+'/take',{'epoch':0})[0],200)
                    self.assertTrue(all(not r['batches'] for r in service.journal.records.values()))
                finally:case.doCleanups()

    def test_slow_planner_runs_before_take_without_admission_locks_or_late_publish(self):
        from unittest.mock import patch
        from contentrium_cut import coordinator
        entered=threading.Event();release=threading.Event();stopped=threading.Event();observed=[];responses=[]
        admission=threading.RLock();self.service._guard=lambda epoch:admission
        actual=coordinator.plan_edit
        def slow(*args):
            observed.append((admission._is_owned(),self.service.lock._is_owned(),self.service.auth.lock._is_owned()))
            entered.set();release.wait(3);return actual(*args)
        body=dict(jobId=self.job,analysisId=self.state['analysisId'],analysisRevision=self.state['revision'],mapping=self.case.mapping,policy=self.case.policy,epoch=0)
        taker=None;stopper=None
        try:
            with patch('contentrium_cut.coordinator.plan_edit',side_effect=slow):
                initial=self.raw('/plan',body);self.assertEqual(initial[0],200,initial)
                pending='/continuations/'+initial[1]['continuation']['id']
                if not entered.wait(.3):
                    # Pre-fix reproduction: only final take starts the planner.
                    path,progress=self.ready(initial);self.assertEqual(progress['status'],'ready')
                    taker=threading.Thread(target=lambda:responses.append(self.raw(path+'/take',{'epoch':0})))
                    taker.start();self.assertTrue(entered.wait(1))
                if taker is None:
                    self.assertEqual(self.case.heartbeat()[0],200)
                    self.assertEqual(self.raw(pending,method='GET')[1]['status'],'pending')
                    self.assertEqual(self.raw(pending+'/take',{'epoch':0})[1]['error']['code'],'VALIDATION_PENDING')
                stopper=threading.Thread(target=lambda:(self.service.stop_all(1),stopped.set()));stopper.start()
                self.assertTrue(stopped.wait(.3),'planner holds the service/update admission locks')
                self.assertEqual(observed,[(False,False,False)])
                self.assertIsNone(taker,'plan computation must precede ready/take')
                self.assertNotEqual(self.raw(pending+'/take',{'epoch':0})[0],200)
        finally:
            release.set()
            if taker:taker.join(2)
            if stopper:stopper.join(2)
        owner=self.service.auth.owner_id(self.case.client['id'])
        self.assertEqual(self.service.coordinator.session(owner)['plans'],{})

    def test_ready_plan_is_published_once_without_recomputing_in_final_take(self):
        from unittest.mock import patch
        from contentrium_cut import coordinator
        calls=[];leases=[];actual=coordinator.plan_edit
        def reader(path):
            stream=open(path,'rb');leases.append(stream);return stream
        self.service.validations.open_reader=reader
        def compute(*args):
            self.assertTrue(leases);self.assertTrue(all(not stream.closed for stream in leases))
            calls.append(threading.current_thread().name);return actual(*args)
        body=dict(jobId=self.job,analysisId=self.state['analysisId'],analysisRevision=self.state['revision'],mapping=self.case.mapping,policy=self.case.policy,epoch=0)
        owner=self.service.auth.owner_id(self.case.client['id'])
        with patch('contentrium_cut.coordinator.plan_edit',side_effect=compute):
            path,status=self.ready(self.raw('/plan',body));self.assertEqual(status['status'],'ready')
        self.assertEqual(len(calls),1);self.assertEqual(self.service.coordinator.session(owner)['plans'],{})
        self.assertTrue(all(not stream.closed for stream in leases))
        with patch('contentrium_cut.coordinator.plan_edit',side_effect=AssertionError('final take recomputed plan')),patch.object(self.service.coordinator,'plan',side_effect=AssertionError('final take called sync adapter')):
            code,result=self.raw(path+'/take',{'epoch':0})
        self.assertEqual(code,200,result)
        self.assertEqual(self.service.coordinator.require_plan(owner,result['planHash'],self.case.snapshot['snapshotHash']),result)
        self.assertEqual(self.service.coordinator.session(owner)['planAnalyses'][result['planHash']],{'analysisId':self.state['analysisId'],'revision':self.state['revision']})
        self.assertNotEqual(self.raw(path+'/take',{'epoch':0})[0],200)

    def test_plan_preparation_authority_changes_never_publish_a_late_plan(self):
        from unittest.mock import patch
        from contentrium_cut import coordinator
        for change in ['revision','same-snapshot-rebind','host','reconnect','epoch','cancel']:
            with self.subTest(change=change):
                case=ServiceValidationTests('runTest');case.setUp()
                entered=threading.Event();release=threading.Event();actual=coordinator.plan_edit
                def slow(*args):entered.set();release.wait(2);return actual(*args)
                owner=case.service.auth.owner_id(case.case.client['id'])
                try:
                    body=dict(jobId=case.job,analysisId=case.state['analysisId'],analysisRevision=case.state['revision'],mapping=case.case.mapping,policy=case.case.policy,epoch=0)
                    with patch('contentrium_cut.coordinator.plan_edit',side_effect=slow):
                        initial=case.raw('/plan',body);self.assertEqual(initial[0],200,initial);self.assertTrue(entered.wait(1))
                        path='/continuations/'+initial[1]['continuation']['id']
                        if change=='revision':
                            code,value=case.case.request('/analyses/'+case.state['analysisId']+'/correct',dict(expectedRevision=0,operation={'type':'name','speakerId':'A','name':'new'},requestId='during-plan',epoch=0))
                            self.assertEqual(code,200,value)
                        elif change=='same-snapshot-rebind':
                            self.assertEqual(case.raw('/project',dict(snapshot=case.case.snapshot,epoch=0,hostIdentity=case.case.host))[0],200)
                        elif change=='host':case.case.host['createTime']='new-host'
                        elif change=='reconnect':case.case.client=case.case.session();case.case.heartbeat()
                        elif change=='epoch':case.service.stop_all(1)
                        else:self.assertEqual(case.raw(path+'/cancel',{})[0],200)
                        release.set()
                        deadline=time.monotonic()+1
                        while time.monotonic()<deadline:
                            code,value=case.raw(path,method='GET')
                            if code!=200 or value['status']!='pending':break
                            time.sleep(.005)
                        self.assertNotEqual(case.raw(path+'/take',{'epoch':0})[0],200)
                    self.assertEqual(case.service.coordinator.session(owner)['plans'],{})
                    self.assertEqual(case.service.coordinator.session(owner)['planAnalyses'],{})
                finally:release.set();case.doCleanups()

    def test_uncooperative_planner_is_force_reaped_and_cannot_publish(self):
        from unittest.mock import patch
        from test_s2_workers import Scope
        from contentrium_cut import coordinator
        entered=threading.Event();forced=threading.Event();actual=coordinator.plan_edit
        class ForceScope(Scope):
            def terminate_if_owned(inner):forced.set();return True
        self.service.validations.scope_factory=ForceScope;self.service.validations.STOP_GRACE=.05
        def stubborn(*args):entered.set();forced.wait(2);return actual(*args)
        body=dict(jobId=self.job,analysisId=self.state['analysisId'],analysisRevision=self.state['revision'],mapping=self.case.mapping,policy=self.case.policy,epoch=0)
        with patch('contentrium_cut.coordinator.plan_edit',side_effect=stubborn):
            initial=self.raw('/plan',body);self.assertEqual(initial[0],200,initial);self.assertTrue(entered.wait(1))
            self.service.stop_all(1);self.assertTrue(forced.wait(.5));deadline=time.monotonic()+1
            while not self.service.validations.quiescent() and time.monotonic()<deadline:time.sleep(.005)
        self.assertTrue(self.service.validations.quiescent())
        owner=self.service.auth.owner_id(self.case.client['id']);self.assertEqual(self.service.coordinator.session(owner)['plans'],{})
        path='/continuations/'+initial[1]['continuation']['id']
        self.assertNotEqual(self.raw(path+'/take',{'epoch':0})[0],200)
