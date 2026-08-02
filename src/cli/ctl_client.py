"""Tiny stdlib HTTP client for the `pokemon queue` / `pokemon runs` CLIs.

These CLIs are thin wrappers over the running control center's JSON API
(``http://localhost:<port>``). No third-party deps — ``urllib`` only — so they
work in any environment the package is installed in. Each call returns
``(status_code, parsed_json)``; connection failures print a friendly hint and
exit, since the only cause is "the app isn't running on that port".
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from typing import Any


def api(
    method: str, path: str, *, port: int, body: dict | None = None, soft: bool = False
) -> tuple[int, Any]:
    """Call ``METHOD http://localhost:<port><path>`` → ``(status, json|None)``.

    ``body`` is JSON-encoded when given. HTTP error responses (4xx/5xx) are
    returned as ``(status, parsed_body)`` rather than raised, so callers can show
    the server's ``detail`` message. A refused connection is fatal (exit 3) —
    for every command except ``pokemon status``, "the app isn't running" is a
    dead end worth exiting on.

    ``soft=True`` inverts that: a refused connection returns ``(0, None)`` and
    prints nothing. ``status`` uses it because "the app is down" is one of the
    answers it exists to give, not a failure to give one.
    """
    url = f"http://localhost:{port}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method.upper())
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw) if raw else None
        except json.JSONDecodeError:
            return exc.code, {"detail": raw.decode(errors="replace")}
    except urllib.error.URLError as exc:
        if soft:
            return 0, None
        print(
            f"ERROR: cannot reach the control center at {url}\n"
            f"  ({exc.reason}) — is `pokemon app` running on port {port}?\n"
            f"  `pokemon status` shows what is up; `pokemon app` starts it.",
            file=sys.stderr,
        )
        sys.exit(3)


def detail(data: Any) -> str:
    """Best-effort human message from an error body (the API uses ``{detail}``)."""
    if isinstance(data, dict) and "detail" in data:
        return str(data["detail"])
    return json.dumps(data)


def emit_json(obj: Any) -> None:
    """Print ``obj`` as pretty JSON (the ``--json`` output path)."""
    print(json.dumps(obj, indent=2))
