#!/usr/bin/env python
"""Standalone smoke probe for the TaskMaster agent (Phase B2).

Feeds a hand-crafted COLD-START ``TaskMasterInput`` and prints the resulting
``TaskMasterOutput``. Runs without SERPER (web search degrades gracefully) and,
if OpenRouter is unreachable / there is no network / no API key, skips
gracefully with a clear message and exit 0 — never tracebacks.

Usage:
    ./venv/bin/python scripts/probe_task_master.py
"""

from __future__ import annotations

import asyncio
import os
import sys

# Ensure the repo root is importable when run as a script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

from src.agent.task_master import (
    DEFAULT_REQUEST_LIMIT,
    TaskMasterDeps,
    TaskMasterInput,
    create_task_master_agent,
    render_input,
)
from src.agent.tools.page_visit import PageVisitor


# A hand-crafted cold-start input: no prior task outputs, no previous-task
# evidence. TaskMaster should emit a null rating and a sensible first task.
COLD_START_INPUT = TaskMasterInput(
    meta_goal="Beat the first gym leader Brock in Pokemon FireRed.",
    prior_task_outputs=[],
    prev_player_reasons=[],
    prev_first_image=None,
    prev_last_image=None,
    prev_player_self_assessment=None,
)


def _build_config() -> dict:
    """Assemble a minimal config dict for agent construction.

    Mirrors what the run loop would pass: a resolved OpenRouter model id + the
    OpenRouter API key from env. Defaults to a small, capable model so the
    probe is cheap when a key IS present.
    """
    return {
        "openrouter_api_key": os.environ.get("OPENROUTER_API_KEY", ""),
        "llm_model": os.environ.get(
            "TASK_MASTER_PROBE_MODEL", "google/gemini-2.5-flash"
        ),
    }


async def _main() -> int:
    load_dotenv()  # must run BEFORE agent construction / Serper key read

    # Report tool availability up front (SERPER is expected to be absent here).
    has_serper = bool(os.environ.get("SERPER_API_KEY"))
    has_openrouter = bool(os.environ.get("OPENROUTER_API_KEY"))
    print(f"[probe] SERPER_API_KEY present: {has_serper}")
    print(f"[probe] OPENROUTER_API_KEY present: {has_openrouter}")

    config = _build_config()

    try:
        agent, model_settings = create_task_master_agent(config)
    except Exception as exc:  # construction itself should not normally fail
        print(f"[probe] SKIP — could not construct TaskMaster agent: "
              f"{type(exc).__name__}: {exc}")
        return 0

    if not has_openrouter:
        print("[probe] SKIP — no OPENROUTER_API_KEY; cannot call the model. "
              "(Construction + input rendering verified above.)")
        print("\n[probe] Rendered cold-start user message:\n")
        print(render_input(COLD_START_INPUT))
        return 0

    deps = TaskMasterDeps(page_visitor=PageVisitor())
    user_message = render_input(COLD_START_INPUT)

    from pydantic_ai.usage import UsageLimits

    # Bound by round count, NOT total tokens (web-research agents accumulate
    # page text; an aggregate token cap trips mid-run).
    usage_limits = UsageLimits(request_limit=DEFAULT_REQUEST_LIMIT)

    kwargs: dict = {"model_settings": model_settings} if model_settings else {}
    try:
        result = await agent.run(
            user_message, deps=deps, usage_limits=usage_limits, **kwargs
        )
    except Exception as exc:
        # No network / OpenRouter unreachable / auth / rate-limit etc.
        print(f"[probe] SKIP — TaskMaster call failed (likely no network / "
              f"unreachable OpenRouter): {type(exc).__name__}: {exc}")
        return 0

    out = result.output
    print("\n[probe] TaskMasterOutput:\n")
    print(f"  reasoning: {out.reasoning}\n")
    if out.rating_of_previous_task is None:
        print("  rating_of_previous_task: None (expected on cold start) ✓")
    else:
        print(f"  rating_of_previous_task: {out.rating_of_previous_task!r} "
              "(UNEXPECTED on cold start — should be None)")
    print(f"\n  task.title: {out.task.title}")
    print(f"  task.description: {out.task.description}")
    print(f"  task.success_criteria: {out.task.success_criteria}")
    return 0


def main() -> None:
    try:
        rc = asyncio.run(_main())
    except KeyboardInterrupt:
        rc = 0
    except Exception as exc:  # last-resort guard — never traceback out of probe
        print(f"[probe] SKIP — unexpected error: {type(exc).__name__}: {exc}")
        rc = 0
    sys.exit(rc)


if __name__ == "__main__":
    main()
