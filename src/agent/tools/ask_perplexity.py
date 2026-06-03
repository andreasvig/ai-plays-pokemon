"""Perplexity Sonar "ask" tool for the TaskMaster agent (via OpenRouter).

A single search-and-synthesize tool that replaces the separate ``web_search`` +
``page_visit`` pair: the TaskMaster asks a natural-language question and a
Perplexity Sonar model (selected by config) does the searching and returns a
synthesized answer with citations. Routed through OpenRouter so it shares the
``OPENROUTER_API_KEY`` and the project's single provider.

Cost is captured per call — OpenRouter returns ``usage.cost`` when the request
sets ``usage: {include: true}`` — so the TaskMaster's research spend is accounted
separately from its reasoning tokens.

Degrades gracefully when ``OPENROUTER_API_KEY`` is absent/empty or the call
fails: returns an "unavailable" payload (cost 0.0) instead of raising, so the
planner falls back to its own knowledge. The key is read at call time from
``os.environ`` — any path that needs it must have called ``load_dotenv()`` first
(an empty key is treated identically to a missing one, cf. the OpenRouter
empty-``api_key`` env-fallback footgun).
"""

from __future__ import annotations

import os

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Default Perplexity model. Candidates (in Andreas's preference order):
# perplexity/sonar-pro-search, perplexity/sonar-reasoning-pro,
# perplexity/sonar-pro, perplexity/sonar. Override via config
# ``task_master.search_model``.
DEFAULT_SEARCH_MODEL = "perplexity/sonar-pro-search"

# Short framing so the sonar model returns concise, actionable route/strategy
# facts rather than an essay.
_SYSTEM = (
    "You are a research assistant for a Pokemon FireRed playthrough. Answer the "
    "question concisely and factually with concrete, actionable detail (route "
    "order, locations, levels, gym-leader teams, item spots). Prefer specifics "
    "over generalities and cite your sources."
)


def _get_api_key() -> str:
    """Read OPENROUTER_API_KEY from the environment (empty string if unset)."""
    return os.environ.get("OPENROUTER_API_KEY", "") or ""


@retry(
    retry=retry_if_exception_type(httpx.TransportError),
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=1, max=10),
    reraise=True,
)
async def _call_openrouter(api_key: str, model: str, query: str) -> dict:
    """Raw OpenRouter chat-completions call against a Perplexity Sonar model.

    Retries on transport errors (connection/timeout); HTTP-status errors are
    raised straight through to the graceful handler in ``ask_perplexity``.
    Requests ``usage.include`` so the response carries the dollar cost.
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": query},
        ],
        "usage": {"include": True},
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(OPENROUTER_URL, headers=headers, json=payload)
        resp.raise_for_status()
        return resp.json()


def _extract(data: dict) -> tuple[str, list[str], float]:
    """Pull (answer, citations, cost_usd) out of an OpenRouter response body."""
    choices = data.get("choices") or []
    answer = ""
    citations: list[str] = []
    if choices:
        msg = (choices[0] or {}).get("message") or {}
        answer = str(msg.get("content") or "").strip()
        # Sources surface either as per-message `annotations` (url_citation) or
        # a top-level `citations` list of URLs — collect from both.
        for ann in msg.get("annotations") or []:
            if isinstance(ann, dict):
                url = ((ann.get("url_citation") or {}).get("url")) or ann.get("url")
                if url:
                    citations.append(str(url))
    for c in data.get("citations") or []:
        if isinstance(c, str):
            citations.append(c)
        elif isinstance(c, dict) and c.get("url"):
            citations.append(str(c["url"]))
    # Dedupe, preserve order.
    seen: set[str] = set()
    citations = [u for u in citations if not (u in seen or seen.add(u))]

    usage = data.get("usage") or {}
    try:
        cost = float(usage.get("cost") or 0.0)
    except (TypeError, ValueError):
        cost = 0.0
    return answer, citations, cost


async def ask_perplexity(query: str, model: str = DEFAULT_SEARCH_MODEL) -> dict:
    """Ask a Perplexity Sonar model (via OpenRouter) and return a synthesized answer.

    Returns a dict the agent can read directly:
      - ``{"query", "answer", "citations": [...], "model", "cost_usd"}`` on success
      - ``{..., "answer": "", "citations": [], "cost_usd": 0.0, "error": "..."}``
        when unavailable (no key) or on failure. NEVER raises — a planning agent
        should degrade to its own knowledge rather than crash the run.
    """
    api_key = _get_api_key()
    if not api_key:
        return {
            "query": query,
            "answer": "",
            "citations": [],
            "model": model,
            "cost_usd": 0.0,
            "error": (
                "perplexity unavailable: no OPENROUTER_API_KEY set in the "
                "environment. Proceed using your own knowledge."
            ),
        }

    try:
        data = await _call_openrouter(api_key, model, query)
        answer, citations, cost = _extract(data)
        return {
            "query": query,
            "answer": answer,
            "citations": citations,
            "model": model,
            "cost_usd": cost,
        }
    except httpx.HTTPStatusError as exc:
        return {
            "query": query,
            "answer": "",
            "citations": [],
            "model": model,
            "cost_usd": 0.0,
            "error": f"perplexity failed: HTTP {exc.response.status_code} from OpenRouter.",
        }
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        return {
            "query": query,
            "answer": "",
            "citations": [],
            "model": model,
            "cost_usd": 0.0,
            "error": f"perplexity failed: {type(exc).__name__}: {exc}",
        }
