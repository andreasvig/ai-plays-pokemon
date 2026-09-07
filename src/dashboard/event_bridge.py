"""Bridges RunLogger events to the WebSocket broadcast system."""

import threading


# Event types the live wire deliberately drops (they are still written to
# ``events.jsonl`` and still reach the run report, which reads the file).
#
# ``turn_trace`` / ``compaction_trace`` carry the WHOLE outbound conversation of
# the request that produced them (``display_messages(outbound["messages"] +
# [message])`` in append_agent). On an append run the conversation grows for a
# full segment before a compaction resets it, so retaining one per turn is
# O(segment) each and O(n²) over a segment on the wire — and the events WS
# replays its entire backlog from cursor 0 on every (re)connect, so a reconnect
# 19 turns into a segment re-sends every one of them.
#
# Nothing on the live side consumes them: Spectate.svelte ignores both types
# (the report renders the trace from the file), and dashboard/recorder.py's
# event loop reads only the event NAME, and only for ``llm_output`` /
# ``turn_start`` / ``screen_settling`` / ``screen_settled``.
LIVE_EXCLUDED_TYPES = frozenset({"turn_trace", "compaction_trace"})


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
            # USD already spent by the lineage this run continues, on the same
            # principle as prior_duration_s. `cost` is the LINEAGE total, while
            # a spend ceiling bounds THIS segment (turn.py anchors the budget to
            # `_spend_baseline_usd`), so the live view needs the baseline to show
            # a cap ratio that isn't a lie on a resumed run.
            "prior_cost_usd": 0.0,
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
            self._stats["prior_cost_usd"] = cost

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
        """RunLogger listener callback. Must be non-blocking.

        Retains every event for the live stream EXCEPT the report-only whole-
        conversation traces (see ``LIVE_EXCLUDED_TYPES``). Their stats still
        fold in below — the filter is about what goes on the wire, not about
        what the run counts.
        """
        etype = event.get("type", "")
        if etype not in LIVE_EXCLUDED_TYPES:
            with self._lock:
                self._events.append(event)

        # Update running stats
        if etype == "turn_start":
            self._stats["turns"] = event.get("turn", self._stats["turns"])
        elif etype == "turn_usage":
            self._stats["cost"] += event.get("cost_usd") or 0
            self._stats["input_tokens"] += event.get("request_tokens") or 0
            self._stats["output_tokens"] += event.get("response_tokens") or 0
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
