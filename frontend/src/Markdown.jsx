import { memo, useMemo } from 'react';
import MarkdownIt from 'markdown-it';

const parser = new MarkdownIt({ html: false, breaks: true });
parser.renderer.rules.image = (tokens, index) => parser.utils.escapeHtml(tokens[index].content);
const validateLink = parser.validateLink;
parser.validateLink = url => /^https?:\/\//i.test(url) && validateLink(url);

export default memo(function Markdown({ text }) {
  const markup = useMemo(() => ({ __html: parser.render(text) }), [text]);
  return <div className="markdown" dangerouslySetInnerHTML={markup} />;
});
