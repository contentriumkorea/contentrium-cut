import copy
import unittest
import tempfile
from unittest.mock import patch
from contentrium_cut.audio import sync_sources
from contentrium_cut.coordinator import Coordinator
from contentrium_cut.contract import CutError
import test_coordinator as fixtures
import test_audio as media_fixtures

FPS={'num':30,'den':1}
def metadata(value,fps='30/1'):
    return {'streams':[{'codec_type':'audio','channels':2,'sample_rate':'48000','duration':'10'},{'codec_type':'video','avg_frame_rate':fps,'tags':{'timecode':value}}],'format':{'duration':'10'}}

class SyncMethodTests(unittest.TestCase):
    def sources(self,method):return [{'assetId':a,'path':a,'syncMethod':method} for a in ['a','b']]
    def invoke(self,sources,meta=None):
        with patch('contentrium_cut.audio._media_metadata',side_effect=lambda source,cancel=None: {'path':source['path'],'ffmpeg':'local','metadata':(meta or {})[source['assetId']],'size':10,'mtimeNs':1}):return sync_sources(sources,'a',FPS)
    def test_coordinator_scopes_channel_choices_and_confirmation_payload(self):
        with tempfile.TemporaryDirectory() as d:
            c=Coordinator(d);snap,_=fixtures.CoordinatorTests().sync_fixture();c.bind('panel',snap)
            options={'method':'manual','reference':'a','sourceSelections':[{'assetId':'a','streamIndex':1,'channelIndex':2},{'assetId':'b','streamIndex':0,'channelIndex':1}],'manualOffsets':{'b':{'confirmed':True,'offsetSeconds':'2.0'}}}
            payload=c.sync_payload('panel',options)
            self.assertEqual([(s['streamIndex'],s['channelIndex']) for s in payload['sources']],[(1,2),(0,1)])
            self.assertEqual(payload['sources'][1]['manualConfirmation'],options['manualOffsets']['b'])
            with self.assertRaises(CutError):c.sync_payload('panel',dict(options,sourceSelections=[{'assetId':'a','channelIndex':True},{'assetId':'b'}]))
    def test_manual_confirmation_and_distributed_point_disagreement(self):
        sources=self.sources('manual');sources[1]['manualConfirmation']={'confirmed':True,'offsetSeconds':'2.5','correspondences':[{'sourceSeconds':'0','referenceSeconds':'2.5'},{'sourceSeconds':'1','referenceSeconds':'3.5'}]}
        result=self.invoke(sources,{a:metadata('00:00:00:00') for a in ['a','b']})
        self.assertEqual(result['offsets'],{'a':0.,'b':2.5});self.assertEqual(result['sources']['b']['status'],'accepted')
        sources[1]['manualConfirmation']['correspondences'][-1]['referenceSeconds']='3.6'
        result=self.invoke(sources,{a:metadata('00:00:00:00') for a in ['a','b']})
        self.assertNotIn('b',result['offsets']);self.assertEqual(result['reviews'][0]['code'],'MANUAL_SYNC_DRIFT')
        sources[1]['manualConfirmation']['confirmed']=False
        self.assertEqual(self.invoke(sources,{a:metadata('00:00:00:00') for a in ['a','b']})['reviews'][0]['code'],'MANUAL_SYNC_CONFIRMATION_REQUIRED')
    def test_timecode_requires_matching_actual_tags_fps_clock_df_and_explicit_dates(self):
        sources=self.sources('timecode')
        for s in sources:s['timecodeConfirmation']={'confirmed':True,'clockId':'clock-1','date':'2026-10-05','fps':FPS,'dropFrame':False,'reset':False}
        result=self.invoke(sources,{'a':metadata('01:00:00:00'),'b':metadata('01:00:02:15')})
        self.assertEqual(result['offsets']['b'],2.5)
        for change,code in [({'clockId':'other'},'TIMECODE_CLOCK_MISMATCH'),({'fps':{'num':25,'den':1}},'TIMECODE_FPS_MISMATCH'),({'dropFrame':True},'TIMECODE_DROPFRAME_MISMATCH'),({'reset':True},'TIMECODE_RESET'),({'date':None},'TIMECODE_DATE_REQUIRED')]:
            candidate=copy.deepcopy(sources);candidate[1]['timecodeConfirmation'].update(change)
            result=self.invoke(candidate,{'a':metadata('01:00:00:00'),'b':metadata('01:00:02:15')})
            self.assertNotIn('b',result['offsets']);self.assertEqual(result['reviews'][0]['code'],code)
    def test_explicit_date_handles_midnight_and_skipped_dropframe_numbers_review(self):
        from contentrium_cut.sync_methods import timecode_seconds
        self.assertAlmostEqual(timecode_seconds('00:01:00;02',{'num':30000,'den':1001},True),60.06)
        with self.assertRaises(CutError):timecode_seconds('00:01:00;00',{'num':30000,'den':1001},True)
        sources=self.sources('timecode')
        for s in sources:s['timecodeConfirmation']={'confirmed':True,'clockId':'same','date':'2026-10-05','fps':FPS,'dropFrame':False,'reset':False}
        sources[1]['timecodeConfirmation']['date']='2026-10-06'
        result=self.invoke(sources,{'a':metadata('23:59:59:00'),'b':metadata('00:00:01:00')})
        self.assertEqual(result['offsets']['b'],2.)
        fractional={'num':30000,'den':1001}
        for source in sources:source['timecodeConfirmation']['fps']=fractional
        result=self.invoke(sources,{'a':metadata('23:59:59:00','30000/1001'),'b':metadata('00:00:01:00','30000/1001')})
        self.assertAlmostEqual(result['offsets']['b'],2.002,places=6)
    def test_actual_mov_metadata_and_selected_second_stream_stereo_channel(self):
        import numpy as np
        import subprocess
        from pathlib import Path
        from contentrium_cut.cache import validate_result
        media=media_fixtures.AudioTests();media.setUp();self.addCleanup(media.doCleanups)
        signal=media.signal(15);sources=[]
        for asset,start,tc in [('a',0,'01:00:00:00'),('b',2,'01:00:02:00')]:
            samples=signal[start*16000:];wav=media.source(asset+'-wav',np.column_stack((media.signal(len(samples)/16000),samples)))
            movie=Path(media.tmp.name)/(asset+'.mov')
            subprocess.run([media_fixtures.FFMPEG,'-v','error','-nostdin','-f','lavfi','-i','color=c=black:s=16x16:r=30','-f','lavfi','-i','anullsrc=r=16000:cl=mono','-i',wav['path'],'-t',str(len(samples)/16000),'-map','0:v','-map','1:a','-map','2:a','-c:v','mpeg4','-c:a','pcm_s16le','-timecode',tc,str(movie)],check=True,capture_output=True)
            sources.append({'assetId':asset,'path':str(movie),'ffmpeg':media_fixtures.FFMPEG,'streamIndex':1,'channelIndex':1})
        result=sync_sources(sources,'a',FPS);validate_result('sync',result)
        self.assertAlmostEqual(result['offsets']['b'],2.,delta=1/30)
        for source in sources:
            source['syncMethod']='timecode';source['timecodeConfirmation']={'confirmed':True,'clockId':'owned-test-clock','date':'2026-10-05','fps':FPS,'dropFrame':False,'reset':False}
        result=sync_sources(sources,'a',FPS);validate_result('sync',result)
        self.assertEqual(result['offsets']['b'],2.)
        native=media.audio._media_metadata
        def wrong_clock(source,cancel=None,settings=None):
            info=native(source,cancel,settings)
            if source['assetId']=='b':
                for stream in info['metadata']['streams']:
                    if 'timecode' in stream.get('tags',{}):stream['tags']['timecode']='01:00:08:00'
            return info
        with patch('contentrium_cut.audio._media_metadata',side_effect=wrong_clock):result=sync_sources(sources,'a',FPS)
        self.assertNotIn('b',result['offsets']);self.assertTrue(any(r['code']=='TIMECODE_AUDIO_CONFLICT' for r in result['reviews']));validate_result('sync',result)
