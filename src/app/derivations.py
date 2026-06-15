"""Pure leaderboard + history derivations over a list of :class:`RunSummary`.

No I/O, no state — just functions the API layer (P4) calls over the index's
entries. The ranking metric is locked decision #3: "plays farthest, fastest" —
furthest gate first, fewest turns as the tiebreak.
"""

from __future__ import annotations

from src.app.models import RunStatus, RunSummary


def leaderboard(summaries: list[RunSummary]) -> list[RunSummary]:
    """Best official run per model, ranked farthest-then-fastest.

    Pipeline:
      1. keep only ``leaderboard_eligible`` runs (official + completed/terminated;
         casual, continued-casual, and cancelled official runs are all excluded);
      2. group by ``model`` and pick the BEST = highest ``gates_reached``,
         tiebreak FEWEST ``turns``;
      3. sort the winners by (``gates_reached`` desc, ``turns`` asc).
    """
    eligible = [s for s in summaries if s.leaderboard_eligible]

    best_by_model: dict[str, RunSummary] = {}
    for s in eligible:
        current = best_by_model.get(s.model)
        if current is None or _better(s, current):
            best_by_model[s.model] = s

    winners = list(best_by_model.values())
    winners.sort(key=lambda s: (-s.gates_reached, s.turns))
    return winners


def _better(candidate: RunSummary, incumbent: RunSummary) -> bool:
    """True if ``candidate`` beats ``incumbent``: more gates, or same gates + fewer turns."""
    if candidate.gates_reached != incumbent.gates_reached:
        return candidate.gates_reached > incumbent.gates_reached
    return candidate.turns < incumbent.turns


_SORT_KEYS = {
    "recent": lambda s: (s.started_at or ""),
    "completion": lambda s: s.gates_reached,
    "cost": lambda s: s.total_cost_usd,
    "duration": lambda s: s.duration_s,
}


def history(
    summaries: list[RunSummary],
    *,
    kind=None,
    status=None,
    q: str | None = None,
    sort: str = "recent",
    order: str = "desc",
) -> list[RunSummary]:
    """Filter + sort the full run list for the history view.

    Filters: ``kind`` (RunKind), ``status`` (RunStatus), and ``q`` (case-insensitive
    substring matched against ``model`` and ``run_id``). Sort keys:
    ``recent`` (started_at), ``completion`` (gates_reached), ``cost``
    (total_cost_usd), ``duration`` (duration_s); ``order`` is "asc"/"desc".
    """
    rows = list(summaries)

    if kind is not None:
        rows = [s for s in rows if s.kind == kind]
    if status is not None:
        rows = [s for s in rows if s.status == status]
    if q:
        needle = q.lower()
        rows = [
            s
            for s in rows
            if needle in s.model.lower() or needle in s.run_id.lower()
        ]

    key = _SORT_KEYS.get(sort, _SORT_KEYS["recent"])
    rows.sort(key=key, reverse=(order != "asc"))
    return rows


def run_counts_by_model(summaries: list[RunSummary]) -> dict[str, int]:
    """Map each ``model`` alias → how many runs exist for it in the index.

    Counts EVERY run (any kind/status) so the new-run dialog (Round 8 / C3) can
    show ``model — N runs`` and tell at a glance which models are already
    benchmarked. Pure aggregation over the index entries — cheap, no I/O.
    Models with zero runs simply don't appear in the map (the API defaults them
    to 0).
    """
    counts: dict[str, int] = {}
    for s in summaries:
        counts[s.model] = counts.get(s.model, 0) + 1
    return counts


__all__ = ["leaderboard", "history", "run_counts_by_model", "RunStatus"]
