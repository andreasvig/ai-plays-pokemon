"""Lossless coercion for models that mis-encode nested tool-call arguments.

Some OpenRouter models (observed: ``xiaomi/mimo-v2.5``) call the structured-
output tool correctly but serialize **nested object arguments as JSON strings**
instead of nested JSON objects, and emit the literal strings ``"None"`` /
``"null"`` for a null optional field. pydantic's strict validation then rejects
the (semantically correct) value with ``Input should be an object`` and the run
dies after exhausting ModelRetry — the model understands the schema from its
reasoning but cannot stop stringifying (see the 2026-06-17 mimo TaskMaster
cold-start diagnosis).

This is an *encoding* mistake, not bad content, so decoding it is lossless
coercion — not the kind of silent-sanitize that the "ModelRetry over sanitize"
rule warns against (ModelRetry demonstrably cannot fix a systematic
serialization quirk). Models that already encode correctly hand pydantic a
dict/None here, so this is a pure pass-through for them.

Use as a ``mode="before"`` field validator on nested-object fields:

    _coerce_task = field_validator("task", mode="before")(coerce_stringified_object)
"""

import json
from typing import Any

# Literal string tokens a model may emit for a JSON null (case-insensitive).
_NULL_TOKENS = {"", "none", "null", "nil"}


def coerce_stringified_object(value: Any) -> Any:
    """Decode a stringified JSON object/null back to a dict/None.

    Non-string input is returned untouched (the common, correct case). A string
    that is a null-token becomes ``None``; a string that parses as JSON becomes
    the parsed value; anything else is returned unchanged so pydantic raises its
    normal, informative error.
    """
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if stripped.lower() in _NULL_TOKENS:
        return None
    try:
        return json.loads(stripped)
    except (ValueError, TypeError):
        return value


def coerce_object_to_json_string(value: Any) -> Any:
    """Serialize a nested object/array back to a JSON string for a str-typed field.

    The mirror image of ``coerce_stringified_object``. Some fields are typed
    ``str`` but described to the model as "a JSON object" because a downstream
    consumer does ``json.loads`` on them (e.g. ``GameAction.memory_updates``,
    which ``turn.py`` parses into memory-key updates). A capable model
    (observed: ``anthropic/claude-sonnet-5``, 2026-07-01, at Oak's Lab) reads
    "JSON object" literally and emits a REAL nested object ``{"notes": "..."}``
    instead of a JSON *string*, so pydantic's ``str`` field rejects it
    (``string_type``) and the run burns ModelRetry attempts before recovering.

    Re-serializing is lossless — the downstream ``json.loads`` recovers exactly
    what the model meant, and a correctly-encoded string passes through
    untouched. ``None`` maps to ``"none"`` (the schema's no-update sentinel).
    """
    if value is None:
        return "none"
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            return value
    return value
