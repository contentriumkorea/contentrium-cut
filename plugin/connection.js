'use strict';
// Private installed-panel transport. No DOM, shell, picker or bearer fallback.
const sha256 = require('./vendor/sha256.js');
const ENDPOINT = 'http://127.0.0.1:41737';
const IDENTITY_FILE = 'contentrium-panel-identity.json';
const HEX32 = /^[0-9a-f]{64}$/;
const HEX16 = /^[0-9a-f]{32}$/;
const ID = /^[A-Za-z0-9_-]{3,100}$/;
const COMPONENT = /^[A-Za-z0-9._-]{3,100}$/;

function fail(code) { const error = new Error('Private local connection needs attention.'); error.code = code; return error; }
function exact(value, fields) { return value && typeof value === 'object' && !Array.isArray(value) && Object.keys(value).length === fields.length && fields.every(k => Object.prototype.hasOwnProperty.call(value,k)); }
function textMatches(value,pattern){return typeof value==='string'&&pattern.test(value);}
function equal(a,b) { if(typeof a!=='string'||typeof b!=='string'||a.length!==b.length)return false;let difference=0;for(let i=0;i<a.length;i++)difference|=a.charCodeAt(i)^b.charCodeAt(i);return difference===0; }
function keyed(key, domain, value) { return sha256.hmac(key,domain+'\n'+value); }
function bytes(hex) { return new Uint8Array(hex.match(/../g).map(x=>parseInt(x,16))); }
function random(count) {
  if(!globalThis.crypto || typeof globalThis.crypto.getRandomValues!=='function')throw fail('AUTH_REQUIRED');
  const value = new Uint8Array(count); globalThis.crypto.getRandomValues(value);
  return Array.from(value,x=>x.toString(16).padStart(2,'0')).join('');
}
function secret(value) {
  if(typeof value!=='string'||!/^[A-Za-z0-9+/]{43}=$/.test(value))throw fail('BOOTSTRAP_INVALID');
  const alphabet='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';let buffer=0,bits=0,result=[];
  for(const character of value.slice(0,-1)){buffer=(buffer<<6)|alphabet.indexOf(character);bits+=6;if(bits>=8){bits-=8;result.push((buffer>>>bits)&255);}}
  if(result.length!==32||(buffer&3)!==0)throw fail('BOOTSTRAP_INVALID');
  return new Uint8Array(result);
}
function bootstrap(raw) {
  if(typeof raw!=='string'||raw.length>4096)throw fail('BOOTSTRAP_INVALID');
  let value;
  try {
    value=JSON.parse(raw);
    // Bootstrap values are primitive exact-schema fields. Catch duplicate and
    // escaped duplicate field names before JSON.parse can silently overwrite.
    const names=[...raw.matchAll(/"((?:\\.|[^"\\])*)"\s*:/g)].map(match=>JSON.parse('"'+match[1]+'"'));
    if(new Set(names).size!==names.length)throw Error();
  } catch(_) {throw fail('BOOTSTRAP_INVALID');}
  if(!exact(value,['schemaVersion','productId','installationId','keyId','authProtocol','endpoint','secret'])||value.schemaVersion!==1||value.productId!=='com.contentrium.cut'||value.authProtocol!==1||value.endpoint!==ENDPOINT||!textMatches(value.installationId,ID)||!textMatches(value.keyId,ID))throw fail('BOOTSTRAP_INVALID');
  return {value,key:secret(value.secret)};
}

async function enrollOwnFolder(folder,entries,bundle) {
  const challengeName='contentrium-install-challenge.json',receiptName='contentrium-install-receipt.json';
  const challenges=entries.filter(x=>x.name===challengeName);
  if(!challenges.length)throw fail('BOOTSTRAP_MISSING');
  function invalid(){throw fail('INSTALLATION_ENROLLMENT_INVALID');}
  function parse(raw){
    if(typeof raw!=='string'||raw.length>4096)invalid();
    try{const names=[...raw.matchAll(/"((?:\\.|[^"\\])*)"\s*:/g)].map(x=>JSON.parse('"'+x[1]+'"'));
      if(new Set(names).size!==names.length)invalid();return JSON.parse(raw);}catch(_){invalid();}
  }
  const normalize=value=>typeof value==='string'?value.replace(/\\/g,'/').toLowerCase():'';
  const own=normalize(folder.nativePath),now=Date.now()/1000;
  if(challenges.length!==1||challenges[0].isFile===false)invalid();
  let challenge;try{challenge=parse(await challenges[0].read());}catch(_){invalid();}
  if(!exact(challenge,['schemaVersion','productId','hostMajor','appVersion','bundleId','nonce','issuedAt','expiresAt','canonicalPluginData'])||
    challenge.schemaVersion!==1||challenge.productId!=='com.contentrium.cut'||challenge.hostMajor!==26||
    challenge.appVersion!==bundle?.appVersion||challenge.bundleId!==bundle?.bundleId||!textMatches(challenge.nonce,HEX32)||
    !Number.isSafeInteger(challenge.issuedAt)||!Number.isSafeInteger(challenge.expiresAt)||challenge.issuedAt>now||
    challenge.expiresAt<=now||challenge.expiresAt-challenge.issuedAt!==600||
    !/^[a-z]:\/(?:[^/]+\/)*adobe\/uxp\/pluginsstorage\/ppro\/26\/external\/com\.contentrium\.cut\/plugindata$/.test(own)||
    own.split('/').some(part=>part==='.'||part==='..')||normalize(challenge.canonicalPluginData)!==own)invalid();
  const receipt={...challenge,verifiedBy:'installed-uxp-getDataFolder',nativePath:folder.nativePath};
  const existing=entries.filter(x=>x.name===receiptName);
  if(existing.length){
    if(existing.length!==1||existing[0].isFile===false)invalid();
    let value;try{value=parse(await existing[0].read());}catch(_){invalid();}
    if(!exact(value,Object.keys(receipt))||Object.keys(receipt).some(key=>value[key]!==receipt[key]))invalid();
  }else{
    try{const file=await folder.createFile(receiptName,{overwrite:false});await file.write(JSON.stringify(receipt));}catch(_){invalid();}
  }
  throw fail('INSTALLATION_PENDING');
}

function create(uxp,bundle,options={}) {
  let installed=null, instanceId=null, contextId=null, session=null, connecting=null, generation=0, contextLost=false;
  let requests=Promise.resolve();
  const validations=new Map();
  function notifyValidation(){try{options.onValidation?.(validations.size);}catch(_){}}
  function sameScope(scope){return scope.generation===generation&&scope.session===session;}
  async function initialize() {
    if(installed&&instanceId&&contextId)return;
    let folder,entries;
    try {folder=await uxp.storage.localFileSystem.getDataFolder();entries=await folder.getEntries();}catch(_){throw fail('BOOTSTRAP_INVALID');}
    const source=entries.filter(x=>x.name==='contentrium-bootstrap.json');
    if(!source.length)return enrollOwnFolder(folder,entries,bundle);
    if(source.length!==1||source[0].isFile===false)throw fail('BOOTSTRAP_INVALID');
    let parsed;
    try {parsed=bootstrap(await source[0].read());}catch(error){throw error.code?error:fail('BOOTSTRAP_INVALID');}
    if(!bundle||!textMatches(bundle.appVersion,COMPONENT)||!textMatches(bundle.bundleId,COMPONENT)||(bundle.protocolVersion!==undefined&&bundle.protocolVersion!==1))throw fail('AUTH_REQUIRED');
    const identities=entries.filter(x=>x.name===IDENTITY_FILE);
    let identity;
    if(identities.length) {
      try {identity=JSON.parse(await identities[0].read());}catch(_){throw fail('BOOTSTRAP_INVALID');}
      if(identities.length!==1||!exact(identity,['schemaVersion','installationId','instanceId'])||identity.schemaVersion!==1||identity.installationId!==parsed.value.installationId||!HEX16.test(identity.instanceId))throw fail('BOOTSTRAP_INVALID');
    } else {
      identity={schemaVersion:1,installationId:parsed.value.installationId,instanceId:random(16)};
      try {const file=await folder.createFile(IDENTITY_FILE,{overwrite:false});await file.write(JSON.stringify(identity));}catch(_){throw fail('BOOTSTRAP_INVALID');}
    }
    installed=parsed;instanceId=identity.instanceId;if(!contextId)contextId=random(16);
  }
  async function transport(path,method,raw,headers) {
    let timer;
    try {
      const response=await Promise.race([
        (async()=>{const response=await globalThis.fetch(ENDPOINT+path,{method,headers:{'Content-Type':'application/json',...headers},...(method==='POST'?{body:raw}:{})});const text=await response.text();if(text.length>5*1024*1024)throw fail('AUTH_REQUIRED');return {status:response.status,value:JSON.parse(text)};})(),
        new Promise((_,reject)=>{timer=setTimeout(()=>reject(fail(method==='POST'?'REQUEST_OUTCOME_UNKNOWN':'TRANSPORT_FAILED')),8000);})
      ]);
      return response;
    } catch(error) {throw error.code?error:fail(method==='POST'?'REQUEST_OUTCOME_UNKNOWN':'TRANSPORT_FAILED');}
    finally {clearTimeout(timer);}
  }
  function safe() {return {installationId:installed.value.installationId,keyId:installed.value.keyId,instanceId,serverBootId:session.boot,expiresAt:session.expiresAt,appVersion:session.appVersion,bundleId:session.bundleId,protocolVersion:1,diagnosticOnly:session.diagnosticOnly};}
  async function handshake() {
    const current=generation;
    await initialize();
    const request={installationId:installed.value.installationId,keyId:installed.value.keyId,instanceId,contextId,clientNonce:random(32),appVersion:bundle.appVersion,bundleId:bundle.bundleId,protocolVersion:1};
    const response=await transport('/auth/challenge','POST',JSON.stringify(request),{});
    const challenge=response.value;
    if(response.status!==200||!exact(challenge,['challengeId','serverNonce','serverBootId','expiresAt','serverAppVersion','serverBundleId','serverProof'])||!HEX16.test(challenge.challengeId)||!HEX32.test(challenge.serverNonce)||!HEX16.test(challenge.serverBootId)||!textMatches(challenge.serverAppVersion,COMPONENT)||!textMatches(challenge.serverBundleId,COMPONENT)||!Number.isSafeInteger(challenge.expiresAt)||challenge.expiresAt<=Date.now()/1000||challenge.expiresAt>Date.now()/1000+31||!HEX32.test(challenge.serverProof))throw fail('AUTH_REQUIRED');
    const transcript=JSON.stringify([request.installationId,request.keyId,request.instanceId,request.contextId,request.clientNonce,request.appVersion,request.bundleId,1,challenge.challengeId,challenge.serverNonce,challenge.serverBootId,challenge.expiresAt,challenge.serverAppVersion,challenge.serverBundleId]);
    if(!equal(challenge.serverProof,keyed(installed.key,'CUT-SERVER-AUTH-1',transcript)))throw fail('AUTH_REQUIRED');
    if(current!==generation)throw fail('AUTH_REQUIRED');
    // Server possession is proven before sending the panel proof or any media.
    const established=await transport('/auth/session','POST',JSON.stringify({challengeId:challenge.challengeId,clientProof:keyed(installed.key,'CUT-PANEL-AUTH-1',transcript)}),{});
    const value=established.value;
    if(established.status!==200&&value?.error?.code==='PANEL_CONTEXT_CONFLICT')throw fail('PANEL_CONTEXT_CONFLICT');
    const diagnosticOnly=challenge.serverAppVersion!==bundle.appVersion||challenge.serverBundleId!==bundle.bundleId;
    if(established.status!==200||!exact(value,['sessionId','expiresAt','serverBootId','instanceId','diagnosticOnly','sessionProof'])||!HEX32.test(value.sessionId)||value.serverBootId!==challenge.serverBootId||value.instanceId!==instanceId||value.diagnosticOnly!==diagnosticOnly||!Number.isSafeInteger(value.expiresAt)||value.expiresAt<=Date.now()/1000||value.expiresAt>Date.now()/1000+1801||!HEX32.test(value.sessionProof))throw fail('AUTH_REQUIRED');
    const sessionText=JSON.stringify([value.sessionId,value.expiresAt,value.serverBootId,value.instanceId,value.diagnosticOnly])+'\n'+transcript;
    if(!equal(value.sessionProof,keyed(installed.key,'CUT-SESSION-1',sessionText))||current!==generation)throw fail('AUTH_REQUIRED');
    session={id:value.sessionId,boot:value.serverBootId,expiresAt:value.expiresAt,appVersion:challenge.serverAppVersion,bundleId:challenge.serverBundleId,diagnosticOnly,counter:0,requestKey:bytes(keyed(installed.key,'CUT-REQUEST-KEY-1',transcript+'\n'+value.sessionId)),responseKey:bytes(keyed(installed.key,'CUT-RESPONSE-KEY-1',transcript+'\n'+value.sessionId))};
    return safe();
  }
  function connect() {
    if(contextLost)return Promise.reject(fail('PANEL_CONTEXT_CONFLICT'));
    if(session&&session.expiresAt>Date.now()/1000)return Promise.resolve(safe());
    if(connecting)return connecting;
    const promise=handshake();connecting=promise;
    promise.then(()=>{if(connecting===promise)connecting=null;},()=>{if(connecting===promise)connecting=null;});
    return promise;
  }
  function reset() {generation++;session=null;connecting=null;}
  function physicalRequest(path,body,method,scope,beforeSend) {
    const run=async()=>{
      const verb=method===undefined?(body===undefined?'GET':'POST'):method;
      if(!['GET','POST'].includes(verb)||typeof path!=='string'||!/^\/[A-Za-z0-9/_-]*$/.test(path)||path.startsWith('/auth/')||(verb==='GET'&&body!==undefined))throw fail('INVALID_REQUEST');
      let raw='';try {if(verb==='POST'){if(!body||typeof body!=='object'||Array.isArray(body))throw Error();raw=JSON.stringify(body);if(typeof raw!=='string'||unescape(encodeURIComponent(raw)).length>2*1024*1024)throw Error();}}catch(_){throw fail('INVALID_REQUEST');}
      if(scope&&!sameScope(scope))throw fail('SESSION_EXPIRED');
      await connect();if(scope&&!sameScope(scope))throw fail('SESSION_EXPIRED');const owned=session;const current=generation;
      // Queue/handshake waits may outlive local cancellation or expiry. Check
      // before assigning a physical counter or transmitting an unsent take.
      if(beforeSend)beforeSend();
      const counter=++owned.counter;
      const headers={'X-Cut-Session':owned.id,'X-Cut-Counter':String(counter),'X-Cut-Mac':keyed(owned.requestKey,'CUT-REQUEST-1',JSON.stringify([owned.id,counter,verb,path,sha256(raw)]))};
      let response;
      try {response=await transport(path,verb,raw,headers);}catch(error){reset();throw error;}
      const envelope=response.value;
      if(!exact(envelope,['sessionId','counter','status','body','responseMac'])){
        reset();const code=envelope&&envelope.error&&envelope.error.code;
        if(code==='PANEL_CONTEXT_CONFLICT'){contextLost=true;throw fail('PANEL_CONTEXT_CONFLICT');}
        throw fail(code==='SESSION_EXPIRED'?'SESSION_EXPIRED':'AUTH_REQUIRED');
      }
      if(envelope.sessionId!==owned.id||envelope.counter!==counter||envelope.status!==response.status||typeof envelope.body!=='string'||!HEX32.test(envelope.responseMac)||!equal(envelope.responseMac,keyed(owned.responseKey,'CUT-RESPONSE-1',JSON.stringify([owned.id,counter,response.status,sha256(envelope.body)])))||current!==generation){reset();throw fail('AUTH_REQUIRED');}
      let value;try {value=JSON.parse(envelope.body);}catch(_){reset();throw fail('AUTH_REQUIRED');}
      if(response.status!==200){const code=value&&value.error&&value.error.code;throw fail(typeof code==='string'&&/^[A-Z0-9_]{1,64}$/.test(code)?code:'REQUEST_FAILED');}
      return {value,scope:{generation:current,session:owned}};
    };
    const pending=requests.then(run,run);requests=pending.catch(()=>{});return pending;
  }
  async function request(path,body,method){
    const initial=await physicalRequest(path,body,method),value=initial.value;
    if(!value||typeof value!=='object'||!Object.prototype.hasOwnProperty.call(value,'continuation'))return value;
    const info=value.continuation;
    if(!exact(value,['continuation'])||!exact(info,['id','status','pollAfterMs','expiresInMs','epoch'])||
      !textMatches(info.id,HEX16)||info.status!=='pending'||!Number.isSafeInteger(info.epoch)||info.epoch<0||
      !Number.isSafeInteger(info.pollAfterMs)||info.pollAfterMs<50||info.pollAfterMs>1000||
      !Number.isSafeInteger(info.expiresInMs)||info.expiresInMs<1||info.expiresInMs>1800000||validations.has(info.id))throw fail('AUTH_REQUIRED');
    const pending={id:info.id,scope:initial.scope,canceled:false,deadline:Date.now()+info.expiresInMs};
    validations.set(info.id,pending);notifyValidation();
    function current(){if(!sameScope(pending.scope))throw fail('SESSION_EXPIRED');if(pending.canceled)throw fail('CANCELED');if(Date.now()>=pending.deadline)throw fail('CONTINUATION_EXPIRED');}
    const endpoint='/continuations/'+info.id;
    try{
      while(true){
        current();const status=(await physicalRequest(endpoint,undefined,'GET',pending.scope,current)).value;current();
        if(!status||typeof status!=='object'||status.id!==info.id||!['pending','ready','failed','canceled','consumed'].includes(status.status))throw fail('AUTH_REQUIRED');
        if(status.status==='failed'||status.status==='canceled'){
          const code=status.error?.code;throw fail(typeof code==='string'&&/^[A-Z0-9_]{1,64}$/.test(code)?code:status.status==='canceled'?'CANCELED':'REQUEST_FAILED');
        }
        if(status.status==='consumed')throw fail('REQUEST_OUTCOME_UNKNOWN');
        if(status.status==='ready'){
          current();const result=await physicalRequest(endpoint+'/take',{epoch:info.epoch},'POST',pending.scope,current);current();
          // Never retry a take or the original operation, including on response loss.
          return result.value;
        }
        // This wait is outside the physical request queue so heartbeat/update
        // controls can run while the owned source validation is still active.
        await new Promise(resolve=>setTimeout(resolve,info.pollAfterMs));
      }
    }catch(error){
      if(error.code==='CONTINUATION_EXPIRED'&&sameScope(pending.scope)){
        pending.canceled=true;try{await physicalRequest(endpoint+'/cancel',{},'POST',pending.scope);}catch(_){}
      }
      throw error;
    }finally{validations.delete(info.id);notifyValidation();}
  }
  async function cancelPending(){
    const owned=[...validations.values()].filter(value=>!value.canceled);
    for(const value of owned)value.canceled=true;
    const outcomes=await Promise.allSettled(owned.filter(value=>sameScope(value.scope)).map(value=>physicalRequest('/continuations/'+value.id+'/cancel',{},'POST',value.scope)));
    const failed=outcomes.find(value=>value.status==='rejected');if(failed)throw failed.reason;
  }
  return {connect,request,reset,cancelPending};
}
module.exports={create};
