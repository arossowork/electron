const $ = (s) => document.querySelector(s);
let cur = null, es = null, tab = 'build/REPORT.md';
const LABELS = {'build/REPORT.md':'Report','SUMMARY.md':'Summary','requirements.yaml':'Requirements','conflicts.md':'Conflicts',
  'architecture.md':'Architecture','state.md':'State','notes/parts.md':'Parts','design.yaml':'Design',
  'build/fab/pcb.svg':'PCB','build/fab/pcb_3d_top.png':'3D','build/fab/schematic.pdf':'Schematic'};
const model = () => $('#model').value;
async function api(path, body) {
  const r = await fetch(path, body ? {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)} : {});
  const j = await r.json(); if (!r.ok) { alert(j.error || 'error'); throw new Error(j.error); } return j;
}
async function loadProjects() {
  const ps = await api('/api/projects');
  $('#projects').innerHTML = '';
  for (const p of ps) {
    const li = document.createElement('li');
    li.className = p.name === cur ? 'active' : '';
    const stage = (p.state.match(/stage:\s*(.*)/i) || [,''])[1];
    li.innerHTML = `<span class="dot ${p.busy?'busy':p.waiting?'wait':''}"></span>${p.name}${p.waiting?` <b class="badge">${p.waiting} question${p.waiting>1?'s':''}</b>`:''}<small>${stage||p.state.split('\n')[0]}</small>`;
    li.onclick = () => open(p.name);
    $('#projects').append(li);
  }
}
let toolBox = null, toolN = 0, firstIdx = 0, usage = {};
const tm = (n) => n >= 3600 ? (n / 3600).toFixed(1) + ' h' : n >= 60 ? Math.round(n / 60) + ' min' : n + ' s';
function logAdd(el) { const log = $('#log'); log.append(el); log.scrollTop = log.scrollHeight; }
function toolLine(ev) {
  // Tool calls of one agent turn go in one collapsed box; its summary is the status line (item 33).
  if (!toolBox) {
    toolBox = document.createElement('details'); toolBox.className = 'tools'; toolN = 0;
    toolBox.innerHTML = '<summary></summary><div class="toolbody"></div>';
    logAdd(toolBox);
  }
  toolN++;
  const l = document.createElement('div'); l.textContent = ev.text;
  toolBox.querySelector('.toolbody').append(l);
  toolBox.querySelector('summary').textContent = `${toolN} tool call${toolN > 1 ? 's' : ''}, last: ${ev.text.split('\n')[0].slice(0, 90)}`;
}
function add(ev) {
  if (ev.kind === 'files') { renderTabs(ev.files); show(tab); loadProjects(); return; }
  if (ev.kind === 'questions') { renderQuestions(ev.questions); return; }
  if (ev.kind === 'usage') { usage = ev.agents; renderUsage(); return; }
  if (ev.kind === 'tool') { toolLine(ev); return; }
  toolBox = null;
  const d = document.createElement('div');
  d.className = 'msg ' + ev.kind + (ev.queued ? ' queued' : '');
  if (ev.kind === 'agent') { d.classList.add('md'); d.innerHTML = renderMarkdown(ev.text); }
  else d.textContent = ev.text + (ev.queued ? ' (queued)' : '');
  logAdd(d);
  if (ev.kind === 'status' && !ev.old) {
    if (ev.busy !== undefined) setBusy(ev.busy);
    loadProjects();
  }
}
function setBusy(b) {
  $('#busy').textContent = b ? 'working...' : '';
  $('#interrupt').hidden = !b;
  $('#sendbtn').textContent = b ? 'Queue' : 'Send';
}
function renderUsage() {
  const rows = Object.entries(usage), box = $('#usage');
  box.hidden = !rows.length;
  if (!rows.length) return;
  const tok = (a) => Object.values(a.tokens).reduce((n, t) => n + Object.values(t).reduce((x, y) => x + y, 0), 0);
  const models = (a) => Object.keys(a.tokens).map(m => m.replace('claude-', '')).join(', ');
  const total = rows.reduce((n, [, a]) => n + tok(a), 0);
  box.querySelector('summary').textContent = `Usage this process: ${(total / 1e6).toFixed(1)} M tokens`;
  box.querySelector('tbody').innerHTML = rows.sort((x, y) => tok(y[1]) - tok(x[1])).map(([k, a]) =>
    `<tr><td>${mdEsc(k)}</td><td>${a.launches || ''}</td><td>${a.turns}</td><td>${tm(a.seconds)}</td><td>${(tok(a) / 1e6).toFixed(2)} M</td><td>${mdEsc(models(a))}</td></tr>`).join('');
}
async function loadEarlier() {
  const pg = await api(`/api/projects/${cur}/history?before=${firstIdx}&limit=300`);
  firstIdx = pg.start; $('#more').hidden = firstIdx <= 0;
  const log = $('#log'), old = [...log.children].filter(c => c.id !== 'more');
  old.forEach(c => c.remove()); toolBox = null;
  pg.events.forEach(e => add({...e, old: true})); toolBox = null;
  old.forEach(c => log.append(c));
  log.scrollTop = 0;
}
async function open(name) {
  cur = name; history.replaceState(null, '', '#' + name); $('#pname').textContent = name; $('#side').classList.remove('open');
  const info = await api(`/api/projects/${name}/info`);
  $('#log').innerHTML = '<button id="more" type="button" hidden>Load earlier messages</button>'; toolBox = null;
  $('#more').onclick = loadEarlier; firstIdx = info.start; $('#more').hidden = firstIdx <= 0;
  info.history.forEach(e => add(e)); usage = info.usage || {}; renderUsage();
  setBusy(info.busy);
  renderTabs(info.files); show(info.files.includes(tab) ? tab : info.files[0]);
  renderQuestions(info.questions);
  if (es) es.close();
  es = new EventSource(`/api/projects/${name}/events`); es.onmessage = (m) => add(JSON.parse(m.data));
  loadProjects();
}
function renderTabs(files) {
  $('#tabs').innerHTML = '';
  for (const f of files) {
    const b = document.createElement('button'); b.textContent = LABELS[f] || f;
    b.className = f === tab ? 'active' : ''; b.onclick = () => show(f); $('#tabs').append(b);
  }
}
async function show(f) {
  if (!f || !cur) return; tab = f;
  [...$('#tabs').children].forEach(b => b.classList.toggle('active', b.textContent === (LABELS[f]||f)));
  const url = `/api/projects/${cur}/file/${f}?t=${Date.now()}`;
  if (/\.(svg|png)$/.test(f)) { $('#view').innerHTML = `<img src="${url}" alt="${LABELS[f]}">`; return; }
  if (/\.pdf$/.test(f)) { $('#view').innerHTML = `<object data="${url}" type="application/pdf" width="100%" height="100%"></object>`; return; }
  const txt = await (await fetch(url)).text();
  const isMd = /\.md$/.test(f) && f !== 'build/REPORT.md';
  const pre = document.createElement(isMd ? 'div' : 'pre'); pre.textContent = txt;
  if (isMd) { pre.className = 'md'; pre.innerHTML = renderMarkdown(txt); }
  $('#view').innerHTML = '';
  if (f === 'build/REPORT.md') {
    const s = await (await fetch(`/api/projects`)).json(); const p = s.find(x => x.name === cur);
    if (p && p.score) $('#view').append(scoreCard(p.score));
  }
  $('#view').append(pre);
}
function scoreCard(sc) {
  const d = document.createElement('div'); d.className = 'score';
  const item = (k, v, good) => `<div><span class="muted">${k}</span><b class="${good===undefined?'':good?'ok':'bad'}">${v ?? 'n/a'}</b></div>`;
  d.innerHTML = item('ERC errors', sc.erc_errors, sc.erc_errors === 0) + item('DRC errors', sc.drc_errors, sc.drc_errors === 0)
    + item('Sim pass', sc.sim_pass_ratio != null ? Math.round(sc.sim_pass_ratio*100)+'%' : null, sc.sim_pass_ratio === 1)
    + item('Post-layout pass', sc.post_pass_ratio != null ? Math.round(sc.post_pass_ratio*100)+'%' : null, sc.post_pass_ratio === 1)
    + item('Requirements unverified', sc.req_unverified, sc.req_unverified === 0)
    + item('Board mm²', sc.area_mm2) + item('BOM $', sc.cost_usd) + item('Vias', sc.vias);
  return d;
}
function renderQuestions(q) {
  const f = $('#qform');
  if (!q || !q.questions || !q.questions.length) { f.hidden = true; return; }
  f.hidden = false;
  f.innerHTML = `<h3>The engineer needs your decision: ${q.questions.length} question${q.questions.length > 1 ? 's' : ''} (round ${q.round ?? ''})</h3>` + q.questions.map(x => `
    <fieldset><legend>${mdEsc(x.text)}</legend>${x.why ? `<div class="why">${mdEsc(x.why)}</div>` : ''}
    ${(x.options||[]).map(o => `<label><input type="radio" name="${mdEsc(x.id)}" value="${mdEsc(o)}"> ${mdEsc(o)}</label>`).join('')}
    <input name="${mdEsc(x.id)}__free" placeholder="Or write your own answer"></fieldset>`).join('')
    + `<div class="row"><button class="primary">Send answers</button></div>`;
  f.onsubmit = async (e) => {
    e.preventDefault(); const fd = new FormData(f), answers = {};
    for (const x of q.questions) answers[x.id] = (fd.get(x.id + '__free') || fd.get(x.id) || '').toString();
    f.hidden = true; await api(`/api/projects/${cur}/answer`, {answers, model: model()});
  };
}
$('#composer').onsubmit = async (e) => {
  e.preventDefault(); if (!cur) return alert('Pick or create a product first');
  const text = $('#msg').value.trim(); if (!text) return;
  await api(`/api/projects/${cur}/message`, {text, model: model()}); $('#msg').value = '';
};
$('#interrupt').onclick = async () => {
  const text = $('#msg').value.trim(); if (!cur || !text) return alert('Type the message to send first');
  await api(`/api/projects/${cur}/message`, {text, model: model(), interrupt: true}); $('#msg').value = '';
};
$('#stop').onclick = () => cur && api(`/api/projects/${cur}/stop`, {});
$('#build').onclick = () => cur && api(`/api/projects/${cur}/build`, {});
$('#new').onclick = () => $('#newdlg').showModal();
$('#menu').onclick = () => $('#side').classList.toggle('open');
$('#newform').onsubmit = async (e) => {
  if (e.submitter && e.submitter.value === 'cancel') return;
  const fd = new FormData(e.target);
  const r = await api('/api/projects', {name: fd.get('name'), brief: fd.get('brief'), model: model()});
  await open(r.name);
};
if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js');
loadProjects(); setInterval(loadProjects, 10000);
if (location.hash.length > 1) open(decodeURIComponent(location.hash.slice(1)));
