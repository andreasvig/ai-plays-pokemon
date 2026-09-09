"""Pure leaderboard + history derivations over a list of :class:`RunSummary`.

No I/O, no state — just functions the API layer (P4) calls over the index's
entries. The ranking metric is locked decision #3: "plays farthest, fastest" —
furthest first, fewest turns as the tiebreak. Since 2026-09-09 "farthest" is
``RunSummary.rank_score``: gates reached PLUS the fraction of the current leg
walked (path distance on the walk graph), so two runs terminated at the same
gate — which carry the same turn count, the gate's deadline — separate on how
close to the next gate they got. Runs without position data rank on gates.
"""

from __future__ import annotations

from src.app.models import RunStatus, RunSummary


def leaderboard(
    summaries: list[RunSummary], *, benchmark: str | None = None
) -> list[RunSummary]:
    """Best official run per model, ranked farthest-then-fastest.

    Pipeline:
      1. keep only ``leaderboard_eligible`` runs (official + completed/terminated;
         casual, continued-casual, and cancelled official runs are all excluded);
      2. when ``benchmark`` is given, keep only runs of THAT benchmark — each
         benchmark (easy / first-badge / full) has its own ranking, since their
         gate ladders differ and gate counts aren't comparable across them;
      3. group by ``model`` and pick the BEST = highest ``rank_score``
         (gates + leg fraction), tiebreak FEWEST ``turns``;
      4. sort the winners by (``rank_score`` desc, ``turns`` asc).
    """
    eligible = [s for s in summaries if s.leaderboard_eligible]
    if benchmark is not None:
        eligible = [s for s in eligible if s.benchmark == benchmark]

    best_by_model: dict[str, RunSummary] = {}
    for s in eligible:
        current = best_by_model.get(s.model)
        if current is None or _better(s, current):
            best_by_model[s.model] = s

    winners = list(best_by_model.values())
    winners.sort(key=lambda s: (-s.rank_score, s.turns))
    return winners


def _better(candidate: RunSummary, incumbent: RunSummary) -> bool:
    """True if ``candidate`` beats ``incumbent``: farther (gates + leg fraction), or as far + fewer turns."""
    if candidate.rank_score != incumbent.rank_score:
        return candidate.rank_score > incumbent.rank_score
    return candidate.turns < incumbent.turns


_SORT_KEYS = {
    "recent": lambda s: (s.started_at or ""),
    "completion": lambda s: s.rank_score,
    "cost": lambda s: s.total_cost_usd,
    "duration": lambda s: s.duration_s,
}


def history(
    summaries: list[RunSummary],
    *,
    kind=None,
    status=None,
    benchmark: str | None = None,
    q: str | None = None,
    sort: str = "recent",
    order: str = "desc",
) -> list[RunSummary]:
    """Filter + sort the full run list for the history view.

    Filters: ``kind`` (RunKind), ``status`` (RunStatus), ``benchmark`` (id —
    keeps only runs of that benchmark), and ``q`` (case-insensitive substring
    matched against ``model`` and ``run_id``). Sort keys: ``recent``
    (started_at), ``completion`` (rank_score), ``cost`` (total_cost_usd),
    ``duration`` (duration_s); ``order`` is "asc"/"desc".
    """
    rows = list(summaries)

    if kind is not None:
        rows = [s for s in rows if s.kind == kind]
    if status is not None:
        rows = [s for s in rows if s.status == status]
    if benchmark is not None:
        rows = [s for s in rows if s.benchmark == benchmark]
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
