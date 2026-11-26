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
    const app = pick(['downloadAll', 'detectUrlType', 'isSongUrl'], {
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
    $: () => output,
    playerEngine: { currentTime: 100 },
    npUpdate: (time) => { updatedTime = time; },
  };
  const f = pick(['npSetSync', 'npLyricsTime'], globals);

  f.npSetSync('speed', '1.1');

  assert.ok(Math.abs(f.npLyricsTime(100) - 110) < 1e-9);
  assert.ok(Math.abs(updatedTime - 110) < 1e-9);
});

test('parseLyrics shows line-only enhanced LRC line by line, real word timing word by word', () => {
  const { parseLyrics } = pick(['parseLyrics']);
  const lineOnly = '[by:Apple Music]\n[00:04.24]<00:04.24>One summer night<00:08.12>\n[00:08.52]<00:08.52>We ran away<00:14.77>';
  const a = parseLyrics(lineOnly);
  assert.equal(a.wordSynced, false);
  assert.equal(a.lines[0].text, 'One summer night');
  assert.equal(a.lines[0].words, null);
  const b = parseLyrics(lineOnly + '\n[00:15.47]<00:15.47>Laughing, <00:16.10>we<00:16.40>');
  assert.equal(b.wordSynced, true);
  assert.equal(b.lines[2].words.length, 2);
});

test('parseLyrics reads .mlrc translation tags by time, readable and base64', () => {
  const { parseLyrics } = pick(['parseLyrics', 'lyricSupplement'], { atob, TextDecoder, Uint8Array });
  const mlrc = '[x-mlrc:2]\n[00:01.00]Hola\n[x-romanized:1000:o-la]\n[x-translated:1000:Hello\\nthere]\n'
    + '[00:02.50]Mundo\n[x-translated-words:2500:<00:02.50>World<00:03.00>]\n[x-translated:2500:World]';
  const a = parseLyrics(mlrc);
  assert.equal(a.lines.length, 2);
  assert.equal(a.lines[0].tr, 'Hello\nthere');
  assert.equal(a.lines[0].rom, 'o-la');
  assert.equal(a.lines[1].tr, 'World');
  const legacy = parseLyrics(`[00:01.00]Hola\n[x-translation:1000:${Buffer.from('Hellò').toString('base64')}]\n[x-translation:9:%%%]`);
  assert.equal(legacy.lines[0].tr, 'Hellò');
  const plain = parseLyrics('[x-mlrc:2]\nHola\n[x-translated:0:Hello]\nMundo');
  assert.equal(plain.lines.length, 2);
  assert.equal(plain.lines[0].tr, 'Hello');
});

// ── Personal Home and the listening record (Mornye mobile 5.4.5) ──────────
const home = pick(['homeStatusText', 'quickPlayQueue', 'moveInList', 'homeGenreKey', 'homeFolderOf',
  'listenNew', 'listenAdvance', 'listenPiece']);

test('homeStatusText says what arrived and what is downloading', () => {
  assert.equal(home.homeStatusText({ added: 0 }, 0), '');
  assert.equal(home.homeStatusText({ added: 1, since_yesterday: true }, 0), '1 song added since yesterday');
  assert.equal(home.homeStatusText({ added: 4, since_yesterday: false }, 2), '4 songs added since your last visit · 2 downloading');
});

test('quickPlayQueue plays the tapped song with the other eight after it', () => {
  const songs = ['a', 'b', 'c', 'd'];
  assert.deepEqual([...home.quickPlayQueue(songs, 2)], ['c', 'a', 'b', 'd']);
  assert.deepEqual([...home.quickPlayQueue(songs, 9)], []);
});

test('moveInList moves within bounds only', () => {
  assert.deepEqual([...home.moveInList([1, 2, 3], 0, 1)], [2, 1, 3]);
  assert.deepEqual([...home.moveInList([1, 2, 3], 0, -1)], [1, 2, 3]);
});

test('homeGenreKey folds like the backend; homeFolderOf takes the parent', () => {
  assert.equal(home.homeGenreKey('Hip-Hop/Rap'), 'hip hop rap');
  assert.equal(home.homeGenreKey('Électro'), 'electro');
  assert.equal(home.homeFolderOf('/m/A/b.flac'), '/m/A');
  assert.equal(home.homeFolderOf('C:\\m\\b.flac'), 'C:\\m');
});

test('listening: only playing time counts, a play after 30 s, gaps capped', () => {
  const s = home.listenNew({ path: '/x.flac', title: 'T', artist: 'A' }, 'FLAC 24-bit / 96 kHz');
  home.listenAdvance(s, 0, true);
  home.listenAdvance(s, 20000, true);       // a 20 s gap counts 3 s
  assert.equal(s.ms, 3000);
  for (let t = 21000; t <= 50000; t += 1000) home.listenAdvance(s, t, true);
  home.listenAdvance(s, 60000, false);     // paused: the 10 s gap counts 3 s, then nothing
  home.listenAdvance(s, 90000, false);
  assert.equal(s.ms, 36000);
  const piece = home.listenPiece(s);
  assert.equal(piece.ms, 36000);
  assert.equal(piece.play, true);
  assert.equal(piece.outcome, '');
  assert.equal(home.listenPiece(s), null, 'nothing left to send');
});

test('listening: the end of a song says played through or skipped', () => {
  const skipped = home.listenNew({ title: 'T' }, '');
  skipped.duration = 200;
  home.listenAdvance(skipped, 0, true);
  home.listenAdvance(skipped, 2000, false);
  assert.equal(home.listenPiece(skipped, true).outcome, 'skipped');
  const through = home.listenNew({ title: 'T' }, '');
  through.duration = 4;
  home.listenAdvance(through, 0, true);
  home.listenAdvance(through, 2500, false);
  assert.equal(home.listenPiece(through, true).outcome, 'listened');
});

const prof = pick(['profileShare', 'profileChangeText']);

test('profileShare rounds a part of a whole; nothing of nothing', () => {
  assert.equal(prof.profileShare(1, 3), '33%');
  assert.equal(prof.profileShare(5, 0), '0%');
});

test('profileChangeText compares with the 30 days before', () => {
  assert.equal(prof.profileChangeText(null), 'nothing to compare with yet');
  assert.equal(prof.profileChangeText(40), '+40% on the 30 days before');
  assert.equal(prof.profileChangeText(-12), '−12% on the 30 days before');
  assert.equal(prof.profileChangeText(0), 'as much as the 30 days before');
});

// ── Cast engine (DLNA/FCast, from Mornye mobile 5.4.5) ─────────────────────
test('castStateFromStatus reads the receiver for the loaded song only', () => {
  const { castStateFromStatus, castDeviceLabel } = pick(['castStateFromStatus', 'castDeviceLabel']);
  assert.equal(castStateFromStatus({ connected: false }, 1).lost, true);
  const s = castStateFromStatus({ connected: true, token: 2, state: 2, position: 1500, duration: 200000, finished: true }, 2);
  assert.equal(s.playing, true);
  assert.equal(s.finished, true);
  assert.equal(s.time, 1.5);
  assert.equal(s.duration, 200);
  const other = castStateFromStatus({ connected: true, token: 1, state: 1, finished: true }, 2);
  assert.equal(other.ours, false);
  assert.equal(other.finished, false);
  assert.equal(castDeviceLabel({ name: 'TV', kind: 'dlna', model: 'Bravia' }), 'TV · DLNA · Bravia');
  assert.equal(castDeviceLabel({ name: 'Box', kind: 'fcast', model: 'FCast' }), 'Box · FCast');
});

test('the cast engine loads, follows the receiver and ends the song', async () => {
  const calls = [];
  let lost = 0;
  const api = {
    cast_load: (...a) => { calls.push(['load', ...a]); return { status: 'ok', cast: { token: 5 } }; },
    cast_command: (...a) => { calls.push(a); return { status: 'ok' }; },
  };
  let now = 0;
  const { playerCastEngine } = pick(['castStateFromStatus', 'playerCastEngine'], {
    setInterval: () => 1, clearInterval: () => {}, setTimeout: (fn) => { fn(); return 1; }, clearTimeout: () => {},
    stopCasting: () => { lost++; }, Promise,
  });
  const engine = playerCastEngine(api, () => now);
  const events = [];
  engine.attach((type) => events.push(type));
  engine.load('/m/a.flac', undefined, { title: 'T', start: 12 });
  await Promise.resolve(); await Promise.resolve();
  assert.equal(calls[0][0], 'load');
  assert.equal(calls[0][1], '/m/a.flac');
  assert.equal(calls[0][3], 12);
  assert.equal(engine.paused, false);
  assert.ok(events.includes('play'));
  now = 2000;
  assert.equal(engine.currentTime, 14);
  engine.receive({ connected: true, token: 5, state: 2, position: 30000, duration: 100000 });
  assert.equal(engine.duration, 100);
  assert.ok(events.includes('loadedmetadata'));
  engine.volume = 0.3;
  assert.deepEqual([...calls.at(-1)], ['volume', 0.3]);
  engine.receive({ connected: true, token: 5, state: 1, finished: true, position: 100000, duration: 100000 });
  assert.ok(events.includes('ended'));
  assert.equal(engine.paused, true);
  engine.receive({ connected: false });
  assert.equal(lost, 1);
});

test('Connect: queue window, repeat names and where a handed-over queue starts', () => {
  const c = pick(['connectWindow', 'connectRepeatOut', 'connectRepeatIn', 'connectStartIndex']);
  assert.deepEqual([...c.connectWindow(50, 10)], [0, 50]);
  assert.deepEqual([...c.connectWindow(1000, 500)], [400, 600]);
  assert.deepEqual([...c.connectWindow(1000, 999)], [800, 1000]);
  assert.deepEqual([...c.connectWindow(1000, -1)], [0, 200]);
  assert.equal(c.connectRepeatOut('off'), 'none');
  assert.equal(c.connectRepeatIn('none'), 'off');
  assert.equal(c.connectRepeatIn('one'), 'one');
  assert.equal(c.connectStartIndex([{}, null, {}], 1), 2);
  assert.equal(c.connectStartIndex([{}, null, null], 2), 0);
  assert.equal(c.connectStartIndex([null, null], 0), -1);
});

test('Connect: a remote position moves on while playing, never past the end', () => {
  const { connectPositionAt } = pick(['connectPositionAt']);
  assert.equal(connectPositionAt({ positionMs: 1000, playing: true, durationMs: 5000 }, 100, 1100), 2000);
  assert.equal(connectPositionAt({ positionMs: 1000, playing: true, durationMs: 5000 }, 0, 99999), 5000);
  assert.equal(connectPositionAt({ positionMs: 1000, playing: false }, 0, 5000), 1000);
  assert.equal(connectPositionAt(null, 0, 0), 0);
});

test('Connect: default device names from the user agent', () => {
  const { connectDefaultName } = pick(['connectDefaultName']);
  assert.equal(connectDefaultName('Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit Version/17 Mobile Safari/604.1'), 'Safari on iPhone');
  assert.equal(connectDefaultName('Mozilla/5.0 (Windows NT 10.0) AppleWebKit Chrome/120 Safari/537 Edg/120'), 'Edge on Windows');
  assert.equal(connectDefaultName(''), 'Browser');
});

test('playerRemoteEngine shows the remote state and turns controls into commands', () => {
  const sent = [];
  let now = 0;
  const { playerRemoteEngine } = pick(['connectPositionAt', 'playerRemoteEngine'], {
    setInterval: () => 1, clearInterval: () => {}, setTimeout: (fn) => { fn(); return 0; }, clearTimeout: () => {},
    Promise,
  });
  const engine = playerRemoteEngine((action, args) => sent.push([action, args]), () => now);
  const events = [];
  engine.attach((type) => events.push(type));
  engine.receive({ track: { title: 'A' }, playing: true, positionMs: 10000, durationMs: 60000, volume: 0.4 }, 0);
  assert.equal(engine.paused, false);
  assert.equal(engine.volume, 0.4);
  assert.ok(events.includes('play') && events.includes('loadedmetadata'));
  now = 2000;
  assert.equal(engine.currentTime, 12);
  engine.pause();
  engine.currentTime = 30;
  engine.load('/m/b.flac', undefined, { index: 3 });
  engine.volume = 0.5;
  assert.deepEqual(sent.map(s => s[0]), ['pause', 'seek', 'jump', 'volume']);
  assert.equal(JSON.stringify(sent[1][1]), '{"positionMs":30000}');
  assert.equal(JSON.stringify(sent[2][1]), '{"index":3}');
  sent.length = 0;
  engine.silent = true;
  engine.load('/m/c.flac', undefined, { index: 4 });
  engine.volume = 1;
  engine.play();
  assert.equal(sent.length, 0);
  engine.silent = false;
  engine.receive({ track: { title: 'A' }, playing: false, positionMs: 31000, durationMs: 60000 }, now);
  assert.equal(engine.paused, true);
  engine.play();
  assert.equal(JSON.stringify(sent), '[["play",{}]]');
});
