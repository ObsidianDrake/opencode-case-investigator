import { useEffect, useRef, useState } from 'react';

export default function MessageForm({ programs, busy, onSend, onError }) {
  const intake = programs !== undefined;
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
    const form = event.currentTarget;
    const data = new FormData(form);
    if (file) data.set('image', file, file.name || 'capture.png');
    if (await onSend(data)) {
      form.reset();
      select(null);
    }
  }

  return <form id={intake ? 'report' : 'reply'} onSubmit={submit}>
    {intake && <>
      <label>執行的程式 <small>必填</small>
        <select name="program" required>{programs.map(p => <option key={p.name}>{p.name}</option>)}</select>
      </label>
      <label>程式執行位置 <small>必填</small>
        <input name="folder" placeholder="執行程式時所在的資料夾" required />
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
