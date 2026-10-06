const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const expected={projectPath:'D:/owned/Contentrium CUT 60min QA.prproj',paths:['A.mp4','B.mp4','C.mp4','master.wav'].map(n=>'D:/owned/fixture/'+n),exportPath:'D:/owned'};
function harness(){
  let live={project:{path:expected.projectPath},snapshot:{projectRef:'owned-guid',projectName:'Contentrium CUT 60min QA',sources:expected.paths.map((canonicalPath,i)=>({assetId:String(i),canonicalPath,offline:false}))}};
  const elements=new Map(),host={ppro:{},snapshot:async()=>live};
  const context=vm.createContext({ContentriumHost:host,document:{getElementById:id=>{if(!elements.has(id))elements.set(id,{});return elements.get(id);}},
    require:name=>name==='./sync.js'?{install(){}}:name==='./qa-config.js'?structuredClone(expected):require('../tools/native_qa_guard'),
    fetch:async()=>({json:async()=>({...expected,projectPath:'D:/unowned/Contentrium CUT 60min QA.prproj'})})});
  vm.runInContext(fs.readFileSync('tools/native_qa_main.js','utf8'),context);
  return {context,elements,get live(){return live;},set live(v){live=v;}};
}
test('owned native harness accepts the exact generated project and media paths',async()=>{const f=harness();assert.equal(await f.context.owned(),f.live);});
test('matching QA display name cannot authorize another project path',async()=>{const f=harness();f.live.project.path='D:/user/Contentrium CUT 60min QA.prproj';await assert.rejects(f.context.owned(),/OWNED_QA_PROJECT_REQUIRED/);});
test('owned native harness rejects foreign or missing media before mutation',async()=>{
  for(const change of [f=>{f.live.snapshot.sources[0].canonicalPath='D:/user/interview.mp4';},f=>{f.live.snapshot.sources.pop();},f=>{f.live.snapshot.sources[0].offline=true;}]){const f=harness();change(f);await assert.rejects(f.context.owned(),/OWNED_QA_MEDIA_REQUIRED/);}
});
test('owned native harness rejects path traversal rather than normalizing it into authorization',async()=>{const f=harness();f.live.project.path='D:/owned/../owned/Contentrium CUT 60min QA.prproj';await assert.rejects(f.context.owned(),/OWNED_QA_PROJECT_REQUIRED/);});
test('owned native harness pins the project GUID after the first successful observation',async()=>{const f=harness();await f.context.owned();f.live.snapshot.projectRef='other-guid';await assert.rejects(f.context.owned(),/OWNED_QA_PROJECT_REQUIRED/);});
test('fixture creation uses prepared local paths, never mutable HTTP fixture paths',async()=>{
  const f=harness();let received;f.context.ContentriumHost.createFixture=async(paths,projectPath)=>{received={paths:[...paths],projectPath};return {};};
  await f.elements.get('fixture').onclick();assert.deepEqual(received, {paths:expected.paths,projectPath:expected.projectPath});
});
