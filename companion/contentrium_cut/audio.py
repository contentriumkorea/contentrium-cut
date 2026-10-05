"""Local audio evidence; PCM is disk backed and source media is never modified."""
import itertools
import json
import math
import os
import shutil
import subprocess
import tempfile
import time
from fractions import Fraction
from pathlib import Path

import numpy as np
from scipy.signal import correlate, resample_poly
from .contract import CutError
from .models import ModelManager, SileroVAD, CommunityDiarizer, check_cancel

SR = 16000


def _frame(seconds, fps):
    return math.floor(Fraction(str(seconds))*int(fps['num'])/int(fps['den'])+Fraction(1,2))


def _seconds(frame, fps):
    return float(Fraction(int(frame)*int(fps['den']),int(fps['num'])))


def _run(argv, cancel=None, stdout=None):
    check_cancel(cancel)
    with tempfile.TemporaryFile() as errors, tempfile.TemporaryFile() as captured:
        child=subprocess.Popen(argv,stdout=stdout if stdout is not None else captured,stderr=errors,shell=False)
        try:
            while child.poll() is None:
                check_cancel(cancel)
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


def _probe(source, settings=None, cancel=None):
    path=Path(source.get('path',''))
    if not path.is_file(): raise CutError('MISSING_AUDIO','Selected source is missing.',{'assetId':source.get('assetId')})
    executable=(settings or {}).get('ffmpeg') or source.get('ffmpeg') or os.environ.get('CONTENTRIUM_FFMPEG') or shutil.which('ffmpeg')
    if not executable or not Path(executable).is_file(): raise CutError('FFMPEG_NOT_READY','Configure a local FFmpeg executable.')
    probe=str(Path(executable).with_name('ffprobe.exe' if os.name=='nt' else 'ffprobe'))
    data=json.loads(_run([probe,'-v','error','-show_streams','-show_format','-of','json',str(path.resolve())],cancel))
    streams=[s for s in data.get('streams',[]) if s.get('codec_type')=='audio']
    index=int(source.get('streamIndex',0)); channel=int(source.get('channelIndex',0))
    if index<0 or index>=len(streams): raise CutError('MISSING_AUDIO','Selected stream has no audio.')
    stream=streams[index]
    if channel<0 or channel>=int(stream['channels']): raise CutError('MISSING_AUDIO','Selected channel does not exist.')
    return {'path':str(path.resolve()),'ffmpeg':str(executable),'stream':index,'channel':channel,'channels':int(stream['channels']),
            'origin':float(stream.get('start_time',0) or 0),'size':path.stat().st_size,'mtimeNs':path.stat().st_mtime_ns}


def _verify_mixed_channels(argv, info, target, source, cancel):
    """Inspect disk-backed channels before averaging an unselected stereo mix."""
    audit = target.with_name('channels-' + target.name)
    with audit.open('wb') as output:
        _run(argv + ['-ar', str(SR), '-ac', str(info['channels']), '-f', 'f32le', 'pipe:1'], cancel, stdout=output)
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


def _decode(source,directory,number,settings=None,cancel=None,start=None,duration=None,mixed=False):
    if start is None:start=source.get('sourceStartSeconds')
    if duration is None:duration=source.get('durationSeconds')
    info=_probe(source,settings,cancel); target=Path(directory)/('pcm-%s.f32'%number)
    argv=[info['ffmpeg'],'-v','error','-nostdin','-i',info['path']]
    if start is not None: argv+=['-ss',str(max(0.,start))]
    if duration is not None: argv+=['-t',str(max(0.,duration))]
    argv+=['-map','0:a:%s'%info['stream']]
    if mixed:
        if 'channelIndex' in source:
            argv+=['-af','pan=mono|c0=c%d'%info['channel']]
            info['channelSelection'] = {'mode': 'explicit', 'channelIndex': info['channel']}
        elif info['channels']>1:
            _verify_mixed_channels(argv, info, target, source, cancel)
            expr='+'.join('%s*c%d'%(1/info['channels'],c) for c in range(info['channels']))
            argv+=['-af','pan=mono|c0='+expr]
            info['channelSelection'] = {'mode': 'verified-average', 'channelCount': info['channels']}
        else:
            info['channelSelection'] = {'mode': 'mono', 'channelIndex': 0}
    else: argv+=['-af','pan=mono|c0=c%d'%info['channel']]
    argv+=['-ar',str(SR),'-ac','1','-f','f32le','pipe:1']
    with target.open('wb') as f: _run(argv,cancel,stdout=f)
    if not target.stat().st_size: raise CutError('MISSING_AUDIO','No audio samples were decoded.')
    info.update(samples=np.memmap(target,dtype='<f4',mode='r'),origin=info['origin']+(start or 0),pcmPath=str(target))
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
    model=SileroVAD(ModelManager(settings.get('modelRoot'))); steps={}; reviews=[]; unknown=[]; valid=[]; evidence=[]
    for channel,info in zip(channels,decoded):
        sid=channel['speakerId']; offset=info['sessionOrigin']; samples=info['samples']; active=False; rows=[]; sample_ranges=[]; run_start=None
        for a,b,p in model.probabilities(samples,cancel):
            if p>=float(settings.get('vadThreshold',.5)): active=True
            elif p<float(settings.get('vadEndThreshold',.35)): active=False
            rms=float(np.sqrt(np.mean(np.square(samples[a:b],dtype=np.float64))))
            rows.append((offset+a/SR,offset+b/SR,active,rms,p))
            if active and run_start is None: run_start=a
            if not active and run_start is not None:
                sample_ranges.append({'startSample':run_start,'endSample':a}); run_start=None
        if run_start is not None: sample_ranges.append({'startSample':run_start,'endSample':len(samples)})
        steps[sid]=rows
        valid.append({'assetId':channel['assetId'],'speakerId':sid,'startFrame':_frame(offset,fps),'endFrame':_frame(offset+len(samples)/SR,fps),
                      'startSample':0,'endSample':len(samples),'sampleRate':SR,'sourceOriginSeconds':info['origin']})
        levels=[]; clipped=0
        for a in range(0,len(samples),SR):
            chunk=np.asarray(samples[a:a+SR]); levels.append(float(np.sqrt(np.mean(chunk.astype(np.float64)**2)))); clipped+=int(np.count_nonzero(np.abs(chunk)>=.999))
        evidence.append({'speakerId':sid,'noiseFloorRms':float(np.percentile(levels,10)),'levelRms':float(np.percentile(levels,90)),
                         'clippedSamples':clipped,'vadScoreRange':[min((r[4] for r in rows),default=0),max((r[4] for r in rows),default=0)],
                         'speechSampleRanges':sample_ranges,'sessionOriginSeconds':offset,'sampleRate':SR})
        coverage=valid[-1]
        for lo,hi in ((frame_range['startFrame'],coverage['startFrame']),(coverage['endFrame'],frame_range['endFrame'])):
            lo,hi=max(lo,frame_range['startFrame']),min(hi,frame_range['endFrame'])
            if lo<hi:
                unknown.append((lo,hi)); reviews.append({'code':'AUDIO_COVERAGE_GAP','assetId':channel['assetId'],'startFrame':lo,'endFrame':hi})
    duplicates=set()
    for i,j in itertools.combinations(range(len(decoded)),2):
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
        sid=example['speakerId']; lo=_seconds(example['startFrame'],fps); hi=_seconds(example['endFrame'],fps); levels={}
        for channel,info in zip(channels,decoded):
            a=max(0,round((lo-info['sessionOrigin'])*SR)); b=min(len(info['samples']),round((hi-info['sessionOrigin'])*SR))
            if b>a: levels[channel['speakerId']]=float(np.sqrt(np.mean(np.square(info['samples'][a:b],dtype=np.float64))))
        if levels.get(sid,0)>1e-4: patterns[sid]={other:level/levels[sid] for other,level in levels.items()}
    turns=[]; bleed_count=0; ongoing={}; calibration_reviews=[]
    for frame in range(frame_range['startFrame'],frame_range['endFrame']):
        if frame%100==0: check_cancel(cancel)
        t=_seconds(frame,fps); levels={}; candidates=[]
        for sid,rows in steps.items():
            ix=int((t-rows[0][0])*SR/512) if rows else -1
            if 0<=ix<len(rows):
                a,b,active,rms,p=rows[ix]
                if a<=t<b and active: candidates.append(sid); levels[sid]=rms
        retained=set(candidates)
        for dominant in candidates:
            pattern=patterns.get(dominant)
            if dominant in duplicates or not pattern: continue
            for sid in candidates:
                if sid!=dominant and sid not in duplicates and pattern.get(sid,1)<.5 and levels.get(sid,0)<=levels.get(dominant,0)*pattern.get(sid,0)*float(settings.get('bleedTolerance',1.8)):
                    retained.discard(sid); bleed_count+=1
        if len(retained)>1:
            unresolved=sorted(sid for sid in retained if sid not in patterns or not set(candidates).issubset(patterns[sid]))
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
    fps=settings['fps']; offsets=settings.get('offsets',{}); source_map={s['assetId']:s for s in sources}
    for s in sources: _probe(s,settings,cancel)
    with tempfile.TemporaryDirectory(prefix='contentrium-analysis-') as directory:
        decoded=[]
        try:
            if mode=='mixed':
                if len(sources)!=1: raise CutError('INVALID_AUDIO_INPUT','Mixed mode requires one selected recording.')
                source=sources[0]; info=_decode(source,directory,0,settings,cancel,mixed=True); decoded.append(info)
                origin=float(source['sequenceStartSeconds']) if 'sequenceStartSeconds' in source else info['origin']+float(offsets.get(source['assetId'],source.get('offsetSeconds',0))); info['sessionOrigin']=origin
                frame_range=settings.get('range',{'startFrame':_frame(origin,fps),'endFrame':_frame(origin+len(info['samples'])/SR,fps)})
                maximum=float(settings.get('maxMixedSeconds',7200))
                if len(info['samples'])/SR>maximum: raise CutError('AUDIO_RESOURCE_LIMIT','Whole-session diarization limit exceeded; explicit chunk identity linking is required.',{'maxMixedSeconds':maximum})
                engine=CommunityDiarizer(ModelManager(settings.get('modelRoot'))); wav=Path(directory)/'mixed.wav'
                _run([info['ffmpeg'],'-v','error','-nostdin','-f','f32le','-ar','16000','-ac','1','-i',info['pcmPath'],'-c:a','pcm_s16le',str(wav)],cancel)
                raw=engine.turns(wav,settings.get('speakerCount'),cancel)
                labels=sorted({r[2] for r in raw},key=lambda label:(min(a for a,b,s in raw if s==label),label))
                ids={label:chr(65+i) if i<26 else 'S%d'%(i+1) for i,label in enumerate(labels)}
                turns=[(origin+a,origin+b,ids[s]) for a,b,s in raw]
                valid=[{'assetId':source['assetId'],'startFrame':_frame(origin,fps),'endFrame':_frame(origin+len(info['samples'])/SR,fps),'startSample':0,'endSample':len(info['samples']),'sampleRate':SR,'sourceOriginSeconds':info['origin']}]
                unknown=[]; revision=engine.revision; evidence={'localToSessionIds':ids,'output':'speaker_diarization', 'channelSelection': info['channelSelection'],
                    'sourceIntervals':[{'startSample':round(a*SR),'endSample':round(b*SR),'localSpeakerId':s,'sessionSpeakerId':ids[s]} for a,b,s in raw]}; reviews=[]
                if settings.get('speakerCount') and len(ids)!=int(settings['speakerCount']): reviews.append({'code':'SPEAKER_COUNT_CONFLICT','expected':int(settings['speakerCount']),'actual':len(ids)})
            else:
                channels=settings.get('channels') or [{'assetId':s['assetId'],'speakerId':chr(65+i),'channelIndex':s.get('channelIndex',0)} for i,s in enumerate(sources)]
                if len({c['speakerId'] for c in channels})!=len(channels): raise CutError('INVALID_AUDIO_INPUT','Separate microphones require distinct session speaker IDs.')
                for n,c in enumerate(channels):
                    if c['assetId'] not in source_map: raise CutError('MISSING_AUDIO','Assigned microphone source is missing.')
                    source=dict(source_map[c['assetId']]); source['channelIndex']=c.get('channelIndex',source.get('channelIndex',0))
                    info=_decode(source,directory,n,settings,cancel); decoded.append(info)
                    info['sessionOrigin']=float(source['sequenceStartSeconds']) if 'sequenceStartSeconds' in source else info['origin']+float(offsets.get(c['assetId'],source.get('offsetSeconds',0)))
                frame_range=settings.get('range',{'startFrame':min(_frame(i['sessionOrigin'],fps) for i in decoded),'endFrame':max(_frame(i['sessionOrigin']+len(i['samples'])/SR,fps) for i in decoded)})
                turns,valid,reviews,unknown,revision,evidence=_separate(decoded,channels,settings,fps,frame_range,cancel)
            check_cancel(cancel)
            return {'schemaVersion':1,'modelRevision':revision,'intervals':_intervals(turns,valid,fps,frame_range,unknown),
                    'sessionSpeakerIds':sorted({s for _,_,s in turns}),'reviews':reviews,'validAudioRanges':valid,'evidence':evidence}
        finally:
            for info in decoded: info['samples']._mmap.close()
