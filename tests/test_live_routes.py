"""The LIVE surface: the event bridge and the per-run live routes.

Covers the append-harness live-view work from
``artifacts/append-standard-frontend-review/plan.md``:

  #2/#23  what the event bridge puts on the wire (and what it deliberately does not)
  #4      the gate HUD keys on the ladder, not on ``kind``
  #7      the Home active-run card gets a REAL current turn
  #14     the simple view has a compaction phase
  #15     the memory panel can say when memory is first written
  #16     the spend ceiling reaches the live view; budget_exhausted renders
  #20     one Memory box per compaction

No mGBA, no model calls: a RunSession is registered by hand (the same probe
``test_self_directed_config.py`` uses) and the control plane gets doubles.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.dashboard.event_bridge import LIVE_EXCLUDED_TYPES, EventBridge
from src.dashboard.server import RunSession, app, get_registry

WEB = Path(__file__).resolve().parent.parent / "src/dashboard/web/src"


# ───────────────────────────── the event bridge ─────────────────────────────


def test_bridge_keeps_the_whole_taxonomy_on_the_wire():
    bridge = EventBridge()
    kept = [
        {"type": "turn_start", "turn": 1},
        {"type": "llm_thinking", "turn": 1, "content": "x"},
        {"type": "llm_request_usage", "turn": 1, "phase": "gameplay"},
        {"type": "compaction_start", "turn": 21, "after_turn": 20},
        {"type": "compaction_thinking", "turn": 21, "content": "y"},
        {"type": "compaction_complete", "turn": 21, "handover": {}},
        {"type": "llm_output", "turn": 1, "args": {}},
        {"type": "memory_update_output", "turn": 21, "content": "{}"},
        {"type": "endpoint_warning", "endpoint": "e", "message": "m"},
        {"type": "budget_exhausted", "turn": 5, "spent_usd": 1, "max_spend_usd": 1},
        {"type": "screen_settled", "turn": 1, "duration": 1},
    ]
    for event in kept:
        bridge.on_event(event)
    events, cursor = bridge.get_events_since(0)
    assert [e["type"] for e in events] == [e["type"] for e in kept]
    assert cursor == len(kept)


def test_bridge_drops_the_report_only_whole_conversation_traces():
    """#23 — turn_trace/compaction_trace are O(segment) each and unread live."""
    bridge = EventBridge()
    big = [{"role": "user", "content": "x" * 1000}] * 20
    bridge.on_event({"type": "turn_start", "turn": 1})
    bridge.on_event({"type": "turn_trace", "turn": 1, "messages": big})
    bridge.on_event({"type": "compaction_trace", "turn": 21, "messages": big})
    bridge.on_event({"type": "llm_output", "turn": 1, "args": {}})
    events, _ = bridge.get_events_since(0)
    assert [e["type"] for e in events] == ["turn_start", "llm_output"]
    assert LIVE_EXCLUDED_TYPES == {"turn_trace", "compaction_trace"}


def test_bridge_still_counts_stats_for_a_dropped_event_type():
    """The filter is about the wire, not about accounting."""
    bridge = EventBridge()
    bridge.on_event({"type": "turn_trace", "turn": 3, "messages": []})
    bridge.on_event({"type": "turn_usage", "cost_usd": 0.5, "request_tokens": 10, "response_tokens": 2})
    stats = bridge.get_stats()
    assert stats["cost"] == pytest.approx(0.5)
    assert stats["input_tokens"] == 10


def test_bridge_turn_count_does_not_move_during_a_compaction():
    """The Turn stat is stamped by turn_start and by nothing else."""
    bridge = EventBridge()
    bridge.on_event({"type": "turn_start", "turn": 21})
    before = bridge.get_stats()["turns"]
    for event in (
        {"type": "compaction_start", "turn": 21, "after_turn": 20},
        {"type": "compaction_thinking", "turn": 21, "content": "x"},
        {"type": "llm_request_usage", "turn": 21, "phase": "compaction"},
        {"type": "compaction_complete", "turn": 21, "handover": {}},
    ):
        bridge.on_event(event)
    assert bridge.get_stats()["turns"] == before == 21


def test_bridge_reports_the_prior_cost_baseline_for_a_continue():
    """#16 — `cost` is the LINEAGE total; a spend cap bounds THIS segment."""
    bridge = EventBridge()
    assert bridge.get_stats()["prior_cost_usd"] == 0.0
    bridge.seed_stats(cost=4.25, turns=100, prior_duration_s=900.0)
    bridge.on_event({"type": "turn_usage", "cost_usd": 0.75})
    stats = bridge.get_stats()
    assert stats["cost"] == pytest.approx(5.0)
    assert stats["prior_cost_usd"] == pytest.approx(4.25)
    # segment spend, which is what the ceiling is measured against
    assert stats["cost"] - stats["prior_cost_usd"] == pytest.approx(0.75)


# ───────────────────────────── /runs/{id}/api/config ─────────────────────────


@pytest.fixture
def probe():
    """Register a live RunSession by hand; unregister whatever the test made."""
    made: list[str] = []

    def register(run_id: str, config: dict, bridge: EventBridge | None = None):
        from src.dashboard.screen_stream import ScreenStreamer

        get_registry().register(
            RunSession(
                run_id=run_id,
                label=run_id,
                config=config,
                bridge=bridge or EventBridge(),
                streamer=ScreenStreamer(stream_path="/tmp/_test_live_routes_stream.png"),
                state_manager=None,
                run_dir=Path("/tmp") / run_id,
            )
        )
        made.append(run_id)

    yield register
    for run_id in made:
        get_registry().unregister(run_id)


def test_config_route_exposes_the_spend_ceiling_and_compaction_interval(probe):
    probe(
        "live-append",
        {
            "task": {"goal": "beat the game"},
            "max_spend_usd": 2.5,
            "player_agent": {
                "agent_type": "append_compact",
                "compaction": {"every_n_turns": 20},
            },
        },
    )
    payload = TestClient(app).get("/runs/live-append/api/config").json()
    assert payload["max_spend_usd"] == pytest.approx(2.5)
    assert payload["compaction"] == {"every_n_turns": 20}


def test_config_route_omits_both_when_the_run_has_neither(probe):
    """A legacy / unbounded run's payload keeps its exact prior shape."""
    probe("live-legacy", {"task": {"goal": "x"}, "task_master": {"enabled": True}})
    payload = TestClient(app).get("/runs/live-legacy/api/config").json()
    assert "max_spend_usd" not in payload
    assert "compaction" not in payload
    assert payload["task_master"] is True


def test_config_route_ignores_a_nonsense_ceiling_or_interval(probe):
    probe(
        "live-junk",
        {
            "task": {"goal": "x"},
            "max_spend_usd": "lots",
            "player_agent": {"compaction": {"every_n_turns": 0}},
        },
    )
    payload = TestClient(app).get("/runs/live-junk/api/config").json()
    assert "max_spend_usd" not in payload
    assert "compaction" not in payload


def test_events_socket_never_ships_a_whole_conversation_trace(probe):
    """End to end: the bridge filter is what the browser actually receives."""
    bridge = EventBridge()
    bridge.on_event({"type": "turn_start", "turn": 1})
    bridge.on_event({"type": "turn_trace", "turn": 1, "messages": [{"role": "user", "content": "x"}]})
    bridge.on_event({"type": "compaction_start", "turn": 21, "after_turn": 20})
    probe("live-ws", {"task": {"goal": "x"}}, bridge)

    with TestClient(app).websocket_connect("/runs/live-ws/ws/events") as ws:
        seen = []
        # stats frame first, then the replayed backlog.
        for _ in range(6):
            msg = json.loads(ws.receive_text())
            if msg["type"] == "event":
                seen.append(msg["data"]["type"])
            if len(seen) == 2:
                break
    assert seen == ["turn_start", "compaction_start"]
    assert "turn_trace" not in seen


# ───────────────────────────── /api/queue active turn ───────────────────────


@pytest.fixture
def control(tmp_path):
    from src.app.executor import RunExecutor
    from src.app.queue_manager import QueueManager
    from src.app.run_index import RunIndex
    from src.dashboard import server

    class FakeSupervisor:
        class _Status:
            busy = False

        muted = True

        def status(self):
            return self._Status()

    runs_root = tmp_path / "runs"
    runs_root.mkdir()
    queue = QueueManager(tmp_path / "queue.json")
    index = RunIndex(tmp_path / "runs_index.json", runs_root)
    index.load()
    executor = RunExecutor(
        supervisor=FakeSupervisor(),
        queue_manager=queue,
        run_index=index,
        runs_root=runs_root,
        saves_dir=tmp_path / "saves",
        run_fn=lambda *a, **k: runs_root / "noop",
    )
    server.configure_control_plane(
        queue_manager=queue, executor=executor, run_index=index
    )
    yield {"tc": TestClient(server.app), "queue": queue, "executor": executor}
    server._CONTROL["queue"] = None
    server._CONTROL["executor"] = None
    server._CONTROL["index"] = None


def _alias() -> str:
    from src.config import _load_models_registry

    return sorted(_load_models_registry())[0]


def test_queue_payload_is_unchanged_while_nothing_runs(control):
    """No active run → the exact prior shape, no fabricated turn."""
    body = control["tc"].get("/api/queue").json()
    assert body == {"active": None, "items": [], "last_error": None}


def test_queue_payload_carries_the_live_turn_of_the_active_run(control, probe):
    """#7 — the Home active card had no source for a turn at all."""
    tc = control["tc"]
    item = tc.post("/api/queue", json={"kind": "casual", "model": _alias()}).json()
    control["queue"].set_active(item["queue_id"])

    bridge = EventBridge()
    bridge.on_event({"type": "turn_start", "turn": 137})
    probe("live-active", {"task": {"goal": "x"}}, bridge)
    control["executor"]._active_run_id = "live-active"

    body = tc.get("/api/queue").json()
    assert body["active"] == item["queue_id"]
    assert body["active_current_turn"] == 137


def test_queue_live_turn_does_not_move_during_a_compaction(control, probe):
    tc = control["tc"]
    item = tc.post("/api/queue", json={"kind": "casual", "model": _alias()}).json()
    control["queue"].set_active(item["queue_id"])
    bridge = EventBridge()
    bridge.on_event({"type": "turn_start", "turn": 21})
    probe("live-compacting", {"task": {"goal": "x"}}, bridge)
    control["executor"]._active_run_id = "live-compacting"

    before = tc.get("/api/queue").json()["active_current_turn"]
    bridge.on_event({"type": "compaction_start", "turn": 21, "after_turn": 20})
    bridge.on_event({"type": "compaction_complete", "turn": 21, "handover": {}})
    assert tc.get("/api/queue").json()["active_current_turn"] == before == 21


def test_queue_omits_the_live_turn_when_no_session_is_registered(control):
    """An active queue item whose run has not registered yet → no claim made."""
    tc = control["tc"]
    item = tc.post("/api/queue", json={"kind": "casual", "model": _alias()}).json()
    control["queue"].set_active(item["queue_id"])
    assert "active_current_turn" not in tc.get("/api/queue").json()


# ───────────────────── the components' side of the contract ─────────────────
# Source-level, like test_self_directed_config's spectate checks: these pin the
# WIRING (which field feeds which panel), while tests/js/live.test.mjs owns the
# behaviour of the logic those components delegate to.


def test_spectate_gate_hud_keys_on_the_ladder_not_on_the_run_kind():
    """#4 — a --stop-at run is casual-only AND has the full ladder."""
    src = (WEB / "components/Spectate.svelte").read_text()
    assert "const showGates = $derived(ladder.length > 0)" in src
    assert "run?.kind !== 'casual'" not in src


def test_spectate_reads_the_cap_and_the_compaction_hint_from_the_config_route():
    src = (WEB / "components/Spectate.svelte").read_text()
    assert "cfg.max_spend_usd" in src          # #16
    assert "memoryHint(cfg)" in src            # #15
    assert "{compactionHint}" in src           # rendered in the memory panel
    assert "{usd(spendCap)}" in src             # rendered as the Cost denominator


def test_trace_feed_labels_the_new_box_kinds_and_the_compaction_row():
    """#2/#21/#22/#16 — and the compaction row itself carries no turn number."""
    src = (WEB / "components/TraceFeed.svelte").read_text()
    for kind in ("diag:", "withheld:", "warning:", "terminal:"):
        assert kind in src, f"missing box label for {kind}"
    assert "Compaction {entry.number}" in src
    assert "after turn {entry.afterTurn}" in src
    # the compaction head must not print a turn number
    assert "Turn {entry.turn}" not in src


def test_simple_view_has_a_compaction_phase():
    """#14 — otherwise a compaction is an indistinguishable dot cycle."""
    src = (WEB / "components/SimpleView.svelte").read_text()
    assert "'compaction_start'" in src
    assert "'compaction_complete'" in src
    assert "phase = 'compacting'" in src
    assert "Compacting memory" in src
    # it must go BACK to thinking about the interrupted turn
    assert "await beginThinking(turn)" in src


def test_queue_cards_render_the_real_turn_and_never_a_fabricated_zero():
    """#7 — `currentTurn ?? 0` claimed turn 0 on every live run."""
    for name in ("QueueBar.svelte", "QueuePanel.svelte"):
        src = (WEB / "components" / name).read_text()
        assert "active.currentTurn ?? '—'" in src, name
        assert "active.currentTurn ?? 0" not in src, name
    api = (WEB / "lib/api.js").read_text()
    assert "active_current_turn" in api
