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

from src.app.benchmarks import get_benchmark
from src.app.catalog import stop_at_referee_config
from src.app.models import QueuedRun, RunKind, RunStatus
from src.app.roms import apply_rom, get_rom, rom_for_game
from src.app.projection import project_run_dir
from src.app.trace_build import build_and_cache_trace

# Frozen official benchmark wiring (locked decisions #4 / #7). Read at runtime —
# never hardcode gate numbers; the ladder + config stay WIP until launch.
OFFICIAL_CONFIG = "configs/config-3.13.yaml"
OFFICIAL_LADDER = "configs/checkpoints-firered-v1.yaml"
OFFICIAL_BENCHMARK_VERSION = "pokebench-v1"

# Canonical committed start save (locked #7) — official + casual-fresh load this.
CANONICAL_SAVE = "configs/saves/pokebench-v1"


# A run function: (handle, config, *, turns, snapshot, open_browser) -> run_dir.
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

        # Last dispatch failure — an item that was dequeued but never became a
        # run (bad config, ROM that won't load, recorder that won't boot).
        # ``drain_loop`` deliberately swallows every such failure so one poisoned
        # item can't freeze the serial queue; the cost was that the item just
        # VANISHED — the caller saw its 201, the queue went back to empty, and
        # the only trace was a traceback on the app's stdout, which nothing
        # tails. This is that trace, kept somewhere the API can read it.
        # ``{queue_id, kind, model, error, at}``; cleared when the next run
        # actually starts.
        self.last_error: Optional[dict] = None

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

    def _record_failure(self, item: QueuedRun, error: str) -> None:
        """Remember why an item was dequeued without producing a run, and say so.

        Two audiences: the terminal (one loud line, so someone watching the app
        sees it immediately) and ``GET /api/queue``'s ``last_error`` (so a client
        that only ever sees the 201 can find out afterwards). Best-effort — a
        failure to record a failure must never derail the drain.
        """
        try:
            self.last_error = {
                "queue_id": item.queue_id,
                "kind": item.kind.value if hasattr(item.kind, "value") else str(item.kind),
                "model": item.model,
                "error": error,
                "at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
            }
            print(f"QUEUE: dispatch FAILED for {item.queue_id} ({item.model}) — {error}")
            self._notify_control()
        except Exception:
            pass

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

        - Official (locked #4/#7): FROZEN ``config-3.13`` (TaskMaster on) + the
          chosen benchmark's gate ladder injected with ``enforce: true``, the
          benchmark's overall goal overriding the config's ``task.goal``, NO
          max-turns (a large sentinel turn cap that the gate ladder bounds in
          practice), canonical start save.
        - Casual fresh: the item's chosen config + max-turns, canonical start save.
        - Casual continue: reuse the SOURCE run's config + model via
          ``continue_from_run`` (resolves the latest savepoint), the item's
          max-turns, snapshot = that savepoint dir.

        Both casual branches also take the item's optional ``stop_at`` story
        event (see :meth:`_apply_stop_at`) and its optional ``max_spend_usd``
        ceiling (see :meth:`_apply_max_spend`). Both bound the run alongside
        max-turns rather than replacing it — first one to land ends it. Neither
        is offered to an official run: pace is its only bound (locked #8).

        Which ROM each branch plays: official takes the benchmark ladder's, casual
        fresh takes the item's (``None`` → the registry default), and a continue
        inherits the source run's — its config already carries the ``rom_path``
        and ``game_name`` it was played with, so a resumed Emerald run resumes on
        Emerald without the queue item having to say so.
        """
        prepare_config = self._resolve_prepare_config()

        if item.continue_from:
            # Casual continue — model + config come from the source run, not the
            # item (locked #10 reuses the source model; the API already enforced
            # this when enqueuing). Resolve the latest savepoint.
            # ``continue_from`` is the canonical run_id (a bare dir name); the
            # resolver treats its argument as a PATH (Path(...).resolve()), so it
            # must get the FULL run dir under runs_root — NOT the bare id, which
            # would resolve against CWD and fail "not a directory".
            source_dir = self.runs_root / item.continue_from
            cfg, savepoint_dir = self._resolve_continue_fn()(str(source_dir))
            if item.kind == RunKind.official:
                # Tamper-seal gate: an official resume must prove the paused
                # checkpoint wasn't hand-edited. A present-but-mismatched seal is
                # genuine tamper evidence — refuse to resume it AS official (it
                # must never post a score). A legacy savepoint with no seal passes
                # (verify_savepoint returns True). Raising here skips the run; the
                # drain logs it and advances the queue.
                from src.core.snapshots import SnapshotManager
                if not SnapshotManager.verify_savepoint(savepoint_dir):
                    raise ValueError(
                        f"checkpoint seal mismatch for official continue of "
                        f"{item.continue_from!r} — refusing to resume a tampered "
                        f"benchmark checkpoint (savepoint: {savepoint_dir})"
                    )
                # Official continue: resume the SAME benchmark. Re-apply the
                # canonical official wiring (goal + enforced ladder + benchmark
                # mode) on top of the resumed config, reading the ladder POINTER
                # at runtime exactly like the official-fresh branch below — so the
                # continued run stays enforced and leaderboard-eligible even if the
                # source predates a ladder edit. No max-turns (gate deadlines bound
                # it); the savepoint dir is the snapshot.
                benchmark = get_benchmark(item.benchmark)
                task = cfg.get("task")
                if not isinstance(task, dict):
                    task = {}
                task["goal"] = benchmark.goal
                cfg["task"] = task
                cfg["referee"] = {"checkpoints": benchmark.ladder, "enforce": True}
                self._stamp_mode(cfg, "benchmark")
                return cfg, str(savepoint_dir), self._OFFICIAL_TURN_SENTINEL
            # Casual continue. Playstyle is per-SEGMENT like stop_at and the
            # budget: you may well be continuing precisely because you now want
            # the other one.
            self._stamp_mode(cfg, self._mode_for_gameplay(item.gameplay))
            # Casual continue may swap the Player and/or TaskMaster model (UI
            # pickers). ``item.model`` carries the chosen Player model. Re-resolve
            # it ONLY when it (a) is a genuine registry selection and (b) actually
            # differs from the source run's own alias — so a plain reuse-continue
            # keeps the source's EXACT settings (no needless round-trip, no churn
            # if the registry retuned the model since), an override re-resolves,
            # and a legacy/raw/stale ``model`` value can't crash dispatch.
            # ``item.task_master_model`` is the explicit TM override; None keeps
            # the source/freeplay-default resolution.
            from src.cli.runner import _resolve_player_model, _resolve_task_master_model
            from src.config import _load_models_registry, is_valid_model_selection

            source_alias = cfg.get("_llm_alias") or cfg.get("llm_model")
            if (
                item.model
                and item.model != source_alias
                and is_valid_model_selection(item.model, _load_models_registry())
            ):
                _resolve_player_model(cfg, item.model)
            if item.task_master_model and self._tm_enabled(cfg):
                _resolve_task_master_model(cfg, item.task_master_model)
            # A continue picks its OWN stop event (like max-turns) — it is a
            # property of this segment, not something inherited from the source.
            self._apply_stop_at(cfg, item.stop_at)
            self._apply_max_spend(cfg, item.max_spend_usd)
            turns = item.max_turns or 1500
            return cfg, str(savepoint_dir), turns

        if item.kind == RunKind.official:
            # Which benchmark this official run plays (easy / first-badge / full).
            # None / unknown falls back to the registry default so a stale queue
            # item still runs rather than wedging the drain.
            benchmark = get_benchmark(item.benchmark)
            cfg = prepare_config(self.official_config_path, item.model)
            # Override the frozen config's goal with the benchmark's overall goal
            # — the meta-goal the agent (TaskMaster) plays toward. The frozen
            # config stays the same across all benchmarks; only the objective +
            # gate ladder change, so cross-model comparability holds per benchmark.
            task = cfg.get("task")
            if not isinstance(task, dict):
                task = {}
            task["goal"] = benchmark.goal
            cfg["task"] = task
            # Inject the benchmark's gate ladder, ENFORCED (locked #4/#7). Each
            # benchmark has its OWN ladder file (a self-contained prefix of the
            # full ladder); reaching its final rung WINS the run. We read the
            # ladder POINTER at runtime (never the gate numbers).
            cfg["referee"] = {
                "checkpoints": benchmark.ladder,
                "enforce": True,
            }
            # The ROM is the BENCHMARK's, never the item's: a score has to come
            # from the dump the ladder's gate addresses were authored against, so
            # an official run has no ROM choice to make. (``item.rom`` is
            # casual-only and the API refuses to set it on an official run.)
            apply_rom(cfg, rom_for_game(benchmark.game))
            # Official = benchmark mode: the frozen config's TaskMaster (and the
            # Player) get benchmark_guidelines.
            self._stamp_mode(cfg, "benchmark")
            # NO max-turns: pace is the only bound (locked #8). We still pass a
            # large sentinel turn cap to the loop (it never owns termination —
            # the referee's gate deadlines do).
            turns = self._OFFICIAL_TURN_SENTINEL
            return cfg, self.canonical_save, turns

        # Casual fresh = custom/freeplay mode: whichever agent owns the
        # guidelines on the chosen config gets freeplay_guidelines. A config with
        # no task_master block (4.0+) skips TM model resolution entirely — there
        # is no TaskMaster to give a model to.
        cfg = prepare_config(self._resolve_config_path(item.config), item.model)
        self._stamp_mode(cfg, self._mode_for_gameplay(item.gameplay))
        if self._tm_enabled(cfg):
            from src.cli.runner import _resolve_task_master_model

            _resolve_task_master_model(cfg, None)
        self._apply_stop_at(cfg, item.stop_at)
        self._apply_max_spend(cfg, item.max_spend_usd)
        # Which game. None → the registry default, so an item enqueued before
        # ROMs existed behaves exactly as it did. The start state travels with
        # the ROM: the canonical save is FireRed's bedroom, and loading it under
        # another cartridge would restore garbage — so a non-default ROM starts
        # from its OWN committed savepoint, or from the title screen when it
        # hasn't got one yet.
        rom = get_rom(item.rom)
        apply_rom(cfg, rom)
        snapshot = self.canonical_save if rom.is_default else rom.start_save
        turns = item.max_turns or 1500
        return cfg, snapshot, turns

    def _ensure_rom_loaded(self, cfg: dict) -> None:
        """Make the emulator hold the ROM this run's config asks for.

        Called once per dispatch, after the config is built and before the turn
        loop. Almost always a no-op — the supervisor returns immediately when it
        already has that ROM — so the cost of supporting mixed-game queues is one
        string comparison per run.

        When it is NOT a no-op, mGBA relaunches and the Lua script has to be
        re-loaded by hand before the run can start (see
        :meth:`AppSupervisor.switch_rom`); the dispatch blocks on that reconnect.
        That is why the switch happens HERE rather than at enqueue time: the
        queue stays freely editable, and the interruption lands on the run that
        actually needs the other cartridge.

        Tolerates a supervisor without ``switch_rom`` (the fakes injected by
        tests) and a config with no ``rom_path`` — both mean "nothing to
        reconcile", not "fail the run".
        """
        wanted = str((cfg.get("emulator") or {}).get("rom_path") or "")
        switch = getattr(self.supervisor, "switch_rom", None)
        if not wanted or not callable(switch):
            return
        switch(wanted, force=True)

    @staticmethod
    def _tm_enabled(cfg: dict) -> bool:
        """True when this config actually runs the TaskMaster meta-agent."""
        return bool((cfg.get("task_master") or {}).get("enabled", False))

    # The two playstyles a casual run can pick, and the steering block each one
    # selects. The VALUES are the existing run modes, so nothing downstream
    # changes: `agent._player_mode_guidelines` and `task_master._mode_guidelines`
    # already switch on exactly these strings.
    GAMEPLAY_MODES = {"exploration": "freeplay", "speed": "benchmark"}
    DEFAULT_GAMEPLAY = "exploration"

    @classmethod
    def _mode_for_gameplay(cls, gameplay: str | None) -> str:
        """Run mode for a CASUAL item's playstyle.

        ``None`` → exploration → ``"freeplay"``, which is what every casual run
        has always been given, so an item enqueued before this field existed
        behaves identically. An unrecognised value falls back the same way
        rather than raising: the API rejects a bad playstyle at enqueue time
        (:func:`server._validate_gameplay`), so anything reaching here is either
        valid or a hand-edited queue.json, and wedging the drain over it would
        be worse than playing it the default way.

        Official runs never call this — a benchmark always races.
        """
        return cls.GAMEPLAY_MODES.get(
            (gameplay or cls.DEFAULT_GAMEPLAY).lower(),
            cls.GAMEPLAY_MODES[cls.DEFAULT_GAMEPLAY],
        )

    @classmethod
    def _stamp_mode(cls, cfg: dict, mode: str) -> None:
        """Stamp the run mode (``"benchmark"`` / ``"freeplay"``) onto the config.

        Two sinks, because two different agents consume it:
          - top-level ``mode`` — read by the PLAYER
            (``agent._player_mode_guidelines``). The only sink that exists on a
            TaskMaster-less config (4.0+), where the Player carries the
            freeplay/benchmark guidelines itself.
          - ``task_master.mode`` — read by the TASKMASTER
            (``task_master._mode_guidelines``), and only stamped when a
            task_master block is already present. Deliberately NOT setdefault'd
            into existence: on a 4.0 config that would conjure a phantom
            ``task_master:`` block which reads as "TaskMaster is configured" in
            logs and makes ``_resolve_task_master_model`` resolve a model for an
            agent that is never constructed.
        """
        cfg["mode"] = mode
        tm = cfg.get("task_master")
        if isinstance(tm, dict):
            tm["mode"] = mode

    @staticmethod
    def _apply_stop_at(cfg: dict, stop_at: str | None) -> None:
        """Wire a casual run's early finish line onto its config, in place.

        The block itself is built by ``catalog.stop_at_referee_config`` (shared
        with ``pokemon run --stop-at``): the FULL ladder, observe-only, plus the
        chosen gate. A side effect worth knowing is that the run also gets the
        live gate HUD and a scorecard — wanted, and it stays off the leaderboard
        because eligibility keys on ``kind == official``, never on the presence
        of a ladder.

        No stop event → the config is left exactly as it was (a casual run gets
        no referee block at all, as before).
        """
        block = stop_at_referee_config(stop_at)
        if block is not None:
            cfg["referee"] = block

    @staticmethod
    def _apply_max_spend(cfg: dict, max_spend_usd: float | None) -> None:
        """Wire a casual run's USD ceiling onto its config, in place.

        Rides on the config rather than a new dispatch parameter because that is
        where ``TurnManager`` already looks for its bounds, and it means the same
        key serves the queue and ``pokemon run --max-spend``. Like ``stop_at``,
        a continue picks its OWN ceiling — the budget bounds the segment you are
        launching, not the lineage.

        No ceiling → the key is not written, so the config keeps its exact prior
        shape and the run is unbounded, as every casual run was before.
        """
        if max_spend_usd is not None:
            cfg["max_spend_usd"] = float(max_spend_usd)

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
        captured: dict = {}
        try:
            config, snapshot, turns = self.build_run_config(item)
            self._ensure_rom_loaded(config)
            # Opt-in MP4 recording. Stamped onto the config rather than passed as
            # a run_fn argument: `run_single_loop` is the one place both entry
            # points (this executor and `pokemon run --record`) converge, and it
            # already reads private `_`-prefixed keys off the config. Keeping the
            # run_fn signature fixed also means every injected test fake keeps
            # working unchanged. See dashboard/recorder.py.
            if item.record is not None:
                config["_record"] = item.record.model_dump(mode="json")
            run_fn = self._resolve_run_fn()
            # Publish (and capture) the active run dir the instant the run starts
            # (not after it returns) so the control plane exposes it DURING the run
            # — live spectate + a matchable stop target (run_fn blocks for the whole
            # run) — AND so a run interrupted by a stop (run_fn raises
            # KeyboardInterrupt, see below) can still be finalised from the captured
            # dir even though run_fn never returned it.
            def _publish(rd):
                captured["run_dir"] = Path(rd)
                self._active_run_id = Path(rd).name
                # A run really started, so whatever failed last time is history.
                self.last_error = None
                # Re-notify now that the active run id is known (the earlier
                # run-became-active ping fired before run_fn set it). This second
                # push lets the SPA refetch and open the live spectate stream.
                self._notify_control()

            run_dir = None
            try:
                run_dir = run_fn(
                    self.supervisor.handle,
                    config,
                    turns=turns,
                    snapshot=snapshot,
                    open_browser=False,
                    on_run_dir=_publish,
                    # Cooperative stop: true once a stop is requested for the run
                    # that's currently active. The turn loop checks this each turn
                    # and raises KeyboardInterrupt → savepoint → cancelled. Without
                    # it, request_stop only recorded a verdict and never halted the
                    # run, so the UI "kill" appeared to do nothing.
                    should_stop=lambda: (
                        self._stop_requested_run_id is not None
                        and self._stop_requested_run_id == self._active_run_id
                    ),
                )
            except KeyboardInterrupt:
                # run_single_loop RAISES KeyboardInterrupt when the turn loop was
                # interrupted — a stop request (locked #9) or a Ctrl-C that reached
                # the run — instead of returning the run_dir. This is an EXPECTED
                # stop, not a fatal error. It must NOT propagate out of the drain
                # thread: letting it bubble to drain_loop (whose `except Exception`
                # cannot catch a BaseException like KeyboardInterrupt) would KILL the
                # serial drain thread and freeze the queue forever — no run, official
                # or casual, would ever start again. Recover the run dir the run_fn
                # published before it raised and fall through to finalise it (the
                # stop verdict — cancelled + voided — is applied by _finalize_run).
                run_dir = captured.get("run_dir")
            if run_dir is not None:
                run_dir = Path(run_dir)
                run_id = run_dir.name
                self._active_run_id = run_id
                self._finalize_run(run_dir, item)
            elif captured.get("run_dir") is None:
                # The run_fn returned without ever publishing a run dir, so there
                # is no folder to finalise and nothing on disk to explain it.
                self._record_failure(item, "run produced no run directory")
        except Exception as exc:
            # Record it somewhere readable BEFORE re-raising. drain_loop still
            # catches (and still prints the traceback) — this only adds a trace
            # the API can serve, so a dequeued-then-dropped item stops being
            # indistinguishable from one that never existed.
            self._record_failure(item, f"{type(exc).__name__}: {exc}")
            raise
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
            # Auto-mute once the queue has drained: a run just ended and nothing
            # is waiting, so return the emulator to its default silent state
            # rather than play audio to an empty room. If another run is queued
            # we leave the audio as-is (the next run starts immediately).
            try:
                if self.queue.peek_next() is None:
                    self.set_mute(True)
            except Exception:
                pass
        return run_id

    def set_mute(self, mute: bool) -> bool:
        """Best-effort mute/unmute of the emulator via the supervisor.

        Returns the resulting muted state. Never raises — a supervisor without a
        ``set_mute`` (or an AppleScript failure) is swallowed, leaving the last
        known state. Used by the /api/emulator/mute route and the auto-mute hook.
        """
        sup = self.supervisor
        fn = getattr(sup, "set_mute", None)
        if fn is not None:
            try:
                return bool(fn(mute))
            except Exception:
                pass
        return bool(getattr(sup, "muted", mute))

    def drain_loop(self, poll_interval: float = 0.5) -> None:
        """Serial loop: drain the queue forever until :meth:`stop`.

        Sleeps ``poll_interval`` between empty polls so an idle queue doesn't
        spin. The ``pokemon app`` entrypoint runs this in a background thread.
        """
        self._stopped.clear()
        while not self._stopped.is_set():
            try:
                ran = self.drain_once()
            except BaseException:
                # A single run failing (bad config, dispatch error, mid-run
                # crash, or a stop's KeyboardInterrupt that escaped drain_once)
                # must NOT kill the serial drain thread — otherwise one poisoned
                # item silently stops the whole executor and the queue freezes
                # forever. We catch BaseException (not just Exception) precisely
                # so a KeyboardInterrupt/SystemExit raised inside a run can never
                # take the drain thread down. (App shutdown uses self._stopped on
                # the MAIN thread — Ctrl-C is delivered there, not here — so this
                # is safe and does not swallow a real shutdown.) drain_once's
                # finally already removed the item + cleared busy/active, so we
                # just log and carry on to the next item.
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

        # benchmark / benchmark_version: stamp WHICH benchmark an official run
        # played (drives the per-benchmark filter AND lets a continue resume the
        # same benchmark) on EVERY official run — including a CANCELLED (voided,
        # locked #9) or CRASHED one. The void is enforced by STATUS:
        # ``leaderboard_eligible`` already requires completed/terminated, so a
        # cancelled official run with a benchmark id can never reach the board; it
        # only stays identifiable so an overnight stop can be continued + finished.
        # ``benchmark_version`` (the season marker) is still withheld from
        # cancelled/crashed runs to preserve the existing void semantics. Casual =
        # always null on both.
        if is_official:
            summary["benchmark"] = get_benchmark(item.benchmark).id
            summary["benchmark_version"] = (
                None
                if status in (RunStatus.cancelled.value, RunStatus.crashed.value)
                else OFFICIAL_BENCHMARK_VERSION
            )
        else:
            summary["benchmark"] = None
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

        # Cache the projected trace so the Report opens without re-parsing the
        # whole events.jsonl on every request (stale-guarded by mtime in the API).
        try:
            build_and_cache_trace(run_dir)
        except Exception:
            pass

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

    def build_continue_spec(
        self,
        source_run_id: str,
        *,
        player_model: str | None = None,
        task_master_model: str | None = None,
    ) -> dict:
        """Build a continue spec, reusing the source run's model by default.

        Resolves the latest savepoint of the source run (raises if none), reads
        the source model from its ``run_summary.json`` (or the index), and
        returns a dict ready for ``QueueManager.enqueue`` kwargs.

        CASUAL continues may override the models (UI pickers): ``player_model``
        replaces the reused Player model, ``task_master_model`` sets a TaskMaster
        override. OFFICIAL continues IGNORE both overrides — their models stay
        locked to the source so the resumed benchmark segment is comparable and
        leaderboard-eligible (locked #10).

        Kind is INHERITED from the source: a continue of an OFFICIAL run stays
        official and resumes the SAME benchmark (so a run stopped overnight can be
        finished + scored), rather than silently downgrading to casual. A casual
        source continues casual.
        """
        source_dir = self.runs_root / source_run_id
        # Resolve the latest savepoint (raises FileNotFoundError if absent).
        from src.cli.runner import _find_latest_savepoint

        _find_latest_savepoint(source_dir)  # validates a savepoint exists

        source_model = self._source_model(source_run_id, source_dir)
        kind, benchmark = self._source_kind_and_benchmark(source_run_id, source_dir)
        # Official continues are model-locked; casual may swap Player + TaskMaster.
        is_casual = kind != RunKind.official
        spec = {
            "kind": kind,
            "model": (player_model if (is_casual and player_model) else source_model),
            "config": None,
            "continue_from": source_run_id,
            "task_master_model": (task_master_model if is_casual else None),
        }
        if kind == RunKind.official:
            spec["benchmark"] = benchmark
        return spec

    def _source_kind_and_benchmark(
        self, source_run_id: str, source_dir: Path
    ) -> tuple[RunKind, str | None]:
        """Recover (kind, benchmark id) of the source run, so an official continue
        resumes the same benchmark. Index first, then the run summary. A benchmark
        id of None (a legacy/stopped official run that never stamped one) falls
        back downstream to the registry default in ``build_run_config``."""
        entry = self.index.get(source_run_id)
        if entry is not None and entry.kind == RunKind.official:
            return RunKind.official, entry.benchmark
        try:
            with open(source_dir / "run_summary.json") as f:
                summary = json.load(f)
            if summary.get("kind") == RunKind.official.value:
                return RunKind.official, summary.get("benchmark")
        except Exception:
            pass
        return RunKind.casual, None

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
