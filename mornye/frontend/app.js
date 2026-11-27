let isDirty = false;
let initialSettings = {};

// ── Detect OS and apply system-specific styles ──────────────────────────────
function detectAndApplyOSStyles() {
  // Detect OS from the user agent
  const userAgent = navigator.userAgent.toLowerCase();
  let detectedOS = 'mac'; // Default to macOS (colorful dots)

  if (userAgent.includes('win')) {
    detectedOS = 'windows';
  } else if (userAgent.includes('linux')) {
    detectedOS = 'linux';
  } else if (userAgent.includes('x11')) {
    detectedOS = 'linux';
  }

  // If this is Windows, apply the CSS class for Windows-style buttons
  if (detectedOS === 'windows') {
    document.body.classList.add('windows-style');
  }

  console.log(`[OS Detection] Detected OS: ${detectedOS}`);
}

// Run detection on page load
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', detectAndApplyOSStyles);
} else {
  detectAndApplyOSStyles();
}

// While a link is fetched or a search runs, the track table shows a small
// equalizer of bouncing bars and what is happening, in place of the old
// grey placeholder rows. The name stays for its callers; `count` is unused.
function showSkeletonTracks(count = 5, label = 'Fetching metadata…') {
  const container = $('track-rows');
  if (!container) return;
  clearArtistPageParts();
  container.innerHTML = `
    <div class="fetch-loader" role="status" aria-live="polite">
      <div class="fl-bars" aria-hidden="true"><i></i><i></i><i></i><i></i><i></i></div>
      <div class="fl-label">${escHtml(label)}</div>
    </div>`;

  $('track-table-wrap').classList.remove('hidden');
  
  // Hide the recents
  if ($('recent-wrap')) $('recent-wrap').style.display = 'none'; 
  
  // Hide the table header until the real data arrives
  const header = document.querySelector('.track-table-header');
  if (header) header.style.display = 'none';

  // ...and the previous result's page bar: it stayed under the placeholders
  // ("Page 1 of 39" from a playlist while an artist loads), and its Next
  // button put the old playlist back on screen. renderTracks() shows it
  // again when the new result has more than one page.
  updatePaginationControls(0);
}

// Initialize state after loaded settings
function initSettingsTracking() {
    initialSettings = buildConfig();
    isDirty = false;
    updateSaveButtonVisual();
}

function updateSaveButtonVisual() {
    // By id: `.s-actions .act-btn.primary` matched the Local Auto-Tagger's
    // scan button first, so the Settings button never showed the edits.
    const btn = $('settings-save-btn');
    if (!btn) return;
    if (isDirty) {
        btn.style.opacity = "1";
        btn.textContent = "Save Changes (Unsaved)";
        btn.dataset.dirty = "true"; // red border: css/legacy.css
    } else {
        btn.textContent = "Save Changes";
        btn.dataset.dirty = "false";
    }
}

function clearSearchUI() {
    const container = $('text-search-results');
    if (container) container.innerHTML = ''; // Clear the message
    $('text-search-container')?.classList.add('hidden'); // Hide the container
}
// ── Helpers ─────────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);
// A top-level const is not a window property; the markup's data-on*
// handlers (inline-handlers.js) name it, so it is registered there.
window.MornyeHandlers?.expose({ $ });
const ts = () => new Date().toLocaleTimeString('en-US');
// ── Global Initialization ──────────────────────────────────────────────────
const toastMgr = new ToastManager();
// A top-level `const` is not a property of `window`, and the settings code
// reaches the manager as `window.toastMgr`: without this line every
// "Notification sounds" change and every saved setting was dropped, and the
// sound followed only the browser's own localStorage (issue #254).
window.toastMgr = toastMgr;

// ── Keyboard reach for clickable non-buttons (F7) ───────────────────────────
// Rows and cards that act on a click are <div>s (a <button> cannot hold
// their block layout or nested buttons). Each carries tabindex="0" and
// data-activate, and Enter or Space on it is its click. Only the element
// itself: a key typed in a field or on a button inside it is left alone.
function makeActivatable(el, role = 'button') {
  el.tabIndex = 0;
  el.dataset.activate = '';
  if (role && !el.hasAttribute('role')) el.setAttribute('role', role);
  return el;
}
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Enter' && e.key !== ' ') return;
  const el = e.target;
  if (!(el instanceof Element) || !el.hasAttribute('data-activate')) return;
  if (e.repeat) return;
  e.preventDefault();
  el.click();
});

// ── Confirmation dialog (F7) ─────────────────────────────────────────────────
// askConfirm(question, {title, ok, danger}) → Promise<boolean>. Opens
// #confirm-modal in place of the native confirm(), which pywebview draws in
// the OS's own style and outside the window. Escape, the backdrop and Cancel
// answer false. A destructive question (danger) starts on Cancel, so a
// stray Enter does not delete anything; otherwise on the confirm button.
let confirmSettle = null;
// A question replaced by another one before it was answered resolves to null
// (falsy like a "no", but not one).
function askConfirm(question, { title = 'Are you sure?', ok = 'OK', cancel = 'Cancel', danger = false } = {}) {
  const modal = $('confirm-modal');
  if (confirmSettle) confirmSettle(null);
  $('confirm-title').textContent = title;
  $('confirm-message').textContent = question;
  const cancelBtn = $('confirm-cancel');
  if (cancelBtn) cancelBtn.textContent = cancel;
  const okBtn = $('confirm-ok');
  okBtn.textContent = ok;
  okBtn.classList.toggle('danger', danger);
  okBtn.classList.toggle('primary', !danger);
  const opener = document.activeElement;
  modal.classList.remove('hidden');
  (danger ? $('confirm-cancel') : okBtn).focus();
  return new Promise(resolve => {
    confirmSettle = (answer) => {
      confirmSettle = null;
      modal.classList.add('hidden');
      if (opener && typeof opener.focus === 'function' && document.contains(opener)) opener.focus();
      resolve(answer);
    };
  });
}
(function initConfirmDialog() {
  const modal = $('confirm-modal');
  if (!modal) return;
  modal.addEventListener('click', (e) => {
    const b = e.target.closest('[data-confirm]');
    if (b && confirmSettle) confirmSettle(b.dataset.confirm === 'ok');
  });
  modal.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      e.preventDefault(); e.stopPropagation();
      if (confirmSettle) confirmSettle(false);
    } else if (e.key === 'Tab') {
      // Two buttons: keep the focus inside the dialog while it is open.
      const first = $('confirm-cancel'), last = $('confirm-ok');
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  });
})();

// ── View switching ───────────────────────────────────────────────────────────
function switchView(name) {
  if (isDirty) {
    // Ask in the app's own dialog; on "Leave" drop the edits and switch.
    askConfirm('You have unsaved changes. Leave this page without saving them?',
      { title: 'Unsaved changes', ok: 'Leave' }).then(leave => {
      if (!leave) return;
      isDirty = false; // Force reset when the user chooses to leave
      updateSaveButtonVisual();
      switchView(name);
    });
    return;
  }
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  document.querySelectorAll('.nav-btn[id^="nav-"]').forEach(b => b.classList.remove('active'));
  $('view-' + name)?.classList.add('active');
  $('nav-' + name)?.classList.add('active');
  if (name === 'local') loadNetworkShares();
  if (name === 'queue') { loadStaged(); window.dismissDownloadToast?.(); }
  if (name === 'history') loadHistory();
  if (name === 'settings' && $('tc-general')?.classList.contains('active')) loadStorageUsage();
  if (name === 'following') loadWeeklyReleases(false);
  if (name === 'player') initPlayerView();
  if (name === 'home') { ensureHomeFeed(); renderMusicRecentSongs(); }
  if (name === 'home') renderPersonalHome();
  placeQueueDock(name === 'home' && isMusicStyle());
  syncLibraryNav();
}

// Mounted network shares as one-click picks under the library path field:
// smb://nas/Music works typed in too, but only once the OS has mounted it,
// and these are exactly the ones that will open.
async function loadNetworkShares() {
  const box = $('local-network-shares');
  if (!box || !window.pywebview?.api?.get_network_shares) return;
  try {
    const res = await window.pywebview.api.get_network_shares();
    const shares = res?.shares || [];
    box.classList.toggle('hidden', shares.length === 0);
    box.innerHTML = shares.length
      ? '<span class="share-chips-label">Network:</span>' + shares.map(s =>
          `<button type="button" class="share-chip" title="${escHtml(s.path)}" data-path="${escHtml(s.path)}">`
          + '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2" y="3" width="20" height="8" rx="2"/><rect x="2" y="13" width="20" height="8" rx="2"/><line x1="6" y1="7" x2="6.01" y2="7"/><line x1="6" y1="17" x2="6.01" y2="17"/></svg>'
          + `${escHtml(s.label)}</button>`).join('')
      : '';
    box.querySelectorAll('.share-chip').forEach(btn => btn.addEventListener('click', () => {
      const input = $('local-path-input');
      if (input) { input.value = btn.dataset.path; input.focus(); }
    }));
  } catch (e) { /* the field still accepts a typed path */ }
}

// ── Sidebar ──────────────────────────────────────────────────────────────────
// Labelled by default; collapsed it folds back to the icon rail. The choice is
// a per-browser convenience, so localStorage is enough — the <body> script in
// index.html applies it before the first paint.
function toggleSidebar(force) {
  const collapsed = typeof force === 'boolean'
    ? force
    : !document.body.classList.contains('sidebar-collapsed');
  document.body.classList.toggle('sidebar-collapsed', collapsed);
  const btn = document.querySelector('.sb-collapse');
  if (btn) {
    btn.setAttribute('aria-pressed', String(collapsed));
    btn.title = collapsed ? 'Expand sidebar' : 'Collapse sidebar';
    const label = btn.querySelector('.nav-label');
    if (label) label.textContent = collapsed ? 'Expand' : 'Collapse';
  }
  try { localStorage.setItem('mornye-sidebar', collapsed ? 'collapsed' : 'expanded'); } catch (e) {}
}

// ── Settings search ──────────────────────────────────────────────────────────
// Typing searches every page at once: all pages are shown, rows whose label
// does not match are hidden, and a section with nothing left goes too. An
// empty query puts the single active page back.
function filterSettings(query) {
  const q = (query || '').trim().toLowerCase();
  const body = document.querySelector('#view-settings .s-body');
  if (!body) return;
  body.classList.toggle('searching', !!q);
  let anyVisible = false;
  body.querySelectorAll('.tc').forEach(tc => {
    let pageHits = 0;
    tc.querySelectorAll('.s-section').forEach(section => {
      const title = (section.querySelector('.s-title')?.textContent || '').toLowerCase();
      const titleHit = !!q && title.includes(q);
      let hits = 0;
      section.querySelectorAll('.s-row').forEach(row => {
        const hit = !q || titleHit || row.textContent.toLowerCase().includes(q);
        row.classList.toggle('s-filtered', !hit);
        if (hit) hits++;
      });
      const rows = section.querySelectorAll('.s-row').length;
      // Sections built from something other than .s-row (lists, grids) match
      // on their whole text instead.
      const sectionHit = !q || titleHit || hits > 0
        || (rows === 0 && section.textContent.toLowerCase().includes(q));
      section.classList.toggle('s-filtered', !sectionHit);
      if (sectionHit) pageHits++;
    });
    tc.classList.toggle('search-hit', !!q && pageHits > 0);
    if (pageHits > 0) anyVisible = true;
  });
  $('settings-no-match')?.classList.toggle('hidden', !q || anyVisible);
}

function switchTab(name, btn) {
  const search = $('settings-search');
  if (search && search.value) { search.value = ''; filterSettings(''); }
  // A caller outside the tab strip may not have the button: find it, and stop
  // before clearing the active tab if there is nothing to switch to.
  btn = btn || document.querySelector(`.stab[data-onclick*="'${name}'"]`);
  const panel = $('tc-' + name);
  if (!btn || !panel) return;
  document.querySelectorAll('.stab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tc').forEach(c => c.classList.remove('active'));
  btn.classList.add('active');
  panel.classList.add('active');
  if (name === 'status' && !extensionStatusChecking) {
    // Lists the installed extensions' checks without running them; Check
    // runs them. Re-read each time: an extension may have been installed.
    loadExtensionStatusList();
  }
  if (name === 'general') loadStorageUsage();
  if (name === 'player') { loadPlayerSettings(); loadLinkSettings(); loadConnectSettings(); loadIntegrationSettings(); }
  else stopLinkPolling();
  if (name === 'extensions') {
    loadRegistries();
    loadDirectories();
    loadTrustedKeys();
    loadExtensionSettings();
    loadExtensionHealth();
    loadSignedSessions();
  }
}

// ── Appearance ───────────────────────────────────────────────────────────────
// The theme class goes on BOTH <html> and <body>. The tokens are declared on
// :root and overridden per theme, and `html { background: var(--bg) }` reads
// them from <html> — with the class only on <body>, <html> kept resolving
// --bg to the light default, so the page's outermost background (what shows
// through on overscroll and around the content) stayed light grey in dark
// mode. Keeping both in sync also means the pre-paint script in index.html
// and this function can't disagree about which element carries the state.
function setThemeClass(dark) {
  for (const el of [document.documentElement, document.body]) {
    if (!el) continue;
    el.classList.toggle('dark-theme', dark);
    el.classList.toggle('light-theme', !dark);
  }
  // The browser chrome around the page follows the theme too — on a phone
  // that is Chrome's address bar, and the status/gesture bars of an
  // installed PWA. index.html's pre-paint script sets the same tag for the
  // first frame; this keeps it honest when the theme is switched later, or
  // when the OS flips under an 'auto' choice.
  try {
    const tc = document.querySelector('meta[name="theme-color"]');
    if (tc) tc.setAttribute('content', dark ? '#0d0d0d' : '#f2f2f0');
  } catch (e) { /* no <head> access is not worth failing the theme over */ }
}

function applyTheme(mode) {
  if (mode === 'light' || mode === 'dark') {
    setThemeClass(mode === 'dark');
  } else {
    setThemeClass(window.matchMedia('(prefers-color-scheme: dark)').matches);
  }
}

function changeTheme() {
  const val = $('config-theme').value;
  applyTheme(val);
  try {
    localStorage.setItem('mornye-theme-mode', val);
    // Mirror the choice into the settings blob as well. saveSettings() only
    // writes it when the user presses Save, so without this the blob kept
    // saying 'auto' while mornye-theme-mode said 'dark' — and the blob is
    // what applySettings() reads at boot, which is how a picked dark theme
    // came back light on the next launch.
    const stored = JSON.parse(localStorage.getItem(SETTINGS_STORAGE_KEY) || '{}');
    stored.theme = val;
    localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify(stored));
  } catch (e) {}
  // …and into gui-settings.json, which is the copy that actually survives.
  // Both localStorage writes above live in the web view's per-origin storage,
  // and the desktop window has not historically kept that across launches
  // (see run_gui() in app.py), so on its own the picker never stuck. This is
  // a theme-only merge, so it cannot clobber unsaved edits elsewhere in the
  // Settings form.
  try {
    window.pywebview?.api?.save_theme?.(val);
  } catch (e) {}
}

function syncSystemTheme(e) {
  const val = $('config-theme')?.value || 'auto';
  if (val === 'auto') applyTheme('auto');
}

//: Must match MornyeAPI.ACCENTS in app.py and the blocks in css/accents.css.
//: 'amber' (the console look's signal) is the default and has no block of
//: its own: it is what tokens.css already says, so it is applied by removing
//: the attribute. An accent changes the signal tokens and nothing else.
const ACCENTS = ['amber', 'teal', 'green', 'blue', 'purple', 'pink', 'orange', 'red', 'cyan'];
//: Bumped by a rebrand that changes the default accent. Stored both in
//: localStorage ('mornye-brand') and in gui-settings.json ('brand', written by
//: save_accent()) so the one-off migration below happens once per install.
const BRAND_VERSION = 3;

// The default was 'green' before the 2026 warm rebrand and 'teal' until the
// console redesign; a stored one is nearly always that untouched default
// rather than a choice. Move it to amber once; anyone who picks teal or
// green again afterwards keeps it.
function migrateAccent(accent, cfgBrand) {
  let done = Number(cfgBrand) >= BRAND_VERSION;
  try { done = done || localStorage.getItem('mornye-brand') === String(BRAND_VERSION); } catch (e) {}
  try { localStorage.setItem('mornye-brand', String(BRAND_VERSION)); } catch (e) {}
  return (!done && (accent === 'green' || accent === 'teal')) ? 'amber' : accent;
}

// data-accent goes on <html> *and* <body>, the same pair the theme classes
// use (see setThemeClass) — css/accents.css keys the blocks off both, so
// that the token values are in scope no matter which element a rule resolves
// against.
function applyAccent(accent) {
  const val = ACCENTS.includes(accent) ? accent : 'amber';
  for (const el of [document.documentElement, document.body]) {
    if (!el) continue;
    if (val === 'amber') el.removeAttribute('data-accent');
    else el.setAttribute('data-accent', val);
  }
  // The music look's swatches (.m-swatches) mirror the select.
  document.querySelectorAll('.m-swatches [data-a]').forEach((b) => {
    b.classList.toggle('on', b.dataset.a === val);
    b.setAttribute('aria-checked', String(b.dataset.a === val));
  });
}

// A swatch in the music look: the same choice as the Accent select.
function pickAccent(accent) {
  const select = $('config-accent');
  if (!select || !ACCENTS.includes(accent)) return;
  select.value = accent;
  changeAccent();
}

function changeAccent() {
  const val = $('config-accent')?.value || 'amber';
  applyAccent(val);
  // Three copies for the same reason changeTheme() keeps three: the two
  // localStorage writes are per-origin and can vanish, gui-settings.json is
  // the one that survives a restart.
  try {
    localStorage.setItem('mornye-accent', val);
    const stored = JSON.parse(localStorage.getItem(SETTINGS_STORAGE_KEY) || '{}');
    stored.accent = val;
    localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify(stored));
  } catch (e) {}
  try {
    window.pywebview?.api?.save_accent?.(val);
  } catch (e) {}
}

// ── Density (N4) ───────────────────────────────────────────────────────────
// Each density is a zoom of the whole interface, far enough apart to tell
// at a glance; the compact ones also put data-density="compact" on <html>
// and <body>, where css/layout.css takes a step off the row height and the
// spacing tokens, so more rows fit. Stored like the accent: localStorage for
// the next first paint (boot-head.js), the settings blob, and
// gui-settings.json (save_density), the copy that survives.
const DENSITIES = ['extra-compact', 'compact', 'comfortable', 'large', 'extra-large'];
// The desktop window zooms natively (set_page_zoom, WKWebView pageZoom —
// like Cmd-+); where that isn't available (browser tab, other platforms)
// css/layout.css applies CSS zoom under data-zoom="css", by --page-zoom.
const DENSITY_ZOOM = { 'extra-compact': 0.8, compact: 0.9, comfortable: 1, large: 1.15, 'extra-large': 1.3 };

function applyDensity(density) {
  const val = DENSITIES.includes(density) ? density : 'comfortable';
  const tokens = val.endsWith('compact') ? 'compact' : val.endsWith('large') ? 'large' : '';
  for (const el of [document.documentElement, document.body]) {
    if (!el) continue;
    if (tokens) el.setAttribute('data-density', tokens);
    else el.removeAttribute('data-density');
  }
  applyPageZoom(DENSITY_ZOOM[val]);
}

function applyPageZoom(factor) {
  document.documentElement.style.setProperty('--page-zoom', String(factor));
  const setCss = (on) => {
    if (on) document.documentElement.setAttribute('data-zoom', 'css');
    else document.documentElement.removeAttribute('data-zoom');
  };
  const native = window.pywebview?.api?.set_page_zoom;
  if (typeof native !== 'function') { setCss(factor !== 1); return; }
  Promise.resolve(native(factor))
    .then(res => setCss(!(res && res.ok) && factor !== 1))
    .catch(() => setCss(factor !== 1));
}

function changeDensity() {
  const val = $('config-density')?.value || 'comfortable';
  applyDensity(val);
  try {
    localStorage.setItem('mornye-density', val);
    const stored = JSON.parse(localStorage.getItem(SETTINGS_STORAGE_KEY) || '{}');
    stored.density = val;
    localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify(stored));
  } catch (e) {}
  try {
    window.pywebview?.api?.save_density?.(val);
  } catch (e) {}
}

// ── Interface style ────────────────────────────────────────────────────────
// 'console' is the monospace console look; 'music' the music-app look
// (css/music-tokens.css + css/music.css, keyed off data-style="music" on
// <html> and <body>). Stored like the density: localStorage for the next
// first paint (boot-head.js), the settings blob, and gui-settings.json
// (save_style). Must match MornyeAPI.STYLES in app.py.
const STYLES = ['music', 'console'];

function isMusicStyle() {
  return document.documentElement.getAttribute('data-style') === 'music';
}

function applyStyle(style) {
  const val = STYLES.includes(style) ? style : STYLES[0];
  for (const el of [document.documentElement, document.body]) {
    if (!el) continue;
    if (val === 'music') el.setAttribute('data-style', 'music');
    else el.removeAttribute('data-style');
  }
  mountHomeFeed(val === 'music');
  // History's rows carry the covers only in the music look: redrawn on a
  // switch either way.
  if ($('view-history')?.classList.contains('active')) loadHistory();
}

function changeStyle() {
  const val = $('config-style')?.value || 'console';
  applyStyle(val);
  try {
    localStorage.setItem('mornye-style', val);
    const stored = JSON.parse(localStorage.getItem(SETTINGS_STORAGE_KEY) || '{}');
    stored.style = val;
    localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify(stored));
  } catch (e) {}
  try {
    window.pywebview?.api?.save_style?.(val);
  } catch (e) {}
}

// In the music look Home is also where the recommendations are, the way a
// music app's Home is: Explore's feed (picker and sections, #explore-feed)
// moves into #recent-wrap, which Home already hides while an album is open
// and shows again when it closes, and the Explore entry leaves the sidebar.
// Back in the console look the feed returns to its own page. Moving the
// nodes keeps their ids and handlers, so loadExploreData() works in both.
let exploreFeedHome = null;
let exploreSourceHome = null;
let homeFeedLoaded = false;

function mountHomeFeed(music) {
  const feed = $('explore-feed');
  const slot = $('recent-wrap');
  if (!feed || !slot) return;
  if (!exploreFeedHome) exploreFeedHome = feed.parentElement;
  if (music) {
    if (feed.parentElement !== slot) slot.prepend(feed);
  } else if (exploreFeedHome && feed.parentElement !== exploreFeedHome) {
    exploreFeedHome.appendChild(feed);
  }
  // The source picker sits beside Home's title in the music look
  // ("Recommendations from …"), in the feed's own header otherwise.
  const picker = $('explore-source');
  const pickSlot = $('m-feed-slot');
  if (picker && pickSlot) {
    if (!exploreSourceHome) exploreSourceHome = picker.parentElement;
    if (music) pickSlot.appendChild(picker);
    else if (exploreSourceHome && picker.parentElement !== exploreSourceHome) exploreSourceHome.appendChild(picker);
  }
  $('nav-explore')?.classList.toggle('hidden', music);
  placeVersion(music);
  if (music && $('view-explore')?.classList.contains('active')) switchView('home');
  const onHome = $('view-home')?.classList.contains('active');
  placeQueueDock(music && onHome);
  if (music && onHome) { ensureHomeFeed(); renderMusicRecentSongs(); renderPersonalHome(); }
}

// The version sits under Settings, at the foot of the sidebar, in the music
// look; beside the brand in the console look.
let versionHome = null;

function placeVersion(music) {
  const ver = $('sb-version');
  const settings = $('nav-settings');
  if (!ver || !settings) return;
  if (!versionHome) versionHome = ver.parentElement;
  if (music) { if (settings.nextElementSibling !== ver) settings.after(ver); }
  else if (versionHome && ver.parentElement !== versionHome) versionHome.appendChild(ver);
}

// The sidebar's Search in the music look: Home, in search mode, focused.
function musicSearch() {
  switchView('home');
  setSearchModeTo('search');
  $('urlInput')?.focus();
}

// The Link / Search / CSV control drives the same mode the console look's
// toggle does; this only says which one to be in.
function setSearchModeTo(mode) {
  if ($('searchMode') && $('searchMode').value !== mode) toggleSearchMode();
  syncModeSeg();
}

function syncModeSeg() {
  const mode = $('searchMode')?.value || 'link';
  document.querySelectorAll('.m-seg [data-mode]').forEach((b) => {
    b.classList.toggle('on', b.dataset.mode === mode);
    b.setAttribute('aria-pressed', String(b.dataset.mode === mode));
  });
}

// The download dock is a floating pill in the music look, except on Home,
// where it sits under the fetch bar as the "Downloading …" line. Only the
// node moves: renderQueueNow() keeps filling it by id wherever it is, and
// it goes back to <body> on every other page, so it never disappears with
// a view (the reason it lives at body level, see index.html).
let queueDockHome = null;

function placeQueueDock(onMusicHome) {
  const dock = $('queue-dock');
  const bar = $('search-bar');
  if (!dock || !bar) return;
  if (!queueDockHome) queueDockHome = dock.parentElement;
  if (onMusicHome) {
    if (dock.previousElementSibling !== bar) bar.after(dock);
  } else if (queueDockHome && dock.parentElement !== queueDockHome) {
    queueDockHome.appendChild(dock);
  }
}

// "Recently Added" on the music look's Home: the newest downloaded songs,
// three columns of three, with the cover read from each file.
async function renderMusicRecentSongs() {
  const shelf = $('m-recent-songs');
  const grid = $('m-recent-songs-grid');
  const api = window.pywebview?.api;
  if (!shelf || !grid || !isMusicStyle() || typeof api?.get_download_history !== 'function') return;
  let items = [];
  try {
    const res = await api.get_download_history('', 'newest', 30);
    items = (res?.items || []).filter((r) => r.success).slice(0, 9);
  } catch (e) { items = []; }
  shelf.classList.toggle('hidden', !items.length);
  if (!items.length) { grid.innerHTML = ''; return; }
  let covers = {};
  try {
    const paths = items.map((r) => r.file_path).filter(Boolean);
    if (paths.length && typeof api.get_track_thumbs === 'function') covers = (await api.get_track_thumbs(paths, 96)) || {};
  } catch (e) { covers = {}; }
  grid.innerHTML = items.map((r) => {
    const cover = covers[r.file_path];
    const art = cover
      ? `<img class="m-song-art" src="${escHtml(cover)}" alt="" loading="lazy">`
      : '<div class="m-song-art m-song-blank" aria-hidden="true">♪</div>';
    const fmt = (r.format || '').toUpperCase();
    return `<div class="m-song" title="${escHtml(r.file_path || '')}">
      ${art}
      <div class="m-song-text"><div class="m-song-title">${escHtml(r.title || 'Untitled')}</div>
        <div class="m-song-artist">${escHtml(r.artist || '')}</div></div>
      ${fmt ? `<span class="m-song-q">${escHtml(fmt)}</span>` : ''}
    </div>`;
  }).join('');
  placeRecentSongs();
}

// The feed's container is emptied on every load (innerHTML), which would
// take the shelf with it once placeRecentSongs() has put it in there: it
// steps back out to #recent-wrap first.
function rescueRecentSongs() {
  const shelf = $('m-recent-songs');
  const wrap = $('recent-wrap');
  if (shelf && wrap && $('explore-sections')?.contains(shelf)) {
    wrap.insertBefore(shelf, wrap.querySelector('.recent-container'));
  }
}

// After the feed's first shelf (Top Picks), as on a music app's Home.
function placeRecentSongs() {
  // The personal Home orders its sections itself (Edit Home).
  if (personalHome) return;
  const shelf = $('m-recent-songs');
  const first = $('explore-sections')?.firstElementChild;
  if (shelf && first && isMusicStyle() && first.nextElementSibling !== shelf) first.after(shelf);
}

// Top Picks: the panel under each card takes the colour of its artwork, the
// album page's own averaging (applyAlbumGlow) on a second, CORS-enabled
// copy of the image. A host without CORS leaves the neutral panel.
function tintPickCard(card, url) {
  if (!url) return;
  const img = new Image();
  img.crossOrigin = 'anonymous';
  img.onload = () => {
    try {
      const SIZE = 16;
      const canvas = document.createElement('canvas');
      canvas.width = SIZE; canvas.height = SIZE;
      const ctx = canvas.getContext('2d');
      ctx.drawImage(img, 0, 0, SIZE, SIZE);
      const { data } = ctx.getImageData(0, 0, SIZE, SIZE);
      let r = 0, g = 0, b = 0, n = 0;
      for (let i = 0; i < data.length; i += 4) { r += data[i]; g += data[i + 1]; b += data[i + 2]; n++; }
      // Darkened so the white text on it stays readable.
      const k = 0.55;
      card.style.setProperty('--pick-bg', `rgb(${Math.round(r / n * k)}, ${Math.round(g / n * k)}, ${Math.round(b / n * k)})`);
      card.classList.add('m-tinted');
    } catch (e) { /* tainted canvas: keep the neutral panel */ }
  };
  img.src = url;
}

// Following, music look: "Follow several artists at once" is folded behind
// a button under the follow field, as in the mockup.
function toggleBulkFollow() {
  const box = $('m-bulk-follow');
  if (!box) return;
  const open = !box.classList.contains('m-open');
  box.classList.toggle('m-open', open);
  document.querySelector('.m-bulk-toggle')?.setAttribute('aria-expanded', String(open));
  if (open) $('subscription-bulk-input')?.focus();
}

// Queue, music look: "9 items · 3 downloading" under the title, from the
// staged queue and the live downloads.
function updateMusicQueueSub() {
  const el = $('m-queue-sub');
  if (!el) return;
  const items = (stagedState.items || []).length;
  const active = queue.filter((q) => q.status === 'active').length;
  el.textContent = [`${items} item${items === 1 ? '' : 's'}`, active ? `${active} downloading` : '']
    .filter(Boolean).join(' · ');
}

// The feed is fetched once per launch when Home first shows it; the picker
// and the error state's retry reload it on demand, as on Explore.
function ensureHomeFeed() {
  if (homeFeedLoaded || !isMusicStyle()) return;
  if (!window.pywebview?.api) return; // the boot handler asks again once the backend is up
  homeFeedLoaded = true;
  loadExploreData({ quiet: true });
}

// ── Personal Home (from Mornye mobile 5.4.5) ───────────────────────────────
// The music look's Home made from the listener's own record
// (core/personal_home.py): Quick play, Pinned, Mixes for you, Recently
// played, Finish your collection, Recommended for you and On repeat, in the
// order Edit Home saved, beside the feed (Top picks) and Recently added that
// were already here. A status line under the greeting says what arrived
// since the last visit and what is downloading.
var HOME_SESSION = Math.random().toString(36).slice(2, 10);
var HOME_SECTION_ELEMENT = { topPicks: 'explore-feed', recentlyAdded: 'm-recent-songs' };
var personalHome = null;
var personalHomeLoading = null;

// What the status line says: arrivals since the last visit, then the queue.
function homeStatusText(status, downloading) {
  const parts = [];
  const added = Number(status?.added) || 0;
  if (added) {
    const songs = added === 1 ? '1 song' : `${added} songs`;
    parts.push(status.since_yesterday ? `${songs} added since yesterday` : `${songs} added since your last visit`);
  }
  if (downloading > 0) parts.push(`${downloading} downloading`);
  return parts.join(' · ');
}

// Quick play: the tapped song first, the other eight queued after it.
function quickPlayQueue(songs, index) {
  if (!songs[index]) return [];
  return [songs[index], ...songs.filter((_, i) => i !== index)];
}

// Edit Home's up/down: `list` with the item at `index` moved by `delta`.
function moveInList(list, index, delta) {
  const to = index + delta;
  if (to < 0 || to >= list.length) return list.slice();
  const out = list.slice();
  const [item] = out.splice(index, 1);
  out.splice(to, 0, item);
  return out;
}

function homePinKey(pin) { return `${pin.kind}:${pin.id}`; }

function homeSongItem(s) {
  return { path: s.path, title: s.title || s.path.split(/[\\/]/).pop(), artist: s.artist || '', album: s.album || '' };
}

function playHomeSongs(songs, index = 0, shuffle) {
  const items = (songs || []).filter(s => s && s.path).map(homeSongItem);
  if (!items.length) { toastMgr.error('Nothing to play here.'); return; }
  startPlayback(items, Math.min(index, items.length - 1), shuffle, false);
}

async function renderPersonalHome({ force = false } = {}) {
  if (!isMusicStyle()) return;
  const api = window.pywebview?.api;
  if (typeof api?.get_personal_home !== 'function') return;
  if (personalHomeLoading && !force) return personalHomeLoading;
  personalHomeLoading = (async () => {
    let res = null;
    try { res = await api.get_personal_home(HOME_SESSION, false); } catch (e) { res = null; }
    if (!res || res.status !== 'ok') return;
    personalHome = res;
    drawPersonalHome();
    loadHomeRecommendations();
  })();
  try { await personalHomeLoading; } finally { personalHomeLoading = null; }
}

async function loadHomeRecommendations(refresh = false) {
  const api = window.pywebview?.api;
  if (!personalHome || !personalHome.order.includes('recommended') || typeof api?.get_home_recommendations !== 'function') return;
  let res = null;
  try { res = await api.get_home_recommendations(refresh); } catch (e) { res = null; }
  personalHome.sections.recommended = res?.tracks || [];
  drawHomeSection('recommended');
}

function homeDownloadingCount() {
  try { return queue.filter(q => q.status === 'active' || q.status === 'queued' || q.status === 'waiting').length; } catch (e) { return 0; }
}

function drawHomeHead() {
  if (!personalHome) return;
  const title = $('m-home-title');
  if (title) title.textContent = personalHome.greeting || 'Home';
  const line = $('m-home-status');
  if (line) {
    const text = homeStatusText(personalHome.arrivals, homeDownloadingCount());
    line.textContent = text;
    line.classList.toggle('hidden', !text);
  }
}

function drawPersonalHome() {
  const wrap = $('recent-wrap');
  if (!wrap || !personalHome) return;
  wrap.classList.add('m-personal');
  drawHomeHead();
  rescueRecentSongs();
  const order = personalHome.order || [];
  const all = (personalHome.layout || []).map(s => s.id);
  all.forEach((id) => {
    const el = homeSectionEl(id);
    if (!el) return;
    const at = order.indexOf(id);
    el.style.order = String(at < 0 ? 999 : at);
    el.classList.toggle('m-home-off', at < 0);
  });
  for (const id of order) drawHomeSection(id);
}

// The element of a section: the feed and Recently added are already in
// the page; the rest are made here, once.
function homeSectionEl(id) {
  if (HOME_SECTION_ELEMENT[id]) return $(HOME_SECTION_ELEMENT[id]);
  let el = $(`m-home-${id}`);
  if (!el) {
    el = document.createElement('section');
    el.id = `m-home-${id}`;
    el.className = 'm-only m-shelf m-home-sec';
    el.dataset.sec = id;
    $('recent-wrap')?.appendChild(el);
  }
  return el;
}

function homeTitleOf(id) {
  return (personalHome?.layout || []).find(s => s.id === id)?.title || id;
}

function homeShelf(id, inner, { action = '' } = {}) {
  const el = homeSectionEl(id);
  if (!el) return;
  el.innerHTML = inner
    ? `<div class="m-home-sec-head"><h3 class="explore-section-title">${escHtml(homeTitleOf(id))}</h3>${action}</div>${inner}`
    : '';
  el.classList.toggle('hidden', !inner);
}

function homeCard({ title, subtitle = '', cover = '', attrs = '', badge = '', menu = '' }) {
  const art = cover
    ? `<img src="${escHtml(cover)}" alt="" loading="lazy">`
    : '<div class="explore-cover-blank" aria-hidden="true">♪</div>';
  return `<div class="explore-card m-home-card" tabindex="0" role="button" data-activate ${attrs}>
    ${art}${badge}
    <div class="explore-card-title" title="${escHtml(title)}">${escHtml(title)}</div>
    <div class="explore-card-subtitle" title="${escHtml(subtitle)}">${escHtml(subtitle)}</div>${menu}
  </div>`;
}

// Covers are read from the files (get_track_thumbs), after the shelf is
// drawn, so the shelf shows at once.
async function fillHomeCovers(el) {
  const api = window.pywebview?.api;
  const slots = [...el.querySelectorAll('[data-cover-path]')];
  if (!slots.length || typeof api?.get_track_thumbs !== 'function') return;
  let covers = {};
  try { covers = (await api.get_track_thumbs([...new Set(slots.map(s => s.dataset.coverPath))], 220)) || {}; } catch (e) { covers = {}; }
  for (const slot of slots) {
    const src = covers[slot.dataset.coverPath];
    const blank = slot.querySelector('.explore-cover-blank, .m-song-blank');
    if (src && blank) {
      const img = document.createElement('img');
      img.src = src; img.alt = ''; img.className = blank.className.includes('m-song') ? 'm-song-art' : '';
      blank.replaceWith(img);
    }
  }
}

function drawHomeSection(id) {
  if (!personalHome) return;
  const data = personalHome.sections?.[id];
  let html = '';
  let action = '';
  if (id === 'quickPlay') {
    const songs = data || [];
    html = songs.length ? `<div class="m-quick">${songs.map((s, i) => `
      <button type="button" class="m-quick-tile" data-home-quick="${i}" data-cover-path="${escHtml(s.path)}" title="${escHtml(`${s.title} — ${s.artist}`)}">
        <div class="explore-cover-blank" aria-hidden="true">♪</div>
        <span class="m-quick-text"><span class="m-quick-title">${escHtml(s.title)}</span><span class="m-quick-artist">${escHtml(s.artist)}</span></span>
      </button>`).join('')}</div>` : '';
  } else if (id === 'pins') {
    const pins = personalHome.pins || [];
    const KIND = { album: 'Album', playlist: 'Playlist', artist: 'Artist', genre: 'Genre', folder: 'Folder' };
    html = pins.length ? `<div class="explore-grid">${pins.map(p => homeCard({
      title: p.title,
      subtitle: p.subtitle || KIND[p.kind] || '',
      attrs: `data-home-pin="${escHtml(homePinKey(p))}"${p.cover_path ? ` data-cover-path="${escHtml(p.cover_path)}"` : ''}`,
      menu: `<button type="button" class="m-home-more" data-home-pin-menu="${escHtml(homePinKey(p))}" aria-label="More for ${escHtml(p.title)}" title="More">…</button>`,
    })).join('')}</div>` : '';
  } else if (id === 'mixes') {
    const mixes = data || [];
    html = mixes.length ? `<div class="explore-grid">${mixes.map((m, i) => homeCard({
      title: m.title,
      subtitle: m.subtitle || 'Made from your Library, updated daily',
      attrs: `data-home-mix="${i}" data-cover-path="${escHtml(m.songs?.[0]?.path || '')}"`,
      badge: `<span class="m-mix-badge">${m.songs.length} songs</span>`,
      menu: `<button type="button" class="m-home-more" data-home-mix-menu="${i}" aria-label="More for ${escHtml(m.title)}" title="More">…</button>`,
    })).join('')}</div>` : '';
  } else if (id === 'recentlyPlayed') {
    const albums = data || [];
    html = albums.length ? `<div class="explore-grid">${albums.map(a => homeCard({
      title: a.title, subtitle: a.artist,
      attrs: `data-home-album="${escHtml(a.key)}" data-album-title="${escHtml(a.title)}" data-cover-path="${escHtml(a.cover_path)}"`,
    })).join('')}</div>` : '';
  } else if (id === 'onRepeat') {
    if (data?.kind === 'songs') {
      html = data.songs.length ? `<div class="m-songs">${data.songs.map((s, i) => `
        <div class="m-song" role="button" tabindex="0" data-activate data-home-repeat="${i}" data-cover-path="${escHtml(s.path)}">
          <div class="m-song-art m-song-blank" aria-hidden="true">♪</div>
          <div class="m-song-text"><div class="m-song-title">${escHtml(s.title)}</div><div class="m-song-artist">${escHtml(s.artist)}</div></div>
          <span class="m-song-q">${s.plays}×</span>
        </div>`).join('')}</div>` : '';
    } else {
      const albums = data?.albums || [];
      html = albums.length ? `<div class="explore-grid">${albums.map(a => homeCard({
        title: a.title, subtitle: a.artist,
        attrs: `data-home-album="${escHtml(a.key)}" data-album-title="${escHtml(a.title)}" data-cover-path="${escHtml(a.cover_path)}"`,
      })).join('')}</div>` : '';
    }
  } else if (id === 'unfinished') {
    const rows = data?.rows || [];
    html = rows.length ? `<ul class="m-unfinished">${rows.map(r => `
      <li><button type="button" class="m-unfinished-row" data-home-unfinished="${escHtml(r.id)}">${escHtml(r.text)}<span aria-hidden="true">›</span></button></li>`).join('')}</ul>
      <div id="m-unfinished-albums" class="explore-grid hidden">${(data.albums || []).map(a => homeCard({
        title: a.title, subtitle: `${a.present} of ${a.expected} songs`,
        attrs: `data-home-album="${escHtml(a.key)}" data-album-title="${escHtml(a.title)}" data-cover-path="${escHtml(a.cover_path)}"`,
      })).join('')}</div>` : '';
  } else if (id === 'recommended') {
    const tracks = data || [];
    action = '<button type="button" class="m-home-link" data-home-refresh-reco title="Ask for new songs">Refresh</button>';
    html = tracks.length ? `<div class="m-songs">${tracks.map((t, i) => {
      const cover = httpUrlOrNull(t.cover);
      return `<div class="m-song" role="button" tabindex="0" data-activate data-home-reco="${i}" title="Search for ${escHtml(`${t.title} — ${t.artist}`)}">
          ${cover ? `<img class="m-song-art" src="${escHtml(cover)}" alt="" loading="lazy">` : '<div class="m-song-art m-song-blank" aria-hidden="true">♪</div>'}
          <div class="m-song-text"><div class="m-song-title">${escHtml(t.title)}</div><div class="m-song-artist">${escHtml(t.artist)}</div></div>
        </div>`;
    }).join('')}</div>` : '';
  } else {
    return;
  }
  homeShelf(id, html, { action });
  const el = homeSectionEl(id);
  if (el && html) fillHomeCovers(el);
}

function openHomeAlbum(key, title) {
  if (!enterLibrary()) return;
  const open = () => {
    if (libAll().albums.some(a => a.key === key)) { openPlayerAlbum(key); return true; }
    return false;
  };
  if (open()) return;
  // The Library may still be loading from its store.
  setTimeout(() => { if (!open()) { $('player-search').value = title || ''; setPlayerTab('songs'); } }, 700);
}

async function openHomePin(key) {
  const pin = (personalHome?.pins || []).find(p => homePinKey(p) === key);
  if (!pin) return;
  if (pin.kind === 'album') return openHomeAlbum(pin.id, pin.title);
  if (pin.kind === 'playlist') return openLibraryPlaylist(pin.id);
  if (pin.kind === 'artist') {
    if (!enterLibrary()) return;
    const go = () => { const a = libAll().artists.find(x => x.key === pin.id || x.name.toLowerCase() === pin.id); if (a) openPlayerArtist(a.key); return !!a; };
    if (!go()) setTimeout(() => { if (!go()) { $('player-search').value = pin.title; setPlayerTab('songs'); } }, 700);
    return;
  }
  // Genres and folders have no page in the Library: they play.
  const res = await window.pywebview?.api?.get_home_pin_songs?.(key);
  if (res?.status !== 'ok') { toastMgr.error(res?.error || 'Could not open this pin.'); return; }
  playHomeSongs(res.songs, 0, true);
}

async function pinToHome(pin) {
  const res = await window.pywebview?.api?.pin_to_home?.(pin);
  if (res?.status !== 'ok') { toastMgr.error(res?.error || 'Could not pin to Home.'); return; }
  if (personalHome) { personalHome.pins = res.pins; drawHomeSection('pins'); }
  toastMgr.success(`Pinned ${pin.title} to Home`);
}

async function unpinFromHome(key) {
  const res = await window.pywebview?.api?.unpin_from_home?.(key);
  if (res?.status === 'ok' && personalHome) { personalHome.pins = res.pins; drawHomeSection('pins'); }
}

async function moveHomePinToFront(key) {
  const res = await window.pywebview?.api?.move_home_pin?.(key, 0);
  if (res?.status === 'ok' && personalHome) { personalHome.pins = res.pins; drawHomeSection('pins'); }
}

function homePinned(kind, id) {
  return (personalHome?.pins || []).some(p => p.kind === kind && p.id === id);
}

// "Pin to Home" / "Unpin from Home" for a Library context menu.
function homePinMenuItem(pin) {
  if (!pin?.id) return null;
  const pinned = homePinned(pin.kind, pin.id);
  return pinned
    ? { label: 'Unpin from Home', action: () => unpinFromHome(homePinKey(pin)) }
    : { label: 'Pin to Home', action: () => pinToHome(pin) };
}

// The parent folder of a file, in its own spelling.
function homeFolderOf(path) {
  const cut = Math.max(String(path || '').lastIndexOf('/'), String(path || '').lastIndexOf('\\'));
  return cut > 0 ? path.slice(0, cut) : '';
}

function homeGenreKey(genre) {
  return String(genre || '').normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase()
    .replace(/[^\p{L}\p{N}_]+/gu, ' ').trim().replace(/\s+/g, ' ');
}

// ── Edit Home ──
let homeEditDraft = [];

function openEditHome() {
  homeEditDraft = (personalHome?.layout || []).map(s => ({ ...s }));
  if (!homeEditDraft.length) { toastMgr.error('Home is still loading.'); return; }
  renderEditHome();
  $('home-edit-modal')?.classList.remove('hidden');
}

function closeEditHome() { $('home-edit-modal')?.classList.add('hidden'); }

function renderEditHome() {
  const list = $('home-edit-list');
  if (!list) return;
  list.innerHTML = homeEditDraft.map((s, i) => `
    <li class="home-edit-row">
      <label class="home-edit-check"><input type="checkbox" data-home-edit-toggle="${i}" ${s.shown ? 'checked' : ''}>
        <span><span class="home-edit-name">${escHtml(s.title)}</span><span class="home-edit-about">${escHtml(s.about)}</span></span></label>
      <span class="home-edit-moves">
        <button type="button" class="btn ghost" data-home-edit-move="${i}" data-delta="-1" ${i === 0 ? 'disabled' : ''} aria-label="Move ${escHtml(s.title)} up">↑</button>
        <button type="button" class="btn ghost" data-home-edit-move="${i}" data-delta="1" ${i === homeEditDraft.length - 1 ? 'disabled' : ''} aria-label="Move ${escHtml(s.title)} down">↓</button>
      </span>
    </li>`).join('');
}

async function saveEditHome() {
  const api = window.pywebview?.api;
  const visible = Object.fromEntries(homeEditDraft.map(s => [s.id, !!s.shown]));
  const res = await api?.save_home_layout?.(homeEditDraft.map(s => s.id), visible);
  if (res?.status !== 'ok') { toastMgr.error(res?.error || 'Could not save Home.'); return; }
  closeEditHome();
  renderPersonalHome({ force: true });
}

async function resetEditHome() {
  const res = await window.pywebview?.api?.reset_home_layout?.();
  if (res?.status === 'ok') { homeEditDraft = res.layout; renderEditHome(); }
}

(function initPersonalHome() {
  document.addEventListener('click', (e) => {
    const t = e.target instanceof Element ? e.target : null;
    if (!t) return;
    const edit = t.closest('[data-home-edit-toggle],[data-home-edit-move]');
    if (edit) {
      if (edit.dataset.homeEditToggle != null) homeEditDraft[Number(edit.dataset.homeEditToggle)].shown = edit.checked;
      else { homeEditDraft = moveInList(homeEditDraft, Number(edit.dataset.homeEditMove), Number(edit.dataset.delta)); renderEditHome(); }
      return;
    }
    if (!t.closest('#recent-wrap')) return;
    const pinMenu = t.closest('[data-home-pin-menu]');
    if (pinMenu) {
      e.stopPropagation();
      const key = pinMenu.dataset.homePinMenu;
      openActionMenu(pinMenu, [
        { label: 'Open', action: () => openHomePin(key) },
        { label: 'Move to Front', action: () => moveHomePinToFront(key) },
        '-',
        { label: 'Unpin from Home', danger: true, action: () => unpinFromHome(key) },
      ]);
      return;
    }
    const mixMenu = t.closest('[data-home-mix-menu]');
    if (mixMenu) {
      e.stopPropagation();
      const mix = personalHome?.sections?.mixes?.[Number(mixMenu.dataset.homeMixMenu)];
      if (!mix) return;
      openActionMenu(mixMenu, [
        { label: 'Play', action: () => playHomeSongs(mix.songs, 0, false) },
        { label: 'Shuffle', action: () => playHomeSongs(mix.songs, 0, true) },
        { label: 'Save as Playlist', submenu: () => playlistPickerItems(mix.songs.map(homeSongItem)) },
      ], mix.title);
      return;
    }
    const quick = t.closest('[data-home-quick]');
    if (quick) { playHomeSongs(quickPlayQueue(personalHome.sections.quickPlay, Number(quick.dataset.homeQuick)), 0, false); return; }
    const pin = t.closest('[data-home-pin]');
    if (pin) { openHomePin(pin.dataset.homePin); return; }
    const mix = t.closest('[data-home-mix]');
    if (mix) { playHomeSongs(personalHome.sections.mixes[Number(mix.dataset.homeMix)]?.songs, 0, false); return; }
    const album = t.closest('[data-home-album]');
    if (album) { openHomeAlbum(album.dataset.homeAlbum, album.dataset.albumTitle); return; }
    const repeat = t.closest('[data-home-repeat]');
    if (repeat) { playHomeSongs(personalHome.sections.onRepeat.songs, Number(repeat.dataset.homeRepeat), false); return; }
    const reco = t.closest('[data-home-reco]');
    if (reco) {
      const track = personalHome.sections.recommended?.[Number(reco.dataset.homeReco)];
      if (!track) return;
      setSearchModeTo('search');
      $('urlInput').value = `${track.artist} ${track.title}`.trim();
      onFetch();
      return;
    }
    if (t.closest('[data-home-refresh-reco]')) { loadHomeRecommendations(true); return; }
    const row = t.closest('[data-home-unfinished]');
    if (row) {
      const which = row.dataset.homeUnfinished;
      if (which === 'incompleteAlbums') $('m-unfinished-albums')?.classList.toggle('hidden');
      else if (which === 'wishlist') openLibrary('playlists');
      else if (which === 'failedDownloads') switchView('queue');
    }
  });
})();

function loadAccentFromStorage() {
  const stored = (() => {
    try { return localStorage.getItem('mornye-accent'); } catch (e) { return null; }
  })() || 'amber';
  const accent = migrateAccent(stored);
  try { localStorage.setItem('mornye-accent', accent); } catch (e) {}
  if ($('config-accent')) $('config-accent').value = accent;
  applyAccent(accent);
}

function loadThemeFromStorage() {
  const stored = (() => {
    try { return localStorage.getItem('mornye-theme-mode'); } catch (e) { return null; }
  })() || 'auto';
  if ($('config-theme')) $('config-theme').value = stored;
  applyTheme(stored);
}

// Applies the slider to the audio element (if one exists yet) and to the
// label beside it. Called live while dragging, so a preview that is playing
// changes volume under your hand instead of on the next clip.
function changePreviewVolume() {
  const el = $('config-preview-volume');
  if (el) previewVolume = Math.max(0, Math.min(100, Number(el.value) || 0));
  const out = $('preview-volume-value');
  if (out) out.textContent = `${previewVolume}%`;
  if (previewAudio) previewAudio.volume = previewVolume / 100;
  try {
    const stored = JSON.parse(localStorage.getItem(SETTINGS_STORAGE_KEY) || '{}');
    stored.preview_volume = previewVolume;
    localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify(stored));
  } catch (e) {}
}

function changeFont() {
  // One typeface (redesign §2: JetBrains Mono for everything, from
  // css/tokens.css). A font saved by an older version, or chosen in the old
  // picker, no longer applies: whatever is inline is removed. #config-font
  // stays in the page, hidden, so settings that still carry the key load.
  document.documentElement.style.removeProperty('--app-font');
  document.documentElement.style.removeProperty('--font-display');
}

function onNotificationSoundChange() {
  const enabled = $('config-sound-enabled')?.checked ?? true;
  try {
    localStorage.setItem('mornye-sound-enabled', String(enabled));
    const stored = JSON.parse(localStorage.getItem(SETTINGS_STORAGE_KEY) || '{}');
    stored.sound_enabled = enabled;
    stored.notification_sound = enabled;
    localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify(stored));
  } catch (e) {}
  if (window.toastMgr) window.toastMgr.setSoundEnabled(enabled);
  isDirty = true;
  updateSaveButtonVisual();
}

function qualityFallbackChain(q) {
  const n = (q || '').toString().toUpperCase();
  // Dolby Atmos is Tidal-exclusive (see core/quality.py's
  // quality_for_provider()): on Tidal it falls back through hi-res to
  // plain lossless like shown here; on every other provider it's treated
  // as HI_RES_LOSSLESS from the start, which is still a fair chain to
  // show since this tooltip isn't provider-specific.
  const chains = {
      'DOLBY_ATMOS': ['DOLBY_ATMOS', 'HI_RES_LOSSLESS', 'LOSSLESS'],
      'HI_RES_LOSSLESS': ['HI_RES_LOSSLESS', 'LOSSLESS'],
      'LOSSLESS': ['LOSSLESS'],
  };
  return chains[n] || [n || 'LOSSLESS'];
}

function applySettings(settings = {}) {
  const cfg = { ...DEFAULT_SETTINGS, ...settings };
  if ($('config-quality')) {
      $('config-quality').value = cfg.quality;
      // show fallback chain as tooltip
      $('config-quality').title = qualityFallbackChain(cfg.quality).join(' → ');
      // update tooltip when user changes selection (use onchange to avoid duplicate listeners)
      $('config-quality').onchange = function() {
          const val = $('config-quality').value;
          $('config-quality').title = qualityFallbackChain(val).join(' → ');
          isDirty = true; updateSaveButtonVisual();
      };
  }
  if ($('config-fallback')) $('config-fallback').checked = cfg.allow_fallback;
  if ($('config-provider-fallback')) {
    $('config-provider-fallback').checked = cfg.fallback_services !== false;
  }
  if ($('config-ask-provider')) {
    $('config-ask-provider').checked = cfg.ask_provider_before_download === true;
  }
  // The dedicated key wins over the blob's copy: it is what the pre-paint
  // script in index.html already acted on, and a stale cfg.theme here would
  // otherwise flip the UI back a moment after load. When the key is absent —
  // the normal state in the desktop window, whose storage does not always
  // survive a restart — cfg.theme (from gui-settings.json, written by
  // save_theme()) is the surviving copy, so seed the key back from it and
  // the *next* launch gets the right colour before the first paint.
  let themeMode = cfg.theme;
  try {
    themeMode = localStorage.getItem('mornye-theme-mode') || cfg.theme;
    localStorage.setItem('mornye-theme-mode', themeMode);
  } catch (e) {}
  if ($('config-theme')) $('config-theme').value = themeMode;
  // Same dedicated-key-wins-over-blob dance as the theme above, for the
  // same reason: gui-settings.json is what survives, localStorage is what
  // the next launch reads first.
  let accent = cfg.accent || 'amber';
  try { accent = localStorage.getItem('mornye-accent') || accent; } catch (e) {}
  accent = migrateAccent(accent, cfg.brand);
  try { localStorage.setItem('mornye-accent', accent); } catch (e) {}
  if ($('config-accent')) $('config-accent').value = accent;
  let density = cfg.density || 'comfortable';
  try { density = localStorage.getItem('mornye-density') || density; } catch (e) {}
  if (!DENSITIES.includes(density)) density = 'comfortable';
  try { localStorage.setItem('mornye-density', density); } catch (e) {}
  if ($('config-density')) $('config-density').value = density;
  applyDensity(density);
  let style = cfg.style || STYLES[0];
  try { style = localStorage.getItem('mornye-style') || style; } catch (e) {}
  if (!STYLES.includes(style)) style = STYLES[0];
  try { localStorage.setItem('mornye-style', style); } catch (e) {}
  if ($('config-style')) $('config-style').value = style;
  applyStyle(style);
  const soundEnabled = cfg.sound_enabled !== false && cfg.notification_sound !== false;
  if ($('config-sound-enabled')) $('config-sound-enabled').checked = soundEnabled;
  if (window.toastMgr) window.toastMgr.setSoundEnabled(soundEnabled);
  if ($('config-font')) $('config-font').value = cfg.font;
  previewVolume = Number.isFinite(Number(cfg.preview_volume)) ? Number(cfg.preview_volume) : 100;
  if ($('config-preview-volume')) $('config-preview-volume').value = previewVolume;
  changeFont();
  changeTheme();
  changeAccent();
  changePreviewVolume();
  if ($('config-lyrics')) { $('config-lyrics').checked = cfg.lyrics; onLyricsChange(); }
  if ($('config-apple-wbw')) $('config-apple-wbw').checked = cfg.apple_lyrics_word_by_word !== false;
  if ($('config-enrich')) { $('config-enrich').checked = cfg.enrich_metadata; onEnrichChange(); }
  if ($('config-animated-artwork')) {
    $('config-animated-artwork').checked = cfg.save_animated_artwork === true;
  }
  if ($('config-animated-artwork-api-url')) {
    $('config-animated-artwork-api-url').value = cfg.animated_artwork_api_url || '';
  }
  updateApiConfigDisplay('animated');
  if ($('config-filename')) $('config-filename').value = cfg.filename_format;
  if ($('config-track-numbers')) { $('config-track-numbers').checked = cfg.use_track_numbers; onTNChange(); }
  if ($('config-album-track-numbers')) $('config-album-track-numbers').checked = cfg.use_album_track_numbers;
  if ($('config-artist-sub')) $('config-artist-sub').checked = cfg.use_artist_subfolders;
  if ($('config-album-sub')) $('config-album-sub').checked = cfg.use_album_subfolders;
  if ($('config-album-year')) $('config-album-year').checked = cfg.album_folder_year === true;
  if ($('config-playlist-sub')) $('config-playlist-sub').checked = cfg.create_playlist_subfolders !== false;
  if ($('config-library-scan-dirs')) {
    const dirs = cfg.library_scan_dirs;
    $('config-library-scan-dirs').value = Array.isArray(dirs) ? dirs.join('; ') : (dirs || '');
  }
  if ($('config-first-artist')) $('config-first-artist').checked = cfg.first_artist_only;
  if ($('config-artist-separator')) $('config-artist-separator').value = cfg.artist_separator || '';
  updateArtistSeparatorState(cfg.first_artist_only);
  if ($('config-transcode')) { $('config-transcode').value = cfg.transcode_to || 'none'; onTranscodeChange(); }
  if ($('config-transcode-bitrate')) $('config-transcode-bitrate').value = cfg.transcode_bitrate || '320k';
  if ($('config-transcode-keep')) $('config-transcode-keep').checked = cfg.transcode_keep_original;
  if ($('config-dolby-ac3')) $('config-dolby-ac3').checked = cfg.dolby_to_ac3 !== false;
  if ($('config-dolby-ac3-keep')) $('config-dolby-ac3-keep').checked = cfg.dolby_ac3_keep_original !== false;
  onDolbyAc3Change();
  if ($('config-verify-hires')) $('config-verify-hires').checked = !!cfg.verify_hires || !!cfg.redownload_fake_hires;
  if ($('config-redownload-fake-hires')) $('config-redownload-fake-hires').checked = !!cfg.redownload_fake_hires;
  onVerifyHiresChange();
  if ($('config-retries')) $('config-retries').value = cfg.track_max_retries;
  if ($('config-post-action')) { $('config-post-action').value = cfg.post_download_action; onPostChange(); }
  if ($('config-post-cmd')) $('config-post-cmd').value = cfg.post_download_command;
  extensionQualityChoices = { ...(cfg.extension_quality || {}) };
  renderExtensionQualityRows();
  if ($('config-animated-artwork-api-url')) $('config-animated-artwork-api-url').value = cfg.animated_artwork_api_url || '';
  if ($('config-acoustid-key')) $('config-acoustid-key').value = cfg.acoustid_api_key || '';
  if ($('config-loop')) $('config-loop').value = cfg.loop;
  if ($('config-loglevel')) $('config-loglevel').value = cfg.log_level;
  if ($('config-concurrent')) $('config-concurrent').value = cfg.max_concurrent_downloads || 2;
  if ($('config-metadata-delay-min')) $('config-metadata-delay-min').value = cfg.metadata_delay_min_s ?? 0;
  if ($('config-metadata-delay-max')) $('config-metadata-delay-max').value = cfg.metadata_delay_max_s ?? 0;
  if ($('config-provider-delay-min')) $('config-provider-delay-min').value = cfg.provider_delay_min_s ?? 0;
  if ($('config-provider-delay-max')) $('config-provider-delay-max').value = cfg.provider_delay_max_s ?? 0;
  if ($('config-auto-rate-limit-cooldown')) {
    $('config-auto-rate-limit-cooldown').checked = cfg.auto_rate_limit_cooldown !== false;
  }
  if ($('config-network-destination')) {
    $('config-network-destination').value = cfg.network_destination_id || '';
  }
  lastAppliedServices = Array.isArray(cfg.services) ? cfg.services : lastAppliedServices;
  applyListState('services-list', cfg.services);
  if (Array.isArray(cfg.lyrics_providers)) lastAppliedLyrics = cfg.lyrics_providers;
  applyListState('lyrics-list', cfg.lyrics_providers);
  applyListState('enrich-list', cfg.enrich_providers);
  updateAllApiConfigDisplays();
}

function renumberList(el) {
  // The priority number is the row's position, so it has to be rewritten
  // whenever the rows move — after a drag, and after applyListState()
  // reorders them to match the saved settings. It was only ever written at
  // build time, so a saved order showed its rows numbered 8, 6, 3, 1…
  if (!el) return;
  el.querySelectorAll('.sort-item').forEach((item, i) => {
    const num = item.querySelector('.priority-num');
    if (num) num.textContent = i + 1;
  });
}

function applyListState(id, values = []) {
  const el = $(id);
  if (!el) return;
  const items = Array.from(el.querySelectorAll('.sort-item'));
  items.forEach(item => {
    const cb = item.querySelector('input[type="checkbox"]');
    if (cb) cb.checked = values.includes(item.dataset.value);
    // The row was drawn with the default's on/off look; it follows the tick.
    item.classList.toggle('inactive', !(cb && cb.checked));
  });
  if (values.length) {
    values.forEach(value => {
      const item = el.querySelector(`.sort-item[data-value="${value}"]`);
      if (item) el.appendChild(item);
    });
    items.filter(i => !values.includes(i.dataset.value)).forEach(item => el.appendChild(item));
  }
  renumberList(el);
  if (id === 'services-list') refreshServicePriorityIndicators();
}

async function loadSettingsFromStorage() {
  try {
    let stored = null;
    if (window.pywebview?.api) {
      stored = await window.pywebview.api.load_settings();
    }
    if (!stored || !Object.keys(stored).length) {
      stored = JSON.parse(localStorage.getItem(SETTINGS_STORAGE_KEY) || 'null');
    }
    if (stored) applySettings(stored);
    else { loadThemeFromStorage(); loadAccentFromStorage(); }
  } catch(e) {
    loadThemeFromStorage();
    loadAccentFromStorage();
  }
}

// Track any changes in the settings fields. On #view-settings itself: the
// first `.s-body` in the page is the Player's, so the listener used to sit
// there and never saw a settings edit. `change` as well as `input`, for the
// controls that only report a committed value.
function refreshSettingsDirty() {
    const current = JSON.stringify(buildConfig());
    const initial = JSON.stringify(initialSettings);
    isDirty = (current !== initial);
    updateSaveButtonVisual();
}
['input', 'change'].forEach(type =>
    $('view-settings')?.addEventListener(type, refreshSettingsDirty));

function showToast(message, type = 'success') {
  toastMgr[type](message);
}

async function saveSettings() {
  try {
    const cfg = buildConfig();
    cfg.theme  = $('config-theme')?.value  || DEFAULT_SETTINGS.theme;
    cfg.accent = $('config-accent')?.value || DEFAULT_SETTINGS.accent;
    cfg.density = $('config-density')?.value || DEFAULT_SETTINGS.density;
    cfg.font   = $('config-font')?.value   || DEFAULT_SETTINGS.font;
    cfg.preview_volume = previewVolume;
    if (window.pywebview?.api) {
      await window.pywebview.api.save_settings(cfg);
    }
    localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify(cfg));
    isDirty = false;
    // buildConfig(), not cfg: cfg carries theme/accent/density on top, so
    // comparing against it made every later edit look unsaved forever.
    initialSettings = buildConfig();
    updateSaveButtonVisual();
    logMessage('Settings saved.', 'ok');
  } catch(e) {
    showToast('Unable to save settings.');
  }
}

function resetSettings() {
  try {
    localStorage.removeItem(SETTINGS_STORAGE_KEY);
    localStorage.removeItem('mornye-theme-mode');
    applySettings(DEFAULT_SETTINGS);
    isDirty = false; // Reset state
    initialSettings = buildConfig(); // Update the baseline to default
    updateSaveButtonVisual();
    logMessage('Settings reset to defaults.', 'ok');
  } catch (e) {
    logMessage('Unable to reset settings.', 'error');
  }
}

function openConfigFolder() {
  if (window.pywebview?.api?.open_config_folder) {
    window.pywebview.api.open_config_folder();
  } else {
    logMessage('Open config folder action is unavailable.', 'warn');
  }
}

// ── Settings helpers ─────────────────────────────────────────────────────────
function onTNChange() {
  $('album-track-row').style.display = $('config-track-numbers').checked ? 'flex' : 'none';
}
function onLyricsChange() {
  const on = $('config-lyrics').checked;
  $('lyrics-prov-wrap').style.opacity = on ? '1' : '.4';
  $('lyrics-prov-wrap').style.pointerEvents = on ? '' : 'none';
}
function onEnrichChange() {
  const on = $('config-enrich').checked;
  $('enrich-prov-wrap').style.opacity = on ? '1' : '.4';
  $('enrich-prov-wrap').style.pointerEvents = on ? '' : 'none';
}
function onPostChange() {
  $('post-cmd-row').style.display = $('config-post-action').value === 'command' ? 'flex' : 'none';
}
// Formats that carry no bitrate knob — must match core/transcode.LOSSLESS_FORMATS.
const LOSSLESS_TRANSCODE_FORMATS = ['flac', 'alac', 'wav', 'aiff', 'wavpack', 'tta'];

function onTranscodeChange() {
  const fmt = $('config-transcode') ? $('config-transcode').value : 'none';
  const on = fmt !== 'none';
  document.querySelectorAll('.transcode-opt').forEach(row => {
    row.style.display = on ? 'flex' : 'none';
  });
  // Bitrate is meaningless for a lossless target: the encoder re-encodes the
  // samples untouched, so the row would offer a setting that does nothing.
  const lossy = on && !LOSSLESS_TRANSCODE_FORMATS.includes(fmt);
  document.querySelectorAll('.transcode-lossy').forEach(row => {
    row.style.display = lossy ? 'flex' : 'none';
  });
}

// Replacing a fake Hi-Res file means first knowing it is one, so the
// replace toggle only exists while the check is on. Hidden rather than
// disabled: an off check makes the setting inapplicable, not unavailable.
function onDolbyAc3Change() {
  const on = $('config-dolby-ac3')?.checked === true;
  document.querySelectorAll('.dolby-ac3-opt').forEach(row => {
    row.style.display = on ? 'flex' : 'none';
  });
}

function onVerifyHiresChange() {
  const on = $('config-verify-hires') ? $('config-verify-hires').checked : false;
  document.querySelectorAll('.verify-hires-opt').forEach(row => {
    row.style.display = on ? 'flex' : 'none';
  });
  // Clearing the nested toggle is the whole point of doing this here. The
  // backend reads "replace" as implying "verify" (one cannot act on a
  // finding it never made), so a checkbox left on behind a hidden row
  // would switch the check the user just turned off straight back on.
  if (!on && $('config-redownload-fake-hires')) {
    $('config-redownload-fake-hires').checked = false;
  }
}

// ── Sortable lists ───────────────────────────────────────────────────────────
function makeSortable(el) {
  let drag = null;
  let startingOrder = [];
  function onDS(e) {
    drag = e.currentTarget;
    startingOrder = [...el.querySelectorAll('.sort-item')].map(item => item.dataset.value);
    setTimeout(() => drag?.classList.add('dragging'), 0);
  }
  function onDE() {
    drag?.classList.remove('dragging');
    el.querySelectorAll('.sort-item').forEach(i => i.classList.remove('drag-over'));
    drag = null;
    renumberList(el);
    if (el.id === 'services-list') {
      refreshServicePriorityIndicators();
      const currentOrder = [...el.querySelectorAll('.sort-item')].map(item => item.dataset.value);
      if (currentOrder.some((value, i) => value !== startingOrder[i])) markSettingsDirty();
    }
  }
  function onDO(e) {
    e.preventDefault();
    if (!drag || drag.parentElement !== el) return;
    const items = [...el.querySelectorAll('.sort-item:not(.dragging)')];
    const after = items.find(i => e.clientY < i.getBoundingClientRect().top + i.getBoundingClientRect().height / 2);
    items.forEach(i => i.classList.remove('drag-over'));
    if (after) { after.classList.add('drag-over'); el.insertBefore(drag, after); }
    else el.appendChild(drag);
  }
  const apply = () => {
    el.querySelectorAll('.sort-item').forEach(item => {
      item.setAttribute('draggable', 'true');
      item.removeEventListener('dragstart', onDS); item.removeEventListener('dragend', onDE);
      item.addEventListener('dragstart', onDS); item.addEventListener('dragend', onDE);
    });
  };
  el.addEventListener('dragover', onDO);
  el.addEventListener('dragleave', e => {
    if (!el.contains(e.relatedTarget)) el.querySelectorAll('.sort-item').forEach(i => i.classList.remove('drag-over'));
  });
  apply(); return apply;
}

// ── Data definitions ─────────────────────────────────────────────────────────
const ALL_SERVICES = [
  { id:'tidal',       label:'Tidal',          badge:'FLAC', on:true,  icon:'T',  iconClass:'tidal', iconFile:'tidal_l.png' },
  { id:'qobuz',       label:'Qobuz',          badge:'FLAC', on:true,  icon:'Q',  iconClass:'qobuz', iconFile:'qbz.png' },
  { id:'deezer',      label:'Deezer',         badge:'FLAC', on:true,  icon:'D',  iconClass:'deezer', iconFile:'dzr.png' },
  { id:'amazon',      label:'Amazon Music',   badge:'FLAC', on:true,  icon:'AM', iconClass:'amazon', iconFile:'amzn.png' },
  { id:'joox',        label:'Joox',           badge:'FLAC', on:false, icon:'JX', iconClass:'joox', iconFile:'joox.svg' },
  { id:'netease',     label:'NetEase',        badge:'FLAC', on:false, icon:'NE', iconClass:'netease', iconFile:'netease.svg' },
  { id:'migu',        label:'Migu',           badge:'FLAC', on:false, icon:'MG', iconClass:'migu', iconFile:'migu.jpeg' },
  { id:'kuwo',        label:'Kuwo',           badge:'FLAC', on:false, icon:'KW', iconClass:'kuwo', iconFile:'kuwo.png' },
  { id:'soundcloud',  label:'SoundCloud',     badge:'MP3',  on:false, icon:'SC', iconClass:'soundcloud', iconFile:'soundcloud.svg' },
  { id:'youtube',     label:'YouTube Music',  badge:'M4A',  on:false, icon:'YT', iconClass:'youtube', iconFile:'youtube.svg' },
  { id:'apple',       label:'Apple Music',    badge:'M4A',  on:false, icon:'AM', iconClass:'apple', iconFile:'am.png' },
  { id:'pandora',     label:'Pandora',        badge:'MP3',  on:false, icon:'P',  iconClass:'pandora', iconFile:'pandora.svg' },
];
//: Logos shipped for services that come from extensions rather than the
//: built-in list (ids as get_download_services() names them).
const EXTENSION_SERVICE_ICONS = {
  beatport: 'beatport.png',
  jellyfin: 'jellyfin.png',
  jiosaavn: 'jiosaavn.png',
  'melon-music': 'melon-music.png',
  'bugs-music': 'bugs-music.png',
  'genie-music': 'genie-music.png',
};
const ALL_LYRICS = [
  { id:'apple',      label:'Apple Music',on:true, iconFile:'am.png', iconClass:'apple' },
  { id:'binilyrics', label:'BiniLyrics', on:true, icon:'BL', iconClass:'binilyrics', iconFile:'binilyrics.svg' },
  { id:'lrclib',     label:'LRCLib',     on:true,  iconFile:'lrclib.png', iconClass:'lrclib' },
  { id:'lyricsplus', label:'LyricsPlus', on:false, icon:'LP', iconClass:'lyricsplus', iconFile:'lyricsplus.svg' },
  { id:'lrcred',     label:'lrc.red',    on:false, icon:'LR', iconClass:'lrcred', iconFile:'lrcred.svg' },
  { id:'unison',     label:'Unison',     on:false, icon:'UN', iconClass:'unison', iconFile:'unison.png' },
  { id:'jiosaavn',   label:'JioSaavn',   on:false, icon:'JS', iconClass:'jiosaavn', iconFile:'jiosaavn.png' },
  { id:'kugou',      label:'KuGou',      on:false, icon:'KG', iconClass:'kugou', iconFile:'kugou.png' },
  { id:'qq',         label:'QQ Music',   on:false, icon:'QQ', iconClass:'qq', iconFile:'qq.png' },
  { id:'genius',     label:'Genius',     on:false, icon:'GE', iconClass:'genius', iconFile:'genius.png' },
  { id:'spotify',    label:'Spotify',    on:false, iconFile:'spotify.svg', iconClass:'spotify' },
];
const ALL_ENRICH = [
  { id:'deezer',     label:'Deezer',     on:true, iconFile:'dzr.png', iconClass:'deezer' },
  { id:'apple',      label:'Apple Music',on:true, iconFile:'am.png', iconClass:'apple' },
];

const SETTINGS_STORAGE_KEY = 'mornye-settings';
const DEFAULT_SETTINGS = {
  theme: 'auto',
  accent: 'amber',
  density: 'comfortable',
  sound_enabled: true,
  notification_sound: true,
  preview_volume: 100,
  font: "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
  quality: 'LOSSLESS',
  allow_fallback: false,
  fallback_services: true,
  ask_provider_before_download: false,
  lyrics: true,
  enrich_metadata: true,
  save_animated_artwork: false,
  animated_artwork_api_url: 'https://artwork.m8tec.top',
  filename_format: '{title} - {artist}',
  use_track_numbers: false,
  use_album_track_numbers: false,
  use_artist_subfolders: true,
  use_album_subfolders: true,
  album_folder_year: false,
  create_playlist_subfolders: true,
  library_scan_dirs: '',
  first_artist_only: false,
  artist_separator: '',
  track_max_retries: 0,
  post_download_action: 'none',
  post_download_command: '',
  transcode_to: 'none',
  transcode_bitrate: '320k',
  transcode_keep_original: false,
  dolby_to_ac3: true,
  dolby_ac3_keep_original: true,
  verify_hires: false,
  redownload_fake_hires: false,
  // Per-service quality id from each extension's manifest qualityOptions,
  // e.g. { amazon: 'ac4' }. Missing = use the global tier.
  extension_quality: {},
  acoustid_api_key: '',
  loop: 0,
  log_level: 'INFO',
  max_concurrent_downloads: 2,
  metadata_delay_min_s: 0,
  metadata_delay_max_s: 0,
  provider_delay_min_s: 0,
  provider_delay_max_s: 0,
  auto_rate_limit_cooldown: true,
  services: ['tidal','qobuz','deezer','amazon','joox','netease','migu','kuwo','apple','soundcloud','youtube','pandora'],
  lyrics_providers: ['apple', 'binilyrics', 'lrclib'],
  apple_lyrics_word_by_word: true,
  enrich_providers: ['deezer','apple'],
};

// Ensure the version is populated even if pywebview API isn't ready yet
async function fetchVersionWithRetry(retries = 10, delayMs = 200) {
  for (let i = 0; i < retries; i++) {
    try {
      if (window.pywebview?.api && typeof window.pywebview.api.get_version === 'function') {
        const local = await window.pywebview.api.get_version();
        // The version on show is the published one on PyPI — what `pip
        // install mornye` gives you — with the local build's own number as
        // the fallback when PyPI cannot be reached.
        let v = local;
        try {
          const pypi = await window.pywebview.api.get_pypi_version?.();
          if (pypi?.version) v = pypi.version;
        } catch (e) { /* offline: keep the local version */ }
        const label = v && v !== 'unknown' ? `v${v}` : 'v...';
        for (const id of ['tb-version', 'hero-version', 'sb-version']) {
          const el = document.getElementById(id);
          if (el) el.innerText = label;
        }
        if (v && v !== 'unknown' && v !== '...') {
          await checkLatestVersion(v);
        }
        return;
      }
    } catch (e) {
      /* ignore */
    }
    await new Promise(r => setTimeout(r, delayMs));
  }
}

document.addEventListener('DOMContentLoaded', () => fetchVersionWithRetry(20, 200));
// Sync the collapse button's label/aria with the state the <body> script restored.
document.addEventListener('DOMContentLoaded', () => toggleSidebar(document.body.classList.contains('sidebar-collapsed')));

const UPDATE_RELEASE_URL = 'https://github.com/mornyeapp/Mornye-Python/releases';

function normalizeVersionString(version) {
  return String(version || '').trim().replace(/^v/i, '');
}

function compareVersionStrings(a, b) {
  const normalize = (value) => String(value || '').split(/[.\-+]/).map(part => {
    const num = Number(part);
    return Number.isNaN(num) ? part : num;
  });
  const partsA = normalize(a);
  const partsB = normalize(b);
  const maxLen = Math.max(partsA.length, partsB.length);

  for (let i = 0; i < maxLen; i++) {
    const partA = partsA[i] !== undefined ? partsA[i] : 0;
    const partB = partsB[i] !== undefined ? partsB[i] : 0;

    if (typeof partA === 'number' && typeof partB === 'number') {
      if (partA !== partB) return partA > partB ? 1 : -1;
      continue;
    }

    const aStr = String(partA);
    const bStr = String(partB);
    if (aStr !== bStr) return aStr > bStr ? 1 : -1;
  }
  return 0;
}

function showUpdateBadge(latestVersion, publishedAt) {
  const tbBadge = document.getElementById('tb-update-badge');
  const heroBadge = document.getElementById('hero-update-badge');
  const title = latestVersion ? `Update available: v${latestVersion}` : 'Update available';
  if (tbBadge) {
    tbBadge.title = publishedAt ? `${title}\nReleased: ${publishedAt}` : title;
    tbBadge.classList.remove('hidden');
  }
  if (heroBadge) {
    heroBadge.title = publishedAt ? `${title}\nReleased: ${publishedAt}` : title;
    heroBadge.classList.remove('hidden');
  }
}

async function openReleasePage() {
  if (window.pywebview?.api?.open_url) {
    window.pywebview.api.open_url(UPDATE_RELEASE_URL);
  } else {
    window.open(UPDATE_RELEASE_URL, '_blank');
  }
}

async function checkLatestVersion(currentVersion) {
  const normalizedCurrent = normalizeVersionString(currentVersion);
  if (!normalizedCurrent || normalizedCurrent === 'unknown' || normalizedCurrent === '...') return;
  if (!window.pywebview?.api || typeof window.pywebview.api.get_latest_version !== 'function') return;

  try {
    const info = await window.pywebview.api.get_latest_version();
    const latestVersion = normalizeVersionString(info?.latest_version);
    if (latestVersion && compareVersionStrings(latestVersion, normalizedCurrent) > 0) {
      showUpdateBadge(latestVersion, info?.published_at || '');
    }
  } catch (error) {
    console.warn('Failed to check for updates:', error);
  }
}

function buildSortItem(item, index) {
  const d = document.createElement('div');
  d.className = `sort-item ${item.on ? '' : 'inactive'}`;
  d.dataset.value = item.id;
  
  const iconHtml = item.iconFile
    ? `<span class="svc-icon ${item.iconClass} icon-image"><img src="assets/icons/${item.iconFile}" alt="${item.label}" data-onerror="this.onerror=null; this.src='assets/icons/${item.id}.png';"></span>`
    : item.icon ? `<span class="svc-icon ${item.iconClass}">${item.icon}</span>` : '';
  
  // Add the number (index + 1) and the checkbox
  if (item.title) d.title = `Provided by ${item.title}`;
  d.innerHTML = `
    <span class="priority-num">${index + 1}</span>
    <span class="drag-h">⠿</span>
    ${iconHtml}
    <input type="checkbox" ${item.on ? 'checked' : ''} data-onclick="event.stopPropagation(); toggleItemActive(this)">
    <span class="svc-name">${item.label}</span>
    ${item.badge ? `<span class="svc-badge">${item.badge}</span>` : ''}
  `;
  return d;
}

function buildServiceSortItem(item, index) {
  const d = document.createElement('div');
  d.className = `sort-item service-sort-item ${item.on ? '' : 'inactive'}`;
  d.dataset.value = item.id;
  if (item.title) d.title = `Provided by ${item.title}`;
  // A logo shipped with the app, else the one the extension itself ships
  // (iconUrl, a data URL from get_download_services), else two letters.
  const iconHtml = item.iconFile
    ? `<span class="svc-icon ${item.iconClass} icon-image"><img src="assets/icons/${item.iconFile}" alt="" data-onerror="this.onerror=null; this.src='assets/icons/${item.id}.png';"></span>`
    : item.iconUrl && /^data:image\//.test(item.iconUrl)
      ? `<span class="svc-icon ${item.iconClass} icon-image"><img src="${escHtml(item.iconUrl)}" alt=""></span>`
      : item.icon ? `<span class="svc-icon ${item.iconClass}">${escHtml(item.icon)}</span>` : '';
  d.innerHTML = `
    <span class="priority-num">${index + 1}</span>
    <span class="drag-h" aria-hidden="true">⠿</span>
    ${iconHtml}
    <span class="svc-name">${escHtml(item.label)}</span>
    <span class="service-order-status"></span>
    ${item.badge ? `<span class="svc-badge">${escHtml(item.badge)}</span>` : ''}
    <div class="service-row-actions">
      <button type="button" class="service-move" data-onclick="moveService(this, -1)" aria-label="Move ${escHtml(item.label)} up" title="Move up">↑</button>
      <button type="button" class="service-move" data-onclick="moveService(this, 1)" aria-label="Move ${escHtml(item.label)} down" title="Move down">↓</button>
      <label class="service-enable" title="${item.on ? 'Enabled download source' : 'Enable download source'}">
        <input type="checkbox" ${item.on ? 'checked' : ''} data-onchange="onServiceEnabledChange(this)" aria-label="Enable ${escHtml(item.label)} as a download source">
        <span>${item.on ? 'On' : 'Off'}</span>
      </label>
    </div>`;
  return d;
}

function markSettingsDirty() {
  isDirty = true;
  updateSaveButtonVisual();
}

function refreshServicePriorityIndicators() {
  const el = $('services-list');
  if (!el) return;
  let activeRank = 0;
  el.querySelectorAll('.service-sort-item').forEach((item, index) => {
    const enabled = item.querySelector('input[type="checkbox"]')?.checked;
    item.querySelector('.priority-num').textContent = String(index + 1).padStart(2, '0');
    const status = item.querySelector('.service-order-status');
    if (enabled) {
      activeRank += 1;
      status.textContent = activeRank === 1 ? 'Primary' : `Fallback ${activeRank - 1}`;
      item.classList.toggle('service-primary', activeRank === 1);
    } else {
      status.textContent = 'Disabled';
      item.classList.remove('service-primary');
    }
    item.querySelectorAll('.service-move').forEach((button, moveIndex) => {
      button.disabled = moveIndex === 0 ? index === 0 : index === el.children.length - 1;
    });
    const enableLabel = item.querySelector('.service-enable span');
    if (enableLabel) enableLabel.textContent = enabled ? 'On' : 'Off';
  });
  const count = el.querySelectorAll('input[type="checkbox"]:checked').length;
  const summary = $('services-summary');
  if (summary) summary.textContent = `${count} enabled · first enabled source is tried first`;
}

function moveService(button, direction) {
  const item = button.closest('.service-sort-item');
  const list = $('services-list');
  const target = direction < 0 ? item?.previousElementSibling : item?.nextElementSibling;
  if (!item || !target || !list) return;
  if (direction < 0) list.insertBefore(item, target);
  else list.insertBefore(target, item);
  refreshServicePriorityIndicators();
  markSettingsDirty();
}

function onServiceEnabledChange(input) {
  const item = input.closest('.service-sort-item');
  if (item) item.classList.toggle('inactive', !input.checked);
  refreshServicePriorityIndicators();
  markSettingsDirty();
}

function toggleItemActive(cb) {
  const item = cb.closest('.sort-item');
  item.classList.toggle('inactive', !cb.checked);
}

function populateList(id, items) {
  const el = $(id); el.innerHTML = '';
  items.forEach((i, idx) => el.appendChild(
    id === 'services-list' ? buildServiceSortItem(i, idx) : buildSortItem(i, idx)
  ));
  makeSortable(el);
  if (id === 'services-list') refreshServicePriorityIndicators();
}
function getChecked(id) {
  return [...$(id).querySelectorAll('.sort-item')]
    .filter(el => el.querySelector('input[type="checkbox"]').checked)
    .map(el => el.dataset.value);
}

// The download sources are the installed extensions and nothing else: the
// list starts empty and refreshInstalledServices() fills it from the
// backend. A fresh install with no extension shows that, not twelve
// services that could only fail.
function showServicesPlaceholder(message, withLink = false) {
  const el = $('services-list');
  if (!el) return;
  const link = withLink
    ? ` <a href="#" data-onclick="openExtensionsSettings(); return false;">Settings → Extensions</a>.`
    : '';
  el.innerHTML = `<div class="s-label u-fs-11_5 u-p-6-2">${escHtml(message)}${link}</div>`;
}

showServicesPlaceholder('Reading the installed extensions…');
populateList('lyrics-list',   ALL_LYRICS.map(x => ({ ...x, badge: null })));
populateList('enrich-list',   ALL_ENRICH.map(x => ({ ...x, badge: null })));

// ALL_SERVICES above is presentation only — the icon, the badge, the label —
// and it paints instantly so Settings is never empty. What can actually be
// downloaded from is decided by the installed extensions, which only the
// backend knows: refreshInstalledServices() replaces the list with those,
// exactly as the interactive wizard does (both read
// extensions/catalog.installed_download_services).
// The backend installed or removed extensions (startup install finished, a
// registry was added): the provider list and the Extensions page re-read.
window.app_extensions_changed = function () {
  refreshInstalledServices();
  refreshLyricsExtensions();
  if ($('tc-extensions')?.classList.contains('active')) {
    loadExtensionSettings?.();
    loadExtensionHealth?.();
  }
};

// The lyrics providers saved last, so an extension's entry ("ext:jellyfin")
// keeps its place and tick even though the list is drawn before the backend
// has said which extensions are installed.
let lastAppliedLyrics = null;

// Extensions of type lyrics_provider (core/extension_lyrics.py) join the
// built-in lyrics providers, off by default, ranked like any other.
async function refreshLyricsExtensions() {
  const api = window.pywebview?.api;
  if (typeof api?.get_lyrics_extensions !== 'function' || !$('lyrics-list')) return;
  let rows;
  try {
    rows = (await api.get_lyrics_extensions())?.extensions || [];
  } catch (e) {
    return;
  }
  const el = $('lyrics-list');
  const shown = [...el.querySelectorAll('.sort-item')].map((x) => x.dataset.value);
  const wanted = getChecked('lyrics-list');
  (lastAppliedLyrics || []).forEach((id, i) => {
    if (!shown.includes(id) && rows.some((r) => r.id === id)) {
      wanted.splice(Math.min(i, wanted.length), 0, id);
    }
  });
  const builtIn = ALL_LYRICS.map((x) => ({ ...x, badge: null }));
  const fromExtensions = rows.map((r) => ({
    id: r.id, label: escHtml(r.label), on: false, icon: 'EX', iconClass: 'ext',
    title: `the ${r.label} extension`,
  }));
  populateList('lyrics-list', [...builtIn, ...fromExtensions]);
  applyListState('lyrics-list', wanted);
}

async function refreshInstalledServices() {
  if (typeof window.pywebview?.api?.get_download_services !== 'function') {
    // An older backend that cannot say what is installed: the built-in list.
    populateList('services-list', ALL_SERVICES);
    applyListState('services-list', lastAppliedServices);
    return;
  }
  let services;
  try {
    const result = await window.pywebview.api.get_download_services();
    services = result?.services;
  } catch (e) {
    console.warn('[services] could not read the installed providers:', e);
    showServicesPlaceholder('Could not read the installed extensions.');
    return;
  }
  // Nothing installed: say so instead of offering sources that cannot work.
  if (!Array.isArray(services) || !services.length) {
    installedServiceRows = [];
    renderExtensionQualityRows();
    showServicesPlaceholder('No download extension is installed yet. Add a registry in', true);
    return;
  }

  installedServiceRows = services;
  renderExtensionQualityRows();

  const known = new Map(ALL_SERVICES.map(x => [x.id, x]));
  const items = services.map(svc => {
    const preset = known.get(svc.id);
    // The JS extension downloads first; the Python one, if any, is its
    // fallback. Say so in the tooltip, and take the badge from what the
    // primary extension's manifest says it delivers.
    const title = serviceTitle(svc);
    const badge = serviceBadge(svc);
    if (preset) return { ...preset, badge: badge || preset.badge, title, iconUrl: svc.icon || '' };
    // A provider this build has no artwork or label for — a third-party
    // extension, most likely. It still belongs in the list, with the logo
    // its extension ships when there is one.
    return {
      id: svc.id,
      label: svc.label || svc.id,
      badge,
      on: false,
      icon: (svc.id || '?').slice(0, 2).toUpperCase(),
      iconFile: EXTENSION_SERVICE_ICONS[svc.id] || '',
      iconUrl: svc.icon || '',
      iconClass: svc.id,
      title,
    };
  });

  populateList('services-list', items);
  // Re-apply what was saved: populateList rebuilt the rows, so the order and
  // the ticks that applySettings() put there are gone with them.
  applyListState('services-list', lastAppliedServices.filter(id => known.has(id) || services.some(s => s.id === id)));
}

//: What applySettings() last put in the services list, so a refresh that
//: arrives after it can restore the user's order instead of the defaults.
let lastAppliedServices = ALL_SERVICES.filter(s => s.on).map(s => s.id);

// ── Per-service quality (extension qualityOptions) ───────────────────────────
// `var`, not `let`: applySettings() can run before this part of the file has
// been evaluated, and a `let` would still be in its temporal dead zone.
//: Rows from get_download_services(), with each service's quality_options.
var installedServiceRows = [];
//: { serviceId: qualityOptionId } — what the user picked per service.
var extensionQualityChoices = {};

function serviceTitle(svc) {
  const exts = svc.extensions || [];
  if (!svc.primary || exts.length < 2) return exts.join(', ');
  const fallback = exts.filter(e => e !== svc.primary);
  return `${svc.primary} (fallback: ${fallback.join(', ')})`;
}

function serviceBadge(svc) {
  const opts = svc.quality_options || [];
  if (!opts.length) return null;
  if (svc.atmos) return 'ATMOS';
  const text = o => `${o.id} ${o.label}`.toLowerCase();
  if (opts.some(o => o.kind === 'lossless' || /flac|lossless|alac/.test(text(o)))) return 'FLAC';
  const first = opts[0];
  const m = /(aac|opus|mp3|m4a)/.exec(text(first));
  return m ? m[1].toUpperCase() : null;
}

function renderExtensionQualityRows() {
  const section = $('ext-quality-section');
  const box = $('ext-quality-rows');
  if (!section || !box) return;
  const rows = (installedServiceRows || []).filter(s => (s.quality_options || []).length > 1);
  section.style.display = rows.length ? '' : 'none';
  box.innerHTML = rows.map(svc => {
    const current = extensionQualityChoices?.[svc.id] || '';
    const opts = [`<option value="">Use global quality</option>`].concat(
      svc.quality_options.map(o =>
        `<option value="${regEscapeHtml(o.id)}" ${o.id === current ? 'selected' : ''} title="${regEscapeHtml(o.description || '')}">${regEscapeHtml(o.label || o.id)}</option>`)
    ).join('');
    return `<div class="extension-quality-row">
      <span class="s-label">${regEscapeHtml(svc.label || svc.id)}<small>${regEscapeHtml(svc.primary || '')}</small></span>
      <select aria-label="Quality for ${regEscapeHtml(svc.label || svc.id)}" data-service="${regEscapeHtml(svc.id)}" data-onchange="onExtensionQualityChange(this)">${opts}</select>
    </div>`;
  }).join('');
}

function onExtensionQualityChange(select) {
  const service = select.dataset.service;
  if (!service) return;
  if (select.value) extensionQualityChoices[service] = select.value;
  else delete extensionQualityChoices[service];
  markSettingsDirty();
}

function collectExtensionQuality() {
  const out = {};
  for (const [service, id] of Object.entries(extensionQualityChoices || {})) {
    if (id) out[service] = id;
  }
  return out;
}

// ── HC chips ─────────────────────────────────────────────────────────────────
const API_SOURCES = [
  { id:'apple',      type:'apple',      name:'Apple Music Lyrics', url:'' },
  { id:'lyricsplus', type:'lyricsplus', name:'LyricsPlus',          url:'' },
  { id:'lrcred',     type:'lrcred',     name:'lrc.red',             url:'' },
  { id:'lrclib',     type:'lrclib',     name:'LRCLIB',             url:'' },
  { id:'spotify',    type:'spotify',    name:'Spotify Lyrics',     url:'' },
  { id:'binilyrics', type:'binilyrics', name:'BiniLyrics',        url:'' },
  { id:'unison',     type:'unison',     name:'Unison',            url:'' },
  { id:'jiosaavn',   type:'jiosaavn',   name:'JioSaavn',          url:'' },
];
let apiStatusState = {
  checkingSources: {},
  statuses: {},
};

function renderStatusIcon(status) {
  if (status === 'online') return '<span class="status-icon-dot online">✓</span>';
  if (status === 'offline') return '<span class="status-icon-dot offline">✗</span>';
  if (status === 'checking') return '<span class="status-icon-dot checking"></span>';
  return '<span class="status-icon-dot idle"></span>';
}
/**
 * Copies the visible application logs to the clipboard.
 */
function copyLogs() {
    const logArea = $('logArea');
    if (!logArea) {
        logMessage('Error: Log area not found.', 'error');
        return;
    }

    // Extract just the plain text (without the HTML tags)
    const logsText = logArea.innerText;

    if (navigator.clipboard) {
        navigator.clipboard.writeText(logsText).then(() => {
            toastMgr.success('Logs copied to clipboard!');
        }).catch(err => {
            toastMgr.error('Error copying logs.');
        });
    } else {
        // Fallback for older browsers
        const textArea = document.createElement("textarea");
        textArea.value = logsText;
        document.body.appendChild(textArea);
        textArea.select();
        document.execCommand("copy");
        document.body.removeChild(textArea);
        toastMgr.info('Logs copied (fallback).');
    }
}

/**
 * Creates the HTML icon markup for a platform or extension type.
 * @param {string} type - The platform or extension identifier.
 * @returns {string} HTML markup containing the corresponding icon.
 */
function renderPlatformIcon(type) {
  if (type === 'extensions') {
    return '<span class="svc-icon icon-glyph extensions">🧩</span>';
  }
  const iconMap = {
    tidal: 'tidal_l.png',
    qobuz: 'qbz.png',
    deezer: 'dzr.png',
    amazon: 'amzn.png',
    apple: 'am.png',
    soundcloud: 'soundcloud.svg',
    pandora: 'pandora.svg',
    youtube: 'youtube.svg',
    musicbrainz: 'musicbrainz_l.png',
    kuwo: 'kuwo.png',
    joox: 'joox.svg',
    netease: 'netease.svg',
    migu: 'migu.jpeg',
    songstats: 'songstats.png',
    ...EXTENSION_SERVICE_ICONS,
  };
  // The provider tables carry their own artwork (LRCLib, lrc.red, Unison,
  // BiniLyrics, …): the Status tab used to show letters for those.
  const known = [...ALL_SERVICES, ...ALL_LYRICS, ...ALL_ENRICH].find(x => x.id === type);
  const iconFile = iconMap[type] || known?.iconFile;
  if (!iconFile) {
    // No artwork shipped for this one. The provider tables already carry a
    // letter glyph for exactly this case; guessing at `${type}.svg` and then
    // at `${type}.png` just put two 404s and a broken-image icon on screen.
    const glyph = known?.icon || (type || '?').slice(0, 2).toUpperCase();
    return `<span class="svc-icon icon-glyph ${type}">${escHtml(glyph)}</span>`;
  }
  return `<span class="svc-icon icon-image ${type}"><img src="assets/icons/${iconFile}" alt="${type}" data-onerror="this.onerror=null; this.src='assets/icons/${type}.png';"></span>`;
}

function buildStatusCard(source) {
  const status = apiStatusState.statuses[source.id] || 'idle';
  const checking = apiStatusState.checkingSources[source.id] === true;
  return `<div class="status-card">
    <div class="status-card-header">
      <div class="status-card-left">
        ${renderPlatformIcon(source.id)}
        <div class="status-card-name">${source.name}</div>
      </div>
      ${renderStatusIcon(checking ? 'checking' : status)}
    </div>
  </div>`;
}

/** Renders the extension and lyrics provider status cards. */
function renderStatusGrids() {
  renderExtensionStatusGrid();
  const servicesGrid = $('status-services-grid');
  if (servicesGrid) {
    servicesGrid.innerHTML = API_SOURCES
      .filter((source) => apiStatusState.statuses[source.id] !== 'none')
      .map((source) => buildStatusCard(source))
      .join('');
  }
}

// ── Extension checks (manifest `serviceHealth`, core/health_check.py) ──────
// `var`: renderStatusGrids() runs while this file is still being evaluated.
var extensionStatusRows = [];
var extensionStatusChecking = false;
var extensionStatusLoaded = false;

function extensionStatusOf(row) {
  if (extensionStatusChecking) return 'checking';
  if (row.ok === true) return 'online';
  if (row.ok === false) return 'offline';
  return 'idle';
}

function renderExtensionStatusGrid() {
  const grid = $('status-extensions-grid');
  if (!grid) return;
  if (!extensionStatusRows.length) {
    grid.innerHTML = `<div class="s-label u-fs-11_5 u-grid-column-1-n1">${
      extensionStatusLoaded
        ? 'No installed extension declares a health check.'
        : 'Loading the installed extensions…'}</div>`;
    return;
  }
  grid.innerHTML = extensionStatusRows.map((row) => {
    const checks = (row.checks || []).map((c) => {
      const state = c.ok === true ? '✓' : c.ok === false ? '✗' : '·';
      const latency = c.latency >= 0 ? ` · ${c.latency} ms` : '';
      const detail = c.detail ? ` — ${c.detail}` : '';
      return `<div class="s-label u-fs-11" title="${regEscapeHtml(c.url)}">${state} ${regEscapeHtml(c.label)}${regEscapeHtml(detail)}${latency}</div>`;
    }).join('');
    return `<div class="status-card" title="${regEscapeHtml(row.name)}">
      <div class="status-card-header">
        <div class="status-card-left">
          ${renderPlatformIcon(row.service)}
          <div class="status-card-name">${regEscapeHtml(row.display_name)}</div>
        </div>
        ${renderStatusIcon(extensionStatusOf(row))}
      </div>
      ${checks}
    </div>`;
  }).join('');
}

async function loadExtensionStatusList() {
  if (!window.pywebview?.api?.check_extension_health) return;
  try {
    const res = await window.pywebview.api.check_extension_health(null, false);
    // Keep the last result of an extension that is still installed.
    const previous = new Map(extensionStatusRows.map((row) => [row.name, row]));
    extensionStatusRows = (res?.extensions || []).map((row) => previous.get(row.name) || row);
  } catch (e) {
    extensionStatusRows = [];
  }
  extensionStatusLoaded = true;
  renderExtensionStatusGrid();
}

async function checkExtensions() {
  if (!window.pywebview?.api?.check_extension_health) return { ok: 0, total: 0 };
  extensionStatusChecking = true;
  renderExtensionStatusGrid();
  try {
    const res = await window.pywebview.api.check_extension_health(null, true);
    extensionStatusRows = res?.extensions || [];
  } catch (e) {
    extensionStatusRows = extensionStatusRows.map((row) => ({ ...row, ok: false }));
  } finally {
    extensionStatusChecking = false;
    extensionStatusLoaded = true;
    renderExtensionStatusGrid();
  }
  return {
    ok: extensionStatusRows.filter((row) => row.ok === true).length,
    total: extensionStatusRows.length,
  };
}

/**
 * Updates the health-check summary label.
 * @param {string} text - The summary text to display.
 */
/** The overall pill from what the cards show: extensions and lyrics. */
function updateOverallStatusFromCards() {
  const lyricsDone = API_SOURCES.filter((s) => ['online', 'offline'].includes(apiStatusState.statuses[s.id]));
  const lyricsOk = lyricsDone.filter((s) => apiStatusState.statuses[s.id] === 'online').length;
  const extDone = extensionStatusRows.filter((row) => row.ok === true || row.ok === false);
  const extOk = extDone.filter((row) => row.ok === true).length;
  updateOverallStatus(lyricsOk + extOk, lyricsDone.length + extDone.length);
}

function updateOverallStatus(okCount, totalCount) {
  const el = $('status-overall');
  if (!el) return;
  const online = okCount > 0;
  el.className = `status-overall ${online ? 'online' : 'offline'}`;
  el.querySelector('.status-overall-icon').textContent = online ? '✓' : '✗';
  el.querySelector('.status-overall-text').textContent = totalCount > 0 ? `${okCount}/${totalCount} providers OK` : 'No checks yet';
}

/**
 * Checks the status of all configured providers and extensions.
 * Updates the health-check interface while the check is running and marks all sources offline if the check fails or no backend is available.
 */
function checkAll() {
  setFetchingState('start', 'checking provider status...');
  const sources = API_SOURCES.map((source) => source.id);
  sources.forEach((sourceId) => {
    apiStatusState.checkingSources[sourceId] = true;
    apiStatusState.statuses[sourceId] = 'checking';
  });
  renderStatusGrids();
  // The extensions' own checks run alongside; they answer directly rather
  // than through the pushed updateHealthResults().
  checkExtensions().then(() => updateOverallStatusFromCards());
  if (window.pywebview?.api?.run_health_check) {
    window.pywebview.api.run_health_check(sources).catch(() => {
      setFetchingState('hide');
      sources.forEach((sourceId) => {
        apiStatusState.statuses[sourceId] = 'offline';
        apiStatusState.checkingSources[sourceId] = false;
      });
      renderStatusGrids();
      updateOverallStatus(0, sources.length);
    });
  } else {
    setTimeout(() => {
      sources.forEach((sourceId) => {
        apiStatusState.statuses[sourceId] = 'offline';
        apiStatusState.checkingSources[sourceId] = false;
      });
      renderStatusGrids();
      updateOverallStatus(0, sources.length);
    }, 800);
  }
}

function withTimeout(promise, ms, message) {
  return Promise.race([
    promise,
    new Promise((_, reject) => window.setTimeout(() => reject(new Error(message)), ms)),
  ]);
}

function checkOne(sourceId) {
  apiStatusState.checkingSources[sourceId] = true;
  apiStatusState.statuses[sourceId] = 'checking';
  renderStatusGrids();
  if (window.pywebview?.api?.run_health_check) {
    window.pywebview.api.run_health_check([sourceId]).catch(() => {
      apiStatusState.statuses[sourceId] = 'offline';
      renderStatusGrids();
    }).finally(() => {
      apiStatusState.checkingSources[sourceId] = false;
      renderStatusGrids();
    });
  } else {
    setTimeout(() => {
      apiStatusState.statuses[sourceId] = 'offline';
      apiStatusState.checkingSources[sourceId] = false;
      renderStatusGrids();
    }, 800);
  }
}

/**
 * Updates health-check statuses for lyrics providers.
 * @param {Array<Object>} data - Health-check results containing provider identifiers and success states.
 */
function updateStatusesFromResults(data) {
  const statusMap = {};
  data.forEach((result) => {
    if (!result.provider) return;
    const current = statusMap[result.provider];
    if (result.ok) {
      statusMap[result.provider] = 'online';
    } else if (!current) {
      statusMap[result.provider] = 'offline';
    }
  });
  for (const source of API_SOURCES) {
    if (statusMap[source.id]) {
      apiStatusState.statuses[source.id] = statusMap[source.id];
    } else if (apiStatusState.statuses[source.id] === 'checking') {
      // Asked but not answered: the check has no server for it. Not
      // offline — there is nothing to reach.
      apiStatusState.statuses[source.id] = 'none';
    }
    apiStatusState.checkingSources[source.id] = false;
  }
  renderStatusGrids();
}

window.updateHealthResults = (results) => {
  setFetchingState('hide');
  const data = typeof results === 'string' ? JSON.parse(results) : results;
  updateStatusesFromResults(data);
  updateOverallStatusFromCards();
};

renderStatusGrids();

// ── State ────────────────────────────────────────────────────────────────────
let currentTracks  = [];
let trackRenderToken = 0;
let currentUrl     = '';
let currentItemType = 'ALBUM'; // ALBUM, TRACK, ARTIST, PLAYLIST
let queue          = [];
let queueStats     = { downloaded:'0.00 MB', speed:'0.0 MB/s', eta:'' };
let isDownloading  = false;
let queueStartTime = null;
let queueDurationInterval = null;
//: Queue view filters. A hundred-track playlist makes the queue a list you
//: have to search rather than read — most often to find the handful that
//: failed. null status = show everything.
// One Set of queue-item ids per dispatched batch, oldest first. The backend
// runs batches one at a time (see _await_download_slot), so the batch that
// reports finished is the one at the front.
let dispatchedBatches = [];
let queueFilterStatus = null;   // null | 'waiting' | 'done' | 'skipped' | 'error'
let queueSearch = '';
let previewAudio = null;
let previewPlayingIndex = -1;
//: 0-100. A 30-second clip at whatever the system volume happens to be is
//: the one sound this app makes, and it was always full blast.
let previewVolume = 100;
// Destroy current audio to release OS media keys
function stopCurrentPreview() {
  if (previewAudio) {
    previewAudio.pause();
    previewAudio.removeAttribute('src'); // Removes the source
    previewAudio.load(); // Forces the browser to release the media session
  }
  if (previewPlayingIndex >= 0) {
    const prevBtns = document.querySelectorAll(`button.ta-preview[data-preview-index="${previewPlayingIndex}"]`);
    prevBtns.forEach(btn => setPreviewButtonState(btn, false));
    previewPlayingIndex = -1;
  }
}

// ── Pagination state ──────────────────────────────────────────────────────────
let currentPage = 1;
const TRACKS_PER_PAGE = 50;

// ── Logging & Python bridge ──────────────────────────────────────────────────
function logMessage(msg, type = '') {
  // A '-quiet' suffix means "colour this line like its base type, but never
  // toast it". It exists for lists the user genuinely wants itemised in the
  // panel — the unmatched rows of a CSV import, say — where itemising the
  // notifications instead would mean hundreds of popups. The panel keeps
  // every line, in its usual colour; a single summary toast is raised by the
  // caller alongside the list.
  const quiet = typeof type === 'string' && type.endsWith('-quiet');
  const base = quiet ? type.slice(0, -'-quiet'.length) : type;

  // Write to the log UI panel
  const area = $('logArea');
  if (area) {
    const line = document.createElement('div');
    line.className = 'log-line';
    line.innerHTML = `<span class="log-ts">${ts()}</span><span class="log-msg ${base}">${escHtml(msg)}</span>`;
    area.appendChild(line);
    area.scrollTop = area.scrollHeight;
  }

  // Also generate a visual Toast based on the event type.
  // 'debug' (and an absent type) stay in the panel above and never toast —
  // that is where startup diagnostics go, so a launch no longer greets the
  // user with a stack of notifications they did not ask for.
  if (quiet || base === 'debug' || !base) return;

  // A toast is a headline, not a transcript. A provider that fails logs its
  // whole Python traceback at error level, and passing that through put a
  // wall of stack frames over half the window — unreadable, and it buried
  // the one line that said what went wrong. The panel above still has all
  // of it, which is what the panel is for.
  const headline = toastHeadline(msg);

  if (base === 'ok') toastMgr.success(headline);
  else if (base === 'error') toastMgr.error(headline);
  else if (base === 'warn') toastMgr.warning(headline);
  else if (base === 'info') toastMgr.info(headline, { duration: 2500 });
}

const TOAST_MAX_CHARS = 160;

function toastHeadline(msg) {
  const text = String(msg ?? '');
  const lines = text.split('\n').map(l => l.trim()).filter(Boolean);
  if (!lines.length) return text;

  // A traceback's useful line is its last one ("SomeError: what happened"),
  // not its first ("Traceback (most recent call last):").
  const isTraceback = /^Traceback \(most recent call last\)/.test(lines[0]);
  let headline = isTraceback ? lines[lines.length - 1] : lines[0];

  if (headline.length > TOAST_MAX_CHARS) {
    headline = headline.slice(0, TOAST_MAX_CHARS - 1).trimEnd() + '…';
  } else if (lines.length === 1) {
    return headline;
  }
  return `${headline} (see Logs for the rest)`;
}

function clearLog() { $('logArea').innerHTML = ''; }

window.app_log = (msg, type = '') => logMessage(msg, type);
// The backend pushes a bare string, so whether the job is still running has
// to be read off the line itself: it ends in "…" while something is in
// flight ("Reading the file…"), or carries a counter ("Matching 812/1875 ·
// 806 found"), and finishes on a full stop ("Ready for download.", "Error.").
// Getting it wrong only spins or stops a small dial, which is why a
// heuristic is worth more here than a second event.
window.app_set_progress = (label) => setStatus(label || '', /…|\d+\/\d+/.test(label || ''));
window.app_set_metadata = (data) => {
  try {
    const d = typeof data === 'string' ? JSON.parse(data) : data;
    setAlbumCard(
      d.title,
      d.artist,
      d.cover,
      d.quality,
      d.description,
      d.followers,
      d.owner,
      d.owner_avatar,
      d.source,
      d.artist_listeners,
      d.artist_rank,
      d.artist_verified,
      d.artist_biography,
      d.release_date,
      d.track_count,
      d.artist_url,
      d.artists_data,
      d.artist_avatar
    );
    setArtistExpression(d);
    g_artistConcerts = Array.isArray(d.artist_concerts) ? d.artist_concerts : [];
    renderConcertsButton(d.artist);
  } catch(e) {}
};
window.updateFolderLabel = (path) => {
  $('folder-path').textContent = path; $('folder-path').title = path;
};
// Sent by choose_folder() when the native dialog is switched off
// (MORNYE_FOLDER_DIALOG=builtin): the download folder is picked in the page.
window.openDownloadFolderBrowser = () => openFolderBrowser('download');
    let currentDownloadToastId = null;
    let downloadToastMuted = false;
    // Hidden quietly: no "closed by the user" mute when the app removes it.
    window.dismissDownloadToast = () => {
      if (currentDownloadToastId) toastMgr.dismiss(currentDownloadToastId);
      currentDownloadToastId = null;
    };

    window.app_update_download_stats = (payload) => {
      try {
        const data = typeof payload === 'string' ? JSON.parse(payload) : payload;
        if (!data) return;
        
        queueStats.downloaded = `${Number(data.total_downloaded || 0).toFixed(2)} MB`;
        // One decimal, as Mornye shows it; the ETA is the backend's
        // (core/progress.py estimate_eta_seconds): remaining bytes over the
        // current speed, only when the size is known and under an hour.
        queueStats.speed = `${Number(data.current_speed || 0).toFixed(1)} MB/s`;
        queueStats.eta = formatEta(data.eta_seconds);

        // The Queue page already shows all of this (live card + bottom bar),
        // so the toast would only cover the list there. Elsewhere it stays,
        // but the user can close it and it stays closed for this run.
        const onQueuePage = $('view-queue')?.classList.contains('active');
        if (currentDownloadToastId && !document.getElementById(currentDownloadToastId)) {
          currentDownloadToastId = null;
          downloadToastMuted = true;
        }
        if (onQueuePage && currentDownloadToastId) dismissDownloadToast();
        if (isDownloading && !onQueuePage && !downloadToastMuted) {
          const activeItem = queue.find(q => q.status === 'active');
          if (activeItem) {
            // Plain text, not an HTML fragment: toastMgr's rendering path
            // escapes the message (see toast-system.js escapeHtml), so
            // markup passed here would show up as literal tags instead of
            // being rendered.
            const msg = `${activeItem.title} — ${activeItem.progress}% · ${queueStats.speed}`
              + (queueStats.eta ? ` · ${queueStats.eta} left` : '');

            if (!currentDownloadToastId) {
              currentDownloadToastId = toastMgr.loading(msg, {
                title: 'Downloading Tracks...', dismissible: true, className: 'toast-compact',
              });
            } else {
              // Update il testo del toast esistente
              const toastEl = document.getElementById(currentDownloadToastId);
              const toastMsgEl = toastEl && toastEl.querySelector('.toast-message');
              if (toastMsgEl) toastMsgEl.textContent = msg;
            }
          }
        }
        if (Array.isArray(data.queue) && data.queue.length) {
          // The queue is indexed once per push instead of being scanned once
          // per stat entry: those scans were quadratic in the queue length,
          // and Python pushes this payload four times a second for the whole
          // queue. First match wins, as findIndex() did here before.
          const byId = new Map(), bySpotifyId = new Map(), byName = new Map();
          queue.forEach(q => {
            if (q.id && !byId.has(q.id)) byId.set(q.id, q);
            if (q.spotify_id && !bySpotifyId.has(q.spotify_id)) bySpotifyId.set(q.spotify_id, q);
            const nameKey = `${q.title}\u0000${q.artist}`;
            if (!byName.has(nameKey)) byName.set(nameKey, q);
          });
          data.queue.forEach(stat => {
            const item = (stat.id ? byId.get(stat.id) : null)
              || (stat.spotify_id ? bySpotifyId.get(stat.spotify_id) : null)
              || (stat.track_name ? byName.get(`${stat.track_name}\u0000${stat.artist_name}`) : null);
            if (!item) return;
            if (stat.status === 'downloading') item.status = 'active';
            else if (stat.status === 'skipped') item.status = 'skipped';
            else if (stat.status === 'completed') item.status = 'done';
            else if (stat.status === 'failed') item.status = 'error';
            if (stat.total_size > 0) {
              item.progress = Math.min(100, Math.round((stat.progress / stat.total_size) * 100));
            } else if (stat.status === 'completed') {
              item.progress = 100;
            }
            if (stat.file_path) item.file_path = stat.file_path;
            if (stat.total_size > 0) item.file_size_mb = (stat.total_size / (1024 * 1024));
          });
        }
        renderQueue();
      } catch (e) {
        console.warn('Failed to parse download stats', e);
      }
    };

window.showTracklist = (tracksJson) => {
  setFetchingState('success');
  const tracks = typeof tracksJson === 'string' ? JSON.parse(tracksJson) : tracksJson;
  renderTracks(tracks, 1);
  $('fetchBtn').disabled = false;
  $('text-search-container')?.classList.add('hidden');
};

window.app_download_finished = (success = true, indices = null) => {
  // Close out the items of *this* batch only. Marking every active row done
  // was wrong as soon as a second batch was queued behind the first (the
  // backend runs them one at a time now, see _await_download_slot): the
  // waiting batch's tracks were reported finished before they had started.
  // Matched on the ids startDownloadQueue dispatched, not on `indices`:
  // those are tracklist positions, and two fetches put two different tracks
  // at index 0, so a queue holding both closed out the wrong rows. `indices`
  // is still accepted for older builds, and a build that pushes neither
  // falls back to the previous all-active behaviour.
  const dispatched = dispatchedBatches.shift() || null;
  const byIndex = !dispatched && Array.isArray(indices) ? new Set(indices) : null;
  const inBatch = (item) => {
    if (dispatched) return dispatched.has(item.id);
    if (byIndex) return byIndex.has(item.index);
    return true;
  };
  const activeItems = queue
    .map((item, qi) => (item.status === 'active' && inBatch(item) ? qi : -1))
    .filter(i => i >= 0);
  
  // Close out items from the completed batch
  if (activeItems.length > 0) {
    activeItems.forEach(qi => updateQueueItem(qi, success ? 'done' : 'error', success ? 100 : 0));
  }

  dismissDownloadToast();
  
  // Check if there are still any downloads running concurrently
  const stillActive = queue.some(q => q.status === 'active');
  if (!stillActive) {
    isDownloading = false;
    downloadToastMuted = false;
    resetQueueDuration();
    setStatus(success ? 'Download complete! ✓' : 'Error during download.');
    logMessage(success ? 'All downloads finished.' : 'Download failed.', success ? 'ok' : 'error');
  }
  
  // Safety fallback: trigger any newly added tracks that got stuck
  const waiting = queue.filter(q => q.status === 'waiting');
  if (waiting.length > 0) {
    startDownloadQueue();
  }
  loadFailedTracks();
};
// A download this page did not dispatch — a batch the backend resumed after a
// restart, or a retry from the missing-tracks list — ends here instead of in
// app_download_finished, which closes the oldest batch *this page* sent and
// would close the wrong rows.
window.app_background_download_finished = (success = true, count = 0) => {
  loadFailedTracks();
  logMessage(
    success
      ? `Background download finished (${count} track(s)).`
      : 'Background download failed.',
    success ? 'ok' : 'error',
  );
};
window.loadHistoryAndProfiles = async () => {
  if (!window.pywebview?.api) return;
  loadFailedTracks();
  try {
    const hist     = await window.pywebview.api.get_history();
    renderRecent(hist);
    const profiles = await window.pywebview.api.get_profiles();
    const sel      = $('profile-select');
    sel.innerHTML  = '<option value="">Select…</option>';
    profiles.forEach(p => {
      const o = document.createElement('option'); o.value = p; o.textContent = p;
      sel.appendChild(o);
    });
    try {
      const v = await window.pywebview.api.get_version();
      const tb = document.getElementById('tb-version');
      const hero = document.getElementById('hero-version');
      if (tb) tb.innerText = v;
      if (hero) hero.innerText = v && v !== 'unknown' ? `v${v}` : 'v...';
    } catch(e) { /* ignore */ }
  } catch(e) { logMessage('Could not load history/profiles: ' + e, 'warn'); }
};

// ── Status bar ────────────────────────────────────────────────────────────────
function setStatus(msg, loading = false) {
  const statusText = $('status-text');
  if (statusText) statusText.textContent = msg || '';
  // The strip only exists while it has something to say; an empty message is
  // how every caller clears it.
  const bar = $('status-bar');
  if (bar) bar.classList.toggle('hidden', !msg);
  const spinner = $('spinner');
  if (spinner) spinner.style.display = loading && msg ? 'block' : 'none';
}
function setTrackRenderStatus(msg, visible = false) {
  const el = $('track-render-status');
  if (!el) return;
  el.textContent = msg;
  el.classList.toggle('hidden', !visible);
}

function setPlaycountHeaderLabel(label) {
  const header = document.querySelector('.track-table-header');
  if (!header) return;
  // header children: [empty, #, Title, Playcount, Duration, Actions]
  if (header.children && header.children[3]) header.children[3].textContent = label;
}

// ── Album card ────────────────────────────────────────────────────────────────
let g_albumReleaseDate = '';
let g_albumTrackCount = 0;

// Samples the cover's average colour into --album-glow on #album-card (see
// css/views/home.css #album-card::before) for a soft artwork-derived tint instead of
// a flat surface. Best-effort: a cover served without CORS headers taints
// the canvas and getImageData() throws — caught silently, the card just
// keeps its plain background. Never touches the <img> itself.
function applyAlbumGlow(imgEl) {
  const cardEl = $('album-card');
  if (!cardEl) return;
  try {
    const SIZE = 24; // downsample hard — this is an average, not a picture
    const canvas = document.createElement('canvas');
    canvas.width = SIZE; canvas.height = SIZE;
    const ctx = canvas.getContext('2d');
    ctx.drawImage(imgEl, 0, 0, SIZE, SIZE);
    const { data } = ctx.getImageData(0, 0, SIZE, SIZE);
    let r = 0, g = 0, b = 0, n = 0;
    for (let i = 0; i < data.length; i += 4) {
      if (data[i + 3] < 16) continue; // skip near-transparent pixels
      r += data[i]; g += data[i + 1]; b += data[i + 2]; n++;
    }
    if (!n) { cardEl.style.removeProperty('--album-glow'); return; }
    r = Math.round(r / n); g = Math.round(g / n); b = Math.round(b / n);
    cardEl.style.setProperty('--album-glow', `rgba(${r}, ${g}, ${b}, .5)`);
  } catch (err) {
    // Cross-origin cover, unsupported canvas, whatever — no glow this time.
    cardEl.style.removeProperty('--album-glow');
  }
}

// Reads the clipboard into the fetch bar and runs the fetch — the paste and
// the press, which are always done together for a link that was just copied
// out of Spotify.
async function pasteAndFetch() {
  let text = '';
  try {
    text = (await navigator.clipboard.readText() || '').trim();
  } catch (e) {
    // Denied or unavailable: say so rather than appearing to do nothing.
    toastMgr.warning('Could not read the clipboard. Paste with ' +
      (navigator.platform.startsWith('Mac') ? '⌘V' : 'Ctrl+V') + ' instead.');
    return;
  }
  if (!text) { toastMgr.info('The clipboard is empty.'); return; }
  const input = $('urlInput');
  input.value = text;
  input.dispatchEvent(new Event('input'));
  // In search mode a pasted string is a query, and onFetch() handles both —
  // it reads the mode itself, so nothing here needs to know which we are in.
  onFetch();
}

// The button is only worth showing where the clipboard can actually be read:
// pywebview's web view and a plain browser tab differ here, and a button that
// always fails is worse than no button. Checked once at boot via the
// Permissions API where it exists; where it doesn't, the button stays and the
// catch above covers the refusal.
async function initPasteButton() {
  const btn = $('pasteBtn');
  if (!btn) return;
  if (!navigator.clipboard || !navigator.clipboard.readText) return; // stays hidden
  try {
    const status = await navigator.permissions?.query?.({ name: 'clipboard-read' });
    if (status && status.state === 'denied') return; // stays hidden
  } catch (e) { /* Permissions API missing or does not know this name — show it */ }
  btn.classList.remove('hidden');
}

// Navigates to an artist's own page the same way clicking a recent-fetch
// card does: drop the URL in the fetch bar and run the normal fetch flow,
// rather than a one-off code path that would skip whatever that flow does
// (recent-card highlight, search-mode reset, etc.) and drift from it later.
function goToUrl(url) {
  const safeUrl = httpUrlOrNull(url);
  if (!safeUrl) return;
  $('urlInput').value = safeUrl;
  if ($('searchMode').value === 'search') toggleSearchMode();
  onFetch();
}

// Kept as its own name because that is what the call sites mean, and because
// an artist link is the one that has to survive being clicked from inside a
// row (see the delegated listener below).
function goToArtist(url) { goToUrl(url); }

// One delegated listener rather than an onclick="" per artist name: the URL
// only ever goes into a data- attribute (escHtml covers both quote chars),
// never into a JS string built by interpolation — the cover-URL comment
// above setAlbumCard's <img> explains why that path is avoided here too.
document.addEventListener('click', (e) => {
  const el = e.target.closest('.artist-link');
  if (!el) return;
  e.stopPropagation();
  goToArtist(el.dataset.artistUrl);
});

// Wraps an artist name as a clickable span when a URL is known, or leaves
// it as plain text otherwise — used everywhere an artist name is set, so a
// track from a provider that never returned an artist_url just shows a
// name, same as before this existed.
function artistNameHtml(name, url) {
  const safeName = escHtml(name || '');
  const safeUrl = httpUrlOrNull(url);
  if (!safeUrl) return safeName;
  return `<span class="artist-link" data-artist-url="${escHtml(safeUrl)}">${safeName}</span>`;
}

// A credit line where *each* artist is its own link. artistsData is the
// backend's per-artist [{id,name,url}] list (see _artist_nodes in
// core/spotify_metadata.py); when it's missing — another provider, an
// older payload — this falls back to the joined string with one link on
// the whole thing, which is what it did before.
//
// The joined string is never split back apart to do this: "Tyler, The
// Creator" is one artist whose name contains the separator, and splitting
// is exactly what turns that into two wrong links.
// A link to one song: Spotify's /track/, a YouTube video, and Apple Music's
// /song/ page or an album link pointing at one of its tracks (?i=).
function isSongUrl(url) {
  const u = String(url || '').toLowerCase();
  return u.includes('/track/') || u.includes('spotify:track:') || u.includes('watch?v=') || u.includes('youtu.be/')
    || /music\.apple\.com\/.*\/song\//.test(u) || /music\.apple\.com\/.*\/album\/.*[?&]i=\d+/.test(u);
}

function artistsCreditHtml(artistsData, joinedNames, fallbackUrl) {
  const list = Array.isArray(artistsData) ? artistsData.filter(a => a && a.name) : [];
  if (!list.length) return artistNameHtml(joinedNames, fallbackUrl);
  return list.map(a => artistNameHtml(a.name, a.url)).join(', ');
}

let g_albumArtistUrl = '';
let g_albumArtistsData = [];

// "See more…" toggle for long artist bios. The bio is line-clamped in CSS; the
// button only shows while the text is actually truncated (or already expanded),
// and is re-evaluated on resize since the card may be hidden when this runs.
let _bioObserver = null;
function setupBioToggle(subtitle, enabled) {
  let btn = document.getElementById('artist-bio-toggle');
  if (_bioObserver) { _bioObserver.disconnect(); _bioObserver = null; }
  if (!enabled) { btn?.remove(); return; }
  if (!btn) {
    btn = document.createElement('button');
    btn.id = 'artist-bio-toggle';
    btn.type = 'button';
    btn.className = 'artist-bio-toggle';
    subtitle.insertAdjacentElement('afterend', btn);
  }
  btn.onclick = () => {
    const expanded = subtitle.classList.toggle('expanded');
    btn.textContent = expanded ? 'See less' : 'See more…';
    btn.setAttribute('aria-expanded', String(expanded));
  };
  btn.textContent = 'See more…';
  btn.setAttribute('aria-expanded', 'false');
  btn.hidden = true;
  const update = () => {
    if (!subtitle.clientHeight) return; // card not laid out yet
    const expanded = subtitle.classList.contains('expanded');
    btn.hidden = !(expanded || subtitle.scrollHeight > subtitle.clientHeight + 1);
  };
  if (typeof ResizeObserver !== 'undefined') {
    _bioObserver = new ResizeObserver(update);
    _bioObserver.observe(subtitle);
  }
  requestAnimationFrame(update);
}

// Providers join the © and ℗ lines with " · "; when both say the same thing
// show it once (mirrors collapse_copyright in core/models.py).
function collapseCopyright(value) {
  const mark = /^\s*(?:[©℗]|\([cp]\))\s*/i;
  const parts = String(value || '').split(' · ').map(p => p.trim()).filter(Boolean);
  if (parts.length < 2) return String(value || '').trim();
  const seen = new Map();
  const marks = new Set();
  for (const part of parts) {
    const m = part.match(mark);
    if (m) marks.add(m[0].trim().toUpperCase());
    const key = part.replace(mark, '').toLowerCase().split(/\s+/).join(' ');
    if (!seen.has(key)) seen.set(key, part);
  }
  if (seen.size === 1 && marks.has('©') && marks.has('℗')) {
    return '©℗ ' + [...seen.values()][0].replace(mark, '');
  }
  return [...seen.values()].join(' · ');
}

// The owner row is a fixed run of fields with a "·" between each; any of
// them can be empty (no follower count, no source…). Empty fields are
// hidden and a separator is shown only between two visible fields, so the
// row never reads "Spotify · · 90 tracks ·".
function tidyOwnerRow() {
  const row = $('album-owner-row');
  if (!row) return;
  let pendingSep = null;
  let seenField = false;
  for (const el of row.children) {
    if (el.id === 'album-owner-avatar') continue;
    if (el.classList.contains('dot-sep')) {
      el.style.display = 'none';
      if (seenField) pendingSep = el;
      continue;
    }
    const filled = !!el.textContent.trim();
    el.style.display = filled ? '' : 'none';
    if (!filled) continue;
    if (pendingSep) pendingSep.style.display = '';
    pendingSep = null;
    seenField = true;
  }
}

//: The collection on screen, for "Import playlist" (importFetchedPlaylist).
let g_albumImport = { title: '', description: '', cover: '' };

function setAlbumCard(title, artist, coverUrl, quality, description, followers, owner, ownerAvatar, source, artistListeners, artistRank, artistVerified, artistBiography, releaseDate, trackCount, artistUrl, artistsData, artistAvatar) {
  g_albumImport = { title: title || '', description: description || '', cover: coverUrl || '' };
  g_albumReleaseDate = releaseDate || '';
  g_albumTrackCount = trackCount || 0;
  g_albumArtistUrl = artistUrl || '';
  g_albumArtistsData = Array.isArray(artistsData) ? artistsData : [];
  
  const metaSection = $('track-meta-section');
  if (metaSection) {
    metaSection.innerHTML = '';
    metaSection.style.display = 'none';
  }
  // Cleared now, repopulated once the tracks are in (updateAlbumMeta, or
  // showSingleTrackCard) — otherwise the previous fetch's sheet lingers
  // while the new one loads.
  renderAlbumTech([]);
  $('album-cover').querySelector('.cover-duration')?.remove();
  $('album-subtitle').style.display = '';
  // showSingleTrackCard()'s quality/duration chip row is cleaned up from
  // renderTracks()'s own single-track-vs-not branch, not from here — see
  // the comment there for why (setAlbumCard and renderTracks run off two
  // separate, not-strictly-ordered backend callbacks).

  $('album-actions').innerHTML = `
    <button class="act-btn primary" data-onclick="downloadAll()" data-tip="Download all" aria-label="Download all">
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
      Download All
    </button>
    <button class="act-btn secondary" data-onclick="downloadSelected()" id="dl-selected-btn" style="display:none;" data-tip="Download selected" aria-label="Download selected">
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><polyline points="9 12 11 14 15 10"/></svg>
      Download Selected
    </button>
    <button class="act-btn secondary" id="save-all-covers-btn" data-tip="Save all covers as .jpg" data-onclick="downloadAllCovers(this)">
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>
    </button>
    <button class="act-btn secondary" id="save-all-lyrics-btn" data-tip="Save all lyrics as .lrc" data-onclick="downloadAllLyrics(this)">
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>
    </button>
    ${CLI_BUTTON_HTML}
  `;
  renderImportPlaylistButton();
  $('album-title').innerHTML = escHtml(title || '—') + (artistVerified
    ? ` <span class="artist-verified-badge" title="Verified Artist"><svg width="20" height="20" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="12" fill="#1d9bf0"/><path d="M8 12.5l2.5 2.5 5.5-5.5" stroke="#fff" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg></span>`
    : '');
  const subtitle = $('album-subtitle');
  
  // For artists, show rank or listeners; for playlists, show quality
  // An artist link is an artist page even when its provider sends none of
  // Spotify's extras (rank, listeners, verified, biography): Apple Music's
  // artists came without them and lost the photo banner and the name.
  const isArtistUrl = /\/artist\/|\/browse\/artist|spotify:artist:/i.test(currentUrl || '');
  const isArtistCard = !!(artistRank || artistListeners || artistVerified || artistBiography || isArtistUrl);
  setArtistHero(isArtistCard, coverUrl);
  if (isArtistCard) {
    // On the banner, Spotify-style: "Verified Artist" over the name, the
    // monthly listeners under it.
    const listenerCount = Number(artistListeners) || 0;
    $('album-title').innerHTML = `
      ${artistVerified ? `<span class="hero-verified"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden="true"><circle cx="12" cy="12" r="12" fill="#3d91f4"/><path d="M8 12.5l2.5 2.5 5.5-5.5" stroke="#fff" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>Verified Artist</span>` : ''}
      <span class="hero-name">${escHtml(title || '—')}</span>
      ${listenerCount > 0 ? `<span class="hero-listeners">${listenerCount.toLocaleString('en-US')} monthly listeners</span>` : ''}`;
  }
  g_artistAbout = isArtistCard ? {
    name: title || '',
    avatar: artistAvatar || coverUrl || '',
    rank: artistRank,
    listeners: artistListeners,
    followers,
    bio: artistBiography || description || '',
  } : null;
  if (!isArtistCard) setArtistOverviewMode(false);
  $('album-card').classList.toggle('playlist-mode', !isArtistCard && /\/playlist\/|spotify:playlist:/i.test(currentUrl || ''));
  renderArtistAbout();

  if (isArtistCard) {
    const bio = artistBiography || description || '';
    // textContent, not innerHTML: the bio comes from whichever metadata
    // provider (or third-party extension) answered, and the server-side
    // `re.sub(r"<[^>]+>", "", ...)` in spotify_metadata.py is a tag stripper,
    // not a sanitiser — it doesn't cover every provider and doesn't survive
    // an unterminated tag. Nothing renders differently here: .artist-bio is
    // a -webkit-box line clamp, so markup never contributed anything anyway.
    subtitle.textContent = bio;
    subtitle.className = bio ? 'artist-bio' : '';
    subtitle.style.display = bio ? '' : 'none';
  } else {
    subtitle.textContent = description || '';
    subtitle.className = '';
    subtitle.style.display = description ? '' : 'none';
  }
  setupBioToggle(subtitle, isArtistCard);

  const ownerEl = $('album-owner');
  const followersEl = $('album-followers');
  const sourceEl = $('album-source');
  const metaDetails = $('album-meta-details');
  const avatarEl = $('album-owner-avatar');
  
  // Create a safe container for artist stats without breaking the original HTML
  let artistStatsRow = $('artist-stats-row');
  if (!artistStatsRow) {
    artistStatsRow = document.createElement('div');
    artistStatsRow.id = 'artist-stats-row';
    artistStatsRow.style.cssText = 'display:flex;align-items:center;gap:8px;';
    metaDetails.insertBefore(artistStatsRow, metaDetails.firstChild);
  }
  
  const ownerRow = $('album-owner-row');

  if (isArtistCard) {
    const parts = [];
    if (artistRank)      parts.push(`#${artistRank} rank`);
    if (followers)       parts.push(`${Number(followers).toLocaleString('en-US')} followers`);
    if (artistListeners) parts.push(`${Number(artistListeners).toLocaleString('en-US')} listeners`);
    
    artistStatsRow.innerHTML = parts.map(p => `<span>${escHtml(p)}</span>`).join('<span class="dot-sep"> · </span>');
    artistStatsRow.style.display = 'flex';
    if (ownerRow) ownerRow.style.display = 'none'; // Hide the original row while keeping it intact
    
    metaDetails.classList.remove('hidden');
    if (avatarEl) avatarEl.classList.add('hidden');
  } else {
    artistStatsRow.style.display = 'none';
    if (ownerRow) ownerRow.style.display = 'flex';
    
    if (ownerEl) ownerEl.textContent = owner || '';
    // Number('') and Number(null) are both 0, not NaN — so an album with no
    // follower count was showing "0 followers", and that non-empty string
    // then kept the whole meta-details row visible.
    const followerCount = (followers === 0 || followers) ? Number(followers) : NaN;
    if (followersEl) followersEl.textContent = Number.isFinite(followerCount) && followerCount > 0 ? `${followerCount.toLocaleString()} followers` : '';
    // "Spotify · 90 tracks · … · Spotify": a playlist Spotify curates has
    // Spotify as both owner and source, and the second says nothing new.
    const sameAsOwner = owner && source && owner.trim().toLowerCase() === source.trim().toLowerCase();
    if (sourceEl) sourceEl.textContent = sameAsOwner ? '' : (source || '');
  }
  tidyOwnerRow();

  if (!isArtistCard) {
    const hasMetaDetails = !!(
      (ownerEl && ownerEl.textContent) ||
      (followersEl && followersEl.textContent) ||
      (sourceEl && sourceEl.textContent) ||
      ownerAvatar
    );
    metaDetails.classList.toggle('hidden', !hasMetaDetails);
  }

  // If owner present, prefer showing owner as the album artist (playlist behavior)
  //
  // This is the single place #album-artist is written. It used to be the
  // second: an earlier line set the linked markup and this one overwrote it
  // with plain textContent a few statements later, so the artist name in
  // the single-track card — the one view where this span is what's actually
  // on screen — was never clickable however well the backend resolved it.
  //
  // innerHTML: one clickable span per credited artist when the backend sent
  // the per-artist list, a single link when it only sent one URL, plain
  // escaped text when it sent neither (an artist's own page sends neither —
  // no point linking a page to itself).
  const artistEl = $('album-artist');
  if (owner) {
    artistEl.textContent = "";
  } else {
    artistEl.innerHTML = artistsCreditHtml(artistsData, artist, artistUrl);
  }

  if (ownerAvatar) {
    avatarEl.style.backgroundImage = `url('${encodeURI(ownerAvatar)}')`;
    avatarEl.textContent = '';
    avatarEl.classList.remove('hidden');
  } else if (owner) {
    avatarEl.style.backgroundImage = '';
    avatarEl.textContent = owner.trim().charAt(0).toUpperCase();
    avatarEl.classList.remove('hidden');
  } else {
    avatarEl.style.backgroundImage = '';
    avatarEl.textContent = '';
    avatarEl.classList.add('hidden');
  }

  const descriptionEl = $('album-description');
  if (!isArtistCard && description) {
    descriptionEl.textContent = description;
    descriptionEl.classList.add('visible');
  } else {
    descriptionEl.textContent = '';
    descriptionEl.classList.remove('visible');
  }

  const coverEl = $('album-cover');
  const safeCover = httpUrlOrNull(coverUrl);
  if (safeCover) {
    const displayArtist = artist || title || 'Unknown';
    // Built with DOM calls rather than a template string: coverUrl is remote
    // metadata, and the old markup dropped it unescaped into both a src=""
    // attribute and a JS string literal inside onclick="" — one apostrophe in
    // a cover URL was enough to break out and run as script.
    coverEl.textContent = '';
    $('album-card')?.style.removeProperty('--album-glow'); // don't carry the previous cover's tint while this one loads

    const img = document.createElement('img');
    img.alt = 'cover';
    img.className = 'cover-loading'; // blur-up: css/views/home.css clears it once decode() resolves
    // Asked for with CORS so applyAlbumGlow can read its colour (Spotify's
    // image host allows it); a host that refuses gets one retry without,
    // which shows the cover and simply leaves the card untinted.
    img.crossOrigin = 'anonymous';
    img.src = safeCover;
    img.addEventListener('error', () => {
      if (img.crossOrigin) {
        img.removeAttribute('crossorigin');
        img.src = safeCover;
        return;
      }
      coverEl.textContent = '🎵';
      $('album-card')?.style.removeProperty('--album-glow');
    });
    (img.decode ? img.decode().catch(() => {}) : Promise.resolve()).then(() => {
      img.classList.remove('cover-loading');
      applyAlbumGlow(img);
    });
    coverEl.appendChild(img);

    const btn = document.createElement('button');
    btn.id = 'cover-download-btn';
    btn.className = 'cover-download-btn';
    btn.title = 'Download cover';
    btn.style.cssText = 'left: 50%; top: 50%; transform: translate(-50%, -50%);';
    // Static markup, no interpolation.
    btn.innerHTML = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>';
    btn.addEventListener('click', () => {
      downloadAlbumCover(btn, safeCover, title || 'album', displayArtist, owner || '');
    });
    coverEl.appendChild(btn);
  } else {
    coverEl.textContent = '🎵';
    $('album-card')?.style.removeProperty('--album-glow');
  }
  $('album-card').classList.remove('hidden');
  $('text-search-container')?.classList.add('hidden');
}
// An artist page opens on a hero: the artist's picture, blurred, behind the
// name and the round download controls (css/views/home.css, .artist-mode).
function setArtistHero(on, coverUrl) {
  const card = $('album-card');
  if (!card) return;
  card.classList.toggle('artist-mode', !!on);
  let bg = card.querySelector('.artist-hero-bg');
  const safe = httpUrlOrNull(coverUrl);
  if (!on || !safe) { bg?.remove(); return; }
  if (!bg) {
    bg = document.createElement('div');
    bg.className = 'artist-hero-bg';
    bg.setAttribute('aria-hidden', 'true');
    bg.appendChild(document.createElement('img'));
    card.prepend(bg);
  }
  const img = bg.querySelector('img');
  img.alt = '';
  img.src = safe;
}

// Apple Music's artist header, as music.apple.com draws it: the motion
// video over its preview frame (the still setArtistHero already shows),
// the artist's wordmark in place of the typed name, and the page tinted
// in the colour the video was graded in. Every piece is optional — a
// Spotify artist, or an Apple one without editorial art, keeps the plain
// banner and the app's own colours.
const HEX_COLOR = /^[0-9a-f]{6}$/i;
function setArtistExpression(d) {
  const card = $('album-card');
  const root = document.documentElement;
  const on = !!card?.classList.contains('artist-mode');
  const bg = card?.querySelector('.artist-hero-bg');

  // Tint: the site sets --artist-joe-color from this same bgColor.
  const tint = on && HEX_COLOR.test(d?.artist_bg_color || '') ? `#${d.artist_bg_color}` : '';
  if (tint) {
    root.style.setProperty('--artist-tint', tint);
    root.dataset.artistTint = '';
  } else {
    root.style.removeProperty('--artist-tint');
    delete root.dataset.artistTint;
  }

  // Video: HLS plays natively in WebKit (the desktop app) and Safari;
  // elsewhere the still frame stays. It plays with Reduce Motion on too, as
  // music.apple.com does: the still is only the video's poster frame, often
  // a different shot, so without it the header did not look like the site's.
  bg?.querySelector('video')?.remove();
  const video = on ? httpUrlOrNull(d?.artist_video) : null;
  if (bg && video) {
    const v = document.createElement('video');
    if (v.canPlayType('application/vnd.apple.mpegurl')) {
      Object.assign(v, { muted: true, loop: true, autoplay: true, playsInline: true });
      v.setAttribute('muted', '');
      v.poster = bg.querySelector('img')?.src || '';
      v.addEventListener('error', () => v.remove(), { once: true });
      v.src = video;
      bg.appendChild(v);
      v.play?.().catch(() => {});
    }
  }

  // Wordmark: a transparent PNG, drawn white by Apple, sized like the name.
  const nameEl = card?.querySelector('.hero-name');
  const logo = on ? httpUrlOrNull(d?.artist_logo) : null;
  if (nameEl && logo) {
    const name = nameEl.textContent;
    const img = document.createElement('img');
    img.className = 'hero-logo';
    img.alt = name;
    img.decoding = 'async';
    // A logo that fails to load gives the typed name back.
    img.addEventListener('error', () => { nameEl.textContent = name; nameEl.classList.remove('has-logo'); }, { once: true });
    img.src = logo;
    nameEl.textContent = '';
    nameEl.classList.add('has-logo');
    nameEl.appendChild(img);
  }
}

function updateAlbumMeta(trackCount) {
  const searchMode = $('searchMode')?.value === 'search';
  const url = currentUrl.toLowerCase();
  let badgeType = 'ALBUM';

  if (searchMode) {
    badgeType = 'SEARCH';
  } else if (isSongUrl(url)) {
    badgeType = 'TRACK';
  } else if (url.includes('/playlist/')) {
    badgeType = 'PLAYLIST';
  } else if (url.includes('/artist/') || url.includes('/browse/artist')) {
    badgeType = 'ARTIST';
  }

  currentItemType = badgeType;
  $('album-card')?.classList.toggle('artist-mode', badgeType === 'ARTIST');
  $('album-type-badge').textContent = badgeType;

  // Hide "Save all covers" button for albums since they only have one cover
  const saveAllCoversBtn = $('save-all-covers-btn');
  if (saveAllCoversBtn) {
    saveAllCoversBtn.style.display = badgeType === 'ALBUM' ? 'none' : '';
  }

  if (badgeType === 'ARTIST') {
    const albumSet = new Set(
      currentTracks.map(t => t.album || t.album_name || t.release).filter(Boolean)
    );
    const artistStatsRow = $('artist-stats-row');
    if (artistStatsRow) {
      const ac = albumSet.size;
      const albumTrackText = `${ac} album${ac !== 1 ? 's' : ''} · ${trackCount} track${trackCount !== 1 ? 's' : ''}`;
      
      // Append the album/track part to the existing row — once: this runs
      // again on every re-sort, and used to add the same segment each time.
      artistStatsRow.querySelectorAll('.stats-albumtrack').forEach(el => el.remove());
      artistStatsRow.insertAdjacentHTML('beforeend', `<span class="dot-sep stats-albumtrack"> · </span><span class="stats-albumtrack">${escHtml(albumTrackText)}</span>`);
    }
  }
  
  // For albums, show artist, date, and track count in the subtitle
  if (badgeType === 'ALBUM') {
    const artistEl = $('album-artist');
    const artist = artistEl.textContent?.trim() || '';
    const subtitleEl = $('album-subtitle');

    // Built as HTML, not a joined text string: the artist segment needs to
    // be the same clickable span as everywhere else an artist name shows,
    // the date/track-count segments stay plain text.
    let subtitleParts = [];
    if (artist) subtitleParts.push(artistsCreditHtml(g_albumArtistsData, artist, g_albumArtistUrl));
    if (g_albumReleaseDate) {
      const dateStr = String(g_albumReleaseDate).split('T')[0];
      if (dateStr) subtitleParts.push(escHtml(dateStr));
    }
    if (trackCount > 0) {
      subtitleParts.push(escHtml(`${trackCount} track${trackCount !== 1 ? 's' : ''}`));
    }

    const subtitleHtml = subtitleParts.join(' · ');
    subtitleEl.innerHTML = subtitleHtml;
    subtitleEl.style.display = subtitleHtml ? '' : 'none';
  }

  
  
  const artistEl = $('album-artist');
  const hasArtist = Boolean(artistEl.textContent && artistEl.textContent.trim());
  $('album-meta').classList.toggle('no-artist', !hasArtist);
  artistEl.style.display = hasArtist ? '' : 'none';
  const trackCountEl = $('album-tracks-count');
  if (trackCountEl) {
    trackCountEl.textContent = `${trackCount} track${trackCount !== 1 ? 's' : ''}`;
  }
  // A playlist opens like Spotify's: cover beside a big title, then one
  // line of owner · tracks · total time · followers, the buttons under it.
  const isPlaylist = badgeType === 'PLAYLIST';
  $('album-card')?.classList.toggle('playlist-mode', isPlaylist);
  const durationEl = $('album-duration');
  if (durationEl) {
    const totalMs = currentTracks.reduce((s, t) => s + (Number(t.duration_ms) || 0), 0);
    durationEl.textContent = isPlaylist && totalMs > 0 ? formatLongDuration(totalMs) : '';
  }
  tidyOwnerRow();
  // For an album the subtitle already reads "<artist> · <date> · <n> tracks";
  // the standalone artist line right under it was the same word again.
  $('album-meta').style.display = badgeType === 'ALBUM' ? 'none' : '';

  // Technical sheet (right column of the card). Per-track fields are shared
  // across an album, so the first track stands in for the release; ISRC is
  // genuinely per-track and stays out of an album-level sheet.
  if (badgeType === 'PLAYLIST') {
    // Track count and runtime are on the owner line; the rest of the sheet
    // (label, UPC, ℗) would only describe whichever track came first.
    renderAlbumTech([]);
  } else if (badgeType === 'ALBUM') {
    const t0 = currentTracks[0] || {};
    const totalMs = currentTracks.reduce((s, t) => s + (Number(t.duration_ms) || 0), 0);
    const released = g_albumReleaseDate
      ? String(g_albumReleaseDate).split('T')[0]
      : (t0.release_date ? String(t0.release_date).split('T')[0] : '');
    // Ordered by how much the fact is worth here, not by how the metadata
    // happens to arrive: copyright is four lines of legal boilerplate and
    // the least actionable thing in the sheet, and leading with it pushed
    // the release date, the track count and the runtime to the bottom of
    // the column. Those go first now; the ℗ line brings up the rear.
    renderAlbumTech([
      ['Released', withRelativeAge(released)],
      ['Tracks', trackCount > 0 ? String(trackCount) : ''],
      ['Total time', formatLongDuration(totalMs)],
      ['Label', t0.publisher || t0.label],
      ['UPC', t0.upc],
      ['Copyright', collapseCopyright(t0.copyright)],
    ]);
  } else if (badgeType !== 'TRACK') {
    // ARTIST / SEARCH — showSingleTrackCard owns the TRACK case.
    renderAlbumTech([]);
  }

  // Also update the tracks table header label
  setPlaycountHeaderLabel(badgeType === 'PLAYLIST' ? 'Album' : 'Playcount');
}

function showSingleTrackCard(t) {
  
  // Duration overlay sul cover
  const coverEl = $('album-cover');
  coverEl.querySelector('.cover-duration')?.remove();
  const dur = formatDuration(t.duration_ms);
  if (dur && dur !== '—') {
    const badge = document.createElement('span');
    badge.className = 'cover-duration';
    badge.textContent = dur;
    coverEl.appendChild(badge);
  }

  // Explicit badge inline nel titolo
  const titleEl = $('album-title');
  titleEl.innerHTML = escHtml(t.title || t.name || '—');
  if (t.explicit) {
    titleEl.innerHTML = escHtml(t.title || t.name || '—') +
      ' <span class="track-explicit-title">E</span>';
  }

  // setAlbumCard() put the quality string into #album-subtitle, but that
  // sits above the artist name — showing it there too would duplicate the
  // quality chip added below (see track-quality-row below), just in a less
  // useful spot. Hidden here, same as before.
  $('album-subtitle').style.display = 'none';

  // The old below-the-title grid is superseded by the technical sheet in the
  // card's right column — same facts, plus ISRC and label, in the space that
  // was empty anyway.
  const section = $('track-meta-section');
  if (section) { section.innerHTML = ''; section.style.display = 'none'; }
  const playcountRaw = t.plays ?? t.playcount ?? t.playCount ?? t.plays_count;
  const playcountVal = playcountRaw != null && String(playcountRaw).trim() && String(playcountRaw) !== '0'
    ? Number(playcountRaw).toLocaleString('en-US')
    : null;

  // Same ordering rationale as the album sheet in updateAlbumMeta(): the
  // facts you actually read first, with the copyright boilerplate last.
  renderAlbumTech([
    ['Album', t.album || t.album_name || t.release],
    ['Released', withRelativeAge(t.release_date ? String(t.release_date).split('T')[0] : (t.year || ''))],
    ['Plays', playcountVal],
    ['Genre', t.genre],
    ['ISRC', t.isrc],
    ['Label', t.publisher || t.label],
    // Apple Music's credits, as its song page lists them.
    ...(Array.isArray(t.credits) ? t.credits : []).map(group => [
      String(group.title || '').toLowerCase().replace(/(^|\s|&\s)\S/g, c => c.toUpperCase()),
      (group.artists || []).map(a => a.name).filter(Boolean).join(', '),
    ]),
    ['UPC', t.upc],
    ['Copyright', collapseCopyright(t.copyright)],
  ]);

  // Bottoni azione specifici per la track
  const previewUrl = t.preview_url || t.previewUrl || '';
  const extUrl     = t.external_url || t.externalUrl || t.link || '';
  const trackId    = t.id || '';
  $('album-actions').innerHTML = `
  <button class="act-btn primary" data-tip="Download" data-onclick="downloadSingle(0)">
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
    Download
  </button>
  <button class="act-btn secondary ta-preview" data-tip="Play Preview" data-preview-index="0" data-track-id="${trackId}" data-onclick="playPreview(0)">
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="5 3 19 12 5 21 5 3"/></svg>
  </button>
  <button class="act-btn secondary ta-lyrics" data-tip="Save Lyrics (.lrc)" data-track-index="0" data-onclick="downloadLyrics(0)">
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>
  </button>
  <button class="act-btn secondary ta-cover" data-tip="Save Cover (.jpg)" data-track-index="0" data-onclick="downloadCover(0)">
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>
  </button>
  ${extUrl ? `
  <button class="act-btn secondary" data-tip="Open in Spotify" data-onclick="openExternal(0)">
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
  </button>` : ''}
  ${CLI_BUTTON_HTML}
`;

  // The left column (cover + title + artist + actions) is almost always
  // shorter than the right column's technical sheet, leaving the card's
  // lower-left empty. Quality is real, relevant info that was set into
  // #album-subtitle by setAlbumCard() but never shown here (that element
  // sits above the artist name and stays hidden — see above); duration is
  // otherwise only a small overlay on the cover. A quiet chip row under the
  // actions fills the gap with facts about *this* track, not a re-listing
  // of the technical sheet (that already covers ISRC/label/genre/etc).
  let factsRow = document.getElementById('track-quality-row');
  if (!factsRow) {
    factsRow = document.createElement('div');
    factsRow.id = 'track-quality-row';
    factsRow.className = 'track-quality-row';
    $('album-actions').insertAdjacentElement('afterend', factsRow);
  }
  const chips = [];
  if (dur && dur !== '—') chips.push(`<span class="track-quality-chip">${escHtml(dur)}</span>`);
  factsRow.innerHTML = chips.join('');
  factsRow.classList.toggle('hidden', chips.length === 0);
}

function closeAlbumCard() {
  setArtistOverviewMode(false);
  setFetchingState(false);
  stopCurrentPreview();
  $('album-card').classList.add('hidden');
  $('text-search-container')?.classList.add('hidden');
  $('album-subtitle').style.display = '';
  $('track-controls').classList.add('hidden');
  $('track-table-wrap').classList.add('hidden');
  $('recent-wrap').style.display = '';
  $('dl-selected-btn').style.display = 'none';
  currentTracks  = [];
  queue          = [];
  isDownloading  = false;
  renderQueue();
  setStatus('Ready — paste a link and press Fetch');
  $('fetchBtn').disabled  = false;
  $('urlInput').disabled  = false;
  const metaSection = $('track-meta-section');
  if (metaSection) {
  metaSection.innerHTML = '';
  metaSection.style.display = 'none';
}
  renderAlbumTech([]);
  $('album-cover').querySelector('.cover-duration')?.remove();
  document.getElementById('artist-tabs-section')?.remove();
  loadHistoryAndProfiles();
}

// ── Escape HTML ───────────────────────────────────────────────────────────────
function escHtml(s) {
  return String(s || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// Returns the URL only if it's an ordinary http(s) one, otherwise null.
// Cover/avatar URLs arrive from whichever metadata provider answered, and
// they end up in src attributes and in fetches — neither should ever be
// handed a javascript:, data: or file: URL.
// Apple's artwork comes as …/3000x3000bb.jpg, about 2.5 MB each: the size
// a downloaded file's tag wants, not a 170px card. An artist page draws a
// hundred and more of them, which made it slow to fill and left WebKit
// giving up on some (the broken-image "?"). The same image server makes
// any size, so what is only shown asks for `px` (times the screen's
// density). Other hosts' URLs pass through unchanged.
const MZ_ART_RE = /^(https:\/\/[^/]*mzstatic\.com\/image\/thumb\/.+\/)(\d+)x(\d+)([a-z]*)\.(jpg|jpeg|png|webp)$/i;
function artSized(url, px) {
  const u = String(url || '');
  const m = MZ_ART_RE.exec(u);
  if (!m) return u;
  const want = Math.round(px * Math.min(window.devicePixelRatio || 1, 2));
  const w = Number(m[2]), h = Number(m[3]);
  if (!w || !h || Math.max(w, h) <= want) return u;
  const scale = want / Math.max(w, h);
  return `${m[1]}${Math.round(w * scale)}x${Math.round(h * scale)}${m[4]}.${m[5]}`;
}

function httpUrlOrNull(u) {
  if (!u) return null;
  try {
    const parsed = new URL(String(u), window.location.href);
    return (parsed.protocol === 'http:' || parsed.protocol === 'https:')
      ? parsed.href
      : null;
  } catch {
    return null;
  }
}

function formatDuration(ms) {
  if (!ms) return '—';
  const s = Math.floor(ms / 1000);
  const m = Math.floor(s / 60); const sec = s % 60;
  return `${m}:${sec.toString().padStart(2, '0')}`;
}

// "1 hr 14 min" / "38 min" — for a whole album's runtime, where mm:ss would
// just be a big number.
function formatLongDuration(ms) {
  const total = Math.round((Number(ms) || 0) / 1000);
  if (!total) return '';
  const h = Math.floor(total / 3600);
  const m = Math.round((total % 3600) / 60);
  return h ? `${h} hr ${m} min` : `${m} min`;
}

// "2026-05-15 · 3 months ago" — the date on its own answers "when", but not
// the question actually being asked of a release date in a downloader ("is
// this new?"), which otherwise needs mental arithmetic against today. Coarse
// on purpose: one unit, and nothing at all under a day ("today"), because a
// release date has no time-of-day to be precise about.
function withRelativeAge(dateStr) {
  const raw = String(dateStr || '').trim();
  if (!raw) return '';
  // A year-only release ("1998") has no month or day to measure from.
  if (!/^\d{4}-\d{2}-\d{2}/.test(raw)) return raw;

  const then = new Date(raw + 'T00:00:00');
  if (Number.isNaN(then.getTime())) return raw;

  const days = Math.floor((Date.now() - then.getTime()) / 86400000);
  if (days < 0) return `${raw} · upcoming`;
  if (days === 0) return `${raw} · today`;

  const units = [
    [365, 'year'],
    [30, 'month'],
    [7, 'week'],
    [1, 'day'],
  ];
  for (const [size, label] of units) {
    const n = Math.floor(days / size);
    if (n >= 1) return `${raw} · ${n} ${label}${n === 1 ? '' : 's'} ago`;
  }
  return raw;
}

// ── Album card: the technical sheet ─────────────────────────────────────────
// Fills the column on the right of #album-card. `rows` is [[key, value], …];
// a row whose value is empty or "—" is dropped, and an empty result hides
// the whole column so artist pages (which have none of this) don't show an
// empty rule. Values are plain text — an ISRC belongs in a box you can
// select from, not behind a link.
function renderAlbumTech(rows) {
  const el = $('album-tech');
  if (!el) return;
  const clean = (rows || []).filter(r => {
    const v = r && r[1] != null ? String(r[1]).trim() : '';
    return v && v !== '—';
  });
  el.innerHTML = clean.map(([k, v]) => {
    const val = String(v);
    const barcode = k === 'UPC' ? retailBarcodeSvg(val) : '';
    if (barcode) {
      return `<div class="tech-row"><div class="tech-k">${escHtml(k)}</div>` +
        `<button type="button" class="tech-barcode" data-copy="${escHtml(val)}" ` +
        `title="Click to copy ${escHtml(val)}" aria-label="UPC ${escHtml(val)}, click to copy">` +
        `${barcode}<span class="tech-barcode-num">${escHtml(val)}</span></button></div>`;
    }
    // The ℗/© line is the one long value: it gets the sheet's full width.
    const wide = /copyright|℗|©/i.test(k) ? ' wide' : '';
    return `<div class="tech-row${wide}"><div class="tech-k">${escHtml(k)}</div>` +
      `<div class="tech-v" title="${escHtml(val)}">${escHtml(val)}</div></div>`;
  }).join('');
  el.classList.toggle('hidden', clean.length === 0);
}

// Clicking a rendered barcode copies the number it encodes.
document.addEventListener('click', (e) => {
  const btn = e.target.closest?.('.tech-barcode');
  if (!btn) return;
  const text = btn.dataset.copy || '';
  if (navigator.clipboard?.writeText) {
    navigator.clipboard.writeText(text).then(
      () => toastMgr.success('UPC copied'),
      () => toastMgr.warning('Could not copy the UPC'));
  }
});

// UPC-A (12 digits) / EAN-13 (13 digits) as an inline SVG, like the mobile
// app's metadata screen. Anything that is not a valid retail code (wrong
// length, bad check digit) returns '' and the caller shows plain text.
const _EAN_L = ['0001101','0011001','0010011','0111101','0100011','0110001','0101111','0111011','0110111','0001011'];
const _EAN_G = ['0100111','0110011','0011011','0100001','0011101','0111001','0000101','0010001','0001001','0010111'];
const _EAN_R = ['1110010','1100110','1101100','1000010','1011100','1001110','1010000','1000100','1001000','1110100'];
const _EAN_PARITY = ['LLLLLL','LLGLGG','LLGGLG','LLGGGL','LGLLGG','LGGLLG','LGGGLL','LGLGLG','LGLGGL','LGGLGL'];

function retailBarcodeSvg(value) {
  const digits = String(value || '').replace(/[\s-]/g, '');
  if (!/^\d{12,13}$/.test(digits)) return '';
  const ean = digits.length === 12 ? '0' + digits : digits;
  const d = [...ean].map(Number);
  const sum = d.slice(0, 12).reduce((a, n, i) => a + n * (i % 2 ? 3 : 1), 0);
  if ((10 - (sum % 10)) % 10 !== d[12]) return '';
  const parity = _EAN_PARITY[d[0]];
  let modules = '101';
  for (let i = 0; i < 6; i++) modules += (parity[i] === 'L' ? _EAN_L : _EAN_G)[d[i + 1]];
  modules += '01010';
  for (let i = 7; i < 13; i++) modules += _EAN_R[d[i]];
  modules += '101';
  const quiet = 9;
  let path = '';
  for (let i = 0; i < modules.length; ) {
    if (modules[i] !== '1') { i++; continue; }
    let j = i;
    while (modules[j] === '1') j++;
    path += `M${quiet + i} 0h${j - i}v1h-${j - i}z`;
    i = j;
  }
  return `<svg class="tech-barcode-svg" viewBox="0 0 ${modules.length + quiet * 2} 1" ` +
    `preserveAspectRatio="none" aria-hidden="true"><path d="${path}"/></svg>`;
}

const ARTIST_DL_SVG = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>';

function downloadTrackIndices(indices) {
  addToQueue(indices);
  startDownloadQueue();
  $('queue-drawer').classList.add('open');
}

// "Latest Release" beside "Top Songs", above the artist's tabs: the newest
// album (by release date) and the five most played tracks.
function artistHighlightsEl(tracks, albumMap) {
  const albums = [...albumMap.values()];
  const dateOf = a => {
    const t = tracks[a.indices[0]] || {};
    return String(t.release_date || a.year || '').split('T')[0];
  };
  const latest = albums.slice().sort((x, y) => dateOf(y).localeCompare(dateOf(x)))[0];
  const plays = t => Number(t.plays ?? t.playcount ?? t.playCount ?? t.plays_count ?? 0) || 0;
  // The same song on several releases (single, album, deluxe) shows once.
  const seenTitles = new Set();
  // Apple Music sends its own Top Songs order (top_rank) and no play
  // counts; Spotify sends play counts.
  const ranked = tracks.some(t => Number(t.top_rank) > 0);
  const top = tracks.map((t, i) => ({ t, i }))
    .filter(x => (ranked ? Number(x.t.top_rank) > 0 : plays(x.t) > 0))
    .sort((x, y) => (ranked ? Number(x.t.top_rank) - Number(y.t.top_rank) : plays(y.t) - plays(x.t)))
    .filter(({ t }) => {
      const k = String(t.title || t.name || '').toLowerCase().replace(/\s+/g, ' ').trim();
      if (seenTitles.has(k)) return false;
      seenTitles.add(k);
      return true;
    }).slice(0, 5);
  if (!latest && !top.length) return null;

  const wrap = document.createElement('div');
  wrap.className = 'artist-highlights';

  if (latest) {
    const sec = document.createElement('section');
    sec.className = 'ah-sec';
    const cover = httpUrlOrNull(artSized(latest.cover, 220));
    const date = dateOf(latest);
    sec.innerHTML = `<h3 class="ah-title">Latest Release</h3>
      <div class="ah-latest" title="${escHtml(latest.name)}">
        <div class="ah-latest-cover">${cover ? `<img src="${escHtml(cover)}" alt="" loading="lazy">` : '🎵'}</div>
        <div class="ah-latest-info">
          ${date ? `<span class="ah-muted">${escHtml(date)}</span>` : ''}
          <span class="ah-latest-name">${escHtml(latest.name)}</span>
          <span class="ah-muted">${latest.indices.length} track${latest.indices.length !== 1 ? 's' : ''}</span>
          <button type="button" class="ah-dl" title="Download this release" aria-label="Download ${escHtml(latest.name)}">${ARTIST_DL_SVG}</button>
        </div>
      </div>`;
    const card = sec.querySelector('.ah-latest');
    makeActivatable(card);
    card.onclick = () => {
      if (latest.url) { goToUrl(latest.url); return; }
      switchArtistTab('tracks');
      selectOnlyTracks(latest.indices);
    };
    sec.querySelector('.ah-dl').onclick = e => { e.stopPropagation(); downloadTrackIndices(latest.indices); };
    wrap.appendChild(sec);
  }

  if (top.length) {
    const sec = document.createElement('section');
    sec.className = 'ah-sec';
    sec.innerHTML = `<h3 class="ah-title">Top Songs</h3><div class="ah-top" role="list"></div>`;
    const list = sec.querySelector('.ah-top');
    top.forEach(({ t, i }) => {
      const row = document.createElement('div');
      row.className = 'ah-top-row';
      row.setAttribute('role', 'listitem');
      const cover = httpUrlOrNull(artSized(t.cover_url || t.cover || t.image, 96));
      row.innerHTML = `
        <div class="ah-top-cover">${cover ? `<img src="${escHtml(cover)}" alt="" loading="lazy">` : ''}</div>
        <div class="ah-top-info">
          <span class="ah-top-name">${escHtml(t.title || t.name || '—')}</span>
          <span class="ah-muted">${escHtml(plays(t) > 0 ? (t.artists || t.artist || '') : [t.album, String(t.release_date || '').slice(0, 4)].filter(Boolean).join(' · '))}</span>
        </div>
        <span class="ah-muted ah-num">${plays(t) > 0 ? plays(t).toLocaleString('en-US') : ''}</span>
        <button type="button" class="ah-dl" title="Download" aria-label="Download ${escHtml(t.title || t.name || '')}">${ARTIST_DL_SVG}</button>`;
      row.querySelector('.ah-dl').onclick = () => downloadTrackIndices([i]);
      list.appendChild(row);
    });
    wrap.appendChild(sec);
  }
  return wrap;
}

// "About", under Latest Release / Top Songs: the artist's picture with the
// world rank, listeners and biography over it. The hero above keeps only the
// name and the download controls, the way Spotify lays out an artist page.
//
// Called from setAlbumCard (metadata) and injectArtistTabs (tracks): the two
// arrive on separate callbacks in either order, so whichever comes second
// is the one that actually draws it.
let g_artistAbout = null;
function renderArtistAbout() {
  const host = document.getElementById('artist-tabs-section');
  document.getElementById('artist-about')?.remove();
  const a = g_artistAbout;
  if (!host || !a) return;
  const num = v => (v === 0 || v) && Number.isFinite(Number(v)) && Number(v) > 0 ? Number(v) : 0;
  const listeners = num(a.listeners), followers = num(a.followers), rank = num(a.rank);
  const albums = new Set(currentTracks.map(t => t.album_url || t.album || t.album_name || t.release).filter(Boolean)).size;
  const tracks = currentTracks.length;
  const facts = [];
  if (followers) facts.push(`${followers.toLocaleString('en-US')} followers`);
  if (albums) facts.push(`${albums} album${albums !== 1 ? 's' : ''}`);
  if (tracks) facts.push(`${tracks} track${tracks !== 1 ? 's' : ''}`);
  if (!listeners && !rank && !a.bio && !facts.length) return;

  const sec = document.createElement('section');
  sec.id = 'artist-about';
  sec.className = 'artist-about';
  const img = httpUrlOrNull(artSized(a.avatar, 1000));
  sec.innerHTML = `<h3 class="ah-title">About</h3>
    <div class="about-card${img ? '' : ' no-photo'}">
      ${img ? `<img class="about-photo" src="${escHtml(img)}" alt="" loading="lazy">` : ''}
      ${rank ? `<div class="about-rank"><strong>#${rank}</strong><span>in the world</span></div>` : ''}
      <div class="about-text">
        ${listeners ? `<p class="about-listeners">${listeners.toLocaleString('en-US')} monthly listeners</p>` : ''}
        ${facts.length ? `<p class="about-facts">${facts.map(escHtml).join(' · ')}</p>` : ''}
        <p class="about-bio"></p>
        <button type="button" class="about-more" hidden aria-expanded="false">See more…</button>
      </div>
    </div>`;
  // textContent: the bio is remote text (see setAlbumCard).
  const bio = sec.querySelector('.about-bio');
  // Providers send it with HTML entities left in ("World&#39;s"); decoded
  // through an inert document, then set as text.
  bio.textContent = a.bio.includes('&')
    ? (new DOMParser().parseFromString(a.bio, 'text/html').documentElement.textContent || a.bio)
    : a.bio;
  if (!a.bio) bio.remove();
  const more = sec.querySelector('.about-more');
  more.onclick = () => {
    const open = sec.classList.toggle('expanded');
    more.textContent = open ? 'See less' : 'See more…';
    more.setAttribute('aria-expanded', String(open));
  };
  // At the foot of the Spotify-style overview when there is one, otherwise
  // after the highlights, ahead of the Albums/Tracks tabs.
  const overview = host.querySelector('#artist-overview');
  const highlights = host.querySelector('.artist-highlights');
  if (overview) overview.appendChild(sec);
  else if (highlights) highlights.after(sec);
  else host.prepend(sec);
  if (a.bio) requestAnimationFrame(() => {
    more.hidden = !(bio.scrollHeight > bio.clientHeight + 1);
  });
}

// ── Artist page overview ─────────────────────────────────────────────────
// A Spotify artist link opens like Spotify's own page: Popular, the
// artist's pick, Discography, the playlists that feature the artist, Fans
// also like, Appears on and About (core/artist_overview.py sends the data).
// The full track list — what selection and Download All work on — sits
// behind Discography's "Show all", with a way back.
//
// Anything that can be opened opens in Mornye, like pasting its link; the
// round button on a release's cover downloads it straight away, but only
// for releases whose tracks are in this artist's list.
let g_artistOverviewToken = 0;

function artistCardEl(item, { round = false, sub = '', albumMap = null } = {}) {
  const card = document.createElement('div');
  card.className = 'ao-card' + (round ? ' round' : '');
  card.setAttribute('role', 'listitem');
  const img = httpUrlOrNull(artSized(round ? item.avatar : item.cover, 240));
  const album = albumMap && item.url ? albumMap.get(item.url) : null;
  card.innerHTML = `
    <div class="ao-card-img">${img ? `<img src="${escHtml(img)}" alt="" loading="lazy">` : '<span aria-hidden="true">🎵</span>'}
      ${album ? `<button type="button" class="ao-card-dl" title="Download" aria-label="Download ${escHtml(item.name)}">${ARTIST_DL_SVG}</button>` : ''}
    </div>
    <span class="ao-card-name" title="${escHtml(item.name)}">${escHtml(item.name || '—')}</span>
    ${sub ? `<span class="ao-card-sub">${escHtml(sub)}</span>` : ''}`;
  if (item.url) {
    makeActivatable(card);
    card.onclick = () => goToUrl(item.url);
  }
  if (album) card.querySelector('.ao-card-dl').onclick = e => { e.stopPropagation(); downloadTrackIndices(album.indices); };
  return card;
}

function artistShelfEl(title, items, opts = {}) {
  if (!items?.length) return null;
  const sec = document.createElement('section');
  sec.className = 'ao-sec';
  sec.innerHTML = `<div class="ao-head"><h3 class="ah-title">${escHtml(title)}</h3>
      <button type="button" class="ao-showall" aria-expanded="false">Show all</button></div>
    <div class="ao-row" role="list"></div>`;
  const row = sec.querySelector('.ao-row');
  items.forEach(it => row.appendChild(artistCardEl(it, { ...opts, sub: opts.subOf ? opts.subOf(it) : '' })));
  // "Show all" lays the shelf out as a grid of every card; the row itself
  // scrolls sideways with no scrollbar (trackpad / touch).
  const all = sec.querySelector('.ao-showall');
  all.onclick = () => {
    const open = sec.classList.toggle('show-all');
    all.textContent = open ? 'Show less' : 'Show all';
    all.setAttribute('aria-expanded', String(open));
    if (!open) sec.scrollIntoView({ block: 'nearest' });
  };
  return sec;
}

const RELEASE_KIND = { album: 'Album', single: 'Single', ep: 'EP', compilation: 'Compilation' };
const releaseSub = r => [r.year, RELEASE_KIND[r.type] || ''].filter(Boolean).join(' · ');

function artistPopularEl(top, tracks) {
  if (!top.length) return null;
  // By Spotify id first; a hit Spotify credits to another release of the
  // same song (a single vs. the album) falls back to its title.
  const byId = new Map(tracks.map((t, i) => [t.id, i]));
  const key = v => String(v || '').toLowerCase().replace(/\s+/g, ' ').trim();
  const byTitle = new Map();
  tracks.forEach((t, i) => { const k = key(t.title || t.name); if (k && !byTitle.has(k)) byTitle.set(k, i); });
  const sec = document.createElement('section');
  sec.className = 'ao-sec ao-popular';
  sec.innerHTML = `<h3 class="ah-title">Popular</h3><div class="ao-pop" role="list"></div>
    ${top.length > 5 ? '<button type="button" class="ao-more" aria-expanded="false">Show more</button>' : ''}`;
  const list = sec.querySelector('.ao-pop');
  top.forEach((t, n) => {
    const idx = byId.has(t.id) ? byId.get(t.id) : byTitle.get(key(t.name));
    const row = document.createElement('div');
    row.className = 'ao-pop-row' + (n >= 5 ? ' extra' : '');
    row.setAttribute('role', 'listitem');
    const cover = httpUrlOrNull(t.cover);
    row.innerHTML = `
      <span class="ao-pop-n">${n + 1}</span>
      <div class="ah-top-cover">${cover ? `<img src="${escHtml(cover)}" alt="" loading="lazy">` : ''}</div>
      <div class="ah-top-info">
        <span class="ah-top-name">${escHtml(t.name)}</span>
        <span class="ah-muted">${t.explicit ? '<span class="ao-explicit" title="Explicit">E</span> ' : ''}${escHtml((t.artists || []).join(', '))}</span>
      </div>
      <span class="ah-muted ah-num ao-pop-plays">${t.plays ? t.plays.toLocaleString('en-US') : ''}</span>
      <span class="ah-muted ah-num">${t.duration_ms ? formatDuration(t.duration_ms) : ''}</span>
      ${idx !== undefined || t.url ? `<button type="button" class="ah-dl" title="Download" aria-label="Download ${escHtml(t.name)}">${ARTIST_DL_SVG}</button>` : '<span></span>'}`;
    const dl = row.querySelector('.ah-dl');
    if (idx !== undefined) dl.onclick = e => { e.stopPropagation(); downloadTrackIndices([idx]); };
    // A song from someone else's record (a feature) is not in this artist's
    // list: fetched from its own link and queued as a list of its own.
    else if (dl) dl.onclick = e => { e.stopPropagation(); downloadTrackLink(t, dl); };
    if (t.url) {
      const name = row.querySelector('.ah-top-name');
      name.classList.add('ao-link');
      makeActivatable(name, 'link');
      name.onclick = () => goToUrl(t.url);
    }
    list.appendChild(row);
  });
  const more = sec.querySelector('.ao-more');
  if (more) more.onclick = () => {
    const open = sec.classList.toggle('expanded');
    more.textContent = open ? 'Show less' : 'Show more';
    more.setAttribute('aria-expanded', String(open));
  };
  return sec;
}

// Queue one track by its link (load_track_for_queue keeps it backend-side as
// a one-track list), then start the Queue the same way a row's button does.
async function downloadTrackLink(t, btn) {
  if (btn?.classList.contains('ta-loading')) return;
  const already = queue.find(q => q.spotify_id === t.id && q.source === t.url && !['error', 'skipped'].includes(q.status));
  if (already) { showToast(`"${t.name}" is already in the Queue`, 'info'); return; }
  setTaBtnState(btn, 'loading');
  let res = null;
  try { res = await window.pywebview.api.load_track_for_queue(t.url); } catch (e) { res = null; }
  if (!res || res.status !== 'ok') {
    setTaBtnState(btn, 'error');
    resetTaBtnAfter(btn, 2000);
    showToast(`Could not load "${t.name}"${res?.error ? `: ${res.error}` : ''}`, 'error');
    return;
  }
  const tr = res.track || {};
  queue.push({
    id: tr.id || t.id || t.url,
    spotify_id: tr.id || t.id || t.url,
    source: t.url,
    list: res.list_id,
    index: 0,
    title: tr.title || t.name,
    artist: tr.artist || (t.artists || []).join(', '),
    album: tr.album || '',
    status: 'waiting',
    progress: 0,
    file_path: '',
    file_size_mb: 0,
  });
  renderQueue();
  const emptyMsg = $('queue-empty');
  if (emptyMsg) emptyMsg.style.display = 'none';
  setTaBtnState(btn, 'success');
  resetTaBtnAfter(btn, 1500);
  startDownloadQueue();
  $('queue-drawer')?.classList.add('open');
}

function artistPickEl(pick) {
  if (!pick?.url) return null;
  const sec = document.createElement('section');
  sec.className = 'ao-sec ao-pick';
  const cover = httpUrlOrNull(pick.cover);
  sec.innerHTML = `<h3 class="ah-title">Artist pick</h3>
    <div class="ao-pick-card">
      <div class="ao-pick-cover">${cover ? `<img src="${escHtml(cover)}" alt="" loading="lazy">` : ''}</div>
      <div class="ao-pick-info">
        ${pick.comment ? `<span class="ao-pick-note">${escHtml(pick.comment)}</span>` : ''}
        <span class="ah-latest-name">${escHtml(pick.name)}</span>
        <span class="ah-muted">${escHtml(RELEASE_KIND[pick.type] || (pick.type === 'playlist' ? 'Playlist' : ''))}</span>
      </div>
    </div>`;
  const card = sec.querySelector('.ao-pick-card');
  makeActivatable(card);
  card.onclick = () => goToUrl(pick.url);
  return sec;
}

function artistDiscographyEl(disc, albumMap, onShowAll) {
  const groups = [
    ['popular', 'Popular releases'],
    ['albums', 'Albums'],
    ['singles', 'Singles and EPs'],
    ['compilations', 'Compilations'],
  ].filter(([k]) => disc?.[k]?.length);
  if (!groups.length) return null;
  const sec = document.createElement('section');
  sec.className = 'ao-sec';
  sec.innerHTML = `<div class="ao-head"><h3 class="ah-title">Discography</h3>
      <button type="button" class="ao-showall">Show all</button></div>
    <div class="ao-chips" role="group" aria-label="Release type"></div>
    <div class="ao-row" role="list"></div>`;
  const chips = sec.querySelector('.ao-chips');
  const row = sec.querySelector('.ao-row');
  const show = key => {
    chips.querySelectorAll('button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.k === key)));
    row.textContent = '';
    row.scrollLeft = 0;
    disc[key].forEach(r => row.appendChild(artistCardEl(r, { albumMap, sub: releaseSub(r) })));
  };
  groups.forEach(([k, label]) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'ao-chip';
    b.dataset.k = k;
    b.textContent = label;
    b.onclick = () => show(k);
    chips.appendChild(b);
  });
  sec.querySelector('.ao-showall').onclick = onShowAll;
  show(groups[0][0]);
  return sec;
}

function setArtistOverviewMode(on) {
  document.querySelector('.list-container')?.classList.toggle('artist-overview-mode', !!on);
}

async function loadArtistOverview(host, tracks, albumMap) {
  const token = ++g_artistOverviewToken;
  const url = currentUrl;
  let data = null;
  if (window.pywebview?.api?.get_artist_overview && /spotify(\.com\/(intl-[a-z-]+\/)?artist\/|:artist:)/i.test(url)) {
    try { data = await window.pywebview.api.get_artist_overview(url); } catch (e) { data = null; }
  }
  if (token !== g_artistOverviewToken || !host.isConnected) return; // another artist opened meanwhile
  host.querySelector('#artist-overview')?.remove();
  if (!data || data.status !== 'ok') {
    // No overview (another provider, or Spotify refused): the earlier
    // layout — highlights over the full list — rather than an empty page.
    setArtistOverviewMode(false);
    const highlights = artistHighlightsEl(tracks, albumMap);
    if (highlights) host.prepend(highlights);
    renderArtistAbout();
    return;
  }
  const byUrl = new Map([...albumMap.values()].filter(a => a.url).map(a => [a.url, a]));
  const ov = document.createElement('div');
  ov.id = 'artist-overview';
  const top = document.createElement('div');
  top.className = 'ao-top';
  const pop = artistPopularEl(data.top_tracks || [], tracks);
  const pick = artistPickEl(data.pick);
  if (pop) top.appendChild(pop);
  if (pick) top.appendChild(pick);
  if (top.childElementCount) ov.appendChild(top);
  const name = g_artistAbout?.name || 'the artist';
  const showAll = () => {
    setArtistOverviewMode(false);
    switchArtistTab('albums');
    host.querySelector('.artist-tabs-bar')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };
  const playlistSub = p => p.owner ? `By ${p.owner}` : '';
  [
    artistDiscographyEl(data.discography, byUrl, showAll),
    artistShelfEl(`Featuring ${name}`, data.featuring, { subOf: p => p.description || playlistSub(p) }),
    artistShelfEl('Artist playlists', data.playlists, { subOf: playlistSub }),
    artistShelfEl('Discovered on', data.discovered_on, { subOf: p => p.description || playlistSub(p) }),
    artistShelfEl('Fans also like', data.related_artists, { round: true, subOf: () => 'Artist' }),
    artistShelfEl('Appears on', data.appears_on, { albumMap: byUrl, subOf: r => [r.year, (r.artists || []).join(', ')].filter(Boolean).join(' · ') }),
  ].forEach(el => el && ov.appendChild(el));
  host.prepend(ov);
  setArtistOverviewMode(true);
  renderArtistAbout();
}

// The artist's releases in a music app's order: their own albums, their
// singles and EPs (Apple marks singles; three tracks or fewer, or "Single"
// / "EP" in the title, count too), then other artists' releases they appear
// on. Empty groups are left out.
function artistAlbumGroups(albumMap) {
  const artist = String(g_artistAbout?.name || '').trim().toLowerCase();
  const newest = list => list.sort((x, y) => String(y.date || y.year).localeCompare(String(x.date || x.year)));
  const groups = { albums: [], singles: [], appears: [] };
  albumMap.forEach(album => {
    const owner = album.albumArtist.toLowerCase();
    if (artist && owner && !owner.includes(artist)) groups.appears.push(album);
    else if (album.type === 'single' || /(?:^|[\s(\[-])(single|ep)(?:$|[\s)\]])/i.test(album.name)
      || (album.type !== 'album' && album.indices.length <= 3)) groups.singles.push(album);
    else groups.albums.push(album);
  });
  return [
    { title: 'Albums', albums: newest(groups.albums) },
    { title: 'Singles & EPs', albums: newest(groups.singles) },
    { title: 'Appears On', albums: newest(groups.appears) },
  ].filter(g => g.albums.length);
}

function injectArtistTabs(tracks) {
  document.getElementById('artist-tabs-section')?.remove();

  // Raggruppa per album
  const albumMap = new Map();
  tracks.forEach((t, idx) => {
    // Keyed on the album's own URL when the backend resolved one: two
    // different albums can share a name (a re-release, a deluxe edition),
    // and grouping those together put one cover on someone else's tracks.
    const name = t.album || t.album_name || t.release || '—';
    const key = t.album_url || name;
    if (!albumMap.has(key)) {
      albumMap.set(key, {
        name,
        url: t.album_url || '',
        cover: t.cover_url || t.cover || t.image || '',
        year: t.release_date ? String(t.release_date).split('T')[0].substring(0, 4) : (t.year || ''),
        date: String(t.release_date || '').split('T')[0],
        type: String(t.album_type || '').toLowerCase(),
        albumArtist: String(t.album_artist || ''),
        explicit: false,
        indices: []
      });
    }
    const entry = albumMap.get(key);
    entry.indices.push(idx);
    // Apple lists the clean and explicit editions as two albums with the
    // same name and cover; as on its site, one explicit track marks the album.
    if (t.explicit) entry.explicit = true;
  });

  const section = document.createElement('div');
  section.id = 'artist-tabs-section';
  // The overview (or, failing that, the highlights) is put in by
  // loadArtistOverview once it arrives.

  // ── Tab bar ──
  const tabBar = document.createElement('div');
  tabBar.className = 'artist-tabs-bar';
  [
    { id: 'albums',  label: `Albums · ${albumMap.size}` },
    { id: 'tracks',  label: `All Tracks · ${tracks.length}` },
    { id: 'gallery', label: 'Gallery' },
  ].forEach((tab, i) => {
    const btn = document.createElement('button');
    btn.className = 'artist-tab' + (i === 1 ? ' active' : '');
    btn.id = `artist-tab-${tab.id}-btn`;
    btn.textContent = tab.label;
    btn.onclick = () => switchArtistTab(tab.id);
    tabBar.appendChild(btn);
  });
  const back = document.createElement('button');
  back.type = 'button';
  back.className = 'artist-tab ao-back';
  back.textContent = '← Overview';
  back.onclick = () => {
    setArtistOverviewMode(true);
    $('artist-overview')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };
  tabBar.prepend(back);
  section.appendChild(tabBar);

  // ── Albums Panel ──
  const albumsPanel = document.createElement('div');
  albumsPanel.id = 'artist-panel-albums';
  albumsPanel.className = 'artist-albums-grid';
  albumsPanel.style.display = 'none';
  // As a music app's artist page: Albums, then Singles & EPs, then the
  // releases of others it appears on, each newest first under its title.
  artistAlbumGroups(albumMap).forEach(group => {
    const head = document.createElement('h3');
    head.className = 'aac-group';
    head.textContent = group.title;
    albumsPanel.appendChild(head);
    group.albums.forEach(album => {
    const card = document.createElement('div');
    card.className = 'artist-album-card';
    makeActivatable(card);
    // A card-sized copy, not the 3000px original (artSized).
    const coverHtml = album.cover
      ? `<img src="${escHtml(artSized(album.cover, 240))}" alt="cover" loading="lazy" decoding="async" data-onerror="this.parentElement.innerHTML='🎵'">`
      : '🎵';
    card.innerHTML = `
      <div class="aac-cover">${coverHtml}
        <button class="aac-dl" title="Download this album" aria-label="Download this album">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
        </button>
      </div>
      <div class="aac-body">
        <div class="aac-name" title="${escHtml(album.name)}${album.explicit ? ' (Explicit)' : ''}">${album.explicit ? '<span class="explicit-badge" aria-label="Explicit">E</span> ' : ''}${escHtml(album.name)}</div>
        <div class="aac-meta">${album.year ? album.year + ' · ' : ''}${album.indices.length} track${album.indices.length !== 1 ? 's' : ''}</div>
      </div>`;

    // Clicking the card *opens* the album — the same view you would get by
    // pasting its link. It used to queue every track and start downloading
    // immediately, which is a surprising amount to set in motion for one
    // click on a picture, and left no way to simply look at an album.
    // Downloading is still one click, on the button on the cover.
    const openAlbum = () => {
      if (album.url) { goToUrl(album.url); return; }
      // No album URL from this provider: fall back to selecting the album's
      // tracks in the list below, which is at least non-destructive.
      switchArtistTab('tracks');
      selectOnlyTracks(album.indices);
    };
    card.onclick = openAlbum;
    card.querySelector('.aac-dl').onclick = (e) => {
      e.stopPropagation();
      addToQueue(album.indices);
      startDownloadQueue();
      $('queue-drawer').classList.add('open');
    };
    albumsPanel.appendChild(card);
    });
  });
  section.appendChild(albumsPanel);

  // ── Gallery Panel ──
  const galleryPanel = document.createElement('div');
  galleryPanel.id = 'artist-panel-gallery';
  galleryPanel.className = 'artist-gallery-grid';
  galleryPanel.style.display = 'none';
  galleryPanel.innerHTML = `<div class="artist-gallery-empty">⏳ Loading gallery…</div>`;
  section.appendChild(galleryPanel);

  // Insert before track-controls
  const listContainer = document.querySelector('.list-container');
  listContainer.insertBefore(section, $('track-controls'));
  // Spotify links start on the overview; the list shows once it is asked for.
  const spotifyArtist = /spotify(\.com\/(intl-[a-z-]+\/)?artist\/|:artist:)/i.test(currentUrl);
  setArtistOverviewMode(spotifyArtist);
  if (spotifyArtist) {
    const loading = document.createElement('div');
    loading.id = 'artist-overview';
    loading.className = 'ao-loading';
    loading.textContent = 'Loading the artist page…';
    section.prepend(loading);
  }
  renderArtistAbout();
  loadArtistOverview(section, tracks, albumMap);

  // Load gallery in background
  loadArtistGallery(galleryPanel);
}

async function loadArtistGallery(panel) {
  try {
    // Prova API Python se disponibile
    if (window.pywebview?.api?.get_artist_images) {
      const images = await window.pywebview.api.get_artist_images(currentUrl);
      if (images?.length) {
        panel.innerHTML = images.map(url =>
          `<img class="artist-gallery-img" src="${escHtml(url)}" alt="Artist photo" loading="lazy" data-onerror="this.remove()">`
        ).join('');
        return;
      }
    }
  } catch(e) {}

  // Fallback: cover degli album come gallery
  const covers = [...new Set(currentTracks.map(t => t.cover_url || t.cover || t.image).filter(Boolean))];
  if (covers.length) {
    panel.innerHTML = covers.map(url =>
      `<img class="artist-gallery-img" src="${escHtml(url)}" alt="Cover" loading="lazy" data-onerror="this.remove()">`
    ).join('');
  } else {
    panel.innerHTML = `<div class="artist-gallery-empty">🖼 No gallery images available.</div>`;
  }
}

// The tab picked on this artist's page, kept while the same page re-renders
// (a sort, another page of tracks); a new artist opens on Albums.
let artistTabChoice = { url: '', tab: 'albums' };
function switchArtistTab(tabName) {
  artistTabChoice = { url: currentUrl, tab: tabName };
  document.querySelectorAll('.artist-tab').forEach(b => b.classList.remove('active'));
  $(`artist-tab-${tabName}-btn`)?.classList.add('active');

  const albumsPanel   = $('artist-panel-albums');
  const galleryPanel  = $('artist-panel-gallery');
  const trackControls = $('track-controls');
  const trackTable    = $('track-table-wrap');

  if (albumsPanel)  albumsPanel.style.display  = tabName === 'albums'  ? 'grid' : 'none';
  if (galleryPanel) galleryPanel.style.display  = tabName === 'gallery' ? 'grid' : 'none';

  if (tabName === 'tracks') {
    trackControls?.classList.remove('hidden');
    trackTable?.classList.remove('hidden');
  } else {
    trackControls?.classList.add('hidden');
    trackTable?.classList.add('hidden');
  }
}

// ── Track rendering ───────────────────────────────────────────────────────────
function renderTracks(tracks, page = 1) {
  stopCurrentPreview();
  
  // Save original order so it can be restored later
  tracks.forEach((t, idx) => {
    if (t._originalIndex === undefined) {
      t._originalIndex = idx;
    }
  });

  currentTracks = tracks;
  currentPage = page;
  
  // Calculate pagination
  const totalPages = Math.ceil(tracks.length / TRACKS_PER_PAGE);
  const startIdx = (currentPage - 1) * TRACKS_PER_PAGE;
  const endIdx = startIdx + TRACKS_PER_PAGE;
  const pageTrackS = tracks.slice(startIdx, endIdx);
  
  const container = $('track-rows');
  // Keep the table's height while it refills, so a re-sort or page change
  // doesn't collapse the page under the scroll position.
  if (container.offsetHeight) container.style.minHeight = container.offsetHeight + 'px';
  container.innerHTML = '';
  const header = document.querySelector('.track-table-header');
  if (header) header.style.display = '';
  const renderToken = ++trackRenderToken;
  const batchSize = 40;
  let index = 0;
  // A re-sort redraws one page in a blink: the "Rendering…" line would
  // only push the table down and pull it back up under the pointer.
  const quietRender = !!g_sortAnchor;
  setTrackRenderStatus(`Rendering 0/${pageTrackS.length} tracks…`, pageTrackS.length > 0 && !quietRender);

  // Detect if current view is a playlist so we can show album instead of playcount
  const searchMode = $('searchMode')?.value === 'search';
  const url = (currentUrl || '').toLowerCase();
  const isPlaylist = url && (url.includes('/playlist/') || (url.includes('list=') && !url.includes('olak5uy_')));
  setPlaycountHeaderLabel(isPlaylist ? 'Album' : 'Playcount');

  const renderBatch = () => {
    if (renderToken !== trackRenderToken) return;
    const fragment = document.createDocumentFragment();
    const end = Math.min(index + batchSize, pageTrackS.length);

    for (; index < end; index += 1) {
      const t = pageTrackS[index];
      const globalIndex = startIdx + index; // For compatibility with global indices
      const row = document.createElement('div');
      row.className = 'track-row';
      row.id = `track-row-${globalIndex}`;
      // Same identity addToQueue() uses for a queue item's spotify_id — lets
      // syncTrackRowsWithQueue() find this row even after a re-sort changes
      // which #track-row-N id it currently holds.
      if (t.id || t.external_url) row.dataset.spotifyId = t.id || t.external_url;

      const explicit = t.explicit ? `<span class="explicit-badge">E</span>` : '';
      const coverUrl = t.cover_url || t.cover || t.image || '';
      let thumb;
      if (coverUrl) {
        // AGGIUNTO data-url QUI SOTTO
        const shown = artSized(coverUrl, 96);
        thumb = `<div class="tr-thumb" data-url="${escHtml(coverUrl)}" style="background-image:url('${encodeURI(shown)}')">
                   <img src="${escHtml(shown)}" alt="cover" loading="lazy" decoding="async" data-onerror="this.parentElement.innerHTML='🎵';console.warn('cover load failed', this.src)">
                 </div>`;
      } else {
        thumb = `<div class="tr-thumb">🎵</div>`;
      }
      if (!coverUrl) console.debug('renderTracks: missing cover for', globalIndex, t);

      const dur = formatDuration(t.duration_ms);
      const playcountValue = t.plays ?? t.playcount ?? t.playCount ?? t.plays_count;
      const playcount = playcountValue ? String(playcountValue).replace(/\B(?=(\d{3})+(?!\d))/g, ',') : '—';
      const albumName = t.album || t.album_name || t.release || t.release_name || '';
      const playcountCell = isPlaylist ? escHtml(albumName || '—') : playcount;
      let previewUrl = t.preview_url || '';
      
      // If it is not present, check whether it is a property of the track object
      if (!previewUrl && t.previewUrl) previewUrl = t.previewUrl;
      
      // Lazy Loading: the button is always enabled, but it will fetch the preview on click if necessary
      const previewBtn = `<button class="ta-btn ta-preview" data-preview-index="${globalIndex}" data-track-id="${t.id || ''}" data-tip="Play Preview" data-onclick="playPreview(${globalIndex})">
             <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="5 3 19 12 5 21 5 3"/></svg>
           </button>`;

      const extUrl  = t.external_url || t.externalUrl || t.link || t.url || '';
      const linkBtn = extUrl
        ? `<button class="ta-btn ta-link" data-tip="Open in Spotify" data-onclick="openExternal(${globalIndex})">
             <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
           </button>`
        : `<button class="ta-btn" data-tip="No link" disabled style="opacity:.3;">
             <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
           </button>`;

      row.innerHTML = `
        <div class="tr-check"><input type="checkbox" class="track-cb" value="${globalIndex}" checked data-onchange="onCheckChange()"></div>
        <div class="tr-num">${globalIndex + 1}</div>
        <div class="tr-title-cell">
          ${thumb}
          <div class="tr-info">
            <div class="tr-name">${escHtml(t.title || t.name || '?')} ${explicit}</div>
            <div class="tr-artist">${artistsCreditHtml(t.artists_data, t.artists || t.artist || '', t.artist_url)}</div>
          </div>
        </div>
        <div class="tr-playcount">${playcountCell}</div>
        <div class="tr-dur">${dur}</div>
        <div class="tr-actions">
          <button class="ta-btn dl" data-tip="Download" data-onclick="downloadSingle(${globalIndex})">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
          </button>
          <button class="ta-btn ta-lyrics" data-tip="Save Lyrics (.lrc)" data-onclick="downloadLyrics(${globalIndex})">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>
          </button>
          ${previewBtn}
          <button class="ta-btn ta-cover" data-tip="Save Cover (.jpg)" data-onclick="downloadCover(${globalIndex})">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>
          </button>
          ${linkBtn}
        </div>
      `;
      

      
      fragment.appendChild(row);
    }

    container.appendChild(fragment);
    setTrackRenderStatus(`Rendering ${Math.min(index, pageTrackS.length)}/${pageTrackS.length} tracks…`, index < pageTrackS.length && !quietRender);

    if (index < pageTrackS.length) {
      if (window.requestIdleCallback) {
        requestIdleCallback(renderBatch, { timeout: 200 });
      } else {
        requestAnimationFrame(renderBatch);
      }
    } else {
      setTrackRenderStatus('', false);
      container.style.minHeight = '';
      updateAlbumMeta(tracks.length);
      // If this is an artist page, inject the album section above the tracks
      const urlLower = (currentUrl || '').toLowerCase();
      const isArtist = urlLower.includes('/artist/') || urlLower.includes('spotify:artist:') || urlLower.includes('/browse/artist');
      document.getElementById('artist-tabs-section')?.remove();
      if (isArtist) injectArtistTabs(tracks);
      const isTrackUrl = isSongUrl(urlLower);
      if (isTrackUrl && tracks.length === 1) {
        $('track-controls').classList.add('hidden');
        $('track-table-wrap').classList.add('hidden');
        showSingleTrackCard(tracks[0]);
      } else {
        $('track-controls').classList.remove('hidden');
        $('track-table-wrap').classList.remove('hidden');
        // An artist's page opens on its albums, like a music app's, not on a
        // table of every track; the tab chosen since then is kept.
        const overview = document.querySelector('.list-container')?.classList.contains('artist-overview-mode');
        if (isArtist && !overview && $('artist-panel-albums')?.children.length) {
          switchArtistTab(artistTabChoice.url === currentUrl ? artistTabChoice.tab : 'albums');
        }
        // showSingleTrackCard()'s quality/duration chip row, if the previous
        // fetch on this same card was a single track — removing it here
        // (same render pass that decides "not a single track this time")
        // instead of from setAlbumCard avoids a race: setAlbumCard and
        // renderTracks are driven by two separate backend callbacks
        // (app_set_metadata / showTracklist) that aren't guaranteed to run
        // in a fixed order, so a removal in setAlbumCard could fire *after*
        // showSingleTrackCard had already (re)built the row for the fetch
        // that was actually meant to show it — silently deleting it again.
        document.getElementById('track-quality-row')?.remove();
      }
      $('recent-wrap').style.display = 'none';
      
      // Show/hide pagination
      updatePaginationControls(totalPages);
      restoreSortAnchor();
    }
  };

  renderBatch();

onCheckChange();
}

function updatePaginationControls(totalPages) {
  const pagetionDiv = $('pagetion-controls');
  if (totalPages > 1) {
    pagetionDiv.classList.remove('hidden');
    pagetionDiv.style.display = 'flex';
    
    const jump = $('page-jump');
    if (jump) {
      jump.max = String(totalPages);
      if (document.activeElement !== jump) jump.value = String(currentPage);
    }
    $('page-total').textContent = `of ${totalPages} (${TRACKS_PER_PAGE} per page)`;
    
    $('page-prev').disabled = currentPage === 1;
    $('page-next').disabled = currentPage === totalPages;
  } else {
    pagetionDiv.classList.add('hidden');
    pagetionDiv.style.display = 'none';
  }
}

// The page a typed value asks for, kept within 1..total; null when it is
// not a number at all.
function clampPage(value, total) {
  const n = Math.round(Number(String(value ?? '').trim()));
  if (!String(value ?? '').trim() || !Number.isFinite(n)) return null;
  return Math.min(Math.max(1, n), Math.max(1, total));
}

// Shows page `value` of the track list (typed in the page field, or from
// Previous / Next). Something that is not a page puts the field back.
function goToPage(value) {
  const totalPages = Math.max(1, Math.ceil(currentTracks.length / TRACKS_PER_PAGE));
  const page = clampPage(value, totalPages);
  const jump = $('page-jump');
  if (page === null || page === currentPage) {
    if (jump) jump.value = String(currentPage);
    return;
  }
  currentPage = page;
  renderTracks(currentTracks, currentPage);
  $('track-table-wrap').scrollTop = 0;
  if (jump) jump.value = String(currentPage);
}

function previousPage() { goToPage(currentPage - 1); }

function nextPage() { goToPage(currentPage + 1); }

// ── Action button feedback helper ─────────────────────────────────────────────
const _SPIN_SVG  = `<svg class="ta-spin" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><circle cx="12" cy="12" r="9" stroke-opacity=".25"/><path d="M12 3a9 9 0 0 1 9 9"/></svg>`;
const _CHECK_SVG = `<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>`;
const _X_SVG     = `<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>`;

function setTaBtnState(btn, state) {
  if (!btn) return;
  btn.classList.remove('ta-loading', 'ta-state-success', 'ta-state-error');
  if (state === 'loading') {
    btn._savedInner = btn.innerHTML;
    btn.classList.add('ta-loading');
    btn.innerHTML = _SPIN_SVG;
  } else if (state === 'success') {
    btn.classList.add('ta-state-success');
    btn.innerHTML = _CHECK_SVG;
  } else if (state === 'error') {
    btn.classList.add('ta-state-error');
    btn.innerHTML = _X_SVG;
  } else {
    // restore default
    if (btn._savedInner) { btn.innerHTML = btn._savedInner; btn._savedInner = null; }
  }
}

function resetTaBtnAfter(btn, ms) {
  // Cancel any previous timer for this button
  if (btn._resetTimer) {
    clearTimeout(btn._resetTimer);
  }
  // Schedule new timer and store the ID on the button element
  btn._resetTimer = setTimeout(() => {
    setTaBtnState(btn, 'default');
    btn._resetTimer = null;
  }, ms);
}

// ── Track actions ─────────────────────────────────────────────────────────────
function openExternal(i) {
  const t   = currentTracks[i];
  const url = t?.external_url;
  if (!url) { logMessage('No external URL for this track', 'warn'); return; }
  if (window.pywebview?.api) window.pywebview.api.open_url(url);
  else window.open(url, '_blank');
}

function downloadLyrics(i) {
  const t = currentTracks[i];
  if (!t) return;
  
  // Select both the hidden table button and the visible card button
  const btns = document.querySelectorAll(`#track-row-${i} .ta-btn.ta-lyrics, .ta-lyrics[data-track-index="${i}"]`);
  btns.forEach(btn => setTaBtnState(btn, 'loading'));
  logMessage(`Fetching lyrics: ${t.title}…`, 'info');
  
  if (window.pywebview?.api) {
    Promise.resolve(window.pywebview.api.download_track_lyrics(t))
      .then(() => { btns.forEach(btn => { setTaBtnState(btn, 'success'); resetTaBtnAfter(btn, 2200); }); })
      .catch(() => { btns.forEach(btn => { setTaBtnState(btn, 'error'); resetTaBtnAfter(btn, 2200); }); });
  } else {
    logMessage('Python not connected — demo mode', 'warn');
    setTimeout(() => { btns.forEach(btn => { setTaBtnState(btn, 'success'); resetTaBtnAfter(btn, 2200); }); }, 700);
  }
}

function downloadCover(i) {
  const t = currentTracks[i];
  if (!t || !t.id) return;
  
  // Select both the hidden table button and the visible card button
  const btns = document.querySelectorAll(`#track-row-${i} .ta-btn.ta-cover, .ta-cover[data-track-index="${i}"]`);
  
  // If already loading, ignore further clicks
  if (btns[0] && btns[0].classList.contains('ta-loading')) return;

  // Cancel any previously scheduled reset timers before setting loading state
  btns.forEach(btn => {
    if (btn._resetTimer) {
      clearTimeout(btn._resetTimer);
      btn._resetTimer = null;
    }
  });

  btns.forEach(btn => setTaBtnState(btn, 'loading'));
  logMessage(`Fetching cover: ${t.title}…`, 'info');
  
  if (window.pywebview?.api) {
    // Only starts the process. The 'success'/'error' state gets set
    // by the app_cover_download_finished listener below.
    window.pywebview.api.download_track_cover(t).catch((err) => {
        btns.forEach(btn => { setTaBtnState(btn, 'error'); resetTaBtnAfter(btn, 2200); });
        logMessage('Error starting cover download: ' + err, 'error');
    });
  } else {
    logMessage('Python not connected — demo mode', 'warn');
    setTimeout(() => { btns.forEach(btn => { setTaBtnState(btn, 'success'); resetTaBtnAfter(btn, 2200); }); }, 1500);
  }
}

// ── In ascolto per il completamento REALE dal backend ──
window.app_cover_download_finished = function(payload) {
  const trackId = payload.id;
  const success = payload.success;
  
  const idx = currentTracks.findIndex(t => t.id === trackId);
  if (idx === -1) return;
  
  const btns = document.querySelectorAll(`#track-row-${idx} .ta-btn.ta-cover, .ta-cover[data-track-index="${idx}"]`);
  btns.forEach(btn => {
      setTaBtnState(btn, success ? 'success' : 'error');
      resetTaBtnAfter(btn, 2200);
  });
};

function downloadAlbumCover(btn, imageUrl, title = 'album', artist = 'Unknown', owner = '') {
  const itemType = currentItemType || 'ALBUM';
  const displayName = itemType === 'PLAYLIST' ? owner || title : artist;
  
  // Set loading state
  btn.classList.add('loading');
  btn.disabled = true;
  btn.innerHTML = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>';
  
  if (window.pywebview?.api) {
    logMessage(`Downloading cover for: ${displayName}…`, 'info');
    try {
      window.pywebview.api.download_cover({
        "title": title,
        "artist": artist,
        "owner": owner,
        "cover": imageUrl,
        "type": itemType
      });
      
      // Simulate completion after 2.5 seconds
      setTimeout(() => {
        btn.classList.remove('loading');
        btn.classList.add('success');
        btn.innerHTML = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
        
        // Reset after 2 seconds
        setTimeout(() => {
          btn.classList.remove('success');
          btn.disabled = false;
          btn.innerHTML = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>';
        }, 2000);
      }, 2500);
    } catch (e) {
      btn.classList.remove('loading');
      btn.classList.add('error');
      btn.innerHTML = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
      logMessage('Error downloading cover: ' + e, 'error');
      
      // Reset after 3 seconds
      setTimeout(() => {
        btn.classList.remove('error');
        btn.disabled = false;
        btn.innerHTML = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>';
      }, 3000);
    }
  } else {
    logMessage('Download feature not available in demo mode', 'warn');
    btn.classList.remove('loading');
    btn.disabled = false;
  }
}

async function downloadAllCovers(btn) {
  if (!currentTracks.length) { logMessage('No tracks loaded.', 'warn'); return; }
  setTaBtnState(btn, 'loading');
  logMessage(`Saving covers for ${currentTracks.length} tracks…`, 'info');

  if (window.pywebview?.api) {
    try {
      await window.pywebview.api.download_all_covers(currentTracks);
      setTaBtnState(btn, 'success');
      logMessage('All covers saved.', 'ok');
    } catch (e) {
      setTaBtnState(btn, 'error');
      logMessage('Error saving covers: ' + e, 'error');
    } finally {
      resetTaBtnAfter(btn, 2500);
    }
  } else {
    // Demo mode
    let done = 0;
    for (const t of currentTracks) {
      await new Promise(r => setTimeout(r, 40));
      done++;
      logMessage(`Cover ${done}/${currentTracks.length}: ${t.title}`, 'info');
    }
    setTaBtnState(btn, 'success');
    resetTaBtnAfter(btn, 2500);
    logMessage('Demo: all covers saved.', 'ok');
  }
}

async function downloadAllLyrics(btn) {
  if (!currentTracks.length) { logMessage('No tracks loaded.', 'warn'); return; }
  setTaBtnState(btn, 'loading');
  logMessage(`Fetching lyrics for ${currentTracks.length} tracks…`, 'info');

  if (window.pywebview?.api) {
    try {
      await window.pywebview.api.download_all_lyrics(currentTracks);
      setTaBtnState(btn, 'success');
      logMessage('All lyrics saved.', 'ok');
    } catch (e) {
      setTaBtnState(btn, 'error');
      logMessage('Error saving lyrics: ' + e, 'error');
    } finally {
      resetTaBtnAfter(btn, 2500);
    }
  } else {
    // Demo mode
    let done = 0;
    for (const t of currentTracks) {
      await new Promise(r => setTimeout(r, 40));
      done++;
      logMessage(`Lyrics ${done}/${currentTracks.length}: ${t.title}`, 'info');
    }
    setTaBtnState(btn, 'success');
    resetTaBtnAfter(btn, 2500);
    logMessage('Demo: all lyrics saved.', 'ok');
  }
}

function playPreview(i) {
  const t = currentTracks[i];
  let previewUrl = t?.preview_url || t?.previewUrl || t?.preview || t?.preview_uri || t?.previewUri || '';
  
  const buttons = document.querySelectorAll(`button.ta-preview[data-preview-index="${i}"]`);
  const trackId = buttons[0]?.dataset.trackId || t?.id || '';

  if (!t || !trackId) {
    logMessage('Track ID missing', 'warn');
    return;
  }

  if (!previewAudio) {
    previewAudio = document.createElement('audio');
    previewAudio.id = 'preview-player';
    previewAudio.style.display = 'none';
    previewAudio.preload = 'none';
    document.body.appendChild(previewAudio);

    previewAudio.addEventListener('ended', () => {
      stopCurrentPreview();
    });
  }
  // The element is created lazily on the first preview, so the saved volume
  // has to be applied here as well as in applyPreviewVolume() — otherwise the
  // first clip of a session always plays at full volume whatever the setting.
  previewAudio.volume = previewVolume / 100;

  // Toggle pause if already playing this track
  if (previewPlayingIndex === i && !previewAudio.paused) {
    stopCurrentPreview(); 
    return;
  }

  // Stop previous track
  if (previewPlayingIndex !== -1 && previewPlayingIndex !== i) {
    stopCurrentPreview(); 
  }

  // Show spinner while loading on all matched buttons
  buttons.forEach(b => setTaBtnState(b, 'loading'));

  if (!previewUrl) {
    console.log(`Fetching preview for track ${trackId}…`);
    pywebview.api.get_track_preview(trackId).then((url) => {
      if (url) {
        previewUrl = url;
        t.preview_url = url; 
        playPreviewWithUrl(i, previewUrl, buttons, t);
      } else {
        buttons.forEach(b => setTaBtnState(b, 'error'));
        setTimeout(() => buttons.forEach(b => setTaBtnState(b, 'default')), 2200);
        logMessage('No preview available for this track', 'warn');
      }
    }).catch((err) => {
      console.error('Error fetching preview:', err);
      buttons.forEach(b => setTaBtnState(b, 'error'));
      setTimeout(() => buttons.forEach(b => setTaBtnState(b, 'default')), 2200);
      logMessage('Failed to fetch preview', 'error');
    });
  } else {
    playPreviewWithUrl(i, previewUrl, buttons, t);
  }
}

function playPreviewWithUrl(i, previewUrl, buttons, t) {
  previewAudio.src = previewUrl;
  previewAudio.currentTime = 0;
  previewAudio.play().then(() => {
    buttons.forEach(b => {
      b.classList.remove('ta-loading', 'ta-state-success', 'ta-state-error');
      setPreviewButtonState(b, true);
    });
    previewPlayingIndex = i;
    logMessage(`Playing preview: ${t.title}`, 'info');
  }).catch(() => {
    buttons.forEach(b => setTaBtnState(b, 'error'));
    setTimeout(() => buttons.forEach(b => setTaBtnState(b, 'default')), 2200);
    logMessage('Preview playback failed, opening in browser…', 'warn');
    window.open(previewUrl, '_blank');
  });
}

function setPreviewButtonState(button, active) {
  if (!button) return;
  button.classList.toggle('active', active);
  
  // Check whether this is the single-card button (.act-btn) or the table button
  const isCardBtn = button.classList.contains('act-btn');
  const svgSize = isCardBtn ? "13" : "11";

  // Dynamic tooltip handling
  if (isCardBtn) {
    button.title = active ? 'Pause preview' : 'Play Preview';
  } else {
    button.dataset.tip = active ? 'Pause preview' : 'Play Preview';
  }

  // Cambio icona dinamico mantenendo le proporzioni corrette
  button.innerHTML = active
    ? `<svg width="${svgSize}" height="${svgSize}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="5" width="4" height="14"/><rect x="14" y="5" width="4" height="14"/></svg>`
    : `<svg width="${svgSize}" height="${svgSize}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="5 3 19 12 5 21 5 3"/></svg>`;
  }

// ── Effetto Typewriter per il Placeholder ──
// ── Effetto Typewriter per il Placeholder ──
const placeholderLinks = [
  // Spotify
  "open.spotify.com/track/...",
  "open.spotify.com/album/...",
  "open.spotify.com/playlist/...",
  "open.spotify.com/artist/...",
  
  // Tidal
  "https://listen.tidal.com/track/12345678",
  "https://listen.tidal.com/album/12345678",
  "https://listen.tidal.com/playlist/12345678",
  "https://listen.tidal.com/artist/12345678/discography/albums",
  
  // Apple Music
  "https://music.apple.com/us/song/track-name/12345678",
  "https://music.apple.com/us/album/album-name/12345678",
  "https://music.apple.com/us/playlist/playlist-name/pl.123456",
  "https://music.apple.com/us/artist/artist-name/12345678",
  
  // SoundCloud
  "https://soundcloud.com/artist/track-slug",
  "https://soundcloud.com/artist/sets/set-slug",
  "https://on.soundcloud.com/abcd123",
  
  // YouTube / YT Music
  "https://youtube.com/watch?v=dQw4w9WgXcQ",
  "https://youtu.be/dQw4w9WgXcQ",
  "https://music.youtube.com/playlist?list=OLAK5uy_...",
  "https://youtube.com/playlist?list=PL...",
  
  // Pandora
  "https://pandora.com/artist/artist-name/album-name/song-name/TR:12345",
  "https://pandora.app.link/abcd123"
];

const searchPlaceholderLinks = [
  "Drake", "Taylor Swift", "Latest Hits", "Techno", "Summer Vibes", "Lo-fi"
];

let phIndex = 0;
let phCharIndex = 0;
let phIsDeleting = false;
let phTimeout;

function runTypewriter() {
  const mode = $('searchMode').value;
  const input = $('urlInput');
  
  // Choose the correct array based on the current mode
  const links = (mode === 'search') ? searchPlaceholderLinks : placeholderLinks;
  const currentText = links[phIndex];

  if (phIsDeleting) {
    input.placeholder = currentText.substring(0, phCharIndex - 1);
    phCharIndex--;
  } else {
    input.placeholder = currentText.substring(0, phCharIndex + 1);
    phCharIndex++;
  }

  let typeSpeed = phIsDeleting ? 25 : 60;

  if (!phIsDeleting && phCharIndex === currentText.length) {
    typeSpeed = 2500;
    phIsDeleting = true;
  } else if (phIsDeleting && phCharIndex === 0) {
    phIsDeleting = false;
    // Scegli un indice casuale dall'array corrente
    phIndex = Math.floor(Math.random() * links.length);
    typeSpeed = 400;
  }

  phTimeout = setTimeout(runTypewriter, typeSpeed);
}

// ── Check all ────────────────────────────────────────────────────────────────
function toggleAll(cb) {
  document.querySelectorAll('.track-cb').forEach(c => c.checked = cb.checked);
  onCheckChange();
}
// Ticks exactly the given track indices and unticks the rest — the fallback
// for an album card with no album URL to open (a provider that never sent
// one): you end up with that album's tracks selected in the list, ready for
// "Download Selected", instead of a click that does nothing.
function selectOnlyTracks(indices) {
  const wanted = new Set(indices.map(Number));
  document.querySelectorAll('.track-cb').forEach(cb => {
    cb.checked = wanted.has(Number(cb.value));
  });
  onCheckChange();
  const first = document.getElementById(`track-row-${indices[0]}`);
  first?.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

function onCheckChange() {
  const checked = document.querySelectorAll('.track-cb:checked').length;
  const total   = document.querySelectorAll('.track-cb').length;
  const selectBtn = $('dl-selected-btn');

  const checkAllEl = $('check-all');
  if (checkAllEl) {
    checkAllEl.checked = total > 0 && checked === total;
    checkAllEl.indeterminate = checked > 0 && checked < total;
  }

  if (selectBtn) {
    selectBtn.style.display = checked > 0 ? 'flex' : 'none';
  }
}
// 1. Handling Ricerche Recenti nel LocalStorage
function loadRecentSearches() {
    try {
        const list = JSON.parse(localStorage.getItem('recent_searches') || '[]');
        return Array.isArray(list) ? list.filter(s => typeof s === 'string') : [];
    } catch (e) {
        return [];
    }
}

function storeRecentSearches(list) {
    try { localStorage.setItem('recent_searches', JSON.stringify(list)); } catch (e) { /* private mode */ }
}

// Searching runs as you type, so a query is kept once typing settles (or on
// Enter). Typing "tayl" then "taylor swift" keeps only the finished query:
// an entry the new one extends is dropped, case-insensitively.
function saveRecentSearch(query) {
    query = String(query || '').trim();
    if (query.length < 2) return;
    const lower = query.toLowerCase();
    let searches = loadRecentSearches().filter(s => {
        const other = s.toLowerCase();
        return other !== lower && !lower.startsWith(other);
    });
    searches.unshift(query);
    storeRecentSearches(searches.slice(0, 15));
}

function removeRecentSearch(query) {
    storeRecentSearches(loadRecentSearches().filter(s => s !== query));
    renderRecentSearches();
}

function renderRecentSearches() {
    const searches = loadRecentSearches();
    const grid = $('recent-grid');
    grid.innerHTML = '';
    // Chips that wrap, not one full-width row per search: a past query is
    // two or three words, and giving each of them a row of its own pushed
    // everything below the fold to list five words. See #recent-grid.searches
    // in css/views/home.css — the same grid is a track-artwork grid for fetches.
    grid.classList.add('searches');
    const label = $('recent-wrap').querySelector('.recent-label');
    if (label) label.textContent = 'RECENT SEARCHES';

    searches.forEach(q => {
        const chip = document.createElement('button');
        chip.type = 'button';
        chip.className = 'search-chip';
        chip.title = q;
        // Same stroke icon as the search-mode toggle, not an emoji: it sits
        // in a themed chip and has to take its colour from the theme.
        chip.innerHTML = `<span class="rc-search-icon"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.35-4.35"/></svg></span><span class="search-chip-text">${escHtml(q)}</span><span class="search-chip-remove" role="button" tabindex="0" aria-label="Remove this search" title="Remove">✕</span>`;
        chip.onclick = (e) => {
            if (e.target.closest('.search-chip-remove')) {
                e.stopPropagation();
                removeRecentSearch(q);
                return;
            }
            $('urlInput').value = q;
            $('urlInput').dispatchEvent(new Event('input'));
        };
        grid.appendChild(chip);
    });
    if (!searches.length) {
        grid.innerHTML = '<div class="search-chip-empty">Your searches will appear here.</div>';
    }
}

// What an artist's page adds below its card (tabs, Latest Release / Top
// Songs, About): it outlived the card, so a search showed the last artist's
// sections under its own results.
function clearArtistPageParts() {
  document.getElementById('artist-tabs-section')?.remove();
  document.querySelectorAll('.artist-highlights, #artist-about, #artist-overview').forEach(el => el.remove());
  setArtistOverviewMode(false);
  setArtistHero(false);
}

function toggleSearchMode() {
    clearSearchUI();
    clearTimeout(_searchDebounceTimer);
    _searchDebounceTimer = null;
    
    // Resetta le variabili del typewriter
    phIsDeleting = false;
    phCharIndex = 0;
    phIndex = 0;
    clearTimeout(phTimeout);

    const toggle = $('searchModeToggle');
    const input = $('urlInput');
    const mode = $('searchMode');
    const label = $('searchModeText');
    const fetchBtn = $('fetchBtn');

    if (mode.value === 'link') {
        mode.value = 'search';
        syncModeSeg();
        toggle.classList.add('active');
        label.textContent = 'Search';
        toggle.title = 'Switch to Fetch Mode';
        syncSearchSourceVisibility();

        fetchBtn.style.display = 'none';
        renderRecentSearches();
        
        input.placeholder = searchPlaceholderLinks[0];
        // A link left in the field is not a search: start from empty.
        if (/^(https?:|spotify:)/i.test(input.value.trim())) input.value = '';
        $('track-table-wrap')?.classList.add('hidden');
        $('track-controls')?.classList.add('hidden');
        $('album-card')?.classList.add('hidden');
        clearArtistPageParts();
    } else {
        mode.value = 'link';
        // Search words left in the field are not a link: start from empty.
        if (input.value.trim() && !/^(https?:|spotify:)/i.test(input.value.trim())) input.value = '';
        syncModeSeg();
        toggle.classList.remove('active');
        label.textContent = 'Fetch';
        toggle.title = 'Switch to Search Mode';
        syncSearchSourceVisibility();

        fetchBtn.style.display = 'inline-flex';
        
        const rl = $('recent-wrap').querySelector('.recent-label');
        if (rl) rl.textContent = 'RECENT FETCHES';
        if (window.pywebview?.api) window.pywebview.api.get_history().then(renderRecent);
        
        input.placeholder = placeholderLinks[0];
    }
    runTypewriter();
}

// ── Search source (Spotify or a catalogue extension) ─────────────────────────
function currentSearchSource() {
  return $('searchSource')?.value || 'spotify';
}

function currentSearchSourceLabel() {
  const sel = $('searchSource');
  return sel?.selectedOptions?.[0]?.textContent || 'Spotify';
}

function syncSearchSourceVisibility() {
  const sel = $('searchSource');
  if (!sel) return;
  const show = $('searchMode')?.value === 'search' && sel.options.length > 1;
  sel.classList.toggle('hidden', !show);
}

async function loadSearchSources() {
  const sel = $('searchSource');
  if (!sel || !window.pywebview?.api?.get_metadata_sources) return;
  let sources = [];
  try {
    sources = await window.pywebview.api.get_metadata_sources();
  } catch (e) {
    return;
  }
  if (!Array.isArray(sources) || !sources.length) return;
  let saved = 'spotify';
  try { saved = localStorage.getItem('searchSource') || 'spotify'; } catch (e) {}
  sel.innerHTML = '';
  sources.forEach(s => {
    const opt = document.createElement('option');
    opt.value = s.id;
    opt.textContent = s.label;
    sel.appendChild(opt);
  });
  sel.value = sources.some(s => s.id === saved) ? saved : 'spotify';
  syncSearchSourceVisibility();
  if ($('searchMode')?.value === 'search') updateSearchMode();
}

function onSearchSourceChange() {
  try { localStorage.setItem('searchSource', currentSearchSource()); } catch (e) {}
  if ($('searchMode')?.value !== 'search') return;
  $('urlInput').placeholder = `Search ${currentSearchSourceLabel()} with keywords, artist or track name…`;
  // Re-run the query already typed, against the new source.
  _lastSearchQuery = '';
  $('urlInput').dispatchEvent(new Event('input'));
}

function updateSearchMode() {
  syncModeSeg();
  const mode = $('searchMode').value;
  const input = $('urlInput');
  const toggle = $('searchModeToggle');
  const label = $('searchModeText');
  syncSearchSourceVisibility();

  if (mode === 'search') {
    // Text mode: stop the animation and set the fixed text
    clearTimeout(phTimeout);
    input.placeholder = `Search ${currentSearchSourceLabel()} with keywords, artist or track name…`;
    toggle.classList.add('active');
    label.textContent = 'Search';
    toggle.title = 'Switch to Fetch Mode';
    $('track-table-wrap')?.classList.add('hidden');
    $('track-controls')?.classList.add('hidden');
    $('album-card')?.classList.add('hidden');
  } else {
    // Link mode: reset and restart the animation
    toggle.classList.remove('active');
    label.textContent = 'Fetch';
    toggle.title = 'Switch to Search Mode';
    
    phIsDeleting = false;
    phCharIndex = 0;
    clearTimeout(phTimeout);
    runTypewriter();
  }
}

function renderCodeResults(results) {
  const container = $('track-rows');
  container.innerHTML = '';
  if (!results || results.length === 0) {
    container.innerHTML = `<div class="queue-empty">No matches found.</div>`;
    $('track-controls').classList.add('hidden');
    $('track-table-wrap').classList.remove('hidden');
    return;
  }
  results.forEach((r, idx) => {
    const row = document.createElement('div');
    row.className = 'track-row';
    row.id = `code-row-${idx}`;
    const pathHtml = `<div class="u-ff-jetbrains-mono-monospace u-c-text2 u-fs-12">${escHtml(r.path)}:${r.line}</div>`;
    const snippet = `<pre class="u-ws-pre-wrap u-m-6-0-0 u-c-text u-fs-13">${escHtml(r.snippet)}</pre>`;
    row.innerHTML = `
      <div class="u-p-10-12 u-grid-column-1-n1">
        ${pathHtml}
        ${snippet}
      </div>
    `;
    container.appendChild(row);
  });
  // hide album/track UI and show the code results area
  $('track-controls').classList.add('hidden');
  $('track-table-wrap').classList.remove('hidden');
}

window.app_handle_provider_search_results = function(results) {
  // An answer to a search that has since been replaced — typed over, its
  // source switched, its box cleared — must not paint over the newer one.
  if (!isCurrentSearchRequest(results)) return;
  const isSearchMode = $('searchMode')?.value === 'search';
  if (!isSearchMode) {
    return;
  }
  // The results are the answer: take down the "fetching metadata…" notice
  // onFetch() raised and unlock the bar and the recents. This branch used
  // to clear only isFetchingData and the Fetch button, so the notice stayed
  // in the corner over the results until the next fetch replaced it.
  setFetchingState('hide');

  // ── Build data ────────────────────────────────────────────────────────────
  const allItems = [
    ...(results.tracks   || []).map(i => ({ ...i, _kind: 'track' })),
    ...(results.albums   || []).map(i => ({ ...i, _kind: 'album' })),
    ...(results.artists  || []).map(i => ({ ...i, _kind: 'artist' })),
    ...(results.playlists|| []).map(i => ({ ...i, _kind: 'playlist' })),
  ];
  const counts = {
    all:      allItems.length,
    track:    (results.tracks    || []).length,
    album:    (results.albums    || []).length,
    artist:   (results.artists   || []).length,
    playlist: (results.playlists || []).length,
  };

  // ── State ─────────────────────────────────────────────────────────────────
  let activeTab = 'track';
  let filterVal = '';

  // ── Helpers ───────────────────────────────────────────────────────────────
  function fmtMs(ms) {
    if (!ms) return '';
    const s = Math.round(ms / 1000);
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
  }

  function defaultIcon(kind) {
    return kind === 'artist' ? '👤' : kind === 'album' ? '💿' : kind === 'playlist' ? '📋' : '🎵';
  }

  function resolveSearchImage(value) {
    if (!value) return '';
    if (typeof value === 'string') return value;
    if (Array.isArray(value)) {
      for (const entry of value) {
        const resolved = resolveSearchImage(entry);
        if (resolved) return resolved;
      }
      return '';
    }
    if (typeof value === 'object') {
      return value.url || value.src || value.href || '';
    }
    return '';
  }

  function makeItemHTML(item) {
    const url  = item.external_url || item.external_urls || '';
    const img  = artSized(resolveSearchImage(item.cover_url || item.cover || item.image || item.images), 160);
    const name = escHtml(item.name || item.title || '');
    const meta = escHtml(item.artists || item.artist || item.owner || '');
    const dur  = item._kind === 'track' ? fmtMs(item.duration_ms) : '';
    const typeLabel = item._kind === 'artist' ? 'Artist' : '';
    const thumbClass = item._kind === 'artist' ? 'search-result-thumbnail artist-thumb' : 'search-result-thumbnail';
    // Debug log per gli artisti
    if (item._kind === 'artist') {
      console.log('[Artist] Name:', name, 'cover_url:', item.cover_url, 'images:', item.images, 'cover:', item.cover, 'image:', item.image, 'full item:', item);
    }
    const row = `
      <div class="search-result-item" role="button" tabindex="0" data-activate data-url="${escHtml(url)}" data-name="${name}|${meta}">
        <div class="${thumbClass}">
          ${img ? `<img src="${escHtml(img)}" alt="" data-onerror="this.parentElement.innerHTML='${defaultIcon(item._kind)}'">` : defaultIcon(item._kind)}
        </div>
        <div class="search-result-info">
          <div class="search-result-title">${name}</div>
          ${typeLabel ? `<div class="search-result-meta">${typeLabel}</div>` : ''}
          ${meta && typeLabel ? `<div class="search-result-meta">${meta}</div>` : meta ? `<div class="search-result-meta">${meta}</div>` : ''}
        </div>
        ${dur ? `<span class="sr-duration">${dur}</span>` : ''}
      </div>`;
    if (item._kind !== 'playlist' || !url) return row;
    return `
      <div class="search-result-playlist-row">
        ${row}
        <button type="button" class="act-btn secondary sr-import-playlist"
                data-import-search-playlist data-url="${escHtml(url)}" data-name="${name}">
          Import playlist
        </button>
      </div>`;
  }

  // ── Render ────────────────────────────────────────────────────────────────
  const container = $('text-search-results');
  container.innerHTML = '';

  const panel = document.createElement('div');
  panel.className = 'sr-panel';

  // Tab bar
  const tabBar = document.createElement('div');
  tabBar.className = 'sr-tab-bar';
  const tabs = [
    { id: 'track',    label: 'Tracks' },
    { id: 'album',    label: 'Albums' },
    { id: 'artist',   label: 'Artists' },
    { id: 'playlist', label: 'Playlists' },
  ];
  tabs.forEach(t => {
    const btn = document.createElement('button');
    btn.className = 'sr-tab' + (t.id === activeTab ? ' active' : '');
    btn.dataset.tab = t.id;
    btn.innerHTML = `${t.label}<span class="sr-tab-badge">${counts[t.id]}</span>`;
    btn.onclick = () => {
      activeTab = t.id;
      tabBar.querySelectorAll('.sr-tab').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      renderList();
    };
    tabBar.appendChild(btn);
  });
  panel.appendChild(tabBar);

  // Filter row
  const filterRow = document.createElement('div');
  filterRow.className = 'sr-filter-row';
  const filterInp = document.createElement('input');
  filterInp.className = 'sr-filter-input';
  filterInp.placeholder = 'Filter results…';
  filterInp.oninput = () => { filterVal = filterInp.value.toLowerCase(); renderList(); };
  filterRow.appendChild(filterInp);
  panel.appendChild(filterRow);

  // Content area
  const contentArea = document.createElement('div');
  contentArea.className = 'sr-tab-content';
  panel.appendChild(contentArea);

  container.appendChild(panel);

  function renderList() {
    let items = activeTab === 'all' ? allItems : allItems.filter(i => i._kind === activeTab);
    if (filterVal) {
      items = items.filter(i => {
        const name = (i.name || i.title || '').toLowerCase();
        const meta = (i.artists || i.artist || i.owner || '').toLowerCase();
        return name.includes(filterVal) || meta.includes(filterVal);
      });
    }
    if (!items.length) {
      contentArea.innerHTML = `<div class="search-result-empty">No results found.</div>`;
      return;
    }
    contentArea.innerHTML = items.slice(0, 100).map(makeItemHTML).join('');
    // Attach click → auto-switch to fetch mode + load
    contentArea.querySelectorAll('.search-result-item').forEach(el => {
      el.onclick = () => onSearchResultClick(el.dataset.url);
    });
    contentArea.querySelectorAll('[data-import-search-playlist]').forEach(button => {
      button.onclick = event => {
        event.preventDefault();
        event.stopPropagation();
        importPlayerPlaylistSearchResult(button);
      };
    });
  }

  renderList();

  $('text-search-container').classList.remove('hidden');
  $('track-table-wrap').classList.add('hidden');
};

// Click on a search result: switch to link mode, populate URL, fetch
function onSearchResultClick(url) {
  if (!url) return;
  // Switch to link mode if still in search mode
  const hiddenMode = $('searchMode');
  if (hiddenMode && hiddenMode.value === 'search') {
    hiddenMode.value = 'link';
    updateSearchMode();
  }
  $('urlInput').value = url;
  onFetch();
}

async function importPlayerPlaylistSearchResult(button) {
  const url = button.dataset.url;
  button.disabled = true;
  try {
    const result = await window.pywebview?.api?.fetch_player_playlist_search_import?.(url);
    if (!result || result.status !== 'ok') {
      toastMgr.error(result?.error || 'Could not fetch the playlist.');
      return;
    }
    await importPlaylistCollection({
      name: result.name || button.dataset.name || '',
      description: result.description || '',
      cover: result.cover || '',
      tracks: result.tracks || [],
      sourceUrl: url,
    });
  } catch (e) {
    toastMgr.error(e?.message || 'Could not fetch the playlist.');
  } finally {
    button.disabled = false;
  }
}

// "Import playlist" beside Download All, on a playlist opened from a link
// or a search: the Player gets the same name, description, cover and every
// song. Songs not in the library are kept and labelled "Not downloaded".
function renderImportPlaylistButton() {
  renderSaveM3uButton();
  document.getElementById('import-playlist-btn')?.remove();
  const actions = $('album-actions');
  if (!actions || detectUrlType(currentUrl) !== 'playlist') return;
  const btn = document.createElement('button');
  btn.id = 'import-playlist-btn';
  btn.type = 'button';
  btn.className = 'act-btn secondary';
  // data-tip is the hover bubble (the round header buttons have no visible
  // label); the longer wording stays as the accessible name.
  btn.setAttribute('aria-label', 'Copy this playlist into the Player: cover, description and every song');
  btn.dataset.tip = 'Import into the Player';
  btn.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15V6"/><path d="M18.5 18a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5z"/><path d="M12 12H3"/><path d="M16 6H3"/><path d="M12 18H3"/></svg> Import playlist`;
  btn.onclick = () => importFetchedPlaylist(btn);
  const cli = actions.querySelector('[data-onclick*="CliCommand"], .cli-btn');
  cli ? actions.insertBefore(btn, cli) : actions.appendChild(btn);
}

// "Save .m3u8" beside Download All, on a playlist: rebuilds the playlist file
// (deleted by mistake, or never written) from the tracks already in the
// download folder, where a download would have put it — nothing is fetched.
function renderSaveM3uButton() {
  document.getElementById('save-m3u-btn')?.remove();
  const actions = $('album-actions');
  if (!actions || detectUrlType(currentUrl) !== 'playlist') return;
  const btn = document.createElement('button');
  btn.id = 'save-m3u-btn';
  btn.type = 'button';
  btn.className = 'act-btn secondary';
  btn.setAttribute('aria-label', 'Write this playlist\u2019s .m3u8 file from the tracks already downloaded');
  btn.dataset.tip = 'Save the playlist file (.m3u8)';
  btn.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="8" y1="13" x2="16" y2="13"/><line x1="8" y1="17" x2="13" y2="17"/></svg> Save .m3u8`;
  btn.onclick = () => savePlaylistM3u(btn);
  const cli = actions.querySelector('[data-onclick*="CliCommand"], .cli-btn');
  cli ? actions.insertBefore(btn, cli) : actions.appendChild(btn);
}

async function savePlaylistM3u(btn) {
  if (!currentTracks.length) {
    toastMgr.error('Wait for the playlist\u2019s tracks to load.');
    return;
  }
  if (!window.pywebview?.api?.save_playlist_m3u) return;
  setTaBtnState(btn, 'loading');
  try {
    const res = await window.pywebview.api.save_playlist_m3u(
      buildConfig(), currentTracks[0]?.list_id ?? null);
    if (res?.ok) {
      setTaBtnState(btn, 'success');
      const missing = (res.total || 0) - (res.found || 0);
      toastMgr.success(missing > 0
        ? `Playlist file saved with ${res.found} of ${res.total} tracks (${missing} not downloaded yet).`
        : `Playlist file saved with all ${res.found} tracks.`);
      logMessage(`Playlist file saved: ${res.path}`, 'ok');
    } else {
      setTaBtnState(btn, 'error');
      toastMgr.error(res?.error || 'Could not write the playlist file.');
    }
  } catch (e) {
    setTaBtnState(btn, 'error');
    toastMgr.error('Could not write the playlist file: ' + e);
  } finally {
    resetTaBtnAfter(btn, 2500);
  }
}

async function importFetchedPlaylist(button) {
  if (!currentTracks.length) {
    toastMgr.error('Wait for the playlist\u2019s tracks to load.');
    return;
  }
  button.disabled = true;
  try {
    await importPlaylistCollection({
      name: g_albumImport.title,
      description: g_albumImport.description,
      cover: g_albumImport.cover,
      tracks: currentTracks.map(t => ({
        title: t.title || t.name || '',
        artists: t.artists || t.artist || '',
        album: t.album || t.album_name || '',
        duration_ms: t.duration_ms || 0,
        isrc: t.isrc || '',
        external_url: t.external_url || t.externalUrl || t.link || '',
      })),
      sourceUrl: currentUrl,
    });
  } finally {
    button.disabled = false;
  }
}

//: Casefolded title without "(feat. …)", for matching files to songs.
function playlistMatchTitle(text) {
  return String(text || '')
    .replace(/\s*[([](?:feat|ft|with)\.?\s[^)\]]*[)\]]/gi, '')
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s]/gu, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

// Only the library files that could be one of `tracks` travel to the
// server, which makes the final choice (core matching in
// api_mixins/player_playlists.py).
function playlistLibraryCandidates(tracks) {
  const isrcs = new Set(tracks.map(t => String(t.isrc || '').toUpperCase()).filter(Boolean));
  const titles = new Set(tracks.map(t => playlistMatchTitle(t.title)).filter(Boolean));
  return lib.items
    .filter(t => (t.isrc && isrcs.has(String(t.isrc).toUpperCase())) || titles.has(playlistMatchTitle(t.title)))
    .map(t => ({ path: t.path, title: t.title, artist: t.artist, album: t.album, duration_s: t.duration_s, isrc: t.isrc || '' }));
}

// `sourceUrl` is the provider playlist: the imported copy follows it and
// gets the songs added there later (syncFollowedPlaylists).
async function importPlaylistCollection({ name, description, cover, tracks, sourceUrl }) {
  if (!tracks?.length) {
    toastMgr.error('The playlist has no tracks to import.');
    return;
  }
  const res = await window.pywebview?.api?.import_player_playlist_collection?.(
    name || 'Imported playlist',
    description || '',
    cover || '',
    tracks,
    playlistLibraryCandidates(tracks),
    '',
    /^https?:\/\//i.test(sourceUrl || '') ? sourceUrl : ''
  );
  if (!res || res.status !== 'ok') {
    toastMgr.error(res?.error || 'Could not import the playlist.');
    return;
  }
  await refreshPlayerPlaylists(res.playlist.id);
  const missing = res.not_downloaded
    ? ` · ${res.not_downloaded} not downloaded`
    : '';
  toastMgr.success(`Imported "${res.playlist.name}": ${res.imported} tracks${missing}.`);
}

// Playlists imported from a provider follow it: the songs added there since
// the last check are appended (as "Not downloaded" until they are in the
// library). Checked when the Player opens and every hour after, each
// playlist at most every PLAYLIST_SYNC_EVERY_MS; `ids` forces those now.
const PLAYLIST_SYNC_EVERY_MS = 6 * 60 * 60 * 1000;
let playlistSyncBusy = false;
let playlistSyncTimer = null;
async function syncFollowedPlaylists(ids) {
  const api = window.pywebview?.api;
  if (playlistSyncBusy || !api?.sync_player_playlist) return;
  const now = Date.now();
  const due = lib.playlists.filter(p => p.source_url && (ids
    ? ids.includes(p.id)
    : now - (p.synced_at || 0) * 1000 >= PLAYLIST_SYNC_EVERY_MS));
  if (!due.length) return;
  playlistSyncBusy = true;
  let added = 0;
  let removed = 0;
  const grown = [];
  try {
    for (const playlist of due) {
      try {
        const res = await api.sync_player_playlist(playlist.id);
        if (res?.status !== 'ok') {
          if (ids) toastMgr.error(res?.error || `Could not update "${playlist.name}".`);
          continue;
        }
        if (res.added) { added += res.added; grown.push(res.playlist?.name || playlist.name); }
        removed += res.removed || 0;
      } catch (e) {
        if (ids) toastMgr.error(e?.message || `Could not update "${playlist.name}".`);
      }
    }
  } finally {
    playlistSyncBusy = false;
  }
  if (removed) toastMgr.info(`${plural(removed, 'repeated song')} removed.`);
  if (added) {
    await refreshPlayerPlaylists(lib.playlist || undefined);
    linkPlaylistTracksToLibrary();
    toastMgr.success(grown.length === 1
      ? `"${grown[0]}": ${plural(added, 'new song')} added.`
      : `${plural(added, 'new song')} added to ${grown.length} playlists.`);
  } else if (removed) {
    await refreshPlayerPlaylists(lib.playlist || undefined);
  } else if (ids) {
    await refreshPlayerPlaylists(lib.playlist || undefined);
    toastMgr.info('The playlist is up to date.');
  }
}
function startPlaylistSync() {
  syncFollowedPlaylists();
  if (!playlistSyncTimer) playlistSyncTimer = setInterval(() => syncFollowedPlaylists(), 60 * 60 * 1000);
}

// Songs a playlist lists as "Not downloaded" that are now in the library
// are pointed at their file: same ISRC, else same title and a shared artist.
let playlistLinkBusy = false;
async function linkPlaylistTracksToLibrary() {
  if (playlistLinkBusy || !lib.items.length || !lib.playlists.length) return;
  const remote = lib.playlists.flatMap(p => p.tracks.filter(t => t.downloaded === false));
  if (!remote.length) return;
  const byIsrc = new Map();
  const byTitle = new Map();
  for (const t of lib.items) {
    if (t.isrc) byIsrc.set(String(t.isrc).toUpperCase(), t);
    const key = playlistMatchTitle(t.title);
    if (!byTitle.has(key)) byTitle.set(key, []);
    byTitle.get(key).push(t);
  }
  const names = s => new Set(String(s || '').split(/\s*(?:,|&|;|\/|feat\.?|ft\.?)\s*/i).map(playlistMatchTitle).filter(Boolean));
  const links = [];
  const seen = new Set();
  for (const t of remote) {
    if (seen.has(t.path)) continue;
    seen.add(t.path);
    let hit = t.isrc ? byIsrc.get(String(t.isrc).toUpperCase()) : null;
    if (!hit) {
      const want = names(t.artist);
      hit = (byTitle.get(playlistMatchTitle(t.title)) || []).find(c => {
        const have = names(c.artist);
        return (!want.size || [...want].some(n => have.has(n)))
          && (!t.duration_s || !c.duration_s || Math.abs(c.duration_s - t.duration_s) <= 10);
      });
    }
    if (hit) links.push({ key: t.path, track: { path: hit.path, title: hit.title, artist: hit.artist, album: hit.album, duration_s: hit.duration_s, isrc: hit.isrc || '' } });
  }
  if (!links.length) return;
  playlistLinkBusy = true;
  try {
    const res = await window.pywebview?.api?.link_player_playlist_tracks?.(links);
    if (res?.status === 'ok' && res.linked) await refreshPlayerPlaylists(lib.playlist || undefined);
  } catch (e) {
    console.warn('[playlists] could not link downloaded tracks:', e);
  } finally {
    playlistLinkBusy = false;
  }
}

// ── Error states ─────────────────────────────────────────────────────────────
// Turns a backend error into something a person can act on. The commonest one
// on a fresh install is a missing extension ("The optional utilities extension
// is not installed; '…' requires 'utilities.sflx' …"): that is not a network
// problem, and saying "check your connection" sent people the wrong way.
function describeError(message, what) {
  const text = String(message || '').trim();
  const missing = text.match(/optional (\S+) extension is not installed/i)
    || text.match(/requires '([\w-]+)\./i);
  if (missing || /no (provider|extension)s? (is |are )?installed/i.test(text)) {
    const name = missing ? missing[1] : '';
    const pretty = name ? name.charAt(0).toUpperCase() + name.slice(1) : '';
    return {
      kind: 'setup',
      title: pretty ? `${pretty} extension needed` : 'No extension installed',
      body: `${what} runs through extensions, and ${pretty ? `the ${pretty} one` : 'none'} is installed yet. `
        + 'Add one from Settings → Extensions, then try again.',
      action: { label: 'Open Extensions', onclick: 'openExtensionsSettings()' },
      detail: text,
    };
  }
  if (/does not implement \w+\(\)|No method: /i.test(text)) {
    return {
      kind: 'error',
      title: `${what} is not available here`,
      body: 'The extension behind this source has no such feature. Pick another source, or update the extension from Settings → Extensions.',
      action: { label: 'Open Extensions', onclick: 'openExtensionsSettings()' },
      detail: text,
    };
  }
  if (/timed? ?out|network|connection|ENOTFOUND|getaddrinfo|unreachable/i.test(text)) {
    return {
      kind: 'network',
      title: 'Could not reach the service',
      body: `${what} needs a connection, and the request did not get through. Check that you are online, then retry.`,
      detail: text,
    };
  }
  return {
    kind: 'error',
    title: `${what} failed`,
    body: text ? 'The provider answered with an error. The details are below and in Logs.' : 'Something went wrong. The details are in Logs.',
    detail: text,
  };
}

function errorStateHTML(info, retry) {
  const icon = info.kind === 'setup'
    ? '<path d="M12 2v4"/><path d="M12 18v4"/><path d="m4.93 4.93 2.83 2.83"/><path d="m16.24 16.24 2.83 2.83"/><path d="M2 12h4"/><path d="M18 12h4"/><circle cx="12" cy="12" r="3"/>'
    : info.kind === 'network'
      ? '<path d="M5 12.55a11 11 0 0 1 14.08 0"/><path d="M1.42 9a16 16 0 0 1 21.16 0"/><path d="M8.53 16.11a6 6 0 0 1 6.95 0"/><line x1="12" y1="20" x2="12.01" y2="20"/><line x1="2" y1="2" x2="22" y2="22"/>'
      : '<circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>';
  const buttons = [
    info.action ? `<button type="button" class="act-btn primary" data-onclick="${info.action.onclick}">${escHtml(info.action.label)}</button>` : '',
    retry ? `<button type="button" class="act-btn secondary" data-onclick="${retry}">Try again</button>` : '',
  ].join('');
  return `<div class="error-state ${info.kind}" role="alert">
    <div class="es-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${icon}</svg></div>
    <div class="es-title">${escHtml(info.title)}</div>
    <p class="es-body">${escHtml(info.body)}</p>
    ${buttons ? `<div class="es-actions">${buttons}</div>` : ''}
    ${info.detail ? `<details class="es-detail"><summary>Technical details</summary><code>${escHtml(info.detail)}</code></details>` : ''}
  </div>`;
}

function openExtensionsSettings() {
  switchView('settings');
  const btn = [...document.querySelectorAll('.stab')].find(b => b.textContent.trim() === 'Extensions');
  if (btn) switchTab('extensions', btn);
}

window.app_handle_provider_search_error = function(message) {
  if (message && typeof message === 'object') {
    if (!isCurrentSearchRequest(message)) return;
    message = message.message;
  }
  clearSearchUI();
  // 'hide', not 'error': the card below says what happened, and the generic
  // "Unable to retrieve data" toast on top of it said the same thing less well.
  setFetchingState('hide');
  const info = describeError(message, 'Search');
  $('track-rows').innerHTML = errorStateHTML(info, info.kind === 'setup' ? '' : 'onFetch()');
  $('track-controls').classList.add('hidden');
  $('track-table-wrap').classList.remove('hidden');
  // One error, one place (redesign §8): the card says it, the status line
  // under the search bar no longer repeats its title.
  setStatus('', false);
  // Quiet: into the Logs panel, without a second toast for the same failure.
  logMessage(`Provider search error: ${message}`, 'error-quiet');
  $('urlInput').disabled = false;
  $('fetchBtn').disabled = false;
};

function filterTracks() {
  const q = $('trackSearch').value.toLowerCase();
  document.querySelectorAll('.track-row').forEach(row => {
    const title  = row.querySelector('.tr-name')?.textContent?.toLowerCase()   || '';
    const artist = row.querySelector('.tr-artist')?.textContent?.toLowerCase() || '';
    row.style.display = (!q || title.includes(q) || artist.includes(q)) ? '' : 'none';
  });
}
function reverseTracks() {
  const c = $('track-rows'); const rows = [...c.children];
  rows.reverse().forEach(r => c.appendChild(r));
}
// The nearest ancestor that actually scrolls (the view, or the page).
function scrollParentOf(el) {
  for (let n = el?.parentElement; n; n = n.parentElement) {
    const oy = getComputedStyle(n).overflowY;
    if ((oy === 'auto' || oy === 'scroll') && n.scrollHeight > n.clientHeight) return n;
  }
  return document.scrollingElement || document.documentElement;
}

// Re-sorting rebuilds the table and the artist section above it. Without
// this the page shrinks for a moment, the browser clamps the scroll, and
// the list jumps: the sort box is put back where it was on screen.
let g_sortAnchor = null;
function sortTracks() {
  const box = $('sort-select');
  g_sortAnchor = box ? { el: box, top: box.getBoundingClientRect().top, scroller: scrollParentOf(box) } : null;
  _sortTracks();
}
function restoreSortAnchor() {
  const a = g_sortAnchor;
  g_sortAnchor = null;
  if (!a || !a.el.isConnected) return;
  const delta = a.el.getBoundingClientRect().top - a.top;
  if (Math.abs(delta) > 0.5) a.scroller.scrollTop += delta;
}
function _sortTracks() {
  const val = $('sort-select').value;
  const sorted = [...currentTracks]; // Always works on a copy
  
  // Restore the array using the hidden index saved previously
  if (val === 'default') { 
    sorted.sort((a, b) => a._originalIndex - b._originalIndex);
    renderTracks(sorted, 1); 
    return; 
  }
  
  const pc = t => parseInt(t.plays ?? t.playcount ?? t.playCount ?? t.plays_count ?? '0', 10) || 0;
  if (val === 'title_asc')     sorted.sort((a, b) => (a.title || '').localeCompare(b.title || ''));
  if (val === 'title_desc')    sorted.sort((a, b) => (b.title || '').localeCompare(a.title || ''));
  if (val === 'artist_asc')    sorted.sort((a, b) => (a.artist || a.artists || '').localeCompare(b.artist || b.artists || ''));
  if (val === 'artist_desc')   sorted.sort((a, b) => (b.artist || b.artists || '').localeCompare(a.artist || a.artists || ''));
  if (val === 'duration_asc')  sorted.sort((a, b) => (a.duration_ms || 0) - (b.duration_ms || 0));
  if (val === 'duration_desc') sorted.sort((a, b) => (b.duration_ms || 0) - (a.duration_ms || 0));
  if (val === 'plays_asc')     sorted.sort((a, b) => pc(a) - pc(b));
  if (val === 'plays_desc')    sorted.sort((a, b) => pc(b) - pc(a));
  renderTracks(sorted, 1);
}

// ── Recent fetches ────────────────────────────────────────────────────────────
function detectUrlType(url) {
  if (!url) return '';
  const u = url.toLowerCase();
  if (isSongUrl(u)) return 'track';
  if (u.includes('spotify:album:') || u.includes('/album/') || (u.includes('playlist') && u.includes('olak5uy_'))) return 'album';
  if (u.includes('spotify:playlist:') || u.includes('/playlist/') || (u.includes('list=') && !u.includes('olak5uy_'))) return 'playlist';
  if (u.includes('spotify:artist:') || u.includes('/artist/') || u.includes('/browse/artist')) return 'artist';
  return '';
}

// Convert legacy Spotify URIs (spotify:track:ID) and similar short URIs into web links
function normalizeHistoryUrl(url) {
  if (!url) return '';
  const u = String(url).trim();
  try {
    if (u.startsWith('spotify:')) {
      const parts = u.split(':');
      if (parts.length >= 3) {
        const type = parts[1];
        const id = parts.slice(2).join(':');
        return `https://open.spotify.com/${type}/${id}`;
      }
    }
    // If it already looks like an http(s) link, return as-is
    if (u.startsWith('http://') || u.startsWith('https://')) return u;
    // Support bare open.spotify.com/... without protocol. Match the host
    // exactly (end of string, or followed by '/', ':' or '?') so a hostile
    // value like "open.spotify.com.evil.com" isn't mistaken for the real
    // domain by a plain prefix check.
    const bareHostMatch = /^(open|play)\.spotify\.com(?:[/:?]|$)/.exec(u);
    if (bareHostMatch) return `https://${u}`;
    return u;
  } catch (e) {
    return url;
  }
}

//: The three screens that can open on nothing say so the same way.
const EMPTY_ICONS = {
  disc: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="2.5"/>',
  user: '<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
  chart: '<line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/>',
};

function emptyState(icon, title, hint) {
  return `<div class="empty-state">
    <div class="es-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
      stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${EMPTY_ICONS[icon] || EMPTY_ICONS.disc}</svg></div>
    <div class="es-title">${escHtml(title)}</div>
    <div class="es-hint">${escHtml(hint)}</div>
  </div>`;
}

function renderRecent(hist) {
  const grid = $('recent-grid'); grid.innerHTML = '';
  // Artwork tiles, not the one-line rows renderRecentSearches() builds.
  grid.classList.remove('searches');
  if (!hist || !hist.length) {
    grid.innerHTML = `<div class="u-grid-column-1-n1">${emptyState(
      'disc',
      'No recent fetches yet',
      'Paste a Spotify track, album or playlist link above — what you fetch shows up here.',
    )}</div>`;
    return;
  }
  const BADGE_CFG = {
    playlist: { label:'Playlist', icon:'☰' },
    artist:   { label:'Artist',  icon:'♪' },
    album:    { label:'Album',   icon:'◎' },
    track:    { label:'Track',   icon:'♩' },
  };
  hist.slice(0, 16).forEach(item => {
    const card = document.createElement('div');
    card.className = 'recent-card';
    makeActivatable(card);
  const rawUrl = item.url || '';
  const link = normalizeHistoryUrl(rawUrl);
  card.onclick = () => {
    if (!link) return;
    $('urlInput').value = link;
    highlightRecentCard(link);
    onFetch();
  };
 
    const coverUrl = artSized(item.cover || item.cover_url || item.image || '', 240);
    const coverBg  = coverUrl ? `background-image:url('${encodeURI(coverUrl)}');` : '';
 
    const urlType = item.url_type || detectUrlType(item.url || '');
    const badge   = BADGE_CFG[urlType] || null;
 
    // Subtitle: artist name for tracks, count for everything else
    let subtitle = '';
    if (urlType === 'track') {
      subtitle = escHtml(item.artist || '');
    } else if (item.track_count > 0) {
      subtitle = `${item.track_count} tracks`;
    } else if (item.album_count > 0) {
      subtitle = `${item.album_count} albums`;
    }
 
    const badgeHtml = badge
      ? `<span class="rc-badge ${urlType}">${badge.icon} ${badge.label}</span>`
      : '';
    const subHtml = subtitle ? `<div class="rc-sub">${subtitle}</div>` : '';
 
    card.innerHTML = `
      <div class="rc-cover" style="${coverBg}">${coverUrl ? '' : '🎵'}
        <button class="rc-remove" title="Remove from history"
          data-onclick="event.stopPropagation();removeRecent(this.closest('.recent-card').dataset.url)">✕</button>
      </div>
      <div class="rc-info">
        <div class="rc-title">${escHtml(item.label || item.title || item.url || '—')}</div>
        ${subHtml}
        ${badgeHtml}
      </div>
    `;
    card.dataset.url = link || item.url || '';
    grid.appendChild(card);
  });
}

function highlightRecentCard(url) {
  document.querySelectorAll('.recent-card').forEach(card => {
    card.classList.toggle('active', card.dataset.url === url);
  });
}

async function removeRecent(url) {
  if (!url || !window.pywebview?.api) return;
  try {
    await window.pywebview.api.remove_history_item(url);
    const hist = await window.pywebview.api.get_history();
    renderRecent(hist);
  } catch (e) {
    logMessage('Could not remove history item: ' + e, 'error');
  }
}

// ── Download queue ────────────────────────────────────────────────────────────
function addToQueue(indices, { requeueFinished = false } = {}) {
  console.log('addToQueue called', { indices, currentTracksLength: currentTracks.length, queueLengthBefore: queue.length });
  let added = false;
  const skipped = [];
  indices.forEach(i => {
      const t = currentTracks[i];
      if (!t) {
        console.warn('Skipped invalid track index', i);
        return;
      }

      // Usa l'indice originale per evitare che Python scarichi la track sbagliata
      const realIndex = t._originalIndex !== undefined ? t._originalIndex : i;

      const itemId = t.id || t.external_url || `queue-${realIndex}-${Math.random().toString(16).slice(2)}`;
      const spotifyId = t.id || t.external_url || itemId;

      // Deduped on the track's own identity, never on its position: a
      // second fetch puts a *different* track at index 0, and matching on
      // the index there silently refused to download it.
      //
      // And only within the list it came from. A track a different playlist
      // already queued must not be dropped here: the backend downloads the
      // list as a playlist (own folder + M3U) only when every one of its
      // tracks is selected, so a playlist sharing songs with an earlier one
      // lost them and fell back to loose files in the download folder.
      const sameList = q => (q.source || '') === (currentUrl || '');
      const duplicate = spotifyId
        ? queue.find(q => sameList(q) && q.spotify_id === spotifyId)
        : queue.find(q => sameList(q) && q.index === realIndex && q.title === t.title);
      if (duplicate) {
        if (requeueFinished && ['done', 'skipped', 'error'].includes(duplicate.status)) {
          // A playlist rerun also repairs its M3U; the downloader skips audio
          // already in the library and fetches only tracks that are missing.
          duplicate.list = t.list_id ?? null;
          duplicate.index = realIndex;
          duplicate.title = t.title;
          duplicate.artist = t.artist || t.artists || '';
          duplicate.album = t.album || '';
          duplicate.status = 'waiting';
          duplicate.progress = 0;
          duplicate.file_path = '';
          duplicate.file_size_mb = 0;
          added = true;
          return;
        }
        console.warn('Track already in queue', spotifyId || realIndex);
        skipped.push(duplicate);
        return;
      }
      queue.push({
        id: itemId,
        spotify_id: spotifyId,
        source: currentUrl || '',
        // The tracklist `index` points into (Python numbers each one it
        // shows): the queue can hold several lists by the time it starts.
        list: t.list_id ?? null,
        index: realIndex,
        title: t.title,
        artist: t.artist || t.artists || '',
        album: t.album || '',
        status: 'waiting',
        progress: 0,
        file_path: '',
        file_size_mb: 0,
      });
    added = true;
  });
  console.log('queue state after add', { queueLengthAfter: queue.length, queue });
  renderQueue();
  const emptyMsg = $('queue-empty');
  if (emptyMsg) emptyMsg.style.display = queue.length > 0 ? 'none' : 'flex';

  // Nothing was added and something was recognised: say so. Refusing in
  // silence is what made a re-queued track look like a dead button.
  if (!added && skipped.length) {
    const one = skipped[0];
    const done = skipped.every(q => q.status === 'done');
    toastMgr.info(
      skipped.length === 1
        ? `${one.title} is already ${done ? 'downloaded' : 'in the queue'}.`
        : `Those ${skipped.length} tracks are already ${done ? 'downloaded' : 'in the queue'}.`,
    );
  }
  return added;
}

function updateQueueDuration() {
  const durationEl = $('qd-duration');
  if (!durationEl) return;
  
  if (!queueStartTime) {
    durationEl.textContent = '0s';
    return;
  }
  
  // Calculate elapsed time in seconds
  const seconds = Math.floor((Date.now() - queueStartTime) / 1000);
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  
  durationEl.textContent = m > 0 ? `${m}m ${s}s` : `${seconds}s`;
}

function resetQueueDuration() {
  queueStartTime = null;
  if (queueDurationInterval) {
    clearInterval(queueDurationInterval);
    queueDurationInterval = null;
  }
  updateQueueDuration();
}

// Mirrors each queue item's status/progress onto its row in the (still
// visible) track table — a queue-drawer-only view meant scrolling away to
// check on a download in progress. Matched by data-spotifyId first (stable
// across a re-sort, which reshuffles which #track-row-N id a track holds);
// falls back to the original pre-sort index for tracks with neither an id
// nor an external_url (the same fallback addToQueue() already accepts for
// its own dedup lookup, so this isn't a new class of mismatch).
function syncTrackRowsWithQueue() {
  const rows = document.getElementById('track-rows');
  if (!rows || !rows.children.length) return;
  // The rows are indexed once per call instead of running a document query
  // per queue item: with a few hundred tracks queued those N queries were
  // the bulk of a render, and a render happens four times a second while a
  // download is running.
  const byId = new Map();
  for (const row of rows.children) {
    const sid = row.dataset && row.dataset.spotifyId;
    if (sid) byId.set(sid, row);
  }
  queue.forEach(item => {
    let row = (item.spotify_id && byId.get(item.spotify_id)) || null;
    if (!row && item.index != null) {
      row = document.getElementById(`track-row-${item.index}`);
    }
    if (!row) return;
    row.classList.toggle('dl-active', item.status === 'active');
    row.classList.toggle('done', item.status === 'done');
    row.classList.toggle('failed', item.status === 'error');
    row.classList.toggle('skipped', item.status === 'skipped');
    const pct = item.status === 'active' ? item.progress : (item.status === 'done' ? 100 : 0);
    // Writing the custom property unconditionally invalidates the row's
    // style on every render even when the number has not moved.
    if (row._rowProgress !== pct) {
      row._rowProgress = pct;
      row.style.setProperty('--row-progress', pct + '%');
    }
  });
}

// Rendering the queue used to mean `list.innerHTML = ''` followed by one
// innerHTML parse per item, on every single state change. "Download All"
// fired one full rebuild per track (startDownloadQueue marks each waiting
// item active one at a time) and Python pushes download stats four times a
// second on top of that, so a long tracklist locked the window solid.
// Item nodes are now built once, reused, and only the fields that actually
// changed are written; renders are coalesced into at most one per frame.
const queueNodes = new Map();
let queueRenderScheduled = false;

function renderQueue() {
  if (queueRenderScheduled) return;
  queueRenderScheduled = true;
  const run = () => {
    if (!queueRenderScheduled) return;
    queueRenderScheduled = false;
    renderQueueNow();
  };
  // The frame callback is the fast path, but it never fires while the window
  // is hidden or minimised — which would leave the queue frozen mid-download
  // until it came back — so a timer races it and whichever arrives first
  // renders.
  if (typeof requestAnimationFrame === 'function') requestAnimationFrame(run);
  setTimeout(run, 120);
}

function queueItemNode(item) {
  let el = queueNodes.get(item.id);
  if (el) return el;
  el = document.createElement('div');
  el.className = 'queue-item';
  el.innerHTML = `
      <div class="qi-top">
        <div class="qi-meta">
          <div class="qi-title"></div>
          <div class="qi-artist"></div>
        </div>
        <div class="qi-pill"></div>
      </div>
      <div class="qi-bottom"></div>
      <div class="qi-bottom-meta"><span class="qi-bm-size"></span><span class="qi-bm-path"></span></div>
    `;
  el._parts = {
    title:  el.querySelector('.qi-title'),
    artist: el.querySelector('.qi-artist'),
    pill:   el.querySelector('.qi-pill'),
    bottom: el.querySelector('.qi-bottom'),
    meta:   el.querySelector('.qi-bottom-meta'),
    size:   el.querySelector('.qi-bm-size'),
    path:   el.querySelector('.qi-bm-path'),
  };
  queueNodes.set(item.id, el);
  return el;
}

function paintQueueItem(el, item, qi) {
  const id = `qi-${qi}`;
  if (el.id !== id) el.id = id;

  const artistAlbum = item.album
    ? `${item.artist || ''} • ${item.album}`
    : (item.artist || '');
  // Nothing below depends on anything outside this signature, so an item
  // whose signature is unchanged needs no DOM write at all.
  const sig = [item.status, item.progress, item.title, artistAlbum,
               item.file_path, item.file_size_mb].join('\u0000');
  if (el._sig === sig) return;
  el._sig = sig;

  const p = el._parts;
  const statusLabel = { waiting:'Queued', active:'Downloading', done:'completed', error:'Failed', skipped:'Skipped' }[item.status] || 'Queued';
  const statusText = item.status === 'active'
    ? `Downloading… ${item.progress}%`
    : item.status === 'done'
    ? 'Completed'
    : item.status === 'error'
    ? 'Failed'
    : item.status === 'skipped'
    ? 'Skipped'
    : 'Queued';

  p.title.textContent  = item.title || '';
  p.artist.textContent = artistAlbum;
  p.pill.className = `qi-pill ${item.status}`;
  p.pill.textContent = statusLabel;

  if (item.status === 'done') {
    const hasSize = item.file_size_mb > 0;
    const hasPath = !!item.file_path;
    p.size.textContent = hasSize ? `${item.file_size_mb.toFixed(2)} MB` : '';
    p.size.style.display = hasSize ? '' : 'none';
    p.path.textContent = hasPath ? item.file_path : '';
    p.path.title = hasPath ? item.file_path : '';
    p.path.style.display = hasPath ? '' : 'none';
    p.meta.style.display = (hasSize || hasPath) ? '' : 'none';
    p.bottom.style.display = 'none';
  } else {
    p.meta.style.display = 'none';
    p.bottom.style.display = '';
    if (item.status === 'active') {
      // A segmented meter in whole steps (css/views/queue.css), then the words.
      const lit = Math.round((Math.max(0, Math.min(100, Number(item.progress) || 0)) / 100) * 20);
      const steps = Array.from({ length: 20 }, (_, i) => (i < lit ? '<i class="on"></i>' : '<i></i>')).join('');
      // --qi-p: the same share as a smooth bar, for the music look.
      p.bottom.innerHTML = `<span class="qi-meter" aria-hidden="true" style="--qi-p:${lit * 5}%">${steps}</span><span></span>`;
      p.bottom.lastChild.textContent = statusText;
    } else {
      p.bottom.textContent = statusText;
    }
  }
}

function renderQueueNow() {
  const list = $('queue-list');
  if (!list) return;
  let empty = $('queue-empty');
  if (!empty) {
    empty = document.createElement('div');
    empty.id = 'queue-empty';
    empty.className = 'queue-empty';
    empty.innerHTML = `
      <div class="q-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg></div>
      <div class="q-title">Nothing downloading</div>
      <div class="q-hint">Press Download on a track, album or playlist — or Start on the queue above.</div>
    `;
  }

  const queuedCount = queue.filter(q => q.status === 'waiting').length;
  const completedCount = queue.filter(q => q.status === 'done').length;
  const skippedCount = queue.filter(q => q.status === 'skipped').length;
  const failedCount = queue.filter(q => q.status === 'error').length;

  $('q-queued').textContent = queuedCount;
  $('q-completed').textContent = completedCount;
  $('q-skipped').textContent = skippedCount;
  $('q-failed').textContent = failedCount;
  updateMusicQueueSub();

  // Which counter is currently acting as the filter, and whether the "Clear
  // filter" escape hatch is needed at all.
  const filterMap = { waiting: 'queued', done: 'completed', skipped: 'skipped', error: 'failed' };
  document.querySelectorAll('.queue-summary .qs-item').forEach(el => {
    const on = !!queueFilterStatus && el.classList.contains(filterMap[queueFilterStatus]);
    el.classList.toggle('is-filtering', on);
    el.setAttribute('aria-pressed', String(on));
  });
  $('queue-clear-filters')?.classList.toggle('hidden', !queueFilterStatus && !queueSearch);

  const dock = $('queue-dock');
  // Something in the live list: the empty staged-queue placeholder above it
  // shrinks to one line so "Downloading now" sits near the top.
  $('view-queue')?.classList.toggle('has-live', queue.length > 0);
  if (queue.length === 0) {
    queueStats = { downloaded:'0.00 MB', speed:'0.0 MB/s', eta:'' };
    if (dock) dock.classList.remove('visible');
    document.body.classList.remove('has-dock');
    $('queue-drawer')?.classList.remove('open');

    empty.style.display = 'flex';
    if (list.firstChild !== empty) list.insertBefore(empty, list.firstChild);
    while (list.children.length > 1) list.removeChild(list.lastChild);
    queueNodes.clear();
    $('q-count').textContent = '0 tracks'; $('q-done').textContent = '';
    const downloaded = $('qd-downloaded');
    const speed = $('qd-speed');
    if (downloaded) downloaded.textContent = queueStats.downloaded;
    if (speed) speed.textContent = 'Idle';
    const bar = $('qd-bar-fill');
    if (bar) bar.style.width = '0px';
    dock?.classList.remove('done');
    resetQueueDuration();
    // Queue just went empty (cleared, or its last item removed) — nothing
    // left for syncTrackRowsWithQueue() to match, so any row still carrying
    // dl-active/done/failed/skipped from before would otherwise be stuck.
    document.querySelectorAll('#track-rows .track-row').forEach(row => {
      row.classList.remove('dl-active', 'done', 'failed', 'skipped');
      row.style.removeProperty('--row-progress');
    });
    return;
  }

  empty.style.display = 'none';
  if (dock) dock.classList.add('visible');
  // Drives the bottom padding of #content (css/layout.css) under the bar.
  document.body.classList.add('has-dock');

  // Filtering happens here, not over the rendered nodes: the counts above
  // must keep describing the whole queue (that is what makes them useful as
  // filter buttons), and re-rendering is what this function does anyway.
  // 'active' counts as queued for filtering — a download in flight is one
  // you are waiting on, and having it vanish from "Queued" the moment it
  // starts is not what anyone means by the word.
  const q = (queueSearch || '').toLowerCase();
  const visible = [];
  queue.forEach((item, qi) => {
    if (queueFilterStatus) {
      const matchesStatus = queueFilterStatus === 'waiting'
        ? (item.status === 'waiting' || item.status === 'active')
        : item.status === queueFilterStatus;
      if (!matchesStatus) return;
    }
    if (q && !`${item.title || ''} ${item.artist || ''} ${item.album || ''}`.toLowerCase().includes(q)) return;
    visible.push([item, qi]);
  });

  // Reconcile in place: existing nodes are moved at most once and dropped
  // only when they leave the list, so a filter keystroke or a progress tick
  // no longer rebuilds every row from scratch.
  let cursor = 0;
  const place = (node) => {
    if (list.children[cursor] !== node) list.insertBefore(node, list.children[cursor] || null);
    cursor++;
  };

  if (!visible.length) {
    let none = list.querySelector('.queue-no-match');
    if (!none) {
      none = document.createElement('div');
      none.className = 'queue-no-match';
    }
    none.textContent = queueFilterStatus || q
      ? 'Nothing in the queue matches this filter.'
      : 'Queue is empty.';
    place(none);
  }

  visible.forEach(([item, qi]) => {
    const el = queueItemNode(item);
    paintQueueItem(el, item, qi);
    place(el);
  });

  while (list.children.length > cursor) list.removeChild(list.lastChild);

  // Drop the cached nodes of items that are no longer in the queue (removed
  // or cleared), otherwise the map would grow for the life of the session.
  if (queueNodes.size > queue.length) {
    const live = new Set(queue.map(item => item.id));
    for (const key of [...queueNodes.keys()]) if (!live.has(key)) queueNodes.delete(key);
  }
  
  $('q-count').textContent = `${queue.length} track${queue.length !== 1 ? 's' : ''}`;
  const done = queue.filter(q => q.status === 'done').length;
  $('q-done').textContent = done > 0 ? `${done} done` : '';
  
  // Sync the new Stats Bar inside the drawer
  const qsbDownloaded = $('qsb-downloaded');
  const qsbSpeed = $('qsb-speed');
  if (qsbDownloaded) qsbDownloaded.textContent = queueStats.downloaded;
  if (qsbSpeed) qsbSpeed.textContent = queueStats.speed;
  const qsbEta = $('qsb-eta');
  if (qsbEta) qsbEta.textContent = queueStats.eta || '—';

  // Sync the Dock indicators
  const downloaded = $('qd-downloaded');
  const speed = $('qd-speed');
  if (downloaded) downloaded.textContent = queueStats.downloaded;
  if (speed) {
    // A live rate only exists while a provider is reporting one. Printing
    // "0.00 MB/s" the rest of the time made a working download look stalled;
    // what is actually known then is how far through the queue we are.
    const rate = parseFloat(queueStats.speed);
    const active = queue.some(q => q.status === 'active');
    speed.textContent = rate > 0
      ? queueStats.speed + (queueStats.eta ? ` · ${queueStats.eta} left` : '')
      : active
        ? `Downloading ${done + 1} of ${queue.length}…`
        : `${done} of ${queue.length} done`;
  }
  const bar = $('qd-bar-fill');
  // The dock's meter has 24 LED steps of 8px (css/layout.css): whole steps
  // only, so a step is either lit or not.
  if (bar) bar.style.width = `${queue.length ? Math.round((done / queue.length) * 24) * 8 : 0}px`;
  dock?.classList.toggle('done', queue.length > 0 && done === queue.length);

  updateQueueDuration();
  syncTrackRowsWithQueue();
}

// The queue is a page now (Queue in the sidebar): the bottom bar and every
// old "open the drawer" call land there.
function toggleQueueDrawer() {
  switchView('queue');
  loadFailedTracks();
}

// ── Queue page: staged items ────────────────────────────────────────────────
let stagedState = { items: [], counts: {}, running: false };
let stagedKind = '';

const KIND_META = {
  track:    { label: 'Track',    icon: '<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>' },
  album:    { label: 'Album',    icon: '<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="3"/>' },
  playlist: { label: 'Playlist', icon: '<path d="M21 15V6"/><path d="M18.5 18a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5Z"/><path d="M12 12H3"/><path d="M16 6H3"/><path d="M12 18H3"/>' },
  artist:   { label: 'Artist',   icon: '<circle cx="12" cy="8" r="4"/><path d="M4 21v-1a6 6 0 0 1 6-6h4a6 6 0 0 1 6 6v1"/>' },
};
const STATUS_META = {
  pending: { label: 'Waiting',     cls: 'queued' },
  running: { label: 'Downloading', cls: 'active' },
  done:    { label: 'Done',        cls: 'done' },
  failed:  { label: 'Failed',      cls: 'failed' },
};

// "~42s" / "~3m05s" — the same format as core/progress.format_eta and
// Mornye's queue labels. Empty when there is no estimate.
function formatEta(seconds) {
  const s = Math.round(Number(seconds) || 0);
  if (s <= 0) return '';
  if (s < 60) return `~${s}s`;
  return `~${Math.floor(s / 60)}m${String(s % 60).padStart(2, '0')}s`;
}

function timeAgo(seconds) {
  const t = Number(seconds) || 0;
  if (!t) return '';
  const diff = Math.max(0, Date.now() / 1000 - t);
  if (diff < 60) return 'just now';
  if (diff < 3600) return `${Math.floor(diff / 60)} min ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} h ago`;
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)} d ago`;
  return new Date(t * 1000).toLocaleDateString();
}

function formatSize(bytes) {
  const b = Number(bytes) || 0;
  if (!b) return '';
  const units = ['B', 'KB', 'MB', 'GB'];
  let v = b, u = 0;
  while (v >= 1024 && u < units.length - 1) { v /= 1024; u++; }
  return `${v.toFixed(v >= 100 || u === 0 ? 0 : 1)} ${units[u]}`;
}

async function loadStaged() {
  if (!window.pywebview?.api?.get_staged_queue) return;
  try {
    stagedState = await window.pywebview.api.get_staged_queue() || stagedState;
  } catch (e) { /* keep what is shown */ }
  renderStaged();
}

window.app_staged_queue_changed = function (state) {
  if (state && Array.isArray(state.items)) stagedState = state;
  renderStaged();
};

function setStagedKind(kind) {
  stagedKind = kind || '';
  document.querySelectorAll('#view-queue .seg-tab').forEach(tab => {
    const on = tab.dataset.kind === stagedKind;
    tab.classList.toggle('active', on);
    tab.setAttribute('aria-selected', String(on));
  });
  renderStaged();
}

function visibleStaged() {
  const q = ($('sq-search')?.value || '').trim().toLowerCase();
  const status = $('sq-status')?.value || '';
  return (stagedState.items || []).filter(item =>
    (!stagedKind || item.kind === stagedKind)
    && (!status || item.status === status)
    && (!q || `${item.title} ${item.subtitle}`.toLowerCase().includes(q)));
}

function renderStaged() {
  const items = stagedState.items || [];
  const counts = { '': items.length, ...(stagedState.counts || {}) };
  for (const kind of ['', 'track', 'album', 'playlist', 'artist']) {
    const el = $(`sq-count-${kind || 'all'}`);
    if (el) el.textContent = counts[kind] || 0;
  }
  const pending = items.filter(i => i.status === 'pending').length;
  const badge = $('nav-queue-badge');
  if (badge) { badge.textContent = pending; badge.classList.toggle('hidden', !pending); }
  const startAll = $('sq-start-all');
  if (startAll) startAll.disabled = !pending;
  updateMusicQueueSub();

  const list = $('sq-list');
  if (!list) return;
  const shown = visibleStaged();
  list.classList.toggle('is-empty', !items.length);
  if (!shown.length) {
    list.innerHTML = items.length
      ? emptyState('disc', 'Nothing matches', 'Try another type, status or search.')
      : emptyState('disc', 'The queue is empty',
          'Use Add to queue on a track, album, playlist or discography, then press Start.');
    return;
  }
  list.innerHTML = shown.map(item => {
    const kind = KIND_META[item.kind] || KIND_META.album;
    const st = STATUS_META[item.status] || STATUS_META.pending;
    const cover = item.cover
      ? `<img src="${escHtml(item.cover)}" alt="" loading="lazy">`
      : `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${kind.icon}</svg>`;
    const meta = [kind.label, `${item.track_count} track${item.track_count === 1 ? '' : 's'}`, `added ${timeAgo(item.added_at)}`]
      .filter(Boolean).join(' · ');
    const actions = [
      item.status === 'pending' ? `<button type="button" class="row-btn accent" title="Start" data-onclick="startStagedOne('${item.id}')"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="6 4 20 12 6 20 6 4"/></svg></button>` : '',
      (item.status === 'failed' || item.status === 'done') ? `<button type="button" class="row-btn" title="Queue again" data-onclick="retryStaged('${item.id}')"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/></svg></button>` : '',
      item.status !== 'running' ? `<button type="button" class="row-btn" title="Remove" data-onclick="removeStaged('${item.id}')"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>` : '',
    ].join('');
    return `<div class="page-row status-${item.status}">
      <div class="pr-cover">${cover}</div>
      <div class="pr-main">
        <div class="pr-title">${escHtml(item.title)}</div>
        <div class="pr-sub">${escHtml(item.subtitle || (item.preview || []).join(', '))}</div>
        <div class="pr-meta">${escHtml(meta)}${item.error ? ` · <span class="pr-error">${escHtml(item.error)}</span>` : ''}</div>
      </div>
      <span class="status-pill ${st.cls}">${item.status === 'running' ? '<i class="pill-spinner"></i>' : ''}${st.label}</span>
      <div class="pr-actions">${actions}</div>
    </div>`;
  }).join('');
}

// N2: the album/track card's "CLI" action (copyCliCommand below).
const CLI_BUTTON_HTML = `
    <button class="act-btn secondary" type="button" id="copy-cli-btn" data-onclick="copyCliCommand()" data-tip="Copy as CLI command" aria-label="Copy as CLI command">
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>
      CLI
    </button>`;

// ── Copy as CLI command (N2) ────────────────────────────────────────────────
// The TUI has a Command panel; here the album card copies the same thing:
// the `mornye …` line that downloads this link with the current settings
// (core/cli_preview.py gui_command, the same rendering the TUI uses).
async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // No async clipboard (an insecure origin, an old web view): the
    // selection route, which every engine still supports.
    const area = document.createElement('textarea');
    area.value = text;
    area.setAttribute('readonly', '');
    area.style.cssText = 'position:fixed;top:0;left:0;opacity:0;pointer-events:none;';
    document.body.appendChild(area);
    area.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch { ok = false; }
    area.remove();
    return ok;
  }
}

async function copyCliCommand() {
  const api = window.pywebview?.api;
  const url = String(currentUrl || '').trim();
  if (!/^(https?:|spotify:)/i.test(url)) {
    toastMgr.error('Open a link first: a search has no command line.', { title: 'Nothing to copy' });
    return;
  }
  if (typeof api?.get_cli_command !== 'function') {
    toastMgr.error('This build cannot build the command.', { title: 'Copy as CLI command' });
    return;
  }
  const res = await api.get_cli_command(url, buildConfig());
  if (!res || res.status !== 'ok') {
    toastMgr.error(res?.error || 'Could not build the command.', { title: 'Copy as CLI command' });
    return;
  }
  if (await copyText(res.command)) {
    toastMgr.success('Paste it in a terminal to run the same download.', { title: 'Command copied', duration: 3000 });
  } else {
    logMessage(res.command, 'info');
    toastMgr.info('The clipboard is not available; the command is in Logs.', { title: 'Copy as CLI command' });
  }
}

async function addToStagedQueue() {
  if (!window.pywebview?.api?.add_to_staged_queue) return;
  const checked = [...document.querySelectorAll('.track-cb:checked')].map(cb => parseInt(cb.value));
  const indices = checked.length ? checked : currentTracks.map((_, i) => i);
  if (!indices.length) { setStatus('No tracks loaded.'); return; }
  const coverImg = document.querySelector('#album-cover img');
  const bg = $('album-cover')?.style.backgroundImage || '';
  const info = {
    kind: detectUrlType(currentUrl) || (indices.length === 1 ? 'track' : 'album'),
    title: ($('album-title')?.textContent || '').trim(),
    subtitle: ($('album-artist')?.textContent || '').trim().replace(/^—$/, ''),
    cover: coverImg?.src || (bg.match(/url\(["']?(.*?)["']?\)/) || [])[1] || '',
    source_url: currentUrl || '',
  };
  try {
    const res = await window.pywebview.api.add_to_staged_queue(indices, info);
    if (res?.ok) {
      showToast(`Added "${res.item.title}" to the queue (${res.item.track_count} track${res.item.track_count === 1 ? '' : 's'}).`);
      // Into the Logs panel only: the toast above already says it, and an
      // 'ok' line would raise a second one with the same news.
      logMessage(`Queued for later: ${res.item.title}`);
    } else {
      showToast(res?.error || 'Could not add to the queue.', 'error');
    }
  } catch (e) {
    showToast('Could not add to the queue: ' + e, 'error');
  }
}

async function startStaged(onlyShown) {
  if (!window.pywebview?.api?.start_staged_items) return;
  const ids = onlyShown ? visibleStaged().filter(i => i.status === 'pending').map(i => i.id) : [];
  if (onlyShown && !ids.length) { showToast('Nothing waiting among the items shown.', 'info'); return; }
  const config = await chooseProviderForBatch(buildConfig());
  if (!config) return;
  const res = await window.pywebview.api.start_staged_items(ids, config);
  if (res?.ok) showToast(`Starting ${res.started} item${res.started === 1 ? '' : 's'}.`);
  else showToast(res?.error || 'Nothing to start.', 'info');
  loadStaged();
}

async function stopAllDownloads() {
  const stop = window.pywebview?.api?.stop_all_downloads;
  if (typeof stop !== 'function') {
    showToast('Stop all is unavailable in this client.', 'error');
    return;
  }
  try {
    const result = await stop();
    if (!result?.ok) throw new Error(result?.error || 'The stop request failed.');
    showToast(
      `Stopping ${result.stopped} active download(s); cancelled ${result.cancelled_pending} queued download(s).`,
      'info',
    );
    logMessage('Stop-all requested.', 'warning');
  } catch (error) {
    showToast(`Could not stop downloads: ${error}`, 'error');
  }
}

async function finishCurrentDownloads() {
  const finish = window.pywebview?.api?.finish_current_downloads;
  if (typeof finish !== 'function') {
    showToast('Finish current is unavailable in this client.', 'error');
    return;
  }
  try {
    const result = await finish();
    if (!result?.ok) throw new Error(result?.error || 'The request failed.');
    showToast(
      result.finishing
        ? `Finishing the tracks in progress, then stopping. Cancelled ${result.cancelled_pending} queued download(s).`
        : `Nothing is downloading. Cancelled ${result.cancelled_pending} queued download(s).`,
      'info',
    );
    logMessage('Finish-current requested: no further tracks will start.', 'warning');
  } catch (error) {
    showToast(`Could not finish downloads: ${error}`, 'error');
  }
}

async function startStagedOne(id) {
  const config = await chooseProviderForBatch(buildConfig());
  if (!config) return;
  const res = await window.pywebview?.api?.start_staged_items?.([id], config);
  if (res && !res.ok) showToast(res.error || 'Could not start it.', 'error');
  loadStaged();
}

async function removeStaged(id) {
  await window.pywebview?.api?.remove_staged_items?.([id]);
  loadStaged();
}

async function retryStaged(id) {
  await window.pywebview?.api?.retry_staged_items?.([id]);
  loadStaged();
}

async function clearStaged(scope) {
  if (scope === 'all' && !(await askConfirm('Remove everything waiting in the queue? Downloads in progress keep going.', { title: 'Clear the queue', ok: 'Remove all', danger: true }))) return;
  await window.pywebview?.api?.clear_staged_queue?.(scope);
  loadStaged();
}

// ── History page ────────────────────────────────────────────────────────────
let historyTab = 'downloads';
let historySearchTimer = null;

function setHistoryTab(tab) {
  historyTab = tab;
  document.querySelectorAll('#view-history .seg-tab').forEach(btn => {
    const on = btn.dataset.tab === tab;
    btn.classList.toggle('active', on);
    btn.setAttribute('aria-selected', String(on));
  });
  const search = $('hist-search');
  if (search) search.placeholder = tab === 'downloads' ? 'Search downloads…' : 'Search fetched links…';
  $('hist-sort-wrap')?.classList.toggle('hidden', tab !== 'downloads');
  loadHistory();
}

function onHistorySearch() {
  clearTimeout(historySearchTimer);
  historySearchTimer = setTimeout(loadHistory, 180);
}

async function loadHistory() {
  const list = $('hist-list');
  if (!list || !window.pywebview?.api) return;
  const q = ($('hist-search')?.value || '').trim();
  try {
    if (historyTab === 'downloads') {
      const res = await window.pywebview.api.get_download_history(q, $('hist-sort')?.value || 'newest', 500);
      renderDownloadHistory(res?.items || [], res?.totals || {}, q);
    } else {
      const hist = await window.pywebview.api.get_history() || [];
      const ql = q.toLowerCase();
      renderFetchHistory(hist.filter(h => !ql || `${h.label || ''} ${h.artist || ''} ${h.url || ''}`.toLowerCase().includes(ql)), q);
    }
  } catch (e) {
    list.innerHTML = errorStateHTML(describeError(String(e), 'History'), 'loadHistory()');
  }
}

function renderDownloadHistory(items, totals, q) {
  const list = $('hist-list');
  const tot = $('hist-totals');
  if (tot) tot.textContent = totals.tracks ? `${totals.tracks} tracks · ${formatSize(totals.bytes)}` : '';
  if (!items.length) {
    list.innerHTML = q
      ? emptyState('disc', 'No downloads match', 'Try a different title, artist or album.')
      : emptyState('disc', 'No download history yet', 'Tracks you download show up here.');
    return;
  }
  // The music look groups the rows by day (Today, Yesterday…) when they are
  // in date order, and reads them as columns: the .m-only cells below.
  const byDate = ['newest', 'oldest'].includes($('hist-sort')?.value || 'newest');
  let lastGroup = '';
  list.innerHTML = items.map(r => {
    const meta = [r.provider, r.format ? r.format.toUpperCase() : '', formatSize(r.bytes), timeAgo(r.downloaded_at)]
      .filter(Boolean).join(' · ');
    const day = historyDay(r.downloaded_at);
    const head = byDate && day.group !== lastGroup ? `<div class="m-only m-group-label">${escHtml(day.group)}</div>` : '';
    lastGroup = day.group;
    return `${head}<div class="page-row${r.success ? '' : ' status-failed'}">
      <div class="pr-cover" data-thumb="${escHtml(r.success ? r.file_path || '' : '')}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${KIND_META.track.icon}</svg></div>
      <div class="pr-main">
        <div class="pr-title">${escHtml(r.title || 'Untitled')}</div>
        <div class="pr-sub">${escHtml([r.artist, r.album].filter(Boolean).join(' · '))}</div>
        <div class="pr-meta" title="${escHtml(r.file_path || '')}">${escHtml(meta)}</div>
        <div class="m-only m-h-artist">${escHtml(r.artist || '')}</div>
      </div>
      <span class="m-only m-h-album">${escHtml(r.album || '')}</span>
      <span class="m-only m-h-q">${r.format ? `<span class="m-q">${escHtml(r.format.toUpperCase())}</span>` : ''}</span>
      <span class="m-only m-h-size${r.success ? '' : ' failed'}">${r.success ? escHtml(formatSize(r.bytes)) : 'Failed'}</span>
      <span class="m-only m-h-when" title="${escHtml(new Date((r.downloaded_at || 0) * 1000).toLocaleString())}">${escHtml(day.when)}</span>
      ${r.success ? '' : '<span class="status-pill failed">Failed</span>'}
      <div class="pr-actions">
        <button type="button" class="row-btn" title="Remove from history" data-onclick="removeDownloadHistory(${Number(r.id)})"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
      </div>
    </div>`;
  }).join('');
  fillHistoryCovers(list);
}

// The day a download belongs to, for the music look's History: its group
// (Today, Yesterday, This Week, then the month) and the time shown in the
// row (the clock today and yesterday, the weekday this week, else the date).
function historyDay(seconds) {
  const d = new Date((Number(seconds) || 0) * 1000);
  const start = new Date(); start.setHours(0, 0, 0, 0);
  const days = Math.floor((start - new Date(d.getFullYear(), d.getMonth(), d.getDate())) / 86400000);
  const clock = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  if (days <= 0) return { group: 'Today', when: clock };
  if (days === 1) return { group: 'Yesterday', when: clock };
  if (days < 7) return { group: 'Earlier This Week', when: d.toLocaleDateString([], { weekday: 'short' }) };
  return {
    group: d.toLocaleDateString([], { month: 'long', year: 'numeric' }),
    when: d.toLocaleDateString([], { day: 'numeric', month: 'short' }),
  };
}

// The music look's History shows each file's own cover, read from the file
// (get_track_thumbs, as the Library does); the console look keeps its icon.
// Only the first rows ask, the ones that are on screen.
async function fillHistoryCovers(list) {
  const api = window.pywebview?.api;
  if (!isMusicStyle() || typeof api?.get_track_thumbs !== 'function') return;
  const cells = [...list.querySelectorAll('.pr-cover[data-thumb]')].filter((c) => c.dataset.thumb).slice(0, 120);
  if (!cells.length) return;
  let thumbs = {};
  try { thumbs = (await api.get_track_thumbs([...new Set(cells.map((c) => c.dataset.thumb))], 96)) || {}; } catch (e) { return; }
  for (const cell of cells) {
    const src = thumbs[cell.dataset.thumb];
    if (src && cell.isConnected) cell.innerHTML = `<img src="${escHtml(src)}" alt="" loading="lazy">`;
  }
}

function renderFetchHistory(items, q) {
  const list = $('hist-list');
  const tot = $('hist-totals');
  if (tot) tot.textContent = items.length ? `${items.length} link${items.length === 1 ? '' : 's'}` : '';
  if (!items.length) {
    list.innerHTML = q
      ? emptyState('disc', 'No fetched links match', 'Try a different name.')
      : emptyState('disc', 'Nothing fetched yet', 'Links you open on Home show up here.');
    return;
  }
  list.innerHTML = items.map((h, idx) => {
    const kind = h.url_type || detectUrlType(h.url || '');
    const km = KIND_META[kind] || KIND_META.album;
    const cover = h.cover
      ? `<img src="${escHtml(h.cover)}" alt="" loading="lazy">`
      : `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${km.icon}</svg>`;
    const meta = [km.label, h.track_count ? `${h.track_count} tracks` : '', timeAgo(h.at)].filter(Boolean).join(' · ');
    return `<div class="page-row clickable" role="button" tabindex="0" data-activate data-idx="${idx}" title="Open this link again">
      <div class="pr-cover">${cover}</div>
      <div class="pr-main">
        <div class="pr-title">${escHtml(h.label || h.url || '—')}</div>
        <div class="pr-sub">${escHtml(h.artist || '')}</div>
        <div class="pr-meta">${escHtml(meta)}</div>
      </div>
      <div class="pr-actions">
        <button type="button" class="row-btn" title="Remove from history" data-remove="${idx}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
      </div>
    </div>`;
  }).join('');
  list.querySelectorAll('.page-row.clickable').forEach(row => {
    const h = items[Number(row.dataset.idx)];
    row.addEventListener('click', (ev) => {
      if (ev.target.closest('[data-remove]')) {
        ev.stopPropagation();
        window.pywebview?.api?.remove_history_item?.(h.url);
        setTimeout(() => { loadHistory(); window.pywebview?.api?.get_history?.().then(renderRecent); }, 150);
        return;
      }
      switchView('home');
      $('urlInput').value = normalizeHistoryUrl(h.url || '');
      onFetch();
    });
  });
}

async function removeDownloadHistory(id) {
  await window.pywebview?.api?.remove_download_history?.([id]);
  loadHistory();
}

async function clearHistory() {
  const what = historyTab === 'downloads' ? 'the download history (your files stay where they are)' : 'the list of fetched links';
  if (!(await askConfirm(`Clear ${what}?`, { title: 'Clear history', ok: 'Clear', danger: true }))) return;
  if (historyTab === 'downloads') await window.pywebview?.api?.clear_download_history?.();
  else {
    await window.pywebview?.api?.clear_fetch_history?.();
    window.pywebview?.api?.get_history?.().then(renderRecent);
  }
  loadHistory();
}

// ── Tracks still missing ─────────────────────────────────────────────────
// The backend's list (core/failed_tracks.py): every track that failed and has
// not downloaded since, across runs and restarts. Unlike `queue`, it is not
// this page's memory, so it is re-read rather than kept in step by hand.
let failedTracks = [];

async function loadFailedTracks() {
  if (!window.pywebview?.api?.get_failed_tracks) return;
  try {
    const doc = await window.pywebview.api.get_failed_tracks();
    failedTracks = Array.isArray(doc?.tracks) ? doc.tracks : [];
  } catch (e) {
    console.warn('[failed-tracks] could not load the list:', e);
    return;
  }
  renderFailedTracks();
}

function renderFailedTracks() {
  const panel = $('failed-panel');
  if (!panel) return;
  panel.classList.toggle('hidden', failedTracks.length === 0);
  const count = $('fp-count');
  if (count) count.textContent = String(failedTracks.length);
  const title = $('fp-title');
  if (title && title.lastChild) {
    title.lastChild.textContent = failedTracks.length === 1
      ? ' track still missing'
      : ' tracks still missing';
  }
  const list = $('fp-list');
  if (!list) return;
  list.innerHTML = failedTracks.map((t) => {
    const name = [t.title, t.artists].filter(Boolean).join(' — ') || t.key;
    const tries = t.attempts > 1 ? `${t.attempts} tries` : '1 try';
    const error = t.error || 'No error message';
    return `
      <li class="fp-item">
        <div class="fp-main">
          <div class="fp-name" title="${regEscapeHtml(name)}">${regEscapeHtml(name)}</div>
          <div class="fp-error" title="${regEscapeHtml(error)}">${regEscapeHtml(error)}</div>
        </div>
        <span class="fp-attempts">${tries}</span>
        <button type="button" class="act-btn secondary" data-key="${regEscapeHtml(t.key)}"
          data-onclick="retryFailedTracks([this.dataset.key])">Retry</button>
      </li>`;
  }).join('');
}

function toggleFailedList() {
  const list = $('fp-list');
  if (!list) return;
  const open = !list.classList.toggle('hidden');
  const btn = $('fp-toggle');
  if (btn) {
    btn.textContent = open ? 'Hide' : 'Show';
    btn.setAttribute('aria-expanded', String(open));
  }
}

async function retryFailedTracks(keys = null) {
  if (!window.pywebview?.api?.retry_failed_tracks) return;
  try {
    const config = await chooseProviderForBatch(buildConfig());
    if (!config) return;
    const res = await window.pywebview.api.retry_failed_tracks(config, keys);
    if (!res?.queued) {
      showToast(res?.already_queued
        ? 'These tracks are already being retried.'
        : 'Nothing left to retry.', 'info');
    }
  } catch (e) {
    logMessage('Could not retry the missing tracks: ' + e, 'error');
  }
}

async function clearFailedTracks() {
  if (!window.pywebview?.api?.clear_failed_tracks) return;
  try {
    await window.pywebview.api.clear_failed_tracks(null);
  } catch (e) {
    logMessage('Could not clear the missing-tracks list: ' + e, 'error');
    return;
  }
  loadFailedTracks();
}

// The four counters double as filters: the number and the way to see what it
// counts are the same control, which is one fewer thing on screen than a
// count plus a separate status dropdown. Clicking the active one clears it.
function filterQueue(status) {
  queueFilterStatus = (queueFilterStatus === status) ? null : status;
  renderQueue();
}

function onQueueSearch(value) {
  queueSearch = value || '';
  renderQueue();
}

function clearQueueFilters() {
  queueFilterStatus = null;
  queueSearch = '';
  const input = $('queue-search');
  if (input) input.value = '';
  renderQueue();
}

function updateQueueItem(qi, status, progress) {
  if (qi < 0 || qi >= queue.length) return;
  queue[qi].status = status; queue[qi].progress = progress;
  renderQueue();
}

function clearQueue() {
  queue = []; isDownloading = false; downloadToastMuted = false;
  queueStats = { downloaded:'0.00 MB', speed:'0.0 MB/s', eta:'' };
  resetQueueDuration();
  renderQueue();
  setStatus('Queue cleared.');
}

function exportFailures() {
  const failures = queue.filter(q => q.status === 'error');
  
  if (!failures.length) {
    showToast('No failed tracks to export.');
    logMessage('Export aborted: No failed tracks found.', 'info');
    return;
  }
  
  // Construct the text content for the file
  let text = 'Mornye Failed Downloads Export\n';
  text += 'Date: ' + new Date().toLocaleString() + '\n';
  text += 'Total Failures: ' + failures.length + '\n';
  text += '-'.repeat(40) + '\n\n';
  
  failures.forEach(f => {
    text += `Title:  ${f.title || 'Unknown'}\n`;
    text += `Artist: ${f.artist || 'Unknown'}\n`;
    text += `ID/URL: ${f.spotify_id || f.id || 'Unknown'}\n`;
    text += '-'.repeat(40) + '\n';
  });
  
  // Create and trigger the download blob
  const blob = new Blob([text], { type: 'text/plain' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  
  a.href = url;
  a.download = `mornye_failures_${Date.now()}.txt`;
  document.body.appendChild(a);
  a.click();
  
  // Cleanup
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
  
  showToast(`${failures.length} failures exported.`);
  logMessage(`Exported ${failures.length} failed tracks to text file.`, 'ok');
}

// ── Download functions (actually work!) ───────────────────────────────────────
function downloadSingle(i) {
  addToQueue([i]);
  startDownloadQueue();
}

function downloadAll() {
  const all = currentTracks.map((_, i) => i);
  console.log('[downloadAll] Total tracks:', all.length);
  if (!all.length) { setStatus('No tracks loaded.'); return; }
  addToQueue(all, { requeueFinished: detectUrlType(currentUrl) === 'playlist' });
  console.log('[downloadAll] Queue length after addToQueue:', queue.length);
  startDownloadQueue();
  $('queue-drawer').classList.add('open');
}

function downloadSelected() {
  const sel = [...document.querySelectorAll('.track-cb:checked')].map(cb => parseInt(cb.value));
  console.log('[downloadSelected] Selected tracks:', sel.length);
  if (!sel.length) { setStatus('No tracks selected.'); return; }
  addToQueue(sel);
  console.log('[downloadSelected] Queue length after addToQueue:', queue.length);
  startDownloadQueue();
  $('queue-drawer').classList.add('open');
}

let providerPickerResolve = null;

function chooseProviderForBatch(config) {
  const activeServices = (config.services || []).filter(Boolean);
  if (!config.ask_provider_before_download) return Promise.resolve(config);
  if (providerPickerResolve) return Promise.resolve(null);
  if (!activeServices.length) {
    toastMgr.error('Enable at least one download source in Settings.');
    return Promise.resolve(null);
  }
  const modal = $('provider-picker-modal');
  const select = $('provider-picker-service');
  const fallback = $('provider-picker-fallback');
  const qualitySelect = $('provider-picker-quality');
  if (!modal || !select || !fallback || !qualitySelect) {
    toastMgr.error('The download source picker is unavailable.');
    return Promise.resolve(null);
  }
  const rows = [...$('services-list').querySelectorAll('.service-sort-item')];
  select.innerHTML = activeServices.map(service => {
    const row = rows.find(item => item.dataset.value === service);
    const label = row?.querySelector('.svc-name')?.textContent || service;
    return `<option value="${escHtml(service)}">${escHtml(label)}</option>`;
  }).join('');
  const preferred = config.preferred_service || activeServices[0];
  select.value = activeServices.includes(preferred) ? preferred : activeServices[0];
  select.onchange = () => updateProviderPickerQuality(select.value);
  updateProviderPickerQuality(select.value, config);
  fallback.checked = config.fallback_services !== false;
  modal.classList.remove('hidden');
  select.focus();
  return new Promise(resolve => { providerPickerResolve = resolve; });
}

function updateProviderPickerQuality(service, config = buildConfig()) {
  const select = $('provider-picker-quality');
  if (!select) return;

  const provider = installedServiceRows.find(row => row.id === service);
  const options = provider?.quality_options || [];
  const savedChoice = config.extension_quality?.[service] || '';

  if (options.length) {
    select.innerHTML = [
      '<option value="">Use global quality</option>',
      ...options.map(option =>
        `<option value="${escHtml(option.id)}" title="${escHtml(option.description || '')}">${escHtml(option.label || option.id)}</option>`
      ),
    ].join('');
    select.value = options.some(option => option.id === savedChoice) ? savedChoice : '';
    select.disabled = false;
    return;
  }

  const globalQuality = $('config-quality');
  select.innerHTML = [...(globalQuality?.options || [])].map(option =>
    `<option value="${escHtml(option.value)}">${escHtml(option.textContent)}</option>`
  ).join('');
  select.value = config.quality || globalQuality?.value || '';
  select.disabled = !select.options.length;
}

function finishProviderPicker(confirmed) {
  const modal = $('provider-picker-modal');
  const resolve = providerPickerResolve;
  providerPickerResolve = null;
  modal?.classList.add('hidden');
  if (!resolve) return;
  if (!confirmed) {
    resolve(null);
    return;
  }
  const config = buildConfig();
  const selected = $('provider-picker-service')?.value;
  const fallback = $('provider-picker-fallback')?.checked === true;
  const ordered = getChecked('services-list');
  if (!selected || !ordered.includes(selected)) {
    toastMgr.error('Choose an enabled download source.');
    resolve(null);
    return;
  }
  config.preferred_service = selected;
  config.fallback_services = fallback;
  config.services = fallback
    ? [selected, ...ordered.filter(service => service !== selected)]
    : [selected];
  const provider = installedServiceRows.find(row => row.id === selected);
  const pickedQuality = $('provider-picker-quality')?.value || '';
  if ((provider?.quality_options || []).length) {
    config.extension_quality = { ...(config.extension_quality || {}) };
    if (pickedQuality) config.extension_quality[selected] = pickedQuality;
    else delete config.extension_quality[selected];
  } else if (pickedQuality) {
    config.quality = pickedQuality;
  }
  resolve(config);
}

function closeProviderPicker() {
  finishProviderPicker(false);
}

function confirmProviderPicker() {
  finishProviderPicker(true);
}

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && !$('provider-picker-modal')?.classList.contains('hidden')) {
    closeProviderPicker();
  }
});

// Execute downloads immediately without waiting for previous batches
async function startDownloadQueue() {
  console.log('[startDownloadQueue] Starting... Queue status:', queue.map(q => q.status));
  
  // One tracklist per batch: its positions only mean something against the
  // list they came from (issue #277). The oldest waiting item's list goes
  // now; the others stay waiting and start when this batch ends (see
  // app_download_finished), which also keeps the batches in order.
  const pending = queue.filter(q => q.status === 'waiting');
  const list = pending.length ? (pending[0].list ?? null) : null;
  const waiting = pending.filter(q => (q.list ?? null) === list);
  console.log('[startDownloadQueue] Waiting items:', waiting.length);
  
  if (!waiting.length) {
    console.warn('[startDownloadQueue] No waiting items, returning');
    return false;
  }

  let config = await chooseProviderForBatch(buildConfig());
  if (!config) return false;

  console.log('[startDownloadQueue] Proceeding with download for:', waiting.length, 'items');



  // Force downloading state but do NOT block concurrent executions
  isDownloading = true;

  // Start duration timer if it's the first active batch
  if (!queueStartTime) {
    queueStartTime = Date.now();
    updateQueueDuration();
    queueDurationInterval = setInterval(updateQueueDuration, 1000);
  }

  // Mark this batch's items as active
  const inBatch = new Set(waiting.map(w => w.id));
  for (let qi = 0; qi < queue.length; qi++) {
    if (queue[qi].status === 'waiting' && inBatch.has(queue[qi].id)) updateQueueItem(qi, 'active', 0);
  }
  
  setStatus(`Downloading track(s)…`, true);

  const indices = waiting.map(w => w.index);
  // The ids, not the indices, are what identifies these rows later: `index`
  // is a position in the fetched tracklist, and a second fetch puts a
  // different track at index 0 — the same trap the dedup above sidesteps.
  const batchIds = new Set(waiting.map(w => w.id));
  console.log('[startDownloadQueue] Indices to download:', indices);
  console.log('[startDownloadQueue] Config:', config);
  console.log('[startDownloadQueue] pywebview available:', !!window.pywebview?.api);

  if (window.pywebview?.api) {
    // Recorded only on the path that actually produces an
    // app_download_finished callback, or the demo fallback below would leave
    // a batch queued up for the next real one to consume.
    dispatchedBatches.push(batchIds);
    try {
      console.log('[startDownloadQueue] Calling download_tracks with indices:', indices);
      // Send the tracks directly to the Python backend
      const op = window.pywebview.api.download_tracks(indices, config, list);
      console.log('[startDownloadQueue] download_tracks returned:', op);
      if (op && typeof op.catch === 'function') {
        op.catch(e => {
          console.error('[startDownloadQueue] Download error:', e);
          dispatchedBatches = dispatchedBatches.filter(b => b !== batchIds);
          queue.forEach((item, qi) => {
            if (batchIds.has(item.id) && item.status === 'active') updateQueueItem(qi, 'error', 0);
          });
          logMessage('Download error: ' + e, 'error');
          setStatus('Error during download.');
        });
      }
    } catch(e) {
      console.error('[startDownloadQueue] Exception:', e);
      logMessage('Download error: ' + e, 'error');
    }
  } else {
    console.warn('[startDownloadQueue] pywebview not available, using demo fallback');
    // Demo fallback for immediate download execution
    for (let idx of indices) {
      const qi = queue.findIndex(q => q.index === idx);
      if (qi < 0 || queue[qi].status !== 'active') continue;
      
      const demoProgress = async () => {
        for (let p = 20; p <= 100; p += 20) {
          updateQueueItem(qi, 'active', p);
          await new Promise(r => setTimeout(r, 150));
        }
        updateQueueItem(qi, 'done', 100);
      };
      demoProgress();
    }
  }
}

// ── UI State Helpers ────────────────────────────────────────────────────────
// Global flag to prevent spam clicks
let isFetchingData = false;
let toastTimeout;

let currentFetchToastId = null;

function setFetchingState(state, customMsg = null) {
  // Backward compatibility
  if (state === true) state = 'start';
  if (state === false) state = 'hide';

  const rw = $('recent-wrap');
  const fetchBtn = $('fetchBtn');
  const urlInput = $('urlInput');

  // Update global lock state
  isFetchingData = (state === 'start');

  // Lock/Unlock UI components
  if (rw) {
    if (isFetchingData) rw.classList.add('fetching-disabled');
    else rw.classList.remove('fetching-disabled');
  }
  if (fetchBtn) fetchBtn.disabled = isFetchingData;
  const isSearchMode = $('searchMode')?.value === 'search';
  if (urlInput) urlInput.disabled = isFetchingData && !isSearchMode;

  if (state === 'start') {
    const title = customMsg || 'fetching metadata...';
    // If a toast is already open, close it before opening another
    if (currentFetchToastId) toastMgr.dismiss(currentFetchToastId);
    
    // Plain text, not markup: toastMgr escapes the message (see
    // toast-system.js's escapeHtml), so the <div> that used to be passed
    // here rendered as a literal tag in the corner of the window — which
    // is what made a normal fetch look like a debug popup.
    currentFetchToastId = toastMgr.loading(
      'please wait...',
      { title: title, position: 'bottom-left' } // Lo teniamo a sinistra come l'originale
    );
  }
  else if (state === 'success') {
    if (currentFetchToastId) toastMgr.dismiss(currentFetchToastId);
    toastMgr.success('Tracklist loaded.', { title: 'Done', position: 'bottom-left', duration: 2500 });
    currentFetchToastId = null;
  }
  else if (state === 'error') {
    if (currentFetchToastId) toastMgr.dismiss(currentFetchToastId);
    const errorTitle = customMsg || 'error occurred';
    toastMgr.error('Unable to retrieve data', { title: errorTitle, position: 'bottom-left', duration: 3500 });
    currentFetchToastId = null;
  }
  else if (state === 'hide') {
    if (currentFetchToastId) toastMgr.dismiss(currentFetchToastId);
    currentFetchToastId = null;
  }
}

// ── Main fetch action ─────────────────────────────────────────────────────────
async function onFetch() {
  if (isFetchingData) return;

  const mode = $('searchMode').value;
  const url = $('urlInput').value.trim();
  // 1. Basic check: input must not be empty in any mode
  if (!url) {
    setFetchingState('error', "Input empty. Please enter a URL or search term.");
    return;
  }

  if (mode === 'search') {
     saveRecentSearch(url);
  }

  if (mode === 'link') {
    const isUrl = url.startsWith('http') || url.startsWith('https') || url.startsWith('spotify:');
    if (!isUrl) {
      setFetchingState('error', "Invalid URL. Please enter a valid URL.");
      return; // Blocca l'esecuzione
    }

    if (url.toLowerCase().includes('amazon.')) {
      showToast("Amazon links cannot be inserted.");
      return; 
    }
  }

  if (mode === 'search') {
    if (url.length < 2) {
      setFetchingState('error', "Search term not valid. Please enter a non-link search term.");
      return;
    }
  }

  setFetchingState('start');

  if (mode === 'search') {
    highlightRecentCard(url);
    setStatus(`Searching "${url}"...`, true);
    // -quiet: still a line in the log panel, but setFetchingState('start')
    // above already raised a bottom-left "fetching" toast for this same
    // search — logMessage's own auto-toast would otherwise stack a second
    // "fetching" notice in the opposite corner for the same action.
    logMessage(`Text search: ${url}`, 'info-quiet');
    currentUrl = url;

    if (window.pywebview?.api) {
      window.pywebview.api.search_provider_async(url, 50, currentSearchSource(), nextSearchRequest())
        .then(() => {
          setStatus(`Searching "${url}"...`, true);
        })
        .catch((e) => {
          setStatus(describeError(String(e), 'Search').title + '.', false);
          logMessage('Search error: ' + e, 'error');
          setFetchingState('error');
        });
    } else {
      const searchUrl = `https://open.spotify.com/search/$${encodeURIComponent(url)}`;
      window.open(searchUrl, '_blank');
      setStatus('Demo: opened Spotify in browser (Python not connected)', false);
      setFetchingState('success'); // Sblocca demo
    }
    return;
  }

  highlightRecentCard(url);
  setStatus('Fetching metadata…', true);
  // -quiet, same reason as the search-mode branch above: setFetchingState('start')
  // already put up a "fetching metadata…" toast bottom-left for this fetch.
  logMessage(`Fetching: ${url}`, 'info-quiet');
  currentUrl = url;
  showSkeletonTracks(5);

  if (window.pywebview?.api) {
    try {
      await window.pywebview.api.fetch_metadata(url);
      // We do not unlock the UI here. Wait for Python callbacks!
    } catch (e) {
      logMessage('Fetch error: ' + e, 'error');
      setFetchingState('error'); // Sblocca in caso di crash Python
    }
  } else {
    // Demo mode
    setTimeout(() => {
      setAlbumCard('ICEMAN', 'Drake', '', 'FLAC · 3 tracks');
      const demo = [
        { index:0, id:'abc1', title:'Make Them Cry', artist:'Drake', duration_ms:307000, explicit:true,  cover:'', isrc:'USRC12345678', external_url:'https://open.spotify.com/track/abc1', preview_url:'https://p.scdn.co/mp3-preview/abc1', playcount:'1234567' }
      ];
      renderTracks(demo, 1);
      setStatus('Found: ICEMAN (1 track) — demo mode', false);
      logMessage('Demo data loaded (Python not connected)', 'warn');
      setFetchingState('success'); // Sblocca demo con successo
    }, 1500);
  }
}

// ── Build config ──────────────────────────────────────────────────────────────
// One request delay range (issue #279) from its two fields. A maximum below
// the minimum ("min 2", max left at 0) is a fixed delay of the minimum: the
// backend refuses max < min, and every download would fail.
function delayRange(kind) {
  const read = (edge) => Math.max(0, parseFloat($(`config-${kind}-delay-${edge}`)?.value) || 0);
  const min = read('min');
  return { [`${kind}_delay_min_s`]: min, [`${kind}_delay_max_s`]: Math.max(min, read('max')) };
}

function buildConfig() {
  const soundEnabled = $('config-sound-enabled')?.checked ?? true;
  const services = getChecked('services-list');
  return {
    services,
    preferred_service:      services[0] || null,
    fallback_services:      $('config-provider-fallback')?.checked !== false,
    ask_provider_before_download: $('config-ask-provider')?.checked === true,
    quality:                $('config-quality').value,
    allow_fallback:         $('config-fallback').checked,
    sound_enabled:          soundEnabled,
    notification_sound:     soundEnabled,
    lyrics:                 $('config-lyrics').checked,
    lyrics_providers:       getChecked('lyrics-list'),
    apple_lyrics_word_by_word: $('config-apple-wbw') ? $('config-apple-wbw').checked : true,
    enrich_metadata:        $('config-enrich').checked,
    save_animated_artwork:  $('config-animated-artwork')?.checked || false,
    animated_artwork_api_url: $('config-animated-artwork-api-url')?.value.trim() || '',
    enrich_providers:       getChecked('enrich-list'),
    filename_format:         $('config-filename').value.trim() || '{title} - {artist}',
    use_track_numbers:      $('config-track-numbers').checked,
    use_album_track_numbers:$('config-album-track-numbers').checked,
    use_artist_subfolders:  $('config-artist-sub').checked,
    use_album_subfolders:   $('config-album-sub').checked,
    album_folder_year:      $('config-album-year')?.checked === true,
    create_playlist_subfolders: $('config-playlist-sub') ? $('config-playlist-sub').checked : true,
    library_scan_dirs:      $('config-library-scan-dirs')?.value.trim() || '',
    first_artist_only:       $('config-first-artist').checked,
    artist_separator:        $('config-artist-separator')?.value.trim() || null,
    transcode_to:           $('config-transcode')?.value || 'none',
    transcode_bitrate:      $('config-transcode-bitrate')?.value || '320k',
    transcode_keep_original: $('config-transcode-keep')?.checked || false,
    dolby_to_ac3: $('config-dolby-ac3') ? $('config-dolby-ac3').checked : true,
    dolby_ac3_keep_original: $('config-dolby-ac3-keep') ? $('config-dolby-ac3-keep').checked : true,
    verify_hires:           $('config-verify-hires')?.checked || false,
    redownload_fake_hires:  $('config-redownload-fake-hires')?.checked || false,
    track_max_retries:      parseInt($('config-retries').value) || 0,
    post_download_action:   $('config-post-action').value,
    post_download_command:  $('config-post-cmd')?.value?.trim() || '',
    extension_quality:      collectExtensionQuality(),
    acoustid_api_key:       $('config-acoustid-key')?.value.trim() || '',
    loop:                   parseInt($('config-loop').value) || null,
    log_level:              $('config-loglevel').value,
    max_concurrent_downloads: parseInt($('config-concurrent')?.value) || 2,
    ...delayRange('metadata'),
    ...delayRange('provider'),
    auto_rate_limit_cooldown: $('config-auto-rate-limit-cooldown')?.checked !== false,
    network_destination_id: $('config-network-destination')?.value || '',
  };
}

let networkDestinations = [];

async function loadNetworkDestinations() {
  const jobSelect = $('config-network-destination');
  const profileSelect = $('network-destination-profile');
  if (!jobSelect || !profileSelect || !window.pywebview?.api?.list_network_destinations) return;
  try {
    const profiles = await window.pywebview.api.list_network_destinations();
    networkDestinations = Array.isArray(profiles) ? profiles : [];
    jobSelect.replaceChildren(new Option('Local / mounted folder', ''));
    profileSelect.replaceChildren(new Option('New destination…', ''));
    for (const profile of networkDestinations) {
      const label = `${profile.name} — ${networkDestinationTarget(profile)}`;
      jobSelect.add(new Option(label, profile.id));
      profileSelect.add(new Option(label, profile.id));
    }
    const cfg = JSON.parse(localStorage.getItem(SETTINGS_STORAGE_KEY) || '{}');
    jobSelect.value = cfg.network_destination_id || '';
  } catch (error) {
    const status = $('network-destination-status');
    if (status) status.textContent = 'Could not load saved network destinations.';
  }
}

function selectNetworkDestination() {
  const profile = networkDestinations.find(
    item => item.id === $('network-destination-profile')?.value
  );
  if (!profile) {
    $('network-destination-type').value = 'smb';
    for (const id of [
      'network-destination-name', 'network-destination-host',
      'network-destination-share', 'network-destination-subdirectory',
      'network-destination-username', 'network-destination-domain',
      'network-destination-url'
    ]) $(id).value = '';
    $('network-destination-password').value = '';
    $('network-destination-timeout').value = '15';
    $('network-destination-encrypt').checked = false;
    if ($('network-destination-method')) $('network-destination-method').value = 'PUT';
    updateNetworkDestinationFields();
    return;
  }
  if ($('network-destination-method')) $('network-destination-method').value = profile.method || 'PUT';
  $('network-destination-type').value = profile.type || 'smb';
  $('network-destination-name').value = profile.name;
  $('network-destination-host').value = profile.host || '';
  $('network-destination-share').value = profile.share || '';
  $('network-destination-url').value = profile.url || '';
  $('network-destination-subdirectory').value = profile.subdirectory || '';
  $('network-destination-username').value = profile.username || '';
  $('network-destination-domain').value = profile.domain || '';
  $('network-destination-password').value = '';
  $('network-destination-timeout').value = profile.timeout || 15;
  $('network-destination-encrypt').checked = profile.encrypt === true;
  updateNetworkDestinationFields();
}

function networkDestinationTarget(profile) {
  if (profile.type === 'webdav') return profile.url;
  if (profile.type === 'http') return `${profile.method || 'PUT'} ${profile.url}`;
  return `${profile.host}/${profile.share}`;
}

function networkDestinationProtocol(type) {
  if (type === 'webdav') return 'WebDAV';
  if (type === 'http') return 'HTTP(S) upload';
  return 'SMB';
}

function updateNetworkDestinationFields() {
  const type = $('network-destination-type')?.value;
  const isHttp = type === 'http';
  // WebDAV and plain HTTP(S) upload both take a URL instead of a server + share.
  const isWebdav = type === 'webdav' || isHttp;
  $('network-destination-host-field')?.classList.toggle('hidden', isWebdav);
  $('network-destination-share-field')?.classList.toggle('hidden', isWebdav);
  $('network-destination-url-field')?.classList.toggle('hidden', !isWebdav);
  $('network-destination-method-field')?.classList.toggle('hidden', !isHttp);
  $('network-destination-domain')?.classList.toggle('hidden', isWebdav);
  $('network-destination-encryption')?.classList.toggle('hidden', isWebdav);
  const urlLabel = $('network-destination-url-label');
  const urlHint = $('network-destination-url-hint');
  const urlInput = $('network-destination-url');
  if (urlLabel) urlLabel.textContent = isHttp ? 'Upload URL (http:// or https://)' : 'WebDAV collection URL';
  if (urlHint) {
    urlHint.textContent = isHttp
      ? 'Each file is sent here with its path appended, or in place of {path}. Use HTTPS when sending a password.'
      : 'Use HTTPS when connecting over an untrusted network.';
  }
  if (urlInput) {
    urlInput.placeholder = isHttp
      ? 'https://nas.example.net/upload/{path}'
      : 'https://cloud.example.net/remote.php/dav/files/user';
  }
  const usernameLabel = $('network-destination-username')
    ?.closest('.network-destination-field')?.querySelector('span');
  if (usernameLabel) {
    usernameLabel.firstChild.textContent = isWebdav
      ? (isHttp ? 'Username (optional, HTTP Basic)' : 'Username')
      : 'Username and optional domain';
  }
}

function networkDestinationForm() {
  const profile = {
    id: $('network-destination-profile').value || undefined,
    type: $('network-destination-type').value,
    name: $('network-destination-name').value.trim(),
    subdirectory: $('network-destination-subdirectory').value.trim(),
    username: $('network-destination-username').value.trim(),
    timeout: Number($('network-destination-timeout').value || 15),
  };
  if (profile.type === 'webdav') {
    profile.url = $('network-destination-url').value.trim();
  } else if (profile.type === 'http') {
    profile.url = $('network-destination-url').value.trim();
    profile.method = $('network-destination-method').value || 'PUT';
  } else {
    profile.host = $('network-destination-host').value.trim();
    profile.share = $('network-destination-share').value.trim();
    profile.domain = $('network-destination-domain').value.trim();
    profile.encrypt = $('network-destination-encrypt').checked;
  }
  return profile;
}

async function saveNetworkDestination() {
  const status = $('network-destination-status');
  if (!window.pywebview?.api?.save_network_destination) {
    status.textContent = 'Network destination management is unavailable.';
    return null;
  }
  try {
    const result = await window.pywebview.api.save_network_destination(
      networkDestinationForm(),
      $('network-destination-password').value || null
    );
    if (!result?.ok) throw new Error(result?.error || 'Could not save the destination.');
    $('network-destination-password').value = '';
    const storage = result.credential_storage;
    status.textContent = storage === 'OS credential store'
      ? 'Profile saved. Password saved in the OS credential store.'
      : storage === 'session-only'
        ? 'Profile saved. Password is session-only.'
        : 'Profile saved. Enter a password, then test the connection.';
    await loadNetworkDestinations();
    $('network-destination-profile').value = result.destination.id;
    return result.destination.id;
  } catch (error) {
    status.textContent = error.message || 'Could not save the destination.';
    return null;
  }
}

async function testNetworkDestination() {
  const status = $('network-destination-status');
  let id = $('network-destination-profile').value;
  if (!id) id = await saveNetworkDestination();
  if (!id) return;
  const protocol = networkDestinationProtocol(networkDestinationForm().type);
  status.textContent = `Testing ${protocol} connection and write permissions…`;
  try {
    const result = await window.pywebview.api.test_network_destination(
      id,
      $('network-destination-password').value || null
    );
    $('network-destination-password').value = '';
    status.textContent = result?.ok
      ? result.message
      : (result?.error || `${protocol} connection test failed.`);
  } catch (error) {
    status.textContent = error.message || `${protocol} connection test failed.`;
  }
}

async function removeNetworkDestination() {
  const id = $('network-destination-profile').value;
  const status = $('network-destination-status');
  if (!id) {
    status.textContent = 'Select a saved destination to remove.';
    return;
  }
  try {
    const result = await window.pywebview.api.remove_network_destination(id);
    if (!result?.ok) throw new Error('That destination no longer exists.');
    if ($('config-network-destination').value === id) {
      $('config-network-destination').value = '';
    }
    $('network-destination-profile').value = '';
    selectNetworkDestination();
    status.textContent = 'Destination removed.';
    await loadNetworkDestinations();
  } catch (error) {
    status.textContent = error.message || 'Could not remove the destination.';
  }
}

async function retryNetworkDelivery() {
  const id = $('config-network-destination').value;
  const stagingId = $('network-destination-retry-id').value.trim();
  const status = $('network-destination-status');
  if (!id) {
    status.textContent = 'Select the destination used by the failed transfer.';
    return;
  }
  status.textContent = 'Retrying retained network transfer…';
  try {
    const result = await window.pywebview.api.retry_network_delivery(id, stagingId);
    status.textContent = result?.ok
      ? result.message
      : (result?.error || 'Network transfer retry failed.');
    if (result?.ok) $('network-destination-retry-id').value = '';
  } catch (error) {
    status.textContent = error.message || 'Network transfer retry failed.';
  }
}

let apiConfigTarget = null;

// The provider APIs (Tidal, Qobuz…) moved into each extension's own
// settings; the popup now only configures the animated artwork API.
function openApiConfigPopup(target) {
  apiConfigTarget = target;
  const title = 'Animated artwork API';
  const description = 'Enter your Apple Music animated artwork API URL and verify reachability.';
  const existingValue = $('config-animated-artwork-api-url').value.trim();

  const status = $('api-config-status');
  $('api-config-title').textContent = title;
  $('api-config-desc').textContent = description;
  $('api-config-value').value = existingValue || '';
  status.textContent = 'Enter a URL and press Check.';
  status.dataset.tone = '';
  const helpLink = $('api-config-help');
  if (helpLink) {
    helpLink.href = 'https://github.com/m8tec/apple-music-animated-artworks';
    helpLink.textContent = 'How to create your own instance';
  }
  $('api-config-modal').classList.remove('hidden');
  setTimeout(() => $('api-config-value').focus(), 0);
}

function closeApiConfigPopup() {
  apiConfigTarget = null;
  $('api-config-modal').classList.add('hidden');
}

function normalizeApiInput(raw) {
  const trimmed = raw.trim();
  if (!trimmed) return '';
  const first = trimmed.split(/\s+/)[0];
  return first;
}

async function checkApiConfig() {
  const rawValue = $('api-config-value').value;
  const url = normalizeApiInput(rawValue);
  const status = $('api-config-status');
  const button = $('api-config-check-btn');
  if (!url) {
    status.textContent = 'Enter a URL first.';
    status.dataset.tone = 'error';
    return;
  }
  if (rawValue.trim() !== url) {
    status.textContent = 'Multiple URLs detected; only the first will be tested.';
    status.dataset.tone = 'warn';
    $('api-config-value').value = url;
  } else {
    status.textContent = 'Checking…';
    status.dataset.tone = '';
  }
  button.disabled = true;
  try {
    if (!window.pywebview?.api) {
      status.textContent = 'API check unavailable in this environment.';
      status.dataset.tone = 'error';
      return;
    }
    let result = null;
    if (apiConfigTarget === 'animated') {
      result = await window.pywebview.api.check_animated_artwork_api(url);
    }
    if (result?.ok) {
      status.textContent = 'Reachable ✓';
      status.dataset.tone = 'ok';
    } else {
      status.textContent = `Check failed: ${result?.error || 'invalid response'}`;
      status.dataset.tone = 'error';
    }
  } catch (e) {
    status.textContent = `Check failed: ${e?.message || e}`;
    status.dataset.tone = 'error';
  } finally {
    button.disabled = false;
  }
}

function clearApiConfigValue() {
  const input = $('api-config-value');
  const status = $('api-config-status');
  const current = normalizeApiInput(input.value);
  if (!current) {
    status.textContent = 'No API configured to clear.';
    status.dataset.tone = 'error';
    return;
  }
  input.value = '';
  status.textContent = 'API cleared from the field. Save to remove it from settings.';
  status.dataset.tone = 'ok';
}

function saveApiConfig() {
  if (!apiConfigTarget) return;
  const rawValue = $('api-config-value').value;
  const value = normalizeApiInput(rawValue);
  if (apiConfigTarget === 'animated') {
    $('config-animated-artwork-api-url').value = value;
  }
  if (rawValue.trim() !== value) {
    const status = $('api-config-status');
    status.textContent = 'Only the first URL was saved.';
    status.dataset.tone = 'warn';
  }
  updateAllApiConfigDisplays();
  closeApiConfigPopup();
  isDirty = true;
  updateSaveButtonVisual();
}

function updateApiConfigDisplay(target) {
  if (target !== 'animated') return;
  const value = $('config-animated-artwork-api-url')?.value.trim() || '';
  const display = $('config-animated-artwork-api-display');
  if (!display) return;
  display.textContent = value ? 'Configured' : 'Not set';
  display.classList.toggle('configured', !!value);
}

function updateAllApiConfigDisplays() {
  updateApiConfigDisplay('animated');
}

// ── Profiles ──────────────────────────────────────────────────────────────────
async function saveProfile() {
  const name = $('profile-name').value.trim();
  if (!name) { logMessage('Enter a profile name', 'error'); return; }
  if (window.pywebview?.api) {
    await window.pywebview.api.save_profile_data(name, buildConfig());
    logMessage(`Profile '${name}' saved.`, 'ok');
    loadHistoryAndProfiles();
  }
}
async function deleteProfile() {
  const name = $('profile-select').value;
  if (!name) {
    logMessage('Select a profile to delete.', 'error');
    return;
  }
  if (!(await askConfirm(`Delete profile '${name}'? This cannot be undone.`, { title: 'Delete profile', ok: 'Delete', danger: true }))) return;
  if (window.pywebview?.api) {
    const result = await window.pywebview.api.delete_profile_data(name);
    if (result) {
      logMessage(`Profile '${name}' deleted.`, 'ok');
      loadHistoryAndProfiles();
    } else {
      logMessage(`Unable to delete profile '${name}'.`, 'error');
    }
  }
}
async function loadProfile() {
  const name = $('profile-select').value;
  if (!name || !window.pywebview?.api) return;
  const data = await window.pywebview.api.load_profile_data(name);
  if (!data) return;
  if (data.quality)                $('config-quality').value            = data.quality;
  if (data.filename_format)         $('config-filename').value           = data.filename_format;
  if (data.extension_quality && typeof data.extension_quality === 'object') {
    extensionQualityChoices = { ...data.extension_quality };
    renderExtensionQualityRows();
  }
  $('config-track-numbers').checked = !!data.use_track_numbers; onTNChange();
  $('config-lyrics').checked = data.lyrics !== false;
  if ($('config-apple-wbw')) $('config-apple-wbw').checked = data.apple_lyrics_word_by_word !== false;
  $('config-enrich').checked        = data.enrich_metadata !== false; onEnrichChange();
  if ($('config-animated-artwork')) {
    $('config-animated-artwork').checked = data.save_animated_artwork === true;
  }
  if ($('config-animated-artwork-api-url')) {
    $('config-animated-artwork-api-url').value = data.animated_artwork_api_url || '';
  }
  updateAllApiConfigDisplays();
  isDirty = true;
  updateSaveButtonVisual();
  logMessage(`Profile '${name}' loaded.`, 'ok');
}

// ── Window controls (no-drag safe wrappers) ───────────────────────────────────
function pyWin(method, arg) {
  if (arg !== undefined) window.pywebview?.api?.[method]?.(arg);
  else window.pywebview?.api?.[method]?.();
}

// Double-clicking the titlebar zooms / restores the window, as a native
// title bar does — the frameless window has none of its own.
document.addEventListener('dblclick', (e) => {
  if (e.target.closest?.('#titlebar .pywebview-drag-region, #np-view .np-drag')) pyWin('WindowToggleMaximise');
});

// ── Extension Registries ───────────────────────────────────────────────────
const REGISTRY_SOURCE_LABELS = {
  environment: 'Terminal export',
  env_file: '.env file',
  custom: 'Added in app',
};

function regEscapeHtml(str) {
  const d = document.createElement('div');
  d.textContent = str ?? '';
  return d.innerHTML
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

async function loadRegistries() {
  const list = $('registry-list');
  if (!list) return;
  if (!window.pywebview?.api?.get_registries) {
    list.innerHTML = '<div class="s-label u-fs-11_5">Registry management is unavailable in this build.</div>';
    return;
  }
  try {
    const registries = await window.pywebview.api.get_registries();
    renderRegistries(registries);
  } catch (e) {
    list.innerHTML = '<div class="s-label u-fs-11_5 u-c-red">Unable to load registries.</div>';
  }
}

function renderRegistries(registries) {
  const list = $('registry-list');
  if (!list) return;

  if (registries?.error) {
    list.innerHTML = '<div class="s-label u-fs-11_5 u-c-red">Failed to load registries: ' + regEscapeHtml(registries.message || 'unknown error') + '</div>';
    return;
  }

  if (!registries || !registries.length) {
    list.innerHTML = '<div class="s-label u-fs-11_5">No registry links configured yet.</div>';
    return;
  }

  list.innerHTML = registries.map((r) => {
    const badges = (r.sources || []).map((s) =>
      `<span class="reg-badge reg-badge-${regEscapeHtml(s)}">${regEscapeHtml(REGISTRY_SOURCE_LABELS[s] || s)}</span>`
    ).join('');
    const disabledCls = r.enabled ? '' : ' reg-item-disabled';
    return `
      <div class="sort-item reg-item${disabledCls}">
        <div class="reg-item-main">
          <span class="reg-url" title="${regEscapeHtml(r.url)}">${regEscapeHtml(r.url)}</span>
          <div class="reg-badges">${badges}${r.enabled ? '' : '<span class="reg-badge reg-badge-off">Removed</span>'}</div>
        </div>
        <button class="act-btn secondary reg-remove-btn" type="button" data-onclick="removeRegistryLink('${encodeURIComponent(r.url)}')" title="Remove this registry link">
          Remove
        </button>
      </div>`;
  }).join('');
}

async function addRegistryLink() {
  const input = $('registry-url-input');
  const url = (input?.value || '').trim();
  if (!url) return;
  if (!window.pywebview?.api?.add_registry) {
    showToast('Registry management is unavailable in this build.', 'error');
    return;
  }
  try {
    const result = await window.pywebview.api.add_registry(url);
    if (result?.ok) {
      input.value = '';
      renderRegistries(result.registries || []);
      logMessage(`Registry added: ${url}`, 'ok');
      showToast('Registry link added.');
    } else {
      showToast(result?.error || 'Unable to add registry link.', 'error');
    }
  } catch (e) {
    showToast('Unable to add registry link.', 'error');
  }
}

async function removeRegistryLink(encodedUrl) {
  const url = decodeURIComponent(encodedUrl);
  if (!window.pywebview?.api?.remove_registry) return;
  try {
    const result = await window.pywebview.api.remove_registry(url);
    if (result?.ok) {
      renderRegistries(result.registries || []);
      logMessage(`Registry removed: ${url}`, 'ok');
      showToast('Registry link removed.');
    } else {
      showToast(result?.error || 'Unable to remove registry link.', 'error');
    }
  } catch (e) {
    showToast('Unable to remove registry link.', 'error');
  }
}

// ── Registry Discovery (Directories) ─────────────────────────────────────────
// A directory lists *registries* (for review), not extensions directly —
// see extensions/directories.py. Same shape/conventions as the registry
// functions just above, on purpose.
async function loadDirectories() {
  const list = $('directory-list');
  if (!list) return;
  if (!window.pywebview?.api?.get_registry_directories) {
    list.innerHTML = '<div class="s-label u-fs-11_5">Directory management is unavailable in this build.</div>';
    return;
  }
  try {
    const directories = await window.pywebview.api.get_registry_directories();
    renderDirectories(directories);
  } catch (e) {
    list.innerHTML = '<div class="s-label u-fs-11_5 u-c-red">Unable to load directories.</div>';
  }
}

function renderDirectories(directories) {
  const list = $('directory-list');
  if (!list) return;

  if (directories?.error) {
    list.innerHTML = '<div class="s-label u-fs-11_5 u-c-red">Failed to load directories: ' + regEscapeHtml(directories.error) + '</div>';
    return;
  }
  if (!directories || !directories.length) {
    list.innerHTML = '<div class="s-label u-fs-11_5">No directory links configured yet.</div>';
    return;
  }

  list.innerHTML = directories.map((d) => {
    const badges = (d.sources || []).map((s) =>
      `<span class="reg-badge reg-badge-${regEscapeHtml(s)}">${regEscapeHtml(REGISTRY_SOURCE_LABELS[s] || s)}</span>`
    ).join('');
    const disabledCls = d.enabled ? '' : ' reg-item-disabled';
    return `
      <div class="sort-item reg-item${disabledCls}">
        <div class="reg-item-main">
          <span class="reg-url" title="${regEscapeHtml(d.url)}">${regEscapeHtml(d.url)}</span>
          <div class="reg-badges">${badges}${d.enabled ? '' : '<span class="reg-badge reg-badge-off">Removed</span>'}</div>
        </div>
        <button class="act-btn secondary reg-remove-btn" type="button" data-onclick="removeDirectoryLink('${encodeURIComponent(d.url)}')" title="Remove this directory link">
          Remove
        </button>
      </div>`;
  }).join('');
}

async function addDirectoryLink() {
  const input = $('directory-url-input');
  const url = (input?.value || '').trim();
  if (!url) return;
  if (!window.pywebview?.api?.add_registry_directory) {
    showToast('Directory management is unavailable in this build.', 'error');
    return;
  }
  try {
    const result = await window.pywebview.api.add_registry_directory(url);
    if (result?.ok) {
      input.value = '';
      renderDirectories(result.directories || []);
      logMessage(`Directory added: ${url}`, 'ok');
      showToast('Directory link added.');
    } else {
      showToast(result?.error || 'Unable to add directory link.', 'error');
    }
  } catch (e) {
    showToast('Unable to add directory link.', 'error');
  }
}

async function removeDirectoryLink(encodedUrl) {
  const url = decodeURIComponent(encodedUrl);
  if (!window.pywebview?.api?.remove_registry_directory) return;
  try {
    const result = await window.pywebview.api.remove_registry_directory(url);
    if (result?.ok) {
      renderDirectories(result.directories || []);
      logMessage(`Directory removed: ${url}`, 'ok');
      showToast('Directory link removed.');
    } else {
      showToast(result?.error || 'Unable to remove directory link.', 'error');
    }
  } catch (e) {
    showToast('Unable to remove directory link.', 'error');
  }
}

async function runDiscovery() {
  const btn = $('btn-run-discovery');
  const results = $('discovery-results');
  if (!results) return;
  if (!window.pywebview?.api?.discover_registries) {
    showToast('Discovery is unavailable in this build.', 'error');
    return;
  }
  if (btn) { btn.disabled = true; btn.textContent = 'Discovering…'; }
  results.innerHTML = '<div class="s-label u-fs-11_5">Probing registries…</div>';
  try {
    const byDirectory = await window.pywebview.api.discover_registries();
    renderDiscoveryResults(byDirectory);
  } catch (e) {
    results.innerHTML = '<div class="s-label u-fs-11_5 u-c-red">Discovery failed.</div>';
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Discover Registries'; }
  }
}

function renderDiscoveryResults(byDirectory) {
  const results = $('discovery-results');
  if (!results) return;

  if (byDirectory?.error) {
    results.innerHTML = '<div class="s-label u-fs-11_5 u-c-red">' + regEscapeHtml(byDirectory.error) + '</div>';
    return;
  }

  const directoryUrls = Object.keys(byDirectory || {});
  if (!directoryUrls.length) {
    results.innerHTML = '<div class="s-label u-fs-11_5">No directories configured, or none reachable.</div>';
    return;
  }

  results.innerHTML = directoryUrls.map((dirUrl) => {
    const rows = (byDirectory[dirUrl] || []).map((r) => {
      const health = r.health || {};
      const badge = health.reachable
        ? `<span class="reg-badge reg-badge-reachable">Reachable · ${health.extension_count} ext.</span>`
        : `<span class="reg-badge reg-badge-unreachable">Unreachable</span>`;
      return `
        <div class="sort-item reg-item">
          <div class="reg-item-main">
            <span class="reg-url" title="${regEscapeHtml(r.url)}">${regEscapeHtml(r.name)} — ${regEscapeHtml(r.url)}</span>
            <div class="reg-badges">${badge}</div>
          </div>
          <button class="act-btn secondary reg-remove-btn" type="button" data-onclick="addDiscoveredAsRegistry('${encodeURIComponent(r.url)}')">
            Add as Registry
          </button>
        </div>`;
    }).join('');
    return `<div class="s-label u-fs-11 u-m-8-0-4">${regEscapeHtml(dirUrl)}</div>${rows}`;
  }).join('');
}

async function addDiscoveredAsRegistry(encodedUrl) {
  const url = decodeURIComponent(encodedUrl);
  if (!window.pywebview?.api?.add_registry) return;
  try {
    const result = await window.pywebview.api.add_registry(url);
    if (result?.ok) {
      renderRegistries(result.registries || []);
      showToast('Registry added.');
    } else {
      showToast(result?.error || 'Unable to add registry.', 'error');
    }
  } catch (e) {
    showToast('Unable to add registry.', 'error');
  }
}

// ── Trusted Signing Keys ──────────────────────────────────────────────────────
// See extensions/trust.py: an Ed25519 signature check layered on top of the
// sha256 checksum ExtensionManager already enforces. Nothing trusted by
// default — an unsigned entry is exactly as trusted as it was before.
async function loadTrustedKeys() {
  const list = $('trust-key-list');
  if (!list) return;
  if (!window.pywebview?.api?.get_trusted_keys) {
    list.innerHTML = '<div class="s-label u-fs-11_5">Trust management is unavailable in this build.</div>';
    return;
  }
  try {
    const keys = await window.pywebview.api.get_trusted_keys();
    renderTrustedKeys(keys);
  } catch (e) {
    list.innerHTML = '<div class="s-label u-fs-11_5 u-c-red">Unable to load trusted keys.</div>';
  }
}

function renderTrustedKeys(keys) {
  const list = $('trust-key-list');
  if (!list) return;

  if (keys?.error) {
    list.innerHTML = '<div class="s-label u-fs-11_5 u-c-red">Failed to load trusted keys: ' + regEscapeHtml(keys.error) + '</div>';
    return;
  }
  if (!keys || !keys.length) {
    list.innerHTML = '<div class="s-label u-fs-11_5">No trusted keys yet.</div>';
    return;
  }

  list.innerHTML = keys.map((k) => `
    <div class="sort-item reg-item">
      <div class="reg-item-main">
        <span class="reg-url">${regEscapeHtml(k.name)}</span>
        <div class="reg-badges"><span class="reg-badge">${regEscapeHtml((k.public_key_b64 || '').slice(0, 16))}…</span></div>
      </div>
      <button class="act-btn secondary reg-remove-btn" type="button" data-onclick="removeTrustedKeyLink('${encodeURIComponent(k.name)}')">
        Remove
      </button>
    </div>`).join('');
}

async function addTrustedKeyLink() {
  const nameInput = $('trust-key-name-input');
  const keyInput = $('trust-key-value-input');
  const name = (nameInput?.value || '').trim();
  const key = (keyInput?.value || '').trim();
  if (!name || !key) {
    showToast('Enter both a name and a public key.', 'error');
    return;
  }
  if (!window.pywebview?.api?.add_trusted_key) {
    showToast('Trust management is unavailable in this build.', 'error');
    return;
  }
  try {
    const result = await window.pywebview.api.add_trusted_key(name, key);
    if (result?.ok) {
      nameInput.value = '';
      keyInput.value = '';
      renderTrustedKeys(result.keys || []);
      showToast('Trusted key added.');
    } else {
      showToast(result?.error || 'Unable to add trusted key.', 'error');
    }
  } catch (e) {
    showToast('Unable to add trusted key.', 'error');
  }
}

async function removeTrustedKeyLink(encodedName) {
  const name = decodeURIComponent(encodedName);
  if (!window.pywebview?.api?.remove_trusted_key) return;
  try {
    const result = await window.pywebview.api.remove_trusted_key(name);
    if (result?.ok) {
      loadTrustedKeys();
      showToast('Trusted key removed.');
    } else {
      showToast(result?.error || 'Unable to remove trusted key.', 'error');
    }
  } catch (e) {
    showToast('Unable to remove trusted key.', 'error');
  }
}

// ── Duplicate Detection (acoustic fingerprint) ───────────────────────────────
// See core/audio_fingerprint.py. pyacoustid ships with Mornye, but the
// fingerprinting is done by fpcalc — a system binary pip cannot install —
// so this can still be unavailable. get_dedup_status() lets us say so up
// front instead of just failing after the user waits for a scan.
async function startDedupScan() {
  const path = $('local-path-input').value.trim();
  if (!path) {
    toastMgr.error('Please enter a valid folder or file path.');
    return;
  }

  if (window.pywebview?.api?.get_dedup_status) {
    try {
      const status = await window.pywebview.api.get_dedup_status();
      if (status && status.available === false) {
        toastMgr.error(status.install_hint || 'Duplicate detection is not available on this machine.');
        return;
      }
    } catch (e) {
      // Fall through and let scan_for_duplicates report the real error.
    }
  }

  setTaBtnState($('btn-scan-dedup'), 'loading');
  $('dedup-results-wrap')?.classList.add('hidden');

  try {
    if (window.pywebview?.api && typeof window.pywebview.api.scan_for_duplicates === 'function') {
      const result = await window.pywebview.api.scan_for_duplicates(path);
      if (result && result.status === 'error') {
        throw new Error(result.error || 'Scan failed');
      }
    }
    toastMgr.info('Fingerprinting files for duplicates... this can take a while for a large library.');
  } catch (err) {
    console.error('[Dedup] start failed:', err);
    setTaBtnState($('btn-scan-dedup'), 'error');
    setTimeout(() => setTaBtnState($('btn-scan-dedup'), 'default'), 2500);
    toastMgr.error(err.message || 'Failed to start duplicate scan');
  }
}

// Called by backend when the fingerprint scan finishes
window.app_dedup_results = function (payload) {
  setTaBtnState($('btn-scan-dedup'), 'default');
  renderDuplicateGroups(payload.groups || []);
};

// Called by backend on scan error
window.app_dedup_error = function (err) {
  setTaBtnState($('btn-scan-dedup'), 'error');
  setTimeout(() => setTaBtnState($('btn-scan-dedup'), 'default'), 2500);
  toastMgr.error('Duplicate scan failed: ' + err);
};

function renderDuplicateGroups(groups) {
  const wrap = $('dedup-results-wrap');
  const container = $('dedup-groups');
  if (!wrap || !container) return;

  if (!groups.length) {
    container.innerHTML = '<div class="s-label u-fs-11_5">No duplicates found.</div>';
    wrap.classList.remove('hidden');
    toastMgr.success('No duplicates found.');
    return;
  }

  container.innerHTML = groups.map((group, i) => `
    <div class="sort-item u-dir-column u-items-stretch u-gap-6 u-cursor-default">
      <div class="s-label u-fs-11">Group ${i + 1} (${group.length} files)</div>
      ${group.map((path) => `<span class="reg-url" title="${regEscapeHtml(path)}">${regEscapeHtml(path)}</span>`).join('')}
    </div>`).join('');
  wrap.classList.remove('hidden');
  toastMgr.success(`Found ${groups.length} duplicate group(s).`);
}

// ── Library duplicates (core/library_dedup.py) ───────────────────────────────
// The other duplicate finder: metadata first, so it scales to a real library,
// and it can act on what it found. The report lives on the backend instance
// between the scan and the resolve — this keeps only what the UI needs to
// render it and to say which files the user picked.
let libDedupGroups = [];
let libDedupManifest = '';

// The seconds two durations may differ by and still count as the same track.
// 0 is a meaningful setting — "the durations must match exactly" — so an empty
// or non-numeric box is what falls back to the default, not every falsy parse.
function libDedupTolerance() {
  const parsed = parseFloat($('libdedup-tolerance')?.value ?? '');
  return Number.isFinite(parsed) ? parsed : 4;
}

async function startLibraryDedupScan() {
  const path = $('local-path-input').value.trim();
  if (!path) {
    toastMgr.error('Please enter a valid folder or file path.');
    return;
  }

  if (typeof window.pywebview?.api?.scan_library_duplicates !== 'function') {
    toastMgr.error('The backend is not ready yet — try again in a moment.');
    return;
  }

  const verify = $('libdedup-verify')?.checked || false;
  if (verify && window.pywebview?.api?.get_dedup_status) {
    // Same courtesy as the fingerprint scan: say the optional dependency is
    // missing now, rather than after the user has waited for a walk.
    try {
      const status = await window.pywebview.api.get_dedup_status();
      if (status && status.available === false) {
        toastMgr.error(status.install_hint || 'Audio confirmation is not available on this machine.');
        return;
      }
    } catch (e) {
      // Fall through; the scan reports its own note if it cannot verify.
    }
  }

  setTaBtnState($('btn-libdedup-scan'), 'loading');
  libDedupGroups = [];
  renderLibraryDedup(null);
  $('libdedup-progress')?.classList.remove('hidden');
  if ($('libdedup-progress')) $('libdedup-progress').textContent = 'Scanning…';

  try {
    const result = await window.pywebview.api.scan_library_duplicates(
      path,
      true,
      $('libdedup-match')?.value || 'both',
      libDedupTolerance(),
      verify,
      0.95,
      $('libdedup-db')?.checked || false,
    );
    if (result && result.status === 'error') throw new Error(result.error || 'Scan failed');
  } catch (err) {
    console.error('[LibDedup] start failed:', err);
    setTaBtnState($('btn-libdedup-scan'), 'error');
    setTimeout(() => setTaBtnState($('btn-libdedup-scan'), 'default'), 2500);
    $('libdedup-progress')?.classList.add('hidden');
    toastMgr.error(err.message || 'Failed to start the library scan');
  }
}

window.app_library_dedup_progress = function (payload) {
  const el = $('libdedup-progress');
  if (!el) return;
  el.classList.remove('hidden');
  el.textContent = `Scanning ${payload.done} / ${payload.total} file(s)…`;
};

window.app_library_dedup_results = function (report) {
  setTaBtnState($('btn-libdedup-scan'), 'default');
  $('libdedup-progress')?.classList.add('hidden');
  renderLibraryDedup(report);
};

window.app_library_dedup_error = function (err) {
  setTaBtnState($('btn-libdedup-scan'), 'error');
  setTimeout(() => setTaBtnState($('btn-libdedup-scan'), 'default'), 2500);
  $('libdedup-progress')?.classList.add('hidden');
  toastMgr.error('Library scan failed: ' + err);
};

function renderLibraryDedup(report) {
  const summary = $('libdedup-summary');
  const container = $('libdedup-groups');
  const actions = $('libdedup-actions');
  if (!container) return;

  if (!report) {
    container.innerHTML = '';
    summary?.classList.add('hidden');
    actions?.classList.add('hidden');
    return;
  }

  const lib = report.library || {};
  const lines = [
    `${lib.files || 0} file(s) scanned · ${lib.total_size || '0 B'}`,
    `${report.groups || 0} duplicate group(s) · ${report.duplicate_files || 0} redundant copies · ${report.reclaimable || '0 B'} reclaimable`,
    `${lib.missing_isrc || 0} without ISRC · ${lib.missing_tags || 0} without artist/title${lib.unreadable ? ` · ${lib.unreadable} unreadable` : ''}`,
  ];
  if (report.database) lines.push(`index written to ${report.database}`);
  (report.notes || []).forEach((n) => lines.push(`note: ${n}`));
  if (summary) {
    summary.textContent = lines.join('\n');
    summary.classList.remove('hidden');
  }

  // Each group keeps its own chosen keeper and its own selection, so the
  // user can disagree with the ranking on one group without disturbing the
  // rest. The backend is told both when they resolve.
  libDedupGroups = (report.duplicate_groups || []).map((group) => {
    const files = [group.keep, ...(group.duplicates || [])];
    return {
      key: group.key,
      label: group.label,
      matchedBy: group.matched_by,
      reclaimable: group.reclaimable_bytes || 0,
      files,
      keepPath: group.keep?.path || '',
      selected: new Set((group.duplicates || []).map((f) => f.path)),
    };
  });

  if (!libDedupGroups.length) {
    container.innerHTML = '<div class="s-label u-fs-11_5">No duplicates found.</div>';
    actions?.classList.add('hidden');
    toastMgr.success('No duplicates found.');
    return;
  }

  container.innerHTML = libDedupGroups.map((group, i) => `
    <div class="sort-item u-dir-column u-items-stretch u-gap-6 u-cursor-default">
      <div class="s-label u-fs-11">
        ${regEscapeHtml(group.label)} — ${group.files.length} copies, ${formatLibDedupSize(group.reclaimable)} reclaimable, matched by ${regEscapeHtml(group.matchedBy)}
      </div>
      ${group.files.map((file, j) => {
        const isKeeper = file.path === group.keepPath;
        return `
        <div class="u-items-center u-gap-8" style="display:flex;">
          <input type="radio" name="libdedup-keep-${i}" ${isKeeper ? 'checked' : ''}
                 data-onchange="setLibDedupKeeper(${i}, ${j})" title="Keep this copy">
          <input type="checkbox" ${group.selected.has(file.path) ? 'checked' : ''}
                 ${isKeeper ? 'disabled' : ''}
                 data-onchange="toggleLibDedupFile(${i}, ${j}, this.checked)"
                 title="${isKeeper ? 'The kept copy is never removed' : 'Remove this copy'}">
          <span class="reg-url u-flex-1 u-minw-0" title="${regEscapeHtml(file.path)}">${regEscapeHtml(file.path)}</span>
          <span class="s-label u-fs-10_5 u-ws-nowrap">${regEscapeHtml(file.quality || '')}</span>
        </div>`;
      }).join('')}
    </div>`).join('');

  if (report.shown_groups !== undefined && report.shown_groups < report.groups) {
    container.innerHTML += `<div class="s-label u-fs-11">Showing ${report.shown_groups} of ${report.groups} groups — resolve these, then scan again for the rest.</div>`;
  }
  actions?.classList.remove('hidden');
  updateLibDedupCount();
  toastMgr.success(`Found ${report.groups} duplicate group(s), ${report.reclaimable} reclaimable.`);
}

function formatLibDedupSize(bytes) {
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let value = bytes || 0;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit++; }
  return `${unit === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[unit]}`;
}

function setLibDedupKeeper(groupIndex, fileIndex) {
  const group = libDedupGroups[groupIndex];
  if (!group) return;
  const chosen = group.files[fileIndex];
  if (!chosen) return;
  // The copy being kept is never also a copy being removed; the one it
  // replaces goes back to being selectable (and selected, since the point of
  // the group is that it is redundant).
  group.selected.delete(chosen.path);
  group.selected.add(group.keepPath);
  group.keepPath = chosen.path;
  renderLibraryDedupGroup(groupIndex);
  updateLibDedupCount();
}

function toggleLibDedupFile(groupIndex, fileIndex, checked) {
  const group = libDedupGroups[groupIndex];
  const file = group?.files[fileIndex];
  if (!file || file.path === group.keepPath) return;
  if (checked) group.selected.add(file.path); else group.selected.delete(file.path);
  updateLibDedupCount();
}

function renderLibraryDedupGroup(groupIndex) {
  // Only the radios and checkboxes of one group change when its keeper does,
  // and re-rendering the whole list would scroll a long report back to the
  // top under the user's cursor.
  const group = libDedupGroups[groupIndex];
  const container = $('libdedup-groups');
  const item = container?.children[groupIndex];
  if (!group || !item) return;
  const rows = item.querySelectorAll('div[style*="display:flex"]');
  group.files.forEach((file, j) => {
    const row = rows[j];
    if (!row) return;
    const [radio, box] = row.querySelectorAll('input');
    const isKeeper = file.path === group.keepPath;
    if (radio) radio.checked = isKeeper;
    if (box) {
      box.disabled = isKeeper;
      box.checked = group.selected.has(file.path);
    }
  });
}

function updateLibDedupCount() {
  const total = libDedupGroups.reduce((sum, g) => sum + g.selected.size, 0);
  const el = $('libdedup-selected');
  if (el) el.textContent = `${total} selected`;
  ['btn-libdedup-trash', 'btn-libdedup-delete'].forEach((id) => {
    const btn = $(id);
    if (btn) btn.disabled = total === 0;
  });
}

async function resolveLibraryDuplicates(action) {
  const paths = [];
  const keepPaths = [];
  libDedupGroups.forEach((group) => {
    if (!group.selected.size) return;
    keepPaths.push(group.keepPath);
    group.selected.forEach((path) => paths.push(path));
  });
  if (!paths.length) {
    toastMgr.error('Nothing selected.');
    return;
  }

  const question = action === 'delete'
    ? `Delete ${paths.length} file(s) permanently? This cannot be undone.`
    : `Move ${paths.length} file(s) into the quarantine folder? You can undo this afterwards.`;
  if (!(await askConfirm(question, { title: action === 'delete' ? 'Delete duplicates' : 'Quarantine duplicates', ok: action === 'delete' ? 'Delete' : 'Move', danger: action === 'delete' }))) return;

  const btn = $(action === 'delete' ? 'btn-libdedup-delete' : 'btn-libdedup-trash');
  setTaBtnState(btn, 'loading');
  try {
    const result = await window.pywebview.api.resolve_library_duplicates(
      paths, keepPaths, action, false,
    );
    if (!result || result.status === 'error') throw new Error(result?.error || 'Failed');

    setTaBtnState(btn, 'default');
    toastMgr.success(`${result.resolved} file(s) resolved, ${result.freed} reclaimed.`);
    (result.actions || [])
      .filter((a) => a.action === 'skip')
      .slice(0, 10)
      .forEach((a) => toastMgr.info(`Left alone: ${a.path} — ${a.error}`));

    libDedupManifest = result.manifest || '';
    const undo = $('libdedup-undo');
    if (libDedupManifest && action !== 'delete') {
      if ($('libdedup-manifest')) $('libdedup-manifest').textContent = libDedupManifest;
      undo?.classList.remove('hidden');
    } else {
      undo?.classList.add('hidden');
    }

    // The scan describes a library that no longer exists; the backend has
    // dropped the report for the same reason, so offering the stale list
    // again would only produce "gone since the scan" for every row.
    libDedupGroups = [];
    renderLibraryDedup(null);
  } catch (err) {
    console.error('[LibDedup] resolve failed:', err);
    setTaBtnState(btn, 'error');
    setTimeout(() => setTaBtnState(btn, 'default'), 2500);
    toastMgr.error(err.message || 'Could not resolve the duplicates');
  }
}

async function undoLibraryDedup() {
  if (!libDedupManifest) return;
  setTaBtnState($('btn-libdedup-undo'), 'loading');
  try {
    const result = await window.pywebview.api.restore_library_duplicates(libDedupManifest);
    if (!result || result.status === 'error') throw new Error(result?.error || 'Failed');
    setTaBtnState($('btn-libdedup-undo'), 'default');
    toastMgr.success(`${result.resolved} file(s) put back.`);
    (result.actions || [])
      .filter((a) => a.action === 'skip')
      .slice(0, 10)
      .forEach((a) => toastMgr.info(`Not restored: ${a.path} — ${a.error}`));
    libDedupManifest = '';
    $('libdedup-undo')?.classList.add('hidden');
  } catch (err) {
    setTaBtnState($('btn-libdedup-undo'), 'error');
    setTimeout(() => setTaBtnState($('btn-libdedup-undo'), 'default'), 2500);
    toastMgr.error(err.message || 'Could not restore the files');
  }
}

// ── Multi-user auth (--web-multiuser; web mode only) ─────────────────────────
// Desktop/pywebview mode has no concept of accounts, so all of this is a
// no-op there — gated on __MORNYE_WEB_MODE__, set only by webapp.py's
// index() route (see web-shim.js / sw.js for the same gate elsewhere).
async function checkAuthStatus() {
  if (!window.__MORNYE_WEB_MODE__) return;
  try {
    const res = await fetch('/api/auth/status');
    const status = await res.json();
    $('account-signout-row')?.classList.toggle('hidden', !status.multiuser);
    if (status.multiuser && !status.logged_in) {
      $('login-modal')?.classList.remove('hidden');
    }
  } catch (e) {
    // Can't reach the server at all — the rest of the app will surface
    // its own connection errors; nothing useful to add here.
  }
}

async function doLogin() {
  const username = ($('login-username')?.value || '').trim();
  const password = $('login-password')?.value || '';
  const errorEl = $('login-error');
  if (errorEl) errorEl.textContent = '';
  if (!username || !password) {
    if (errorEl) errorEl.textContent = 'Enter a username and password.';
    return;
  }
  const btn = $('login-submit-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Signing in…'; }
  try {
    const res = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      if (errorEl) errorEl.textContent = data.error || 'Invalid username or password.';
      return;
    }
    $('login-modal')?.classList.add('hidden');
    $('login-password').value = '';
    $('account-signout-row')?.classList.remove('hidden');
  } catch (e) {
    if (errorEl) errorEl.textContent = 'Could not reach the server.';
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Sign in'; }
  }
}

async function doLogout() {
  try {
    await fetch('/api/auth/logout', { method: 'POST' });
  } catch (e) {
    // Best-effort — reload regardless so the login screen reappears.
  }
  location.reload();
}

// --- EXPLORE LOGIC ---
// Explore's feed comes from Spotify or from any installed extension with a
// home feed (core/extension_home_feed.py) — Jellyfin's "Recently added" and
// "Most played" from the user's own server among them. The choice is a
// per-browser convenience, so localStorage is enough. Until one is picked
// the feed is Apple Music's (the apple-music extension's getHomeFeed), the
// one a music app's Home would show; without that extension, Spotify's.
const EXPLORE_SOURCE_KEY = 'mornye-explore-source';
const DEFAULT_EXPLORE_SOURCE = 'ext:apple-music';

function savedExploreSource() {
  try { return localStorage.getItem(EXPLORE_SOURCE_KEY) || DEFAULT_EXPLORE_SOURCE; } catch (e) { return DEFAULT_EXPLORE_SOURCE; }
}

function setExploreSource(value) {
  try { localStorage.setItem(EXPLORE_SOURCE_KEY, value); } catch (e) { /* private mode */ }
  loadExploreData();
}

/** Fills the source picker; returns the source to load (falls back to
 *  Spotify when the saved one is no longer installed). */
async function refreshExploreSources() {
  const select = $('explore-source');
  let wanted = savedExploreSource();
  const api = window.pywebview?.api;
  if (!select || typeof api?.get_home_feed_sources !== 'function') {
    return wanted.startsWith('ext:') ? 'spotify' : wanted;
  }
  try {
    const sources = (await api.get_home_feed_sources())?.sources || [];
    if (sources.length) {
      select.innerHTML = sources.map((s) =>
        `<option value="${escHtml(s.id)}">${escHtml(s.label)}</option>`).join('');
    }
  } catch (e) { /* keep what is there */ }
  if (![...select.options].some((o) => o.value === wanted)) wanted = 'spotify';
  select.value = wanted;
  return wanted;
}

// quiet: the load Home starts on its own in the music look. A missing feed
// (no extension that provides one) is then said once, in the feed's own
// error state on Home, not also as an error toast at every launch.
async function loadExploreData({ quiet = false } = {}) {
  const sectionsContainer = $('explore-sections');
  const greetingEl = $('explore-greeting');
  
  if (!sectionsContainer) return;
  
  rescueRecentSongs();
  sectionsContainer.innerHTML = '<div class="u-ta-center u-p-40 u-c-muted">Loading feed...</div>';
  const source = await refreshExploreSources();
  if (window.pywebview?.api?.get_spotify_home_feed) {
    try {
      const homeData = source === 'spotify' || !window.pywebview.api.get_extension_home_feed
        ? await window.pywebview.api.get_spotify_home_feed()
        : await window.pywebview.api.get_extension_home_feed(source);
      
      if (homeData && homeData.success) {
        if (greetingEl) greetingEl.textContent = homeData.greeting || 'Explore';
        renderHomeSections(homeData.sections);
      } else {
        const error = homeData?.error || '';
        if (error && !quiet) logMessage('Failed to load explore feed: ' + error, 'error');
        sectionsContainer.innerHTML = errorStateHTML(describeError(error, 'The Explore feed'), 'loadExploreData()');
      }
    } catch (e) {
      if (!quiet) logMessage('Failed to load explore feed: ' + e, 'error');
      sectionsContainer.innerHTML = errorStateHTML(describeError(String(e), 'The Explore feed'), 'loadExploreData()');
    }
  } else {
    // Demo Mode
    if (greetingEl) greetingEl.textContent = 'Explore (Demo)';
    sectionsContainer.innerHTML = '<div class="u-c-muted">Python backend not connected. Unable to load recommendations.</div>';
  }
}

function renderHomeSections(sections) {
  const container = $('explore-sections');
  rescueRecentSongs();
  container.innerHTML = '';

  const KIND = { album: 'Album', playlist: 'Playlist', artist: 'Artist', track: 'Song', station: 'Station' };
  let shelfIndex = 0;
  sections.forEach(section => {
    if (!section.items || section.items.length === 0) return;
    const isFirstShelf = shelfIndex++ === 0;

    const sectionEl = document.createElement('div');
    const titleEl = document.createElement('h3');
    titleEl.className = 'explore-section-title m-shelf-link';
    titleEl.textContent = section.title;
    // The title's chevron opens the shelf: the sideways row becomes a grid
    // of every card (the feed gives no "see all" page to go to), and a
    // second click folds it back.
    titleEl.setAttribute('aria-expanded', 'false');
    titleEl.title = 'Show all';
    makeActivatable(titleEl);
    titleEl.onclick = () => {
      const open = sectionEl.classList.toggle('show-all');
      titleEl.setAttribute('aria-expanded', String(open));
      titleEl.title = open ? 'Show less' : 'Show all';
      if (!open) sectionEl.scrollIntoView({ block: 'nearest' });
    };
    sectionEl.appendChild(titleEl);

    const gridEl = document.createElement('div');
    gridEl.className = 'explore-grid';

    section.items.forEach(item => {
      const card = document.createElement('div');
      card.className = 'explore-card';
      makeActivatable(card);
      
      // No cover, or one that fails to load: a neutral square (css/views/
      // explore.css), not another service's logo — the feed may be Apple's
      // or a Jellyfin server's.
      const blank = '<div class="explore-cover-blank" aria-hidden="true">♪</div>';
      const cover = artSized(item.cover_url, isFirstShelf ? 260 : 220);
      const subText = item.description || item.artists || item.type;

      // The first shelf is the music look's Top Picks: a label above each
      // card (.m-only, hidden in the console look) and a tinted panel.
      const why = KIND[String(item.type || '').toLowerCase()] || section.title || '';
      card.innerHTML = `
        ${isFirstShelf ? `<div class="m-only m-pick-why">${escHtml(why)}</div>` : ''}
        ${cover
          ? `<img src="${escHtml(cover)}" alt="" loading="lazy" data-onerror="this.outerHTML='${blank.replace(/"/g, '&quot;')}'">`
          : blank}
        <div class="explore-card-title" title="${escHtml(item.name)}">${escHtml(item.name)}</div>
        <div class="explore-card-subtitle" title="${escHtml(subText)}">${escHtml(subText)}</div>
      `;

      card.onclick = () => {
        // Return to the home page
        switchView('home');
        
        // Switch to Fetch (Link) mode
        const mode = $('searchMode');
        if (mode && mode.value === 'search') {
          toggleSearchMode(); // Simulate click to set it back to "link"
        }
        
        // Insert the URI
        const input = $('urlInput');
        if (input) {
          // open_url: a link the URL box can open, built by the backend for
          // extension feeds (a Jellyfin item becomes <server>/web/#/details?id=…).
          input.value = item.open_url || item.uri || `spotify:${item.type}:${item.id}`;
          // Scatena la ricerca
          onFetch(); 
        }
      };

      if (isFirstShelf) tintPickCard(card, cover);
      gridEl.appendChild(card);
    });

    sectionEl.appendChild(gridEl);
    container.appendChild(sectionEl);
  });
  placeRecentSongs();
}

// ── Boot ──────────────────────────────────────────────────────────────────────
window.addEventListener('pywebviewready', async () => {
  // No "backend connected" line here: the backend logs its own as soon as it
  // is ready (_on_loaded() in app.py, and webapp.py in --web mode), so
  // announcing it from this side too printed the same event twice, in two
  // different capitalisations.
  loadHistoryAndProfiles();
  loadStaged();  // the sidebar's waiting-count badge
  loadSidebarPlaylists();
  playerSetupEngine();  // the native engine in the desktop app, when libmpv is there
  checkAuthStatus();
  // The real download folder (MORNYE_DOWNLOAD_DIR in a container, the
  // account's own folder in multi-user mode), not the placeholder in the HTML.
  window.pywebview.api.get_download_dir?.()
    .then((res) => { if (res?.download_dir) window.updateFolderLabel(res.download_dir); })
    .catch(() => {});

  await loadNetworkDestinations();
  await loadSettingsFromStorage();
  // After the settings, so the saved order is known and can be restored on
  // top of the narrowed list — and outside them, because a machine with no
  // saved settings yet never calls applySettings() at all, which is exactly
  // the fresh install that most needs to be told what it can download from.
  await refreshInstalledServices();
  await refreshLyricsExtensions();
  initSettingsTracking();
  updateSearchMode();
  initPasteButton();
  loadSearchSources();
  // The music look's Home carries the recommendations; now the backend is
  // there to ask (applySettings may have run before it was).
  if ($('view-home')?.classList.contains('active')) ensureHomeFeed();
});

window.matchMedia('(prefers-color-scheme: dark)').addEventListener?.('change', syncSystemTheme);

window.addEventListener('beforeunload', function (e) {
    if (isDirty) {
        e.preventDefault();
        e.returnValue = '';
    }
});

// ── Real-time search on keystroke ────────────────────────────────────────────
let _searchDebounceTimer = null;
let _searchSaveTimer = null;
let _lastSearchQuery = '';

// Enter in search mode keeps the query in the recent searches at once.
$('urlInput')?.addEventListener('keydown', (e) => {
  if (e.key !== 'Enter' || $('searchMode')?.value !== 'search') return;
  clearTimeout(_searchSaveTimer);
  saveRecentSearch($('urlInput').value);
});

// Every provider search gets a number, and only the newest one's answer is
// shown. Searches overlap all the time — a debounced keystroke, a source
// switch re-running the query — and whichever backend call finished last used
// to win, so a slow Spotify answer could replace the Melon results asked for
// after it.
let _searchRequestSeq = 0;
let _activeSearchRequest = 0;

function nextSearchRequest() {
  _searchRequestSeq += 1;
  _activeSearchRequest = _searchRequestSeq;
  return _activeSearchRequest;
}

function isCurrentSearchRequest(payload) {
  const id = payload && typeof payload === 'object' ? payload.request_id : undefined;
  // No id: a backend that does not send one — nothing to compare, keep it.
  return id === undefined || id === null || id === _activeSearchRequest;
}

$('urlInput').addEventListener('input', function() {
  const mode = $('searchMode').value;
  if (mode !== 'search') return;

  const query = this.value.trim();

  // Clear results if query is empty
  if (!query) {
    clearSearchUI();
    _lastSearchQuery = '';
    nextSearchRequest(); // whatever is still out answers a box that is now empty
    // ...and is dropped as stale, so nothing else would take down a
    // "fetching" notice a Fetch press left up for it.
    if (isFetchingData) setFetchingState('hide');
    clearTimeout(_searchDebounceTimer);
    const container = $('text-search-results');
    if (container) container.innerHTML = '';
    $('text-search-container')?.classList.add('hidden');
    // Was .remove('hidden') — unhid the track table without ever clearing
    // #track-rows, so backspacing a search query back to empty didn't show
    // "no query" at all: it showed whatever track/album was still sitting
    // in the table from before search mode was even entered (e.g. the
    // track you'd just fetched), looking like a stale result for a search
    // that was never run.
    $('track-table-wrap')?.classList.add('hidden');
    $('track-controls')?.classList.add('hidden');
    if ($('recent-wrap')) $('recent-wrap').style.display = ''; // showSkeletonTracks() hides this once a real query starts
    renderRecentSearches();
    return;
  }

  // Skip if same query
  if (query === _lastSearchQuery) return;

  clearTimeout(_searchDebounceTimer);
  clearTimeout(_searchSaveTimer);
  // Kept in the recent searches once typing has settled on it.
  _searchSaveTimer = setTimeout(() => saveRecentSearch(query), 1500);
  _searchDebounceTimer = setTimeout(() => {
    _lastSearchQuery = query;

    // BEGIN CHANGE: Instead of old loading text, show skeletons!
    // Call the function you just created
    showSkeletonTracks(6, 'Searching…');
    
    // Ensure the table container is visible
    $('track-table-wrap')?.classList.remove('hidden');
    $('text-search-container')?.classList.add('hidden');
    // END CHANGE

    if (window.pywebview?.api) {
      window.pywebview.api.search_provider_async(query, 50, currentSearchSource(), nextSearchRequest()).catch(e => {
        logMessage('Real-time search error: ' + e, 'error');
      });
    }
  }, 350);
});

setTimeout(() => {
  if (!window.pywebview) {
    renderRecent([
      { title:'ICEMAN', label:'ICEMAN', url:'https://open.spotify.com/album/0OAv7DCME2AV4q1KPO95HY' },
      { title:'Certified Lover Boy', label:'CLB', url:'https://open.spotify.com/album/3SpBlxme9WbeUDTbAcVsBN' },
    ]);
  }
}, 500);

// ── ffmpeg warning banner ─────────────────────────────────────────────────
// Informational only — Mornye will still try to install ffmpeg itself
// the first time MP3 transcoding is actually used (see
// core/ffmpeg_check.py's ensure_ffmpeg_installed()); this banner just means
// that hasn't happened/worked yet, not that nothing will be attempted. Tidal
// FLAC muxing and Amazon decryption have no such auto-install and will keep
// failing until ffmpeg is available one way or another.
window.showFfmpegWarning = function(result) {
  // Avoid duplicate banners
  if ($('ffmpeg-warning-banner')) return;

  const banner = document.createElement('div');
  banner.id = 'ffmpeg-warning-banner';
  banner.className = 'ffmpeg-banner';
  banner.innerHTML = `
    <span class="ffmpeg-banner-icon">⚠</span>
    <div class="ffmpeg-banner-body">
      <strong>ffmpeg not found</strong>
      <span>Tidal FLAC muxing and Amazon decryption will be unavailable. MP3 transcoding will try to install ffmpeg automatically the first time you use it.</span>
      <a href="#" class="ffmpeg-banner-link"
        data-onclick="event.preventDefault(); pyWin('open_url', 'https://ffmpeg.org/download.html')">
        Download ffmpeg
      </a>
    </div>
    <button class="ffmpeg-banner-close" data-onclick="this.closest('.ffmpeg-banner').remove()" title="Dismiss">✕</button>
  `;

  // Insert right after the search bar
  const searchBar = $('search-bar');
  if (searchBar && searchBar.parentNode) {
    searchBar.parentNode.insertBefore(banner, searchBar.nextSibling);
  }
};

// ── Node.js warning banner ───────────────────────────────────────────────────
// Informational only, like the ffmpeg one above — Mornye still tries to
// install Node itself the first time a JS extension actually runs (see
// core/node_check.py); this banner just means that hasn't happened/worked
// yet, not that nothing will be attempted.
window.showNodeWarning = function(result) {
  if ($('node-warning-banner')) return;

  const banner = document.createElement('div');
  banner.id = 'node-warning-banner';
  banner.className = 'ffmpeg-banner';
  banner.innerHTML = `
    <span class="ffmpeg-banner-icon">⚠</span>
    <div class="ffmpeg-banner-body">
      <strong>Node.js not found</strong>
      <span>JavaScript extensions won't work until it's installed — Mornye will try to install it automatically the first time you use one.</span>
      <a href="#" class="ffmpeg-banner-link"
        data-onclick="event.preventDefault(); pyWin('open_url', 'https://nodejs.org/en/download')">
        Download Node.js
      </a>
    </div>
    <button class="ffmpeg-banner-close" data-onclick="this.closest('.ffmpeg-banner').remove()" title="Dismiss">✕</button>
  `;

  const searchBar = $('search-bar');
  if (searchBar && searchBar.parentNode) {
    searchBar.parentNode.insertBefore(banner, searchBar.nextSibling);
  }
};
// Sets the two version-label DOM elements. Called from Python via the
// generic _push() bridge (desktop: evaluate_js, web: WebSocket dispatch) —
// see mornye/app.py's _push() and frontend/web-shim.js.
// Playcounts that arrived after the table was drawn — a CSV import shows its
// tracklist immediately and fills this column in behind it (see
// _start_csv_playcounts() in api_mixins/csv_import.py), because an arbitrary
// list of tracks costs one Spotify lookup each and there is no album-wide
// query to read them all from at once. Patches the cells in place rather than
// re-rendering, so the user's checkboxes, scroll position and page stay put.
window.app_update_playcounts = function (byId) {
  if (!byId || typeof byId !== 'object') return;
  let patched = 0;
  currentTracks.forEach((t, i) => {
    const count = byId[t.id];
    if (!count) return;
    t.playcount = count;
    patched++;
    // Only the rows on the current page exist in the DOM; the rest pick the
    // value up from currentTracks the next time they are rendered.
    const cb = document.querySelector(`.track-cb[value="${i}"]`);
    const cell = cb && cb.closest('.track-row')?.querySelector('.tr-playcount');
    // A playlist view puts the album name in this column instead — leave it be.
    if (cell && cell.textContent.trim() === '—') {
      cell.textContent = String(count).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    }
  });
  if (patched) logMessage(`Playcount filled in for ${patched} track(s).`, 'debug');
};

window.__set_version_label = function (version) {
  const tb = document.getElementById('tb-version');
  if (tb) tb.innerText = version;
  const hero = document.getElementById('hero-version');
  if (hero) hero.innerText = 'v' + version;
};
// ══════════ LOCAL AUTO-TAGGER LOGIC ══════════

let localScanData = [];

// ── Drag & drop for the "Fix Local Files" target directory ─────────────────
//
// A dropped item only carries a real, absolute filesystem path when the app
// is running as a native pywebview window (pywebview's embedded webview
// exposes File.path for dropped files/folders, unlike a normal browser
// sandbox). In plain browser/web mode there is no way for JS to learn the
// server-side path of something dragged from the user's OS file manager, so
// we fall back to asking the user to type/paste it instead.
function onLocalDragOver(e) {
    e.preventDefault();
    e.stopPropagation();
    $('local-drop-zone').classList.add('drag-over');
}

function onLocalDragLeave(e) {
    e.preventDefault();
    e.stopPropagation();
    $('local-drop-zone').classList.remove('drag-over');
}

function onLocalDrop(e) {
    e.preventDefault();
    e.stopPropagation();
    $('local-drop-zone').classList.remove('drag-over');

    const files = e.dataTransfer?.files;
    if (!files || !files.length) return;

    const dropped = files[0];
    const realPath = dropped.path; // populated by pywebview's desktop webview

    if (realPath) {
        $('local-path-input').value = realPath;
        if (files.length > 1) {
            toastMgr.info(`Using "${realPath.split(/[\\/]/).pop()}" — drop one folder/file at a time.`);
        }
        startLocalScan();
        return;
    }

    toastMgr.warning(
        "Browsers can't reveal the real folder path of a dropped item — "
        + "paste the absolute path into the field above instead."
    );
}

// ── Folder Browser for Local Auto-Tagger ──────────────────────────────────

let currentFolderBrowserPath = null;
let folderBrowserTarget = 'local';   // which page asked for a folder: 'local' | 'player'
let currentFolderBrowserParent = null;

// Wrapper for the "← Back" button: navigateFolderBrowser() needs the real
// parent path from the server (stored after each browse), not a literal
// '..' — the server resolves paths itself and has no notion of a relative
// '..' relative to nothing.
function goFolderBrowserBack() {
    if (currentFolderBrowserParent) {
        navigateFolderBrowser(currentFolderBrowserParent);
    }
}

async function openFolderBrowser(target) {
    folderBrowserTarget = ['player', 'download'].includes(target) ? target : 'local';
    const modal = $('folder-browser-modal');
    modal.classList.remove('hidden');
    modal.focus();

    // Escape key handler
    const escapeHandler = (e) => {
        if (e.key === 'Escape') {
            closeFolderBrowser();
        }
    };
    modal.addEventListener('keydown', escapeHandler);
    modal.dataset.escapeAttached = 'true';

    currentFolderBrowserParent = null;
    const currentPath = (folderBrowserTarget === 'download'
        ? $('folder-path').textContent
        : $(folderBrowserTarget === 'player' ? 'player-path-input' : 'local-path-input').value
    ).trim() || null;

    if (window.pywebview?.api) {
        const api = window.pywebview.api;
        try {
            // Start where the field already points; the home folder is the
            // fallback. (Starting at '/' is refused in the web build, which
            // only browses inside the approved folders.)
            const homePath = typeof api.get_home_dir === 'function' ? await api.get_home_dir() : null;
            if (currentPath || homePath) {
                await navigateFolderBrowser(currentPath || homePath);
                return;
            }
        } catch (err) {
            console.warn('pywebview folder browse fallback failed:', err);
        }
    }

    if (currentPath) {
        await navigateFolderBrowser(currentPath);
        return;
    }

    try {
        const response = await fetch('/api/get-home-dir');
        if (!response.ok) {
            console.warn('get-home-dir endpoint not available, using /');
            await navigateFolderBrowser('/');
            return;
        }
        const data = await response.json();
        const homePath = data.home_dir || '/';
        await navigateFolderBrowser(homePath);
    } catch (err) {
        console.warn('Failed to get home dir, falling back to root:', err);
        await navigateFolderBrowser('/');
    }
}

function closeFolderBrowser() {
    $('folder-browser-modal').classList.add('hidden');
}

async function navigateFolderBrowser(path) {
    if (!path) return;

    const modal = $('folder-browser-modal');
    if (modal.classList.contains('hidden')) return;

    try {
        let data;

        if (window.pywebview?.api && typeof window.pywebview.api.browse_folder === 'function') {
            data = await window.pywebview.api.browse_folder(path);
        } else {
            console.log('[FolderBrowser] Navigating to:', path);
            const encodedPath = encodeURIComponent(path);
            const url = `/api/browse-folder?path=${encodedPath}`;
            const response = await fetch(url);
            if (!response.ok) {
                const errorData = await response.json().catch(() => ({ error: response.statusText }));
                throw new Error(errorData.error || `HTTP ${response.status}`);
            }
            data = await response.json();
        }

        if (!data || data.error) {
            toastMgr.error(`Browse error: ${data?.error || 'Unknown folder browse error'}`);
            console.error('[FolderBrowser] Backend error:', data?.error || data);
            return;
        }

        currentFolderBrowserPath = data.path;
        currentFolderBrowserParent = data.parent || null;
        $('fb-path').value = data.path;

        const entriesDiv = $('fb-entries');
        entriesDiv.innerHTML = '';

        const items = [];
        (data.directories || []).forEach(dirName => {
            items.push({
                type: 'dir',
                name: dirName,
                path: (data.path || '') + '/' + dirName,
            });
        });
        (data.files || []).forEach(fileName => {
            items.push({
                type: 'file',
                name: fileName,
                path: (data.path || '') + '/' + fileName,
            });
        });

        if (items.length > 0) {
            items.forEach(item => {
                const div = document.createElement('div');
                div.className = 'fb-entry';
                makeActivatable(div);
                const icon = item.type === 'dir'
                    ? '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>'
                    : '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H7a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M9 13h6M9 17h6"/></svg>';
                div.innerHTML = icon + ' ';
                div.appendChild(document.createTextNode(item.name));

                div.onclick = async () => {
                    if (item.type === 'dir') {
                        await navigateFolderBrowser(item.path);
                        return;
                    }

                    applyFolderBrowserChoice(item.path);
                };

                entriesDiv.appendChild(div);
            });
        } else {
            entriesDiv.innerHTML = '<div class="fb-empty">No files or subdirectories found.</div>';
        }

        $('fb-back').disabled = !data.parent;
        $('fb-back').classList.toggle('is-root', !data.parent);

    } catch (err) {
        console.error('[FolderBrowser] Navigation error:', err);
        toastMgr.error(`Failed to browse: ${err.message}`);
    }
}

function setFolderPath() {
    if (currentFolderBrowserPath) applyFolderBrowserChoice(currentFolderBrowserPath);
}

// The folder picker is shared: Fix Local Files and the Player open it, and
// so does Settings' Choose Folder when the desktop app is told to use it
// instead of the native dialog (MORNYE_FOLDER_DIALOG=builtin, see app.py).
function applyFolderBrowserChoice(path) {
    closeFolderBrowser();
    if (folderBrowserTarget === 'download') {
        window.pywebview?.api?.set_download_dir?.(path)
            .then((res) => {
                if (res?.ok) toastMgr.success(`Download folder: ${res.download_dir}`);
                else toastMgr.error(res?.error || 'Could not use that folder.');
            })
            .catch((err) => toastMgr.error(`Could not use that folder: ${err}`));
        return;
    }
    if (folderBrowserTarget === 'player') {
        $('player-path-input').value = path;
        scanPlayerLibrary();
        return;
    }
    $('local-path-input').value = path;
    toastMgr.success(`Selected: ${path}`);
    startLocalScan();
}

async function startLocalScan() {
    const path = $('local-path-input').value.trim();
    if (!path) {
        toastMgr.error("Please enter a valid folder or file path.");
        return;
    }

    setTaBtnState($('btn-scan-local'), 'loading');
    $('local-results-wrap').classList.add('hidden');
    $('local-footer').classList.add('hidden');
    localScanData = [];
    localScan.pending.clear();
    if (localScan.raf) { cancelAnimationFrame(localScan.raf); localScan.raf = 0; }
    $('local-track-rows').innerHTML = '';
    localList.view = [];
    localList.shown = 0;
    localList.rows.clear();
    localList.picked.clear();
    localList.applied.clear();
    localScan.id = null;
    localScan.path = path;
    paintScanBar('local-scan', { phase: 'listing', done: 0, total: 0 }, localScan);

    try {
        if (window.pywebview?.api && typeof window.pywebview.api.scan_local === 'function') {
            const result = await window.pywebview.api.scan_local(path);
            if (result && result.status === 'error') {
                throw new Error(result.error || 'Scan failed');
            }
            localScanAdopt(result?.scan);
        } else {
            const response = await fetch('/api/scan_local', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify([path]),
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(data?.error || 'Failed to start local scan');
            }
            if (data?.result?.status === 'error') {
                throw new Error(data.result.error || 'Scan failed');
            }
            localScanAdopt(data?.result?.scan);
        }
    } catch (err) {
        paintScanBar('local-scan', null, localScan);
        console.error('[LocalScan] start failed:', err);
        setTaBtnState($('btn-scan-local'), 'error');
        setTimeout(() => setTaBtnState($('btn-scan-local'), 'default'), 2500);
        toastMgr.error(err.message || 'Failed to start local scan');
    }
}

// The scan whose events the list follows: a newer scan of the same page
// replaces it, and events of an older one are dropped.
const localScan = { id: null, last: 0, path: '', raf: 0, pending: new Set(), scanSamples: [] };

function localScanAdopt(id) {
  if (typeof id !== 'number') return;
  localScan.last = Math.max(localScan.last, id);
  if (localScan.id === null) localScan.id = id;
}

function localScanAccept(p) {
  if (localScan.id === null) {
    if (typeof p?.scan !== 'number' || p.scan < localScan.last) return false;
    localScan.id = p.scan;
    localScan.last = p.scan;
  }
  return p?.scan === localScan.id;
}

// Results by file, in the order they first came: a later result for the
// same file (it changed on disk, or its stored result had grown old)
// replaces it in place and a new file goes at the end, so the rows already
// on screen keep their index. `touched` is the indices that changed.
function mergeLocalResults(current, incoming) {
  const items = current.slice();
  const at = new Map(items.map((e, i) => [e.file_path, i]));
  const touched = [];
  for (const e of incoming || []) {
    let i = at.get(e.file_path);
    if (i === undefined) { i = items.length; at.set(e.file_path, i); items.push(e); }
    else items[i] = e;
    touched.push(i);
  }
  return { items, touched };
}

//: Rows of the list in the page at a time; the next ones are added as the
//: end of the list scrolls into view. Every file used to get a row, and
//: laying out a list of thousands of rows again after each batch of
//: results (~40 ms a batch at 3,000 files, growing with the folder) is
//: what kept the window stalling for as long as a scan ran.
const LOCAL_PAGE = 120;

// What the list shows. `view` is the indices of localScanData that pass
// the filter, in order; the first `shown` of them have a row, kept in
// `rows` by index. The ticks are kept here by file, not in the rows, since
// most files have no row: `picked` is what the user chose (a file not in
// it is ticked when its match is safe), `applied` the files already tagged.
const localList = { view: [], shown: 0, rows: new Map(), picked: new Map(), applied: new Set() };

function localHasMatch(item) {
  return !!(item && item.candidates && item.candidates[0]);
}

function localIsPicked(item) {
  if (!localHasMatch(item) || localList.applied.has(item.file_path)) return false;
  const choice = localList.picked.get(item.file_path);
  return choice === undefined ? !!item.candidates[0].is_safe : choice;
}

// The indices of the ticked files, rows on screen or not.
function localPickedIndices() {
  const out = [];
  localScanData.forEach((item, i) => { if (localIsPicked(item)) out.push(i); });
  return out;
}

// A row's box was ticked or unticked.
function localPick(cb) {
  const item = localScanData[Number(cb.value)];
  if (item) localList.picked.set(item.file_path, cb.checked);
  updateLocalSelection();
}

function localViewIndices() {
  const terms = localSearchTerms(localQuery);
  const out = [];
  localScanData.forEach((item, i) => {
    if (item && (!localFilter || localRowState(item) === localFilter)
        && (!terms.length || localMatchesSearch(item, terms))) out.push(i);
  });
  return out;
}

//: What the search box above the list asks for; '' shows every file.
let localQuery = '';
let localSearchTimer = 0;

function localSearchTerms(query) {
  return String(query || '').toLowerCase().split(/\s+/).filter(Boolean);
}

// The text a file is found by: its tags (or what its name gave), its file
// name and the match it would get. Kept per result, since a scan asks
// again every frame while results arrive; a changed result is a new object.
const localSearchText = new WeakMap();

// Every word of the search is somewhere in it, in any order: "queen
// bohemian" finds "Bohemian Rhapsody" by Queen.
function localMatchesSearch(item, terms) {
  let text = localSearchText.get(item);
  if (text === undefined) {
    const meta = (item.candidates && item.candidates[0] && item.candidates[0].metadata) || {};
    text = [
      item.old_title, item.old_artist, item.old_album, item.guessed_title, item.guessed_artist,
      String(item.file_path || '').split(/[\\/]/).pop(), meta.title, meta.first_artist, meta.album,
    ].filter(Boolean).join(' ').toLowerCase();
    localSearchText.set(item, text);
  }
  return terms.every(t => text.includes(t));
}

// Typing in the search box: the list follows once the typing pauses.
function filterLocalSearch() {
  clearTimeout(localSearchTimer);
  localSearchTimer = setTimeout(() => {
    localQuery = ($('local-search')?.value || '').trim();
    $('local-search-clear')?.classList.toggle('hidden', !localQuery);
    renderLocalTracks();
  }, 120);
}

function clearLocalSearch() {
  const input = $('local-search');
  if (input) input.value = '';
  clearTimeout(localSearchTimer);
  localQuery = '';
  $('local-search-clear')?.classList.add('hidden');
  renderLocalTracks();
  input?.focus();
}

// Said in the list when the search or the filter leaves no file to show.
function localNoMatchNote(container) {
  if (localList.view.length || !localScanData.length || container.querySelector(':scope > .local-nomatch')) return;
  const note = document.createElement('div');
  note.className = 'local-nomatch u-p-40-20 u-ta-center u-c-muted u-fs-14';
  note.textContent = localQuery ? `No file matches "${localQuery}".` : 'No file in this group.';
  container.appendChild(note);
}

// Watches the "Loading more" marker against the list itself, which scrolls
// on its own: against the window, the page around the list hides the
// marker and the next rows would never come.
let localIO = null;
function localObserver(container) {
  if (!localIO && typeof IntersectionObserver === 'function') {
    localIO = new IntersectionObserver(entries => {
      for (const en of entries) {
        if (en.isIntersecting && en.target.isConnected) localAppendRows(LOCAL_PAGE);
      }
    }, { root: container, rootMargin: '300px' });
  }
  return localIO;
}

// Adds rows for the next `count` files of the view, then the "Loading
// more" marker when some are still without a row.
function localAppendRows(count) {
  const container = $('local-track-rows');
  if (!container) return;
  if (!localObserver(container)) count = Infinity;
  let more = container.querySelector(':scope > .local-more');
  const from = localList.shown;
  const to = Math.min(localList.view.length, from + count);
  if (to > from) {
    const frag = document.createDocumentFragment();
    for (let k = from; k < to; k++) {
      const i = localList.view[k];
      const row = localRowElement(localScanData[i], i);
      localList.rows.set(i, row);
      frag.appendChild(row);
    }
    container.insertBefore(frag, more);
  }
  localList.shown = to;
  const left = to < localList.view.length;
  if (left && !more) {
    more = document.createElement('div');
    more.className = 'local-more';
    more.textContent = 'Loading more…';
    container.appendChild(more);
    localIO.observe(more);
  } else if (!left && more) {
    localIO?.unobserve(more);
    more.remove();
  } else if (left && to > from) {
    // Observed again so that a marker still in view after this page
    // (the user is at the bottom) asks for the next one.
    localIO.unobserve(more);
    localIO.observe(more);
  }
}

function scheduleLocalRows() {
  if (!localScan.raf) localScan.raf = requestAnimationFrame(paintLocalRows);
}

// Brings the list up to date with the results that came in since the last
// frame: a changed file that has a row gets a new one, and new files get a
// row while the list is still short of a page (else the "Loading more"
// marker adds them as the user scrolls). If the rows on screen are no
// longer the first of the view (a file changed state under a filter), the
// rows are built again.
function paintLocalRows() {
  localScan.raf = 0;
  const container = $('local-track-rows');
  if (!container) return;
  const touched = localScan.pending;
  localScan.pending = new Set();
  const view = localViewIndices();
  const same = localList.shown <= view.length
    && localList.view.slice(0, localList.shown).every((i, k) => view[k] === i);
  const fresh = !localList.shown;
  $('local-results-wrap').classList.remove('hidden');
  $('local-footer').classList.remove('hidden');
  // The first results of a scan start at the top: the list was hidden
  // when the scan began, and a hidden list keeps the old scroll position.
  if (fresh) container.scrollTop = 0;
  if (!same) {
    renderLocalTracks(true);
  } else {
    if (fresh && view.length) container.innerHTML = '';  // the "no files" or "no match" note
    localList.view = view;
    for (const i of touched) {
      const old = localList.rows.get(i);
      if (!old) continue;
      const row = localRowElement(localScanData[i], i);
      old.replaceWith(row);
      localList.rows.set(i, row);
    }
    localAppendRows(Math.max(0, LOCAL_PAGE - localList.shown));
    localNoMatchNote(container);
  }
  scheduleLocalSummary();
}

// Results arrive in parts: those stored by an earlier scan of the folder
// at once (cached), then the files that were new or changed as they are
// matched.
window.app_local_scan_chunk = function(p) {
  if (!localScanAccept(p)) return;
  const { items, touched } = mergeLocalResults(localScanData, p.files);
  localScanData = items;
  touched.forEach(i => localScan.pending.add(i));
  scheduleLocalRows();
};

window.app_local_scan_progress = function(p) {
  if (!localScanAccept(p)) return;
  paintScanBar('local-scan', p, localScan);
};

// Stops the scan in progress: what was worked out so far stays in the
// list (and is stored, so scanning again picks up from there).
async function cancelLocalScan() {
  const stop = $('local-scan-stop');
  if (stop) stop.disabled = true;
  let res = null;
  try {
    res = await window.pywebview?.api?.cancel_local_scan?.();
  } finally {
    if (stop) stop.disabled = false;
  }
  if (!res || res.status !== 'ok') toastMgr.error('Could not stop the scan.');
}

window.app_local_scan_cancelled = function(p) {
  if (localScan.id === null || p?.scan !== localScan.id) return;
  const n = localScanData.length;
  // Nothing found yet is not "no audio files in this folder"; the rows
  // still to paint are painted as usual.
  paintScanBar('local-scan', null, localScan);
  setTaBtnState($('btn-scan-local'), 'default');
  toastMgr.info(n ? `Scan stopped: ${n} file${n === 1 ? '' : 's'} shown.` : 'Scan stopped.');
};

window.app_local_scan_done = function(p) {
  if (!localScanAccept(p)) return;
  const gone = new Set(p.removed || []);
  if (gone.size || !localScanData.length) {
    // Rows leave the list: indices shift, so it is drawn again whole.
    localScanData = localScanData.filter(e => !gone.has(e.file_path));
    if (localScan.raf) { cancelAnimationFrame(localScan.raf); localScan.raf = 0; }
    renderLocalTracks(true);
    $('local-results-wrap').classList.remove('hidden');
    $('local-footer').classList.remove('hidden');
    updateLocalSelection();
  }
  paintScanBar('local-scan', null, localScan);
  setTaBtnState($('btn-scan-local'), 'default');
  const n = localScanData.length;
  const checked = Number(p.checked) || 0;
  toastMgr.success(checked < n
    ? `Scan complete: ${n} files, ${checked} new or changed.`
    : `Scan complete: found ${n} files.`);
};

// Called by backend on scan error
window.app_local_scan_error = function(err) {
    paintScanBar('local-scan', null, localScan);
    setTaBtnState($('btn-scan-local'), 'error');
    setTimeout(() => setTaBtnState($('btn-scan-local'), 'default'), 2500);
    toastMgr.error("Scan failed: " + err);
};

// Builds the rows again from the first file of the view. `keepPlace`
// keeps as many rows as there were and where the list was scrolled to (a
// file was edited, converted or moved); otherwise it starts from the top
// (the filter changed).
function renderLocalTracks(keepPlace = false) {
    const container = $('local-track-rows');
    const top = container.scrollTop;
    const count = keepPlace ? Math.max(LOCAL_PAGE, localList.shown) : LOCAL_PAGE;
    localScan.pending.clear();
    localList.rows.clear();
    localList.shown = 0;
    localList.view = localViewIndices();
    container.innerHTML = '';

    if (!localScanData.length) {
        container.innerHTML = '<div class="u-p-40-20 u-ta-center u-c-muted u-fs-14">No supported audio files found in this folder (FLAC, MP3, M4A/AAC, OGG, Opus, WAV, AIFF, WMA, WavPack, APE and more).</div>';
        return;
    }

    localAppendRows(count);
    localNoMatchNote(container);
    container.scrollTop = keepPlace ? top : 0;
    renderLocalSummary();
}

// One row of the Fix Local Files list; `i` is the item's index in
// localScanData, which its checkbox and buttons carry.
function localRowElement(item, i) {
        const best = item.candidates && item.candidates[0];
        const hasMatch = !!best;
        const isSafe = hasMatch && best.is_safe;
        
        const oldCover = item.old_cover_base64
            ? `<img src="${item.old_cover_base64}" alt="cover" decoding="async">`
            : `🎵`;
            
        const fileName = item.file_path.split(/[\\/]/).pop();
        const oldTitle = item.old_title || item.guessed_title || fileName;
        const oldArtist = item.old_artist || item.guessed_artist || "Unknown Artist";
        
        let newCol = `<div class="u-c-muted u-fs-12_5 u-fst-italic">No match found</div>`;
        let scoreCol = `<span class="local-badge err">No Match</span>`;
        let checkbox = `<input type="checkbox" class="local-cb" value="${i}" data-file-path="${escHtml(item.file_path)}" disabled>`;
        const done = localList.applied.has(item.file_path);

        if (hasMatch) {
            const newCover = best.metadata.cover_url || best.metadata.cover || '';
            // Lazy: a folder of thousands of files would otherwise fetch
            // every candidate's cover from the network as its row appears,
            // the ones far below the fold included.
            const newCoverHtml = newCover
                ? `<img src="${escHtml(newCover)}" alt="" loading="lazy" decoding="async">`
                : `🎵`;
            const newTitle = best.metadata.title || '';
            const newArtist = best.metadata.first_artist || '';

            // Diffing logic: mark as different if texts don't match (case insensitive)
            const hlTitle = oldTitle.toLowerCase() !== newTitle.toLowerCase() ? 'diff' : '';
            const hlArtist = oldArtist.toLowerCase() !== newArtist.toLowerCase() ? 'diff' : '';

            newCol = `
                <div class="local-cell-content">
                    <div class="local-thumb">${newCoverHtml}</div>
                    <div class="local-info">
                        <div class="local-title ${hlTitle}" title="New: ${escHtml(newTitle)}">${escHtml(newTitle)}</div>
                        <div class="local-artist ${hlArtist}">${escHtml(newArtist)}</div>
                    </div>
                </div>
            `;

            // An ISRC hit is an identity check, not a similarity score, so it
            // says so instead of showing a meaningless 100%. A row that scored
            // well but is held back — the title disagrees, or it looks like a
            // live/remix version with no duration to confirm it — explains why
            // it is not pre-ticked, rather than leaving the user to wonder.
            if (best.how === 'isrc') {
                scoreCol = `<span class="local-badge ok" title="Matched by ISRC — the file's own recording ID, not a guess">ISRC</span>`;
            } else {
                let why = 'Confidence score';
                if (!isSafe && best.confidence >= 90) {
                    if (!best.artist_known) {
                        why = 'This file has no artist — in tags or in its name — so only the title was compared. Check before applying';
                    } else if (best.variant_unconfirmed) {
                        why = 'Looks like a different version (live/remix/instrumental) and there is no duration to confirm it — check before applying';
                    } else {
                        why = 'The titles do not agree closely enough to apply this unattended — check before applying';
                    }
                }
                scoreCol = `<span class="local-badge ${isSafe ? 'ok' : 'warn'}" title="${escHtml(why)}">${best.confidence}%</span>`;
            }
            checkbox = `<input type="checkbox" class="local-cb" value="${i}" data-file-path="${escHtml(item.file_path)}" ${localIsPicked(item) ? 'checked' : ''} ${done ? 'disabled' : ''} data-onchange="localPick(this)">`;
        } else if (item.error) {
            scoreCol = `<span class="local-badge err">Error</span>`;
            newCol = `<div class="u-c-red u-fs-11">${escHtml(item.error)}</div>`;
        }

        const row = document.createElement('div');
        row.className = 'track-row local-row u-cols-24-1fr-80-1fr u-cursor-default';
        
        row.innerHTML = `
            <div class="tr-check">${checkbox}</div>
            <div class="local-cell-content">
                <div class="local-thumb">${oldCover}</div>
                <div class="local-info">
                    <div class="local-title" title="Current: ${escHtml(oldTitle)}">${escHtml(oldTitle)}</div>
                    <div class="local-artist">${escHtml(oldArtist)}</div>
                    <div class="u-ff-jetbrains-mono-monospace u-fs-9_5 u-c-muted u-o-hidden u-text-overflow-ellipsis u-ws-nowrap u-mt-3" title="${escHtml(item.file_path)}">${escHtml(fileName)}</div>
                    <div class="local-actions">
                      <button type="button" class="local-convert-btn" title="Play this file as it is stored" data-onclick="playLocal(${i})">Play</button>
                      <button type="button" class="local-convert-btn" title="Edit the tags, cover and lyrics of this file" data-onclick="openTagEditor(${i})">Edit</button>
                      <button type="button" class="local-convert-btn" title="Convert this file to another format" data-onclick="openConvertDialog([${i}])">Convert</button>
                      <button type="button" class="local-convert-btn" title="Show technical details and a spectrogram" data-onclick="openAnalyze(${i})">Analyze</button>
                    </div>
                </div>
            </div>
            <div class="u-ta-center">${scoreCol}</div>
            ${newCol}
        `;
        // What the summary above the list counts and filters by.
        row.dataset.state = localRowState(item);
        row.dataset.index = String(i);
        return row;
}

// A result's state in the list: what its row shows and the chips count.
function localRowState(item) {
    const best = item.candidates && item.candidates[0];
    if (!best) return item.error ? 'error' : 'none';
    return best.is_safe ? 'matched' : 'review';
}

//: Which rows the list shows: '' for all, else one row state.
let localFilter = '';

// Counts per state above the list; a click shows only that state.
//
// Counted from localScanData, not by querying the rows: during a scan this
// ran after every painted batch, and walking 4,000 rows each time (plus the
// filter and the selection count doing the same) is what made a large
// folder grind as it filled up.
function renderLocalSummary() {
    const box = $('local-summary');
    if (!box) return;
    const counts = { matched: 0, review: 0, none: 0, error: 0 };
    let total = 0;
    for (const item of localScanData) {
        if (!item) continue;
        total += 1;
        counts[localRowState(item)] += 1;
    }
    const chips = [
        ['', `${total} file${total === 1 ? '' : 's'}`, total],
        ['matched', 'Ready', counts.matched],
        ['review', 'Check', counts.review],
        ['none', 'No match', counts.none],
        ['error', 'Errors', counts.error],
    ].filter(([state, , n]) => state === '' || n > 0);
    const lost = localFilter && !chips.some(([state]) => state === localFilter);
    if (lost) localFilter = '';
    box.innerHTML = total ? chips.map(([state, label, n]) =>
        `<button type="button" class="local-chip ${state || 'all'}${localFilter === state ? ' active' : ''}" data-state="${state}" aria-pressed="${localFilter === state}">`
        + (state ? `<span class="local-chip-n">${n}</span> ` : '') + `${escHtml(label)}</button>`).join('') : '';
    box.querySelectorAll('.local-chip').forEach(btn => btn.addEventListener('click', () => {
        localFilter = btn.dataset.state === localFilter ? '' : btn.dataset.state;
        renderLocalTracks();
    }));
    if (lost) renderLocalTracks();  // its last file changed state
}

//: Pending timer for the summary and selection count while a scan paints.
let localSummaryTimer = 0;

// Both walk the whole result set, so while results keep arriving they run
// at most a few times a second instead of after every painted batch.
function scheduleLocalSummary() {
    if (localSummaryTimer) return;
    localSummaryTimer = setTimeout(() => {
        localSummaryTimer = 0;
        renderLocalSummary();
        updateLocalSelection();
    }, 400);
}

// ── Convert files to another format ─────────────────────────────────────────
let convertPaths = [];
let convertFormats = null;

async function openConvertDialog(indices) {
  const idx = Array.isArray(indices)
    ? indices
    : localPickedIndices();
  convertPaths = idx.map(i => localScanData[i]?.file_path).filter(Boolean);
  if (!convertPaths.length) {
    toastMgr.info('Tick at least one file to convert.');
    return;
  }
  if (!convertFormats) {
    try {
      convertFormats = (await window.pywebview?.api?.get_convert_formats?.()) || [];
    } catch (e) { convertFormats = []; }
  }
  if (!convertFormats.length) {
    toastMgr.error('Could not load the list of formats.');
    return;
  }
  const sel = $('convert-format');
  if (!sel.options.length) {
    sel.innerHTML = convertFormats.map(f =>
      `<option value="${escHtml(f.value)}">${escHtml(f.label)} — ${escHtml(f.hint)}</option>`).join('');
    $('convert-bitrate').innerHTML = [320, 256, 192, 160, 128, 96, 64]
      .map(b => `<option value="${b}">${b} kbps</option>`).join('');
  }
  $('convert-desc').textContent = convertPaths.length === 1
    ? convertPaths[0].split(/[\\/]/).pop()
    : `${convertPaths.length} files`;
  updateConvertOptions();
  $('convert-modal').classList.remove('hidden');
}

function closeConvertDialog() {
  $('convert-modal').classList.add('hidden');
}

function updateConvertOptions() {
  const f = (convertFormats || []).find(x => x.value === $('convert-format').value);
  const lossless = !!f?.lossless;
  $('convert-lossy-opts').classList.toggle('hidden', lossless);
  $('convert-lossless-opts').classList.toggle('hidden', !lossless);
  $('convert-lossless-opts').style.display = lossless ? 'flex' : 'none';
  $('convert-note').textContent = lossless
    ? 'Lossless targets only work on lossless sources (FLAC, ALAC, WAV, AIFF…): converting an MP3 or AAC file would only make it bigger.'
    : 'Tags, cover art and lyrics are carried over to the new file.';
}

async function startConvert() {
  const btn = $('convert-start-btn');
  const options = {
    format: $('convert-format').value,
    bitrate: $('convert-bitrate').value,
    keep_original: $('convert-keep').checked,
    max_bit_depth: parseInt($('convert-depth').value) || 0,
    dither: $('convert-dither')?.value || 'tpdf',
    max_sample_rate: parseInt($('convert-rate').value) || 0,
  };
  btn.disabled = true;
  btn.textContent = `Converting 0/${convertPaths.length}…`;
  const res = await window.pywebview?.api?.convert_local_files?.(convertPaths, options);
  if (!res || res.status !== 'started') {
    btn.disabled = false;
    btn.textContent = 'Convert';
    toastMgr.error(res?.error || 'Could not start the conversion.');
  }
}

window.app_local_convert_progress = function(payload) {
  $('convert-start-btn').textContent = `Converting ${payload.done}/${payload.total}…`;
  const last = payload.last || {};
  if (!last.success) {
    toastMgr.error(`${(last.file_path || '').split(/[\\/]/).pop()}: ${last.error}`);
  }
};

window.app_local_convert_finished = function(payload) {
  const btn = $('convert-start-btn');
  btn.disabled = false;
  btn.textContent = 'Convert';
  closeConvertDialog();
  const results = payload.results || [];
  const ok = results.filter(r => r.success);
  // Point each converted row at its new file so a follow-up "Apply tags"
  // does not aim at a source that has just been removed.
  ok.forEach(r => {
    const item = localScanData.find(e => e.file_path === r.file_path);
    if (item && r.output_path) item.file_path = r.output_path;
  });
  if (ok.length) renderLocalTracks(), updateLocalSelection();
  if (ok.length) toastMgr.success(`Converted ${ok.length} of ${results.length} file(s).`);
};

window.app_local_convert_error = function(err) {
  const btn = $('convert-start-btn');
  btn.disabled = false;
  btn.textContent = 'Convert';
  toastMgr.error('Conversion failed: ' + err);
};

// ── Tag editor ──────────────────────────────────────────────────────────────
const TAG_LABELS = {
  TITLE: 'Title', ARTIST: 'Artist', ALBUM: 'Album', ALBUMARTIST: 'Album artist',
  DATE: 'Date', GENRE: 'Genre', TRACKNUMBER: 'Track #', DISCNUMBER: 'Disc #',
  COMPOSER: 'Composer', ISRC: 'ISRC', COPYRIGHT: 'Copyright',
};
let tagEditState = null;

async function openTagEditor(index) {
  const item = localScanData[index];
  if (!item) return;
  const res = await window.pywebview?.api?.read_file_tags?.(item.file_path);
  if (!res || res.status !== 'ok') {
    toastMgr.error(res?.error || 'Could not read the tags.');
    return;
  }
  tagEditState = { path: item.file_path, index, cover: undefined, removeCover: false, original: res.tags, lyrics: res.lyrics };
  $('tagedit-file').textContent = item.file_path.split(/[\\/]/).pop();
  $('tagedit-fields').innerHTML = res.fields.map(f => `
    <div style="${f === 'TITLE' || f === 'COPYRIGHT' ? 'grid-column:1/-1;' : ''}">
      <label class="u-fs-12 u-c-muted" for="tagedit-${f}">${escHtml(TAG_LABELS[f] || f)}</label>
      <input class="u-w-100pct u-mt-3" type="text" id="tagedit-${f}" data-tag="${f}" value="${escHtml(res.tags[f] || '')}">
    </div>`).join('');
  $('tagedit-lyrics').value = res.lyrics || '';
  $('tagedit-lyrics-source').textContent = '';
  fillTagEditorLyricsProviders();
  $('tagedit-translation-wrap').classList.add('hidden');
  $('tagedit-translation').value = '';
  await ensureTranslationLanguages();
  const img = $('tagedit-cover');
  img.src = res.cover || '';
  img.style.visibility = res.cover ? 'visible' : 'hidden';
  $('tagedit-modal').classList.remove('hidden');
}

function closeTagEditor() { $('tagedit-modal').classList.add('hidden'); tagEditState = null; }

function pickTagEditorCover(input) {
  const file = input.files && input.files[0];
  if (!file || !tagEditState) return;
  const reader = new FileReader();
  reader.onload = () => {
    tagEditState.cover = reader.result;
    tagEditState.removeCover = false;
    $('tagedit-cover').src = reader.result;
    $('tagedit-cover').style.visibility = 'visible';
  };
  reader.readAsDataURL(file);
  input.value = '';
}

function removeTagEditorCover() {
  if (!tagEditState) return;
  tagEditState.cover = undefined;
  tagEditState.removeCover = true;
  $('tagedit-cover').style.visibility = 'hidden';
}

async function saveTagEditor() {
  if (!tagEditState) return;
  const edits = {};
  document.querySelectorAll('#tagedit-fields [data-tag]').forEach(el => {
    const key = el.dataset.tag;
    if (el.value.trim() !== (tagEditState.original[key] || '')) edits[key] = el.value;
  });
  const lyricsNow = $('tagedit-lyrics').value;
  const lyrics = lyricsNow !== (tagEditState.lyrics || '') ? lyricsNow : null;
  if (!Object.keys(edits).length && lyrics === null && !tagEditState.cover && !tagEditState.removeCover) {
    closeTagEditor();
    return;
  }
  const btn = $('tagedit-save-btn');
  btn.disabled = true;
  btn.textContent = 'Saving…';
  const res = await window.pywebview?.api?.save_file_tags?.(
    tagEditState.path, edits, lyrics, tagEditState.cover || '', tagEditState.removeCover);
  btn.disabled = false;
  btn.textContent = 'Save';
  if (!res || res.status !== 'ok') {
    toastMgr.error(res?.error || 'Could not save the tags.');
    return;
  }
  const item = localScanData[tagEditState.index];
  if (item) {
    if ('TITLE' in edits) item.old_title = edits.TITLE;
    if ('ARTIST' in edits) item.old_artist = edits.ARTIST;
    if ('ALBUM' in edits) item.old_album = edits.ALBUM;
    if (tagEditState.cover) item.old_cover_base64 = tagEditState.cover;
    if (tagEditState.removeCover) item.old_cover_base64 = '';
  }
  toastMgr.success('Tags saved.');
  closeTagEditor();
  renderLocalTracks();
  updateLocalSelection();
}

// ── Audio analysis ──────────────────────────────────────────────────────────
async function openAnalyze(index) {
  const item = localScanData[index];
  if (!item) return;
  $('analyze-file').textContent = item.file_path.split(/[\\/]/).pop();
  $('analyze-info').innerHTML = '';
  $('analyze-img').classList.add('hidden');
  $('analyze-status').textContent = 'Drawing the spectrogram…';
  $('analyze-status').classList.remove('hidden');
  $('analyze-modal').classList.remove('hidden');
  const res = await window.pywebview?.api?.analyze_audio_file?.(item.file_path);
  if ($('analyze-modal').classList.contains('hidden')) return;
  if (!res || res.status !== 'ok') {
    $('analyze-status').textContent = res?.error || 'Analysis failed.';
    return;
  }
  const i = res.info || {};
  const mb = i.size_bytes ? (i.size_bytes / 1048576).toFixed(1) + ' MB' : '';
  const dur = i.duration_s ? `${Math.floor(i.duration_s / 60)}:${String(Math.round(i.duration_s % 60)).padStart(2, '0')}` : '';
  const facts = [
    ['Format', i.format], ['Sample rate', i.sample_rate ? (i.sample_rate / 1000) + ' kHz' : ''],
    ['Bit depth', i.bit_depth ? i.bit_depth + '-bit' : ''], ['Channels', i.channels],
    ['Bitrate', i.bitrate ? Math.round(i.bitrate / 1000) + ' kbps' : ''], ['Duration', dur], ['Size', mb],
  ].filter(f => f[1]);
  $('analyze-info').innerHTML = facts.map(([k, v]) =>
    `<span><span class="u-c-muted">${escHtml(k)}</span> ${escHtml(String(v))}</span>`).join('');
  $('analyze-img').src = res.spectrogram;
  $('analyze-img').classList.remove('hidden');
  $('analyze-status').classList.add('hidden');
}

function closeAnalyze() { $('analyze-modal').classList.add('hidden'); }

// ── Library reorganiser ─────────────────────────────────────────────────────
let reorgPlan = null;
let reorgRoot = '';

async function openReorganizeDialog() {
  reorgRoot = ($('local-path-input')?.value || '').trim();
  if (!reorgRoot) { toastMgr.info('Scan a folder first.'); return; }
  const defaults = await window.pywebview?.api?.get_reorganize_defaults?.();
  if (!defaults) { toastMgr.error('Could not load the organizer.'); return; }
  if (!$('reorg-template').value) $('reorg-template').value = defaults.template;
  $('reorg-hint').textContent = 'Placeholders: ' + defaults.placeholders.map(p => '{' + p + '}').join(' ');
  $('reorg-root').textContent = reorgRoot;
  invalidateReorganizePlan();
  $('reorg-modal').classList.remove('hidden');
}

function closeReorganize() { $('reorg-modal').classList.add('hidden'); }

function invalidateReorganizePlan() {
  reorgPlan = null;
  $('reorg-apply-btn').disabled = true;
  $('reorg-summary').textContent = '';
  $('reorg-list').innerHTML = '';
}

async function previewReorganize() {
  const btn = $('reorg-preview-btn');
  btn.disabled = true;
  const res = await window.pywebview?.api?.plan_library_reorganize?.(reorgRoot, $('reorg-template').value);
  btn.disabled = false;
  if (!res || res.status !== 'ok') {
    $('reorg-summary').textContent = res?.error || 'Could not build the plan.';
    return;
  }
  reorgPlan = res.moves;
  const count = s => reorgPlan.filter(m => m.status === s).length;
  $('reorg-summary').textContent =
    `${count('move')} to move · ${count('same')} already in place · ${count('conflict')} skipped (destination exists)`;
  const rel = p => p.startsWith(reorgRoot) ? p.slice(reorgRoot.length).replace(/^[\\/]/, '') : p;
  $('reorg-list').innerHTML = reorgPlan.filter(m => m.status !== 'same').slice(0, 300).map(m =>
    `<div${m.status === 'conflict' ? ' data-tone="error"' : ''}>${escHtml(rel(m.src))} → ${escHtml(rel(m.dest))}</div>`).join('');
  $('reorg-apply-btn').disabled = count('move') === 0;
}

async function applyReorganize() {
  if (!reorgPlan) return;
  const btn = $('reorg-apply-btn');
  btn.disabled = true;
  btn.textContent = 'Moving…';
  const res = await window.pywebview?.api?.apply_library_reorganize?.(reorgRoot, reorgPlan);
  btn.textContent = 'Move files';
  if (!res || res.status !== 'ok') {
    toastMgr.error(res?.error || 'Could not move the files.');
    return;
  }
  const ok = res.results.filter(r => r.success);
  res.results.filter(r => !r.success).slice(0, 3).forEach(r => toastMgr.error(`${r.src.split(/[\\/]/).pop()}: ${r.error}`));
  ok.forEach(r => {
    const item = localScanData.find(e => e.file_path === r.src);
    if (item) item.file_path = r.dest;
  });
  toastMgr.success(`Moved ${ok.length} file(s).`);
  closeReorganize();
  renderLocalTracks();
  updateLocalSelection();
}

// ── Weekly releases (Following page) ────────────────────────────────────────
let weeklyLoading = false;

async function loadWeeklyReleases(refresh) {
  const status = $('weekly-status');
  const list = $('weekly-list');
  if (!status || !list || weeklyLoading) return;
  if (!window.pywebview?.api?.get_weekly_releases) {
    status.textContent = 'Not available in this build.';
    return;
  }
  weeklyLoading = true;
  $('weekly-refresh-btn').disabled = true;
  status.textContent = 'Checking the artists you follow…';
  list.innerHTML = '';
  try {
    const res = await window.pywebview.api.get_weekly_releases(parseInt($('weekly-days').value) || 7, !!refresh);
    if (!res || res.status !== 'ok') {
      status.textContent = res?.error || 'Could not load the releases.';
      return;
    }
    if (!res.followed) {
      status.textContent = 'Follow an artist below to see their new releases here.';
      return;
    }
    status.textContent = res.releases.length
      ? `${res.releases.length} release(s) from ${res.followed} followed artist(s).`
      : `Nothing new from the ${res.followed} artist(s) you follow in this period.`;
    if (res.unavailable?.length) {
      status.textContent += ` Could not check: ${res.unavailable.join(', ')}.`;
    }
    list.innerHTML = res.releases.map((r, i) => `
      <div class="weekly-row">
        <div class="weekly-day">${escHtml(r.day)}</div>
        <div class="weekly-info">
          <div class="weekly-title">${escHtml(r.title || '—')}</div>
          <div class="weekly-artist">${escHtml(r.artist)} · ${escHtml(r.type)}</div>
        </div>
        <button type="button" class="act-btn secondary u-p-5-12 u-fs-12" data-weekly="${i}">Open</button>
      </div>`).join('');
    list.querySelectorAll('[data-weekly]').forEach(btn => btn.addEventListener('click', () => {
      const rel = res.releases[parseInt(btn.dataset.weekly)];
      if (rel) { switchView('home'); goToUrl(rel.url); }
    }));
  } catch (e) {
    status.textContent = 'Could not load the releases.';
  } finally {
    weeklyLoading = false;
    $('weekly-refresh-btn').disabled = false;
  }
}

// ── Artist concerts ─────────────────────────────────────────────────────────
let g_artistConcerts = [];

function renderConcertsButton(artistName) {
  document.getElementById('concerts-btn')?.remove();
  if (!g_artistConcerts.length) return;
  const actions = $('album-actions');
  if (!actions) return;
  const btn = document.createElement('button');
  btn.id = 'concerts-btn';
  btn.type = 'button';
  btn.className = 'act-btn secondary';
  btn.title = 'Upcoming concerts';
  btn.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg> Concerts (${g_artistConcerts.length})`;
  btn.onclick = () => openConcerts($('album-title')?.textContent?.trim());
  actions.appendChild(btn);
}

// The venue's own wall-clock date and time, not the viewer's.
function concertWhen(c) {
  const hasZone = c.has_time && c.time_zone && /(Z|[+-]\d{2}:?\d{2})$/.test(c.start_at);
  const d = new Date(c.has_time ? c.start_at : c.start_at + 'T00:00:00Z');
  try {
    const opts = { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' };
    if (c.has_time) { opts.hour = '2-digit'; opts.minute = '2-digit'; }
    opts.timeZone = hasZone ? c.time_zone : (c.has_time ? undefined : 'UTC');
    return new Intl.DateTimeFormat(undefined, opts).format(d);
  } catch (e) {
    return c.start_at;
  }
}

function concertIcs(c, artist) {
  const esc = t => String(t || '').replace(/[\\,;]/g, m => '\\' + m).replace(/\n/g, '\\n');
  const fmt = dt => dt.toISOString().replace(/[-:]/g, '').replace(/\.\d{3}/, '');
  const lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Mornye//Concerts//EN', 'BEGIN:VEVENT',
    `UID:${esc(c.id)}@mornye`, `DTSTAMP:${fmt(new Date())}`];
  if (c.has_time) {
    const start = new Date(c.start_at);
    lines.push(`DTSTART:${fmt(start)}`, `DTEND:${fmt(new Date(start.getTime() + 3 * 3600e3))}`);
  } else {
    const day = c.start_at.replace(/-/g, '');
    const next = new Date(c.start_at + 'T00:00:00Z'); next.setUTCDate(next.getUTCDate() + 1);
    lines.push(`DTSTART;VALUE=DATE:${day}`, `DTEND;VALUE=DATE:${next.toISOString().slice(0, 10).replace(/-/g, '')}`);
  }
  lines.push(`SUMMARY:${esc(artist || 'Concert')}`, `LOCATION:${esc([c.venue, c.location].filter(Boolean).join(', '))}`);
  if (c.url) lines.push(`URL:${c.url}`);
  lines.push('END:VEVENT', 'END:VCALENDAR');
  return lines.join('\r\n');
}

function downloadConcertIcs(index) {
  const c = g_artistConcerts[index];
  if (!c) return;
  const blob = new Blob([concertIcs(c, $('album-title')?.textContent?.trim())], { type: 'text/calendar' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'concert.ics';
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

function openConcerts(artistName) {
  $('concerts-title').textContent = `Concerts${artistName ? ' — ' + artistName : ''}`;
  $('concerts-list').innerHTML = g_artistConcerts.map((c, i) => `
    <div class="weekly-row">
      <div class="weekly-info">
        <div class="weekly-title">${escHtml(concertWhen(c))}</div>
        <div class="weekly-artist">${escHtml([c.venue, c.location].filter(Boolean).join(' · '))}</div>
      </div>
      <button type="button" class="act-btn secondary u-p-5-12 u-fs-12" data-onclick="downloadConcertIcs(${i})" title="Download a calendar event">Add to calendar</button>
      ${c.url ? `<button type="button" class="act-btn secondary u-p-5-12 u-fs-12" data-onclick="openExternalUrl(${escHtml(JSON.stringify(c.url))})">Details</button>` : ''}
    </div>`).join('');
  $('concerts-modal').classList.remove('hidden');
}

function closeConcerts() { $('concerts-modal').classList.add('hidden'); }

function openExternalUrl(url) {
  const safe = httpUrlOrNull(url);
  if (!safe) return;
  if (window.pywebview?.api?.open_url) window.pywebview.api.open_url(safe);
  else window.open(safe, '_blank', 'noopener');
}

// ── Lyrics translation ──────────────────────────────────────────────────────
let aiConfig = null;

async function ensureTranslationLanguages() {
  const sel = $('tagedit-lang');
  if (!sel || sel.options.length) return;
  aiConfig = await window.pywebview?.api?.get_lyrics_ai_config?.();
  if (!aiConfig) return;
  sel.innerHTML = aiConfig.languages.map(l => `<option value="${escHtml(l.code)}">${escHtml(l.name)}</option>`).join('');
  const prefer = (navigator.language || 'en').slice(0, 2);
  if ([...sel.options].some(o => o.value === prefer)) sel.value = prefer;
}

// The lyrics in the editor, looked up again from the lyrics services. Only
// the text box changes; Save writes it to the file like a hand edit.
// "Settings order" first (every service ticked in Settings, ranked), then
// each lyrics service on its own: the built-in ones and any extension.
async function fillTagEditorLyricsProviders() {
  const sel = $('tagedit-lyrics-provider');
  if (!sel) return;
  const keep = sel.value;
  let exts = [];
  try { exts = (await window.pywebview?.api?.get_lyrics_extensions?.())?.extensions || []; } catch (e) { exts = []; }
  const opts = [{ id: '', label: 'Settings order' }, ...ALL_LYRICS, ...exts];
  sel.innerHTML = opts.map(o => `<option value="${escHtml(o.id)}">${escHtml(o.label)}</option>`).join('');
  if ([...sel.options].some(o => o.value === keep)) sel.value = keep;
}

async function fetchTagEditorLyrics() {
  if (!tagEditState) return;
  const path = tagEditState.path;
  const tags = {};
  document.querySelectorAll('#tagedit-fields [data-tag]').forEach(el => {
    if (['TITLE', 'ARTIST', 'ALBUMARTIST', 'ALBUM', 'ISRC'].includes(el.dataset.tag)) tags[el.dataset.tag] = el.value.trim();
  });
  const btn = $('tagedit-fetch-lyrics-btn');
  btn.disabled = true;
  btn.textContent = 'Searching…';
  const provider = $('tagedit-lyrics-provider')?.value || '';
  const res = await window.pywebview?.api?.fetch_file_lyrics?.(path, tags, provider);
  btn.disabled = false;
  btn.textContent = 'Fetch again';
  if (!tagEditState || tagEditState.path !== path) return;
  if (!res || res.status !== 'ok') { toastMgr.error(res?.error || 'Could not fetch the lyrics.'); return; }
  $('tagedit-lyrics').value = res.lyrics;
  $('tagedit-lyrics-source').textContent = res.provider ? `· from ${res.provider}, not saved yet` : '· not saved yet';
  toastMgr.info(`Lyrics found${res.provider ? ' on ' + res.provider : ''}. Press Save to write them to the file.`);
}

async function translateTagEditorLyrics() {
  const lyrics = $('tagedit-lyrics').value;
  if (!lyrics.trim()) { toastMgr.info('There are no lyrics to translate.'); return; }
  aiConfig = await window.pywebview?.api?.get_lyrics_ai_config?.();
  if (!aiConfig?.configured) { toastMgr.info('Set up an AI provider first.'); openAiConfig(); return; }
  const btn = $('tagedit-translate-btn');
  btn.disabled = true;
  btn.textContent = 'Translating…';
  const res = await window.pywebview.api.translate_lyrics(lyrics, $('tagedit-lang').value);
  btn.disabled = false;
  btn.textContent = 'Translate';
  if (!res || res.status !== 'ok') { toastMgr.error(res?.error || 'Translation failed.'); return; }
  $('tagedit-translation').value = res.lyrics;
  $('tagedit-translation-wrap').classList.remove('hidden');
}

async function saveTagEditorTranslation() {
  if (!tagEditState) return;
  const res = await window.pywebview.api.save_translated_lyrics(
    tagEditState.path, $('tagedit-lang').value, $('tagedit-translation').value, $('tagedit-lyrics').value);
  if (res?.status === 'ok') toastMgr.success('Saved ' + res.path.split(/[\\/]/).pop());
  else toastMgr.error(res?.error || 'Could not save the translation.');
}

async function openAiConfig() {
  aiConfig = await window.pywebview?.api?.get_lyrics_ai_config?.();
  if (!aiConfig) return;
  $('aiconf-provider').innerHTML = aiConfig.providers.map(p =>
    `<option value="${escHtml(p.id)}">${escHtml(p.label)}</option>`).join('');
  $('aiconf-provider').value = aiConfig.provider;
  $('aiconf-model').value = aiConfig.model;
  $('aiconf-url').value = aiConfig.base_url;
  $('aiconf-key').value = '';
  updateAiConfigFields();
  $('aiconf-modal').classList.remove('hidden');
}

function closeAiConfig() { $('aiconf-modal').classList.add('hidden'); }

function updateAiConfigFields() {
  const p = aiConfig?.providers.find(x => x.id === $('aiconf-provider').value);
  if (!p) return;
  $('aiconf-model').placeholder = p.model || 'model name';
  $('aiconf-url-wrap').classList.toggle('hidden', p.id !== 'custom');
  $('aiconf-key-note').textContent = aiConfig.has_key
    ? 'A key is stored. Leave this empty to keep it.'
    : (p.key ? 'Required.' : 'Optional for a local server.');
}

async function saveAiConfig() {
  const key = $('aiconf-key').value;
  const res = await window.pywebview.api.save_lyrics_ai_config(
    $('aiconf-provider').value, $('aiconf-model').value, $('aiconf-url').value,
    key ? key : null);
  if (res?.status === 'ok') {
    toastMgr.success(res.configured ? 'AI provider saved.' : 'Saved — still missing a key or model.');
    closeAiConfig();
  } else {
    toastMgr.error(res?.error || 'Could not save the settings.');
  }
}

// ── Player ──────────────────────────────────────────────────────────────────
// Plays the file as stored: no EQ, no normalisation, no Web Audio graph, so the
// bytes reach the output untouched. Formats the window cannot decode are played
// from a lossless FLAC copy made by the backend (see core/playback.py).
let playerQueue = [];
let playerIndex = -1;       // index into playerQueue of the track that is loaded
let playerOrder = [];       // indices into playerQueue, in the order they play
let playerPos = -1;         // where playerIndex sits in playerOrder
let playerShuffle = false;
let playerRepeat = 'off';   // 'off' | 'all' | 'one'
let playerToken = 0;
let playerSeeking = false;

try {
  const saved = JSON.parse(localStorage.getItem('mornye-player-modes') || '{}');
  playerShuffle = !!saved.shuffle;
  if (['off', 'all', 'one'].includes(saved.repeat)) playerRepeat = saved.repeat;
} catch (e) { /* a per-browser convenience only */ }

function fmtTime(sec) {
  if (!Number.isFinite(sec) || sec < 0) return '0:00';
  return `${Math.floor(sec / 60)}:${String(Math.floor(sec % 60)).padStart(2, '0')}`;
}

// The play order: the queue as it is, or — with shuffle — a random permutation
// with the chosen track first, so shuffling never skips the one you picked.
function playerBuildOrder(first) {
  const idx = playerQueue.map((_, i) => i);
  if (playerShuffle) {
    for (let i = idx.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [idx[i], idx[j]] = [idx[j], idx[i]];
    }
    if (first != null && first >= 0) {
      const at = idx.indexOf(first);
      [idx[0], idx[at]] = [idx[at], idx[0]];
    } else if (idx.length > 1 && idx[0] === playerIndex) {
      [idx[0], idx[1]] = [idx[1], idx[0]];       // a new round must not open with the last track
    }
  }
  playerOrder = idx;
  playerPos = first != null && first >= 0 ? idx.indexOf(first) : 0;
}

function updatePlayerModes() {
  document.querySelectorAll('#pl-shuffle, #np-shuffle').forEach(b => {
    b.classList.toggle('on', playerShuffle);
    b.setAttribute('aria-pressed', String(playerShuffle));
  });
  document.querySelectorAll('#pl-repeat, #np-repeat').forEach(b => {
    b.dataset.mode = playerRepeat;
    b.classList.toggle('on', playerRepeat !== 'off');
    b.setAttribute('aria-pressed', String(playerRepeat !== 'off'));
    b.title = { off: 'Repeat: off', all: 'Repeat all', one: 'Repeat this track' }[playerRepeat];
  });
  // A remote shows the playing device's modes; they are not this browser's.
  if (playerEngine.kind !== 'remote') {
    try { localStorage.setItem('mornye-player-modes', JSON.stringify({ shuffle: playerShuffle, repeat: playerRepeat })); } catch (e) {}
  }
  playerRefreshNext();
  connectSchedule();
}

function playerToggleShuffle() {
  if (playerEngine.kind === 'remote') { connectSendRemote('shuffle', { enabled: !playerShuffle }); return; }
  playerShuffle = !playerShuffle;
  if (playerQueue.length) playerBuildOrder(playerIndex >= 0 ? playerIndex : null);
  updatePlayerModes();
  if (!$('np-queue').classList.contains('hidden')) npRenderQueue();
}

function playerCycleRepeat() {
  if (playerEngine.kind === 'remote') {
    connectSendRemote('repeat', { mode: connectRepeatOut({ off: 'all', all: 'one', one: 'off' }[playerRepeat]) });
    return;
  }
  playerRepeat = { off: 'all', all: 'one', one: 'off' }[playerRepeat];
  updatePlayerModes();
}

// `shuffle`: true/false sets the mode (the Play all / Shuffle buttons); leave
// it undefined to keep whatever the player is set to (clicking a row).
function startPlayback(items, index, shuffle, openView) {
  if (!items.length) return;
  if (playerEngine.kind === 'remote') {
    // Spotify Connect: a song picked on a remote plays where the music is.
    connectStartRemote(items, index, shuffle);
    if (openView) openNowPlaying();
    return;
  }
  playerQueue = items;
  if (shuffle === true || shuffle === false) playerShuffle = shuffle;
  updatePlayerModes();
  const first = shuffle === true ? Math.floor(Math.random() * items.length) : index;
  playerIndex = -1;
  playerBuildOrder(first);
  if (openView) openNowPlaying();
  loadPlayerTrack(first);
}

function playLocal(index) {
  const items = localScanData.map(e => ({
    path: e.file_path,
    title: e.old_title || e.guessed_title || e.file_path.split(/[\\/]/).pop(),
    artist: e.old_artist || e.guessed_artist || '',
  }));
  startPlayback(items, index, undefined, true);
}

// ── Playback engine ─────────────────────────────────────────────────────────
// Everything the player asks of the audio goes through `playerEngine`: the
// queue, the bars, the Now Playing view, the synced lyrics and the Media
// Session never touch the <audio> element directly. The adapter keeps the
// listeners itself and re-emits the events of whichever engine is active, so
// the engine can be swapped without registering them again: the window's own
// <audio>, or libmpv in the desktop app (docs/native-player-plan.md). The
// surface is the part of HTMLMediaElement the player uses, plus `advanced`
// (a gapless change to the queued track) and `output` (what reaches the
// device), which only the native engine sends.
const PLAYER_ENGINE_EVENTS = ['play', 'pause', 'timeupdate', 'ended', 'loadedmetadata', 'seeked', 'error'];

function playerBrowserEngine(element) {
  return {
    kind: 'browser',
    attach(emit) {
      for (const type of PLAYER_ENGINE_EVENTS) element.addEventListener(type, () => emit(type));
    },
    get hasSource() { return !!element.getAttribute('src'); },
    load(url) { element.src = url; },
    clear() {
      element.pause();
      element.removeAttribute('src');
      element.load();
    },
    play() { return element.play(); },
    pause() { element.pause(); },
    get paused() { return element.paused; },
    get currentTime() { return element.currentTime; },
    set currentTime(t) { element.currentTime = t; },
    get duration() { return element.duration; },
    get volume() { return element.volume; },
    set volume(v) { element.volume = v; },
    get muted() { return element.muted; },
    set muted(v) { element.muted = v; },
    get playbackRate() { return element.playbackRate; },
    set playbackRate(r) { element.playbackRate = r; },
  };
}

// The native engine: libmpv in the backend (core/native_player.py), driven
// through the `native_player_*` API and heard back through
// window.app_native_player_event, which hands each event to receive().
// Calls are chained so they reach the backend in the order they were made
// (pywebview runs each call on a thread of its own). The position arrives
// about four times a second; between two updates it is extrapolated, since
// the synced lyrics read it on every frame. An update reaches the page a
// little after mpv took it (more so on a network share, where mpv also runs
// in uneven steps), so it is often behind the extrapolated position: a small
// step back like that is not shown; the position holds until mpv catches up.
// Only a real gap (over a second) moves it back.
function playerNativeEngine(api, clock = () => performance.now()) {
  let emit = () => {};
  let chain = Promise.resolve();
  let src = '', paused = true, ended = false, time = 0, at = 0, duration = NaN;
  let volume = 1, muted = false, rate = 1, seekUntil = 0, output = null, loadGuardUntil = 0, staleAt = NaN;
  let floor = 0; // the position never shows less than this until the next settle
  const DRIFT_MAX_S = 1;
  const call = (name, ...args) => {
    const run = chain.then(() => api[name](...args));
    chain = run.catch(() => {});
    return run.then(r => {
      if (r && r.status && r.status !== 'ok') throw new Error(r.error || r.status);
      return r;
    });
  };
  const command = (name, value = null) => call('native_player_command', name, value)
    .catch(e => emit('error', { message: e.message }));
  const now = () => clock();
  const position = () => {
    if (paused || !src) return time;
    const t = Math.max(floor, time + (now() - at) / 1000 * rate);
    return Number.isFinite(duration) ? Math.min(duration, t) : t;
  };
  const settle = (t) => { if (typeof t === 'number' && Number.isFinite(t)) time = t; at = now(); floor = 0; };
  const engine = {
    kind: 'native',
    attach(fn) { emit = fn; },
    get output() { return output; },
    get hasSource() { return !!src; },
    // mpv starts playing as soon as it opens the file, so a load is a play.
    load(path, next) {
      const wasPaused = paused;
      // Where the last file was: a late update from it says about this.
      staleAt = src ? position() : NaN;
      src = path; paused = false; ended = false; duration = NaN; output = null;
      settle(0);
      loadGuardUntil = now() + 1500;
      call('native_player_load', path, next || null)
        .catch(e => { paused = true; emit('pause'); emit('error', { message: e.message }); });
      // Set while nothing was loaded (mpv starts only on the first file).
      if (volume !== 1) command('volume', volume);
      if (muted) command('mute', true);
      if (rate !== 1) command('speed', rate);
      if (wasPaused) emit('play');
    },
    setNext(path) {
      if (src) call('native_player_set_next', path || null).catch(() => {});
    },
    clear() {
      src = ''; paused = true; ended = false; duration = NaN; output = null;
      settle(0);
      command('stop');
    },
    play() {
      if (!src || !paused) return Promise.resolve();
      if (ended) { // mpv is idle after the end: open the file again
        const from = time >= (duration || Infinity) - 0.05 ? 0 : time;
        engine.load(src, null);
        if (from > 0) engine.currentTime = from;
        return Promise.resolve();
      }
      settle(time);
      paused = false;
      command('play');
      emit('play');
      return Promise.resolve();
    },
    pause() {
      if (paused) return;
      settle(position());
      paused = true;
      if (src) command('pause');
      emit('pause');
    },
    get paused() { return paused; },
    get currentTime() { return position(); },
    set currentTime(t) {
      settle(Math.max(0, t));
      if (!src) return;
      if (ended) { ended = false; paused = true; engine.load(src, null); }
      seekUntil = now() + 1500; // older positions still on their way are dropped
      command('seek', time);
    },
    get duration() { return duration; },
    get volume() { return volume; },
    set volume(v) { volume = v; if (src) command('volume', v); },
    get muted() { return muted; },
    set muted(v) { muted = !!v; if (src) command('mute', muted); },
    get playbackRate() { return rate; },
    set playbackRate(r) { settle(position()); rate = r; if (src) command('speed', r); },
    setAbLoop(a, b) { command('ab_loop', a == null || b == null ? null : [a, b]); },
    receive(evt) {
      switch (evt?.type) {
        case 'timeupdate':
          if (now() < seekUntil || paused) return;
          // A position the last file sent just before the change can arrive
          // after the new one was opened; it would put the new track (and
          // its lyrics) at the old track's minute.
          if (now() < loadGuardUntil && Math.abs(evt.time - staleAt) < 1.5) return;
          {
            const shown = position();
            settle(evt.time);
            if (shown > time && shown - time < DRIFT_MAX_S) floor = shown;
          }
          emit('timeupdate'); return;
        case 'play':
          if (paused && !ended) { paused = false; settle(evt.time); emit('play'); }
          return;
        case 'pause':
          if (!paused) { paused = true; settle(evt.time); emit('pause'); }
          return;
        case 'loadedmetadata':
          if (Number.isFinite(evt.duration)) duration = evt.duration;
          emit('loadedmetadata'); return;
        case 'seeked':
          seekUntil = 0; settle(evt.time); emit('seeked'); emit('timeupdate'); return;
        case 'ended': // as <audio>: pause, then ended
          settle(Number.isFinite(duration) ? duration : time);
          ended = true;
          if (!paused) { paused = true; emit('pause'); }
          emit('ended'); return;
        case 'advanced':
          // The duration comes with the change (core/native_player.py): a
          // track that repeats never reports it again on its own.
          src = evt.path; ended = false; paused = false; output = null;
          duration = Number.isFinite(evt.duration) ? evt.duration : NaN;
          settle(0);
          emit('advanced', { path: evt.path });
          if (Number.isFinite(duration)) emit('loadedmetadata');
          return;
        case 'output':
          output = evt; emit('output', evt); return;
        case 'error':
          emit('error', { message: evt.message }); return;
        case 'warning':
          emit('warning', { message: evt.message }); return;
        case 'media': // a media key or Now Playing command that reached mpv
          emit('media', { action: evt.action, seekTime: evt.seekTime }); return;
      }
    },
  };
  return engine;
}

function createPlayerEngine(initial) {
  const listeners = new Map();
  const attached = new Set();
  let active = null;
  const engine = {
    get kind() { return active.kind; },
    get active() { return active; },
    addEventListener(type, fn) {
      if (!listeners.has(type)) listeners.set(type, new Set());
      listeners.get(type).add(fn);
    },
    removeEventListener(type, fn) { listeners.get(type)?.delete(fn); },
    // Makes `next` the engine that plays. The old one is stopped and
    // emptied; its late events are dropped. Volume, mute and speed carry over.
    use(next) {
      if (next === active) return;
      if (active) {
        const { volume, muted, playbackRate } = active;
        if (active.hasSource) active.clear();
        next.volume = volume;
        next.muted = muted;
        next.playbackRate = playbackRate;
      }
      active = next;
      if (!attached.has(next)) {
        attached.add(next);
        next.attach((type, detail) => {
          if (active !== next) return;
          for (const fn of [...(listeners.get(type) || [])]) {
            try { fn(detail); } catch (e) { console.error(e); }
          }
        });
      }
    },
    get hasSource() { return active.hasSource; },
    load(url, next, meta) { active.load(url, next, meta); },
    setNext(path) { active.setNext?.(path); },
    setAbLoop(a, b) { active.setAbLoop?.(a, b); },
    get output() { return active.output || null; },
    clear() { active.clear(); },
    play() { return active.play(); },
    pause() { active.pause(); },
    get paused() { return active.paused; },
    get currentTime() { return active.currentTime; },
    set currentTime(t) { active.currentTime = t; },
    get duration() { return active.duration; },
    get volume() { return active.volume; },
    set volume(v) { active.volume = v; },
    get muted() { return active.muted; },
    set muted(v) { active.muted = v; },
    get playbackRate() { return active.playbackRate; },
    set playbackRate(r) { active.playbackRate = r; },
  };
  engine.use(initial);
  return engine;
}

const playerBrowser = playerBrowserEngine($('player-audio'));
const playerEngine = createPlayerEngine(playerBrowser);
let playerNative = null;          // the native engine, once the backend has one
let playerNativeStatus = null;    // native_player_status(): availability and options
let playerSourceQuality = '';     // the file's own quality label, from prepare_playback

window.app_native_player_event = (evt) => playerNative?.receive(evt);

// Desktop window only: the native engine when the setting asks for it (or
// "auto") and libmpv is there; otherwise the window's own engine. A track
// that is playing restarts on the new engine.
async function playerSetupEngine() {
  const api = window.pywebview?.api;
  if (window.__mornyeWeb || !api?.native_player_status) return;
  // While casting the receiver plays; the local engine is chosen again
  // when casting stops.
  if (playerEngine.kind === 'cast') return;
  let status = null;
  try { status = await api.native_player_status(); } catch (e) { status = null; }
  playerNativeStatus = status;
  const want = status?.options?.engine || 'auto';
  const native = !!status?.available && want !== 'browser';
  const target = native ? (playerNative ||= playerNativeEngine(api)) : playerBrowser;
  if (want === 'native' && !status?.available) {
    toastMgr.warning(`Native player unavailable: ${status?.reason || 'libmpv not found'}. Using the window's engine.`);
  }
  if (playerEngine.active === target) return;
  const playing = playerIndex >= 0 && playerEngine.hasSource;
  playerEngine.use(target);
  playerMediaSessionActive(!native);
  if (playing) loadPlayerTrack(playerIndex);
}

// ── Cast: DLNA/UPnP and FCast receivers (from Mornye mobile 5.4.5) ─────────
// Casting swaps the player's engine for one that drives the receiver
// (api_mixins/cast.py, core/cast): the queue, lyrics and Now Playing stay
// here and follow the receiver's clock; when the receiver says a song has
// finished, the engine's "ended" moves the queue on as it does locally.
// Desktop only: the web player has no cast methods, so no button.
var playerCast = null;          // the cast engine, while casting
var castLocalEngine = null;     // the engine to go back to
var castDevices = [];
var castStartAt = 0;            // where the next cast load starts, once
var castPicker = null;          // the button the picker is open on

// What a receiver status says for the song loaded as `token`.
function castStateFromStatus(snap, token) {
  if (!snap || !snap.connected) return { lost: true };
  const ours = token == null || snap.token == null || snap.token === token;
  return {
    lost: false,
    ours,
    playing: snap.state === 2,
    paused: snap.state === 3 || snap.state === 1,
    buffering: snap.state === 4 || snap.state === 5,
    finished: ours && !!snap.finished,
    failed: ours && !!snap.failed,
    time: Math.max(0, (Number(snap.position) || 0) / 1000),
    duration: (Number(snap.duration) || 0) > 0 ? snap.duration / 1000 : NaN,
    volume: typeof snap.volume === 'number' ? snap.volume : null,
  };
}

function castDeviceLabel(device) {
  const kind = device.kind === 'fcast' ? 'FCast' : 'DLNA';
  return device.model && device.model !== 'FCast' ? `${device.name} · ${kind} · ${device.model}` : `${device.name} · ${kind}`;
}

function playerCastEngine(api, clock = () => performance.now()) {
  let emit = () => {};
  let src = '', paused = true, ended = false, time = 0, at = 0, duration = NaN;
  let token = null, volume = 1, muted = false, guardUntil = 0, tick = 0, volumeTimer = 0;
  const call = (name, ...args) => Promise.resolve(api[name](...args)).then((r) => {
    if (r && r.status && r.status !== 'ok') throw new Error(r.error || r.status);
    return r;
  });
  const fail = (e) => { emit('error', { message: e.message || String(e) }); };
  const position = () => {
    if (paused || !src) return time;
    const t = time + (clock() - at) / 1000;
    return Number.isFinite(duration) ? Math.min(duration, t) : t;
  };
  const settle = (t) => { time = Math.max(0, t); at = clock(); };
  const ticking = (on) => {
    clearInterval(tick);
    tick = on ? setInterval(() => emit('timeupdate'), 500) : 0;
  };
  const engine = {
    kind: 'cast',
    attach(fn) { emit = fn; },
    get hasSource() { return !!src; },
    get output() { return null; },
    load(path, _next, meta = {}) {
      src = path; ended = false; duration = Number(meta.duration) > 0 ? Number(meta.duration) : NaN;
      token = null; guardUntil = clock() + 4000;
      settle(meta.start || 0);
      const wasPaused = paused;
      paused = false;
      call('cast_load', path, meta, meta.start || 0, true)
        .then((r) => { token = r.cast?.token ?? null; })
        .catch((e) => { paused = true; ticking(false); emit('pause'); fail(e); });
      ticking(true);
      if (wasPaused) emit('play');
    },
    setNext() {},
    clear() {
      src = ''; paused = true; ended = false; ticking(false);
      call('cast_command', 'stop').catch(() => {});
    },
    play() {
      if (!src || !paused) return Promise.resolve();
      if (ended) { engine.load(src, null, { start: 0 }); return Promise.resolve(); }
      settle(time); paused = false; guardUntil = clock() + 4000;
      ticking(true);
      emit('play');
      return call('cast_command', 'play').catch(fail);
    },
    pause() {
      if (paused) return;
      settle(position()); paused = true; ticking(false);
      emit('pause');
      call('cast_command', 'pause').catch(fail);
    },
    get paused() { return paused; },
    get currentTime() { return position(); },
    set currentTime(t) {
      settle(Math.max(0, t));
      if (src) call('cast_command', 'seek', time).catch(fail);
      emit('timeupdate'); emit('seeked');
    },
    get duration() { return duration; },
    get volume() { return volume; },
    set volume(v) {
      volume = Math.max(0, Math.min(1, v));
      clearTimeout(volumeTimer);
      volumeTimer = setTimeout(() => call('cast_command', 'volume', muted ? 0 : volume).catch(() => {}), 150);
    },
    get muted() { return muted; },
    set muted(m) { muted = !!m; engine.volume = volume; },
    get playbackRate() { return 1; },
    set playbackRate(_r) {},
    receive(snap) {
      const s = castStateFromStatus(snap, token);
      if (s.lost) { ticking(false); stopCasting({ lost: true }); return; }
      if (!s.ours || !src) return;
      if (Number.isFinite(s.duration) && s.duration !== duration) { duration = s.duration; emit('loadedmetadata'); }
      if (s.failed) { paused = true; ticking(false); emit('pause'); fail(new Error(`${snap.deviceName || 'The device'} could not play this song`)); return; }
      if (s.finished && !ended) { ended = true; paused = true; settle(duration || s.time); ticking(false); emit('pause'); emit('ended'); return; }
      if (s.playing) {
        settle(s.time);
        if (paused) { paused = false; ticking(true); emit('play'); }
      } else if (s.paused && !paused && clock() > guardUntil) {
        // Paused on the receiver itself (its remote, its own buttons).
        settle(s.time); paused = true; ticking(false); emit('pause');
      } else if (!s.buffering && paused) {
        settle(s.time);
      }
      emit('timeupdate');
    },
    dispose() { ticking(false); clearTimeout(volumeTimer); },
  };
  return engine;
}

window.app_cast_status = (snap) => playerCast?.receive(snap);
window.app_cast_devices = (list) => {
  castDevices = Array.isArray(list) ? list : [];
  // Redrawn only while the picker is still the open menu.
  renderConnectPanel();
};

function castAvailable() {
  return !window.__mornyeWeb && typeof window.pywebview?.api?.cast_devices === 'function';
}

// Connect, as in Spotify: a panel on the right with where the music plays on
// top (this device, another Connect device, a receiver), in the signal
// colour, and below it the account's other devices and (desktop) the DLNA and
// FCast receivers this computer can cast to. A click moves playback there;
// the panel stays open and follows what changes.
var connectPanelOpen = false;

async function openCastPicker(button, refresh = false) {
  if (!refresh && connectPanelOpen) { closeConnectPanel(); return; }
  const api = window.pywebview?.api;
  const cast = castAvailable();
  // --web: asking for the list also has the server look for receivers.
  if (window.__mornyeWeb && !refresh && connect.ready) {
    try { const snap = await connectApi()?.connect_devices(); if (snap?.status === 'ok') connectDevices(snap); } catch (e) {}
  }
  if (!cast && !connectOthers().length) return;
  castPicker = button;
  connectPanelOpen = true;
  document.querySelectorAll('.cast-btn').forEach(b => b.setAttribute('aria-expanded', 'true'));
  $('connect-panel').classList.remove('hidden');
  renderConnectPanel();
  if (cast && !refresh) {
    try { castDevices = (await api.cast_devices())?.devices || castDevices; } catch (e) { /* keep the last list */ }
    renderConnectPanel();
  }
}

function closeConnectPanel() {
  if (!connectPanelOpen) return;
  connectPanelOpen = false;
  castPicker = null;
  $('connect-panel').classList.add('hidden');
  document.querySelectorAll('.cast-btn').forEach(b => b.setAttribute('aria-expanded', 'false'));
}

var CONNECT_ICONS = {
  computer: '<rect x="3" y="4" width="18" height="12" rx="1.5"/><path d="M1.5 20h21"/>',
  browser: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
  phone: '<rect x="6.5" y="2.5" width="11" height="19" rx="2"/><path d="M11 18.5h2"/>',
  speaker: '<rect x="5.5" y="2.5" width="13" height="19" rx="2"/><circle cx="12" cy="14" r="3.5"/><circle cx="12" cy="6.5" r="1"/>',
};

function connectIcon(kind) {
  return `<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${CONNECT_ICONS[kind] || CONNECT_ICONS.speaker}</svg>`;
}

// Every place the music can play, as the panel lists it: `id` '' is here.
function connectPanelEntries() {
  const web = !!window.__mornyeWeb;
  const browser = web && connect.kind !== 'window';
  const entries = [{
    id: '', name: browser ? 'This browser' : 'This computer', icon: browser ? 'browser' : 'computer',
    note: '', run: () => connectPlayHere(),
  }];
  const icons = { window: 'computer', mornye: 'computer', browser: 'browser', phone: 'phone' };
  const notes = { window: 'Mornye', mornye: 'Mornye', browser: 'Browser', phone: 'Phone', server: 'Server', cast: 'Receiver' };
  for (const d of connectOthers()) {
    entries.push({ id: d.id, name: d.name, icon: icons[d.kind] || 'speaker', note: notes[d.kind] || '', run: () => connectTransferTo(d) });
  }
  if (castAvailable()) {
    for (const d of castDevices) {
      entries.push({ id: d.id, name: d.name, icon: 'speaker', note: d.kind === 'fcast' ? 'FCast' : 'DLNA', run: () => startCasting(d) });
    }
  }
  return entries;
}

function renderConnectPanel() {
  const body = $('connect-panel-body');
  if (!connectPanelOpen || !body) return;
  // Above the player bar, wherever the look puts it (the music look floats it).
  const bar = $('player-bar');
  const top = bar && !bar.classList.contains('hidden') ? bar.getBoundingClientRect().top : innerHeight;
  $('connect-panel').style.bottom = `${Math.max(12, innerHeight - top + 10)}px`;
  const current = playerCast && playerEngine.active === playerCast ? playerCastDeviceId
    : (playerEngine.kind === 'remote' ? connect.remoteId : '');
  const entries = connectPanelEntries();
  const active = entries.find(e => e.id === current) || entries[0];
  const others = entries.filter(e => e !== active);
  const playing = !playerEngine.paused && (playerEngine.kind === 'remote' || (playerIndex >= 0 && playerEngine.hasSource));
  const status = playing ? 'Playing' : (active.id ? 'Paused' : '');
  const row = (e, i) => `<button type="button" class="cp-device" data-i="${i}">${connectIcon(e.icon)}<span class="cp-name">${escHtml(e.name)}${e.note ? `<small>${escHtml(e.note)}</small>` : ''}</span></button>`;
  body.innerHTML = `
    <div class="cp-current">${connectIcon(active.icon)}<span class="cp-name">${escHtml(active.name)}${status ? `<small>${status}</small>` : ''}</span>${playing ? '<span class="cp-bars" aria-hidden="true"><i></i><i></i><i></i></span>' : ''}</div>
    ${others.length ? `<h3 class="cp-sub">Select a device</h3><div class="cp-list">${others.map(e => row(e, entries.indexOf(e))).join('')}</div>` : ''}
    ${castAvailable() ? `<p class="cp-hint">${castDevices.length ? 'Looking for more devices…' : 'Looking for DLNA and FCast devices…'}</p>` : ''}`;
  body.querySelectorAll('.cp-device').forEach((b) => {
    b.addEventListener('click', async () => {
      b.disabled = true;
      try { await entries[Number(b.dataset.i)]?.run(); } finally { renderConnectPanel(); }
    });
  });
}

(function initConnectPanel() {
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && connectPanelOpen) { e.stopPropagation(); closeConnectPanel(); }
  }, true);
  document.addEventListener('pointerdown', (e) => {
    if (!connectPanelOpen) return;
    if (e.target.closest?.('#connect-panel, .cast-btn')) return;
    closeConnectPanel();
  }, true);
})();

var playerCastDeviceId = '';

async function startCasting(device) {
  const api = window.pywebview?.api;
  castPicker = null;
  if (playerCast && playerCastDeviceId === device.id) return;
  if (playerEngine.kind === 'remote') { toastMgr.info('Move playback to this computer first, then cast it.'); return; }
  const at = playerEngine.hasSource ? playerEngine.currentTime : 0;
  const wasPlaying = playerEngine.hasSource && !playerEngine.paused;
  const res = await api.cast_connect(device.id);
  if (res?.status !== 'ok') { toastMgr.error(res?.error || `Could not connect to ${device.name}`); return; }
  if (!playerCast) castLocalEngine = playerEngine.active;
  playerCast ||= playerCastEngine(api);
  playerCastDeviceId = device.id;
  playerEngine.use(playerCast);
  playerMediaSessionActive(true);
  document.body.classList.add('is-casting');
  castButtonsShow(true, `Playing on ${device.name}`);
  toastMgr.success(`Playing on ${device.name}`);
  if (playerIndex >= 0) {
    castStartAt = at;
    await loadPlayerTrack(playerIndex);
    if (!wasPlaying) playerEngine.pause();
  }
}

async function stopCasting({ lost = false } = {}) {
  castPicker = null;
  if (!playerCast || playerEngine.active !== playerCast) {
    if (!lost) window.pywebview?.api?.cast_disconnect?.();
    return;
  }
  const at = playerCast.currentTime;
  const wasPlaying = !playerCast.paused;
  const name = castDevices.find(d => d.id === playerCastDeviceId)?.name || 'the device';
  playerCastDeviceId = '';
  playerEngine.use(castLocalEngine || playerBrowser);
  playerCast.dispose();
  playerCast = null;
  playerMediaSessionActive(playerEngine.kind !== 'native');
  if (!lost) window.pywebview?.api?.cast_disconnect?.();
  document.body.classList.remove('is-casting');
  castButtonsShow(false, 'Play on another device');
  if (lost) toastMgr.warning(`Stopped playing on ${name}`);
  if (playerIndex >= 0) {
    await loadPlayerTrack(playerIndex);
    if (at > 1) playerEngine.currentTime = at;
    if (!wasPlaying) playerEngine.pause();
  }
}

// ── Mornye Connect ─────────────────────────────────────────────────────────
// Every page is a device of its account (api_mixins/connect.py, core/connect):
// the desktop window, each browser tab on --web, and through the backend the
// paired phones. One device plays; the others are remotes. This page reports
// its own player as it changes (play order window, position, modes) and runs
// the commands addressed to it. When another device plays, the player swaps
// its engine for `playerRemoteEngine`, as casting does: the bar, Now Playing
// and the Media Session then follow that device, and the buttons send it
// commands. "Play on" moves playback between devices (and to cast receivers).
var CONNECT_ID_KEY = 'mornye-connect-id';       // sessionStorage: one device per tab
var CONNECT_NAME_KEY = 'mornye-connect-name';   // localStorage: the name chosen here
var CONNECT_REPORT_MS = 250;
var CONNECT_HEARTBEAT_MS = 15000;
var CONNECT_HANDOFF_MAX = 1000;                 // queue entries sent on a transfer
var connect = {
  id: '', name: '', kind: 'browser', ready: false, starting: false,
  devices: [], activeId: null, remoteId: '', localEngine: null, applying: false,
  timer: 0, beat: 0, last: null, lastSig: '', lastAt: 0,
};
var playerRemote = null;        // the remote engine, while another device plays

// The part of a list of `length` sent around `index` (core/connect/model.py
// queue_window): at most `max` entries, the current one in the middle.
function connectWindow(length, index, max = 200) {
  if (length <= max) return [0, length];
  const start = Math.min(Math.max(Math.max(index, 0) - Math.floor(max / 2), 0), length - max);
  return [start, start + max];
}

function connectRepeatOut(mode) { return mode === 'off' ? 'none' : (mode === 'all' || mode === 'one' ? mode : 'none'); }
function connectRepeatIn(mode) { return mode === 'all' || mode === 'one' ? mode : 'off'; }

// Where a handed-over queue starts (link_desktop_ops.dart): the requested
// entry if this library has it, else the next one it has, else the last.
function connectStartIndex(slots, index) {
  if (slots[index]) return index;
  for (let i = index + 1; i < slots.length; i++) if (slots[i]) return i;
  for (let i = Math.min(index, slots.length) - 1; i >= 0; i--) if (slots[i]) return i;
  return -1;
}

// A remote state's position now: it was read when it arrived.
function connectPositionAt(state, receivedAt, now) {
  if (!state) return 0;
  const p = (Number(state.positionMs) || 0) + (state.playing ? now - receivedAt : 0);
  const d = Number(state.durationMs) || 0;
  return Math.max(0, d ? Math.min(d, p) : p);
}

function connectDefaultName(ua) {
  ua = String(ua || '');
  const browser = /Edg\//.test(ua) ? 'Edge' : /Firefox\//.test(ua) ? 'Firefox'
    : /Chrome\//.test(ua) ? 'Chrome' : /Safari\//.test(ua) ? 'Safari' : 'Browser';
  const os = /iPhone/.test(ua) ? 'iPhone' : /iPad/.test(ua) ? 'iPad' : /Android/.test(ua) ? 'Android'
    : /Mac OS X|Macintosh/.test(ua) ? 'Mac' : /Windows/.test(ua) ? 'Windows' : /Linux/.test(ua) ? 'Linux' : '';
  return os ? `${browser} on ${os}` : browser;
}

function connectDeviceLabel(d) {
  const kind = { window: 'Mornye app', browser: 'Browser', phone: 'Phone', server: 'Server', cast: 'Receiver', mornye: 'Computer' }[d.kind] || '';
  const playing = d.state?.playing ? ' · playing' : '';
  return kind ? `${d.name} · ${kind}${playing}` : `${d.name}${playing}`;
}

// The engine of a page that remotes another device: it plays nothing, shows
// the device's state (extrapolated between updates, like the cast engine)
// and turns every transport call into a command. `send(action, args)`.
function playerRemoteEngine(send, clock = () => performance.now()) {
  let emit = () => {};
  let state = null, at = 0, paused = true, tick = 0, volume = 1, muted = false, volumeTimer = 0;
  const position = () => connectPositionAt(state, at, clock()) / 1000;
  const ticking = (on) => {
    clearInterval(tick);
    tick = on ? setInterval(() => emit('timeupdate'), 500) : 0;
  };
  const engine = {
    kind: 'remote',
    silent: false,             // while set, setters change nothing remotely
    attach(fn) { emit = fn; },
    get hasSource() { return !!state?.track; },
    get output() { return null; },
    load(_path, _next, meta = {}) {
      if (!engine.silent && Number.isInteger(meta.index)) send('jump', { index: meta.index });
    },
    setNext() {},
    clear() { ticking(false); },
    play() { if (paused && !engine.silent) send('play', {}); return Promise.resolve(); },
    pause() { if (!paused && !engine.silent) send('pause', {}); },
    get paused() { return paused; },
    get currentTime() { return position(); },
    set currentTime(t) {
      const ms = Math.max(0, Math.round((Number(t) || 0) * 1000));
      if (state) { state = { ...state, positionMs: ms }; at = clock(); }
      if (!engine.silent) send('seek', { positionMs: ms });
      emit('timeupdate'); emit('seeked');
    },
    get duration() { return state?.durationMs ? state.durationMs / 1000 : NaN; },
    get volume() { return volume; },
    set volume(v) {
      volume = Math.max(0, Math.min(1, Number(v) || 0));
      if (engine.silent) return;
      clearTimeout(volumeTimer);
      volumeTimer = setTimeout(() => { volumeTimer = 0; send('volume', { value: muted ? 0 : volume }); }, 150);
    },
    get muted() { return muted; },
    set muted(m) { muted = !!m; engine.volume = volume; },
    get playbackRate() { return 1; },
    set playbackRate(_r) {},
    receive(next, receivedAt = clock()) {
      if (!next) return;
      const wasPaused = paused, oldDuration = state?.durationMs;
      state = next; at = receivedAt; paused = !next.playing;
      if (typeof next.volume === 'number' && !volumeTimer) volume = next.volume;
      if (next.durationMs !== oldDuration) emit('loadedmetadata');
      if (wasPaused !== paused) emit(paused ? 'pause' : 'play');
      ticking(!paused);
      emit('timeupdate');
    },
    dispose() { ticking(false); clearTimeout(volumeTimer); },
  };
  return engine;
}

function connectApi() {
  const api = window.pywebview?.api;
  return typeof api?.connect_register === 'function' ? api : null;
}

async function connectInit() {
  const api = connectApi();
  if (!api || connect.ready || connect.starting) return;
  connect.starting = true;
  try {
    let id = '';
    try { id = sessionStorage.getItem(CONNECT_ID_KEY) || ''; } catch (e) {}
    if (!id) {
      id = (crypto.randomUUID?.() || `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`);
      try { sessionStorage.setItem(CONNECT_ID_KEY, id); } catch (e) {}
    }
    let name = '';
    try { name = localStorage.getItem(CONNECT_NAME_KEY) || ''; } catch (e) {}
    const desktop = !window.__mornyeWeb;
    const res = await api.connect_register(id, name || (desktop ? '' : connectDefaultName(navigator.userAgent)), desktop ? 'window' : 'browser');
    if (res?.status !== 'ok') return;
    Object.assign(connect, { id, name: res.device.name, kind: res.device.kind, ready: true });
    clearInterval(connect.beat);
    connect.beat = setInterval(connectBeat, CONNECT_HEARTBEAT_MS);
    connectDevices(res);
    if (playerIndex >= 0) connectSchedule(true);
  } catch (e) {
    console.warn('Connect unavailable', e);
  } finally {
    connect.starting = false;
  }
}

async function connectBeat() {
  const api = connectApi();
  if (!api || !connect.ready) return;
  if (playerEngine.kind !== 'remote' && playerIndex >= 0 && !playerEngine.paused) { connectReport(true); return; }
  try {
    const res = await api.connect_heartbeat(connect.id);
    if (res?.status === 'ok' && res.known === false) { connect.ready = false; connectInit(); }
  } catch (e) { /* the next beat tries again */ }
}

// This page's player as connect_report_state takes it: the play order's
// window around the current entry, in the page's own queue entries.
function connectLocalState() {
  const audio = playerEngine;
  const order = playerOrder.map(i => playerQueue[i]);
  const [start, end] = connectWindow(order.length, playerPos);
  const has = playerIndex >= 0 && audio.hasSource;
  return {
    playing: has && !audio.paused,
    positionMs: has ? Math.round((audio.currentTime || 0) * 1000) : 0,
    durationMs: has && Number.isFinite(audio.duration) ? Math.round(audio.duration * 1000) : 0,
    items: order.slice(start, end).map(q => (q ? {
      path: q.path, title: q.title || '', artist: q.artist || '', album: q.album || '', duration_s: q.duration_s || 0,
    } : null)),
    index: playerIndex >= 0 ? playerPos - start : -1,
    shuffle: playerShuffle,
    repeat: connectRepeatOut(playerRepeat),
    volume: audio.muted ? 0 : audio.volume,
  };
}

function connectSchedule(force = false) {
  if (!connect.ready || playerEngine.kind === 'remote') return;
  clearTimeout(connect.timer);
  connect.timer = setTimeout(() => connectReport(force), CONNECT_REPORT_MS);
}

// Sends the state unless only the position moved, and moved as playback
// alone explains (the backend applies the same rule for the phones).
async function connectReport(force = false) {
  connect.timer = 0;
  const api = connectApi();
  if (!api || !connect.ready || playerEngine.kind === 'remote') return;
  if (playerIndex < 0 && !connect.last) return;
  const s = connectLocalState();
  const now = performance.now();
  const sig = JSON.stringify([s.playing, s.index, s.shuffle, s.repeat, Math.round(s.volume * 200), s.durationMs,
    s.items.map(i => i?.path || '')]);
  const expected = connect.last ? connectPositionAt(connect.last, connect.lastAt, now) : -1;
  if (!force && sig === connect.lastSig && Math.abs(expected - s.positionMs) <= 2000) return;
  connect.last = s; connect.lastSig = sig; connect.lastAt = now;
  try { await api.connect_report_state(connect.id, s); } catch (e) { /* the heartbeat sends it again */ }
}

function connectOthers() {
  return connect.devices.filter(d => d.id !== connect.id && d.canPlay);
}

function connectButtonsShow() {
  const visible = castAvailable() || connectOthers().length > 0;
  document.querySelectorAll('.cast-btn').forEach(b => b.classList.toggle('hidden', !visible));
  if (playerCast) return;
  const remote = playerEngine.kind === 'remote' ? connect.devices.find(d => d.id === connect.remoteId) : null;
  castButtonsShow(!!remote, remote ? `Playing on ${remote.name}` : 'Play on another device');
}

// app_connect_devices: the account's devices and which one plays.
function connectDevices(snap) {
  if (!snap || !Array.isArray(snap.devices)) return;
  connect.devices = snap.devices;
  connect.activeId = snap.activeId || null;
  const active = connect.devices.find(d => d.id === connect.activeId);
  if (active && active.id !== connect.id && active.state) connectFollow(active.id, active.state, active.extra);
  else if (playerEngine.kind === 'remote') connectLeaveRemote({ lost: !active });
  connectButtonsShow();
  renderConnectPanel();
}

// app_connect_state: the active device's player changed.
function connectRemoteState(msg) {
  if (!msg || msg.deviceId === connect.id || !msg.state) return;
  connect.activeId = msg.deviceId;
  const device = connect.devices.find(d => d.id === msg.deviceId);
  if (device) { device.state = msg.state; device.extra = msg.extra; }
  connectFollow(msg.deviceId, msg.state, msg.extra);
}

function connectEnterRemote() {
  // A receiver this window casts to is the user's own choice: leave it be.
  if (playerCast && playerEngine.active === playerCast) return false;
  listenFinish();
  const local = connect.ready && playerIndex >= 0 ? connectLocalState() : null;
  connect.localEngine = playerEngine.active;
  playerRemote ||= playerRemoteEngine((action, args) => connectSendRemote(action, args));
  playerRemote.silent = true;
  playerEngine.use(playerRemote);
  playerRemote.silent = false;
  playerMediaSessionActive(true);
  document.body.classList.add('is-remote');
  // This page stopped playing: say so once, since a remote page reports
  // nothing. Left "playing", it would be sent to another computer as what
  // plays here, and take that one's controls away once it paused.
  if (local?.playing) connectReportStopped(local);
  return true;
}

async function connectReportStopped(local) {
  clearTimeout(connect.timer);
  connect.timer = 0;
  const s = { ...local, playing: false };
  connect.last = s; connect.lastSig = ''; connect.lastAt = performance.now();
  try { await connectApi()?.connect_report_state(connect.id, s); } catch (e) { /* nothing else to do */ }
}

async function connectLeaveRemote({ lost = false, keep = true } = {}) {
  if (playerEngine.kind !== 'remote') return;
  const at = playerRemote?.currentTime || 0;
  const back = connect.localEngine || playerBrowser;
  connect.remoteId = '';
  playerEngine.use(back);
  playerRemote?.dispose();
  playerRemote = null;
  playerMediaSessionActive(back.kind !== 'native');
  document.body.classList.remove('is-remote');
  connectButtonsShow();
  presenceSync();
  if (lost) toastMgr.warning('The device that was playing is gone.');
  if (keep && playerIndex >= 0 && playerQueue[playerIndex]?.path) {
    await loadPlayerTrack(playerIndex);
    if (at > 1) playerEngine.currentTime = at;
    playerEngine.pause();
  } else if (keep) {
    playerIndex = -1;
    playerShowIdle();
    markPlayingRow();
  }
}

// Shows the remote device's queue and song here, without playing anything.
async function connectFollow(id, state, extra) {
  const receivedAt = performance.now();
  if (playerEngine.kind !== 'remote' && !connectEnterRemote()) return;
  const changedDevice = connect.remoteId !== id;
  connect.remoteId = id;
  let paths = Array.isArray(extra?.paths) ? extra.paths : [];
  if (!paths.length && (state.queue || []).length) paths = await connectLocalPaths(state.queue);
  const items = (state.queue || []).map((r, i) => ({
    path: paths[i] || '', title: r.title || '', artist: r.artist || '', album: r.album || '',
    duration_s: r.durationMs ? r.durationMs / 1000 : 0,
  }));
  const same = !changedDevice && items.length === playerQueue.length
    && items.every((q, i) => q.path === playerQueue[i]?.path && q.title === playerQueue[i]?.title);
  if (!same) { playerQueue = items; playerOrder = items.map((_, i) => i); }
  playerShuffle = !!state.shuffle;
  playerRepeat = connectRepeatIn(state.repeat);
  updatePlayerModes();
  playerRemote.receive(state, receivedAt);
  const v = Math.round((typeof state.volume === 'number' ? state.volume : 1) * 100);
  ['pl-vol', 'np-vol'].forEach(n => { const el = $(n); if (el) { el.value = v; amPaint(el); } });
  const index = Number.isInteger(state.index) ? state.index : -1;
  if (index >= 0 && (index !== playerIndex || !same)) {
    connect.applying = true;
    try { await loadPlayerTrack(index); } finally { connect.applying = false; }
  } else if (index < 0 && state.track) {
    connectShowRemoteTrack({ title: state.track.title || '', artist: state.track.artist || '', path: '' });
  }
  presenceSync();
  connectButtonsShow();
  if (!$('np-queue').classList.contains('hidden')) npRenderQueue();
}

// Another computer or a phone sends no paths: its queue's songs are looked
// up in this library, so the ones it has show their cover, quality and
// lyrics from the file here. Looked up once per queue.
let connectLocalLookup = { key: '', paths: [] };
async function connectLocalPaths(queue) {
  const key = queue.map(r => `${r.songId || ''}\u0001${r.title || ''}\u0001${r.artist || ''}`).join('\u0002');
  if (key === connectLocalLookup.key) return connectLocalLookup.paths;
  let paths = [];
  try {
    const res = await connectApi()?.connect_resolve(queue);
    if (res?.status === 'ok') paths = (res.items || []).map(item => item?.path || '');
  } catch (e) {}
  connectLocalLookup = { key, paths };
  return paths;
}

// A remote song this library has no file for: its name only.
function connectShowRemoteTrack(item) {
  $('player-bar').classList.remove('hidden');
  document.body.classList.add('has-player');
  document.body.classList.remove('player-idle');
  const artist = displayArtists(item.artist);
  $('pl-title').textContent = artist ? `${item.title} — ${artist}` : item.title;
  marqueeFit($('pl-title'));
  $('pl-quality').textContent = '';
  setPlayerArtwork('');
  fillNowPlaying(item, {});
}

async function connectSendRemote(action, args) {
  const api = connectApi();
  if (!api || !connect.remoteId) return;
  try {
    const res = await api.connect_command(action, args, connect.remoteId);
    if (res?.status !== 'ok') toastMgr.error(res?.error || 'The device did not answer.');
  } catch (e) {
    toastMgr.error('The device did not answer.');
  }
}

// app_connect_command: a remote's command for this page's player.
async function connectRun(action, args = {}) {
  const audio = playerEngine;
  if (audio.kind === 'remote' && action !== 'play_queue') return;
  switch (action) {
    case 'play':
      if (audio.hasSource) { try { await audio.play(); } catch (e) { connectAutoplayBlocked(); } }
      else if (playerIndex >= 0) await loadPlayerTrack(playerIndex);
      break;
    case 'pause': audio.pause(); break;
    case 'toggle': playerToggle(); break;
    case 'next': await playerNext(); break;
    case 'previous': await playerPrev(); break;
    case 'seek': if (audio.hasSource) audio.currentTime = (Number(args.positionMs) || 0) / 1000; break;
    case 'volume': playerSetVolumePercent((typeof args.value === 'number' ? args.value : 1) * 100); break;
    case 'shuffle': if (playerShuffle !== !!args.enabled) playerToggleShuffle(); break;
    case 'repeat': playerRepeat = connectRepeatIn(args.mode); updatePlayerModes(); break;
    case 'jump': { const p = connectOrderPos(args.index); if (p >= 0) await loadPlayerTrack(playerOrder[p]); break; }
    case 'move': connectMove(connectOrderPos(args.from), connectOrderPos(args.to)); break;
    case 'remove': connectRemove(connectOrderPos(args.index)); break;
    case 'play_queue': await connectPlayQueue(args); break;
    case 'queue_add': await connectQueueAdd(args); break;
    default: return;
  }
  connectSchedule();
}

// An index into the window this page last reported, as a play-order position.
function connectOrderPos(index) {
  const [start, end] = connectWindow(playerOrder.length, playerPos);
  const p = start + Number(index);
  return Number.isInteger(p) && p >= start && p < end ? p : -1;
}

function connectMove(from, to) {
  if (from < 0 || to < 0 || from === to) return;
  const [moved] = playerOrder.splice(from, 1);
  playerOrder.splice(to, 0, moved);
  playerPos = playerOrder.indexOf(playerIndex);
  playerRefreshNext();
  if (!$('np-queue').classList.contains('hidden')) npRenderQueue();
}

function connectRemove(pos) {
  if (pos < 0 || pos === playerPos) return;
  playerOrder.splice(pos, 1);
  playerPos = playerOrder.indexOf(playerIndex);
  playerRefreshNext();
  if (!$('np-queue').classList.contains('hidden')) npRenderQueue();
}

// queue_add: songs a remote (or the phone's queue/add) puts in this queue.
async function connectQueueAdd(args) {
  const res = await connectApi()?.connect_resolve(Array.isArray(args.queue) ? args.queue : []);
  const items = (res?.items || []).filter(Boolean);
  if (!items.length) return;
  if (!playerQueue.length || playerIndex < 0) { startPlayback(items, 0, false, false); return; }
  const start = playerQueue.length;
  playerQueue.push(...items);
  const added = items.map((_, i) => start + i);
  if (args.next) playerOrder.splice(playerPos + 1, 0, ...added);
  else playerOrder.push(...added);
  playerRefreshNext();
  if (!$('np-queue').classList.contains('hidden')) npRenderQueue();
}

function connectAutoplayBlocked() {
  toastMgr.info('Press play to start playback on this device.');
}

// play_queue: a queue handed to this page (another device's, a phone's).
// The songs this library lacks are skipped, as the desktop app does.
async function connectPlayQueue(args) {
  const api = connectApi();
  if (!api) return;
  const res = await api.connect_resolve(Array.isArray(args.queue) ? args.queue : []);
  if (res?.status !== 'ok') { toastMgr.error(res?.error || 'Could not play that queue here.'); return; }
  const slots = res.items || [];
  const wanted = Math.max(0, Number(args.index) || 0);
  const startAt = connectStartIndex(slots, wanted);
  if (startAt < 0) { toastMgr.error('None of those songs is in this library.'); return; }
  if (playerEngine.kind === 'remote') await connectLeaveRemote({ keep: false });
  if (res.missing?.length) toastMgr.info(`${plural(res.missing.length, 'song')} not in this library ${res.missing.length === 1 ? 'was' : 'were'} skipped.`);
  const items = slots.filter(Boolean);
  playerQueue = items;
  playerOrder = items.map((_, i) => i);
  const first = slots.slice(0, startAt).filter(Boolean).length;
  playerPos = first;
  if (typeof args.shuffle === 'boolean') playerShuffle = args.shuffle;
  if (args.repeat) playerRepeat = connectRepeatIn(args.repeat);
  updatePlayerModes();
  playerIndex = -1;
  await loadPlayerTrack(first);
  const position = startAt === wanted ? (Number(args.positionMs) || 0) / 1000 : 0;
  if (position > 0) playerEngine.currentTime = position;
  if (args.playing === false) playerEngine.pause();
  else if (playerEngine.paused) connectAutoplayBlocked();
  connectSchedule(true);
}

// The whole play order (up to CONNECT_HANDOFF_MAX around the current song)
// for another device to continue from here.
function connectHandoff() {
  const order = playerOrder.map(i => playerQueue[i]);
  const [start, end] = connectWindow(order.length, playerPos, CONNECT_HANDOFF_MAX);
  return {
    items: order.slice(start, end).map(q => (q ? { path: q.path, title: q.title || '', artist: q.artist || '', album: q.album || '', duration_s: q.duration_s || 0 } : null)),
    index: playerPos - start,
    positionMs: Math.round((playerEngine.currentTime || 0) * 1000),
    playing: !playerEngine.paused,
    shuffle: playerShuffle,
    repeat: connectRepeatOut(playerRepeat),
  };
}

async function connectTransferTo(device) {
  const api = connectApi();
  castPicker = null;
  if (!api) return;
  if (playerCast && playerEngine.active === playerCast) await stopCasting();
  let res;
  if (playerEngine.kind === 'remote') {
    res = await api.connect_transfer(device.id);
  } else if (playerIndex >= 0 && playerEngine.hasSource) {
    res = await api.connect_transfer(device.id, connectHandoff());
  } else {
    toastMgr.info('Nothing is playing yet.');
    return;
  }
  if (res?.status !== 'ok') toastMgr.error(res?.error || `Could not play on ${device.name}`);
  else connectDevices(res);
}

async function connectPlayHere() {
  castPicker = null;
  if (playerCast && playerEngine.active === playerCast) { await stopCasting(); return; }
  if (playerEngine.kind !== 'remote') return;
  const api = connectApi();
  const res = await api?.connect_transfer(connect.id);
  if (res?.status !== 'ok') toastMgr.error(res?.error || 'Could not move playback here.');
}

// Plays `items` on the device this page remotes, as Spotify Connect does when
// a song is picked on a remote. `shuffle` true sends them already shuffled.
function connectStartRemote(items, index, shuffle) {
  let list = items.map(q => ({ path: q.path, title: q.title || '', artist: q.artist || '', album: q.album || '', duration_s: q.duration_s || 0 }));
  let first = index;
  if (shuffle === true) {
    for (let i = list.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [list[i], list[j]] = [list[j], list[i]];
    }
    first = 0;
  }
  const [start, end] = connectWindow(list.length, first, CONNECT_HANDOFF_MAX);
  list = list.slice(start, end);
  connectSendRemote('play_queue', {
    items: list, index: first - start, positionMs: 0, playing: true,
    ...(typeof shuffle === 'boolean' ? { shuffle } : {}),
  });
}


window.app_connect_devices = (snap) => connectDevices(snap);
window.app_connect_state = (msg) => connectRemoteState(msg);
window.app_connect_command = (msg) => { if (msg && msg.target === connect.id) connectRun(msg.action, msg.args || {}); };

// A playlist changed somewhere else (a phone over Mornye Link, or the
// server following its provider playlist): the list, and the playlist open,
// are read again. Several changes in a row are read once.
let playlistsChangedTimer = null;
window.app_player_playlists_changed = () => {
  clearTimeout(playlistsChangedTimer);
  playlistsChangedTimer = setTimeout(() => {
    playlistsChangedTimer = null;
    refreshPlayerPlaylists(lib.playlist || undefined);
  }, 300);
};

(function initConnect() {
  for (const type of ['play', 'pause', 'seeked', 'loadedmetadata', 'ended']) {
    playerEngine.addEventListener(type, () => connectSchedule());
  }
  window.addEventListener('pywebviewready', connectInit);
  window.addEventListener('pagehide', () => {
    const api = connectApi();
    if (api && connect.ready) { try { api.connect_unregister(connect.id); } catch (e) {} }
    connect.ready = false;
  });
  window.addEventListener('pageshow', (e) => { if (e.persisted) connectInit(); });
  if (connectApi()) setTimeout(connectInit, 0);
})();

// Every cast button (the player bar's, Now Playing's) lights in the signal
// colour while the music plays elsewhere (a receiver, another Connect
// device), as Spotify's does; its tooltip says where.
function castButtonsShow(on, title) {
  document.body.classList.toggle('plays-elsewhere', !!on);
  renderConnectPanel();
  document.querySelectorAll('.cast-btn').forEach((b) => {
    b.classList.toggle('on', on);
    b.setAttribute('title', title);
  });
}

(function initCastButtons() {
  const buttons = [...document.querySelectorAll('.cast-btn')];
  const show = () => buttons.forEach(b => b.classList.toggle('hidden', !(castAvailable() || connectOthers().length)));
  show();
  window.addEventListener('pywebviewready', show);
  buttons.forEach(btn => btn.addEventListener('click', (e) => { e.stopPropagation(); openCastPicker(btn); }));
})();

// The track that plays after the current one, or -1: what the native
// engine opens in advance for a gapless change. Repeat-one queues the same
// track; the wrap of a shuffled repeat-all reshuffles, so it is not queued.
function playerUpcomingIndex() {
  if (!playerOrder.length || playerIndex < 0) return -1;
  if (playerRepeat === 'one') return playerIndex;
  if (playerPos + 1 < playerOrder.length) return playerOrder[playerPos + 1];
  if (playerRepeat === 'all' && !playerShuffle) return playerOrder[0];
  return -1;
}

function playerUpcomingPath() {
  const next = playerUpcomingIndex();
  return next >= 0 ? (playerQueue[next]?.path || null) : null;
}

// Tells the native engine which file comes next, after anything that
// changes the order (shuffle, repeat, a new queue).
function playerRefreshNext() {
  if (playerEngine.kind === 'native' && playerEngine.hasSource) playerEngine.setNext(playerUpcomingPath());
}

// The native engine went on to the queued file by itself: the page follows
// without loading anything.
function playerOnAdvanced(path) {
  const expected = playerUpcomingIndex();
  const index = expected >= 0 && playerQueue[expected]?.path === path
    ? expected : playerQueue.findIndex(q => q.path === path);
  if (index >= 0) loadPlayerTrack(index, { advanced: true });
}

function playerQualityText() {
  const out = playerEngine.output;
  if (out?.bitstream) return out.label;
  return playerSourceQuality;
}

function playerShowQuality() {
  const el = $('pl-quality');
  if (!el || playerIndex < 0) return;
  el.textContent = playerQualityText();
  const out = playerEngine.output;
  el.title = out?.label
    ? `Output: ${out.label}${out.ao ? ` (${out.ao})` : ''}`
    : 'Played exactly as stored. Formats the window cannot decode are played as a lossless FLAC copy. The operating system may still resample to your output device\'s rate.';
}

// ── Listening record ────────────────────────────────────────────────────────
// As Mornye mobile records it (core/listening.py): elapsed playing time,
// never a jump of the seek bar; a play counts once per song after 30
// seconds of it; when a song stops being the current one it was played
// through (to the end or past half) or skipped. Sent in pieces: every 30
// seconds heard, and when the song changes or the player closes.
var listenState = null;

function listenNew(item, quality) {
  return { item, quality: quality || '', duration: 0, ms: 0, sent: 0, counted: false, last: null, playing: false, ended: false, started: Date.now() / 1000 };
}

// Adds the time since the last tick while playing; a gap longer than
// `maxGap` (a sleeping machine, a stalled stream) counts only `maxGap`.
function listenAdvance(state, now, playing, maxGap = 3000) {
  if (!state) return state;
  if (state.playing && state.last != null) {
    const gap = now - state.last;
    if (gap > 0) state.ms += Math.min(gap, maxGap);
  }
  state.last = now;
  state.playing = !!playing;
  return state;
}

// What is still to be sent of `state`, or null; marks it sent. `final`
// when the song stops being the current one.
function listenPiece(state, final = false, playMs = 30000) {
  if (!state) return null;
  const ms = Math.max(0, Math.round(state.ms - state.sent));
  const play = !state.counted && state.ms >= playMs;
  let outcome = '';
  if (final && state.ms >= 1000) {
    const half = state.duration > 0 ? state.ms >= state.duration * 500 : state.ms >= playMs;
    outcome = state.ended || half ? 'listened' : 'skipped';
  }
  if (!ms && !play && !outcome) return null;
  state.sent += ms;
  if (play) state.counted = true;
  const it = state.item || {};
  const piece = { path: it.path || '', title: it.title || '', artist: it.artist || '', album: it.album || '', quality: state.quality, ms, play, outcome };
  // The final piece also carries what a Last.fm scrobble needs: the whole
  // time heard, the song's length and when it started (core/scrobbler.py).
  if (outcome) Object.assign(piece, { heard: state.ms / 1000, duration: state.duration || 0, started: state.started || 0 });
  return piece;
}

function listenSend(final = false) {
  const piece = listenPiece(listenState, final);
  if (!piece) return;
  try { window.pywebview?.api?.record_listening?.(piece); } catch (e) { /* the record is best effort */ }
}

function listenTick() {
  if (!listenState) return;
  listenAdvance(listenState, performance.now(), !playerEngine.paused);
  if (listenState.ms - listenState.sent >= 30000 || (!listenState.counted && listenState.ms >= 30000)) listenSend(false);
}

function listenFinish({ ended = false } = {}) {
  if (!listenState) return;
  listenAdvance(listenState, performance.now(), false);
  if (ended) listenState.ended = true;
  listenSend(true);
  listenState = null;
  presenceSync();
}

// ── Now playing, for Discord and Last.fm (api_mixins/integrations.py) ───────
// Told on every change of song, play/pause and seek, gathered over a moment
// and only when something changed. Desktop only: over --web the services
// would be the server's.
var presenceTimer = null, presenceSent = '';

function presenceInfo() {
  if (playerEngine.kind === 'remote') return presenceRemoteInfo();
  const it = listenState?.item;
  if (!it?.title) return null;
  const duration = Number.isFinite(playerEngine.duration) ? playerEngine.duration : (listenState.duration || 0);
  return {
    title: it.title || '', artist: it.artist || '', album: it.album || '',
    duration: duration || 0, position: playerEngine.currentTime || 0, playing: !playerEngine.paused,
  };
}

// Another device plays and this page remotes it: Discord still shows the
// song, as Spotify does for a Connect device (what plays there is still
// this person listening). The device's own report is the truth; the queue
// here fills what it leaves out.
function presenceRemoteInfo() {
  const state = connect.devices.find(d => d.id === connect.remoteId)?.state;
  const item = playerQueue[playerIndex];
  const track = state?.track?.title ? state.track : item;
  if (!track?.title) return null;
  const durationMs = state?.durationMs || track.durationMs || 0;
  const duration = durationMs ? durationMs / 1000 : (track.duration_s || 0);
  return {
    title: track.title || '', artist: track.artist || '', album: track.album || '',
    duration, position: playerEngine.currentTime || 0, playing: !playerEngine.paused,
  };
}

function presenceSync() {
  if (window.__MORNYE_WEB_MODE__) return;
  clearTimeout(presenceTimer);
  presenceTimer = setTimeout(() => {
    const info = presenceInfo();
    // The start time, rounded, so a playing song is not re-sent every
    // tick; Mornye itself skips what moved by less than a few seconds.
    const key = info
      ? [info.title, info.artist, info.album, info.playing, Math.round(Date.now() / 1000 - info.position)].join('|')
      : '';
    if (key === presenceSent) return;
    presenceSent = key;
    try { window.pywebview?.api?.set_now_playing?.(info); } catch (e) { /* best effort */ }
  }, 400);
}

async function loadPlayerTrack(index, { advanced = false } = {}) {
  const item = playerQueue[index];
  if (!item) return;
  listenFinish();
  playerIndex = index;
  if (playerOrder.length !== playerQueue.length) playerBuildOrder(index);
  else playerPos = playerOrder.indexOf(index);
  const token = ++playerToken;
  const audio = playerEngine;
  const native = audio.kind === 'native';
  const remote = audio.kind === 'remote';
  const cast = audio.kind === 'cast' || remote;
  // Another device plays: a song picked here (Next, Previous, a row) is a
  // jump there, sent first; the file here, if any, only shows its cover,
  // quality and lyrics. A load that only follows what that device moved on
  // to (connect.applying) sends nothing.
  if (remote && !advanced && !connect.applying) audio.load('', undefined, { index });
  if (remote && !item.path) {
    connectShowRemoteTrack(item);
    markPlayingRow();
    return;
  }
  $('player-bar').classList.remove('hidden');
  document.body.classList.add('has-player');
  document.body.classList.remove('player-idle');
  setPlayerArtwork('');
  const artist = displayArtists(item.artist);
  $('pl-title').textContent = artist ? `${item.title} — ${artist}` : item.title;
  marqueeFit($('pl-title'));
  if (!advanced) $('pl-quality').textContent = 'Preparing…';
  markPlayingRow();
  playerAbReset();
  if (!advanced && !remote) audio.pause();
  // A receiver is sent the file as stored (native: no local copy is made).
  const res = await window.pywebview?.api?.prepare_playback?.(item.path, !window.__mornyeWeb, native || cast);
  if (token !== playerToken) return; // another track was chosen meanwhile
  if (!res || res.status !== 'ok') {
    $('pl-quality').textContent = 'Cannot play this file';
    toastMgr.error(res?.error || 'Could not play the file.');
    return;
  }
  if (!advanced && remote) {
    // The jump went out above: this only shows the file.
    playerRemote.silent = true;
    audio.load(res.path, undefined, { index });
    playerRemote.silent = false;
  } else if (!advanced && cast) {
    const start = castStartAt; castStartAt = 0;
    audio.load(res.path, undefined, {
      title: res.title || item.title, artist: res.artist || item.artist || '', album: res.album || item.album || '',
      duration: item.duration_s || res.info?.duration || 0, start,
    });
  } else if (!advanced) {
    audio.load(native ? res.path : (res.url || ('/api/media?path=' + encodeURIComponent(res.path))),
      native ? playerUpcomingPath() : undefined);
  } else {
    playerRefreshNext();
  }
  playerSourceQuality = res.quality;
  playerShowQuality();
  // A listen is counted where the music plays, not on a remote.
  if (!remote) {
    listenState = listenNew({ ...item, title: res.title || item.title, artist: res.artist || item.artist, album: res.album || item.album }, res.quality);
    listenAdvance(listenState, performance.now(), !audio.paused);
  }
  fillNowPlaying(item, res);
  if (!advanced && !remote) {
    try { await audio.play(); } catch (e) { /* autoplay blocked: the play button works */ }
  }
  playerMediaMetadata(item, res, artist);
}

// ── Media keys and the system's "Now Playing" ───────────────────────────────
// The Media Session is what the keyboard's play/pause/next keys, the macOS
// Now Playing panel, the Windows media overlay and a Bluetooth headset talk
// to, in the desktop window (WebView2, WebKit) as in a browser tab. With the
// native engine the window plays nothing, so the system talks to mpv instead
// (its media-controls) and the page's handlers are taken off: both at once
// would answer every key twice. mpv hands each key back to the page (a
// 'media' event), since the page owns the queue. Where a key also arrives as
// a keydown (WebView2 or GTK with the window focused, some browsers), the
// same press can come twice: from the keydown and from the system, so an
// echo from another source within PLAYER_MEDIA_ECHO_MS is dropped.
// The volume keys are left to the system: on macOS they never reach the
// window, and where they do the system volume moves anyway, so moving
// Mornye's slider too would apply every press twice.
let playerMediaSessionOn = true;
const PLAYER_MEDIA_ECHO_MS = 400;
let playerMediaLast = { family: '', source: '', at: -Infinity };

function playerMediaMetadata(item, res, artist) {
  if (!('mediaSession' in navigator) || !playerMediaSessionOn) return;
  try {
    const cover = res?.cover || '';
    navigator.mediaSession.metadata = new MediaMetadata({
      title: res?.title || item.title,
      artist,
      album: res?.album || item.album || '',
      artwork: cover ? [{ src: cover, sizes: '512x512' }] : [],
    });
  } catch (e) { /* an engine without MediaMetadata artwork support */ }
}

function playerMediaPosition() {
  const audio = playerEngine;
  if (!playerMediaSessionOn) return;
  if (!('mediaSession' in navigator) || !navigator.mediaSession.setPositionState) return;
  if (!Number.isFinite(audio.duration) || audio.duration <= 0) return;
  try {
    navigator.mediaSession.setPositionState({
      duration: audio.duration,
      position: Math.min(audio.currentTime, audio.duration),
      playbackRate: audio.playbackRate || 1,
    });
  } catch (e) { /* position past the end while a track changes */ }
}

// The keydown keys that are media keys, as Media Session actions.
function playerMediaKeyAction(key) {
  switch (key) {
    case 'MediaPlayPause': return 'toggle';
    case 'MediaPlay': return 'play';
    case 'MediaPause': return 'pause';
    case 'MediaStop': return 'stop';
    case 'MediaTrackNext': return 'nexttrack';
    case 'MediaTrackPrevious': return 'previoustrack';
    default: return null;
  }
}

// Play, pause and toggle are one key: the system turns a toggle into play
// or pause.
function playerMediaFamily(action) {
  return ['toggle', 'play', 'pause'].includes(action) ? 'playpause' : action;
}

// True when `action` from `source` is the same press already handled from
// another source (a keydown and the system's command for one key).
function playerMediaIsEcho(last, action, source, now) {
  return last.family === playerMediaFamily(action) && last.source !== source
    && now - last.at < PLAYER_MEDIA_ECHO_MS;
}

// Every media key, whichever way it came: 'session' (Media Session),
// 'native' (mpv) or 'key' (a keydown).
function playerMediaAction(action, source, detail) {
  const handler = playerMediaHandlers()[action];
  if (!handler) return false;
  const now = performance.now();
  if (playerMediaIsEcho(playerMediaLast, action, source, now)) return true;
  playerMediaLast = { family: playerMediaFamily(action), source, at: now };
  handler(detail);
  return true;
}

function playerMediaHandlers() {
  const audio = playerEngine;
  const seekBy = (offset) => {
    if (!audio.hasSource || !Number.isFinite(audio.duration)) return;
    audio.currentTime = Math.max(0, Math.min(audio.duration, audio.currentTime + offset));
    playerMediaPosition();
  };
  return {
    toggle: () => playerToggle(),
    play: () => { if (audio.hasSource) audio.play(); },
    pause: () => audio.pause(),
    stop: () => { audio.pause(); if (audio.hasSource) audio.currentTime = 0; },
    previoustrack: () => playerPrev(),
    nexttrack: () => playerNext(),
    seekbackward: (d) => seekBy(-(d?.seekOffset || 10)),
    seekforward: (d) => seekBy(d?.seekOffset || 10),
    seekto: (d) => {
      if (!audio.hasSource || d?.seekTime == null) return;
      audio.currentTime = d.seekTime;
      playerMediaPosition();
    },
  };
}

function playerMediaSessionActive(on) {
  playerMediaSessionOn = on;
  if (!('mediaSession' in navigator)) return;
  const session = navigator.mediaSession;
  for (const action of Object.keys(playerMediaHandlers())) {
    if (action === 'toggle') continue; // not a Media Session action
    const handler = (d) => playerMediaAction(action, 'session', d);
    try { session.setActionHandler(action, on ? handler : null); } catch (e) { /* not supported here */ }
  }
  if (!on) {
    try { session.metadata = null; session.playbackState = 'none'; } catch (e) { /* read-only here */ }
  }
}

playerEngine.addEventListener('media', (d) => playerMediaAction(d?.action, 'native', d));

// Media keys that arrive as keys (with the window focused). Never typed
// text, so a focused field does not keep them from the player.
document.addEventListener('keydown', (e) => {
  const action = playerMediaKeyAction(e.key);
  if (!action || e.defaultPrevented) return;
  if (playerMediaAction(action, 'key')) e.preventDefault();
});

(function initMediaSession() {
  const audio = playerEngine;
  if (!('mediaSession' in navigator)) return;
  const session = navigator.mediaSession;
  playerMediaSessionActive(true);
  audio.addEventListener('play', () => { if (playerMediaSessionOn) session.playbackState = 'playing'; playerMediaPosition(); });
  audio.addEventListener('pause', () => { if (playerMediaSessionOn) session.playbackState = 'paused'; playerMediaPosition(); });
  audio.addEventListener('loadedmetadata', playerMediaPosition);
  audio.addEventListener('seeked', playerMediaPosition);
})();

function playerToggle() {
  const audio = playerEngine;
  if (!audio.hasSource) return;
  audio.paused ? audio.play() : audio.pause();
}

// `auto` is true only when a track ended by itself: then the end of the queue
// stops the music, while the Next button at the end simply does nothing.
function playerNext(auto) {
  if (!playerOrder.length) return;
  if (playerPos + 1 < playerOrder.length) return loadPlayerTrack(playerOrder[playerPos + 1]);
  if (playerRepeat === 'all') {
    if (playerShuffle) playerBuildOrder(null);
    return loadPlayerTrack(playerOrder[0]);
  }
}
function playerEnded() {
  const heard = listenState;
  listenFinish({ ended: true });
  const audio = playerEngine;
  if (playerRepeat === 'one') {
    // The same song again is a new listen.
    if (heard) listenState = listenNew(heard.item, heard.quality);
    audio.currentTime = 0; audio.play(); return;
  }
  playerNext(true);
}
function playerPrev() {
  const audio = playerEngine;
  if (audio.currentTime > 3 || !playerOrder.length) { audio.currentTime = 0; return; }
  if (playerPos > 0) return loadPlayerTrack(playerOrder[playerPos - 1]);
  if (playerRepeat === 'all') return loadPlayerTrack(playerOrder[playerOrder.length - 1]);
  audio.currentTime = 0;
}
function closePlayer() {
  listenFinish();
  playerToken++;
  const audio = playerEngine;
  audio.clear();
  playerAbReset();
  playerIndex = -1;
  playerShowIdle();
  markPlayingRow();
  connectSchedule(true);
  if ('mediaSession' in navigator && playerMediaSessionOn) {
    navigator.mediaSession.metadata = null;
    navigator.mediaSession.playbackState = 'none';
  }
}

// The player bar stays on screen with nothing playing, as Spotify's does:
// idle it says so, and ✕ (which stops the music) brings it back here.
function playerShowIdle() {
  $('player-bar').classList.remove('hidden');
  document.body.classList.add('has-player', 'player-idle');
  $('pl-title').textContent = 'Not playing';
  marqueeFit($('pl-title'));
  $('pl-quality').textContent = '';
  setPlayerArtwork('');
  $('pl-play-icon').innerHTML = '<path d="M8 5v14l11-7z"/>';
  $('pl-cur').textContent = '0:00';
  $('pl-dur').textContent = '0:00';
  const seek = $('pl-seek');
  seek.value = 0;
  amPaint(seek);
}

(function playerStartIdle() {
  const start = () => { if (playerIndex < 0 && playerEngine.kind !== 'remote') playerShowIdle(); };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, { once: true });
  else start();
})();

// ── Speed and A-B repeat (both engines) ─────────────────────────────────────
// Speed keeps the pitch (the browser's default; mpv's audio-pitch-correction
// unless turned off in Settings → Player). A-B: the first press marks A, the
// second B and starts the loop, the third clears it. mpv loops by itself; the
// window's engine is sent back to A from timeupdate.
const PLAYER_SPEED_MIN = 0.5, PLAYER_SPEED_MAX = 2;
let playerAb = { a: null, b: null };

function playerSpeedStep(current, delta) {
  if (delta === 0) return 1;
  const next = Math.round((current + delta) * 100) / 100;
  return Math.min(PLAYER_SPEED_MAX, Math.max(PLAYER_SPEED_MIN, next));
}

function playerSetSpeed(delta) {
  if (!playerEngine.hasSource) return;
  const speed = playerSpeedStep(playerEngine.playbackRate || 1, delta);
  playerEngine.playbackRate = speed;
  toastMgr.info(`Speed ${speed.toFixed(2)}×`);
}

function playerAbNext(ab, time) {
  if (ab.a == null) return { a: time, b: null };
  if (ab.b == null) return time > ab.a + 0.2 ? { a: ab.a, b: time } : { a: null, b: null };
  return { a: null, b: null };
}

function playerAbCycle() {
  if (!playerEngine.hasSource) return;
  playerAb = playerAbNext(playerAb, playerEngine.currentTime);
  const { a, b } = playerAb;
  playerEngine.setAbLoop(a, b);
  // B is set where playback is now, so it would never be crossed: start the
  // loop from A at once, as mpv's own ab-loop command does.
  if (a != null && b != null) playerEngine.currentTime = a;
  toastMgr.info(a == null ? 'A-B repeat off'
    : b == null ? `A-B repeat: A at ${fmtTime(a)}, press again for B`
      : `A-B repeat ${fmtTime(a)} – ${fmtTime(b)}`);
}

function playerAbReset() {
  if (playerAb.a == null && playerAb.b == null) return;
  playerAb = { a: null, b: null };
  playerEngine.setAbLoop(null, null);
}

playerEngine.addEventListener('timeupdate', () => {
  const { a, b } = playerAb;
  if (playerEngine.kind !== 'browser' || a == null || b == null) return;
  if (playerEngine.currentTime >= b) playerEngine.currentTime = a;
});
playerEngine.addEventListener('advanced', (d) => playerOnAdvanced(d?.path));
playerEngine.addEventListener('output', playerShowQuality);
// The Canvas moves only while the music does, as in Spotify.
playerEngine.addEventListener('play', () => npCanvasSync());
playerEngine.addEventListener('pause', () => npCanvasSync());
{ const box = $('np-canvas-enabled'); if (box) box.checked = canvasEnabled(); }
playerEngine.addEventListener('warning', (d) => { if (d?.message) toastMgr.warning(d.message); });

(function initPlayer() {
  const audio = playerEngine;
  const seek = $('pl-seek');
  audio.addEventListener('timeupdate', () => {
    $('pl-cur').textContent = fmtTime(audio.currentTime);
    if (!playerSeeking && audio.duration) {
      seek.value = Math.round(audio.currentTime / audio.duration * 1000);
      amPaint(seek);
    }
  });
  audio.addEventListener('loadedmetadata', () => {
    $('pl-dur').textContent = fmtTime(audio.duration);
    if (listenState) listenState.duration = audio.duration || 0;
  });
  audio.addEventListener('ended', playerEnded);
  // The listening record follows the clock while the song plays.
  audio.addEventListener('timeupdate', listenTick);
  audio.addEventListener('play', listenTick);
  audio.addEventListener('pause', listenTick);
  window.addEventListener('pagehide', () => { listenTick(); listenSend(false); });
  for (const type of ['play', 'pause', 'seeked', 'loadedmetadata', 'ended']) audio.addEventListener(type, presenceSync);
  for (const type of ['play', 'pause', 'ended']) audio.addEventListener(type, () => renderConnectPanel());
  const icon = $('pl-play-icon');
  const PLAY = '<path d="M8 5v14l11-7z"/>', PAUSE = '<path d="M6 5h4v14H6zM14 5h4v14h-4z"/>';
  audio.addEventListener('play', () => { icon.innerHTML = PAUSE; });
  audio.addEventListener('pause', () => { icon.innerHTML = PLAY; });
  audio.addEventListener('error', (d) => {
    if (!audio.hasSource && !d?.message) return;
    $('pl-quality').textContent = d?.message ? 'Cannot play this file' : 'This window cannot decode the file';
    if (d?.message) toastMgr.error(d.message);
  });
  seek.addEventListener('input', () => {
    playerSeeking = true;
    seek.classList.add('dragging');
    amPaint(seek);
    if (audio.duration) $('pl-cur').textContent = fmtTime(seek.value / 1000 * audio.duration);
  });
  seek.addEventListener('change', () => {
    if (audio.duration) audio.currentTime = seek.value / 1000 * audio.duration;
    playerSeeking = false;
    seek.classList.remove('dragging');
  });
  const setVolume = value => {
    audio.volume = value / 100;
    $('pl-vol').value = value; $('np-vol').value = value;
    amPaint($('pl-vol')); amPaint($('np-vol'));
  };
  $('pl-vol').addEventListener('input', e => setVolume(e.target.value));
  updatePlayerModes();
  $('np-vol').addEventListener('input', e => setVolume(e.target.value));
  setVolume($('pl-vol').value);
  amPaint(seek);

  // Full-screen view: same audio element, its own controls.
  const npSeek = $('np-seek');
  const npIcon = $('np-play-icon');
  audio.addEventListener('timeupdate', () => {
    $('np-cur').textContent = fmtTime(audio.currentTime);
    $('np-rem').textContent = '-' + fmtTime((audio.duration || 0) - audio.currentTime);
    if (!playerSeeking && audio.duration) {
      npSeek.value = Math.round(audio.currentTime / audio.duration * 1000);
      npPaintSeek();
    }
    npSyncLyrics(audio.currentTime);
  });
  audio.addEventListener('play', () => { npIcon.innerHTML = PAUSE; npStartTick(); });
  audio.addEventListener('pause', () => { npIcon.innerHTML = PLAY; });
  npSeek.addEventListener('input', () => {
    playerSeeking = true;
    npSeek.classList.add('dragging');
    npPaintSeek();
    // While dragging, the times follow the bar, as in Apple Music.
    if (audio.duration) {
      const at = npSeek.value / 1000 * audio.duration;
      $('np-cur').textContent = fmtTime(at);
      $('np-rem').textContent = '-' + fmtTime(audio.duration - at);
    }
  });
  npSeek.addEventListener('change', () => {
    if (audio.duration) audio.currentTime = npSeek.value / 1000 * audio.duration;
    playerSeeking = false;
    npSeek.classList.remove('dragging');
  });
  npSeek.addEventListener('blur', () => npSeek.classList.remove('dragging'));
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && !$('np-view').classList.contains('hidden')) closeNowPlaying();
  });
})();

// ── Settings → Player (the native engine's options) ─────────────────────────
// Stored by the backend in player-audio.json and applied to mpv the moment
// they change, so they stay out of the page's Save Changes.
const NP_OPT_UNITS = { replaygain_preamp: ' dB', audio_delay_ms: ' ms' };
let npOptSaveTimer = 0;
let npOptPending = {};

function npOptValue(el) {
  if (el.type === 'checkbox') return el.checked;
  if (el.type === 'range') return Number(el.value);
  return el.value;
}

function npOptShowValue(name, value) {
  const out = document.querySelector(`[data-np-value="${name}"]`);
  if (out) out.textContent = `${value > 0 && name === 'replaygain_preamp' ? '+' : ''}${value}${NP_OPT_UNITS[name] || ''}`;
}

function npOptFill(options) {
  if (!options) return;
  document.querySelectorAll('#tc-player [data-np-opt]').forEach(el => {
    const name = el.dataset.npOpt;
    if (!(name in options)) return;
    if (el.type === 'checkbox') el.checked = !!options[name];
    else el.value = String(options[name]);
    if (el.type === 'range') { amPaint(el); npOptShowValue(name, options[name]); }
  });
  document.querySelectorAll('#tc-player [data-np-spdif]').forEach(el => {
    el.checked = (options.spdif || []).includes(el.dataset.npSpdif);
  });
  document.querySelectorAll('#np-eq .np-eq-slider').forEach(el => {
    el.value = String(options.eq_gains?.[Number(el.dataset.band)] ?? 0);
    amPaint(el);
  });
  npPeqShowMode(options.eq_mode);
  npPeqRender(options.eq_filters || []);
  npOptState(options);
}

// ── Parametric equalizer ────────────────────────────────────────────────────
// Up to 20 peak/shelf filters with exact values, and the AutoEQ / Equalizer
// APO text list in and out (core/native_player.py parse_filter_list).
const NP_PEQ_MAX = 20;
const NP_PEQ_TYPES = [['peak', 'Peak'], ['lowshelf', 'Low shelf'], ['highshelf', 'High shelf']];

function npPeqShowMode(mode) {
  const parametric = mode === 'parametric';
  document.querySelectorAll('#tc-player .np-eq-graphic').forEach(el => el.classList.toggle('hidden', parametric));
  document.querySelectorAll('#tc-player .np-eq-parametric').forEach(el => el.classList.toggle('hidden', !parametric));
  const hint = $('np-eq-mode-hint');
  if (hint) hint.textContent = parametric
    ? 'Parametric: up to 20 filters, 20 Hz to 20 kHz, ±24 dB, Q 0.1 to 20.'
    : 'Graphic: ten bands, lowered by the largest boost so they cannot clip.';
}

function npPeqRow(f, i) {
  const types = NP_PEQ_TYPES.map(([v, label]) => `<option value="${v}"${f.type === v ? ' selected' : ''}>${label}</option>`).join('');
  return `<div class="np-peq-row" data-index="${i}">
    <input type="checkbox" class="np-peq-on" ${f.enabled === false ? '' : 'checked'} aria-label="Filter ${i + 1} on">
    <select class="np-peq-type" aria-label="Filter ${i + 1} type">${types}</select>
    <input type="number" class="np-peq-freq" min="20" max="20000" step="1" value="${Number(f.freq) || 1000}" aria-label="Filter ${i + 1} frequency, Hz">
    <input type="number" class="np-peq-gain" min="-24" max="24" step="0.1" value="${Number(f.gain) || 0}" aria-label="Filter ${i + 1} gain, dB">
    <input type="number" class="np-peq-q" min="0.1" max="20" step="0.01" value="${Number(f.q) || 0.707}" aria-label="Filter ${i + 1} Q">
    <button type="button" class="np-peq-remove" data-onclick="npPeqRemove(this)" aria-label="Remove filter ${i + 1}" title="Remove">×</button>
  </div>`;
}

function npPeqRender(filters) {
  const list = $('np-peq-list');
  if (!list) return;
  list.innerHTML = filters.length
    ? filters.map(npPeqRow).join('')
    : '<div class="np-peq-empty">No filters yet. Add one, or load an AutoEQ list.</div>';
  const add = $('np-peq-add');
  if (add) add.disabled = filters.length >= NP_PEQ_MAX;
  if (playerNativeStatus && !playerNativeStatus.available) npOptState(playerNativeStatus.options);
}

function npPeqCollect() {
  return [...document.querySelectorAll('#np-peq-list .np-peq-row')].map(row => ({
    type: row.querySelector('.np-peq-type').value,
    freq: Number(row.querySelector('.np-peq-freq').value),
    gain: Number(row.querySelector('.np-peq-gain').value),
    q: Number(row.querySelector('.np-peq-q').value),
    enabled: row.querySelector('.np-peq-on').checked,
  }));
}

function npPeqAdd() {
  const filters = npPeqCollect();
  if (filters.length >= NP_PEQ_MAX) return;
  filters.push({ type: 'peak', freq: 1000, gain: 0, q: 0.707, enabled: true });
  npPeqRender(filters);
  npOptQueue({ eq_filters: filters }, 0);
}

function npPeqRemove(btn) {
  const filters = npPeqCollect();
  filters.splice(Number(btn.closest('.np-peq-row')?.dataset.index), 1);
  npPeqRender(filters);
  npOptQueue({ eq_filters: filters }, 0);
}

async function npPeqImport(text) {
  const api = window.pywebview?.api;
  if (!api?.native_player_eq_import) return;
  let res;
  try { res = await api.native_player_eq_import(String(text || '')); } catch (e) { res = { status: 'error', error: String(e) }; }
  if (res?.status !== 'ok') { toastMgr.error(res?.error || 'Could not read the filter list.'); return; }
  if (playerNativeStatus) playerNativeStatus.options = res.options;
  npOptFill(res.options);
  showToast(`${res.imported} filter${res.imported === 1 ? '' : 's'} loaded.`, 'success');
}

function npPeqApplyText() { npPeqImport($('np-peq-text').value); }

function npPeqPickFile() { $('np-peq-file').click(); }

function npPeqLoadFile(input) {
  const file = input.files?.[0];
  input.value = '';
  if (!file) return;
  if (file.size > 64000) { toastMgr.error('That file is too large to be a filter list.'); return; }
  const reader = new FileReader();
  reader.onload = () => npPeqImport(reader.result);
  reader.onerror = () => toastMgr.error('Could not read the file.');
  reader.readAsText(file);
}

async function npPeqExport() {
  const wrap = $('np-peq-text-wrap');
  if (!wrap.classList.contains('hidden')) { wrap.classList.add('hidden'); return; }
  // Unsaved edits first, so the list shows what the rows show.
  clearTimeout(npOptSaveTimer);
  await npOptFlush();
  const api = window.pywebview?.api;
  let res = null;
  try { res = await api?.native_player_eq_export?.(); } catch (e) { res = null; }
  $('np-peq-text').value = res?.text || '';
  wrap.classList.remove('hidden');
}

async function npPeqCopy() {
  const text = $('np-peq-text').value;
  if (await copyText(text)) showToast('Filter list copied.', 'success');
  else { $('np-peq-text').select(); toastMgr.error('Could not copy: select the text and copy it by hand.'); }
}

// What can be set right now: without libmpv (or with the window's engine
// chosen) the native-only sections are greyed out and say why.
function npOptState(options) {
  const st = playerNativeStatus;
  const note = $('np-engine-note');
  let message = '';
  if (window.__mornyeWeb) message = 'In the web interface the browser plays the music on your device; these settings apply to the desktop app.';
  else if (!st?.available) message = `Native engine unavailable: ${st?.reason || 'libmpv not found'}. Install libmpv to use the settings below (see the guide, "Player").`;
  else if ((options?.engine || st.options?.engine) === 'browser') message = 'The window\'s own engine is selected: the settings below apply when the native engine plays.';
  if (note) { note.textContent = message; note.classList.toggle('hidden', !message); }
  const off = !!window.__mornyeWeb || !st?.available;
  document.querySelectorAll('#tc-player .np-native-only').forEach(sec => {
    sec.classList.toggle('np-disabled', off);
    sec.querySelectorAll('input, select, button, textarea').forEach(el => { el.disabled = off; });
  });
  $('np-opt-engine').disabled = !!window.__mornyeWeb;
}

// A device's name in the menu. macOS lists each device under two drivers
// (coreaudio/…, avfoundation/…) with the same description, Windows under
// wasapi/… only: when a description repeats, the driver tells them apart.
function npDeviceLabel(device, devices) {
  if (device.name === 'auto') return 'System default';
  const same = devices.filter(d => d.description === device.description).length;
  return same > 1 ? `${device.description} (${device.name.split('/')[0]})` : device.description;
}

async function loadPlayerSettings() {
  const api = window.pywebview?.api;
  if (window.__mornyeWeb || !api?.native_player_status) { npOptState(null); return; }
  try { playerNativeStatus = await api.native_player_status(); } catch (e) { playerNativeStatus = null; }
  npOptFill(playerNativeStatus?.options);
  if (!playerNativeStatus?.available) return;
  try {
    const res = await api.native_player_devices();
    const select = $('np-opt-audio_device');
    const current = playerNativeStatus.options.audio_device || 'auto';
    const devices = res?.devices || [];
    if (!devices.some(d => d.name === current)) devices.push({ name: current, description: `${current} (not connected)` });
    select.innerHTML = devices.map(d => `<option value="${escHtml(d.name)}">${escHtml(npDeviceLabel(d, devices))}</option>`).join('');
    select.value = current;
  } catch (e) { /* the list stays at "System default" */ }
}

// ── Mornye Link (api_mixins/link.py) ─────────────────────────────────────
// Every call answers with the same status, so one renderer draws the
// section. While the Player tab is open it is re-read every 2 s: a phone
// pairing, connecting or the PIN running out shows up without a click.
let g_linkPoll = null;
let g_linkPinShown = '';

function stopLinkPolling() { clearInterval(g_linkPoll); g_linkPoll = null; stopIntegrationPolling(); }

// ── Services: Discord and Last.fm (api_mixins/integrations.py) ────────────
// Desktop only. A connection is made in the background, so the status is
// asked again every few seconds while Settings › Player is open.
var g_integrationPoll = null;

function stopIntegrationPolling() { clearInterval(g_integrationPoll); g_integrationPoll = null; }

function openServiceUrl(url) {
  if (window.pywebview?.api?.open_url) window.pywebview.api.open_url(url);
  else window.open(url, '_blank', 'noopener');
}

function renderServiceStatus(id, connected, enabled, text) {
  const el = $(id);
  if (!el) return;
  el.classList.toggle('on', !!connected);
  el.classList.toggle('waiting', !connected && !!enabled);
  const label = el.querySelector('.svc-text');
  if (label) label.textContent = text || 'Off';
}

function renderDiscordPresence(s) {
  if (!s || s.status !== 'ok') return;
  const box = $('discord-enabled'), id = $('discord-app-id'), art = $('discord-artwork');
  if (box) box.checked = !!s.enabled;
  if (id && document.activeElement !== id) id.value = s.application_id || '';
  if (art) art.checked = !!s.show_artwork;
  if (id) id.disabled = !s.enabled;
  if (art) art.disabled = !s.enabled;
  renderServiceStatus('discord-status', s.connected, s.enabled, s.status_text);
}

async function loadIntegrationSettings() {
  const api = window.pywebview?.api;
  const available = !window.__MORNYE_WEB_MODE__ && !!api?.get_discord_presence;
  $('discord-section')?.classList.toggle('hidden', !available);
  $('lastfm-section')?.classList.toggle('hidden', !available || !api?.get_lastfm);
  if (!available) return;
  renderDiscordPresence(await linkCall('get_discord_presence'));
  renderLastFm(await linkCall('get_lastfm'));
  if (!g_integrationPoll) g_integrationPoll = setInterval(loadIntegrationSettings, 4000);
}

// ── Services: Last.fm (core/scrobbler.py) ──────────────────────────────────
function renderLastFm(s) {
  if (!s || s.status !== 'ok') return;
  const box = $('lastfm-enabled'), key = $('lastfm-key'), secret = $('lastfm-secret');
  if (box) box.checked = !!s.enabled;
  if (key && document.activeElement !== key) key.value = s.api_key || '';
  if (secret && document.activeElement !== secret) {
    secret.value = '';
    secret.placeholder = s.has_secret ? 'Saved — type to replace' : 'Not set';
  }
  for (const el of [key, secret]) if (el) el.disabled = !s.enabled;
  const connect = $('lastfm-connect');
  if (connect) {
    connect.classList.toggle('hidden', !!s.connected);
    connect.disabled = !s.enabled || !s.api_key || !s.has_secret || !!s.authorising;
    connect.textContent = s.authorising ? 'Waiting…' : 'Connect…';
  }
  $('lastfm-disconnect')?.classList.toggle('hidden', !s.connected);
  $('lastfm-retry')?.classList.toggle('hidden', !s.connected || !s.pending);
  const pending = $('lastfm-pending');
  if (pending) {
    pending.classList.toggle('hidden', !s.pending);
    pending.textContent = s.pending ? `${s.pending} scrobble${s.pending === 1 ? '' : 's'} waiting to be sent` : '';
  }
  renderServiceStatus('lastfm-status', s.connected, s.enabled, s.status_text);
}

async function lastFmCall(name, ...args) {
  let res = null;
  try { res = await window.pywebview?.api?.[name]?.(...args); } catch (e) { res = { error: String(e) }; }
  if (res?.status !== 'ok') { toastMgr.error(res?.error || 'Last.fm did not answer.'); return null; }
  renderLastFm(res);
  return res;
}

function saveLastFm(setting, value) {
  const changes = { enabled: null, api_key: null, secret: null, [setting]: value };
  // An emptied secret field means "keep it": the stored one is never shown.
  if (setting === 'secret' && !String(value || '').trim()) return;
  return lastFmCall('save_lastfm', changes.enabled, changes.api_key, changes.secret);
}

async function connectLastFm() {
  const res = await lastFmCall('connect_lastfm');
  if (res) toastMgr.info?.('Approve Mornye on the Last.fm page that just opened.');
}

function disconnectLastFm() { return lastFmCall('disconnect_lastfm'); }
function retryLastFm() { return lastFmCall('retry_lastfm'); }

async function saveDiscordPresence(setting, value) {
  const changes = { enabled: null, application_id: null, show_artwork: null, [setting]: value };
  let res = null;
  try { res = await window.pywebview?.api?.save_discord_presence?.(changes.enabled, changes.application_id, changes.show_artwork); } catch (e) { res = { error: String(e) }; }
  if (res?.status !== 'ok') { toastMgr.error(res?.error || 'Could not save the Discord settings.'); return; }
  renderDiscordPresence(res);
}

async function linkCall(name, ...args) {
  const api = window.pywebview?.api;
  if (!api?.[name]) return null;
  try {
    const res = await api[name](...args);
    // --web multi-user answers non-admins "Not found": no section for them.
    return res && !res.error ? res : null;
  } catch (e) { return null; }
}

// Settings → Mornye Connect: this device's name, and (--web) the server
// speaker switch, which only admins get an answer for.
async function loadConnectSettings() {
  const section = $('connect-section');
  if (!section) return;
  section.classList.toggle('hidden', !connect.ready);
  if (!connect.ready) return;
  const input = $('connect-name');
  if (document.activeElement !== input) input.value = connect.name || '';
  const status = await linkCall('connect_speaker_status');
  const row = $('connect-speaker-row');
  row.classList.toggle('hidden', !status?.supported);
  if (!status?.supported) return;
  const box = $('connect-speaker');
  box.checked = !!status.enabled;
  box.disabled = !!status.forced || (!status.available && !status.enabled);
  $('connect-speaker-hint').textContent = status.forced
    ? 'On through MORNYE_CONNECT_SERVER_SPEAKER.'
    : status.available
      ? 'The server plays out loud through its own audio output when picked in "Play on". Admins only.'
      : `Unavailable: ${status.reason || 'libmpv was not found on the server'}.`;
}

async function connectSaveName(value) {
  const name = String(value || '').trim();
  if (!name || !connect.ready) return;
  try { localStorage.setItem(CONNECT_NAME_KEY, name); } catch (e) {}
  const res = await connectApi()?.connect_rename(connect.id, name);
  if (res?.status === 'ok') { connect.name = name; connectDevices(res); toastMgr.success('Name saved.'); }
}

async function connectToggleSpeaker(on) {
  const res = await linkCall('connect_speaker_set', !!on);
  if (!res || res.status !== 'ok') toastMgr.error(res?.error || 'Only an admin can change this.');
  loadConnectSettings();
}

async function loadLinkSettings() {
  const status = await linkCall('link_status');
  $('link-section')?.classList.toggle('hidden', !status);
  if (!status) return;
  renderLink(status);
  loadLinkComputers();
  if (!g_linkPoll) {
    g_linkPoll = setInterval(async () => {
      if (!$('tc-player')?.classList.contains('active') || !$('view-settings')?.classList.contains('active')) {
        stopLinkPolling();
        return;
      }
      const next = await linkCall('link_status');
      if (next) renderLink(next);
      loadLinkComputers();
    }, 2000);
  }
}

function linkAgo(ms) {
  if (!ms) return 'never';
  const s = Math.max(0, Math.round((Date.now() - ms) / 1000));
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return new Date(ms).toLocaleDateString();
}

const LINK_ENDED = {
  paired: 'Paired.',
  expired: 'The PIN expired.',
  attempts: 'Too many wrong PINs: show a new one.',
};

function renderLink(s) {
  const on = !!s.enabled;
  $('link-enabled').checked = on;
  const running = on && s.running;
  $('link-address-row').classList.toggle('hidden', !running);
  $('link-pair-row').classList.toggle('hidden', !running);
  $('link-devices').classList.toggle('hidden', !(s.devices || []).length);

  const err = $('link-error');
  const message = on && !s.running ? (s.error || 'Starting…') : '';
  err.textContent = message;
  err.classList.toggle('hidden', !message);

  if (running) {
    $('link-address').innerHTML = (s.addresses || []).map(a => `<code>${escHtml(a)}</code>`).join('')
      || `<code>port ${escHtml(String(s.port))}</code>`;
    $('link-address-hint').textContent = s.discovery
      ? 'The phone finds it on the same Wi-Fi; or add it by address'
      : s.discoveryError
        ? 'The phone cannot find it by itself here: add it by address'
        : 'Announcing it on the Wi-Fi…';
  }

  // The PIN, its countdown and the certificate code the phone shows too.
  const pin = $('link-pin');
  const btn = $('link-pair-btn');
  if (s.pairing) {
    const left = Math.max(0, Math.round((s.pairing.expiresMs - Date.now()) / 1000));
    const mmss = `${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')}`;
    pin.innerHTML = `<span class="link-pin-code">${escHtml(s.pairing.pin.replace(/(\d{3})(\d{3})/, '$1 $2'))}</span>`
      + `<span class="link-pin-meta">Code ${escHtml(s.shortFingerprint)} · ${mmss}</span>`;
    pin.classList.remove('hidden');
    btn.textContent = 'Cancel';
    g_linkPinShown = s.pairing.pin;
  } else {
    pin.classList.add('hidden');
    btn.textContent = 'Show PIN';
    if (g_linkPinShown) {
      g_linkPinShown = '';
      const said = LINK_ENDED[s.pairingEnded];
      if (said) showToast(said, s.pairingEnded === 'paired' ? 'success' : 'info');
    }
  }

  // Devices asking to connect without a PIN (also asked in a dialog).
  const requests = running ? (s.requests || []) : [];
  $('link-requests').classList.toggle('hidden', !requests.length);
  $('link-requests').innerHTML = requests.map(r => `
    <div class="link-device">
      <span class="link-device-dot on" aria-hidden="true"></span>
      <span class="link-device-name">${escHtml(r.name)} wants to connect<small>${escHtml(r.platform)} · ${escHtml(r.address)}</small></span>
      <span class="link-device-actions">
        <button class="act-btn secondary" type="button" data-request="${escHtml(r.id)}" data-onclick="answerLinkRequest(this, false)">Deny</button>
        <button class="act-btn" type="button" data-request="${escHtml(r.id)}" data-onclick="answerLinkRequest(this, true)">Allow</button>
      </span>
    </div>`).join('');
  $('link-trust-row').classList.toggle('hidden', !on);
  $('link-trust').checked = !!s.trustNetwork;

  $('link-devices').innerHTML = (s.devices || []).map(d => `
    <div class="link-device">
      <span class="link-device-dot${d.connected ? ' on' : ''}" aria-hidden="true"></span>
      <span class="link-device-name">${escHtml(d.name)}<small>${escHtml(d.platform)} · ${d.connected ? 'connected' : `seen ${escHtml(linkAgo(d.lastSeenMs))}`}</small></span>
      <button class="act-btn secondary" type="button" data-device="${escHtml(d.deviceId)}" data-name="${escHtml(d.name)}" data-onclick="revokeLinkDevice(this)">Unpair</button>
    </div>`).join('');
}

async function toggleLink(on) {
  $('link-error').textContent = on ? 'Starting…' : '';
  const status = await linkCall('link_set_enabled', !!on);
  if (status) renderLink(status);
}

async function toggleLinkPairing() {
  const status = await linkCall(g_linkPinShown ? 'link_end_pairing' : 'link_begin_pairing');
  if (status) {
    // A cancel is the person's own doing: no toast for it.
    if (g_linkPinShown && !status.pairing) g_linkPinShown = '';
    renderLink(status);
  }
}

async function revokeLinkDevice(btn) {
  const name = btn?.dataset.name || 'this device';
  const ok = await askConfirm(`${name} will no longer reach this library until it pairs again.`,
    { title: `Unpair ${name}?`, ok: 'Unpair', danger: true });
  if (!ok) return;
  const status = await linkCall('link_revoke_device', btn.dataset.device);
  if (status) renderLink(status);
}

// A phone or computer asks to connect without a PIN (link_server.py): every
// page of the account asks; the first answer counts. One dialog at a time.
const linkAsked = new Set();
let linkAsking = Promise.resolve();
window.app_link_pair_request = (req) => {
  if (!req?.id || linkAsked.has(req.id)) return;
  linkAsked.add(req.id);
  linkAsking = linkAsking.then(async () => {
    const ok = await askConfirm(
      `${req.name} (${req.platform}, ${req.address}) asks to connect to this computer. It will browse and play this library, and control what plays here. Allow it only if it is yours.`,
      { title: `Allow ${req.name}?`, ok: 'Allow', cancel: 'Deny' });
    // Another question took the dialog: still waiting, in Settings → Mornye Link.
    if (ok === null) return;
    const status = await linkCall('link_answer_pair_request', req.id, !!ok);
    if (status && $('link-section') && !$('link-section').classList.contains('hidden')) renderLink(status);
    if (ok) toastMgr.success(`${req.name} is connected.`);
  });
};

async function answerLinkRequest(btn, allow) {
  const status = await linkCall('link_answer_pair_request', btn.dataset.request, !!allow);
  if (status) renderLink(status);
}

async function toggleLinkTrust(on) {
  if (on) {
    const ok = await askConfirm('Every phone and computer on this network will connect without a PIN and without asking you. Anyone on it could then browse, download and play this library.',
      { title: 'Trust this network?', ok: 'Trust it', danger: true });
    if (!ok) { $('link-trust').checked = false; return; }
  }
  const status = await linkCall('link_set_trust_network', !!on);
  if (status) renderLink(status);
}

// Settings → Mornye Link → Other computers (core/connect/link_peers.py).
const LINK_COMPUTER_STATE = {
  connected: 'connected',
  connecting: 'connecting…',
  offline: 'not reachable now',
  unauthorized: 'unpaired there: pair again',
  waiting: 'waiting for someone to allow it there…',
  refused: 'refused there',
  failed: 'could not connect',
};
let g_linkPairFingerprint = '';

async function loadLinkComputers() {
  const res = await linkCall('link_computers');
  if (res) renderLinkComputers(res.computers || []);
}

function renderLinkComputers(list) {
  const box = $('link-computers');
  if (!box) return;
  box.classList.toggle('hidden', !list.length);
  box.innerHTML = list.map(c => `
    <div class="link-device">
      <span class="link-device-dot${c.state === 'connected' ? ' on' : ''}" aria-hidden="true"></span>
      <span class="link-device-name">${escHtml(c.name)}<small>${escHtml(c.address)} · ${escHtml(c.state === 'failed' && c.message ? c.message : (LINK_COMPUTER_STATE[c.state] || c.state))}</small></span>
      <button class="act-btn secondary" type="button" data-server="${escHtml(c.serverId)}" data-name="${escHtml(c.name)}" data-asking="${c.serverId.startsWith('request:') ? '1' : ''}" data-onclick="removeLinkComputer(this)">${c.state === 'waiting' ? 'Cancel' : c.serverId.startsWith('request:') ? 'Dismiss' : 'Remove'}</button>
    </div>`).join('');
}

async function findLinkComputers() {
  const btn = $('link-find-btn');
  const box = $('link-found');
  btn.disabled = true;
  btn.textContent = 'Searching…';
  try {
    const res = await linkCall('link_find_computers');
    const found = res?.found || [];
    box.classList.remove('hidden');
    box.innerHTML = found.length
      ? found.map(c => `
        <div class="link-device">
          <span class="link-device-name">${escHtml(c.name)}<small>${escHtml(c.host)}:${escHtml(String(c.port))}${c.paired ? ' · paired' : ''}</small></span>
          <span class="link-device-actions">
            <button class="act-btn secondary" type="button" data-host="${escHtml(c.host)}" data-port="${escHtml(String(c.port))}" data-fp="${escHtml(c.fingerprint || '')}" data-name="${escHtml(c.name)}" data-onclick="startLinkComputerPair(this)">Use a PIN</button>
            <button class="act-btn" type="button" data-host="${escHtml(c.host)}" data-port="${escHtml(String(c.port))}" data-fp="${escHtml(c.fingerprint || '')}" data-name="${escHtml(c.name)}" data-onclick="connectLinkComputer(this)">${c.paired ? 'Connect again' : 'Connect'}</button>
          </span>
        </div>`).join('')
      : '<div class="link-error">No other computer found. Turn Mornye Link on there, or add it by address.</div>';
  } finally {
    btn.disabled = false;
    btn.textContent = 'Find computers';
  }
}

function startLinkComputerPair(btn) {
  const host = btn?.dataset?.host || '';
  const port = btn?.dataset?.port || '';
  g_linkPairFingerprint = btn?.dataset?.fp || '';
  $('link-computer-pair-name').textContent = btn?.dataset?.name || 'a computer';
  const address = $('link-computer-address');
  address.value = host ? `${host.includes(':') ? `[${host}]` : host}:${port}` : '';
  $('link-computer-pin').value = '';
  $('link-computer-pair-row').classList.remove('hidden');
  (host ? $('link-computer-pin') : address).focus();
}

function parseLinkAddress(text) {
  const value = String(text || '').trim();
  const v6 = /^\[([^\]]+)\]:(\d+)$/.exec(value);
  if (v6) return { host: v6[1], port: Number(v6[2]) };
  const v4 = /^([^:\s]+):(\d+)$/.exec(value);
  return v4 ? { host: v4[1], port: Number(v4[2]) } : null;
}

// Without a PIN: the other computer asks someone there to allow this one
// (or, trusting this network, lets it in at once). The list shows the wait.
async function connectLinkComputer(btn) {
  const res = await linkCall('link_connect_computer', btn.dataset.host, Number(btn.dataset.port),
    btn.dataset.fp || '', btn.dataset.name || '');
  if (!res?.ok) { toastMgr.error(res?.message || 'Could not connect.'); return; }
  $('link-found').classList.add('hidden');
  toastMgr.info(`Asked ${btn.dataset.name || 'the computer'}: allow this computer there.`);
  renderLinkComputers(res.computers || []);
}

async function pairLinkComputer() {
  const address = parseLinkAddress($('link-computer-address').value);
  if (!address) { toastMgr.error('Type the address as host:port, as Mornye Link shows it there.'); return; }
  const pin = $('link-computer-pin').value.replace(/\D/g, '');
  if (!pin) {
    const name = $('link-computer-pair-name').textContent;
    await connectLinkComputer({ dataset: { host: address.host, port: String(address.port), fp: g_linkPairFingerprint, name: name === 'a computer' ? '' : name } });
    $('link-computer-pair-row').classList.add('hidden');
    return;
  }
  if (pin.length !== 6) { toastMgr.error('The PIN has 6 digits, or leave it empty to ask there.'); return; }
  const btn = $('link-computer-pair-btn');
  btn.disabled = true;
  btn.textContent = 'Pairing…';
  try {
    const res = await linkCall('link_pair_computer', address.host, address.port, pin, g_linkPairFingerprint);
    if (!res?.ok) { toastMgr.error(res?.message || 'Pairing failed.'); return; }
    toastMgr.success(`Paired with ${res.computer?.name || 'the computer'}. It shows in "Play on".`);
    $('link-computer-pair-row').classList.add('hidden');
    $('link-found').classList.add('hidden');
    g_linkPairFingerprint = '';
    renderLinkComputers(res.computers || []);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Pair';
  }
}

async function removeLinkComputer(btn) {
  if (btn?.dataset.asking) {
    const res = await linkCall('link_remove_computer', btn.dataset.server);
    if (res) renderLinkComputers(res.computers || []);
    return;
  }
  const name = btn?.dataset.name || 'this computer';
  const ok = await askConfirm(`${name} leaves "Play on" here. To stop it reaching this computer too, unpair it in Mornye Link there.`,
    { title: `Remove ${name}?`, ok: 'Remove', danger: true });
  if (!ok) return;
  const res = await linkCall('link_remove_computer', btn.dataset.server);
  if (res) renderLinkComputers(res.computers || []);
}

function npOptQueue(patch, delay) {
  Object.assign(npOptPending, patch);
  clearTimeout(npOptSaveTimer);
  npOptSaveTimer = setTimeout(npOptFlush, delay);
}

async function npOptFlush() {
  const patch = npOptPending;
  npOptPending = {};
  const api = window.pywebview?.api;
  if (!api?.native_player_set_options || !Object.keys(patch).length) return;
  let res;
  try { res = await api.native_player_set_options(patch); } catch (e) { res = { status: 'error', error: String(e) }; }
  if (res?.status !== 'ok') { toastMgr.error(res?.error || 'Could not save the player settings.'); return; }
  if (playerNativeStatus) playerNativeStatus.options = res.options;
  // A preset fills the sliders; a slider makes the preset "Custom".
  if ('eq_preset' in patch || 'eq_gains' in patch) npOptFill(res.options);
  if ('eq_mode' in patch) npPeqShowMode(res.options.eq_mode);
  // Values out of range come back clamped; redraw unless a row is being typed in.
  if ('eq_filters' in patch && !$('np-peq-list')?.contains(document.activeElement)) npPeqRender(res.options.eq_filters || []);
  if ('engine' in patch) { npOptState(res.options); playerSetupEngine(); }
}

(function initPlayerSettings() {
  const pane = $('tc-player');
  if (!pane) return;
  const onChange = (e, live) => {
    const el = e.target;
    if (el.dataset.npOpt) {
      const name = el.dataset.npOpt;
      const value = npOptValue(el);
      if (el.type === 'range') { amPaint(el); npOptShowValue(name, value); }
      if (live && el.type !== 'range') return; // selects and toggles report on change
      npOptQueue({ [name]: value }, el.type === 'range' ? 200 : 0);
    } else if (el.dataset.npSpdif != null) {
      if (live) return;
      const spdif = [...pane.querySelectorAll('[data-np-spdif]')].filter(c => c.checked).map(c => c.dataset.npSpdif);
      npOptQueue({ spdif }, 0);
    } else if (el.closest('.np-peq-row')) {
      // Typing waits for a pause; a type, a switch or leaving a field saves now.
      npOptQueue({ eq_filters: npPeqCollect() }, live ? 400 : 0);
    } else if (el.classList.contains('np-eq-slider')) {
      amPaint(el);
      const eq_gains = [...pane.querySelectorAll('.np-eq-slider')].map(s => Number(s.value));
      $('np-opt-eq_preset').value = 'custom';
      npOptQueue({ eq_gains, eq_preset: 'custom' }, 200);
    }
  };
  pane.addEventListener('input', e => onChange(e, true));
  pane.addEventListener('change', e => onChange(e, false));
})();

// ── Playback statistics (I) ─────────────────────────────────────────────────
function playerStatsRows(stats, engine) {
  const rows = [['Engine', engine === 'native' ? 'Native (libmpv)' : 'Window (built-in)']];
  if (!stats) return rows;
  const p = stats.params || {}, o = stats.out_params || {};
  const fmt = (v, unit = '') => (v == null || v === '' ? '—' : `${v}${unit}`);
  rows.push(
    ['File', stats.path ? stats.path.split(/[\\/]/).pop() : '—'],
    ['Codec', stats.codec_desc ? `${stats.codec} (${stats.codec_desc})` : fmt(stats.codec)],
    ['Source', [p.format, p.samplerate && `${p.samplerate / 1000} kHz`, p['hr-channels'] || p.channels].filter(Boolean).join(' · ') || '—'],
    ['Bitrate', stats.bitrate ? `${Math.round(stats.bitrate / 1000)} kbps` : '—'],
    ['Output', stats.output || '—'],
    ['Output format', [o.format, o.samplerate && `${o.samplerate / 1000} kHz`, o['hr-channels'] || o.channels].filter(Boolean).join(' · ') || '—'],
    ['Bitstream', stats.bitstream ? 'Yes' : 'No'],
    ['Audio output', `${fmt(stats.ao)} · ${fmt(stats.device)}`],
    ['Buffer', stats.cache_s != null ? `${Number(stats.cache_s).toFixed(1)} s${stats.cache_bytes ? ` · ${Math.round(stats.cache_bytes / 1024)} KB` : ''}` : '—'],
    ['Speed', stats.speed != null ? `${Number(stats.speed).toFixed(2)}×` : '—'],
    ['Audio delay', `${stats.delay_ms || 0} ms`],
    ['A-B repeat', (stats.ab_loop || []).every(v => typeof v === 'number') ? stats.ab_loop.map(fmtTime).join(' – ') : 'Off'],
    ['ReplayGain', stats.replaygain === 'no' ? 'Off' : stats.replaygain],
    ['Filters', stats.filters?.length ? `${stats.filters.length} active` : 'None'],
  );
  return rows;
}

let playerStatsTimer = 0;

async function renderPlayerStats() {
  const list = $('player-stats-list');
  if (!list) return;
  let stats = null;
  const engine = playerEngine.kind;
  if (engine === 'native') {
    try { stats = (await window.pywebview.api.native_player_stats())?.stats || null; } catch (e) { stats = null; }
  } else if (playerEngine.hasSource) {
    stats = {
      path: playerQueue[playerIndex]?.path || '',
      codec: playerSourceQuality,
      output: 'PCM to the system mixer',
      speed: playerEngine.playbackRate,
      ab_loop: [playerAb.a, playerAb.b],
      replaygain: 'no',
    };
  }
  list.innerHTML = playerStatsRows(stats, engine)
    .map(([k, v]) => `<dt>${escHtml(k)}</dt><dd>${escHtml(String(v))}</dd>`).join('');
}

function openPlayerStats() {
  $('player-stats-modal').classList.remove('hidden');
  renderPlayerStats();
  clearInterval(playerStatsTimer);
  playerStatsTimer = setInterval(renderPlayerStats, 1000);
}

function closePlayerStats() {
  $('player-stats-modal').classList.add('hidden');
  clearInterval(playerStatsTimer);
}

// ── Keyboard, as in MPC-HC ──────────────────────────────────────────────────
// Only while something is loaded, and never while typing in a field or
// with a dialog other than the full-screen player open.
function playerShortcutAction(e) {
  if (e.metaKey || e.ctrlKey || e.altKey) return null;
  const k = e.key;
  if (k === ' ') return 'toggle';
  if (k === 'ArrowRight') return e.shiftKey ? 'fwd30' : 'fwd5';
  if (k === 'ArrowLeft') return e.shiftKey ? 'back30' : 'back5';
  if (k === '+' || k === '=') return 'volup';
  if (k === '-' || k === '_') return 'voldown';
  if (k === 'm' || k === 'M') return 'mute';
  if (k === ']') return 'faster';
  if (k === '[') return 'slower';
  if (k === 'Backspace') return 'speed1';
  if (k === 'l' || k === 'L') return 'ab';
  if (k === 'i' || k === 'I') return 'stats';
  return null;
}

function playerSetVolumePercent(value) {
  const v = Math.max(0, Math.min(100, Math.round(value)));
  playerEngine.volume = v / 100;
  ['pl-vol', 'np-vol'].forEach(id => { const el = $(id); if (el) { el.value = v; amPaint(el); } });
  connectSchedule();
}

document.addEventListener('keydown', (e) => {
  if (!playerEngine.hasSource || e.defaultPrevented) return;
  const t = e.target;
  if (t instanceof Element && (t.closest('input, textarea, select, [contenteditable=""], [contenteditable="true"]')
    || (t.closest('button, [data-activate]') && (e.key === ' ' || e.key === 'Enter')))) return;
  if (document.querySelector('.modal:not(.hidden):not(#player-stats-modal)')) return;
  const action = playerShortcutAction(e);
  if (!action) return;
  e.preventDefault();
  const audio = playerEngine;
  const seekBy = s => {
    if (!Number.isFinite(audio.duration)) return;
    audio.currentTime = Math.max(0, Math.min(audio.duration - 0.5, audio.currentTime + s));
  };
  switch (action) {
    case 'toggle': playerToggle(); break;
    case 'fwd5': seekBy(5); break;
    case 'back5': seekBy(-5); break;
    case 'fwd30': seekBy(30); break;
    case 'back30': seekBy(-30); break;
    case 'volup': playerSetVolumePercent(audio.volume * 100 + 5); break;
    case 'voldown': playerSetVolumePercent(audio.volume * 100 - 5); break;
    case 'mute': audio.muted = !audio.muted; toastMgr.info(audio.muted ? 'Muted' : 'Sound on'); break;
    case 'faster': playerSetSpeed(0.1); break;
    case 'slower': playerSetSpeed(-0.1); break;
    case 'speed1': playerSetSpeed(0); break;
    case 'ab': playerAbCycle(); break;
    case 'stats': $('player-stats-modal').classList.contains('hidden') ? openPlayerStats() : closePlayerStats(); break;
  }
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && !$('player-stats-modal')?.classList.contains('hidden')) closePlayerStats();
});

// The filled share of an Apple Music slider (.am-range), drawn from --p.
function amPaint(el) {
  if (!el) return;
  const min = Number(el.min) || 0, max = Number(el.max) || 100;
  const p = max > min ? (Number(el.value) - min) / (max - min) * 100 : 0;
  el.style.setProperty('--p', p.toFixed(2) + '%');
}

// The played share of the seek bar, which its CSS draws from --p.
function npPaintSeek() {
  const seek = $('np-seek');
  if (seek) seek.style.setProperty('--p', (seek.value / 10).toFixed(1) + '%');
}

// ── Now playing: cover, synced lyrics, queue ────────────────────────────────
let npLines = [];       // [{t: seconds|null, text}]
let npSynced = false;
let npWordSynced = false;
let npActive = -1;
const NP_SYNC = { offset: 0, speed: 1 };

function npLyricsTime(audioTime) {
  return Math.max(0, audioTime * NP_SYNC.speed + NP_SYNC.offset);
}

function npAudioTimeForLyrics(lyricsTime) {
  return Math.max(0, (lyricsTime - NP_SYNC.offset) / NP_SYNC.speed);
}

function npSetSync(kind, rawValue) {
  const value = Number(rawValue);
  if (!Number.isFinite(value)) return;
  if (kind === 'offset') {
    NP_SYNC.offset = Math.max(-5, Math.min(5, value));
    $('np-sync-offset-value').textContent = `${NP_SYNC.offset > 0 ? '+' : ''}${NP_SYNC.offset.toFixed(1)} s`;
  } else if (kind === 'speed') {
    NP_SYNC.speed = Math.max(0.9, Math.min(1.1, value));
    $('np-sync-speed-value').textContent = `${NP_SYNC.speed.toFixed(2)}×`;
  } else {
    return;
  }
  NP.focus = -2;
  npUpdate(npLyricsTime(playerEngine.currentTime), true);
}

function npResetSync() {
  NP_SYNC.offset = 0;
  NP_SYNC.speed = 1;
  $('np-sync-offset').value = '0';
  $('np-sync-offset-value').textContent = '0.0 s';
  $('np-sync-speed').value = '1';
  $('np-sync-speed-value').textContent = '1.00×';
}

function openNowPlaying() {
  if (document.body.classList.contains('player-idle')) return;
  $('np-view').classList.remove('hidden');
  npStartTick();
  npCanvasSync();
  if (npLines.length) { npLayoutLyrics(); NP.focus = -2; NP.first = true; npUpdate(npLyricsTime(playerEngine.currentTime), true); }
}
function closeNowPlaying() { closeActionMenu(); $('np-view').classList.add('hidden'); npCanvasSync(); }

// Now Playing's "…": the song playing.
function npOpenMenu(button) {
  const cur = playerQueue[playerIndex];
  if (cur) openActionMenu(button, trackMenuItems(cur, { nowPlaying: true }));
}

function npShow(which) {
  $('np-lyrics').classList.toggle('hidden', which !== 'lyrics');
  $('np-queue').classList.toggle('hidden', which !== 'queue');
  $('np-tab-lyrics').classList.toggle('active', which === 'lyrics');
  $('np-tab-queue').classList.toggle('active', which === 'queue');
  if (which === 'queue') npRenderQueue();
}

// Lyrics in any LRC flavour:
//   [mm:ss.xx]text                      line-synced
//   [mm:ss.xx]v1:<t>Word <t_end><t>Next  enhanced (word-by-word), "v1:/v2:" is
//                                         the singer, as written by Apple/lrc.red
//   [bg:(backing vocals)]               attached to the line before it
//   [x-translated:K:...] [x-romanized:K:...]  translation / pronunciation of
//                                         the line at K ms (index K without
//                                         timing), as the Mornye app writes
//                                         them in .mlrc; the older
//                                         [x-translation:K:base64] still read
// A tag between two words is both the end of the first and the start of the
// second; when there are two (end, then start) the pair can differ by a gap.
function parseLyrics(text) {
  const WORD_SPLIT = /(<\d{1,3}:\d{1,2}(?:[.:]\d{1,3})?>)/;
  const WORD_TS = /<(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?>/;
  const LEAD_TS = /^((?:\[\d{1,3}:\d{1,2}(?:[.:]\d{1,3})?\]\s*)+)/;
  const ONE_TS = /\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]/g;
  const VOICE = /^((?:\s*<[^>]*>)*)\s*(v[1-9]\d*):[ \t]*/i;
  const ID_TAG = /^\[(ti|ar|al|by|offset|length|re|ve|tool|au|la|encoder|instrumental|x-[a-z0-9_-]+):.*\]$/i;
  const SUPP = /^\[x-(romanized-words|romanized|translated-words|translated|romaji-words|romaji|translation-words|translation):(\d+):(.*)\]$/i;
  const BG = /^\[bg:(.*)\]$/i;
  const sec = m => Number(m[1]) * 60 + Number(m[2]) + (m[3] ? Number('0.' + m[3]) : 0);
  const stripWordTags = t => t.replace(/<\d{1,3}:\d{1,2}(?:[.:]\d{1,3})?>/g, '').replace(/\s+/g, ' ').trim();

  // The words of a piece of text with their own times (<mm:ss.xx> tags), or
  // null: a line's body, or the backing vocals of a [bg:…] tag.
  const timedWords = (body, fallback) => {
    if (!WORD_TS.test(body)) return null;
    const words = [];
    let run = [];
    let prev = null;
    for (const part of body.split(WORD_SPLIT)) {
      const tag = WORD_TS.exec(part);
      if (tag && part.startsWith('<')) { run.push(sec(tag)); continue; }
      if (!part) continue;
      if (!part.trim()) { if (prev) prev.text += part; continue; }
      const start = run.length ? run[run.length - 1] : (prev ? (prev.e ?? prev.s) : fallback);
      if (prev && prev.e == null) prev.e = run.length ? run[0] : start;
      prev = { s: start, e: null, text: words.length ? part : part.replace(/^\s+/, '') };
      words.push(prev);
      run = [];
    }
    if (prev && prev.e == null) {
      if (run.length) prev.e = run[0];
      // No closing stamp: a guess, which fillNowPlaying() refines once
      // it knows when the next line starts.
      else { prev.e = prev.s + 0.6; prev.open = true; }
    }
    return words.length ? words : null;
  };

  const synced = [], plain = [];
  let last = null;
  const supplements = [];
  // U+2800 is the blank written between words for Jellyfin (core/lyrics.py
  // JELLYFIN_WORD_GAP, and other taggers such as SpotiFLAC): trim() keeps it
  // inside the word, where it draws as a space wider than a space.
  for (const raw of String(text || '').replace(/\r/g, '').replace(/⠀/g, ' ').split('\n')) {
    const line = raw.trim();
    if (!line) continue;
    let m = SUPP.exec(line);
    if (m) { const sup = lyricSupplement(m); if (sup) supplements.push(sup); continue; }
    if (ID_TAG.test(line)) continue;
    m = BG.exec(line);
    // Backing vocals keep their word times when they have them, so they
    // light up word by word under their line like the line itself.
    if (m) { if (last) { last.bg = stripWordTags(m[1]); last.bgWords = timedWords(m[1], last.t); } continue; }

    const lead = LEAD_TS.exec(line);
    if (!lead) { plain.push({ t: null, text: stripWordTags(line), words: null, voice: 1 }); continue; }
    const stamps = [...lead[1].matchAll(ONE_TS)].map(sec);
    let body = line.slice(lead[0].length);
    let voice = 1;
    const v = VOICE.exec(body);
    if (v) {
      voice = Number(v[2].slice(1)) || 1;
      body = v[1].replace(/\s+/g, '') + body.slice(v[0].length);
    }

    // Words with their own times, if the line carries any.
    const words = timedWords(body, stamps[0]);
    const lineText = words ? words.map(w => w.text).join('').trim() : stripWordTags(body);
    for (const t of stamps) {
      const entry = { t, text: lineText, words, voice, bg: '', tr: '', rom: '' };
      synced.push(entry);
      last = entry;
    }
  }
  // `[t]<t>Whole line<t_end>` on every line is line-synced lyrics written in
  // the enhanced form: with no line split into two timed pieces there is no
  // word timing to show, so it is shown line by line.
  if (synced.length && !synced.some(l => l.words && l.words.length > 1)) {
    synced.forEach(l => { l.words = null; });
  }
  const attach = (target, sup) => {
    if (!target) return;
    if (sup.kind === 'translation') target.tr = sup.text;
    else if (sup.kind === 'romaji') target.rom = sup.text;
  };
  if (synced.length) {
    // The key is the line's time in ms; a key no line starts at is an index.
    const byMs = new Map(synced.map(l => [Math.round(l.t * 1000), l]));
    supplements.forEach(sup => attach(byMs.get(sup.key) ?? synced[sup.key], sup));
    return { lines: synced.sort((a, b) => a.t - b.t), synced: true, wordSynced: synced.some(l => l.words) };
  }
  supplements.forEach(sup => attach(plain[sup.key], sup));
  while (plain.length && !plain[0].text) plain.shift();
  return { lines: plain, synced: false, wordSynced: false };
}

// One [x-…:key:value] row as { kind, key, text }, or null for word timing
// and damaged values (lib: core/lyric_tags.py). The readable form escapes
// "\n" and "\\"; the older x-translation / x-romaji rows are base64.
function lyricSupplement(m) {
  const written = m[1].toLowerCase();
  const KINDS = { romanized: 'romaji', translated: 'translation' };
  const legacy = written === 'romaji' || written === 'translation';
  const kind = KINDS[written] || (legacy ? written : null);
  if (!kind) return null; // -words: word timing is not drawn for these
  let text;
  if (legacy) {
    try { text = new TextDecoder().decode(Uint8Array.from(atob(m[3]), c => c.charCodeAt(0))); }
    catch (e) { return null; }
  } else {
    text = m[3].replace(/\\(.)/g, (_, c) => (c === 'n' ? '\n' : c === '\\' || c === '<' ? c : '\\' + c));
  }
  text = text.trim();
  return text ? { key: Number(m[2]), kind, text } : null;
}

// A timed piece can hold several words: a provider that times phrases, or a
// line with a single stamp (`[00:51.00]<00:51.00>I don't know how … stay`).
// The lit copy of a piece is laid over it on one row and cannot wrap with
// it, so a phrase longer than the row ran off the right edge ("…you to trea")
// while the dim copy underneath wrapped and stayed grey. Each word becomes a
// piece of its own, the phrase's time shared out by length, so the lit copy
// wraps word by word with the text it covers.
function npSplitTimedPhrases(words) {
  const out = [];
  for (const w of words) {
    const tokens = w.text.match(/\S+\s*/g);
    if (!tokens || tokens.length < 2) { out.push(w); continue; }
    const lead = w.text.match(/^\s*/)[0];
    const total = tokens.reduce((n, t) => n + t.trim().length, 0) || 1;
    const span = Math.max(0, (w.e ?? w.s) - w.s);
    let at = w.s;
    tokens.forEach((t, k) => {
      const share = span * t.trim().length / total;
      const last = k === tokens.length - 1;
      out.push({ s: at, e: last ? w.e : at + share, text: (k === 0 ? lead : '') + t });
      at += share;
    });
  }
  return out;
}

function npTechLabel(res) {
  const i = res.info || {};
  const parts = [(i.format || '').toUpperCase()];
  if (i.bit_depth) parts.push(`${i.bit_depth}-bit`);
  if (i.sample_rate) parts.push(`${i.sample_rate / 1000} kHz`);
  if (!i.bit_depth && i.bitrate) parts.push(`${Math.round(i.bitrate / 1000)} kbps`);
  let label = parts.filter(Boolean).join(' · ');
  if (res.mode === 'flac-copy') label += ' · lossless copy';
  return label;
}

function npApplyColors(src) {
  const bg = $('np-bg');
  // The music look paints the cover itself, blurred, behind the view.
  if (src) bg.style.setProperty('--np-art', `url("${String(src).replace(/["\\\n]/g, c => encodeURIComponent(c))}")`);
  else bg.style.removeProperty('--np-art');
  if (!src) { bg.style.setProperty('--np-bg', 'linear-gradient(135deg, #5a3a3a, #2c1f1f)'); return; }
  const img = new Image();
  img.onload = () => {
    try {
      const c = document.createElement('canvas');
      c.width = c.height = 8;
      const ctx = c.getContext('2d');
      ctx.drawImage(img, 0, 0, 8, 8);
      const d = ctx.getImageData(0, 0, 8, 8).data;
      let r = 0, g = 0, b = 0;
      for (let k = 0; k < d.length; k += 4) { r += d[k]; g += d[k + 1]; b += d[k + 2]; }
      const n = d.length / 4;
      const tone = (f) => `rgb(${Math.round(r / n * f)}, ${Math.round(g / n * f)}, ${Math.round(b / n * f)})`;
      bg.style.setProperty('--np-bg', `linear-gradient(135deg, ${tone(0.62)}, ${tone(0.32)})`);
    } catch (e) { /* the default tint stays */ }
  };
  img.src = src;
}

// A line in the player too long for its box scrolls slowly to its end and
// back instead of being cut off with "…". The text is moved into an inner
// span (the delegated data-onclick links keep working), and a ResizeObserver
// measures again whenever the box changes size — including when the Now
// Playing view, filled while hidden, is opened.
const marqueeObserver = typeof ResizeObserver === 'function'
  ? new ResizeObserver(entries => entries.forEach(e => marqueeMeasure(e.target)))
  : null;

function marqueeFit(el) {
  if (!el) return;
  let inner = el.firstElementChild;
  if (!(inner && inner.classList.contains('marquee-inner') && el.childNodes.length === 1)) {
    inner = document.createElement('span');
    inner.className = 'marquee-inner';
    while (el.firstChild) inner.appendChild(el.firstChild);
    el.appendChild(inner);
  }
  if (marqueeObserver && !el.dataset.marquee) {
    el.dataset.marquee = '1';
    marqueeObserver.observe(el);
  }
  marqueeMeasure(el);
}

function marqueeMeasure(el) {
  const inner = el.firstElementChild;
  if (!inner || !inner.classList.contains('marquee-inner')) return;
  el.classList.remove('marquee');
  const overflow = el.scrollWidth - el.clientWidth;
  if (el.clientWidth > 0 && overflow > 2) {
    el.style.setProperty('--marquee-shift', `${-overflow}px`);
    // About 30px a second, never so short the text flickers past.
    el.style.setProperty('--marquee-dur', `${Math.max(6, overflow / 30 + 4).toFixed(1)}s`);
    el.classList.add('marquee');
  }
}

// The artist and album under the title, as links into the library: an
// artist with a page of their own opens it, anyone else (a featured artist
// is rarely an album artist) opens the songs that name them; the album
// opens its page.
function npArtistLinks(names, fallback) {
  const list = names && names.length ? names : artistNames(fallback);
  return list.map(name => `<button type="button" class="np-link" data-artist-name="${escHtml(name)}"
    data-onclick="event.stopPropagation(); npGoArtist(this.dataset.artistName)">${escHtml(name)}</button>`).join(', ');
}

function npFillCredits(item, res) {
  const libItem = lib.items.find(t => t.path === item.path);
  const names = artistNames(res.artist).length ? artistNames(res.artist) : (libItem?.artist_names || []);
  const album = (res.album || item.album || '').trim();
  NP.albumKey = libItem ? libAlbumKey(libItem) : '';
  NP.albumName = album;
  const parts = [];
  const artists = npArtistLinks(names, item.artist);
  if (artists) parts.push(artists);
  if (album) parts.push(`<button type="button" class="np-link" data-onclick="npGoAlbum()">${escHtml(album)}</button>`);
  // The separator is drawn by CSS: " · " in the console look, " — " in music.
  $('np-artist').innerHTML = parts.join('<span class="np-sep" aria-hidden="true"></span>');
  marqueeFit($('np-artist'));
}

function npShowInLibrary() {
  closeNowPlaying();
  switchView('player');
}

function npGoArtist(name) {
  const key = String(name || '').trim().toLowerCase();
  if (!key) return;
  npShowInLibrary();
  if (libAll().artists.some(a => a.key === key)) {
    $('player-search').value = '';
    openPlayerArtist(key);
  } else {
    $('player-search').value = name;
    setPlayerTab('songs');
  }
}

function npGoAlbum() {
  npShowInLibrary();
  if (NP.albumKey && libAll().albums.some(a => a.key === NP.albumKey)) {
    $('player-search').value = '';
    openPlayerAlbum(NP.albumKey);
  } else if (NP.albumName) {
    $('player-search').value = NP.albumName;
    setPlayerTab('albums');
  }
}

// Where the animated artwork is served from: the desktop window gets a
// media-server URL, the web UI streams the file through /api/media like the
// audio. None with Reduce Motion on, where the still cover stays.
function npMotionUrl(res) {
  if (!res?.motion_path && !res?.motion_url) return '';
  if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return '';
  return res.motion_url || ('/api/media?path=' + encodeURIComponent(res.motion_path));
}

// ── Canvas ──────────────────────────────────────────────────────────────────
// The short looping video Spotify shows with a track, saved beside the file
// by "Save Spotify Canvas", plays in the cover's square of the full-screen
// player, as in Spotify's Now Playing panel. On by
// default and switched off in Settings → Player → Canvas; the choice is per
// device, like the sidebar's.
const NP_CANVAS_KEY = 'mornye-np-canvas';
let npCanvasRes = null;
function canvasEnabled() {
  try { return localStorage.getItem(NP_CANVAS_KEY) !== 'off'; } catch (e) { return true; }
}
function setCanvasEnabled(on) {
  try { localStorage.setItem(NP_CANVAS_KEY, on ? 'on' : 'off'); } catch (e) {}
  npShowCanvas(npCanvasRes);
}
function npCanvasUrl(res) {
  if (!res?.canvas_path && !res?.canvas_url) return '';
  return res.canvas_url || ('/api/media?path=' + encodeURIComponent(res.canvas_path));
}
function npShowCanvas(res) {
  npCanvasRes = res;
  const video = $('np-canvas'), view = $('np-view');
  if (!video || !view) return;
  const url = canvasEnabled() ? npCanvasUrl(res) : '';
  if (!url) {
    view.classList.remove('has-canvas');
    video.pause();
    if (video.dataset.src) { delete video.dataset.src; video.removeAttribute('src'); video.load(); }
    return;
  }
  if (video.dataset.src !== url) {
    // The last track's frame fades out; this one fades in once it can show.
    view.classList.remove('has-canvas');
    video.onloadeddata = () => view.classList.add('has-canvas');
    video.onerror = () => view.classList.remove('has-canvas');
    video.dataset.src = url;
    video.src = url;
  } else if (video.readyState >= 2) {
    view.classList.add('has-canvas');
  }
  npCanvasSync();
}
// Plays only while the view is open and the music is playing.
function npCanvasSync() {
  const video = $('np-canvas');
  if (!video?.dataset.src) return;
  const open = !$('np-view').classList.contains('hidden');
  if (open && !playerEngine.paused) video.play().catch(() => {});
  else video.pause();
}

function fillNowPlaying(item, res) {
  setPlayerArtwork(res.cover || '');
  npResetSync();
  $('np-title').textContent = res.title || item.title;
  marqueeFit($('np-title'));
  npFillCredits(item, res);
  $('np-explicit').classList.toggle('hidden', !res.explicit);
  const cover = $('np-cover');
  // The album's animated artwork (cover.webp beside the track) plays in
  // place of the still cover; the colours still come from the still one.
  const motion = npMotionUrl(res);
  cover.parentElement.classList.toggle('empty', !res.cover && !motion);
  cover.parentElement.classList.toggle('animated', !!motion);
  cover.onerror = motion ? () => {
    cover.onerror = null;
    cover.parentElement.classList.remove('animated');
    cover.parentElement.classList.toggle('empty', !res.cover);
    cover.src = res.cover || '';
  } : null;
  cover.src = motion || res.cover || '';
  npApplyColors(res.cover);
  npShowCanvas(res);
  $('np-tech').textContent = npTechLabel(res);
  $('np-seek').value = 0;
  npPaintSeek();
  $('np-cur').textContent = '0:00';
  $('np-rem').textContent = '-0:00';
  const parsed = parseLyrics(res.lyrics);
  // Before the first line there is an intro: three dots stand in for it and
  // fill up until the words start, like in the mobile app.
  if (parsed.synced && parsed.lines.length && parsed.lines[0].t > 3) {
    parsed.lines.unshift({ t: 0, text: '', words: null, voice: 1, bg: '', tr: '', rom: '' });
  }
  npLines = parsed.lines;
  npSynced = parsed.synced;
  npWordSynced = parsed.wordSynced;
  npActive = -1;
  NP.set = new Set(); NP.focus = -2; NP.first = true; NP.lastTime = 0;
  npClearMotion();
  const box = $('np-lyrics');
  box.classList.toggle('plain', !npSynced);
  box.scrollTop = 0;
  if (!npLines.length) {
    box.style.paddingBottom = '';
    box.innerHTML = '<div class="np-empty">No lyrics for this track.<br><small>Save a .lrc next to the file, or add them with Edit.</small></div>';
  } else {
    // A line ends when its last word does, else when the next one starts.
    npLines.forEach((l, i) => {
      const next = npLines[i + 1];
      if (l.words) {
        // A last piece with no end of its own — one stamp for a whole line,
        // say — lasts about a third of a second a word, never past the
        // next line, before it is shared out word by word.
        const last = l.words[l.words.length - 1];
        if (last.open) {
          const count = (last.text.match(/\S+/g) || []).length;
          const until = next && next.t != null && next.t > last.s ? next.t : Infinity;
          last.e = Math.min(until, last.s + Math.max(0.6, 0.32 * count));
        }
        l.words = npSplitTimedPhrases(l.words);
      }
      l.end = l.words ? l.words[l.words.length - 1].e : (next && next.t != null ? next.t : (l.t ?? 0) + 4);
      if (l.words && next && next.t != null && l.end > next.t + 8) l.end = next.t;
      if (l.bgWords) {
        const last = l.bgWords[l.bgWords.length - 1];
        if (last.open) last.e = Math.max(last.s + 0.6, Math.min(l.end, last.s + 2));
        l.bgWords = npSplitTimedPhrases(l.bgWords);
        // The line stays lit while its backing vocals are still sung.
        l.end = Math.max(l.end, l.bgWords[l.bgWords.length - 1].e);
      }
    });
    if (npSynced) npLines = npAddRests(npLines);
    box.innerHTML = npLines.map((l, i) => {
      if (npSynced && !l.text) {
        return `<div class="np-line gap" data-i="${i}"><span class="gd"></span><span class="gd"></span><span class="gd"></span></div>`;
      }
      // Spaces stay outside a word: inside, they would be part of what the
      // lit copy draws and of the box the line wraps around.
      const wordSpans = words => words.map(w => {
        const lead = w.text.match(/^\s*/)[0];
        const core = w.text.trim();
        const tail = w.text.slice(lead.length + core.length);
        return `${escHtml(lead)}<span class="w" data-s="${w.s}" data-e="${w.e}" data-t="${escHtml(core)}">${escHtml(core)}</span>${escHtml(tail)}`;
      }).join('');
      const body = l.words ? wordSpans(l.words) : escHtml(l.text);
      const extra = (l.rom ? `<div class="np-sub">${escHtml(l.rom)}</div>` : '')
        + (l.tr ? `<div class="np-sub">${escHtml(l.tr)}</div>` : '')
        + (l.bgWords ? `<div class="np-sub bg">${wordSpans(l.bgWords)}</div>` : l.bg ? `<div class="np-sub bg">${escHtml(l.bg)}</div>` : '');
      return `<div class="np-line${l.words || l.bgWords ? ' wsync' : ''}${l.voice === 2 ? ' v2' : ''}" data-i="${i}">${body}${extra}</div>`;
    }).join('');
    box.querySelectorAll('.np-line').forEach(el => el.addEventListener('click', () => {
      const l = npLines[parseInt(el.dataset.i)];
      if (npSynced && l && l.t != null) { playerEngine.currentTime = npAudioTimeForLyrics(l.t); playerEngine.play(); }
    }));
    npLayoutLyrics();
  }
  if (!$('np-queue').classList.contains('hidden')) npRenderQueue();
  if (npSynced) npUpdate(npLyricsTime(playerEngine.currentTime), true);
}

// How long a pause between two lines has to be before the intro's three
// dots stand in for it, as Apple Music does for an instrumental break.
const NP_REST_MIN_S = 5;

// Synced lyrics with a blank row for every long pause between two sung lines.
// A word-synced line knows when its last word ends; a line-synced one only
// knows when it starts, so how long it is sung for is estimated from its
// word count (about 0.45 s a word plus a second, between 2.5 s and 8 s),
// and the line then ends there instead of lasting up to the next one.
// Blank rows already in the file are left as they are.
function npAddRests(lines) {
  const out = [];
  lines.forEach((l, i) => {
    out.push(l);
    const next = lines[i + 1];
    if (!l.text || !next || !next.text || l.t == null || next.t == null) return;
    let sungUntil = l.end;
    if (!l.words) {
      const count = (l.text.match(/\S+/g) || []).length;
      sungUntil = l.t + Math.min(8, Math.max(2.5, 0.45 * count + 1));
    }
    if (next.t - sungUntil < NP_REST_MIN_S) return;
    l.end = Math.min(l.end, sungUntil);
    out.push({ t: sungUntil, end: next.t, text: '', words: null, voice: 1, bg: '', tr: '', rom: '' });
  });
  return out;
}

function setPlayerArtwork(src) {
  const wrap = $('pl-artwork');
  const image = $('pl-cover');
  if (!wrap || !image) return;
  wrap.classList.remove('has-artwork');
  image.onload = () => wrap.classList.add('has-artwork');
  image.onerror = () => wrap.classList.remove('has-artwork');
  image.removeAttribute('src');
  if (src) image.src = src;
}

// ── Lyrics engine ───────────────────────────────────────────────────────────
// The view logic of the mobile app's lyrics screen, ported:
//  * the sung line sits near the TOP of the pane (6% of the height, 16–48 px),
//    not in the middle, so what comes next is what you see;
//  * moving to a new line is a critically damped spring (550 ms, no overshoot);
//    the rows after it follow a softer spring one after another (35 ms apart),
//    so the list ripples instead of sliding as a block;
//  * lines other than the sung one are defocused by distance (2.4 / 3.6 / 4.8);
//  * a line stays in focus while the previous one is still being sung (duets);
//  * words fill with a soft edge, rise a little, and a held word glows;
//  * a blank stretch shows three dots that fill with the gap;
//  * scrolling by hand pauses the follow for 4 s.
const NP = { set: new Set(), focus: -2, first: true, lastTime: 0, userScrolling: false, userTimer: 0,
  motion: new Map(), motionRaf: 0 };

// The mobile app's defocus (2.4 / 3.6 / 4.8) scaled down: the same values
// look much heavier at desktop size, so this keeps the depth without the smear.
const NP_BLUR = 0.5;
const NP_SCROLL_S = 0.55, NP_TOTAL_S = 0.8;
const npCrit = s => 1 - (1 + 14 * s) * Math.exp(-14 * s);              // stiffness 196, ratio 1
const npScrollCurve = u => npCrit(u * NP_SCROLL_S) / npCrit(NP_SCROLL_S);
const npSoft = s => {                                                  // stiffness 121, ratio .85
  if (s <= 0) return 0;
  const w = 11, z = 0.85, wd = w * Math.sqrt(1 - z * z);
  return 1 - Math.exp(-z * w * s) * (Math.cos(wd * s) + (z * w / wd) * Math.sin(wd * s));
};
const npEase = v => { const t = Math.min(1, Math.max(0, v)); return t * t * (3 - 2 * t); };

// Where the sung line sits, from the top of the pane. Never less than 34px:
// the pane's mask fades the first 22px out (player.css), and a smaller inset
// put the first and the sung line half inside that fade on short windows.
function npInset() { return Math.min(56, Math.max(34, window.innerHeight * 0.06)); }

function npLayoutLyrics() {
  const box = $('np-lyrics');
  const inset = npInset();
  box.style.paddingTop = inset + 'px';
  const last = box.lastElementChild;
  // Enough room under the last line for it to reach the same focus position.
  box.style.paddingBottom = Math.max(inset, box.clientHeight - (last ? last.offsetHeight : 0) - inset) + 'px';
}
window.addEventListener('resize', () => { if (!$('np-view').classList.contains('hidden')) npLayoutLyrics(); });

function npClearMotion() {
  cancelAnimationFrame(NP.motionRaf); NP.motionRaf = 0;
  NP.motion.forEach((m, el) => { el.style.transform = ''; });
  NP.motion.clear();
}

function npOffsetNow(m, now) {
  const elapsed = (now - m.t0) / 1000;
  if (m.kind === 'seek') {
    const u = Math.min(1, elapsed / 0.22);
    return (m.init + m.D) * Math.pow(1 - u, 3);                        // ease-out cubic
  }
  if (elapsed >= NP_TOTAL_S) return 0;
  const s = npScrollCurve(Math.min(1, elapsed / NP_SCROLL_S));
  const inherited = m.init * (1 - s);
  let off = inherited + m.D * (1 - s);
  if (m.k > 0) {
    const d = Math.max(-96, Math.min(96, m.D));
    const delay = Math.min(6, m.k) * 0.035;
    const f = elapsed <= delay ? 0 : npSoft(elapsed - delay);
    off = inherited + m.D * (1 - s) + d * (s - f);
  }
  return off;
}

function npMotionTick(now) {
  NP.motionRaf = 0;
  let live = false;
  NP.motion.forEach((m, el) => {
    const done = (now - m.t0) / 1000 >= (m.kind === 'seek' ? 0.22 : NP_TOTAL_S);
    if (done) { el.style.transform = ''; NP.motion.delete(el); return; }
    el.style.transform = `translateY(${npOffsetNow(m, now).toFixed(2)}px)`;
    live = true;
  });
  if (live) NP.motionRaf = requestAnimationFrame(npMotionTick);
}

function npScrollToFocus(focus, mode) {
  const box = $('np-lyrics');
  const el = box.children[focus];
  if (!el || !box.clientHeight) return;
  const target = Math.max(0, Math.min(el.offsetTop - npInset(), box.scrollHeight - box.clientHeight));
  const D = target - box.scrollTop;
  if (mode === 'instant' || Math.abs(D) < 0.5) { box.scrollTop = target; npClearMotion(); return; }
  const now = performance.now();
  const rows = [...box.children].slice(Math.max(0, focus - 6), focus + 24);
  rows.forEach((row, n) => {
    const idx = Math.max(0, focus - 6) + n;
    const prev = NP.motion.get(row);
    row.style.transform = '';
    NP.motion.set(row, { t0: now, D, k: idx - focus, init: prev ? npOffsetNow(prev, now) : 0, kind: mode });
  });
  box.scrollTop = target;
  rows.forEach(row => {
    const m = NP.motion.get(row);
    row.style.transform = `translateY(${npOffsetNow(m, now).toFixed(2)}px)`;
  });
  if (!NP.motionRaf) NP.motionRaf = requestAnimationFrame(npMotionTick);
}

// The line the view follows. A line still being sung keeps the focus only
// when another voice has started over it (a duet); for one singer, a held
// last word running into the next line used to keep the view a line behind.
// The line the view centres on: the earliest one still being sung. In
// word-synced lyrics the next line often starts before the last word of the
// current one ends; moving on at its start scrolled away from a line still
// being sung. That used to be held only for a duet's other voice. The newer
// line is lit meanwhile all the same (it is in `set`).
function npFocusFor(active, set, time) {
  let focus = active;
  for (let i = active - 1; i >= 0; i--) {
    if (!set.has(i)) continue;
    const line = npLines[i];
    if (!line.text) break;
    if (line.end > time && line.t < npLines[active].t) focus = i;
  }
  return focus;
}

// Lift and glow envelopes of a timed word, as in the mobile app.
function npLift(elapsed, duration) {
  const normal = npEase((elapsed + 180) / 720);
  const held = Math.min(1, Math.max(0, (duration - 1000) / 1500));
  if (held === 0) return normal;
  const rise = npEase((elapsed - 400) / Math.min(1200, (duration - 400) * 0.6));
  const settle = npEase((duration - elapsed) / 600);
  return normal + 1.3 * held * rise * settle;
}
function npGlow(elapsed, duration) {
  const strength = Math.min(1, Math.max(0, (duration - 900) / 1100));
  if (strength === 0) return 0;
  return strength * npEase((elapsed - 250) / Math.min(900, duration * 0.5)) * npEase((duration + 500 - elapsed) / 500);
}

function npUpdate(time, force) {
  if (!npSynced || !npLines.length) return;
  const box = $('np-lyrics');
  const els = box.children;
  if (els.length !== npLines.length) return;

  let idx = -1;
  for (let i = 0; i < npLines.length; i++) { if (npLines[i].t <= time) idx = i; else break; }
  const set = new Set();
  if (idx >= 0) {
    set.add(idx);
    for (let i = idx - 1; i >= 0 && i > idx - 4; i--) if (npLines[i].text && npLines[i].end > time) set.add(i);
  }
  const focus = idx < 0 ? -1 : npFocusFor(idx, set, time);
  const sig = [...set].join(',');
  if (force || focus !== NP.focus || sig !== NP.sig) {
    const leaving = [...(NP.set || [])].filter(i => !set.has(i));
    NP.set = set; NP.sig = sig;
    for (let i = 0; i < els.length; i++) {
      const active = set.has(i);
      els[i].classList.toggle('active', active);
      const dist = focus < 0 ? 9 : Math.abs(i - focus);
      const blur = active || i === focus ? 0 : NP_BLUR * (focus < 0 ? 4.8 : dist === 1 ? 2.4 : dist === 2 ? 3.6 : 4.8);
      els[i].style.setProperty('--b', blur.toFixed(2) + 'px');
    }
    leaving.forEach(i => els[i] && els[i].querySelectorAll('.w').forEach(w => {
      w.style.removeProperty('--p'); w.style.removeProperty('--l'); w.style.removeProperty('--g');
    }));
    leaving.forEach(i => els[i] && els[i].querySelectorAll('.gd').forEach(d => { d.style.opacity = ''; d.style.transform = ''; }));
    if (focus !== NP.focus && focus >= 0 && !NP.userScrolling) {
      const jumped = Math.abs(time - NP.lastTime) > 0.75;
      npScrollToFocus(focus, NP.first ? 'instant' : (jumped ? 'seek' : 'spring'));
      NP.first = false;
    } else if (focus < 0 && NP.focus >= 0 && !NP.userScrolling) {
      // Back before the first line (a seek to the start, or a new track
      // whose first line comes within its first seconds): the view goes
      // back to the top instead of staying where the last line left it.
      npClearMotion();
      box.scrollTop = 0;
    }
    NP.focus = focus;
  }
  NP.lastTime = time;

  // Per-word fill, lift and glow, and the dots of a blank stretch.
  set.forEach(i => {
    const l = npLines[i], el = els[i];
    if (!l.text) {
      const span = Math.max(l.end - l.t, 0.001);
      const p = Math.min(1, Math.max(0, (time - l.t) / span));
      const breath = Math.sin(Math.PI * ((performance.now() % 3600) / 3600));
      [...el.children].forEach((dot, n) => {
        dot.style.opacity = (0.25 + 0.75 * Math.min(1, Math.max(0, p * 3 - n))).toFixed(3);
        dot.style.transform = `scale(${(1 + 0.24 * breath * breath).toFixed(3)})`;
      });
      return;
    }
    if (!l.words && !l.bgWords) return;
    el.querySelectorAll('.w').forEach(w => {
      const s = Number(w.dataset.s), e = Number(w.dataset.e);
      const dur = Math.max((e - s) * 1000, 1), elapsed = (time - s) * 1000;
      const p = time <= s ? 0 : (time >= e ? 1 : (time - s) / Math.max(e - s, 0.05));
      const lift = npLift(elapsed, dur), glow = npGlow(elapsed, dur);
      w.style.setProperty('--p', p.toFixed(3));
      w.style.setProperty('--l', (lift * 1.7).toFixed(2));
      w.style.setProperty('--g', glow.toFixed(2));
    });
  });
}

(function initLyricScrollGuard() {
  const box = $('np-lyrics');
  if (!box) return;
  const hold = () => {
    NP.userScrolling = true;
    npClearMotion();
    clearTimeout(NP.userTimer);
    NP.userTimer = setTimeout(() => {
      NP.userScrolling = false;
      NP.focus = -2;                       // follow again, with the spring
      npUpdate(npLyricsTime(playerEngine.currentTime), true);
    }, 4000);
  };
  box.addEventListener('wheel', hold, { passive: true });
  box.addEventListener('touchmove', hold, { passive: true });
})();

// Once per frame while playing.
let npRaf = 0;
function npTick() {
  npRaf = 0;
  const audio = playerEngine;
  if (audio.paused || $('np-view').classList.contains('hidden')) return;
  npUpdate(npLyricsTime(audio.currentTime));
  npRaf = requestAnimationFrame(npTick);
}
function npStartTick() { if (!npRaf) npRaf = requestAnimationFrame(npTick); }
function npSyncLyrics(time) { npUpdate(npLyricsTime(time)); }

function npRenderQueue() {
  const rows = playerQueue.length ? [`<div class="np-queue-head"><span>Up Next</span>
    <button type="button" class="np-queue-save" id="np-queue-save" data-onclick="npSaveQueueAsPlaylist(this)" aria-haspopup="menu" title="Save the whole queue, in its order, to a playlist">Save as Playlist</button></div>`] : [];
  for (let n = Math.max(0, playerPos); n < Math.min(playerOrder.length, Math.max(0, playerPos) + 200); n++) {
    const i = playerOrder[n], q = playerQueue[i];
    if (!q) continue;
    rows.push(`<div class="np-queue-row${n === playerPos ? ' active' : ''}" role="button" tabindex="0" data-activate data-onclick="loadPlayerTrack(${i})">
       <span class="t">${escHtml(q.title)}</span><span class="a">${npArtistLinks(lib.items.find(t => t.path === q.path)?.artist_names, q.artist)}</span></div>`);
  }
  $('np-queue').innerHTML = rows.join('');
}

// ── Player: browse and play a folder ────────────────────────────────────────
const lib = { items: [], view: [], scan: null, lastScan: 0, shown: 0, loading: false, scanning: false, scanSamples: [], lastLink: 0, started: false, raf: 0, thumbBusy: 0,
  tab: 'songs', album: null, artist: null, playlist: null, playlists: [], folder: '', cards: 0,
  cardHtml: [], cardsShown: 0 };
const LIB_PAGE = 150;
const LIB_FOLDERS_KEY = 'mornye-player-folders';
//: The folders the library is made of, scanned together into one library.
const LIB_LIBRARY_KEY = 'mornye-player-library';

function libReadList(key) {
  try { return JSON.parse(localStorage.getItem(key) || '[]').filter(x => typeof x === 'string' && x.trim()); }
  catch (e) { return []; }
}
function libWriteList(key, list) {
  try { localStorage.setItem(key, JSON.stringify(list)); } catch (e) {}
}
function libRecent() { return libReadList(LIB_FOLDERS_KEY); }
function libFolders() { return libReadList(LIB_LIBRARY_KEY); }
function libFolderName(f) {
  const m = /^(?:smb|cifs|afp|nfs):\/\/([^/]+)\/?(.*)$/i.exec(f);
  if (m) return (m[2].split('/').filter(Boolean).pop() || m[1]);
  return f.split(/[\\/]/).filter(Boolean).pop() || f;
}
function libIsNetwork(f) { return /^(?:smb|cifs|afp|nfs):\/\//i.test(f) || /^\\\\|^\/\//.test(f) || /^\/Volumes\//.test(f); }

function libRemember(folder) {
  const list = [folder, ...libRecent().filter(f => f !== folder)].slice(0, 6);
  libWriteList(LIB_FOLDERS_KEY, list);
  renderRecentFolders();
}
function setPlayerFolders(list) {
  libWriteList(LIB_LIBRARY_KEY, [...new Set(list)]);
  renderPlayerFolders();
}

// The folders in the library, each removable. An empty list says what to do.
function renderPlayerFolders() {
  const box = $('player-folder-list');
  if (!box) return;
  const list = libFolders();
  box.innerHTML = list.length ? list.map(f => {
    const net = libIsNetwork(f);
    const icon = net
      ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2" y="3" width="20" height="8" rx="2"/><rect x="2" y="13" width="20" height="8" rx="2"/><line x1="6" y1="7" x2="6.01" y2="7"/><line x1="6" y1="17" x2="6.01" y2="17"/></svg>'
      : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 7h6l2 2h10v10H3z"/></svg>';
    return `<li class="psrc-item ${net ? 'is-network' : ''}" data-path="${escHtml(f)}">
      <span class="psrc-icon">${icon}</span>
      <span class="psrc-text"><strong>${escHtml(libFolderName(f))}</strong><span>${escHtml(f)}</span></span>
      <button type="button" class="psrc-remove" title="Remove from the library" aria-label="Remove ${escHtml(libFolderName(f))} from the library">✕</button>
    </li>`;
  }).join('') : '<li class="psrc-empty">No folders yet — add one below.</li>';
  box.querySelectorAll('.psrc-remove').forEach(btn => btn.addEventListener('click', () => {
    const path = btn.closest('.psrc-item').dataset.path;
    setPlayerFolders(libFolders().filter(f => f !== path));
  }));
}

function renderRecentFolders() {
  const box = $('player-recent');
  const inLibrary = new Set(libFolders());
  const list = libRecent().filter(f => !inLibrary.has(f));
  box.classList.toggle('hidden', list.length === 0);
  box.innerHTML = list.length ? '<span class="share-chips-label">Recent:</span>' + list.map(f =>
    `<button type="button" class="share-chip" data-path="${escHtml(f)}" title="Add ${escHtml(f)}">${escHtml(libFolderName(f))}</button>`).join('') : '';
  box.querySelectorAll('.share-chip').forEach(b => b.addEventListener('click', () => {
    addPlayerFolder(b.dataset.path);
  }));
}

// Mounted network shares, one click to add to the library.
async function renderPlayerNetworkShares() {
  const box = $('player-network');
  if (!box || !window.pywebview?.api?.get_network_shares) return;
  try {
    const res = await window.pywebview.api.get_network_shares();
    const shares = res?.shares || [];
    box.classList.toggle('hidden', shares.length === 0);
    box.innerHTML = shares.length
      ? '<span class="share-chips-label">Network:</span>' + shares.map(s =>
          `<button type="button" class="share-chip" title="${escHtml(s.path)}" data-path="${escHtml(s.path)}">`
          + '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2" y="3" width="20" height="8" rx="2"/><rect x="2" y="13" width="20" height="8" rx="2"/><line x1="6" y1="7" x2="6.01" y2="7"/><line x1="6" y1="17" x2="6.01" y2="17"/></svg>'
          + `${escHtml(s.label)}</button>`).join('')
      : '';
    box.querySelectorAll('.share-chip').forEach(btn => btn.addEventListener('click', () => addPlayerFolder(btn.dataset.path)));
  } catch (e) { /* a share can still be typed */ }
}

function addPlayerFolder(path) {
  path = String(path || '').trim();
  if (!path) return false;
  if (!libFolders().includes(path)) setPlayerFolders([...libFolders(), path]);
  renderRecentFolders();
  return true;
}
function addPlayerFolderFromInput() {
  const input = $('player-path-input');
  if (!addPlayerFolder(input.value)) { toastMgr.error('Type a folder path or pick one first.'); return; }
  input.value = '';
  scanPlayerLibrary();
}

async function initPlayerView() {
  if (lib.started) { markPlayingRow(); return; }
  lib.started = true;
  await refreshPlayerPlaylists();
  startPlaylistSync();
  // A library saved before several folders were possible is its last folder.
  if (!libFolders().length) {
    let folder = libRecent()[0];
    if (!folder) {
      try { folder = (await window.pywebview?.api?.get_library_default_folder?.())?.path; } catch (e) {}
    }
    if (folder) libWriteList(LIB_LIBRARY_KEY, [folder]);
  }
  renderPlayerFolders();
  renderRecentFolders();
  renderPlayerNetworkShares();
  if (libFolders().length) scanPlayerLibrary();
}

function openPlayerFolderBrowser() { openFolderBrowser('player'); }

// Scans every folder of the library into one list. A path left in the field
// (typed, or chosen with the picker) joins the library first.
async function scanPlayerLibrary() {
  const typed = $('player-path-input').value.trim();
  if (typed) { addPlayerFolder(typed); $('player-path-input').value = ''; }
  const folders = libFolders();
  if (!folders.length) { toastMgr.error('Add a music folder first.'); showPlayerSource(true); return; }
  lib.items = []; lib.view = []; lib.shown = 0; lib.scan = null; lib.loading = true;
  lib.album = null; lib.artist = null;
  $('player-list').innerHTML = '';
  $('player-grid').innerHTML = '';
  $('player-toolbar').classList.add('hidden');
  setPlayerStatus('Scanning…');
  renderPlayerEmptyState();
  // The library bar carries the scan's progress, from the first scan on.
  $('plb-name').textContent = libFoldersLabel(folders);
  $('plb-name').title = folders.join('\n');
  $('plb-stats').textContent = '';
  setPlayerScanBusy(true);
  const res = await window.pywebview?.api?.scan_music_library?.(folders);
  if (!res || res.status !== 'started') {
    lib.loading = false;
    setPlayerScanBusy(false);
    setPlayerStatus(res?.error || 'Could not open that folder.');
    showPlayerSource(true);
    renderPlayerEmptyState();
    return;
  }
  lib.lastScan = Math.max(lib.lastScan, res.scan);
  if (lib.scan === null) lib.scan = res.scan;
}

function setPlayerScanBusy(busy) {
  $('player-scan-btn').disabled = busy;
  const rescan = $('plb-rescan');
  if (rescan) { rescan.disabled = busy; rescan.textContent = busy ? 'Scanning…' : 'Rescan'; }
  if (busy && !lib.scanning) lib.lastLink = Date.now();  // the first link waits for some rows
  lib.scanning = !!busy;
  setPlayerScanProgress(busy ? { phase: 'listing', done: 0, total: 0 } : null);
}

// What the scan bar shows for an app_library_scan_progress payload: a
// percentage once the total is known, null (a sliding bar) while the
// folders are still being listed — on a network share that can take long.
// `eta` (seconds left, from libScanRemaining) is added when known.
function libScanProgress(p, eta) {
  const done = Math.max(0, Number(p?.done) || 0);
  const total = Math.max(0, Number(p?.total) || 0);
  if (p?.phase !== 'reading' || !total) {
    return { pct: null, text: done ? `Looking for music… ${done.toLocaleString()} files found` : 'Looking for music…' };
  }
  const shown = Math.min(done, total);
  const left = shown < total ? libScanEtaText(eta) : '';
  return {
    pct: Math.round((shown / total) * 100),
    text: `${shown.toLocaleString()} / ${total.toLocaleString()} files scanned${left ? ` · ${left}` : ''}`,
  };
}

// Seconds left in the reading phase, from the pace of the last ~30 s
// (samples are {t: ms, done}); null until the pace is known. Files already
// stored are skipped in a blink while new ones are read over the network,
// so a recent window follows the real pace better than the average since
// the start.
function libScanRemaining(samples, done, total) {
  if (!samples?.length || !(total > done)) return null;
  const last = samples[samples.length - 1];
  const from = samples.find(s => last.t - s.t <= 30000) || samples[0];
  const secs = (last.t - from.t) / 1000;
  const files = last.done - from.done;
  if (secs < 3 || files <= 0) return null;
  return (total - done) / (files / secs);
}

function libScanEtaText(seconds) {
  const s = Math.round(Number(seconds) || 0);
  if (!(s > 0)) return '';
  if (s < 60) return 'less than a minute left';
  const m = Math.round(s / 60);
  if (m < 60) return `~${m} min left`;
  return `~${Math.floor(m / 60)} h ${String(m % 60).padStart(2, '0')} min left`;
}

// The bar under the library summary; null hides it.
function setPlayerScanProgress(p) {
  if (!$('plb-scan')) return;
  paintScanBar('plb-scan', p, lib);
  syncPlayerLibbar();
}

// Paints a scan bar (#<id>, #<id>-track, #<id>-text, styled in
// feedback.css) for a scan progress payload; null hides it. `state` keeps
// the pace samples the time left is worked out from.
function paintScanBar(id, p, state) {
  const box = $(id);
  if (!box) return;
  box.classList.toggle('hidden', !p);
  if (!p) { state.scanSamples = []; return; }
  let eta = null;
  if (p.phase === 'reading' && p.total) {
    const samples = (state.scanSamples ||= []);
    samples.push({ t: Date.now(), done: Number(p.done) || 0 });
    while (samples.length > 2 && samples[samples.length - 1].t - samples[1].t > 30000) samples.shift();
    eta = libScanRemaining(samples, Number(p.done) || 0, Number(p.total));
  } else {
    state.scanSamples = [];
  }
  const { pct, text } = libScanProgress(p, eta);
  const track = $(`${id}-track`);
  box.classList.toggle('indeterminate', pct === null);
  box.style.setProperty('--scan-pct', `${pct || 0}%`);
  if (pct === null) track.removeAttribute('aria-valuenow');
  else track.setAttribute('aria-valuenow', String(pct));
  $(`${id}-text`).textContent = text;
}

// One folder by its name; several as "Music, NAS +1".
function libFoldersLabel(folders) {
  const names = folders.map(libFolderName);
  return !names.length ? 'Library'
    : names.length <= 2 ? names.join(', ')
    : `${names.slice(0, 2).join(', ')} +${names.length - 2}`;
}

// The library bar shows once there is a library, and during any scan (its
// progress bar lives there), even the first one.
function syncPlayerLibbar() {
  $('player-libbar')?.classList.toggle('hidden', !lib.scanning && (lib.items.length === 0 || lib.loading));
}

function setPlayerStatus(text) {
  const el = $('player-status');
  if (el) el.textContent = text || '';
}

// The folder panel is what you need before a library is loaded; after that
// it is one line (folder, counts, Change / Rescan) and opens on demand.
function showPlayerSource(show) {
  $('player-source')?.classList.toggle('hidden', !show);
}
function togglePlayerSource() {
  const src = $('player-source');
  if (!src) return;
  const opening = src.classList.contains('hidden');
  showPlayerSource(opening);
  if (opening) {
    renderPlayerFolders();
    renderRecentFolders();
    renderPlayerNetworkShares();  // a share mounted since the page opened
    $('player-path-input')?.focus();
  }
}

// An artist tag as the player shows it. Files tagged with an artist
// separator read "Doja Cat;SZA" (";" for Navidrome, "/" from ID3v2.3, NUL
// between multi-value frames); the player shows "Doja Cat, SZA". The tag on
// disk is left alone. A slash splits only a credit that has spaces in it,
// so "AC/DC" stays one name.
function artistNames(value) {
  return String(value || '')
    .split(/\s*[;\u0000]\s*/)
    .flatMap(part => (/\s/.test(part) ? part.split(/\s*\/\s*/) : [part]))
    .map(part => part.trim())
    .filter(Boolean);
}
function displayArtists(value) { return artistNames(value).join(', '); }

function libAccept(p) {
  if (lib.scan === null) { if (p.scan <= lib.lastScan) return false; lib.scan = p.scan; }
  return p.scan === lib.scan;
}

// A row from the server as the library keeps it.
function libItem(t) {
  return {
    ...t,
    // The names one by one, for the now-playing links: once joined with
    // commas they cannot be told apart from "Tyler, The Creator".
    artist_names: artistNames(t.artist),
    album_artist_names: artistNames(t.album_artist),
    artist: displayArtists(t.artist),
    album_artist: displayArtists(t.album_artist),
  };
}

// While a long scan reads files (a network share can take an hour), the
// songs of the playlists are linked to what has been read so far, every
// LIB_LINK_EVERY ms, not only once the scan is over.
const LIB_LINK_EVERY = 20000;
function libLinkDue(last, now) {
  return !(now - (last || 0) < LIB_LINK_EVERY);
}
function linkPlaylistsDuringScan() {
  const now = Date.now();
  if (!lib.scanning || !libLinkDue(lib.lastLink, now)) return;
  lib.lastLink = now;
  linkPlaylistTracksToLibrary();
}

window.app_library_scan_chunk = function(p) {
  if (!libAccept(p)) return;
  lib.items.push(...p.items.map(libItem));
  setPlayerStatus(`Scanning… ${p.done} / ${p.total}`);
  if (!lib.raf) lib.raf = requestAnimationFrame(() => { lib.raf = 0; filterPlayerLibrary(true); });
  linkPlaylistsDuringScan();
};

// Stops the scan in progress (a slow network share can take very long):
// what was read so far stays in the library, and Rescan picks up from there.
async function cancelPlayerScan() {
  const stop = $('plb-scan-stop');
  if (stop) stop.disabled = true;
  const res = await window.pywebview?.api?.cancel_music_library_scan?.();
  if (stop) stop.disabled = false;
  if (!res || res.status !== 'ok') toastMgr.error('Could not stop the scan.');
}

window.app_library_scan_cancelled = function(p) {
  if (lib.scan === null || p?.scan !== lib.scan) return;
  lib.loading = false;
  lib.partial = true;  // files not read yet are not missing ones
  setPlayerScanBusy(false);
  setPlayerStatus(lib.items.length ? '' : 'Scan stopped.');
  if (lib.items.length) {
    lib.folder = libFolders()[0] || '';
    lib.folders = libFolders();
    showPlayerSource(false);
  } else {
    showPlayerSource(true);
  }
  filterPlayerLibrary(true);
  renderPlayerEmptyState();
  linkPlaylistTracksToLibrary();  // what was read before the stop counts too
};

window.app_library_scan_progress = function(p) {
  if (!libAccept(p) || !lib.scanning) return;
  setPlayerScanProgress(p);
};

window.app_library_scan_finished = function(p) {
  if (!libAccept(p)) return;
  lib.loading = false;
  lib.partial = false;
  // A stored library is usable at once; the check for changes on disk
  // goes on behind it until app_library_scan_synced.
  setPlayerScanBusy(!!p.stored);
  setPlayerStatus('');
  if (lib.items.length) {
    libFolders().forEach(libRemember);
    lib.folder = p.folder || libFolders()[0] || '';
    lib.folders = libFolders();
    showPlayerSource(false);
  } else {
    showPlayerSource(true);
  }
  filterPlayerLibrary(true);
  linkPlaylistTracksToLibrary();
};

// After a library stored from an earlier visit is shown, the server walks
// the folders and sends only what changed: rows of new or edited files
// (replacing their old row) and the paths of files that are gone.
window.app_library_scan_delta = function(p) {
  if (!libAccept(p)) return;
  const removed = new Set(p.removed || []);
  const fresh = new Map((p.items || []).map(t => [t.path, libItem(t)]));
  if (!removed.size && !fresh.size) return;
  lib.items = lib.items.filter(t => !removed.has(t.path) && !fresh.has(t.path));
  lib.items.push(...fresh.values());
  if (!lib.raf) lib.raf = requestAnimationFrame(() => { lib.raf = 0; filterPlayerLibrary(true); });
  linkPlaylistsDuringScan();
};

window.app_library_scan_synced = function(p) {
  if (!libAccept(p)) return;
  setPlayerScanBusy(false);
  setPlayerStatus('');
  filterPlayerLibrary(true);  // counts that wait for the scan to end
  linkPlaylistTracksToLibrary();
};

window.app_library_scan_error = function(p) {
  if (p && p.scan != null && !libAccept(p)) return;
  lib.loading = false;
  setPlayerScanBusy(false);
  setPlayerStatus('Scan failed: ' + (p?.error || 'unknown error'));
  showPlayerSource(true);
  renderPlayerEmptyState();
};

function libSortKey(key) {
  const text = v => (v || '').toString().toLowerCase();
  switch (key) {
    case 'title': return (a, b) => text(a.title).localeCompare(text(b.title));
    case 'artist': return (a, b) => text(a.artist).localeCompare(text(b.artist)) || text(a.album).localeCompare(text(b.album)) || a.disc - b.disc || a.track - b.track;
    case 'album': return (a, b) => text(a.album).localeCompare(text(b.album)) || a.disc - b.disc || a.track - b.track;
    case 'duration': return (a, b) => b.duration_s - a.duration_s;
    default: return null;
  }
}

// ── Library grouping: albums and artists ────────────────────────────────────
// An album is (album artist, album title): two "Greatest Hits" by different
// artists stay apart, and a compilation tagged with one ALBUMARTIST stays one
// album however many track artists it credits.
const libTrackOrder = (a, b) => (a.disc || 0) - (b.disc || 0) || (a.track || 0) - (b.track || 0)
  || String(a.title || '').localeCompare(String(b.title || ''));
function libArtistOf(t) { return (t.album_artist || t.artist || '').trim() || 'Unknown artist'; }
// The key travels through a data- attribute, so its separator must survive
// HTML parsing (a NUL comes back as U+FFFD and the album is never found).
function libAlbumKey(t) { return `${libArtistOf(t).toLowerCase()} /// ${(t.album || '').trim().toLowerCase()}`; }

function libAlbums(items) {
  const map = new Map();
  for (const t of items) {
    const key = libAlbumKey(t);
    let a = map.get(key);
    if (!a) {
      a = { key, title: (t.album || '').trim() || 'Unknown album', artist: libArtistOf(t), year: '', tracks: [], duration: 0, formats: new Set() };
      map.set(key, a);
    }
    a.tracks.push(t);
    a.duration += t.duration_s || 0;
    if ((t.added || 0) > (a.added || 0)) a.added = t.added;
    if (!a.year && t.year) a.year = t.year;
    if (t.format) a.formats.add(t.format);
  }
  const albums = [...map.values()];
  albums.forEach(a => a.tracks.sort(libTrackOrder));
  return albums.sort((x, y) => x.artist.localeCompare(y.artist, undefined, { sensitivity: 'base' })
    || (x.year || '').localeCompare(y.year || '') || x.title.localeCompare(y.title, undefined, { sensitivity: 'base' }));
}

// Every artist a track credits, one by one, as Jellyfin lists them: the
// album artists first, then the track's own artists ("Doja Cat", "SZA").
// A shared credit ("50 Cent;Olivia") gives each of them a page, and the
// album shows on both; the album itself stays one album.
function libCredited(t) {
  const seen = new Set();
  const out = [];
  for (const name of [...(t.album_artist_names || []), ...(t.artist_names || [])]) {
    const key = name.toLowerCase();
    if (!seen.has(key)) { seen.add(key); out.push(name); }
  }
  if (!out.length) out.push(libArtistOf(t));
  return out;
}
function libCredits(t, key) { return libCredited(t).some(n => n.toLowerCase() === key); }
// The album's own artists, lower-cased (not the guests on it).
function libAlbumOwners(album) {
  const t = album.tracks[0] || {};
  const names = t.album_artist_names && t.album_artist_names.length ? t.album_artist_names : [libArtistOf(t)];
  return names.map(n => n.toLowerCase());
}
// Whether `key` is one of the album's own artists, rather than a guest on it.
function libAlbumIsBy(album, key) {
  return libAlbumOwners(album).includes(key);
}

// The whole library grouped into albums and artists, kept until the list of
// tracks changes. Every tab switch used to group 40,000 tracks again (twice)
// before showing anything. lib.items only grows by push or is replaced,
// never edited in place, so the same array at the same length is the same
// library.
const libCache = { items: null, length: -1, albums: [], artists: [], covers: null };
function libAll() {
  if (libCache.items !== lib.items || libCache.length !== lib.items.length) {
    libCache.items = lib.items;
    libCache.length = lib.items.length;
    libCache.albums = libAlbums(lib.items);
    libCache.artists = libArtists(lib.items);
    libCache.covers = null;
  }
  return libCache;
}
// The albums of `items`: the cached ones for the whole library.
function libAlbumsFor(items) { return items === lib.items ? libAll().albums : libAlbums(items); }
function libArtistsFor(items) { return items === lib.items ? libAll().artists : libArtists(items); }
// Each artist's first own album, in album order: the cover of their card.
// One pass, where each card used to search every album.
function libArtistCover(key) {
  const c = libAll();
  if (!c.covers) {
    c.covers = new Map();
    for (const al of c.albums) {
      for (const k of libAlbumOwners(al)) if (!c.covers.has(k)) c.covers.set(k, al);
    }
  }
  return c.covers.get(key);
}
function libAlbumsOf(allAlbums, key) {
  const own = [], guest = [];
  for (const a of allAlbums) {
    if (libAlbumIsBy(a, key)) own.push(a);
    else if (a.tracks.some(t => libCredits(t, key))) guest.push(a);
  }
  return { own, guest };
}

function libArtists(items) {
  const map = new Map();
  for (const t of items) {
    for (const name of libCredited(t)) {
      const key = name.toLowerCase();
      let a = map.get(key);
      if (!a) { a = { key, name, albums: new Set(), tracks: [], duration: 0 }; map.set(key, a); }
      a.tracks.push(t);
      a.albums.add(libAlbumKey(t));
      a.duration += t.duration_s || 0;
    }
  }
  return [...map.values()].sort((x, y) => x.name.localeCompare(y.name, undefined, { sensitivity: 'base' }));
}

function libDuration(seconds) {
  const s = Math.round(seconds || 0);
  const h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60);
  if (!s) return '';
  if (h) return `${h} h ${m} min`;
  return m ? `${m} min` : `${s} s`;
}
const plural = (n, word) => `${n.toLocaleString()} ${word}${n === 1 ? '' : 's'}`;

function setPlayerTab(tab) {
  if (lib.playlist) $('player-search').value = '';
  lib.tab = tab;
  lib.album = null;
  lib.artist = null;
  lib.playlist = null;
  document.querySelectorAll('.player-tabs .seg-tab').forEach(b => {
    const on = b.dataset.tab === tab;
    b.classList.toggle('active', on);
    b.setAttribute('aria-selected', on ? 'true' : 'false');
  });
  filterPlayerLibrary();
  playerScrollTop();
}

// ── Library in the sidebar ───────────────────────────────────────────────────
// Each page of the library (Recently Added, Artists, Albums, Songs) and each
// playlist is a row of its own in the sidebar, the way a music app lays it
// out, instead of tabs at the top of one Player page. The phone bar keeps a
// single Player button and those tabs.
const LIB_TITLES = { recent: 'Recently Added', artists: 'Artists', albums: 'Albums', songs: 'Songs', playlists: 'Playlists' };
const LIB_RECENT_MAX = 100;  // albums on Recently Added

// Switches to the Player first; a page with unsaved changes may keep it.
function enterLibrary() {
  switchView('player');
  return !!$('view-player')?.classList.contains('active');
}
function openLibrary(tab) {
  if (enterLibrary()) setPlayerTab(tab);
}
function openLibraryPlaylist(id) {
  if (enterLibrary()) openPlayerPlaylist(id);
}
function newLibraryPlaylist() {
  if (!enterLibrary()) return;
  setPlayerTab('playlists');
  openPlayerPlaylistEditor();
}

// Highlights the row of the page the Player shows, and names the page.
function syncLibraryNav() {
  const on = !!$('view-player')?.classList.contains('active');
  const tab = lib.tab || 'songs';
  const inPlaylist = tab === 'playlists' && !!lib.playlist;
  document.querySelectorAll('#sidebar .nav-lib').forEach(b => {
    b.classList.toggle('active', on && !inPlaylist && b.id === 'nav-lib-' + tab);
  });
  document.querySelectorAll('#sb-playlist-list .nav-btn').forEach(b => {
    const current = on && inPlaylist && b.dataset.id === lib.playlist;
    b.classList.toggle('active', current);
    if (current) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current');
  });
  const title = $('player-title');
  if (title) title.textContent = LIB_TITLES[tab] || 'Player';
}

function renderSidebarPlaylists() {
  const box = $('sb-playlist-list');
  if (!box) return;
  const icon = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15V6"/><path d="M18.5 18a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5Z"/><path d="M12 12H3"/><path d="M16 6H3"/><path d="M12 18H3"/></svg>';
  box.innerHTML = lib.playlists.map(p =>
    `<button type="button" class="nav-btn sb-pl-item" role="listitem" data-id="${escHtml(p.id)}" title="${escHtml(p.name)}"`
    + ` data-onclick="openLibraryPlaylist(this.dataset.id)">${icon}<span class="nav-label">${escHtml(p.name)}</span></button>`).join('');
  syncLibraryNav();
}

// The sidebar lists the playlists from the start, before the Player has
// been opened (which loads them again, with the library).
async function loadSidebarPlaylists() {
  try {
    const res = await window.pywebview?.api?.get_player_playlists?.();
    if (res?.status !== 'ok' || lib.started) return;
    lib.playlists = res.playlists || [];
    renderSidebarPlaylists();
  } catch (e) { /* the rows come with the Player */ }
}

// The music scrolls in its own box (the whole page on a phone): a new page
// of the library starts at its top in both.
function playerScrollTop() {
  $('player-scroll')?.scrollTo?.({ top: 0 });
  $('player-body')?.scrollTo?.({ top: 0 });
}

function openPlayerAlbum(key) {
  // Back from an album opened on Recently Added goes back there.
  lib.tab = lib.tab === 'recent' ? 'recent' : 'albums';
  lib.album = key;
  lib.playlist = null;
  document.querySelectorAll('.player-tabs .seg-tab').forEach(b => {
    b.classList.toggle('active', b.dataset.tab === 'albums');
    b.setAttribute('aria-selected', b.dataset.tab === 'albums' ? 'true' : 'false');
  });
  filterPlayerLibrary();
  playerScrollTop();
}

function openPlayerArtist(key) {
  lib.tab = 'artists';
  lib.album = null;
  lib.artist = key;
  lib.playlist = null;
  document.querySelectorAll('.player-tabs .seg-tab').forEach(b => {
    b.classList.toggle('active', b.dataset.tab === 'artists');
    b.setAttribute('aria-selected', b.dataset.tab === 'artists' ? 'true' : 'false');
  });
  filterPlayerLibrary();
  playerScrollTop();
}

// Back from an album page goes to the artist it was opened from, if any.
function playerBack() {
  if (lib.playlist) { lib.playlist = null; $('player-search').value = ''; }
  else if (lib.album && lib.artist) { lib.album = null; lib.tab = 'artists'; }
  else if (lib.album) lib.album = null;
  else lib.artist = null;
  document.querySelectorAll('.player-tabs .seg-tab').forEach(b => {
    b.classList.toggle('active', b.dataset.tab === lib.tab);
    b.setAttribute('aria-selected', b.dataset.tab === lib.tab ? 'true' : 'false');
  });
  filterPlayerLibrary();
  playerScrollTop();
}

function clearPlayerSearch() {
  $('player-search').value = '';
  filterPlayerLibrary();
  $('player-search').focus();
}

async function refreshPlayerPlaylists(selectedId) {
  const res = await window.pywebview?.api?.get_player_playlists?.();
  if (!res || res.status !== 'ok') {
    toastMgr.error(res?.error || 'Could not load playlists.');
    return false;
  }
  lib.playlists = res.playlists || [];
  const select = $('player-add-playlist');
  if (select) {
    const current = selectedId || select.value;
    select.innerHTML = '<option value="">Choose playlist</option>' + lib.playlists.map(p =>
      `<option value="${escHtml(p.id)}">${escHtml(p.name)}</option>`).join('');
    if (lib.playlists.some(p => p.id === current)) select.value = current;
  }
  $('ptab-count-playlists').textContent = lib.playlists.length.toLocaleString();
  renderSidebarPlaylists();
  filterPlayerLibrary(true);
  return true;
}

// `tracks`: songs a new playlist starts with (Add to Playlist → New, or the
// queue saved from Up Next).
function openPlayerPlaylistEditor(id, tracks) {
  const current = id ? lib.playlists.find(p => p.id === id) : null;
  playerPlaylistEdit = {
    id: current?.id || '',
    cover: current?.cover || '',
    mode: 'edit',
    tracks: !current && Array.isArray(tracks) ? tracks : [],
  };
  $('player-playlist-import-options').classList.add('hidden');
  $('player-playlist-new-fields').classList.remove('hidden');
  $('player-playlist-modal-title').textContent = current ? 'Edit playlist' : 'New playlist';
  $('player-playlist-save').textContent = current ? 'Save changes' : 'Create playlist';
  $('player-playlist-name').value = current?.name || '';
  $('player-playlist-description').value = current?.description || '';
  // An existing playlist can follow a provider playlist: the ones imported
  // before imports remembered their link get it here.
  $('player-playlist-source-field').classList.toggle('hidden', !current);
  $('player-playlist-source').value = current?.source_url || '';
  renderPlayerPlaylistCover();
  $('player-playlist-modal').classList.remove('hidden');
  $('player-playlist-name').focus();
}

function closePlayerPlaylistEditor() {
  $('player-playlist-modal').classList.add('hidden');
  playerPlaylistEdit = null;
}

function updatePlayerPlaylistImportTarget() {
  const target = $('player-playlist-import-target').value;
  $('player-playlist-new-fields').classList.toggle('hidden', !!target);
  $('player-playlist-save').textContent = target ? 'Add tracks to playlist' : 'Create playlist';
}

function renderPlayerPlaylistCover() {
  const img = $('player-playlist-cover-preview');
  const empty = $('player-playlist-cover-placeholder');
  img.src = playerPlaylistEdit?.cover || '';
  img.classList.toggle('hidden', !playerPlaylistEdit?.cover);
  empty.classList.toggle('hidden', !!playerPlaylistEdit?.cover);
}

function openPlayerPlaylistCoverPicker() { $('player-playlist-cover-file').click(); }
function removePlayerPlaylistCover() {
  if (!playerPlaylistEdit) return;
  playerPlaylistEdit.cover = '';
  renderPlayerPlaylistCover();
}

function readFileAsDataURL(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ''));
    reader.onerror = () => reject(reader.error || new Error('Could not read the image.'));
    reader.readAsDataURL(file);
  });
}

async function onPlayerPlaylistCoverChosen(input) {
  const file = input?.files?.[0];
  input.value = '';
  if (!file || !playerPlaylistEdit) return;
  if (file.size > 5_000_000) {
    toastMgr.error('Cover image must be smaller than 5 MB.');
    return;
  }
  try {
    playerPlaylistEdit.cover = await readFileAsDataURL(file);
    renderPlayerPlaylistCover();
  } catch (e) {
    toastMgr.error(e?.message || 'Could not read the cover image.');
  }
}

async function savePlayerPlaylist() {
  if (!playerPlaylistEdit) return;
  const name = $('player-playlist-name').value.trim();
  const description = $('player-playlist-description').value.trim();
  if (!name) {
    toastMgr.error('Enter a playlist name.');
    $('player-playlist-name').focus();
    return;
  }
  const button = $('player-playlist-save');
  const mode = playerPlaylistEdit.mode;
  const editing = !!playerPlaylistEdit.id;
  let sourceAdded = 0;
  if (mode === 'import' && !playerPlaylistEdit.preview.matched) {
    toastMgr.error('No tracks from this file matched your local library.');
    return;
  }
  button.disabled = true;
  try {
    const api = window.pywebview?.api;
    let res;
    if (mode === 'import') {
      res = await api?.import_player_playlist?.(
        playerPlaylistEdit.text,
        playerPlaylistEdit.filename,
        name,
        description,
        playerPlaylistEdit.cover,
        playerPlaylistEdit.candidates,
        $('player-playlist-import-target').value
      );
    } else if (playerPlaylistEdit.id) {
      res = await api?.update_player_playlist?.(
        playerPlaylistEdit.id, name, description, playerPlaylistEdit.cover
      );
      const source = $('player-playlist-source').value.trim();
      const before = lib.playlists.find(p => p.id === playerPlaylistEdit.id)?.source_url || '';
      if (res?.status === 'ok' && source !== before) {
        const linked = await api?.set_player_playlist_source?.(playerPlaylistEdit.id, source);
        if (!linked || linked.status !== 'ok') {
          toastMgr.error(linked?.error || 'Could not follow that playlist link.');
          $('player-playlist-source').focus();
          return;
        }
        sourceAdded = linked.added || 0;
      }
    } else {
      res = await api?.create_player_playlist?.(
        name, description, playerPlaylistEdit.cover, playerPlaylistEdit.tracks || []
      );
    }
    if (!res || res.status !== 'ok') {
      toastMgr.error(res?.error || 'Could not save the playlist.');
      return;
    }
    const id = res.playlist.id;
    closePlayerPlaylistEditor();
    await refreshPlayerPlaylists(id);
    openPlayerPlaylist(id);
    if (mode === 'import') {
      toastMgr.success(res.imported
        ? `Imported ${res.imported} tracks.`
        : 'No new tracks were added; they were already in the playlist.');
    } else {
      const songs = res.playlist.tracks?.length || 0;
      toastMgr.success(editing ? 'Playlist updated.' : songs ? `Playlist created with ${plural(songs, 'song')}.` : 'Playlist created.');
      if (sourceAdded) {
        linkPlaylistTracksToLibrary();
        toastMgr.success(`${plural(sourceAdded, 'new song')} added from the original playlist.`);
      }
    }
  } catch (e) {
    toastMgr.error(e?.message || 'Could not save the playlist.');
  } finally {
    button.disabled = false;
  }
}

function openPlayerPlaylistImport() {
  if (!lib.items.length) {
    toastMgr.error('Scan your music library before importing a playlist.');
    return;
  }
  $('player-playlist-file').click();
}

function playerPlaylistImportCandidates(text) {
  const basenames = new Set();
  const metadata = new Set();
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line) continue;
    if (/^#EXTINF:/i.test(line)) {
      const display = line.slice(line.indexOf(',') + 1).trim();
      const split = display.indexOf(' - ');
      if (split > 0) metadata.add(`${display.slice(0, split).trim().toLowerCase()}\u0000${display.slice(split + 3).trim().toLowerCase()}`);
      else if (display) metadata.add(`\u0000${display.toLowerCase()}`);
      continue;
    }
    if (line.startsWith('#') || /^(https?:|spotify:)/i.test(line)) continue;
    let path = line.replace(/^file:\/\//i, '').split(/[?#]/, 1)[0];
    try { path = decodeURIComponent(path); } catch {}
    basenames.add(path.split(/[\\/]/).pop().toLowerCase());
  }
  return lib.items.filter(track => {
    const basename = String(track.path || '').split(/[\\/]/).pop().toLowerCase();
    const artist = String(track.artist || '').toLowerCase();
    const title = String(track.title || '').toLowerCase();
    return basenames.has(basename) || metadata.has(`${artist}\u0000${title}`) || metadata.has(`\u0000${title}`);
  });
}

async function preparePlayerPlaylistImport(text, filename, name = '') {
  if (!lib.items.length) {
    toastMgr.error('Scan your music library before importing a playlist.');
    return false;
  }
  const playlistName = name.trim() ||
    filename.replace(/\.(m3u8?)$/i, '').trim() || 'Imported playlist';
  const candidates = playerPlaylistImportCandidates(text);
  const preview = await window.pywebview?.api?.preview_player_playlist_import?.(
    text, filename, candidates
  );
  if (!preview || preview.status !== 'ok') {
    toastMgr.error(preview?.error || 'Could not preview the playlist.');
    return false;
  }
  await refreshPlayerPlaylists();
  playerPlaylistEdit = {
    id: '',
    mode: 'import',
    text,
    filename,
    name: playlistName,
    description: '',
    cover: '',
    candidates,
    preview,
  };
  $('player-playlist-modal-title').textContent = 'Import playlist';
  $('player-playlist-save').textContent = 'Create playlist';
  $('player-playlist-name').value = playlistName;
  $('player-playlist-description').value = '';
  $('player-playlist-import-target').innerHTML = '<option value="">Create a new playlist</option>' +
    lib.playlists.map(p => `<option value="${escHtml(p.id)}">${escHtml(p.name)}</option>`).join('');
  $('player-playlist-import-preview').textContent =
    `${preview.matched} tracks found; ${preview.skipped} not found; ${preview.ambiguous} ambiguous.` +
    (preview.missing_tracks?.length ? ` Missing: ${preview.missing_tracks.join(', ')}.` : '') +
    (preview.ambiguous_tracks?.length ? ` Ambiguous: ${preview.ambiguous_tracks.join(', ')}.` : '');
  $('player-playlist-import-options').classList.remove('hidden');
  $('player-playlist-new-fields').classList.remove('hidden');
  $('player-playlist-import-target').value = '';
  renderPlayerPlaylistCover();
  $('player-playlist-modal').classList.remove('hidden');
  $('player-playlist-name').focus();
  return true;
}

async function onPlayerPlaylistFileChosen(input) {
  const file = input?.files?.[0];
  input.value = '';
  if (!file) return;
  try {
    const text = await readFileAsText(file);
    await preparePlayerPlaylistImport(text, file.name);
  } catch (e) {
    toastMgr.error(e?.message || 'Could not read the playlist file.');
  }
}

async function importPlayerPlaylistFromUrl() {
  const input = $('player-playlist-url');
  const url = input.value.trim();
  if (!url) {
    toastMgr.error('Paste a direct M3U/M3U8 link first.');
    input.focus();
    return;
  }
  if (!lib.items.length) {
    toastMgr.error('Scan your music library before importing a playlist.');
    return;
  }
  const button = $('player-playlist-url-import');
  button.disabled = true;
  try {
    const result = await window.pywebview?.api?.fetch_player_playlist_import?.(url);
    if (!result || result.status !== 'ok') {
      toastMgr.error(result?.error || 'Could not download the playlist link.');
      return;
    }
    await preparePlayerPlaylistImport(result.text, result.filename);
  } catch (e) {
    toastMgr.error(e?.message || 'Could not download the playlist link.');
  } finally {
    button.disabled = false;
  }
}

function openPlayerPlaylist(id) {
  const playlist = lib.playlists.find(p => p.id === id);
  if (!playlist) return;
  lib.tab = 'playlists';
  lib.album = null;
  lib.artist = null;
  if (lib.playlist !== playlist.id) $('player-search').value = '';
  lib.playlist = playlist.id;
  document.querySelectorAll('.player-tabs .seg-tab').forEach(b => {
    const on = b.dataset.tab === 'playlists';
    b.classList.toggle('active', on);
    b.setAttribute('aria-selected', on ? 'true' : 'false');
  });
  filterPlayerLibrary();
  playerScrollTop();
}

async function removePlayerPlaylistTrack(index) {
  const playlist = lib.playlists.find(p => p.id === lib.playlist);
  const track = lib.view[Number(index)];
  if (!playlist || !track) return;
  try {
    const res = await window.pywebview?.api?.remove_player_playlist_track?.(playlist.id, track.path);
    if (!res || res.status !== 'ok') {
      toastMgr.error(res?.error || 'Could not remove the track.');
      return;
    }
    await refreshPlayerPlaylists(playlist.id);
    openPlayerPlaylist(playlist.id);
  } catch (e) {
    toastMgr.error(e?.message || 'Could not remove the track.');
  }
}

async function deletePlayerPlaylist(id) {
  const playlist = lib.playlists.find(p => p.id === id);
  if (!playlist || !window.confirm(`Delete “${playlist.name}”?`)) return;
  try {
    const res = await window.pywebview?.api?.delete_player_playlist?.(id);
    if (!res || res.status !== 'ok') {
      toastMgr.error(res?.error || 'Could not delete the playlist.');
      return;
    }
    lib.playlist = null;
    await refreshPlayerPlaylists();
    toastMgr.success('Playlist deleted.');
  } catch (e) {
    toastMgr.error(e?.message || 'Could not delete the playlist.');
  }
}

async function reorderPlayerPlaylistTrack(index, delta) {
  const playlist = lib.playlists.find(p => p.id === lib.playlist);
  if (!playlist || lib.playlistFiltered) return;
  const ordered = playlist.tracks.map(track => track.path);
  const from = Number(index);
  const to = from + Number(delta);
  if (!Number.isInteger(from) || to < 0 || to >= ordered.length) return;
  [ordered[from], ordered[to]] = [ordered[to], ordered[from]];
  try {
    const res = await window.pywebview?.api?.reorder_player_playlist_tracks?.(playlist.id, ordered);
    if (!res || res.status !== 'ok') {
      toastMgr.error(res?.error || 'Could not reorder playlist tracks.');
      return;
    }
    await refreshPlayerPlaylists(playlist.id);
    openPlayerPlaylist(playlist.id);
  } catch (e) {
    toastMgr.error(e?.message || 'Could not reorder playlist tracks.');
  }
}

async function exportPlayerPlaylist(id) {
  try {
    const res = await window.pywebview?.api?.export_player_playlist?.(id);
    if (!res || res.status !== 'ok') {
      toastMgr.error(res?.error || 'Could not export the playlist.');
      return;
    }
    const url = URL.createObjectURL(new Blob([res.text], { type: 'audio/x-mpegurl;charset=utf-8' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = `${res.name}.m3u8`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    toastMgr.success('Playlist exported as M3U8.');
  } catch (e) {
    toastMgr.error(e?.message || 'Could not export the playlist.');
  }
}

function filterPlayerLibrary(keepScroll) {
  const q = ($('player-search').value || '').trim().toLowerCase();
  $('player-search-clear').classList.toggle('hidden', !q);
  const matches = q
    ? lib.items.filter(t => `${t.title} ${t.artist} ${t.album} ${t.album_artist || ''}`.toLowerCase().includes(q))
    : lib.items;
  const { albums: allAlbums, artists: allArtists } = libAll();
  const tab = lib.tab || 'songs';

  // Library bar and tab counts describe the whole library, not the search.
  const total = lib.items.reduce((s, t) => s + (t.duration_s || 0), 0);
  const folders = (lib.folders && lib.folders.length) ? lib.folders : [lib.folder].filter(Boolean);
  $('plb-name').textContent = libFoldersLabel(folders);
  $('plb-name').title = folders.join('\n');
  $('plb-stats').textContent = [plural(lib.items.length, 'track'), plural(allAlbums.length, 'album'),
    plural(allArtists.length, 'artist'), libDuration(total)].filter(Boolean).join(' · ');
  syncPlayerLibbar();
  $('ptab-count-songs').textContent = lib.items.length.toLocaleString();
  $('ptab-count-albums').textContent = allAlbums.length.toLocaleString();
  $('ptab-count-artists').textContent = allArtists.length.toLocaleString();
  $('ptab-count-playlists').textContent = lib.playlists.length.toLocaleString();
  $('player-toolbar').classList.toggle('hidden', lib.items.length === 0 && tab !== 'playlists');
  const addSelect = $('player-add-select-wrap');
  // A song's "+" opens the list of playlists itself (openActionMenu).
  addSelect.classList.add('hidden');
  $('player-play-controls').classList.toggle('hidden', tab === 'playlists' && !lib.playlist);
  // Inside a playlist the search looks through its songs only.
  const inPlaylistDetail = tab === 'playlists' && !!lib.playlist;
  $('player-search-wrap').classList.toggle('hidden', tab === 'playlists' && !inPlaylistDetail);
  $('player-search').placeholder = inPlaylistDetail ? 'Search in this playlist' : 'Search title, artist or album';
  lib.playlistFiltered = false;
  // New playlist / Import belong to the list of playlists, not every page.
  $('player-playlist-tools')?.classList.toggle('hidden', !(tab === 'playlists' && !lib.playlist));

  const list = $('player-list'), grid = $('player-grid'), detail = $('player-detail');
  let rows = null, cards = null, header = '', count = '', recent = null, shownAlbums = null, shownArtists = null;

  if (tab === 'playlists' && lib.playlist) {
    const playlist = lib.playlists.find(p => p.id === lib.playlist);
    if (!playlist) { lib.playlist = null; return filterPlayerLibrary(keepScroll); }
    header = libPlaylistHeaderHtml(playlist);
    if (q) {
      // The songs keep their number in the playlist; moving one is off
      // while the list is filtered, since its neighbours are hidden.
      lib.playlistPos = new Map(playlist.tracks.map((t, n) => [t.path, n]));
      lib.playlistFiltered = true;
      rows = playlist.tracks.filter(t => `${t.title} ${t.artist} ${t.album}`.toLowerCase().includes(q));
      count = `${rows.length.toLocaleString()} of ${plural(playlist.tracks.length, 'track')}`;
    } else {
      rows = playlist.tracks;
      count = plural(rows.length, 'track');
    }
  } else if (tab === 'playlists') {
    cards = lib.playlists.length
      ? lib.playlists.map(libPlaylistCardHtml)
      : ['<div class="pl-playlist-empty">No playlists yet. Create one or import an M3U/M3U8 file.</div>'];
    count = plural(lib.playlists.length, 'playlist');
  } else if ((tab === 'albums' || tab === 'recent') && lib.album) {
    const album = allAlbums.find(a => a.key === lib.album);
    if (!album) { lib.album = null; return filterPlayerLibrary(keepScroll); }
    rows = album.tracks;
    header = libAlbumHeaderHtml(album);
    count = '';
  } else if (tab === 'artists' && lib.artist) {
    const artist = allArtists.find(a => a.key === lib.artist);
    if (!artist) { lib.artist = null; return filterPlayerLibrary(keepScroll); }
    const { own, guest } = libAlbumsOf(allAlbums, artist.key);
    // As a music app's artist page: shelves under the hero, newest first —
    // Albums, Singles & EPs, Appears On, Similar Artists.
    const newest = list => list.slice().sort((x, y) => String(y.year || '').localeCompare(String(x.year || '')));
    const singles = own.filter(libIsSingle);
    const full = own.filter(al => !libIsSingle(al));
    cards = [];
    header = libArtistHeaderHtml(artist, [...own, ...guest])
      + libShelfHtml('Albums', newest(full).map(libAlbumCardHtml))
      + libShelfHtml('Singles & EPs', newest(singles).map(libAlbumCardHtml))
      + libShelfHtml('Appears On', newest(guest).map(libAlbumCardHtml))
      + libShelfHtml('Similar Artists', libSimilarArtists(artist.key, allArtists).map(libArtistCardHtml), 'artists');
  } else if (tab === 'recent') {
    // The albums whose newest file arrived last, newest first.
    recent = libAlbumsFor(matches)
      .filter(a => a.added)
      .sort((x, y) => y.added - x.added)
      .slice(0, LIB_RECENT_MAX);
    cards = recent.map(libAlbumCardHtml);
    count = plural(recent.length, 'album');
  } else if (tab === 'albums') {
    const albums = libAlbumsFor(matches);
    shownAlbums = albums;
    cards = albums.map(libAlbumCardHtml);
    count = plural(albums.length, 'album');
  } else if (tab === 'artists') {
    const artists = libArtistsFor(matches);
    shownArtists = artists;
    cards = artists.map(libArtistCardHtml);
    count = plural(artists.length, 'artist');
  } else {
    rows = matches.slice();
    const cmp = libSortKey($('player-sort').value);
    if (cmp) rows.sort(cmp);
    const dur = rows.reduce((s, t) => s + (t.duration_s || 0), 0);
    count = [plural(rows.length, 'track'), libDuration(dur)].filter(Boolean).join(' · ');
  }

  const inDetail = !!header;
  $('player-sort-wrap').classList.toggle('hidden', tab !== 'songs');
  $('player-colhead').classList.toggle('hidden', tab !== 'songs');
  // The music look lays the Songs page out as a table with its own head
  // (docs/mocks/music-style/): the count and length under the title.
  $('view-player').classList.toggle('is-songs', tab === 'songs' && !inDetail);
  $('player-msub').textContent = [plural(lib.items.length, 'song'), libDuration(total)].filter(Boolean).join(', ');
  $('player-count').textContent = count;
  detail.innerHTML = header;
  detail.classList.toggle('hidden', !inDetail);
  // The music look lets an artist's photo run to the top of the page.
  $('view-player').classList.toggle('is-artist-page', tab === 'artists' && !!lib.artist);
  if (header) libObserve(detail);

  if (cards) {
    lib.view = [];
    lib.shown = 0;
    list.innerHTML = '';
    // A page of cards at a time, like the rows: thousands of cards built
    // (and torn down again on the next tab) at once froze the window.
    lib.cardHtml = cards;
    lib.cardsShown = Math.min(cards.length, Math.max(LIB_CARD_PAGE, keepScroll === true ? lib.cardsShown : 0));
    grid.innerHTML = cards.slice(0, lib.cardsShown).join('') + libCardSentinel();
    grid.classList.remove('hidden');
    libObserve(grid);
  } else {
    lib.cardHtml = [];
    lib.cardsShown = 0;
    grid.innerHTML = '';
    grid.classList.add('hidden');
    lib.view = rows || [];
    list.classList.toggle('album-tracks', !!lib.album);
    // Under the column header the rows start at once; elsewhere the page gap stays.
    list.classList.toggle('flush', tab === 'songs');
    lib.shown = Math.min(lib.view.length, Math.max(LIB_PAGE, keepScroll === true ? lib.shown : 0));
    renderPlayerRows();
  }
  lib.cards = cards ? cards.length : 0;
  // What "Play all" / "Shuffle" in the toolbar play on the current page: the
  // rows when there are rows, otherwise every album shown, in grid order.
  // Worked out when one of them is pressed (libPlayAll), not on every render.
  if (cards) {
    if (tab === 'artists' && lib.artist) {
      const key = lib.artist;
      lib.playAll = () => libArtistTracks(key);
    } else if (tab === 'artists') {
      const shown = new Set((shownArtists || []).map(a => a.key));
      lib.playAll = () => allAlbums.flatMap(a => a.tracks).filter(t => libCredited(t).some(n => shown.has(n.toLowerCase())));
    } else if (tab === 'playlists') {
      lib.playAll = null;
    } else if (recent) {
      lib.playAll = recent.flatMap(a => a.tracks);
    } else {
      const shown = new Set((shownAlbums || []).map(a => a.key));
      lib.playAll = () => allAlbums.filter(a => shown.has(a.key)).flatMap(a => a.tracks);
    }
  } else {
    lib.playAll = null;
  }
  renderPlayerEmptyState();
  syncLibraryNav();
}

const LIB_PLAY_SVG = '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5v14l11-7z"/></svg>';
const LIB_SHUFFLE_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="16 3 21 3 21 8"/><line x1="4" y1="20" x2="21" y2="3"/><polyline points="21 16 21 21 16 21"/><line x1="15" y1="15" x2="21" y2="21"/><line x1="4" y1="4" x2="9" y2="9"/></svg>';
const LIB_BACK_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="15 18 9 12 15 6"/></svg>';
const LIB_MORE_SVG = '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="5" cy="12" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="19" cy="12" r="1.8"/></svg>';
const LIB_NOTE_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>';
let playerPlaylistEdit = null;

// `photo` is an artist's name: their own photo replaces the album cover
// once it arrives (see libLoadPhoto), and the cover stays if none is found.
function libCoverHtml(path, size, extraClass = '', photo = '') {
  const photoAttr = photo ? ` data-photo="${escHtml(photo)}"` : '';
  return `<div class="pl-cover ${extraClass}" data-p="${escHtml(path || '')}" data-size="${size}"${photoAttr}>
    <img alt="" draggable="false"><span class="pl-cover-ph">${LIB_NOTE_SVG}</span></div>`;
}

function libAlbumCardHtml(a) {
  const meta = [a.artist, a.year].filter(Boolean).join(' · ');
  return `<div class="pl-card" role="listitem" tabindex="0" data-activate data-album="${escHtml(a.key)}" title="${escHtml(a.title)} — ${escHtml(a.artist)}">
    <div class="pl-card-art">${libCoverHtml(a.tracks[0]?.path, 320)}
      <button type="button" class="pl-card-play" data-play-album="${escHtml(a.key)}" aria-label="Play ${escHtml(a.title)}">${LIB_PLAY_SVG}</button></div>
    <div class="pl-card-t">${escHtml(a.title)}</div>
    <div class="pl-card-a">${escHtml(meta)}</div>
  </div>`;
}

function libArtistCardHtml(a) {
  const first = libArtistCover(a.key);
  const meta = `${plural(a.albums.size, 'album')} · ${plural(a.tracks.length, 'track')}`;
  return `<div class="pl-card artist" role="listitem" tabindex="0" data-activate data-artist="${escHtml(a.key)}" title="${escHtml(a.name)}">
    <div class="pl-card-art">${libCoverHtml((first?.tracks[0] || a.tracks[0])?.path, 320, '', a.name)}
      <button type="button" class="pl-card-play" data-play-artist="${escHtml(a.key)}" aria-label="Play ${escHtml(a.name)}">${LIB_PLAY_SVG}</button></div>
    <div class="pl-card-t">${escHtml(a.name)}</div>
    <div class="pl-card-a">${escHtml(meta)}</div>
  </div>`;
}

function libPlaylistCardHtml(playlist) {
  const cover = playlist.cover
    ? `<img src="${escHtml(playlist.cover)}" alt="" draggable="false">`
    : `<span class="pl-cover-ph">${LIB_NOTE_SVG}</span>`;
  const summary = playlist.description || plural(playlist.tracks.length, 'track');
  return `<div class="pl-card pl-playlist-card" role="listitem" tabindex="0" data-activate data-player-playlist="${escHtml(playlist.id)}" title="${escHtml(playlist.name)}">
    <div class="pl-card-art"><div class="pl-cover ${playlist.cover ? 'has-cover' : ''}">${cover}</div></div>
    <div class="pl-card-t">${escHtml(playlist.name)}</div>
    <div class="pl-card-a">${escHtml(summary)}</div>
    <div class="pl-playlist-card-actions">
      <button type="button" class="act-btn secondary" data-edit-player-playlist="${escHtml(playlist.id)}">Edit</button>
      <button type="button" class="act-btn secondary" data-delete-player-playlist="${escHtml(playlist.id)}">Delete</button>
    </div>
  </div>`;
}

// A playlist song is "not downloaded" when it was never linked to a file
// (its key is remote:…) or, once a scan is over, when its file sits in a
// library folder but the scan did not find it there (deleted or moved).
// So Rescan moves the count both ways: new files are linked by
// linkPlaylistTracksToLibrary, missing ones are counted here.
let libPathCache = { items: null, len: -1, set: new Set() };
function libPathSet() {
  if (libPathCache.items !== lib.items || libPathCache.len !== lib.items.length) {
    libPathCache = { items: lib.items, len: lib.items.length, set: new Set(lib.items.map(t => t.path)) };
  }
  return libPathCache.set;
}
function libPathUnder(path, folder) {
  const root = String(folder || '').replace(/[\\/]+$/, '');
  return !!root && (path.startsWith(root + '/') || path.startsWith(root + '\\'));
}
function libTrackMissing(t) {
  if (t.downloaded === false) return true;
  if (lib.scanning || lib.loading || lib.partial || !lib.items.length) return false;
  const path = String(t.path || '');
  if (!libFolders().some(f => libPathUnder(path, f))) return false;
  return !libPathSet().has(path);
}

function libPlaylistHeaderHtml(playlist) {
  const cover = playlist.cover
    ? `<img src="${escHtml(playlist.cover)}" alt="" draggable="false">`
    : `<span class="pl-cover-ph">${LIB_NOTE_SVG}</span>`;
  const duration = playlist.tracks.reduce((sum, track) => sum + (track.duration_s || 0), 0);
  const missing = playlist.tracks.filter(libTrackMissing).length;
  const meta = [plural(playlist.tracks.length, 'track'), missing ? `${missing} not downloaded` : '', libDuration(duration)].filter(Boolean);
  return `<button type="button" class="pl-back" data-onclick="playerBack()">${LIB_BACK_SVG}Playlists</button>
    <div class="pl-detail-head">
      <div class="pl-cover ${playlist.cover ? 'has-cover' : ''} pl-detail-cover">${cover}</div>
      <div class="pl-detail-info">
        <span class="pl-detail-kind">Playlist</span>
        <h2 class="pl-detail-title">${escHtml(playlist.name)}</h2>
        ${playlist.description ? `<div class="pl-playlist-description">${escHtml(playlist.description)}</div>` : ''}
        <div class="pl-detail-meta">${meta.map(escHtml).join('<span aria-hidden="true"> · </span>')}</div>
        <div class="pl-detail-actions">
          <button class="pt-btn primary" type="button" data-play-player-playlist="${escHtml(playlist.id)}">${LIB_PLAY_SVG}Play</button>
          <button class="pt-btn" type="button" data-shuffle-player-playlist="${escHtml(playlist.id)}">${LIB_SHUFFLE_SVG}Shuffle</button>
          <button class="pt-btn" type="button" data-edit-player-playlist="${escHtml(playlist.id)}">Edit</button>
          <button class="pt-btn" type="button" data-export-player-playlist="${escHtml(playlist.id)}">Export M3U8</button>
          ${playlist.source_url ? `<button class="pt-btn" type="button" data-sync-player-playlist="${escHtml(playlist.id)}" title="Add the songs added to the original playlist">Check for new songs</button>` : ''}
          <button class="pt-btn pl-more-btn" type="button" data-group-menu="playlist" data-group-key="${escHtml(playlist.id)}" aria-haspopup="menu" aria-label="More" title="More">${LIB_MORE_SVG}</button>
        </div>
      </div>
    </div>`;
}

function libDetailActions(kind, key) {
  return `<div class="pl-detail-actions">
    <button class="pt-btn primary" type="button" data-play-${kind}="${escHtml(key)}">${LIB_PLAY_SVG}Play</button>
    <button class="pt-btn" type="button" data-shuffle-${kind}="${escHtml(key)}">${LIB_SHUFFLE_SVG}Shuffle</button>
    <button class="pt-btn pl-more-btn" type="button" data-group-menu="${kind}" data-group-key="${escHtml(key)}" aria-haspopup="menu" aria-label="More" title="More">${LIB_MORE_SVG}</button>
  </div>`;
}

function libAlbumArtistNames(a) {
  const t = a.tracks[0] || {};
  return t.album_artist_names && t.album_artist_names.length ? t.album_artist_names : [a.artist];
}

function libAlbumHeaderHtml(a) {
  const back = lib.artist ? 'Artist' : 'Albums';
  const meta = [a.year, plural(a.tracks.length, 'track'), libDuration(a.duration), [...a.formats].join(' / ')].filter(Boolean);
  return `<button type="button" class="pl-back" data-onclick="playerBack()">${LIB_BACK_SVG}${back}</button>
    <div class="pl-detail-head">
      ${libCoverHtml(a.tracks[0]?.path, 400, 'pl-detail-cover')}
      <div class="pl-detail-info">
        <span class="pl-detail-kind">Album</span>
        <h2 class="pl-detail-title">${escHtml(a.title)}</h2>
        <div class="pl-detail-artists">${libAlbumArtistNames(a).map(n =>
          `<button type="button" class="pl-detail-artist" data-artist-link="${escHtml(n.toLowerCase())}">${escHtml(n)}</button>`).join(', ')}</div>
        <div class="pl-detail-meta">${meta.map(escHtml).join('<span aria-hidden="true"> · </span>')}</div>
        ${libDetailActions('album', a.key)}
      </div>
    </div>`;
}

const LIB_PLAYS = t => parseInt(t.plays ?? t.playcount ?? t.playCount ?? t.plays_count ?? '0', 10) || 0;

// The artist's newest release: their own albums first, a guest spot only
// when they have none.
function libLatestAlbum(albums) {
  const newest = list => list.slice().sort((x, y) => String(y.year || '').localeCompare(String(x.year || '')))[0];
  return newest(albums.filter(al => libAlbumIsBy(al, lib.artist))) || newest(albums);
}

// A single or an EP: three songs or fewer, or named so.
function libIsSingle(album) {
  return album.tracks.length <= 3 || /(?:^|[\s(\[-])(single|ep)(?:$|[\s)\]])/i.test(album.title || '');
}

// The artists most often credited next to this one in the library.
function libSimilarArtists(key, allArtists) {
  const score = new Map();
  for (const t of libArtistTracks(key)) {
    for (const name of libCredited(t)) {
      const k = name.toLowerCase();
      if (k !== key) score.set(k, (score.get(k) || 0) + 1);
    }
  }
  return allArtists.filter(a => score.has(a.key)).sort((x, y) => score.get(y.key) - score.get(x.key)).slice(0, 12);
}

// A titled row of cards that scrolls sideways.
function libShelfHtml(title, cards, extra = '') {
  if (!cards.length) return '';
  return `<section class="pl-shelf ${extra}">
      <h3 class="pl-sec-title">${escHtml(title)}</h3>
      <div class="pl-shelf-row" role="list">${cards.join('')}</div>
    </section>`;
}

// Top songs by play count when the files carry one; otherwise the newest
// release's tracks lead, in album order.
function libArtistTopSongs(key, albums) {
  const tracks = libArtistTracks(key).filter(t => t.downloaded !== false);
  if (tracks.some(t => LIB_PLAYS(t) > 0)) {
    return { ranked: true, tracks: tracks.slice().sort((x, y) => LIB_PLAYS(y) - LIB_PLAYS(x)).slice(0, 9) };
  }
  const order = albums.slice().sort((x, y) => String(y.year || '').localeCompare(String(x.year || '')));
  const rank = new Map(order.map((al, i) => [al.key, i]));
  const sorted = tracks.slice().sort((x, y) => (rank.get(libAlbumKey(x)) ?? 99) - (rank.get(libAlbumKey(y)) ?? 99));
  return { ranked: false, tracks: sorted.slice(0, 9) };
}

function libArtistHeaderHtml(a, albums) {
  const meta = [plural(albums.length, 'album'), plural(a.tracks.length, 'track'), libDuration(a.duration)].filter(Boolean);
  const heroPath = (albums[0]?.tracks[0] || a.tracks[0])?.path;
  const latest = libLatestAlbum(albums);
  const top = libArtistTopSongs(a.key, albums);
  lib.artistTop = top.tracks;
  const latestHtml = latest ? `<section class="pl-artist-sec">
      <h3 class="pl-sec-title">Latest Release</h3>
      <div class="pl-latest" role="button" tabindex="0" data-activate data-album="${escHtml(latest.key)}" title="${escHtml(latest.title)}">
        ${libCoverHtml(latest.tracks[0]?.path, 400, 'pl-latest-cover')}
        <div class="pl-latest-info">
          ${latest.year ? `<span class="pl-latest-date">${escHtml(String(latest.year))}</span>` : ''}
          <span class="pl-latest-title">${escHtml(latest.title)}</span>
          <span class="pl-latest-meta">${escHtml(plural(latest.tracks.length, 'track'))}</span>
        </div>
      </div>
    </section>` : '';
  const topHtml = top.tracks.length ? `<section class="pl-artist-sec">
      <h3 class="pl-sec-title"><button type="button" class="pl-sec-link" data-artist-songs="${escHtml(a.name)}" title="All songs">${top.ranked ? 'Top Songs' : 'Songs'}<span aria-hidden="true"> ›</span></button></h3>
      <div class="pl-top-list" role="list">${top.tracks.map((t, i) => `
        <div class="pl-top-row" role="listitem" tabindex="0" data-activate data-artist-top="${i}" title="Play ${escHtml(t.title)}">
          ${libCoverHtml(t.path, 120, 'pl-top-cover')}
          <div class="pl-top-info"><span class="pl-top-t">${escHtml(t.title)}</span><span class="pl-top-a">${escHtml([t.album, t.year].filter(Boolean).join(' · '))}</span></div>
          <span class="pl-top-dur">${t.duration_s ? fmtTime(t.duration_s) : ''}</span>
          <button type="button" class="pl-top-more" data-top-menu="${i}" aria-haspopup="menu" aria-label="More for ${escHtml(t.title)}" title="More">${LIB_MORE_SVG}</button>
        </div>`).join('')}
      </div>
    </section>` : '';
  return `<div class="pl-artist-hero">
      ${libCoverHtml(heroPath, 400, 'pl-artist-hero-bg', a.name)}
      <button type="button" class="pl-hero-back" data-onclick="playerBack()" aria-label="Back to artists" title="Artists">${LIB_BACK_SVG}</button>
      <div class="pl-artist-hero-body">
        <h2 class="pl-artist-name">${escHtml(a.name)}</h2>
        <div class="pl-artist-meta">${meta.map(escHtml).join('<span aria-hidden="true"> · </span>')}</div>
        <div class="pl-artist-actions">
          <button type="button" class="pl-round" data-shuffle-artist="${escHtml(a.key)}" aria-label="Shuffle ${escHtml(a.name)}" title="Shuffle">${LIB_SHUFFLE_SVG}</button>
          <button type="button" class="pl-round primary" data-play-artist="${escHtml(a.key)}" aria-label="Play ${escHtml(a.name)}" title="Play">${LIB_PLAY_SVG}</button>
          <button type="button" class="pl-round" data-group-menu="artist" data-group-key="${escHtml(a.key)}" aria-haspopup="menu" aria-label="More for ${escHtml(a.name)}" title="More">${LIB_MORE_SVG}</button>
        </div>
      </div>
    </div>
    ${latestHtml || topHtml ? `<div class="pl-artist-cols">${latestHtml}${topHtml}</div>` : ''}`;
}

function libRowHtml(t, i) {
  const inAlbum = !!lib.album;
  const art = inAlbum ? (t.artist && t.artist !== libArtistOf(t) ? t.artist : '') : [t.artist, t.album].filter(Boolean).join(' — ');
  const num = inAlbum && t.track ? t.track
    : lib.playlistFiltered ? (lib.playlistPos?.get(t.path) ?? i) + 1 : i + 1;
  const rowAction = lib.tab === 'playlists' && lib.playlist
    ? `<span class="pl-row-actions${lib.playlistFiltered ? ' is-filtered' : ''}">
        <button type="button" class="pl-row-action" data-move-player-track="${i},-1" ${i === 0 || lib.playlistFiltered ? 'disabled' : ''} aria-label="Move ${escHtml(t.title)} up" title="Move up">↑</button>
        <button type="button" class="pl-row-action" data-move-player-track="${i},1" ${i === lib.view.length - 1 || lib.playlistFiltered ? 'disabled' : ''} aria-label="Move ${escHtml(t.title)} down" title="Move down">↓</button>
        <button type="button" class="pl-row-action" data-remove-player-track="${i}" aria-label="Remove ${escHtml(t.title)} from playlist" title="Remove from playlist">−</button>
      </span>`
    : `<button type="button" class="pl-row-action" data-add-player-track="${i}" aria-haspopup="menu" aria-label="Add ${escHtml(t.title)} to a playlist" title="Add to a playlist">+</button>`;
  const more = `<button type="button" class="pl-row-action pl-row-more" data-track-menu="${i}" aria-haspopup="menu" aria-label="More for ${escHtml(t.title)}" title="More">${LIB_MORE_SVG}</button>`;
  const remote = libTrackMissing(t);
  const badge = remote ? '<span class="pl-not-downloaded" title="Not in your library yet: download it and it plays from here">Not downloaded</span>' : '';
  return `<div class="pl-row ${remote ? 'is-remote' : ''}" role="listitem" tabindex="0" data-activate data-i="${i}" data-p="${remote ? '' : escHtml(t.path)}" data-size="0">
    <div class="pl-num"><span class="n">${num}</span><span class="pi" aria-hidden="true">${LIB_PLAY_SVG}</span><span class="eq" aria-hidden="true"><i></i><i></i><i></i></span></div>
    <div class="pl-thumb"><img alt="" draggable="false"></div>
    <div class="pl-main"><div class="pl-main-info"><div class="pl-t">${escHtml(t.title)}${badge}</div>${art ? `<div class="pl-a">${escHtml(art)}</div>` : ''}</div>${rowAction}${more}</div>
    <div class="pl-dur">${t.duration_s ? fmtTime(t.duration_s) : ''}</div>
    <div class="pl-fmt">${escHtml(t.format)}</div>
    <div class="pl-c-artist m-only">${escHtml(t.artist || '')}</div>
    <div class="pl-c-album m-only">${escHtml(t.album || '')}</div>
  </div>`;
}

function renderPlayerRows() {
  const list = $('player-list');
  list.innerHTML = lib.view.slice(0, lib.shown).map(libRowHtml).join('')
    + (lib.shown < lib.view.length ? '<div id="player-sentinel" class="pl-more">Loading more…</div>' : '');
  libObserve(list);
  markPlayingRow();
}

function renderPlayerEmptyState() {
  const empty = $('player-empty');
  if (!empty) return;
  const show = !lib.loading && lib.view.length === 0 && !lib.cards && !lib.album && !lib.artist;
  empty.classList.toggle('hidden', !show);
  if (!show) return;

  const title = empty.querySelector('.player-empty-title');
  const copy = empty.querySelector('.player-empty-copy');
  if (lib.tab === 'playlists' && lib.playlist) {
    title.textContent = 'This playlist is empty';
    copy.textContent = 'Add tracks from Songs, Albums or Artists.';
    return;
  }
  if (lib.tab === 'playlists') {
    title.textContent = 'No playlists yet';
    copy.textContent = 'Create a playlist or import an M3U/M3U8 file above.';
    return;
  }
  const query = ($('player-search')?.value || '').trim();
  if (query && lib.items.length) {
    title.textContent = 'Nothing matches';
    copy.textContent = 'Try a different title, artist, or album search.';
  } else if (lib.scan !== null && !lib.items.length) {
    title.textContent = 'No audio files found';
    copy.textContent = 'Choose a folder that contains music, then scan it again.';
  } else {
    title.textContent = 'Choose a music folder';
    copy.textContent = 'Your tracks will appear here after the library is scanned.';
  }
}

const LIB_CARD_PAGE = 120;
function libCardSentinel() {
  return lib.cardsShown < lib.cardHtml.length ? '<div id="player-grid-sentinel" class="pl-more">Loading more…</div>' : '';
}
function libAppendCards() {
  const grid = $('player-grid');
  grid.querySelector('#player-grid-sentinel')?.remove();
  const from = lib.cardsShown;
  lib.cardsShown = Math.min(lib.cardHtml.length, from + LIB_CARD_PAGE);
  grid.insertAdjacentHTML('beforeend', lib.cardHtml.slice(from, lib.cardsShown).join('') + libCardSentinel());
  libObserve(grid);
}

// "Play all" on a page of cards is worked out when pressed.
function libPlayAll() {
  if (typeof lib.playAll === 'function') lib.playAll = lib.playAll();
  return lib.playAll || [];
}

function libAppendRows() {
  const list = $('player-list');
  const from = list.querySelectorAll('.pl-row').length;
  const to = Math.min(lib.view.length, from + LIB_PAGE);
  list.querySelector('#player-sentinel')?.remove();
  list.insertAdjacentHTML('beforeend', lib.view.slice(from, to).map((t, k) => libRowHtml(t, from + k)).join('')
    + (to < lib.view.length ? '<div id="player-sentinel" class="pl-more">Loading more…</div>' : ''));
  lib.shown = to;
  libObserve(list);
  markPlayingRow();
}

// Thumbnails and "load more" are driven by what scrolls into view.
const libIO = typeof IntersectionObserver === 'function' ? new IntersectionObserver(entries => {
  entries.forEach(en => {
    if (!en.isIntersecting) return;
    const el = en.target;
    if (el.id === 'player-sentinel') { libIO.unobserve(el); libAppendRows(); return; }
    if (el.id === 'player-grid-sentinel') { libIO.unobserve(el); libAppendCards(); return; }
    libIO.unobserve(el);
    libLoadThumb(el);
  });
}, { rootMargin: '300px' }) : null;

function libObserve(root) {
  const targets = root.querySelectorAll('.pl-row:not([data-th]), .pl-cover:not([data-th])');
  if (!libIO) { targets.forEach(libLoadThumb); return; }
  targets.forEach(r => libIO.observe(r));
  const s = root.querySelector('#player-sentinel, #player-grid-sentinel');
  if (s) libIO.observe(s);
}

// Re-rendering a grid (search, tab switch) must not ask the backend again
// for covers it has already sent.
const libThumbCache = new Map();
const libThumbQueue = [];
let libThumbPumpQueued = false;
function libLoadThumb(el) {
  el.dataset.th = '1';
  if (el.dataset.photo) libLoadPhoto(el);
  if (!el.dataset.p) return;
  const key = `${el.dataset.p}\u0000${el.dataset.size || 0}`;
  if (libThumbCache.has(key)) { libShowThumb(el, libThumbCache.get(key)); return; }
  libThumbQueue.push(el);
  if (!libThumbPumpQueued) {
    libThumbPumpQueued = true;
    setTimeout(() => { libThumbPumpQueued = false; libPumpThumbs(); }, 0);
  }
}
function libShowThumb(el, url) {
  const img = el.querySelector('img');
  if (!url || !img) return;
  el._libCover = url; // the fallback if an artist photo fails to load
  if (el.classList.contains('has-photo')) return; // the artist's photo is already up
  img.src = url;
  el.classList.add('has-cover');
}

// Artists' own photos — the ones the text search shows — asked for only
// for the cards in view, a dozen names a call and one call at a time: the
// backend searches the ones it has not stored, and a library can have
// thousands of artists.
const libPhotoCache = new Map(); // name → URL, '' = none found
const libPhotoQueue = [];
let libPhotoBusy = false;
function libLoadPhoto(el) {
  const name = el.dataset.photo;
  if (libPhotoCache.has(name)) { libShowPhoto(el, libPhotoCache.get(name)); return; }
  libPhotoQueue.push(el);
  libPumpPhotos();
}
function libShowPhoto(el, url) {
  const img = el.querySelector('img');
  if (!url || !img) return;
  el.classList.add('has-cover', 'has-photo');
  img.onerror = () => { // an image that no longer loads: back to the album cover
    img.onerror = null;
    el.classList.remove('has-photo');
    if (el._libCover) img.src = el._libCover;
    else { img.removeAttribute('src'); el.classList.remove('has-cover'); }
  };
  img.src = url;
}
async function libPumpPhotos() {
  const api = window.pywebview?.api;
  if (libPhotoBusy || !api?.get_artist_photos) return;
  const batch = [];
  const names = new Set();
  while (names.size < 12 && libPhotoQueue.length) {
    const el = libPhotoQueue.pop();
    if (!el.isConnected) continue;
    const name = el.dataset.photo;
    if (libPhotoCache.has(name)) { libShowPhoto(el, libPhotoCache.get(name)); continue; }
    if (!libThumbNear(el)) { delete el.dataset.th; libIO?.observe(el); continue; } // ask again when it comes back
    batch.push(el);
    names.add(name);
  }
  if (!batch.length) return;
  libPhotoBusy = true;
  try {
    const urls = (await api.get_artist_photos([...names])) || {};
    names.forEach(n => libPhotoCache.set(n, urls[n] || ''));
    batch.forEach(el => { if (el.isConnected) libShowPhoto(el, libPhotoCache.get(el.dataset.photo)); });
  } catch (e) { /* the album cover stays */ }
  libPhotoBusy = false;
  libPumpPhotos();
}
// Covers are asked for in batches the backend reads side by side, the rows
// seen last first: scrolling fast through a long list should not leave the
// screen waiting on covers for rows already scrolled past.
function libThumbNear(el) {
  if (!el.isConnected) return false;
  const r = el.getBoundingClientRect();
  return r.bottom > -600 && r.top < innerHeight + 600;
}
async function libPumpThumbs() {
  const api = window.pywebview?.api;
  while (lib.thumbBusy < 2 && libThumbQueue.length) {
    const batch = [];
    while (batch.length < 24 && libThumbQueue.length) {
      const el = libThumbQueue.pop();
      if (libThumbNear(el)) batch.push(el);
      else if (el.isConnected) { delete el.dataset.th; libIO?.observe(el); } // ask again when it comes back
    }
    if (!batch.length) break;
    lib.thumbBusy++;
    (async () => {
      const size = Number(batch[0].dataset.size) || 0;
      const sameSize = batch.every(el => (Number(el.dataset.size) || 0) === size);
      try {
        let urls = {};
        if (sameSize && api?.get_track_thumbs) {
          urls = (await api.get_track_thumbs([...new Set(batch.map(el => el.dataset.p))], size)) || {};
        } else {
          await Promise.all(batch.map(async el => {
            urls[el.dataset.p] = await api?.get_track_thumb?.(el.dataset.p, Number(el.dataset.size) || 0);
          }));
        }
        batch.forEach(el => {
          const url = urls[el.dataset.p] || '';
          libThumbCache.set(`${el.dataset.p}\u0000${el.dataset.size || 0}`, url);
          if (el.isConnected) libShowThumb(el, url);
        });
      } catch (e) { /* a missing cover is fine */ }
      lib.thumbBusy--;
      libPumpThumbs();
    })();
  }
}

function libQueueItems(tracks) {
  return tracks.map(t => ({ path: t.path, title: t.title, artist: t.artist, album: t.album }));
}

function playFromLibrary(index, shuffle) {
  const tracks = lib.view.length ? lib.view : libPlayAll();
  const chosen = tracks[index];
  if (chosen && libTrackMissing(chosen)) {
    toastMgr.info('This song is not downloaded yet.');
    return;
  }
  // A playlist can list songs that are not downloaded: the queue skips
  // them, so the clicked song's place is counted among the playable ones.
  const playable = tracks.filter(t => !libTrackMissing(t));
  const at = Math.max(0, playable.indexOf(chosen));
  if (playable.length) startPlayback(libQueueItems(playable), at, shuffle, false);
}

// The toolbar's "Play all" / "Shuffle": every playable song on the page,
// skipping the ones not downloaded wherever they sit in the list.
function playAllFromLibrary(shuffle) {
  const tracks = lib.view.length ? lib.view : libPlayAll();
  const playable = tracks.filter(t => !libTrackMissing(t));
  if (playable.length) startPlayback(libQueueItems(playable), 0, !!shuffle, false);
  else if (tracks.length) toastMgr.info('None of these songs is downloaded yet.');
}

// An artist plays album by album, each in track order: their own albums
// whole, then the tracks they appear on elsewhere.
function libArtistTracks(key) {
  const { own, guest } = libAlbumsOf(libAll().albums, key);
  return [...own.flatMap(a => a.tracks), ...guest.flatMap(a => a.tracks.filter(t => libCredits(t, key)))];
}

function playLibraryGroup(kind, key, shuffle) {
  let tracks = [];
  if (kind === 'album') tracks = libAll().albums.find(a => a.key === key)?.tracks || [];
  else {
    tracks = libArtistTracks(key);
  }
  if (tracks.length) startPlayback(libQueueItems(tracks), 0, !!shuffle, false);
}

// ── Action menus ("…" and "+") ──────────────────────────────────────────────
// One small popover for every "…" button (a song, an album, a playlist, an
// artist, Now Playing) and for a song's "+" (straight to the playlist list).
// Items: { label, action, disabled, danger, keep } or '-' for a rule; an
// item with `submenu` (a function returning items) swaps the menu's content
// for that list, with a Back row on top.
let actionMenu = null;

function closeActionMenu() {
  if (!actionMenu) return;
  actionMenu.el.remove();
  document.removeEventListener('pointerdown', actionMenu.onDown, true);
  document.removeEventListener('keydown', actionMenu.onKey, true);
  window.removeEventListener('resize', closeActionMenu);
  actionMenu.anchor?.setAttribute('aria-expanded', 'false');
  const anchor = actionMenu.anchor;
  actionMenu = null;
  if (anchor && document.contains(anchor)) anchor.focus({ preventScroll: true });
}

function openActionMenu(anchor, items, title = '') {
  if (actionMenu?.anchor === anchor) { closeActionMenu(); return; }
  closeActionMenu();
  const el = document.createElement('div');
  el.className = 'action-menu';
  el.setAttribute('role', 'menu');
  // Inside Now Playing the menu takes the view's light ink.
  if (anchor.closest('#np-view')) el.classList.add('on-media');
  document.body.appendChild(el);
  const stack = [];
  const render = (list, heading) => {
    el.innerHTML = '';
    if (stack.length) {
      const back = document.createElement('button');
      back.type = 'button';
      back.className = 'am-item am-back';
      back.setAttribute('role', 'menuitem');
      back.textContent = heading || 'Back';
      back.addEventListener('click', () => { const prev = stack.pop(); render(prev.list, prev.heading); });
      el.appendChild(back);
      el.appendChild(Object.assign(document.createElement('div'), { className: 'am-sep' }));
    } else if (heading) {
      el.appendChild(Object.assign(document.createElement('div'), { className: 'am-title', textContent: heading }));
    }
    for (const item of list) {
      if (item === '-') { el.appendChild(Object.assign(document.createElement('div'), { className: 'am-sep' })); continue; }
      if (item.note) { el.appendChild(Object.assign(document.createElement('div'), { className: 'am-note', textContent: item.note })); continue; }
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'am-item' + (item.danger ? ' danger' : '') + (item.submenu ? ' has-sub' : '');
      b.setAttribute('role', 'menuitem');
      b.textContent = item.label;
      b.disabled = !!item.disabled;
      b.addEventListener('click', async () => {
        if (item.submenu) {
          // A submenu that asks the network (Share → Copy Link) says so.
          b.disabled = true;
          b.setAttribute('aria-busy', 'true');
          const waiting = setTimeout(() => { b.textContent = `${item.label} — looking up…`; }, 250);
          let sub;
          try { sub = await item.submenu(); }
          finally { clearTimeout(waiting); b.disabled = false; b.removeAttribute('aria-busy'); b.textContent = item.label; }
          if (!actionMenu || actionMenu.el !== el) return; // closed meanwhile
          stack.push({ list, heading });
          render(sub, item.label);
          return;
        }
        if (!item.keep) closeActionMenu();
        try { await item.action?.(); } catch (e) { toastMgr.error(e?.message || 'That did not work.'); }
      });
      el.appendChild(b);
    }
    place();
    el.querySelector('.am-item:not(:disabled)')?.focus({ preventScroll: true });
  };
  const place = () => {
    const r = anchor.getBoundingClientRect();
    const w = el.offsetWidth, h = el.offsetHeight;
    let left = Math.min(window.innerWidth - w - 8, Math.max(8, r.right - w));
    let top = r.bottom + 6;
    if (top + h > window.innerHeight - 8) top = Math.max(8, r.top - h - 6);
    el.style.left = `${left}px`;
    el.style.top = `${top}px`;
  };
  const onDown = e => { if (!el.contains(e.target) && !anchor.contains(e.target)) closeActionMenu(); };
  const onKey = e => {
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeActionMenu(); return; }
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      const all = [...el.querySelectorAll('.am-item:not(:disabled)')];
      const at = all.indexOf(document.activeElement);
      const next = all[(at + (e.key === 'ArrowDown' ? 1 : -1) + all.length) % all.length];
      next?.focus();
    }
  };
  actionMenu = { el, anchor, onDown, onKey };
  anchor.setAttribute('aria-expanded', 'true');
  document.addEventListener('pointerdown', onDown, true);
  document.addEventListener('keydown', onKey, true);
  window.addEventListener('resize', closeActionMenu);
  render(items, title);
}

// Play Next / Add to Queue: the songs go into the running queue, right after
// the song playing or at its end; with nothing playing they start it.
function queueLibraryTracks(tracks, next) {
  const items = libQueueItems(tracks.filter(t => !libTrackMissing(t)));
  if (!items.length) { toastMgr.info('None of these songs is downloaded yet.'); return; }
  if (playerEngine.kind === 'remote') {
    // The queue is the playing device's: the songs go there.
    connectSendRemote('queue_add', { items: items.map(q => ({ path: q.path, title: q.title || '', artist: q.artist || '', album: q.album || '', duration_s: q.duration_s || 0 })), next: !!next });
    const what = items.length === 1 ? `"${items[0].title}"` : plural(items.length, 'song');
    toastMgr.success(next ? `${what} plays next.` : `${what} added to the queue.`);
    return;
  }
  if (!playerQueue.length || playerIndex < 0) { startPlayback(items, 0, false, false); return; }
  const start = playerQueue.length;
  playerQueue.push(...items);
  const added = items.map((_, i) => start + i);
  if (next) playerOrder.splice(playerPos + 1, 0, ...added);
  else playerOrder.push(...added);
  playerRefreshNext();
  connectSchedule();
  if (!$('np-queue')?.classList.contains('hidden')) npRenderQueue();
  const what = items.length === 1 ? `"${items[0].title}"` : plural(items.length, 'song');
  toastMgr.success(next ? `${what} plays next.` : `${what} added to the queue.`);
}

// Up Next → a new or existing playlist: every song of the queue, in the
// order it plays (the backend drops repeats and files that are gone).
function playerQueueTracks() {
  return playerOrder.map(i => playerQueue[i]).filter(Boolean).map(q => {
    const t = libTrackFor(q) || {};
    return { path: q.path, title: q.title || t.title || '', artist: q.artist || t.artist || '',
      album: t.album || q.album || '', duration_s: t.duration_s || q.duration_s || 0, isrc: t.isrc || '' };
  }).filter(t => t.path);
}

async function npSaveQueueAsPlaylist(btn) {
  const tracks = playerQueueTracks();
  if (!tracks.length) { toastMgr.info('The queue is empty.'); return; }
  openActionMenu(btn, await playlistPickerItems(tracks), 'Save Queue to Playlist');
}

// The playlists a song can go into, for "+" and "Add to Playlist".
async function playlistPickerItems(tracks) {
  if (!lib.playlists.length) await refreshPlayerPlaylists();
  const others = lib.playlists.filter(p => !(lib.tab === 'playlists' && p.id === lib.playlist));
  const items = others.map(p => ({
    label: p.name,
    action: () => addTracksToPlayerPlaylist(p.id, tracks),
  }));
  if (!items.length) items.push({ note: 'No playlists yet.' });
  items.push('-', { label: 'New Playlist…', action: () => openPlayerPlaylistEditor('', tracks) });
  return items;
}

async function addTracksToPlayerPlaylist(id, tracks) {
  const playlist = lib.playlists.find(p => p.id === id);
  const res = await window.pywebview?.api?.add_player_playlist_tracks?.(id, tracks);
  if (!res || res.status !== 'ok') { toastMgr.error(res?.error || 'Could not add to the playlist.'); return; }
  await refreshPlayerPlaylists(lib.playlist || id);
  const what = tracks.length === 1 ? `"${tracks[0].title}"` : plural(tracks.length, 'song');
  toastMgr.success(`${what} added to ${playlist ? `"${playlist.name}"` : 'the playlist'}.`);
}

// A song as the library knows it (album, credits), from a queue entry.
function libTrackFor(item) {
  return lib.items.find(t => t.path === item?.path) || item || null;
}

function goToLibraryAlbum(track) {
  const t = libTrackFor(track);
  if (!t) return;
  const key = libAlbumKey(t);
  closeNowPlaying();
  switchView('player');
  if (libAll().albums.some(a => a.key === key)) openPlayerAlbum(key);
  else { $('player-search').value = t.album || t.title || ''; setPlayerTab('songs'); }
}

function revealLabel() {
  const os = navigator.userAgent || '';
  return /Mac/.test(os) ? 'Show in Finder' : /Win/.test(os) ? 'Show in Explorer' : 'Show in Folder';
}

async function revealLibraryFile(path) {
  const res = await window.pywebview?.api?.reveal_in_folder?.(path);
  if (!res?.ok) toastMgr.error(res?.error || 'Could not show the file.');
}

async function copyTrackText(t) {
  const text = [t.title, t.artist].filter(Boolean).join(' — ');
  try { await navigator.clipboard.writeText(text); toastMgr.success('Copied.'); }
  catch (e) { toastMgr.error('Could not copy.'); }
}

// Share: the song's name, or its link on a service it is on (looked up by
// ISRC, else by title and artist: api track_share_links).
const SHARE_PLATFORMS = { spotify: 'Spotify', appleMusic: 'Apple Music', tidal: 'TIDAL', qobuz: 'Qobuz',
  deezer: 'Deezer', amazonMusic: 'Amazon Music', youtubeMusic: 'YouTube Music', youtube: 'YouTube', soundcloud: 'SoundCloud' };

function shareMenuItems(t) {
  return [
    { label: 'Copy Name', action: () => copyTrackText(t) },
    { label: 'Copy Link', submenu: () => shareLinkItems(t) },
  ];
}

async function shareLinkItems(t) {
  const api = window.pywebview?.api;
  let res = null;
  try { res = await api?.track_share_links?.(t.title || '', t.artist || '', t.isrc || ''); } catch (e) { res = null; }
  const links = res?.status === 'ok' ? res.links : {};
  const items = Object.keys(SHARE_PLATFORMS).filter(k => links[k]).map(k => ({
    label: SHARE_PLATFORMS[k],
    action: async () => { if (await copyText(links[k])) toastMgr.success(`${SHARE_PLATFORMS[k]} link copied.`); else toastMgr.error('Could not copy.'); },
  }));
  return items.length ? items : [{ note: res?.error || 'This song was not found on any service.' }];
}

// What a song's "…" offers. ctx: { playlistIndex } for a row of the open
// playlist, { nowPlaying: true } in Now Playing.
function trackMenuItems(track, ctx = {}) {
  const t = libTrackFor(track);
  const playable = !libTrackMissing(t);
  const items = [
    { label: 'Play Next', disabled: !playable, action: () => queueLibraryTracks([t], true) },
    { label: 'Add to Queue', disabled: !playable, action: () => queueLibraryTracks([t], false) },
    { label: 'Add to Playlist', submenu: () => playlistPickerItems([t]) },
    '-',
  ];
  if (t.album) items.push({ label: 'Go to Album', action: () => goToLibraryAlbum(t) });
  const names = (t.artist_names && t.artist_names.length ? t.artist_names : artistNames(t.artist || '')).slice(0, 3);
  for (const name of names) items.push({ label: names.length > 1 ? `Go to ${name}` : 'Go to Artist', action: () => npGoArtist(name) });
  if (ctx.playlistIndex != null) {
    const i = Number(ctx.playlistIndex);
    items.push('-',
      { label: 'Move Up', disabled: i === 0 || lib.playlistFiltered, action: () => reorderPlayerPlaylistTrack(i, -1) },
      { label: 'Move Down', disabled: i >= lib.view.length - 1 || lib.playlistFiltered, action: () => reorderPlayerPlaylistTrack(i, 1) },
      { label: 'Remove from Playlist', danger: true, action: () => removePlayerPlaylistTrack(i) });
  }
  items.push('-');
  if (playable && window.pywebview?.api?.reveal_in_folder) items.push({ label: revealLabel(), action: () => revealLibraryFile(t.path) });
  items.push({ label: 'Share', submenu: () => shareMenuItems(t) });
  // Pin to Home: the song's genre and folder (they have no page of their
  // own to pin from).
  const pinItems = [];
  const genre = String(t.genre || '').split(/[;,/]/)[0].trim();
  const folder = playable ? homeFolderOf(t.path) : '';
  const pinChoice = (pin, what) => {
    const item = homePinMenuItem(pin);
    return { ...item, label: `${item.label.startsWith('Unpin') ? 'Unpin ' : ''}${what}: ${pin.title}` };
  };
  if (genre) pinItems.push(pinChoice({ kind: 'genre', id: homeGenreKey(genre), title: genre, subtitle: 'Genre', cover_path: t.path }, 'Genre'));
  if (folder) pinItems.push(pinChoice({ kind: 'folder', id: folder, title: folder.split(/[\\/]/).pop() || folder, subtitle: folder, cover_path: t.path }, 'Folder'));
  if (pinItems.length) items.push({ label: 'Pin to Home', submenu: () => pinItems });
  if (ctx.nowPlaying) items.push({ label: 'Playback Statistics', action: () => openPlayerStats() });
  return items;
}

// A group's "…": the album page, a playlist page, an artist page.
function groupMenuItems(kind, key) {
  if (kind === 'album') {
    const album = libAll().albums.find(a => a.key === key);
    const tracks = album?.tracks || [];
    const owners = album ? libAlbumArtistNames(album) : [];
    const items = [
      { label: 'Play Next', action: () => queueLibraryTracks(tracks, true) },
      { label: 'Add to Queue', action: () => queueLibraryTracks(tracks, false) },
      { label: 'Add to Playlist', submenu: () => playlistPickerItems(tracks) },
      '-',
    ];
    owners.slice(0, 3).forEach(name => items.push({ label: owners.length > 1 ? `Go to ${name}` : 'Go to Artist', action: () => npGoArtist(name) }));
    if (tracks[0] && window.pywebview?.api?.reveal_in_folder) items.push({ label: revealLabel(), action: () => revealLibraryFile(tracks[0].path) });
    if (album) items.push('-', homePinMenuItem({ kind: 'album', id: album.key, title: album.title, subtitle: album.artist, cover_path: tracks[0]?.path || '' }));
    return items;
  }
  if (kind === 'playlist') {
    const playlist = lib.playlists.find(p => p.id === key);
    const tracks = playlist?.tracks || [];
    return [
      { label: 'Play Next', action: () => queueLibraryTracks(tracks, true) },
      { label: 'Add to Queue', action: () => queueLibraryTracks(tracks, false) },
      { label: 'Add to Another Playlist', submenu: () => playlistPickerItems(tracks.filter(t => !libTrackMissing(t))) },
      '-',
      { label: 'Edit Playlist…', action: () => openPlayerPlaylistEditor(key) },
      ...(playlist ? [homePinMenuItem({ kind: 'playlist', id: key, title: playlist.name, subtitle: 'Playlist', cover_path: tracks.find(t => !libTrackMissing(t))?.path || '' })] : []),
      { label: 'Export M3U8…', action: () => exportPlayerPlaylist(key) },
      ...(playlist?.source_url ? [{ label: 'Check for New Songs', action: () => syncFollowedPlaylists([key]) }] : []),
      '-',
      { label: 'Delete Playlist…', danger: true, action: () => deletePlayerPlaylist(key) },
    ];
  }
  const tracks = libArtistTracks(key);
  const artist = libAll().artists.find(a => a.key === key);
  return [
    { label: 'Play Next', action: () => queueLibraryTracks(tracks, true) },
    { label: 'Add to Queue', action: () => queueLibraryTracks(tracks, false) },
    { label: 'Add to Playlist', submenu: () => playlistPickerItems(tracks) },
    '-',
    { label: 'All Songs', action: () => { $('player-search').value = artist?.name || ''; setPlayerTab('songs'); } },
    ...(artist ? [homePinMenuItem({ kind: 'artist', id: artist.name.toLowerCase(), title: artist.name, subtitle: 'Artist', cover_path: tracks[0]?.path || '' })] : []),
  ];
}

function markPlayingRow() {
  const list = $('player-list');
  if (!list) return;
  list.querySelectorAll('.pl-row.playing').forEach(r => r.classList.remove('playing'));
  const cur = playerQueue[playerIndex];
  if (!cur) return;
  list.querySelectorAll('.pl-row').forEach(r => { if (r.dataset.p === cur.path) r.classList.add('playing'); });
}

(function initPlayerList() {
  const list = $('player-list');
  if (!list) return;
  list.addEventListener('click', e => {
    const move = e.target.closest('[data-move-player-track]');
    if (move) {
      e.preventDefault();
      e.stopPropagation();
      const [index, delta] = move.dataset.movePlayerTrack.split(',');
      reorderPlayerPlaylistTrack(index, delta);
      return;
    }
    const add = e.target.closest('[data-add-player-track]');
    if (add) {
      e.preventDefault();
      e.stopPropagation();
      const track = lib.view[Number(add.dataset.addPlayerTrack)];
      if (track) playlistPickerItems([track]).then(items => openActionMenu(add, items, 'Add to Playlist'));
      return;
    }
    const menu = e.target.closest('[data-track-menu]');
    if (menu) {
      e.preventDefault();
      e.stopPropagation();
      const i = Number(menu.dataset.trackMenu);
      const track = lib.view[i];
      const inPlaylist = lib.tab === 'playlists' && !!lib.playlist;
      if (track) openActionMenu(menu, trackMenuItems(track, inPlaylist ? { playlistIndex: i } : {}));
      return;
    }
    const remove = e.target.closest('[data-remove-player-track]');
    if (remove) {
      e.preventDefault();
      e.stopPropagation();
      removePlayerPlaylistTrack(remove.dataset.removePlayerTrack);
      return;
    }
    const row = e.target.closest('.pl-row');
    if (row) playFromLibrary(Number(row.dataset.i));
  });
  const onGroupClick = e => {
    const songs = e.target.closest('[data-artist-songs]');
    if (songs) { $('player-search').value = songs.dataset.artistSongs; setPlayerTab('songs'); return; }
    const menu = e.target.closest('[data-group-menu],[data-top-menu]');
    if (menu) {
      e.preventDefault();
      e.stopPropagation();
      if (menu.dataset.topMenu != null) {
        const t = (lib.artistTop || [])[Number(menu.dataset.topMenu)];
        if (t) openActionMenu(menu, trackMenuItems(t));
      } else {
        openActionMenu(menu, groupMenuItems(menu.dataset.groupMenu, menu.dataset.groupKey));
      }
      return;
    }
    const t = e.target.closest('[data-artist-top],[data-play-album],[data-play-artist],[data-shuffle-album],[data-shuffle-artist],[data-artist-link],[data-player-playlist],[data-play-player-playlist],[data-shuffle-player-playlist],[data-edit-player-playlist],[data-delete-player-playlist],[data-export-player-playlist],[data-sync-player-playlist],.pl-card,.pl-latest');
    if (!t) return;
    const d = t.dataset;
    if (d.editPlayerPlaylist != null) { e.stopPropagation(); openPlayerPlaylistEditor(d.editPlayerPlaylist); return; }
    if (d.deletePlayerPlaylist != null) { e.stopPropagation(); deletePlayerPlaylist(d.deletePlayerPlaylist); return; }
    if (d.exportPlayerPlaylist != null) { e.stopPropagation(); exportPlayerPlaylist(d.exportPlayerPlaylist); return; }
    if (d.syncPlayerPlaylist != null) { e.stopPropagation(); syncFollowedPlaylists([d.syncPlayerPlaylist]); return; }
    if (d.playPlayerPlaylist != null) {
      e.stopPropagation();
      const playlist = lib.playlists.find(p => p.id === d.playPlayerPlaylist);
      const playable = (playlist?.tracks || []).filter(t => !libTrackMissing(t));
      if (playable.length) startPlayback(libQueueItems(playable), 0, false, false);
      else if (playlist) toastMgr.info('None of these songs is downloaded yet.');
      return;
    }
    if (d.shufflePlayerPlaylist != null) {
      e.stopPropagation();
      const playlist = lib.playlists.find(p => p.id === d.shufflePlayerPlaylist);
      const playable = (playlist?.tracks || []).filter(t => !libTrackMissing(t));
      if (playable.length) startPlayback(libQueueItems(playable), 0, true, false);
      else if (playlist) toastMgr.info('None of these songs is downloaded yet.');
      return;
    }
    if (d.artistTop != null) {
      const top = lib.artistTop || [];
      if (top.length) startPlayback(libQueueItems(top), Number(d.artistTop) || 0, false, false);
      return;
    }
    if (d.playerPlaylist != null) { openPlayerPlaylist(d.playerPlaylist); return; }
    if (d.playAlbum != null) { e.stopPropagation(); playLibraryGroup('album', d.playAlbum, false); return; }
    if (d.playArtist != null) { e.stopPropagation(); playLibraryGroup('artist', d.playArtist, false); return; }
    if (d.shuffleAlbum != null) { playLibraryGroup('album', d.shuffleAlbum, true); return; }
    if (d.shuffleArtist != null) { playLibraryGroup('artist', d.shuffleArtist, true); return; }
    if (d.artistLink != null) { openPlayerArtist(d.artistLink); return; }
    if (d.album != null) { openPlayerAlbum(d.album); return; }
    if (d.artist != null) openPlayerArtist(d.artist);
  };
  $('player-grid')?.addEventListener('click', onGroupClick);
  $('player-detail')?.addEventListener('click', onGroupClick);
  // Scrolling the music folds the page title's subtitle and the library bar
  // away, so the list gets their room; back at the top they return. Only
  // when there is enough to scroll: a list that barely overflows would
  // un-fold itself as soon as the extra room let it fit.
  $('player-scroll')?.addEventListener('scroll', e => {
    const box = e.currentTarget;
    const view = $('view-player');
    const top = box.scrollTop;
    if (top > 40 && box.scrollHeight - box.clientHeight > 160) view.classList.add('player-compact');
    else if (top < 8) view.classList.remove('player-compact');
  }, { passive: true });
  const audio = playerEngine;
  audio.addEventListener('play', () => $('player-list').classList.remove('paused'));
  audio.addEventListener('pause', () => $('player-list').classList.add('paused'));
})();

// Every file with a match, rows on screen or not.
function toggleAllLocal(cb) {
    for (const item of localScanData) {
        if (localHasMatch(item)) localList.picked.set(item.file_path, cb.checked);
    }
    for (const row of localList.rows.values()) {
        const box = row.querySelector('.local-cb');
        if (box && !box.disabled) box.checked = cb.checked;
    }
    updateLocalSelection();
}

// Counted from the results, since only the files near the top have a row.
function updateLocalSelection() {
    let checked = 0;
    let total = 0;
    for (const item of localScanData) {
        if (!localHasMatch(item) || localList.applied.has(item.file_path)) continue;
        total += 1;
        if (localIsPicked(item)) checked += 1;
    }

    const checkAll = $('check-all-local');
    if (checkAll) {
        checkAll.checked = total > 0 && checked === total;
        checkAll.indeterminate = checked > 0 && checked < total;
    }
    
    $('local-selected-count').textContent = `${checked} file(s) selected`;
    $('btn-apply-local').disabled = checked === 0;
    const convertBtn = $('btn-convert-local');
    if (convertBtn) convertBtn.disabled = checked === 0;
}

function applyLocalTags() {
    const selectedIdx = localPickedIndices();
    if (!selectedIdx.length) return;
    
    const itemsToApply = selectedIdx.map(i => {
        const entry = localScanData[i];
        return {
            file_path: entry.file_path,
            metadata: entry.candidates[0].metadata,
            backup: true // Always create .bak for safety
        };
    });
    
    setTaBtnState($('btn-apply-local'), 'loading');
    $('btn-apply-local').innerHTML = `Applying 0/${itemsToApply.length}...`;
    
    if (window.pywebview?.api) {
        window.pywebview.api.apply_local_tags(itemsToApply);
    } else {
        setTimeout(() => window.app_local_apply_finished({results: itemsToApply.map(i => ({success: true}))}), 2000);
    }
}

// Called per file during apply
window.app_local_apply_progress = function(payload) {
    const { done, total, last } = payload;
    $('btn-apply-local').innerHTML = `Applying ${done}/${total}...`;
    
    if (!last.success) {
        const name = last.file_path.split(/[\\/]/).pop();
        toastMgr.error(`Failed to tag ${name}: ${last.error}`);
    }
};

// Called when all applies are done
window.app_local_apply_finished = function(payload) {
    setTaBtnState($('btn-apply-local'), 'success');
    setTimeout(() => {
        setTaBtnState($('btn-apply-local'), 'default');
        $('btn-apply-local').innerHTML = `
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>
          Apply Selected Tags`;
    }, 2000);
    
    const successes = payload.results.filter(r => r.success).length;
    const errors = payload.results.length - successes;
    
    if (errors === 0) {
        toastMgr.success(`Done! ${successes} files successfully tagged and backed up.`);
    } else {
        toastMgr.warning(`Finished: ${successes} tagged, ${errors} failed.`);
    }
    
    // Automatically deselect checkboxes for successful ones so the user knows they are done
    payload.results.forEach((res) => {
        if (res.success && res.file_path) localList.applied.add(res.file_path);
    });
    for (const row of localList.rows.values()) {
        const cb = row.querySelector('.local-cb');
        if (cb && localList.applied.has(cb.dataset.filePath)) {
            cb.checked = false;
            cb.disabled = true;
        }
    }
    updateLocalSelection();
};

// Helper function to update artist separator field and row state
function updateArtistSeparatorState(firstArtistOnly) {
    const sepField = $('config-artist-separator');
    const row = $('config-artist-sep-row');
    const disabled = firstArtistOnly;

    if (sepField) {
        sepField.disabled = disabled;
    }
    if (row) {
        row.style.opacity = disabled ? '0.4' : '1';
        row.style.pointerEvents = disabled ? 'none' : 'auto';
    }
}

if ($('config-first-artist')) {
    $('config-first-artist').addEventListener('change', function() {
        updateArtistSeparatorState(this.checked);
    });
}
/* ──────────────────────────────────────────────────────────────────────────
   Following (subscriptions) — see core/subscriptions.py

   A subscription is a followed artist or playlist plus the set of releases
   (or tracks) already seen. "Check for new" reports, "Check & download" is
   the explicit second step — or a "Check every…" schedule, which checks on
   its own (core/subscription_scheduler.py) and downloads what is new with
   the download settings sent along with it.
   ────────────────────────────────────────────────────────────────────────── */

/* Minutes; 0 = manual. The backend refuses anything under 15 (see
   core/subscriptions.MIN_INTERVAL_MINUTES). */
const SUB_INTERVALS = [
  [0, 'Manual only'],
  [15, 'Every 15 min'],
  [30, 'Every 30 min'],
  [60, 'Every hour'],
  [180, 'Every 3 hours'],
  [360, 'Every 6 hours'],
  [720, 'Every 12 hours'],
  [1440, 'Every day'],
];

function subIntervalOptions(selected) {
  const minutes = Number(selected) || 0;
  const options = SUB_INTERVALS.some(([m]) => m === minutes)
    ? SUB_INTERVALS
    : [...SUB_INTERVALS, [minutes, `Every ${minutes} min`]];
  return options.map(([m, label]) =>
    `<option value="${m}"${m === minutes ? ' selected' : ''}>${label}</option>`
  ).join('');
}

function subFormatDate(ts) {
  if (!ts) return 'never';
  try {
    return new Date(ts * 1000).toLocaleString();
  } catch (e) {
    return 'unknown';
  }
}

async function loadSubscriptions() {
  const list = $('subscription-list');
  if (!list) return;
  const intervalSelect = $('subscription-interval');
  if (intervalSelect && !intervalSelect.options.length) {
    intervalSelect.innerHTML = subIntervalOptions(0);
  }
  if (!window.pywebview?.api?.get_subscriptions) {
    list.innerHTML = '<div class="s-label u-fs-11_5">Following is unavailable in this build.</div>';
    return;
  }
  try {
    renderSubscriptions(await window.pywebview.api.get_subscriptions());
  } catch (e) {
    list.innerHTML = '<div class="s-label u-fs-11_5 u-c-red">Unable to load subscriptions.</div>';
  }
}

function renderSubscriptions(subs) {
  const list = $('subscription-list');
  if (!list) return;

  if (subs?.error) {
    list.innerHTML = '<div class="s-label u-fs-11_5 u-c-red">Failed to load: ' + regEscapeHtml(subs.error) + '</div>';
    return;
  }
  if (!subs || !subs.length) {
    list.innerHTML = emptyState(
      'user',
      'Not following anything yet',
      'Paste an artist or playlist link above to be told — or have it downloaded — when something new appears.',
    );
    return;
  }

  list.innerHTML = subs.map(subRowHtml).join('');
  subWireImages(list);
  subFillMissingDetails(subs);
}

// A picture that no longer loads (an expired CDN link) shows the kind's
// icon instead of an empty frame.
function subWireImages(root) {
  root.querySelectorAll('.sub-art img').forEach(img => img.addEventListener('error', () => {
    const art = img.parentElement;
    if (!art) return;
    const playlist = art.classList.contains('is-playlist');
    art.classList.add('is-empty');
    art.innerHTML = playlist
      ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>'
      : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/></svg>';
  }, { once: true }));
}

const SUB_GROUP_LABELS = {
  'album,single': 'Albums & singles', album: 'Albums', single: 'Singles', all: 'Everything',
};

function subRelative(ts) {
  if (!ts) return 'never checked';
  const s = Math.round(Date.now() / 1000 - ts);
  if (s < 60) return 'checked just now';
  if (s < 3600) return `checked ${Math.round(s / 60)} min ago`;
  if (s < 86400) return `checked ${Math.round(s / 3600)} h ago`;
  return `checked ${Math.round(s / 86400)} d ago`;
}

// One followed artist or playlist: its picture, name and description, what
// has been seen, and its controls. Artists get a round photo, playlists a
// square cover, as in the music apps.
function subRowHtml(s) {
  const id = regEscapeHtml(s.id);
  const isPlaylist = s.kind === 'playlist';
  const target = isPlaylist ? 'playlist' : 'artist';
  const name = regEscapeHtml(s.name || (isPlaylist ? 'Playlist' : 'Artist'));
  const icon = isPlaylist
    ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>'
    : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/></svg>';
  const art = s.image_url
    ? `<img src="${regEscapeHtml(s.image_url)}" alt="" loading="lazy" referrerpolicy="no-referrer">`
    : icon;
  const facts = [
    s.subtitle ? regEscapeHtml(s.subtitle) : '',
    isPlaylist ? '' : regEscapeHtml(SUB_GROUP_LABELS[s.include_groups] || s.include_groups),
  ].filter(Boolean).join(' · ');
  const seen = `${s.seen_count} ${isPlaylist ? 'track' : 'release'}${s.seen_count === 1 ? '' : 's'} seen`;
  const next = s.next_check_at && s.enabled
    ? ` · next ${regEscapeHtml(subFormatDate(s.next_check_at))}`
    : '';
  const status = !s.enabled
    ? '<span class="sub-state paused">Paused</span>'
    : (s.interval_minutes ? '<span class="sub-state auto">Auto</span>' : '');
  const err = s.last_error
    ? `<div class="sub-error" title="${regEscapeHtml(s.last_error)}">Last check failed: ${regEscapeHtml(s.last_error)}</div>`
    : '';
  return `
    <div class="sub-row${s.enabled ? '' : ' is-paused'}" data-sub-id="${id}">
      <a class="sub-art ${isPlaylist ? 'is-playlist' : 'is-artist'}${s.image_url ? '' : ' is-empty'}"
         href="${regEscapeHtml(s.url)}" target="_blank" rel="noopener" title="Open on Spotify">${art}</a>
      <div class="sub-main">
        <div class="sub-name-row">
          <span class="sub-kind">${isPlaylist ? 'Playlist' : 'Artist'}</span>
          <span class="sub-name" title="${regEscapeHtml(s.url)}">${name}</span>
          ${status}
        </div>
        ${facts ? `<div class="sub-facts">${facts}</div>` : ''}
        <div class="sub-meta">${seen} · ${regEscapeHtml(subRelative(s.last_checked_at))}${next}</div>
        ${err}
      </div>
      <div class="sub-actions">
        <select title="Check this ${target} automatically and download what is new"
                aria-label="Schedule for ${name}"
                data-onchange="setSubscriptionInterval('${id}', this.value)">
          ${subIntervalOptions(s.interval_minutes)}
        </select>
        <button class="act-btn secondary sub-btn" type="button"
                data-onclick="toggleSubscription('${id}', ${s.enabled ? 'false' : 'true'})"
                title="${s.enabled ? `Stop checking this ${target} without forgetting what has been seen` : `Resume checking this ${target}`}">
          ${s.enabled ? 'Pause' : 'Resume'}
        </button>
        <button class="act-btn secondary sub-btn" type="button"
                data-onclick="resetSubscription('${id}')"
                title="Forget what this subscription has seen, so the whole back catalogue counts as new again">
          Reset
        </button>
        <button class="act-btn secondary sub-btn sub-unfollow" type="button"
                data-onclick="removeSubscription('${id}')" title="Stop following this ${target}">
          Unfollow
        </button>
      </div>
    </div>`;
}

// Rows followed before pictures were stored get them now, one at a time in
// the background, each row redrawn as its details arrive. Asked once per id
// per page load, so a link Spotify cannot describe is not asked forever.
const subDetailsAsked = new Set();
async function subFillMissingDetails(subs) {
  if (!window.pywebview?.api?.refresh_subscription_details) return;
  for (const s of subs || []) {
    if (s.image_url || subDetailsAsked.has(s.id)) continue;
    subDetailsAsked.add(s.id);
    try {
      const res = await window.pywebview.api.refresh_subscription_details(s.id);
      if (!res?.ok || !res.subscription) continue;
      const row = document.querySelector(`.sub-row[data-sub-id="${CSS.escape(s.id)}"]`);
      if (row) {
        row.outerHTML = subRowHtml(res.subscription);
        const fresh = document.querySelector(`.sub-row[data-sub-id="${CSS.escape(s.id)}"]`);
        if (fresh) subWireImages(fresh);
      }
    } catch (e) { /* the row stays as it is */ }
  }
}

function parseSubscriptionUrlList(raw) {
  // Split on newlines and commas; trim whitespace and discard empty/duplicate entries.
  const values = String(raw || '').split(/[\n,]+/).map(value => value.trim()).filter(Boolean);
  return [...new Set(values)];
}

async function addSubscriptionsBulk() {
  const input = $('subscription-bulk-input');
  const urls = parseSubscriptionUrlList(input?.value);
  if (!urls.length) {
    showToast('Paste at least one Spotify artist URL.', 'error');
    return;
  }

  const invalid = urls.filter(url =>
    !/^https?:\/\/open\.spotify\.com\/(?:intl-[a-z-]+\/)?artist\/[A-Za-z0-9]+(?:[/?#].*)?$/i.test(url)
  );
  if (invalid.length) {
    showToast('Only Spotify artist URLs are accepted in bulk mode. Check the pasted list.', 'error');
    return;
  }
  if (!(await askConfirm(`Follow ${urls.length} artist(s)?${$('subscription-bulk-backfill')?.checked ? '\nTheir seen history will be reset so existing releases count as new.' : ''}`, { title: 'Follow artists', ok: 'Follow' }))) return;

  const interval = Number($('subscription-interval')?.value || 0);
  const groups = $('subscription-groups')?.value || 'album,single';
  const backfill = !!$('subscription-bulk-backfill')?.checked;
  const results = { added: 0, reset: 0, failed: [] };

  // Sequential calls avoid flooding Spotify/API and make partial failures clear.
  for (const url of urls) {
    try {
      const res = await window.pywebview.api.add_subscription(
        url, '', groups, '', interval, buildConfig()
      );
      if (!res?.ok) {
        results.failed.push({ url, error: res?.error || 'Could not follow URL.' });
        continue;
      }
      results.added++;
      if (backfill) {
        const reset = await window.pywebview.api.reset_subscription(res.subscription.id);
        if (!reset?.ok) {
          results.failed.push({ url, error: reset?.error || 'Followed, but could not reset seen history.' });
        } else {
          results.reset++;
        }
      }
    } catch (e) {
      results.failed.push({ url, error: e?.message || 'Request failed.' });
    }
  }

  input.value = results.failed.map(({ url }) => url).join('\n');
  loadSubscriptions();
  if (results.failed.length) {
    showToast(`Followed ${results.added}; reset ${results.reset}; ${results.failed.length} failed. See the Logs or retry failed URLs.`, 'error');
    results.failed.forEach(({ url, error }) => {
      logMessage(`Could not complete bulk follow for ${url}: ${error}`, 'error-quiet');
    });
  } else {
    showToast(`Followed ${results.added} artist(s)${backfill ? ' with unseen back catalogues' : ''}.`);
  }
}

async function addSubscription() {
  const input = $('subscription-url-input');
  const url = (input?.value || '').trim();
  if (!url) {
    showToast('Paste an artist or playlist link first.', 'error');
    return;
  }
  const interval = Number($('subscription-interval')?.value || 0);
  try {
    const res = await window.pywebview.api.add_subscription(
      url, '', $('subscription-groups')?.value || 'album,single', '',
      interval, buildConfig()
    );
    if (res?.ok) {
      input.value = '';
      const what = res.subscription?.kind === 'playlist' ? 'that playlist' : 'that artist';
      showToast('Now following ' + (res.subscription?.name || what) +
        (interval ? ' — checked automatically.' : '.'));
      loadSubscriptions();
    } else {
      showToast(res?.error || 'Could not follow that link.', 'error');
    }
  } catch (e) {
    showToast('Could not follow that link.', 'error');
  }
}

async function setSubscriptionInterval(id, minutes) {
  try {
    const res = await window.pywebview.api.set_subscription_interval(
      id, Number(minutes) || 0, buildConfig()
    );
    if (res?.ok) {
      showToast(Number(minutes)
        ? 'Scheduled — new items will be downloaded with your current settings.'
        : 'Back to manual checks.');
    } else {
      showToast(res?.error || 'Could not change the schedule.', 'error');
    }
  } catch (e) {
    showToast('Could not change the schedule.', 'error');
  }
  loadSubscriptions();
}

async function removeSubscription(id) {
  try {
    await window.pywebview.api.remove_subscription(id);
    loadSubscriptions();
  } catch (e) {
    showToast('Could not unfollow.', 'error');
  }
}

async function toggleSubscription(id, enabled) {
  try {
    await window.pywebview.api.set_subscription_enabled(id, enabled);
    loadSubscriptions();
  } catch (e) {
    showToast('Could not update that subscription.', 'error');
  }
}

async function resetSubscription(id) {
  if (!(await askConfirm('Forget what this subscription has seen? The next check with downloading on will treat the whole back catalogue as new.', { title: 'Reset subscription', ok: 'Reset', danger: true }))) return;
  try {
    await window.pywebview.api.reset_subscription(id);
    showToast('Reset. The next check with downloading on will treat the whole back catalogue as new.');
    loadSubscriptions();
  } catch (e) {
    showToast('Could not reset that subscription.', 'error');
  }
}

async function checkSubscriptions(download) {
  const results = $('subscription-results');
  if (results) {
    results.innerHTML = '<div class="s-label u-fs-11_5">Checking…</div>';
    $('subscription-results-section').style.display = '';
  }
  try {
    // The settings go along only when downloading: they are what the
    // downloads use, and they are saved for scheduled checks too.
    await window.pywebview.api.check_subscriptions(
      !!download, download ? buildConfig() : null
    );
  } catch (e) {
    showToast('Could not start the check.', 'error');
  }
}

/* Pushed by SubscriptionsMixin._check_subscriptions_thread when it finishes. */
function subscriptionsChecked(payload) {
  const results = $('subscription-results');
  const section = $('subscription-results-section');
  if (!results || !section) return;
  section.style.display = '';

  if (payload?.error) {
    results.innerHTML = '<div class="s-label u-fs-11_5 u-c-red">' + regEscapeHtml(payload.error) + '</div>';
    return;
  }

  const rows = payload?.results || [];
  if (!rows.length) {
    results.innerHTML = '<div class="s-label u-fs-11_5">Nothing is being followed yet.</div>';
    return;
  }

  results.innerHTML = rows.map((r) => {
    const artist = regEscapeHtml(r.artist || r.url);
    const isPlaylist = r.kind === 'playlist';
    const unit = isPlaylist ? 'track' : 'release';
    if (r.error) {
      return `<div class="sort-item reg-item"><div class="reg-item-main">
        <span class="reg-url">${artist}</span>
        <div class="s-label u-fs-11 u-c-red">${regEscapeHtml(r.error)}</div>
      </div></div>`;
    }
    if (r.watermarked) {
      return `<div class="sort-item reg-item"><div class="reg-item-main">
        <span class="reg-url">${artist}</span>
        <div class="s-label u-fs-11">First check — ${r.total_releases} ${unit}(s) recorded as seen. Only ${isPlaylist ? 'tracks added later' : 'later releases'} will be fetched.</div>
      </div></div>`;
    }
    const releases = (r.new_releases || []).map((rel) =>
      isPlaylist
        ? `<div class="s-label u-fs-11">· ${regEscapeHtml(rel.title)}</div>`
        : `<div class="s-label u-fs-11">· ${regEscapeHtml(rel.title)}${rel.year ? ' (' + regEscapeHtml(rel.year) + ')' : ''} [${regEscapeHtml(rel.type)}]</div>`
    ).join('');
    return `<div class="sort-item reg-item"><div class="reg-item-main">
      <span class="reg-url">${artist}</span>
      <div class="s-label u-fs-11">${(r.new_releases || []).length} new ${unit}(s)${payload?.scheduled ? ' · scheduled check' : ''}</div>
      ${releases}
    </div></div>`;
  }).join('');

  loadSubscriptions();
}

/* ──────────────────────────────────────────────────────────────────────────
   Extension health — see core/provider_stats.py and api_mixins/extension_health.py
   ────────────────────────────────────────────────────────────────────────── */

function healthRate(rate) {
  return rate === null || rate === undefined ? '—' : Math.round(rate * 100) + '%';
}

/** The tone (see [data-tone] in css/utilities.css) of an extension's success rate. */
function healthTone(row) {
  if (!row.attempts) return 'muted';
  if (row.success_rate >= 0.9) return 'ok';
  if (row.success_rate >= 0.5) return 'warn';
  return 'error';
}

// ── Storage & memory (api_mixins/storage.py) ─────────────────────────────
async function loadStorageUsage() {
  const list = $('storage-list');
  const api = window.pywebview?.api;
  if (!list || !api?.storage_usage) return;
  let data;
  try { data = await api.storage_usage(); } catch (e) { data = { status: 'error', error: String(e) }; }
  if (!data || data.status !== 'ok') {
    list.innerHTML = '<div class="s-row"><span class="s-label u-c-red">' + regEscapeHtml(data?.error || 'Could not read storage usage.') + '</span></div>';
    return;
  }
  const mem = data.memory || {};
  $('storage-mem-total').textContent = formatSize(mem.total_bytes) || '—';
  const parts = (mem.processes || []).map((p) => `${p.name}${p.count > 1 ? ' ×' + p.count : ''} ${formatSize(p.bytes)}`);
  $('storage-mem-detail').textContent = parts.length ? parts.join(' · ') : 'Mornye and the helpers it started';
  list.innerHTML = (data.disk || []).map((it) => {
    const size = formatSize(it.bytes) || '0 B';
    const btn = it.compactable
      ? `<button type="button" class="act-btn secondary" data-onclick="compactStorage()">Compact</button>`
      : it.clearable && it.bytes
        ? `<button type="button" class="act-btn secondary" data-onclick="clearStorage('${regEscapeHtml(it.id)}')">Clear</button>`
        : '';
    return `<div class="s-row storage-row">
        <span class="s-label">${regEscapeHtml(it.label)}<small>${regEscapeHtml(it.description)}</small></span>
        <div class="storage-actions"><span class="storage-size">${size}</span>${btn}</div>
      </div>`;
  }).join('');
  $('storage-summary').textContent =
    `${formatSize(data.disk_total_bytes) || '0 B'} on disk in ${data.data_dir}; ${formatSize(data.clearable_bytes) || 'nothing'} can be cleared.`;
  const all = $('storage-clear-all');
  if (all) all.disabled = !data.clearable_bytes;
}

async function clearStorage(id) {
  const api = window.pywebview?.api;
  if (!api?.storage_clear) return;
  const what = id === 'all' ? 'every cache' : 'this cache';
  if (!(await askConfirm(`Clear ${what}? Mornye fetches or rebuilds it again when needed.`, { title: 'Clear cache', ok: 'Clear', danger: true }))) return;
  const r = await api.storage_clear(id);
  if (r?.status === 'ok') showToast(`Freed ${formatSize(r.freed_bytes + (r.compact?.freed_bytes || 0)) || '0 B'}.`);
  else showToast(r?.error || 'Could not clear the cache.', 'error');
  loadStorageUsage();
}

async function compactStorage() {
  const api = window.pywebview?.api;
  if (!api?.storage_compact) return;
  const r = await api.storage_compact();
  if (r?.status === 'ok') showToast(`Database compacted: ${formatSize(r.freed_bytes) || '0 B'} freed.`);
  else showToast(r?.error || 'Could not compact the database.', 'error');
  loadStorageUsage();
}

async function loadExtensionHealth() {
  const list = $('ext-health-list');
  if (!list) return;
  if (!window.pywebview?.api?.get_extension_health) {
    list.innerHTML = '<div class="s-label u-fs-11_5">Extension health is unavailable in this build.</div>';
    return;
  }
  try {
    renderExtensionHealth(await window.pywebview.api.get_extension_health());
  } catch (e) {
    list.innerHTML = '<div class="s-label u-fs-11_5 u-c-red">Unable to load extension health.</div>';
  }
}

function renderExtensionHealth(data) {
  const list = $('ext-health-list');
  if (!list) return;

  if (data?.error) {
    list.innerHTML = '<div class="s-label u-fs-11_5 u-c-red">' + regEscapeHtml(data.error) + '</div>';
    return;
  }
  const rows = data?.providers || [];
  if (!rows.length) {
    list.innerHTML = '<div class="s-label u-fs-11_5">No extensions installed, and nothing has been downloaded yet.</div>';
    return;
  }

  list.innerHTML = rows.map((r) => {
    const version = r.version ? ' · v' + regEscapeHtml(r.version) : '';
    const detail = r.attempts
      ? `${r.successes}/${r.attempts} succeeded · ~${r.avg_duration_s}s each`
      : 'never tried';
    const err = r.last_error
      ? `<div class="s-label u-fs-11 u-c-red">Last error: ${regEscapeHtml(r.last_error)}</div>`
      : '';
    return `
      <div class="sort-item reg-item">
        <div class="reg-item-main">
          <span class="reg-url">${regEscapeHtml(r.provider)}${version}</span>
          <div class="s-label u-fs-11">${regEscapeHtml(detail)}</div>
          ${err}
        </div>
        <span class="u-fw-600 u-fs-13" data-tone="${healthTone(r)}">${healthRate(r.success_rate)}</span>
      </div>`;
  }).join('');
}

async function resetExtensionHealth() {
  if (!(await askConfirm('Clear the recorded provider statistics?', { title: 'Clear statistics', ok: 'Clear', danger: true }))) return;
  try {
    await window.pywebview.api.reset_extension_health();
    showToast('Extension statistics cleared.');
    loadExtensionHealth();
  } catch (e) {
    showToast('Could not clear the statistics.', 'error');
  }
}

// ── Extension settings (api_mixins/extension_settings.py) ─────────────────
//
// One collapsible card per JS extension that declares `settings` in its
// manifest. Fields are rendered from the manifest's own types; the backend
// validates again on save, so this is presentation only.

// ── Self-hosted endpoints ─────────────────────────────────────────────────
//
// A shortcut to tidal-web's, qobuz-web's and amazon's own "customEndpoint"
// settings (plus qobuz-web's API key and amazon's session token): the same
// values the extension cards below edit.

const CUSTOM_ENDPOINTS = [
  { service: 'tidal', ext: 'tidal-web', input: 'ce-tidal', note: 'ce-tidal-note', key: 'customEndpoint' },
  { service: 'qobuz', ext: 'qobuz-web', input: 'ce-qobuz', note: 'ce-qobuz-note', key: 'customEndpoint',
    secret: { input: 'ce-qobuz-key', key: 'customEndpointApiKey' } },
  { service: 'amazon', ext: 'amazon', input: 'ce-amazon', note: 'ce-amazon-note', key: 'customEndpoint',
    secret: { input: 'ce-amazon-key', key: 'customEndpointApiKey' } },
];

function customEndpointSupport(info, key) {
  if (!info || info.error) return 'not installed';
  if (!(info.settings || []).some((s) => s.key === key)) return `v${info.version} — update the extension to use this`;
  return '';
}

async function loadCustomEndpoints() {
  const api = window.pywebview?.api;
  if (!api?.get_extension_settings || !$('custom-endpoints')) return;
  for (const item of CUSTOM_ENDPOINTS) {
    let info = null;
    try { info = await api.get_extension_settings(item.ext); } catch (e) { info = null; }
    const problem = customEndpointSupport(info, item.key);
    const input = $(item.input);
    input.disabled = !!problem;
    input.value = problem ? '' : (info.values?.[item.key] ?? '');
    $(item.note).textContent = problem ? `${item.ext}: ${problem}` : `${item.ext} v${info.version}`;
    if (item.secret) {
      const secret = $(item.secret.input);
      const supported = !problem && (info.settings || []).some((s) => s.key === item.secret.key);
      secret.disabled = !supported;
      secret.value = '';
      secret.placeholder = (info?.secrets_set || []).includes(item.secret.key) ? 'Saved — leave empty to keep it' : 'Not set';
    }
  }
}

// The Test buttons: api_mixins/extension_settings.py test_custom_endpoint,
// which checks the server's status route and one call that needs its
// upstream account. An empty field tests the saved value.
async function testCustomEndpoint(service) {
  const api = window.pywebview?.api;
  const item = CUSTOM_ENDPOINTS.find((e) => e.service === service);
  const out = $(`ce-${service}-result`);
  const btn = $(`ce-${service}-test`);
  if (!item || !out) return;
  if (!api?.test_custom_endpoint) {
    out.hidden = false;
    out.className = 'ce-result bad';
    out.textContent = 'Testing endpoints is unavailable in this build.';
    return;
  }
  const url = $(item.input).value.trim();
  const key = item.secret ? $(item.secret.input).value.trim() : '';
  btn.disabled = true;
  out.hidden = false;
  out.className = 'ce-result';
  out.textContent = 'Testing…';
  try {
    const res = await api.test_custom_endpoint(service, url, key);
    out.className = `ce-result ${res?.ok ? 'good' : 'bad'}`;
    out.textContent = res?.message || (res?.ok ? 'It works.' : 'The test failed.');
  } catch (e) {
    out.className = 'ce-result bad';
    out.textContent = 'The test could not run.';
  } finally {
    btn.disabled = false;
  }
}

async function saveCustomEndpoints() {
  const api = window.pywebview?.api;
  if (!api?.save_extension_settings) return;
  let saved = 0;
  for (const item of CUSTOM_ENDPOINTS) {
    const input = $(item.input);
    if (input.disabled) continue;
    const value = input.value.trim();
    if (value && !/^https?:\/\/[^\/?#\s]+/i.test(value)) {
      showToast(`${item.ext}: the endpoint must start with http:// or https://`, 'error');
      return;
    }
    const values = { [item.key]: value };
    if (item.secret && !$(item.secret.input).disabled) values[item.secret.key] = $(item.secret.input).value.trim();
    try {
      const res = await api.save_extension_settings(item.ext, values);
      if (res?.error) { showToast(`${item.ext}: ${res.error}`, 'error'); return; }
      saved++;
    } catch (e) {
      showToast(`Could not save the ${item.ext} endpoint.`, 'error');
      return;
    }
  }
  showToast(saved ? 'Endpoints saved.' : 'No extension supports custom endpoints yet.', saved ? 'success' : 'error');
  loadExtensionSettings();
}

async function loadExtensionSettings() {
  loadCustomEndpoints();
  const list = $('ext-settings-list');
  if (!list) return;
  if (!window.pywebview?.api?.list_extension_settings) {
    list.innerHTML = '<div class="s-label u-fs-11_5">Extension settings are unavailable in this build.</div>';
    return;
  }
  try {
    renderExtensionSettings(await window.pywebview.api.list_extension_settings());
  } catch (e) {
    list.innerHTML = '<div class="s-label u-fs-11_5 u-c-red">Unable to load extension settings.</div>';
  }
}

function extSettingFieldHtml(ext, entry, cardIdx) {
  const id = `ext-set-${cardIdx}-${entry.key}`.replace(/[^A-Za-z0-9_-]/g, '_');
  const attrs = `id="${id}" data-key="${regEscapeHtml(entry.key)}" data-type="${regEscapeHtml(entry.type)}"`;
  const value = ext.values?.[entry.key];
  const desc = entry.description
    ? `<small>${regEscapeHtml(entry.description)}</small>`
    : '';
  const label = `<span class="s-label">${regEscapeHtml(entry.label)}${desc}</span>`;

  if (entry.type === 'button') {
    return `<div class="s-row">${label}
      <button class="act-btn secondary u-p-6-14 u-fs-12" type="button"
        data-onclick="runExtensionSettingAction('${regEscapeHtml(ext.name)}','${regEscapeHtml(entry.key)}')">${regEscapeHtml(entry.label)}</button>
    </div>`;
  }
  if (entry.type === 'boolean') {
    return `<div class="s-row">${label}
      <label class="toggle"><input type="checkbox" ${attrs} ${value ? 'checked' : ''}><span class="toggle-sl"></span></label>
    </div>`;
  }
  if (entry.type === 'select') {
    const opts = (entry.options || []).map((o) =>
      `<option value="${regEscapeHtml(o.value)}" ${String(o.value) === String(value) ? 'selected' : ''}>${regEscapeHtml(o.label)}</option>`
    ).join('');
    return `<div class="s-row">${label}<select ${attrs}>${opts}</select></div>`;
  }
  if (entry.secret) {
    const isSet = (ext.secrets_set || []).includes(entry.key);
    return `<div class="s-row">${label}
      <input type="password" class="full" autocomplete="off" ${attrs}
        placeholder="${isSet ? 'Saved — leave empty to keep it' : 'Not set'}" style="flex:1;min-width:200px;max-width:320px;">
    </div>`;
  }
  // Free text, optionally with suggestions (e.g. a storefront list).
  const listId = entry.options?.length ? `${id}-suggestions` : '';
  const suggestions = listId
    ? `<datalist id="${listId}">${entry.options.map((o) => `<option value="${regEscapeHtml(o.value)}">`).join('')}</datalist>`
    : '';
  const type = entry.type === 'number' ? 'number' : 'text';
  return `<div class="s-row">${label}
    <input type="${type}" class="full" ${attrs} ${listId ? `list="${listId}"` : ''}
      value="${regEscapeHtml(value ?? '')}" placeholder="${regEscapeHtml(entry.default ?? '')}" style="flex:1;min-width:200px;max-width:320px;">
    ${suggestions}
  </div>`;
}

// "Allow local network access" (extensions/local_network.py). Extensions
// are kept off private addresses; this lets one reach the server typed into
// its own settings — a Jellyfin on 192.168.x.x — and nothing else on the LAN.
// Shown only where a text setting holds a URL: elsewhere it would open nothing.
function extLocalNetworkRowHtml(ext) {
  const hosts = ext.local_network_hosts || [];
  if (!hosts.length && !ext.local_network) return '';
  const what = hosts.length
    ? `Lets it reach ${hosts.map(regEscapeHtml).join(', ')} even on your home network. Nothing else on the local network is opened.`
    : 'No server address is saved yet.';
  return `<div class="s-row">
    <span class="s-label">Allow local network access<small>${what}</small></span>
    <label class="toggle"><input type="checkbox" ${ext.local_network ? 'checked' : ''}
      data-onchange="setExtensionLocalNetwork('${regEscapeHtml(ext.name)}', this)"><span class="toggle-sl"></span></label>
  </div>`;
}

async function setExtensionLocalNetwork(name, input) {
  const api = window.pywebview?.api;
  if (!api?.set_extension_local_network) return;
  const wanted = !!input.checked;
  try {
    const res = await api.set_extension_local_network(name, wanted);
    if (res?.error) throw new Error(res.error);
    showToast(wanted
      ? `${res.display_name || name} may now reach ${(res.local_network_hosts || []).join(', ') || 'its server'}.`
      : `${res.display_name || name} is kept off the local network again.`);
  } catch (e) {
    input.checked = !wanted;
    showToast(e?.message || 'Could not change local network access.', 'error');
  }
}

function renderExtensionSettings(data) {
  const list = $('ext-settings-list');
  if (!list) return;
  if (data?.error) {
    list.innerHTML = '<div class="s-label u-fs-11_5 u-c-red">' + regEscapeHtml(data.error) + '</div>';
    return;
  }
  const rows = data?.extensions || [];
  if (!rows.length) {
    list.innerHTML = '<div class="s-label u-fs-11_5">No installed extension has settings.</div>';
    return;
  }
  list.innerHTML = rows.map((ext, idx) => `
    <details class="sort-item reg-item" data-ext="${regEscapeHtml(ext.name)}" style="display:block;">
      <summary class="u-cursor-pointer u-justify-space-between u-items-center u-gap-8" style="display:flex;">
        <span class="reg-url">${regEscapeHtml(ext.display_name)}</span>
        <span class="s-label u-fs-11">${regEscapeHtml(ext.name)} · v${regEscapeHtml(ext.version || '?')}</span>
      </summary>
      <div class="u-mt-10">
        ${ext.settings.map((entry) => extSettingFieldHtml(ext, entry, idx)).join('')}
        ${extLocalNetworkRowHtml(ext)}
        <div class="s-row u-justify-flex-end u-gap-8">
          <button class="act-btn secondary u-p-6-14 u-fs-12" type="button"
            data-onclick="resetExtensionSettings('${regEscapeHtml(ext.name)}')">Reset to defaults</button>
          <button class="act-btn primary u-p-6-14 u-fs-12" type="button"
            data-onclick="saveExtensionSettings('${regEscapeHtml(ext.name)}', this)">Save</button>
        </div>
      </div>
    </details>`).join('');
}

function collectExtensionSettings(card) {
  const values = {};
  card.querySelectorAll('[data-key][data-type]').forEach((el) => {
    const key = el.dataset.key;
    const type = el.dataset.type;
    if (type === 'button') return;
    if (type === 'boolean') values[key] = !!el.checked;
    else values[key] = el.value;
  });
  return values;
}

async function saveExtensionSettings(name, btn) {
  const card = btn.closest('details[data-ext]');
  if (!card) return;
  try {
    const res = await window.pywebview.api.save_extension_settings(name, collectExtensionSettings(card));
    if (res?.error) {
      showToast(res.error, 'error');
      return;
    }
    showToast(`Settings saved for ${res.display_name || name}.`);
    await loadExtensionSettings();
  } catch (e) {
    showToast('Could not save the extension settings.', 'error');
  }
}

async function resetExtensionSettings(name) {
  if (!(await askConfirm(`Reset every setting of ${name} to its default?`, { title: 'Reset settings', ok: 'Reset', danger: true }))) return;
  try {
    const res = await window.pywebview.api.reset_extension_settings(name);
    if (res?.error) {
      showToast(res.error, 'error');
      return;
    }
    showToast(`Settings of ${res.display_name || name} reset.`);
    loadExtensionSettings();
  } catch (e) {
    showToast('Could not reset the extension settings.', 'error');
  }
}

async function runExtensionSettingAction(name, key) {
  try {
    const res = await window.pywebview.api.run_extension_setting_action(name, key);
    if (res?.error) {
      showToast(res.error, 'error');
      return;
    }
    showToast('Done.');
  } catch (e) {
    showToast('The extension action failed.', 'error');
  }
}

// ── Signed sessions (core/signed_session_status.py) ───────────────────────
//
// Read from disk, no network. The expiry is sent as seconds-from-now and
// counted down here, re-anchored on every load, so an open tab stays honest
// without polling the backend.

let signedSessionsTimer = null;

const SIGNED_STATE = {
  active: ['active', 'ok'],
  refresh_due: ['refresh due', 'warn'],
  expired: ['expired', 'error'],
  unverified: ['not verified', 'muted'],
  orphaned: ['orphaned', 'muted'],
};

function signedDuration(seconds) {
  // Rounded, not truncated: the first tick runs a few ms after the load, and
  // truncating 7499.99 turned a fresh "2h 05m" into "2h 04m" on sight.
  let s = Math.abs(Math.round(seconds));
  const d = Math.floor(s / 86400); s %= 86400;
  const h = Math.floor(s / 3600); s %= 3600;
  const m = Math.floor(s / 60);
  if (d) return `${d}d ${String(h).padStart(2, '0')}h`;
  if (h) return `${h}h ${String(m).padStart(2, '0')}m`;
  if (m) return `${m}m`;
  return `${s % 60}s`;
}

async function loadSignedSessions() {
  const list = $('signed-sessions-list');
  if (!list) return;
  if (!window.pywebview?.api?.get_signed_sessions) {
    list.innerHTML = '<div class="s-label u-fs-11_5">Signed sessions are unavailable in this build.</div>';
    return;
  }
  try {
    renderSignedSessions(await window.pywebview.api.get_signed_sessions());
  } catch (e) {
    list.innerHTML = '<div class="s-label u-fs-11_5 u-c-red">Unable to load signed sessions.</div>';
  }
}

function renderSignedSessions(data) {
  const list = $('signed-sessions-list');
  if (!list) return;
  if (signedSessionsTimer) { clearInterval(signedSessionsTimer); signedSessionsTimer = null; }

  if (data?.error) {
    list.innerHTML = '<div class="s-label u-fs-11_5 u-c-red">' + regEscapeHtml(data.error) + '</div>';
    return;
  }
  const rows = data?.sessions || [];
  if (!rows.length) {
    list.innerHTML = '<div class="s-label u-fs-11_5">No signed sessions stored yet — one is created the first time an extension needs it.</div>';
    return;
  }

  const loadedAt = Date.now();
  list.innerHTML = rows.map((r, i) => {
    const [stateLabel, tone] = SIGNED_STATE[r.state] || [r.state, 'muted'];
    const version = r.version ? ' · v' + regEscapeHtml(r.version) : '';
    const details = [];
    if (r.kind !== 'gateway' && r.extensions?.length) details.push('used by ' + r.extensions.join(', '));
    if (r.state === 'orphaned') details.push('no installed extension uses it');
    if (r.capabilities?.length) details.push(r.capabilities.join(', '));
    if (r.auth_paused_s) details.push('verification paused ' + signedDuration(r.auth_paused_s));
    const clearBtn = r.state === 'unverified' ? '' :
      `<button class="act-btn secondary u-p-4-10 u-fs-11" type="button" data-signed-clear="${regEscapeHtml(r.key)}" title="Drop this session so the next request verifies again.">Clear</button>`;
    return `
      <div class="sort-item reg-item">
        <div class="reg-item-main">
          <span class="reg-url">${regEscapeHtml(r.label)}${version}</span>
          <div class="s-label u-fs-11" data-signed-expiry="${i}"></div>
          ${details.length ? `<div class="s-label u-fs-11">${regEscapeHtml(details.join(' · '))}</div>` : ''}
        </div>
        <span class="u-items-center u-gap-8" style="display:flex;">
          <span class="u-fw-600 u-fs-12" data-tone="${tone}">${regEscapeHtml(stateLabel)}</span>
          ${clearBtn}
        </span>
      </div>`;
  }).join('');
  list.querySelectorAll('[data-signed-clear]').forEach(btn => {
    btn.addEventListener('click', () => clearSignedSession(btn.dataset.signedClear));
  });

  const tick = () => {
    const elapsed = (Date.now() - loadedAt) / 1000;
    let lapsed = false;
    rows.forEach((r, i) => {
      const el = list.querySelector(`[data-signed-expiry="${i}"]`);
      if (!el) return;
      if (r.expires_in_s === null || r.expires_in_s === undefined) {
        el.textContent = r.state === 'unverified' ? 'Verifies on first use' : 'No expiry recorded';
        return;
      }
      const left = r.expires_in_s - elapsed;
      if (r.expires_in_s > 0 && left <= 0) lapsed = true;
      let text = left > 0 ? 'Expires in ' + signedDuration(left) : 'Expired ' + signedDuration(left) + ' ago';
      if (r.state === 'active' && r.refresh_in_s !== null && r.refresh_in_s !== undefined) {
        const refresh = r.refresh_in_s - elapsed;
        if (r.refresh_in_s > 0 && refresh <= 0) lapsed = true;
        text += refresh > 0 ? ' · refresh in ' + signedDuration(refresh) : ' · refresh due';
      }
      el.textContent = text;
    });
    // A session crossed its expiry while the tab was open: the state badge is
    // now wrong, so re-read rather than recolour on the client's guess.
    if (lapsed) loadSignedSessions();
  };
  tick();
  signedSessionsTimer = setInterval(() => {
    if (!document.body.contains(list) || !$('tc-extensions')?.classList.contains('active')) {
      clearInterval(signedSessionsTimer); signedSessionsTimer = null;
      return;
    }
    tick();
  }, 1000);
}

async function clearSignedSession(key) {
  if (!(await askConfirm('Clear this signed session? The next request that needs it will verify again.', { title: 'Clear session', ok: 'Clear' }))) return;
  try {
    const res = await window.pywebview.api.clear_signed_session(key);
    if (res?.ok) showToast('Signed session cleared.');
    else showToast(res?.error || 'Could not clear the session.', 'error');
  } catch (e) {
    showToast('Could not clear the session.', 'error');
  }
  loadSignedSessions();
}

async function pruneSignedSessions() {
  if (!(await askConfirm('Delete the session files no installed extension uses?', { title: 'Delete unused sessions', ok: 'Delete', danger: true }))) return;
  try {
    const res = await window.pywebview.api.prune_signed_sessions();
    if (res?.ok) {
      const n = res.removed?.length || 0;
      showToast(n ? `Removed ${n} orphaned session${n === 1 ? '' : 's'}.` : 'No orphaned sessions to remove.');
    } else {
      showToast(res?.error || 'Could not remove orphaned sessions.', 'error');
    }
  } catch (e) {
    showToast('Could not remove orphaned sessions.', 'error');
  }
  loadSignedSessions();
}

// ── Your library, in numbers (core/stats.py) ──────────────────────────────
//
// The download log read back as a dashboard. Everything here is derived from
// downloads this install actually made, so the view is honest about what it
// cannot know: genre, release year and duration are only recorded for
// downloads made since the feature landed, and each section reports its own
// coverage rather than presenting a fifth of the library as all of it.

function statsPeriodArgs() {
  const value = $('stats-period')?.value || 'all';
  if (value === 'year') return [new Date().getFullYear(), null];
  if (value === '365') return [null, 365];
  if (value === '30') return [null, 30];
  return [null, null];
}

function statsBytes(value) {
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let size = Number(value) || 0;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) { size /= 1024; unit += 1; }
  return (unit === 0 ? size.toFixed(0) : size.toFixed(1)) + ' ' + units[unit];
}

function statsDuration(ms) {
  const seconds = Math.floor((Number(ms) || 0) / 1000);
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (days) return `${days}d ${hours}h`;
  if (hours) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

function statsTile(label, value, hint) {
  return `
    <div class="stat-tile">
      <div class="stat-tile-value">${regEscapeHtml(value)}</div>
      <div class="stat-tile-label">${regEscapeHtml(label)}</div>
      ${hint ? `<div class="stat-tile-hint">${regEscapeHtml(hint)}</div>` : ''}
    </div>`;
}

// One ranking as labelled bars. `rows` is [{label, value, caption}], drawn
// relative to the largest value so the shape of the list is readable at a
// glance rather than needing the numbers to be compared by eye.
function statsBars(rows, formatValue) {
  if (!rows.length) return '<div class="s-label u-fs-11_5">Nothing here yet.</div>';
  const peak = Math.max(...rows.map((row) => row.value)) || 1;
  return rows.map((row) => `
    <div class="stat-bar-row">
      <div class="stat-bar-label" title="${regEscapeHtml(row.label)}">${regEscapeHtml(row.label)}</div>
      <div class="stat-bar-track" style="--v:${Math.max(1, Math.round((row.value / peak) * 20)) / 20}"></div>
      <div class="stat-bar-value">${regEscapeHtml((formatValue || String)(row.value))}</div>
    </div>`).join('');
}

function statsSection(title, inner, note) {
  return `
    <div class="s-section">
      <div class="s-title">${regEscapeHtml(title)}</div>
      ${note ? `<div class="s-label u-fs-11 u-m-n2-0-10">${regEscapeHtml(note)}</div>` : ''}
      ${inner}
    </div>`;
}

let statsSmbScanActive = false;

function setStatsSmbStatus(message, isError = false) {
  const status = $('stats-smb-status');
  if (!status) return;
  status.textContent = message;
  status.classList.toggle('hidden', !message);
  status.classList.toggle('error', isError);
}

async function loadStatsSmbDestinations() {
  const select = $('stats-smb-destination');
  const button = $('stats-smb-scan');
  if (!select || !button) return;
  const previous = select.value;
  if (!window.pywebview?.api?.list_network_destinations) {
    select.replaceChildren(new Option('Network scan unavailable', ''));
    select.disabled = true;
    button.disabled = true;
    return;
  }
  try {
    // An HTTP(S) upload destination cannot be listed, so it is no library.
    const profiles = (await window.pywebview.api.list_network_destinations())
      .filter(profile => profile.type !== 'http');
    select.replaceChildren(new Option(
      profiles.length ? 'Select network library…' : 'No saved network destinations',
      '',
    ));
    for (const profile of profiles) {
      select.add(new Option(`${profile.name} — ${networkDestinationTarget(profile)}`, profile.id));
    }
    select.value = profiles.some(profile => profile.id === previous) ? previous : '';
    select.disabled = profiles.length === 0 || statsSmbScanActive;
    button.disabled = profiles.length === 0 || statsSmbScanActive;
  } catch (error) {
    select.replaceChildren(new Option('Network destinations unavailable', ''));
    select.disabled = true;
    button.disabled = true;
  }
}

async function scanSmbLibrary() {
  const profileId = $('stats-smb-destination')?.value;
  const button = $('stats-smb-scan');
  const profile = networkDestinations.find(item => item.id === profileId);
  const protocol = profile?.type === 'webdav' ? 'WebDAV' : 'SMB';
  if (!profileId || !button || statsSmbScanActive) return;
  if (!window.pywebview?.api?.scan_smb_library) {
    setStatsSmbStatus('Network library scanning is unavailable in this build.', true);
    return;
  }
  statsSmbScanActive = true;
  button.disabled = true;
  button.classList.add('ta-loading');
  setStatsSmbStatus(`Connecting to ${protocol} library…`);
  $('stats-body').innerHTML = `<div class="s-label u-fs-11_5">Scanning ${protocol} library…</div>`;
  try {
    const result = await window.pywebview.api.scan_smb_library(profileId);
    if (result?.status === 'error') throw new Error(result.error || 'Could not start network scan.');
  } catch (error) {
    finishSmbStatsScan();
    setStatsSmbStatus(error.message || 'Could not start network scan.', true);
  }
}

function finishSmbStatsScan() {
  statsSmbScanActive = false;
  $('stats-smb-scan')?.classList.remove('ta-loading');
  void loadStatsSmbDestinations();
}

window.app_smb_library_progress = function(payload) {
  const { done = 0, total = 0 } = payload || {};
  setStatsSmbStatus(total ? `Scanning network library · ${done} of ${total} files` : 'No supported audio files found.');
};

window.app_smb_library_results = function(report) {
  finishSmbStatsScan();
  const scan = report?.scan || {};
  const unreadable = scan.unreadable || 0;
  setStatsSmbStatus(
    `${scan.readable || 0} audio files read${unreadable ? ` · ${unreadable} unreadable` : ''}`,
    unreadable > 0 && !(scan.readable > 0),
  );
  renderStats(report);
};

window.app_smb_library_error = function(message) {
  finishSmbStatsScan();
  setStatsSmbStatus(message || 'Network library scan failed.', true);
};

// ── Listener profile (from Mornye mobile 5.4.5) ─────────────────────────────
// Stats' Listening tab: what the Player's listening record says about its
// listener (core/listener_profile.py), drawn with the Library tab's tiles
// and bars.
var statsTab = 'library';

function setStatsTab(tab) {
  statsTab = tab === 'listening' ? 'listening' : 'library';
  document.querySelectorAll('[data-stats-tab]').forEach((b) => {
    const on = b.dataset.statsTab === statsTab;
    b.classList.toggle('active', on);
    b.setAttribute('aria-selected', String(on));
  });
  $('profile-body')?.classList.toggle('hidden', statsTab !== 'listening');
  $('stats-library-panel')?.classList.toggle('hidden', statsTab !== 'library');
  document.querySelector('.stats-actions')?.classList.toggle('hidden', statsTab !== 'library');
  if (statsTab === 'listening') loadListenerProfile();
}

// "42%" of `whole`; "0%" without one.
function profileShare(part, whole) {
  return whole > 0 ? `${Math.round((part / whole) * 100)}%` : '0%';
}

// The last 30 days against the 30 before.
function profileChangeText(percent) {
  if (percent == null) return 'nothing to compare with yet';
  if (percent === 0) return 'as much as the 30 days before';
  return `${percent > 0 ? '+' : '−'}${Math.abs(percent)}% on the 30 days before`;
}

function profileDayLabel(day) {
  if (!day) return '';
  const d = new Date(`${day}T12:00:00`);
  return Number.isNaN(d.getTime()) ? day : d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
}

// A row of small columns, one per value (the 30 days, the 24 hours).
function profileColumns(values, labels, title) {
  const peak = Math.max(1, ...values);
  return `<div class="profile-cols" role="img" aria-label="${regEscapeHtml(title)}">${values.map((v, i) =>
    `<span class="profile-col" style="--v:${(v / peak).toFixed(3)}" title="${regEscapeHtml(`${labels[i]}: ${statsDuration(v)}`)}"></span>`).join('')}</div>`;
}

async function loadListenerProfile() {
  const body = $('profile-body');
  if (!body) return;
  if (!window.pywebview?.api?.get_listener_profile) {
    body.innerHTML = '<div class="s-label u-fs-11_5">The listener profile is unavailable in this build.</div>';
    return;
  }
  body.innerHTML = '<div class="s-label u-fs-11_5">Loading…</div>';
  let doc = null;
  try { doc = await window.pywebview.api.get_listener_profile(); } catch (e) { doc = null; }
  renderListenerProfile(doc);
}

function renderListenerProfile(doc) {
  const body = $('profile-body');
  if (!body) return;
  if (!doc || doc.status !== 'ok') {
    body.innerHTML = `<div class="s-label u-fs-11_5 u-c-red">${regEscapeHtml(doc?.error || 'Could not read the listening record.')}</div>`;
    return;
  }
  if (doc.empty) {
    body.innerHTML = `<div class="s-section">${emptyState('chart', 'Nothing played yet',
      'The profile is built from what the Player plays: time heard, songs played for 30 seconds or more, '
      + 'and the songs played through or skipped. Play a few songs and come back.')}</div>`;
    return;
  }
  const at = doc.all_time || {};
  const month = doc.last_30_days || {};
  const hab = doc.habits || {};
  const snd = doc.sound || {};
  const rh = doc.rhythm || {};
  const coll = doc.collection || {};
  const WEEK = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
  const parts = [];

  const traits = doc.traits || [];
  if (traits.length) {
    parts.push(statsSection('Listener type', `<ul class="profile-traits">${traits.map(t =>
      `<li><strong>${regEscapeHtml(t.name)}</strong><span>${regEscapeHtml(t.text)}</span></li>`).join('')}</ul>`));
  }

  parts.push(statsSection('All time', `<div class="stat-tiles">
    ${statsTile('listened', statsDuration(at.milliseconds), doc.since ? `since ${profileDayLabel(doc.since)}` : '')}
    ${statsTile('plays', (at.plays || 0).toLocaleString())}
    ${statsTile('songs', (at.songs || 0).toLocaleString())}
    ${statsTile('artists', (at.artists || 0).toLocaleString())}
    ${statsTile('days with music', (at.active_days || 0).toLocaleString())}
    ${statsTile('day streak', at.current_streak || 0, `longest ${at.longest_streak || 0}`)}
    ${at.peak_day ? statsTile('biggest day', statsDuration(at.peak_day_ms), profileDayLabel(at.peak_day)) : ''}
    ${at.replay ? statsTile('on repeat', `${at.replay.plays}×`, `${at.replay.title} on ${profileDayLabel(at.replay.day)}`) : ''}
  </div>`));

  const recent = hab.recent || [];
  const dayLabels = recent.map((_, i) => {
    const d = new Date(); d.setDate(d.getDate() - (recent.length - 1 - i));
    return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
  });
  parts.push(statsSection('Last 30 days', `<div class="stat-tiles">
      ${statsTile('listened', statsDuration(month.milliseconds), profileChangeText(month.change_percent))}
      ${statsTile('songs', (month.songs || 0).toLocaleString())}
      ${statsTile('days with music', month.active_days || 0)}
      ${statsTile('new artists', month.new_artist_count || 0, (month.new_artists || []).slice(0, 3).join(', '))}
    </div>${profileColumns(recent, dayLabels, 'Listening on each of the last 30 days')}`));

  const months = hab.months || [];
  if (months.some(v => v > 0)) {
    parts.push(statsSection('Last 12 months', statsBars(
      months.map((v, i) => ({ label: (hab.month_labels || [])[i] || '', value: v })), statsDuration)));
  }

  const genres = doc.genres || [];
  if (genres.length) {
    parts.push(statsSection('Taste by genre', statsBars(
      genres.slice(0, 10).map(g => ({ label: g.name, value: g.milliseconds })),
      v => profileShare(v, doc.genre_ms)),
      `${doc.genre_count} genres; shares of the songs with a genre tag.`));
  }
  const decades = snd.decades || [];
  if (decades.length) {
    parts.push(statsSection('Decades', statsBars(
      decades.map(d => ({ label: `${d.decade}s`, value: d.milliseconds })), v => profileShare(v, snd.decade_ms))));
  }
  if (snd.quality_ms > 0) {
    const q = snd.quality || {};
    parts.push(statsSection('Sound', statsBars([
      { label: 'Hi-Res', value: q.hiRes || 0 },
      { label: 'Lossless', value: q.lossless || 0 },
      { label: 'Lossy', value: q.lossy || 0 },
      ...(snd.formats || []).map(f => ({ label: f.name, value: f.milliseconds })),
    ].filter(r => r.value > 0), v => profileShare(v, snd.quality_ms))));
  }

  if (rh.played_through + rh.skipped > 0) {
    const hours = rh.hours || [];
    const DAYPART = { morning: 'Morning', afternoon: 'Afternoon', evening: 'Evening', night: 'Night' };
    const note = rh.enough
      ? `Mostly in the ${(rh.daypart || '').toLowerCase()}, busiest around ${rh.peak_hour}:00 · ${profileShare(rh.played_through, rh.played_through + rh.skipped)} played through`
      : `Needs ${20} songs played through or skipped to say much; ${rh.played_through + rh.skipped} so far.`;
    parts.push(statsSection('Time of day',
      `<div class="profile-cols profile-hours" role="img" aria-label="Songs played through at each hour">${hours.map((n, h) =>
        `<span class="profile-col" style="--v:${(n / Math.max(1, ...hours)).toFixed(3)}" title="${h}:00 — ${n} songs"></span>`).join('')}</div>
       <div class="profile-axis"><span>0:00</span><span>6:00</span><span>12:00</span><span>18:00</span><span>23:00</span></div>`
      + statsBars(Object.entries(rh.dayparts || {}).map(([k, v]) => ({ label: DAYPART[k] || k, value: v })), v => `${v} songs`),
      note));
  }

  if ((hab.weekdays || []).some(v => v > 0)) {
    const best = hab.weekdays.indexOf(Math.max(...hab.weekdays));
    parts.push(statsSection('Listening habits', statsBars(
      hab.weekdays.map((v, i) => ({ label: WEEK[i], value: v })), statsDuration),
      `An active day averages ${statsDuration(hab.average_ms)}; ${WEEK[best]} is the busiest.`));
  }

  parts.push(statsSection('Top artists', statsBars((doc.top_artists || []).map(a => ({ label: a.name, value: a.milliseconds })), statsDuration)));
  parts.push(statsSection('Top albums', statsBars((doc.top_albums || []).map(a => ({ label: a.artist ? `${a.album} — ${a.artist}` : a.album, value: a.milliseconds })), statsDuration)));
  parts.push(statsSection('Top songs', statsBars((doc.top_tracks || []).map(t => ({ label: `${t.title} — ${t.artist}`, value: t.plays })), v => `${v} plays`)));

  if (coll.songs || coll.downloads) {
    parts.push(statsSection('Collection', `<div class="stat-tiles">
      ${statsTile('songs', (coll.songs || 0).toLocaleString())}
      ${statsTile('albums', (coll.albums || 0).toLocaleString())}
      ${statsTile('artists', (coll.artists || 0).toLocaleString())}
      ${statsTile('genres', (coll.genres || 0).toLocaleString())}
      ${statsTile('playlists', (coll.playlists || 0).toLocaleString())}
      ${statsTile('of music', statsDuration(coll.duration_ms))}
      ${statsTile('downloaded', (coll.downloads || 0).toLocaleString())}
    </div>`));
  }
  body.innerHTML = parts.join('');
}

async function loadStats() {
  if (statsTab === 'listening') loadListenerProfile();
  const body = $('stats-body');
  if (!body) return;
  void loadStatsSmbDestinations();
  if (!window.pywebview?.api?.get_stats) {
    body.innerHTML = '<div class="s-label u-fs-11_5">Statistics are unavailable in this build.</div>';
    return;
  }
  body.innerHTML = '<div class="s-label u-fs-11_5">Loading…</div>';
  try {
    const [year, days] = statsPeriodArgs();
    renderStats(await window.pywebview.api.get_stats(year, days, 10, true));
  } catch (e) {
    body.innerHTML = '<div class="s-label u-fs-11_5 u-c-red">Could not read the download log.</div>';
  }
}

function renderStats(doc) {
  const body = $('stats-body');
  if (!body) return;
  if (!doc || doc.error) {
    body.innerHTML = `<div class="s-label u-fs-11_5 u-c-red">${regEscapeHtml(doc?.error || 'No data.')}</div>`;
    return;
  }

  const totals = doc.totals || {};
  const isNetworkScan = ['smb', 'webdav'].includes(doc.source);
  const sourceLabel = doc.source === 'webdav' ? 'WebDAV library' : 'SMB library';
  if (!totals.tracks) {
    const scan = doc.scan || {};
    body.innerHTML = `<div class="s-section">${emptyState(
      'chart',
      isNetworkScan ? 'No readable audio files' : 'Nothing to count yet',
      isNetworkScan
        ? `${scan.files || 0} supported audio files scanned; none had readable metadata.`
        : 'This is built from the download log, which fills up as you fetch tracks. '
          + 'Come back after a download or two.',
    )}</div>`;
    return;
  }

  const sections = [];
  if (isNetworkScan) {
    const scan = doc.scan || {};
    sections.push(`<div class="stats-source-note"><strong>${sourceLabel}</strong><span>${regEscapeHtml(doc.source_name || '')} · ${scan.files || 0} files scanned${scan.unreadable ? ` · ${scan.unreadable} unreadable` : ''}</span></div>`);
  }

  sections.push(`
    <div class="stat-tiles">
      ${statsTile('tracks', totals.tracks)}
      ${statsTile('artists', totals.artists)}
      ${statsTile('albums', totals.albums)}
      ${statsTile('on disk', statsBytes(totals.bytes))}
      ${totals.listening_known
        ? statsTile('of music', statsDuration(totals.listening_ms),
            totals.listening_known === totals.tracks ? '' : `timed for ${totals.listening_known} of them`)
        : ''}
      ${totals.failed ? statsTile('failed', totals.failed, `${Math.round((totals.success_rate || 0) * 100)}% succeeded`) : ''}
    </div>`);
  // Files the Player found that Mornye did not download (core/stats.py).
  if (totals.library_tracks) {
    sections.push(`<div class="stats-source-note"><strong>${totals.library_tracks.toLocaleString()} from before Mornye</strong><span>tracks in your Player library that Mornye did not download. They count in the totals and rankings, not in the activity.</span></div>`);
  }

  sections.push(statsSection('Top artists', statsBars(
    (doc.top_artists || []).map((e) => ({ label: e.name, value: e.tracks })),
  )));

  const albums = (doc.top_albums || []).map((e) => ({
    label: e.artist ? `${e.name} — ${e.artist}` : e.name,
    value: e.tracks,
  }));
  sections.push(statsSection('Top albums', statsBars(albums)));

  const genres = doc.top_genres || { entries: [] };
  sections.push(statsSection(
    'Top genres',
    statsBars((genres.entries || []).map((e) => ({ label: e.name, value: e.tracks }))),
    genres.unknown
      ? isNetworkScan
        ? `Genre tags found for ${genres.known} of ${totals.tracks} readable tracks.`
        : `Known for ${genres.known} of ${totals.tracks} tracks — a genre needs metadata enrichment to have been on when the track was downloaded.`
      : '',
  ));

  const decades = doc.decades || { entries: [] };
  if ((decades.entries || []).length) {
    sections.push(statsSection(
      'By decade',
      statsBars(decades.entries.map((e) => ({ label: e.name, value: e.tracks }))),
      decades.unknown
        ? `${isNetworkScan ? 'Release year tags' : 'Release year'} known for ${decades.known} of ${totals.tracks} tracks.`
        : '',
    ));
  }

  const repeats = doc.top_tracks || [];
  if (repeats.length) {
    sections.push(statsSection(
      isNetworkScan ? 'Top tracks' : 'Fetched more than once',
      statsBars(repeats.map((e) => ({ label: `${e.name} — ${e.artist}`, value: e.tracks }))),
      isNetworkScan
        ? `Most common track names and artists discovered in the ${doc.source.toUpperCase()} library scan.`
        : 'A re-download after a quality upgrade, or the same song from two playlists.',
    ));
  }

  if (!isNetworkScan) {
    sections.push(statsSection('Providers', statsBars(
      (doc.providers || []).map((e) => ({ label: e.name, value: e.tracks })),
    )));
  }

  const formats = doc.formats || [];
  if (formats.length) {
    sections.push(statsSection('Formats', statsBars(
      formats.map((e) => ({ label: (e.name || '').toUpperCase(), value: e.tracks })),
    )));
  }

  const timeline = (doc.timeline || []).slice(-12);
  if (!isNetworkScan && timeline.length) {
    sections.push(statsSection('Last months', statsBars(
      timeline.map((e) => ({ label: e.month, value: e.tracks })),
    )));
  }

  const activity = doc.activity || {};
  const weekdays = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
  if (!isNetworkScan && (activity.by_weekday || []).some((n) => n)) {
    sections.push(statsSection('By weekday', statsBars(
      activity.by_weekday.map((count, index) => ({ label: weekdays[index], value: count })),
    )));
  }

  const facts = [];
  if (!isNetworkScan && activity.busiest_day) {
    facts.push(`Busiest day: ${activity.busiest_day.date} (${activity.busiest_day.tracks} tracks)`);
  }
  if (!isNetworkScan && activity.active_days) {
    facts.push(`Downloaded on ${activity.active_days} day(s) · longest streak ${activity.longest_streak}`);
  }
  if (!isNetworkScan && doc.first) {
    const when = new Date((doc.first.downloaded_at || 0) * 1000).toLocaleDateString();
    facts.push(`First in this period: ${doc.first.title} — ${doc.first.artist} (${when})`);
  }
  if (facts.length) {
    sections.push(statsSection(
      'Highlights',
      facts.map((fact) => `<div class="s-label u-fs-12 u-lh-1_9">${regEscapeHtml(fact)}</div>`).join(''),
    ));
  }

  body.innerHTML = sections.join('');
}

// ── CSV import (core/csv_source.py) ───────────────────────────────────────
//
// The file is read here, in the browser, and only its text crosses to Python
// — so this works identically in the desktop window and over `--web`, and a
// server never has to be able to see the user's disk.

// Blob.text() everywhere it exists, FileReader where it doesn't — the
// desktop window runs whatever webview the operating system ships, which on
// an older install predates it.
function readFileAsText(file) {
  if (typeof file.text === 'function') return file.text();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ''));
    reader.onerror = () => reject(reader.error || new Error('read failed'));
    reader.readAsText(file);
  });
}

function openCsvPicker() {
  $('csvFileInput')?.click();
}

async function onCsvFileChosen(input) {
  const file = input?.files?.[0];
  // Cleared straight away so choosing the same file twice in a row still
  // fires a change event.
  if (input) input.value = '';
  if (!file) return;

  if (file.size > 2000000) {
    showToast('That file is too large to import (2 MB max).', 'error');
    return;
  }
  if (!window.pywebview?.api?.fetch_csv) {
    showToast('CSV import is unavailable in this build.', 'error');
    return;
  }

  let text;
  try {
    text = await readFileAsText(file);
  } catch (e) {
    showToast('Could not read that file.', 'error');
    return;
  }

  try {
    const started = await window.pywebview.api.fetch_csv(text, file.name);
    if (started?.status === 'error') {
      showToast(started.error || 'Could not read that file.', 'error');
      return;
    }
    showToast(`Reading ${file.name}… rows without a link are matched against the catalogue.`, 'info');
  } catch (e) {
    showToast(e.message || 'CSV import failed.', 'error');
  }
}

// Pushed by api_mixins/csv_import.py through both long phases of an import:
// matching the rows against the catalogue, then fetching the metadata of
// every link that matched. A large file is minutes of work in each, and
// without this the import said nothing between "Reading the file…" and the
// finished track list — a file whose columns were mapped wrong looked
// exactly like one that was working, and a stalled fetch like a slow one.
// Three numbers, because they answer different questions: how far along it
// is, how much of it is coming back, and how much is being lost.
window.app_csv_progress = function (payload) {
  const { phase = 'matching', done = 0, total = 0, found = 0, missing = 0 } = payload || {};
  const matching = phase === 'matching';
  const label =
    `${matching ? 'Matching' : 'Fetching metadata'} ${done}/${total} · ` +
    `${found} ${matching ? 'found' : 'ready'}` +
    (missing ? ` · ${missing} ${matching ? 'not found' : 'without metadata'}` : '');
  // The line itself is already on screen: the same counter arrives as an
  // app_set_progress label. This puts it on the button that started the
  // import too, since that is where the pointer is, and marks the button
  // busy — without touching its innerHTML, which is the icon.
  const btn = $('csvBtn');
  if (!btn) return;
  btn.title = label;
  btn.setAttribute('aria-label', label);
  // Only the metadata phase reaching its total ends the import; matching
  // hits done === total and is immediately followed by the second phase.
  btn.classList.toggle('is-busy', matching || done < total);
};

// Pushed by api_mixins/csv_import.py once the track list is ready. The table
// itself is filled by the ordinary showTracklist event, so this only reports
// on the rows that did not make it.
window.app_csv_loaded = function (payload) {
  const missed = (payload?.unresolved || []).length;
  const failed = payload?.failed || 0;
  const btn = $('csvBtn');
  if (btn) btn.classList.remove('is-busy');
  if (missed || failed) {
    const parts = [];
    if (missed) parts.push(`${missed} row(s) could not be matched`);
    // A row can match a link whose metadata then fails to fetch, which is
    // why this is a separate number from the unmatched rows: together they
    // account for the gap between the file's line count and the table's.
    if (failed) parts.push(`${failed} link(s) returned no metadata`);
    showToast(`${payload.tracks} track(s) loaded · ${parts.join(' · ')} (see the log).`, 'info');
  } else {
    showToast(`${payload.tracks} track(s) loaded from ${payload.file}.`, 'success');
  }
};

window.app_csv_error = function (payload) {
  const btn = $('csvBtn');
  if (btn) btn.classList.remove('is-busy');
  showToast(payload?.error || 'CSV import failed.', 'error');
};
