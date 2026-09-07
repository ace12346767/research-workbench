import { confirmAction, promptAction } from './dialogs.js';
const $=s=>document.querySelector(s);
const labels={unclassified:'未分类',manual:'人工归类',queued:'排队中',classifying:'分类中',ready:'已分类',failed:'分类失败',cancelled:'已取消'};

export function createPaperLibrary({api,onAsk,onImport,onClose}){
  let papers=[],selected=null,tree=null,mode='tree',visible=false,signature='',page=1,detailTicket=0,loadPromise=null;
  const status=text=>$('#libraryStatus').textContent=text;
  const request=(method,body)=>({method,headers:{'content-type':'application/json'},body:JSON.stringify(body)});
  function filtered(){const query=$('#paperSearch').value.trim().toLocaleLowerCase(),topic=$('#paperTopicFilter').value,year=$('#paperYearFilter').value;
    return papers.filter(p=>{const m=p.metadata||{};return (!topic||(m.topic||'未分类')===topic)&&(!year||String(m.year||'unknown')===year)&&(!query||JSON.stringify([p.filename,m.title,m.venue,m.authors,m.tags,m.topic]).toLocaleLowerCase().includes(query));});}
  function fillOptions(selector,values,title){const el=$(selector),value=el.value;el.replaceChildren(new Option(title,''),...values.map(([label,value])=>new Option(label,value)));el.value=values.some(v=>v[1]===value)?value:'';}
  function list(){const body=$('#paperTableBody');body.replaceChildren();for(const p of filtered()){
    const row=document.createElement('tr'),m=p.metadata||{};
    for(const value of [m.year||'待确认',m.title||p.filename,m.venue||'待确认',m.topic||'未分类',labels[p.classification_status]||'未分类']){const cell=document.createElement('td');cell.textContent=value;row.append(cell);}
    const button=document.createElement('button');button.type='button';button.textContent=m.title||p.filename;button.onclick=()=>select(p.paper_id);row.children[1].replaceChildren(button);body.append(row);
  }}
  async function render(){
    if(!visible)return;
    const data=filtered();$('#libraryCount').textContent=`${data.length} / ${papers.length} 篇`;
    $('#renamePaperTopic').hidden=!$('#paperTopicFilter').value;
    $('#libraryEmpty').hidden=data.length>0;$('#treeViewport').hidden=mode!=='tree'||!data.length;$('#paperTable').hidden=mode!=='list'||!data.length;
    $('#treeReset').hidden=mode!=='tree';$('#treeMode').setAttribute('aria-pressed',String(mode==='tree'));$('#listMode').setAttribute('aria-pressed',String(mode==='list'));
    list();
    if(mode==='tree'&&data.length){try{
      if(!tree){loadPromise ||= import('./paper-tree.js');const module=await loadPromise;if(!visible||mode!=='tree')return;
        tree ||= module.createPaperTree($('#treeViewport'),{onSelect:select,onError:e=>{status(e.message);tree?.dispose();tree=null;signature='';mode='list';void render();}});}
      const next=JSON.stringify(data.map(p=>[p.paper_id,p.metadata,p.topic_slot]));
      if(signature!==next){tree.setPapers(data);signature=next;}else tree.resize();tree.select(selected?.paper_id);
    }catch(e){status('3D 渲染不可用，已切换列表');mode='list';void render();}}
  }
  function update(next){papers=next;
    fillOptions('#paperTopicFilter',[...new Set(papers.map(p=>p.metadata?.topic||'未分类'))].sort().map(v=>[v,v]),'全部主题');
    fillOptions('#paperYearFilter',[...new Set(papers.map(p=>String(p.metadata?.year||'unknown')))].sort().reverse().map(v=>[v==='unknown'?'年份待确认':v,v]),'全部年份');
    if(selected){const fresh=papers.find(p=>p.paper_id===selected.paper_id);if(!fresh){closeDetail();}else if(!$('#paperEditFields').open){selected=fresh;showDetail();}}
    void render();
  }
  async function refresh(){update((await api('/api/kb/papers')).papers||[]);}
  function closeDetail(){selected=null;$('#paperDetail').hidden=true;$('#libraryBody').classList.remove('has-detail');tree?.select(null);tree?.resize();}
  function showDetail(){const m=selected.metadata||{};
    $('#paperDetailTitle').textContent=m.title||selected.filename;
    $('#paperDetailMeta').textContent=`${m.year||'年份待确认'} · ${{published:'正式发表',preprint:'预印本',unknown:'年份类型待确认'}[m.year_type||'unknown']} · ${m.venue||'出处待确认'} · ${selected.page_count||0} 页`;
    $('#paperAuthors').textContent=(m.authors||[]).join(' / ');
    $('#paperTopic').textContent=[m.topic||'未分类',m.subtopic].filter(Boolean).join(' / ');
    $('#paperReason').textContent=m.reason||'尚无分类说明';
    $('#paperClassificationStatus').textContent=[labels[selected.classification_status]||'未分类',selected.classification_error].filter(Boolean).join(' · ');
    const usage=selected.classification_usage||{};$('#paperUsage').textContent=usage.prompt_tokens!=null?`分类用量 · 输入 ${usage.prompt_tokens} / 输出 ${usage.completion_tokens??'未提供'}`:'';
    $('#paperEvidence').replaceChildren();for(const [field,evidence] of Object.entries(m.evidence||{})){
      const button=document.createElement('button');button.type='button';button.className='paper-evidence';
      button.textContent=`${({title:'标题',year:'年份',venue:'出处',authors:'作者',doi:'DOI'})[field]||field} · PDF 第 ${evidence.page} 页`;
      const quote=document.createElement('blockquote');quote.textContent=evidence.quote;button.onclick=()=>openReader(evidence.page);$('#paperEvidence').append(button,quote);
    }
    $('#paperManualNote').textContent=selected.manual_fields?.length?'含人工修正字段，自动分类不会覆盖':'';
    const running=['queued','classifying'].includes(selected.classification_status);$('#classifyPaper').disabled=running;$('#cancelPaperClassification').hidden=!running;
    $('#readPaper').disabled=selected.status!=='ready';
    for(const name of ['title','year','venue','doi','topic','subtopic','year_type'])$(`#paperEdit_${name}`).value=m[name]??(name==='year_type'?'unknown':'');
    $('#paperEdit_authors').value=(m.authors||[]).join(', ');
    $('#paperEdit_tags').value=(m.tags||[]).join(', ');
  }
  async function select(id){const ticket=++detailTicket;
    try{const paper=await api(`/api/kb/papers/${id}`);if(ticket!==detailTicket)return;selected=paper;$('#paperEditFields').open=false;
      $('#paperDetail').hidden=false;$('#libraryBody').classList.add('has-detail');showDetail();tree?.select(id);tree?.resize();
    }catch(e){status(e.message);}}
  async function openReader(number=1){if(!selected)return;const id=selected.paper_id;page=number;
    $('#paperReaderTitle').textContent=selected.metadata?.title||selected.filename;
    if(!$('#paperReader').open)$('#paperReader').showModal();
    $('#paperReaderStatus').textContent='读取中';
    $('#paperPageImage').hidden=true;
    try{const data=await api(`/api/kb/papers/${id}/pages/${page}`);if(id!==selected?.paper_id||number!==page)return;
      $('#paperPageNumber').textContent=`${page} / ${data.pages}`;$('#paperPrevious').disabled=page<=1;$('#paperNext').disabled=page>=data.pages;
      $('#paperPageImage').src=`/api/kb/papers/${id}/pages/${page}?image=true`;$('#paperPageImage').alt=`PDF 第 ${page} 页`;
      $('#paperPageText').textContent=data.text;$('#paperReaderStatus').textContent=data.text.trim()?'':'此页没有可提取正文';
    }catch(e){$('#paperReaderStatus').textContent=e.message;}}
  $('#paperPageImage').onerror=()=>$('#paperReaderStatus').textContent='页面图像读取失败';
  $('#paperPageImage').onload=()=>$('#paperPageImage').hidden=false;
  $('#closePaperReader').onclick=()=>$('#paperReader').close();$('#paperPrevious').onclick=()=>openReader(page-1);$('#paperNext').onclick=()=>openReader(page+1);
  $('#closePaperDetail').onclick=closeDetail;$('#readPaper').onclick=()=>openReader();
  $('#askPaper').onclick=()=>{if(selected)onAsk(selected);};
  $('#classifyPaper').onclick=async()=>{if(!selected||!await confirmAction('使用当前模型分类这篇论文？会发送部分 PDF 正文并产生模型用量。'))return;
    try{await api(`/api/kb/papers/${selected.paper_id}/classify?confirmed=true`,{method:'POST'});await refresh();}catch(e){status(e.message);}};
  $('#cancelPaperClassification').onclick=async()=>{try{await api(`/api/kb/papers/${selected.paper_id}/classification/cancel`,{method:'POST'});await refresh();}catch(e){status(e.message);}};
  $('#savePaperMetadata').onclick=async()=>{if(!selected)return;const metadata={...selected.metadata};
    for(const name of ['title','venue','doi','topic','subtopic','year_type'])metadata[name]=$(`#paperEdit_${name}`).value.trim();
    metadata.year=$('#paperEdit_year').value?Number($('#paperEdit_year').value):null;
    metadata.authors=$('#paperEdit_authors').value.split(/[,，]/).map(s=>s.trim()).filter(Boolean);metadata.tags=$('#paperEdit_tags').value.split(/[,，]/).map(s=>s.trim()).filter(Boolean);
    try{selected=await api(`/api/kb/papers/${selected.paper_id}/metadata`,request('PUT',{metadata,revision:selected.revision}));$('#paperEditFields').open=false;await refresh();status('已保存');}catch(e){status(e.message);}};
  $('#deleteLibraryPaper').onclick=async()=>{if(!selected||!await confirmAction('删除这篇论文及其索引？'))return;try{await api(`/api/kb/papers/${selected.paper_id}`,{method:'DELETE'});closeDetail();await refresh();}catch(e){status(e.message);}};
  $('#treeMode').onclick=()=>{mode='tree';void render();};$('#listMode').onclick=()=>{mode='list';void render();};$('#treeReset').onclick=()=>tree?.reset();
  for(const s of ['#paperSearch','#paperTopicFilter','#paperYearFilter'])$(s).addEventListener(s==='#paperSearch'?'input':'change',()=>void render());
  $('#libraryImport').onclick=onImport;$('#closePaperLibrary').onclick=onClose;
  $('#renamePaperTopic').onclick=async()=>{const source=$('#paperTopicFilter').value;if(!source)return;
    const target=await promptAction('新的主题名称（填写已有主题可合并）',source);if(!target||target===source)return;
    const count=papers.filter(p=>(p.metadata?.topic||'未分类')===source).length;
    if(!await confirmAction(`将“${source}”下的 ${count} 篇论文移动到“${target}”？人工分类将保留。`))return;
    try{await api('/api/kb/topics/rename',request('POST',{source:source==='未分类'?'':source,target}));await refresh();$('#paperTopicFilter').value=target;void render();}catch(e){status(e.message);}};
  return {update,needsRefresh:()=>papers.some(p=>['queued','classifying'].includes(p.classification_status)),open:async()=>{visible=true;$('#paperLibrary').hidden=false;await refresh();},close:()=>{visible=false;$('#paperLibrary').hidden=true;},askScope:()=>selected?.paper_id};
}
