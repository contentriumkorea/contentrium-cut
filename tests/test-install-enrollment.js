'use strict';
const test=require('node:test');const assert=require('node:assert/strict');
const {create}=require('../plugin/connection.js');
const bundle={appVersion:'0.2.0',bundleId:'bundle-new',protocolVersion:1};
const data='C:\\Users\\Fixture\\AppData\\Roaming\\Adobe\\UXP\\PluginsStorage\\PPRO\\26\\External\\com.contentrium.cut\\PluginData';
function fixture(changes={},path=data){
  const now=Math.floor(Date.now()/1000),challenge={schemaVersion:1,productId:'com.contentrium.cut',hostMajor:26,
    ...bundle,nonce:'ab'.repeat(32),issuedAt:now,expiresAt:now+600,canonicalPluginData:data,...changes};
  delete challenge.protocolVersion;
  const files=new Map([['contentrium-install-challenge.json',JSON.stringify(challenge)]]);
  const entry=name=>({name,isFile:true,read:async()=>files.get(name),write:async raw=>files.set(name,raw)});
  const folder={nativePath:path,getEntries:async()=>[...files.keys()].map(entry),createFile:async(name,options)=>{
    if(files.has(name)&&!options.overwrite)throw Error('exists');return entry(name);}};
  return {files,challenge,uxp:{storage:{localFileSystem:{getDataFolder:async()=>folder}}}};
}
test('own native data folder writes exact public receipt without transport or identity',async()=>{
  const store=fixture(),old=global.fetch;let requests=0;global.fetch=async()=>{requests++;throw Error('network forbidden');};
  try{
    const client=create(store.uxp,bundle);
    await assert.rejects(client.connect(),e=>e.code==='INSTALLATION_PENDING');
    const raw=store.files.get('contentrium-install-receipt.json');
    assert.deepEqual(JSON.parse(raw),{...store.challenge,verifiedBy:'installed-uxp-getDataFolder',nativePath:data});
    await assert.rejects(client.connect(),e=>e.code==='INSTALLATION_PENDING');
    assert.equal(store.files.get('contentrium-install-receipt.json'),raw);
    assert.equal(requests,0);assert.equal(store.files.size,2);
  }finally{global.fetch=old;}
});
test('wrong stale other major developer and outside folder challenges never write',async()=>{
  for(const [change,path] of [[{nonce:'wrong'},data],[{appVersion:'0.1.0'},data],[{bundleId:'wrong'},data],
    [{hostMajor:27},data],[{expiresAt:0},data],[{extra:'bad'},data],[{},data.replace('External','Developer')],
    [{},data.replace('PPRO\\26','PPRO\\27')],[{},data.replace('com.contentrium.cut','other.product')]]){
    const store=fixture(change,path);
    await assert.rejects(create(store.uxp,bundle).connect(),e=>e.code==='INSTALLATION_ENROLLMENT_INVALID');
    assert.equal(store.files.size,1);
  }
});
test('unknown receipt and invalid private bootstrap are never replaced',async()=>{
  const store=fixture();store.files.set('contentrium-install-receipt.json','unknown');
  await assert.rejects(create(store.uxp,bundle).connect(),e=>e.code==='INSTALLATION_ENROLLMENT_INVALID');
  assert.equal(store.files.get('contentrium-install-receipt.json'),'unknown');
  store.files.set('contentrium-bootstrap.json','invalid');
  await assert.rejects(create(store.uxp,bundle).connect(),e=>e.code==='BOOTSTRAP_INVALID');
  assert.equal(store.files.get('contentrium-bootstrap.json'),'invalid');
});
