"""Retry policy for provider-side failures: classify, then wait long enough.

Written after the 2026-09-09 qwen3.8-flash run: OpenRouter's shared pool for
that model returned HTTP 429 ("temporarily rate-limited upstream") on five of
twenty-one turns, the append agent re-sent each failed request immediately
(three attempts five seconds apart), and the run crashed at turn 21 — a
provider hiccup ended a benchmark run that was playing fine.

Three classes of failure want three different reactions:

- **transient** — the provider or the network failed, the request itself was
  fine: 429, 408, 5xx, a dropped connection, a timeout, a truncated body, an
  OpenRouter "no completion" finish. Retrying the identical request later is
  correct; the only question is how long to wait. These get equal-jitter
  exponential backoff (a growing guaranteed floor plus randomization so many
  runs do not hammer one recovering provider in lockstep), capped per wait and
  bounded by a wall-clock budget, and they honour ``Retry-After``.
- **output** — the model answered but not in the shape asked for (wrong tool,
  unparsable JSON, schema violation, truncated completion). The provider is
  healthy; re-asking immediately is fine, but only a few times — a model that
  cannot produce the shape will not learn to by waiting.
- **fatal** — the request is wrong or cannot be served: 400/401/402/403/404/
  405/422, a continuity violation, the spend budget. Retrying repeats the fault.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any, Mapping, Optional

# Provider HTTP statuses that mean "not now", never "not like that".
TRANSIENT_HTTP = frozenset({408, 425, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524, 529})
# Statuses where re-sending the same bytes cannot help.
FATAL_HTTP = frozenset({400, 401, 402, 403, 404, 405, 413, 422})


@dataclass(frozen=True)
class RetryPolicy:
    """Transient-retry knobs, read from ``transport:`` with these defaults.

    Ceilings for base 5 s, factor 2, cap 600 s: 5, 10, 20, 40, 80, 160, 320,
    600, 600, ... — the actual wait is ``ceiling/2 + U(0, ceiling/2)``. Twelve
    waits sum to ~30 min at the floors and ~45 min in expectation, which is
    what ``budget_s`` (45 min) is sized to. A provider that is still down after
    that has stopped being a hiccup.
    """

    transient_retries: int = 12
    base_s: float = 5.0
    factor: float = 2.0
    cap_s: float = 600.0
    budget_s: float = 2700.0

    @classmethod
    def from_transport(cls, transport: Mapping[str, Any]) -> "RetryPolicy":
        d = cls()
        return cls(
            transient_retries=int(transport.get("transient_retries", d.transient_retries)),
            base_s=float(transport.get("backoff_base_seconds", d.base_s)),
            factor=float(transport.get("backoff_factor", d.factor)),
            cap_s=float(transport.get("backoff_cap_seconds", d.cap_s)),
            budget_s=float(transport.get("retry_budget_seconds", d.budget_s)),
        )

    def wait_s(self, attempt_idx: int, retry_after_s: Optional[float] = None) -> float:
        """Seconds to wait before transient attempt ``attempt_idx + 2`` (0-based idx)."""
        w = equal_jitter_wait(attempt_idx, self.base_s, self.factor, self.cap_s)
        if retry_after_s is not None and retry_after_s > 0:
            # The provider named a time; never go earlier than it, but never
            # sit longer than the cap on a header we cannot verify.
            w = min(max(w, float(retry_after_s)), self.cap_s)
        return w


def equal_jitter_wait(attempt_idx: int, base_s: float, factor: float, cap_s: float) -> float:
    """Equal-jitter exponential backoff: ``ceiling/2 + U(0, ceiling/2)``.

    ``attempt_idx`` is 0-based (the wait after the first failure is idx 0).
    The floor grows geometrically so a flapping provider gets real time; the
    jitter decorrelates many runs retrying the same provider.
    """
    ceiling = min(cap_s, base_s * (factor ** max(0, attempt_idx)))
    return ceiling / 2.0 + random.uniform(0.0, ceiling / 2.0)


def parse_retry_after(headers: Optional[Mapping[str, str]]) -> Optional[float]:
    """Seconds from a ``Retry-After`` (delta or HTTP-date) or an ``X-RateLimit-Reset``
    (epoch ms, OpenRouter) header; None when neither is present or parsable."""
    if not headers:
        return None
    lower = {str(k).lower(): v for k, v in headers.items()}
    ra = lower.get("retry-after")
    if ra:
        try:
            return max(0.0, float(ra))
        except ValueError:
            try:
                return max(0.0, parsedate_to_datetime(ra).timestamp() - time.time())
            except (TypeError, ValueError):
                pass
    reset = lower.get("x-ratelimit-reset")
    if reset:
        try:
            value = float(reset)
        except ValueError:
            return None
        if value > 1e11:  # epoch milliseconds
            value /= 1000.0
        if value > 1e9:   # epoch seconds → delta
            value -= time.time()
        return max(0.0, value)
    return None


def classify(exc: BaseException) -> str:
    """``"fatal"``, ``"transient"`` or ``"output"`` for a failed append request.

    Imports the agent's error classes lazily so this module has no import
    cycle with append_agent.
    """
    from src.agent.append_agent import (  # noqa: WPS433 (lazy on purpose)
        ContinuityError,
        ProviderRefusal,
        ProviderRequestError,
        ProviderTransportError,
        SpendLimitReached,
    )

    if isinstance(exc, (ContinuityError, SpendLimitReached)):
        return "fatal"
    if isinstance(exc, ProviderRequestError):
        if exc.status in FATAL_HTTP:
            return "fatal"
        if exc.status in TRANSIENT_HTTP or exc.status >= 500:
            return "transient"
        return "fatal"
    if isinstance(exc, ProviderTransportError):
        return "transient"
    # A refusal is the PROVIDER declining, not the model answering badly: there is no
    # output to correct, and the correction note the output path appends is what turns
    # one refusal into a permanent one (2026-09-23, claude-opus-5.5). Back off and
    # re-ask the identical request instead.
    if isinstance(exc, ProviderRefusal):
        return "transient"
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "transient"
    try:
        import httpx  # noqa: WPS433
        if isinstance(exc, httpx.TransportError):  # connect/read/write/pool timeouts and network faults
            return "transient"
    except ImportError:  # pragma: no cover
        pass
    # A body that stopped mid-JSON is the provider cutting the stream, not the
    # model — but a JSONDecodeError from the model's own tool arguments is an
    # output defect. Callers that know which one wrap it; here the raw decoder
    # error on the transport path is what reaches us.
    if isinstance(exc, json.JSONDecodeError):
        return "transient"
    msg = str(exc)
    if any(p in msg for p in ("Provider returned error", "network_error", "upstream", "rate-limit", "rate limit",
                              "overloaded", "Service Unavailable", "Bad Gateway", "Gateway Timeout")):
        return "transient"
    return "output"


def describe(exc: BaseException) -> str:
    """One short line for the terminal and the live feed."""
    from src.agent.append_agent import ProviderRequestError  # noqa: WPS433

    if isinstance(exc, ProviderRequestError):
        body = exc.response_body if isinstance(exc.response_body, dict) else {}
        err = body.get("error") if isinstance(body.get("error"), dict) else {}
        meta = err.get("metadata") if isinstance(err.get("metadata"), dict) else {}
        raw = meta.get("raw") or err.get("message") or ""
        raw = str(raw).split(". ")[0][:120]
        return f"HTTP {exc.status}" + (f" — {raw}" if raw else "")
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
    return f"{type(exc).__name__}: {text[:120]}"


__all__ = ["RetryPolicy", "TRANSIENT_HTTP", "FATAL_HTTP", "equal_jitter_wait", "parse_retry_after", "classify", "describe"]
