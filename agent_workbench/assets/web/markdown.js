import { copyButton } from './clipboard.js';
const parser = window.markdownit({ html: false, linkify: false, typographer: false, maxNesting: 20 });

function webLink(value) {
  if (typeof value !== "string" || value.length > 8192 || /[\s\x00-\x1f\x7f\\]/u.test(value)) return null;
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) && url.hostname && !url.username && !url.password ? url.href : null;
  } catch {
    return null;
  }
}

parser.validateLink = (url) => webLink(url) !== null;
// Untrusted responses must not issue passive image requests or load local files.
parser.renderer.rules.image = (tokens, index) => `<span class="markdown-image-alt">${parser.utils.escapeHtml(tokens[index].content)}</span>`;

async function openLink(event) {
  const link = event.target.closest("a");
  if (!link || !event.currentTarget.contains(link)) return;
  event.preventDefault();
  if (event.type === "auxclick" && event.button !== 1) return;
  const url = webLink(link.getAttribute("href"));
  if (!url) return;
  try {
    if (window.pywebview?.api) {
      const opened = await window.pywebview.api.open_external_url(url);
      if (!opened) throw new Error("Browser did not open");
    } else {
      window.open(url, "_blank", "noopener,noreferrer");
    }
  } catch {
    link.title = "Unable to open link";
    const status = document.createElement("span");
    status.className = "markdown-link-error";
    status.setAttribute("role", "status");
    status.textContent = " (Unable to open link)";
    link.nextElementSibling?.classList.contains("markdown-link-error") || link.after(status);
  }
}

export function renderMarkdown(target, source) {
  const fragment = window.DOMPurify.sanitize(parser.render(source), {
    ALLOWED_TAGS: ["p", "h1", "h2", "h3", "h4", "h5", "h6", "a", "em", "strong", "s",
      "ul", "ol", "li", "blockquote", "pre", "code", "hr", "br", "table", "thead", "tbody", "tr", "th", "td", "span"],
    ALLOWED_ATTR: ["href", "title", "class", "start"],
    ALLOW_DATA_ATTR: false,
    ALLOW_ARIA_ATTR: false,
    RETURN_DOM_FRAGMENT: true,
  });
  for (const link of fragment.querySelectorAll("a")) {
    const url = webLink(link.getAttribute("href"));
    if (!url) {
      link.replaceWith(...link.childNodes);
      continue;
    }
    link.href = url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.title = url;
  }
  for (const table of fragment.querySelectorAll("table")) {
    const wrapper = document.createElement("div");
    wrapper.className = "markdown-table";
    wrapper.tabIndex = 0;
    wrapper.setAttribute("role", "region");
    wrapper.setAttribute("aria-label", "Table");
    table.replaceWith(wrapper);
    wrapper.append(table);
  }
  const content=document.createElement('div');content.className='markdown-content';content.append(fragment);
  target.replaceChildren(content);
  if(target.classList.contains('assistant')){
    for(const code of content.querySelectorAll('pre > code')){
      const wrapper=document.createElement('div');wrapper.className='code-block';
      const pre=code.parentElement;pre.replaceWith(wrapper);wrapper.append(copyButton(code.textContent,'copy-code','复制代码'),pre);
    }
    const tools=document.createElement('div');tools.className='message-tools';
    tools.append(copyButton(source,'copy-answer','复制回复'));target.append(tools);
  }
  target.onclick = openLink;
  target.onauxclick = openLink;
}
