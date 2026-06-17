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
from pathlib import Path


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


def _project_turn(turn: dict) -> dict:
    """Project one per-turn dict (from ``group_events_by_turn``) for the SPA."""
    exp = turn.get("explanation") or {}
    usage = turn.get("usage") or {}
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
        "trace": _trace_steps(turn.get("trace", [])),
    }


def report_format_action(action) -> str:
    """Thin reuse wrapper over ``event_parsing._format_action`` (list vs str)."""
    from src.core import event_parsing

    return event_parsing._format_action(action if action is not None else "?")


def build_run_trace(run_dir: Path) -> dict:
    """Build the task-grouped trace JSON for a finished run (Round 8 B1+B2).

    Reuses ``event_parsing``'s grouping verbatim. For a TaskMaster run, returns
    one entry per task group (master decision + nested player turns). For a
    casual / non-TaskMaster run (no ``task_started`` events), returns a SINGLE
    implicit group holding all turns — a degenerate-but-valid shape so the SPA
    never 500s.
    """
    from src.core import event_parsing

    events = event_parsing.load_events(run_dir)
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

    return {
        "run_id": run_dir.name,
        "has_tasks": has_tasks,
        "task_count": len(tasks_out),
        "turn_count": len(turns),
        "tasks": tasks_out,
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
