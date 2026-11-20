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
  return '<div class="bash-block"><button type="button" class="copy-bash" aria-live="polite">複製</button>' + block + '</div>\n';
};

async function copyBash(event) {
  const button = event.target.closest('.copy-bash');
  if (!button) return;
  const code = button.parentElement.querySelector('pre code');
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(code.textContent);
      button.textContent = '已複製';
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
      button.textContent = '已複製';
      return;
    }
  } catch {
    // Leave the commands selected for manual copying.
  }
  button.textContent = '請手動複製';
}

export default memo(function Markdown({ text }) {
  const markup = useMemo(() => ({ __html: parser.render(text) }), [text]);
  return <div className="markdown" onClick={copyBash} dangerouslySetInnerHTML={markup} />;
});
