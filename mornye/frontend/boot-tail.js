// boot-tail.js — the app scripts and the service worker (F1: was inline
// script at the end of index.html).
(function () {
  var t = Date.now();
  // inline-handlers.js first: it binds the data-on<event>="…" handlers of
  // the markup above, which a Content-Security-Policy without
  // 'unsafe-inline' would not run as onclick="…" attributes.
  document.write('<script src="inline-handlers.js?_=' + t + '"><\/script>');
  document.write('<script src="toast-system.js?_=' + t + '"><\/script>');
  document.write('<script src="app.js?_=' + t + '"><\/script>');
  // The command palette and shortcuts (N1): after app.js, whose functions it calls.
  document.write('<script src="palette.js?_=' + t + '"><\/script>');
})();

// --web mode only (see sw.js's own comment for what the service worker
// actually does — installability, not offline caching of live data).
// window.__MORNYE_WEB_MODE__ is set by web-shim.js, which webapp.py's
// index() route injects before this script; the desktop pywebview window
// never loads it, so this is a no-op there.
if (window.__MORNYE_WEB_MODE__ && 'serviceWorker' in navigator) {
  navigator.serviceWorker.register('sw.js').catch(function () {
    // Registration failing (e.g. served over plain http:// on a LAN
    // address, where service workers require a secure context) should
    // never block the app itself from working.
  });
}
