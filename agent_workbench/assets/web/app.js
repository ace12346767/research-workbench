import { confirmAction, promptAction } from './dialogs.js';
import { renderMarkdown } from "./markdown.js";
import { createAmbient } from './ambient.js';
import { createRoutingSettings } from "./routing-settings.js";
import { modelPicker } from './model-picker.js';
import { composerModelPicker } from './composer-model.js';
import { desktopFeatures } from './desktop-features.js';
import { createAttachments } from './attachments.js';
import { createPaperLibrary } from './paper-library.js';

const $ = (selector) => document.querySelector(selector);

window.lucide.createIcons({attrs: {"aria-hidden": "true", focusable: "false"}});
for (const button of document.querySelectorAll(".icon-button")) {
  if (!button.hasAttribute("aria-label")) button.setAttribute("aria-label", button.title);
}
const emptyConversation = $(".empty-state").cloneNode(true);
createAmbient();

function assetIcon(name) {
  const holder = document.createElement("span");
  holder.className = "asset-icon";
  const icon = window.lucide.createElement(window.lucide.icons[name]);
  icon.setAttribute("aria-hidden", "true");
  icon.setAttribute("focusable", "false");
  holder.append(icon);
  return holder;
}

function assetName(text) {
  const label = document.createElement("span");
  label.className = "asset-name";
  label.textContent = text;
  return label;
}

const state = {
  requestId: null,
  running: false,
  seenSequences: new Set(),
  route: null,
  cards: [],
  papers: [],
  answerNode: null,
  reasoningNode: null,
  terminalSeen: false,
  audit: null,
  auditing: false,
  maintenance: false,
  diagnosing: false,
  uploading: false,
  selectingModel: false,
  runState: 'idle',
  lastFinish: null,
  paperScope: null,
  conversationSession: null,
};

const RUN_LABELS = {
  idle: "就绪",
  connecting: "连接中",
  routing: "路由中",
  streaming: "生成中",
  tool: "工具执行中",
  approval: "等待审批",
  cancelling: "正在停止",
  failed: "失败",
};

const routingSettings = createRoutingSettings({api, syncBusy:syncBusyControls,
  busy:() => state.running || state.selectingModel || state.maintenance || state.diagnosing || state.uploading,
  onSaved:async result => { if (result.conversation_reset) await refreshConversation(); },
  onError:error => append('event error', `推理设置未保存 · ${error.message}`),
});
const desktop = desktopFeatures({api, busy:() => state.running || state.selectingModel || state.maintenance || state.diagnosing || state.uploading,
  attachmentIds:()=>attachments.ids(),
  setMaintenance:value=>{state.maintenance=value;syncBusyControls();},
  refreshConversation, refreshTree, refreshKnowledge, error:e=>append('event error',e.message)});
const attachments=createAttachments({api,busy:()=>state.running || state.selectingModel || state.maintenance || state.diagnosing,
  setUploading:value=>{state.uploading=value;syncBusyControls();},onChange:()=>{syncBusyControls();void desktop.estimate();}});
const paperLibrary=createPaperLibrary({api,onImport:()=>$('#pdfInput').click(),onClose:closePaperLibrary,
  onAsk:paper=>{state.paperScope=paper;$('#paperScopeName').textContent=paper.metadata?.title||paper.filename;$('#paperScope').hidden=false;closePaperLibrary();$('#prompt').focus();}});
function closePaperLibrary(){paperLibrary.close();$('.conversation').hidden=false;}
$('#openPaperLibrary').onclick=()=>{ $('.conversation').hidden=true;void paperLibrary.open().catch(e=>append('event error',e.message)); };
$('#clearPaperScope').onclick=()=>{state.paperScope=null;$('#paperScope').hidden=true;};

function setRunState(name) {
  document.body.dataset.runState=name;
  state.runState=name;
  state.running = !["idle", "failed"].includes(name);
  $("#runStatus").textContent = RUN_LABELS[name] || name;
  $("#runStatus").dataset.state = name;
  syncBusyControls();
}

function syncBusyControls() {
  const exclusive = state.selectingModel || state.maintenance || state.diagnosing || state.uploading || routingSettings.isSaving() || state.papers.some(p=>p.classification_status==='classifying');
  for (const selector of ["#chooseWorkspace", "#clearConversation", "#providerDiagnose", "#saveSettings", "#reloadLocalModel", '#composerModel']) {
    $(selector).disabled = state.running || exclusive;
  }
  for (const node of document.querySelectorAll('#newCard, #pdfInput, #paperList button, #cardList button, #cardForm button[type="submit"], #deleteCard')) node.disabled = exclusive;
  $("#auditKnowledge").disabled = state.auditing || exclusive;
  $("#repairKnowledge").disabled = state.running || exclusive || state.auditing || !state.audit?.repairable_count || state.papers.some(paper => paper.status === "importing");
  $("#settingsButton").disabled = exclusive;
  for (const node of document.querySelectorAll('#settingsForm input, #settingsForm select, #settingsForm textarea, #providerTest, #loadModels')) node.disabled = exclusive || state.running;
  routingSettings.sync();
  desktop.sync();
  attachments.sync();
  const button=$('#sendButton'),label=state.running?'停止生成':'发送';
  button.disabled=state.running ? state.runState==='cancelling' : exclusive || !attachments.ready();
  button.type=state.running?'button':'submit';button.title=label;button.setAttribute('aria-label',label);
  const shape=state.running?'Square':'ArrowUp';
  if(button.dataset.shape!==shape){button.replaceChildren(window.lucide.createElement(window.lucide.icons[shape]));button.querySelector('svg').setAttribute('aria-hidden','true');button.dataset.shape=shape;}
  button.dataset.running=String(state.running);
}

async function api(url, options = {}) {
  const response = await fetch(url, options);
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("json") ? await response.json() : await response.text();
  if (!response.ok) {
    const message = typeof payload === "object" ? payload.detail : payload;
    throw new Error(message || `HTTP ${response.status}`);
  }
  return payload;
}

function truncate(value, length = 4000) {
  const text = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  return text.length > length ? `${text.slice(0, length)}\n... [truncated]` : text;
}

function removeEmptyState() {
  const empty = $(".empty-state");
  if (empty) empty.remove();
}

function append(className, text = "") {
  removeEmptyState();
  const element = document.createElement("div");
  element.className = className;
  element.textContent = text;
  $("#messages").append(element);
  element.scrollIntoView({ block: "end" });
  return element;
}

function keyValueList(target, values) {
  target.replaceChildren();
  for (const [key, value] of Object.entries(values)) {
    const term = document.createElement("dt");
    term.textContent = key;
    const detail = document.createElement("dd");
    detail.textContent = value ?? "--";
    target.append(term, detail);
  }
}

function showEmptyConversation() {
  $("#messages").replaceChildren();
  $("#messages").append(emptyConversation.cloneNode(true));
  state.answerNode = null;
  state.reasoningNode = null;
}

async function refreshConversation() {
  const snapshot = await api("/api/conversation");
  if(state.conversationSession!==snapshot.session_id){state.paperScope=null;$('#paperScope').hidden=true;state.conversationSession=snapshot.session_id;}
  showEmptyConversation();
  for (const turn of snapshot.turns) {
    const user=append("message user", turn.user);
    attachments.renderSent(user,turn.attachments);
    const answer = append("message assistant");
    renderMarkdown(answer, turn.assistant);
  }
  if (snapshot.turns.some(turn=>turn.status && !['stop','length'].includes(turn.status))) $("#contextStatus").title = '包含未完成回合';
  await attachments.loadSession(snapshot.session_id || 'default');
}

async function refreshTree() {
  const data = await api("/api/workspace/tree?directory=.");
  const status = data.workspace || "尚未选择工作区";
  $("#workspaceStatus").textContent = status;
  $("#workspaceStatus").title = status;
  const list = $("#fileTree");
  list.replaceChildren();
  list.classList.toggle("empty", !data.entries.length);
  if (!data.entries.length) list.textContent = data.workspace ? "工作区为空" : "选择目录后显示文件";
  renderTreeEntries(list, data.entries);
}

function renderTreeEntries(list, entries) {
  for (const entry of entries) {
    const path = entry.path;
    if (entry.type === "directory") {
      const folder = document.createElement("details");
      folder.className = "tree-folder";
      const summary = document.createElement("summary");
      summary.append(assetIcon("Folder"), assetName(entry.name));
      summary.title = path;
      const children = document.createElement("div");
      children.className = "tree-children";
      folder.append(summary, children);
      let loading = false;
      let loaded = false;
      folder.ontoggle = async () => {
        if (!folder.open || loaded || loading) return;
        loading = true;
        children.textContent = "加载中";
        try {
          const data = await api(`/api/workspace/tree?directory=${encodeURIComponent(path)}`);
          children.replaceChildren();
          renderTreeEntries(children, data.entries);
          if (!data.entries.length) children.textContent = "空目录";
          loaded = true;
        } catch (error) {
          children.textContent = error.message;
        } finally {
          loading = false;
        }
      };
      list.append(folder);
      continue;
    }
    const button = document.createElement("button");
    button.type = "button";
    button.append(assetIcon("File"), assetName(entry.name));
    button.title = path;
    button.onclick = async () => {
      try {
        const file = await api(`/api/workspace/file?path=${encodeURIComponent(path)}&start_line=1&max_lines=1000`);
        $("#fileTitle").textContent = file.path;
        $("#fileMeta").textContent = `${file.size_bytes ?? "--"} bytes · ${file.line_count ?? "--"} lines${file.truncated ? " · truncated" : ""}`;
        $("#fileContent").textContent = file.content;
        $("#fileDialog").showModal();
      } catch (error) {
        append("event error", `文件读取失败 · ${error.message}`);
      }
    };
    list.append(button);
  }
}

function actionButton(title, iconName, handler, danger = false) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `icon-button small${danger ? " danger" : ""}`;
  button.title = title;
  button.setAttribute("aria-label", title);
  button.append(assetIcon(iconName));
  button.onclick = handler;
  return button;
}

function renderPapers() {
  const list = $("#paperList");
  list.replaceChildren();
  list.classList.toggle("empty", !state.papers.length);
  if (!state.papers.length) list.textContent = "尚未导入";
  for (const paper of state.papers) {
    const row = document.createElement("div");
    row.className = "asset-row";
    const label = document.createElement("span");
    label.className = "asset-label";
    const copy = document.createElement("span");
    copy.className = "asset-copy";
    const metadata = document.createElement("span");
    metadata.className = "asset-meta";
    metadata.textContent = `${({ready: "已索引", importing: "索引中", failed: "失败"})[paper.status] || paper.status}${paper.page_count ? ` · ${paper.page_count} 页` : ""}`;
    copy.append(assetName(paper.metadata?.title||paper.filename), metadata);
    label.append(assetIcon("FileText"), copy);
    label.title = paper.error || paper.filename;
    label.tabIndex=0;label.setAttribute('role','button');label.setAttribute('aria-label',`打开论文 ${paper.filename}`);
    label.onclick=()=>$('#openPaperLibrary').click();label.onkeydown=e=>{if(e.key==='Enter')label.click();};
    const actions = document.createElement("span");
    actions.className = "row-actions";
    actions.append(
      actionButton("重新索引", "RefreshCw", async () => {
        try {
          await api(`/api/kb/papers/${paper.paper_id}/reindex`, { method: "POST" });
          knowledgeChanged();
          await refreshKnowledge();
        } catch (error) {
          append("event error", `重新索引失败 · ${error.message}`);
        }
      }),
      actionButton("删除论文", "Trash2", async () => {
        if (!await confirmAction(`删除论文 ${paper.filename} 及其索引？`)) return;
        try {
          await api(`/api/kb/papers/${paper.paper_id}`, { method: "DELETE" });
          knowledgeChanged();
          await refreshKnowledge();
        } catch (error) {
          $("#knowledgeActionStatus").textContent = error.message;
        }
      }, true),
    );
    row.append(label, actions);
    list.append(row);
  }
}

function openCard(card = null) {
  $("#cardId").value = card?.card_id || "";
  $("#cardTitle").value = card?.title || "";
  $("#cardTags").value = (card?.tags || []).join(", ");
  $("#cardSources").value = (card?.sources || []).join(", ");
  $("#cardContent").value = card?.content || "";
  $("#cardDialogTitle").textContent = card ? "编辑卡片" : "新建卡片";
  $("#deleteCard").hidden = !card;
  $("#cardStatus").textContent = "";
  $("#cardDialog").showModal();
}

function renderCards() {
  const list = $("#cardList");
  list.replaceChildren();
  list.classList.toggle("empty", !state.cards.length);
  if (!state.cards.length) list.textContent = "尚未保存";
  for (const card of state.cards) {
    const button = document.createElement("button");
    button.type = "button";
    button.append(assetIcon("NotebookPen"), assetName(card.title));
    button.title = `${card.title}\n${(card.tags || []).join(", ")}`;
    button.onclick = () => openCard(card);
    list.append(button);
  }
}

function updatePaperImportNotice(node, paper) {
  const label = {importing:'PDF 已保存，正在建立索引', ready:'PDF 已索引', failed:'PDF 索引失败'}[paper.status] || 'PDF 状态待确认';
  const details = paper.status === 'ready' ? ` · ${paper.page_count} 页 · ${paper.chunk_count} 个检索片段` : '';
  node.textContent = `${node.dataset.duplicate === 'true' ? '已存在，未重复导入 · ' : ''}${label} · ${paper.filename}${details}${paper.error ? ` · ${paper.error}` : ''}`;
  node.className = paper.status === 'failed' ? 'event error' : 'event';
  node.setAttribute('role','status');
}

let knowledgeRefreshRevision = 0, knowledgeRefreshApplied = 0;
async function refreshKnowledge() {
  const revision = ++knowledgeRefreshRevision;
  const [papers, cards] = await Promise.all([api("/api/kb/papers"), api("/api/kb/cards")]);
  if (revision < knowledgeRefreshApplied) return;
  knowledgeRefreshApplied = revision;
  state.papers = papers.papers;
  state.cards = cards.cards;
  for (const node of document.querySelectorAll('#messages [data-import-paper]')) {
    const paper = state.papers.find(item => item.paper_id === node.dataset.importPaper);
    if (paper) updatePaperImportNotice(node, paper);
    else node.textContent = '论文已移除';
  }
  renderPapers();
  paperLibrary.update(state.papers);
  renderCards();
  syncBusyControls();
}

const ISSUE_LABELS = {
  invalid_manifest: "论文清单损坏", invalid_card: "卡片格式无效",
  card_index_missing: "卡片缺少索引", card_index_stale: "卡片索引过期",
  unsafe_paper_path: "论文路径不安全", paper_too_large: "论文超过大小限制",
  paper_missing: "论文源文件缺失", paper_unreadable: "论文无法读取",
  paper_hash_mismatch: "论文内容已被替换", paper_index_missing: "论文缺少索引",
  paper_state_mismatch: "论文清单状态不一致", paper_last_error: "上次论文索引失败",
  untracked_pdf: "未登记的 PDF", unmanaged_index: "非托管索引", orphan_index: "孤立索引",
};

function renderAudit(result) {
  state.audit = result;
  $("#knowledgeAudit").dataset.status = result.status;
  $("#knowledgeStatus").textContent = result.status === "healthy"
    ? `文件与索引一致 · ${result.indexed_sources} 个索引来源`
    : `发现 ${result.issues.length} 项问题 · 可修复 ${result.repairable_count}`;
  $("#knowledgeIssues").replaceChildren();
  for (const issue of result.issues) {
    const row = document.createElement("li");
    const filename = issue.path.split(/[\\/]/).pop();
    row.textContent = `${filename} · ${ISSUE_LABELS[issue.kind] || issue.kind} · ${issue.repairable ? "可修复" : "需人工处理"}`;
    row.title = issue.path;
    $("#knowledgeIssues").append(row);
  }
  syncBusyControls();
}

async function auditKnowledge() {
  if (state.auditing || state.maintenance) return;
  state.auditing = true;
  syncBusyControls();
  $("#knowledgeStatus").textContent = "正在检查";
  try {
    renderAudit(await api("/api/kb/audit"));
  } catch (error) {
    state.audit = null;
    $("#knowledgeIssues").replaceChildren();
    $("#knowledgeStatus").textContent = `检查失败 · ${error.message}`;
  } finally {
    state.auditing = false;
    syncBusyControls();
  }
}

function knowledgeChanged(warning = "") {
  state.audit = null;
  $("#knowledgeAudit").dataset.status = "unchecked";
  $("#knowledgeStatus").textContent = "知识库已变更，待重新检查";
  $("#knowledgeIssues").replaceChildren();
  $("#knowledgeActionStatus").textContent = warning;
  syncBusyControls();
}

$("#auditKnowledge").onclick = auditKnowledge;
$("#repairKnowledge").onclick = async () => {
  if (state.maintenance || state.diagnosing || state.running) return;
  if (!await confirmAction("重新生成缺失或失败的索引、校正清单状态，并清理源文件已不存在的孤立索引？不会删除或覆盖原始卡片和 PDF。")) return;
  state.maintenance = true;
  syncBusyControls();
  $("#knowledgeActionStatus").textContent = "正在修复索引";
  try {
    const result = await api("/api/kb/repair", {method: "POST"});
    renderAudit(result.after);
    $("#knowledgeActionStatus").textContent = `已修复 ${result.repaired.length} 项 · 失败 ${result.errors.length} 项${result.errors.length ? " · " + result.errors.map(item => item.error).join("；") : ""}`;
    await refreshKnowledge();
  } catch (error) {
    state.audit = null;
    $("#knowledgeStatus").textContent = "修复未完成，待重新检查";
    $("#knowledgeActionStatus").textContent = error.message;
  } finally {
    state.maintenance = false;
    syncBusyControls();
  }
};

function parseSseBlock(block) {
  let type = "message";
  const data = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) type = line.slice(6).trim();
    if (line.startsWith("data:")) data.push(line.slice(5).trim());
  }
  return { type, payload: JSON.parse(data.join("\n")) };
}

async function decideApproval(id, action, panel) {
  for (const button of panel.querySelectorAll("button")) button.disabled = true;
  try {
    const result = await api(`/api/approvals/${id}/${action}`, { method: "POST" });
    const status = document.createElement("div");
    status.className = "approval-status";
    status.textContent = result.status || action;
    if (result.warning) status.textContent += ` · ${result.warning}`;
    knowledgeChanged(result.warning || "");
    panel.append(status);
    await Promise.all([refreshTree(), refreshKnowledge()]);
  } catch (error) {
    append("event error", `审批失败 · ${error.message}`);
    if (state.running) {
      for (const button of panel.querySelectorAll("button")) button.disabled = false;
    }
  }
}

function toolPanel(data, kind) {
  const id = `tool-${data.call_id}`;
  let panel = document.getElementById(id);
  if (!panel) {
    panel = document.createElement("details");
    panel.id = id;
    panel.className = "event tool-event";
    const summary = document.createElement("summary");
    summary.textContent = `${data.tool_name || "tool"} · ${kind}`;
    const content = document.createElement("pre");
    content.textContent = truncate(data.arguments || data.output || "");
    panel.append(summary, content);
    $("#messages").append(panel);
  } else {
    panel.querySelector("summary").textContent = `${data.tool_name || panel.querySelector("summary").textContent.split(" · ")[0]} · ${data.status || kind} · ${data.duration_ms ?? 0} ms`;
    panel.querySelector("pre").textContent = truncate(data.output);
  }
  panel.scrollIntoView({ block: "end" });
}

function handleEvent(type, payload) {
  if (payload.request_id !== state.requestId) return;
  if (payload.sequence && state.seenSequences.has(payload.sequence)) return;
  if (payload.sequence) state.seenSequences.add(payload.sequence);
  const data = payload.data || {};
  desktop.event(type,data);
  if (type === "route") {
    state.route = data;
    $("#routeDetails").textContent = `引导: ${{fast:'简洁',standard:'标准',deep:'深入'}[data.effort] || '未注入'}`;
    $("#routeDetails").title = `${data.source} · ${data.reason}`;
    setRunState("streaming");
  } else if (type === 'compaction') {
    if(data.status==='completed') append('event',`上下文压缩完成 · ${data.compacted_turns || 0} 回合`);
    if(data.warning) append('event error',data.warning);
  } else if (type === "context") {
    if(data.dropped_turns || data.truncated_tools) append("event", `上下文整理 · ${data.dropped_turns} 回合 · 工具输出限缩 ${data.truncated_tools} 项`);
  } else if (type === "reasoning") {
    if (!state.reasoningNode) {
      const details = document.createElement("details");
      details.className = "event reasoning-event";
      const summary = document.createElement("summary");
      summary.textContent = "推理过程 · streaming";
      const content = document.createElement("pre");
      details.append(summary, content);
      $("#messages").append(details);
      state.reasoningNode = details;
    }
    state.reasoningNode.querySelector("pre").textContent += data.delta || "";
  } else if (type === "answer") {
    if (!state.answerNode) {
      state.answerNode = append("message assistant");
      state.answerNode.dataset.raw = "";
    }
    state.answerNode.dataset.raw += data.delta || "";
    renderMarkdown(state.answerNode, state.answerNode.dataset.raw);
  } else if (type === "tool_call") {
    setRunState("tool");
    toolPanel(data, "running");
  } else if (type === "tool_result") {
    setRunState("streaming");
    toolPanel(data, data.status || "done");
  } else if (type === "approval") {
    setRunState("approval");
    const panel = append("event approval");
    panel.dataset.approvalId = data.approval_id;
    const heading = document.createElement("strong");
    heading.textContent = `${data.action} · ${data.target || data.title || ""}`;
    const preview = document.createElement("pre");
    preview.textContent = data.diff || data.content || "";
    const actions = document.createElement("div");
    actions.className = "approval-actions";
    const reject = document.createElement("button");
    reject.type = "button";
    reject.textContent = "拒绝";
    reject.onclick = () => decideApproval(data.approval_id, "reject", panel);
    const approve = document.createElement("button");
    approve.type = "button";
    approve.className = "primary";
    approve.textContent = "批准";
    approve.onclick = () => decideApproval(data.approval_id, "approve", panel);
    actions.append(reject, approve);
    panel.append(heading, preview, actions);
  } else if (type === "error") {
    append("event error", `错误 · ${data.message || "请求失败"}`);
  } else if (type === "done") {
    state.lastFinish=data.finish_reason;
    state.terminalSeen = true;
    for (const button of document.querySelectorAll(".approval-actions button")) button.disabled = true;
    if (state.reasoningNode) state.reasoningNode.querySelector("summary").textContent = "推理过程";
    const usage = data.usage?.prompt_tokens != null ? ` · 输入 ${data.usage.prompt_tokens} · 输出 ${data.usage.completion_tokens ?? '未提供'}${data.usage.complete ? '' : ' · 用量不完整'}` : ' · 用量未提供';
    if (data.history_retained === false) append("event", "本回合超出历史容量，未加入后续上下文");
    if (data.context?.estimated_input != null) {
      $("#contextStatus").title = `保守估算输入 ${data.context.estimated_input}/${data.context.input_limit} · 输出预留 ${data.context.output_reserved}`;
    }
    append("event done-event", `完成 · ${data.finish_reason} · ${data.duration_ms ?? 0} ms${usage}`);
    setRunState(data.finish_reason === "error" ? "failed" : "idle");
  }
}

async function sendMessage(message,files=[],onAccepted=()=>{}) {
  state.requestId = `req-${crypto.randomUUID()}`;
  state.seenSequences.clear();
  state.answerNode = null;
  state.reasoningNode = null;
  state.terminalSeen = false;
  state.lastFinish=null;
  const user=append("message user", message);
  attachments.renderSent(user,files);
  setRunState("connecting");
  let response;
  try { response = await fetch("/api/chat/stream", {
    method: "POST",
    headers: { "content-type": "application/json", accept: "text/event-stream" },
    body: JSON.stringify({ message, request_id: state.requestId, attachment_ids:files.map(f=>f.id), paper_ids:state.paperScope?[state.paperScope.paper_id]:[] }),
  }); } catch(error) { user.remove(); throw error; }
  if (!response.ok || !response.body){let reason=`HTTP ${response.status}`;try{reason=(await response.json()).detail || reason;}catch{}user.remove();throw new Error(reason);}
  onAccepted();
  setRunState("routing");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
    const result = await reader.read();
    buffer += decoder.decode(result.value || new Uint8Array(), { stream: !result.done }).replaceAll("\r\n", "\n");
    let boundary;
    while ((boundary = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      if (block.trim()) {
        const event = parseSseBlock(block);
        handleEvent(event.type, event.payload);
      }
    }
      if (result.done) break;
    }
    if (!state.terminalSeen) throw new Error("连接中断，未收到任务结束事件");
  } finally {
    if (!state.terminalSeen) {
      await reader.cancel().catch(() => {});
      await api(`/api/chat/${state.requestId}/cancel`, { method: "POST" }).catch(() => {});
      for (const button of document.querySelectorAll(".approval-actions button")) button.disabled = true;
    }
    reader.releaseLock();
  }
}

async function openSettings() {
  const settings = await api("/api/settings");
  $('#autoClassifyPapers').checked=settings.auto_classify_papers === true;
  $("#providerMode").value = settings.provider_mode;
  $("#baseUrl").value = settings.base_url || "";
  $("#modelId").value = settings.model || "";
  $("#contextWindow").value = settings.context_window || "";
  $("#maxOutputTokens").value = settings.max_output_tokens || "";
  const limits=settings.effective_limits;
  if(limits){
    $('#contextWindow').placeholder=String(limits.context_window);
    $('#maxOutputTokens').placeholder=String(limits.max_output_tokens);
    $('#tokenDefaults').textContent=`当前生效 · 上下文 ${limits.context_window.toLocaleString('en-US')} tokens · 最大输出 ${limits.max_output_tokens.toLocaleString('en-US')} tokens`;
  }
  $("#apiKey").value = "";
  $("#providerDiagnostics").replaceChildren();
  $("#settingsStatus").textContent = settings.api_key_configured ? "API Key 已安全配置" : "尚未配置 API Key";
  const local = settings.local_model || {};
  $("#localModelStatus").textContent = `${local.state || "unavailable"}${local.error ? ` · ${local.error}` : ""}`;
  $("#providerStatus").textContent = settings.provider_mode === "mock" ? "离线演示" : settings.model || "Provider";
  $('#composerModelName').textContent=$('#providerStatus').textContent;
  $('#composerModel').title=`模型：${$('#providerStatus').textContent}`;
  $("#dataLocation").textContent = settings.data_dir || '';
  await routingSettings.load(settings);
  $("#settingsDialog").showModal();
  syncBusyControls();
}

function csvValues(value) {
  return value.split(",").map((item) => item.trim()).filter(Boolean);
}

let promptRevision=0;
$('#prompt').addEventListener('input',()=>{promptRevision++;});
$("#composer").addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = $("#prompt").value.trim();
  if ((!message && !attachments.ids().length) || state.running || state.selectingModel || state.maintenance || state.diagnosing || state.uploading || !attachments.ready()) return;
  const files=attachments.items();
  const draft=$('#prompt').value,revision=promptRevision;
  let accepted=false;
  $('#prompt').value='';
  void desktop.estimate();
  attachments.status('');
  try {
    await sendMessage(message,files,()=>{accepted=true;});
    if(state.lastFinish==='error'){attachments.status('本轮未完成，已发送消息可在对话中查看，附件已保留');return;}
    attachments.sent(files.map(f=>f.id));
  } catch (error) {
    if(!accepted && promptRevision===revision && !$('#prompt').value){$('#prompt').value=draft;void desktop.estimate();}
    attachments.status(error.message);
    append("event error", `连接失败 · ${error.message}`);
    setRunState("failed");
  }
});

$("#prompt").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    if(state.running)return;
    $("#composer").requestSubmit();
  }
});

$("#sendButton").onclick = async () => {
  if (!state.running || !state.requestId) return;
  setRunState("cancelling");
  try {
    await api(`/api/chat/${state.requestId}/cancel`, { method: "POST" });
  } catch (error) {
    append("event error", `停止请求失败 · ${error.message}`);
    if (!state.terminalSeen) setRunState("streaming");
  }
};

$("#clearConversation").onclick = async () => {
  if (state.running) return;
  if (!await confirmAction('清空当前会话的全部本地记录？此操作不可撤销。')) return;
  $("#clearConversation").disabled = true;
  try {
    await api("/api/conversation", { method: "DELETE" });
    attachments.reset();
    await refreshConversation();
    await desktop.refresh();
  } catch (error) {
    append("event error", `清空失败 · ${error.message}`);
  } finally {
    $("#clearConversation").disabled = state.running;
  }
};

$("#toggleSidebar").setAttribute("aria-expanded", "true");
$("#toggleSidebar").onclick = () => {
  const collapsed = $("#shell").classList.toggle("sidebar-collapsed");
  $("#toggleSidebar").setAttribute("aria-expanded", String(!collapsed));
  $("#toggleSidebar").title = collapsed ? "展开侧栏" : "折叠侧栏";
  $("#toggleSidebar").setAttribute("aria-label", $("#toggleSidebar").title);
};
$("#refreshTree").onclick = () => refreshTree().catch((error) => append("event error", error.message));
$("#openWorkspace").onclick = () => api("/api/workspace/open", { method: "POST" }).catch((error) => append("event error", error.message));
$("#chooseWorkspace").onclick = async () => {
  const path = window.pywebview?.api ? await window.pywebview.api.choose_workspace() : await promptAction("工作区绝对路径");
  if (!path) return;
  await api("/api/workspace/set", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ path }),
  });
  await Promise.all([refreshTree(), refreshConversation()]);
  await desktop.refresh();
};

$("#pdfInput").onchange = async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  try {
    const result = await api(`/api/kb/papers?background=true&filename=${encodeURIComponent(file.name)}`, {
      method: "POST",
      headers: { "content-type": "application/pdf" },
      body: file,
    });
    const notice = append('event');
    notice.dataset.importPaper = result.paper_id;
    notice.dataset.duplicate = String(result.duplicate === true);
    updatePaperImportNotice(notice, result);
    knowledgeChanged();
    await refreshKnowledge();
  } catch (error) {
    append("event error", `PDF 导入失败 · ${error.message}`);
  } finally {
    event.target.value = "";
  }
};

$("#newCard").onclick = () => openCard();
$("#cardForm").onsubmit = async (event) => {
  event.preventDefault();
  const cardId = $("#cardId").value;
  const payload = {
    title: $("#cardTitle").value.trim(),
    content: $("#cardContent").value.trim(),
    tags: csvValues($("#cardTags").value),
    sources: csvValues($("#cardSources").value),
  };
  try {
    let result;
    if (cardId) {
      result = await api(`/api/kb/cards/${cardId}`, {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(payload),
      });
    } else {
      const draft = await api("/api/kb/cards/drafts", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(payload),
      });
      result = await api(`/api/approvals/${draft.approval_id}/approve`, { method: "POST" });
    }
    knowledgeChanged(result.warning || "");
    $("#cardDialog").close();
    await refreshKnowledge();
  } catch (error) {
    $("#cardStatus").textContent = error.message;
  }
};

$("#deleteCard").onclick = async () => {
  const cardId = $("#cardId").value;
  if (!cardId || !await confirmAction("删除这张卡片及其索引？")) return;
  try {
    const result = await api(`/api/kb/cards/${cardId}`, { method: "DELETE" });
    knowledgeChanged(result.warning || "");
    $("#cardDialog").close();
    await refreshKnowledge();
  } catch (error) {
    $("#cardStatus").textContent = error.message;
  }
};

$("#settingsButton").onclick = () => openSettings().catch((error) => append("event error", error.message));
composerModelPicker({api,
  busy:()=>state.running||state.maintenance||state.diagnosing||state.uploading||state.selectingModel,
  pending:value=>{state.selectingModel=value;syncBusyControls();if(!value)void desktop.estimate();},
  saved:async result=>{
    const name=result.provider_mode==='mock'?'离线演示':result.model;
    $('#providerStatus').textContent=name;$('#composerModelName').textContent=name;$('#composerModel').title=`模型：${name}`;$('#modelId').value=result.model;
    try{await routingSettings.load(result);await desktop.refresh();}
    catch(error){append('event error',`模型已切换，界面信息刷新失败 · ${error.message}`);}
  },
});
document.addEventListener('click',event=>{if(!$('#conversationMenu').contains(event.target))$('#conversationMenu').open=false;});
document.addEventListener('keydown',event=>{if(event.key==='Escape')$('#conversationMenu').open=false;});
$("#providerTest").onclick = async () => {
  $("#settingsStatus").textContent = "正在检查模型列表";
  try {
    const result = await api("/api/provider/test", {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(settingsDraft()),
    });
    $("#settingsStatus").textContent = result.ok ? `模型列表可达${result.latency_ms != null ? ` · ${result.latency_ms} ms` : ""}${result.model_visible === false ? " · 当前模型不在列表中" : ""}` : "模型列表检查失败";
  } catch (error) {
    $("#settingsStatus").textContent = error.message;
  }
};

const DIAGNOSTIC_CODES = {
  authentication_failed: "鉴权失败", model_or_endpoint_not_found: "模型或接口未找到",
  rate_limited: "请求限流", service_unavailable: "服务不可用", request_rejected: "请求被拒绝",
  timeout: "请求超时", connection_failed: "连接失败", invalid_response: "响应格式无效",
  output_limit: "达到输出上限", incomplete_stream: "流未正常结束",
  tool_call_not_observed: "未观察到工具调用", invalid_tool_call: "工具调用格式不符合预期",
  empty_response: "未收到正常结束的正文",
};

function renderDiagnostics(result) {
  const target = $("#providerDiagnostics");
  target.replaceChildren();
  const heading = document.createElement("strong");
  heading.textContent = result.mode === "mock" ? "离线诊断" : "当前配置草稿诊断";
  target.append(heading);
  const capabilities = {models: "模型列表", generation: "正文生成", tool_call: "工具调用"};
  const statuses = {passed: "通过", failed: "失败", not_observed: "未观察到", skipped: "已跳过"};
  for (const check of result.checks) {
    const row = document.createElement("div");
    row.className = "diagnostic-row";
    row.dataset.capability = check.capability;
    row.dataset.status = check.status;
    const label = document.createElement("span");
    label.textContent = capabilities[check.capability] || check.capability;
    const detail = document.createElement("span");
    const observations = check.observed ? ` · 推理流${check.observed.reasoning ? "已观察到" : "未观察到"} · usage ${check.observed.usage ? "已观察到" : "未观察到"}` : "";
    detail.textContent = `${statuses[check.status] || check.status} · ${check.latency_ms} ms${check.code !== "ok" ? " · " + (DIAGNOSTIC_CODES[check.code] || check.code) : ""}${check.model_visible === false ? " · 当前模型不在列表中" : ""}${observations}`;
    row.append(label, detail);
    target.append(row);
  }
}

$("#providerDiagnose").onclick = async () => {
  if (state.diagnosing || state.maintenance || state.running) return;
  const draft = settingsDraft();
  if (draft.provider_mode !== "mock" && !await confirmAction("本次诊断最多发出 2 次可能计费的生成请求，另读取一次模型列表。仅发送固定诊断文本和无害工具定义，不发送对话或工作区内容，不执行工具，不保存配置。继续？")) return;
  state.diagnosing = true;
  syncBusyControls();
  $("#providerDiagnostics").replaceChildren();
  $("#settingsStatus").textContent = "正在分项诊断";
  try {
    renderDiagnostics(await api("/api/provider/diagnose", {
      method: "POST", headers: {"content-type": "application/json"},
      body: JSON.stringify({...draft, confirmed: draft.provider_mode !== "mock"}),
    }));
    $("#settingsStatus").textContent = "诊断完成 · 未保存配置";
  } catch (error) {
    $("#settingsStatus").textContent = `诊断失败 · ${error.message}`;
  } finally {
    state.diagnosing = false;
    syncBusyControls();
  }
};

$("#settingsForm").addEventListener("input", () => {
    $("#providerDiagnostics").replaceChildren();
    $("#settingsStatus").textContent = "配置草稿已变更 · 尚未保存";
});

modelPicker({api,draft:settingsDraft});

$("#migrateData").onclick = async () => {
  if (state.running || state.maintenance || state.diagnosing) return;
  try {
    const path = window.pywebview?.api?.choose_workspace ? await window.pywebview.api.choose_workspace() : await promptAction('新的空数据目录绝对路径');
    if (!path || !await confirmAction(`将历史、配置和知识库复制到 ${path}？原目录保留；完成后必须退出并重新打开应用。`)) return;
    state.maintenance = true; syncBusyControls();
    const result = await api('/api/data/migrate',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({path})});
    $("#settingsStatus").textContent = `已迁移到 ${result.data_dir} · 请退出并重新打开应用`;
    $("#dataLocation").textContent = result.data_dir;
    $("#migrateData").disabled = true;
  } catch(e) {state.maintenance=false;syncBusyControls();$("#settingsStatus").textContent=e.message;}
};

$("#reloadLocalModel").onclick = async () => {
  state.maintenance = true;
  syncBusyControls();
  try {
    const result = await api("/api/router/reload", { method: "POST" });
    $("#localModelStatus").textContent = result.state;
  } catch (error) {
    $("#localModelStatus").textContent = error.message;
  } finally {
    state.maintenance = false;
    syncBusyControls();
  }
};

function settingsDraft() {
  const numberOrNull = (selector) => {
    const value = $(selector).value.trim();
    return value ? Number(value) : null;
  };
  const payload = {
    provider_mode: $("#providerMode").value,
    base_url: $("#baseUrl").value.trim(),
    model: $("#modelId").value.trim(),
    context_window: numberOrNull("#contextWindow"),
    max_output_tokens: numberOrNull("#maxOutputTokens"),
    dynamic_background: false,
    auto_classify_papers: $('#autoClassifyPapers').checked,
    ...routingSettings.draft(),
  };
  const key = $("#apiKey").value.trim();
  if (key) payload.api_key = key;
  return payload;
}

$("#settingsForm").onsubmit = async (event) => {
  event.preventDefault();
  const payload = settingsDraft();
  try {
    const result = await api("/api/settings", {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    });
    $("#providerStatus").textContent = result.provider_mode === "mock" ? "离线演示" : result.model;
    $('#composerModelName').textContent = $('#providerStatus').textContent;
    $('#composerModel').title = `模型：${$('#providerStatus').textContent}`;
    routingSettings.accept(result);
    $("#settingsDialog").close();
    if (result.conversation_reset) await refreshConversation();
    await desktop.refresh();
  } catch (error) {
    $("#settingsStatus").textContent = error.message;
  }
};

$("#routeDetails").onclick = () => {
  keyValueList($("#routeDetailList"), {
    '引导档位': state.route?.effort || "abstain",
    '模型推理强度': state.route?.reasoning_effort || '模型默认',
    '推理模式': state.route?.reasoning_mode,
    '匹配片段': state.route?.matched_text,
    '窗口数量': state.route?.window_count,
    '注入提示词': state.route?.guidance_prompt,
    Source: state.route?.source,
    Similarity: state.route?.score,
    Margin: state.route?.margin,
    Reason: state.route?.reason,
    "Router state": state.route?.router_state,
  });
  $("#routeDialog").showModal();
};

for (const [button, dialog] of [
  ["#closeSettings", "#settingsDialog"],
  ["#closeRoute", "#routeDialog"],
  ["#closeFile", "#fileDialog"],
  ["#closeCard", "#cardDialog"],
]) {
  $(button).onclick = () => $(dialog).close();
}

for (const tab of document.querySelectorAll(".tab")) {
  tab.onclick = () => {
    document.querySelectorAll(".tab").forEach((item) => {
      item.classList.toggle("active", item === tab);
      item.setAttribute("aria-selected", String(item === tab));
    });
    $("#workspacePanel").classList.toggle("active", tab.dataset.tab === "workspace");
    $("#knowledgePanel").classList.toggle("active", tab.dataset.tab === "knowledge");
    if (tab.dataset.tab === "knowledge" && !state.audit) void auditKnowledge();
  };
  tab.onkeydown = (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const tabs = [...document.querySelectorAll(".tab")];
    const next = event.key === "Home" ? tabs[0] : event.key === "End" ? tabs.at(-1) : tabs[(tabs.indexOf(tab) + 1) % tabs.length];
    next.focus();
    next.click();
  };
}

const narrowWindow=window.matchMedia('(max-width:700px)');
$('#autoClassifyPapers').onchange=async()=>{
  const checkbox = $('#autoClassifyPapers');
  if (!checkbox.checked) return;
  checkbox.checked = false;
  checkbox.disabled = true;
  try { checkbox.checked = await confirmAction('启用后，新导入论文将自动发送部分正文给当前模型分类，并产生额外用量。是否启用？'); }
  finally { checkbox.disabled = false; }
};
const collapseNarrow=()=>{if(narrowWindow.matches)$('#shell').classList.add('sidebar-collapsed');};
narrowWindow.addEventListener('change',collapseNarrow);collapseNarrow();
setRunState("idle");
setInterval(() => {
  if (paperLibrary.needsRefresh() || state.papers.some((paper) => paper.status === "importing" || ['queued','classifying'].includes(paper.classification_status))) {
    refreshKnowledge().catch((error) => append("event error", error.message));
  }
}, 1000);
Promise.all([refreshTree(), refreshKnowledge(), refreshConversation(), desktop.refresh(), openSettings().then(() => $("#settingsDialog").close())]).then(() => { document.body.dataset.ready = 'true'; }).catch((error) => {
  append("event error", `初始化失败 · ${error.message}`);
});
