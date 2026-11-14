"""The engine's JavaScript tests (tests/js/*.test.mjs), run with Node's own
test runner when Node is installed (a development machine; the Pi has no
Node, and the engine itself never needs it)."""

import shutil
import subprocess
from pathlib import Path

import pytest

JS_TESTS = sorted((Path(__file__).resolve().parent / "js").glob("*.test.mjs"))


@pytest.mark.skipif(shutil.which("node") is None, reason="Node isn't installed")
@pytest.mark.parametrize("path", JS_TESTS, ids=[p.name for p in JS_TESTS])
def test_engine_js(path):
    result = subprocess.run(["node", "--test", str(path)], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
