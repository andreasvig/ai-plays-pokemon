"""A run must record WHICH provider served each turn and whether it thought.

Two gaps, closed together on 2026-08-02 while auditing whether each model is
called in the right output mode:

* **Serving provider.** OpenRouter returns it top-level as ``response.provider``
  ("Parasail", "Google", "Amazon Bedrock", ...). pydantic-ai's model_validate
  drops unknown top-level fields, and ``patches.py`` only rescued ``cost`` — so
  ``_extract_provider_from_messages`` had nothing to read and every
  ``agent_error`` across the entire run archive recorded ``provider: ''``.
  This matters because capability varies by ENDPOINT on the same model: 3 of
  gemma-4-31b's 18 endpoints expose no tools at all, and the harness re-rolls
  provider routing on every retry attempt.

* **Reasoning tokens.** ``turn_usage`` recorded request/response/total tokens
  and cost but not ``reasoning_tokens``, so "did this model actually think?"
  was only answerable via the ``llm_thinking`` event — which needs the provider
  to return a human-readable SUMMARY. The two come apart: a live gpt-5.6-sol
  probe returned 183 reasoning tokens with zero summary characters.

Live-verified 2026-08-02 through the real pydantic-ai path: kimi-k2.7-code via
Parasail reported 812 reasoning tokens, gemini-3.5-flash at effort=high 1229
and at effort=minimal 0.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("OPENROUTER_API_KEY", "test-key-not-used")

from openai.types.chat import ChatCompletion  # noqa: E402
from openai.types.chat.chat_completion import Choice  # noqa: E402
from openai.types.chat.chat_completion_message import ChatCompletionMessage  # noqa: E402
from openai.types.completion_usage import CompletionUsage  # noqa: E402

from pydantic_ai.messages import ModelResponse, TextPart  # noqa: E402

from src.agent.turn import (  # noqa: E402
    _extract_provider_from_messages,
    _usage_event,
)
from src.core.patches import apply_patches  # noqa: E402


def _response(provider=None, cost=None):
    """A ChatCompletion shaped like OpenRouter's, with `provider` top-level."""
    msg = ChatCompletionMessage.construct(role="assistant", content='{"ok":1}')
    choice = Choice.construct(index=0, message=msg, finish_reason="stop")
    usage_kwargs = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    if cost is not None:
        usage_kwargs["cost"] = cost
    usage = CompletionUsage.construct(**usage_kwargs)
    extra = {}
    if provider is not None:
        extra["provider"] = provider
    return ChatCompletion.construct(
        id="x", choices=[choice], created=0, model="m",
        object="chat.completion", usage=usage, **extra,
    )


def _model():
    from pydantic_ai.models.openai import OpenAIChatModel

    return OpenAIChatModel("moonshotai/kimi-k2.7-code", provider="openrouter")


# --- the patch: provider survives model_validate ----------------------------


def test_serving_provider_lands_in_provider_details():
    apply_patches()
    result = _model()._process_response(_response(provider="Parasail"))
    assert (result.provider_details or {}).get("provider") == "Parasail"


def test_provider_and_cost_coexist():
    # Cost rescue predates this; adding provider must not displace it.
    apply_patches()
    result = _model()._process_response(_response(provider="Together", cost=0.0042))
    details = result.provider_details or {}
    assert details.get("provider") == "Together"
    assert details.get("cost") == 0.0042


def test_absent_provider_leaves_no_key():
    # A response with no `provider` must not invent an empty one — downstream
    # distinguishes "unknown" from a named endpoint.
    apply_patches()
    result = _model()._process_response(_response(provider=None, cost=0.001))
    assert "provider" not in (result.provider_details or {})


# --- the extractor ----------------------------------------------------------


def test_extract_provider_reads_provider_details():
    msg = ModelResponse(parts=[TextPart(content="x")])
    msg.provider_details = {"cost": 0.01, "provider": "DeepInfra"}
    assert _extract_provider_from_messages([msg]) == "DeepInfra"


def test_extract_provider_does_not_fall_back_to_model_name():
    """Mutation control for the removed last-resort branch.

    The helper used to return ``msg.model_name`` when it found nothing else.
    On OpenRouter that is the model slug, whose prefix is the model's AUTHOR,
    not the endpoint that served the request — Moonshot's own model is routed
    to DeepInfra, Together, Fireworks and a dozen others. If this assertion
    starts failing, the branch is back and the logs are naming a provider that
    never answered.
    """
    msg = ModelResponse(parts=[TextPart(content="x")], model_name="moonshotai/kimi-k2.7-code")
    msg.provider_details = {"cost": 0.01}
    assert _extract_provider_from_messages([msg]) == ""


def test_extract_provider_empty_when_no_responses():
    assert _extract_provider_from_messages([]) == ""


# --- the turn_usage payload -------------------------------------------------


class _Usage:
    def __init__(self, details):
        self.request_tokens = 100
        self.response_tokens = 50
        self.total_tokens = 150
        self.details = details


def test_usage_event_carries_reasoning_tokens_and_provider():
    ev = _usage_event(7, _Usage({"reasoning_tokens": 812}), 0.0123, "Parasail")
    assert ev["turn"] == 7
    assert ev["reasoning_tokens"] == 812
    assert ev["provider"] == "Parasail"
    # The pre-existing fields are unchanged — consumers read this event as a
    # dict passthrough (src/core/event_parsing.py:101).
    assert ev["request_tokens"] == 100
    assert ev["response_tokens"] == 50
    assert ev["total_tokens"] == 150
    assert ev["cost_usd"] == 0.0123


def test_reasoning_tokens_zero_is_preserved_as_zero():
    """A measured zero (gemini-3.5-flash at effort=minimal) is real data."""
    ev = _usage_event(1, _Usage({"reasoning_tokens": 0}), 0.0, "Google")
    assert ev["reasoning_tokens"] == 0


def test_absent_reasoning_tokens_is_none_not_zero():
    """Novita and StepFun serve step-3.7-flash without reporting the field at
    all, while DeepInfra reports ~2600 on identical output. Reporting 0 there
    would read as "this tier does not think" — a routing artifact promoted to
    a finding about the model."""
    ev = _usage_event(1, _Usage({"audio_tokens": 0}), 0.0, "Novita")
    assert ev["reasoning_tokens"] is None
    assert ev["reasoning_tokens"] != 0


def test_usage_event_tolerates_missing_details():
    ev = _usage_event(1, _Usage(None), 0.0, "")
    assert ev["reasoning_tokens"] is None
