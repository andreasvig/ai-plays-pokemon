"""``runs_index.json``-backed index of flat :class:`RunSummary` entries.

The index is a denormalized list, but it is NOT a source of truth: it is fully
rebuildable by scanning ``runs_root/*`` and projecting each run folder
(``rebuild_from_scan``). Both paths are injectable so tests use tmp dirs.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.app.models import RunSummary
from src.app.projection import project_run_dir


class RunIndex:
    """Load/save + scan-rebuild the run index.

    ``index_path`` is the ``runs_index.json`` file; ``runs_root`` is the
    directory whose immediate children are run folders (``local/runs`` in prod).
    """

    def __init__(self, index_path: Path, runs_root: Path) -> None:
        self.index_path = Path(index_path)
        self.runs_root = Path(runs_root)
        self._entries: list[RunSummary] = []

    def load(self) -> list[RunSummary]:
        """Load entries from ``runs_index.json`` (empty list if absent/unreadable)."""
        try:
            with open(self.index_path) as f:
                raw = json.load(f)
        except Exception:
            self._entries = []
            return self._entries
        self._entries = [RunSummary.model_validate(item) for item in raw]
        return self._entries

    def save(self) -> None:
        """Persist the in-memory entries to ``runs_index.json``."""
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.index_path, "w") as f:
            json.dump(
                [e.model_dump(mode="json") for e in self._entries], f, indent=2
            )

    def rebuild_from_scan(self) -> list[RunSummary]:
        """Re-derive the whole index by projecting every run folder under root.

        Folders without a readable ``run_summary.json`` (``project_run_dir``
        returns None) are dropped. Replaces the in-memory list and saves.
        """
        entries: list[RunSummary] = []
        if self.runs_root.exists():
            for child in sorted(self.runs_root.iterdir()):
                if not child.is_dir():
                    continue
                projected = project_run_dir(child)
                if projected is not None:
                    entries.append(projected)
        self._entries = entries
        self.save()
        return self._entries

    def upsert(self, summary: RunSummary) -> None:
        """Replace the entry with the same ``run_id`` or append it; then save."""
        for i, existing in enumerate(self._entries):
            if existing.run_id == summary.run_id:
                self._entries[i] = summary
                break
        else:
            self._entries.append(summary)
        self.save()

    def remove(self, run_id: str) -> bool:
        """Drop the entry with ``run_id`` and save. Returns True if one was removed.

        Index-only: callers that also want the run *folder* gone must delete it
        separately (the dashboard's delete route moves it to the Trash first,
        then calls this). A no-op when ``run_id`` isn't present.
        """
        before = len(self._entries)
        self._entries = [e for e in self._entries if e.run_id != run_id]
        removed = len(self._entries) != before
        if removed:
            self.save()
        return removed

    def get(self, run_id: str) -> RunSummary | None:
        """Return the entry with ``run_id``, or None."""
        for entry in self._entries:
            if entry.run_id == run_id:
                return entry
        return None

    def all(self) -> list[RunSummary]:
        """Return the current in-memory entries (a shallow copy)."""
        return list(self._entries)
