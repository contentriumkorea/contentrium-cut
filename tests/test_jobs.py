import tempfile
import time
import unittest
import threading
from contextlib import contextmanager
from unittest.mock import patch
from pathlib import Path

from contentrium_cut.contract import CutError
from contentrium_cut.jobs import JobManager

class JobsTest(unittest.TestCase):
    def test_update_during_cache_staging_cannot_promote_result(self):
        with tempfile.TemporaryDirectory() as directory:
            jobs=JobManager(Path(directory));jobs.jobs['job']={'jobId':'job','status':'running','epoch':0}
            from contentrium_cut.jobs import atomic_json
            staged=threading.Event();resume=threading.Event();outcome=[]
            def paused(path,value):
                if str(path).endswith('.pending.json'):staged.set();resume.wait(2)
                atomic_json(path,value)
            with patch('contentrium_cut.jobs.atomic_json',side_effect=paused):
                worker=threading.Thread(target=lambda:outcome.append(jobs.commit_result('job',0,{'value':42},'cachekey')));worker.start()
                self.assertTrue(staged.wait(1));jobs.stop_all(1);resume.set();worker.join(2)
            self.assertEqual(outcome,[False]);self.assertFalse((Path(directory)/'cache'/'cachekey.json').exists());jobs.close()
    def test_rejected_success_is_canceled_and_monitor_survives(self):
        with tempfile.TemporaryDirectory() as directory:
            jobs=JobManager(Path(directory),admit=lambda epoch:(_ for _ in ()).throw(CutError('UPDATE_IN_PROGRESS','Stopped')))
            state={'jobId':'job','status':'running','epoch':0};jobs.jobs['job']=state
            jobs._receive('job',{'cancel':threading.Event()}, {'ok':True,'value':42})
            self.assertEqual(state['status'],'canceled');self.assertTrue(jobs.monitor.is_alive());jobs.close()
    def test_update_stops_admission_before_worker_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            jobs=JobManager(Path(directory))
            jobs.stop_all(7)
            with self.assertRaises(CutError) as error:
                jobs.submit('sync', {})
            self.assertEqual(error.exception.code,'UPDATE_IN_PROGRESS')
            self.assertTrue(jobs.quiescent())
            jobs.close()

    def test_closed_epoch_cannot_commit_completed_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            jobs=JobManager(Path(directory))
            jobs.stop_all(2)
            self.assertFalse(jobs.commit_result('old', 1, {'value':42}, 'cachekey'))
            self.assertFalse((Path(directory)/'cache'/'cachekey.json').exists())
            jobs.close()

    def test_restarted_active_job_is_interrupted_not_resumed(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'jobs').mkdir()
            (root/'jobs'/'old.json').write_text('{"jobId":"old","status":"running","kind":"sync"}')
            jobs=JobManager(root)
            self.assertEqual(jobs.get('old')['status'],'interrupted')
            self.assertTrue(jobs.quiescent())
            jobs.close()

    def test_unknown_worker_kind_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            jobs=JobManager(Path(directory))
            with self.assertRaises(CutError) as error:jobs.submit('shell', {'command':'whoami'})
            self.assertEqual(error.exception.code,'INVALID_JOB')
            jobs.close()
