"""Canonical parsers for a run's ``events.jsonl``.

These functions turn the flat event log into the structured shapes the
dashboard SPA renders (per-turn dicts, TaskMaster task groups, structured
trace steps). They were extracted from the now-removed standalone HTML report
generator so the live SPA trace builder (``src/dashboard/server.py``) has a
single, dependency-light home for them.

Pure data transforms — no I/O beyond reading the events file, no HTML.
"""

import json
from pathlib import Path


def load_events(run_dir: Path) -> list[dict]:
    """Load all events from events.jsonl."""
    events = []
    events_file = run_dir / "events.jsonl"
    if not events_file.exists():
        return events
    with open(events_file) as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def _turn_settled(turn: dict) -> bool:
    """True if a turn produced real output, vs an aborted mid-flight fragment.

    A turn killed in flight (the instant-kill path) leaves only a bare
    ``turn_start`` in the log — it is abandoned before any llm_output / turn_trace
    / explanation / usage is recorded. The resume re-runs that SAME turn number
    and logs it fully. This predicate distinguishes the complete turn from the
    aborted fragment so the latter can be superseded.
    """
    return bool(
        turn.get("trace") is not None
        or turn.get("action")
        or turn.get("explanation")
        or "usage" in turn
    )


def group_events_by_turn(events: list[dict]) -> list[dict]:
    """Group events into turns.

    Resume safety: when a run is killed mid-turn and resumed, the original
    session leaves a bare ``turn_start`` for the in-flight turn (no output — the
    kill abandons it) and the resumed session re-runs and fully logs the SAME
    turn number. Without handling, that surfaces as two same-numbered turns in
    the trace (a phantom empty one + the real one), which both reads as a broken
    resume and breaks the SPA's turn-keyed list. We drop the aborted fragment so
    the trace shows one clean turn — indistinguishable from never having stopped.
    """
    turns = []
    current_turn = None

    for event in events:
        if event["type"] == "turn_start":
            new_turn = event.get("turn", len(turns) + 1)
            if current_turn:
                # Supersede an aborted fragment that the resume is re-running:
                # same turn number AND the prior copy never settled. Any other
                # case (the normal monotonic 6→7, or two genuinely settled turns)
                # is kept as-is.
                if current_turn["turn"] == new_turn and not _turn_settled(current_turn):
                    pass  # discard the aborted fragment
                else:
                    turns.append(current_turn)
            current_turn = {
                "turn": new_turn,
                "agent_id": event.get("agent_id", ""),
                "task_index": event.get("task_index"),
                "events": [],
                "screenshot": None,
                "explanation": None,
                "action": None,
                "tool_calls": [],
            }
        elif current_turn is not None:
            current_turn["events"].append(event)

            if event["type"] == "screenshot":
                current_turn["screenshot"] = event.get("file", "")
            elif event["type"] == "turn_explanation":
                current_turn["explanation"] = event.get("explanation", {})
                current_turn["action"] = event["explanation"].get("action", "")
            elif event["type"] == "tool_call":
                current_turn["tool_calls"].append(event)
            elif event["type"] == "tool_response":
                if current_turn["tool_calls"]:
                    current_turn["tool_calls"][-1]["response"] = event.get("response", "")
            elif event["type"] == "turn_trace":
                current_turn["trace"] = event.get("messages", [])
                current_turn["trace_model"] = event.get("model_used", "")
            elif event["type"] == "turn_user_message":
                current_turn["user_message"] = event.get("message", "")
            elif event["type"] == "turn_usage":
                current_turn["usage"] = event
            elif event["type"] == "ocr_flush":
                current_turn["ocr"] = event

    if current_turn:
        turns.append(current_turn)

    return turns


def group_turns_by_task(turns: list[dict], events: list[dict]) -> list[dict]:
    """Group per-turn dicts into TaskMaster task groups.

    Walks events for ``task_started`` / ``task_master_trace`` / ``task_completed``
    to build one group per task, then buckets ``turns`` by their ``task_index``.
    ``task_completed{N}`` stamps the rating backward onto the already-built group N
    (Decision 2 — grade stamps backward).

    Returns a list of groups, each:
        {
            "task_index": int,
            "title": str,
            "description": str,
            "success_criteria": str,
            "rating": dict | None,        # {status, reasoning} or None if still current
            "master_trace": list[dict],
            "master_model": str,
            "master_cost": float,
            "turns": [turn dict, ...],
        }
    """
    groups: dict[int, dict] = {}
    order: list[int] = []

    for event in events:
        etype = event.get("type")
        if etype == "task_started":
            idx = event.get("task_index")
            if idx is None:
                continue
            if idx not in groups:
                order.append(idx)
            g = groups.setdefault(idx, _empty_task_group(idx))
            g["title"] = event.get("title", "") or g["title"]
            g["description"] = event.get("description", "") or g["description"]
            g["success_criteria"] = event.get("success_criteria", "") or g["success_criteria"]
        elif etype == "task_master_trace":
            idx = event.get("task_index")
            if idx is None:
                continue
            if idx not in groups:
                order.append(idx)
            g = groups.setdefault(idx, _empty_task_group(idx))
            g["master_trace"] = event.get("messages", [])
            g["master_model"] = event.get("model_used", "")
            g["master_cost"] = event.get("cost_usd", 0) or 0
            g["master_input_images"] = event.get("input_images", []) or []
        elif etype == "task_completed":
            idx = event.get("task_index")
            if idx is None:
                continue
            # Stamp backward onto the rated task (may already exist; create if not).
            if idx not in groups:
                order.append(idx)
            g = groups.setdefault(idx, _empty_task_group(idx))
            g["rating"] = event.get("rating") or g["rating"]
            # The Player's own hand-back (final message returned to TaskMaster).
            g["player_self_assessment"] = event.get("player_self_assessment")
            g["player_task_summary"] = event.get("player_task_summary")

    # Bucket turns into their task group by task_index.
    for turn in turns:
        idx = turn.get("task_index")
        if idx is None:
            # No task_index on this turn — attach to the most recent known group
            # if one exists, else skip (legacy path won't call this function).
            idx = order[-1] if order else None
            if idx is None:
                continue
        if idx not in groups:
            order.append(idx)
            groups[idx] = _empty_task_group(idx)
        groups[idx]["turns"].append(turn)

    return [groups[idx] for idx in order]


def _empty_task_group(idx: int) -> dict:
    return {
        "task_index": idx,
        "title": "",
        "description": "",
        "success_criteria": "",
        "rating": None,
        "player_self_assessment": None,
        "player_task_summary": None,
        "master_input_images": [],
        "master_trace": [],
        "master_model": "",
        "master_cost": 0,
        "turns": [],
    }


def _group_trace_into_steps(trace: list[dict]) -> dict:
    """Parse the flat trace into structured steps.

    Returns:
        {
            "system_prompt": str,
            "user_input": str,
            "steps": [
                {
                    "type": "tool_call" | "final_result",
                    "thinking": str | None,
                    "tool_name": str,
                    "args": dict | str,
                    "response": str | None,
                }
            ]
        }
    """
    result = {
        "system_prompt": "",
        "user_input": "",
        "steps": [],
    }

    # Collect pending thinking blocks that precede a tool call
    pending_thinking = []

    for msg in trace:
        role = msg.get("role", "")

        if role == "system":
            result["system_prompt"] = msg.get("content", "")

        elif role == "user":
            result["user_input"] = msg.get("content", "")

        elif role == "thinking":
            pending_thinking.append(msg.get("content", ""))

        elif role == "tool_call":
            step = {
                "type": "final_result" if msg.get("tool_name") == "final_result" else "tool_call",
                "thinking": "\n\n---\n\n".join(pending_thinking) if pending_thinking else None,
                "tool_name": msg.get("tool_name", ""),
                "args": msg.get("args", ""),
                "response": None,
            }
            pending_thinking = []
            result["steps"].append(step)

        elif role == "tool_result":
            tool_name = msg.get("tool_name", "")
            content = msg.get("content", "")
            # Skip noise from final_result
            if tool_name == "final_result":
                continue
            # Attach to most recent matching step
            for step in reversed(result["steps"]):
                if step["tool_name"] == tool_name and step["response"] is None:
                    step["response"] = content
                    break

        elif role == "assistant":
            # Standalone assistant text (rare, but handle it)
            if msg.get("content", "").strip():
                result["steps"].append({
                    "type": "assistant",
                    "thinking": None,
                    "tool_name": "",
                    "args": msg.get("content", ""),
                    "response": None,
                })

        elif role == "retry":
            result["steps"].append({
                "type": "retry",
                "thinking": None,
                "tool_name": "retry",
                "args": msg.get("content", ""),
                "response": None,
            })

    # If there's leftover thinking with no tool call, attach it to a note
    if pending_thinking:
        result["steps"].append({
            "type": "thinking_only",
            "thinking": "\n\n---\n\n".join(pending_thinking),
            "tool_name": "",
            "args": "",
            "response": None,
        })

    return result


def _format_action(action) -> str:
    """Format an action for display. Handles both old str and new list format."""
    if isinstance(action, list):
        return "[" + ", ".join(str(a) for a in action) + "]"
    return str(action)
