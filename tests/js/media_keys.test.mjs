// Media keys (play/pause, next, previous) reach the player three ways: the
// Media Session, mpv (the native engine's 'media' event) and a keydown.
import test from 'node:test';
import assert from 'node:assert/strict';
import { pick } from './load.mjs';

const f = pick(
  ['playerMediaKeyAction', 'playerMediaFamily', 'playerMediaIsEcho', 'playerNativeEngine'],
  { PLAYER_MEDIA_ECHO_MS: 400, performance: { now: () => 0 } },
);

test('media keys from a keydown map to Media Session actions', () => {
  assert.equal(f.playerMediaKeyAction('MediaPlayPause'), 'toggle');
  assert.equal(f.playerMediaKeyAction('MediaPlay'), 'play');
  assert.equal(f.playerMediaKeyAction('MediaPause'), 'pause');
  assert.equal(f.playerMediaKeyAction('MediaStop'), 'stop');
  assert.equal(f.playerMediaKeyAction('MediaTrackNext'), 'nexttrack');
  assert.equal(f.playerMediaKeyAction('MediaTrackPrevious'), 'previoustrack');
  // Volume stays with the system; ordinary keys are not media keys.
  assert.equal(f.playerMediaKeyAction('AudioVolumeUp'), null);
  assert.equal(f.playerMediaKeyAction('AudioVolumeDown'), null);
  assert.equal(f.playerMediaKeyAction(' '), null);
});

test('one press heard from two sources is handled once', () => {
  const last = { family: 'playpause', source: 'key', at: 1000 };
  // The system turns the same toggle into "pause" a moment later: an echo.
  assert.equal(f.playerMediaIsEcho(last, 'pause', 'session', 1100), true);
  assert.equal(f.playerMediaIsEcho(last, 'play', 'native', 1100), true);
  // A second press on the same key is a new press.
  assert.equal(f.playerMediaIsEcho(last, 'toggle', 'key', 1100), false);
  // Long after, or another key: not an echo.
  assert.equal(f.playerMediaIsEcho(last, 'pause', 'session', 1500), false);
  assert.equal(f.playerMediaIsEcho(last, 'nexttrack', 'session', 1100), false);
  const next = { family: 'nexttrack', source: 'session', at: 0 };
  assert.equal(f.playerMediaIsEcho(next, 'nexttrack', 'key', 10), true);
});

test('the native engine passes a media key from mpv on to the page', () => {
  const api = new Proxy({}, { get: () => () => Promise.resolve({ status: 'ok' }) });
  const engine = f.playerNativeEngine(api, () => 0);
  const seen = [];
  engine.attach((type, detail) => seen.push([type, detail]));
  engine.receive({ type: 'media', action: 'nexttrack' });
  engine.receive({ type: 'media', action: 'toggle' });
  assert.deepEqual(JSON.parse(JSON.stringify(seen)), [
    ['media', { action: 'nexttrack' }],
    ['media', { action: 'toggle' }],
  ]);
});
