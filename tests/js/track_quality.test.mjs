// The on-demand Qobuz / TIDAL quality check in the track list.
import test from 'node:test';
import assert from 'node:assert/strict';
import { pick, source } from './load.mjs';

const NAMES = [
  'escHtml', 'trackQualityKey', 'trackQualityChipsHtml', 'requestTrackQuality',
  'stopQualityRun', 'checkSelectedQuality',
];

function setup({ tracks, answer }) {
  const asked = [];
  const errors = [];
  const ctx = {
    QUALITY_MAX_FAILURES: 3,
    trackQualities: new Map(),
    qualityRun: null,
    currentTracks: tracks,
    document: {
      querySelectorAll: () => tracks.map((_, i) => ({ value: String(i) })),
    },
    window: { pywebview: { api: { check_track_quality: async (t) => {
      asked.push(t.id);
      return answer(t, ctx);
    } } } },
    paintTrackQuality() {},
    paintQualityRunButton() {},
    onCheckChange() {},
    setStatus() {},
    logMessage() {},
    toastMgr: { error: (message) => errors.push(message) },
  };
  // pick() runs the functions in a context made of these globals; reading
  // them back through that context is what the functions themselves see.
  const fns = pick(NAMES, ctx);
  return { fns, asked, errors, ctx };
}

const tracks = (n) => Array.from({ length: n }, (_, i) => ({ id: `t${i}`, title: `Song ${i}` }));
const ok = (state) => ({ status: 'ok', results: [{ service: 'qobuz', state, label: 'Qobuz', detail: '' }] });

test('chips escape what a service said', () => {
  const { fns } = setup({ tracks: [], answer: () => ok('hires') });
  const html = fns.trackQualityChipsHtml([
    { state: 'error', label: 'Qobuz <b>', detail: '"quoted" & <img>' },
  ]);
  assert.ok(html.includes('data-state="error"'));
  assert.ok(html.includes('Qobuz &lt;b&gt;'));
  assert.ok(html.includes('title="&quot;quoted&quot; &amp; &lt;img&gt;"'));
  assert.equal(fns.trackQualityChipsHtml(undefined), '');
});

test('a track keeps its key wherever it is in the list', () => {
  const { fns } = setup({ tracks: [], answer: () => ok('hires') });
  assert.equal(fns.trackQualityKey({ isrc: 'ISRC1', id: 'a' }), 'ISRC1');
  assert.equal(fns.trackQualityKey({ id: 'a' }), 'a');
  assert.notEqual(
    fns.trackQualityKey({ title: 'One', artists: 'X', duration_ms: 1 }),
    fns.trackQualityKey({ title: 'Two', artists: 'X', duration_ms: 1 }),
  );
});

test('the selected tracks are asked about one at a time, in order', async () => {
  let open = 0;
  const { fns, asked, errors } = setup({ tracks: tracks(4), answer: async () => {
    assert.equal(++open, 1);
    await null;
    open -= 1;
    return ok('hires');
  } });
  await fns.checkSelectedQuality();
  assert.deepEqual(asked, ['t0', 't1', 't2', 't3']);
  assert.deepEqual(errors, []);
});

test('a run stops when the services keep failing', async () => {
  const { fns, asked, errors } = setup({ tracks: tracks(10), answer: () => ok('error') });
  await fns.checkSelectedQuality();
  assert.equal(asked.length, 3);
  assert.equal(errors.length, 1);
});

test('a run stops at once when there is nothing to ask', async () => {
  const { fns, asked, errors } = setup({
    tracks: tracks(5),
    answer: () => ({ status: 'error', error: 'Install the Qobuz or TIDAL extension to check qualities.' }),
  });
  await fns.checkSelectedQuality();
  assert.equal(asked.length, 1);
  assert.deepEqual(errors, ['Install the Qobuz or TIDAL extension to check qualities.']);
});

test('a run stops when another list takes its place', async () => {
  const list = tracks(5);
  const { fns, asked } = setup({ tracks: list, answer: () => {
    list.splice(0, list.length, { id: 'new', title: 'New' });
    return ok('hires');
  } });
  await fns.checkSelectedQuality();
  assert.deepEqual(asked, ['t0']);
});

test('every row, the card and the toolbar reach the check', () => {
  const app = source('app.js');
  assert.ok(app.includes('data-onclick="checkTrackQuality(${globalIndex})"'));
  assert.ok(app.includes('data-onclick="checkTrackQuality(0)"'));
  for (const file of ['app.js', 'index.html']) {
    assert.ok(source(file).includes('data-onclick="checkSelectedQuality()"'), file);
  }
});
