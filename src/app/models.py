"""Domain enums + pydantic models for the control center (Plan §P1).

``RunSummary`` is the FLAT, denormalized index entry — one row in
``runs_index.json`` and the unit the leaderboard/history derivations operate on.
It is *projected* from each run's nested ``run_summary.json`` (see
``projection.project_run_dir``); the nested writer in ``agent/turn.py`` stays the
source of truth and is never flattened in place.

``QueuedRun`` is what "add new run" produces — the unit the queue persists.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class RunKind(str, Enum):
    """The only run-classification axis (locked decision #4).

    ``official`` runs are the frozen benchmark (gates enforced, model is the only
    pick, counts on the leaderboard). ``casual`` runs pick model+config+max-turns,
    have no gates, and never reach the leaderboard. Continues are always casual.
    """

    official = "official"
    casual = "casual"


class RunStatus(str, Enum):
    """Run lifecycle state.

    ``queued`` → ``running`` → terminal. Terminal states:
      - ``completed``  ran to its natural end (official: final ladder gate; casual: max-turns).
      - ``terminated`` referee killed it on a missed gate deadline (official only).
      - ``crashed``    the process died unexpectedly.
      - ``cancelled``  user removed/stopped it; a cancelled official run is voided.
    """

    queued = "queued"
    running = "running"
    completed = "completed"
    terminated = "terminated"
    crashed = "crashed"
    cancelled = "cancelled"


class RunSummary(BaseModel):
    """Flat, denormalized per-run index entry (Plan "run_summary.json schema").

    Every field is derivable from a run folder (config + events + referee
    scorecard), so the whole index rebuilds by scanning ``local/runs/*``.
    """

    run_id: str
    label: str | None = None
    kind: RunKind
    model: str
    model_resolved: str | None = None
    config_stem: str | None = None
    benchmark_version: str | None = None
    status: RunStatus
    started_at: str | None = None
    ended_at: str | None = None
    turns: int = 0
    duration_s: float = 0.0
    total_cost_usd: float = 0.0
    avg_cost_per_turn_usd: float = 0.0
    avg_s_per_turn: float = 0.0
    furthest_gate: str | None = None
    furthest_gate_turn: int | None = None
    gates_reached: int = 0
    total_gates: int = 0
    termination_reason: str | None = None
    continued_from: str | None = None

    @property
    def leaderboard_eligible(self) -> bool:
        """True iff this run can post a leaderboard entry (locked decision #9).

        Only ``official`` runs that reached a terminal benchmark verdict
        (``completed`` = won, ``terminated`` = referee killed on a missed gate)
        count. A ``cancelled`` official run is voided — it never qualifies.
        """
        return self.kind == RunKind.official and self.status in (
            RunStatus.completed,
            RunStatus.terminated,
        )


class QueuedRun(BaseModel):
    """One item in the serial queue — the spec "add new run" produces.

    ``config``/``max_turns`` are casual-only (official uses the frozen pokebench
    config + no max-turns). ``continue_from`` is set by Continue (casual only).
    """

    queue_id: str
    kind: RunKind
    model: str
    config: str | None = None
    max_turns: int | None = None
    continue_from: str | None = None
    enqueued_at: str
