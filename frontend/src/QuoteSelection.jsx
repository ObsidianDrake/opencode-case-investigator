import { useEffect, useState } from 'react';

export default function QuoteSelection({ log, onAdd }) {
  const [quote, setQuote] = useState(null);
  useEffect(() => {
    function update() {
      const selection = window.getSelection();
      if (!selection || selection.isCollapsed || selection.rangeCount !== 1) {
        setQuote(null);
        return;
      }
      const range = selection.getRangeAt(0);
      const start = range.startContainer;
      const element = start.nodeType === Node.ELEMENT_NODE ? start : start.parentElement;
      const body = element?.closest('.message.agent .message-body');
      if (!body || !log.current?.contains(body) || !body.contains(range.endContainer)) {
        setQuote(null);
        return;
      }
      const text = selection.toString();
      const rect = range.getBoundingClientRect();
      const bounds = log.current.getBoundingClientRect();
      const top = Math.max(rect.top, bounds.top, 0);
      const bottom = Math.min(rect.bottom, bounds.bottom, window.innerHeight);
      if (!text.trim() || bottom <= top) {
        setQuote(null);
        return;
      }
      setQuote({
        text,
        top: top >= 42 ? top - 38 : Math.min(bottom + 4, window.innerHeight - 38),
        left: Math.max(4, Math.min(rect.left, window.innerWidth - 116))
      });
    }
    function dismiss(event) {
      if (event.key === 'Escape') setQuote(null);
    }
    document.addEventListener('selectionchange', update);
    document.addEventListener('scroll', update, true);
    window.addEventListener('resize', update);
    document.addEventListener('keydown', dismiss);
    return () => {
      document.removeEventListener('selectionchange', update);
      document.removeEventListener('scroll', update, true);
      window.removeEventListener('resize', update);
      document.removeEventListener('keydown', dismiss);
    };
  }, [log]);

  return quote && <button type="button" className="quote-selection" style={{ top: quote.top, left: quote.left }}
    onPointerDown={event => event.preventDefault()}
    onClick={() => { if (onAdd(quote.text)) setQuote(null); }}>+ Add to chat</button>;
}
