import { confirmAction, promptAction } from './dialogs.js';
const $ = selector => document.querySelector(selector);
const number = value => typeof value === 'number' ? value.toLocaleString('en-US') : '未提供';

export function desktopFeatures({api, busy, setMaintenance, attachmentIds=()=>[], refreshConversation, refreshTree, refreshKnowledge, error}) {
  let context = null, lastUsage = null, permission = 'ask', active = null, revision = 0, timer;
  let compacting = false;
  function sync() {
    for (const node of document.querySelectorAll('#sessionSelect,#newSession,#deleteSession,#permissionMode,#draftList button,#confirmDraft,#discardDraft,#migrateData')) node.disabled = busy();
    $('#compactNow').disabled = busy() || compacting;
    $('#cancelCompaction').hidden = !compacting;
  }
  async function sessions() {
    const data = await api('/api/sessions');
    active = data.active;
    $('#sessionSelect').replaceChildren(...(data.sessions || []).map(s => new Option(s.title, s.session_id)));
    $('#sessionSelect').value = active || '';
    sync();
  }
  async function estimate() {
    const ticket = ++revision;
    try {
      const value = await api('/api/context/estimate', {method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({message:$('#prompt').value,attachment_ids:attachmentIds()})});
      if (ticket !== revision || busy()) return;
      context = value; renderContext();
    } catch(e) { if (ticket === revision) { $('#contextStatus').textContent = '上下文估算不可用'; $('#contextStatus').title = e.message; } }
  }
  function renderContext() {
    if (!context) return;
    const used = context.estimated_input || 0, capacity = context.capacity;
    const percentage = capacity ? Math.ceil(100 * (used + (context.output_reserved || 0)) / capacity) : null;
    $('#contextStatus').textContent = capacity ? `上下文 ≲ ${number(used)} / ${number(capacity)}` : `上下文 ≲ ${number(used)} · 容量未配置`;
    $('#contextStatus').title = '保守字节上界估算，非精确 Token；点击查看详情';
    $('#contextMeter').hidden = !capacity;
    $('#contextMeter').value = Math.min(100,percentage || 0);
    $('#contextMeter').dataset.warning = String(percentage >= 85);
    $('#contextMeter').setAttribute('aria-valuetext', capacity ? `含输出预留约 ${percentage}%` : '容量未配置');
    if ($('#contextDialog').open) contextDetails();
  }
  function contextDetails() {
    const data = context || {}, usage = data.usage || {};
    const values = {
      '输入估算（保守字节上界）':number(data.estimated_input), '上下文容量':number(data.capacity),
      '容量来源':data.capacity_source === 'user' ? '用户配置' : data.capacity_source === 'model_profile' ? '内置模型资料（转发端可能不同）' : '未配置',
      '输出预留':number(data.output_reserved), '保护预算':number(data.window),
      '图片保护估算':data.image_count ? `${data.image_count} 张 · ${number(data.image_estimate)} 预算单位（非实际用量）` : '无图片',
      '历史压缩':data.compacted_turns ? `${data.compacted_turns} 回合 · 模型语义摘要` : '未压缩',
      '自动整理':data.compaction_pending ? '下次请求前检查并压缩' : '预算充足',
      '本轮输入 / 输出':lastUsage ? `${number(lastUsage.prompt_tokens)} / ${number(lastUsage.completion_tokens)}` : '未提供',
      '本轮缓存命中':number(lastUsage?.cached_tokens),
      '本轮用量状态':lastUsage?.complete ? '接口实际返回' : '未提供或不完整',
      '会话累计输入 / 输出':`${number(usage.prompt_tokens)} / ${number(usage.completion_tokens)}`,
      '累计用量状态':usage.complete ? '完整' : '未提供或不完整',
      '摘要累计输入 / 输出':`${number(data.compaction_usage?.prompt_tokens)} / ${number(data.compaction_usage?.completion_tokens)}`,
      '摘要用量状态':data.compaction_usage?.complete ? '接口实际返回 · 与主对话分开统计' : '未提供或不完整 · 与主对话分开统计',
    };
    $('#contextDetails').replaceChildren();
    for (const [key,value] of Object.entries(values)) { const dt=document.createElement('dt'),dd=document.createElement('dd'); dt.textContent=key; dd.textContent=value; $('#contextDetails').append(dt,dd); }
  }
  async function refresh() {
    await Promise.all([sessions(), drafts(), estimate()]);
    const p = await api('/api/workspace/permissions'); permission=p.mode || 'ask'; $('#permissionMode').value=permission;
  }
  async function changeSession(url, method='POST') {
    await api(url,{method});
    lastUsage = null;
    await Promise.all([refreshConversation(),refreshTree()]);
    await refresh();
  }
  $('#newSession').onclick=() => changeSession('/api/sessions').catch(error);
  $('#sessionSelect').onchange=() => changeSession(`/api/sessions/${$('#sessionSelect').value}/select`).catch(e => {$('#sessionSelect').value=active;error(e);});
  $('#deleteSession').onclick=async () => { if(active && await confirmAction('删除当前会话的全部本地记录？此操作不可撤销。')) changeSession(`/api/sessions/${active}`,'DELETE').catch(error); };
  $('#permissionMode').onchange=async () => {
    const value=$('#permissionMode').value;
    if(value === 'auto_edit' && !await confirmAction('允许模型自动修改当前工作区内的普通文件？将保留变更记录，敏感文件仍禁止自动修改。切换工作区后恢复审批。')) {$('#permissionMode').value=permission;return;}
    try { const result=await api('/api/workspace/permissions',{method:'PUT',headers:{'content-type':'application/json'},body:JSON.stringify({mode:value})}); permission=result.mode; }
    catch(e) {$('#permissionMode').value=permission;error(e);}
  };
  $('#workspaceAudit').onclick=async () => {
    try {const data=await api('/api/workspace/audit');$('#auditContent').textContent=(data.entries || []).map(e=>`${e.created_at} · ${e.status} · ${e.path}\n${e.diff || ''}`).join('\n\n') || '暂无自动修改记录';$('#auditDialog').showModal();} catch(e){error(e);}
  };
  let draftId = null;
  async function drafts() {
    const data=await api('/api/kb/drafts'); $('#draftList').replaceChildren();
    for(const draft of data.drafts || []) {
      const row=document.createElement('button');row.type='button';row.className='draft-row';row.textContent=draft.title;row.title=draft.reason;
      row.onclick=() => {draftId=draft.draft_id;$('#draftTitle').value=draft.title;$('#draftContent').value=draft.content;$('#draftReason').textContent=draft.reason;$('#draftSources').value=(draft.sources||[]).join(', ');$('#draftTags').value=(draft.tags||[]).join(', ');$('#draftOriginal').textContent=draft.original?.content || (draft.duplicates?.length ? '同名卡片已存在，请核对后保存。' : '');$('#draftStatus').textContent='';$('#draftDialog').showModal();};
      $('#draftList').append(row);
    }
    $('#draftCount').textContent=String((data.drafts||[]).length);sync();
  }
  $('#draftForm').onsubmit=async event => {
    event.preventDefault(); if(busy()) return;
    const button=$('#confirmDraft');button.disabled=true;
    try {
      const csv=selector=>$(selector).value.split(',').map(v=>v.trim()).filter(Boolean);
      await api(`/api/kb/drafts/${draftId}`,{method:'PUT',headers:{'content-type':'application/json'},body:JSON.stringify({title:$('#draftTitle').value,content:$('#draftContent').value,tags:csv('#draftTags'),sources:csv('#draftSources')})});
      const result=await api(`/api/kb/drafts/${draftId}/confirm`,{method:'POST'});
      $('#draftDialog').close(); await drafts();await refreshKnowledge();
      if(result.warning) error(new Error(result.warning));
    }catch(e){$('#draftStatus').textContent=e.message;}finally{button.disabled=false;}
  };
  $('#discardDraft').onclick=async () => {try{await api(`/api/kb/drafts/${draftId}`,{method:'DELETE'});$('#draftDialog').close();await drafts();}catch(e){$('#draftStatus').textContent=e.message;}};
  $('#contextStatus').onclick=() => {contextDetails();$('#contextDialog').showModal();};
  $('#compactNow').onclick=async () => {
    if(busy() || compacting) return;
    compacting=true;setMaintenance(true);sync();$('#compactionStatus').textContent='正在压缩上下文…';
    try {
      const result=await api('/api/context/compact',{method:'POST'});
      $('#compactionStatus').textContent=result.status==='completed' ? '上下文压缩已完成' : result.status==='cancelled' ? '压缩已取消，历史保留' : '暂无需要压缩的历史';
    }catch(e){$('#compactionStatus').textContent=e.message;}
    finally{compacting=false;setMaintenance(false);sync();await estimate();}
  };
  $('#cancelCompaction').onclick=async () => {
    try {await api('/api/context/compact/cancel',{method:'POST'});$('#compactionStatus').textContent='正在取消压缩…';}catch(e){error(e);}
  };
  $('#viewSummary').onclick=async () => {
    try {
      const result=await api('/api/context/summary'), saved=result.checkpoint;
      $('#summaryContent').textContent=saved?.summary || '尚未生成摘要';
      $('#summaryMeta').textContent=saved ? `截至记录 #${saved.through_id} · ${new Date(saved.created_at).toLocaleString()}` : '';
      $('#summaryDialog').showModal();
    }catch(e){error(e);}
  };
  $('#closeSummary').onclick=()=>$('#summaryDialog').close();
  for(const [button,dialog] of [['#closeContext','#contextDialog'],['#closeDraft','#draftDialog'],['#closeAudit','#auditDialog']]) $(button).onclick=()=>$(dialog).close();
  $('#prompt').addEventListener('input',()=>{clearTimeout(timer);timer=setTimeout(estimate,300);});
  function event(type,data) {
    if(type==='compaction') {
      const labels={started:'正在整理上下文…',completed:'上下文压缩已完成',pruned:'已限缩工具输出',skipped:'暂无可压缩历史',cancelled:'压缩已取消',failed:'压缩失败，历史保留'};
      $('#compactionStatus').textContent=data.warning || labels[data.status] || '';
    }
    if(type==='context'){++revision;context={...context,...data};renderContext();}
    if(type==='done'){lastUsage=data.usage || null; setTimeout(()=>refresh().catch(error),0);}
    if(type==='tool_result' && data.tool_name==='draft_card') drafts().catch(error);
  }
  return {refresh,sync,event,estimate};
}
