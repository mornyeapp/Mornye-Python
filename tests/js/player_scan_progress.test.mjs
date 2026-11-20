import test from 'node:test';
import assert from 'node:assert/strict';
import { pick, source } from './load.mjs';

const { libScanProgress } = pick(['libScanProgress', 'libScanEtaText']);

test('while folders are listed the bar is indeterminate and counts the files found', () => {
  assert.deepEqual({ ...libScanProgress({ phase: 'listing', done: 0, total: 0 }) },
    { pct: null, text: 'Looking for music…' });
  const p = libScanProgress({ phase: 'listing', done: 1234, total: 0 });
  assert.equal(p.pct, null);
  assert.equal(p.text, `Looking for music… ${(1234).toLocaleString()} files found`);
});

test('once the total is known the bar fills with done of total', () => {
  const p = libScanProgress({ phase: 'reading', done: 250, total: 1000 });
  assert.equal(p.pct, 25);
  assert.equal(p.text, `250 / ${(1000).toLocaleString()} files scanned`);
  assert.equal(libScanProgress({ phase: 'reading', done: 9, total: 3 }).pct, 100);
});

test('a reading phase with no files yet stays indeterminate, junk stays safe', () => {
  assert.equal(libScanProgress({ phase: 'reading', done: 0, total: 0 }).pct, null);
  assert.equal(libScanProgress(null).pct, null);
  assert.equal(libScanProgress({ phase: 'reading', done: 'x', total: 4 }).pct, 0);
});

test('the bar sits in the library card under the summary, and every scan end hides it', () => {
  const html = source('index.html');
  const stats = html.indexOf('id="plb-stats"');
  const bar = html.indexOf('id="plb-scan"');
  assert.ok(stats > 0 && bar > stats && bar < html.indexOf('class="plb-actions"'));
  const app = source('app.js');
  assert.match(app, /window\.app_library_scan_progress = function/);
  // finished / synced / error all go through setPlayerScanBusy(false|stored),
  // which hides the bar when the scan is over.
  assert.match(app, /setPlayerScanProgress\(busy \? \{ phase: 'listing', done: 0, total: 0 \} : null\)/);
});

test('the scan can be stopped from the card, and the Player handles the stop', () => {
  const html = source('index.html');
  assert.match(html, /id="plb-scan-stop"[^>]*data-onclick="cancelPlayerScan\(\)"/);
  const app = source('app.js');
  assert.match(app, /^async function cancelPlayerScan\(\)/m);
  assert.match(app, /cancel_music_library_scan/);
  assert.match(app, /window\.app_library_scan_cancelled = function/);
});

const eta = pick(['libScanProgress', 'libScanRemaining', 'libScanEtaText']);

test('time left comes from the recent pace, and only once the pace is known', () => {
  assert.equal(eta.libScanRemaining([], 0, 100), null);
  assert.equal(eta.libScanRemaining([{ t: 0, done: 0 }, { t: 1000, done: 10 }], 10, 100), null, 'under 3 s');
  // 20 files in 10 s → 2 files/s; 80 left → 40 s.
  assert.equal(eta.libScanRemaining([{ t: 0, done: 0 }, { t: 10000, done: 20 }], 20, 100), 40);
  // Only the last 30 s count: a fast start (skipped, stored files) is forgotten.
  const samples = [{ t: 0, done: 0 }, { t: 5000, done: 1000 }, { t: 35000, done: 1030 }];
  assert.equal(eta.libScanRemaining(samples, 1030, 1090), 60);
  assert.equal(eta.libScanRemaining(samples, 1090, 1090), null, 'nothing left');
});

test('time left reads as minutes or hours, and joins the counter', () => {
  assert.equal(eta.libScanEtaText(null), '');
  assert.equal(eta.libScanEtaText(20), 'less than a minute left');
  assert.equal(eta.libScanEtaText(12 * 60), '~12 min left');
  assert.equal(eta.libScanEtaText(2 * 3600 + 5 * 60), '~2 h 05 min left');
  const p = eta.libScanProgress({ phase: 'reading', done: 1, total: 4 }, 12 * 60);
  assert.equal(p.text, '1 / 4 files scanned · ~12 min left');
  assert.equal(eta.libScanProgress({ phase: 'reading', done: 4, total: 4 }, 30).text, '4 / 4 files scanned');
});

test('stopping a scan still links playlist songs to what was read', () => {
  const app = source('app.js');
  const body = app.slice(app.indexOf('window.app_library_scan_cancelled'), app.indexOf('window.app_library_scan_progress'));
  assert.match(body, /linkPlaylistTracksToLibrary\(\)/);
});

test('playlist songs are linked while the scan reads, at most every 20 s', () => {
  const { libLinkDue } = pick(['libLinkDue'], { LIB_LINK_EVERY: 20000 });
  assert.equal(libLinkDue(0, 1_000_000), true);
  assert.equal(libLinkDue(1_000_000, 1_019_999), false);
  assert.equal(libLinkDue(1_000_000, 1_020_000), true);
  const app = source('app.js');
  for (const handler of ['app_library_scan_chunk', 'app_library_scan_delta']) {
    const start = app.indexOf(`window.${handler} = function`);
    const body = app.slice(start, app.indexOf('\n};\n', start));
    assert.match(body, /linkPlaylistsDuringScan\(\)/, handler);
  }
});
