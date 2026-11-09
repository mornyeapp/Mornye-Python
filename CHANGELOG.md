# Changelog

## Unreleased

### Added

- **Persistent extension storage**: what a JavaScript extension saves with `storage.set` (tokens, sessions, device ids) now survives restarts. It is encrypted per extension under `~/.mornye/extension_storage`, with the key in the OS credential store when available (otherwise a 0600 key file). Several runtimes of the same extension add their own keys without overwriting each other's.

### Fixed

- **Download timeout no longer kills transfers that are still progressing**: `timeout_s` used to cap the whole provider attempt, so a large Hi-Res file on a slow connection was cancelled while data was still arriving and the fallback started over on the next provider. It now bounds the time to the first byte and, after that, only stalls with no progress.
- Translate native Qobuz quality codes to canonical quality IDs when declared by a JavaScript extension, preventing Hi-Res requests from silently selecting CD quality.
- Restore SoundCloud URL metadata resolution by adapting JavaScript `handleUrl` track, collection, and artist responses to the downloader's `TrackMetadata` contract.
- Restore live JavaScript download progress when options follow the callback, safely deliver asynchronous callbacks from the Node reader thread, and preserve received byte counts when the transfer total is unknown.
