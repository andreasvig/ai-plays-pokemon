"""Tests for the extracted trace builder + finalize-time cache (Phase B).

Builds a tiny casual (TaskMaster-less) run dir from a couple of ``turn_start``
events, asserts the projection shape, that ``build_and_cache_trace`` round-trips
to ``trace.json``, and that the ``/api/runs/{id}/trace`` endpoint serves the
cache when fresh but REBUILDS when the cache is stale (events newer than cache).

Discipline (memory ``dont-pin-user-tuned-values-in-tests``): assert STRUCTURE —
keys present, turn_count == turns written, rebuild-on-stale — never a tuned value.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.app.trace_build import build_and_cache_trace, build_run_trace
from src.dashboard import server


# ───────────────────────────── doubles / fixtures ─────────────────────────────


class FakeExecutor:
    """Only the bit the trace route touches: ``runs_root``."""

    def __init__(self, runs_root: Path):
        self.runs_root = Path(runs_root)
        self.supervisor = None


class FakeIndex:
    def all(self):
        return []

    def get(self, run_id):
        return None


def _write_events(run_dir: Path, n_turns: int) -> None:
    """A casual run (no ``task_started``): N turns, each a minimal turn_start
    plus an explanation so the projection has an action to format."""
    run_dir.mkdir(parents=True, exist_ok=True)
    lines = []
    for i in range(1, n_turns + 1):
        lines.append({"type": "turn_start", "turn": i, "agent_id": "player"})
        lines.append(
            {
                "type": "turn_explanation",
                "explanation": {"action": "A", "reasoning": f"r{i}"},
            }
        )
    with open(run_dir / "events.jsonl", "w") as f:
        for ev in lines:
            f.write(json.dumps(ev) + "\n")


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    d = tmp_path / "runs" / "casual_run"
    _write_events(d, n_turns=2)
    return d


@pytest.fixture
def client(tmp_path: Path):
    """TestClient with a control plane whose runs_root is tmp_path/runs."""
    runs_root = tmp_path / "runs"
    executor = FakeExecutor(runs_root=runs_root)
    server.configure_control_plane(
        queue_manager=object(), executor=executor, run_index=FakeIndex()
    )
    tc = TestClient(server.app)
    yield tc
    server._CONTROL["queue"] = None
    server._CONTROL["executor"] = None
    server._CONTROL["index"] = None


# ───────────────────────────── B5.1 — projection shape ─────────────────────────────


def test_build_run_trace_shape(run_dir: Path):
    data = build_run_trace(run_dir)
    assert set(["run_id", "has_tasks", "task_count", "turn_count", "tasks"]).issubset(
        data.keys()
    )
    assert data["run_id"] == run_dir.name
    assert data["has_tasks"] is False
    assert data["turn_count"] == 2  # two turn_start events written
    assert isinstance(data["tasks"], list) and data["tasks"]  # non-empty
    # casual run → single implicit group holding both turns
    assert len(data["tasks"][0]["turns"]) == 2


# ───────────────────────────── B5.2 — cache round-trips ─────────────────────────────


def test_build_and_cache_trace_writes_trace_json(run_dir: Path):
    data = build_and_cache_trace(run_dir)
    cache = run_dir / "trace.json"
    assert cache.is_file()
    with open(cache) as f:
        on_disk = json.load(f)
    assert on_disk == data


# ───────────────────────────── B5.3 — endpoint serve-fresh / rebuild-stale ──────────


def test_endpoint_serves_cache_when_fresh(client, run_dir: Path):
    build_and_cache_trace(run_dir)  # cache now newer than events
    r = client.get(f"/api/runs/{run_dir.name}/trace")
    assert r.status_code == 200
    assert r.json()["turn_count"] == 2


def test_system_prompt_kept_verbatim_not_truncated():
    """The Player / TaskMaster system prompt is projected VERBATIM — it used to
    be capped to a 2000-char preview with an ellipsis, but Andreas reads the full
    prompt in the Report, so it must never be truncated (2026-06-17)."""
    from src.app.trace_build import _trace_steps

    long_prompt = "SYSTEM:\n" + ("x" * 5000)  # well past the old 2000-char cap
    grouped = _trace_steps([{"role": "system", "content": long_prompt}])
    assert grouped["system_prompt"] == long_prompt
    assert "…" not in grouped["system_prompt"]


def test_endpoint_rebuilds_when_cache_stale(client, run_dir: Path):
    # Write a BOGUS cache, then make it OLDER than events.jsonl. A fresh cache
    # would be served verbatim (turn_count 999); a stale one must trigger a
    # rebuild → the REAL turn_count (2), proving the mtime guard works.
    cache = run_dir / "trace.json"
    with open(cache, "w") as f:
        json.dump({"turn_count": 999, "run_id": run_dir.name, "tasks": []}, f)

    events = run_dir / "events.jsonl"
    ev_mtime = events.stat().st_mtime
    old = ev_mtime - 100
    os.utime(cache, (old, old))  # cache strictly older than events → stale

    r = client.get(f"/api/runs/{run_dir.name}/trace")
    assert r.status_code == 200
    assert r.json()["turn_count"] == 2  # rebuilt, NOT the 999 sentinel
