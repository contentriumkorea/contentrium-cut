"""Project-scoped job inputs and deterministic plans for paired UXP panels."""
import copy
import json
import math
from fractions import Fraction
from pathlib import Path
import threading
from .contract import CutError, canonical_hash, tick_int, ticks_frame, frame_ticks, TICKS_PER_SECOND
from .jobs import atomic_json
from .models import ModelManager
from .policy import plan_edit

class Coordinator:
    def __init__(self,root):
        self.root=Path(root);self.sessions={};self.lock=threading.RLock()
    def bind(self,instance,snapshot):
        value=copy.deepcopy(snapshot);claimed=value.pop('snapshotHash',None)
        if canonical_hash(value)!=claimed:raise CutError('SNAPSHOT_HASH_MISMATCH','Premiere snapshot identity does not match its contents.')
        if value.get('schemaVersion')!=1 or not value.get('projectRef') or not value.get('sequenceRef'):raise CutError('INVALID_SNAPSHOT','A Premiere project and sequence are required.')
        value['snapshotHash']=claimed
        with self.lock:
            self.sessions[instance]={'snapshot':value,'plans':{},'jobs':set()}
        return {'snapshotHash':claimed}
    def session(self,instance):
        with self.lock:
            if instance not in self.sessions:raise CutError('PROJECT_REQUIRED','Connect the current Premiere sequence first.')
            return self.sessions[instance]
    def audio_payload(self,instance,options):
        snap=self.session(instance)['snapshot'];clips={c['instanceKey']:c for c in snap['clips']};assets={s['assetId']:s for s in snap['sources']}
        mode=options.get('mode');microphones=options.get('microphones',[])
        if mode not in {'separate','mixed'} or not microphones:raise CutError('INVALID_AUDIO_INPUT','Select recording mode and microphone clips.')
        sources={};offsets={};channels=[]
        for selected in microphones:
            clip=clips.get(selected.get('instanceKey'))
            if not clip:raise CutError('SOURCE_SCOPE','Microphone is not in the connected Premiere sequence.')
            asset=assets.get(clip['assetId'])
            if not asset or asset.get('offline'):raise CutError('MISSING_AUDIO','Selected microphone media is offline.')
            if clip.get('speed')!=1 or clip.get('disabled'):raise CutError('TIME_MAPPING_UNSUPPORTED','Use an enabled, normal-speed microphone clip.')
            key=clip['assetId'];offset=(tick_int(clip['startTicks'])-tick_int(clip['inTicks']))/TICKS_PER_SECOND
            if key in offsets and offsets[key]!=offset:raise CutError('INSTANCE_PROCESSING_REQUIRED','Select one aligned microphone instance per source file.')
            offsets[key]=offset
            source={'assetId':key,'path':str(Path(asset['canonicalPath']).resolve(strict=True)),'streamIndex':selected.get('streamIndex',0)}
            source.update(sourceStartSeconds=tick_int(clip['inTicks'])/TICKS_PER_SECOND,durationSeconds=(tick_int(clip['endTicks'])-tick_int(clip['startTicks']))/TICKS_PER_SECOND,sequenceStartSeconds=tick_int(clip['startTicks'])/TICKS_PER_SECOND)
            if 'channelIndex' in selected:source['channelIndex']=selected['channelIndex']
            sources[key]=source
            if mode=='separate':channels.append({'assetId':key,'speakerId':selected.get('speakerId'),'channelIndex':selected.get('channelIndex',0)})
        settings={'fps':snap['fps'],'range':snap['range'],'offsets':offsets,'channels':channels,'calibration':options.get('calibration',[]),'modelRoot':str(self.root/'models'),'speakerCount':options.get('speakerCount'),'vadThreshold':float(options.get('vadThreshold',.5)),'bleedTolerance':float(options.get('bleedTolerance',1.8))}
        ffmpeg=self.root/'app'/'ffmpeg'/'ffmpeg.exe'
        if ffmpeg.is_file():settings['ffmpeg']=str(ffmpeg)
        model=ModelManager(self.root/'models').state('silero' if mode=='separate' else 'community-1')
        settings['modelRevision']=model.get('revision');settings['modelManifestHash']=canonical_hash(model.get('manifest',{}))
        return {'mode':mode,'sources':list(sources.values()),'settings':settings}
    def sync_payload(self,instance,options):
        snap=self.session(instance)['snapshot'];assets={a['assetId']:a for a in snap['sources']};ids=options.get('assetIds',[])
        if len(ids)<2 or len(set(ids))!=len(ids) or any(i not in assets for i in ids):raise CutError('SOURCE_SCOPE','Select at least two imported sources for synchronization.')
        sources=[{'assetId':i,'path':assets[i]['canonicalPath'],'channelIndex':0} for i in ids]
        ffmpeg=self.root/'app'/'ffmpeg'/'ffmpeg.exe'
        if ffmpeg.is_file():
            for source in sources:source['ffmpeg']=str(ffmpeg)
        return {'sources':sources,'reference':options.get('reference',ids[0]),'fps':snap['fps']}
    def plan(self,instance,analysis,mapping,policy):
        session=self.session(instance);result=plan_edit(session['snapshot'],analysis,mapping,policy)
        with self.lock:session['plans'][result['planHash']]=result
        return result
    def sync_plan(self,instance,result,selected_clip_instance_keys):
        session=self.session(instance);snapshot=session['snapshot']
        if (not isinstance(selected_clip_instance_keys,list) or not selected_clip_instance_keys or
                any(not isinstance(key,str) or not key for key in selected_clip_instance_keys) or
                len(set(selected_clip_instance_keys))!=len(selected_clip_instance_keys)):
            raise CutError('SOURCE_SCOPE','Select unique clips from the connected sequence.')
        clips={clip['instanceKey']:clip for clip in snapshot['clips']}
        assets={source['assetId']:source for source in snapshot['sources']}
        keys=sorted(selected_clip_instance_keys)
        if len(clips)!=len(snapshot['clips']) or any(key not in clips for key in keys):
            raise CutError('SOURCE_SCOPE','Selected clip identity is outside the connected sequence.')
        if not isinstance(result,dict) or result.get('schemaVersion')!=1:
            raise CutError('SYNC_UNRESOLVED','A verified source synchronization result is required.')
        reference=result.get('referenceAssetId');offsets=result.get('offsets');evidence=result.get('sources')
        if not isinstance(reference,str) or not isinstance(offsets,dict) or not isinstance(evidence,dict):
            raise CutError('SYNC_UNRESOLVED','Synchronization source evidence is incomplete.')
        selected=[clips[key] for key in keys];selected_offsets={};anchors=set()
        for clip in selected:
            asset=clip['assetId'];offset=offsets.get(asset);source=assets.get(asset)
            if (not source or source.get('offline') or not isinstance(evidence.get(asset),dict) or
                    evidence[asset].get('status')!='accepted' or isinstance(offset,bool) or
                    not isinstance(offset,(int,float)) or not math.isfinite(offset)):
                raise CutError('SYNC_UNRESOLVED','Every selected source requires an accepted finite offset.')
            if (isinstance(clip.get('speed'),bool) or clip.get('speed')!=1 or clip.get('disabled') or
                    clip.get('supportFlags',{}).get('timeMappingSupported') is False or
                    snapshot.get('supportFlags',{}).get('timeMappingSupported') is False):
                raise CutError('TIME_MAPPING_UNSUPPORTED','Synchronization requires enabled normal-speed clips.')
            start,end,source_in,source_out=(tick_int(clip[field]) for field in ['startTicks','endTicks','inTicks','outTicks'])
            if start<0 or end<=start or source_in<0 or source_out-source_in!=end-start:
                raise CutError('TIME_MAPPING_UNSUPPORTED','Source and sequence clip durations must agree exactly.')
            if not isinstance(clip.get('trackRef'),str) or not clip['trackRef']:
                raise CutError('SOURCE_SCOPE','Selected clips need a stable track identity.')
            selected_offsets[asset]=offset
            if asset==reference:anchors.add(start-source_in)
        if len(anchors)!=1 or selected_offsets.get(reference)!=0:
            raise CutError('SYNC_REFERENCE_AMBIGUOUS','Select reference clips with one consistent source-zero anchor.')
        anchor=next(iter(anchors));placements=[]
        for clip in selected:
            original_start=tick_int(clip['startTicks']);duration=tick_int(clip['endTicks'])-original_start
            if clip['assetId']==reference:
                start=original_start  # Reference clips retain their exact original position.
            else:
                raw=anchor+tick_int(clip['inTicks'])+Fraction(str(selected_offsets[clip['assetId']]))*TICKS_PER_SECOND
                nearest_tick=(2*raw.numerator+raw.denominator)//(2*raw.denominator)
                start=int(frame_ticks(ticks_frame(str(nearest_tick),snapshot['fps']),snapshot['fps']))
            if start<0:
                raise CutError('SYNC_NEGATIVE_POSITION','Synchronization would move a clip before sequence zero.')
            placements.append({'instanceKey':clip['instanceKey'],'assetId':clip['assetId'],'trackRef':clip['trackRef'],
                               'startTicks':str(start),'endTicks':str(start+duration)})
        proposed={item['instanceKey']:item for item in placements};by_track={}
        for clip in snapshot['clips']:
            item=proposed.get(clip['instanceKey'],clip)
            by_track.setdefault(clip.get('trackRef'),[]).append((tick_int(item['startTicks']),tick_int(item['endTicks']),clip['instanceKey']))
        for intervals in by_track.values():
            intervals.sort()
            for index,(start,end,key) in enumerate(intervals):
                for other_start,other_end,other_key in intervals[index+1:]:
                    if other_start>=end:break
                    if (key in proposed or other_key in proposed) and start<other_end:
                        raise CutError('SYNC_COLLISION','Synchronization would overlap another clip on the same track.')
        plan={'schemaVersion':1,'snapshotHash':snapshot['snapshotHash'],'referenceAssetId':reference,
              'offsets':{asset:str(value) for asset,value in selected_offsets.items()},'selectedClipInstanceKeys':keys,'anchorReferenceTicks':str(anchor),
              'placements':placements}
        digest=canonical_hash(plan);plan.update(syncPlanHash=digest,planHash=digest)
        with self.lock:
            if self.sessions.get(instance) is not session:
                raise CutError('PLAN_SCOPE','Connected sequence changed while preparing synchronization.')
            session['plans'][digest]=copy.deepcopy(plan)
        return plan
    def require_plan(self,instance,plan_hash,snapshot_hash):
        session=self.session(instance);plan=session['plans'].get(plan_hash)
        if not plan or plan['snapshotHash']!=snapshot_hash:raise CutError('PLAN_SCOPE','Only the reviewed plan for this connected sequence can be applied.')
        return copy.deepcopy(plan)
