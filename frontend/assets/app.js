'use strict';
const el = id => document.getElementById(id);
let active, editing, programs = [];
async function api(path, options = {}) {
  const response = await fetch('api/' + path, { ...options, headers: { 'X-Case-Request': '1', ...options.headers } });
  if (response.status === 401) { el('workspace').hidden = true; el('login').hidden = false; throw Error('請登入 Gitea。'); }
  if (!response.ok) throw Error((await response.json().catch(() => ({}))).error || '服務無法回應。');
  return response.status === 204 ? null : response.json();
}
function action(fn) {
  return async event => {
    event?.preventDefault();
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
  for (const name of ['intake','thread','admin']) el(name).hidden = name !== id;
}
async function listCases() {
  el('cases').replaceChildren(...(await api('cases')).map(c => button(c.program + ' · ' + c.summary, () => openCase(c.id))));
}
async function listPrograms() {
  programs = await api('programs');
  el('report').elements.program.replaceChildren(...programs.map(p => new Option(p.name,p.name)));
}
async function openCase(id) {
  const data = await api('cases/' + id);
  active = id; view('thread'); el('title').textContent = data.program;
  el('meta').textContent = data.folder + ' · ' + data.revision;
  el('messages').replaceChildren(...data.messages.map(m => {
    const block = document.createElement('div'); block.className = 'message';
    const author = document.createElement('strong'); author.textContent = m.author === '@agent' ? '查案助理' : m.author;
    const body = document.createElement('p'); body.textContent = m.body; block.append(author, body);
    if (m.image) { const img = document.createElement('img'); img.src = m.image; img.alt = '案件附件'; block.append(img); }
    return block;
  }));
  const pending = data.messages.at(-1)?.author !== '@agent';
  el('reply').hidden = pending; el('retry').hidden = !pending;
}
async function turn(data) {
  try { await api('cases/' + active + '/turn', {method:'POST', body:data}); }
  finally { await openCase(active); }
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
    line.append(p.name + ' → ' + p.repo + ' ', button('編輯', () => {
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
  form.addEventListener('paste', e => {
    const file = [...e.clipboardData.items].find(i => ['image/png','image/jpeg'].includes(i.type))?.getAsFile();
    if (file) { const transfer = new DataTransfer(); transfer.items.add(file); form.elements.image.files = transfer.files; e.preventDefault(); }
  });
}
el('logout').onclick = action(async () => { await api('logout',{method:'POST'}); location.reload(); });
action(async () => {
  const me = await api('me'); el('account').textContent = me.login; el('logout').hidden = false;
  await listPrograms(); await listCases(); el('workspace').hidden = false; el('manage').hidden = !me.admin;
})();
