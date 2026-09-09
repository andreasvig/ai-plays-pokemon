"""src/agent/backoff.py — the transient-retry policy behind the append agent.

Pins the schedule's SHAPE (floors grow, cap holds, jitter stays in the upper
half), the Retry-After parsing, and the three-way classification. The
numbers themselves are policy (configs/config-5.x transport block), so the
schedule test uses its own base/cap rather than the shipped ones.
"""

import asyncio
import json
import time

import httpx
import pytest

from src.agent.append_agent import ContinuityError, ProviderRequestError, ProviderTransportError, SpendLimitReached
from src.agent.backoff import RetryPolicy, classify, describe, equal_jitter_wait, parse_retry_after


def test_equal_jitter_floors_grow_and_the_cap_holds():
    base, factor, cap = 5.0, 2.0, 600.0
    for idx in range(8):
        ceiling = min(cap, base * factor ** idx)
        draws = [equal_jitter_wait(idx, base, factor, cap) for _ in range(300)]
        assert min(draws) >= ceiling / 2 - 1e-9, f"idx {idx}: floor is half the ceiling"
        assert max(draws) <= ceiling + 1e-9
    # past the cap every draw sits in [300, 600]
    assert all(300 <= equal_jitter_wait(30, base, factor, cap) <= 600 for _ in range(200))
    # twelve waits: floors sum to ~30 min, expected (mid-jitter) total ~45 min —
    # which is what the shipped 45 min budget is sized to
    ceilings = [min(cap, base * factor ** i) for i in range(12)]
    assert 1700 < sum(ceilings) / 2 < 1900
    assert 2600 < sum(ceilings) * 0.75 < 2800


def test_policy_reads_the_transport_block_and_defaults_the_rest():
    p = RetryPolicy.from_transport({"transient_retries": 3, "backoff_cap_seconds": 30})
    assert (p.transient_retries, p.cap_s) == (3, 30.0)
    assert (p.base_s, p.factor, p.budget_s) == (5.0, 2.0, 2700.0)
    assert RetryPolicy.from_transport({}) == RetryPolicy()


def test_retry_after_is_a_floor_but_never_beats_the_cap():
    p = RetryPolicy(base_s=5, factor=2, cap_s=100)
    assert all(30 <= p.wait_s(0, retry_after_s=30) <= 30 for _ in range(50))   # header above the backoff → header
    assert all(50 <= p.wait_s(6, retry_after_s=1) <= 100 for _ in range(50))   # header below → backoff (ceiling 100)
    assert all(p.wait_s(0, retry_after_s=10_000) == 100 for _ in range(10))    # absurd header → cap
    assert 2.5 <= p.wait_s(0, retry_after_s=None) <= 5


def test_parse_retry_after_delta_date_and_openrouter_reset():
    assert parse_retry_after({"Retry-After": "42"}) == 42.0
    assert parse_retry_after({"retry-after": "0"}) == 0.0
    later = time.time() + 90
    from email.utils import formatdate
    got = parse_retry_after({"Retry-After": formatdate(later, usegmt=True)})
    assert 85 <= got <= 91
    got = parse_retry_after({"X-RateLimit-Reset": str(int((time.time() + 60) * 1000))})
    assert 55 <= got <= 61
    assert parse_retry_after({"X-RateLimit-Reset": "1000"}) == 1000.0  # a plain delta stays a delta
    assert parse_retry_after({"Retry-After": "soon"}) is None
    assert parse_retry_after(None) is None and parse_retry_after({}) is None


@pytest.mark.parametrize("status,kind", [
    (429, "transient"), (500, "transient"), (502, "transient"), (503, "transient"), (504, "transient"), (529, "transient"),
    (408, "transient"), (400, "fatal"), (401, "fatal"), (402, "fatal"), (403, "fatal"), (404, "fatal"), (422, "fatal"),
])
def test_classify_http_statuses(status, kind):
    assert classify(ProviderRequestError(status, {"error": {"message": "x"}})) == kind


def test_classify_non_http_failures():
    assert classify(ProviderTransportError("Provider returned no completion (network_error)")) == "transient"
    assert classify(asyncio.TimeoutError()) == "transient"
    assert classify(httpx.ConnectError("boom")) == "transient"
    assert classify(httpx.ReadTimeout("slow")) == "transient"
    assert classify(json.JSONDecodeError("Expecting value", "", 0)) == "transient"  # truncated body on the wire
    assert classify(RuntimeError("{'code': 429, 'message': 'Provider returned error'}")) == "transient"  # 200 with an error body
    assert classify(ContinuityError("contract changed")) == "fatal"
    assert classify(SpendLimitReached("budget")) == "fatal"
    assert classify(ValueError("Incomplete model output: length")) == "output"
    assert classify(ValueError("Expected exactly one gameplay call")) == "output"


def test_describe_pulls_the_upstream_reason_out_of_the_openrouter_envelope():
    body = {"error": {"message": "Provider returned error", "code": 429, "metadata": {
        "raw": "qwen/qwen3.8-flash is temporarily rate-limited upstream. Please retry shortly, or add your own key"}}}
    assert describe(ProviderRequestError(429, body)) == "HTTP 429 — qwen/qwen3.8-flash is temporarily rate-limited upstream"
    assert describe(ProviderRequestError(503, {"error": "bad gateway text"})) == "HTTP 503"
    assert describe(ValueError("Incomplete model output: length")) == "ValueError: Incomplete model output: length"
