"""JSRuntime._read_loop: the extension's stdout, read in blocks.

A large reply (a 1920-track playlist is one JSON line of a few MB) used to
be read one byte per call into a growing `bytes`, which made receiving it
quadratic: about a minute for a reply the extension produced in three
seconds.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import time

from mornye.extensions.runtime import JSRuntime


def _runtime_reading(script: str) -> tuple[JSRuntime, list[dict]]:
    proc = subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(script)],
        stdout=subprocess.PIPE,
        bufsize=0,
    )
    runtime = object.__new__(JSRuntime)
    runtime._proc = proc
    runtime._pending = {}
    seen: list[dict] = []
    runtime._dispatch = seen.append  # type: ignore[method-assign]
    return runtime, seen


def test_a_multi_megabyte_reply_arrives_whole_and_fast() -> None:
    runtime, seen = _runtime_reading("""
        import json, sys, time
        out = sys.stdout.buffer
        tracks = [{"title": f"Track {i}", "artists": "Artist " * 20} for i in range(20000)]
        line = json.dumps({"id": 1, "result": {"tracks": tracks}}).encode() + b"\\n"
        # Several writes with pauses: the line reaches the reader in pieces.
        for start in range(0, len(line), 300_000):
            out.write(line[start:start + 300_000]); out.flush(); time.sleep(0.01)
        out.write(b'{"type": "ready"}\\n{"id": 2, "result": "x"}\\n\\n not json\\n')
        out.write(b'{"id": 3, "result": "last"}\\n')
        out.flush()
        """)
    started = time.monotonic()
    runtime._read_loop()
    elapsed = time.monotonic() - started

    assert [m.get("id") for m in seen] == [1, None, 2, 3]
    assert len(seen[0]["result"]["tracks"]) == 20000
    assert seen[-1]["result"] == "last"  # read to EOF, nothing left in the pipe
    assert elapsed < 10, f"reading took {elapsed:.1f}s"


def test_a_reply_without_a_final_newline_is_not_dispatched_half() -> None:
    runtime, seen = _runtime_reading("""
        import sys
        sys.stdout.buffer.write(b'{"id": 1, "result": "ok"}\\n{"id": 2, "res')
        """)

    runtime._read_loop()

    assert seen == [{"id": 1, "result": "ok"}]
