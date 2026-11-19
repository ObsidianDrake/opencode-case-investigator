import { useEffect, useState } from 'react';

export default function RepositoryPicker({ value, onChange, searchRepos }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(1);
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!open) return;
    let active = true;
    const timer = setTimeout(async () => {
      try {
        const data = await searchRepos(query.trim(), page);
        if (active) setResult(data);
      } catch (err) {
        if (active) setError(err.message);
      }
    }, 250);
    return () => { active = false; clearTimeout(timer); };
  }, [open, query, page, searchRepos]);

  function browse(text, nextPage) {
    setQuery(text);
    setPage(nextPage);
    setResult(null);
    setError('');
    setOpen(true);
  }

  return <fieldset className="repo-picker">
    <legend>Gitea repository（選填）</legend>
    <input type="hidden" name="repo" value={value} />
    <div className="repo-actions">
      <button type="button" aria-expanded={open} onClick={() => open ? setOpen(false) : browse('', 1)}>
        {value || '選擇 repository'}</button>
      {value && <button type="button" onClick={() => { onChange(''); setOpen(false); }}>清除選擇</button>}
    </div>
    {open && <div className="repo-browser">
      <input type="search" autoFocus aria-label="搜尋 Gitea repository" placeholder="輸入 repository 名稱搜尋"
        value={query} onChange={event => browse(event.target.value, 1)} />
      <div className="repo-results">{result?.names.map(name =>
        <button key={name} type="button" onClick={() => { onChange(name); setOpen(false); }}>{name}</button>)}</div>
      {error ? <p role="alert">{error}</p> : !result ? <p role="status">搜尋中…</p>
        : !result.names.length && <p role="status">沒有符合的 repository</p>}
      <div className="repo-actions">
        {page > 1 && <button type="button" onClick={() => browse(query, page - 1)}>上一頁</button>}
        {result?.has_more && <button type="button" onClick={() => browse(query, page + 1)}>下一頁</button>}
      </div>
    </div>}
  </fieldset>;
}
