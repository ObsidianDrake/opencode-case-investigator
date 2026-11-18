import { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import MessageForm from './MessageForm';
import ProgramIndex from './ProgramIndex';
import scarecrow from '../assets/scarecrow.svg';
import '../assets/style.css';

async function api(path, options = {}) {
  const response = await fetch('api/' + path, {
    ...options, headers: { 'X-Case-Request': '1', ...options.headers }
  });
  if (!response.ok) {
    const details = await response.json().catch(() => ({}));
    const error = new Error(response.status === 401
      ? (path === 'me' ? '' : '登入已逾時，請重新登入 Gitea。')
      : details.error || '服務無法回應。');
    error.status = response.status;
    throw error;
  }
  return response.status === 204 ? null : response.json();
}

function Login() {
  return <section id="login" aria-labelledby="login-title"><div className="login-stage">
    <div className="login-copy">
      <p className="login-label"><span aria-hidden="true" />AI 查案助手</p>
      <h1 id="login-title">城堡還在走，<br /><em>哪裡鬆了？</em></h1>
      <p className="login-intro">程式跑起來怪怪的？描述你看到的現象，或貼上截圖。查案助手會陪你循著線索，一步步找出原因。</p>
      <a className="login-button" href="auth/login">使用 Gitea 登入<span aria-hidden="true">→</span></a>
    </div>
    <div className="login-art" role="img" aria-label="拼著鐵板與老屋的移動城堡正邁步穿過草地，路旁站著一個小稻草人。" />
  </div></section>;
}

function Hero({ label, title, children }) {
  return <div className="hero"><div>
    <span className="section-label">{label}</span><h1>{title}</h1>{children}
  </div><div className="castle-print" aria-hidden="true" /></div>;
}

function Thread({ thread, thinking, busy, retry, reply, refresh, onError }) {
  const log = useRef(null);
  useEffect(() => { log.current.scrollTop = log.current.scrollHeight; }, [thread]);
  const [activity, setActivity] = useState(thread.active_turn);
  const [stopping, setStopping] = useState(false);
  useEffect(() => {
    if (!thinking && !thread.active_turn) { setActivity(null); return; }
    let live = true;
    let timer;
    async function poll() {
      try {
        const { turn_id } = await api('cases/' + thread.id + '/activity');
        if (live) {
          setActivity(turn_id);
          if (!turn_id && !thinking) { await refresh(); return; }
        }
      } catch (error) { if (live) onError(error.message); }
      if (live) timer = setTimeout(poll, 1500);
    }
    poll();
    return () => { live = false; clearTimeout(timer); };
  }, [thinking, thread.id, thread.active_turn]);
  async function stop() {
    setStopping(true);
    try {
      await api('cases/' + thread.id + '/abort', { method: 'POST', body: new URLSearchParams({ turn_id: activity }) });
    } catch (error) { onError(error.message); }
    finally { setStopping(false); }
  }
  const pending = thread.messages.at(-1)?.author !== '@agent';
  return <section id="thread">
    <Hero label="案件對話" title={thread.program}>
      {thread.session_title && <p id="subtitle">{thread.session_title}</p>}
      <p id="meta" title={thread.folder + ' · ' + thread.revision}>{thread.folder} · {thread.revision.slice(0, 8)}</p>
    </Hero>
    <div id="messages" ref={log} role="log" aria-label="案件對話紀錄">{thread.messages.map(m =>
      <div key={m.id} className={'message ' + (m.author === '@agent' ? 'agent' : 'human')}>
        <strong>{m.author === '@agent' ? '查案助理' : m.author}</strong><p>{m.body}</p>
        {m.image && <img src={m.image} alt="案件附件" />}
      </div>)}</div>
    {(thinking || activity) && <div id="thinking" role="status"><img src={scarecrow} width="34" height="34" alt="" />查案助手正在調查… <button type="button" disabled={!activity || stopping} onClick={stop}>{stopping ? '正在終止…' : '終止查案'}</button></div>}
    {pending
      ? <button id="retry" disabled={busy || !!activity} onClick={retry}>重新取得回覆</button>
      : <MessageForm busy={busy} onSend={reply} onError={onError} />}
  </section>;
}

function App() {
  const [user, setUser] = useState(undefined);
  const [cases, setCases] = useState([]);
  const [programs, setPrograms] = useState([]);
  const [view, setView] = useState('intake');
  const [thread, setThread] = useState(null);
  const [model, setModel] = useState('');
  const [models, setModels] = useState([]);
  const [choice, setChoice] = useState('');
  const [catalogStatus, setCatalogStatus] = useState('');
  const [query, setQuery] = useState('');
  const [busy, setBusy] = useState(false);
  const [thinking, setThinking] = useState(false);
  const [status, setStatus] = useState('');
  const locked = useRef(false);

  async function act(work) {
    if (locked.current) return false;
    locked.current = true;
    setBusy(true);
    setStatus('處理中…');
    try {
      await work();
      setStatus('');
      return true;
    } catch (error) {
      if (error.status === 401) setUser(null);
      setStatus(error.message);
      return false;
    } finally {
      locked.current = false;
      setBusy(false);
    }
  }

  async function openCase(id) {
    const data = await api('cases/' + id);
    setThread({ ...data, id });
    setCases(list => list.map(c => c.id === id ? { ...c, session_title: data.session_title } : c));
    setView('thread');
  }

  useEffect(() => {
    act(async () => {
      const me = await api('me');
      const [list, mappings] = await Promise.all([api('cases'), api('programs')]);
      setCases(list);
      setPrograms(mappings);
      setUser(me);
      if (list.length) await openCase(list[0].id);
    });
  }, []);

  async function turn(id, body) {
    setThinking(true);
    setStatus('');
    try { await api('cases/' + id + '/turn', { method: 'POST', body }); }
    finally { setThinking(false); await openCase(id); }
  }

  function createCase(data) {
    return act(async () => {
      const { id } = await api('cases', { method: 'POST', body: data });
      setCases(await api('cases'));
      await openCase(id);
      await turn(id);
    });
  }

  function switchView(next) {
    return act(async () => { setPrograms(await api('programs')); setView(next); });
  }

  function openModelSettings() {
    return act(async () => {
      const saved = (await api('admin/model')).model;
      setModel(saved);
      setChoice('');
      setModels([]);
      setCatalogStatus('正在讀取 OpenCode 模型清單…');
      setView('model');
      try {
        const list = await api('admin/models');
        setModels(list);
        setChoice(list.some(item => item.id === saved) ? saved : '');
        setCatalogStatus(list.length ? '' : 'OpenCode 目前未列出模型。');
      } catch (error) {
        if (error.status === 401 || error.status === 403) throw error;
        setCatalogStatus('無法取得 OpenCode 模型清單。' + error.message);
      }
    });
  }

  async function saveModel(event) {
    event.preventDefault();
    if (!choice) return;
    if (await act(async () => {
      const result = await api('admin/model', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ model: choice })
      });
      setModel(result.model);
    })) setStatus('已儲存模型設定');
  }

  function changeProgram(name, options) {
    return act(async () => {
      await api('programs' + (name ? '/' + encodeURIComponent(name) : ''), options);
      setPrograms(await api('programs'));
    });
  }

  const filter = query.trim().toLocaleLowerCase();
  const visible = cases.filter(c => [c.program, c.session_title || ''].some(v => v.toLocaleLowerCase().includes(filter)));
  return <>
    <header>
      <div className="brand"><img className="brandmark" src={scarecrow} alt="" /><strong>DRC查案助手</strong></div>
      {user === null && <span className="login-access"><i aria-hidden="true" />內部使用</span>}
      {user && <><span className="account">{user.login}</span><button disabled={busy} onClick={() => act(async () => {
        await api('logout', { method: 'POST' }); location.reload();
      })}>登出</button></>}
    </header>
    <main><p id="status" role="status">{status}</p>
      {user === null && <Login />}
      {user && <div id="workspace"><aside>
        <button id="new" disabled={busy} onClick={() => switchView('intake')}>＋ 建立案件</button>
        <input id="case-search" type="search" aria-label="搜尋案件對話" placeholder="搜尋程式名稱或對話標題"
          value={query} onChange={e => setQuery(e.target.value)} />
        <h2>案件對話</h2><nav id="cases">{visible.map(c => <button key={c.id} disabled={busy}
          aria-current={view === 'thread' && thread?.id === c.id} onClick={() => act(() => openCase(c.id))}>
          <strong>{c.program}</strong><span>{c.session_title}</span>
        </button>)}</nav>
        {filter && !visible.length && <p id="no-matches" role="status">沒有符合的案件</p>}
        {user.admin && <button id="manage" disabled={busy} onClick={() => switchView('admin')}>管理程式索引</button>}
        {user.admin && <button id="model-settings" disabled={busy} onClick={openModelSettings}>查案模型設定</button>}
      </aside><article>
        {view === 'model' && user.admin && <section>
          <Hero label="管理員" title="查案模型設定" />
          <form id="model-form" onSubmit={saveModel}>
            <p>目前模型：<strong>{model || '由 OpenCode 選擇'}</strong></p>
            <label>可用模型<select name="model" value={choice}
              onChange={event => setChoice(event.target.value)} required disabled={busy || !models.length}>
              <option value="" disabled>請選擇模型</option>
              {models.map(item => <option key={item.id} value={item.id}>
                {item.id}{item.image_input ? '（支援圖片）' : ''}
              </option>)}
            </select></label>
            {catalogStatus && <p role="status">{catalogStatus}</p>}
            <button type="button" disabled={busy} onClick={openModelSettings}>重新取得清單</button>
            <p>儲存後，所有案件從下一次提問起使用此模型。</p>
            <button className="primary" disabled={busy || !choice}>儲存模型</button>
          </form>
        </section>}

        <section id="intake" hidden={view !== 'intake'}><Hero label="查案助理" title="開始新的調查" />
          <MessageForm programs={programs} busy={busy} onSend={createCase} onError={setStatus} />
        </section>
        {view === 'thread' && thread && <Thread key={thread.id} thread={thread} thinking={thinking} busy={busy}
          refresh={() => openCase(thread.id)} retry={() => act(() => turn(thread.id))} reply={data => act(() => turn(thread.id, data))} onError={setStatus} />}
        {view === 'admin' && user.admin && <section id="admin"><Hero label="管理員" title="程式索引" />
          <ProgramIndex programs={programs} busy={busy}
            save={(name, values) => changeProgram(name, { method: name ? 'PUT' : 'POST',
              headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(values) })}
            remove={name => changeProgram(name, { method: 'DELETE' })} />
        </section>}
      </article></div>}
    </main>
  </>;
}

createRoot(document.getElementById('root')).render(<App />);
