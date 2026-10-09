'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),{spawn}=require('node:child_process'),{createInterface}=require('node:readline'),crypto=require('node:crypto'),path=require('node:path');
const connection=require('../plugin/connection');
const bundle={appVersion:'0.1.0',bundleId:'bundle-test',protocolVersion:1};
const bootstrap={schemaVersion:1,productId:'com.contentrium.cut',installationId:'install-test',keyId:'key-test',authProtocol:1,endpoint:'http://127.0.0.1:41737',secret:Buffer.from(Array.from({length:32},(_,i)=>i)).toString('base64')};
async function fixture(t,scenario='success',{loseTake=false}={}){
  const process=spawn(path.join(__dirname,'../.build-venv/Scripts/python.exe'),['-B',path.join(__dirname,'continuation_auth_bridge.py'),scenario],{cwd:path.join(__dirname,'..'),env:{...global.process.env,PYTHONPATH:'companion;tests'},windowsHide:true,stdio:['pipe','pipe','pipe']});
  const queue=[],calls=[],activity=[],descriptors=[];let exited=false,stderr='';const lines=createInterface({input:process.stdout});
  process.stderr.on('data',v=>{stderr=(stderr+v.toString()).slice(-2000);});
  lines.on('line',line=>{const next=queue.shift();if(next)next.resolve(JSON.parse(line));});
  process.on('error',e=>{while(queue.length)queue.shift().reject(e);});
  process.on('exit',code=>{exited=true;while(queue.length)queue.shift().reject(new Error('Fixture exited '+code+': '+stderr));});
  const oldFetch=global.fetch,oldCrypto=Object.getOwnPropertyDescriptor(globalThis,'crypto');Object.defineProperty(globalThis,'crypto',{configurable:true,value:crypto.webcrypto});
  global.fetch=async(url,options)=>{
    const request={path:new URL(url).pathname,method:options.method,headers:{Host:'127.0.0.1:41737',...options.headers},body:options.body||''};calls.push(request);
    const response=await new Promise((resolve,reject)=>{queue.push({resolve,reject});process.stdin.write(JSON.stringify(request)+'\n');});
    if(loseTake&&request.path.endsWith('/take'))throw new Error('Lost consumed response');
    return {status:response.status,text:async()=>JSON.stringify(response.body)};
  };
  t.after(async()=>{global.fetch=oldFetch;if(oldCrypto)Object.defineProperty(globalThis,'crypto',oldCrypto);if(!exited){process.stdin.end();const timeout=setTimeout(()=>process.kill(),3000);await new Promise(resolve=>process.once('exit',resolve));clearTimeout(timeout);}lines.close();});
  const files=new Map([['contentrium-bootstrap.json',JSON.stringify(bootstrap)]]),entry=name=>({name,isFile:true,read:async()=>files.get(name),write:async value=>files.set(name,value)});
  const uxp={storage:{localFileSystem:{getDataFolder:async()=>({getEntries:async()=>[...files.keys()].map(entry),createFile:async name=>{files.set(name,'');return entry(name);}})}}};
  const client=connection.create(uxp,bundle,{onValidation:(count,active)=>{activity.push(count);descriptors.push(active);}});
  const started=async()=>{for(let i=0;i<200&&!calls.some(c=>c.path==='/continuations/'+'c'.repeat(32));i++)await new Promise(resolve=>setTimeout(resolve,5));};
  return {client,calls,activity,descriptors,started};
}
test('signed validation continuation is consumed once with captured epoch and no original replay',async t=>{
  const f=await fixture(t);assert.deepEqual(await f.client.request('/fixture/work',{}),{execute:true,restored:true,count:1});
  assert.equal(f.calls.filter(c=>c.path==='/fixture/work').length,1);const takes=f.calls.filter(c=>c.path.endsWith('/take'));assert.equal(takes.length,1);assert.equal(takes[0].method,'POST');assert.deepEqual(JSON.parse(takes[0].body),{epoch:7});assert.deepEqual(f.activity,[1,0]);assert.deepEqual(f.descriptors,[[{id:'c'.repeat(32),path:'/fixture/work',epoch:7}],[]]);assert.deepEqual(Object.keys(f.descriptors[0][0]).sort(),['epoch','id','path']);
});
test('waiting validation releases the transport queue for heartbeats and update controls',async t=>{
  const f=await fixture(t,'hold'),pending=f.client.request('/fixture/work',{});await f.started();
  assert.deepEqual(await f.client.request('/fixture/heartbeat',{}),{awake:true});assert.equal((await pending).execute,true);
  assert.ok(f.calls.findIndex(c=>c.path==='/fixture/heartbeat')<f.calls.findIndex(c=>c.path.endsWith('/take')));
});
test('explicit cancel prevents a final take and releases visible validation activity',async t=>{
  const f=await fixture(t,'hold'),pending=f.client.request('/fixture/work',{});const denied=assert.rejects(pending,e=>e.code==='CANCELED');await f.started();await f.client.cancelPending();await denied;
  assert.equal(f.calls.some(c=>c.path.endsWith('/take')),false);assert.equal(f.calls.filter(c=>c.path.endsWith('/cancel')).length,1);assert.equal(f.activity.at(-1),0);
});
test('lost final take response remains unknown and never repeats original work or permit take',async t=>{
  const f=await fixture(t,'success',{loseTake:true});await assert.rejects(f.client.request('/fixture/work',{}),e=>e.code==='REQUEST_OUTCOME_UNKNOWN');
  assert.equal(f.calls.filter(c=>c.path==='/fixture/work').length,1);assert.equal(f.calls.filter(c=>c.path.endsWith('/take')).length,1);assert.equal(f.activity.at(-1),0);
});
test('reset while pending never transfers a continuation to a new authenticated session',async t=>{
  const f=await fixture(t,'hold'),pending=f.client.request('/fixture/work',{});const denied=assert.rejects(pending,e=>e.code==='SESSION_EXPIRED');await f.started();f.client.reset();await denied;
  assert.equal(f.calls.some(c=>c.path.endsWith('/take')),false);assert.equal(f.calls.filter(c=>c.path==='/fixture/work').length,1);
});
test('failed or malformed continuation cannot activate restored work',async t=>{
  const f=await fixture(t,'failed');await assert.rejects(f.client.request('/fixture/work'),e=>e.code==='SOURCE_CHANGED');assert.equal(f.calls.some(c=>c.path.endsWith('/take')),false);
});
test('authenticated but malformed continuation identity is rejected before polling',async t=>{
  const f=await fixture(t,'malformed');await assert.rejects(f.client.request('/fixture/work',{}),e=>e.code==='AUTH_REQUIRED');assert.equal(f.calls.some(c=>c.path.startsWith('/continuations/')),false);
});

// In-memory signed peer: hold a heartbeat after ready, ahead of the queued take.
// External transport/storage/clock are doubles; queue and authentication are real.
async function queuedTakePeer(t){
  const oldFetch=global.fetch,oldCrypto=Object.getOwnPropertyDescriptor(globalThis,'crypto'),oldNow=Date.now;
  let now=oldNow();Date.now=()=>now;Object.defineProperty(globalThis,'crypto',{value:crypto.webcrypto,configurable:true});
  const secret=Buffer.from(bootstrap.secret,'base64'),keyed=(key,domain,value)=>crypto.createHmac('sha256',key).update(domain+'\n'+value).digest('hex'),hash=text=>crypto.createHash('sha256').update(text).digest('hex');
  const sid='e'.repeat(64),boot='b'.repeat(32),id='c'.repeat(32),calls=[],activity=[];
  let transcript,requestKey,responseKey,client,heldHeartbeat,release,entered,counter=0;
  const holdEntered=new Promise(resolve=>{entered=resolve;}),held=new Promise(resolve=>{release=resolve;});
  t.after(()=>{release();global.fetch=oldFetch;Date.now=oldNow;if(oldCrypto)Object.defineProperty(globalThis,'crypto',oldCrypto);else delete globalThis.crypto;});
  global.fetch=async(url,options)=>{
    const p=new URL(url).pathname;calls.push(p);let value;
    if(p==='/auth/challenge'){
      const r=JSON.parse(options.body),c={challengeId:'d'.repeat(32),serverNonce:'a'.repeat(64),serverBootId:boot,expiresAt:Math.floor(now/1000)+30,serverAppVersion:bundle.appVersion,serverBundleId:bundle.bundleId};
      transcript=JSON.stringify([r.installationId,r.keyId,r.instanceId,r.contextId,r.clientNonce,r.appVersion,r.bundleId,1,c.challengeId,c.serverNonce,c.serverBootId,c.expiresAt,c.serverAppVersion,c.serverBundleId]);
      c.serverProof=keyed(secret,'CUT-SERVER-AUTH-1',transcript);value=c;
    }else if(p==='/auth/session'){
      const r=JSON.parse(transcript),v={sessionId:sid,expiresAt:Math.floor(now/1000)+1800,serverBootId:boot,instanceId:r[2],diagnosticOnly:false};
      v.sessionProof=keyed(secret,'CUT-SESSION-1',JSON.stringify([v.sessionId,v.expiresAt,v.serverBootId,v.instanceId,v.diagnosticOnly])+'\n'+transcript);
      requestKey=Buffer.from(keyed(secret,'CUT-REQUEST-KEY-1',transcript+'\n'+sid),'hex');responseKey=Buffer.from(keyed(secret,'CUT-RESPONSE-KEY-1',transcript+'\n'+sid),'hex');value=v;
    }else{
      const supplied=Number(options.headers['X-Cut-Counter']),raw=options.body||'';
      assert.equal(supplied,++counter);assert.equal(options.headers['X-Cut-Session'],sid);
      assert.equal(options.headers['X-Cut-Mac'],keyed(requestKey,'CUT-REQUEST-1',JSON.stringify([sid,supplied,options.method,p,hash(raw)])));
      let payload;
      if(p==='/fixture/work')payload={continuation:{id,status:'pending',pollAfterMs:50,expiresInMs:3000,epoch:7}};
      else if(p==='/continuations/'+id){heldHeartbeat=client.request('/hold',{});payload={id,status:'ready'};}
      else if(p==='/hold'){entered();await held;payload={awake:true};}
      else if(p.endsWith('/take')){assert.deepEqual(JSON.parse(raw),{epoch:7});payload={execute:true};}
      else if(p.endsWith('/cancel'))payload={id,status:'canceled'};
      else if(p==='/fixture/after')payload={awake:true};
      else throw Error(p);
      const body=JSON.stringify(payload);value={sessionId:sid,counter:supplied,status:200,body,responseMac:keyed(responseKey,'CUT-RESPONSE-1',JSON.stringify([sid,supplied,200,hash(body)]))};
    }
    return {status:200,text:async()=>JSON.stringify(value)};
  };
  const files=new Map([['contentrium-bootstrap.json',JSON.stringify(bootstrap)]]),entry=name=>({name,isFile:true,read:async()=>files.get(name),write:async s=>files.set(name,s)});
  const uxp={storage:{localFileSystem:{getDataFolder:async()=>({getEntries:async()=>[...files.keys()].map(entry),createFile:async name=>{files.set(name,'');return entry(name);}})}}};
  client=connection.create(uxp,bundle,{onValidation:n=>activity.push(n)});
  const outcome=client.request('/fixture/work',{}).then(value=>({value}),error=>({code:error.code}));
  await holdEntered;
  // Drain ready-response microtasks. The held physical request remains blocked,
  // so the continuation has queued take without allowing it to reach the peer.
  await new Promise(setImmediate);
  assert.equal(calls.some(p=>p.endsWith('/take')),false);
  return {client,calls,activity,outcome,release,expire:()=>{now+=3000;},heartbeat:()=>heldHeartbeat};
}

for(const action of ['cancel','expire']){
  test('ready take queued behind heartbeat is not sent after local '+action,async t=>{
    const f=await queuedTakePeer(t);let cancellation;
    if(action==='cancel'){cancellation=Promise.all([f.client.cancelPending(),f.client.cancelPending()]);}else f.expire();
    f.release();const result=await f.outcome;await f.heartbeat();await cancellation;
    assert.equal(result.code,action==='cancel'?'CANCELED':'CONTINUATION_EXPIRED');
    assert.equal(f.calls.filter(p=>p.endsWith('/take')).length,0);assert.equal(f.calls.filter(p=>p.endsWith('/cancel')).length,1);
    assert.equal(f.calls.filter(p=>p==='/fixture/work').length,1);assert.deepEqual(f.activity,[1,0]);
    assert.deepEqual(await f.client.request('/fixture/after',{}),{awake:true});
    assert.deepEqual(f.calls.slice(-3),['/hold','/continuations/'+'c'.repeat(32)+'/cancel','/fixture/after']);
  });
}
