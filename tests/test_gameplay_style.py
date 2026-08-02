"""Tests for the casual `gameplay` playstyle — exploration vs speed.

Before this, WHICH steering block an agent got was welded to `kind`: official
runs were handed ``benchmark_guidelines`` (shortest path to the top goal),
casual runs ``freeplay_guidelines`` (wander, catch, roleplay). There was no way
to time a model on a casual run, or to watch a config just play. `gameplay`
unwelds them for casual runs only — official always races.

The mechanism is deliberately thin: `gameplay` picks the run ``mode`` string the
executor already stamps, so both consumers (``agent._player_mode_guidelines``
for the Player, ``task_master._mode_guidelines`` for the TaskMaster) keep
switching on exactly the values they always did.

Layers, mirroring test_stop_at_event.py / test_max_spend_budget.py:
  * the mapping — including that an absent/garbage value still plays
  * the executor — the right mode reaches the config, on fresh AND continue
  * the API — accepted, rejected, and dropped for official
  * the selector — the mode actually swaps the injected text
"""

from __future__ import annotations

import pytest

from src.agent.agent import _player_mode_guidelines
from src.app.executor import RunExecutor

# --- the mapping --------------------------------------------------------------


def test_exploration_maps_to_freeplay():
    assert RunExecutor._mode_for_gameplay("exploration") == "freeplay"


def test_speed_maps_to_benchmark():
    assert RunExecutor._mode_for_gameplay("speed") == "benchmark"


def test_absent_playstyle_is_exploration():
    """The back-compat rung. Every casual run enqueued before this field
    existed has `gameplay: None`, and must keep playing exactly as it did."""
    assert RunExecutor._mode_for_gameplay(None) == "freeplay"
    assert RunExecutor.DEFAULT_GAMEPLAY == "exploration"


def test_the_two_styles_do_not_map_to_the_same_mode():
    """Mutation control. If both rungs collapsed onto one mode the tests above
    would still pass individually while the feature did nothing."""
    assert (
        RunExecutor._mode_for_gameplay("exploration")
        != RunExecutor._mode_for_gameplay("speed")
    )


def test_an_unknown_playstyle_plays_rather_than_raising():
    """A hand-edited queue.json must not wedge the serial drain. The API is
    where a typo is rejected (see the 400 test below); by the time an item is
    being dispatched, refusing to run it costs more than playing it."""
    assert RunExecutor._mode_for_gameplay("blitz") == "freeplay"


def test_case_is_not_load_bearing():
    assert RunExecutor._mode_for_gameplay("SPEED") == "benchmark"


# --- the executor -------------------------------------------------------------


def _stamped(gameplay):
    cfg: dict = {"task_master": {"enabled": True}}
    RunExecutor._stamp_mode(cfg, RunExecutor._mode_for_gameplay(gameplay))
    return cfg


def test_speed_stamps_both_sinks():
    """Two agents read the mode from two places; a run that only steered one of
    them would have the Player racing while the TaskMaster wandered."""
    cfg = _stamped("speed")
    assert cfg["mode"] == "benchmark"
    assert cfg["task_master"]["mode"] == "benchmark"


def test_exploration_stamps_both_sinks():
    cfg = _stamped("exploration")
    assert cfg["mode"] == "freeplay"
    assert cfg["task_master"]["mode"] == "freeplay"


def test_no_task_master_block_is_not_conjured():
    """A 4.0-style config has no TaskMaster. Stamping must not invent one —
    that reads as 'TaskMaster is configured' downstream."""
    cfg: dict = {}
    RunExecutor._stamp_mode(cfg, RunExecutor._mode_for_gameplay("speed"))
    assert cfg["mode"] == "benchmark"
    assert "task_master" not in cfg


def test_queued_run_defaults_to_no_playstyle():
    from src.app.models import QueuedRun, RunKind

    item = QueuedRun(
        queue_id="q_test", kind=RunKind.casual, model="claude-haiku-4.5(medium)",
        enqueued_at="2026-08-01T00:00:00Z",
    )
    assert item.gameplay is None


def test_queue_manager_round_trips_the_playstyle(tmp_path):
    from src.app.models import RunKind
    from src.app.queue_manager import QueueManager

    q = QueueManager(tmp_path / "queue.json")
    q.enqueue(RunKind.casual, "claude-haiku-4.5(medium)", gameplay="speed")
    # Reloaded from disk, not the in-memory item — the drain reads the file.
    assert QueueManager(tmp_path / "queue.json").items[0].gameplay == "speed"


# --- the API ------------------------------------------------------------------


@pytest.fixture
def api(tmp_path):
    import types

    from fastapi.testclient import TestClient

    from src.app.queue_manager import QueueManager
    from src.dashboard import server

    server.configure_control_plane(
        queue_manager=QueueManager(tmp_path / "queue.json"),
        executor=types.SimpleNamespace(runs_root=tmp_path / "runs"),
        run_index=types.SimpleNamespace(all=lambda: [], get=lambda rid: None),
    )
    yield TestClient(server.app)
    server._CONTROL["queue"] = None
    server._CONTROL["executor"] = None


def test_enqueue_accepts_speed(api):
    r = api.post("/api/queue", json={
        "kind": "casual", "model": "claude-haiku-4.5(medium)", "gameplay": "speed",
    })
    assert r.status_code == 201
    assert r.json()["gameplay"] == "speed"


def test_enqueue_rejects_an_unknown_playstyle(api):
    """Rejected, not defaulted. A typo that silently fell back to exploration
    would give you a run that reads right in the queue and plays the other
    way — findable only by reading the agent's prompt."""
    r = api.post("/api/queue", json={
        "kind": "casual", "model": "claude-haiku-4.5(medium)", "gameplay": "blitz",
    })
    assert r.status_code == 400
    assert "blitz" in r.json()["detail"]


def test_enqueue_without_a_playstyle_is_unchanged(api):
    r = api.post("/api/queue", json={"kind": "casual", "model": "claude-haiku-4.5(medium)"})
    assert r.status_code == 201
    assert r.json()["gameplay"] is None


def test_official_ignores_a_playstyle(api):
    """A benchmark always races; an exploring official run would post a
    leaderboard row that isn't comparable to any other."""
    r = api.post("/api/queue", json={
        "kind": "official", "model": "claude-haiku-4.5(medium)", "gameplay": "exploration",
    })
    assert r.status_code == 201
    assert r.json()["gameplay"] is None


def test_a_bad_playstyle_rejects_the_whole_batch(api):
    r = api.post("/api/queue/batch", json={"items": [
        {"kind": "casual", "model": "claude-haiku-4.5(medium)", "gameplay": "speed"},
        {"kind": "casual", "model": "claude-haiku-4.5(medium)", "gameplay": "nope"},
    ]})
    assert r.status_code == 400
    assert api.get("/api/queue").json()["items"] == []   # nothing half-enqueued


# --- the selector -------------------------------------------------------------


GUIDELINES = {
    "freeplay_guidelines": "# Freeplay Mode: TRUE\nexplore and enjoy",
    "benchmark_guidelines": "# Benchmark Mode: TRUE\nshortest path",
}


def test_speed_injects_the_benchmark_block():
    cfg = {**GUIDELINES, "mode": RunExecutor._mode_for_gameplay("speed")}
    assert _player_mode_guidelines(cfg) == GUIDELINES["benchmark_guidelines"]


def test_exploration_injects_the_freeplay_block():
    cfg = {**GUIDELINES, "mode": RunExecutor._mode_for_gameplay("exploration")}
    assert _player_mode_guidelines(cfg) == GUIDELINES["freeplay_guidelines"]


def test_the_injected_text_actually_differs():
    """The end-to-end mutation control: the whole point is that the agent reads
    something different. Two modes that resolved to the same string would pass
    every mapping test above and change nothing about how the model plays."""
    speed = _player_mode_guidelines({**GUIDELINES, "mode": "benchmark"})
    explore = _player_mode_guidelines({**GUIDELINES, "mode": "freeplay"})
    assert speed != explore
    assert speed and explore          # neither is the empty fallback


def test_a_config_without_guidelines_collapses_to_empty():
    """config-3.13 authors these under `task_master:`; a config that has no
    Player-level blocks must yield an empty placeholder, not a KeyError."""
    assert _player_mode_guidelines({"mode": "benchmark"}) == ""
    assert _player_mode_guidelines({"mode": "freeplay"}) == ""


def test_the_shipped_config_carries_both_blocks():
    """Guards the wiring against the config: the feature is a switch between
    two texts, so a config that lost one of them would silently make one
    playstyle a no-op."""
    import yaml

    from src.config import CONFIGS_DIR

    with open(CONFIGS_DIR / "config-4.0.yaml") as f:
        raw = yaml.safe_load(f)
    player = raw.get("player_agent") or raw
    assert player["freeplay_guidelines"].strip()
    assert player["benchmark_guidelines"].strip()
    assert player["freeplay_guidelines"] != player["benchmark_guidelines"]
