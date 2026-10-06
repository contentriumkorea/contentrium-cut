'use strict';
// Cross-language integration: real connection/workflow/service; simulated host.
const test=require('node:test'),assert=require('node:assert/strict');
const {spawn}=require('node:child_process'),{createInterface}=require('node:readline');
const path=require('node:path'),crypto=require('node:crypto');
const connection=require('../plugin/connection'),workflow=require('../plugin/workflow');
const {controller}=require('../plugin/mutation');

function secureStorage(){
  const data=new Map();
  return {get length(){return data.size;},key:i=>[...data.keys()][i],getItem:async k=>data.get(k),setItem:async(k,v)=>data.set(k,v),removeItem:async k=>data.delete(k)};
}
async function peer(t){
  const child=spawn(path.join(__dirname,'../.build-venv/Scripts/python.exe'),['-B',path.join(__dirname,'panel_service_bridge.py')],
    {cwd:path.join(__dirname,'..'),env:{...process.env,PYTHONPATH:'companion;tests'},windowsHide:true,stdio:['pipe','pipe','pipe']});
  let stderr='',exited=false;const pending=[];
  child.stderr.on('data',chunk=>{stderr=(stderr+chunk.toString()).slice(-3000);});
  const lines=createInterface({input:child.stdout});
  lines.on('line',line=>{const next=pending.shift();if(next){try{next.resolve(JSON.parse(line));}catch(e){next.reject(e);}}});
  child.on('error',error=>{while(pending.length)pending.shift().reject(error);});
  child.on('exit',code=>{exited=true;while(pending.length)pending.shift().reject(new Error('Test peer exited '+code+': '+stderr));});
  const command=value=>new Promise((resolve,reject)=>{
    if(exited)return reject(new Error('Test peer closed: '+stderr));
    pending.push({resolve,reject});child.stdin.write(JSON.stringify(value)+'\n');
  });
  const oldFetch=global.fetch,oldCrypto=Object.getOwnPropertyDescriptor(globalThis,'crypto');
  Object.defineProperty(globalThis,'crypto',{configurable:true,value:crypto.webcrypto});
  let loss=null;const calls=[],lostResponses=[];
  global.fetch=async(url,options)=>{
    const request={command:'http',path:new URL(url).pathname,method:options.method,headers:{Host:'127.0.0.1:41737',...options.headers},body:options.body||''};
    calls.push(request);const response=await command(request);
    if(loss?.path===request.path&&loss.when(JSON.parse(request.body||'{}'))){
      const value=typeof response.body?.body==='string'?JSON.parse(response.body.body):response.body;
      // Drop the requested operation's actual result, after its one-shot take
      // has committed. Losing only the pending acknowledgement mints no permit.
      if(value?.continuation){loss={...loss,path:'/continuations/'+value.continuation.id+'/take',when:()=>true};}
      else{lostResponses.push({origin:loss.origin,path:request.path,value});loss=null;throw new Error('Simulated lost response');}
    }
    return {status:response.status,text:async()=>JSON.stringify(response.body)};
  };
  t.after(async()=>{
    global.fetch=oldFetch;if(oldCrypto)Object.defineProperty(globalThis,'crypto',oldCrypto);
    if(!exited){child.stdin.end();const timeout=setTimeout(()=>child.kill(),3000);await new Promise(resolve=>child.once('exit',resolve));clearTimeout(timeout);}
    lines.close();
  });
  const data=await command({command:'fixture'}),files=new Map([['contentrium-bootstrap.json',JSON.stringify(data.bootstrap)]]);
  const entry=name=>({name,isFile:true,read:async()=>files.get(name),write:async value=>files.set(name,value)});
  const uxp={storage:{localFileSystem:{getDataFolder:async()=>({getEntries:async()=>[...files.keys()].map(entry),createFile:async name=>{if(files.has(name))throw Error('exists');files.set(name,'');return entry(name);}})}}};
  let client=connection.create(uxp,data.bundle);
  const api=(...args)=>client.request(...args),saved=secureStorage();
  const flow=workflow.create({api,storage:saved,randomId:()=>crypto.randomBytes(16).toString('hex')});
  const heartbeat=()=>api('/heartbeat',{hostIdentity:data.host,panelVersion:data.bundle.appVersion,bundleId:data.bundle.bundleId,protocolVersion:data.bundle.protocolVersion,epoch:0,batchRunning:false,quiescent:true});
  async function ready(){
    await client.connect();await heartbeat();
    await api('/project',{snapshot:data.snapshot,hostIdentity:data.host,epoch:0});
    const job=await command({command:'seed'}),state=await api('/analyses/register',{jobId:job.jobId,epoch:0});
    return {job,state};
  }
  const plan=(job,state)=>api('/plan',{jobId:job.jobId,analysisId:state.analysisId,analysisRevision:state.revision,mapping:data.mapping,policy:data.policy,epoch:0});
  return {data,api,get client(){return client;},flow,calls,lostResponses,ready,plan,heartbeat,lose:(path,when=()=>true)=>{loss={path,origin:path,when};},
    restart:()=>command({command:'restart'}),exitHost:()=>command({command:'host_exit'}),replaceMedia:()=>command({command:'replace_media'}),
    loseLocalIdentity:async()=>{
      await saved.removeItem(workflow.INTENT_KEY);files.delete('contentrium-panel-identity.json');
      client=connection.create(uxp,data.bundle);await client.connect();
    }};
}
const host={hash:value=>crypto.createHash('sha256').update(JSON.stringify(value)).digest('hex')};
function edit(f,plan,mutations){
  return {kind:'edit',body:{planHash:plan.planHash,snapshotHash:f.data.snapshot.snapshotHash,epoch:0},
    native:async(approved,control)=>{
      assert.equal(approved.plan.planHash,plan.planHash);
      const mutate=controller(host,f.data.snapshot.sequenceRef,control);
      await mutate.run('clone',()=>mutations.push('clone'));await mutate.result('result-sequence');
      await mutate.run('cut',()=>mutations.push('cut'));
      return {sequenceRef:'result-sequence'};
    },receipt:()=>({planHash:plan.planHash,sourceSnapshotHash:f.data.snapshot.snapshotHash,resultSnapshotHash:'a'.repeat(64),
      resultSequenceRef:'result-sequence',sourceUnchanged:true,readback:{verified:true},saved:true})};
}

test('authenticated panel carries corrected analysis revisions into planning',async t=>{
  const f=await peer(t),{job,state}=await f.ready();
  const corrected=await f.api('/analyses/'+state.analysisId+'/correct',{expectedRevision:state.revision,operation:{type:'name',speakerId:'A',name:'화자 하나'},requestId:'rename-1',epoch:0});
  assert.equal(corrected.revision,state.revision+1);assert.equal(corrected.names.A,'화자 하나');
  await assert.rejects(f.plan(job,state),e=>e.code==='CORRECTION_REVISION_CONFLICT');
  const plan=await f.plan(job,corrected);assert.ok(plan.segments.length>0);
  assert.equal((await f.api('/state')).compatible,true);
});

test('same private owner restores a persisted completed analysis and corrections after service restart',async t=>{
  const f=await peer(t),{job,state}=await f.ready();
  await f.api('/analyses/'+state.analysisId+'/correct',{expectedRevision:0,operation:{type:'name',speakerId:'A',name:'진행자'},requestId:'saved-name',epoch:0});
  await f.restart();f.client.reset();await f.client.connect();await f.heartbeat();
  await f.api('/project',{snapshot:f.data.snapshot,hostIdentity:f.data.host,epoch:0});
  const completed=await f.api('/jobs/'+job.jobId),restored=await f.api('/analyses/'+state.analysisId);
  assert.equal(completed.kind,'analysis');assert.equal(completed.status,'completed');assert.equal(restored.analysisId,state.analysisId);
  assert.equal(restored.revision,1);assert.equal(restored.names.A,'진행자');assert.equal(restored.history.length,1);
  assert.ok((await f.plan(completed,restored)).segments.length>0);
  assert.equal(f.calls.some(call=>call.path==='/jobs'&&call.method==='POST'),false);
  assert.equal(f.calls.some(call=>call.path.startsWith('/apply/')),false);
});

test('same-path replaced audio bytes cannot restore, plan or authorize an existing analysis',async t=>{
  const f=await peer(t),{job,state}=await f.ready(),plan=await f.plan(job,state);
  assert.equal((await f.replaceMedia()).snapshotHash,f.data.snapshot.snapshotHash);
  const outcomes=[];
  for(const request of [()=>f.api('/analyses/'+state.analysisId),()=>f.plan(job,state),
    ()=>f.api('/apply/begin',{planHash:plan.planHash,snapshotHash:f.data.snapshot.snapshotHash,requestId:'replaced-media',epoch:0})]){
    try{await request();outcomes.push('accepted');}catch(error){outcomes.push(error.code);}
  }
  assert.deepEqual(outcomes,['SOURCE_CHANGED','SOURCE_CHANGED','SOURCE_CHANGED']);
});

test('media changed after apply begin cannot obtain the first native batch permit',async t=>{
  const f=await peer(t),{job,state}=await f.ready(),plan=await f.plan(job,state);
  const lease=await f.api('/apply/begin',{planHash:plan.planHash,snapshotHash:f.data.snapshot.snapshotHash,requestId:'change-after-begin',epoch:0});
  await f.replaceMedia();
  await assert.rejects(f.api('/apply/check',{applyId:lease.applyId,epoch:0,batchId:1,operationDigest:'d'.repeat(64),resultSequenceRef:null}),error=>error.code==='SOURCE_CHANGED');
});

test('real fresh resource defaults allow a device-only settings save',async t=>{
  const f=await peer(t);await f.ready();
  const fresh=await f.api('/resources');assert.equal('cacheBudgetBytes' in fresh.settings,false);
  const saved=await f.api('/resources',{settings:{device:'cuda'},epoch:0});
  assert.equal(saved.settings.device,'cuda');assert.equal('cacheBudgetBytes' in saved.settings,false);
});

test('real service consumes workflow permits once and replays durable completion after restart',async t=>{
  const f=await peer(t),{job,state}=await f.ready(),plan=await f.plan(job,state),mutations=[];
  await f.flow.run(edit(f,plan,mutations));assert.deepEqual(mutations,['clone','cut']);assert.equal(await f.flow.pending(),null);
  const begin=JSON.parse(f.calls.find(x=>x.path==='/apply/begin').body);
  const replay=await f.api('/apply/begin',begin);assert.equal(replay.execute,false);
  const completion=JSON.parse(f.calls.find(x=>x.path==='/apply/end').body);
  await f.restart();f.client.reset();await f.client.connect();
  const durable=await f.api('/apply/end',completion);assert.equal(durable.status,'completed');
  assert.deepEqual(mutations,['clone','cut']);
});

test('lost begin is recovered without ever entering the simulated native host',async t=>{
  const f=await peer(t),{job,state}=await f.ready(),plan=await f.plan(job,state),mutations=[];
  f.lose('/apply/begin');await assert.rejects(f.flow.run(edit(f,plan,mutations)),e=>e.code==='REQUEST_OUTCOME_UNKNOWN');
  assert.equal(f.lostResponses[0].value.execute,true);assert.match(f.lostResponses[0].path,/^\/continuations\/[0-9a-f]{32}\/take$/);
  assert.ok(await f.flow.pending());assert.deepEqual(mutations,[]);
  const recovered=await f.flow.recover();assert.equal(recovered.resolved,true);assert.equal(await f.flow.pending(),null);
  assert.equal(f.calls.filter(x=>x.path==='/apply/begin').length,1);
});

test('lost completed response is acknowledged without sending failed or retrying native work',async t=>{
  const f=await peer(t),{job,state}=await f.ready(),plan=await f.plan(job,state),mutations=[];
  f.lose('/apply/end');await assert.rejects(f.flow.run(edit(f,plan,mutations)),e=>e.code==='REQUEST_OUTCOME_UNKNOWN');
  assert.deepEqual(mutations,['clone','cut']);assert.ok(await f.flow.pending());
  await f.flow.recover();assert.equal(await f.flow.pending(),null);
  const ends=f.calls.filter(x=>x.path==='/apply/end').map(x=>JSON.parse(x.body).status);assert.deepEqual(ends,['completed']);
});

test('lost batch permit never enters native work and remains blocked through engine restart until host exit',async t=>{
  const f=await peer(t),{job,state}=await f.ready(),plan=await f.plan(job,state),mutations=[];
  f.lose('/apply/check',body=>body.batchId===1);
  await assert.rejects(f.flow.run(edit(f,plan,mutations)),e=>e.code==='REQUEST_OUTCOME_UNKNOWN');
  assert.equal(f.lostResponses[0].value.execute,true);assert.match(f.lostResponses[0].path,/^\/continuations\/[0-9a-f]{32}\/take$/);
  assert.deepEqual(mutations,[]);assert.ok(await f.flow.pending());
  await f.restart();f.client.reset();await f.client.connect();
  assert.equal((await f.api('/apply/status')).blocked,true);
  await assert.rejects(f.flow.recover(),e=>e.code==='APPLY_HOST_EXIT_REQUIRED');
  assert.ok(await f.flow.pending());await f.exitHost();
  assert.equal((await f.flow.recover()).resolved,true);assert.equal(await f.flow.pending(),null);
  assert.deepEqual(mutations,[]);
});

test('lost batch receipt halts before the next native mutation and retains recovery evidence',async t=>{
  const f=await peer(t),{job,state}=await f.ready(),plan=await f.plan(job,state),mutations=[];
  f.lose('/apply/batch-end');
  await assert.rejects(f.flow.run(edit(f,plan,mutations)),e=>e.code==='REQUEST_OUTCOME_UNKNOWN');
  assert.deepEqual(mutations,['clone']);assert.ok(await f.flow.pending());
  assert.equal((await f.api('/apply/status')).blocked,true);
  await assert.rejects(f.flow.recover(),e=>e.code==='APPLY_HOST_EXIT_REQUIRED');
  await f.exitHost();assert.equal((await f.flow.recover()).resolved,true);
  assert.deepEqual(mutations,['clone']);
});

test('lost result identity response preserves the known sequence without retrying the edit',async t=>{
  const f=await peer(t),{job,state}=await f.ready(),plan=await f.plan(job,state),mutations=[];
  f.lose('/apply/result');
  await assert.rejects(f.flow.run(edit(f,plan,mutations)),e=>e.code==='REQUEST_OUTCOME_UNKNOWN'&&e.resultSequenceRef==='result-sequence');
  assert.deepEqual(mutations,['clone']);
  const status=await f.api('/apply/status');assert.equal(status.blocked,true);
  assert.ok(status.records.some(row=>row.resultSequenceRef==='result-sequence'));
  await assert.rejects(f.flow.recover(),e=>e.code==='APPLY_HOST_EXIT_REQUIRED');
  await f.exitHost();assert.equal((await f.flow.recover()).resolved,true);
  assert.deepEqual(mutations,['clone']);
});

test('losing client identity and intent cannot hide unresolved work from a fresh panel',async t=>{
  const f=await peer(t),{job,state}=await f.ready(),plan=await f.plan(job,state),mutations=[];
  f.lose('/apply/check',body=>body.batchId===1);
  await assert.rejects(f.flow.run(edit(f,plan,mutations)),e=>e.code==='REQUEST_OUTCOME_UNKNOWN');
  assert.equal(f.lostResponses[0].value.execute,true);assert.match(f.lostResponses[0].path,/^\/continuations\/[0-9a-f]{32}\/take$/);
  await f.loseLocalIdentity();await f.heartbeat();
  assert.equal(await f.flow.pending(),null);
  assert.equal((await f.api('/state')).applyRecovery.blocked,true);
  await assert.rejects(f.flow.recover(),e=>e.code==='APPLY_HOST_EXIT_REQUIRED');
  assert.deepEqual(mutations,[]);
  await f.exitHost();assert.equal((await f.flow.recover()).resolved,true);
  assert.equal((await f.api('/apply/status')).blocked,false);assert.deepEqual(mutations,[]);
});
