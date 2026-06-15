"""RunExecutor — the serial dispatcher of queued runs into the persistent emulator.

Plan §P3 (+ "Architecture" RunExecutor box). The control inversion: the
:class:`~src.app.supervisor.AppSupervisor` owns one long-lived emulator; this
executor pops one :class:`~src.app.models.QueuedRun` at a time off the
:class:`~src.app.queue_manager.QueueManager`, builds the right run config, runs
it against ``supervisor.handle`` via an injected ``run_fn`` (defaults to
``cli.runner.run_single_loop`` — which itself registers the live
``RunSession`` so the existing spectate streams work unchanged), stamps the
run's ``run_summary.json`` with the control-plane top-level fields, upserts the
flat :class:`~src.app.models.RunSummary` into the :class:`~src.app.run_index.RunIndex`,
clears ``active``, frees the supervisor, and auto-advances.

Single-active invariant (locked decision #1): only one run executes at a time —
``drain_once`` is a no-op while ``supervisor.status().busy``. Official runs
(locked #4/#7) use the FROZEN config (``config-3.13`` — TaskMaster-enabled) +
the v1 gate ladder, gates ENFORCED, NO max-turns. Casual runs use their chosen
config + max-turns and have no gates.

Everything heavy is injectable so tests run fully headless with a FAKE
``run_fn`` (and never launch mGBA): the config builders, the savepoint
resolver, and the run function are all seams.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from src.app.models import QueuedRun, RunKind, RunStatus
from src.app.projection import project_run_dir

# Frozen official benchmark wiring (locked decisions #4 / #7). Read at runtime —
# never hardcode gate numbers; the ladder + config stay WIP until launch.
OFFICIAL_CONFIG = "configs/config-3.13.yaml"
OFFICIAL_LADDER = "configs/checkpoints-firered-v1.yaml"
OFFICIAL_BENCHMARK_VERSION = "pokebench-v1"

# Canonical committed start save (locked #7) — official + casual-fresh load this.
CANONICAL_SAVE = "configs/saves/pokebench-v1"


# A run function: (handle, config, *, turns, snapshot, open_browser, open_report) -> run_dir.
RunFn = Callable[..., Path]


class RunExecutor:
    """Serial drain loop: one queued run at a time into the persistent emulator.

    Inject ``run_fn`` (a ``run_single_loop``-shaped callable) so tests use a
    FAKE that writes a minimal ``run_summary.json`` without launching mGBA. The
    config builders + savepoint resolver are likewise seams.
    """

    def __init__(
        self,
        *,
        supervisor: Any,
        queue_manager: Any,
        run_index: Any,
        runs_root: str | Path,
        saves_dir: str | Path,
        run_fn: Optional[RunFn] = None,
        official_config_path: str = OFFICIAL_CONFIG,
        official_ladder_path: str = OFFICIAL_LADDER,
        canonical_save: str | Path = CANONICAL_SAVE,
        prepare_config_fn: Optional[Callable[..., dict]] = None,
        continue_fn: Optional[Callable[[str], tuple[dict, Path]]] = None,
    ) -> None:
        self.supervisor = supervisor
        self.queue = queue_manager
        self.index = run_index
        self.runs_root = Path(runs_root)
        self.saves_dir = Path(saves_dir)

        self.official_config_path = official_config_path
        self.official_ladder_path = official_ladder_path
        self.canonical_save = str(canonical_save)

        # Seams (default to the shared runner helpers; lazy import so the module
        # is import-light and tests can inject before any runner import cost).
        self._run_fn = run_fn
        self._prepare_config_fn = prepare_config_fn
        self._continue_fn = continue_fn

        # Stop signalling (locked #9). When a run_id is requested to stop, the
        # currently-executing run is finalised as cancelled (+ voided if it was
        # official). Set by :meth:`request_stop`; consumed after the run_fn returns.
        self._stop_requested_run_id: Optional[str] = None
        # Maps the active queue_id -> the run-dir name the run_fn produced, so a
        # stop/finalise can be attributed to the right on-disk run.
        self._active_run_id: Optional[str] = None
        self._active_kind: Optional[RunKind] = None

        self._lock = threading.Lock()
        self._stopped = threading.Event()

    # --- control-hub notify ---------------------------------------------------

    def _notify_control(self) -> None:
        """Best-effort ping the dashboard control hub on a state change (P6).

        Lazy import + swallow everything: the executor must stay usable headless
        (tests, no server) and a broadcast failure must never derail the drain.
        """
        try:
            from src.dashboard.server import notify_control

            notify_control()
        except Exception:
            pass

    # --- seam resolution ------------------------------------------------------

    def _resolve_run_fn(self) -> RunFn:
        if self._run_fn is not None:
            return self._run_fn
        from src.cli.runner import run_single_loop

        self._run_fn = run_single_loop
        return self._run_fn

    def _resolve_prepare_config(self) -> Callable[..., dict]:
        if self._prepare_config_fn is not None:
            return self._prepare_config_fn
        from src.cli.runner import prepare_config

        self._prepare_config_fn = prepare_config
        return self._prepare_config_fn

    def _resolve_continue_fn(self) -> Callable[[str], tuple[dict, Path]]:
        if self._continue_fn is not None:
            return self._continue_fn
        from src.cli.runner import continue_from_run

        self._continue_fn = continue_from_run
        return self._continue_fn

    # --- config building ------------------------------------------------------

    def build_run_config(self, item: QueuedRun) -> tuple[dict, str | None, int]:
        """Build (config, snapshot_dir, turns) for a queued item.

        - Official (locked #4/#7): FROZEN ``config-3.13`` (TaskMaster on) + v1
          ladder injected with ``enforce: true``, NO max-turns (a large sentinel
          turn cap that the gate ladder bounds in practice), canonical start save.
        - Casual fresh: the item's chosen config + max-turns, canonical start save.
        - Casual continue: reuse the SOURCE run's config + model via
          ``continue_from_run`` (resolves the latest savepoint), the item's
          max-turns, snapshot = that savepoint dir.
        """
        prepare_config = self._resolve_prepare_config()

        if item.continue_from:
            # Casual continue — model + config come from the source run, not the
            # item (locked #10 reuses the source model; the API already enforced
            # this when enqueuing). Resolve the latest savepoint.
            cfg, savepoint_dir = self._resolve_continue_fn()(item.continue_from)
            turns = item.max_turns or 1500
            return cfg, str(savepoint_dir), turns

        if item.kind == RunKind.official:
            cfg = prepare_config(self.official_config_path, item.model)
            # Inject the frozen gate ladder, ENFORCED (locked #4/#7). The
            # official config carries no referee block of its own; we read the
            # ladder POINTER at runtime (never the gate numbers).
            cfg["referee"] = {
                "checkpoints": self.official_ladder_path,
                "enforce": True,
            }
            # NO max-turns: pace is the only bound (locked #8). We still pass a
            # large sentinel turn cap to the loop (it never owns termination —
            # the referee's gate deadlines do).
            turns = self._OFFICIAL_TURN_SENTINEL
            return cfg, self.canonical_save, turns

        # Casual fresh.
        cfg = prepare_config(self._resolve_config_path(item.config), item.model)
        turns = item.max_turns or 1500
        return cfg, self.canonical_save, turns

    @staticmethod
    def _resolve_config_path(config: str | None) -> str:
        """Map a casual config STEM (e.g. ``config-3.13``, as ``/api/configs``
        returns and the dialog enqueues) to its file path
        (``configs/config-3.13.yaml``). Accepts a bare stem, a filename, or a
        full path unchanged — so this is idempotent for already-resolved paths."""
        if not config:
            raise ValueError("casual run requires a config")
        if "/" in config or config.endswith(".yaml"):
            return config
        return f"configs/{config}.yaml"

    # A turn cap big enough that the gate ladder (not max-turns) bounds official
    # runs — "no max-turns" in practice. Read as a sentinel, not a tuned number.
    _OFFICIAL_TURN_SENTINEL = 10_000_000

    # --- drain ----------------------------------------------------------------

    def drain_once(self) -> Optional[str]:
        """Run the next queued item if idle; return its run_id, or None.

        No-op (returns None) when the supervisor is busy or the queue has nothing
        runnable — enforcing the single-active invariant. Persists the active
        queue_id before running and clears it after, so a crash mid-run leaves a
        recoverable queue.
        """
        with self._lock:
            if self.supervisor.status().busy:
                return None
            item = self.queue.peek_next()
            if item is None:
                return None
            # Claim it as active under the lock so a concurrent drain can't
            # double-pop (single-active invariant).
            self.queue.set_active(item.queue_id)
            self.supervisor.set_busy(True)
            self._active_kind = item.kind

        # State change: a run just became active. Ping the control hub so the
        # Home view reflects it live (Plan §P6). Best-effort — never let a
        # notify failure derail the drain.
        self._notify_control()

        run_id: Optional[str] = None
        try:
            config, snapshot, turns = self.build_run_config(item)
            run_fn = self._resolve_run_fn()
            # Publish the active run id the instant the run starts (not after it
            # returns) so the control plane exposes it DURING the run — live
            # spectate + a matchable stop target (run_fn blocks for the whole run).
            def _publish(rd):
                self._active_run_id = Path(rd).name
                # Re-notify now that the active run id is known (the earlier
                # run-became-active ping fired before run_fn set it). This second
                # push lets the SPA refetch and open the live spectate stream.
                self._notify_control()

            run_dir = run_fn(
                self.supervisor.handle,
                config,
                turns=turns,
                snapshot=snapshot,
                open_browser=False,
                open_report=False,
                on_run_dir=_publish,
            )
            run_dir = Path(run_dir)
            run_id = run_dir.name
            self._active_run_id = run_id
            self._finalize_run(run_dir, item)
        finally:
            with self._lock:
                # Remove the just-run item from the queue and clear active.
                self.queue.cancel(item.queue_id)
                self.supervisor.set_busy(False)
                self._active_run_id = None
                self._active_kind = None
            # State change: the run finished, the queue advanced, and (on a
            # successful finalize) the leaderboard may have a new row. Ping the
            # control hub so Home refetches live (Plan §P6).
            self._notify_control()
        return run_id

    def drain_loop(self, poll_interval: float = 0.5) -> None:
        """Serial loop: drain the queue forever until :meth:`stop`.

        Sleeps ``poll_interval`` between empty polls so an idle queue doesn't
        spin. The ``pokemon app`` entrypoint runs this in a background thread.
        """
        self._stopped.clear()
        while not self._stopped.is_set():
            try:
                ran = self.drain_once()
            except Exception:
                # A single run failing (bad config, dispatch error, mid-run
                # crash) must NOT kill the serial drain thread — otherwise one
                # poisoned item silently stops the whole executor. drain_once's
                # finally already removed the item from the queue and cleared
                # busy/active, so we just log and carry on to the next item.
                import traceback

                traceback.print_exc()
                ran = None
            if ran is None:
                self._stopped.wait(poll_interval)

    def stop(self) -> None:
        """Signal :meth:`drain_loop` to exit after the current iteration."""
        self._stopped.set()

    # --- finalize + stamp -----------------------------------------------------

    def _finalize_run(self, run_dir: Path, item: QueuedRun) -> None:
        """Stamp the control-plane top-level fields onto ``run_summary.json``.

        Post-hoc JSON augmentation (load → set top-level keys → dump): sets
        ``run_id``, ``kind``, ``benchmark_version`` (the official version for an
        official run, else null), ``continued_from``, ``ended_at``, and ``status``.

        Status mapping (locked decisions): a referee missed-deadline already wrote
        ``terminated`` and a referee final-gate win already wrote ``completed``
        from the turn loop; we only fill ``status`` when the run didn't latch one
        (casual hit max-turns → ``completed``). A stop request overrides to
        ``cancelled`` (and an official run is then voided by leaving
        ``benchmark_version`` null so it's never leaderboard-eligible).
        """
        summary_path = run_dir / "run_summary.json"
        try:
            with open(summary_path) as f:
                summary = json.load(f)
        except Exception:
            summary = {}

        is_official = item.kind == RunKind.official
        stop_requested = (
            self._stop_requested_run_id is not None
            and self._stop_requested_run_id == run_dir.name
        )

        summary["run_id"] = run_dir.name
        summary["kind"] = item.kind.value
        summary["continued_from"] = item.continue_from

        # Determine status. A stop request wins. Else respect a status the turn
        # loop already wrote (terminated on missed gate, completed on win). Else
        # the run reached its natural end (casual max-turns) → completed.
        existing_status = summary.get("status")
        if stop_requested:
            status = RunStatus.cancelled.value
        elif existing_status in {s.value for s in RunStatus}:
            status = existing_status
        else:
            status = RunStatus.completed.value
        summary["status"] = status

        # benchmark_version: pokebench-v1 for an official run that posts a verdict;
        # a CANCELLED (voided, locked #9) official run gets NULL so it is never
        # leaderboard-eligible. Casual = always null.
        if is_official and status != RunStatus.cancelled.value:
            summary["benchmark_version"] = OFFICIAL_BENCHMARK_VERSION
        else:
            summary["benchmark_version"] = None

        summary.setdefault(
            "ended_at", time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
        )

        try:
            with open(summary_path, "w") as f:
                json.dump(summary, f, indent=2)
        except Exception:
            pass

        # Project the (now-stamped) run dir into the flat index.
        projected = project_run_dir(run_dir)
        if projected is not None:
            self.index.upsert(projected)

        # Clear a consumed stop request.
        if stop_requested:
            self._stop_requested_run_id = None

    # --- stop (locked decision #9) --------------------------------------------

    def request_stop(self, run_id: str) -> bool:
        """Request the active run be finalised as ``cancelled`` (+ voided if official).

        Records the request; the actual savepoint + finalise happens when the
        run_fn returns. Returns True if ``run_id`` matches the active run (the
        only thing that can be stopped). The graceful savepoint itself is taken
        by the turn loop's KeyboardInterrupt/crash handler when the run is
        interrupted; this method records the VERDICT (cancelled + void).
        """
        self._stop_requested_run_id = run_id
        return self._active_run_id == run_id or run_id == self.queue.active

    # --- continue (locked decision #10) ---------------------------------------

    def build_continue_spec(self, source_run_id: str) -> dict:
        """Build a CASUAL continue spec that reuses the source run's model.

        Resolves the latest savepoint of the source run (raises if none), reads
        the source model from its ``run_summary.json`` (or the index), and
        returns a dict ready for ``QueueManager.enqueue`` kwargs: always casual,
        ``continue_from`` set, model reused. An injected model is the CALLER's
        problem to drop — this never reads one (locked #10).
        """
        source_dir = self.runs_root / source_run_id
        # Resolve the latest savepoint (raises FileNotFoundError if absent).
        from src.cli.runner import _find_latest_savepoint

        _find_latest_savepoint(source_dir)  # validates a savepoint exists

        model = self._source_model(source_run_id, source_dir)
        return {
            "kind": RunKind.casual,
            "model": model,
            "config": None,
            "continue_from": source_run_id,
        }

    def _source_model(self, source_run_id: str, source_dir: Path) -> str:
        """Recover the source run's model alias (index first, then summary)."""
        entry = self.index.get(source_run_id)
        if entry is not None and entry.model:
            return entry.model
        try:
            with open(source_dir / "run_summary.json") as f:
                summary = json.load(f)
            session = summary.get("session") or {}
            return session.get("llm_alias") or session.get("llm_model") or "unknown"
        except Exception:
            return "unknown"
