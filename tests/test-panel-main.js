/* No browser, desktop, network or Adobe process. Drive actual panel handlers. */
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),crypto=require('node:crypto');
function canonical(v){return Array.isArray(v)?'['+v.map(canonical).join(',')+']':v&&typeof v==='object'?'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+canonical(v[k])).join(',')+'}':JSON.stringify(v);}
const hash=v=>crypto.createHash('sha256').update(canonical(v)).digest('hex');
function dom(){
  const nodes=[],ids=new Map();
  class Node{
    constructor(tag,attrs={}){this.tag=tag;this.attrs=attrs;this.id=attrs.id;this.className=attrs.class||'';this.children=[];this.style={};this._value=attrs.value||'';this.textContent='';this.disabled=false;this.checked=false;nodes.push(this);if(this.id)ids.set(this.id,this);}
    get value(){return this._value;}set value(v){this._value=String(v);}
    get firstChild(){return this.children[0]||null;}
    set innerHTML(v){this.children=[];if(this.tag==='select')this._value='';}
    appendChild(e){this.children.push(e);e.parent=this;if(this.tag==='select'&&this.children.length===1)this.value=e.value;return e;}
    insertBefore(e){this.children.unshift(e);e.parent=this;}
    setAttribute(k,v){this.attrs[k]=v;}getAttribute(k){return this.attrs[k];}
    remove(){if(this.parent)this.parent.children=this.parent.children.filter(e=>e!==this);}
    querySelector(tag){return this.children.find(n=>n.tag===tag)||null;}
  }
  for(const m of fs.readFileSync('plugin/index.html','utf8').matchAll(/<([a-z0-9]+)\b([^>]*)>/g)){
    const attrs=Object.fromEntries([...m[2].matchAll(/([\w-]+)="([^"]*)"/g)].map(a=>[a[1],a[2]]));new Node(m[1],attrs);
  }
  ids.get('boot-status').appendChild(new Node('p'));
  for(const [id,value] of Object.entries({'sync-method':'audio','analysis-device':'cpu'}))ids.get(id).value=value;
  const document={getElementById:id=>ids.get(id),createElement:tag=>new Node(tag),querySelectorAll:q=>{
    if(q==='input,select,button.mode')return nodes.filter(n=>['input','select'].includes(n.tag)||n.tag==='button'&&n.className.split(' ').includes('mode'));
    const attr=q.match(/^\[([^\]]+)\]$/);return attr?nodes.filter(n=>n.attrs[attr[1]]!==undefined):[];
  }};
  return {document,nodes,get:id=>ids.get(id)};
}
function storage(){const rows=new Map();return {rows,get length(){return rows.size;},key:i=>[...rows.keys()][i],getItem:async k=>rows.get(k),setItem:async(k,v)=>rows.set(k,v),removeItem:async k=>rows.delete(k)};}
function nativeSnapshot(id='sequence-1'){
  const perFrame=8467200000,snapshot={schemaVersion:1,projectRef:'project-1',sequenceRef:id,projectName:'Owned fixture',sequenceName:id,fps:{num:30,den:1},range:{startFrame:0,endFrame:300},
    tracks:[{trackRef:'video:0',mediaType:'video',index:0,name:'Camera',muted:false},{trackRef:'audio:0',mediaType:'audio',index:0,name:'Mic',muted:false}],
    clips:[{instanceKey:'camera-clip',trackRef:'video:0',mediaType:'video',assetId:'camera',startTicks:'0',endTicks:String(perFrame*300),inTicks:'0'},
      {instanceKey:'mic-clip',trackRef:'audio:0',mediaType:'audio',assetId:'mic',startTicks:'0',endTicks:String(perFrame*300),inTicks:'0'}],
    sources:[{assetId:'camera',canonicalPath:'D:/owned/camera.mov'},{assetId:'mic',canonicalPath:'D:/owned/mic.wav'}],supportFlags:{hostApplyVerified:true}};
  snapshot.snapshotHash=hash(snapshot);return {snapshot,perFrame,sequence:{setPlayerPosition:async()=>true}};
}
async function panel(extra={}){
  const d=dom(),saved=storage(),calls=[],timers=[],timeouts=[],bundle={appVersion:'0.1.0',bundleId:'contentrium-cut-0.1.0',protocolVersion:1};
  const f={...d,saved,calls,timers,timeouts,native:extra.native||nativeSnapshot(),analysisId:'a'.repeat(64),analysisRevision:extra.analysisRevision||0,jobStatus:'completed',jobKind:'analysis',connectCalls:0,resetCalls:0,cancelValidationCalls:0};
  extra.storage?.(saved);
  const state={...bundle,epoch:0,gateOpen:true,compatible:true,stopEpoch:null,models:{silero:{status:'ready'},'community-1':{status:'ready'}},update:{updateState:'IDLE',checkState:'CURRENT',candidate:null},applyRecovery:{blocked:false,records:[]}};
  function analysis(){return {analysisId:f.analysisId,revision:f.analysisRevision,snapshotHash:f.bound.snapshotHash,names:{A:f.analysisRevision?'진행자':'A'},history:f.analysisRevision?[{revision:1,operation:{type:'name'}}]:[],examples:[],analysis:{sessionSpeakerIds:extra.speakerIds||['A'],intervals:[],unresolvedSpeakerIds:[]}};}
  const request=async(path,body)=>{
    calls.push({path,body});if(extra.request){const result=await extra.request(path,body,f);if(result!==undefined)return result;}
    if(path==='/state')return structuredClone(state);
    if(path==='/heartbeat')return {gateOpen:true,stopEpoch:null};
    if(path==='/project'){f.bound=structuredClone(body.snapshot);return {snapshotHash:body.snapshot.snapshotHash};}
    if(path==='/jobs'){f.jobKind=body.kind;return {jobId:'job-1',kind:body.kind,status:'running'};}
    if(path==='/jobs/job-1')return {jobId:'job-1',kind:f.jobKind,status:f.jobStatus,result:{}};
    if(path==='/analyses/register'||path==='/analyses/'+'a'.repeat(64))return analysis();
    if(path.endsWith('/correct')){f.analysisRevision++;return analysis();}
    if(path==='/plan')return f.planResult||{planHash:'p'.repeat(64),snapshotHash:f.bound.snapshotHash,segments:[{startFrame:0,endFrame:300,cameraId:'video:0',reason:'speech'}],reviews:[]};
    if(path==='/resources')return {settings:{device:'cpu',cacheBudgetBytes:1073741824},status:{cacheBytes:0,freeDiskBytes:10737418240}};
    return {};
  };
  const host={hash,snapshot:async()=>{if(f.snapshotError)throw new Error(f.snapshotError);return {snapshot:structuredClone(f.native.snapshot),perFrame:f.native.perFrame,sequence:f.native.sequence};},ppro:{SourceMonitor:{play:async()=>true,openFilePath:async()=>true}},time:v=>v};
  const uxp={storage:{secureStorage:saved},shell:{openExternal:async()=>true}};
  const context=vm.createContext({document:d.document,crypto:crypto.webcrypto,TextDecoder,Uint8Array,BigInt,Map,Set,Date:extra.Date||Date,JSON,console,
    setInterval:fn=>{timers.push(fn);return timers.length;},setTimeout:fn=>{timeouts.push(fn);return timeouts.length;},clearTimeout:id=>{timeouts[id-1]=null;},ContentriumHost:host,
      require:name=>name==='uxp'?uxp:name==='./bundle.json'?bundle:name==='./connection.js'?{create:(_uxp,_bundle,options)=>{f.validation=options?.onValidation;return {connect:async()=>{f.connectCalls++;return extra.connect?extra.connect(f):{};},request,reset:()=>{f.resetCalls++;},cancelPending:async()=>{f.cancelValidationCalls++;}};}}:
      ['./sync.js','./selection.js'].includes(name)?{install:x=>x}:require('../plugin/'+name.replace('./',''))});
  vm.runInContext(fs.readFileSync('plugin/main.js','utf8'),context);
  for(let i=0;i<40;i++)await Promise.resolve();
  f.click=async id=>{assert.ok(d.get(id),id);await d.get(id).onclick();};f.tick=()=>timers[0]();f.host=host;f.state=state;f.evaluate=source=>vm.runInContext(source,context);
  f.reviewPlan=async expression=>{f.planResult=f.evaluate('('+expression+')');await f.click('analyze');await f.tick();await f.click('plan');};
  return f;
}

async function updatePanel(extra={}){
  const f=await panel(extra);
  f.state.update={updateState:'IDLE',updateEpoch:0,checkState:'AVAILABLE',candidate:{candidateId:'release:hash',manifestDigest:'a'.repeat(64),appVersion:'0.1.1'}};
  await f.tick();return f;
}

function mic(f,index=0){return f.evaluate('microphoneRows['+index+']');}
function micError(f,index=0){return f.get('microphones').children[index]?.children.find(n=>n.className.includes('input-error'))?.textContent||'';}
function syncRow(f,index=0){return f.evaluate('syncRows['+index+']');}

async function analyzedPanel(extra={}){const f=await panel(extra);await f.click('analyze');await f.tick();return f;}
async function overridePanel(extra={}){const f=await analyzedPanel(extra);await f.click('add-override');return f;}
function overrideRow(f,index=0){return f.evaluate('overrideRows['+index+']');}
function manualToggle(f){return f.nodes.find(n=>n.attrs['data-disclosure']==='disclosure-6');}
function manualVisible(f,index){return !f.get('overrides').children[index].className.split(/\s+/).includes('hidden');}

function manualDelete(f,index=0){return f.get('overrides').children[index].children.find(n=>n.tag==='button');}
function rawManual(f){return f.evaluate('JSON.stringify(overrideRows.map(r=>({first:r.first.value,last:r.last.value,camera:r.camera.value})))');}

test('recording mode preserves source DOM raw settings protected cameras and policy choices',async()=>{
  const f=await overridePanel({native:twoCameraNative()}),r=mic(f),camera=f.evaluate('cameraRows[1]'),calibration=f.evaluate('calibrationRows[0]'),sync=syncRow(f);r.speaker.value=' 진행자 ';r.channel.value=' ';r.stream.value='2';r.channel.oninput();camera.role.value='protected';camera.role.onchange();calibration.first.value='';calibration.last.value='90';sync.stream.value='2';sync.channel.value='';overrideRow(f).first.value=' ';f.get('start-camera').value='video:0';
  for(const id of ['mode-mixed','mode-separate']){await f.click(id);assert.equal(mic(f),r);assert.equal(f.evaluate('cameraRows[1]'),camera);assert.equal(f.evaluate('calibrationRows[0]'),calibration);assert.equal(syncRow(f),sync);assert.equal(r.speaker.value,' 진행자 ');assert.equal(r.channel.value,' ');assert.equal(r.stream.value,'2');assert.equal(camera.role.value,'protected');assert.equal(calibration.first.value,'');assert.equal(sync.channel.value,'');assert.equal(overrideRow(f).first.value,' ');assert.equal(f.get('start-camera').value,'video:0');assert.equal(f.evaluate('cameraValues().some(v=>v[0]==="video:1")'),false);}
});
function threeMicNative(){const native=nativeSnapshot();for(let i=1;i<3;i++){native.snapshot.tracks.push({trackRef:'audio:'+i,mediaType:'audio',index:i,name:'Mic'+i,muted:false});native.snapshot.clips.push({...native.snapshot.clips[1],instanceKey:'mic-'+i,trackRef:'audio:'+i,assetId:'mic'+i});native.snapshot.sources.push({assetId:'mic'+i,canonicalPath:'D:/owned/mic'+i+'.wav'});}delete native.snapshot.snapshotHash;native.snapshot.snapshotHash=hash(native.snapshot);return native;}
function checkedMics(f){return JSON.parse(f.evaluate('JSON.stringify(microphoneRows.map(r=>r.check.checked))'));}
test('mode defaults follow untouched selection but preserve explicit and restored choices',async()=>{
  const f=await panel({native:threeMicNative()});assert.deepEqual(checkedMics(f),[true,true,false]);mic(f).speaker.value='진행자';mic(f).speaker.oninput();await f.click('mode-mixed');assert.deepEqual(checkedMics(f),[true,false,false]);assert.equal(mic(f).speaker.value,'진행자');await f.click('mode-separate');assert.deepEqual(checkedMics(f),[true,true,false]);mic(f,1).check.checked=false;mic(f,1).check.onchange();mic(f,2).check.checked=true;mic(f,2).check.onchange();await f.click('mode-mixed');assert.deepEqual(checkedMics(f),[true,false,true]);await f.click('mode-separate');assert.deepEqual(checkedMics(f),[true,false,true]);
  const g=await panel({native:threeMicNative()}),saved=g.evaluate('JSON.stringify(captureSettings())');g.evaluate('restoreSettings('+saved+')');await g.click('mode-mixed');assert.deepEqual(checkedMics(g),[true,true,false]);
});
test('same mode is inert and direct locked mode callbacks retain analysis plan raw and save timer',async()=>{
  const f=await analyzedPanel();await f.click('plan');const id=f.evaluate('analysisState.analysisId'),plan=f.evaluate('plan.planHash'),timer=f.evaluate('settingsTimer');await f.click('mode-separate');assert.equal(f.evaluate('analysisState.analysisId'),id);assert.equal(f.evaluate('plan.planHash'),plan);assert.equal(f.evaluate('settingsTimer'),timer);
  for(const lock of ['pending=true','localEditPending=true','state.applyRecovery.blocked=true','updateIntent={inFlight:true}','state.compatible=false','job={jobId:"owned"}','validationCount=1','credentials=null']){const g=await analyzedPanel();g.evaluate(lock+';toggle()');assert.equal(g.get('mode-mixed').disabled,true,lock);const raw=g.evaluate('JSON.stringify(microphoneOptions())'),saved=g.evaluate('settingsTimer');await g.click('mode-mixed');assert.equal(g.evaluate('mode'),'separate',lock);assert.equal(g.evaluate('analysisState.analysisId'),g.analysisId,lock);assert.equal(g.evaluate('JSON.stringify(microphoneOptions())'),raw);assert.equal(g.evaluate('settingsTimer'),saved);}
});
test('mode transitions clear identity bound camera links and keep pending update immediate',async()=>{
  const f=await analyzedPanel();f.evaluate('cameraRows[0].covered.value="A"');await f.click('mode-mixed');assert.equal(f.evaluate('cameraRows[0].covered.value'),'');assert.equal(f.evaluate('analysisState'),null);await f.click('analyze');await f.tick();f.evaluate('cameraRows[0].covered.value="A";speakerRows[0].select.value="video:0";speakerRows[0].select.onchange()');await f.click('mode-separate');assert.equal(f.evaluate('cameraRows[0].covered.value'),'');assert.equal(f.evaluate('analysisState'),null);
  const g=await updatePanel();let resume;g.host.snapshot=()=>new Promise(resolve=>{resume=resolve;});const run=g.click('read-project');for(let i=0;i<20;i++)await Promise.resolve();await g.click('mode-mixed');assert.equal(g.evaluate('mode'),'separate');await g.click('update');assert.equal(g.calls.filter(c=>c.path==='/updates/start').length,1);resume({snapshot:structuredClone(g.native.snapshot),perFrame:g.native.perFrame,sequence:g.native.sequence});await run;
});
test('mode applies preserved settings before metadata wait while update remains immediate',async()=>{
  let held=false,resume;const f=await updatePanel({request:(path,body,fixture)=>{if(held&&path==='/state'){held=false;return new Promise(resolve=>{resume=()=>resolve(structuredClone(fixture.state));});}}});mic(f).stream.value='2';const original=mic(f);held=true;const run=f.click('mode-mixed');for(let i=0;i<20;i++)await Promise.resolve();assert.equal(typeof resume,'function');assert.equal(f.evaluate('mode'),'mixed');assert.equal(mic(f),original);assert.equal(mic(f).stream.value,'2');assert.equal(f.get('mode-separate').disabled,true);await f.click('update');assert.equal(f.calls.filter(c=>c.path==='/updates/start').length,1);resume();await run;assert.equal(f.get('analyze').disabled,true);
});
test('mode local errors are shown without leaking pending and new sequence starts with fresh selection',async()=>{
  const f=await panel({native:threeMicNative()});mic(f,2).check.checked=true;mic(f,2).check.onchange();await f.click('mode-mixed');const native=threeMicNative();native.snapshot.sequenceRef='new-owned-sequence';delete native.snapshot.snapshotHash;native.snapshot.snapshotHash=hash(native.snapshot);f.native=native;await f.click('read-project');assert.deepEqual(checkedMics(f),[true,false,false]);await f.click('mode-separate');assert.deepEqual(checkedMics(f),[true,true,false]);
  const g=await panel();g.evaluate('renderSpeakers=()=>{throw new Error("owned mode error")}');await g.click('mode-mixed');assert.match(g.get('status').textContent,/owned mode error/);assert.equal(g.evaluate('pending'),false);
});
test('solo calibration blocks invalid raw and clip bounds before analysis but keeps zero zero optional',async()=>{
  const f=await panel(),r=f.evaluate('calibrationRows[0]');assert.equal(f.get('analyze').disabled,false);assert.equal(f.evaluate('microphoneOptions().calibration.length'),0);
  for(const [first,last] of [['','90'],[' ','90'],['NaN','90'],['0','Infinity'],['0.5','90'],['-1','90'],['150','90'],['90','90'],['0','301'],['0','']]){r.first.value=first;r.last.value=last;f.evaluate('toggle()');assert.equal(f.get('analyze').disabled,true,first+'/'+last);assert.equal(r.first.getAttribute('aria-invalid'),'true');assert.ok(r.issue.textContent);const n=f.calls.filter(c=>c.path==='/jobs').length;await f.click('analyze');assert.equal(f.calls.filter(c=>c.path==='/jobs').length,n);}
  r.first.value='0';r.last.value='300';r.first.oninput();assert.equal(f.get('analyze').disabled,false);await f.click('analyze');assert.equal(f.calls.filter(c=>c.path==='/jobs').at(-1).body.options.calibration[0].endFrame,300);
});
test('solo calibration edits invalidate immediately preserve raw restore and obey work locks',async()=>{
  const f=await analyzedPanel(),r=f.evaluate('calibrationRows[0]');await f.click('plan');r.first.value='';r.last.value='90';r.first.oninput();assert.equal(f.evaluate('analysisState'),null);assert.equal(f.evaluate('plan'),null);const saved=f.evaluate('JSON.stringify(captureSettings())');r.first.value='0';f.evaluate('restoreSettings('+saved+');toggle()');assert.equal(r.first.value,'');assert.equal(r.last.value,'90');assert.equal(f.get('analyze').disabled,true);
  for(const lock of ['localEditPending=true','pending=true','state.compatible=false','updateIntent={inFlight:true}']){const g=await analyzedPanel(),row=g.evaluate('calibrationRows[0]');g.evaluate(lock+';toggle()');assert.equal(row.first.disabled,true,lock);const id=g.evaluate('analysisState.analysisId'),timer=g.evaluate('settingsTimer');row.first.oninput();assert.equal(g.evaluate('analysisState.analysisId'),id);assert.equal(g.evaluate('settingsTimer'),timer);}
});
test('mixed and unselected calibration is disabled ignored and cannot discard completed analysis',async()=>{
  for(const mode of ['separate','mixed']){const f=await panel();if(mode==='mixed')await f.click('mode-mixed');else {mic(f).check.checked=false;mic(f).check.onchange();}const r=f.evaluate('calibrationRows[0]');assert.equal(r.first.disabled,true);r.first.value='';r.last.value='90';r.first.oninput();assert.equal(r.first.getAttribute('aria-invalid'),'false');assert.equal(f.evaluate('microphoneOptions().calibration.length'),0);if(mode==='mixed'){await f.click('analyze');await f.tick();const id=f.evaluate('analysisState.analysisId');r.last.value='';r.last.oninput();assert.equal(f.evaluate('analysisState.analysisId'),id);}}
});
test('solo calibration permits examples outside selected analysis range but within exact clip ticks',async()=>{
  const f=await panel(),r=f.evaluate('calibrationRows[0]');f.evaluate('connected.snapshot.range={startFrame:120,endFrame:300}');r.first.value='0';r.last.value='90';r.first.oninput();assert.equal(f.get('analyze').disabled,false);
  f.evaluate("calibrationRows[0].clip.startTicks='1';calibrationRows[0].clip.endTicks=String(BigInt(connected.perFrame)*300n-1n)");r.first.value='0';r.last.value='299';r.first.oninput();assert.equal(f.get('analyze').disabled,true);r.first.value='1';r.last.value='300';r.first.oninput();assert.equal(f.get('analyze').disabled,true);r.last.value='299';r.last.oninput();assert.equal(f.get('analyze').disabled,false);
});
test('legacy full calibration hash restores mixed analysis and malformed active restore is rejected',async()=>{
  const f=await panel();await f.click('mode-mixed');let r=f.evaluate('calibrationRows[0]');r.first.value='0';r.last.value='90';r.first.oninput();await f.click('analyze');await f.tick();const saved=JSON.parse(f.evaluate('JSON.stringify(captureSettings())'));saved.analysisReference.inputHash=hash({...f.evaluate('microphoneOptions()'),calibration:f.evaluate('calibrationOptions(true)')});f.evaluate('clearAnalysis();restoreSettings('+JSON.stringify(saved)+')');assert.equal(await f.evaluate('restoreSavedAnalysis('+JSON.stringify(saved)+')'),true);assert.equal(f.evaluate('captureSettings().analysisReference.inputHash'),hash(f.evaluate('microphoneOptions()')));
  const g=await analyzedPanel(),bad=JSON.parse(g.evaluate('JSON.stringify(captureSettings())'));bad.calibration[0].first='';bad.calibration[0].last='90';g.evaluate('clearAnalysis();restoreSettings('+JSON.stringify(bad)+')');bad.analysisReference.inputHash=hash(g.evaluate('microphoneOptions()'));const before=g.calls.length;await assert.rejects(g.evaluate('restoreSavedAnalysis('+JSON.stringify(bad)+')'));assert.equal(g.calls.slice(before).some(c=>c.path.startsWith('/jobs/')||c.path.startsWith('/analyses/')),false);
});
test('calibration async invalidation and update click never submit a stale job',async()=>{
  for(const update of [false,true]){const f=await updatePanel();let resume;f.host.snapshot=()=>new Promise(resolve=>{resume=resolve;});const run=f.click('analyze');for(let i=0;i<20;i++)await Promise.resolve();const r=f.evaluate('calibrationRows[0]');r.first.value='';r.last.value='90';if(update){await f.click('update');assert.equal(f.calls.filter(c=>c.path==='/updates/start').length,1);}resume({snapshot:structuredClone(f.native.snapshot),perFrame:f.native.perFrame,sequence:f.native.sequence});await run;assert.equal(f.calls.filter(c=>c.path==='/jobs').length,0);}
});
test('fractional FPS calibration compares exact tick boundaries without rounding out of the clip',async()=>{
  const native=nativeSnapshot();native.perFrame=8475667200;native.snapshot.fps={num:30000,den:1001};for(const clip of native.snapshot.clips)clip.endTicks=String(BigInt(native.perFrame)*300n);native.snapshot.snapshotHash=hash(Object.fromEntries(Object.entries(native.snapshot).filter(([k])=>k!=='snapshotHash')));const f=await panel({native}),r=f.evaluate('calibrationRows[0]');r.first.value='0';r.last.value='300';r.first.oninput();assert.equal(f.get('analyze').disabled,false);r.clip.endTicks=String(BigInt(native.perFrame)*300n-1n);r.last.oninput();assert.equal(f.get('analyze').disabled,true);r.last.value='299';r.last.oninput();assert.equal(f.get('analyze').disabled,false);
});
test('mode transitions retain calibration raw by clip and validate again when active',async()=>{
  for(const [first,last] of [['10','90'],['','90'],['0','0']]){const f=await panel(),r=f.evaluate('calibrationRows[0]');r.first.value=first;r.last.value=last;r.first.oninput();await f.click('mode-mixed');let next=f.evaluate('calibrationRows[0]');assert.equal(next.first.value,first);assert.equal(next.last.value,last);assert.equal(next.first.disabled,true);assert.equal(next.first.getAttribute('aria-invalid'),'false');await f.click('mode-separate');next=f.evaluate('calibrationRows[0]');assert.equal(next.first.value,first);assert.equal(next.last.value,last);assert.equal(next.first.disabled,false);assert.equal(f.get('analyze').disabled,first==='');assert.equal(f.evaluate('analysisState'),null);}
});
test('active analysis scalars reject blank nonfinite and out of range before job submission',async()=>{
  for(const [mode,id,values] of [['separate','vad-threshold',['',' ','NaN','Infinity','0','0.049','0.951']],['mixed','speaker-count',['',' ','NaN','Infinity','0','1.5','27']]]){
    const f=await panel();if(mode==='mixed')await f.click('mode-mixed');
    for(const value of values){f.get(id).value=value;f.evaluate('toggle()');const count=f.calls.filter(c=>c.path==='/jobs').length;assert.equal(f.get('analyze').disabled,true,value);assert.equal(f.get(id).getAttribute('aria-invalid'),'true');assert.ok(f.get(id+'-error').textContent);await f.click('analyze');assert.equal(f.calls.filter(c=>c.path==='/jobs').length,count,value);}
  }
});
test('inactive analysis settings are disabled ignored and preserve completed analysis identity',async()=>{
  for(const [mode,id,active,boundaries] of [['separate','speaker-count','vad-threshold',['0.05','0.95']],['mixed','vad-threshold','speaker-count',['1','26']]]){
    const f=await panel();if(mode==='mixed')await f.click('mode-mixed');assert.equal(f.get(id).disabled,true);f.get(id).value='';f.get(id).oninput();
    for(const value of boundaries){f.get(active).value=value;f.get(active).oninput();assert.equal(f.get('analyze').disabled,false);await f.click('analyze');await f.tick();const identity=f.evaluate('analysisState.analysisId'),options=JSON.stringify(f.evaluate('microphoneOptions()'));f.get(id).value='Infinity';f.get(id).oninput();assert.equal(f.evaluate('analysisState.analysisId'),identity);assert.equal(JSON.stringify(f.evaluate('microphoneOptions()')),options);assert.equal(f.get(id).getAttribute('aria-invalid'),'false');}
  }
});
test('analysis scalar raw restore preserves blanks numeric zero and input invalidates analysis immediately',async()=>{
  const f=await analyzedPanel(),settings=JSON.parse(f.evaluate('JSON.stringify(captureSettings())'));settings.vadThreshold='';settings.speakerCount=' ';f.evaluate('restoreSettings('+JSON.stringify(settings)+');toggle()');assert.equal(f.get('vad-threshold').value,'');assert.equal(f.get('speaker-count').value,' ');assert.equal(f.get('analyze').disabled,true);
  settings.vadThreshold=0;settings.speakerCount=0;f.evaluate('restoreSettings('+JSON.stringify(settings)+');toggle()');assert.equal(f.get('vad-threshold').value,'0');assert.equal(f.get('speaker-count').value,'0');
  f.get('vad-threshold').value='0.5';f.get('vad-threshold').oninput();await f.click('analyze');await f.tick();await f.click('plan');f.get('vad-threshold').value='';f.get('vad-threshold').oninput();assert.equal(f.evaluate('analysisState'),null);assert.equal(f.evaluate('plan'),null);assert.equal(f.evaluate('captureSettings().vadThreshold'),'');assert.equal(f.get('analyze').disabled,true);
});
test('analysis scalar guard rechecks after async sequence read and update stays immediate',async()=>{
  const f=await updatePanel();let resume;f.host.snapshot=()=>new Promise(resolve=>{resume=resolve;});const operation=f.click('analyze');for(let i=0;i<20;i++)await Promise.resolve();f.get('vad-threshold').value='';await f.click('update');assert.equal(f.calls.filter(c=>c.path==='/updates/start').length,1);const timer=f.evaluate('settingsTimer');f.get('vad-threshold').oninput();assert.equal(f.evaluate('settingsTimer'),timer);resume({snapshot:structuredClone(f.native.snapshot),perFrame:f.native.perFrame,sequence:f.native.sequence});await operation;assert.equal(f.calls.filter(c=>c.path==='/jobs').length,0);
  const g=await panel();let go;g.host.snapshot=()=>new Promise(resolve=>{go=resolve;});const run=g.click('analyze');for(let i=0;i<20;i++)await Promise.resolve();g.get('vad-threshold').value='';go({snapshot:structuredClone(g.native.snapshot),perFrame:g.native.perFrame,sequence:g.native.sequence});await run;assert.equal(g.calls.filter(c=>c.path==='/jobs').length,0);
});

test('legacy scalar analysis reference restores only matching valid active inputs then migrates hash',async()=>{
  const f=await analyzedPanel();f.get('speaker-count').value='9';const settings=JSON.parse(f.evaluate('JSON.stringify(captureSettings())'));settings.analysisReference.inputHash=hash({...f.evaluate('microphoneOptions()'),speakerCount:9});f.evaluate('clearAnalysis();restoreSettings('+JSON.stringify(settings)+')');assert.equal(await f.evaluate('restoreSavedAnalysis('+JSON.stringify(settings)+')'),true);assert.equal(f.evaluate('analysisState.analysisId'),f.analysisId);assert.equal(f.evaluate('captureSettings().analysisReference.inputHash'),hash(f.evaluate('microphoneOptions()')));
  const bad=structuredClone(settings);bad.vadThreshold='';f.evaluate('clearAnalysis();restoreSettings('+JSON.stringify(bad)+')');const before=f.calls.length;await assert.rejects(f.evaluate('restoreSavedAnalysis('+JSON.stringify(bad)+')'));assert.equal(f.calls.slice(before).some(c=>c.path.startsWith('/analyses/')||c.path.startsWith('/jobs/')),false);
  f.evaluate('restoreSettings('+JSON.stringify(settings)+')');settings.analysisReference.inputHash='0'.repeat(64);await assert.rejects(f.evaluate('restoreSavedAnalysis('+JSON.stringify(settings)+')'));assert.equal(f.evaluate('analysisState'),null);
});
test('analysis scalar callbacks honor direct update recovery and pending locks',async()=>{
  for(const state of ['pending=true','updateIntent={inFlight:true}','localEditPending=true','state.applyRecovery.blocked=true','job={jobId:"owned"}','validationCount=1']){const f=await analyzedPanel();f.evaluate(state);const id=f.evaluate('analysisState.analysisId'),timer=f.evaluate('settingsTimer');f.get('vad-threshold').value='';f.get('vad-threshold').oninput();f.get('vad-threshold').onchange();assert.equal(f.evaluate('analysisState.analysisId'),id,state);assert.equal(f.evaluate('settingsTimer'),timer,state);}
});
test('active scalar controls reflect recovery compatibility and update intent locks',async()=>{
  for(const condition of ['localEditPending=true','state.applyRecovery.blocked=true','state.compatible=false','updateIntent={inFlight:true}']){const f=await analyzedPanel();f.evaluate(condition+';toggle()');assert.equal(f.get('vad-threshold').disabled,true,condition);assert.equal(f.get('speaker-count').disabled,true,condition);f.evaluate('localEditPending=false;state.applyRecovery.blocked=false;state.compatible=true;updateIntent=null;toggle()');assert.equal(f.get('vad-threshold').disabled,false);assert.equal(f.get('speaker-count').disabled,true);}
});
test('update click locks manual add and delete and direct handlers preserve all raw rows',async()=>{
  const f=await updatePanel();await f.click('add-override');await f.click('add-override');overrideRow(f).first.value=' ';overrideRow(f).first.oninput();await f.click('override-filter');const remove=manualDelete(f);
  await f.click('update');const before=rawManual(f),timer=f.evaluate('settingsTimer'),display=f.get('override-summary').textContent;assert.equal(f.calls.filter(c=>c.path==='/updates/start').length,1);
  assert.equal(f.get('add-override').disabled,true);assert.equal(remove.disabled,true);await f.click('add-override');remove.onclick();assert.equal(rawManual(f),before);assert.equal(f.evaluate('settingsTimer'),timer);assert.equal(f.get('override-summary').textContent,display);assert.equal(f.get('plan').disabled,true);
});

test('manual edit locks cover busy recovery unsupported authentication and disconnected states',async()=>{
  const states=[['validation',f=>f.validation(1)],['job',f=>f.evaluate("job={jobId:'owned-running',kind:'analysis'};toggle()")],['applying',f=>f.evaluate('applying=true;toggle()')],['recovery',f=>f.evaluate('localEditPending=true;toggle()')],['server recovery',f=>f.evaluate('state.applyRecovery.blocked=true;toggle()')],['unsupported',f=>f.evaluate('state.compatible=false;toggle()')],['stopped',f=>f.evaluate('stopped=true;toggle()')],['gate closed',f=>f.evaluate('state.gateOpen=false;toggle()')],['credentials lost',f=>f.evaluate('credentials=null;toggle()')],['disconnected',f=>f.evaluate('connected=null;toggle()')],['intent only',f=>f.evaluate('updateIntent={inFlight:true};stopped=false;state.gateOpen=true;toggle()')]];
  for(const [name,lock] of states){const f=await overridePanel();overrideRow(f).first.value=' ';overrideRow(f).first.oninput();const remove=manualDelete(f);lock(f);const before=rawManual(f),timer=f.evaluate('settingsTimer');assert.equal(f.get('add-override').disabled,true,name);assert.equal(remove.disabled,true,name);await f.click('add-override');remove.onclick();assert.equal(rawManual(f),before,name);assert.equal(f.evaluate('settingsTimer'),timer,name);}
});

test('pending project read blocks manual deletion and addition until the actual response completes',async()=>{
  const f=await overridePanel(),remove=manualDelete(f);let resume;f.host.snapshot=()=>new Promise(resolve=>{resume=resolve;});const operation=f.click('read-project');for(let i=0;i<20;i++)await Promise.resolve();assert.equal(typeof resume,'function');const before=rawManual(f),timer=f.evaluate('settingsTimer');assert.equal(remove.disabled,true);assert.equal(f.get('add-override').disabled,true);
  await f.click('add-override');remove.onclick();assert.equal(rawManual(f),before);assert.equal(f.evaluate('settingsTimer'),timer);resume({snapshot:structuredClone(f.native.snapshot),perFrame:f.native.perFrame,sequence:f.native.sequence});await operation;assert.equal(f.get('add-override').disabled,false);assert.equal(manualDelete(f).disabled,false);
});

test('stale or repeated manual deletion never removes another current row or schedules settings',async()=>{
  const f=await overridePanel();await f.click('add-override');overrideRow(f,1).first.value='120';overrideRow(f,1).first.oninput();const oldDelete=manualDelete(f).onclick;oldDelete();const before=rawManual(f),timer=f.evaluate('settingsTimer');oldDelete();assert.equal(rawManual(f),before);assert.equal(f.evaluate('settingsTimer'),timer);assert.equal(f.evaluate('overrideRows.length'),1);
  const detached=manualDelete(f).onclick,settings=JSON.parse(f.evaluate('JSON.stringify(captureSettings())'));f.evaluate('restoreSettings('+JSON.stringify(settings)+');toggle()');const restored=rawManual(f),restoredTimer=f.evaluate('settingsTimer');detached();assert.equal(rawManual(f),restored);assert.equal(f.evaluate('settingsTimer'),restoredTimer);
});

test('internal manual batch recovery remains available while public edits are pending locked',async()=>{
  const f=await analyzedPanel(),settings=JSON.parse(f.evaluate('JSON.stringify(captureSettings())'));settings.overrideInput=[{first:' ',last:'300',camera:'video:0'},{first:'120',last:'300',camera:'video:0'}];f.evaluate('pending=true;restoreSettings('+JSON.stringify(settings)+');toggle()');assert.equal(f.evaluate('overrideRows.length'),2);assert.equal(overrideRow(f).first.value,' ');assert.equal(overrideRow(f,1).first.value,'120');assert.equal(f.get('add-override').disabled,true);assert.equal(manualDelete(f,1).disabled,true);
  f.evaluate('pending=false;toggle()');assert.equal(f.get('add-override').disabled,false);assert.equal(manualDelete(f,1).disabled,false);
});

test('allowed manual edits invalidate the plan preserve analysis and save raw settings',async()=>{
  const f=await analyzedPanel();await f.click('plan');await f.click('add-override');assert.equal(f.evaluate('overrideRows.length'),1);assert.equal(f.get('apply').disabled,true);assert.equal(f.evaluate('analysisState!==null'),true);assert.equal(f.get('add-override').disabled,false);assert.equal(manualDelete(f).disabled,false);overrideRow(f).first.value='120';overrideRow(f).first.oninput();await f.click('plan');assert.equal(f.get('apply').disabled,false);
  manualDelete(f).onclick();assert.equal(f.evaluate('overrideRows.length'),0);assert.equal(f.get('apply').disabled,true);assert.equal(f.evaluate('analysisState!==null'),true);for(let i=0;i<f.timeouts.length;i++){const fn=f.timeouts[i];f.timeouts[i]=null;if(fn)await fn();}const key=[...f.saved.rows.keys()].find(k=>k.startsWith('cut-settings-'));assert.deepEqual(JSON.parse(f.saved.rows.get(key)).overrideInput,[]);
});

test('new manual delete controls are born locked during internal pending restore before later toggles',async()=>{
  for(const lock of ['pending=true','updateIntent={inFlight:true};stopped=true']){const f=await analyzedPanel(),settings=JSON.parse(f.evaluate('JSON.stringify(captureSettings())'));settings.overrideInput=[{first:' ',last:'300',camera:'video:0'}];f.evaluate(lock+';toggle();restoreSettings('+JSON.stringify(settings)+')');assert.equal(f.get('add-override').disabled,true);assert.equal(manualDelete(f).disabled,true);for(const field of [overrideRow(f).first,overrideRow(f).last,overrideRow(f).camera])assert.equal(field.disabled,true,'restored input is locked before later toggle');const before=rawManual(f);manualDelete(f).onclick();assert.equal(rawManual(f),before);}
});

test('collapsed manual summary identifies restored invalid rows without forcing disclosure open',async()=>{
  const f=await analyzedPanel(),settings=JSON.parse(f.evaluate('JSON.stringify(captureSettings())'));
  settings.overrideInput=Array.from({length:7},(_,i)=>({first:i===6?'':'0',last:'300',camera:'video:0'}));f.evaluate('restoreSettings('+JSON.stringify(settings)+');toggle()');
  assert.ok(f.get('override-summary'),'collapsed status exists');assert.match(f.get('override-summary').textContent,/7.*1/);assert.equal(manualToggle(f).getAttribute('aria-expanded'),'false');assert.match(f.get('disclosure-6').className,/hidden/);
  assert.equal(f.get('override-filter').disabled,false);assert.equal(f.get('plan').disabled,true);
});

test('manual error-only display opens the section preserves row identities and keeps all policy and settings',async()=>{
  const f=await overridePanel();await f.click('add-override');await f.click('add-override');overrideRow(f,1).first.value='';overrideRow(f,2).last.value='';overrideRow(f,2).last.oninput();
  const before=f.evaluate('JSON.stringify(captureSettings())'),id=overrideRow(f,2).error.getAttribute('id');await f.click('override-filter');
  assert.equal(manualToggle(f).getAttribute('aria-expanded'),'true');assert.doesNotMatch(f.get('disclosure-6').className,/hidden/);assert.equal(manualVisible(f,0),false);assert.equal(manualVisible(f,1),true);assert.equal(manualVisible(f,2),true);assert.match(overrideRow(f,2).title.textContent,/3/);assert.equal(overrideRow(f,2).error.getAttribute('id'),id);
  assert.match(f.get('override-summary').textContent,/오류 확인 중/);assert.equal(f.evaluate('JSON.stringify(captureSettings())'),before);assert.equal(f.evaluate('policy().overrides.length'),3);assert.equal(f.evaluate('analysisState!==null'),true);
  await f.click('override-filter');assert.equal(manualVisible(f,0),true);assert.equal(manualToggle(f).getAttribute('aria-expanded'),'true');
});

test('correcting manual errors recomputes visible rows and returns to full display once resolved',async()=>{
  const f=await overridePanel();await f.click('add-override');overrideRow(f).first.value='';overrideRow(f,1).last.value='';overrideRow(f,1).last.oninput();await f.click('override-filter');
  overrideRow(f).first.value='0';overrideRow(f).first.oninput();assert.equal(manualVisible(f,0),true);assert.equal(manualVisible(f,1),true);assert.match(f.get('override-summary').textContent,/2.*1/);
  overrideRow(f,1).last.value='300';overrideRow(f,1).last.oninput();assert.equal(manualVisible(f,0),true);assert.equal(manualVisible(f,1),true);assert.doesNotMatch(f.get('override-summary').textContent,/오류 확인 중/);assert.match(f.get('override-filter').className,/hidden/);assert.equal(f.get('plan').disabled,false);
});

test('conflicting camera rows remain visible with original numbers and deletion updates summary',async()=>{
  const f=await overridePanel({native:twoCameraNative()});await f.click('add-override');await f.click('add-override');overrideRow(f,1).camera.value='video:1';overrideRow(f,1).camera.onchange();await f.click('override-filter');
  assert.equal(manualVisible(f,0),true);assert.equal(manualVisible(f,1),true);assert.equal(manualVisible(f,2),false);assert.match(f.get('override-summary').textContent,/3.*2/);
  f.get('overrides').children[1].children.find(n=>n.tag==='button').onclick();assert.equal(f.evaluate('overrideRows.length'),2);assert.equal(manualVisible(f,0),true);assert.equal(manualVisible(f,1),true);assert.match(overrideRow(f,1).title.textContent,/2/);assert.match(f.get('override-summary').textContent,/2/);
});

test('adding and restoring manual rows leave error-only display so valid new rows are visible',async()=>{
  const f=await overridePanel();overrideRow(f).first.value='';overrideRow(f).first.oninput();await f.click('override-filter');await f.click('add-override');assert.equal(manualVisible(f,1),true);assert.doesNotMatch(f.get('override-summary').textContent,/오류 확인 중/);
  await f.click('override-filter');const settings=JSON.parse(f.evaluate('JSON.stringify(captureSettings())'));f.evaluate('restoreSettings('+JSON.stringify(settings)+');toggle()');assert.equal(manualVisible(f,1),true);assert.doesNotMatch(f.get('override-summary').textContent,/오류 확인 중/);
});

test('manual summary remains stable while polling and respects explicit user collapse',async()=>{
  const f=await overridePanel();overrideRow(f).first.value='';overrideRow(f).first.oninput();await f.click('override-filter');await manualToggle(f).onclick();let text=f.get('override-summary').textContent,writes=0;
  Object.defineProperty(f.get('override-summary'),'textContent',{get:()=>text,set:v=>{text=v;writes++;}});await f.tick();await f.tick();assert.equal(writes,0);assert.equal(manualToggle(f).getAttribute('aria-expanded'),'false');assert.match(f.get('disclosure-6').className,/hidden/);
});

test('manual error display is locked during update and cannot delay immediate update admission',async()=>{
  const f=await updatePanel();await f.click('add-override');overrideRow(f).first.value='';overrideRow(f).first.oninput();await f.click('update');assert.equal(f.calls.filter(c=>c.path==='/updates/start').length,1);assert.ok(f.get('override-filter'),'error control exists');assert.equal(f.get('override-filter').disabled,true);
  await f.click('override-filter');assert.equal(manualToggle(f).getAttribute('aria-expanded'),'false');assert.match(f.get('action-readiness').textContent,/업데이트/);
});

test('protected cameras update the error-only view without replacing the stored camera',async()=>{
  const f=await overridePanel({native:twoCameraNative()});await f.click('add-override');overrideRow(f,1).camera.value='video:1';overrideRow(f,1).first.value='';overrideRow(f,1).first.oninput();await f.click('override-filter');
  f.evaluate("cameraRows[0].role.value='protected';cameraRows[0].role.onchange()");assert.equal(manualVisible(f,0),true);assert.equal(manualVisible(f,1),true);assert.equal(overrideRow(f).camera.value,'video:0');assert.match(f.get('override-summary').textContent,/2.*2/);
});

test('manual error display locks for validation and recovery without changing collapse choice',async()=>{
  for(const kind of ['validation','recovery']){const f=await overridePanel();overrideRow(f).first.value='';overrideRow(f).first.oninput();if(kind==='validation')f.validation(1);else {f.state.applyRecovery.blocked=true;await f.tick();}
    assert.equal(f.get('override-filter').disabled,true);await f.click('override-filter');assert.equal(manualToggle(f).getAttribute('aria-expanded'),'false');assert.match(f.get('action-readiness').textContent,kind==='validation'?/원본/:/중단 작업/);}
});

test('switching sequences clears temporary manual error display and old rows',async()=>{
  const f=await overridePanel();overrideRow(f).first.value='';overrideRow(f).first.oninput();await f.click('override-filter');f.native=nativeSnapshot('sequence-2');await f.click('read-project');
  assert.equal(f.evaluate('overrideRows.length'),0);assert.match(f.get('override-summary').className,/hidden/);assert.match(f.get('override-filter').className,/hidden/);await f.click('add-override');assert.equal(manualVisible(f,0),true);assert.doesNotMatch(f.get('override-summary').textContent,/오류 확인 중/);
});

test('manual error display keeps a correcting row visible throughout multi-digit input',async()=>{
  const f=await overridePanel();await f.click('add-override');overrideRow(f).first.value='';overrideRow(f,1).first.value='';overrideRow(f,1).first.oninput();await f.click('override-filter');
  for(const value of ['1','12','120']){overrideRow(f).first.value=value;overrideRow(f).first.oninput();assert.equal(manualVisible(f,0),true,'editing row remains visible at '+value);assert.equal(manualVisible(f,1),true);}
  assert.equal(overrideRow(f).first.value,'120');assert.match(f.get('override-summary').textContent,/2.*1/);await f.click('save-settings');assert.equal(JSON.parse([...f.saved.rows.values()].find(v=>v.includes('overrideInput'))).overrideInput[0].first,'120');
});

test('explicit full manual display reopens a collapsed section while polling never does',async()=>{
  const f=await overridePanel();await f.click('add-override');overrideRow(f,1).first.value='';overrideRow(f,1).first.oninput();await f.click('override-filter');await manualToggle(f).onclick();await f.tick();assert.equal(manualToggle(f).getAttribute('aria-expanded'),'false');
  await f.click('override-filter');assert.equal(manualToggle(f).getAttribute('aria-expanded'),'true');assert.doesNotMatch(f.get('disclosure-6').className,/hidden/);assert.equal(manualVisible(f,0),true);assert.equal(manualVisible(f,1),true);
});

test('manual frame errors identify the row and field and block only plan and apply',async()=>{
  for(const [field,value] of [['first',''],['first',' '],['first','-1'],['first','0.5'],['first','bad'],['first','1e309'],['first','9007199254740992'],['first','300'],['last','0'],['last','301'],['last','']]){
    const f=await overridePanel(),row=overrideRow(f);row[field].value=value;row[field].oninput();await f.click('plan');assert.equal(f.calls.some(c=>c.path==='/plan'),false,field+value);
    assert.equal(f.get('plan').disabled,true);assert.equal(f.get('analyze').disabled,false);assert.equal(f.get('sync').disabled,false);assert.equal(f.get('save-settings').disabled,false);
    assert.match(row.error.textContent,/수동 구간 1/);assert.equal(row[field].getAttribute('aria-invalid'),'true');assert.equal(row[field].getAttribute('aria-describedby'),row.error.getAttribute('id'));
    row[field].value=field==='first'?'30':'300';row[field].oninput();assert.equal(f.get('plan').disabled,false);await f.click('plan');assert.equal(f.get('apply').disabled,false);
  }
});
test('manual frame bounds respect a nonzero editing range and half-open adjacency',async()=>{
  const f=await analyzedPanel();f.get('range-start').value='30';f.get('range-end').value='150';f.get('range-end').onchange();await f.click('analyze');await f.tick();await f.click('add-override');const row=overrideRow(f);
  row.first.value='29';row.first.oninput();await f.click('plan');assert.equal(f.calls.some(c=>c.path==='/plan'),false);assert.match(row.error.textContent,/30.*150/);
  row.first.value='30';row.last.value='150';row.last.oninput();await f.click('plan');assert.equal(f.calls.filter(c=>c.path==='/plan').at(-1).body.policy.overrides[0].startFrame,30);
});
test('different-camera overlaps are rejected while same-camera overlaps and adjacent cuts are accepted',async()=>{
  for(const [camera,start,blocked] of [['video:1','90',true],['video:0','90',false],['video:1','100',false]]){
    const f=await overridePanel({native:twoCameraNative()});overrideRow(f).last.value='100';await f.click('add-override');const row=overrideRow(f,1);row.first.value=start;row.last.value='200';row.camera.value=camera;row.camera.onchange();await f.click('plan');
    assert.equal(f.calls.some(c=>c.path==='/plan'),!blocked);if(blocked){assert.match(row.error.textContent,/겹|중복/);assert.match(overrideRow(f).error.textContent,/겹|중복/);}
  }
});
test('protected manual camera is preserved for correction instead of silently selecting another',async()=>{
  const f=await overridePanel({native:twoCameraNative()}),row=overrideRow(f);row.camera.value='video:1';row.camera.onchange();f.evaluate("cameraRows[1].role.value='protected';cameraRows[1].role.onchange()");
  assert.equal(row.camera.value,'video:1');assert.equal(f.get('plan').disabled,true);assert.equal(row.camera.getAttribute('aria-invalid'),'true');assert.match(row.error.textContent,/카메라/);row.camera.value='video:0';row.camera.onchange();assert.equal(f.get('plan').disabled,false);
});
test('invalid manual rows survive raw settings and legacy numeric recovery without dropping rows',async()=>{
  const f=await overridePanel(),row=overrideRow(f);row.first.value='';row.last.value='bad';row.last.oninput();await f.click('save-settings');await f.click('load-settings');
  assert.equal(f.evaluate('overrideRows.length'),1);assert.equal(overrideRow(f).first.value,'');assert.equal(overrideRow(f).last.value,'bad');assert.equal(f.evaluate('analysisState!==null'),true);assert.equal(f.get('plan').disabled,true);
  const settings=JSON.parse(f.evaluate('JSON.stringify(captureSettings())'));delete settings.overrideInput;settings.policy.overrides=[{startFrame:30,endFrame:150,cameraId:'video:0'},{startFrame:200,endFrame:100,cameraId:'missing'}];
  f.evaluate('restoreSettings('+JSON.stringify(settings)+');toggle()');assert.equal(f.evaluate('overrideRows.length'),2);assert.equal(overrideRow(f).first.value,'30');assert.equal(overrideRow(f,1).camera.value,'missing');assert.equal(overrideRow(f,1).last.value,'100');assert.equal(f.get('plan').disabled,true);
});
test('manual raw input survives pending autosave and same-sequence refresh',async()=>{
  const f=await overridePanel();overrideRow(f).first.value=' ';overrideRow(f).first.oninput();await f.click('read-project');assert.equal(f.evaluate('overrideRows.length'),1);assert.equal(overrideRow(f).first.value,' ');
  for(let i=0;i<f.timeouts.length;i++){const fn=f.timeouts[i];f.timeouts[i]=null;if(fn)await fn();}const key=[...f.saved.rows.keys()].find(k=>k.startsWith('cut-settings-'));assert.equal(JSON.parse(f.saved.rows.get(key)).overrideInput[0].first,' ');
});
test('manual input is validated at direct apply plan response and native permit boundaries',async()=>{
  for(const stage of ['direct','response','permit']){
    const f=await overridePanel({request:(path,body,fixture)=>{if(stage==='response'&&path==='/plan'){overrideRow(fixture).last.value='';return {planHash:'p'.repeat(64),segments:[],reviews:[]};}if(stage==='permit'&&path==='/apply/begin'){overrideRow(fixture).last.value='';return {applyId:'owned-manual',epoch:0,execute:true,plan:{planHash:'p'.repeat(64)}};}}});let calls=0;f.host.apply=async()=>{calls++;throw new Error('Owned fixture stop');};await f.click('plan');if(stage==='direct')overrideRow(f).last.value='';await f.click('apply');assert.equal(calls,0);assert.match(f.get('status').textContent,/수동 구간.*종료/);if(stage==='direct'||stage==='response')assert.equal(f.calls.some(c=>c.path==='/apply/begin'),false);else assert.equal(f.calls.filter(c=>c.path==='/apply/end').at(-1)?.body.status,'failed');
  }
});
test('invalid manual rows do not delay immediate update and update guidance wins',async()=>{
  const f=await updatePanel();await f.click('add-override');overrideRow(f).last.value='';overrideRow(f).last.oninput();await f.nodes.find(n=>n.attrs['data-step']==='cut').onclick();await f.click('update');assert.equal(f.calls.filter(c=>c.path==='/updates/start').length,1);assert.match(f.get('action-readiness').textContent,/업데이트/);
});
test('manual row errors renumber after deletion and stable guidance does not rewrite live text',async()=>{
  const f=await overridePanel();await f.click('add-override');const row=overrideRow(f,1);row.last.value='';row.last.oninput();const id=row.error.getAttribute('id');let text=row.error.textContent,writes=0;
  Object.defineProperty(row.error,'textContent',{get:()=>text,set:value=>{text=value;writes++;}});await f.tick();await f.tick();assert.equal(writes,0);
  f.get('overrides').children[0].children.find(n=>n.tag==='button').onclick();assert.match(row.error.textContent,/수동 구간 1/);assert.equal(row.error.getAttribute('id'),id);assert.equal(row.last.getAttribute('aria-describedby'),id);
  row.last.value='300';row.last.oninput();assert.equal(row.error.textContent,'');assert.equal(row.last.getAttribute('aria-invalid'),'false');assert.equal(f.get('plan').disabled,false);
});
test('manual range coverage rejection from the engine receives Korean actionable guidance',async()=>{
  const f=await overridePanel({request:path=>{if(path==='/plan')throw Object.assign(new Error('Fixed camera does not cover the complete override'),{code:'OVERRIDE_COVERAGE_GAP'});}});await f.click('plan');assert.match(f.get('status').textContent,/영상.*구간.*카메라/);assert.doesNotMatch(f.get('status').textContent,/Fixed camera/);assert.equal(f.get('apply').disabled,true);
});
test('restoring many manual rows validates the batch once while preserving raw invalid rows',async()=>{
  const f=await analyzedPanel(),settings=JSON.parse(f.evaluate('JSON.stringify(captureSettings())'));
  settings.overrideInput=Array.from({length:500},(_,i)=>({first:i===499?'':'0',last:'300',camera:'video:0'}));
  f.evaluate('var feedbackCalls=0,originalOverrideFeedback=overrideFeedback;overrideFeedback=()=>{feedbackCalls++;return originalOverrideFeedback();}');
  const small={...settings,overrideInput:settings.overrideInput.slice(0,2)};f.evaluate('restoreSettings('+JSON.stringify(small)+')');const smallPasses=f.evaluate('feedbackCalls');f.evaluate('feedbackCalls=0');
  f.evaluate('restoreSettings('+JSON.stringify(settings)+')');assert.equal(f.evaluate('feedbackCalls'),smallPasses,'validation passes should be independent of row count');
  assert.equal(f.evaluate('overrideRows.length'),500);assert.equal(overrideRow(f,499).first.value,'');assert.match(overrideRow(f,499).error.textContent,/수동 구간 500/);f.evaluate('toggle()');assert.equal(f.get('plan').disabled,true);
});
test('simultaneously invalid manual frames and camera keep the first-field correction actionable',async()=>{
  for(const field of ['first','last']){const f=await overridePanel(),row=overrideRow(f);row[field].value='';row.camera.value='missing';row[field].oninput();assert.match(row.error.textContent,/프레임은.*정수/);assert.doesNotMatch(row.error.textContent,/프레임를|프레임을 다시 선택/);assert.equal(row.camera.getAttribute('aria-invalid'),'true');}
});

function twoCameraNative(){const native=nativeSnapshot();native.snapshot.tracks.push({trackRef:'video:1',mediaType:'video',index:1,name:'Other',muted:false});native.snapshot.clips.push({...native.snapshot.clips[0],instanceKey:'other-camera',trackRef:'video:1'});delete native.snapshot.snapshotHash;native.snapshot.snapshotHash=hash(native.snapshot);return native;}
const planChanges=[
  ['min shot',f=>{f.get('min-shot').value='3';}],['short turn',f=>{f.get('short-turn').value='0.75';}],['overlap',f=>{f.get('overlap').value='1.25';}],
  ['speaker camera',f=>{speakerCamera(f).value='video:1';}],['camera role',f=>{f.evaluate("cameraRows[1].role.value='protected'");}],
  ['camera coverage',f=>{f.evaluate("cameraRows[0].covered.value='A, B'");}],['start camera',f=>{f.get('start-camera').value='video:1';}],['reserve camera',f=>{f.get('reserve-camera').value='video:1';}]
];
test('reviewed plan rejects valid silent changes to every effective planning input',async()=>{
  for(const [name,change] of planChanges){const f=await analyzedPanel({native:twoCameraNative()});await f.click('plan');change(f);await f.click('apply');
    assert.equal(f.calls.some(c=>c.path==='/apply/begin'),false,name);assert.match(f.get('status').textContent,/편집안.*다시/,name);assert.equal(f.get('cut-count').textContent,'—');assert.equal(f.evaluate('analysisState!==null'),true);}
});
test('plan response cannot promote results after valid planning input changes',async()=>{
  for(const [name,change] of planChanges){const f=await analyzedPanel({native:twoCameraNative(),request:(path,body,fixture)=>{if(path==='/plan'){change(fixture);return {planHash:'p'.repeat(64),segments:[],reviews:[]};}}});await f.click('plan');
    assert.equal(f.get('apply').disabled,true,name);assert.match(f.get('status').textContent,/편집안.*다시/,name);assert.equal(f.evaluate('analysisState!==null'),true);}
});
test('native permit rechecks every effective plan input and preserves failed recovery records',async()=>{
  for(const [name,change] of planChanges){const f=await analyzedPanel({native:twoCameraNative(),request:(path,body,fixture)=>{if(path==='/apply/begin'){change(fixture);return {applyId:'owned-plan',epoch:0,execute:true,plan:{planHash:'p'.repeat(64)}};}}});let calls=0;f.host.apply=async()=>{calls++;throw new Error('Owned fixture stop');};await f.click('plan');await f.click('apply');
    assert.equal(calls,0,name);assert.equal(f.calls.filter(c=>c.path==='/apply/end').at(-1)?.body.status,'failed',name);assert.match(f.get('status').textContent,/편집안.*다시/,name);assert.ok([...f.saved.rows.keys()].some(k=>k.startsWith('cut-native-edit-intent')),name);}
});
test('periodic plan mismatch clears review and retains replan guidance until a new plan',async()=>{
  const f=await analyzedPanel();await f.click('plan');f.get('min-shot').value='3';await f.tick();assert.equal(f.get('apply').disabled,true);assert.equal(f.get('cut-count').textContent,'—');assert.match(f.get('action-readiness').textContent,/편집안.*다시/);
  f.get('min-shot').value='2';await f.tick();assert.equal(f.get('apply').disabled,true);assert.match(f.get('action-readiness').textContent,/편집안.*다시/);await f.click('plan');assert.equal(f.get('apply').disabled,false);
});
test('planning input identity is required even when a plan appears current',async()=>{
  const f=await analyzedPanel();await f.click('plan');f.evaluate('planInputHash=null');await f.click('apply');assert.equal(f.calls.some(c=>c.path==='/apply/begin'),false);assert.match(f.get('status').textContent,/편집안.*다시/);
});
test('equivalent numeric inputs and unused settings preserve reviewed plan identity',async()=>{
  const f=await analyzedPanel();await f.click('plan');f.get('min-shot').value='2.00';f.get('short-turn').value='0.600';f.get('speaker-count').value='4';f.get('sync-method').value='manual';await f.click('apply');assert.equal(f.calls.filter(c=>c.path==='/apply/begin').length,1);
});
test('manual override typing removal and coverage input clear the reviewed preview and autosave',async()=>{
  const f=await analyzedPanel();await f.click('plan');const coverage=f.evaluate('cameraRows[0].covered');assert.equal(typeof coverage.oninput,'function');coverage.value='A';coverage.oninput();assert.equal(f.get('cut-count').textContent,'—');
  await f.click('add-override');await f.click('plan');const row=f.evaluate('overrideRows[0]');assert.equal(typeof row.first.oninput,'function');row.first.value='30';row.first.oninput();assert.equal(f.get('apply').disabled,true);assert.equal(f.get('cut-count').textContent,'—');
  for(let i=0;i<f.timeouts.length;i++){const fn=f.timeouts[i];f.timeouts[i]=null;if(fn)await fn();}const key=[...f.saved.rows.keys()].find(k=>k.startsWith('cut-settings-'));assert.equal(JSON.parse(f.saved.rows.get(key)).policy.overrides[0].startFrame,30);
  await f.click('plan');const remove=f.get('overrides').children[0].children.find(n=>n.tag==='button');remove.onclick();assert.equal(f.get('cut-count').textContent,'—');
  for(let i=0;i<f.timeouts.length;i++){const fn=f.timeouts[i];f.timeouts[i]=null;if(fn)await fn();}assert.equal(JSON.parse(f.saved.rows.get(key)).policy.overrides.length,0);
});
test('manual override silent changes and approval wait cannot reach native mutation',async()=>{
  for(const stage of ['direct','permit']){const change=f=>{f.evaluate("overrideRows[0].last.value='150'");};const f=await analyzedPanel({request:(path,body,fixture)=>{if(stage==='permit'&&path==='/apply/begin'){change(fixture);return {applyId:'owned-override',epoch:0,execute:true,plan:{planHash:'p'.repeat(64)}};}}});await f.click('add-override');await f.click('plan');if(stage==='direct')change(f);let calls=0;f.host.apply=async()=>{calls++;throw new Error('Owned fixture stop');};await f.click('apply');assert.equal(calls,0);if(stage==='direct')assert.equal(f.calls.some(c=>c.path==='/apply/begin'),false);assert.match(f.get('status').textContent,/편집안.*다시/);}
});
test('analysis correction invalidates the old visible plan and preserves its new revision',async()=>{
  const f=await analyzedPanel();await f.click('plan');assert.equal(f.get('cut-count').textContent,'1');
  await f.nodes.find(n=>n.tag==='button'&&n.textContent==='이름 저장').onclick();
  assert.equal(f.get('cut-count').textContent,'—');assert.equal(f.get('timeline').children.length,0);assert.equal(f.get('segments').children.some(n=>n.tag==='button'),false);
  assert.equal(f.evaluate('analysisState.revision'),1);assert.equal(f.get('apply').disabled,true);await f.click('plan');assert.equal(f.calls.filter(c=>c.path==='/plan').at(-1).body.analysisRevision,1);assert.equal(f.get('apply').disabled,false);
});

test('malformed cut duration identifies its field and prevents planning without losing analysis',async()=>{
  for(const [id,value,label] of [['min-shot','','최소 샷'],['min-shot','-1','최소 샷'],['short-turn','bad','짧은 발화'],['overlap','1e309','동시 발화']]){
    const f=await analyzedPanel();f.get(id).value=value;f.get(id).onchange();await f.click('plan');
    assert.equal(f.calls.some(c=>c.path==='/plan'),false);assert.equal(f.get('plan').disabled,true);assert.equal(f.get('analyze').disabled,false);
    assert.match(f.get(id+'-error').textContent,new RegExp(label));assert.equal(f.get(id).getAttribute('aria-invalid'),'true');
    f.get(id).value='0.125';f.get(id).onchange();assert.equal(f.get(id+'-error').textContent,'');assert.equal(f.get('plan').disabled,false);await f.click('plan');assert.equal(f.calls.filter(c=>c.path==='/plan').length,1);
  }
});

test('zero and decimal cut durations preserve the policy payload',async()=>{
  const f=await analyzedPanel();f.get('min-shot').value='0';f.get('short-turn').value='0.125';f.get('overlap').value='1.25';f.get('overlap').onchange();await f.click('plan');
  const policy=f.calls.find(c=>c.path==='/plan').body.policy;assert.equal(policy.minShot,0);assert.equal(policy.shortTurn,0.125);assert.equal(policy.overlap,1.25);
});

test('typing cut settings invalidates a previous plan while retaining completed analysis',async()=>{
  const f=await analyzedPanel();await f.click('plan');assert.equal(f.get('apply').disabled,false);
  assert.equal(typeof f.get('short-turn').oninput,'function');f.get('short-turn').value='0.75';f.get('short-turn').oninput();
  assert.equal(f.get('apply').disabled,true);assert.equal(f.get('plan').disabled,false);assert.equal(f.evaluate('analysisState!==null'),true);
  f.get('short-turn').value='';f.get('short-turn').oninput();assert.equal(f.get('plan').disabled,true);assert.match(f.get('short-turn-error').textContent,/짧은 발화/);
});

test('cut settings save raw input and still restore legacy numeric policy',async()=>{
  const f=await analyzedPanel();f.get('min-shot').value='';f.get('short-turn').value='bad';f.get('short-turn').onchange();await f.click('save-settings');
  f.get('min-shot').value='2';f.get('short-turn').value='0.6';await f.click('load-settings');assert.equal(f.get('min-shot').value,'');assert.equal(f.get('short-turn').value,'bad');assert.equal(f.get('plan').disabled,true);assert.equal(f.evaluate('analysisState!==null'),true);
  const settings=JSON.parse(f.evaluate('JSON.stringify(captureSettings())'));delete settings.policyInput;settings.policy.minShot=0;settings.policy.shortTurn=0.25;
  f.evaluate('restoreSettings('+JSON.stringify(settings)+');toggle()');assert.equal(f.get('min-shot').value,'0');assert.equal(f.get('short-turn').value,'0.25');assert.equal(f.get('plan').disabled,false);
});

test('direct apply rejects a malformed cut duration before requesting native authorization',async()=>{
  const f=await analyzedPanel();await f.click('plan');f.get('overlap').value='-1';await f.click('apply');
  assert.equal(f.calls.some(c=>c.path==='/apply/begin'),false);assert.match(f.get('status').textContent,/동시 발화/);assert.equal(f.get('project-name').textContent,'sequence-1');
});

test('invalid cut duration in a plan response cannot promote the returned plan',async()=>{
  const f=await analyzedPanel({request:(path,body,fixture)=>{if(path==='/plan'){fixture.get('min-shot').value='';return {planHash:'p'.repeat(64),segments:[],reviews:[]};}}});
  await f.click('plan');assert.equal(f.get('apply').disabled,true);assert.match(f.get('status').textContent,/최소 샷/);assert.equal(f.evaluate('analysisState!==null'),true);
});

test('native permit wait rechecks cut duration before reaching the editing entry point',async()=>{
  const f=await analyzedPanel({request:(path,body,fixture)=>{if(path==='/apply/begin'){fixture.get('overlap').value='';return {applyId:'owned-cut-review',epoch:0,execute:true,plan:{planHash:'p'.repeat(64)}};}}});let nativeCalls=0;f.host.apply=async()=>{nativeCalls++;throw Object.assign(new Error('Owned fixture stops native mutation'),{code:'OWNED_FIXTURE_STOP'});};
  await f.click('plan');await f.click('apply');assert.equal(nativeCalls,0);assert.match(f.get('status').textContent,/동시 발화/);assert.equal(f.calls.filter(c=>c.path==='/apply/end').at(-1)?.body.status,'failed');
});

test('invalid cut settings do not prevent immediate update and update guidance takes priority',async()=>{
  const f=await updatePanel();f.get('overlap').value='';f.get('overlap').onchange();await f.nodes.find(n=>n.attrs['data-step']==='cut').onclick();await f.click('update');
  assert.equal(f.calls.filter(c=>c.path==='/updates/start').length,1);assert.match(f.get('action-readiness').textContent,/업데이트/);
});

test('cut duration raw input survives autosave and same-sequence refresh',async()=>{
  const f=await analyzedPanel();f.get('short-turn').value=' ';f.get('short-turn').oninput();await f.click('read-project');
  assert.equal(f.get('short-turn').value,' ');
  for(let i=0;i<f.timeouts.length;i++){const fn=f.timeouts[i];f.timeouts[i]=null;if(fn)await fn();}
  const key=[...f.saved.rows.keys()].find(k=>k.startsWith('cut-settings-'));assert.equal(JSON.parse(f.saved.rows.get(key)).policyInput.shortTurn,' ');
  f.get('short-turn').value='0.6';await f.click('load-settings');assert.equal(f.get('short-turn').value,' ');assert.equal(f.get('plan').disabled,true);
});

test('periodic cut duration check blocks a silently invalidated plan and connects accessible guidance',async()=>{
  const f=await analyzedPanel();await f.click('plan');f.get('min-shot').value='';await f.tick();
  assert.equal(f.get('apply').disabled,true);assert.equal(f.get('plan').disabled,true);assert.equal(f.evaluate('analysisState!==null'),true);
  assert.equal(f.get('min-shot').getAttribute('aria-describedby'),'min-shot-error');assert.match(f.get('action-readiness').textContent,/최소 샷/);
  f.get('min-shot').value='0';await f.tick();assert.equal(f.get('min-shot').getAttribute('aria-invalid'),'false');assert.equal(f.get('plan').disabled,false);assert.equal(f.get('apply').disabled,true);
});

test('cut input invalidation clears the old visible preview while preserving the analysis',async()=>{
  for(const event of ['input','periodic']){
    const f=await analyzedPanel();await f.click('plan');assert.equal(f.get('cut-count').textContent,'1');
    f.get('overlap').value=event==='input'?'0.75':'';if(event==='input')f.get('overlap').oninput();else await f.tick();
    assert.equal(f.get('cut-count').textContent,'—');assert.equal(f.get('review-count').textContent,'—');assert.equal(f.get('timeline').children.length,0);
    assert.equal(f.get('segments').children.some(n=>n.tag==='button'),false);assert.equal(f.get('reviews').children.length,0);assert.match(f.get('review-page-info').textContent,/편집안/);
    assert.equal(f.evaluate('analysisState!==null'),true);
  }
});

test('unchanged cut duration errors do not repeatedly rewrite live-region text',async()=>{
  const f=await analyzedPanel();f.get('overlap').value='';f.get('overlap').oninput();const hint=f.get('overlap-error');let text=hint.textContent,writes=0;
  Object.defineProperty(hint,'textContent',{get:()=>text,set:value=>{text=value;writes++;}});
  await f.tick();await f.tick();assert.equal(writes,0);f.get('overlap').value='0';f.get('overlap').oninput();assert.equal(writes,1);assert.equal(text,'');
});

async function completedSync(extra={}){
  const f=await panel({request:async(path,body,fixture)=>{
    if(extra.request){const value=await extra.request(path,body,fixture);if(value!==undefined)return value;}
    if(path==='/jobs/job-1')return {jobId:'job-1',kind:'sync',status:fixture.jobStatus,result:{sources:{camera:{status:'accepted'},mic:{status:'accepted'}},offsets:{camera:0,mic:0}}};
    if(path==='/sync-plan')throw Object.assign(new Error('Owned fixture stops before native mutation'),{code:'OWNED_FIXTURE_STOP'});
  }});
  if(extra.method){f.get('sync-method').value=extra.method;f.get('sync-method').onchange();}
  if(extra.method==='manual'){syncRow(f,1).confirmed.checked=true;syncRow(f,1).confirmed.onchange();}
  if(extra.method==='timecode')for(const index of [0,1]){const row=syncRow(f,index);row.clockConfirmed.checked=true;row.clockId.value='owned clock';row.date.value='2026-10-09';row.clockId.onchange();}
  await f.click('sync');if(extra.beforeComplete)await extra.beforeComplete(f);await f.tick();return f;
}

test('completed sync cannot apply after valid silent channel or reference changes',async()=>{
  for(const change of [f=>{syncRow(f).channel.value='2';},f=>{f.get('sync-reference').value='mic';}]){
    const f=await completedSync();assert.equal(f.get('apply-sync').disabled,false);change(f);await f.click('apply-sync');
    assert.equal(f.calls.some(c=>c.path==='/sync-plan'),false);assert.equal(f.get('apply-sync').disabled,true);assert.equal(f.get('sync-result').textContent,'');assert.match(f.get('status').textContent,/입력.*다시.*분석/);assert.equal(f.get('project-name').textContent,'sequence-1');
  }
});

test('sync completion discards a result when input changed during analysis',async()=>{
  const f=await completedSync({beforeComplete:f=>{syncRow(f).stream.value='2';}});
  assert.equal(f.get('apply-sync').disabled,true);assert.equal(f.get('sync-result').textContent,'');assert.match(f.get('status').textContent,/입력.*다시.*분석/);assert.equal(f.get('analyze').disabled,false);
  syncRow(f).stream.value='1';f.evaluate('toggle()');assert.equal(f.get('apply-sync').disabled,true);
});

test('manual and timecode effective confirmation changes invalidate completed sync without events',async()=>{
  for(const [method,field,value] of [['manual','offset','1.25'],['manual','confirmed',false],['timecode','clockId','other clock'],['timecode','date','2026-10-10'],['timecode','fps','25/1'],['timecode','drop',true],['timecode','clockConfirmed',false]]){
    const f=await completedSync({method}),row=syncRow(f,1);if(typeof value==='boolean')row[field].checked=value;else row[field].value=value;
    await f.click('apply-sync');assert.equal(f.calls.some(c=>c.path==='/sync-plan'),false);assert.match(f.get('status').textContent,/입력.*다시.*분석/);
  }
});

test('unused sync values and equivalent numeric indices do not reject a current result',async()=>{
  for(const method of ['audio','manual','timecode']){
    const f=await completedSync({method});syncRow(f).stream.value='01';
    if(method!=='manual')syncRow(f,1).offset.value='1.25';else syncRow(f).offset.value='1.25';
    if(method!=='timecode')syncRow(f,1).clockId.value='unused clock';
    await f.click('apply-sync');assert.equal(f.calls.filter(c=>c.path==='/sync-plan').length,1);
  }
});

test('sync input is rechecked after approval response before beginning native work',async()=>{
  const f=await completedSync({request:(path,body,fixture)=>{if(path==='/sync-plan'){syncRow(fixture).channel.value='2';return {planHash:'p'.repeat(64)};}}});
  await f.click('apply-sync');assert.equal(f.calls.filter(c=>c.path==='/sync-plan').length,1);assert.equal(f.calls.some(c=>c.path==='/apply/begin'),false);assert.match(f.get('status').textContent,/입력.*다시.*분석/);assert.equal(f.get('apply-sync').disabled,true);
});

test('sync results without captured input identity are never sent for approval',async()=>{
  const f=await panel();f.evaluate("syncJob='job-1';syncResult={};toggle();");await f.click('apply-sync');assert.equal(f.calls.some(c=>c.path==='/sync-plan'),false);assert.equal(f.get('apply-sync').disabled,true);assert.match(f.get('status').textContent,/다시.*분석/);
});

test('sync input is rechecked after native permit before reaching the mutation entry point',async()=>{
  const f=await completedSync({request:(path,body,fixture)=>{
    if(path==='/sync-plan')return {planHash:'p'.repeat(64)};
    if(path==='/apply/begin'){syncRow(fixture).channel.value='2';return {applyId:'owned-review',epoch:0,execute:true,plan:{planHash:'p'.repeat(64)}};}
  }});let nativeCalls=0;f.host.applySync=async()=>{nativeCalls++;throw Object.assign(new Error('Owned fixture stops native mutation'),{code:'OWNED_FIXTURE_STOP'});};
  await f.click('apply-sync');assert.equal(nativeCalls,0);assert.match(f.get('status').textContent,/입력.*다시.*분석/);
  assert.equal(f.calls.filter(c=>c.path==='/apply/end').at(-1)?.body.status,'failed');assert.equal(f.get('apply-sync').disabled,true);
});

test('periodic detection clears stale sync offsets and displays reanalysis guidance',async()=>{
  const f=await completedSync();assert.match(f.get('sync-result').textContent,/camera.mov/);syncRow(f).channel.value='2';await f.tick();
  assert.equal(f.get('apply-sync').disabled,true);assert.equal(f.get('sync-result').textContent,'');assert.match(f.get('status').textContent,/입력.*다시.*분석/);assert.match(f.get('action-readiness').textContent,/다시.*분석/);
});

test('update priority survives periodic detection of a stale sync result',async()=>{
  const f=await completedSync();syncRow(f).channel.value='2';f.state.update={updateState:'WAITING_HOST_EXIT',checkState:'CURRENT',candidate:null};await f.tick();
  assert.match(f.get('action-readiness').textContent,/Premiere.*종료/);assert.equal(f.get('apply-sync').disabled,true);
});

test('sync source malformed indices are rejected before submitting a job',async()=>{
  for(const [field,value] of [['channel',''],['channel','0'],['channel','-1'],['stream','1.5'],['stream','invalid'],['stream','9007199254740992']]){
    const f=await panel(),row=syncRow(f);row[field].value=value;row[field].onchange();await f.click('sync');
    assert.equal(f.calls.some(c=>c.path==='/jobs'),false);assert.equal(f.get('sync').disabled,true);assert.equal(f.get('analyze').disabled,false);
    assert.match(f.get('sync-error').textContent,/camera.mov.*정수/);assert.equal(row[field].getAttribute('aria-invalid'),'true');
    row[field].value='1';row[field].onchange();assert.equal(f.get('sync-error').textContent,'');assert.equal(f.get('sync').disabled,false);
    await f.click('sync');const options=f.calls.find(c=>c.path==='/jobs').body.options;assert.equal(options.sourceSelections[0].streamIndex,0);assert.equal(options.sourceSelections[0].channelIndex,0);
  }
});

test('sync selection count and excluded reference have visible correction guidance',async()=>{
  const f=await panel(),first=syncRow(f);first.check.checked=false;first.check.onchange();await f.click('sync');
  assert.equal(f.calls.some(c=>c.path==='/jobs'),false);assert.match(f.get('sync-error').textContent,/소스.*2/);
  const native=nativeSnapshot();native.snapshot.sources.push({assetId:'extra',canonicalPath:'D:/owned/extra.wav'});delete native.snapshot.snapshotHash;native.snapshot.snapshotHash=hash(native.snapshot);
  const g=await panel({native});syncRow(g).check.checked=false;syncRow(g).check.onchange();await g.click('sync');assert.equal(g.calls.some(c=>c.path==='/jobs'),false);assert.match(g.get('sync-error').textContent,/기준.*선택/);
  g.get('sync-reference').value='mic';g.get('sync-reference').onchange();assert.equal(g.get('sync-error').textContent,'');assert.equal(g.get('sync').disabled,false);
});

test('invalid unchecked sync row is ignored and all methods retain index conversion',async()=>{
  for(const method of ['audio','manual','timecode']){
    const native=nativeSnapshot();native.snapshot.sources.push({assetId:'extra',canonicalPath:'D:/owned/extra.wav'});delete native.snapshot.snapshotHash;native.snapshot.snapshotHash=hash(native.snapshot);
    const f=await panel({native}),ignored=syncRow(f,2);ignored.channel.value='';ignored.stream.value='bad';ignored.check.checked=false;ignored.check.onchange();
    f.get('sync-method').value=method;f.get('sync-method').onchange();syncRow(f).stream.value='257';syncRow(f).channel.value='65';syncRow(f).stream.onchange();await f.click('sync');
    assert.equal(f.get('sync-error').textContent,'');const options=f.calls.find(c=>c.path==='/jobs').body.options;assert.equal(options.method,method);assert.equal(options.sourceSelections.length,2);assert.equal(options.sourceSelections[0].streamIndex,256);assert.equal(options.sourceSelections[0].channelIndex,64);
  }
});

test('sync settings preserve raw invalid and unchecked input without breaking legacy settings',async()=>{
  const f=await panel();syncRow(f).stream.value='';syncRow(f,1).check.checked=false;syncRow(f,1).channel.value='bad';syncRow(f).stream.onchange();await f.click('save-settings');
  syncRow(f).stream.value='1';syncRow(f,1).channel.value='1';await f.click('load-settings');assert.equal(syncRow(f).stream.value,'');assert.equal(syncRow(f,1).channel.value,'bad');assert.equal(syncRow(f,1).check.checked,false);
  const settings=JSON.parse(f.evaluate('JSON.stringify(captureSettings())'));delete settings.syncInput;settings.sync.sourceSelections=[{assetId:'camera',streamIndex:1,channelIndex:2},{assetId:'mic',streamIndex:0,channelIndex:0}];
  f.evaluate('restoreSettings('+JSON.stringify(settings)+');toggle()');assert.equal(syncRow(f).stream.value,'2');assert.equal(syncRow(f).channel.value,'3');assert.equal(f.get('sync').disabled,false);
});

test('typing sync input clears stale results without blocking unrelated analysis or immediate update',async()=>{
  const f=await completedSync();f.state.update={updateState:'IDLE',checkState:'AVAILABLE',candidate:{candidateId:'release:hash',manifestDigest:'a'.repeat(64),appVersion:'0.1.1'}};await f.tick();assert.equal(f.get('apply-sync').disabled,false);
  const row=syncRow(f);assert.equal(typeof row.channel.oninput,'function');row.channel.value='';row.channel.oninput();assert.equal(f.get('apply-sync').disabled,true);assert.equal(f.get('sync-result').textContent,'');assert.equal(f.get('analyze').disabled,false);
  await f.click('update');assert.equal(f.calls.filter(c=>c.path==='/updates/start').length,1);assert.match(f.get('action-readiness').textContent,/업데이트/);
});

test('direct sync apply rejects changed malformed input even without an input event',async()=>{
  const f=await panel();f.evaluate("syncJob='job-1';syncResult={};");syncRow(f).channel.value='';await f.click('apply-sync');
  assert.equal(f.calls.some(c=>c.path==='/sync-plan'),false);assert.equal(f.get('project-name').textContent,'sequence-1');assert.match(f.get('status').textContent,/채널/);
});

test('sync input autosave preserves invalid values after automatic same-sequence refresh',async()=>{
  const f=await panel();syncRow(f).stream.value='';syncRow(f).stream.oninput();syncRow(f,1).channel.value='bad';syncRow(f,1).check.checked=false;syncRow(f,1).check.onchange();
  f.native.snapshot.sequenceName='Updated timeline';delete f.native.snapshot.snapshotHash;f.native.snapshot.snapshotHash=hash(f.native.snapshot);await f.tick();
  for(let i=0;i<f.timeouts.length;i++){const callback=f.timeouts[i];f.timeouts[i]=null;if(callback)await callback();}
  const settings=JSON.parse([...f.saved.rows.values()].find(raw=>typeof raw==='string'&&raw.includes('syncInput')));
  assert.equal(settings.syncInput[0].stream,'');assert.equal(settings.syncInput[1].channel,'bad');assert.equal(syncRow(f).stream.value,'');assert.equal(syncRow(f,1).check.checked,false);
});

test('direct sync analysis rejects bad indices without events and input typing updates row accessibility',async()=>{
  const f=await panel(),row=syncRow(f);row.stream.value='1.5';await f.click('sync');assert.equal(f.calls.some(c=>c.path==='/jobs'),false);assert.equal(row.stream.getAttribute('aria-invalid'),'true');assert.equal(row.stream.getAttribute('aria-describedby'),row.issue.id);
  row.stream.value='2';row.stream.oninput();assert.equal(row.stream.getAttribute('aria-invalid'),'false');assert.equal(row.issue.textContent,'');assert.equal(f.get('sync').disabled,false);
});

test('invalid range does not discard the connected sequence or customized tracks',async()=>{
  const f=await panel(),row=mic(f);row.speaker.value='진행자';row.channel.value='2';row.channel.onchange();
  f.get('range-start').value='100';f.get('range-end').value='90';f.get('range-end').onchange();
  const binds=f.calls.filter(c=>c.path==='/project').length;await f.click('analyze');
  assert.equal(f.get('project-name').textContent,'sequence-1');assert.equal(mic(f),row);
  assert.equal(row.speaker.value,'진행자');assert.equal(row.channel.value,'2');
  assert.equal(f.get('range-start').value,'100');assert.equal(f.get('range-end').value,'90');
  assert.equal(f.calls.filter(c=>c.path==='/project').length,binds);assert.equal(f.calls.some(c=>c.path==='/jobs'),false);
});

test('range feedback rejects malformed bounds and clears while typing a correction',async()=>{
  for(const [start,end] of [['','90'],['0',''],['-1','90'],['1.5','90'],['invalid','90'],['90','90'],['0','301'],['0','9007199254740992']]){
    const f=await panel();f.get('range-start').value=start;f.get('range-end').value=end;
    assert.equal(typeof f.get('range-end').oninput,'function');f.get('range-end').oninput();
    assert.match(f.get('range-error').textContent,/프레임/);assert.equal(f.get('analyze').disabled,true);assert.equal(f.get('sync').disabled,true);
    await f.click('analyze');assert.equal(f.calls.some(c=>c.path==='/jobs'),false);assert.equal(f.get('project-name').textContent,'sequence-1');
    f.get('range-start').value='0';f.get('range-end').value='90';f.get('range-end').oninput();
    assert.equal(f.get('range-error').textContent,'');assert.equal(f.get('range-end').getAttribute('aria-invalid'),'false');assert.equal(f.get('analyze').disabled,false);
    await f.click('analyze');assert.deepEqual(JSON.parse(JSON.stringify(f.calls.filter(c=>c.path==='/project').at(-1).body.snapshot.range)),{startFrame:0,endFrame:90});
  }
});

test('manual refresh and automatic timeline change preserve invalid range and track choices',async()=>{
  const f=await panel(),row=mic(f);row.speaker.value='진행자';row.channel.value='2';row.channel.onchange();
  f.get('range-end').value='';f.get('range-end').onchange();await f.click('read-project');
  assert.equal(mic(f),row);assert.equal(f.get('range-end').value,'');assert.equal(f.get('project-name').textContent,'sequence-1');
  f.native.snapshot.sequenceName='Updated timeline';delete f.native.snapshot.snapshotHash;f.native.snapshot.snapshotHash=hash(f.native.snapshot);
  await f.tick();assert.equal(f.get('project-name').textContent,'Updated timeline');assert.equal(mic(f).speaker.value,'진행자');assert.equal(mic(f).channel.value,'2');
  assert.equal(f.get('range-end').value,'');assert.match(f.get('range-error').textContent,/프레임/);assert.equal(f.get('analyze').disabled,true);
  assert.deepEqual(JSON.parse(JSON.stringify(f.calls.filter(c=>c.path==='/project').at(-1).body.snapshot.range)),{startFrame:0,endFrame:300});
});

test('invalid raw range persists in settings while legacy numeric ranges still restore',async()=>{
  const f=await panel();f.get('range-start').value='';f.get('range-end').value='90';f.get('range-start').onchange();await f.click('save-settings');
  f.get('range-start').value='30';await f.click('load-settings');assert.equal(f.get('range-start').value,'');assert.equal(f.get('analyze').disabled,true);
  const settings=JSON.parse(f.evaluate('JSON.stringify(captureSettings())'));delete settings.rangeInput;settings.range={startFrame:30,endFrame:90};
  f.evaluate('restoreSettings('+JSON.stringify(settings)+');toggle()');assert.equal(f.get('range-start').value,'30');assert.equal(f.get('range-end').value,'90');assert.equal(f.get('analyze').disabled,false);
});

test('invalid manual refresh reschedules queued autosave for raw bounds and track choices',async()=>{
  const f=await panel();await f.click('save-settings');mic(f).speaker.value='진행자';mic(f).speaker.oninput();
  f.get('range-end').value='';f.get('range-end').oninput();await f.click('read-project');
  assert.equal(f.get('range-end').value,'');
  for(let i=0;i<f.timeouts.length;i++){const callback=f.timeouts[i];f.timeouts[i]=null;if(callback)await callback();}
  const settings=JSON.parse([...f.saved.rows.values()].find(raw=>typeof raw==='string'&&raw.includes('rangeInput')));
  assert.equal(settings.rangeInput.end,'');assert.equal(settings.microphones[0].speaker,'진행자');
});

test('automatic same-sequence refresh preserves a pending settings autosave',async()=>{
  const f=await panel();await f.click('save-settings');mic(f).speaker.value='진행자';mic(f).speaker.oninput();
  f.get('range-end').value='';f.get('range-end').oninput();f.native.snapshot.sequenceName='Updated timeline';delete f.native.snapshot.snapshotHash;f.native.snapshot.snapshotHash=hash(f.native.snapshot);await f.tick();
  for(let i=0;i<f.timeouts.length;i++){const callback=f.timeouts[i];f.timeouts[i]=null;if(callback)await callback();}
  const settings=JSON.parse([...f.saved.rows.values()].find(raw=>typeof raw==='string'&&raw.includes('rangeInput')));
  assert.equal(settings.rangeInput.end,'');assert.equal(settings.microphones[0].speaker,'진행자');assert.equal(settings.sequenceRef,'sequence-1');
});

test('typing a range invalidates the existing plan and does not prevent immediate update',async()=>{
  const f=await updatePanel();await f.click('analyze');await f.tick();await f.click('plan');assert.equal(f.get('apply').disabled,false);
  assert.equal(typeof f.get('range-end').oninput,'function');f.get('range-end').value='0';f.get('range-end').oninput();assert.equal(f.get('apply').disabled,true);
  await f.click('update');assert.equal(f.calls.filter(c=>c.path==='/updates/start').length,1);assert.match(f.get('action-readiness').textContent,/업데이트/);
});

test('closing the native sequence still clears the connection even with invalid range input',async()=>{
  const f=await panel();f.get('range-end').value='0';f.get('range-end').onchange();f.snapshotError='SEQUENCE_REQUIRED';await f.tick();
  assert.equal(f.get('project-name').textContent,'시퀀스를 열어 주세요');assert.equal(f.get('microphones').children.length,0);assert.equal(f.get('analyze').disabled,true);
});

test('direct work handlers reject malformed bounds even when no input event fired',async()=>{
  for(const action of ['sync','plan','apply-sync','apply']){
    const f=await panel();f.get('range-end').value='';const before=f.calls.length;await f.click(action);
    assert.equal(f.get('project-name').textContent,'sequence-1');assert.match(f.get('status').textContent,/프레임/);
    assert.equal(f.calls.slice(before).some(c=>['/jobs','/plan','/apply/begin','/sync/begin'].includes(c.path)),false);
  }
});

test('automatic timeline shrink preserves malformed input and falls back to a safe bound',async()=>{
  const f=await panel();f.get('range-start').value='100';f.get('range-end').value='200';f.get('range-end').onchange();await f.click('read-project');
  f.evaluate("cameraRows[0].role.value='wide'");f.get('range-end').value='';f.get('range-end').onchange();
  f.native.snapshot.range.endFrame=50;delete f.native.snapshot.snapshotHash;f.native.snapshot.snapshotHash=hash(f.native.snapshot);await f.tick();
  assert.deepEqual(JSON.parse(JSON.stringify(f.calls.filter(c=>c.path==='/project').at(-1).body.snapshot.range)),{startFrame:0,endFrame:50});
  assert.equal(f.get('range-start').value,'100');assert.equal(f.get('range-end').value,'');assert.equal(f.evaluate('cameraRows[0].role.value'),'wide');
  assert.equal(f.get('analyze').disabled,true);assert.match(f.get('range-error').textContent,/50/);
});

test('switching to another sequence does not carry invalid local range across sequences',async()=>{
  const f=await panel();f.get('range-end').value='';f.get('range-end').onchange();f.native=nativeSnapshot('sequence-2');await f.tick();
  assert.equal(f.get('project-name').textContent,'sequence-2');assert.equal(f.get('range-start').value,'0');assert.equal(f.get('range-end').value,'300');assert.equal(f.get('range-error').textContent,'');assert.equal(f.get('analyze').disabled,false);
});

test('invalid microphone channel inputs identify the row and block analysis before any job',async()=>{
  for(const value of ['', '0','1.5','invalid','65']){
    const f=await panel(),row=mic(f);row.channel.value=value;row.channel.onchange();
    assert.match(micError(f),/A1.*채널.*1.*64.*정수/);assert.equal(row.channel.getAttribute('aria-invalid'),'true');assert.equal(f.get('analyze').disabled,true);
    await f.click('analyze');assert.equal(f.calls.some(c=>c.path==='/jobs'),false);assert.match(f.get('status').textContent,/A1.*채널/);
    row.channel.value='2';row.channel.onchange();assert.equal(micError(f),'');assert.equal(row.channel.getAttribute('aria-invalid'),'false');assert.equal(f.get('analyze').disabled,false);
  }
});

test('invalid microphone streams and empty speaker IDs can be corrected in their source row',async()=>{
  const f=await panel(),row=mic(f);row.stream.value='257';row.speaker.value='  ';row.stream.onchange();
  assert.match(micError(f),/스트림.*256/);assert.match(micError(f),/화자 ID/);assert.equal(f.get('analyze').disabled,true);
  row.stream.value='1';row.stream.onchange();assert.doesNotMatch(micError(f),/스트림/);assert.match(micError(f),/화자 ID/);
  row.speaker.value='진행자';row.speaker.onchange();assert.equal(micError(f),'');assert.equal(f.get('analyze').disabled,false);
  await f.click('analyze');const call=f.calls.find(c=>c.path==='/jobs');assert.equal(call.body.options.microphones[0].speakerId,'진행자');
});

test('empty microphone selection gives a visible next action and cannot submit analysis',async()=>{
  const f=await panel(),row=mic(f);row.check.checked=false;row.check.onchange();
  assert.equal(f.get('analyze').disabled,true);assert.match(f.get('action-readiness').textContent,/마이크.*하나.*선택/);
  await f.click('analyze');assert.equal(f.calls.some(c=>c.path==='/jobs'),false);assert.match(f.get('status').textContent,/마이크.*선택/);
});

test('mixed recording accepts an empty speaker ID but still rejects malformed channel selection',async()=>{
  const f=await panel();await f.click('mode-mixed');const row=mic(f);row.speaker.value='';row.speaker.onchange();
  assert.equal(micError(f),'');assert.equal(f.get('analyze').disabled,false);
  row.channel.value='0';row.channel.onchange();assert.match(micError(f),/채널/);assert.equal(f.get('analyze').disabled,true);
});

test('unselected malformed microphones are ignored and valid indices retain their zero-based payload',async()=>{
  const native=nativeSnapshot();native.snapshot.tracks.push({trackRef:'audio:1',mediaType:'audio',index:1,name:'Second mic',muted:false});
  native.snapshot.clips.push({...native.snapshot.clips[1],instanceKey:'mic-clip-2',trackRef:'audio:1'});delete native.snapshot.snapshotHash;native.snapshot.snapshotHash=hash(native.snapshot);
  const f=await panel({native}),first=mic(f),second=mic(f,1);second.speaker.value='';second.channel.value='bad';second.check.checked=false;second.check.onchange();
  assert.equal(micError(f,1),'');assert.equal(f.get('analyze').disabled,false);
  first.channel.value='64';first.stream.value='256';first.stream.onchange();await f.click('analyze');
  const microphones=f.calls.find(c=>c.path==='/jobs').body.options.microphones;assert.equal(microphones.length,1);assert.equal(microphones[0].streamIndex,255);assert.equal(microphones[0].channelIndex,63);
});

test('restoring malformed microphone settings retains their values and exposes correction guidance',async()=>{
  const f=await panel(),settings=JSON.parse(f.evaluate('JSON.stringify(captureSettings())'));settings.microphones[0].channel='';
  f.evaluate('restoreSettings('+JSON.stringify(settings)+');toggle()');
  assert.equal(mic(f).channel.value,'');assert.match(micError(f),/채널/);assert.equal(f.get('analyze').disabled,true);
  mic(f).channel.value='1';mic(f).channel.onchange();assert.equal(micError(f),'');assert.equal(f.get('analyze').disabled,false);
});

test('saving and restoring an empty audio stream preserves the invalid raw input',async()=>{
  const f=await panel(),row=mic(f);row.stream.value='';row.stream.oninput();
  const settings=JSON.parse(f.evaluate('JSON.stringify(captureSettings())'));assert.equal(settings.microphones[0].stream,'');
  f.evaluate('restoreSettings('+JSON.stringify(settings)+');toggle()');
  assert.equal(mic(f).stream.value,'');assert.match(micError(f),/스트림/);assert.equal(f.get('analyze').disabled,true);
});

test('update guidance takes precedence over invalid microphone input and starts immediately',async()=>{
  const f=await updatePanel(),row=mic(f);row.channel.value='0';row.channel.onchange();await f.click('update');
  assert.equal(f.calls.filter(c=>c.path==='/updates/start').length,1);assert.match(f.get('action-readiness').textContent,/업데이트/);assert.equal(f.get('analyze').disabled,true);assert.equal(f.calls.some(c=>c.path==='/jobs'),false);
});

test('microphone feedback reacts while typing and invalidates an older plan without requiring blur',async()=>{
  const f=await panel();await f.click('analyze');await f.tick();await f.click('plan');assert.equal(f.get('apply').disabled,false);
  const row=mic(f);assert.equal(typeof row.channel.oninput,'function');row.channel.value='0';row.channel.oninput();
  assert.match(micError(f),/채널/);assert.equal(f.get('analyze').disabled,true);assert.equal(f.get('apply').disabled,true);
  row.channel.value='1';row.channel.oninput();assert.equal(micError(f),'');assert.equal(f.get('analyze').disabled,false);assert.equal(f.get('apply').disabled,true);
});

async function modelPanel(extra={}){
  return panel({request:async(path,body,f)=>{
    if(extra.request){const value=await extra.request(path,body,f);if(value!==undefined)return value;}
    if(path==='/models/community-1/revision')return {revision:'b'.repeat(40)};
    if(path==='/models/community-1/install')return {jobId:'job-1',kind:'model-setup',status:'running'};
  }});
}
async function installModel(f){f.get('model-token').value='owned-fixture-access';f.get('model-terms').checked=true;await f.click('install-model');}

test('model setup request failures clear busy guidance without exposing provider details',async()=>{
  for(const endpoint of ['revision','install']){
    const f=await modelPanel({request:path=>{if(path==='/models/community-1/'+endpoint)throw Object.assign(new Error('private authenticated URL'),{code:'MODEL_NOT_READY'});}});
    await installModel(f);assert.equal(f.get('model-token').value,'');assert.match(f.get('model-install-status').textContent,/실패.*권한.*네트워크.*토큰.*다시/);
    assert.doesNotMatch(f.get('model-install-status').textContent,/private|확인하고 있습니다|설치 중/);assert.doesNotMatch(f.get('status').textContent,/private/);assert.equal(f.get('install-model').disabled,false);
  }
});

test('model worker failure and cancellation show distinct terminal guidance and allow retry',async()=>{
  for(const status of ['failed','canceled']){
    const f=await modelPanel({request:(path,body,f)=>{if(path==='/jobs/job-1')return {jobId:'job-1',status:f.jobStatus,error:{code:'MODEL_NOT_READY',message:'private provider URL'}};}});await installModel(f);f.jobStatus=status;await f.tick();
    assert.match(f.get('model-install-status').textContent,status==='failed'?/실패.*다시/:/중단.*토큰.*다시/);
    assert.doesNotMatch(f.get('model-install-status').textContent,/private provider/);assert.doesNotMatch(f.get('status').textContent,/private provider/);
    assert.equal(f.get('install-model').disabled,false);assert.doesNotMatch(f.get('model-install-status').textContent,/설치 중/);
    await installModel(f);assert.match(f.get('model-install-status').textContent,/설치 중/);assert.equal(f.get('install-model').disabled,true);
  }
});

test('model worker canceling updates its own installation status before terminal drain',async()=>{
  const f=await modelPanel();await installModel(f);f.jobStatus='canceling';await f.tick();
  assert.match(f.get('model-install-status').textContent,/중단.*요청.*종료.*기다/);assert.equal(f.get('install-model').disabled,true);
});

test('model setup input guidance resets stale outcome and keeps token out of display',async()=>{
  const f=await modelPanel();f.get('model-token').value='owned-fixture-access';f.get('model-terms').checked=false;await f.click('install-model');
  assert.equal(f.get('model-token').value,'');assert.match(f.get('model-install-status').textContent,/토큰.*동의.*다시 설치/);assert.doesNotMatch(f.get('model-install-status').textContent,/owned-fixture/);
  assert.equal(f.calls.some(c=>c.path.startsWith('/models/')),false);
});

test('update interruption ends model installation guidance without allowing editing',async()=>{
  const f=await modelPanel();await installModel(f);f.jobStatus='running';
  f.state.update={updateState:'IDLE',checkState:'AVAILABLE',candidate:{candidateId:'release:hash',manifestDigest:'a'.repeat(64),appVersion:'0.1.1'}};await f.tick();await f.click('update');
  assert.match(f.get('model-install-status').textContent,/중단.*요청/);assert.equal(f.calls.filter(c=>c.path==='/updates/start').length,1);
  f.jobStatus='completed';await f.tick();assert.match(f.get('model-install-status').textContent,/중단/);assert.doesNotMatch(f.get('model-install-status').textContent,/마쳤/);assert.equal(f.get('analyze').disabled,true);
});

test('model cancel reports a request before drain and never promotes a canceled completion',async()=>{
  const f=await modelPanel();await installModel(f);f.jobStatus='running';await f.click('cancel');
  assert.match(f.get('model-install-status').textContent,/중단.*요청/);assert.equal(f.get('install-model').disabled,true);
  f.jobStatus='completed';await f.tick();assert.match(f.get('model-install-status').textContent,/중단.*토큰.*다시/);assert.doesNotMatch(f.get('model-install-status').textContent,/마쳤/);
});

test('model setup completion promises integrity verification on analysis rather than readiness',async()=>{
  const f=await modelPanel();await installModel(f);await f.tick();
  assert.match(f.get('model-install-status').textContent,/설치.*마쳤.*분석.*무결성.*확인/);assert.doesNotMatch(f.get('status').textContent,/분석을 시작할 수/);
});

test('invalid model metadata uses Korean repair instructions for the current recording mode',async()=>{
  const f=await panel();f.state.models.silero.status='error';f.state.models['community-1'].status='error';await f.tick();await f.click('next-step');
  assert.match(f.get('models').textContent,/정보.*확인 필요/);assert.doesNotMatch(f.get('models').textContent,/error/);
  assert.match(f.get('model-status').textContent,/모델 정보.*Setup.*복구/);
  await f.click('mode-mixed');assert.match(f.get('model-status').textContent,/모델 정보.*설정.*다시 설치/);assert.equal(f.get('analyze').disabled,true);
});

test('update information explains unavailable integration and incompatible candidates',async()=>{
  const f=await panel();f.state.update={updateState:'UNAVAILABLE',checkState:'UNAVAILABLE',error:{code:'UPDATER_UNAVAILABLE',message:'private detail'}};await f.tick();
  assert.match(f.get('update-info').textContent,/Setup.*복구/);assert.doesNotMatch(f.get('update-info').textContent,/private detail/);assert.equal(f.get('update').disabled,true);
  f.state.update={updateState:'IDLE',checkState:'INCOMPATIBLE',candidate:{appVersion:'9.0.0',compatibilityReasons:['private path']}};await f.tick();
  assert.match(f.get('update-info').textContent,/9\.0\.0.*호환.*Premiere/);assert.equal(f.get('update').disabled,true);assert.doesNotMatch(f.get('update-info').textContent,/private path/);
});

test('update lookup failure offers retry and identifies integrity failures without server details',async()=>{
  const f=await panel();f.state.update={updateState:'IDLE',checkState:'CHECK_FAILED',error:{code:'UPDATE_FAILED',message:'secret CDN query'}};await f.tick();
  assert.match(f.get('update-info').textContent,/네트워크.*업데이트 확인/);assert.doesNotMatch(f.get('update-info').textContent,/secret CDN query/);
  for(const code of ['UPDATE_SIGNATURE','UPDATE_HASH']){f.state.update.error.code=code;await f.tick();assert.match(f.get('update-info').textContent,/검증.*업데이트 확인/);assert.ok(f.get('update-info').textContent.includes(code));}
  f.state.update.error={code:'unsafe/error/path',message:'private'};await f.tick();assert.doesNotMatch(f.get('update-info').textContent,/unsafe|private/);
  const checks=f.calls.filter(c=>c.path==='/updates/check').length;await f.click('check-update');assert.equal(f.calls.filter(c=>c.path==='/updates/check').length,checks+1);
});

test('update request limit shows remaining wait and clears expired countdown',async()=>{
  let now=100000;const clock=class extends Date{static now(){return now;}};
  const f=await panel({Date:clock});f.state.update={updateState:'IDLE',checkState:'CHECK_FAILED',error:{code:'UPDATE_RATE_LIMIT'},retryAt:102.2};await f.tick();
  assert.match(f.get('update-info').textContent,/요청.*제한.*3초.*업데이트 확인/);
  now=103000;await f.tick();assert.doesNotMatch(f.get('update-info').textContent,/3초|0초|NaN/);assert.match(f.get('update-info').textContent,/업데이트 확인/);
});

test('update phases show installation and recovery actions rather than internal enum names',async()=>{
  const f=await panel();
  for(const [phase,expected] of [['INSTALLING',/파일.*교체/],['VERIFYING_INSTALL',/설치.*검증/],['ROLLING_BACK',/이전 버전.*복구/],['RECOVERY_REQUIRED',/설치 복구/],['FAILED',/설치 복구/],['UNKNOWN_PHASE',/상태.*확인/]]){
    f.state.update={updateState:phase,checkState:'CURRENT'};await f.tick();assert.match(f.get('update-info').textContent,expected);assert.doesNotMatch(f.get('update-info').textContent,new RegExp(phase));
    assert.equal(f.get('recover-update').disabled,!['FAILED','RECOVERY_REQUIRED'].includes(phase));
  }
});

test('verified update remains immediately actionable during another lookup and intent guidance wins',async()=>{
  let finish;const held=new Promise(resolve=>{finish=resolve;});
  const f=await updatePanel({request:(path)=>path==='/updates/start'?held:undefined});f.state.update.checkState='CHECKING';await f.tick();
  assert.match(f.get('update-info').textContent,/새 버전.*0\.1\.1/);assert.equal(f.get('update').disabled,false);
  const click=f.click('update');for(let i=0;i<40;i++)await Promise.resolve();
  await f.tick();assert.match(f.get('update-info').textContent,/시작 요청/);assert.equal(f.calls.filter(c=>c.path==='/updates/start').length,1);assert.equal(f.get('analyze').disabled,true);
  finish({});await click;
});

test('retry intent replaces stale canceled failed or rolled-back update information',async()=>{
  for(const phase of ['CANCELED','FAILED_BEFORE_REPLACE','ROLLED_BACK']){
    let finish;const held=new Promise(resolve=>{finish=resolve;});
    const f=await updatePanel({request:path=>path==='/updates/start'?held:undefined});f.state.update.updateState=phase;await f.tick();
    const click=f.click('update');for(let i=0;i<40;i++)await Promise.resolve();
    await f.tick();const text=f.get('update-info').textContent;
    finish({});await click;
    assert.match(text,/시작 요청/);assert.equal(f.calls.filter(c=>c.path==='/updates/start').length,1);
  }
});

test('action guidance follows step navigation and missing analysis or plan without data calls',async()=>{
  const f=await panel(),before=f.calls.length;
  await f.nodes.find(n=>n.attrs['data-step']==='cut').onclick();
  assert.match(f.get('action-readiness')?.textContent||'',/화자.*분석/);assert.equal(f.get('plan').disabled,true);
  await f.nodes.find(n=>n.attrs['data-step']==='review').onclick();
  assert.match(f.get('action-readiness')?.textContent||'',/컷 편집.*편집안/);assert.equal(f.get('apply').disabled,true);
  assert.equal(f.calls.length,before);
  await f.click('analyze');await f.tick();await f.click('plan');
  assert.equal(f.get('apply').disabled,false);assert.match(f.get('action-readiness').textContent,/전체 편집안.*적용/);
});

test('action guidance names missing models and clears them after readiness changes',async()=>{
  const f=await panel();f.state.models.silero.status='missing';await f.tick();await f.click('next-step');
  assert.match(f.get('action-readiness')?.textContent||'',/발화 모델.*Setup.*복구/);assert.equal(f.get('analyze').disabled,true);
  assert.match(f.get('model-status').textContent,/발화 모델.*Setup.*복구/);
  f.state.models.silero.status='installed';await f.tick();
  assert.equal(f.get('analyze').disabled,false);assert.match(f.get('action-readiness').textContent,/무결성.*확인/);
  assert.doesNotMatch(f.get('action-readiness').textContent,/모델.*준비하세요/);
  f.state.models['community-1'].status='missing';await f.click('mode-mixed');
  assert.match(f.get('action-readiness').textContent,/설정.*혼합 녹음.*모델/);assert.equal(f.get('analyze').disabled,true);
});

test('action guidance prioritizes update over missing model and analysis',async()=>{
  const f=await updatePanel();f.state.models.silero.status='missing';f.evaluate('view.show("cut")');
  await f.click('update');
  assert.match(f.get('action-readiness')?.textContent||'',/업데이트/);assert.equal(f.get('plan').disabled,true);
  assert.equal(f.calls.filter(c=>c.path==='/updates/start').length,1);assert.equal(f.calls.some(c=>c.path==='/jobs'),false);
});

test('action guidance distinguishes heartbeat compatibility from a real component mismatch',async()=>{
  const f=await panel();f.state.compatible=false;f.state.admissionError={code:'HEARTBEAT_REQUIRED'};await f.tick();
  assert.match(f.get('action-readiness').textContent,/연결/);assert.doesNotMatch(f.get('action-readiness').textContent,/버전/);
  assert.equal(f.get('analyze').disabled,true);
  f.state.admissionError={code:'COMPONENT_MISMATCH'};await f.tick();
  assert.match(f.get('action-readiness').textContent,/버전/);assert.equal(f.get('analyze').disabled,true);
  f.state.compatible=true;f.state.admissionError=null;f.state.appVersion='different';await f.tick();
  assert.match(f.get('action-readiness').textContent,/버전/);assert.equal(f.get('analyze').disabled,true);
});

test('action guidance does not claim an update is running when the updater is unavailable',async()=>{
  const f=await panel();f.state.update={updateState:'UNAVAILABLE',checkState:'UNAVAILABLE'};await f.tick();
  await f.nodes.find(n=>n.attrs['data-step']==='cut').onclick();
  assert.match(f.get('action-readiness').textContent,/화자.*분석/);assert.doesNotMatch(f.get('action-readiness').textContent,/업데이트를 진행/);
  assert.equal(f.get('analyze').disabled,false);
});

test('action guidance reports source validation then restored step prerequisites',async()=>{
  const f=await panel();f.evaluate('view.show("cut")');f.validation(1);
  assert.match(f.get('action-readiness')?.textContent||'',/원본 파일.*확인/);assert.equal(f.get('plan').disabled,true);
  f.validation(0);assert.match(f.get('action-readiness').textContent,/화자.*분석/);
});

test('action guidance identifies missing sequence and explicit edit recovery',async()=>{
  const f=await panel();f.evaluate('resetSequence()');
  assert.match(f.get('action-readiness')?.textContent||'',/Premiere.*시퀀스/);assert.equal(f.get('analyze').disabled,true);
  f.state.applyRecovery.blocked=true;await f.tick();
  assert.match(f.get('action-readiness').textContent,/설정.*중단 작업/);assert.equal(f.get('analyze').disabled,true);
});

test('review distinguishes adjacent subsecond cuts by original frame bounds and duration',async()=>{
  const f=await panel();let actual;f.native.sequence.setPlayerPosition=async value=>{actual=value;};
  await f.reviewPlan('{segments:[{startFrame:1,endFrame:2,cameraId:"video:0",reason:"speech"},{startFrame:2,endFrame:3,cameraId:"video:0",reason:"speech"}],reviews:[]}');
  const rows=f.get('segments').children;
  assert.equal(rows[0].children.find(n=>n.className==='segment-timing')?.textContent,'1–2 프레임 · 1프레임 / 0.033초');
  assert.equal(rows[1].children.find(n=>n.className==='segment-timing')?.textContent,'2–3 프레임 · 1프레임 / 0.033초');
  await rows[1].onclick();assert.equal(actual,String(2n*BigInt(f.native.perFrame)));
});

test('review uses rational FPS for fractional rate durations and shows the exact sequence rate',async()=>{
  const native=nativeSnapshot();native.snapshot.fps={num:30000,den:1001};delete native.snapshot.snapshotHash;native.snapshot.snapshotHash=hash(native.snapshot);
  const f=await panel({native});
  assert.match(f.get('sequence-info').textContent,/29\.970 fps \(30000\/1001\)/);
  await f.reviewPlan('{segments:[{startFrame:29,endFrame:59,cameraId:"video:0",reason:"speech"}],reviews:[]}');
  assert.equal(f.get('segments').children[0].children.find(n=>n.className==='segment-timing')?.textContent,'29–59 프레임 · 30프레임 / 1.001초');
});

test('review searches displayed end frames and keeps the complete original edit plan',async()=>{
  const f=await panel();
  await f.reviewPlan('{segments:[{startFrame:1,endFrame:17,cameraId:"video:0",reason:"speech"},{startFrame:18,endFrame:25,cameraId:"video:0",reason:"speech"}],reviews:[]}');
  const before=f.evaluate('JSON.stringify(plan)');
  f.get('review-search').value='1–17';f.get('review-search').oninput();
  assert.equal(f.get('segments').children.length,1);
  assert.match(f.get('segments').children[0].children.find(n=>n.className==='segment-timing')?.textContent||'',/^1–17/);
  assert.equal(f.evaluate('JSON.stringify(plan)'),before);
});

test('long-form cut review pages and filters without changing the edit plan',async()=>{
  const f=await panel(),segments=Array.from({length:121},(_,i)=>({startFrame:i*2,endFrame:i*2+2,cameraId:i%2?'video:1':'video:0',reason:i%2?'OVERLAP_HOLD':'SPEAKER_TURN'}));
  const value={planHash:'p'.repeat(64),segments,reviews:[]},before=JSON.stringify(value);
  await f.reviewPlan(before);
  assert.equal(f.get('segments').children.length,50);
  assert.equal(f.get('review-previous').disabled,true);
  await f.click('review-next');assert.equal(f.get('segments').children.length,50);
  await f.click('review-next');assert.equal(f.get('segments').children.length,21);
  assert.equal(f.get('review-next').disabled,true);
  f.get('review-camera').value='video:1';f.get('review-camera').onchange();
  assert.equal(f.get('segments').children.length,50);
  f.get('review-search').value='화자 전환';f.get('review-search').oninput();
  assert.match(f.get('segments').children[0].textContent,/일치하는 컷/);
  assert.equal(f.get('review-next').disabled,true);
  await f.click('review-clear');assert.equal(f.get('segments').children.length,50);
  assert.equal(f.evaluate('JSON.stringify(plan)'),before);
  assert.equal(f.get('cut-count').textContent,'121');
});

test('a cut on a later review page seeks its original frame and update locks paging',async()=>{
  const f=await panel();let actual;f.native.sequence.setPlayerPosition=async value=>{actual=value;};
  await f.reviewPlan('{segments:Array.from({length:51},(_,i)=>({startFrame:i*2,endFrame:i*2+2,cameraId:"video:0",reason:"speech"})),reviews:[]}');
  await f.click('review-next');await f.get('segments').children[0].onclick();
  assert.equal(actual,String(100n*BigInt(f.native.perFrame)));
  f.state.gateOpen=false;f.state.update.updateState='QUIESCING';await f.tick();
  assert.equal(f.get('review-next').disabled,true);assert.equal(f.get('review-previous').disabled,true);
});

test('clearing analysis resets review filters and page status',async()=>{
  const f=await panel();await f.reviewPlan('{segments:[{startFrame:0,endFrame:2,cameraId:"video:0",reason:"speech"}],reviews:[]}');
  f.get('review-search').value='발화';f.get('review-search').oninput();
  f.get('review-camera').value='video:0';f.evaluate('clearAnalysis();toggle();');
  assert.equal(f.get('review-search').value,'');assert.equal(f.get('review-camera').value,'');
  assert.match(f.get('review-page-info').textContent,/편집안/);
  assert.equal(f.get('review-next').disabled,true);
});

test('update starts while preview stop is held and stale refresh cannot reopen editing',async()=>{
  let release;const held=new Promise(resolve=>{release=resolve;});
  const f=await updatePanel();f.evaluate('previewPlaying=true');f.host.ppro.SourceMonitor.play=()=>held;
  const click=f.click('update');for(let i=0;i<40;i++)await Promise.resolve();
  try{
    assert.equal(f.calls.filter(c=>c.path==='/updates/start').length,1);
    await f.tick();assert.equal(f.get('analyze').disabled,true);
    await f.click('update');assert.equal(f.calls.filter(c=>c.path==='/updates/start').length,1);
    f.state.gateOpen=false;f.state.epoch=1;f.state.stopEpoch=1;f.state.update.updateState='QUIESCING';f.state.update.updateEpoch=1;
    await f.tick();assert.equal(f.calls.filter(c=>c.path==='/updates/ack').length,0);
    assert.equal(f.calls.filter(c=>c.path==='/heartbeat').at(-1).body.quiescent,false);
  }finally{release();await click;}
  await f.tick();assert.ok(f.calls.some(c=>c.path==='/updates/ack'));
});

test('preview stop failure cannot prevent update start or claim quiescence',async()=>{
  const f=await updatePanel();f.evaluate('previewPlaying=true');f.host.ppro.SourceMonitor.play=async()=>{throw new Error('host playback failed');};
  await f.click('update');assert.equal(f.calls.filter(c=>c.path==='/updates/start').length,1);
  f.state.gateOpen=false;f.state.epoch=1;f.state.stopEpoch=1;f.state.update.updateState='QUIESCING';f.state.update.updateEpoch=1;
  await f.tick();assert.equal(f.calls.filter(c=>c.path==='/updates/ack').length,0);assert.equal(f.get('analyze').disabled,true);
});

test('verified candidate remains actionable during a background metadata recheck',async()=>{
  const f=await updatePanel();f.state.update.checkState='CHECKING';await f.tick();
  assert.equal(f.get('update').disabled,false);await f.click('update');assert.equal(f.calls.filter(c=>c.path==='/updates/start').length,1);
});

test('unknown update response retains the local stop and retry uses the same request identity',async()=>{
  let lose=true;
  const f=await updatePanel({request:async path=>{if(path==='/updates/start'&&lose)throw Object.assign(new Error('lost'),{code:'REQUEST_OUTCOME_UNKNOWN'});}});
  await f.click('update');await f.tick();assert.equal(f.get('analyze').disabled,true);
  lose=false;await f.click('update');const calls=f.calls.filter(c=>c.path==='/updates/start');assert.equal(calls.length,2);assert.equal(calls[0].body.requestId,calls[1].body.requestId);
});

test('unknown start retries retained identity after discovery loses its candidate',async()=>{
  let attempts=0;
  const f=await updatePanel({request:async path=>{if(path==='/updates/start')throw Object.assign(new Error('retry'),{code:++attempts===1?'REQUEST_OUTCOME_UNKNOWN':'UPDATE_CANDIDATE'});}});
  await f.click('update');f.state.update.candidate=null;f.state.update.checkState='CHECK_FAILED';await f.tick();
  assert.equal(f.get('analyze').disabled,true);await f.click('update');
  const calls=f.calls.filter(c=>c.path==='/updates/start');assert.equal(calls.length,2);assert.deepEqual(calls[0].body,calls[1].body);
  await f.tick();assert.equal(f.get('check-update').disabled,false);assert.equal(f.get('analyze').disabled,false);
});
test('panel opens with actual native tracks and no manual pairing UI',async()=>{
  const f=await panel();assert.equal(f.get('project-name').textContent,'sequence-1');assert.equal(f.get('track-count').textContent,'1 V / 1 A');
  assert.equal(f.calls.filter(c=>c.path==='/project').length,1);assert.ok(!f.get('pair-code'));assert.ok(f.get('analyze').disabled===false);
});

test('fresh resource settings keep an unset budget and save a device-only change',async()=>{
  const f=await panel({request:(path,body)=>path==='/resources'?{settings:{device:'cpu'},status:{cacheBytes:0,freeDiskBytes:10737418240}}:undefined});
  await f.click('open-settings');for(let i=0;i<10;i++)await Promise.resolve();
  assert.notEqual(f.get('cache-budget').value,'NaN');
  f.get('analysis-device').value='cuda';await f.click('save-resources');
  const save=f.calls.find(c=>c.path==='/resources'&&c.body);assert.equal(save.body.settings.device,'cuda');
  assert.ok(!('cacheBudgetBytes' in save.body.settings)||Number.isSafeInteger(save.body.settings.cacheBudgetBytes)&&save.body.settings.cacheBudgetBytes>0);
});

test('invalid visible budget is rejected before a resource write',async()=>{
  const f=await panel();await f.click('open-settings');for(let i=0;i<10;i++)await Promise.resolve();
  f.get('cache-budget').value='NaN';await f.click('save-resources');
  assert.equal(f.calls.filter(c=>c.path==='/resources'&&c.body).length,0);
});

test('pending installation has a specific status and no media request before bootstrap auth',async()=>{
  let ready=false;const f=await panel({connect:()=>{if(!ready)throw Object.assign(new Error('pending'),{code:'INSTALLATION_PENDING'});return {};}});
  assert.match(f.get('boot-status').querySelector('p').textContent,/설치.*연결/);
  assert.equal(f.calls.length,0);assert.equal(f.get('analyze').disabled,true);
  ready=true;await f.click('refresh');assert.equal(f.get('analyze').disabled,false);
});

test('invalid installation evidence is a repair state with no automatic retries',async()=>{
  const f=await panel({connect:()=>{throw Object.assign(new Error('invalid'),{code:'INSTALLATION_ENROLLMENT_INVALID'});}});
  assert.match(f.get('boot-status').querySelector('p').textContent,/복구/);
  await f.tick();assert.equal(f.connectCalls,1);assert.equal(f.calls.length,0);
});

test('installation automatic retry stops after its bounded wait and explicit refresh can resume',async()=>{
  let now=1000;const clock=class extends Date{static now(){return now;}};
  const f=await panel({Date:clock,connect:()=>{throw Object.assign(new Error('pending'),{code:'INSTALLATION_PENDING'});}});
  now+=601000;await f.tick();const attempts=f.connectCalls;
  now+=601000;await f.tick();assert.equal(f.connectCalls,attempts);
  assert.match(f.get('boot-status').querySelector('p').textContent,/대기를 마쳤/);
  await f.click('refresh');assert.equal(f.connectCalls,attempts+1);
});
test('range edit rebinds selected bounds without losing sequence identity',async()=>{
  const f=await panel();f.get('range-start').value='30';f.get('range-end').value='90';f.get('range-end').onchange();await f.click('analyze');
  const bind=f.calls.filter(c=>c.path==='/project').at(-1);assert.deepEqual(JSON.parse(JSON.stringify(bind.body.snapshot.range)),{startFrame:30,endFrame:90});assert.equal(bind.body.snapshot.sequenceRef,'sequence-1');
  assert.equal(f.calls.at(-1).path,'/jobs');assert.equal(f.calls.at(-1).body.options.microphones[0].streamIndex,0);
});
test('corrected analysis revision is used to create a reviewed plan',async()=>{
  const f=await panel();await f.click('analyze');await f.tick();
  const name=f.nodes.find(n=>n.tag==='button'&&n.textContent==='이름 저장');assert.ok(name);await name.onclick();
  await f.click('plan');const call=f.calls.find(c=>c.path==='/plan');assert.equal(call.body.analysisRevision,1);assert.equal(call.body.analysisId,'a'.repeat(64));assert.equal(f.get('view-review').className,'page');
});
test('timeline changes during analysis cannot promote stale results',async()=>{
  const f=await panel();await f.click('analyze');f.native=nativeSnapshot('other-sequence');await f.tick();
  assert.equal(f.calls.some(c=>c.path==='/analyses/register'),false);assert.equal(f.get('apply').disabled,true);assert.equal(f.get('plan').disabled,true);
});
test('project close invalidates the plan and clears visible source controls',async()=>{
  const f=await panel();await f.click('analyze');await f.tick();await f.click('plan');f.snapshotError='PROJECT_REQUIRED';await f.click('read-project');
  assert.equal(f.get('apply').disabled,true);assert.equal(f.get('cameras').children.length,0);assert.equal(f.get('project-name').textContent,'시퀀스를 열어 주세요');
});
test('sync method/stream payload follows the visible controls',async()=>{
  const f=await panel();f.get('sync-method').value='manual';f.get('sync-method').onchange();await f.click('sync');
  const call=f.calls.find(c=>c.path==='/jobs');assert.equal(call.body.options.method,'manual');assert.equal(call.body.options.sourceSelections.length,2);assert.equal(call.body.options.sourceSelections[0].channelIndex,0);assert.equal(call.body.options.manualOffsets.camera.confirmed,false);
});
function descendants(node){return [node,...node.children.flatMap(descendants)];}
function speakerCamera(f,id='A'){const row=f.get('speaker-mapping').children.find(row=>row.children.some(n=>n.textContent==='화자 '+id));return descendants(row).find(n=>n.tag==='select');}
function coveredSpeakers(f){return descendants(f.get('cameras')).find(n=>n.tag==='input');}
async function mixedAnalysis(f){await f.click('mode-mixed');await f.click('analyze');await f.tick();}
test('a second mixed analysis reseeding A and B requires new camera and coverage confirmation',async()=>{
  const f=await panel({speakerIds:['A','B']});await mixedAnalysis(f);
  for(const id of ['A','B']){const select=speakerCamera(f,id);select.value='video:0';select.onchange();}
  const covered=coveredSpeakers(f);covered.value='A, B';covered.onchange();
  const role=descendants(f.get('cameras')).find(n=>n.tag==='select');role.value='two-shot';role.onchange();
  f.get('reserve-camera').value='video:0';f.get('reserve-camera').onchange();
  await f.click('analyze');assert.equal(covered.value,'');f.analysisId='b'.repeat(64);await f.tick();
  assert.equal(speakerCamera(f,'A').value,'');assert.equal(speakerCamera(f,'B').value,'');assert.equal(covered.value,'');
  assert.equal(role.value,'two-shot');assert.equal(f.get('start-camera').value,'video:0');assert.equal(f.get('reserve-camera').value,'video:0');
});
test('persisted mixed mappings carry analysis scope and cannot restore into a fresh analysis',async()=>{
  const f=await panel();await mixedAnalysis(f);const select=speakerCamera(f);select.value='video:0';select.onchange();coveredSpeakers(f).value='A';
  await f.click('save-settings');const key=[...f.saved.rows.keys()].find(k=>k.startsWith('cut-settings-'));const settings=JSON.parse(f.saved.rows.get(key));
  assert.equal(settings.speakerMappingScope.analysisId,'a'.repeat(64));assert.equal(settings.speakerMappingScope.snapshotHash,f.bound.snapshotHash);
  await f.click('read-project');assert.equal(coveredSpeakers(f).value,'');
  await f.click('analyze');f.analysisId='b'.repeat(64);await f.tick();assert.equal(speakerCamera(f).value,'');assert.equal(coveredSpeakers(f).value,'');
});
test('correction of the same mixed analysis retains an explicitly chosen camera and coverage',async()=>{
  const f=await panel();await mixedAnalysis(f);const select=speakerCamera(f);select.value='video:0';select.onchange();coveredSpeakers(f).value='A';
  await f.nodes.find(n=>n.tag==='button'&&n.textContent==='이름 저장').onclick();
  assert.equal(speakerCamera(f).value,'video:0');assert.equal(coveredSpeakers(f).value,'A');await f.click('plan');
  assert.equal(f.calls.find(c=>c.path==='/plan').body.mapping.speakers.A,'video:0');
});
test('separate microphone camera mapping cannot transfer to a different source instance with the same speaker ID',async()=>{
  const native=nativeSnapshot();native.snapshot.tracks.push({trackRef:'video:1',mediaType:'video',index:1,name:'Other',muted:false});
  native.snapshot.clips.push({...native.snapshot.clips[0],instanceKey:'other-camera',trackRef:'video:1'});delete native.snapshot.snapshotHash;native.snapshot.snapshotHash=hash(native.snapshot);
  const f=await panel({native});const select=speakerCamera(f);select.value='video:1';select.onchange();
  const mic=f.native.snapshot.clips.find(c=>c.mediaType==='audio');mic.instanceKey='different-mic';mic.assetId='different-source';f.native.snapshot.sources.push({assetId:'different-source',canonicalPath:'D:/owned/different.wav'});
  delete f.native.snapshot.snapshotHash;f.native.snapshot.snapshotHash=hash(f.native.snapshot);await f.click('read-project');assert.equal(speakerCamera(f).value,'video:0');
});
test('cut buttons display actual multiple reasons plus manual fallback and unknown future codes',async()=>{
  const f=await panel({request:async(path,_body,f)=>path==='/plan'?{planHash:'p'.repeat(64),snapshotHash:f.bound.snapshotHash,reviews:[],segments:[
    {startFrame:0,endFrame:100,cameraId:'video:0',reason:'SPEAKER_TURN',reasonCodes:['SPEAKER_TURN','MIN_SHOT_EXCEPTION_COVERAGE','HOLD','UNKNOWN_HOLD','FUTURE_CAMERA_RULE','__proto__']},
    {startFrame:100,endFrame:200,cameraId:'video:0',reason:'MANUAL_OVERRIDE',reasonCodes:['MIN_SHOT_EXCEPTION_OVERRIDE']},
    {startFrame:200,endFrame:300,cameraId:'video:0',reason:'VIDEO_GAP_FALLBACK',reasonCodes:['RANGE_END_SHORT']}]}:undefined});
  await f.click('analyze');await f.tick();await f.click('plan');const rows=f.get('segments').children;
  const text=row=>descendants(row).map(n=>n.textContent).join(' ');
  assert.match(text(rows[0]),/화자 전환/);assert.match(text(rows[0]),/영상 공백으로 최소 샷 길이 예외/);assert.match(text(rows[0]),/FUTURE_CAMERA_RULE/);
  assert.match(text(rows[0]),/__proto__/);
  assert.match(text(rows[0]),/카메라 유지/);assert.match(text(rows[0]),/화자 불확실로 카메라 유지/);
  assert.match(text(rows[1]),/수동 카메라 지정/);assert.match(text(rows[1]),/수동 지정으로 최소 샷 길이 예외/);
  assert.match(text(rows[2]),/영상 공백으로 대체 카메라/);assert.match(text(rows[2]),/분석 범위 끝의 짧은 샷/);
  for(const row of rows){assert.equal(row.tag,'button');assert.equal(typeof row.onclick,'function');}
});
test('unreadable local edit intent keeps authenticated settings and recovery reachable while editing is blocked',async()=>{
  let resolved=false;
  const f=await panel({storage:saved=>{saved.rows.set('cut-native-edit-intent-1','unreadable');const get=saved.getItem;saved.getItem=async k=>{if(k==='cut-native-edit-intent-1'&&saved.rows.has(k))throw new Error('decrypt');return get(k);};},
    request:async(path)=>path==='/apply/recover'?{resolved}:undefined});
  assert.ok(f.calls.some(c=>c.path==='/state'));assert.equal(f.get('analyze').disabled,true);assert.equal(f.get('recover-apply').disabled,false);
  assert.match(f.get('apply-recovery-text').textContent,/EDIT_INTENT_STORAGE_UNAVAILABLE/);f.get('open-settings').onclick();await Promise.resolve();assert.ok(f.calls.some(c=>c.path==='/resources'));
  await f.click('recover-apply');assert.ok(f.saved.rows.has('cut-native-edit-intent-1'));assert.equal(f.get('analyze').disabled,true);
  resolved=true;await f.click('recover-apply');assert.equal(f.saved.rows.has('cut-native-edit-intent-1'),false);await f.tick();assert.equal(f.get('analyze').disabled,false);
});
test('initial panel conflict is diagnosed and only an explicit retry can reconnect after the owner becomes inactive',async()=>{
  let occupied=true;const f=await panel({connect:()=>{if(occupied)throw Object.assign(new Error('conflict'),{code:'PANEL_CONTEXT_CONFLICT'});return {};}});
  assert.match(f.get('boot-status').querySelector('p').textContent,/다른 CUT 패널/);await f.tick();assert.equal(f.connectCalls,1);
  occupied=false;await f.click('refresh');assert.equal(f.connectCalls,2);assert.equal(f.get('analyze').disabled,false);assert.equal(f.resetCalls,0);
});
test('a displaced panel context stops automatic reconnect attempts without resetting the owner identity',async()=>{
  let displaced=false;const f=await panel({request:async path=>{if(displaced&&path==='/heartbeat')throw Object.assign(new Error('conflict'),{code:'PANEL_CONTEXT_CONFLICT'});}});
  displaced=true;await f.tick();assert.match(f.get('connection').textContent,/다른 CUT 패널/);assert.equal(f.get('analyze').disabled,true);
  await f.tick();assert.equal(f.connectCalls,1);assert.equal(f.resetCalls,0);
});

async function savedCorrectedAnalysis(){
  const f=await panel();await mixedAnalysis(f);const select=speakerCamera(f);select.value='video:0';select.onchange();coveredSpeakers(f).value='A';
  await f.nodes.find(n=>n.tag==='button'&&n.textContent==='이름 저장').onclick();await f.click('save-settings');
  const key=[...f.saved.rows.keys()].find(k=>k.startsWith('cut-settings-'));return {f,key,settings:JSON.parse(f.saved.rows.get(key))};
}
test('settings save the exact completed analysis reference and restore its correction without a new job',async()=>{
  const {key,settings}=await savedCorrectedAnalysis();assert.equal(settings.analysisReference.analysisId,'a'.repeat(64));assert.equal(settings.analysisReference.jobId,'job-1');assert.equal(settings.analysisReference.revision,1);
  const f=await panel({analysisRevision:1,storage:s=>s.rows.set(key,JSON.stringify(settings))});
  assert.equal(f.get('plan').disabled,true);await f.click('load-settings');assert.equal(f.get('plan').disabled,false);
  assert.equal(f.calls.some(c=>c.path==='/jobs'),false);assert.equal(speakerCamera(f).value,'video:0');assert.equal(coveredSpeakers(f).value,'A');
  await f.click('plan');const planned=f.calls.find(c=>c.path==='/plan');assert.equal(planned.body.jobId,'job-1');assert.equal(planned.body.analysisRevision,1);
});
test('a stored analysis cannot be restored onto changed media or microphone settings',async()=>{
  const {key,settings}=await savedCorrectedAnalysis();
  for(const changed of ['media','channel']){
    const saved=structuredClone(settings),native=nativeSnapshot();
    if(changed==='media'){native.snapshot.sources[1].canonicalPath='D:/owned/replaced.wav';delete native.snapshot.snapshotHash;native.snapshot.snapshotHash=hash(native.snapshot);}
    else saved.microphones[0].channel='2';
    const f=await panel({native,analysisRevision:1,storage:s=>s.rows.set(key,JSON.stringify(saved))});await f.click('load-settings');
    assert.equal(f.get('plan').disabled,true);assert.equal(f.calls.some(c=>c.path==='/analyses/'+'a'.repeat(64)),false);assert.match(f.get('status').textContent,/이전 분석/);
  }
});
test('restoring an analysis rejects missing owned work or a regressed correction history',async()=>{
  const {key,settings}=await savedCorrectedAnalysis();
  for(const broken of ['job','history']){
    const f=await panel({storage:s=>s.rows.set(key,JSON.stringify(settings)),request:async path=>{if(broken==='job'&&path==='/jobs/job-1')throw Object.assign(new Error('JOB_SCOPE'),{code:'JOB_SCOPE'});}});
    await f.click('load-settings');assert.equal(f.get('plan').disabled,true);assert.equal(f.calls.some(c=>c.path==='/jobs'),false);
    assert.ok(f.saved.rows.has(key));assert.match(f.get('status').textContent,broken==='job'?/JOB_SCOPE/:/교정 이력/);
  }
});

test('heartbeat continues while completed job content verification is waiting',async()=>{
  let entered,release;const started=new Promise(resolve=>{entered=resolve;}),held=new Promise(resolve=>{release=resolve;});
  const f=await panel({request:async path=>{if(path==='/jobs/job-1'){entered();await held;}}});await f.click('analyze');
  const firstTick=f.tick();await started;const before=f.calls.filter(c=>c.path==='/heartbeat').length;
  await f.tick();const after=f.calls.filter(c=>c.path==='/heartbeat').length;release();await firstTick;assert.equal(after,before+1);
});
test('pending source verification is visible and can be explicitly canceled without a native batch',async()=>{
  const f=await panel();assert.equal(typeof f.validation,'function');f.validation(1);assert.equal(f.get('cancel').disabled,false);assert.match(f.get('status').textContent,/원본 파일/);
  await f.click('cancel');assert.equal(f.cancelValidationCalls,1);assert.equal(f.calls.some(c=>c.path==='/apply/batch-end'),false);f.validation(0);assert.equal(f.get('cancel').disabled,true);
});
test('rejected changed media ends completed-job polling and leaves reanalysis available',async()=>{
  const f=await panel({request:async path=>{if(path==='/jobs/job-1')throw Object.assign(new Error('changed'),{code:'SOURCE_CHANGED'});}});
  await f.click('analyze');await f.tick();assert.equal(f.get('plan').disabled,true);assert.equal(f.get('analyze').disabled,false);assert.match(f.get('status').textContent,/원본 파일/);
  const before=f.calls.filter(c=>c.path==='/jobs/job-1').length;await f.tick();assert.equal(f.calls.filter(c=>c.path==='/jobs/job-1').length,before);
});

for(const code of ['SOURCE_REANALYSIS_REQUIRED','VALIDATION_EXPIRED','VALIDATION_WORKER_EXITED','VALIDATION_STALE','VALIDATION_UNAVAILABLE','VALIDATION_SCOPE','CANCELED','CONTINUATION_EXPIRED']){
  test('terminal '+code+' ends job reads and permits only explicit reanalysis',async()=>{
    let rejected=false;
    const f=await panel({request:async path=>{if(rejected&&path==='/jobs/job-1')throw Object.assign(new Error('Private local connection needs attention.'),{code});}});
    await f.click('analyze');await f.tick();await f.click('plan');assert.equal(f.get('apply').disabled,false);
    const reads=f.calls.filter(c=>c.path==='/jobs/job-1').length,registrations=f.calls.filter(c=>c.path==='/analyses/register').length;
    rejected=true;await f.click('analyze');await f.tick();
    assert.equal(f.get('analyze').disabled,false);assert.equal(f.get('plan').disabled,true);assert.equal(f.get('apply').disabled,true);
    assert.match(f.get('status').textContent,/[가-힣]/);assert.match(f.get('status').textContent,/다시|재시도/);assert.doesNotMatch(f.get('status').textContent,/Private local|VALIDATION_|CONTINUATION_|CANCELED/);
    const jobs=f.calls.filter(c=>c.path==='/jobs').length;
    await f.tick();await f.tick();
    assert.equal(f.calls.filter(c=>c.path==='/jobs/job-1').length,reads+1);assert.equal(f.calls.filter(c=>c.path==='/jobs').length,jobs);
    assert.equal(f.calls.filter(c=>c.path==='/analyses/register').length,registrations);assert.equal(f.get('connection').textContent,'편집 준비됨');assert.equal(f.resetCalls,0);
    rejected=false;await f.click('analyze');await f.tick();
    assert.equal(f.calls.filter(c=>c.path==='/jobs').length,jobs+1);assert.equal(f.get('plan').disabled,false);
  });
}

test('validation capacity failure preserves the active job for subsequent polling',async()=>{
  let busy=true;const f=await panel({request:async path=>{if(busy&&path==='/jobs/job-1')throw Object.assign(new Error('busy'),{code:'VALIDATION_BUSY'});}});
  await f.click('analyze');await f.tick();assert.equal(f.get('analyze').disabled,true);
  busy=false;await f.tick();assert.equal(f.calls.filter(c=>c.path==='/jobs/job-1').length,2);assert.equal(f.get('plan').disabled,false);
});

test('edit capacity rejection gives Korean range and cut-count guidance before apply',async()=>{
  const f=await panel({request:async path=>{if(path==='/plan')throw Object.assign(new Error('limit'),{code:'APPLY_CAPACITY_EXCEEDED'});}});
  await f.click('analyze');await f.tick();await f.click('plan');
  assert.match(f.get('status').textContent,/8,192/);assert.match(f.get('status').textContent,/범위.*나누거나.*컷 수/);
  assert.equal(f.get('apply').disabled,true);assert.equal(f.calls.some(c=>c.path==='/apply/begin'),false);
});

test('cache maintenance recovery is explicit and disabled until actual drain',async()=>{
  const f=await panel({request:async(path,body,f)=>{if(path==='/resources/prune'&&body?.action==='release'){f.state.gateOpen=true;f.state.maintenance=null;return {released:true};}}});
  f.state.gateOpen=false;f.state.maintenance={status:'canceled',drained:false,canRelease:false};await f.tick();
  assert.equal(f.get('release-cache').disabled,true);
  f.state.maintenance.drained=true;f.state.maintenance.canRelease=true;await f.tick();
  assert.equal(f.get('release-cache').disabled,false);assert.equal(f.get('analyze').disabled,true);
  assert.equal(f.calls.filter(c=>c.path==='/resources/prune').length,0);
  await f.click('release-cache');assert.equal(f.calls.filter(c=>c.path==='/resources/prune'&&c.body.action==='release').length,1);
  assert.equal(f.get('analyze').disabled,false);assert.match(f.get('status').textContent,/편집.*계속/);
});

test('job authentication failure retains connection recovery and never promotes a result',async()=>{
  const f=await panel({request:async path=>{if(path==='/jobs/job-1')throw Object.assign(new Error('auth'),{code:'AUTH_REQUIRED'});}});
  await f.click('analyze');await f.tick();assert.equal(f.resetCalls,1);assert.equal(f.get('analyze').disabled,true);
  assert.equal(f.get('apply').disabled,true);assert.equal(f.calls.some(c=>c.path==='/analyses/register'),false);assert.match(f.get('connection').textContent,/복구/);
  assert.match(f.get('action-readiness').textContent,/연결/);
});
