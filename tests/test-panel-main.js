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
    if(path==='/plan')return {planHash:'p'.repeat(64),snapshotHash:f.bound.snapshotHash,segments:[{startFrame:0,endFrame:300,cameraId:'video:0',reason:'speech'}],reviews:[]};
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
  const f=await updatePanel();f.evaluate("syncJob='job-1';syncResult={};");f.get('sync-result').textContent='old result';f.evaluate('toggle()');assert.equal(f.get('apply-sync').disabled,false);
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
  f.evaluate('plan={segments:[{startFrame:1,endFrame:2,cameraId:"video:0",reason:"speech"},{startFrame:2,endFrame:3,cameraId:"video:0",reason:"speech"}],reviews:[]};renderPlan();');
  const rows=f.get('segments').children;
  assert.equal(rows[0].children.find(n=>n.className==='segment-timing')?.textContent,'1–2 프레임 · 1프레임 / 0.033초');
  assert.equal(rows[1].children.find(n=>n.className==='segment-timing')?.textContent,'2–3 프레임 · 1프레임 / 0.033초');
  await rows[1].onclick();assert.equal(actual,String(2n*BigInt(f.native.perFrame)));
});

test('review uses rational FPS for fractional rate durations and shows the exact sequence rate',async()=>{
  const native=nativeSnapshot();native.snapshot.fps={num:30000,den:1001};delete native.snapshot.snapshotHash;native.snapshot.snapshotHash=hash(native.snapshot);
  const f=await panel({native});
  assert.match(f.get('sequence-info').textContent,/29\.970 fps \(30000\/1001\)/);
  f.evaluate('plan={segments:[{startFrame:29,endFrame:59,cameraId:"video:0",reason:"speech"}],reviews:[]};renderPlan();');
  assert.equal(f.get('segments').children[0].children.find(n=>n.className==='segment-timing')?.textContent,'29–59 프레임 · 30프레임 / 1.001초');
});

test('review searches displayed end frames and keeps the complete original edit plan',async()=>{
  const f=await panel();
  f.evaluate('plan={segments:[{startFrame:1,endFrame:17,cameraId:"video:0",reason:"speech"},{startFrame:18,endFrame:25,cameraId:"video:0",reason:"speech"}],reviews:[]};renderPlan();');
  const before=f.evaluate('JSON.stringify(plan)');
  f.get('review-search').value='1–17';f.get('review-search').oninput();
  assert.equal(f.get('segments').children.length,1);
  assert.match(f.get('segments').children[0].children.find(n=>n.className==='segment-timing')?.textContent||'',/^1–17/);
  assert.equal(f.evaluate('JSON.stringify(plan)'),before);
});

test('long-form cut review pages and filters without changing the edit plan',async()=>{
  const f=await panel(),segments=Array.from({length:121},(_,i)=>({startFrame:i*2,endFrame:i*2+2,cameraId:i%2?'video:1':'video:0',reason:i%2?'OVERLAP_HOLD':'SPEAKER_TURN'}));
  const value={planHash:'p'.repeat(64),segments,reviews:[]},before=JSON.stringify(value);
  f.evaluate('plan='+before+';renderPlan();');
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
  f.evaluate('plan={segments:Array.from({length:51},(_,i)=>({startFrame:i*2,endFrame:i*2+2,cameraId:"video:0",reason:"speech"})),reviews:[]};renderPlan();');
  await f.click('review-next');await f.get('segments').children[0].onclick();
  assert.equal(actual,String(100n*BigInt(f.native.perFrame)));
  f.state.gateOpen=false;f.state.update.updateState='QUIESCING';await f.tick();
  assert.equal(f.get('review-next').disabled,true);assert.equal(f.get('review-previous').disabled,true);
});

test('clearing analysis resets review filters and page status',async()=>{
  const f=await panel();f.evaluate('plan={segments:[{startFrame:0,endFrame:2,cameraId:"video:0",reason:"speech"}],reviews:[]};renderPlan();');
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
