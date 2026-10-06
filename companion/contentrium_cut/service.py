"""Authenticated native-panel loopback boundary and independent update manager.

Windows process/installation evidence is injected by the native launcher. The
HTTP client never supplies worker payloads, credentials, installer paths or a
process-exit assertion. Pair codes are issued only through the native AuthManager.
"""
import copy
from contextlib import nullcontext, contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import re
import socket
import threading
import time
import uuid

from .auth import AuthManager
from .contract import CutError, integer, canonical_hash
from .apply_journal import ApplyJournal
from .input_capabilities import InputCapabilities
from .source_validation import Validations
from .coordinator import Coordinator
from .cache import result_digest
from .jobs import JobManager, TERMINAL, atomic_json
from .models import ModelManager
from .updater import UpdateManager, PRE_REPLACE

PORT = 41737
MAX_BODY = 2 * 1024 * 1024


def _reject(code, message='Request cannot be accepted.'):
    raise CutError(code, message)


def _json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate JSON key')
            result[key] = value
        return result
    result = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                        parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))
    if not isinstance(result, dict):
        raise ValueError('JSON object required')
    return result


class CutService:
    @staticmethod
    def _error_message(code):
        return {'APPLY_CAPACITY_EXCEEDED':'The edit exceeds the 8192-batch journal capacity. Split the review range or reduce cut density.',
                'SOURCE_REANALYSIS_REQUIRED':'Reanalyze the connected sequence to capture all source media.'}.get(code,'Request could not be completed.')

    def __init__(self, root, config, installation=None, process_probe=None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.config = dict(config)
        self.installation = installation
        self.process_probe = process_probe
        self.lock = threading.RLock()
        self.closed = threading.Event()
        self.draining = False
        self.dispatch_condition = threading.Condition()
        self.active_dispatches = 0
        self.update_check = None
        self.connections = set()
        self.listeners = []
        self.drain_lock = threading.Lock()
        self.resources_closed = False
        self.auth = AuthManager(self.root, bootstrap=config.get('privateBootstrap'), config=config)
        self.coordinator = Coordinator(self.root)
        self.inputs = InputCapabilities(self.root)
        self.journal = ApplyJournal(self.root)
        self.panels, self.applies, self.workers = {}, self.journal.records, {}
        self.maintenance = False
        self.stop_serial = 0
        self.auth.context_busy = self._context_busy
        self.ownership_path = self.root / 'service-job-owners.json'
        self.owners = {}
        try:
            if self.ownership_path.is_file():
                saved = json.loads(self.ownership_path.read_text(encoding='utf-8'))
                if isinstance(saved, dict):
                    self.owners = saved
        except (ValueError, OSError):
            pass  # Missing ownership always denies access to a recovered job.
        self.updater = None
        if installation is not None and config.get('publicKey'):
            try:
                self.updater = UpdateManager(self.root, config['publicKey'], config['appVersion'], self.stop_all,
                                             installation, participants=self.participants,
                                             participant_exited=self.participant_exited,
                                             transport=config.get('updateTransport'),
                                             activation_handoff=config.get('activationHandoff'),
                                             signing_key_id=config.get('signingKeyId', 'contentrium-cut-2026-01'))
            except (ValueError, TypeError):
                pass  # Missing/invalid pinned signing key must never imply CURRENT.
        self.epoch = self.updater.state()['updateEpoch'] if self.updater else 0
        self.gate_open = self.updater.gate_open if self.updater else not (self.root / 'updates' / 'journal.json').exists()
        self.jobs = JobManager(self.root, admit=self.updater.assert_admitted if self.updater else None)
        self.validations = Validations(config.get('sourceReader'),config.get('validationContext'),config.get('validationScope'))
        if self.updater and hasattr(self.updater, 'admission_guard'):
            self.jobs.admission_guard = self.updater.admission_guard
        if not self.gate_open:
            self.jobs.stop_all(self.epoch)
        else:
            self.jobs.epoch = self.epoch
        self.servers = []
        self.manager = threading.Thread(target=self._manager_loop, name='Contentrium-CUT-update-manager', daemon=True)
        self.manager.start()

    def _owner(self, token):
        return self.auth.owner_id(token)

    def _context_busy(self, owner):
        # S2 may retain an unresolved apply after the real host has exited.
        # Permit diagnostic auth then; never infer exit from heartbeat timeout.
        if self._host_exited(): return False
        # Auth owns its lock here. Avoid reversing the service->auth lock order
        # used by project binding; a bounded GIL snapshot is sufficient because
        # in-flight MAC dispatches independently prevent context takeover.
        panel=self.panels.get(owner,{})
        active=[value for value in list(self.applies.values()) if value.get('owner')==owner and value.get('active')]
        if not active and panel.get('batchRunning') is not True:return False
        identities=[value.get('hostIdentity') or panel.get('projectHost') or panel.get('host') for value in active]
        if not identities:identities=[panel.get('projectHost') or panel.get('host')]
        probe=getattr(self.installation,'host_identity_exited',None)
        if callable(probe):
            try:
                if all(probe(identity) is True for identity in identities):return False
            except Exception:pass
        return True

    def _panel(self, token):
        session = self.auth.authenticate(token)
        owner = self._owner(token)
        with self.lock:
            # A reconnect retains durable ownership but must earn fresh host/project
            # evidence. Session IDs and keys remain memory-only.
            previous = self.panels.get(owner)
            generation = hashlib.sha256(token.encode()).hexdigest()
            if self.auth.private and previous and previous.get('sessionGeneration') != generation:
                self.validations.cancel_owner(owner,generation)
                previous.update(host=None, projectHost=None, epoch=None, appVersion=None, bundleId=None,
                                quiescent=False, heartbeatAt=None, heartbeatGeneration=None, protocolVersion=None,
                                heartbeatIdentity=None, sessionGeneration=generation)
            panel = self.panels.setdefault(owner, {'instanceId': session['instanceId'], 'host': None,
                                                   'lastSeen': time.monotonic(), 'epoch': None,
                                                   'stopEpoch': self.epoch if not self.gate_open else None,
                                                   'batchRunning': False, 'quiescent': False,
                                                   'appVersion': None, 'bundleId': None,
                                                   'heartbeatAt': None,
                                                   'protocolVersion': None,
                                                   'ownerId': owner, 'heartbeatGeneration': None,
                                                   'heartbeatIdentity': None,
                                                   'sessionGeneration': generation})
            panel['lastSeen'] = time.monotonic()
        return owner, session, panel

    def _host(self, panel, claim):
        if self.process_probe is None:
            _reject('HOST_IDENTITY_REQUIRED')
        actual = self.process_probe(panel['instanceId'], claim)
        if (not isinstance(actual, dict) or actual.get('alive') is not True or
                not isinstance(actual.get('pid'), int) or isinstance(actual['pid'], bool) or
                not actual.get('createTime') or 'premiere' not in str(actual.get('processName', '')).lower()):
            _reject('HOST_IDENTITY_REQUIRED')
        return copy.deepcopy(actual)

    def _snapshot(self, token, owner):
        snapshot = self.coordinator.session(owner)['snapshot']
        self.auth.require_project(token, snapshot['projectRef'])
        return snapshot

    def _project_host(self, panel):
        actual = self._host(panel, panel.get('projectHost'))
        bound = panel.get('projectHost')
        if not isinstance(bound, dict) or any(actual.get(key) != bound.get(key) for key in ['pid', 'createTime']):
            _reject('HOST_IDENTITY_REQUIRED')
        panel['host'] = actual

    def _admit(self, payload):
        if self.config.get('recoveryOnly'): _reject('RECOVERY_REQUIRED')
        if self.validations.failed:_reject('VALIDATION_UNAVAILABLE')
        epoch = integer(payload.get('epoch'), 'epoch')
        if self.closed.is_set() or not self.gate_open or self.maintenance or epoch != self.epoch:
            _reject('UPDATE_IN_PROGRESS')
        if self.updater:
            self.updater.assert_admitted(epoch)
        self.jobs.assert_admitted(epoch)
        return epoch

    def _compatible(self, panel, generation):
        if panel['sessionGeneration'] != generation: _reject('SESSION_EXPIRED')
        if self.auth.private and not self.auth.activation_session(panel['ownerId'],generation): _reject('SESSION_EXPIRED')
        if (panel.get('heartbeatAt') is None or not 0 <= time.monotonic()-panel['heartbeatAt'] <= 10 or
                panel.get('heartbeatGeneration') != generation): _reject('HEARTBEAT_REQUIRED')
        if (panel.get('appVersion') != self.config['appVersion'] or panel.get('bundleId') != self.config.get('bundleId') or
                type(panel.get('protocolVersion')) is not int or panel['protocolVersion'] != self.config.get('protocolVersion',1)):
            _reject('COMPONENT_MISMATCH')
        if panel.get('epoch') != self.epoch: _reject('UPDATE_IN_PROGRESS')

    @contextmanager
    def _work(self, body, panel, generation, *, apply_id=None, project=False):
        epoch = self._admit(body)
        self._compatible(panel,generation)
        bound = copy.deepcopy(panel.get('projectHost') if project else panel.get('host'))
        actual = self._host(panel,bound)  # A blocking OS probe does not own later session evidence.
        with self._guard(epoch), self.lock, self.auth.lock:
            self._admit(body); self._compatible(panel,generation)
            actual = self._host(panel,bound)
            self._compatible(panel,generation)
            current = panel.get('projectHost') if project else panel.get('host')
            if not isinstance(bound,dict) or not isinstance(current,dict) or any(actual.get(k)!=bound.get(k) or current.get(k)!=bound.get(k) for k in ['pid','createTime']):
                _reject('HOST_IDENTITY_REQUIRED')
            if self.journal.blocked(except_id=apply_id): _reject('APPLY_RECOVERY_REQUIRED')
            yield epoch

    def _analysis(self, token, owner, identity):
        self._snapshot(token,owner)
        # The coordinator's filesystem is project scoped; HTTP authority remains job-owner scoped.
        state = self.coordinator.analysis_state(owner,identity)
        matches = [job_id for job_id,record in self.owners.items() if record.get('owner')==owner and
                   record.get('snapshotHash')==state['snapshotHash'] and
                   canonical_hash({'jobId':job_id,'snapshotHash':state['snapshotHash'],'analysisHash':state['analysisHash']})==identity]
        if not matches: _reject('ANALYSIS_SCOPE')
        return state

    def _validation_target(self,method,path,body,token,owner,panel):
        """Capture immutable evidence only; never perform source I/O here."""
        state=None;job=None;record=None;raw=None;stamp={};apply_id=None
        match=re.fullmatch(r'/analyses/([0-9a-f]{64})',path)
        job_match=re.fullmatch(r'/jobs/([a-zA-Z0-9_-]+)',path)
        if method=='GET' and match:state=self._analysis(token,owner,match[1])
        elif (method=='POST' and path=='/analyses/register') or (method=='GET' and job_match):
            job=self._owned_job(body['jobId'] if path=='/analyses/register' else job_match[1],token,owner)
            if job['kind'] not in {'analysis','sync'} or job['status']!='completed':return None
            stamp['job']=result_digest(job)
        elif method=='POST' and path=='/sync-plan':
            if set(body)!={'jobId','selectedClipInstanceKeys','epoch'}:_reject('INVALID_REQUEST')
            job=self._owned_job(body['jobId'],token,owner)
            if job['kind']!='sync' or job['status']!='completed':_reject('SYNC_REQUIRED')
            stamp['job']=result_digest(job)
        elif method=='POST' and path=='/plan':
            if set(body)!={'jobId','analysisId','analysisRevision','mapping','policy','epoch'}:_reject('INVALID_REQUEST')
            job=self._owned_job(body['jobId'],token,owner)
            if job['kind']!='analysis' or job['status']!='completed':_reject('ANALYSIS_REQUIRED')
            state=self._analysis(token,owner,body['analysisId'])
            expected=canonical_hash({'jobId':job['jobId'],'snapshotHash':state['snapshotHash'],'analysisHash':result_digest(job['result'])})
            if expected!=state['analysisId']:_reject('ANALYSIS_SCOPE')
            if type(body['analysisRevision']) is not int or body['analysisRevision']!=state['revision']:_reject('CORRECTION_REVISION_CONFLICT')
            stamp['sessionGeneration']=self.coordinator.session(owner)['generation']
        elif method=='POST' and path in {'/apply/begin','/input/begin'}:
            if self.journal.replay(owner,body['requestId'],canonical_hash(body)):return None
            if path=='/apply/begin':
                plan=self.coordinator.require_plan(owner,body['planHash'],body['snapshotHash'])
                bound=self.coordinator.session(owner)['planAnalyses'].get(plan['planHash'])
                stamp['plan']=plan['planHash']
                if bound:state=self._analysis(token,owner,bound['analysisId'])
                else:raw=self.coordinator.session(owner).get('planMedia',{}).get(plan['planHash'],{})
            else:
                project=panel.get('inputProject');self.auth.require_project(token,project)
                record=self.inputs._get(self.inputs.capabilities,body['capabilityId'],owner,project)
                self.inputs.plan(body['capabilityId'],owner,project,body['choices'],verified=True)
        elif method=='POST' and path=='/input/capabilities/result':
            job=self._owned_job(body['jobId'],token,owner);owned=self.owners[job['jobId']]
            if job['kind']!='input-probe' or job['status']!='completed':_reject('INPUT_SCOPE')
            project=panel.get('inputProject');self.auth.require_project(token,project)
            if owned.get('inputProject')!=project:_reject('INPUT_SCOPE')
            record=dict(self.inputs._get(self.inputs.selections,owned['selectionId'],owner,project),assets=job['result']['assets'])
            stamp['job']=result_digest(job)
        elif method=='POST' and path=='/apply/check' and 'batchId' in body:
            current=self.journal.get(owner,body['applyId'],body['epoch']);apply_id=current['applyId']
            if current['batches']:return None
            stamp['apply']=canonical_hash(current)
            if current['kind']=='input':
                record=self.inputs._get(self.inputs.capabilities,current['capabilityId'],owner,current['projectRef'])
            else:
                bound=self.coordinator.session(owner)['planAnalyses'].get(current['planHash'])
                if bound:state=self._analysis(token,owner,bound['analysisId'])
                else:raw=self.coordinator.session(owner).get('planMedia',{}).get(current['planHash'],{})
        else:return None
        if record is not None:
            if len(record['sources'])!=len(record['assets']):_reject('INPUT_SCOPE')
            sources=[]
            for source,asset in zip(record['sources'],record['assets']):
                if source['assetId']!=asset['assetId']:_reject('INPUT_SCOPE')
                sources.append({'path':source['path'],'sha256':asset['identity']['sha256']})
            stamp['input']=canonical_hash(record)
        else:
            raw=state['raw'] if state else job['result'] if job else raw
            snapshot=self._snapshot(token,owner)
            media=raw.get('mediaInputs',[])
            normalized=lambda path:os.path.normcase(os.path.abspath(path))
            expected={s['assetId']:normalized(s['canonicalPath']) for s in snapshot['sources']}
            if (raw.get('mediaSnapshotHash')!=snapshot['snapshotHash'] or len(media)!=len(expected) or
                {s.get('assetId'):normalized(s.get('path','')) for s in media}!=expected):
                _reject('SOURCE_REANALYSIS_REQUIRED','Reanalyze the connected sequence to capture all source media.')
            sources=[{'path':s.get('path'),'sha256':s.get('sha256')} for s in media+raw.get('sourceInputs',[])]
            stamp['analysis']=result_digest(state if state else raw)
            stamp['snapshotHash']=self._snapshot(token,owner)['snapshotHash']
        if not sources:_reject('SOURCE_CHANGED')
        return {'sources':sources,'stamp':stamp,'applyId':apply_id}

    def _start_validation(self,method,path,body,token,owner,panel,generation):
        target=self._validation_target(method,path,body,token,owner,panel)
        if target is None:return None
        epoch=body.get('epoch') if method=='POST' else self.epoch
        with self._work({'epoch':epoch},panel,generation,project=True,apply_id=target['applyId']):
            target=self._validation_target(method,path,body,token,owner,panel)
            if target is None:_reject('VALIDATION_STALE')
            scope=dict(method=method,path=path,body=copy.deepcopy(body),epoch=epoch,target=target,
                       stopSerial=self.stop_serial,host=copy.deepcopy(panel['projectHost']))
            preparation=None
            if method=='POST' and path=='/plan':
                state=self._analysis(token,owner,body['analysisId'])
                scope['planRequest']=self.coordinator.capture_plan(owner,state['analysis'],body['mapping'],body['policy'])
                preparation={'kind':'edit-plan','request':scope['planRequest']}
            elif method=='POST' and path=='/sync-plan':
                session=self.coordinator.session(owner)
                scope['syncRequest']=copy.deepcopy(dict(snapshot=session['snapshot'],generation=session['generation'],result=self._owned_job(body['jobId'],token,owner)['result'],keys=body['selectedClipInstanceKeys']))
                preparation={'kind':'sync-plan','request':scope['syncRequest']}
            return self.validations.start(owner,generation,scope,target['sources'],preparation)

    def _take_validation(self,key,body,token,owner,panel,generation):
        if set(body)!={'epoch'}:_reject('INVALID_REQUEST')
        def commit(scope,proof):
            if body['epoch']!=scope['epoch'] or type(body['epoch']) is not int:_reject('VALIDATION_STALE')
            if scope['path']=='/models/community-1/revision':
                with self._work(body,panel,generation):
                    if self.stop_serial!=scope['stopSerial'] or panel['host']!=scope['host']:_reject('VALIDATION_STALE')
                    return proof.prepared_plan()
            if scope['path']=='/resources/prune':
                with self._guard(scope['epoch']),self.lock,self.auth.lock:
                    self._maintenance_authorized(scope,owner,panel,generation)
                    value=proof.prepared_plan()
                    # An explicit, one-use successful take is the only normal
                    # maintenance completion that reopens admission.
                    self.jobs.reopen(scope['epoch']);self.maintenance=False;self.gate_open=True
                    return value
            with self._work(body,panel,generation,project=True,apply_id=scope['target']['applyId']):
                if self.stop_serial!=scope['stopSerial'] or panel['projectHost']!=scope['host']:_reject('VALIDATION_STALE')
                current=self._validation_target(scope['method'],scope['path'],scope['body'],token,owner,panel)
                if current!=scope['target']:_reject('VALIDATION_STALE')
                proof.require()
                if 'planRequest' in scope:
                    return self.coordinator.publish_plan(owner,scope['planRequest'],proof.prepared_plan())
                if 'syncRequest' in scope:
                    return self.coordinator.publish_sync_plan(owner,scope['syncRequest'],proof.prepared_plan())
                return self._control(scope['method'],scope['path'],token,scope['body'],validated=True)
        return self.validations.take(key,owner,generation,commit)

    def _maintenance_authorized(self,scope,owner,panel,generation):
        if (not self.maintenance or self.maintenance['id']!=scope['maintenanceId'] or self.closed.is_set() or
            self.stop_serial!=scope['stopSerial'] or self.epoch!=scope['epoch'] or self.gate_open or
            not self.jobs.quiescent() or self.journal.blocked() or any(p['batchRunning'] for p in self.panels.values())):
            _reject('RESOURCE_MAINTENANCE_BUSY')
        self._compatible(panel,generation)
        if panel.get('host')!=scope['host'] or owner!=self.maintenance['owner']:_reject('SESSION_EXPIRED')
        actual=self._host(panel,scope['host'])
        if any(actual.get(k)!=scope['host'].get(k) for k in ['pid','createTime']):_reject('HOST_IDENTITY_REQUIRED')
        return True

    def _maintenance_state(self,owner):
        with self.lock,self.validations.lock:
            if not self.maintenance or self.maintenance['owner']!=owner:return None
            key=self.maintenance.get('continuationId');item=self.validations.items.get(key)
            if item is None:return dict(id=key,status='unavailable',canRelease=False)
            drained='process' not in item and not self.validations.failed
            return dict(id=key,status=item['status'],drained=drained,canRelease=drained and item['status'] in {'canceled','failed','consumed'})

    def _release_maintenance(self,body,owner,panel,generation):
        epoch=integer(body.get('epoch'),'epoch')
        with self._guard(epoch),self.lock,self.auth.lock:
            if not self.maintenance or self.maintenance['owner']!=owner:_reject('RESOURCE_MAINTENANCE_BUSY')
            scope=self.maintenance['scope']
            if epoch!=scope['epoch'] or self.jobs.epoch!=epoch:_reject('UPDATE_IN_PROGRESS')
            # Fresh same-owner sessions may release after drain; the original
            # continuation generation can never issue another delete or take.
            self._maintenance_authorized(scope,owner,panel,generation)
            status=self._maintenance_state(owner)
            if not status['canRelease'] or not self.validations.quiescent():_reject('RESOURCE_MAINTENANCE_BUSY')
            self.jobs.reopen(epoch);self.maintenance=False;self.gate_open=True
            return dict(released=True,status=status['status'])

    def _apply_binding(self,owner,panel,project,epoch,plan_hash,snapshot_hash,sequence,kind):
        return dict(owner=owner,instanceId=panel['instanceId'],hostIdentity=copy.deepcopy(panel['projectHost']),
                    epoch=epoch,appVersion=self.config['appVersion'],bundleId=self.config['bundleId'],
                    protocolVersion=self.config.get('protocolVersion',1),projectRef=project,sequenceRef=sequence,
                    snapshotHash=snapshot_hash,planHash=plan_hash,kind='sync' if kind=='sync' else 'edit')

    def _guard(self, epoch):
        if self.updater:
            guard = getattr(self.updater, 'admission_guard', None)
            if guard is None:
                _reject('UPDATER_PROTOCOL')
            return guard(epoch)
        return nullcontext()

    def record_job(self, job_id, owner, project_ref, snapshot_hash):
        """Persist coordinator-supplied ownership; never exposed as an HTTP route."""
        with self.lock:
            self.owners[job_id] = {'owner': owner, 'projectRef': project_ref, 'snapshotHash': snapshot_hash}
            atomic_json(self.ownership_path, self.owners)

    def _owned_job(self, job_id, token, owner):
        record = self.owners.get(job_id)
        if not isinstance(record, dict) or record.get('owner') != owner:
            _reject('JOB_SCOPE')
        if record.get('projectRef') is not None:
            snapshot = self._snapshot(token, owner)
            if record['projectRef'] != snapshot['projectRef'] or record['snapshotHash'] != snapshot['snapshotHash']:
                _reject('JOB_SCOPE')
        return self.jobs.get(job_id)

    def _public_job(self, value, result=False):
        allowed = ['jobId', 'kind', 'status', 'epoch', 'createdAt', 'finishedAt']
        public = {key: value[key] for key in allowed if key in value}
        if result and value.get('status') == 'completed':
            public['result'] = value.get('result')
        if value.get('error'):
            public['error'] = {'code': value['error'].get('code', 'WORKER_FAILED'), 'message': 'Local operation could not finish.'}
        return public

    def update_state(self):
        if not self.updater:
            return {'checkState': 'UNAVAILABLE', 'updateState': 'UNAVAILABLE', 'gateOpen': self.gate_open,
                    'updateEpoch': self.epoch, 'currentVersion': self.config['appVersion'],
                    'error': {'code': 'UPDATER_UNAVAILABLE', 'message': 'Verified signing key and installation integration are required.'}}
        state = self.updater.state()
        fields = ['checkState', 'updateState', 'candidate', 'currentVersion', 'newVersion', 'bundleId',
                  'updateEpoch', 'epoch', 'gateOpen', 'retryAt', 'lastCheckedAt']
        result = {key: state[key] for key in fields if key in state}
        if state.get('error'):
            result['error'] = {'code': state['error'].get('code', 'UPDATE_FAILED'), 'message': 'Update needs attention.'}
        return result

    def request_update_check(self):
        # A provider lookup must not occupy the panel's ordered MAC transport.
        # Coalesce checks and retain ownership until handoff drains this thread.
        with self.dispatch_condition:
            if self.draining: _reject('HANDOFF_DRAINING')
            if self.updater and self.updater.gate_open and (self.update_check is None or not self.update_check.is_alive()):
                self.update_check = threading.Thread(target=self.updater.check,
                    name='Contentrium-CUT-update-check',daemon=True)
                self.update_check.start()
        return self.update_state()

    def state(self, token=None):
        owner, panel = None, None
        if token is not None:
            owner, _, panel = self._panel(token)
        visible = []
        for job in self.jobs.list():
            if owner is None:
                visible.append(self._public_job(job))
            else:
                try:
                    self._owned_job(job['jobId'], token, owner)
                    visible.append(self._public_job(job))
                except CutError:
                    pass
        models = {}
        for model in ['silero', 'community-1']:
            status = ModelManager(self.root / 'models').state(model,verify=False)
            models[model] = {key: status[key] for key in ['modelId', 'status', 'revision', 'setup'] if key in status}
        admission = None
        if panel:
            try: self._compatible(panel,hashlib.sha256(token.encode()).hexdigest())
            except CutError as error: admission={'code':error.code}
        return {'protocolVersion': 1, 'appVersion': self.config['appVersion'], 'bundleId': self.config.get('bundleId'),
                'compatible': panel is not None and admission is None, 'admissionError': admission,
                'applyRecovery':self.journal.status(owner),
                'maintenance':self._maintenance_state(owner),
                'epoch': self.epoch, 'gateOpen': self.gate_open, 'stopEpoch': panel['stopEpoch'] if panel else None,
                'batchRunning': panel['batchRunning'] if panel else any(p['batchRunning'] for p in self.panels.values()),
                'panelVersion': panel['appVersion'] if panel else None, 'panelBundleId': panel['bundleId'] if panel else None,
                'models': models, 'jobs': visible, 'update': self.update_state()}

    def participants(self):
        with self.lock:
            values = [{'id': 'panel:'+owner, 'identity': {'instanceId': panel['instanceId'], 'host': copy.deepcopy(panel['host'])}}
                      for owner, panel in self.panels.items()]
        with self.jobs.lock:
            for job_id in self.jobs.processes:
                identity = {'jobId': job_id, 'pid': self.jobs.jobs[job_id]['workerPid']}
                self.workers[job_id] = identity
                values.append({'id': 'worker:'+job_id, 'identity': identity})
        with self.lock:
            values.extend({'id':'apply:'+r['applyId'],'identity':{'host':copy.deepcopy(r['hostIdentity'])}}
                          for r in self.applies.values() if r['active'])
        with self.validations.lock:
            values.extend({'id':'validation:'+key,'identity':{'pid':r['process'].pid}}
                          for key,r in self.validations.items.items() if 'process' in r)
        return values

    def participant_exited(self, participant_id, identity):
        if participant_id.startswith('validation:'):
            with self.validations.lock:
                item=self.validations.items.get(participant_id[11:])
                return bool(item and item.get('workerPid')==identity.get('pid') and 'process' not in item and not self.validations.failed)
        if participant_id.startswith('apply:'):
            record=self.applies.get(participant_id[6:])
            if not record or identity!={'host':record['hostIdentity']}:return False
            probe=getattr(self.installation,'host_identity_exited',None)
            try:return not record['active'] or (callable(probe) and probe(record['hostIdentity']) is True)
            except Exception:return False
        if participant_id.startswith('worker:'):
            job_id = participant_id[7:]
            with self.jobs.lock:
                return (self.workers.get(job_id) == identity and job_id not in self.jobs.processes and
                        self.jobs.jobs.get(job_id, {}).get('status') in TERMINAL)
        # A timestamp, missing heartbeat or failed process query never proves exit.
        return (participant_id.startswith('panel:') and isinstance(identity, dict) and
                isinstance(identity.get('host'), dict) and self._host_exited())

    def _host_exited(self):
        try:
            return self.installation is not None and self.installation.host_exited() is True
        except Exception:
            return False

    def stop_all(self, epoch):
        with self.lock:
            self.stop_serial += 1
            self.gate_open = False
            self.maintenance = False  # Canceled owned work still participates in drain.
            self.epoch = integer(epoch, 'epoch')
            for panel in self.panels.values():
                panel['stopEpoch'] = epoch
        self.validations.cancel_all()
        self.jobs.stop_all(epoch)

    def advance_once(self):
        if self.draining:
            return self.update_state()
        if not self.updater:
            return self.update_state()
        state = self.updater.state()
        if state['updateState'] in PRE_REPLACE and self.jobs.quiescent() and self.validations.quiescent():
            self.updater.advance()  # Updater independently rechecks every participant.
        if self.updater.gate_open and not self.gate_open and not self.maintenance and self.jobs.quiescent() and self.validations.quiescent() and not self.config.get('recoveryOnly'):
            self.jobs.reopen(self.updater.state()['updateEpoch'])
            with self.lock:
                self.epoch = self.jobs.epoch
                self.gate_open = True
                for panel in self.panels.values():
                    panel['stopEpoch'] = None
        return self.update_state()

    def _manager_loop(self):
        while not self.closed.wait(.1):
            try:
                self.advance_once()
            except Exception:
                self.gate_open = False  # An unknown control failure must not admit work.

    def activate_panel(self, token, receipt):
        owner, _, panel = self._panel(token)
        panel['host'] = self._host(panel, receipt.get('hostIdentity', panel['host']))
        if not self.updater:
            _reject('UPDATER_UNAVAILABLE')
        actual = dict(receipt, companionVersion=self.config['appVersion'])
        if (actual.get('bundleId') != self.config.get('bundleId') or
                panel['appVersion'] != actual.get('panelVersion') or panel['bundleId'] != actual.get('bundleId')):
            _reject('UPDATE_ACTIVATION')
        self.updater.activate(actual)
        self.advance_once()
        return self.update_state()

    def _activation_panels(self, version, bundle):
        with self.lock:
            panels = [copy.deepcopy(panel) for panel in self.panels.values()]
        candidates = []
        for panel in panels:
            if (panel['appVersion'] != version or panel['bundleId'] != bundle or not panel.get('host') or
                    panel.get('heartbeatAt') is None or not 0 <= time.monotonic()-panel['heartbeatAt'] <= 10):
                continue
            if self.auth.private:
                session = self.auth.activation_session(panel['ownerId'], panel['sessionGeneration'])
                if (not session or session['diagnosticOnly'] or session['appVersion'] != version or
                        session['bundleId'] != bundle or panel.get('heartbeatGeneration') != panel['sessionGeneration'] or
                        panel.get('heartbeatIdentity') != {key: session[key] for key in
                            ['appVersion', 'bundleId', 'protocolVersion', 'serverBootId', 'diagnosticOnly']}):
                    continue
            candidates.append(panel)
        return candidates

    def activation_heartbeat(self, version, bundle):
        """Shared native installation predicate; body claims alone grant no authority."""
        return bool(self._activation_panels(version, bundle))

    def finalize_activation(self):
        """Native lifecycle only: build a receipt from a fresh matched heartbeat.

        The concrete installation.verify hook must additionally attest the loaded
        versioned executable/config and panel registration. Readable JSON is not
        a replacement for that actual runtime probe.
        """
        if not self.updater:
            _reject('UPDATER_UNAVAILABLE')
        state = self.updater.state()
        if state['updateState'] not in ['PENDING_ACTIVATION', 'VERIFYING_INSTALL']:
            return self.update_state()
        if self.config['appVersion'] != state.get('newVersion') or self.config.get('bundleId') != state.get('bundleId'):
            _reject('UPDATE_ACTIVATION')
        for path in [self.auth.path, self.ownership_path]:
            if path.exists() and not isinstance(json.loads(path.read_text(encoding='utf-8')), dict):
                _reject('UPDATE_ACTIVATION')
        candidates = self._activation_panels(self.config['appVersion'], self.config.get('bundleId'))
        for panel in candidates:
            actual_host = self._host(panel, panel['host'])
            receipt = {'panelVersion': panel['appVersion'], 'companionVersion': self.config['appVersion'],
                       'bundleId': panel['bundleId'], 'handshake': True, 'dataReadable': True,
                       'hostIdentity': actual_host}
            self.updater.activate(receipt)
            self.advance_once()
            return self.update_state()
        _reject('UPDATE_ACTIVATION')

    def _control(self, method, path, token, body, *, validated=False):
        owner, session, panel = self._panel(token)
        generation = hashlib.sha256(token.encode()).hexdigest()
        continuation=re.fullmatch(r'/continuations/([0-9a-f]{32})(/take|/cancel)?',path)
        if continuation:
            key,action=continuation.groups()
            if method=='GET' and action is None:return self.validations.status(key,owner,generation)
            if method=='POST' and action=='/cancel' and not body:return self.validations.cancel(key,owner,generation)
            if method=='POST' and action=='/take':return self._take_validation(key,body,token,owner,panel,generation)
            _reject('INVALID_REQUEST')
        if (session.get('diagnosticOnly') or self.config.get('recoveryOnly')) and path not in [
                '/state','/heartbeat','/updates/check','/updates/recover','/updates/cancel','/updates/ack','/apply/recover','/apply/status','/apply/end','/apply/result','/apply/batch-end']:
            _reject('COMPONENT_MISMATCH' if session.get('diagnosticOnly') else 'RECOVERY_REQUIRED')
        if not validated:
            pending=self._start_validation(method,path,body,token,owner,panel,generation)
            if pending is not None:return pending
        if path == '/state' and method in ['GET', 'POST']:
            return self.state(token)
        if path == '/project' and method == 'POST':
            with self._work(body,panel,generation):
                result = self.coordinator.bind(owner, body['snapshot'])
                self.auth.bind_project(token, body['snapshot']['projectRef'])
                panel['projectHost']=copy.deepcopy(panel['host'])
            return result
        if path == '/heartbeat' and method == 'POST':
            # Authority belongs to this authenticated request, not the shared
            # panel dictionary a reconnect may replace while the host probe waits.
            generation = hashlib.sha256(token.encode()).hexdigest()
            version, bundle = body.get('appVersion', body.get('panelVersion')), body.get('bundleId')
            protocol = body.get('protocolVersion')
            if ('appVersion' in body and 'panelVersion' in body and body['appVersion']!=body['panelVersion']) or type(protocol) is not int or protocol!=self.config.get('protocolVersion',1):
                _reject('COMPONENT_MISMATCH')
            if self.auth.private and (version != session['appVersion'] or bundle != session['bundleId']):
                _reject('COMPONENT_MISMATCH')
            actual = self._host(panel, body.get('hostIdentity', panel['host']))
            epoch = integer(body['epoch'], 'epoch')
            if any(not isinstance(body.get(field), bool) for field in ['batchRunning', 'quiescent']):
                _reject('INVALID_REQUEST')
            # Compare and commit atomically with _panel's generation reset. Do
            # not acquire auth.lock here: component authority is the request copy.
            with self.lock:
                if panel['sessionGeneration'] != generation: _reject('SESSION_EXPIRED')
                panel.update(host=actual, epoch=epoch, batchRunning=body['batchRunning'], quiescent=body['quiescent'],
                             appVersion=version, bundleId=bundle, heartbeatAt=time.monotonic(),
                             protocolVersion=protocol,
                             heartbeatGeneration=generation,
                             heartbeatIdentity={key: session[key] for key in
                                 ['appVersion', 'bundleId', 'protocolVersion', 'serverBootId', 'diagnosticOnly']}
                                 if self.auth.private else None)
                if any(value['owner'] == owner and value['active'] and value.get('batchRunning') for value in self.applies.values()):
                    panel.update(batchRunning=True, quiescent=False)
                return {'epoch': self.epoch, 'stopEpoch': panel['stopEpoch'], 'gateOpen': self.gate_open}
        if path == '/apply/status' and method == 'GET':
            with self.lock: return self.journal.status(owner)
        if path == '/apply/recover' and method == 'POST':
            def exited(identity):
                probe=getattr(self.installation,'host_identity_exited',None)
                try: return callable(probe) and probe(identity) is True
                except Exception: return False
            with self.lock:
                result=self.journal.recover(owner,body.get('requestId'),body.get('applyId'),body.get('acknowledged'),exited)
                panel['batchRunning']=any(r['owner']==owner and r['batchRunning'] for r in self.applies.values())
                return result
        if path == '/resources' and method == 'GET':
            return self.coordinator.resource_settings()
        if path in ['/models/community-1/revision','/models/community-1/install'] and method=='POST':
            from .model_setup import validate_access,community_revision
            fields={'token','termsAccepted','epoch'} | ({'revision'} if path.endswith('/install') else set())
            if set(body)!=fields: _reject('INVALID_REQUEST')
            validate_access(body['token'],body['termsAccepted'])
            with self._work(body,panel,generation): pass
            if path.endswith('/revision'):
                with self._work(body,panel,generation):
                    scope=dict(path=path,epoch=body['epoch'],stopSerial=self.stop_serial,host=copy.deepcopy(panel['host']))
                    return self.validations.start(owner,generation,scope,[],
                        preparation={'kind':'model-revision','token':body['token']})
            if not isinstance(body['revision'],str) or not re.fullmatch('[0-9a-fA-F]{40}',body['revision']): _reject('MODEL_NOT_READY')
            with self._work(body,panel,generation):
                value=self.jobs.submit('model-setup',dict(modelRoot=str(self.root/'models'),token=body['token'],termsAccepted=True,revision=body['revision']),expected_epoch=body['epoch'])
                self.record_job(value['jobId'],owner,None,None)
                return self._public_job(value)
        if path == '/input/sources' and method=='POST':
            if set(body)!={'projectRef','sources','epoch'}: _reject('INVALID_REQUEST')
            with self._work(body,panel,generation):
                result=self.inputs.select(owner,body['projectRef'],body['sources'])
                self.auth.bind_project(token,body['projectRef'])
                panel.update(projectHost=copy.deepcopy(panel['host']),inputProject=body['projectRef'])
                return result
        if path == '/input/capabilities' and method=='POST':
            if set(body)!={'selectionId','epoch'}: _reject('INVALID_REQUEST')
            with self._work(body,panel,generation,project=True):
                project=panel.get('inputProject');self.auth.require_project(token,project)
                payload=self.inputs.payload(body['selectionId'],owner,project)
                value=self.jobs.submit('input-probe',payload,expected_epoch=body['epoch'])
                self.record_job(value['jobId'],owner,None,None)
                self.owners[value['jobId']].update(selectionId=body['selectionId'],inputProject=project)
                atomic_json(self.ownership_path,self.owners)
                return self._public_job(value)
        if path == '/input/capabilities/result' and method=='POST':
            if set(body)!={'jobId','epoch'}: _reject('INVALID_REQUEST')
            with self._work(body,panel,generation,project=True):
                job=self._owned_job(body['jobId'],token,owner);record=self.owners[job['jobId']]
                if job['kind']!='input-probe' or job['status']!='completed' or record.get('inputProject')!=panel.get('inputProject'): _reject('INPUT_SCOPE')
                self.auth.require_project(token,record['inputProject'])
                return self.inputs.promote(record['selectionId'],owner,record['inputProject'],job['result'],verified=validated)
        if path == '/input/begin' and method=='POST':
            if set(body)!={'capabilityId','choices','requestId','epoch'}: _reject('INVALID_REQUEST')
            request_hash=canonical_hash(body)
            with self.lock:
                replay=self.journal.replay(owner,body['requestId'],request_hash)
                if replay:return replay
            with self._work(body,panel,generation,project=True) as epoch:
                project=panel.get('inputProject');self.auth.require_project(token,project)
                plan,capability=self.inputs.plan(body['capabilityId'],owner,project,body['choices'],verified=validated)
                binding=self._apply_binding(owner,panel,project,epoch,plan['planHash'],None,'input:'+project,'input')
                binding.update(kind='input',sourceRecordsDigest=capability['sourceRecordsDigest'],capabilityId=body['capabilityId'])
                value=self.journal.begin(binding,body['requestId'],plan,request_hash)
                panel['quiescent']=False
                return dict(value,capability=capability)
        if path == '/resources' and method == 'POST':
            if set(body)!={'settings','epoch'}: _reject('INVALID_REQUEST')
            with self._work(body,panel,generation): return self.coordinator.resource_settings(body['settings'])
        if path == '/resources/prune' and method == 'POST':
            if set(body)=={'epoch','action'} and body['action']=='release':return self._release_maintenance(body,owner,panel,generation)
            if set(body)!={'epoch'}: _reject('INVALID_REQUEST')
            with self._work(body,panel,generation) as epoch:
                if not self.jobs.quiescent() or not self.validations.quiescent() or any(p['batchRunning'] for p in self.panels.values()): _reject('RESOURCE_MAINTENANCE_BUSY')
                settings=self.coordinator.resource_settings(include_status=False)['settings']
                if 'cacheBudgetBytes' not in settings: _reject('RESOURCE_BUDGET_REQUIRED')
                scope=dict(path=path,epoch=epoch,stopSerial=self.stop_serial,host=copy.deepcopy(panel['host']),maintenanceId=uuid.uuid4().hex)
                self.maintenance={'id':scope['maintenanceId'],'owner':owner,'scope':copy.deepcopy(scope)};self.gate_open=False;self.jobs.gate_open=False
                permitted=set()
                def boundary(entry):
                    with self._guard(epoch),self.lock,self.auth.lock:
                        self._maintenance_authorized(scope,owner,panel,generation)
                        if entry is not None:
                            if not isinstance(entry,str) or not re.fullmatch('[0-9a-f]{64}\\.json',entry) or entry in permitted:_reject('RESOURCE_GATE_REQUIRED')
                            permitted.add(entry)
                        return True
                try:
                    pending=self.validations.start(owner,generation,scope,[],{'kind':'cache-prune','root':str(self.root/'cache'),'budget':settings['cacheBudgetBytes']},boundary=boundary)
                    self.maintenance['continuationId']=pending['continuation']['id']
                    return pending
                except BaseException:
                    self.maintenance=False  # Fail closed; no automatic gate reopening.
                    raise
        if path == '/analyses/register' and method == 'POST':
            if set(body)!={'jobId','epoch'}: _reject('INVALID_REQUEST')
            with self._work(body,panel,generation,project=True):
                job=self._owned_job(body['jobId'],token,owner)
                if job['kind']!='analysis' or job['status']!='completed': _reject('ANALYSIS_REQUIRED')
                return self.coordinator.register_analysis(owner,job['jobId'],job['result'])
        analysis_match=re.fullmatch(r'/analyses/([0-9a-f]{64})(/correct|/example)?',path)
        if analysis_match:
            identity,action=analysis_match.groups()
            if method=='GET' and action is None: return self._analysis(token,owner,identity)
            if method=='POST' and action in ['/correct','/example']:
                with self._work(body,panel,generation,project=True):
                    state=self._analysis(token,owner,identity)
                    if action=='/correct':
                        if set(body)!={'expectedRevision','operation','requestId','epoch'}: _reject('INVALID_REQUEST')
                        return self.coordinator.correct_analysis(owner,identity,body['expectedRevision'],body['operation'],body['requestId'])
                    if set(body)!={'exampleId','expectedRevision','epoch'}: _reject('INVALID_REQUEST')
                    if type(body['expectedRevision']) is not int or state['revision']!=body['expectedRevision']: _reject('CORRECTION_REVISION_CONFLICT')
                    if not any(e['exampleId']==body['exampleId'] for e in state['examples']): _reject('EXAMPLE_SCOPE')
                    payload=dict(root=str(self.root),owner=owner,snapshot=copy.deepcopy(self._snapshot(token,owner)),
                                 analysisId=identity,revision=state['revision'],exampleId=body['exampleId'])
                    value=self.jobs.submit('example',payload,expected_epoch=body['epoch'])
                    self.record_job(value['jobId'],owner,state['projectRef'],state['snapshotHash'])
                    self.owners[value['jobId']].update(analysisId=identity,revision=state['revision'])
                    atomic_json(self.ownership_path,self.owners)
                    return self._public_job(value)
        if path == '/jobs' and method == 'POST':
            epoch = self._admit(body)
            if set(body) - {'kind', 'options', 'epoch', 'termsAccepted', 'revision'}:
                _reject('INVALID_REQUEST')
            kind = body.get('kind')
            project_ref, snapshot_hash = None, None
            if kind in ['sync', 'analysis']:
                snapshot = self._snapshot(token, owner)
                project_ref, snapshot_hash = snapshot['projectRef'], snapshot['snapshotHash']
                options = body.get('options', {})
                allowed={'assetIds','reference','method','sourceSelections','manualOffsets','timecodeConfirmations'} if kind=='sync' else {'mode','microphones','calibration','speakerCount','vadThreshold','bleedTolerance'}
                if not isinstance(options, dict) or set(options) - allowed:
                    _reject('INVALID_REQUEST')
                payload = (self.coordinator.sync_payload(owner, options) if kind == 'sync'
                           else self.coordinator.audio_payload(owner, options))
            elif kind == 'model-setup':
                provider = self.config.get('tokenProvider')
                model_token = provider() if callable(provider) else None
                if not model_token or body.get('termsAccepted') is not True or not re.fullmatch('[0-9a-fA-F]{40}', body.get('revision', '')):
                    _reject('MODEL_NOT_READY')
                payload = {'modelRoot': str(self.root / 'models'), 'token': model_token,
                           'termsAccepted': True, 'revision': body['revision']}
            else:
                _reject('INVALID_JOB')
            with self._work(body,panel,generation,project=kind in ['sync','analysis']):
                if project_ref is not None and self._snapshot(token,owner)['snapshotHash']!=snapshot_hash: _reject('JOB_SCOPE')
                value = self.jobs.submit(kind, payload, expected_epoch=epoch)
                self.record_job(value['jobId'], owner, project_ref, snapshot_hash)
                return self._public_job(value)
        match = re.fullmatch(r'/jobs/([a-zA-Z0-9_-]+)(/cancel)?', path)
        if match:
            job = self._owned_job(match[1], token, owner)
            if match[2] and method == 'POST':
                self.jobs.cancel(match[1])
                return self._public_job(self.jobs.get(match[1]))
            if not match[2] and method == 'GET':
                if job['kind']=='example' and job['status']=='completed':
                    record=self.owners[job['jobId']]; state=self._analysis(token,owner,record['analysisId'])
                    if state['revision']!=record['revision'] or job['result'].get('revision')!=state['revision']: _reject('EXAMPLE_SCOPE')
                return self._public_job(job, result=True)
        if path == '/plan' and method == 'POST':
            # HTTP plans can be published only by their owned prepared result.
            # Never fall back to computing inside a final take/physical request.
            _reject('VALIDATION_REQUIRED')
        if path == '/sync-plan' and method == 'POST':
            _reject('VALIDATION_REQUIRED')
        if path.startswith('/apply/') and method == 'POST':
            if path == '/apply/begin':
                if set(body)!={'planHash','snapshotHash','requestId','epoch'}: _reject('INVALID_REQUEST')
                request_hash=canonical_hash(body)
                with self.lock:
                    replay=self.journal.replay(owner,body['requestId'],request_hash)
                    if replay: return replay
                self._admit(body);self._compatible(panel,generation)
                initial=self._snapshot(token,owner)
                self.coordinator.require_plan(owner,body['planHash'],body['snapshotHash'])
                with self._work(body,panel,generation,project=True) as epoch:
                    snapshot=self._snapshot(token,owner)
                    if snapshot['snapshotHash']!=initial['snapshotHash']: _reject('PLAN_SCOPE')
                    if snapshot.get('supportFlags',{}).get('hostApplyVerified') is not True: _reject('HOST_APPLY_UNVERIFIED')
                    plan=self.coordinator.require_plan(owner,body['planHash'],body['snapshotHash'])
                    binding=self._apply_binding(owner,panel,snapshot['projectRef'],epoch,plan['planHash'],
                                                snapshot['snapshotHash'],snapshot['sequenceRef'],'sync' if 'syncPlanHash' in plan else 'edit')
                    if binding['kind']=='edit':binding['batchLimit']=self.coordinator.session(owner)['planCapacity'][plan['planHash']]
                    value=self.journal.begin(binding,body['requestId'],plan,request_hash)
                    panel['quiescent']=False
                    return value
            identity=body.get('applyId'); epoch=integer(body.get('epoch'),'epoch')
            # Cleanup and terminal replay depend on the journal, never on a rebuilt coordinator.
            with self.lock: apply=self.journal.get(owner,identity,epoch)
            if path == '/apply/end':
                if set(body)!={'applyId','epoch','status','receipt'}: _reject('INVALID_REQUEST')
                completion=self._work(body,panel,generation,apply_id=identity,project=True) if body['status']=='completed' and 'terminal' not in apply else nullcontext()
                with completion,self.lock:
                    result=self.journal.end(owner,identity,epoch,body['status'],body['receipt'])
                    current=self.journal.get(owner,identity)
                    panel.update(batchRunning=current['batchRunning'],quiescent=not current['active'])
                    return result
            if path == '/apply/batch-end':
                if set(body)!={'applyId','epoch','batchId','receipt'}: _reject('INVALID_REQUEST')
                with self.lock:
                    result=self.journal.batch_end(owner,identity,epoch,body['batchId'],body['receipt'])
                    panel['batchRunning']=self.journal.get(owner,identity)['batchRunning']
                    return result
            if path == '/apply/result':
                if set(body)!={'applyId','epoch','resultSequenceRef'}: _reject('INVALID_REQUEST')
                with self.lock: return self.journal.result(owner,identity,epoch,body['resultSequenceRef'])
            if path == '/apply/check':
                fields=set(body)-{'applyId','epoch'}
                if fields and fields!={'batchId','operationDigest','resultSequenceRef'}: _reject('INVALID_REQUEST')
                with self._work(body,panel,generation,apply_id=identity,project=True):
                    current=self.journal.get(owner,identity,epoch)
                    if not current['active'] or current['phase']=='UNCERTAIN': _reject('APPLY_RECOVERY_REQUIRED')
                    if any(current['hostIdentity'].get(k)!=panel['projectHost'].get(k) for k in ['pid','createTime']): _reject('HOST_IDENTITY_REQUIRED')
                    self.auth.require_project(token,current['projectRef'])
                    if current['kind']!='input':
                        snapshot=self._snapshot(token,owner)
                        if snapshot['snapshotHash']!=current['snapshotHash']: _reject('PLAN_SCOPE')
                        self.coordinator.require_plan(owner,current['planHash'],current['snapshotHash'])
                    elif fields and not current['batches']:
                        self.inputs.recheck(current['capabilityId'],owner,current['projectRef'],verified=validated)
                    if fields:
                        value=self.journal.batch(owner,identity,epoch,body['batchId'],body['operationDigest'],body['resultSequenceRef'])
                        panel.update(batchRunning=self.journal.get(owner,identity)['batchRunning'],quiescent=False)
                        return value
                    return dict(applyId=identity,epoch=epoch,allowed=True)
        if path.startswith('/updates/') and method == 'POST':
            action = path[9:]
            if action == 'check':
                return self.request_update_check()
            if not self.updater:
                _reject('UPDATER_UNAVAILABLE')
            if action == 'start':
                self.updater.start(body['candidateId'], body['manifestDigest'], body['requestId'])
            elif action == 'ack':
                participant_id = 'panel:'+owner
                if body.get('participantId', participant_id) != participant_id:
                    _reject('PARTICIPANT_SCOPE')
                epoch = integer(body['epoch'], 'epoch')
                if (panel['epoch'] != epoch or panel['stopEpoch'] != epoch or panel['quiescent'] is not True or
                        panel['batchRunning'] is not False or any(a['owner'] == owner and a['active'] for a in self.applies.values())):
                    _reject('UPDATE_QUIESCE')
                self.updater.register_participant(participant_id, {'instanceId': panel['instanceId'], 'host': copy.deepcopy(panel['host'])})
                self.updater.ack(participant_id, epoch, {'quiescent': body.get('quiescent'), 'batchRunning': body.get('batchRunning')})
            elif action == 'cancel':
                self.updater.cancel()
                self.advance_once()
            elif action == 'recover':
                self.updater.recover()
                self.advance_once()
            else:
                _reject('NOT_FOUND')
            return self.update_state()
        _reject('NOT_FOUND')

    def dispatch(self, method, path, headers, raw=b''):
        """Trusted in-process legacy regression adapter; never registered on HTTP.

        Headers/config cannot select this adapter on a network request. Native
        tools must not expose this method to untrusted callers.
        """
        with self.dispatch_condition:
            if self.draining:
                return 409, {'error': {'code': 'HANDOFF_DRAINING', 'message': 'Local service is stopping.'}}
            self.active_dispatches += 1
        try:
            return self._dispatch(method, path, headers, raw)
        finally:
            with self.dispatch_condition:
                self.active_dispatches -= 1
                self.dispatch_condition.notify_all()

    def _dispatch(self, method, path, headers, raw=b''):
        """Legacy in-process test adapter, with the historic validation contract."""
        try:
            lowered = {key.lower(): value for key, value in headers.items()}
            self.auth.validate_origin(lowered.get('origin'))
            if lowered.get('host') not in ['localhost:41737', '127.0.0.1:41737']:
                _reject('HOST_DENIED')
            if '?' in path or not path.startswith('/') or method not in ['GET', 'POST']:
                _reject('NOT_FOUND')
            if method == 'GET' and path == '/health':
                return 200, {'protocolVersion': 1, 'appVersion': self.config['appVersion']}
            if len(raw) > MAX_BODY:
                _reject('BODY_TOO_LARGE')
            if method == 'POST' and lowered.get('content-type', '').split(';')[0].strip().lower() != 'application/json':
                _reject('CONTENT_TYPE')
            body = _json(raw or b'{}')
            if method == 'POST' and path == '/pair':
                result = self.auth.pair(body.get('code'), body.get('instanceId'))
                self._panel(result['token'])
                return 200, result
            authorization = lowered.get('authorization', '')
            if not authorization.startswith('Bearer '):
                _reject('AUTH_REQUIRED')
            token = authorization[7:]
            self.auth.authenticate(token)
            return 200, self._control(method, path, token, body)
        except CutError as error:
            code = error.code if isinstance(error.code, str) and re.fullmatch('[A-Z0-9_]{1,64}', error.code) else 'REQUEST_FAILED'
            statuses = {'AUTH_REQUIRED': 401, 'ORIGIN_DENIED': 403, 'HOST_DENIED': 403,
                        'PAIRING_FAILED': 403, 'JOB_SCOPE': 403, 'APPLY_SCOPE': 403, 'PROJECT_SCOPE': 403,
                        'PARTICIPANT_SCOPE': 403, 'BODY_TOO_LARGE': 413, 'CONTENT_TYPE': 415, 'NOT_FOUND': 404,
                        'INVALID_REQUEST': 400, 'INVALID_JOB': 400, 'INVALID_CONTRACT': 400}
            return statuses.get(code, 409), {'error': {'code': code, 'message': self._error_message(code)}}
        except (ValueError, TypeError, KeyError, UnicodeError, AttributeError):
            return 400, {'error': {'code': 'INVALID_REQUEST', 'message': 'Use a valid request for this operation.'}}
        except Exception:
            return 500, {'error': {'code': 'SERVICE_FAILED', 'message': 'Local service needs attention.'}}

    def dispatch_authenticated(self, method, path, headers, raw=b''):
        """Only network dispatch boundary. No anonymous/manual bearer fallback."""
        session_id, counter, authenticated = None, None, False
        with self.dispatch_condition:
            self.active_dispatches += 1
        try:
            lowered = {key.lower(): value for key, value in headers.items()}
            self.auth.validate_origin(lowered.get('origin'))
            if lowered.get('host') != '127.0.0.1:41737': _reject('HOST_DENIED')
            if method not in ['GET', 'POST'] or not re.fullmatch(r'/[A-Za-z0-9/_-]*', path): _reject('NOT_FOUND')
            if len(raw) > MAX_BODY: _reject('BODY_TOO_LARGE')
            if method == 'GET' and raw: _reject('INVALID_REQUEST')
            if method == 'POST' and lowered.get('content-type', '').split(';')[0].strip().lower() != 'application/json': _reject('CONTENT_TYPE')
            if method == 'GET' and path == '/health':
                return 200, {'protocolVersion': 1, 'appVersion': self.config['appVersion']}
            if self.draining: _reject('HANDOFF_DRAINING')
            if not self.auth.private: _reject('BOOTSTRAP_MISSING')
            if path in ['/auth/challenge', '/auth/session']:
                if method != 'POST' or len(raw) > 2048 or lowered.get('authorization'): _reject('INVALID_REQUEST')
                callback = self.auth.challenge if path == '/auth/challenge' else self.auth.establish_session
                return 200, callback(_json(raw))
            if lowered.get('authorization'): _reject('AUTH_REQUIRED')
            session_id = lowered.get('x-cut-session')
            value = lowered.get('x-cut-counter', '')
            if not re.fullmatch('[1-9][0-9]{0,15}', value): _reject('AUTH_REQUIRED')
            counter = int(value)
            self.auth.verify_request(session_id, counter, method, path, raw, lowered.get('x-cut-mac'))
            authenticated = True
            body = _json(raw or b'{}')
            status, value = 200, self._control(method, path, session_id, body)
        except CutError as error:
            code = error.code if isinstance(error.code, str) and re.fullmatch('[A-Z0-9_]{1,64}', error.code) else 'REQUEST_FAILED'
            statuses = {'AUTH_REQUIRED': 401, 'SESSION_EXPIRED': 401, 'BOOTSTRAP_MISSING': 401,
                        'PANEL_CONTEXT_CONFLICT': 409,
                        'AUTH_RATE_LIMIT': 429, 'HOST_DENIED': 403, 'ORIGIN_DENIED': 403,
                        'BODY_TOO_LARGE': 413, 'CONTENT_TYPE': 415, 'NOT_FOUND': 404,
                        'JOB_SCOPE': 403, 'APPLY_SCOPE': 403, 'PROJECT_SCOPE': 403, 'PARTICIPANT_SCOPE': 403,
                        'INVALID_REQUEST': 400, 'INVALID_JOB': 400, 'INVALID_CONTRACT': 400}
            status, value = statuses.get(code, 409), {'error': {'code': code, 'message': self._error_message(code)}}
        except (ValueError, TypeError, KeyError, UnicodeError, AttributeError):
            status, value = 400, {'error': {'code': 'INVALID_REQUEST', 'message': 'Request could not be completed.'}}
        except Exception:
            status, value = 500, {'error': {'code': 'SERVICE_FAILED', 'message': 'Local service needs attention.'}}
        finally:
            with self.dispatch_condition:
                self.active_dispatches -= 1
                self.dispatch_condition.notify_all()
        return status, self.auth.sign_response(session_id, counter, status, value) if authenticated else value

    def http_server(self, port=PORT):
        service = self
        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def setup(self):
                super().setup()
                self.connection.settimeout(5)
                with service.dispatch_condition:
                    service.connections.add(self.connection)
            def finish(self):
                try:
                    super().finish()
                except OSError:
                    pass
                finally:
                    with service.dispatch_condition:
                        service.connections.discard(self.connection)
                        service.dispatch_condition.notify_all()
            def log_message(self, *args):
                pass  # Never record request credentials, source paths or payloads.
            def handle(self):
                try:
                    super().handle()
                except OSError:
                    self.close_connection = True
            def send_error(self, code, message=None, explain=None):
                self.close_connection = True
                self.respond(code, {'error': {'code': 'INVALID_REQUEST'}})
            def respond(self, status, value):
                raw = json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(raw)))
                self.send_header('Cache-Control', 'no-store')
                if self.close_connection:
                    self.send_header('Connection', 'close')
                self.end_headers()
                try:
                    self.wfile.write(raw)
                except OSError:
                    pass
            def handle_request(self):
                if (any(len(self.headers.get_all(name, [])) > 1 for name in ['Host', 'Origin', 'Authorization', 'Content-Length', 'Content-Type', 'X-Cut-Session', 'X-Cut-Counter', 'X-Cut-Mac']) or
                        self.headers.get('Transfer-Encoding') is not None or sum(len(k)+len(v) for k, v in self.headers.items()) > 8192):
                    self.close_connection = True
                    self.respond(400, {'error': {'code': 'INVALID_REQUEST'}})
                    return
                length = self.headers.get('Content-Length', '0')
                if not re.fullmatch('[0-9]{1,10}', length):
                    self.close_connection = True
                    self.respond(400, {'error': {'code': 'INVALID_REQUEST'}})
                    return
                if int(length) > MAX_BODY:
                    self.close_connection = True
                    self.respond(413, {'error': {'code': 'BODY_TOO_LARGE'}})
                    return
                try:
                    raw = self.rfile.read(int(length))
                    if len(raw) != int(length):
                        raise ValueError('Incomplete body')
                    self.respond(*service.dispatch_authenticated(self.command, self.path, dict(self.headers), raw))
                except OSError:
                    self.close_connection = True
                except ValueError:
                    self.close_connection = True
                    self.respond(400, {'error': {'code': 'INVALID_REQUEST'}})
            do_GET = do_POST = do_OPTIONS = do_HEAD = do_PUT = do_PATCH = do_DELETE = handle_request
        class LoopbackServer(ThreadingHTTPServer):
            allow_reuse_address = False
            allow_reuse_port = False
            def server_bind(self):
                if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                    self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                super().server_bind()
            def __init__(self, *args):
                self.serving = threading.Event()
                super().__init__(*args)
            def serve_forever(self, poll_interval=.1):
                self.serving.set()
                try:
                    super().serve_forever(poll_interval)
                finally:
                    self.serving.clear()
        with self.dispatch_condition:
            if self.draining:
                _reject('HANDOFF_DRAINING')
            server = LoopbackServer(('127.0.0.1', port), Handler)
            self.listeners.append(server)
        server.daemon_threads = True
        return server

    def start(self):
        server = self.http_server()
        thread = threading.Thread(target=server.serve_forever, name='Contentrium-CUT-loopback', daemon=True)
        with self.dispatch_condition:
            if self.draining:
                server.server_close()
                _reject('HANDOFF_DRAINING')
            self.servers.append((server, thread))
            thread.start()
        return server

    def quiesce_for_handoff(self, timeout=10):
        """Native-only drain, required before issuing the durable handoff token.

        New HTTP callbacks remain denied even when a bounded drain fails. The
        launcher must stop dispatching native updater mutations before calling.
        This method never mints a ticket or writes an activation journal receipt.
        """
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 60:
            _reject('HANDOFF_DRAIN_FAILED')
        deadline = time.monotonic() + timeout
        with self.drain_lock:
            if self.resources_closed:
                return True
            with self.dispatch_condition:
                self.draining = True
                self.gate_open = False
                self.closed.set()
            self.jobs.stop_all(self.jobs.epoch)
            self.validations.cancel_all()
            for server in self.listeners:
                if server.serving.is_set():
                    server.shutdown()
                server.server_close()
            for server, thread in self.servers:
                thread.join(max(0, deadline-time.monotonic()))
                if thread.is_alive():
                    _reject('HANDOFF_DRAIN_FAILED')
            self.manager.join(max(0, deadline-time.monotonic()))
            with self.dispatch_condition:
                while self.active_dispatches:
                    remaining = deadline-time.monotonic()
                    if remaining <= 0:
                        _reject('HANDOFF_DRAIN_FAILED')
                    self.dispatch_condition.wait(remaining)
                connections = tuple(self.connections)
            if self.update_check is not None:
                self.update_check.join(max(0,deadline-time.monotonic()))
                if self.update_check.is_alive(): _reject('HANDOFF_DRAIN_FAILED')
            for connection in connections:
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            while not self.jobs.quiescent() and time.monotonic() < deadline:
                with self.dispatch_condition:
                    self.dispatch_condition.wait(min(.05, max(0, deadline-time.monotonic())))
            if not self.jobs.quiescent():
                _reject('HANDOFF_DRAIN_FAILED')
            self.jobs.close()
            self.validations.close(timeout=max(.01,deadline-time.monotonic()))
            with self.dispatch_condition:
                while self.connections:
                    remaining = deadline-time.monotonic()
                    if remaining <= 0:
                        _reject('HANDOFF_DRAIN_FAILED')
                    self.dispatch_condition.wait(remaining)
            acquired = []
            try:
                if self.updater:
                    for name in ['_checking', '_operation']:
                        barrier = getattr(self.updater, name, None)
                        if barrier is None or not barrier.acquire(timeout=max(0, deadline-time.monotonic())):
                            _reject('HANDOFF_DRAIN_FAILED')
                        acquired.append(barrier)
                if (time.monotonic() > deadline or self.manager.is_alive() or self.jobs.monitor.is_alive() or
                        self.active_dispatches or not self.jobs.quiescent()):
                    _reject('HANDOFF_DRAIN_FAILED')
                self.resources_closed = True
                return True
            finally:
                for barrier in reversed(acquired):
                    barrier.release()

    def close(self):
        self.quiesce_for_handoff(timeout=6)
