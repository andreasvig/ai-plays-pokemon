"""Monkey-patches for Pydantic AI 0.8.x to support OpenRouter features.

1. Reasoning: OpenRouter returns reasoning in message.reasoning, but Pydantic AI
   only checks for DeepSeek's reasoning_content. We capture it before model_validate.

2. Cost: OpenRouter returns cost in response.usage.model_extra["cost"], but
   Pydantic AI doesn't propagate this to provider_details. We inject it.
"""

import logging

logger = logging.getLogger(__name__)


def patch_openai_model_response():
    """Patch OpenAIChatModel._process_response for OpenRouter reasoning + cost."""
    try:
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.messages import ThinkingPart

        _original = OpenAIChatModel._process_response

        def _patched(self, response):
            # Grab reasoning from the raw response BEFORE model_validate strips it
            reasoning_text = None
            try:
                choice = response.choices[0]
                reasoning_text = getattr(choice.message, 'reasoning', None)
            except (IndexError, AttributeError):
                pass

            # Grab cost from usage.model_extra BEFORE model_validate strips it
            openrouter_cost = None
            try:
                usage_extra = getattr(response.usage, 'model_extra', None) or {}
                openrouter_cost = usage_extra.get('cost')
            except (AttributeError, TypeError):
                pass

            # Call original
            result = _original(self, response)

            # Inject ThinkingPart if we found reasoning
            if reasoning_text and isinstance(reasoning_text, str) and reasoning_text.strip():
                result.parts.insert(0, ThinkingPart(content=reasoning_text))

            # Inject cost into provider_details
            if openrouter_cost is not None:
                if result.provider_details is None:
                    result.provider_details = {}
                result.provider_details['cost'] = float(openrouter_cost)

            return result

        OpenAIChatModel._process_response = _patched
        logger.debug("Patched OpenAIChatModel for OpenRouter reasoning + cost")
    except Exception as e:
        logger.warning(f"Failed to patch: {e}")


def apply_patches():
    """Apply all monkey-patches."""
    patch_openai_model_response()
