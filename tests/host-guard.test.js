const test=require('node:test');const assert=require('node:assert/strict');
const guard=require('../plugin/guard');
const sha=require('../plugin/vendor/sha256');
function hash(value){const stable=v=>Array.isArray(v)?'['+v.map(stable).join(',')+']':v&&typeof v==='object'?'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+stable(v[k])).join(',')+'}':JSON.stringify(v);return sha(stable(value));}
function fixture(){const snapshot={snapshotHash:'owned',fps:{num:30,den:1},range:{startFrame:0,endFrame:720},tracks:[{trackRef:'video:0',mediaType:'video',muted:false,protected:false}],clips:[{instanceKey:'A',trackRef:'video:0',mediaType:'video',startTicks:'0',endTicks:'6096384000000',inTicks:'0',outTicks:'6096384000000',speed:1,disabled:false,supportFlags:{timeMappingSupported:true,effectPreservationSupported:true}}],supportFlags:{timeMappingSupported:true,audioPreservationSupported:true,effectPreservationSupported:true,hostApplyVerified:true}};const plan={snapshotHash:'owned',segments:[{startFrame:0,endFrame:720,sourceClipInstanceKey:'A',sourceIn:'0',sourceOut:'6096384000000',cameraId:'CA',reason:'speaker'}]};plan.planHash=hash(plan);return {snapshot,plan};}
test('modified source ticks cannot enter a host transaction',()=>{const f=fixture();f.plan.segments[0].sourceIn='1';const p={...f.plan};delete p.planHash;f.plan.planHash=hash(p);assert.throws(()=>guard.validateApply(f.snapshot,f.plan,['video:0'],hash),/SOURCE_TIME_MISMATCH/);});
test('protected camera track cannot be removed',()=>{const f=fixture();f.snapshot.tracks[0].protected=true;assert.throws(()=>guard.validateApply(f.snapshot,f.plan,['video:0'],hash));});
test('snapshot mismatch blocks before clone',()=>{const f=fixture();f.plan.snapshotHash='different';assert.throws(()=>guard.validateApply(f.snapshot,f.plan,['video:0'],hash));});
test('no gap or overlap is admitted',()=>{const f=fixture();f.plan.segments[0].startFrame=1;const p={...f.plan};delete p.planHash;f.plan.planHash=hash(p);assert.throws(()=>guard.validateApply(f.snapshot,f.plan,['video:0'],hash));});
test('valid exact frame plan is admitted',()=>{const f=fixture();assert.equal(guard.validateApply(f.snapshot,f.plan,['video:0'],hash),true);});
test('unverified host version blocks before any editing',()=>{const f=fixture();f.snapshot.supportFlags.hostApplyVerified=false;assert.throws(()=>guard.validateApply(f.snapshot,f.plan,['video:0'],hash),/HOST_SUPPORT_REQUIRED/);});
test('cuts require a per-batch authorization callback before host queries',async()=>{const vm=require('node:vm'),fs=require('node:fs');const context={require:name=>name==='premierepro'?{}:name==='./guard.js'?guard:{},sha256:sha};context.globalThis=context;vm.runInNewContext(fs.readFileSync(require.resolve('../plugin/host.js'),'utf8'),context);await assert.rejects(context.ContentriumHost.apply({}, {}, [], {}),/HOST_AUTHORIZATION_REQUIRED/);});

function dense(count){const f=fixture();f.snapshot.range.endFrame=count*60;f.snapshot.clips[0].endTicks=f.snapshot.clips[0].outTicks=String(BigInt(count*60)*8467200000n);f.plan.segments=Array.from({length:count},(_,i)=>({startFrame:i*60,endFrame:(i+1)*60,sourceClipInstanceKey:'A',sourceIn:String(BigInt(i*60)*8467200000n),sourceOut:String(BigInt((i+1)*60)*8467200000n),cameraId:'CA',reason:'speaker'}));const content={...f.plan};delete content.planHash;f.plan.planHash=hash(content);return f;}
test('dense 60-minute plan admits 5404 batches and counts preserved outside fragments',()=>{
  const f=dense(1800);assert.equal(guard.validateApply(f.snapshot,f.plan,['video:0'],hash),true);assert.equal(guard.editBatchCount(f.snapshot,f.plan,['video:0']),5404);
  f.snapshot.range={startFrame:30,endFrame:107970};f.plan.segments[0].startFrame=30;f.plan.segments[0].sourceIn=String(30n*8467200000n);f.plan.segments.at(-1).endFrame=107970;f.plan.segments.at(-1).sourceOut=String(107970n*8467200000n);
  const content={...f.plan};delete content.planHash;f.plan.planHash=hash(content);assert.equal(guard.validateApply(f.snapshot,f.plan,['video:0'],hash),true);assert.equal(guard.editBatchCount(f.snapshot,f.plan,['video:0']),5410);
});
test('unsupported dense plan fails before first native query or clone',async()=>{
  const f=dense(2800),vm=require('node:vm');let nativeQueries=0;
  const context={require:name=>name==='premierepro'?new Proxy({},{get(){nativeQueries++;throw new Error('native query');}}):name==='./guard.js'?guard:{},sha256:sha};context.globalThis=context;
  vm.runInNewContext(require('node:fs').readFileSync(require.resolve('../plugin/host.js'),'utf8'),context);
  await assert.rejects(context.ContentriumHost.apply(f.plan,f.snapshot,['video:0'],{check:async()=>true}),/APPLY_CAPACITY_EXCEEDED.*Split/);assert.equal(nativeQueries,0);
});
