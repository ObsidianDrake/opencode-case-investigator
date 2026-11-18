import { useState } from 'react';

export default function ProgramIndex({ programs, busy, save, remove }) {
  const [editing, setEditing] = useState(null);
  async function submit(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = Object.fromEntries(new FormData(form));
    if (await save(editing?.name, values)) {
      form.reset();
      setEditing(null);
    }
  }
  return <>
    <div id="programs">{programs.map(p => <div key={p.name}>
      <span className="mapping-name">{p.name} → {p.repo}</span>
      <button disabled={busy} onClick={() => setEditing(p)}>編輯</button>
      <button disabled={busy} onClick={async () => {
        if (confirm('刪除 ' + p.name + ' 的程式對應？') && await remove(p.name)) setEditing(null);
      }}>刪除</button>
    </div>)}</div>
    <form id="mapping" key={editing?.name || ''} onSubmit={submit} onReset={() => setEditing(null)}>
      <label>程式名稱<input name="name" defaultValue={editing?.name} required /></label>
      <label>Gitea repository<input name="repo" placeholder="owner/repository" defaultValue={editing?.repo} required /></label>
      <label>分支<select name="branch" defaultValue={editing?.branch || 'main'}><option>main</option><option>master</option></select></label>
      <label>入口檔（選填）<input name="entry" placeholder="repository 內相對路徑" defaultValue={editing?.entry} /></label>
      <button className="primary" disabled={busy}>儲存</button><button type="reset" disabled={busy}>清除</button>
    </form>
  </>;
}
