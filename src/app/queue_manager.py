"""``queue.json``-backed serial queue with a single-active invariant.

State is ``{"active": <queue_id|null>, "items": [QueuedRun, ...]}``. The control
center runs ONE run at a time (locked decision #1): at most one ``active`` is
ever set, enforced in :meth:`set_active`. ``queue_path`` is injectable for tests.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from src.app.models import QueuedRun, RecordSpec, RunKind


def _now_iso() -> str:
    """Current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


class QueueManager:
    """Load/save the queue + enqueue/cancel/move/set-active.

    ``queue_path`` is the ``queue.json`` file. The in-memory state is loaded
    lazily on construction; every mutator persists.
    """

    def __init__(self, queue_path: Path) -> None:
        self.queue_path = Path(queue_path)
        self.active: str | None = None
        self.items: list[QueuedRun] = []
        self.load()

    # --- persistence ----------------------------------------------------------

    def load(self) -> None:
        """Load ``{active, items}`` from ``queue.json`` (empty if absent/unreadable)."""
        try:
            with open(self.queue_path) as f:
                raw = json.load(f)
        except Exception:
            self.active = None
            self.items = []
            return
        self.active = raw.get("active")
        self.items = [QueuedRun.model_validate(it) for it in raw.get("items", [])]

    def save(self) -> None:
        """Persist ``{active, items}`` to ``queue.json``."""
        self.queue_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.queue_path, "w") as f:
            json.dump(
                {
                    "active": self.active,
                    "items": [it.model_dump(mode="json") for it in self.items],
                },
                f,
                indent=2,
            )

    # --- mutators -------------------------------------------------------------

    def enqueue(
        self,
        kind: RunKind,
        model: str,
        *,
        config: str | None = None,
        benchmark: str | None = None,
        max_turns: int | None = None,
        stop_at: str | None = None,
        rom: str | None = None,
        continue_from: str | None = None,
        task_master_model: str | None = None,
        record: dict | RecordSpec | None = None,
        enqueued_at: str | None = None,
    ) -> QueuedRun:
        """Mint a :class:`QueuedRun`, append it, and save.

        ``benchmark`` is the official benchmark id (which ladder + goal); ``None``
        for casual runs. ``stop_at`` is a casual run's early finish line — the
        story event that ends it before its turn cap (``None`` = turn cap only).
        ``rom`` is which game a casual run needs — a ROM id from the registry
        (``None`` = the default ROM). ``record`` opts the run into an MP4 capture
        (``None`` = no recording).
        ``enqueued_at`` is overridable so tests can pin a deterministic
        timestamp; it defaults to the current UTC ISO time.
        """
        item = QueuedRun(
            queue_id=f"q_{uuid4().hex[:8]}",
            kind=RunKind(kind),
            model=model,
            config=config,
            benchmark=benchmark,
            max_turns=max_turns,
            stop_at=stop_at,
            rom=rom,
            continue_from=continue_from,
            task_master_model=task_master_model,
            record=RecordSpec.model_validate(record) if record is not None else None,
            enqueued_at=enqueued_at or _now_iso(),
        )
        self.items.append(item)
        self.save()
        return item

    def cancel(self, queue_id: str) -> bool:
        """Remove a queued item by id. Clears ``active`` if it was active.

        Returns True if something was removed.
        """
        before = len(self.items)
        self.items = [it for it in self.items if it.queue_id != queue_id]
        removed = len(self.items) != before
        if self.active == queue_id:
            self.active = None
        if removed:
            self.save()
        return removed

    def move(self, queue_id: str, to_index: int) -> None:
        """Reorder ``queue_id`` to position ``to_index`` (clamped) and save."""
        idx = next(
            (i for i, it in enumerate(self.items) if it.queue_id == queue_id), None
        )
        if idx is None:
            raise KeyError(f"queue_id not found: {queue_id}")
        item = self.items.pop(idx)
        to_index = max(0, min(to_index, len(self.items)))
        self.items.insert(to_index, item)
        self.save()

    def reorder(self, order: list[str]) -> None:
        """Reorder the whole queue to ``order`` (a permutation of current ids), save.

        ``order`` must list EXACTLY the current item ids — same set, no dupes,
        none missing — so a bulk reorder can never silently drop or duplicate a
        run. Raises :class:`ValueError` on any mismatch (the caller maps it to a
        400). The single-active invariant is untouched: ``active`` still names
        whichever item it named before, wherever it now sits.
        """
        by_id = {it.queue_id: it for it in self.items}
        if len(order) != len(set(order)):
            raise ValueError("order contains duplicate queue_ids")
        if set(order) != set(by_id):
            missing = set(by_id) - set(order)
            unknown = set(order) - set(by_id)
            raise ValueError(
                f"order must be a permutation of the current queue; "
                f"missing={sorted(missing)} unknown={sorted(unknown)}"
            )
        self.items = [by_id[qid] for qid in order]
        self.save()

    def set_active(self, queue_id: str | None) -> None:
        """Set (or clear, with None) the single active run, then save.

        Enforces the single-active invariant: ``queue_id`` (when not None) must
        be a known queued item. At most one active is ever set by construction
        (``active`` is a scalar).
        """
        if queue_id is not None:
            known = {it.queue_id for it in self.items}
            assert queue_id in known, f"cannot activate unknown queue_id: {queue_id}"
        self.active = queue_id
        self.save()

    # --- reads ----------------------------------------------------------------

    def peek_next(self) -> QueuedRun | None:
        """The next item to run: the first queued item that is not active."""
        for it in self.items:
            if it.queue_id != self.active:
                return it
        return None
