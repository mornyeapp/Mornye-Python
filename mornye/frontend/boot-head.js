// boot-head.js — runs in <head>, before the first paint (F1: was inline
// script in index.html; a Content-Security-Policy without 'unsafe-inline'
// only runs scripts from files). Two jobs, in this order: the stylesheets,
// then the theme and accent classes.

// No hand-kept ?v= suffix: the files below are written out with a
// fresh timestamp on every load, so a changed stylesheet or app.js is never
// served from the web view's cache. They are local files; nothing is lost.
// css/tokens.css (token v2, generated from branding_tokens.py) comes
// first: every other sheet is built on its tokens.
document.write('<link rel="stylesheet" href="css/tokens.css?_=' + Date.now() + '">');
// css/accents.css: the Appearance accents, each a set of --signal values.
document.write('<link rel="stylesheet" href="css/accents.css?_=' + Date.now() + '">');
// css/layout.css: reset, fonts and the shell (titlebar, rail, dock, player
// bar, the phone bottom bar).
document.write('<link rel="stylesheet" href="css/layout.css?_=' + Date.now() + '">');
// Old shared controls, then one stylesheet per rebuilt view.
document.write('<link rel="stylesheet" href="css/legacy.css?_=' + Date.now() + '">');
document.write('<link rel="stylesheet" href="css/feedback.css?_=' + Date.now() + '">');
document.write('<link rel="stylesheet" href="css/views/home.css?_=' + Date.now() + '">');
document.write('<link rel="stylesheet" href="css/views/queue.css?_=' + Date.now() + '">');
document.write('<link rel="stylesheet" href="css/views/settings.css?_=' + Date.now() + '">');
document.write('<link rel="stylesheet" href="css/views/local.css?_=' + Date.now() + '">');
document.write('<link rel="stylesheet" href="css/views/following.css?_=' + Date.now() + '">');
document.write('<link rel="stylesheet" href="css/views/explore.css?_=' + Date.now() + '">');
document.write('<link rel="stylesheet" href="css/views/stats.css?_=' + Date.now() + '">');
document.write('<link rel="stylesheet" href="css/views/player.css?_=' + Date.now() + '">');
document.write('<link rel="stylesheet" href="css/views/logs.css?_=' + Date.now() + '">');
// css/utilities.css: the u-* classes and the status tones, last so they win.
document.write('<link rel="stylesheet" href="css/utilities.css?_=' + Date.now() + '">');
// The music-app look (Settings → General → Interface Style): its token
// values and its rules, both scoped to data-style="music", so they change
// nothing in the console look. Last, so they win the ties.
document.write('<link rel="stylesheet" href="css/music-tokens.css?_=' + Date.now() + '">');
document.write('<link rel="stylesheet" href="css/music.css?_=' + Date.now() + '">');
// Theme, applied before the first paint. app.js used to do this only from
// its 'pywebviewready' handler, which meant the window painted light and
// then flipped — and in --web mode that event never fires until you are
// signed in, so the login screen, and any session that failed to sign in,
// stayed light no matter what was chosen.
//
// This lives in <head>, not at the top of <body>: it needs to run before
// anything is painted, and at that point document.body does not exist
// yet, which is why the class goes on documentElement. app.js's
// setThemeClass() then keeps <html> and <body> carrying the same class —
// <html> so that `html { background: var(--bg) }` picks up the dark
// tokens, <body> for the many `body.dark-theme ...` component rules.
//
// The choice is stored twice: 'mornye-theme-mode' here (per-origin,
// readable this early) and gui-settings.json server-side (survives a
// restart even when the web view's storage does not — see save_theme()
// in app.py). applyTheme() in app.js reconciles the two after load.
(function () {
  var mode = 'auto';
  try { mode = localStorage.getItem('mornye-theme-mode') || 'auto'; } catch (e) {}
  var dark = mode === 'dark' ||
    (mode === 'auto' && window.matchMedia('(prefers-color-scheme: dark)').matches);
  document.documentElement.classList.add(dark ? 'dark-theme' : 'light-theme');
  // theme-color is what paints the browser UI around the page on a phone —
  // Chrome's address bar, and the status/gesture bars of an installed PWA.
  // Hard-coded dark, it framed the light theme in a black bar. It follows
  // the chosen theme instead, here for the first paint and in app.js's
  // setThemeClass() for every change after it.
  try {
    var tc = document.querySelector('meta[name="theme-color"]');
    if (tc) tc.setAttribute('content', dark ? '#0d0d0d' : '#f2f2f0');
  } catch (e) {}
  // Accent, for the same reason and in the same breath: without it the
  // first frames paint the default amber before applyAccent() runs.
  // 'amber' (the console signal) is the default and has no data-accent
  // value. A stored 'teal' or 'green' from before the console redesign
  // is almost always an untouched old default: moved to amber once
  // ('mornye-brand' marks it done; see migrateAccent in app.js).
  try {
    var accent = localStorage.getItem('mornye-accent');
    if ((accent === 'teal' || accent === 'green') && localStorage.getItem('mornye-brand') !== '3') accent = 'amber';
    if (accent && accent !== 'amber') document.documentElement.setAttribute('data-accent', accent);
  } catch (e) {}
  // Density (N4), for the same reason: the rows would jump once
  // applyDensity() runs. boot-body.js copies it onto <body>.
  try {
    if (localStorage.getItem('mornye-style') === 'music') document.documentElement.setAttribute('data-style', 'music');
  } catch (e) {}
  try {
    var _d = localStorage.getItem('mornye-density');
    if (_d === 'compact' || _d === 'extra-compact') document.documentElement.setAttribute('data-density', 'compact');
    else if (_d === 'large' || _d === 'extra-large') document.documentElement.setAttribute('data-density', 'large');
  } catch (e) {}
})();
