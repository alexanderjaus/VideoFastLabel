(() => {
  const $ = (s) => document.querySelector(s);
  const loginPanel = $('#loginPanel');
  const reviewPanel = $('#reviewPanel');
  const loginBtn = $('#loginBtn');
  const logoutBtn = $('#logoutBtn');
  const password = $('#password');
  const loginError = $('#loginError');
  const userFilter = $('#userFilter');
  const searchId = $('#searchId');
  const refreshBtn = $('#refreshBtn');
  const summary = $('#summary');
  const tableBody = $('#labelsTable tbody');

  async function apiGet(path) {
    const res = await fetch(path, { cache: 'no-store' });
    if (res.status === 401) throw new Error('unauthorized');
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  }
  async function apiPost(path, body) {
    const res = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  }

  async function tryAuthState() {
    try {
      const data = await apiGet('/api/users');
      populateUsers(data.perUser);
      summary.textContent = `Users: ${Object.keys(data.perUser).length}, Total videos: ${data.totalVideos}`;
      loginPanel.style.display = 'none';
      reviewPanel.style.display = '';
      await loadLabels();
    } catch (e) {
      loginPanel.style.display = '';
      reviewPanel.style.display = 'none';
    }
  }

  function populateUsers(map) {
    userFilter.innerHTML = '';
    const optAll = document.createElement('option');
    optAll.value = '';
    optAll.textContent = 'All users';
    userFilter.appendChild(optAll);
    Object.entries(map).sort((a,b)=>b[1]-a[1]).forEach(([user, count]) => {
      const opt = document.createElement('option');
      opt.value = user;
      opt.textContent = `${user} (${count})`;
      userFilter.appendChild(opt);
    });
  }

  function fmtMs(ms) {
    if (!ms && ms !== 0) return '';
    const s = Math.round(ms/100)/10;
    return `${s.toFixed(1)}s`;
  }
  function fmtTs(ts) {
    try {
      const d = new Date(ts);
      return d.toLocaleString();
    } catch { return String(ts); }
  }

  function renderRows(items) {
    const filterUser = userFilter.value;
    const q = searchId.value.trim().toLowerCase();
    tableBody.innerHTML = '';
    const frag = document.createDocumentFragment();
    items.forEach(it => {
      if (filterUser && it.user !== filterUser) return;
      if (q && !String(it.id).toLowerCase().includes(q)) return;
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td>${fmtTs(it.ts)}</td>
        <td>${it.user || ''}</td>
        <td>${it.id}</td>
        <td><span class="pill ${it.label === 'ok' ? 'pill-ok' : 'pill-not'}">${it.label}</span></td>
        <td>${fmtMs(it.time_ms)}</td>
        <td>${fmtMs(it.duration_ms)}</td>
        <td><a href="/videos/${encodeURIComponent(it.id)}" target="_blank">open</a></td>
      `;
      frag.appendChild(tr);
    });
    tableBody.appendChild(frag);
  }

  async function loadLabels() {
    const u = userFilter.value ? `?user=${encodeURIComponent(userFilter.value)}` : '';
    const data = await apiGet(`/api/labels${u}`);
    renderRows(data.items || []);
  }

  loginBtn.addEventListener('click', async () => {
    loginError.style.display = 'none';
    try {
      await apiPost('/api/reviewer/login', { password: password.value });
      password.value = '';
      await tryAuthState();
    } catch (e) {
      loginError.textContent = 'Login failed';
      loginError.style.display = '';
    }
  });
  password.addEventListener('keydown', (e) => { if (e.key === 'Enter') loginBtn.click(); });
  logoutBtn.addEventListener('click', async () => {
    try { await apiPost('/api/reviewer/logout', {}); } catch {}
    loginPanel.style.display = '';
    reviewPanel.style.display = 'none';
  });

  userFilter.addEventListener('change', loadLabels);
  searchId.addEventListener('input', async () => {
    // live filter without refetch
    try {
      const u = userFilter.value ? `?user=${encodeURIComponent(userFilter.value)}` : '';
      const data = await apiGet(`/api/labels${u}`);
      renderRows(data.items || []);
    } catch {}
  });
  refreshBtn.addEventListener('click', loadLabels);

  tryAuthState();
})();

