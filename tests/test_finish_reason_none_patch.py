"""OpenRouter finish_reason=None must not crash the run.

Some OpenRouter providers (notably certain Gemini endpoints) return
``finish_reason: null``. OpenAI's ChatCompletion schema types finish_reason as a
strict Literal, so pydantic-ai's re-validation in ``_process_response`` raises
``UnexpectedModelBehavior`` and kills the whole run — observed mid-handoff on a
gemini-3.1-flash-lite run (2026-06-19). The patch coerces a missing
finish_reason to 'stop' (the message is complete) before that validation.

This pins: with patches applied, a finish_reason=None response is processed into
a normal ModelResponse instead of raising.
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

from src.core.patches import apply_patches  # noqa: E402


def _response_with_finish_reason(fr):
    msg = ChatCompletionMessage.construct(role="assistant", content='{"task":"x"}')
    choice = Choice.construct(index=0, message=msg, finish_reason=fr)
    usage = CompletionUsage.construct(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    return ChatCompletion.construct(
        id="x", choices=[choice], created=0, model="m", object="chat.completion", usage=usage
    )


def _model():
    from pydantic_ai.models.openai import OpenAIChatModel

    return OpenAIChatModel("google/gemini-3.1-flash-lite-preview", provider="openrouter")


def test_finish_reason_none_is_coerced_not_crashed():
    apply_patches()
    model = _model()
    resp = _response_with_finish_reason(None)

    # Before the patch this raised UnexpectedModelBehavior on the strict Literal.
    result = model._process_response(resp)

    assert result is not None
    assert result.parts  # the message content survived
    # The raw response's finish_reason was coerced to a valid literal.
    assert resp.choices[0].finish_reason == "stop"


def test_normal_finish_reason_untouched():
    apply_patches()
    model = _model()
    resp = _response_with_finish_reason("stop")
    result = model._process_response(resp)
    assert result is not None
    assert resp.choices[0].finish_reason == "stop"
