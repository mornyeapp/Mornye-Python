// palette.js — command palette and keyboard shortcuts (N1, docs/redesign-plan.md).
//
// Ctrl+K / Cmd+K opens a list of everything the rail, Settings and the main
// actions do, filtered as you type (every word of the query must appear, in
// any order). ↑/↓ move, Enter runs, Escape closes. Outside a text field
// `/` focuses the link/search bar — the TUI's `/` — and `?` opens the
// palette too, which lists the shortcuts beside the commands.
//
// Loaded after app.js (boot-tail.js): every command calls a function app.js
// already exposes; none is new behaviour.

(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const isMac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || '');
  const MOD = isMac ? '⌘' : 'Ctrl+';

  const view = (name) => () => window.switchView(name);
  const settingsTab = (tab) => () => {
    window.switchView('settings');
    const btn = document.querySelector(`.stab[data-onclick*="'${tab}'"]`);
    if (btn) window.switchTab(tab, btn);
  };
  const setTheme = (mode) => () => {
    const sel = $('config-theme');
    if (!sel) return;
    sel.value = mode;
    window.changeTheme();
  };
  const cardOpen = () => !$('album-card')?.classList.contains('hidden');

  function focusBar() {
    window.switchView('home');
    const bar = $('urlInput');
    if (bar && !bar.disabled) { bar.focus(); bar.select(); }
  }

  // [label, group, run, shortcut?, available?]
  const COMMANDS = [
    ['Home', 'Go to', view('home')],
    ['Explore', 'Go to', view('explore')],
    ['Player', 'Go to', view('player')],
    ['Queue', 'Go to', view('queue')],
    ['History', 'Go to', view('history')],
    ['Fix Local Files', 'Go to', view('local')],
    ['Following', 'Go to', view('following')],
    ['Stats', 'Go to', view('stats')],
    ['Logs', 'Go to', view('logs')],
    ['Settings', 'Go to', view('settings')],
    ['Settings → General', 'Settings', settingsTab('general')],
    ['Settings → Sources', 'Settings', settingsTab('sources')],
    ['Settings → Download', 'Settings', settingsTab('download')],
    ['Settings → Files', 'Settings', settingsTab('files')],
    ['Settings → Metadata', 'Settings', settingsTab('metadata')],
    ['Settings → Status', 'Settings', settingsTab('status')],
    ['Settings → Extensions', 'Settings', settingsTab('extensions')],
    ['Save settings', 'Settings', () => window.saveSettings()],
    ['Focus the link / search bar', 'Action', focusBar, '/'],
    ['Fetch the link in the bar', 'Action', () => { window.switchView('home'); window.onFetch(); }],
    ['Switch between link and search', 'Action', () => { window.switchView('home'); window.toggleSearchMode(); }],
    ['Copy as CLI command', 'Action', () => window.copyCliCommand(), null, cardOpen],
    ['Download all tracks of the open card', 'Action', () => window.downloadAll(), null, cardOpen],
    ['Open the download queue drawer', 'Action', () => window.toggleQueueDrawer()],
    ['Start the queue', 'Action', () => { window.switchView('queue'); window.startStaged(false); }],
    ['Stop all downloads', 'Action', () => window.stopAllDownloads()],
    ['Retry failed tracks', 'Action', () => window.retryFailedTracks()],
    ['Check providers', 'Action', () => { settingsTab('status')(); window.checkAll(); }],
    ['Play / pause', 'Player', () => window.playerToggle()],
    ['Open Now Playing', 'Player', () => window.openNowPlaying()],
    ['Theme: dark', 'Appearance', setTheme('dark')],
    ['Theme: light', 'Appearance', setTheme('light')],
    ['Theme: follow the system', 'Appearance', setTheme('auto')],
    ['Collapse or expand the sidebar', 'Appearance', () => window.toggleSidebar()],
    ['Command palette', 'Help', () => {}, `${MOD}K`],
  ].map(([label, group, run, key, available]) => ({ label, group, run, key, available }));

  // ── Markup (built here: nothing in index.html depends on it) ─────────────
  const root = document.createElement('div');
  root.id = 'palette';
  root.className = 'palette hidden';
  root.setAttribute('role', 'dialog');
  root.setAttribute('aria-modal', 'true');
  root.setAttribute('aria-label', 'Command palette');
  root.innerHTML = `
    <div class="palette-backdrop"></div>
    <div class="palette-card">
      <input id="palette-input" class="palette-input" type="text" autocomplete="off" spellcheck="false"
        role="combobox" aria-expanded="true" aria-controls="palette-list" aria-autocomplete="list"
        aria-label="Command" placeholder="Type a command…">
      <ul id="palette-list" class="palette-list" role="listbox" aria-label="Commands"></ul>
      <div class="palette-foot"></div>
    </div>`;
  root.querySelector('.palette-foot').textContent =
    `↑↓ move · Enter run · Esc close · ${MOD}K anywhere · / link bar`;
  document.body.appendChild(root);
  const input = $('palette-input');
  const list = $('palette-list');
  let shown = [];
  let active = 0;
  let opener = null;

  function matches(cmd, words) {
    const hay = `${cmd.label} ${cmd.group}`.toLowerCase();
    return words.every((w) => hay.includes(w));
  }

  function render() {
    const words = input.value.toLowerCase().split(/\s+/).filter(Boolean);
    shown = COMMANDS.filter((c) => (!c.available || c.available()) && matches(c, words));
    active = Math.min(active, Math.max(0, shown.length - 1));
    list.textContent = '';
    if (!shown.length) {
      const li = document.createElement('li');
      li.className = 'palette-empty';
      li.setAttribute('role', 'presentation');
      li.textContent = 'No command matches.';
      list.appendChild(li);
    }
    shown.forEach((c, i) => {
      const li = document.createElement('li');
      li.id = `palette-opt-${i}`;
      li.className = 'palette-item';
      li.setAttribute('role', 'option');
      li.setAttribute('aria-selected', String(i === active));
      li.dataset.i = String(i);
      const label = document.createElement('span');
      label.className = 'palette-label';
      label.textContent = c.label;
      const group = document.createElement('span');
      group.className = 'palette-group';
      group.textContent = c.group;
      li.append(label, group);
      if (c.key) {
        const kbd = document.createElement('kbd');
        kbd.className = 'palette-key';
        kbd.textContent = c.key;
        li.appendChild(kbd);
      }
      list.appendChild(li);
    });
    input.setAttribute('aria-activedescendant', shown.length ? `palette-opt-${active}` : '');
    list.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: 'nearest' });
  }

  function open() {
    if (!root.classList.contains('hidden')) { input.select(); return; }
    opener = document.activeElement;
    input.value = '';
    active = 0;
    render();
    root.classList.remove('hidden');
    input.focus();
  }

  function close(restoreFocus = true) {
    root.classList.add('hidden');
    if (restoreFocus && opener && document.contains(opener) && typeof opener.focus === 'function') {
      opener.focus();
    }
  }

  function run(i) {
    const cmd = shown[i];
    if (!cmd) return;
    close(false);
    try {
      cmd.run();
    } catch (err) {
      console.error('[palette]', cmd.label, err);
      window.toastMgr?.error(String((err && err.message) || err), { title: cmd.label });
    }
  }

  const move = (step) => {
    if (!shown.length) return;
    active = (active + step + shown.length) % shown.length;
    render();
  };
  input.addEventListener('input', () => { active = 0; render(); });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); move(1); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); move(-1); }
    else if (e.key === 'Enter') { e.preventDefault(); run(active); }
    else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); }
    else if (e.key === 'Tab') { e.preventDefault(); } // one field: the focus stays in the dialog
  });
  list.addEventListener('mousemove', (e) => {
    const li = e.target.closest('.palette-item');
    if (li && Number(li.dataset.i) !== active) { active = Number(li.dataset.i); render(); }
  });
  list.addEventListener('click', (e) => {
    const li = e.target.closest('.palette-item');
    if (li) run(Number(li.dataset.i));
  });
  root.querySelector('.palette-backdrop').addEventListener('click', () => close());

  function typing(target) {
    return target instanceof Element
      && target.closest('input, textarea, select, [contenteditable=""], [contenteditable="true"]') !== null;
  }
  function anotherDialogOpen() {
    return document.querySelector('.api-modal:not(.hidden), #np-view:not(.hidden)') !== null;
  }

  document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      if (!anotherDialogOpen()) open();
      return;
    }
    if (e.ctrlKey || e.metaKey || e.altKey || typing(e.target) || anotherDialogOpen()) return;
    if (!root.classList.contains('hidden')) return;
    if (e.key === '/') { e.preventDefault(); focusBar(); }
    else if (e.key === '?') { e.preventDefault(); open(); }
  });

  window.openCommandPalette = open;
})();
