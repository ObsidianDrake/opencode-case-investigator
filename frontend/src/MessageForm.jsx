import { useEffect, useMemo, useRef, useState } from 'react';

const programOrder = new Intl.Collator('en', { sensitivity: 'base' });

export default function MessageForm({ programs, busy, onSend, onError }) {
  const intake = programs !== undefined;
  const programNames = useMemo(
    () => (programs || []).map(program => program.name).sort(programOrder.compare),
    [programs]
  );
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState('');
  const textarea = useRef(null);

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
    if (intake) {
      const input = form.elements.program;
      const typed = input.value.trim();
      const matches = programNames.filter(name => name.toLowerCase() === typed.toLowerCase());
      const name = programNames.includes(typed) ? typed : matches.length === 1 ? matches[0] : null;
      if (!name) {
        input.setCustomValidity('請從清單選擇已登錄的程式。');
        input.reportValidity();
        return;
      }
      data.set('program', name);
    }
    if (file) data.set('image', file, file.name || 'capture.png');
    if (await onSend(data)) {
      form.reset();
      select(null);
    }
  }

  return <form id={intake ? 'report' : 'reply'} onSubmit={submit}>
    {intake && <>
      <label>執行的程式 <small>必填</small>
        <input name="program" list="intake-programs" placeholder="輸入程式名稱搜尋…" required
          autoComplete="off" onInput={event => event.currentTarget.setCustomValidity('')} />
      </label>
      <datalist id="intake-programs">
        {programNames.map(name => <option key={name} value={name} />)}
      </datalist>
      <label>程式執行位置 <small>必填</small>
        <input name="folder" placeholder="執行目錄絕對路徑（需有群組寫入權限）" required />
      </label>
      <label htmlFor="description">遇到什麼狀況 <small>必填</small></label>
    </>}
    <div className="draft">
      <textarea ref={textarea} id={intake ? 'description' : undefined} name="text" required maxLength={16000}
        aria-label={intake ? undefined : '補充訊息'}
        placeholder={intake ? '描述你看到的現象，可在這裡直接貼上截圖…' : '補充訊息，或直接貼上截圖…'}
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
