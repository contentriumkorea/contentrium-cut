"""Bounded diarization contexts; identity links require shared solo audio evidence."""
import math
from fractions import Fraction
from pathlib import Path
import numpy as np
from scipy.io import wavfile
from .contract import CutError
from .models import check_cancel
from .resource import positive_number

SR=16000

def _solo(turns):
    points=sorted({x for a,b,s in turns for x in (a,b)})
    result=[]
    for a,b in zip(points,points[1:]):
        labels={s for lo,hi,s in turns if lo<=a and b<=hi}
        if len(labels)==1:result.append((a,b,next(iter(labels))))
    return result

def _links(current,previous):
    scores={}
    for a,b,label in _solo(current):
        for c,d,target in _solo(previous):
            overlap=max(0,min(b,d)-max(a,c))
            if overlap:scores[label,target]=scores.get((label,target),0)+overlap
    links={}
    for label in {s for _,_,s in current}:
        candidates=sorted(((score,target) for (local,target),score in scores.items() if local==label),reverse=True)
        if not candidates:continue
        score,target=candidates[0];other=candidates[1][0] if len(candidates)>1 else 0
        competitors=[v for (local,t),v in scores.items() if t==target and local!=label]
        if score>=.3*SR and score>3*other and (not competitors or score>3*max(competitors)):links[label]=target
    return links

def _conflicts(current,previous,lo,hi):
    points=sorted({lo,hi}|{x for rows in (current,previous) for a,b,s in rows for x in (a,b) if lo<x<hi});result=[]
    for a,b in zip(points,points[1:]):
        left={s for x,y,s in previous if x<=a and b<=y};right={s for x,y,s in current if x<=a and b<=y}
        if left==right:continue
        if result and result[-1][1]==a:result[-1]=(result[-1][0],b)
        else:result.append((a,b))
    return result

def diarize_chunks(sources,decoded,engine,settings,fps,directory,cancel=None):
    maximum=round(positive_number(settings.get('mixedChunkSeconds',600),'mixedChunkSeconds')*SR)
    overlap=round(positive_number(settings.get('mixedOverlapSeconds',20),'mixedOverlapSeconds')*SR)
    if maximum>600*SR or overlap*2>=maximum:raise CutError('INVALID_AUDIO_SETTINGS','Chunk context must be at most 600 seconds and exceed twice its overlap.')
    core=maximum-2*overlap;turns=[];valid=[];unknown=[];reviews=[];chunks=[];owned=[];previous=[];seeded=False;next_id=0;next_candidate=0;legacy={};legacy_rows=[];boundary=[]
    def frame(t):return math.floor(Fraction(str(t))*fps['num']/fps['den']+Fraction(1,2))
    for source,info in sorted(zip(sources,decoded),key=lambda pair:pair[1]['sessionOrigin']):
        origin=info['sessionOrigin'];total=len(info['samples']);previous=[];previous_context=None
        valid.append({'assetId':source['assetId'],'inputKey':source.get('inputKey',source.get('instanceKey',source['assetId'])),'startFrame':frame(origin),'endFrame':frame(origin+total/SR),'startSample':0,'endSample':total,'sampleRate':SR,'sourceOriginSeconds':info['origin']})
        if source.get('instanceKey'):valid[-1]['instanceKey']=source['instanceKey']
        for lo in range(0,total,core):
            check_cancel(cancel);hi=min(total,lo+core);ctx_lo=max(0,lo-overlap);ctx_hi=min(total,hi+overlap)
            path=Path(directory)/('chunk-%d.wav'%len(chunks))
            samples=np.asarray(info['samples'][ctx_lo:ctx_hi],dtype=np.float32)
            wavfile.write(path,SR,(np.clip(samples,-1,1)*32767).astype(np.int16));del samples
            try:raw=engine.turns(path,settings.get('speakerCount') if total<=core and len(sources)==1 else None,cancel)
            finally:path.unlink(missing_ok=True)
            normalized=[]
            for a,b,label in raw:
                if isinstance(a,bool) or isinstance(b,bool) or not isinstance(a,(int,float)) or not isinstance(b,(int,float)) or not math.isfinite(a+b) or a<0 or b<=a or b>(ctx_hi-ctx_lo)/SR+1/SR or not isinstance(label,str) or not label:
                    raise CutError('MODEL_INFERENCE_FAILED','Diarization returned an invalid chunk interval.')
                normalized.append((ctx_lo+round(a*SR),min(ctx_hi,ctx_lo+round(b*SR)),label))
            labels=sorted({s for _,_,s in normalized},key=lambda s:(min(a for a,b,label in normalized if label==s),s))
            mapping=_links(normalized,previous)
            if not seeded and labels:
                for label in labels:
                    mapping[label]=chr(65+next_id) if next_id<26 else 'S%d'%(next_id+1);next_id+=1
                seeded=True;legacy=dict(mapping)
            for label in labels:
                if label not in mapping:
                    next_candidate+=1;mapping[label]='U%d'%next_candidate
                    reviews.append({'code':'CHUNK_IDENTITY_UNRESOLVED','candidateSpeakerId':mapping[label],'chunkId':len(chunks),'assetId':source['assetId']})
            linked=[(a,b,mapping[label]) for a,b,label in normalized]
            if previous_context:
                for a,b in _conflicts(linked,previous,max(ctx_lo,previous_context[0]),min(ctx_hi,previous_context[1])):
                    start,end=frame(origin+a/SR),frame(origin+b/SR)
                    if start<end:
                        unknown.append((start,end));item={'code':'CHUNK_BOUNDARY_CONFLICT','chunkId':len(chunks),'assetId':source['assetId'],'startFrame':start,'endFrame':end}
                        boundary.append(item);reviews.append(item)
            native_rate=info.get('sampleRate',SR);native_start=info['origin']-info.get('audioStreamOriginSeconds',0)
            chunk={'chunkId':len(chunks),'assetId':source['assetId'],'inputKey':valid[-1]['inputKey'],'contextStartSample':ctx_lo,'contextEndSample':ctx_hi,'startSample':lo,'endSample':hi,'sampleRate':SR,'sessionStartSample':round(origin*SR)+lo,'nativeStartSample':round((native_start+lo/SR)*native_rate),'nativeEndSample':round((native_start+hi/SR)*native_rate),'nativeSampleRate':native_rate,'modelRevision':engine.revision,'localToSessionIds':mapping}
            chunks.append(chunk)
            for a,b,label in normalized:
                ca,cb=max(a,lo),min(b,hi)
                if ca>=cb:continue
                start,end=frame(origin+ca/SR),frame(origin+cb/SR)
                if start>=end:continue
                target=mapping[label];candidate=target if target.startswith('U') else None
                row={'assetId':source['assetId'],'inputKey':valid[-1]['inputKey'],'chunkId':chunk['chunkId'],'startFrame':start,'endFrame':end,'startSample':ca,'endSample':cb,'sampleRate':SR,'localSpeakerId':label,'sessionSpeakerId':None if candidate else target,'candidateSpeakerId':candidate}
                owned.append(row)
                if candidate:unknown.append((start,end))
                else:turns.append((origin+ca/SR,origin+cb/SR,target))
                if len(sources)==1 and total<=core:legacy_rows.append({'startSample':ca,'endSample':cb,'localSpeakerId':label,'sessionSpeakerId':target})
            previous=linked;previous_context=(ctx_lo,ctx_hi)
    evidence={'chunks':chunks,'ownedTurns':owned,'boundaryConflicts':boundary,'localToSessionIds':legacy,'sourceIntervals':legacy_rows,'output':'speaker_diarization','channelSelection':decoded[0]['channelSelection'] if len(decoded)==1 else 'per-input'}
    return turns,valid,reviews,unknown,evidence
