import copy
import tempfile
import unittest
from collections import namedtuple
from pathlib import Path
from unittest.mock import patch
from contentrium_cut.contract import CutError, canonical_hash, TICKS_PER_SECOND
from contentrium_cut.coordinator import Coordinator

class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        usage=namedtuple('Usage','total used free')(100*1024**3,20*1024**3,80*1024**3)
        for target,value in [('contentrium_cut.resource.available_memory',8*1024**3),('contentrium_cut.resource.shutil.disk_usage',usage)]:
            mock=patch(target,return_value=value);mock.start();self.addCleanup(mock.stop)

    def test_prepare_is_pure_and_publish_rejects_identical_snapshot_rebind(self):
        from contentrium_cut.coordinator import prepare_plan
        from test_policy import fixture,speech
        snapshot,analysis,mapping,policy=fixture();analysis['intervals']=[speech(0,200,'A')]
        snapshot['snapshotHash']=canonical_hash({k:v for k,v in snapshot.items() if k!='snapshotHash'})
        with tempfile.TemporaryDirectory() as directory:
            coordinator=Coordinator(directory);coordinator.bind('panel',snapshot)
            request=coordinator.capture_plan('panel',analysis,mapping,policy)
            before=copy.deepcopy(request);plan=prepare_plan(request)
            self.assertEqual(request,before);self.assertEqual(coordinator.session('panel')['plans'],{})
            coordinator.bind('panel',snapshot)
            with self.assertRaises(CutError) as error:coordinator.publish_plan('panel',request,plan)
            self.assertEqual(error.exception.code,'PLAN_SCOPE');self.assertEqual(coordinator.session('panel')['plans'],{})
            self.assertEqual(coordinator.plan('panel',analysis,mapping,policy),plan)

    def sync_fixture(self):
        snapshot=self.fixture();snapshot['clips'][0]['trackRef']='audio-a'
        snapshot['clips'].append({'instanceKey':'mic-b','assetId':'b','trackRef':'audio-b','mediaType':'audio','startTicks':str(7*TICKS_PER_SECOND),'endTicks':str(10*TICKS_PER_SECOND),'inTicks':'0','outTicks':str(3*TICKS_PER_SECOND),'speed':1,'disabled':False})
        snapshot['sources'].append({'assetId':'b','canonicalPath':__file__,'offline':False})
        snapshot['tracks']=[{'trackRef':'audio-a','mediaType':'audio'},{'trackRef':'audio-b','mediaType':'audio'}]
        snapshot.pop('snapshotHash');snapshot['snapshotHash']=canonical_hash(snapshot)
        result={'schemaVersion':1,'referenceAssetId':'a','offsets':{'a':0.0,'b':2.0},'sources':{'a':{'status':'accepted'},'b':{'status':'accepted'}},'reviews':[]}
        return snapshot,result
    def sync_plan(self,snapshot,result,selection=None):
        with tempfile.TemporaryDirectory() as directory:
            coordinator=Coordinator(directory);coordinator.bind('panel',snapshot)
            return coordinator.sync_plan('panel',result,selection or ['mic','mic-b'])
    def test_sync_plan_is_deterministic_scoped_and_stored_as_generic_reviewed_plan(self):
        snapshot,result=self.sync_fixture();before=copy.deepcopy((snapshot,result))
        with tempfile.TemporaryDirectory() as directory:
            coordinator=Coordinator(directory);coordinator.bind('panel',snapshot)
            plan=coordinator.sync_plan('panel',result,['mic-b','mic'])
            self.assertEqual(plan,coordinator.sync_plan('panel',result,['mic','mic-b']))
            self.assertEqual(plan['anchorReferenceTicks'],str(TICKS_PER_SECOND))
            self.assertEqual(plan['selectedClipInstanceKeys'],['mic','mic-b'])
            self.assertEqual(plan['planHash'],plan['syncPlanHash'])
            self.assertEqual(plan['syncPlanHash'],canonical_hash({k:v for k,v in plan.items() if k not in ['planHash','syncPlanHash']}))
            self.assertEqual(plan,coordinator.require_plan('panel',plan['planHash'],snapshot['snapshotHash']))
            placements={item['instanceKey']:item for item in plan['placements']}
            self.assertEqual(placements['mic']['startTicks'],snapshot['clips'][0]['startTicks'])
            self.assertEqual(placements['mic-b']['startTicks'],str(3*TICKS_PER_SECOND))
            self.assertEqual(placements['mic-b']['endTicks'],str(6*TICKS_PER_SECOND))
        self.assertEqual((snapshot,result),before)
    def test_sync_plan_requires_selected_reference_unique_known_clip_instances(self):
        for selection in [[],['unknown'],['mic','mic'],['mic-b']]:
            with self.subTest(selection=selection):
                snapshot,result=self.sync_fixture()
                with tempfile.TemporaryDirectory() as directory:
                    coordinator=Coordinator(directory);coordinator.bind('panel',snapshot)
                    with self.assertRaises(CutError):coordinator.sync_plan('panel',result,selection)
    def test_sync_plan_blocks_unresolved_and_nonfinite_selected_offsets(self):
        for value in [None,float('nan'),float('inf'),True,'2.0']:
            with self.subTest(value=value):
                snapshot,result=self.sync_fixture();result['offsets']['b']=value
                with self.assertRaises(CutError):self.sync_plan(snapshot,result)
        snapshot,result=self.sync_fixture();result['sources']['b']['status']='review'
        with self.assertRaises(CutError):self.sync_plan(snapshot,result)
    def test_sync_plan_requires_consistent_anchor_for_every_selected_reference_instance(self):
        snapshot,result=self.sync_fixture();other=copy.deepcopy(snapshot['clips'][0]);other.update(instanceKey='other-ref',trackRef='other-track',startTicks=str(4*TICKS_PER_SECOND),endTicks=str(14*TICKS_PER_SECOND));snapshot['clips'].append(other)
        snapshot.pop('snapshotHash');snapshot['snapshotHash']=canonical_hash(snapshot)
        with self.assertRaises(CutError) as error:self.sync_plan(snapshot,result,['mic','mic-b','other-ref'])
        self.assertEqual(error.exception.code,'SYNC_REFERENCE_AMBIGUOUS')
    def test_sync_plan_blocks_disabled_speed_and_inconsistent_source_time(self):
        for change in [{'disabled':True},{'speed':2},{'speed':True},{'outTicks':str(4*TICKS_PER_SECOND)},{'supportFlags':{'timeMappingSupported':False}}]:
            with self.subTest(change=change):
                snapshot,result=self.sync_fixture();snapshot['clips'][1].update(change)
                snapshot.pop('snapshotHash');snapshot['snapshotHash']=canonical_hash(snapshot)
                with self.assertRaises(CutError):self.sync_plan(snapshot,result)
    def test_sync_plan_rejects_negative_target_and_unselected_track_collision(self):
        snapshot,result=self.sync_fixture();result['offsets']['b']=-2
        with self.assertRaises(CutError) as error:self.sync_plan(snapshot,result)
        self.assertEqual(error.exception.code,'SYNC_NEGATIVE_POSITION')
        snapshot,result=self.sync_fixture();other=copy.deepcopy(snapshot['clips'][1]);other.update(instanceKey='protected',startTicks=str(4*TICKS_PER_SECOND),endTicks=str(7*TICKS_PER_SECOND));snapshot['clips'].append(other)
        snapshot.pop('snapshotHash');snapshot['snapshotHash']=canonical_hash(snapshot)
        with self.assertRaises(CutError) as error:self.sync_plan(snapshot,result)
        self.assertEqual(error.exception.code,'SYNC_COLLISION')
    def test_sync_plan_checks_selected_collisions_and_allows_touching_boundaries(self):
        snapshot,result=self.sync_fixture();snapshot['clips'][1]['trackRef']='audio-a'
        snapshot.pop('snapshotHash');snapshot['snapshotHash']=canonical_hash(snapshot)
        with self.assertRaises(CutError) as error:self.sync_plan(snapshot,result)
        self.assertEqual(error.exception.code,'SYNC_COLLISION')
        result['offsets']['b']=11
        plan=self.sync_plan(snapshot,result)
        self.assertEqual(plan['placements'][1]['startTicks'],str(12*TICKS_PER_SECOND))
    def test_sync_plan_rounds_moved_start_to_sequence_frame_and_preserves_exact_duration(self):
        snapshot,result=self.sync_fixture();result['offsets']['b']=1/60
        plan=self.sync_plan(snapshot,result)
        moved=next(item for item in plan['placements'] if item['instanceKey']=='mic-b')
        self.assertEqual(moved['startTicks'],'262483200000')
        self.assertEqual(int(moved['endTicks'])-int(moved['startTicks']),3*TICKS_PER_SECOND)
    def test_sync_plan_serializes_offsets_as_portable_exact_decimal_strings(self):
        snapshot,result=self.sync_fixture();result['offsets']['b']=1e-6
        plan=self.sync_plan(snapshot,result)
        self.assertEqual(plan['offsets'],{'a':'0.0','b':'1e-06'})
        self.assertEqual(plan['syncPlanHash'],canonical_hash({k:v for k,v in plan.items() if k not in ['planHash','syncPlanHash']}))
    def fixture(self):
        s={'schemaVersion':1,'projectRef':'project-1','sequenceRef':'seq-1','fps':{'num':30,'den':1},'range':{'startFrame':0,'endFrame':300},'tracks':[],'clips':[{'instanceKey':'mic','assetId':'a','mediaType':'audio','startTicks':'508032000000','endTicks':'3048192000000','inTicks':'254016000000','outTicks':'2794176000000','speed':1,'disabled':False}], 'sources':[{'assetId':'a','canonicalPath':__file__,'offline':False}],'supportFlags':{}}
        s['snapshotHash']=canonical_hash(s);return s
    def test_snapshot_hash_cannot_be_forged(self):
        with tempfile.TemporaryDirectory() as d:
            c=Coordinator(d);s=self.fixture();s['sequenceRef']='changed'
            with self.assertRaises(CutError) as e:c.bind('panel',s)
            self.assertEqual(e.exception.code,'SNAPSHOT_HASH_MISMATCH')
    def test_analysis_scalar_active_validation(self):
        with tempfile.TemporaryDirectory() as d:
            c=Coordinator(d);c.bind('panel',self.fixture())
            for mode,key,invalid in [('separate','vadThreshold',['',None,True,float('nan'),float('inf'),10**1000,0,.049,.951]),('mixed','speakerCount',['',True,float('nan'),float('inf'),10**1000,0,1.5,27])]:
                for value in invalid:
                    with self.subTest(mode=mode,value=value):
                        with self.assertRaises(CutError) as error:c.audio_payload('panel',{'mode':mode,'microphones':[{'instanceKey':'mic','speakerId':'A'}],key:value})
                        self.assertEqual(error.exception.code,'INVALID_AUDIO_INPUT')

    def test_analysis_scalar_inactive_defaults_boundaries_and_auto_count(self):
        with tempfile.TemporaryDirectory() as d:
            c=Coordinator(d);c.bind('panel',self.fixture())
            for value in [.05,.95]:
                settings=c.audio_payload('panel',{'mode':'separate','microphones':[{'instanceKey':'mic','speakerId':'A'}],'vadThreshold':value,'speakerCount':''})['settings']
                self.assertEqual(settings['vadThreshold'],value);self.assertIsNone(settings['speakerCount'])
            for value in [None,1,26]:
                settings=c.audio_payload('panel',{'mode':'mixed','microphones':[{'instanceKey':'mic'}],'speakerCount':value,'vadThreshold':''})['settings']
                self.assertEqual(settings['speakerCount'],value);self.assertEqual(settings['vadThreshold'],.5)

    def test_analysis_uses_selected_instance_start_minus_source_in(self):
        with tempfile.TemporaryDirectory() as d:
            c=Coordinator(d);s=self.fixture();c.bind('panel',s)
            payload=c.audio_payload('panel',{'mode':'separate','microphones':[{'instanceKey':'mic','speakerId':'A','channelIndex':0}]})
            self.assertEqual(payload['settings']['offsets'],{'a':1.0})
            self.assertEqual(payload['sources'][0]['path'],str(Path(__file__).resolve()))
            self.assertEqual(payload['sources'][0]['sourceStartSeconds'],1.0)
            self.assertEqual(payload['sources'][0]['durationSeconds'],10.0)
            self.assertEqual(payload['sources'][0]['sequenceStartSeconds'],2.0)
    def test_client_cannot_supply_unimported_file(self):
        with tempfile.TemporaryDirectory() as d:
            c=Coordinator(d);c.bind('panel',self.fixture())
            with self.assertRaises(CutError) as e:c.audio_payload('panel',{'mode':'mixed','microphones':[{'instanceKey':'unknown','path':'C:/private.wav'}]})
            self.assertEqual(e.exception.code,'SOURCE_SCOPE')
    def test_same_source_samples_cannot_be_assigned_to_different_people(self):
        with tempfile.TemporaryDirectory() as d:
            c=Coordinator(d);s=self.fixture();b=copy.deepcopy(s['clips'][0]);b.update(instanceKey='mic2',startTicks='1016064000000');s['clips'].append(b);s.pop('snapshotHash');s['snapshotHash']=canonical_hash(s);c.bind('panel',s)
            with self.assertRaises(CutError) as e:c.audio_payload('panel',{'mode':'separate','microphones':[{'instanceKey':'mic','speakerId':'A'},{'instanceKey':'mic2','speakerId':'B'}]})
            self.assertEqual(e.exception.code,'SOURCE_ASSIGNMENT_CONFLICT')
