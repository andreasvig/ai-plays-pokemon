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
import sys
import time
import webbrowser
from pathlib import Path

from src.app.supervisor import AppSupervisor
from src.cli.runner import prepare_config


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
    args = parser.parse_args()

    config = _build_supervisor_config()

    rom_path = config["emulator"]["rom_path"]
    import os

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
