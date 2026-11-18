'use strict';
const el = id => document.getElementById(id);
let active, editing, programs = [];
async function api(path, options = {}) {
  const response = await fetch('api/' + path, { ...options, headers: { 'X-Case-Request': '1', ...options.headers } });
  if (response.status === 401) {
    el('workspace').hidden = true;
    el('logout').hidden = true;
    el('account').textContent = '';
    document.querySelector('.login-access').hidden = false;
    el('login').hidden = false;
    throw Error(path === 'me' ? '' : '登入已逾時，請重新登入 Gitea。');
  }
  if (!response.ok) throw Error((await response.json().catch(() => ({}))).error || '服務無法回應。');
  return response.status === 204 ? null : response.json();
}
function action(fn) {
  return async event => {
    event?.preventDefault();
    if (!el('thinking').hidden) return;
    const controls = [...document.querySelectorAll('button')].filter(b => !b.disabled);
    controls.forEach(b => b.disabled = true);
    el('status').textContent = '處理中…';
    try { await fn(); el('status').textContent = ''; }
    catch (e) { el('status').textContent = e.message; }
    finally { controls.forEach(b => b.disabled = false); }
  };
}
function button(label, fn) {
  const b = document.createElement('button'); b.textContent = label; b.onclick = action(fn); return b;
}
function view(id) {
  for (const item of el('cases').children) {
    item.setAttribute('aria-current', String(id === 'thread' && item.dataset.id === String(active)));
  }
  for (const name of ['intake','thread','admin']) el(name).hidden = name !== id;
}
function filterCases() {
  const query = el('case-search').value.trim().toLocaleLowerCase();
  let visible = 0;
  for (const item of el('cases').children) {
    const matches = Array.from(item.children).some(field =>
      field.textContent.toLocaleLowerCase().includes(query));
    item.hidden = !matches;
    if (matches) visible++;
  }
  el('no-matches').hidden = !query || visible > 0;
}
el('case-search').addEventListener('input', filterCases);
async function listCases() {
  const cases = await api('cases');
  el('cases').replaceChildren(...cases.map(c => {
    const item = button('', () => openCase(c.id));
    const program = document.createElement('strong');
    program.textContent = c.program;
    const summary = document.createElement('span');
    summary.textContent = c.session_title || '';
    item.dataset.id = c.id;
    item.append(program, summary);
    return item;
  }));
  filterCases();
  return cases;
}
async function listPrograms() {
  programs = await api('programs');
  el('report').elements.program.replaceChildren(...programs.map(p => new Option(p.name,p.name)));
}
async function openCase(id) {
  const data = await api('cases/' + id);
  if (active !== id) el('reply').reset();
  active = id; view('thread'); el('title').textContent = data.program;
  const subtitle = data.session_title || '';
  el('subtitle').textContent = subtitle;
  el('subtitle').hidden = !subtitle;
  const item = Array.from(el('cases').children).find(node => node.dataset.id === String(id));
  if (item) item.querySelector('span').textContent = subtitle;
  filterCases();
  el('meta').textContent = data.folder + '   ·   ' + data.revision.slice(0, 8);
  el('meta').title = data.folder + ' · ' + data.revision;
  el('messages').replaceChildren(...data.messages.map(m => {
    const block = document.createElement('div'); block.className = m.author === '@agent' ? 'message agent' : 'message human';
    const author = document.createElement('strong'); author.textContent = m.author === '@agent' ? '查案助理' : m.author;
    const body = document.createElement('p'); body.textContent = m.body; block.append(author, body);
    if (m.image) { const img = document.createElement('img'); img.src = m.image; img.alt = '案件附件'; block.append(img); }
    return block;
  }));
  el('messages').scrollTop = el('messages').scrollHeight;
  const pending = data.messages.at(-1)?.author !== '@agent';
  el('reply').hidden = pending; el('retry').hidden = !pending;
}
async function turn(data) {
  const caseId = active;
  el('status').textContent = '';
  el('thinking').hidden = false;
  try {
    await api('cases/' + caseId + '/turn', {method:'POST', body:data});
  } finally {
    el('thinking').hidden = true;
    await openCase(caseId);
  }
}
el('report').onsubmit = action(async () => {
  const {id} = await api('cases', {method:'POST',body:new FormData(el('report'))});
  el('report').reset(); await listCases(); await openCase(id); await turn();
});
el('reply').onsubmit = action(async () => {
  await turn(new FormData(el('reply'))); el('reply').reset();
});
el('retry').onclick = action(() => turn());
el('new').onclick = action(async () => { await listPrograms(); view('intake'); });
function showMappings() {
  view('admin');
  el('programs').replaceChildren(...programs.map(p => {
    const line = document.createElement('div');
    const mappingName = document.createElement('span');
    mappingName.className = 'mapping-name';
    mappingName.textContent = p.name + ' → ' + p.repo;
    line.append(mappingName, button('編輯', () => {
      editing = p.name;
      for (const key of ['name','repo','branch','entry']) el('mapping').elements[key].value = p[key];
    }), button('刪除', async () => {
      if (!confirm('刪除 ' + p.name + ' 的程式對應？')) return;
      await api('programs/'+encodeURIComponent(p.name),{method:'DELETE'});
      el('mapping').reset(); await listPrograms(); showMappings();
    }));
    return line;
  }));
}
el('manage').onclick = action(async () => { await listPrograms(); showMappings(); });
el('mapping').onreset = () => { editing = null; };
el('mapping').onsubmit = action(async () => {
  await api('programs'+(editing ? '/'+encodeURIComponent(editing) : ''),{method:editing?'PUT':'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify(Object.fromEntries(new FormData(el('mapping'))))});
  el('mapping').reset(); await listPrograms(); showMappings();
});
for (const form of [el('report'),el('reply')]) {
  const input = form.elements.image;
  const text = form.elements.text;
  const preview = form.querySelector('.draft-image');
  const image = preview.querySelector('img');
  let attachment = null;

  function selectFile(file) {
    if (file && !['image/png', 'image/jpeg'].includes(file.type)) {
      el('status').textContent = '請選擇 PNG 或 JPEG 圖片。';
      return;
    }
    attachment = file;
    input.value = '';
    image.removeAttribute('src');
    preview.hidden = !file;
    if (!file) return;
    preview.querySelector('span').textContent = file.name || '貼上的圖片';
    const reader = new FileReader();
    reader.onload = () => { if (attachment === file) image.src = reader.result; };
    reader.readAsDataURL(file);
  }

  input.addEventListener('change', () => {
    if (input.files.length) selectFile(input.files[0]);
  });
  preview.querySelector('button').onclick = () => { selectFile(null); text.focus(); };
  form.addEventListener('reset', () => selectFile(null));
  form.addEventListener('formdata', event => {
    event.formData.delete('image');
    if (attachment) event.formData.set('image', attachment, attachment.name || 'capture.png');
  });
  text.addEventListener('paste', event => {
    const item = Array.from(event.clipboardData?.items || []).find(i => i.type.startsWith('image/'));
    const file = item?.getAsFile();
    if (file) {
      event.preventDefault();
      selectFile(file);
    }
  });
}
el('logout').onclick = action(async () => { await api('logout',{method:'POST'}); location.reload(); });
action(async () => {
  const me = await api('me'); el('account').textContent = me.login; el('logout').hidden = false;
  await listPrograms();
  const cases = await listCases();
  el('workspace').hidden = false; el('manage').hidden = !me.admin;
  if (cases.length) await openCase(cases[0].id);
})();
