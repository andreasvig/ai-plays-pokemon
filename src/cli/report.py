"""Report generator: creates an interactive HTML report from a run folder.

Usage:
    pokemon report local/runs/2026-04-06_21-34-37_<run>
    pokemon report                 # auto-picks latest run
"""

import argparse
import base64
import json
import sys
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


def image_to_base64(path: str, run_dir: Path = None) -> str:
    """Convert an image file to base64 data URI.

    Tries the path as-is, then relative to run_dir's screenshots folder,
    then with a local/ prefix (for runs moved from runs/ to local/runs/).
    """
    candidates = [Path(path)]
    if run_dir:
        candidates.append(run_dir / "screenshots" / Path(path).name)
    # Handle old paths that start with runs/ but files now live in local/runs/
    if path.startswith("runs/"):
        candidates.append(Path("local") / path)

    for p in candidates:
        if p.exists():
            with open(p, "rb") as f:
                data = base64.b64encode(f.read()).decode("utf-8")
            return f"data:image/png;base64,{data}"
    return ""


def group_events_by_turn(events: list[dict]) -> list[dict]:
    """Group events into turns."""
    turns = []
    current_turn = None

    for event in events:
        if event["type"] == "turn_start":
            if current_turn:
                turns.append(current_turn)
            current_turn = {
                "turn": event.get("turn", len(turns) + 1),
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


def _render_trace_html(
    trace: list[dict],
    input_images: list[dict] | None = None,
    skip_final_result: bool = False,
) -> str:
    """Render the trace into grouped, collapsible HTML.

    ``input_images`` (TaskMaster only) are the actual screenshots this agent
    saw; they render inside the Input step so the trace shows what it looked at.

    ``skip_final_result`` (TaskMaster only): the master's final_result is the
    TaskSpec (next task + rating of the previous task), already surfaced as the
    task header + the verdict row — so its raw "Output" step is dropped here.
    """
    grouped = _group_trace_into_steps(trace)

    parts = []

    # --- System prompt (collapsible, collapsed by default) ---
    if grouped["system_prompt"]:
        parts.append(f"""
        <details class="trace-step trace-system">
            <summary><span class="step-label">System Prompt</span></summary>
            <pre class="step-content">{_escape(grouped["system_prompt"])}</pre>
        </details>""")

    # --- User input (collapsible, collapsed by default, with preview) ---
    # The screenshots the agent actually saw render here, under the Input tab.
    imgs_html = ""
    if input_images:
        cells = "".join(
            f'<figure class="master-thumb"><img src="{img.get("data_url", "")}" '
            f'alt="{_escape(img.get("label", ""))}"/>'
            f'<figcaption>{_escape(img.get("label", ""))}</figcaption></figure>'
            for img in input_images if img.get("data_url")
        )
        if cells:
            imgs_html = f'<div class="master-thumbs">{cells}</div>'
    if grouped["user_input"] or imgs_html:
        preview = (grouped["user_input"] or "")[:100].replace("\n", " ")
        parts.append(f"""
        <details class="trace-step trace-input">
            <summary>
                <span class="step-label">Input</span>
                <span class="step-preview">{_escape(preview)}...</span>
            </summary>
            {imgs_html}
            <pre class="step-content">{_escape(grouped["user_input"] or "")}</pre>
        </details>""")

    # --- Steps: tool calls and final result ---
    step_num = 0
    for step in grouped["steps"]:
        step_num += 1

        if step["type"] == "tool_call":
            tool_name = step["tool_name"]
            args = step["args"]
            args_str = json.dumps(args, indent=2) if isinstance(args, dict) else str(args)
            resp_str = str(step["response"] or "")

            # Build inner content
            inner = ""
            if step["thinking"]:
                inner += f'<div class="step-thinking"><div class="thinking-label">Thinking</div><pre>{_escape(step["thinking"])}</pre></div>'
            inner += f'<div class="step-call"><div class="call-label">Call</div><pre>{_escape(tool_name)}({_escape(args_str)})</pre></div>'
            if resp_str:
                inner += f'<div class="step-response"><div class="response-label">Response</div><pre>{_escape(resp_str)}</pre></div>'

            # Summary line
            if isinstance(args, dict):
                args_preview = ", ".join(f"{k}=..." for k in list(args.keys())[:3])
            else:
                args_preview = str(args)[:60]

            parts.append(f"""
            <details class="trace-step trace-tool">
                <summary>
                    <span class="step-label">Tool</span>
                    <span class="step-tool-name">{_escape(tool_name)}</span>
                    <span class="step-preview">({_escape(args_preview)})</span>
                </summary>
                <div class="step-body">{inner}</div>
            </details>""")

        elif step["type"] == "final_result":
            # Master block: its final_result is the TaskSpec (next task + rating of
            # the previous task), already shown as the task header + the verdict row
            # below. The TaskSpec carries a `reasoning` key, so the player-template
            # branch below would render a nonsensical "Output ? / Last turn / Action"
            # step. Skip it — keep only the planning thinking (parity with live).
            if skip_final_result:
                if step["thinking"]:
                    parts.append(f"""
            <details class="trace-step trace-thinking-only">
                <summary><span class="step-label">Thinking</span></summary>
                <pre class="step-content">{_escape(step["thinking"])}</pre>
            </details>""")
                continue
            args = step["args"]
            parsed = args if isinstance(args, dict) else _try_parse_json(args)

            inner = ""
            if step["thinking"]:
                inner += f'<div class="step-thinking"><div class="thinking-label">Thinking</div><pre>{_escape(step["thinking"])}</pre></div>'

            if isinstance(parsed, dict) and ("reasoning" in parsed or "last_turn_succeeded" in parsed):
                memory_html = ""
                mem_raw = parsed.get('memory_updates', '')
                if mem_raw and str(mem_raw).strip().lower() != 'none':
                    # Try to pretty-print JSON
                    try:
                        mem_obj = json.loads(mem_raw) if isinstance(mem_raw, str) else mem_raw
                        mem_display = json.dumps(mem_obj, indent=2)
                    except (json.JSONDecodeError, TypeError):
                        mem_display = str(mem_raw)
                    memory_html = f'<div class="decision-row"><strong>Memory Update:</strong> <pre style="display:inline-block;margin:4px 0;background:#1a2e1a;padding:4px 8px;border-radius:4px;font-size:12px;">{_escape(mem_display)}</pre></div>'
                grade = parsed.get('last_turn_succeeded')
                if grade is True:
                    grade_label = "✅ succeeded"
                elif grade is False:
                    grade_label = "❌ failed"
                elif grade is None:
                    grade_label = "➖ n/a (first turn)"
                else:
                    grade_label = "(missing)"
                # If the Player handed control back to TaskMaster this turn, show
                # the return block (self-assessment + summary) instead of an empty
                # action — this is the "final output" of the Player on the task.
                handback = parsed.get('return_to_taskmaster')
                if isinstance(handback, dict):
                    action_row = (
                        '<div class="decision-row"><strong>↩️ Return to TaskMaster:</strong> '
                        f'<strong>{_escape(str(handback.get("self_assessment", "")).capitalize())}</strong> — '
                        f'{_escape(handback.get("task_summary", ""))}</div>'
                    )
                else:
                    action_row = (
                        '<div class="decision-row"><strong>Action:</strong> '
                        f'<code>{_escape(_format_action(parsed.get("inputs", "")))}</code></div>'
                    )
                inner += f"""<div class="step-decision">
                    <div class="decision-row"><strong>Last turn:</strong> {grade_label}</div>
                    <div class="decision-row"><strong>Reasoning:</strong> {_escape(parsed.get('reasoning', ''))}</div>
                    {action_row}
                    {memory_html}
                </div>"""
            else:
                args_str = json.dumps(parsed, indent=2) if isinstance(parsed, dict) else str(args)
                inner += f'<div class="step-call"><pre>{_escape(args_str)}</pre></div>'

            action_preview = _format_action(parsed.get("inputs", "?")) if isinstance(parsed, dict) else "?"
            parts.append(f"""
            <details class="trace-step trace-output">
                <summary>
                    <span class="step-label">Output</span>
                    <span class="step-action-code">{_escape(str(action_preview))}</span>
                </summary>
                <div class="step-body">{inner}</div>
            </details>""")

        elif step["type"] == "retry":
            parts.append(f"""
            <div class="trace-step trace-retry-step">
                <span class="step-label">Retry</span>
                <pre>{_escape(str(step["args"]))}</pre>
            </div>""")

        elif step["type"] == "thinking_only":
            parts.append(f"""
            <details class="trace-step trace-thinking-only">
                <summary><span class="step-label">Thinking</span></summary>
                <pre class="step-content">{_escape(step["thinking"])}</pre>
            </details>""")

    return "\n".join(parts)


# Badge pill metadata, keyed by task_completed rating status. ``None`` → current
# task (no rating yet). Colors mirror Decision 7 / Phase 2 design tokens.
_TASK_BADGES = {
    "succeeded": ("✅", "succeeded", "badge-succeeded"),
    "failed": ("❌", "failed", "badge-failed"),
    "partial": ("🟡", "partial", "badge-partial"),
    "other": ("➖", "other", "badge-other"),
}


def _task_badge_html(rating: dict | None) -> str:
    """Render the grade badge pill for a task group header."""
    if not rating:
        return '<span class="task-badge badge-current">⏳ current</span>'
    status = (rating.get("status") or "").lower()
    icon, label, cls = _TASK_BADGES.get(status, ("➖", status or "?", "badge-other"))
    return f'<span class="task-badge {cls}">{icon} {_escape(label)}</span>'


def _render_turn_card_html(turn: dict, label: str, run_dir: Path) -> str:
    """Render a single player turn card. ``label`` is the displayed turn number
    (``Turn 5`` flat, or ``Turn 2.3`` nested under a task)."""
    turn_num = turn["turn"]
    exp = turn.get("explanation") or {}
    action = _format_action(turn.get("action", "?"))

    # Screenshot
    screenshot_html = ""
    if turn["screenshot"]:
        b64 = image_to_base64(turn["screenshot"], run_dir)
        if b64:
            screenshot_html = f'<img src="{b64}" class="screenshot" />'
        else:
            screenshot_html = '<span class="no-screenshot">Screenshot not found</span>'

    # Structured trace
    trace_html = ""
    trace = turn.get("trace", [])
    if trace:
        trace_content = _render_trace_html(trace)
        n_tools = sum(1 for s in _group_trace_into_steps(trace)["steps"] if s["type"] == "tool_call")
        trace_html = f"""
        <div class="trace-section">
            <div class="trace-header">Trace ({n_tools} tool call{'s' if n_tools != 1 else ''})</div>
            <div class="trace-container">{trace_content}</div>
        </div>"""

    # Usage info
    usage_html = ""
    usage = turn.get("usage", {})
    if usage:
        cost = usage.get("cost_usd")
        cost_str = f" | ${cost:.4f}" if cost else ""
        usage_html = f'<div class="usage">{usage.get("request_tokens", "?")} in / {usage.get("response_tokens", "?")} out{cost_str}</div>'

    # Extract screen settle duration from events
    settle_html = ""
    for evt in turn.get("events", []):
        if evt.get("type") == "screen_settled":
            dur = evt.get("duration", 0) or evt.get("data", {}).get("duration", 0)
            settle_html = f'<span class="settle-time">⏱ {dur}s</span>'

    # Last-turn-succeeded label (rendered inside the explanation block)
    _grade = exp.get('last_turn_succeeded')
    if _grade is True:
        grade_label = "✅ succeeded"
    elif _grade is False:
        grade_label = "❌ failed"
    elif _grade is None:
        grade_label = "➖ n/a (first turn)"
    else:
        grade_label = "(missing)"

    # Raw events (collapsed)
    raw_events_json = json.dumps(turn["events"], indent=2, default=str)

    return f"""
    <div class="turn" id="turn-{turn_num}">
        <div class="turn-header" onclick="this.parentElement.classList.toggle('collapsed')">
            <span class="turn-number">{_escape(label)}</span>
            <span class="turn-action"><code>{_escape(action)}</code></span>
            <span class="turn-summary">{_escape(exp.get('reasoning', '')[:100])}</span>
            {settle_html}
            {usage_html}
        </div>
        <div class="turn-body">
            <div class="turn-columns">
                <div class="turn-left">
                    {screenshot_html}
                </div>
                <div class="turn-right">
                    <div class="explanation">
                        <div class="exp-row"><strong>Last turn:</strong> {grade_label}</div>
                        <div class="exp-row"><strong>Reasoning:</strong> {_escape(exp.get('reasoning', ''))}</div>
                        {_render_memory_update_html(exp)}
                    </div>
                </div>
            </div>
            {_render_ocr_html(turn)}
            {trace_html}
            <details class="raw-events">
                <summary>Raw Events ({len(turn['events'])})</summary>
                <pre>{_escape(raw_events_json[:10000])}</pre>
            </details>
        </div>
    </div>
    """


def _master_rating_of_previous(trace: list[dict]) -> dict | None:
    """Pull this invocation's own ``rating_of_previous_task`` out of its trace.

    This is the chronologically-honest "result of the previous task" — the
    verdict the master produced AT this invocation — as opposed to the badge,
    which is the current task's verdict (backfilled from the next invocation).
    """
    for msg in trace or []:
        name = msg.get("tool_name")
        role = msg.get("role") or msg.get("kind")
        if name == "final_result" or role == "final_result":
            args = msg.get("args")
            parsed = args if isinstance(args, dict) else _try_parse_json(args)
            if isinstance(parsed, dict):
                rop = parsed.get("rating_of_previous_task")
                if isinstance(rop, dict):
                    return rop
    return None


def _render_master_block_html(group: dict, run_dir: Path) -> str:
    """Render the TaskMaster detail block CHRONOLOGICALLY: its trace (with the
    screenshots it saw under the Input tab) first, then its OUTPUT card at the
    bottom — the task it set (title + plan + success criteria) plus its rating of
    the PREVIOUS task. Mirrors the live dashboard. The current task's own verdict
    is NOT backfilled here (that belongs to the next invocation); it lives only as
    the badge on the task header."""
    trace = group.get("master_trace") or []

    # The master's own output: the result of the PREVIOUS task it just judged.
    # Null on the cold-start invocation (there is no previous task).
    rop = _master_rating_of_previous(trace)
    verdict_rows = ""
    if rop:
        status = (rop.get("status") or "").lower()
        icon, label, _cls = _TASK_BADGES.get(status, ("➖", status or "?", "badge-other"))
        verdict_rows = (
            f'<div class="decision-row"><strong>⚖️ Rating of the previous task:</strong> '
            f'{icon} {_escape(label)}</div>'
        )
        if rop.get("reasoning"):
            verdict_rows += (
                f'<div class="decision-row"><strong>Reasoning:</strong> '
                f'{_escape(rop.get("reasoning", ""))}</div>'
            )
    else:
        verdict_rows = (
            '<div class="decision-row" style="color:#888;">First task — no previous '
            'task to rate.</div>'
        )

    trace_html = ""
    if trace:
        trace_content = _render_trace_html(
            trace, group.get("master_input_images"), skip_final_result=True
        )
        n_tools = sum(1 for s in _group_trace_into_steps(trace)["steps"] if s["type"] == "tool_call")
        trace_html = f"""
        <div class="trace-section">
            <div class="trace-header">TaskMaster trace ({n_tools} tool call{'s' if n_tools != 1 else ''})</div>
            <div class="trace-container">{trace_content}</div>
        </div>"""

    # The master's OUTPUT card at the BOTTOM (chronological), mirroring the live
    # dashboard: the task it set (title + plan + success criteria), then its rating
    # of the previous task. The task header above is the collapsed-nav label.
    title = group.get("title", "")
    description = group.get("description", "")
    criteria = group.get("success_criteria", "")
    output_rows = ""
    if title:
        output_rows += (
            f'<div class="decision-row"><strong>📋 Task:</strong> {_escape(title)}</div>'
        )
    if description:
        output_rows += (
            '<div class="decision-row"><strong>🧭 Plan:</strong></div>'
            f'<div class="master-output-desc">{_escape(description)}</div>'
        )
    if criteria:
        output_rows += (
            '<div class="decision-row"><strong>🎯 Success criteria:</strong> '
            f'{_escape(criteria)}</div>'
        )
    output_rows += verdict_rows

    return f"""
    <div class="master-block">
        <div class="master-label">TaskMaster</div>
        {trace_html}
        <div class="master-verdict master-output">{output_rows}</div>
    </div>
    """


def _render_task_group_html(group: dict, run_dir: Path) -> str:
    """Render one collapsible task group: an always-visible header (task label,
    title, grade badge, turn count, cost, and the full description) plus a body
    (master detail block + nested player turn cards). Renders collapsed."""
    idx = group["task_index"]
    title = group.get("title", "")
    description = group.get("description", "")
    n_turns = len(group["turns"])

    # Task cost = sum of player-turn costs + master cost.
    player_cost = 0.0
    for turn in group["turns"]:
        usage = turn.get("usage") or {}
        c = usage.get("cost_usd")
        if c:
            player_cost += c
    task_cost = player_cost + (group.get("master_cost") or 0)

    badge = _task_badge_html(group.get("rating"))

    # Nested player turn cards, relabeled Turn N.M (M = position within group).
    turns_inner = "\n".join(
        _render_turn_card_html(turn, f"Turn {idx}.{m}", run_dir)
        for m, turn in enumerate(group["turns"], start=1)
    )

    return f"""
    <div class="task-group collapsed" data-task="{idx}">
        <div class="task-header" onclick="this.parentElement.classList.toggle('collapsed')">
            <div class="task-header-main">
                <span class="task-arrow">▶</span>
                <span class="task-number">Task {idx} (Master)</span>
                <span class="task-title">{_escape(title)}</span>
                {badge}
                <span class="task-count">{n_turns} turn{'s' if n_turns != 1 else ''}</span>
                <span class="task-cost">${task_cost:.4f}</span>
            </div>
            <div class="task-description">{_escape(description)}</div>
        </div>
        <div class="task-body">
            {_render_master_block_html(group, run_dir)}
            <div class="task-turns">
                {turns_inner}
            </div>
        </div>
    </div>
    """


def _render_referee_html(summary: dict) -> str:
    """Top-of-report benchmark-gate scorecard.

    Renders only when the run carried a referee (``summary["referee"]``):
    a headline verdict (failed-at-gate / reached-furthest / all-cleared), then a
    table of every gate with its status (done / auto / skipped / pending), the
    turn it was reached, and its limit. Returns "" for non-benchmark runs.
    """
    ref = summary.get("referee")
    if not ref or not ref.get("gates"):
        return ""
    gates = ref["gates"]
    total = len(gates)
    cleared = sum(1 for g in gates if g["status"] in ("done", "auto"))
    by_id = {g["id"]: g for g in gates}

    tr = ref.get("termination_reason")
    if tr and tr.startswith("missed_gate:"):
        fid = tr.split(":", 1)[1]
        fg = by_id.get(fid, {})
        dl = fg.get("deadline_turn")
        headline = (
            '<span class="ref-verdict failed">✗ Failed</span> missed '
            f'<strong>{_escape(fg.get("name", fid))}</strong>'
            + (f" (limit T{dl})" if dl is not None else "")
        )
    else:
        furthest = ref.get("furthest")
        fu = ref.get("first_unmet")
        if furthest is None:
            headline = '<span class="ref-verdict none">No gates reached</span>'
        elif fu is None:
            headline = '<span class="ref-verdict cleared">🏁 All gates cleared</span>'
        else:
            fname = by_id.get(furthest, {}).get("name", furthest)
            uname = by_id.get(fu, {}).get("name", fu)
            headline = (
                '<span class="ref-verdict stopped">Reached</span> '
                f"<strong>{_escape(fname)}</strong> — stopped before "
                f"<strong>{_escape(uname)}</strong>"
            )

    status_cell = {
        "done": '<span class="ref-st done">✓ done</span>',
        "auto": '<span class="ref-st auto">⤴ auto</span>',
        "skipped": '<span class="ref-st skipped">✗ skipped</span>',
        "pending": '<span class="ref-st pending">·</span>',
    }
    rows = []
    for g in gates:
        turn_str = f"T{g['turn']}" if g["turn"] is not None else "—"
        limit_str = f"T{g['deadline_turn']}" if g["deadline_turn"] is not None else "—"
        rows.append(
            f'<tr class="ref-row {g["status"]}">'
            f'<td>{_escape(g["name"])}</td>'
            f'<td>{status_cell.get(g["status"], g["status"])}</td>'
            f"<td>{turn_str}</td><td>{limit_str}</td></tr>"
        )

    auto_note = ""
    n_auto = len(ref.get("autofilled") or [])
    if n_auto:
        auto_note = (
            f'<div class="ref-note">⤴ {n_auto} gate{"s" if n_auto != 1 else ""} '
            "auto-credited by the safety net (a later gate was reached within the "
            "skipped gate's limit).</div>"
        )

    return f"""
    <div class="referee-report">
        <div class="ref-head">
            <span class="ref-title">🏁 Benchmark Gates</span>
            <span class="ref-score">{cleared}/{total} cleared</span>
            <span class="ref-headline">{headline}</span>
        </div>
        <table class="ref-table">
            <thead><tr><th>Gate</th><th>Status</th><th>Reached</th><th>Limit</th></tr></thead>
            <tbody>{''.join(rows)}</tbody>
        </table>
        {auto_note}
    </div>"""


def generate_html(run_dir: Path, events: list[dict], turns: list[dict]) -> str:
    """Generate the full HTML report."""
    config = {}
    config_file = run_dir / "config.json"
    if config_file.exists():
        with open(config_file) as f:
            config = json.load(f)

    # Load run summary if available
    summary = {}
    summary_file = run_dir / "run_summary.json"
    if summary_file.exists():
        with open(summary_file) as f:
            summary = json.load(f)

    run_name = run_dir.name
    task = config.get("task", {}).get("goal", "")

    # Cost info from summary
    cost_info = summary.get("cost", {})
    total_cost = cost_info.get("total_usd", 0)
    total_input = cost_info.get("total_input_tokens", 0)
    total_output = cost_info.get("total_output_tokens", 0)
    duration = summary.get("session", {}).get("duration_seconds", 0)

    # --- Benchmark gate scorecard (top of report) ---
    referee_html = _render_referee_html(summary)

    # --- Task goal section ---
    task_html = ""
    if task:
        task_html = f"""
        <div class="task-section">
            <div class="task-goal">{_escape(task)}</div>
        </div>"""

    # --- Build turn / task-group HTML ---
    # Detect TaskMaster runs: if any task_started event exists, render the
    # two-level task tree. Otherwise fall back to the flat turn list (legacy).
    has_tasks = any(e.get("type") == "task_started" for e in events)

    if has_tasks:
        task_groups = group_turns_by_task(turns, events)
        turns_html = "\n".join(
            _render_task_group_html(group, run_dir) for group in task_groups
        )
    else:
        turns_html = "\n".join(
            _render_turn_card_html(turn, f"Turn {turn['turn']}", run_dir)
            for turn in turns
        )

    # Event type summary
    type_counts = {}
    for e in events:
        t = e["type"]
        type_counts[t] = type_counts.get(t, 0) + 1
    stats_html = " | ".join(f"{k}: {v}" for k, v in sorted(type_counts.items()))

    # Cost summary
    cost_summary = ""
    if total_cost > 0:
        llm_cost = cost_info.get("llm_usd", 0)
        ocr_cost = cost_info.get("ocr_usd", 0)
        breakdown_bits = []
        if llm_cost:
            breakdown_bits.append(f"LLM ${llm_cost:.4f}")
        if ocr_cost:
            breakdown_bits.append(f"OCR ${ocr_cost:.5f}")
        breakdown = f" ({' + '.join(breakdown_bits)})" if breakdown_bits else ""
        cost_summary = f'<span><span class="label">Cost:</span> ${total_cost:.4f}{breakdown}</span>'

    duration_str = ""
    if duration:
        m, s = divmod(int(duration), 60)
        duration_str = f'<span><span class="label">Duration:</span> {m}m {s}s</span>'

    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Run Report: {run_name}</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #1a1a2e; color: #e0e0e0; padding: 20px; max-width: 1200px; margin: 0 auto; }}
        h1 {{ color: #e94560; margin-bottom: 10px; }}
        .meta {{ background: #16213e; padding: 15px; border-radius: 8px; margin-bottom: 12px; font-size: 14px; display: flex; flex-wrap: wrap; gap: 8px 20px; }}
        .meta span {{ white-space: nowrap; }}
        .meta .label {{ color: #888; }}
        .stats {{ background: #16213e; padding: 10px 15px; border-radius: 8px; margin-bottom: 20px; font-size: 12px; color: #888; }}

        /* Benchmark gate scorecard (top of report) */
        .referee-report {{ background: #16213e; border-radius: 8px; padding: 16px 20px; margin-bottom: 20px; border-left: 4px solid #53d8fb; }}
        .ref-head {{ display: flex; align-items: center; gap: 14px; flex-wrap: wrap; margin-bottom: 10px; }}
        .ref-title {{ font-size: 16px; font-weight: bold; color: #53d8fb; }}
        .ref-score {{ font-size: 13px; color: #aaa; }}
        .ref-headline {{ font-size: 14px; color: #ddd; }}
        .ref-verdict {{ font-weight: 700; padding: 2px 8px; border-radius: 999px; font-size: 12px; }}
        .ref-verdict.failed {{ background: #e94560; color: #fff; }}
        .ref-verdict.stopped {{ background: #ffce54; color: #16213e; }}
        .ref-verdict.cleared {{ background: #7ddf64; color: #16213e; }}
        .ref-verdict.none {{ background: #555; color: #ddd; }}
        .ref-table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
        .ref-table th {{ text-align: left; color: #888; font-weight: normal; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; padding: 4px 8px; border-bottom: 1px solid #0f3460; }}
        .ref-table td {{ padding: 4px 8px; border-bottom: 1px solid rgba(15,52,96,0.4); }}
        .ref-table th:nth-child(n+3), .ref-table td:nth-child(n+3) {{ text-align: right; white-space: nowrap; color: #aaa; }}
        .ref-row.done td:first-child {{ color: #7ddf64; }}
        .ref-row.auto td:first-child {{ color: #53d8fb; }}
        .ref-row.skipped td:first-child {{ color: #e94560; }}
        .ref-row.pending td:first-child {{ color: #888; }}
        .ref-st {{ font-size: 11px; font-weight: 600; }}
        .ref-st.done {{ color: #7ddf64; }}
        .ref-st.auto {{ color: #53d8fb; }}
        .ref-st.skipped {{ color: #e94560; }}
        .ref-st.pending {{ color: #666; }}
        .ref-note {{ margin-top: 8px; font-size: 12px; color: #53d8fb; }}

        /* Task section */
        .task-section {{ background: #0f3460; border-radius: 8px; padding: 16px 20px; margin-bottom: 20px; border-left: 4px solid #e94560; }}
        .task-goal {{ font-size: 20px; font-weight: bold; color: #fff; }}
        .task-subgoals {{ margin-top: 12px; padding-left: 0; list-style: none; }}
        .task-subgoals li {{ padding: 4px 0 4px 20px; position: relative; color: #bbb; font-size: 14px; }}
        .task-subgoals li::before {{ content: ''; position: absolute; left: 0; top: 11px; width: 12px; height: 12px; border-radius: 3px; border: 2px solid #53d8fb; }}

        /* ── TaskMaster two-level tree (Decision 7 design tokens) ──
           amber/gold master accent #ffce54 · current grey #999 · green #7ddf64
           failed/player-red #e94560 · partial amber #ffce54 · player guide #2a3a5a */
        .task-group {{ background: #16213e; border-radius: 8px; margin-bottom: 16px; overflow: hidden; border-left: 4px solid #ffce54; }}
        .task-header {{ padding: 12px 16px; cursor: pointer; background: #2b2614; }}
        .task-header:hover {{ background: #342d18; }}
        .task-header-main {{ display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }}
        .task-arrow {{ color: #ffce54; font-size: 11px; transition: transform 0.15s; display: inline-block; }}
        .task-group:not(.collapsed) .task-arrow {{ transform: rotate(90deg); }}
        .task-number {{ font-weight: bold; color: #ffce54; white-space: nowrap; }}
        .task-title {{ font-weight: bold; color: #fff; }}
        .task-count {{ color: #aaa; font-size: 13px; white-space: nowrap; }}
        .task-cost {{ color: #aaa; font-size: 13px; white-space: nowrap; margin-left: auto; }}
        .task-description {{ color: #bbb; font-size: 13px; margin-top: 6px; line-height: 1.5; white-space: normal; word-break: break-word; }}

        /* Grade badge pills */
        .task-badge {{ font-size: 12px; font-weight: 700; padding: 2px 10px; border-radius: 999px; white-space: nowrap; color: #16213e; }}
        .badge-succeeded {{ background: #7ddf64; }}
        .badge-failed {{ background: #e94560; color: #fff; }}
        .badge-partial {{ background: #ffce54; }}
        .badge-current {{ background: #999; color: #16213e; }}
        .badge-other {{ background: #555; color: #ddd; }}

        .task-group.collapsed .task-body {{ display: none; }}
        .task-body {{ padding: 14px 16px; }}

        /* Master detail block — distinct amber strategy layer */
        .master-block {{ background: #211d0f; border-left: 3px solid #ffce54; border-radius: 6px; padding: 12px 14px; margin-bottom: 12px; }}
        .master-label {{ font-size: 11px; text-transform: uppercase; letter-spacing: 1px; color: #ffce54; font-weight: 700; margin-bottom: 8px; }}
        .master-verdict {{ line-height: 1.7; margin-top: 10px; padding-top: 8px; border-top: 1px solid #3a3320; }}
        .master-verdict .decision-row {{ margin-bottom: 4px; }}
        .master-verdict .decision-row strong {{ color: #ffce54; }}
        .master-verdict .decision-row.player-handback {{ padding-bottom: 6px; margin-bottom: 6px; border-bottom: 1px dashed #3a3320; }}
        .master-verdict .decision-row.player-handback strong {{ color: #6ca4ff; }}
        .master-output-desc {{ white-space: pre-wrap; margin: 2px 0 8px; color: #d8d8d8; line-height: 1.6; }}
        .master-thumbs {{ display: flex; gap: 12px; margin-bottom: 10px; flex-wrap: wrap; }}
        .master-thumb {{ margin: 0; text-align: center; }}
        .master-thumb img {{ width: 160px; image-rendering: pixelated; border: 1px solid #3a3320; border-radius: 4px; display: block; }}
        .master-thumb figcaption {{ font-size: 10px; color: #ffce54; text-transform: uppercase; letter-spacing: 0.5px; margin-top: 3px; }}

        /* Nested player turns — indented under the task with a guide line */
        .task-turns {{ margin-left: 24px; border-left: 2px solid #2a3a5a; padding-left: 12px; }}
        .task-turns .turn {{ margin-bottom: 10px; }}

        /* Turn cards */
        .turn {{ background: #16213e; border-radius: 8px; margin-bottom: 12px; overflow: hidden; border-left: 4px solid #e94560; }}
        .turn-header {{ padding: 12px 15px; cursor: pointer; display: flex; gap: 12px; align-items: center; background: #1a1a3e; }}
        .turn-header:hover {{ background: #1f1f4e; }}
        .turn-number {{ font-weight: bold; color: #e94560; min-width: 55px; }}
        .turn-action {{ min-width: 80px; }}
        .turn-action code {{ background: #0f3460; padding: 2px 8px; border-radius: 4px; color: #53d8fb; font-size: 13px; }}
        .turn-summary {{ color: #aaa; font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }}
        .turn.collapsed .turn-body {{ display: none; }}
        .turn-body {{ padding: 15px; }}

        .turn-columns {{ display: flex; gap: 20px; margin-bottom: 12px; }}
        .turn-left {{ flex: 0 0 auto; }}
        .turn-right {{ flex: 1; min-width: 0; }}

        .screenshot {{ max-width: 360px; border-radius: 6px; border: 2px solid #333; image-rendering: pixelated; }}
        .no-screenshot {{ color: #888; font-style: italic; }}

        .explanation {{ background: #0f3460; padding: 12px; border-radius: 6px; }}
        .exp-row {{ margin-bottom: 6px; line-height: 1.5; }}
        .exp-row strong {{ color: #53d8fb; }}

        /* Trace section */
        .trace-section {{ margin-top: 8px; }}
        .trace-header {{ font-size: 12px; color: #888; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 8px; padding-bottom: 4px; border-bottom: 1px solid #333; }}
        .trace-container {{ display: flex; flex-direction: column; gap: 4px; }}

        /* Trace steps - shared */
        .trace-step {{ border-radius: 6px; overflow: hidden; }}
        .trace-step > summary {{ cursor: pointer; padding: 8px 12px; display: flex; align-items: center; gap: 8px; list-style: none; }}
        .trace-step > summary::-webkit-details-marker {{ display: none; }}
        .trace-step > summary::before {{ content: '\\25B6'; font-size: 9px; color: #666; transition: transform 0.15s; flex-shrink: 0; }}
        .trace-step[open] > summary::before {{ transform: rotate(90deg); }}
        .step-label {{ font-weight: 700; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; min-width: 55px; }}
        .step-preview {{ color: #666; font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
        .step-content {{ padding: 0 12px 12px; font-size: 12px; max-height: 400px; overflow-y: auto; white-space: pre-wrap; word-break: break-word; }}
        .step-body {{ padding: 4px 12px 12px; }}

        /* System prompt step */
        .trace-system {{ background: #1a1a3e; }}
        .trace-system .step-label {{ color: #888; }}

        /* Input step */
        .trace-input {{ background: #0f2a4a; }}
        .trace-input .step-label {{ color: #53d8fb; }}

        /* Tool call step */
        .trace-tool {{ background: #1e1a2e; }}
        .trace-tool .step-label {{ color: #e994ff; }}
        .step-tool-name {{ color: #ffd700; font-family: 'SF Mono', monospace; font-size: 13px; }}

        /* Output / final result step */
        .trace-output {{ background: #1a2e1a; border-left: 3px solid #7ddf64; }}
        .trace-output .step-label {{ color: #7ddf64; }}
        .step-action-code {{ color: #e94560; font-family: 'SF Mono', monospace; font-weight: bold; font-size: 14px; }}

        /* Retry step */
        .trace-retry-step {{ background: #2e1a1a; padding: 8px 12px; }}
        .trace-retry-step .step-label {{ color: #ff6b6b; font-weight: 700; font-size: 11px; text-transform: uppercase; }}
        .trace-retry-step pre {{ font-size: 12px; margin-top: 4px; }}

        /* Thinking only */
        .trace-thinking-only {{ background: #2e2a1a; }}
        .trace-thinking-only .step-label {{ color: #ffd700; }}

        /* Inner step parts */
        .step-thinking {{ background: #2a2515; border-left: 3px solid #ffd700; border-radius: 4px; padding: 8px 12px; margin-bottom: 8px; }}
        .thinking-label {{ font-size: 10px; text-transform: uppercase; color: #ffd700; letter-spacing: 1px; margin-bottom: 4px; }}
        .step-thinking pre {{ font-size: 12px; max-height: 300px; overflow-y: auto; white-space: pre-wrap; word-break: break-word; color: #ddd; }}

        .step-call {{ background: #1a1530; border-radius: 4px; padding: 8px 12px; margin-bottom: 8px; }}
        .call-label {{ font-size: 10px; text-transform: uppercase; color: #e994ff; letter-spacing: 1px; margin-bottom: 4px; }}
        .step-call pre {{ font-size: 12px; max-height: 200px; overflow-y: auto; white-space: pre-wrap; word-break: break-word; }}

        .step-response {{ background: #152025; border-radius: 4px; padding: 8px 12px; }}
        .response-label {{ font-size: 10px; text-transform: uppercase; color: #94ffe9; letter-spacing: 1px; margin-bottom: 4px; }}
        .step-response pre {{ font-size: 12px; max-height: 200px; overflow-y: auto; white-space: pre-wrap; word-break: break-word; }}

        /* Decision box inside output */
        .step-decision {{ padding: 8px 0; line-height: 1.8; }}
        .decision-row {{ margin-bottom: 4px; }}
        .decision-row strong {{ color: #53d8fb; }}
        .decision-row code {{ background: #0f3460; padding: 2px 8px; border-radius: 4px; color: #e94560; font-size: 14px; }}

        /* Raw events */
        .raw-events {{ margin-top: 10px; }}
        .raw-events summary {{ cursor: pointer; color: #555; font-size: 11px; padding: 5px 0; }}
        .raw-events pre {{ background: #0a0a1a; padding: 10px; border-radius: 4px; font-size: 11px; max-height: 400px; overflow: auto; }}

        /* OCR section */
        .ocr-section {{ background: #2a1a05; border-left: 3px solid #ffb454; border-radius: 6px; padding: 10px 12px; margin: 8px 0; }}
        .ocr-header {{ font-size: 11px; text-transform: uppercase; letter-spacing: 1px; color: #ffb454; margin-bottom: 6px; font-weight: bold; }}
        .ocr-cleaned {{ font-size: 12px; color: #ffb454; white-space: pre-wrap; word-break: break-word; font-family: 'SF Mono', monospace; }}
        .ocr-raw {{ margin-top: 8px; }}
        .ocr-raw summary {{ cursor: pointer; color: #c98a3e; font-size: 11px; }}
        .ocr-raw pre {{ margin-top: 4px; font-size: 11px; color: #c98a3e; background: #1a1202; padding: 8px; border-radius: 4px; max-height: 300px; overflow-y: auto; }}

        .settle-time {{ font-size: 11px; color: #e2a93b; margin-left: 8px; white-space: nowrap; }}
        .usage {{ font-size: 11px; color: #666; margin-left: auto; white-space: nowrap; }}

        code {{ font-family: 'SF Mono', 'Fira Code', monospace; }}
        pre {{ white-space: pre-wrap; word-break: break-word; }}
    </style>
</head>
<body>
    <h1>Run Report</h1>
    <div class="meta">
        <span><span class="label">Run:</span> {run_name}</span>
        <span><span class="label">LLM:</span> {config.get('_llm_alias') or config.get('llm_model', '?')}</span>
        <span><span class="label">Turns:</span> {len(turns)}</span>
        <span><span class="label">Events:</span> {len(events)}</span>
        {cost_summary}
        {duration_str}
    </div>

    {referee_html}

    {task_html}

    <div class="stats">{stats_html}</div>

    {turns_html}
</body>
</html>"""
    return html


def _try_parse_json(s):
    """Try to parse a string as JSON, return original if it fails."""
    if isinstance(s, dict):
        return s
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return s


def _escape(text: str) -> str:
    """Escape HTML special characters."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _render_memory_update_html(exp: dict) -> str:
    """Render memory updates from a turn explanation dict."""
    mem_raw = exp.get('memory_updates_raw', '') or exp.get('memory_updates', '')
    if not mem_raw:
        return '<div class="exp-row"><strong>Memory:</strong> <span style="color:#666;">(none)</span></div>'
    if isinstance(mem_raw, str) and mem_raw.strip().lower() == 'none':
        return '<div class="exp-row"><strong>Memory:</strong> <span style="color:#666;">(none)</span></div>'
    # Try to pretty-print
    try:
        if isinstance(mem_raw, str):
            mem_obj = json.loads(mem_raw)
        elif isinstance(mem_raw, dict):
            mem_obj = mem_raw
        else:
            mem_obj = mem_raw
        mem_display = json.dumps(mem_obj, indent=2)
    except (json.JSONDecodeError, TypeError):
        mem_display = str(mem_raw)
    return f'<div class="exp-row"><strong>Memory:</strong> <pre style="display:inline-block;margin:4px 0;background:#1a2e1a;padding:6px 10px;border-radius:4px;font-size:12px;color:#7ddf64;">{_escape(mem_display)}</pre></div>'


def _render_ocr_html(turn: dict) -> str:
    """Render the OCR flush event for this turn, if present."""
    ocr = turn.get("ocr")
    if not ocr:
        return ""
    n = ocr.get("n_captures", 0)
    dur = ocr.get("duration", 0)
    cleaned = ocr.get("cleaned", "") or ""
    raw = ocr.get("raw", {}) or {}
    cost = ocr.get("cost_usd", 0) or 0
    in_tok = ocr.get("input_tokens", 0) or 0
    out_tok = ocr.get("output_tokens", 0) or 0
    if n == 0 and not cleaned:
        return ""

    raw_json = json.dumps(raw, indent=2, ensure_ascii=False)
    cleaned_html = f'<pre class="ocr-cleaned">{_escape(cleaned or "(empty)")}</pre>'
    raw_html = f"""
        <details class="ocr-raw">
            <summary>Raw buffer ({len(raw)})</summary>
            <pre>{_escape(raw_json[:8000])}</pre>
        </details>
    """ if raw else ""

    meta_bits = [f"{n} captures", f"{dur}s"]
    if cost > 0:
        meta_bits.append(f"${cost:.5f}")
    if in_tok or out_tok:
        meta_bits.append(f"{in_tok}→{out_tok} tok")
    meta = " | ".join(meta_bits)

    return f"""
        <div class="ocr-section">
            <div class="ocr-header">📝 OCR ({meta})</div>
            {cleaned_html}
            {raw_html}
        </div>
    """


def _format_action(action) -> str:
    """Format an action for display. Handles both old str and new list format."""
    if isinstance(action, list):
        return "[" + ", ".join(str(a) for a in action) + "]"
    return str(action)


def main():
    parser = argparse.ArgumentParser(
        prog="pokemon report",
        description="Regenerate the standalone HTML report for a run directory.",
    )
    parser.add_argument(
        "run_dir", nargs="?", default=None,
        help="Run directory (e.g. local/runs/2026-04-06_…). Default: latest run.",
    )
    args = parser.parse_args()

    runs_dir = Path("local/runs")

    if args.run_dir:
        run_dir = Path(args.run_dir)
    else:
        if not runs_dir.exists():
            print("No runs directory found.")
            sys.exit(1)
        dirs = sorted(runs_dir.iterdir(), reverse=True)
        if not dirs:
            print("No runs found.")
            sys.exit(1)
        run_dir = dirs[0]

    if not run_dir.exists():
        print(f"Run directory not found: {run_dir}")
        sys.exit(1)

    print(f"Generating report for: {run_dir}")

    events = load_events(run_dir)
    turns = group_events_by_turn(events)

    print(f"  {len(events)} events, {len(turns)} turns")

    html = generate_html(run_dir, events, turns)

    output_path = run_dir / "report.html"
    with open(output_path, "w") as f:
        f.write(html)

    print(f"  Report saved: {output_path}")
    print(f"  Open: file://{output_path.resolve()}")

    # Auto-open on macOS
    if sys.platform == "darwin":
        import subprocess
        subprocess.run(["open", str(output_path)])


if __name__ == "__main__":
    main()
