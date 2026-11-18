[← Back to the guide](README.md)

# Listening from other apps (Subsonic)

`mornye --web` speaks the **Subsonic / OpenSubsonic** API under `/rest`, so
the music on your server plays in the apps that already know it: Symfonium,
Feishin, DSub, Substreamer, Tempo, Sonixd and the rest. Browse by artist and
album, search, stream, star, keep playlists, and pick up a play queue on
another device where you left it.

It is **off** until you give it a password.

## Turning it on

| Variable | What it does |
| --- | --- |
| `MORNYE_SUBSONIC_PASSWORD` | The password apps sign in with. Setting it turns the API on. |
| `MORNYE_SUBSONIC_USER` | The user name to go with it (default `mornye`). |
| `MORNYE_SUBSONIC_API_KEY` | Optional: an OpenSubsonic API key, for apps that sign in with one instead. |

```bash
export MORNYE_SUBSONIC_PASSWORD="a long random password"
mornye --web --host 0.0.0.0
```

In the app, add a server with the address of your Mornye web page
(`http://nas.local:8000`), the user name and the password.

**Why a password of its own.** Subsonic apps sign in by sending
`md5(password + salt)`, which can only be checked against the password
itself. Web accounts (`--web-multiuser`) are stored hashed, so they cannot be
used here; this password is separate, and it is all that `/rest` accepts.

**The `--web-token` does not apply to `/rest`.** Apps have no way to carry
it: every `/rest` call signs in with the Subsonic credentials instead. The
web page itself still needs the token as before.

Use HTTPS (a reverse proxy) whenever the server is reachable from outside
your network: apps sign in on every request, and one that sends the
password in clear (`p=`) sends it as it is.

## What it plays

The library is the one the **Player** page keeps (each file's tags, stored so
the page opens at once). A server nobody has opened the Player on is scanned
on the first call: the download folder and the
[existing library folders](automation.md#music-you-already-have---library-scan-dir)
(`MORNYE_LIBRARY_SCAN_DIRS`). After that, an app's **Rescan** (`startScan`)
reads only what is new or changed on disk.

- **Artists** are split the way the Player splits them: `Doja Cat;SZA` is
  two artists, and an album shared by two artists is on both their pages.
- **Covers** come from the art embedded in the file, or a `cover.jpg` /
  `folder.jpg` beside it.
- **Lyrics**: synced ones from a `.lrc` beside the file, otherwise those
  embedded in it (`getLyricsBySongId`, OpenSubsonic's songLyrics).
- Files are streamed as they are, with byte ranges for seeking; nothing is
  transcoded.

## What apps keep on the server

Stars, play counts (for "most played" and "recently played"), playlists and
the saved play queue are stored in Mornye's database, so they follow you from
one app or device to another.

## Supported methods

`ping`, `getLicense`, `getOpenSubsonicExtensions`, `getMusicFolders`,
`getUser`, `getScanStatus`, `startScan`, `getIndexes`, `getArtists`,
`getArtist`, `getAlbum`, `getSong`, `getMusicDirectory`, `getArtistInfo(2)`,
`getAlbumInfo(2)`, `getSimilarSongs(2)`, `getTopSongs`, `getGenres`,
`getAlbumList(2)`, `getRandomSongs`, `getSongsByGenre`, `search2`, `search3`
(an empty query lists everything, which is how Symfonium syncs),
`getStarred(2)`, `getNowPlaying`, `star`, `unstar`, `scrobble`, `setRating`,
`getPlaylists`, `getPlaylist`, `createPlaylist`, `updatePlaylist`,
`deletePlaylist`, `getPlayQueue`, `savePlayQueue`, `getLyrics`,
`getLyricsBySongId`, `stream`, `download`, `getCoverArt`.

OpenSubsonic extensions: `formPost` (parameters in a POST body),
`songLyrics`, `apiKeyAuthentication`. Answers are XML by default and JSON
with `f=json`.

Not offered: transcoding, podcasts, internet radio, shares, bookmarks and
ratings (they answer, empty, so apps do not stall on them).
