"""Real signed metadata/files with controlled network, registry and process boundaries."""
import importlib
import base64
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch
import test_updater as updater_tests
import test_windows_install as windows_tests


class InstallerTests(unittest.TestCase):
    def private_integration_options(self):
        roaming=self.fixture.root/'isolated-roaming'
        data=roaming/'Adobe/UXP/PluginsStorage/PPRO/26/External/com.contentrium.cut/PluginData'
        data.mkdir(parents=True,exist_ok=True)
        return dict(mapping_evidence=dict(schemaVersion=1,productId='com.contentrium.cut',hostMajor=26,
                    verifiedBy='installed-uxp-getDataFolder',canonicalPluginData=str(data),receiptHash='ab'*32),
                    owner_sid='S-1-5-21-123',roaming_root=roaming,protect=lambda *a:None,popen=lambda *a,**kw:None)
    def setUp(self):
        for target in ['tkinter.Tk', 'tkinter.messagebox.showerror']:
            guard = patch(target, side_effect=AssertionError('Unexpected GUI path blocked by installer test guard'))
            guard.start(); self.addCleanup(guard.stop)
        physical = patch('contentrium_cut.bootstrap._physical_path', side_effect=lambda path:path.resolve())
        physical.start(); self.addCleanup(physical.stop)
        self.fixture = updater_tests.UpdaterTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.entry = importlib.import_module('tools.install_entry')
        gui = patch.object(self.entry, 'show_gui', side_effect=AssertionError('Unexpected Setup GUI blocked by installer test guard'))
        gui.start(); self.addCleanup(gui.stop)
        # Existing tests isolate first-install/update selection from B2 enrollment.
        # Dedicated test_install_completion exercises the real preparation path.
        self.preparation_patch=patch.object(self.entry,'prepare_installation',return_value={'status':'PREPARED'})
        self.preparation_patch.start();self.addCleanup(self.preparation_patch.stop)
        self.root = self.fixture.root / 'install'
        self.setup = self.fixture.root / 'Setup.exe'
        self.setup.write_bytes(b'MZ frozen installer boundary')
        self.config = {'publicKey': base64.b64encode(self.fixture.public).decode()}
        self.calls = []
        owner = self
        class Installation:
            def compatibility(inner, manifest): return []
            def _require_host_exit(inner): owner.calls.append('host-exit')
            def bootstrap(inner, manifest, assets):
                owner.calls.append(('bootstrap', manifest, {k: Path(v['path']).read_bytes() for k,v in assets.items()}))
                return {'status': 'PENDING_ACTIVATION', 'appVersion': manifest['appVersion'], 'bundleId':manifest['bundleId']}
        self.hooks = Installation()

    def install(self, **kwargs):
        return self.entry.install(self.config, self.root, installation=self.hooks,
            transport=self.fixture.network, launcher_source=self.setup, mutex=lambda root: tempfile.TemporaryDirectory(),
            dependency_installer=lambda root: self.calls.append('ffmpeg'),
            integration_installer=lambda *args, **kw: self.calls.append('integration') or {'launcher':'boundary','shortcut':'boundary'}, **kwargs)

    def actual_windows_payload(self, extra=None):
        import hashlib
        windows = windows_tests.WindowsInstallationTests('runTest')
        windows.setUp(); self.addCleanup(windows.doCleanups)
        self.root = windows.root
        self.hooks = windows.hooks
        manifest, paths = windows.payload('0.2.0','bundle-new',extra=extra)
        binary = {role:path.read_bytes() for role,path in paths.items()}
        def change(signed):
            for asset in signed['assets']:
                asset['size']=len(binary[asset['role']]); asset['sha256']=hashlib.sha256(binary[asset['role']]).hexdigest()
        self.fixture.prepare(change=change)
        for asset in self.fixture.network.release['assets']:
            role = 'companion' if asset['name'].endswith('.zip') else 'panel' if asset['name'].endswith('.ccx') else None
            if role:
                self.fixture.network.documents[asset['browser_download_url']]=binary[role]
                asset['size']=len(binary[role])
        # The stable Setup is an exact signed installer-role asset, not arbitrary bytes.
        manifest=self.fixture.manifest;prefix=self.fixture.network.release['assets'][0]['browser_download_url'].rsplit('/',1)[0]+'/'
        setup=self.setup.read_bytes();name='Contentrium-CUT-Setup.exe'
        manifest['assets'].append(dict(role='installer',assetId=105,name=name,size=len(setup),sha256=hashlib.sha256(setup).hexdigest()))
        self.fixture.network.release['assets'].append(dict(id=105,name=name,size=len(setup),state='uploaded',browser_download_url=prefix+name))
        self.fixture.network.documents[prefix+name]=setup
        raw=json.dumps(manifest,separators=(',',':')).encode()
        signature=json.dumps(dict(algorithm='Ed25519',keyId=manifest['signingKeyId'],signature=base64.b64encode(self.fixture.key.sign(raw)).decode())).encode()
        for name,value in [('update-manifest.json',raw),('update-manifest.sig',signature)]:
            self.fixture.network.documents[prefix+name]=value
            next(a for a in self.fixture.network.release['assets'] if a['name']==name)['size']=len(value)
        self.fixture.network.release_by_id[42]=json.loads(json.dumps(self.fixture.network.release))
        return windows

    def test_real_bootstrap_registry_integration_failure_is_repaired_offline_without_reinstall(self):
        from contextlib import nullcontext
        windows = self.actual_windows_payload()
        integration = importlib.import_module('contentrium_cut.integration')
        class Registry:
            HKEY_CURRENT_USER, REG_SZ = 1, 1
            fail=True
            @staticmethod
            def CreateKey(hive,key): return nullcontext(key)
            @staticmethod
            def QueryValueEx(key,name): raise FileNotFoundError()
            @staticmethod
            def OpenKey(hive,key):raise FileNotFoundError()
            @classmethod
            def SetValueEx(cls,*args):
                if cls.fail: raise PermissionError('controlled registry failure')
        def integrate(root, config, **kwargs):
            kwargs.pop('mapping_evidence',None)
            return integration.install_integration(root,config,**kwargs,**self.private_integration_options(),registry=Registry,menu_root=windows.root.parent/'menu')
        arguments = dict(installation=windows.hooks,transport=self.fixture.network,launcher_source=self.setup,
            resource_root=self.setup.parent,dependency_installer=lambda root:None,integration_installer=integrate,mutex=lambda root:nullcontext())
        with self.assertRaises(PermissionError): self.entry.install(self.config,self.root,**arguments)
        self.assertTrue(windows.hooks.active.exists())
        self.assertEqual(self.entry.setup_action(self.root),'repair')
        before = sum(args[1]=='/install' for args,_ in windows.adobe.calls)
        Registry.fail=False
        arguments['transport']=type('Offline',(),{'get':lambda *a:(_ for _ in ()).throw(ConnectionError('offline'))})()
        result = self.entry.install(self.config,self.root,**arguments)
        self.assertEqual(result['status'],'PENDING_ACTIVATION')
        self.assertTrue((windows.root.parent/'menu'/'Contentrium CUT.url').exists())
        self.assertEqual(sum(args[1]=='/install' for args,_ in windows.adobe.calls),before)
        self.assertEqual(json.loads((self.root/'updates'/'first-install.json').read_text())['state'],'READY_TO_OPEN')
        self.assertEqual(self.entry.setup_action(self.root),'open')

    def test_gui_recovery_pins_old_release_after_latest_advances(self):
        from contextlib import nullcontext
        windows = self.actual_windows_payload()
        original = windows.adobe.run
        def reopen(args, timeout):
            result=original(args,timeout)
            if args[1]=='/install': windows.processes.append({'pid':55,'imageName':'Adobe Premiere Pro.exe'})
            return result
        windows.adobe.run=reopen
        arguments=dict(installation=windows.hooks,transport=self.fixture.network,launcher_source=self.setup,
            resource_root=self.setup.parent,dependency_installer=lambda root:None,
            integration_installer=lambda *a,**k:{'launcher':'boundary','shortcut':'boundary'},mutex=lambda root:nullcontext())
        with self.assertRaises(Exception): self.entry.install(self.config,self.root,**arguments)
        self.assertFalse(windows.hooks.active.exists())
        latest_before=sum('/releases/latest' in url for url,_ in self.fixture.network.calls)
        self.fixture.network.release.update(id=43,tag_name='v0.3.0')
        windows.processes.clear(); windows.adobe.run=original
        arguments['installation']=windows.w.WindowsInstallation(windows.root,upia_path=windows.upia,runner=windows.adobe,process_probe=lambda: [])
        result=self.entry.install(self.config,self.root,**arguments)
        self.assertEqual(result['appVersion'],'0.2.0')
        self.assertEqual(sum('/releases/latest' in url for url,_ in self.fixture.network.calls),latest_before)
        self.assertEqual(sum(args[1]=='/install' for args,_ in windows.adobe.calls),1)

    def test_recovery_rejects_changed_or_withdrawn_pinned_release(self):
        from contextlib import nullcontext
        windows=self.actual_windows_payload()
        original=windows.adobe.run
        def reopen(args,timeout):
            result=original(args,timeout)
            if args[1]=='/install': windows.processes.append({'pid':55,'imageName':'Adobe Premiere Pro.exe'})
            return result
        windows.adobe.run=reopen
        arguments=dict(installation=windows.hooks,transport=self.fixture.network,launcher_source=self.setup,
                       dependency_installer=lambda root:None,integration_installer=lambda *a,**k:{},mutex=lambda root:nullcontext())
        with self.assertRaises(Exception): self.entry.install(self.config,self.root,**arguments)
        windows.processes.clear(); windows.adobe.run=original
        self.fixture.network.release_status=404
        with self.assertRaises(Exception): self.entry.install(self.config,self.root,**arguments)
        self.assertFalse(windows.hooks.active.exists())
        self.assertEqual(sum(args[1]=='/install' for args,_ in windows.adobe.calls),1)

    def test_repair_refuses_another_active_bundle_and_preserves_pointer(self):
        from contextlib import nullcontext
        windows=self.actual_windows_payload()
        arguments=dict(installation=windows.hooks,transport=self.fixture.network,launcher_source=self.setup,
                       dependency_installer=lambda root:None,integration_installer=lambda *a,**k:(_ for _ in ()).throw(PermissionError()),
                       mutex=lambda root:nullcontext())
        with self.assertRaises(PermissionError): self.entry.install(self.config,self.root,**arguments)
        descriptor=json.loads(windows.hooks.active.read_text());descriptor['bundleId']='different'
        windows.hooks.active.write_text(json.dumps(descriptor))
        before=windows.hooks.active.read_bytes()
        with self.assertRaises(Exception): self.entry.install(self.config,self.root,**arguments)
        self.assertEqual(windows.hooks.active.read_bytes(),before)
        self.assertEqual(sum(args[1]=='/install' for args,_ in windows.adobe.calls),1)

    def test_durable_install_intent_failure_has_no_bootstrap_side_effect(self):
        original=self.entry._atomic_json
        def fail(path,value):
            if path.name=='first-install.json' and value['state']=='BOOTSTRAP_PENDING':
                raise OSError('controlled intent failure')
            return original(path,value)
        with patch.object(self.entry,'_atomic_json',fail),self.assertRaises(OSError):self.install()
        self.assertFalse(any(isinstance(call,tuple) for call in self.calls))

    def test_signed_first_install_downloads_matching_assets_and_uses_release_endpoint(self):
        result = self.install()
        self.assertEqual(result['status'], 'PENDING_ACTIVATION')
        self.assertEqual(self.fixture.network.calls[0][0], self.fixture.u.API_ROOT + '/releases/latest')
        installed = next(call for call in self.calls if isinstance(call, tuple))
        self.assertEqual(set(installed[2]), {'panel', 'companion'})
        self.assertLess(self.calls.index('ffmpeg'), self.calls.index(installed))

    def test_asset_hash_tampering_never_reaches_bootstrap_or_shortcuts(self):
        url = next(url for url in self.fixture.network.documents if url.endswith('.zip'))
        self.fixture.network.documents[url] += b'tampered'
        with self.assertRaises(Exception): self.install()
        self.assertFalse(any(isinstance(call, tuple) for call in self.calls))
        self.assertNotIn('integration', self.calls)

    def test_bad_signature_missing_asset_and_existing_install_fail_closed(self):
        for case in ('signature', 'asset', 'existing'):
            with self.subTest(case=case):
                self.fixture.prepare()
                if case == 'signature':
                    url = next(url for url in self.fixture.network.documents if url.endswith('.sig'))
                    self.fixture.network.documents[url] = b'{}'
                if case == 'asset': self.fixture.network.release['assets'].pop()
                if case == 'existing':
                    (self.root / 'app').mkdir(parents=True)
                    (self.root / 'app' / 'active.json').write_text('original')
                with self.assertRaises(Exception): self.install()
                self.assertFalse(any(isinstance(call, tuple) for call in self.calls))

    def test_installer_exe_role_is_hash_checked_without_zip_validation(self):
        def add_installer(manifest):
            import hashlib
            content = b'MZ actual executable boundary'
            manifest['assets'].append({'role': 'installer', 'name': 'Contentrium-CUT-Setup.exe',
                'assetId': 105, 'size': len(content), 'sha256': hashlib.sha256(content).hexdigest()})
        self.fixture.prepare(change=add_installer)
        prefix = self.fixture.network.release['assets'][0]['browser_download_url'].rsplit('/',1)[0] + '/'
        content = b'MZ actual executable boundary'
        self.fixture.network.documents[prefix + 'Contentrium-CUT-Setup.exe'] = content
        self.fixture.network.release['assets'].append({'id':105,'name':'Contentrium-CUT-Setup.exe','size':len(content),
            'state':'uploaded','browser_download_url':prefix + 'Contentrium-CUT-Setup.exe'})
        self.fixture.network.release_by_id[42] = json.loads(json.dumps(self.fixture.network.release))
        self.assertEqual(self.install()['status'], 'PENDING_ACTIVATION')
        installed = next(call for call in self.calls if isinstance(call, tuple))
        self.assertEqual(installed[2]['installer'], content)

    def test_tag_endpoint_is_validated_and_pinned(self):
        self.assertEqual(self.entry.release_url('v0.2.0'), self.fixture.u.API_ROOT + '/releases/tags/v0.2.0')
        for tag in ('../latest', 'v0.2.0?token=secret', '0.2.0', 'v0.2.0-rc.1'):
            with self.subTest(tag=tag), self.assertRaises(Exception): self.entry.release_url(tag)

    def test_open_resolves_changed_active_version_and_verifies_before_launch(self):
        integration = importlib.import_module('contentrium_cut.integration')
        class Installed:
            def verify_application(inner, version, bundle): return version == '0.2.0' and bundle == 'new'
        directory = self.root / 'app' / 'versions' / '0.2.0'
        directory.mkdir(parents=True)
        (directory / 'Contentrium CUT.exe').write_bytes(b'MZ app boundary')
        (directory / 'config.json').write_text('{"appVersion":"0.2.0","bundleId":"new"}')
        (self.root / 'app' / 'active.json').write_text(json.dumps({'schemaVersion':1,'productId':'com.contentrium.cut',
            'appVersion':'0.2.0','bundleId':'new','versionDirectory':'versions/0.2.0'}))
        launches = []
        integration.open_active(self.root, installation=Installed(), popen=lambda args, **kw: launches.append(args))
        self.assertEqual(launches, [[str(directory / 'Contentrium CUT.exe'),'--supervisor','--root',str(self.root),'--config',str(directory / 'config.json')]])
        with self.assertRaises(Exception):
            integration.open_active(self.root, installation=type('Bad',(),{'verify_application':lambda *a:False})(),
                                    popen=lambda *a,**kw:launches.append('unverified'))
        with self.assertRaises(Exception):
            integration.open_active(self.root, installation=type('Claim',(),{'verify_application':lambda *a:{'claimed':True}})(),
                                    popen=lambda *a,**kw:launches.append('claim'))
        self.assertEqual(launches, [[str(directory / 'Contentrium CUT.exe'),'--supervisor','--root',str(self.root),'--config',str(directory / 'config.json')]])

    def test_protocol_is_stable_exe_without_uri_input_or_version_path(self):
        integration = importlib.import_module('contentrium_cut.integration')
        command = integration.protocol_command(self.root)
        self.assertEqual(command, '"' + str(self.root / 'Contentrium CUT Launcher.exe') + '" --open --root "'+str(self.root)+'"')
        self.assertNotIn('%1', command)
        self.assertNotIn('.cmd', command)

    def test_real_integration_copies_native_launcher_config_and_licenses(self):
        from contextlib import nullcontext
        integration = importlib.import_module('contentrium_cut.integration')
        resources = self.fixture.root / 'resources'
        (resources / 'licenses').mkdir(parents=True)
        (resources / 'licenses' / 'cryptography.txt').write_text('license boundary')
        values = []
        class Registry:
            HKEY_CURRENT_USER, REG_SZ = 1, 1
            @staticmethod
            def CreateKey(hive, key): return nullcontext(key)
            @staticmethod
            def QueryValueEx(key,name): raise FileNotFoundError()
            @staticmethod
            def OpenKey(hive,key):raise FileNotFoundError()
            @staticmethod
            def SetValueEx(key,name,reserved,kind,value): values.append((key,name,value))
        config = {'publicKey':'public key boundary','appVersion':'0.1.0'}
        result = integration.install_integration(self.root, config, launcher_source=self.setup,
            resource_root=resources, registry=Registry, menu_root=self.fixture.root / 'menu',**self.private_integration_options())
        self.assertEqual(Path(result['launcher']).read_bytes(), self.setup.read_bytes())
        self.assertEqual(json.loads((self.root / 'launcher-config.json').read_text()), config)
        self.assertEqual((self.root / 'licenses' / 'licenses' / 'cryptography.txt').read_text(), 'license boundary')
        self.assertIn(('Software\\Classes\\contentrium-cut\\shell\\open\\command','',integration.protocol_command(self.root)), values)
        self.assertEqual(Path(result['shortcut']).read_text(), '[InternetShortcut]\nURL=contentrium-cut://open\n')
        self.assertFalse((self.root / 'Contentrium CUT.cmd').exists())

    def test_missing_native_launcher_source_is_rejected_before_adobe_bootstrap(self):
        self.setup.unlink()
        with self.assertRaises(Exception): self.install()
        self.assertFalse(any(isinstance(call, tuple) for call in self.calls))

    def test_default_entry_is_gui_and_open_ignores_uri_without_loading_config(self):
        config = self.fixture.root / 'installer-config.json'
        config.write_text('{"publicKey":"boundary"}')
        with patch.object(self.entry,'show_gui') as gui:
            self.assertEqual(self.entry.main(['--root',str(self.root),'--config',str(config)]),0)
            gui.assert_called_once()
        with patch.object(self.entry,'open_active') as launch:
            self.assertEqual(self.entry.main(['--open','--root',str(self.root),'contentrium-cut://open?command=bad']),0)
            launch.assert_called_once_with(self.root)

    def test_release_withdrawn_after_download_cannot_install(self):
        self.fixture.network.release_status = 404
        with self.assertRaises(Exception): self.install()
        self.assertFalse(any(isinstance(call, tuple) for call in self.calls))


class DependencyTests(unittest.TestCase):
    def setUp(self):
        self.dependencies = importlib.import_module('contentrium_cut.dependencies')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def provider_zip(self, extra=None):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            for name, value in dict({'ffmpeg/bin/ffmpeg.exe':b'MZ ffmpeg', 'ffmpeg/bin/ffprobe.exe':b'MZ ffprobe',
                    'ffmpeg/LICENSE':b'GPLv3 boundary', 'ffmpeg/README.txt':b'provider boundary'}, **(extra or {})).items():
                archive.writestr(name,value)
        return stream.getvalue()

    def install_bytes(self, content, **kwargs):
        import hashlib
        class Provider:
            def download(inner, destination): Path(destination).write_bytes(content)
        with patch.object(self.dependencies, 'FFMPEG_SHA256', kwargs.pop('expected_hash',hashlib.sha256(content).hexdigest())):
            return self.dependencies.install_ffmpeg(self.root, transport=Provider(), **kwargs)

    def test_provider_hash_checked_extraction_and_license_record_are_real_files(self):
        installed = self.install_bytes(self.provider_zip())
        self.assertEqual(Path(installed['ffmpeg']).read_bytes(), b'MZ ffmpeg')
        record = json.loads((self.root / 'app' / 'ffmpeg' / 'source-record.json').read_text())
        self.assertFalse(record['redistributedByContentrium'])
        self.assertEqual(record['files']['LICENSE'], __import__('hashlib').sha256(b'GPLv3 boundary').hexdigest())

    def test_provider_hash_and_zip_escape_fail_before_dependency_activation(self):
        for content, expected in [(self.provider_zip(), '0'*64), (self.provider_zip({'../escape.exe':b'bad'}), None)]:
            with self.subTest(expected=expected), self.assertRaises(Exception):
                self.install_bytes(content, **({'expected_hash':expected} if expected else {}))
            self.assertFalse((self.root / 'app' / 'ffmpeg').exists())

    def test_provider_redirects_are_exact_allowlist(self):
        self.dependencies.validate_provider_url(self.dependencies.FFMPEG_URL)
        for url in ('http://github.com/GyanD/codexffmpeg/releases/download/9.0.2/a.zip',
                    'https://github.com/attacker/codexffmpeg/releases/download/9.0.2/a.zip',
                    'https://release-assets.githubusercontent.com.attacker/a.zip'):
            with self.subTest(url=url), self.assertRaises(Exception): self.dependencies.validate_provider_url(url)

    def test_unknown_existing_ffmpeg_is_preserved_on_failure(self):
        target = self.root / 'app' / 'ffmpeg'
        target.mkdir(parents=True)
        (target / 'ffmpeg.exe').write_bytes(b'user installed binary')
        with self.assertRaises(Exception): self.install_bytes(self.provider_zip())
        self.assertEqual((target / 'ffmpeg.exe').read_bytes(), b'user installed binary')
        self.assertFalse((target / 'source-record.json').exists())


if __name__ == '__main__': unittest.main()
