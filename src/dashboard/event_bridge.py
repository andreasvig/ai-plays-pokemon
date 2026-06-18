"""Bridges RunLogger events to the WebSocket broadcast system."""

import threading


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
            # Seconds of play already elapsed in a prior run, when this is a
            # --continue. The live "Elapsed" clock adds this to its own wall time
            # so a resumed run keeps counting up instead of restarting at 0.
            "prior_duration_s": 0.0,
        }

    def seed_stats(
        self,
        *,
        cost: float = 0.0,
        turns: int = 0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        prior_duration_s: float = 0.0,
    ) -> None:
        """Seed the running stats baseline (used by --continue).

        A continued run only streams ITS OWN events to clients, so cost/tokens
        would restart at 0 and the elapsed clock at the new boot time. Seeding
        the baseline from the source run's summary makes the live stats row pick
        up exactly where the prior run left off. Turns self-correct on the first
        turn_start; the rest accumulate on top of the seed.
        """
        with self._lock:
            self._stats["cost"] = cost
            self._stats["turns"] = turns
            self._stats["input_tokens"] = input_tokens
            self._stats["output_tokens"] = output_tokens
            self._stats["prior_duration_s"] = prior_duration_s

    def inject(self, event: dict) -> None:
        """Push a synthetic event to clients WITHOUT it touching the event log.

        Used by --continue to re-announce the restored TaskMaster task: the
        source run's task_started is in the copied events.jsonl (so the report
        still has it) but NOT in this session's live stream, so the live
        spectate would show "No task yet". Injecting a task_started-shaped event
        here surfaces it live without double-writing it to the persistent log.
        """
        with self._lock:
            self._events.append(event)

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
