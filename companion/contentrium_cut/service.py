"""Authenticated native-panel loopback boundary and independent update manager.

Windows process/installation evidence is injected by the native launcher. The
HTTP client never supplies worker payloads, credentials, installer paths or a
process-exit assertion. Pair codes are issued only through the native AuthManager.
"""
import copy
from contextlib import nullcontext
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import re
import socket
import threading
import time
import uuid

from .auth import AuthManager
from .contract import CutError, integer
from .coordinator import Coordinator
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
        self.connections = set()
        self.listeners = []
        self.drain_lock = threading.Lock()
        self.resources_closed = False
        self.auth = AuthManager(self.root)
        self.coordinator = Coordinator(self.root)
        self.panels, self.applies, self.workers = {}, {}, {}
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
        return hashlib.sha256(token.encode('utf-8')).hexdigest()

    def _panel(self, token):
        session = self.auth.authenticate(token)
        owner = self._owner(token)
        with self.lock:
            panel = self.panels.setdefault(owner, {'instanceId': session['instanceId'], 'host': None,
                                                   'lastSeen': time.monotonic(), 'epoch': None,
                                                   'stopEpoch': self.epoch if not self.gate_open else None,
                                                   'batchRunning': False, 'quiescent': False,
                                                   'appVersion': None, 'bundleId': None})
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
        epoch = integer(payload.get('epoch'), 'epoch')
        if self.closed.is_set() or not self.gate_open or epoch != self.epoch:
            _reject('UPDATE_IN_PROGRESS')
        if self.updater:
            self.updater.assert_admitted(epoch)
        self.jobs.assert_admitted(epoch)
        return epoch

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
            status = ModelManager(self.root / 'models').state(model)
            models[model] = {key: status[key] for key in ['modelId', 'status', 'revision', 'setup'] if key in status}
        return {'protocolVersion': 1, 'appVersion': self.config['appVersion'], 'bundleId': self.config.get('bundleId'),
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
        return values

    def participant_exited(self, participant_id, identity):
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
            self.gate_open = False
            self.epoch = integer(epoch, 'epoch')
            for panel in self.panels.values():
                panel['stopEpoch'] = epoch
        self.jobs.stop_all(epoch)

    def advance_once(self):
        if self.draining:
            return self.update_state()
        if not self.updater:
            return self.update_state()
        state = self.updater.state()
        if state['updateState'] in PRE_REPLACE and self.jobs.quiescent():
            self.updater.advance()  # Updater independently rechecks every participant.
        if self.updater.gate_open and not self.gate_open and self.jobs.quiescent():
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

    def finalize_activation(self):
        """Native GUI only: build a receipt from matched authenticated heartbeat.

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
        with self.lock:
            candidates = [copy.deepcopy(panel) for panel in self.panels.values()
                          if panel['appVersion'] == self.config['appVersion'] and panel['bundleId'] == self.config.get('bundleId')]
        for panel in candidates:
            actual_host = self._host(panel, panel['host'])
            receipt = {'panelVersion': panel['appVersion'], 'companionVersion': self.config['appVersion'],
                       'bundleId': panel['bundleId'], 'handshake': True, 'dataReadable': True,
                       'hostIdentity': actual_host}
            self.updater.activate(receipt)
            self.advance_once()
            return self.update_state()
        _reject('UPDATE_ACTIVATION')

    def _control(self, method, path, token, body):
        owner, session, panel = self._panel(token)
        if path == '/state' and method in ['GET', 'POST']:
            return self.state(token)
        if path == '/project' and method == 'POST':
            actual = self._host(panel, body.get('hostIdentity'))
            with self.lock:
                if any(value['owner'] == owner and value['active'] for value in self.applies.values()):
                    _reject('APPLY_BUSY')
                result = self.coordinator.bind(owner, body['snapshot'])
                self.auth.bind_project(token, body['snapshot']['projectRef'])
                panel.update(host=actual, projectHost=actual)
            return result
        if path == '/heartbeat' and method == 'POST':
            actual = self._host(panel, body.get('hostIdentity', panel['host']))
            epoch = integer(body['epoch'], 'epoch')
            if any(not isinstance(body.get(field), bool) for field in ['batchRunning', 'quiescent']):
                _reject('INVALID_REQUEST')
            panel.update(host=actual, epoch=epoch, batchRunning=body['batchRunning'], quiescent=body['quiescent'],
                         appVersion=body.get('appVersion', body.get('panelVersion')), bundleId=body.get('bundleId'))
            if any(value['owner'] == owner and value['active'] and value.get('batchRunning') for value in self.applies.values()):
                panel.update(batchRunning=True, quiescent=False)
            return {'epoch': self.epoch, 'stopEpoch': panel['stopEpoch'], 'gateOpen': self.gate_open}
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
                if not isinstance(options, dict) or set(options) - {'mode', 'microphones', 'calibration', 'speakerCount', 'vadThreshold', 'bleedTolerance', 'assetIds', 'reference'}:
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
                return self._public_job(job, result=True)
        if path == '/plan' and method == 'POST':
            self._admit(body)
            self._snapshot(token, owner)
            job = self._owned_job(body['jobId'], token, owner)
            if job['kind'] != 'analysis' or job['status'] != 'completed':
                _reject('ANALYSIS_REQUIRED')
            result = self.coordinator.plan(owner, job['result'], body['mapping'], body['policy'])
            self._admit(body)  # A stopped epoch must not receive a late planner result.
            return result
        if path == '/sync-plan' and method == 'POST':
            epoch = self._admit(body)
            if set(body) - {'jobId', 'selectedClipInstanceKeys', 'epoch'}:
                _reject('INVALID_REQUEST')
            snapshot = self._snapshot(token, owner)
            job = self._owned_job(body['jobId'], token, owner)
            if job['kind'] != 'sync' or job['status'] != 'completed':
                _reject('SYNC_REQUIRED')
            plan = self.coordinator.sync_plan(owner, job['result'], body['selectedClipInstanceKeys'])
            with self._guard(epoch), self.lock:
                self._admit(body)
                if self._snapshot(token, owner)['snapshotHash'] != snapshot['snapshotHash']:
                    _reject('PLAN_SCOPE')
            return plan
        if path.startswith('/apply/') and method == 'POST':
            snapshot = self._snapshot(token, owner)
            if path == '/apply/begin':
                epoch = self._admit(body)
                self._project_host(panel)
                if snapshot.get('supportFlags', {}).get('hostApplyVerified') is not True:
                    _reject('HOST_APPLY_UNVERIFIED')
                plan = self.coordinator.require_plan(owner, body['planHash'], body['snapshotHash'])
                apply_id = uuid.uuid4().hex
                with self._guard(epoch), self.lock:
                    if not self.gate_open or self.epoch != epoch:
                        _reject('UPDATE_IN_PROGRESS')
                    self._project_host(panel)
                    if self._snapshot(token, owner)['snapshotHash'] != snapshot['snapshotHash']:
                        _reject('PLAN_SCOPE')
                    self.coordinator.require_plan(owner, body['planHash'], body['snapshotHash'])
                    if any(value['owner'] == owner and value['active'] for value in self.applies.values()):
                        _reject('APPLY_BUSY')
                    self.applies[apply_id] = {'owner': owner, 'projectRef': snapshot['projectRef'], 'snapshotHash': snapshot['snapshotHash'],
                                              'planHash': plan['planHash'], 'epoch': epoch, 'active': True, 'batchRunning': False}
                    panel['quiescent'] = False
                return {'applyId': apply_id, 'epoch': epoch, 'plan': plan}
            apply = self.applies.get(body.get('applyId'))
            if not apply or apply['owner'] != owner:
                _reject('APPLY_SCOPE')
            if not apply['active'] or apply['snapshotHash'] != snapshot['snapshotHash'] or apply['projectRef'] != snapshot['projectRef']:
                _reject('PLAN_SCOPE')
            if path == '/apply/check':
                self._admit(body)
                self._project_host(panel)
                with self._guard(body['epoch']), self.lock:
                    if apply['epoch'] != self.epoch or not self.gate_open:
                        _reject('UPDATE_IN_PROGRESS')
                    apply['batchRunning'] = True
                    panel.update(batchRunning=True, quiescent=False)
                return {'applyId': body['applyId'], 'epoch': self.epoch, 'allowed': True}
            if path == '/apply/end':
                status = body.get('status')
                if status not in ['completed', 'canceled', 'failed']:
                    _reject('INVALID_REQUEST')
                receipt = body.get('receipt', {})
                if status == 'completed' and (receipt.get('planHash') != apply['planHash'] or
                        receipt.get('sourceUnchanged') is not True or not isinstance(receipt.get('readback'), dict) or
                        receipt['readback'].get('verified') is not True):
                    _reject('HOST_READBACK_REQUIRED')
                completion_guard = self._guard(apply['epoch']) if status == 'completed' else nullcontext()
                with completion_guard, self.lock:
                    if not apply['active']:
                        _reject('PLAN_SCOPE')
                    if status == 'completed' and (self.closed.is_set() or not self.gate_open or self.epoch != apply['epoch']):
                        _reject('UPDATE_IN_PROGRESS')
                    apply.update(active=False, status=status, batchRunning=False)
                    panel.update(batchRunning=False, quiescent=True)
                return {'applyId': body['applyId'], 'status': status, 'hostReported': True}
        if path.startswith('/updates/') and method == 'POST':
            action = path[9:]
            if action == 'check':
                if self.updater:
                    self.updater.check()
                return self.update_state()
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
        """The same strict boundary is used by HTTP and native integration tests."""
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
            return statuses.get(code, 409), {'error': {'code': code, 'message': 'Request could not be completed.'}}
        except (ValueError, TypeError, KeyError, UnicodeError, AttributeError):
            return 400, {'error': {'code': 'INVALID_REQUEST', 'message': 'Use a valid request for this operation.'}}
        except Exception:
            return 500, {'error': {'code': 'SERVICE_FAILED', 'message': 'Local service needs attention.'}}

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
                if (any(len(self.headers.get_all(name, [])) > 1 for name in ['Host', 'Authorization', 'Content-Length', 'Content-Type']) or
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
                    self.respond(*service.dispatch(self.command, self.path, dict(self.headers), raw))
                except OSError:
                    self.close_connection = True
                except ValueError:
                    self.close_connection = True
                    self.respond(400, {'error': {'code': 'INVALID_REQUEST'}})
            do_GET = do_POST = do_OPTIONS = do_HEAD = do_PUT = do_PATCH = do_DELETE = handle_request
        class LoopbackServer(ThreadingHTTPServer):
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
