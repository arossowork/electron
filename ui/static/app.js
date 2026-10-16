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
    li.innerHTML = `<span class="dot ${p.busy?'busy':p.waiting?'wait':''}"></span>${p.name}<small>${stage||p.state.split('\n')[0]}</small>`;
    li.onclick = () => open(p.name);
    $('#projects').append(li);
  }
}
function add(ev) {
  if (ev.kind === 'files') { renderTabs(ev.files); show(tab); loadProjects(); return; }
  if (ev.kind === 'questions') { renderQuestions(ev.questions); return; }
  const d = document.createElement('div');
  d.className = 'msg ' + ev.kind; d.textContent = ev.text;
  $('#log').append(d); $('#log').scrollTop = $('#log').scrollHeight;
  if (ev.kind === 'status') { $('#busy').textContent = /started/.test(ev.text) ? 'working...' : ''; loadProjects(); }
}
async function open(name) {
  cur = name; history.replaceState(null, '', '#' + name); $('#pname').textContent = name; $('#side').classList.remove('open');
  const info = await api(`/api/projects/${name}/info`);
  $('#log').innerHTML = ''; info.history.forEach(add);
  $('#busy').textContent = info.busy ? 'working...' : '';
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
  const pre = document.createElement('pre'); pre.textContent = txt;
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
  f.innerHTML = `<h3>The engineer needs your decision (round ${q.round ?? ''})</h3>` + q.questions.map(x => `
    <fieldset><legend>${x.text}</legend>${x.why ? `<div class="why">${x.why}</div>` : ''}
    ${(x.options||[]).map((o,i) => `<label><input type="radio" name="${x.id}" value="${o.replace(/"/g,'&quot;')}" ${i===0?'':''}> ${o}</label>`).join('')}
    <input name="${x.id}__free" placeholder="Or write your own answer"></fieldset>`).join('')
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
