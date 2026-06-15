"""`pokemon app` — the long-lived PokeBench Local Control Center entrypoint.

Plan §P2 + decision D6. This is the persistent process that owns the emulator
and (eventually) the queue: it launches mGBA + the Lua connector ONCE via
:class:`AppSupervisor`, starts the existing FastAPI dashboard server long-lived,
opens the browser to ``/``, then idles serving until Ctrl-C. On shutdown it
tears the emulator down.

The control inversion vs ``pokemon run``: here the *server* is the long-lived
owner of the emulator, and runs are dispatched *into* it (the queue executor +
control routes land in P3/P4). For P2 the app just boots supervisor + server
and idles — there is no run executor yet.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import webbrowser
from pathlib import Path

from src.app.supervisor import AppSupervisor, SupervisorStatus
from src.cli.runner import prepare_config


class _FakeSupervisor:
    """A no-mGBA stand-in for :class:`AppSupervisor` (headless browser testing).

    Reports ``process_up`` + ``connected`` so the Home spectate pill renders
    "live" against seeded fixtures, but never launches a process or holds a real
    emulator handle. Used only by ``pokemon app --fake-emulator`` (Plan §P5
    headless serve mode) — NEVER on the live path.
    """

    def __init__(self) -> None:
        self._busy = False
        self.handle = None

    def start(self) -> dict:
        return {"fake": True}

    def set_busy(self, busy: bool) -> None:
        self._busy = bool(busy)

    def status(self) -> SupervisorStatus:
        return SupervisorStatus(process_up=True, connected=True, busy=self._busy)

    def restart(self) -> dict:
        return {"fake": True}

    def shutdown(self) -> None:
        pass


def _backfill_index_on_boot(run_index) -> int:
    """Populate an empty/missing run index from ``runs_root`` on a REAL boot.

    Plan §P7: the live leaderboard/history should not be empty just because the
    denormalized ``runs_index.json`` is missing or empty while ``local/runs`` has
    real run folders (e.g. first boot after upgrading from ``pokemon run``, or a
    deleted index). The index is fully rebuildable from a scan, so we re-derive it
    rather than serving an empty board. An existing NON-empty index is left
    untouched (``run_index.load()`` already populated it) — we never clobber it.

    Returns the number of runs backfilled (0 if the index was already non-empty).
    """
    if run_index.all():
        return 0
    entries = run_index.rebuild_from_scan()
    return len(entries)


def _seed_index(run_index, seed_path: Path) -> int:
    """Seed ``run_index`` from a path for headless serving.

    ``seed_path`` may be a ``runs_index.json`` file (a flat ``RunSummary`` list,
    loaded as-is) or a directory of run folders (scanned + projected). Returns
    the number of entries seeded. Errors are surfaced (a bad seed path should
    fail loudly in test setup, not silently serve an empty board).
    """
    if seed_path.is_file():
        # Point the index at the seed file and load it directly.
        run_index.index_path = seed_path
        return len(run_index.load())
    if seed_path.is_dir():
        run_index.runs_root = seed_path
        return len(run_index.rebuild_from_scan())
    raise SystemExit(f"ERROR: --seed-runs path not found: {seed_path}")


def _run_headless(args) -> None:
    """Boot the server with a FAKE supervisor + seeded index, NO mGBA.

    For browser-testing the SPA + control routes without the emulator. Wires the
    control plane (so /api/* serve), seeds the run index from ``--seed-runs`` (if
    given), and idles. The executor drain loop is NOT started — there is no real
    emulator to dispatch into; the queue is still mutable (enqueue/cancel/move
    just edit ``queue.json``), which is all the Home view needs to verify.
    """
    from src.app.executor import RunExecutor
    from src.app.queue_manager import QueueManager
    from src.app.run_index import RunIndex
    from src.dashboard import server as _dash_server

    app_dir = Path(os.environ.get("POKEBENCH_APP_DIR", "local/app"))
    runs_root = Path("local/runs")
    app_dir.mkdir(parents=True, exist_ok=True)

    queue_manager = QueueManager(app_dir / "queue.json")
    queue_manager.load()
    run_index = RunIndex(app_dir / "runs_index.json", runs_root)
    run_index.load()

    seed = args.seed_runs or os.environ.get("POKEBENCH_SEED_RUNS")
    if seed:
        n = _seed_index(run_index, Path(seed))
        print(f"Seeded run index with {n} run(s) from {seed}")

    supervisor = _FakeSupervisor()
    supervisor.start()
    executor = RunExecutor(
        supervisor=supervisor,
        queue_manager=queue_manager,
        run_index=run_index,
        runs_root=runs_root,
        saves_dir=app_dir / "saves",
    )
    _dash_server.configure_control_plane(
        queue_manager=queue_manager, executor=executor, run_index=run_index
    )

    _dash_server._start_server_if_needed(args.port)
    url = f"http://localhost:{args.port}/"
    print(f"Control center (FAKE emulator, no mGBA): {url}")
    if not args.no_browser:
        webbrowser.open(url)

    print("Headless serve — server up, fake emulator, queue editable. Ctrl-C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping headless serve.")


def _build_supervisor_config() -> dict:
    """Load the app's emulator config (latest config + a default model alias).

    The supervisor only needs the emulator/paths block to launch mGBA + Lua; the
    per-run model binding happens when the executor (P3) dispatches each run. We
    reuse ``prepare_config`` for a fully-formed config so the emulator section
    (rom_path, port) is populated identically to ``pokemon run``.
    """
    # A model alias is required by prepare_config's registry binding, but the
    # supervisor never runs the agent itself — it only owns the emulator. Use
    # the latest config (path=None) with a placeholder alias; the executor
    # rebinds the real model per run.
    return prepare_config(None, "claude-opus-4.7(medium)")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="pokemon app",
        description=(
            "Launch the PokeBench Local Control Center: a long-lived process "
            "that owns the emulator and serves the web UI. Boots mGBA + Lua "
            "once, starts the dashboard server, idles until Ctrl-C."
        ),
    )
    parser.add_argument(
        "--port", type=int, default=3420,
        help="Port for the control-center web server. Default: 3420.",
    )
    parser.add_argument(
        "--no-browser", action="store_true",
        help="Don't open a browser tab on boot.",
    )
    parser.add_argument(
        "--connect-timeout", type=float, default=300.0,
        help="Timeout (seconds) for the initial Lua connection. Default: 300.",
    )
    parser.add_argument(
        "--fake-emulator", action="store_true",
        help=(
            "Headless serve mode (Plan §P5): boot the server with a FAKE "
            "supervisor + seeded index and NO mGBA, for browser-testing the SPA. "
            "Also enabled via POKEBENCH_FAKE_EMULATOR=1."
        ),
    )
    parser.add_argument(
        "--seed-runs", default=None,
        help=(
            "Headless seed source: a runs_index.json file (flat RunSummary list) "
            "or a directory of run folders to scan. Also via POKEBENCH_SEED_RUNS."
        ),
    )
    args = parser.parse_args()

    if args.fake_emulator or os.environ.get("POKEBENCH_FAKE_EMULATOR") == "1":
        _run_headless(args)
        return

    config = _build_supervisor_config()

    rom_path = config["emulator"]["rom_path"]

    if not os.path.exists(rom_path):
        sys.exit(f"ERROR: ROM not found at {rom_path}")

    ts = time.strftime("%Y-%m-%d_%H-%M-%S")
    saves_dir = Path(f"local/app/_session_{ts}/saves")

    supervisor = AppSupervisor(
        config, saves_dir, connect_timeout=args.connect_timeout,
    )

    print("Starting PokeBench Local Control Center...")
    print("Launching emulator (mGBA + Lua connector)...")
    supervisor.start()
    print("Emulator connected. Starting web server...")

    # Start the existing FastAPI dashboard server long-lived (no run registered
    # yet — runs register into it as the executor dispatches them in P3). We
    # call the server's own bind helper rather than restructuring server.py
    # (SPA serving is P5).
    from src.dashboard import server as _dash_server

    _dash_server._start_server_if_needed(args.port)
    url = f"http://localhost:{args.port}/"
    print(f"Control center: {url}")

    if not args.no_browser:
        webbrowser.open(url)

    # Control plane (P3): construct the queue + index + executor, wire the
    # control routes to them, and start the executor's serial drain loop in a
    # background thread. Runs are dispatched INTO the persistent emulator one at
    # a time; the queue + history survive restarts via their JSON files.
    import threading

    from src.app.executor import RunExecutor
    from src.app.queue_manager import QueueManager
    from src.app.run_index import RunIndex

    app_dir = Path("local/app")
    runs_root = Path("local/runs")
    queue_manager = QueueManager(app_dir / "queue.json")
    run_index = RunIndex(app_dir / "runs_index.json", runs_root)
    run_index.load()
    # Backfill-on-boot (Plan §P7): if the index is missing/empty but real run
    # folders exist under runs_root, rebuild it so the leaderboard/history aren't
    # empty. No-op when the index already has entries.
    backfilled = _backfill_index_on_boot(run_index)
    if backfilled:
        print(f"Backfilled run index with {backfilled} run(s) from {runs_root}")
    executor = RunExecutor(
        supervisor=supervisor,
        queue_manager=queue_manager,
        run_index=run_index,
        runs_root=runs_root,
        saves_dir=saves_dir,
    )
    _dash_server.configure_control_plane(
        queue_manager=queue_manager, executor=executor, run_index=run_index
    )
    drain_thread = threading.Thread(
        target=executor.drain_loop, daemon=True, name="run-executor-drain",
    )
    drain_thread.start()

    print("Idle — emulator warm, server up, queue draining. Ctrl-C to shut down.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        executor.stop()
        supervisor.shutdown()
        print("Emulator shut down. Bye.")


if __name__ == "__main__":
    main()
