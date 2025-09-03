(() => {
  const $ = (sel) => document.querySelector(sel);
  const video = $('#video');
  const preloadVideo = $('#preloadVideo');
  const btnOk = $('#btnOk');
  const btnNotOk = $('#btnNotOk');
  const btnSkip = $('#btnSkip');
  const btnReplay = $('#btnReplay');
  const btnUndo = $('#btnUndo');
  const progress = $('#progress');
  const userDisplay = $('#userDisplay');
  const userModal = $('#userModal');
  const userInput = $('#userInput');
  const userSave = $('#userSave');
  const speedDown = $('#speedDown');
  const speedUp = $('#speedUp');
  const speedDisplay = $('#speedDisplay');

  let current = null; // { id, url }
  let user = null;
  let busy = false;
  let speed = parseFloat(localStorage.getItem('fvl_speed') || '1.0');
  let lastMyRemaining = null;
  let lastMyTarget = null;
  let lastTotal = null;

  function clampSpeed(v) { return Math.min(3.0, Math.max(0.25, v)); }
  function applySpeed() {
    if (video) video.playbackRate = speed;
    if (preloadVideo) preloadVideo.playbackRate = speed;
    if (speedDisplay) speedDisplay.textContent = `${speed.toFixed(2)}x`;
  }
  function setSpeed(v) {
    speed = clampSpeed(v);
    localStorage.setItem('fvl_speed', String(speed));
    applySpeed();
  }

  function getUserFromUrl() {
    const u = new URL(window.location.href);
    const name = u.searchParams.get('user');
    return name ? name.trim() : null;
  }

  function setUser(name) {
    user = name.trim();
    localStorage.setItem('fvl_user', user);
    userDisplay.textContent = `User: ${user}`;
    const u = new URL(window.location.href);
    u.searchParams.set('user', user);
    history.replaceState(null, '', u.toString());
    // Update My Labels link
    const my = document.getElementById('myLabelsLink');
    if (my) {
      const m = new URL('/my', window.location.origin);
      m.searchParams.set('user', user);
      my.href = m.toString();
    }
  }

  async function getKnownUsers() {
    try {
      const s = await apiGet('/api/stats');
      return Object.keys(s.perUser || {});
    } catch { return []; }
  }

  function matchCanonical(name, known) {
    const lower = name.toLowerCase();
    for (const k of known) {
      if (k.toLowerCase() === lower) return k; // canonical existing
    }
    return null;
  }

  async function ensureUser() {
    const fromUrl = getUserFromUrl();
    const fromLS = localStorage.getItem('fvl_user');
    const initial = fromUrl || fromLS;
    if (initial) {
      const known = await getKnownUsers();
      const canonical = matchCanonical(initial, known);
      if (!canonical) {
        // New user — ask confirmation
        const ok = window.confirm(`Create new user "${initial}"?`);
        if (!ok) {
          userModal.style.display = 'grid';
          userInput.value = initial;
          userInput.focus();
          return;
        }
        setUser(initial);
      } else {
        if (canonical !== initial) {
          const ok = window.confirm(`Use existing user "${canonical}" instead of "${initial}"?`);
          setUser(ok ? canonical : initial);
        } else {
          setUser(initial);
        }
      }
      userModal.style.display = 'none';
    } else {
      userModal.style.display = 'grid';
      userInput.focus();
    }
  }

  async function apiGet(path) {
    const res = await fetch(path, { cache: 'no-store' });
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

  async function updateStats() {
    try {
      const [s, me] = await Promise.all([
        apiGet('/api/stats'),
        user ? apiGet(`/api/mystats?user=${encodeURIComponent(user)}`) : Promise.resolve(null)
      ]);
      // Detect workload changes for this user
      if (me && me.user) {
        const curRemaining = me.user.remaining;
        const curTarget = me.user.target;
        const curTotal = s.total;
        const hasPrev = lastMyRemaining !== null && lastMyTarget !== null && lastTotal !== null;
        const changed = hasPrev && (curRemaining !== lastMyRemaining || curTarget !== lastMyTarget || curTotal !== lastTotal);
        if (changed && me.user.labeled < curTarget) {
          let cause = '';
          if (curTotal !== lastTotal) cause = 'Dataset changed';
          else if (curTarget !== lastMyTarget) cause = 'Team changed';
          else cause = 'Workload updated';
          const dir = curRemaining < lastMyRemaining ? 'decreased' : (curRemaining > lastMyRemaining ? 'increased' : 'updated');
          showToast(`${cause}: your remaining ${dir} to ${curRemaining}`);
        }
        lastMyRemaining = curRemaining;
        lastMyTarget = curTarget;
        lastTotal = curTotal;
      } else {
        lastTotal = s.total;
      }
      if (me && me.user) {
        const u = me.user;
        progress.textContent = `Progress: ${u.labeled}/${u.target} (remaining ${u.remaining})`;
      } else {
        progress.textContent = `Progress: ${s.labeled}/${s.total} (remaining ${s.remaining})`;
      }
      // Team summary and per-user list
      const teamSummary = document.getElementById('teamSummary');
      const perUserList = document.getElementById('perUserList');
      if (teamSummary && perUserList) {
        const entries = Object.entries(s.perUser || {});
        entries.sort((a,b)=>b[1]-a[1]);
        teamSummary.textContent = `Annotators: ${entries.length} — Single-label mode: ${s.singleLabelPerVideo ? 'on' : 'off'}`;
        perUserList.innerHTML = '';
        entries.forEach(([u, c]) => {
          const div = document.createElement('div');
          div.className = 'peruser-item' + (u === user ? ' me' : '');
          const name = document.createElement('div');
          name.className = 'peruser-name';
          name.textContent = u;
          const count = document.createElement('div');
          count.className = 'peruser-count';
          count.textContent = c;
          div.appendChild(name); div.appendChild(count);
          perUserList.appendChild(div);
        });
      }
    } catch (e) {
      progress.textContent = 'Progress: …';
    }
  }

  function setVideoSource(url) {
    video.src = url;
    video.currentTime = 0;
    // With muted+autoplay+playsinline, most browsers will auto-play
    video.play().catch(() => {});
    applySpeed();
  }

  async function preloadNext() {
    try {
      const peek = await apiGet(`/api/peek?user=${encodeURIComponent(user)}`);
      if (!peek.done && peek.url) {
        preloadVideo.src = peek.url;
        // Kick off preload with a tiny play attempt
        preloadVideo.load();
      }
    } catch (e) {
      // ignore
    }
  }

  async function getNext() {
    if (!user) return;
    busy = true;
    try {
      const res = await apiGet(`/api/next?user=${encodeURIComponent(user)}`);
      if (res.done) {
        current = null;
        video.removeAttribute('src');
        video.load();
        alert('All videos labeled. Great job!');
        return;
      }
      current = { id: res.id, url: res.url };
      setVideoSource(current.url);
      updateStats();
      preloadNext();
    } catch (e) {
      console.error(e);
      alert('Failed to get next video');
    } finally {
      busy = false;
    }
  }

  async function sendLabel(label) {
    if (!current || busy) return;
    busy = true;
    try {
      const body = {
        id: current.id,
        user,
        label,
        time_ms: Math.round((video.currentTime || 0) * 1000),
        duration_ms: Math.round((video.duration || 0) * 1000),
      };
      await apiPost('/api/label', body);
      await getNext();
    } catch (e) {
      console.error(e);
      alert('Failed to submit label');
    } finally {
      busy = false;
    }
  }

  async function skip() {
    if (!current || busy) return;
    busy = true;
    try {
      await apiPost('/api/skip', { id: current.id, user });
      await getNext();
    } catch (e) {
      console.error(e);
    } finally {
      busy = false;
    }
  }

  // UI events
  btnOk.addEventListener('click', () => sendLabel('ok'));
  btnNotOk.addEventListener('click', () => sendLabel('not_ok'));
  btnSkip.addEventListener('click', skip);
  btnReplay.addEventListener('click', () => {
    if (!video.src) return;
    video.currentTime = 0;
    video.play().catch(() => {});
  });

  // Undo last label immediately (press multiple times to undo multiple)
  let undoBusy = false;
  let toastRoot = null;
  async function undoOne() {
    if (!user || undoBusy) return;
    undoBusy = true;
    if (btnUndo) btnUndo.disabled = true;
    const prev = current ? { ...current } : null;
    try {
      const res = await apiPost('/api/undo', { user, count: 1 });
      updateStats();
      const ids = res.ids || [];
      const lastId = ids.length ? ids[ids.length - 1] : null;
      if (res.ok && res.undone > 0 && lastId) {
        // Release currently assigned clip so we don't hold locks while relabeling previous
        try {
          if (prev && prev.id) await apiPost('/api/skip', { id: prev.id, user });
        } catch {}
        // Switch player to previous (undone) clip
        try { video.pause(); } catch {}
        try { video.removeAttribute('src'); video.load(); } catch {}
        try { preloadVideo.removeAttribute('src'); preloadVideo.load(); } catch {}
        // Use a cache buster to force reload
        current = { id: lastId, url: `/videos/${encodeURIComponent(lastId)}?rev=${Date.now()}` };
        setVideoSource(current.url);
        preloadNext();
        showUndoToast(res.undone, ids);
      } else {
        showToast('Nothing to undo');
      }
    } catch (e) {
      console.error('undo failed', e);
      alert('Undo failed');
    } finally {
      undoBusy = false;
      if (btnUndo) btnUndo.disabled = false;
    }
  }
  if (btnUndo) btnUndo.addEventListener('click', undoOne);

  // Speed controls
  if (speedDown) speedDown.addEventListener('click', () => setSpeed(speed - 0.25));
  if (speedUp) speedUp.addEventListener('click', () => setSpeed(speed + 0.25));

  document.addEventListener('keydown', (e) => {
    if (userModal.style.display !== 'none') return;
    if (e.key === 'k' || e.key === 'K') return sendLabel('ok');
    if (e.key === 'j' || e.key === 'J') return sendLabel('not_ok');
    if (e.key === 's' || e.key === 'S') return skip();
    if (e.key === 'r' || e.key === 'R') {
      e.preventDefault();
      video.currentTime = 0;
      video.play().catch(() => {});
      return;
    }
    if (e.ctrlKey && (e.key === 'z' || e.key === 'Z')) { e.preventDefault(); return undoOne(); }
    if (e.key === 'u' || e.key === 'U') { e.preventDefault(); return undoOne(); }
    if (e.code === 'Space') {
      e.preventDefault();
      if (video.paused) video.play().catch(() => {});
      else video.pause();
      return;
    }
    if (e.key === 'ArrowLeft') {
      e.preventDefault();
      video.currentTime = Math.max(0, (video.currentTime || 0) - 0.5);
      return;
    }
    if (e.key === 'ArrowRight') {
      e.preventDefault();
      video.currentTime = Math.min((video.duration || 0), (video.currentTime || 0) + 0.5);
      return;
    }
  });

  // User modal
  userSave.addEventListener('click', async () => {
    const name = userInput.value.trim();
    if (!name) return;
    const known = await getKnownUsers();
    const canonical = matchCanonical(name, known);
    if (!canonical) {
      const ok = window.confirm(`Create new user "${name}"?`);
      if (!ok) return;
      setUser(name);
    } else if (canonical !== name) {
      const ok = window.confirm(`Use existing user "${canonical}" instead of "${name}"?`);
      setUser(ok ? canonical : name);
    } else {
      setUser(name);
    }
    userModal.style.display = 'none';
    getNext();
    updateStats();
  });
  userInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') userSave.click();
  });

  // boot
  ensureUser().then(() => {
    if (user) {
      getNext();
      updateStats();
    }
  });
  applySpeed();
  // Periodic team progress refresh
  setInterval(updateStats, 3000);

  // Autoplay when in viewport; pause when not visible
  try {
    const io = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) video.play().catch(() => {});
        else video.pause();
      });
    }, { threshold: 0.25 });
    io.observe(video);
  } catch {}

  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') video.play().catch(() => {});
    else video.pause();
  });

  // Allow clicking the user label to change user
  if (userDisplay) {
    userDisplay.style.cursor = 'pointer';
    userDisplay.title = 'Click to change user';
    userDisplay.addEventListener('click', () => {
      userModal.style.display = 'grid';
      userInput.value = user || '';
      userInput.focus();
      userInput.select();
    });
  }

  // Toast helpers
  function ensureToastRoot() {
    if (!toastRoot) {
      toastRoot = document.createElement('div');
      toastRoot.className = 'toast-container';
      document.body.appendChild(toastRoot);
    }
    return toastRoot;
  }
  function showToast(message, actions = []) {
    const root = ensureToastRoot();
    const el = document.createElement('div');
    el.className = 'toast';
    const span = document.createElement('span');
    span.textContent = message;
    el.appendChild(span);
    actions.forEach(({ label, handler }) => {
      const btn = document.createElement('button');
      btn.textContent = label;
      btn.addEventListener('click', async () => {
        try { await handler(); } finally { root.removeChild(el); }
      });
      el.appendChild(btn);
    });
    root.appendChild(el);
    setTimeout(() => { if (el.parentNode === root) root.removeChild(el); }, 5000);
  }
  async function redoLast() {
    try {
      const res = await apiPost('/api/redo', { user });
      if (res && res.ok) {
        showToast(`Redone ${res.redone} label(s)`);
        updateStats();
      }
    } catch (e) { console.error(e); showToast('Redo failed'); }
  }
  function showUndoToast(n, ids) {
    if (!n) return;
    const short = ids && ids.length ? ids[ids.length - 1] : '';
    const msg = n === 1 ? `Undid 1 label ${short ? `(${short})` : ''}` : `Undid ${n} labels`;
    showToast(msg, [{ label: 'Redo', handler: redoLast }]);
  }
})();
