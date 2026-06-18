"""P3 — continuing an OFFICIAL run stays official on the SAME benchmark.

The core of the resumable-benchmark feature: a run stopped overnight (or that
died) must be continuable and still finish AS that benchmark — enforced ladder,
benchmark goal, leaderboard-eligible — rather than silently downgrading to casual.
A casual source still continues casual (no regression).

Drives the two pure seams: build_continue_spec (what kind/benchmark a continue
inherits) and build_run_config (the dispatch wiring) — no emulator/network.
"""

import json
from pathlib import Path

import pytest

from src.app.benchmarks import get_benchmark
from src.app.executor import RunExecutor
from src.app.models import RunKind
from src.app.queue_manager import QueueManager
from src.app.run_index import RunIndex


class _Supervisor:
    def status(self):
        class _S:
            busy = False
        return _S()


def _write_source(runs_root: Path, run_id: str, *, kind: str, benchmark, turn=30):
    d = runs_root / run_id
    (d / "savepoints" / f"turn_{turn}").mkdir(parents=True)
    summary = {
        "kind": kind,
        "session": {"llm_alias": "gemini-x", "llm_model": "resolved/gemini-x"},
        "cost": {},
    }
    if benchmark is not None:
        summary["benchmark"] = benchmark
    (d / "run_summary.json").write_text(json.dumps(summary))
    return d


def _executor(tmp_path):
    runs_root = tmp_path / "runs"
    runs_root.mkdir()
    app = tmp_path / "app"
    app.mkdir()
    index = RunIndex(app / "idx.json", runs_root)
    index.load()
    # Fake continue_fn: stand in for runner.continue_from_run (no config.json /
    # dotenv reads). Returns a bare cfg + the resolved savepoint dir.
    def fake_continue(src):
        return ({"task": {}, "task_master": {}}, Path(src) / "savepoints" / "turn_30")
    ex = RunExecutor(
        supervisor=_Supervisor(),
        queue_manager=QueueManager(app / "q.json"),
        run_index=index,
        runs_root=runs_root,
        saves_dir=tmp_path / "saves",
        run_fn=lambda *a, **k: None,
        prepare_config_fn=lambda path, model, tm_model_alias=None: {"task": {}},
        continue_fn=fake_continue,
    )
    return ex, runs_root


def test_official_continue_spec_inherits_kind_and_benchmark(tmp_path):
    ex, runs_root = _executor(tmp_path)
    _write_source(runs_root, "src_official", kind="official", benchmark="pokebench-full")

    spec = ex.build_continue_spec("src_official")

    assert spec["kind"] == RunKind.official
    assert spec["benchmark"] == "pokebench-full"
    assert spec["continue_from"] == "src_official"


def test_cancelled_official_segment_still_continuable_as_benchmark(tmp_path):
    # A run stopped overnight is voided (no benchmark_version) but keeps its id, so
    # the continue still resumes the same benchmark.
    ex, runs_root = _executor(tmp_path)
    _write_source(runs_root, "src_stopped", kind="official", benchmark="pokebench-first-badge")
    spec = ex.build_continue_spec("src_stopped")
    assert spec["kind"] == RunKind.official
    assert spec["benchmark"] == "pokebench-first-badge"


def test_official_continue_dispatch_reapplies_enforced_ladder(tmp_path):
    ex, runs_root = _executor(tmp_path)
    _write_source(runs_root, "src_official", kind="official", benchmark="pokebench-full")
    spec = ex.build_continue_spec("src_official")
    item = ex.queue.enqueue(
        kind=spec["kind"], model=spec["model"],
        benchmark=spec.get("benchmark"), continue_from=spec["continue_from"],
    )

    cfg, snapshot, turns = ex.build_run_config(item)

    full = get_benchmark("pokebench-full")
    assert cfg["referee"]["enforce"] is True
    assert cfg["referee"]["checkpoints"] == full.ladder
    assert cfg["task"]["goal"] == full.goal
    assert cfg["task_master"]["mode"] == "benchmark"
    assert turns == ex._OFFICIAL_TURN_SENTINEL          # no max-turns; gates bound it
    assert snapshot.endswith("savepoints/turn_30")     # resumes from the savepoint


def test_official_continue_refuses_tampered_seal(tmp_path):
    # P5: a present-but-mismatched checkpoint seal is tamper evidence — the
    # official continue refuses to resume it (raises; the drain then skips it).
    ex, runs_root = _executor(tmp_path)
    src = _write_source(runs_root, "src_tampered", kind="official", benchmark="pokebench-full")
    (src / "savepoints" / "turn_30" / "checkpoint.sha256").write_text("deadbeef")

    spec = ex.build_continue_spec("src_tampered")
    item = ex.queue.enqueue(
        kind=spec["kind"], model=spec["model"],
        benchmark=spec.get("benchmark"), continue_from=spec["continue_from"],
    )
    with pytest.raises(ValueError, match="seal mismatch"):
        ex.build_run_config(item)


def test_casual_continue_stays_freeplay_no_ladder(tmp_path):
    ex, runs_root = _executor(tmp_path)
    _write_source(runs_root, "src_casual", kind="casual", benchmark=None)

    spec = ex.build_continue_spec("src_casual")
    assert spec["kind"] == RunKind.casual
    assert "benchmark" not in spec

    item = ex.queue.enqueue(kind=spec["kind"], model=spec["model"],
                            continue_from=spec["continue_from"], max_turns=200)
    cfg, _snapshot, turns = ex.build_run_config(item)
    assert "referee" not in cfg                        # casual is never enforced
    assert cfg["task_master"]["mode"] == "freeplay"
    assert turns == 200
