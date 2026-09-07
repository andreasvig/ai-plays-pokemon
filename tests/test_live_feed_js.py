"""Runs the live-feed JS unit tests (``tests/js/live.test.mjs``) under pytest.

The live spectate view's event → box mapping is the one piece of frontend logic
with real rules in it: which box an event becomes, whether it belongs to a
gameplay TURN or to a COMPACTION BLOCK, and which boxes must not exist. Those
rules live in ``src/dashboard/web/src/lib/{live,feed}.js`` — plain ESM with no
svelte imports precisely so node can check them — and this test makes the suite
fail when they break, instead of leaving them to a browser walk.

node is a hard requirement of this repo's frontend (`npm run build` produces the
served SPA), so a missing node is a failure, not a skip.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SUITE = REPO / "tests/js/live.test.mjs"


def test_live_feed_js_unit_tests_pass():
    node = shutil.which("node")
    assert node, "node is required (the SPA is built with it) — install node"
    assert SUITE.exists(), f"missing JS suite: {SUITE}"
    proc = subprocess.run(
        [node, str(SUITE)],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=180,
    )
    # node:test exits non-zero on any failing test and prints a TAP-ish report.
    assert proc.returncode == 0, (
        f"node {SUITE.relative_to(REPO)} failed:\n{proc.stdout}\n{proc.stderr}"
    )
    # A green exit with zero tests run would be a silently empty suite.
    assert "# pass " in proc.stdout or "pass " in proc.stdout, proc.stdout
    assert "fail 0" in proc.stdout.replace("# ", ""), proc.stdout
