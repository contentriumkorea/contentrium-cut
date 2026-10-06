import importlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from contentrium_cut.contract import CutError


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / 'installation'
        self.roaming = self.base / 'Roaming'
        self.data = self.roaming / 'Adobe/UXP/PluginsStorage/PPRO/26/External/com.contentrium.cut/PluginData'
        self.data.mkdir(parents=True)
        self.protected = []
        self.module = importlib.import_module('contentrium_cut.bootstrap')
        self.evidence = dict(schemaVersion=1, productId='com.contentrium.cut', hostMajor=26,
                             verifiedBy='installed-uxp-getDataFolder', canonicalPluginData=str(self.data),
                             receiptHash='ab' * 32)

    def provision(self, **kwargs):
        return self.module.provision(self.root, self.evidence, owner_sid='S-1-5-21-123',
            roaming_root=self.roaming, protect=lambda path, sid: self.protected.append((path, path.stat().st_size if path.is_file() else None)), **kwargs)

    def test_private_install_retains_secret_on_update_and_preserves_unknown_files(self):
        unknown = self.data / 'user-data.txt'
        unknown.write_text('preserve')
        first = self.provision()
        secret_file = self.root / 'private/auth-bootstrap.json'
        before = secret_file.read_bytes()
        second = self.provision()
        self.assertEqual(first.installation_id, second.installation_id)
        self.assertEqual(secret_file.read_bytes(), before)
        self.assertEqual((self.data / 'contentrium-bootstrap.json').read_bytes(), before)
        self.assertEqual(unknown.read_text(), 'preserve')
        self.assertTrue(all(size in [0, None] for _, size in self.protected))
        resolved = self.module.resolve_installation(self.root, owner_sid='S-1-5-21-123')
        self.assertEqual(resolved.root, self.root)
        with self.assertRaises(CutError): self.module.resolve_installation(self.root, owner_sid='S-1-5-21-other')

    def test_unverified_mapping_developer_and_conflicting_files_fail_closed(self):
        for evidence in [dict(self.evidence, hostMajor=27), dict(self.evidence, verifiedBy='directory-exists'),
                         dict(self.evidence, canonicalPluginData=str(self.data).replace('External', 'Developer'))]:
            with self.assertRaises(CutError): self.module.provision(self.root, evidence, owner_sid='S-1-5-21-123', roaming_root=self.roaming, protect=lambda *a:None)
        self.assertFalse(self.root.exists())
        (self.data / 'contentrium-bootstrap.json').write_text('{"unknown":true}')
        with self.assertRaises(CutError): self.provision()
        self.assertFalse((self.root / 'install-location.json').exists())
        self.assertEqual((self.data / 'contentrium-bootstrap.json').read_text(), '{"unknown":true}')

    def test_environment_path_does_not_select_a_different_root(self):
        self.provision()
        with self.assertRaises(CutError):
            self.module.resolve_installation(self.base / 'wrong', owner_sid='S-1-5-21-123')
        with self.assertRaises(CutError):
            self.module.resolve_installation(self.root / '..' / 'installation', owner_sid='S-1-5-21-123')

    def test_handle_resolved_physical_root_is_propagated(self):
        with patch.object(self.module, '_physical_path', return_value=self.base/'physical'):
            self.assertEqual(self.module.canonical_root(self.root),self.base/'physical')

    def test_native_reprovision_rejects_unprotected_existing_files_without_changes(self):
        self.provision()
        paths = [self.root/'install-location.json', self.root/'private/auth-bootstrap.json',
                 self.data/'contentrium-bootstrap.json']
        before = {path: path.read_bytes() for path in paths}
        for rejected in paths:
            with self.subTest(rejected=rejected.name):
                checked = []
                def verify(path, sid):
                    checked.append(path)
                    self.assertEqual(sid, 'S-1-5-21-123')
                    if path == rejected: raise CutError('INSTALL_PROTECTION', 'Fixture rejects public DACL.')
                with patch.object(self.module, 'current_user_sid', return_value='S-1-5-21-123'), \
                        patch.object(self.module, 'known_folder', return_value=self.roaming), \
                        patch.object(self.module, 'verify_private_protection', side_effect=verify):
                    with self.assertRaises(CutError) as error:
                        self.module.provision(self.root, self.evidence)
                self.assertEqual(error.exception.code, 'INSTALL_PROTECTION')
                self.assertIn(rejected, checked)
                self.assertEqual({path: path.read_bytes() for path in paths}, before)
                self.assertFalse(list(self.base.rglob('*.tmp')))

    def test_native_protected_reprovision_retains_key_and_checks_all_copies(self):
        first = self.provision()
        paths = [self.root/'install-location.json', self.root/'private/auth-bootstrap.json',
                 self.data/'contentrium-bootstrap.json']
        before = {path: path.read_bytes() for path in paths}
        with patch.object(self.module, 'current_user_sid', return_value='S-1-5-21-123'), \
                patch.object(self.module, 'known_folder', return_value=self.roaming), \
                patch.object(self.module, 'verify_private_protection') as verify:
            retained = self.module.provision(self.root, self.evidence)
        self.assertEqual(retained, first)
        self.assertEqual({call.args[0] for call in verify.call_args_list}, set(paths))
        self.assertEqual({path: path.read_bytes() for path in paths}, before)


if __name__ == '__main__': unittest.main()
