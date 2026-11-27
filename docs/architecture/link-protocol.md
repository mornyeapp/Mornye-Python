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
| `playlists` | The Player's playlists, read and edited from the phone |

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

## `playlists`: the same playlists on every device

These are the Player page's playlists (not Subsonic's `getPlaylists`, which
is a separate list kept for other Subsonic apps). A playlist may hold songs
that are not downloaded yet (imported from a provider): they have no
`songId`.

Errors as above, plus `409 conflict` (see `baseUpdatedMs`).

### A track

A TrackRef, as in the player ops, plus three fields:

```json
{"title": "Alpha", "artist": "Ann", "album": "A", "durationMs": 200000,
 "isrc": "USAAA0000001", "matchKey": "…", "songId": "so-…", "serverId": "…",
 "key": "so-…", "downloaded": true, "sourceUrl": "https://…"}
```

- `songId` / `serverId`: only for a song in this library; play it with
  `/rest/stream?id=<songId>` or hand it over like any queue entry.
- `key`: opaque. Sending `{"key": "…"}` back keeps that entry exactly.
- `downloaded`: false for a song the library does not have yet.
- `sourceUrl`: the song's provider link, when the import knew it.

### `GET playlists`

```json
{"playlists": [{"id": "…", "name": "Mix", "description": "",
  "trackCount": 42, "downloadedCount": 40, "durationMs": 9876000,
  "createdMs": 1764240000000, "updatedMs": 1764243600000,
  "hasCover": true, "followsUrl": "", "syncedMs": 0}]}
```

Newest change first. `updatedMs` is the playlist's version: keep it to know
when to reload, and to send as `baseUpdatedMs`. `followsUrl` is the provider
playlist it follows (the computer adds the songs added there); "" if none.

### `GET playlists/get?id=…`

`{"playlist": {…the summary…, "tracks": [track, …]}}`

### `GET playlists/cover?id=…`

`{"cover": "data:image/jpeg;base64,…"}` ("" when there is none). Kept out
of the list because covers are large; reload it when `updatedMs` changes.

### `POST playlists/create`

```json
{"name": "Road trip", "description": "", "cover": "data:image/…",
 "tracks": [{"title": "Beta", "artist": "Bob", "isrc": "…"}]}
```

Only `name` is required. Each track is a TrackRef: a song this library has
(by `songId`, ISRC, then title/artist/duration) is stored as that file,
anything else as not downloaded. The cover is re-encoded to a 512 px JPEG.
Answers `{"playlist": {…with tracks…}}`.

### `POST playlists/update`

```json
{"id": "…", "baseUpdatedMs": 1764243600000, "name": "New name",
 "tracks": [{"key": "so-…"}, {"key": "remote:isrc:…"}, {"title": "New song"}]}
```

Every field but `id` is optional and only what is sent changes:

- `name`, `description`.
- `cover`: a data URL replaces it, "" removes it.
- `tracks`: the whole new list, in order (add, remove and reorder are all
  this). Entries the phone got send their `key`; new ones are TrackRefs.
- `baseUpdatedMs`: the `updatedMs` the edit was made on. When the playlist
  changed since (on the computer, or on another phone), nothing is written
  and the answer is `409 conflict`: reload it, apply the edit again, resend.
  Without it the edit wins.

Answers `{"playlist": {…with tracks…}}`.

### `POST playlists/delete`

`{"id": "…"}` → `{"ok": true}`.

### Socket frame `playlists.changed`

```json
{"type": "playlists.changed", "ids": ["…"], "deleted": false}
```

After every change to the account's playlists, wherever it was made (the
Player page, this phone, another phone, the computer following a provider
playlist). `deleted: true` when they are gone. Reload what is shown.

## Another computer as a Link client (no flag)

A computer running Mornye can pair with another one exactly as the phone
does (PIN, `POST pair`, then the socket); see
`mornye/core/connect/link_peers.py`. Its `hello` carries
`"kind": "mornye"`, so the computer it connects to lists it in `devices`
with kind `mornye` (a computer), not `phone`. Nothing changes for the app,
except that `devices` may now hold `mornye` entries: show them as computers.
"Play on" works for them the same way (`transfer`).

## Word-by-word lyrics (no flag)

`getLyricsBySongId` follows OpenSubsonic `songLyrics` version 2: with
`enhanced=true` the answer adds `kind: "main"` and, for word-synced lyrics,
`cueLine` (word times in ms, `byteStart`/`byteEnd` inclusive UTF-8 offsets
into the line). `getOpenSubsonicExtensions` lists `songLyrics` `[1, 2]`.
Without `enhanced` the answer is the version 1 one.
