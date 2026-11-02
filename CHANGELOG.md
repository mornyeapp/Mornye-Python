# Changelog

## Unreleased

### Fixed

- Translate native Qobuz quality codes to canonical quality IDs when declared by a JavaScript extension, preventing Hi-Res requests from silently selecting CD quality.
- Restore live JavaScript download progress when options follow the callback, safely deliver asynchronous callbacks from the Node reader thread, and preserve received byte counts when the transfer total is unknown.
