/* Actual selection and durable workflow; only the external Adobe/UXP boundary is doubled. */
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {create,INTENT_KEY}=require('../plugin/workflow');
const hash=JSON.stringify;
function fixture({disableIneffective=false,outputDisabled=false,partialOutputDisabled=false,changeSource=false,changeOriginal=false,saveResult=true}={}){
  const events=[],sequences=[],objects=new Map();
  function item(assetId,name){return {name,getId:()=>assetId,isSequence:async()=>false,isMergedClip:async()=>false,isMulticamClip:async()=>false,isOffline:async()=>false,hasProxy:async()=>false,
    getMediaFilePath:async()=>changeSource&&events.includes('save')&&assetId==='camera'?'D:/changed.mov':'D:/'+assetId+'.mov',getInPoint:async()=>({ticks:'0'}),getOutPoint:async()=>({ticks:'300'})};}
  const camera=item('camera','Camera'),mic=item('mic','Mic');
  const original={guid:'source-sequence',clips:[{instanceKey:'source',assetId:'camera',mediaType:'video',disabled:false,startTicks:'0'}]};sequences.push(original);
  const result={guid:'result-sequence',clips:[]};
  function place(source,track){for(const mediaType of ['video','audio']){const clip={instanceKey:source.getId()+':'+mediaType,assetId:source.getId(),mediaType,trackRef:mediaType+':'+track,disabled:mediaType==='audio'&&source.getId()==='mic'&&outputDisabled,startTicks:'0'};result.clips.push(clip);
    objects.set(clip.instanceKey,{createSetDisabledAction:disabled=>()=>{events.push('disable:'+clip.assetId);if(!disableIneffective)clip.disabled=disabled;}});}}
  const project={guid:'project',getRootItem:async()=>({}),getSequences:async()=>sequences,
    createSequenceFromMedia:async(_name,items)=>{events.push('create');place(items[0],0);sequences.push(result);return result;},
    openSequence:async sequence=>{assert.equal(sequence,result);events.push('open');return true;},save:async()=>{events.push('save');if(changeOriginal)original.clips[0].startTicks='1';if(partialOutputDisabled)result.clips.push({...result.clips.find(c=>c.assetId==='mic'&&c.mediaType==='audio'),instanceKey:'mic:audio:extra',disabled:true});return saveResult;}};
  const editor={createOverwriteItemAction:(source,_time,video,audio)=>()=>{assert.equal(video,audio);place(source,video);},
    createRemoveItemsAction:(selected,ripple,media,shift)=>()=>{assert.equal(ripple,false);assert.equal(media,'VIDEO');assert.equal(shift,false);result.clips=result.clips.filter(c=>!selected.keys.has(c.instanceKey));}};
  const host={hash,ppro:{ClipProjectItem:{cast:source=>source},Guid:{fromString:id=>id},Project:{getProject:async id=>id==='project'?project:null},
    Constants:{MediaType:{VIDEO:'VIDEO',AUDIO:'AUDIO'}},TickTime:{TIME_ZERO:'0'},SequenceEditor:{getEditor:()=>editor},
    TrackItemSelection:{createEmptySelection:fn=>fn({keys:new Set(),addItem(value){for(const [key,object] of objects)if(object===value){this.keys.add(key);return true;}return false;}})}},
    transaction:async(_project,_name,build)=>{const actions=[];build({addAction:action=>actions.push(action)});for(const action of actions)action();},
    snapshot:async(sequence)=>{const snapshot={sequenceRef:String(sequence.guid),clips:structuredClone(sequence.clips)};snapshot.snapshotHash=hash(snapshot);return {snapshot,objects};}};
  const module={exports:{}};vm.runInNewContext(fs.readFileSync('plugin/selection.js','utf8'),{module,require:name=>name==='uxp'?{host:{version:'26.5.2'}}:require('../plugin/'+name.slice(2)),Map,Set,TextDecoder});module.exports.install(host);
  const sources=[camera,mic].map(source=>({assetId:source.getId(),path:'D:/'+source.getId()+'.mov',name:source.name,bounds:{VIDEO:{in:'0',out:'300'},AUDIO:{in:'0',out:'300'}}}));
  const selection={projectRef:'project',sources,items:[camera,mic],project},choices=[{assetId:'camera',role:'camera',outputAudio:false},{assetId:'mic',role:'audio',outputAudio:true}];
  const rows=new Map(),storage={get length(){return rows.size;},key:i=>[...rows.keys()][i],getItem:async key=>rows.get(key),setItem:async(key,value)=>rows.set(key,value),removeItem:async key=>rows.delete(key)};
  const calls=[],workflow=create({storage,randomId:()=> 'input-request',api:async(path,body)=>{calls.push({path,body});
    if(path==='/input/begin')return {execute:true,applyId:'input-lease',epoch:1,planHash:'input-plan',capability:{projectRef:'project',sourceRecordsDigest:hash(sources),assets:[{assetId:'camera',hasVideo:true,hasAudio:true},{assetId:'mic',hasVideo:true,hasAudio:true}]}};
    if(path==='/apply/check')return {execute:true};if(path==='/apply/end')return {status:body.status};return {};}});
  const run=()=>workflow.run({kind:'input',beginPath:'/input/begin',body:{choices,epoch:1},native:(_approved,control)=>host.createSelectedInput(selection,choices,control),receipt:(approved,readback)=>({...readback.inputReceipt,planHash:approved.planHash})});
  return {run,host,selection,choices,result,original,events,calls,rows};
}
test('saved selected input verifies enabled output and disabled excluded audio before durable completion',async()=>{
  const f=fixture(),sourceBefore=structuredClone(f.selection.sources),originalBefore=structuredClone(f.original);const result=await f.run();
  assert.equal(result.inputReceipt.saved,true);assert.deepEqual(f.selection.sources,sourceBefore);assert.deepEqual(f.original,originalBefore);
  assert.equal(result.snapshot.clips.find(c=>c.assetId==='camera'&&c.mediaType==='audio').disabled,true);
  assert.equal(result.snapshot.clips.find(c=>c.assetId==='mic'&&c.mediaType==='audio').disabled,false);
  assert.equal(result.snapshot.clips.some(c=>c.assetId==='mic'&&c.mediaType==='video'),false);
  assert.ok(f.events.indexOf('save')>f.events.indexOf('disable:camera'));assert.equal(f.calls.at(-1).body.status,'completed');assert.equal(f.rows.has(INTENT_KEY),false);
});
test('ineffective audio disable rejects actual selection readback before workflow completion receipt',async()=>{
  const f=fixture({disableIneffective:true});await assert.rejects(f.run(),e=>e.message==='HOST_INPUT_READBACK_FAILED'&&e.resultSequenceRef==='result-sequence');
  assert.equal(f.events.includes('save'),true);assert.equal(f.result.clips.find(c=>c.assetId==='camera'&&c.mediaType==='audio').disabled,false);
  assert.equal(f.calls.some(c=>c.path==='/apply/end'&&c.body.status==='completed'),false);assert.equal(f.calls.at(-1).body.status,'failed');assert.equal(f.rows.has(INTENT_KEY),true);
});
test('disabled requested output audio cannot return an input completion receipt',async()=>{
  const f=fixture({outputDisabled:true});await assert.rejects(f.run(),/HOST_INPUT_READBACK_FAILED/);assert.equal(f.calls.some(c=>c.path==='/apply/end'&&c.body.status==='completed'),false);
});
test('every clip of requested output audio must be enabled in the final saved readback',async()=>{
  const f=fixture({partialOutputDisabled:true});await assert.rejects(f.run(),/HOST_INPUT_READBACK_FAILED/);assert.equal(f.calls.some(c=>c.path==='/apply/end'&&c.body.status==='completed'),false);
});
for(const [option,message] of [['changeSource','HOST_ORIGINAL_SOURCE_CHANGED'],['changeOriginal','HOST_ORIGINAL_CHANGED'],['saveResult','HOST_SAVE_FAILED']])test('input completion rejects '+message,async()=>{
  const f=fixture({[option]:option==='saveResult'?false:true});await assert.rejects(f.run(),new RegExp(message));assert.equal(f.calls.some(c=>c.path==='/apply/end'&&c.body.status==='completed'),false);assert.equal(f.rows.has(INTENT_KEY),true);
});
