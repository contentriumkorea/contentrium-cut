import hashlib
import json
from pathlib import Path
from PIL import Image

root=Path(__file__).resolve().parents[1];private=root/'docs'/'qa'/'private'
events=[json.loads(line) for line in (private/'host-events.jsonl').read_text(encoding='utf-8').splitlines()]
proofs=[e for e in events if e.get('value',{}).get('event')=='HOST_APPLY_PROOF']
observed=[]
for e in proofs:
    result=e['value']['value'];segments=result['segments'];snap=result['snapshot'];audio=[c for c in snap['clips'] if c['mediaType']=='audio']
    assert result['originalUnchanged'] and result['audioAndOverlaysUnchanged']
    assert [(int(c['startTicks'])//8467200000,int(c['endTicks'])//8467200000) for c in segments]==[(0,180),(180,360),(360,540),(540,720)]
    observed.append({'at':e['at'],'linkedAudio':len(audio)>1,'segmentFrames':[[0,180],[180,360],[360,540],[540,720]],'originalUnchanged':True,'audioUnchanged':True,'audioClipCount':len(audio),'resultSequenceRef':result['sequenceRef']})
assert any(p['linkedAudio'] for p in observed) and any(not p['linkedAudio'] for p in observed)
render=[]
for frame,dominant in [(0,0),(179,0),(180,1),(359,1),(360,2),(539,2),(540,0),(719,0)]:
    path=private/f'host-frame-{frame}.png';im=Image.open(path);pixel=im.getpixel((320,180));assert im.size==(640,360);assert max(range(3),key=lambda i:pixel[i])==dominant
    render.append({'frame':frame,'pixel':list(pixel),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
report={'hostVersion':'26.5.2','panelVersion':'0.1.0','kind':'actual Adobe UXP native transaction and rendered frames','apply':observed,'renderBoundarySamples':render,'limits':['Native Motion/Opacity defaults only; additional keyed effects still pending','Community-1 human overlap quality not established by this test']}
(root/'docs'/'qa'/'native-host-proof.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print('PASS: actual unlinked + linked audio readback, four segments, eight rendered boundary samples.')
