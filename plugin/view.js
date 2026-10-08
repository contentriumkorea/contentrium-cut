/* Shared by the native panel and the QA-only preview. No host or data calls. */
(function(root){
  function install(document){
    const get=id=>document.getElementById(id),steps=['tracks','speakers','cut','review'];
    let current='tracks',previous='tracks',changed=null;
    function show(next){
      if(!steps.includes(next)&&next!=='settings')return;
      if(next==='settings'&&current!=='settings')previous=current;
      current=next;
      for(const name of [...steps,'settings'])get('view-'+name).className='page'+(name===next?'':' hidden');
      for(const button of document.querySelectorAll('[data-step]')){
        const active=button.getAttribute('data-step')===next;
        button.className='step'+(active?' active':'');button.setAttribute('aria-current',active?'step':'false');
      }
      const action={tracks:'next-step',speakers:'analyze',cut:'plan',review:'apply',settings:'close-settings'}[next];
      for(const id of ['next-step','analyze','plan','apply','close-settings'])get(id).className='primary'+(id===action?'':' hidden');
      get('back-step').disabled=next==='tracks';get('open-settings').setAttribute('aria-expanded',String(next==='settings'));
      get('workspace').scrollTop=0;
      if(changed)changed();
    }
    for(const button of document.querySelectorAll('[data-step]'))button.onclick=()=>show(button.getAttribute('data-step'));
    for(const button of document.querySelectorAll('[data-next]'))button.onclick=()=>show(button.getAttribute('data-next'));
    for(const button of document.querySelectorAll('[data-disclosure]'))button.onclick=()=>{
      const body=get(button.getAttribute('data-disclosure'));
      const open=body.className.split(/\s+/).includes('hidden');
      button.setAttribute('data-open',String(open));
      button.setAttribute('aria-expanded',String(open));
      body.className='disclosure-content'+(open?'':' hidden');
    };
    get('next-step').onclick=()=>show('speakers');
    get('back-step').onclick=()=>show(current==='settings'?previous:steps[Math.max(0,steps.indexOf(current)-1)]);
    get('open-settings').onclick=()=>show(current==='settings'?previous:'settings');
    get('close-settings').onclick=()=>show(previous);
    show('tracks');return {show,current:()=>current,onChange:listener=>{changed=listener;}};
  }
  if(typeof module!=='undefined'&&module.exports)module.exports={install};
  else root.ContentriumView={install};
})(typeof globalThis!=='undefined'?globalThis:this);
