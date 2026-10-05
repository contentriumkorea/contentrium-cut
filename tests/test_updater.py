"""Updater trust and state tests. Network/Adobe installation are controlled boundaries."""
import base64
import hashlib
import importlib
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
import zipfile

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization


def zip_bytes(entries):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        for name, value in entries.items():
            archive.writestr(name, value)
    return stream.getvalue()


class Network:
    def __init__(self, release, documents):
        self.release, self.documents = release, documents
        self.calls = []
        self.status, self.headers = 200, {'ETag': '"candidate"'}
        self.release_by_id = {release['id']: json.loads(json.dumps(release))}
        self.release_status, self.release_headers = 200, {}

    def get(self, url, headers, timeout, max_bytes):
        from contentrium_cut.updater import HttpResponse
        self.calls.append((url, headers.copy()))
        if '/releases/latest' in url:
            body, status, response_headers = json.dumps(self.release).encode(), self.status, self.headers
        elif '/releases/' in url and url.rsplit('/', 1)[1].isdigit():
            body = json.dumps(self.release_by_id[int(url.rsplit('/', 1)[1])]).encode()
            status, response_headers = self.release_status, self.release_headers
        else:
            body, status, response_headers = self.documents[url], 200, {}
        return HttpResponse(status, response_headers, body, url)

    def download(self, url, destination, headers, cancel, expected_size):
        self.calls.append((url, headers.copy()))
        cancel()
        Path(destination).write_bytes(self.documents[url])
        cancel()


class Installation:
    def __init__(self):
        self.host_closed = True
        self.installs = []
        self.rollbacks = []
        self.good_activation = True
        self.good_previous = True
        self.fail_install = False

    def compatibility(self, manifest):
        return []

    def host_exited(self):
        return self.host_closed

    def snapshot(self, manifest):
        return {'previousVersion': '0.1.0', 'previousBundleId': 'old',
                'previousVerified': True, 'dataSnapshot': 'preserved-data'}

    def install(self, manifest, prepared_assets, snapshot):
        self.installs.append((manifest, prepared_assets, snapshot))
        if self.fail_install:
            raise OSError('controlled installer failure')
        return {'installed': True}

    def verify(self, version, bundle_id, receipt):
        return self.good_activation

    def rollback(self, snapshot):
        self.rollbacks.append(snapshot)
        return {'restored': True}

    def verify_previous(self, snapshot):
        return self.good_previous

    def verify_application(self, version, bundle_id):
        return version == '0.2.0' and bundle_id == 'bundle-new'


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        try:
            self.u = importlib.import_module('contentrium_cut.updater')
        except ModuleNotFoundError:
            self.fail('Updater implementation is absent')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.key = Ed25519PrivateKey.generate()
        self.public = self.key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.installer = Installation()
        self.stops = []
        self.prepare()

    def prepare(self, version='0.2.0', zip_entries=None, change=None):
        tag = 'v' + version
        prefix = f'https://github.com/contentriumkorea/contentrium-cut/releases/download/{tag}/'
        payloads = {'Contentrium-CUT-Windows-x64.zip': zip_bytes(zip_entries or {'companion/app.txt': 'new'}),
                    'Contentrium-CUT.ccx': zip_bytes({'manifest.json': '{"id":"com.contentrium.cut"}'})}
        assets = [{'role': role, 'assetId': i, 'name': name, 'size': len(payloads[name]),
                   'sha256': hashlib.sha256(payloads[name]).hexdigest()}
                  for role, i, name in [('companion', 101, 'Contentrium-CUT-Windows-x64.zip'), ('panel', 102, 'Contentrium-CUT.ccx')]]
        manifest = {'schemaVersion': 1, 'productId': 'com.contentrium.cut', 'displayName': 'Contentrium CUT',
                    'appVersion': version, 'channel': 'stable', 'releaseId': 42, 'tag': tag,
                    'builtAt': '2026-10-05T00:00:00Z', 'platform': 'windows', 'architecture': 'x64',
                    'panelVersion': version, 'companionVersion': version, 'bundleId': 'bundle-new',
                    'updateProtocolRange': {'min': 1, 'max': 1}, 'minUpdaterVersion': '0.1.0',
                    'dataSchemaFrom': [1], 'dataSchemaTo': 1, 'migrationId': 'none', 'assets': assets,
                    'modelCompatibility': {}, 'releaseNotes': 'Changes', 'signingKeyId': 'contentrium-cut-2026-01'}
        if change:
            change(manifest)
        raw = json.dumps(manifest, separators=(',', ':')).encode()
        signature = json.dumps({'algorithm': 'Ed25519', 'keyId': 'contentrium-cut-2026-01',
                                'signature': base64.b64encode(self.key.sign(raw)).decode()}).encode()
        payloads.update({'update-manifest.json': raw, 'update-manifest.sig': signature})
        self.manifest = manifest
        self.network = Network({'id': 42, 'name': 'Contentrium CUT', 'tag_name': tag,
                                'draft': False, 'prerelease': False, 'published_at': '2026-10-05T00:00:00Z',
                                'assets': [{'id': i, 'name': name, 'size': len(value), 'state': 'uploaded',
                                            'browser_download_url': prefix + name}
                                           for i, (name, value) in enumerate(payloads.items(), 101)]},
                               {prefix + name: value for name, value in payloads.items()})

    def manager(self, **kwargs):
        return self.u.UpdateManager(self.root, self.public, kwargs.pop('current_version', '0.1.0'), self.stops.append,
                                    self.installer, transport=self.network, **kwargs)

    def start(self, manager):
        state = manager.check()
        candidate = state['candidate']
        return manager.start(candidate['candidateId'], candidate['manifestDigest'], 'click-1')

    def test_semver_numeric_and_prerelease_precedence(self):
        self.assertGreater(self.u.SemVer('1.10.0'), self.u.SemVer('1.9.0'))
        self.assertLess(self.u.SemVer('1.0.0-rc.2'), self.u.SemVer('1.0.0-rc.10'))
        self.assertLess(self.u.SemVer('1.0.0-rc.10'), self.u.SemVer('1.0.0'))
        self.assertEqual(self.u.SemVer('1.0.0+one'), self.u.SemVer('1.0.0+two'))
        for value in ['01.2.3', '1.2', '1.2.3-01', 'v1.2.3', '1.2.3\n', '1.2.3٠']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.u.SemVer(value)

    def test_check_uses_json_headers_and_downloads_no_install_payload(self):
        state = self.manager().check()
        self.assertEqual(state['checkState'], 'AVAILABLE')
        self.assertEqual(len(self.network.calls), 3)
        headers = self.network.calls[0][1]
        self.assertEqual(headers['Accept'], 'application/vnd.github+json')
        self.assertIn('X-GitHub-Api-Version', headers)
        self.assertNotIn('Authorization', headers)
        self.assertFalse(any(url.endswith('.zip') for url, _ in self.network.calls))

    def test_signature_tampering_never_exposes_candidate(self):
        url = next(url for url in self.network.documents if url.endswith('.json'))
        self.network.documents[url] += b' '
        state = self.manager().check()
        self.assertEqual(state['checkState'], 'CHECK_FAILED')
        self.assertIsNone(state['candidate'])

    def test_signed_product_asset_and_compatibility_mismatches(self):
        cases = [lambda m: m.update(productId='evil.product'),
                 lambda m: m['assets'][0].update(assetId=999),
                 lambda m: m.update(panelVersion='0.9.0')]
        for mutate in cases:
            with self.subTest(mutate=mutate):
                self.prepare(change=mutate)
                self.assertEqual(self.manager().check()['checkState'], 'CHECK_FAILED')
        self.prepare(change=lambda m: m.update(updateProtocolRange={'min': 2, 'max': 3}))
        self.assertEqual(self.manager().check()['checkState'], 'INCOMPATIBLE')

    def test_gate_stop_and_idempotency_precede_any_payload_download(self):
        manager = self.manager()
        state = self.start(manager)
        self.assertFalse(manager.gate_open)
        self.assertEqual(self.stops, [1])
        self.assertEqual(state['updateState'], 'STOP_REQUESTED')
        self.assertEqual(len(self.network.calls), 3)
        candidate = state['candidate']
        duplicate = manager.start(candidate['candidateId'], candidate['manifestDigest'], 'click-1')
        self.assertEqual(duplicate['updateId'], state['updateId'])
        self.assertEqual(self.stops, [1])
        with self.assertRaises(Exception):
            manager.assert_admitted(0)

    def test_every_panel_must_ack_current_epoch_before_download(self):
        manager = self.manager()
        manager.register_participant('panel-a', {'pid': 12})
        manager.register_participant('panel-b', {'pid': 13})
        state = self.start(manager)
        manager.ack('panel-a', state['updateEpoch'], {'quiescent': True, 'batchRunning': False})
        self.assertEqual(manager.advance()['updateState'], 'QUIESCING')
        self.assertEqual(len(self.network.calls), 3)
        with self.assertRaises(Exception):
            manager.ack('panel-b', 0, {'quiescent': True, 'batchRunning': False})
        manager.ack('panel-b', state['updateEpoch'], {'quiescent': True, 'batchRunning': False})
        self.assertEqual(manager.advance()['updateState'], 'PENDING_ACTIVATION')
        self.assertEqual(len(self.installer.installs), 1)

    def test_bad_zip_paths_block_install_without_data_changes(self):
        for path in ['../escape.txt', 'C:/escape.txt', '/escape.txt', 'nested\\..\\escape', 'safe/file:stream', 'CON.txt']:
            with self.subTest(path=path):
                self.prepare(zip_entries={path: 'evil'})
                manager = self.manager()
                self.start(manager)
                self.assertEqual(manager.advance()['updateState'], 'FAILED_BEFORE_REPLACE')
                self.assertFalse(self.installer.installs)

    def test_corrupt_download_keeps_previous_install(self):
        manager = self.manager()
        self.start(manager)
        url = next(url for url in self.network.documents if url.endswith('.zip'))
        self.network.documents[url] = b'corrupt'
        state = manager.advance()
        self.assertEqual(state['updateState'], 'FAILED_BEFORE_REPLACE')
        self.assertFalse(self.installer.installs)
        self.assertFalse(list(self.root.rglob('*.zip')))

    def test_host_open_waits_and_actual_activation_required(self):
        self.installer.host_closed = False
        manager = self.manager()
        self.start(manager)
        self.assertEqual(manager.advance()['updateState'], 'WAITING_HOST_EXIT')
        self.assertFalse(self.installer.installs)
        self.installer.host_closed = True
        self.assertEqual(manager.advance()['updateState'], 'PENDING_ACTIVATION')
        self.assertFalse(manager.gate_open)
        state = manager.activate({'panelVersion': '0.2.0', 'companionVersion': '0.2.0',
                                  'bundleId': 'bundle-new', 'handshake': True, 'dataReadable': True})
        self.assertEqual(state['updateState'], 'COMPLETE')
        self.assertTrue(manager.gate_open)
        saved = json.loads((self.root / 'updates' / 'journal.json').read_text())
        self.assertEqual(saved['updateState'], 'COMPLETE')

    def test_mixed_activation_rolls_back_only_after_previous_verification(self):
        manager = self.manager()
        self.start(manager)
        manager.advance()
        state = manager.activate({'panelVersion': '0.1.0', 'companionVersion': '0.2.0',
                                  'bundleId': 'bundle-new', 'handshake': True, 'dataReadable': True})
        self.assertEqual(state['updateState'], 'ROLLED_BACK')
        self.assertTrue(manager.gate_open)
        self.assertEqual(len(self.installer.rollbacks), 1)

    def test_failed_rollback_keeps_gate_closed(self):
        self.installer.fail_install = True
        self.installer.good_previous = False
        manager = self.manager()
        self.start(manager)
        self.assertEqual(manager.advance()['updateState'], 'RECOVERY_REQUIRED')
        self.assertFalse(manager.gate_open)

    def test_success_persistence_failure_cannot_be_visible_success(self):
        manager = self.manager()
        self.start(manager)
        manager.advance()
        real_persist = manager._persist
        def fail_complete(state):
            if state['updateState'] == 'COMPLETE':
                raise OSError('disk full')
            return real_persist(state)
        manager._persist = fail_complete
        manager.activate({'panelVersion': '0.2.0', 'companionVersion': '0.2.0',
                          'bundleId': 'bundle-new', 'handshake': True, 'dataReadable': True})
        self.assertNotEqual(manager.state()['updateState'], 'COMPLETE')
        self.assertEqual(json.loads((self.root / 'updates' / 'journal.json').read_text())['updateState'], 'ROLLED_BACK')

    def test_restart_keeps_interrupted_install_locked(self):
        manager = self.manager()
        self.start(manager)
        restored = self.manager()
        self.assertFalse(restored.gate_open)
        self.assertEqual(restored.state()['updateEpoch'], 1)
        restored.advance()
        self.assertFalse(restored.gate_open)

    def test_cancel_before_replace_requires_verified_previous_install(self):
        manager = self.manager()
        self.start(manager)
        self.assertEqual(manager.cancel()['updateState'], 'CANCELED')
        self.assertTrue(manager.gate_open)
        self.assertFalse(self.installer.installs)

    def test_rate_limit_avoids_repeat_network_and_never_calls_current(self):
        self.network.status = 429
        self.network.headers = {'Retry-After': '120'}
        manager = self.manager(clock=lambda: 1000)
        self.assertEqual(manager.check()['checkState'], 'CHECK_FAILED')
        count = len(self.network.calls)
        self.assertEqual(manager.check()['checkState'], 'CHECK_FAILED')
        self.assertEqual(len(self.network.calls), count)

    def test_304_requires_verified_cache(self):
        manager = self.manager()
        self.assertEqual(manager.check()['checkState'], 'AVAILABLE')
        self.network.status = 304
        self.assertEqual(manager.check()['checkState'], 'AVAILABLE')
        self.assertEqual(self.network.calls[-1][1]['If-None-Match'], '"candidate"')

    def test_stop_callback_failure_can_never_install(self):
        def stop_failure(epoch):
            raise OSError('worker control unavailable')
        manager = self.manager()
        manager.stop_all = stop_failure
        candidate = manager.check()['candidate']
        with self.assertRaises(Exception):
            manager.start(candidate['candidateId'], candidate['manifestDigest'], 'click')
        manager.advance()
        self.assertFalse(manager.gate_open)
        self.assertFalse(self.installer.installs)
        self.assertEqual(len(self.network.calls), 3)

    def test_journal_corruption_is_not_recoverable_without_install_evidence(self):
        (self.root / 'updates').mkdir(exist_ok=True)
        (self.root / 'updates' / 'journal.json').write_text('{broken')
        manager = self.manager()
        self.assertEqual(manager.recover()['updateState'], 'RECOVERY_REQUIRED')
        self.assertFalse(manager.gate_open)

    def test_rechecking_304_renews_candidate_without_install_download(self):
        clock = [1000]
        manager = self.manager(clock=lambda: clock[0])
        manager.check()
        self.network.status = 304
        clock[0] = 5000
        state = manager.check()
        candidate = state['candidate']
        self.assertEqual(manager.start(candidate['candidateId'], candidate['manifestDigest'], 'click')['updateState'], 'STOP_REQUESTED')
        self.assertEqual(len(self.network.calls), 4)

    def test_cancel_during_download_never_calls_installer(self):
        entered, released = threading.Event(), threading.Event()
        actual_download = self.network.download
        def slow_download(url, destination, headers, cancel, expected_size):
            entered.set()
            if not released.wait(2):
                raise RuntimeError('test deadlock')
            actual_download(url, destination, headers, cancel, expected_size)
        self.network.download = slow_download
        manager = self.manager()
        self.start(manager)
        runner = threading.Thread(target=manager.advance)
        runner.start()
        self.addCleanup(lambda: runner.join(2))
        self.assertTrue(entered.wait(2))
        self.assertTrue(manager.cancel()['cancelRequested'])
        released.set()
        runner.join(2)
        self.assertFalse(runner.is_alive())
        self.assertEqual(manager.state()['updateState'], 'CANCELED')
        self.assertFalse(self.installer.installs)
        self.assertFalse(list(self.root.rglob('*.part')))

    def test_redirect_allowlist_rejects_credentials_http_and_other_repository(self):
        for url in ['http://github.com/contentriumkorea/contentrium-cut/releases/download/v1/a.zip',
                    'https://github.com.evil.test/file', 'https://user:pass@github.com/file',
                    'https://github.com/other/repo/releases/download/v1/a.zip',
                    'https://api.github.com/repos/other/repo/releases/assets/1',
                    'https://release-assets.githubusercontent.com:444/file']:
            with self.subTest(url=url), self.assertRaises(Exception):
                self.u.validate_url(url)
        self.assertEqual(self.u.validate_url('https://release-assets.githubusercontent.com/path?signature=opaque'),
                         'https://release-assets.githubusercontent.com/path?signature=opaque')

    def test_zip_symlink_and_case_aliases_rejected_before_install(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            symlink = zipfile.ZipInfo('link')
            symlink.create_system = 3
            symlink.external_attr = (0o120777 << 16)
            archive.writestr(symlink, '../escape')
        path = self.root / 'linked.zip'
        path.write_bytes(stream.getvalue())
        with self.assertRaises(Exception):
            self.u.validate_zip(path)
        path.write_bytes(zip_bytes({'A/file': 'one', 'a/FILE': 'two'}))
        with self.assertRaises(Exception):
            self.u.validate_zip(path)

    def test_manifest_invalid_schema_prevents_install(self):
        for mutate in [lambda m: m.update(schemaVersion=True),
                       lambda m: m.update(dataSchemaTo='oops'),
                       lambda m: m.update(builtAt='not-date'),
                       lambda m: m.update(migrationId=['shell']),
                       lambda m: m.update(modelCompatibility='wrong')]:
            with self.subTest(mutate=mutate):
                self.prepare(change=mutate)
                self.assertEqual(self.manager().check()['checkState'], 'CHECK_FAILED')

    def test_forged_final_url_rejected_in_metadata_transport(self):
        actual_get = self.network.get
        def forged_get(*args):
            result = actual_get(*args)
            return self.u.HttpResponse(result.status, result.headers, result.body, 'https://evil.test/payload')
        self.network.get = forged_get
        self.assertEqual(self.manager().check()['checkState'], 'CHECK_FAILED')

    def test_no_new_notification_for_same_lower_or_unstable_version(self):
        for version, expected in [('0.1.0', 'CURRENT'), ('0.0.9', 'CURRENT'), ('0.2.0-rc.1', 'CHECK_FAILED')]:
            with self.subTest(version=version):
                self.prepare(version=version)
                manager = self.manager()
                self.assertEqual(manager.check()['checkState'], expected)
                self.assertIsNone(manager.state()['candidate'])
                self.assertTrue(manager.gate_open)

    def test_late_participant_blocks_host_wait_install(self):
        current = []
        self.installer.host_closed = False
        manager = self.manager(participants=lambda: current)
        self.start(manager)
        manager.advance()
        current.append({'id': 'late-panel', 'identity': {'pid': 55}})
        self.installer.host_closed = True
        self.assertEqual(manager.advance()['updateState'], 'QUIESCING')
        self.assertFalse(self.installer.installs)

    def test_previous_exit_requires_explicit_process_evidence(self):
        manager = self.manager(participant_exited=lambda participant_id, identity: identity.get('verifiedExited') is True)
        manager.register_participant('gone', {'pid': 12, 'verifiedExited': True})
        self.start(manager)
        self.assertEqual(manager.advance()['updateState'], 'PENDING_ACTIVATION')

    def test_pending_activation_cancel_restores_matched_previous_bundle(self):
        manager = self.manager()
        self.start(manager)
        manager.advance()
        self.assertEqual(manager.cancel()['updateState'], 'ROLLED_BACK')
        self.assertEqual(len(self.installer.rollbacks), 1)

    def test_restart_can_restore_recorded_snapshot(self):
        manager = self.manager()
        self.start(manager)
        manager.advance()
        restored = self.manager()
        self.assertFalse(restored.gate_open)
        self.assertEqual(restored.recover()['updateState'], 'ROLLED_BACK')
        self.assertTrue(restored.gate_open)

    def test_staged_file_tampering_while_waiting_for_host_blocks_replace(self):
        self.installer.host_closed = False
        manager = self.manager()
        self.start(manager)
        state = manager.advance()
        Path(state['preparedAssets']['panel']['path']).write_bytes(b'changed after preparation')
        self.installer.host_closed = True
        self.assertEqual(manager.advance()['updateState'], 'FAILED_BEFORE_REPLACE')
        self.assertFalse(self.installer.installs)

    def test_participant_arriving_during_snapshot_prevents_install(self):
        current = []
        manager = self.manager(participants=lambda: current)
        actual_snapshot = self.installer.snapshot
        def registration_during_snapshot(manifest):
            current.append({'id': 'new-panel', 'identity': {'pid': 999}})
            return actual_snapshot(manifest)
        self.installer.snapshot = registration_during_snapshot
        self.start(manager)
        self.assertEqual(manager.advance()['updateState'], 'QUIESCING')
        self.assertFalse(self.installer.installs)

    def test_stop_is_invoked_even_if_initial_intent_write_fails(self):
        manager = self.manager()
        candidate = manager.check()['candidate']
        def failed_persist(state):
            raise OSError('disk full')
        manager._persist = failed_persist
        with self.assertRaises(OSError):
            manager.start(candidate['candidateId'], candidate['manifestDigest'], 'click')
        self.assertFalse(manager.gate_open)
        self.assertEqual(self.stops, [1])
        self.assertFalse(self.installer.installs)

    def test_stop_callback_does_not_hold_updater_lock_against_worker_gate(self):
        manager = self.manager()
        observed = []
        def stop(epoch):
            done = threading.Event()
            def worker_boundary():
                observed.append(manager.gate_open)
                done.set()
            thread = threading.Thread(target=worker_boundary)
            thread.start()
            observed.append(done.wait(0.5))
            self.addCleanup(lambda: thread.join(2))
        manager.stop_all = stop
        self.start(manager)
        self.assertEqual(observed, [False, True])

    def test_cancel_keeps_gate_closed_until_inflight_stop_callback_returns(self):
        entered, released = threading.Event(), threading.Event()
        manager = self.manager()
        candidate = manager.check()['candidate']
        errors = []
        def stop(epoch):
            entered.set()
            if not released.wait(2):
                raise RuntimeError('test timed out')
        manager.stop_all = stop
        def start_first():
            try:
                manager.start(candidate['candidateId'], candidate['manifestDigest'], 'first')
            except Exception as error:
                errors.append(error)
        first = threading.Thread(target=start_first)
        first.start()
        self.addCleanup(lambda: first.join(2))
        self.addCleanup(released.set)
        self.assertTrue(entered.wait(2))
        canceled = manager.cancel()
        self.assertFalse(canceled['gateOpen'])
        duplicate = manager.start(candidate['candidateId'], candidate['manifestDigest'], 'second')
        self.assertEqual(duplicate['updateEpoch'], 1)
        self.assertEqual(manager.advance()['updateState'], 'STOP_REQUESTED')
        released.set()
        first.join(2)
        self.assertFalse(first.is_alive())
        self.assertFalse(errors)
        self.assertEqual(manager.state()['updateState'], 'CANCELED')
        self.assertTrue(manager.gate_open)
        self.assertFalse(self.installer.installs)

    def test_delayed_stop_completion_does_not_overwrite_another_update_identity(self):
        manager = self.manager()
        def superseded_stop(epoch):
            # Fault injection models a persisted successor after control handoff.
            manager._commit(updateId='successor', updateEpoch=2, epoch=2, stopSignaled=False,
                            updateState='STOP_REQUESTED', gateOpen=False)
        manager.stop_all = superseded_stop
        self.start(manager)
        self.assertEqual(manager.state()['updateId'], 'successor')
        self.assertFalse(manager.state()['stopSignaled'])
        self.assertEqual(manager.advance()['updateState'], 'STOP_REQUESTED')
        self.assertFalse(self.installer.installs)

    def test_delayed_stop_error_does_not_overwrite_another_update_identity(self):
        manager = self.manager()
        def superseded_stop(epoch):
            manager._commit(updateId='successor', updateEpoch=2, epoch=2, stopSignaled=False,
                            updateState='STOP_REQUESTED', gateOpen=False)
            raise OSError('old callback failed after handoff')
        manager.stop_all = superseded_stop
        with self.assertRaises(OSError):
            self.start(manager)
        self.assertEqual(manager.state()['updateId'], 'successor')
        self.assertEqual(manager.state()['updateState'], 'STOP_REQUESTED')
        self.assertFalse(manager.state()['stopSignaled'])

    def test_incomplete_terminal_journal_never_reopens_admission(self):
        (self.root / 'updates').mkdir(exist_ok=True)
        for phase in ['COMPLETE', 'ROLLED_BACK', 'CANCELED', 'FAILED_BEFORE_REPLACE']:
            with self.subTest(phase=phase):
                (self.root / 'updates' / 'journal.json').write_text(json.dumps({
                    'schemaVersion': 1, 'updateEpoch': 4, 'updateState': phase, 'gateOpen': True}))
                manager = self.manager()
                self.assertEqual(manager.state()['updateState'], 'RECOVERY_REQUIRED')
                self.assertFalse(manager.gate_open)

    def test_complete_journal_requires_matching_activation_and_signed_bundle(self):
        manager = self.manager()
        self.start(manager)
        manager.advance()
        manager.activate({'panelVersion': '0.2.0', 'companionVersion': '0.2.0',
                          'bundleId': 'bundle-new', 'handshake': True, 'dataReadable': True})
        real = json.loads(manager.journal.read_text())
        self.assertEqual(self.manager().state()['updateState'], 'COMPLETE')
        for field, value in [('activationReceipt', {}), ('currentVersion', '0.9.0'),
                             ('bundleId', 'forged'), ('epoch', 90)]:
            with self.subTest(field=field):
                changed = dict(real, **{field: value})
                manager.journal.write_text(json.dumps(changed))
                self.assertFalse(self.manager().gate_open)

    def test_valid_canceled_journal_reloads_with_verified_previous_install(self):
        manager = self.manager()
        self.start(manager)
        manager.cancel()
        restored = self.manager()
        self.assertEqual(restored.state()['updateState'], 'CANCELED')
        self.assertTrue(restored.gate_open)

    def test_withdrawn_or_recreated_pinned_release_cannot_install_cached_assets(self):
        for status, mutate in [(404, lambda r: None),
                               (200, lambda r: r.update(id=43)),
                               (200, lambda r: r.update(tag_name='v0.3.0')),
                               (200, lambda r: r.update(draft=True)),
                               (200, lambda r: r.update(prerelease=True)),
                               (200, lambda r: r['assets'][0].update(id=201))]:
            with self.subTest(status=status, mutate=mutate):
                self.prepare()
                manager = self.manager()
                self.start(manager)
                self.network.release_status = status
                mutate(self.network.release_by_id[42])
                self.assertEqual(manager.advance()['updateState'], 'FAILED_BEFORE_REPLACE')
                self.assertFalse(self.installer.installs)
                self.assertTrue(any(url.endswith('/releases/42') for url, headers in self.network.calls))

    def test_latest_change_does_not_replace_chosen_candidate(self):
        manager = self.manager()
        self.start(manager)
        self.network.release.update(id=99, tag_name='v0.9.0')
        self.assertEqual(manager.advance()['updateState'], 'PENDING_ACTIVATION')
        self.assertEqual(self.installer.installs[0][0]['appVersion'], '0.2.0')

    def test_pinned_release_rate_limit_waits_without_requery_or_replace(self):
        clock = [1000]
        manager = self.manager(clock=lambda: clock[0])
        self.start(manager)
        self.network.release_status = 429
        self.network.release_headers = {'Retry-After': '120'}
        self.assertEqual(manager.advance()['updateState'], 'WAITING_HOST_EXIT')
        count = len(self.network.calls)
        self.assertEqual(manager.advance()['updateState'], 'WAITING_HOST_EXIT')
        self.assertEqual(len(self.network.calls), count)
        self.assertFalse(self.installer.installs)
        clock[0] = 1121
        self.network.release_status = 200
        self.assertEqual(manager.advance()['updateState'], 'PENDING_ACTIVATION')

    def test_release_withdrawal_during_host_wait_prevents_install(self):
        manager = self.manager()
        self.installer.host_closed = False
        self.start(manager)
        self.assertEqual(manager.advance()['updateState'], 'WAITING_HOST_EXIT')
        self.network.release_status = 404
        self.installer.host_closed = True
        self.assertEqual(manager.advance()['updateState'], 'FAILED_BEFORE_REPLACE')
        self.assertFalse(self.installer.installs)

    def test_complete_clears_candidate_and_rejects_reinstall_of_same_version(self):
        manager = self.manager()
        start = self.start(manager)
        candidate = start['candidate']
        manager.advance()
        complete = manager.activate({'panelVersion': '0.2.0', 'companionVersion': '0.2.0',
                                     'bundleId': 'bundle-new', 'handshake': True, 'dataReadable': True})
        self.assertIsNone(complete['candidate'])
        self.assertNotEqual(complete['checkState'], 'AVAILABLE')
        with self.assertRaises(Exception):
            manager.start(candidate['candidateId'], candidate['manifestDigest'], 'reinstall')
        self.assertEqual(self.stops, [1])
        self.assertEqual(manager.state()['updateState'], 'COMPLETE')

    def test_start_rechecks_signed_version_against_current_even_if_candidate_is_stale(self):
        manager = self.manager()
        candidate = manager.check()['candidate']
        manager.current_version = '0.2.0'
        manager._commit(currentVersion='0.2.0')
        with self.assertRaises(Exception):
            manager.start(candidate['candidateId'], candidate['manifestDigest'], 'stale')
        self.assertTrue(manager.gate_open)
        self.assertFalse(self.stops)

    def test_completed_bundle_evidence_survives_discovery_of_next_update(self):
        manager = self.manager()
        self.start(manager)
        manager.advance()
        manager.activate({'panelVersion': '0.2.0', 'companionVersion': '0.2.0',
                          'bundleId': 'bundle-new', 'handshake': True, 'dataReadable': True})
        self.prepare(version='0.3.0', change=lambda m: m.update(releaseId=43))
        self.network.release['id'] = 43
        manager.transport = self.network
        self.assertEqual(manager.check()['candidate']['appVersion'], '0.3.0')
        restored = self.manager()
        self.assertEqual(restored.state()['updateState'], 'COMPLETE')
        self.assertTrue(restored.gate_open)

    def test_bad_recovery_snapshot_never_reaches_installing(self):
        initial_root = self.root
        for index, descriptor in enumerate([{'previousVerified': True},
                           {'previousVerified': True, 'previousVersion': '9.0.0', 'dataSnapshot': 'backup'},
                           {'previousVerified': True, 'previousVersion': '0.1.0', 'dataSnapshot': ''}]):
            with self.subTest(descriptor=descriptor):
                self.root = initial_root / str(index)
                manager = self.manager()
                self.installer.snapshot = lambda manifest, value=descriptor: value
                self.start(manager)
                self.assertEqual(manager.advance()['updateState'], 'FAILED_BEFORE_REPLACE')
                self.assertFalse(self.installer.installs)

    def test_none_install_result_cannot_be_persisted_as_pending_or_complete(self):
        manager = self.manager()
        self.installer.install = lambda manifest, paths, snapshot: None
        self.start(manager)
        self.assertEqual(manager.advance()['updateState'], 'ROLLED_BACK')
        self.assertTrue(manager.gate_open)
        self.assertEqual(self.manager().state()['updateState'], 'ROLLED_BACK')

    def test_none_rollback_result_cannot_be_persisted_as_rolled_back(self):
        manager = self.manager()
        self.installer.fail_install = True
        self.installer.rollback = lambda snapshot: None
        self.start(manager)
        self.assertEqual(manager.advance()['updateState'], 'RECOVERY_REQUIRED')
        self.assertFalse(manager.gate_open)

    def test_admission_guard_serializes_cache_promotion_before_update_stop(self):
        manager = self.manager()
        candidate = manager.check()['candidate']
        attempted = threading.Event()
        result = []
        def start_update():
            attempted.set()
            result.append(manager.start(candidate['candidateId'], candidate['manifestDigest'], 'guarded'))
        with manager.admission_guard(0) as epoch:
            self.assertEqual(epoch, 0)
            runner = threading.Thread(target=start_update)
            runner.start()
            self.addCleanup(lambda: runner.join(2))
            self.assertTrue(attempted.wait(2))
            (self.root / 'confirmed-cache.json').write_text('{"complete":true}')
            self.assertFalse(self.stops)
        runner.join(2)
        self.assertFalse(runner.is_alive())
        self.assertEqual(result[0]['updateState'], 'STOP_REQUESTED')
        with self.assertRaises(Exception), manager.admission_guard(0):
            pass

    def pending_handoff(self, clock=lambda: 1000):
        manager = self.manager(clock=clock)
        self.start(manager)
        self.assertEqual(manager.advance()['updateState'], 'PENDING_ACTIVATION')
        return manager, manager.create_activation_handoff()

    def test_activation_handoff_is_private_durable_and_requires_real_receipt(self):
        old, handoff = self.pending_handoff()
        self.assertEqual(handoff['updateId'], old.state()['updateId'])
        self.assertNotIn(handoff['token'], old.journal.read_text())
        self.assertNotIn('tokenHash', json.dumps(old.state()))
        new = self.manager(current_version='0.2.0', activation_handoff=handoff['token'], clock=lambda: 1001)
        self.assertEqual(new.state()['updateState'], 'PENDING_ACTIVATION')
        self.assertFalse(new.gate_open)
        self.assertEqual(new.runtime_version, '0.2.0')
        self.assertEqual(new.state()['currentVersion'], '0.1.0')
        complete = new.activate({'panelVersion': '0.2.0', 'companionVersion': '0.2.0',
                                'bundleId': 'bundle-new', 'handshake': True, 'dataReadable': True})
        self.assertEqual(complete['updateState'], 'COMPLETE')

    def test_ordinary_restart_does_not_consume_or_activate_handoff(self):
        old, handoff = self.pending_handoff()
        restarted = self.manager(current_version='0.2.0', clock=lambda: 1001)
        self.assertEqual(restarted.state()['updateState'], 'RECOVERY_REQUIRED')
        self.assertFalse(restarted.gate_open)
        self.assertFalse(json.loads(old.journal.read_text())['_activationHandoff']['consumed'])

    def test_bad_expired_wrong_version_and_unverified_handoffs_stay_closed(self):
        initial_root = self.root
        for index, case in enumerate(['invalid', 'expired', 'version', 'unverified']):
            with self.subTest(case=case):
                self.root = initial_root / str(index)
                old, handoff = self.pending_handoff()
                if case == 'unverified':
                    self.installer.verify_application = lambda version, bundle: False
                new = self.manager(current_version='0.1.0' if case == 'version' else '0.2.0',
                                   activation_handoff='invalid' if case == 'invalid' else handoff['token'],
                                   clock=lambda: 1400 if case == 'expired' else 1001)
                self.assertEqual(new.state()['updateState'], 'RECOVERY_REQUIRED')
                self.assertFalse(new.gate_open)
                self.assertFalse(json.loads(old.journal.read_text())['_activationHandoff']['consumed'])

    def test_activation_handoff_cannot_be_replayed_or_reissued_by_stale_parent(self):
        old, handoff = self.pending_handoff()
        new = self.manager(current_version='0.2.0', activation_handoff=handoff['token'], clock=lambda: 1001)
        self.assertEqual(new.state()['updateState'], 'PENDING_ACTIVATION')
        replay = self.manager(current_version='0.2.0', activation_handoff=handoff['token'], clock=lambda: 1002)
        self.assertEqual(replay.state()['updateState'], 'RECOVERY_REQUIRED')
        with self.assertRaises(Exception):
            old.create_activation_handoff()
        with self.assertRaises(Exception):
            new.create_activation_handoff()
        self.assertTrue(json.loads(old.journal.read_text())['_activationHandoff']['consumed'])

    def test_handoff_consumption_failure_does_not_restore_pending(self):
        old, handoff = self.pending_handoff()
        new = self.manager(current_version='0.2.0', clock=lambda: 1001)
        new._persist = lambda value: (_ for _ in ()).throw(OSError('controlled disk failure'))
        with self.assertRaises(Exception):
            new.accept_activation_handoff(handoff['token'])
        self.assertEqual(new.state()['updateState'], 'RECOVERY_REQUIRED')
        self.assertFalse(new.gate_open)
        self.assertFalse(json.loads(old.journal.read_text())['_activationHandoff']['consumed'])

    def test_handoff_binding_corruption_fails_journal_validation(self):
        old, handoff = self.pending_handoff()
        value = json.loads(old.journal.read_text())
        value['_activationHandoff']['updateEpoch'] += 1
        old.journal.write_text(json.dumps(value))
        new = self.manager(current_version='0.2.0', activation_handoff=handoff['token'], clock=lambda: 1001)
        self.assertEqual(new.state()['updateState'], 'RECOVERY_REQUIRED')
        self.assertTrue(new._journal_bad)

    def test_pending_activation_missing_installer_result_is_not_eligible_for_handoff(self):
        old, handoff = self.pending_handoff()
        value = json.loads(old.journal.read_text())
        value.pop('installerResult')
        old.journal.write_text(json.dumps(value))
        new = self.manager(current_version='0.2.0', activation_handoff=handoff['token'], clock=lambda: 1001)
        self.assertEqual(new.state()['updateState'], 'RECOVERY_REQUIRED')
        self.assertTrue(new._journal_bad)


if __name__ == '__main__':
    unittest.main()
