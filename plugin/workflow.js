/* Persist client intent before requesting a one-use native editing permit. */
const INTENT_KEY='cut-native-edit-intent-1';
function fail(code){const e=new Error(code);e.code=code;return e;}
function create({api,storage,randomId,onBatch=()=>{},onResult=()=>{},stopped=()=>false}){
  let running=false;
  async function pending(){
    let raw;
    try{
      if(typeof storage.key==='function'&&Number.isSafeInteger(storage.length)){
        let present=false;for(let i=0;i<storage.length;i++)if(storage.key(i)===INTENT_KEY)present=true;
        if(!present)return null;
      }
      raw=await storage.getItem(INTENT_KEY);
    }catch(e){
      throw fail('EDIT_INTENT_STORAGE_UNAVAILABLE');
    }
    if(raw===undefined||raw===null)return null;
    try{
      const value=JSON.parse(typeof raw==='string'?raw:new TextDecoder().decode(raw));
      if(value.schemaVersion!==1||typeof value.requestId!=='string'||!value.requestId||!['edit','sync','input'].includes(value.kind))throw new Error();
      return value;
    }catch(_){throw fail('EDIT_INTENT_CORRUPT');}
  }
  async function save(value){await storage.setItem(INTENT_KEY,JSON.stringify(value));}
  async function clear(){await storage.removeItem(INTENT_KEY);}
  async function run({kind,body,beginPath='/apply/begin',native,receipt}){
    if(running)throw fail('APPLY_BUSY');
    running=true;
    let intent=null,approved=null,resultSequenceRef=null,completionSent=false;
    try{
      if(await pending())throw fail('APPLY_RECOVERY_REQUIRED');
      intent={schemaVersion:1,requestId:randomId(),kind,body};
      await save(intent);
      approved=await api(beginPath,{...body,requestId:intent.requestId});
      if(!approved?.applyId)throw fail('APPLY_AUTHORIZATION_INVALID');
      intent.applyId=approved.applyId;await save(intent);
      if(approved.execute!==true)throw fail('APPLY_REPLAY_OR_UNCERTAIN');
      const base={applyId:approved.applyId,epoch:approved.epoch};
      const control={
        capability:approved.capability,
        stopped,onBatch,
        check:async()=>{if(stopped())throw fail('CANCELED');await api('/apply/check',base);if(stopped())throw fail('CANCELED');},
        beforeBatch:batch=>api('/apply/check',{...base,...batch}),
        afterBatch:batch=>api('/apply/batch-end',{...base,...batch}),
        onResult:async sequenceRef=>{
          resultSequenceRef=sequenceRef;
          await api('/apply/result',{...base,resultSequenceRef:sequenceRef});
          intent.resultSequenceRef=sequenceRef;await save(intent);onResult(sequenceRef);
        }
      };
      const result=await native(approved,control);
      const readback=receipt(approved,result);
      completionSent=true;
      const ended=await api('/apply/end',{...base,status:'completed',receipt:readback});
      if(ended.status!=='completed'||ended.needsRecovery===true)throw fail('APPLY_COMPLETION_UNCERTAIN');
      await clear();
      return result;
    }catch(e){
      // A lost completion response may already be committed. Never replace it
      // with a contradictory cancellation or repeat a native transaction.
      if(approved?.execute===true&&!completionSent){
        await api('/apply/end',{applyId:approved.applyId,epoch:approved.epoch,status:stopped()?'canceled':'failed',
          receipt:{resultSequenceRef:resultSequenceRef||e.resultSequenceRef||null}}).catch(()=>{});
      }
      if(resultSequenceRef)e.resultSequenceRef=resultSequenceRef;
      throw e;
    }finally{running=false;onBatch(false);}
  }
  async function recover(){
    if(running)throw fail('APPLY_BUSY');
    let intent=null,unreadable=false;
    try{intent=await pending();}catch(e){if(!['EDIT_INTENT_STORAGE_UNAVAILABLE','EDIT_INTENT_CORRUPT'].includes(e.code))throw e;unreadable=true;}
    const result=await api('/apply/recover',{requestId:intent?.requestId||null,applyId:intent?.applyId||null,acknowledged:true});
    if(result.resolved!==true)throw fail('APPLY_RECOVERY_REQUIRED');
    if(intent||unreadable)await clear();
    return result;
  }
  return {run,pending,recover};
}
module.exports={create,INTENT_KEY};
