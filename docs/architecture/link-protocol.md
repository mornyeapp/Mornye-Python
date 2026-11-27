# Mornye Link: what the computer answers beyond protocol 1

For the Mornye app (the phone). Protocol 1 is the one the app already speaks
(`link_desktop_ops.dart`, `link_desktop_host.dart`): pairing, `/rest/*`
(OpenSubsonic), the push socket, and the player ops `state`, `command`,
`handoff/*`, `queue/add`, `player/details`, `resolve`, `PUT files/…`.
Everything below is additive. An app that ignores it keeps working; an app
that uses it checks `features` first.

`features` is in `GET /mornye/v1/info` and in the socket's `welcome`:

| Feature | What it adds |
| --- | --- |
| `connect.devices` | The account's devices; "Play on" from the phone |

Ops are `/mornye/v1/<op>` with `Authorization: Bearer <apiKey>`, JSON in and
out, and the usual errors (`{"error": "<code>", "message": "…"}`: 400
`invalid`, 404 `not_found`, 503 `unavailable`). A `GET` takes its arguments
as query parameters.

## `connect.devices`: choose where it plays

The computer keeps one list of devices per account: the desktop window,
browser tabs, the server's own speaker, cast receivers, every connected
phone. One of them is **active** (what plays). As in Spotify Connect,
picking another one moves playback there.

### `GET devices`

```json
{
  "activeId": "mac",
  "targetId": "mac",
  "devices": [
    {"id": "mac", "name": "MacBook", "kind": "window", "canPlay": true,
     "active": true, "self": false, "state": { "...": "a player.state" }},
    {"id": "phone:3f2a…", "name": "Pixel", "kind": "phone", "canPlay": true,
     "active": false, "self": true}
  ]
}
```

- `kind`: `window`, `browser`, `phone`, `cast`, `server`, `mornye` (another
  computer).
- `self`: the phone asking.
- `targetId`: the device this phone's ops reach when they name none. It is
  the active device, unless that is this phone; then the computer.
- `state` is present when the device reported one (same shape as
  `player.state`).

### Socket frame `devices`

The same body with `"type": "devices"`, sent after the first
`player.state` that follows `welcome`, and again whenever a device joins,
leaves, is renamed, or another one becomes active.

### `POST transfer`

```json
{"target": "phone:9c1e…", "playing": true}
```

"Play on": what plays now (its queue and position) moves to `target`, and
the device that played is paused. `playing` is optional (default: as it
was). The target may be any device in `devices`, this phone included: a
phone picked as target gets the `command` frame `play_queue`, exactly as for
"Continue on phone". Answers the new `devices` body. `404 not_found` for an
unknown id, `400 invalid` for a device that cannot play, `503 unavailable`
when nothing plays.

### `target` on the player ops

`GET state`, `POST command`, `POST handoff/play`, `GET handoff/current` and
`POST queue/add` take an optional `"target": "<device id>"`. Without it they
reach `targetId`, as before. With it:

- `command`: the command goes to that device (`{"action": "pause",
  "target": "tab-1"}`).
- `handoff/play`: the phone's queue plays on that device;
  `handoff/append` then follows to the same device.
- `state`, `handoff/current`: that device's state.

### A phone that was handed playback

When playback moves to phone B, phone B is the active device. Every other
phone then sees phone B in `state` / `player.state` and controls it with
`command`: the computer forwards commands to phone B as `command` frames,
which it already handles. Phone B itself keeps seeing the computer.

## Word-by-word lyrics (no flag)

`getLyricsBySongId` follows OpenSubsonic `songLyrics` version 2: with
`enhanced=true` the answer adds `kind: "main"` and, for word-synced lyrics,
`cueLine` (word times in ms, `byteStart`/`byteEnd` inclusive UTF-8 offsets
into the line). `getOpenSubsonicExtensions` lists `songLyrics` `[1, 2]`.
Without `enhanced` the answer is the version 1 one.
