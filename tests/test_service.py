import hashlib
import http.client
import importlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
import wave
import time
import socket

from contentrium_cut.contract import canonical_hash
from test_policy import fixture, speech
import test_updater as updater_fixtures


class ServiceTests(unittest.TestCase):
    def setUp(self):
        try:
            self.module = importlib.import_module('contentrium_cut.service')
        except ModuleNotFoundError:
            self.fail('Authenticated service implementation is missing')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.host = {'pid': 17, 'createTime': 123, 'alive': True, 'processName': 'Adobe Premiere Pro.exe'}
        self.config = {'appVersion': '0.1.0', 'bundleId': 'old'}
        self.service = self.make_service()
        self.token = self.pair('panel-one')

    def make_service(self, installation=None, config=None):
        from test_s2_workers import ThreadContext,Scope
        service = self.module.CutService(self.tmp.name, dict(config or self.config,sourceReader=lambda p:open(p,'rb'),validationContext=ThreadContext(),validationScope=Scope), installation=installation,
                                        process_probe=lambda instance, claim: dict(self.host) if self.host['alive'] else None)
        self.addCleanup(service.close)
        return service

    def request(self, path, body=None, token=None, method='POST', headers=None):
        body=dict(body or {})
        if path=='/heartbeat':
            body.setdefault('protocolVersion',1);body.setdefault('appVersion',self.service.config['appVersion']);body.setdefault('bundleId',self.service.config['bundleId'])
        if path=='/project':body.setdefault('epoch',self.service.epoch)
        if path=='/apply/begin':
            import uuid
            body.setdefault('requestId',uuid.uuid4().hex)
        if path=='/apply/end':
            body.setdefault('epoch',0)
            if body.get('status') in ['failed','canceled']:body.setdefault('receipt',{'resultSequenceRef':None})
        if path=='/plan':
            body.setdefault('analysisId','0'*64);body.setdefault('analysisRevision',0)
            job=self.service.jobs.jobs.get(body.get('jobId'),{})
            if job.get('kind')=='analysis' and job.get('status')=='completed' and token:
                owner=self.service.auth.owner_id(token)
                record=self.service.owners.get(job['jobId'],{})
                if record.get('owner')==owner:
                    try:
                        state=self.service.coordinator.register_analysis(owner,job['jobId'],job['result'])
                        body.update(analysisId=state['analysisId'],analysisRevision=state['revision'])
                    except self.module.CutError:pass
        values = {'Host': '127.0.0.1:41737', 'Content-Type': 'application/json'}
        if token is not None:
            values['Authorization'] = 'Bearer ' + token
        values.update(headers or {})
        from test_source_validation import finish_validation
        call=lambda m,p,b:self.service.dispatch(m,p,values,json.dumps(b or {}).encode())
        return finish_validation(call,call(method,path,body))

    def pair(self, instance):
        code = self.service.auth.issue_code()
        status, body = self.request('/pair', {'code': code, 'instanceId': instance})
        self.assertEqual(status, 200)
        return body['token']

    def bind(self, token=None):
        args = fixture()
        snapshot = args[0]
        source=Path(self.tmp.name)/'analysis.wav'
        if not source.exists():source.write_bytes(b'original analysis media')
        snapshot['sources'][0]['canonicalPath']=str(source)
        for asset in snapshot['sources'][1:]:
            media=Path(self.tmp.name)/(asset['assetId']+'.mov');media.write_bytes(b'original camera media');asset['canonicalPath']=str(media)
        args[1]['sourceInputs']=[dict(assetId='CA-0',instanceKey='CA-0',path=str(source),sha256=hashlib.sha256(source.read_bytes()).hexdigest(),sessionOriginSeconds=0,sourceStartSeconds=0,sourceSampleRate=48000)]
        snapshot['supportFlags']['hostApplyVerified'] = True
        snapshot['snapshotHash'] = canonical_hash({k: v for k, v in snapshot.items() if k != 'snapshotHash'})
        self.request('/heartbeat',{'hostIdentity':self.host,'epoch':self.service.epoch,'batchRunning':False,'quiescent':True},token or self.token)
        status, body = self.request('/project', {'snapshot': snapshot, 'hostIdentity': self.host}, token or self.token)
        self.assertEqual(status, 200, body)
        return args

    def completed_analysis(self, args, token=None, job_id='analysis-fixture'):
        # A durable completed worker result is input to this integration layer.
        # Worker execution/process cancellation is independently tested in test_jobs.
        token = token or self.token
        owner = hashlib.sha256(token.encode()).hexdigest()
        args[1].setdefault('evidence',{})
        args[1].update(mediaSnapshotHash=args[0]['snapshotHash'],mediaInputs=[dict(assetId=s['assetId'],path=s['canonicalPath'],sha256=hashlib.sha256(Path(s['canonicalPath']).read_bytes()).hexdigest()) for s in args[0]['sources']])
        for valid in args[1].get('validAudioRanges',[]):valid.update(assetId='CA-0',startSample=0,endSample=320000,sampleRate=48000)
        self.service.jobs.jobs[job_id] = {'jobId': job_id, 'kind': 'analysis', 'status': 'completed', 'epoch': 0, 'result': args[1]}
        self.service.record_job(job_id, owner, args[0]['projectRef'], args[0]['snapshotHash'])
        return job_id

    def plan(self, args):
        args[1]['intervals'] = [speech(0, 200, 'A')]
        job = self.completed_analysis(args)
        status, plan = self.request('/plan', {'jobId': job, 'mapping': args[2], 'policy': args[3], 'epoch': 0}, self.token)
        self.assertEqual(status, 200, plan)
        return plan

    def signed_update(self):
        external = updater_fixtures.UpdaterTests('runTest')
        external.setUp()
        self.addCleanup(external.doCleanups)
        self.service.close()
        self.service = self.make_service(external.installer, dict(self.config, publicKey=external.public,
                                                                updateTransport=external.network))
        self.token = self.pair('update-panel')
        self.request('/heartbeat', {'hostIdentity': self.host, 'epoch': 0, 'batchRunning': False,
                                   'quiescent': True, 'appVersion': '0.1.0', 'bundleId': 'old'}, self.token)
        status, state = self.request('/updates/check', {}, self.token)
        self.assertEqual(status, 200)
        self.service.update_check.join(3)
        state=self.service.update_state()
        self.assertEqual(state['checkState'], 'AVAILABLE')
        return external, state['candidate']

    def test_health_minimal_and_every_control_endpoint_requires_auth(self):
        status, body = self.request('/health', method='GET')
        self.assertEqual(status, 200)
        self.assertEqual(body, {'protocolVersion': 1, 'appVersion': '0.1.0'})
        for path in ['/state', '/project', '/jobs', '/plan', '/heartbeat', '/apply/begin', '/updates/check']:
            with self.subTest(path=path):
                self.assertEqual(self.request(path)[0], 401)

    def test_any_origin_and_rebinding_host_are_denied_even_with_token(self):
        for origin in ['', 'null', 'http://localhost:41737', 'https://attacker.test']:
            with self.subTest(origin=origin):
                self.assertEqual(self.request('/state', token=self.token, headers={'Origin': origin})[0], 403)
        for host in ['attacker.test:41737', '127.0.0.1:80', 'localhost', '127.0.0.1:41737@attacker.test']:
            with self.subTest(host=host):
                self.assertEqual(self.request('/state', token=self.token, headers={'Host': host})[0], 403)

    def test_pair_code_is_single_use_and_no_http_code_generation_exists(self):
        code = self.service.auth.issue_code()
        self.assertEqual(self.request('/pair', {'code': code, 'instanceId': 'new-panel'})[0], 200)
        self.assertEqual(self.request('/pair', {'code': code, 'instanceId': 'other-panel'})[0], 403)
        self.assertEqual(self.request('/pair/code', token=self.token)[0], 404)

    def test_json_type_size_and_nonfinite_values_are_rejected(self):
        self.assertEqual(self.request('/state', token=self.token, headers={'Content-Type': 'text/plain'})[0], 415)
        headers = {'Host': 'localhost:41737', 'Content-Type': 'application/json', 'Authorization': 'Bearer '+self.token}
        for raw in [b'[]', b'{"a":NaN}', b'{"a":1,"a":2}', b'\xff', b'{']:
            with self.subTest(raw=raw):
                self.assertEqual(self.service.dispatch('POST', '/state', headers, raw)[0], 400)
        self.assertEqual(self.service.dispatch('POST', '/state', headers, b'x'*(self.module.MAX_BODY+1))[0], 413)

    def test_project_requires_actual_host_and_verified_snapshot_hash(self):
        args = fixture()
        self.assertEqual(self.request('/project', {'snapshot': args[0], 'hostIdentity': self.host}, self.token)[0], 409)
        self.host['alive'] = False
        args[0]['snapshotHash'] = canonical_hash({k: v for k, v in args[0].items() if k != 'snapshotHash'})
        self.assertEqual(self.request('/project', {'snapshot': args[0]}, self.token)[0], 409)

    def test_arbitrary_worker_sources_paths_and_commands_are_not_accepted(self):
        self.bind()
        for body in [{'kind': 'execute', 'command': 'whoami', 'epoch': 0},
                     {'kind': 'analysis', 'sources': [{'path': 'C:/secret'}], 'epoch': 0},
                     {'kind': 'sync', 'payload': {'path': 'C:/secret'}, 'epoch': 0}]:
            with self.subTest(body=body):
                self.assertEqual(self.request('/jobs', body, self.token)[0], 400)

    def test_native_model_token_only_not_request_token_or_root(self):
        for body in [{'kind': 'model-setup', 'epoch': 0, 'token': 'secret'},
                     {'kind': 'model-setup', 'epoch': 0, 'modelRoot': 'C:/secret'}]:
            self.assertEqual(self.request('/jobs', body, self.token)[0], 400)
        status, body = self.request('/jobs', {'kind': 'model-setup', 'epoch': 0, 'termsAccepted': True, 'revision': 'a'*40}, self.token)
        self.assertEqual(status, 409)
        self.assertNotIn('secret', json.dumps(body))

    def test_model_public_receipt_distinguishes_terminal_message_from_owned_process_drain(self):
        for status in ['running', 'canceling', 'completed', 'canceled', 'failed', 'interrupted']:
            value = dict(jobId='owned-model-job', kind='model-setup', status=status, epoch=0,
                         result={'private': 'provider'}, error={'code': 'MODEL_NOT_READY', 'message': 'private URL'})
            with self.service.jobs.lock:
                self.service.jobs.processes[value['jobId']] = {'owned': True}
            try:
                receipt = self.service._public_job(value)
                self.assertIs(receipt['drained'], False)
                self.assertNotIn('private', json.dumps(receipt))
                self.assertEqual(receipt['status'], status)
            finally:
                with self.service.jobs.lock:
                    self.service.jobs.processes.pop(value['jobId'])
            self.assertIs(self.service._public_job(value)['drained'], True)
        self.assertNotIn('drained', self.service._public_job(dict(jobId='other', kind='analysis', status='completed')))

    def test_sync_public_receipt_requires_owned_process_drain_after_terminal_message(self):
        for status in ['completed', 'canceled', 'failed', 'interrupted']:
            value = dict(jobId='owned-sync', kind='sync', status=status, epoch=0)
            with self.service.jobs.lock:
                self.service.jobs.processes[value['jobId']] = {'owned': True}
            try:
                self.assertIs(self.service._public_job(value)['drained'], False)
            finally:
                with self.service.jobs.lock:
                    self.service.jobs.processes.pop(value['jobId'])
            self.assertIs(self.service._public_job(value)['drained'], True)

    def test_job_read_cancel_and_plan_are_owned_by_authenticated_session(self):
        args = self.bind()
        job = self.completed_analysis(args)
        other = self.pair('panel-two')
        self.bind(other)
        for path, body, method in [('/jobs/'+job, {}, 'GET'), ('/jobs/'+job+'/cancel', {}, 'POST'),
                                   ('/plan', {'jobId': job, 'mapping': args[2], 'policy': args[3], 'epoch': 0}, 'POST')]:
            with self.subTest(path=path):
                self.assertEqual(self.request(path, body, other, method)[0], 403)

    def test_plan_requires_completed_analysis_and_current_snapshot_scope(self):
        args = self.bind()
        job = self.completed_analysis(args)
        self.service.jobs.jobs[job]['status'] = 'running'
        self.assertEqual(self.request('/plan', {'jobId': job, 'mapping': args[2], 'policy': args[3], 'epoch': 0}, self.token)[0], 409)
        self.service.jobs.jobs[job]['status'] = 'completed'
        self.service.jobs.jobs[job]['kind'] = 'sync'
        self.assertEqual(self.request('/plan', {'jobId': job, 'mapping': args[2], 'policy': args[3], 'epoch': 0}, self.token)[0], 409)

    def test_apply_lease_has_exact_plan_scope_and_batch_check_epoch(self):
        args = self.bind()
        plan = self.plan(args)
        status, lease = self.request('/apply/begin', {'planHash': plan['planHash'], 'snapshotHash': args[0]['snapshotHash'], 'epoch': 0}, self.token)
        self.assertEqual(status, 200)
        self.assertEqual(self.request('/apply/check', {'applyId': lease['applyId'], 'epoch': 0}, self.token)[0], 200)
        self.service.stop_all(1)
        self.assertEqual(self.request('/apply/check', {'applyId': lease['applyId'], 'epoch': 0}, self.token)[0], 409)
        self.assertEqual(self.request('/apply/end', {'applyId': lease['applyId'], 'status': 'canceled'}, self.token)[0], 200)
        self.assertEqual(self.request('/state', token=self.token)[1]['batchRunning'], False)

    def test_apply_other_panel_cannot_reuse_lease_or_plan(self):
        args = self.bind()
        plan = self.plan(args)
        other = self.pair('panel-two')
        self.bind(other)
        status, lease = self.request('/apply/begin', {'planHash': plan['planHash'], 'snapshotHash': args[0]['snapshotHash'], 'epoch': 0}, self.token)
        self.assertEqual(status, 200)
        self.assertEqual(self.request('/apply/check', {'applyId': lease['applyId'], 'epoch': 0}, other)[0], 403)
        self.assertEqual(self.request('/apply/begin', {'planHash': plan['planHash'], 'snapshotHash': args[0]['snapshotHash'], 'epoch': 0}, other)[0], 409)

    def test_stop_closes_global_gate_before_marking_panels_and_jobs(self):
        self.request('/heartbeat', {'hostIdentity': self.host, 'epoch': 0, 'batchRunning': False, 'quiescent': True}, self.token)
        self.service.stop_all(7)
        self.assertEqual(self.request('/state', token=self.token)[1]['stopEpoch'], 7)
        self.assertFalse(self.service.jobs.gate_open)
        self.assertEqual(self.request('/jobs', {'kind': 'model-setup', 'epoch': 0}, self.token)[0], 409)
        self.assertEqual(self.request('/heartbeat', {'hostIdentity': self.host, 'epoch': 7, 'batchRunning': False, 'quiescent': True}, self.token)[0], 200)

    def test_updates_missing_key_or_installer_display_unavailable_not_current(self):
        status, body = self.request('/updates/check', {}, self.token)
        self.assertEqual(status, 200)
        self.assertEqual(body['checkState'], 'UNAVAILABLE')
        self.assertEqual(self.request('/updates/start', {'candidateId': 'fake', 'manifestDigest': 'fake', 'requestId': 'click'}, self.token)[0], 409)

    def test_real_updater_download_after_ack_and_replace_only_after_host_exit(self):
        external, candidate = self.signed_update()
        external.installer.host_closed = False
        status, state = self.request('/updates/start', dict(candidateId=candidate['candidateId'], manifestDigest=candidate['manifestDigest'], requestId='click'), self.token)
        self.assertEqual(status, 200)
        self.assertFalse(self.service.jobs.gate_open)
        self.assertEqual(self.request('/state', token=self.token)[1]['stopEpoch'], state['updateEpoch'])
        self.service.advance_once()
        self.assertFalse(any(url.endswith('.zip') for url, _ in external.network.calls))
        self.request('/heartbeat', {'hostIdentity': self.host, 'epoch': state['updateEpoch'], 'batchRunning': False, 'quiescent': True}, self.token)
        self.assertEqual(self.request('/updates/ack', {'epoch': state['updateEpoch'], 'quiescent': True, 'batchRunning': False}, self.token)[0], 200)
        self.service.advance_once()
        self.assertTrue(any(url.endswith('.zip') for url, _ in external.network.calls))
        self.assertEqual(self.service.updater.state()['updateState'], 'WAITING_HOST_EXIT')
        self.assertFalse(external.installer.installs)
        external.installer.host_closed = True
        self.service.advance_once()
        self.assertEqual(self.service.updater.state()['updateState'], 'PENDING_ACTIVATION')
        self.assertEqual(len(external.installer.installs), 1)

    def test_cancel_reopens_gate_and_clears_every_panels_stop_epoch(self):
        external, candidate = self.signed_update()
        other = self.pair('another-panel')
        external.installer.host_closed = False
        self.request('/updates/start', dict(candidateId=candidate['candidateId'], manifestDigest=candidate['manifestDigest'], requestId='click'), self.token)
        self.assertIsNotNone(self.request('/state', token=other)[1]['stopEpoch'])
        self.assertEqual(self.request('/updates/cancel', {}, self.token)[0], 200)
        for token in [self.token, other]:
            state = self.request('/state', token=token)[1]
            self.assertTrue(state['gateOpen'])
            self.assertIsNone(state['stopEpoch'])

    def test_activation_handoff_is_native_config_only_and_is_not_exposed(self):
        external, candidate = self.signed_update()
        self.request('/updates/start', dict(candidateId=candidate['candidateId'], manifestDigest=candidate['manifestDigest'], requestId='click'), self.token)
        self.service.advance_once()
        handoff = self.service.updater.create_activation_handoff()
        self.service.close()
        config = dict(self.config, appVersion=candidate['appVersion'], bundleId=candidate['bundleId'], publicKey=external.public, updateTransport=external.network)
        self.service = self.make_service(external.installer, config)
        self.token = self.pair('restarted-panel')
        status, body = self.request('/state', {'activationHandoff': handoff['token']}, self.token)
        self.assertEqual(body['update']['updateState'], 'RECOVERY_REQUIRED')
        self.assertNotIn(handoff['token'], json.dumps(body))
        self.service.close()
        external.installer.verify_application = lambda version, bundle: version == candidate['appVersion'] and bundle == candidate['bundleId']
        self.service = self.make_service(external.installer, dict(config, activationHandoff=handoff['token']))
        self.token = self.pair('native-authorized-panel')
        body = self.request('/state', token=self.token)[1]
        self.assertEqual(body['update']['updateState'], 'PENDING_ACTIVATION')
        self.assertFalse(body['gateOpen'])
        self.assertNotIn(handoff['token'], json.dumps(body))

    def test_other_participant_cannot_be_acked_and_stale_heartbeat_is_not_exit(self):
        external, candidate = self.signed_update()
        other = self.pair('panel-unresponsive')
        status, state = self.request('/updates/start', dict(candidateId=candidate['candidateId'], manifestDigest=candidate['manifestDigest'], requestId='click'), self.token)
        self.assertEqual(status, 200)
        self.assertEqual(self.request('/updates/ack', {'participantId': 'other', 'epoch': state['updateEpoch'], 'quiescent': True, 'batchRunning': False}, self.token)[0], 403)
        self.service.advance_once()
        self.assertFalse(any(url.endswith('.zip') for url, _ in external.network.calls))
        self.assertEqual(self.request('/updates/ack', {'epoch': state['updateEpoch']-1, 'quiescent': True, 'batchRunning': False}, other)[0], 409)

    def test_http_listener_enforces_origin_host_and_json(self):
        server = self.service.http_server(port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), thread.join(2)))
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
        self.addCleanup(connection.close)
        connection.request('GET', '/health', headers={'Host': '127.0.0.1:41737'})
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.read())['protocolVersion'], 1)
        connection.close()
        connection.request('POST', '/state', '{}', {'Host': '127.0.0.1:41737', 'Content-Type': 'application/json', 'Authorization': 'Bearer '+self.token, 'Origin': 'null'})
        response = connection.getresponse()
        self.assertEqual(response.status, 403)
        self.assertNotIn(self.tmp.name.encode(), response.read())

    def test_project_cannot_be_rebound_during_an_active_apply(self):
        args = self.bind()
        plan = self.plan(args)
        status, _ = self.request('/apply/begin', {'planHash': plan['planHash'], 'snapshotHash': args[0]['snapshotHash'], 'epoch': 0}, self.token)
        self.assertEqual(status, 200)
        self.assertEqual(self.request('/project', {'snapshot': args[0], 'hostIdentity': self.host}, self.token)[0], 409)

    def test_heartbeat_cannot_erase_server_authorized_running_batch(self):
        args = self.bind()
        plan = self.plan(args)
        status, lease = self.request('/apply/begin', {'planHash': plan['planHash'], 'snapshotHash': args[0]['snapshotHash'], 'epoch': 0}, self.token)
        self.assertEqual(status, 200)
        self.request('/apply/check', {'applyId': lease['applyId'], 'epoch': 0,'batchId':1,'operationDigest':'c'*64,'resultSequenceRef':None}, self.token)
        self.request('/heartbeat', {'hostIdentity': self.host, 'epoch': 0, 'batchRunning': False, 'quiescent': True}, self.token)
        self.assertTrue(self.request('/state', token=self.token)[1]['batchRunning'])

    def test_update_during_plan_lookup_cannot_create_a_late_apply_lease(self):
        args = self.bind()
        plan = self.plan(args)
        looked_up, resume = threading.Event(), threading.Event()
        real_lookup = self.service.coordinator.require_plan
        def scheduled_lookup(*values):
            result = real_lookup(*values)
            looked_up.set()
            resume.wait(2)
            return result
        self.service.coordinator.require_plan = scheduled_lookup
        results = []
        request = threading.Thread(target=lambda: results.append(self.request('/apply/begin', {'planHash': plan['planHash'], 'snapshotHash': args[0]['snapshotHash'], 'epoch': 0}, self.token)))
        request.start()
        self.assertTrue(looked_up.wait(2))
        self.service.stop_all(1)
        resume.set()
        request.join(2)
        self.assertEqual(results[0][0], 409)
        self.assertFalse(self.service.applies)

    def test_apply_requires_verified_host_capability_and_readback(self):
        args = self.bind()
        plan = self.plan(args)
        args[0]['supportFlags']['hostApplyVerified'] = False
        args[0]['snapshotHash'] = canonical_hash({k: v for k, v in args[0].items() if k != 'snapshotHash'})
        self.request('/project', {'snapshot': args[0], 'hostIdentity': self.host}, self.token)
        plan = self.plan(args)
        self.assertEqual(self.request('/apply/begin', {'planHash': plan['planHash'], 'snapshotHash': args[0]['snapshotHash'], 'epoch': 0}, self.token)[0], 409)

    def test_project_change_during_plan_lookup_cannot_create_a_stale_lease(self):
        args = self.bind()
        plan = self.plan(args)
        looked_up, resume = threading.Event(), threading.Event()
        real_lookup = self.service.coordinator.require_plan
        def scheduled_lookup(*values):
            result = real_lookup(*values)
            looked_up.set()
            resume.wait(2)
            return result
        self.service.coordinator.require_plan = scheduled_lookup
        results = []
        request = threading.Thread(target=lambda: results.append(self.request('/apply/begin', {'planHash': plan['planHash'], 'snapshotHash': args[0]['snapshotHash'], 'epoch': 0}, self.token)))
        request.start()
        self.assertTrue(looked_up.wait(2))
        args[0]['sequenceRef'] = 'newly-connected-sequence'
        args[0]['snapshotHash'] = canonical_hash({k: v for k, v in args[0].items() if k != 'snapshotHash'})
        self.assertEqual(self.request('/project', {'snapshot': args[0], 'hostIdentity': self.host}, self.token)[0], 200)
        resume.set()
        request.join(2)
        self.assertEqual(results[0][0], 409)
        self.assertFalse(self.service.applies)

    def test_analysis_from_previous_snapshot_cannot_be_reused(self):
        args = self.bind()
        job = self.completed_analysis(args)
        args[0]['sequenceRef'] = 'different-sequence'
        args[0]['snapshotHash'] = canonical_hash({k: v for k, v in args[0].items() if k != 'snapshotHash'})
        self.assertEqual(self.request('/project', {'snapshot': args[0], 'hostIdentity': self.host}, self.token)[0], 200)
        self.assertEqual(self.request('/jobs/'+job, token=self.token, method='GET')[0], 403)

    def test_apply_rechecks_actual_process_identity_before_begin_and_batch(self):
        args = self.bind()
        plan = self.plan(args)
        begin = {'planHash': plan['planHash'], 'snapshotHash': args[0]['snapshotHash'], 'epoch': 0}
        self.host['createTime'] += 1
        self.assertEqual(self.request('/apply/begin', begin, self.token)[0], 409)
        self.host['createTime'] -= 1
        status, lease = self.request('/apply/begin', begin, self.token)
        self.assertEqual(status, 200)
        self.host['alive'] = False
        self.assertEqual(self.request('/apply/check', {'applyId': lease['applyId'], 'epoch': 0}, self.token)[0], 409)
        # Lost host evidence must still allow clearing the existing lease.
        self.assertEqual(self.request('/apply/end', {'applyId': lease['applyId'], 'status': 'canceled'}, self.token)[0], 200)

    def test_newly_identified_panel_ack_is_bound_to_its_current_host(self):
        external, candidate = self.signed_update()
        other = self.pair('late-heartbeat-panel')
        external.installer.host_closed = False
        _, state = self.request('/updates/start', dict(candidateId=candidate['candidateId'], manifestDigest=candidate['manifestDigest'], requestId='click'), self.token)
        epoch = state['updateEpoch']
        self.request('/heartbeat', {'hostIdentity': self.host, 'epoch': epoch, 'batchRunning': False, 'quiescent': True}, other)
        self.assertEqual(self.request('/updates/ack', {'epoch': epoch, 'quiescent': True, 'batchRunning': False}, other)[0], 200)
        participant = self.service.updater.state()['participants']['panel:'+hashlib.sha256(other.encode()).hexdigest()]
        self.assertEqual(participant['identity']['host'], self.host)
        self.assertEqual(participant['ackEpoch'], epoch)

    def test_completed_apply_requires_real_readback_receipt_fields(self):
        args = self.bind()
        plan = self.plan(args)
        _, lease = self.request('/apply/begin', {'planHash': plan['planHash'], 'snapshotHash': args[0]['snapshotHash'], 'epoch': 0}, self.token)
        body = {'applyId': lease['applyId'], 'status': 'completed', 'receipt': {'planHash': plan['planHash'], 'sourceUnchanged': True, 'readback': {'verified': False}}}
        self.assertEqual(self.request('/apply/end', body, self.token)[0], 409)
        self.assertTrue(self.service.applies[lease['applyId']]['active'])
        self.request('/apply/check',{'applyId':lease['applyId'],'epoch':0,'batchId':1,'operationDigest':'c'*64,'resultSequenceRef':None},self.token)
        self.request('/apply/result',{'applyId':lease['applyId'],'epoch':0,'resultSequenceRef':'result'},self.token)
        self.request('/apply/batch-end',{'applyId':lease['applyId'],'epoch':0,'batchId':1,'receipt':{'transactionReturned':True}},self.token)
        body['receipt'].update(sourceSnapshotHash=args[0]['snapshotHash'],resultSnapshotHash='d'*64,resultSequenceRef='result',saved=True)
        body['receipt']['readback']['verified'] = True
        self.assertEqual(self.request('/apply/end', body, self.token)[0], 200)

    def test_activation_is_native_only_and_requires_matching_current_runtime(self):
        external, candidate = self.signed_update()
        self.request('/updates/start', dict(candidateId=candidate['candidateId'], manifestDigest=candidate['manifestDigest'], requestId='click'), self.token)
        self.service.advance_once()
        self.assertEqual(self.service.updater.state()['updateState'], 'PENDING_ACTIVATION')
        with self.assertRaises(self.module.CutError):
            self.service.finalize_activation()
        self.assertEqual(self.request('/updates/activate', {'panelVersion': candidate['appVersion'], 'companionVersion': candidate['appVersion'], 'bundleId': candidate['bundleId'], 'handshake': True, 'dataReadable': True}, self.token)[0], 404)
        # Simulate the native executable starting with the new installed config.
        self.service.config.update(appVersion=candidate['appVersion'], bundleId=candidate['bundleId'])
        self.request('/heartbeat', {'hostIdentity': self.host, 'epoch': self.service.epoch, 'batchRunning': False, 'quiescent': True, 'appVersion': candidate['appVersion'], 'bundleId': candidate['bundleId']}, self.token)
        self.assertEqual(self.service.finalize_activation()['updateState'], 'COMPLETE')
        self.assertTrue(self.service.jobs.gate_open)

    def test_native_matching_heartbeat_cannot_override_failed_installation_probe(self):
        external, candidate = self.signed_update()
        self.request('/updates/start', dict(candidateId=candidate['candidateId'], manifestDigest=candidate['manifestDigest'], requestId='click'), self.token)
        self.service.advance_once()
        self.service.config.update(appVersion=candidate['appVersion'], bundleId=candidate['bundleId'])
        self.request('/heartbeat', {'hostIdentity': self.host, 'epoch': self.service.epoch, 'batchRunning': False, 'quiescent': True, 'appVersion': candidate['appVersion'], 'bundleId': candidate['bundleId']}, self.token)
        external.installer.good_activation = False
        self.assertNotEqual(self.service.finalize_activation()['updateState'], 'COMPLETE')
        self.assertEqual(len(external.installer.rollbacks), 1)

    def test_update_during_apply_completion_rejects_success_and_failed_retains_uncertainty(self):
        external, candidate = self.signed_update()
        external.installer.host_closed = False
        args = self.bind()
        plan = self.plan(args)
        _, lease = self.request('/apply/begin', {'planHash': plan['planHash'], 'snapshotHash': args[0]['snapshotHash'], 'epoch': 0}, self.token)
        self.request('/apply/check', {'applyId': lease['applyId'], 'epoch': 0,'batchId':1,'operationDigest':'c'*64,'resultSequenceRef':None}, self.token)
        inspected, resume = threading.Event(), threading.Event()
        real_snapshot = self.service._host
        def paused_snapshot(*values):
            result = real_snapshot(*values)
            inspected.set()
            resume.wait(2)
            return result
        self.service._host = paused_snapshot
        responses = []
        body = {'applyId': lease['applyId'], 'status': 'completed', 'receipt': {'planHash': plan['planHash'], 'sourceUnchanged': True, 'readback': {'verified': True}}}
        request = threading.Thread(target=lambda: responses.append(self.request('/apply/end', body, self.token)))
        request.start()
        self.addCleanup(lambda: (resume.set(), request.join(2)))
        self.assertTrue(inspected.wait(2))
        self.assertEqual(self.request('/updates/start', dict(candidateId=candidate['candidateId'], manifestDigest=candidate['manifestDigest'], requestId='during-completion'), self.token)[0], 200)
        resume.set()
        request.join(2)
        self.assertEqual(responses[0][0], 409)
        self.assertTrue(self.service.applies[lease['applyId']]['active'])
        self.assertTrue(self.service.panels[self.service._owner(self.token)]['batchRunning'])
        self.assertEqual(self.request('/apply/end', {'applyId': lease['applyId'], 'status': 'failed'}, self.token)[0], 200)
        self.assertTrue(self.service.panels[self.service._owner(self.token)]['batchRunning'])
        self.assertTrue(self.service.journal.status(self.service._owner(self.token))['blocked'])

    def test_old_apply_cannot_complete_after_update_canceled_and_new_epoch_reopened(self):
        external, candidate = self.signed_update()
        external.installer.host_closed = False
        args = self.bind()
        plan = self.plan(args)
        _, lease = self.request('/apply/begin', {'planHash': plan['planHash'], 'snapshotHash': args[0]['snapshotHash'], 'epoch': 0}, self.token)
        self.request('/updates/start', dict(candidateId=candidate['candidateId'], manifestDigest=candidate['manifestDigest'], requestId='closed-epoch'), self.token)
        self.request('/updates/cancel', {}, self.token)
        deadline=time.monotonic()+2
        while not self.service.gate_open and time.monotonic()<deadline:
            self.service.advance_once();threading.Event().wait(.01)
        self.assertTrue(self.service.gate_open)
        self.assertEqual(self.service.epoch, 1)
        body = {'applyId': lease['applyId'], 'status': 'completed', 'receipt': {'planHash': plan['planHash'], 'sourceUnchanged': True, 'readback': {'verified': True}}}
        self.assertEqual(self.request('/apply/end', body, self.token)[0], 409)
        self.assertEqual(self.request('/apply/end', {'applyId': lease['applyId'], 'status': 'canceled'}, self.token)[0], 200)

    def test_payload_preparation_cannot_promote_old_job_request_into_reopened_epoch(self):
        external, candidate = self.signed_update()
        external.installer.host_closed = False
        args = self.bind()
        selected = args[0]['sources'][:2]
        for index, source in enumerate(selected):
            path = Path(self.tmp.name) / ('microphone-'+str(index)+'.wav')
            with wave.open(str(path), 'wb') as stream:
                stream.setnchannels(1)
                stream.setsampwidth(2)
                stream.setframerate(16000)
                stream.writeframes(b'\0\0' * 16000)
            source['canonicalPath'] = str(path)
        args[0]['snapshotHash'] = canonical_hash({k: v for k, v in args[0].items() if k != 'snapshotHash'})
        self.assertEqual(self.request('/project', {'snapshot': args[0], 'hostIdentity': self.host}, self.token)[0], 200)
        prepared, resume = threading.Event(), threading.Event()
        real_payload = self.service.coordinator.sync_payload
        def paused_payload(*values):
            result = real_payload(*values)
            prepared.set()
            resume.wait(2)
            return result
        self.service.coordinator.sync_payload = paused_payload
        responses = []
        request = threading.Thread(target=lambda: responses.append(self.request('/jobs', {'kind': 'sync', 'epoch': 0, 'options': {'assetIds': [source['assetId'] for source in selected]}}, self.token)))
        request.start()
        self.addCleanup(lambda: (resume.set(), request.join(2)))
        self.assertTrue(prepared.wait(2))
        self.request('/updates/start', dict(candidateId=candidate['candidateId'], manifestDigest=candidate['manifestDigest'], requestId='during-payload'), self.token)
        self.request('/updates/cancel', {}, self.token)
        self.assertEqual(self.service.epoch, 1)
        self.assertTrue(self.service.jobs.gate_open)
        resume.set()
        request.join(2)
        self.assertEqual(responses[0][0], 409)
        self.assertFalse(self.service.jobs.jobs)
        self.assertTrue(self.service.jobs.quiescent())

    def completed_sync(self,args):
        job=self.completed_analysis(args,job_id='sync-fixture')
        assets=[source['assetId'] for source in args[0]['sources'][:2]]
        self.service.jobs.jobs[job].update(kind='sync',result={'schemaVersion':1,'referenceAssetId':assets[0],
            'offsets':{assets[0]:0.0,assets[1]:2.0},'sources':{asset:{'status':'accepted'} for asset in assets},'reviews':[],
            'mediaInputs':args[1]['mediaInputs'],'mediaSnapshotHash':args[0]['snapshotHash']})
        return job,[clip['instanceKey'] for clip in args[0]['clips'][:2]]

    def test_owned_completed_sync_plan_reuses_reviewed_apply_lease(self):
        args=self.bind();job,keys=self.completed_sync(args)
        status,plan=self.request('/sync-plan',{'jobId':job,'selectedClipInstanceKeys':keys,'epoch':0},self.token)
        self.assertEqual(status,200,plan)
        self.assertEqual(plan['planHash'],plan['syncPlanHash'])
        self.assertEqual(plan['anchorReferenceTicks'],'0')
        self.assertEqual(plan['placements'][1]['startTicks'],'508032000000')
        status,lease=self.request('/apply/begin',{'planHash':plan['planHash'],'snapshotHash':args[0]['snapshotHash'],'epoch':0},self.token)
        self.assertEqual(status,200,lease)
        self.assertEqual(lease['plan']['syncPlanHash'],plan['syncPlanHash'])

    def test_sync_plan_requires_owned_current_completed_sync_job_and_server_offsets(self):
        args=self.bind();job,keys=self.completed_sync(args)
        request={'jobId':job,'selectedClipInstanceKeys':keys,'epoch':0}
        self.service.jobs.jobs[job]['status']='running'
        self.assertEqual(self.request('/sync-plan',request,self.token)[0],409)
        self.service.jobs.jobs[job].update(status='completed',kind='analysis')
        self.assertEqual(self.request('/sync-plan',request,self.token)[0],409)
        self.service.jobs.jobs[job]['kind']='sync'
        other=self.pair('foreign-sync-panel');self.bind(other)
        self.assertEqual(self.request('/sync-plan',request,other)[0],403)
        self.assertEqual(self.request('/sync-plan',dict(request,offsets={'fake':0}),self.token)[0],400)
        args[0]['sequenceRef']='changed';args[0]['snapshotHash']=canonical_hash({k:v for k,v in args[0].items() if k!='snapshotHash'})
        self.request('/project',{'snapshot':args[0],'hostIdentity':self.host},self.token)
        self.assertEqual(self.request('/sync-plan',request,self.token)[0],403)

    def test_handoff_drain_waits_for_existing_dispatch_and_stops_old_writers(self):
        external,candidate=self.signed_update()
        self.request('/updates/start',dict(candidateId=candidate['candidateId'],manifestDigest=candidate['manifestDigest'],requestId='handoff'),self.token)
        self.service.advance_once()
        entered,resume=threading.Event(),threading.Event();real_control=self.service._control
        def paused_control(*values):
            entered.set();resume.wait(2);return real_control(*values)
        self.service._control=paused_control;response=[];drained=[]
        request=threading.Thread(target=lambda:response.append(self.request('/updates/check',{},self.token)))
        request.start();self.addCleanup(lambda:(resume.set(),request.join(2)))
        self.assertTrue(entered.wait(2))
        self.assertTrue(hasattr(self.service,'quiesce_for_handoff'),'A native drain is required before ticket creation')
        drain=threading.Thread(target=lambda:drained.append(self.service.quiesce_for_handoff(timeout=3)))
        drain.start();self.addCleanup(lambda:drain.join(4))
        deadline=time.monotonic()+1
        while not getattr(self.service,'draining',False) and time.monotonic()<deadline:time.sleep(.01)
        self.assertEqual(self.request('/updates/cancel',{},self.token)[0],409)
        self.assertFalse(drained)
        resume.set();request.join(2);drain.join(4)
        self.assertEqual(drained,[True])
        self.assertFalse(self.service.manager.is_alive())
        self.assertFalse(self.service.jobs.monitor.is_alive())
        ticket=self.service.updater.create_activation_handoff()
        journal=self.service.updater.journal.read_bytes()
        self.assertEqual(self.request('/updates/cancel',{},self.token)[0],409)
        self.assertEqual(self.service.updater.journal.read_bytes(),journal)
        self.assertNotIn(ticket['token'],journal.decode())

    def test_handoff_drain_timeout_aborts_with_late_dispatch_still_denied(self):
        entered,resume=threading.Event(),threading.Event();real_control=self.service._control
        def paused_control(*values):entered.set();resume.wait(2);return real_control(*values)
        self.service._control=paused_control
        request=threading.Thread(target=lambda:self.request('/state',token=self.token));request.start()
        self.addCleanup(lambda:(resume.set(),request.join(2)))
        self.assertTrue(entered.wait(2))
        self.assertTrue(hasattr(self.service,'quiesce_for_handoff'),'A bounded handoff drain is required')
        with self.assertRaises(self.module.CutError) as error:self.service.quiesce_for_handoff(timeout=.1)
        self.assertEqual(error.exception.code,'HANDOFF_DRAIN_FAILED')
        self.assertEqual(self.request('/updates/check',{},self.token)[0],409)

    def test_handoff_drain_closes_actual_listener_and_idle_keepalive_handlers(self):
        server=self.service.http_server(port=0);port=server.server_port
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(lambda:(server.shutdown(),server.server_close(),thread.join(2)))
        connection=http.client.HTTPConnection('127.0.0.1',port,timeout=2)
        self.addCleanup(connection.close)
        connection.request('GET','/health',headers={'Host':'127.0.0.1:41737'})
        response=connection.getresponse();self.assertEqual(response.status,200);response.read()
        self.assertTrue(self.service.connections)
        self.assertTrue(self.service.quiesce_for_handoff(timeout=2))
        self.assertFalse(self.service.connections)
        self.assertFalse(server.serving.is_set())
        thread.join(1);self.assertFalse(thread.is_alive())
        with self.assertRaises(OSError):socket.create_connection(('127.0.0.1',port),timeout=1)


if __name__ == '__main__':
    unittest.main()
