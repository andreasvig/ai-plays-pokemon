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

from src.app.trace_build import build_and_cache_trace, build_run_trace
from src.dashboard import server


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
