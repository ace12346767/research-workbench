const $=selector=>document.querySelector(selector);
const accept='.docx,.xlsx,.xls,.pdf,.txt,.md,.csv,.tsv,.json,.jsonl,.yaml,.yml,.toml,.ini,.log,.py,.js,.ts,.tsx,.jsx,.html,.css,.sql,.sh,.ps1,.c,.cpp,.h,.java,.rs,.go,.xml,.tex,.png,.jpg,.jpeg,.webp,.gif,.bmp';
function icon(name) {
  const el=window.lucide.createElement(window.lucide.icons[name]);
  el.setAttribute('aria-hidden','true'); return el;
}
const size=bytes=>bytes>=1048576 ? `${(bytes/1048576).toFixed(1)} MB` : `${Math.max(1,Math.round(bytes/1024))} KB`;

export function createAttachments({api,busy,setUploading,onChange}) {
  let items=[],uploading=false,session=null,revision=0;
  const drafts=new Map();
  $('#attachmentInput').accept=accept;
  function status(text){$('#composerStatus').textContent=text;}
  function ids(){return items.filter(i=>i.status==='ready').map(i=>i.id);}
  function ready(){return !uploading && items.every(i=>i.status==='ready');}
  function sync(){
    const disabled=busy() || uploading;
    $('#attachButton').disabled=disabled;
    $('#attachmentInput').disabled=disabled;
    for(const node of document.querySelectorAll('#attachmentTray button')) node.disabled=disabled;
  }
  function chip(item, removable=false) {
    const row=document.createElement('div');row.className=`attachment-chip ${item.kind || 'document'}`;
    const open=document.createElement('button');open.type='button';open.className='attachment-open';open.title=`预览 ${item.name}`;
    if(item.kind==='image' && item.id){const img=document.createElement('img');img.src=`/api/attachments/${encodeURIComponent(item.id)}/preview`;img.alt='';open.append(img);}
    else open.append(icon(/\.xlsx?$/.test(item.name) ? 'Sheet' : 'FileText'));
    const copy=document.createElement('span');copy.className='attachment-copy';
    const name=document.createElement('strong');name.textContent=item.name;
    const meta=document.createElement('small');meta.textContent=item.status==='uploading'?'正在读取':item.status==='failed'?'读取失败，点击重试':size(item.size);
    copy.append(name,meta);open.append(copy);
    open.onclick=()=> item.status==='failed' ? retry(item) : preview(item).catch(e=>status(e.message));
    open.disabled=item.status==='uploading';row.append(open);
    if(removable){const remove=document.createElement('button');remove.type='button';remove.className='attachment-remove';remove.title=`移除 ${item.name}`;remove.setAttribute('aria-label',remove.title);remove.append(icon('X'));remove.onclick=()=>removeItem(item);row.append(remove);}
    return row;
  }
  function render(){
    $('#attachmentTray').replaceChildren(...items.map(i=>chip(i,true)));
    $('#attachmentTray').hidden=!items.length;
    onChange();sync();
  }
  async function removeItem(item){
    if(busy()||uploading)return;
    try {
      // A failed send may already have a durable transcript reference; removing it from
      // the draft must not delete the original attachment used by that transcript.
      if(item.id) await api(`/api/attachments/${item.id}`,{method:'DELETE'}).catch(e=>{if(!e.message.includes('已发送'))throw e;});
      items=items.filter(i=>i!==item);render();status('');
    }catch(e){status(e.message);}
  }
  async function uploadOne(item){
    item.status='uploading';render();
    try{
      const saved=await api(`/api/attachments?filename=${encodeURIComponent(item.name)}`,{method:'POST',headers:{'content-type':'application/octet-stream'},body:item.file});
      Object.assign(item,saved,{status:'ready'});delete item.file;status('');
    }catch(e){item.status='failed';status(`${item.name}：${e.message}`);}
    render();
  }
  async function retry(item){
    if(busy()||uploading)return;
    uploading=true;setUploading(true);
    try{await uploadOne(item);}finally{uploading=false;setUploading(false);render();}
  }
  async function add(files){
    if(busy()||uploading){status('当前任务结束后可添加附件');return;}
    files=[...files];
    if(items.length+files.length>6){status('每条消息最多添加 6 个附件');return;}
    if(files.some(f=>f.size>10*1048576)||files.reduce((n,f)=>n+f.size,0)+items.reduce((n,f)=>n+f.size,0)>20*1048576){status('每文件最多 10 MB，每条消息合计最多 20 MB');return;}
    uploading=true;setUploading(true);
    try {for(const file of files){const item={name:file.name,size:file.size,file,status:'uploading'};items.push(item);await uploadOne(item);}}
    finally{uploading=false;setUploading(false);render();$('#attachmentInput').value='';}
  }
  async function preview(item){
    if(!item.id)return;
    $('#attachmentPreviewTitle').textContent=item.name;
    $('#attachmentPreviewBody').replaceChildren();$('#attachmentPreviewStatus').textContent='正在加载';
    $('#attachmentPreviewDialog').showModal();
    if(item.kind==='image'){
      const image=document.createElement('img');image.alt=item.name;image.src=`/api/attachments/${item.id}/preview`;image.onload=()=>{$('#attachmentPreviewStatus').textContent='';};image.onerror=()=>{$('#attachmentPreviewStatus').textContent='图片加载失败';};
      $('#attachmentPreviewBody').append(image);
    }else{
      const data=await api(`/api/attachments/${item.id}`);
      const text=document.createElement('pre');text.textContent=data.text;$('#attachmentPreviewBody').append(text);
      $('#attachmentPreviewStatus').textContent=item.kind==='text'?'':'提取正文 · 不含原始排版；公式未重新计算';
    }
  }
  function renderSent(target,attachments){if(!attachments?.length)return;const tray=document.createElement('div');tray.className='sent-attachments';tray.append(...attachments.map(i=>chip({...i,status:'ready'})));target.append(tray);}
  async function loadSession(next){
    if(session===next)return;
    const ticket=++revision;
    if(session) drafts.set(session,{items,text:$('#prompt').value});
    session=next;
    const saved=drafts.get(next);
    items=saved?.items || [];$('#prompt').value=saved?.text || '';render();
    if(!saved){const data=await api('/api/attachments');if(ticket!==revision)return;items=(data.attachments||[]).map(i=>({...i,status:'ready'}));render();}
  }
  function sent(submitted){const keys=new Set(submitted);items=items.filter(i=>!keys.has(i.id));render();}
  $('#attachButton').onclick=()=>$('#attachmentInput').click();
  $('#attachmentInput').onchange=()=>add($('#attachmentInput').files);
  $('#closeAttachmentPreview').onclick=()=>$('#attachmentPreviewDialog').close();
  $('#prompt').addEventListener('paste',event=>{
    const files=[...(event.clipboardData?.items||[])].filter(i=>i.kind==='file').map(i=>i.getAsFile()).filter(Boolean);
    if(files.length){event.preventDefault();void add(files);}
  });
  const composer=$('#composer');let drag=0;
  for(const name of ['dragenter','dragover','dragleave','drop']) document.addEventListener(name,event=>{
    if(![...(event.dataTransfer?.types||[])].includes('Files'))return;
    event.preventDefault();
    if(name==='dragenter'){drag++;composer.classList.add('dragging');}
    if(name==='dragleave' && --drag<=0){drag=0;composer.classList.remove('dragging');}
    if(name==='drop'){drag=0;composer.classList.remove('dragging');void add(event.dataTransfer.files);}
  });
  return {ids,ready,sync,loadSession,sent,renderSent,items:()=>items.filter(i=>i.status==='ready'),status,reset:()=>{items=[];render();}};
}
