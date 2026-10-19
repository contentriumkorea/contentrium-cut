/* Shared by the native panel and the QA-only preview. No host or data calls. */
(function(root){
  function install(document){
    const get=id=>document.getElementById(id),sections=['tracks','speakers','cut'];
    let current='workbench',changed=null,hasAnalysis=false,hasPlan=false;
    function render(){
      const settings=current==='settings';
      for(const name of sections)get('view-'+name).className='page'+(settings?' hidden':'');
      get('view-review').className='page'+(!settings&&hasPlan?'':' hidden');
      get('view-settings').className='page'+(settings?'':' hidden');
      const action=settings?'close-settings':hasPlan?'apply':hasAnalysis?'plan':'analyze';
      for(const id of ['analyze','plan','apply','close-settings'])get(id).className='primary'+(id===action?'':' hidden');
      get('reanalyze').className='text-button'+(hasAnalysis?'':' hidden');
      get('show-review').className='text-button full'+(!settings&&hasPlan?'':' hidden');
      get('open-settings').setAttribute('aria-expanded',String(settings));
    }
    function show(next){
      if(!['workbench','settings','review',...sections].includes(next))return;
      const target=next==='settings'?'settings':'workbench';
      if(current!==target){current=target;get('workspace').scrollTop=0;}
      render();if(changed)changed();
      if(next==='review'&&hasPlan){
        const target=get('view-review'),workspace=get('workspace');
        if(typeof target.scrollIntoView==='function')target.scrollIntoView();
        else if(Number.isFinite(target.offsetTop)&&Number.isFinite(workspace.offsetTop))workspace.scrollTop=Math.max(0,target.offsetTop-workspace.offsetTop);
        const heading=get('review-heading');if(typeof heading.focus==='function')heading.focus();
      }
    }
    function setDisclosure(button,open){
      const id=button.getAttribute('data-disclosure');
      button.setAttribute('data-open',String(open));button.setAttribute('aria-expanded',String(open));
      button.setAttribute('aria-controls',id);
      get(id).className='disclosure-content'+(open?'':' hidden');
    }
    function openDisclosure(id){
      // Advanced subcontrols must not remain hidden behind the outer disclosure.
      if(['disclosure-2','disclosure-4','disclosure-5','disclosure-6'].includes(id))openDisclosure('advanced-settings');
      for(const button of document.querySelectorAll('[data-disclosure]'))if(button.getAttribute('data-disclosure')===id)setDisclosure(button,true);
    }
    function source(value){
      get('project-selection-panel').className='disclosure control-well'+(value==='project'?'':' hidden');
      for(const [id,active] of [['read-selection',value==='project'],['read-project',value!=='project']]){
        get(id).className='source-button'+(active?' active':'');get(id).setAttribute('aria-pressed',String(active));
      }
      if(value==='project')openDisclosure('disclosure-3');
    }
    for(const button of document.querySelectorAll('[data-disclosure]')){
      setDisclosure(button,button.getAttribute('data-open')==='true');
      button.onclick=()=>setDisclosure(button,get(button.getAttribute('data-disclosure')).className.split(/\s+/).includes('hidden'));
    }
    get('open-settings').onclick=()=>show(current==='settings'?'workbench':'settings');
    get('close-settings').onclick=()=>show('workbench');
    get('show-review').onclick=()=>{if(hasPlan)show('review');};
    source('sequence');render();
    return {show,source,openDisclosure,current:()=>current,onChange:listener=>{changed=listener;},
      readiness:({analysis,plan})=>{hasAnalysis=!!analysis;hasPlan=!!plan;render();}};
  }
  if(typeof module!=='undefined'&&module.exports)module.exports={install};
  else root.ContentriumView={install};
})(typeof globalThis!=='undefined'?globalThis:this);
