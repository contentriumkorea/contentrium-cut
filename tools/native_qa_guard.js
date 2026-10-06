/* Unshipped QA boundary. Prepared paths are local constants, never server input. */
'use strict';
function filePath(value){
  if(typeof value!=='string'||!value||/[\0]/.test(value))return null;
  const path=value.replace(/\\/g,'/');
  if(!/^[a-z]:\//i.test(path)||path.slice(2).includes(':')||path.includes('//')||path.split('/').some(v=>v==='.'||v==='..'||v===''))return null;
  return path.toLowerCase();
}
function verify(live,expected,pinnedProjectRef){
  const snapshot=live?.snapshot,actualPath=filePath(live?.project?.path),wantedPath=filePath(expected.projectPath);
  if(!wantedPath||actualPath!==wantedPath||typeof snapshot?.projectRef!=='string'||!snapshot.projectRef||
    (pinnedProjectRef&&snapshot.projectRef!==pinnedProjectRef))throw new Error('OWNED_QA_PROJECT_REQUIRED');
  const wanted=new Set(expected.paths.map(filePath)),sources=snapshot.sources;
  if(wanted.has(null)||wanted.size!==4||!Array.isArray(sources)||sources.length!==wanted.size||
    sources.some(s=>s.offline!==false||!wanted.has(filePath(s.canonicalPath)))||
    new Set(sources.map(s=>filePath(s.canonicalPath))).size!==wanted.size||
    new Set(sources.map(s=>s.assetId)).size!==wanted.size)throw new Error('OWNED_QA_MEDIA_REQUIRED');
  return snapshot.projectRef;
}
module.exports={verify};
