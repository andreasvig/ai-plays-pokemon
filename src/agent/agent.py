"""Agent turn loop using Pydantic AI with OpenRouter."""

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Literal, Optional

from openai import OpenAI
from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext, Tool
from pydantic_ai.models.openai import OpenAIModel

Button = Literal["up", "down", "left", "right", "a", "b", "start", "select", "lb", "rb"]

# Apply patches before creating any models
from src.core.patches import apply_patches
apply_patches()

from src.emulator import EmulatorClient, VisionPipeline, OCRRunner
from src.core import RunLogger, StateManager


# --- Output models ---

class GameAction(BaseModel):
    """Output: send button presses to the game."""
    inputs: list[Button]
    i_saw: str
    i_thought: str
    i_did: str
    memory_updates: str = Field(
        description=(
            "JSON object string with keys to update in the memory dictionary. "
            "You MUST provide updates when: location changed, required keys are "
            "missing (location, goal, party, story_progress, map), or you learned "
            "new info. Only keys you include are changed — others stay. "
            'Example: \'{"location": "1F, Player\'s house", "goal": "Exit house"}\' '
            "Write \"none\" ONLY if absolutely nothing changed this turn."
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

    def for_subtask(self, agent_id: str) -> "AgentDeps":
        """Create deps for a sub-agent: shared infra, fresh per-turn state."""
        return AgentDeps(
            emulator=self.emulator,
            state=self.state,
            vision=self.vision,
            logger=self.logger,
            ocr=self.ocr,
            turn_number=0,
            agent_id=agent_id,
        )


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
        current_task=config.get("top_level_task", "Play the game."),
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
