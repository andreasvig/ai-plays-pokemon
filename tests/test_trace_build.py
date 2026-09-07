"""Tests for the extracted trace builder + finalize-time cache (Phase B).

Builds a tiny casual (TaskMaster-less) run dir from a couple of ``turn_start``
events, asserts the projection shape, that ``build_and_cache_trace`` round-trips
to ``trace.json``, and that the ``/api/runs/{id}/trace`` endpoint serves the
cache when fresh but REBUILDS when the cache is stale (events newer than cache).

Discipline (memory ``dont-pin-user-tuned-values-in-tests``): assert STRUCTURE —
keys present, turn_count == turns written, rebuild-on-stale — never a tuned value.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.app.trace_build import TRACE_VERSION, build_and_cache_trace, build_run_trace
from src.dashboard import server


# ───────────────────── the projection contract, per TRACE_VERSION ─────────────────────
#
# A built trace is CACHED to ``run_dir/trace.json`` and served straight back for
# as long as its stamp equals ``TRACE_VERSION``, so the bump is the ONLY thing
# that retires the caches on disk. Version 5 shipped twice (implied-cache
# economics, then the split-turn fold) and 13 append runs kept serving the
# projection from before both — a Cache overview reading "No pricing snapshot"
# for runs whose endpoint prices were on disk.
#
# So the key sets are goldened PER VERSION. The test below fails in both
# directions: change the builder's shape without bumping and the sets mismatch;
# bump without recording the new shape and there is no golden to compare
# against. Values are never pinned here — only which keys exist.
TRACE_SHAPES = {
    6: {
        "trace": {
            "trace_version", "run_id", "has_tasks", "task_count", "turn_count",
            "compaction_count", "harness", "referee_enforced",
            "cleared_gate_statuses", "tasks", "cache", "cache_breakdown",
            "segment_costs",
        },
        "task": {
            "task_index", "title", "description", "success_criteria", "rating",
            "player_self_assessment", "player_task_summary", "master_model",
            "master_cost", "master_input_images", "master_trace", "turns",
            "timeline",
        },
        "turn": {
            "kind", "turn", "task_index", "action", "reasoning",
            "last_turn_succeeded", "screenshot", "cost_usd", "request_tokens",
            "response_tokens", "trace", "diagnostics", "fresh",
        },
        "turn_trace": {"system_prompt", "user_input", "steps", "conversation", "segment_context"},
        "compaction": {
            "kind", "number", "after_turn", "complete", "trace", "diagnostics",
            "cost_usd", "request_tokens", "response_tokens",
        },
        "compaction_trace": {
            "system_prompt", "user_input", "steps", "user_messages", "output",
            "previous_memory",
        },
    },
}


# ───────────────────────────── doubles / fixtures ─────────────────────────────


class FakeExecutor:
    """Only the bit the trace route touches: ``runs_root``."""

    def __init__(self, runs_root: Path):
        self.runs_root = Path(runs_root)
        self.supervisor = None


class FakeIndex:
    def all(self):
        return []

    def get(self, run_id):
        return None


def _write_events(run_dir: Path, n_turns: int) -> None:
    """A casual run (no ``task_started``): N turns, each a minimal turn_start
    plus an explanation so the projection has an action to format."""
    run_dir.mkdir(parents=True, exist_ok=True)
    lines = []
    for i in range(1, n_turns + 1):
        lines.append({"type": "turn_start", "turn": i, "agent_id": "player"})
        lines.append(
            {
                "type": "turn_explanation",
                "explanation": {"action": "A", "reasoning": f"r{i}"},
            }
        )
    with open(run_dir / "events.jsonl", "w") as f:
        for ev in lines:
            f.write(json.dumps(ev) + "\n")


def _gameplay_trace(turn: int) -> list[dict]:
    """One turn's retained conversation in the shape ``turn_trace`` carries.

    Routed through the PRODUCER's own projection (``append_agent.display_messages``,
    which is what emits the event) rather than the raw wire shape — the raw
    shape is not what the builder ever reads.
    """
    from src.agent.append_agent import display_messages

    reply = {"role": "assistant", "content": None, "tool_calls": [
        {"id": f"c{turn}", "type": "function", "function": {
            "name": "gameplay",
            "arguments": json.dumps({"inputs": ["a"], "reasoning": f"r{turn}",
                                     "last_turn_succeeded": None})}}]}
    return display_messages([
        {"role": "system", "content": "You play the game."},
        {"role": "user", "content": "Top goal: beat the game."},
        {"role": "user", "content": f"Turn {turn}"},
        reply,
    ])


def _write_append_events(run_dir: Path, *, commit_compaction: bool = True,
                         gameplay_after_compaction: bool = True,
                         agent_type: str = "append_compact",
                         enforce: bool = False, task_master: dict | None = None) -> None:
    """An append-shaped run: two turns, a compaction between them, one gameplay
    RETRY on turn 2, and the run's frozen ``config.json``.

    Deliberately the full vocabulary the builder projects — per-request usage,
    a compaction request with its own trace and handover, and a retried turn —
    so the shape golden and the per-turn cost rule are exercised on one fixture.
    """
    from src.agent.append_agent import display_messages

    run_dir.mkdir(parents=True, exist_ok=True)
    handover = {"continuation_summary": "Left the house.", "memory": {"goal": "Route 1"}}
    events: list[dict] = [
        {"type": "turn_start", "turn": 1, "agent_id": "player"},
        {"type": "screenshot", "file": f"{run_dir}/screenshots/00001_turn_1.png"},
        {"type": "turn_trace", "messages": _gameplay_trace(1), "model_used": "m"},
        {"type": "turn_explanation", "explanation": {"action": "A", "reasoning": "r1"}},
        {"type": "turn_usage", "turn": 1, "phase": "gameplay", "attempt": 1,
         "request_id": "g1", "segment": 1, "cost_usd": 0.01,
         "request_tokens": 100, "response_tokens": 10},
        {"type": "llm_request_usage", "turn": 1, "phase": "gameplay", "segment": 1,
         "request_id": "g1", "attempt": 1, "cost_usd": 0.01, "request_tokens": 100,
         "response_tokens": 10, "provider": "P", "cached_tokens": 0, "cache_write_tokens": 0,
         "raw_usage": {"cost_details": {"upstream_inference_prompt_cost": 100 * 0.00000075}}},
        # ── turn 2: compaction first, then a gameplay attempt that retries ──
        {"type": "turn_start", "turn": 2, "agent_id": "player"},
        {"type": "compaction_start", "turn": 2, "after_turn": 1, "segment": 1, "reason": "interval"},
        {"type": "turn_usage", "turn": 2, "phase": "compaction", "attempt": 1,
         "request_id": "k1", "segment": 1, "cost_usd": 0.05,
         "request_tokens": 900, "response_tokens": 90},
        {"type": "llm_request_usage", "turn": 2, "phase": "compaction", "segment": 1,
         "request_id": "k1", "attempt": 1, "cost_usd": 0.05,
         "request_tokens": 900, "response_tokens": 90, "provider": "P",
         "cached_tokens": 0, "cache_write_tokens": 0,
         "raw_usage": {"cost_details": {"upstream_inference_prompt_cost": 900 * 0.00000075}}},
        {"type": "compaction_trace", "turn": 2, "phase": "compaction", "segment": 1,
         "request_id": "k1", "messages": display_messages([
             {"role": "system", "content": "You play the game."},
             {"role": "user", "content": "Turn 2"},
             {"role": "user", "content": "Prepare a handover after turn 1."},
             {"role": "assistant", "content": None, "tool_calls": [
                 {"id": "k1", "type": "function", "function": {
                     "name": "compaction", "arguments": json.dumps(handover)}}]},
         ])},
        # Dropped when commit_compaction is False: the run died after the
        # compaction request was traced but before its handover was saved.
        {"type": "compaction_complete", "turn": 2, "after_turn": 1, "segment": 2,
         "handover": handover, "previous_memory": {"goal": "leave the house"},
         "reasoning_transition": "intentional_compaction_reset"},
    ]
    # Dropped when gameplay_after_compaction is False: the run died inside its
    # compaction, so turn 2 has a turn_usage but never made a gameplay request.
    gameplay = [
        {"type": "turn_trace", "messages": _gameplay_trace(2), "model_used": "m"},
        {"type": "turn_explanation", "explanation": {"action": "B", "reasoning": "r2"}},
        {"type": "turn_usage", "turn": 2, "phase": "gameplay", "attempt": 1,
         "request_id": "g2a", "segment": 2, "cost_usd": 0.02,
         "request_tokens": 200, "response_tokens": 20},
        {"type": "turn_usage", "turn": 2, "phase": "gameplay", "attempt": 2,
         "request_id": "g2b", "segment": 2, "cost_usd": 0.03,
         "request_tokens": 210, "response_tokens": 21},
        {"type": "llm_request_usage", "turn": 2, "phase": "gameplay", "segment": 2,
         "request_id": "g2b", "attempt": 2, "cost_usd": 0.03, "request_tokens": 210,
         "response_tokens": 21, "provider": "P", "cached_tokens": 0, "cache_write_tokens": 0,
         "raw_usage": {"cost_details": {"upstream_inference_prompt_cost": 210 * 0.00000075}}},
    ]
    if gameplay_after_compaction:
        events += gameplay
    if not commit_compaction:
        events = [e for e in events if e["type"] != "compaction_complete"]
    with open(run_dir / "events.jsonl", "w") as f:
        for event in events:
            f.write(json.dumps(event) + "\n")
    config = {"agent_type": agent_type, "task_master": task_master,
              "referee": {"checkpoints": "configs/ladder.yaml", "enforce": enforce}}
    (run_dir / "config.json").write_text(json.dumps(config))


@pytest.fixture
def append_run_dir(tmp_path: Path) -> Path:
    d = tmp_path / "runs" / "append_run"
    _write_append_events(d)
    return d


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    d = tmp_path / "runs" / "casual_run"
    _write_events(d, n_turns=2)
    return d


@pytest.fixture
def client(tmp_path: Path):
    """TestClient with a control plane whose runs_root is tmp_path/runs."""
    runs_root = tmp_path / "runs"
    executor = FakeExecutor(runs_root=runs_root)
    server.configure_control_plane(
        queue_manager=object(), executor=executor, run_index=FakeIndex()
    )
    tc = TestClient(server.app)
    yield tc
    server._CONTROL["queue"] = None
    server._CONTROL["executor"] = None
    server._CONTROL["index"] = None


# ───────────────────────────── B5.1 — projection shape ─────────────────────────────


def test_build_run_trace_shape(run_dir: Path):
    data = build_run_trace(run_dir)
    assert set(["run_id", "has_tasks", "task_count", "turn_count", "tasks"]).issubset(
        data.keys()
    )
    assert data["run_id"] == run_dir.name
    assert data["has_tasks"] is False
    assert data["turn_count"] == 2  # two turn_start events written
    assert isinstance(data["tasks"], list) and data["tasks"]  # non-empty
    # casual run → single implicit group holding both turns
    assert len(data["tasks"][0]["turns"]) == 2


# ───────────────────────────── B5.2 — cache round-trips ─────────────────────────────


def test_build_and_cache_trace_writes_trace_json(run_dir: Path):
    data = build_and_cache_trace(run_dir)
    cache = run_dir / "trace.json"
    assert cache.is_file()
    with open(cache) as f:
        on_disk = json.load(f)
    assert on_disk == data


# ───────────────────────────── B5.3 — endpoint serve-fresh / rebuild-stale ──────────


def test_endpoint_serves_cache_when_fresh(client, run_dir: Path):
    build_and_cache_trace(run_dir)  # cache now newer than events
    r = client.get(f"/api/runs/{run_dir.name}/trace")
    assert r.status_code == 200
    assert r.json()["turn_count"] == 2


def test_system_prompt_kept_verbatim_not_truncated():
    """The Player / TaskMaster system prompt is projected VERBATIM — it used to
    be capped to a 2000-char preview with an ellipsis, but Andreas reads the full
    prompt in the Report, so it must never be truncated (2026-06-17)."""
    from src.app.trace_build import _trace_steps

    long_prompt = "SYSTEM:\n" + ("x" * 5000)  # well past the old 2000-char cap
    grouped = _trace_steps([{"role": "system", "content": long_prompt}])
    assert grouped["system_prompt"] == long_prompt
    assert "…" not in grouped["system_prompt"]


def test_endpoint_rebuilds_when_cache_stale(client, run_dir: Path):
    # Write a BOGUS cache, then make it OLDER than events.jsonl. A fresh cache
    # would be served verbatim (turn_count 999); a stale one must trigger a
    # rebuild → the REAL turn_count (2), proving the mtime guard works.
    cache = run_dir / "trace.json"
    with open(cache, "w") as f:
        json.dump({"turn_count": 999, "run_id": run_dir.name, "tasks": []}, f)

    events = run_dir / "events.jsonl"
    ev_mtime = events.stat().st_mtime
    old = ev_mtime - 100
    os.utime(cache, (old, old))  # cache strictly older than events → stale

    r = client.get(f"/api/runs/{run_dir.name}/trace")
    assert r.status_code == 200
    assert r.json()["turn_count"] == 2  # rebuilt, NOT the 999 sentinel


def test_segment_costs_include_compaction_retries_and_preserve_unknowns(run_dir):
    events = [
        {"type": "llm_request_usage", "request_id": "play", "segment": 1, "phase": "gameplay", "cost_usd": .02},
        {"type": "llm_request_usage", "request_id": "compact", "segment": 1, "phase": "compaction", "cost_usd": .03},
        {"type": "llm_request_error", "request_id": "compact", "segment": 1, "phase": "compaction", "error": "invalid output"},
        {"type": "llm_request_usage", "request_id": "retry", "segment": 1, "phase": "compaction", "cost_usd": .04},
        {"type": "llm_request_usage", "request_id": "next", "segment": 2, "phase": "gameplay", "cost_usd": .01},
        {"type": "llm_request_error", "request_id": "unknown", "segment": 2, "phase": "gameplay"},
    ]
    with (run_dir / 'events.jsonl').open('a') as f:
        for event in events:
            f.write(json.dumps(event) + '\n')
    costs = build_run_trace(run_dir)['segment_costs']
    assert costs['1']['total_cost_usd'] == pytest.approx(.09)
    assert costs['1']['requests'] == 3
    assert costs['2']['total_cost_usd'] is None
    assert costs['2']['reported_cost_usd'] == .01
    assert costs['2']['measured_requests'] == 1
    assert costs['2']['requests'] == 2


def test_trace_attaches_billing_implied_cache_when_pricing_snapshot_exists(tmp_path):
    run_dir = tmp_path / "run"
    _write_events(run_dir, 1)
    usage = {"type": "llm_request_usage", "turn": 1, "phase": "gameplay", "segment": 1, "request_id": "r1",
             "provider": "Google AI Studio", "request_tokens": 10000, "cached_tokens": 0, "cache_write_tokens": 0,
             "raw_usage": {"cost_details": {"upstream_inference_prompt_cost": 10000 * 0.00000075}}}
    with open(run_dir / "events.jsonl", "a") as f:
        f.write(json.dumps(usage) + "\n")
    trace = build_run_trace(run_dir)
    assert trace["cache"]["measured_attempts"] == 1 and "implied_cached_tokens" not in trace["cache"]
    (run_dir / "conversation").mkdir()
    (run_dir / "conversation/endpoint-pricing.json").write_text(json.dumps({"endpoints": [
        {"name": "Google AI Studio", "provider_name": "Google AI Studio",
         "pricing": {"prompt": "0.00000075", "input_cache_read": "0.000000075"}}]}))
    trace = build_run_trace(run_dir)
    assert trace["cache"]["implied_cached_tokens"] == 0 and trace["cache"]["implied_agreement"] == {"matches": 1}
    event = trace["tasks"][0]["turns"][0]["diagnostics"][0]
    assert event["implied_cache"]["status"] == "no_discount_billed"



def test_split_turn_is_folded_back_into_one_observation():
    """`final_turn_text_only` profiles send user[text, screenshot] → assistant "Observed." → user prompt.
    The turn view must still show the observation (OCR text) as the input, not the acknowledgement or
    the bare prompt, and the synthetic assistant message must not flip a segment start to "continued".
    The fixture goes through display_messages, the projection turn_trace events actually carry."""
    from src.app.trace_build import _project_turn
    from src.agent.append_agent import TURN_ACK, display_messages
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,c2NyZWVu"}}
    call = {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "gameplay", "arguments": "{\"inputs\": [\"a\"]}"}}]}
    observation = {"role": "user", "content": [{"type": "text", "text": "Turn 1\n\nText observed since your last action:\nOAK: Hello!"}, image]}
    ack = {"role": "assistant", "content": TURN_ACK}
    prompt = {"role": "user", "content": "Turn 1: respond with your next action now."}
    head = [{"role": "system", "content": "You play the game."}, {"role": "user", "content": "Top goal: beat the game."}]
    split = display_messages(head + [observation, ack, prompt, call])
    plain = display_messages(head + [observation, call])
    diagnostics = [{"type": "llm_request_usage", "turn": 1}]
    folded = _project_turn({"turn": 1, "trace": split, "events": diagnostics})["trace"]
    control = _project_turn({"turn": 1, "trace": plain, "events": diagnostics})["trace"]
    for trace in (folded, control):
        assert trace["conversation"] == "start"
        assert "Top goal: beat the game." in trace["segment_context"]
        assert "OAK: Hello!" in json.dumps(trace)
    assert TURN_ACK not in json.dumps(folded["steps"])
    assert "respond with your next action now" in json.dumps(folded)
    # A later turn: the acknowledgement must not hide the real model reply, and "continued" still holds.
    later = display_messages(head + [observation, ack, prompt, call, {"role": "tool", "tool_call_id": "c1", "content": "Accepted."}, observation, ack, prompt, call])
    view = _project_turn({"turn": 2, "trace": later, "events": diagnostics})["trace"]
    assert view["conversation"] == "continued"
    assert TURN_ACK not in json.dumps(view["steps"])
    assert "OAK: Hello!" in json.dumps(view)


def test_compaction_view_skips_the_split_turn_acknowledgement():
    from src.app.trace_build import _compaction_trace
    from src.agent.append_agent import TURN_ACK, display_messages
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,c2NyZWVu"}}
    observation = {"role": "user", "content": [{"type": "text", "text": "Turn 16\n\nText observed since your last action:\nRIVAL: Smell ya later!"}, image]}
    prompt = {"role": "user", "content": "Pause gameplay after turn 15 and prepare a handover for yourself."}
    reply = {"role": "assistant", "content": None, "tool_calls": [{"id": "c9", "type": "function", "function": {"name": "compaction", "arguments": "{\"continuation_summary\": \"x\", \"memory\": {}}"}}]}
    messages = display_messages([{"role": "system", "content": "sys"}, observation, {"role": "assistant", "content": TURN_ACK}, prompt, reply])
    view = _compaction_trace([{"type": "compaction_trace", "messages": messages}])
    assert "RIVAL: Smell ya later!" in view["user_input"] and "Pause gameplay" in view["user_input"]
    assert TURN_ACK not in json.dumps(view)


# ─────────────────── #1 — the version IS the cache-invalidation contract ───────────────────


def test_projected_key_sets_are_pinned_to_the_trace_version(append_run_dir: Path):
    """A shape change without a bump, or a bump without a recorded shape, fails.

    ``/api/runs/{id}/trace`` hands back a cached ``trace.json`` untouched while
    its stamp matches ``TRACE_VERSION``, so a builder that starts projecting a
    new key while the stamp stands still leaves every report on disk serving the
    old projection. This test is the forcing function: edit the builder's key
    sets and the golden below has to move with it, which means choosing a
    version to move it under.
    """
    shape = TRACE_SHAPES.get(TRACE_VERSION)
    assert shape is not None, (
        f"TRACE_VERSION is {TRACE_VERSION} and TRACE_SHAPES has no golden for it. "
        "Record the projected key sets for this version — the bump is what "
        "retires every stale trace.json on disk."
    )
    bump = ("the builder's projected keys no longer match the golden for "
            f"TRACE_VERSION {TRACE_VERSION}. Bump TRACE_VERSION and record the "
            "new key sets, or every cached trace.json keeps serving the old shape.")

    data = build_run_trace(append_run_dir)
    assert set(data) == shape["trace"], bump
    assert data["trace_version"] == TRACE_VERSION

    task = data["tasks"][0]
    assert set(task) == shape["task"], bump

    timeline = task["timeline"]
    turn = next(e for e in timeline if e["kind"] == "turn")
    compaction = next(e for e in timeline if e["kind"] == "compaction")
    assert set(turn) == shape["turn"], bump
    assert set(turn["trace"]) == shape["turn_trace"], bump
    assert set(compaction) == shape["compaction"], bump
    assert set(compaction["trace"]) == shape["compaction_trace"], bump


def test_endpoint_rebuilds_a_cache_stamped_with_another_version(client, run_dir: Path):
    """The BUMP alone must invalidate, with nothing for the route to do.

    The 13 stale runs had caches that were newer than events.jsonl, so the mtime
    guard served them forever. Only the stamp comparison can retire them, and it
    must rebuild AND rewrite — otherwise every open pays for the rebuild.
    """
    cache = run_dir / "trace.json"
    build_and_cache_trace(run_dir)          # a genuinely FRESH cache…
    stale = json.loads(cache.read_text())
    stale["trace_version"] = TRACE_VERSION - 1
    stale["turn_count"] = 999               # …stamped one version back
    cache.write_text(json.dumps(stale))
    events = run_dir / "events.jsonl"
    newer = events.stat().st_mtime + 100
    os.utime(cache, (newer, newer))         # mtime says fresh; only the stamp is stale

    r = client.get(f"/api/runs/{run_dir.name}/trace")
    assert r.status_code == 200
    assert r.json()["turn_count"] == 2                 # rebuilt, not the 999 sentinel
    assert r.json()["trace_version"] == TRACE_VERSION
    # rewritten, so the next open serves the cache instead of rebuilding again
    assert json.loads(cache.read_text())["trace_version"] == TRACE_VERSION


def test_bumping_the_version_reprojects_implied_cache_economics(tmp_path: Path):
    """The concrete damage the unbumped version did: a run whose endpoint prices
    are on disk served a projection with no implied-cache fields at all."""
    run_dir = tmp_path / "run"
    _write_append_events(run_dir)
    (run_dir / "conversation").mkdir()
    (run_dir / "conversation/endpoint-pricing.json").write_text(json.dumps({"endpoints": [
        {"name": "P", "provider_name": "P",
         "pricing": {"prompt": "0.00000075", "input_cache_read": "0.000000075"}}]}))
    data = build_run_trace(run_dir)
    assert "implied_read_fraction" in data["cache"]
    usage = [e for t in data["tasks"][0]["timeline"] if t["kind"] == "turn"
             for e in t["diagnostics"] if e["type"] == "llm_request_usage"]
    assert usage and all("implied_cache" in e for e in usage)


# ─────────────────── #3 / harness — run-shape facts the report needs ───────────────────


def test_harness_identity_comes_from_the_recorded_config(tmp_path: Path):
    """The report never said which agent drove the run. agent_type + task_master
    decide it, not event vocabulary, so a crashed run still names its harness."""
    cases = [
        ("append_compact", None, "append_compact"),
        (None, {"enabled": True}, "task_master"),
        (None, None, "self_directed"),
        (None, {"enabled": False}, "self_directed"),
    ]
    for i, (agent_type, task_master, expected) in enumerate(cases):
        run_dir = tmp_path / f"run{i}_{expected}"
        _write_append_events(run_dir, agent_type=agent_type, task_master=task_master)
        harness = build_run_trace(run_dir)["harness"]
        assert harness["id"] == expected
        assert harness["label"]


def test_harness_is_none_without_a_config(run_dir: Path):
    """A run dir with no config.json cannot name a harness; the report then
    shows no chip rather than guessing one."""
    assert not (run_dir / "config.json").exists()
    assert build_run_trace(run_dir)["harness"] is None
    assert build_run_trace(run_dir)["referee_enforced"] is None


def test_referee_enforcement_is_exposed_so_the_report_can_hide_an_unearned_score(tmp_path: Path):
    """Observe-only runs are scored against the ladder but judged against
    nothing. The report needs the distinction to stop showing a Completion %
    and a deadline scorecard for a run that was never gated."""
    observed = tmp_path / "observed"
    enforced = tmp_path / "enforced"
    _write_append_events(observed, enforce=False)
    _write_append_events(enforced, enforce=True)
    assert build_run_trace(observed)["referee_enforced"] is False
    assert build_run_trace(enforced)["referee_enforced"] is True


# ─────────────────── #17 — one definition of "cleared" ───────────────────


def test_cleared_gate_statuses_are_the_projections_own_set(append_run_dir: Path):
    """The report counted only ``done`` while the index counted ``done`` and
    ``auto``, so an auto-cleared gate read as unreached in the report header and
    as reached in the Completion % beside it. Shipping the set means the report
    cannot re-declare it."""
    from src.app.projection import _CLEARED_STATUSES

    assert build_run_trace(append_run_dir)["cleared_gate_statuses"] == list(_CLEARED_STATUSES)
    assert "auto" in _CLEARED_STATUSES  # the status the two readers disagreed on


# ─────────────────── #24 — a retried turn's real cost ───────────────────


def test_turn_cost_sums_gameplay_attempts_and_leaves_the_compaction_out(append_run_dir: Path):
    """append emits one turn_usage per REQUEST. Keeping the last one billed a
    retried turn for its final attempt alone, and a turn whose compaction ran
    last would have been billed the compaction's cost instead of its own."""
    timeline = build_run_trace(append_run_dir)["tasks"][0]["timeline"]
    turns = {t["turn"]: t for t in timeline if t["kind"] == "turn"}
    compaction = next(t for t in timeline if t["kind"] == "compaction")

    assert turns[1]["cost_usd"] == pytest.approx(0.01)          # single attempt, unchanged
    assert turns[2]["cost_usd"] == pytest.approx(0.05)          # 0.02 + 0.03, NOT 0.03
    assert compaction["cost_usd"] == pytest.approx(0.05)        # its own request, unchanged
    # tokens describe the request that produced the action, so they are the last
    # gameplay attempt's — never the compaction's 900.
    assert turns[2]["request_tokens"] == 210


def test_legacy_single_usage_turns_are_untouched(run_dir: Path):
    """Control: the legacy agent emits exactly one turn_usage per turn, so the
    fold is an identity there. Reverting it must not change this."""
    with (run_dir / "events.jsonl").open("a") as f:
        f.write(json.dumps({"type": "turn_usage", "turn": 2, "cost_usd": 0.07,
                            "request_tokens": 500, "response_tokens": 50}) + "\n")
    turns = {t["turn"]: t for t in build_run_trace(run_dir)["tasks"][0]["turns"]}
    assert turns[2]["cost_usd"] == pytest.approx(0.07)
    assert turns[2]["request_tokens"] == 500


def test_a_compaction_only_turn_survives_and_keeps_its_cost_off_the_turn(tmp_path: Path):
    """The last turn of a run that died inside its compaction has a turn_usage
    but no gameplay one. It must still be projected (its compaction row is the
    only record of what happened) with no cost of its own."""
    run_dir = tmp_path / "run"
    _write_append_events(run_dir, gameplay_after_compaction=False)
    timeline = build_run_trace(run_dir)["tasks"][0]["timeline"]
    turns = {t["turn"]: t for t in timeline if t["kind"] == "turn"}
    assert set(turns) == {1, 2}                      # turn 2 was NOT dropped as a fragment
    assert turns[2]["cost_usd"] is None              # the compaction's cost is not the turn's
    assert next(t for t in timeline if t["kind"] == "compaction")["cost_usd"] == pytest.approx(0.05)


def test_a_resumed_turn_does_not_supersede_its_own_compaction(tmp_path: Path):
    """Regression guard on the ``_turn_settled`` predicate.

    A kill inside the compaction leaves a same-numbered fragment holding
    compaction_start / _trace / _complete, and the resume re-runs that turn. The
    fragment is only kept because it is "settled", which used to mean "the turn
    dict has a usage key" — a key the compaction's own usage no longer sets. Read
    off the raw events instead, so folding the compaction out of the turn's cost
    does not silently delete the compaction row from the report.
    """
    run_dir = tmp_path / "run"
    _write_append_events(run_dir, gameplay_after_compaction=False)
    with (run_dir / "events.jsonl").open("a") as f:      # the resumed session re-runs turn 2
        for event in [
            {"type": "turn_start", "turn": 2, "agent_id": "player"},
            {"type": "turn_trace", "messages": _gameplay_trace(2), "model_used": "m"},
            {"type": "turn_explanation", "explanation": {"action": "B", "reasoning": "r2"}},
            {"type": "turn_usage", "turn": 2, "phase": "gameplay", "attempt": 1,
             "request_id": "g2", "segment": 2, "cost_usd": 0.02,
             "request_tokens": 200, "response_tokens": 20},
        ]:
            f.write(json.dumps(event) + "\n")
    data = build_run_trace(run_dir)
    assert data["compaction_count"] == 1                  # the compaction row survived
    turns = [t for t in data["tasks"][0]["timeline"] if t["kind"] == "turn"]
    assert [t["turn"] for t in turns] == [1, 2, 2]        # unchanged from before the fold


# ─────────────────── #19 — an uncommitted compaction's proposal ───────────────────


def test_uncommitted_compaction_still_carries_its_proposed_handover(tmp_path: Path):
    """A run that died between the traced compaction request and
    ``compaction_complete`` has no committed output — but the model's proposal is
    in the final_result step, parsed, and the report renders it labelled
    "proposed, not committed" instead of only "No committed compaction output"."""
    committed = tmp_path / "committed"
    uncommitted = tmp_path / "uncommitted"
    _write_append_events(committed, commit_compaction=True)
    _write_append_events(uncommitted, commit_compaction=False)

    def compaction(run_dir):
        return next(t for t in build_run_trace(run_dir)["tasks"][0]["timeline"]
                    if t["kind"] == "compaction")

    done = compaction(committed)
    assert done["complete"] is True
    assert done["trace"]["output"]["continuation_summary"] == "Left the house."

    open_row = compaction(uncommitted)
    assert open_row["complete"] is False
    assert open_row["trace"]["output"] is None          # nothing was committed…
    final = next(s for s in open_row["trace"]["steps"] if s["type"] == "final_result")
    assert final["args"]["continuation_summary"] == "Left the house."   # …but the proposal is here
    assert final["args"]["memory"] == {"goal": "Route 1"}


def test_compaction_count_is_projected_for_the_report_header(append_run_dir: Path, run_dir: Path):
    """Rendered nowhere until 2026-09-07; it is the defining event of the
    harness. Zero on a legacy run, so its header is unchanged."""
    assert build_run_trace(append_run_dir)["compaction_count"] == 1
    assert build_run_trace(run_dir)["compaction_count"] == 0
