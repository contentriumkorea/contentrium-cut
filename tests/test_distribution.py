import importlib
from pathlib import Path
import tempfile
import unittest
import zipfile


class DistributionTests(unittest.TestCase):
    def test_extractable_contains_runtime_panel_and_honest_setup_instructions(self):
        try:module=importlib.import_module('tools.distribution')
        except ModuleNotFoundError:self.fail('Separate extractable distribution is not implemented')
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory)
            with zipfile.ZipFile(out/'Contentrium-CUT-Windows-x64.zip','w') as archive:
                archive.writestr('Contentrium CUT.exe',b'MZ runtime');archive.writestr('_internal/config.json','{}')
            (out/'Contentrium-CUT.ccx').write_bytes(b'panel')
            result=module.build_portable(out,'0.1.108')
            with zipfile.ZipFile(result) as archive:
                self.assertEqual(archive.read('Contentrium CUT.exe'),b'MZ runtime')
                self.assertEqual(archive.read('Premiere/Contentrium-CUT.ccx'),b'panel')
                guide=archive.read('README.txt').decode('utf-8-sig')
                self.assertIn('패널 등록',guide);self.assertIn('완전 무설치',guide);self.assertIn('0.1.108',guide)
            before=result.read_bytes()
            with self.assertRaises(RuntimeError):module.build_portable(out,'0.1.108')
            self.assertEqual(result.read_bytes(),before)

    def test_extractable_rejects_path_traversal(self):
        try:module=importlib.import_module('tools.distribution')
        except ModuleNotFoundError:self.fail('Separate extractable distribution is not implemented')
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory)
            with zipfile.ZipFile(out/'Contentrium-CUT-Windows-x64.zip','w') as archive:archive.writestr('../escape.exe',b'MZ')
            (out/'Contentrium-CUT.ccx').write_bytes(b'panel')
            with self.assertRaises(Exception):module.build_portable(out,'0.1.108')
            self.assertFalse((out/'Contentrium-CUT-Portable-Windows-x64.zip').exists())

    def test_missing_panel_never_leaves_publishable_final_zip(self):
        try:module=importlib.import_module('tools.distribution')
        except ModuleNotFoundError:self.fail('Separate extractable distribution is not implemented')
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory)
            with zipfile.ZipFile(out/'Contentrium-CUT-Windows-x64.zip','w') as archive:archive.writestr('Contentrium CUT.exe',b'MZ')
            with self.assertRaises(FileNotFoundError):module.build_portable(out,'0.1.108')
            self.assertFalse((out/'Contentrium-CUT-Portable-Windows-x64.zip').exists())
            (out/'Contentrium-CUT.ccx').write_bytes(b'panel')
            self.assertTrue(module.build_portable(out,'0.1.108').is_file())


if __name__=='__main__':unittest.main()
