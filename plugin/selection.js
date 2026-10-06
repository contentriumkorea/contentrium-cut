/* Project-panel input creates only a new working sequence; source items stay read-only. */
function install(host){
  async function sourceRecord(item){
    const source=host.ppro.ClipProjectItem.cast(item);
    if(!source||await source.isSequence()||await source.isMergedClip()||await source.isMulticamClip()||await source.isOffline()||await source.hasProxy())throw new Error('PROJECT_SOURCE_UNSUPPORTED');
    const record={assetId:String(item.getId()),path:await source.getMediaFilePath(),name:item.name,bounds:{}};
    for(const type of ['VIDEO','AUDIO']){
      try{record.bounds[type]={in:String((await source.getInPoint(host.ppro.Constants.MediaType[type])).ticks),out:String((await source.getOutPoint(host.ppro.Constants.MediaType[type])).ticks)};}catch(_){record.bounds[type]=null;}
    }
    return record;
  }
  host.selectedSources=async function(){
    const project=await host.ppro.Project.getActiveProject();if(!project)throw new Error('PROJECT_REQUIRED');
    const items=await (await host.ppro.ProjectUtils.getSelection(project)).getItems();if(!items.length)throw new Error('PROJECT_SELECTION_REQUIRED');
    const sources=[];for(const item of items)sources.push(await sourceRecord(item));
    if(new Set(sources.map(s=>s.assetId)).size!==sources.length)throw new Error('PROJECT_SELECTION_AMBIGUOUS');
    return {projectRef:String(project.guid),sources,items,project};
  };
  host.createSelectedInput=async function(selection,choices,control){
    if(typeof control?.check!=='function')throw new Error('HOST_AUTHORIZATION_REQUIRED');
    if(require('uxp').host.version!=='26.5.2')throw new Error('HOST_SUPPORT_REQUIRED');
    if(!Array.isArray(choices)||choices.length!==selection.sources.length||!choices.some(c=>c.role==='camera')||!choices.some(c=>c.outputAudio===true))throw new Error('PROJECT_ROLES_REQUIRED');
    const byId=new Map(selection.sources.map((s,i)=>[s.assetId,{record:s,item:selection.items[i]}]));
    const used=new Set();for(const choice of choices){
      if(!byId.has(choice.assetId)||used.has(choice.assetId)||!['camera','audio','exclude'].includes(choice.role)||typeof choice.outputAudio!=='boolean')throw new Error('PROJECT_ROLES_REQUIRED');
      used.add(choice.assetId);
      const original=byId.get(choice.assetId);if(host.hash(await sourceRecord(original.item))!==host.hash(original.record))throw new Error('PROJECT_SELECTION_CHANGED');
    }
    const project=await host.ppro.Project.getProject(host.ppro.Guid.fromString(selection.projectRef));if(!project)throw new Error('PROJECT_CLOSED');
    const originals=new Map();for(const sequence of await project.getSequences())originals.set(String(sequence.guid),(await host.snapshot(sequence,project)).snapshot.snapshotHash);
    const ordered=choices.filter(c=>c.role==='camera').concat(choices.filter(c=>c.role==='audio'));
    const capability=control.capability;
    if(!capability||capability.projectRef!==selection.projectRef||capability.sourceRecordsDigest!==host.hash(selection.sources))throw new Error('INPUT_CAPABILITY_REQUIRED');
    for(const choice of ordered){const media=capability.assets?.find(a=>a.assetId===choice.assetId);if(!media||choice.role==='camera'&&!media.hasVideo||choice.role==='audio'&&!media.hasAudio||choice.outputAudio&&!media.hasAudio)throw new Error('INPUT_STREAM_UNSUPPORTED');}
    const mutations=require('./mutation.js').controller(host,'input:'+selection.projectRef,control),check=mutations.check;
    const bin=await project.getRootItem();
    const result=await mutations.run('Contentrium CUT · 입력 생성',()=>project.createSequenceFromMedia('Contentrium CUT · 입력',[byId.get(ordered[0].assetId).item],bin));
    if(!result||originals.has(String(result.guid)))throw new Error('HOST_INPUT_CREATION_FAILED');
    await mutations.result(String(result.guid));
    const editor=host.ppro.SequenceEditor.getEditor(result);
    try{
      for(let i=1;i<ordered.length;i++){
        await mutations.batch(project,'Contentrium CUT · 선택 파일 배치',c=>c.addAction(editor.createOverwriteItemAction(byId.get(ordered[i].assetId).item,host.ppro.TickTime.TIME_ZERO,i,i)));
      }
      const before=await host.snapshot(result,project);const outputIds=new Set(ordered.filter(c=>c.outputAudio).map(c=>c.assetId));
      const cameraIds=new Set(ordered.filter(c=>c.role==='camera').map(c=>c.assetId));
      const extraVideo=before.snapshot.clips.filter(c=>c.mediaType==='video'&&!cameraIds.has(c.assetId));
      if(extraVideo.length){await mutations.batch(project,'Contentrium CUT · 독립 오디오의 영상 제외',c=>host.ppro.TrackItemSelection.createEmptySelection(selected=>{for(const clip of extraVideo)if(!selected.addItem(before.objects.get(clip.instanceKey)))throw new Error('HOST_SELECTION_FAILED');c.addAction(editor.createRemoveItemsAction(selected,false,host.ppro.Constants.MediaType.VIDEO,false));}));}
      const extraAudio=before.snapshot.clips.filter(c=>c.mediaType==='audio'&&!outputIds.has(c.assetId));
      if(extraAudio.length){await mutations.batch(project,'Contentrium CUT · 출력 오디오 선택',c=>{for(const clip of extraAudio)c.addAction(before.objects.get(clip.instanceKey).createSetDisabledAction(true));});}
      await check();if(await project.openSequence(result)!==true)throw new Error('HOST_OPEN_RESULT_FAILED');await check();if(await project.save()!==true)throw new Error('HOST_SAVE_FAILED');
      for(const item of selection.items){const initial=byId.get(String(item.getId())).record;if(host.hash(await sourceRecord(item))!==host.hash(initial))throw new Error('HOST_ORIGINAL_SOURCE_CHANGED');}
      for(const sequence of await project.getSequences())if(originals.has(String(sequence.guid))&&(await host.snapshot(sequence,project)).snapshot.snapshotHash!==originals.get(String(sequence.guid)))throw new Error('HOST_ORIGINAL_CHANGED');
      const final=await host.snapshot(result,project);
      if(final.snapshot.clips.some(c=>c.mediaType==='video'&&!cameraIds.has(c.assetId)))throw new Error('HOST_INPUT_READBACK_FAILED');
      if(final.snapshot.clips.some(c=>c.mediaType==='audio'&&(outputIds.has(c.assetId)?c.disabled!==false:c.disabled!==true)))throw new Error('HOST_INPUT_READBACK_FAILED');
      for(const choice of ordered){const clips=final.snapshot.clips.filter(c=>c.assetId===choice.assetId);if(!clips.length||choice.role==='camera'&&!clips.some(c=>c.mediaType==='video')||choice.outputAudio&&!clips.some(c=>c.mediaType==='audio'&&!c.disabled))throw new Error('HOST_INPUT_READBACK_FAILED');}
      final.inputReceipt={resultSequenceRef:String(result.guid),resultSnapshotHash:final.snapshot.snapshotHash,sourceRecordsDigest:host.hash(selection.sources),priorSequencesHash:host.hash(Object.fromEntries(originals)),originalsUnchanged:true,saved:true};
      return final;
    }catch(e){e.resultSequenceRef=String(result.guid);throw e;}
  };
  return host;
}
module.exports={install};
