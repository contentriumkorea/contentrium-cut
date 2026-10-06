"""Replayable project corrections. Raw model evidence is immutable."""
import copy
import re
from .cache import result_digest,validate_result
from .contract import CutError,frame_ticks

def _require(value,message='Correction input is invalid.'):
    if not value:raise CutError('INVALID_CORRECTION',message)

def _compact(rows):
    result=[]
    for row in rows:
        row=copy.deepcopy(row)
        if result and result[-1]['endFrame']==row['startFrame'] and all(result[-1].get(k)==row.get(k) for k in ('speakers','unknown','reason')):result[-1]['endFrame']=row['endFrame']
        else:result.append(row)
    return result

def apply_operation(analysis,names,links,operation):
    _require(isinstance(operation,dict));kind=operation.get('type');ids=set(analysis['sessionSpeakerIds'])
    if kind=='name':
        sid=operation.get('speakerId');name=operation.get('name')
        _require(isinstance(sid,str) and sid in ids and isinstance(name,str) and 0<len(name.strip())<=100 and not any(ord(c)<32 for c in name));names[sid]=name.strip()
    elif kind=='merge':
        selected=operation.get('speakerIds');target=operation.get('targetSpeakerId')
        _require(isinstance(selected,list) and all(isinstance(s,str) for s in selected) and len(selected)>=2 and len(set(selected))==len(selected) and set(selected)<=ids and isinstance(target,str) and target in selected)
        for row in analysis['intervals']:row['speakers']=sorted({target if sid in selected else sid for sid in row['speakers']})
        for row in analysis['validAudioRanges']:
            if row.get('speakerId') in selected:row['speakerId']=target
        for sid in selected:
            if sid!=target:names.pop(sid,None)
        for candidate,sid in list(links.items()):
            if sid in selected:links[candidate]=target
        analysis['sessionSpeakerIds']=sorted(ids-set(selected)|{target});analysis['intervals']=_compact(analysis['intervals'])
    elif kind=='reassign':
        lo,hi=operation.get('startFrame'),operation.get('endFrame');speakers=operation.get('speakers');unknown=operation.get('unknown',False)
        additions=operation.get('newSpeakerIds',[])
        _require(isinstance(additions,list) and all(isinstance(s,str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}',s) and not s.startswith('U') for s in additions) and len(set(additions))==len(additions) and not set(additions)&ids)
        ids|=set(additions)
        _require(type(lo) is int and type(hi) is int and 0<=lo<hi and isinstance(speakers,list) and all(isinstance(s,str) for s in speakers) and len(set(speakers))==len(speakers) and set(speakers)<=ids and type(unknown) is bool)
        _require(set(additions)<=set(speakers));analysis['sessionSpeakerIds']=sorted(ids)
        covered=sum(max(0,min(hi,r['endFrame'])-max(lo,r['startFrame'])) for r in analysis['intervals'])
        _require(covered==hi-lo,'Correction cannot create source coverage across a missing recording.')
        rows=[]
        for row in analysis['intervals']:
            points=sorted({row['startFrame'],row['endFrame']}|{x for x in (lo,hi) if row['startFrame']<x<row['endFrame']})
            for a,b in zip(points,points[1:]):
                item=dict(row,startFrame=a,endFrame=b)
                if lo<=a and b<=hi:
                    item.update(speakers=sorted(speakers),unknown=unknown);item.pop('reason',None)
                    if unknown:item['reason']='MANUAL_REVIEW'
                rows.append(item)
        analysis['intervals']=_compact(rows)
    elif kind=='link':
        candidate=operation.get('candidateSpeakerId');target=operation.get('sessionSpeakerId');owned=analysis.get('evidence',{}).get('ownedTurns',[])
        _require(isinstance(candidate,str) and candidate not in links and any(r.get('candidateSpeakerId')==candidate for r in owned))
        _require(isinstance(target,str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}',target) and not target.startswith('U'))
        links[candidate]=target;analysis['sessionSpeakerIds']=sorted(ids|{target});rows=[]
        for row in analysis['intervals']:
            points=sorted({row['startFrame'],row['endFrame']}|{x for r in owned for x in (r['startFrame'],r['endFrame']) if row['startFrame']<x<row['endFrame']})
            for a,b in zip(points,points[1:]):
                item=dict(row,startFrame=a,endFrame=b);active={r.get('candidateSpeakerId') for r in owned if r.get('candidateSpeakerId') and r['startFrame']<=a and b<=r['endFrame']}
                if candidate in active:
                    conflict=any(r['startFrame']<=a and b<=r['endFrame'] for r in analysis.get('evidence',{}).get('boundaryConflicts',[]))
                    item['speakers']=sorted(set(item['speakers'])|{target});item['unknown']=bool(active-set(links)) or conflict
                    if not item['unknown']:item.pop('reason',None)
                rows.append(item)
        analysis['intervals']=_compact(rows)
    else:raise CutError('INVALID_CORRECTION','Unknown correction operation.')

def derive(raw,history):
    active=[]
    for entry in history:
        _require(isinstance(entry.get('operation'),dict))
        if entry['operation'].get('type')=='undo':
            _require(bool(active),'There is no active correction to undo.')
            target=entry['operation'].get('targetRevision',active[-1]['revision']);_require(target==active[-1]['revision'],'Undo must refer to the latest active correction.')
            active.pop()
        else:active.append(entry)
    analysis=copy.deepcopy(raw);names={};links={}
    for entry in active:apply_operation(analysis,names,links,entry['operation'])
    analysis['correctionRevision']=len(history);analysis['candidateLinks']=links
    if analysis.get('fps'):
        analysis['timelineEvidence']=[{'startFrame':r['startFrame'],'endFrame':r['endFrame'],'startTicks':frame_ticks(r['startFrame'],analysis['fps']),'endTicks':frame_ticks(r['endFrame'],analysis['fps'])} for r in analysis['intervals']]
    if isinstance(analysis.get('evidence'),dict):
        analysis['unresolvedSpeakerIds']=sorted({r['candidateSpeakerId'] for r in analysis['evidence'].get('ownedTurns',[]) if r.get('candidateSpeakerId') and r['candidateSpeakerId'] not in links})
        for review in analysis.get('reviews',[]):
            if review.get('candidateSpeakerId') in links:review['resolvedByCorrection']=True
    return analysis,names

def verify_artifact(value,project,snapshot):
    try:
        stored=copy.deepcopy(value);digest=stored.pop('artifactHash')
        if result_digest(stored)!=digest or value['schemaVersion']!=1 or value['projectRef']!=project or value['snapshotHash']!=snapshot or result_digest(value['raw'])!=value['analysisHash']:raise ValueError()
        validate_result('analysis',value['raw'])
        if value['revision']!=len(value['history']) or any(e['revision']!=i+1 for i,e in enumerate(value['history'])):raise ValueError()
        derive(value['raw'],value['history'])
    except (KeyError,TypeError,ValueError,AttributeError,OverflowError,RecursionError,CutError):raise CutError('ANALYSIS_SCOPE','Analysis history is missing, stale, or corrupt.')
    return value

def artifact_hash(value):
    result=copy.deepcopy(value);result.pop('artifactHash',None);result['artifactHash']=result_digest(result);return result

def solo_examples(analysis,fps):
    inputs={s.get('inputKey',s.get('instanceKey',s['assetId'])):s for s in analysis.get('sourceInputs',[])};examples=[];groups={}
    example_rows=list(analysis['intervals'])
    evidence=analysis.get('evidence',{}) if isinstance(analysis.get('evidence'),dict) else {}
    owned=evidence.get('ownedTurns',[]);conflicts=evidence.get('boundaryConflicts',[])
    points=sorted({x for rows in (owned,conflicts) for turn in rows for x in (turn['startFrame'],turn['endFrame'])})
    for start,end in zip(points,points[1:]):
        # Candidate identity uncertainty cannot erase contradictory context membership.
        if any(turn['startFrame']<end and start<turn['endFrame'] for turn in conflicts):continue
        labels={t.get('candidateSpeakerId') or t.get('sessionSpeakerId') for t in owned if t['startFrame']<=start and end<=t['endFrame']}
        if len(labels)!=1:continue
        candidate=next(iter(labels))
        if not candidate or not candidate.startswith('U') or candidate in analysis.get('candidateLinks',{}):continue
        for covered in analysis['intervals']:
            lo,hi=max(start,covered['startFrame']),min(end,covered['endFrame'])
            if lo<hi:example_rows.append({'startFrame':lo,'endFrame':hi,'speakers':[candidate],'unknown':False,'candidate':True})
    for row in example_rows:
        if row['unknown'] or len(row['speakers'])!=1:continue
        sid=row['speakers'][0]
        for coverage in analysis['validAudioRanges']:
            if coverage.get('speakerId',sid)!=sid:continue
            key=coverage.get('inputKey',coverage.get('instanceKey',coverage['assetId']));source=inputs.get(key)
            if not source:continue
            lo=max(row['startFrame'],coverage['startFrame']);hi=min(row['endFrame'],coverage['endFrame']);duration=(hi-lo)*fps['den']/fps['num']
            if duration<.5:continue
            duration=min(5.,duration);start=source.get('sourceStartSeconds',0)+(lo*fps['den']/fps['num']-source['sessionOriginSeconds'])
            rate=source.get('sourceSampleRate',16000)
            item={'speakerId':None if row.get('candidate') else sid,'candidateSpeakerId':sid if row.get('candidate') else None,'inputKey':key,'assetId':source['assetId'],'sourceSha256':source.get('sha256'),'streamIndex':source.get('streamIndex',0),'channelIndex':source.get('channelIndex'),'sourceStartSeconds':str(start),'durationSeconds':str(duration),'startFrame':lo,'endFrame':min(hi,lo+round(duration*fps['num']/fps['den'])),'sourceSampleRate':rate,'startSample':round(start*rate),'endSample':round((start+duration)*rate)}
            item['startSample']=round((start-source.get('audioStreamOriginSeconds',0))*rate);item['endSample']=round((start+duration-source.get('audioStreamOriginSeconds',0))*rate)
            item['exampleId']=result_digest(item);groups.setdefault(sid,[]).append(item)
    for sid,rows in sorted(groups.items()):
        # Spread representatives over the session instead of repeating one early utterance.
        indexes=sorted({0,len(rows)//2,len(rows)-1})
        examples.extend(rows[i] for i in indexes)
    return examples
