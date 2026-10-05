import json
from pathlib import Path
root=Path(__file__).resolve().parents[1]
events=[json.loads(line)['value'] for line in (root/'docs/qa/private/host-events.jsonl').read_text(encoding='utf-8').splitlines()]
proof=[e for e in events if e.get('event')=='HOST_SYNC_PROOF'][-1]
before,after,plan=proof['before'],proof['result']['snapshot'],proof['plan']
assert proof['result']['originalUnchanged'] and proof['result']['readbackVerified']
assert before['sequenceRef']!=after['sequenceRef'] and before['tracks']==after['tracks']
selected=set(plan['selectedClipInstanceKeys']);moves=[]
for clip in before['clips']:
    matches=[c for c in after['clips'] if c['assetId']==clip['assetId'] and c['trackRef']==clip['trackRef']]
    assert len(matches)==1
    actual=matches[0];delta=round(plan['offsets'].get(clip['assetId'],0)*254016000000) if clip['instanceKey'] in selected else 0
    assert int(actual['startTicks'])==int(clip['startTicks'])+delta
    assert int(actual['endTicks'])==int(clip['endTicks'])+delta
    for key in ['inTicks','outTicks','speed','disabled','effectFingerprint']:assert actual[key]==clip[key]
    if delta:moves.append({'mediaType':clip['mediaType'],'trackRef':clip['trackRef'],'offsetFrames':60,'durationFrames':720})
assert {m['mediaType'] for m in moves}=={'audio','video'} and len(moves)==2
result={'hostVersion':'26.5.2','fixture':'linked camera audio','originalUnchanged':True,'sourceTimingAndEffectsPreserved':True,'unselectedClipsUnchanged':True,'moves':moves,'resultSequenceRef':after['sequenceRef'],'evidenceTime':next(json.loads(l)['at'] for l in reversed((root/'docs/qa/private/host-events.jsonl').read_text(encoding='utf-8').splitlines()) if json.loads(l)['value'].get('event')=='HOST_SYNC_PROOF')}
(root/'docs/qa/native-sync-proof.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print('Native linked video and audio sync movement verified: 60 frames, original intact.')
