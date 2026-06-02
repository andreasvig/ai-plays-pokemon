"""Page-visit tool for the TaskMaster agent.

Fetches a URL with ``httpx`` and extracts readable text. The plan prefers
``readability-lxml`` or ``trafilatura`` to strip nav/ads; neither is a project
dependency in this environment, so this falls back to a minimal stdlib
tag-strip. If one of those libraries becomes available later, it is preferred
automatically.

Output is capped to ~10k chars to keep TaskMaster prompt-context cheap. A
per-instance URL cache satisfies the statelessness rule — caching is scoped to
a single TaskMaster invocation only (one ``PageVisitor`` per invocation), with
no cross-invocation persistence.
"""

from __future__ import annotations

import html
import re

import httpx

MAX_CHARS = 10_000

# Try the richer extractors first; degrade to the stdlib strip if absent.
try:  # pragma: no cover - depends on optional dep presence
    import trafilatura  # type: ignore

    _HAS_TRAFILATURA = True
except ImportError:  # pragma: no cover
    _HAS_TRAFILATURA = False

try:  # pragma: no cover - depends on optional dep presence
    from readability import Document  # type: ignore

    _HAS_READABILITY = True
except ImportError:  # pragma: no cover
    _HAS_READABILITY = False


_SCRIPT_STYLE_RE = re.compile(
    r"<(script|style|noscript|template|svg)\b[^>]*>.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_BLANKLINES_RE = re.compile(r"\n\s*\n\s*\n+")


def _strip_html(raw_html: str) -> str:
    """Minimal stdlib HTML-to-text: drop script/style, strip tags, unescape."""
    text = _SCRIPT_STYLE_RE.sub(" ", raw_html)
    # Turn block boundaries into newlines so paragraphs survive tag stripping.
    text = re.sub(r"<(br|/p|/div|/li|/h[1-6]|/tr)\b[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text)
    text = _BLANKLINES_RE.sub("\n\n", text)
    return text.strip()


def _extract_text(raw_html: str) -> str:
    """Extract main-content text, preferring richer extractors when available."""
    if _HAS_TRAFILATURA:  # pragma: no cover - optional dep
        extracted = trafilatura.extract(raw_html, include_comments=False)
        if extracted:
            return extracted.strip()
    if _HAS_READABILITY:  # pragma: no cover - optional dep
        try:
            summary_html = Document(raw_html).summary()
            return _strip_html(summary_html)
        except Exception:
            pass
    return _strip_html(raw_html)


class PageVisitor:
    """Fetches and extracts page text, caching by URL for one invocation.

    Instantiate one per TaskMaster invocation; the cache lives on the instance
    and is discarded when the invocation ends, honoring statelessness.
    """

    def __init__(self, max_chars: int = MAX_CHARS) -> None:
        self.max_chars = max_chars
        self._cache: dict[str, str] = {}

    def visit(self, url: str) -> str:
        """Fetch ``url`` and return capped readable text (or an error line).

        Never raises — returns a short ``"page visit failed: ..."`` string so
        the agent can move on rather than crash the run.
        """
        if url in self._cache:
            return self._cache[url]

        try:
            resp = httpx.get(
                url,
                timeout=30.0,
                follow_redirects=True,
                headers={"User-Agent": "ai-plays-pokemon-taskmaster/0.1"},
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            return f"page visit failed: HTTP {exc.response.status_code} for {url}"
        except httpx.HTTPError as exc:
            return f"page visit failed: {type(exc).__name__}: {exc}"

        text = _extract_text(resp.text)
        if len(text) > self.max_chars:
            text = text[: self.max_chars] + "\n\n[...truncated...]"
        if not text:
            text = f"page visit returned no extractable text for {url}"

        self._cache[url] = text
        return text
