"""Owned, cancelable SHA validation; the worker retains read leases until take.

No proof is reusable by another request. Windows denies write/delete sharing
through the final commit. Controller cancellation has an independent scope
termination deadline, even when a filesystem read never returns.
"""
import copy
import ctypes
from ctypes import wintypes
import hashlib
import multiprocessing as mp
import os
from pathlib import Path
import queue
import re
import threading
import time
import uuid
from .contract import CutError
from .jobs import _scoped_worker
from .process_scope import ProcessScope


def fail(code='SOURCE_CHANGED'):
    raise CutError(code,'Source validation must finish for this exact operation.')


def read_lease(path):
    """Production Windows read-only handle, denying concurrent writers/deletion."""
    if os.name!='nt':fail('SOURCE_LEASE_UNAVAILABLE')
    import msvcrt
    api=ctypes.WinDLL('kernel32',use_last_error=True)
    api.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
    api.CreateFileW.restype=wintypes.HANDLE
    api.CloseHandle.argtypes=[wintypes.HANDLE]
    handle=api.CreateFileW(str(path),0x80000000,1,None,3,0x08000000,None)
    if handle==ctypes.c_void_p(-1).value:fail()
    try:descriptor=msvcrt.open_osfhandle(handle,os.O_RDONLY|os.O_BINARY)
    except BaseException:api.CloseHandle(handle);raise
    return os.fdopen(descriptor,'rb',buffering=0)


def stat_key(value):
    return [value.st_size,value.st_mtime_ns,value.st_ctime_ns,value.st_ino,value.st_dev]


def validate_worker(sources,cancel,release,result,reader,preparation=None,permits=None):
    from .windows_install import _guard_path
    handles=[]
    try:
        proof=[];seen={}
        for source in sources:
            if cancel.is_set():fail('CANCELED')
            path=Path(source['path']);_guard_path(path)
            if not path.is_absolute() or not re.fullmatch('[0-9a-f]{64}',source.get('sha256','')):fail()
            key=os.path.normcase(str(path))
            if key in seen:
                if seen[key]!=source['sha256']:fail()
                continue
            stream=reader(path);handles.append(stream);before=stat_key(os.fstat(stream.fileno()));path_before=stat_key(path.stat())
            # Python's Windows fstat/path.stat have different ctime semantics.
            # Compare each clock to itself; file identity must match across APIs.
            if [before[i] for i in [0,1,3,4]]!=[path_before[i] for i in [0,1,3,4]]:fail()
            digest=hashlib.sha256()
            while True:
                if cancel.is_set():fail('CANCELED')
                block=stream.read(1024*1024)
                if not block:break
                digest.update(block)
            if (cancel.is_set() or digest.hexdigest()!=source['sha256'] or before!=stat_key(os.fstat(stream.fileno())) or
                path_before!=stat_key(path.stat())):fail('CANCELED' if cancel.is_set() else 'SOURCE_CHANGED')
            proof.append({'path':str(path),'stat':path_before});seen[key]=source['sha256']
        if cancel.is_set():fail('CANCELED')
        prepared=None
        if preparation is not None:
            if preparation.get('kind')=='cache-prune':
                from contextlib import contextmanager
                from .resource import prune_completed_cache
                @contextmanager
                def authorize(entry=None):
                    if cancel.is_set():fail('CANCELED')
                    result.put({'boundary':entry})
                    while True:
                        if cancel.is_set():fail('CANCELED')
                        try:allowed=permits.get(timeout=.05);break
                        except queue.Empty:pass
                    if allowed is not True or cancel.is_set():fail('CANCELED')
                    yield True
                prepared=prune_completed_cache(preparation['root'],preparation['budget'],authorize,cancel=cancel.is_set)
            elif preparation.get('kind')=='edit-plan':
                from .coordinator import prepare_plan
                prepared=prepare_plan(preparation['request'],cancel=cancel.is_set)
            elif preparation.get('kind')=='sync-plan':
                from .coordinator import prepare_sync_plan
                prepared=prepare_sync_plan(preparation['request'],cancel=cancel.is_set)
            elif preparation.get('kind')=='model-revision':
                from .model_setup import community_revision
                revision=community_revision(preparation.pop('token'))
                if not isinstance(revision,str) or not re.fullmatch('[0-9a-f]{40}',revision):fail('MODEL_NOT_READY')
                prepared={'revision':revision}
            else:fail('INVALID_REQUEST')
        if cancel.is_set():fail('CANCELED')
        result.put({'proof':proof,'prepared':prepared})
        # The controller owns expiry and a pinned-scope kill timer. Do not close
        # source leases between ready and the controller's final commit.
        while not release.wait(.05):
            if cancel.is_set():return
    except CutError as error:result.put({'error':{'code':error.code}})
    except BaseException:result.put({'error':{'code':'SOURCE_CHANGED'}})
    finally:
        for stream in reversed(handles):stream.close()


class Proof:
    def __init__(self,item):self.item=item
    def require(self):
        item=self.item
        if item['cancel'].is_set() or 'process' not in item or not item['process'].is_alive():fail('VALIDATION_STALE')
        # The assigned worker still owns the exact no-write/no-delete handles
        # used for SHA validation. Even stat(path) may block on a disconnected
        # source drive, so there is no source filesystem I/O in final admission.
        if not item.get('proof') and item['scope'].get('path') not in {'/resources/prune','/models/community-1/revision'}:fail('VALIDATION_STALE')

    def prepared_plan(self):
        self.require()
        result=self.item.get('prepared')
        if not isinstance(result,dict):fail('VALIDATION_STALE')
        return result


class Validations:
    TTL=1800
    READY_TTL=60
    STOP_GRACE=3
    def __init__(self,open_reader=None,context=None,scope_factory=None):
        self.open_reader=open_reader or read_lease
        self.context=context or mp.get_context('spawn');self.scope_factory=scope_factory or ProcessScope
        self.lock=threading.RLock();self.items={};self.closed=threading.Event();self.failed=False
        self.monitor=threading.Thread(target=self._monitor,daemon=True,name='Contentrium-CUT-source-validation')
        self.monitor.start()

    def start(self,owner,generation,scope,sources,preparation=None,boundary=None):
        maintenance=preparation is not None and preparation.get('kind')=='cache-prune' and callable(boundary)
        metadata=preparation is not None and preparation.get('kind')=='model-revision' and scope.get('path')=='/models/community-1/revision'
        if not isinstance(sources,list) or not (0 if maintenance or metadata else 1)<=len(sources)<=128:fail()
        with self.lock:
            if self.closed.is_set() or self.failed:fail('VALIDATION_UNAVAILABLE')
            if sum('process' in r for r in self.items.values())>=4:fail('VALIDATION_BUSY')
            for key in list(self.items):
                if len(self.items)<64:break
                if 'process' not in self.items[key]:del self.items[key]
            if len(self.items)>=64:fail('VALIDATION_BUSY')
            key=uuid.uuid4().hex;native=self.scope_factory()
            event=self.context.Event();start=self.context.Event();release=self.context.Event();result=self.context.Queue();permits=self.context.Queue()
            process=self.context.Process(target=_scoped_worker,args=(start,event,validate_worker,(copy.deepcopy(sources),event,release,result,self.open_reader,copy.deepcopy(preparation),permits)),name='Contentrium-CUT-source-validation')
            process.daemon=True
            item=dict(id=key,owner=owner,generation=generation,scope=copy.deepcopy(scope),status='pending',cancel=event,
                      release=release,queue=result,permits=permits,boundary=boundary,process=process,native=native,deadline=time.monotonic()+self.TTL)
            try:
                process.start();native.assign(process);item['workerPid']=process.pid;self.items[key]=item;start.set()
            except BaseException:
                event.set();native.close()
                if process.pid is not None:
                    if process.is_alive():process.terminate()
                    process.join(timeout=2);process.close()
                result.close();permits.close();raise
        return {'continuation':{'id':key,'status':'pending','pollAfterMs':250,'expiresInMs':self.TTL*1000,'epoch':scope['epoch']}}

    def _owned(self,key,owner,generation):
        item=self.items.get(key)
        if not item or item['owner']!=owner or item['generation']!=generation:fail('VALIDATION_SCOPE')
        return item

    def status(self,key,owner,generation):
        with self.lock:
            item=self._owned(key,owner,generation)
            return dict(id=key,status='consumed' if item['status']=='consuming' else item['status'],**({'error':item['error']} if item.get('error') else {}))

    def _watch_exit(self,item):
        if 'timer' not in item and 'process' in item:
            timer=threading.Timer(self.STOP_GRACE,self._force,args=(item,));timer.daemon=True;item['timer']=timer;timer.start()

    def _cancel(self,item,code='CANCELED'):
        if item['status'] in {'pending','ready','consuming'}:
            item.update(status='canceled',error={'code':code});item['cancel'].set()
        # Terminal receipt is not proof that a blocked close/descendant exited.
        if 'process' in item:item['cancel'].set();self._watch_exit(item)

    def _force(self,item):
        try:item['native'].terminate_if_owned()
        except BaseException:self.failed=True

    def cancel(self,key,owner,generation):
        with self.lock:
            item=self._owned(key,owner,generation);self._cancel(item)
            return {'id':key,'status':item['status']}

    def cancel_all(self):
        with self.lock:
            for item in self.items.values():self._cancel(item)

    def cancel_owner(self,owner,generation):
        with self.lock:
            for item in self.items.values():
                if item['owner']==owner and item['generation']!=generation:self._cancel(item,'SESSION_EXPIRED')

    def take(self,key,owner,generation,commit):
        with self.lock:
            item=self._owned(key,owner,generation)
            if item['status']!='ready':fail(item.get('error',{}).get('code','VALIDATION_'+item['status'].upper()))
            if time.monotonic()>item['deadline']:self._cancel(item,'VALIDATION_EXPIRED');fail('VALIDATION_EXPIRED')
            item['status']='consuming'
        try:return commit(item['scope'],Proof(item))
        finally:
            with self.lock:
                item['status']='consumed';item['release'].set();self._watch_exit(item)

    def quiescent(self):
        with self.lock:return not any('process' in item for item in self.items.values()) and not self.failed

    def _monitor(self):
        while not self.closed.wait(.02):
            boundaries=[]
            with self.lock:
                for item in list(self.items.values()):
                    if 'process' not in item:continue
                    try:
                        if time.monotonic()>item['deadline'] and item['status'] in {'pending','ready'}:self._cancel(item,'VALIDATION_EXPIRED')
                        try:message=item['queue'].get_nowait()
                        except queue.Empty:message=None
                        if message is None and not item['process'].is_alive() and item['status']=='pending':
                            # A spawn queue's final error can arrive between the
                            # nonblocking poll and observing process exit.
                            try:message=item['queue'].get(timeout=.05)
                            except queue.Empty:pass
                        if message and item['status']=='pending':
                            if 'boundary' in message:boundaries.append((item,message['boundary']))
                            elif 'proof' in message:item.update(status='ready',proof=message['proof'],prepared=message.get('prepared'),deadline=min(item['deadline'],time.monotonic()+self.READY_TTL))
                            else:
                                item.update(status='failed',error=message.get('error',{'code':'SOURCE_CHANGED'}));item['release'].set();self._watch_exit(item)
                        process=item['process']
                        if not process.is_alive():
                            if item['native'].active_count():item['native'].terminate_if_owned();continue
                            process.join(timeout=0)
                            if item['status'] in {'pending','ready','consuming'}:item.update(status='failed',error={'code':'VALIDATION_WORKER_EXITED'})
                            if item.get('timer'):item['timer'].cancel()
                            item['native'].close();item['queue'].close();item['permits'].close();process.close();del item['process']
                    except BaseException:
                        self.failed=True;self._cancel(item,'VALIDATION_UNAVAILABLE')
            # Never acquire service/auth/admission locks while holding ours.
            for item,entry in boundaries:
                try:allowed=item['boundary'](entry) is True
                except Exception:allowed=False
                with self.lock:
                    if item['status']=='pending' and not item['cancel'].is_set():item['permits'].put(allowed)

    def close(self,timeout=5):
        self.cancel_all();deadline=time.monotonic()+timeout
        while not self.quiescent() and time.monotonic()<deadline:time.sleep(.02)
        if not self.quiescent():fail('HANDOFF_DRAIN_FAILED')
        self.closed.set();self.monitor.join(timeout=1)
