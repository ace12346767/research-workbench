export function modelPicker({api, draft}) {
  const input = document.querySelector('#modelId');
  const list = document.querySelector('#modelChoices');
  const button = document.querySelector('#loadModels');
  const status = document.querySelector('#settingsStatus');
  const panel = document.createElement('section');
  panel.id = 'modelPickerPanel'; panel.hidden = true;
  const header = document.createElement('div'); header.className = 'model-picker-header';
  const label = document.createElement('span');
  const dismiss = document.createElement('button'); dismiss.type = 'button'; dismiss.className = 'icon-button';
  dismiss.title = '收起模型列表'; dismiss.setAttribute('aria-label', dismiss.title);
  dismiss.append(window.lucide.createElement(window.lucide.icons.X));
  list.before(panel); header.append(label, dismiss); panel.append(header, list);
  let models = [], revision = 0;
  function close() { list.hidden = true; panel.hidden = true; input.setAttribute('aria-expanded','false'); }
  dismiss.onclick = () => { ++revision; close(); input.focus(); };
  document.addEventListener('pointerdown', event => {
    if (!panel.contains(event.target) && event.target !== input && event.target !== button) { ++revision; close(); }
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !panel.hidden) { event.preventDefault(); event.stopPropagation(); close(); input.focus(); }
  }, true);
  function render(filter = '') {
    list.replaceChildren();
    const filtered = models.filter(model => model.toLowerCase().includes(filter.toLowerCase()));
    for (const model of filtered) {
      const option = document.createElement('button');
      option.type = 'button'; option.role = 'option'; option.textContent = draft().provider_mode === 'mock' && model === 'mock-model' ? '离线演示' : model;
      option.setAttribute('aria-selected', String(input.value === model));
      option.onclick = () => { input.value = model; close(); input.dispatchEvent(new Event('input',{bubbles:true})); close(); input.focus(); };
      list.append(option);
    }
    list.hidden = false; panel.hidden = false; label.textContent = `可用模型 · ${filtered.length}`; input.setAttribute('aria-expanded','true');
    if (!filtered.length) list.textContent = '没有匹配的模型';
  }
  button.onclick = async () => {
    if (!panel.hidden) { close(); input.focus(); return; }
    const ticket = ++revision;
    button.disabled = true; status.textContent = '正在加载模型';
    try {
      const result = await api('/api/provider/models', {method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(draft())});
      if (ticket !== revision) return;
      models = [...new Set(result.models)].sort(); render();
      status.textContent = `已加载 ${models.length} 个模型`;
    } catch(error) { status.textContent = `加载失败 · ${error.message}`; close(); }
    finally { button.disabled = false; }
  };
  input.addEventListener('input', () => { if (models.length) render(input.value); });
  input.addEventListener('keydown', event => {
    if (event.key === 'Escape') close();
    if (event.key === 'ArrowDown' && models.length) { event.preventDefault(); render(input.value); list.querySelector('button')?.focus(); }
  });
  list.addEventListener('keydown', event => {
    const choices = [...list.querySelectorAll('button')], index = choices.indexOf(document.activeElement);
    if (event.key === 'Escape') { close(); input.focus(); }
    if (['ArrowDown','ArrowUp'].includes(event.key)) { event.preventDefault(); choices[(index + (event.key === 'ArrowDown' ? 1 : choices.length-1)) % choices.length]?.focus(); }
  });
  for (const selector of ['#baseUrl','#providerMode']) document.querySelector(selector).addEventListener('input', () => { ++revision; models=[]; close(); });
  document.querySelector('#settingsDialog').addEventListener('close', () => { ++revision; close(); });
}
