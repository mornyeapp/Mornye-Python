"""Carry an audio codec's configuration box across an ffmpeg remux.

Amazon's Dolby Atmos is AC-4 in an encrypted MP4. ffmpeg decrypts it but
can only write AC-4 into a MOV container, and its MOV muxer does not know
AC-4: it writes a bare ``ac-4`` sample entry without the ``dac4`` box, the
decoder specific information that tells a player the presentations and
channel layout. Players that do support AC-4 generally need that box to
open the file.

The encrypted original still has it, as a child of its protected (``enca``)
sample entry. :func:`restore_codec_config` copies every such child that the
decrypted file lacks (``sinf``, the protection description, excepted) into
the decrypted sample entry, fixing the sizes of the boxes around it, and
the chunk offsets too when the ``moov`` sits in front of the media data.
"""

from __future__ import annotations

import struct
from collections.abc import Iterator
from pathlib import Path

#: Boxes on the path from the top of the file to the sample descriptions.
_PATH = (b"moov", b"trak", b"mdia", b"minf", b"stbl", b"stsd")
#: Children of a protected sample entry that are not the codec's own.
_NOT_CONFIG = {b"sinf"}


class Mp4ConfigError(ValueError):
    """The file does not have the structure this module expects."""


def _boxes(
    buf: bytes | bytearray, start: int, end: int
) -> Iterator[tuple[bytes, int, int, int]]:
    """`(type, box_start, header_size, box_end)` for each box in a range."""
    pos = start
    while pos + 8 <= end:
        size, kind = struct.unpack_from(">I4s", buf, pos)
        header = 8
        if size == 1:
            size = struct.unpack_from(">Q", buf, pos + 8)[0]
            header = 16
        elif size == 0:
            size = end - pos
        if size < header or pos + size > end:
            raise Mp4ConfigError(f"malformed {kind!r} box")
        yield kind, pos, header, pos + size
        pos += size


def _has_sound_handler(buf: bytes | bytearray, start: int, end: int) -> bool:
    for kind, box, header, box_end in _boxes(buf, start, end):
        if kind == b"mdia":
            for k, b, h, _e in _boxes(buf, box + header, box_end):
                if k == b"hdlr":
                    return bytes(buf[b + h + 8 : b + h + 12]) == b"soun"
    return False


def _audio_entry(
    buf: bytes | bytearray,
) -> tuple[list[tuple[int, int]], int, int]:
    """The first sample entry of the first audio track.

    Returns its ancestors as `(box_start, header_size)`, from the `moov`
    down to the entry itself, the offset its child boxes start at, and the
    offset it ends at.
    """
    ancestors: list[tuple[int, int]] = []
    start, end = 0, len(buf)
    for name in _PATH:
        for kind, box, header, box_end in _boxes(buf, start, end):
            if kind != name:
                continue
            if name == b"trak" and not _has_sound_handler(buf, box + header, box_end):
                continue
            ancestors.append((box, header))
            start, end = box + header, box_end
            break
        else:
            raise Mp4ConfigError(f"no {name.decode()} box")
    start += 8  # stsd: version/flags + entry_count
    for _kind, entry, header, entry_end in _boxes(buf, start, end):
        ancestors.append((entry, header))
        payload = entry + header
        version = struct.unpack_from(">H", buf, payload + 8)[0]
        children = payload + 28 + {1: 16, 2: 36}.get(version, 0)
        return ancestors, children, entry_end
    raise Mp4ConfigError("no sample entry")


def codec_config_boxes(buf: bytes | bytearray) -> dict[bytes, bytes]:
    """The codec configuration boxes of the audio sample entry, by type."""
    _, children, entry_end = _audio_entry(buf)
    try:
        boxes = list(_boxes(buf, children, entry_end))
    except Mp4ConfigError:
        return {}
    return {
        kind: bytes(buf[box:box_end])
        for kind, box, _h, box_end in boxes
        if kind not in _NOT_CONFIG
    }


def _box_end(buf: bytearray, box: int, header: int) -> int:
    if header == 16:
        return box + struct.unpack_from(">Q", buf, box + 8)[0]
    size = struct.unpack_from(">I", buf, box)[0]
    return len(buf) if size == 0 else box + size


def _grow(buf: bytearray, box: int, header: int, delta: int) -> None:
    if header == 16:
        size = struct.unpack_from(">Q", buf, box + 8)[0]
        struct.pack_into(">Q", buf, box + 8, size + delta)
        return
    size = struct.unpack_from(">I", buf, box)[0]
    if size == 0:
        return  # "to the end of the file": still true
    if size + delta > 0xFFFFFFFF:
        raise Mp4ConfigError("box would outgrow a 32-bit size")
    struct.pack_into(">I", buf, box, size + delta)


def _shift_chunk_offsets(buf: bytearray, start: int, end: int, delta: int) -> None:
    for kind, box, header, box_end in _boxes(buf, start, end):
        payload = box + header
        if kind in (b"trak", b"mdia", b"minf", b"stbl"):
            _shift_chunk_offsets(buf, payload, box_end, delta)
        elif kind in (b"stco", b"co64"):
            width, fmt = (4, ">I") if kind == b"stco" else (8, ">Q")
            count = struct.unpack_from(">I", buf, payload + 4)[0]
            for i in range(count):
                at = payload + 8 + width * i
                struct.pack_into(
                    fmt, buf, at, struct.unpack_from(fmt, buf, at)[0] + delta
                )


def restore_codec_config(original: bytes, target: str | Path) -> list[str]:
    """Copy the codec configuration boxes `target` lacks from `original`.

    `original` is the file as it was before the remux (its bytes, since the
    remux replaces it); `target` is rewritten in place. Returns the types of
    the boxes added, empty when nothing was missing.
    """
    wanted = codec_config_boxes(original)
    path = Path(target)
    buf = bytearray(path.read_bytes())
    present = codec_config_boxes(buf)
    missing = [box for kind, box in wanted.items() if kind not in present]
    if not missing:
        return []
    top = list(_boxes(buf, 0, len(buf)))
    if any(kind == b"moof" for kind, *_ in top):
        raise Mp4ConfigError("fragmented output is not supported")

    extra = b"".join(missing)
    ancestors, _children, entry_end = _audio_entry(buf)
    moov, moov_header = ancestors[0]
    moov_end = _box_end(buf, moov, moov_header)
    # Media stored after the moov moves along with the inserted bytes; media
    # in front of it (ffmpeg's default layout) stays where it is.
    media_moves = any(kind == b"mdat" and box >= moov_end for kind, box, *_ in top)

    for box, header in ancestors:
        _grow(buf, box, header, len(extra))
    buf[entry_end:entry_end] = extra
    if media_moves:
        _shift_chunk_offsets(buf, moov + moov_header, moov_end + len(extra), len(extra))

    tmp = path.with_name(f"{path.stem}.config{path.suffix}")
    tmp.write_bytes(buf)
    tmp.replace(path)
    return [box[4:8].decode("latin1") for box in missing]
