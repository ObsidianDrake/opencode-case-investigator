import { useEffect, useState } from 'react';

import SearchPicker from './SearchPicker';

export default function ProgramIndex({ programs, busy, save, remove, loadSkills, searchRepos }) {
  const [editing, setEditing] = useState(null);
  const [repo, setRepo] = useState('');
  const [pickerKey, setPickerKey] = useState(0);
  const [catalog, setCatalog] = useState([]);
  const [error, setError] = useState('');
  const [selected, setSelected] = useState([]);
  const [query, setQuery] = useState('');
  useEffect(() => {
    let active = true;
    loadSkills().then(names => { if (active) setCatalog(names); })
      .catch(err => { if (active) setError(err.message); });
    return () => { active = false; };
  }, [loadSkills]);

  function edit(program) {
    setEditing(program);
    setRepo(program?.repo || '');
    setPickerKey(key => key + 1);
    setSelected(program?.skills || []);
    setQuery('');
  }

  async function submit(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = Object.fromEntries(new FormData(form));
    values.skills = selected;
    if (await save(editing?.name, values)) {
      form.reset();
      edit(null);
    }
  }
  return <>
    <div id="programs">{programs.map(p => <div key={p.name}>
      <span className="mapping-name">{p.name}{p.repo && ' → ' + p.repo}</span>
      <button disabled={busy} onClick={() => edit(p)}>編輯</button>
      <button disabled={busy} onClick={async () => {
        if (confirm('刪除 ' + p.name + ' 的程式對應？') && await remove(p.name)) edit(null);
      }}>刪除</button>
    </div>)}</div>
    <form id="mapping" key={editing?.name || ''} onSubmit={submit} onReset={() => edit(null)}>
      <label>程式名稱（必填）<input name="name" placeholder="例如：DRC 規則檢查（正式版）" defaultValue={editing?.name} required /></label>
      <label>程式路徑（必填，含檔名的絕對路徑）<input name="program_path" required pattern=".*\S.*" placeholder="/twhome/techfile/bin/" defaultValue={editing?.program_path || ''} /></label>
      <SearchPicker key={pickerKey} name="repo" label="Gitea repository" prompt="選擇 repository"
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
      <button className="primary" disabled={busy}>儲存</button><button type="reset" disabled={busy}>清除</button>
    </form>
  </>;
}
