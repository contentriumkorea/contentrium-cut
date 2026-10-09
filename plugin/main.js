/* Native UXP panel; source media and provider credentials never leave this PC. */
const uxp=require('uxp'),bundle=require('./bundle.json');
const cutReview=require('./review.js');
require('./sync.js').install(ContentriumHost);
require('./selection.js').install(ContentriumHost);
const $=id=>document.getElementById(id);
const view=require('./view.js').install(document);
const connection=require('./connection.js').create(uxp,bundle,{onValidation:(count,descriptors)=>{validationRevision++;validationCount=count;const own=cacheValidation(count,descriptors),modelOwn=modelValidation(count,descriptors),syncOwn=syncValidation(count,descriptors);if(count&&!stopped){if(syncOwn){if(syncPoll.guidanceRevision===statusRevision){say('싱크 원본 파일을 확인하고 있습니다.');syncPoll.guidanceRevision=statusRevision;}}else if(modelOwn){if(modelRequest.guidanceRevision===statusRevision){say('화자 모델 리비전을 확인하고 있습니다.');modelRequest.guidanceRevision=statusRevision;}}else{say(own?'완료된 캐시를 확인하고 정리하고 있습니다.':'원본 파일의 내용이 분석 결과와 같은지 확인하고 있습니다.');if(own)cacheRequest.guidanceRevision=statusRevision;}}toggle();}});
function requestId(){const bytes=new Uint8Array(16);crypto.getRandomValues(bytes);return Array.from(bytes,b=>b.toString(16).padStart(2,'0')).join('');}
const workflow=require('./workflow.js').create({api:(...args)=>api(...args),storage:uxp.storage.secureStorage,randomId:requestId,
  stopped:()=>stopped,onBatch:value=>{batchRunning=value;},onResult:()=>say('결과 시퀀스에 편집을 적용하고 있습니다.')});
let credentials=null,state=null,connected=null,mode='separate',job=null,analysisJob=null,analysis=null,plan=null,syncResult=null,syncJob=null,planCameraRefs=[],applying=false,batchRunning=false,stopped=false,applyId=null,polling=false,pending=false;
const microphoneRows=[],cameraRows=[],speakerRows=[],calibrationRows=[],overrideRows=[],syncRows=[];
let microphoneSelectionCustomized=false;
let projectSelection=null,inputCapability=null,dismissedCandidate=null,localEditPending=false,resourceLoaded=false,settingsTimer=null,previewPlaying=false,rangeDirty=false,binding=false;let projectRead=null,nativePreparation=null,editingSubmission=null,correctionRequest=null,settingsRestore=null,settingsSave=null;
let settingsWriteTail=Promise.resolve();
let modelRequest=null,modelInputRevision=0,modelPoll=null;
let cacheRequest=null,syncPoll=null,examplePoll=null,refreshRequest=null,updateCheckRequest=null,updateRecoveryRequest=null,applyRecoveryRequest=null,cancelRequest=null,updateStartRequest=null;
let previewBusy=0,previewGeneration=0;
let resourceRequest=null,resourceInputRevision=0,resourceViewRevision=0,resourceInputDirty=false,stopRevision=0;
let savedSpeakerMappings={},savedSpeakerMappingScope=null,speakerRowsScope=null,localIntentError=null,panelContextConflict=false;
let analysisState=null;
let planInputHash=null,planInvalidated=false;
let overrideSerial=0,overrideErrorsOnly=false,overrideVisibleRows=new Set();
let validationCount=0,validationRevision=0,statusRevision=0,heartbeatRequest=null,heartbeatOwner=null,selectionRead=null,updateIntent=null;
let reviewPage=0,reviewWindow=null;
let microphoneIssue='',rangeIssue='',syncIssue='',policyIssue='',syncResultInputHash=null,syncInvalidated=false;
const selectedRows=[];
function selectedSourceMedia(row){return Array.isArray(inputCapability?.assets)?inputCapability.assets.find(media=>media.assetId===row.source.assetId):null;}
function selectedSourceEditable(row){return !workLocked()&&!!projectSelection&&selectedRows.includes(row)&&!!selectedSourceMedia(row);}
function selectedSourceFeedback(row,locked=workLocked()){
  const media=selectedSourceMedia(row),ready=!!projectSelection&&!!media,excluded=row.role.value==='exclude';
  row.role.disabled=locked||!ready;row.audio.disabled=locked||!ready||excluded||media.hasAudio!==true;
  const text=!ready?'미디어 확인 중 · 확인이 끝나면 설정할 수 있습니다.':excluded?'이 소스는 제외됩니다 · 출력 오디오 선택은 다시 포함하면 적용됩니다.':media.hasAudio!==true?'오디오 스트림 없음 · 출력 오디오를 사용할 수 없습니다.':'';
  if(row.selectionHint.textContent!==text)row.selectionHint.textContent=text;row.selectionHint.className='hint'+(text?'':' hidden');
}
function inputSourceChoices(){return selectedRows.map(row=>({assetId:row.source.assetId,role:row.role.value,outputAudio:row.role.value!=='exclude'&&selectedSourceMedia(row)?.hasAudio===true&&row.audio.checked}));}
function say(value){statusRevision++;$('status').textContent=value;}
function setConnection(ready,text){$('connection').textContent=text;$('connection-dot').className=ready?'connected':'disconnected';$('host-status').textContent=ready?'PREMIERE 로컬 연결됨':'PREMIERE 연결 대기';}
function invalidateAnalysis(){clearAnalysis();renderSpeakers(mode==='mixed'?[]:[...new Set(microphoneRows.filter(r=>r.check.checked).map(r=>r.speaker.value.trim()))]);scheduleSettings();say('음성 입력이 바뀌었습니다. 화자 분석을 다시 실행하세요.');toggle();}
function invalidatePlan(){clearPolicyPlan();scheduleSettings();toggle();}
function clearPolicyPlan(){
  planInvalidated=planInvalidated||!!plan;plan=null;planInputHash=null;reviewPage=0;reviewWindow=null;$('cut-count').textContent=$('review-count').textContent='—';
  for(const id of ['timeline','segments','reviews'])$(id).innerHTML='';
  $('segments').appendChild(element('p','컷 설정을 확인하고 편집안을 다시 만들어 주세요.','hint'));
  $('review-page-info').textContent='편집안을 만들어 주세요';
}
function policyInputsChanged(){if(!workLocked()&&connected)invalidatePlan();}
const messages={SOURCE_REANALYSIS_REQUIRED:'연결된 시퀀스의 모든 원본을 확인하려면 다시 분석하세요.',APPLY_CAPACITY_EXCEEDED:'편집 기록은 최대 8,192개 작업을 지원합니다. 검토 범위를 나누거나 컷 수를 줄여 주세요.',APPLY_RECOVERY_REQUIRED:'이전 편집 기록을 확인해야 합니다. 설정에서 중단 작업을 확인하세요.',APPLY_HOST_EXIT_REQUIRED:'프로젝트를 저장하고 Premiere를 정상 종료한 뒤 다시 열어 중단 작업을 확인하세요.',CORRECTION_REVISION_CONFLICT:'화자 교정 내용이 변경됐습니다. 최신 결과를 다시 불러왔습니다.',SPEAKER_LINK_REQUIRED:'아직 연결하지 않은 목소리가 있습니다. 화자 교정에서 연결하세요.',MODEL_NOT_READY:'설정에서 필요한 로컬 분석 모델을 준비하세요.',SOURCE_CHANGED:'분석한 원본 파일이 변경됐습니다. 소스를 다시 읽어 주세요.',HOST_SUPPORT_REQUIRED:'이 Premiere 버전에서는 적용 검증이 필요합니다.',INPUT_STREAM_UNSUPPORTED:'선택한 파일이 지정한 영상·오디오 역할을 지원하지 않습니다.',AUTH_REQUIRED:'편집 연결을 복구하고 있습니다.',SESSION_EXPIRED:'편집 연결을 복구하고 있습니다.',UPDATE_IN_PROGRESS:'업데이트를 위해 편집 작업이 중단됐습니다.'};
Object.assign(messages,{PANEL_CONTEXT_CONFLICT:'다른 CUT 패널이 이 설치를 제어하고 있습니다. 제어 중인 패널에서 계속하세요.',EDIT_INTENT_STORAGE_UNAVAILABLE:'이전 편집 기록을 읽지 못했습니다. 설정에서 중단 작업을 확인하세요. (EDIT_INTENT_STORAGE_UNAVAILABLE)',EDIT_INTENT_CORRUPT:'이전 편집 기록이 손상되었습니다. 설정에서 중단 작업을 확인하세요. (EDIT_INTENT_CORRUPT)'});
Object.assign(messages,{
  INVALID_OVERRIDE:'수동 고정 구간의 프레임 범위와 카메라를 확인하세요.',
  OVERRIDE_CONFLICT:'서로 다른 카메라의 수동 고정 구간이 겹칩니다. 구간 또는 카메라를 수정하세요.',
  OVERRIDE_COVERAGE_GAP:'고정한 카메라의 영상이 수동 구간 전체를 덮지 않습니다. 구간을 줄이거나 카메라를 바꾸세요.',
  PLAN_INPUT_CHANGED:'편집안 입력이 바뀌었거나 입력 출처를 확인할 수 없습니다. 컷 설정과 카메라 연결을 확인하고 편집안을 다시 만드세요.',
  SYNC_INPUT_CHANGED:'싱크 입력이 분석할 때와 달라졌거나 결과의 입력을 확인할 수 없습니다. 싱크를 다시 분석하세요.',
  CANCELED:'작업을 중단했습니다. 필요하면 분석을 다시 시작하세요.',
  CONTINUATION_EXPIRED:'원본 파일 확인 시간이 지났습니다. 소스 상태를 확인한 뒤 다시 분석하세요.',
  VALIDATION_EXPIRED:'원본 파일 확인 시간이 지났습니다. 소스 상태를 확인한 뒤 다시 분석하세요.',
  VALIDATION_WORKER_EXITED:'원본 파일 확인이 중단됐습니다. 소스 상태를 확인한 뒤 다시 분석하세요.',
  VALIDATION_STALE:'원본 파일 확인 결과를 사용할 수 없습니다. 소스를 다시 읽고 분석하세요.',
  VALIDATION_UNAVAILABLE:'원본 파일 확인을 실행할 수 없습니다. 편집 연결 상태를 확인한 뒤 다시 분석하세요.',
  VALIDATION_SCOPE:'원본 파일 확인 대상이 유효하지 않습니다. 소스를 다시 읽고 분석하세요.'
});
const currentCheckDiscarded=new Error('Discarded superseded timeline check');
function error(e){if(e===currentCheckDiscarded)return;say(messages[e.code]||((e.code||'작업 오류')+' · '+(e.message||String(e))));}
function isIntentReadError(e){return ['EDIT_INTENT_STORAGE_UNAVAILABLE','EDIT_INTENT_CORRUPT'].includes(e.code);}
function contextConflict(showGuide=true){panelContextConflict=true;credentials=null;stopped=true;plan=null;setConnection(false,'다른 CUT 패널이 제어 중');$('boot-status').className='notice';$('boot-status').querySelector('p').textContent=messages.PANEL_CONTEXT_CONFLICT;if(showGuide)say(messages.PANEL_CONTEXT_CONFLICT);toggle();}
function element(tag,text,cls){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;}
function number(value){const e=element('input');e.type='number';e.value=String(value);return e;}
function options(select,values,empty){select.innerHTML='';if(empty){const e=element('option',empty);e.value='';select.appendChild(e);}for(const [value,label] of values){const e=element('option',label);e.value=value;select.appendChild(e);}}
function label(text,input){const e=element('label',text);e.appendChild(input);return e;}
function basename(path){return path.split(/[\\/]/).pop();}
function settingsKey(){return 'cut-settings-'+ContentriumHost.hash({projectRef:connected.snapshot.projectRef,sequenceRef:connected.snapshot.sequenceRef});}
function settingsWriteReady(){return !!connected&&!!credentials&&!!state?.gateOpen&&!stopped&&!updateIntent&&!updateRecoveryRequest&&!applyRecoveryRequest&&!cancelRequest&&state?.compatible!==false&&!panelContextConflict&&!localEditPending&&!state?.applyRecovery?.blocked&&!binding&&!projectRead&&!applying&&!batchRunning&&(state?.stopEpoch===null||state?.stopEpoch===undefined);}
function scheduleSettings(){
  if(settingsTimer){clearTimeout(settingsTimer);settingsTimer=null;}
  if(!settingsWriteReady())return;
  const key=settingsKey(),timer=setTimeout(async()=>{if(settingsTimer!==timer)return;settingsTimer=null;if(!settingsWriteReady()||settingsKey()!==key)return;await saveSettings(true);},400);
  settingsTimer=timer;
}
async function saveSettings(automatic=false){
  if(!settingsWriteReady())return;
  if(!automatic&&settingsTimer){clearTimeout(settingsTimer);settingsTimer=null;}
  const token={automatic},key=settingsKey(),payload=JSON.stringify(captureSettings()),scope=[credentials,connected,mode,analysisState,analysisState?.revision,state.epoch,connected.snapshot.snapshotHash,connected.snapshot.hostSnapshotHash,projectSelection,inputCapability,plan,planInputHash,syncResult,syncJob,syncResultInputHash,job,validationCount,validationRevision,settingsRestore,correctionRequest,editingSubmission,nativePreparation],rows=selectedRows.slice();
  const current=()=>settingsSave===token&&settingsWriteReady()&&scope.every((value,index)=>value===[credentials,connected,mode,analysisState,analysisState?.revision,state.epoch,connected.snapshot.snapshotHash,connected.snapshot.hostSnapshotHash,projectSelection,inputCapability,plan,planInputHash,syncResult,syncJob,syncResultInputHash,job,validationCount,validationRevision,settingsRestore,correctionRequest,editingSubmission,nativePreparation][index])&&selectedRows.length===rows.length&&rows.every((row,index)=>selectedRows[index]===row)&&settingsKey()===key&&JSON.stringify(captureSettings())===payload;
  const prior=settingsWriteTail;let release;settingsWriteTail=new Promise(resolve=>{release=resolve;});settingsSave=token;
  if(!automatic)say('현재 시퀀스의 설정을 저장하고 있습니다.');const guidanceRevision=statusRevision;toggle();
  try{
    await prior;if(!current())return;
    await uxp.storage.secureStorage.setItem(key,payload);if(!current())return;
    if(!automatic&&statusRevision===guidanceRevision)say('현재 시퀀스의 설정을 저장했습니다.');
  }catch(e){if(current()&&statusRevision===guidanceRevision){if(automatic)say('설정을 자동 저장하지 못했습니다. 설정에서 다시 저장해 주세요.');else error(e);}}
  finally{release();if(settingsSave===token){settingsSave=null;toggle();}}
}
function captureSettings(){
  return {schemaVersion:2,projectRef:connected.snapshot.projectRef,sequenceRef:connected.snapshot.sequenceRef,mode,policy:policy(),policyInput:{minShot:$('min-shot').value,shortTurn:$('short-turn').value,overlap:$('overlap').value},
    overrideInput:overrideRows.map(r=>({first:r.first.value,last:r.last.value,camera:r.camera.value})),
    calibration:calibrationRows.map(r=>({key:r.instanceKey,speaker:r.speaker.value,first:r.first.value,last:r.last.value})),
    microphones:microphoneRows.map(r=>({key:r.clip.instanceKey,checked:r.check.checked,speaker:r.speaker.value,channel:r.channel.value,stream:r.stream?.value??'1'})),
    cameras:cameraRows.map(r=>({id:r.id,assets:connected.snapshot.clips.filter(c=>c.trackRef===r.id).map(c=>c.assetId).sort(),role:r.role.value,covered:r.covered.value})),
    speakers:speakerRows.map(r=>({id:r.id,camera:r.select.value})),speakerMappingScope:currentSpeakerMappingScope(),speakerCount:$('speaker-count').value,vadThreshold:$('vad-threshold').value,start:$('start-camera').value,reserve:$('reserve-camera').value,
    range:{startFrame:Number($('range-start').value),endFrame:Number($('range-end').value)},rangeInput:{start:$('range-start').value,end:$('range-end').value},sync:syncOptions(),syncInput:syncRows.map(r=>({assetId:r.source.assetId,stream:r.stream.value,channel:r.channel.value})),
    analysisReference:analysisState&&analysisJob?{schemaVersion:1,analysisId:analysisState.analysisId,jobId:analysisJob,revision:analysisState.revision,
      snapshotHash:analysisState.snapshotHash,mode,inputHash:ContentriumHost.hash(microphoneOptions())}:null};
}
function restoreSettings(settings){
  if(settings.schemaVersion!==2||settings.projectRef!==connected.snapshot.projectRef||settings.sequenceRef!==connected.snapshot.sequenceRef)throw new Error('현재 시퀀스의 설정이 아닙니다.');
  if(mode!==settings.mode){mode=settings.mode;for(const value of ['separate','mixed'])$('mode-'+value).className='mode'+(mode===value?' active':'');renderSources();}
  for(const r of microphoneRows){const v=settings.microphones.find(v=>v.key===r.clip.instanceKey);if(v){r.check.checked=v.checked;if(typeof v.checked==='boolean')microphoneSelectionCustomized=true;r.speaker.value=v.speaker;r.channel.value=v.channel;if(r.stream)r.stream.value=v.stream;}}
  const mappingScope=currentSpeakerMappingScope(),restoreMappings=sameSpeakerMappingScope(settings.speakerMappingScope,mappingScope);
  for(const r of cameraRows){const assets=connected.snapshot.clips.filter(c=>c.trackRef===r.id).map(c=>c.assetId).sort();const v=settings.cameras.find(v=>v.id===r.id&&ContentriumHost.hash(v.assets)===ContentriumHost.hash(assets));if(v){r.role.value=v.role;r.covered.value=mode==='mixed'&&!restoreMappings?'':v.covered;}}
  for(const r of calibrationRows){const v=settings.calibration?.find(v=>v.key===r.instanceKey);if(v){r.first.value=v.first;r.last.value=v.last;}}
  savedSpeakerMappings=restoreMappings?Object.fromEntries(settings.speakers.filter(v=>cameraValues().some(c=>c[0]===v.camera)).map(v=>[v.id,v.camera])):{};
  savedSpeakerMappingScope=restoreMappings?mappingScope:null;speakerRowsScope=null;
  renderSpeakers(mode==='mixed'?(analysisState?.analysis.sessionSpeakerIds||[]):microphoneRows.filter(r=>r.check.checked).map(r=>r.speaker.value));mappingInputs();
  if(cameraValues().some(c=>c[0]===settings.start))$('start-camera').value=settings.start;
  if(!settings.reserve||cameraValues().some(c=>c[0]===settings.reserve))$('reserve-camera').value=settings.reserve;
  $('min-shot').value=String(settings.policy.minShot);$('short-turn').value=String(settings.policy.shortTurn);$('overlap').value=String(settings.policy.overlap);
  for(const [id,key] of [['min-shot','minShot'],['short-turn','shortTurn'],['overlap','overlap']])if(typeof settings.policyInput?.[key]==='string')$(id).value=settings.policyInput[key];
  for(const [id,key] of [['speaker-count','speakerCount'],['vad-threshold','vadThreshold']])if(typeof settings[key]==='string'||typeof settings[key]==='number'&&Number.isFinite(settings[key]))$(id).value=String(settings[key]);
  const bounds=settings.range;
  if(bounds&&Number.isSafeInteger(bounds.startFrame)&&Number.isSafeInteger(bounds.endFrame)&&bounds.startFrame>=0&&bounds.endFrame>bounds.startFrame&&bounds.endFrame<=(connected.fullRangeEnd||connected.snapshot.range.endFrame)){
    $('range-start').value=String(bounds.startFrame);$('range-end').value=String(bounds.endFrame);rangeDirty=bounds.startFrame!==connected.snapshot.range.startFrame||bounds.endFrame!==connected.snapshot.range.endFrame;
  }
  if(settings.rangeInput&&typeof settings.rangeInput.start==='string'&&typeof settings.rangeInput.end==='string'){
    $('range-start').value=settings.rangeInput.start;$('range-end').value=settings.rangeInput.end;
    const input=rangeInput(connected.fullRangeEnd);rangeDirty=!!input.text||input.start!==connected.snapshot.range.startFrame||input.end!==connected.snapshot.range.endFrame;
  }
  const sync=settings.sync;
  if(sync&&['audio','manual','timecode'].includes(sync.method)){
    $('sync-method').value=sync.method;if(syncRows.some(r=>r.source.assetId===sync.reference))$('sync-reference').value=sync.reference;
    for(const r of syncRows){
      const selection=sync.sourceSelections?.find(s=>s.assetId===r.source.assetId);r.check.checked=!!selection;
      if(selection){r.stream.value=String(selection.streamIndex+1);r.channel.value=String(selection.channelIndex+1);}
      const manual=sync.manualOffsets?.[r.source.assetId];if(manual){r.offset.value=manual.offsetSeconds;r.confirmed.checked=manual.confirmed===true;}
      const clock=sync.timecodeConfirmations?.[r.source.assetId];if(clock){r.clockId.value=clock.clockId;r.date.value=clock.date;r.fps.value=clock.fps.num+'/'+clock.fps.den;r.drop.checked=clock.dropFrame===true;r.clockConfirmed.checked=clock.confirmed===true;}
    }
    for(const r of syncRows){const input=settings.syncInput?.find(v=>v.assetId===r.source.assetId);if(input&&typeof input.stream==='string'&&typeof input.channel==='string'){r.stream.value=input.stream;r.channel.value=input.channel;}}
    syncMethodChanged();
  }
  overrideRows.length=0;overrideErrorsOnly=false;overrideVisibleRows.clear();$('overrides').innerHTML='';
  const restoredOverrides=Array.isArray(settings.overrideInput)?settings.overrideInput:(settings.policy.overrides||[]).map(v=>({first:String(v.startFrame),last:String(v.endFrame),camera:v.cameraId}));
  for(const v of restoredOverrides){if(v&&typeof v.first==='string'&&typeof v.last==='string'&&typeof v.camera==='string'){addOverride(true);const r=overrideRows[overrideRows.length-1];r.first.value=v.first;r.last.value=v.last;overrideCameraOptions(r.camera,cameraValues(),v.camera);}}
  clearPolicyPlan();overrideFeedback();
}
async function readSavedSettings(current){
  try{
    const raw=await uxp.storage.secureStorage.getItem(settingsKey());if(!current())throw currentCheckDiscarded;
    return JSON.parse(typeof raw==='string'?raw:new TextDecoder().decode(raw));
  }catch(e){if(!current()||e===currentCheckDiscarded)throw currentCheckDiscarded;throw new Error('현재 시퀀스에 저장된 설정을 불러오지 못했습니다.');}
}
function settingsResponseGuard(allowBinding=false){
  const scoped=editingResponseGuard(allowBinding),input=connected?ContentriumHost.hash(captureSettings()):null;
  return ()=>scoped()&&(input===null?!connected:!!connected&&ContentriumHost.hash(captureSettings())===input);
}
async function restoreSavedAnalysis(settings,owned=()=>true,accepted=()=>{}){
  const ref=settings.analysisReference;if(!ref)return false;
  let scoped=settingsResponseGuard();const current=()=>owned()&&scoped();
  const checkOptions={responseCurrent:current,guardFactory:()=>{const guard=settingsResponseGuard(true);return ()=>owned()&&guard();},onSettledRead:()=>{scoped=settingsResponseGuard();accepted();},guidanceCurrent:()=>false};
  const matches=()=>ref.schemaVersion===1&&/^[a-f0-9]{64}$/.test(ref.analysisId)&&/^[a-zA-Z0-9_-]{1,128}$/.test(ref.jobId)&&
    Number.isSafeInteger(ref.revision)&&ref.revision>=0&&ref.snapshotHash===connected?.snapshot.snapshotHash&&ref.mode===mode&&!microphoneFeedback()&&analysisReferenceMatches(ref.inputHash);
  let restored;
  try{
    if(!current())throw currentCheckDiscarded;
    await requireCurrent(checkOptions);if(!current())throw currentCheckDiscarded;
    if(!matches())throw new Error('이전 분석의 타임라인 또는 음성 입력이 달라졌습니다. 새로 분석해 주세요.');
    const completed=await api('/jobs/'+ref.jobId);if(!current())throw currentCheckDiscarded;
    if(completed.kind!=='analysis'||completed.status!=='completed')throw new Error('이전 분석 작업을 확인할 수 없습니다. 새로 분석해 주세요.');
    restored=await api('/analyses/'+ref.analysisId);if(!current())throw currentCheckDiscarded;
    await requireCurrent(checkOptions);if(!current())throw currentCheckDiscarded;
    if(!matches()||restored.analysisId!==ref.analysisId||restored.snapshotHash!==ref.snapshotHash)throw new Error('이전 분석의 범위가 현재 시퀀스와 다릅니다.');
    if(!Number.isSafeInteger(restored.revision)||restored.revision<ref.revision)throw new Error('저장한 교정 이력을 확인할 수 없습니다.');
  }catch(e){if(!current())throw currentCheckDiscarded;throw e;}
  try{analysisJob=ref.jobId;acceptAnalysis(restored);binding=true;try{restoreSettings(settings);}finally{binding=false;}scheduleSettings();}finally{accepted();}
  return true;
}
async function loadSavedSettings(){
  const token={};settingsRestore=token;let scopeCurrent=settingsResponseGuard();const current=()=>settingsRestore===token&&scopeCurrent();
  say('현재 시퀀스의 설정을 불러오고 있습니다.');const guidanceRevision=statusRevision;toggle();
  try{
    if(settingsTimer){clearTimeout(settingsTimer);settingsTimer=null;}
    const settings=await readSavedSettings(current);if(!current())throw currentCheckDiscarded;
    binding=true;try{restoreSettings(settings);clearAnalysis();renderSpeakers(mode==='mixed'?[]:[...new Set(microphoneRows.filter(r=>r.check.checked).map(r=>r.speaker.value))]);}finally{binding=false;scopeCurrent=settingsResponseGuard();}
    let restored;try{restored=await restoreSavedAnalysis(settings,()=>settingsRestore===token,()=>{scopeCurrent=settingsResponseGuard();});}catch(e){if(!current())throw currentCheckDiscarded;throw e;}if(!current())throw currentCheckDiscarded;
    if(statusRevision===guidanceRevision)say(restored?'저장한 분석과 화자 교정을 불러왔습니다. 편집안을 다시 만들어 주세요.':'저장한 설정을 불러왔습니다. 음성 입력을 다시 분석하세요.');
  }catch(e){if(current()&&statusRevision===guidanceRevision)error(e);}finally{if(settingsRestore===token){settingsRestore=null;toggle();}}
}
async function api(path,body,method){return connection.request(path,body,method);}
function admitted(){if(cancelRequest)throw new Error('중단 요청 상태를 확인한 뒤 다시 실행하세요.');if(applyRecoveryRequest)throw new Error('중단 작업 기록을 확인한 뒤 다시 실행하세요.');if(updateRecoveryRequest)throw new Error('설치 복구 상태를 확인한 뒤 다시 실행하세요.');if(initializing||initializationIncomplete)throw new Error('편집 연결 확인을 마친 뒤 다시 실행하세요.');if(!credentials||!connected)throw new Error('Premiere에서 편집할 시퀀스를 열어 주세요.');if(!state?.gateOpen||stopped)throw new Error('현재 작업 상태를 확인한 뒤 다시 실행하세요.');if(localEditPending||state?.applyRecovery?.blocked)throw Object.assign(new Error('APPLY_RECOVERY_REQUIRED'),{code:'APPLY_RECOVERY_REQUIRED'});}
function missingModelGuidance(){
  if(state?.models?.[mode==='separate'?'silero':'community-1']?.status==='error')return mode==='separate'?'발화 모델 정보를 확인하지 못했습니다. Contentrium CUT Setup으로 설치를 복구하세요.':'혼합 녹음 모델 정보를 확인하지 못했습니다. 설정에서 모델을 다시 설치하세요.';
  return mode==='separate'?'발화 모델을 찾지 못했습니다. Contentrium CUT Setup으로 설치를 복구하세요.':'설정에서 혼합 녹음 모델을 준비하세요.';
}
function modelInstallMessage(status,code){
  const text=status==='completed'?'화자 모델 설치를 마쳤습니다. 다음 분석에서 모델 무결성을 확인합니다.':status==='canceling'?'모델 설치 중단을 요청했습니다. 실제 작업 종료를 기다리고 있습니다.':status==='interrupted'?'모델 설치 작업이 이전 연결에서 중단됐습니다. 다시 설치하려면 접근 토큰을 다시 입력하세요.':status==='canceled'?'모델 설치를 중단했습니다. 다시 설치하려면 접근 토큰을 다시 입력하세요.':
    ['AUTH_REQUIRED','SESSION_EXPIRED'].includes(code)?'편집 연결을 확인하지 못했습니다. 연결이 복구되면 접근 토큰을 다시 입력하고 모델 설치를 재시도하세요.':'모델 설치에 실패했습니다. 제공자 접근 권한과 네트워크를 확인하고 접근 토큰을 다시 입력해 재시도하세요.';
  const suffix=status==='failed'&&typeof code==='string'&&/^[A-Z][A-Z0-9_]{0,63}$/.test(code)?' ('+code+')':'';
  return text+suffix;
}
function modelInstallResult(status,code){
  const text=modelInstallMessage(status,code);$('model-install-status').textContent=text;return text;
}
function actionGuidance(){
  if(cancelRequest&&cancelCurrent(cancelRequest))return '중단 요청을 확인하고 있습니다. 실제 작업 종료 뒤 상태를 다시 확인하세요.';
  if(applyRecoveryRequest)return '중단 작업 기록을 확인하고 있습니다. 완료 후 결과 시퀀스를 확인하세요.';
  if(updateRecoveryRequest)return '설치 복구 상태를 확인하고 있습니다. 완료 후 현재 버전과 연결 상태를 확인하세요.';
  const update=state?.update?.updateState;
  if(update==='WAITING_HOST_EXIT')return '프로젝트를 저장하고 Premiere를 정상 종료하면 업데이트가 계속됩니다.';
  if(['RECOVERY_REQUIRED','FAILED'].includes(update))return '설정에서 업데이트 복구 상태를 확인하세요. 새 편집은 차단되어 있습니다.';
  if(updateIntent||update&&!['IDLE','COMPLETE','CANCELED','FAILED_BEFORE_REPLACE','ROLLED_BACK','UNAVAILABLE'].includes(update))return '업데이트를 진행하고 있습니다. 완료될 때까지 새 편집은 차단됩니다.';
  if(panelContextConflict)return messages.PANEL_CONTEXT_CONFLICT;
  if(initializing)return '편집 연결과 중단 작업 기록을 확인하고 있습니다.';
  if(initializationIncomplete)return '편집 연결 확인이 끝나지 않았습니다. 새로고침으로 다시 확인하세요.';
  if(!credentials)return 'Premiere 편집 연결을 준비하고 있습니다. 연결 안내를 확인하세요.';
  if(state?.admissionError?.code==='COMPONENT_MISMATCH'||state&&(state.appVersion!==bundle.appVersion||state.bundleId!==bundle.bundleId||state.protocolVersion!==bundle.protocolVersion))return '플러그인과 분석 엔진 버전이 맞지 않습니다. 설정에서 업데이트 상태를 확인하세요.';
  if(state?.compatible===false)return 'Premiere 편집 연결을 확인하고 있습니다. 잠시 후 연결 상태를 다시 확인하세요.';
  if(localEditPending||state?.applyRecovery?.blocked)return '설정에서 중단 작업 기록을 확인한 뒤 새 편집을 시작하세요.';
  if(applying)return 'Premiere에 편집을 적용하고 있습니다. 완료될 때까지 기다려 주세요.';
  if(validationCount>0)return '원본 파일의 내용을 확인하고 있습니다. 완료되면 다음 작업을 진행할 수 있습니다.';
  if(job)return '요청한 작업을 처리하고 있습니다. 진행 상태를 확인하거나 중단할 수 있습니다.';
  if(pending)return '요청을 처리하고 있습니다. 완료될 때까지 기다려 주세요.';
  if(state?.maintenance?.canRelease)return '설정에서 캐시 정리 종료를 누르면 편집을 계속할 수 있습니다.';
  if(stopped||!state?.gateOpen)return '작업이 중단되어 있습니다. 설정에서 업데이트·복구·캐시 정리 상태를 확인하세요.';
  if(!connected)return 'Premiere에서 편집할 시퀀스를 열고 트랙을 확인하세요.';
  if(rangeIssue)return rangeIssue;
  if(microphoneIssue&&['tracks','speakers'].includes(view.current()))return microphoneIssue;
  if(syncIssue&&view.current()==='tracks')return syncIssue;
  if(syncInvalidated&&view.current()==='tracks')return messages.SYNC_INPUT_CHANGED;
  const step=view.current();
  if(policyIssue&&['cut','review'].includes(step))return policyIssue;
  if(planInvalidated&&['cut','review'].includes(step))return messages.PLAN_INPUT_CHANGED;
  if(step==='speakers')return ['ready','installed'].includes(state?.models?.[mode==='separate'?'silero':'community-1']?.status)?'마이크와 카메라를 확인하고 분석을 시작하세요. 시작 시 모델 무결성을 확인합니다.':missingModelGuidance();
  if(step==='cut')return analysisState?'컷 설정을 확인하고 편집안을 만드세요.':'화자 단계에서 음성을 분석한 뒤 편집안을 만들 수 있습니다.';
  if(step==='review')return plan?'전체 편집안을 검토한 뒤 Premiere에 적용하세요.':'컷 편집 단계에서 편집안을 먼저 만드세요.';
  return step==='settings'?'모델·분석 자원·업데이트 설정을 확인하세요.':'카메라와 마이크를 지정한 뒤 화자 단계로 이동하세요.';
}
function inputLocked(){return !!cancelRequest||!!applyRecoveryRequest||!!updateRecoveryRequest||initializing||initializationIncomplete||binding||pending||applying||!!job||previewBusy>0||validationCount>0||stopped||!credentials||!state?.gateOpen;}
function workLocked(){return inputLocked()||state?.compatible===false||!!updateIntent||localEditPending||!!state?.applyRecovery?.blocked;}
function toggle(){
  if(syncJob&&syncResult&&!syncResultMatches())invalidateSyncResult();
  microphoneIssue=microphoneFeedback();
  rangeIssue=rangeFeedback();
  syncIssue=syncFeedback();
  policyIssue=policyFeedback();if(policyIssue&&plan)clearPolicyPlan();
  if(plan&&!planMatches()){clearPolicyPlan();planInvalidated=true;}
  const busy=pending||applying||!!job||validationCount>0,locked=workLocked();
  for(const el of document.querySelectorAll('input,select,button.mode'))el.disabled=inputLocked();
  for(const value of ['separate','mixed'])$('mode-'+value).disabled=locked;
  for(const row of microphoneRows)for(const field of [row.check,row.speaker,row.channel,row.stream])field.disabled=locked||!connected;
  for(const row of cameraRows)for(const field of [row.role,row.covered])field.disabled=locked||!connected;
  for(const row of speakerRows)row.select.disabled=locked||!connected;
  for(const id of ['start-camera','reserve-camera'])$(id).disabled=locked||!connected;
  for(const id of ['range-start','range-end','min-shot','short-turn','overlap'])$(id).disabled=locked||!connected;
  for(const id of ['analysis-device','cache-budget'])$(id).disabled=locked;
  for(const id of ['model-token','model-terms'])$(id).disabled=locked||!modelReady();
  for(const id of ['sync-method','sync-reference'])$(id).disabled=locked||!connected;
  for(const row of syncRows)for(const field of [row.check,row.stream,row.channel,row.offset,row.confirmed,row.clockId,row.date,row.fps,row.drop,row.clockConfirmed])field.disabled=locked||!connected;
  for(const row of selectedRows)selectedSourceFeedback(row,locked);
  $('speaker-count').disabled=workLocked()||mode!=='mixed';$('vad-threshold').disabled=workLocked()||mode!=='separate';
  for(const row of calibrationRows)for(const field of [row.first,row.last])field.disabled=workLocked()||!calibrationActive(row);
  for(const id of ['analyze','sync','plan','apply-sync','apply','save-settings','load-settings'])$(id).disabled=locked||!connected||(id==='plan'&&!analysisState)||(id==='apply'&&!plan)||(id==='apply-sync'&&!syncResult);
  for(const [id,active,idle,label] of [['save-settings',settingsSave&&!settingsSave.automatic,'저장','저장 중…'],['load-settings',settingsRestore,'불러오기','불러오는 중…']]){$(id).textContent=active?label:idle;$(id).setAttribute('aria-busy',active?'true':'false');}
  for(const id of ['read-project','read-selection','install-model','save-resources','prune-cache'])$(id).disabled=locked;
  const budgetIssue=cacheBudgetFeedback();$('save-resources').disabled=locked||!!budgetIssue;
  $('prune-cache').disabled=locked||resourceInputDirty;
  const saveInfo=resourceInputDirty&&!resourceRequest?.save&&!resourceRequest?.discard?'변경한 분석 자원 설정을 먼저 저장하세요. 캐시 정리는 저장된 예산을 사용합니다.':'';
  if($('resource-save-info').textContent!==saveInfo)$('resource-save-info').textContent=saveInfo;$('resource-save-info').className='hint'+(saveInfo?'':' hidden');
  const savingResources=!!resourceRequest?.save;$('save-resources').textContent=savingResources?(resourceRequest.phase==='checking'?'저장 결과 확인 중…':'자원 설정 저장 중…'):'자원 설정 저장';$('save-resources').setAttribute('aria-busy',savingResources?'true':'false');
  const discardingResources=!!resourceRequest?.discard;$('discard-resources').disabled=locked||!resourceSettingsReady(true)||!resourceInputDirty||!!resourceRequest;$('discard-resources').textContent=discardingResources?'저장된 설정 확인 중…':'저장된 설정으로 되돌리기';$('discard-resources').setAttribute('aria-busy',discardingResources?'true':'false');
  for(const [id,release,idle] of [['prune-cache',false,'완료된 분석 캐시 정리'],['release-cache',true,'정리 종료 후 편집 계속']]){
    const active=!!cacheRequest&&cacheRequest.release===release;$(id).textContent=active?(cacheRequest.phase==='checking'?(release?'편집 상태 확인 중…':'정리 결과 확인 중…'):(release?'정리 종료 요청 중…':'분석 캐시 정리 중…')):idle;$(id).setAttribute('aria-busy',active?'true':'false');
  }
  for(const el of document.querySelectorAll('[data-work]'))el.disabled=locked;
  $('add-override').disabled=locked||!connected;for(const row of overrideRows)row.remove.disabled=locked||!connected;
  for(const row of overrideRows)for(const field of [row.first,row.last,row.camera])field.disabled=locked||!connected;
  $('override-filter').disabled=locked||!overrideRows.some(r=>r.error.textContent);
  $('create-input').disabled=locked||!projectSelection||!inputCapability;$('cancel').disabled=!!cancelRequest||!applyRecoveryRequest&&!updateRecoveryRequest&&!initializing&&!job&&!applying&&!previewPlaying&&!previewBusy&&!validationCount&&(!modelRequest||stopped||!!updateIntent);
  $('cancel').textContent=cancelRequest?'중단 요청 중…':'중단';$('cancel').setAttribute('aria-busy',cancelRequest?'true':'false');
  $('undo-correction').disabled=locked||!analysisState||activeCorrections().length===0;
  $('recover-apply').disabled=!applyRecoveryReady();$('recover-apply').textContent=applyRecoveryRequest?'기록 확인 중…':'중단 작업 확인';$('recover-apply').setAttribute('aria-busy',applyRecoveryRequest?'true':'false');
  $('release-cache').disabled=busy||!cacheReady(true);
  const updateBusy=renderUpdateUI();
  $('progress').className=cancelRequest||applyRecoveryRequest||updateRecoveryRequest||initializing||job||applying||pending||validationCount||updateBusy?'running':'';
  const update=state?.update,available=update?.candidate&&['AVAILABLE','CHECKING'].includes(update.checkState);
  $('update').disabled=!credentials||!!updateIntent?.inFlight||!!updateIntent?.accepted||(!updateIntent&&!available)||!!update&& !['IDLE','COMPLETE','CANCELED','FAILED_BEFORE_REPLACE','ROLLED_BACK'].includes(update.updateState);
  $('update-banner-button').disabled=$('update').disabled;
  $('review-previous').disabled=locked||!plan||!reviewWindow?.previous;
  $('review-next').disabled=locked||!plan||!reviewWindow?.next;
  $('review-clear').disabled=locked||!plan;
  $('check-update').disabled=!credentials||initializing||initializationIncomplete||!!applyRecoveryRequest||!!updateRecoveryRequest||!!updateCheckRequest||!!updateIntent||update?.checkState==='CHECKING';
  $('recover-update').disabled=!updateRecoveryReady();$('recover-update').textContent=updateRecoveryRequest?'복구 확인 중…':'설치 복구';$('recover-update').setAttribute('aria-busy',updateRecoveryRequest?'true':'false');
  if(!['ready','installed'].includes(state?.models?.[mode==='separate'?'silero':'community-1']?.status))$('analyze').disabled=true;
  if(microphoneIssue)$('analyze').disabled=true;
  if(rangeIssue)for(const id of ['analyze','sync','plan','apply-sync','apply'])$(id).disabled=true;
  if(syncIssue)for(const id of ['sync','apply-sync'])$(id).disabled=true;
  if(policyIssue)for(const id of ['plan','apply'])$(id).disabled=true;
  if(!syncResultMatches())$('apply-sync').disabled=true;
  const guidance=actionGuidance();if($('action-readiness').textContent!==guidance)$('action-readiness').textContent=guidance;
}
function cameraValues(){return cameraRows.filter(r=>r.role.value!=='protected').map(r=>[r.id,r.title]);}
function overrideCameraOptions(select,values,previous){options(select,values);if(!values.some(v=>v[0]===previous)){const unavailable=element('option','사용할 수 없는 카메라 · 다시 선택하세요');unavailable.value=previous;select.appendChild(unavailable);}select.value=previous;}
function mappingInputs(){const values=cameraValues();const old=$('start-camera').value,reserve=$('reserve-camera').value;options($('start-camera'),values);options($('reserve-camera'),values,'지정 안 함');if(values.some(v=>v[0]===old))$('start-camera').value=old;if(values.some(v=>v[0]===reserve))$('reserve-camera').value=reserve;for(const row of speakerRows){const previous=row.select.value;options(row.select,values,'카메라 선택');if(values.some(v=>v[0]===previous))row.select.value=previous;}for(const row of overrideRows)overrideCameraOptions(row.camera,values,row.camera.value);invalidatePlan();}
function trackTitle(tag,name,count,audio=false){
  const title=element('div',undefined,'track-title');title.appendChild(element('span',tag,'track-badge'+(audio?' audio':'')));title.appendChild(element('span',name,'source-title'));if(count!==undefined)title.appendChild(element('span',count+' CLIP','track-meta'));return title;
}
function checkbox(checked=false){const e=element('input');e.type='checkbox';e.checked=checked;return e;}
function rangeInput(limit){
  const rawStart=$('range-start').value,rawEnd=$('range-end').value,start=Number(rawStart),end=Number(rawEnd);
  const invalidStart=!rawStart.trim()||!Number.isSafeInteger(start)||start<0||start>=limit;
  const invalidEnd=!rawEnd.trim()||!Number.isSafeInteger(end)||end<1||end>limit||(!invalidStart&&end<=start);
  return {start,end,invalidStart,invalidEnd,text:invalidStart||invalidEnd?'편집 범위의 시작·종료 프레임은 0–'+limit+' 사이의 정수이며 종료가 시작보다 커야 합니다.':''};
}
function rangeFeedback(){
  const input=connected?rangeInput(connected.fullRangeEnd):{invalidStart:false,invalidEnd:false,text:''};
  for(const [id,invalid] of [['range-start',input.invalidStart],['range-end',input.invalidEnd]]){
    const field=$(id),value=invalid?'true':'false';if(field.getAttribute('aria-invalid')!==value)field.setAttribute('aria-invalid',value);
    if(connected)field.setAttribute('max',String(connected.fullRangeEnd));
  }
  const node=$('range-error');if(node.textContent!==input.text)node.textContent=input.text;
  node.className='hint input-error'+(input.text?'':' hidden');return input.text;
}
function analysisOptionsFeedback(){
  let first='';
  for(const [id,active,text,integer,min,max] of [['speaker-count',mode==='mixed','예상 화자 수는 1–26의 정수로 입력하세요.',true,1,26],['vad-threshold',mode==='separate','발화 민감도는 0.05–0.95의 숫자로 입력하세요.',false,.05,.95]]){
    const field=$(id),raw=field.value.trim(),value=Number(raw),invalid=active&&(!raw||!Number.isFinite(value)||value<min||value>max||integer&&!Number.isSafeInteger(value));
    const flag=invalid?'true':'false';if(field.getAttribute('aria-invalid')!==flag)field.setAttribute('aria-invalid',flag);
    const node=$(id+'-error'),message=invalid?text:'';if(node.textContent!==message)node.textContent=message;
    node.className='hint input-error'+(message?'':' hidden');if(message&&!first)first=message;
  }
  return first;
}
function analysisReferenceMatches(inputHash){
  const current=microphoneOptions();if(inputHash===ContentriumHost.hash(current))return true;
  const legacy={...current,calibration:calibrationOptions(true)};if(inputHash===ContentriumHost.hash(legacy))return true;
  const count=Number($('speaker-count').value),threshold=Number($('vad-threshold').value);
  return Number.isFinite(count)&&Number.isFinite(threshold)&&inputHash===ContentriumHost.hash({...legacy,speakerCount:count,vadThreshold:threshold});
}
function analysisOptionChanged(id){
  if(workLocked())return;
  if(id===(mode==='mixed'?'speaker-count':'vad-threshold'))invalidateAnalysis();
  else {scheduleSettings();toggle();}
}
function calibrationActive(row){return mode==='separate'&&row.check.checked;}
function calibrationFeedback(){
  let firstIssue='';
  for(const row of calibrationRows){
    const active=calibrationActive(row),a=row.first.value.trim(),b=row.last.value.trim(),first=Number(a),last=Number(b);
    const unused=a!==''&&b!==''&&first===0&&last===0;
    let text='';
    if(active&&!unused){
      if(!a||!b||!Number.isSafeInteger(first)||!Number.isSafeInteger(last)||first<0||last<=first)text='시작·종료는 정수 프레임이며 종료가 시작보다 커야 합니다. 미사용은 0 / 0으로 두세요.';
      else if(BigInt(first)*BigInt(connected.perFrame)<BigInt(row.clip.startTicks)||BigInt(last)*BigInt(connected.perFrame)>BigInt(row.clip.endTicks))text='단독 발화 구간은 이 마이크 클립 안에 지정하세요.';
    }
    const flag=text?'true':'false';for(const field of [row.first,row.last])if(field.getAttribute('aria-invalid')!==flag)field.setAttribute('aria-invalid',flag);
    const message=text?row.title+' · '+text:'';if(row.issue.textContent!==message)row.issue.textContent=message;row.issue.className='hint input-error'+(message?'':' hidden');
    const hint=active?'0 / 0은 미사용 · 구간은 이 마이크 클립 안의 프레임으로 지정하세요.':mode==='mixed'?'개별 마이크 녹음에서 사용합니다.':'이 마이크를 선택하면 사용할 수 있습니다.';
    if(row.hint.textContent!==hint)row.hint.textContent=hint;if(message&&!firstIssue)firstIssue=message;
  }
  return firstIssue;
}
function calibrationOptions(legacy=false){return calibrationRows.filter(r=>(legacy||calibrationActive(r))&&Number(r.last.value)>Number(r.first.value)).map(r=>({speakerId:r.speaker.value.trim(),inputKey:ContentriumHost.hash({instanceKey:r.instanceKey,streamIndex:Number(r.stream.value)-1,channelIndex:Number(r.channel.value)-1}),startFrame:Number(r.first.value),endFrame:Number(r.last.value)}));}
function calibrationChanged(row){if(workLocked())return;if(calibrationActive(row))invalidateAnalysis();else {scheduleSettings();toggle();}}
function microphoneFeedback(){
  const scalarIssue=analysisOptionsFeedback();
  const calibrationIssue=calibrationFeedback();
  let first='',selected=0;
  for(const row of microphoneRows){
    const active=row.check.checked;selected+=active?1:0;
    const selectionText=active?'':'분석에서 제외됨 · 설정은 저장되며 다시 선택하면 적용됩니다.';
    if(row.selectionHint.textContent!==selectionText)row.selectionHint.textContent=selectionText;
    row.selectionHint.className='hint'+(selectionText?'':' hidden');
    const invalidChannel=active&&(!Number.isSafeInteger(Number(row.channel.value))||Number(row.channel.value)<1||Number(row.channel.value)>64);
    const invalidStream=active&&(!Number.isSafeInteger(Number(row.stream.value))||Number(row.stream.value)<1||Number(row.stream.value)>256);
    const invalidSpeaker=active&&mode==='separate'&&!row.speaker.value.trim();
    for(const [field,invalid] of [[row.channel,invalidChannel],[row.stream,invalidStream],[row.speaker,invalidSpeaker]]){
      const value=invalid?'true':'false';if(field.getAttribute('aria-invalid')!==value)field.setAttribute('aria-invalid',value);
    }
    const errors=[];
    if(invalidChannel)errors.push('채널은 1–64의 정수로 입력하세요.');
    if(invalidStream)errors.push('오디오 스트림은 1–256의 정수로 입력하세요.');
    if(invalidSpeaker)errors.push('화자 ID를 입력하세요.');
    const text=errors.length?row.title+' · '+errors.join(' '):'';
    if(row.issue.textContent!==text)row.issue.textContent=text;
    row.issue.className='hint input-error'+(text?'':' hidden');if(text&&!first)first=text;
  }
  return connected&&!selected?'분석할 마이크를 하나 이상 선택하세요.':first||scalarIssue||calibrationIssue;
}
function renderSources(){
  for(const id of ['microphones','cameras','calibration','speaker-mapping','sync-sources'])$(id).innerHTML='';
  microphoneRows.length=cameraRows.length=calibrationRows.length=speakerRows.length=syncRows.length=0;microphoneSelectionCustomized=false;
  const s=connected.snapshot,assets=new Map(s.sources.map(a=>[a.assetId,a]));
  const audioTracks=s.tracks.filter(t=>t.mediaType==='audio'&&s.clips.some(c=>c.trackRef===t.trackRef));
  for(const clip of s.clips.filter(c=>c.mediaType==='audio')){
    const track=audioTracks.find(t=>t.trackRef===clip.trackRef),order=audioTracks.indexOf(track);
    const row=element('div',undefined,'source-row'),check=checkbox(order<(mode==='mixed'?1:2));
    const title=trackTitle('A'+(track.index+1),track.name,undefined,true);title.appendChild(check);row.appendChild(title);
    row.appendChild(element('p',basename(assets.get(clip.assetId).canonicalPath),'hint'));
    const fields=element('div',undefined,'row'),speaker=element('input'),channel=number(1),stream=number(1);speaker.value=String.fromCharCode(65+order);
    channel.min=stream.min='1';channel.max='64';stream.max='256';
    fields.appendChild(label('화자 ID',speaker));fields.appendChild(label('채널',channel));row.appendChild(fields);row.appendChild(label('오디오 스트림',stream));
    const issue=element('p','','hint input-error hidden');issue.id='microphone-error-'+microphoneRows.length;issue.setAttribute('role','status');issue.setAttribute('aria-live','polite');row.appendChild(issue);
    const selectionHint=element('p','','hint hidden');selectionHint.id='microphone-selection-'+microphoneRows.length;selectionHint.setAttribute('role','status');selectionHint.setAttribute('aria-live','polite');row.appendChild(selectionHint);
    for(const field of [speaker,channel,stream])field.setAttribute('aria-describedby',issue.id+' '+selectionHint.id);check.setAttribute('aria-describedby',selectionHint.id);
    $('microphones').appendChild(row);microphoneRows.push({clip,check,speaker,channel,stream,issue,selectionHint,defaultChecked:{separate:order<2,mixed:order<1},title:'A'+(track.index+1)+' · '+track.name});
    for(const field of [check,speaker,channel,stream]){field.disabled=workLocked()||!connected;field.onchange=()=>{if(workLocked()||!connected)return;if(field!==check&&!check.checked){scheduleSettings();toggle();return;}if(field===check)microphoneSelectionCustomized=true;invalidateAnalysis();renderSpeakers(mode==='mixed'?[]:[...new Set(microphoneRows.filter(r=>r.check.checked).map(r=>r.speaker.value.trim()))]);};}
    for(const field of [speaker,channel,stream])field.oninput=field.onchange;
    const calibration=element('div',undefined,'source-row'),bounds=element('div',undefined,'row'),first=number(0),last=number(0);
    calibration.appendChild(element('div','A'+(track.index+1)+' · '+basename(assets.get(clip.assetId).canonicalPath),'source-title'));
    bounds.appendChild(label('단독 발화 시작 · 프레임',first));bounds.appendChild(label('종료 · 프레임',last));calibration.appendChild(bounds);
    const calibrationHint=element('p','','hint'),calibrationIssue=element('p','','hint input-error hidden');calibrationIssue.id='calibration-error-'+calibrationRows.length;calibrationIssue.setAttribute('aria-live','polite');calibration.appendChild(calibrationHint);calibration.appendChild(calibrationIssue);$('calibration').appendChild(calibration);
    const calibrationRow={instanceKey:clip.instanceKey,clip,check,speaker,first,last,stream,channel,hint:calibrationHint,issue:calibrationIssue,title:'A'+(track.index+1)+' · '+track.name};calibrationRows.push(calibrationRow);
    for(const field of [first,last]){field.min='0';field.step='1';field.setAttribute('aria-describedby',calibrationIssue.id);field.disabled=workLocked()||!calibrationActive(calibrationRow);field.oninput=field.onchange=()=>calibrationChanged(calibrationRow);}
  }
  for(const track of s.tracks.filter(t=>t.mediaType==='video'&&s.clips.some(c=>c.trackRef===t.trackRef))){
    const row=element('div',undefined,'camera-row'),clips=s.clips.filter(c=>c.trackRef===track.trackRef),title='V'+(track.index+1)+' · '+track.name;
    row.appendChild(trackTitle('V'+(track.index+1),track.name,clips.length));
    const fields=element('div',undefined,'row'),role=element('select'),covered=element('input');
    options(role,[['speaker','화자 카메라'],['wide','전체샷'],['two-shot','투샷'],['reserve','예비'],['protected','보호 트랙']]);covered.placeholder='A, B';role.value=track.muted?'protected':'speaker';
    fields.appendChild(label('트랙 역할',role));fields.appendChild(label('보이는 화자',covered));row.appendChild(fields);$('cameras').appendChild(row);
    cameraRows.push({id:track.trackRef,track,title,role,covered});for(const field of [role,covered])field.disabled=workLocked()||!connected;
    role.onchange=()=>{if(!workLocked()&&connected)mappingInputs();};covered.oninput=covered.onchange=()=>{if(!workLocked()&&connected)invalidatePlan();};
  }
  renderSyncSources();renderSpeakers(mode==='mixed'?[]:[...new Set(microphoneRows.filter(r=>r.check.checked).map(r=>r.speaker.value))]);
  if(mode==='mixed')$('speaker-mapping').appendChild(element('p','화자를 분석하면 감지한 목소리를 카메라에 연결할 수 있습니다.','hint'));mappingInputs();
}
function renderSyncSources(){
  const values=[];
  for(const source of connected.snapshot.sources){
    const row=element('div',undefined,'source-row'),check=checkbox(true),title=element('label',basename(source.canonicalPath));title.insertBefore(check,title.firstChild);row.appendChild(title);
    const fields=element('div',undefined,'row'),stream=number(1),channel=number(1);stream.min=channel.min='1';stream.step=channel.step='1';fields.appendChild(label('오디오 스트림',stream));fields.appendChild(label('채널',channel));row.appendChild(fields);
    const issue=element('p','', 'hint input-error hidden');issue.id='sync-source-error-'+syncRows.length;issue.setAttribute('role','status');issue.setAttribute('aria-live','polite');
    for(const field of [stream,channel])field.setAttribute('aria-describedby',issue.id);row.appendChild(issue);
    const manual=element('div'),offset=number(0),confirmed=checkbox();offset.step='0.001';manual.appendChild(label('기준 대비 오프셋 · 초',offset));manual.appendChild(label('이 오프셋을 확인했습니다',confirmed));row.appendChild(manual);
    const clock=element('div'),clockId=element('input'),date=element('input'),fps=element('select'),drop=checkbox(),clockConfirmed=checkbox();date.placeholder='YYYY-MM-DD';clockId.placeholder='같은 동기 장치 또는 시계 이름';
    options(fps,[['24000/1001','23.976'],['24/1','24'],['25/1','25'],['30000/1001','29.97'],['30/1','30'],['50/1','50'],['60000/1001','59.94'],['60/1','60']]);fps.value=connected.snapshot.fps.num+'/'+connected.snapshot.fps.den;
    clock.appendChild(label('공통 시계',clockId));clock.appendChild(label('촬영 날짜',date));clock.appendChild(label('타임코드 FPS',fps));clock.appendChild(label('Drop-frame',drop));clock.appendChild(label('날짜와 시계가 같고 촬영 중 리셋하지 않았습니다',clockConfirmed));row.appendChild(clock);
    $('sync-sources').appendChild(row);syncRows.push({check,source,stream,channel,issue,manual,offset,confirmed,clock,clockId,date,fps,drop,clockConfirmed});
    for(const field of [check,stream,channel,offset,confirmed,clockId,date,fps,drop,clockConfirmed]){field.disabled=workLocked()||!connected;field.oninput=field.onchange=syncInputEdited;}
    values.push([source.assetId,basename(source.canonicalPath)]);
  }
  options($('sync-reference'),values);syncMethodChanged();
}
function clearSyncResult(){syncResult=syncJob=syncResultInputHash=null;syncInvalidated=false;$('sync-result').textContent='';}
function syncInputsChanged(){clearSyncResult();scheduleSettings();toggle();}
function syncMethodChanged(){for(const r of syncRows){r.manual.className=$('sync-method').value==='manual'?'':'hidden';r.clock.className=$('sync-method').value==='timecode'?'':'hidden';}syncInputsChanged();}
function syncInputEdited(){if(!workLocked()&&connected)syncInputsChanged();}
function syncMethodEdited(){if(!workLocked()&&connected)syncMethodChanged();}
function syncFeedback(){
  let first='';const selected=syncRows.filter(r=>r.check.checked);
  for(const row of syncRows){
    const invalidStream=row.check.checked&&(!row.stream.value.trim()||!Number.isSafeInteger(Number(row.stream.value))||Number(row.stream.value)<1);
    const invalidChannel=row.check.checked&&(!row.channel.value.trim()||!Number.isSafeInteger(Number(row.channel.value))||Number(row.channel.value)<1);
    for(const [field,invalid] of [[row.stream,invalidStream],[row.channel,invalidChannel]]){const value=invalid?'true':'false';if(field.getAttribute('aria-invalid')!==value)field.setAttribute('aria-invalid',value);}
    const errors=[];if(invalidStream)errors.push('오디오 스트림은 1 이상의 정수로 입력하세요.');if(invalidChannel)errors.push('채널은 1 이상의 정수로 입력하세요.');
    const text=errors.length?basename(row.source.canonicalPath)+' · '+errors.join(' '):'';
    if(row.issue.textContent!==text)row.issue.textContent=text;row.issue.className='hint input-error'+(text?'':' hidden');if(text&&!first)first=text;
  }
  const issue=!connected?'':first|| (selected.length<2?'싱크할 소스를 2개 이상 선택하세요.':!selected.some(r=>r.source.assetId===$('sync-reference').value)?'기준 소스를 선택한 싱크 소스 중에서 지정하세요.':'');
  const node=$('sync-error');if(node.textContent!==issue)node.textContent=issue;node.className='hint input-error'+(issue?'':' hidden');return issue;
}
function syncOptions(){
  const rows=syncRows.filter(r=>r.check.checked),method=$('sync-method').value;
  return {assetIds:rows.map(r=>r.source.assetId),reference:$('sync-reference').value,method,
    sourceSelections:rows.map(r=>({assetId:r.source.assetId,streamIndex:Number(r.stream.value)-1,channelIndex:Number(r.channel.value)-1})),
    manualOffsets:Object.fromEntries(rows.map(r=>[r.source.assetId,{offsetSeconds:r.offset.value,confirmed:r.confirmed.checked}])),
    timecodeConfirmations:Object.fromEntries(rows.map(r=>{const parts=r.fps.value.split('/').map(Number);return [r.source.assetId,{confirmed:r.clockConfirmed.checked,clockId:r.clockId.value.trim(),date:r.date.value.trim(),fps:{num:parts[0],den:parts[1]},dropFrame:r.drop.checked,reset:false}];}))};
}
function syncInputHash(options=syncOptions()){
  const effective={assetIds:options.assetIds,reference:options.reference,method:options.method,sourceSelections:options.sourceSelections};
  if(options.method==='manual')effective.manualOffsets=Object.fromEntries(options.assetIds.filter(id=>id!==options.reference).map(id=>[id,options.manualOffsets[id]]));
  if(options.method==='timecode')effective.timecodeConfirmations=options.timecodeConfirmations;
  return ContentriumHost.hash(effective);
}
function syncResultMatches(){return !!syncJob&&!!syncResult&&typeof syncResultInputHash==='string'&&syncResultInputHash===syncInputHash();}
function invalidateSyncResult(){clearSyncResult();syncInvalidated=true;say(messages.SYNC_INPUT_CHANGED);scheduleSettings();}
function rejectStaleSync(){invalidateSyncResult();throw Object.assign(new Error(messages.SYNC_INPUT_CHANGED),{code:'SYNC_INPUT_CHANGED'});}
function workButton(text,fn){const button=element('button',text,'full');button.setAttribute('data-work','true');button.onclick=()=>runAction(fn);return button;}
async function runAction(fn){if(cancelRequest||applyRecoveryRequest||updateRecoveryRequest||initializing||initializationIncomplete||pending||applying||job)return;pending=true;toggle();try{await fn();}catch(e){error(e);}finally{pending=false;toggle();}}
function activeCorrections(){const active=[];for(const item of analysisState?.history||[]){if(item.operation.type==='undo')active.pop();else active.push(item);}return active;}
function addExamples(parent,id,candidate=false){
  const examples=(analysisState?.examples||[]).filter(e=>(candidate?e.candidateSpeakerId:e.speakerId)===id);
  if(!examples.length){parent.appendChild(element('p','확실한 단독 발화 샘플이 없습니다. 타임라인에서 확인하세요.','hint'));return;}
  const row=element('div',undefined,'sample-buttons');
  examples.forEach((example,i)=>row.appendChild(workButton('▶ '+(i+1)+' · '+frameLabel(example.startFrame),()=>listenExample(example))));
  parent.appendChild(row);
}
function currentSpeakerMappingScope(){
  if(!connected)return null;
  if(mode==='mixed')return analysisState?{mode,analysisId:analysisState.analysisId,snapshotHash:analysisState.snapshotHash}:null;
  return {mode,sourceScope:ContentriumHost.hash({projectRef:connected.snapshot.projectRef,sequenceRef:connected.snapshot.sequenceRef,microphones:microphoneRows.filter(r=>r.check.checked).map(r=>({instanceKey:r.clip.instanceKey,assetId:r.clip.assetId,speakerId:r.speaker.value.trim(),stream:r.stream?.value??'1',channel:r.channel.value}))})};
}
function sameSpeakerMappingScope(a,b){return !!a&&!!b&&ContentriumHost.hash(a)===ContentriumHost.hash(b);}
function clearMixedMappings(){
  savedSpeakerMappings={};savedSpeakerMappingScope=speakerRowsScope=null;
  for(const r of cameraRows)r.covered.value='';
  for(const r of speakerRows)r.select.value='';
}
function renderSpeakers(ids){
  const scope=currentSpeakerMappingScope(),previous={...(sameSpeakerMappingScope(savedSpeakerMappingScope,scope)?savedSpeakerMappings:{}),...(sameSpeakerMappingScope(speakerRowsScope,scope)?Object.fromEntries(speakerRows.map(r=>[r.id,r.select.value])):{})};
  $('speaker-mapping').innerHTML='';speakerRows.length=0;
  speakerRowsScope=scope;
  for(const id of [...new Set(ids)].filter(Boolean)){
    const row=element('div',undefined,'source-row'),select=element('select'),values=cameraValues();
    row.appendChild(element('div','화자 '+id,'source-title'));options(select,values,'카메라 선택');
    // Separate microphones have explicit IDs; mixed identities need deliberate
    // camera selection after listening, never an order-based guess.
    if(values.some(v=>v[0]===previous[id]))select.value=previous[id];
    else if(mode==='separate'&&values[speakerRows.length])select.value=values[speakerRows.length][0];
    if(analysisState){
      const name=element('input');name.value=analysisState.names[id]||id;name.maxLength=100;
      row.appendChild(label('화자 이름',name));row.appendChild(workButton('이름 저장',()=>correct({type:'name',speakerId:id,name:name.value.trim()})));
      addExamples(row,id);
    }
    row.appendChild(label('연결 카메라',select));$('speaker-mapping').appendChild(row);speakerRows.push({id,select});
    select.disabled=workLocked()||!connected;select.onchange=()=>{if(workLocked()||!connected)return;if(!sameSpeakerMappingScope(savedSpeakerMappingScope,scope))savedSpeakerMappings={};savedSpeakerMappingScope=scope;savedSpeakerMappings[id]=select.value;invalidatePlan();};
  }
  if(!ids.length)$('speaker-mapping').appendChild(element('p',analysisState?'아래에서 감지한 목소리를 화자에 연결하세요.':'분석할 마이크를 지정하면 화자를 연결할 수 있습니다.','hint'));
  renderCorrections();
}
function commaValues(value){return [...new Set(value.split(',').map(v=>v.trim()).filter(Boolean))];}
function renderCorrections(){
  $('speaker-corrections').innerHTML='';$('correction-history').innerHTML='';
  if(!analysisState){$('correction-history').appendChild(element('p','분석 후 이름·화자·발화 구간을 교정할 수 있습니다.','hint'));return;}
  const ids=analysis.sessionSpeakerIds;
  for(const candidate of analysis.unresolvedSpeakerIds||[]){
    const row=element('div',undefined,'source-row'),target=element('input');target.placeholder='기존 화자 ID 또는 새 ID';
    row.appendChild(element('b',candidate+' · 화자 확인 필요'));addExamples(row,candidate,true);row.appendChild(label('연결할 화자',target));
    row.appendChild(workButton('이 화자로 연결',()=>correct({type:'link',candidateSpeakerId:candidate,sessionSpeakerId:target.value.trim()})));$('speaker-corrections').appendChild(row);
  }
  if(ids.length>1){
    const row=element('div',undefined,'source-row'),sources=element('input'),target=element('select');sources.placeholder=ids.slice(0,2).join(', ');options(target,ids.map(id=>[id,analysisState.names[id]||id]));
    row.appendChild(element('b','같은 사람으로 합치기'));row.appendChild(label('합칠 화자 ID · 쉼표로 구분',sources));row.appendChild(label('유지할 화자',target));
    row.appendChild(workButton('화자 합치기',()=>correct({type:'merge',speakerIds:commaValues(sources.value),targetSpeakerId:target.value})));$('speaker-corrections').appendChild(row);
  }
  const row=element('div',undefined,'source-row'),bounds=element('div',undefined,'row'),first=number(connected.snapshot.range.startFrame),last=number(connected.snapshot.range.endFrame),speakers=element('input'),newIds=element('input'),unknown=checkbox();
  row.appendChild(element('b','구간별 화자 교정'));bounds.appendChild(label('시작 프레임',first));bounds.appendChild(label('종료 프레임',last));row.appendChild(bounds);
  speakers.placeholder='A 또는 A, B';newIds.placeholder='새 사람을 추가할 때만 입력';row.appendChild(label('말하는 화자 ID',speakers));row.appendChild(label('위 화자 중 새로 추가할 ID',newIds));row.appendChild(label('불확실한 구간으로 남기기',unknown));
  row.appendChild(workButton('이 구간 교정',()=>correct({type:'reassign',startFrame:Number(first.value),endFrame:Number(last.value),speakers:commaValues(speakers.value),newSpeakerIds:commaValues(newIds.value),unknown:unknown.checked})));
  $('speaker-corrections').appendChild(row);
  const titles={name:'이름 변경',merge:'화자 합침',reassign:'발화 구간 교정',link:'목소리 연결',undo:'교정 되돌림'};
  for(const item of analysisState.history.slice().reverse())$('correction-history').appendChild(element('p',item.revision+' · '+(titles[item.operation.type]||item.operation.type),'hint'));
  if(!analysisState.history.length)$('correction-history').appendChild(element('p','아직 교정 이력이 없습니다.','hint'));
}
function acceptAnalysis(next){
  if(!connected||next.snapshotHash!==connected.snapshot.snapshotHash)throw new Error('ANALYSIS_SCOPE');
  if(mode==='mixed'&&!sameSpeakerMappingScope(currentSpeakerMappingScope(),{mode,analysisId:next.analysisId,snapshotHash:next.snapshotHash}))clearMixedMappings();
  analysisState=next;analysis=next.analysis;clearPolicyPlan();renderSpeakers(analysis.sessionSpeakerIds);toggle();scheduleSettings();
}
async function correct(operation){
  await requireCurrent();if(!analysisState)throw new Error('화자 분석을 먼저 실행하세요.');
  const token={},scopeCurrent=editingResponseGuard(),id=analysisState.analysisId,revision=analysisState.revision,epoch=state.epoch;
  const current=()=>correctionRequest===token&&scopeCurrent();correctionRequest=token;
  try{
    if(!current())throw currentCheckDiscarded;
    let next;
    try{next=await api('/analyses/'+id+'/correct',{expectedRevision:revision,operation,requestId:requestId(),epoch});}
    catch(e){
      if(!current())throw currentCheckDiscarded;
      if(e.code==='CORRECTION_REVISION_CONFLICT'){
        let latest;try{latest=await api('/analyses/'+id);}catch(recoveryError){if(!current())throw currentCheckDiscarded;throw recoveryError;}
        if(!current())throw currentCheckDiscarded;
        acceptAnalysis(latest);
      }
      throw e;
    }
    if(!current())throw currentCheckDiscarded;
    acceptAnalysis(next);scheduleSettings();say('화자 교정을 저장했습니다. 편집안을 다시 만들어 주세요.');
  }finally{if(correctionRequest===token)correctionRequest=null;}
}
async function stopPreview(current=()=>true){
  if(!current())throw currentCheckDiscarded;
  if(previewPlaying){
    const generation=previewGeneration;previewBusy++;toggle();
    try{
      let result;try{result=await ContentriumHost.ppro.SourceMonitor.play(0);}catch(e){if(!current()||generation!==previewGeneration)throw currentCheckDiscarded;throw e;}
      if(!current()||generation!==previewGeneration)throw currentCheckDiscarded;
      if(!result)throw new Error('음성 미리보기를 멈추지 못했습니다.');
      previewPlaying=false;previewGeneration++;toggle();
    }finally{previewBusy--;toggle();}
  }
}

async function afterEditingPreview(next,nativeToken=null){
  const scope=projectScope(),read=projectRead,selection=projectSelection,capability=inputCapability,rows=selectedRows.slice(),reviewed=plan,reviewedHash=planInputHash,sync=syncResult,syncId=syncJob,syncHash=syncResultInputHash;
  const current=()=>!binding&&!batchRunning&&projectRead===read&&projectScopeCurrent(scope,!!nativeToken)&&(!nativeToken||applying&&nativePreparation===nativeToken)&&projectSelection===selection&&inputCapability===capability&&selectedRows.length===rows.length&&rows.every((row,index)=>selectedRows[index]===row)&&plan===reviewed&&planInputHash===reviewedHash&&syncResult===sync&&syncJob===syncId&&syncResultInputHash===syncHash;
  if(!current())throw currentCheckDiscarded;
  await stopPreview(current);if(!current())throw currentCheckDiscarded;
  return next();
}
function editingResponseGuard(allowBinding=false){
  const scope=projectScope(),read=projectRead,selection=projectSelection,capability=inputCapability,rows=selectedRows.slice(),reviewed=plan,reviewedHash=planInputHash,sync=syncResult,syncId=syncJob,syncHash=syncResultInputHash;
  return ()=>(allowBinding||!binding)&&!batchRunning&&projectRead===read&&projectScopeCurrent(scope)&&projectSelection===selection&&inputCapability===capability&&selectedRows.length===rows.length&&rows.every((row,index)=>selectedRows[index]===row)&&plan===reviewed&&planInputHash===reviewedHash&&syncResult===sync&&syncJob===syncId&&syncResultInputHash===syncHash;
}
async function submitEditingJob(path,body,identity,message,inputsCurrent=()=>true){
  const token={},scopeCurrent=editingResponseGuard();
  const current=()=>editingSubmission===token&&scopeCurrent()&&inputsCurrent();
  editingSubmission=token;
  try{
    if(!current())throw currentCheckDiscarded;
    let next;try{next=await api(path,body);}catch(e){if(!current())throw currentCheckDiscarded;throw e;}
    if(!current())throw currentCheckDiscarded;
    job={...next,...identity,...(['sync','example'].includes(next.kind)?{epoch:body.epoch}:{})};say(message);toggle();
  }finally{if(editingSubmission===token)editingSubmission=null;}
}
async function listenExample(example){
  await requireCurrent();return afterEditingPreview(async()=>{
  if(!analysisState)throw new Error('화자 분석을 먼저 실행하세요.');
  const identity={analysisId:analysisState.analysisId,revision:analysisState.revision,snapshotHash:connected.snapshot.snapshotHash};
  return submitEditingJob('/analyses/'+identity.analysisId+'/example',{exampleId:example.exampleId,expectedRevision:identity.revision,epoch:state.epoch},identity,'단독 발화 샘플을 준비하고 있습니다.');
  });
}
function microphoneOptions(){return {mode,microphones:microphoneRows.filter(r=>r.check.checked).map(r=>({instanceKey:r.clip.instanceKey,speakerId:r.speaker.value.trim(),streamIndex:Number(r.stream.value)-1,channelIndex:Number(r.channel.value)-1})),speakerCount:mode==='mixed'?Number($('speaker-count').value):2,vadThreshold:mode==='separate'?Number($('vad-threshold').value):.5,calibration:calibrationOptions()};}
function mapping(){const s=connected.snapshot;return {speakers:Object.fromEntries(speakerRows.map(r=>[r.id,r.select.value])),cameras:cameraRows.filter(r=>r.role.value!=='protected').map(r=>({cameraId:r.id,role:r.role.value,coveredSpeakers:r.covered.value.split(',').map(v=>v.trim()).filter(Boolean),clips:s.clips.filter(c=>c.trackRef===r.id).map(c=>({instanceKey:c.instanceKey,startFrame:Math.round(Number(c.startTicks)/connected.perFrame),endFrame:Math.round(Number(c.endTicks)/connected.perFrame)}))})),startCameraId:$('start-camera').value,fallbackOrder:$('reserve-camera').value?[$('reserve-camera').value]:[]};}
function policyFeedback(){
  let first='';
  for(const [id,title] of [['min-shot','최소 샷 길이'],['short-turn','짧은 발화'],['overlap','동시 발화']]){
    const input=$(id),raw=input.value.trim(),value=Number(raw);
    const text=connected&&(!raw||!Number.isFinite(value)||value<0)?title+'에 0 이상의 유효한 초를 입력하세요.':'';
    input.setAttribute('aria-invalid',text?'true':'false');input.setAttribute('aria-describedby',id+'-error');
    const hint=$(id+'-error');if(hint.textContent!==text)hint.textContent=text;hint.className='hint input-error'+(text?'':' hidden');if(text&&!first)first=text;
  }
  const manual=overrideFeedback();return first||manual;
}
function requirePolicy(){const issue=policyFeedback();if(issue){clearPolicyPlan();throw new Error(issue);}}
function overrideFeedback(){
  const bounds=connected?.snapshot.range,available=new Set(cameraValues().map(v=>v[0]));
  const checks=overrideRows.map((row,index)=>{
    const first=Number(row.first.value),last=Number(row.last.value),invalidFirst=!!bounds&&(!row.first.value.trim()||!Number.isSafeInteger(first)||first<bounds.startFrame||first>=bounds.endFrame),
      invalidLast=!!bounds&&(!row.last.value.trim()||!Number.isSafeInteger(last)||last<=bounds.startFrame||last>bounds.endFrame||!invalidFirst&&last<=first),invalidCamera=!!bounds&&!available.has(row.camera.value);
    const message=invalidFirst?'시작 프레임':invalidLast?'종료 프레임':invalidCamera?'카메라':'';
    return {row,index,first,last,invalidFirst,invalidLast,invalidCamera,text:message?'수동 구간 '+(index+1)+' · '+message+(message==='카메라'?'를 다시 선택하세요.':'은 '+bounds.startFrame+'–'+bounds.endFrame+' 사이의 정수이며 종료가 시작보다 커야 합니다.'):''};
  });
  const valid=checks.filter(c=>!c.text).sort((a,b)=>a.first-b.first||a.last-b.last);let longest=null;
  if(bounds)for(const current of valid){
    if(longest&&current.first<longest.last&&current.row.camera.value!==longest.row.camera.value){
      const text='수동 구간 '+(longest.index+1)+'와 '+(current.index+1)+'에 서로 다른 카메라가 겹칩니다. 구간 또는 카메라를 수정하세요.';
      longest.text=current.text=text;longest.invalidCamera=current.invalidCamera=true;break;
    }
    if(!longest||current.last>longest.last)longest=current;
  }
  const count=checks.filter(c=>c.text).length;if(!count){overrideErrorsOnly=false;overrideVisibleRows.clear();}
  let first='';for(const c of checks){
    const title='수동 구간 '+(c.index+1);if(c.row.title.textContent!==title)c.row.title.textContent=title;
    if(c.row.error.textContent!==c.text)c.row.error.textContent=c.text;c.row.error.className='hint input-error'+(c.text?'':' hidden');
    for(const [field,invalid] of [[c.row.first,c.invalidFirst],[c.row.last,c.invalidLast],[c.row.camera,c.invalidCamera]])field.setAttribute('aria-invalid',invalid?'true':'false');
    if(overrideErrorsOnly&&c.text)overrideVisibleRows.add(c.row);
    c.row.row.className='override-row'+(overrideErrorsOnly&&!overrideVisibleRows.has(c.row)?' hidden':'');
    if(c.text&&!first)first=c.text;
  }
  const summary=checks.length?'수동 구간 '+checks.length+'개'+(count?' · 확인 필요 '+count+'개':' · 입력 형식 확인 완료')+(overrideErrorsOnly?' · 오류 확인 중':''):'';
  if($('override-summary').textContent!==summary)$('override-summary').textContent=summary;
  $('override-summary').className='hint'+(count?' input-error':'')+(checks.length?'':' hidden');
  const caption=overrideErrorsOnly?'전체 '+checks.length+'개 보기':'오류 구간만 보기';
  if($('override-filter').textContent!==caption)$('override-filter').textContent=caption;
  $('override-filter').className='full'+(count?'':' hidden');
  return first;
}
function policy(){return {minShot:Number($('min-shot').value),shortTurn:Number($('short-turn').value),suppressShort:true,overlap:Number($('overlap').value),overrides:overrideRows.map(r=>({startFrame:Number(r.first.value),endFrame:Number(r.last.value),cameraId:r.camera.value}))};}
function planInputs(){return {snapshotHash:connected.snapshot.snapshotHash,analysisId:analysisState.analysisId,analysisRevision:analysisState.revision,jobId:analysisJob,mapping:mapping(),policy:policy(),epoch:state.epoch};}
function planInputsHash(){try{return connected&&analysisState&&analysisJob?ContentriumHost.hash(planInputs()):null;}catch(_){return null;}}
function planMatches(){return !!planInputHash&&planInputHash===planInputsHash();}
function rejectStalePlan(){clearPolicyPlan();planInvalidated=true;throw Object.assign(new Error(messages.PLAN_INPUT_CHANGED),{code:'PLAN_INPUT_CHANGED'});}
function requireReviewedPlan(reviewed,inputHash){if(plan!==reviewed||planInputHash!==inputHash||!planMatches())rejectStalePlan();}
function projectScope(){return {connection:connected,snapshotHash:connected?.snapshot.snapshotHash,hostSnapshotHash:connected?.snapshot.hostSnapshotHash,mode,analysisState,revision:analysisState?.revision,epoch:state?.epoch,credentials};}
function projectScopeCurrent(scope,allowApplying=false){return !!credentials&&credentials===scope.credentials&&!!state?.gateOpen&&!stopped&&!updateIntent&&state?.compatible!==false&&!panelContextConflict&&(!applying||allowApplying)&&!job&&!validationCount&&!localEditPending&&!state?.applyRecovery?.blocked&&(state?.stopEpoch===null||state?.stopEpoch===undefined)&&state?.epoch===scope.epoch&&connected===scope.connection&&connected?.snapshot.snapshotHash===scope.snapshotHash&&connected?.snapshot.hostSnapshotHash===scope.hostSnapshotHash&&mode===scope.mode&&analysisState===scope.analysisState&&analysisState?.revision===scope.revision;}
async function readProject({fresh=null,automatic=false,guardFactory=null,onSettledRead=null,guidanceCurrent=()=>true}={}){
  if(applying||job)throw new Error('현재 작업을 마친 뒤 시퀀스를 변경하세요.');
  const scope=projectScope();if(!projectScopeCurrent(scope))return false;
  const token={};projectRead=token;binding=true;toggle();
  const settingsQueued=!!settingsTimer,prior=connected,settings=prior?captureSettings():null;let preserveSettings=false,committed=false,receipt=null,failed=false;
  const responseCurrent=guardFactory?guardFactory():()=>true;
  const current=()=>projectRead===token&&projectScopeCurrent(scope)&&responseCurrent();
  try{
    const next=fresh||await ContentriumHost.snapshot();if(!current())return false;const s=next.snapshot;
    next.fullRangeEnd=s.range.endFrame;
    const same=prior&&prior.snapshot.projectRef===s.projectRef&&prior.snapshot.sequenceRef===s.sequenceRef;
    s.hostSnapshotHash=s.snapshotHash;
    const input=same?rangeInput(s.range.endFrame):{start:0,end:s.range.endFrame,text:''};
    let {start,end}=input;
    if(input.text){
      if(!automatic)throw Object.assign(new Error(input.text),{code:'RANGE_INPUT_INVALID'});
      const priorRange=prior.snapshot.range;
      start=priorRange.startFrame;end=Math.min(priorRange.endFrame,s.range.endFrame);
      if(start>=end){start=0;end=s.range.endFrame;}
    }
    s.range={startFrame:start,endFrame:end};delete s.snapshotHash;s.snapshotHash=ContentriumHost.hash(s);
    if(!await heartbeat(current)||!current())return false;
    await api('/project',{snapshot:s,hostIdentity:null,epoch:scope.epoch});if(!current())return false;
    let saved;
    if(!same){
      try{const raw=await uxp.storage.secureStorage.getItem('cut-settings-'+ContentriumHost.hash({projectRef:s.projectRef,sequenceRef:s.sequenceRef}));if(!current())return false;saved=JSON.parse(typeof raw==='string'?raw:new TextDecoder().decode(raw));}catch(e){if(!current())return false;}
    }
    if(!current())return false;
    if(settingsTimer){clearTimeout(settingsTimer);settingsTimer=null;}
    connected=next;rangeDirty=false;committed=true;
    $('project-name').textContent=s.sequenceName;$('project-name').title=s.projectName+' / '+s.sequenceName;
    $('sequence-info').textContent=frameLabel(s.range.endFrame)+' · '+(s.fps.num/s.fps.den).toFixed(3)+' fps ('+s.fps.num+'/'+s.fps.den+')';
    $('track-count').textContent=s.tracks.filter(t=>t.mediaType==='video').length+' V / '+s.tracks.filter(t=>t.mediaType==='audio').length+' A';
    $('range-start').value=String(start);$('range-end').value=String(end);
    clearAnalysis();clearSyncResult();savedSpeakerMappings={};overrideRows.length=0;overrideErrorsOnly=false;overrideVisibleRows.clear();$('overrides').innerHTML='';
    renderSources();if(same&&settings)restoreSettings(settings);else if(saved){try{restoreSettings(saved);}catch(_){/* An invalid optional saved preset must not prevent connecting. */}}
    toggle();if(guidanceCurrent())say('트랙을 확인하고 분석할 마이크와 카메라를 지정하세요.');receipt=projectScope();return receipt;
  }catch(e){if(!committed&&!current())return false;failed=true;if(e.code==='RANGE_INPUT_INVALID')preserveSettings=true;else resetSequence();throw e;}
  finally{if(projectRead===token){projectRead=null;binding=false;toggle();if(preserveSettings||committed&&settingsQueued&&connected&&prior&&connected.snapshot.projectRef===prior.snapshot.projectRef&&connected.snapshot.sequenceRef===prior.snapshot.sequenceRef)scheduleSettings();if((receipt||failed)&&onSettledRead)onSettledRead(receipt);}}
}
function frameLabel(frame){const rate=connected?connected.snapshot.fps.num/connected.snapshot.fps.den:30,seconds=Math.max(0,Math.floor(frame/rate));return [Math.floor(seconds/3600),Math.floor(seconds/60)%60,seconds%60].map(n=>String(n).padStart(2,'0')).join(':');}
function segmentTiming(segment){const fps=connected?.snapshot.fps||{num:30,den:1},frames=segment.endFrame-segment.startFrame;return segment.startFrame+'–'+segment.endFrame+' 프레임 · '+frames+'프레임 / '+(frames*fps.den/fps.num).toFixed(3)+'초';}
function clearAnalysis(){planInputHash=null;planInvalidated=false;reviewPage=0;reviewWindow=null;$('review-search').value='';options($('review-camera'),[],'모든 카메라');$('review-page-info').textContent='편집안을 만들어 주세요';if(mode==='mixed'||speakerRowsScope?.mode==='mixed'||savedSpeakerMappingScope?.mode==='mixed')clearMixedMappings();analysisJob=analysis=plan=null;analysisState=null;if(mode==='mixed'){$('speaker-mapping').innerHTML='';speakerRows.length=0;}$('cut-count').textContent=$('review-count').textContent='—';for(const id of ['timeline','segments','reviews','speaker-corrections','correction-history'])$(id).innerHTML='';$('segments').appendChild(element('p','화자를 분석하고 편집안을 만들어 주세요.','hint'));}
function resetSequence(){connected=null;clearAnalysis();clearSyncResult();$('project-name').textContent='시퀀스를 열어 주세요';$('sequence-info').textContent='Premiere 타임라인을 자동으로 읽습니다.';$('track-count').textContent='TIMELINE';for(const id of ['microphones','cameras','calibration','speaker-mapping','sync-sources'])$(id).innerHTML='';microphoneRows.length=cameraRows.length=calibrationRows.length=speakerRows.length=syncRows.length=0;toggle();}
async function followSequence(){
  if(binding)return;const scope=projectScope();if(!projectScopeCurrent(scope))return;let reading=false;
  try{
    const fresh=await ContentriumHost.snapshot();if(!projectScopeCurrent(scope)||binding)return;
    if(!connected||fresh.snapshot.snapshotHash!==connected.snapshot.hostSnapshotHash){reading=true;await readProject({fresh,automatic:true});}
  }catch(e){
    if(!reading&&(!projectScopeCurrent(scope)||binding))return;
    if(/PROJECT_REQUIRED|SEQUENCE_REQUIRED/.test(String(e))){if(connected)resetSequence();say('Premiere에서 편집할 시퀀스를 열어 주세요.');}
    else throw e;
  }
}
async function requireCurrent({responseCurrent=()=>true,guardFactory=null,onSettledRead=null,guidanceCurrent=()=>true}={}){
  admitted();const scope=projectScope(),read=projectRead;
  const current=()=>!binding&&projectRead===read&&projectScopeCurrent(scope)&&responseCurrent();
  if(!current())throw currentCheckDiscarded;
  let fresh;
  try{fresh=await ContentriumHost.snapshot();}catch(e){if(!current())throw currentCheckDiscarded;throw e;}
  if(!current())throw currentCheckDiscarded;
  const changed=fresh.snapshot.snapshotHash!==scope.hostSnapshotHash;
  if(changed){
    const receipt=await readProject({fresh,automatic:true,guardFactory,onSettledRead,guidanceCurrent});
    if(!receipt||binding||projectRead||!projectScopeCurrent(receipt)||!responseCurrent())throw currentCheckDiscarded;
    throw new Error('타임라인이 변경됐습니다. 갱신된 트랙 설정을 확인하세요.');
  }
  const issue=rangeFeedback();if(issue)throw Object.assign(new Error(issue),{code:'RANGE_INPUT_INVALID'});
  if(rangeDirty){
    const receipt=await readProject({fresh,guardFactory,onSettledRead,guidanceCurrent});
    if(!receipt||binding||projectRead||!projectScopeCurrent(receipt)||!responseCurrent())throw currentCheckDiscarded;
  }
}
async function seekFrame(frame){await requireCurrent();return afterEditingPreview(()=>connected.sequence.setPlayerPosition(ContentriumHost.time(String(BigInt(frame)*BigInt(connected.perFrame)))));}
const cutReasons={START_CAMERA:'시작 카메라',START_SPEAKER:'첫 발화 화자',SPEAKER_TURN:'화자 전환',speech:'발화',speaker:'화자',MANUAL_OVERRIDE:'수동 카메라 지정',OVERRIDE_END:'수동 지정 종료',OVERLAP_SUSTAINED:'지속된 동시 발화',OVERLAP_HOLD:'동시 발화 중 카메라 유지',MIN_SHOT_HOLD:'최소 샷 길이 유지',VIDEO_GAP_FALLBACK:'영상 공백으로 대체 카메라',MIN_SHOT_EXCEPTION_OVERRIDE:'수동 지정으로 최소 샷 길이 예외',MIN_SHOT_EXCEPTION_OVERRIDE_END:'수동 지정 종료로 최소 샷 길이 예외',MIN_SHOT_EXCEPTION_OVERLAP:'동시 발화로 최소 샷 길이 예외',MIN_SHOT_EXCEPTION_COVERAGE:'영상 공백으로 최소 샷 길이 예외',RANGE_END_SHORT:'분석 범위 끝의 짧은 샷'};
Object.assign(cutReasons,{HOLD:'카메라 유지',UNKNOWN_HOLD:'화자 불확실로 카메라 유지'});
function segmentReason(segment){const codes=[...new Set([segment.reason,...(Array.isArray(segment.reasonCodes)?segment.reasonCodes:[])].filter(code=>typeof code==='string'&&code.trim()))];return codes.length?'이유 · '+codes.map(code=>Object.prototype.hasOwnProperty.call(cutReasons,code)?cutReasons[code]:code).join(' · '):'이유 정보 없음';}
function renderPlan(){
  const segments=plan.segments;$('cut-count').textContent=String(segments.length);$('review-count').textContent=String(plan.reviews.length);$('timeline').innerHTML='';
  const bar=element('div',undefined,'timeline');for(const segment of segments){const block=element('div');block.style.flex=String(segment.endFrame-segment.startFrame);bar.appendChild(block);}$('timeline').appendChild(bar);$('segments').innerHTML='';
  const selected=$('review-camera').value,ids=[...new Set(segments.map(segment=>segment.cameraId))];
  options($('review-camera'),ids.map(id=>[id,cameraRows.find(row=>row.id===id)?.title||id]),'모든 카메라');
  if(ids.includes(selected))$('review-camera').value=selected;
  reviewPage=0;renderReviewCuts();
  $('reviews').innerHTML='';
  for(const review of plan.reviews){const row=element('div',(review.startFrame===undefined?'전체':frameLabel(review.startFrame)+'–'+frameLabel(review.endFrame))+' · '+(review.message||review.code),'review-row');if(review.startFrame!==undefined)row.appendChild(workButton('구간 확인',()=>seekFrame(review.startFrame)));$('reviews').appendChild(row);}
  if(!plan.reviews.length)$('reviews').appendChild(element('p','확인이 필요한 사항이 없습니다.','hint'));toggle();
}
function renderReviewCuts(){
  reviewWindow=cutReview.window(plan?.segments||[],{cameraId:$('review-camera').value,query:$('review-search').value,page:reviewPage},
    segment=>(cameraRows.find(row=>row.id===segment.cameraId)?.title||segment.cameraId)+' '+segmentReason(segment)+' '+frameLabel(segment.startFrame)+' '+segmentTiming(segment));
  reviewPage=reviewWindow.index;$('segments').innerHTML='';
  for(const segment of reviewWindow.rows){
    const row=workButton('',()=>seekFrame(segment.startFrame));row.className='segment-row';
    const camera=cameraRows.find(r=>r.id===segment.cameraId);row.appendChild(element('span',frameLabel(segment.startFrame)+'–'+frameLabel(segment.endFrame),'segment-range'));row.appendChild(element('span',camera?.title||segment.cameraId,'segment-camera'));row.appendChild(element('span',segmentReason(segment),'segment-reasons'));row.appendChild(element('span',segmentTiming(segment),'segment-timing'));$('segments').appendChild(row);
  }
  if(!reviewWindow.total)$('segments').appendChild(element('p',plan?'일치하는 컷이 없습니다. 검색과 카메라 필터를 확인하세요.':'편집안을 만들어 주세요.','hint'));
  $('review-page-info').textContent=reviewWindow.total?reviewWindow.first+'–'+reviewWindow.last+' / '+reviewWindow.total+' 컷':'0 컷';toggle();
}
function editOverrides(change){if(workLocked()||!connected)return;try{change();}catch(e){error(e);}}
function addOverride(defer=false){
  if(!defer){overrideErrorsOnly=false;overrideVisibleRows.clear();}
  if(!connected)return;const row=element('div',undefined,'override-row'),fields=element('div',undefined,'row'),first=number(connected.snapshot.range.startFrame),last=number(connected.snapshot.range.endFrame),camera=element('select');
  const title=element('b','수동 구간 '+(overrideRows.length+1)),error=element('p','', 'hint input-error hidden'),errorId='override-error-'+(++overrideSerial);
  error.setAttribute('id',errorId);error.setAttribute('role','status');error.setAttribute('aria-live','polite');row.appendChild(title);
  options(camera,cameraValues());for(const field of [first,last]){field.setAttribute('min',String(connected.snapshot.range.startFrame));field.setAttribute('max',String(connected.snapshot.range.endFrame));field.setAttribute('step','1');}
  fields.appendChild(label('시작 · 프레임',first));fields.appendChild(label('종료 · 프레임',last));row.appendChild(fields);row.appendChild(label('고정 카메라',camera));row.appendChild(error);
  const remove=element('button','삭제');remove.setAttribute('data-work','true');remove.disabled=workLocked()||!connected;row.appendChild(remove);const value={first,last,camera,error,title,row,remove};for(const field of [first,last,camera]){field.disabled=workLocked()||!connected;field.setAttribute('aria-describedby',errorId);field.oninput=field.onchange=()=>editOverrides(invalidatePlan);}
  overrideRows.push(value);remove.onclick=()=>editOverrides(()=>{const index=overrideRows.indexOf(value);if(index<0)return;overrideRows.splice(index,1);overrideVisibleRows.delete(value);row.remove();invalidatePlan();});$('overrides').appendChild(row);if(!defer)invalidatePlan();
}
function localUpdatePhase(phase){return ['IDLE','COMPLETE','CANCELED','FAILED_BEFORE_REPLACE','ROLLED_BACK','UNAVAILABLE'].includes(phase);}
function localUpdateGuidance(){
  if(!credentials)return (updateIntent?.accepted?'업데이트 시작이 접수되었습니다.':'업데이트 시작 결과를 확인하지 못했습니다.')+' 새로고침으로 연결과 업데이트 상태를 확인하세요.';
  if(updateIntent?.inFlight)return '작업 중단을 요청하고 업데이트 시작 요청을 확인하고 있습니다.';
  if(updateIntent?.accepted)return '업데이트 시작이 접수되었습니다. 진행 상태를 확인하고 있습니다.';
  return credentials?'업데이트 시작 결과를 확인하지 못했습니다. 업데이트 시작 재확인을 누르면 같은 요청을 다시 확인합니다. 작업은 중단된 상태로 유지됩니다.':'업데이트 시작 결과를 확인하지 못했습니다. 새로고침으로 연결과 업데이트 상태를 확인하세요.';
}
function renderUpdateUI(){
  const update=state?.update,local=localUpdatePhase(update?.updateState);
  const active=['STOP_REQUESTED','QUIESCING','DOWNLOADING','VERIFYING_PACKAGE','WAITING_HOST_EXIT','INSTALLING','PENDING_ACTIVATION','VERIFYING_INSTALL','ROLLING_BACK'].includes(update?.updateState);
  const busy=!!updateIntent?.inFlight||active||local&&!!updateIntent?.accepted;
  const label=local&&updateIntent?.inFlight?'시작 요청 중…':busy?'업데이트 진행 중':local&&updateIntent&&!updateIntent.accepted?'업데이트 시작 재확인':null;
  for(const id of ['update','update-banner-button']){const button=$(id);button.textContent=label||(id==='update'?'업데이트':'지금 업데이트');button.setAttribute('aria-busy',busy?'true':'false');}
  if(update)$('update-info').textContent=updateGuidance(update)+(!credentials&&!local&&(updateIntent||active)?' 새로고침으로 연결과 업데이트 상태를 확인하세요.':'');
  return busy;
}
function updateGuidance(update){
  const phase=update.updateState,code=update.error?.code;
  const suffix=typeof code==='string'&&/^[A-Z][A-Z0-9_]{0,63}$/.test(code)?' ('+code+')':'';
  const remaining=Number.isFinite(update.retryAt)?Math.max(0,Math.ceil(update.retryAt-Date.now()/1000)):0;
  const retry=remaining?'최소 '+remaining+'초 후 업데이트 확인을 다시 누르세요.':'업데이트 확인을 다시 누르세요.';
  if(updateIntent&&localUpdatePhase(phase))return localUpdateGuidance();
  const phases={STOP_REQUESTED:'작업 중단을 요청했습니다.',
    QUIESCING:previewPlaying||previewBusy?'미리보기 종료를 기다리고 있습니다. 지연되면 프로젝트를 저장하고 Premiere를 정상 종료하세요.':'진행 중인 작업을 안전하게 종료하고 있습니다.',
    DOWNLOADING:'업데이트 파일을 다운로드하고 있습니다.',VERIFYING_PACKAGE:'다운로드한 파일을 검증하고 있습니다.',
    WAITING_HOST_EXIT:code==='UPDATE_RATE_LIMIT'?'서버 요청 제한으로 설치를 기다리고 있습니다. '+(remaining?'최소 '+remaining+'초 후 자동으로 다시 시도합니다. ':'')+'프로젝트를 저장하고 Premiere를 정상 종료하세요.':'프로젝트를 저장하고 Premiere를 정상 종료하면 설치를 계속합니다.',
    INSTALLING:'업데이트 파일을 교체하고 있습니다.',PENDING_ACTIVATION:'새 플러그인의 실행을 확인하고 있습니다.',
    VERIFYING_INSTALL:'설치 결과를 검증하고 있습니다.',ROLLING_BACK:'이전 버전으로 복구하고 있습니다.',
    RECOVERY_REQUIRED:'업데이트를 계속하려면 설치 복구를 누르세요.',FAILED:'업데이트를 계속하려면 설치 복구를 누르세요.',
    CANCELED:'업데이트를 중단했습니다. '+retry,
    FAILED_BEFORE_REPLACE:'파일 교체 전에 업데이트가 실패했습니다. '+retry+suffix,
    ROLLED_BACK:'이전 버전으로 복구했습니다. '+retry};
  if(!['IDLE','COMPLETE','UNAVAILABLE'].includes(phase))return phases[phase]||'업데이트 상태를 확인하지 못했습니다. 설정에서 설치 상태를 확인하세요.';
  if(updateIntent)return localUpdateGuidance();
  if(phase==='UNAVAILABLE'||update.checkState==='UNAVAILABLE')return '자동 업데이트 연결을 사용할 수 없습니다. Contentrium CUT Setup으로 설치를 복구하세요.';
  if(update.checkState==='INCOMPATIBLE')return '새 버전 '+(update.candidate?.appVersion||'')+'은 현재 설치 환경과 호환되지 않습니다. Premiere 버전과 설치 환경을 확인하세요.';
  if(update.candidate&&['AVAILABLE','CHECKING'].includes(update.checkState))return '새 버전 '+update.candidate.appVersion;
  if(update.checkState==='CHECK_FAILED'){
    if(code==='UPDATE_RATE_LIMIT')return '서버 요청 제한으로 업데이트를 확인하지 못했습니다. '+retry+suffix;
    if(['UPDATE_SIGNATURE','UPDATE_HASH','UPDATE_MANIFEST','UPDATE_ASSET','UPDATE_URL'].includes(code))return '업데이트 파일 검증에 실패했습니다. '+retry+suffix;
    return '업데이트를 확인하지 못했습니다. 네트워크 연결을 확인하고 '+retry+suffix;
  }
  return {CURRENT:'최신 버전입니다.',NO_RELEASE:'게시된 업데이트가 없습니다.',CHECKING:'업데이트 확인 중'}[update.checkState]||'업데이트 상태를 확인하지 못했습니다. 업데이트 확인을 다시 누르세요.';
}
function refreshScope(){
  return [credentials,state,state?.epoch,state?.gateOpen,state?.stopEpoch,state?.compatible,state?.appVersion,state?.bundleId,state?.protocolVersion,JSON.stringify([state?.models,state?.update,state?.applyRecovery,state?.maintenance]),stopRevision,stopped,updateIntent,JSON.stringify(updateIntent),panelContextConflict,mode,connected,connected?.snapshot.snapshotHash,connected?.snapshot.hostSnapshotHash,analysisState,analysisState?.revision,plan,planInputHash,job,applying,batchRunning,previewPlaying,previewBusy,localEditPending,localIntentError,binding,projectRead,projectSelection,inputCapability,validationRevision,validationCount];
}
function validStateReceipt(receipt,prior){
  const record=value=>!!value&&typeof value==='object'&&!Array.isArray(value),integer=value=>Number.isSafeInteger(value)&&value>=0,text=value=>typeof value==='string'&&!!value.trim();
  if(!record(receipt)||!integer(receipt.epoch)||typeof receipt.gateOpen!=='boolean'||typeof receipt.compatible!=='boolean'||!text(receipt.appVersion)||!text(receipt.bundleId)||!integer(receipt.protocolVersion)||receipt.protocolVersion===0||!(receipt.stopEpoch==null||integer(receipt.stopEpoch)&&receipt.stopEpoch<=receipt.epoch))return false;
  if(prior&&receipt.appVersion===prior.appVersion&&receipt.bundleId===prior.bundleId&&receipt.protocolVersion===prior.protocolVersion&&receipt.epoch<prior.epoch)return false;
  if(!record(receipt.models)||['silero','community-1'].some(id=>!record(receipt.models[id])||!text(receipt.models[id].status)))return false;
  const update=receipt.update,recovery=receipt.applyRecovery;
  if(!record(update)||!text(update.updateState)||!text(update.checkState)||update.updateEpoch!==undefined&&!integer(update.updateEpoch)||!(update.candidate==null||record(update.candidate)&&text(update.candidate.candidateId)&&text(update.candidate.appVersion))||update.candidate?.releaseNotes!==undefined&&typeof update.candidate.releaseNotes!=='string')return false;
  return record(recovery)&&typeof recovery.blocked==='boolean'&&(recovery.records===undefined||Array.isArray(recovery.records))&&(receipt.maintenance==null||record(receipt.maintenance));
}
async function refresh(current=()=>true,accepted=()=>{},guidanceCurrent=()=>true){
  if(!credentials||!current())return false;
  const token={credential:credentials,prior:state,scope:refreshScope(),guidanceRevision:statusRevision};refreshRequest=token;
  const owned=()=>refreshRequest===token&&credentials===token.credential;
  const scopeCurrent=()=>{if(!owned())return false;const scope=refreshScope();return token.scope.every((value,index)=>value===scope[index]);};
  const ready=receipt=>scopeCurrent()&&current(receipt);
  const failed=e=>{
    if(!ready())return;
    const guide=statusRevision===token.guidanceRevision&&guidanceCurrent();
    if(e?.code==='PANEL_CONTEXT_CONFLICT'){contextConflict(guide);return;}
    stopped=true;setConnection(false,'편집 상태 확인 필요');
    if(['AUTH_REQUIRED','SESSION_EXPIRED'].includes(e?.code)){credentials=null;connection.reset();retryAt=Date.now()+1000;setConnection(false,'편집 연결 복구 중');}
    if(guide)say(e?.code==='AUTH_REQUIRED'||e?.code==='SESSION_EXPIRED'?'편집 연결을 복구하고 있습니다. 새로고침으로 상태를 확인하세요.':'편집 상태를 확인하지 못했습니다. 새로고침을 다시 실행하세요.');
    toggle();
  };
  try{
    let receipt;try{receipt=await api('/state');}catch(e){failed(e);return false;}
    if(!ready(receipt))return false;
    if(!validStateReceipt(receipt,token.prior)){failed();return false;}
    state=receipt;token.scope=refreshScope();accepted();if(!ready())return false;
  if(updateIntent&&state.gateOpen&&state.update.updateEpoch>updateIntent.epoch&&['COMPLETE','CANCELED','FAILED_BEFORE_REPLACE','ROLLED_BACK'].includes(state.update.updateState))updateIntent=null;
  if(cancelRequest&&cancelCurrent(cancelRequest)||updateIntent||!state.gateOpen||state.stopEpoch!==null&&state.stopEpoch!==undefined){stopped=true;plan=null;}else if(!applying)stopped=false;
  $('boot-status').className='hidden';
  const compatible=state.appVersion===bundle.appVersion&&state.bundleId===bundle.bundleId&&state.protocolVersion===bundle.protocolVersion;
  if(!compatible){stopped=true;plan=null;setConnection(false,'업데이트된 패널을 열려면 Premiere를 다시 시작하세요.');}
  else if(state.compatible===false)setConnection(false,'Premiere 연결 확인 중');
  else setConnection(true,updateIntent?'업데이트 진행 중':state.gateOpen?'편집 준비됨':state.maintenance?'캐시 정리 상태 확인':'업데이트 진행 중');
  const statuses={ready:'준비됨',installed:'설치됨 · 분석 시 무결성 확인',not_installed:'설치 필요',error:'모델 정보 확인 필요',failed:'확인 필요',installing:'설치 중'};
  $('models').textContent=['silero','community-1'].map(id=>(id==='silero'?'Silero':'Community-1')+' · '+(statuses[state.models[id].status]||state.models[id].status)).join('\n');
  const required=state.models[mode==='separate'?'silero':'community-1'];$('model-status').textContent=['ready','installed'].includes(required.status)?'분석 시작 시 로컬 모델 무결성을 확인합니다.':missingModelGuidance();
  const update=state.update,candidate=update.candidate;
  $('update-banner').className=!updateIntent&&candidate&&['AVAILABLE','CHECKING'].includes(update.checkState)&&candidate.candidateId!==dismissedCandidate?'':'hidden';
  $('update-banner-text').textContent=candidate?'Contentrium CUT '+candidate.appVersion+' 업데이트':'';
  $('release-notes').textContent=candidate?.releaseNotes||'';
  const recovery=state.applyRecovery;
  $('apply-recovery').className=localEditPending||recovery?.blocked?'notice recovery-notice':'hidden';
  $('apply-recovery-text').textContent=localIntentError?messages[localIntentError]:localEditPending||recovery?.blocked?'이전 편집이 중단되었습니다. 기록을 확인한 뒤 새 작업을 시작할 수 있습니다.':'';
  $('cache-maintenance').className=state.maintenance?'':'hidden';
  $('cache-maintenance-text').textContent=state.maintenance?.canRelease?'정리 작업이 종료됐습니다. 편집을 계속할 수 있습니다.':'캐시 정리 작업이 종료되는 것을 기다리고 있습니다.';

    toggle();token.scope=refreshScope();accepted();
    if(!ready())return false;
    if(!state.gateOpen&&!state.maintenance&&!applying&&!batchRunning&&!previewPlaying&&!previewBusy){
      const epoch=receipt.epoch;
      if(ready())await api('/updates/ack',{epoch,quiescent:true,batchRunning:false}).catch(()=>{});
    }
    return ready();
  }finally{if(refreshRequest===token)refreshRequest=null;}
}

async function heartbeat(current=()=>true){
  if(!credentials||!state||!current())return false;
  const token={credential:credentials,scope:refreshScope(),epoch:state.epoch,guidanceRevision:statusRevision};heartbeatOwner=token;
  const ready=receipt=>{if(heartbeatOwner!==token||credentials!==token.credential)return false;const scope=refreshScope();return token.scope.every((value,index)=>value===scope[index])&&current(receipt);};
  const failed=e=>{
    if(!ready())return;
    const guide=statusRevision===token.guidanceRevision;
    if(e?.code==='PANEL_CONTEXT_CONFLICT'){contextConflict(guide);return;}
    stopped=true;setConnection(false,'편집 연결 상태 확인 필요');
    if(['AUTH_REQUIRED','SESSION_EXPIRED'].includes(e?.code)){credentials=null;connection.reset();retryAt=Date.now()+1000;setConnection(false,'편집 연결 복구 중');}
    if(guide)say(['AUTH_REQUIRED','SESSION_EXPIRED'].includes(e?.code)?'편집 연결을 복구하고 있습니다. 새로고침으로 상태를 확인하세요.':'편집 연결 상태를 확인하지 못했습니다. 새로고침을 다시 실행하세요.');
    toggle();
  };
  try{
    let receipt;try{receipt=await api('/heartbeat',{hostIdentity:null,epoch:token.epoch,batchRunning,quiescent:!applying&&!previewPlaying&&!previewBusy,panelVersion:bundle.appVersion,bundleId:bundle.bundleId,protocolVersion:bundle.protocolVersion});}catch(e){failed(e);return false;}
    if(!ready(receipt))return false;
    if(!receipt||typeof receipt!=='object'||Array.isArray(receipt)||!Number.isSafeInteger(receipt.epoch)||receipt.epoch<token.epoch||typeof receipt.gateOpen!=='boolean'||!(receipt.stopEpoch===null||Number.isSafeInteger(receipt.stopEpoch)&&receipt.stopEpoch>=0&&receipt.stopEpoch<=receipt.epoch)){failed();return false;}
    if(receipt.epoch>token.epoch||!receipt.gateOpen||receipt.stopEpoch!==null){stopped=true;plan=null;toggle();}
    return true;
  }finally{if(heartbeatOwner===token)heartbeatOwner=null;}
}
function periodicHeartbeat(){
  if(!heartbeatRequest){const next=heartbeat();heartbeatRequest=next;const clear=()=>{if(heartbeatRequest===next)heartbeatRequest=null;};next.then(clear,clear);}
  return heartbeatRequest;
}
const canceledJobs=new Set();
function polledJobCurrent(active,epoch){
  return job===active&&!canceledJobs.has(active.jobId)&&!stopped&&!updateIntent&&!applying&&!validationCount&&!panelContextConflict&&!!credentials&&!!state?.gateOpen&&state.compatible!==false&&!localEditPending&&!state.applyRecovery?.blocked&&state.stopEpoch==null&&state.epoch===epoch;
}
function inputProbeCurrent(active,scope){
  return polledJobCurrent(active,scope.epoch)&&!!scope.selection&&projectSelection===scope.selection&&projectSelection.selectionId===active.selectionId&&selectedRows.length===scope.rows.length&&scope.rows.every((row,index)=>selectedRows[index]===row);
}
function analysisResultCurrent(active,scope){
  return polledJobCurrent(active,scope.epoch)&&!!scope.connection&&connected===scope.connection&&connected.snapshot.snapshotHash===scope.snapshotHash&&scope.snapshotHash===active.snapshotHash&&connected.snapshot.hostSnapshotHash===scope.hostSnapshotHash&&mode===scope.mode&&analysisState===scope.analysisState&&analysisState?.revision===scope.revision;
}
async function analysisSnapshotCurrent(active,scope){
  let fresh;
  try{fresh=await ContentriumHost.snapshot();}catch(e){if(!analysisResultCurrent(active,scope))return false;error(e);throw e;}
  if(!analysisResultCurrent(active,scope))return false;
  if(fresh?.snapshot?.snapshotHash!==scope.hostSnapshotHash){resetSequence();const issue=new Error('분석 중 타임라인이 변경됐습니다. 현재 시퀀스를 다시 읽어 주세요.');error(issue);throw issue;}
  return true;
}
function finishPolledJob(active){
  if(job===active)job=null;
  if(!job||job.jobId!==active.jobId)canceledJobs.delete(active.jobId);
  toggle();
}
function modelPollAdmission(){
  return [stopRevision,validationRevision,validationCount,state?.gateOpen,state?.stopEpoch,state?.compatible,state?.appVersion,state?.bundleId,state?.protocolVersion,state?.maintenance,stopped,updateIntent,panelContextConflict,localEditPending,state?.applyRecovery?.blocked,applying];
}
async function pollModelJob(active){
  if(modelPoll)return;
  const token={active,credential:credentials,epoch:state?.epoch,jobEpoch:active.epoch??state?.epoch,admission:modelPollAdmission(),guidanceRevision:statusRevision,modelStatus:$('model-install-status').textContent};modelPoll=token;
  const owned=()=>modelPoll===token&&job===active&&credentials===token.credential&&state?.epoch===token.epoch;
  const current=()=>owned()&&modelPollAdmission().every((value,index)=>value===token.admission[index]);
  const canGuide=()=>current()&&!stopped&&!updateIntent&&!canceledJobs.has(active.jobId)&&!!credentials&&!!state?.gateOpen&&state.stopEpoch==null&&state.compatible!==false&&!validationCount&&!state.maintenance&&!panelContextConflict&&!localEditPending&&!state.applyRecovery?.blocked&&!applying;
  const queryFailure=()=>{
    if(!canGuide())return;
    const text='모델 설치 상태를 확인하지 못했습니다. 실제 작업 종료를 확인할 때까지 다시 조회합니다.';
    if($('model-install-status').textContent===token.modelStatus)$('model-install-status').textContent=text;
    if(statusRevision===token.guidanceRevision)say(text);
  };
  try{
    let value;
    try{value=await api('/jobs/'+active.jobId);}catch(e){
      if(canGuide()){
        queryFailure();
        if(['AUTH_REQUIRED','SESSION_EXPIRED'].includes(e.code)){setConnection(false,'편집 연결 복구 중');credentials=null;connection.reset();retryAt=Date.now()+1000;toggle();}
      }
      return;
    }
    if(!owned())return;
    if(!value||value.jobId!==active.jobId||value.kind!=='model-setup'||value.epoch!==token.jobEpoch||typeof value.drained!=='boolean'||!['running','canceling','completed','canceled','failed','interrupted'].includes(value.status)){queryFailure();return;}
    if(['running','canceling'].includes(value.status)||!value.drained){
      if(!canGuide())return;
      if(value.status==='canceling'&&$('model-install-status').textContent===token.modelStatus)modelInstallResult('canceling');
      if(statusRevision===token.guidanceRevision)say(value.status==='canceling'?'작업을 중단하고 있습니다.':'화자 모델을 설치하고 있습니다.');
      return;
    }
    const canceled=canceledJobs.has(active.jobId);
    if(!current()&&!canceled)return;
    if(canGuide()){
      const outcome=value.status==='completed'?'completed':value.status==='canceled'?'canceled':value.status==='interrupted'?'interrupted':'failed',text=modelInstallMessage(outcome,value.error?.code);
      if($('model-install-status').textContent===token.modelStatus)$('model-install-status').textContent=text;
      if(statusRevision===token.guidanceRevision)say(text);
    }else if(canceled&&$('model-install-status').textContent===modelInstallMessage('canceling'))modelInstallResult('canceled');
    finishPolledJob(active);
  }finally{if(modelPoll===token)modelPoll=null;}
}
function syncValidation(count,descriptors){
  const token=syncPoll;if(!token)return false;
  const own=!token.invalid&&token.phase==='query'&&!token.validationFinished&&count===1&&Array.isArray(descriptors)&&descriptors.length===1&&descriptors[0].path==='/jobs/'+token.active.jobId&&descriptors[0].epoch===token.epoch&&typeof descriptors[0].id==='string'&&/^[a-f0-9]{32}$/.test(descriptors[0].id)&&(!token.id||token.id===descriptors[0].id);
  if(own){token.id=descriptors[0].id;token.validationActive=true;token.validationRevision=validationRevision;return true;}
  if(!count&&token.validationActive&&!token.invalid){token.validationActive=false;token.validationFinished=true;token.validationRevision=validationRevision;return false;}
  token.invalid=true;return false;
}
function syncPollScope(){
  return [credentials,connected,connected?.snapshot.snapshotHash,connected?.snapshot.hostSnapshotHash,mode,job,state?.epoch,state?.gateOpen,state?.stopEpoch,state?.compatible,state?.appVersion,state?.bundleId,state?.protocolVersion,state?.maintenance,stopped,updateIntent,stopRevision,panelContextConflict,localEditPending,state?.applyRecovery?.blocked,applying,batchRunning,binding,projectRead,projectSelection,inputCapability,analysisState,analysisState?.revision,plan,planInputHash,syncResult,syncJob,syncResultInputHash,syncInvalidated,settingsRestore,settingsSave,resourceRequest,cacheRequest,modelRequest,syncInputHash(),JSON.stringify([$('sync-method').value,$('sync-reference').value,syncRows.map(r=>[r.check.checked,r.stream.value,r.channel.value,r.offset.value,r.confirmed.checked,r.clockId.value,r.date.value,r.fps.value,r.drop.checked,r.clockConfirmed.checked])])];
}
function stagedSyncDisplay(result,assets,connection){
  const record=value=>!!value&&typeof value==='object'&&!Array.isArray(value);
  if(!record(result)||!record(result.sources)||!record(result.offsets)||Object.keys(result.sources).length!==assets.length||assets.some(id=>!record(result.sources[id])||typeof result.sources[id].status!=='string'))throw Object.assign(new Error('싱크 결과를 확인하지 못했습니다. 싱크를 다시 분석하세요.'),{code:'SYNC_RESULT_INVALID'});
  const sources=new Map(connection.snapshot.sources.map(s=>[s.assetId,basename(s.canonicalPath)]));
  return assets.map(id=>{const evidence=result.sources[id],offset=result.offsets[id];if(evidence.status==='accepted'&&(!['number','string'].includes(typeof offset)||typeof offset==='string'&&!offset.trim()||!Number.isFinite(Number(offset))))throw Object.assign(new Error('싱크 시간 정보를 확인하지 못했습니다. 싱크를 다시 분석하세요.'),{code:'SYNC_RESULT_INVALID'});return sources.get(id)+' · '+(evidence.status==='accepted'?Number(offset).toFixed(3)+'초':'확인 필요 · '+(evidence.reason||evidence.code||evidence.status));}).join('\n');
}
async function pollSyncJob(active){
  if(syncPoll)return;
  const token={active,credential:credentials,epoch:state?.epoch,jobEpoch:active.epoch??state?.epoch,scope:syncPollScope(),rows:syncRows.slice(),phase:'query',validationRevision,validationActive:false,validationFinished:false,invalid:false,guidanceRevision:statusRevision};syncPoll=token;
  const owned=()=>syncPoll===token&&job===active&&credentials===token.credential&&state?.epoch===token.epoch;
  const current=()=>{if(!owned()||token.invalid||validationRevision!==token.validationRevision||validationCount!==(token.validationActive?1:0))return false;const scope=syncPollScope();return token.scope.every((value,index)=>value===scope[index])&&syncRows.length===token.rows.length&&token.rows.every((row,index)=>row===syncRows[index]);};
  const canGuide=()=>current()&&!token.validationActive&&polledJobCurrent(active,token.epoch)&&!state.maintenance&&!batchRunning&&!binding&&!projectRead&&!!connected&&active.snapshotHash===connected.snapshot.snapshotHash&&active.inputHash===syncInputHash();
  const queryFailure=()=>{if(canGuide()&&statusRevision===token.guidanceRevision)say('싱크 작업 상태를 확인하지 못했습니다. 실제 종료를 확인할 때까지 다시 조회합니다.');};
  const showError=e=>{if(canGuide()&&statusRevision===token.guidanceRevision)error(e);};
  try{
    let value;
    try{value=await api('/jobs/'+active.jobId);}catch(e){
      const sourceChanged=['SOURCE_CHANGED','SOURCE_REANALYSIS_REQUIRED'].includes(e.code),stopping=canceledJobs.has(active.jobId);
      if(current()&&!token.validationActive&&((sourceChanged&&canGuide())||stopping)&&!['AUTH_REQUIRED','SESSION_EXPIRED'].includes(e.code)){
        // Completed GET source verification can fail or be gated after stop.
        // A fresh authenticated state may prove drain, but never supplies a result.
        token.phase='drain';let receipt;
        try{receipt=await api('/state');}catch(_){if(canGuide())queryFailure();return;}
        if(!current())return;
        const terminal=Array.isArray(receipt?.jobs)?receipt.jobs.find(value=>value?.jobId===active.jobId):null;
        if(receipt?.epoch===token.epoch&&receipt.appVersion===state.appVersion&&receipt.bundleId===state.bundleId&&receipt.protocolVersion===state.protocolVersion&&terminal?.kind==='sync'&&terminal.epoch===token.jobEpoch&&terminal.drained===true&&['completed','canceled','failed','interrupted'].includes(terminal.status)){
          if(sourceChanged&&canGuide()&&statusRevision===token.guidanceRevision){syncInvalidated=true;say('싱크 원본이 변경됐습니다. 현재 시퀀스를 다시 읽고 싱크를 다시 분석하세요.');}
          finishPolledJob(active);return;
        }
      }
      if(canGuide()){queryFailure();if(['AUTH_REQUIRED','SESSION_EXPIRED'].includes(e.code)){setConnection(false,'편집 연결 복구 중');credentials=null;connection.reset();retryAt=Date.now()+1000;toggle();}}return;
    }
    if(!owned())return;
    if(!value||value.jobId!==active.jobId||value.kind!=='sync'||value.epoch!==token.jobEpoch||typeof value.drained!=='boolean'||!['running','canceling','completed','canceled','failed','interrupted'].includes(value.status)){queryFailure();return;}
    if(['running','canceling'].includes(value.status)||!value.drained){if(canGuide()&&statusRevision===token.guidanceRevision)say(value.status==='canceling'?'싱크 작업을 중단하고 있습니다.':'소스의 싱크를 분석하고 있습니다.');return;}
    const canceled=()=>canceledJobs.has(active.jobId);
    if(!current()&&!canceled())return;
    if(!canGuide()){if(current()&&!stopped&&!updateIntent&&!canceled()&&connected&&active.inputHash!==syncInputHash()&&!syncResult&&!syncJob&&statusRevision===token.guidanceRevision)invalidateSyncResult();if(!token.validationActive)finishPolledJob(active);return;}
    if(value.status!=='completed'){if(statusRevision===token.guidanceRevision)say(value.status==='interrupted'?'싱크 작업이 이전 연결에서 중단됐습니다. 싱크를 다시 분석하세요.':'싱크 작업을 중단했습니다. 설정을 확인하고 다시 분석하세요.');finishPolledJob(active);return;}
    let display;try{display=stagedSyncDisplay(value.result,syncOptions().assetIds,connected);}catch(e){showError(e);finishPolledJob(active);return;}
    token.phase='snapshot';let fresh;
    try{fresh=await ContentriumHost.snapshot();}catch(e){if(canGuide()){showError(e);finishPolledJob(active);}else if(owned()&&canceled())finishPolledJob(active);return;}
    if(!canGuide()){if(owned()&&canceled())finishPolledJob(active);return;}
    if(fresh?.snapshot?.snapshotHash!==connected.snapshot.hostSnapshotHash){const guide=statusRevision===token.guidanceRevision;resetSequence();if(guide)error(new Error('싱크 중 타임라인이 변경됐습니다. 현재 시퀀스를 다시 읽어 주세요.'));if(owned())finishPolledJob(active);return;}
    syncJob=value.jobId;syncResult=value.result;syncResultInputHash=active.inputHash;$('sync-result').textContent=display;
    if(statusRevision===token.guidanceRevision)say('싱크 분석 완료 · 확인이 필요한 소스를 검토하세요.');finishPolledJob(active);
  }finally{if(syncPoll===token)syncPoll=null;}
}
function examplePollScope(){
  return [credentials,connected,connected?.snapshot.snapshotHash,connected?.snapshot.hostSnapshotHash,mode,job,state?.epoch,state?.gateOpen,state?.stopEpoch,state?.compatible,state?.appVersion,state?.bundleId,state?.protocolVersion,state?.maintenance,stopped,updateIntent,stopRevision,panelContextConflict,localEditPending,state?.applyRecovery?.blocked,applying,batchRunning,binding,projectRead,projectSelection,inputCapability,analysisState,analysisState?.analysisId,analysisState?.revision,analysisJob,plan,planInputHash,syncResult,syncJob,syncResultInputHash,settingsRestore,settingsSave,resourceRequest,cacheRequest,modelRequest,correctionRequest,validationRevision,validationCount];
}
async function pollExampleJob(active){
  if(examplePoll||previewBusy)return;
  const token={active,credential:credentials,epoch:state?.epoch,jobEpoch:active.epoch,scope:examplePollScope(),rows:selectedRows.slice(),guidanceRevision:statusRevision};examplePoll=token;
  const owned=()=>examplePoll===token&&job===active&&credentials===token.credential&&state?.epoch===token.epoch;
  const current=()=>{if(!owned())return false;const scope=examplePollScope();return token.scope.every((value,index)=>value===scope[index])&&selectedRows.length===token.rows.length&&token.rows.every((row,index)=>row===selectedRows[index]);};
  const usable=()=>current()&&polledJobCurrent(active,token.epoch)&&!state.maintenance&&!batchRunning&&!binding&&!projectRead&&!!connected&&active.snapshotHash===connected.snapshot.snapshotHash&&!!analysisState&&analysisState.analysisId===active.analysisId&&analysisState.revision===active.revision;
  const guide=()=>usable()&&statusRevision===token.guidanceRevision;
  const queryFailure=()=>{if(guide())say('음성 샘플 작업 상태를 확인하지 못했습니다. 실제 종료를 확인할 때까지 다시 조회합니다.');};
  const terminal=value=>!!value&&value.jobId===active.jobId&&value.kind==='example'&&value.epoch===token.jobEpoch&&value.drained===true&&['completed','canceled','failed','interrupted'].includes(value.status);
  const cleanup=()=>{if(owned()&&(current()||canceledJobs.has(active.jobId)))finishPolledJob(active);};
  try{
    let value;
    try{value=await api('/jobs/'+active.jobId);}catch(e){
      if(current()&&((e.code==='EXAMPLE_SCOPE'&&usable())||canceledJobs.has(active.jobId))&&!['AUTH_REQUIRED','SESSION_EXPIRED'].includes(e.code)){
        let receipt;try{receipt=await api('/state');}catch(_){queryFailure();return;}
        if(!current())return;
        const proof=Array.isArray(receipt?.jobs)?receipt.jobs.find(v=>v?.jobId===active.jobId):null;
        if(receipt?.epoch===token.epoch&&receipt.appVersion===state.appVersion&&receipt.bundleId===state.bundleId&&receipt.protocolVersion===state.protocolVersion&&terminal(proof)){
          if(guide())say('음성 샘플의 분석 내용이 변경됐습니다. 최신 화자 분석에서 다시 선택하세요.');cleanup();return;
        }
      }
      if(usable()){queryFailure();if(['AUTH_REQUIRED','SESSION_EXPIRED'].includes(e.code)){setConnection(false,'편집 연결 복구 중');credentials=null;connection.reset();retryAt=Date.now()+1000;toggle();}}return;
    }
    if(!owned())return;
    if(!value||value.jobId!==active.jobId||value.kind!=='example'||value.epoch!==token.jobEpoch||typeof value.drained!=='boolean'||!['running','canceling','completed','canceled','failed','interrupted'].includes(value.status)){queryFailure();return;}
    if(!terminal(value)){if(guide())say(value.status==='canceling'?'음성 샘플 작업을 중단하고 있습니다.':value.status==='running'?'단독 발화 샘플을 준비하고 있습니다.':'음성 샘플 작업의 실제 종료를 확인하고 있습니다.');return;}
    if(!usable()){cleanup();return;}
    if(value.status!=='completed'){if(guide())say(value.status==='interrupted'?'음성 샘플 작업이 이전 연결에서 중단됐습니다. 샘플을 다시 선택하세요.':'음성 샘플 작업을 중단했습니다. 샘플을 다시 선택하세요.');cleanup();return;}
    const sample=value.result,duration=sample?.durationSeconds;
    if(!sample||sample.analysisId!==active.analysisId||sample.revision!==active.revision||typeof sample.path!=='string'||!sample.path.trim()||!['string','number'].includes(typeof duration)||typeof duration==='string'&&!duration.trim()||!Number.isFinite(Number(duration))||Number(duration)<=0||Number(duration)>2147483){if(guide())say('음성 샘플 정보를 확인하지 못했습니다. 샘플을 다시 선택하세요.');cleanup();return;}
    let fresh;try{fresh=await ContentriumHost.snapshot();}catch(_){if(guide())say('음성 샘플의 타임라인을 확인하지 못했습니다. 샘플을 다시 선택하세요.');cleanup();return;}
    if(!usable()){cleanup();return;}
    if(fresh?.snapshot?.snapshotHash!==connected.snapshot.hostSnapshotHash){const show=guide();resetSequence();if(show)say('샘플 준비 중 타임라인이 변경됐습니다. 현재 시퀀스를 다시 읽어 주세요.');if(owned())finishPolledJob(active);return;}
    const generation=++previewGeneration;let playIssued=false;previewBusy++;toggle();
    try{
      if(!await ContentriumHost.ppro.SourceMonitor.openFilePath(sample.path))throw new Error('SAMPLE_OPEN_FAILED');
      if(!usable())return;
      playIssued=true;const playing=await ContentriumHost.ppro.SourceMonitor.play(1);
      if(!playing)throw new Error('SAMPLE_PLAY_FAILED');
      // An issued Adobe call cannot be revoked. Track and stop its owned playback
      // after completion, including when cancel/update arrived during the await.
      if(generation!==previewGeneration)return;
      previewPlaying=true;
      if(!usable()){await stopPreview(()=>generation===previewGeneration);return;}
      const guidance=guide();
      if(guidance)say('Premiere 소스 모니터에서 단독 발화를 재생합니다.');
      const revision=statusRevision;
      setTimeout(()=>{
        if(generation!==previewGeneration)return;
        stopPreview(()=>generation===previewGeneration).catch(()=>{if(generation===previewGeneration&&statusRevision===revision)say('음성 미리보기가 멈추지 않으면 프로젝트를 저장하고 Premiere를 정상 종료하세요.');});
      },Math.ceil(Number(duration)*1000)+250);
    }catch(_){
      // A rejected play may have reached Adobe. Keep playback non-idle until
      // the owned stop confirms completion, or leave it available for retry.
      if(playIssued&&generation===previewGeneration){previewPlaying=true;try{await stopPreview(()=>generation===previewGeneration);}catch(_){} }
      if(guide())say('음성 샘플 재생을 완료하지 못했습니다. 샘플을 다시 선택하세요.');
    }
    finally{previewBusy--;cleanup();toggle();}
  }finally{if(examplePoll===token)examplePoll=null;}
}
async function pollJob(){
  if(!job)return;
  if(job.kind==='sync'){await pollSyncJob(job);return;}
  if(job.kind==='example'){await pollExampleJob(job);return;}
  if(job.kind==='model-setup'){await pollModelJob(job);return;}
  const active=job,inputScope=active.kind==='input-probe'?{selection:projectSelection,rows:selectedRows.slice(),epoch:state?.epoch}:null,analysisScope=active.kind==='analysis'?{connection:connected,snapshotHash:connected?.snapshot.snapshotHash,hostSnapshotHash:connected?.snapshot.hostSnapshotHash,epoch:state?.epoch,mode,analysisState,revision:analysisState?.revision}:null;let value;
  try{value=await api('/jobs/'+active.jobId);}catch(e){
    if(inputScope&&!inputProbeCurrent(active,inputScope)||analysisScope&&!analysisResultCurrent(active,analysisScope)){finishPolledJob(active);return;}
    if(e.code==='VALIDATION_BUSY'){error(e);return;}
    if(!['SOURCE_CHANGED','SOURCE_REANALYSIS_REQUIRED','CANCELED','CONTINUATION_EXPIRED','VALIDATION_EXPIRED','VALIDATION_WORKER_EXITED','VALIDATION_STALE','VALIDATION_UNAVAILABLE','VALIDATION_SCOPE'].includes(e.code)&&!canceledJobs.has(active.jobId))throw e;
    if(job===active)job=null;
    if(active.kind==='analysis')clearAnalysis();
    if(active.kind==='sync')clearSyncResult();
    if(active.kind==='input-probe')inputCapability=null;
    if(active.kind==='model-setup')say(modelInstallResult(['CANCELED','UPDATE_IN_PROGRESS'].includes(e.code)?'canceled':'failed',e.code));else error(e);toggle();return;
  }
  if(inputScope&&!inputProbeCurrent(active,inputScope)||analysisScope&&!analysisResultCurrent(active,analysisScope)){finishPolledJob(active);return;}
  if(['running','canceling'].includes(value.status)){
    if(active.kind==='model-setup'&&value.status==='canceling')modelInstallResult('canceling');
    const labels={sync:'소스의 싱크를 분석하고 있습니다.',analysis:'로컬에서 화자를 분석하고 있습니다.',example:'단독 발화 샘플을 준비하고 있습니다.','model-setup':'화자 모델을 설치하고 있습니다.','input-probe':'선택 소스의 영상과 오디오를 확인하고 있습니다.'};
    say(value.status==='canceling'?'작업을 중단하고 있습니다.':labels[active.kind]||'작업 중');return;
  }
  try{
    if(canceledJobs.has(active.jobId)){say(active.kind==='model-setup'?modelInstallResult('canceled'):'작업을 중단했습니다.');return;}
    if(value.status!=='completed'){say(active.kind==='model-setup'?modelInstallResult(value.status==='canceled'?'canceled':'failed',value.error?.code):'작업 중단 · '+(value.error?.code||value.status));return;}
    if(analysisScope){if(!await analysisSnapshotCurrent(active,analysisScope)||!analysisResultCurrent(active,analysisScope))return;}
    else if(['sync','example'].includes(active.kind)){
      const fresh=await ContentriumHost.snapshot();
      if(!connected||active.snapshotHash!==connected.snapshot.snapshotHash||fresh.snapshot.snapshotHash!==connected.snapshot.hostSnapshotHash){resetSequence();throw new Error('분석 중 타임라인이 변경됐습니다. 현재 시퀀스를 다시 읽어 주세요.');}
    }
    if(active.kind==='analysis'){
      let next;
      try{next=await api('/analyses/register',{jobId:value.jobId,epoch:analysisScope.epoch});}catch(e){if(!analysisResultCurrent(active,analysisScope))return;error(e);throw e;}
      if(!analysisResultCurrent(active,analysisScope))return;
      if(!await analysisSnapshotCurrent(active,analysisScope)||!analysisResultCurrent(active,analysisScope))return;
      try{acceptAnalysis(next);}catch(e){error(e);throw e;}
      analysisJob=value.jobId;view.show('speakers');say('화자 분석이 끝났습니다. 목소리와 카메라를 확인하세요.');
    }else if(active.kind==='sync'){
      if(active.inputHash!==syncInputHash()){invalidateSyncResult();return;}
      syncJob=value.jobId;syncResult=value.result;syncResultInputHash=active.inputHash;
      const sources=new Map(connected.snapshot.sources.map(s=>[s.assetId,basename(s.canonicalPath)]));
      $('sync-result').textContent=Object.entries(syncResult.sources).map(([id,evidence])=>sources.get(id)+' · '+(evidence.status==='accepted'?(Number(syncResult.offsets[id]).toFixed(3)+'초'):'확인 필요 · '+(evidence.reason||evidence.code||evidence.status))).join('\n');
      say('싱크 분석 완료 · 확인이 필요한 소스를 검토하세요.');
    }else if(active.kind==='input-probe'){
      try{
        const next=await api('/input/capabilities/result',{jobId:value.jobId,epoch:inputScope.epoch});
        if(!inputProbeCurrent(active,inputScope))return;
        const mediaRows=inputScope.rows.map(row=>Array.isArray(next?.assets)?next.assets.find(media=>media.assetId===row.source.assetId):null);
        if(mediaRows.some(media=>!media))throw Object.assign(new Error('INPUT_SCOPE'),{code:'INPUT_SCOPE'});
        inputCapability=next;
        inputScope.rows.forEach((row,index)=>{const media=mediaRows[index];row.role.value=media.hasVideo?'camera':media.hasAudio?'audio':'exclude';row.audio.checked=!media.hasVideo&&media.hasAudio;row.info.textContent=media.hasVideo?(media.hasAudio?'영상 · 오디오':'영상만 있음'):(media.hasAudio?'오디오만 있음':'지원하는 스트림 없음');});
        say('선택 소스를 확인했습니다. 역할과 출력 오디오를 지정하세요.');
      }catch(e){if(!inputProbeCurrent(active,inputScope))return;error(e);throw e;}
    }else if(active.kind==='model-setup'){
      say(modelInstallResult('completed'));
    }
  }finally{finishPolledJob(active);}
}
function handler(id,fn){
  const serial=!['refresh','check-update','update','cancel','recover-update','recover-apply'].includes(id);
  $(id).onclick=async()=>{if(serial&&(cancelRequest||applyRecoveryRequest||updateRecoveryRequest||pending||initializing||initializationIncomplete))return;if(serial){pending=true;toggle();}try{await fn();}catch(e){error(e);}finally{if(serial)pending=false;toggle();}};
}
async function performNative(kind,body,native,receipt,beginPath='/apply/begin'){
  if(updateIntent||stopped||!state?.gateOpen)throw Object.assign(new Error('Update stop is active.'),{code:'UPDATE_IN_PROGRESS'});
  if(applying)throw new Error('현재 적용 작업이 끝난 뒤 다시 실행하세요.');
  const token={};nativePreparation=token;applying=true;let handedOff=false;toggle();
  try{return await afterEditingPreview(()=>{handedOff=true;return workflow.run({kind,body,beginPath,native,receipt});},token);}
  finally{
    if(nativePreparation===token){
      nativePreparation=null;applying=false;
      if(handedOff){batchRunning=false;try{localEditPending=!!await workflow.pending();}catch(_){localEditPending=true;}await refresh();}
      else toggle();
    }
  }
}
function savedReceipt(approved,result,source){return {planHash:approved.plan.planHash,sourceSnapshotHash:source.snapshotHash,resultSnapshotHash:result.snapshot.snapshotHash,resultSequenceRef:result.sequenceRef,sourceUnchanged:result.originalUnchanged,readback:{verified:result.audioAndOverlaysUnchanged===true||result.readbackVerified===true},saved:true};}
handler('refresh',async()=>{if(credentials&&!initializationIncomplete)await refresh();else await initialize({manual:true});});
handler('read-project',()=>readProject());
handler('analyze',async()=>{
  admitted();const issue=microphoneFeedback();if(issue)throw new Error(issue);
  await requireCurrent();return afterEditingPreview(async()=>{admitted();if(updateIntent)throw Object.assign(new Error('UPDATE_IN_PROGRESS'),{code:'UPDATE_IN_PROGRESS'});const currentIssue=microphoneFeedback();if(currentIssue)throw new Error(currentIssue);clearAnalysis();
  const options=microphoneOptions(),inputHash=ContentriumHost.hash(options),snapshotHash=connected.snapshot.snapshotHash;
  return submitEditingJob('/jobs',{kind:'analysis',options,epoch:state.epoch},{snapshotHash},'화자 분석을 시작합니다.',()=>ContentriumHost.hash(microphoneOptions())===inputHash);
  });
});
handler('sync',async()=>{
  admitted();const issue=syncFeedback();if(issue)throw new Error(issue);
  await requireCurrent();return afterEditingPreview(async()=>{clearSyncResult();
  const options=syncOptions(),inputHash=syncInputHash(options),snapshotHash=connected.snapshot.snapshotHash;
  return submitEditingJob('/jobs',{kind:'sync',options,epoch:state.epoch},{snapshotHash,inputHash},'싱크 분석을 시작합니다.',()=>syncInputHash()===inputHash);
  });
});
handler('plan',async()=>{
  await requireCurrent();requirePolicy();if(!analysisState)throw new Error('화자 분석을 먼저 실행하세요.');
  const inputs=planInputs(),inputHash=ContentriumHost.hash(inputs),currentMapping=inputs.mapping,snapshotHash=inputs.snapshotHash,epoch=inputs.epoch,revision=inputs.analysisRevision;
  const {snapshotHash:_snapshotHash,...request}=inputs;
  const next=await api('/plan',request);
  if(stopped||!state.gateOpen||state.epoch!==epoch||connected?.snapshot.snapshotHash!==snapshotHash||analysisState?.revision!==revision)throw new Error('PLAN_STALE');
  requirePolicy();
  if(inputHash!==planInputsHash())rejectStalePlan();
  plan=next;planInputHash=inputHash;planInvalidated=false;planCameraRefs=currentMapping.cameras.map(c=>c.cameraId);renderPlan();view.show('review');scheduleSettings();say(plan.reviews.length?'검토 사항 '+plan.reviews.length+'개를 확인하세요.':'편집안을 검토한 뒤 Premiere에 적용하세요.');
});
handler('apply',async()=>{
  await requireCurrent();requirePolicy();if(!plan){if(planInvalidated)rejectStalePlan();throw new Error('편집안을 먼저 만들어 주세요.');}
  const source=connected.snapshot,reviewed=plan,inputHash=planInputHash,refs=planCameraRefs.slice();requireReviewedPlan(reviewed,inputHash);
  const result=await performNative('edit',{planHash:reviewed.planHash,snapshotHash:source.snapshotHash,epoch:state.epoch},(approved,control)=>{requirePolicy();requireReviewedPlan(reviewed,inputHash);return ContentriumHost.apply(approved.plan,source,refs,control);},(approved,result)=>savedReceipt(approved,result,source));
  clearAnalysis();say('적용 완료 · '+result.sequenceName+' / 원본·오디오 보존 확인');
});
handler('apply-sync',async()=>{
  await requireCurrent();const issue=syncFeedback();if(issue)throw new Error(issue);if(!syncJob||!syncResult){if(syncInvalidated)rejectStaleSync();throw new Error('싱크 분석을 먼저 실행하세요.');}
  if(!syncResultMatches())rejectStaleSync();
  const source=connected.snapshot,reviewedJob=syncJob,inputHash=syncResultInputHash,assets=new Set(syncRows.filter(r=>r.check.checked).map(r=>r.source.assetId)),keys=source.clips.filter(c=>assets.has(c.assetId)).map(c=>c.instanceKey);
  const reviewed=await api('/sync-plan',{jobId:reviewedJob,selectedClipInstanceKeys:keys,epoch:state.epoch});
  await requireCurrent();if(syncJob!==reviewedJob||syncResultInputHash!==inputHash||!syncResultMatches())rejectStaleSync();
  const result=await performNative('sync',{planHash:reviewed.planHash,snapshotHash:source.snapshotHash,epoch:state.epoch},(approved,control)=>{
    if(syncJob!==reviewedJob||syncResultInputHash!==inputHash||!syncResultMatches())rejectStaleSync();
    return ContentriumHost.applySync(approved.plan,source,control);
  },(approved,result)=>savedReceipt(approved,result,source));
  clearAnalysis();clearSyncResult();say('싱크 적용 완료 · '+result.sequenceName+' / 원본 보존 확인');
});
function selectionReadScope(){
  // Compare service state values, not the fresh /state object's identity: a
  // normal periodic read must not starve a longer native selection query.
  return refreshScope().filter((_,index)=>index!==1).concat(JSON.stringify(selectedRows.map(r=>[r.role.value,r.audio.checked])));
}
function selectionReadReady(){return !initializing&&!initializationIncomplete&&!!credentials&&!!state?.gateOpen&&state.stopEpoch==null&&state.compatible!==false&&!stopped&&!updateIntent&&!job&&!applying&&!localEditPending&&!state.applyRecovery?.blocked&&!panelContextConflict&&!binding&&!projectRead&&!previewBusy&&!validationCount;}
function validSelectionJob(value,epoch){return !!value&&typeof value==='object'&&!Array.isArray(value)&&typeof value.jobId==='string'&&/^[A-Za-z0-9_-]{1,128}$/.test(value.jobId)&&value.kind==='input-probe'&&['running','completed'].includes(value.status)&&(value.epoch===undefined||value.epoch===epoch);}
async function readSelection(){
  if(!selectionReadReady())throw new Error('현재 작업 상태를 확인하세요.');
  const token={credential:credentials,epoch:state.epoch,scope:selectionReadScope(),rows:selectedRows.slice(),guidanceRevision:statusRevision};selectionRead=token;
  const current=()=>{if(selectionRead!==token||!selectionReadReady()||credentials!==token.credential)return false;const scope=selectionReadScope();return token.scope.every((value,index)=>value===scope[index])&&selectedRows.length===token.rows.length&&token.rows.every((row,index)=>selectedRows[index]===row);};
  const text=value=>typeof value==='string'&&value.trim().length>0;
  try{
    if(!await heartbeat(current)||!current())return;
    const selection=await ContentriumHost.selectedSources();if(!current())return;
    if(!selection||!text(selection.projectRef)||!Array.isArray(selection.sources)||!selection.sources.length||selection.sources.some(source=>!source||!text(source.assetId)||!text(source.name)||!text(source.path||source.canonicalPath))||new Set(selection.sources.map(source=>source.assetId)).size!==selection.sources.length)throw new Error('Invalid source selection');
    const bound=await api('/input/sources',{projectRef:selection.projectRef,sources:selection.sources,epoch:token.epoch});if(!current())return;
    if(!bound||!text(bound.selectionId))throw new Error('Invalid selection registration');
    const rows=[],nodes=[];
  for(const source of selection.sources){
    const row=element('div',undefined,'source-row'),role=element('select'),audio=checkbox(),info=element('p','스트림 확인 중','hint');
    options(role,[['camera','카메라 영상'],['audio','독립 오디오'],['exclude','제외']]);
    const selectionHint=element('p','', 'hint');selectionHint.id='selected-source-hint-'+rows.length;selectionHint.setAttribute('role','status');selectionHint.setAttribute('aria-live','polite');for(const field of [role,audio])field.setAttribute('aria-describedby',selectionHint.id);
    const value={source,role,audio,info,selectionHint};role.oninput=role.onchange=()=>{if(selectedSourceEditable(value))toggle();};audio.oninput=audio.onchange=()=>{if(selectedSourceEditable(value)&&selectedSourceMedia(value).hasAudio===true&&role.value!=='exclude')toggle();};
    row.appendChild(element('div',source.name,'source-title'));row.appendChild(info);row.appendChild(label('소스 역할',role));row.appendChild(label('이 파일의 오디오를 결과에 출력',audio));row.appendChild(selectionHint);rows.push(value);nodes.push(row);
  }
    const accepted=await api('/input/capabilities',{selectionId:bound.selectionId,epoch:token.epoch});
    if(!current()){
      // This response can arrive after stop was sent. Cancel only this owned
      // job in the original session; never authenticate cleanup as a new one.
      if(credentials===token.credential&&job?.jobId!==accepted?.jobId&&validSelectionJob(accepted,token.epoch)){
        canceledJobs.add(accepted.jobId);try{await api('/jobs/'+accepted.jobId+'/cancel',{});}catch(_){}
      }
      return;
    }
    if(!validSelectionJob(accepted,token.epoch))throw new Error('Invalid media probe receipt');
    projectSelection={...selection,selectionId:bound.selectionId};inputCapability=null;
    selectedRows.length=0;selectedRows.push(...rows);$('selected-sources').innerHTML='';for(const node of nodes)$('selected-sources').appendChild(node);
    job={...accepted,selectionId:bound.selectionId};
    if(statusRevision===token.guidanceRevision)say('선택한 소스의 미디어 구성을 확인합니다.');
  }catch(e){
    if(!current())return;
    const guide=statusRevision===token.guidanceRevision;
    if(e?.code==='PANEL_CONTEXT_CONFLICT'){contextConflict(guide);return;}
    if(['AUTH_REQUIRED','SESSION_EXPIRED'].includes(e?.code)){credentials=null;stopped=true;connection.reset();retryAt=Date.now()+1000;setConnection(false,'편집 연결 복구 중');}
    if(guide)say('선택 소스를 확인하지 못했습니다. 기존 목록을 보존했습니다. 소스를 다시 읽어 주세요.');
  }finally{if(selectionRead===token)selectionRead=null;}
}
handler('read-selection',readSelection);
handler('create-input',async()=>{
  if(!state?.gateOpen||stopped||!projectSelection||!inputCapability)throw new Error('선택 소스 확인을 먼저 마쳐 주세요.');
  const selection=projectSelection,capability=inputCapability,choices=inputSourceChoices();
  if(!await heartbeat()||stopped||updateIntent)return;
  await performNative('input',{capabilityId:capability.capabilityId,choices,epoch:state.epoch},(approved,control)=>ContentriumHost.createSelectedInput(selection,choices,control),(approved,result)=>({...result.inputReceipt,planHash:approved.planHash}),'/input/begin');
  projectSelection=inputCapability=null;selectedRows.length=0;$('selected-sources').innerHTML='';connected=null;await readProject();say('입력 시퀀스를 만들었습니다. 트랙 설정에서 싱크와 화자 분석을 시작하세요.');
});
handler('save-settings',()=>{admitted();return saveSettings();});
handler('load-settings',async()=>{admitted();return afterEditingPreview(loadSavedSettings);});
$('add-override').onclick=()=>editOverrides(()=>addOverride());
$('override-filter').onclick=()=>{if($('override-filter').disabled||!overrideRows.some(r=>r.error.textContent))return;overrideErrorsOnly=!overrideErrorsOnly;overrideVisibleRows.clear();view.show('cut');view.openDisclosure('disclosure-6');overrideFeedback();};
handler('undo-correction',()=>correct({type:'undo'}));
function cancelScope(){
  return [credentials,state?.epoch,state?.gateOpen,state?.stopEpoch,state?.compatible,state?.appVersion,state?.bundleId,state?.protocolVersion,JSON.stringify([state?.update?.updateState,state?.update?.updateEpoch,state?.maintenance]),stopRevision,updateIntent,JSON.stringify(updateIntent),panelContextConflict,mode,connected,connected?.snapshot.snapshotHash,connected?.snapshot.hostSnapshotHash,analysisState,analysisState?.revision,projectSelection,inputCapability];
}
function cancelCurrent(token){
  if(cancelRequest!==token||credentials!==token.credential||!Array.isArray(token.scope)||job&&(job!==token.job||job.jobId!==token.jobId))return false;
  const scope=cancelScope();return token.scope.every((value,index)=>value===scope[index]);
}
async function cancelWork(){
  if(cancelRequest)return;
  stopRevision++;
  if(job?.kind==='model-setup'||modelRequest)modelInstallResult('canceling');
  stopped=true;plan=null;
  const token={credential:credentials,job,jobId:job?.jobId,scope:cancelScope(),guidanceRevision:statusRevision};cancelRequest=token;toggle();
  say('작업 중단을 요청하고 있습니다.');token.guidanceRevision=statusRevision;
  const current=()=>cancelCurrent(token),guide=()=>statusRevision===token.guidanceRevision;
  // Submit every owned stop before awaiting Adobe or a service response.
  const submit=fn=>{try{return Promise.resolve(fn());}catch(e){return Promise.reject(e);}};
  const cancellations=[submit(()=>connection.cancelPending())];
  if(token.job){canceledJobs.add(token.jobId);cancellations.push(submit(()=>api('/jobs/'+token.jobId+'/cancel',{})));}
  cancellations.push(submit(()=>stopPreview(current)));
  try{
    const outcomes=await Promise.allSettled(cancellations);if(!current())return;
    const failures=outcomes.filter(value=>value.status==='rejected').map(value=>value.reason);
    const failure=failures.find(e=>e?.code==='PANEL_CONTEXT_CONFLICT')||failures.find(e=>['AUTH_REQUIRED','SESSION_EXPIRED'].includes(e?.code))||failures[0];
    if(failures.length){
      if(failure?.code==='PANEL_CONTEXT_CONFLICT'){contextConflict(guide());return;}
      const auth=['AUTH_REQUIRED','SESSION_EXPIRED'].includes(failure?.code);
      if(auth){credentials=null;stopped=true;connection.reset();retryAt=Date.now()+1000;setConnection(false,'편집 연결 복구 중');}
      if(guide())say(auth?'편집 연결을 복구하고 있습니다. 새로고침으로 중단 상태를 확인하세요.':'중단 요청 일부를 확인하지 못했습니다. 작업·재생 상태를 다시 확인하세요.');
      return;
    }
    // A pending native batch remains owned by its workflow, even after receipts.
    if(guide())say('중단 요청 · 진행 중인 트랜잭션 뒤 추가 편집을 멈춥니다.');
  }finally{if(cancelRequest===token){cancelRequest=null;toggle();}}
}
handler('cancel',cancelWork);
function applyRecoveryReady(){return !!credentials&&!initializing&&!initializationIncomplete&&!cancelRequest&&!applyRecoveryRequest&&!updateRecoveryRequest&&!pending&&!applying&&!batchRunning&&!job&&!validationCount&&!previewPlaying&&!previewBusy&&!updateIntent&&!!(localEditPending||state?.applyRecovery?.blocked);}
async function recoverApply(){
  if(!applyRecoveryReady())return;
  const token={credential:credentials,scope:null,guidanceRevision:statusRevision};applyRecoveryRequest=token;toggle();
  token.scope=updateCheckScope();say('중단 작업 기록을 확인하고 있습니다.');token.guidanceRevision=statusRevision;
  const current=()=>{if(applyRecoveryRequest!==token||credentials!==token.credential)return false;const scope=updateCheckScope();return token.scope.every((value,index)=>value===scope[index]);};
  const guide=()=>statusRevision===token.guidanceRevision;
  try{
    await workflow.recover({current});if(!current())return;
    localEditPending=false;localIntentError=null;token.scope=updateCheckScope();
    if(!await refresh(current,()=>{token.scope=updateCheckScope();},guide)||!current())return;
    if(guide())say(state.applyRecovery?.blocked?'중단 작업 복구가 더 필요합니다. 결과 시퀀스와 작업 기록을 다시 확인하세요.':'중단 작업 기록을 확인했습니다. 결과 시퀀스를 검토한 뒤 새 작업을 시작하세요.');
  }catch(e){
    if(!current())return;
    if(e?.code==='PANEL_CONTEXT_CONFLICT'){contextConflict(guide());return;}
    if(['AUTH_REQUIRED','SESSION_EXPIRED'].includes(e?.code)){credentials=null;stopped=true;connection.reset();retryAt=Date.now()+1000;setConnection(false,'편집 연결 복구 중');}
    if(guide())say(['AUTH_REQUIRED','SESSION_EXPIRED'].includes(e?.code)?'편집 연결을 복구하고 있습니다. 새로고침으로 상태를 확인하세요.':'중단 작업 기록을 확인하지 못했습니다. 상태를 다시 확인한 뒤 재시도하세요.');
  }finally{if(applyRecoveryRequest===token){applyRecoveryRequest=null;toggle();}}
}
handler('recover-apply',recoverApply);
function updateCheckScope(){
  return [credentials,state?.epoch,state?.gateOpen,state?.stopEpoch,state?.compatible,state?.appVersion,state?.bundleId,state?.protocolVersion,JSON.stringify([state?.models,state?.update?.updateState,state?.update?.updateEpoch,state?.applyRecovery,state?.maintenance]),stopRevision,stopped,updateIntent,JSON.stringify(updateIntent),panelContextConflict,mode,connected,connected?.snapshot.snapshotHash,connected?.snapshot.hostSnapshotHash,analysisState,analysisState?.revision,plan,planInputHash,job,applying,batchRunning,previewPlaying,previewBusy,localEditPending,localIntentError,binding,projectRead,projectSelection,inputCapability,validationRevision,validationCount];
}
async function checkUpdate(){
  if(!credentials||initializing||initializationIncomplete||applyRecoveryRequest||updateRecoveryRequest||updateCheckRequest||updateIntent||state?.update?.checkState==='CHECKING')return;
  const token={credential:credentials,scope:updateCheckScope(),guidanceRevision:statusRevision};updateCheckRequest=token;toggle();
  const current=()=>{if(updateCheckRequest!==token||credentials!==token.credential)return false;const scope=updateCheckScope();return token.scope.every((value,index)=>value===scope[index]);};
  try{
    await api('/updates/check',{});if(!current())return;
    await refresh(current,()=>{token.scope=updateCheckScope();},()=>statusRevision===token.guidanceRevision);
  }catch(e){
    if(!current())return;
    const guide=statusRevision===token.guidanceRevision;
    if(e?.code==='PANEL_CONTEXT_CONFLICT'){contextConflict(guide);return;}
    if(['AUTH_REQUIRED','SESSION_EXPIRED'].includes(e?.code)){credentials=null;stopped=true;connection.reset();retryAt=Date.now()+1000;setConnection(false,'편집 연결 복구 중');}
    if(guide)say(['AUTH_REQUIRED','SESSION_EXPIRED'].includes(e?.code)?'편집 연결을 복구하고 있습니다. 새로고침으로 상태를 확인하세요.':'업데이트를 확인하지 못했습니다. 잠시 후 다시 확인하세요.');
  }finally{if(updateCheckRequest===token){updateCheckRequest=null;toggle();}}
}
handler('check-update',checkUpdate);
function updateStartScope(){
  // Update gate/epoch/phase and native drain can change because of this start.
  // Capture identity, not those expected server progress values.
  return [state?.appVersion,state?.bundleId,state?.protocolVersion,state?.compatible,
    panelContextConflict,mode,connected,connected?.snapshot.snapshotHash,connected?.snapshot.hostSnapshotHash];
}
async function startUpdate(){
  if(updateIntent?.inFlight||updateIntent?.accepted)return;
  const candidate=state?.update?.candidate;if(!candidate&&!updateIntent)throw new Error('업데이트를 다시 확인하세요.');
  if(!updateIntent)updateIntent={candidateId:candidate.candidateId,manifestDigest:candidate.manifestDigest,requestId:requestId(),epoch:state.epoch};
  const intent=updateIntent;intent.inFlight=true;stopRevision++;
  stopped=true;plan=null;if(job)canceledJobs.add(job.jobId);
  const token={credential:credentials,intent,revision:stopRevision,scope:updateStartScope(),identity:JSON.stringify([intent.candidateId,intent.manifestDigest,intent.requestId,intent.epoch])};updateStartRequest=token;toggle();
  if(job?.kind==='model-setup'||modelRequest)modelInstallResult('canceling');
  say('Contentrium CUT 작업을 중단하고 업데이트를 시작합니다.');
  token.guidanceRevision=statusRevision;
  const current=()=>{if(updateStartRequest!==token||credentials!==token.credential||updateIntent!==intent||stopRevision!==token.revision||token.identity!==JSON.stringify([intent.candidateId,intent.manifestDigest,intent.requestId,intent.epoch]))return false;const scope=updateStartScope();return token.scope.every((value,index)=>value===scope[index]);};
  const guide=()=>statusRevision===token.guidanceRevision;
  // Submit the global stop before waiting for any Adobe playback response.
  const previewStopGeneration=previewGeneration;
  const start=api('/updates/start',{candidateId:intent.candidateId,manifestDigest:intent.manifestDigest,requestId:intent.requestId});
  stopPreview().catch(()=>{if(current()&&guide()&&previewGeneration===previewStopGeneration){say('업데이트를 시작했습니다. 미리보기가 멈추지 않으면 프로젝트를 저장하고 Premiere를 정상 종료하세요.');token.guidanceRevision=statusRevision;}});
  try{
    await start;if(!current())return;
    intent.accepted=true;await refresh(current,()=>{},guide);
  }catch(e){
    if(!current())return;
    if(e?.code==='PANEL_CONTEXT_CONFLICT'){contextConflict(guide());return;}
    if(['AUTH_REQUIRED','SESSION_EXPIRED'].includes(e?.code)){credentials=null;stopped=true;connection.reset();retryAt=Date.now()+1000;setConnection(false,'편집 연결 복구 중');}
    if(e?.code==='UPDATE_CANDIDATE')updateIntent=null;
    if(guide())say(e?.code==='UPDATE_CANDIDATE'?'업데이트 후보를 다시 확인하세요.': ['AUTH_REQUIRED','SESSION_EXPIRED'].includes(e?.code)?'편집 연결을 복구하고 있습니다. 새로고침으로 업데이트 상태를 확인하세요.':'업데이트 시작 결과를 확인하지 못했습니다. 상태를 새로고침하거나 같은 업데이트를 다시 요청하세요.');
  }finally{
    // Release only the completed physical request. A stale result cannot mark
    // acceptance or clear a replacement intent; unknown outcomes keep the ID.
    if(updateStartRequest===token){updateStartRequest=null;if(updateIntent===intent)intent.inFlight=false;toggle();}
  }
}
handler('update',startUpdate);
function updateRecoveryReady(){return !!credentials&&!initializing&&!initializationIncomplete&&!cancelRequest&&!applyRecoveryRequest&&!updateRecoveryRequest&&!pending&&!applying&&!batchRunning&&!job&&!validationCount&&!previewPlaying&&!previewBusy&&!updateIntent?.inFlight&&['RECOVERY_REQUIRED','FAILED'].includes(state?.update?.updateState);}
async function recoverUpdate(){
  if(!updateRecoveryReady())return;
  const token={credential:credentials,scope:null,guidanceRevision:statusRevision};updateRecoveryRequest=token;toggle();
  token.scope=updateCheckScope();say('설치 복구 상태를 확인하고 있습니다.');token.guidanceRevision=statusRevision;
  const current=()=>{if(updateRecoveryRequest!==token||credentials!==token.credential)return false;const scope=updateCheckScope();return token.scope.every((value,index)=>value===scope[index]);};
  const guide=()=>statusRevision===token.guidanceRevision;
  try{
    await api('/updates/recover',{});if(!current())return;
    if(!await refresh(current,()=>{token.scope=updateCheckScope();},guide)||!current())return;
    if(guide())say(['RECOVERY_REQUIRED','FAILED'].includes(state.update.updateState)?'설치 복구가 더 필요합니다. 업데이트 상태와 설치 프로그램의 안내를 확인하세요.':'설치 복구 상태를 확인했습니다. 현재 버전과 연결 상태를 확인하세요.');
  }catch(e){
    if(!current())return;
    if(e?.code==='PANEL_CONTEXT_CONFLICT'){contextConflict(guide());return;}
    if(['AUTH_REQUIRED','SESSION_EXPIRED'].includes(e?.code)){credentials=null;stopped=true;connection.reset();retryAt=Date.now()+1000;setConnection(false,'편집 연결 복구 중');}
    if(guide())say(['AUTH_REQUIRED','SESSION_EXPIRED'].includes(e?.code)?'편집 연결을 복구하고 있습니다. 새로고침으로 상태를 확인하세요.':'설치 복구를 확인하지 못했습니다. 설치 상태를 다시 확인한 뒤 재시도하세요.');
  }finally{if(updateRecoveryRequest===token){updateRecoveryRequest=null;toggle();}}
}
handler('recover-update',recoverUpdate);
handler('open-model-provider',()=>uxp.shell.openExternal('https://huggingface.co/pyannote/speaker-diarization-community-1','화자 모델 제공자의 이용 조건과 접근 권한을 확인합니다.'));
function modelValidation(count,descriptors){
  const token=modelRequest;if(!token)return false;
  const own=!token.invalid&&token.phase==='revision'&&!token.validationFinished&&count===1&&Array.isArray(descriptors)&&descriptors.length===1&&descriptors[0].path==='/models/community-1/revision'&&descriptors[0].epoch===token.epoch&&typeof descriptors[0].id==='string'&&/^[a-f0-9]{32}$/.test(descriptors[0].id)&&(!token.id||token.id===descriptors[0].id);
  if(own){token.id=descriptors[0].id;token.validationActive=true;token.validationRevision=validationRevision;return true;}
  if(!count&&token.validationActive&&!token.invalid){token.validationActive=false;token.validationFinished=true;token.validationRevision=validationRevision;return false;}
  token.invalid=true;return false;
}
function modelReady(){return resourceSettingsReady(true)&&!state.maintenance&&state.appVersion===bundle.appVersion&&state.bundleId===bundle.bundleId&&state.protocolVersion===bundle.protocolVersion;}
function modelResponseGuard(token){
  const scope=[...cacheScope(),state.gateOpen,stopped,plan,planInputHash,modelInputRevision,cacheRequest],rows=selectedRows.slice(),raw=JSON.stringify([$('model-token').value,$('model-terms').checked,$('analysis-device').value,$('cache-budget').value]);
  return ()=>{
    if(modelRequest!==token||token.invalid||!credentials||!state?.gateOpen||stopped||updateIntent||panelContextConflict||state.stopEpoch!=null||state.compatible===false||state.maintenance||validationRevision!==token.validationRevision||validationCount!==(token.validationActive?1:0)||$('model-install-status').textContent!==token.modelStatus)return false;
    const live=[...cacheScope(),state.gateOpen,stopped,plan,planInputHash,modelInputRevision,cacheRequest];return scope.every((value,index)=>value===live[index])&&rows.length===selectedRows.length&&rows.every((row,index)=>row===selectedRows[index])&&raw===JSON.stringify([$('model-token').value,$('model-terms').checked,$('analysis-device').value,$('cache-budget').value]);
  };
}
async function runModelInstall(){
  if(!modelReady())return;
  const access=$('model-token').value.trim(),termsAccepted=$('model-terms').checked;$('model-token').value='';
  if(!access||!termsAccepted){const text='접근 토큰을 입력하고 제공자 이용 조건 동의를 확인한 뒤 다시 설치하세요.';$('model-install-status').textContent=text;say(text);return;}
  const token={epoch:state.epoch,phase:'revision',id:null,validationActive:false,validationFinished:false,validationRevision,guidanceRevision:statusRevision,modelStatus:'모델 리비전을 확인하고 있습니다.',invalid:false};modelRequest=token;$('model-install-status').textContent=token.modelStatus;toggle();const current=modelResponseGuard(token);
  try{
    const revision=await api('/models/community-1/revision',{token:access,termsAccepted,epoch:token.epoch});if(!current()||token.validationActive)return;
    if(typeof revision?.revision!=='string'||!/^[a-fA-F0-9]{40}$/.test(revision.revision))throw Object.assign(new Error('MODEL_NOT_READY'),{code:'MODEL_NOT_READY'});
    token.phase='install';const next=await api('/models/community-1/install',{token:access,termsAccepted,revision:revision.revision,epoch:token.epoch});if(!current())return;
    if(typeof next?.jobId!=='string'||!/^[a-zA-Z0-9_-]{1,128}$/.test(next.jobId)||next.kind!=='model-setup'||next.status!=='running'||Object.prototype.hasOwnProperty.call(next,'epoch')&&next.epoch!==token.epoch)throw Object.assign(new Error('MODEL_NOT_READY'),{code:'MODEL_NOT_READY'});
    job={...next,epoch:token.epoch};$('model-install-status').textContent='로컬 모델 설치 중';
  }catch(e){if(current()){const canceled=['CANCELED','UPDATE_IN_PROGRESS'].includes(e.code),text=modelInstallResult(canceled?'canceled':'failed',e.code);if(statusRevision===token.guidanceRevision)say(text);}}
  finally{if(modelRequest===token)modelRequest=null;}
}
handler('install-model',()=>runModelInstall());
for(const id of ['model-token','model-terms'])$(id).oninput=$(id).onchange=()=>{if(workLocked()||!modelReady())return;modelInputRevision++;};

function cacheBudgetInput(){
  const raw=$('cache-budget').value.trim(),bytes=raw?Math.round(Number(raw)*1073741824):null;
  return {bytes,error:raw&&(!Number.isSafeInteger(bytes)||bytes<=0)?'캐시 예산을 양수로 입력하세요. 저장 가능한 바이트 범위의 값을 사용하세요.':''};
}
function cacheBudgetFeedback(){
  const issue=cacheBudgetInput().error,node=$('cache-budget-error');$('cache-budget').setAttribute('aria-invalid',issue?'true':'false');
  if(node.textContent!==issue)node.textContent=issue;node.className='hint input-error'+(issue?'':' hidden');return issue;
}
function resourceSettingsReady(save=false){
  return !!credentials&&!!state&&!updateIntent&&!panelContextConflict&&state.compatible!==false&&state.stopEpoch==null&&(!save||state.gateOpen&&!stopped&&!binding&&!projectRead&&!job&&!validationCount&&!applying&&!batchRunning&&!localEditPending&&!state.applyRecovery?.blocked);
}
function resourceScope(){return [credentials,connected,connected?.snapshot.snapshotHash,connected?.snapshot.hostSnapshotHash,mode,analysisState,analysisState?.revision,state?.epoch,state?.gateOpen,stopped,state?.compatible,state?.appVersion,state?.bundleId,state?.protocolVersion,localEditPending,state?.applyRecovery?.blocked,binding,projectRead,job,validationCount,validationRevision,applying,batchRunning,projectSelection,inputCapability,plan,planInputHash,syncResult,syncJob,syncResultInputHash,resourceInputRevision,resourceViewRevision,resourceInputDirty,stopRevision];}
function resourceResponseGuard(token,save=false){
  const scope=resourceScope(),rows=selectedRows.slice(),raw=JSON.stringify([$('analysis-device').value,$('cache-budget').value]);
  return ()=>{if(resourceRequest!==token||!resourceSettingsReady(save))return false;const live=resourceScope();return scope.every((value,index)=>value===live[index])&&selectedRows.length===rows.length&&rows.every((row,index)=>row===selectedRows[index])&&JSON.stringify([$('analysis-device').value,$('cache-budget').value])===raw;};
}
async function loadResources(current=()=>true,accepted=()=>{}){
  if(!current())return false;const value=await api('/resources');if(!current())return false;
  const device=value.settings.device,budget=value.settings.cacheBudgetBytes,budgetText=Number.isSafeInteger(budget)&&budget>0?String(budget/1073741824):'',cacheInfo='캐시 '+(value.status.cacheBytes/1073741824).toFixed(2)+' GB · 사용 가능 디스크 '+(value.status.freeDiskBytes/1073741824).toFixed(1)+' GB';
  $('analysis-device').value=device;$('cache-budget').value=budgetText;$('cache-info').textContent=cacheInfo;resourceLoaded=true;resourceInputDirty=false;accepted();return true;
}
async function runResourceSettings(save=false,discard=false){
  const editing=save||discard;if(!resourceSettingsReady(editing)||discard&&(!resourceInputDirty||resourceRequest||previewBusy>0))return;
  const token={save,discard,phase:discard?'checking':'saving'};resourceRequest=token;let current=resourceResponseGuard(token,editing);
  if(save)say('분석 자원 설정을 저장하고 있습니다.');else if(discard)say('저장된 분석 자원 설정을 다시 확인하고 있습니다.');let guidanceRevision=statusRevision;toggle();
  try{
    if(save){
      const settings={device:$('analysis-device').value},budget=cacheBudgetInput();
      if(budget.error)throw new Error(budget.error);if(budget.bytes!==null)settings.cacheBudgetBytes=budget.bytes;
      await api('/resources',{settings,epoch:state.epoch});if(!current())return;
      token.phase='checking';if(statusRevision===guidanceRevision){say('저장된 분석 자원 설정과 캐시 상태를 확인하고 있습니다.');guidanceRevision=statusRevision;}toggle();
    }
    if(!await loadResources(current,()=>{current=resourceResponseGuard(token,editing);})||!current())return;
    if(save&&statusRevision===guidanceRevision)say('분석 자원 설정을 저장했습니다.');
    else if(discard&&statusRevision===guidanceRevision)say('저장된 분석 자원 설정으로 되돌렸습니다.');
  }catch(e){if(current()&&statusRevision===guidanceRevision)error(e);}
  finally{if(resourceRequest===token){resourceRequest=null;toggle();}}
}
handler('save-resources',()=>runResourceSettings(true));
handler('discard-resources',()=>runResourceSettings(false,true));
function cacheReady(release=false){
  return !!credentials&&!!state&&!updateIntent&&!panelContextConflict&&state.compatible!==false&&state.stopEpoch==null&&!binding&&!projectRead&&!job&&!validationCount&&!applying&&!batchRunning&&!localEditPending&&!state.applyRecovery?.blocked&&(release?state.maintenance?.canRelease===true&&state.maintenance.drained===true&&!state.gateOpen:state.gateOpen&&!stopped&&!state.maintenance&&!resourceInputDirty);
}
function cacheValidation(count,descriptors){
  const token=cacheRequest;if(!token)return false;
  const own=!token.release&&!token.invalid&&count===1&&Array.isArray(descriptors)&&descriptors.length===1&&descriptors[0].path==='/resources/prune'&&descriptors[0].epoch===token.epoch&&typeof descriptors[0].id==='string'&&/^[a-f0-9]{32}$/.test(descriptors[0].id)&&!token.validationFinished&&(!token.id||token.id===descriptors[0].id);
  if(own){token.id=descriptors[0].id;token.validationActive=true;token.validationRevision=validationRevision;return true;}
  if(!count&&token.validationActive&&!token.invalid){token.validationActive=false;token.validationFinished=true;token.validationRevision=validationRevision;return false;}
  token.invalid=true;return false;
}
function cacheScope(){return [credentials,connected,connected?.snapshot.snapshotHash,connected?.snapshot.hostSnapshotHash,mode,analysisState,analysisState?.revision,state?.epoch,state?.compatible,state?.appVersion,state?.bundleId,state?.protocolVersion,localEditPending,state?.applyRecovery?.blocked,binding,projectRead,job,applying,batchRunning,projectSelection,inputCapability,syncResult,syncJob,syncResultInputHash,resourceInputRevision,resourceViewRevision,resourceInputDirty,stopRevision,resourceRequest,planInvalidated,JSON.stringify(state?.update),JSON.stringify(state?.models),JSON.stringify(state?.applyRecovery)];}
function cacheResponseGuard(token){
  const scope=cacheScope(),rows=selectedRows.slice(),raw=JSON.stringify([$('analysis-device').value,$('cache-budget').value]);
  return receipt=>{
    if(cacheRequest!==token||token.invalid||!credentials||!state||updateIntent||panelContextConflict||state.stopEpoch!=null||state.compatible===false||validationRevision!==token.validationRevision||validationCount!==(token.validationActive?1:0))return false;
    const live=cacheScope();if(!scope.every((value,index)=>value===live[index])||rows.length!==selectedRows.length||!rows.every((row,index)=>row===selectedRows[index])||raw!==JSON.stringify([$('analysis-device').value,$('cache-budget').value]))return false;
    if(plan!==token.plan&&!(token.id&&!token.release&&plan===null)||planInputHash!==token.planInputHash)return false;
    const allowed=value=>!!value&&value.epoch===token.epoch&&value.stopEpoch==null&&value.compatible!==false&&value.appVersion===state.appVersion&&value.bundleId===state.bundleId&&value.protocolVersion===state.protocolVersion&&!value.applyRecovery?.blocked&&JSON.stringify(value.update)===JSON.stringify(state.update)&&JSON.stringify(value.models)===JSON.stringify(state.models)&&JSON.stringify(value.applyRecovery)===JSON.stringify(state.applyRecovery)&&(!value.maintenance?value.gateOpen:value.maintenance.id===token.id&&!value.gateOpen);
    if(!allowed(state)||receipt&&!allowed(receipt))return false;
    return token.release||!stopped||!!token.id;
  };
}
async function runCache(release=false){
  if(!cacheReady(release))return;
  const token={release,phase:'requesting',epoch:state.epoch,id:release?state.maintenance.id:null,plan,planInputHash,validationRevision,guidanceRevision:statusRevision,validationActive:false,validationFinished:false,invalid:false};cacheRequest=token;let current=cacheResponseGuard(token);
  say(release?'캐시 정리 종료를 요청하고 있습니다.':'완료된 분석 캐시를 정리하고 있습니다.');token.guidanceRevision=statusRevision;toggle();
  try{
    const value=await api('/resources/prune',release?{epoch:token.epoch,action:'release'}:{epoch:token.epoch});if(!current())return;
    let message;
    if(release){
      if(value?.released!==true)throw new Error('캐시 정리 종료 응답을 확인하지 못했습니다.');
      token.phase='checking';if(statusRevision===token.guidanceRevision){say('편집 상태를 확인하고 있습니다.');token.guidanceRevision=statusRevision;}toggle();
      if(!await refresh(current,()=>{current=cacheResponseGuard(token);},()=>statusRevision===token.guidanceRevision)||!current())return;
      message='정리를 종료했습니다. 편집을 계속할 수 있습니다.';
    }else{
      if(!Array.isArray(value?.removed)||typeof value.budgetMet!=='boolean')throw new Error('캐시 정리 결과를 확인하지 못했습니다.');
      token.phase='checking';if(statusRevision===token.guidanceRevision){say('정리 결과와 캐시 상태를 확인하고 있습니다.');token.guidanceRevision=statusRevision;}toggle();
      message='완료된 캐시 '+value.removed.length+'개 정리'+(value.budgetMet?'':' · 보존해야 하는 데이터가 있어 예산을 초과합니다.');
      if(!await loadResources(current,()=>{current=cacheResponseGuard(token);})||!current())return;
    }
    if(statusRevision===token.guidanceRevision)say(message);
  }catch(e){if(current()&&statusRevision===token.guidanceRevision)error(e);}
  finally{if(cacheRequest===token){cacheRequest=null;toggle();}}
}
handler('prune-cache',()=>runCache());
handler('release-cache',()=>runCache(true));
const showSettings=$('open-settings').onclick;
$('open-settings').onclick=()=>{showSettings();if(view.current()==='settings'&&!resourceLoaded&&!resourceInputDirty)return runResourceSettings();};
for(const id of ['analysis-device','cache-budget'])$(id).oninput=$(id).onchange=()=>{if(workLocked())return;resourceInputRevision++;resourceInputDirty=true;toggle();};
function changeRecordingMode(value){
  if(workLocked()||mode===value)return;
  const defaults=!microphoneSelectionCustomized&&microphoneRows.every(r=>r.check.checked===r.defaultChecked[mode]);
  mode=value;for(const other of ['separate','mixed'])$('mode-'+other).className='mode'+(mode===other?' active':'');
  clearAnalysis();clearMixedMappings();
  if(defaults)for(const row of microphoneRows)row.check.checked=row.defaultChecked[mode];else if(microphoneRows.length)microphoneSelectionCustomized=true;
  if(connected){renderSpeakers(mode==='mixed'?[]:[...new Set(microphoneRows.filter(r=>r.check.checked).map(r=>r.speaker.value.trim()))]);mappingInputs();}
  scheduleSettings();toggle();say('녹음 방식을 변경했습니다. 트랙 설정을 확인하고 새로 분석하세요.');return true;
}
for(const value of ['separate','mixed'])$('mode-'+value).onclick=async()=>{let changing=false;try{if(!changeRecordingMode(value))return;changing=pending=true;toggle();await refresh();}catch(e){error(e);toggle();}finally{if(changing){pending=false;toggle();}}};
for(const id of ['range-start','range-end'])$(id).oninput=$(id).onchange=()=>{if(workLocked()||!connected)return;rangeDirty=true;invalidateAnalysis();say(rangeIssue||'분석할 범위를 변경했습니다. 다음 분석에 적용됩니다.');};
for(const id of ['min-shot','short-turn','overlap'])$(id).oninput=$(id).onchange=policyInputsChanged;
for(const id of ['start-camera','reserve-camera'])$(id).onchange=()=>{if(!workLocked()&&connected)invalidatePlan();};
for(const id of ['speaker-count','vad-threshold'])$(id).oninput=$(id).onchange=()=>analysisOptionChanged(id);
$('sync-method').onchange=syncMethodEdited;
$('review-camera').onchange=$('review-search').oninput=()=>{reviewPage=0;renderReviewCuts();};
$('review-previous').onclick=()=>{if(plan&&reviewWindow?.previous){reviewPage--;renderReviewCuts();}};
$('review-next').onclick=()=>{if(plan&&reviewWindow?.next){reviewPage++;renderReviewCuts();}};
$('review-clear').onclick=()=>{$('review-camera').value='';$('review-search').value='';reviewPage=0;renderReviewCuts();};
$('sync-reference').onchange=syncInputEdited;
$('version').textContent=bundle.appVersion;
$('header-version').textContent=bundle.appVersion;
$('update-banner-button').onclick=()=>$('update').onclick();
$('update-later').onclick=()=>{dismissedCandidate=state?.update?.candidate?.candidateId;$('update-banner').className='hidden';};
let initializing=false,initializationIncomplete=false,initializationRequest=null,retryAt=0,retryDelay=1000,sequencePollAt=0,enrollmentDeadline=0;
function initializationScope(){return refreshScope().filter((_,index)=>index!==1).concat(initializationIncomplete);}
function sameInitializationScope(scope){const now=initializationScope();return scope.every((value,index)=>value===now[index]);}
async function initialize({manual=false}={}){
  if(initializing||panelContextConflict&&!manual)return false;
  initializing=true;initializationIncomplete=true;
  const token={credential:credentials,stop:stopRevision,update:updateIntent,updateText:JSON.stringify(updateIntent),scope:initializationScope(),guidanceRevision:statusRevision};initializationRequest=token;
  const alive=()=>initializationRequest===token&&credentials===token.credential&&stopRevision===token.stop&&updateIntent===token.update&&JSON.stringify(updateIntent)===token.updateText;
  const current=()=>alive()&&sameInitializationScope(token.scope);
  const accepted=()=>{token.scope=initializationScope();};
  const guide=()=>statusRevision===token.guidanceRevision;
  toggle();if(manual)enrollmentDeadline=0;
  try{
    await connection.connect();if(!current())return false;
    credentials={};token.credential=credentials;panelContextConflict=false;retryDelay=1000;enrollmentDeadline=0;accepted();
    try{
      const intent=await workflow.pending();if(!current())return false;
      if(intent!==null&&(!intent||typeof intent!=='object'||Array.isArray(intent)||intent.schemaVersion!==1||typeof intent.requestId!=='string'||!intent.requestId||!['edit','sync','input'].includes(intent.kind)))throw Object.assign(new Error('Invalid persisted intent'),{code:'EDIT_INTENT_CORRUPT'});
      localEditPending=intent!==null;localIntentError=null;
    }catch(e){if(!current())return false;if(!isIntentReadError(e))throw e;localEditPending=true;localIntentError=e.code;}
    accepted();
    if(!await refresh(alive)||!alive())return false;accepted();
    if(!await heartbeat(current)||!current())return false;
    if(localIntentError&&guide()){error({code:localIntentError});token.guidanceRevision=statusRevision;}
    if(!localEditPending&&!state?.applyRecovery?.blocked&&!stopped&&!updateIntent&&state?.gateOpen&&state.compatible!==false){
      try{
        const receipt=await readProject({guardFactory:()=>{const scope=initializationScope();return ()=>alive()&&sameInitializationScope(scope);},onSettledRead:()=>{if(alive())accepted();},guidanceCurrent:guide});
        if(!alive()||!receipt)return false;accepted();
      }catch(e){
        if(!current())return false;
        if(guide())say(/PROJECT_REQUIRED|SEQUENCE_REQUIRED/.test(String(e?.code||e))?'Premiere에서 편집할 시퀀스를 열어 주세요.':'시퀀스를 확인하지 못했습니다. 새로고침으로 다시 확인하세요.');
      }
    }
    if(!current())return false;
    // Local readiness is complete. A network update check must not hold the
    // editing controls, but its late response still belongs to this owner.
    initializationIncomplete=false;initializing=false;accepted();toggle();
    if(updateIntent||!state?.gateOpen)return true;
    try{await api('/updates/check',{});if(!current())return false;return await refresh(alive);}catch(_){return false;}
  }catch(e){
    if(!current())return false;
    if(e.code==='PANEL_CONTEXT_CONFLICT'){contextConflict(guide());return false;}
    credentials=null;retryAt=Date.now()+retryDelay;retryDelay=Math.min(30000,retryDelay*2);
    // Retry safety is independent of who owns the visible guidance.
    if(e.code==='INSTALLATION_PENDING'){
      if(!enrollmentDeadline)enrollmentDeadline=Date.now()+600000;
      if(Date.now()>=enrollmentDeadline)retryAt=Infinity;
    }else if(['BOOTSTRAP_INVALID','INSTALLATION_ENROLLMENT_INVALID'].includes(e.code))retryAt=Infinity;
    if(!guide())return false;
    setConnection(false,'편집 준비 중');$('boot-status').className='notice';
    if(e.code==='INSTALLATION_PENDING'){
      setConnection(false,'설치 연결 준비 중');
      $('boot-status').querySelector('p').textContent=retryAt===Infinity?'설치 연결 대기를 마쳤습니다. Setup을 다시 열고 새로고침해 주세요.':'설치 연결을 준비하고 있습니다. Contentrium CUT Setup 창을 열어 둔 채 잠시 기다려 주세요.';
    }else if(['BOOTSTRAP_INVALID','INSTALLATION_ENROLLMENT_INVALID'].includes(e.code)){
      retryAt=Infinity;setConnection(false,'설치 복구 필요');
      $('boot-status').querySelector('p').textContent='설치 연결 정보를 검증하지 못했습니다. 같은 Contentrium CUT Setup으로 설치 복구를 진행해 주세요.';
    }else $('boot-status').querySelector('p').textContent=e.code==='BOOTSTRAP_MISSING'?'설치 정보를 찾지 못했습니다. Contentrium CUT 설치 복구가 필요합니다.':'편집 기능을 준비하고 있습니다. 잠시 후 자동으로 다시 확인합니다.';
    return false;
  }finally{
    if(initializationRequest===token){
      if(initializationIncomplete&&credentials===token.credential&&stopRevision!==token.stop)retryAt=Infinity;
      initializationRequest=null;initializing=false;toggle();
    }
  }
}
view.onChange(()=>{resourceViewRevision++;toggle();});
initialize();
function pollError(e){if(e.code==='PANEL_CONTEXT_CONFLICT')contextConflict();else{setConnection(false,'편집 연결 복구 중');if(e.code==='AUTH_REQUIRED'||e.code==='SESSION_EXPIRED'||!e.code){credentials=null;connection.reset();retryAt=Date.now()+1000;}if(job||applying){stopped=true;error(e);}toggle();}}
setInterval(async()=>{
  if(!credentials){if(Date.now()>=retryAt)await initialize();return;}
  if(initializing&&!updateIntent)return;
  try{if(!await periodicHeartbeat())return;}catch(e){pollError(e);return;}
  if(polling)return;
  polling=true;
  try{await pollJob();if(!await refresh())return;if(!pending&&!applying&&!job&&!validationCount&&!localEditPending&&!state?.applyRecovery?.blocked&&Date.now()>=sequencePollAt){sequencePollAt=Date.now()+2500;await followSequence();}}
  catch(e){pollError(e);}
  finally{polling=false;}
},1000);
