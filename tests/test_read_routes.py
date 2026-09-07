"""Headless API tests for the P4 read routes.

FastAPI TestClient against the additive read surface — ``/api/{leaderboard,runs,
runs/{id},models,configs,emulator/status}`` — backed by a real RunIndex (tmp
dirs) seeded with fixture RunSummary objects + a light executor double. NEVER
launches mGBA.

Discipline (memory ``dont-pin-user-tuned-values-in-tests``): assert STRUCTURE —
ordering, inclusion/exclusion, subsets, 404s, no-500 — never a tuned gate/cost
integer.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.app.models import RunKind, RunStatus, RunSummary
from src.dashboard import server


# ───────────────────────────── doubles ─────────────────────────────


class _FakeStatus:
    def __init__(self, process_up, connected, busy):
        self.process_up = process_up
        self.connected = connected
        self.busy = busy


class FakeSupervisor:
    def __init__(self, process_up=True, connected=True, busy=False):
        self._s = _FakeStatus(process_up, connected, busy)

    def status(self):
        return self._s


class FakeExecutor:
    """Just the bits the read routes touch: ``supervisor`` + ``runs_root``."""

    def __init__(self, runs_root: Path, supervisor=None):
        self.runs_root = Path(runs_root)
        self.supervisor = supervisor


class FakeIndex:
    """Minimal RunIndex stand-in: ``all()`` + ``get()`` over seeded entries."""

    def __init__(self, entries: list[RunSummary]):
        self._entries = list(entries)

    def all(self):
        return list(self._entries)

    def get(self, run_id):
        return next((e for e in self._entries if e.run_id == run_id), None)


# ───────────────────────────── fixtures ─────────────────────────────


def _summary(**kw) -> RunSummary:
    base = dict(
        run_id="r",
        kind=RunKind.official,
        model="m",
        status=RunStatus.completed,
        turns=100,
        gates_reached=5,
        total_gates=21,
        # Leaderboard eligibility requires a config-5.x stem since the
        # 2026-09-07 harness flip; these rows exercise ranking and filtering,
        # so they default to an eligible stem. The partition itself is tested
        # in test_app_derivations.py and by the route test below.
        config_stem="config-5.0",
    )
    base.update(kw)
    return RunSummary(**base)


@pytest.fixture
def seeded():
    """A spread of runs exercising leaderboard eligibility + history filters."""
    entries = [
        # official, completed — best for model A (more gates than A's other run)
        _summary(run_id="a_best", model="alpha", status=RunStatus.completed,
                 gates_reached=10, turns=400, started_at="2026-06-10",
                 total_cost_usd=50.0, duration_s=4000.0),
        # official, completed — same model A, fewer gates (loses best-per-model)
        _summary(run_id="a_worse", model="alpha", status=RunStatus.completed,
                 gates_reached=6, turns=200, started_at="2026-06-11",
                 total_cost_usd=20.0, duration_s=2000.0),
        # official, terminated — model B, fewer gates than A's best
        _summary(run_id="b_term", model="beta", status=RunStatus.terminated,
                 gates_reached=7, turns=300, started_at="2026-06-12",
                 total_cost_usd=30.0, duration_s=3000.0),
        # casual — must NOT appear on leaderboard
        _summary(run_id="c_casual", kind=RunKind.casual, model="gamma",
                 status=RunStatus.completed, gates_reached=20, turns=500,
                 started_at="2026-06-13", total_cost_usd=5.0, duration_s=900.0),
        # cancelled official — voided, must NOT appear on leaderboard
        _summary(run_id="d_cancelled", model="delta", status=RunStatus.cancelled,
                 gates_reached=15, turns=600, started_at="2026-06-14",
                 total_cost_usd=99.0, duration_s=6000.0),
    ]
    index = FakeIndex(entries)
    executor = FakeExecutor(runs_root=Path("/tmp/does-not-matter"),
                            supervisor=FakeSupervisor())
    server.configure_control_plane(queue_manager=object(), executor=executor,
                                   run_index=index)
    tc = TestClient(server.app)
    yield {"tc": tc, "index": index, "executor": executor}
    server._CONTROL["queue"] = None
    server._CONTROL["executor"] = None
    server._CONTROL["index"] = None


# ───────────────────────────── leaderboard ─────────────────────────────


def test_leaderboard_official_best_per_model_ordered(seeded):
    rows = seeded["tc"].get("/api/leaderboard").json()
    ids = [r["run_id"] for r in rows]
    # casual + cancelled excluded; best-per-model only (a_best beats a_worse).
    assert "c_casual" not in ids
    assert "d_cancelled" not in ids
    assert "a_worse" not in ids
    assert set(ids) == {"a_best", "b_term"}
    # ordered gates desc (a_best=10 > b_term=7).
    assert ids == ["a_best", "b_term"]


def test_leaderboard_gates_tiebreak_fewer_turns(seeded):
    # Two same-gate winners for different models → fewer-turns ranks higher.
    index = seeded["index"]
    index._entries = [
        _summary(run_id="x", model="x", status=RunStatus.completed,
                 gates_reached=8, turns=500),
        _summary(run_id="y", model="y", status=RunStatus.completed,
                 gates_reached=8, turns=200),
    ]
    rows = seeded["tc"].get("/api/leaderboard").json()
    assert [r["run_id"] for r in rows] == ["y", "x"]  # 200 turns before 500


# ───────────────────────────── benchmarks ─────────────────────────────


def test_benchmarks_endpoint_lists_registry(seeded):
    rows = seeded["tc"].get("/api/benchmarks").json()
    ids = [r["id"] for r in rows]
    assert ids == ["pokebench-easy", "pokebench-first-badge", "pokebench-full"]
    # exactly one default, and it's FIRST BADGE (moved from easy 2026-09-07)
    defaults = [r["id"] for r in rows if r["default"]]
    assert defaults == ["pokebench-first-badge"]
    # each carries its goal + ladder
    assert all(r["goal"] and r["ladder"] for r in rows)


def test_benchmarks_endpoint_carries_the_official_config_stem(seeded):
    """The dialog's "official config" label comes from HERE, not a UI literal.

    It used to print ``config-3.13 (frozen…)`` as hard-coded text — a second
    copy of ``executor.OFFICIAL_CONFIG`` that flipping the constant could not
    reach, so the dialog would have kept advertising 3.13 while dispatch loaded
    5.0. Asserted against the constant, not the string, so the two cannot drift.
    """
    from pathlib import Path as _Path

    from src.app.executor import OFFICIAL_CONFIG

    rows = seeded["tc"].get("/api/benchmarks").json()
    stem = _Path(OFFICIAL_CONFIG).stem
    assert stem == "config-5.0"
    assert rows and all(r["official_config"] == stem for r in rows)


def test_leaderboard_shows_only_config_5_x_runs(seeded):
    """The route inherits the harness partition — legacy officials drop out.

    Both directions on one index, since the point is the partition and not the
    ordering: the config-5.0 row ranks, the config-3.13 row does not, and the
    legacy row is still served by /api/runs (History), which is where the
    decision said it should stay.
    """
    index = seeded["index"]
    index._entries = [
        _summary(run_id="new", model="a", gates_reached=8,
                 config_stem="config-5.0"),
        _summary(run_id="old", model="b", gates_reached=20,
                 config_stem="config-3.13"),
    ]
    board = seeded["tc"].get("/api/leaderboard").json()
    assert [r["run_id"] for r in board] == ["new"]
    history_ids = {r["run_id"] for r in seeded["tc"].get("/api/runs").json()}
    assert history_ids == {"new", "old"}


def test_runs_route_exposes_harness_and_max_turns(seeded):
    """``harness`` + ``max_turns`` reach the browser on every row.

    ``run_summary.json["agent_type"]`` had zero readers; these two fields are
    the first. ``api.js``'s ``toRun`` was already reading ``s.max_turns`` — off
    a row that never carried it.
    """
    index = seeded["index"]
    index._entries = [
        _summary(run_id="ap", model="a", harness="append_compact", max_turns=100),
        _summary(run_id="lg", model="b"),
    ]
    rows = {r["run_id"]: r for r in seeded["tc"].get("/api/runs").json()}
    assert rows["ap"]["harness"] == "append_compact"
    assert rows["ap"]["max_turns"] == 100
    # Default, not absent: a legacy row has to say "current", not omit the key.
    assert rows["lg"]["harness"] == "current"
    assert rows["lg"]["max_turns"] is None
    assert seeded["tc"].get("/api/runs/ap").json()["harness"] == "append_compact"


def test_leaderboard_benchmark_filter(seeded):
    # Each benchmark has its own ranking; the filter scopes to one.
    index = seeded["index"]
    index._entries = [
        _summary(run_id="e1", model="a", benchmark="pokebench-easy",
                 status=RunStatus.completed, gates_reached=18, turns=900),
        _summary(run_id="f1", model="a", benchmark="pokebench-full",
                 status=RunStatus.completed, gates_reached=12, turns=500),
        _summary(run_id="f2", model="b", benchmark="pokebench-full",
                 status=RunStatus.completed, gates_reached=20, turns=1100),
    ]
    easy = seeded["tc"].get("/api/leaderboard", params={"benchmark": "pokebench-easy"}).json()
    assert [r["run_id"] for r in easy] == ["e1"]
    full = seeded["tc"].get("/api/leaderboard", params={"benchmark": "pokebench-full"}).json()
    assert [r["run_id"] for r in full] == ["f2", "f1"]  # 20 gates before 12


def test_enqueue_official_rejects_unknown_benchmark(seeded):
    # The enqueue path validates the benchmark id against the registry.
    # Use a raw "provider/model" id so model-validation passes and the request
    # reaches the benchmark check.
    r = seeded["tc"].post(
        "/api/queue",
        json={"kind": "official", "model": "prov/model", "benchmark": "nope"},
    )
    assert r.status_code == 400
    assert "unknown benchmark" in r.json()["detail"]


# ───────────────────────────── history ─────────────────────────────


def test_history_filter_kind_casual(seeded):
    rows = seeded["tc"].get("/api/runs", params={"kind": "casual"}).json()
    assert {r["run_id"] for r in rows} == {"c_casual"}


def test_history_filter_status_completed(seeded):
    rows = seeded["tc"].get("/api/runs", params={"status": "completed"}).json()
    ids = {r["run_id"] for r in rows}
    assert ids == {"a_best", "a_worse", "c_casual"}  # the completed ones only


def test_history_search_q_by_model(seeded):
    rows = seeded["tc"].get("/api/runs", params={"q": "beta"}).json()
    assert {r["run_id"] for r in rows} == {"b_term"}


def test_history_sort_cost_asc(seeded):
    rows = seeded["tc"].get("/api/runs",
                            params={"sort": "cost", "order": "asc"}).json()
    costs = [r["total_cost_usd"] for r in rows]
    assert costs == sorted(costs)


def test_history_default_recent_desc(seeded):
    rows = seeded["tc"].get("/api/runs").json()
    started = [r["started_at"] for r in rows]
    assert started == sorted(started, reverse=True)


def test_history_invalid_kind_400(seeded):
    assert seeded["tc"].get("/api/runs", params={"kind": "bogus"}).status_code == 400


# ───────────────────────────── one run ─────────────────────────────


def test_run_get_returns_entry(seeded):
    r = seeded["tc"].get("/api/runs/a_best")
    assert r.status_code == 200
    assert r.json()["run_id"] == "a_best"


def test_run_get_unknown_404(seeded):
    assert seeded["tc"].get("/api/runs/nope").status_code == 404


# ───────────────────────────── models / configs ─────────────────────────────


def test_models_reflect_registry(seeded):
    """The route serves the live registry, one row per model + a level axis.

    Re-pointed 2026-09-07: this used to pin ``gemini-3-flash`` and its exact
    ladder ``[high, medium, low, minimal]``, so pruning that model out of the
    registry failed the test for a reason that had nothing to do with the route.
    The membership + ladder claims are now made against the registry itself, so
    they still assert the SAME relationship (route agrees with models.yaml,
    default level is the highest) without naming a model that can be retired.
    """
    from src.config import _load_models_registry

    registry = {
        name: entry
        for name, entry in _load_models_registry().items()
        if isinstance(entry, dict) and not entry.get("retired")
    }
    rows = seeded["tc"].get("/api/models").json()
    assert isinstance(rows, list) and rows
    # Collapsed shape: one row per non-retired model, with a thinking-level axis.
    models = {r["model"] for r in rows}
    assert models == set(registry)
    for r in rows:
        assert {"model", "openrouter_id", "reasoning_type", "default_level",
                "levels", "observed"} <= set(r)
        if r["observed"] is not None:
            assert "avg_turn_cost_usd" in r["observed"]
            assert "avg_turn_latency_s" in r["observed"]
    # Every effort-tiered row carries its registry ladder verbatim, highest
    # first, and its default level IS that highest one.
    effort = [r for r in rows if r["reasoning_type"] == "effort"]
    assert effort, "registry has no effort-tiered model — retarget this test"
    for r in effort:
        levels = registry[r["model"]]["thinking_levels"]
        assert [lv["level"] for lv in r["levels"]] == levels
        assert r["default_level"] == levels[0]


def test_models_tolerate_missing_observed(seeded):
    # Real registry has entries whose observed block lacks the numeric fields
    # (sample_turns: 0) — those must serialize as observed=None, not crash.
    rows = seeded["tc"].get("/api/models").json()
    assert any(r["observed"] is None for r in rows) or all(
        r["observed"] is None or "avg_turn_cost_usd" in r["observed"] for r in rows
    )


def test_configs_includes_known_stem(seeded):
    rows = seeded["tc"].get("/api/configs").json()
    assert "config-3.13" in rows


# ───────────────────────────── emulator status ─────────────────────────────


def test_emulator_status_with_supervisor(seeded):
    r = seeded["tc"].get("/api/emulator/status")
    assert r.status_code == 200
    body = r.json()
    assert body["process_up"] is True
    assert body["connected"] is True
    assert body["busy"] is False
    assert body["configured"] is True


def test_emulator_status_unconfigured_no_500():
    # No control plane → idle configured:false payload, NOT 500/503.
    server._CONTROL["queue"] = None
    server._CONTROL["executor"] = None
    server._CONTROL["index"] = None
    tc = TestClient(server.app)
    r = tc.get("/api/emulator/status")
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is False
    assert body["process_up"] is False
    assert body["busy"] is False


# ───────────── retired models: resolvable by name, not offerable ─────────────


def test_retired_models_are_hidden_from_the_picker_but_still_resolve(seeded, monkeypatch):
    """`retired: true` is a CATALOG state, not a deletion.

    A model reaches it when you would no longer start a new run with it, while a
    frozen config, a saved run config or the app's boot placeholder may still
    name it. So the two readers must diverge: ``/api/models`` (what may I pick
    NOW) drops it, and ``load_config``/``resolve_model_selection`` (what does
    this name mean) keeps resolving it. Filtering it in the registry LOADER
    instead would make an old run's model unresolvable, which is the failure
    retirement exists to prevent.

    Both directions on one fake registry, so a filter that dropped everything —
    or nothing — fails.
    """
    from src.app import catalog
    from src.config import is_valid_model_selection, resolve_model_selection

    fake = {
        "live-model": {
            "openrouter_id": "vendor/live",
            "reasoning_type": "effort",
            "thinking_levels": ["high", "low"],
        },
        "retired-model": {
            "openrouter_id": "vendor/retired",
            "reasoning_type": "effort",
            "thinking_levels": ["high"],
            "retired": True,
        },
    }
    monkeypatch.setattr(catalog, "_load_models_registry", lambda: fake)

    served = {row["model"] for row in seeded["tc"].get("/api/models").json()}
    assert "live-model" in served
    assert "retired-model" not in served

    # ...and the retired name still means what it meant, straight from the
    # registry helpers the loader uses (unpatched — they read the real file).
    assert resolve_model_selection("retired-model(high)", fake)["openrouter_id"] == (
        "vendor/retired"
    )
    assert is_valid_model_selection("retired-model(high)", fake)


def test_the_app_boot_placeholder_is_a_live_selection(seeded):
    """`pokemon app` must not be bootable-only-by-luck.

    ``_build_supervisor_config`` needs SOME alias (``prepare_config`` binds the
    registry entry) even though the supervisor never runs an agent. It used to
    be a literal, and the whole control center stopped booting the day that
    model left models.yaml. Asserted as "resolves AND is offerable", so a future
    prune or retirement cannot break the boot silently.
    """
    from src.app.catalog import list_models
    from src.cli.app import _build_supervisor_config, _placeholder_alias
    from src.config import _load_models_registry, is_valid_model_selection

    alias = _placeholder_alias()
    assert is_valid_model_selection(alias, _load_models_registry())
    base = alias.split("(")[0]
    assert base in {row["model"] for row in list_models()}, (
        f"boot placeholder {alias!r} names a retired/absent model"
    )
    # And the real boot path constructs — no emulator needed, config only.
    cfg = _build_supervisor_config()
    assert cfg["_config_path"].endswith("config-5.0.yaml")
    assert cfg["emulator"]["rom_path"]
