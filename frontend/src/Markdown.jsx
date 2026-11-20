import { memo, useMemo } from 'react';
import MarkdownIt from 'markdown-it';

const parser = new MarkdownIt({ html: false, breaks: true });
parser.renderer.rules.image = (tokens, index) => parser.utils.escapeHtml(tokens[index].content);
const validateLink = parser.validateLink;
parser.validateLink = url => /^https?:\/\//i.test(url) && validateLink(url);

const renderFence = parser.renderer.rules.fence;
parser.renderer.rules.fence = (tokens, index, ...args) => {
  const block = renderFence(tokens, index, ...args);
  if (tokens[index].info.trim().split(/\s+/)[0] !== 'bash') return block;
  return `<div class="bash-block"><button type="button" class="copy-bash" title="複製 Bash 指令" aria-label="複製 Bash 指令">
    <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <g class="copy-shape"><rect x="8" y="8" width="12" height="13" rx="2"/><path d="M15 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h3"/></g>
      <path class="check-shape" d="m4 12 5 5L20 6"/>
    </svg></button><span class="copy-status" role="status"></span>${block}</div>\n`;
};

function showCopyResult(button, copied) {
  button.dataset.result = copied ? 'success' : 'manual';
  button.nextElementSibling.textContent = copied ? '已複製' : '請手動複製';
  clearTimeout(button.copyTimer);
  button.copyTimer = setTimeout(() => {
    delete button.dataset.result;
    button.nextElementSibling.textContent = '';
  }, 2400);
}

async function copyBash(event) {
  const button = event.target.closest('.copy-bash');
  if (!button) return;
  const code = button.parentElement.querySelector('pre code');
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(code.textContent);
      showCopyResult(button, true);
      return;
    } catch {
      // Restricted clipboard access can still allow copying a selection.
    }
  }
  const selection = window.getSelection();
  selection.selectAllChildren(code);
  try {
    if (document.execCommand('copy')) {
      selection.removeAllRanges();
      showCopyResult(button, true);
      return;
    }
  } catch {
    // Leave the commands selected for manual copying.
  }
  showCopyResult(button, false);
}

export default memo(function Markdown({ text }) {
  const markup = useMemo(() => ({ __html: parser.render(text) }), [text]);
  return <div className="markdown" onClick={copyBash} dangerouslySetInnerHTML={markup} />;
});
