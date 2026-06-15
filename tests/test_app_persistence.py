"""Tests for the control-center persistence layer (Plan §P1).

Discipline: assert STRUCTURE / invariants / relationships, never the tuned gate
counts or deadlines (the ladder is WIP). We DO assert ``total_gates`` equals the
ladder's node length read at runtime — a structural relationship, not a literal.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.app.models import QueuedRun, RunKind, RunStatus, RunSummary
from src.app.projection import project_run_dir
from src.app.queue_manager import QueueManager
from src.app.run_index import RunIndex
from src.referee.checkpoints import load_ladder

# The default ladder the projection falls back to. Read its node count at runtime
# so the assertion tracks the WIP ladder instead of pinning an integer.
_DEFAULT_LADDER = Path("configs/checkpoints-firered-v1.yaml")
_DEFAULT_NODES = len(load_ladder(_DEFAULT_LADDER).nodes)


# --- fixture builders ---------------------------------------------------------

def _write_run(
    root: Path,
    name: str,
    *,
    session_extra: dict | None = None,
    cost_total: float = 1.0,
    turns: int = 10,
    referee: dict | None = None,
    top_level: dict | None = None,
    ladder_in_config: str | None = None,
) -> Path:
    """Write a minimal run folder with a nested run_summary.json."""
    run_dir = root / name
    run_dir.mkdir(parents=True)
    session = {
        "llm_alias": "test-model(medium)",
        "llm_model": "vendor/test-model",
        "thinking": {"effort": "medium"},
        "fallback_models": [],
        "task": "Beat Brock",
        "total_turns": turns,
        "duration_seconds": 100.0,
        "started_at": "2026-06-10T12:00:00",
    }
    if session_extra:
        session.update(session_extra)
    summary: dict = {
        "session": session,
        "cost": {"total_usd": cost_total, "per_turn": []},
        "turns": [],
    }
    if referee is not None:
        summary["referee"] = referee
    if top_level:
        summary.update(top_level)
    (run_dir / "run_summary.json").write_text(json.dumps(summary))

    if ladder_in_config is not None:
        (run_dir / "config.json").write_text(
            json.dumps({"referee": {"checkpoints": ladder_in_config, "enforce": True}})
        )
    return run_dir


def _referee_block(*, n_gates: int, n_cleared: int, furthest_idx: int | None,
                   furthest_turn: int | None = 5, termination: str | None = None) -> dict:
    """Build a scorecard-shaped referee block with `n_cleared` done gates."""
    gates = []
    for i in range(n_gates):
        status = "done" if i < n_cleared else "pending"
        gates.append({
            "kind": "single",
            "id": f"gate_{i}",
            "name": f"Gate {i}",
            "type": "map",
            "deadline_turn": 100 + i,
            "turn": furthest_turn if i < n_cleared else None,
            "status": status,
        })
    furthest = f"gate_{furthest_idx}" if furthest_idx is not None else None
    return {
        "checkpoints": {},
        "gates": gates,
        "furthest": furthest,
        "first_unmet": None,
        "autofilled": [],
        "termination_reason": termination,
    }


@pytest.fixture
def runs_root(tmp_path: Path) -> Path:
    """Five-fixture runs_root: official-completed, official-terminated, casual,
    continued, and a legacy run with no referee + no new fields."""
    root = tmp_path / "runs"
    root.mkdir()

    # 1. official, completed, 8 gates cleared of N
    _write_run(
        root, "2026-06-10_10-00-00_pokebench-v1__model-a",
        top_level={"kind": "official", "benchmark_version": "pokebench-v1",
                   "status": "completed"},
        referee=_referee_block(n_gates=_DEFAULT_NODES, n_cleared=8, furthest_idx=7),
        turns=120,
    )
    # 2. official, terminated, more gates cleared (best by gates)
    _write_run(
        root, "2026-06-10_11-00-00_pokebench-v1__model-b",
        top_level={"kind": "official", "benchmark_version": "pokebench-v1",
                   "status": "terminated",
                   "termination_reason": "missed_gate:gate_11"},
        referee=_referee_block(n_gates=_DEFAULT_NODES, n_cleared=11, furthest_idx=10,
                               termination="missed_gate:gate_11"),
        turns=300,
    )
    # 3. casual run — never on the leaderboard
    _write_run(
        root, "2026-06-10_12-00-00_config-3.13__model-c",
        top_level={"kind": "casual", "status": "completed"},
        referee=_referee_block(n_gates=_DEFAULT_NODES, n_cleared=14, furthest_idx=13),
        turns=200,
    )
    # 4. continued (casual) run
    _write_run(
        root, "2026-06-10_13-00-00_config-3.13__model-a",
        top_level={"kind": "casual", "status": "completed",
                   "continued_from": "2026-06-10_10-00-00_pokebench-v1__model-a"},
        turns=50,
    )
    # 5. legacy: no referee, no top-level new fields, no _llm_alias
    _write_run(
        root, "2026-04-09_15-14-58_phase5_test",
        session_extra={"llm_alias": None},
        turns=30,
    )
    return root


# --- projection ---------------------------------------------------------------

def test_project_run_dir_missing_summary_returns_none(tmp_path: Path):
    empty = tmp_path / "empty_run"
    empty.mkdir()
    assert project_run_dir(empty) is None


def test_project_legacy_run_is_defensive(runs_root: Path):
    """A run without referee / new fields still projects to a valid entry."""
    legacy = project_run_dir(runs_root / "2026-04-09_15-14-58_phase5_test")
    assert legacy is not None
    # No referee → casual (no benchmark_version), completed (no termination).
    assert legacy.kind == RunKind.casual
    assert legacy.status == RunStatus.completed
    assert legacy.gates_reached == 0
    assert legacy.furthest_gate is None
    # total_gates still populated from the fallback ladder (structural).
    assert legacy.total_gates == _DEFAULT_NODES
    # run_id inferred from the dir name; config_stem parsed from it.
    assert legacy.run_id == "2026-04-09_15-14-58_phase5_test"
    assert legacy.config_stem == "phase5_test"
    # model_resolved present even when alias is null.
    assert legacy.model == "vendor/test-model"


def test_project_official_run_fields_and_averages(runs_root: Path):
    official = project_run_dir(runs_root / "2026-06-10_11-00-00_pokebench-v1__model-b")
    assert official is not None
    assert official.kind == RunKind.official
    assert official.status == RunStatus.terminated
    assert official.benchmark_version == "pokebench-v1"
    assert official.termination_reason == "missed_gate:gate_11"
    # gates_reached counts done/auto (mirrors report.py "cleared").
    assert official.gates_reached == 11
    assert official.total_gates == _DEFAULT_NODES
    assert official.gates_reached <= official.total_gates
    assert official.furthest_gate == "gate_10"
    assert official.furthest_gate_turn == 5
    # averages = total / turns, guarded.
    assert official.avg_cost_per_turn_usd == pytest.approx(
        official.total_cost_usd / official.turns
    )
    assert official.avg_s_per_turn == pytest.approx(
        official.duration_s / official.turns
    )


def test_project_total_gates_uses_run_ladder(tmp_path: Path):
    """total_gates reads the ladder recorded in config.json, not a hardcoded int."""
    root = tmp_path / "runs"
    root.mkdir()
    failtest = Path("configs/checkpoints-firered-failtest.yaml")
    if not failtest.exists():
        pytest.skip("failtest ladder not present")
    n_nodes = len(load_ladder(failtest).nodes)
    _write_run(
        root, "2026-06-12_00-00-00_smoke__model-x",
        top_level={"kind": "official", "benchmark_version": "v",
                   "status": "completed"},
        referee=_referee_block(n_gates=n_nodes, n_cleared=2, furthest_idx=1),
        ladder_in_config=str(failtest),
    )
    proj = project_run_dir(root / "2026-06-12_00-00-00_smoke__model-x")
    assert proj is not None
    # total_gates tracks the recorded ladder, which differs from the default.
    assert proj.total_gates == n_nodes


def test_zero_turns_guarded(tmp_path: Path):
    root = tmp_path / "runs"
    root.mkdir()
    _write_run(root, "2026-06-10_00-00-00_x__m", turns=0)
    proj = project_run_dir(root / "2026-06-10_00-00-00_x__m")
    assert proj is not None
    assert proj.avg_cost_per_turn_usd == 0.0
    assert proj.avg_s_per_turn == 0.0


# --- RunIndex -----------------------------------------------------------------

def test_rebuild_from_scan_one_entry_per_run(tmp_path: Path, runs_root: Path):
    idx = RunIndex(tmp_path / "runs_index.json", runs_root)
    entries = idx.rebuild_from_scan()
    assert len(entries) == 5
    assert {e.run_id for e in idx.all()} == {p.name for p in runs_root.iterdir()}


def test_rebuild_is_idempotent_after_corruption(tmp_path: Path, runs_root: Path):
    index_path = tmp_path / "runs_index.json"
    idx = RunIndex(index_path, runs_root)
    first = {e.run_id for e in idx.rebuild_from_scan()}
    # Corrupt then delete the index — scan must reproduce the same set.
    index_path.write_text("}{ not json")
    idx2 = RunIndex(index_path, runs_root)
    idx2.load()  # tolerates corrupt file
    assert idx2.all() == []
    second = {e.run_id for e in idx2.rebuild_from_scan()}
    assert first == second
    index_path.unlink()
    idx3 = RunIndex(index_path, runs_root)
    third = {e.run_id for e in idx3.rebuild_from_scan()}
    assert first == third


def test_index_save_load_round_trip(tmp_path: Path, runs_root: Path):
    index_path = tmp_path / "runs_index.json"
    idx = RunIndex(index_path, runs_root)
    rebuilt = idx.rebuild_from_scan()
    reloaded = RunIndex(index_path, runs_root)
    loaded = reloaded.load()
    assert [e.model_dump() for e in loaded] == [e.model_dump() for e in rebuilt]


def test_index_upsert_replaces_by_run_id(tmp_path: Path):
    idx = RunIndex(tmp_path / "i.json", tmp_path / "runs")
    s1 = RunSummary(run_id="r1", kind=RunKind.casual, model="m",
                    status=RunStatus.completed)
    idx.upsert(s1)
    assert idx.get("r1").turns == 0
    s1b = RunSummary(run_id="r1", kind=RunKind.casual, model="m",
                     status=RunStatus.completed, turns=99)
    idx.upsert(s1b)
    assert len(idx.all()) == 1  # replaced, not appended
    assert idx.get("r1").turns == 99
    idx.upsert(RunSummary(run_id="r2", kind=RunKind.casual, model="m",
                          status=RunStatus.completed))
    assert len(idx.all()) == 2


# --- QueueManager -------------------------------------------------------------

def test_enqueue_cancel_move_order(tmp_path: Path):
    qm = QueueManager(tmp_path / "queue.json")
    a = qm.enqueue(RunKind.casual, "m", enqueued_at="t1")
    b = qm.enqueue(RunKind.casual, "m", enqueued_at="t2")
    c = qm.enqueue(RunKind.official, "m", enqueued_at="t3")
    assert [i.queue_id for i in qm.items] == [a.queue_id, b.queue_id, c.queue_id]
    # cancel the middle
    assert qm.cancel(b.queue_id) is True
    assert [i.queue_id for i in qm.items] == [a.queue_id, c.queue_id]
    # move c to the front
    qm.move(c.queue_id, 0)
    assert [i.queue_id for i in qm.items] == [c.queue_id, a.queue_id]


def test_enqueued_at_overridable(tmp_path: Path):
    qm = QueueManager(tmp_path / "queue.json")
    item = qm.enqueue(RunKind.casual, "m", enqueued_at="fixed-clock")
    assert item.enqueued_at == "fixed-clock"
    assert item.queue_id.startswith("q_")


def test_single_active_invariant(tmp_path: Path):
    qm = QueueManager(tmp_path / "queue.json")
    a = qm.enqueue(RunKind.casual, "m", enqueued_at="t1")
    b = qm.enqueue(RunKind.casual, "m", enqueued_at="t2")
    qm.set_active(a.queue_id)
    assert qm.active == a.queue_id
    # switching active replaces — never two active at once (scalar field).
    qm.set_active(b.queue_id)
    assert qm.active == b.queue_id
    # activating an unknown id is rejected.
    with pytest.raises(AssertionError):
        qm.set_active("q_unknown")
    qm.set_active(None)
    assert qm.active is None


def test_cancel_active_clears_active(tmp_path: Path):
    qm = QueueManager(tmp_path / "queue.json")
    a = qm.enqueue(RunKind.casual, "m", enqueued_at="t1")
    qm.set_active(a.queue_id)
    qm.cancel(a.queue_id)
    assert qm.active is None


def test_peek_next_skips_active(tmp_path: Path):
    qm = QueueManager(tmp_path / "queue.json")
    a = qm.enqueue(RunKind.casual, "m", enqueued_at="t1")
    b = qm.enqueue(RunKind.casual, "m", enqueued_at="t2")
    assert qm.peek_next().queue_id == a.queue_id
    qm.set_active(a.queue_id)
    assert qm.peek_next().queue_id == b.queue_id


def test_queue_save_load_round_trip(tmp_path: Path):
    path = tmp_path / "queue.json"
    qm = QueueManager(path)
    a = qm.enqueue(RunKind.casual, "m", config="c", max_turns=1500,
                   enqueued_at="t1")
    qm.set_active(a.queue_id)
    reloaded = QueueManager(path)
    assert reloaded.active == a.queue_id
    assert len(reloaded.items) == 1
    assert reloaded.items[0].config == "c"
    assert reloaded.items[0].max_turns == 1500


def test_queued_run_model_is_pydantic():
    item = QueuedRun(queue_id="q_1", kind=RunKind.casual, model="m",
                     enqueued_at="t")
    assert item.config is None
    assert item.continue_from is None
