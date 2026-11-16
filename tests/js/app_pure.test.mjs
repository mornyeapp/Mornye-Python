import test from 'node:test';
import assert from 'node:assert/strict';
import { pick } from './load.mjs';

const f = pick(['escHtml', 'httpUrlOrNull', 'formatEta', 'normalizeHistoryUrl', 'fmtTime',
  'describeError', 'formatAudioQuality']);

test('escHtml escapes the five markup characters and survives null', () => {
  assert.equal(f.escHtml(`<a href="x">'&'</a>`), '&lt;a href=&quot;x&quot;&gt;&#39;&amp;&#39;&lt;/a&gt;');
  assert.equal(f.escHtml(null), '');
});

test('httpUrlOrNull keeps http(s) and refuses script, data and file URLs', () => {
  assert.equal(f.httpUrlOrNull('https://i.scdn.co/a.jpg'), 'https://i.scdn.co/a.jpg');
  for (const bad of ['javascript:alert(1)', 'data:image/png;base64,x', 'file:///etc/passwd']) {
    assert.equal(f.httpUrlOrNull(bad), null, bad);
  }
  assert.equal(f.httpUrlOrNull(''), null);
});

test('formatEta: seconds, then minutes and padded seconds; nothing for 0', () => {
  assert.equal(f.formatEta(0), '');
  assert.equal(f.formatEta(42), '~42s');
  assert.equal(f.formatEta(125), '~2m05s');
});

test('normalizeHistoryUrl turns URIs and bare hosts into links, but not look-alike hosts', () => {
  assert.equal(f.normalizeHistoryUrl('spotify:album:abc'), 'https://open.spotify.com/album/abc');
  assert.equal(f.normalizeHistoryUrl('open.spotify.com/track/x'), 'https://open.spotify.com/track/x');
  assert.notEqual(f.normalizeHistoryUrl('open.spotify.com.evil.com/x'), 'https://open.spotify.com.evil.com/x');
});

test('fmtTime clamps nonsense to 0:00', () => {
  assert.equal(f.fmtTime(65), '1:05');
  assert.equal(f.fmtTime(-1), '0:00');
  assert.equal(f.fmtTime(NaN), '0:00');
});

test('describeError sorts a failure into one kind (one error, one place)', () => {
  assert.equal(f.describeError('optional tidal extension is not installed', 'Search').kind, 'setup');
  assert.equal(f.describeError('the extension does not implement customSearch()', 'Search').kind, 'error');
  assert.equal(f.describeError('getaddrinfo ENOTFOUND api', 'Search').kind, 'network');
  assert.equal(f.describeError('boom', 'Search').title, 'Search failed');
});

test('formatAudioQuality: depth / kHz · format, or kbps for lossy', () => {
  assert.equal(f.formatAudioQuality({ format: 'flac', bit_depth: 24, sample_rate: 96000 }), '24 / 96 · FLAC');
  assert.equal(f.formatAudioQuality({ format: 'FLAC', bit_depth: 16, sample_rate: 44100 }), '16 / 44.1 · FLAC');
  assert.equal(f.formatAudioQuality({ format: 'MP3', bitrate: 320000 }), '320 KBPS · MP3');
  assert.equal(f.formatAudioQuality(null), '');
});
