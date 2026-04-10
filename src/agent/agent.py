"""Agent turn loop using Pydantic AI with OpenRouter."""

import json
from dataclasses import dataclass
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext, Tool
from pydantic_ai.models.openai import OpenAIModel

Button = Literal["up", "down", "left", "right", "a", "b", "start", "select"]

# Apply patches before creating any models
from src.core.patches import apply_patches
apply_patches()

from src.emulator import EmulatorClient, VisionPipeline, OCRRunner
from src.core import RunLogger, StateManager


# --- Output models ---

class GameAction(BaseModel):
    """Output: button presses, observations, reasoning, and memory updates."""
    inputs: list[Button] = Field(
        description="Button presses to send to the game. Aim for 6-12 for predictable actions (walking, dialogue). Use fewer (1-5) when the outcome is uncertain (entering a new room, using a move in battle).",
    )
    i_saw: str = Field(
        description=(
            "Detailed description of everything you observe on screen. "
            "Include: screen type (overworld/menu/battle), all visible text and dialogue, "
            "positions of key objects, NPCs, doors, and exits using coordinates, "
            "obstacles and walls, your player's position and facing direction, "
            "menu cursor position and options, Pokemon HP/levels, and any changes from last turn."
        ),
    )
    i_did: str = Field(
        description=(
            "Describe what you did, why, and how it ties in to the plan. "
            "E.g. 'Chained [left, up, left, up, left, up] to enter the Gym door at (-2, 1), "
            "with the goal of getting closer to our sub-goal of beating the Gym leader. "
            "Overestimated by a few tiles since the exact distance is hard to judge from the screenshot.' "
            "Also mention any memory updates you made and why. "
            "E.g. 'Updated party.pikachu since we successfully caught the Pikachu.'"
        ),
    )
    i_expect: str = Field(
        description=(
            "What you expect to see on the exact screen next turn. Be specific — next turn will compare the actual screen against this to judge if this turn succeeded. "
            "E.g. 'Should have entered the Gym, standing inside at the entrance.' "
            "Or: 'Thunderbolt should deal super-effective damage since the opponent is a water type. Expect their HP to drop below 20% or even 0%.'"
        ),
    )
    memory_updates: str = Field(
        description=(
            "JSON object with keys to update in the memory dictionary (dot notation for nesting). "
            "Only include changed keys — others stay. Set a key to \"\" to delete it. "
            "Example: '{\"current_location\": \"Viridian City\", \"party.pikachu.hp\": \"28/40\"}'. "
            "Only update after you have confirmed the new information on screen, NOT when you expect a change to happen. "
            "Write \"none\" only if absolutely nothing changed this turn."
        ),
    )


# --- Agent dependencies (passed to tools via RunContext) ---

@dataclass
class AgentDeps:
    """Dependencies available to all agent tools."""
    # Shared infrastructure (immutable per run)
    emulator: EmulatorClient
    state: StateManager
    vision: VisionPipeline
    logger: RunLogger
    ocr: Optional[OCRRunner] = None

    # Per-turn state (mutable)
    current_screenshot: Any = None  # PIL Image
    turn_number: int = 0
    agent_id: str = "agent_0"



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

    # Build model settings with thinking/reasoning if configured
    thinking_config = config.get("thinking")
    model_settings = None
    if thinking_config:
        from pydantic_ai.models.openai import OpenAIModelSettings
        model_settings = OpenAIModelSettings(
            extra_body={"reasoning": thinking_config},
        )

    # Resolve system prompt with template substitution
    raw_prompt = config.get("system_prompt", "You are an AI agent playing a Pokemon game.")
    system_prompt = fill_prompt(
        raw_prompt,
        game_name="Pokemon FireRed",
        button_list=", ".join(config.get("valid_inputs", [])),
        current_task=config.get("task", {}).get("goal", "Play the game."),
    )

    # Build tool list based on config toggles
    tool_config = config.get("tools", {})
    all_tools = [
        ("ask_vlm", tool_ask_vlm),
    ]
    tools = [
        Tool(fn, takes_ctx=True, name=name)
        for name, fn in all_tools
        if tool_config.get(name, True)
    ]

    agent = Agent(
        model=model,
        system_prompt=system_prompt,
        deps_type=AgentDeps,
        output_type=GameAction,
        tools=tools if tools else [],
        retries=3,
    )

    # Fallback models (tried in order if primary fails)
    fallback_models = config.get("llm_fallback_models", []) or []

    return agent, model_settings, fallback_models


# --- Tool implementations ---

async def tool_ask_vlm(ctx: RunContext[AgentDeps], question: str) -> str:
    """Ask the vision model a follow-up question about the current game screenshot."""
    ctx.deps.logger.log_tool_call("ask_vlm", {"question": question}, ctx.deps.agent_id)
    ctx.deps.logger.log_vlm_request(question, ctx.deps.agent_id)
    answer = ctx.deps.vision.ask_vlm(ctx.deps.current_screenshot, question)
    ctx.deps.logger.log_vlm_response(answer, ctx.deps.agent_id)
    ctx.deps.logger.log_tool_response("ask_vlm", answer, ctx.deps.agent_id)
    return answer
