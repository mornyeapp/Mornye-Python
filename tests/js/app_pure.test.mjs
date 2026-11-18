import test from 'node:test';
import assert from 'node:assert/strict';
import { pick } from './load.mjs';

const f = pick(['escHtml', 'httpUrlOrNull', 'formatEta', 'normalizeHistoryUrl', 'fmtTime',
  'describeError']);

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

test('Download All requeues finished playlist tracks so a missing M3U can be rebuilt', () => {
  for (const status of ['done', 'skipped', 'error']) {
    const queue = [{
      id: 'song',
      spotify_id: 'song',
      source: 'https://open.spotify.com/playlist/mix',
      list: 1,
      index: 0,
      title: 'Old title',
      artist: 'Old artist',
      album: 'Old album',
      status,
      progress: 100,
      file_path: '/music/Mix/song.flac',
      file_size_mb: 12,
    }];
    const currentTracks = [{
      id: 'song',
      title: 'Song',
      artist: 'Artist',
      album: 'Album',
      list_id: 2,
    }];
    const app = pick(['addToQueue'], {
      queue,
      currentTracks,
      currentUrl: 'https://open.spotify.com/playlist/mix',
      renderQueue() {},
      $: () => null,
      console: { log() {}, warn() {} },
    });

    assert.equal(app.addToQueue([0], { requeueFinished: true }), true);
    assert.deepEqual(queue[0], {
      id: 'song',
      spotify_id: 'song',
      source: 'https://open.spotify.com/playlist/mix',
      list: 2,
      index: 0,
      title: 'Song',
      artist: 'Artist',
      album: 'Album',
      status: 'waiting',
      progress: 0,
      file_path: '',
      file_size_mb: 0,
    });
  }
});

test('Download All only requeues finished playlist tracks', () => {
  const options = [];
  for (const url of [
    'https://open.spotify.com/playlist/mix',
    'https://open.spotify.com/album/album',
  ]) {
    const app = pick(['downloadAll', 'detectUrlType'], {
      currentTracks: [{}],
      queue: [],
      currentUrl: url,
      addToQueue: (_indices, value) => { options.push(value); },
      startDownloadQueue() {},
      setStatus() {},
      $: () => ({ classList: { add() {} } }),
      console: { log() {} },
    });
    app.downloadAll();
  }

  assert.deepEqual(JSON.parse(JSON.stringify(options)), [
    { requeueFinished: true },
    { requeueFinished: false },
  ]);
});

test('playlist Download All leaves waiting and active queue items alone', () => {
  for (const status of ['waiting', 'active']) {
    const queue = [{
      id: 'song',
      spotify_id: 'song',
      source: 'https://open.spotify.com/playlist/mix',
      index: 0,
      title: 'Song',
      status,
      progress: 10,
    }];
    const app = pick(['addToQueue'], {
      queue,
      currentTracks: [{ id: 'song', title: 'Song' }],
      currentUrl: 'https://open.spotify.com/playlist/mix',
      renderQueue() {},
      $: () => null,
      toastMgr: { info() {} },
      console: { log() {}, warn() {} },
    });

    assert.equal(app.addToQueue([0], { requeueFinished: true }), false);
    assert.equal(queue[0].status, status);
  }
});

test('batch source picker lists that provider’s declared qualities and restores its choice', () => {
  const qualitySelect = { value: '', disabled: false, innerHTML: '' };
  const picker = pick(['updateProviderPickerQuality', 'escHtml'], {
    $: (id) => id === 'provider-picker-quality' ? qualitySelect : null,
    installedServiceRows: [{
      id: 'tidal',
      quality_options: [
        { id: 'LOSSLESS', label: 'Lossless' },
        { id: 'DOLBY_ATMOS', label: 'Dolby Atmos' },
      ],
    }],
  });

  picker.updateProviderPickerQuality('tidal', {
    extension_quality: { tidal: 'DOLBY_ATMOS' },
  });

  assert.equal(qualitySelect.value, 'DOLBY_ATMOS');
  assert.match(qualitySelect.innerHTML, /Use global quality/);
  assert.match(qualitySelect.innerHTML, /Lossless/);
  assert.match(qualitySelect.innerHTML, /Dolby Atmos/);
});

test('lyrics timing applies offset and speed, with inverse mapping for line seeking', () => {
  const sync = { offset: 0.5, speed: 1.1 };
  const f = pick(['npLyricsTime', 'npAudioTimeForLyrics'], { NP_SYNC: sync });

  assert.equal(f.npLyricsTime(10), 11.5);
  assert.equal(f.npAudioTimeForLyrics(11.5), 10);
  assert.equal(f.npLyricsTime(0), 0.5);
});

test('changing lyric speed immediately recalculates the current lyric position', () => {
  const sync = { offset: 0, speed: 1 };
  const output = { textContent: '' };
  let updatedTime = null;
  const globals = {
    NP_SYNC: sync,
    NP: { focus: 0 },
    $: (id) => id === 'player-audio' ? { currentTime: 100 } : output,
    npUpdate: (time) => { updatedTime = time; },
  };
  const f = pick(['npSetSync', 'npLyricsTime'], globals);

  f.npSetSync('speed', '1.1');

  assert.ok(Math.abs(f.npLyricsTime(100) - 110) < 1e-9);
  assert.ok(Math.abs(updatedTime - 110) < 1e-9);
});
