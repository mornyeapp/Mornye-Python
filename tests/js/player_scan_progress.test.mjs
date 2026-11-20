import test from 'node:test';
import assert from 'node:assert/strict';
import { pick, source } from './load.mjs';

const { libScanProgress } = pick(['libScanProgress']);

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
