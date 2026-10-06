"""Held provider/host boundaries must not delay authenticated stop controls."""
import json
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
import test_service
import test_single_panel_service
import test_updater


class UpdateResponsivenessTests(unittest.TestCase):
    def case(self, kind):
        value=kind();value.setUp();self.addCleanup(value.doCleanups);return value

    def test_metadata_check_returns_promptly_coalesces_and_allows_start_from_cached_candidate(self):
        case=self.case(test_service.ServiceTests);external,candidate=case.signed_update()
        external.installer.host_closed=False
        entered,release,returned=threading.Event(),threading.Event(),threading.Event()
        get=external.network.get;calls=[];responses=[]
        def slow(*args,**kwargs):
            if '/releases/latest' in args[0]:calls.append(args[0]);entered.set();release.wait(3)
            return get(*args,**kwargs)
        external.network.get=slow
        def check():
            responses.append(case.request('/updates/check',{},case.token));returned.set()
        worker=threading.Thread(target=check);worker.start()
        try:
            self.assertTrue(entered.wait(1));self.assertTrue(returned.wait(.5),'metadata occupies the physical request')
            self.assertEqual(responses[0][0],200)
            self.assertEqual(case.request('/updates/check',{},case.token)[0],200)
            self.assertEqual(len(calls),1,'duplicate checks must share the in-flight lookup')
            status,value=case.request('/updates/start',dict(candidateId=candidate['candidateId'],manifestDigest=candidate['manifestDigest'],requestId='instant'),case.token)
            self.assertEqual(status,200,value);self.assertFalse(case.service.gate_open)
        finally:release.set();worker.join(4)

    def test_recheck_304_cannot_overwrite_an_update_that_started_while_network_waited(self):
        case=self.case(test_updater.UpdaterTests);manager=case.manager();candidate=manager.check()['candidate']
        entered,release=threading.Event(),threading.Event();get=case.network.get
        case.network.status=304
        def slow(*args,**kwargs):entered.set();release.wait(3);return get(*args,**kwargs)
        case.network.get=slow;worker=threading.Thread(target=manager.check);worker.start()
        try:
            self.assertTrue(entered.wait(1))
            state=manager.start(candidate['candidateId'],candidate['manifestDigest'],'instant')
            self.assertFalse(state['gateOpen']);before=manager.journal.read_bytes()
            release.set();worker.join(2)
            self.assertEqual(manager.journal.read_bytes(),before,'stale lookup wrote after the stop boundary')
        finally:release.set();worker.join(4)

    def test_model_revision_lookup_is_cancelable_and_does_not_hold_the_request_queue(self):
        case=self.case(test_single_panel_service.SinglePanelTests);case.heartbeat()
        entered,release,returned=threading.Event(),threading.Event(),threading.Event();responses=[]
        secret='hf_only_in_worker_memory'
        def metadata(token):self.assertEqual(token,secret);entered.set();release.wait(3);return 'a'*40
        def request():
            responses.append(case.signed_request(case.client,'POST','/models/community-1/revision',dict(token=secret,termsAccepted=True,epoch=0)));returned.set()
        with patch('contentrium_cut.model_setup.community_revision',side_effect=metadata):
            worker=threading.Thread(target=request);worker.start()
            try:
                self.assertTrue(entered.wait(1));self.assertTrue(returned.wait(.5),'model metadata occupies the physical request')
                status,value=responses[0];self.assertEqual(status,200,value);self.assertIn('continuation',value)
                self.assertEqual(case.heartbeat()[0],200)
                case.service.stop_all(1)
                key=value['continuation']['id'];path='/continuations/'+key
                self.assertEqual(case.signed_request(case.client,'GET',path,None)[1]['status'],'canceled')
                release.set();worker.join(2)
                self.assertNotEqual(case.signed_request(case.client,'POST',path+'/take',{'epoch':0})[0],200)
                for file in Path(case.tmp.name).rglob('*.json'):self.assertNotIn(secret,file.read_text())
            finally:release.set();worker.join(4)

    def test_decoder_child_has_no_console_window(self):
        import subprocess
        from contentrium_cut.audio import _run
        child=Mock();child.poll.return_value=0;child.returncode=0
        with patch('contentrium_cut.audio.subprocess.Popen',return_value=child) as spawn:
            self.assertEqual(_run(['owned-ffmpeg','-version']),b'')
        self.assertEqual(spawn.call_args.kwargs.get('creationflags'),getattr(subprocess,'CREATE_NO_WINDOW',0))
