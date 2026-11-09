"""Post-download transcoding.

Providers always fetch the best source available (FLAC, ALAC/M4A, …). When
`DownloadOptions.transcode_to` is set the orchestrator converts every finished
track to that format via ffmpeg.

Two families of targets are supported:

* **Lossless** — FLAC, ALAC (`.m4a`), WAV, AIFF, WavPack (`.wv`) and
  TTA (`.tta`). The conversion is bit-exact: the decoded samples are
  re-encoded without resampling and without changing the bit depth, so
  the result decodes back to the very same PCM as the source.
* **Lossy** — MP3 (320 kbps by default), the original reason this module
  exists.

The converted file keeps the exact same path as the source, only with a
different extension, so the "already downloaded" check keeps working: the
downloader looks for the *transcoded* filename before contacting any provider
and skips the track when it is already there. Note that the extension is not
always the format name — ALAC lives inside an `.m4a` container and WavPack
inside `.wv` — so always go through `extension_for()` instead of formatting
`f".{fmt}"` by hand.
"""

from __future__ import annotations

import asyncio
import asyncio.subprocess as _subproc
import contextlib
import functools
import logging
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from .errors import ErrorKind, MornyeError
from .tagger import transfer_tags_async

logger = logging.getLogger(__name__)

MP3 = "mp3"
FLAC = "flac"
ALAC = "alac"
WAV = "wav"
AIFF = "aiff"
WAVPACK = "wavpack"
TTA = "tta"
AAC = "aac"
OPUS = "opus"

DEFAULT_MP3_BITRATE = "320k"

#: Menu label → format name (None = no conversion), for every UI that offers
#: the choice. Spelled out rather than generated from SUPPORTED_FORMATS so a
#: menu can say what each format is actually *for*; the extensions still come
#: from `extension_for()`. It lives here rather than in one frontend because
#: the wizard, the TUI and the GUI have to offer the same targets, and
#: the only way to be sure of that is for them to read the same tuple.
TRANSCODE_CHOICES: tuple[tuple[str, str | None], ...] = (
    ("Keep the provider's format (no conversion)", None),
    ("FLAC — lossless, the universal default", "flac"),
    ("ALAC (.m4a) — lossless, native on macOS/iOS", "alac"),
    ("WavPack (.wv) — lossless, smallest of the exotics", "wavpack"),
    ("TTA (.tta) — lossless, fast to decode", "tta"),
    ("WAV — uncompressed PCM, for DJ software and editors", "wav"),
    ("AIFF — uncompressed PCM, the Apple flavour", "aiff"),
    ("AAC (.m4a) — lossy, better than MP3 at the same bitrate", "aac"),
    ("Opus — lossy, the most efficient; not every player reads it", "opus"),
    ("MP3 — lossy, for players that read nothing else", "mp3"),
)


def transcode_label(fmt: str | None) -> str:
    """Menu label for a stored format name, falling back to 'no conversion'."""
    for label, value in TRANSCODE_CHOICES:
        if value == fmt:
            return label
    return TRANSCODE_CHOICES[0][0]


#: ffmpeg's FLAC compression level. 8 is the strongest setting the encoder
#: offers; it buys ~1-2% over the default 5 for roughly twice the CPU time.
#: A download is converted once and kept forever, so the trade favours size.
DEFAULT_FLAC_COMPRESSION = 8

# PCM has no single "codec": the container picks a fixed-width, fixed-endian
# encoder per bit depth. WAV is little-endian, AIFF big-endian.
_PCM_LITTLE_ENDIAN: Mapping[int, str] = {
    16: "pcm_s16le",
    24: "pcm_s24le",
    32: "pcm_s32le",
}
_PCM_BIG_ENDIAN: Mapping[int, str] = {
    16: "pcm_s16be",
    24: "pcm_s24be",
    32: "pcm_s32be",
}


@dataclass(frozen=True)
class FormatSpec:
    """Everything needed to encode one target format with ffmpeg."""

    name: str
    extension: str
    lossless: bool
    muxer: str
    #: Fixed encoder name. Empty for PCM, where `pcm_codecs` decides.
    encoder: str = ""
    #: Bit depth → encoder, for the PCM containers (WAV/AIFF).
    pcm_codecs: Mapping[int, str] | None = None
    #: Spellings accepted by `normalize_transcode_format()` besides `name`.
    aliases: tuple[str, ...] = field(default_factory=tuple)

    def encoder_for(self, bit_depth: int) -> str:
        """The ffmpeg encoder to use for a source of the given bit depth."""
        if self.pcm_codecs is None:
            return self.encoder
        return self.pcm_codecs[_nearest_pcm_depth(bit_depth)]


_FORMAT_SPECS: tuple[FormatSpec, ...] = (
    FormatSpec(
        name=MP3,
        extension=".mp3",
        lossless=False,
        muxer="mp3",
        encoder="libmp3lame",
    ),
    FormatSpec(
        name=FLAC,
        extension=".flac",
        lossless=True,
        muxer="flac",
        encoder="flac",
    ),
    FormatSpec(
        # ALAC is a codec, not a container: it ships inside MPEG-4, which is
        # why the extension is .m4a and the muxer is ffmpeg's "ipod".
        name=ALAC,
        extension=".m4a",
        lossless=True,
        muxer="ipod",
        encoder="alac",
        aliases=("m4a", "applelossless", "apple lossless", "apple-lossless"),
    ),
    FormatSpec(
        name=WAV,
        extension=".wav",
        lossless=True,
        muxer="wav",
        pcm_codecs=_PCM_LITTLE_ENDIAN,
        aliases=("wave", "pcm", "riff"),
    ),
    FormatSpec(
        name=AIFF,
        extension=".aiff",
        lossless=True,
        muxer="aiff",
        pcm_codecs=_PCM_BIG_ENDIAN,
        aliases=("aif", "aifc"),
    ),
    FormatSpec(
        name=WAVPACK,
        extension=".wv",
        lossless=True,
        muxer="wv",
        encoder="wavpack",
        aliases=("wv",),
    ),
    FormatSpec(
        name=TTA,
        extension=".tta",
        lossless=True,
        muxer="tta",
        encoder="tta",
        aliases=("trueaudio", "true audio"),
    ),
    FormatSpec(
        # Same MPEG-4 container as ALAC, so the codec — not the extension —
        # tells the two apart (see _already_in_target_format).
        name=AAC,
        extension=".m4a",
        lossless=False,
        muxer="ipod",
        encoder="aac",
        aliases=("aac-lc", "m4a-aac", "mp4a"),
    ),
    FormatSpec(
        name=OPUS,
        extension=".opus",
        lossless=False,
        muxer="opus",
        encoder="libopus",
        aliases=("ogg-opus",),
    ),
)

_SPEC_BY_NAME: dict[str, FormatSpec] = {spec.name: spec for spec in _FORMAT_SPECS}
_SPEC_BY_ALIAS: dict[str, FormatSpec] = {
    alias: spec for spec in _FORMAT_SPECS for alias in (spec.name, *spec.aliases)
}

SUPPORTED_FORMATS: tuple[str, ...] = tuple(spec.name for spec in _FORMAT_SPECS)
LOSSLESS_FORMATS: tuple[str, ...] = tuple(
    spec.name for spec in _FORMAT_SPECS if spec.lossless
)
LOSSY_FORMATS: tuple[str, ...] = tuple(
    spec.name for spec in _FORMAT_SPECS if not spec.lossless
)

# Values that disable conversion (useful when they come from GUI/config)
_DISABLED_VALUES = {"", "none", "off", "no", "original", "source", "keep"}

#: MPEG-4 containers, where the extension alone does not say whether the
#: audio inside is lossless (ALAC) or lossy (AAC).
_EXT_MP4 = frozenset({".m4a", ".mp4", ".m4b"})

#: Containers that only ever hold lossless audio. Used as the last-resort
#: answer when neither mutagen nor ffprobe could read a bit depth.
_LOSSLESS_SUFFIXES = frozenset(
    {".flac", ".wav", ".wave", ".aiff", ".aif", ".wv", ".tta", ".ape", ".alac"}
)


def _nearest_pcm_depth(depth: int) -> int:
    """Rounds an arbitrary bit depth up to one PCM actually encodes.

    Rounding *up* matters: a 20-bit source stored as 24-bit PCM is still
    bit-exact, whereas truncating it to 16 would quietly throw samples away.
    """
    if depth <= 16:
        return 16
    if depth <= 24:
        return 24
    return 32


def format_spec(fmt: str) -> FormatSpec:
    """Returns the `FormatSpec` for an already-normalized format name."""
    try:
        return _SPEC_BY_NAME[fmt]
    except KeyError:
        msg = (
            f"Unsupported transcode format: {fmt!r}. "
            f"Supported: {', '.join(SUPPORTED_FORMATS)}"
        )
        raise MornyeError(ErrorKind.FILE_IO, msg) from None


def extension_for(fmt: str | None) -> str:
    """File extension a track gets once converted to `fmt` (``".m4a"`` for ALAC).

    Returns an empty string when transcoding is disabled, so callers can
    build a path unconditionally and check the result.
    """
    if not fmt:
        return ""
    return format_spec(fmt).extension


def result_format_for(fmt: str | None) -> str | None:
    """The `DownloadResult.format` label for a transcode target.

    That field names the *container*, not the codec — the codebase's own
    `_ext_to_fmt()` maps ".m4a" to "m4a" — so a target whose name differs
    from its extension has to be translated: "alac" is reported as "m4a"
    and "wavpack" as "wv", exactly as if a provider had delivered the file.
    """
    extension = extension_for(fmt)
    return extension.lstrip(".") or None


def is_lossless(fmt: str | None) -> bool:
    """True when `fmt` is one of the lossless targets."""
    return bool(fmt) and format_spec(str(fmt)).lossless


def normalize_transcode_format(value: str | None) -> str | None:
    """Normalizes the requested target format.

    Accepts the canonical names (``"flac"``, ``"alac"``, ``"mp3"``, …), the
    usual spellings for each (``".m4a"``, ``"WV"``, ``"aif"``, ``"wave"``)
    and the bitrate-suffixed aliases the GUI sends (``"mp3_320"``). Returns
    None when transcoding is disabled and raises ValueError for formats we
    cannot produce.
    """
    if value is None:
        return None

    normalized = str(value).strip().lower().lstrip(".")
    if normalized in _DISABLED_VALUES:
        return None

    spec = _SPEC_BY_ALIAS.get(normalized)
    if spec is not None:
        return spec.name

    # "mp3_320" / "mp3-320" → "mp3". Only the leading token is a format
    # name here, so this runs *after* the alias lookup: "apple-lossless"
    # must not be truncated to "apple".
    base = normalized.replace("-", "_").split("_")[0]
    spec = _SPEC_BY_ALIAS.get(base)
    if spec is not None:
        return spec.name

    msg = (
        f"Unsupported transcode format: {value!r}. "
        f"Supported: {', '.join(SUPPORTED_FORMATS)}"
    )
    raise ValueError(msg)


def normalize_bitrate(value: str | int | None) -> str:
    """Normalizes a bitrate into the ffmpeg form (``"320k"``).

    Only meaningful for the lossy targets; the lossless encoders ignore it.
    """
    if value is None:
        return DEFAULT_MP3_BITRATE

    normalized = str(value).strip().lower().replace("kbps", "k").replace(" ", "")
    if not normalized:
        return DEFAULT_MP3_BITRATE
    if normalized.isdigit():
        # 320 → "320k", 320000 → "320000" (ffmpeg accepts raw bit/s too)
        return f"{normalized}k" if int(normalized) <= 3000 else normalized
    return normalized


def transcoded_path(source: Path | str, fmt: str = MP3) -> Path:
    """Returns the path `source` will have once converted to `fmt`."""
    return Path(source).with_suffix(extension_for(fmt))


def transcoded_file_exists(path: Path | str) -> bool:
    """True when a previously transcoded file is already on disk and non-empty."""
    p = Path(path)
    try:
        return p.is_file() and p.stat().st_size > 0
    except OSError:
        return False


def ensure_ffmpeg_available(fmt: str) -> None:
    """Raises MornyeError when ffmpeg — required for transcoding — is
    still missing after a best-effort automatic install attempt (see
    ffmpeg_check.ensure_ffmpeg_installed()).
    """
    from .ffmpeg_check import ensure_ffmpeg_installed

    result = ensure_ffmpeg_installed()
    if result.get("available"):
        return

    msg = (
        f"Transcoding to {fmt.upper()} requires ffmpeg, and automatic "
        f"installation didn't work: {result.get('error') or 'unknown error'}"
    )
    raise MornyeError(ErrorKind.FILE_IO, msg)


async def _run_ffmpeg(*args: str) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        *args,
        stdout=_subproc.PIPE,
        stderr=_subproc.PIPE,
    )
    _, stderr = await proc.communicate()
    return proc.returncode or 0, stderr.decode(errors="ignore").strip()


def _probe_with_mutagen(src: Path) -> tuple[int, bool | None]:
    """`(bit_depth, is_lossless)`, or `(0, None)` when mutagen cannot tell.

    Tried first because mutagen is already a hard dependency and costs no
    subprocess. It does not know every container though — `TrueAudioInfo`,
    for one, exposes no `bits_per_sample` at all — hence the None verdict
    for the caller to resolve.
    """
    try:
        from mutagen import File as MutagenFile

        info = getattr(MutagenFile(str(src)), "info", None)
        if info is None:
            return 0, None

        depth = int(getattr(info, "bits_per_sample", 0) or 0)
        if not depth:
            return 0, None

        if src.suffix.lower() in _EXT_MP4:
            # AAC, ALAC *and* FLAC all share the MPEG-4 container and all
            # report a bit depth, so only the codec tells a lossy download
            # from a lossless one. mutagen spells them "mp4a", "alac" and
            # "fLaC" — and the last of those, which is what a Tidal stream
            # arrives as, fell through as lossy: converting one to ALAC
            # warned that quality could not be restored from a source that
            # had lost none.
            codec = str(getattr(info, "codec", "") or "").lower()
            return depth, codec.startswith(("alac", "flac"))
        return depth, True
    except Exception as exc:
        logger.debug("[transcode] mutagen could not probe %s: %s", src.name, exc)
        return 0, None


def _probe_depth_with_ffprobe(src: Path) -> int:
    """Bit depth per ffprobe, or 0 when it reports none.

    Only lossless codecs declare `bits_per_raw_sample`; MP3, AAC and friends
    leave it unset, which makes its absence a usable "this is lossy" signal.
    ffprobe ships with ffmpeg, already a hard requirement for transcoding.
    """
    try:
        out = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=bits_per_raw_sample",
                "-of",
                "default=nw=1:nk=1",
                str(src),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        ).stdout.strip()
        return int(out) if out.isdigit() else 0
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        logger.debug("[transcode] ffprobe could not probe %s: %s", src.name, exc)
        return 0


def _probe_source(src: Path) -> tuple[int, bool]:
    """Returns `(bit_depth, source_is_lossless)` for an audio file.

    The depth drives the PCM encoder choice for the WAV/AIFF targets, so
    every fallback here rounds *towards* keeping data: guessing too high
    only wastes bytes, guessing too low silently truncates samples.
    """
    depth, verdict = _probe_with_mutagen(src)
    if verdict is not None:
        return depth, verdict

    depth = _probe_depth_with_ffprobe(src)
    if depth:
        return depth, True

    # Neither tool found a bit depth. For a lossy codec that is the correct
    # answer; for a lossless container we could not inspect, assume 24-bit
    # rather than risk truncating a hi-res source.
    if src.suffix.lower() in _LOSSLESS_SUFFIXES:
        return 24, True
    return 16, False


#: libmp3lame stops at 48 kHz. A Hi-Res source has to be resampled for MP3,
#: and the ratio is clean only within a rate family: 88.2/176.4 kHz → 44.1,
#: 96/192 kHz → 48.
MP3_MAX_SAMPLE_RATE = 48_000
#: SoX resampler precision, in bits. 28 is SoX's "very high quality".
SOXR_PRECISION = 28


def _family_rate_at_most(source_rate: int, ceiling: int) -> int:
    """The highest rate <= `ceiling` in `source_rate`'s family (44.1k/48k
    multiples), so the resampling ratio stays an integer when it can."""
    base = 44_100 if source_rate % 44_100 == 0 else 48_000
    best = 0
    multiple = base
    while multiple <= ceiling:
        best = multiple
        multiple *= 2
    return best or ceiling


def mp3_target_sample_rate(source_rate: int) -> int | None:
    """The rate to resample a source to for MP3, or None to leave it."""
    if not source_rate or source_rate <= MP3_MAX_SAMPLE_RATE:
        return None
    return _family_rate_at_most(source_rate, MP3_MAX_SAMPLE_RATE)


#: Bit depths a lossless conversion can be capped to.
CAPPABLE_BIT_DEPTHS = (16, 24)

#: Sample formats per encoder, for a capped bit depth. 24-bit audio travels
#: in 32-bit containers with bits_per_raw_sample telling the encoder the
#: real width.
_SAMPLE_FMT: Mapping[str, Mapping[int, str]] = {
    "flac": {16: "s16", 24: "s32"},
    "alac": {16: "s16p", 24: "s32p"},
    "wavpack": {16: "s16p", 24: "s32p"},
    "tta": {16: "s16", 24: "s32"},
}


def plan_resample(
    spec: FormatSpec,
    source_rate: int,
    source_depth: int,
    *,
    max_sample_rate: int = 0,
    max_bit_depth: int = 0,
) -> tuple[int | None, int | None]:
    """(target rate, target bit depth); None for each left untouched.

    The target format sets the first limit (lame and AAC stop at 48 kHz,
    Opus only runs at 48 kHz); the user's caps set the second. A lossless
    target is never touched unless a cap asks for it.
    """
    rate: int | None = None
    if spec.name in (MP3, AAC):
        rate = mp3_target_sample_rate(source_rate)
    elif spec.name == OPUS and source_rate and source_rate != 48_000:
        rate = 48_000
    effective = rate or source_rate
    if max_sample_rate and effective and effective > max_sample_rate:
        rate = _family_rate_at_most(source_rate or effective, max_sample_rate)

    depth: int | None = None
    if (
        spec.lossless
        and max_bit_depth
        and source_depth
        and source_depth > max_bit_depth
    ):
        depth = max_bit_depth
    return rate, depth


def exceeds_caps(
    src: Path | str, *, max_sample_rate: int = 0, max_bit_depth: int = 0
) -> bool:
    """Whether `src` is above either cap — and so needs converting even
    when it is already in the target format."""
    if not (max_sample_rate or max_bit_depth):
        return False
    path = Path(src)
    if max_sample_rate and _probe_sample_rate(path) > max_sample_rate:
        return True
    if max_bit_depth:
        depth, lossless = _probe_source(path)
        return lossless and depth > max_bit_depth
    return False


@functools.lru_cache(maxsize=1)
def soxr_available() -> bool:
    """Whether this ffmpeg was built with libsoxr.

    `ffmpeg -h filter=aresample` lists `soxr` as a choice whether or not
    the library is compiled in, and asking for it without it fails the
    whole conversion — so the build configuration is what decides.
    """
    try:
        out = subprocess.run(
            ["ffmpeg", "-hide_banner", "-buildconf"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return "--enable-libsoxr" in (out.stdout + out.stderr)


def _probe_sample_rate(src: Path) -> int:
    try:
        from mutagen import File as MutagenFile

        info = getattr(MutagenFile(str(src)), "info", None)
        return int(getattr(info, "sample_rate", 0) or 0)
    except Exception:
        return 0


def _audio_codec(src: Path) -> str:
    """The first audio stream's codec, lower-cased; "" when unknown."""
    try:
        return (
            subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-select_streams",
                    "a:0",
                    "-show_entries",
                    "stream=codec_name",
                    "-of",
                    "default=nw=1:nk=1",
                    str(src),
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            .stdout.strip()
            .lower()
        )
    except (OSError, subprocess.SubprocessError):
        return ""


def _already_in_target_format(src: Path, spec: FormatSpec) -> bool:
    """True when the provider already delivered exactly what was requested.

    The extension alone is not enough for ALAC, because `.m4a` is a
    container and not a codec: a lossy AAC download lands in one, and so
    does the FLAC-in-MP4 that Tidal serves. Testing only that the source is
    *lossless* accepted that FLAC-in-MP4 as "already ALAC" and returned it
    untouched — an `.m4a` no ordinary player would open. The codec name is
    the question actually being asked, so it is the one now asked.
    """
    if src.suffix.lower() != spec.extension:
        return False
    if spec.extension not in _EXT_MP4:
        return True
    codec = _audio_codec(src)
    if not codec:
        # ffprobe had no answer. Fall back to the older, looser test rather
        # than re-encoding a file that may well already be right.
        _, lossless = _probe_source(src)
        return lossless == spec.lossless
    return codec == spec.encoder


def already_in_target_format(src: Path | str, fmt: str) -> bool:
    """Whether `src` is already exactly `fmt`. The orchestrator's own check.

    Public so that downloader.py asks this one question in one place: it
    used to compare extensions itself, which is the test this module had
    already found insufficient.
    """
    return _already_in_target_format(Path(src), format_spec(fmt))


#: libopus refuses more than 256 kb/s per channel, and a mono source is one
#: channel; past ~192k Opus is transparent anyway, so the MP3-oriented
#: default of 320k is simply lowered rather than failing the conversion.
OPUS_MAX_BITRATE = 256_000


def _bitrate_bps(value: str) -> int:
    text = (value or "").strip().lower()
    try:
        if text.endswith("k"):
            return int(float(text[:-1]) * 1000)
        return int(float(text))
    except ValueError:
        return 0


def _bitrate_for(spec: FormatSpec, bitrate: str) -> str:
    if spec.name == OPUS and _bitrate_bps(bitrate) > OPUS_MAX_BITRATE:
        return f"{OPUS_MAX_BITRATE // 1000}k"
    return bitrate


def _encode_command(
    spec: FormatSpec,
    src: Path,
    dest: Path,
    bitrate: str,
    bit_depth: int,
    *,
    sample_rate: int = 0,
    max_sample_rate: int = 0,
    max_bit_depth: int = 0,
) -> list[str]:
    target_rate, target_depth = plan_resample(
        spec,
        sample_rate,
        bit_depth,
        max_sample_rate=max_sample_rate,
        max_bit_depth=max_bit_depth,
    )
    encoder = spec.encoder_for(target_depth or bit_depth)
    cmd = [
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(src),
        # Only the first audio stream: embedded artwork is re-attached later
        # by the tagger, which handles every source container the same way.
        "-map",
        "0:a:0",
        "-vn",
        "-map_metadata",
        "0",
        "-c:a",
        encoder,
    ]

    if not spec.lossless:
        cmd += ["-b:a", _bitrate_for(spec, bitrate)]
    if spec.name == MP3:
        cmd += ["-id3v2_version", "3", "-write_id3v1", "1"]
    elif spec.name == FLAC:
        cmd += ["-compression_level", str(DEFAULT_FLAC_COMPRESSION)]

    # Rate and depth change only when the target format or a user cap asks:
    # left alone, the lossless targets stay bit-identical to the source.
    # The rate is chosen explicitly rather than left to ffmpeg, which picks
    # the nearest rate an encoder supports and so turns 88.2 kHz into 48 kHz.
    # SoXR when this ffmpeg has it, as the mobile app uses; triangular
    # dither whenever bits are dropped, so the truncation is noise rather
    # than distortion.
    options: list[str] = []
    if target_rate and soxr_available():
        options += [str(target_rate), "resampler=soxr", f"precision={SOXR_PRECISION}"]
    if target_depth:
        options.append("dither_method=triangular")
    if options:
        cmd += ["-af", "aresample=" + ":".join(options)]
    if target_rate:
        cmd += ["-ar", str(target_rate)]
    if target_depth:
        sample_fmt = _SAMPLE_FMT.get(encoder, {}).get(target_depth)
        if spec.pcm_codecs is not None:
            sample_fmt = "s16" if target_depth == 16 else "s32"
        if sample_fmt:
            cmd += ["-sample_fmt", sample_fmt]
        if target_depth == 24 and spec.pcm_codecs is None:
            cmd += ["-bits_per_raw_sample", "24"]
    cmd += ["-f", spec.muxer, str(dest)]
    return cmd


async def transcode_file_async(
    source: Path | str,
    *,
    fmt: str = MP3,
    bitrate: str = DEFAULT_MP3_BITRATE,
    keep_original: bool = False,
    max_sample_rate: int = 0,
    max_bit_depth: int = 0,
) -> Path:
    """Converts `source` to `fmt`, preserving tags, cover art and lyrics.

    The output lands next to the source with the same stem. The source is
    removed once the conversion succeeds unless `keep_original` is set.
    Returns the path of the converted file.

    For the lossless targets the sample rate and bit depth of the source are
    carried over untouched, so the conversion round-trips to identical PCM.

    Raises
    ------
        MornyeError: If the source is missing or ffmpeg fails.

    """
    src = Path(source)
    spec = format_spec(fmt)

    dest = transcoded_path(src, spec.name)
    if _already_in_target_format(src, spec) and not await asyncio.to_thread(
        exceeds_caps,
        src,
        max_sample_rate=max_sample_rate,
        max_bit_depth=max_bit_depth,
    ):
        # The provider already produced the requested format, within the caps.
        # (One above a cap is re-encoded in place, under the same name.)
        return src

    if not await asyncio.to_thread(src.is_file):
        msg = f"Cannot transcode, file not found: {src}"
        raise MornyeError(ErrorKind.FILE_IO, msg)

    bit_depth, source_is_lossless = await asyncio.to_thread(_probe_source, src)
    if spec.lossless and not source_is_lossless:
        # Worth saying out loud: the output will be a bit-perfect copy of an
        # already-degraded signal, not a recovered original. Not an error —
        # some libraries are deliberately single-format.
        logger.warning(
            "[transcode] %s is lossy: converting it to %s cannot restore "
            "quality, only grow the file",
            src.name,
            spec.name.upper(),
        )

    tmp = dest.with_name(f".{dest.stem}.mornye-transcode{spec.extension}")
    sample_rate = await asyncio.to_thread(_probe_sample_rate, src)
    rc, stderr = await _run_ffmpeg(
        *_encode_command(
            spec,
            src,
            tmp,
            bitrate,
            bit_depth,
            sample_rate=sample_rate,
            max_sample_rate=max_sample_rate,
            max_bit_depth=max_bit_depth,
        )
    )

    if rc != 0 or not transcoded_file_exists(tmp):
        with contextlib.suppress(OSError):
            await asyncio.to_thread(tmp.unlink, True)
        msg = f"ffmpeg failed to transcode {src.name} to {spec.name.upper()}: {stderr}"
        raise MornyeError(ErrorKind.FILE_IO, msg)

    try:
        await transfer_tags_async(src, tmp)
    except Exception as exc:
        # The audio is good, so the conversion is kept — but say plainly what
        # is lost. The writers clear the destination before they write, so a
        # failure part-way through does not leave "the basic tags ffmpeg
        # carried over via -map_metadata", as this comment used to claim: it
        # leaves nothing. One non-numeric track number ("B2", from the vinyl
        # pressing MusicBrainz picked) was enough to strip a finished ALAC
        # file of every tag it had. See tagger._tag_int.
        logger.warning(
            "[transcode] tag transfer failed for %s — %s may have no tags: %s",
            src.name,
            dest.name,
            exc,
        )

    await asyncio.to_thread(os.replace, tmp, dest)

    if not keep_original and dest != src:
        try:
            await asyncio.to_thread(src.unlink)
        except OSError as exc:
            logger.warning("[transcode] could not remove source %s: %s", src.name, exc)

    logger.info(
        "[transcode] %s → %s (%s)",
        src.name,
        dest.name,
        bitrate if not spec.lossless else f"{bit_depth}-bit lossless",
    )
    return dest
