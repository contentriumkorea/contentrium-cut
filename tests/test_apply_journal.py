import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from contentrium_cut.contract import CutError


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        try: self.module = importlib.import_module('contentrium_cut.apply_journal')
        except ModuleNotFoundError: self.fail('Durable apply journal is missing')
        self.journal = self.module.ApplyJournal(Path(self.tmp.name))
        self.binding = dict(owner='owner', instanceId='panel', hostIdentity=dict(pid=1,createTime='2'),
                            epoch=0,appVersion='0.1.0',bundleId='bundle',protocolVersion=1,
                            projectRef='project',sequenceRef='source',snapshotHash='a'*64,
                            planHash='b'*64,kind='edit')

    def begin(self): return self.journal.begin(self.binding,'request',dict(planHash='b'*64))

    def test_duplicate_begin_permit_receipt_and_end_survive_restart(self):
        first=self.begin(); identity=first['applyId']
        self.assertTrue(first['execute']); self.assertFalse(self.begin()['execute'])
        self.assertTrue(self.journal.batch('owner',identity,0,1,'c'*64,None)['execute'])
        self.assertFalse(self.journal.batch('owner',identity,0,1,'c'*64,None)['execute'])
        self.journal.result('owner',identity,0,'result')
        self.journal.batch_end('owner',identity,0,1,{'transactionReturned':True})
        receipt=dict(planHash='b'*64,sourceSnapshotHash='a'*64,resultSnapshotHash='d'*64,
                     resultSequenceRef='result',sourceUnchanged=True,readback={'verified':True},saved=True)
        ended=self.journal.end('owner',identity,0,'completed',receipt)
        restored=self.module.ApplyJournal(Path(self.tmp.name))
        self.assertEqual(restored.end('owner',identity,0,'completed',receipt),ended)
        self.assertFalse(restored.status('owner')['blocked'])
        self.assertFalse(restored.begin(self.binding,'request',{})['execute'])

    def test_cancel_cannot_clear_intent_and_recovery_requires_original_host_exit(self):
        identity=self.begin()['applyId']; self.journal.batch('owner',identity,0,1,'c'*64,None)
        self.journal.end('owner',identity,0,'canceled',{'resultSequenceRef':None})
        self.assertTrue(self.journal.status('owner')['blocked'])
        with self.assertRaises(CutError) as error: self.journal.recover('owner','request',None,True,lambda host:False)
        self.assertEqual(error.exception.code,'APPLY_HOST_EXIT_REQUIRED')
        self.assertTrue(self.journal.recover('owner','request',None,True,lambda host:True)['resolved'])

    def test_failed_durable_write_never_returns_or_consumes_permit(self):
        identity=self.begin()['applyId']
        with patch.object(self.module,'atomic_json',side_effect=OSError('disk')):
            with self.assertRaises(OSError): self.journal.batch('owner',identity,0,1,'c'*64,None)
        self.assertTrue(self.journal.batch('owner',identity,0,1,'c'*64,None)['execute'])

    def test_restart_preserves_uncertainty_and_corrupt_evidence_fails_closed(self):
        identity=self.begin()['applyId']; self.journal.batch('owner',identity,0,1,'c'*64,None)
        restored=self.module.ApplyJournal(Path(self.tmp.name))
        self.assertTrue(restored.status('owner')['blocked'])
        with self.assertRaises(CutError): restored.batch('owner',identity,0,2,'d'*64,None)
        self.journal.path.write_text('{}')
        broken=self.module.ApplyJournal(Path(self.tmp.name))
        self.assertTrue(broken.status('new-owner')['blocked'])
        with self.assertRaises(CutError): broken.begin(self.binding,'new-request',{})

    def test_failed_begin_and_end_write_do_not_claim_authorization_or_success(self):
        with patch.object(self.module,'atomic_json',side_effect=OSError('disk')):
            with self.assertRaises(OSError):self.begin()
        self.assertFalse(self.journal.records)
        identity=self.begin()['applyId'];self.journal.batch('owner',identity,0,1,'c'*64,None)
        self.journal.result('owner',identity,0,'result');self.journal.batch_end('owner',identity,0,1,{'transactionReturned':True})
        receipt=dict(planHash='b'*64,sourceSnapshotHash='a'*64,resultSnapshotHash='d'*64,resultSequenceRef='result',sourceUnchanged=True,readback={'verified':True},saved=True)
        with patch.object(self.module,'atomic_json',side_effect=OSError('disk')):
            with self.assertRaises(OSError):self.journal.end('owner',identity,0,'completed',receipt)
        self.assertTrue(self.journal.status('owner')['blocked']);self.assertNotIn('terminal',self.journal.get('owner',identity))
        self.assertEqual(self.journal.end('owner',identity,0,'completed',receipt)['status'],'completed')

    def test_changed_duplicates_order_result_binding_and_own_unmutated_recovery(self):
        identity=self.begin()['applyId']
        with self.assertRaises(CutError):self.journal.begin(dict(self.binding,planHash='f'*64),'request',{})
        with self.assertRaises(CutError):self.journal.batch('owner',identity,0,2,'c'*64,None)
        self.journal.batch('owner',identity,0,1,'c'*64,None)
        with self.assertRaises(CutError):self.journal.batch('owner',identity,0,1,'d'*64,None)
        with self.assertRaises(CutError):self.journal.result('owner',identity,0,'source')
        with self.assertRaises(CutError):self.journal.batch('owner',identity,0,2,'d'*64,None)
        self.journal.result('owner',identity,0,'result')
        with self.assertRaises(CutError):self.journal.result('owner',identity,0,'other-result')
        self.journal.recover('owner','request',None,True,lambda identity:True)
        identity=self.journal.begin(self.binding,'authorized-only',{})['applyId']
        restarted=self.module.ApplyJournal(Path(self.tmp.name))
        self.assertTrue(restarted.recover('owner','authorized-only',identity,True,lambda identity:False)['resolved'])
