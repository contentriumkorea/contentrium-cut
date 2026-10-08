"""Source-assisted migration with real signed files and isolated native boundaries."""
import base64
import hashlib
import importlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from contentrium_cut import bootstrap, lifecycle, updater, release_assets
from contentrium_cut.contract import CutError
from test_install_preparation import PreparationFixture
from test_startup_integration import Registry
import test_updater
import test_windows_install


COMMIT = '1234567890abcdef1234567890abcdef12345678'


class MaintenanceTests(PreparationFixture):
    def setUp(self):
        super().setUp()
        self.windows = test_windows_install.WindowsInstallationTests('runTest'); self.windows.setUp()
        self.addCleanup(self.windows.doCleanups)
        self.windows.hooks.activation_probe=None
        self.fixture = test_updater.UpdaterTests('runTest'); self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        model = self.code/'_internal/silero'
        self.model_files = {'_internal/silero/'+p.name:p.read_bytes() for p in model.iterdir()}
        self.root = self.windows.root
        self.config = dict(productId='com.contentrium.cut', appVersion='0.1.0', panelVersion='0.1.0',
            companionVersion='0.1.0', bundleId='legacy-bundle', dataSchemaVersion=1, protocolVersion=1,
            publicKey=base64.b64encode(self.fixture.public).decode(), signingKeyId=updater.KEY_ID)
        old, paths, network = self.payload('0.1.0', 'legacy-bundle')
        self.windows.hooks.bootstrap(old, paths)
        (self.root/'Contentrium CUT Launcher.exe').write_bytes(paths['installer'].read_bytes())
        (self.root/'launcher-config.json').write_text(json.dumps(self.config))
        prefix=network.release['assets'][0]['browser_download_url'].rsplit('/',1)[0]+'/'
        first=dict(schemaVersion=1,productId='com.contentrium.cut',state='READY_TO_OPEN',
            appVersion='0.1.0',bundleId='legacy-bundle',release=network.release,
            manifest=base64.b64encode(network.documents[prefix+'update-manifest.json']).decode(),
            signature=base64.b64encode(network.documents[prefix+'update-manifest.sig']).decode())
        (self.root/'updates/first-install.json').write_text(json.dumps(first))
        self.original_first=(self.root/'updates/first-install.json').read_bytes()
        for name,value in [('settings/user.json',b'{"language":"ko"}'),('jobs/job.json',b'{"saved":true}'),
                           ('models/user-model.bin',b'preserved user model')]:
            path=self.root/name;path.parent.mkdir(exist_ok=True);path.write_bytes(value)
        self.user_files={name:(self.root/name).read_bytes() for name in ['settings/user.json','jobs/job.json','models/user-model.bin']}
        self.target, _, self.network = self.payload('0.1.1', 'target-bundle')
        self.registry=Registry(); self.registry.values[r'Software\Classes\contentrium-cut\shell\open\command','']='"'+str(self.root/'Contentrium CUT Launcher.exe')+'" --open'
        self.registry.DeleteValue=lambda key,name:self.registry.values.pop((key,name))
        self.windows.hooks.migration_registry=self.registry
        self.events=[];self.owned=set();self.launches=[];self.pipes=[]
        self.options=dict(tag='v0.1.1',version='0.1.1',commit=COMMIT,installation=self.windows.hooks,
            transport=self.network,owner_sid='S-1-5-21-123',roaming_root=self.roaming,protect=lambda *a:None,
            registry=self.registry,menu_root=self.base/'menu',lease_factory=self.leases,
            popen=self.spawn,clock=lambda:self.now)
        native=patch('contentrium_cut.windows_install.process_identity',return_value=dict(pid=123,createdAtTicks='5',imagePath='fixture-python.exe'))
        native.start();self.addCleanup(native.stop)

    def payload(self, version, bundle):
        config=dict(self.config,appVersion=version,panelVersion=version,companionVersion=version,bundleId=bundle)
        manifest,paths=self.windows.payload(version,bundle,extra=dict(self.model_files,**{'config.json':json.dumps(config)}))
        installer=self.windows.inputs/(version+'-setup.exe');installer.write_bytes(b'signed setup '+version.encode())
        paths['installer']=installer
        self.fixture.prepare(version=version,change=lambda m:m.update(bundleId=bundle))
        network=self.fixture.network; signed=self.fixture.manifest
        prefix=network.release['assets'][0]['browser_download_url'].rsplit('/',1)[0]+'/'
        assets=[]
        for index,(role,path) in enumerate(paths.items(),101):
            name=path.name;raw=path.read_bytes()
            assets.append(dict(role=role,assetId=index,name=name,size=len(raw),sha256=hashlib.sha256(raw).hexdigest()))
            network.documents[prefix+name]=raw
        signed['assets']=assets
        raw=json.dumps(signed).encode();signature=json.dumps(dict(algorithm='Ed25519',keyId=updater.KEY_ID,
            signature=base64.b64encode(self.fixture.key.sign(raw)).decode())).encode()
        network.release['assets']=[dict(id=a['assetId'],name=a['name'],size=a['size'],state='uploaded',browser_download_url=prefix+a['name']) for a in assets]
        for index,(name,data) in enumerate([('update-manifest.json',raw),('update-manifest.sig',signature)],110):
            network.documents[prefix+name]=data
            network.release['assets'].append(dict(id=index,name=name,size=len(data),state='uploaded',browser_download_url=prefix+name))
        network.release['target_commitish']=COMMIT
        network.release_by_id[42]=json.loads(json.dumps(network.release))
        network.documents[updater.API_ROOT+'/releases/tags/v0.1.1']=json.dumps(network.release).encode()
        return signed,paths,network

    def leases(self, root, sid):
        owner=self
        class Lease:
            def __init__(self,name):self.name=name;self.acquired=False
            def __enter__(self):
                if self.name in owner.owned:raise CutError('UPDATER_ALREADY_RUNNING','Fixture busy owner')
                owner.owned.add(self.name);self.acquired=True;owner.events.append('acquire-'+self.name);return self
            def close(self):
                if self.acquired:
                    owner.owned.remove(self.name);self.acquired=False;owner.events.append('release-'+self.name)
            def __exit__(self,*args):self.close()
        return Lease('supervisor'),Lease('runtime')

    def spawn(self,args,**kwargs):
        self.assertFalse(self.owned,'Dispatch before both ownership leases drained')
        owner=self
        class Pipe(io.BytesIO):
            def close(self):owner.pipes.append(self.getvalue());super().close()
        self.launches.append((args,kwargs));self.events.append('dispatch')
        return type('Child',(),{'stdin':Pipe()})()

    def migrate(self, **kwargs):
        try:entry=importlib.import_module('tools.maintenance_entry')
        except ModuleNotFoundError:self.fail('M1 maintenance entry is missing')
        return entry.migrate(self.root,**dict(self.options,**kwargs))

    def journal(self):return json.loads((self.root/'updates/journal.json').read_bytes())

    def test_pending_provision_resume_offline_preserves_files_and_hands_off_once(self):
        first=self.migrate()
        self.assertEqual(first['status'],'PENDING_PROVISIONING')
        self.assertEqual(self.journal()['updateState'],'PENDING_ACTIVATION')
        self.assertIsNone(self.journal().get('_activationHandoff'))
        installs=sum(a[1]=='/install' for a,_ in self.windows.adobe.calls)
        self.windows.processes.append(dict(pid=55,imageName='Adobe Premiere Pro.exe'))
        self.now+=400
        # The receipt must still be current; waiting longer than ticket TTL before
        # minting must not expire the subsequently issued ticket.
        self.receipt()
        offline=type('Offline',(),{'get':lambda *a,**k:(_ for _ in ()).throw(AssertionError('Unexpected network'))})()
        original=updater.UpdateManager.create_activation_handoff
        def ticket(manager):
            self.assertEqual(self.registry.values[r'Software\Classes\contentrium-cut\shell\open\command',''],
                '"'+str(self.root/'Contentrium CUT Launcher.exe')+'" --open --root "'+str(self.root)+'"')
            self.events.append('ticket');return original(manager)
        with patch.object(updater.UpdateManager,'create_activation_handoff',ticket):
            result=self.migrate(action='resume',transport=offline)
        self.assertEqual(result['status'],'HANDOFF_DISPATCHED')
        self.assertEqual(result['updateState'],'PENDING_ACTIVATION')
        self.assertFalse(result['gateOpen']);self.assertNotIn('activationVerified',result)
        self.assertEqual(sum(a[1]=='/install' for a,_ in self.windows.adobe.calls),installs)
        self.assertEqual(len(self.launches),1)
        packet=json.loads(self.pipes[0]);self.assertEqual(packet['updateId'],self.journal()['updateId'])
        self.assertNotIn(packet['token'],json.dumps(result));self.assertNotIn(packet['token'],str(self.launches))
        self.assertNotIn(packet['token'],(self.root/'updates/journal.json').read_text())
        self.assertEqual(self.events[-4:],['ticket','release-runtime','release-supervisor','dispatch'])
        authorization=self.journal()['_activationHandoff']
        self.assertEqual(authorization['expiresAt'],self.now+300)
        self.assertFalse(authorization['consumed'])
        self.assertEqual((self.root/'updates/first-install.json').read_bytes(),self.original_first)
        self.assertEqual({name:(self.root/name).read_bytes() for name in self.user_files},self.user_files)
        with self.assertRaises(CutError):self.migrate(action='resume',transport=offline)
        self.assertEqual(len(self.launches),1)

    def test_target_identity_mismatch_and_wrong_ownership_fail_before_install(self):
        before_installs=sum(a[1]=='/install' for a,_ in self.windows.adobe.calls)
        for changes in [dict(commit='f'*40),dict(tag='v0.1.2'),dict(version='0.1.2')]:
            with self.subTest(changes=changes),self.assertRaises(CutError):self.migrate(**changes)
        self.assertEqual(sum(a[1]=='/install' for a,_ in self.windows.adobe.calls),before_installs)
        before=len(self.network.calls)
        (self.root/'Contentrium CUT Launcher.exe').write_bytes(b'wrong owner')
        with self.assertRaises(CutError):self.migrate()
        self.assertEqual(len(self.network.calls),before)

    def test_pinned_migration_remains_available_when_latest_is_a_newer_release(self):
        self.network.release=dict(self.network.release,tag_name='v0.1.2',target_commitish='f'*40)
        result=self.migrate()
        self.assertEqual(result['status'],'PENDING_PROVISIONING')
        self.assertEqual(self.journal()['_attemptCandidate']['manifest']['appVersion'],'0.1.1')
        self.assertFalse(any(url==updater.LATEST_URL for url,_ in self.network.calls))
        self.assertTrue(any(url.endswith('/releases/tags/v0.1.1') for url,_ in self.network.calls))

    def test_pinned_lookup_rejects_an_unreviewed_tag_target_before_install(self):
        tag=updater.API_ROOT+'/releases/tags/v0.1.1'
        self.network.documents[tag]=json.dumps(dict(self.network.release,target_commitish='f'*40)).encode()
        before=sum(a[1]=='/install' for a,_ in self.windows.adobe.calls)
        with self.assertRaises(CutError):self.migrate()
        self.assertEqual(sum(a[1]=='/install' for a,_ in self.windows.adobe.calls),before)

    def test_migration_tag_does_not_reuse_latest_conditional_metadata(self):
        entry=importlib.import_module('tools.maintenance_entry')
        headers=dict(updater.API_HEADERS,**{'If-None-Match':'"latest-0.1.2"'})
        network=entry._MigrationTransport(self.network)
        result=network.get(updater.LATEST_URL,headers,15,1024*1024)
        self.assertEqual(result.status,200)
        self.assertEqual(self.network.calls[-1][0],updater.API_ROOT+'/releases/tags/v0.1.1')
        self.assertNotIn('If-None-Match',self.network.calls[-1][1])
        self.assertEqual(headers['If-None-Match'],'"latest-0.1.2"')
        network.get(updater.API_ROOT+'/releases/42',headers,15,1024*1024)
        self.assertEqual(self.network.calls[-1][1],headers)

    def test_busy_supervisor_or_runtime_is_diagnostic_and_leaves_journal_untouched(self):
        for name in ['supervisor','runtime']:
            with self.subTest(owner=name):
                self.owned.add(name)
                with self.assertRaises(CutError) as error:self.migrate()
                self.assertEqual(error.exception.code,'UPDATER_ALREADY_RUNNING')
                self.assertEqual(self.owned,{name});self.owned.clear()
                self.assertFalse((self.root/'updates/journal.json').exists())
        self.assertEqual(self.network.calls,[]);self.assertEqual(self.launches,[])

    def test_saved_signature_rejected_without_touching_legacy_evidence(self):
        path=self.root/'updates/first-install.json';saved=json.loads(path.read_bytes())
        saved['signature']=base64.b64encode(b'{"signature":"bad"}').decode();path.write_text(json.dumps(saved))
        before=path.read_bytes()
        with self.assertRaises(CutError):self.migrate()
        self.assertEqual(path.read_bytes(),before);self.assertEqual(self.network.calls,[])

    def test_pending_resume_target_mismatch_does_not_change_journal(self):
        self.migrate();before=(self.root/'updates/journal.json').read_bytes()
        with self.assertRaises(CutError):self.migrate(action='resume',commit='a'*40)
        self.assertEqual((self.root/'updates/journal.json').read_bytes(),before)

    def test_saved_candidate_must_remain_a_published_stable_release(self):
        self.migrate();path=self.root/'updates/journal.json';saved=path.read_bytes()
        for changes in [dict(draft=True),dict(prerelease=True),dict(name='Other product')]:
            changed=json.loads(saved);changed['_attemptCandidate']['release'].update(changes)
            path.write_text(json.dumps(changed));before=path.read_bytes()
            with self.subTest(changes=changes),self.assertRaises(CutError):self.migrate(action='resume')
            self.assertEqual(path.read_bytes(),before)

    def test_historical_bytes_archived_before_explicit_recovery_then_retry(self):
        original=self.windows.w._atomic_json
        def interrupt(path,value):
            original(path,value)
            if path==self.windows.hooks.launcher_journal and value.get('state')=='LAUNCHER_REPLACED':
                raise KeyboardInterrupt()
        with patch.object(self.windows.w,'_atomic_json',interrupt):
            with self.assertRaises(KeyboardInterrupt):self.migrate()
        saved=(self.root/'updates/journal.json').read_bytes()
        self.assertEqual(json.loads(saved)['updateState'],'INSTALLING')
        with self.assertRaises(CutError):self.migrate(action='resume')
        recovered=self.migrate(action='recover')
        self.assertEqual(recovered['updateState'],'ROLLED_BACK')
        history=self.root/'updates/maintenance-history'/hashlib.sha256(saved).hexdigest()/'journal.json'
        self.assertEqual(history.read_bytes(),saved)
        result=self.migrate()
        self.assertEqual(result['status'],'PENDING_PROVISIONING')
        self.assertEqual(self.journal()['updateEpoch'],2)
        self.assertNotEqual(self.journal()['updateId'],json.loads(saved)['updateId'])
        self.assertEqual(history.read_bytes(),saved)

    def test_resume_tampered_cancel_result_asset_and_snapshot_never_dispatches(self):
        self.migrate();journal=self.root/'updates/journal.json';saved=journal.read_bytes()
        for field,value in [('cancelRequested',True),('stopSignaled',False),('installerResult',{'status':'COMPLETE'})]:
            with self.subTest(field=field):
                changed=json.loads(saved);changed[field]=value;journal.write_text(json.dumps(changed));before=journal.read_bytes()
                with self.assertRaises(CutError):self.migrate(action='resume')
                self.assertEqual(journal.read_bytes(),before)
        journal.write_bytes(saved)
        asset=Path(self.journal()['preparedAssets']['panel']['path']);original=asset.read_bytes();asset.write_bytes(b'tampered')
        with self.assertRaises(CutError):self.migrate(action='resume')
        self.assertEqual(journal.read_bytes(),saved);asset.write_bytes(original)
        snapshot=Path(self.journal()['snapshot']['dataSnapshot'])/'jobs/job.json';snapshot.write_bytes(b'tampered backup')
        with self.assertRaises(CutError):self.migrate(action='resume')
        self.assertEqual(journal.read_bytes(),saved);self.assertEqual(self.launches,[])

    def test_integration_failure_retains_bootstrap_and_resumes_without_a_ticket(self):
        self.migrate();self.receipt()
        entry=importlib.import_module('tools.maintenance_entry')
        original=entry.install_integration
        def interrupted(*args,**kwargs):
            original(*args,**kwargs)
            raise KeyboardInterrupt()
        with patch.object(entry,'install_integration',interrupted):
            with self.assertRaises(KeyboardInterrupt):self.migrate(action='resume')
        private=(self.root/'private/auth-bootstrap.json').read_bytes()
        self.assertIsNone(self.journal().get('_activationHandoff'));self.assertEqual(self.launches,[])
        self.assertEqual(self.migrate(action='resume')['status'],'HANDOFF_DISPATCHED')
        self.assertEqual((self.root/'private/auth-bootstrap.json').read_bytes(),private)

    def test_issued_expired_or_consumed_ticket_requires_recovery_without_renewal(self):
        self.migrate();self.receipt();self.migrate(action='resume')
        path=self.root/'updates/journal.json';saved=path.read_bytes();self.now+=301
        for consumed in [False,True]:
            changed=json.loads(saved);changed['_activationHandoff']['consumed']=consumed
            if consumed:changed['_activationHandoff']['consumedAt']=1001
            path.write_text(json.dumps(changed));before=path.read_bytes()
            with self.assertRaises(CutError):self.migrate(action='resume')
            self.assertEqual(path.read_bytes(),before)
        self.assertEqual(len(self.launches),1)

    def test_history_conflict_is_preserved_and_blocks_recovery(self):
        self.migrate();path=self.root/'updates/journal.json';before=path.read_bytes()
        history=self.root/'updates/maintenance-history'/hashlib.sha256(before).hexdigest()/'journal.json'
        history.parent.mkdir(parents=True);history.write_bytes(b'conflicting evidence')
        with self.assertRaises(CutError) as error:self.migrate(action='recover')
        self.assertEqual(error.exception.code,'MAINTENANCE_HISTORY')
        self.assertEqual(path.read_bytes(),before);self.assertEqual(history.read_bytes(),b'conflicting evidence')

    def test_original_draft_or_wrong_release_identity_is_rejected(self):
        path=self.root/'updates/first-install.json';saved=path.read_bytes()
        for changes in [dict(draft=True),dict(name='Another product')]:
            value=json.loads(saved);value['release'].update(changes);path.write_text(json.dumps(value))
            with self.assertRaises(CutError):self.migrate()
        self.assertEqual(self.network.calls,[])

    def test_live_host_wait_never_installs_and_interrupted_wait_requires_recovery(self):
        self.windows.processes.append(dict(pid=55,imageName='Adobe Premiere Pro.exe'))
        result=self.migrate()
        self.assertEqual(result['updateState'],'WAITING_HOST_EXIT')
        before=(self.root/'updates/journal.json').read_bytes()
        with self.assertRaises(CutError):self.migrate()
        self.assertEqual((self.root/'updates/journal.json').read_bytes(),before)

    def test_source_activation_boundary_refuses_fake_receipt(self):
        self.migrate();self.assertIsNone(self.windows.hooks.activation_probe)
        receipt=dict(panelVersion='0.1.1',companionVersion='0.1.1',bundleId='target-bundle',handshake=True,dataReadable=True)
        self.assertFalse(self.windows.hooks.verify('0.1.1','target-bundle',receipt))
        location=bootstrap.InstallLocation(self.root,'fixture','key','S-1-5-21-123')
        verified=lifecycle.verify_runtime(location,self.windows.hooks)
        with patch.object(lifecycle,'WindowsNamedMutex',side_effect=lambda root:self.leases(root,'sid')[1]):
            with self.assertRaises(CutError):lifecycle.run_headless(location,verified)
        self.assertNotEqual(self.journal()['updateState'],'COMPLETE')

    def test_release_commit_change_before_replace_is_refused(self):
        self.network.release_by_id[42]['target_commitish']='f'*40
        result=self.migrate()
        self.assertEqual(result['updateState'],'FAILED_BEFORE_REPLACE')
        self.assertEqual(result['error']['code'],'UPDATE_WITHDRAWN')
        self.assertEqual(self.windows.adobe.registered[0]['version'],'0.1.0')

    def test_public_payload_rejects_maintenance_history(self):
        history=self.base/'public/maintenance-history'/('a'*64)/'evidence.txt'
        history.parent.mkdir(parents=True);history.write_bytes(b'per-install evidence')
        with self.assertRaises(CutError):release_assets.assert_public_tree(self.base/'public')

    def test_later_signed_latest_release_is_not_substituted(self):
        _,_,later=self.payload('0.1.2','later-bundle')
        before=sum(a[1]=='/install' for a,_ in self.windows.adobe.calls)
        with self.assertRaises(CutError) as error:self.migrate(transport=later)
        self.assertEqual(error.exception.code,'MAINTENANCE_TARGET')
        self.assertEqual(sum(a[1]=='/install' for a,_ in self.windows.adobe.calls),before)
        self.assertEqual(self.journal()['updateState'],'IDLE')

    def test_cli_uses_exact_explicit_identity_and_reports_only_pending_status(self):
        from contextlib import redirect_stdout,redirect_stderr
        entry=importlib.import_module('tools.maintenance_entry');output=io.StringIO();errors=io.StringIO()
        original=entry.migrate
        def isolated(root,**kwargs):return original(root,**dict(self.options,**kwargs))
        with patch.object(entry,'migrate',isolated),redirect_stdout(output),redirect_stderr(errors):
            code=entry.main(['--root',str(self.root),'--tag','v0.1.1','--version','0.1.1','--commit',COMMIT])
        self.assertEqual(code,2);self.assertEqual(errors.getvalue(),'')
        self.assertEqual(json.loads(output.getvalue().splitlines()[-1])['status'],'PENDING_PROVISIONING')
        self.assertNotIn(str(self.root),output.getvalue())
        before=(self.root/'updates/journal.json').read_bytes()
        with redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
            entry.main(['--root',str(self.root),'--tag','v0.1.1','--version','0.1.1','--commit',COMMIT,'--public-key','alternate'])
        self.assertEqual((self.root/'updates/journal.json').read_bytes(),before)

    def test_wait_interruption_during_provision_does_not_issue_a_ticket(self):
        observed=[]
        result=self.migrate(wait=lambda seconds:observed.append(seconds) or True)
        self.assertEqual(result['status'],'PENDING_PROVISIONING');self.assertEqual(observed,[0.75])
        self.assertIsNone(self.journal().get('_activationHandoff'));self.assertEqual(self.launches,[])
        self.assertFalse(self.owned)

    def test_migration_receipt_is_bound_to_attempt_and_preparation_stays_nonsecret(self):
        self.migrate();self.receipt();result=self.migrate(action='resume')
        migration=self.journal()['sourceAssistedMigration']
        self.assertEqual(migration.get('updateId'),result['updateId'])
        self.assertEqual(migration.get('updateEpoch'),result['updateEpoch'])
        self.assertEqual(migration['targetCommit'],COMMIT)
        packet=json.loads(self.pipes[0])
        for path in self.root.rglob('*.json'):
            self.assertNotIn(packet['token'].encode(),path.read_bytes())

    def activated_manager(self):
        packet=json.loads(self.pipes[0])
        return updater.UpdateManager(self.root,self.fixture.public,'0.1.1',lambda _:None,self.windows.hooks,
            transport=self.network,clock=lambda:self.now,activation_handoff=packet['token'])

    def test_real_activation_failure_rolls_back_owned_registration_and_keeps_unrelated_values(self):
        run=r'Software\Microsoft\Windows\CurrentVersion\Run'
        approved=r'Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run'
        protocol=r'Software\Classes\contentrium-cut\shell\open\command'
        self.registry.values[run,'Other product']='preserve other startup'
        self.registry.values[approved,'Contentrium CUT']=bytes([2])+bytes(11)
        self.migrate();self.receipt();self.migrate(action='resume')
        manager=self.activated_manager()
        failed=manager.activate(dict(panelVersion='0.1.1',companionVersion='0.1.1',bundleId='target-bundle',handshake=True,dataReadable=True))
        self.assertEqual(failed['updateState'],'ROLLED_BACK')
        self.assertEqual(self.registry.values[protocol,''],'"'+str(self.root/'Contentrium CUT Launcher.exe')+'" --open')
        self.assertNotIn((run,'Contentrium CUT'),self.registry.values)
        self.assertEqual(self.registry.values[run,'Other product'],'preserve other startup')
        self.assertEqual(self.registry.values[approved,'Contentrium CUT'],bytes([2])+bytes(11))
        previous=dict(self.registry.values)
        self.windows.hooks.rollback(failed['snapshot'])
        self.assertEqual(self.registry.values,previous)

    def test_partial_integration_is_recoverable_from_durable_registration_intent(self):
        self.migrate();self.receipt()
        protocol=r'Software\Classes\contentrium-cut\shell\open\command'
        set_value=self.registry.SetValueEx
        def interrupted(key,name,reserved,kind,value):
            set_value(key,name,reserved,kind,value)
            if key==protocol:raise KeyboardInterrupt()
        with patch.object(self.registry,'SetValueEx',interrupted):
            with self.assertRaises(KeyboardInterrupt):self.migrate(action='resume')
        self.assertIsNone(self.journal().get('_activationHandoff'))
        result=self.migrate(action='recover')
        self.assertEqual(result['updateState'],'ROLLED_BACK')
        self.assertEqual(self.registry.values[protocol,''],'"'+str(self.root/'Contentrium CUT Launcher.exe')+'" --open')
        self.assertEqual(self.launches,[])

    def test_changed_registry_value_blocks_rollback_until_explicit_conflict_resolution(self):
        run=r'Software\Microsoft\Windows\CurrentVersion\Run'
        protocol=r'Software\Classes\contentrium-cut\shell\open\command'
        self.migrate();self.receipt();self.migrate(action='resume')
        expected=self.registry.values[run,'Contentrium CUT'];modern_protocol=self.registry.values[protocol,'']
        self.registry.values[run,'Contentrium CUT']='user changed command'
        manager=self.activated_manager()
        result=manager.activate({})
        self.assertEqual(result['updateState'],'RECOVERY_REQUIRED')
        self.assertEqual(self.registry.values[run,'Contentrium CUT'],'user changed command')
        self.assertEqual(self.registry.values[protocol,''],modern_protocol)
        self.registry.values[run,'Contentrium CUT']=expected
        result=self.migrate(action='recover')
        self.assertEqual(result['updateState'],'ROLLED_BACK')
        self.assertNotIn((run,'Contentrium CUT'),self.registry.values)
        self.assertEqual(self.registry.values[protocol,''],'"'+str(self.root/'Contentrium CUT Launcher.exe')+'" --open')

    def test_registration_intent_is_attempt_bound_and_never_removes_preexisting_startup(self):
        from contentrium_cut import integration, migration_registration
        run=r'Software\Microsoft\Windows\CurrentVersion\Run'
        startup=integration.startup_command(self.root);self.registry.values[run,'Contentrium CUT']=startup
        self.migrate();self.receipt();self.migrate(action='resume')
        path=next((self.root/'updates/migration-registration').rglob('migration-registration.json'))
        saved=path.read_bytes();changed=json.loads(saved)
        self.assertEqual(changed['updateId'],self.journal()['updateId'])
        changed['updateId']='f'*32;path.write_text(json.dumps(changed))
        with self.assertRaises(CutError):release_assets.assert_public_tree(path)
        failed=self.activated_manager().activate({})
        self.assertEqual(failed['updateState'],'RECOVERY_REQUIRED')
        self.assertEqual(self.registry.values[run,'Contentrium CUT'],startup)
        path.write_bytes(saved);self.assertEqual(self.migrate(action='recover')['updateState'],'ROLLED_BACK')
        self.assertEqual(self.registry.values[run,'Contentrium CUT'],startup)
        previous=dict(self.registry.values)
        later=self.windows.hooks.snapshot(self.target)
        self.assertIsNone(migration_registration.restore(self.root,later,registry=self.registry))
        self.assertEqual(self.registry.values,previous)

    def test_interrupted_registration_restore_can_repeat_without_reenabling_startup(self):
        self.migrate();self.receipt();self.migrate(action='resume')
        protocol=r'Software\Classes\contentrium-cut\shell\open\command'
        with patch.object(self.registry,'DeleteValue',side_effect=OSError('isolated deletion fault')):
            failed=self.activated_manager().activate({})
        self.assertEqual(failed['updateState'],'RECOVERY_REQUIRED')
        self.assertEqual(self.registry.values[protocol,''],'"'+str(self.root/'Contentrium CUT Launcher.exe')+'" --open')
        self.assertEqual(self.migrate(action='recover')['updateState'],'ROLLED_BACK')
        self.assertNotIn((r'Software\Microsoft\Windows\CurrentVersion\Run','Contentrium CUT'),self.registry.values)


if __name__=='__main__':unittest.main()
