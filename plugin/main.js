/* Native UXP panel; source media and provider credentials never leave this PC. */
const uxp=require('uxp'),bundle=require('./bundle.json');
require('./sync.js').install(ContentriumHost);
require('./selection.js').install(ContentriumHost);
const $=id=>document.getElementById(id);
const view=require('./view.js').install(document);
const connection=require('./connection.js').create(uxp,bundle,{onValidation:count=>{validationCount=count;if(count&&!stopped)say('원본 파일의 내용이 분석 결과와 같은지 확인하고 있습니다.');toggle();}});
function requestId(){const bytes=new Uint8Array(16);crypto.getRandomValues(bytes);return Array.from(bytes,b=>b.toString(16).padStart(2,'0')).join('');}
const workflow=require('./workflow.js').create({api:(...args)=>api(...args),storage:uxp.storage.secureStorage,randomId:requestId,
  stopped:()=>stopped,onBatch:value=>{batchRunning=value;},onResult:()=>say('결과 시퀀스에 편집을 적용하고 있습니다.')});
let credentials=null,state=null,connected=null,mode='separate',job=null,analysisJob=null,analysis=null,plan=null,syncResult=null,syncJob=null,planCameraRefs=[],applying=false,batchRunning=false,stopped=false,applyId=null,polling=false,pending=false;
const microphoneRows=[],cameraRows=[],speakerRows=[],calibrationRows=[],overrideRows=[],syncRows=[];
let projectSelection=null,inputCapability=null,dismissedCandidate=null,localEditPending=false,resourceLoaded=false,settingsTimer=null,previewPlaying=false,rangeDirty=false,binding=false;
let savedSpeakerMappings={},savedSpeakerMappingScope=null,speakerRowsScope=null,localIntentError=null,panelContextConflict=false;
let analysisState=null;
let validationCount=0,heartbeatRequest=null,updateIntent=null;
const selectedRows=[];
function say(value){$('status').textContent=value;}
function setConnection(ready,text){$('connection').textContent=text;$('connection-dot').className=ready?'connected':'disconnected';$('host-status').textContent=ready?'PREMIERE 로컬 연결됨':'PREMIERE 연결 대기';}
function invalidateAnalysis(){clearAnalysis();renderSpeakers(mode==='mixed'?[]:[...new Set(microphoneRows.filter(r=>r.check.checked).map(r=>r.speaker.value.trim()))]);scheduleSettings();say('음성 입력이 바뀌었습니다. 화자 분석을 다시 실행하세요.');toggle();}
function invalidatePlan(){plan=null;scheduleSettings();toggle();}
const messages={SOURCE_REANALYSIS_REQUIRED:'연결된 시퀀스의 모든 원본을 확인하려면 다시 분석하세요.',APPLY_CAPACITY_EXCEEDED:'편집 기록은 최대 8,192개 작업을 지원합니다. 검토 범위를 나누거나 컷 수를 줄여 주세요.',APPLY_RECOVERY_REQUIRED:'이전 편집 기록을 확인해야 합니다. 설정에서 중단 작업을 확인하세요.',APPLY_HOST_EXIT_REQUIRED:'프로젝트를 저장하고 Premiere를 정상 종료한 뒤 다시 열어 중단 작업을 확인하세요.',CORRECTION_REVISION_CONFLICT:'화자 교정 내용이 변경됐습니다. 최신 결과를 다시 불러왔습니다.',SPEAKER_LINK_REQUIRED:'아직 연결하지 않은 목소리가 있습니다. 화자 교정에서 연결하세요.',MODEL_NOT_READY:'설정에서 필요한 로컬 분석 모델을 준비하세요.',SOURCE_CHANGED:'분석한 원본 파일이 변경됐습니다. 소스를 다시 읽어 주세요.',HOST_SUPPORT_REQUIRED:'이 Premiere 버전에서는 적용 검증이 필요합니다.',INPUT_STREAM_UNSUPPORTED:'선택한 파일이 지정한 영상·오디오 역할을 지원하지 않습니다.',AUTH_REQUIRED:'편집 연결을 복구하고 있습니다.',SESSION_EXPIRED:'편집 연결을 복구하고 있습니다.',UPDATE_IN_PROGRESS:'업데이트를 위해 편집 작업이 중단됐습니다.'};
Object.assign(messages,{PANEL_CONTEXT_CONFLICT:'다른 CUT 패널이 이 설치를 제어하고 있습니다. 제어 중인 패널에서 계속하세요.',EDIT_INTENT_STORAGE_UNAVAILABLE:'이전 편집 기록을 읽지 못했습니다. 설정에서 중단 작업을 확인하세요. (EDIT_INTENT_STORAGE_UNAVAILABLE)',EDIT_INTENT_CORRUPT:'이전 편집 기록이 손상되었습니다. 설정에서 중단 작업을 확인하세요. (EDIT_INTENT_CORRUPT)'});
Object.assign(messages,{
  CANCELED:'작업을 중단했습니다. 필요하면 분석을 다시 시작하세요.',
  CONTINUATION_EXPIRED:'원본 파일 확인 시간이 지났습니다. 소스 상태를 확인한 뒤 다시 분석하세요.',
  VALIDATION_EXPIRED:'원본 파일 확인 시간이 지났습니다. 소스 상태를 확인한 뒤 다시 분석하세요.',
  VALIDATION_WORKER_EXITED:'원본 파일 확인이 중단됐습니다. 소스 상태를 확인한 뒤 다시 분석하세요.',
  VALIDATION_STALE:'원본 파일 확인 결과를 사용할 수 없습니다. 소스를 다시 읽고 분석하세요.',
  VALIDATION_UNAVAILABLE:'원본 파일 확인을 실행할 수 없습니다. 편집 연결 상태를 확인한 뒤 다시 분석하세요.',
  VALIDATION_SCOPE:'원본 파일 확인 대상이 유효하지 않습니다. 소스를 다시 읽고 분석하세요.'
});
function error(e){say(messages[e.code]||((e.code||'작업 오류')+' · '+(e.message||String(e))));}
function isIntentReadError(e){return ['EDIT_INTENT_STORAGE_UNAVAILABLE','EDIT_INTENT_CORRUPT'].includes(e.code);}
function contextConflict(){panelContextConflict=true;credentials=null;stopped=true;plan=null;setConnection(false,'다른 CUT 패널이 제어 중');$('boot-status').className='notice';$('boot-status').querySelector('p').textContent=messages.PANEL_CONTEXT_CONFLICT;say(messages.PANEL_CONTEXT_CONFLICT);toggle();}
function element(tag,text,cls){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;}
function number(value){const e=element('input');e.type='number';e.value=String(value);return e;}
function options(select,values,empty){select.innerHTML='';if(empty){const e=element('option',empty);e.value='';select.appendChild(e);}for(const [value,label] of values){const e=element('option',label);e.value=value;select.appendChild(e);}}
function label(text,input){const e=element('label',text);e.appendChild(input);return e;}
function basename(path){return path.split(/[\\/]/).pop();}
function settingsKey(){return 'cut-settings-'+ContentriumHost.hash({projectRef:connected.snapshot.projectRef,sequenceRef:connected.snapshot.sequenceRef});}
function scheduleSettings(){
  if(settingsTimer)clearTimeout(settingsTimer);
  if(!connected||binding)return;
  const key=settingsKey();
  settingsTimer=setTimeout(async()=>{settingsTimer=null;if(!connected||settingsKey()!==key||applying)return;try{await uxp.storage.secureStorage.setItem(key,JSON.stringify(captureSettings()));}catch(_){say('설정을 자동 저장하지 못했습니다. 설정에서 다시 저장해 주세요.');}},400);
}
function captureSettings(){
  return {schemaVersion:2,projectRef:connected.snapshot.projectRef,sequenceRef:connected.snapshot.sequenceRef,mode,policy:policy(),
    calibration:calibrationRows.map(r=>({key:r.instanceKey,speaker:r.speaker.value,first:r.first.value,last:r.last.value})),
    microphones:microphoneRows.map(r=>({key:r.clip.instanceKey,checked:r.check.checked,speaker:r.speaker.value,channel:r.channel.value,stream:r.stream?.value||'1'})),
    cameras:cameraRows.map(r=>({id:r.id,assets:connected.snapshot.clips.filter(c=>c.trackRef===r.id).map(c=>c.assetId).sort(),role:r.role.value,covered:r.covered.value})),
    speakers:speakerRows.map(r=>({id:r.id,camera:r.select.value})),speakerMappingScope:currentSpeakerMappingScope(),speakerCount:$('speaker-count').value,vadThreshold:$('vad-threshold').value,start:$('start-camera').value,reserve:$('reserve-camera').value,
    range:{startFrame:Number($('range-start').value),endFrame:Number($('range-end').value)},sync:syncOptions(),
    analysisReference:analysisState&&analysisJob?{schemaVersion:1,analysisId:analysisState.analysisId,jobId:analysisJob,revision:analysisState.revision,
      snapshotHash:analysisState.snapshotHash,mode,inputHash:ContentriumHost.hash(microphoneOptions())}:null};
}
function restoreSettings(settings){
  if(settings.schemaVersion!==2||settings.projectRef!==connected.snapshot.projectRef||settings.sequenceRef!==connected.snapshot.sequenceRef)throw new Error('현재 시퀀스의 설정이 아닙니다.');
  if(mode!==settings.mode){mode=settings.mode;for(const value of ['separate','mixed'])$('mode-'+value).className='mode'+(mode===value?' active':'');renderSources();}
  for(const r of microphoneRows){const v=settings.microphones.find(v=>v.key===r.clip.instanceKey);if(v){r.check.checked=v.checked;r.speaker.value=v.speaker;r.channel.value=v.channel;if(r.stream)r.stream.value=v.stream;}}
  const mappingScope=currentSpeakerMappingScope(),restoreMappings=sameSpeakerMappingScope(settings.speakerMappingScope,mappingScope);
  for(const r of cameraRows){const assets=connected.snapshot.clips.filter(c=>c.trackRef===r.id).map(c=>c.assetId).sort();const v=settings.cameras.find(v=>v.id===r.id&&ContentriumHost.hash(v.assets)===ContentriumHost.hash(assets));if(v){r.role.value=v.role;r.covered.value=mode==='mixed'&&!restoreMappings?'':v.covered;}}
  for(const r of calibrationRows){const v=settings.calibration?.find(v=>v.key===r.instanceKey);if(v){r.first.value=v.first;r.last.value=v.last;}}
  savedSpeakerMappings=restoreMappings?Object.fromEntries(settings.speakers.filter(v=>cameraValues().some(c=>c[0]===v.camera)).map(v=>[v.id,v.camera])):{};
  savedSpeakerMappingScope=restoreMappings?mappingScope:null;speakerRowsScope=null;
  renderSpeakers(mode==='mixed'?(analysisState?.analysis.sessionSpeakerIds||[]):microphoneRows.filter(r=>r.check.checked).map(r=>r.speaker.value));mappingInputs();
  if(cameraValues().some(c=>c[0]===settings.start))$('start-camera').value=settings.start;
  if(!settings.reserve||cameraValues().some(c=>c[0]===settings.reserve))$('reserve-camera').value=settings.reserve;
  $('min-shot').value=String(settings.policy.minShot);$('short-turn').value=String(settings.policy.shortTurn);$('overlap').value=String(settings.policy.overlap);
  if(settings.speakerCount)$('speaker-count').value=settings.speakerCount;
  if(settings.vadThreshold)$('vad-threshold').value=settings.vadThreshold;
  const bounds=settings.range;
  if(bounds&&Number.isSafeInteger(bounds.startFrame)&&Number.isSafeInteger(bounds.endFrame)&&bounds.startFrame>=0&&bounds.endFrame>bounds.startFrame&&bounds.endFrame<=(connected.fullRangeEnd||connected.snapshot.range.endFrame)){
    $('range-start').value=String(bounds.startFrame);$('range-end').value=String(bounds.endFrame);rangeDirty=bounds.startFrame!==connected.snapshot.range.startFrame||bounds.endFrame!==connected.snapshot.range.endFrame;
  }
  const sync=settings.sync;
  if(sync&&['audio','manual','timecode'].includes(sync.method)){
    $('sync-method').value=sync.method;if(syncRows.some(r=>r.source.assetId===sync.reference))$('sync-reference').value=sync.reference;
    for(const r of syncRows){
      const selection=sync.sourceSelections?.find(s=>s.assetId===r.source.assetId);r.check.checked=!!selection;
      if(selection){r.stream.value=String(selection.streamIndex+1);r.channel.value=String(selection.channelIndex+1);}
      const manual=sync.manualOffsets?.[r.source.assetId];if(manual){r.offset.value=manual.offsetSeconds;r.confirmed.checked=manual.confirmed===true;}
      const clock=sync.timecodeConfirmations?.[r.source.assetId];if(clock){r.clockId.value=clock.clockId;r.date.value=clock.date;r.fps.value=clock.fps.num+'/'+clock.fps.den;r.drop.checked=clock.dropFrame===true;r.clockConfirmed.checked=clock.confirmed===true;}
    }syncMethodChanged();
  }
  overrideRows.length=0;$('overrides').innerHTML='';
  for(const v of settings.policy.overrides||[]){if(v.startFrame>=connected.snapshot.range.startFrame&&v.endFrame<=connected.snapshot.range.endFrame&&v.endFrame>v.startFrame&&cameraValues().some(c=>c[0]===v.cameraId)){addOverride();const r=overrideRows[overrideRows.length-1];r.first.value=String(v.startFrame);r.last.value=String(v.endFrame);r.camera.value=v.cameraId;}}
  plan=null;
}
async function restoreSavedSettings(optional=false){
  try{const raw=await uxp.storage.secureStorage.getItem(settingsKey()),settings=JSON.parse(typeof raw==='string'?raw:new TextDecoder().decode(raw));restoreSettings(settings);return settings;}
  catch(e){if(!optional)throw new Error('현재 시퀀스에 저장된 설정을 불러오지 못했습니다.');}
}
async function restoreSavedAnalysis(settings){
  const ref=settings.analysisReference;if(!ref)return false;
  const matches=()=>ref.schemaVersion===1&&/^[a-f0-9]{64}$/.test(ref.analysisId)&&/^[a-zA-Z0-9_-]{1,128}$/.test(ref.jobId)&&
    Number.isSafeInteger(ref.revision)&&ref.revision>=0&&ref.snapshotHash===connected?.snapshot.snapshotHash&&ref.mode===mode&&ref.inputHash===ContentriumHost.hash(microphoneOptions());
  await requireCurrent();if(!matches())throw new Error('이전 분석의 타임라인 또는 음성 입력이 달라졌습니다. 새로 분석해 주세요.');
  const completed=await api('/jobs/'+ref.jobId);
  if(completed.kind!=='analysis'||completed.status!=='completed')throw new Error('이전 분석 작업을 확인할 수 없습니다. 새로 분석해 주세요.');
  const restored=await api('/analyses/'+ref.analysisId);
  await requireCurrent();if(!matches()||restored.analysisId!==ref.analysisId||restored.snapshotHash!==ref.snapshotHash)throw new Error('이전 분석의 범위가 현재 시퀀스와 다릅니다.');
  if(!Number.isSafeInteger(restored.revision)||restored.revision<ref.revision)throw new Error('저장한 교정 이력을 확인할 수 없습니다.');
  analysisJob=ref.jobId;acceptAnalysis(restored);
  binding=true;try{restoreSettings(settings);}finally{binding=false;}
  scheduleSettings();return true;
}
async function api(path,body,method){return connection.request(path,body,method);}
function admitted(){if(!credentials||!connected)throw new Error('Premiere에서 편집할 시퀀스를 열어 주세요.');if(!state?.gateOpen||stopped)throw new Error('현재 작업 상태를 확인한 뒤 다시 실행하세요.');if(localEditPending||state?.applyRecovery?.blocked)throw Object.assign(new Error('APPLY_RECOVERY_REQUIRED'),{code:'APPLY_RECOVERY_REQUIRED'});}
function toggle(){
  const busy=pending||applying||!!job||validationCount>0,locked=!credentials||!state?.gateOpen||state.compatible===false||stopped||busy||localEditPending||state?.applyRecovery?.blocked;
  for(const el of document.querySelectorAll('input,select,button.mode'))el.disabled=busy||stopped||!credentials||!state?.gateOpen;
  for(const id of ['analyze','sync','plan','apply-sync','apply','save-settings','load-settings'])$(id).disabled=locked||!connected||(id==='plan'&&!analysisState)||(id==='apply'&&!plan)||(id==='apply-sync'&&!syncResult);
  for(const id of ['read-project','read-selection','install-model','save-resources','prune-cache'])$(id).disabled=locked;
  for(const el of document.querySelectorAll('[data-work]'))el.disabled=locked;
  $('create-input').disabled=locked||!projectSelection||!inputCapability;$('cancel').disabled=!job&&!applying&&!previewPlaying&&!validationCount;
  $('undo-correction').disabled=locked||!analysisState||activeCorrections().length===0;
  $('recover-apply').disabled=busy||!credentials||!(localEditPending||state?.applyRecovery?.blocked);
  $('release-cache').disabled=busy||!credentials||state?.compatible===false||state?.maintenance?.canRelease!==true;
  $('progress').className=job||applying||pending||validationCount?'running':'';
  const update=state?.update,available=update?.candidate&&['AVAILABLE','CHECKING'].includes(update.checkState);
  $('update').disabled=!credentials||!!updateIntent?.inFlight||!!updateIntent?.accepted||(!updateIntent&&!available)||!!update&& !['IDLE','COMPLETE','CANCELED','FAILED_BEFORE_REPLACE','ROLLED_BACK'].includes(update.updateState);
  $('update-banner-button').disabled=$('update').disabled;
  $('check-update').disabled=!!updateIntent||update?.checkState==='CHECKING';
  if(!['ready','installed'].includes(state?.models?.[mode==='separate'?'silero':'community-1']?.status))$('analyze').disabled=true;
}
function cameraValues(){return cameraRows.filter(r=>r.role.value!=='protected').map(r=>[r.id,r.title]);}
function mappingInputs(){const values=cameraValues();const old=$('start-camera').value,reserve=$('reserve-camera').value;options($('start-camera'),values);options($('reserve-camera'),values,'지정 안 함');if(values.some(v=>v[0]===old))$('start-camera').value=old;if(values.some(v=>v[0]===reserve))$('reserve-camera').value=reserve;for(const row of speakerRows){const previous=row.select.value;options(row.select,values,'카메라 선택');if(values.some(v=>v[0]===previous))row.select.value=previous;}for(const row of overrideRows){const previous=row.camera.value;options(row.camera,values);if(values.some(v=>v[0]===previous))row.camera.value=previous;}invalidatePlan();}
function trackTitle(tag,name,count,audio=false){
  const title=element('div',undefined,'track-title');title.appendChild(element('span',tag,'track-badge'+(audio?' audio':'')));title.appendChild(element('span',name,'source-title'));if(count!==undefined)title.appendChild(element('span',count+' CLIP','track-meta'));return title;
}
function checkbox(checked=false){const e=element('input');e.type='checkbox';e.checked=checked;return e;}
function renderSources(){
  for(const id of ['microphones','cameras','calibration','speaker-mapping','sync-sources'])$(id).innerHTML='';
  microphoneRows.length=cameraRows.length=calibrationRows.length=speakerRows.length=syncRows.length=0;
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
    $('microphones').appendChild(row);microphoneRows.push({clip,check,speaker,channel,stream});
    for(const field of [check,speaker,channel,stream])field.onchange=()=>{invalidateAnalysis();renderSpeakers(mode==='mixed'?[]:[...new Set(microphoneRows.filter(r=>r.check.checked).map(r=>r.speaker.value.trim()))]);};
    const calibration=element('div',undefined,'source-row'),bounds=element('div',undefined,'row'),first=number(0),last=number(0);
    calibration.appendChild(element('div','A'+(track.index+1)+' · '+basename(assets.get(clip.assetId).canonicalPath),'source-title'));
    bounds.appendChild(label('단독 발화 시작 · 프레임',first));bounds.appendChild(label('종료 · 프레임',last));calibration.appendChild(bounds);$('calibration').appendChild(calibration);
    calibrationRows.push({instanceKey:clip.instanceKey,speaker,first,last,stream,channel});for(const field of [first,last])field.onchange=invalidateAnalysis;
  }
  for(const track of s.tracks.filter(t=>t.mediaType==='video'&&s.clips.some(c=>c.trackRef===t.trackRef))){
    const row=element('div',undefined,'camera-row'),clips=s.clips.filter(c=>c.trackRef===track.trackRef),title='V'+(track.index+1)+' · '+track.name;
    row.appendChild(trackTitle('V'+(track.index+1),track.name,clips.length));
    const fields=element('div',undefined,'row'),role=element('select'),covered=element('input');
    options(role,[['speaker','화자 카메라'],['wide','전체샷'],['two-shot','투샷'],['reserve','예비'],['protected','보호 트랙']]);covered.placeholder='A, B';role.value=track.muted?'protected':'speaker';
    fields.appendChild(label('트랙 역할',role));fields.appendChild(label('보이는 화자',covered));row.appendChild(fields);$('cameras').appendChild(row);
    cameraRows.push({id:track.trackRef,track,title,role,covered});role.onchange=mappingInputs;covered.onchange=invalidatePlan;
  }
  renderSyncSources();renderSpeakers(mode==='mixed'?[]:[...new Set(microphoneRows.filter(r=>r.check.checked).map(r=>r.speaker.value))]);
  if(mode==='mixed')$('speaker-mapping').appendChild(element('p','화자를 분석하면 감지한 목소리를 카메라에 연결할 수 있습니다.','hint'));mappingInputs();
}
function renderSyncSources(){
  const values=[];
  for(const source of connected.snapshot.sources){
    const row=element('div',undefined,'source-row'),check=checkbox(true),title=element('label',basename(source.canonicalPath));title.insertBefore(check,title.firstChild);row.appendChild(title);
    const fields=element('div',undefined,'row'),stream=number(1),channel=number(1);stream.min=channel.min='1';fields.appendChild(label('오디오 스트림',stream));fields.appendChild(label('채널',channel));row.appendChild(fields);
    const manual=element('div'),offset=number(0),confirmed=checkbox();offset.step='0.001';manual.appendChild(label('기준 대비 오프셋 · 초',offset));manual.appendChild(label('이 오프셋을 확인했습니다',confirmed));row.appendChild(manual);
    const clock=element('div'),clockId=element('input'),date=element('input'),fps=element('select'),drop=checkbox(),clockConfirmed=checkbox();date.placeholder='YYYY-MM-DD';clockId.placeholder='같은 동기 장치 또는 시계 이름';
    options(fps,[['24000/1001','23.976'],['24/1','24'],['25/1','25'],['30000/1001','29.97'],['30/1','30'],['50/1','50'],['60000/1001','59.94'],['60/1','60']]);fps.value=connected.snapshot.fps.num+'/'+connected.snapshot.fps.den;
    clock.appendChild(label('공통 시계',clockId));clock.appendChild(label('촬영 날짜',date));clock.appendChild(label('타임코드 FPS',fps));clock.appendChild(label('Drop-frame',drop));clock.appendChild(label('날짜와 시계가 같고 촬영 중 리셋하지 않았습니다',clockConfirmed));row.appendChild(clock);
    $('sync-sources').appendChild(row);syncRows.push({check,source,stream,channel,manual,offset,confirmed,clock,clockId,date,fps,drop,clockConfirmed});
    for(const field of [check,stream,channel,offset,confirmed,clockId,date,fps,drop,clockConfirmed])field.onchange=()=>{syncResult=syncJob=null;scheduleSettings();toggle();};
    values.push([source.assetId,basename(source.canonicalPath)]);
  }
  options($('sync-reference'),values);syncMethodChanged();
}
function syncMethodChanged(){for(const r of syncRows){r.manual.className=$('sync-method').value==='manual'?'':'hidden';r.clock.className=$('sync-method').value==='timecode'?'':'hidden';}syncResult=syncJob=null;scheduleSettings();toggle();}
function syncOptions(){
  const rows=syncRows.filter(r=>r.check.checked),method=$('sync-method').value;
  return {assetIds:rows.map(r=>r.source.assetId),reference:$('sync-reference').value,method,
    sourceSelections:rows.map(r=>({assetId:r.source.assetId,streamIndex:Number(r.stream.value)-1,channelIndex:Number(r.channel.value)-1})),
    manualOffsets:Object.fromEntries(rows.map(r=>[r.source.assetId,{offsetSeconds:r.offset.value,confirmed:r.confirmed.checked}])),
    timecodeConfirmations:Object.fromEntries(rows.map(r=>{const parts=r.fps.value.split('/').map(Number);return [r.source.assetId,{confirmed:r.clockConfirmed.checked,clockId:r.clockId.value.trim(),date:r.date.value.trim(),fps:{num:parts[0],den:parts[1]},dropFrame:r.drop.checked,reset:false}];}))};
}
function workButton(text,fn){const button=element('button',text,'full');button.setAttribute('data-work','true');button.onclick=()=>runAction(fn);return button;}
async function runAction(fn){if(pending||applying||job)return;pending=true;toggle();try{await fn();}catch(e){error(e);}finally{pending=false;toggle();}}
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
  return {mode,sourceScope:ContentriumHost.hash({projectRef:connected.snapshot.projectRef,sequenceRef:connected.snapshot.sequenceRef,microphones:microphoneRows.filter(r=>r.check.checked).map(r=>({instanceKey:r.clip.instanceKey,assetId:r.clip.assetId,speakerId:r.speaker.value.trim(),stream:r.stream?.value||'1',channel:r.channel.value}))})};
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
    select.onchange=()=>{if(!sameSpeakerMappingScope(savedSpeakerMappingScope,scope))savedSpeakerMappings={};savedSpeakerMappingScope=scope;savedSpeakerMappings[id]=select.value;invalidatePlan();};
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
  analysisState=next;analysis=next.analysis;plan=null;renderSpeakers(analysis.sessionSpeakerIds);toggle();scheduleSettings();
}
async function correct(operation){
  await requireCurrent();if(!analysisState)throw new Error('화자 분석을 먼저 실행하세요.');
  const id=analysisState.analysisId;
  try{acceptAnalysis(await api('/analyses/'+id+'/correct',{expectedRevision:analysisState.revision,operation,requestId:requestId(),epoch:state.epoch}));scheduleSettings();say('화자 교정을 저장했습니다. 편집안을 다시 만들어 주세요.');}
  catch(e){if(e.code==='CORRECTION_REVISION_CONFLICT')acceptAnalysis(await api('/analyses/'+id));throw e;}
}
async function stopPreview(){if(previewPlaying){await ContentriumHost.ppro.SourceMonitor.play(0);previewPlaying=false;toggle();}}
async function listenExample(example){
  await requireCurrent();await stopPreview();
  if(!analysisState)throw new Error('화자 분석을 먼저 실행하세요.');
  const identity={analysisId:analysisState.analysisId,revision:analysisState.revision,snapshotHash:connected.snapshot.snapshotHash};
  job={...await api('/analyses/'+identity.analysisId+'/example',{exampleId:example.exampleId,expectedRevision:identity.revision,epoch:state.epoch}),...identity};
  say('단독 발화 샘플을 준비하고 있습니다.');toggle();
}
function microphoneOptions(){return {mode,microphones:microphoneRows.filter(r=>r.check.checked).map(r=>({instanceKey:r.clip.instanceKey,speakerId:r.speaker.value.trim(),streamIndex:Number(r.stream.value)-1,channelIndex:Number(r.channel.value)-1})),speakerCount:Number($('speaker-count').value),vadThreshold:Number($('vad-threshold').value),calibration:calibrationRows.filter(r=>Number(r.last.value)>Number(r.first.value)).map(r=>({speakerId:r.speaker.value.trim(),inputKey:ContentriumHost.hash({instanceKey:r.instanceKey,streamIndex:Number(r.stream.value)-1,channelIndex:Number(r.channel.value)-1}),startFrame:Number(r.first.value),endFrame:Number(r.last.value)}))};}
function mapping(){const s=connected.snapshot;return {speakers:Object.fromEntries(speakerRows.map(r=>[r.id,r.select.value])),cameras:cameraRows.filter(r=>r.role.value!=='protected').map(r=>({cameraId:r.id,role:r.role.value,coveredSpeakers:r.covered.value.split(',').map(v=>v.trim()).filter(Boolean),clips:s.clips.filter(c=>c.trackRef===r.id).map(c=>({instanceKey:c.instanceKey,startFrame:Math.round(Number(c.startTicks)/connected.perFrame),endFrame:Math.round(Number(c.endTicks)/connected.perFrame)}))})),startCameraId:$('start-camera').value,fallbackOrder:$('reserve-camera').value?[$('reserve-camera').value]:[]};}
function policy(){return {minShot:Number($('min-shot').value),shortTurn:Number($('short-turn').value),suppressShort:true,overlap:Number($('overlap').value),overrides:overrideRows.map(r=>({startFrame:Number(r.first.value),endFrame:Number(r.last.value),cameraId:r.camera.value}))};}
async function readProject({fresh=null,automatic=false}={}){
  if(applying||job)throw new Error('현재 작업을 마친 뒤 시퀀스를 변경하세요.');
  binding=true;if(settingsTimer){clearTimeout(settingsTimer);settingsTimer=null;}
  const prior=connected,settings=prior?captureSettings():null;
  try{
    const next=fresh||await ContentriumHost.snapshot(),s=next.snapshot;
    next.fullRangeEnd=s.range.endFrame;
    const same=prior&&prior.snapshot.projectRef===s.projectRef&&prior.snapshot.sequenceRef===s.sequenceRef;
    s.hostSnapshotHash=s.snapshotHash;
    let start=same?Number($('range-start').value||0):0,end=same?Number($('range-end').value||s.range.endFrame):s.range.endFrame;
    if(automatic&&(end>s.range.endFrame||start>=s.range.endFrame)){start=0;end=s.range.endFrame;}
    if(!Number.isSafeInteger(start)||!Number.isSafeInteger(end)||start<0||end<=start||end>s.range.endFrame)throw new Error('프레임 범위를 확인하세요.');
    s.range={startFrame:start,endFrame:end};delete s.snapshotHash;s.snapshotHash=ContentriumHost.hash(s);
    await heartbeat();await api('/project',{snapshot:s,hostIdentity:null,epoch:state.epoch});connected=next;rangeDirty=false;
    $('project-name').textContent=s.sequenceName;$('project-name').title=s.projectName+' / '+s.sequenceName;
    $('sequence-info').textContent=frameLabel(s.range.endFrame)+' · '+(s.fps.num/s.fps.den).toFixed(2)+' fps';
    $('track-count').textContent=s.tracks.filter(t=>t.mediaType==='video').length+' V / '+s.tracks.filter(t=>t.mediaType==='audio').length+' A';
    $('range-start').value=String(start);$('range-end').value=String(end);
    clearAnalysis();syncResult=syncJob=null;savedSpeakerMappings={};overrideRows.length=0;$('overrides').innerHTML='';
    renderSources();if(same&&settings)restoreSettings(settings);else await restoreSavedSettings(true);
    toggle();say('트랙을 확인하고 분석할 마이크와 카메라를 지정하세요.');
  }catch(e){resetSequence();throw e;}
  finally{binding=false;}
}
function frameLabel(frame){const rate=connected?connected.snapshot.fps.num/connected.snapshot.fps.den:30,seconds=Math.max(0,Math.floor(frame/rate));return [Math.floor(seconds/3600),Math.floor(seconds/60)%60,seconds%60].map(n=>String(n).padStart(2,'0')).join(':');}
function clearAnalysis(){if(mode==='mixed'||speakerRowsScope?.mode==='mixed'||savedSpeakerMappingScope?.mode==='mixed')clearMixedMappings();analysisJob=analysis=plan=null;analysisState=null;if(mode==='mixed'){$('speaker-mapping').innerHTML='';speakerRows.length=0;}$('cut-count').textContent=$('review-count').textContent='—';for(const id of ['timeline','segments','reviews','speaker-corrections','correction-history'])$(id).innerHTML='';$('segments').appendChild(element('p','화자를 분석하고 편집안을 만들어 주세요.','hint'));}
function resetSequence(){connected=null;clearAnalysis();syncResult=syncJob=null;$('sync-result').textContent='';$('project-name').textContent='시퀀스를 열어 주세요';$('sequence-info').textContent='Premiere 타임라인을 자동으로 읽습니다.';$('track-count').textContent='TIMELINE';for(const id of ['microphones','cameras','calibration','speaker-mapping','sync-sources'])$(id).innerHTML='';microphoneRows.length=cameraRows.length=calibrationRows.length=speakerRows.length=syncRows.length=0;toggle();}
async function followSequence(){
  try{
    const fresh=await ContentriumHost.snapshot();
    if(!connected||fresh.snapshot.snapshotHash!==connected.snapshot.hostSnapshotHash)await readProject({fresh,automatic:true});
  }catch(e){
    if(/PROJECT_REQUIRED|SEQUENCE_REQUIRED/.test(String(e))){if(connected){resetSequence();say('Premiere에서 편집할 시퀀스를 열어 주세요.');}}
    else throw e;
  }
}
async function requireCurrent(){admitted();const fresh=await ContentriumHost.snapshot();if(fresh.snapshot.snapshotHash!==connected.snapshot.hostSnapshotHash){await readProject({fresh,automatic:true});throw new Error('타임라인이 변경됐습니다. 갱신된 트랙 설정을 확인하세요.');}if(rangeDirty)await readProject({fresh});}
async function seekFrame(frame){await requireCurrent();await stopPreview();await connected.sequence.setPlayerPosition(ContentriumHost.time(String(BigInt(frame)*BigInt(connected.perFrame))));}
const cutReasons={START_CAMERA:'시작 카메라',START_SPEAKER:'첫 발화 화자',SPEAKER_TURN:'화자 전환',speech:'발화',speaker:'화자',MANUAL_OVERRIDE:'수동 카메라 지정',OVERRIDE_END:'수동 지정 종료',OVERLAP_SUSTAINED:'지속된 동시 발화',OVERLAP_HOLD:'동시 발화 중 카메라 유지',MIN_SHOT_HOLD:'최소 샷 길이 유지',VIDEO_GAP_FALLBACK:'영상 공백으로 대체 카메라',MIN_SHOT_EXCEPTION_OVERRIDE:'수동 지정으로 최소 샷 길이 예외',MIN_SHOT_EXCEPTION_OVERRIDE_END:'수동 지정 종료로 최소 샷 길이 예외',MIN_SHOT_EXCEPTION_OVERLAP:'동시 발화로 최소 샷 길이 예외',MIN_SHOT_EXCEPTION_COVERAGE:'영상 공백으로 최소 샷 길이 예외',RANGE_END_SHORT:'분석 범위 끝의 짧은 샷'};
Object.assign(cutReasons,{HOLD:'카메라 유지',UNKNOWN_HOLD:'화자 불확실로 카메라 유지'});
function segmentReason(segment){const codes=[...new Set([segment.reason,...(Array.isArray(segment.reasonCodes)?segment.reasonCodes:[])].filter(code=>typeof code==='string'&&code.trim()))];return codes.length?'이유 · '+codes.map(code=>Object.prototype.hasOwnProperty.call(cutReasons,code)?cutReasons[code]:code).join(' · '):'이유 정보 없음';}
function renderPlan(){
  const segments=plan.segments;$('cut-count').textContent=String(segments.length);$('review-count').textContent=String(plan.reviews.length);$('timeline').innerHTML='';
  const bar=element('div',undefined,'timeline');for(const segment of segments){const block=element('div');block.style.flex=String(segment.endFrame-segment.startFrame);bar.appendChild(block);}$('timeline').appendChild(bar);$('segments').innerHTML='';
  for(const segment of segments){
    const row=workButton('',()=>seekFrame(segment.startFrame));row.className='segment-row';
    const camera=cameraRows.find(r=>r.id===segment.cameraId);row.appendChild(element('span',frameLabel(segment.startFrame)+'–'+frameLabel(segment.endFrame),'segment-range'));row.appendChild(element('span',camera?.title||segment.cameraId,'segment-camera'));row.appendChild(element('span',segmentReason(segment),'segment-reasons'));$('segments').appendChild(row);
  }
  $('reviews').innerHTML='';
  for(const review of plan.reviews){const row=element('div',(review.startFrame===undefined?'전체':frameLabel(review.startFrame)+'–'+frameLabel(review.endFrame))+' · '+(review.message||review.code),'review-row');if(review.startFrame!==undefined)row.appendChild(workButton('구간 확인',()=>seekFrame(review.startFrame)));$('reviews').appendChild(row);}
  if(!plan.reviews.length)$('reviews').appendChild(element('p','확인이 필요한 사항이 없습니다.','hint'));toggle();
}
function addOverride(){if(!connected)return;const row=element('div',undefined,'override-row'),fields=element('div',undefined,'row'),first=number(connected.snapshot.range.startFrame),last=number(connected.snapshot.range.endFrame),camera=element('select');options(camera,cameraValues());fields.appendChild(label('시작',first));fields.appendChild(label('종료',last));row.appendChild(fields);row.appendChild(label('고정 카메라',camera));const remove=element('button','삭제');row.appendChild(remove);const value={first,last,camera};for(const field of [first,last,camera])field.onchange=()=>{plan=null;toggle();};overrideRows.push(value);remove.onclick=()=>{overrideRows.splice(overrideRows.indexOf(value),1);row.remove();plan=null;toggle();};$('overrides').appendChild(row);plan=null;toggle();}
async function refresh(){
  if(!credentials)return;state=await api('/state');
  if(updateIntent&&state.gateOpen&&state.update.updateEpoch>updateIntent.epoch&&['COMPLETE','CANCELED','FAILED_BEFORE_REPLACE','ROLLED_BACK'].includes(state.update.updateState))updateIntent=null;
  if(updateIntent||!state.gateOpen||state.stopEpoch!==null&&state.stopEpoch!==undefined){stopped=true;plan=null;}else if(!applying)stopped=false;
  $('boot-status').className='hidden';
  const compatible=state.appVersion===bundle.appVersion&&state.bundleId===bundle.bundleId&&state.protocolVersion===bundle.protocolVersion;
  if(!compatible){stopped=true;plan=null;setConnection(false,'업데이트된 패널을 열려면 Premiere를 다시 시작하세요.');}
  else if(state.compatible===false)setConnection(false,'Premiere 연결 확인 중');
  else setConnection(true,updateIntent?'업데이트 진행 중':state.gateOpen?'편집 준비됨':state.maintenance?'캐시 정리 상태 확인':'업데이트 진행 중');
  const statuses={ready:'준비됨',installed:'설치됨 · 분석 시 무결성 확인',not_installed:'설치 필요',failed:'확인 필요',installing:'설치 중'};
  $('models').textContent=['silero','community-1'].map(id=>(id==='silero'?'Silero':'Community-1')+' · '+(statuses[state.models[id].status]||state.models[id].status)).join('\n');
  const required=state.models[mode==='separate'?'silero':'community-1'];$('model-status').textContent=['ready','installed'].includes(required.status)?'분석 시작 시 로컬 모델 무결성을 확인합니다.':'설정에서 '+(mode==='separate'?'발화':'혼합 녹음')+' 모델을 준비하세요.';
  const update=state.update,candidate=update.candidate;
  $('update-banner').className=!updateIntent&&candidate&&['AVAILABLE','CHECKING'].includes(update.checkState)&&candidate.candidateId!==dismissedCandidate?'':'hidden';
  $('update-banner-text').textContent=candidate?'Contentrium CUT '+candidate.appVersion+' 업데이트':'';
  $('update-info').textContent=['IDLE','COMPLETE'].includes(update.updateState)?(updateIntent?'업데이트 시작 요청을 확인하고 있습니다.':candidate?'새 버전 '+candidate.appVersion:({CURRENT:'최신 버전입니다.',NO_RELEASE:'게시된 업데이트가 없습니다.',CHECK_FAILED:'업데이트를 확인하지 못했습니다.'}[update.checkState]||'업데이트 확인 중')):({STOP_REQUESTED:'작업 중단을 요청했습니다.',QUIESCING:previewPlaying?'미리보기 종료를 기다리고 있습니다. 지연되면 프로젝트를 저장하고 Premiere를 정상 종료하세요.':'진행 중인 작업을 안전하게 종료하고 있습니다.',DOWNLOADING:'업데이트 파일을 다운로드하고 있습니다.',VERIFYING_PACKAGE:'다운로드한 파일을 검증하고 있습니다.',WAITING_HOST_EXIT:'프로젝트를 저장하고 Premiere를 정상 종료하면 설치를 계속합니다.',PENDING_ACTIVATION:'새 플러그인의 실행을 확인하고 있습니다.',RECOVERY_REQUIRED:'설치 복구가 필요합니다.',CANCELED:'업데이트를 중단했습니다.',FAILED_BEFORE_REPLACE:'설치 전에 업데이트가 실패했습니다. 다시 확인해 주세요.',ROLLED_BACK:'이전 버전으로 복구했습니다.'}[update.updateState]||update.updateState);
  $('release-notes').textContent=candidate?.releaseNotes||'';$('recover-update').disabled=!['RECOVERY_REQUIRED','FAILED'].includes(update.updateState);
  const recovery=state.applyRecovery;
  $('apply-recovery').className=localEditPending||recovery?.blocked?'notice recovery-notice':'hidden';
  $('apply-recovery-text').textContent=localIntentError?messages[localIntentError]:localEditPending||recovery?.blocked?'이전 편집이 중단되었습니다. 기록을 확인한 뒤 새 작업을 시작할 수 있습니다.':'';
  $('cache-maintenance').className=state.maintenance?'':'hidden';
  $('cache-maintenance-text').textContent=state.maintenance?.canRelease?'정리 작업이 종료됐습니다. 편집을 계속할 수 있습니다.':'캐시 정리 작업이 종료되는 것을 기다리고 있습니다.';
  toggle();if(!state.gateOpen&&!state.maintenance&&!applying&&!batchRunning&&!previewPlaying){await api('/updates/ack',{epoch:state.epoch,quiescent:true,batchRunning:false}).catch(()=>{});}
}
async function heartbeat(){if(!credentials||!state)return;const receipt=await api('/heartbeat',{hostIdentity:null,epoch:state.epoch,batchRunning,quiescent:!applying&&!previewPlaying,panelVersion:bundle.appVersion,bundleId:bundle.bundleId,protocolVersion:bundle.protocolVersion});if(!receipt.gateOpen||receipt.stopEpoch!==null&&receipt.stopEpoch!==undefined){stopped=true;plan=null;toggle();}}
function periodicHeartbeat(){
  if(!heartbeatRequest){const next=heartbeat();heartbeatRequest=next;const clear=()=>{if(heartbeatRequest===next)heartbeatRequest=null;};next.then(clear,clear);}
  return heartbeatRequest;
}
const canceledJobs=new Set();
async function pollJob(){
  if(!job)return;
  const active=job;let value;
  try{value=await api('/jobs/'+active.jobId);}catch(e){
    if(!['SOURCE_CHANGED','SOURCE_REANALYSIS_REQUIRED','CANCELED','CONTINUATION_EXPIRED','VALIDATION_EXPIRED','VALIDATION_WORKER_EXITED','VALIDATION_STALE','VALIDATION_UNAVAILABLE','VALIDATION_SCOPE'].includes(e.code)&&!canceledJobs.has(active.jobId))throw e;
    if(job===active)job=null;
    if(active.kind==='analysis')clearAnalysis();
    if(active.kind==='sync')syncResult=syncJob=null;
    if(active.kind==='input-probe')inputCapability=null;
    error(e);toggle();return;
  }
  if(['running','canceling'].includes(value.status)){
    const labels={sync:'소스의 싱크를 분석하고 있습니다.',analysis:'로컬에서 화자를 분석하고 있습니다.',example:'단독 발화 샘플을 준비하고 있습니다.','model-setup':'화자 모델을 설치하고 있습니다.','input-probe':'선택 소스의 영상과 오디오를 확인하고 있습니다.'};
    say(value.status==='canceling'?'작업을 중단하고 있습니다.':labels[active.kind]||'작업 중');return;
  }
  try{
    if(canceledJobs.has(active.jobId)){say('작업을 중단했습니다.');return;}
    if(value.status!=='completed'){say('작업 중단 · '+(value.error?.code||value.status));return;}
    if(['analysis','sync','example'].includes(active.kind)){
      const fresh=await ContentriumHost.snapshot();
      if(!connected||active.snapshotHash!==connected.snapshot.snapshotHash||fresh.snapshot.snapshotHash!==connected.snapshot.hostSnapshotHash){resetSequence();throw new Error('분석 중 타임라인이 변경됐습니다. 현재 시퀀스를 다시 읽어 주세요.');}
    }
    if(active.kind==='analysis'){
      analysisJob=value.jobId;acceptAnalysis(await api('/analyses/register',{jobId:value.jobId,epoch:state.epoch}));
      view.show('speakers');say('화자 분석이 끝났습니다. 목소리와 카메라를 확인하세요.');
    }else if(active.kind==='sync'){
      syncJob=value.jobId;syncResult=value.result;
      const sources=new Map(connected.snapshot.sources.map(s=>[s.assetId,basename(s.canonicalPath)]));
      $('sync-result').textContent=Object.entries(syncResult.sources).map(([id,evidence])=>sources.get(id)+' · '+(evidence.status==='accepted'?(Number(syncResult.offsets[id]).toFixed(3)+'초'):'확인 필요 · '+(evidence.reason||evidence.code||evidence.status))).join('\n');
      say('싱크 분석 완료 · 확인이 필요한 소스를 검토하세요.');
    }else if(active.kind==='example'){
      const sample=value.result;
      if(stopped||!analysisState||analysisState.analysisId!==active.analysisId||analysisState.revision!==active.revision||sample.analysisId!==active.analysisId||sample.revision!==active.revision)throw new Error('EXAMPLE_SCOPE');
      if(!await ContentriumHost.ppro.SourceMonitor.openFilePath(sample.path))throw new Error('음성 샘플을 열지 못했습니다.');
      if(stopped)throw new Error('CANCELED');
      if(!await ContentriumHost.ppro.SourceMonitor.play(1))throw new Error('음성 샘플을 재생하지 못했습니다.');
      previewPlaying=true;setTimeout(()=>{previewPlaying=false;toggle();},Math.ceil(Number(sample.durationSeconds)*1000)+250);
      say('Premiere 소스 모니터에서 단독 발화를 재생합니다.');
    }else if(active.kind==='input-probe'){
      if(!projectSelection||projectSelection.selectionId!==active.selectionId)throw new Error('INPUT_SCOPE');
      inputCapability=await api('/input/capabilities/result',{jobId:value.jobId,epoch:state.epoch});
      for(const row of selectedRows){const media=inputCapability.assets.find(a=>a.assetId===row.source.assetId);if(!media)throw new Error('INPUT_SCOPE');row.role.value=media.hasVideo?'camera':media.hasAudio?'audio':'exclude';row.audio.checked=!media.hasVideo&&media.hasAudio;row.info.textContent=media.hasVideo?(media.hasAudio?'영상 · 오디오':'영상만 있음'):(media.hasAudio?'오디오만 있음':'지원하는 스트림 없음');}
      say('선택 소스를 확인했습니다. 역할과 출력 오디오를 지정하세요.');
    }else if(active.kind==='model-setup'){
      $('model-install-status').textContent='화자 모델 설치를 마쳤습니다.';say('모델 준비 완료 · 화자 분석을 시작할 수 있습니다.');
    }
  }finally{if(job?.jobId===active.jobId)job=null;canceledJobs.delete(active.jobId);toggle();}
}
function handler(id,fn){
  const serial=!['refresh','check-update','update','cancel','recover-update'].includes(id);
  $(id).onclick=async()=>{if(serial&&pending)return;if(serial){pending=true;toggle();}try{await fn();}catch(e){error(e);}finally{if(serial)pending=false;toggle();}};
}
async function performNative(kind,body,native,receipt,beginPath='/apply/begin'){
  if(updateIntent||stopped||!state?.gateOpen)throw Object.assign(new Error('Update stop is active.'),{code:'UPDATE_IN_PROGRESS'});
  applying=true;stopped=false;await stopPreview();toggle();
  try{return await workflow.run({kind,body,beginPath,native,receipt});}
  finally{applying=batchRunning=false;try{localEditPending=!!await workflow.pending();}catch(_){localEditPending=true;}await refresh();}
}
function savedReceipt(approved,result,source){return {planHash:approved.plan.planHash,sourceSnapshotHash:source.snapshotHash,resultSnapshotHash:result.snapshot.snapshotHash,resultSequenceRef:result.sequenceRef,sourceUnchanged:result.originalUnchanged,readback:{verified:result.audioAndOverlaysUnchanged===true||result.readbackVerified===true},saved:true};}
handler('refresh',async()=>{if(credentials)await refresh();else await initialize({manual:true});});
handler('read-project',()=>readProject());
handler('analyze',async()=>{
  await requireCurrent();await stopPreview();clearAnalysis();
  job={...await api('/jobs',{kind:'analysis',options:microphoneOptions(),epoch:state.epoch}),snapshotHash:connected.snapshot.snapshotHash};say('화자 분석을 시작합니다.');
});
handler('sync',async()=>{
  await requireCurrent();await stopPreview();syncResult=syncJob=null;$('sync-result').textContent='';
  job={...await api('/jobs',{kind:'sync',options:syncOptions(),epoch:state.epoch}),snapshotHash:connected.snapshot.snapshotHash};say('싱크 분석을 시작합니다.');
});
handler('plan',async()=>{
  await requireCurrent();if(!analysisState)throw new Error('화자 분석을 먼저 실행하세요.');
  const currentMapping=mapping(),snapshotHash=connected.snapshot.snapshotHash,epoch=state.epoch,revision=analysisState.revision;
  const next=await api('/plan',{jobId:analysisJob,analysisId:analysisState.analysisId,analysisRevision:revision,mapping:currentMapping,policy:policy(),epoch});
  if(stopped||!state.gateOpen||state.epoch!==epoch||connected?.snapshot.snapshotHash!==snapshotHash||analysisState?.revision!==revision)throw new Error('PLAN_STALE');
  plan=next;planCameraRefs=currentMapping.cameras.map(c=>c.cameraId);renderPlan();view.show('review');scheduleSettings();say(plan.reviews.length?'검토 사항 '+plan.reviews.length+'개를 확인하세요.':'편집안을 검토한 뒤 Premiere에 적용하세요.');
});
handler('apply',async()=>{
  await requireCurrent();if(!plan)throw new Error('편집안을 먼저 만들어 주세요.');
  const source=connected.snapshot,reviewed=plan,refs=planCameraRefs.slice();
  const result=await performNative('edit',{planHash:reviewed.planHash,snapshotHash:source.snapshotHash,epoch:state.epoch},(approved,control)=>ContentriumHost.apply(approved.plan,source,refs,control),(approved,result)=>savedReceipt(approved,result,source));
  clearAnalysis();say('적용 완료 · '+result.sequenceName+' / 원본·오디오 보존 확인');
});
handler('apply-sync',async()=>{
  await requireCurrent();if(!syncJob||!syncResult)throw new Error('싱크 분석을 먼저 실행하세요.');
  const source=connected.snapshot,assets=new Set(syncRows.filter(r=>r.check.checked).map(r=>r.source.assetId)),keys=source.clips.filter(c=>assets.has(c.assetId)).map(c=>c.instanceKey);
  const reviewed=await api('/sync-plan',{jobId:syncJob,selectedClipInstanceKeys:keys,epoch:state.epoch});
  const result=await performNative('sync',{planHash:reviewed.planHash,snapshotHash:source.snapshotHash,epoch:state.epoch},(approved,control)=>ContentriumHost.applySync(approved.plan,source,control),(approved,result)=>savedReceipt(approved,result,source));
  clearAnalysis();syncResult=syncJob=null;say('싱크 적용 완료 · '+result.sequenceName+' / 원본 보존 확인');
});
handler('read-selection',async()=>{
  if(!credentials||!state?.gateOpen||stopped||job||applying||localEditPending)throw new Error('현재 작업 상태를 확인하세요.');
  await heartbeat();projectSelection=null;inputCapability=null;selectedRows.length=0;$('selected-sources').innerHTML='';
  const selection=await ContentriumHost.selectedSources();
  const bound=await api('/input/sources',{projectRef:selection.projectRef,sources:selection.sources,epoch:state.epoch});
  projectSelection={...selection,...bound};
  for(const source of selection.sources){
    const row=element('div',undefined,'source-row'),role=element('select'),audio=checkbox(),info=element('p','스트림 확인 중','hint');
    options(role,[['camera','카메라 영상'],['audio','독립 오디오'],['exclude','제외']]);role.onchange=()=>{if(role.value==='exclude')audio.checked=false;};
    row.appendChild(element('div',source.name,'source-title'));row.appendChild(info);row.appendChild(label('소스 역할',role));row.appendChild(label('이 파일의 오디오를 결과에 출력',audio));$('selected-sources').appendChild(row);selectedRows.push({source,role,audio,info});
  }
  job={...await api('/input/capabilities',{selectionId:bound.selectionId,epoch:state.epoch}),selectionId:bound.selectionId};say('선택한 소스의 미디어 구성을 확인합니다.');
});
handler('create-input',async()=>{
  if(!state?.gateOpen||stopped||!projectSelection||!inputCapability)throw new Error('선택 소스 확인을 먼저 마쳐 주세요.');
  const selection=projectSelection,capability=inputCapability,choices=selectedRows.map(r=>({assetId:r.source.assetId,role:r.role.value,outputAudio:r.role.value!=='exclude'&&r.audio.checked}));
  await heartbeat();
  await performNative('input',{capabilityId:capability.capabilityId,choices,epoch:state.epoch},(approved,control)=>ContentriumHost.createSelectedInput(selection,choices,control),(approved,result)=>({...result.inputReceipt,planHash:approved.planHash}),'/input/begin');
  projectSelection=inputCapability=null;selectedRows.length=0;$('selected-sources').innerHTML='';connected=null;await readProject();say('입력 시퀀스를 만들었습니다. 트랙 설정에서 싱크와 화자 분석을 시작하세요.');
});
handler('save-settings',async()=>{admitted();await uxp.storage.secureStorage.setItem(settingsKey(),JSON.stringify(captureSettings()));say('현재 시퀀스의 설정을 저장했습니다.');});
handler('load-settings',async()=>{admitted();await stopPreview();if(settingsTimer){clearTimeout(settingsTimer);settingsTimer=null;}let settings;binding=true;try{settings=await restoreSavedSettings();clearAnalysis();renderSpeakers(mode==='mixed'?[]:[...new Set(microphoneRows.filter(r=>r.check.checked).map(r=>r.speaker.value))]);}finally{binding=false;}const restored=await restoreSavedAnalysis(settings);say(restored?'저장한 분석과 화자 교정을 불러왔습니다. 편집안을 다시 만들어 주세요.':'저장한 설정을 불러왔습니다. 음성 입력을 다시 분석하세요.');});
handler('add-override',()=>{addOverride();scheduleSettings();});
handler('undo-correction',()=>correct({type:'undo'}));
handler('cancel',async()=>{
  stopped=true;plan=null;await stopPreview();
  const cancellations=[connection.cancelPending()];
  if(job){canceledJobs.add(job.jobId);cancellations.push(api('/jobs/'+job.jobId+'/cancel',{}));}
  const outcomes=await Promise.allSettled(cancellations),failed=outcomes.find(value=>value.status==='rejected');if(failed)throw failed.reason;
  // Only the active host workflow reports its completed batch. Cancellation
  // must never clear an outstanding Adobe transaction from the side.
  say('중단 요청 · 진행 중인 트랜잭션 뒤 추가 편집을 멈춥니다.');
});
handler('recover-apply',async()=>{await workflow.recover();localEditPending=false;localIntentError=null;await refresh();say('중단 작업 기록을 확인했습니다. 결과 시퀀스를 검토한 뒤 새 작업을 시작하세요.');});
handler('check-update',async()=>{await api('/updates/check',{});await refresh();});
handler('update',async()=>{
  if(updateIntent?.inFlight||updateIntent?.accepted)return;
  const candidate=state?.update?.candidate;if(!candidate&&!updateIntent)throw new Error('업데이트를 다시 확인하세요.');
  if(!updateIntent)updateIntent={candidateId:candidate.candidateId,manifestDigest:candidate.manifestDigest,requestId:requestId(),epoch:state.epoch};
  updateIntent.inFlight=true;
  stopped=true;plan=null;if(job)canceledJobs.add(job.jobId);toggle();
  say('Contentrium CUT 작업을 중단하고 업데이트를 시작합니다.');
  // Start the global stop independently of an unresponsive Adobe playback API.
  const intent=updateIntent;
  const start=api('/updates/start',{candidateId:intent.candidateId,manifestDigest:intent.manifestDigest,requestId:intent.requestId});
  stopPreview().catch(()=>say('업데이트를 시작했습니다. 미리보기가 멈추지 않으면 프로젝트를 저장하고 Premiere를 정상 종료하세요.'));
  try{await start;intent.accepted=true;await refresh();}
  catch(e){if(e.code==='UPDATE_CANDIDATE')updateIntent=null;throw e;}
  finally{intent.inFlight=false;toggle();}
});
handler('recover-update',async()=>{await api('/updates/recover',{});await refresh();});
handler('open-model-provider',()=>uxp.shell.openExternal('https://huggingface.co/pyannote/speaker-diarization-community-1','화자 모델 제공자의 이용 조건과 접근 권한을 확인합니다.'));
handler('install-model',async()=>{
  if(!credentials||!state?.gateOpen)throw new Error('편집 연결을 확인하세요.');
  const token=$('model-token').value.trim(),termsAccepted=$('model-terms').checked;$('model-token').value='';
  if(!token||!termsAccepted)throw new Error('접근 토큰과 제공자 이용 조건 동의를 확인하세요.');
  $('model-install-status').textContent='모델 리비전을 확인하고 있습니다.';
  const epoch=state.epoch,revision=await api('/models/community-1/revision',{token,termsAccepted,epoch});
  job=await api('/models/community-1/install',{token,termsAccepted,revision:revision.revision,epoch});$('model-install-status').textContent='로컬 모델 설치 중';
});
async function loadResources(){
  const value=await api('/resources');$('analysis-device').value=value.settings.device;const budget=value.settings.cacheBudgetBytes;$('cache-budget').value=Number.isSafeInteger(budget)&&budget>0?String(budget/1073741824):'';
  $('cache-info').textContent='캐시 '+(value.status.cacheBytes/1073741824).toFixed(2)+' GB · 사용 가능 디스크 '+(value.status.freeDiskBytes/1073741824).toFixed(1)+' GB';resourceLoaded=true;
}
handler('save-resources',async()=>{const settings={device:$('analysis-device').value},raw=$('cache-budget').value.trim();if(raw){const bytes=Math.round(Number(raw)*1073741824);if(!Number.isSafeInteger(bytes)||bytes<=0)throw new Error('캐시 예산을 양수로 입력하세요.');settings.cacheBudgetBytes=bytes;}await api('/resources',{settings,epoch:state.epoch});await loadResources();say('분석 자원 설정을 저장했습니다.');});
handler('prune-cache',async()=>{const value=await api('/resources/prune',{epoch:state.epoch});await loadResources();say('완료된 캐시 '+value.removed.length+'개 정리'+(value.budgetMet?'':' · 보존해야 하는 데이터가 있어 예산을 초과합니다.'));});
handler('release-cache',async()=>{await api('/resources/prune',{epoch:state.epoch,action:'release'});await refresh();say('정리를 종료했습니다. 편집을 계속할 수 있습니다.');});
const showSettings=$('open-settings').onclick;
$('open-settings').onclick=()=>{showSettings();if(credentials&&!resourceLoaded)loadResources().catch(error);};
for(const value of ['separate','mixed'])handler('mode-'+value,async()=>{mode=value;for(const other of ['separate','mixed'])$('mode-'+other).className='mode'+(mode===other?' active':'');clearAnalysis();if(connected)renderSources();scheduleSettings();await refresh();});
for(const id of ['range-start','range-end'])$(id).onchange=()=>{rangeDirty=true;invalidateAnalysis();say('분석할 범위를 변경했습니다. 다음 분석에 적용됩니다.');};
for(const id of ['min-shot','short-turn','overlap','start-camera','reserve-camera'])$(id).onchange=invalidatePlan;
for(const id of ['speaker-count','vad-threshold'])$(id).onchange=invalidateAnalysis;
$('sync-method').onchange=syncMethodChanged;
$('sync-reference').onchange=()=>{syncResult=syncJob=null;scheduleSettings();toggle();};
$('version').textContent=bundle.appVersion;
$('header-version').textContent=bundle.appVersion;
$('update-banner-button').onclick=()=>$('update').onclick();
$('update-later').onclick=()=>{dismissedCandidate=state?.update?.candidate?.candidateId;$('update-banner').className='hidden';};
let initializing=false,retryAt=0,retryDelay=1000,sequencePollAt=0,enrollmentDeadline=0;
async function initialize({manual=false}={}){
  if(initializing||panelContextConflict&&!manual)return;initializing=true;
  if(manual)enrollmentDeadline=0;
  try{
    await connection.connect();credentials=true;panelContextConflict=false;retryDelay=1000;enrollmentDeadline=0;
    try{localEditPending=!!await workflow.pending();localIntentError=null;}catch(e){if(!isIntentReadError(e))throw e;localEditPending=true;localIntentError=e.code;}
    await refresh();await heartbeat();if(localIntentError)error({code:localIntentError});
    if(!localEditPending&&!state?.applyRecovery?.blocked){try{await readProject();}catch(e){say(/PROJECT_REQUIRED|SEQUENCE_REQUIRED/.test(String(e))?'Premiere에서 편집할 시퀀스를 열어 주세요.':String(e));}}
    api('/updates/check',{}).then(refresh).catch(()=>{});
  }catch(e){
    if(e.code==='PANEL_CONTEXT_CONFLICT'){contextConflict();return;}
    credentials=null;retryAt=Date.now()+retryDelay;retryDelay=Math.min(30000,retryDelay*2);setConnection(false,'편집 준비 중');$('boot-status').className='notice';
    if(e.code==='INSTALLATION_PENDING'){
      if(!enrollmentDeadline)enrollmentDeadline=Date.now()+600000;
      setConnection(false,'설치 연결 준비 중');
      if(Date.now()>=enrollmentDeadline)retryAt=Infinity;
      $('boot-status').querySelector('p').textContent=retryAt===Infinity?'설치 연결 대기를 마쳤습니다. Setup을 다시 열고 새로고침해 주세요.':'설치 연결을 준비하고 있습니다. Contentrium CUT Setup 창을 열어 둔 채 잠시 기다려 주세요.';
    }else if(['BOOTSTRAP_INVALID','INSTALLATION_ENROLLMENT_INVALID'].includes(e.code)){
      retryAt=Infinity;setConnection(false,'설치 복구 필요');
      $('boot-status').querySelector('p').textContent='설치 연결 정보를 검증하지 못했습니다. 같은 Contentrium CUT Setup으로 설치 복구를 진행해 주세요.';
    }else $('boot-status').querySelector('p').textContent=e.code==='BOOTSTRAP_MISSING'?'설치 정보를 찾지 못했습니다. Contentrium CUT 설치 복구가 필요합니다.':'편집 기능을 준비하고 있습니다. 잠시 후 자동으로 다시 확인합니다.';
  }finally{initializing=false;toggle();}
}
initialize();
function pollError(e){if(e.code==='PANEL_CONTEXT_CONFLICT')contextConflict();else{setConnection(false,'편집 연결 복구 중');if(e.code==='AUTH_REQUIRED'||e.code==='SESSION_EXPIRED'||!e.code){credentials=null;connection.reset();retryAt=Date.now()+1000;}if(job||applying){stopped=true;error(e);}}}
setInterval(async()=>{
  if(!credentials){if(Date.now()>=retryAt)await initialize();return;}
  try{await periodicHeartbeat();}catch(e){pollError(e);return;}
  if(polling)return;
  polling=true;
  try{await pollJob();await refresh();if(!pending&&!applying&&!job&&!validationCount&&!localEditPending&&!state?.applyRecovery?.blocked&&Date.now()>=sequencePollAt){sequencePollAt=Date.now()+2500;await followSequence();}}
  catch(e){pollError(e);}
  finally{polling=false;}
},1000);
