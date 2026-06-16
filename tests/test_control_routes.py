"""Headless API tests for the control-plane routes (Plan §P3).

FastAPI TestClient against the additive ``/api/{queue,runs}`` routes, backed by a
real QueueManager + RunIndex (tmp dirs) and a light executor double. NEVER
launches mGBA. Asserts STRUCTURE/invariants — enqueue order, official-forces-
frozen, stop verdict, continue reuses model + savepoint — not tuned numbers.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from src.app.executor import RunExecutor
from src.app.queue_manager import QueueManager
from src.app.run_index import RunIndex
from src.dashboard import server


class FakeSupervisor:
    def __init__(self) -> None:
        self._busy = False
        self.handle = {}
        self.muted = True

    class _Status:
        def __init__(self, busy):
            self.busy = busy

    def status(self):
        return self._Status(self._busy)

    def set_busy(self, busy):
        self._busy = bool(busy)

    def set_mute(self, mute):
        self.muted = bool(mute)
        return self.muted


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


def test_batch_enqueue_all_or_nothing(client):
    tc = client["tc"]
    alias = _some_alias()
    # One bad model in the batch → whole batch rejected, queue untouched.
    bad = tc.post(
        "/api/queue/batch",
        json={"items": [{"kind": "casual", "model": alias}, {"kind": "casual", "model": "nope-not-real"}]},
    )
    assert bad.status_code == 400
    assert tc.get("/api/queue").json()["items"] == []

    # All-good batch → every spec enqueued, in order.
    ok = tc.post(
        "/api/queue/batch",
        json={"items": [
            {"kind": "casual", "model": alias, "max_turns": 1},
            {"kind": "casual", "model": alias, "max_turns": 2},
            {"kind": "casual", "model": alias, "max_turns": 3},
        ]},
    )
    assert ok.status_code == 201
    assert len(ok.json()["items"]) == 3
    items = tc.get("/api/queue").json()["items"]
    assert [it["max_turns"] for it in items] == [1, 2, 3]


def test_batch_empty_rejected(client):
    tc = client["tc"]
    assert tc.post("/api/queue/batch", json={"items": []}).status_code == 400


def test_reorder_permutation(client):
    tc = client["tc"]
    alias = _some_alias()
    ids = [
        tc.post("/api/queue", json={"kind": "casual", "model": alias, "max_turns": n}).json()["queue_id"]
        for n in (1, 2, 3)
    ]
    # Reverse the order.
    r = tc.post("/api/queue/reorder", json={"order": list(reversed(ids))})
    assert r.status_code == 200
    got = [it["queue_id"] for it in tc.get("/api/queue").json()["items"]]
    assert got == list(reversed(ids))


def test_reorder_rejects_non_permutation(client):
    tc = client["tc"]
    alias = _some_alias()
    a = tc.post("/api/queue", json={"kind": "casual", "model": alias}).json()["queue_id"]
    tc.post("/api/queue", json={"kind": "casual", "model": alias})
    # Missing one id → 400, queue untouched (still 2 items).
    assert tc.post("/api/queue/reorder", json={"order": [a]}).status_code == 400
    assert len(tc.get("/api/queue").json()["items"]) == 2
    # Unknown id → 400.
    assert tc.post("/api/queue/reorder", json={"order": [a, "q_bogus"]}).status_code == 400


def test_delete_run_trashes_and_deindexes(client, tmp_path, monkeypatch):
    tc = client["tc"]
    runs_root = client["runs_root"]
    index = client["index"]
    # Redirect "~/.Trash" to a tmp dir so the test never touches the real Trash.
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr("pathlib.Path.home", staticmethod(lambda: fake_home))

    run_id = "2026-06-16_del_config-3.13__claude"
    run_dir = runs_root / run_id
    run_dir.mkdir(parents=True)
    with open(run_dir / "run_summary.json", "w") as f:
        json.dump({"session": {"llm_alias": "claude"}}, f)
    index.rebuild_from_scan()
    assert index.get(run_id) is not None

    r = tc.delete(f"/api/runs/{run_id}")
    assert r.status_code == 200
    assert not run_dir.exists()                       # folder moved out of runs_root
    assert (fake_home / ".Trash" / run_id).exists()   # ...into the Trash
    assert index.get(run_id) is None                  # de-indexed


def test_delete_run_404_when_unknown(client):
    tc = client["tc"]
    assert tc.delete("/api/runs/does-not-exist").status_code == 404


def test_emulator_mute_toggles_and_status_reflects(client):
    tc = client["tc"]
    # Default is muted (emulator boots with -C mute=1).
    assert tc.get("/api/emulator/status").json()["muted"] is True
    # Unmute.
    r = tc.post("/api/emulator/mute", json={"mute": False})
    assert r.status_code == 200 and r.json()["muted"] is False
    assert tc.get("/api/emulator/status").json()["muted"] is False
    # Mute again (default body → mute).
    r = tc.post("/api/emulator/mute", json={})
    assert r.status_code == 200 and r.json()["muted"] is True
    assert tc.get("/api/emulator/status").json()["muted"] is True


def test_routes_503_when_unconfigured():
    # No configure_control_plane → control routes report unavailable.
    server._CONTROL["queue"] = None
    server._CONTROL["executor"] = None
    server._CONTROL["index"] = None
    tc = TestClient(server.app)
    assert tc.get("/api/queue").status_code == 503
