export async function copyText(text) {
  if(window.pywebview?.api?.copy_text){try{if(await window.pywebview.api.copy_text(text))return;}catch{ /* Try the browser clipboard if native access is unavailable. */ }}
  if(navigator.clipboard?.writeText){try{await navigator.clipboard.writeText(text);return;}catch{ /* Older or restricted WebViews may only support selection-based copying. */ }}
  const previous=document.activeElement,selection=getSelection();
  const ranges=selection?Array.from({length:selection.rangeCount},(_,i)=>selection.getRangeAt(i).cloneRange()):[];
  const input=document.createElement('textarea');input.value=text;input.readOnly=true;input.style.cssText='position:fixed;left:-10000px;top:0;';document.body.append(input);
  let copied=false;
  try{input.select();copied=document.execCommand('copy');}
  finally{input.remove();previous?.focus({preventScroll:true});if(selection){selection.removeAllRanges();for(const range of ranges)selection.addRange(range);}}
  if(!copied)throw new Error('无法访问剪贴板，请选中文字后复制');
}

export function copyButton(text, className, label) {
  const button=document.createElement('button');button.type='button';button.className=`icon-button small ${className}`;
  function show(title,icon){button.title=title;button.setAttribute('aria-label',title);const svg=window.lucide.createElement(window.lucide.icons[icon]);svg.setAttribute('aria-hidden','true');button.replaceChildren(svg);}
  show(label,'Copy');
  button.onclick=async event=>{event.stopPropagation();button.disabled=true;try{await copyText(text);show('已复制','Check');}catch(error){show(error.message,'AlertCircle');}finally{button.disabled=false;}setTimeout(()=>{if(button.isConnected)show(label,'Copy');},2000);};
  return button;
}
