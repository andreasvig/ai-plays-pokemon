"""Runs EVERY JS unit suite in ``tests/js/`` under pytest.

The frontend's pure logic — the pieces with real rules rather than markup — is
deliberately written as import-free ESM in ``src/dashboard/web/src/lib/`` so node
can check it without a browser, and these are the suites:

- ``live.test.mjs`` → ``lib/{live,feed}.js``: the live spectate view's event →
  box mapping (which box an event becomes, whether it belongs to a gameplay TURN
  or a COMPACTION BLOCK, and which boxes must not exist).
- ``queue.test.mjs`` → ``lib/queue.js``: ``/api/queue``'s ``last_error`` → the
  dismissible dispatch-failure strip, including its dismissal identity.

Discovered by glob rather than listed, so adding ``tests/js/<name>.test.mjs``
puts it in the suite with no wiring here — and an empty ``tests/js/`` fails
rather than passing vacuously.

node is a hard requirement of this repo's frontend (`npm run build` produces the
served SPA), so a missing node is a failure, not a skip.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
JS_DIR = REPO / "tests/js"
SUITES = sorted(JS_DIR.glob("*.test.mjs"))


def test_the_js_suite_directory_is_not_empty():
    """Mutation control for the parametrisation below: a glob that matched
    nothing would collect zero cases and report green."""
    assert SUITES, f"no *.test.mjs found in {JS_DIR}"


@pytest.mark.parametrize("suite", SUITES, ids=lambda p: p.name)
def test_live_feed_js_unit_tests_pass(suite):
    node = shutil.which("node")
    assert node, "node is required (the SPA is built with it) — install node"
    assert suite.exists(), f"missing JS suite: {suite}"
    proc = subprocess.run(
        [node, str(suite)],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=180,
    )
    # node:test exits non-zero on any failing test and prints a TAP-ish report.
    assert proc.returncode == 0, (
        f"node {suite.relative_to(REPO)} failed:\n{proc.stdout}\n{proc.stderr}"
    )
    # A green exit with zero tests run would be a silently empty suite.
    assert "# pass " in proc.stdout or "pass " in proc.stdout, proc.stdout
    assert "fail 0" in proc.stdout.replace("# ", ""), proc.stdout
