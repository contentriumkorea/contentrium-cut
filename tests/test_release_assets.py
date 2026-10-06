import importlib
from pathlib import Path
import tempfile
import unittest
from contentrium_cut.contract import CutError


class ReleaseAssetTests(unittest.TestCase):
    def test_public_tree_rejects_private_material_without_disclosing_contents(self):
        module=importlib.import_module('contentrium_cut.release_assets')
        with tempfile.TemporaryDirectory() as root:
            directory=Path(root)
            (directory/'bundle.json').write_text('{"appVersion":"0.1.0","bundleId":"public"}')
            module.assert_public_tree(directory)
            for name in ['contentrium-bootstrap.json','contentrium-panel-identity.json','install-location.json','paired-sessions.json','service-job-owners.json','apply-journal.json','launcher-transaction.json','launcher-ownership.json',
                         'contentrium-install-challenge.json','contentrium-install-receipt.json','install-preparation.json','integration-registration.json']:
                path=directory/name;path.write_text('{"secret":"do-not-disclose"}')
                with self.assertRaises(CutError) as error:module.assert_public_tree(directory)
                with self.assertRaises(CutError):module.assert_public_tree(path)
                self.assertNotIn('do-not-disclose',str(error.exception));path.unlink()
            (directory/'bundle.json').write_text('{"nested":{"privateBootstrap":{"secret":"do-not-disclose"}}}')
            with self.assertRaises(CutError):module.assert_public_tree(directory)
            with self.assertRaises(CutError):module.assert_public_tree(directory/'bundle.json')
            (directory/'bundle.json').write_text('{"appVersion":"0.1.0"}')
            module.assert_public_tree(directory/'bundle.json')
            with self.assertRaises(CutError):module.assert_public_tree(directory/'missing')
            history=directory/'launcher-history'/('a'*64)/'launcher-transaction.json'
            history.parent.mkdir(parents=True);history.write_text('{"schemaVersion":1}')
            with self.assertRaises(CutError):module.assert_public_tree(directory)
            with self.assertRaises(CutError):module.assert_public_tree(history)


if __name__=='__main__':unittest.main()
