const test=require('node:test'),assert=require('node:assert/strict');
const {controller}=require('../plugin/mutation');
const host={hash:v=>JSON.stringify(v),transaction:(_p,_name,build)=>build({})};
function control(extra={}){return {check:async()=>{},beforeBatch:async()=>({execute:true}),afterBatch:async()=>{},onResult:async()=>{},...extra};}
test('a replay or uncertain permit makes zero native changes',async()=>{
  let changes=0;const m=controller(host,'source',control({beforeBatch:async()=>({execute:false})}));
  await assert.rejects(m.run('clone',()=>changes++),/REPLAY_OR_UNCERTAIN/);assert.equal(changes,0);
});
test('native mutation follows permit and receipt precedes later work',async()=>{
  const events=[];let release;const pending=new Promise(resolve=>release=resolve);
  const m=controller(host,'source',control({beforeBatch:async b=>{events.push(['permit',b]);return {execute:true};},afterBatch:async b=>{events.push(['receipt',b]);await pending;}}));
  let returned=false;const work=m.run('clone',()=>events.push(['native'])).then(()=>returned=true);
  await new Promise(resolve=>setImmediate(resolve));assert.equal(returned,false);
  assert.deepEqual(events.map(e=>e[0]),['permit','native','receipt']);assert.equal(events[0][1].batchId,1);
  assert.equal(events[0][1].operationDigest,JSON.stringify({name:'clone',sourceSequenceRef:'source',ordinal:1}));
  release();await work;assert.equal(returned,true);
});
test('result identity must persist before naming or editing can continue',async()=>{
  let release,returned=false;const pending=new Promise(resolve=>release=resolve);
  const m=controller(host,'source',control({onResult:async()=>pending}));const work=m.result('result').then(()=>returned=true);
  await new Promise(resolve=>setImmediate(resolve));assert.equal(returned,false);release();await work;
  await assert.rejects(m.result('different'),/RESULT_IDENTITY/);
});
test('all durable boundaries are mandatory',()=>{
  for(const key of ['check','beforeBatch','afterBatch','onResult']){const c=control();delete c[key];assert.throws(()=>controller(host,'source',c),/DURABLE_AUTHORIZATION/);}
});
test('a lost receipt forbids later native transactions in the same controller',async()=>{
  let changes=0;const m=controller(host,'source',control({afterBatch:async()=>{throw new Error('DISK_FAILED');}}));
  await assert.rejects(m.run('clone',()=>changes++),/DISK_FAILED/);
  await assert.rejects(m.run('rename',()=>changes++),/BATCH_UNCERTAIN/);assert.equal(changes,1);
});
