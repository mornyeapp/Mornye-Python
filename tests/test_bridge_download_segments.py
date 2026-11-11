"""The bridge feature a manifest-based provider cannot do without.

`file.download` fetches one URL into one file. A Tidal (or any DASH/HLS)
track is not one URL: it is an init segment plus N media segments, and only
their concatenation *in manifest order* is a stream a demuxer can open. The
tidal-web extension declares `downloadSegments@1` in its manifest and calls
`file.downloadSegments(...)`; the host bridge never defined it, so every such
download died two seconds in with

    file.downloadSegments is not a function

These tests drive the real _bridge.js under the real Node against a local
HTTP server, because the parts worth pinning down are the ones a plausible
implementation gets wrong: order must follow the manifest rather than the
order the parallel requests happened to finish in, a failed segment must
leave no half-written output or `.part` debris behind, and an aged-out CDN
URL has to come back as `expired_stream` — that is the one error the
extension retries instead of reporting the track as dead.
"""

from __future__ import annotations

import http.server
import os
import shutil
import threading
import wave
from pathlib import Path

import pytest

from mornye.extensions.runtime import JSRuntime

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="needs Node to exercise the real bridge"
)

# Deliberately uneven and out of size order: a big later segment finishes
# after a small earlier one, so a concatenation that followed completion
# order instead of index order would come out visibly scrambled.
SEGMENTS = {
    "/seg0": b"INIT",
    "/seg1": b"A" * 40_000,
    "/seg2": b"B" * 200,
    "/seg3": b"C" * 90_000,
    "/seg4": b"D" * 10,
}
EXPECTED = b"".join(SEGMENTS[f"/seg{i}"] for i in range(len(SEGMENTS)))
RANGED_SEGMENTS = {
    "/ranged0": ("bytes=0-3", SEGMENTS["/seg0"]),
    "/ranged-seg2": ("bytes=0-199", SEGMENTS["/seg2"]),
}


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/moved-ranged":
            if self.headers.get("Range") != "bytes=0-199":
                self.send_response(416)
            else:
                self.send_response(302)
                self.send_header("Location", "/ranged-seg2")
            self.end_headers()
            return
        ranged = RANGED_SEGMENTS.get(self.path)
        if ranged is not None:
            expected_range, body = ranged
            if self.headers.get("Range") != expected_range:
                self.send_response(416)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/gone":
            self.send_response(410)
            self.end_headers()
            return
        if self.path == "/moved":
            self.send_response(302)
            self.send_header("Location", "/seg2")
            self.end_headers()
            return
        body = SEGMENTS.get(self.path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


EXTENSION = """
registerExtension({
  initialize: function () {},
  probe: function (urls, outputPath, maxParallel, onProgress) {
    return file.downloadSegments(urls, outputPath, {
      maxParallel: maxParallel,
      persistentCheckpoint: true,
      onProgress: onProgress
    });
  },
  probeFfmpeg: function (inputPath, outputPath, codec) {
    return ffmpeg.convert(inputPath, outputPath, { codec: codec });
  }
});
"""


@pytest.fixture
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # The guards are the real ones: loopback is reachable only with the
    # documented opt-out, and the output directory has to be sanctioned.
    monkeypatch.setenv("MORNYE_EXT_ALLOW_PRIVATE_NETWORK", "1")
    monkeypatch.setenv("MORNYE_EXT_WRITABLE_DIRS", str(tmp_path))
    ext = tmp_path / "probe.js"
    ext.write_text(EXTENSION)
    rt = JSRuntime(ext)
    rt.start()
    yield rt
    rt.stop()


def _urls(server: str, count: int = len(SEGMENTS)) -> list[str]:
    return [f"{server}/seg{i}" for i in range(count)]


def test_segments_are_joined_in_manifest_order(runtime, server, tmp_path) -> None:
    out = tmp_path / "track.m4a"

    result = runtime.call("probe", _urls(server), str(out), 4, None)

    assert result["success"] is True, result
    assert out.read_bytes() == EXPECTED
    assert result["size"] == len(EXPECTED)


@pytest.mark.parametrize("parallel", [1, 2, 8])
def test_the_order_holds_however_many_requests_are_in_flight(
    runtime, server, tmp_path, parallel
) -> None:
    out = tmp_path / f"track-{parallel}.m4a"

    assert runtime.call("probe", _urls(server), str(out), parallel, None)["success"]
    assert out.read_bytes() == EXPECTED


def test_a_redirected_segment_is_followed(runtime, server, tmp_path) -> None:
    out = tmp_path / "redirected.m4a"

    result = runtime.call(
        "probe", [f"{server}/seg0", f"{server}/moved"], str(out), 2, None
    )

    assert result["success"] is True, result
    assert out.read_bytes() == SEGMENTS["/seg0"] + SEGMENTS["/seg2"]


def test_object_segments_preserve_ranges_across_redirects(
    runtime, server, tmp_path
) -> None:
    out = tmp_path / "ranged.m4a"
    segments = [
        {"url": f"{server}/ranged0", "headers": {"Range": "bytes=0-3"}},
        {
            "url": f"{server}/moved-ranged",
            "headers": {"Range": "bytes=0-199"},
        },
    ]

    result = runtime.call("probe", segments, str(out), 2, None)

    assert result["success"] is True, result
    assert out.read_bytes() == SEGMENTS["/seg0"] + SEGMENTS["/seg2"]


def test_an_expired_url_is_reported_as_such_and_leaves_nothing_behind(
    runtime, server, tmp_path
) -> None:
    out = tmp_path / "expired.m4a"
    urls = [f"{server}/seg0", f"{server}/gone", f"{server}/seg1"]

    result = runtime.call("probe", urls, str(out), 2, None)

    # 410 is a signed CDN saying the URL aged out. The extension retries only
    # on this error_type; anything else it reports as a dead track.
    assert result["success"] is False
    assert result["error_type"] == "expired_stream"
    assert not out.exists()
    # No .segN.part debris either — a retry must not concatenate it.
    assert list(tmp_path.glob("*.part")) == []


def test_a_missing_segment_fails_as_an_ordinary_download_error(
    runtime, server, tmp_path
) -> None:
    out = tmp_path / "missing.m4a"

    result = runtime.call(
        "probe", [f"{server}/seg0", f"{server}/nope"], str(out), 2, None
    )

    assert result["success"] is False
    assert result["error_type"] == "download_error"
    assert not out.exists()
    assert list(tmp_path.glob("*.part")) == []


def test_an_empty_manifest_is_refused_rather_than_writing_an_empty_file(
    runtime, tmp_path
) -> None:
    out = tmp_path / "empty.m4a"

    result = runtime.call("probe", [], str(out), 4, None)

    assert result["success"] is False
    assert not out.exists()


def test_the_bridge_reports_bytes_while_the_segments_arrive(
    runtime, server, tmp_path
) -> None:
    out = tmp_path / "progress.m4a"
    seen: list[float] = []

    result = runtime.call(
        "probe",
        _urls(server),
        str(out),
        2,
        None,
        progress_cb=lambda value, *rest: seen.append(value),
    )

    assert result["success"] is True, result
    assert seen, "no progress reached the host"
    assert seen[-1] == pytest.approx(1.0)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_the_bridge_remuxes_audio_for_extensions(runtime, tmp_path) -> None:
    source = tmp_path / "source.wav"
    output = tmp_path / "output.wav"
    with wave.open(str(source), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"\0\0" * 800)

    result = runtime.call("probeFfmpeg", str(source), str(output), "copy")

    assert result["success"] is True, result
    assert output.stat().st_size > 0


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_ffmpeg_cannot_write_where_the_extension_may_not(runtime, tmp_path) -> None:
    """ffmpeg is a child process, so _fsguard.js never sees its writes.

    The bridge checks the output itself; without that, ffmpeg.convert was a
    way to overwrite any file the user owns.
    """
    source = tmp_path / "source.wav"
    with wave.open(str(source), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"\0\0" * 80)
    outside = Path.home() / f".mornye-fsguard-probe-{os.getpid()}.wav"
    try:
        result = runtime.call("probeFfmpeg", str(source), str(outside), "copy")

        assert result["success"] is False
        assert "fsguard" in result["error"]
        assert not outside.exists()
    finally:
        outside.unlink(missing_ok=True)


def test_ffmpeg_only_reads_local_files(runtime, tmp_path) -> None:
    """A URL input would let ffmpeg fetch past _netguard.js."""
    result = runtime.call(
        "probeFfmpeg", "http://169.254.169.254/latest", str(tmp_path / "o.wav"), "copy"
    )

    assert result["success"] is False
    assert "not a local file" in result["error"]
