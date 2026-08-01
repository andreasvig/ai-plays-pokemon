"""Tests for the casual `--stop-at <event>` early finish line.

A casual run can name a story event from the benchmark's own gate ladder and
stop the moment the referee detects it. Four layers, one file:

* the catalog (`src.app.catalog`) — the event list, its validation, and the
  referee config block both entry points share;
* the referee — `stop_at` latching, and the observe-only property that keeps a
  ladder deadline from killing a casual run on the way to its event;
* the wiring — executor (queue) and the enqueue/continue API;
* the turn loop — the run actually stops, and reports `completed`.

Neither mGBA nor the network is touched: the referee tests reuse the fake
memory image from test_referee_enforce, the loop test the stub harness from
test_taskmaster_loop.
"""

from __future__ import annotations

import pytest

from src.app.catalog import (
    STOP_EVENT_LADDER,
    list_stop_events,
    stop_at_referee_config,
    validate_stop_event,
)
from src.referee.referee import Referee
from tests.test_referee_enforce import (
    FakeEmulator,
    FakeImage,
    FakeLogger,
    build_sb1,
    make_ladder,
)


def make_referee(tmp_path, emu, logger=None, *, enforce=False, stop_at=None):
    return Referee(
        make_ladder(), emu, logger or FakeLogger(), tmp_path,
        enforce=enforce, stop_at=stop_at,
    )


# --- the catalog -------------------------------------------------------------


def test_catalog_is_the_full_ladder_in_order():
    events = list_stop_events()
    ids = [e["id"] for e in events]
    assert ids[0] == "left_bedroom"
    assert "viridian_forest_reached" in ids
    assert all(set(e) == {"id", "name", "type"} for e in events)
    # Named, not just id'd — the picker shows the name.
    forest = next(e for e in events if e["id"] == "viridian_forest_reached")
    assert forest["name"] == "Entered Viridian Forest"


def test_catalog_flattens_multigate_members_individually():
    """A multigate is an any-order SET, so its members are separately
    reachable — and therefore separately stoppable. Offering only the group
    would make "stop at the Cascade Badge" unexpressible."""
    ids = [e["id"] for e in list_stop_events()]
    assert "cascade_badge" in ids
    assert "bills_errand_reached" in ids
    # The synthetic group id is NOT offered: the referee stamps members, not
    # groups, so a group id could never latch.
    assert not any("+" in i for i in ids)


def test_catalog_ladder_is_a_superset_of_every_benchmark():
    """The catalog reads ONE ladder file on the claim that it contains every
    event any benchmark can detect. Adding a benchmark with a gate outside it
    would silently leave that event out of the picker — so assert the claim
    rather than trusting the prefix relationship to hold."""
    from src.app.benchmarks import load_benchmarks
    from src.referee.checkpoints import load_ladder

    catalog = {e["id"] for e in list_stop_events()}
    for bench in load_benchmarks():
        gates = {cp.id for cp in load_ladder(bench.ladder).checkpoints}
        missing = gates - catalog
        assert not missing, f"{bench.id} has gates outside {STOP_EVENT_LADDER}: {missing}"


def test_every_offered_event_is_one_the_referee_accepts(tmp_path):
    """The picker and the referee must agree on the id space. An event offered
    in the dialog that the Referee rejects at construction would take the run
    down at startup — and the catalog projects ids while the referee latches
    them, so nothing but this test ties the two together."""
    from src.referee.checkpoints import load_ladder

    nodes = load_ladder(STOP_EVENT_LADDER).nodes
    for event in list_stop_events():
        Referee(nodes, FakeEmulator(FakeImage()), FakeLogger(), tmp_path,
                stop_at=event["id"])   # must not raise


@pytest.mark.parametrize("unset", [None, ""])
def test_validate_treats_none_and_empty_as_no_stop_event(unset):
    """The dialog's "— none" option posts an empty string; the CLI omits the
    flag entirely. Both mean the same thing and neither may 400."""
    assert validate_stop_event(unset) is None


def test_validate_rejects_an_unknown_event_and_names_the_options():
    with pytest.raises(ValueError) as exc:
        validate_stop_event("viridian_forrest")     # plausible typo
    msg = str(exc.value)
    assert "viridian_forrest" in msg
    assert "viridian_forest_reached" in msg          # the list is in the error


def test_validate_returns_a_known_event_unchanged():
    assert validate_stop_event("pewter_reached") == "pewter_reached"


def test_referee_block_is_observe_only():
    """enforce MUST be False. The full ladder carries turn deadlines; arming
    them would let a pace gate terminate a casual run long before it reached
    the event that was actually asked for."""
    block = stop_at_referee_config("viridian_forest_reached")
    assert block == {
        "checkpoints": STOP_EVENT_LADDER,
        "enforce": False,
        "stop_at": "viridian_forest_reached",
    }


def test_no_stop_event_builds_no_block():
    assert stop_at_referee_config(None) is None
    assert stop_at_referee_config("") is None


# --- the referee --------------------------------------------------------------


def test_stop_at_latches_when_its_gate_is_stamped(tmp_path):
    logger = FakeLogger()
    # Somewhere that satisfies nothing on the ladder.
    emu = FakeEmulator(FakeImage(block=build_sb1(map_group=4, map_num=1)))
    ref = make_referee(tmp_path, emu, logger, stop_at="left_house")

    assert ref.poll(1) is False
    assert ref.should_stop_at() is False
    assert ref.stop_at_reason is None

    # Walk into left_house's map (3,0).
    emu.set_image(FakeImage(block=build_sb1(map_group=3, map_num=0)))
    ref.poll(2)
    assert ref.should_stop_at() is True
    assert ref.stop_at_reason == "stop_at:left_house"


def test_stop_at_ignores_other_gates(tmp_path):
    """Reaching a DIFFERENT event must not end the run — including a later one,
    which the back-fill will stamp on the way past."""
    emu = FakeEmulator(FakeImage(block=build_sb1(map_group=3, map_num=0)))
    ref = make_referee(tmp_path, emu, stop_at="parcel_delivered")
    ref.poll(1)
    assert ref.stamps.get("left_house") == 1     # a gate DID latch
    assert ref.should_stop_at() is False         # just not the requested one


def test_stop_at_fires_on_a_back_filled_gate(tmp_path):
    """The safety net credits an earlier gate the poll never saw directly (the
    agent crossed it inside one button sequence). That credit has to stop the
    run too — otherwise "stop at Route 1" plays on forever because the run was
    only ever *seen* deeper in the ladder."""
    # Stand on starter_chosen's flag, which is ladder-deeper than left_house,
    # so left_house gets back-filled rather than directly detected.
    emu = FakeEmulator(FakeImage(block=build_sb1(map_group=9, map_num=9,
                                                 flags={0x828: True})))
    ref = make_referee(tmp_path, emu, stop_at="left_house")
    ref.poll(3)
    assert "left_house" in ref.autofilled        # credited, never seen
    assert ref.should_stop_at() is True


def test_stop_at_is_orthogonal_to_the_final_rung(tmp_path):
    """A mid-ladder stop is not a ladder completion. The run ends, but nothing
    claims the benchmark was won."""
    emu = FakeEmulator(FakeImage(block=build_sb1(map_group=3, map_num=0)))
    ref = make_referee(tmp_path, emu, stop_at="left_house")
    ref.poll(1)
    assert ref.should_stop_at() is True
    assert ref.should_complete_run() is False
    assert ref.completion_reason is None


def test_unset_stop_at_never_fires(tmp_path):
    """The control: the same ladder, the same stamps, no stop event. This is
    every official run, and it must be unaffected by the feature."""
    emu = FakeEmulator(FakeImage(block=build_sb1(map_group=3, map_num=0)))
    ref = make_referee(tmp_path, emu)
    ref.poll(1)
    assert ref.stamps                             # gates DID stamp
    assert ref.should_stop_at() is False
    assert ref.stop_at_reason is None


def test_unknown_stop_at_raises_at_construction(tmp_path):
    """Fail at the door. The alternative is a run that plays to its turn cap
    and only then reveals it was never going to stop."""
    emu = FakeEmulator(FakeImage())
    with pytest.raises(ValueError) as exc:
        make_referee(tmp_path, emu, stop_at="not_a_gate")
    assert "not_a_gate" in str(exc.value)


def test_observe_only_never_terminates_on_a_missed_deadline(tmp_path):
    """The property that makes attaching the full ladder to a casual run safe.
    left_bedroom's deadline is turn 30 and it is unstamped — under enforcement
    that terminates. With enforce=False the run plays on toward its event."""
    logger = FakeLogger()
    emu = FakeEmulator(FakeImage(block=build_sb1(map_group=4, map_num=1)))
    ref = make_referee(tmp_path, emu, logger, stop_at="left_house")

    assert ref.poll(500) is False                 # far past every deadline
    assert ref.should_terminate() is False
    assert ref.termination_reason is None
    assert not [t for t, _ in logger.events if t == "referee_gate_missed"]


def test_enforcing_referee_still_enforces_with_a_stop_event(tmp_path):
    """The mutation control for the test above: same call, enforce=True. If
    this passed too, the previous test would be proving nothing."""
    emu = FakeEmulator(FakeImage(block=build_sb1(map_group=4, map_num=1)))
    ref = make_referee(tmp_path, emu, enforce=True, stop_at="left_house")
    assert ref.poll(500) is True
    assert ref.termination_reason == "missed_gate:left_bedroom"


# --- executor wiring ----------------------------------------------------------


def _item(**kw):
    from src.app.models import QueuedRun, RunKind

    return QueuedRun(
        queue_id="q_test", kind=kw.pop("kind", RunKind.casual),
        model="claude-haiku-4.5(medium)", enqueued_at="2026-08-01T00:00:00Z", **kw,
    )


def _apply(cfg, stop_at):
    from src.app.executor import RunExecutor

    RunExecutor._apply_stop_at(cfg, stop_at)
    return cfg


def test_executor_stamps_the_block_on_a_casual_run():
    cfg = _apply({}, "viridian_forest_reached")
    assert cfg["referee"]["stop_at"] == "viridian_forest_reached"
    assert cfg["referee"]["enforce"] is False


def test_executor_leaves_an_unstopped_run_without_a_referee_key():
    """Absent, not None: a present `referee: None` would be falsy today, but
    the turn loop's check is `if referee_cfg`, and a future `.get("referee",
    {})` reader would find a None it can't call .get on."""
    cfg = _apply({"llm_model": "x"}, None)
    assert "referee" not in cfg


def test_queued_run_defaults_to_no_stop_event():
    assert _item().stop_at is None


def test_queue_manager_round_trips_the_stop_event(tmp_path):
    from src.app.models import RunKind
    from src.app.queue_manager import QueueManager

    q = QueueManager(tmp_path / "queue.json")
    q.enqueue(RunKind.casual, "claude-haiku-4.5(medium)", stop_at="pewter_reached")
    # Reloaded from disk, not the in-memory item — the drain reads the file.
    assert QueueManager(tmp_path / "queue.json").items[0].stop_at == "pewter_reached"


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
    server._CONTROL["index"] = None


def test_checkpoints_route_serves_the_catalog(api):
    events = api.get("/api/checkpoints").json()
    assert {"id", "name", "type"} == set(events[0])
    assert "viridian_forest_reached" in [e["id"] for e in events]


def test_enqueue_accepts_a_known_event(api):
    r = api.post("/api/queue", json={
        "kind": "casual", "model": "claude-haiku-4.5(medium)",
        "config": "config-3.13", "max_turns": 100,
        "stop_at": "viridian_forest_reached",
    })
    assert r.status_code == 201
    assert r.json()["stop_at"] == "viridian_forest_reached"


def test_enqueue_rejects_an_unknown_event(api):
    r = api.post("/api/queue", json={
        "kind": "casual", "model": "claude-haiku-4.5(medium)", "stop_at": "mt_silver",
    })
    assert r.status_code == 400
    assert "mt_silver" in r.json()["detail"]


def test_enqueue_without_an_event_is_unchanged(api):
    r = api.post("/api/queue", json={"kind": "casual", "model": "claude-haiku-4.5(medium)"})
    assert r.status_code == 201
    assert r.json()["stop_at"] is None


def test_official_ignores_a_stop_event(api):
    """A benchmark ends at its own ladder. Silently honouring a stop event
    would produce a short official run that still looked comparable."""
    r = api.post("/api/queue", json={
        "kind": "official", "model": "claude-haiku-4.5(medium)",
        "stop_at": "viridian_forest_reached",
    })
    assert r.status_code == 201
    assert r.json()["stop_at"] is None


def test_a_bad_event_rejects_the_whole_batch(api):
    r = api.post("/api/queue/batch", json={"items": [
        {"kind": "casual", "model": "claude-haiku-4.5(medium)", "stop_at": "pewter_reached"},
        {"kind": "casual", "model": "claude-haiku-4.5(medium)", "stop_at": "nope"},
    ]})
    assert r.status_code == 400
    assert api.get("/api/queue").json()["items"] == []   # nothing half-enqueued


# --- the turn loop ------------------------------------------------------------


class _StopAtReferee:
    """Referee shaped just enough for the loop: stops at a chosen turn."""

    def __init__(self, at_turn: int):
        self.at_turn = at_turn
        self.polled_turns: list[int] = []
        self._hit = False

    def poll(self, turn: int) -> bool:
        self.polled_turns.append(turn)
        if turn >= self.at_turn:
            self._hit = True
        return False                       # never a missed-gate termination

    @property
    def termination_reason(self):
        return None

    def should_stop_at(self) -> bool:
        return self._hit

    @property
    def stop_at_reason(self):
        return "stop_at:left_house" if self._hit else None

    def should_complete_run(self) -> bool:
        return False

    def scorecard(self) -> dict:
        return {"termination_reason": None, "gates": [], "furthest": None}


def test_loop_stops_at_the_event_and_reports_completed(tmp_path):
    """The composition: the event ends the run BEFORE its turn budget, and the
    summary says `completed` — the run did what it was asked to do."""
    import json

    from tests.test_taskmaster_loop import _base_config, _ga, _make_mgr

    cfg = _base_config(tmp_path, enabled=False)
    # Budget for four turns; the event lands on the second.
    mgr, logger = _make_mgr(cfg, [_ga("t1"), _ga("t2"), _ga("t3"), _ga("t4")], None)
    mgr.referee = _StopAtReferee(at_turn=2)

    mgr.run_loop(max_turns=4)

    summary = json.loads((logger.run_dir / "run_summary.json").read_text())
    assert summary["session"]["player_turns"] == 2      # not 4
    assert summary["status"] == "completed"
    assert mgr.referee.polled_turns == [1, 2]


def test_loop_without_an_event_uses_the_whole_budget(tmp_path):
    """The control. Same harness, a referee that never stops — the run must
    still play its four turns, or the test above proves only that the loop
    breaks somewhere."""
    import json

    from tests.test_taskmaster_loop import _base_config, _ga, _make_mgr

    cfg = _base_config(tmp_path, enabled=False)
    mgr, logger = _make_mgr(cfg, [_ga("t1"), _ga("t2"), _ga("t3"), _ga("t4")], None)
    mgr.referee = _StopAtReferee(at_turn=99)   # never reached

    mgr.run_loop(max_turns=4)

    summary = json.loads((logger.run_dir / "run_summary.json").read_text())
    assert summary["session"]["player_turns"] == 4
