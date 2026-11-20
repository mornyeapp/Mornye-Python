// Contract of the playback-engine adapter (docs/native-player-plan.md, phase 0).
//
// The player reaches the audio only through `playerEngine`; these tests pin
// the part of HTMLMediaElement it relies on, with the browser engine over a
// stand-in <audio>, and the engine swap a native engine will use.
import test from 'node:test';
import assert from 'node:assert/strict';
import { pick, source } from './load.mjs';

const EVENTS = ['play', 'pause', 'timeupdate', 'ended', 'loadedmetadata', 'seeked', 'error'];
const quiet = { error() {} };
// Objects made inside the vm context have that context's prototypes.
const plain = (v) => JSON.parse(JSON.stringify(v));
const f = pick(['playerBrowserEngine', 'createPlayerEngine'], { PLAYER_ENGINE_EVENTS: EVENTS, console: quiet });

// Enough of an <audio> element: attributes, properties, listeners.
function fakeAudio() {
  const attrs = {};
  const handlers = {};
  return {
    paused: true, currentTime: 0, duration: NaN, volume: 1, muted: false, playbackRate: 1,
    loads: 0,
    get src() { return attrs.src || ''; },
    set src(v) { attrs.src = v; },
    getAttribute: (k) => attrs[k] ?? null,
    removeAttribute: (k) => { delete attrs[k]; },
    addEventListener(type, fn) { (handlers[type] ||= []).push(fn); },
    fire(type) { for (const fn of handlers[type] || []) fn(); },
    play() { this.paused = false; this.fire('play'); return Promise.resolve(); },
    pause() { this.paused = true; this.fire('pause'); },
    load() { this.loads++; },
  };
}

// An engine that records what it is told, as a native engine would be driven.
function fakeEngine(kind) {
  let emit = null;
  return {
    kind, hasSource: false, paused: true, currentTime: 0, duration: 0,
    volume: 1, muted: false, playbackRate: 1, cleared: 0, attachCount: 0,
    attach(fn) { emit = fn; this.attachCount++; },
    emit(type, detail) { emit(type, detail); },
    load() { this.hasSource = true; },
    clear() { this.hasSource = false; this.cleared++; },
    play() { this.paused = false; return Promise.resolve(); },
    pause() { this.paused = true; },
  };
}

test('browser engine: load, play, pause and clear act on the element', async () => {
  const el = fakeAudio();
  const engine = f.createPlayerEngine(f.playerBrowserEngine(el));
  assert.equal(engine.kind, 'browser');
  assert.equal(engine.hasSource, false);

  engine.load('/api/media?path=a.flac');
  assert.equal(el.src, '/api/media?path=a.flac');
  assert.equal(engine.hasSource, true);

  await engine.play();
  assert.equal(engine.paused, false);
  engine.pause();
  assert.equal(engine.paused, true);

  engine.clear();
  assert.equal(engine.hasSource, false);
  assert.equal(el.getAttribute('src'), null);
  assert.equal(el.loads, 1);
});

test('browser engine: time, volume, mute and rate pass through both ways', () => {
  const el = fakeAudio();
  const engine = f.createPlayerEngine(f.playerBrowserEngine(el));
  el.duration = 200;
  engine.currentTime = 42;
  engine.volume = 0.3;
  engine.muted = true;
  engine.playbackRate = 1.25;
  assert.equal(el.currentTime, 42);
  assert.equal(el.volume, 0.3);
  assert.equal(el.muted, true);
  assert.equal(el.playbackRate, 1.25);
  assert.equal(engine.duration, 200);
  el.currentTime = 50;
  assert.equal(engine.currentTime, 50);
});

test('every media event of the element reaches the adapter listeners', () => {
  const el = fakeAudio();
  const engine = f.createPlayerEngine(f.playerBrowserEngine(el));
  const seen = [];
  for (const type of EVENTS) engine.addEventListener(type, () => seen.push(type));
  for (const type of EVENTS) el.fire(type);
  assert.deepEqual(seen, EVENTS);
});

test('a failing listener does not stop the others; removed listeners are not called', () => {
  const el = fakeAudio();
  const engine = f.createPlayerEngine(f.playerBrowserEngine(el));
  const calls = [];
  engine.addEventListener('ended', () => { throw new Error('boom'); });
  engine.addEventListener('ended', () => calls.push('second'));
  const gone = () => calls.push('removed');
  engine.addEventListener('ended', gone);
  engine.removeEventListener('ended', gone);
  el.fire('ended');
  assert.deepEqual(calls, ['second']);
});

test('swapping engines keeps listeners, carries volume, and silences the old one', () => {
  const a = fakeEngine('browser');
  const b = fakeEngine('native');
  const engine = f.createPlayerEngine(a);
  const seen = [];
  engine.addEventListener('timeupdate', () => seen.push(engine.kind));

  engine.volume = 0.4;
  engine.muted = true;
  engine.load('x');
  engine.use(b);

  assert.equal(engine.kind, 'native');
  assert.equal(a.cleared, 1, 'the old engine is emptied');
  assert.equal(b.volume, 0.4);
  assert.equal(b.muted, true);

  a.emit('timeupdate'); // late event from the old engine: dropped
  b.emit('timeupdate');
  assert.deepEqual(seen, ['native']);

  engine.use(a); // back again: attached once, not twice
  engine.use(b);
  assert.equal(a.attachCount, 1);
  assert.equal(b.attachCount, 1);
});

test('the player reaches the <audio> element only through the adapter', () => {
  const app = source('app.js');
  const uses = app.match(/\$\('player-audio'\)/g) || [];
  assert.equal(uses.length, 1, 'only the line that builds the browser engine');
  assert.match(app, /const playerBrowser = playerBrowserEngine\(\$\('player-audio'\)\);/);
  assert.match(app, /const playerEngine = createPlayerEngine\(playerBrowser\);/);
});

test('event details reach the listeners', () => {
  const a = fakeEngine('native');
  const engine = f.createPlayerEngine(a);
  let got = null;
  engine.addEventListener('advanced', (d) => { got = d; });
  a.emit('advanced', { path: '/m/b.flac' });
  assert.deepEqual(got, { path: '/m/b.flac' });
});

// ── The native engine ──────────────────────────────────────────────────────

const n = pick(['playerNativeEngine'], { console: quiet, performance: { now: () => 0 } });

function fakeApi() {
  const calls = [];
  const reply = (name) => (...args) => { calls.push([name, ...args]); return Promise.resolve({ status: 'ok' }); };
  return {
    calls,
    native_player_load: reply('load'),
    native_player_set_next: reply('next'),
    native_player_command: reply('command'),
  };
}

function nativeRig() {
  let t = 0;
  const api = fakeApi();
  const eng = n.playerNativeEngine(api, () => t);
  const events = [];
  eng.attach((type, d) => events.push(d ? [type, d] : [type]));
  const flush = () => new Promise((r) => setTimeout(r, 0));
  return { api, eng, events, flush, tick: (ms) => { t += ms; } };
}

test('native engine: load sends the path and the next one, in call order', async () => {
  const { api, eng, flush } = nativeRig();
  eng.load('/m/a.flac', '/m/b.flac');
  eng.pause();
  eng.currentTime = 30;
  await flush(); await flush(); await flush(); await flush();
  assert.deepEqual(api.calls, [
    ['load', '/m/a.flac', '/m/b.flac'],
    ['command', 'pause', null],
    ['command', 'seek', 30],
  ]);
  assert.equal(eng.hasSource, true);
});

test('native engine: position is extrapolated between updates, at the speed', () => {
  const { eng, tick } = nativeRig();
  eng.load('/m/a.flac');
  eng.receive({ type: 'loadedmetadata', duration: 100 });
  eng.receive({ type: 'timeupdate', time: 10 });
  tick(500);
  assert.equal(eng.currentTime, 10.5);
  eng.playbackRate = 2;
  tick(500);
  assert.equal(eng.currentTime, 11.5);
  eng.pause();
  tick(5000);
  assert.equal(eng.currentTime, 11.5, 'paused: the position holds');
  eng.play();
  tick(1_000_000);
  assert.equal(eng.currentTime, 100, 'never past the end');
});

test('native engine: a late update never moves the position back a little', () => {
  // On a network share the update reaches the page behind the extrapolated
  // position; the synced lyrics read it every frame and used to step back.
  const { eng, tick } = nativeRig();
  eng.load('/Volumes/share/a.flac');
  eng.receive({ type: 'loadedmetadata', duration: 100 });
  eng.receive({ type: 'timeupdate', time: 10 });
  tick(250);
  assert.equal(eng.currentTime, 10.25);
  eng.receive({ type: 'timeupdate', time: 10.1 }); // late: mpv said 10.1
  assert.equal(eng.currentTime, 10.25, 'held, not back to 10.1');
  tick(100);
  assert.equal(eng.currentTime, 10.25, 'holds until mpv catches up');
  tick(100);
  assert.ok(Math.abs(eng.currentTime - 10.3) < 1e-9, 'then follows mpv again');
  let last = eng.currentTime;
  for (let i = 0; i < 40; i++) { // jittery updates, always a bit behind
    tick(250);
    eng.receive({ type: 'timeupdate', time: last + 0.25 - (i % 3) * 0.08 });
    tick(16);
    assert.ok(eng.currentTime >= last, `step ${i}: ${eng.currentTime} < ${last}`);
    last = eng.currentTime;
  }
});

test('native engine: a real gap, a seek or a pause still sets the position', () => {
  const { eng, tick } = nativeRig();
  eng.load('/m/a.flac');
  eng.receive({ type: 'loadedmetadata', duration: 100 });
  eng.receive({ type: 'timeupdate', time: 10 });
  tick(3000); // mpv stalled while the page went on to 13
  eng.receive({ type: 'timeupdate', time: 10.5 });
  assert.equal(eng.currentTime, 10.5, 'over a second behind: follow mpv');
  tick(250);
  eng.receive({ type: 'timeupdate', time: 10.6 });
  eng.currentTime = 5;
  assert.equal(eng.currentTime, 5, 'a seek back is not held');
  eng.receive({ type: 'seeked', time: 5 });
  tick(250);
  eng.receive({ type: 'timeupdate', time: 5.2 });
  eng.pause();
  tick(1000);
  assert.equal(eng.currentTime, 5.25, 'paused where it was shown');
});

test('native engine: old positions after a seek are dropped until it lands', () => {
  const { eng, events, tick } = nativeRig();
  eng.load('/m/a.flac');
  eng.receive({ type: 'loadedmetadata', duration: 100 });
  eng.currentTime = 60;
  eng.receive({ type: 'timeupdate', time: 2 });
  assert.equal(eng.currentTime, 60);
  eng.receive({ type: 'seeked', time: 60 });
  tick(100);
  eng.receive({ type: 'timeupdate', time: 60.1 });
  assert.equal(eng.currentTime, 60.1);
  assert.ok(events.some(([t]) => t === 'seeked'));
});

test('native engine: a load is a play; backend play/pause only emits on a real change', () => {
  const { eng, events } = nativeRig();
  eng.load('/m/a.flac');
  eng.play(); // what loadPlayerTrack does next: nothing more to do
  eng.receive({ type: 'play', time: 0 }); // already playing after load
  eng.receive({ type: 'pause', time: 3 });
  eng.receive({ type: 'pause', time: 3 });
  assert.deepEqual(events.map(([t]) => t), ['play', 'pause']);
  assert.equal(eng.paused, true);
});

test('native engine: ended, then play opens the file again', async () => {
  const { api, eng, events, flush } = nativeRig();
  eng.load('/m/a.flac', '/m/b.flac');
  eng.receive({ type: 'loadedmetadata', duration: 100 });
  eng.receive({ type: 'ended' });
  assert.deepEqual(events.map(([t]) => t).slice(-3), ['loadedmetadata', 'pause', 'ended'], 'as <audio>');
  assert.equal(eng.paused, true);
  assert.equal(eng.currentTime, 100);
  eng.currentTime = 0;
  eng.play();
  await flush(); await flush(); await flush(); await flush();
  assert.deepEqual(api.calls.filter(c => c[0] === 'load').map(c => c.slice(1)),
    [['/m/a.flac', '/m/b.flac'], ['/m/a.flac', null]]);
  assert.equal(eng.paused, false);
  assert.ok(events.some(([t]) => t === 'ended'));
});

test('native engine: a gapless change and the output label', () => {
  const { eng, events } = nativeRig();
  eng.load('/m/a.flac', '/m/b.flac');
  eng.receive({ type: 'output', label: 'Bitstream AC-3 5.1', bitstream: true });
  assert.equal(eng.output.label, 'Bitstream AC-3 5.1');
  eng.receive({ type: 'advanced', path: '/m/b.flac' });
  assert.equal(eng.output, null);
  assert.equal(eng.currentTime, 0);
  assert.deepEqual(plain(events.at(-1)), ['advanced', { path: '/m/b.flac' }]);
});

test('native engine: a failed load is an error event', async () => {
  const api = {
    native_player_load: () => Promise.resolve({ status: 'unavailable', error: 'libmpv was not found' }),
  };
  const eng = n.playerNativeEngine(api, () => 0);
  const events = [];
  eng.attach((type, d) => events.push([type, d]));
  eng.load('/m/a.flac');
  for (let i = 0; i < 5; i++) await new Promise((r) => setTimeout(r, 0));
  assert.deepEqual(plain(events.filter(([t]) => t === 'error')), [['error', { message: 'libmpv was not found' }]]);
  assert.equal(eng.paused, true);
});

// ── Queue, speed, A-B, shortcuts, statistics ───────────────────────────────

function upcoming(state) {
  return pick(['playerUpcomingIndex'], state).playerUpcomingIndex();
}

test('the track queued for a gapless change follows repeat and shuffle', () => {
  const base = { playerOrder: [2, 0, 1], playerIndex: 0, playerPos: 1, playerRepeat: 'off', playerShuffle: false };
  assert.equal(upcoming(base), 1);
  assert.equal(upcoming({ ...base, playerRepeat: 'one' }), 0);
  assert.equal(upcoming({ ...base, playerIndex: 1, playerPos: 2 }), -1);
  assert.equal(upcoming({ ...base, playerIndex: 1, playerPos: 2, playerRepeat: 'all' }), 2);
  assert.equal(upcoming({ ...base, playerIndex: 1, playerPos: 2, playerRepeat: 'all', playerShuffle: true }), -1);
  assert.equal(upcoming({ ...base, playerOrder: [] }), -1);
});

test('speed steps stay between 0.5x and 2x; zero resets', () => {
  const g = pick(['playerSpeedStep'], { PLAYER_SPEED_MIN: 0.5, PLAYER_SPEED_MAX: 2 });
  assert.equal(g.playerSpeedStep(1, 0.1), 1.1);
  assert.equal(g.playerSpeedStep(1.95, 0.1), 2);
  assert.equal(g.playerSpeedStep(0.55, -0.1), 0.5);
  assert.equal(g.playerSpeedStep(1.7, 0), 1);
});

test('A-B repeat: A, then B, then off; B before A is refused', () => {
  const g = pick(['playerAbNext']);
  const off = { a: null, b: null };
  const withA = g.playerAbNext(off, 10);
  assert.deepEqual(plain(withA), { a: 10, b: null });
  assert.deepEqual(plain(g.playerAbNext(withA, 20)), { a: 10, b: 20 });
  assert.deepEqual(plain(g.playerAbNext({ a: 10, b: 20 }, 30)), off);
  assert.deepEqual(plain(g.playerAbNext(withA, 5)), off);
});

test('shortcuts as in MPC-HC', () => {
  const g = pick(['playerShortcutAction']);
  const k = (key, mods = {}) => g.playerShortcutAction({ key, ...mods });
  assert.equal(k(' '), 'toggle');
  assert.equal(k('ArrowRight'), 'fwd5');
  assert.equal(k('ArrowLeft', { shiftKey: true }), 'back30');
  assert.equal(k('+'), 'volup');
  assert.equal(k('-'), 'voldown');
  assert.equal(k('m'), 'mute');
  assert.equal(k(']'), 'faster');
  assert.equal(k('['), 'slower');
  assert.equal(k('Backspace'), 'speed1');
  assert.equal(k('L'), 'ab');
  assert.equal(k('i'), 'stats');
  assert.equal(k('ArrowRight', { metaKey: true }), null);
  assert.equal(k('x'), null);
});

test('statistics rows show the output and what is active', () => {
  const g = pick(['playerStatsRows', 'fmtTime']);
  const rows = Object.fromEntries(g.playerStatsRows({
    path: '/m/Album/01 a.ac3', codec: 'ac3', codec_desc: 'ATSC A/52A (AC-3)',
    params: { format: 'floatp', samplerate: 48000, 'hr-channels': '5.1' },
    out_params: { format: 'spdif-ac3', samplerate: 48000, 'hr-channels': '5.1' },
    output: 'Bitstream AC-3 5.1', bitstream: true, ao: 'wasapi', device: 'wasapi/{x}',
    bitrate: 448000, cache_s: 12.34, speed: 1, delay_ms: 0, ab_loop: [61, 75],
    replaygain: 'no', filters: [],
  }, 'native'));
  assert.equal(rows.Engine, 'Native (libmpv)');
  assert.equal(rows.File, '01 a.ac3');
  assert.equal(rows.Output, 'Bitstream AC-3 5.1');
  assert.equal(rows.Bitstream, 'Yes');
  assert.equal(rows.Bitrate, '448 kbps');
  assert.equal(rows['A-B repeat'], '1:01 – 1:15');
  assert.equal(rows.Filters, 'None');
  assert.deepEqual(plain(g.playerStatsRows(null, 'browser')), [['Engine', 'Window (built-in)']]);
});

test('device names are told apart by driver only when they repeat', () => {
  const g = pick(['npDeviceLabel']);
  const devices = [
    { name: 'auto', description: 'Autoselect device' },
    { name: 'coreaudio/Built', description: 'MacBook Speakers' },
    { name: 'avfoundation/Built', description: 'MacBook Speakers' },
    { name: 'wasapi/{1}', description: 'Xonar S/PDIF' },
  ];
  assert.deepEqual(devices.map(d => g.npDeviceLabel(d, devices)), [
    'System default', 'MacBook Speakers (coreaudio)', 'MacBook Speakers (avfoundation)', 'Xonar S/PDIF',
  ]);
});

test('native engine: volume, mute and speed set before any file wait for the first load', async () => {
  const { api, eng, flush } = nativeRig();
  eng.volume = 0.3; eng.muted = true; eng.playbackRate = 1.5;
  await flush(); await flush();
  assert.equal(api.calls.length, 0, 'mpv is not started for a setting');
  eng.load('/m/a.flac');
  for (let i = 0; i < 10; i++) await flush();
  assert.deepEqual(plain(api.calls), [
    ['load', '/m/a.flac', null],
    ['command', 'volume', 0.3],
    ['command', 'mute', true],
    ['command', 'speed', 1.5],
  ]);
});
