function fail(code){throw new Error(code);}
function integer(v){return Number.isSafeInteger(v);}
function tick(v){if(typeof v!=='string'||!/^-?\d+$/.test(v))fail('INVALID_TICKS');return BigInt(v);}
function frameTicks(frame,fps){if(!integer(frame)||!integer(fps.num)||!integer(fps.den)||fps.num<=0||fps.den<=0)fail('INVALID_TIME');const n=BigInt(frame)*254016000000n*BigInt(fps.den),d=BigInt(fps.num);if(n%d!==0n)fail('FRAME_RATE_UNSUPPORTED');return n/d;}
function validateApply(snapshot,plan,cameraTrackRefs,hash){
  if(!snapshot||!plan||!Array.isArray(plan.segments)||!plan.segments.length)fail('INVALID_PLAN');
  if(plan.snapshotHash!==snapshot.snapshotHash)fail('SNAPSHOT_MISMATCH');
  const content={...plan};delete content.planHash;if(hash(content)!==plan.planHash)fail('PLAN_HASH_MISMATCH');
  for(const flag of ['timeMappingSupported','audioPreservationSupported','effectPreservationSupported'])if(snapshot.supportFlags?.[flag]!==true)fail('HOST_SUPPORT_REQUIRED');
  if(!Array.isArray(cameraTrackRefs)||!cameraTrackRefs.length||new Set(cameraTrackRefs).size!==cameraTrackRefs.length)fail('CAMERA_TRACKS_REQUIRED');
  const tracks=new Map(snapshot.tracks.map(t=>[t.trackRef,t])),clips=new Map(snapshot.clips.map(c=>[c.instanceKey,c]));
  for(const ref of cameraTrackRefs){const t=tracks.get(ref);if(!t||t.mediaType!=='video'||t.protected||t.muted)fail('CAMERA_TRACK_PROTECTED');}
  let cursor=snapshot.range.startFrame;const end=snapshot.range.endFrame;if(!integer(cursor)||!integer(end)||end<=cursor)fail('INVALID_RANGE');
  for(const segment of plan.segments){
    if(!integer(segment.startFrame)||!integer(segment.endFrame)||segment.startFrame!==cursor||segment.endFrame<=cursor||segment.endFrame>end)fail('PLAN_GAP_OR_OVERLAP');
    const c=clips.get(segment.sourceClipInstanceKey);if(!c||c.mediaType!=='video'||!cameraTrackRefs.includes(c.trackRef)||c.disabled||c.speed!==1||c.supportFlags?.timeMappingSupported!==true||c.supportFlags?.effectPreservationSupported!==true)fail('SOURCE_UNSUPPORTED');
    const first=frameTicks(segment.startFrame,snapshot.fps),last=frameTicks(segment.endFrame,snapshot.fps);
    if(first<tick(c.startTicks)||last>tick(c.endTicks))fail('SOURCE_COVERAGE');
    if(tick(segment.sourceIn)!==tick(c.inTicks)+first-tick(c.startTicks)||tick(segment.sourceOut)!==tick(c.inTicks)+last-tick(c.startTicks))fail('SOURCE_TIME_MISMATCH');
    if(typeof segment.reason!=='string'||!segment.reason)fail('PLAN_REASON_REQUIRED');cursor=segment.endFrame;
  }
  if(cursor!==end)fail('PLAN_GAP_OR_OVERLAP');return true;
}
module.exports={validateApply,frameTicks,tick};
