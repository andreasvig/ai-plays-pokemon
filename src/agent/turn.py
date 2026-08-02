"""Turn manager: orchestrates the agent turn loop."""

import asyncio
import json
import logging
import random
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from PIL import Image
from pydantic_ai.messages import (
    ImageUrl,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    UserContent,
    UserPromptPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    RetryPromptPart,
)
from pydantic_ai.models.openai import OpenAIModel

from src.agent.agent import AgentDeps, GameAction, create_agent
from src.agent.task_master import (
    TaskMasterDeps,
    TaskMasterInput,
    TaskMasterOutput,
    create_task_master_agent,
    render_input as render_task_master_input,
)
from src.emulator import EmulatorClient, VisionPipeline, OCRRunner
from src.core import RunLogger, StateManager
from src.core.prompts import fill_prompt
from src.core.snapshots import SnapshotManager

logger = logging.getLogger(__name__)

# Markers that suggest a model rejected thinking/reasoning params
_THINKING_ERROR_MARKERS = (
    "reasoning", "thinking", "not supported", "unsupported parameter",
)

# Per-attempt timeouts for the LLM call (6 attempts, ~2x the old 3-attempt
# budget — Andreas 2026-06-17, so flaky providers don't poison a model's score).
# Escalating: short early attempts re-roll the provider fast, later attempts grow
# patient. DOUBLED again for models flagged `slow: true` in the registry (Gemma
# et al.). Provider routing also escalates per attempt (throughput → latency →
# OpenRouter default) — see _provider_routing_for_attempt.
_LLM_CALL_TIMEOUTS_S = (120.0, 120.0, 180.0, 240.0, 300.0, 360.0)

# Multiplier applied to every per-attempt timeout for a model marked `slow: true`
# (known-slow end-to-end latency, e.g. Gemma — cross-checked vs Artificial
# Analysis). Same attempt count, just twice the patience per attempt.
_SLOW_MODEL_TIMEOUT_MULT = 2.0

# Backoff between transient retries. Exponential growth (base * factor**idx)
# with a HIGH cap so the LATER attempts wait minutes, not seconds — a flapping
# provider gets real time to recover before we burn another attempt (Andreas
# 2026-06-17: "add more safety… exponentially more wait time so the longer waits
# get past 120s"). Equal jitter (half fixed + half random) gives a guaranteed
# GROWING FLOOR (the safety) while keeping half-range randomization to
# decorrelate many runs hammering the same provider at once. Resulting schedule
# of ceilings ≈ 2, 6, 18, 54, 162, 300s; the longest USED backoff (before the
# final attempt) clears ~120s.
_RETRY_BACKOFF_BASE_S = 2.0
_RETRY_BACKOFF_FACTOR = 3.0
_RETRY_BACKOFF_CAP_S = 300.0

# Transient errors that justify retrying the SAME model. The NoneType subscript
# is the OpenRouter "HTTP 200 with error body" wrapped-5xx case — pydantic-ai
# chokes on the missing `choices[0]`. See reference_openrouter-200-error-body-bug.
_TRANSIENT_ERROR_PATTERNS = (
    "'NoneType' object is not subscriptable",
)


# Seconds between in-flight "still working" heartbeat lines during an LLM call.
# Long/slow calls (slow models, big thinking budgets) stay visible; calls that
# finish under this interval print nothing, so fast turns aren't spammed.
_HEARTBEAT_INTERVAL_S = 20.0


async def _emit_heartbeat(label: str, interval_s: float = _HEARTBEAT_INTERVAL_S) -> None:
    """Print an elapsed-time line every ``interval_s`` until cancelled.

    Makes a long or fully hung in-flight LLM call visible in the terminal
    instead of dead-silent. Cancelled the moment the call returns/raises.
    """
    elapsed = 0.0
    try:
        while True:
            await asyncio.sleep(interval_s)
            elapsed += interval_s
            print(f"  {label} … {elapsed:.0f}s elapsed", flush=True)
    except asyncio.CancelledError:
        return


def _is_taskmaster_retryable(exc: BaseException) -> bool:
    """Whether a failed agent.run deserves a full re-invocation."""
    return _is_agent_invoke_retryable(exc)


def _is_agent_invoke_retryable(exc: BaseException) -> bool:
    """Whether a failed Player/TaskMaster agent.run deserves a full re-invocation."""
    if _is_transient_llm_error(exc):
        return True
    from pydantic_ai.exceptions import UnexpectedModelBehavior, UsageLimitExceeded

    if isinstance(exc, (UnexpectedModelBehavior, UsageLimitExceeded)):
        return True
    msg = str(exc).lower()
    if "output validation" in msg or "maximum retries" in msg:
        return True
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and (status >= 500 or status == 429):
        return True
    return False


def _is_transient_llm_error(exc: BaseException) -> bool:
    if isinstance(exc, asyncio.TimeoutError):
        return True
    # A JSONDecodeError raised mid-call is the provider returning a malformed or
    # truncated response body (SSE stream cut mid-chunk, an HTML error page, a
    # partial JSON object) — not a deterministic fault on our side. It's the same
    # provider-side-glitch family as the timeouts and the wrapped-5xx NoneType
    # case, and re-rolling the provider recovers it. (2026-06-17: a gpt-5.5 run
    # died at T31 on "Expecting value: line 225 column 1" purely because this was
    # classed non-transient and skipped the 6-attempt re-roll.) Match by name too
    # in case the decoder swaps the concrete class (e.g. orjson/simplejson).
    if isinstance(exc, json.JSONDecodeError) or type(exc).__name__ == "JSONDecodeError":
        return True
    msg = str(exc)
    return any(p in msg for p in _TRANSIENT_ERROR_PATTERNS)


def _retry_backoff_s(attempt_idx: int) -> float:
    """Equal-jitter exponential backoff before the next transient retry.

    ``attempt_idx`` is 0-based. The ceiling grows ``base * factor**idx``, capped
    at _RETRY_BACKOFF_CAP_S → ceilings ≈ 2, 6, 18, 54, 162, 300s. The actual
    wait is the upper half of that ceiling plus jitter (``ceiling/2 + U[0,
    ceiling/2]``): a guaranteed growing FLOOR for safety, with half-range
    randomization to decorrelate many runs retrying one struggling provider. So
    early attempts re-roll within seconds, but the later waits climb past two
    minutes — giving a flapping provider real time to recover.
    """
    ceiling = min(
        _RETRY_BACKOFF_CAP_S,
        _RETRY_BACKOFF_BASE_S * (_RETRY_BACKOFF_FACTOR ** attempt_idx),
    )
    return ceiling / 2.0 + random.uniform(0.0, ceiling / 2.0)


def _provider_routing_for_attempt(
    attempt_idx: int, base_provider: Optional[dict]
) -> Optional[dict]:
    """OpenRouter provider routing for a 0-based attempt index.

    Escalation (Andreas 2026-06-17): attempt 1 sorts by throughput (fastest
    backend), attempt 2 by latency (lowest TTFT), attempt 3+ drops the sort so
    OpenRouter's default uptime/price-weighted routing chooses the provider. A
    model's own registry ``provider`` block (e.g. an order/ignore allowlist) is
    preserved as the base; only the ``sort`` key is overridden on attempts 1-2.
    Returns None when there's nothing to send (no base, no sort).
    """
    base = dict(base_provider) if base_provider else {}
    if attempt_idx == 0:
        return {**base, "sort": "throughput"}
    if attempt_idx == 1:
        return {**base, "sort": "latency"}
    return base or None


def _settings_for_attempt(base_settings, provider_routing: Optional[dict]):
    """Clone model settings with this attempt's provider routing swapped in.

    ``base_settings`` is the OpenAIModelSettings built at agent creation (a
    TypedDict → plain dict). We copy it and replace ``extra_body['provider']``
    so the per-attempt routing escalation doesn't mutate the shared settings.
    Returns None if the result carries nothing (so callers pass no settings).
    """
    settings = dict(base_settings) if base_settings else {}
    extra = dict(settings.get("extra_body") or {})
    if provider_routing is not None:
        extra["provider"] = provider_routing
    else:
        extra.pop("provider", None)
    if extra:
        settings["extra_body"] = extra
    elif "extra_body" in settings:
        del settings["extra_body"]
    return settings or None


def _serialize_messages(messages) -> list[dict]:
    """Convert Pydantic AI messages to serializable dicts for logging."""
    trace = []
    for msg in messages:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, SystemPromptPart):
                    trace.append({"role": "system", "content": part.content})
                elif isinstance(part, UserPromptPart):
                    if isinstance(part.content, str):
                        content = part.content
                    elif isinstance(part.content, (list, tuple)):
                        # Multimodal content — extract text parts, replace images with placeholder
                        text_bits = []
                        for item in part.content:
                            if isinstance(item, str):
                                text_bits.append(item)
                            elif isinstance(item, ImageUrl):
                                text_bits.append("[image]")
                            else:
                                text_bits.append(f"[{type(item).__name__}]")
                        content = "\n".join(text_bits)
                    else:
                        content = str(part.content)
                    trace.append({"role": "user", "content": content})
                elif isinstance(part, ToolReturnPart):
                    # Full tool response (e.g. the perplexity answer) — no cap; the
                    # frontends contain it with a scrollable max-height instead.
                    trace.append({
                        "role": "tool_result",
                        "tool_name": part.tool_name,
                        "content": str(part.content),
                    })
                elif isinstance(part, RetryPromptPart):
                    trace.append({
                        "role": "retry",
                        "content": str(part.content),
                    })
                else:
                    trace.append({"role": "request_part", "type": type(part).__name__, "content": str(part)[:500]})
        elif isinstance(msg, ModelResponse):
            _extract_openrouter_reasoning(msg, trace)
            for part in msg.parts:
                if isinstance(part, TextPart):
                    # Prompted-output mode emits the GameAction JSON as a TextPart
                    # rather than a final_result tool call. Re-route to the same
                    # trace shape so terminal display formats it identically.
                    parsed_action = _try_parse_game_action(part.content)
                    if parsed_action is not None:
                        trace.append({
                            "role": "tool_call",
                            "tool_name": "final_result",
                            "args": parsed_action,
                        })
                    else:
                        trace.append({"role": "assistant", "content": part.content})
                elif isinstance(part, ThinkingPart):
                    trace.append({"role": "thinking", "content": part.content})
                elif isinstance(part, ToolCallPart):
                    trace.append({
                        "role": "tool_call",
                        "tool_name": part.tool_name,
                        "args": part.args if isinstance(part.args, dict) else str(part.args),
                    })
                else:
                    trace.append({"role": "response_part", "type": type(part).__name__, "content": str(part)[:500]})
        else:
            trace.append({"role": "unknown", "type": type(msg).__name__, "content": str(msg)[:500]})
    return trace


def _extract_openrouter_reasoning(msg: ModelResponse, trace: list[dict]) -> None:
    """Extract reasoning from OpenRouter's response if present."""
    try:
        if msg.provider_details:
            reasoning = msg.provider_details.get('reasoning')
            if reasoning:
                trace.append({"role": "thinking", "content": reasoning})
    except Exception:
        pass


def _extract_cost_from_messages(messages) -> float:
    """Extract total USD cost from OpenRouter provider_details.

    OpenRouter includes actual cost in response metadata. This is more
    accurate than computing from token counts + pricing tables.
    """
    total_cost = 0.0
    for msg in messages:
        if isinstance(msg, ModelResponse):
            try:
                if msg.provider_details:
                    cost = msg.provider_details.get("cost")
                    if cost is not None:
                        total_cost += float(cost)
            except Exception:
                pass
    return total_cost


def _screenshot_path_to_data_url(path: Optional[str]) -> Optional[str]:
    """Resolve a screenshot REF (file path) to an inline base64 PNG data URL.

    The run loop stores the previous task's first/last screen by REF (path) per
    Decision 8; this materializes the inline image only at TaskMaster-invocation
    time so the agent can actually look at the screen. Returns None when the path
    is falsy or unreadable — the TaskMaster then rates from the text evidence.
    """
    if not path:
        return None
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    import base64

    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


def _master_input_images(inp) -> list[dict]:
    """The actual screenshots a TaskMaster invocation saw, for the trace.

    Cold-start sees one image (the current/start screen); a handoff sees two
    (the previous task's start + end). Returns a list of ``{label, data_url}``,
    skipping any that aren't real data URLs (capture failed / none attached).
    """
    pairs = [
        ("Start screen", getattr(inp, "current_screen_image", None)),
        ("Previous task — start screen", getattr(inp, "prev_first_image", None)),
        ("Previous task — end screen", getattr(inp, "prev_last_image", None)),
    ]
    out: list[dict] = []
    for label, url in pairs:
        if isinstance(url, str) and url.strip().lower().startswith("data:"):
            out.append({"label": label, "data_url": url})
    return out


def _extract_provider_from_messages(messages) -> str:
    """Pull the OpenRouter provider name from response metadata.

    OpenRouter stamps `provider` (e.g. "DeepInfra", "Novita") into the
    response body when provider routing is active. Used for debugging
    which backend the router actually picked — especially when
    sort:"throughput" produces flaky results from one specific provider.

    `provider_details["provider"]` is the real source and is populated by
    ``src.core.patches`` (pydantic-ai drops it, so without that patch there is
    nothing here to find). The remaining lookups are version-tolerance for
    pydantic-ai moving the key.

    Deliberately NOT falling back to ``msg.model_name``: on OpenRouter that is
    the model slug (``moonshotai/kimi-k2.7-code``), whose prefix is the model's
    AUTHOR, not the endpoint that served the request — Moonshot's own model is
    routed to DeepInfra, Together, Fireworks and a dozen others. Returning it
    here would put a confident wrong provider in the logs, which is worse than
    the empty string that says "unknown".
    """
    _KNOWN_PROVIDERS = {
        "deepinfra", "chutes", "ambient", "siliconflow", "novita",
        "parasail", "venice", "together", "dekallm", "nextbit",
        "cloudflare", "google-vertex", "openai", "anthropic", "google",
    }

    for msg in messages or []:
        if not isinstance(msg, ModelResponse):
            continue
        try:
            pd = getattr(msg, "provider_details", None) or {}
            # Direct key lookups
            for key in ("provider", "x-or-provider", "provider_name"):
                v = pd.get(key)
                if v:
                    return str(v)
            # Scan dict for known provider strings
            for v in pd.values():
                if isinstance(v, str) and v.lower() in _KNOWN_PROVIDERS:
                    return v
            # Try vendor_details (newer pydantic-ai)
            vd = getattr(msg, "vendor_details", None) or {}
            for key in ("provider", "x-or-provider"):
                v = vd.get(key)
                if v:
                    return str(v)
        except Exception:
            pass
    return ""


def _usage_event(turn: int, usage, turn_cost: float, provider: str) -> dict:
    """Build the ``turn_usage`` payload for one turn.

    Split out as a pure function so the two fields added on 2026-08-02 —
    ``reasoning_tokens`` and ``provider`` — are testable without a live model.

    ``reasoning_tokens`` is the only ground truth for "did this model actually
    think on this turn". The ``llm_thinking`` event is not: it requires the
    provider to return a human-readable SUMMARY, and the two come apart — a
    gpt-5.6-sol probe returned 183 reasoning tokens with zero summary
    characters, and across the run archive gpt-5.6-luna logs thinking on 54% of
    turns while billing for it on all of them. OpenRouter reports the count in
    ``completion_tokens_details``, which pydantic-ai copies verbatim into
    ``usage.details`` (``models/openai.py:1408``).

    ABSENT IS NOT ZERO. A provider that never reports the field (Novita and
    StepFun both do this for step-3.7-flash, while DeepInfra reports ~2600 on
    identical output) means "unknown"; 0 means "measured, and it did not
    think". Collapsing them would turn a routing artifact into evidence that a
    thinking tier is dead, so an absent key stays None.
    """
    details = getattr(usage, "details", None) or {}
    return {
        "turn": turn,
        "request_tokens": usage.request_tokens,
        "response_tokens": usage.response_tokens,
        "total_tokens": usage.total_tokens,
        "reasoning_tokens": details.get("reasoning_tokens"),
        "cost_usd": turn_cost,
        # Which endpoint served the turn. Capability and token accounting both
        # vary by provider on the same model, and the harness re-rolls routing
        # per retry attempt, so this can differ turn to turn within one run.
        "provider": provider,
    }


def _should_retry_without_thinking(error: Exception) -> bool:
    """Check if an error suggests the model doesn't support thinking params."""
    error_str = str(error).lower()
    return any(marker in error_str for marker in _THINKING_ERROR_MARKERS)


def _try_parse(s) -> dict:
    """Try to parse a string as JSON dict, return empty dict on failure."""
    if isinstance(s, dict):
        return s
    try:
        result = json.loads(s)
        return result if isinstance(result, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


_GAME_ACTION_KEYS = {"inputs", "reasoning", "last_turn_succeeded", "memory_updates"}


def _try_parse_game_action(text) -> Optional[dict]:
    """If `text` parses to a JSON object with GameAction's full key set, return
    it. Used to recognize prompted-output TextParts as the same structured
    final_result that tool-mode emits — so dashboard + terminal display the
    parsed fields (Output / Reasoning / Last turn ok? / Memory) instead of one
    raw JSON blob."""
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        from src.core.patches import _robust_strip_markdown_fences
        cleaned = _robust_strip_markdown_fences(text)
    except Exception:
        cleaned = text
    try:
        parsed = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError, TypeError):
        return None
    if isinstance(parsed, dict) and _GAME_ACTION_KEYS.issubset(parsed.keys()):
        return parsed
    return None


def _truncate(text: str, max_len: int = 120) -> str:
    """Truncate text for terminal display."""
    text = text.replace("\n", " ").strip()
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."


def _print_trace_summary(turn_num: int, trace: list[dict]) -> None:
    """Print a structured summary of the turn's trace to the terminal."""
    step = 0

    def tag(extra: str = "") -> str:
        base = f"Turn {turn_num}"
        if step > 0:
            base += f", Step {step}"
        if extra:
            base += f", {extra}"
        return f"  [{base}]"

    for msg in trace:
        role = msg.get("role", "")

        if role == "system":
            print(f"{tag()} System prompt ({len(msg.get('content', ''))} chars)")

        elif role == "user":
            content = msg.get("content", "")
            lines = [l.strip() for l in content.split("\n") if l.strip() and not l.strip().startswith("[")]
            preview = lines[0] if lines else ""
            print(f"{tag()} Input ({len(content)} chars): {_truncate(preview, 80)}")

        elif role == "thinking":
            content = msg.get("content", "")
            first_line = content.split("\n")[0].strip() if content else ""
            print(f"{tag('Thinking')} {_truncate(first_line, 100)}")

        elif role == "tool_call":
            step += 1
            tool = msg.get("tool_name", "?")
            args = msg.get("args", "")

            if tool == "final_result":
                parsed = args if isinstance(args, dict) else _try_parse(args)
                action = parsed.get("inputs", "?")
                if isinstance(action, list):
                    action = "[" + ", ".join(str(a) for a in action) + "]"
                reasoning = parsed.get("reasoning", "")
                succeeded = parsed.get("last_turn_succeeded")
                if succeeded is True:
                    succeeded_str = "true"
                elif succeeded is False:
                    succeeded_str = "false"
                else:
                    succeeded_str = "null"
                memory = parsed.get("memory_updates", "")
                print(f"{tag('Output')} {action}")
                print(f"{tag('Last turn ok?')} {succeeded_str}")
                print(f"{tag('Reasoning')} {_truncate(reasoning, 200)}")
                print(f"{tag('Memory')} {_truncate(str(memory), 200) if memory else '(none)'}")
            else:
                if isinstance(args, dict):
                    args_preview = ", ".join(f"{k}={_truncate(str(v), 30)}" for k, v in args.items())
                else:
                    args_preview = _truncate(str(args), 60)
                print(f"{tag('Call')} {tool}({args_preview})")

        elif role == "tool_result":
            tool = msg.get("tool_name", "")
            if tool == "final_result":
                continue
            content = msg.get("content", "")
            print(f"{tag('Response')} {_truncate(str(content), 100)}")

        elif role == "retry":
            print(f"{tag('Retry')} {_truncate(msg.get('content', ''), 100)}")


@dataclass
class TaskMasterInvocation:
    """Result of one TaskMaster invocation.

    The seam between the run loop and the TaskMaster agent: the run loop only
    needs the structured ``output`` plus the trace/cost/model for logging. Tests
    inject a runner that returns scripted instances of this, so the handoff
    orchestration can be exercised without OpenRouter or mGBA.
    """

    output: TaskMasterOutput
    trace: list[dict] = field(default_factory=list)
    cost_usd: float = 0.0
    tool_cost_usd: float = 0.0
    model_used: str = ""


class TaskMasterRunner:
    """Maps a ``TaskMasterInput`` → ``TaskMasterInvocation`` via pydantic-ai.

    Construction mirrors the Player's: ``create_task_master_agent`` resolves
    model + output-mode from config; the runner owns ``request_limit`` (round
    count, NOT an aggregate token cap — a web-research agent accumulates result
    text across tool rounds). Fresh ``TaskMasterDeps`` per invocation (incl. an
    empty ``tool_costs`` accumulator) keeps each call self-contained
    (statelessness rule).

    The whole class is the injectable seam: ``TurnManager`` accepts a
    ``task_master_runner`` and only ever awaits ``.invoke_async(inp)``, so a test
    can pass a stub with the same one-method surface.
    """

    def __init__(self, config: dict[str, Any]):
        from src.agent.task_master import (
            DEFAULT_INVOKE_RETRIES,
            DEFAULT_MAX_SEARCHES,
            DEFAULT_REQUEST_LIMIT,
            DEFAULT_SEARCH_MODEL,
        )

        self.config = config
        self._agent, self._model_settings = create_task_master_agent(config)
        tm_cfg = config.get("task_master") or {}
        self._request_limit = int(
            tm_cfg.get("request_limit", DEFAULT_REQUEST_LIMIT)
        )
        self._invoke_retries = int(
            tm_cfg.get("invoke_retries", DEFAULT_INVOKE_RETRIES)
        )
        self._model_used = (
            config.get("task_master_model") or config.get("llm_model") or ""
        )
        self._search_model = (
            config.get("task_master", {}).get("search_model") or DEFAULT_SEARCH_MODEL
        )
        self._max_searches = int(
            (config.get("task_master") or {}).get("max_searches", DEFAULT_MAX_SEARCHES)
        )
        # User-message templates (config-provided wins; module defaults apply when
        # omitted). Two: the handoff template and the cold-start template.
        tm_cfg = config.get("task_master", {}) or {}
        self._user_prompt = tm_cfg.get("user_prompt")
        self._cold_start_prompt = tm_cfg.get("user_prompt_cold_start")

    async def invoke_async(
        self, inp: TaskMasterInput, *, is_cold_start: bool = False
    ) -> TaskMasterInvocation:
        """Run the TaskMaster agent once on ``inp`` (awaited from the run loop).

        MUST be awaited directly from the already-running loop event loop — it
        must NOT wrap the agent call in ``asyncio.run`` (that raises "cannot be
        called from a running event loop" inside the live loop).

        When the input carries data-URL screenshots (the first/last screen of the
        previous task, resolved from refs by the run loop), they are attached as
        image content parts — mirroring the Player's text-then-image layout — so
        the TaskMaster can actually cross-check the success criteria against the
        screen instead of seeing only a textual placeholder.
        """
        from pydantic_ai import capture_run_messages
        from pydantic_ai.usage import UsageLimits

        from src.agent.task_master import _looks_like_data_url

        text = render_task_master_input(
            inp,
            is_cold_start=is_cold_start,
            template=self._user_prompt,
            cold_start_template=self._cold_start_prompt,
        )

        content: list[Any] = [text]
        for label, img in (
            ("CURRENT screen (starting point)", inp.current_screen_image),
            ("START of the previous task", inp.prev_first_image),
            ("END of the previous task", inp.prev_last_image),
        ):
            if _looks_like_data_url(img):
                content.append(f"=== Screenshot — {label} ===")
                content.append(ImageUrl(url=img))
        user_message: Any = content if len(content) > 1 else text

        usage_limits = UsageLimits(request_limit=self._request_limit)

        agent = self._agent
        model_settings = self._model_settings
        max_attempts = self._invoke_retries
        accumulated_cost = 0.0
        accumulated_tool_cost = 0.0
        last_error: BaseException | None = None

        for attempt_idx in range(max_attempts):
            attempt_num = attempt_idx + 1
            if attempt_idx > 0:
                await asyncio.sleep(_retry_backoff_s(attempt_idx - 1))
                agent, model_settings = create_task_master_agent(self.config)

            deps = TaskMasterDeps(
                is_cold_start=is_cold_start,
                search_model=self._search_model,
                tool_costs=[],
                max_searches=self._max_searches,
                # Which game the research tool should answer about. Route order
                # and gym teams are per-GAME facts, so asking a web model about
                # FireRed while the screen shows Emerald returns confident,
                # wrong answers.
                game_name=self.config.get("game_name") or "Pokemon FireRed",
            )

            kwargs: dict[str, Any] = {}
            if model_settings:
                kwargs["model_settings"] = model_settings

            with capture_run_messages() as captured:
                try:
                    result = await agent.run(
                        user_message,
                        deps=deps,
                        usage_limits=usage_limits,
                        **kwargs,
                    )
                except Exception as exc:
                    last_error = exc
                    if captured:
                        accumulated_cost += _extract_cost_from_messages(captured)
                        accumulated_tool_cost += float(sum(deps.tool_costs))
                    if (
                        attempt_idx < max_attempts - 1
                        and _is_taskmaster_retryable(exc)
                    ):
                        print(
                            f"  [TaskMaster] invoke attempt {attempt_num}/"
                            f"{max_attempts} failed ({type(exc).__name__}: "
                            f"{exc}); retrying after backoff"
                        )
                        continue
                    raise

            messages = list(captured)
            trace = _serialize_messages(messages)
            cost = _extract_cost_from_messages(messages) + accumulated_cost
            tool_cost = float(sum(deps.tool_costs)) + accumulated_tool_cost
            if attempt_idx > 0:
                print(
                    f"  [TaskMaster] invoke attempt {attempt_num}/"
                    f"{max_attempts} succeeded after earlier failure(s)"
                )
            return TaskMasterInvocation(
                output=result.output,
                trace=trace,
                cost_usd=cost,
                tool_cost_usd=tool_cost,
                model_used=self._model_used,
            )

        if last_error is not None:
            raise last_error
        raise RuntimeError("TaskMaster invoke exhausted retries without result")


class _TeeWriter:
    """Duplicates writes to both a stream and a file."""

    def __init__(self, original, log_file):
        self._original = original
        self._log_file = log_file

    def write(self, data):
        self._original.write(data)
        try:
            self._log_file.write(data)
            self._log_file.flush()
        except Exception:
            pass

    def flush(self):
        self._original.flush()
        try:
            self._log_file.flush()
        except Exception:
            pass

    def __getattr__(self, name):
        return getattr(self._original, name)


# Default Player per-turn user-message template. Overridable via the top-level
# `user_prompt` config key (mirrors `system_prompt`). {{placeholders}} are filled
# with computed VALUES each turn (fill_prompt); the per-turn loop + screen
# heads-up are pre-rendered into their values. Keep one logical line per
# paragraph for readability.
DEFAULT_PLAYER_USER_PROMPT = """\
{{screen_heads_up}}

## Recent OCR Text
Cleaned text captured from the screen between the last turn and now. May include scrolling dialogue, menu labels, and UI text. Use as ground-truth for exact character sequences; trust the screenshot for spatial layout.
{{ocr_text}}

## Memory
```json
{{memory_json}}
```

## Previous Turns
{{previous_turns}}

## Current Task
{{task_block}}

Output your action (inputs) and update memory_updates with any new information.{{handoff_instruction}}
"""


class TurnManager:
    """Orchestrates the turn loop: screenshot -> think -> act -> repeat."""

    def __init__(
        self,
        config: dict[str, Any],
        task_master_runner: Optional[Any] = None,
    ):
        self.config = config
        self.max_turns = config.get("max_turns_per_task", 50)

        # Third stop condition for a casual run, alongside the turn cap and the
        # `stop_at` story event: an all-in USD ceiling. None = unbounded (the
        # historical behaviour, and what every official run gets — locked #8
        # says pace is the only bound there). Validated in src/config.py.
        _ms = config.get("max_spend_usd")
        self.max_spend_usd: Optional[float] = float(_ms) if _ms is not None else None
        # Spend already on the clock when this SEGMENT started. A continue seeds
        # total_cost_usd from the source run so the reported cost is cumulative,
        # but the budget — like max_turns and stop_at — is a property of the
        # segment you are launching, not of the whole lineage. Set in
        # _run_loop_async before the cold start.
        self._spend_baseline_usd: float = 0.0
        # Latched when the ceiling ended the run, so the summary can say so.
        self._budget_stopped: bool = False
        # The turn cap this run is launched with. Arrives as a run_loop argument
        # rather than a config key, so _run_loop_async records it here for the
        # summary; None until a loop starts.
        self._turn_limit: Optional[int] = None

        # Cooperative stop hook. A long-lived caller (the control-center executor)
        # sets this to a predicate checked at the top of every turn; when it
        # returns True the loop raises KeyboardInterrupt so the existing
        # interrupt→savepoint→cancelled path fires. Without it the loop only stops
        # on a real Ctrl-C, so a UI "stop" never actually halts the run.
        self._should_stop: Optional[Any] = None

        # Dedicated event loop + daemon thread that player turns run ON, so the
        # main loop stays free to watch the stop flag. A single turn can sit
        # inside one LLM call for MINUTES (slow "thinking" models use 240s
        # per-attempt timeouts), and if that call ever blocks its event loop a
        # same-loop stop-watcher is starved → the kill hangs for the whole turn.
        # Running the turn off-thread lets the main loop poll + ABANDON it
        # instantly. Lazily started on the first stop-armed turn; torn down in
        # run_loop's finally. See _run_turn_or_stop.
        self._turn_worker_loop: Optional[asyncio.AbstractEventLoop] = None
        self._turn_worker_thread: Optional[threading.Thread] = None
        # How long a stop waits for a cancelled turn to unwind cleanly before
        # abandoning it (an async LLM call unwinds near-instantly; a turn wedged
        # in a blocking call is abandoned — the run is being killed regardless).
        self._stop_unwind_timeout_s = 2.0

        # TaskMaster gate. When enabled the Player prompt gains a current-task +
        # task-progress block each turn and the budget validator (attached in
        # create_agent) is fed the per-task turn count via AgentDeps. When
        # disabled, every Player-facing behavior below is bypassed and the
        # legacy single-agent path is unchanged.
        self.task_master_enabled = bool(
            config.get("task_master", {}).get("enabled", False)
        )
        # How many turns the Player has spent on the CURRENT task (1-based, set
        # at the top of each turn). Reset to 0 on each TaskMaster handoff.
        self.current_task_turn = 0
        # 1-based index of the current task (cold-start task is 1). Drives the
        # task_index carried on each player turn_start + the task-lifecycle events.
        self.current_task_index = 0
        # The current task the Player is executing, as a dict with at least a
        # `title`/`goal`; `description` and `success_criteria` optional. Set by
        # the cold-start / handoff logic from TaskMaster's returned task.
        self.current_task: Optional[dict] = None

        # TaskMaster history + per-task evidence accumulators (only used when TM
        # enabled). `task_history` is the list the savepoint persists and the
        # rolling-window inputs are built from. The two `_cur_task_*` buffers
        # collect the just-finished task's evidence so the next TaskMaster
        # invocation can rate it.
        self.task_history: list[dict] = []
        self._cur_task_player_reasons: list[str] = []
        self._cur_task_first_image: Optional[str] = None
        self._cur_task_last_image: Optional[str] = None
        # Separate TaskMaster cost counter (Decision 10) — distinct from the
        # Player's total_cost_usd so strategy vs tactics cost is comparable.
        self.task_master_cost_usd = 0.0
        # TaskMaster invocations counted as turns (Andreas 2026-06-17): each
        # cold-start + handoff is +1 reported turn, added to player turns for the
        # leaderboard total. Player/game turns (self.turn_number) stay separate so
        # gate deadlines keep measuring game progress only.
        self.task_master_turns = 0
        # Rolling-window size for the TaskMaster's view of its own prior outputs.
        self.history_window_n = int(
            config.get("task_master", {}).get("history_window_n", 20)
        )
        # The injectable seam. Built lazily on the real path (so a TM-disabled
        # run never constructs the agent), or injected by tests as a stub with a
        # matching `async invoke_async(TaskMasterInput) -> TaskMasterInvocation`.
        self._task_master_runner = task_master_runner

        # These get set during setup
        self.emulator: Optional[EmulatorClient] = None
        self.state: Optional[StateManager] = None
        self.vision: Optional[VisionPipeline] = None
        self.logger: Optional[RunLogger] = None
        self.ocr: Optional[OCRRunner] = None
        # Observe-only Referee (set in setup() if a `referee` config block is
        # present). Polled in the turn loop; never reachable from agent.py.
        self.referee: Optional[Any] = None
        # Latched True when the referee signals the final ladder rung is reached
        # (locked decision #8) — the run loop breaks as a WIN and the summary is
        # stamped `completed`. Stays False on every other exit path.
        self._referee_completed: bool = False
        # Latched when the loop gives up because a turn produced NO valid Player
        # output — every retry and every fallback model exhausted (a provider
        # 400/403, a dead model id, all attempts timing out). Such a run is not
        # a result: nothing was played, and on the leaderboard it would read as
        # the model scoring zero rather than never having answered. The summary
        # is stamped `crashed`, which `SummaryRow.leaderboard_eligible` already
        # excludes and `_finalize_run` already voids by withholding
        # `benchmark_version`. Without this the loop exited with status None and
        # the executor's fallback stamped `completed` — see
        # src/app/executor.py's `_finalize_run`.
        self._aborted_no_output: bool = False
        self._abort_error: Optional[str] = None
        # Finalisation guards. The loop's clean exit writes the summary +
        # restores stdout, but a crash or a cooperative stop aborts before that;
        # run_single_loop then calls finalize_run_summary() so a killed/failed
        # run still produces a readable report (Andreas 2026-06-17). The guard
        # makes the call idempotent whichever path reaches it first; the
        # stdout/log handles default to None so finalisation is safe even if the
        # loop crashed before they were opened.
        self._summary_finalized: bool = False
        self._orig_stdout = None
        self._terminal_log = None
        self.agent, self.model_settings, self.fallback_models = create_agent(config)
        self.max_steps_per_turn = config.get("max_steps_per_turn", 10)
        self.max_turns_before_trim = config.get("max_turns_before_trim")
        self.historic_images_count = config.get("historic_images_count", 0)
        self.task_override_snapshot = config.get("task_override_snapshot", False)

        # Turn history
        self.turn_explanations: list[dict] = []
        # Real game-turn number for each turn_explanations entry, same index.
        # Handoff turns produce a turn_start but NO explanation, so explanation
        # index != turn number once any handoff has happened — this keeps the
        # "## Previous Turns" headings and historic-screenshot action lookups on
        # the true turn number instead of drifting by the handoff count.
        self._explanation_turns: list[int] = []
        # Ring buffer of (turn_number, PIL.Image) for the last K screenshots —
        # only populated when historic_images_count > 0. Bounded by K so memory
        # stays flat regardless of run length.
        self.turn_screenshots: list[tuple[int, Image.Image]] = []
        self.turn_number = 0

        # Highest turn whose game-state mutation has fully settled — i.e. the
        # turn the live emulator state (and thus any savepoint taken right now)
        # is byte-exact for. Advanced at the END of each committed turn (after
        # buttons press + screen settle) and on each TaskMaster handoff. A stop
        # or crash that fires mid-turn (during the LLM call, before any button
        # press) leaves the emulator at THIS turn's state, never the in-flight
        # one — so the kill savepoint stamps this, and resume re-runs the
        # interrupted turn from a clean boundary. Restored to the savepoint turn
        # on a continued run.
        self._last_settled_turn = 0

        # Tasks (loaded from run folder)
        self.tasks: Optional[dict] = None

        # Cost tracking
        self.total_cost_usd = 0.0
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.turn_costs: list[dict] = []

        # Run timing
        self._run_start_time: Optional[float] = None
        # Wall-clock seconds already spent in a prior run (--continue). Added to
        # this session's duration so the reported elapsed time is cumulative.
        self._prior_duration_s: float = 0.0

        # Savepoint config (validated in src/config.py)
        sp = config.get("savepoints") or {}
        self.savepoint_every_n_turns: int = sp.get("every_n_turns", 0)
        self.savepoint_at_end: bool = sp.get("at_end", False)
        self.savepoint_on_crash: bool = sp.get("on_crash", False)
        self._snapshot_mgr: Optional[SnapshotManager] = None

    def setup(
        self,
        emulator: EmulatorClient,
        state: StateManager,
        vision: VisionPipeline,
        logger: RunLogger,
        ocr: Optional[OCRRunner] = None,
    ) -> None:
        """Inject dependencies."""
        self.emulator = emulator
        self.state = state
        self.vision = vision
        self.logger = logger
        self.ocr = ocr

        # SnapshotManager needs state_file path + emulator handle. We point it
        # at the run's state.json so save_run_savepoint copies the live agent
        # memory, not the global state_file from config.
        sp_enabled = (
            self.savepoint_every_n_turns > 0
            or self.savepoint_at_end
            or self.savepoint_on_crash
        )
        if sp_enabled:
            sp_config = dict(self.config)
            sp_config["state_file"] = str(logger.run_dir / "state.json")
            self._snapshot_mgr = SnapshotManager(sp_config, emulator)

        # Referee (observe-only): when a `referee` config block is present, load
        # its checkpoint ladder and build a Referee that polls game memory
        # out-of-band each turn. The player agent is provably blind to it — the
        # import lives here, in turn.py, and nothing in src/agent/agent.py
        # references the referee (module boundary, enforced by a grep check).
        self.referee = None
        referee_cfg = self.config.get("referee")
        if referee_cfg:
            from src.referee.checkpoints import load_ladder
            from src.referee.referee import Referee

            ladder = load_ladder(referee_cfg["checkpoints"])
            self.referee = Referee(
                nodes=ladder.nodes,
                emulator=emulator,
                logger=logger,
                run_dir=logger.run_dir,
                # Calibration runs leave enforce off (observe-only); only an
                # explicit `enforce: true` arms the deadline gates.
                enforce=bool(referee_cfg.get("enforce", False)),
                # Casual `--stop-at`: end the run when this gate latches. None
                # on every official run — a benchmark ends at its own ladder.
                stop_at=referee_cfg.get("stop_at") or None,
            )

        # tasks.json supersession (plan.md "Reconciliation"): when TaskMaster is
        # enabled it OWNS the current task — the legacy tasks.json read is
        # bypassed entirely and TM state lives in task_master_state.json instead.
        # The block below runs ONLY on the TM-disabled legacy path.
        if self.task_master_enabled:
            cfg_goal = (self.config.get("task") or {}).get("goal", "?")
            print(f"  Task source: TASKMASTER (supersedes tasks.json) — meta-goal: {cfg_goal!r}")
            return

        # Load tasks from run folder if present — UNLESS the config has
        # task_override_snapshot=true, in which case we ignore the snapshot's
        # task and let the user-message build fall through to config["task"]
        # (which matches what the system prompt already uses).
        tasks_path = logger.run_dir / "tasks.json"
        if tasks_path.exists() and not self.task_override_snapshot:
            with open(tasks_path) as f:
                self.tasks = json.load(f)
            snap_goal = (self.tasks or {}).get("goal", "?")
            print(f"  Task source: SNAPSHOT — goal: {snap_goal!r}")
        else:
            cfg_goal = (self.config.get("task") or {}).get("goal", "?")
            if tasks_path.exists() and self.task_override_snapshot:
                snap_goal = "?"
                try:
                    with open(tasks_path) as f:
                        snap_goal = json.load(f).get("goal", "?")
                except Exception:
                    pass
                print(
                    f"  Task source: CONFIG (override active) — goal: {cfg_goal!r} "
                    f"(snapshot goal {snap_goal!r} ignored)"
                )
            else:
                print(f"  Task source: CONFIG — goal: {cfg_goal!r}")

    def save_savepoint(self, kind: str, turn: Optional[int] = None) -> Optional[str]:
        """Write a savepoint. Returns the path on success.

        ``turn`` overrides the stamped turn number; it defaults to
        ``self.turn_number`` (a clean periodic/handoff/end save). Crash/stop
        handlers pass ``self._last_settled_turn`` instead: an interrupt can land
        mid-turn (turn_number already incremented, but no button pressed yet),
        and the live emulator state is the LAST SETTLED turn, not the in-flight
        one — so stamping the in-flight number would claim a turn that never
        actually happened and make resume skip it.

        Best-effort: failures are logged but do not raise. Safe to call from
        crash handlers in the caller.
        """
        save_turn = self.turn_number if turn is None else turn
        if self._snapshot_mgr is None or save_turn <= 0:
            return None
        try:
            tm_state = self._task_master_state() if self.task_master_enabled else None
            ref_state = self.referee.export_state() if self.referee is not None else None
            path = self._snapshot_mgr.save_run_savepoint(
                run_dir=self.logger.run_dir,
                turn=save_turn,
                kind=kind,
                task_master_state=tm_state,
                referee_state=ref_state,
            )
            self.logger.log_custom("savepoint_saved", {
                "turn": save_turn, "kind": kind, "path": str(path),
            })
            print(f"  [Turn {save_turn}] Savepoint ({kind}): {path}")
            return str(path)
        except Exception as e:
            self.logger.log_custom("savepoint_error", {
                "turn": save_turn, "kind": kind, "error": str(e),
            })
            print(f"  [Turn {save_turn}] Savepoint ({kind}) FAILED: {e}")
            return None

    # --- TaskMaster orchestration -------------------------------------------

    def _get_task_master_runner(self) -> Any:
        """Lazily build the real TaskMasterRunner, or return the injected stub.

        Built lazily so a TM-disabled run never constructs the agent, and tests
        can inject a stub with a matching ``async invoke_async(...) ->
        TaskMasterInvocation`` surface before the loop runs.
        """
        if self._task_master_runner is None:
            self._task_master_runner = TaskMasterRunner(self.config)
        return self._task_master_runner

    def _meta_goal(self) -> str:
        """Run meta-goal = the existing top-level config task.goal (Decision 6)."""
        task = self.config.get("task") or {}
        if isinstance(task, str):
            return task
        return task.get("goal", "Play the game.")

    def _prior_task_outputs(self, *, include_current_unrated: bool = False) -> list[str]:
        """Rolling window (oldest first) of TaskMaster's own prior outputs.

        One entry per task: the task it issued (title + description + success
        criteria) and the verdict it later gave. Trimmed to ``history_window_n``
        entries.

        ``include_current_unrated`` appends the task that just finished — the one
        this invocation must rate — as the newest entry, with its rating left as
        a fill-in placeholder. Without this the first handoff shows "(none)" and
        the master never sees the spec (esp. success_criteria) it is rating
        against; it only has the Player's trace, which is the wrong thing to
        anchor a verdict on.
        """
        records = list(self.task_history)
        if include_current_unrated and self.current_task:
            records.append({"task": self.current_task, "rating": None})

        lines: list[str] = []
        for rec in records:
            task = rec.get("task") or {}
            title = task.get("title", "?")
            desc = task.get("description", "")
            crit = task.get("success_criteria", "")
            rating = rec.get("rating") or {}
            if rating:
                status = rating.get("status", "(unrated)")
                reasoning = rating.get("reasoning", "")
                verdict = f"your rating: {status}" + (f" — {reasoning}" if reasoning else "")
            else:
                verdict = "your rating: ⟵ THIS is the task you must rate now (set rating_of_previous_task)"
            entry = f"task={title!r}\n  description: {desc}"
            if crit:
                entry += f"\n  success_criteria: {crit}"
            entry += f"\n  {verdict}"
            lines.append(entry)
        if self.history_window_n > 0:
            lines = lines[-self.history_window_n:]
        return lines

    def _current_screen_data_url(self) -> Optional[str]:
        """Capture the current screen as a data-URL for the cold-start TaskMaster
        input (so it can see where the Player is starting). Returns None if the
        emulator/vision aren't ready or capture fails — the master then plans
        from text alone."""
        try:
            shot = self.emulator.capture_screenshot(preprocess=True)
            return self.vision.image_to_data_url(shot)
        except Exception:
            return None

    def _player_memory_json(self) -> Optional[str]:
        """JSON snapshot of the Player's persistent memory for the TaskMaster
        input. Returns None when empty so the renderer shows "(empty)"."""
        try:
            view = self.state.get_truncated_view()
        except Exception:
            return None
        if not view:
            return None
        try:
            return json.dumps(view, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(view)

    def _build_cold_start_input(self) -> TaskMasterInput:
        """Cold-start input: no previous task, but show the master the current
        screen, the (likely empty) Player memory, and the per-task turn budget so
        the first task is informed and correctly sized."""
        return TaskMasterInput(
            meta_goal=self._meta_goal(),
            current_screen_image=self._current_screen_data_url(),
            player_memory=self._player_memory_json(),
            max_turns=self.max_turns if self.max_turns > 0 else None,
        )

    def _build_handoff_input(self, handoff: Optional[Any]) -> TaskMasterInput:
        """Assemble the rolling-window input for a boundary TaskMaster call.

        Feeds the just-finished task's evidence: the Player's verbatim
        self-assessment block (Decision 9), its per-turn reasons, the first/last
        screenshot refs (Decision 8), the Player's memory, and how much of the
        per-task budget it used. Withholds nothing the contract asks for; the
        TaskMaster rates from this.
        """
        self_assessment: Optional[str] = None
        if handoff is not None:
            self_assessment = (
                f"self_assessment={handoff.self_assessment}; "
                f"task_summary={handoff.task_summary}"
            )
        return TaskMasterInput(
            meta_goal=self._meta_goal(),
            prior_task_outputs=self._prior_task_outputs(include_current_unrated=True),
            prev_player_reasons=list(self._cur_task_player_reasons),
            prev_first_image=_screenshot_path_to_data_url(self._cur_task_first_image),
            prev_last_image=_screenshot_path_to_data_url(self._cur_task_last_image),
            prev_player_self_assessment=self_assessment,
            player_memory=self._player_memory_json(),
            max_turns=self.max_turns if self.max_turns > 0 else None,
            turns_used=self.current_task_turn,
        )

    def _record_task_master_turn(self, inv) -> None:
        """Account one TaskMaster invocation as a turn (Andreas 2026-06-17).

        TaskMaster's strategy calls now contribute to BOTH the run's reported
        turn count (``task_master_turns``, added to player turns for the
        leaderboard total) and the per-turn cost breakdown — previously the cost
        landed only in the grand-total bucket and was attached to no turn, so it
        was invisible in any per-turn view. The cost still also accumulates in
        ``task_master_cost_usd`` for the strategy-vs-tactics split (Decision 10).
        The per-turn entry is tagged ``kind:"task_master"``; player entries carry
        no ``kind`` (treat absent as the player).
        """
        cost = inv.cost_usd + inv.tool_cost_usd
        self.task_master_cost_usd += cost
        self.task_master_turns += 1
        self.turn_costs.append({
            "turn": self.turn_number,
            "cost_usd": cost,
            "model": inv.model_used,
            "kind": "task_master",
        })

    async def _cold_start(self) -> None:
        """First TaskMaster invocation: set task 1 (no rating).

        Emits, in order: ``task_master_trace{1}`` → ``task_started{1}``. No
        ``task_completed`` — there is no previous task to rate.
        """
        print("  Cold start: invoking TaskMaster for the opening task...")
        runner = self._get_task_master_runner()
        cs_input = self._build_cold_start_input()
        inv = await runner.invoke_async(cs_input, is_cold_start=True)
        self._record_task_master_turn(inv)
        self.current_task_index = 1
        self.current_task_turn = 0
        task = inv.output.task

        self.logger.log_task_master_trace(
            task_index=1,
            messages=inv.trace,
            model_used=inv.model_used,
            cost_usd=inv.cost_usd,
            search_cost_usd=inv.tool_cost_usd,
            input_images=_master_input_images(cs_input),
        )
        self.logger.log_task_started(
            task_index=1,
            title=task.title,
            description=task.description,
            success_criteria=task.success_criteria,
            global_turn=self.turn_number + 1,
        )
        self.current_task = task.model_dump()
        # Start a fresh evidence buffer for task 1.
        self._cur_task_player_reasons = []
        self._cur_task_first_image = None
        self._cur_task_last_image = None
        print(f"  Task 1: {task.title!r}")

    async def _handle_handoff(
        self, result: Optional[GameAction], handoff: Optional[Any]
    ) -> None:
        """Boundary TaskMaster invocation: rate task N, set task N+1.

        Emits, in order: ``task_completed{N}`` → ``task_master_trace{N+1}`` →
        ``task_started{N+1}``. Appends the rating to task N's history record,
        resets the per-task turn counter, and advances current_task to N+1.
        ``result`` is unused (the Player's action is discarded on a handoff turn)
        and may be None when the run loop force-hands-off at the budget boundary.
        """
        n = self.current_task_index
        why = "handed back" if handoff is not None else "budget exhausted"
        print(f"  Handoff after task {n} ({why}): invoking TaskMaster...")

        runner = self._get_task_master_runner()
        ho_input = self._build_handoff_input(handoff)
        inv = await runner.invoke_async(ho_input, is_cold_start=False)
        self._record_task_master_turn(inv)
        out: TaskMasterOutput = inv.output

        # 1. Rate the just-finished task N (task_completed{N}). Backward-stamp.
        # Carry the Player's own hand-back (self-assessment + summary) on the same
        # event so the frontends can show the Player's claim beside the Master's
        # ruling. None when the run-loop force-hands-off at the budget boundary
        # (the Player emitted no return block that turn).
        rating_dict: Optional[dict] = None
        if out.rating_of_previous_task is not None:
            rating_dict = out.rating_of_previous_task.model_dump()
            self.logger.log_task_completed(
                task_index=n,
                rating=rating_dict,
                player_self_assessment=(
                    handoff.self_assessment if handoff is not None else None
                ),
                player_task_summary=(
                    handoff.task_summary if handoff is not None else None
                ),
            )

        # Record the finished task + its rating + evidence refs in history.
        self.task_history.append({
            "task": self.current_task,
            "rating": rating_dict,
            "first_image_ref": self._cur_task_first_image,
            "last_image_ref": self._cur_task_last_image,
            "player_reasons": list(self._cur_task_player_reasons),
        })

        # 2. Set task N+1: trace then started.
        next_index = n + 1
        next_task = out.task
        self.logger.log_task_master_trace(
            task_index=next_index,
            messages=inv.trace,
            model_used=inv.model_used,
            cost_usd=inv.cost_usd,
            search_cost_usd=inv.tool_cost_usd,
            input_images=_master_input_images(ho_input),
        )
        self.logger.log_task_started(
            task_index=next_index,
            title=next_task.title,
            description=next_task.description,
            success_criteria=next_task.success_criteria,
            global_turn=self.turn_number + 1,
        )

        # 3. Advance state + reset per-task buffers.
        self.current_task_index = next_index
        self.current_task = next_task.model_dump()
        self.current_task_turn = 0
        self._cur_task_player_reasons = []
        self._cur_task_first_image = None
        self._cur_task_last_image = None
        status = rating_dict["status"] if rating_dict else "(none)"
        print(f"  Task {n} rated {status}; Task {next_index}: {next_task.title!r}")

        # Checkpoint at the task boundary. A handoff is a natural, semantically
        # clean savepoint; taking one here tightens the worst-case replay window
        # for an official benchmark that dies between periodic saves (a hard kill
        # skips the on_crash handler, so resume falls back to the last bundle).
        # Idempotent with a periodic save at the same turn (the dir is rewritten).
        # A handoff turn presses no buttons, so the emulator is unchanged from the
        # prior settle — but the turn counter advanced and TaskMaster state is now
        # persisted, so this IS a clean resume boundary. Mark it for a later kill.
        self._last_settled_turn = self.turn_number
        self.save_savepoint("handoff")

    def _task_master_state(self) -> dict:
        """Serializable TaskMaster state for a savepoint (Phase B4)."""
        return {
            "current_task": self.current_task,
            "current_task_index": self.current_task_index,
            "current_task_turn": self.current_task_turn,
            "task_history": self.task_history,
        }

    def restore_task_master_state(self, state: dict) -> None:
        """Reload TaskMaster state from a savepoint (--continue path).

        Setting ``current_task`` here suppresses the cold-start in the run loop,
        so a resumed run keeps its task index + history instead of restarting at
        task 1.
        """
        self.current_task = state.get("current_task")
        self.current_task_index = int(state.get("current_task_index", 0) or 0)
        self.current_task_turn = int(state.get("current_task_turn", 0) or 0)
        self.task_history = list(state.get("task_history") or [])

    def restore_player_history(
        self,
        events_path: "Path | str",
        screenshots_dir: "Path | str",
        up_to_turn: int,
    ) -> None:
        """Rebuild the Player's in-memory turn context from a source run's
        events.jsonl so a continued run resumes seamlessly — same turn number,
        same "## Previous Turns" history, same historic-image buffer, and same
        in-progress-task evidence as if the run had never stopped (Andreas
        2026-06-18: "no difference from stopping and starting vs letting it run").

        The emulator state + TaskMaster task tree are restored elsewhere (the
        savepoint's emulator.state and task_master_state.json). What was LOST on
        continue — and is rebuilt here, for every turn <= ``up_to_turn`` (the
        savepoint turn) — is the Player's transient context:

        - ``self.turn_explanations`` — the action/reasoning/grade list behind the
          "## Previous Turns" block. Without it the resumed agent's first turn
          renders "(none — this is the first turn.)" and forgets all prior play.
        - ``self.turn_number`` — continued FROM ``up_to_turn`` (not reset to 0), so
          turn numbering, the "### Turn N" labels, and new screenshot/savepoint
          names stay globally monotonic across the continuation boundary.
        - ``self.turn_screenshots`` — the last ``historic_images_count`` start-of-
          turn screenshots (skipped when historic images are off).
        - ``self._cur_task_player_reasons`` / ``_cur_task_first_image`` /
          ``_cur_task_last_image`` — the in-progress task's evidence (reasonings +
          first/last screenshot refs since the current task began) so TaskMaster's
          NEXT rating judges the whole task, not just the post-resume turns.

        Pure read of the (already-copied) events.jsonl + screenshot files; no
        emulator interaction. Best-effort — a malformed/absent log degrades to the
        old fresh-start behaviour rather than failing the run. Screenshot paths
        are remapped to ``screenshots_dir`` (the new run's copies) so the resumed
        run is self-contained and doesn't depend on the source run surviving.
        """
        events_path = Path(events_path)
        screenshots_dir = Path(screenshots_dir)
        if not events_path.exists():
            return

        def _resolve(file_ref: str) -> str:
            """Map a logged screenshot path to the new run's copy by basename."""
            local = screenshots_dir / Path(file_ref).name
            return str(local) if local.exists() else file_ref

        explanations_by_turn: dict[int, dict] = {}
        screenshot_by_turn: dict[int, str] = {}
        task_start_turn: dict[int, int] = {}
        cur_turn: Optional[int] = None

        try:
            with open(events_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        evt = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    etype = evt.get("type")
                    if etype == "turn_start":
                        t = evt.get("turn")
                        cur_turn = t if isinstance(t, int) else None
                    elif etype == "screenshot":
                        # The first screenshot logged after a turn_start is that
                        # turn's start-of-turn capture (label "turn_<t>").
                        if (
                            isinstance(cur_turn, int)
                            and cur_turn <= up_to_turn
                            and cur_turn not in screenshot_by_turn
                        ):
                            file_ref = evt.get("file")
                            if file_ref:
                                screenshot_by_turn[cur_turn] = _resolve(file_ref)
                    elif etype == "turn_explanation":
                        t = evt.get("turn")
                        exp = evt.get("explanation")
                        if isinstance(t, int) and t <= up_to_turn and isinstance(exp, dict):
                            explanations_by_turn[t] = exp
                    elif etype == "task_started":
                        ti = evt.get("task_index")
                        gt = evt.get("global_turn")
                        if isinstance(ti, int) and isinstance(gt, int):
                            task_start_turn[ti] = gt
        except OSError:
            return

        if not explanations_by_turn and not screenshot_by_turn:
            return

        # turn_explanations is the loop's append-ordered list — one entry per
        # player-action turn (handoff turns produce none). Rebuild in turn order,
        # keeping the parallel real-turn-number list so headings + action lookups
        # stay on the true turn number across the handoff gaps.
        ordered_turns = sorted(explanations_by_turn)
        self.turn_explanations = [explanations_by_turn[t] for t in ordered_turns]
        self._explanation_turns = list(ordered_turns)

        # Continue the global turn counter from the savepoint turn (the loop adds
        # 1 before the first resumed turn → it picks up at up_to_turn + 1).
        self.turn_number = up_to_turn
        # The restored emulator state IS the savepoint turn's settled state, so a
        # kill before the first resumed turn completes must stamp back to here.
        self._last_settled_turn = up_to_turn

        # Rebuild the historic-image ring buffer: the last K start-of-turn
        # screenshots among turns <= up_to_turn, decoded back into PIL images.
        if self.historic_images_count > 0 and screenshot_by_turn:
            recent = sorted(screenshot_by_turn)[-self.historic_images_count:]
            buf: list[tuple[int, Image.Image]] = []
            for t in recent:
                try:
                    with Image.open(screenshot_by_turn[t]) as im:
                        buf.append((t, im.copy()))
                except (OSError, ValueError):
                    continue
            self.turn_screenshots = buf

        # Restore the in-progress task's evidence buffers so TaskMaster's next
        # invocation rates the whole task, not just the post-resume turns.
        if self.task_master_enabled and self.current_task_index:
            start = task_start_turn.get(self.current_task_index, 1)
            self._cur_task_player_reasons = [
                r
                for t in sorted(explanations_by_turn)
                if t >= start and (r := explanations_by_turn[t].get("reasoning"))
            ]
            task_shots = [t for t in sorted(screenshot_by_turn) if t >= start]
            if task_shots:
                self._cur_task_first_image = screenshot_by_turn[task_shots[0]]
                self._cur_task_last_image = screenshot_by_turn[task_shots[-1]]

        print(
            f"  Player history restored: {len(self.turn_explanations)} prior turns, "
            f"resuming at turn {self.turn_number + 1} "
            f"({len(self.turn_screenshots)} historic screenshot(s) buffered)"
        )

    def restore_run_accounting(self, summary: dict) -> None:
        """Seed cost / token / duration / TaskMaster counters from a source run's
        run_summary.json so a continued run's totals are CUMULATIVE, not reset
        (Andreas 2026-06-18: "the time elapsed and the cost to continue as well
        as the token counts").

        Seeds, from the source summary:
          - total_cost_usd  ← source Player+OCR cost (llm_usd + ocr_usd)
          - ocr.total_cost_usd ← source OCR cost (so the finalize-time llm/ocr
            split stays correct after seeding the combined total)
          - task_master_cost_usd ← source TaskMaster cost
          - total_input_tokens / total_output_tokens
          - task_master_turns (folds into total_turns)
          - _prior_duration_s (added to this session's wall clock)

        Best-effort: a missing/partial summary leaves the counters at zero (the
        old reset behaviour) rather than failing the resume.
        """
        cost = summary.get("cost") or {}
        session = summary.get("session") or {}
        llm_usd = float(cost.get("llm_usd", 0) or 0)
        ocr_usd = float(cost.get("ocr_usd", 0) or 0)
        self.total_cost_usd = llm_usd + ocr_usd
        if self.ocr is not None:
            self.ocr.total_cost_usd = (self.ocr.total_cost_usd or 0) + ocr_usd
        self.task_master_cost_usd = float(cost.get("task_master_usd", 0) or 0)
        self.total_input_tokens = int(cost.get("total_input_tokens", 0) or 0)
        self.total_output_tokens = int(cost.get("total_output_tokens", 0) or 0)
        self.task_master_turns = int(session.get("task_master_turns", 0) or 0)
        self._prior_duration_s = float(session.get("duration_seconds", 0) or 0)

    def _all_in_spend_usd(self) -> float:
        """Everything this run has paid for so far, in USD.

        The same figure the summary reports as ``cost.total_usd``: Player LLM +
        OCR (both accumulated in ``total_cost_usd``) plus TaskMaster, which is
        counted separately for the strategy-vs-tactics split (Decision 10). A
        budget that ignored the TaskMaster would be a lie on any 3.x config —
        strategy is a real share of the bill.
        """
        return float(self.total_cost_usd) + float(self.task_master_cost_usd)

    def _budget_exhausted(self) -> bool:
        """True once this SEGMENT has spent its ceiling.

        Segment-relative (see ``_spend_baseline_usd``) and inclusive: at exactly
        the cap the budget is gone, so no further turn starts. No ceiling set →
        always False, which is the unbounded default.
        """
        if self.max_spend_usd is None:
            return False
        return (self._all_in_spend_usd() - self._spend_baseline_usd) >= self.max_spend_usd

    def run_loop(self, max_turns: Optional[int] = None) -> None:
        """Run the turn loop synchronously."""
        try:
            asyncio.run(self._run_loop_async(max_turns))
        finally:
            # Tear down the player-turn worker thread (if a stop-armed run
            # started one), including after a KeyboardInterrupt abandon.
            self._shutdown_turn_worker()

    async def _run_loop_async(self, max_turns: Optional[int] = None) -> None:
        """Run the turn loop."""
        self._run_start_time = time.time()
        limit = max_turns or self.max_turns
        # Remember the cap the run actually ran under. It arrives as a call
        # argument, not a config key, so without this it is unrecoverable from
        # the run folder afterwards — and the summary is where the recording
        # filename and any later comparison read a run's bounds from.
        self._turn_limit = limit
        # Anchor the spend budget to what this segment adds, not to the lineage
        # total a continue inherited. Taken BEFORE the cold start so the opening
        # TaskMaster call is charged to this segment too.
        self._spend_baseline_usd = self._all_in_spend_usd()

        # Tee stdout to a terminal log file in the run folder
        self._terminal_log = open(self.logger.run_dir / "terminal.log", "w")
        self._orig_stdout = sys.stdout
        sys.stdout = _TeeWriter(self._orig_stdout, self._terminal_log)

        # Cold start: before turn 1, ask TaskMaster for the opening task. Emits
        # task_master_trace{1} → task_started{1} (no task_completed — nothing to
        # rate yet). Skipped when TaskMaster is disabled OR a continued run
        # already restored a current_task from task_master_state.json.
        if self.task_master_enabled and self.current_task is None:
            await self._cold_start()

        for _ in range(limit):
            # Cooperative stop: a UI/executor stop request halts at the next turn
            # boundary (a clean point — never mid-turn). Raising KeyboardInterrupt
            # reuses run_single_loop's interrupt path: graceful savepoint + the
            # run is finalised as `cancelled` (voided if official).
            if self._should_stop is not None and self._should_stop():
                print("\n  Stop requested — ending run at turn boundary.")
                raise KeyboardInterrupt

            # Spend ceiling. Checked at the turn boundary — a turn's cost is only
            # known once it has been paid, so this is a "start no turn you cannot
            # afford to have started" bound: the final total can exceed the cap by
            # up to one turn. Break (not KeyboardInterrupt): hitting the budget is
            # the run finishing as instructed, exactly like the turn cap, so it
            # finalises `completed` rather than `cancelled`.
            if self._budget_exhausted():
                spent = self._all_in_spend_usd() - self._spend_baseline_usd
                self._budget_stopped = True
                print(
                    f"\n  Spend budget reached — ${spent:.4f} of "
                    f"${self.max_spend_usd:.4f}. Ending run at turn boundary."
                )
                self.logger.log_custom("budget_exhausted", {
                    "turn": self.turn_number,
                    "spent_usd": round(spent, 6),
                    "max_spend_usd": self.max_spend_usd,
                })
                break

            self.turn_number += 1
            print(f"\n{'─'*60}")
            print(f"  Turn {self.turn_number}")
            print(f"{'─'*60}")

            result = await self._run_turn_or_stop()
            if result is None:
                # At the per-task budget boundary the handoff to TaskMaster is an
                # invariant. If the Player produced no valid output there (e.g. a
                # prompted model that won't emit the optional return_to_taskmaster
                # discriminator even after retries), force the handoff in code
                # rather than ending the whole run.
                if (
                    self.task_master_enabled
                    and self.max_turns > 0
                    and self.current_task_turn >= self.max_turns
                ):
                    print(f"  [Turn {self.turn_number}] No valid Player output at the "
                          f"budget boundary; forcing handoff to TaskMaster.")
                    await self._handle_handoff(None, None)
                    if self._referee_should_break():
                        break
                    continue
                print(f"  [Turn {self.turn_number}] No result. Stopping.")
                self._aborted_no_output = True
                break

            # TaskMaster handoff: when the Player hands control back
            # (return_to_taskmaster set — the budget validator forces this at the
            # boundary) OR the per-task budget is exhausted, rate the finished
            # task and set the next one. The Player's `inputs` are ignored on a
            # handoff turn (it handed back), so we skip button execution + memory.
            if self.task_master_enabled:
                handoff = getattr(result, "return_to_taskmaster", None)
                budget_hit = (
                    self.max_turns > 0 and self.current_task_turn >= self.max_turns
                )
                if handoff is not None or budget_hit:
                    # Feed the finishing turn's reasoning to TaskMaster as evidence
                    # before the handoff (this turn is otherwise not added to the
                    # per-task reason buffer, since we `continue` past it).
                    if getattr(result, "reasoning", None):
                        self._cur_task_player_reasons.append(result.reasoning)
                    await self._handle_handoff(result, handoff)
                    if self._referee_should_break():
                        break
                    continue

            # Apply memory updates from the agent's output (string → dict)
            updates = {}
            if result.memory_updates and result.memory_updates.strip().lower() != "none":
                try:
                    parsed = json.loads(result.memory_updates)
                    if isinstance(parsed, dict):
                        updates = parsed
                except (json.JSONDecodeError, TypeError):
                    pass
            if updates:
                for key, value in updates.items():
                    if value is None or value == "":
                        self.state.delete_by_path(key)
                    else:
                        self.state.set_by_path(key, value)
                self.state.save()
                print(f"  [Turn {self.turn_number}] Memory: {json.dumps(updates)}")
                self.logger.log_state_change("memory_update", {
                    "updates": updates,
                })

            # Log the turn explanation
            explanation = {
                "action": result.inputs,
                "reasoning": result.reasoning,
                "last_turn_succeeded": result.last_turn_succeeded,
                "memory_updates": updates,
                "memory_updates_raw": result.memory_updates,
            }
            self.turn_explanations.append(explanation)
            self._explanation_turns.append(self.turn_number)
            self.logger.log_turn_explanation(self.turn_number, explanation)
            # Accumulate the Player's reasoning for the CURRENT task — fed to the
            # next TaskMaster invocation so it can judge the task from the trace.
            if self.task_master_enabled and result.reasoning:
                self._cur_task_player_reasons.append(result.reasoning)

            # Execute button presses and wait for screen to settle.
            # OCR captures are gated to this window only — no captures during
            # LLM thinking or during the next turn's vision call.
            try:
                action_display = "[" + ", ".join(result.inputs) + "]"
                print(f"  [Turn {self.turn_number}] Executing {action_display}...")
                if self.ocr and self.ocr.enabled:
                    self.ocr.set_active(True)
                self.emulator.press_button_list(result.inputs)
                self.logger.log_button_sequence(str(result.inputs))
                print(f"  [Turn {self.turn_number}] Waiting for screen to settle...")
                self.logger.log_custom("screen_settling", {"turn": self.turn_number})
                settle_duration = self.emulator.wait_for_stable_screen()
                self.logger.log_custom("screen_settled", {"turn": self.turn_number, "duration": round(settle_duration, 1)})
                print(f"  [Turn {self.turn_number}] Screen settled ({settle_duration:.1f}s)")
            except Exception as e:
                print(f"  [Turn {self.turn_number}] Execution error: {e}")
                self.logger.log_custom("action_error", {"error": str(e)})
                # Reset facing — we don't know where the player ended up
                self.emulator.facing = None
            finally:
                if self.ocr and self.ocr.enabled:
                    self.ocr.set_active(False)

            # Referee poll — runs after the screen has settled so memory
            # reflects the just-executed action. Polled on EVERY turn (including
            # the handoff turns above, via their own poll before `continue`) so a
            # deadline gate is evaluated at the exact turn it falls due, not a
            # turn late.
            if self._referee_should_break():
                break

            # This turn's action has been pressed AND the screen has settled, so
            # the live emulator state is now byte-exact for this turn. Mark it so
            # a later mid-turn kill/crash savepoint stamps THIS turn (the clean
            # boundary), never the in-flight one.
            self._last_settled_turn = self.turn_number

            # Periodic savepoint — at the very end of the iteration so the
            # emulator state is post-settle, not mid-button-press.
            if (
                self.savepoint_every_n_turns > 0
                and self.turn_number % self.savepoint_every_n_turns == 0
            ):
                self.save_savepoint("periodic")

        if self.savepoint_at_end:
            self.save_savepoint("end")

        ocr_cost = self.ocr.total_cost_usd if self.ocr else 0.0
        llm_cost = self.total_cost_usd - ocr_cost
        print(f"\n{'═'*60}")
        print(f"  Run complete: {self.turn_number} turns")
        print(
            f"  Cost: ${self.total_cost_usd:.4f} "
            f"(LLM: ${llm_cost:.4f}, OCR: ${ocr_cost:.5f})"
        )
        print(f"  Tokens: {self.total_input_tokens} in / {self.total_output_tokens} out")
        print(f"{'═'*60}")

        # Exit status. A referee success-exit (final ladder rung reached) is a
        # WIN → `completed`. A loop that gave up because the model never
        # produced a valid output is NOT a result → `crashed`, so it can't post
        # a leaderboard row (see `_aborted_no_output`). Every other clean exit
        # leaves status None so the writer/executor infers it (referee
        # missed-gate → `terminated`, casual max-turns → `completed`). Crash and
        # stop exits finalise via run_single_loop instead.
        if self._referee_completed:
            exit_status = "completed"
        elif self._aborted_no_output:
            exit_status = "crashed"
        else:
            exit_status = None
        self.finalize_run_summary(status=exit_status)

    def _referee_should_break(self) -> bool:
        """Poll the referee at the current turn and act on its verdict.

        Returns True when the run loop should break — a missed-gate termination
        (enforcement), the requested ``stop_at`` event, or the final-rung WIN
        (locked decision #8).
        Wrapped so a referee fault (e.g. a flaky memory read) can NEVER take
        down a player run. Safe to call on every turn, including TaskMaster
        handoff turns: that's what evaluates a deadline gate at the exact turn
        it falls due instead of one turn late (the off-by-one fix, 2026-06-17).
        Idempotent — poll() only first-stamps and the missed-gate latch is
        one-shot, so calling it on a handoff turn that pressed no buttons just
        re-runs the deadline check against the unchanged game state.
        """
        if self.referee is None:
            return False
        # Missed-gate enforcement. Gate deadlines are measured against TOTAL
        # turns — player/game turns PLUS TaskMaster invocations (Andreas
        # 2026-06-17) — so a run that misses a gate stops the instant the
        # combined count reaches that gate's cutoff. This keeps the budget
        # honest (the strategy layer's turns count too) AND makes the
        # leaderboard's total_turns land exactly on a gate deadline whenever a
        # run is terminated short of 100%. The summary's total_turns uses the
        # same player+master sum, so the two always agree.
        total_turns = self.turn_number + self.task_master_turns
        try:
            if bool(self.referee.poll(total_turns)):
                reason = self.referee.termination_reason
                print(f"  [Turn {self.turn_number}] Referee gate enforcement: "
                      f"{reason}. Stopping run cleanly.")
                self.logger.log_custom("referee_terminate", {
                    "turn": self.turn_number,
                    "total_turns": total_turns,
                    "reason": reason,
                })
                return True
        except Exception as e:
            self.logger.log_custom("referee_error", {
                "turn": self.turn_number, "error": str(e),
            })
            return False
        # Requested early finish line (casual `--stop-at`). Checked BEFORE the
        # final-rung win so a run told to stop at the last gate reports the
        # reason the user actually asked for. Counts as a clean completion: the
        # run was asked to reach an event and it reached it.
        try:
            if self.referee.should_stop_at():
                self._referee_completed = True
                reason = self.referee.stop_at_reason
                print(f"  [Turn {self.turn_number}] Referee: requested stop "
                      f"event reached ({reason}) — run complete.")
                self.logger.log_custom("referee_stop_at", {
                    "turn": self.turn_number,
                    "total_turns": total_turns,
                    "reason": reason,
                })
                return True
        except Exception as e:
            self.logger.log_custom("referee_error", {
                "turn": self.turn_number, "error": str(e),
            })
        # Success exit (locked decision #8): final ladder rung complete → WIN.
        try:
            if self.referee.should_complete_run():
                self._referee_completed = True
                reason = getattr(self.referee, "completion_reason", "final_gate")
                print(f"  [Turn {self.turn_number}] Referee: final ladder "
                      f"gate reached ({reason}) — run complete (WIN).")
                self.logger.log_custom("referee_complete", {
                    "turn": self.turn_number, "reason": reason,
                })
                return True
        except Exception as e:
            self.logger.log_custom("referee_error", {
                "turn": self.turn_number, "error": str(e),
            })
        return False

    def _ensure_turn_worker(self) -> asyncio.AbstractEventLoop:
        """Lazily start the player-turn worker loop on a daemon thread."""
        if self._turn_worker_loop is None:
            self._turn_worker_loop = asyncio.new_event_loop()
            self._turn_worker_thread = threading.Thread(
                target=self._turn_worker_loop.run_forever,
                name="pokemon-turn-worker",
                daemon=True,
            )
            self._turn_worker_thread.start()
        return self._turn_worker_loop

    def _shutdown_turn_worker(self) -> None:
        """Stop the worker loop + join its thread. Best-effort, idempotent."""
        loop = self._turn_worker_loop
        self._turn_worker_loop = None
        thread = self._turn_worker_thread
        self._turn_worker_thread = None
        if loop is not None:
            try:
                loop.call_soon_threadsafe(loop.stop)
            except Exception:
                pass
        if thread is not None:
            # Daemon thread, so even a turn wedged in a blocking call (which we
            # deliberately abandoned) can't keep the process alive past exit.
            thread.join(timeout=2.0)

    async def _run_turn_or_stop(self) -> Optional[GameAction]:
        """Run one turn, but abort it the instant a stop is requested mid-turn.

        A single turn can sit inside one LLM call for MINUTES (slow thinking
        models use 240s per-attempt timeouts). Polling the stop flag only at the
        loop boundary means a UI "kill" has to wait for that whole call — a dead
        button. An earlier same-loop race fixed this ONLY when the call yielded;
        if the LLM call ever blocks its event loop, a same-loop watcher is
        starved and the kill still hangs.

        So the turn runs on a dedicated WORKER loop while THIS loop stays free to
        poll the stop flag every 0.1s. On stop we cancel the turn (an async LLM
        call unwinds near-instantly), wait briefly for it to settle, then ABANDON
        it and raise KeyboardInterrupt — reusing the existing interrupt →
        savepoint → cancelled path. The main loop is never starved, so the kill
        is instant regardless of what the turn is doing.

        Safe because the player agent has NO tools: a turn only READS the
        emulator (the start-of-turn screenshot) and never presses a button —
        buttons are pressed by the loop body AFTER this returns. So an abandoned
        turn leaves the game byte-exact at ``_last_settled_turn``; resume
        re-runs it from that clean boundary, with no double-counted turn and no
        leaked context (memory is applied only on a completed turn).
        """
        if self._should_stop is None:
            return await self._run_turn()
        worker = self._ensure_turn_worker()
        fut = asyncio.run_coroutine_threadsafe(self._run_turn(), worker)
        while True:
            if fut.done():
                return fut.result()
            if self._should_stop():
                fut.cancel()
                # Let an async turn unwind its LLM call cleanly so the emulator
                # is quiescent before the crash savepoint; abandon a turn wedged
                # in a blocking call (the run is being killed regardless).
                try:
                    await asyncio.wait_for(
                        asyncio.wrap_future(fut), timeout=self._stop_unwind_timeout_s
                    )
                except BaseException:
                    pass
                print("\n  Stop requested — aborting the in-flight turn now.")
                raise KeyboardInterrupt
            await asyncio.sleep(0.1)

    async def _run_turn(self) -> Optional[GameAction]:
        """Execute a single turn."""
        turn_start = time.time()
        t = self.turn_number

        # Per-task turn counter feeds the budget validator. The run loop resets
        # self.current_task_turn to 0 on each TaskMaster handoff; here we advance
        # it one per turn. Only meaningful when TaskMaster is enabled — left
        # untouched/ignored on the legacy path.
        if self.task_master_enabled:
            self.current_task_turn += 1

        # turn_start carries the current task_index (the only hard new field on
        # player turns under the TaskMaster contract) so the frontend can bucket
        # the turn into its task group. None on the legacy path → omitted.
        self.logger.log_turn_start(
            t, task_index=self.current_task_index if self.task_master_enabled else None
        )

        # 1. Capture screenshot
        print(f"  [Turn {t}] Capturing screenshot...")
        screenshot = self.emulator.capture_screenshot(preprocess=True)
        screenshot_ref = self.logger.log_screenshot(screenshot, label=f"turn_{t}")
        # Track first/last screenshot refs for the CURRENT task so the next
        # TaskMaster invocation can rate it from start/end evidence (Decision 8 —
        # stored by ref, resolved at TM-invocation time). Only the run loop's
        # handoff reads these; harmless on the legacy path.
        if self.task_master_enabled:
            if self._cur_task_first_image is None:
                self._cur_task_first_image = screenshot_ref
            self._cur_task_last_image = screenshot_ref

        # 2. Encode the screenshot for the LLM (direct multimodal)
        print(f"  [Turn {t}] Encoding screen...")
        analysis = self.vision.analyze_screenshot(screenshot)
        vision_content = self.vision.format_for_llm(analysis)

        # 3. Flush OCR buffer (background captures since last turn) + LLM cleanup
        ocr_text = ""
        ocr_raw: dict = {}
        if self.ocr and self.ocr.enabled:
            t_ocr = time.time()
            ocr_text, ocr_raw, ocr_usage, ocr_stats = self.ocr.flush_and_cleanup()
            cleanup_elapsed = time.time() - t_ocr
            ocr_cost = ocr_usage.get("cost_usd", 0.0)
            self.total_cost_usd += ocr_cost
            self.logger.log_custom("ocr_flush", {
                "turn": t,
                "raw": ocr_raw,
                "cleaned": ocr_text,
                "n_captures": len(ocr_raw),
                "duration": round(cleanup_elapsed, 2),  # cleanup call time (kept for back-compat)
                "cleanup_s": round(cleanup_elapsed, 2),
                "cost_usd": ocr_cost,
                "input_tokens": ocr_usage.get("input_tokens", 0),
                "output_tokens": ocr_usage.get("output_tokens", 0),
                "model": self.ocr.cleanup_model,
                "window_s": ocr_stats["window_s"],
                "attempts": ocr_stats["attempts"],
                "tesseract_runs": ocr_stats["tesseract_runs"],
                "hash_dupes": ocr_stats["hash_dupes"],
                "text_dupes": ocr_stats["text_dupes"],
                "empty_ocr": ocr_stats["empty_ocr"],
                "buffer_full": ocr_stats["buffer_full"],
            })
            if ocr_stats["attempts"] > 0 or ocr_raw:
                print(
                    f"  [Turn {t}] OCR: window={ocr_stats['window_s']:.1f}s "
                    f"| {ocr_stats['attempts']} polls → "
                    f"{ocr_stats['tesseract_runs']} tesseract "
                    f"({ocr_stats['hash_dupes']} hash-dup) → "
                    f"{len(ocr_raw)} kept "
                    f"({ocr_stats['text_dupes']} text-dup, {ocr_stats['empty_ocr']} empty"
                    + (f", {ocr_stats['buffer_full']} overflow" if ocr_stats['buffer_full'] else "")
                    + ")"
                )
                print(
                    f"  [Turn {t}] OCR cleanup: {cleanup_elapsed:.1f}s | ${ocr_cost:.5f} "
                    f"| {ocr_usage.get('input_tokens', 0)}→{ocr_usage.get('output_tokens', 0)} tokens"
                )

        # 4. Get current memory dictionary
        state_view = self.state.get_truncated_view()

        # 5. Build the user message
        # At this point self.turn_screenshots contains the last K *prior* turns'
        # screenshots — current turn t is intentionally NOT in the buffer yet,
        # since it's already passed in separately as the "current screen".
        user_message = self._build_turn_message(
            vision_content, ocr_text, state_view, screenshot,
        )

        # Now that the message is built, stash this turn's screenshot for the
        # NEXT turn's historic-image block. Capped at K so memory stays flat.
        if self.historic_images_count > 0:
            self.turn_screenshots.append((t, screenshot))
            if len(self.turn_screenshots) > self.historic_images_count:
                self.turn_screenshots = self.turn_screenshots[-self.historic_images_count:]

        # Log the text portion of the user message
        if isinstance(user_message, str):
            log_msg = user_message[:5000]
        else:
            # Multimodal list — extract text parts for logging
            log_msg = " ".join(str(p) for p in user_message if isinstance(p, str))[:5000]
        self.logger.log_custom("turn_user_message", {
            "turn": t,
            "message": log_msg,
        })

        # 6. Build deps. When TaskMaster is enabled, also pass the per-task turn
        # count + budget so the output validator can force a handoff at the
        # boundary. Left at their AgentDeps defaults (0/0 → validator no-op) on
        # the legacy path.
        deps = AgentDeps(
            emulator=self.emulator,
            state=self.state,
            logger=self.logger,
            ocr=self.ocr,
            turn_number=t,
            current_task_turn=self.current_task_turn if self.task_master_enabled else 0,
            max_turns_per_task=self.max_turns if self.task_master_enabled else 0,
        )

        # 7. Run the agent
        print(f"  [Turn {t}] Running LLM...")
        messages = []
        try:
            result, model_used = await self._run_agent_with_fallback(
                user_message, deps, messages
            )

            # 8. Log trace + cost
            trace = _serialize_messages(messages)
            turn_cost = _extract_cost_from_messages(messages)
            self.total_cost_usd += turn_cost

            # Print trace summary to terminal
            _print_trace_summary(t, trace)

            self.logger.log_custom("turn_trace", {
                "turn": t,
                "messages": trace,
                "model_used": model_used,
            })

            # Log usage
            tokens_str = ""
            provider = _extract_provider_from_messages(messages)
            if result.usage():
                usage = result.usage()
                self.total_input_tokens += usage.request_tokens or 0
                self.total_output_tokens += usage.response_tokens or 0
                event = _usage_event(t, usage, turn_cost, provider)
                reasoning_tokens = event["reasoning_tokens"]
                tokens_str = (
                    f" | {usage.request_tokens}→{usage.response_tokens} tokens"
                )
                if reasoning_tokens is not None:
                    tokens_str += f" ({reasoning_tokens} reasoning)"
                self.logger.log_custom("turn_usage", event)

            duration = round(time.time() - turn_start, 1)
            prov_str = f" | provider={provider}" if provider else ""
            print(f"  [Turn {t}] Done ({duration}s | ${turn_cost:.4f}{tokens_str}{prov_str})")

            self.turn_costs.append({
                "turn": t,
                "cost_usd": turn_cost,
                "model": model_used,
                "duration_s": duration,
            })

            return result.output

        except Exception as e:
            # Always log the trace even on failure
            if messages:
                trace = _serialize_messages(messages)
                turn_cost = _extract_cost_from_messages(messages)
                self.total_cost_usd += turn_cost
                _print_trace_summary(t, trace)
                self.logger.log_custom("turn_trace", {
                    "turn": t,
                    "messages": trace,
                    "error": str(e),
                })

            provider = _extract_provider_from_messages(messages)
            prov_str = f" | provider={provider}" if provider else ""
            print(f"  [Turn {t}] ERROR{prov_str}: {e}")
            self.logger.log_custom("agent_error", {
                "error": str(e),
                "turn": t,
                "provider": provider,
            })
            # Remembered for the run summary. Returning None ends the run (see
            # the `No result. Stopping.` break), and "why" is otherwise only in
            # events.jsonl — not in the one file the control plane reads.
            self._abort_error = str(e)
            return None

    async def _run_agent_iter(self, user_message, deps, model, usage_limits, model_settings=None):
        """Run a single agent iteration with streaming. Returns (result, captured_messages)."""
        from pydantic_ai import capture_run_messages
        from pydantic_ai._agent_graph import CallToolsNode, ModelRequestNode

        with capture_run_messages() as captured:
            kwargs = {"model_settings": model_settings} if model_settings else {}
            async with self.agent.iter(
                user_message, deps=deps, model=model,
                usage_limits=usage_limits, **kwargs,
            ) as agent_run:
                async for node in agent_run:
                    if isinstance(node, CallToolsNode):
                        self._emit_node_events(node, deps)
                    elif isinstance(node, ModelRequestNode):
                        self._emit_retry_events(node, deps)
            return agent_run.result, list(captured)

    async def _run_agent_with_fallback(
        self,
        user_message,
        deps: AgentDeps,
        out_messages: list,
    ) -> tuple:
        """Run the agent, trying fallback models if the primary fails.

        For each model in the chain, retries up to len(_LLM_CALL_TIMEOUTS_S)
        times on transient errors (per-attempt timeout exceeded OR known
        wrapped-5xx pattern). Non-transient errors skip the remaining
        attempts for that model and fall through to the next model.

        Populates out_messages with the captured message history.
        Returns (result, model_id_used).
        """
        from pydantic_ai.usage import UsageLimits

        usage_limits = UsageLimits(request_limit=self.max_steps_per_turn)

        primary_model_id = self.config.get("llm_model", "")
        model_chain = [primary_model_id] + list(self.fallback_models)

        last_error: BaseException = RuntimeError("no model attempts made")
        max_attempts = len(_LLM_CALL_TIMEOUTS_S)
        t = deps.turn_number

        # Per-model retry tuning, read off the resolved registry entry: `slow`
        # doubles every per-attempt timeout; `provider` is the base routing block
        # the per-attempt throughput→latency→default escalation layers onto.
        resolved = self.config.get("_llm_resolved") or {}
        is_slow = bool(resolved.get("slow"))
        base_provider = resolved.get("provider")
        slow_mult = _SLOW_MODEL_TIMEOUT_MULT if is_slow else 1.0

        for model_id in model_chain:
            for attempt_idx, base_timeout in enumerate(_LLM_CALL_TIMEOUTS_S):
                attempt_num = attempt_idx + 1
                timeout_s = base_timeout * slow_mult
                provider_routing = _provider_routing_for_attempt(
                    attempt_idx, base_provider
                )
                attempt_settings = _settings_for_attempt(
                    self.model_settings, provider_routing
                )
                sort_label = (provider_routing or {}).get("sort", "default")
                model = OpenAIModel(model_id, provider="openrouter")
                heartbeat = asyncio.ensure_future(_emit_heartbeat(
                    f"[Turn {t}] LLM in flight (attempt {attempt_num}/{max_attempts}, "
                    f"{model_id}, sort={sort_label}, timeout={timeout_s:.0f}s)"
                ))
                try:
                    result, captured = await asyncio.wait_for(
                        self._run_agent_iter(
                            user_message, deps, model, usage_limits, attempt_settings
                        ),
                        timeout=timeout_s,
                    )
                    heartbeat.cancel()
                    out_messages.extend(captured)
                    return result, model_id

                except (asyncio.TimeoutError, Exception) as exc:
                    heartbeat.cancel()
                    last_error = exc
                    is_transient = _is_transient_llm_error(exc)
                    is_retryable = is_transient or _is_agent_invoke_retryable(exc)
                    err_label = (
                        f"timeout after {timeout_s:.0f}s"
                        if isinstance(exc, asyncio.TimeoutError)
                        else f"{type(exc).__name__}: {exc}"
                    )
                    slow_note = " slow×2" if is_slow else ""
                    print(
                        f"  [Turn {t}] LLM attempt {attempt_num}/{max_attempts} "
                        f"({model_id}, sort={sort_label}, timeout={timeout_s:.0f}s"
                        f"{slow_note}) failed: {err_label}"
                    )

                    try:
                        self.logger.log_custom("agent_retry", {
                            "turn": t,
                            "model": model_id,
                            "attempt": attempt_num,
                            "max_attempts": max_attempts,
                            "timeout_s": timeout_s,
                            "provider_sort": sort_label,
                            "slow_model": is_slow,
                            "error_type": type(exc).__name__,
                            "error": str(exc)[:300],
                            "retryable": is_retryable,
                        })
                    except Exception:
                        pass

                    if is_retryable and attempt_num < max_attempts:
                        # Backoff (jittered) before re-rolling: the next attempt
                        # also escalates provider routing (throughput→latency→default).
                        backoff = _retry_backoff_s(attempt_idx)
                        if backoff > 0:
                            await asyncio.sleep(backoff)
                        continue

                    # Non-transient OR out of attempts: try the thinking-strip
                    # workaround once, then move to the next model in the chain.
                    if self.model_settings and _should_retry_without_thinking(exc):
                        try:
                            logger.info(f"Retrying {model_id} without thinking params")
                            result, captured = await asyncio.wait_for(
                                self._run_agent_iter(
                                    user_message, deps, model, usage_limits
                                ),
                                timeout=_LLM_CALL_TIMEOUTS_S[-1],
                            )
                            out_messages.extend(captured)
                            return result, model_id
                        except Exception as exc2:
                            last_error = exc2
                            logger.warning(f"Model {model_id} failed without thinking: {exc2}")
                    break  # move to next model in chain

        # Bare TimeoutError() stringifies to "" — substitute a readable message
        # so the outer error log doesn't print "[Turn N] ERROR: " with nothing.
        if isinstance(last_error, asyncio.TimeoutError) and not str(last_error):
            raise TimeoutError(
                f"all {max_attempts} attempts timed out "
                f"across {len(model_chain)} model(s) "
                f"(timeouts: {[int(s) for s in _LLM_CALL_TIMEOUTS_S]}s)"
            ) from last_error
        raise last_error

    def _emit_retry_events(self, node, deps: AgentDeps) -> None:
        """Emit log events for retry prompts (validation failures)."""
        request = node.request
        if not request or not hasattr(request, 'parts'):
            return

        for part in request.parts:
            if isinstance(part, RetryPromptPart):
                content = str(part.content) if part.content else "Validation failed"
                self.logger.log_custom("output_retry", {
                    "content": content,
                    "turn": deps.turn_number,
                    "agent_id": deps.agent_id,
                })

    def _emit_node_events(self, node, deps: AgentDeps) -> None:
        """Emit log events for a CallToolsNode's content (thinking, tool calls, output)."""
        response = node.model_response
        if not response or not hasattr(response, 'parts'):
            return

        # Some providers (e.g. Moonshot Kimi) return their chain-of-thought in
        # OpenRouter's top-level `reasoning` field rather than as a structured
        # ThinkingPart, so it lands in provider_details — NOT in response.parts.
        # The post-hoc trace serializer surfaces it (_extract_openrouter_reasoning),
        # but the LIVE spectate HUD is built solely from these node events, so
        # without this fallback the Player's thinking silently vanishes for those
        # models while the TaskMaster (serialized path) still shows it. Emit it
        # FIRST so thinking precedes the output in the live stream, and skip when
        # a ThinkingPart already carried it to avoid double-logging.
        has_thinking_part = any(
            isinstance(p, ThinkingPart) and p.content for p in response.parts
        )
        if not has_thinking_part:
            try:
                or_reasoning = (response.provider_details or {}).get("reasoning")
            except Exception:
                or_reasoning = None
            if or_reasoning:
                self.logger.log_custom("llm_thinking", {
                    "content": or_reasoning,
                    "turn": deps.turn_number,
                    "agent_id": deps.agent_id,
                })

        for part in response.parts:
            if isinstance(part, ThinkingPart) and part.content:
                self.logger.log_custom("llm_thinking", {
                    "content": part.content,
                    "turn": deps.turn_number,
                    "agent_id": deps.agent_id,
                })
            elif isinstance(part, TextPart) and part.content:
                # In prompted-output mode the GameAction JSON arrives as a
                # TextPart. Emit the same llm_output + memory_update_output
                # events tool-mode emits, so the dashboard renders identical
                # labeled fields. Fall back to llm_text for genuine prose.
                parsed_action = _try_parse_game_action(part.content)
                if parsed_action is not None:
                    self.logger.log_custom("llm_output", {
                        "args": json.dumps(parsed_action),
                        "turn": deps.turn_number,
                        "agent_id": deps.agent_id,
                    })
                    mem_raw = parsed_action.get("memory_updates", "")
                    self.logger.log_custom("memory_update_output", {
                        "content": mem_raw if mem_raw else "(no changes)",
                        "turn": deps.turn_number,
                        "agent_id": deps.agent_id,
                    })
                else:
                    self.logger.log_custom("llm_text", {
                        "content": part.content,
                        "turn": deps.turn_number,
                        "agent_id": deps.agent_id,
                    })
            elif isinstance(part, ToolCallPart):
                tool_name = part.tool_name
                args = part.args
                if tool_name == 'final_result':
                    self.logger.log_custom("llm_output", {
                        "args": args if isinstance(args, str) else json.dumps(args),
                        "turn": deps.turn_number,
                        "agent_id": deps.agent_id,
                    })
                    # Emit memory update as a separate event for the dashboard
                    parsed_args = args if isinstance(args, dict) else {}
                    if isinstance(args, str):
                        try:
                            parsed_args = json.loads(args)
                        except (json.JSONDecodeError, TypeError):
                            parsed_args = {}
                    mem_raw = parsed_args.get("memory_updates", "")
                    self.logger.log_custom("memory_update_output", {
                        "content": mem_raw if mem_raw else "(no changes)",
                        "turn": deps.turn_number,
                        "agent_id": deps.agent_id,
                    })

    def _build_turn_message(
        self,
        vision_content: list[dict],
        ocr_text: str,
        state_view: dict,
        current_screenshot: Image.Image,
    ):
        """Build the user message for a turn.

        Layout follows OpenRouter's "text first, then images" recommendation:
        all explanatory text is concatenated into one block, then any images
        are appended at the end with explicit per-image labels immediately
        preceding each one. The current-turn screenshot is ALWAYS last so it
        sits closest to the model's decision point.

        Returns a list[UserContent] — always at least
        [text, label, current_image]. With historic_images_count = K and
        enough prior turns, becomes
        [text, hist_label_1, hist_img_1, ..., hist_label_K, hist_img_K,
         current_label, current_image]. (Falls back to a plain text string
        only in the defensive case where no screenshot could be encoded.)
        """
        current_image_url: Optional[str] = None
        screen_text_extra: list[str] = []

        # Vision content. In direct_multimodal mode this yields a "[Game Screen]"
        # text marker plus a data URL — strip the marker (the explicit labels in
        # the image tail carry the role) and keep the URL. Any other text blocks
        # are folded into the screen heads-up value.
        for block in vision_content:
            if block["type"] == "text":
                if block["text"].strip() != "[Game Screen]":
                    screen_text_extra.append(block["text"])
            elif block["type"] == "image_url":
                current_image_url = block["image_url"]["url"]

        # Compute the template VALUES, then fill the user-prompt template. The
        # template (config `user_prompt` or DEFAULT_PLAYER_USER_PROMPT) owns the
        # layout + wording; the {{placeholders}} carry the dynamic values (the
        # per-turn loop + screen heads-up are pre-rendered into theirs).
        screen_heads_up = self._render_screen_heads_up(current_image_url)
        if screen_text_extra:
            extra = "\n".join(screen_text_extra)
            screen_heads_up = (
                extra + ("\n" + screen_heads_up if screen_heads_up else "")
            ).strip()

        handoff_instruction = (
            " If the current task is complete, impossible, or you're out of "
            "useful moves, set `return_to_taskmaster` instead to hand control "
            "back to TaskMaster."
            if self.task_master_enabled
            else ""
        )

        template = self.config.get("user_prompt") or DEFAULT_PLAYER_USER_PROMPT
        combined_text = fill_prompt(
            template,
            screen_heads_up=screen_heads_up,
            ocr_text=ocr_text if ocr_text else "(none)",
            memory_json=json.dumps(state_view, indent=2),
            previous_turns=self._render_previous_turns_text(),
            task_block=self._render_current_task_text(),
            handoff_instruction=handoff_instruction,
        ).strip()

        # Defensive: if no screenshot could be encoded, return text only.
        if current_image_url is None:
            return combined_text

        # Direct multimodal: text first, then image tail.
        # Historic images go oldest → newest, then the current screenshot last.
        parts: list[UserContent] = [combined_text]
        n_total_images = len(self.turn_screenshots) + 1
        idx = 0
        for hist_turn_num, hist_image in self.turn_screenshots:
            idx += 1
            actions_str = self._lookup_actions(hist_turn_num)
            parts.append(
                f"=== SCREENSHOT {idx} of {n_total_images} — Turn {hist_turn_num} "
                f"(BEFORE actions). This is what the agent saw at the START of "
                f"turn {hist_turn_num}, before pressing [{actions_str}]. ==="
            )
            parts.append(ImageUrl(url=self.vision.image_to_data_url(hist_image)))
        idx += 1
        parts.append(
            f"=== SCREENSHOT {idx} of {n_total_images} — CURRENT (Turn {self.turn_number}). "
            f"This is what you see NOW. Decide your next action based on THIS screen. ==="
        )
        parts.append(ImageUrl(url=current_image_url))
        return parts

    def _render_screen_heads_up(self, current_image_url: Optional[str]) -> str:
        """Value for the {{screen_heads_up}} placeholder: the ## Screen(s) note
        telling the model what the image tail contains. Empty when there is no
        current image."""
        if current_image_url is None:
            return ""
        n_historic = len(self.turn_screenshots) if self.historic_images_count > 0 else 0
        n_total_images = n_historic + 1
        if n_historic > 0:
            return (
                "## Screens\n"
                f"This message ends with {n_total_images} screenshots in chronological order. "
                f"The first {n_historic} are historic — the screen the agent saw at the START "
                f"of each of the last {n_historic} turn(s), BEFORE pressing the actions listed "
                "under '## Previous Turns'. The LAST screenshot is the CURRENT turn — that "
                "is what you must reason about and act on now. Each image is preceded by an "
                "explicit label."
            )
        return (
            "## Screen\n"
            "The current-turn screenshot is shown at the end of this message — that is "
            "what you must reason about and act on."
        )

    def _render_previous_turns_text(self) -> str:
        """Value for the {{previous_turns}} placeholder: the rendered per-turn
        history (the loop body stays in code), with the truncation notice. Returns
        a '(none)' note on the first turn."""
        historic_turn_nums = {
            turn_num for (turn_num, _) in self.turn_screenshots
        } if self.historic_images_count > 0 else set()
        if not self.turn_explanations:
            return "(none — this is the first turn.)"
        out: list[str] = []
        n_total = len(self.turn_explanations)
        trim = self.max_turns_before_trim
        if trim is not None and n_total > trim:
            start = n_total - trim
            out.append(
                f"_(Earlier turns have been truncated. Showing the last {trim} of {n_total} turns.)_"
            )
        else:
            start = 0
        visible = self.turn_explanations[start:]
        n_visible = len(visible)
        # Real turn numbers, aligned with `visible`. Falls back to positional
        # numbering only if the parallel list is somehow out of sync (old data).
        if len(self._explanation_turns) == len(self.turn_explanations):
            visible_turns = self._explanation_turns[start:]
        else:
            visible_turns = [start + j + 1 for j in range(n_visible)]
        for j, exp in enumerate(visible):
            turn_num = visible_turns[j]
            action = exp.get('action', [])
            action_str = ", ".join(action) if isinstance(action, list) else str(action)
            reasoning = exp.get('reasoning', '')
            next_idx = j + 1
            if next_idx < n_visible:
                grade = visible[next_idx].get('last_turn_succeeded')
                if grade is True:
                    grade_str = "true"
                elif grade is False:
                    grade_str = "false"
                elif grade is None:
                    grade_str = "null"
                else:
                    grade_str = "(missing)"
            else:
                grade_str = "<for you to decide this turn>"
            out.append(f"### Turn {turn_num}")
            out.append(f"- actions: {action_str}")
            out.append(f"- reasoning: {reasoning}")
            out.append(f"- did this turn succeed?: {grade_str}")
            if turn_num in historic_turn_nums:
                out.append(
                    f"- (screenshot from the START of turn {turn_num}, BEFORE these actions "
                    "were pressed, is included in the image tail below — compare to the "
                    "CURRENT screen image)"
                )
            out.append("")
        return "\n".join(out).rstrip()

    def _render_current_task_text(self) -> str:
        """Value for the {{task_block}} placeholder: the TaskMaster-owned current
        task (title / description / success_criteria + progress line) when
        TaskMaster is enabled, else the legacy goal/description. The '## Current
        Task' header lives in the template."""
        if self.task_master_enabled:
            task = self.current_task
            if not task:
                # Cold-start fallback: reuse the config/snapshot task shape.
                task = self.tasks or self.config.get("task", {})
                if isinstance(task, str):
                    task = {"goal": task}
            task = task or {}
            # Title accepts either `title` (TaskMaster shape) or `goal` (config).
            title = task.get("title") or task.get("goal") or "Play the game."
            desc = task.get("description", "")
            criteria = task.get("success_criteria", "")
            task_text = f"**Task:** {title}"
            if desc:
                task_text += f"\n{desc}"
            if criteria:
                task_text += f"\n\n**Success criteria:** {criteria}"
            # The Player is intentionally NOT shown the per-task turn budget — it
            # should play the task on its merits, not pace to a turn count. The
            # budget is still enforced server-side (the output validator forces a
            # handoff at the boundary), the Player just isn't told about it.
            return task_text

        # Legacy (TaskMaster disabled): tasks.json overrides config task.
        task = self.tasks or self.config.get("task", {})
        if isinstance(task, str):
            task = {"goal": task}
        goal = task.get("goal", "Play the game.")
        desc = task.get("description", "")
        task_text = f"**Goal:** {goal}"
        if desc:
            task_text += f"\n{desc}"
        return task_text

    def _lookup_actions(self, turn_num: int) -> str:
        """Return the comma-joined action list for a past turn, or '?' if unknown.

        Looks up by REAL turn number (handoff turns leave gaps, so positional
        indexing drifts — see _explanation_turns). A handoff turn has a
        screenshot but no explanation, so its actions resolve to '?'.
        """
        if len(self._explanation_turns) == len(self.turn_explanations):
            try:
                idx = self._explanation_turns.index(turn_num)
            except ValueError:
                return "?"
        else:
            idx = turn_num - 1  # fallback: positional (old data)
        if 0 <= idx < len(self.turn_explanations):
            action = self.turn_explanations[idx].get("action", [])
            if isinstance(action, list):
                return ", ".join(action)
            return str(action)
        return "?"

    def finalize_run_summary(self, status: Optional[str] = None) -> None:
        """Write run_summary.json + restore stdout. Idempotent end-of-run hook.

        Called once on a clean loop exit, and also by run_single_loop's crash/stop
        handler — whichever reaches it first wins (the ``_summary_finalized``
        guard makes the second call a no-op). This guarantees a killed run
        (cooperative stop) or a crashed run (mid-run fault) still gets a FULL,
        readable summary instead of vanishing or masquerading as ``completed``
        (Andreas 2026-06-17). ``status``: ``"crashed"`` for a fault, ``None`` for
        a stop/clean-non-win (the executor then stamps ``cancelled`` for a stop
        or infers ``terminated``/``completed``), ``"completed"`` for a referee
        win. stdout restore + terminal-log close are best-effort so a partial
        run started before stdout was even teed still finalises cleanly."""
        if self._summary_finalized:
            return
        self._summary_finalized = True
        self._write_run_summary(status=status)
        if self._orig_stdout is not None:
            sys.stdout = self._orig_stdout
        if self._terminal_log is not None:
            try:
                self._terminal_log.close()
            except Exception:
                pass

    def _write_run_summary(
        self,
        *,
        run_id: str | None = None,
        kind: str | None = None,
        benchmark_version: str | None = None,
        status: str | None = None,
        continued_from: str | None = None,
        ended_at: str | None = None,
    ) -> None:
        """Write a structured run_summary.json to the run folder.

        The optional keyword args let a caller (the control-center executor, P3)
        stamp control-plane fields as TOP-LEVEL keys alongside the nested
        ``session``/``cost``/``turns``/``referee`` blocks. When omitted the writer
        behaves exactly as before (no extra keys), so existing callers are
        unchanged. The flat ``app.projection`` layer reads these when present and
        falls back to defensive inference when absent (legacy runs)."""
        this_session_s = (time.time() - self._run_start_time) if self._run_start_time else 0
        duration = this_session_s + self._prior_duration_s
        # Segment ledger: a continued run is transparently multi-segment. Record
        # THIS segment's own wall clock + where it resumed; the full chain is walked
        # via continued_from. duration_seconds stays cumulative ACTIVE-compute time
        # (sum of segments) and excludes the idle pause — it is display-only, never
        # scored (the benchmark ranks gates + turns), so an overnight gap is invisible.
        continued_from_cfg = self.config.get("_continued_from")
        resumed_at_turn = self.config.get("_continued_from_turn")

        summary = {
            "session": {
                "llm_alias": self.config.get("_llm_alias"),
                "llm_model": self.config.get("llm_model", ""),
                "thinking": self.config.get("thinking"),
                "fallback_models": self.fallback_models,
                "task": (self.tasks or self.config.get("task", {})).get("goal", ""),
                # total_turns is the leaderboard metric: player/game turns PLUS
                # TaskMaster invocations (Andreas 2026-06-17). player_turns is the
                # game-progress count gate deadlines are measured against; they're
                # broken out so the two scales stay legible. NOTE: including TM
                # turns means new official runs are not turn-comparable to runs
                # recorded before this change.
                "total_turns": self.turn_number + self.task_master_turns,
                "player_turns": self.turn_number,
                "task_master_turns": self.task_master_turns,
                "duration_seconds": round(duration, 1),
                "resumed": bool(continued_from_cfg),
                "segment": {
                    "continued_from": continued_from_cfg,
                    "resumed_at_turn": resumed_at_turn,
                    "prior_duration_s": round(self._prior_duration_s, 1),
                    "segment_duration_s": round(this_session_s, 1),
                    "segment_player_turns": (
                        self.turn_number - resumed_at_turn
                        if isinstance(resumed_at_turn, int)
                        else self.turn_number
                    ),
                },
                "started_at": time.strftime(
                    "%Y-%m-%dT%H:%M:%S",
                    time.localtime(self._run_start_time),
                ) if self._run_start_time else None,
            },
            "cost": {
                # total_usd is the all-in run cost: Player (LLM) + OCR +
                # TaskMaster. self.total_cost_usd tracks Player/OCR; the
                # TaskMaster cost is accumulated separately (Decision 10) so it
                # can be compared, and added in here for the grand total.
                "total_usd": round(self.total_cost_usd + self.task_master_cost_usd, 6),
                "llm_usd": round(
                    self.total_cost_usd
                    - (self.ocr.total_cost_usd if self.ocr else 0),
                    6,
                ),
                "ocr_usd": round(self.ocr.total_cost_usd if self.ocr else 0, 6),
                # Separate TaskMaster (strategy) cost — distinct from the Player's
                # (tactics) llm_usd above (Decision 10).
                "task_master_usd": round(self.task_master_cost_usd, 6),
                "total_input_tokens": self.total_input_tokens,
                "total_output_tokens": self.total_output_tokens,
                "per_turn": self.turn_costs,
            },
            "turns": [
                {
                    # Real turn number (handoff gaps make position != turn);
                    # falls back to positional only if the parallel list desyncs.
                    "turn": (
                        self._explanation_turns[i]
                        if len(self._explanation_turns) == len(self.turn_explanations)
                        else i + 1
                    ),
                    "action": exp.get("action", ""),
                    "reasoning": exp.get("reasoning", ""),
                    "last_turn_succeeded": exp.get("last_turn_succeeded"),
                }
                for i, exp in enumerate(self.turn_explanations)
            ],
        }

        # Why the run ended, when it ended badly. Only written on the
        # no-valid-output abort, so a normal summary keeps its exact old shape.
        # This is the one line that distinguishes "the model played and lost"
        # from "the model never answered" without reading events.jsonl.
        if self._aborted_no_output:
            summary["error"] = (
                self._abort_error or "no valid model output (retries + fallbacks exhausted)"
            )

        # The bounds this run was given, recorded whether or not they fired. A
        # cap that never fired still describes the run — "1500 turns, no budget"
        # and "20 turns, $1" are different experiments even when both end at
        # turn 12. Writing them only on the stop that fired would mean the
        # answer exists only for the losing condition.
        # getattr, not attribute access: several callers build a TurnManager via
        # __new__ to exercise this writer without constructing an agent, so the
        # __init__ defaults are not there to read.
        _turn_limit = getattr(self, "_turn_limit", None)
        if _turn_limit is not None:
            summary["max_turns"] = _turn_limit
        _spend_cap = getattr(self, "max_spend_usd", None)
        if _spend_cap is not None:
            summary["max_spend_usd"] = _spend_cap

        # Which of the three casual stop conditions actually fired, when it was
        # the budget. Deliberately NOT `termination_reason`: projection.py reads
        # that key as a missed-gate kill and would derive status `terminated`.
        # A run that spent its budget finished as asked — it stays `completed`.
        if getattr(self, "_budget_stopped", False):
            summary["stop_reason"] = "max_spend"

        # Referee scorecard (observe-only this phase). Best-effort: a scorecard
        # failure must not block writing the rest of the summary.
        if self.referee is not None:
            try:
                summary["referee"] = self.referee.scorecard()
            except Exception as e:
                summary["referee"] = {"error": str(e)}

        # Control-plane top-level fields (Plan §P1 deliverable 6). Only written
        # when a caller provides them; otherwise the dict keeps its legacy shape.
        # If the caller didn't pass an explicit status but the referee latched a
        # missed-gate termination, derive `terminated` locally.
        if status is None and self.referee is not None:
            try:
                if self.referee.termination_reason:
                    status = "terminated"
            except Exception:
                pass
        for _key, _val in (
            ("run_id", run_id),
            ("kind", kind),
            ("benchmark_version", benchmark_version),
            ("status", status),
            ("continued_from", continued_from),
            ("ended_at", ended_at),
        ):
            if _val is not None:
                summary[_key] = _val

        summary_path = self.logger.run_dir / "run_summary.json"
        with open(summary_path, "w") as f:
            json.dump(summary, f, indent=2)

        print(f"Run summary saved: {summary_path}")
