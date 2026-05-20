"""Monkey-patches for Pydantic AI 0.8.x to support OpenRouter features.

1. Reasoning: OpenRouter returns reasoning in message.reasoning, but Pydantic AI
   only checks for DeepSeek's reasoning_content. We capture it before model_validate.

2. Cost: OpenRouter returns cost in response.usage.model_extra["cost"], but
   Pydantic AI doesn't propagate this to provider_details. We inject it.

3. JSON fence stripping: Pydantic AI's strip_markdown_fences only handles the
   exact ```json\\n{...}\\n``` shape. Real-world LLM output (especially Qwen3.6-Plus
   under PromptedOutput) drops the opening `{`, drops first-key quotes, prepends
   prose, or wraps in non-standard fences. We replace it with a robust extractor.
"""

import json
import logging
import re

logger = logging.getLogger(__name__)


def _slice_outer_braces(text: str) -> str | None:
    """Return the substring from the first '{' through its matching '}',
    counting brace depth and respecting strings + escapes. None if no balanced
    span exists."""
    start = text.find('{')
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        c = text[i]
        if escape:
            escape = False
            continue
        if c == '\\' and in_string:
            escape = True
            continue
        if c == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def _repair_unbraced_json(text: str) -> str | None:
    """Try to recover JSON when the model dropped the opening `{`. Strips
    fence/language remnants, prepends `{`, appends `}` if missing, repairs a
    missing opening `"` on the first key. Returns the repaired string only if
    json.loads accepts it; otherwise None."""
    s = text.strip()
    s = re.sub(r'^```\w*\s*\n?', '', s)
    s = re.sub(r'\n?```\s*$', '', s)
    s = s.strip()
    # Strip stray "json" language tag the fence regex may have left behind.
    if s.lower().startswith('json'):
        rest = s[4:].lstrip()
        if rest.startswith(('{', '"')) or re.match(r'^[a-zA-Z_]\w*"\s*:', rest):
            s = rest
    if s.startswith('{'):
        sliced = _slice_outer_braces(s)
        if sliced:
            try:
                json.loads(sliced)
                return sliced
            except (json.JSONDecodeError, ValueError):
                pass
    # Bare `key":` start → missing opening quote on the first key.
    if re.match(r'^[a-zA-Z_]\w*"\s*:', s):
        s = '"' + s
    candidate = s if s.endswith('}') else s + '}'
    candidate = '{' + candidate if not candidate.startswith('{') else candidate
    try:
        json.loads(candidate)
        return candidate
    except (json.JSONDecodeError, ValueError):
        return None


def _robust_strip_markdown_fences(text: str) -> str:
    """Drop-in replacement for pydantic_ai._utils.strip_markdown_fences.
    Tries, in order: clean JSON; balanced brace slice; fenced block;
    unbraced repair. Falls back to original text so the JSON parser still
    raises an honest error if nothing recovers."""
    if not text:
        return text
    if text.lstrip().startswith('{'):
        sliced = _slice_outer_braces(text)
        if sliced:
            try:
                json.loads(sliced)
                return sliced
            except (json.JSONDecodeError, ValueError):
                pass
    sliced = _slice_outer_braces(text)
    if sliced:
        try:
            json.loads(sliced)
            return sliced
        except (json.JSONDecodeError, ValueError):
            pass
    fence_match = re.search(r'```(?:\w+)?\s*\n?(.*?)\n?```', text, re.DOTALL)
    if fence_match:
        inner = fence_match.group(1).strip()
        inner_sliced = _slice_outer_braces(inner)
        if inner_sliced:
            try:
                json.loads(inner_sliced)
                return inner_sliced
            except (json.JSONDecodeError, ValueError):
                pass
        repaired = _repair_unbraced_json(inner)
        if repaired:
            return repaired
    repaired = _repair_unbraced_json(text)
    if repaired:
        return repaired
    return text


def patch_strip_markdown_fences():
    """Replace Pydantic AI's bare-bones JSON-fence stripper with a robust one
    that handles dropped braces, missing first-key quotes, and prose around
    fenced blocks."""
    try:
        from pydantic_ai import _utils
        _utils.strip_markdown_fences = _robust_strip_markdown_fences
        # _output.py imports the symbol at module-load time, so rebind there too.
        from pydantic_ai import _output
        _output._utils.strip_markdown_fences = _robust_strip_markdown_fences
        logger.debug("Patched pydantic_ai strip_markdown_fences with robust extractor")
    except Exception as e:
        logger.warning(f"Failed to patch strip_markdown_fences: {e}")


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


def patch_openai_service_tier():
    """OpenAI's ChatCompletion schema restricts service_tier to a fixed Literal
    ('auto'|'default'|'flex'|'scale'|'priority'). OpenRouter sends 'standard'
    for some providers, which fails validation before _process_response runs.
    Loosen the field annotation to Optional[str] so any value passes."""
    try:
        from typing import Optional
        from openai.types.chat import ChatCompletion
        ChatCompletion.model_fields['service_tier'].annotation = Optional[str]
        ChatCompletion.model_rebuild(force=True)
        logger.debug("Patched ChatCompletion.service_tier to Optional[str]")
    except Exception as e:
        logger.warning(f"Failed to patch service_tier: {e}")


def apply_patches():
    """Apply all monkey-patches."""
    patch_openai_service_tier()
    patch_openai_model_response()
    patch_strip_markdown_fences()
