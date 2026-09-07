let active = false;

// Reject overlapping requests rather than queueing stale destructive actions.
function ask(message, {title = '确认操作', action = '继续', value = null} = {}) {
  if (active) return Promise.resolve(value === null ? false : null);
  active = true;
  const previous = document.activeElement;
  const dialog = document.createElement('dialog');
  dialog.id = 'actionDialog';
  dialog.className = 'action-dialog';
  dialog.setAttribute('aria-labelledby', 'actionTitle');
  dialog.setAttribute('aria-describedby', 'actionDescription');
  dialog.innerHTML = '<form method="dialog"><header><span class="action-symbol"></span><h2 id="actionTitle"></h2><button type="button" class="icon-button" id="actionClose" aria-label="关闭" title="关闭"></button></header><p id="actionDescription"></p><input id="actionInput" aria-label="输入内容"><footer><button value="cancel" id="actionCancel" autofocus>取消</button><button value="accept" id="actionAccept" class="primary"></button></footer></form>';
  dialog.querySelector('#actionTitle').textContent = title;
  dialog.querySelector('#actionDescription').textContent = message;
  dialog.querySelector('#actionAccept').textContent = action;
  dialog.querySelector('.action-symbol').append(window.lucide.createElement(window.lucide.icons.CircleHelp));
  dialog.querySelector('#actionClose').append(window.lucide.createElement(window.lucide.icons.X));
  const input = dialog.querySelector('#actionInput');
  input.hidden = value === null;
  input.value = value ?? '';
  if (/删除|清空/.test(action)) dialog.querySelector('#actionAccept').classList.add('destructive');
  document.body.append(dialog);
  return new Promise(resolve => {
    dialog.querySelector('#actionClose').onclick = () => dialog.close('cancel');
    dialog.addEventListener('close', () => {
      const result = dialog.returnValue === 'accept' ? (value === null ? true : input.value.trim()) : (value === null ? false : null);
      dialog.remove(); active = false;
      if (previous?.isConnected) previous.focus();
      resolve(result);
    }, {once:true});
    dialog.showModal();
    if (value !== null) { input.focus(); input.select(); }
  });
}

export function confirmAction(message) {
  const action = /是否启用/.test(message) ? '启用' : /诊断/.test(message) ? '开始诊断' : /删除/.test(message.slice(0,12)) ? '删除' : /清空/.test(message) ? '清空记录' : /自动修改/.test(message) ? '允许自动编辑' : /复制到/.test(message) ? '迁移数据' : /移动到/.test(message) ? '移动论文' : /分类/.test(message) ? '开始分类' : '修复索引';
  const title = action === '启用' ? '启用自动论文分类' : action;
  return ask(message, {title, action});
}
export function promptAction(message, value = '') { return ask(message, {title:'编辑内容', action:'确定', value}); }
