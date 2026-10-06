"""Normal Setup core/worker without manual receipt input or live boundaries."""
from contextlib import nullcontext
import importlib
import json
from pathlib import Path
import queue
import threading
import unittest
from unittest.mock import patch
import test_installer
from test_install_preparation import PreparationFixture
from test_startup_integration import Registry
from contentrium_cut import bootstrap, integration
from contentrium_cut.contract import CutError


class CompletionTests(PreparationFixture):
    def setUp(self):
        super().setUp()
        self.fixture = test_installer.InstallerTests('runTest'); self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        if hasattr(self.fixture, 'preparation_patch'): self.fixture.preparation_patch.stop()
        self.entry = self.fixture.entry
        model=self.code/'_internal/silero'
        self.windows = self.fixture.actual_windows_payload(extra={
            '_internal/silero/'+path.name:path.read_bytes() for path in model.iterdir()})
        self.root = self.fixture.root
        self.registry = Registry(); self.launches = []
        self.arguments = dict(installation=self.windows.hooks, transport=self.fixture.fixture.network,
            launcher_source=self.fixture.setup, resource_root=self.fixture.setup.parent,
            dependency_installer=lambda root: None, mutex=lambda root: nullcontext())
        self.arguments['preparation'] = self.prepare_step
        self.arguments['integration_installer'] = self.integrate_step

    def prepare_step(self, root, config, **kwargs):
        from contentrium_cut.installer_preparation import prepare_installation
        return prepare_installation(root, config, owner_sid='S-1-5-21-123', roaming_root=self.roaming,
            protect=lambda *a:None, clock=lambda:self.now, **kwargs)

    def integrate_step(self, root, config, **kwargs):
        return integration.install_integration(root, config, registry=self.registry, menu_root=self.base/'menu',
            owner_sid='S-1-5-21-123', roaming_root=self.roaming, protect=lambda *a:None,
            popen=lambda args, **kw:self.launches.append((args,kw)), **kwargs)

    def install_core(self, **kwargs):
        return self.entry.install(self.fixture.config, self.root, **dict(self.arguments, **kwargs))

    def test_normal_core_pending_relaunch_receipt_completes_offline_without_ccx_reinstall(self):
        first = self.install_core()
        self.assertEqual(first['status'], 'PENDING_PROVISIONING')
        journal = self.root/'updates/first-install.json'
        self.assertEqual(json.loads(journal.read_text())['state'], 'PROVISIONING_PENDING')
        self.assertEqual(self.launches, [])
        installed_before = sum(args[1]=='/install' for args,_ in self.windows.adobe.calls)
        self.windows.processes.append({'pid':55,'imageName':'Adobe Premiere Pro.exe'})
        self.receipt()
        self.arguments['transport'] = type('Offline',(),{'get':lambda *a: (_ for _ in ()).throw(AssertionError('network'))})()
        result = self.install_core()
        self.assertEqual(result['status'], 'PENDING_ACTIVATION')
        self.assertEqual(json.loads(journal.read_text())['state'], 'READY_TO_OPEN')
        self.assertEqual((self.root/'models/silero/model.onnx').read_bytes(),b'exact signed fixture model')
        self.assertEqual(sum(args[1]=='/install' for args,_ in self.windows.adobe.calls), installed_before)
        self.assertEqual(self.launches[0][0], [str(self.root/integration.LAUNCHER_NAME),'--supervise','--root',str(self.root)])
        self.assertNotEqual(self.launches[0][1]['creationflags'], 0)

    def test_maintenance_defers_dispatch_and_migrates_only_verified_legacy_registration(self):
        self.install_core();self.receipt();self.install_core()
        secret=(self.root/'private/auth-bootstrap.json').read_bytes()
        legacy='"'+str(self.root/integration.LAUNCHER_NAME)+'" --open'
        key=r'Software\Classes\contentrium-cut\shell\open\command'
        self.registry.values[key,'']=legacy
        before=len(self.launches)
        config=json.loads((self.root/'launcher-config.json').read_text())
        prepared=self.prepare_step(self.root,config,installation=self.windows.hooks)
        result=self.integrate_step(self.root,config,launcher_source=self.fixture.setup,resource_root=self.fixture.setup.parent,
                                   mapping_evidence=prepared['mappingEvidence'],launch=False)
        self.assertEqual(len(self.launches),before)
        self.assertFalse(result['dispatchStarted'])
        self.assertEqual(self.registry.values[key,''],integration.protocol_command(self.root))
        record=json.loads((self.root/'updates/integration-registration.json').read_text())
        self.assertEqual(record['previousProtocolCommand'],legacy)
        self.assertEqual((self.root/'private/auth-bootstrap.json').read_bytes(),secret)
        self.registry.values[key,'']=legacy+' %1'
        with self.assertRaises(CutError):self.integrate_step(self.root,config,launcher_source=self.fixture.setup,
            mapping_evidence=prepared['mappingEvidence'],launch=False)
        self.assertEqual(self.registry.values[key,''],legacy+' %1')

    def test_owned_receipt_empty_or_truncated_write_gets_bounded_read_grace(self):
        self.install_core()
        path=self.data/'contentrium-install-receipt.json';path.write_text('')
        waits=[]
        def wait(seconds):
            waits.append(seconds)
            if len(waits)==1:path.write_text('{"schemaVersion":')
            else:self.receipt()
            return False
        result=self.install_core(wait=wait)
        self.assertEqual(result['status'],'PENDING_ACTIVATION')
        self.assertEqual(waits,[0.1,0.1])

    def test_persistently_truncated_receipt_fails_after_fixed_grace_without_overwrite(self):
        self.install_core()
        path=self.data/'contentrium-install-receipt.json';path.write_text('{"schemaVersion":')
        waits=[]
        with self.assertRaises(CutError) as error:self.install_core(wait=lambda seconds:waits.append(seconds) or False)
        self.assertEqual(error.exception.code,'INSTALL_RECEIPT_INVALID')
        self.assertEqual(waits,[0.1,0.1,0.1])
        self.assertEqual(path.read_text(),'{"schemaVersion":')
        self.assertFalse((self.root/'private/auth-bootstrap.json').exists())
        self.assertEqual(self.launches,[])

    def test_gui_worker_cancel_keeps_pending_then_another_worker_completes(self):
        events=queue.Queue(); cancel=threading.Event()
        def run(): self.entry.run_setup_worker(self.fixture.config,self.root,events,cancel,**self.arguments)
        worker=threading.Thread(target=run); worker.start()
        try:
            while True:
                kind,value=events.get(timeout=10)
                if kind=='progress' and '패널' in value and '열어' in value: break
                if kind=='error':self.fail(value)
            self.assertTrue(worker.is_alive());cancel.set();worker.join(5)
            self.assertFalse(worker.is_alive())
            self.assertEqual(json.loads((self.root/'updates/first-install.json').read_text())['state'],'PROVISIONING_PENDING')
            self.receipt();cancel.clear();run()
            outcomes=[]
            while not events.empty():outcomes.append(events.get())
            self.assertTrue(any(kind=='success' for kind,_ in outcomes))
        finally:cancel.set();worker.join(5)


class RootTests(PreparationFixture):
    def retained_launcher_installation(self):
        import base64
        from test_launcher_transaction import LauncherTests
        fixture=LauncherTests('runTest');fixture.setUp();self.addCleanup(fixture.doCleanups)
        manifest,assets,launcher,config=fixture.setup_signed()
        first=fixture.hooks.snapshot(manifest);fixture.hooks.install(manifest,assets,first)
        retained={path:path.read_bytes() for path in [launcher,fixture.root/'launcher-config.json',fixture.root/'launcher-ownership.json']}
        optional,prepared=fixture.payload('0.3.0','third')
        optional['signingKeyId']='contentrium-cut-2026-01'
        raw=json.dumps(optional).encode()
        signature=json.dumps(dict(algorithm='Ed25519',keyId=optional['signingKeyId'],
                                 signature=base64.b64encode(fixture.key.sign(raw)).decode())).encode()
        (fixture.root/'updates/journal.json').write_text(json.dumps({'_attemptCandidate':dict(
            raw=base64.b64encode(raw).decode(),signature=base64.b64encode(signature).decode())}))
        second=fixture.hooks.snapshot(optional);fixture.hooks.install(optional,prepared,second)
        self.assertEqual({path:path.read_bytes() for path in retained},retained)
        registry=Registry()
        registry.values[r'Software\Classes\contentrium-cut\shell\open\command','']=integration.protocol_command(fixture.root)
        registry.values[r'Software\Microsoft\Windows\CurrentVersion\Run','Contentrium CUT']=integration.startup_command(fixture.root)
        return fixture,config,registry

    def test_registered_root_uses_new_active_target_with_retained_older_signed_launcher(self):
        fixture,config,registry=self.retained_launcher_installation()
        self.assertEqual(json.loads((fixture.root/'launcher-config.json').read_text())['appVersion'],'0.2.0')
        self.assertTrue(fixture.hooks.verify_code('0.3.0','third'))
        before={path:path.read_bytes() for path in fixture.root.rglob('*') if path.is_file()}
        registrations=dict(registry.values)
        with patch.object(fixture.hooks,'verify_code',wraps=fixture.hooks.verify_code) as verify:
            result=integration.resolve_setup_root(config,registry=registry,known_local=self.base/'wrong-local',
                owner_sid='S-1-5-21-123',installation_factory=lambda root:fixture.hooks)
        self.assertEqual(result,fixture.root)
        verify.assert_called_once_with('0.3.0','third')
        self.assertEqual({path:path.read_bytes() for path in before},before)
        self.assertEqual(registry.values,registrations)
        self.assertFalse((self.base/'wrong-local').exists())

    def test_retained_launcher_does_not_authorize_missing_forged_or_corrupt_active_target(self):
        fixture,config,registry=self.retained_launcher_installation()
        active=fixture.hooks.active;original=active.read_bytes();descriptor=json.loads(original)
        code=fixture.root/'app/versions/0.3.0'/'Contentrium CUT.exe';runtime=code.read_bytes()
        launcher=fixture.root/integration.LAUNCHER_NAME;signed_launcher=launcher.read_bytes()
        for case in ['missing','forged-pointer','mismatched-bundle','corrupt-code','altered-launcher']:
            with self.subTest(case=case):
                active.write_bytes(original);code.write_bytes(runtime);launcher.write_bytes(signed_launcher)
                if case=='missing':active.unlink()
                elif case=='forged-pointer':active.write_text(json.dumps(dict(descriptor,versionDirectory='../../foreign')))
                elif case=='mismatched-bundle':active.write_text(json.dumps(dict(descriptor,bundleId='unverified')))
                elif case=='corrupt-code':code.write_bytes(b'unknown runtime')
                else:launcher.write_bytes(b'unknown launcher')
                before={path:path.read_bytes() for path in fixture.root.rglob('*') if path.is_file()}
                with self.assertRaises(CutError) as error:
                    integration.resolve_setup_root(config,registry=registry,known_local=self.base/'wrong-local',
                        owner_sid='S-1-5-21-123',installation_factory=lambda root:fixture.hooks)
                self.assertEqual(error.exception.code,'INSTALL_REGISTRATION')
                self.assertEqual({path:path.read_bytes() for path in fixture.root.rglob('*') if path.is_file()},before)
                self.assertFalse((self.base/'wrong-local').exists())

    def test_fresh_fallback_explicit_pin_and_conflicting_registered_roots(self):
        registry=Registry()
        self.assertEqual(integration.resolve_setup_root({},registry=registry,known_local=self.base/'chosen'),self.base/'chosen'/'Contentrium CUT')
        self.assertEqual(integration.resolve_setup_root({},explicit=self.root),self.root)
        registry.values[r'Software\Classes\contentrium-cut\shell\open\command','']=integration.protocol_command(self.root)
        registry.values[r'Software\Microsoft\Windows\CurrentVersion\Run','Contentrium CUT']=integration.startup_command(self.base/'other')
        with self.assertRaises(CutError) as error:integration.resolve_setup_root({},registry=registry,known_local=self.base/'chosen')
        self.assertEqual(error.exception.code,'INSTALL_REGISTRATION')
        self.assertFalse((self.base/'chosen').exists())

    def test_registered_physical_root_beats_changed_known_folder_and_legacy_migrates_only_owned_command(self):
        fixture=test_installer.InstallerTests('runTest');fixture.setUp();self.addCleanup(fixture.doCleanups)
        windows=fixture.actual_windows_payload()
        from contextlib import nullcontext
        fixture.entry.install(fixture.config,windows.root,installation=windows.hooks,transport=fixture.fixture.network,
            launcher_source=fixture.setup,dependency_installer=lambda *a:None,mutex=lambda *a:nullcontext(),
            preparation=lambda *a,**k:dict(status='PREPARED',mappingEvidence={}),
            integration_installer=lambda root,config,**kw: self.seed_launcher(root,config,fixture.setup))
        registry=Registry();legacy='"'+str(windows.root/integration.LAUNCHER_NAME)+'" --open'
        registry.values[r'Software\Classes\contentrium-cut\shell\open\command','']=legacy
        result=integration.resolve_setup_root(fixture.config,registry=registry,known_local=self.base/'wrong-local',
            owner_sid='S-1-5-21-123',installation_factory=lambda root:windows.hooks)
        self.assertEqual(result,windows.root)
        self.assertFalse((self.base/'wrong-local').exists())
        for invalid in [legacy+' %1',legacy+' --root "outside"',legacy.replace(' --open',' --other')]:
            registry.values[r'Software\Classes\contentrium-cut\shell\open\command','']=invalid
            with self.assertRaises(CutError):integration.resolve_setup_root(fixture.config,registry=registry,
                known_local=self.base/'wrong-local',owner_sid='S-1-5-21-123',installation_factory=lambda root:windows.hooks)
        registry.values[r'Software\Classes\contentrium-cut\shell\open\command','']=legacy
        with self.assertRaises(CutError):integration.resolve_setup_root(dict(fixture.config,publicKey='untrusted'),registry=registry,
            known_local=self.base/'wrong-local',owner_sid='S-1-5-21-123',installation_factory=lambda root:windows.hooks)

    def seed_launcher(self,root,config,source):
        import shutil
        shutil.copyfile(source,root/integration.LAUNCHER_NAME)
        (root/'launcher-config.json').write_text(json.dumps(config))
        return dict(launcher=str(root/integration.LAUNCHER_NAME),shortcut='fixture')

if __name__=='__main__':unittest.main()
