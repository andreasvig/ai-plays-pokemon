"""Report generator: creates an interactive HTML report from a run folder.

Usage:
    python report.py local/runs/2026-04-06_21-34-37_phase5_test
    python report.py  # auto-picks latest run
"""

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

    if current_turn:
        turns.append(current_turn)

    return turns


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


def _render_trace_html(trace: list[dict]) -> str:
    """Render the trace into grouped, collapsible HTML."""
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
    if grouped["user_input"]:
        preview = grouped["user_input"][:100].replace("\n", " ")
        parts.append(f"""
        <details class="trace-step trace-input">
            <summary>
                <span class="step-label">Input</span>
                <span class="step-preview">{_escape(preview)}...</span>
            </summary>
            <pre class="step-content">{_escape(grouped["user_input"][:8000])}</pre>
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
                inner += f'<div class="step-thinking"><div class="thinking-label">Thinking</div><pre>{_escape(step["thinking"][:5000])}</pre></div>'
            inner += f'<div class="step-call"><div class="call-label">Call</div><pre>{_escape(tool_name)}({_escape(args_str[:2000])})</pre></div>'
            if resp_str:
                inner += f'<div class="step-response"><div class="response-label">Response</div><pre>{_escape(resp_str[:2000])}</pre></div>'

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
            args = step["args"]
            parsed = args if isinstance(args, dict) else _try_parse_json(args)

            inner = ""
            if step["thinking"]:
                inner += f'<div class="step-thinking"><div class="thinking-label">Thinking</div><pre>{_escape(step["thinking"][:5000])}</pre></div>'

            if isinstance(parsed, dict) and ("i_did" in parsed or "i_expect" in parsed):
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
                inner += f"""<div class="step-decision">
                    <div class="decision-row"><strong>I saw:</strong> {_escape(parsed.get('i_saw', ''))}</div>
                    <div class="decision-row"><strong>I did:</strong> {_escape(parsed.get('i_did', ''))}</div>
                    <div class="decision-row"><strong>I expect:</strong> {_escape(parsed.get('i_expect', ''))}</div>
                    <div class="decision-row"><strong>Action:</strong> <code>{_escape(_format_action(parsed.get('inputs', '')))}</code></div>
                    {memory_html}
                </div>"""
            else:
                args_str = json.dumps(parsed, indent=2) if isinstance(parsed, dict) else str(args)
                inner += f'<div class="step-call"><pre>{_escape(args_str[:2000])}</pre></div>'

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
                <pre>{_escape(str(step["args"])[:1000])}</pre>
            </div>""")

        elif step["type"] == "thinking_only":
            parts.append(f"""
            <details class="trace-step trace-thinking-only">
                <summary><span class="step-label">Thinking</span></summary>
                <pre class="step-content">{_escape(step["thinking"][:5000])}</pre>
            </details>""")

    return "\n".join(parts)


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

    # --- Task goal section ---
    task_html = ""
    if task:
        task_html = f"""
        <div class="task-section">
            <div class="task-goal">{_escape(task)}</div>
        </div>"""

    # --- Build turn HTML ---
    turns_html = ""
    for turn in turns:
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

        # Raw events (collapsed)
        raw_events_json = json.dumps(turn["events"], indent=2, default=str)

        turns_html += f"""
        <div class="turn" id="turn-{turn_num}">
            <div class="turn-header" onclick="this.parentElement.classList.toggle('collapsed')">
                <span class="turn-number">Turn {turn_num}</span>
                <span class="turn-action"><code>{_escape(action)}</code></span>
                <span class="turn-summary">{_escape(exp.get('i_did', '')[:100])}</span>
                {usage_html}
            </div>
            <div class="turn-body">
                <div class="turn-columns">
                    <div class="turn-left">
                        {screenshot_html}
                    </div>
                    <div class="turn-right">
                        <div class="explanation">
                            <div class="exp-row"><strong>I saw:</strong> {_escape(exp.get('i_saw', ''))}</div>
                            <div class="exp-row"><strong>I did:</strong> {_escape(exp.get('i_did', ''))}</div>
                            <div class="exp-row"><strong>I expect:</strong> {_escape(exp.get('i_expect', ''))}</div>
                            {_render_memory_update_html(exp)}
                        </div>
                    </div>
                </div>
                {trace_html}
                <details class="raw-events">
                    <summary>Raw Events ({len(turn['events'])})</summary>
                    <pre>{_escape(raw_events_json[:10000])}</pre>
                </details>
            </div>
        </div>
        """

    # Event type summary
    type_counts = {}
    for e in events:
        t = e["type"]
        type_counts[t] = type_counts.get(t, 0) + 1
    stats_html = " | ".join(f"{k}: {v}" for k, v in sorted(type_counts.items()))

    # Cost summary
    cost_summary = ""
    if total_cost > 0:
        cost_summary = f'<span><span class="label">Cost:</span> ${total_cost:.4f}</span>'

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

        /* Task section */
        .task-section {{ background: #0f3460; border-radius: 8px; padding: 16px 20px; margin-bottom: 20px; border-left: 4px solid #e94560; }}
        .task-goal {{ font-size: 20px; font-weight: bold; color: #fff; }}
        .task-subgoals {{ margin-top: 12px; padding-left: 0; list-style: none; }}
        .task-subgoals li {{ padding: 4px 0 4px 20px; position: relative; color: #bbb; font-size: 14px; }}
        .task-subgoals li::before {{ content: ''; position: absolute; left: 0; top: 11px; width: 12px; height: 12px; border-radius: 3px; border: 2px solid #53d8fb; }}

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

        .usage {{ font-size: 11px; color: #666; margin-left: auto; white-space: nowrap; }}

        code {{ font-family: 'SF Mono', 'Fira Code', monospace; }}
        pre {{ white-space: pre-wrap; word-break: break-word; }}
    </style>
</head>
<body>
    <h1>Run Report</h1>
    <div class="meta">
        <span><span class="label">Run:</span> {run_name}</span>
        <span><span class="label">LLM:</span> {config.get('llm_model', '?')}</span>
        <span><span class="label">VLM:</span> {config.get('vlm_model', '?')}</span>
        <span><span class="label">Turns:</span> {len(turns)}</span>
        <span><span class="label">Events:</span> {len(events)}</span>
        {cost_summary}
        {duration_str}
    </div>

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


def _format_action(action) -> str:
    """Format an action for display. Handles both old str and new list format."""
    if isinstance(action, list):
        return "[" + ", ".join(str(a) for a in action) + "]"
    return str(action)


def main():
    runs_dir = Path("local/runs")

    if len(sys.argv) > 1:
        run_dir = Path(sys.argv[1])
    else:
        # Pick latest run
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
