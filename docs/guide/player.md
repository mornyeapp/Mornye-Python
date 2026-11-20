# The Player

The Player page plays the local library: songs, albums, artists and
playlists, with a queue, shuffle and repeat, synced lyrics and the system's
media keys.

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
