const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const sha=require('../plugin/vendor/sha256');
const stable=v=>Array.isArray(v)?'['+v.map(stable).join(',')+']':v&&typeof v==='object'?'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+stable(v[k])).join(',')+'}':JSON.stringify(v);
const hash=v=>sha(stable(v));
const path=require('node:path').join(__dirname,'../plugin/sync.js');
const sync=fs.existsSync(path)?require(path):{};
const TPS=254016000000n;
function durable(value={}){return {beforeBatch:async()=>({execute:true}),afterBatch:async()=>{},onResult:async()=>{},...value};}
function clip(key,asset,track,start,end,inside=0){return {instanceKey:key,assetId:asset,projectItemRef:asset,trackRef:track,mediaType:track.split(':')[0],startTicks:String(BigInt(start)*TPS),endTicks:String(BigInt(end)*TPS),inTicks:String(BigInt(inside)*TPS),outTicks:String(BigInt(inside+end-start)*TPS),speed:1,disabled:false,channelMap:[],effectFingerprint:'unchanged-effects',supportFlags:{timeMappingSupported:true,effectPreservationSupported:true,linkedAudioSafe:true}};}
function fixture(){
  const snapshot={schemaVersion:1,projectRef:'P',sequenceRef:'S',fps:{num:30,den:1},tracks:[{trackRef:'video:0',mediaType:'video',index:0,muted:false,protected:false},{trackRef:'audio:0',mediaType:'audio',index:0,muted:false,protected:true}],sources:[{assetId:'R',canonicalPath:'reference.wav'},{assetId:'B',canonicalPath:'camera.mov'}],clips:[clip('ref','R','audio:0',10,20,2),clip('camera','B','video:0',30,40,3)],supportFlags:{hostApplyVerified:true}};
  snapshot.snapshotHash=hash(snapshot);
  const plan={schemaVersion:1,snapshotHash:snapshot.snapshotHash,referenceAssetId:'R',offsets:{R:0,B:1},selectedClipInstanceKeys:['ref','camera'],anchorReferenceTicks:'2032128000000'};
  plan.syncPlanHash=hash(plan);return {snapshot,plan};
}
function sign(f){delete f.snapshot.snapshotHash;f.snapshot.snapshotHash=hash(f.snapshot);f.plan.snapshotHash=f.snapshot.snapshotHash;delete f.plan.syncPlanHash;f.plan.syncPlanHash=hash(f.plan);}
test('sync installer and pure preflight are present',()=>{assert.equal(typeof sync.install,'function');assert.equal(typeof sync.preflight,'function');});
test('source offset maps source In to the anchored session rather than adding to existing placement',()=>{const f=fixture();const moves=sync.preflight(f.plan,f.snapshot,hash).moves;assert.equal(moves[1].startTicks,'3048192000000');assert.equal(moves[1].endTicks,'5588352000000');assert.equal(moves[1].deltaTicks,'-4572288000000');assert.equal(moves[0].deltaTicks,'0');});
test('explicitly selected audio and video preserve source duration and In Out',()=>{const f=fixture();f.snapshot.clips.push(clip('camera-audio','B','audio:1',30,40,3));f.snapshot.tracks.push({trackRef:'audio:1',mediaType:'audio',index:1,protected:true,muted:false});f.plan.selectedClipInstanceKeys.push('camera-audio');sign(f);const moves=sync.preflight(f.plan,f.snapshot,hash).moves;assert.equal(moves[2].startTicks,'3048192000000');assert.equal(moves[2].endTicks,'5588352000000');assert.equal(moves[2].inTicks,'762048000000');assert.equal(moves[2].outTicks,'3302208000000');});
test('same asset instance outside explicit selection is left untouched',()=>{const f=fixture();f.snapshot.clips.push(clip('other-B','B','video:0',50,60));sign(f);const result=sync.preflight(f.plan,f.snapshot,hash);assert.equal(result.moves.length,2);assert.equal(result.expectedClips.find(c=>c.instanceKey==='other-B').startTicks,'12700800000000');});
test('modified authorized offsets cannot pass the plan hash',()=>{const f=fixture();f.plan.offsets.B=2;assert.throws(()=>sync.preflight(f.plan,f.snapshot,hash),/SYNC_PLAN_HASH/);});
test('changed snapshot and unregistered selection cannot be applied',()=>{const f=fixture();f.plan.snapshotHash='other';assert.throws(()=>sync.preflight(f.plan,f.snapshot,hash),/SNAPSHOT/);const g=fixture();g.plan.selectedClipInstanceKeys.push('unknown');sign(g);assert.throws(()=>sync.preflight(g.plan,g.snapshot,hash),/SYNC_SELECTION/);});
test('negative placement fails before a native clone',()=>{const f=fixture();f.plan.offsets.B=-12;sign(f);assert.throws(()=>sync.preflight(f.plan,f.snapshot,hash),/SYNC_NEGATIVE/);});
test('offsets requiring review or invalid numbers are not inferred as zero',()=>{for(const value of [undefined,NaN,Infinity,'invalid',true]){const f=fixture();if(value===undefined)delete f.plan.offsets.B;else f.plan.offsets.B=value;delete f.plan.syncPlanHash;f.plan.syncPlanHash=hash(f.plan);assert.throws(()=>sync.preflight(f.plan,f.snapshot,hash),/SYNC_OFFSET/);}});
test('disabled speed-changed or effect-unverified selected clips are rejected',()=>{for(const change of [c=>c.disabled=true,c=>c.speed=2,c=>c.supportFlags.timeMappingSupported=false,c=>c.supportFlags.effectPreservationSupported=false]){const f=fixture();change(f.snapshot.clips[1]);sign(f);assert.throws(()=>sync.preflight(f.plan,f.snapshot,hash),/SYNC_SOURCE/);}});
test('collision with a protected nonselected clip on the target track is rejected',()=>{const f=fixture();f.snapshot.clips.push(clip('protected','B','video:0',15,25));sign(f);assert.throws(()=>sync.preflight(f.plan,f.snapshot,hash),/SYNC_COLLISION/);});
test('collision between selected clips is rejected while half-open adjacency is allowed',()=>{const f=fixture();f.snapshot.clips.push(clip('second','B','video:0',50,60,8));f.plan.selectedClipInstanceKeys.push('second');sign(f);assert.throws(()=>sync.preflight(f.plan,f.snapshot,hash),/SYNC_COLLISION/);f.snapshot.clips[2]=clip('second','B','video:0',50,60,13);sign(f);assert.equal(sync.preflight(f.plan,f.snapshot,hash).moves[2].startTicks,'5588352000000');});
test('frame placement uses rational FPS at both sides of its half-frame boundary',()=>{for(const [seconds,expected] of [[.01668333333,'0'],[.016683333333333334,'8475667200']]){const f=fixture();f.snapshot.fps={num:30000,den:1001};f.snapshot.clips[0]=clip('ref','R','audio:0',2,12,2);f.snapshot.clips[1]=clip('camera','B','video:0',30,40);f.plan.anchorReferenceTicks='0';f.plan.offsets.B=seconds;sign(f);assert.equal(sync.preflight(f.plan,f.snapshot,hash).moves[1].startTicks,expected);}});
test('an exact half frame goes to the later frame',()=>{const f=fixture();f.snapshot.fps={num:25,den:1};f.snapshot.clips[0]=clip('ref','R','audio:0',2,12,2);f.snapshot.clips[1]=clip('camera','B','video:0',30,40);f.plan.anchorReferenceTicks='0';f.plan.offsets.B=.02;sign(f);assert.equal(sync.preflight(f.plan,f.snapshot,hash).moves[1].startTicks,'10160640000');});
test('an anchor conflicting with the selected reference placement is rejected',()=>{const f=fixture();f.plan.anchorReferenceTicks='0';sign(f);assert.throws(()=>sync.preflight(f.plan,f.snapshot,hash),/SYNC_REFERENCE/);});
test('readback detects a linked side effect on an unselected clip and modified source trims',()=>{const f=fixture();f.snapshot.clips.push(clip('unselected-audio','B','audio:1',30,40,3));f.snapshot.tracks.push({trackRef:'audio:1',mediaType:'audio',index:1,protected:true,muted:false});sign(f);const result=sync.preflight(f.plan,f.snapshot,hash);const after=structuredClone(f.snapshot);after.clips=structuredClone(result.expectedClips);assert.equal(sync.verifyReadback(after,result,f.snapshot,hash),true);after.clips[2].startTicks='0';assert.throws(()=>sync.verifyReadback(after,result,f.snapshot,hash),/SYNC_READBACK/);const moved=structuredClone(f.snapshot);moved.clips=structuredClone(result.expectedClips);moved.clips[1].inTicks='0';assert.throws(()=>sync.verifyReadback(moved,result,f.snapshot,hash),/SYNC_READBACK/);});
test('native apply requires a per-batch authorization callback',async()=>{const host={hash};sync.install(host);await assert.rejects(host.applySync(fixture().plan,fixture().snapshot,{}),/SYNC_AUTHORIZATION/);});
test('the universal planHash alias does not change canonical sync content',()=>{const f=fixture();f.plan.planHash=f.plan.syncPlanHash;assert.equal(sync.preflight(f.plan,f.snapshot,hash).moves[1].startTicks,'3048192000000');f.plan.planHash='wrong';assert.throws(()=>sync.preflight(f.plan,f.snapshot,hash),/SYNC_PLAN_HASH/);});
test('snapshot contents must match the authorized snapshot digest',()=>{const f=fixture();f.snapshot.clips[1].inTicks='0';assert.throws(()=>sync.preflight(f.plan,f.snapshot,hash),/SYNC_SNAPSHOT/);});
test('subtick rounding matches server tick rounding before frame quantization',()=>{const f=fixture();f.snapshot.fps={num:25,den:1};f.snapshot.clips[0]=clip('ref','R','audio:0',2,12,2);f.snapshot.clips[1]=clip('camera','B','video:0',30,40);f.plan.anchorReferenceTicks='0';f.plan.offsets.B=.019999999999999997;sign(f);assert.equal(sync.preflight(f.plan,f.snapshot,hash).moves[1].startTicks,'10160640000');});
test('service placements must exactly agree with calculated selected clip movements',()=>{const f=fixture();f.plan.placements=[{instanceKey:'ref',assetId:'R',trackRef:'audio:0',startTicks:'2540160000000',endTicks:'5080320000000'},{instanceKey:'camera',assetId:'B',trackRef:'video:0',startTicks:'3048192000000',endTicks:'5588352000000'}];sign(f);assert.equal(sync.preflight(f.plan,f.snapshot,hash).moves.length,2);f.plan.placements[1].startTicks='0';sign(f);assert.throws(()=>sync.preflight(f.plan,f.snapshot,hash),/SYNC_PLACEMENT/);});
// Only the Adobe boundary is substituted. Transactions execute the actual actions
// against independent original/clone clip state, including regenerated instance keys.
function nativeFixture(){
  const f=fixture(),sequences=[];
  function sequence(guid,clips){return {guid,name:'Interview',clips,getVideoTrack:async()=>({getTrackItems:()=>host.transitions?[{}]:[]}),getAudioTrack:async()=>({getTrackItems:()=>host.transitions?[{}]:[]}),getProjectItem:async()=>({createSetNameAction:name=>()=>{sequences.find(s=>s.guid===guid).name=name;}}),createCloneAction:()=>()=>{sequences.push(sequence('clone',structuredClone(clips)));}};}
  const original=sequence('S',structuredClone(f.snapshot.clips));sequences.push(original);
  const project={getSequences:async()=>sequences,openSequence:async()=>true,save:async()=>{if(host.changeOriginalOnSave)original.clips[0].startTicks='0';return true;}};
  const host={hash,ppro:{ClipProjectItem:{cast:pi=>pi},Constants:{TrackItemType:{TRANSITION:1}}},time:v=>v,transaction:(p,name,build)=>{const actions=[];build({addAction:a=>actions.push(a)});actions.forEach(a=>a());},snapshot:async(seq=original)=>{
    const snapshot=structuredClone(f.snapshot),objects=new Map();snapshot.clips=seq.clips.map(c=>{const copy={...c};const before=f.snapshot.clips.find(v=>v.instanceKey===c.instanceKey);if(c.startTicks!==before.startTicks)copy.instanceKey=c.instanceKey+'@'+c.startTicks;objects.set(copy.instanceKey,{getProjectItem:async()=>({isSequence:async()=>host.opaqueKind==='nested',isMergedClip:async()=>host.opaqueKind==='merged',isMulticamClip:async()=>host.opaqueKind==='multicam',hasProxy:async()=>host.opaqueKind==='proxy'}),isAdjustmentLayer:async()=>host.opaqueKind==='adjustment',createMoveAction:delta=>()=>{c.startTicks=String(BigInt(c.startTicks)+delta);c.endTicks=String(BigInt(c.endTicks)+delta);}});return copy;});
    snapshot.sequenceRef=seq.guid;delete snapshot.snapshotHash;snapshot.snapshotHash=hash(snapshot);return {snapshot,sequence:seq,project,objects};
  }};sync.install(host);return {...f,host,sequences,original};
}
test('native sync moves cloned items through empty staging without touching the original',async()=>{const f=nativeFixture();const result=await f.host.applySync(f.plan,f.snapshot,durable({check:async()=>{}}));assert.equal(result.readbackVerified,true);assert.equal(result.originalUnchanged,true);assert.equal(f.original.clips[1].startTicks,'7620480000000');assert.equal(f.sequences[1].clips[1].startTicks,'3048192000000');assert.equal(f.sequences[1].clips[1].inTicks,'762048000000');assert.equal(result.sequenceRef,'clone');});
test('a rejected gate before final movement leaves a partial clone and cannot report completion',async()=>{const f=nativeFixture();let calls=0,stopped=false;await assert.rejects(f.host.applySync(f.plan,f.snapshot,durable({check:async()=>{if(++calls===4){stopped=true;throw new Error('UPDATE_IN_PROGRESS');}},stopped:()=>stopped})),e=>e.message==='UPDATE_IN_PROGRESS'&&e.resultSequenceRef==='clone');assert.equal(f.original.clips[1].startTicks,'7620480000000');assert.equal(f.sequences[1].clips[1].startTicks,'10414656000000');});
test('native nested merged multicam adjustment and unverified proxy sources block before clone',async()=>{for(const kind of ['nested','merged','multicam','adjustment','proxy']){const f=nativeFixture();f.host.opaqueKind=kind;await assert.rejects(f.host.applySync(f.plan,f.snapshot,{check:async()=>{}}),/SYNC_SOURCE/);assert.equal(f.sequences.length,1);}});
test('transitions on a selected track require separate preservation proof before clone',async()=>{const f=nativeFixture();f.host.transitions=true;await assert.rejects(f.host.applySync(f.plan,f.snapshot,{check:async()=>{}}),/SYNC_TRANSITION/);assert.equal(f.sequences.length,1);});
test('original readback is verified after asynchronous project save before reporting success',async()=>{const f=nativeFixture();f.host.changeOriginalOnSave=true;await assert.rejects(f.host.applySync(f.plan,f.snapshot,durable({check:async()=>{}})),/SYNC_ORIGINAL_CHANGED/);});
test('failed project save cannot produce a successful preservation receipt',async()=>{const f=nativeFixture();const live=await f.host.snapshot();live.project.save=async()=>false;await assert.rejects(f.host.applySync(f.plan,f.snapshot,durable({check:async()=>{}})),/HOST_SAVE_FAILED/);});
test('production decimal seconds strings keep cross-language hash and exact placement semantics',()=>{for(const [seconds,expected] of [['1.0','3048192000000'],['1e-06','2794176000000']]){const f=fixture();f.plan.offsets={R:'0.0',B:seconds};sign(f);f.plan.planHash=f.plan.syncPlanHash;assert.equal(sync.preflight(f.plan,f.snapshot,hash).moves[1].startTicks,expected);}});
test('malformed and unbounded decimal string offsets are rejected before conversion',()=>{for(const seconds of ['1e999999','1e-999999','Infinity','NaN',' 1.0','1.0x']){const f=fixture();f.plan.offsets.B=seconds;sign(f);assert.throws(()=>sync.preflight(f.plan,f.snapshot,hash),/SYNC_OFFSET/);}});
const python=require('node:path').join(__dirname,'../.build-venv/Scripts/python.exe');
test('actual Python coordinator decimal plans pass JavaScript hash and placement verification',{skip:!fs.existsSync(python)&&'Project Python runtime is unavailable'},()=>{
  const script=`import json,tempfile
from contentrium_cut.coordinator import Coordinator
from contentrium_cut.contract import canonical_hash
from test_coordinator import CoordinatorTests
output=[]
for value in [2.0,1e-6,.019999999999999997]:
 snapshot,result=CoordinatorTests('runTest').sync_fixture()
 snapshot['supportFlags']['hostApplyVerified']=True
 for clip in snapshot['clips']:
  clip['effectFingerprint']='known-effect'
  clip['supportFlags']={'timeMappingSupported':True,'effectPreservationSupported':True,'linkedAudioSafe':True}
 snapshot.pop('snapshotHash');snapshot['snapshotHash']=canonical_hash(snapshot)
 result['offsets']['b']=value
 with tempfile.TemporaryDirectory() as directory:
  coordinator=Coordinator(directory);coordinator.bind('panel',snapshot)
  output.append({'snapshot':snapshot,'plan':coordinator.sync_plan('panel',result,['mic','mic-b'])})
print(json.dumps(output))`;
  const cwd=require('node:path').join(__dirname,'..'),cases=JSON.parse(require('node:child_process').execFileSync(python,['-c',script],{cwd,env:{...process.env,PYTHONPATH:'companion;tests',PYTHONDONTWRITEBYTECODE:'1'},encoding:'utf8'}));
  assert.equal(cases[0].plan.offsets.a,'0.0');assert.equal(cases[1].plan.offsets.b,'1e-06');
  for(const value of cases)assert.equal(sync.preflight(value.plan,value.snapshot,hash).moves.length,2);
});
