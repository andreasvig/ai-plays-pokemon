"""Headless boot tests for the control center (Plan §P7: boot polish).

Covers the additive boot behaviours wired in P7 — backfill-on-boot of the run
index, queue persistence across a restart, and the legacy (unconfigured)
``GET /api/runs`` path that keeps ``pokemon run`` working (closes the P4 test
gap). NEVER launches mGBA or a live ``pokemon app``.

Discipline (MEMORY): assert STRUCTURE / invariants (counts, shape, no-clobber,
round-trip survival), not tuned numbers.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from src.app.queue_manager import QueueManager
from src.app.run_index import RunIndex
from src.cli.app import _backfill_index_on_boot
from src.dashboard import server


# --- fixture builder ---------------------------------------------------------

def _write_min_run(root: Path, name: str) -> Path:
    """Write a minimal run folder with a nested run_summary.json (projectable)."""
    run_dir = root / name
    run_dir.mkdir(parents=True)
    summary = {
        "session": {
            "llm_alias": "test-model(medium)",
            "llm_model": "vendor/test-model",
            "thinking": {"effort": "medium"},
            "fallback_models": [],
            "task": "Beat Brock",
            "total_turns": 10,
            "duration_seconds": 100.0,
            "started_at": "2026-06-10T12:00:00",
        },
        "cost": {"total_usd": 1.0, "per_turn": []},
        "turns": [],
        "kind": "casual",
        "status": "completed",
    }
    (run_dir / "run_summary.json").write_text(json.dumps(summary))
    return run_dir


# --- backfill-on-boot --------------------------------------------------------

def test_backfill_populates_empty_index_from_scan(tmp_path: Path):
    """Index missing + real run folders present → boot backfills to the count."""
    runs_root = tmp_path / "runs"
    runs_root.mkdir()
    for i in range(3):
        _write_min_run(runs_root, f"2026-06-10_1{i}-00-00_config__model-{i}")

    index_path = tmp_path / "app" / "runs_index.json"
    assert not index_path.exists()  # missing index — the real first-boot case

    run_index = RunIndex(index_path, runs_root)
    run_index.load()  # mirrors boot order: load (empty) then backfill
    assert run_index.all() == []

    n = _backfill_index_on_boot(run_index)
    assert n == 3
    assert len(run_index.all()) == 3
    # The index file was materialised by rebuild_from_scan's save().
    assert index_path.exists()
    assert {e.run_id for e in run_index.all()} == {
        p.name for p in runs_root.iterdir()
    }


def test_backfill_does_not_clobber_existing_index(tmp_path: Path):
    """Non-empty index on disk → boot loads it and does NOT rebuild from scan."""
    runs_root = tmp_path / "runs"
    runs_root.mkdir()
    # runs_root has MANY folders, but the persisted index has only ONE entry —
    # if backfill wrongly fired, the count would jump to the scan count.
    for i in range(4):
        _write_min_run(runs_root, f"2026-06-10_1{i}-00-00_config__model-{i}")

    index_path = tmp_path / "app" / "runs_index.json"
    # Seed a persisted index with a single, hand-picked entry.
    seed = RunIndex(index_path, runs_root)
    only = next(p for p in sorted(runs_root.iterdir()))
    seed.rebuild_from_scan()
    kept = seed.get(only.name)
    assert kept is not None
    # Overwrite the file so it holds exactly one entry (the kept one).
    index_path.write_text(json.dumps([kept.model_dump(mode="json")]))

    run_index = RunIndex(index_path, runs_root)
    loaded = run_index.load()
    assert len(loaded) == 1  # the persisted index, not the scan

    n = _backfill_index_on_boot(run_index)
    assert n == 0  # no-op: non-empty index is preserved
    assert len(run_index.all()) == 1
    assert run_index.all()[0].run_id == only.name


# --- queue persistence across restart ----------------------------------------

def test_queue_survives_restart(tmp_path: Path):
    """Items written to queue.json by one QueueManager survive a fresh one."""
    path = tmp_path / "queue.json"
    qm = QueueManager(path)
    a = qm.enqueue("casual", "model-a", enqueued_at="2026-06-10T00:00:00+00:00")
    b = qm.enqueue("official", "model-b", enqueued_at="2026-06-10T00:01:00+00:00")
    qm.set_active(a.queue_id)

    # Simulate a process restart: brand-new manager on the same path.
    restarted = QueueManager(path)
    assert [it.queue_id for it in restarted.items] == [a.queue_id, b.queue_id]
    assert [it.model for it in restarted.items] == ["model-a", "model-b"]
    assert restarted.active == a.queue_id


# --- legacy unconfigured /api/runs (closes the P4 gap) -----------------------

def _clear_control() -> None:
    server._CONTROL["queue"] = None
    server._CONTROL["executor"] = None
    server._CONTROL["index"] = None


class _NoopStreamer:
    """Minimal streamer stub: unregister() calls .stop() on the session."""

    def stop(self) -> None:
        pass


def test_api_runs_unconfigured_returns_legacy_listing():
    """Control plane NOT configured (the ``pokemon run`` path) → /api/runs 200s
    with the legacy live-registered listing shape, not 500 and not the history
    projection shape."""
    _clear_control()
    sess = server.RunSession(
        run_id="boot-legacy-run",
        label="Boot Legacy Run",
        config={"task": {"goal": "Beat Brock"}, "_llm_alias": "test-model(medium)"},
        bridge=None,
        streamer=_NoopStreamer(),
        state_manager=None,
        run_dir=Path("/tmp/nonexistent"),
    )
    server._REGISTRY.register(sess)
    try:
        assert server._CONTROL["index"] is None  # unconfigured precondition
        tc = TestClient(server.app)
        resp = tc.get("/api/runs")
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        row = next(r for r in body if r["run_id"] == "boot-legacy-run")
        # Legacy shape: the live-registry fields + a /runs/{id} URL — NOT the
        # flat RunSummary history projection (which has kind/status/cost).
        assert set(row.keys()) == {"run_id", "label", "url"}
        assert row["label"] == "Boot Legacy Run"
        assert row["url"] == "/runs/boot-legacy-run"
        assert "kind" not in row and "status" not in row
    finally:
        server._REGISTRY.unregister("boot-legacy-run")
        _clear_control()
