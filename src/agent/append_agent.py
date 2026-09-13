"""Append-only OpenRouter conversation with transactional, explicit handovers.

Uses the HTTP boundary directly: the legacy PydanticAI adapter drops opaque
reasoning metadata. No legacy model/serializer patches are changed here.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any, Literal
import uuid

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.core.prompts import fill_prompt
from src.agent.coerce import coerce_stringified_object, drop_null_extras
from src.agent.provider_profiles import project_history, router_diagnostics, output_json_text
from src.agent.backoff import RetryPolicy, classify, describe, parse_retry_after


class PlayAction(BaseModel):
    """Gameplay output since config-5.1: the buttons and the visible reasoning.

    The reasoning is where the model compares the screen with its previous
    prediction and ends with the next one; there is no separate verdict field.
    Decision 2026-09-08 (Andreas): with the whole conversation appended, the
    previous turn's prediction is already in context, so a forced boolean grade
    added schema quirks (Muse's stringified null, mimo's "None") and nothing
    downstream scored on it. `self_grade: true` in a config selects
    GradedPlayAction instead, which is what config-5.0 and earlier emit.
    """
    model_config = ConfigDict(extra="forbid", strict=True)
    inputs: list[Literal["up", "down", "left", "right", "a", "b", "start", "select", "wait"]]
    reasoning: str = Field(min_length=1)


class GradedPlayAction(PlayAction):
    """config-5.0 gameplay output: PlayAction plus the previous-turn verdict."""
    last_turn_succeeded: bool | None

    # Some endpoints encode non-string tool-call arguments as JSON *strings*:
    # meta/muse-spark-1.3 returned `"last_turn_succeeded":"null"` on turn 1
    # (2026-09-07, three identical attempts, run died before its first action).
    # Decoding "null"/"true"/"false" is lossless, the same 2026-06-17 rule the
    # legacy agent applies via src/agent/coerce.py; a correctly typed value
    # passes through untouched and any other string still fails strict validation.
    _coerce_last_turn = field_validator("last_turn_succeeded", mode="before")(coerce_stringified_object)


class Handover(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    continuation_summary: str = Field(min_length=1)
    memory: dict[str, Any]

    # Same rule for a stringified memory object (the mimo shape from 2026-06-17).
    _coerce_memory = field_validator("memory", mode="before")(coerce_stringified_object)


class ContinuityError(RuntimeError):
    pass


class SpendLimitReached(RuntimeError):
    pass


# Synthetic assistant message that closes a screenshot turn on `final_turn_text_only` profiles.
# The trace builder folds it back into the observation, so keep the text constant.
TURN_ACK = "Observed."


class ProviderTransportError(RuntimeError):
    """The gateway or upstream failed mid-request and returned no completion.

    Kept apart from output-format errors so error tallies separate provider
    outages from model formatting defects (GLM turn 1 in the 2026-09-06 samples
    surfaced as "Expected exactly one gameplay call" after a 181 s network_error).
    """


class ProviderRequestError(RuntimeError):
    def __init__(self, status, response_body, headers=None):
        self.status = status
        self.response_body = response_body
        # Seconds the provider asked us to wait (Retry-After / X-RateLimit-Reset),
        # None when it named none. The retry loop treats it as a floor.
        self.retry_after_s = parse_retry_after(headers)
        super().__init__(f"Provider HTTP {status}: {json.dumps(response_body)[:2000]}")


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with temp.open("w") as f:
        json.dump(value, f, ensure_ascii=False, allow_nan=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)


class ReplayStore:
    """Lossless JSON, with immutable image data URLs deduplicated on disk."""
    def __init__(self, root: Path):
        self.root = root
        (root / "assets").mkdir(parents=True, exist_ok=True)

    def pack(self, value):
        if isinstance(value, str) and value.startswith("data:image/"):
            key = hashlib.sha256(value.encode()).hexdigest()
            path = self.root / "assets" / key
            if not path.exists():
                path.write_text(value)
            return {"$image_asset": key}
        if isinstance(value, dict):
            return {k: self.pack(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.pack(v) for v in value]
        return value

    def unpack(self, value):
        if isinstance(value, dict) and set(value) == {"$image_asset"}:
            key = value["$image_asset"]
            if not isinstance(key, str) or len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
                raise ValueError("Invalid replay asset reference")
            data = (self.root / "assets" / key).read_text()
            if hashlib.sha256(data.encode()).hexdigest() != key:
                raise ContinuityError("Replay image digest mismatch")
            return data
        if isinstance(value, dict):
            return {k: self.unpack(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.unpack(v) for v in value]
        return value

    def write(self, name, value):
        atomic_json(self.root / name, self.pack(value))


def approx_tokens(value, image_tokens: int) -> int:
    """Rough token count for the compaction trigger, not for billing.

    Roughly four bytes per text token, a fixed reserve per image, and a model's
    raw reasoning counted once even when the gateway returns it under both
    ``reasoning`` and ``reasoning_details``. The earlier byte count treated a
    22 KB screenshot and 37 KB of duplicated Qwen reasoning as ~60k tokens and
    compacted every turn while the real prompt was 10k tokens.
    """
    if isinstance(value, dict):
        if value.get("type") == "image_url":
            return image_tokens
        skip = "reasoning" if value.get("reasoning_details") else None
        return sum(approx_tokens(v, image_tokens) for k, v in value.items() if k != skip)
    if isinstance(value, list):
        return sum(approx_tokens(v, image_tokens) for v in value)
    if isinstance(value, str):
        return len(value.encode()) // 4 + 1
    return 1


def reasoning_manifest(messages: list[dict]) -> list[dict]:
    blocks = []
    for i, message in enumerate(messages):
        if message.get("role") != "assistant":
            continue
        for key in ("reasoning_details", "reasoning", "reasoning_content", "provider_specific_fields"):
            value = message.get(key)
            if value:
                values = value if isinstance(value, list) else [value]
                for j, block in enumerate(values):
                    blocks.append({"message": i, "field": key, "index": j,
                                   "digest": digest(block), "length": len(json.dumps(block)),
                                   "format": block.get("type", key) if isinstance(block, dict) else key})
        def signatures(value, path):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key in ("thought_signature", "signature", "encrypted_content") and item:
                        blocks.append({"message": i, "field": f"{path}.{key}", "index": 0,
                            "digest": digest(item), "length": len(json.dumps(item)), "format": "signature"})
                    elif key not in ("reasoning_details", "provider_specific_fields"):
                        signatures(item, f"{path}.{key}")
            elif isinstance(value, list):
                for idx, item in enumerate(value):
                    signatures(item, f"{path}[{idx}]")
        signatures(message, "message")
    return blocks


class EmptyInputs(ValueError):
    """The gameplay output carried no inputs — an output defect, retried with a note."""

    def __init__(self):
        super().__init__("Empty inputs: every gameplay turn must return at least one input")


class MissingToolCall(ValueError):
    """Tool mode got a reply that is not exactly one call to the phase's tool.

    Usually the model wrote its answer as plain text: deepseek-v4.1-flash(max)
    (2026-09-12) returned the handover as a JSON string, a fenced block or
    <continuation_summary> tags on 6 of 12 compaction attempts; at turn 110 all
    three attempts did, and the run crashed. The message text is unchanged from
    the old bare ValueError so traces and tallies read the same.
    """

    def __init__(self, phase):
        self.phase = phase
        super().__init__(f"Expected exactly one {phase} call")


def tool_call_note(phase):
    """Appended to the retry after a MissingToolCall. A rule, not the deepseek case:
    the retry is otherwise byte-identical to the request that failed, and an
    identical re-ask reproduces an identical reply."""
    return (f"Your last reply was not accepted: it was not a call to the `{phase}` tool. "
            f"Text outside a tool call is never read. Answer with exactly one `{phase}` tool call "
            "and put the whole answer in its arguments.")


class MalformedJSON(ValueError):
    """A reply that json.loads rejected. The message is `describe_json_error`'s."""


def _open_brackets(text):
    """Brackets still open when the text ends, and whether it ends inside a string."""
    stack, in_string, escaped = [], False, False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "{[":
            stack.append(char)
        elif char in "}]" and stack:
            stack.pop()
    return stack, in_string


def missing_closers(text):
    """The closers a reply that stopped early is missing, or None when that is not
    what is wrong with it (it ends inside a string, or nothing is open)."""
    stack, in_string = _open_brackets(text)
    if in_string or not stack:
        return None
    return "".join("}" if char == "{" else "]" for char in reversed(stack))


def describe_json_error(text, exc):
    """Turn a JSONDecodeError into something a model can act on.

    json's own message points at a character offset ("Expecting ',' delimiter:
    line 1 column 6318 (char 6317)"), which is useless to the model when 6317 is
    simply the end of its reply: qwen3.8-flash(thinking) ended every compaction
    reply one `}` short on 2026-09-12 (turns 21, 41, 61, 101), got that offset
    quoted back five times at turn 101, never understood it, and the run died.
    When the text ends with brackets still open, say so and name the closers;
    otherwise keep json's message.
    """
    stack, in_string = _open_brackets(text)
    reason = f"{exc.msg.removesuffix(' at')} at character {exc.pos}"
    prose = leading_prose(text)
    if prose is not None:
        # claude-fable-5.1(medium) 2026-09-13: "Prediction correct: Oak's event
        # triggered. Now mash B..." then the object, five attempts in a row; the
        # quoted offset (0) told it nothing.
        return (f"The reply began with prose ({prose[:80]!r}) instead of the JSON object. "
                f"Reply with the JSON object only, nothing before or after it.")
    if in_string:
        # json points at the START of an unterminated string, not at the end of the text.
        return f"The reply ended inside a string value ({reason}). Close the string and every open object and array."
    if exc.pos < len(text.rstrip()):
        return reason
    if stack:
        closers = "".join("}" if char == "{" else "]" for char in reversed(stack))
        return (f"The reply ended with {len(stack)} bracket(s) still open ({reason}). "
                f"It is missing `{closers}` at the very end; close every object and array you open.")
    return reason


def leading_prose(text):
    """The text before the first `{` when a reply starts with something other
    than the JSON object, else None. Whitespace alone is not prose (json accepts
    it); a reply with no object at all is a different defect."""
    start = text.find("{")
    if start <= 0 or not text[:start].strip():
        return None
    return text[:start]


def strip_leading_prose(text):
    """Parse the object that follows a prose prefix. Returns ``(value, prose)``,
    or ``(None, None)`` when the reply is not exactly "prose, then one complete
    JSON object": no prefix, an object that does not parse, or text after the
    object all leave the original error to the retry path."""
    prose = leading_prose(text)
    if prose is None:
        return None, None
    try:
        value, end = json.JSONDecoder().raw_decode(text, len(prose))
    except json.JSONDecodeError:
        return None, None
    if text[end:].strip():
        return None, None
    return value, prose


def output_defect_note(phase, mode, exc):
    """Appended to the retry after any other output defect (unparsable arguments, a
    schema miss, a truncated completion, an oversized handover): the error the
    harness saw, then the shape it needs. Andreas 2026-09-12: "in the retry feed
    the model with the error message of what it has to do"."""
    reason = " ".join(str(exc).split())[:600]
    shape = (f"exactly one `{phase}` tool call whose arguments match that tool's schema"
             if mode == "tool" else f"a single JSON object that matches the {phase} schema")
    hint = " The reply was cut off at the output limit, so make it shorter." if "length" in reason else ""
    return f"Your last reply was not accepted: {reason}. Answer with {shape}.{hint}"


# Appended to the retry after an EmptyInputs defect. Written as a rule, not as the
# Brock case: the referee's ground truth is game memory, and a message box still
# on screen means the game has not yet processed whatever the box announces.
EMPTY_INPUTS_NOTE = (
    "Your last output contained no inputs. Every turn must return at least one input. "
    "Progress is judged from the game's memory, not from what the screen says: an event only counts "
    "once the game has processed it, and an open message box means it has not been processed yet. "
    "Close dialogue with A or B and keep playing; never return an empty list."
)


def replay_check(expected: list[dict], outbound: list[dict]) -> dict:
    old = reasoning_manifest(expected)
    # Check the entire prefix, including image bytes and associated tool IDs.
    unchanged = outbound[:len(expected)] == expected
    replayed = reasoning_manifest(outbound[:len(expected)])
    opaque = lambda b: b["format"] in ("reasoning.encrypted", "signature") or b["field"] == "provider_specific_fields"
    return {"expected_blocks": len(old), "replayed_blocks": len(replayed),
            "expected_opaque_blocks": sum(opaque(b) for b in old),
            "replayed_opaque_blocks": sum(opaque(b) for b in replayed),
            "local_replay": "intact" if old == replayed else "modified_or_missing",
            "history_prefix": "unchanged" if unchanged else "changed",
            "provider_feedback": "not_reported", "manifest": old}


def normalize_usage(raw: dict) -> dict:
    details = raw.get("prompt_tokens_details") or {}
    output = raw.get("completion_tokens_details") or {}
    prompt = raw.get("prompt_tokens")
    cached = details.get("cached_tokens")
    return {"request_tokens": prompt, "response_tokens": raw.get("completion_tokens"),
            "total_tokens": raw.get("total_tokens"), "reasoning_tokens": output.get("reasoning_tokens"),
            "cached_tokens": cached, "cache_write_tokens": details.get("cache_write_tokens"),
            "cache_read_fraction": cached / prompt if isinstance(prompt, (int, float)) and prompt > 0 and isinstance(cached, (int, float)) else None,
            "cost_usd": raw.get("cost"), "raw_usage": raw}


def implied_cache(attempt: dict, endpoints: list[dict] | None, tolerance: float = 0.03) -> dict:
    """Back out the cache discount from what the provider billed for the prompt.

    ``implied = (n × P_full − billed) / (P_full − P_cache_read)`` after picking
    the cheapest list tier whose full price covers the billed per-token rate
    (the conservative choice: fewest implied cached tokens). Cache-write
    premiums are removed first when the provider reports write tokens. This is
    what the provider charged, so it is a check on the usage block, not a
    replacement for it: when both exist, ``agreement`` says whether they match.
    """
    n = attempt.get("request_tokens")
    usage = attempt.get("raw_usage") or {}
    billed = (usage.get("cost_details") or {}).get("upstream_inference_prompt_cost")
    if not n or billed is None:
        return {"status": "unbilled"}
    rate = billed / n
    tiers = []
    for endpoint in endpoints or []:
        pricing = endpoint.get("pricing") or {}
        try:
            full = float(pricing["prompt"])
        except (KeyError, TypeError, ValueError):
            continue
        listed = pricing.get("input_cache_read") not in (None, "")
        read = float(pricing.get("input_cache_read") or 0)
        write = float(pricing.get("input_cache_write") or 0)
        tiers.append((full, read, write, endpoint, listed))
    if not tiers:
        return {"status": "unpriced", "billed_prompt_rate_per_m": rate * 1e6}
    tag = (attempt.get("continuity") or {}).get("configured_endpoint")
    provider = attempt.get("provider")
    # The configured tag is exact: `openai` must not also match `openai/flex` (half price), or the
    # cheapest-qualifying rule below picks the flex tier and under-implies the cache (Astra 2026-09-06:
    # 87% reported, 62% "implied"). Fall back to prefix/provider matching only when nothing is exact.
    named = [t for t in tiers if tag and t[3].get("tag") == tag]
    if not named:
        named = [t for t in tiers if (tag and (t[3].get("tag") or "").startswith(tag))
                 or (provider and t[3].get("provider_name") == provider)]
    # Remove each tier's cache-write premium before comparing rates: on the first
    # request of a segment nearly every token is a write billed above list, which
    # would otherwise push the rate past the true tier (Anthropic 1.25x writes).
    writes = attempt.get("cache_write_tokens") or 0
    def adjusted_cost(tier):
        full, _, write, _, _ = tier
        return billed - (writes * (write - full) if write > full else 0)
    candidates = sorted((t for t in (named or tiers) if t[0] * n >= adjusted_cost(t) * (1 - tolerance)),
                        key=lambda t: t[0])
    if not candidates:
        return {"status": "rate_above_list", "billed_prompt_rate_per_m": rate * 1e6}
    full, read, write, endpoint, listed = candidates[0]
    adjusted = adjusted_cost(candidates[0])
    implied = 0 if full <= read else max(0, min(n, round((full * n - adjusted) / (full - read))))
    reported = attempt.get("cached_tokens")
    if reported is None:
        agreement = "provider_not_reporting"
    else:
        gap = implied - reported
        agreement = ("matches" if abs(gap) <= max(64, 0.05 * n)
                     else "billing_implies_more" if gap > 0 else "billing_implies_less")
    return {"status": "discount_billed" if implied else "no_discount_billed",
            "billed_prompt_rate_per_m": rate * 1e6, "tier": endpoint.get("name"),
            "tier_prompt_per_m": full * 1e6, "tier_cache_read_per_m": read * 1e6 if listed else None,
            "implied_cached_tokens": implied, "implied_read_fraction": implied / n, "agreement": agreement}


def cache_economics(attempts: list[dict]) -> dict:
    """What the cache was worth in money on this run, from list prices + reported hits.

    Verdicts: ``no_cache_offered`` (endpoint lists no cache-read price and no hits),
    ``no_discount_on_hits`` (cache-read price equals prompt price), ``priced_no_hits``
    (a discount exists but nothing was cached), ``saving`` otherwise.
    """
    priced = [a for a in attempts if (a.get("implied_cache") or {}).get("tier_prompt_per_m")]
    if not priced:
        return {"verdict": "unknown", "saved_usd": None, "discount_fraction": None}
    saved = 0.0
    cached = 0
    listed = False
    discounts = []
    for a in priced:
        c = a["implied_cache"]
        hits = a.get("cached_tokens") or 0
        cached += hits
        if c.get("tier_cache_read_per_m") is not None:
            listed = True
            discounts.append((c["tier_prompt_per_m"] - c["tier_cache_read_per_m"]) / c["tier_prompt_per_m"])
            saved += hits * (c["tier_prompt_per_m"] - c["tier_cache_read_per_m"]) / 1e6
    discount = max(discounts) if discounts else None
    if not listed and cached == 0:
        verdict = "no_cache_offered"
    elif discount is not None and discount < 0.01:
        verdict = "no_discount_on_hits"
    elif cached == 0:
        verdict = "priced_no_hits"
    else:
        verdict = "saving"
    return {"verdict": verdict, "saved_usd": saved, "discount_fraction": discount, "cached_tokens": cached}


def cache_totals(attempts: list[dict]) -> dict:
    measured = [a for a in attempts if a.get("cached_tokens") is not None and a.get("request_tokens") is not None]
    reads = sum(a["cached_tokens"] for a in measured)
    inputs = sum(a["request_tokens"] for a in measured)
    result = {"attempts": len(attempts), "measured_attempts": len(measured),
              "cached_tokens": reads, "measured_input_tokens": inputs,
              "input_read_fraction": reads / inputs if inputs else None,
              "request_hit_fraction": sum(a["cached_tokens"] > 0 for a in measured) / len(measured) if measured else None}
    implied = [a for a in attempts if (a.get("implied_cache") or {}).get("implied_cached_tokens") is not None]
    if implied:
        implied_reads = sum(a["implied_cache"]["implied_cached_tokens"] for a in implied)
        implied_inputs = sum(a["request_tokens"] for a in implied)
        agreements = {}
        for a in implied:
            key = a["implied_cache"]["agreement"]
            agreements[key] = agreements.get(key, 0) + 1
        result.update(implied_attempts=len(implied), implied_cached_tokens=implied_reads,
                      implied_input_tokens=implied_inputs,
                      implied_read_fraction=implied_reads / implied_inputs if implied_inputs else None,
                      implied_agreement=agreements)
    result["economics"] = cache_economics(attempts)
    return result


def display_messages(messages):
    """Existing trace roles, without using the display projection for replay."""
    out = []
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if isinstance(content, list):
            content = "\n".join(p.get("text", "[image]") for p in content)
        if role == "assistant":
            thinking = message.get("reasoning") or message.get("reasoning_content")
            if not thinking:
                thinking = "\n".join(b.get("text", b.get("summary", "")) for b in message.get("reasoning_details", []) if isinstance(b, dict))
            if thinking:
                out.append({"role": "thinking", "content": thinking})
            for call in message.get("tool_calls") or []:
                out.append({"role": "tool_call", "tool_name": "final_result",
                            "args": call["function"]["arguments"]})
        if content:
            out.append({"role": "tool_result" if role == "tool" else role, "content": content,
                        **({"tool_name": "final_result"} if role == "tool" else {})})
    return out


class StreamAssembly:
    """Assemble OpenRouter chat deltas while retaining raw chunks in the archive."""
    def __init__(self):
        self.message = {"role": "assistant", "content": ""}
        self.result = {"choices": [{"message": self.message, "finish_reason": None}]}
        self.calls = {}
        self.details = {}

    def add(self, chunk):
        if chunk.get("error"):
            raise RuntimeError(str(chunk["error"]))
        for k, v in chunk.items():
            if k != "choices":
                self.result[k] = v
        for choice in chunk.get("choices", []):
            if choice.get("index", 0) != 0:
                raise ValueError("Only one completion is supported")
            if choice.get("finish_reason"):
                self.result["choices"][0]["finish_reason"] = choice["finish_reason"]
            delta = choice.get("delta", {})
            for k, v in delta.items():
                if v is None:
                    continue
                if k in ("content", "reasoning", "reasoning_content", "refusal"):
                    self.message[k] = self.message.get(k, "") + v
                elif k == "tool_calls":
                    for call in v:
                        idx = call["index"]
                        dest = self.calls.setdefault(idx, {"type": "function", "function": {"name": "", "arguments": ""}})
                        if call.get("id"):
                            dest["id"] = call["id"]
                        for field, text in call.get("function", {}).items():
                            if text:
                                dest["function"][field] = dest["function"].get(field, "") + text
                    self.message[k] = [self.calls[i] for i in sorted(self.calls)]
                elif k == "reasoning_details":
                    for block in v:
                        if "index" not in block:
                            # Missing identity is ambiguous; never guess for opaque chunks.
                            raise ContinuityError("Stream reasoning block has no index; use stream: false for this endpoint")
                        dest = self.details.setdefault(block["index"], {})
                        for field, value in block.items():
                            if field in ("text", "summary", "data", "signature") and isinstance(value, str):
                                dest[field] = dest.get(field, "") + value
                            else:
                                if field in dest and dest[field] != value:
                                    raise ContinuityError("Stream reasoning identity changed")
                                dest[field] = value
                    self.message[k] = [self.details[i] for i in sorted(self.details)]
                else:
                    self.message[k] = v


class OpenRouterTransport:
    async def __call__(self, body, api_key, timeout, on_chunk):
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", "https://openrouter.ai/api/v1/chat/completions",
                                     headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
                                              "X-OpenRouter-Metadata": "enabled"},
                                     content=body) as response:
                if response.status_code >= 400:
                    raw = await response.aread()
                    try:
                        error = json.loads(raw)
                    except ValueError:
                        error = {"error": raw.decode(errors="replace")}
                    raise ProviderRequestError(response.status_code, error, headers=dict(response.headers))
                if not json.loads(body).get("stream"):
                    return json.loads(await response.aread())
                assembly = StreamAssembly()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    chunk = json.loads(data)
                    on_chunk(chunk)
                    assembly.add(chunk)
                return assembly.result


class AppendAgent:
    def __init__(self, config, run_dir, emit, transport=None, on_usage=None, budget_exhausted=None, pricing_fetcher=None):
        self.config = config
        # Optional async (model, api_key) -> endpoint pricing snapshot; written once
        # per run dir as conversation/endpoint-pricing.json for the trace's
        # billing-implied cache estimate. None (tests, offline) skips it.
        self.pricing_fetcher = pricing_fetcher
        self._pricing_written = False
        self.options = config["compaction"]
        self.run_dir = Path(run_dir)
        self.store = ReplayStore(self.run_dir / "conversation")
        self.emit = emit
        self.transport = transport or OpenRouterTransport()
        # Injectable so tests can measure the backoff schedule without waiting it out.
        self._sleep = asyncio.sleep
        self.retry_policy = RetryPolicy.from_transport(config["transport"])
        self.on_usage = on_usage or (lambda usage: None)
        self.budget_exhausted = budget_exhausted or (lambda: False)
        self.model = config["llm_model"]
        self.profile = deepcopy(config.get("_provider_profile") or {})
        # A continue queued with rebase_contract: the ONE place a conversation may
        # change its provider profile / wire contract, on purpose and on record
        # (2026-09-12: a harness fix — memory as a JSON string under strict
        # json_schema — had to reach a run already 77 turns in). Consumed once:
        # the first mismatching request re-baselines the digest and emits
        # `contract_rebased`; a second change in the same segment is still an error.
        self.rebase_contract = bool(config.get("_rebase_contract"))
        self._rebased = False
        # config-5.0 grades the previous turn in the output; 5.1+ does not.
        self.graded = bool(config.get("self_grade", True))
        mode = str(config.get("mode") or "benchmark").lower()
        self.system = fill_prompt(config["system_prompt"], game_name=config.get("game_name", "Pokemon FireRed"),
                                  mode_guidelines=config.get(f"{mode}_guidelines", ""))
        self.state = {"version": 1, "model": self.model, "session_id": uuid.uuid4().hex,
                      "segment": 1, "completed_turn": 0, "segment_turns": 0,
                      "messages": [], "handover": {"continuation_summary": "", "memory": {}},
                      "observation_turn": None, "last_action": None, "recent_grades": [],
                      "last_input_tokens": 0, "attempts": []}
        if self.profile:
            self.state["provider_profile"] = deepcopy(self.profile)
        self.checkpoint = deepcopy(self.state)
        self.pending = None

    def _play_schema(self):
        return GradedPlayAction if self.graded else PlayAction

    def export_checkpoint(self):
        return self.store.pack(self.checkpoint)

    def restore(self, packed, completed_turn):
        value = self.store.unpack(packed)
        if value.get("version") != 1 or value.get("model") != self.model or value.get("completed_turn") != completed_turn:
            raise ContinuityError("Conversation checkpoint does not match model/game turn")
        if value.get("messages") and value["messages"][0] != {"role": "system", "content": self.system}:
            raise ContinuityError("System prompt changed since conversation checkpoint")
        if value.get("provider_profile", {}) != self.profile:
            if not self.rebase_contract:
                raise ContinuityError("Provider profile changed since conversation checkpoint; start a new run")
            before = value.get("provider_profile", {})
            changed = sorted(k for k in set(before) | set(self.profile) if before.get(k) != self.profile.get(k))
            self.emit("contract_rebased", {"turn": completed_turn, "stage": "profile", "changed": changed,
                                           "before": {k: before.get(k) for k in changed}, "after": {k: self.profile.get(k) for k in changed}})
            value.setdefault("contract_rebases", []).append({"turn": completed_turn, "stage": "profile", "changed": changed})
            value["provider_profile"] = deepcopy(self.profile)
        self.state = value
        self.checkpoint = deepcopy(value)

    def _start(self):
        text = fill_prompt(self.config["segment_start_prompt"],
                           task_block=json.dumps(self.config.get("task", {})),
                           continuation_summary=self.state["handover"]["continuation_summary"],
                           memory_json=json.dumps(self.state["handover"]["memory"], ensure_ascii=False),
                           last_action=json.dumps(self.state["last_action"]),
                           recent_grades=json.dumps(self.state["recent_grades"]))
        self.state["messages"] = [{"role": "system", "content": self.system}, {"role": "user", "content": text}]

    def _observation(self, turn, ocr, image):
        """Messages that deliver one observation (a list; one message unless the profile splits the turn).

        With `final_turn_text_only` the screenshot turn is closed by a one-word assistant
        acknowledgement, and `_action_prompt` supplies a text-only user message to end the
        request. OpenAI via OpenRouter cannot extend the prompt cache past the first image
        when the request's final turn contains an image (probe 2026-09-06: 25 live turns
        cached 1,192 tokens each; this shape cached the whole previous request).
        """
        observation = {"role": "user", "content": [
            {"type": "text", "text": fill_prompt(self.config["user_prompt"], turn_number=turn, ocr_text=ocr or "(none)")},
            {"type": "image_url", "image_url": {"url": image}}]}
        if not self._splits_turn():
            return [observation]
        return [observation, {"role": "assistant", "content": TURN_ACK}]

    def _splits_turn(self):
        return bool(self.profile and self.profile.get("final_turn_text_only"))

    def _action_prompt(self, turn):
        """Text-only final user message for gameplay requests on split-turn profiles."""
        if not self._splits_turn():
            return []
        # config-5.0 declares `action_prompt` and `_validate_append_config`
        # requires it, so this fallback is unreachable for any config loaded
        # from disk. It survives for RESUMED runs: `continue_from_run` replays
        # the saved `config.json` of a run started before the key existed, and
        # that path never re-validates. Do not read it as "the prompt is
        # optional".
        text = self.config.get("action_prompt") or "Turn {{turn_number}}: respond with your next action now."
        return [{"role": "user", "content": fill_prompt(text, turn_number=turn)}]

    async def play(self, turn, ocr, image, initial_memory=None):
        if turn != self.state["completed_turn"] + 1:
            raise ContinuityError("Game and conversation turn counters diverged")
        if not self.state["messages"]:
            self.state["handover"]["memory"] = deepcopy(initial_memory or {})
            self._start()
        observation = self._observation(turn, ocr, image)
        due = self.state["segment_turns"] >= self.options["every_n_turns"]
        # Conservative reserve, in approximate tokens, for the latest response,
        # next image, compaction instruction and output. Exact tokenization
        # remains upstream; last_input_tokens is the provider's own count.
        image_tokens = self.options.get("image_token_reserve", 4096)
        recent = self.state["messages"][-2:]
        growth = approx_tokens(recent, image_tokens) + approx_tokens(ocr, image_tokens) + image_tokens
        # The output ceiling is the endpoint's maximum, not what a turn uses; reserve
        # a realistic amount so a 128k ceiling does not force compaction every turn.
        output_reserve = min(self.options["max_output_tokens"], self.options.get("estimate_output_reserve", 12288))
        estimate = (self.state["last_input_tokens"] + growth + approx_tokens(self.options["prompt"], image_tokens)
                    + output_reserve)
        early = estimate >= self.options["context_token_limit"] * self.options["context_limit_fraction"]
        # Hard cap independent of the endpoint's context length. The profile
        # overwrites `context_token_limit` with the model's context (1M on Gemini,
        # so the fraction trigger sits near 680k and never fires); this cap is
        # the safety valve for a model whose per-turn growth balloons. It is
        # deliberately ABOVE any normal segment: the 2026-09-07 cost fit put the
        # cheapest interval at 20-100k prompt tokens for every measured model and
        # the curve within 8% of optimal at 20 turns, so the cap must not become
        # the de-facto interval. Optional: absent means no cap.
        cap = self.options.get("max_prompt_tokens")
        capped = bool(cap) and estimate >= cap
        if (due or early or capped) and self.state["segment_turns"] and self.state["observation_turn"] != turn:
            reason = "turn_interval" if due else ("context_threshold" if early else "token_cap")
            await self.compact(turn, observation, reason)
        if self.budget_exhausted():
            raise SpendLimitReached("Spend budget reached before next gameplay request")
        if self.state["observation_turn"] != turn:
            self.state["messages"].extend(observation + self._action_prompt(turn))
            self.state["observation_turn"] = turn
        output, messages = await self._request("gameplay", turn, deepcopy(self.state["messages"]))
        self.pending = (output, messages)
        return self._play_schema().model_validate(output)

    async def compact(self, turn, observation, reason):
        messages = deepcopy(self.state["messages"]) + observation + [
            {"role": "user", "content": fill_prompt(self.options["prompt"],
                summary_target_tokens=self.options["summary_target_tokens"], after_turn=turn - 1)}]
        self.emit("compaction_start", {"turn": turn, "after_turn": turn - 1, "segment": self.state["segment"], "reason": reason})
        output, _ = await self._request("compaction", turn, messages)
        old = deepcopy(self.state["handover"])
        self.state["handover"] = output
        self.state["segment"] += 1
        self.state["segment_turns"] = 0
        self.state["last_input_tokens"] = 0
        if not self.profile:
            self.state["last_provider"] = None
        self._start()
        self.state["messages"].extend(observation + self._action_prompt(turn))
        self.state["observation_turn"] = turn
        self._commit()
        self.emit("compaction_complete", {"turn": turn, "after_turn": turn - 1,
            "segment": self.state["segment"], "handover": output, "previous_memory": old["memory"],
            "reasoning_transition": "intentional_compaction_reset"})

    def commit_action(self, turn):
        if self.pending is None:
            raise RuntimeError("No accepted action to commit")
        action, messages = self.pending
        self.state["messages"] = messages
        self.state["completed_turn"] = turn
        self.state["segment_turns"] += 1
        self.state["last_action"] = action
        if self.graded:
            self.state["recent_grades"] = (self.state["recent_grades"] + [action["last_turn_succeeded"]])[-3:]
        self.state["observation_turn"] = None
        self.pending = None
        self._commit()

    def _commit(self):
        self.store.write("state.json", self.state)
        self.checkpoint = deepcopy(self.state)

    @staticmethod
    def _decode(text):
        """json.loads, with an error message the model can act on (describe_json_error)."""
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise MalformedJSON(describe_json_error(text, exc)) from exc

    def _repair_prompted(self, text):
        """The two allowed prompted-JSON repairs, each behind its profile flag.
        Returns ``(value, output_coerced fields)`` or ``(None, None)``.

        - `close_unbalanced_json` (qwen3.8-flash, 2026-09-12): the reply stopped
          with brackets still open, one `}` short on 7 of 7 compaction replies
          with the structure otherwise intact. Append the derived closers.
        - `strip_leading_prose` (claude-fable-5.1, 2026-09-13): a sentence of
          prose before an otherwise complete object ("Prediction correct: ...
          {...}"), 5 of 5 attempts at turn 8 even with the prose named in the
          retry note. Parse the object that follows the prose.
        """
        if self.profile.get("close_unbalanced_json"):
            closers = missing_closers(text)
            if closers:
                try:
                    return json.loads(text + closers), {"kind": "closed_brackets", "appended": closers, "chars": len(text)}
                except json.JSONDecodeError:
                    pass
        if self.profile.get("strip_leading_prose"):
            value, prose = strip_leading_prose(text)
            if value is not None:
                return value, {"kind": "stripped_prose", "prose": prose[:200], "chars": len(prose)}
        return None, None

    def _body(self, phase, messages):
        resolved = self.config.get("_llm_resolved") or {}
        if self.profile:
            # Profile owns endpoint-sensitive settings; aliases still select reasoning level.
            # An UNPROFILED model carries the `defaults:` contract with endpoint "" —
            # nobody probed a serving endpoint for it, so it must NOT be pinned to one.
            # It keeps the registry's own `provider:` block (None for most models, which
            # is then omitted from the body) instead of being pinned to the empty tag.
            pin = ({"only": [self.profile["endpoint"]], "allow_fallbacks": False, "require_parameters": True}
                   if self.profile["endpoint"] else deepcopy(resolved.get("provider")))
            resolved = {"output_mode": self.profile["output_mode"], "reasoning": self.profile["reasoning"],
                        "provider": pin,
                        **self.profile["sampling"],
                        **({"max_tokens": resolved["max_tokens"]} if "max_tokens" in resolved else {})}
            if self.profile.get("excluded_endpoints") and isinstance(resolved["provider"], dict):
                resolved["provider"]["ignore"] = deepcopy(self.profile["excluded_endpoints"])
        messages = project_history(messages, self.profile)
        mode = resolved.get("output_mode", "tool")
        schemas = {"gameplay": self._play_schema().model_json_schema(), "compaction": Handover.model_json_schema()}
        for schema in schemas.values():
            # A class docstring is documentation for developers, not the model.
            # pydantic copies it into the schema's top-level `description`, and
            # PlayAction's ("…schema quirks (Muse's stringified null, mimo's
            # "None")…") reached every request from 2026-09-08: Anthropic's
            # classifier refused claude-fable-5.1 outright ("duplicating model
            # outputs", finish_reason content_filter) on all three attempts of
            # turn 1, and the same request with the description removed was
            # answered (probe 2026-09-12). Field descriptions stay.
            schema.pop("description", None)
        # Memory travels as a JSON string when the profile says so — or whenever the
        # output is a strict json_schema (native_json): the provider closes every
        # object under strict, so an open `memory` object can only come back as {}
        # (claude-opus-5, all three compactions of the 2026-09-12 14:17 run; a
        # string memory came back with 10 keys). Handover's before-validator
        # decodes the string, so the parse side needs nothing extra.
        if self.profile.get("memory_encoding") == "json_string" or mode == "native_json":
            schemas["compaction"]["properties"]["memory"] = {"type": "string", "description": (self.profile or {}).get("memory_wire_description")
                or "The complete flexible memory dictionary serialized as a JSON object string. Choose keys freely; the harness parses it back into a dictionary."}
        body = {"model": self.model, "messages": messages, "session_id": self.state["session_id"],
                "stream": self.config["transport"]["stream"],
                "max_tokens": self.options["max_output_tokens"] if phase == "compaction" else resolved.get("max_tokens", self.config["transport"]["max_output_tokens"])}
        for k in ("temperature", "top_p", "reasoning", "provider"):
            if resolved.get(k) is not None:
                body[k] = deepcopy(resolved[k])
        if "reasoning" not in body and self.config.get("thinking"):
            body["reasoning"] = deepcopy(self.config["thinking"])
        if self.config.get("caching", {}).get("cache_control"):
            body["cache_control"] = deepcopy(self.config["caching"]["cache_control"])
        for prefix, control in self.config.get("caching", {}).get("cache_control_by_model_prefix", {}).items():
            if self.model.startswith(prefix):
                body["cache_control"] = deepcopy(control)
        if self.profile:
            body.pop("cache_control", None)
            if self.profile["cache_mode"] == "automatic":
                body["cache_control"] = deepcopy(self.profile["cache_control"])
            body["transforms"] = []  # Our explicit compaction owns history trimming.
        if mode == "tool":
            body["tools"] = [{"type": "function", "function": {"name": name, "parameters": schema}} for name, schema in schemas.items()]
            body["tool_choice"] = {"type": "function", "function": {"name": phase}}
            body["parallel_tool_calls"] = False
            if self.profile:
                body.pop("parallel_tool_calls")  # Not advertised by every endpoint.
                choice = self.profile["tool_choice"]
                if choice == "omit":
                    body.pop("tool_choice")
                elif choice != "named":
                    body["tool_choice"] = choice
                for tool in body["tools"]:
                    tool["function"]["description"] = self.profile["tool_descriptions"][tool["function"]["name"]]
        else:
            envelope = {"type": "object", "properties": {"result": {"anyOf": list(schemas.values())}}, "required": ["result"], "additionalProperties": False}
            if mode == "native_json":
                # strict: the provider constrains generation to the schema, so an
                # invented key (claude-opus-5's `prediction`, 2 of 3 first attempts
                # on 2026-09-12) or a garbled one (`"}": null`) cannot be emitted.
                body["response_format"] = {"type": "json_schema", "json_schema": {"name": "agent_response", "strict": True, "schema": envelope}}
            elif mode == "prompted":
                # Append once per segment; never edit the original system prefix.
                instruction = fill_prompt(self.config["transport"]["prompted_output_prompt"], schema=json.dumps(envelope))
                body["messages"] = messages[:1] + [{"role": "system", "content": instruction}] + messages[1:]
            else:
                raise ValueError(f"Unsupported output mode: {mode}")
        if body["stream"]:
            body["stream_options"] = {"include_usage": True}
        return body, mode

    async def _snapshot_pricing(self):
        if self._pricing_written or self.pricing_fetcher is None:
            return
        self._pricing_written = True  # One attempt per run dir; a failure is recorded, not retried.
        try:
            snapshot = await self.pricing_fetcher(self.model, self.config.get("openrouter_api_key", ""))
        except Exception as exc:
            snapshot = {"model": self.model, "error": str(exc), "endpoints": []}
        atomic_json(self.store.root / "endpoint-pricing.json", snapshot)
        self._warn_on_cache_economics(snapshot)

    def _warn_on_cache_economics(self, snapshot):
        """Say up front when the pinned endpoint cannot return money on cache hits."""
        tag = self.profile.get("endpoint") if self.profile else None
        if not tag:
            return
        match = next((e for e in snapshot.get("endpoints") or [] if (e.get("tag") or "") == tag), None)
        if match is None:
            return
        pricing = match.get("pricing") or {}
        try:
            full = float(pricing.get("prompt"))
        except (TypeError, ValueError):
            return
        read = pricing.get("input_cache_read")
        if read in (None, ""):
            reason = "lists no cache-read price: caching cannot save money here"
        elif full and float(read) >= full * 0.99:
            reason = f"charges cache reads at the prompt price (${float(read)*1e6:.3f}/M): hits save nothing"
        else:
            return
        message = f"Endpoint {tag} {reason}"
        self.emit("endpoint_warning", {"endpoint": tag, "kind": "cache_economics", "message": message})
        print(f"  Warning: {message}", flush=True)

    async def _request(self, phase, turn, messages):
        await self._snapshot_pricing()
        schema = self._play_schema() if phase == "gameplay" else Handover
        # Two retry budgets (2026-09-09, see src/agent/backoff.py): OUTPUT defects —
        # the model answered in the wrong shape — get `max_retries` immediate
        # re-asks, as before. TRANSIENT provider failures (429 / 5xx / network /
        # timeout) get `retry_policy.transient_retries` attempts with long
        # exponential backoff inside a wall-clock budget, and never consume the
        # output budget: a rate-limited provider is not a model defect.
        output_budget = self.options["max_retries"] if phase == "compaction" else self.config["transport"]["max_retries"]
        policy = self.retry_policy
        output_failures = transient_failures = attempt = 0
        retry_started = time.monotonic()
        while True:
            attempt += 1
            if self.budget_exhausted():
                raise SpendLimitReached("Spend budget reached before model request")
            request_id = uuid.uuid4().hex
            body, mode = self._body(phase, deepcopy(messages))
            encoded = json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
            outbound = json.loads(encoded)
            sent_history = outbound["messages"]
            if mode == "prompted":
                sent_history = sent_history[:1] + sent_history[2:]
            continuity = replay_check(project_history(self.state["messages"], self.profile), sent_history)
            if self.profile:
                continuity.update(reasoning_policy=self.profile["reasoning_replay"], reasoning_use=self.profile["reasoning_use"],
                                  archived_blocks=len(reasoning_manifest(self.state["messages"])),
                                  configured_endpoint=self.profile["endpoint"], cache_mode=self.profile["cache_mode"])
                if self.profile.get("name"):
                    continuity["profile_name"] = self.profile["name"]
            contract_fields = {k: outbound.get(k) for k in ("model", "tools", "response_format", "reasoning", "temperature", "top_p")}
            if self.profile:
                # A forced call to the phase's own tool (profile tool_choice `named`)
                # differs between gameplay and compaction requests by design; digest
                # it as the policy, not the value, or the first handover would read
                # as a contract change (2026-09-12).
                tool_choice = outbound.get("tool_choice")
                if isinstance(tool_choice, dict) and tool_choice.get("function", {}).get("name") == phase:
                    tool_choice = "named"
                contract_fields.update(profile=self.profile, provider=outbound.get("provider"), tool_choice=tool_choice,
                                       cache_control=outbound.get("cache_control"), transforms=outbound.get("transforms"))
            if mode == "prompted":
                contract_fields["system_messages"] = [m for m in outbound["messages"] if m.get("role") == "system"]
            contract = digest(contract_fields)
            field_digests = {k: digest(v) for k, v in contract_fields.items()}
            if self.state.get("wire_contract") not in (None, contract):
                if not (self.rebase_contract and not self._rebased):
                    raise ContinuityError("Model/tool/reasoning contract changed within the conversation")
                # Deliberate rebase (see __init__): name what changed, using the
                # per-field digests kept since this feature landed; an older state
                # without them reports every field as unknown rather than guessing.
                previous = self.state.get("wire_contract_fields")
                changed = (sorted(k for k in field_digests if previous.get(k) != field_digests[k]) if previous
                           else ["unknown (state predates per-field digests)"])
                self._rebased = True
                self.emit("contract_rebased", {"turn": turn, "phase": phase, "stage": "wire", "changed": changed,
                                               "from": self.state["wire_contract"], "to": contract})
                self.state.setdefault("contract_rebases", []).append({"turn": turn, "stage": "wire", "changed": changed})
            self.state["wire_contract"] = contract
            self.state["wire_contract_fields"] = field_digests
            if continuity["local_replay"] != "intact" or continuity["history_prefix"] != "unchanged":
                self.emit("llm_request_error", {"turn": turn, "phase": phase, "segment": self.state["segment"],
                    "request_id": request_id, "continuity": continuity, "error": "Final outbound history differs from retained conversation"})
                raise ContinuityError("Final outbound history differs from retained conversation")
            meta = {"turn": turn, "phase": phase, "segment": self.state["segment"], "attempt": attempt,
                    "request_id": request_id, "model": self.model, "continuity": continuity,
                    "request_artifact": f"conversation/{request_id}-request.json",
                    "response_artifact": f"conversation/{request_id}-response.json"}
            self.store.write(f"{request_id}-request.json", outbound)
            start = time.monotonic()
            first_token = None
            chunk_path = self.store.root / f"{request_id}-chunks.jsonl"
            def on_chunk(chunk):
                nonlocal first_token
                if first_token is None and any(c.get("delta") for c in chunk.get("choices", [])):
                    first_token = time.monotonic() - start
                with chunk_path.open("a") as f:
                    f.write(json.dumps(chunk) + "\n")
            raw = None
            try:
                raw = await self.transport(encoded, self.config.get("openrouter_api_key", ""), self.config["transport"]["timeout_seconds"], on_chunk)
                self.store.write(f"{request_id}-response.json", raw)
                usage = normalize_usage(raw.get("usage") or {})
                diagnostics, dropped = router_diagnostics(raw)
                returned = reasoning_manifest([(raw.get("choices") or [{}])[0].get("message") or {}])
                previous_provider = self.state.get("last_provider")
                provider_changed = bool(previous_provider and raw.get("provider") and previous_provider != raw["provider"])
                previous_model = self.state.get("last_response_model")
                model_changed = bool(previous_model and raw.get("model") and previous_model != raw["model"])
                continuity.update({**diagnostics, "request": "accepted", "returned_blocks": returned,
                                   "previous_provider": previous_provider, "provider_changed": provider_changed,
                                   "response_model": raw.get("model"), "response_model_changed": model_changed})
                meta.update(usage, provider=raw.get("provider"), response_id=raw.get("id"),
                            latency_s=time.monotonic() - start, time_to_first_token_s=first_token)
                self.state["attempts"].append({k: v for k, v in meta.items() if k not in ("continuity", "raw_usage")})
                self.on_usage(meta)
                self.emit("llm_request_usage", meta)
                self.state["last_input_tokens"] = usage["request_tokens"] or self.state["last_input_tokens"]
                if dropped and self.config["observability"]["on_reasoning_loss"] == "stop":
                    raise ContinuityError("Provider reported dropped reasoning")
                if (provider_changed or model_changed) and (self.profile or
                        (continuity["expected_blocks"] and self.config["observability"]["on_reasoning_loss"] == "stop")):
                    raise ContinuityError("Serving provider/model changed; cache and reasoning compatibility unverified")
                self.state["last_provider"] = raw.get("provider") or previous_provider
                self.state["last_response_model"] = raw.get("model") or previous_model
                if raw.get("error"):
                    raise RuntimeError(str(raw["error"]))
                choice = raw["choices"][0]
                native = choice.get("native_finish_reason")
                if native in ("network_error", "error") or choice.get("finish_reason") == "error":
                    raise ProviderTransportError(f"Provider returned no completion ({native or choice.get('finish_reason')})")
                if choice.get("finish_reason") not in ("stop", "tool_calls"):
                    raise ValueError(f"Incomplete model output: {choice.get('finish_reason')}")
                message = deepcopy(choice["message"])
                calls = message.get("tool_calls") or []
                if mode == "tool":
                    if len(calls) != 1 or calls[0]["function"]["name"] != phase or not calls[0].get("id"):
                        raise MissingToolCall(phase)
                    value = self._decode(calls[0]["function"]["arguments"])
                else:
                    text = output_json_text(message["content"], self.profile)
                    try:
                        parsed = self._decode(text)
                    except MalformedJSON as original:
                        # Profile-gated repairs (each proven per model, off by
                        # default); anything else wrong with the text still raises
                        # the original error and takes the retry path.
                        parsed, repair = self._repair_prompted(text)
                        if parsed is None:
                            raise original
                        self.emit("output_coerced", {"turn": turn, "phase": phase, **repair})
                    if isinstance(parsed, dict) and "result" in parsed:
                        value = parsed["result"]
                    elif self.profile.get("allow_unwrapped_json", True):
                        value = parsed
                    else:
                        raise ValueError("Expected a JSON object containing result")
                    # A prompted model that pads the object with a key holding null
                    # has encoded nothing; dropping it is lossless (coerce.py).
                    # Tool and native_json output is schema-enforced on the wire.
                    value = drop_null_extras(value, schema.model_fields)
                if phase == "compaction" and self.profile.get("memory_encoding") == "json_string":
                    value["memory"] = json.loads(value["memory"])
                output = schema.model_validate(value).model_dump()
                json.dumps(output, allow_nan=False)  # Reject NaN/Infinity before any state mutation.
                if phase == "gameplay" and not output["inputs"]:
                    # An empty input list is an output defect, not a legal move
                    # (2026-09-11): gpt-6-astra(low) beat Brock, saw "received the
                    # BOULDERBADGE" on the battle screen and returned [] for 80
                    # turns — the post-battle script that sets the badge flag only
                    # runs once that text box is dismissed, so the referee never saw
                    # the win and the leg cap ended the run. Checked HERE rather than
                    # as `min_length=1` on the schema so the tool JSON schema — part
                    # of the wire contract every continue re-verifies — is unchanged.
                    raise EmptyInputs()
                if phase == "compaction" and len(json.dumps(output)) > self.options["max_handover_chars"]:
                    raise ValueError("Handover exceeds configured size limit")
                accepted = messages + [message]
                for call in calls:
                    accepted.append({"role": "tool", "tool_call_id": call["id"], "content": "Accepted."})
                trace = display_messages(outbound["messages"] + [message])
                if mode != "tool":
                    trace.append({"role": "tool_call", "tool_name": "final_result", "args": output})
                self.emit("turn_trace" if phase == "gameplay" else "compaction_trace", {**meta, "messages": trace, "model_used": self.model})
                for item in display_messages([message]):
                    if item["role"] == "thinking":
                        self.emit("llm_thinking" if phase == "gameplay" else "compaction_thinking", {"turn": turn, "content": item["content"]})
                if phase == "gameplay":
                    self.emit("llm_output", {"turn": turn, "tool": "final_result", "args": output})
                return output, accepted
            except asyncio.CancelledError:
                self.emit("llm_request_error", {**meta, "error": "cancelled; provider cost may be unknown"})
                raise
            except Exception as exc:
                if isinstance(exc, ProviderRequestError):
                    self.store.write(f"{request_id}-response.json", exc.response_body)
                    meta["provider_error"] = exc.response_body
                    meta["http_status"] = exc.status
                    continuity["request"] = "rejected"
                    diagnostics, _ = router_diagnostics(exc.response_body)
                    continuity.update(diagnostics)
                    if "signature" in str(exc).lower():
                        continuity["provider_feedback"] = "signature_rejected"
                if raw is None:
                    meta.update(normalize_usage({}))
                    self.state["attempts"].append({k: v for k, v in meta.items() if k not in ("continuity", "raw_usage")})
                self.emit("llm_request_error", {**meta, "error": str(exc), "latency_s": time.monotonic() - start,
                    "messages": display_messages(outbound["messages"] + ([raw["choices"][0]["message"]] if raw and raw.get("choices") and raw["choices"][0].get("message") else []))})
                kind = classify(exc)
                # A completion DID arrive and the failure is in reading it (bad JSON in
                # the tool arguments, a schema miss, an unexpected finish_reason): that
                # is the model's output, not the provider — unless the body itself
                # carried a provider error, which classify() already recognises.
                if kind == "transient" and raw is not None and not isinstance(exc, (ProviderRequestError, ProviderTransportError)) \
                        and not raw.get("error"):
                    kind = "output"
                if kind == "fatal":
                    raise
                if kind == "transient":
                    transient_failures += 1
                    waited = time.monotonic() - retry_started
                    wait = policy.wait_s(transient_failures - 1, getattr(exc, "retry_after_s", None))
                    if transient_failures > policy.transient_retries or waited + wait > policy.budget_s:
                        self.emit("llm_backoff", {"turn": turn, "phase": phase, "attempt": attempt, "kind": "exhausted",
                            "transient_failures": transient_failures, "transient_retries": policy.transient_retries,
                            "waited_s": round(waited, 1), "budget_s": policy.budget_s, "reason": describe(exc),
                            "http_status": getattr(exc, "status", None)})
                        print(f"  [Turn {turn}] Provider still failing after {transient_failures} transient failure(s) "
                              f"and {waited / 60:.1f} min of waiting ({describe(exc)}) — giving up.", flush=True)
                        raise
                    self.emit("llm_backoff", {"turn": turn, "phase": phase, "attempt": attempt, "kind": "waiting",
                        "transient_failures": transient_failures, "transient_retries": policy.transient_retries,
                        "wait_s": round(wait, 1), "waited_s": round(waited, 1), "budget_s": policy.budget_s,
                        "retry_after_s": getattr(exc, "retry_after_s", None), "reason": describe(exc),
                        "http_status": getattr(exc, "status", None)})
                    print(f"  [Turn {turn}] Provider failure ({describe(exc)}) — transient retry "
                          f"{transient_failures}/{policy.transient_retries}, waiting {wait:.0f}s "
                          f"({waited / 60:.1f} of {policy.budget_s / 60:.0f} min budget used)", flush=True)
                    await self._sleep(wait)
                    continue
                output_failures += 1
                if output_failures > output_budget:
                    raise
                self.emit("output_retry", {"turn": turn, "phase": phase, "content": str(exc)})
                if isinstance(exc, EmptyInputs):
                    # Identical re-asks reproduce an identical refusal, so each
                    # defect gets a correction appended for the retry. It extends the
                    # outbound history past the retained prefix (replay_check is a
                    # prefix check, so continuity holds) and, on acceptance, becomes
                    # part of the conversation like any other user turn.
                    if not any(m.get("content") == EMPTY_INPUTS_NOTE for m in messages):
                        messages = messages + [{"role": "user", "content": EMPTY_INPUTS_NOTE}]
                elif isinstance(exc, MissingToolCall):
                    # Same shape for a text reply in tool mode. For compaction the
                    # accepted history is discarded anyway (compact() replaces it
                    # with the handover), so the note never reaches a later request.
                    note = tool_call_note(phase)
                    if not any(m.get("content") == note for m in messages):
                        messages = messages + [{"role": "user", "content": note}]
                else:
                    # Every other output defect carries the error it caused, once per
                    # distinct error, so the retry is never a byte-identical re-ask.
                    note = output_defect_note(phase, mode, exc)
                    if not any(m.get("content") == note for m in messages):
                        messages = messages + [{"role": "user", "content": note}]
                # Failed requests are archived, not appended to accepted history.
                # Retry identical context without switching models or stripping state.
