"""Durable owned-process jobs, with one admission epoch for update interruption."""
import hashlib
import json
import multiprocessing as mp
import os
from pathlib import Path
import queue
import threading
import time
import uuid
from contextlib import nullcontext

from .contract import CutError, canonical_hash
from .cache import ENGINE_SCHEMA, envelope, load as load_cache, validate_result
from .process_scope import ProcessScope

TERMINAL={'completed','failed','canceled','interrupted'}

def atomic_json(path, value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    pending=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with pending.open('w',encoding='utf-8') as stream:
            json.dump(value,stream,ensure_ascii=False,allow_nan=False);stream.flush();os.fsync(stream.fileno())
        os.replace(pending,path)
    finally:pending.unlink(missing_ok=True)

def _fingerprints(sources,cancel):
    values=[]
    for source in sources:
        path=Path(source['path']).resolve(strict=True);before=path.stat();digest=hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda:stream.read(1024*1024),b''):
                if cancel.is_set():raise CutError('CANCELED','Operation canceled')
                digest.update(block)
        after=path.stat()
        if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise CutError('SOURCE_CHANGED','Source changed during analysis.')
        values.append({'path':str(path),'size':after.st_size,'hash':digest.hexdigest()})
    return values

def _worker(kind,payload,cancel,result,cache_root):
    os.environ['HF_HUB_OFFLINE']='1' if kind!='model-setup' else '0'
    os.environ['PYANNOTE_METRICS_ENABLED']='0'
    os.environ['DO_NOT_TRACK']='1'
    try:
        key=None;fingerprints=None;media_fingerprints=None
        if kind in {'sync','analysis'}:
            media_fingerprints=_fingerprints(payload.get('mediaSources',payload.get('sources',[])),cancel)
            fingerprints=_fingerprints(payload.get('sources',[]),cancel)
            key=canonical_hash({'kind':kind,'payload':payload,'files':fingerprints,'media':media_fingerprints,'engineSchema':ENGINE_SCHEMA})
            cached=Path(cache_root)/(key+'.json')
            if cached.is_file():
                value=load_cache(cached,kind,key,payload)
                if value is not None:
                    if fingerprints!=_fingerprints(payload.get('sources',[]),cancel):raise CutError('SOURCE_CHANGED','Source changed while reading cached analysis.')
                    if media_fingerprints!=_fingerprints(payload.get('mediaSources',payload.get('sources',[])),cancel):raise CutError('SOURCE_CHANGED','Sequence media changed while reading cached analysis.')
                    if cancel.is_set():raise CutError('CANCELED','Operation canceled')
                    result.put({'ok':True,'value':value,'cacheKey':key});return
        if kind=='sync':
            from .audio import sync_sources
            value=sync_sources(payload['sources'],payload['reference'],payload['fps'],cancel=cancel.is_set)
        elif kind=='analysis':
            from .audio import analyze_audio
            value=analyze_audio(payload['mode'],payload['sources'],payload['settings'],cancel=cancel.is_set)
        elif kind=='model-setup':
            from .models import ModelManager
            value=ModelManager(payload['modelRoot']).install_community(token=payload['token'],terms_accepted=payload['termsAccepted'],revision=payload['revision'],cancel=cancel.is_set)
        elif kind=='example':
            from .coordinator import Coordinator
            coordinator=Coordinator(payload['root']);coordinator.bind(payload['owner'],payload['snapshot'])
            state=coordinator.analysis_state(payload['owner'],payload['analysisId'])
            if state['revision']!=payload['revision']:raise CutError('EXAMPLE_SCOPE','Analysis revision changed.')
            value=coordinator.render_example(payload['owner'],payload['analysisId'],payload['exampleId'],cancel=cancel.is_set)
        elif kind=='input-probe':
            from .input_capabilities import probe_sources
            value=probe_sources(payload,cancel=cancel.is_set)
        else:raise CutError('INVALID_JOB','Unsupported worker operation')
        if media_fingerprints is not None and 'mediaSources' in payload:
            if media_fingerprints!=_fingerprints(payload['mediaSources'],cancel):raise CutError('SOURCE_CHANGED','Sequence media changed during analysis.')
            value.update(mediaSnapshotHash=payload['mediaSnapshotHash'],mediaInputs=[dict(assetId=source['assetId'],path=identity['path'],sha256=identity['hash']) for source,identity in zip(payload['mediaSources'],media_fingerprints)])
        if key:validate_result(kind,value,payload)
        if fingerprints is not None and fingerprints!=_fingerprints(payload.get('sources',[]),cancel):raise CutError('SOURCE_CHANGED','Source changed during analysis.')
        if cancel.is_set():raise CutError('CANCELED','Operation canceled')
        result.put({'ok':True,'value':value,'cacheKey':key})
    except CutError as error:
        failure={'code':error.code,'message':'Local operation could not finish.'} if kind=='model-setup' else {'code':error.code,'message':error.message,'details':error.details}
        result.put({'ok':False,'error':failure})
    except BaseException:result.put({'ok':False,'error':{'code':'WORKER_FAILED','message':'Local worker failed. See the operation and model status.'}})

def _scoped_worker(start,cancel,target,args):
    # Imports/model/decode/native descendants cannot begin before assignment.
    deadline=time.monotonic()+30
    while not start.wait(.1):
        if cancel.is_set() or time.monotonic()>=deadline:return
    if not cancel.is_set():target(*args)

class JobManager:
    def __init__(self,root,admit=None,admission_guard=None):
        self.root=Path(root);self.lock=threading.RLock();self.epoch=0;self.gate_open=True
        self.admit=admit;self.admission_guard=admission_guard;self.jobs={};self.processes={};self.closed=threading.Event();self.context=mp.get_context('spawn')
        (self.root/'jobs').mkdir(parents=True,exist_ok=True);(self.root/'cache').mkdir(exist_ok=True)
        for path in (self.root/'jobs').glob('*.json'):
            try:
                state=json.loads(path.read_text(encoding='utf-8'))
                if state['status'] not in TERMINAL:
                    state.update(status='interrupted',error={'code':'INTERRUPTED','message':'The previous process stopped. Start a new operation explicitly.'})
                    atomic_json(path,state)
                self.jobs[state['jobId']]=state
            except (ValueError,KeyError,OSError):continue
        self.monitor=threading.Thread(target=self._monitor,daemon=True);self.monitor.start()

    def _save(self,state):atomic_json(self.root/'jobs'/(state['jobId']+'.json'),state)

    def assert_admitted(self,epoch=None):
        if not self.gate_open or (epoch is not None and epoch!=self.epoch):raise CutError('UPDATE_IN_PROGRESS','Contentrium CUT work is stopped for an update.')
        if self.admit:self.admit(epoch)

    def submit(self,kind,payload,expected_epoch=None):
        if kind not in {'sync','analysis','model-setup','example','input-probe'}:raise CutError('INVALID_JOB','Unsupported job operation')
        expected_epoch=self.epoch if expected_epoch is None else expected_epoch
        with self.admission_guard(expected_epoch) if self.admission_guard else nullcontext(), self.lock:
            self.assert_admitted(expected_epoch)
            if self.processes:raise CutError('JOB_BUSY','Another local operation is active.')
            job_id=uuid.uuid4().hex;state={'jobId':job_id,'kind':kind,'status':'running','epoch':self.epoch,'createdAt':time.time(),'cacheKey':None}
            scope=ProcessScope();process=None;result=None;event=None
            try:
                event=self.context.Event();start=self.context.Event();result=self.context.Queue()
                process=self.context.Process(target=_scoped_worker,args=(start,event,_worker,(kind,payload,event,result,str(self.root/'cache'))),name='Contentrium-CUT-analysis')
                process.daemon=True
                self.assert_admitted(expected_epoch)
                process.start();scope.assign(process)
                state['workerPid']=process.pid;self.jobs[job_id]=state
                self.processes[job_id]={'process':process,'scope':scope,'cancel':event,'queue':result,'cancelAt':None,'received':False}
                self._save(state);start.set();return self.get(job_id)
            except BaseException:
                if event:event.set()
                scope.close()
                if process is not None and process.pid is not None:
                    if process.is_alive():process.terminate()
                    process.join(timeout=2);process.close()
                if result:result.close()
                self.processes.pop(job_id,None)
                self.jobs.pop(job_id,None)
                raise

    def commit_result(self,job_id,epoch,value,key=None):
        pending=self.root/'cache'/(uuid.uuid4().hex+'.pending.json') if key else None
        try:
            if pending:
                with self.lock:state=self.jobs.get(job_id);kind=state.get('kind') if state else None
                # No cache is staged for jobs that are already absent/stale.
                if not state:return False
                atomic_json(pending,envelope(kind,key,value))
            try:
                with self.admission_guard(epoch) if self.admission_guard else nullcontext(), self.lock:
                    self.assert_admitted(epoch)
                    state=self.jobs.get(job_id)
                    if not state or state['status']!='running':return False
                    if pending:os.replace(pending,self.root/'cache'/(key+'.json'))
                    state.update(status='completed',result=value,cacheKey=key,finishedAt=time.time());self._save(state);return True
            except CutError:return False
        finally:
            if pending:pending.unlink(missing_ok=True)

    def get(self,job_id):
        with self.lock:
            if job_id not in self.jobs:raise CutError('JOB_NOT_FOUND','Job no longer exists.')
            return json.loads(json.dumps(self.jobs[job_id]))

    def list(self):
        with self.lock:return [self.get(key) for key in self.jobs]

    def cancel(self,job_id):
        with self.lock:
            item=self.processes.get(job_id)
            if item:
                item['cancel'].set()
                if item['cancelAt'] is None:
                    item['cancelAt']=time.monotonic()
                    # Completion serialization can block the monitor; shutdown's
                    # deadline must have an independent owner in the controller.
                    timer=threading.Timer(3,self._force_stop,args=(job_id,item))
                    timer.daemon=True;item['stopTimer']=timer;timer.start()
            state=self.jobs.get(job_id)
            if state and state['status']=='running':state['status']='canceling';self._save(state)

    def _force_stop(self,job_id,item):
        # A pinned scope can only refer to this job, even after registry removal.
        # Its lifetime lock synchronizes with close; jobs.lock may be blocked on
        # disk and must never be required to enforce the cancellation deadline.
        try:item['scope'].terminate_if_owned()
        except CutError as error:
            self.gate_open=False
            item['scopeError']={'code':error.code,'message':error.message,'details':error.details}

    def stop_all(self,epoch):
        with self.lock:
            self.gate_open=False;self.epoch=epoch
            for job_id in tuple(self.processes):self.cancel(job_id)

    def reopen(self,epoch):
        with self.lock:
            if not self.quiescent():raise CutError('JOB_BUSY','Workers have not exited.')
            self.epoch=epoch;self.gate_open=True

    def quiescent(self):
        with self.lock:return not self.processes

    def _receive(self,job_id,item,message):
        with self.lock:state=self.jobs[job_id];epoch=state['epoch']
        item['received']=True
        if not item['cancel'].is_set() and message.get('ok') and self.commit_result(job_id,epoch,message['value'],message.get('cacheKey')):return
        with self.lock:
            if item['cancel'].is_set() or not self.gate_open or message.get('ok'):
                state.update(status='canceled',finishedAt=time.time())
            else:state.update(status='failed',error=message.get('error',{'code':'WORKER_FAILED','message':'Invalid worker response.'}),finishedAt=time.time())
            self._save(state)

    def _monitor(self):
        while not self.closed.wait(.05):
            with self.lock:owned=tuple(self.processes.items())
            for job_id,item in owned:
                try:
                    process=item['process'];state=self.jobs[job_id]
                    if item['cancelAt'] and time.monotonic()-item['cancelAt']>=3:
                        item['scope'].terminate()
                    try:message=item['queue'].get_nowait()
                    except queue.Empty:message=None
                    if message:self._receive(job_id,item,message)
                    if not process.is_alive():
                        # A worker exiting is not proof that its native descendants exited.
                        if item['scope'].active_count():
                            item['scope'].terminate()
                            continue
                        process.join(timeout=0)
                        if not item['received']:
                            try:self._receive(job_id,item,item['queue'].get(timeout=.1))
                            except queue.Empty:pass
                        if not item['received']:
                            state.update(status='canceled' if item['cancel'].is_set() else 'failed',error={'code':'CANCELED' if item['cancel'].is_set() else 'WORKER_EXITED','message':'Local worker exited.'},finishedAt=time.time());self._save(state)
                        with self.lock:
                            if item.get('stopTimer'):item['stopTimer'].cancel()
                            item['scope'].close();item['queue'].close();process.close()
                            self.processes.pop(job_id,None)
                except Exception:
                    item['cancel'].set();item['cancelAt']=item['cancelAt'] or time.monotonic()
                    with self.lock:
                        state.update(status='failed',error={'code':'JOB_STATE_FAILED','message':'Unable to commit local job state.'})

    def close(self):
        self.stop_all(self.epoch+1)
        deadline=time.monotonic()+5
        while not self.quiescent() and time.monotonic()<deadline:time.sleep(.05)
        if not self.quiescent():raise CutError('PROCESS_SCOPE_FAILED','Owned workers have not drained; work remains stopped.')
        self.closed.set();self.monitor.join(timeout=1)
