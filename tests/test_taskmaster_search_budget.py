"""TaskMaster ask_perplexity search-budget enforcement.

The TaskMaster over-researched (many searches before every task), burning time
and request_limit rounds. ``tool_ask_perplexity`` now enforces a hard
per-invocation cap (``TaskMasterDeps.max_searches``): the first N calls run, and
every call after that REFUSES — returning a stop-researching instruction WITHOUT
spending an upstream Perplexity call. This is code-enforced, not just a prompt
rule (invariant-in-code-not-prompt).

These tests drive the tool directly with a minimal fake RunContext (the tool only
reads ``ctx.deps``) and a monkeypatched ``_ask_perplexity`` so no network is hit.
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agent import task_master as tm
from src.agent.task_master import DEFAULT_MAX_SEARCHES, TaskMasterDeps


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _patch_search(monkeypatch):
    """Replace the real Perplexity call with a counting stub; return the counter."""
    calls = {"n": 0}

    async def _fake(query, model):
        calls["n"] += 1
        return {"answer": f"answer for {query!r}", "cost_usd": 0.01}

    monkeypatch.setattr(tm, "_ask_perplexity", _fake)
    return calls


def test_default_max_searches_is_three():
    assert DEFAULT_MAX_SEARCHES == 3
    assert TaskMasterDeps().max_searches == 3
    assert TaskMasterDeps().search_count == 0


def test_first_n_searches_run_then_tool_refuses(monkeypatch):
    calls = _patch_search(monkeypatch)
    deps = TaskMasterDeps(max_searches=3)
    ctx = SimpleNamespace(deps=deps)

    # First 3 calls hit the (stubbed) search and return its answer.
    for i in range(3):
        out = _run(tm.tool_ask_perplexity(ctx, f"q{i}"))
        assert "answer for" in out
    assert calls["n"] == 3
    assert deps.search_count == 3

    # 4th call is refused WITHOUT an upstream call.
    refused = _run(tm.tool_ask_perplexity(ctx, "q4"))
    assert "budget exhausted" in refused.lower()
    assert "ask_perplexity" in refused  # tells the model the tool will keep refusing
    assert calls["n"] == 3  # no extra upstream call
    assert deps.search_count == 3  # counter not advanced past the cap


def test_budget_is_per_deps_instance(monkeypatch):
    """A fresh deps (next invocation) gets a fresh budget."""
    calls = _patch_search(monkeypatch)
    ctx1 = SimpleNamespace(deps=TaskMasterDeps(max_searches=2))
    _run(tm.tool_ask_perplexity(ctx1, "a"))
    _run(tm.tool_ask_perplexity(ctx1, "b"))
    assert "budget exhausted" in _run(tm.tool_ask_perplexity(ctx1, "c")).lower()

    ctx2 = SimpleNamespace(deps=TaskMasterDeps(max_searches=2))
    out = _run(tm.tool_ask_perplexity(ctx2, "a"))
    assert "answer for" in out
    assert ctx2.deps.search_count == 1
    assert calls["n"] == 3  # 2 from ctx1 + 1 from ctx2 (the refused one didn't call)


def test_cost_still_accumulates_only_for_real_calls(monkeypatch):
    _patch_search(monkeypatch)
    deps = TaskMasterDeps(max_searches=1)
    ctx = SimpleNamespace(deps=deps)
    _run(tm.tool_ask_perplexity(ctx, "a"))   # real → records cost
    _run(tm.tool_ask_perplexity(ctx, "b"))   # refused → no cost appended
    assert deps.tool_costs == [0.01]
