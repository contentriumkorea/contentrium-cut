import copy
import tempfile
import unittest
from contentrium_cut.coordinator import Coordinator
from contentrium_cut.contract import CutError
import test_coordinator as fixtures

class CorrectionTests(unittest.TestCase):
    def raw(self):
        return {'schemaVersion':1,'modelRevision':'fixture','sessionSpeakerIds':['A','B'],'intervals':[{'startFrame':0,'endFrame':60,'speakers':['A'],'unknown':False},{'startFrame':60,'endFrame':120,'speakers':['A','B'],'unknown':False}], 'validAudioRanges':[{'assetId':'a','startFrame':0,'endFrame':120,'startSample':0,'endSample':64000,'sampleRate':16000}], 'reviews':[],'evidence':{}}
    def setup(self,d):
        c=Coordinator(d);c.bind('panel',fixtures.CoordinatorTests().fixture());return c
    def test_name_merge_reassign_undo_durable_raw_immutable_and_revision_conflicts(self):
        with tempfile.TemporaryDirectory() as d:
            c=self.setup(d);raw=self.raw();before=copy.deepcopy(raw);state=c.register_analysis('panel','job-1',raw);identity=state['analysisId']
            state=c.correct_analysis('panel',identity,0,{'type':'name','speakerId':'A','name':'진행자'},'req1')
            self.assertEqual(state['names']['A'],'진행자');self.assertEqual(state['revision'],1)
            self.assertEqual(state,c.correct_analysis('panel',identity,0,{'type':'name','speakerId':'A','name':'진행자'},'req1'))
            with self.assertRaises(CutError):c.correct_analysis('panel',identity,0,{'type':'merge','speakerIds':['A','B'],'targetSpeakerId':'A'})
            state=c.correct_analysis('panel',identity,1,{'type':'merge','speakerIds':['A','B'],'targetSpeakerId':'A'})
            self.assertEqual(state['analysis']['sessionSpeakerIds'],['A']);self.assertEqual(state['analysis']['intervals'][-1]['speakers'],['A'])
            state=c.correct_analysis('panel',identity,2,{'type':'reassign','startFrame':30,'endFrame':90,'speakers':[],'unknown':True})
            self.assertEqual(state['analysis']['intervals'][1]['startFrame'],30);self.assertTrue(state['analysis']['intervals'][1]['unknown'])
            state=c.correct_analysis('panel',identity,3,{'type':'undo'})
            self.assertEqual(state['analysis']['intervals'][-1]['speakers'],['A'])
            reopened=self.setup(d).analysis_state('panel',identity)
            self.assertEqual(reopened,state);self.assertEqual(state['raw'],before);self.assertEqual(raw,before)
    def test_reassignment_cannot_fill_missing_source_gap_and_project_scope_isolation(self):
        with tempfile.TemporaryDirectory() as d:
            c=self.setup(d);state=c.register_analysis('panel','job',self.raw());identity=state['analysisId']
            with self.assertRaises(CutError):c.correct_analysis('panel',identity,0,{'type':'reassign','startFrame':90,'endFrame':180,'speakers':['A'],'unknown':False})
            snap=fixtures.CoordinatorTests().fixture();snap['projectRef']='different';snap.pop('snapshotHash')
            from contentrium_cut.contract import canonical_hash
            snap['snapshotHash']=canonical_hash(snap);c.bind('panel',snap)
            with self.assertRaises(CutError):c.analysis_state('panel',identity)
    def test_unresolved_chunk_link_requires_explicit_candidate_and_rebuilds_overlap(self):
        raw=self.raw();raw['evidence']['ownedTurns']=[{'startFrame':0,'endFrame':120,'sessionSpeakerId':'A','candidateSpeakerId':None},{'startFrame':60,'endFrame':120,'sessionSpeakerId':None,'candidateSpeakerId':'U1'}]
        raw['intervals'][-1].update(speakers=['A'],unknown=True)
        with tempfile.TemporaryDirectory() as d:
            c=self.setup(d);state=c.register_analysis('panel','job',raw)
            state=c.correct_analysis('panel',state['analysisId'],0,{'type':'link','candidateSpeakerId':'U1','sessionSpeakerId':'B'})
            self.assertEqual(state['analysis']['intervals'][-1]['speakers'],['A','B']);self.assertFalse(state['analysis']['intervals'][-1]['unknown'])
    def test_unlinked_chunk_identity_cannot_escape_required_link_via_unknown_hold_plan(self):
        raw=self.raw();raw['evidence']['ownedTurns']=[{'startFrame':60,'endFrame':120,'sessionSpeakerId':None,'candidateSpeakerId':'U1'}]
        with tempfile.TemporaryDirectory() as d:
            c=self.setup(d)
            with self.assertRaises(CutError) as error:c.plan('panel',raw,{}, {})
            self.assertEqual(error.exception.code,'SPEAKER_LINK_REQUIRED')
    def test_mixed_identity_can_be_split_to_an_explicit_new_person_and_bad_shapes_are_typed(self):
        with tempfile.TemporaryDirectory() as d:
            c=self.setup(d);state=c.register_analysis('panel','job',self.raw());identity=state['analysisId']
            state=c.correct_analysis('panel',identity,0,{'type':'reassign','startFrame':30,'endFrame':60,'speakers':['C'],'newSpeakerIds':['C'],'unknown':False})
            self.assertIn('C',state['analysis']['sessionSpeakerIds']);self.assertEqual(state['analysis']['intervals'][1]['speakers'],['C'])
            for operation in [None,[],{'type':'name','speakerId':{}},{'type':'merge','speakerIds':[{},'A'],'targetSpeakerId':'A'}]:
                with self.assertRaises(CutError) as error:c.correct_analysis('panel',identity,1,operation)
                self.assertEqual(error.exception.code,'INVALID_CORRECTION')
    def test_saved_history_digest_and_raw_range_corruption_are_fail_closed(self):
        import json
        from contentrium_cut.corrections import artifact_hash
        with tempfile.TemporaryDirectory() as d:
            c=self.setup(d);state=c.register_analysis('panel','job',self.raw());identity=state['analysisId'];path=c._analysis_path('panel',identity)
            value=json.loads(path.read_text(encoding='utf-8'));value['raw']['intervals'][0]['endFrame']=1000
            path.write_text(json.dumps(value),encoding='utf-8')
            with self.assertRaises(CutError) as error:c.analysis_state('panel',identity)
            self.assertEqual(error.exception.code,'ANALYSIS_SCOPE')
            value=artifact_hash(value);path.write_text(json.dumps(value),encoding='utf-8')
            with self.assertRaises(CutError):c.analysis_state('panel',identity)
    def test_correction_during_plan_computation_cannot_reintroduce_stale_plan(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d:
            c=self.setup(d);state=c.register_analysis('panel','job',self.raw());identity=state['analysisId']
            def compute(*args):
                c.correct_analysis('panel',identity,0,{'type':'name','speakerId':'A','name':'host'})
                return {'planHash':'fake','snapshotHash':state['snapshotHash']}
            with patch('contentrium_cut.coordinator.plan_edit',side_effect=compute):
                with self.assertRaises(CutError) as error:c.plan('panel',state['analysis'],{}, {})
            self.assertEqual(error.exception.code,'CORRECTION_REVISION_CONFLICT');self.assertNotIn('fake',c.session('panel')['plans'])
