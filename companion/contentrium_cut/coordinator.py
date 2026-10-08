"""Project-scoped job inputs and deterministic plans for paired UXP panels."""
import copy
import json
import math
from fractions import Fraction
from pathlib import Path
import threading
import uuid
from .contract import CutError, canonical_hash, tick_int, ticks_frame, frame_ticks, TICKS_PER_SECOND
from .jobs import atomic_json
from .models import ModelManager
from .policy import plan_edit
from .analysis_inputs import channel_index, check_assignments
from .cache import result_digest, validate_result
from .corrections import derive,verify_artifact,artifact_hash,solo_examples


def prepare_plan(request,cancel=None):
    """Pure planning over copied values; no coordinator or publication authority."""
    from .models import check_cancel
    check_cancel(cancel)
    snapshot=request['snapshot'];analysis=request['analysis'];evidence=analysis.get('evidence',{})
    if isinstance(evidence,dict):
        links=analysis.get('candidateLinks',{});bounds=snapshot['range'];unresolved=set()
        for row in evidence.get('ownedTurns',[]):
            check_cancel(cancel)
            if row.get('candidateSpeakerId') and row['candidateSpeakerId'] not in links and row['startFrame']<bounds['endFrame'] and bounds['startFrame']<row['endFrame']:
                unresolved.add(row['candidateSpeakerId'])
        if unresolved:raise CutError('SPEAKER_LINK_REQUIRED','Confirm each unresolved chunk identity before planning.',{'candidateSpeakerIds':sorted(unresolved)})
    result=plan_edit(snapshot,analysis,request['mapping'],request['policy'])
    check_cancel(cancel)
    return result


def prepare_sync_plan(request,cancel=None):
    # Reuse the planner over an isolated in-memory coordinator, with no publish
    # authority or access to the live session's plans.
    coordinator=Coordinator('.')
    coordinator.bind('preparation',request['snapshot'])
    return coordinator.sync_plan('preparation',request['result'],request['keys'],cancel=cancel)

class Coordinator:
    def __init__(self,root):
        self.root=Path(root);self.sessions={};self.lock=threading.RLock()
    def bind(self,instance,snapshot):
        value=copy.deepcopy(snapshot);claimed=value.pop('snapshotHash',None)
        if canonical_hash(value)!=claimed:raise CutError('SNAPSHOT_HASH_MISMATCH','Premiere snapshot identity does not match its contents.')
        if value.get('schemaVersion')!=1 or not value.get('projectRef') or not value.get('sequenceRef'):raise CutError('INVALID_SNAPSHOT','A Premiere project and sequence are required.')
        value['snapshotHash']=claimed
        with self.lock:
            self.sessions[instance]={'snapshot':value,'plans':{},'planAnalyses':{},'jobs':set(),'generation':uuid.uuid4().hex}
        return {'snapshotHash':claimed}
    def session(self,instance):
        with self.lock:
            if instance not in self.sessions:raise CutError('PROJECT_REQUIRED','Connect the current Premiere sequence first.')
            return self.sessions[instance]
    def media_scope(self,snapshot):
        # Capture identities only here. The owned worker observes bytes before
        # analysis; planning must never backfill evidence from newer contents.
        sources=[]
        for source in snapshot['sources']:
            if source.get('offline') or not source.get('canonicalPath'):
                raise CutError('SOURCE_REANALYSIS_REQUIRED','Reconnect all sequence media before analysis.')
            sources.append({'assetId':source['assetId'],'path':source['canonicalPath']})
        return dict(mediaSources=sources,mediaSnapshotHash=snapshot['snapshotHash'])
    def audio_payload(self,instance,options):
        snap=self.session(instance)['snapshot'];clips={c['instanceKey']:c for c in snap['clips']};assets={s['assetId']:s for s in snap['sources']}
        mode=options.get('mode');microphones=options.get('microphones',[])
        if mode not in {'separate','mixed'} or not microphones:raise CutError('INVALID_AUDIO_INPUT','Select recording mode and microphone clips.')
        threshold=options.get('vadThreshold',.5) if mode=='separate' else .5
        count=options.get('speakerCount') if mode=='mixed' else None
        if isinstance(threshold,bool) or not isinstance(threshold,(int,float)) or not .05<=threshold<=.95 or not math.isfinite(threshold):
            raise CutError('INVALID_AUDIO_INPUT','Speech sensitivity must be a finite number between 0.05 and 0.95.')
        if count is not None and (isinstance(count,bool) or not isinstance(count,(int,float)) or not 1<=count<=26 or not math.isfinite(count) or int(count)!=count):
            raise CutError('INVALID_AUDIO_INPUT','Expected speaker count must be an integer between 1 and 26, or omitted for automatic detection.')
        sources=[];offsets={};instance_offsets={};channels=[];seen=set();ambiguous=set()
        for selected in microphones:
            clip=clips.get(selected.get('instanceKey'))
            if not clip:raise CutError('SOURCE_SCOPE','Microphone is not in the connected Premiere sequence.')
            asset=assets.get(clip['assetId'])
            if not asset or asset.get('offline'):raise CutError('MISSING_AUDIO','Selected microphone media is offline.')
            if isinstance(clip.get('speed'),bool) or clip.get('speed')!=1 or clip.get('disabled'):raise CutError('TIME_MAPPING_UNSUPPORTED','Use an enabled, normal-speed microphone clip.')
            key=clip['assetId'];offset=(tick_int(clip['startTicks'])-tick_int(clip['inTicks']))/TICKS_PER_SECOND
            stream=channel_index(selected.get('streamIndex',0));channel=channel_index(selected.get('channelIndex',0))
            input_key=canonical_hash({'instanceKey':clip['instanceKey'],'streamIndex':stream,'channelIndex':channel})
            if input_key in seen:raise CutError('INVALID_AUDIO_INPUT','A recording channel was selected twice.')
            seen.add(input_key)
            if key in offsets and offsets[key]!=offset:ambiguous.add(key)
            offsets[key]=offset;instance_offsets[input_key]=offset
            source={'assetId':key,'instanceKey':clip['instanceKey'],'inputKey':input_key,'offsetSeconds':offset,'path':str(Path(asset['canonicalPath']).resolve(strict=True)),'streamIndex':stream}
            source.update(sourceStartSeconds=tick_int(clip['inTicks'])/TICKS_PER_SECOND,durationSeconds=(tick_int(clip['endTicks'])-tick_int(clip['startTicks']))/TICKS_PER_SECOND,sequenceStartSeconds=tick_int(clip['startTicks'])/TICKS_PER_SECOND)
            if 'channelIndex' in selected:source['channelIndex']=channel
            sources.append(source)
            if mode=='separate':channels.append({'assetId':key,'instanceKey':clip['instanceKey'],'inputKey':input_key,'speakerId':selected.get('speakerId'),'streamIndex':stream,'channelIndex':channel})
        for key in ambiguous:offsets.pop(key,None)
        if mode=='separate':check_assignments(channels,sources)
        settings=dict(self.resource_settings(include_status=False)['settings'],fps=snap['fps'],range=snap['range'],offsets=offsets,channels=channels,calibration=options.get('calibration',[]),modelRoot=str(self.root/'models'),speakerCount=int(count) if count is not None else None,vadThreshold=float(threshold),bleedTolerance=float(options.get('bleedTolerance',1.8)))
        settings['instanceOffsets']=instance_offsets
        ffmpeg=self.root/'app'/'ffmpeg'/'ffmpeg.exe'
        if ffmpeg.is_file():settings['ffmpeg']=str(ffmpeg)
        model=ModelManager(self.root/'models').state('silero' if mode=='separate' else 'community-1',verify=False)
        settings['modelRevision']=model.get('revision');settings['modelManifestHash']=canonical_hash(model.get('manifest',{}))
        return dict(mode=mode,sources=sources,settings=settings,**self.media_scope(snap))
    def sync_payload(self,instance,options):
        snap=self.session(instance)['snapshot'];assets={a['assetId']:a for a in snap['sources']};selections=options.get('sourceSelections')
        if selections is None:selections=[{'assetId':a} for a in options.get('assetIds',[])]
        if not isinstance(selections,list) or any(not isinstance(s,dict) or not isinstance(s.get('assetId'),str) for s in selections):raise CutError('INVALID_AUDIO_INPUT','Select synchronization source streams and channels.')
        ids=[s['assetId'] for s in selections]
        if len(ids)<2 or len(set(ids))!=len(ids) or any(i not in assets for i in ids):raise CutError('SOURCE_SCOPE','Select at least two imported sources for synchronization.')
        if 'assetIds' in options and options['assetIds']!=ids:raise CutError('SOURCE_SCOPE','Source selection identities disagree.')
        method=options.get('method','audio');reference=options.get('reference',ids[0])
        if method not in {'audio','manual','timecode'} or reference not in ids:raise CutError('INVALID_AUDIO_INPUT','Choose a selected reference and synchronization method.')
        sources=[]
        for selection in selections:
            asset=selection['assetId']
            if assets[asset].get('offline'):raise CutError('MISSING_AUDIO','Selected synchronization media is offline.')
            source={'assetId':asset,'path':str(Path(assets[asset]['canonicalPath']).resolve(strict=True)),'streamIndex':channel_index(selection.get('streamIndex',0)),'channelIndex':channel_index(selection.get('channelIndex',0)),'syncMethod':method,'resourceSettings':self.resource_settings(include_status=False)['settings']}
            if method=='manual':source['manualConfirmation']=copy.deepcopy(options.get('manualOffsets',{}).get(asset,{}))
            if method=='timecode':source['timecodeConfirmation']=copy.deepcopy(options.get('timecodeConfirmations',{}).get(asset,{}))
            sources.append(source)
        ffmpeg=self.root/'app'/'ffmpeg'/'ffmpeg.exe'
        if ffmpeg.is_file():
            for source in sources:source['ffmpeg']=str(ffmpeg)
        return dict(sources=sources,reference=reference,fps=snap['fps'],**self.media_scope(snap))
    def plan(self,instance,analysis,mapping,policy):
        request=self.capture_plan(instance,analysis,mapping,policy)
        return self.publish_plan(instance,request,prepare_plan(request))

    def capture_plan(self,instance,analysis,mapping,policy):
        """Copy the pure worker payload while pinning this exact bound session."""
        with self.lock:
            session=self.session(instance)
            return copy.deepcopy(dict(snapshot=session['snapshot'],sessionGeneration=session['generation'],
                                      analysis=analysis,mapping=mapping,policy=policy))

    def publish_plan(self,instance,request,result):
        """Publish an internally prepared plan; never compute it under admission."""
        with self.lock:
            session=self.session(instance);analysis=request['analysis']
            if session['generation']!=request['sessionGeneration'] or session['snapshot']['snapshotHash']!=request['snapshot']['snapshotHash']:
                raise CutError('PLAN_SCOPE','Connected sequence changed while preparing the edit plan.')
            identity=analysis.get('analysisId')
            if identity:
                current=self.analysis_state(instance,identity)
                if current['revision']!=analysis.get('correctionRevision') or current['analysisHash']!=analysis.get('analysisHash'):
                    raise CutError('CORRECTION_REVISION_CONFLICT','Analysis changed while preparing the edit plan.')
            elif analysis.get('correctionRevision',0):raise CutError('ANALYSIS_SCOPE','Corrected plans require their persisted analysis identity.')
            if result.get('snapshotHash')!=request['snapshot']['snapshotHash']:
                raise CutError('PLAN_SCOPE','Prepared plan belongs to another snapshot.')
            from .apply_journal import edit_batch_count
            clips={c['instanceKey']:c for c in request['snapshot']['clips']}
            tracks={clips[c['instanceKey']]['trackRef'] for camera in request['mapping']['cameras'] for c in camera['clips']}
            session.setdefault('planCapacity',{})[result['planHash']]=edit_batch_count(request['snapshot'],result,tracks)+1
            if identity:session['planAnalyses'][result['planHash']]={'analysisId':identity,'revision':current['revision']}
            session['plans'][result['planHash']]=result
        return result
    def publish_sync_plan(self,instance,request,plan):
        with self.lock:
            session=self.session(instance)
            if session['generation']!=request['generation'] or session['snapshot']['snapshotHash']!=plan['snapshotHash']:
                raise CutError('PLAN_SCOPE','Connected sequence changed during synchronization planning.')
            session['plans'][plan['planHash']]=copy.deepcopy(plan)
            session.setdefault('planMedia',{})[plan['planHash']]=copy.deepcopy(request['result'])
        return plan

    def sync_plan(self,instance,result,selected_clip_instance_keys,cancel=None):
        from .models import check_cancel
        check_cancel(cancel)
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
            check_cancel(cancel)
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
            check_cancel(cancel)
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
            check_cancel(cancel)
            item=proposed.get(clip['instanceKey'],clip)
            by_track.setdefault(clip.get('trackRef'),[]).append((tick_int(item['startTicks']),tick_int(item['endTicks']),clip['instanceKey']))
        for intervals in by_track.values():
            intervals.sort()
            for index,(start,end,key) in enumerate(intervals):
                check_cancel(cancel)
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
            session.setdefault('planMedia',{})[digest]=copy.deepcopy(result)
        return plan
    def require_plan(self,instance,plan_hash,snapshot_hash):
        with self.lock:
            session=self.session(instance);plan=session['plans'].get(plan_hash)
            if not plan or plan['snapshotHash']!=snapshot_hash:raise CutError('PLAN_SCOPE','Only the reviewed plan for this connected sequence can be applied.')
            bound=session['planAnalyses'].get(plan_hash)
            if bound and self.analysis_state(instance,bound['analysisId'])['revision']!=bound['revision']:raise CutError('CORRECTION_REVISION_CONFLICT','Analysis changed after the edit plan was reviewed.')
            return copy.deepcopy(plan)
    def _analysis_path(self,instance,analysis_id):
        import re
        snap=self.session(instance)['snapshot']
        if not isinstance(analysis_id,str) or not re.fullmatch('[0-9a-f]{64}',analysis_id):raise CutError('ANALYSIS_SCOPE','Analysis identity is invalid.')
        return self.root/'projects'/canonical_hash({'projectRef':snap['projectRef']})/'analyses'/(analysis_id+'.json')
    def register_analysis(self,instance,job_id,analysis):
        with self.lock:
            snap=self.session(instance)['snapshot']
            if not isinstance(job_id,str) or not job_id:raise CutError('INVALID_ANALYSIS','Only a completed valid analysis can be registered.')
            validate_result('analysis',analysis)
            assets={s['assetId']:s for s in snap['sources']};clips={c['instanceKey']:c for c in snap['clips']}
            for source in analysis.get('sourceInputs',[]):
                asset=assets.get(source.get('assetId'));clip=clips.get(source.get('instanceKey'))
                if not asset or Path(source.get('path','')).resolve()!=Path(asset['canonicalPath']).resolve() or (source.get('instanceKey') and (not clip or clip['assetId']!=source['assetId'])):
                    raise CutError('ANALYSIS_SCOPE','Analysis inputs do not belong to the connected snapshot.')
            digest=result_digest(analysis);identity=canonical_hash({'jobId':job_id,'snapshotHash':snap['snapshotHash'],'analysisHash':digest})
            path=self._analysis_path(instance,identity)
            if path.is_file():return self.analysis_state(instance,identity)
            value={'schemaVersion':1,'analysisId':identity,'analysisHash':digest,'projectRef':snap['projectRef'],'snapshotHash':snap['snapshotHash'],'fps':snap['fps'],'revision':0,'raw':copy.deepcopy(analysis),'history':[]}
            atomic_json(path,artifact_hash(value));return self.analysis_state(instance,identity)
    def analysis_state(self,instance,analysis_id):
        with self.lock:
            snap=self.session(instance)['snapshot'];path=self._analysis_path(instance,analysis_id)
            try:value=json.loads(path.read_text(encoding='utf-8'))
            except (OSError,ValueError):raise CutError('ANALYSIS_SCOPE','Analysis does not belong to this connected project and sequence.')
            verify_artifact(value,snap['projectRef'],snap['snapshotHash']);analysis,names=derive(value['raw'],value['history'])
            analysis.update(analysisId=value['analysisId'],analysisHash=value['analysisHash'],snapshotHash=value['snapshotHash'])
            return dict(value,analysis=analysis,names=names,examples=solo_examples(analysis,value['fps']))
    def correct_analysis(self,instance,analysis_id,expected_revision,operation,request_id=None):
        with self.lock:
            if not isinstance(operation,dict):raise CutError('INVALID_CORRECTION','Correction operation must be an object.')
            state=self.analysis_state(instance,analysis_id)
            if request_id is not None:
                if not isinstance(request_id,str) or not request_id or len(request_id)>128:raise CutError('INVALID_CORRECTION','Correction request identity is invalid.')
                prior=next((e for e in state['history'] if e.get('requestId')==request_id),None)
                if prior:
                    if prior['operation']!=operation:raise CutError('CORRECTION_REQUEST_CONFLICT','A correction request identity was reused with different contents.')
                    return state
            if type(expected_revision) is not int or expected_revision!=state['revision']:raise CutError('CORRECTION_REVISION_CONFLICT','Reload the latest correction revision.')
            value={k:v for k,v in state.items() if k not in ('analysis','names','examples','artifactHash')};value['revision']+=1
            value['history'].append({'revision':value['revision'],'requestId':request_id,'operation':copy.deepcopy(operation)})
            corrected,_=derive(value['raw'],value['history'])
            validate_result('analysis',corrected)
            atomic_json(self._analysis_path(instance,analysis_id),artifact_hash(value));self.session(instance)['plans'].clear();self.session(instance)['planAnalyses'].clear()
            return self.analysis_state(instance,analysis_id)
    def solo_examples(self,instance,analysis_id):
        return self.analysis_state(instance,analysis_id)['examples']
    def resource_settings(self,update=None,include_status=True):
        from .resource import validate_settings,validate_settings_shape,cache_status
        path=self.root/'settings'/'audio-resources.json'
        with self.lock:
            try:saved=json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
            except (ValueError,OSError):raise CutError('INVALID_AUDIO_SETTINGS','Saved local resource settings are corrupt.')
            settings=validate_settings(saved)
            if update is not None:
                validate_settings_shape(update);settings=validate_settings(dict(settings,**update));atomic_json(path,settings)
            return dict(settings=settings,**({'status':cache_status(self.root/'cache')} if include_status else {}))
    def render_example(self,instance,analysis_id,example_id,cancel=None):
        import tempfile
        from .audio import _decode,SR
        from .audio_evidence import require_media_identity
        from .models import check_cancel
        from scipy.io import wavfile
        import numpy as np
        with self.lock:
            state=self.analysis_state(instance,analysis_id);snap_hash=state['snapshotHash'];revision=state['revision']
            example=next((e for e in state['examples'] if e['exampleId']==example_id),None)
            if not example:raise CutError('EXAMPLE_SCOPE','Listening example is outside this analysis revision.')
            source=next(s for s in state['raw'].get('sourceInputs',[]) if s.get('inputKey',s.get('instanceKey',s['assetId']))==example['inputKey'])
        require_media_identity(source,cancel);directory=self._analysis_path(instance,analysis_id).parent/(analysis_id+'-examples');directory.mkdir(parents=True,exist_ok=True)
        target=directory/(example_id+'.wav')
        settings=self.resource_settings(include_status=False)['settings'];ffmpeg=self.root/'app'/'ffmpeg'/'ffmpeg.exe'
        if ffmpeg.is_file():settings['ffmpeg']=str(ffmpeg)
        with tempfile.TemporaryDirectory(prefix='example-',dir=directory) as scratch:
            info=_decode(source,scratch,0,settings,cancel=cancel,start=float(example['sourceStartSeconds']),duration=float(example['durationSeconds']),mixed=state['raw'].get('mode')=='mixed')
            try:
                check_cancel(cancel);samples=(np.clip(np.asarray(info['samples']),-1,1)*32767).astype(np.int16)
                wavfile.write(Path(scratch)/'preview.wav',SR,samples)
                require_media_identity(source,cancel)
                with self.lock:
                    current=self.analysis_state(instance,analysis_id)
                    if current['snapshotHash']!=snap_hash or current['revision']!=revision:raise CutError('EXAMPLE_SCOPE','Analysis revision changed while rendering the example.')
                    check_cancel(cancel);(Path(scratch)/'preview.wav').replace(target)
            finally:info['samples']._mmap.close()
        return dict(example,path=str(target),analysisId=analysis_id,revision=revision)
