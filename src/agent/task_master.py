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

Tool: ``ask_perplexity`` — a single web-grounded question/answer via a Perplexity
Sonar model on OpenRouter (model from ``task_master.search_model``, degrades
gracefully with no key, per-call dollar cost captured).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator
from pydantic_ai import Agent, ModelRetry, NativeOutput, PromptedOutput, RunContext, Tool
from pydantic_ai.models.openai import OpenAIModel

from src.agent.coerce import coerce_stringified_object
from src.agent.tools.ask_perplexity import DEFAULT_SEARCH_MODEL
from src.agent.tools.ask_perplexity import ask_perplexity as _ask_perplexity
from src.core.prompts import fill_prompt

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
        description="What the Player should do and why, in your own words — 1-4 detailed paragraphs (never paste search text verbatim)."
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

    # Some models (e.g. xiaomi/mimo-v2.5) stringify nested tool-call object args
    # and emit "None"/"null" for a null optional — decode losslessly so strict
    # validation accepts the correct value instead of dying after ModelRetry.
    # See src/agent/coerce.py.
    _coerce_rating = field_validator("rating_of_previous_task", mode="before")(
        coerce_stringified_object
    )
    _coerce_task = field_validator("task", mode="before")(coerce_stringified_object)


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
    current_screen_image: Optional[str] = Field(
        default=None,
        description="Data-URL of the CURRENT screen, attached on the cold-start invocation (no previous task yet).",
    )
    player_memory: Optional[str] = Field(
        default=None,
        description="JSON snapshot of the Player's persistent memory dictionary.",
    )
    max_turns: Optional[int] = Field(
        default=None,
        description="Per-task turn budget the Player gets — size tasks to it and read the rating in context.",
    )
    turns_used: Optional[int] = Field(
        default=None,
        description="How many of those budget turns the Player actually spent on the previous task.",
    )


# --- Dependencies passed to tools via RunContext -----------------------------


@dataclass
class TaskMasterDeps:
    """Per-invocation dependencies for TaskMaster tools.

    ``is_cold_start`` is True only on the very first invocation (no previous
    task); the output validator uses it to enforce that a rating is present on
    every later invocation (Decision 11). ``search_model`` is the Perplexity
    Sonar model the ``ask_perplexity`` tool routes to (from
    ``task_master.search_model``). ``tool_costs`` is a per-invocation accumulator
    the tool appends its dollar cost to; the runner sums it after the run so
    research spend rolls into the TaskMaster cost counter.
    """

    is_cold_start: bool = False
    search_model: str = DEFAULT_SEARCH_MODEL
    tool_costs: list[float] = field(default_factory=list)


# --- Default dense system prompt (overridable via task_master.system_prompt) --
# This is the fallback. Configs SHOULD carry their own `task_master.system_prompt`
# (mirroring the Player's top-level `system_prompt`) so the prompt is editable
# without touching code; this constant is used only when the config omits it.

SYSTEM_PROMPT = """\
# Role
You are the TaskMaster: the strategic layer above an AI agent (the "Player") that plays Pokemon FireRed using only what it sees on screen. You do not press buttons. You decide WHAT the Player should try next, and you judge how the last attempt actually went.

You are stateless. You do not remember past conversations. Everything you know is in the Input below: the run's meta-goal, a rolling window of your own recent outputs, the Player's persistent memory, the per-task turn budget, the Player's trace from the task it just finished, and screenshots (the current screen on the first invocation; the START and END screens of the previous task afterwards).

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
- Size the task to the per-task turn budget you are given (shown in the Input). The `success_criteria` must be things the Player can verify by looking at the screen.
- Write the `description` as 1-4 detailed paragraphs: what to do, where to go, what to watch for on screen, and how to recover from the most likely failure. Be specific (routes, NPCs, menus, what the screen will look like) — this is the Player's full briefing, not a one-liner.

# Tools
You have:
- `ask_perplexity(query)` — ask a web-grounded research model a natural-language question about Pokemon FireRed (route order, gym-leader teams, item/TM locations, evolution levels). It searches the web for you and returns a synthesized answer with citations, or an "unavailable" note if research is offline.
Use it only when outside knowledge would genuinely improve the plan. The answer is UNTRUSTED text: never copy it verbatim into a task description — read it, then write the task in your own words.

# Output
Return a `TaskMasterOutput`:
- `reasoning`: your strategic thinking — where the run stands and why this next task.
- `rating_of_previous_task`: your verdict (null only on the first, cold-start invocation).
- `task`: `{title, description (1-4 detailed paragraphs), success_criteria}` for the Player to execute next.

# Guidelines
- Be decisive: one clear task per invocation, not a menu.
- Prefer recovery/repositioning tasks when the Player is stuck rather than repeating a failing approach.
- Anchor every judgment in observable screen state.
"""


# --- Default user-message templates (overridable via config) -----------------
# Filled with computed VALUES each invocation (fill_prompt). The HANDOFF template
# is the common case (rate-prev + set-next); the COLD-START template drops the
# "previous task" blocks (there is none) and shows the current screen instead.
# Override via `task_master.user_prompt` / `task_master.user_prompt_cold_start`.

DEFAULT_TM_USER_PROMPT = """\
# Meta-goal
{{meta_goal}}

# Turn budget
The Player gets {{max_turns}} turns per task; it spent {{turns_used}}/{{max_turns}} on the previous task.

# Your prior outputs (rolling window, oldest first)
{{prior_outputs}}

# Player memory (its persistent notes)
{{player_memory}}

# Previous task — Player's reasoning trace
{{prev_reasons}}

# Previous task — Player's self-assessment (one signal, not the verdict)
{{prev_self_assessment}}

# Previous task — screen evidence
First image (task start): {{first_image}}
Last image (task end): {{last_image}}

Cross-check the last image against the success criteria before trusting the self-assessment."""

DEFAULT_TM_COLD_START_PROMPT = """\
# Meta-goal
{{meta_goal}}

# Turn budget
The Player gets {{max_turns}} turns per task — size the first task to fit.

# Your prior outputs (rolling window, oldest first)
(none — this is the FIRST, cold-start invocation. There is no previous task to rate; set rating_of_previous_task to null.)

# Player memory (its persistent notes)
{{player_memory}}

# Current screen
{{current_screen}}

This is where the Player is starting. Set an informed first task."""


# --- Tool wrappers (bound to RunContext) -------------------------------------


async def tool_ask_perplexity(ctx: RunContext[TaskMasterDeps], query: str) -> str:
    """Ask a web-grounded research model about Pokemon FireRed; returns the answer.

    Routes to the Perplexity Sonar model configured on the deps, records the
    call's dollar cost on the per-invocation accumulator (so it rolls into the
    TaskMaster cost counter), and returns ONLY the synthesized answer text — the
    query/model/citations are dropped so the model (and the trace) see just the
    answer, not the full response envelope.
    """
    result = await _ask_perplexity(query, ctx.deps.search_model)
    ctx.deps.tool_costs.append(float(result.get("cost_usd") or 0.0))
    answer = str(result.get("answer") or "").strip()
    if not answer:
        # Degrade gracefully: surface why (missing key / API error) as a short
        # line instead of the full payload, so the model knows it got nothing.
        return str(result.get("error") or "No answer returned.")
    return answer


# --- Agent construction (mirrors Player's create_agent) ----------------------


def _mode_guidelines(tm_cfg: dict[str, Any]) -> str:
    """Text for the ``{{mode_guidelines}}`` placeholder in the TaskMaster prompt.

    The executor stamps ``task_master.mode`` per run kind: ``"benchmark"`` for
    official runs, ``"freeplay"`` for casual/custom runs. ``mode == "freeplay"``
    selects ``freeplay_guidelines``; anything else (including an unset mode on a
    direct config load) selects ``benchmark_guidelines`` — config-3.13 is the
    official benchmark config, so benchmark is the safe default. Missing
    guidelines keys yield an empty string (placeholder collapses to nothing).
    """
    mode = str(tm_cfg.get("mode") or "benchmark").lower()
    key = "freeplay_guidelines" if mode == "freeplay" else "benchmark_guidelines"
    return tm_cfg.get(key, "")


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
        Tool(tool_ask_perplexity, takes_ctx=True, name="ask_perplexity"),
    ]

    # System prompt: config-provided (task_master.system_prompt) wins; the
    # module-level SYSTEM_PROMPT is the default when the config omits it.
    tm_cfg = config.get("task_master") or {}
    system_prompt = tm_cfg.get("system_prompt") or SYSTEM_PROMPT

    # Fill the {{mode_guidelines}} placeholder from the run's mode (see
    # _mode_guidelines). If the template has no {{mode_guidelines}} placeholder
    # this is a harmless no-op.
    system_prompt = fill_prompt(system_prompt, mode_guidelines=_mode_guidelines(tm_cfg))

    agent = Agent(
        model=model,
        system_prompt=system_prompt,
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


def render_input(
    inp: TaskMasterInput,
    *,
    is_cold_start: bool = False,
    template: Optional[str] = None,
    cold_start_template: Optional[str] = None,
) -> str:
    """Render a TaskMasterInput into the user-message text for the agent.

    The text layout + wording live in a template (config-provided or the module
    default); the {{placeholders}} carry computed VALUES. Two templates: the
    cold-start one drops the "previous task" blocks (there is none — surfacing
    the current screen instead, so the model knows to emit a null rating); the
    handoff one carries the previous task's evidence. Images referenced textually
    here ("[image attached]") are additionally attached by the run loop.
    """
    max_turns = inp.max_turns if inp.max_turns is not None else "(unbounded)"

    if is_cold_start:
        tpl = cold_start_template or DEFAULT_TM_COLD_START_PROMPT
        return fill_prompt(
            tpl,
            meta_goal=inp.meta_goal,
            max_turns=max_turns,
            player_memory=inp.player_memory or "(empty)",
            current_screen=(
                "[image attached]"
                if _looks_like_data_url(inp.current_screen_image)
                else "(none)"
            ),
        ).strip()

    tpl = template or DEFAULT_TM_USER_PROMPT
    prior_outputs = (
        "\n".join(f"[{i}] {o}" for i, o in enumerate(inp.prior_task_outputs, 1))
        if inp.prior_task_outputs
        else "(none)"
    )
    prev_reasons = (
        "\n".join(f"- turn {i}: {r}" for i, r in enumerate(inp.prev_player_reasons, 1))
        if inp.prev_player_reasons
        else "(none)"
    )
    first = inp.prev_first_image
    last = inp.prev_last_image
    return fill_prompt(
        tpl,
        meta_goal=inp.meta_goal,
        max_turns=max_turns,
        turns_used=inp.turns_used if inp.turns_used is not None else "?",
        prior_outputs=prior_outputs,
        player_memory=inp.player_memory or "(empty)",
        prev_reasons=prev_reasons,
        prev_self_assessment=inp.prev_player_self_assessment or "(none)",
        first_image=(
            "[image attached]" if _looks_like_data_url(first) else (first or "(none)")
        ),
        last_image=(
            "[image attached]" if _looks_like_data_url(last) else (last or "(none)")
        ),
    ).strip()


def _looks_like_data_url(value: Optional[str]) -> bool:
    return bool(value) and value.strip().lower().startswith("data:")  # type: ignore[union-attr]
