// boot-body.js — first thing in <body>, still before anything paints
// (F1: was inline script in index.html).
// Mirror the class the <head> script just put on <html> onto <body>, still
// before anything paints. Most component rules are written as
// `body.dark-theme ...`, and the base `body { background: ... }` is a
// light-only gradient — so a body without the class would paint a light
// slab over the correctly-dark <html> for the frames between here and
// app.js's applyTheme().
document.body.classList.add(
  document.documentElement.classList.contains('dark-theme') ? 'dark-theme' : 'light-theme'
);
// Same mirroring for the accent — css/accents.css keys its --signal blocks
// off html *and* body (see setThemeClass/applyAccent in app.js).
var _accent = document.documentElement.getAttribute('data-accent');
if (_accent) document.body.setAttribute('data-accent', _accent);
var _density = document.documentElement.getAttribute('data-density');
if (_density) document.body.setAttribute('data-density', _density);
// Sidebar collapsed or labelled (toggleSidebar in app.js), before paint so
// the content does not jump sideways once the app has loaded.
try {
  if (localStorage.getItem('mornye-sidebar') === 'collapsed') document.body.classList.add('sidebar-collapsed');
} catch (e) {}
