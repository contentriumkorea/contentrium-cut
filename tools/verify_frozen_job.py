"""Exercise an owned QA project through the frozen HTTP service, never log credentials."""
import argparse, json, time
from pathlib import Path
from urllib.request import Request, urlopen
ROOT=Path(__file__).resolve().parents[1]
PRIVATE=ROOT/'docs'/'qa'/'private'
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--code');args=parser.parse_args()
    credentials=None
    def api(path,body=None):
        headers={'Content-Type':'application/json','Connection':'close'}
        if credentials:headers['Authorization']='Bearer '+credentials['token']
        with urlopen(Request('http://localhost:41737'+path,data=None if body is None else json.dumps(body).encode(),headers=headers),timeout=30) as response:
            return json.load(response)
    if args.code:
        credentials=api('/pair',{'code':args.code,'instanceId':'contentrium-owned-frozen-qa'})
        (PRIVATE/'qa-session.json').write_text(json.dumps(credentials),encoding='utf-8')
    else:credentials=json.loads((PRIVATE/'qa-session.json').read_text(encoding='utf-8'))
    events=[json.loads(line) for line in (PRIVATE/'host-events.jsonl').read_text(encoding='utf-8').splitlines()]
    snapshot=next(e['value']['before'] for e in reversed(events) if e['value'].get('event')=='HOST_SYNC_PROOF')
    api('/project',{'snapshot':snapshot,'hostIdentity':None})
    state=api('/state')
    audio=next(c for c in snapshot['clips'] if c['mediaType']=='audio' and c['trackRef']=='audio:0')
    job=api('/jobs',{'kind':'analysis','epoch':state['epoch'],'options':{'mode':'separate','microphones':[{'instanceKey':audio['instanceKey'],'speakerId':'A','channelIndex':0}]}})
    deadline=time.monotonic()+120
    while time.monotonic()<deadline:
        result=api('/jobs/'+job['jobId'])
        if result['status'] in ['completed','failed','canceled']:break
        time.sleep(.25)
    if result['status']!='completed':raise RuntimeError(json.dumps(result))
    evidence={'frozenService':True,'appVersion':state['appVersion'],'jobStatus':result['status'],'result':result.get('result')}
    (PRIVATE/'frozen-job.json').write_text(json.dumps(evidence),encoding='utf-8')
    print(json.dumps({'frozenService':True,'appVersion':state['appVersion'],'jobStatus':result['status']}))
if __name__=='__main__':main()
