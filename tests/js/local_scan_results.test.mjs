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

const safe = (path) => ({ file_path: path, candidates: [{ is_safe: true }] });
const unsure = (path) => ({ file_path: path, candidates: [{ is_safe: false }] });
const none = (path) => ({ file_path: path, candidates: [] });
const localNames = ['localHasMatch', 'localIsPicked', 'localPickedIndices', 'localViewIndices', 'localRowState',
  'localSearchTerms', 'localMatchesSearch'];
const freshList = () => ({ view: [], shown: 0, rows: new Map(), picked: new Map(), applied: new Set() });
const localGlobals = (localScanData, more = {}) => ({
  localScanData, localList: freshList(), localFilter: '', localQuery: '', localSearchText: new WeakMap(), ...more,
});

test('a file is ticked when its match is safe, until the user says otherwise', () => {
  const localScanData = [safe('/a'), unsure('/b'), none('/c'), safe('/d')];
  const localList = freshList();
  const f = pick(localNames, localGlobals(localScanData, { localList }));
  assert.deepEqual([...f.localPickedIndices()], [0, 3]);
  localList.picked.set('/a', false);
  localList.picked.set('/b', true);
  localList.picked.set('/c', true);  // no match: nothing to apply, never ticked
  assert.deepEqual([...f.localPickedIndices()], [1, 3]);
  localList.applied.add('/d');  // already tagged
  assert.deepEqual([...f.localPickedIndices()], [1]);
});

test('the ticks and the filter work on the results, not on the rows on screen', () => {
  // Only the first files of a long list have a row (LOCAL_PAGE), so a tick
  // or a filter that read the rows would miss the rest of the folder.
  const localScanData = [safe('/a'), unsure('/b'), none('/c'), unsure('/d')];
  const f = pick(localNames, localGlobals(localScanData, { localFilter: 'review' }));
  assert.deepEqual([...f.localViewIndices()], [1, 3]);
  const all = pick(localNames, localGlobals(localScanData));
  assert.deepEqual([...all.localViewIndices()], [0, 1, 2, 3]);
  const app = source('app.js');
  for (const name of ['updateLocalSelection', 'toggleAllLocal', 'applyLocalTags']) {
    const start = app.indexOf(`function ${name}(`);
    const body = app.slice(start, app.indexOf('\n}\n', start));
    assert.doesNotMatch(body, /querySelectorAll\('\.local-cb/, name);
  }
});

test('the search finds a song by every word, in its tags, file name or match', () => {
  const song = (path, title, artist, match) => ({
    file_path: path, old_title: title, old_artist: artist,
    candidates: match ? [{ is_safe: true, metadata: { title: match, first_artist: 'Queen' } }] : [],
  });
  const localScanData = [
    song('/m/01 track.flac', 'Bohemian Rhapsody', 'Queen'),
    song('/m/02 Under Pressure.mp3', '', ''),
    song('/m/03 x.flac', 'untitled', '', "Don't Stop Me Now"),
    song('/m/04 y.flac', 'Rhapsody in Blue', 'Gershwin'),
  ];
  const view = (query, localFilter = '') =>
    [...pick(localNames, localGlobals(localScanData, { localQuery: query, localFilter })).localViewIndices()];
  assert.deepEqual(view('queen bohemian'), [0], 'words in any order, across title and artist');
  assert.deepEqual(view('RHAPSODY'), [0, 3], 'case does not matter');
  assert.deepEqual(view('pressure'), [1], 'by the file name when the tags are empty');
  assert.deepEqual(view('stop me'), [2], 'by the match it would get');
  assert.deepEqual(view('rhapsody', 'matched'), [], 'together with the filter chips');
  assert.deepEqual(view('  '), [0, 1, 2, 3], 'blank shows every file');
  const html = source('index.html');
  assert.match(html, /id="local-search"[^>]*data-oninput="filterLocalSearch\(\)"/);
});

test('the list has rows for a page of files, and more as its end scrolls into view', () => {
  // A row for each of thousands of files made every batch of a scan lay
  // the whole list out again (~40 ms a batch at 3,000 files).
  const app = source('app.js');
  assert.match(app, /^const LOCAL_PAGE = \d+;/m);
  const start = app.indexOf('function renderLocalTracks(');
  const body = app.slice(start, app.indexOf('\n}\n', start));
  assert.doesNotMatch(body, /localScanData\.forEach/);
  assert.match(body, /localAppendRows\(/);
  // Watched against the list, which scrolls on its own inside the page.
  assert.match(app, /root: container, rootMargin/);
  const css = source('css/views/local.css');
  assert.match(css, /#local-track-rows > \.local-more/);
  assert.doesNotMatch(css, /data-filter/);
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
