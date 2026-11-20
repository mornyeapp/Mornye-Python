import test from 'node:test';
import assert from 'node:assert/strict';
import { pick, source } from './load.mjs';

const { mergeLocalResults } = pick(['mergeLocalResults']);
const row = (path, title = '') => ({ file_path: path, old_title: title });
const paths = list => [...list].map(e => e.file_path);

test('a result for a file already listed replaces it in place; a new file goes last', () => {
  const cached = [row('/m/b.flac', 'old b'), row('/m/a.flac', 'a')];
  const { items, touched } = mergeLocalResults(cached, [row('/m/b.flac', 'new b'), row('/m/c.flac')]);
  assert.deepEqual(paths(items), ['/m/b.flac', '/m/a.flac', '/m/c.flac'], 'rows on screen keep their index');
  assert.equal(items[0].old_title, 'new b');
  assert.deepEqual([...touched], [0, 2]);
  assert.equal(cached[0].old_title, 'old b', 'the list it was given is left alone');
});

test('new results are painted row by row, not by rebuilding the whole list', () => {
  const app = source('app.js');
  const start = app.indexOf('window.app_local_scan_chunk = function');
  const body = app.slice(start, app.indexOf('\n};\n', start));
  assert.match(body, /scheduleLocalRows\(\)/);
  assert.doesNotMatch(body, /renderLocalTracks/);
  assert.match(app, /^function paintLocalRows\(\)/m);
  assert.match(app, /^function localRowElement\(item, i\)/m);
});

test('Fix Local Files follows the scan in parts, with the shared scan bar', () => {
  const app = source('app.js');
  for (const event of ['app_local_scan_chunk', 'app_local_scan_progress', 'app_local_scan_done']) {
    assert.match(app, new RegExp(`window\\.${event} = function`), event);
  }
  assert.doesNotMatch(app, /window\.app_local_scan_results/);
  assert.match(app, /paintScanBar\('local-scan'/);
  const html = source('index.html');
  assert.match(html, /class="scan-bar hidden" id="local-scan"/);
  assert.match(html, /class="scan-bar hidden" id="plb-scan"/);
});

test('a result is Ready, Check, No match or Error from its best candidate', () => {
  const { localRowState } = pick(['localRowState']);
  assert.equal(localRowState({ candidates: [{ is_safe: true }] }), 'matched');
  assert.equal(localRowState({ candidates: [{ is_safe: false }] }), 'review');
  assert.equal(localRowState({ candidates: [] }), 'none');
  assert.equal(localRowState({ candidates: [], error: 'unreadable' }), 'error');
  assert.equal(localRowState({ candidates: [{ is_safe: true }], error: 'x' }), 'matched',
    'a match wins over an error, as the row shows it');
});

test('a filter chip hides rows with one attribute, not a class on every row', () => {
  // Toggling .hidden on each of thousands of rows froze the window for
  // minutes on "Check"; the list now carries data-filter and CSS does the rest.
  const app = source('app.js');
  const start = app.indexOf('function applyLocalFilter()');
  const body = app.slice(start, app.indexOf('\n}\n', start));
  assert.match(body, /container\.dataset\.filter = localFilter/);
  assert.doesNotMatch(body, /querySelectorAll/);
  const css = source('css/views/local.css');
  for (const state of ['matched', 'review', 'none', 'error']) {
    assert.match(css, new RegExp(
      `#local-track-rows\\[data-filter="${state}"\\] > \\[data-state\\]:not\\(\\[data-state="${state}"\\]\\)`
    ), state);
  }
});

test('while a scan paints, the summary is counted from the data a few times a second', () => {
  const app = source('app.js');
  const start = app.indexOf('function paintLocalRows()');
  const body = app.slice(start, app.indexOf('\n}\n', start));
  assert.match(body, /scheduleLocalSummary\(\)/);
  assert.doesNotMatch(body, /renderLocalSummary\(\)/);
  assert.doesNotMatch(body, /updateLocalSelection\(\)/);
  const summaryStart = app.indexOf('function renderLocalSummary()');
  const summary = app.slice(summaryStart, app.indexOf('\n}\n', summaryStart));
  assert.match(summary, /for \(const item of localScanData\)/);
  assert.doesNotMatch(summary, /querySelectorAll\('#local-track-rows/);
});

test('a Fix Local Files scan can be stopped from its bar', () => {
  const html = source('index.html');
  assert.match(html, /id="local-scan-stop"[^>]*data-onclick="cancelLocalScan\(\)"/);
  const app = source('app.js');
  assert.match(app, /^async function cancelLocalScan\(\)/m);
  assert.match(app, /cancel_local_scan/);
  const start = app.indexOf('window.app_local_scan_cancelled = function');
  const body = app.slice(start, app.indexOf('\n};\n', start));
  assert.match(body, /paintScanBar\('local-scan', null/);
  assert.match(body, /setTaBtnState\(\$\('btn-scan-local'\), 'default'\)/);
});
