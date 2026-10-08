/* Display-only filtering. The source EditPlan remains the apply contract. */
const PAGE_SIZE=50;
function window(segments,{cameraId='',query='',page=0}={},describe=()=> ''){
  const words=String(query).trim().toLowerCase().split(/\s+/).filter(Boolean);
  const matches=segments.filter(segment=>{
    if(cameraId&&segment.cameraId!==cameraId)return false;
    const text=String(describe(segment)).toLowerCase();
    return words.every(word=>text.includes(word));
  });
  const pages=Math.ceil(matches.length/PAGE_SIZE);
  const index=Math.max(0,Math.min(Number.isSafeInteger(page)?page:0,Math.max(0,pages-1)));
  const offset=index*PAGE_SIZE;
  return {rows:matches.slice(offset,offset+PAGE_SIZE),total:matches.length,index,pages,
    first:matches.length?offset+1:0,last:Math.min(offset+PAGE_SIZE,matches.length),
    previous:index>0,next:index+1<pages};
}
module.exports={window};
