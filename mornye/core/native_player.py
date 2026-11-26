"""The player's native audio engine: libmpv, through python-mpv.

The browser engine of the window (WKWebView, WebView2) always decodes to
PCM and hands it to the system mixer: no bitstream, no choice of output
device, no exclusive mode. This engine plays the files itself, so it can
send AC-3/DTS as they are to an S/PDIF or HDMI receiver, pick the device,
take it exclusively, and run gapless, ReplayGain, an equalizer and the
other options below (docs/native-player-plan.md).

Desktop only. In ``--web`` the audio must come out of the listener's
device, not the server's, and the methods are not exposed over HTTP.

The page drives it through ``api_mixins/native_player.py`` and hears back
through one event function, ``window.app_native_player_event``.
"""

from __future__ import annotations

import contextlib
import ctypes.util
import json
import logging
import os
import queue
import re
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from .atomic_io import write_json_atomic
from .paths import data_path

logger = logging.getLogger(__name__)

#: What the page is told, at most this often, while the position moves.
TIME_UPDATE_INTERVAL_S = 0.25

SPDIF_CODECS = ("ac3", "eac3", "dts", "dts-hd", "truehd")
#: Formats that only HDMI carries; S/PDIF (coaxial, optical) has room for
#: AC-3 and core DTS only.
HDMI_ONLY_CODECS = ("eac3", "dts-hd", "truehd")
ENGINES = ("auto", "browser", "native")
REPLAYGAIN_MODES = ("no", "track", "album")
CHANNEL_LAYOUTS = ("auto", "stereo", "5.1", "7.1")
NORMALIZERS = ("off", "dynaudnorm", "loudnorm")
EQ_BANDS_HZ = (31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000)
EQ_PRESETS: dict[str, tuple[float, ...]] = {
    "flat": (0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
    "bass": (6, 5, 4, 2, 0, 0, 0, 0, 0, 0),
    "treble": (0, 0, 0, 0, 0, 1, 2, 4, 5, 6),
    "loudness": (5, 4, 2, 0, -1, -1, 0, 2, 4, 5),
    "vocal": (-2, -2, -1, 1, 3, 4, 3, 1, 0, -1),
    "rock": (4, 3, 2, 0, -1, -1, 1, 2, 3, 4),
    "classical": (3, 2, 1, 0, 0, 0, -1, -1, 1, 2),
    "electronic": (5, 4, 1, 0, -2, 1, 0, 1, 4, 5),
}
EQ_RANGE_DB = 12.0
EQ_MODES = ("graphic", "parametric")
#: Parametric filters: the kinds, how many, and the ranges each value keeps
#: to (the mobile app's, so a filter list moves between them unchanged).
PEQ_TYPES = ("peak", "lowshelf", "highshelf")
PEQ_MAX_FILTERS = 20
PEQ_FREQ_HZ = (20.0, 20000.0)
PEQ_GAIN_DB = 24.0
PEQ_Q = (0.1, 20.0)
PEQ_DEFAULT_Q = 0.707

#: ffmpeg codec names (``audio-codec-name``) and the bitstream settings
#: that send them out compressed.
_SPDIF_FOR_CODEC = {
    "ac3": ("ac3",),
    "eac3": ("eac3",),
    "dts": ("dts", "dts-hd"),
    "truehd": ("truehd",),
}


class NativePlayerUnavailable(RuntimeError):
    """libmpv or python-mpv cannot be loaded: the browser engine plays."""


# ── Options ──────────────────────────────────────────────────────────────────


def _clamped(value: Any, low: float, high: float, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:  # NaN
        return default
    return max(low, min(high, number))


def clean_filter(raw: Any) -> dict | None:
    """One parametric filter with every value in range; None if unusable."""
    if not isinstance(raw, dict):
        return None
    kind = str(raw.get("type", "peak"))
    if kind not in PEQ_TYPES:
        return None
    enabled = raw.get("enabled", True)
    return {
        "type": kind,
        "freq": round(_clamped(raw.get("freq"), *PEQ_FREQ_HZ, 1000.0), 2),
        "gain": round(_clamped(raw.get("gain"), -PEQ_GAIN_DB, PEQ_GAIN_DB, 0.0), 2),
        "q": round(_clamped(raw.get("q"), *PEQ_Q, PEQ_DEFAULT_Q), 3),
        "enabled": enabled if isinstance(enabled, bool) else True,
    }


def clean_filters(raw: Any) -> list[dict]:
    """The usable filters of `raw`, at most PEQ_MAX_FILTERS of them."""
    if not isinstance(raw, list):
        return []
    filters = [f for f in (clean_filter(item) for item in raw) if f is not None]
    return filters[:PEQ_MAX_FILTERS]


@dataclass
class PlayerOptions:
    """What the Player settings store; applied to mpv as they change."""

    engine: str = "auto"
    audio_device: str = "auto"
    spdif: list[str] = field(default_factory=list)
    exclusive: bool = False
    gapless: bool = True
    replaygain: str = "no"
    replaygain_preamp: float = 0.0
    replaygain_clip: bool = True
    audio_delay_ms: int = 0
    pitch_correction: bool = True
    eq_enabled: bool = False
    eq_preset: str = "flat"
    eq_gains: list[float] = field(default_factory=lambda: [0.0] * len(EQ_BANDS_HZ))
    eq_mode: str = "graphic"
    eq_preamp: float = 0.0
    eq_filters: list[dict] = field(default_factory=list)
    channels: str = "auto"
    upmix: bool = False
    normalize: str = "off"
    quiet_boost: bool = False

    @classmethod
    def from_dict(cls, raw: Any) -> PlayerOptions:
        """Options from stored or submitted values; anything invalid falls
        back to its default instead of failing."""
        base = cls()
        if not isinstance(raw, dict):
            return base

        def pick(name: str, allowed: tuple[str, ...]) -> str:
            value = str(raw.get(name, getattr(base, name)))
            return value if value in allowed else getattr(base, name)

        def number(name: str, low: float, high: float) -> float:
            try:
                value = float(raw.get(name, getattr(base, name)))
            except (TypeError, ValueError):
                return float(getattr(base, name))
            if value != value:  # NaN
                return float(getattr(base, name))
            return max(low, min(high, value))

        def flag(name: str) -> bool:
            value = raw.get(name, getattr(base, name))
            return value if isinstance(value, bool) else bool(getattr(base, name))

        spdif = raw.get("spdif", [])
        gains = raw.get("eq_gains", base.eq_gains)
        if not isinstance(gains, list) or len(gains) != len(EQ_BANDS_HZ):
            gains = base.eq_gains
        clean_gains = []
        for g in gains:
            try:
                value = float(g)
            except (TypeError, ValueError):
                value = 0.0
            if value != value:
                value = 0.0
            clean_gains.append(max(-EQ_RANGE_DB, min(EQ_RANGE_DB, value)))
        device = raw.get("audio_device", base.audio_device)
        preset = str(raw.get("eq_preset", base.eq_preset))
        return cls(
            engine=pick("engine", ENGINES),
            audio_device=(
                device.strip() if isinstance(device, str) and device.strip() else "auto"
            ),
            spdif=[c for c in SPDIF_CODECS if isinstance(spdif, list) and c in spdif],
            exclusive=flag("exclusive"),
            gapless=flag("gapless"),
            replaygain=pick("replaygain", REPLAYGAIN_MODES),
            replaygain_preamp=number("replaygain_preamp", -15.0, 15.0),
            replaygain_clip=flag("replaygain_clip"),
            audio_delay_ms=int(number("audio_delay_ms", -2000, 2000)),
            pitch_correction=flag("pitch_correction"),
            eq_enabled=flag("eq_enabled"),
            eq_preset=preset if preset in EQ_PRESETS or preset == "custom" else "flat",
            eq_gains=clean_gains,
            channels=pick("channels", CHANNEL_LAYOUTS),
            upmix=flag("upmix"),
            normalize=pick("normalize", NORMALIZERS),
            quiet_boost=flag("quiet_boost"),
            eq_mode=pick("eq_mode", EQ_MODES),
            eq_preamp=number("eq_preamp", -PEQ_GAIN_DB, PEQ_GAIN_DB),
            eq_filters=clean_filters(raw.get("eq_filters", base.eq_filters)),
        )

    def to_dict(self) -> dict:
        return asdict(self)


def _options_file() -> Path:
    return data_path("player-audio.json")


def load_options() -> PlayerOptions:
    try:
        raw = json.loads(_options_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return PlayerOptions()
    return PlayerOptions.from_dict(raw)


def save_options(options: PlayerOptions) -> None:
    write_json_atomic(_options_file(), options.to_dict())


# ── From options to mpv ──────────────────────────────────────────────────────


def _db(value: float) -> str:
    return f"{value:g}"


def filter_chain(options: PlayerOptions) -> list[str]:
    """The ``af`` list for PCM playback: equalizer, upmix, normalization.

    Each entry is one mpv filter (``lavfi=[graph]``). Empty when nothing is
    on, so a plain file goes through no filter at all.
    """
    chain: list[str] = []
    if options.eq_enabled and options.eq_mode == "parametric":
        graph = _parametric_graph(options)
        if graph:
            chain.append(f"lavfi=[{graph}]")
    elif options.eq_enabled and any(abs(g) > 0.01 for g in options.eq_gains):
        # Headroom first: a band raised by +6 dB would clip a file mastered
        # to 0 dBFS, so the whole signal is lowered by the largest boost.
        headroom = max(0.0, max(options.eq_gains))
        bands = [
            f"equalizer=f={hz}:t=o:w=1:g={_db(g)}"
            for hz, g in zip(EQ_BANDS_HZ, options.eq_gains, strict=True)
            if abs(g) > 0.01
        ]
        if headroom:
            bands.insert(0, f"volume=-{_db(headroom)}dB")
        chain.append(f"lavfi=[{','.join(bands)}]")
    if options.upmix and options.channels in ("5.1", "7.1"):
        chain.append(f"lavfi=[surround=chl_out={options.channels}]")
    if options.normalize == "dynaudnorm":
        chain.append("lavfi=[dynaudnorm=f=250:g=15]")
    elif options.normalize == "loudnorm":
        chain.append("lavfi=[loudnorm=I=-16:TP=-1.5:LRA=11]")
    if quiet_boost_active(options):
        # ReplayGain may now raise a song past its peak: the limiter holds
        # the loudest moments under full scale instead of letting them clip.
        # level=disabled, or alimiter would turn the whole song up itself.
        chain.append("lavfi=[alimiter=limit=0.977:attack=5:release=50:level=disabled]")
    return chain


def quiet_boost_active(options: PlayerOptions) -> bool:
    """ "Turn quiet songs up" works through ReplayGain: without it on, there
    is no gain telling how much a song should be raised."""
    return options.quiet_boost and options.replaygain != "no"


_PEQ_FILTER = {"peak": "equalizer", "lowshelf": "lowshelf", "highshelf": "highshelf"}


def _parametric_graph(options: PlayerOptions) -> str:
    """The lavfi graph of the parametric equalizer: the preamp as given (an
    AutoEQ list brings its own, negative, to make room for its boosts),
    then one biquad per filter that is on and changes something."""
    parts = [
        f"{_PEQ_FILTER[f['type']]}=f={_db(f['freq'])}:t=q:w={_db(f['q'])}:g={_db(f['gain'])}"
        for f in options.eq_filters
        if f.get("enabled", True) and abs(f["gain"]) > 0.01
    ]
    if abs(options.eq_preamp) > 0.01:
        parts.insert(0, f"volume={_db(options.eq_preamp)}dB")
    return ",".join(parts)


# ── AutoEQ / Equalizer APO filter lists ──────────────────────────────────────

_AUTOEQ_TYPES = {
    "PK": "peak",
    "PEQ": "peak",
    "LS": "lowshelf",
    "LSC": "lowshelf",
    "LSQ": "lowshelf",
    "HS": "highshelf",
    "HSC": "highshelf",
    "HSQ": "highshelf",
}
_AUTOEQ_NAMES = {"peak": "PK", "lowshelf": "LSC", "highshelf": "HSC"}
_NUM = r"([-+]?\d+(?:[.,]\d+)?)"
_PREAMP_RE = re.compile(rf"^\s*preamp\s*:\s*{_NUM}\s*db", re.I)
_FILTER_RE = re.compile(
    rf"^\s*filter(?:\s*\d+)?\s*:\s*(on|off)\s+([a-z]+)"
    rf"(?:\s+fc\s+{_NUM}\s*hz)?(?:\s+gain\s+{_NUM}\s*db)?(?:\s+q\s+{_NUM})?",
    re.I,
)


def _num(text: str | None) -> float | None:
    return None if text is None else float(text.replace(",", "."))


def parse_filter_list(text: str) -> tuple[float, list[dict]]:
    """(preamp dB, filters) from a plain text filter list.

    Reads the lines AutoEQ and Equalizer APO write::

        Preamp: -6.4 dB
        Filter 1: ON PK Fc 105 Hz Gain -3.5 dB Q 0.70
        Filter 2: ON LSC Fc 105 Hz Gain 6.0 dB Q 0.70

    Shelves without a Q take 0.707. Lines of other kinds (comments,
    unsupported filter types such as LP/HP) are skipped; ValueError when
    nothing usable is left.
    """
    preamp = 0.0
    filters: list[dict] = []
    seen_preamp = False
    for line in str(text or "").splitlines():
        m = _PREAMP_RE.match(line)
        if m:
            preamp = _clamped(_num(m.group(1)), -PEQ_GAIN_DB, PEQ_GAIN_DB, 0.0)
            seen_preamp = True
            continue
        m = _FILTER_RE.match(line)
        if not m:
            continue
        state, code, fc, gain, q = m.groups()
        kind = _AUTOEQ_TYPES.get(code.upper())
        if kind is None or fc is None or gain is None:
            continue
        cleaned = clean_filter(
            {
                "type": kind,
                "freq": _num(fc),
                "gain": _num(gain),
                "q": _num(q) if q is not None else PEQ_DEFAULT_Q,
                "enabled": state.upper() == "ON",
            }
        )
        if cleaned is not None:
            filters.append(cleaned)
    if not filters and not seen_preamp:
        raise ValueError("No Preamp or Filter lines found in the filter list.")
    return preamp, filters[:PEQ_MAX_FILTERS]


def _q_text(q: float) -> str:
    """Two decimals as AutoEQ writes them, three when that would lose some."""
    two = f"{q:.2f}"
    return two if float(two) == q else f"{q:.3f}"


def _gain_text(gain: float) -> str:
    one = f"{gain:.1f}"
    return one if float(one) == gain else f"{gain:.2f}"


def format_filter_list(preamp: float, filters: list[dict]) -> str:
    """The filters as an AutoEQ text list, which parse_filter_list reads back."""
    lines = [f"Preamp: {_gain_text(preamp)} dB"]
    for index, f in enumerate(clean_filters(filters), start=1):
        lines.append(
            f"Filter {index}: {'ON' if f['enabled'] else 'OFF'} "
            f"{_AUTOEQ_NAMES[f['type']]} Fc {f['freq']:g} Hz "
            f"Gain {_gain_text(f['gain'])} dB Q {_q_text(f['q'])}"
        )
    return "\n".join(lines) + "\n"


def mpv_properties(options: PlayerOptions) -> dict[str, Any]:
    """The mpv properties the options set (filters apart: see filter_chain)."""
    return {
        "audio-device": options.audio_device or "auto",
        "audio-spdif": ",".join(options.spdif),
        "audio-exclusive": options.exclusive,
        # "weak", not "yes": "yes" keeps the output as the first file opened
        # it, so after a PCM track an AC-3 one was decoded to PCM instead of
        # going out as bitstream (and a PCM track after a bitstreamed one hit
        # an output still in S/PDIF mode). "weak" stays gapless between files
        # of one format and reopens the output when the format changes.
        "gapless-audio": "weak" if options.gapless else "no",
        "replaygain": options.replaygain,
        "replaygain-preamp": options.replaygain_preamp,
        # With "Turn quiet songs up" mpv may raise a song past its peak;
        # the limiter in filter_chain keeps it from clipping instead.
        "replaygain-clip": options.replaygain_clip and not quiet_boost_active(options),
        "audio-delay": options.audio_delay_ms / 1000.0,
        "audio-pitch-correction": options.pitch_correction,
        "audio-channels": (
            "auto-safe" if options.channels == "auto" else options.channels
        ),
        "audio-normalize-downmix": True,
    }


def is_bitstreamed(codec: str | None, spdif: list[str]) -> bool:
    """Whether `codec` goes out compressed with these bitstream settings."""
    return any(s in spdif for s in _SPDIF_FOR_CODEC.get((codec or "").lower(), ()))


_CODEC_NAMES = {"ac3": "AC-3", "eac3": "E-AC-3", "dts": "DTS", "truehd": "TrueHD"}
_SAMPLE_BITS = {
    "u8": "8-bit",
    "s16": "16-bit",
    "s24": "24-bit",
    "s32": "32-bit",
    "s64": "64-bit",
    "float": "32-bit float",
    "double": "64-bit float",
}


def output_label(codec: str | None, params: dict | None, out: dict | None) -> str:
    """What reaches the device: "Bitstream AC-3 5.1", "PCM 24-bit / 96 kHz 5.1"."""
    out = out if isinstance(out, dict) else {}
    params = params if isinstance(params, dict) else {}
    fmt = str(out.get("format") or "")
    if fmt.startswith("spdif"):
        name = _CODEC_NAMES.get((codec or "").lower(), (codec or "").upper())
        channels = str(params.get("hr-channels") or params.get("channels") or "")
        return " ".join(p for p in ("Bitstream", name, channels) if p)
    if not fmt:
        return ""
    bits = _SAMPLE_BITS.get(fmt[:-1] if fmt.endswith("p") else fmt, fmt)
    rate = out.get("samplerate")
    khz = f"{rate / 1000:g} kHz" if isinstance(rate, (int, float)) and rate else ""
    out_channels = str(out.get("hr-channels") or out.get("channels") or "")
    head = " / ".join(p for p in (f"PCM {bits}", khz) if p)
    return f"{head} {out_channels}".strip()


# ── Loading libmpv ───────────────────────────────────────────────────────────

_mpv_module: Any = None
_mpv_error: str | None = None
_load_lock = threading.Lock()


def _libmpv_candidates() -> list[str]:
    """Where libmpv may be when the system loader does not find it.

    ``MORNYE_LIBMPV`` first; then next to the executable (a packaged app
    ships it there) and the usual Homebrew/MacPorts/Linux prefixes.
    """
    found: list[str] = []
    override = os.environ.get("MORNYE_LIBMPV", "").strip()
    if override and Path(override).is_file():
        found.append(override)
    here = [Path(sys.executable).resolve().parent, Path(__file__).resolve().parents[2]]
    if getattr(sys, "_MEIPASS", None):
        here.insert(0, Path(sys._MEIPASS))  # type: ignore[attr-defined]
    if sys.platform == "win32":
        names: tuple[str, ...] = ("libmpv-2.dll", "mpv-2.dll", "mpv-1.dll")
        dirs = here
    elif sys.platform == "darwin":
        names = ("libmpv.2.dylib", "libmpv.dylib")
        dirs = [
            *here,
            Path("/opt/homebrew/lib"),
            Path("/usr/local/lib"),
            Path("/opt/local/lib"),
        ]
    else:
        names = ("libmpv.so.2", "libmpv.so.1", "libmpv.so")
        dirs = [
            *here,
            Path("/usr/lib"),
            Path("/usr/local/lib"),
            Path("/usr/lib/x86_64-linux-gnu"),
        ]
    found += [str(d / n) for d in dirs for n in names if (d / n).is_file()]
    return found


def load_mpv() -> Any:
    """The python-mpv module, with libmpv loaded; NativePlayerUnavailable if not.

    python-mpv looks libmpv up with ``ctypes.util.find_library``, which on
    macOS does not search Homebrew's /opt/homebrew/lib and on Windows only
    the PATH. When the plain lookup finds nothing, the import is retried
    with the lookup answering from the candidates above.
    """
    global _mpv_module, _mpv_error
    with _load_lock:
        if _mpv_module is not None:
            return _mpv_module
        if _mpv_error is not None:
            raise NativePlayerUnavailable(_mpv_error)
        try:
            import mpv  # python-mpv

            _mpv_module = mpv
            return mpv
        except ImportError:
            _mpv_error = "python-mpv is not installed (pip install python-mpv)."
            raise NativePlayerUnavailable(_mpv_error) from None
        except OSError:
            sys.modules.pop("mpv", None)
        candidates = _libmpv_candidates()
        if not candidates:
            _mpv_error = "libmpv was not found (see the Player section of the guide)."
            raise NativePlayerUnavailable(_mpv_error)
        original = ctypes.util.find_library

        def find_library(name: str) -> str | None:
            if name.startswith(("mpv", "libmpv")):
                return candidates[0]
            return original(name)

        if sys.platform == "win32":
            folder = str(Path(candidates[0]).parent)
            os.environ["PATH"] = folder + os.pathsep + os.environ.get("PATH", "")
            with contextlib.suppress(OSError, AttributeError):
                os.add_dll_directory(folder)  # type: ignore[attr-defined]
        ctypes.util.find_library = find_library
        try:
            import mpv

            _mpv_module = mpv
            return mpv
        except (ImportError, OSError) as exc:
            sys.modules.pop("mpv", None)
            _mpv_error = f"libmpv could not be loaded: {exc}"
            raise NativePlayerUnavailable(_mpv_error) from exc
        finally:
            ctypes.util.find_library = original


def _reset_loader_for_tests() -> None:
    global _mpv_module, _mpv_error
    _mpv_module = None
    _mpv_error = None


# ── The player ───────────────────────────────────────────────────────────────

#: Properties whose changes the page hears about.
_OBSERVED = (
    "time-pos",
    "duration",
    "pause",
    "audio-codec-name",
    "audio-params",
    "audio-out-params",
    "current-ao",
)


#: The system's media keys, as mpv names them, and what the page does for
#: each. The macOS Now Playing commands, the Windows media overlay and a
#: keyboard's media keys reach mpv as these keys; its own bindings for them
#: are off (input_default_bindings), and they would only walk mpv's
#: two-entry playlist anyway. The page owns the queue, so each key goes to
#: it. PLAY and PAUSE toggle, as in mpv's input.conf.
_MEDIA_KEYS = {
    "PLAY": "toggle",
    "PAUSE": "toggle",
    "PLAYPAUSE": "toggle",
    "PLAYONLY": "play",
    "PAUSEONLY": "pause",
    "STOP": "stop",
    "NEXT": "nexttrack",
    "PREV": "previoustrack",
    "FORWARD": "seekforward",
    "REWIND": "seekbackward",
}
_MEDIA_MESSAGE = "mornye-media"


#: Properties fixed when mpv opens the audio output (see NativePlayer._reopen).
_OUTPUT_PROPERTIES = ("audio-spdif", "audio-exclusive", "audio-device")


def _now_playing_tags(state: dict) -> dict:
    """Title, artist and album for Now Playing, from mpv's metadata (its
    keys keep the file's case: TITLE in a FLAC, title in an M4A)."""
    meta = state.get("metadata") or {}
    folded = (
        {str(k).lower(): v for k, v in meta.items()} if isinstance(meta, dict) else {}
    )
    title = folded.get("title") or state.get("media-title") or ""
    return {
        "title": str(title),
        "artist": str(folded.get("artist") or folded.get("album_artist") or ""),
        "album": str(folded.get("album") or ""),
    }


class NativePlayer:
    """One mpv instance, created on first use, and its events to the page.

    `emit(event: dict)` is called from a sender thread of its own, never
    from mpv's event thread: the desktop window's evaluate_js waits for the
    page, and mpv's thread must not wait on anything.
    """

    def __init__(
        self,
        emit: Callable[[dict], None],
        *,
        options: PlayerOptions | None = None,
        mpv_factory: Callable[..., Any] | None = None,
        media_controls: bool = True,
    ) -> None:
        self._emit = emit
        self._options = options if options is not None else load_options()
        self._mpv_factory = mpv_factory
        self._media_controls = media_controls
        self._mpv: Any = None
        self._lock = threading.RLock()
        self._events: queue.Queue[dict | None] = queue.Queue()
        self._sender: threading.Thread | None = None
        self._current: str | None = None
        self._next: str | None = None
        self._last_time_sent = 0.0
        self._pending_time: float | None = None
        self._state: dict[str, Any] = {}
        self._paused = True
        self._advancing = False
        self._closed = False
        # (position, paused) to restore once a reopened file has loaded.
        self._resume: tuple[float, bool] | None = None
        # Whether the current file has finished opening. A seek before that
        # fails ("Error running mpv command", -12) — on a network share the
        # open takes long enough for the page's first seek to land in it —
        # so it is held in `_pending_seek` and made on file-loaded.
        self._loaded = False
        self._pending_seek: float | None = None
        # The output refused bitstream: play PCM until the setting changes.
        self._spdif_failed = False
        # macOS media keys and Now Playing (core/macos_now_playing.py),
        # registered with the first mpv instance.
        self._now_playing: Any = None

    # ── lifecycle ──

    @property
    def options(self) -> PlayerOptions:
        return self._options

    def _player(self) -> Any:
        with self._lock:
            if self._closed:
                raise NativePlayerUnavailable("The native player was closed.")
            if self._mpv is not None:
                return self._mpv
            factory = self._mpv_factory or load_mpv().MPV
            kwargs: dict[str, Any] = dict(
                video=False,
                vo="null",
                ytdl=False,
                terminal=False,
                input_default_bindings=False,
                input_vo_keyboard=False,
                osc=False,
                idle="yes",
                keep_open="no",
                audio_display="no",
            )
            if self._media_controls and self._mpv_factory is None:
                # On macOS libmpv's media-controls never registers with the
                # system when mpv is embedded, so Mornye registers itself.
                from . import macos_now_playing

                self._now_playing = macos_now_playing.create(self._on_now_playing)
            if self._media_controls and self._now_playing is None:
                # libmpv leaves the system's media keys and Now Playing off;
                # the CLI player turns them on itself.
                kwargs["media_controls"] = "yes"
            try:
                player = factory(**kwargs)
            except Exception as exc:
                if "media_controls" not in kwargs:
                    raise NativePlayerUnavailable(
                        f"mpv could not start: {exc}"
                    ) from exc
                kwargs.pop("media_controls")
                try:
                    player = factory(**kwargs)
                except Exception as exc2:
                    raise NativePlayerUnavailable(
                        f"mpv could not start: {exc2}"
                    ) from exc2
            self._mpv = player
            self._apply(player, self._options, codec=None)
            for name in _OBSERVED:
                player.observe_property(name, self._on_property)
            if self._now_playing is not None:
                player.observe_property("metadata", self._on_property)
                player.observe_property("media-title", self._on_property)
            player.register_event_callback(self._on_event)
            self._bind_media_keys(player)
            self._start_sender()
            return player

    @staticmethod
    def _bind_media_keys(player: Any) -> None:
        for key, action in _MEDIA_KEYS.items():
            try:
                player.command(
                    "keybind", key, f"script-message {_MEDIA_MESSAGE} {action}"
                )
            except Exception:  # an mpv without `keybind`: the keys stay mpv's
                logger.debug("mpv keybind %s failed", key, exc_info=True)
                return

    def _start_sender(self) -> None:
        if self._sender is not None:
            return
        self._sender = threading.Thread(
            target=self._send_loop, name="mornye-native-player-events", daemon=True
        )
        self._sender.start()

    def _on_now_playing(self, action: str, detail: dict) -> None:
        """A media key or a Now Playing command: the page owns the queue."""
        self._post({"type": "media", "action": action, **detail})

    def _now_playing_update(self, **fields: Any) -> None:
        if self._now_playing is not None:
            self._now_playing.update(**fields)

    def close(self) -> None:
        if self._now_playing is not None:
            with contextlib.suppress(Exception):
                self._now_playing.close()
            self._now_playing = None
        with self._lock:
            self._closed = True
            player, self._mpv = self._mpv, None
        if player is not None:
            with contextlib.suppress(Exception):
                player.terminate()
        self._events.put(None)

    # ── status ──

    def status(self) -> dict:
        """Whether the engine can play here, and the stored options."""
        available, reason = True, ""
        if self._mpv_factory is None:
            try:
                load_mpv()
            except NativePlayerUnavailable as exc:
                available, reason = False, str(exc)
        return {
            "available": available,
            "reason": reason,
            "options": self._options.to_dict(),
            "eq_bands": list(EQ_BANDS_HZ),
            "eq_presets": {k: list(v) for k, v in EQ_PRESETS.items()},
            "hdmi_only": list(HDMI_ONLY_CODECS),
        }

    def devices(self) -> list[dict]:
        player = self._player()
        out = []
        for d in player.audio_device_list or []:
            name = str(d.get("name", ""))
            if name:
                out.append(
                    {"name": name, "description": str(d.get("description") or name)}
                )
        return out

    def stats(self) -> dict:
        """What the Statistics panel shows."""
        player = self._player()

        def get(name: str) -> Any:
            # Attribute access reads the live property; python-mpv's
            # player[name] reads only the option of that name.
            try:
                return getattr(player, name.replace("-", "_"))
            except Exception:
                return None

        codec = get("audio-codec-name")
        params = get("audio-params")
        out = get("audio-out-params")
        cache = get("demuxer-cache-state")
        filters = get("af")
        return {
            "path": self._current or "",
            "codec": codec or "",
            "codec_desc": get("audio-codec") or "",
            "params": params if isinstance(params, dict) else {},
            "out_params": out if isinstance(out, dict) else {},
            "output": output_label(codec, params, out),
            "bitstream": str((out or {}).get("format", "")).startswith("spdif"),
            "ao": get("current-ao") or "",
            "device": get("audio-device") or "",
            "bitrate": get("audio-bitrate"),
            "cache_s": get("demuxer-cache-duration"),
            "cache_bytes": cache.get("fw-bytes") if isinstance(cache, dict) else None,
            "speed": get("speed"),
            "delay_ms": round((get("audio-delay") or 0) * 1000),
            "ab_loop": [get("ab-loop-a"), get("ab-loop-b")],
            "filters": [
                str(f.get("name")) for f in filters or [] if isinstance(f, dict)
            ],
            "replaygain": self._options.replaygain,
            "volume": get("volume"),
        }

    # ── commands ──

    def load(self, path: str, next_path: str | None = None) -> None:
        player = self._player()
        with self._lock:
            self._current = path
            self._next = None
            self._advancing = False
            self._resume = None
            self._loaded = False
            self._pending_seek = None
            for key in ("time-pos", "duration"):
                self._state.pop(key, None)
            player.command("loadfile", path, "replace")
            player.pause = False
            self._paused = False
        self.set_next(next_path)

    def set_next(self, path: str | None) -> None:
        """Queues `path` after the current file, for a gapless change; None
        drops whatever was queued. The page owns the order: this is only
        ever the one file it expects next."""
        player = self._player()
        with self._lock:
            if path == self._next:
                return
            with contextlib.suppress(Exception):
                player.command("playlist-clear")  # keeps the current entry
            self._next = None
            if path and self._current is not None:
                player.command("loadfile", path, "append")
                self._next = path

    def command(self, name: str, value: Any = None) -> None:
        player = self._player()
        if name == "play":
            player.pause = False
        elif name == "pause":
            player.pause = True
        elif name == "seek":
            with self._lock:
                if not self._loaded:
                    self._pending_seek = float(value)
                    return
            player.command("seek", float(value), "absolute", "exact")
        elif name == "volume":
            player.volume = max(0.0, min(100.0, float(value) * 100.0))
        elif name == "mute":
            player.mute = bool(value)
        elif name == "speed":
            player.speed = max(0.25, min(4.0, float(value)))
        elif name == "ab_loop":
            a, b = (
                value
                if isinstance(value, (list, tuple)) and len(value) == 2
                else (None, None)
            )
            player["ab-loop-a"] = "no" if a is None else float(a)
            player["ab-loop-b"] = "no" if b is None else float(b)
        elif name == "stop":
            with self._lock:
                self._current = None
                self._next = None
            player.command("stop")
        else:
            raise ValueError(f"Unknown command: {name}")

    def set_options(self, raw: dict) -> PlayerOptions:
        """Stores new options and applies them to a running mpv at once.

        Choosing a preset (without gains) loads the preset's gains.
        """
        raw = raw if isinstance(raw, dict) else {}
        merged = {**self._options.to_dict(), **raw}
        if raw.get("eq_preset") in EQ_PRESETS and "eq_gains" not in raw:
            merged["eq_gains"] = list(EQ_PRESETS[raw["eq_preset"]])
        options = PlayerOptions.from_dict(merged)
        save_options(options)
        if options.spdif != self._options.spdif:
            self._spdif_failed = False  # a new choice deserves a new try
        before = self._output_properties(self._options)
        self._options = options
        with self._lock:
            player = self._mpv
        if player is not None:
            self._apply(player, options, codec=self._state.get("audio-codec-name"))
            if self._output_properties(options) != before:
                self._reopen()
        return options

    def _effective(self, options: PlayerOptions) -> PlayerOptions:
        return replace(options, spdif=[]) if self._spdif_failed else options

    def _output_properties(self, options: PlayerOptions) -> tuple:
        props = mpv_properties(self._effective(options))
        return tuple(props[k] for k in _OUTPUT_PROPERTIES)

    def _reopen(self) -> None:
        """Reloads the playing file where it was, on the new output.

        Bitstream, exclusive mode and the device are fixed when mpv opens
        the audio output. Switched under a playing file, mpv tears the
        decoder and output down in place, and on Windows (WASAPI) that
        often ends the file with an error: the song stopped and the page
        showed "error". Opening the file again sets them up from scratch.
        """
        with self._lock:
            player, path = self._mpv, self._current
            if player is None or path is None:
                return
            nxt = self._next
            self._next = None
            self._advancing = False
            self._resume = (self._time(), self._paused)
            self._loaded = False
            # Held silently while it reloads: the page keeps its state, and
            # the play that follows is one it already shows.
            self._paused = True
            player.pause = True
            player.command("loadfile", path, "replace")
        self.set_next(nxt)

    def _finish_resume(self, position: float, paused: bool) -> None:
        with self._lock:
            player = self._mpv
        if player is None:
            return
        try:
            if position > 0.5:
                player.command("seek", position, "absolute", "exact")
            player.pause = paused
        except Exception:
            logger.debug("Could not resume after reopening", exc_info=True)

    def _fall_back_to_pcm(self) -> None:
        with self._lock:
            player = self._mpv
        if player is None:
            return
        try:
            player["audio-spdif"] = ""
            self._apply_filters(
                player, self._options, self._state.get("audio-codec-name")
            )
            self._reopen()
        except Exception:
            logger.warning("Could not reopen the file as PCM", exc_info=True)

    def _apply(self, player: Any, options: PlayerOptions, codec: str | None) -> None:
        for name, value in mpv_properties(self._effective(options)).items():
            try:
                player[name] = value
            except Exception as exc:
                logger.warning("mpv refused %s=%r: %s", name, value, exc)
        self._apply_filters(player, options, codec)

    def _apply_filters(
        self, player: Any, options: PlayerOptions, codec: str | None
    ) -> None:
        # A compressed stream cannot be filtered: with bitstream on for
        # this codec the chain stays empty, or mpv would decode it to PCM.
        spdif = self._effective(options).spdif
        chain = [] if is_bitstreamed(codec, spdif) else filter_chain(options)
        try:
            player["af"] = ",".join(chain)
        except Exception as exc:
            logger.warning("mpv refused the audio filters %r: %s", chain, exc)
            with contextlib.suppress(Exception):
                player["af"] = ""
            self._post(
                {"type": "warning", "message": f"Audio filters not applied: {exc}"}
            )

    # ── events from mpv (mpv's own thread) ──

    def _on_property(self, name: str, value: Any) -> None:
        self._state[name] = value
        if name == "time-pos":
            if value is None:
                return
            now = time.monotonic()
            if now - self._last_time_sent >= TIME_UPDATE_INTERVAL_S:
                self._last_time_sent = now
                self._pending_time = None
                self._post({"type": "timeupdate", "time": float(value)})
            else:
                self._pending_time = float(value)
        elif name == "pause":
            paused = bool(value)
            self._now_playing_update(paused=paused, position=self._time())
            if paused != self._paused:
                self._paused = paused
                self._post(
                    {"type": "pause" if paused else "play", "time": self._time()}
                )
        elif name == "duration":
            if value:
                self._now_playing_update(duration=float(value), position=self._time())
                self._post({"type": "loadedmetadata", "duration": float(value)})
        elif name in ("metadata", "media-title"):
            self._now_playing_update(**_now_playing_tags(self._state))
        elif name == "audio-codec-name":
            with self._lock:
                player = self._mpv
            if player is not None:
                self._apply_filters(player, self._options, value)
            self._post_output()
        elif name in ("audio-params", "audio-out-params", "current-ao"):
            self._post_output()

    def _time(self) -> float:
        t = self._state.get("time-pos")
        return float(t) if isinstance(t, (int, float)) else 0.0

    def _post_output(self) -> None:
        codec = self._state.get("audio-codec-name")
        out = self._state.get("audio-out-params")
        label = output_label(codec, self._state.get("audio-params"), out)
        if label:
            self._post(
                {
                    "type": "output",
                    "label": label,
                    "codec": codec or "",
                    "bitstream": isinstance(out, dict)
                    and str(out.get("format", "")).startswith("spdif"),
                    "ao": self._state.get("current-ao") or "",
                }
            )

    def _on_event(self, event: Any) -> None:
        data = _event_dict(event)
        kind = data.get("event")
        if kind == "file-loaded":
            # The file that loads after a natural end, with one queued, is
            # the queued one: mpv went on to it without a gap. (Reading the
            # `path` property from this thread returns nothing.)
            with self._lock:
                advanced = self._advancing and self._next is not None
                self._advancing = False
                if advanced:
                    self._current, self._next = self._next, None
                current = self._current
                resume, self._resume = self._resume, None
                self._loaded = True
                pending, self._pending_seek = self._pending_seek, None
                if pending is not None and resume is None and not advanced:
                    resume = (pending, self._paused)
            if advanced:
                # The duration goes with the change. mpv reports a property
                # only when it changes, so a file that follows itself (repeat
                # one) or one as long as the last never reports it again, and
                # the page, which forgets the old one on a change, was left
                # with no duration: no remaining time, a seek bar that did not
                # move. When a new value does come in later it still arrives
                # as "loadedmetadata" and replaces this one.
                advanced_event: dict = {"type": "advanced", "path": current}
                duration = self._state.get("duration")
                if isinstance(duration, (int, float)) and duration > 0:
                    advanced_event["duration"] = float(duration)
                self._post(advanced_event)
            elif resume is not None:
                # Off mpv's event thread: a seek from here would wait on it.
                threading.Thread(
                    target=self._finish_resume, args=resume, daemon=True
                ).start()
        elif kind == "end-file":
            reason = data.get("reason")
            # Only a file that was going out as bitstream can blame the
            # output for refusing it. A PCM file (ALAC, FLAC, AAC...) that
            # fails is a real error: blaming bitstream for it switched
            # passthrough off for the rest of the session, so every AC-3
            # track after it played as PCM. An unknown codec keeps the
            # benefit of the doubt.
            codec = self._state.get("audio-codec-name")
            if (
                reason == "error"
                and self._options.spdif
                and not self._spdif_failed
                and self._current is not None
                and (not codec or is_bitstreamed(codec, self._options.spdif))
            ):
                # The output would not take the compressed stream (a device
                # without passthrough, a shared-mode WASAPI endpoint): carry
                # on as PCM rather than stopping the song.
                self._spdif_failed = True
                self._post(
                    {
                        "type": "warning",
                        "message": "This output does not accept bitstream; "
                        "playing as PCM instead.",
                    }
                )
                threading.Thread(target=self._fall_back_to_pcm, daemon=True).start()
            elif reason == "error":
                message = data.get("file_error") or "mpv could not play the file."
                self._post({"type": "error", "message": str(message)})
            elif reason == "eof":
                with self._lock:
                    queued = self._next is not None
                    self._advancing = queued
                if not queued:
                    self._post({"type": "ended"})
        elif kind == "client-message":
            args = data.get("args") or []
            if (
                len(args) == 2
                and args[0] == _MEDIA_MESSAGE
                and args[1] in _MEDIA_KEYS.values()
            ):
                self._post({"type": "media", "action": args[1]})
        elif kind == "playback-restart":
            self._now_playing_update(position=self._time())
            self._post({"type": "seeked", "time": self._time()})

    # ── to the page (sender thread) ──

    def _post(self, event: dict) -> None:
        self._events.put(event)

    def _send_loop(self) -> None:
        while True:
            try:
                event = self._events.get(timeout=TIME_UPDATE_INTERVAL_S)
            except queue.Empty:
                event = {}
            if event is None:
                return
            # A position that arrived inside the throttle window still goes
            # out once the window has passed, so a pause lands on its time.
            pending = self._pending_time
            if (
                pending is not None
                and time.monotonic() - self._last_time_sent >= TIME_UPDATE_INTERVAL_S
            ):
                self._pending_time = None
                self._last_time_sent = time.monotonic()
                self._send({"type": "timeupdate", "time": pending})
            if event:
                self._send(event)

    def _send(self, event: dict) -> None:
        try:
            self._emit(event)
        except Exception:
            logger.debug(
                "native player event %s not delivered", event.get("type"), exc_info=True
            )


def _event_dict(event: Any) -> dict:
    """python-mpv's MpvEvent as mpv's own node form, or a test's dict."""
    if isinstance(event, dict):
        return event
    try:
        data = event.as_dict(
            decoder=lambda b: (
                b.decode("utf-8", "replace") if isinstance(b, bytes) else b
            )
        )
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}
