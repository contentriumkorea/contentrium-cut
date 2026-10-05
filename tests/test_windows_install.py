"""Windows installation boundary tests: real files, controlled UPIA commands/host registry."""
import hashlib
from contextlib import closing
import importlib
import io
import json
import os
from pathlib import Path
import subprocess
import sqlite3
import tempfile
import unittest
import uuid
import zipfile

REAL_UPIA_PREMIERE_LIST = '''2 extensions installed for Premiere Pro (ver 26.5.2)
 Status                        Extension Name                         Version
=========  =======================================================  ==========
 Enabled    MotionBro3                                                   4.5.0
 Enabled    Premium Builder Youtube Pack                                   1.0

0 extension installed for\x20
 Status                        Extension Name                         Version
=========  =======================================================  ==========

0 extension installed for Others
 Status                        Extension Name                         Version
=========  =======================================================  ==========

0 extension installed for Illustrator 2026 (ver 30.8.2)
 Status                        Extension Name                         Version
=========  =======================================================  ==========

'''


def archive(entries):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as package:
        for name, value in entries.items():
            package.writestr(name, value)
    return output.getvalue()


class AdobeBoundary:
    def __init__(self):
        self.registered = []
        self.calls = []
        self.install_code = 0
        self.no_register = False
        self.timeout = False

    def run(self, args, timeout):
        from contentrium_cut.windows_install import CommandResult
        self.calls.append((tuple(args), timeout))
        if self.timeout:
            raise subprocess.TimeoutExpired(args, timeout)
        if args[1] == '/list':
            return CommandResult(0, json.dumps({'plugins': self.registered}), '')
        if args[1] == '/remove':
            self.registered = []
            return CommandResult(0, '', '')
        if args[1] == '/install':
            if self.install_code:
                return CommandResult(self.install_code, '', 'controlled failure')
            if not self.no_register:
                with zipfile.ZipFile(args[2]) as package:
                    manifest = json.loads(package.read('manifest.json'))
                self.registered = [{'id': manifest['id'], 'version': manifest['version']}]
            return CommandResult(0, 'command accepted', '')
        raise AssertionError('Unexpected installer operation')


class WindowsInstallationTests(unittest.TestCase):
    def setUp(self):
        try:
            self.w = importlib.import_module('contentrium_cut.windows_install')
        except ModuleNotFoundError:
            self.fail('Windows installation hooks are absent')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'data'
        self.inputs = Path(self.tmp.name) / 'payloads'
        self.inputs.mkdir()
        self.upia = Path(self.tmp.name) / 'UnifiedPluginInstallerAgent.exe'
        self.upia.write_bytes(b'command boundary')
        self.adobe = AdobeBoundary()
        self.processes = []
        self.hooks = self.w.WindowsInstallation(self.root, upia_path=self.upia, runner=self.adobe,
                     process_probe=lambda: self.processes, activation_probe=lambda version, bundle, receipt: True)

    def payload(self, version='0.1.0', bundle='old', extra=None):
        config = {'schemaVersion': 1, 'productId': 'com.contentrium.cut', 'appVersion': version,
                  'panelVersion': version, 'companionVersion': version, 'bundleId': bundle, 'dataSchemaVersion': 1}
        files = {'config.json': json.dumps(config), 'Contentrium CUT.exe': b'actual file fixture ' + version.encode(),
                 '_internal/library.dll': b'runtime fixture'}
        files.update(extra or {})
        panel = archive({'manifest.json': json.dumps({'id': 'com.contentrium.cut', 'name': 'Contentrium CUT',
                              'version': version, 'host': {'app': 'premierepro', 'minVersion': '26.3.0'}}),
                         'bundle.json': json.dumps(config), 'index.html': '<div>CUT</div>'})
        values = {'companion': archive(files), 'panel': panel}
        assets, manifest_assets = {}, []
        for index, (role, value) in enumerate(values.items(), 1):
            name = f'{version}-{role}' + ('.ccx' if role == 'panel' else '.zip')
            path = self.inputs / name
            path.write_bytes(value)
            assets[role] = path
            manifest_assets.append({'role': role, 'assetId': index, 'name': name,
                                     'size': len(value), 'sha256': hashlib.sha256(value).hexdigest()})
        manifest = {'productId': 'com.contentrium.cut', 'appVersion': version, 'bundleId': bundle,
                    'panelVersion': version, 'companionVersion': version, 'dataSchemaFrom': [1],
                    'dataSchemaTo': 1, 'migrationId': 'none', 'assets': manifest_assets}
        return manifest, assets

    def old_install(self):
        manifest, assets = self.payload()
        self.hooks.bootstrap(manifest, assets)
        (self.root / 'settings').mkdir()
        (self.root / 'settings' / 'user.json').write_text('{"language":"ko"}')
        (self.root / 'jobs').mkdir()
        (self.root / 'jobs' / 'job.json').write_text('{"schemaVersion":1}')
        (self.root / 'models').mkdir()
        (self.root / 'models' / 'large.bin').write_bytes(b'keep model')
        return manifest

    def test_bootstrap_stages_real_version_directory_and_pending_activation(self):
        manifest, assets = self.payload()
        result = self.hooks.bootstrap(manifest, assets)
        self.assertEqual(result['status'], 'PENDING_ACTIVATION')
        active = json.loads((self.root / 'app' / 'active.json').read_text())
        self.assertEqual(active['appVersion'], '0.1.0')
        self.assertTrue((self.root / 'app' / 'versions' / '0.1.0' / 'Contentrium CUT.exe').is_file())
        self.assertTrue(self.hooks.verify_previous({'previousVersion': '0.1.0'}))
        self.assertTrue(any(args[1:] == ('/list', 'Premiere Pro') for args, _ in self.adobe.calls))

    def test_exit_zero_without_adobe_registration_is_not_installed(self):
        self.adobe.no_register = True
        manifest, assets = self.payload()
        with self.assertRaises(Exception):
            self.hooks.bootstrap(manifest, assets)
        self.assertFalse((self.root / 'app' / 'active.json').exists())

    def test_host_open_blocks_install_and_snapshot_without_termination(self):
        manifest, assets = self.payload()
        self.processes.append({'pid': 55, 'imageName': 'Adobe Premiere Pro.exe', 'imagePath': 'C:/Adobe/Premiere.exe'})
        self.assertFalse(self.hooks.host_exited())
        with self.assertRaises(Exception):
            self.hooks.bootstrap(manifest, assets)
        self.assertFalse(self.adobe.calls)

    def test_snapshot_and_rollback_preserve_model_and_restore_verified_pair(self):
        self.old_install()
        manifest, assets = self.payload('0.2.0', 'new')
        snapshot = self.hooks.snapshot(manifest)
        self.assertTrue(snapshot['previousVerified'])
        self.assertFalse(list(Path(snapshot['dataSnapshot']).rglob('large.bin')))
        self.hooks.install(manifest, assets, snapshot)
        (self.root / 'settings' / 'user.json').write_text('{"language":"changed"}')
        self.hooks.rollback(snapshot)
        self.assertTrue(self.hooks.verify_previous(snapshot))
        self.assertEqual((self.root / 'models' / 'large.bin').read_bytes(), b'keep model')
        self.assertEqual((self.root / 'settings' / 'user.json').read_text(), '{"language":"ko"}')
        self.assertTrue(any(args[1:] == ('/remove', 'com.contentrium.cut') for args, _ in self.adobe.calls))
        self.assertTrue((self.root / 'app' / 'versions' / '0.2.0' / 'Contentrium CUT.exe').exists())

    def test_snapshot_requires_actual_adobe_previous_version_and_file_hashes(self):
        self.old_install()
        self.adobe.registered = [{'id': 'com.contentrium.cut', 'version': '9.0.0'}]
        manifest, _ = self.payload('0.2.0', 'new')
        with self.assertRaises(Exception):
            self.hooks.snapshot(manifest)
        self.adobe.registered = [{'id': 'com.contentrium.cut', 'version': '0.1.0'}]
        (self.root / 'app' / 'versions' / '0.1.0' / 'Contentrium CUT.exe').write_bytes(b'tampered')
        self.assertFalse(self.hooks.verify_previous({'previousVersion': '0.1.0'}))

    def test_corrupt_snapshot_prevents_rollback_commands(self):
        self.old_install()
        manifest, assets = self.payload('0.2.0', 'new')
        snapshot = self.hooks.snapshot(manifest)
        self.hooks.install(manifest, assets, snapshot)
        (Path(snapshot['dataSnapshot']) / 'settings' / 'user.json').write_text('tampered backup')
        count = len(self.adobe.calls)
        with self.assertRaises(Exception):
            self.hooks.rollback(snapshot)
        self.assertEqual(len(self.adobe.calls), count)

    def test_zip_escape_and_symlink_block_before_upia(self):
        manifest, assets = self.payload(extra={'../escape.exe': b'escape'})
        with self.assertRaises(Exception):
            self.hooks.bootstrap(manifest, assets)
        self.assertFalse(any(args[1] == '/install' for args, _ in self.adobe.calls))
        self.assertFalse((self.root / 'app' / 'escape.exe').exists())

    def test_signed_hash_and_bundle_mismatch_block_before_upia(self):
        manifest, assets = self.payload()
        manifest['assets'][0]['sha256'] = '0' * 64
        with self.assertRaises(Exception):
            self.hooks.bootstrap(manifest, assets)
        manifest, assets = self.payload()
        manifest['bundleId'] = 'wrong'
        with self.assertRaises(Exception):
            self.hooks.bootstrap(manifest, assets)
        self.assertFalse(any(args[1] == '/install' for args, _ in self.adobe.calls))

    def test_runner_timeout_does_not_activate_or_kill_premiere(self):
        manifest, assets = self.payload()
        self.adobe.timeout = True
        with self.assertRaises(self.w.CutError) as raised:
            self.hooks.bootstrap(manifest, assets)
        self.assertEqual(raised.exception.code, 'UPIA_TIMEOUT')
        self.assertFalse((self.root / 'app' / 'active.json').exists())

    def test_verification_requires_actual_activation_probe_and_matching_receipt(self):
        manifest, assets = self.payload()
        self.hooks.bootstrap(manifest, assets)
        receipt = {'panelVersion': '0.1.0', 'companionVersion': '0.1.0', 'bundleId': 'old',
                   'handshake': True, 'dataReadable': True}
        self.assertTrue(self.hooks.verify('0.1.0', 'old', receipt))
        self.hooks.activation_probe = None
        self.assertFalse(self.hooks.verify('0.1.0', 'old', receipt))
        self.hooks.activation_probe = lambda *args: True
        receipt['panelVersion'] = '0.9.0'
        self.assertFalse(self.hooks.verify('0.1.0', 'old', receipt))

    def test_unreadable_or_duplicate_upia_output_cannot_prove_registration(self):
        with self.assertRaises(Exception):
            self.w.parse_upia_list('opaque success text')
        with self.assertRaises(Exception):
            self.w.parse_upia_list(json.dumps({'plugins': [{'id': 'com.contentrium.cut', 'version': '0.1.0'},
                                                         {'id': 'com.contentrium.cut', 'version': '0.2.0'}]}))
        with self.assertRaises(self.w.CutError):
            self.w.parse_upia_list('')
        self.assertEqual(self.w.parse_upia_list('Plugin ID: com.contentrium.cut\nVersion: 0.1.0\n'),
                         [{'id': 'com.contentrium.cut', 'version': '0.1.0', 'status': 'Enabled'}])

    def test_unsupported_migration_is_incompatible_without_commands(self):
        manifest, _ = self.payload()
        manifest.update(dataSchemaTo=2, migrationId='arbitrary')
        self.assertIn('DATA_MIGRATION', self.hooks.compatibility(manifest))
        self.assertFalse(self.adobe.calls)

    def test_process_pid_reuse_is_verified_exit_only_with_complete_identity(self):
        identity = {'pid': 123, 'imagePath': 'C:/owned/worker.exe', 'createdAtTicks': '100'}
        self.assertTrue(self.w.process_has_exited(identity, probe=lambda pid: dict(identity, createdAtTicks='101')))
        self.assertFalse(self.w.process_has_exited({'pid': 123}, probe=lambda pid: None))
        def denied(pid):
            raise PermissionError('access denied')
        self.assertFalse(self.w.process_has_exited(identity, probe=denied))

    def test_snapshot_excludes_media_even_inside_job_directory(self):
        self.old_install()
        (self.root / 'jobs' / 'source.wav').write_bytes(b'user original media')
        manifest, _ = self.payload('0.2.0', 'new')
        snapshot = self.hooks.snapshot(manifest)
        self.assertFalse((Path(snapshot['dataSnapshot']) / 'jobs' / 'source.wav').exists())
        self.assertEqual((self.root / 'jobs' / 'source.wav').read_bytes(), b'user original media')

    def test_json_empty_registration_list_is_known_absence(self):
        self.assertEqual(self.w.parse_upia_list('{"plugins":[]}'), [])

    def test_host_process_probe_failure_blocks_install(self):
        def inaccessible():
            raise PermissionError('snapshot denied')
        self.hooks.process_probe = inaccessible
        self.assertFalse(self.hooks.host_exited())

    @unittest.skipUnless(os.name == 'nt', 'Windows API wrapper requires Windows')
    def test_native_process_identity_is_not_confused_with_pid_reuse(self):
        identity = self.w.process_identity(os.getpid())
        self.assertEqual(identity['pid'], os.getpid())
        self.assertTrue(Path(identity['imagePath']).is_absolute())
        self.assertTrue(identity['createdAtTicks'].isdigit())
        self.assertFalse(self.w.process_has_exited(identity))
        self.assertTrue(any(row['pid'] == os.getpid() for row in self.w.windows_process_snapshot()))

    @unittest.skipUnless(os.name == 'nt', 'Windows named mutex requires Windows')
    def test_native_named_mutex_excludes_second_updater_then_releases(self):
        name = 'Local\\ContentriumCUT-test-' + uuid.uuid4().hex
        with self.w.WindowsNamedMutex(name=name):
            with self.assertRaises(self.w.CutError):
                with self.w.WindowsNamedMutex(name=name):
                    self.fail('Second updater acquired occupied installation')
        with self.w.WindowsNamedMutex(name=name):
            pass

    def test_bootstrap_cannot_replace_untracked_existing_adobe_plugin(self):
        self.adobe.registered = [{'id': 'com.contentrium.cut', 'version': '9.0.0'}]
        manifest, assets = self.payload()
        with self.assertRaises(Exception):
            self.hooks.bootstrap(manifest, assets)
        self.assertFalse(any(args[1] == '/install' for args, _ in self.adobe.calls))

    def test_bootstrap_resumes_owned_registered_bundle_after_host_reopens(self):
        manifest, assets = self.payload()
        original = self.adobe.run
        def during_install(args, timeout):
            result = original(args, timeout)
            if args[1] == '/install':
                self.processes.append({'pid':55, 'imageName':'Adobe Premiere Pro.exe'})
            return result
        self.adobe.run = during_install
        with self.assertRaises(self.w.CutError): self.hooks.bootstrap(manifest, assets)
        self.assertFalse(self.hooks.active.exists())
        self.assertTrue(self.hooks.bootstrap_journal.exists())
        self.processes.clear()
        self.adobe.run = original
        restored = self.w.WindowsInstallation(self.root, upia_path=self.upia, runner=self.adobe, process_probe=lambda: [])
        self.assertEqual(restored.bootstrap(manifest, assets)['status'], 'PENDING_ACTIVATION')
        self.assertEqual(sum(args[1] == '/install' for args,_ in self.adobe.calls), 1)
        self.assertFalse(any(args[1] == '/remove' for args,_ in self.adobe.calls))
        self.assertTrue(restored.verify_application('0.1.0','old'))

    def test_bootstrap_resumes_after_destination_promoted_before_active_write(self):
        from unittest.mock import patch
        manifest, assets = self.payload()
        atomic = self.w._atomic_json
        def fail_pointer(path, value):
            if path == self.hooks.active: raise OSError('controlled active pointer failure')
            return atomic(path, value)
        with patch.object(self.w,'_atomic_json',fail_pointer), self.assertRaises(OSError):
            self.hooks.bootstrap(manifest, assets)
        self.assertTrue((self.hooks.versions / '0.1.0').exists())
        self.assertFalse(self.hooks.active.exists())
        restored = self.w.WindowsInstallation(self.root, upia_path=self.upia, runner=self.adobe, process_probe=lambda: [])
        self.assertEqual(restored.bootstrap(manifest, assets)['status'], 'PENDING_ACTIVATION')
        self.assertEqual(sum(args[1] == '/install' for args,_ in self.adobe.calls), 1)

    def test_bootstrap_retry_refuses_other_bundle_or_modified_owned_stage(self):
        manifest, assets = self.payload()
        original = self.adobe.run
        def during_install(args, timeout):
            result = original(args, timeout)
            if args[1] == '/install': self.processes.append({'pid':55,'imageName':'Adobe Premiere Pro.exe'})
            return result
        self.adobe.run = during_install
        with self.assertRaises(self.w.CutError): self.hooks.bootstrap(manifest, assets)
        self.processes.clear(); self.adobe.run = original
        other, other_assets = self.payload('0.2.0','different')
        with self.assertRaises(self.w.CutError): self.hooks.bootstrap(other, other_assets)
        stage = next(self.hooks.versions.glob('.stage-*'))
        (stage / 'Contentrium CUT.exe').write_bytes(b'changed')
        with self.assertRaises(self.w.CutError): self.hooks.bootstrap(manifest, assets)
        self.assertFalse(self.hooks.active.exists())
        self.assertEqual(sum(args[1] == '/install' for args,_ in self.adobe.calls), 1)

    def test_bootstrap_failed_intent_write_has_no_adobe_side_effect(self):
        from unittest.mock import patch
        manifest, assets = self.payload()
        atomic = self.w._atomic_json
        def fail_intent(path, value):
            if path == self.hooks.bootstrap_journal: raise OSError('controlled bootstrap intent failure')
            return atomic(path,value)
        with patch.object(self.w,'_atomic_json',fail_intent), self.assertRaises(OSError):
            self.hooks.bootstrap(manifest, assets)
        self.assertFalse(any(args[1] == '/install' for args,_ in self.adobe.calls))

    def test_snapshot_rejects_unknown_job_schema_before_replace(self):
        self.old_install()
        (self.root / 'jobs' / 'job.json').write_text('{"schemaVersion":99}')
        manifest, _ = self.payload('0.2.0', 'new')
        with self.assertRaises(Exception):
            self.hooks.snapshot(manifest)

    def test_pending_result_accepts_updater_asset_descriptors(self):
        manifest, assets = self.payload()
        described = {entry['role']: {'path': str(assets[entry['role']]), 'sha256': entry['sha256'],
                                    'assetId': entry['assetId']} for entry in manifest['assets']}
        self.assertEqual(self.hooks.bootstrap(manifest, described)['status'], 'PENDING_ACTIVATION')

    def test_linked_app_parent_never_receives_staged_files(self):
        outside = Path(self.tmp.name) / 'outside'
        outside.mkdir()
        self.root.mkdir()
        try:
            (self.root / 'app').symlink_to(outside, target_is_directory=True)
        except OSError as error:
            self.skipTest('OS does not allow test symlink: ' + str(error.winerror))
        manifest, assets = self.payload()
        with self.assertRaises(Exception):
            self.hooks.bootstrap(manifest, assets)
        self.assertFalse(list(outside.iterdir()))

    def test_application_verification_checks_files_and_adobe_without_claiming_host_load(self):
        self.old_install()
        self.hooks.activation_probe = None
        self.assertTrue(self.hooks.verify_application('0.1.0', 'old'))
        self.assertFalse(self.hooks.verify_application('0.2.0', 'old'))
        self.assertFalse(self.hooks.verify_application('0.1.0', 'different'))
        receipt = {'panelVersion': '0.1.0', 'companionVersion': '0.1.0', 'bundleId': 'old',
                   'handshake': True, 'dataReadable': True}
        self.assertFalse(self.hooks.verify('0.1.0', 'old', receipt))
        self.adobe.registered = []
        self.assertFalse(self.hooks.verify_application('0.1.0', 'old'))
        self.adobe.registered = [{'id': 'com.contentrium.cut', 'version': '0.1.0'}]
        (self.root / 'app' / 'versions' / '0.1.0' / 'Contentrium CUT.exe').write_bytes(b'changed')
        self.assertFalse(self.hooks.verify_application('0.1.0', 'old'))

    def upia_identity_database(self, records):
        database = Path(self.tmp.name) / ('UPI-' + uuid.uuid4().hex + '.db')
        with closing(sqlite3.connect(database)) as db:
            db.executescript('CREATE TABLE Tb_ExtBasicInfo(ExtID INTEGER, ExtIDInFile TEXT, ExtName TEXT, ExtVersion TEXT);'
                             'CREATE TABLE Tb_ExtProductMap(ExtID INTEGER, ProdID INTEGER, State TEXT);'
                             'CREATE TABLE Tb_ProductInfo(ProdID INTEGER, DisplayName TEXT, ProdVersion TEXT);')
            db.execute('INSERT INTO Tb_ProductInfo VALUES(1,?,?)', ('Premiere Pro', '26.5.2'))
            for index, (plugin_id, name, version, status) in enumerate(records, 1):
                db.execute('INSERT INTO Tb_ExtBasicInfo VALUES(?,?,?,?)', (index, plugin_id, name, version))
                db.execute('INSERT INTO Tb_ExtProductMap VALUES(?,1,?)', (index, status))
            db.commit()
        return database

    def table_hooks(self, output, records):
        database = self.upia_identity_database(records)
        class CapturedUPIA:
            def run(inner, args, timeout):
                self.assertEqual(args[1:], ['/list', 'Premiere Pro'])
                return self.w.CommandResult(0, output, '')
        return self.w.WindowsInstallation(self.root, upia_path=self.upia, runner=CapturedUPIA(),
                                           registration_databases=[database], process_probe=lambda: [])

    def test_real_upia_table_joins_actual_database_identity_and_two_part_versions(self):
        hooks = self.table_hooks(REAL_UPIA_PREMIERE_LIST,
                                [('MotionBro3', 'MotionBro3', '4.5.0', 'Enabled'),
                                 ('com.premiumilk.PremiumBuilderYoutubePack', 'Premium Builder Youtube Pack', '1.0', 'Enabled')])
        rows = hooks.registrations()
        self.assertEqual([row['id'] for row in rows], ['MotionBro3', 'com.premiumilk.PremiumBuilderYoutubePack'])
        self.assertFalse(hooks._registered('0.1.0'))

    def test_display_name_without_database_id_is_not_registration_proof(self):
        output = REAL_UPIA_PREMIERE_LIST.replace('2 extensions', '1 extension').replace(
            ' Enabled    MotionBro3                                                   4.5.0\n', '').replace(
            'Premium Builder Youtube Pack                                   1.0', 'Contentrium CUT                                                0.1.0')
        wrong = self.table_hooks(output, [('com.someone.fake', 'Contentrium CUT', '0.1.0', 'Enabled')])
        self.assertFalse(wrong._registered('0.1.0'))
        actual = self.table_hooks(output, [('com.contentrium.cut', 'Contentrium CUT', '0.1.0', 'Enabled')])
        self.assertTrue(actual._registered('0.1.0'))
        missing = self.table_hooks(output, [])
        with self.assertRaises(self.w.CutError):
            missing.registrations()

    def test_table_count_status_and_ambiguous_database_records_fail_closed(self):
        for output in [REAL_UPIA_PREMIERE_LIST.replace('2 extensions', '3 extensions'),
                       REAL_UPIA_PREMIERE_LIST.replace(' Enabled    MotionBro3', ' Unknown    MotionBro3')]:
            with self.subTest(output=output), self.assertRaises(self.w.CutError):
                self.w.parse_upia_list(output)
        hooks = self.table_hooks(REAL_UPIA_PREMIERE_LIST, [('MotionBro3', 'MotionBro3', '4.5.0', 'Enabled'),
                              ('different.identity', 'MotionBro3', '4.5.0', 'Enabled'),
                              ('youtube', 'Premium Builder Youtube Pack', '1.0', 'Enabled')])
        with self.assertRaises(self.w.CutError):
            hooks.registrations()

    def test_empty_success_stdout_is_unknown_not_proven_absence(self):
        with self.assertRaises(self.w.CutError):
            self.w.parse_upia_list('')

    def test_disabled_cut_cannot_verify_and_unlisted_database_cut_is_disagreement(self):
        output = REAL_UPIA_PREMIERE_LIST.replace('2 extensions', '1 extension').replace(
            ' Enabled    MotionBro3                                                   4.5.0\n', '').replace(
            ' Enabled    Premium Builder Youtube Pack                                   1.0',
            ' Disabled    Contentrium CUT                                                0.1.0')
        disabled = self.table_hooks(output, [('com.contentrium.cut', 'Contentrium CUT', '0.1.0', 'Disabled')])
        self.assertFalse(disabled._registered('0.1.0'))
        discrepancy = self.table_hooks(REAL_UPIA_PREMIERE_LIST,
                        [('MotionBro3', 'MotionBro3', '4.5.0', 'Enabled'),
                         ('youtube', 'Premium Builder Youtube Pack', '1.0', 'Enabled'),
                         ('com.contentrium.cut', 'Contentrium CUT', '0.1.0', 'Enabled')])
        with self.assertRaises(self.w.CutError):
            discrepancy.registrations()

    def test_identity_database_reads_are_read_only_and_missing_path_is_not_created(self):
        database = self.upia_identity_database([('com.contentrium.cut', 'Contentrium CUT', '0.1.0', 'Enabled')])
        before = database.read_bytes()
        rows = self.w.upia_registration_records([database])
        self.assertEqual(rows[0]['id'], 'com.contentrium.cut')
        self.assertEqual(database.read_bytes(), before)
        missing = Path(self.tmp.name) / 'missing.db'
        self.assertEqual(self.w.upia_registration_records([missing]), [])
        self.assertFalse(missing.exists())


if __name__ == '__main__':
    unittest.main()
