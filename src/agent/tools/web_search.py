"""Serper (Google Search) web-search tool for the TaskMaster agent.

A standalone light wrapper around the Serper API (``google.serper.dev/search``),
mirroring the uAPE Serper provider shape (``httpx.AsyncClient``, ``SERPER_API_KEY``
read from env, tenacity retry on transport errors) without importing it — the
``ape`` package is not importable in this repo.

Degrades gracefully when ``SERPER_API_KEY`` is absent or empty: the search
function returns a clear "unavailable" payload instead of raising, so the
TaskMaster agent can keep planning with no live web access. The key is read at
call time from ``os.environ`` — any path that needs it must have called
``load_dotenv()`` first (an empty key is treated identically to a missing one,
cf. the OpenRouter empty-``api_key`` env-fallback footgun).
"""

from __future__ import annotations

import json
import os

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

SERPER_URL = "https://google.serper.dev/search"

# Default number of organic results to surface. Kept small to keep the
# TaskMaster prompt-context cheap (it accumulates these across rounds).
DEFAULT_NUM_RESULTS = 5


def _get_api_key() -> str:
    """Read SERPER_API_KEY from the environment (empty string if unset)."""
    return os.environ.get("SERPER_API_KEY", "") or ""


@retry(
    retry=retry_if_exception_type(httpx.TransportError),
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=1, max=10),
    reraise=True,
)
async def _call_serper(api_key: str, query: str, num_results: int) -> str:
    """Raw Serper API call. Returns the JSON response body as a string.

    Retries on transport errors (connection/timeout); HTTP-status errors are
    raised straight through to the graceful handler in ``web_search``.
    """
    num_results = min(max(int(num_results), 1), 30)
    headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}
    payload = {"q": query, "num": num_results}

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(SERPER_URL, headers=headers, json=payload)
        resp.raise_for_status()
        return resp.text


def _extract_top_results(payload: str, limit: int) -> list[dict[str, str]]:
    """Parse a compact top-N ``[{title, link, snippet}]`` list from Serper JSON."""
    data = json.loads(payload)
    organic = data.get("organic") or []
    results: list[dict[str, str]] = []
    for item in organic:
        if not isinstance(item, dict):
            continue
        link = str(item.get("link") or "").strip()
        if not link:
            continue
        results.append(
            {
                "title": str(item.get("title") or "").strip(),
                "link": link,
                "snippet": str(item.get("snippet") or "").strip(),
            }
        )
        if len(results) >= limit:
            break
    return results


async def web_search(query: str, num_results: int = DEFAULT_NUM_RESULTS) -> dict:
    """Search the web via Serper and return the top organic results.

    Returns a dict the agent can read directly:
      - ``{"query", "results": [{title, link, snippet}, ...]}`` on success
      - ``{"query", "results": [], "error": "..."}`` when search is
        unavailable (no key) or fails. NEVER raises — a planning agent should
        degrade to its own knowledge rather than crash the run.
    """
    api_key = _get_api_key()
    if not api_key:
        return {
            "query": query,
            "results": [],
            "error": (
                "web search unavailable: no SERPER_API_KEY set in the "
                "environment. Proceed using your own knowledge."
            ),
        }

    try:
        raw = await _call_serper(api_key, query, num_results)
        results = _extract_top_results(raw, num_results)
        return {"query": query, "results": results}
    except httpx.HTTPStatusError as exc:
        return {
            "query": query,
            "results": [],
            "error": f"web search failed: Serper HTTP {exc.response.status_code}.",
        }
    except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
        return {
            "query": query,
            "results": [],
            "error": f"web search failed: {type(exc).__name__}: {exc}",
        }
