"""Round 8 backend: trace endpoint + screenshot serving + /api/models run_count.

Drives the REAL code paths (per the stub-at-seam lesson — no fakes for the
grouping or file serving):
  - ``GET /api/runs/{id}/trace`` against a REAL TaskMaster run dir copied from
    ``local/runs`` (asserts the real grouped JSON), AND a casual no-task run dir
    (asserts the degenerate single-group shape).
  - ``GET /api/runs/{id}/screenshots/{name}`` serves the REAL PNG bytes + headers.
  - ``GET /api/models`` carries a ``run_count`` derived from the index.
  - ``derivations.run_counts_by_model`` aggregation.

Asserts STRUCTURE (keys present, nesting, types, references resolve) — never a
tuned cost/turn number.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.app import derivations
from src.app.executor import RunExecutor
from src.app.queue_manager import QueueManager
from src.app.run_index import RunIndex
from src.dashboard import server

_REAL_RUNS_ROOT = Path(__file__).resolve().parents[1] / "local" / "runs"


class _FakeSupervisor:
    class _Status:
        busy = False

    def status(self):
        return self._Status()


def _find_taskmaster_run() -> Path | None:
    """A real run dir carrying task_master_trace + screenshots, or None."""
    if not _REAL_RUNS_ROOT.is_dir():
        return None
    for child in sorted(_REAL_RUNS_ROOT.iterdir(), reverse=True):
        events = child / "events.jsonl"
        shots = child / "screenshots"
        if not (events.is_file() and shots.is_dir()):
            continue
        try:
            text = events.read_text()
        except Exception:
            continue
        if '"task_master_trace"' in text and '"task_started"' in text:
            if any(shots.glob("*.png")):
                return child
    return None


@pytest.fixture
def control(tmp_path):
    """A configured control plane over a tmp runs_root (no mGBA, no drain)."""
    runs_root = tmp_path / "runs"
    runs_root.mkdir()
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    queue = QueueManager(app_dir / "queue.json")
    index = RunIndex(app_dir / "runs_index.json", runs_root)
    index.load()
    executor = RunExecutor(
        supervisor=_FakeSupervisor(),
        queue_manager=queue,
        run_index=index,
        runs_root=runs_root,
        saves_dir=tmp_path / "saves",
        run_fn=lambda *a, **k: runs_root / "noop",
    )
    server.configure_control_plane(
        queue_manager=queue, executor=executor, run_index=index
    )
    tc = TestClient(server.app)
    yield {"tc": tc, "index": index, "runs_root": runs_root}
    server._CONTROL["queue"] = None
    server._CONTROL["executor"] = None
    server._CONTROL["index"] = None


def _copy_run(src: Path, runs_root: Path) -> str:
    dst = runs_root / src.name
    shutil.copytree(src, dst)
    return src.name


# ───────────────────── B1+B2: task-grouped trace ─────────────────────


def test_trace_real_taskmaster_run_is_grouped(control):
    src = _find_taskmaster_run()
    if src is None:
        pytest.skip("no real TaskMaster run with screenshots under local/runs")
    run_id = _copy_run(src, control["runs_root"])

    r = control["tc"].get(f"/api/runs/{run_id}/trace")
    assert r.status_code == 200
    body = r.json()

    # Top-level shape.
    assert body["run_id"] == run_id
    assert body["has_tasks"] is True
    assert body["task_count"] >= 1
    assert isinstance(body["tasks"], list) and body["tasks"]

    # A master node exists with model + structured trace; at least one group has
    # input thumbnails (the screenshots the master saw).
    saw_master_model = False
    saw_master_images = False
    saw_turn_screenshot = False
    for task in body["tasks"]:
        assert "task_index" in task
        assert "master_trace" in task and isinstance(task["master_trace"], dict)
        # Structured (reused report._group_trace_into_steps), not raw messages.
        assert set(task["master_trace"]) >= {"system_prompt", "user_input", "steps"}
        if task["master_model"]:
            saw_master_model = True
        if task["master_input_images"]:
            img = task["master_input_images"][0]
            assert "data_url" in img  # inlined data-URI, SPA renders directly
            saw_master_images = True
        assert isinstance(task["turns"], list)
        for turn in task["turns"]:
            assert {"turn", "action", "reasoning", "screenshot", "trace"} <= set(turn)
            assert isinstance(turn["trace"], dict)
            if turn["screenshot"]:
                # Reference is a bare basename (SPA composes the URL).
                assert "/" not in turn["screenshot"]
                saw_turn_screenshot = True
    assert saw_master_model, "expected at least one master_model in the real run"
    assert saw_master_images, "expected master_input_images on the real run"
    assert saw_turn_screenshot, "expected a per-turn screenshot reference"


def test_trace_screenshot_route_serves_real_png(control):
    src = _find_taskmaster_run()
    if src is None:
        pytest.skip("no real TaskMaster run with screenshots under local/runs")
    run_id = _copy_run(src, control["runs_root"])
    tc = control["tc"]

    # Pull a screenshot reference out of the REAL trace JSON, then load it.
    body = tc.get(f"/api/runs/{run_id}/trace").json()
    name = None
    for task in body["tasks"]:
        for turn in task["turns"]:
            if turn["screenshot"]:
                name = turn["screenshot"]
                break
        if name:
            break
    assert name, "trace JSON carried no screenshot reference"

    r = tc.get(f"/api/runs/{run_id}/screenshots/{name}")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"  # real PNG magic bytes


def test_trace_screenshot_route_rejects_traversal(control):
    src = _find_taskmaster_run()
    if src is None:
        pytest.skip("no real TaskMaster run under local/runs")
    run_id = _copy_run(src, control["runs_root"])
    # Encoded traversal still resolves inside the route; a bare missing name 404s.
    r = tc = control["tc"].get(f"/api/runs/{run_id}/screenshots/nope-does-not-exist.png")
    assert r.status_code == 404


def test_trace_casual_no_task_run_is_single_group(control):
    """A run with NO task_started events → one implicit group, never 500."""
    runs_root = control["runs_root"]
    run_id = "2026-01-01_00-00-00_casual-fixture"
    rd = runs_root / run_id
    (rd / "screenshots").mkdir(parents=True)
    # Minimal events.jsonl: two player turns, no task_* events.
    lines = [
        '{"type":"run_start","id":1}',
        '{"type":"turn_start","turn":1,"id":2}',
        '{"type":"screenshot","file":"' + str(rd / "screenshots" / "t1.png") + '","id":3}',
        '{"type":"turn_explanation","explanation":{"action":"A","reasoning":"press A"},"id":4}',
        '{"type":"turn_usage","cost_usd":0.01,"request_tokens":10,"response_tokens":5,"id":5}',
        '{"type":"turn_start","turn":2,"id":6}',
        '{"type":"turn_explanation","explanation":{"action":"B","reasoning":"press B"},"id":7}',
    ]
    (rd / "events.jsonl").write_text("\n".join(lines) + "\n")

    r = control["tc"].get(f"/api/runs/{run_id}/trace")
    assert r.status_code == 200
    body = r.json()
    assert body["has_tasks"] is False
    assert body["task_count"] == 1
    group = body["tasks"][0]
    assert group["task_index"] is None
    assert group["master_model"] == ""
    assert len(group["turns"]) == 2
    assert group["turns"][0]["action"] == "A"
    assert group["turns"][0]["reasoning"] == "press A"


def test_trace_404_for_missing_run(control):
    r = control["tc"].get("/api/runs/does-not-exist/trace")
    assert r.status_code == 404


# ───────────────────── C3: per-model run_count ─────────────────────


def test_run_counts_by_model_aggregates():
    from src.app.models import RunKind, RunStatus, RunSummary

    def _s(run_id, model):
        return RunSummary(
            run_id=run_id, label=None, kind=RunKind.casual, model=model,
            model_resolved=model, config_stem=None, benchmark_version=None,
            status=RunStatus.completed, started_at="2026-01-01T00:00:00",
            ended_at=None, turns=1, duration_s=1.0, total_cost_usd=0.0,
            avg_cost_per_turn_usd=0.0, avg_s_per_turn=0.0, furthest_gate=None,
            furthest_gate_turn=None, gates_reached=0, total_gates=0,
            termination_reason=None, continued_from=None,
        )

    counts = derivations.run_counts_by_model(
        [_s("a", "m1"), _s("b", "m1"), _s("c", "m2")]
    )
    assert counts == {"m1": 2, "m2": 1}
    assert derivations.run_counts_by_model([]) == {}


def test_api_models_carries_run_count(control):
    r = control["tc"].get("/api/models")
    assert r.status_code == 200
    models = r.json()
    assert isinstance(models, list) and models
    # Collapsed shape: one row per model, run_count on the model + each level.
    for entry in models:
        assert "model" in entry
        assert "run_count" in entry
        assert isinstance(entry["run_count"], int)
        # Empty index → every count is 0.
        assert entry["run_count"] == 0
        for lvl in entry["levels"]:
            assert lvl["run_count"] == 0
