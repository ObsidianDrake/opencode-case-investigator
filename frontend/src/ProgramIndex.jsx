import { useEffect, useRef, useState } from 'react';

import SearchPicker from './SearchPicker';

export default function ProgramIndex({ programs, remove, ...formProps }) {
  const [editor, setEditor] = useState(null);
  return <>
    <div className="program-toolbar"><button className="primary" disabled={formProps.busy}
      onClick={() => setEditor({})}>新增程式</button></div>
    <div id="programs">{programs.length === 0 && <p>尚未建立程式。</p>}
      {programs.map(p => <div key={p.name}>
        <span className="mapping-name">{p.name}{p.repo && ' → ' + p.repo}</span>
        <button disabled={formProps.busy} onClick={() => setEditor(p)}>編輯</button>
        <button disabled={formProps.busy} onClick={() => {
          if (confirm('刪除 ' + p.name + ' 的程式對應？')) remove(p.name);
        }}>刪除</button>
      </div>)}
    </div>
    {editor && <ProgramDialog editing={editor} {...formProps} close={() => setEditor(null)} />}
  </>;
}

function ProgramDialog({ editing, busy, save, status, close, loadSkills, searchRepos }) {
  const dialog = useRef(null);
  const [repo, setRepo] = useState(editing.repo || '');
  const [saveFailed, setSaveFailed] = useState(false);
  const [catalog, setCatalog] = useState([]);
  const [error, setError] = useState('');
  const [selected, setSelected] = useState(editing.skills || []);
  const [query, setQuery] = useState('');
  useEffect(() => { dialog.current.showModal(); }, []);
  useEffect(() => {
    let active = true;
    loadSkills().then(names => { if (active) setCatalog(names); })
      .catch(err => { if (active) setError(err.message); });
    return () => { active = false; };
  }, [loadSkills]);

  async function submit(event) {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(event.currentTarget));
    values.skills = selected;
    setSaveFailed(false);
    if (await save(editing.name, values)) close();
    else setSaveFailed(true);
  }

  return <dialog className="program-dialog" ref={dialog} aria-labelledby="program-dialog-title"
    onCancel={event => { event.preventDefault(); if (!busy) close(); }}>
    <form id="mapping" onSubmit={submit}>
      <h2 id="program-dialog-title">{editing.name ? '編輯程式' : '新增程式'}</h2>
      <label>程式名稱（必填）<input name="name" placeholder="例如：DRC 規則檢查（正式版）" defaultValue={editing?.name} required /></label>
      <label>程式路徑（必填，含檔名的絕對路徑）<input name="program_path" required pattern=".*\S.*" placeholder="/twhome/techfile/bin/" defaultValue={editing?.program_path || ''} /></label>
      <SearchPicker name="repo" label="Gitea repository" prompt="選擇 repository"
        placeholder="輸入 repository 名稱搜尋" emptyMessage="沒有符合的 repository" value={repo}
        onChange={setRepo} search={searchRepos} />
      {repo && <div key={repo}>
        <label>分支（必填）<select name="branch" required defaultValue={repo === editing?.repo ? editing.branch : ''}>
          <option value="">請選擇分支</option><option>main</option><option>master</option>
        </select></label>
        <label>入口檔（選填）<input name="entry" placeholder="repository 內相對路徑"
          defaultValue={repo === editing?.repo ? editing.entry : ''} /></label>
      </div>}
      <fieldset className="skill-picker">
        <legend>查案 Skills（選填）</legend>
        <input type="search" aria-label="搜尋 Skill" placeholder="搜尋 Skill" value={query} onChange={event => setQuery(event.target.value)} />
        {error && <p role="alert">{error}</p>}
        <div>{[...new Set([...selected, ...catalog])].sort().filter(name => name.includes(query.trim().toLowerCase())).map(name =>
          <label key={name}><input type="checkbox" checked={selected.includes(name)}
            onChange={event => setSelected(event.target.checked ? [...selected, name] : selected.filter(item => item !== name))} />{name}</label>
        )}</div>
      </fieldset>
      {saveFailed && status && <p role="alert">{status}</p>}
      <div className="program-actions"><button type="button" disabled={busy} onClick={close}>取消</button>
        <button className="primary" disabled={busy}>儲存</button></div>
    </form>
  </dialog>;
}
