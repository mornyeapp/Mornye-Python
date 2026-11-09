# Changelog

## Unreleased

### Added

- **Direct lyrics sources**: `kugou`, `qq` and `genius` in `--lyrics-providers` now actually fetch lyrics, straight from the services. They had been offered by the CLI and the TUI without anything answering to those names. KuGou and QQ Music return line-synced LRC; Genius returns plain lyrics with every verse and chorus container kept, and uses the official API when `MORNYE_GENIUS_TOKEN` is set. All three are opt-in and also appear in the web GUI and the health check.
- **CUE sheet splitting**: `mornye --split-cue PATH` splits a single-file album (FLAC/WAV/APE…) plus its `.cue` into one tagged file per track (`--cue-format flac|alac|wav`, `--cue-output`, `--cue-dry-run`, `--cue-overwrite`, `--cue-encoding`). Cuts are frame-accurate (1/75 s); the source is never modified.
- **Persistent extension storage**: what a JavaScript extension saves with `storage.set` (tokens, sessions, device ids) now survives restarts. It is encrypted per extension under `~/.mornye/extension_storage`, with the key in the OS credential store when available (otherwise a 0600 key file). Several runtimes of the same extension add their own keys without overwriting each other's.

### Fixed

- **Download timeout no longer kills transfers that are still progressing**: `timeout_s` used to cap the whole provider attempt, so a large Hi-Res file on a slow connection was cancelled while data was still arriving and the fallback started over on the next provider. It now bounds the time to the first byte and, after that, only stalls with no progress.
- Translate native Qobuz quality codes to canonical quality IDs when declared by a JavaScript extension, preventing Hi-Res requests from silently selecting CD quality.
- Restore SoundCloud URL metadata resolution by adapting JavaScript `handleUrl` track, collection, and artist responses to the downloader's `TrackMetadata` contract.
- Restore live JavaScript download progress when options follow the callback, safely deliver asynchronous callbacks from the Node reader thread, and preserve received byte counts when the transfer total is unknown.
