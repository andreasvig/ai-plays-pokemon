"""Build + cache the task-grouped trace projection for a finished run.

Extracted out of the FastAPI module so it can run at run-finalize (executor)
as well as on report open (server). The projection groups a run's
``events.jsonl`` into a SPA-friendly structure: each task group is a master
decision (model/cost/structured trace/input thumbnails) with the Player's
turns nested. Casual / non-TaskMaster runs collapse into a single implicit
group so the SPA never 500s.

These helpers depend ONLY on :mod:`pathlib` and :mod:`src.core.event_parsing`
(imported inside the funcs, as before).
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

# Cache-invalidation stamp for ``run_dir/trace.json``. ``/api/runs/{id}/trace``
# serves a cached projection only while its stamp equals this constant, so a
# BUMP is the only thing that retires every stale cache on disk — and the only
# thing that has to happen when the builder's output shape changes.
#
# BUMP THIS whenever a projected key is added, renamed, dropped or re-meaned.
# ``tests/test_trace_build.py::TRACE_SHAPES`` holds a golden of the projected
# key sets per version and fails both ways: a shape change without a bump, and
# a bump without a recorded golden.
#
# Version 5 shipped twice: the implied-cache economics (fe1e7a7) and the
# split-turn fold (5b8ab07) both landed under it, which left 13 append runs
# serving a pre-fe1e7a7 projection whose Cache overview read "No pricing
# snapshot" for runs whose ``endpoint-pricing.json`` was on disk.
TRACE_VERSION = 6


def _screenshot_ref(file_path: str | None) -> str | None:
    """Reduce a stored screenshot path to its basename (the SPA composes the URL).

    ``group_events_by_turn`` stores the event's full ``file`` (e.g.
    ``local/runs/<dir>/screenshots/00001_turn_1.png``). The SPA only needs the
    basename to hit ``GET /api/runs/{id}/screenshots/{name}``. None → None.
    """
    if not file_path:
        return None
    return Path(file_path).name


def _trace_steps(messages: list[dict]) -> dict:
    """Project a raw message trace into SPA-friendly structured steps.

    Reuses ``event_parsing._group_trace_into_steps`` (the canonical trace
    parser). The system prompt is kept VERBATIM — it used to be capped to a 2000
    char preview, but Andreas reads the full Player / TaskMaster system prompts
    in the Report, so it must never be truncated (2026-06-17). Existing
    ``trace.json`` caches were built with the old cap; they are deleted on the
    finalize path / can be force-rebuilt by removing the file.
    """
    from src.core import event_parsing

    return event_parsing._group_trace_into_steps(messages or [])


def _is_turn_ack(message: dict) -> bool:
    from src.agent.append_agent import TURN_ACK
    return message.get("role") == "assistant" and message.get("content") == TURN_ACK and not message.get("tool_calls")


def _project_turn(turn: dict) -> dict:
    """Project one per-turn dict (from ``group_events_by_turn``) for the SPA."""
    exp = turn.get("explanation") or {}
    usage = turn.get("usage") or {}
    diagnostics = [e for e in turn.get("events", []) if e.get("type") in (
        "llm_request_usage", "llm_request_error", "compaction_start", "compaction_trace", "compaction_complete")]
    messages = turn.get("trace", [])
    conversation = None
    segment_context = ""
    if diagnostics:
        # Render the additions to the conversation. Full request history stays
        # in the raw trace/archive, not in each turn's observation or output.
        user_indices = [i for i, m in enumerate(messages) if m.get("role") == "user"]
        if user_indices:
            last_user = user_indices[-1]
            first_user = last_user
            # Split-turn profiles (`final_turn_text_only`) deliver one observation as three messages:
            # user[text, screenshot], the synthetic assistant acknowledgement, user action prompt.
            # Fold them back into one observation so the turn shows its OCR text and screenshot,
            # and so the acknowledgement does not count as model output when deciding "start".
            if last_user >= 2 and _is_turn_ack(messages[last_user - 1]) and messages[last_user - 2].get("role") == "user":
                first_user = last_user - 2
                observation = deepcopy(messages[first_user])
                prompt = messages[last_user].get("content", "")
                if isinstance(observation.get("content"), list):  # raw wire shape
                    observation["content"] = observation["content"] + [{"type": "text", "text": prompt}]
                else:  # display projection (display_messages): text with "[image]" placeholders
                    observation["content"] = f"{observation.get('content', '')}\n\n{prompt}"
                messages = messages[:first_user] + [observation] + messages[last_user + 1:]
                last_user = first_user
            prior = messages[:last_user]
            start = not any(m.get("role") not in ("system", "user") for m in prior)
            conversation = "start" if start else "continued"
            systems = []
            if start:
                systems = [{"role": "system", "content": "\n\n".join(
                    m.get("content", "") for m in prior if m.get("role") == "system")}]
                segment_context = "\n\n".join(m.get("content", "") for m in prior if m.get("role") == "user")
            messages = systems + messages[last_user:]
    trace = _trace_steps(messages)
    if conversation:
        trace.update(conversation=conversation, segment_context=segment_context)
    return {
        "turn": turn.get("turn"),
        "task_index": turn.get("task_index"),
        "action": report_format_action(turn.get("action")),
        "reasoning": exp.get("reasoning", ""),
        "last_turn_succeeded": exp.get("last_turn_succeeded"),
        "screenshot": _screenshot_ref(turn.get("screenshot")),
        "cost_usd": usage.get("cost_usd"),
        "request_tokens": usage.get("request_tokens"),
        "response_tokens": usage.get("response_tokens"),
        "trace": trace,
        "diagnostics": diagnostics,
    }


def report_format_action(action) -> str:
    """Thin reuse wrapper over ``event_parsing._format_action`` (list vs str)."""
    from src.core import event_parsing

    return event_parsing._format_action(action if action is not None else "?")


def _compaction_trace(events: list[dict]) -> dict:
    """Project only new compaction inputs and its response, with parsed output."""
    event = next((e for e in reversed(events) if e["type"] == "compaction_trace"), {})
    messages = event.get("messages", [])
    users = [i for i, m in enumerate(messages) if m.get("role") == "user"]
    trace = _trace_steps([])
    if users:
        # The latest observation and compaction instruction are consecutive
        # user messages appended to the existing segment.
        first = last = users[-1]
        # Consecutive user messages; a split-turn profile's synthetic acknowledgement sits between the
        # observation and the compaction prompt and is skipped, not shown.
        while first > 0 and (messages[first - 1].get("role") == "user" or _is_turn_ack(messages[first - 1])):
            first -= 1
        inputs = [m for m in messages[first:last + 1] if not _is_turn_ack(m)]
        trace = _trace_steps(messages[last + 1:])
        trace["user_input"] = "\n\n".join(m.get("content", "") for m in inputs)
        trace["user_messages"] = inputs
        for step in trace["steps"]:
            if step["type"] == "final_result" and isinstance(step["args"], str):
                try:
                    step["args"] = json.loads(step["args"])
                except (ValueError, TypeError):
                    pass
    complete = next((e for e in reversed(events) if e["type"] == "compaction_complete"), {})
    trace["output"] = complete.get("handover")
    trace["previous_memory"] = complete.get("previous_memory")
    return trace


def _add_conversation_timeline(groups: list[dict]) -> int:
    """Separate compaction from gameplay numbering while retaining chronology."""
    number = 0
    for group in groups:
        timeline = []
        for turn in group["turns"]:
            compaction = [e for e in turn["diagnostics"]
                          if e["type"].startswith("compaction_") or e.get("phase") == "compaction"]
            if compaction:
                number += 1
                attempts = {e["request_id"]: e for e in compaction
                            if e["type"] == "llm_request_usage"}
                usage = {key: sum(e[key] for e in attempts.values())
                         if attempts and all(e.get(key) is not None for e in attempts.values()) else None
                         for key in ("cost_usd", "request_tokens", "response_tokens")}
                timeline.append({"kind": "compaction", "number": number,
                                 "after_turn": next((e["after_turn"] for e in compaction if "after_turn" in e), turn["turn"] - 1),
                                 "complete": any(e["type"] == "compaction_complete" for e in compaction),
                                 "trace": _compaction_trace(compaction),
                                 "diagnostics": compaction, **usage})
                turn["diagnostics"] = [e for e in turn["diagnostics"] if e not in compaction]
            turn["fresh"] = turn["trace"].get("conversation") == "start"
            timeline.append({"kind": "turn", **turn})
        group["timeline"] = timeline
    return number


def _load_endpoint_pricing(run_dir: Path):
    """Endpoint list prices recorded for this run, or None when never snapshotted.

    ``conversation/endpoint-pricing.json`` is written by the append agent on its
    first request; protocol probes wrote a single ``endpoint-snapshot.json``.
    """
    multi = run_dir / "conversation" / "endpoint-pricing.json"
    single = run_dir / "endpoint-snapshot.json"
    try:
        if multi.exists():
            return json.loads(multi.read_text()).get("endpoints") or []
        if single.exists():
            return [json.loads(single.read_text())]
    except (OSError, ValueError):
        return None
    return None


def _run_config(run_dir: Path) -> dict:
    """The run's frozen ``config.json``, or ``{}`` when it was never written.

    The report reads run-shape facts the summary projection does not carry —
    which harness drove the run, whether the referee's deadlines were armed —
    and the trace is the channel it already fetches, so they are derived here
    rather than grown onto ``RunSummary``.
    """
    try:
        return json.loads((run_dir / "config.json").read_text())
    except (OSError, ValueError):
        return {}


# Harness identity for the report's chip. ``run_summary.json["agent_type"]``
# exists but has no readers and is absent from RunSummary, so the config is the
# source: ``append_compact`` is the append harness, and a legacy run is
# TaskMaster-driven or self-directed depending on its ``task_master`` block.
_HARNESS_LABELS = {
    "append_compact": "append-and-compact",
    "task_master": "TaskMaster",
    "self_directed": "self-directed",
}


def _harness(config: dict) -> dict | None:
    """``{"id", "label"}`` for the agent that drove the run, or None.

    None when the run wrote no ``config.json`` — the report then shows no chip
    rather than guessing a harness from event vocabulary (which is what the
    ``if diagnostics:`` gate in ``_project_turn`` has to do, and is exactly the
    coupling the chip exists to replace).
    """
    if not config:
        return None
    if config.get("agent_type") == "append_compact":
        key = "append_compact"
    elif (config.get("task_master") or {}).get("enabled"):
        key = "task_master"
    else:
        key = "self_directed"
    return {"id": key, "label": _HARNESS_LABELS[key]}


def _referee_enforced(config: dict) -> bool | None:
    """Were the gate DEADLINES armed for this run? None when unknowable.

    Calibration and casual runs run the referee observe-only
    (``referee.enforce: false``): the ladder is still scored into
    ``run_summary.json``, so the report used to show a Completion % and a full
    deadline scorecard for a run that was never scored against them, while
    History showed a dash for the same run. The report needs this to tell the
    two apart; ``None`` (no config.json) means "cannot tell", and the report
    falls back to the run's ``kind``.
    """
    if not config:
        return None
    return bool((config.get("referee") or {}).get("enforce"))


def _attach_implied_cache(run_dir: Path, events: list[dict]) -> None:
    """Add the billing-implied cache estimate to each usage event, in place."""
    from src.agent.append_agent import implied_cache
    pricing = _load_endpoint_pricing(run_dir)
    for event in events:
        if event.get("type") != "llm_request_usage":
            continue
        event["implied_cache"] = {"status": "no_pricing_snapshot"} if pricing is None else implied_cache(event, pricing)


def build_run_trace(run_dir: Path) -> dict:
    """Build the task-grouped trace JSON for a finished run (Round 8 B1+B2).

    Reuses ``event_parsing``'s grouping verbatim. For a TaskMaster run, returns
    one entry per task group (master decision + nested player turns). For a
    casual / non-TaskMaster run (no ``task_started`` events), returns a SINGLE
    implicit group holding all turns — a degenerate-but-valid shape so the SPA
    never 500s.
    """
    from src.core import event_parsing

    config = _run_config(run_dir)
    events = event_parsing.load_events(run_dir)
    _attach_implied_cache(run_dir, events)
    turns = event_parsing.group_events_by_turn(events)
    has_tasks = any(e.get("type") == "task_started" for e in events)

    tasks_out: list[dict] = []
    if has_tasks:
        groups = event_parsing.group_turns_by_task(turns, events)
        for g in groups:
            tasks_out.append(
                {
                    "task_index": g.get("task_index"),
                    "title": g.get("title", ""),
                    "description": g.get("description", ""),
                    "success_criteria": g.get("success_criteria", ""),
                    "rating": g.get("rating"),
                    "player_self_assessment": g.get("player_self_assessment"),
                    "player_task_summary": g.get("player_task_summary"),
                    "master_model": g.get("master_model", ""),
                    "master_cost": g.get("master_cost", 0) or 0,
                    # input_images already carry {label, data_url}; inline as-is
                    # (no separate on-disk name to reference them by).
                    "master_input_images": g.get("master_input_images", []) or [],
                    "master_trace": _trace_steps(g.get("master_trace", [])),
                    "turns": [_project_turn(t) for t in g.get("turns", [])],
                }
            )
    else:
        # Implicit single group (no TaskMaster): all turns, empty master node.
        tasks_out.append(
            {
                "task_index": None,
                "title": "",
                "description": "",
                "success_criteria": "",
                "rating": None,
                "player_self_assessment": None,
                "player_task_summary": None,
                "master_model": "",
                "master_cost": 0,
                "master_input_images": [],
                "master_trace": {"system_prompt": "", "user_input": "", "steps": []},
                "turns": [_project_turn(t) for t in turns],
            }
        )

    compaction_count = _add_conversation_timeline(tasks_out)
    from src.agent.append_agent import cache_totals
    # The gate statuses the leaderboard/index treat as CLEARED, shipped to the
    # report so its header count cannot drift from the Completion % the
    # projection computed. The report must not re-declare the set: a gate that
    # is `auto` was counted by the projection and read as pending in the header.
    from src.app.projection import _CLEARED_STATUSES
    attempts = {}
    for event in events:
        if event.get("type") in ("llm_request_usage", "llm_request_error"):
            key = event.get("request_id")
            attempts[key] = {**attempts.get(key, {}), **event}
    measurements = list(attempts.values())
    segment_attempts = {}
    for event in measurements:
        segment_attempts.setdefault(str(event.get("segment", "?")), []).append(event)
    segment_costs = {}
    for segment, requests in segment_attempts.items():
        known = [r["cost_usd"] for r in requests if r.get("cost_usd") is not None]
        segment_costs[segment] = {
            "total_cost_usd": sum(known) if len(known) == len(requests) else None,
            "reported_cost_usd": sum(known),
            "measured_requests": len(known), "requests": len(requests),
        }
    breakdown = {}
    for event in measurements:
        key = f"{event.get('phase', 'unknown')} / {event.get('provider') or 'unknown'} / segment {event.get('segment', '?')}"
        breakdown.setdefault(key, []).append(event)
    return {
        "trace_version": TRACE_VERSION,
        "run_id": run_dir.name,
        "has_tasks": has_tasks,
        "task_count": len(tasks_out),
        "turn_count": len(turns),
        "compaction_count": compaction_count,
        "harness": _harness(config),
        "referee_enforced": _referee_enforced(config),
        "cleared_gate_statuses": list(_CLEARED_STATUSES),
        "tasks": tasks_out,
        "cache": cache_totals(measurements) if measurements else None,
        "cache_breakdown": {key: cache_totals(value) for key, value in breakdown.items()},
        "segment_costs": segment_costs,
    }


def build_and_cache_trace(run_dir: Path) -> dict:
    """Build the trace projection and persist it to run_dir/trace.json."""
    data = build_run_trace(run_dir)
    try:
        with open(run_dir / "trace.json", "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass
    return data
