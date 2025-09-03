(() => {
  const $ = (s) => document.querySelector(s);
  const video = $('#vid');
  const filterSel = $('#filter');
  const refreshBtn = $('#refresh');
  const prevBtn = $('#prevBtn');
  const nextBtn = $('#nextBtn');
  const removeBtn = $('#removeBtn');
  const who = $('#who');
  const meta = $('#meta');

  let user = null;
  let items = [];
  let idx = 0;

  function getUser() {
    const u = new URL(location.href);
    return (u.searchParams.get('user') || localStorage.getItem('fvl_user') || '').trim();
  }
  function setBackLink() {
    const back = document.getElementById('backLink');
    if (back) {
      const u = new URL('/', location.origin);
      if (user) u.searchParams.set('user', user);
      back.href = u.toString();
    }
  }
  function setVideoSource(url) {
    try { video.pause(); } catch {}
    video.src = url;
    video.currentTime = 0;
    video.play().catch(()=>{});
  }
  async function apiGet(path) {
    const res = await fetch(path, { cache: 'no-store' });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  }
  async function apiPost(path, body) {
    const res = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  }

  function renderCurrent() {
    if (!items.length) {
      meta.textContent = 'No items';
      try { video.removeAttribute('src'); video.load(); } catch {}
      return;
    }
    idx = Math.max(0, Math.min(idx, items.length - 1));
    const it = items[idx];
    who.textContent = `User: ${user} — ${filterSel.value}, ${items.length} item(s)`;
    meta.textContent = `${idx + 1}/${items.length} — ${it.id} — label: ${it.label}`;
    setVideoSource(`/videos/${encodeURIComponent(it.id)}?rev=${Date.now()}`);
  }

  async function loadList() {
    const flt = filterSel.value || 'all';
    const data = await apiGet(`/api/user_labels?user=${encodeURIComponent(user)}&label=${encodeURIComponent(flt)}&limit=5000`);
    items = data.items || [];
    idx = 0;
    renderCurrent();
  }

  async function removeCurrent() {
    if (!items.length) return;
    const it = items[idx];
    const ok = confirm(`Remove label for ${it.id}?`);
    if (!ok) return;
    await apiPost('/api/unlabel', { user, id: it.id });
    // Remove from local list and keep position sensible
    items.splice(idx, 1);
    if (idx >= items.length) idx = Math.max(0, items.length - 1);
    renderCurrent();
  }

  // Wiring
  refreshBtn.addEventListener('click', loadList);
  filterSel.addEventListener('change', loadList);
  prevBtn.addEventListener('click', () => { if (idx > 0) { idx -= 1; renderCurrent(); } });
  nextBtn.addEventListener('click', () => { if (idx < items.length - 1) { idx += 1; renderCurrent(); } });
  removeBtn.addEventListener('click', removeCurrent);

  document.addEventListener('keydown', (e) => {
    if (e.key === 'a' || e.key === 'A') { e.preventDefault(); prevBtn.click(); return; }
    if (e.key === 'd' || e.key === 'D') { e.preventDefault(); nextBtn.click(); return; }
    if (e.key === 'Delete' || e.key === 'Backspace') { e.preventDefault(); removeCurrent(); return; }
    if (e.code === 'Space') { e.preventDefault(); if (video.paused) video.play().catch(()=>{}); else video.pause(); return; }
  });

  // Boot
  user = getUser();
  if (!user) {
    alert('No user specified. Use /my?user=yourname or set it on the main page.');
  }
  setBackLink();
  loadList().catch(err => { console.error(err); alert('Failed to load list'); });
})();

