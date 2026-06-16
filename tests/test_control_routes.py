"""Headless API tests for the control-plane routes (Plan §P3).

FastAPI TestClient against the additive ``/api/{queue,runs}`` routes, backed by a
real QueueManager + RunIndex (tmp dirs) and a light executor double. NEVER
launches mGBA. Asserts STRUCTURE/invariants — enqueue order, official-forces-
frozen, stop verdict, continue reuses model + savepoint — not tuned numbers.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.app.executor import RunExecutor
from src.app.models import RunKind
from src.app.queue_manager import QueueManager
from src.app.run_index import RunIndex
from src.dashboard import server


class FakeSupervisor:
    def __init__(self) -> None:
        self._busy = False
        self.handle = {}

    class _Status:
        def __init__(self, busy):
            self.busy = busy

    def status(self):
        return self._Status(self._busy)

    def set_busy(self, busy):
        self._busy = bool(busy)


@pytest.fixture
def client(tmp_path):
    runs_root = tmp_path / "runs"
    runs_root.mkdir()
    app_dir = tmp_path / "app"
    app_dir.mkdir()

    queue = QueueManager(app_dir / "queue.json")
    index = RunIndex(app_dir / "runs_index.json", runs_root)
    index.load()
    executor = RunExecutor(
        supervisor=FakeSupervisor(),
        queue_manager=queue,
        run_index=index,
        runs_root=runs_root,
        saves_dir=tmp_path / "saves",
        run_fn=lambda *a, **k: runs_root / "noop",  # never drained in these tests
    )
    server.configure_control_plane(
        queue_manager=queue, executor=executor, run_index=index
    )
    tc = TestClient(server.app)
    yield {"tc": tc, "queue": queue, "index": index, "runs_root": runs_root}
    # Reset the module-singleton control plane so tests don't leak.
    server._CONTROL["queue"] = None
    server._CONTROL["executor"] = None
    server._CONTROL["index"] = None


# A real models.yaml alias to satisfy validation (chosen at runtime so we never
# pin a specific model — just the first available alias).
def _some_alias() -> str:
    from src.config import _load_models_registry

    registry = _load_models_registry()
    assert registry, "models.yaml empty — cannot exercise model validation"
    return sorted(registry)[0]


# ───────────────────────────── tests ─────────────────────────────


def test_queue_get_empty(client):
    r = client["tc"].get("/api/queue")
    assert r.status_code == 200
    assert r.json() == {"active": None, "items": []}


def test_enqueue_casual_then_get_in_order(client):
    tc = client["tc"]
    alias = _some_alias()
    r1 = tc.post(
        "/api/queue",
        json={"kind": "casual", "model": alias, "config": "configs/config-3.13.yaml", "max_turns": 50},
    )
    r2 = tc.post("/api/queue", json={"kind": "casual", "model": alias, "max_turns": 99})
    assert r1.status_code == 201 and r2.status_code == 201

    items = tc.get("/api/queue").json()["items"]
    assert len(items) == 2
    assert items[0]["max_turns"] == 50
    assert items[1]["max_turns"] == 99
    assert items[0]["kind"] == "casual"


def test_enqueue_official_forces_frozen_ignores_config_and_maxturns(client):
    tc = client["tc"]
    alias = _some_alias()
    # Try to smuggle a config + max_turns into an official enqueue.
    r = tc.post(
        "/api/queue",
        json={"kind": "official", "model": alias, "config": "configs/sneaky.yaml", "max_turns": 5},
    )
    assert r.status_code == 201
    item = r.json()
    assert item["kind"] == "official"
    # The smuggled fields are dropped — official freezes config + has no max-turns.
    assert item["config"] is None
    assert item["max_turns"] is None


def test_enqueue_rejects_unknown_model(client):
    tc = client["tc"]
    r = tc.post("/api/queue", json={"kind": "casual", "model": "definitely-not-a-real-alias"})
    assert r.status_code == 400


def test_enqueue_rejects_bad_kind(client):
    tc = client["tc"]
    r = tc.post("/api/queue", json={"kind": "nonsense", "model": _some_alias()})
    assert r.status_code == 400


def test_cancel_queue_item(client):
    tc = client["tc"]
    item = tc.post("/api/queue", json={"kind": "casual", "model": _some_alias()}).json()
    r = tc.delete(f"/api/queue/{item['queue_id']}")
    assert r.status_code == 200
    assert tc.get("/api/queue").json()["items"] == []
    # Cancelling a gone item 404s.
    assert tc.delete(f"/api/queue/{item['queue_id']}").status_code == 404


def test_move_reorders_queue(client):
    tc = client["tc"]
    alias = _some_alias()
    a = tc.post("/api/queue", json={"kind": "casual", "model": alias, "max_turns": 1}).json()
    tc.post("/api/queue", json={"kind": "casual", "model": alias, "max_turns": 2})
    # Move the first item to the end.
    r = tc.post(f"/api/queue/{a['queue_id']}/move", json={"to_index": 1})
    assert r.status_code == 200
    items = tc.get("/api/queue").json()["items"]
    assert items[-1]["queue_id"] == a["queue_id"]


def test_stop_active_run(client):
    tc = client["tc"]
    r = tc.post("/api/runs/some_run_id/stop")
    assert r.status_code == 200
    body = r.json()
    assert body["stopping"] == "some_run_id"


def test_continue_enqueues_casual_reusing_source_model(client):
    tc = client["tc"]
    runs_root = client["runs_root"]
    index = client["index"]

    source_id = "2026-06-15_src_config-3.12__claude"
    source_dir = runs_root / source_id
    (source_dir / "savepoints" / "turn_88").mkdir(parents=True)
    with open(source_dir / "run_summary.json", "w") as f:
        json.dump({"session": {"llm_alias": "claude", "llm_model": "anthropic/claude"}}, f)
    index.rebuild_from_scan()

    # An injected model in the continue body must be IGNORED (locked #10).
    r = tc.post(f"/api/runs/{source_id}/continue", json={"model": "gpt-smuggled", "max_turns": 30})
    assert r.status_code == 201
    item = r.json()
    assert item["kind"] == "casual"
    assert item["continue_from"] == source_id
    assert item["model"] == "claude"  # reused from source, NOT the request
    assert item["max_turns"] == 30

    # And it landed in the queue.
    items = tc.get("/api/queue").json()["items"]
    assert any(it["continue_from"] == source_id for it in items)


def test_continue_without_savepoint_400(client):
    tc = client["tc"]
    runs_root = client["runs_root"]
    source_id = "2026-06-15_nosave_cfg__m"
    (runs_root / source_id).mkdir(parents=True)
    r = tc.post(f"/api/runs/{source_id}/continue", json={})
    assert r.status_code == 400


def test_routes_503_when_unconfigured():
    # No configure_control_plane → control routes report unavailable.
    server._CONTROL["queue"] = None
    server._CONTROL["executor"] = None
    server._CONTROL["index"] = None
    tc = TestClient(server.app)
    assert tc.get("/api/queue").status_code == 503
