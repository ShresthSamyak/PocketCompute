/* PocketCompute — mobile web client.
 * Pure vanilla JS, no build step. Talks to the agent over REST + WebSocket. */
'use strict';

const LS = {
  token: 'pc_token',
  base: 'pc_base',
};

const state = {
  base: localStorage.getItem(LS.base) || location.origin,
  token: localStorage.getItem(LS.token) || null,
  ws: null,
  connected: false,
  deviceName: 'My PC',
  shells: ['powershell'],
  shortcuts: [],
  jobs: new Map(),
  reqSeq: 0,
  openJobLogs: new Set(),
  filePath: null,
  reconnectTimer: null,
};

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);
const el = (tag, cls, html) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (html != null) n.innerHTML = html;
  return n;
};
const esc = (s) => String(s).replace(/[&<>"]/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

/* ---------------- networking ---------------- */
function normalizeBase(input) {
  let h = (input || '').trim();
  if (!h) return location.origin;
  if (!/^https?:\/\//i.test(h)) h = 'http://' + h;
  return h.replace(/\/+$/, '');
}

async function api(path, opts = {}) {
  const headers = Object.assign({}, opts.headers || {});
  if (state.token) headers['Authorization'] = 'Bearer ' + state.token;
  const res = await fetch(state.base + path, Object.assign({}, opts, { headers }));
  if (res.status === 401) { logout(); throw new Error('unauthorized'); }
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (e) {}
    throw new Error(detail);
  }
  const ct = res.headers.get('content-type') || '';
  return ct.includes('json') ? res.json() : res;
}

/* ---------------- pairing ---------------- */
async function tryPair(base, code, label) {
  const res = await fetch(normalizeBase(base) + '/api/pair', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ secret: code, label: label || 'phone' }),
  });
  if (!res.ok) {
    let msg = 'Pairing failed';
    try { msg = (await res.json()).detail || msg; } catch (e) {}
    throw new Error(msg);
  }
  const data = await res.json();
  state.base = normalizeBase(base);
  state.token = data.token;
  state.deviceName = data.device_name || 'My PC';
  localStorage.setItem(LS.base, state.base);
  localStorage.setItem(LS.token, state.token);
}

function logout() {
  state.token = null;
  localStorage.removeItem(LS.token);
  if (state.ws) { try { state.ws.close(); } catch (e) {} }
  showPairScreen();
}

function showPairScreen() {
  $('#app').classList.add('hidden');
  $('#pair-screen').classList.remove('hidden');
}

function showApp() {
  $('#pair-screen').classList.add('hidden');
  $('#app').classList.remove('hidden');
}

/* ---------------- websocket ---------------- */
function connectWS() {
  if (!state.token) return;
  const wsBase = state.base.replace(/^http/, 'ws');
  const ws = new WebSocket(wsBase + '/ws?token=' + encodeURIComponent(state.token));
  state.ws = ws;

  ws.onopen = () => {
    state.connected = true;
    setConnDot(true);
    if (state.reconnectTimer) { clearTimeout(state.reconnectTimer); state.reconnectTimer = null; }
  };
  ws.onclose = () => {
    state.connected = false;
    setConnDot(false);
    scheduleReconnect();
  };
  ws.onerror = () => { try { ws.close(); } catch (e) {} };
  ws.onmessage = (ev) => {
    let msg; try { msg = JSON.parse(ev.data); } catch (e) { return; }
    handleMessage(msg);
  };
}

function scheduleReconnect() {
  if (state.reconnectTimer || !state.token) return;
  state.reconnectTimer = setTimeout(() => {
    state.reconnectTimer = null;
    connectWS();
  }, 2000);
}

function wsSend(obj) {
  if (state.ws && state.connected) state.ws.send(JSON.stringify(obj));
}

function setConnDot(online) {
  const dot = $('#conn-dot');
  if (!dot) return;
  dot.classList.toggle('online', online);
  dot.classList.toggle('offline', !online);
}

/* ---------------- message routing ---------------- */
const pending = new Map(); // req_id -> {bubbleOut, target}

function handleMessage(msg) {
  switch (msg.type) {
    case 'snapshot':
      state.deviceName = msg.device_name || state.deviceName;
      $('#device-name').textContent = state.deviceName;
      applyMetrics(msg.metrics);
      (msg.jobs || []).forEach((j) => state.jobs.set(j.id, j));
      renderJobs();
      break;
    case 'metrics':
      applyMetrics(msg.metrics);
      break;
    case 'output_start': onOutputStart(msg); break;
    case 'output': onOutput(msg); break;
    case 'output_end': onOutputEnd(msg); break;
    case 'job_update':
      state.jobs.set(msg.id, Object.assign(state.jobs.get(msg.id) || {}, msg));
      renderJobs();
      break;
    case 'job_log':
      appendJobLog(msg.id, msg.line);
      break;
    case 'job_detail':
      state.jobs.set(msg.id, msg);
      renderJobs();
      break;
    case 'shortcuts_changed':
      state.shortcuts = msg.shortcuts || [];
      renderShortcuts();
      break;
    case 'error':
      pushBubble('pc', '⚠️ ' + (msg.message || 'error'));
      break;
  }
}

/* ---------------- metrics rendering ---------------- */
function fmtBytes(bps) {
  if (bps < 1024) return bps.toFixed(0) + ' B/s';
  if (bps < 1024 * 1024) return (bps / 1024).toFixed(1) + ' KB/s';
  return (bps / 1024 / 1024).toFixed(1) + ' MB/s';
}
function fmtUptime(sec) {
  const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60);
  if (d) return `${d}d ${h}h`;
  if (h) return `${h}h ${m}m`;
  return `${m}m`;
}

function applyMetrics(m) {
  if (!m) return;
  // Top bar.
  $('#top-cpu').textContent = Math.round(m.cpu.percent) + '%';
  $('#top-ram').textContent = m.ram.used_gb + 'G';
  const gpu = (m.gpu && m.gpu[0]) ? Math.round(m.gpu[0].load) + '%' : '—';
  $('#top-gpu').textContent = gpu;
  $('#top-jobs').textContent = [...state.jobs.values()].filter((j) => j.status === 'running').length;
  // Full grid.
  renderMetricsGrid(m);
}

function barClass(p) { return p >= 90 ? 'bar crit' : p >= 70 ? 'bar warn' : 'bar'; }

function renderMetricsGrid(m) {
  const grid = $('#metrics-grid');
  if (!grid) return;
  const cards = [];
  cards.push(metricCard('CPU', Math.round(m.cpu.percent) + '%', m.cpu.percent,
    `${m.cpu.cores} cores`));
  cards.push(metricCard('Memory', m.ram.percent + '%', m.ram.percent,
    `${m.ram.used_gb} / ${m.ram.total_gb} GB`));
  cards.push(metricCard('Disk', m.disk.percent + '%', m.disk.percent,
    `${m.disk.used_gb} / ${m.disk.total_gb} GB`));
  cards.push(`<div class="metric-card"><div class="label">Network</div>
    <div class="value" style="font-size:18px">↓ ${fmtBytes(m.net.down_bps)}</div>
    <div class="sub">↑ ${fmtBytes(m.net.up_bps)}</div></div>`);
  if (m.gpu && m.gpu.length) {
    m.gpu.forEach((g) => {
      const memPct = g.mem_total_mb ? (g.mem_used_mb / g.mem_total_mb * 100) : 0;
      cards.push(`<div class="metric-card wide"><div class="label">GPU · ${esc(g.name)}</div>
        <div class="value">${Math.round(g.load)}% <small>load${g.temp_c != null ? ' · ' + Math.round(g.temp_c) + '°C' : ''}</small></div>
        <div class="${barClass(g.load)}"><span style="width:${g.load}%"></span></div>
        <div class="sub">VRAM ${g.mem_used_mb} / ${g.mem_total_mb} MB</div>
        <div class="${barClass(memPct)}" style="margin-top:6px"><span style="width:${memPct}%"></span></div></div>`);
    });
  }
  cards.push(`<div class="metric-card wide"><div class="label">Uptime</div>
    <div class="value" style="font-size:22px">${fmtUptime(m.uptime_seconds)}</div></div>`);
  grid.innerHTML = cards.join('');
}

function metricCard(label, value, pct, sub) {
  return `<div class="metric-card"><div class="label">${label}</div>
    <div class="value">${value}</div>
    <div class="${barClass(pct)}"><span style="width:${Math.min(pct, 100)}%"></span></div>
    <div class="sub">${sub}</div></div>`;
}

function renderSysInfo(info) {
  const box = $('#sysinfo');
  if (!box || !info) return;
  box.innerHTML = `
    <div><b>OS:</b> ${esc(info.platform)} ${esc(info.release)} (${esc(info.machine)})</div>
    <div><b>CPU:</b> ${info.cpu_physical} physical / ${info.cpu_cores} logical cores</div>
    <div><b>RAM:</b> ${info.ram_total_gb} GB</div>
    <div><b>Python:</b> ${esc(info.python)}</div>`;
}

/* ---------------- tabs ---------------- */
function switchTab(name) {
  $$('.tab').forEach((t) => t.classList.toggle('active', t.dataset.tab === name));
  $$('.view').forEach((v) => v.classList.toggle('hidden', v.dataset.view !== name));
  if (name === 'files' && !state.filePath) loadFiles(null);
}

/* ---------------- chat / commands ---------------- */
function pushBubble(who, text, cls) {
  const log = $('#chat-log');
  const b = el('div', 'bubble ' + who + (cls ? ' ' + cls : ''));
  b.textContent = text;
  log.appendChild(b);
  log.scrollTop = log.scrollHeight;
  return b;
}

function sendChat() {
  const input = $('#chat-input');
  const cmd = input.value.trim();
  if (!cmd) return;
  input.value = '';
  runCommand(cmd, $('#chat-shell').value);
}

function runCommand(command, shell, label) {
  pushBubble('me', command);
  const reqId = 'r' + (++state.reqSeq);
  const b = el('div', 'bubble pc mono');
  const meta = el('span', 'meta', `${esc(shell)} · running…`);
  const out = el('span', 'out');
  b.appendChild(meta); b.appendChild(out);
  $('#chat-log').appendChild(b);
  $('#chat-log').scrollTop = $('#chat-log').scrollHeight;
  pending.set(reqId, { out, meta, buf: '', target: 'chat' });
  wsSend({ type: 'run', command, shell, req_id: reqId });
}

function onOutputStart(msg) {
  // For terminal-originated runs we set up the target lazily.
  const p = pending.get(msg.req_id);
  if (p && p.meta) p.meta.textContent = `${esc(msg.shell)} · running…`;
}

function onOutput(msg) {
  const p = pending.get(msg.req_id);
  if (!p) return;
  p.buf += msg.chunk;
  if (p.target === 'terminal') {
    appendTerm(msg.chunk);
  } else if (p.out) {
    p.out.textContent = p.buf;
    const log = $('#chat-log');
    log.scrollTop = log.scrollHeight;
  }
}

function onOutputEnd(msg) {
  const p = pending.get(msg.req_id);
  if (!p) return;
  if (p.target === 'terminal') {
    appendTerm(`\n[exit ${msg.exit_code}]\n`, 'ec');
  } else {
    if (!p.buf.trim()) { p.out.textContent = '(no output)'; }
    const ok = msg.exit_code === 0;
    p.meta.textContent = ok ? '✓ done' : `exit ${msg.exit_code}`;
    if (!ok) p.meta.style.color = 'var(--red)';
  }
  pending.delete(msg.req_id);
}

/* ---------------- shortcuts ---------------- */
function renderShortcuts() {
  const row = $('#quick-shortcuts');
  row.innerHTML = '';
  state.shortcuts.forEach((s) => {
    const chip = el('button', 'chip', `${s.emoji || '⚡'} ${esc(s.name)}`);
    chip.onclick = () => runCommand(s.command, s.shell || 'powershell', s.name);
    chip.oncontextmenu = (e) => { e.preventDefault(); confirmDeleteShortcut(s); };
    // long-press to delete on touch
    let timer;
    chip.addEventListener('touchstart', () => { timer = setTimeout(() => confirmDeleteShortcut(s), 600); }, { passive: true });
    chip.addEventListener('touchend', () => clearTimeout(timer));
    row.appendChild(chip);
  });
  const add = el('button', 'chip add', '＋ Shortcut');
  add.onclick = openShortcutModal;
  row.appendChild(add);
}

function confirmDeleteShortcut(s) {
  openModal(`Delete shortcut?`, `
    <p class="muted">Remove "<b>${esc(s.name)}</b>"?</p>`,
    [{ label: 'Cancel', cls: 'btn-cancel', onClick: closeModal },
     { label: 'Delete', cls: 'btn-danger', onClick: async () => {
        try { await api('/api/shortcuts/' + s.id, { method: 'DELETE' }); } catch (e) {}
        closeModal();
     }}]);
}

function openShortcutModal() {
  const shells = state.shells.map((s) => `<option value="${s}">${s}</option>`).join('');
  openModal('New shortcut', `
    <label>Name</label><input id="sc-name" placeholder="Start backend" />
    <div class="row">
      <div style="flex:0 0 80px"><label>Emoji</label><input id="sc-emoji" value="⚡" /></div>
      <div><label>Shell</label><select id="sc-shell">${shells}</select></div>
    </div>
    <label>Command</label><textarea id="sc-cmd" placeholder="cd C:\\proj; npm run dev"></textarea>`,
    [{ label: 'Cancel', cls: 'btn-cancel', onClick: closeModal },
     { label: 'Save', cls: 'btn-ok', onClick: async () => {
        const name = $('#sc-name').value.trim();
        const command = $('#sc-cmd').value.trim();
        if (!name || !command) return;
        try {
          await api('/api/shortcuts', { method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, command, shell: $('#sc-shell').value, emoji: $('#sc-emoji').value }) });
        } catch (e) {}
        closeModal();
     }}]);
}

/* ---------------- jobs ---------------- */
function renderJobs() {
  const list = $('#jobs-list');
  const jobs = [...state.jobs.values()].sort((a, b) => (b.started_at || 0) - (a.started_at || 0));
  $('#jobs-empty').classList.toggle('hidden', jobs.length > 0);
  $('#top-jobs').textContent = jobs.filter((j) => j.status === 'running').length;
  list.innerHTML = '';
  jobs.forEach((j) => list.appendChild(jobCard(j)));
}

function jobCard(j) {
  const card = el('div', 'job-card');
  const running = j.status === 'running' || j.status === 'starting';
  card.innerHTML = `
    <div class="row1">
      <span class="jname">${esc(j.name)}</span>
      <span class="badge ${j.status}">${j.status}</span>
    </div>
    <div class="jcmd">${esc(j.command || '')}</div>
    <div class="jmeta">
      <span>${j.shell || ''}</span>
      ${j.pid ? `<span>PID ${j.pid}</span>` : ''}
      <span>${fmtUptime(j.runtime_seconds || 0)}</span>
      ${j.exit_code != null ? `<span>exit ${j.exit_code}</span>` : ''}
    </div>`;
  const actions = el('div', 'job-actions');
  const logBtn = el('button', 'btn-small', state.openJobLogs.has(j.id) ? 'Hide logs' : 'Logs');
  logBtn.onclick = () => toggleJobLog(j.id);
  actions.appendChild(logBtn);
  if (running) {
    const stop = el('button', 'btn-small', '■ Stop');
    stop.onclick = () => wsSend({ type: 'job_stop', id: j.id });
    actions.appendChild(stop);
  }
  card.appendChild(actions);
  if (state.openJobLogs.has(j.id)) {
    const log = el('pre', 'job-log');
    log.id = 'joblog-' + j.id;
    log.textContent = (j.log || []).join('\n') || '(no output yet)';
    card.appendChild(log);
  }
  return card;
}

function toggleJobLog(id) {
  if (state.openJobLogs.has(id)) state.openJobLogs.delete(id);
  else { state.openJobLogs.add(id); wsSend({ type: 'job_detail', id }); }
  renderJobs();
}

function appendJobLog(id, line) {
  const j = state.jobs.get(id);
  if (j) { j.log = j.log || []; j.log.push(line); if (j.log.length > 500) j.log.shift(); }
  if (state.openJobLogs.has(id)) {
    const pre = $('#joblog-' + id);
    if (pre) { pre.textContent += (pre.textContent ? '\n' : '') + line; pre.scrollTop = pre.scrollHeight; }
  }
}

function openJobModal() {
  const shells = state.shells.map((s) => `<option value="${s}">${s}</option>`).join('');
  openModal('Start a job', `
    <label>Name</label><input id="job-name" placeholder="Backend server" />
    <label>Shell</label><select id="job-shell">${shells}</select>
    <label>Working directory (optional)</label><input id="job-cwd" placeholder="C:\\proj" />
    <label>Command</label><textarea id="job-cmd" placeholder="npm run dev"></textarea>`,
    [{ label: 'Cancel', cls: 'btn-cancel', onClick: closeModal },
     { label: 'Start', cls: 'btn-ok', onClick: () => {
        const name = $('#job-name').value.trim();
        const command = $('#job-cmd').value.trim();
        if (!command) return;
        wsSend({ type: 'job_start', name: name || command, command,
                 shell: $('#job-shell').value, cwd: $('#job-cwd').value.trim() || null });
        switchTab('jobs');
        closeModal();
     }}]);
}

/* ---------------- files ---------------- */
async function loadFiles(path) {
  let data;
  try { data = await api('/api/files' + (path ? '?path=' + encodeURIComponent(path) : '')); }
  catch (e) { return; }
  state.filePath = data.path;
  $('#file-path').textContent = data.path;
  const list = $('#files-list');
  list.innerHTML = '';
  if (data.parent) {
    const up = el('div', 'file-row');
    up.innerHTML = `<span class="fi">↩</span><span class="fname">..</span>`;
    up.onclick = () => loadFiles(data.parent);
    list.appendChild(up);
  }
  data.entries.forEach((e) => {
    const row = el('div', 'file-row');
    const icon = e.is_dir ? '📁' : fileIcon(e.name);
    row.innerHTML = `<span class="fi">${icon}</span>
      <span class="fname">${esc(e.name)}</span>
      ${e.is_dir ? '' : `<span class="fsize">${humanSize(e.size)}</span><span class="dl">⬇</span>`}`;
    if (e.is_dir) {
      row.onclick = () => loadFiles(e.path);
    } else {
      row.querySelector('.dl').onclick = (ev) => { ev.stopPropagation(); downloadFile(e.path); };
      row.onclick = () => downloadFile(e.path);
    }
    list.appendChild(row);
  });
}

function fileIcon(name) {
  const ext = (name.split('.').pop() || '').toLowerCase();
  if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'].includes(ext)) return '🖼️';
  if (['py', 'js', 'ts', 'go', 'rs', 'c', 'cpp', 'java', 'rb'].includes(ext)) return '📜';
  if (['zip', 'tar', 'gz', '7z', 'rar'].includes(ext)) return '🗜️';
  if (['mp4', 'mov', 'mkv', 'avi'].includes(ext)) return '🎬';
  if (['mp3', 'wav', 'flac'].includes(ext)) return '🎵';
  if (['pdf'].includes(ext)) return '📕';
  return '📄';
}
function humanSize(b) {
  if (b < 1024) return b + ' B';
  if (b < 1024 * 1024) return (b / 1024).toFixed(0) + ' KB';
  if (b < 1024 * 1024 * 1024) return (b / 1024 / 1024).toFixed(1) + ' MB';
  return (b / 1024 / 1024 / 1024).toFixed(2) + ' GB';
}

function downloadFile(path) {
  const url = state.base + '/api/files/download?path=' + encodeURIComponent(path) +
    '&token=' + encodeURIComponent(state.token);
  const a = document.createElement('a');
  a.href = url; a.download = '';
  document.body.appendChild(a); a.click(); a.remove();
}

async function uploadFile(file) {
  const fd = new FormData();
  fd.append('dest', state.filePath || '');
  fd.append('file', file);
  try {
    await api('/api/files/upload', { method: 'POST', body: fd });
    loadFiles(state.filePath);
  } catch (e) { alert('Upload failed: ' + e.message); }
}

/* ---------------- terminal ---------------- */
function appendTerm(text, cls) {
  const out = $('#term-output');
  if (cls) {
    const span = el('span', cls); span.textContent = text; out.appendChild(span);
  } else {
    out.appendChild(document.createTextNode(text));
  }
  out.scrollTop = out.scrollHeight;
}

function sendTerm() {
  const input = $('#term-input');
  const cmd = input.value.trim();
  if (!cmd) return;
  input.value = '';
  appendTerm('\n', null);
  const span = el('span', 'cmd'); span.textContent = '$ ' + cmd + '\n'; $('#term-output').appendChild(span);
  const reqId = 'r' + (++state.reqSeq);
  pending.set(reqId, { target: 'terminal' });
  wsSend({ type: 'run', command: cmd, shell: $('#term-shell').value, req_id: reqId });
}

/* ---------------- modal ---------------- */
function openModal(title, bodyHTML, actions) {
  const root = $('#modal-root');
  root.innerHTML = '';
  const backdrop = el('div', 'modal-backdrop');
  const modal = el('div', 'modal');
  modal.innerHTML = `<h3>${esc(title)}</h3>${bodyHTML}`;
  const act = el('div', 'modal-actions');
  actions.forEach((a) => {
    const btn = el('button', a.cls, a.label);
    btn.onclick = a.onClick;
    act.appendChild(btn);
  });
  modal.appendChild(act);
  backdrop.appendChild(modal);
  backdrop.onclick = (e) => { if (e.target === backdrop) closeModal(); };
  root.appendChild(backdrop);
}
function closeModal() { $('#modal-root').innerHTML = ''; }

/* ---------------- shells select ---------------- */
function populateShells() {
  const opts = state.shells.map((s) => `<option value="${s}">${s}</option>`).join('');
  $('#chat-shell').innerHTML = opts;
  $('#term-shell').innerHTML = opts;
}

/* ---------------- boot ---------------- */
async function boot() {
  // Auto-pair from QR: URL like /?pair=SECRET
  const params = new URLSearchParams(location.search);
  const pairCode = params.get('pair');
  if (pairCode && !state.token) {
    try {
      await tryPair(location.origin, pairCode);
      history.replaceState({}, '', location.pathname);
    } catch (e) { /* fall through to manual screen */ }
  }

  if (!state.token) {
    showPairScreen();
    $('#pair-host').value = location.host;
    if (pairCode) $('#pair-code').value = pairCode;
    return;
  }

  // We have a token — load state and connect.
  try {
    const st = await api('/api/state');
    state.deviceName = st.device_name;
    state.shells = st.shells.length ? st.shells : ['powershell'];
    state.shortcuts = st.shortcuts || [];
    (st.jobs || []).forEach((j) => state.jobs.set(j.id, j));
    $('#device-name').textContent = state.deviceName;
    populateShells();
    renderShortcuts();
    renderJobs();
    applyMetrics(st.metrics);
    renderSysInfo(st.info);
    showApp();
    connectWS();
  } catch (e) {
    if (e.message === 'unauthorized') return; // logout already triggered
    // Likely offline; still show app shell and try to reconnect.
    showApp();
    populateShells();
    renderShortcuts();
    connectWS();
  }
}

/* ---------------- event wiring ---------------- */
function wire() {
  $('#pair-btn').onclick = async () => {
    const err = $('#pair-error');
    err.classList.add('hidden');
    try {
      await tryPair($('#pair-host').value, $('#pair-code').value.trim());
      boot();
    } catch (e) {
      err.textContent = e.message; err.classList.remove('hidden');
    }
  };

  $$('.tab').forEach((t) => t.onclick = () => switchTab(t.dataset.tab));

  $('#chat-send').onclick = sendChat;
  $('#chat-input').addEventListener('keydown', (e) => { if (e.key === 'Enter') sendChat(); });

  $('#term-send').onclick = sendTerm;
  $('#term-input').addEventListener('keydown', (e) => { if (e.key === 'Enter') sendTerm(); });

  $('#new-job-btn').onclick = openJobModal;

  $('#upload-btn').onclick = () => $('#upload-input').click();
  $('#upload-input').onchange = (e) => { if (e.target.files[0]) uploadFile(e.target.files[0]); e.target.value = ''; };

  // keep-alive ping
  setInterval(() => wsSend({ type: 'ping' }), 25000);
}

// Service worker (offline shell) — optional, ignore failures.
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/sw.js').catch(() => {});
}

wire();
boot();
