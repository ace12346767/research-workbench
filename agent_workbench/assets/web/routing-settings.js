const $ = selector => document.querySelector(selector);
const tiers = {fast:'简洁', standard:'标准', deep:'深入'};
const unknown = {policy:'unsupported', levels:[], can_link:false};

export function createRoutingSettings({api, busy, onSaved, onError, syncBusy}) {
  let saved = null, defaults = null, capability = unknown;
  let revision = 0, pending = false, saving = false;

  function renderProfiles(profiles) {
    const target = $('#guidanceProfiles');
    target.replaceChildren();
    for (const [tier, title] of Object.entries(tiers)) {
      const section = document.createElement('section');
      section.className = 'guidance-profile';
      const heading = document.createElement('h3');
      heading.textContent = title;
      section.append(heading);
      for (const [field, label, rows, limit] of [['examples','触发示例',3,3852], ['prompt','引导提示词',4,2000]]) {
        const holder = document.createElement('label');
        holder.textContent = label;
        const input = document.createElement('textarea');
        input.id = `guidance-${tier}-${field}`;
        input.rows = rows;
        input.maxLength = limit;
        input.required = true;
        input.value = field === 'examples' ? profiles[tier].examples.join('\n') : profiles[tier].prompt;
        holder.append(input);
        section.append(holder);
      }
      target.append(section);
    }
  }

  function renderMain() {
    const select = $('#reasoningEffort');
    const caps = saved?.reasoning_capabilities || unknown;
    select.replaceChildren();
    const add = (value, label) => select.add(new Option(label, value));
    add('', '模型默认');
    if (caps.can_link) add('auto', '自动');
    for (const level of caps.levels || []) add(level, level);
    select.value = saved?.guidance?.link_reasoning ? 'auto' : saved?.reasoning_effort || '';
    select.title = caps.can_link ? '模型推理强度' : '当前适配器未启用推理参数';
    sync();
  }

  function sync() {
    $('#linkReasoning').disabled = busy() || pending || !$('#guidanceEnabled').checked || !capability.can_link;
    $('#resetGuidance').disabled = busy() || !defaults;
    $('#reasoningEffort').disabled = busy() || saving || !saved?.reasoning_capabilities?.levels?.length;
    if (pending) $('#saveSettings').disabled = true;
  }

  function accept(settings) {
    saved = structuredClone(settings);
    renderMain();
  }

  async function load(settings) {
    const ticket = ++revision;
    defaults ||= await api('/api/router/defaults');
    if (ticket !== revision) return;
    capability = settings.reasoning_capabilities || unknown;
    pending = false;
    const guidance = settings.guidance || defaults;
    $('#guidanceEnabled').checked = guidance.enabled;
    $('#linkReasoning').checked = guidance.link_reasoning && capability.can_link;
    renderProfiles(guidance.profiles);
    $('#guidanceEditor').open = false;
    accept(settings);
  }

  function draft() {
    const profiles = {};
    for (const tier of Object.keys(tiers)) {
      profiles[tier] = {
        examples: $(`#guidance-${tier}-examples`).value.split('\n').map(value => value.trim()).filter(Boolean),
        prompt: $(`#guidance-${tier}-prompt`).value.trim(),
      };
    }
    const sameModel = saved?.model === $('#modelId').value.trim() && saved?.provider_mode === $('#providerMode').value;
    return {
      reasoning_effort: sameModel ? saved?.reasoning_effort || null : null,
      guidance: {enabled:$('#guidanceEnabled').checked,
        link_reasoning:$('#guidanceEnabled').checked && $('#linkReasoning').checked && capability.can_link && !pending, profiles},
    };
  }

  async function refreshCapability() {
    const ticket = ++revision;
    pending = true;
    syncBusy();
    try {
      const result = await api('/api/provider/capabilities', {
        method:'POST', headers:{'content-type':'application/json'},
        body:JSON.stringify({provider_mode:$('#providerMode').value, model:$('#modelId').value.trim()}),
      });
      if (ticket !== revision) return;
      capability = result;
      if (!result.can_link) $('#linkReasoning').checked = false;
    } catch (error) {
      if (ticket !== revision) return;
      capability = unknown;
      $('#linkReasoning').checked = false;
      $('#settingsStatus').textContent = error.message;
    } finally {
      if (ticket === revision) { pending = false; syncBusy(); }
    }
  }

  $('#guidanceEnabled').addEventListener('change', () => {
    if (!$('#guidanceEnabled').checked) $('#linkReasoning').checked = false;
    syncBusy();
  });
  $('#resetGuidance').onclick = () => {
    renderProfiles(defaults.profiles);
    $('#settingsStatus').textContent = '引导草稿已重置 · 尚未保存';
    syncBusy();
  };
  for (const id of ['#modelId', '#providerMode']) $(id).addEventListener('input', refreshCapability);
  $('#reasoningEffort').onchange = async () => {
    if (!saved || busy() || saving) { renderMain(); return; }
    const value = $('#reasoningEffort').value;
    const payload = {
      provider_mode:saved.provider_mode, base_url:saved.base_url, model:saved.model,
      context_window:saved.context_window, max_output_tokens:saved.max_output_tokens,
      reasoning_effort:value === 'auto' ? saved.reasoning_effort : value || null,
      guidance:{...structuredClone(saved.guidance || defaults), link_reasoning:value === 'auto'},
    };
    if (value === 'auto') payload.guidance.enabled = true;
    saving = true;
    syncBusy();
    try {
      const result = await api('/api/settings', {method:'PUT', headers:{'content-type':'application/json'}, body:JSON.stringify(payload)});
      accept(result);
      await onSaved(result);
    } catch (error) { renderMain(); onError(error); }
    finally { saving = false; syncBusy(); }
  };
  return {load, draft, accept, sync, isSaving:() => saving};
}
