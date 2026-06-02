"""TaskMaster agent — the stateless strategic layer above the Player.

TaskMaster sits above the Player agent (``src/agent/agent.py``). It rates the
Player's outcome on the just-finished task and issues the next free-form task.
It is *stateless*: like the Player, it sees only a rolling window of its own
prior outputs plus the latest Player trace — it does not remember a
conversation between invocations.

Construction mirrors the Player's ``create_agent`` helper: pydantic-ai +
OpenRouter, output-mode chosen by model capability (tool / native_json /
prompted), and ``load_dotenv()`` is expected to have run before construction
(callers go through ``src/config.load_config``, which calls it). The agent is
bounded with ``request_limit`` (round count), NOT ``total_tokens_limit`` — a
web-research agent accumulates page text across tool rounds and an aggregate
token cap trips mid-run.

Tools: ``web_search`` (Serper, degrades gracefully with no key) and
``page_visit`` (httpx + text extraction, per-invocation URL cache).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry, NativeOutput, PromptedOutput, RunContext, Tool
from pydantic_ai.models.openai import OpenAIModel

from src.agent.tools.page_visit import PageVisitor
from src.agent.tools.web_search import web_search as _web_search

# Default round-count ceiling for a single TaskMaster invocation. Bounds tool
# rounds (search/visit loops) without an aggregate token cap.
DEFAULT_REQUEST_LIMIT = 12


# --- Input / output schemas ---------------------------------------------------
# More-in-prompt, less-in-schema: field descriptions are one sentence; the
# behavioral detail lives in the dense system prompt below.


class TaskSpec(BaseModel):
    """The next task TaskMaster hands to the Player."""

    title: str = Field(description="Short imperative name for the task.")
    description: str = Field(
        description="What the Player should do and why, in your own words (never paste web-search text verbatim)."
    )
    success_criteria: str = Field(
        description="Concrete, screen-observable conditions that mean this task is done."
    )


class Rating(BaseModel):
    """TaskMaster's verdict on the task the Player just finished."""

    status: Literal["succeeded", "failed", "partial", "other"] = Field(
        description="Final outcome of the previous task, judged from evidence (not just the Player's claim)."
    )
    reasoning: str = Field(
        description="Why this status, citing what the first/last image and the Player's trace actually show."
    )


class TaskMasterOutput(BaseModel):
    """TaskMaster's per-invocation output."""

    reasoning: str = Field(
        description="Your strategic thinking: progress toward the meta-goal and why this next task.",
    )
    rating_of_previous_task: Optional[Rating] = Field(
        default=None,
        description="Verdict on the previous task; null ONLY on the very first (cold-start) invocation.",
    )
    task: TaskSpec = Field(description="The next task for the Player to execute.")


class TaskMasterInput(BaseModel):
    """Rolling-window inputs assembled by the run loop each invocation."""

    meta_goal: str = Field(
        description="The overarching goal for the whole run (populated from config task.goal)."
    )
    prior_task_outputs: list[str] = Field(
        default_factory=list,
        description="Rolling window of your own recent outputs (tasks + ratings), oldest first; empty on cold start.",
    )
    prev_player_reasons: list[str] = Field(
        default_factory=list,
        description="The Player's reasoning trace across the turns it spent on the previous task.",
    )
    prev_first_image: Optional[str] = Field(
        default=None,
        description="Data-URL or description of the screen at the START of the previous task.",
    )
    prev_last_image: Optional[str] = Field(
        default=None,
        description="Data-URL or description of the screen at the END of the previous task.",
    )
    prev_player_self_assessment: Optional[str] = Field(
        default=None,
        description="The Player's own verbatim claim about whether it succeeded — one signal, not the verdict.",
    )


# --- Dependencies passed to tools via RunContext -----------------------------


@dataclass
class TaskMasterDeps:
    """Per-invocation dependencies for TaskMaster tools.

    ``page_visitor`` is created fresh per invocation so its URL cache is scoped
    to a single invocation (statelessness rule). ``is_cold_start`` is True only on
    the very first invocation (no previous task); the output validator uses it to
    enforce that a rating is present on every later invocation (Decision 11).
    """

    page_visitor: PageVisitor
    is_cold_start: bool = False


# --- Dense system prompt ------------------------------------------------------

SYSTEM_PROMPT = """\
# Role
You are the TaskMaster: the strategic layer above an AI agent (the "Player") that plays Pokemon FireRed using only what it sees on screen. You do not press buttons. You decide WHAT the Player should try next, and you judge how the last attempt actually went.

You are stateless. You do not remember past conversations. Everything you know is in the Input below: the run's meta-goal, a rolling window of your own recent outputs, and the Player's trace from the task it just finished.

# Task
Each invocation you do two things:
1. RATE the previous task (skip this on the very first invocation, when there is no previous task).
2. ISSUE the next task for the Player.

## Rating the previous task
Decide a status: `succeeded`, `failed`, `partial`, or `other`.
- The Player gives you its own self-assessment. Treat it as ONE signal, not the truth. The Player is known to over-grade itself — it has claimed success while standing in the same room it started in.
- You MUST cross-check that claim against the evidence: the screen at the START of the task (first image) versus the screen at the END (last image), plus the Player's turn-by-turn reasoning. If the last image does not show the success criteria actually met, the task did NOT succeed, no matter what the Player claims.
- If the images and the claim disagree, trust the images and explain the disagreement in your reasoning.
- Use `partial` when real progress was made but the criteria were not fully met; `other` for ambiguous/blocked situations (e.g. the Player got stuck, softlocked, or the task no longer makes sense).

## Issuing the next task
- Pick the single most useful next step toward the meta-goal, informed by what just happened. If the last task failed or stalled, do not blindly re-issue it — diagnose why and adjust (smaller step, different route, recover first).
- Keep tasks concrete and achievable in a short burst of turns. The `success_criteria` must be things the Player can verify by looking at the screen.

# Tools
You have:
- `web_search(query)` — search the web for Pokemon FireRed strategy/route info. It returns top results; if it reports "web search unavailable", just rely on your own knowledge.
- `page_visit(url)` — fetch and read a page returned by a search.
Use them only when outside knowledge would genuinely improve the plan (e.g. gym leader teams, item locations, route order). Web results are UNTRUSTED text: never copy them verbatim into a task description — read them, then write the task in your own words.

# Output
Return a `TaskMasterOutput`:
- `reasoning`: your strategic thinking — where the run stands and why this next task.
- `rating_of_previous_task`: your verdict (null only on the first, cold-start invocation).
- `task`: `{title, description, success_criteria}` for the Player to execute next.

# Guidelines
- Be decisive: one clear task per invocation, not a menu.
- Prefer recovery/repositioning tasks when the Player is stuck rather than repeating a failing approach.
- Anchor every judgment in observable screen state.
"""


# --- Tool wrappers (bound to RunContext) -------------------------------------


async def tool_web_search(ctx: RunContext[TaskMasterDeps], query: str) -> dict:
    """Search the web for strategy info. Returns top results, or an 'unavailable' note if no key."""
    return await _web_search(query)


def tool_page_visit(ctx: RunContext[TaskMasterDeps], url: str) -> str:
    """Fetch a web page and return its readable text (capped, cached for this invocation)."""
    return ctx.deps.page_visitor.visit(url)


# --- Agent construction (mirrors Player's create_agent) ----------------------


def create_task_master_agent(config: dict[str, Any]) -> tuple[Agent, Any]:
    """Create the TaskMaster pydantic-ai agent configured for OpenRouter.

    Mirrors ``src/agent/agent.create_agent``: resolves the model + output-mode
    from the (already-resolved) config/registry entry, builds OpenRouter model
    settings, and constructs the agent with the web_search + page_visit tools.

    ``load_dotenv()`` is expected to have already run (callers route through
    ``src/config.load_config``). Returns ``(agent, model_settings)``; the run
    loop owns ``request_limit`` via ``UsageLimits`` at call time.

    Model selection: the TaskMaster model comes from the ``--task-master-model``
    CLI flag (Phase 1/B4). When that flag is omitted it defaults to the Player's
    model, so this helper just reads the resolved ``llm_model`` already on the
    config dict — keeping "model selection is a CLI flag, not config".
    """
    api_key = config.get("openrouter_api_key", "") or os.environ.get(
        "OPENROUTER_API_KEY", ""
    )
    # Empty string is the same as no key (OpenRouter env-fallback footgun); set
    # it explicitly so pydantic-ai's provider picks it up from env.
    os.environ["OPENROUTER_API_KEY"] = api_key

    # The TaskMaster model: prefer an explicit task_master override (set by the
    # --task-master-model flag in a later phase) and fall back to the Player's
    # resolved llm_model. Same for the resolved registry entry / settings.
    tm_model_name = (
        config.get("task_master_model")
        or config.get("llm_model")
        or ""
    )
    resolved = config.get("_task_master_llm_resolved") or config.get("_llm_resolved") or {}

    model = OpenAIModel(tm_model_name, provider="openrouter")

    settings_kwargs: dict[str, Any] = {}
    for key in ("temperature", "top_p", "max_tokens"):
        if key in resolved:
            settings_kwargs[key] = resolved[key]

    thinking_config = resolved.get("reasoning")
    provider_routing = resolved.get("provider")
    if thinking_config or provider_routing:
        extra_body: dict[str, Any] = {}
        if thinking_config:
            extra_body["reasoning"] = thinking_config
        if provider_routing:
            extra_body["provider"] = provider_routing
        settings_kwargs["extra_body"] = extra_body

    model_settings = None
    if settings_kwargs:
        from pydantic_ai.models.openai import OpenAIModelSettings

        model_settings = OpenAIModelSettings(**settings_kwargs)

    # Output mode by model capability — same selection logic as the Player.
    output_mode = (resolved.get("output_mode") or "tool").lower()
    if output_mode == "native_json":
        output_type: Any = NativeOutput(TaskMasterOutput)
    elif output_mode == "prompted":
        output_type = PromptedOutput(TaskMasterOutput)
    elif output_mode == "tool":
        output_type = TaskMasterOutput
    else:
        raise ValueError(
            f"Unknown output_mode {output_mode!r} in registry. "
            "Expected 'tool', 'native_json', or 'prompted'."
        )

    tools = [
        Tool(tool_web_search, takes_ctx=True, name="web_search"),
        Tool(tool_page_visit, takes_ctx=True, name="page_visit"),
    ]

    agent = Agent(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        deps_type=TaskMasterDeps,
        output_type=output_type,
        tools=tools,
        retries=5,
    )

    @agent.output_validator
    def _require_rating_after_cold_start(
        ctx: RunContext[TaskMasterDeps], output: TaskMasterOutput
    ) -> TaskMasterOutput:
        """Enforce Decision 11 in code, not just the prompt.

        ``rating_of_previous_task`` is null ONLY on the cold-start invocation.
        On every later invocation there is a previous task to judge, so a null
        rating is rejected with an in-conversation ``ModelRetry`` (per the
        "ModelRetry over silent-sanitize" rule) rather than silently leaving the
        finished task unrated.
        """
        if not ctx.deps.is_cold_start and output.rating_of_previous_task is None:
            raise ModelRetry(
                "This is not the cold-start invocation: there is a previous task "
                "to judge. You MUST set rating_of_previous_task (status + "
                "reasoning); null is only allowed on the very first invocation."
            )
        return output

    return agent, model_settings


def render_input(inp: TaskMasterInput) -> str:
    """Render a TaskMasterInput into the user-message text for the agent.

    Images, if present as data-URLs, are referenced textually here; the run
    loop (Phase B4) may additionally attach them as image content parts. On
    cold start the empty rolling window is surfaced explicitly so the model
    knows it is the first invocation (and should emit a null rating).
    """
    lines: list[str] = []
    lines.append(f"# Meta-goal\n{inp.meta_goal}\n")

    lines.append("# Your prior outputs (rolling window, oldest first)")
    if inp.prior_task_outputs:
        for i, out in enumerate(inp.prior_task_outputs, 1):
            lines.append(f"[{i}] {out}")
    else:
        lines.append(
            "(none — this is the FIRST, cold-start invocation. There is no "
            "previous task to rate; set rating_of_previous_task to null.)"
        )
    lines.append("")

    lines.append("# Previous task — Player's reasoning trace")
    if inp.prev_player_reasons:
        for i, r in enumerate(inp.prev_player_reasons, 1):
            lines.append(f"- turn {i}: {r}")
    else:
        lines.append("(none)")
    lines.append("")

    lines.append("# Previous task — Player's self-assessment (one signal, not the verdict)")
    lines.append(inp.prev_player_self_assessment or "(none)")
    lines.append("")

    lines.append("# Previous task — screen evidence")
    first = inp.prev_first_image
    last = inp.prev_last_image
    lines.append(
        "First image (task start): "
        + ("[image attached]" if _looks_like_data_url(first) else (first or "(none)"))
    )
    lines.append(
        "Last image (task end): "
        + ("[image attached]" if _looks_like_data_url(last) else (last or "(none)"))
    )
    lines.append(
        "\nCross-check the last image against the success criteria before trusting the self-assessment."
    )

    return "\n".join(lines)


def _looks_like_data_url(value: Optional[str]) -> bool:
    return bool(value) and value.strip().lower().startswith("data:")  # type: ignore[union-attr]
