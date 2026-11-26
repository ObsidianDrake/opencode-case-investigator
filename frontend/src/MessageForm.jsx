import { useEffect, useMemo, useRef, useState } from 'react';

import IntakePicker from './IntakePicker';

const programOrder = new Intl.Collator('en', { sensitivity: 'base' });

export function draftKeyFor(userId, scope) {
  return JSON.stringify(['case-draft', location.pathname, userId, scope]);
}

export function readDraft(key) {
  try { return JSON.parse(sessionStorage.getItem(key)); } catch { return null; }
}

export default function MessageForm({ draftKey, programs, sites = [], busy, onSend, onError, draftRef }) {
  const intake = programs !== undefined;
  const programNames = useMemo(
    () => (programs || []).map(program => program.name).sort(programOrder.compare),
    [programs]
  );
  const [initial] = useState(() => readDraft(draftKey) || {});
  const draft = useRef({ ...initial });
  function saveField(name, value) {
    draft.current[name] = value;
    try {
      if (Object.values(draft.current).some(Boolean)) sessionStorage.setItem(draftKey, JSON.stringify(draft.current));
      else sessionStorage.removeItem(draftKey);
    } catch { onError('瀏覽器無法暫存草稿，請先複製文字備份。'); }
  }
  const [program, setProgram] = useState(initial.program || '');
  const [site, setSite] = useState(initial.site || '');
  const [invalidField, setInvalidField] = useState('');
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState('');
  const localDraft = useRef(null);
  const textarea = draftRef || localDraft;

  useEffect(() => {
    if (!file) {
      setPreview('');
      return;
    }
    const reader = new FileReader();
    reader.onload = () => setPreview(reader.result);
    reader.readAsDataURL(file);
    return () => reader.abort();
  }, [file]);

  function select(file) {
    if (file && !['image/png', 'image/jpeg'].includes(file.type)) {
      onError('請選擇 PNG 或 JPEG 圖片。');
      return;
    }
    setFile(file);
    setPreview('');
  }

  async function submit(event) {
    event.preventDefault();
    if (busy) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    if (intake && !programNames.includes(program)) {
      setInvalidField('program');
      form.querySelector('#intake-program').focus();
      return;
    }
    if (intake && sites.length > 1 && !sites.includes(site)) {
      setInvalidField('site');
      form.elements.site.focus();
      return;
    }
    if (intake && !file && !textarea.current.value.trim()) {
      onError('請描述遇到的狀況，或附上一張圖片。');
      textarea.current.focus();
      return;
    }
    if (file) data.set('image', file, file.name || 'capture.png');
    if (await onSend(data)) {
      draft.current = {};
      sessionStorage.removeItem(draftKey);
      form.reset();
      form.elements.text.value = '';
      if (intake) form.elements.folder.value = '';
      setProgram('');
      setSite('');
      setInvalidField('');
      select(null);
    }
  }

  return <form id={intake ? 'report' : 'reply'} onSubmit={submit}>
    {intake && <>
      <IntakePicker name="program" label="執行的程式" placeholder="輸入程式名稱搜尋"
        names={programNames} value={program} disabled={busy}
        error={invalidField === 'program' && '請從清單選擇已登錄的程式。'}
        onChange={name => { saveField('program', name); setProgram(name); setInvalidField(''); }} />
      {sites.length > 1 && <IntakePicker name="site" label="執行主機" placeholder="選擇或搜尋執行主機"
        names={sites} value={site} disabled={busy}
        error={invalidField === 'site' && '請從清單選擇執行主機。'}
        onChange={name => { saveField('site', name); setSite(name); setInvalidField(''); }} />}
      <label>程式執行位置 <small>必填</small>
        <input name="folder" defaultValue={initial.folder || ''} disabled={busy} onInput={e => saveField('folder', e.target.value)} placeholder="執行目錄絕對路徑（需有群組寫入權限）" required />
      </label>
      <label htmlFor="description">遇到什麼狀況 <small>文字或圖片擇一</small></label>
    </>}
    <div className="draft">
      <textarea defaultValue={initial.text || ''} readOnly={busy} onInput={e => saveField('text', e.target.value)} ref={textarea} id={intake ? 'description' : undefined} name="text" required={!intake || !file} maxLength={16000}
        aria-label={intake ? undefined : '補充訊息'}
        placeholder={intake ? '描述你看到的現象，或直接貼上截圖…' : '補充訊息，或直接貼上截圖…'}
        onPaste={event => {
          const image = Array.from(event.clipboardData.items).find(item => item.type.startsWith('image/'))?.getAsFile();
          if (image) { event.preventDefault(); select(image); }
        }} />
      {file && <div className="draft-image">
        {preview && <img src={preview} alt="待送出的圖片" />}
        <span>{file.name || '貼上的圖片'}</span>
        <button type="button" disabled={busy} aria-label="移除圖片" onClick={() => { select(null); textarea.current.focus(); }}>移除</button>
      </div>}
    </div>
    <div className="tools">
      <label className="attach"><span aria-hidden="true">＋</span><span className="filename">附上圖片</span>
        <input type="file" accept="image/png,image/jpeg" disabled={busy} onChange={event => {
          if (event.target.files.length) select(event.target.files[0]);
          event.target.value = '';
        }} />
      </label>
      <button className="primary" disabled={busy}>{intake ? '開始調查 ↗' : '送出 ↑'}</button>
    </div>
  </form>;
}
