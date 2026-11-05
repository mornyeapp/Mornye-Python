# Changelog

## Unreleased

### Fixed

- Translate native Qobuz quality codes to canonical quality IDs when declared by a JavaScript extension, preventing Hi-Res requests from silently selecting CD quality.
- Restore SoundCloud URL metadata resolution by adapting JavaScript `handleUrl` track, collection, and artist responses to the downloader's `TrackMetadata` contract.
- Restore live JavaScript download progress when options follow the callback, safely deliver asynchronous callbacks from the Node reader thread, and preserve received byte counts when the transfer total is unknown.
