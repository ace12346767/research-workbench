export function composerModelPicker({api, busy, pending, saved}) {
  const trigger=document.querySelector('#composerModel');
  const panel=document.createElement('section');
  panel.id='composerModelPopover';panel.hidden=true;panel.setAttribute('aria-label','选择模型');
  const search=document.createElement('input');search.id='composerModelSearch';search.placeholder='搜索模型';search.setAttribute('aria-label','搜索模型');
  const status=document.createElement('p');status.className='model-picker-status';status.role='status';
  const list=document.createElement('div');list.id='composerModelChoices';list.role='listbox';list.setAttribute('aria-label','模型');
  const retry=document.createElement('button');retry.type='button';retry.textContent='重新加载';retry.hidden=true;
  panel.append(search,status,list,retry);document.body.append(panel);
  trigger.setAttribute('aria-controls',panel.id);trigger.setAttribute('aria-haspopup','listbox');trigger.setAttribute('aria-expanded','false');
  let models=[],revision=0,saving=false;
  function position(){const box=trigger.getBoundingClientRect();panel.style.width=`${Math.min(360,innerWidth-24)}px`;panel.style.left=`${Math.max(12,Math.min(box.left,innerWidth-panel.offsetWidth-12))}px`;panel.style.bottom=`${Math.max(12,innerHeight-box.top+8)}px`;panel.style.maxHeight=`${Math.max(100,box.top-20)}px`;}
  function close(focus=false){++revision;panel.hidden=true;trigger.setAttribute('aria-expanded','false');if(focus)trigger.focus();}
  async function select(model){
    if(saving||busy())return;
    let switched=false;
    saving=true;pending(true);search.disabled=true;retry.disabled=true;list.querySelectorAll('button').forEach(b=>b.disabled=true);
    status.textContent='正在切换模型';
    try {const result=await api('/api/provider/model',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({model})});await saved(result);switched=true;close();}
    catch(error){status.textContent=`切换失败 · ${error.message}`;}
    finally{saving=false;pending(false);search.disabled=false;retry.disabled=false;list.querySelectorAll('button').forEach(b=>b.disabled=false);if(switched)trigger.focus();}
  }
  function render(){
    list.replaceChildren();
    const filtered=models.filter(m=>m.toLowerCase().includes(search.value.toLowerCase()));
    for(const model of filtered){const option=document.createElement('button');option.type='button';option.role='option';option.textContent=model==='mock-model'?'离线演示':model;option.title=model;option.setAttribute('aria-selected',String(document.querySelector('#modelId').value===model));option.onclick=()=>select(model);list.append(option);}
    status.textContent=filtered.length?`可用模型 · ${filtered.length}`:'没有匹配的模型';
  }
  async function load(){
    const ticket=++revision;status.textContent='正在加载模型';retry.hidden=true;list.replaceChildren();search.disabled=true;
    try{const result=await api('/api/provider/models');if(ticket!==revision)return;models=[...new Set(result.models.filter(m=>typeof m==='string'))].sort();render();if(!models.length){status.textContent='没有可用模型，请先在设置中配置连接';retry.hidden=false;}}
    catch(error){if(ticket===revision){status.textContent=`加载失败 · ${error.message}`;retry.hidden=false;}}
    finally{if(ticket===revision){search.disabled=false;search.focus();}}
  }
  trigger.onclick=()=>{if(!panel.hidden){close(true);return;}if(busy())return;panel.hidden=false;trigger.setAttribute('aria-expanded','true');search.value='';position();load();};
  retry.onclick=load;search.oninput=render;
  panel.addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();close(true);}if(['ArrowDown','ArrowUp'].includes(event.key)){event.preventDefault();const items=[...list.querySelectorAll('button:not(:disabled)')];const index=items.indexOf(document.activeElement);items[(index+(event.key==='ArrowDown'?1:items.length-1)+items.length)%items.length]?.focus();}});
  document.addEventListener('pointerdown',event=>{if(!panel.hidden&&!panel.contains(event.target)&&!trigger.contains(event.target))close();});
  document.addEventListener('keydown',event=>{if(event.key==='Escape'&&!panel.hidden){event.preventDefault();close(true);}});
  window.addEventListener('resize',()=>{if(!panel.hidden)position();});
  new MutationObserver(()=>{if(trigger.disabled&&!saving)close();}).observe(trigger,{attributes:true,attributeFilter:['disabled']});
  return {close};
}
