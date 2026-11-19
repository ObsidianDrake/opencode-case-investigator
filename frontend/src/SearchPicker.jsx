import { useEffect, useMemo, useRef, useState } from 'react';

const pageSize = 20;

export default function SearchPicker({ name, label, prompt, placeholder, emptyMessage, value, onChange,
  search, options, required = false, disabled = false, validationError }) {
  const trigger = useRef(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(1);
  const [remoteResult, setRemoteResult] = useState(null);
  const [error, setError] = useState('');

  const matches = useMemo(() => {
    if (!open || !options) return null;
    const term = query.trim().toLowerCase();
    return options.filter(item => item.toLowerCase().includes(term));
  }, [options, open, query]);
  const start = (page - 1) * pageSize;
  const result = matches
    ? { names: matches.slice(start, start + pageSize), has_more: matches.length > start + pageSize }
    : remoteResult;

  useEffect(() => {
    if (!open || !search) return;
    let active = true;
    const timer = setTimeout(async () => {
      try {
        const data = await search(query.trim(), page);
        if (active) setRemoteResult(data);
      } catch (err) {
        if (active) setError(err.message);
      }
    }, 250);
    return () => { active = false; clearTimeout(timer); };
  }, [open, query, page, search]);

  function browse(text, nextPage) {
    setQuery(text);
    setPage(nextPage);
    setRemoteResult(null);
    setError('');
    setOpen(true);
  }

  function choose(item) {
    onChange(item);
    setOpen(false);
    trigger.current.focus();
  }

  return <fieldset className="search-picker" disabled={disabled} onKeyDown={event => {
    if (open && event.key === 'Escape' && !event.nativeEvent.isComposing) {
      event.preventDefault();
      setOpen(false);
      trigger.current.focus();
    }
  }}>
    <legend>{label}（{required ? '必填' : '選填'}）</legend>
    <input type="hidden" name={name} value={value} />
    <div className="picker-actions">
      <button type="button" ref={trigger} id={name + '-trigger'} aria-expanded={open}
        aria-invalid={validationError ? true : undefined} aria-describedby={validationError ? name + '-error' : undefined}
        onClick={() => open ? setOpen(false) : browse('', 1)}>
        {value || prompt}</button>
      {value && <button type="button" onClick={() => choose('')}>清除選擇</button>}
    </div>
    {validationError && <p id={name + '-error'} role="alert">{validationError}</p>}
    {open && <div className="picker-browser">
      <input type="search" autoFocus aria-label={'搜尋 ' + label} placeholder={placeholder}
        value={query} onChange={event => browse(event.target.value, 1)} onKeyDown={event => {
          if (event.key === 'Enter' && !event.nativeEvent.isComposing) {
            event.preventDefault();
            const names = matches || result?.names || [];
            const typed = query.trim();
            const exact = names.find(item => item === typed);
            const sameName = names.filter(item => item.toLowerCase() === typed.toLowerCase());
            if (exact) choose(exact);
            else if (sameName.length === 1) choose(sameName[0]);
            else if (page === 1 && result?.names.length === 1 && !result.has_more) choose(result.names[0]);
          }
        }} />
      <div className="picker-results">{result?.names.map(item =>
        <button key={item} type="button" onClick={() => choose(item)}>{item}</button>)}</div>
      {error ? <p role="alert">{error}</p> : !result ? <p role="status">搜尋中…</p>
        : !result.names.length && <p role="status">{emptyMessage}</p>}
      <div className="picker-actions">
        {page > 1 && <button type="button" onClick={() => browse(query, page - 1)}>上一頁</button>}
        {result?.has_more && <button type="button" onClick={() => browse(query, page + 1)}>下一頁</button>}
      </div>
    </div>}
  </fieldset>;
}
