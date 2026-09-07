export function createAmbient() {
  const conversation=document.querySelector('.conversation');
  const reduced=matchMedia('(prefers-reduced-motion: reduce)');
  let scene=null,host=null,ticket=0,load=null,failed=false;
  function sync() {
    const next=conversation.querySelector('.mobius-stage');
    if(next!==host) {
      ++ticket;scene?.dispose();scene=null;host=next;failed=false;
      if(host) {
        const current=host,revision=ticket;
        load ||= import('./mobius-scene.js');
        load.then(module=>{
          if(revision!==ticket || !current.isConnected)return;
          try {scene=module.createMobius(current,()=>{failed=true;scene?.dispose();scene=null;current.classList.remove('mobius-ready');});sync();}
          catch {failed=true;current.classList.remove('mobius-ready');current.querySelector('canvas')?.remove();}
        }).catch(()=>{failed=true;});
      }
    }
    const visible=!document.hidden && window.__awbNativeVisible!==false;
    document.body.classList.toggle('ambient-suspended',!visible);
    document.body.classList.toggle('ambient-empty',Boolean(host));
    scene?.setActive(!failed && visible && !reduced.matches && !conversation.hidden && !document.querySelector('dialog[open]'));
  }
  new MutationObserver(sync).observe(document.querySelector('#messages'),{childList:true});
  new MutationObserver(sync).observe(conversation,{attributes:true,attributeFilter:['hidden']});
  new MutationObserver(records=>{if(records.some(r=>r.target.tagName==='DIALOG'))sync();}).observe(document.body,{subtree:true,attributes:true,attributeFilter:['open']});
  document.addEventListener('visibilitychange',sync);
  window.addEventListener('awb-visibility',sync);
  reduced.addEventListener('change',sync);
  window.addEventListener('pagehide',()=>{++ticket;scene?.dispose();scene=null;host=null;});
  window.addEventListener('pageshow',sync);
  sync();
}
