"""Agent turn loop using Pydantic AI with OpenRouter."""

import json
from dataclasses import dataclass
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field, field_validator
from pydantic_ai import Agent, ModelRetry, NativeOutput, PromptedOutput, RunContext
from pydantic_ai.models.openai import OpenAIModel

from src.agent.coerce import coerce_stringified_object

Button = Literal["up", "down", "left", "right", "a", "b", "start", "select", "wait"]

# Apply patches before creating any models
from src.core.patches import apply_patches
apply_patches()

from src.emulator import EmulatorClient, OCRRunner
from src.core import RunLogger, StateManager


# --- Output models ---

class ReturnToTaskMaster(BaseModel):
    """Hand control back to TaskMaster with the Player's self-assessment.

    Only emitted when the TaskMaster meta-agent is enabled. Set on `GameAction`
    instead of `inputs` when the current task is done (or impossible) and the
    Player wants the next task. This block is fed to TaskMaster verbatim.
    """
    self_assessment: Literal["succeeded", "failed", "partial", "other"] = Field(
        description="Your grade of how the task went: succeeded / failed / partial / other.",
    )
    task_summary: str = Field(
        description="Factual prose summary of the task for TaskMaster. See the system prompt for what to include.",
    )


class GameAction(BaseModel):
    """Output: button presses, reasoning, success grade, memory updates.

    The optional `return_to_taskmaster` field is the discriminator between the
    two Player modes: when it is None (the default) this is a normal
    interact-with-game turn driven by `inputs`; when it is set the Player is
    handing control back to TaskMaster and `inputs` is ignored. A single schema
    (no true union) keeps prompted-output models reliable.

    `return_to_taskmaster` is only surfaced to the model when TaskMaster is
    enabled — `create_agent` then uses this class as the output type. When
    TaskMaster is disabled the model-facing output type is `_LegacyGameAction`
    (the four base fields, no TM field), so the legacy single-agent schema and
    behavior are byte-for-byte unchanged.
    """
    inputs: list[Button] = Field(
        description="The buttons to press this turn.",
    )
    reasoning: str = Field(
        description="Your reasoning for this turn. See the system prompt.",
        max_length=5000,
    )
    last_turn_succeeded: Optional[bool] = Field(
        description="Your grade of the previous turn: true / false / null. See the system prompt.",
    )
    memory_updates: str = Field(
        description=(
            "JSON object of memory keys to update (dot notation for nesting), or \"none\". "
            "See the system prompt."
        ),
    )
    return_to_taskmaster: Optional[ReturnToTaskMaster] = Field(
        default=None,
        description=(
            "Optional handoff field. Null for a normal game turn (drive the turn with `inputs`); "
            "set it (and leave `inputs` empty) to hand the task back to TaskMaster. When and why to "
            "do so is explained in the system prompt."
        ),
    )

    # Some models stringify this nested object / emit "None" for the null case;
    # decode losslessly so strict validation accepts it. See src/agent/coerce.py.
    # check_fields=False: _LegacyGameAction (TM-disabled path) inherits this then
    # drops the field, which is the documented inherit-and-drop case.
    _coerce_handoff = field_validator(
        "return_to_taskmaster", mode="before", check_fields=False
    )(coerce_stringified_object)


# Model-facing schema for the legacy (TaskMaster-disabled) path. Mirrors
# GameAction's four base fields EXACTLY and omits `return_to_taskmaster`, so the
# JSON schema the model sees in the single-agent path is identical to before
# TaskMaster existed. Built by subclassing-then-removing rather than re-declaring
# the fields, so the descriptions never drift from GameAction. The schema title
# is pinned to "GameAction" so the tool/schema name on the wire is unchanged too.
class _LegacyGameAction(GameAction):
    """Output: button presses, reasoning, success grade, memory updates."""
    # NOTE: docstring above is pinned to GameAction's original (pre-TaskMaster)
    # wording on purpose — it becomes the schema `description` the model sees in
    # the TM-disabled path, so that schema stays byte-for-byte unchanged.
    model_config = {"title": "GameAction"}


# Drop the TM-only field from the legacy model so it never reaches the model.
del _LegacyGameAction.model_fields["return_to_taskmaster"]
_LegacyGameAction.model_rebuild(force=True)


# --- Agent dependencies (passed to tools via RunContext) ---

@dataclass
class AgentDeps:
    """Dependencies available to all agent tools."""
    # Shared infrastructure (immutable per run)
    emulator: EmulatorClient
    state: StateManager
    logger: RunLogger
    ocr: Optional[OCRRunner] = None

    # Per-turn state (mutable)
    turn_number: int = 0
    agent_id: str = "agent_0"

    # TaskMaster budget context (only meaningful when TaskMaster is enabled).
    # `current_task_turn` is how many turns the Player has spent on the CURRENT
    # task (1-based); `max_turns_per_task` is the per-task budget. The output
    # validator uses these to force a handoff at the budget boundary. Left at
    # their defaults (and unused) in the legacy single-agent path.
    current_task_turn: int = 0
    max_turns_per_task: int = 0



# --- Build the agent ---

def create_agent(config: dict[str, Any]) -> tuple[Agent, Any, list[str]]:
    """Create a Pydantic AI agent configured for OpenRouter.

    Returns:
        (agent, model_settings, fallback_model_ids)
    """
    import os
    from src.core.prompts import fill_prompt

    api_key = config.get("openrouter_api_key", "")
    llm_model_name = config.get("llm_model", "")

    # Set the API key for OpenRouter provider
    os.environ["OPENROUTER_API_KEY"] = api_key

    model = OpenAIModel(
        llm_model_name,
        provider="openrouter",
    )

    # Build model settings from resolved registry entry (if alias used) + legacy
    # top-level `thinking` block. Registry entry wins when both are present.
    resolved = config.get("_llm_resolved") or {}
    thinking_config = resolved.get("reasoning") or config.get("thinking")

    settings_kwargs: dict[str, Any] = {}
    for key in ("temperature", "top_p", "max_tokens"):
        if key in resolved:
            settings_kwargs[key] = resolved[key]

    # extra_body bundles OpenRouter-specific routing controls. Two passthrough
    # fields are merged here: `reasoning` (effort/binary toggle) and `provider`
    # (per-model OpenRouter provider routing — sort: "throughput" | "price" |
    # "latency", order: [...], ignore: [...]). Only relevant for multi-provider
    # models; for single-provider ones (xAI, Xiaomi) it's a no-op.
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

    # Resolve system prompt with template substitution.
    # {{previous_turns_description}} switches wording based on
    # historic_images_count so the model knows whether to expect historic
    # screenshots interleaved with the text history.
    K = config.get("historic_images_count", 0)
    if K > 0:
        previous_turns_description = (
            "A summary of your recent turns. Each turn shows its `actions`, `reasoning`, "
            "and a `did this turn succeed?` line. The screenshot the agent saw at the "
            f"START of each of the last {K} turn(s) — BEFORE the actions listed below "
            "it were pressed — is included as a labeled image at the END of the user "
            "message. Use those historic screenshots to verify what actually happened "
            "on screen, compare against your prior reasoning, and ground predictions "
            "about position and screen-state changes."
        )
    else:
        previous_turns_description = (
            "A summary of your recent turns. Each turn shows its `actions`, `reasoning`, "
            "and a `did this turn succeed?` line. Use this history to avoid repeating "
            "failed approaches, track progress, and estimate how ambitious of a plan "
            "you can try to execute based on earlier turns' successes/failures."
        )

    raw_prompt = config.get("system_prompt", "You are an AI agent playing a Pokemon game.")
    system_prompt = fill_prompt(
        raw_prompt,
        game_name="Pokemon FireRed",
        button_list=", ".join(config.get("valid_inputs", [])),
        current_task=config.get("task", {}).get("goal", "Play the game."),
        previous_turns_description=previous_turns_description,
    )

    # The Player drives the game purely through its structured output
    # (GameAction); it has no agent tools. (The former ask_vlm follow-up tool
    # was removed along with the separate-VLM vision mode.)

    # TaskMaster gate: when enabled, the Player output schema gains the optional
    # `return_to_taskmaster` discriminator field (GameAction) and a budget-aware
    # output validator is attached. When disabled, the model-facing schema is
    # `_LegacyGameAction` — byte-for-byte the pre-TaskMaster GameAction — and no
    # validator is attached, so the legacy single-agent path is unchanged.
    tm_enabled = bool(config.get("task_master", {}).get("enabled", False))
    OutputModel = GameAction if tm_enabled else _LegacyGameAction

    # Output mode: registry can override per-model. Default "tool" path uses
    # tool_choice="required" — strongest schema enforcement, broadest support.
    # "native_json" → response_format json_schema. Use for models whose
    #   OpenRouter providers don't expose tool_choice (e.g. qwen/qwen3.6-plus).
    # "prompted" → text + parse. Last-resort for models without either.
    output_mode = (resolved.get("output_mode") or "tool").lower()
    if output_mode == "native_json":
        output_type = NativeOutput(OutputModel)
    elif output_mode == "prompted":
        # Pydantic AI's default prompted template ("Don't include any text or
        # Markdown fencing before or after") is too weak for some models — Qwen3.6-Plus
        # consistently emitted ```json{...}``` despite it, and Pydantic AI's
        # fence stripper is asymmetric (eats the leading `{` with the opening
        # fence, leaves the trailing fence intact). This template gives explicit
        # boundary chars + a concrete example, which models follow more reliably.
        # NOTE: Pydantic AI calls .format(schema=...) on this template, so
        # literal { and } in the body must be doubled to {{ and }} (except the
        # real {schema} slot). Otherwise Python's format parser reads them as
        # field markers and raises before the model ever sees the prompt.
        prompted_template = (
            "Always respond with a JSON object that's compatible with this schema:\n\n"
            "{schema}\n\n"
            "CRITICAL formatting rules:\n"
            "- Your response MUST start with the literal character `{{` and end with `}}`.\n"
            "- DO NOT prepend ```json, ```, or the word `json` before the JSON.\n"
            "- DO NOT append ``` after the JSON.\n"
            "- DO NOT include any prose, commentary, or whitespace outside the JSON.\n"
            "- Output ONLY the raw JSON object.\n\n"
            "Example of a correctly-formatted response (structure only, values are illustrative):\n"
            "{{\"inputs\":[\"a\"],\"reasoning\":\"...\",\"last_turn_succeeded\":null,\"memory_updates\":\"none\"}}"
        )
        # When TaskMaster is enabled, show a second example covering the handoff
        # variant so prompted-mode models know the optional discriminator field
        # exists and what shape it takes. The legacy example above is unchanged.
        if tm_enabled:
            prompted_template += (
                "\nExample of a hand-back-to-TaskMaster response (the current task is done — "
                "leave `inputs` empty and set `return_to_taskmaster`):\n"
                "{{\"inputs\":[],\"reasoning\":\"...\",\"last_turn_succeeded\":true,\"memory_updates\":\"none\","
                "\"return_to_taskmaster\":{{\"self_assessment\":\"succeeded\",\"task_summary\":\"...\"}}}}"
            )
        output_type = PromptedOutput(OutputModel, template=prompted_template)
    elif output_mode == "tool":
        output_type = OutputModel
    else:
        raise ValueError(
            f"Unknown output_mode {output_mode!r} in registry. "
            "Expected 'tool', 'native_json', or 'prompted'."
        )

    # retries=5 instead of the previous 3 — prompted-output models occasionally
    # need extra rounds to nail the JSON shape, and the retry cost is cheap
    # vs. losing the whole turn.
    #
    # When TaskMaster is enabled we also give the OUTPUT validator its own retry
    # budget (output_retries=3) so the budget-boundary rejection below gets up to
    # three in-conversation retries to coax a handoff out of the model, per the
    # "ModelRetry over silent-sanitize" rule. When disabled this stays None and
    # the agent's behavior is identical to before.
    agent_kwargs: dict[str, Any] = dict(
        model=model,
        system_prompt=system_prompt,
        deps_type=AgentDeps,
        output_type=output_type,
        tools=[],
        retries=5,
    )
    if tm_enabled:
        agent_kwargs["output_retries"] = 3
    agent = Agent(**agent_kwargs)

    # Budget-boundary output validator. Only attached when TaskMaster is enabled,
    # so the legacy single-agent path has no validator at all. When the Player has
    # used its full per-task turn budget, the ONLY acceptable output is a handoff
    # (`return_to_taskmaster` set) — an interact-with-game output is rejected with
    # an in-conversation ModelRetry rather than silently sanitized. The validator
    # reads the current-task turn count + budget off RunContext deps, which
    # turn.py refreshes each turn.
    if tm_enabled:
        @agent.output_validator
        def _enforce_budget_handoff(
            ctx: RunContext[AgentDeps], output: GameAction
        ) -> GameAction:
            budget = ctx.deps.max_turns_per_task
            used = ctx.deps.current_task_turn
            if (
                budget > 0
                and used >= budget
                and getattr(output, "return_to_taskmaster", None) is None
            ):
                raise ModelRetry(
                    "You cannot take another in-game action on this task right now. "
                    "Hand control back to TaskMaster: set `return_to_taskmaster` with "
                    "your self_assessment and a task_summary. Button presses are not "
                    "accepted here."
                )
            return output

    # Fallback models (tried in order if primary fails)
    fallback_models = config.get("llm_fallback_models", []) or []

    return agent, model_settings, fallback_models
