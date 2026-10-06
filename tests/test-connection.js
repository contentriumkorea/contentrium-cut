'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const {createInterface} = require('node:readline');
const crypto = require('node:crypto');
const path = require('node:path');
const connection = require('../plugin/connection.js');
const bundle = {appVersion:'0.1.0', bundleId:'bundle-test', protocolVersion:1};
const bootstrap = {schemaVersion:1,productId:'com.contentrium.cut',installationId:'install-test',keyId:'key-test',authProtocol:1,endpoint:'http://127.0.0.1:41737',secret:Buffer.from(Array.from({length:32},(_,i)=>i)).toString('base64')};

function storage(value=bootstrap) {
  const files = new Map(value ? [['contentrium-bootstrap.json',JSON.stringify(value)]] : []);
  const entry = name => ({name,isFile:true,read:async()=>files.get(name),write:async text=>{files.set(name,text);}});
  return {files, uxp:{storage:{localFileSystem:{getDataFolder:async()=>({getEntries:async()=>[...files.keys()].map(entry),createFile:async(name)=>{if(files.has(name))throw Error('exists');files.set(name,'');return entry(name);}})}}}};
}

async function peer(t, alter) {
  const child=spawn(path.join(__dirname,'../.build-venv/Scripts/python.exe'),[path.join(__dirname,'private_auth_bridge.py')],{cwd:path.join(__dirname,'..'),env:{...process.env,PYTHONPATH:'companion;tests'},windowsHide:true,stdio:['pipe','pipe','pipe']});
  const pending=[]; const lines=createInterface({input:child.stdout});
  lines.on('line',line=>pending.shift().resolve(JSON.parse(line)));
  child.on('error',error=>{while(pending.length)pending.shift().reject(error);});
  const requests=[];
  const oldFetch=global.fetch;
  Object.defineProperty(globalThis,'crypto',{configurable:true,value:crypto.webcrypto});
  global.fetch=async(url,opts)=>{
    const request={path:new URL(url).pathname,method:opts.method,headers:{Host:'127.0.0.1:41737',...opts.headers},body:opts.body||''};requests.push(request);
    const response=await new Promise((resolve,reject)=>{pending.push({resolve,reject});child.stdin.write(JSON.stringify(request)+'\n');});
    if(alter)alter(request,response);
    return {status:response.status,text:async()=>JSON.stringify(response.body)};
  };
  t.after(async()=>{global.fetch=oldFetch;child.stdin.end();await new Promise(resolve=>child.once('exit',resolve));lines.close();});
  return requests;
}

test('missing or rogue bootstrap fails before transport',async()=>{
  const original=global.fetch;let calls=0;global.fetch=async()=>{calls++;throw Error('unexpected');};
  try {
    await assert.rejects(connection.create(storage(null).uxp,bundle).connect(),e=>e.code==='BOOTSTRAP_MISSING');
    await assert.rejects(connection.create(storage({...bootstrap,endpoint:'http://localhost:41737'}).uxp,bundle).connect(),e=>e.code==='BOOTSTRAP_INVALID');
    assert.equal(calls,0);
  } finally {global.fetch=original;}
});

test('real Python HMAC peer coalesces connect and serializes independent callers',async t=>{
  const calls=await peer(t);const store=storage();const client=connection.create(store.uxp,bundle);
  const [a,b]=await Promise.all([client.connect(),client.connect()]);assert.equal(a.instanceId,b.instanceId);
  const result=await Promise.all([client.request('/state'),client.request('/state'),client.request('/unknown',{}) .catch(e=>e.code)]);
  assert.equal(result[0].bundleId,'bundle-test');assert.equal(result[1].bundleId,'bundle-test');assert.equal(result[2],'NOT_FOUND');
  assert.deepEqual(calls.filter(x=>x.headers['X-Cut-Counter']).map(x=>x.headers['X-Cut-Counter']),['1','2','3']);
  assert.equal(calls.filter(x=>x.path==='/auth/challenge').length,1);
  client.reset();const next=await client.connect();assert.equal(next.instanceId,a.instanceId);
  assert.ok(!JSON.stringify(next).includes(bootstrap.secret));assert.ok(!('sessionId' in next));
  assert.equal([...store.files.keys()].filter(x=>x!=='contentrium-bootstrap.json').length,1);
});

test('rogue server proof receives no panel proof or media',async t=>{
  const calls=await peer(t,(request,response)=>{if(request.path==='/auth/challenge')response.body.serverProof='00'.repeat(32);});
  const client=connection.create(storage().uxp,bundle);
  await assert.rejects(client.request('/project',{mediaPath:'secret-media'}),e=>e.code==='AUTH_REQUIRED');
  assert.deepEqual(calls.map(x=>x.path),['/auth/challenge']);
  assert.ok(!JSON.stringify(calls).includes('secret-media'));
});

test('altered authenticated response is rejected without automatic mutation replay',async t=>{
  const calls=await peer(t,(request,response)=>{if(request.path==='/state')response.body.body='{"gateOpen":true,"fake":true}';});
  const client=connection.create(storage().uxp,bundle);
  await assert.rejects(client.request('/state',{}),e=>e.code==='AUTH_REQUIRED');
  assert.equal(calls.filter(x=>x.path==='/state').length,1);
});

test('existing unreadable identity fails closed instead of minting another owner',async()=>{
  const store=storage();store.files.set('contentrium-panel-identity.json','not json');
  await assert.rejects(connection.create(store.uxp,bundle).connect(),e=>e.code==='BOOTSTRAP_INVALID');
  assert.equal(store.files.get('contentrium-panel-identity.json'),'not json');
});

test('distinct simultaneous transport contexts cannot share one logical owner',async t=>{
  const calls=await peer(t);const store=storage();
  const first=connection.create(store.uxp,bundle);await first.connect();
  const second=connection.create(store.uxp,bundle);
  await assert.rejects(second.connect(),e=>e.code==='PANEL_CONTEXT_CONFLICT');
  assert.equal((await first.request('/state')).bundleId,'bundle-test');
  const contexts=calls.filter(x=>x.path==='/auth/challenge').map(x=>JSON.parse(x.body));
  assert.equal(contexts[0].instanceId,contexts[1].instanceId);assert.notEqual(contexts[0].contextId,contexts[1].contextId);
});

test('displaced active context remains closed after reset without reconnect ping-pong',async t=>{
  const calls=await peer(t,(request,response)=>{if(request.path==='/state'){response.status=409;response.body={error:{code:'PANEL_CONTEXT_CONFLICT'}};}});
  const client=connection.create(storage().uxp,bundle);await client.connect();
  await assert.rejects(client.request('/state'),e=>e.code==='PANEL_CONTEXT_CONFLICT');
  client.reset();await assert.rejects(client.connect(),e=>e.code==='PANEL_CONTEXT_CONFLICT');
  assert.equal(calls.filter(x=>x.path==='/auth/challenge').length,1);
});

test('component mismatch authenticates diagnosis but cannot bind project or media',async t=>{
  await peer(t);const client=connection.create(storage().uxp,{...bundle,appVersion:'0.2.0',bundleId:'new-bundle'});
  const session=await client.connect();assert.equal(session.diagnosticOnly,true);assert.equal(session.bundleId,'bundle-test');
  assert.equal((await client.request('/state')).appVersion,'0.1.0');
  await assert.rejects(client.request('/project',{snapshot:{media:'test'}}),e=>e.code==='COMPONENT_MISMATCH');
});
