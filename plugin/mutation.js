/* A durable permit is consumed once, immediately before each native mutation. */
function controller(host,sourceSequenceRef,control){
  for(const key of ['check','beforeBatch','afterBatch','onResult'])
    if(typeof control?.[key]!=='function')throw new Error('HOST_DURABLE_AUTHORIZATION_REQUIRED');
  let ordinal=0,resultSequenceRef=null,halted=false;
  async function check(){await control.check();if(control.stopped?.())throw new Error('CANCELED');}
  async function run(name,operation){
    if(halted)throw new Error('HOST_BATCH_UNCERTAIN');
    await check();const batchId=++ordinal;
    const operationDigest=host.hash({name,sourceSequenceRef,ordinal:batchId});
    let permit;
    try{permit=await control.beforeBatch({batchId,operationDigest,resultSequenceRef});}
    catch(error){halted=true;throw error;}
    if(permit?.execute!==true){halted=true;throw new Error('HOST_BATCH_REPLAY_OR_UNCERTAIN');}
    // No further asynchronous work occurs between this check and a synchronous
    // Adobe transaction. Asynchronous creation stays outstanding until return.
    if(control.stopped?.()){halted=true;throw new Error('CANCELED');}
    control.onBatch?.(true);
    try{
      const value=await operation();
      await control.afterBatch({batchId,receipt:{transactionReturned:true}});
      return value;
    }catch(error){halted=true;throw error;}finally{control.onBatch?.(false);}
  }
  async function result(sequenceRef){
    if(typeof sequenceRef!=='string'||!sequenceRef||resultSequenceRef&&sequenceRef!==resultSequenceRef)throw new Error('HOST_RESULT_IDENTITY_FAILED');
    try{await control.onResult(sequenceRef);resultSequenceRef=sequenceRef;}
    catch(error){halted=true;throw error;}
  }
  return {check,run,result,batch:(project,name,build)=>run(name,()=>host.transaction(project,name,build))};
}
module.exports={controller};
