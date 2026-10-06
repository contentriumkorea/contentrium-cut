"""Local audio evidence; PCM is disk backed and source media is never modified."""
import itertools
import json
import math
import os
import shutil
import subprocess
import tempfile
import time
from bisect import bisect_right
from fractions import Fraction
from pathlib import Path

import numpy as np
from scipy.signal import correlate, resample_poly
from .contract import CutError,frame_ticks
from .models import ModelManager, SileroVAD, CommunityDiarizer, check_cancel
from .analysis_inputs import source_key, check_assignments, channel_index
from .resource import preflight_pcm, positive_number
from .mixed_chunks import diarize_chunks
from .audio_evidence import media_digest

SR = 16000


def _frame(seconds, fps):
    return math.floor(Fraction(str(seconds))*int(fps['num'])/int(fps['den'])+Fraction(1,2))


def _seconds(frame, fps):
    return float(Fraction(int(frame)*int(fps['den']),int(fps['num'])))


def _run(argv, cancel=None, stdout=None,max_output_bytes=None):
    check_cancel(cancel)
    with tempfile.TemporaryFile() as errors, tempfile.TemporaryFile() as captured:
        child=subprocess.Popen(argv,stdout=stdout if stdout is not None else captured,stderr=errors,shell=False,
                               creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            while child.poll() is None:
                check_cancel(cancel)
                if max_output_bytes is not None and stdout is not None and os.fstat(stdout.fileno()).st_size>max_output_bytes:
                    raise CutError('AUDIO_RESOURCE_LIMIT','Decoder output exceeded its admitted PCM bound.')
                time.sleep(.02)
            captured.seek(0)
            output=captured.read() if stdout is None else None
            if child.returncode:
                errors.seek(0)
                raise CutError('AUDIO_DECODE_FAILED','Local decoder failed.',{'decoder':errors.read(4096).decode('utf-8','replace')})
            return output
        except BaseException:
            if child.poll() is None:
                child.terminate()
                try: child.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    child.kill(); child.wait()
            raise


def _media_metadata(source,cancel=None,settings=None):
    path=Path(source.get('path',''))
    if not path.is_file(): raise CutError('MISSING_AUDIO','Selected source is missing.',{'assetId':source.get('assetId')})
    executable=(settings or {}).get('ffmpeg') or source.get('ffmpeg') or os.environ.get('CONTENTRIUM_FFMPEG') or shutil.which('ffmpeg')
    if not executable or not Path(executable).is_file(): raise CutError('FFMPEG_NOT_READY','Configure a local FFmpeg executable.')
    probe=str(Path(executable).with_name('ffprobe.exe' if os.name=='nt' else 'ffprobe'))
    data=json.loads(_run([probe,'-v','error','-show_streams','-show_format','-of','json',str(path.resolve())],cancel))
    return {'path':str(path.resolve()),'ffmpeg':str(executable),'metadata':data,'size':path.stat().st_size,'mtimeNs':path.stat().st_mtime_ns}

def _probe(source, settings=None, cancel=None):
    base=_media_metadata(source,cancel,settings);data=base['metadata']
    streams=[s for s in data.get('streams',[]) if s.get('codec_type')=='audio']
    index=channel_index(source.get('streamIndex',0)); channel=channel_index(source.get('channelIndex',0))
    if index<0 or index>=len(streams): raise CutError('MISSING_AUDIO','Selected stream has no audio.')
    stream=streams[index]
    if channel<0 or channel>=int(stream['channels']): raise CutError('MISSING_AUDIO','Selected channel does not exist.')
    duration=stream.get('duration') or data.get('format',{}).get('duration')
    duration=float(duration) if duration is not None else None
    format_origin=float(data.get('format',{}).get('start_time',0) or 0);pts_origin=float(stream.get('start_time',0) or 0)
    if stream.get('duration') is not None:duration=float(stream['duration'])+pts_origin-format_origin
    return dict(base,stream=index,channel=channel,channels=int(stream['channels']),durationSeconds=duration,
                origin=pts_origin-format_origin,audioStreamOriginSeconds=pts_origin-format_origin,formatPtsOriginSeconds=format_origin,sampleRate=int(stream.get('sample_rate',SR)))


def _verify_mixed_channels(argv, info, target, source, cancel,max_output_bytes=None):
    """Inspect disk-backed channels before averaging an unselected stereo mix."""
    audit = target.with_name('channels-' + target.name)
    with audit.open('wb') as output:
        _run(argv + ['-ar', str(SR), '-ac', str(info['channels']), '-f', 'f32le', 'pipe:1'], cancel, stdout=output,max_output_bytes=max_output_bytes)
    if max_output_bytes is not None and audit.stat().st_size>max_output_bytes:
        raise CutError('AUDIO_RESOURCE_LIMIT','Multichannel audit exceeded its admitted PCM bound.')
    if not audit.stat().st_size or audit.stat().st_size % (4 * info['channels']):
        raise CutError('MISSING_AUDIO', 'No complete multichannel samples were decoded.')
    pcm = np.memmap(audit, dtype='<f4', mode='r')
    try:
        samples = pcm.reshape((-1, info['channels']))
        for start in range(0, len(samples), SR):
            check_cancel(cancel)
            chunk = np.asarray(samples[start:start+SR], dtype=np.float64)
            levels = np.sqrt(np.mean(chunk * chunk, axis=0))
            active = levels >= .0001
            if not np.any(active):
                continue
            reason = None
            if not np.all(active):
                reason = 'INCONSISTENT_CHANNEL_ACTIVITY'
            elif np.sqrt(np.mean(np.square(np.mean(chunk, axis=1)))) < .1 * np.max(levels):
                reason = 'POLARITY_CANCELLATION'
            else:
                for left, right in itertools.combinations(range(info['channels']), 2):
                    correlation = float(np.corrcoef(chunk[:, left], chunk[:, right])[0, 1])
                    if not math.isfinite(correlation) or correlation < .95:
                        reason = 'DISTINCT_CHANNEL_CONTENT'
                        break
            if reason:
                raise CutError('AUDIO_CHANNEL_SELECTION_REQUIRED',
                               'Choose an explicit channel before analyzing this multichannel recording.',
                               {'assetId': source.get('assetId'), 'channelCount': info['channels'],
                                'reason': reason, 'windowStartSample': start})
    finally:
        pcm._mmap.close()
        audit.unlink(missing_ok=True)


def _decode(source,directory,number,settings=None,cancel=None,start=None,duration=None,mixed=False):
    if settings is None:settings=source.get('resourceSettings')
    if start is None:start=source.get('sourceStartSeconds')
    if duration is None:duration=source.get('durationSeconds')
    info=_probe(source,settings,cancel); target=Path(directory)/('pcm-%s.f32'%number)
    if start is not None and (isinstance(start,bool) or not isinstance(start,(int,float)) or not math.isfinite(start) or start<0):
        raise CutError('INVALID_AUDIO_INPUT','Source start must be finite and nonnegative.')
    estimated=duration if duration is not None else (info['durationSeconds']-(start or 0) if info['durationSeconds'] is not None else None)
    budget=preflight_pcm(estimated,settings,directory,channels=info['channels']+1 if mixed and 'channelIndex' not in source else 1,mixed=mixed)
    argv=[info['ffmpeg'],'-v','error','-nostdin','-i',info['path']]
    if start is not None: argv+=['-ss',str(max(0.,start))]
    if duration is not None: argv+=['-t',str(max(0.,duration))]
    argv+=['-map','0:a:%s'%info['stream']]
    if mixed:
        if 'channelIndex' in source:
            argv+=['-af','pan=mono|c0=c%d'%info['channel']]
            info['channelSelection'] = {'mode': 'explicit', 'channelIndex': info['channel']}
        elif info['channels']>1:
            _verify_mixed_channels(argv, info, target, source, cancel,budget['maxOutputBytes']*info['channels'])
            expr='+'.join('%s*c%d'%(1/info['channels'],c) for c in range(info['channels']))
            argv+=['-af','pan=mono|c0='+expr]
            info['channelSelection'] = {'mode': 'verified-average', 'channelCount': info['channels']}
        else:
            info['channelSelection'] = {'mode': 'mono', 'channelIndex': 0}
    else: argv+=['-af','pan=mono|c0=c%d'%info['channel']]
    argv+=['-ar',str(SR),'-ac','1','-f','f32le','pipe:1']
    with target.open('wb') as f: _run(argv,cancel,stdout=f,max_output_bytes=budget['maxOutputBytes'])
    if target.stat().st_size>budget['maxOutputBytes']:raise CutError('AUDIO_RESOURCE_LIMIT','Decoder output exceeded its admitted PCM bound.')
    if not target.stat().st_size: raise CutError('MISSING_AUDIO','No audio samples were decoded.')
    media_origin=max(start or 0,info['audioStreamOriginSeconds'])
    info.update(samples=np.memmap(target,dtype='<f4',mode='r'),origin=media_origin,sourcePtsOriginSeconds=info['formatPtsOriginSeconds']+media_origin,pcmPath=str(target))
    return info


def _pair(a,b,fps,cancel):
    x,y=a['samples'],b['samples']; window=min(10*SR,len(x)//5,len(y)//5)
    if window<SR//2: return None
    records=[]
    for position in np.unique(np.linspace(0,len(x)-window,11).astype(int)):
        check_cancel(cancel)
        raw=np.array(x[position:position+window],dtype=np.float64); raw-=raw.mean()
        if np.sqrt(np.mean(raw*raw))<.0001: continue
        query=resample_poly(raw,1,8); query-=query.mean(); energy=np.sum(query*query); candidates=[]
        for block_start in range(0,max(1,len(y)-window+1),60*SR-window):
            check_cancel(cancel)
            block=resample_poly(np.asarray(y[block_start:min(len(y),block_start+60*SR)],dtype=np.float64),1,8)
            if len(block)<len(query): continue
            dot=correlate(block,query,mode='valid',method='fft')
            cumulative=np.concatenate(([0.],np.cumsum(block*block)))
            energies=cumulative[len(query):]-cumulative[:-len(query)]
            scores=np.abs(dot)/np.sqrt(np.maximum(energies*energy,1e-24)); best=int(np.argmax(scores))
            mask=scores.copy(); mask[max(0,best-250):best+251]=0
            candidates.append((float(scores[best]),block_start+best*8,float(mask.max(initial=0))))
        if not candidates: continue
        candidates.sort(reverse=True); score,coarse,competitor=candidates[0]
        competitor=max([competitor]+[c[0] for c in candidates[1:] if abs(c[1]-coarse)>SR//8])
        if score<.28: continue
        lo=max(0,coarse-128); hi=min(len(y),coarse+window+128)
        dots=correlate(np.asarray(y[lo:hi],dtype=np.float64),raw,mode='valid',method='fft')
        match=lo+int(np.argmax(np.abs(dots)))
        records.append({'sourceTimeSeconds':a['origin']+int(position)/SR,'offsetSeconds':a['origin']+int(position)/SR-b['origin']-match/SR,
                        'correlation':score,'competitorCorrelation':competitor})
    if not records: return None
    offsets=np.array([r['offsetSeconds'] for r in records]); median=float(np.median(offsets)); tolerance=int(fps['den'])/int(fps['num'])
    consistent=[r for r in records if abs(r['offsetSeconds']-median)<=tolerance]
    usable=[r for r in consistent if r['correlation']>=.65 and r['correlation']-r['competitorCorrelation']>=.1]
    residual=max(abs(r['offsetSeconds']-median) for r in records)
    for r in records: r['sampleResidual']=round((r['offsetSeconds']-median)*SR)
    times=np.array([r['sourceTimeSeconds'] for r in records])
    slope=float(np.polyfit(times,offsets,1)[0]) if len(records)>=3 and np.ptp(times) else None
    distributed=len(usable)>=3 and max(r['sourceTimeSeconds'] for r in usable)-min(r['sourceTimeSeconds'] for r in usable)>=window/SR
    return {'offsetSeconds':median,'status':'drift' if len(consistent)<len(records) and residual>tolerance else ('accepted' if distributed else 'review'),
            'windows':records,'uncertaintySeconds':max(1/SR,max([abs(r['offsetSeconds']-median) for r in consistent],default=residual)),
            'driftSlope':slope,'maxResidualSeconds':residual}


def sync_sources(sources,reference,fps,cancel=None):
    if not sources or len({s['assetId'] for s in sources})!=len(sources) or reference not in {s['assetId'] for s in sources}:
        raise CutError('INVALID_AUDIO_INPUT','Sync requires unique sources and a reference.')
    methods={s.get('syncMethod','audio') for s in sources}
    if len(methods)!=1 or not methods<={'audio','manual','timecode'}:raise CutError('INVALID_AUDIO_INPUT','Choose one synchronization method for all selected sources.')
    if methods!={'audio'}:
        from .sync_methods import explicit_sync
        result=explicit_sync(sources,reference,fps,_media_metadata,cancel)
        if methods=={'timecode'}:
            eligible=[dict(s,syncMethod='audio') for s in sources if s['assetId'] in result['offsets'] and result['sources'][s['assetId']]['hasAudio'] and Path(s['path']).is_file()]
            if len(eligible)>=2:
                audio_reference=reference if any(s['assetId']==reference for s in eligible) else eligible[0]['assetId']
                waveform=sync_sources(eligible,audio_reference,fps,cancel)
                unsafe={asset for review in waveform['reviews'] if review['code'] in {'SYNC_DRIFT','SYNC_GRAPH_CONFLICT'} for asset in review.get('assetIds',[])}
                anchor=result['offsets'][audio_reference]
                for source in eligible:
                    asset=source['assetId']
                    if asset==reference:continue
                    verified=waveform['offsets'].get(asset);expected=result['offsets'][asset]-anchor
                    if asset in unsafe or (verified is not None and abs(verified-expected)>fps['den']/fps['num']):
                        result['sources'][asset]['status']='review';result['offsets'].pop(asset,None)
                        result['reviews'].append({'code':'TIMECODE_AUDIO_CONFLICT','assetId':asset,'fallback':'manual'})
                    else:result['sources'][asset]['audioVerification']='accepted' if verified is not None else 'unresolved'
                result['audioVerificationEdges']=waveform['edges']
        return result
    plan={'schemaVersion':1,'referenceAssetId':reference,'offsets':{reference:0.},'sources':{},'edges':[],'reviews':[]}
    with tempfile.TemporaryDirectory(prefix='contentrium-sync-') as directory:
        decoded={}
        try:
            for n,s in enumerate(sources): decoded[s['assetId']]=_decode(s,directory,n,cancel=cancel)
            for asset,info in decoded.items():
                plan['sources'][asset]={'status':'accepted' if asset==reference else 'unresolved','path':[reference] if asset==reference else [],
                    'pcmOriginSeconds':info['origin'],'validSourceRange':{'startSample':0,'endSample':len(info['samples']),'sampleRate':SR},'size':info['size'],'mtimeNs':info['mtimeNs']}
            for left,right in itertools.combinations(decoded,2):
                edge=_pair(decoded[left],decoded[right],fps,cancel)
                if edge:
                    edge.update(fromAssetId=left,toAssetId=right); plan['edges'].append(edge)
                    if edge['status']=='drift': plan['reviews'].append({'code':'SYNC_DRIFT','assetIds':[left,right],'details':edge})
            pending=True; uncertainty={reference:0.}
            while pending:
                pending=False
                for edge in plan['edges']:
                    if edge['status']!='accepted': continue
                    a,b=edge['fromAssetId'],edge['toAssetId']; delta=edge['offsetSeconds']
                    if b in plan['offsets'] and a not in plan['offsets']: a,b,delta=b,a,-delta
                    if a not in plan['offsets']: continue
                    candidate=plan['offsets'][a]+delta
                    if b not in plan['offsets']:
                        plan['offsets'][b]=candidate; uncertainty[b]=uncertainty[a]+edge['uncertaintySeconds']
                        plan['sources'][b].update(status='accepted',path=plan['sources'][a]['path']+[b],uncertaintySeconds=uncertainty[b]); pending=True
                    elif abs(candidate-plan['offsets'][b])>int(fps['den'])/int(fps['num']):
                        plan['sources'][b]['status']='review'; plan['reviews'].append({'code':'SYNC_GRAPH_CONFLICT','assetIds':[a,b]})
            for asset,info in plan['sources'].items():
                if asset!=reference and any(asset in r.get('assetIds',[]) for r in plan['reviews']): info['status']='review'
                if asset!=reference and info.get('uncertaintySeconds',0)>int(fps['den'])/int(fps['num']):
                    info['status']='review'; plan['reviews'].append({'code':'SYNC_ACCUMULATED_UNCERTAINTY','assetId':asset})
            for asset,info in plan['sources'].items():
                if any(plan['sources'][ancestor]['status']!='accepted' for ancestor in info['path']):
                    info['status']='review'
                    plan['reviews'].append({'code':'SYNC_PATH_UNCERTAIN','assetId':asset,'path':info['path']})
                if info['status']!='accepted':
                    if asset!=reference: plan['offsets'].pop(asset,None)
                    plan['reviews'].append({'code':'SYNC_UNRESOLVED','assetId':asset})
        finally:
            for info in decoded.values(): info['samples']._mmap.close()
    check_cancel(cancel)
    return plan


def _intervals(turns,valid,fps,frame_range,unknown=()):
    start,end=frame_range['startFrame'],frame_range['endFrame']; events={start:[],end:[]}
    def add(a,b,label):
        a,b=max(start,a),min(end,b)
        if a<b:
            events.setdefault(a,[]).append((label,1)); events.setdefault(b,[]).append((label,-1))
    for a,b,s in turns: add(_frame(a,fps),_frame(b,fps),('speaker',s))
    for a,b in unknown: add(a,b,('unknown',None))
    for r in valid: add(r['startFrame'],r['endFrame'],('valid',None))
    result=[]; ordered=sorted(events); counts={}
    for a,b in zip(ordered,ordered[1:]):
        for label,delta in events[a]: counts[label]=counts.get(label,0)+delta
        if counts.get(('valid',None),0)<=0: continue
        row={'startFrame':a,'endFrame':b,'speakers':sorted(label[1] for label,count in counts.items() if label[0]=='speaker' and count>0),
             'unknown':counts.get(('unknown',None),0)>0}
        if row['unknown']: row['reason']='CHANNEL_IDENTITY_UNCERTAIN'
        if result and all(result[-1].get(k)==row.get(k) for k in ('speakers','unknown','reason')) and result[-1]['endFrame']==a: result[-1]['endFrame']=b
        else: result.append(row)
    return result


def _separate(decoded,channels,settings,fps,frame_range,cancel):
    model=SileroVAD(ModelManager(settings.get('modelRoot'),cancel=cancel)); steps={}; reviews=[]; unknown=[]; valid=[]; evidence=[]
    for channel,info in zip(channels,decoded):
        sid=channel['speakerId']; offset=info['sessionOrigin']; samples=info['samples']; active=False; rows=[]; sample_ranges=[]; run_start=None
        for a,b,p in model.probabilities(samples,cancel):
            if p>=float(settings.get('vadThreshold',.5)): active=True
            elif p<float(settings.get('vadEndThreshold',.35)): active=False
            rms=float(np.sqrt(np.mean(np.square(samples[a:b],dtype=np.float64))))
            rows.append((offset+a/SR,offset+b/SR,active,rms,p,source_key(channel)))
            if active and run_start is None: run_start=a
            if not active and run_start is not None:
                sample_ranges.append({'startSample':run_start,'endSample':a}); run_start=None
        if run_start is not None: sample_ranges.append({'startSample':run_start,'endSample':len(samples)})
        steps.setdefault(sid,[]).extend(rows)
        valid.append({'assetId':channel['assetId'],'speakerId':sid,'startFrame':_frame(offset,fps),'endFrame':_frame(offset+len(samples)/SR,fps),
                      'startSample':0,'endSample':len(samples),'sampleRate':SR,'sourceOriginSeconds':info['origin']})
        for key in ('instanceKey','inputKey'):
            if key in channel:valid[-1][key]=channel[key]
        levels=[]; clipped=0
        for a in range(0,len(samples),SR):
            chunk=np.asarray(samples[a:a+SR]); levels.append(float(np.sqrt(np.mean(chunk.astype(np.float64)**2)))); clipped+=int(np.count_nonzero(np.abs(chunk)>=.999))
        evidence.append({'speakerId':sid,'noiseFloorRms':float(np.percentile(levels,10)),'levelRms':float(np.percentile(levels,90)),
                         'clippedSamples':clipped,'vadScoreRange':[min((r[4] for r in rows),default=0),max((r[4] for r in rows),default=0)],
                         'speechSampleRanges':sample_ranges,'sessionOriginSeconds':offset,'sampleRate':SR})
        for key in ('instanceKey','inputKey','assetId'):
            if key in channel:evidence[-1][key]=channel[key]
    for sid in steps:
        cursor=frame_range['startFrame']
        for coverage in sorted((r for r in valid if r['speakerId']==sid),key=lambda r:r['startFrame']):
            lo,hi=cursor,min(coverage['startFrame'],frame_range['endFrame'])
            if lo<hi:unknown.append((lo,hi));reviews.append({'code':'AUDIO_COVERAGE_GAP','speakerId':sid,'startFrame':lo,'endFrame':hi})
            cursor=max(cursor,coverage['endFrame'])
        if cursor<frame_range['endFrame']:
            unknown.append((cursor,frame_range['endFrame']));reviews.append({'code':'AUDIO_COVERAGE_GAP','speakerId':sid,'startFrame':cursor,'endFrame':frame_range['endFrame']})
    duplicates=set()
    for i,j in itertools.combinations(range(len(decoded)),2):
        if channels[i]['speakerId']==channels[j]['speakerId']:continue
        a,b=decoded[i],decoded[j]; lo=max(a['sessionOrigin'],b['sessionOrigin']); hi=min(a['sessionOrigin']+len(a['samples'])/SR,b['sessionOrigin']+len(b['samples'])/SR)
        correlations=[]; gains=[]
        for pos in np.linspace(lo,max(lo,hi-1),5):
            ix=round((pos-a['sessionOrigin'])*SR); iy=round((pos-b['sessionOrigin'])*SR)
            x=np.asarray(a['samples'][ix:ix+SR],dtype=np.float64); y=np.asarray(b['samples'][iy:iy+SR],dtype=np.float64)
            if len(x)!=len(y) or len(x)<100 or np.std(x)<1e-4 or np.std(y)<1e-4: continue
            correlations.append(abs(float(np.corrcoef(x,y)[0,1]))); gains.append(float(np.dot(x,y)/np.dot(x,x)))
        if len(correlations)>=3 and min(correlations)>.999 and np.std(gains)<max(.005,abs(np.mean(gains))*.02):
            duplicates.update((channels[i]['speakerId'],channels[j]['speakerId'])); unknown.append((_frame(lo,fps),_frame(hi,fps)))
            reviews.append({'code':'DUPLICATE_CHANNELS','speakers':[channels[i]['speakerId'],channels[j]['speakerId']],'correlations':correlations,'gainRatios':gains})
    patterns={}
    for example in settings.get('calibration',[]):
        if not isinstance(example,dict) or type(example.get('startFrame')) is not int or type(example.get('endFrame')) is not int or example['startFrame']<0 or example['endFrame']<=example['startFrame']:
            raise CutError('INVALID_AUDIO_INPUT','Calibration requires a bounded solo frame range.')
        sid=example.get('speakerId'); lo=_seconds(example['startFrame'],fps); hi=_seconds(example['endFrame'],fps); levels={};bindings={};bounds={}
        for channel,info in zip(channels,decoded):
            if lo<info['sessionOrigin']-1/SR or hi>info['sessionOrigin']+len(info['samples'])/SR+1/SR:continue
            a=max(0,round((lo-info['sessionOrigin'])*SR)); b=min(len(info['samples']),round((hi-info['sessionOrigin'])*SR))
            if b>a:
                person=channel['speakerId'];key=source_key(channel)
                if person==sid and example.get('inputKey') and example['inputKey']!=key:continue
                if person in bindings:raise CutError('CALIBRATION_AMBIGUOUS','Select a unique microphone segment for this solo example.')
                bindings[person]=key;bounds[person]=(info['sessionOrigin'],info['sessionOrigin']+len(info['samples'])/SR)
                levels[person]=float(np.sqrt(np.mean(np.square(info['samples'][a:b],dtype=np.float64))))
        if levels.get(sid,0)>1e-4:
            span=bounds[sid];valid_lo=example.get('validStartFrame',_frame(span[0],fps));valid_hi=example.get('validEndFrame',_frame(span[1],fps))
            if type(valid_lo) is not int or type(valid_hi) is not int or valid_lo>=valid_hi or valid_lo<_frame(span[0],fps) or valid_hi>_frame(span[1],fps):raise CutError('INVALID_AUDIO_INPUT','Calibration validity must stay inside its recording segment.')
            patterns.setdefault(sid,[]).append({'levels':{other:level/levels[sid] for other,level in levels.items()},'inputs':bindings,'startFrame':valid_lo,'endFrame':valid_hi})
    starts={}
    for sid,rows in steps.items():rows.sort(key=lambda r:r[0]);starts[sid]=[r[0] for r in rows]
    turns=[]; bleed_count=0; ongoing={}; calibration_reviews=[]
    for frame in range(frame_range['startFrame'],frame_range['endFrame']):
        if frame%100==0: check_cancel(cancel)
        t=_seconds(frame,fps); levels={}; candidates=[];segments={}
        for sid,rows in steps.items():
            ix=bisect_right(starts[sid],t)-1
            if 0<=ix<len(rows):
                a,b,active,rms,p,key=rows[ix]
                if a<=t<b and active: candidates.append(sid); levels[sid]=rms;segments[sid]=key
        applicable={sid:next((p['levels'] for p in reversed(patterns.get(sid,[])) if p['startFrame']<=frame<p['endFrame'] and all(p['inputs'].get(person)==key for person,key in segments.items())),None) for sid in candidates}
        retained=set(candidates)
        for dominant in candidates:
            pattern=applicable.get(dominant)
            if dominant in duplicates or not pattern: continue
            for sid in candidates:
                if sid!=dominant and sid not in duplicates and pattern.get(sid,1)<.5 and levels.get(sid,0)<=levels.get(dominant,0)*pattern.get(sid,0)*float(settings.get('bleedTolerance',1.8)):
                    retained.discard(sid); bleed_count+=1
        if len(retained)>1:
            unresolved=sorted(sid for sid in retained if not applicable.get(sid) or not set(candidates).issubset(applicable[sid]))
            if unresolved:
                unknown.append((frame,frame+1))
                if calibration_reviews and calibration_reviews[-1]['speakers']==unresolved and calibration_reviews[-1]['endFrame']==frame:
                    calibration_reviews[-1]['endFrame']=frame+1
                else:
                    calibration_reviews.append({'code':'BLEED_CALIBRATION_REQUIRED','speakers':unresolved,'startFrame':frame,'endFrame':frame+1})
        for sid in list(ongoing):
            if sid not in retained: turns.append((_seconds(ongoing.pop(sid),fps),_seconds(frame,fps),sid))
        for sid in retained: ongoing.setdefault(sid,frame)
    for sid,start in ongoing.items(): turns.append((_seconds(start,fps),_seconds(frame_range['endFrame'],fps),sid))
    if bleed_count: reviews.append({'code':'BLEED_SUPPRESSED','channelFrames':bleed_count,'calibrationPatterns':patterns})
    reviews.extend(calibration_reviews)
    missing_calibration=sorted(set(steps)-set(patterns))
    if len(channels)>1 and missing_calibration: reviews.append({'code':'BLEED_CALIBRATION_REQUIRED','speakers':missing_calibration})
    return turns,valid,reviews,unknown,model.revision,evidence


def analyze_audio(mode,sources,settings,cancel=None):
    if mode not in ('separate','mixed') or not sources: raise CutError('INVALID_AUDIO_INPUT','Choose separate or mixed recording mode.')
    fps=settings['fps']; offsets=settings.get('offsets',{}); source_map={source_key(s):s for s in sources}
    if len(source_map)!=len(sources):raise CutError('INVALID_AUDIO_INPUT','Recording inputs require distinct instance identities.')
    hashes={s['path']:media_digest(s['path'],cancel) for s in sources}
    for s in sources: _probe(s,settings,cancel)
    with tempfile.TemporaryDirectory(prefix='contentrium-analysis-') as directory:
        decoded=[]
        try:
            if mode=='mixed':
                for n,source in enumerate(sources):
                    info=_decode(source,directory,n,settings,cancel,mixed=True);decoded.append(info)
                    info['sessionOrigin']=float(source['sequenceStartSeconds'])+info['origin']-source.get('sourceStartSeconds',0) if 'sequenceStartSeconds' in source else info['origin']+float(offsets.get(source['assetId'],source.get('offsetSeconds',0)))
                ordered=sorted(decoded,key=lambda i:i['sessionOrigin'])
                if any(a['sessionOrigin']+len(a['samples'])/SR>b['sessionOrigin']+1/SR for a,b in zip(ordered,ordered[1:])):
                    raise CutError('SOURCE_ASSIGNMENT_CONFLICT','Mixed recording placements may not overlap in the session.')
                frame_range=settings.get('range',{'startFrame':min(_frame(i['sessionOrigin'],fps) for i in decoded),'endFrame':max(_frame(i['sessionOrigin']+len(i['samples'])/SR,fps) for i in decoded)})
                engine=CommunityDiarizer(ModelManager(settings.get('modelRoot'),cancel=cancel),settings,cancel)
                turns,valid,reviews,unknown,evidence=diarize_chunks(sources,decoded,engine,settings,fps,directory,cancel);revision=engine.revision
                if settings.get('speakerCount') and len({s for _,_,s in turns})!=int(settings['speakerCount']):reviews.append({'code':'SPEAKER_COUNT_CONFLICT','expected':int(settings['speakerCount']),'actual':len({s for _,_,s in turns})})
                reviews.extend(getattr(engine,'reviews',[]))
            else:
                channels=[dict(c) for c in (settings.get('channels') or [{'assetId':s['assetId'],'inputKey':source_key(s),'speakerId':chr(65+i),'channelIndex':s.get('channelIndex',0)} for i,s in enumerate(sources)])]
                selected_sources=[]
                for n,c in enumerate(channels):
                    key=c.get('inputKey') or c.get('instanceKey') or c['assetId']
                    if key not in source_map: raise CutError('MISSING_AUDIO','Assigned microphone source is missing.')
                    source=dict(source_map[key]); source['channelIndex']=c.get('channelIndex',source.get('channelIndex',0));source['streamIndex']=c.get('streamIndex',source.get('streamIndex',0))
                    for field in ('instanceKey','inputKey'):
                        if field in source:c.setdefault(field,source[field])
                    selected_sources.append(source)
                    info=_decode(source,directory,n,settings,cancel); decoded.append(info)
                    info['sessionOrigin']=float(source['sequenceStartSeconds'])+info['origin']-source.get('sourceStartSeconds',0) if 'sequenceStartSeconds' in source else info['origin']+float(offsets.get(c['assetId'],source.get('offsetSeconds',0)))
                check_assignments(channels,selected_sources,decoded)
                frame_range=settings.get('range',{'startFrame':min(_frame(i['sessionOrigin'],fps) for i in decoded),'endFrame':max(_frame(i['sessionOrigin']+len(i['samples'])/SR,fps) for i in decoded)})
                turns,valid,reviews,unknown,revision,evidence=_separate(decoded,channels,settings,fps,frame_range,cancel)
            check_cancel(cancel)
            source_inputs=[]
            for source,info in zip(sources if mode=='mixed' else selected_sources,decoded):
                if media_digest(source['path'],cancel)!=hashes[source['path']]:raise CutError('SOURCE_CHANGED','Source media changed during analysis.')
                source_inputs.append(dict(source,path=info['path'],sha256=hashes[source['path']],sessionOriginSeconds=info['sessionOrigin'],requestedSourceStartSeconds=source.get('sourceStartSeconds',0),sourceStartSeconds=info['origin'],audioStreamOriginSeconds=info['audioStreamOriginSeconds'],sourcePtsOriginSeconds=info['sourcePtsOriginSeconds'],sourceSampleRate=info['sampleRate'],decodedSampleRate=SR,decodedSampleCount=len(info['samples'])))
            intervals=_intervals(turns,valid,fps,frame_range,unknown)
            timeline=[{'startFrame':r['startFrame'],'endFrame':r['endFrame'],'startTicks':frame_ticks(r['startFrame'],fps),'endTicks':frame_ticks(r['endFrame'],fps)} for r in intervals]
            return {'schemaVersion':1,'modelRevision':revision,'intervals':intervals,
                    'sessionSpeakerIds':sorted({s for _,_,s in turns}),'reviews':reviews,'validAudioRanges':valid,'evidence':evidence,'sourceInputs':source_inputs,'timelineEvidence':timeline,'fps':fps,'mode':mode}
        finally:
            for info in decoded: info['samples']._mmap.close()
