"""Player live-HUD thinking extraction.

The Player's live spectate HUD is built solely from ``_emit_node_events`` (the
node iteration in ``_run_agent_iter``), separate from the post-hoc trace
serializer the TaskMaster uses. Some models (e.g. Moonshot Kimi K2.7 Code)
return their chain-of-thought in OpenRouter's top-level ``reasoning`` field —
which pydantic-ai stows in ``ModelResponse.provider_details``, NOT as a
structured ``ThinkingPart`` in ``response.parts``. Before the fix the live HUD
only emitted thinking from ``ThinkingPart``, so the Player's thinking silently
vanished for those models while the TaskMaster (serialized path, which reads
provider_details) still showed it.

These tests pin the fallback:
  1. provider_details['reasoning'] with NO ThinkingPart → one llm_thinking event,
     emitted BEFORE the llm_output so thinking precedes output in the stream.
  2. A ThinkingPart present → that wins; the provider_details reasoning is NOT
     double-logged.
  3. Neither present → no llm_thinking event.
"""

import os
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("OPENROUTER_API_KEY", "test-key-not-used")

from pydantic_ai.messages import ThinkingPart, ToolCallPart  # noqa: E402

from src.agent.turn import TurnManager  # noqa: E402


class _RecordingLogger:
    def __init__(self):
        self.events = []

    def log_custom(self, event_type, data):
        self.events.append((event_type, data))


def _mgr():
    mgr = TurnManager({
        "openrouter_api_key": "test-key-not-used",
        "llm_model": "stub/player-model",
        "system_prompt": "play",
    })
    mgr.logger = _RecordingLogger()
    return mgr


def _deps():
    return SimpleNamespace(turn_number=4, agent_id="agent_0")


def _final_result_part():
    return ToolCallPart(
        tool_name="final_result",
        args={"inputs": ["a"], "reasoning": "press A", "memory_updates": "none"},
    )


def _node(parts, provider_details):
    response = SimpleNamespace(parts=parts, provider_details=provider_details)
    return SimpleNamespace(model_response=response)


def test_openrouter_reasoning_field_is_emitted_as_thinking():
    mgr = _mgr()
    node = _node([_final_result_part()], {"reasoning": "deep chain of thought"})

    mgr._emit_node_events(node, _deps())

    thinking = [d for (t, d) in mgr.logger.events if t == "llm_thinking"]
    assert len(thinking) == 1
    assert thinking[0]["content"] == "deep chain of thought"
    assert thinking[0]["turn"] == 4

    # Thinking must precede the output in the live stream (chronological order).
    types = [t for (t, _d) in mgr.logger.events]
    assert types.index("llm_thinking") < types.index("llm_output")


def test_thinking_part_wins_no_double_log():
    mgr = _mgr()
    node = _node(
        [ThinkingPart(content="structured CoT"), _final_result_part()],
        {"reasoning": "structured CoT"},  # same content via the other channel
    )

    mgr._emit_node_events(node, _deps())

    thinking = [d for (t, d) in mgr.logger.events if t == "llm_thinking"]
    assert len(thinking) == 1
    assert thinking[0]["content"] == "structured CoT"


def test_no_reasoning_anywhere_emits_no_thinking():
    mgr = _mgr()
    node = _node([_final_result_part()], {})

    mgr._emit_node_events(node, _deps())

    assert not [t for (t, _d) in mgr.logger.events if t == "llm_thinking"]
