const $ = s => document.querySelector(s);
// Math.round(v*10^d)||0 membuang tanda minus pada nilai yang dibulatkan menjadi nol (mis. -0,4 W -> -0,00 kW)
const nf = (v, d = 0) => (Math.round(Number(v) * 10 ** d) / 10 ** d || 0)
  .toLocaleString('id-ID', { minimumFractionDigits: d, maximumFractionDigits: d });
const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const DASH = '—';
const MODEL_LABEL = { pm5330: 'PM5330', sdm630: 'SDM630' };
const PHASE_MIN_V = 10; // di bawah ini fasa dianggap tidak terpasang / tidak bertegangan

const st = { page: 'listrik', devices: [], area: 'all', q: '', openId: null, editId: null,
  conns: [], dbStatus: {}, editConnId: null };

// ---------- API ----------
// Token admin dipakai untuk operasi tulis & halaman koneksi database. Diminta sekali, disimpan di browser ini.
const getToken = () => { try { return localStorage.getItem('apiToken') || ''; } catch { return ''; } };
const setToken = t => { try { localStorage.setItem('apiToken', t); } catch { /* storage diblokir: diminta lagi nanti */ } };

// Satu dialog untuk semua request yang kena 401 bersamaan (mis. polling), supaya tidak muncul berkali-kali.
let tokenAsk = null;
function askToken(errMsg) {
  if (tokenAsk) return tokenAsk;
  tokenAsk = new Promise(resolve => {
    const dlg = $('#token-dlg'), input = $('#token-input'), err = $('#token-err');
    err.textContent = errMsg || ''; err.hidden = !errMsg;
    input.value = '';
    const finish = v => { dlg.close(); tokenAsk = null; resolve(v); };
    $('#token-form').onsubmit = e => { e.preventDefault(); finish(input.value.trim() || null); };
    $('#token-cancel').onclick = () => finish(null);
    dlg.oncancel = e => { e.preventDefault(); finish(null); }; // tombol Esc
    dlg.showModal();
    input.focus();
  });
  return tokenAsk;
}

async function send(path, opts, token) {
  const res = await fetch(path, { ...opts,
    headers: { 'Content-Type': 'application/json', ...(token && { Authorization: 'Bearer ' + token }) },
    body: opts.body ? JSON.stringify(opts.body) : undefined });
  return { res, json: await res.json().catch(() => ({})) };
}

async function api(path, opts = {}) {
  let token = getToken();
  let { res, json } = await send(path, opts, token);
  while (res.status === 401) { // token kosong/salah: tanya sampai benar atau dibatalkan
    const entered = await askToken(token ? 'Token salah atau sudah diganti. Coba lagi.' : '');
    if (!entered) break;
    token = entered;
    ({ res, json } = await send(path, opts, token));
    if (res.ok) setToken(token); // simpan hanya setelah terbukti benar
  }
  if (!res.ok) throw new Error(json.error || `${path}: HTTP ${res.status}`);
  return json;
}
const load = async () => { st.devices = await api('/api/devices'); };

// ---------- turunan data ----------
const devId = d => 'PM-' + String(d.id).padStart(3, '0');
const sum = a => a.reduce((s, v) => s + (v ?? 0), 0);
const activePhases = d => d.live ? d.live.v.filter(v => v != null && v > PHASE_MIN_V).length : 0;

function summary(d) {
  const l = d.live;
  if (!l) return null;
  const vs = l.v.filter(v => v != null && v > PHASE_MIN_V);
  return { v: vs.length ? sum(vs) / vs.length : null, a: sum(l.i), kw: (l.p_total ?? sum(l.p)) / 1000,
    kva: (l.s_total ?? sum(l.s)) / 1000, freq: l.freq };
}

function status(d) {
  if (!d.enabled) return '<span class="pill off">Nonaktif</span>';
  if (!d.live) return `<span class="pill bad" title="${esc(d.error || 'menunggu data pertama')}">Offline</span>`;
  if (activePhases(d) < 3) return `<span class="pill warn">Fase tidak lengkap (${activePhases(d)}/3)</span>`;
  return '<span class="pill ok">Normal</span>';
}

// ---------- halaman Listrik ----------
function buildPage() {
  $('#page').innerHTML = `
  <div class="grid g4" id="kpi"></div>
  <div class="card mt">
    <div class="toolbar">
      <div class="tabs" id="area-tabs"></div>
      <span style="margin-left:auto"></span>
      <input id="q" placeholder="Cari titik…" style="width:200px">
      <button id="btn-add">+ Tambah perangkat</button>
    </div>
    <div class="table-wrap"><table>
      <thead><tr><th>ID</th><th>Titik ukur</th><th>Area</th><th>Model</th><th>Modbus TCP</th><th class="num">Tegangan (V)</th><th class="num">Arus (A)</th>
        <th class="num">Daya (kW)</th><th class="num">Daya semu (kVA)</th><th class="num">Frekuensi (Hz)</th><th>Status</th><th></th></tr></thead>
      <tbody id="rows"></tbody>
    </table></div>
    <div class="muted small mt">Klik baris untuk melihat parameter real-time 3 fasa. Tegangan = rata-rata fasa yang bertegangan; arus, daya, dan daya semu = total 3 fasa.</div>
  </div>`;
  $('#q').oninput = e => { st.q = e.target.value; renderRows(); };
  $('#btn-add').onclick = () => openForm(null);
}

function renderAll() {
  renderKpi(); renderTabs(); renderRows(); renderDrawer();
}

function renderKpi() {
  const ok = st.devices.filter(d => d.live);
  const kw = sum(ok.map(d => summary(d).kw)), kva = sum(ok.map(d => summary(d).kva));
  const enabled = st.devices.filter(d => d.enabled).length;
  $('#kpi').innerHTML = `
    <div class="card kpi elec"><div class="label">Titik ukur</div><div class="value">${st.devices.length}</div><div class="sub">${enabled} aktif</div></div>
    <div class="card kpi elec"><div class="label">Meter online</div><div class="value">${ok.length}<span class="unit">/ ${enabled}</span></div></div>
    <div class="card kpi elec"><div class="label">Total daya saat ini</div><div class="value">${nf(kw, 2)}<span class="unit">kW</span></div></div>
    <div class="card kpi elec"><div class="label">Total daya semu</div><div class="value">${nf(kva, 2)}<span class="unit">kVA</span></div></div>`;
}

function renderTabs() {
  const areas = [...new Set(st.devices.map(d => d.area).filter(Boolean))].sort();
  if (st.area !== 'all' && !areas.includes(st.area)) st.area = 'all';
  $('#area-tabs').innerHTML = `<button data-a="all" class="${st.area === 'all' ? 'on' : ''}">Semua (${st.devices.length})</button>` +
    areas.map(a => `<button data-a="${esc(a)}" class="${st.area === a ? 'on' : ''}">${esc(a)}</button>`).join('');
  document.querySelectorAll('#area-tabs button').forEach(b => b.onclick = () => { st.area = b.dataset.a; renderTabs(); renderRows(); });
}

function renderRows() {
  const q = st.q.toLowerCase();
  const rows = st.devices.filter(d => (st.area === 'all' || d.area === st.area) &&
    (d.name + devId(d) + d.host).toLowerCase().includes(q));
  $('#rows').innerHTML = rows.length ? rows.map(d => {
    const s = summary(d), cell = (v, dec) => s && v != null ? nf(v, dec) : DASH;
    return `<tr class="click ${d.id === st.openId ? 'sel' : ''}" data-id="${d.id}"><td>${devId(d)}</td><td><strong>${esc(d.name)}</strong></td>
      <td>${esc(d.area) || DASH}</td><td>${MODEL_LABEL[d.model] || esc(d.model)}</td><td>${esc(d.host)}:${d.port} · #${d.unit_id}</td>
      <td class="num">${cell(s?.v, 1)}</td><td class="num">${cell(s?.a, 2)}</td>
      <td class="num"><strong>${cell(s?.kw, 2)}</strong></td><td class="num">${cell(s?.kva, 2)}</td>
      <td class="num">${cell(s?.freq, 2)}</td><td>${status(d)}</td>
      <td><button class="sm ghost" data-act="edit">Edit</button> <button class="sm danger" data-act="del">Hapus</button></td></tr>`;
  }).join('') : `<tr><td colspan="12" class="muted">${st.devices.length ? 'Tidak ada titik yang cocok.' : 'Belum ada perangkat. Klik “Tambah perangkat”.'}</td></tr>`;

  document.querySelectorAll('#rows tr.click').forEach(tr => {
    const d = st.devices.find(x => x.id === +tr.dataset.id);
    tr.onclick = () => { st.openId = d.id; $('#drawer').hidden = false; renderDrawer(); renderRows(); };
    tr.querySelector('[data-act=edit]').onclick = e => { e.stopPropagation(); openForm(d); };
    tr.querySelector('[data-act=del]').onclick = e => { e.stopPropagation(); removeDevice(d); };
  });
}

// ---------- right bar: parameter real-time ----------
function renderDrawer() {
  const d = st.devices.find(x => x.id === st.openId);
  if (!d) { $('#drawer').hidden = true; return; }
  const l = d.live, s = summary(d);
  $('#drawer-title').textContent = `${devId(d)} · ${d.name}`;

  const phaseRow = (label, unit, arr, total, dec) => `<tr><td>${label} <span class="muted small">${unit}</span></td>
    ${[0, 1, 2].map(k => `<td class="num">${l && arr[k] != null ? nf(arr[k], dec) : DASH}</td>`).join('')}
    <td class="num"><strong>${l && total != null ? nf(total, dec) : DASH}</strong></td></tr>`;
  const age = d.ts ? Math.max(0, Math.round(Date.now() / 1000 - d.ts)) : null;

  $('#drawer-body').innerHTML = `
    <div class="muted">${esc(d.area) || 'Tanpa area'} · ${MODEL_LABEL[d.model] || esc(d.model)} · ${esc(d.host)}:${d.port} · Unit ${d.unit_id} · ${d.function === 'input' ? 'FC4' : 'FC3'}</div>
    <div class="mt">${status(d)}${!l && d.error && d.enabled ? `<div class="muted small mt">${esc(d.error)}</div>` : ''}</div>
    <div class="grid g3 mt">
      <div class="tile"><div class="muted small">Daya</div><div class="v">${s ? nf(s.kw, 2) : DASH} <span class="unit">kW</span></div></div>
      <div class="tile"><div class="muted small">Daya semu</div><div class="v">${s ? nf(s.kva, 2) : DASH} <span class="unit">kVA</span></div></div>
      <div class="tile"><div class="muted small">Frekuensi</div><div class="v">${s && s.freq != null ? nf(s.freq, 2) : DASH} <span class="unit">Hz</span></div></div>
    </div>
    <div class="card mt"><h3>Parameter real-time <span class="muted small">${age != null ? 'diperbarui ' + age + ' detik lalu' : ''}</span></h3>
      <div class="table-wrap"><table>
        <thead><tr><th>Parameter</th><th class="num">L1</th><th class="num">L2</th><th class="num">L3</th><th class="num">Total</th></tr></thead>
        <tbody>
          ${phaseRow('Tegangan', 'V', l?.v ?? [], s?.v, 1)}
          ${phaseRow('Arus', 'A', l?.i ?? [], s && s.a, 3)}
          ${phaseRow('Daya aktif', 'W', l?.p ?? [], l?.p_total, 1)}
          ${phaseRow('Daya semu', 'VA', l?.s ?? [], l?.s_total, 1)}
        </tbody></table></div>
      <div class="row mt"><span class="muted">Frekuensi</span><strong>${l && l.freq != null ? nf(l.freq, 2) + ' Hz' : DASH}</strong></div>
      <div class="muted small mt">Total tegangan = rata-rata fasa yang bertegangan (&gt; ${PHASE_MIN_V} V).</div>
    </div>
    <div class="toolbar mt"><button class="ghost" id="dr-edit">Edit</button><button class="danger" id="dr-del">Hapus</button></div>`;
  $('#dr-edit').onclick = () => openForm(d);
  $('#dr-del').onclick = () => removeDevice(d);
}

$('#drawer-close').onclick = () => { st.openId = null; $('#drawer').hidden = true; renderRows(); };
document.addEventListener('keydown', e => { if (e.key === 'Escape' && !$('#form-dlg').open) $('#drawer-close').click(); });

// ---------- CRUD ----------
const FORM_FIELDS = ['name', 'area', 'model', 'host', 'port', 'unit_id', 'function', 'byte_order', 'interval_s'];
const DEFAULTS = { name: '', area: '', model: 'pm5330', host: '', port: 502, unit_id: 1, function: 'holding', byte_order: 'big', interval_s: 2, enabled: true };
// Default pembacaan tiap model meter; dipakai saat model diganti di form.
const MODEL_DEFAULTS = { pm5330: { function: 'holding', byte_order: 'big' }, sdm630: { function: 'input', byte_order: 'big' } };
$('#f-model').onchange = e => Object.entries(MODEL_DEFAULTS[e.target.value]).forEach(([f, val]) => { $('#f-' + f).value = val; });

function openForm(d) {
  st.editId = d ? d.id : null;
  const v = d || DEFAULTS;
  $('#form-title').textContent = d ? `Edit ${devId(d)}` : 'Tambah perangkat';
  FORM_FIELDS.forEach(f => { $('#f-' + f).value = v[f]; });
  $('#f-enabled').checked = !!v.enabled;
  $('#form-err').hidden = true;
  $('#form-dlg').showModal();
}

$('#form-cancel').onclick = () => $('#form-dlg').close();
$('#form').onsubmit = async e => {
  e.preventDefault();
  const body = Object.fromEntries(FORM_FIELDS.map(f => [f, $('#f-' + f).value]));
  body.enabled = $('#f-enabled').checked;
  try {
    if (st.editId) await api('/api/devices/' + st.editId, { method: 'PUT', body });
    else st.openId = (await api('/api/devices', { method: 'POST', body })).id;
    $('#form-dlg').close();
    await load();
    if (st.openId) $('#drawer').hidden = false;
    renderAll();
  } catch (err) {
    $('#form-err').textContent = err.message; $('#form-err').hidden = false;
  }
};

async function removeDevice(d) {
  if (!confirm(`Hapus ${devId(d)} · ${d.name}? Konfigurasi perangkat akan hilang.`)) return;
  try {
    await api('/api/devices/' + d.id, { method: 'DELETE' });
    if (st.openId === d.id) st.openId = null;
    await load(); renderAll();
  } catch (err) { alert('Gagal menghapus: ' + err.message); }
}

// ---------- halaman Koneksi Database ----------
const loadConns = async () => { const r = await api('/api/db-connections'); st.conns = r.connections; st.dbStatus = r.status; };

function buildDatabase() {
  $('#page').innerHTML = `
  <div class="grid g4" id="db-kpi"></div>
  <div class="card mt">
    <div class="toolbar"><h3 style="margin:0">Koneksi database MySQL</h3><span style="margin-left:auto"></span>
      <button id="btn-add-conn">+ Tambah koneksi</button></div>
    <div class="table-wrap"><table>
      <thead><tr><th>Nama</th><th>Host : Port</th><th>Database</th><th>User</th><th class="num">Interval simpan</th><th>Status</th><th></th></tr></thead>
      <tbody id="conn-rows"></tbody>
    </table></div>
    <div class="muted small mt">Hanya satu koneksi yang aktif. Saat diaktifkan, database dan tabel <code>power_readings</code> dibuat otomatis bila belum ada,
      lalu pembacaan semua meter disimpan sesuai interval. Selama server database tidak terjangkau, data pada rentang itu tidak tersimpan.
      Password disimpan apa adanya di <code>meters.db</code> pada komputer ini dan tidak pernah dikirim kembali ke browser.</div>
  </div>`;
  $('#btn-add-conn').onclick = () => openConnForm(null);
}

function connStatus(c) {
  if (!c.active) return '<span class="pill off">Nonaktif</span>';
  const s = st.dbStatus;
  if (s.state === 'ok') return '<span class="pill ok">Terhubung · menyimpan</span>';
  if (s.state === 'error') return `<span class="pill bad" title="${esc(s.error || '')}">Gagal</span>`;
  return '<span class="pill warn">Menghubungkan…</span>';
}

function renderDb() {
  const active = st.conns.find(c => c.active), s = st.dbStatus;
  const last = s.last_write ? new Date(s.last_write * 1000).toLocaleTimeString('id-ID') : DASH;
  $('#db-kpi').innerHTML = `
    <div class="card kpi elec"><div class="label">Koneksi aktif</div><div class="value" style="font-size:20px">${active ? esc(active.name) : 'Tidak ada'}</div>
      <div class="sub">${active ? esc(active.host) + ' · ' + esc(active.db_name) : 'Aktifkan satu koneksi untuk mulai menyimpan'}</div></div>
    <div class="card kpi elec"><div class="label">Status</div><div class="value" style="font-size:20px">${active ? connStatus(active) : DASH}</div>
      <div class="sub">${s.state === 'error' ? esc(s.error) : '&nbsp;'}</div></div>
    <div class="card kpi elec"><div class="label">Terakhir menyimpan</div><div class="value" style="font-size:22px">${last}</div></div>
    <div class="card kpi elec"><div class="label">Baris tersimpan</div><div class="value">${nf(s.rows_total || 0)}</div><div class="sub">${s.pending ? nf(s.pending) + ' menunggu terkirim' : 'sejak koneksi diaktifkan'}</div></div>`;

  $('#conn-rows').innerHTML = st.conns.length ? st.conns.map(c => `<tr data-id="${c.id}">
    <td><strong>${esc(c.name)}</strong></td><td>${esc(c.host)}:${c.port}</td><td>${esc(c.db_name)}</td><td>${esc(c.db_user)}</td>
    <td class="num">${nf(c.store_interval_s, c.store_interval_s % 1 ? 1 : 0)} dtk</td><td>${connStatus(c)}</td>
    <td><button class="sm" data-act="toggle">${c.active ? 'Nonaktifkan' : 'Aktifkan'}</button>
      <button class="sm ghost" data-act="test">Tes</button> <button class="sm ghost" data-act="edit">Edit</button>
      <button class="sm danger" data-act="del">Hapus</button></td></tr>`).join('')
    : '<tr><td colspan="7" class="muted">Belum ada koneksi. Klik “Tambah koneksi”.</td></tr>';

  document.querySelectorAll('#conn-rows tr[data-id]').forEach(tr => {
    const c = st.conns.find(x => x.id === +tr.dataset.id);
    tr.querySelector('[data-act=edit]').onclick = () => openConnForm(c);
    tr.querySelector('[data-act=del]').onclick = () => removeConn(c);
    tr.querySelector('[data-act=toggle]').onclick = e => toggleConn(c, e.target);
    tr.querySelector('[data-act=test]').onclick = e => testSaved(c, e.target);
  });
}

async function refreshDb() { await loadConns(); renderDb(); }

async function toggleConn(c, btn) {
  btn.disabled = true; btn.textContent = 'Memproses…';
  try { await api(`/api/db-connections/${c.id}/${c.active ? 'deactivate' : 'activate'}`, { method: 'POST' }); }
  catch (err) { alert(err.message); }
  await refreshDb();
}

async function testSaved(c, btn) {
  btn.disabled = true; btn.textContent = 'Menguji…';
  try { alert(describeTest(await api('/api/db-connections/test', { method: 'POST', body: { ...c, id: c.id } }))); }
  catch (err) { alert(err.message); }
  btn.disabled = false; btn.textContent = 'Tes';
}

function describeTest(r) {
  if (!r.ok) return 'Gagal: ' + r.error;
  return `Terhubung ke server MySQL ${r.version}.\n` + (r.database_exists ? 'Database sudah ada.' : 'Database belum ada — akan dibuat saat koneksi diaktifkan.');
}

async function removeConn(c) {
  if (!confirm(`Hapus koneksi “${c.name}”?${c.active ? ' Penyimpanan data akan berhenti.' : ''}`)) return;
  try { await api('/api/db-connections/' + c.id, { method: 'DELETE' }); } catch (err) { alert(err.message); }
  await refreshDb();
}

const CONN_FIELDS = ['name', 'host', 'port', 'db_user', 'db_name', 'store_interval_s'];
const CONN_DEFAULTS = { name: '', host: '', port: 3306, db_user: '', db_name: 'ois_sosro_pw', store_interval_s: 60 };

function openConnForm(c) {
  st.editConnId = c ? c.id : null;
  const v = c || CONN_DEFAULTS;
  $('#db-form-title').textContent = c ? `Edit koneksi “${c.name}”` : 'Tambah koneksi database';
  CONN_FIELDS.forEach(f => { $('#c-' + f).value = v[f]; });
  $('#c-db_password').value = '';
  $('#c-pw-hint').hidden = !(c && c.has_password);
  $('#c-result').hidden = true;
  $('#db-dlg').showModal();
}

const connBody = () => ({ ...Object.fromEntries(CONN_FIELDS.map(f => [f, $('#c-' + f).value])),
  db_password: $('#c-db_password').value, id: st.editConnId });
function showConnResult(text, ok) { const el = $('#c-result'); el.textContent = text; el.className = 'form-msg ' + (ok ? 'ok' : 'bad'); el.hidden = false; }

$('#c-cancel').onclick = () => $('#db-dlg').close();
$('#c-test').onclick = async e => {
  e.target.disabled = true; showConnResult('Menguji koneksi…', true);
  try { const r = await api('/api/db-connections/test', { method: 'POST', body: connBody() }); showConnResult(describeTest(r), r.ok); }
  catch (err) { showConnResult(err.message, false); }
  e.target.disabled = false;
};
$('#db-form').onsubmit = async e => {
  e.preventDefault();
  try {
    const body = connBody();
    if (st.editConnId) await api('/api/db-connections/' + st.editConnId, { method: 'PUT', body });
    else await api('/api/db-connections', { method: 'POST', body });
    $('#db-dlg').close();
    await refreshDb();
  } catch (err) { showConnResult(err.message, false); }
};

// ---------- router ----------
const PAGES = {
  listrik: { title: 'Listrik', build: buildPage, refresh: async () => { await load(); renderKpi(); renderRows(); renderDrawer(); },
    first: async () => { await load(); renderAll(); } },
  database: { title: 'Koneksi Database', build: buildDatabase, refresh: refreshDb, first: refreshDb },
};

async function go() {
  st.page = PAGES[(location.hash || '#listrik').slice(1)] ? (location.hash || '#listrik').slice(1) : 'listrik';
  const p = PAGES[st.page];
  document.querySelectorAll('nav a').forEach(a => a.classList.toggle('active', a.dataset.page === st.page));
  $('#page-title').textContent = p.title;
  st.openId = null;
  $('#drawer').hidden = true;
  p.build();
  try { await p.first(); } catch (e) { showBackendError(e); }
}

function showBackendError(e) {
  $('#page').innerHTML = `<div class="card"><h3>Tidak dapat terhubung ke backend</h3><p class="muted">${esc(e.message)}</p>
    <p>Jalankan <code>python server.py</code> di folder <code>dashboard</code>.</p></div>`;
}

// ---------- start ----------
setInterval(() => { $('#clock').textContent = new Date().toLocaleString('id-ID', { dateStyle: 'medium', timeStyle: 'medium' }); }, 1000);
window.addEventListener('hashchange', go);
$('#data-source').textContent = 'Sumber: Modbus TCP langsung';
go();
setInterval(async () => {
  if (document.querySelector('dialog[open]')) return; // jangan ganggu form yang sedang diisi
  try { await PAGES[st.page].refresh(); } catch { /* backend sementara putus; coba lagi pada siklus berikutnya */ }
}, 2000);
