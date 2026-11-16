"""Runs the JavaScript unit tests in tests/js with node's own runner (M4).

Until M4 the frontend's only check was ``node --check``. The tests live in
``tests/js/*.test.mjs`` (node:test, no dependencies) and cover the pure
functions of app.js and the handler interpreter of inline-handlers.js.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

JS_TESTS = Path(__file__).with_name("js")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_node_unit_tests_pass():
    files = sorted(str(p) for p in JS_TESTS.glob("*.test.mjs"))
    assert files
    run = subprocess.run(
        ["node", "--test", *files], capture_output=True, text=True, timeout=120
    )
    assert run.returncode == 0, run.stdout[-3000:] + run.stderr[-2000:]
