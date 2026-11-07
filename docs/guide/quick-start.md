<!-- Extracted verbatim from README.md. The README had grown to 76 KB
     and 87 headings, which is past the point where either GitHub or
     PyPI renders it usefully. Nothing here was reworded in the split. -->

[← Back to the README](../../README.md)

# Quick Start

## Quick Start

Mornye can be used in multiple ways. Choose the mode that fits your needs.

### GUI Mode (recommended for most users)

Launch the graphical user interface with the `--gui` flag:

```bash
mornye --gui
```

*(Or `python launcher.py --gui` if running from source)*

### Web Mode (same GUI, in your browser)

Runs the exact same interface as `--gui`, served as a local web server instead of a native window — open it at `http://127.0.0.1:8000` (or whatever host/port you choose) in any browser:

```bash
mornye --web
```

*(Or `python launcher.py --web` if running from source)*

Binds to `127.0.0.1` (this machine only) by default. Override with `--host`/`--port` if needed — see the [CLI Flag Reference](api-reference.md#cli-flag-reference) below. Useful for running the GUI on a headless machine, inside Docker without a virtual display, or just preferring a browser tab over a native window.

> **Security note:** binding `--host` to anything other than `127.0.0.1`/`localhost` (e.g. `0.0.0.0`, or a LAN address) exposes the GUI — including endpoints that trigger downloads — to anyone who can reach that address, with no authentication of any kind, *unless* you set up `--web-token` or `--web-multiuser` below. Only bind beyond localhost deliberately, on a network you trust, and consider putting it behind your own authentication (a reverse proxy, VPN, etc.) regardless.

#### Authentication (`--web-token`)

Off by default (today's behavior, unchanged). Set a shared secret and every request — page, static asset, or API call — needs it, either as `?token=...` on the first visit or a cookie from then on:

```bash
mornye --web --host 0.0.0.0 --web-token "some-long-random-string"
# or: export MORNYE_WEB_TOKEN="some-long-random-string"
```

Then open `http://your-host:8000/?token=some-long-random-string` once; the browser remembers it after that. This travels as a plain query param/cookie with no HTTPS here, so treat it as basic access control on a network you already trust, not a substitute for real TLS.

#### Multi-user mode (`--web-multiuser`)

An alternative to (or combined with) `--web-token`: per-account login instead of one shared secret, plus a small per-user download queue and history.

```bash
# One-time: create an account
mornye --web-user-add alice "a real password"
mornye --web-user-list
mornye --web-user-remove alice

# Then run the server with accounts required
mornye --web --web-multiuser
```

The web GUI shows a sign-in screen automatically when it detects `--web-multiuser` is on (via `GET /api/auth/status`); a "Sign Out" option then appears under Settings → General. You can also call the endpoints directly — `POST /api/auth/login {"username", "password"}` to get a session cookie, `POST /api/auth/logout` to clear it — from a script or a frontend of your own. `POST /api/queue/submit-download {"selected_indices", "config"}` and `GET /api/queue/mine` submit and list a user's own queued downloads.

**What this does and doesn't isolate:** each account gets its own application state — its own search results, its own download folder underneath the shared root, and its own event stream, so one person's progress and file paths no longer appear in everybody's browser. What stays shared is what is genuinely machine-wide: installed extensions, the registry configuration, the Ed25519 trust store, and the HTTP connection pool. Accounts still run in one process as one OS user, and anyone who can install an extension can affect everyone — so this is household or small-team separation, not hostile-tenant isolation.

#### Installable as an app (PWA)

`--web` mode is installable — "Add to Home Screen" on a phone, or a standalone window from a desktop browser's install prompt. This needs a [secure context](https://developer.mozilla.org/en-US/docs/Web/Security/Secure_Contexts): it works out of the box at `127.0.0.1`/`localhost` (browsers treat those as secure even over plain HTTP), but a LAN address (`--host 0.0.0.0` and a phone visiting `http://192.168.x.x:8000`) needs HTTPS in front of it — the same reverse-proxy setup the security note above already recommends for auth. The service worker behind this only exists for installability and a same-page-reload fallback; it's deliberately network-first for everything so it can never make you look at stale frontend code, and it never touches `/api/*` or the WebSocket.

### Terminal UI (guided mode)

`--tui` is the guided mode: one screen with every setting on it, and the
download queue live in front of you while it runs.

```bash
mornye --tui
```

*(Or `python launcher.py --tui` if running from source)*

A sidebar gives you **Search** and **Tracks**, a **Queue** for work staged to
start later, and **Live** for the run in progress. **History** keeps downloaded
tracks and fetched links; **Library** manages saved SMB/WebDAV destinations;
**Extensions** manages registry links and extension settings. **Session** holds
recent URLs and saved profiles; **Health** checks lyric-provider availability;
**Signed** shows stored signed-session status. **Command** shows the equivalent
`mornye …` invocation. `Ctrl+R` starts a download, `Ctrl+L` shows the log,
and `q` quits.

The [Terminal UI page](tui.md) covers it in full.

> **`--interactive` is deprecated.** It still works and opens the same
> screen, with a warning. The wizard it used to open is gone; use `--tui`
> for the current guided workflow. See
> [Coming from the wizard](tui.md#coming-from-the-wizard) for what changed.

### Python API (Synchronous)

The classic synchronous API remains the simplest way to integrate Mornye into your own applications. `services` accepts either a legacy alias (resolved to `ext:<id>` if you have a matching extension installed) or an explicit `ext:<id>`.

```python
from mornye import Mornye

Mornye(
    url="https://open.spotify.com/track/TRACK_ID",
    output_dir="./downloads",
    services=["ext:tidal-web"],  # requires the corresponding extension to already be installed
)
```

This API is fully backwards-compatible with previous releases and is recommended for scripts and applications that do not require asynchronous execution.

### Which API should I use?

| API | Best for |
| --- | --- |
| `Mornye` | Scripts, CLI wrappers, automation |
| `AsyncMornye` | Discord bots, Telegram bots, FastAPI, asyncio applications |

### Asynchronous API

Mornye now features a 100% native asynchronous engine, making it ideal for modern Python applications built on asyncio, including:

- Discord bots
- Telegram bots
- FastAPI applications
- Quart / Sanic web servers
- Background workers
- Any asynchronous Python project

The new `AsyncMornye` client uses a shared asynchronous HTTP session, allowing multiple downloads and metadata requests to run efficiently without blocking the event loop.

```python
import asyncio
from mornye import AsyncMornye

async def main():
    async with AsyncMornye(
        output_dir="./downloads",
        services=["ext:tidal-web", "ext:qobuz-web"],
        quality="LOSSLESS",
    ) as client:

        # Download a single track
        await client.download_track(
            "https://open.spotify.com/track/TRACK_ID"
        )

        # Fetch playlist metadata without downloading
        info, tracks = await client.get_playlist(
            "https://open.spotify.com/playlist/PLAYLIST_ID"
        )

        print(f"{info['name']} contains {len(tracks)} tracks")

asyncio.run(main())
```

**Why use the async API?**

- Fully non-blocking (asyncio native)
- Shared HTTP connection pooling
- Lower memory usage
- Much better performance when downloading multiple tracks concurrently
- Perfect for long-running applications and web backends

> **Note:** The classic synchronous `Mornye()` API remains fully supported and backwards-compatible.

---


## Supported URL Types

Mornye's core resolves the following URL formats as input; whether a given target is actually reachable depends entirely on which extensions you have installed:

| Type | Spotify |
| --- | --- |
| Track | `open.spotify.com/track/...` |
| Album | `open.spotify.com/album/...` |
| Playlist | `open.spotify.com/playlist/...` |
| Discography (via artist URL) | `open.spotify.com/artist/...` |

A **CSV file** is accepted wherever a URL is — `mornye --csv wishlist.csv DEST`, the file icon in the GUI, a path in the terminal UI's Source panel. Rows that carry no link are matched against the catalogue, and the ones that cannot be matched are reported rather than guessed at. See [Downloading from a CSV](csv.md).

> Extensions may add support for resolving Tidal, Apple Music, SoundCloud, YouTube, Pandora, or other platform URLs directly, and may output FLAC, ALAC/M4A, AAC, or MP3 depending on what the source and the extension support. Consult the documentation of the specific extension you install for its supported URL formats and output format.

---
