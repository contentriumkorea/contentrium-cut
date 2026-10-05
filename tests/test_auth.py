import tempfile
import unittest
from pathlib import Path
from contentrium_cut.auth import AuthManager
from contentrium_cut.contract import CutError

class AuthTest(unittest.TestCase):
    def test_pairing_code_is_single_use_and_token_has_project_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            auth=AuthManager(Path(directory),clock=lambda:10)
            code=auth.issue_code();session=auth.pair(code,'panel-instance')
            self.assertEqual(auth.authenticate(session['token'])['instanceId'],'panel-instance')
            with self.assertRaises(CutError):auth.pair(code,'second-instance')
            auth.bind_project(session['token'],'project-A')
            auth.require_project(session['token'],'project-A')
            with self.assertRaises(CutError):auth.require_project(session['token'],'project-B')
    def test_expired_code_and_web_origin_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            now=[10];auth=AuthManager(Path(directory),clock=lambda:now[0]);code=auth.issue_code();now[0]=131
            with self.assertRaises(CutError):auth.pair(code,'panel')
            with self.assertRaises(CutError):auth.validate_origin('https://attacker.example')
    def test_unpaired_token_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            auth=AuthManager(Path(directory))
            with self.assertRaises(CutError):auth.authenticate('arbitrary')
