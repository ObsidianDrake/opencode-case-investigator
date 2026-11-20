import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import Markdown from './Markdown';
import MessageForm from './MessageForm';
import QuoteSelection from './QuoteSelection';
import { flushSync } from 'react-dom';
import ProgramIndex from './ProgramIndex';
import scarecrow from '../assets/scarecrow.png';
import hoppingScarecrow from '../assets/scarecrow-hop.gif';
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

const searchRepos = (q, page) => api('admin/repositories?' + new URLSearchParams({ q, page }));
const loadSkills = () => api('admin/skills');

function Login() {
  return <section id="login" aria-labelledby="login-title"><div className="login-stage">
    <div className="login-copy">
      <p className="login-label"><span aria-hidden="true" />AI 查案助手</p>
      <h1 id="login-title">城堡還在走，<br /><em>哪裡鬆了？</em></h1>
      <p className="login-intro">程式跑起來怪怪的？描述你看到的現象，或貼上截圖。查案助手會陪你循著線索，一步步找出原因。</p>
      <a className="login-button" href={'auth/login' + location.search}>使用 Gitea 登入<span aria-hidden="true">→</span></a>
    </div>
    <div className="login-art" role="img" aria-label="拼著鐵板與老屋的移動城堡正邁步穿過草地，路旁站著一個小稻草人。" />
  </div></section>;
}

function Hero({ label, title, children }) {
  return <div className="hero">
    <span className="section-label">{label}</span><h1>{title}</h1>{children}
  </div>;
}

function ThinkingText({ text }) {
  const [expanded, setExpanded] = useState(false);
  if (!text) return null;
  const tail = text.trimEnd().split('\n').slice(-3).join('\n').slice(-160);
  return <div className="thinking-text">
    <button type="button" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>
      {expanded ? '收合思考過程' : '展開完整思考過程'}
    </button>
    <div className={expanded ? 'thinking-full' : 'thinking-preview'}><Markdown text={expanded ? text : tail} /></div>
  </div>;
}

function caseTitle(c) { return c.custom_title || c.program; }
function caseSubtitle(c) { return c.custom_subtitle || c.session_title || ''; }

function RenameCase({ item, busy, save, close }) {
  const dialog = useRef(null);
  useEffect(() => { dialog.current.showModal(); }, []);
  return <dialog className="rename-dialog" ref={dialog} onCancel={close} aria-label="重新命名對話">
    <form onSubmit={event => {
      event.preventDefault();
      const values = Object.fromEntries(new FormData(event.currentTarget));
      if (values.custom_title === item.program) values.custom_title = '';
      if (values.custom_subtitle === item.session_title) values.custom_subtitle = '';
      save(item.id, values);
    }}>
      <h2>重新命名對話</h2>
      <label>主標題<input name="custom_title" defaultValue={caseTitle(item)} placeholder={item.program} maxLength={200} autoFocus /></label>
      <label>副標題<input name="custom_subtitle" defaultValue={caseSubtitle(item)} placeholder={item.session_title || '對話名稱'} maxLength={200} /></label>
      <p>留空會使用原本的標題。</p>
      <div><button type="button" onClick={close}>取消</button> <button className="primary" disabled={busy}>儲存</button></div>
    </form>
  </dialog>;
}

function PanelToggle({ side, open, controls, label, onClick }) {
  const description = (open ? '收合' : '展開') + label;
  const pointsLeft = side === 'left' ? open : !open;
  return <button type="button" className="panel-toggle" aria-label={description} title={description}
    aria-controls={controls} aria-expanded={open} onClick={onClick}>
    <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor"
      strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="2" y="4" width="20" height="16" rx="2" />
      <path d={side === 'left' ? 'M8 4v16' : 'M16 4v16'} />
      <g transform={side === 'left' ? 'translate(15 12)' : 'translate(9 12)'}>
        <path d={pointsLeft ? 'M1.5 -3l-3 3 3 3' : 'M-1.5 -3l3 3 -3 3'} />
      </g>
    </svg>
  </button>;
}

function Thread({ thread, thinking, turnError, busy, retry, reply, refresh, onError }) {
  const [heroOpen, setHeroOpen] = useState(true);
  const running = Boolean(thinking || thread.active_turn);
  const [idleReplyOpen, setIdleReplyOpen] = useState(() => window.innerWidth > 760);
  const [runningReplyOpen, setRunningReplyOpen] = useState(false);
  const replyOpen = running ? runningReplyOpen : idleReplyOpen;
  useEffect(() => { if (!running) setRunningReplyOpen(false); }, [running]);
  const manualHeading = useRef(false);
  const log = useRef(null);
  const replyDraft = useRef(null);
  function appendQuote(text) {
    const draft = replyDraft.current;
    if (!draft) return false;
    const separator = draft.value && (draft.value.endsWith('\n\n') ? '' : draft.value.endsWith('\n') ? '\n' : '\n\n');
    const citation = '引用查案助理先前的回覆：\n' + text.split('\n').map(line => '> ' + line).join('\n');
    const addition = separator + citation + '\n\n';
    if (draft.value.length + addition.length > draft.maxLength) {
      onError('加入引用後會超過 16000 字，請縮短選取範圍或草稿。');
      return false;
    }
    flushSync(() => setIdleReplyOpen(true));
    draft.setRangeText(addition, draft.value.length, draft.value.length, 'end');
    window.getSelection()?.removeAllRanges();
    draft.focus();
    draft.setSelectionRange(draft.value.length, draft.value.length);
    draft.scrollTop = draft.scrollHeight;
    return true;
  }
  useLayoutEffect(() => {
    if (!heroOpen || manualHeading.current) return;
    const messages = log.current;
    const checkOverflow = () => {
      if (!manualHeading.current && messages.clientHeight > 0 && messages.scrollHeight > messages.clientHeight) {
        setHeroOpen(false);
      }
    };
    checkOverflow();
    const observer = new ResizeObserver(checkOverflow);
    observer.observe(messages);
    for (const message of messages.children) observer.observe(message);
    return () => observer.disconnect();
  }, [thread.messages, heroOpen]);
  useEffect(() => { log.current.scrollTop = log.current.scrollHeight; }, [thread]);
  const [activity, setActivity] = useState(thread.active_turn);
  const [reasoning, setReasoning] = useState('');
  const [historyAvailable, setHistoryAvailable] = useState(true);
  const [stopping, setStopping] = useState(false);
  useEffect(() => {
    if (!thinking && !thread.active_turn) { setActivity(null); setReasoning(''); setHistoryAvailable(true); return; }
    let live = true;
    let timer;
    async function poll() {
      try {
        const { turn_id, reasoning: snapshot, history_available } = await api('cases/' + thread.id + '/activity?reasoning=1');
        if (live) {
          setActivity(turn_id);
          setHistoryAvailable(history_available !== false);
          setReasoning(snapshot || '');
          if (!turn_id && !thinking) { await refresh(); return; }
        }
      } catch (error) { if (live) { setHistoryAvailable(false); onError(error.message); } }
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
    <div className="thread-heading">
      <button className="heading-toggle" aria-label={heroOpen ? '收合案件標題' : '展開案件標題'} aria-expanded={heroOpen} onClick={() => { manualHeading.current = true; setHeroOpen(!heroOpen); }}>{heroOpen ? '⌃' : '⌄'}</button>
      {heroOpen ? <Hero label="案件對話" title={caseTitle(thread)}>
      {caseSubtitle(thread) && <p id="subtitle">{caseSubtitle(thread)}</p>}
      <p id="meta" title={[thread.folder, thread.revision].filter(Boolean).join(' · ')}>{thread.folder}{thread.revision && ' · ' + thread.revision.slice(0, 8)}</p>
    </Hero> : <h1 className="compact-title">{caseSubtitle(thread) || caseTitle(thread)}</h1>}
    </div>
    <div className="conversation">
    <div id="messages" ref={log} role="log" aria-label="案件對話紀錄">{thread.messages.map(m =>
      <div key={m.id} className={'message ' + (m.author === '@agent' ? 'agent' : 'human')}>
        <strong>{m.author === '@agent' ? '查案助理' : m.author}</strong><ThinkingText text={m.reasoning} />{m.author === '@agent' ? <div className="message-body"><Markdown text={m.body} /></div> : <p className="message-body">{m.body}</p>}
        {m.image && <img src={m.image} alt="案件附件" />}
      </div>)}</div>
    {thread.can_reply && !pending && !running && !busy && <QuoteSelection log={log} onAdd={appendQuote} />}
    {turnError && <p role="alert">{turnError}</p>}
    {(thinking || activity) && <div id="thinking" role="status"><img src={hoppingScarecrow} width="56" height="56" alt="" />查案助手正在調查… {thread.can_reply && <button type="button" disabled={!activity || stopping} onClick={stop}>{stopping ? '正在終止…' : '終止查案'}</button>}</div>}
    {(thinking || activity) && <>
      <ThinkingText key={activity} text={reasoning} />
      {!historyAvailable ? <small role="status">暫時無法更新思考過程，正在重試…</small>
        : !reasoning && <small role="status">等候 OpenCode 回傳本回合的思考內容…</small>}
    </>}
    </div>
    {thread.can_reply && <aside className="reply-panel" aria-label="回覆案件">
      <PanelToggle side="right" open={replyOpen} controls="reply-content" label="回覆欄"
        onClick={() => running ? setRunningReplyOpen(!replyOpen) : setIdleReplyOpen(!replyOpen)} />
      <div id="reply-content" hidden={!replyOpen}>
      {pending
      ? <button id="retry" disabled={busy || running} onClick={retry}>重新取得回覆</button>
      : <MessageForm draftRef={replyDraft} busy={busy || running} onSend={reply} onError={onError} />}
      </div>
    </aside>}
  </section>;
}

function App() {
  const [user, setUser] = useState(undefined);
  const [cases, setCases] = useState([]);
  const [programs, setPrograms] = useState([]);
  const [casesOpen, setCasesOpen] = useState(() => window.innerWidth > 760);
  const [view, setView] = useState('intake');
  const [thread, setThread] = useState(null);
  const [model, setModel] = useState('');
  const [models, setModels] = useState([]);
  const [choice, setChoice] = useState('');
  const [catalogStatus, setCatalogStatus] = useState('');
  const [query, setQuery] = useState('');
  const [busy, setBusy] = useState(false);
  const pendingTurns = useRef(new Set());
  const selectedCase = useRef(null);
  const detailVersion = useRef(0);
  const [status, setStatus] = useState('');
  const [menu, setMenu] = useState(null);
  const [renaming, setRenaming] = useState(null);
  const menuId = menu?.id;
  useEffect(() => {
    if (menuId == null) return;
    const dismiss = event => {
      if (!event.target.closest?.('.case-menu, .case-actions')) setMenu(null);
    };
    const escape = event => {
      if (event.key === 'Escape') {
        setMenu(null);
        document.getElementById('actions-button-' + menuId)?.focus();
      }
    };
    const close = () => setMenu(null);
    document.addEventListener('pointerdown', dismiss);
    document.addEventListener('keydown', escape);
    window.addEventListener('scroll', close, true);
    window.addEventListener('resize', close);
    return () => {
      document.removeEventListener('pointerdown', dismiss);
      document.removeEventListener('keydown', escape);
      window.removeEventListener('scroll', close, true);
      window.removeEventListener('resize', close);
    };
  }, [menuId]);
  useEffect(() => {
    if (menuId == null) return;
    let live = true;
    let timer;
    async function checkActivity() {
      try {
        const { turn_id } = await api('cases/' + menuId + '/activity');
        if (live) setMenu(current => current?.id === menuId ? { ...current, active: !!turn_id } : current);
      } catch (error) {
        if (live) setStatus(error.message);
      }
      if (live) timer = setTimeout(checkActivity, 1500);
    }
    checkActivity();
    return () => { live = false; clearTimeout(timer); };
  }, [menuId]);
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

  function setCaseUrl(id, replace = false) {
    selectedCase.current = id;
    const url = new URL(location.href);
    if (id == null) url.searchParams.delete('case');
    else url.searchParams.set('case', String(id));
    if (url.href !== location.href) {
      history[replace ? 'replaceState' : 'pushState'](null, '', url);
    }
  }

  async function refreshCase(id) {
    const version = selectedCase.current === id ? ++detailVersion.current : null;
    const data = await api('cases/' + id);
    if (selectedCase.current === id && version === detailVersion.current) setThread({ ...data, id });
    setCases(list => list.map(c => c.id === id ? { ...c, session_title: data.session_title } : c));
  }

  async function openCase(id, replace = false) {
    setCaseUrl(id, replace);
    setThread(current => current?.id === id ? current : null);
    setView('thread');
    await refreshCase(id);
  }

  useEffect(() => {
    act(async () => {
      const me = await api('me');
      const [list, mappings] = await Promise.all([api('cases'), api('programs')]);
      setCases(list);
      setPrograms(mappings);
      setUser(me);
      const requested = new URLSearchParams(location.search).get('case');
      if (requested !== null) {
        const selected = list.find(item => String(item.id) === requested);
        if (!selected) throw new Error('找不到此案件，請確認分享網址。');
        await openCase(selected.id, true);
      } else if (list.length) await openCase(list[0].id, true);
    });
    const restorePage = () => location.reload();
    window.addEventListener('popstate', restorePage);
    return () => window.removeEventListener('popstate', restorePage);
  }, []);

  async function turn(id, body) {
    if (pendingTurns.current.has(id)) return false;
    pendingTurns.current.add(id);
    setCases(list => list.map(c => c.id === id ? { ...c, running: true, turnError: '' } : c));
    let errorMessage = '';
    try {
      await api('cases/' + id + '/turn', { method: 'POST', body });
    } catch (error) {
      errorMessage = error.message;
      if (error.status === 401) setUser(null);
    }
    try {
      await refreshCase(id);
    } catch (error) {
      if (!errorMessage) errorMessage = error.message;
      if (error.status === 401) setUser(null);
    } finally {
      pendingTurns.current.delete(id);
      setCases(list => list.map(c => c.id === id ? { ...c, running: false, turnError: errorMessage } : c));
    }
    return !errorMessage;
  }

  function createCase(data) {
    return act(async () => {
      const created = await api('cases', { method: 'POST', body: data });
      const { id, program, session_title, can_reply } = created;
      setCases(list => [{ id, program, session_title, can_manage: can_reply }, ...list]);
      setCasesOpen(false);
      setCaseUrl(id);
      setThread(created);
      setView('thread');
      void turn(id);
    });
  }

  function deleteCase(id) {
    return act(async () => {
      await api('cases/' + id, { method: 'DELETE' });
      setCases(list => list.filter(item => item.id !== id));
      if (view === 'thread' && thread?.id === id) {
        setThread(null);
        setView('intake');
        setCaseUrl(null, true);
      }
    });
  }

  function switchView(next) {
    return act(async () => { setPrograms(await api('programs')); setCaseUrl(null); setView(next); });
  }

  function openModelSettings() {
    return act(async () => {
      const saved = (await api('admin/model')).model;
      setModel(saved);
      setChoice('');
      setModels([]);
      setCatalogStatus('正在讀取 OpenCode 模型清單…');
      setCaseUrl(null);
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
      await api('programs' + (name ? '?name=' + encodeURIComponent(name) : ''), options);
      setPrograms(await api('programs'));
    });
  }

  function startInvestigation() {
    return switchView('intake');
  }

  const brand = <><img className="brandmark" src={scarecrow} alt="" /><strong>DRC查案助手</strong></>;
  const currentCase = cases.find(c => c.id === thread?.id);
  const filter = query.trim().toLocaleLowerCase();
  const visible = cases.filter(c => [c.program, caseTitle(c), caseSubtitle(c)].some(v => v.toLocaleLowerCase().includes(filter)));
  return <>
    <header>
      {user
        ? <button type="button" className="brand" disabled={busy} onClick={startInvestigation}
            aria-label="DRC查案助手，開始新的調查" title="開始新的調查">{brand}</button>
        : <div className="brand">{brand}</div>}
      {user === null && <span className="login-access"><i aria-hidden="true" />內部使用</span>}
      {user && <><span className="account">{user.login}</span><button disabled={busy} onClick={() => act(async () => {
        await api('logout', { method: 'POST' }); location.reload();
      })}>登出</button></>}
    </header>
    {renaming && <RenameCase item={renaming} busy={busy} close={() => setRenaming(null)}
      save={(id, values) => act(async () => {
        const names = await api('cases/' + id, { method: 'PATCH',
          headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(values) });
        setCases(list => list.map(c => c.id === id ? { ...c, ...names } : c));
        setThread(c => c?.id === id ? { ...c, ...names } : c);
        setRenaming(null);
      })} />}
    <main><p id="status" role="status">{status}</p>
      {user === null && <Login />}
      {user && <div id="workspace" className={view === 'thread' && !casesOpen ? 'cases-closed' : ''}><aside className="case-panel">
        {view === 'thread' && <PanelToggle side="left" open={casesOpen} controls="case-controls" label="案件列表"
          onClick={() => setCasesOpen(!casesOpen)} />}
        <div id="case-controls" hidden={view === 'thread' && !casesOpen}>
        <button id="new" disabled={busy} onClick={startInvestigation}>＋ 建立案件</button>
        <input id="case-search" type="search" aria-label="搜尋案件對話" placeholder="搜尋程式名稱或對話標題"
          value={query} onChange={e => setQuery(e.target.value)} />
        <h2>案件對話</h2><nav id="cases">{visible.map(c => <div className="case-row" key={c.id}><button className="case-link" disabled={busy}
          aria-current={view === 'thread' && thread?.id === c.id} onClick={() => act(() => openCase(c.id))}>
          <strong>{caseTitle(c)}</strong><span>{caseSubtitle(c)}</span>
        </button>
        {!!c.can_manage && <>
          <button className="case-actions" type="button" aria-label={'對話動作：' + (caseSubtitle(c) || caseTitle(c))}
            title="對話動作" disabled={busy} id={'actions-button-' + c.id} aria-expanded={menu?.id === c.id}
            onClick={event => {
              const rect = event.currentTarget.getBoundingClientRect();
              setMenu(menu?.id === c.id ? null : { id: c.id, right: window.innerWidth - rect.right,
                top: Math.min(rect.bottom + 3, window.innerHeight - 106) });
            }}>⋯</button>
          {menu?.id === c.id && <div className="case-menu" style={{right: menu.right, top: menu.top}}>
            <button onClick={() => { setMenu(null); setRenaming(c); }}>重新命名</button>
            <button disabled={busy || c.running || menu.active !== false} onClick={() => {
              setMenu(null);
              if (window.confirm('確定刪除「' + (caseSubtitle(c) || caseTitle(c)) + '」的整段案件對話？刪除後，所有人都無法再透過分享網址查看。')) deleteCase(c.id);
            }}>刪除案件對話</button>
          </div>}
        </>}
        </div>)}</nav>
        {filter && !visible.length && <p id="no-matches" role="status">沒有符合的案件</p>}
        {user.admin && <nav className="admin-nav" aria-label="管理功能">
          <button id="manage" aria-current={view === 'admin'} disabled={busy}
            onClick={() => switchView('admin')}>管理程式索引</button>
          <button id="model-settings" aria-current={view === 'model'} disabled={busy}
            onClick={openModelSettings}>OpenCode 設定</button>
        </nav>}
        </div>
      </aside><article>
        {view === 'model' && user.admin && <section>
          <Hero label="管理員" title="OpenCode 設定" />
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
        {view === 'thread' && thread && <Thread key={thread.id} thread={thread} thinking={!!currentCase?.running} turnError={currentCase?.turnError} busy={busy}
          refresh={() => refreshCase(thread.id)} retry={() => turn(thread.id)} reply={data => turn(thread.id, data)} onError={setStatus} />}
        {view === 'admin' && user.admin && <section id="admin"><Hero label="管理員" title="程式索引" />
          <ProgramIndex status={status} searchRepos={searchRepos} loadSkills={loadSkills} programs={programs} busy={busy}
            save={(name, values) => changeProgram(name, { method: name ? 'PUT' : 'POST',
              headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(values) })}
            remove={name => changeProgram(name, { method: 'DELETE' })} />
        </section>}
      </article></div>}
    </main>
  </>;
}

createRoot(document.getElementById('root')).render(<App />);
