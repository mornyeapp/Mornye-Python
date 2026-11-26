# The Player

The Player page plays the local library: songs, albums, artists and
playlists, with a queue, shuffle and repeat, synced lyrics and the system's
media keys. It can also tell Discord and Last.fm what plays, be steered
from the Mornye phone app, and cast to receivers on the network (see the
sections at the end).

In the desktop app it can play through two engines:

| | Window (built-in) | Native (libmpv) |
| --- | --- | --- |
| Formats | FLAC, MP3, WAV, Ogg, Opus, AAC as stored; ALAC, AIFF, WavPack, TTA, APE, WMA through a lossless FLAC copy | Everything ffmpeg decodes, as stored (no copy) |
| Output | PCM to the system mixer | PCM or **bitstream** (AC-3, DTS, E-AC-3, DTS-HD, TrueHD) |
| Output device | The system's | Any device, chosen in Settings → Player |
| Exclusive mode / bit-perfect | No | Yes (WASAPI exclusive, Core Audio hog mode) |
| Gapless | No | Yes |
| ReplayGain, audio delay, equalizer, channels, normalization | No | Yes |
| Speed and A-B repeat | Yes | Yes |

The web interface (`--web`) always uses the browser: the music has to come
out of the listener's device, not the server's.

## Installing the native engine

It needs libmpv. Its Python binding, python-mpv, comes with every Mornye
install and loads nothing until the engine starts.

- **Windows**: nothing to do with `Mornye-Windows.exe` or the app from
  `Mornye-Windows-Setup.exe`: both ship `libmpv-2.dll` inside them. Installed from PyPI instead, download a libmpv
  build (`mpv-dev-x86_64-….7z` from shinchiro's mpv builds) and put
  `libmpv-2.dll` in a folder on the `PATH`, or name it with
  `MORNYE_LIBMPV` (below).
- **macOS**: `brew install mpv`. Mornye finds Homebrew's libmpv in
  `/opt/homebrew/lib` (Apple silicon) or `/usr/local/lib` (Intel).
- **Linux**: install the `libmpv2` (Debian, Ubuntu) or `mpv-libs` (Fedora)
  package.

A libmpv somewhere else can be named with the `MORNYE_LIBMPV` environment
variable (the full path of the library file).

With **Engine** on *Auto* (the default), the native engine plays whenever it
can be loaded; otherwise the window's engine plays and Settings → Player says
why.

## Settings → Player

Applied as soon as they change, and kept in `~/.mornye/player-audio.json`.

- **Engine**: Auto, Native, or Window.
- **Output device**: the devices libmpv sees. On macOS each one appears twice,
  once per driver (Core Audio, AVFoundation).
- **Exclusive mode**: the player takes the device for itself. Nothing else is
  heard meanwhile, and the stream reaches the device bit for bit.
- **Bitstream (passthrough)**: the formats sent compressed for the receiver
  to decode. **S/PDIF (coaxial, optical) carries only AC-3 and DTS**;
  E-AC-3, DTS-HD and TrueHD need HDMI. A Dolby Digital Plus album therefore
  reaches a receiver on S/PDIF in 5.1 through the AC-3 copy Mornye makes of
  it (*Dolby Digital copy*, on by default), with AC-3 ticked here.
- **Output channels**: Auto, stereo, 5.1 or 7.1. A smaller layout than the
  file's is a downmix, normalized so it does not clip. **Upmix stereo**
  spreads a stereo file over 5.1 or 7.1.
- **Gapless**: the next track of the queue is opened in advance and follows
  without a pause.
- **ReplayGain**: off, track or album, from the files' tags, with a pre-amp
  and clipping prevention.
- **Audio delay**: for a receiver or speakers that lag behind (−2000 to
  +2000 ms).
- **Keep pitch when changing speed**.
- **Normalization**: dynamic (dynaudnorm) or EBU R128 loudness (loudnorm).
  Off by default.
- **Equalizer**: ten bands (31 Hz – 16 kHz, ±12 dB) with presets. The signal
  is lowered by the largest boost first, so a boost cannot clip.
  **Parametric** instead takes up to 20 peak and shelf filters with a
  preamp, and imports and exports the AutoEQ / Equalizer APO format
  (`Preamp: -6.4 dB`, then lines like
  `Filter 1: ON PK Fc 105 Hz Gain -3.5 dB Q 0.70`; PK, LSC and HSC).

A bitstreamed format reaches the receiver compressed, so the equalizer,
normalization, upmix, ReplayGain and the volume do not apply to it.

The player bar shows the file's quality, or *Bitstream AC-3 5.1* when the
receiver is decoding; hovering it shows what reaches the device.

## Keyboard

While something is loaded, and not while typing in a field:

| Key | |
| --- | --- |
| Space | Play / pause |
| ← / → | Back / forward 5 s (with Shift: 30 s) |
| + / − | Volume |
| M | Mute |
| [ / ] | Slower / faster (0.5× – 2×) |
| Backspace | Normal speed |
| L | A-B repeat: first press sets A, second sets B and loops, third clears |
| I | Playback statistics |

## Playback statistics

The ⓘ button in the player bar (or I): codec, source and output format,
bitstream or PCM, audio driver and device, bitrate, buffer, speed, delay,
A-B loop, ReplayGain and active filters.

## Checking bitstream on a receiver

Play an AC-3 file (the Dolby Digital copy of an E-AC-3 album) with AC-3
ticked and the S/PDIF or HDMI device chosen: the receiver's display should
read *Dolby Digital* and 5.1, and the statistics *Bitstream: Yes*. If the
receiver shows PCM, check that the device chosen is the digital output, and
on Windows that the sound card's own "Dolby Digital Live" or enhancements
are not re-encoding everything.

## Home and the listening record

While a song plays, the Player records how long it was actually heard
(seeking does not count): a play after 30 seconds, and whether the song was
played through (to the end or past half) or skipped. The record stays on
the computer. From it:

- **Home** (in the music look, Settings › General › Interface Style ›
  Music) shows songs recommended for you outside the library (once a day,
  Deezer is asked for songs related to a few you played or added), mixes of
  library songs, Quick Play, what you pinned, "Finish your collection" and
  "On repeat". *Edit Home* reorders and hides the sections.
- **Stats › Listening** shows listening time, plays, streaks, top artists,
  albums and songs, genres, decades and formats, time of day, the
  play-through rate and your listener types.

In `--web` multi-user mode each account has its own record.

## Discord

Settings › Player › Discord shows "Listening to Mornye" on your Discord
profile with the song, artist, album and elapsed time.

1. Create an application in the
   [Discord Developer Portal](https://discord.com/developers/applications).
   Its name is what Discord shows, so call it Mornye.
2. Paste its **Application ID** in Settings and switch it on.

The Discord desktop app has to be running on the same computer; Mornye
talks to it directly, with no Discord SDK, and keeps trying while it is
closed. The status line says when it is connected and as whom. **Show
album art** looks the cover up on Apple Music by artist and album, because
Discord can only show a picture from a public address. Pausing clears the
profile.

## Last.fm

Settings › Player › Last.fm scrobbles what you play:

1. [Create an API account](https://www.last.fm/api/account/create) on
   Last.fm (any name will do) and paste its **API key** and **shared
   secret**.
2. Click **Connect…** and approve Mornye on the Last.fm page that opens.

While a song plays Last.fm shows it as "Scrobbling now". It is scrobbled
once half of it or four minutes has been heard, and a song under thirty
seconds never is: Last.fm's own rule, measured as listening time, not seek
position. A scrobble that cannot be sent (no network, Last.fm down) waits
in `scrobble-queue.json` in the cache folder and is tried again; the
settings show how many are waiting, with **Retry now**. The key, secret and
session are kept in `lastfm.json`, readable only by your user. A new key or
secret signs out.

Discord and Last.fm are **desktop only**: in `--web` they would use the
server's Discord and the server's Last.fm account, not the listener's, so
the settings are hidden and nothing is scrobbled from there.

## Mornye Link and Mornye Connect

**Mornye Link** (Settings › Player) pairs the Mornye phone app with this
computer. Switch it on, click **Show PIN**, and pick the computer in the
app and type the PIN; check that the code under it matches. The phone finds
the computer on the same Wi-Fi, or by the address Settings shows. Each
paired phone gets its own key, which opens the library through the
Subsonic API, and can be removed from the list.

**Mornye Connect** plays on one device and controls it from the others:
the Player's **Play on** lists your devices, and moves the queue and
position to the one picked. Each device's name is set in Settings ›
Player. In `--web`, an admin can let the server play out loud through its
own audio output (**Use the server as a speaker**, or
`MORNYE_CONNECT_SERVER_SPEAKER=1`); it needs libmpv on the server.

## Casting

In the desktop app, **Play on** also lists the DLNA/UPnP renderers and
FCast receivers found on the network. The music is served to them from
this computer, and the Player's controls steer them.
