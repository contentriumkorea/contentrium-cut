const test=require('node:test'),assert=require('node:assert/strict');
const {create,INTENT_KEY}=require('../plugin/workflow');
const {controller}=require('../plugin/mutation');
function storage(){const rows=new Map();return {rows,get length(){return rows.size;},key:i=>[...rows.keys()][i],getItem:async k=>rows.get(k),setItem:async(k,v)=>rows.set(k,v),removeItem:async k=>rows.delete(k)};}
function fixture(overrides={}){
  const saved=storage(),events=[];
  const api=async(path,body)=>{
    events.push({path,body});
    if(path==='/apply/begin'){assert.ok(saved.rows.has(INTENT_KEY));return {applyId:'lease',epoch:3,execute:true,plan:{planHash:'plan'}};}
    if(path==='/apply/check')return {execute:true};
    if(path==='/apply/end')return {status:body.status};
    if(path==='/apply/recover')return {resolved:true};
    return {};
  };
  const w=create({api,storage:saved,randomId:()=> 'request-1',...overrides});
  return {w,saved,events,api};
}
const host={hash:JSON.stringify,transaction:(_p,_name,build)=>build({})};
function operation(native){return {kind:'edit',body:{planHash:'plan',snapshotHash:'source',epoch:3},native,receipt:()=>({planHash:'plan',resultSequenceRef:'result',saved:true})};}
test('durable request, one-use batch, result and saved completion preserve order',async()=>{
  const f=fixture();let changes=0;
  await f.w.run(operation(async(_approved,control)=>{
    const m=controller(host,'source',control);
    await m.run('clone',()=>{changes++;return 'result';});await m.result('result');
    await m.run('cut',()=>changes++);return {};
  }));
  assert.equal(changes,2);assert.equal(f.saved.rows.size,0);
  assert.deepEqual(f.events.map(e=>e.path),['/apply/begin','/apply/check','/apply/check','/apply/batch-end','/apply/result','/apply/check','/apply/check','/apply/batch-end','/apply/end']);
  assert.equal(f.events[2].body.batchId,1);assert.equal(f.events[6].body.batchId,2);assert.equal(f.events[6].body.resultSequenceRef,'result');
});
test('an unreadable existing client intent cannot become a fresh edit',async()=>{
  const saved=storage();saved.rows.set(INTENT_KEY,'encrypted');saved.getItem=async()=>{throw new Error('decrypt failed');};
  let calls=0;const w=create({storage:saved,randomId:()=> 'fresh',api:async()=>calls++});
  await assert.rejects(w.run(operation(()=>{throw new Error('native must not run');})),{code:'EDIT_INTENT_STORAGE_UNAVAILABLE'});assert.equal(calls,0);
});
test('intent write failure occurs before contacting the service',async()=>{
  const saved=storage();saved.setItem=async()=>{throw new Error('disk');};let calls=0;
  const w=create({storage:saved,randomId:()=> 'id',api:async()=>calls++});
  await assert.rejects(w.run(operation(()=>{})),/disk/);assert.equal(calls,0);
});
test('lost begin response retains request and forbids another clone',async()=>{
  let calls=0,changes=0;const f=fixture({api:async path=>{if(path==='/apply/recover')return {resolved:true};calls++;throw new Error('connection lost');}});
  await assert.rejects(f.w.run(operation(()=>changes++)),/connection lost/);
  assert.equal((await f.w.pending()).requestId,'request-1');
  await assert.rejects(f.w.run(operation(()=>changes++)),{code:'APPLY_RECOVERY_REQUIRED'});assert.equal(calls,1);assert.equal(changes,0);
  await f.w.recover();assert.equal(await f.w.pending(),null);
});
test('a replayed permit never mutates or sends a contradictory end',async()=>{
  const calls=[];let changes=0;const f=fixture({api:async path=>{calls.push(path);return {applyId:'lease',epoch:3,execute:false,status:'completed'};}});
  await assert.rejects(f.w.run(operation(()=>changes++)),{code:'APPLY_REPLAY_OR_UNCERTAIN'});
  assert.equal(changes,0);assert.deepEqual(calls,['/apply/begin']);assert.ok(await f.w.pending());
});
test('lost batch receipt stops later SDK work and retains recovery intent',async()=>{
  let changes=0;const f=fixture();const w=create({storage:f.saved,randomId:()=> 'id',api:async(path,body)=>{if(path==='/apply/batch-end')throw new Error('receipt lost');return f.api(path,body);}});
  await assert.rejects(w.run(operation(async(_approved,control)=>{
    const m=controller(host,'source',control);await m.run('clone',()=>changes++);await m.run('cut',()=>changes++);
  })),/receipt lost/);
  assert.equal(changes,1);assert.ok(await w.pending());
  const end=f.events.find(e=>e.path==='/apply/end');assert.equal(end.body.status,'failed');assert.deepEqual(end.body.receipt,{resultSequenceRef:null});
});
test('cancellation cannot release an outstanding asynchronous native batch',async()=>{
  let stop=false,release,entered;const started=new Promise(resolve=>entered=resolve),held=new Promise(resolve=>release=resolve);
  const f=fixture({stopped:()=>stop});let changes=0;
  const run=f.w.run(operation(async(_approved,control)=>{
    const m=controller(host,'source',control);await m.run('clone',async()=>{changes++;entered();await held;});await m.run('cut',()=>changes++);
  }));
  await started;stop=true;await new Promise(resolve=>setImmediate(resolve));
  assert.equal(f.events.some(e=>e.path==='/apply/batch-end'||e.path==='/apply/end'),false);
  release();await assert.rejects(run,{code:'CANCELED'});assert.equal(changes,1);
  assert.equal(f.events.at(-1).body.status,'canceled');assert.ok(await f.w.pending());
});
test('lost final response never rewrites possible completed edit as failed',async()=>{
  const f=fixture();const w=create({storage:f.saved,randomId:()=> 'id',api:async(path,body)=>{const result=await f.api(path,body);if(path==='/apply/end')throw new Error('lost final');return result;}});
  await assert.rejects(w.run(operation(async()=>({}))),/lost final/);
  const ends=f.events.filter(e=>e.path==='/apply/end');assert.equal(ends.length,1);assert.equal(ends[0].body.status,'completed');assert.ok(await w.pending());
});
test('recovery needs server resolution and leaves unresolved records intact',async()=>{
  const f=fixture({api:async()=>({resolved:false})});f.saved.rows.set(INTENT_KEY,JSON.stringify({schemaVersion:1,kind:'edit',requestId:'old',applyId:'old-lease'}));
  await assert.rejects(f.w.recover(),{code:'APPLY_RECOVERY_REQUIRED'});assert.equal((await f.w.pending()).applyId,'old-lease');
});
test('input creation uses the same journal with its verified capability',async()=>{
  const f=fixture({api:async(path,body)=>path==='/input/begin'?{execute:true,applyId:'input',epoch:3,planHash:'derived',capability:{capabilityId:'trusted'}}:path==='/apply/end'?{status:body.status}:{}});
  await f.w.run({kind:'input',body:{capabilityId:'trusted',choices:[],epoch:3},beginPath:'/input/begin',native:async(a,c)=>{assert.equal(c.capability.capabilityId,'trusted');return {};},receipt:approved=>({planHash:approved.planHash})});
  assert.equal(await f.w.pending(),null);
});
for(const unreadable of ['unavailable','corrupt']){
  test('authoritative recovery can resolve '+unreadable+' intent only after explicit server approval',async()=>{
    const saved=storage(),calls=[];saved.rows.set(INTENT_KEY,unreadable==='corrupt'?'{broken':'encrypted');
    if(unreadable==='unavailable')saved.getItem=async()=>{throw new Error('decrypt failed');};
    const w=create({storage:saved,randomId:()=> 'unused',api:async(path,body)=>{assert.equal(saved.rows.has(INTENT_KEY),true);calls.push({path,body});return {resolved:true};}});
    await w.recover();assert.equal(saved.rows.has(INTENT_KEY),false);
    assert.deepEqual(calls,[{path:'/apply/recover',body:{requestId:null,applyId:null,acknowledged:true}}]);
  });
  for(const response of ['denied','failed'])test(response+' recovery preserves '+unreadable+' intent and the editing block',async()=>{
    const saved=storage();saved.rows.set(INTENT_KEY,unreadable==='corrupt'?'{broken':'encrypted');
    if(unreadable==='unavailable')saved.getItem=async()=>{throw new Error('decrypt failed');};
    let contacted=false,native=false;const w=create({storage:saved,randomId:()=> 'unused',api:async path=>{contacted=true;assert.equal(path,'/apply/recover');if(response==='failed')throw new Error('transport');return {resolved:false};}});
    await assert.rejects(w.recover(),response==='failed'?/transport/:{code:'APPLY_RECOVERY_REQUIRED'});assert.equal(contacted,true);assert.equal(saved.rows.has(INTENT_KEY),true);
    await assert.rejects(w.run(operation(()=>{native=true;})),{code:unreadable==='unavailable'?'EDIT_INTENT_STORAGE_UNAVAILABLE':'EDIT_INTENT_CORRUPT'});assert.equal(native,false);
  });
}


for(const stage of ['read','recover','verify'])test('recovery scope discards '+stage+' completion and keeps record',async()=>{
 const saved=storage(),raw=JSON.stringify({schemaVersion:1,kind:'edit',requestId:'old'});saved.rows.set(INTENT_KEY,raw);let active=true,release,reads=0,calls=0;const read=saved.getItem;
 saved.getItem=async k=>{reads++;if(stage==='read'&&reads===1||stage==='verify'&&reads===2)await new Promise(r=>release=r);return read(k);};
 const w=create({storage:saved,randomId:()=> 'unused',api:async()=>{calls++;if(stage==='recover')await new Promise(r=>release=r);return {resolved:true};}});
 const run=w.recover({current:()=>active});for(let i=0;i<50&&!release;i++)await Promise.resolve();assert.equal(typeof release,'function');active=false;release();await assert.rejects(run,{code:'CANCELED'});assert.equal(saved.rows.get(INTENT_KEY),raw);assert.equal(calls,stage==='read'?0:1);
});
for(const raw of [JSON.stringify({schemaVersion:1,kind:'edit',requestId:'old'}),'{broken',null])test('recovery preserves replacement of '+raw,async()=>{
 const f=fixture({api:async()=>{f.saved.rows.set(INTENT_KEY,'new record');return {resolved:true};}});if(raw!==null)f.saved.rows.set(INTENT_KEY,raw);await assert.rejects(f.w.recover(),{code:'EDIT_INTENT_CHANGED'});assert.equal(f.saved.rows.get(INTENT_KEY),'new record');
});
test('recovery serializes its pending storage and server work against another recovery or edit',async()=>{
 let release;const f=fixture({api:async()=>{await new Promise(r=>release=r);return {resolved:true};}});const run=f.w.recover();for(let i=0;i<50&&!release;i++)await Promise.resolve();await assert.rejects(f.w.recover(),{code:'APPLY_BUSY'});await assert.rejects(f.w.run(operation(()=>{throw new Error('must not edit');})),{code:'APPLY_BUSY'});release();await run;await f.w.recover({current:()=>false}).then(()=>assert.fail('must reject'),e=>assert.equal(e.code,'CANCELED'));
});
for(const result of [null,[],{}, {resolved:1},{resolved:'true'}])test('recovery rejects nonapproved typed receipt '+JSON.stringify(result),async()=>{const f=fixture({api:async()=>result});f.saved.rows.set(INTENT_KEY,'{broken');await assert.rejects(f.w.recover(),{code:'APPLY_RECOVERY_REQUIRED'});assert.equal(f.saved.rows.get(INTENT_KEY),'{broken');});

test('recovery compares exact bytes even when invalid UTF8 decodes the same',async()=>{const saved=storage();saved.rows.set(INTENT_KEY,new Uint8Array([255]));const w=create({storage:saved,randomId:()=> 'unused',api:async()=>{saved.rows.set(INTENT_KEY,new Uint8Array([254]));return {resolved:true};}});await assert.rejects(w.recover(),{code:'EDIT_INTENT_CHANGED'});assert.deepEqual(saved.rows.get(INTENT_KEY),new Uint8Array([254]));});
test('recovery cannot remove a readable replacement after an unavailable first read',async()=>{const saved=storage();saved.rows.set(INTENT_KEY,'new record');let reads=0;const get=saved.getItem;saved.getItem=k=>{if(++reads===1)throw new Error('owned unavailable');return get(k);};const w=create({storage:saved,randomId:()=> 'unused',api:async()=>({resolved:true})});await assert.rejects(w.recover(),{code:'EDIT_INTENT_CHANGED'});assert.equal(saved.rows.get(INTENT_KEY),'new record');});
