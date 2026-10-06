/* Service-authorized offsets only; source media and the original sequence stay intact. */
const {tick,frameTicks}=require('./guard.js');
const TPS=254016000000n;
function fail(code){throw new Error(code);}
function floor(n,d){return n>=0n?n/d:-((-n+d-1n)/d);}
function offsetRatio(seconds){
  // Decimal strings are the production wire format: Python/JS JSON encode
  // floats differently. Finite numeric values remain compatible with old plans.
  if(!['number','string'].includes(typeof seconds)||String(seconds).length>256||!Number.isFinite(Number(seconds)))fail('SYNC_OFFSET_REQUIRED');
  const match=String(seconds).match(/^(-?)(\d+)(?:\.(\d+))?(?:e([+-]?\d+))?$/i);
  if(!match||Math.abs(Number(match[4]||0))>324)fail('SYNC_OFFSET_REQUIRED');
  const fraction=match[3]||'',power=Number(match[4]||0)-fraction.length;
  let n=BigInt(match[2]+fraction)*TPS*(match[1]?-1n:1n),d=1n;
  if(power>=0)n*=10n**BigInt(power);else d=10n**BigInt(-power);
  return {n,d};
}
function withoutKey(clip){const result={...clip};delete result.instanceKey;return result;}
function clipContent(clips,hash){return clips.map(c=>hash(withoutKey(c))).sort();}
function verifyReadback(after,expected,baseline,hash){
  if(hash(after.tracks)!==hash(baseline.tracks)||hash(after.sources)!==hash(baseline.sources)||
      hash(after.fps)!==hash(baseline.fps)||hash(clipContent(after.clips,hash))!==hash(clipContent(expected.expectedClips,hash)))fail('SYNC_READBACK_MISMATCH');
  return true;
}
function preflight(plan,snapshot,hash){
  if(!plan||plan.schemaVersion!==1||!snapshot||plan.snapshotHash!==snapshot.snapshotHash)fail('SYNC_SNAPSHOT_MISMATCH');
  if(snapshot.supportFlags?.hostApplyVerified!==true)fail('SYNC_HOST_UNVERIFIED');
  const signed={...plan};delete signed.syncPlanHash;delete signed.planHash;
  if(typeof hash!=='function'||hash(signed)!==plan.syncPlanHash||
      (plan.planHash!==undefined&&plan.planHash!==plan.syncPlanHash))fail('SYNC_PLAN_HASH_MISMATCH');
  const snapshotContent={...snapshot};delete snapshotContent.snapshotHash;
  if(hash(snapshotContent)!==snapshot.snapshotHash)fail('SYNC_SNAPSHOT_HASH_MISMATCH');
  const keys=plan.selectedClipInstanceKeys;
  if(!Array.isArray(keys)||!keys.length||new Set(keys).size!==keys.length||keys.some(k=>typeof k!=='string'))fail('SYNC_SELECTION_REQUIRED');
  if(!plan.offsets||typeof plan.offsets!=='object'||Array.isArray(plan.offsets)||
      typeof plan.referenceAssetId!=='string'||!Object.prototype.hasOwnProperty.call(plan.offsets,plan.referenceAssetId)||
      offsetRatio(plan.offsets[plan.referenceAssetId]).n!==0n)fail('SYNC_OFFSET_REQUIRED');
  const anchor=tick(plan.anchorReferenceTicks),perFrame=frameTicks(1,snapshot.fps);
  const clips=new Map(snapshot.clips.map(c=>[c.instanceKey,c])),tracks=new Map(snapshot.tracks.map(t=>[t.trackRef,t]));
  if(clips.size!==snapshot.clips.length)fail('SYNC_SELECTION_AMBIGUOUS');
  let reference=false;
  const moves=keys.map(key=>{
    const clip=clips.get(key),track=tracks.get(clip?.trackRef);
    if(!clip||!track)fail('SYNC_SELECTION_UNKNOWN');
    if(!['video','audio'].includes(clip.mediaType)||track.mediaType!==clip.mediaType||clip.disabled||track.muted||
        clip.speed!==1||clip.supportFlags?.timeMappingSupported!==true||clip.supportFlags?.effectPreservationSupported!==true||
        !clip.effectFingerprint)fail('SYNC_SOURCE_UNSUPPORTED');
    if(!Object.prototype.hasOwnProperty.call(plan.offsets,clip.assetId))fail('SYNC_OFFSET_REQUIRED');
    const offset=offsetRatio(plan.offsets[clip.assetId]),inside=tick(clip.inTicks),outside=tick(clip.outTicks);
    const start=tick(clip.startTicks),end=tick(clip.endTicks),duration=end-start;
    if(start<0n||inside<0n||duration<=0n||outside-inside!==duration)fail('SYNC_SOURCE_TIME_INVALID');
    const target=(anchor+inside)*offset.d+offset.n;
    if(target<0n)fail('SYNC_NEGATIVE_POSITION');
    // Match the service's exact decimal seconds -> integer ticks -> frame rounding.
    const desiredTicks=floor(2n*target+offset.d,2n*offset.d);
    let positioned=floor(2n*desiredTicks+perFrame,2n*perFrame)*perFrame;
    if(clip.assetId===plan.referenceAssetId){
      reference=true;if(start-inside!==anchor)fail('SYNC_REFERENCE_ANCHOR_MISMATCH');
      positioned=start; // The existing reference is the anchor, including its exact audio tick.
    }
    return {instanceKey:key,assetId:clip.assetId,trackRef:clip.trackRef,startTicks:String(positioned),endTicks:String(positioned+duration),
      deltaTicks:String(positioned-start),inTicks:clip.inTicks,outTicks:clip.outTicks};
  });
  if(!reference)fail('SYNC_REFERENCE_SELECTION_REQUIRED');
  if(plan.placements!==undefined){
    if(!Array.isArray(plan.placements)||plan.placements.length!==moves.length||
        new Set(plan.placements.map(p=>p?.instanceKey)).size!==moves.length)fail('SYNC_PLACEMENT_MISMATCH');
    for(const move of moves){const placement=plan.placements.find(p=>p?.instanceKey===move.instanceKey);
      if(!placement||['assetId','trackRef','startTicks','endTicks'].some(key=>placement[key]!==move[key]))fail('SYNC_PLACEMENT_MISMATCH');
    }
  }
  const byKey=new Map(moves.map(m=>[m.instanceKey,m]));
  const expectedClips=snapshot.clips.map(c=>{const m=byKey.get(c.instanceKey);return m?{...c,startTicks:m.startTicks,endTicks:m.endTicks}:{...c};});
  for(const selected of moves){
    for(const other of expectedClips){
      if(other.instanceKey!==selected.instanceKey&&other.trackRef===selected.trackRef&&tick(other.startTicks)<tick(selected.endTicks)&&tick(other.endTicks)>tick(selected.startTicks))fail('SYNC_COLLISION');
    }
  }
  return {moves,expectedClips,perFrame:String(perFrame)};
}
function install(host){
  host.applySync=async function(plan,input,control={}){
    if(typeof control.check!=='function')fail('SYNC_AUTHORIZATION_REQUIRED');
    const prepared=preflight(plan,input,host.hash),live=await (host.resolveInput?host.resolveInput(input):host.snapshot());
    const originalHash=input.hostSnapshotHash||input.snapshotHash;
    if(live.snapshot.snapshotHash!==originalHash)fail('SYNC_SNAPSHOT_CHANGED');
    verifyReadback(live.snapshot,{expectedClips:input.clips},input,host.hash);
    // Snapshot flags do not identify opaque native source types or transitions.
    // The installed Adobe 26.5 SDK exposes these read-only queries; reject until
    // source-time/transition preservation has an independent host proof.
    const checkedTracks=new Set();
    for(const move of prepared.moves){
      const object=live.objects.get(move.instanceKey);if(!object)fail('SYNC_SOURCE_MISSING');
      const source=host.ppro.ClipProjectItem.cast(await object.getProjectItem());
      if(await source.isSequence()||await source.isMergedClip()||await source.isMulticamClip()||
          await source.hasProxy()||await object.isAdjustmentLayer())fail('SYNC_SOURCE_UNSUPPORTED');
      if(!checkedTracks.has(move.trackRef)){
        const track=input.tracks.find(t=>t.trackRef===move.trackRef);
        const native=await live.sequence[track.mediaType==='video'?'getVideoTrack':'getAudioTrack'](track.index);
        if(native.getTrackItems(host.ppro.Constants.TrackItemType.TRANSITION,false).length)fail('SYNC_TRANSITION_UNVERIFIED');
        checkedTracks.add(move.trackRef);
      }
    }
    let result;
    const mutations=require('./mutation.js').controller(host,input.sequenceRef,control);
    const check=mutations.check,batch=(name,build)=>mutations.batch(live.project,name,build);
    async function readback(expected){
      const after=await host.snapshot(result,live.project);
      verifyReadback(after.snapshot,expected,live.snapshot,host.hash);
      if((await host.snapshot(live.sequence,live.project)).snapshot.snapshotHash!==originalHash)fail('SYNC_ORIGINAL_CHANGED');
      return after;
    }
    try{
      const existing=new Set((await live.project.getSequences()).map(s=>String(s.guid)));
      await batch('Contentrium CUT · 싱크 원본 보존 복제',c=>c.addAction(live.sequence.createCloneAction()));
      const created=(await live.project.getSequences()).filter(s=>!existing.has(String(s.guid)));
      if(created.length!==1)fail('SYNC_CLONE_IDENTITY_FAILED');result=created[0];await mutations.result(String(result.guid));
      const item=await result.getProjectItem();
      await batch('Contentrium CUT · 싱크 결과 이름',c=>c.addAction(item.createSetNameAction('Contentrium CUT · 싱크 · '+live.sequence.name)));
      let current=await readback({expectedClips:input.clips});
      const moving=prepared.moves.filter(m=>m.deltaTicks!=='0');
      if(moving.length){
        // Empty staging positions prevent transient collisions when selected clips swap places.
        let cursor=input.clips.reduce((end,c)=>tick(c.endTicks)>end?tick(c.endTicks):end,0n)+BigInt(prepared.perFrame)*30n;
        const staging=new Map();
        for(const move of moving){const clip=input.clips.find(c=>c.instanceKey===move.instanceKey);staging.set(move.instanceKey,String(cursor));cursor+=tick(clip.endTicks)-tick(clip.startTicks)+BigInt(prepared.perFrame)*30n;}
        const stageExpected={expectedClips:input.clips.map(c=>staging.has(c.instanceKey)?{...c,startTicks:staging.get(c.instanceKey),endTicks:String(tick(staging.get(c.instanceKey))+tick(c.endTicks)-tick(c.startTicks))}:{...c})};
        const stageObjects=new Map();
        for(const move of moving){const object=current.objects.get(move.instanceKey);if(!object)fail('SYNC_CLONE_CLIP_MISSING');stageObjects.set(move.instanceKey,object);}
        await batch('Contentrium CUT · 싱크 이동 준비',c=>{for(const move of moving)c.addAction(stageObjects.get(move.instanceKey).createMoveAction(host.time(tick(staging.get(move.instanceKey))-tick(input.clips.find(v=>v.instanceKey===move.instanceKey).startTicks))));});
        current=await readback(stageExpected);
        const targetObjects=new Map();
        for(const move of moving){
          const original=input.clips.find(c=>c.instanceKey===move.instanceKey),matches=current.snapshot.clips.filter(c=>c.assetId===move.assetId&&c.trackRef===move.trackRef&&c.startTicks===staging.get(move.instanceKey)&&c.inTicks===original.inTicks&&c.outTicks===original.outTicks);
          if(matches.length!==1)fail('SYNC_STAGE_IDENTITY_FAILED');targetObjects.set(move.instanceKey,current.objects.get(matches[0].instanceKey));
        }
        await batch('Contentrium CUT · 싱크 배치',c=>{for(const move of moving)c.addAction(targetObjects.get(move.instanceKey).createMoveAction(host.time(tick(move.startTicks)-tick(staging.get(move.instanceKey)))));});
      }
      await readback(prepared);await check();
      if(await live.project.openSequence(result)!==true)fail('HOST_OPEN_RESULT_FAILED');await check();if(await live.project.save()!==true)fail('HOST_SAVE_FAILED');
      const after=await readback(prepared);
      return {originalUnchanged:true,readbackVerified:true,sequenceRef:String(result.guid),sequenceName:result.name,snapshot:after.snapshot};
    }catch(error){
      if(result){error.resultSequenceRef=String(result.guid);if(!control.stopped?.()){try{const item=await result.getProjectItem();await batch('Contentrium CUT · 미완료 싱크 표시',c=>c.addAction(item.createSetNameAction('Contentrium CUT · 중단됨 · 싱크 · '+live.sequence.name)));}catch(cleanupError){error.cleanupError=String(cleanupError);}}}
      throw error;
    }
  };
  return host;
}
module.exports={install,preflight,verifyReadback};
