"""Tests for `_maybe_open_report` — the report.html auto-open gate (D9.1).

Drives the REAL `_maybe_open_report` with `subprocess.run` monkeypatched and
`sys.platform` forced, verifying the fix WITH its trigger (open_report=True still
fires for standalone `pokemon run`) AND WITHOUT it (open_report=False suppresses
it for the app/executor path, which uses the SPA). One case shows half the story.
"""

from __future__ import annotations

from pathlib import Path

from src.cli import runner


def _patch(monkeypatch, platform):
    """Force the platform and record subprocess.run calls; return the call list."""
    calls: list[tuple] = []
    monkeypatch.setattr(runner.sys, "platform", platform)
    monkeypatch.setattr(
        runner.subprocess, "run", lambda *a, **k: calls.append((a, k))
    )
    return calls


def test_open_report_true_fires_on_darwin(monkeypatch):
    calls = _patch(monkeypatch, "darwin")
    path = Path("/tmp/run/report.html")

    fired = runner._maybe_open_report(path, open_report=True)

    assert fired is True
    assert len(calls) == 1
    (args, _kwargs) = calls[0]
    assert args[0] == ["open", str(path)]


def test_open_report_false_suppresses_open(monkeypatch):
    calls = _patch(monkeypatch, "darwin")
    path = Path("/tmp/run/report.html")

    fired = runner._maybe_open_report(path, open_report=False)

    assert fired is False
    assert calls == []


def test_open_report_true_no_op_off_darwin(monkeypatch):
    calls = _patch(monkeypatch, "linux")
    path = Path("/tmp/run/report.html")

    fired = runner._maybe_open_report(path, open_report=True)

    assert fired is False
    assert calls == []
