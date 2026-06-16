"""Benchmark registry + the per-benchmark leaderboard/history/projection wiring.

Covers: registry loading + validation (``src/app/benchmarks.py``), the benchmark
filter on ``derivations.leaderboard`` / ``derivations.history``, and the
projection's legacy-official → pokebench-full mapping.
"""

from __future__ import annotations

import json

import pytest

from src.app import derivations
from src.app.benchmarks import (
    default_benchmark,
    get_benchmark,
    load_benchmarks,
)
from src.app.models import RunKind, RunStatus, RunSummary

# ───────────────────────────── registry ─────────────────────────────


def _write_registry(path, body):
    path.write_text(body)
    return path


def test_real_registry_loads_three_benchmarks():
    """The committed configs/benchmarks.yaml has the three expected benchmarks,
    full is the default, and each points at its own ladder file."""
    bs = load_benchmarks()
    ids = [b.id for b in bs]
    assert ids == ["pokebench-easy", "pokebench-first-badge", "pokebench-full"]
    assert default_benchmark().id == "pokebench-full"
    assert {b.ladder for b in bs} == {
        "configs/checkpoints-firered-easy.yaml",
        "configs/checkpoints-firered-firstbadge.yaml",
        "configs/checkpoints-firered-v1.yaml",
    }


def test_get_benchmark_unknown_falls_back_to_default():
    assert get_benchmark(None).id == "pokebench-full"
    assert get_benchmark("does-not-exist").id == "pokebench-full"
    assert get_benchmark("pokebench-easy").id == "pokebench-easy"


def test_registry_rejects_duplicate_ids(tmp_path):
    reg = _write_registry(
        tmp_path / "b.yaml",
        "benchmarks:\n"
        "  - {id: a, name: A, goal: g, ladder: x.yaml}\n"
        "  - {id: a, name: B, goal: g, ladder: y.yaml}\n",
    )
    with pytest.raises(ValueError, match="duplicate benchmark id"):
        load_benchmarks(reg)


def test_registry_rejects_two_defaults(tmp_path):
    reg = _write_registry(
        tmp_path / "b.yaml",
        "benchmarks:\n"
        "  - {id: a, name: A, goal: g, ladder: x.yaml, default: true}\n"
        "  - {id: b, name: B, goal: g, ladder: y.yaml, default: true}\n",
    )
    with pytest.raises(ValueError, match="at most one"):
        load_benchmarks(reg)


def test_registry_rejects_missing_field(tmp_path):
    reg = _write_registry(
        tmp_path / "b.yaml",
        "benchmarks:\n  - {id: a, name: A, ladder: x.yaml}\n",  # no goal
    )
    with pytest.raises(ValueError, match="'goal'"):
        load_benchmarks(reg)


def test_default_benchmark_falls_back_to_first_when_none_flagged(tmp_path):
    reg = _write_registry(
        tmp_path / "b.yaml",
        "benchmarks:\n"
        "  - {id: first, name: F, goal: g, ladder: x.yaml}\n"
        "  - {id: second, name: S, goal: g, ladder: y.yaml}\n",
    )
    assert default_benchmark(reg).id == "first"


# ───────────────────────── leaderboard / history filter ─────────────────────


def _run(run_id, *, benchmark, gates, turns, kind=RunKind.official,
         status=RunStatus.completed, model="m"):
    return RunSummary(
        run_id=run_id,
        kind=kind,
        model=model,
        benchmark=benchmark,
        benchmark_version="pokebench-v1" if kind == RunKind.official else None,
        status=status,
        gates_reached=gates,
        turns=turns,
        total_gates=20,
    )


def test_leaderboard_filters_by_benchmark():
    rows = [
        _run("e1", benchmark="pokebench-easy", gates=18, turns=900, model="a"),
        _run("f1", benchmark="pokebench-full", gates=12, turns=500, model="a"),
        _run("f2", benchmark="pokebench-full", gates=20, turns=1100, model="b"),
    ]
    easy = derivations.leaderboard(rows, benchmark="pokebench-easy")
    assert [r.run_id for r in easy] == ["e1"]

    full = derivations.leaderboard(rows, benchmark="pokebench-full")
    # full: b (20 gates) ranks above a (12 gates)
    assert [r.run_id for r in full] == ["f2", "f1"]

    # No filter → every eligible run competes (legacy behaviour).
    assert len(derivations.leaderboard(rows)) == 2  # best-per-model over all


def test_history_filters_by_benchmark():
    rows = [
        _run("e1", benchmark="pokebench-easy", gates=18, turns=900),
        _run("fb", benchmark="pokebench-first-badge", gates=12, turns=400),
    ]
    out = derivations.history(rows, benchmark="pokebench-first-badge")
    assert [r.run_id for r in out] == ["fb"]


# ───────────────────────── projection legacy mapping ─────────────────────


def test_projection_maps_legacy_official_to_full(tmp_path):
    """A legacy official run (benchmark_version set, NO benchmark id) maps to
    pokebench-full — it was scored on the full ladder."""
    from src.app.projection import project_run_dir

    run_dir = tmp_path / "2026-01-01_legacy__m"
    run_dir.mkdir()
    (run_dir / "run_summary.json").write_text(
        json.dumps(
            {
                "kind": "official",
                "benchmark_version": "pokebench-v1",
                "status": "completed",
                "session": {"llm_alias": "m", "total_turns": 10},
            }
        )
    )
    summary = project_run_dir(run_dir)
    assert summary is not None
    assert summary.kind == RunKind.official
    assert summary.benchmark == "pokebench-full"


def test_projection_reads_explicit_benchmark(tmp_path):
    from src.app.projection import project_run_dir

    run_dir = tmp_path / "2026-01-02_easy__m"
    run_dir.mkdir()
    (run_dir / "run_summary.json").write_text(
        json.dumps(
            {
                "kind": "official",
                "benchmark": "pokebench-easy",
                "benchmark_version": "pokebench-v1",
                "status": "completed",
                "session": {"llm_alias": "m", "total_turns": 10},
            }
        )
    )
    summary = project_run_dir(run_dir)
    assert summary.benchmark == "pokebench-easy"
