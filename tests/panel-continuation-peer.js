'use strict';
// Actual installed-panel authentication/queue/continuation code; no sockets,
// browser, Adobe, real bootstrap, or user storage. Timers and peer are doubles.
const assert=require('node:assert/strict'),crypto=require('node:crypto'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
async function attachContinuationPeer(f){
 const bundle={appVersion:'0.1.0',bundleId:'contentrium-cut-0.1.0',protocolVersion:1},id='c'.repeat(32),sid='e'.repeat(64),boot='b'.repeat(32);
 const secret=Buffer.from(Array.from({length:32},(_,i)=>i));
 const bootstrap={schemaVersion:1,productId:'com.contentrium.cut',installationId:'install-test',keyId:'key-test',authProtocol:1,endpoint:'http://127.0.0.1:41737',secret:secret.toString('base64')};
 const keyed=(key,domain,value)=>crypto.createHmac('sha256',key).update(domain+'\n'+value).digest('hex'),hash=value=>crypto.createHash('sha256').update(value).digest('hex');
 const files=new Map([['contentrium-bootstrap.json',JSON.stringify(bootstrap)]]),calls=[],timers=new Set(),activity=[];
 const entry=name=>({name,isFile:true,read:async()=>files.get(name),write:async text=>files.set(name,text)});
 const uxp={storage:{localFileSystem:{getDataFolder:async()=>({getEntries:async()=>[...files.keys()].map(entry),createFile:async name=>{assert.equal(files.has(name),false);files.set(name,'');return entry(name);}})}}};
 const original=f.evaluate('connection'),mockRequest=original.request;
 let transcript,requestKey,responseKey,counter=0,releaseCancel,rejectCancel;
 async function fetch(url,options){
  const p=new URL(url).pathname,raw=options.body||'';calls.push({path:p,method:options.method,body:raw});let value,status=200;
  if(p==='/auth/challenge'){
   const r=JSON.parse(raw),c={challengeId:'d'.repeat(32),serverNonce:'a'.repeat(64),serverBootId:boot,expiresAt:Math.floor(Date.now()/1000)+30,serverAppVersion:bundle.appVersion,serverBundleId:bundle.bundleId};
   transcript=JSON.stringify([r.installationId,r.keyId,r.instanceId,r.contextId,r.clientNonce,r.appVersion,r.bundleId,1,c.challengeId,c.serverNonce,c.serverBootId,c.expiresAt,c.serverAppVersion,c.serverBundleId]);
   value={...c,serverProof:keyed(secret,'CUT-SERVER-AUTH-1',transcript)};
  }else if(p==='/auth/session'){
   assert.equal(JSON.parse(raw).clientProof,keyed(secret,'CUT-PANEL-AUTH-1',transcript));
   const r=JSON.parse(transcript),v={sessionId:sid,expiresAt:Math.floor(Date.now()/1000)+1800,serverBootId:boot,instanceId:r[2],diagnosticOnly:false};
   value={...v,sessionProof:keyed(secret,'CUT-SESSION-1',JSON.stringify([v.sessionId,v.expiresAt,v.serverBootId,v.instanceId,v.diagnosticOnly])+'\n'+transcript)};
   requestKey=Buffer.from(keyed(secret,'CUT-REQUEST-KEY-1',transcript+'\n'+sid),'hex');responseKey=Buffer.from(keyed(secret,'CUT-RESPONSE-KEY-1',transcript+'\n'+sid),'hex');
  }else{
   const supplied=Number(options.headers['X-Cut-Counter']);assert.equal(supplied,++counter);assert.equal(options.headers['X-Cut-Session'],sid);
   assert.equal(options.headers['X-Cut-Mac'],keyed(requestKey,'CUT-REQUEST-1',JSON.stringify([sid,supplied,options.method,p,hash(raw)])));
   let payload;
   if(p==='/resources/prune'&&JSON.parse(raw).action!=='release'){
    f.state.gateOpen=false;f.state.maintenance={id,status:'canceled',drained:true,canRelease:true};
    payload={continuation:{id,status:'pending',pollAfterMs:50,expiresInMs:30000,epoch:0}};
   }else if(p==='/continuations/'+id)payload={id,status:'pending'};
   else if(p==='/continuations/'+id+'/cancel'){
    const result=await new Promise((a,b)=>{releaseCancel=a;rejectCancel=b;});
    if(result?.error){status=409;payload={error:{code:result.error}};}else payload={id,status:'canceled'};
   }else if(p==='/resources/prune'){
    assert.deepEqual(JSON.parse(raw),{epoch:0,action:'release'});f.state.gateOpen=true;f.state.maintenance=null;payload={released:true};
   }else payload=await mockRequest(p,options.method==='POST'?JSON.parse(raw):undefined);
   const body=JSON.stringify(payload);value={sessionId:sid,counter:supplied,status,body,responseMac:keyed(responseKey,'CUT-RESPONSE-1',JSON.stringify([sid,supplied,status,hash(body)]))};
  }
  return {status,text:async()=>JSON.stringify(value)};
 }
 const module={exports:{}};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../plugin/connection.js'),'utf8'),{
  module,exports:module.exports,require:name=>{assert.equal(name,'./vendor/sha256.js');return require('../plugin/vendor/sha256.js');},crypto:crypto.webcrypto,Uint8Array,Date,JSON,Map,Set,fetch,
  setTimeout:(fn,ms)=>{const timer={fn,ms};timers.add(timer);return timer;},clearTimeout:timer=>timers.delete(timer)
 });
 const client=module.exports.create(uxp,bundle,{onValidation:(count,descriptors)=>{activity.push(count);f.validation(count,descriptors);}});
 await client.connect();Object.assign(original,client);
 async function drainUntil(check){for(let i=0;i<400&&!check();i++)await Promise.resolve();assert.equal(!!check(),true,'authenticated peer stage reached');}
 return {calls,files,activity,timers,drainUntil,cancelEntered:()=>typeof releaseCancel==='function',
  release:code=>{assert.equal(typeof releaseCancel,'function');releaseCancel(code?{error:code}:{});},reject:()=>{assert.equal(typeof rejectCancel,'function');rejectCancel(new Error('Owned mocked transport loss'));},
  poll:()=>{const timer=[...timers].find(t=>t.ms===50);assert.ok(timer,'actual continuation timer');timers.delete(timer);timer.fn();},
  close:()=>{assert.equal(timers.size,0,'all actual transport timers must be cleared');assert.equal(activity.at(-1),0);}
 };
}
module.exports={attachContinuationPeer};
