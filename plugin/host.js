/* Adobe UXP only. Host changes stay synchronous inside locked transactions. */
(function(root){
  const ppro=require('premierepro');
  const guard=require('./guard.js');
  const TICKS=254016000000;
  function stable(value){if(Array.isArray(value))return '['+value.map(stable).join(',')+']';if(value&&typeof value==='object')return '{'+Object.keys(value).sort().map(k=>JSON.stringify(k)+':'+stable(value[k])).join(',')+'}';return JSON.stringify(value);}
  function hash(value){return sha256(stable(value));}
  function guid(value){return String(value);}
  function ticks(value){return String(value.ticks);}
  function time(value){return ppro.TickTime.createWithTicks(String(value));}
  function transaction(project,name,build){let result=false;project.lockedAccess(()=>{result=project.executeTransaction(compound=>build(compound),name);});if(!result)throw new Error('HOST_TRANSACTION_FAILED: '+name);}
  async function componentFingerprint(item){
    const chain=await item.getComponentChain();const components=[];
    for(let i=0;i<chain.getComponentCount();i++){
      const c=chain.getComponentAtIndex(i);const params=[];
      for(let j=0;j<c.getParamCount();j++){
        const p=c.getParam(j);const keys=p.getKeyframeListAsTickTimes();
        const samples=[];for(const t of keys)samples.push({ticks:ticks(t),value:await p.getValueAtTime(t)});
        params.push({index:j,value:await p.getValueAtTime(ppro.TickTime.TIME_ZERO),keys:samples});
      }
      components.push({matchName:await c.getMatchName(),params});
    }
    return hash(components);
  }
  async function snapshot(sequenceArg, projectArg){
    const project=projectArg||await ppro.Project.getActiveProject();if(!project)throw new Error('PROJECT_REQUIRED');
    const sequence=sequenceArg||await project.getActiveSequence();if(!sequence)throw new Error('SEQUENCE_REQUIRED');
    const hostVersion=require('uxp').host.version;
    const hostVerified=hostVersion==='26.5.2';
    const timebase=await sequence.getTimebase();const perFrame=Number(timebase);
    if(!Number.isSafeInteger(perFrame)||perFrame<=0)throw new Error('FRAME_RATE_UNSUPPORTED: '+timebase);
    let a=TICKS,b=perFrame;while(b){const t=a%b;a=b;b=t;}const fps={num:TICKS/a,den:perFrame/a};
    const tracks=[],clips=[],sources=[];const objects=new Map();
    for(const mediaType of ['video','audio']){
      const count=await sequence[mediaType==='video'?'getVideoTrackCount':'getAudioTrackCount']();
      for(let index=0;index<count;index++){
        const track=await sequence[mediaType==='video'?'getVideoTrack':'getAudioTrack'](index);
        const trackRef=mediaType+':'+index;tracks.push({trackRef,mediaType,index,name:track.name,muted:await track.isMuted(),protected:mediaType==='audio'});
        const items=track.getTrackItems(ppro.Constants.TrackItemType.CLIP,false);
        for(let ordinal=0;ordinal<items.length;ordinal++){
          const item=items[ordinal],pi=await item.getProjectItem(),clipPi=ppro.ClipProjectItem.cast(pi);
          const assetId=String(pi.getId());const canonicalPath=await clipPi.getMediaFilePath();
          const startTicks=ticks(await item.getStartTime()),endTicks=ticks(await item.getEndTime());
          const inTicks=ticks(await item.getInPoint()),outTicks=ticks(await item.getOutPoint());
          const speed=await item.getSpeed(),reversed=await item.isSpeedReversed();
          let effectFingerprint=null,effectError=null;try{effectFingerprint=await componentFingerprint(item);}catch(e){effectError=String(e);}
          const instanceKey=[trackRef,assetId,startTicks,endTicks,inTicks,ordinal].join('|');objects.set(instanceKey,item);
          clips.push({instanceKey,assetId,projectItemRef:assetId,trackRef,mediaType,startTicks,endTicks,inTicks,outTicks,speed,disabled:await item.isDisabled(),channelMap:[],effectFingerprint,supportFlags:{timeMappingSupported:speed===1&&!reversed,effectPreservationSupported:!effectError,linkedAudioSafe:hostVerified},effectError});
          if(!sources.some(s=>s.assetId===assetId))sources.push({assetId,canonicalPath,offline:await clipPi.isOffline()});
        }
      }
    }
    const endFrame=Math.round(Number(ticks(await sequence.getEndTime()))/perFrame);
    const result={schemaVersion:1,projectRef:guid(project.guid),sequenceRef:guid(sequence.guid),projectName:project.name,sequenceName:sequence.name,fps,range:{startFrame:0,endFrame},tracks,clips,sources,supportFlags:{timeMappingSupported:clips.every(c=>c.supportFlags.timeMappingSupported),audioPreservationSupported:true,effectPreservationSupported:clips.every(c=>c.supportFlags.effectPreservationSupported),hostApplyVerified:hostVerified}};
    result.snapshotHash=hash(result);return {snapshot:result,project,sequence,objects,perFrame};
  }
  async function createFixture(paths,projectPath){
    const project=await ppro.Project.createProject(projectPath);if(!project)throw new Error('TEST_PROJECT_CREATION_FAILED');
    const bin=await project.getRootItem();if(!await project.importFiles(paths,true,bin,false))throw new Error('TEST_IMPORT_FAILED');
    const items=await bin.getItems();const media=[];for(const pi of items){try{const path=await ppro.ClipProjectItem.cast(pi).getMediaFilePath();if(paths.includes(path))media.push(pi);}catch(_){} }
    const byPath={};for(const pi of media)byPath[await ppro.ClipProjectItem.cast(pi).getMediaFilePath()]=pi;
    if(paths.some(p=>!byPath[p]))throw new Error('TEST_MEDIA_RESOLVE_FAILED');
    const sequence=await project.createSequenceFromMedia('Contentrium CUT — integration fixture',[byPath[paths[0]]],bin);
    const editor=ppro.SequenceEditor.getEditor(sequence);
    transaction(project,'Contentrium CUT test fixture',compound=>{
      compound.addAction(editor.createOverwriteItemAction(byPath[paths[1]],ppro.TickTime.TIME_ZERO,1,1));
      compound.addAction(editor.createOverwriteItemAction(byPath[paths[2]],ppro.TickTime.TIME_ZERO,2,2));
      compound.addAction(editor.createOverwriteItemAction(byPath[paths[3]],ppro.TickTime.TIME_ZERO,0,0));
    });
    await project.openSequence(sequence);await project.save();return (await snapshot(sequence,project)).snapshot;
  }
  function invariant(snapshot,refs){const protectedClips=snapshot.clips.filter(c=>c.mediaType==='audio'||!refs.includes(c.trackRef));const ids=new Set(protectedClips.map(c=>c.assetId));return hash({tracks:snapshot.tracks.filter(t=>t.mediaType==='audio'||!refs.includes(t.trackRef)),clips:protectedClips.map(c=>{const v={...c};delete v.instanceKey;return v;}),sources:snapshot.sources.filter(s=>ids.has(s.assetId))});}
  function withSelection(items,callback){let made=false;ppro.TrackItemSelection.createEmptySelection(s=>{for(const item of items)if(!s.addItem(item))throw new Error('HOST_SELECTION_FAILED');callback(s);made=true;});if(!made)throw new Error('HOST_SELECTION_FAILED');}
  async function cloneSequence(project,sequence,name,batch,check,onResult){
    const existing=new Set((await project.getSequences()).map(s=>guid(s.guid)));
    await check();await batch('Contentrium CUT · 원본 보존 복제',c=>c.addAction(sequence.createCloneAction()));
    const created=(await project.getSequences()).filter(s=>!existing.has(guid(s.guid)));
    if(created.length!==1)throw new Error('HOST_CLONE_IDENTITY_FAILED');
    await onResult(guid(created[0].guid));
    const item=await created[0].getProjectItem();await check();await batch('Contentrium CUT · 결과 이름',c=>c.addAction(item.createSetNameAction(name)));return created[0];
  }
  async function apply(plan,input,cameraTrackRefs,control={}){
    if(typeof control.check!=='function')throw new Error('HOST_AUTHORIZATION_REQUIRED');
    guard.validateApply(input,plan,cameraTrackRefs,hash);
    const live=await resolveInput(input);const originalHash=input.hostSnapshotHash||input.snapshotHash;if(live.snapshot.snapshotHash!==originalHash)throw new Error('SNAPSHOT_CHANGED');
    for(const ref of cameraTrackRefs){
      const native=await live.sequence.getVideoTrack(input.tracks.find(t=>t.trackRef===ref).index);
      if(native.getTrackItems(ppro.Constants.TrackItemType.TRANSITION,false).length)throw new Error('HOST_TRANSITION_UNVERIFIED');
      for(const clip of input.clips.filter(c=>c.trackRef===ref)){
        const object=live.objects.get(clip.instanceKey),source=ppro.ClipProjectItem.cast(await object.getProjectItem());
        if(await source.isSequence()||await source.isMergedClip()||await source.isMulticamClip()||await source.hasProxy()||await object.isAdjustmentLayer())throw new Error('HOST_SOURCE_UNSUPPORTED');
      }
    }
    const mutations=require('./mutation.js').controller(root.ContentriumHost,input.sequenceRef,control);
    const check=mutations.check,batch=(name,build)=>mutations.batch(live.project,name,build);
    await check();const result=await cloneSequence(live.project,live.sequence,'Contentrium CUT · '+live.sequence.name,batch,check,mutations.result);
    const baseline=await snapshot(result,live.project);const preserved=invariant(baseline.snapshot,cameraTrackRefs);
    const editor=ppro.SequenceEditor.getEditor(result),stageTrack=await result.getVideoTrackCount();
    // The new highest video track is both the temporary workspace (beyond the
    // sequence end) and the final cut track. No extra empty track remains above it.
    const outputTrack=stageTrack,outputTrackRef='video:'+outputTrack;
    const first=guard.frameTicks(input.range.startFrame,input.fps),last=guard.frameTicks(input.range.endFrame,input.fps);
    const sources=new Map(baseline.snapshot.clips.map(c=>[c.instanceKey,c]));const originals=baseline.snapshot.clips.filter(c=>c.mediaType==='video'&&cameraTrackRefs.includes(c.trackRef)&&guard.tick(c.startTicks)<last&&guard.tick(c.endTicks)>first);
    const fragments=[];
    for(const clip of originals){
      if(guard.tick(clip.startTicks)<first)fragments.push({clip,start:guard.tick(clip.startTicks),end:first,in:guard.tick(clip.inTicks),out:guard.tick(clip.inTicks)+first-guard.tick(clip.startTicks),target:baseline.snapshot.tracks.find(t=>t.trackRef===clip.trackRef).index,outside:true});
      if(guard.tick(clip.endTicks)>last)fragments.push({clip,start:last,end:guard.tick(clip.endTicks),in:guard.tick(clip.inTicks)+last-guard.tick(clip.startTicks),out:guard.tick(clip.outTicks),target:baseline.snapshot.tracks.find(t=>t.trackRef===clip.trackRef).index,outside:true});
    }
    for(const s of plan.segments)fragments.push({clip:sources.get(s.sourceClipInstanceKey),start:guard.frameTicks(s.startFrame,input.fps),end:guard.frameTicks(s.endFrame,input.fps),in:guard.tick(s.sourceIn),out:guard.tick(s.sourceOut),target:outputTrack,outside:false});
    let stageStart=guard.tick(ticks(await result.getEndTime()))+BigInt(baseline.perFrame)*30n;const staged=[];
    try{
      for(const fragment of fragments){
        await check();const current=await snapshot(result,live.project),source=current.objects.get(fragment.clip.instanceKey),sourceTrack=baseline.snapshot.tracks.find(t=>t.trackRef===fragment.clip.trackRef).index;
        const offset=stageStart-guard.tick(fragment.clip.startTicks);
        await batch('Contentrium CUT · 영상 복제',c=>c.addAction(editor.createCloneTrackItemAction(source,time(offset),stageTrack-sourceTrack,0,true,false)));
        const track=await result.getVideoTrack(stageTrack);const items=track.getTrackItems(ppro.Constants.TrackItemType.CLIP,false);let copy;
        for(const item of items){if(ticks(await item.getStartTime())===String(stageStart)&&String((await item.getProjectItem()).getId())===fragment.clip.assetId){if(copy)throw new Error('HOST_CLONE_AMBIGUOUS');copy=item;}}
        if(!copy||await componentFingerprint(copy)!==fragment.clip.effectFingerprint)throw new Error('HOST_EFFECT_PRESERVATION_FAILED');
        if(invariant((await snapshot(result,live.project)).snapshot,cameraTrackRefs.concat(['video:'+stageTrack]))!==preserved)throw new Error('HOST_AUDIO_OR_OVERLAY_CHANGED');
        await check();await batch('Contentrium CUT · 영상 범위',c=>{c.addAction(copy.createSetInPointAction(time(fragment.in)));c.addAction(copy.createSetOutPointAction(time(fragment.out)));});
        const trimmedStart=stageStart+fragment.in-guard.tick(fragment.clip.inTicks);const trimReadback={in:ticks(await copy.getInPoint()),out:ticks(await copy.getOutPoint()),start:ticks(await copy.getStartTime()),end:ticks(await copy.getEndTime())};if(trimReadback.in!==String(fragment.in)||trimReadback.out!==String(fragment.out)||trimReadback.start!==String(trimmedStart)||trimReadback.end!==String(trimmedStart+fragment.end-fragment.start))throw new Error('HOST_TRIM_MISMATCH: '+JSON.stringify({actual:trimReadback,expected:{in:String(fragment.in),out:String(fragment.out),start:String(trimmedStart),end:String(trimmedStart+fragment.end-fragment.start)}}));
        staged.push({...fragment,copy,stageStart:trimmedStart});stageStart+=guard.tick(fragment.clip.endTicks)-guard.tick(fragment.clip.startTicks)+BigInt(baseline.perFrame)*30n;
      }
      await check();const removal=await snapshot(result,live.project);
      await batch('Contentrium CUT · 카메라 영상 교체',c=>withSelection(originals.map(clip=>removal.objects.get(clip.instanceKey)),selected=>c.addAction(ppro.SequenceEditor.getEditor(result).createRemoveItemsAction(selected,false,ppro.Constants.MediaType.VIDEO,false))));
      for(const fragment of staged){
        await check();const track=await result.getVideoTrack(stageTrack);let freshCopy;for(const item of track.getTrackItems(ppro.Constants.TrackItemType.CLIP,false))if(ticks(await item.getStartTime())===String(fragment.stageStart))freshCopy=item;if(!freshCopy)throw new Error('HOST_STAGE_MISSING');await batch('Contentrium CUT · 컷 적용',c=>c.addAction(editor.createCloneTrackItemAction(freshCopy,time(fragment.start-fragment.stageStart),fragment.target-stageTrack,0,true,false)));
      }
      await check();const outputNative=await result.getVideoTrack(stageTrack),stageItems=[],stageStarts=new Map(staged.map(fragment=>[String(fragment.stageStart),fragment]));
      for(const item of outputNative.getTrackItems(ppro.Constants.TrackItemType.CLIP,false)){
        const fragment=stageStarts.get(ticks(await item.getStartTime()));if(!fragment)continue;
        if(String((await item.getProjectItem()).getId())!==fragment.clip.assetId||ticks(await item.getInPoint())!==String(fragment.in)||ticks(await item.getOutPoint())!==String(fragment.out))throw new Error('HOST_STAGE_MISMATCH');
        stageItems.push(item);
      }
      if(stageItems.length!==staged.length)throw new Error('HOST_STAGE_MISSING');
      await batch('Contentrium CUT · 임시 영상 정리',c=>{
        withSelection(stageItems,selected=>c.addAction(editor.createRemoveItemsAction(selected,false,ppro.Constants.MediaType.VIDEO,false)));
        c.addAction(outputNative.createSetNameAction('Contentrium CUT'));
      });
      const after=await snapshot(result,live.project),originalAfter=await snapshot(live.sequence,live.project);
      if(originalAfter.snapshot.snapshotHash!==originalHash)throw new Error('HOST_ORIGINAL_CHANGED');
      if(invariant(after.snapshot,cameraTrackRefs.concat(['video:'+stageTrack]))!==preserved)throw new Error('HOST_AUDIO_OR_OVERLAY_CHANGED');
      const applied=after.snapshot.clips.filter(c=>c.mediaType==='video'&&(cameraTrackRefs.includes(c.trackRef)||c.trackRef===outputTrackRef)&&guard.tick(c.startTicks)<last&&guard.tick(c.endTicks)>first);
      if(applied.length!==plan.segments.length)throw new Error('HOST_SEGMENT_COUNT_MISMATCH');
      const outputMeta=after.snapshot.tracks.find(t=>t.trackRef===outputTrackRef);
      if(!outputMeta||outputMeta.muted||outputMeta.name!=='Contentrium CUT'||outputMeta.index!==Math.max(...after.snapshot.tracks.filter(t=>t.mediaType==='video').map(t=>t.index))||applied.some(c=>c.trackRef!==outputTrackRef))throw new Error('HOST_OUTPUT_TRACK_MISMATCH');
      const outputClips=after.snapshot.clips.filter(c=>c.mediaType==='video'&&c.trackRef===outputTrackRef);
      if(outputClips.length!==plan.segments.length||after.snapshot.range.endFrame!==baseline.snapshot.range.endFrame)throw new Error('HOST_STAGE_CLEANUP_MISMATCH');
      for(const segment of plan.segments){const source=sources.get(segment.sourceClipInstanceKey),matches=applied.filter(c=>c.assetId===source.assetId&&c.startTicks===String(guard.frameTicks(segment.startFrame,input.fps))&&c.endTicks===String(guard.frameTicks(segment.endFrame,input.fps))&&c.inTicks===segment.sourceIn&&c.outTicks===segment.sourceOut);if(matches.length!==1||matches[0].effectFingerprint!==source.effectFingerprint)throw new Error('HOST_APPLIED_PLAN_MISMATCH');}
      await check();if(await live.project.openSequence(result)!==true)throw new Error('HOST_OPEN_RESULT_FAILED');await check();if(await live.project.save()!==true)throw new Error('HOST_SAVE_FAILED');
      const saved=await snapshot(result,live.project);
      if((await snapshot(live.sequence,live.project)).snapshot.snapshotHash!==originalHash)throw new Error('HOST_ORIGINAL_CHANGED');
      if(saved.snapshot.snapshotHash!==after.snapshot.snapshotHash)throw new Error('HOST_SAVE_CHANGED_RESULT');
      return {sequenceRef:guid(result.guid),sequenceName:result.name,outputTrackRef,originalUnchanged:true,audioAndOverlaysUnchanged:true,segments:applied,snapshot:saved.snapshot};
    }catch(error){error.resultSequenceRef=guid(result.guid);if(!control.stopped?.()){try{const item=await result.getProjectItem();await check();await batch('Contentrium CUT · 미완료 결과 표시',c=>c.addAction(item.createSetNameAction('Contentrium CUT · 중단됨 · '+live.sequence.name)));await live.project.openSequence(live.sequence);}catch(cleanupError){error.cleanupError=String(cleanupError);}}throw error;}
  }
  async function resolveInput(input){
    const project=await ppro.Project.getProject(ppro.Guid.fromString(input.projectRef));if(!project)throw new Error('PROJECT_CLOSED');
    const sequence=(await project.getSequences()).find(s=>guid(s.guid)===input.sequenceRef);if(!sequence)throw new Error('SEQUENCE_REMOVED');
    return snapshot(sequence,project);
  }
  root.ContentriumHost={snapshot,resolveInput,createFixture,apply,transaction,time,hash,stable,ppro};
})(globalThis);
