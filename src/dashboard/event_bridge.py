"""Bridges RunLogger events to the WebSocket broadcast system."""

import threading
from typing import Any


class EventBridge:
    """Receives events from RunLogger (sync callback) and makes them available to WebSocket clients.

    Uses an append-only list with per-client cursor tracking instead of a shared queue.
    Each client tracks its position, so multiple clients and reconnections work correctly.
    """

    def __init__(self):
        self._events: list[dict] = []
        self._lock = threading.Lock()
        self._stats = {
            "cost": 0.0,
            "turns": 0,
            "input_tokens": 0,
            "output_tokens": 0,
        }

    def on_event(self, event: dict) -> None:
        """RunLogger listener callback. Must be non-blocking."""
        with self._lock:
            self._events.append(event)

        # Update running stats
        etype = event.get("type", "")
        if etype == "turn_start":
            self._stats["turns"] = event.get("turn", self._stats["turns"])
        elif etype == "turn_usage":
            self._stats["cost"] += event.get("cost_usd", 0)
            self._stats["input_tokens"] += event.get("request_tokens", 0)
            self._stats["output_tokens"] += event.get("response_tokens", 0)
        elif etype == "ocr_flush":
            self._stats["cost"] += event.get("cost_usd", 0)

    def get_events_since(self, cursor: int) -> tuple[list[dict], int]:
        """Return all events since cursor position, and the new cursor.

        Thread-safe. Each client tracks its own cursor.
        """
        with self._lock:
            new_events = self._events[cursor:]
            new_cursor = len(self._events)
        return new_events, new_cursor

    def get_stats(self) -> dict:
        """Return current running stats."""
        return dict(self._stats)
