const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs/promises');
const path = require('node:path');
const { chromium } = require('playwright');

const webRoot = path.resolve(__dirname, '../../assets/web');
const guidanceDefaults = {enabled:true, link_reasoning:false, profiles:Object.fromEntries(['fast','standard','deep'].map(tier => [tier, {examples:[`${tier} example`], prompt:`${tier} prompt`}]))};
const capabilities = {policy:'mapped', levels:['none','low','medium','high','xhigh'], default:'medium', mapping:{fast:'low',standard:'medium',deep:'xhigh'},can_link:true};
let server, browser, origin;
before(async () => {
  server = http.createServer(async (req, res) => {
    const name = req.url === '/' ? 'index.html' : req.url.replace(/^\/assets\//, '');
    const file = path.resolve(webRoot, name);
    if (!file.startsWith(webRoot + path.sep)) { res.writeHead(403).end(); return; }
    try {
      const body = await fs.readFile(file);
      res.setHeader('content-type', file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html');
      res.end(body);
    } catch { res.writeHead(404).end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  origin = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({ channel: 'msedge', headless: true });
});
after(async () => {
  await browser?.close();
  await new Promise(resolve => server.close(resolve));
});

async function pageFor(t, markdown, deltas = null) {
  const context = await browser.newContext();
  t.after(() => context.close());
  const page = await context.newPage();
  page.setDefaultTimeout(5000);
  const errors = [], external = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => {
    window.__executed = 0;
    window.__opened = [];
    window.pywebview = { api: { open_external_url: async url => { window.__opened.push(url); return true; } } };
  });
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.origin !== origin) { external.push(url.href); return route.abort(); }
    if (!url.pathname.startsWith('/api/')) return route.continue();
    if (url.pathname === '/api/chat/stream') {
      const { request_id } = route.request().postDataJSON();
      const events = deltas.map((delta, i) => `event: answer\ndata: ${JSON.stringify({request_id, sequence:i+1, data:{delta}})}\n\n`);
      events.push(`event: done\ndata: ${JSON.stringify({request_id, sequence:deltas.length+1, data:{finish_reason:'stop', history_turns:1}})}\n\n`);
      return route.fulfill({contentType:'text/event-stream', body:events.join('')});
    }
    const payload = url.pathname === '/api/conversation' ? {turns:markdown ? [{user:'Inspect evidence', assistant:markdown}] : [], max_turns:8}
      : url.pathname === '/api/settings' ? {provider_mode:'mock', local_model:{state:'ready'}, guidance:guidanceDefaults}
      : url.pathname === '/api/router/defaults' ? guidanceDefaults
      : url.pathname === '/api/provider/capabilities' ? {policy:'unsupported', levels:[], can_link:false}
      : url.pathname === '/api/sessions' ? {sessions:[],active:null}
      : url.pathname === '/api/context/estimate' ? {estimated_input:1500,capacity:128000,window:128000,input_limit:125440,output_reserved:2048,compacted_turns:0,estimator:'utf8_bytes_conservative',usage:{complete:false}}
      : url.pathname === '/api/kb/drafts' ? {drafts:[]}
      : url.pathname === '/api/kb/audit' ? {status:'healthy',issues:[],repairable_count:0,indexed_sources:0}
      : url.pathname === '/api/workspace/permissions' ? {mode:'ask'}
      : url.pathname === '/api/kb/cards' ? {cards:[]}
      : url.pathname === '/api/kb/papers' ? {papers:[]}
      : {workspace:null, entries:[]};
    return route.fulfill({json:payload});
  });
  await page.goto(origin);
  await page.waitForFunction(() => document.body.dataset.ready === 'true');
  if (markdown) await page.locator('.message.assistant').waitFor();
  return {page, errors, external};
}

test('sending clears submitted text immediately and preserves the next draft', async t => {
  const {page,errors}=await pageFor(t,null);
  let release;
  const gate=new Promise(resolve=>{release=resolve;});
  t.after(()=>release());
  await page.route('**/api/chat/stream',async route=>{
    await gate;
    const {request_id}=route.request().postDataJSON();
    await route.fulfill({contentType:'text/event-stream',body:`event: done\ndata: ${JSON.stringify({request_id,sequence:1,data:{finish_reason:'stop'}})}\n\n`});
  });
  await page.locator('#prompt').fill('Submitted message');
  await page.locator('#sendButton').click();
  assert.equal(await page.locator('#prompt').inputValue(),'');
  assert.equal(await page.locator('#sendButton').getAttribute('aria-label'),'停止生成');
  await page.locator('#prompt').fill('Next draft');
  release();
  await page.waitForFunction(()=>document.body.dataset.runState==='idle');
  assert.equal(await page.locator('#prompt').inputValue(),'Next draft');
  assert.deepEqual(errors,[]);
});

test('rejected submission restores original draft without overwriting newer edits', async t => {
  const {page}=await pageFor(t,null);
  for(const newer of [false,true]){
    let release;
    const gate=new Promise(resolve=>{release=resolve;});
    t.after(()=>release());
    await page.route('**/api/chat/stream',async route=>{await gate;await route.fulfill({status:409,json:{detail:'Busy'}});});
    await page.locator('#prompt').fill('  Original draft  ');
    await page.locator('#sendButton').click();
    assert.equal(await page.locator('#prompt').inputValue(),'');
    if(newer)await page.locator('#prompt').fill('New draft');
    release();
    await page.waitForFunction(()=>document.body.dataset.runState==='failed');
    assert.equal(await page.locator('#prompt').inputValue(),newer?'New draft':'  Original draft  ');
    assert.equal(await page.locator('.message.user').count(),0);
  }
});

test('budget settings show token units and effective defaults', async t => {
  const {page}=await pageFor(t,null);
  await page.route('**/api/settings',route=>route.fulfill({json:{provider_mode:'mock',guidance:guidanceDefaults,effective_limits:{context_window:32768,max_output_tokens:8192}}}));
  await page.locator('#settingsButton').click();
  assert.equal(await page.locator('#maxOutputTokens').getAttribute('placeholder'),'8192');
  assert.match(await page.locator('#tokenDefaults').textContent(),/8,192/);
  assert.match(await page.locator('label').filter({has:page.locator('#contextWindow')}).textContent(),/tokens/);
});

test('restored conversation renders headings, nested lists, tables and escaped code', async t => {
  const {page, errors} = await pageFor(t, '# Evidence\n\n**Strong** and *careful*.\n\n- first\n  - nested\n\n> quoted\n\n| Method | Result |\n| --- | --- |\n| A | pass |\n\n```html\n<img src=x onerror=alert(1)>\n```');
  assert.equal(await page.locator('.assistant h1').textContent(), 'Evidence');
  assert.equal(await page.locator('.assistant strong').textContent(), 'Strong');
  assert.equal(await page.locator('.assistant ul ul li').textContent(), 'nested');
  assert.equal(await page.locator('.assistant table tbody td').first().textContent(), 'A');
  assert.match(await page.locator('.assistant pre code').textContent(), /<img src=x/);
  assert.equal(await page.locator('.assistant img').count(), 0);
  assert.deepEqual(errors, []);
  await page.screenshot({path:path.resolve(__dirname, '../../.tmp/v7-markdown-content.png')});
});

test('composer models open locally, cancel safely and save only the selected model', async t => {
  const {page,errors}=await pageFor(t,'Keep this answer');
  let saved;
  await page.route('**/api/provider/models',r=>r.fulfill({json:{models:['alpha','beta']}}));
  await page.route('**/api/provider/model',r=>{saved=r.request().postDataJSON();return r.fulfill({json:{provider_mode:'openai-compatible',model:saved.model,guidance:guidanceDefaults}});});
  await page.locator('#prompt').fill('Keep this draft');
  await page.locator('#composerModel').click();
  await page.locator('#composerModelPopover').waitFor({state:'visible'});
  assert.equal(await page.locator('#settingsDialog').evaluate(e=>e.open),false);
  await page.keyboard.press('Escape');
  assert.equal(saved,undefined);
  await page.locator('#composerModel').click();
  await page.locator('#composerModelSearch').fill('beta');
  await page.locator('#composerModelChoices [role="option"]').click();
  await page.waitForFunction(()=>document.querySelector('#composerModelName').textContent==='beta');
  assert.deepEqual(saved,{model:'beta'});
  assert.equal(await page.locator('#prompt').inputValue(),'Keep this draft');
  assert.match(await page.locator('.assistant').textContent(),/Keep this answer/);
  assert.deepEqual(errors,[]);
});

test('assistant copy preserves original markdown and code without copying UI', async t => {
  const source='## Answer\n\nA **bold** point.\n\n```js\nconst value = 1;\n```';
  const {page,errors}=await pageFor(t,source);
  await page.evaluate(()=>{window.__copied=[];window.pywebview.api.copy_text=async text=>{window.__copied.push(text);return true;};});
  await page.locator('.assistant .copy-answer').click();
  await page.waitForFunction(()=>window.__copied.length===1);
  assert.equal(await page.evaluate(()=>window.__copied[0]),source);
  await page.locator('.assistant .copy-code').click();
  await page.waitForFunction(()=>window.__copied.length===2);
  assert.equal(await page.evaluate(()=>window.__copied[1]),'const value = 1;\n');
  assert.deepEqual(errors,[]);
});

test('composer picker handles failures, late results and compact layouts', async t => {
  const {page,errors}=await pageFor(t,'Saved response');
  await page.route('**/api/provider/models',r=>r.fulfill({status:503,json:{detail:'Unavailable'}}));
  await page.locator('#composerModel').click();
  await page.getByRole('button',{name:'重新加载',exact:true}).waitFor({state:'visible'});
  assert.equal(await page.locator('#settingsDialog').evaluate(e=>e.open),false);
  await page.route('**/api/provider/models',r=>r.fulfill({json:{models:['a-very-long-model-name-with-reasoning-and-vision-2026','beta']}}));
  await page.getByRole('button',{name:'重新加载',exact:true}).click();
  for(const width of [1366,390]){
    await page.setViewportSize({width,height:850});
    const rect=await page.locator('#composerModelPopover').boundingBox();
    assert.ok(rect.x>=0&&rect.x+rect.width<=width&&rect.y>=0,JSON.stringify(rect));
    await page.screenshot({path:path.resolve(__dirname,`../../.tmp/v26-composer-${width}.png`)});
  }
  await page.route('**/api/provider/model',r=>r.fulfill({status:400,json:{detail:'Busy'}}));
  await page.locator('#composerModelChoices button').last().click();
  await page.waitForFunction(()=>document.querySelector('.model-picker-status').textContent.includes('切换失败'));
  assert.equal(await page.locator('#composerModelName').textContent(),'离线演示');
  await page.keyboard.press('Escape');
  let release;
  await page.route('**/api/provider/models',async r=>{await new Promise(resolve=>release=resolve);await r.fulfill({json:{models:['late']}});});
  await page.locator('#composerModel').click();
  await page.waitForTimeout(100);await page.keyboard.press('Escape');release();await page.waitForTimeout(100);
  assert.equal(await page.locator('#composerModelPopover').isVisible(),false);
  assert.deepEqual(errors,[]);
});

test('streamed answer can be copied and clipboard failure is not reported as success', async t => {
  const {page,errors}=await pageFor(t,'',['Hello ','**world**']);
  await page.evaluate(()=>{window.__copied=[];window.pywebview.api.copy_text=async text=>{window.__copied.push(text);return true;};});
  await page.locator('#prompt').fill('hello');await page.locator('#composer').evaluate(f=>f.requestSubmit());
  await page.waitForFunction(()=>document.querySelector('.assistant strong')?.textContent==='world');
  await page.locator('.copy-answer').click();
  assert.equal(await page.evaluate(()=>window.__copied.at(-1)),'Hello **world**');
  await page.evaluate(()=>{window.pywebview.api.copy_text=async()=>false;Object.defineProperty(navigator,'clipboard',{value:{writeText:async()=>{throw new Error('denied');}},configurable:true});document.execCommand=()=>false;});
  await page.locator('.copy-answer').click();
  await page.waitForFunction(()=>document.querySelector('.copy-answer').title.includes('无法访问剪贴板'));
  assert.deepEqual(errors,[]);
});

test('scan background and its settings are removed', async t => {
  const {page,errors}=await pageFor(t,'');
  await page.locator('#settingsButton').click();
  assert.equal(await page.locator('#dynamicBackground,.ambient-plane,.ambient-preview').count(),0);
  await page.locator('#closeSettings').click();
  assert.equal(await page.locator('.mobius-stage').count(),1);
  assert.deepEqual(errors,[]);
});

test('offline demo labels do not expose Mock and preserve the provider identifier', async t => {
  const {page,errors}=await pageFor(t,'');
  assert.equal(await page.locator('#composerModelName').textContent(),'离线演示');
  await page.route('**/api/provider/models',route=>route.fulfill({json:{models:['mock-model']}}));
  await page.locator('#settingsButton').click();
  assert.equal(await page.locator('#providerMode').inputValue(),'mock');
  assert.equal(await page.locator('#providerMode option:checked').textContent(),'离线演示');
  await page.locator('#loadModels').click();
  await page.locator('#modelChoices [role="option"]').waitFor();
  assert.equal(await page.locator('#modelChoices [role="option"]').textContent(),'离线演示');
  assert.deepEqual(errors,[]);
});

test('model discovery opens searchable choices without replacing the request model ID', async t => {
  const {page,errors} = await pageFor(t, '');
  await page.route('**/api/provider/models', route => route.fulfill({json:{models:['deepseek-v4-flash-0731','gpt-5.5']}}));
  await page.locator('#settingsButton').click();
  await page.locator('#loadModels').click();
  await page.locator('#modelChoices [role="option"]').first().waitFor();
  assert.equal(await page.locator('#modelChoices [role="option"]').count(), 2);
  await page.locator('#modelChoices [role="option"]').first().click();
  assert.equal(await page.locator('#modelId').inputValue(), 'deepseek-v4-flash-0731');
  assert.deepEqual(errors, []);
});

test('model picker closes without selection and Escape preserves settings', async t => {
  const {page,errors}=await pageFor(t,'');
  await page.route('**/api/provider/models',route=>route.fulfill({json:{models:['alpha','beta']}}));
  await page.locator('#settingsButton').click();
  await page.locator('#modelId').fill('keep-model');
  for (const method of ['button','escape','outside','toggle']) {
    await page.locator('#loadModels').click();
    await page.locator('#modelChoices [role="option"]').first().waitFor();
    if(method==='button') await page.getByRole('button',{name:'收起模型列表'}).click();
    if(method==='escape') await page.keyboard.press('Escape');
    if(method==='outside') await page.locator('#settingsDialog h2').click();
    if(method==='toggle') await page.locator('#loadModels').click();
    assert.equal(await page.locator('#modelChoices').isVisible(),false);
    assert.equal(await page.locator('#settingsDialog').isVisible(),true);
    assert.equal(await page.locator('#modelId').inputValue(),'keep-model');
  }
  await page.locator('#loadModels').click();
  await page.locator('#modelChoices [role="option"]').first().waitFor();
  await page.screenshot({path:path.resolve(__dirname,'../../.tmp/v16-model-picker.png')});
  assert.deepEqual(errors,[]);
});

test('classification consent is an accessible app modal with safe cancellation', async t => {
  const {page,errors}=await pageFor(t,'');
  let nativeDialogs=0;page.on('dialog',async dialog=>{nativeDialogs++;await dialog.dismiss();});
  await page.locator('#settingsButton').click();
  await page.locator('#autoClassifyPapers').click();
  assert.equal(await page.locator('.ambient-preview').count(),0);
  await page.locator('#actionDialog').waitFor();
  assert.equal(await page.locator('#autoClassifyPapers').isChecked(),false);
  assert.match(await page.locator('#actionTitle').textContent(),/启用自动论文分类/);
  assert.match(await page.locator('#actionDescription').textContent(),/额外用量/);
  await page.screenshot({path:path.resolve(__dirname,'../../.tmp/v16-app-dialog.png')});
  await page.keyboard.press('Escape');
  await page.locator('#actionDialog').waitFor({state:'detached'});
  assert.equal(await page.locator('#autoClassifyPapers').isChecked(),false);
  assert.equal(await page.locator('#settingsDialog').isVisible(),true);
  await page.locator('#autoClassifyPapers').click();
  await page.locator('#actionAccept').click();
  await page.waitForFunction(()=>document.querySelector('#autoClassifyPapers').checked);
  await page.setViewportSize({width:390,height:720});
  await page.locator('#autoClassifyPapers').click();
  await page.locator('#autoClassifyPapers').click();
  await page.locator('#actionDialog').waitFor();
  const box=await page.locator('#actionDialog').boundingBox();
  assert.ok(box.x>=0 && box.x+box.width<=390);
  await page.screenshot({path:path.resolve(__dirname,'../../.tmp/v16-app-dialog-narrow.png')});
  await page.locator('#actionClose').click();
  assert.equal(nativeDialogs,0);assert.deepEqual(errors,[]);
});

test('PDF import notice follows background completion without duplicate messages', async t => {
  const {page,errors}=await pageFor(t,'');
  const paper={paper_id:'paper-progress',filename:'example.pdf',status:'importing',page_count:0,chunk_count:0};
  let uploaded=false;
  await page.route('**/api/kb/papers*',route=>{
    if(route.request().method()==='POST'){uploaded=true;return route.fulfill({json:{...paper}});}
    return route.fulfill({json:{papers:uploaded?[{...paper}]:[]}});
  });
  await page.locator('#pdfInput').setInputFiles({name:'example.pdf',mimeType:'application/pdf',buffer:Buffer.from('%PDF-test')});
  const notice=page.locator('#messages [data-import-paper="paper-progress"]');
  await page.waitForFunction(()=>document.querySelector('[data-import-paper]')?.textContent.includes('正在建立索引'));
  paper.status='ready';paper.page_count=11;paper.chunk_count=49;
  await page.waitForFunction(()=>document.querySelector('[data-import-paper]')?.textContent.includes('PDF 已索引'));
  assert.equal(await notice.count(),1);
  assert.match(await notice.textContent(),/11 页 · 49 个检索片段/);
  assert.doesNotMatch(await notice.textContent(),/importing/);
  assert.deepEqual(errors,[]);
});

test('PDF import failures and duplicate uploads show accurate safe status', async t => {
  const {page,errors}=await pageFor(t,'');
  const paper={paper_id:'paper-failure',filename:'example.pdf',status:'importing',page_count:0,chunk_count:0};
  let uploaded=false;
  await page.route('**/api/kb/papers*',route=>{
    if(route.request().method()==='POST'){const duplicate=uploaded;uploaded=true;return route.fulfill({json:{...paper,duplicate}});}
    return route.fulfill({json:{papers:uploaded?[{...paper}]:[]}});
  });
  const upload=()=>page.locator('#pdfInput').setInputFiles({name:'example.pdf',mimeType:'application/pdf',buffer:Buffer.from('%PDF-test')});
  await upload();
  await page.waitForFunction(()=>document.querySelector('[data-import-paper]')?.textContent.includes('正在建立索引'));
  paper.status='failed';paper.error='<img src=x onerror=alert(1)> extraction failed';
  await page.waitForFunction(()=>document.querySelector('[data-import-paper]')?.textContent.includes('PDF 索引失败'));
  assert.equal(await page.locator('[data-import-paper] img').count(),0);
  await upload();
  await page.waitForFunction(()=>document.querySelectorAll('[data-import-paper]').length===2);
  assert.match(await page.locator('[data-import-paper]').last().textContent(),/已存在，未重复导入 · PDF 索引失败/);
  assert.deepEqual(errors,[]);
});

test('context details distinguish estimated occupancy from actual and cumulative usage', async t => {
  const {page,errors} = await pageFor(t, '');
  await page.locator('#contextStatus').click();
  await page.locator('#contextDialog').waitFor();
  assert.match(await page.locator('#contextDetails').textContent(), /128,000/);
  assert.match(await page.locator('#contextDetails').textContent(), /估算|保守/);
  assert.deepEqual(errors, []);
});

test('session switches and permission changes require explicit user actions', async t => {
  const {page,errors} = await pageFor(t, '');
  await page.route('**/api/sessions', route => route.fulfill({json:{active:'s1',sessions:[{session_id:'s1',title:'First'},{session_id:'s2',title:'Second'}]}}));
  await page.reload();
  await page.locator('#sessionSelect option[value="s2"]').waitFor({state:'attached'});
  let selected = false;
  await page.route('**/api/sessions/s2/select', route => {selected=true; return route.fulfill({json:{}});});
  await page.locator('#sessionSelect').selectOption('s2');
  await page.waitForTimeout(150);
  assert.equal(selected,true);
  await page.locator('#permissionMode').selectOption('auto_edit');
  await page.locator('#actionCancel').click();
  await page.waitForFunction(()=>document.querySelector('#permissionMode').value==='ask');
  assert.equal(await page.locator('#permissionMode').inputValue(),'ask');
  assert.deepEqual(errors, []);
});

test('Mobius assets render and pending card drafts can be reviewed without sending chat', async t => {
  const {page,errors,external} = await pageFor(t, '');
  let draft = {draft_id:'d1',title:'Reusable result',content:'Proposed content',reason:'Verified solution',tags:['verified'],sources:['test.py'],duplicates:[]};
  let confirmed=false;
  await page.route('**/api/kb/drafts', route => route.fulfill({json:{drafts:confirmed?[]:[draft]}}));
  await page.route('**/api/kb/drafts/d1', route => {draft={...draft,...route.request().postDataJSON()};return route.fulfill({json:draft});});
  await page.route('**/api/kb/drafts/d1/confirm', route => {confirmed=true;return route.fulfill({json:{status:'applied'}});});
  await page.reload();
  await page.waitForFunction(()=>document.querySelector('.brand-mark').naturalWidth === 512);
  await page.locator('[data-tab="knowledge"]').click();
  await page.locator('#draftList button').click();
  await page.locator('#draftContent').fill('Reviewed content');
  for(const width of [1366,960,390]) {
    await page.setViewportSize({width,height:720});
    assert.ok(await page.locator('#draftDialog').evaluate(node=>node.scrollWidth<=node.clientWidth));
    await page.screenshot({path:path.resolve(__dirname,`../../.tmp/v11-draft-${width}.png`)});
  }
  await page.locator('#confirmDraft').click();
  await page.waitForFunction(()=>!document.querySelector('#draftDialog').open);
  assert.equal(confirmed,true);assert.equal(draft.content,'Reviewed content');
  await page.setViewportSize({width:1366,height:880});
  await page.screenshot({path:path.resolve(__dirname,'../../.tmp/v11-workbench.png')});
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
});

test('guidance editor keeps separate examples, auto mapping and remembered manual reasoning', async t => {
  const {page, errors} = await pageFor(t, '');
  let saved = {provider_mode:'openai-compatible', base_url:'https://example.invalid/v1', model:'gpt-5.5',
    reasoning_effort:'high', guidance:structuredClone(guidanceDefaults), reasoning_capabilities:capabilities, local_model:{state:'ready'}};
  await page.route('**/api/settings', async route => {
    if (route.request().method() === 'PUT') saved = {...saved, ...route.request().postDataJSON()};
    await route.fulfill({json:saved});
  });
  await page.route('**/api/provider/capabilities', route => route.fulfill({json:capabilities}));
  await page.locator('#settingsButton').click();
  await page.locator('#linkReasoning').check();
  await page.locator('#guidanceEditor summary').click();
  await page.locator('#guidance-fast-examples').fill('My trigger\nAnother trigger');
  await page.locator('#guidance-fast-prompt').fill('My independent guidance');
  await page.locator('#saveSettings').click();
  await page.waitForFunction(() => !document.querySelector('#settingsDialog').open);
  assert.equal(await page.locator('#reasoningEffort').inputValue(), 'auto');
  assert.deepEqual(saved.guidance.profiles.fast.examples, ['My trigger','Another trigger']);
  assert.equal(saved.guidance.profiles.fast.prompt, 'My independent guidance');
  assert.equal(saved.reasoning_effort, 'high');
  await page.locator('#settingsButton').click();
  await page.locator('#linkReasoning').uncheck();
  await page.locator('#saveSettings').click();
  await page.waitForFunction(() => !document.querySelector('#settingsDialog').open);
  assert.equal(await page.locator('#reasoningEffort').inputValue(), 'high');
  await page.locator('#reasoningEffort').selectOption('auto');
  await page.waitForFunction(() => !document.querySelector('#reasoningEffort').disabled);
  await page.locator('#reasoningEffort').selectOption('xhigh');
  await page.waitForFunction(() => !document.querySelector('#reasoningEffort').disabled);
  assert.equal(saved.guidance.link_reasoning, false);
  assert.equal(saved.guidance.enabled, true);
  assert.equal(saved.reasoning_effort, 'xhigh');
  assert.deepEqual(errors, []);
});

test('DeepSeek allows opt-in linkage and manual levels while reset changes only the draft', async t => {
  const {page, errors} = await pageFor(t, '');
  const saved = {provider_mode:'openai-compatible',model:'deepseek-v4-flash',reasoning_effort:'max',
    guidance:structuredClone(guidanceDefaults), local_model:{state:'ready'},
    reasoning_capabilities:{policy:'mapped',levels:['low','high','max'],default:'high',mapping:{fast:'low',standard:'high',deep:'max'},can_link:true}};
  saved.guidance.profiles.fast.prompt = 'Saved custom';
  await page.route('**/api/settings', route => route.fulfill({json:saved}));
  await page.locator('#settingsButton').click();
  await page.waitForFunction(()=>!document.querySelector('#linkReasoning').disabled);
  assert.equal(await page.locator('#linkReasoning').isDisabled(), false);
  assert.equal(await page.locator('#linkReasoning').isChecked(), false);
  await page.locator('#guidanceEditor summary').click();
  await page.locator('#resetGuidance').click();
  assert.equal(await page.locator('#guidance-fast-prompt').inputValue(), 'fast prompt');
  await page.locator('#closeSettings').click();
  assert.equal(await page.locator('#reasoningEffort').inputValue(), 'max');
  assert.equal(await page.locator('#reasoningEffort').isDisabled(), false);
  assert.deepEqual(await page.locator('#reasoningEffort option').allTextContents(), ['模型默认','自动','low','high','max']);
  for(const width of [1366,390]){
    await page.setViewportSize({width,height:850});
    const control=await page.locator('.reasoning-control').boundingBox();
    assert.ok(control.width<=110&&control.x>=0&&control.x+control.width<=width,JSON.stringify(control));
    assert.equal(await page.locator('#reasoningEffort').evaluate(e=>getComputedStyle(e).textAlignLast),'center');
    assert.equal(await page.locator('.reasoning-select svg').isVisible(),true);
    await page.screenshot({path:path.resolve(__dirname,`../../.tmp/v27-reasoning-${width}.png`)});
  }
  await page.locator('#settingsButton').click();
  await page.locator('#settingsDialog').waitFor({state:'visible'});
  assert.equal(await page.locator('#guidance-fast-prompt').inputValue(), 'Saved custom');
  assert.deepEqual(errors, []);
});

test('stale model capabilities cannot re-enable linkage and failed manual saves roll back', async t => {
  const {page, errors} = await pageFor(t, '');
  const saved = {provider_mode:'openai-compatible', model:'gpt-5.5', reasoning_effort:'high',
    guidance:structuredClone(guidanceDefaults), reasoning_capabilities:capabilities, local_model:{state:'ready'}};
  await page.route('**/api/settings', route => route.request().method() === 'PUT'
    ? route.fulfill({status:409,json:{detail:'Busy'}}) : route.fulfill({json:saved}));
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  await page.route('**/api/provider/capabilities', async route => {
    const {model} = route.request().postDataJSON();
    if (model === 'gpt-5.2') await gate;
    await route.fulfill({json:model === 'gpt-5.2' ? capabilities : {policy:'unsupported',levels:[],can_link:false}});
  });
  await page.locator('#settingsButton').click();
  await page.locator('#modelId').fill('gpt-5.2');
  await page.locator('#modelId').fill('unknown');
  await page.waitForFunction(() => !document.querySelector('#saveSettings').disabled);
  release();
  await page.waitForTimeout(150);
  assert.equal(await page.locator('#linkReasoning').isDisabled(), true);
  await page.locator('#closeSettings').click();
  await page.locator('#reasoningEffort').selectOption('low');
  await page.waitForFunction(() => !document.querySelector('#reasoningEffort').disabled);
  assert.equal(await page.locator('#reasoningEffort').inputValue(), 'high');
  assert.match(await page.locator('.event.error').textContent(), /Busy/);
  assert.deepEqual(errors, []);
});

test('expanded guidance editor fits desktop and mobile without clipping text', async t => {
  const {page, errors} = await pageFor(t, '');
  await page.locator('#settingsButton').click();
  await page.locator('#guidanceEditor summary').click();
  for (const width of [1366, 960, 390]) {
    await page.setViewportSize({width,height:720});
    assert.equal(await page.locator('#guidanceProfiles textarea').count(), 6);
    assert.ok(await page.locator('#settingsDialog').evaluate(node => node.scrollWidth <= node.clientWidth));
    await page.locator('#guidance-fast-prompt').scrollIntoViewIfNeeded();
    await page.screenshot({path:path.resolve(__dirname, `../../.tmp/v10-guidance-${width}.png`)});
  }
  assert.deepEqual(errors, []);
});

test('untrusted HTML, unsafe URLs and image syntax cannot load active content', async t => {
  const source = '<script>window.__executed=1</script>\n\n<img src="https://tracker.invalid/pixel" onerror="window.__executed=2">\n\n<svg onload="window.__executed=3"></svg>\n\n<form id="composer"><input name="sendButton"></form>\n\n[bad](javascript:alert(1)) [encoded](jav&#x61;script:alert(1)) [file](file:///C:/Windows) [data](data:text/html,test) [relative](/api/settings) [protocol](//tracker.invalid)\n\n![remote](https://tracker.invalid/image.png)\n\n[Safe](https://example.com/paper?q=1)';
  const {page, external, errors} = await pageFor(t, source);
  assert.equal(await page.locator('.markdown-content script,.markdown-content img,.markdown-content svg,.markdown-content form,.markdown-content iframe,.markdown-content input').count(), 0);
  assert.deepEqual(await page.locator('.assistant a').evaluateAll(nodes => nodes.map(n => n.getAttribute('href'))), ['https://example.com/paper?q=1']);
  assert.equal(await page.evaluate(() => window.__executed), 0);
  assert.deepEqual(external, []);
  assert.deepEqual(errors, []);
});

test('external links use desktop bridge without navigating the main window', async t => {
  const {page} = await pageFor(t, '[Paper](https://example.com/paper)');
  await page.locator('.assistant a').click();
  assert.deepEqual(await page.evaluate(() => window.__opened), ['https://example.com/paper']);
  assert.equal(page.url(), origin + '/');
});

test('browser launch failure is visible and does not navigate the desktop', async t => {
  const {page, errors} = await pageFor(t, '[Paper](https://example.com/paper)');
  await page.evaluate(() => { window.pywebview.api.open_external_url = async () => false; });
  await page.locator('.assistant a').click();
  assert.match(await page.locator('.assistant [role="status"]').textContent(), /Unable to open link/);
  assert.equal(page.url(), origin + '/');
  assert.deepEqual(errors, []);
});

test('successive answer deltas finish an unclosed fence without executing code', async t => {
  const {page, errors} = await pageFor(t, '', ['## Result\n\n```js\n', 'window.__executed=4;', '\n```\n\n- complete']);
  await page.locator('#prompt').fill('test');
  await page.locator('#sendButton').click();
  await page.locator('.done-event').waitFor();
  assert.equal(await page.locator('.assistant h2').textContent(), 'Result');
  assert.equal(await page.locator('.assistant pre code').textContent(), 'window.__executed=4;\n');
  assert.equal(await page.locator('.assistant li').textContent(), 'complete');
  assert.equal(await page.evaluate(() => window.__executed), 0);
  assert.deepEqual(errors, []);
});

test('wide tables and code remain scrollable within desktop and narrow layouts', async t => {
  const long = 'evidence'.repeat(100);
  const {page} = await pageFor(t, `# Evidence\n\n| ${long} | another |\n| --- | --- |\n| result | pass |\n\n\`\`\`text\n${long}\n\`\`\``);
  for (const width of [1366, 960, 390]) {
    await page.setViewportSize({width, height:768});
    if (width < 700) await page.locator('.shell').evaluate(node => node.classList.add('sidebar-collapsed'));
    const metrics = await page.evaluate(() => {
      const table = document.querySelector('.assistant .markdown-table');
      return {scroll:document.documentElement.scrollWidth, width:innerWidth, tableScrollable:!!table && table.scrollWidth > table.clientWidth};
    });
    assert.ok(metrics.scroll <= metrics.width, JSON.stringify(metrics));
    assert.ok(metrics.tableScrollable);
    await page.screenshot({path:path.resolve(__dirname, `../../.tmp/v7-markdown-${width}.png`)});
  }
});

test('knowledge audit escapes filenames and repair requires confirmation', async t => {
  const {page, errors} = await pageFor(t, '');
  let audits = 0, repairs = 0;
  const healthy = {status:'healthy', issues:[], repairable_count:0, indexed_sources:1};
  await page.route('**/api/kb/audit', route => {
    audits++;
    return route.fulfill({json:repairs ? healthy : {status:'issues', repairable_count:1, indexed_sources:0,
      issues:[{kind:'card_index_missing', path:'<img src=x onerror=alert(1)>.md', repairable:true}]}});
  });
  await page.route('**/api/kb/repair', route => {
    repairs++;
    return route.fulfill({json:{repaired:[{}], errors:[], after:healthy}});
  });
  await page.locator('[data-tab="knowledge"]').click();
  await page.waitForFunction(() => document.querySelector('#knowledgeStatus')?.textContent.includes('可修复 1'));
  assert.match(await page.locator('#knowledgeIssues').textContent(), /<img src=x/);
  assert.equal(await page.locator('#knowledgeIssues img').count(), 0);
  await page.locator('#repairKnowledge').click();
  await page.locator('#actionCancel').click();
  assert.equal(repairs, 0);
  await page.locator('#repairKnowledge').click();
  await page.locator('#actionAccept').click();
  await page.waitForFunction(() => document.querySelector('#knowledgeStatus').textContent.includes('一致'));
  assert.equal(repairs, 1);
  assert.equal(await page.locator('#repairKnowledge').isDisabled(), true);
  await page.waitForTimeout(1200);
  assert.equal(audits, 1, 'audit must not hash all files on a timer');
  assert.deepEqual(errors, []);
});

test('remote diagnostics require consent, use the draft and clear stale results', async t => {
  const {page, errors} = await pageFor(t, '');
  let requests = [], release;
  const gate = new Promise(resolve => { release = resolve; });
  await page.route('**/api/provider/diagnose', async route => {
    requests.push(route.request().postDataJSON());
    await gate;
    return route.fulfill({json:{mode:'openai-compatible', checks:[
      {capability:'models', status:'passed', code:'ok', latency_ms:1},
      {capability:'generation', status:'passed', code:'ok', observed:{content:true, reasoning:false, usage:true}, latency_ms:2},
      {capability:'tool_call', status:'not_observed', code:'tool_call_not_observed', latency_ms:3},
    ]}});
  });
  await page.locator('#settingsButton').click();
  await page.locator('#providerMode').selectOption('openai-compatible');
  await page.locator('#baseUrl').fill('https://provider.invalid/v1');
  await page.locator('#modelId').fill('draft-model');
  await page.locator('#providerDiagnose').click();
  assert.match(await page.locator('#actionDescription').textContent(), /2.*计费/);
  await page.locator('#actionCancel').click();
  assert.equal(requests.length, 0);
  await page.locator('#providerDiagnose').click();
  await page.locator('#actionAccept').click();
  await page.waitForFunction(() => document.querySelector('#modelId').disabled);
  assert.equal(await page.locator('#saveSettings').isDisabled(), true);
  release();
  await page.locator('#providerDiagnostics [data-capability="tool_call"]').waitFor();
  assert.equal(requests[0].model, 'draft-model');
  assert.equal(requests[0].confirmed, true);
  assert.match(await page.locator('#providerDiagnostics').textContent(), /未观察到/);
  assert.equal(await page.locator('#modelId').isDisabled(), false);
  await page.locator('#modelId').fill('other-model');
  assert.equal(await page.locator('#providerDiagnostics').textContent(), '');
  assert.deepEqual(errors, []);
});

test('card indexing warnings remain visible after a successful source save', async t => {
  const {page, errors} = await pageFor(t, '');
  await page.route('**/api/kb/cards/drafts', route => route.fulfill({json:{approval_id:'card-approval-test'}}));
  await page.route('**/api/approvals/card-approval-test/approve', route => route.fulfill({json:{status:'applied', index_status:'failed', warning:'Card saved, but indexing failed'}}));
  await page.locator('[data-tab="knowledge"]').click();
  await page.locator('#newCard').click();
  await page.locator('#cardTitle').fill('Evidence');
  await page.locator('#cardContent').fill('Saved source');
  await page.locator('#cardForm button[type="submit"]').click();
  await page.waitForFunction(() => !document.querySelector('#cardDialog').open);
  assert.match(await page.locator('#knowledgeActionStatus').textContent(), /Card saved, but indexing failed/);
  assert.deepEqual(errors, []);
});

test('desktop chrome uses accessible offline icons and stable composer alignment', async t => {
  const {page, errors, external} = await pageFor(t, '## Research note\n\nA focused answer with **evidence**.');
  await page.waitForFunction(() => !!document.querySelector('#sendButton svg'));
  const buttons = await page.locator('.icon-button').evaluateAll(nodes => nodes.map(node => ({
    icon:!!node.querySelector('svg'), name:node.getAttribute('aria-label'), title:node.title,
  })));
  assert.ok(buttons.every(button => button.icon && button.name && button.title), JSON.stringify(buttons));
  for (const width of [1920, 1366, 960]) {
    await page.setViewportSize({width, height:width === 960 ? 600 : 1080});
    const bounds = await page.evaluate(() => {
      const a = document.querySelector('.message.assistant').getBoundingClientRect();
      const b = document.querySelector('.composer-inner').getBoundingClientRect();
      return {left:Math.abs(a.left-b.left), right:Math.abs(a.right-b.right), overflow:document.documentElement.scrollWidth > innerWidth};
    });
    assert.ok(bounds.left <= 1 && bounds.right <= 1 && !bounds.overflow, JSON.stringify(bounds));
    await page.screenshot({path:path.resolve(__dirname, `../../.tmp/v9-workbench-${width}.png`)});
  }
  assert.deepEqual(errors, []);
  assert.deepEqual(external, []);
});

test('settings groups and actions fit compact native windows with visible keyboard focus', async t => {
  const {page, errors} = await pageFor(t, '');
  await page.locator('#settingsButton').click();
  assert.deepEqual(await page.locator('#settingsForm fieldset legend').allTextContents(), ['连接配置', '模型限制', '本地模型']);
  for (const width of [1366, 960, 390]) {
    await page.setViewportSize({width, height:600});
    await page.locator('#saveSettings').scrollIntoViewIfNeeded();
    const layout = await page.locator('#settingsDialog').evaluate(node => ({
      width:node.clientWidth, scroll:node.scrollWidth,
      actions:[...node.querySelectorAll('.dialog-actions button')].map(button => {
        const r=button.getBoundingClientRect(); return {left:r.left, right:r.right, width:button.clientWidth, scroll:button.scrollWidth};
      }), viewport:innerWidth,
    }));
    assert.ok(layout.scroll <= layout.width, JSON.stringify(layout));
    assert.ok(layout.actions.every(a => a.left >= 0 && a.right <= layout.viewport && a.scroll <= a.width), JSON.stringify(layout));
    await page.screenshot({path:path.resolve(__dirname, `../../.tmp/v9-settings-${width}.png`)});
  }
  await page.locator('#saveSettings').focus();
  await page.keyboard.press('Shift+Tab');
  assert.equal(await page.evaluate(() => document.activeElement.id), 'providerDiagnose');
  const focus = await page.evaluate(() => getComputedStyle(document.activeElement).outlineStyle);
  assert.notEqual(focus, 'none');
  assert.deepEqual(errors, []);
});

test('knowledge assets use consistent icons and preserve issue summary when collapsed', async t => {
  const {page, errors} = await pageFor(t, '');
  await page.route('**/api/kb/papers', route => route.fulfill({json:{papers:[{paper_id:'p1', filename:'Research-methods.pdf', status:'ready', page_count:12}]}}));
  await page.route('**/api/kb/cards', route => route.fulfill({json:{cards:[{card_id:'c1', title:'Routing evidence', content:'A note', tags:[]}]}}));
  await page.route('**/api/kb/audit', route => route.fulfill({json:{status:'issues', repairable_count:0, indexed_sources:2,
    issues:[{kind:'invalid_card', path:'broken.md', repairable:false}]}}));
  await page.reload();
  await page.locator('[data-tab="knowledge"]').click();
  await page.waitForFunction(() => document.querySelector('#knowledgeStatus').textContent.includes('1'));
  assert.equal(await page.locator('#paperList .asset-label .asset-icon svg').count(), 1);
  assert.equal(await page.locator('#cardList .asset-icon svg').count(), 1);
  assert.equal(await page.locator('#paperList .row-actions button svg').count(), 2);
  await page.locator('#knowledgeAudit').evaluate(node => { node.open = false; });
  assert.equal(await page.locator('#knowledgeStatus').isVisible(), true);
  assert.equal(await page.locator('#knowledgeIssues').isVisible(), false);
  await page.locator('#knowledgeAudit summary').click();
  assert.match(await page.locator('#knowledgeIssues').textContent(), /broken.md/);
  await page.screenshot({path:path.resolve(__dirname, '../../.tmp/v9-knowledge.png')});
  await page.locator('#newCard').click();
  assert.equal(await page.locator('#deleteCard').isVisible(), false, 'new cards must not expose the delete command');
  assert.deepEqual(errors, []);
});

test('semantic compaction controls preserve the draft, show safe summary text and fit native windows', async t => {
  const {page, errors} = await pageFor(t, 'Saved answer');
  let calls=0;
  await page.route('**/api/context/compact', async route => {
    calls++;
    await new Promise(resolve=>setTimeout(resolve,150));
    return route.fulfill({json:{status:'completed'}});
  });
  await page.route('**/api/context/summary', route => route.fulfill({json:{checkpoint:{summary:'## Constraints\nDo not force max. <img src=x onerror=alert(1)>',through_id:12,created_at:'2026-09-06'},usage:{requests:1,total_tokens:120,complete:true}}}));
  await page.locator('#prompt').fill('Keep this unsent message');
  await page.locator('#contextStatus').click();
  await page.locator('#compactNow').click();
  await page.waitForFunction(()=>document.querySelector('#compactionStatus').textContent.includes('已完成'));
  assert.equal(calls,1);
  assert.equal(await page.locator('#prompt').inputValue(),'Keep this unsent message');
  await page.locator('#viewSummary').click();
  await page.locator('#summaryDialog').waitFor({state:'visible'});
  assert.match(await page.locator('#summaryContent').textContent(), /Do not force max/);
  assert.equal(await page.locator('#summaryContent img').count(),0);
  for(const width of [1366,960,390]) {
    await page.setViewportSize({width,height:700});
    const box=await page.locator('#summaryDialog').evaluate(node=>({width:node.clientWidth,scroll:node.scrollWidth}));
    assert.ok(box.scroll<=box.width,JSON.stringify(box));
    await page.screenshot({path:path.resolve(__dirname, `../../.tmp/v12-summary-${width}.png`)});
  }
  assert.deepEqual(errors,[]);
});

test('composer uses one send-stop control and retains text and attachments on rejected send', async t => {
  const {page, errors}=await pageFor(t,'');
  const item={id:'test-file',name:'report.docx',kind:'document',size:120,mime:'application/octet-stream'};
  await page.route('**/api/attachments?*',route=>route.fulfill({json:item}));
  await page.route('**/api/chat/stream',route=>route.fulfill({status:400,json:{detail:'Image model required'}}));
  await page.locator('#attachmentInput').setInputFiles({name:'report.docx',mimeType:'application/octet-stream',buffer:Buffer.from('fixture')});
  await page.waitForFunction(()=>document.querySelector('#attachmentTray').textContent.includes('report.docx'));
  assert.equal(await page.locator('#stopButton').count(),0);
  await page.locator('#prompt').fill('Review this document');
  await page.locator('#sendButton').click();
  await page.waitForFunction(()=>document.querySelector('#composerStatus').textContent.includes('Image model required'));
  assert.equal(await page.locator('#prompt').inputValue(),'Review this document');
  assert.match(await page.locator('#attachmentTray').textContent(),/report.docx/);
  assert.equal(await page.locator('#sendButton').getAttribute('aria-label'),'发送');
  for(const width of [1366,960,390]) {
    await page.setViewportSize({width,height:768});
    const layout=await page.locator('.composer-inner').evaluate(el=>({client:el.clientWidth,scroll:el.scrollWidth}));
    assert.ok(layout.scroll<=layout.client,JSON.stringify(layout));
    const button=await page.locator('#sendButton').boundingBox();
    assert.ok(button.x>=0 && button.x+button.width<=width,JSON.stringify(button));
    const conversation=await page.locator('.conversation').boundingBox();
    assert.ok(conversation.width>Math.min(width-35,600),JSON.stringify(conversation));
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    await page.screenshot({path:path.resolve(__dirname,`../../.tmp/v13-composer-${width}.png`)});
  }
  assert.deepEqual(errors,[]);
});

test('send control becomes stop during a request without Enter cancelling it', async t => {
  const {page, errors}=await pageFor(t,'');
  let release, cancelled=0;
  const gate=new Promise(resolve=>{release=resolve;});
  t.after(()=>release());
  await page.route('**/api/chat/stream',async route=>{
    const {request_id}=route.request().postDataJSON();
    await gate;
    await route.fulfill({contentType:'text/event-stream',body:`event: done\ndata: ${JSON.stringify({request_id,sequence:1,data:{finish_reason:'cancelled'}})}\n\n`});
  });
  await page.route('**/api/chat/*/cancel',route=>{cancelled++;release();return route.fulfill({json:{cancelled:true}});});
  await page.locator('#prompt').fill('Run task');
  await page.locator('#sendButton').click();
  await page.waitForFunction(()=>document.querySelector('#sendButton').getAttribute('aria-label')==='停止生成');
  await page.locator('#prompt').press('Enter');
  assert.equal(cancelled,0);
  await page.locator('#sendButton').click();
  await page.waitForFunction(()=>document.querySelector('#sendButton').getAttribute('aria-label')==='发送');
  assert.equal(cancelled,1);
  assert.deepEqual(errors,[]);
});

test('polished composer shows Office drafts and safe document preview at desktop and narrow sizes', async t => {
  const {page, errors}=await pageFor(t,'## 材料核对\n\n已整理文档与工作表中的信息。\n\n| 材料 | 核对内容 |\n| --- | --- |\n| 项目说明 | 目标、约束与待确认项 |\n| 预算表 | 工作表数据与公式文本 |\n\n下一步可以逐项检查差异，并保留原始材料作为依据。');
  await page.route('**/api/attachments?*',route=>{const name=new URL(route.request().url()).searchParams.get('filename');return route.fulfill({json:{id:name,name,kind:'document',size:4096,mime:'application/octet-stream'}});});
  await page.route('**/api/attachments/project.docx',route=>route.fulfill({json:{id:'project.docx',name:'project.docx',kind:'document',text:'Project evidence <script>window.__executed=1</script>'}}));
  await page.locator('#attachmentInput').setInputFiles(['project.docx','budget.xlsx'].map(name=>({name,mimeType:'application/octet-stream',buffer:Buffer.from('fixture')})));
  await page.waitForFunction(()=>document.querySelector('#attachmentTray').textContent.includes('budget.xlsx') && !document.querySelector('#sendButton').disabled);
  await page.locator('#prompt').fill('对照项目说明和预算表，帮我核对下一阶段的工作。');
  for(const width of [1366,390]) {
    await page.setViewportSize({width,height:768});
    const button=await page.locator('#sendButton').boundingBox();
    assert.ok(button.x>=0 && button.x+button.width<=width);
    await page.screenshot({path:path.resolve(__dirname,`../../.tmp/v13-polished-${width}.png`)});
  }
  await page.locator('#attachmentTray .attachment-open').first().click();
  await page.waitForFunction(()=>document.querySelector('#attachmentPreviewBody').textContent.includes('Project evidence'));
  assert.equal(await page.locator('#attachmentPreviewBody script').count(),0);
  assert.equal(await page.evaluate(()=>window.__executed),0);
  await page.locator('#closeAttachmentPreview').click();
  await page.evaluate(()=>{
    const data=new DataTransfer();data.items.add(new File(['Pasted evidence'],'paste.txt',{type:'text/plain'}));
    document.querySelector('#prompt').dispatchEvent(new ClipboardEvent('paste',{clipboardData:data,bubbles:true}));
  });
  await page.waitForFunction(()=>document.querySelector('#attachmentTray').textContent.includes('paste.txt') && !document.querySelector('#sendButton').disabled);
  await page.evaluate(()=>{
    const data=new DataTransfer();data.items.add(new File(['Dropped evidence'],'drop.txt',{type:'text/plain'}));
    document.dispatchEvent(new DragEvent('drop',{dataTransfer:data,bubbles:true,cancelable:true}));
  });
  await page.waitForFunction(()=>document.querySelector('#attachmentTray').textContent.includes('drop.txt') && !document.querySelector('#sendButton').disabled);
  assert.deepEqual(errors,[]);
});

test('Mobius surface is framed, moving and suspended with desktop visibility and settings', async t => {
  const {page,errors}=await pageFor(t,'');
  const canvas=page.locator('.mobius-stage canvas');
  await canvas.waitFor();
  await page.waitForFunction(()=>document.querySelector('.mobius-stage canvas')?.dataset.animating==='true');
  for(const viewport of [{width:1366,height:900},{width:960,height:600},{width:390,height:720}]) {
    await page.setViewportSize(viewport);
    await page.waitForTimeout(180);
    const pixels=await canvas.evaluate(el=>{
      const gl=el.getContext('webgl2'),data=new Uint8Array(el.width*el.height*4);gl.readPixels(0,0,el.width,el.height,gl.RGBA,gl.UNSIGNED_BYTE,data);
      let count=0,minX=el.width,maxX=0,minY=el.height,maxY=0;const hues=new Set();
      for(let i=0;i<data.length;i+=4){if(data[i+3]<200)continue;const r=data[i],g=data[i+1],b=data[i+2];if(Math.max(r,g,b)-Math.min(r,g,b)>40)hues.add(r>g&&r>b?'warm':g>r&&g>b?'green':'blue');}
      for(let y=0;y<el.height;y++)for(let x=0;x<el.width;x++)if(data[(y*el.width+x)*4+3]>30){count++;minX=Math.min(minX,x);maxX=Math.max(maxX,x);minY=Math.min(minY,y);maxY=Math.max(maxY,y);}
      return {count,minX,maxX,minY,maxY,w:el.width,h:el.height,hues:[...hues]};
    });
    assert.ok(pixels.count>1500,JSON.stringify(pixels));
    assert.equal(pixels.hues.length,3,JSON.stringify(pixels));
    assert.ok(pixels.minX>2 && pixels.maxX<pixels.w-3 && pixels.minY>2 && pixels.maxY<pixels.h-3,JSON.stringify(pixels));
    const stage=await page.locator('.mobius-stage').boundingBox(),title=await page.locator('.empty-state strong').boundingBox();
    assert.ok(stage.x>=0 && stage.x+stage.width<=viewport.width && stage.y+stage.height<=title.y);
    await page.screenshot({path:path.resolve(__dirname,`../../.tmp/v19-mobius-${viewport.width}.png`)});
  }
  const before=await canvas.screenshot();await page.waitForTimeout(350);
  assert.notDeepEqual(before,await canvas.screenshot());
  await page.evaluate(()=>{window.__awbNativeVisible=false;window.dispatchEvent(new Event('awb-visibility'));});
  assert.equal(await canvas.getAttribute('data-animating'),'false');
  const still=await canvas.screenshot();await page.waitForTimeout(250);assert.deepEqual(still,await canvas.screenshot());
  await page.evaluate(()=>{window.__awbNativeVisible=true;window.dispatchEvent(new Event('awb-visibility'));});
  assert.equal(await canvas.getAttribute('data-animating'),'true');
  await page.locator('#openPaperLibrary').evaluate(el=>el.click());
  await page.locator('#paperLibrary').waitFor({state:'visible'});
  assert.equal(await canvas.getAttribute('data-animating'),'false');
  await page.locator('#closePaperLibrary').click();
  await page.waitForFunction(()=>document.querySelector('.mobius-stage canvas').dataset.animating==='true');
  await page.emulateMedia({reducedMotion:'reduce'});
  await page.waitForFunction(()=>document.querySelector('.mobius-stage canvas').dataset.animating==='false');
  await page.emulateMedia({reducedMotion:'no-preference'});
  await page.locator('#settingsButton').click();
  assert.equal(await canvas.getAttribute('data-animating'),'false');
  await page.route('**/api/settings',route=>route.fulfill({json:route.request().method()==='PUT'?route.request().postDataJSON():{provider_mode:'mock'}}));
  assert.equal(await page.locator('#dynamicBackground').count(),0);await page.locator('#saveSettings').click();
  await page.locator('#settingsDialog').waitFor({state:'hidden'});
  await page.waitForFunction(()=>document.querySelector('.mobius-stage canvas').dataset.animating==='true');
  assert.equal(await canvas.isVisible(),true);
  assert.deepEqual(errors,[]);
});

test('Mobius WebGL failure retains the bitmap identity without blocking chat', async t => {
  const {page,errors}=await pageFor(t,'');
  await page.addInitScript(()=>{const original=HTMLCanvasElement.prototype.getContext;HTMLCanvasElement.prototype.getContext=function(type,...args){return type.includes('webgl')?null:original.call(this,type,...args);};});
  await page.reload();await page.locator('.empty-mark').waitFor({state:'visible'});
  await page.waitForTimeout(250);
  assert.equal(await page.locator('.mobius-stage canvas').count(),0);
  assert.equal(await page.locator('#prompt').isEnabled(),true);
  assert.deepEqual(errors,[]);
});

test('unframed workspace keeps one header and ambient motion outside readable content', async t => {
  const {page,errors}=await pageFor(t,'## 项目进展\n\n已完成材料核对，接下来整理实现细节。\n\n- 保留完整的原始记录\n- 核对模型配置与附件\n- 完成后汇总变更与验证结果');
  await page.route('**/api/sessions',route=>route.fulfill({json:{active:'design',sessions:[{session_id:'design',title:'桌面工作台设计'}]}}));
  await page.reload();
  await page.locator('.message.assistant').waitFor();
  await page.locator('#prompt').fill('继续检查这次调整，保留当前的工具和工作流。');
  for(const width of [1920,1366,960,390]) {
    await page.setViewportSize({width,height:900});
    const metrics=await page.evaluate(()=>({
      sidebar:getComputedStyle(document.querySelector('.sidebar')).borderRightWidth,
      header:getComputedStyle(document.querySelector('.topbar')).borderBottomWidth,
      prompt:getComputedStyle(document.querySelector('#prompt')).outlineStyle,
      message:getComputedStyle(document.querySelector('.message.user')).borderTopWidth,
      overflow:document.documentElement.scrollWidth>innerWidth,
      targets:['#sendButton','#settingsButton','#contextStatus','#conversationMenu'].map(s=>{const r=document.querySelector(s).getBoundingClientRect();return {x:r.x,right:r.right,width:r.width};}),
    }));
    assert.equal(metrics.sidebar,'0px');assert.equal(metrics.header,'0px');assert.equal(metrics.prompt,'none');assert.equal(metrics.message,'0px');assert.equal(metrics.overflow,false);
    assert.ok(metrics.targets.every(r=>r.x>=0 && r.right<=width && r.width>=20),JSON.stringify(metrics));
    await page.screenshot({path:path.resolve(__dirname,`../../.tmp/v14-workspace-${width}.png`)});
  }
  await page.setViewportSize({width:1920,height:900});
  assert.equal(await page.locator('.ambient-plane,.ambient-scan').count(),0);
  let saved;
  await page.route('**/api/settings',route=>{if(route.request().method()==='PUT'){saved=route.request().postDataJSON();return route.fulfill({json:saved});}return route.fulfill({json:{provider_mode:'mock',dynamic_background:true,guidance:guidanceDefaults}});});
  await page.locator('#settingsButton').click();await page.locator('#saveSettings').click();
  await page.locator('#settingsDialog').waitFor({state:'hidden'});
  assert.equal(saved.dynamic_background,false);
  assert.deepEqual(errors,[]);
});

test('paper tree renders genuine 3D, rotates, filters and opens scoped paper details', async t => {
  const {page,errors,external}=await pageFor(t,'');
  page.setDefaultTimeout(15000);
  let papers=Array.from({length:12},(_,i)=>({paper_id:`paper-${i}`,filename:`fixture-${i}.pdf`,status:'ready',page_count:2,revision:0,
    classification_status:'ready',topic_slot:i%3,manual_fields:[],metadata:{title:['检索增强生成的可靠性研究','基于特征重建的异常检测','视觉语言模型的对齐方法'][i%3]+` ${i+1}`,year:2021+i%5,venue:['示例会议 A','示例期刊 B','预印本'][i%3],topic:['知识检索','异常检测','多模态学习'][i%3],reason:'仅供界面验证的合成数据',evidence:{}}}));
  await page.route('**/api/kb/papers',route=>route.fulfill({json:{papers}}));
  await page.route(/\/api\/kb\/papers\/paper-\d+$/,route=>route.fulfill({json:papers.find(p=>route.request().url().endsWith(p.paper_id))}));
  await page.locator('[data-tab="knowledge"]').click();await page.locator('#openPaperLibrary').click();
  await page.waitForFunction(()=>document.querySelector('#treeViewport canvas')?.dataset.rendered==='true');
  assert.equal(await page.locator('#paperTable').isVisible(),false);
  for(const width of [1920,1366,960,390]){
    await page.setViewportSize({width,height:800});await page.waitForTimeout(120);
    await page.locator('#treeReset').click();await page.waitForTimeout(120);
    const metrics=await page.locator('#treeViewport canvas').evaluate(canvas=>{
      const gl=canvas.getContext('webgl2');const pixels=new Uint8Array(gl.drawingBufferWidth*gl.drawingBufferHeight*4);gl.readPixels(0,0,gl.drawingBufferWidth,gl.drawingBufferHeight,gl.RGBA,gl.UNSIGNED_BYTE,pixels);
      let colored=0;for(let i=0;i<pixels.length;i+=4)if(pixels[i]<210||pixels[i+1]<210||pixels[i+2]<210)colored++;
      return {width:canvas.clientWidth,height:canvas.clientHeight,colored};
    });
    assert.ok(metrics.width>300&&metrics.height>250&&metrics.colored>100,JSON.stringify(metrics));
    assert.ok((await page.locator('#paperTopicFilter').boundingBox()).width>=95);
    assert.ok((await page.locator('#paperYearFilter').boundingBox()).width>=75);
    const boxes=await page.locator('.tree-paper:visible').evaluateAll(nodes=>nodes.map(n=>{const r=n.getBoundingClientRect();return {x:r.x,y:r.y,right:r.right,bottom:r.bottom};}));
    assert.ok(boxes.length>0);
    for(let i=0;i<boxes.length;i++)for(let j=i+1;j<boxes.length;j++){const a=boxes[i],b=boxes[j];assert.ok(!(a.x<b.right&&a.right>b.x&&a.y<b.bottom&&a.bottom>b.y),'paper labels overlap');}
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    await page.screenshot({path:path.resolve(__dirname,`../../.tmp/v15-tree-${width}.png`)});
  }
  await page.setViewportSize({width:1366,height:800});await page.locator('#treeReset').click();
  const before=await page.locator('#treeViewport canvas').evaluate(c=>c.toDataURL());
  const box=await page.locator('#treeViewport').boundingBox();
  await page.mouse.move(box.x+30,box.y+box.height/2);await page.mouse.down();await page.mouse.move(box.x+180,box.y+box.height/2+35,{steps:10});await page.mouse.up();await page.waitForTimeout(150);
  assert.notEqual(await page.locator('#treeViewport canvas').evaluate(c=>c.toDataURL()),before);
  await page.locator('#paperTopicFilter').selectOption('知识检索');
  await page.waitForFunction(()=>document.querySelector('#libraryCount').textContent==='4 / 12 篇');
  await page.locator('#listMode').click();assert.equal(await page.locator('#paperTableBody tr').count(),4);
  await page.locator('#paperTableBody button').first().click();await page.locator('#paperDetailTitle').waitFor();
  assert.match(await page.locator('#paperDetailTitle').textContent(),/检索增强/);
  await page.screenshot({path:path.resolve(__dirname,'../../.tmp/v15-detail.png')});
  await page.locator('#askPaper').click();assert.equal(await page.locator('#paperScope').isVisible(),true);
  assert.equal(await page.locator('#paperLibrary').isVisible(),false);
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
});

test('unknown-year papers are not assigned a fabricated timeline year', async t => {
  const {page,errors}=await pageFor(t,'');
  await page.route('**/api/kb/papers',route=>route.fulfill({json:{papers:[{paper_id:'unknown',filename:'Undated paper.pdf',status:'ready',metadata:{},revision:0}]}}));
  await page.locator('[data-tab="knowledge"]').click();await page.locator('#openPaperLibrary').click();
  await page.waitForFunction(()=>document.querySelector('#treeViewport canvas')?.dataset.rendered==='true');
  assert.deepEqual(await page.locator('.tree-annotation.year').allTextContents(),['年份待确认']);
  assert.match(await page.locator('.tree-paper').textContent(),/年份待确认.*出处待确认/);
  assert.deepEqual(errors,[]);
});

test('paper library falls back to a working list when WebGL is unavailable', async t => {
  const {page,errors}=await pageFor(t,'');
  await page.addInitScript(()=>{const original=HTMLCanvasElement.prototype.getContext;HTMLCanvasElement.prototype.getContext=function(type,...args){return type.startsWith('webgl')?null:original.call(this,type,...args);};});
  await page.route('**/api/kb/papers',route=>route.fulfill({json:{papers:[{paper_id:'fallback',filename:'Fallback paper.pdf',status:'ready',metadata:{year:2024},revision:0}]}}));
  await page.reload();await page.locator('[data-tab="knowledge"]').click();await page.locator('#openPaperLibrary').click();
  await page.waitForFunction(()=>!document.querySelector('#paperTable').hidden);
  assert.equal(await page.locator('#paperTableBody tr').count(),1);
  assert.match(await page.locator('#libraryStatus').textContent(),/已切换列表/);
  assert.deepEqual(errors,[]);
});
