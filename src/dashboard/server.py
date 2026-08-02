"""FastAPI server for the control center.

Serves the Svelte SPA (the sole UI) plus the JSON/WS data surface. Supports N
concurrent runs via dynamic routing; each run registers a RunSession in the
RunRegistry, and the per-run data endpoints are namespaced under /runs/{run_id}.

URL layout:
    GET  /                              → the SPA (client-routed: /spectate,
                                          /history/<id>, …); falls back to a
                                          minimal active-runs list if unbuilt
    GET  /runs/{run_id}/api/state       → state JSON
    GET  /runs/{run_id}/api/config      → config JSON
    WS   /runs/{run_id}/ws/events       → event stream
    WS   /runs/{run_id}/ws/screen       → screen stream
    GET  /api/runs                      → JSON list of registered runs

The server is a process-singleton: first start_dashboard() call binds the
port; subsequent calls register additional runs into the same server.
"""

import asyncio
import json
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from src.app.trace_build import build_run_trace
from src.dashboard.event_bridge import EventBridge
from src.dashboard.screen_stream import ScreenStreamer


# ───────────────────────────── registry ─────────────────────────────


@dataclass
class RunSession:
    run_id: str                  # URL slug (typically run_dir basename)
    label: str                   # human-readable label for the index page
    config: dict
    bridge: EventBridge
    streamer: ScreenStreamer
    state_manager: Any
    run_dir: Path
    registered_at: float = field(default_factory=time.time)


class RunRegistry:
    def __init__(self) -> None:
        self._sessions: dict[str, RunSession] = {}
        self._lock = threading.Lock()

    def register(self, session: RunSession) -> None:
        with self._lock:
            if session.run_id in self._sessions:
                raise ValueError(f"run_id already registered: {session.run_id}")
            self._sessions[session.run_id] = session

    def unregister(self, run_id: str) -> None:
        with self._lock:
            session = self._sessions.pop(run_id, None)
        if session is not None:
            session.streamer.stop()

    def get(self, run_id: str) -> Optional[RunSession]:
        with self._lock:
            return self._sessions.get(run_id)

    def all(self) -> list[RunSession]:
        with self._lock:
            return list(self._sessions.values())


_REGISTRY = RunRegistry()
_SERVER_STARTED = False
_SERVER_LOCK = threading.Lock()
_SERVER_PORT: Optional[int] = None


# ───────────────────────────── app ─────────────────────────────


app = FastAPI(title="AI Plays Pokemon Dashboard")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
async def _on_startup() -> None:
    # Capture the server's event loop at boot so off-thread callers (the
    # executor drain thread) can broadcast control pings via the loop even
    # before any /api/ws/control client connects (Plan §P6).
    _capture_control_loop()


# The built Svelte SPA (Plan §P5). `npm run build` in src/dashboard/web emits
# here. The dir is gitignored + regenerated, so it may be absent (e.g. on the
# headless `pokemon run` path before a build) — every SPA helper degrades
# gracefully when it doesn't exist.
SPA_DIST_DIR = Path(__file__).parent / "web" / "dist"
SPA_INDEX = SPA_DIST_DIR / "index.html"


def _spa_built() -> bool:
    return SPA_INDEX.is_file()


def spa_freshness() -> str:
    """``'missing' | 'stale' | 'ok'`` for the built SPA bundle.

    ``stale`` = a frontend source file under ``web/src/`` is NEWER than the built
    ``dist/index.html`` — i.e. someone pulled new UI code but didn't re-run
    ``npm run build``, so the server would keep serving the OLD bundle. The
    ``pokemon app`` boot path warns on this so a stale bundle reads as itself, not
    as a broken feature. Best-effort: any stat error or a missing source tree
    (e.g. a packaged deploy) reports ``ok`` rather than nagging.
    """
    if not SPA_INDEX.is_file():
        return "missing"
    src_dir = SPA_DIST_DIR.parent / "src"
    if not src_dir.is_dir():
        return "ok"
    try:
        built = SPA_INDEX.stat().st_mtime
        newest_src = max(
            (p.stat().st_mtime for p in src_dir.rglob("*") if p.is_file()),
            default=0.0,
        )
    except OSError:
        return "ok"
    return "stale" if newest_src > built else "ok"


def _require_session(run_id: str) -> RunSession:
    session = _REGISTRY.get(run_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
    return session


@app.get("/")
async def index_page():
    """Serve the SPA when built (Plan §P5); else the legacy active-runs index.

    When ``web/dist/index.html`` exists (the ``pokemon app`` control center),
    ``/`` is the Svelte SPA home. When it's absent (the headless ``pokemon run``
    path that never builds the SPA), this falls back to the original
    list-of-registered-runs HTML — preserving legacy behaviour for that caller.
    """
    if _spa_built():
        return FileResponse(
            str(SPA_INDEX),
            media_type="text/html",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )
    sessions = sorted(_REGISTRY.all(), key=lambda s: s.registered_at)
    rows = []
    for s in sessions:
        cfg = s.config
        task = (cfg.get("task") or {}).get("goal", "—")
        llm = cfg.get("_llm_alias") or cfg.get("llm_model") or "—"
        rows.append(
            f'<tr><td><a href="/runs/{s.run_id}">{s.label}</a></td>'
            f'<td>{task}</td><td>{llm}</td></tr>'
        )
    body = (
        "<h1>AI Plays Pokemon — Active Runs</h1>"
        + (
            "<table><thead><tr><th>Run</th><th>Task</th><th>LLM</th></tr></thead>"
            "<tbody>" + "".join(rows) + "</tbody></table>"
            if rows
            else "<p>No runs registered.</p>"
        )
    )
    return HTMLResponse(
        f"<!doctype html><html><head><title>AI Plays Pokemon</title>"
        f"<style>body{{font-family:system-ui;max-width:720px;margin:40px auto;padding:0 16px}}"
        f"table{{border-collapse:collapse;width:100%}}"
        f"th,td{{padding:8px 12px;text-align:left;border-bottom:1px solid #eee}}"
        f"a{{color:#06c;text-decoration:none}}a:hover{{text-decoration:underline}}</style>"
        f"</head><body>{body}</body></html>"
    )


def _recording_path(run_id: str) -> Optional[Path]:
    """``<run_dir>/recording.mp4`` if this run has a usable video, else None."""
    executor = _CONTROL.get("executor")
    if executor is None or not run_id:
        return None
    p = Path(executor.runs_root) / run_id / "recording.mp4"
    try:
        return p if p.is_file() and p.stat().st_size > 0 else None
    except OSError:
        return None


def _with_recording(row: dict) -> dict:
    """Stamp ``has_recording`` onto a serialised RunSummary.

    Derived from disk on every request rather than stored on RunSummary. The
    index is a projection written once when a run finishes, so a persisted flag
    would be wrong for every run recorded before the field existed, and wrong
    again the moment someone deletes an mp4 to reclaim space. One `stat` per row
    is far cheaper than an index migration that can still go stale.
    """
    row["has_recording"] = _recording_path(row.get("run_id") or "") is not None
    return row


@app.get("/api/runs")
async def api_runs(
    kind: str | None = None,
    status: str | None = None,
    benchmark: str | None = None,
    q: str | None = None,
    sort: str = "recent",
    order: str = "desc",
):
    """Run list.

    When the control plane is configured (``pokemon app``), this is the HISTORY
    view: filtered + sorted flat RunSummary projections from the index (Plan §P4).
    When unconfigured (the headless ``pokemon run`` path), it falls back to the
    legacy listing of LIVE-registered runs — backward compatible for callers that
    predate the control plane.
    """
    index = _CONTROL["index"]
    if index is not None:
        from src.app import derivations
        from src.app.models import RunKind, RunStatus

        kind_enum = None
        if kind:
            try:
                kind_enum = RunKind(kind)
            except ValueError:
                raise HTTPException(status_code=400, detail=f"invalid kind: {kind!r}")
        status_enum = None
        if status:
            try:
                status_enum = RunStatus(status)
            except ValueError:
                raise HTTPException(
                    status_code=400, detail=f"invalid status: {status!r}"
                )

        rows = derivations.history(
            index.all(),
            kind=kind_enum,
            status=status_enum,
            benchmark=benchmark,
            q=q,
            sort=sort,
            order=order,
        )
        return JSONResponse([_with_recording(s.model_dump(mode="json")) for s in rows])

    sessions = sorted(_REGISTRY.all(), key=lambda s: s.registered_at)
    return JSONResponse([
        {
            "run_id": s.run_id,
            "label": s.label,
            "url": f"/runs/{s.run_id}",
        }
        for s in sessions
    ])


@app.get("/runs/{run_id}/api/state")
async def get_state(run_id: str):
    s = _require_session(run_id)
    return JSONResponse(s.state_manager.get_truncated_view() if s.state_manager else {})


def _referee_payload(config: dict) -> Optional[dict]:
    """Build the referee ladder block for the config payload, or None.

    Reads the run's ``referee`` config block (``{checkpoints: <path>,
    enforce: bool}``), loads the ladder, and returns
    ``{"enforce": bool, "ladder": [{id, name, deadline_turn, group?}, ...]}`` in
    ladder order. Multigate members are flattened into the ladder, each tagged
    with its ``group`` (the group's display name) and given the group's FINAL
    deadline as ``deadline_turn`` so the live HUD countdown works; the exact
    per-completion pacing is enforced by the referee and shown in the report.
    Robust by design: a missing block, missing/unreadable checkpoints file, or
    any load error returns ``None`` (the key is then omitted) — it must never
    500 the config endpoint for non-benchmark runs.
    """
    referee_cfg = config.get("referee")
    if not isinstance(referee_cfg, dict):
        return None
    checkpoints_path = referee_cfg.get("checkpoints")
    if not checkpoints_path:
        return None
    try:
        from src.referee.checkpoints import MultiGate, load_ladder

        nodes = load_ladder(checkpoints_path).nodes
    except Exception as exc:  # missing file, parse/validation error, etc.
        print(f"  ⚠ dashboard: could not load referee ladder {checkpoints_path!r}: {exc}")
        return None

    ladder: list[dict] = []
    for node in nodes:
        if isinstance(node, MultiGate):
            final_deadline = next(
                (d for d in reversed(node.deadline_turns) if d is not None), None
            )
            for member in node.gates:
                ladder.append(
                    {
                        "id": member.id,
                        "name": member.name,
                        "deadline_turn": final_deadline,
                        "group": node.name,
                    }
                )
        else:
            ladder.append(
                {"id": node.id, "name": node.name, "deadline_turn": node.deadline_turn}
            )

    return {
        "enforce": bool(referee_cfg.get("enforce", False)),
        "ladder": ladder,
    }


@app.get("/runs/{run_id}/api/config")
async def get_config(run_id: str):
    s = _require_session(run_id)
    payload = {
        "run_id": s.run_id,
        "label": s.label,
        "task": s.config.get("task", {}).get("goal", ""),
        "llm_model": s.config.get("_llm_alias") or s.config.get("llm_model", ""),
        # Whether this run has a TaskMaster above the Player. The UI keys its
        # layout on this rather than on "has a task arrived yet?", which is also
        # false during the first turns of a TaskMaster run.
        "task_master": bool((s.config.get("task_master") or {}).get("enabled", False)),
    }
    referee = _referee_payload(s.config)
    if referee is not None:
        payload["referee"] = referee
    return JSONResponse(payload)


@app.websocket("/runs/{run_id}/ws/events")
async def ws_events(websocket: WebSocket, run_id: str):
    session = _REGISTRY.get(run_id)
    if session is None:
        await websocket.close(code=1008)
        return
    await websocket.accept()

    cursor = 0

    if session.state_manager:
        try:
            await websocket.send_text(json.dumps({
                "type": "state_update",
                "data": session.state_manager.get_truncated_view(),
            }, default=str))
        except Exception:
            return

    try:
        await websocket.send_text(json.dumps({"type": "stats", "data": session.bridge.get_stats()}))
    except Exception:
        return

    try:
        while True:
            if _REGISTRY.get(run_id) is None:
                await websocket.close(code=1008)
                return
            events, cursor = session.bridge.get_events_since(cursor)
            for event in events:
                await websocket.send_text(json.dumps({"type": "event", "data": event}, default=str))

                if event.get("type") == "state_change" and session.state_manager:
                    await websocket.send_text(json.dumps({
                        "type": "state_update",
                        "data": session.state_manager.get_truncated_view(),
                    }, default=str))

                if event.get("type") in ("turn_usage", "turn_start"):
                    await websocket.send_text(json.dumps({
                        "type": "stats",
                        "data": session.bridge.get_stats(),
                    }))

            if not events:
                await asyncio.sleep(0.05)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass


@app.websocket("/runs/{run_id}/ws/screen")
async def ws_screen(websocket: WebSocket, run_id: str):
    session = _REGISTRY.get(run_id)
    if session is None:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    last_frame = None
    try:
        while True:
            if _REGISTRY.get(run_id) is None:
                await websocket.close(code=1008)
                return
            frame = session.streamer.get_frame()
            if frame is not None and frame is not last_frame:
                await websocket.send_bytes(frame)
                last_frame = frame
            await asyncio.sleep(0.033)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass


# ───────────────────────────── lifecycle ─────────────────────────────


def _start_server_if_needed(port: int) -> None:
    """Bind the FastAPI server to `port` exactly once per process."""
    global _SERVER_STARTED, _SERVER_PORT
    with _SERVER_LOCK:
        if _SERVER_STARTED:
            if _SERVER_PORT != port:
                print(
                    f"  ⚠ dashboard already started on :{_SERVER_PORT}; "
                    f"ignoring requested port :{port}"
                )
            return

        import uvicorn

        uvi_config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        server = uvicorn.Server(uvi_config)
        thread = threading.Thread(target=server.run, daemon=True, name="dashboard-server")
        thread.start()
        _SERVER_STARTED = True
        _SERVER_PORT = port
        # Brief wait so the port is bound before callers open URLs
        time.sleep(0.8)


def start_dashboard(
    logger,
    state_manager,
    config: dict[str, Any],
    *,
    port: Optional[int] = None,
    open_browser: bool = True,
) -> RunSession:
    """Register a run with the dashboard and start the server if not already running.

    Returns the RunSession. The server binds on the first call's port;
    subsequent calls only register a new run.

    `port` defaults to config["dashboard"]["port"] or 3420.
    """
    dash_cfg = config.get("dashboard") or {}
    bind_port = port if port is not None else int(dash_cfg.get("port", 3420))

    run_dir = Path(logger.run_dir)
    run_id = run_dir.name
    label = config.get("run_label") or config.get("_llm_alias") or run_id

    stream_path = (
        (config.get("paths") or {}).get("stream")
        or str(run_dir / "mgba_stream.png")
    )

    bridge = EventBridge()
    logger.add_listener(bridge.on_event)
    streamer = ScreenStreamer(stream_path=stream_path)
    streamer.start()

    session = RunSession(
        run_id=run_id,
        label=label,
        config=config,
        bridge=bridge,
        streamer=streamer,
        state_manager=state_manager,
        run_dir=run_dir,
    )
    _REGISTRY.register(session)

    _start_server_if_needed(bind_port)

    # The control center (SPA) is the UI: the live run is at /spectate, finished
    # runs at /history/<run_id>. `open_browser` is accepted for caller
    # compatibility but never opens a tab — `pokemon app` opens the SPA itself at
    # boot (`cli/app.py`); the old per-run livestream dashboard was retired.
    _ = open_browser
    base = f"http://localhost:{_SERVER_PORT}"
    print(f"  Dashboard (live): {base}/spectate")
    print(f"  Run detail: {base}/history/{run_id}")
    return session


def unregister_run(run_id: str) -> None:
    """Detach a run from the dashboard registry (stops its streamer)."""
    _REGISTRY.unregister(run_id)


def get_registry() -> RunRegistry:
    """Expose the process-singleton registry (used by sequential orchestrator)."""
    return _REGISTRY


def get_server_port() -> Optional[int]:
    """The bound port, or None if the server has not started in this process.

    The recorder needs it to point its own headless browser at us; it is the
    only thing that distinguishes "there is a UI to record" from "this is a
    headless run".
    """
    return _SERVER_PORT if _SERVER_STARTED else None


# ───────────────────────── control plane (Plan §P3) ─────────────────────────
#
# The control routes below are ADDITIVE — they don't touch the existing run-
# scoped routes / spectate streams / RunRegistry above. They're backed by a
# QueueManager + RunExecutor + RunIndex injected at app boot via
# `configure_control_plane(...)`. Until configured they 503 (the app wires them
# in `pokemon app`; the per-run `pokemon run` path leaves them unconfigured).

_CONTROL: dict[str, Any] = {"queue": None, "executor": None, "index": None}


# ───────────────────────── control broadcast hub (Plan §P6) ─────────────────
#
# A tiny in-process pub/sub so the SPA's Home view is "live live" (locked #7) —
# NOT polling, NOT a granular delta protocol. State changes (a run starting /
# finishing, the next item auto-dequeuing, a leaderboard row landing, a queue
# edit) call `notify_control()`, which broadcasts a small blob
# `{type:"control", active, queue_len, leaderboard_dirty}` to every WS subscribed
# to `/api/ws/control`. The client refetches /api/queue + /api/leaderboard on
# receipt. Decoupled from the executor/queue internals: callers just ping.

_CONTROL_SUBS: "set[asyncio.Queue]" = set()
_CONTROL_LOOP: Optional[asyncio.AbstractEventLoop] = None
_CONTROL_SUBS_LOCK = threading.Lock()


def _capture_control_loop() -> None:
    """Remember the server's event loop so off-thread callers can broadcast.

    `notify_control()` is called from the executor's drain thread (and route
    handlers run on the loop). To push onto async subscriber queues from another
    thread we hop back onto the captured loop via `call_soon_threadsafe`.
    """
    global _CONTROL_LOOP
    try:
        _CONTROL_LOOP = asyncio.get_running_loop()
    except RuntimeError:
        _CONTROL_LOOP = None


def _control_blob() -> dict:
    """Build the minimal state-changed blob the control WS pushes."""
    queue = _CONTROL["queue"]
    index = _CONTROL["index"]
    active = getattr(queue, "active", None) if queue is not None else None
    try:
        queue_len = len(queue.items) if queue is not None else 0
    except Exception:
        queue_len = 0
    # leaderboard_dirty is a coarse "go refetch" flag; we don't diff rows here.
    leaderboard_dirty = index is not None
    return {
        "type": "control",
        "active": active,
        "queue_len": queue_len,
        "leaderboard_dirty": leaderboard_dirty,
    }


def _broadcast_control_blob(blob: dict) -> None:
    """Push `blob` onto every subscriber queue (must run on the server loop)."""
    with _CONTROL_SUBS_LOCK:
        subs = list(_CONTROL_SUBS)
    for q in subs:
        try:
            q.put_nowait(blob)
        except Exception:
            pass


def notify_control() -> None:
    """Broadcast a state-changed blob to all `/api/ws/control` subscribers.

    Safe to call from any thread (the executor drain thread, queue mutations, or
    a route handler). No-op when nothing is subscribed or the loop isn't up yet.
    """
    blob = _control_blob()
    loop = _CONTROL_LOOP
    if loop is None or loop.is_closed():
        # No running server loop captured — drop the ping (a fresh connect will
        # refetch current state anyway).
        return
    try:
        loop.call_soon_threadsafe(_broadcast_control_blob, blob)
    except RuntimeError:
        pass


@app.websocket("/api/ws/control")
async def ws_control(websocket: WebSocket):
    """Live-home control channel: pushes a small blob on every state change.

    Sends one blob immediately on connect (so the client syncs without a race),
    then one per `notify_control()`. The client refetches /api/queue +
    /api/leaderboard on each message — minimal push, refetch-on-ping (locked #7).
    """
    await websocket.accept()
    _capture_control_loop()
    sub: asyncio.Queue = asyncio.Queue()
    with _CONTROL_SUBS_LOCK:
        _CONTROL_SUBS.add(sub)
    try:
        # Immediate sync blob on connect.
        await websocket.send_text(json.dumps(_control_blob()))
        while True:
            blob = await sub.get()
            await websocket.send_text(json.dumps(blob))
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        with _CONTROL_SUBS_LOCK:
            _CONTROL_SUBS.discard(sub)


def configure_control_plane(*, queue_manager, executor, run_index) -> None:
    """Inject the control-plane collaborators (called once at app boot, P3)."""
    _CONTROL["queue"] = queue_manager
    _CONTROL["executor"] = executor
    _CONTROL["index"] = run_index


def _require_control() -> tuple[Any, Any, Any]:
    queue = _CONTROL["queue"]
    executor = _CONTROL["executor"]
    index = _CONTROL["index"]
    if queue is None or executor is None or index is None:
        raise HTTPException(status_code=503, detail="control plane not configured")
    return queue, executor, index


def _validate_model_alias(model: str) -> None:
    """Reject a model that's neither a known ``model(level)`` selection nor a raw id.

    The message names the CLOSEST few selections rather than all of them. The
    registry is past 160 ``model(level)`` pairs, and a wall that long is read as
    noise — the useful content is "did you mean this one", plus where the full
    list lives.
    """
    import difflib

    from src.config import (
        _load_models_registry,
        is_valid_model_selection,
        list_competitor_aliases,
    )

    registry = _load_models_registry()
    if is_valid_model_selection(model, registry):
        return
    known = list_competitor_aliases(registry)
    if not known:
        raise HTTPException(status_code=400, detail=f"unknown model {model!r} (registry empty)")
    # Match on the whole selection first, then on the bare model name, so both
    # "gpt-5.6-sil(medium)" (typo'd model) and "gpt-5.6-sol(mdium)" (typo'd
    # level) land on something useful.
    base = model.split("(")[0].strip()
    close = difflib.get_close_matches(model, known, n=4, cutoff=0.6)
    if not close:
        close = [k for k in known if k.split("(")[0] == base][:6]
    if not close:
        close = difflib.get_close_matches(base, [k.split("(")[0] for k in known], n=3, cutoff=0.4)
    suffix = f"; did you mean: {', '.join(close)}" if close else ""
    raise HTTPException(
        status_code=400,
        detail=(
            f"unknown model {model!r}{suffix}. "
            f"{len(known)} selections known — `pokemon ls models <substring>` lists them."
        ),
    )


def _validate_benchmark_id(benchmark: Any) -> str | None:
    """Return a known benchmark id, or None (→ executor default).

    ``None`` is allowed (the executor falls back to the registry default).
    A non-empty value must match a registry id, else 400.
    """
    if benchmark is None:
        return None
    if not isinstance(benchmark, str) or not benchmark:
        raise HTTPException(status_code=400, detail="benchmark must be a string id")
    from src.app.benchmarks import load_benchmarks

    known = {b.id for b in load_benchmarks()}
    if benchmark not in known:
        raise HTTPException(
            status_code=400,
            detail=f"unknown benchmark {benchmark!r}; known: {', '.join(sorted(known))}",
        )
    return benchmark


def _validate_stop_at(stop_at: Any) -> str | None:
    """Return a known stop-event id, or None. 400 on anything unrecognised.

    Unlike the benchmark id (which falls back to the registry default so a stale
    queue item still runs), an unknown stop event is rejected outright: falling
    back would silently give you a run with no early exit, and you'd only find
    out at the turn cap.
    """
    from src.app.catalog import validate_stop_event

    try:
        return validate_stop_event(stop_at)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


def _validate_max_spend(raw: Any) -> float | None:
    """Return a positive USD ceiling, or None for unbounded. 400 otherwise.

    Rejected rather than coerced, on the same reasoning as ``_validate_stop_at``:
    a budget you asked for and silently didn't get is only discovered by the
    bill. ``0`` is refused too — a zero budget can only produce an empty run, so
    it is a typo. Bools are ints in Python, hence the explicit exclusion.
    """
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise HTTPException(
            status_code=400,
            detail=f"max_spend_usd must be a number of USD, got {raw!r}",
        )
    if raw <= 0:
        raise HTTPException(
            status_code=400,
            detail=f"max_spend_usd must be greater than 0, got {raw!r}",
        )
    return float(raw)


def _validate_gameplay(raw: Any) -> str | None:
    """Return a known casual playstyle, or None (= exploration). 400 otherwise.

    Rejected rather than defaulted, for the same reason as ``_validate_stop_at``:
    a typo'd playstyle that silently fell back to exploration would produce a run
    that looks right in the queue and plays the other way, and you would only
    find out by reading the agent's prompt.
    """
    from src.app.executor import RunExecutor

    if raw is None or raw == "":
        return None
    if not isinstance(raw, str) or raw.lower() not in RunExecutor.GAMEPLAY_MODES:
        known = ", ".join(sorted(RunExecutor.GAMEPLAY_MODES))
        raise HTTPException(
            status_code=400,
            detail=f"unknown gameplay {raw!r}; known: {known}",
        )
    return raw.lower()


def _validate_rom(rom: Any) -> str | None:
    """Return a known ROM id, or None (→ the registry default). 400 otherwise.

    Rejected outright rather than falling back, for the same reason as the stop
    event: silently running the wrong GAME is not a lesser failure than refusing.
    Also refuses a ROM whose file is missing — ``roms/`` is gitignored, so a
    registry entry with no dump behind it is a normal state, and finding out at
    dispatch (after mGBA fails to boot) is far worse than finding out here.
    """
    from src.app.roms import get_rom, validate_rom

    try:
        rom_id = validate_rom(rom)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if rom_id is not None and not get_rom(rom_id).exists():
        raise HTTPException(
            status_code=400,
            detail=f"rom {rom_id!r} is registered but its file is not on disk",
        )
    return rom_id


def _validate_config_stem(config: Any, *, is_continue: bool) -> str | None:
    """Return a config stem for a casual run: the request's, or the latest.

    Three cases, and the reason each is what it is:

    - **A continue** carries no config of its own — ``continue_from_run`` reads
      it off the source run — so ``None`` passes straight through.
    - **Absent** defaults to the newest ``config-X.Y``, matching what a bare
      ``pokemon run`` already does (``src.config.find_latest_config``). It used
      to pass ``None`` through, and the executor's ``_resolve_config_path`` then
      raised ``"casual run requires a config"`` at DISPATCH — after the item had
      been dequeued, where the only trace was a traceback on the app's stdout.
      The item vanished and the caller saw a 201. The UI always sent a config,
      so only the API and ``pokemon queue add`` (which omits the key unless
      ``--config`` is passed) could hit it.
    - **Unknown** is a 400 here rather than the same silent drop at dispatch.
    """
    if is_continue:
        return None
    from src.app.catalog import list_configs

    known = list_configs()
    if config is None:
        if not known:
            raise HTTPException(
                status_code=400,
                detail="no configs found in configs/ (expected config-X.Y.yaml)",
            )
        return known[-1]  # list_configs is version-sorted; last = latest
    if not isinstance(config, str) or not config:
        raise HTTPException(status_code=400, detail="config must be a stem like 'config-4.0'")
    # Accept a path/filename unchanged (the executor is idempotent for those);
    # only a bare stem is checked against the registry.
    if "/" in config or config.endswith(".yaml"):
        return config
    if config not in known:
        raise HTTPException(
            status_code=400,
            detail=f"unknown config {config!r}; known: {', '.join(known)}",
        )
    return config


@app.get("/api/queue")
async def api_queue_get():
    """`{active, items, last_error}` — the serial queue + the last dispatch failure.

    ``last_error`` is ``None`` in the normal case. It is set when an item was
    dequeued and then failed before its run could start (a bad config, a ROM
    that won't load, a recorder that won't boot). Without it that failure is
    invisible: ``drain_loop`` catches everything so one poisoned item can't
    freeze the queue, which also means the item simply disappears and the queue
    looks idle. See ``RunExecutor.last_error``.
    """
    queue, executor, _index = _require_control()
    return JSONResponse(
        {
            "active": queue.active,
            "items": [it.model_dump(mode="json") for it in queue.items],
            "last_error": getattr(executor, "last_error", None),
        }
    )


def _enqueue_kwargs(spec: dict) -> dict:
    """Validate one enqueue ``spec`` → kwargs for ``QueueManager.enqueue``.

    Raises ``HTTPException(400)`` on a bad kind / missing-or-unknown model /
    unknown benchmark / unknown stop event. Official (locked #4/#7) FORCES the
    frozen config + no max-turns (request config/max_turns/stop_at/max_spend_usd/
    gameplay ignored — a benchmark ends at its own ladder and always races) and
    takes only the request's ``benchmark``; casual keeps the request's
    config/max_turns/stop_at/max_spend_usd/gameplay/rom/continue_from.
    Pure validation — no enqueue, no side effects — so the single and batch
    routes share exactly the same rules.
    """
    from src.app.models import RunKind

    raw_kind = spec.get("kind")
    try:
        kind = RunKind(raw_kind)
    except Exception:
        raise HTTPException(status_code=400, detail=f"invalid kind: {raw_kind!r}")

    model = spec.get("model")
    if not model or not isinstance(model, str):
        raise HTTPException(status_code=400, detail="model is required")
    _validate_model_alias(model)

    record = _validate_record(spec.get("record"))

    if kind == RunKind.official:
        benchmark = _validate_benchmark_id(spec.get("benchmark"))
        return {
            "kind": kind, "model": model, "config": None,
            "benchmark": benchmark, "max_turns": None, "record": record,
        }
    stop_at = _validate_stop_at(spec.get("stop_at"))
    # Which game. Casual-only: an official run's ROM is the benchmark ladder's,
    # so a request that sets one on an official run has it dropped above rather
    # than honoured.
    rom_id = _validate_rom(spec.get("rom"))
    if stop_at and rom_id is not None:
        # Stop events are gates on a FireRed ladder, addressed at FireRed's RAM
        # map. On another cartridge those reads land on unrelated memory, so the
        # event would never fire (or worse, fire on noise) and the run would
        # quietly become a plain turn-capped one.
        from src.app.roms import get_rom, rom_supports_benchmarks

        rom = get_rom(rom_id)
        if not rom_supports_benchmarks(rom):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"stop events are not available for {rom.name} — no gate "
                    f"ladder is authored for {rom.game}"
                ),
            )
    return {
        "kind": kind,
        "model": model,
        # Defaulted to the latest config when absent, 400 when unknown — either
        # way the item that reaches the queue is dispatchable. See
        # :func:`_validate_config_stem`.
        "config": _validate_config_stem(
            spec.get("config"), is_continue=bool(spec.get("continue_from"))
        ),
        "max_turns": spec.get("max_turns"),
        "stop_at": stop_at,
        "max_spend_usd": _validate_max_spend(spec.get("max_spend_usd")),
        "gameplay": _validate_gameplay(spec.get("gameplay")),
        "rom": rom_id,
        "continue_from": spec.get("continue_from"),
        "record": record,
    }


def _validate_record(raw: Any) -> dict | None:
    """Validate an optional record spec → a plain dict, or None for no recording.

    Rejects an unavailable recorder up front (400) instead of enqueuing a run
    that would silently produce no video: the caller asked for a recording, and
    finding out 200 turns later that ffmpeg was missing is the worst outcome.
    """
    from src.dashboard.recorder import normalize_spec, recorder_preflight

    try:
        spec = normalize_spec(raw)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if spec is None:
        return None
    blocked = recorder_preflight()
    if blocked:
        raise HTTPException(status_code=400, detail=f"recording unavailable: {blocked}")
    return spec


@app.post("/api/queue")
async def api_queue_post(spec: dict):
    """Enqueue a single QueuedRun spec (validation per :func:`_enqueue_kwargs`)."""
    queue, _executor, _index = _require_control()
    kwargs = _enqueue_kwargs(spec)  # validate (may 400) BEFORE touching the queue
    item = queue.enqueue(**kwargs)
    notify_control()
    return JSONResponse(item.model_dump(mode="json"), status_code=201)


@app.post("/api/queue/batch")
async def api_queue_batch(body: dict):
    """Enqueue many specs at once — ``{items: [spec, ...]}`` → ``{items: [...]}``.

    All-or-nothing: EVERY spec is validated (same rules as the single route)
    before any is enqueued, so one bad model/benchmark in the batch rejects the
    whole request (400) and leaves the queue untouched. Order is preserved.
    Backs ``pokemon queue add`` with its list intake.
    """
    queue, _executor, _index = _require_control()
    specs = body.get("items")
    if not isinstance(specs, list) or not specs:
        raise HTTPException(status_code=400, detail="items must be a non-empty list of specs")
    kwargs_list = [_enqueue_kwargs(s) for s in specs]  # validate ALL first
    created = [queue.enqueue(**kw) for kw in kwargs_list]
    notify_control()
    return JSONResponse(
        {"items": [it.model_dump(mode="json") for it in created]}, status_code=201
    )


@app.post("/api/queue/reorder")
async def api_queue_reorder(body: dict):
    """Set the whole queue order — ``{order: [queue_id, ...]}``.

    ``order`` must be a permutation of the current item ids (no missing, no
    unknown, no dupes); anything else is a 400. Atomic — one write, no partial
    reorder. Backs ``pokemon queue reorder``.
    """
    queue, _executor, _index = _require_control()
    order = body.get("order")
    if not isinstance(order, list) or not all(isinstance(x, str) for x in order):
        raise HTTPException(status_code=400, detail="order must be a list of queue_ids")
    try:
        queue.reorder(order)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    notify_control()
    return JSONResponse(
        {"active": queue.active, "items": [it.model_dump(mode="json") for it in queue.items]}
    )


@app.delete("/api/queue/{queue_id}")
async def api_queue_delete(queue_id: str):
    """Cancel a queued item by id."""
    queue, _executor, _index = _require_control()
    removed = queue.cancel(queue_id)
    if not removed:
        raise HTTPException(status_code=404, detail=f"queue_id not found: {queue_id}")
    notify_control()
    return JSONResponse({"cancelled": queue_id})


@app.post("/api/queue/{queue_id}/move")
async def api_queue_move(queue_id: str, body: dict):
    """Reorder ``queue_id`` to ``{to_index}``."""
    queue, _executor, _index = _require_control()
    to_index = body.get("to_index")
    if not isinstance(to_index, int):
        raise HTTPException(status_code=400, detail="to_index (int) is required")
    try:
        queue.move(queue_id, to_index)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"queue_id not found: {queue_id}")
    notify_control()
    return JSONResponse(
        {"active": queue.active, "items": [it.model_dump(mode="json") for it in queue.items]}
    )


@app.post("/api/runs/{run_id}/stop")
async def api_run_stop(run_id: str):
    """Stop the active run gracefully → status ``cancelled`` (locked #9).

    An official run stopped this way is VOIDED (never leaderboard-eligible).
    The executor records the verdict; the graceful savepoint is taken by the
    run loop when interrupted.
    """
    _queue, executor, _index = _require_control()
    matched = executor.request_stop(run_id)
    notify_control()
    return JSONResponse({"stopping": run_id, "matched": bool(matched)})


@app.post("/api/runs/{run_id}/continue")
async def api_run_continue(run_id: str, body: dict | None = None):
    """Build a continue spec and enqueue it.

    Resolves the latest savepoint, sets ``continue_from``, and INHERITS the
    source's kind: an official run continues official on the SAME benchmark (so a
    run stopped overnight can be finished + scored); a casual run continues casual.

    CASUAL continues may override the models via the request body:
    ``player_model`` and ``task_master_model`` (both ``model(level)`` aliases).
    Omitted → the source's models are reused. OFFICIAL continues are model-locked
    (locked #10): passing an override for an official source is a 400.
    """
    from src.app.models import RunKind

    queue, executor, _index = _require_control()
    body = body or {}
    player_model = body.get("player_model")
    task_master_model = body.get("task_master_model")
    if player_model is not None:
        _validate_model_alias(player_model)
    if task_master_model is not None:
        _validate_model_alias(task_master_model)
    try:
        spec = executor.build_continue_spec(
            run_id,
            player_model=player_model,
            task_master_model=task_master_model,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # Official continues are model-locked: reject (don't silently drop) overrides.
    if spec["kind"] == RunKind.official and (player_model or task_master_model):
        raise HTTPException(
            status_code=400,
            detail="cannot change models on an official continue — the resumed "
            "benchmark segment must reuse the source run's models to stay comparable",
        )
    item = queue.enqueue(
        kind=spec["kind"],
        model=spec["model"],
        config=spec.get("config"),
        benchmark=spec.get("benchmark"),
        max_turns=body.get("max_turns"),
        # Per-segment, like max_turns: the continue picks its own stop event
        # rather than inheriting the source run's. Ignored for an official
        # continue (the executor's official branch never reads it).
        stop_at=_validate_stop_at(body.get("stop_at")),
        # Also per-segment: the budget bounds the continue you are launching,
        # not the lineage. An official continue never reads it.
        max_spend_usd=_validate_max_spend(body.get("max_spend_usd")),
        # Per-segment as well — a continue may deliberately switch playstyle.
        gameplay=_validate_gameplay(body.get("gameplay")),
        continue_from=spec["continue_from"],
        task_master_model=spec.get("task_master_model"),
        # A continue is a fresh run with its own run dir, so it gets its own
        # video — the source run's recording setting is NOT inherited (you may
        # well be continuing precisely because you now want it recorded).
        record=_validate_record(body.get("record")),
    )
    notify_control()
    return JSONResponse(item.model_dump(mode="json"), status_code=201)


@app.post("/api/emulator/mute")
async def api_emulator_mute(body: dict):
    """Mute/unmute the emulator audio — ``{mute: bool}`` → ``{muted: bool}``.

    Best-effort: drives mGBA's native Mute toggle via the executor/supervisor and
    returns the resulting state. ``mute`` defaults to True. Pings ``notify_control``
    so the other UI surface (Home / Spectate) reflects the change.
    """
    _queue, executor, _index = _require_control()
    mute = bool(body.get("mute", True))
    muted = executor.set_mute(mute)
    notify_control()
    return JSONResponse({"muted": muted})


@app.post("/api/emulator/rom")
async def api_emulator_rom(body: dict):
    """Load a different game — ``{rom: "<id>"}`` → 202 ``{switching_to, rom}``.

    mGBA has to be relaunched to change cartridge (no Lua binding for it), and
    the Lua script then has to be re-loaded by hand, so this can block for as
    long as it takes a human to click through the Scripting window. It therefore
    runs on a background thread and returns immediately; the UI watches
    ``/api/emulator/status`` for ``connected`` to come back true.

    409 while a run is executing — the switch would kill it. Already-loaded is a
    200 no-op rather than an error, so the button is idempotent.
    """
    _queue, executor, _index = _require_control()
    supervisor = getattr(executor, "supervisor", None)
    if supervisor is None or not hasattr(supervisor, "switch_rom"):
        raise HTTPException(status_code=503, detail="no supervised emulator")

    rom_id = _validate_rom(body.get("rom"))
    from src.app.roms import get_rom

    rom = get_rom(rom_id)

    if supervisor.status().busy:
        raise HTTPException(
            status_code=409,
            detail="a run is executing — stop it before switching game",
        )
    if str(getattr(supervisor, "rom_path", "")) == rom.path:
        return JSONResponse({"switching_to": None, "rom": rom.id})

    def _switch():
        try:
            supervisor.switch_rom(rom.path)
        except Exception as exc:  # noqa: BLE001 — surfaced via status, not raised
            print(f"  ROM switch to {rom.id} failed: {exc}")
        notify_control()

    threading.Thread(target=_switch, name=f"rom-switch-{rom.id}", daemon=True).start()
    notify_control()
    return JSONResponse({"switching_to": rom.id, "rom": rom.id}, status_code=202)


@app.delete("/api/runs/{run_id}")
async def api_run_delete(run_id: str):
    """Delete a HISTORICAL run: move its folder to the Trash + drop the index entry.

    Refuses the currently-running run (409) — stop it first. The folder is moved
    to ``~/.Trash`` (recoverable) rather than hard-deleted; a name clash there is
    de-duped with a numeric suffix. Then the in-memory index entry is removed and
    ``leaderboard_dirty`` is broadcast so open clients refetch. 404 if the run is
    neither indexed nor on disk. Backs ``pokemon runs delete``.
    """
    _queue, executor, index = _require_control()

    if getattr(executor, "_active_run_id", None) == run_id:
        raise HTTPException(status_code=409, detail=f"{run_id} is the active run; stop it first")

    run_dir = Path(executor.runs_root) / run_id
    indexed = index.get(run_id) is not None
    if not indexed and not run_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")

    trashed_to: str | None = None
    if run_dir.is_dir():
        trash_root = Path.home() / ".Trash"
        try:
            trash_root.mkdir(parents=True, exist_ok=True)
            dest = trash_root / run_dir.name
            n = 1
            while dest.exists():
                dest = trash_root / f"{run_dir.name} ({n})"
                n += 1
            shutil.move(str(run_dir), str(dest))
            trashed_to = str(dest)
        except Exception as exc:
            # Don't de-index a run whose folder we failed to move — keep them consistent.
            raise HTTPException(status_code=500, detail=f"could not trash run dir: {exc}")

    removed = index.remove(run_id)
    notify_control()
    return JSONResponse({"deleted": run_id, "trashed_to": trashed_to, "deindexed": removed})


# ───────────────────────── read routes (Plan §P4) ─────────────────────────
#
# ADDITIVE read surface backing the SPA's leaderboard / history / report /
# models / configs / emulator-status. Same style as the P3 control routes:
# backed by the injected _CONTROL (queue/executor/index); 503 until configured —
# EXCEPT /api/emulator/status, which returns an idle "configured: false" payload
# rather than erroring when the control plane isn't wired (a useful Home signal
# even on the headless `pokemon run` path).


@app.get("/api/benchmarks")
async def api_benchmarks():
    """The benchmark registry — ``[{id, name, goal, ladder, default}, ...]``.

    Backs the new-run dialog's benchmark picker and the main-page benchmark
    filter. Pure projection of ``configs/benchmarks.yaml``; no control plane
    needed (the registry is on disk), so this stays usable on any server.
    """
    from src.app.benchmarks import load_benchmarks

    try:
        benchmarks = load_benchmarks()
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=f"benchmark registry: {exc}")
    return JSONResponse([b.to_dict() for b in benchmarks])


@app.get("/api/roms")
async def api_roms():
    """The ROM registry — ``[{id, name, game, game_name, default, benchmark_ok,
    has_start_save, on_disk}, ...]``.

    Backs the new-run dialog's game picker. ``benchmark_ok`` is derived (some
    ladder is authored for that game), which is what greys the dialog's Benchmark
    option out for a casual-only game. ``on_disk`` is added here rather than in
    the registry layer because it's a property of THIS machine, not of the
    registry: ``roms/`` is gitignored, so a checkout legitimately has entries
    with no dump behind them, and the picker should say so rather than offer a
    game that can't boot.
    """
    from src.app.roms import get_rom, list_roms

    try:
        roms = list_roms()
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=f"rom registry: {exc}")
    for row in roms:
        row["on_disk"] = get_rom(row["id"]).exists()
    return JSONResponse(roms)


@app.get("/api/checkpoints")
async def api_checkpoints():
    """Story events a casual run can stop at — ``[{id, name, type}, ...]``.

    The full ladder flattened into ladder order (see
    ``src.app.catalog.list_stop_events``). Backs the new-run dialog's "Stop at"
    picker. Like ``/api/benchmarks``: a pure on-disk projection, no control
    plane needed.
    """
    from src.app.catalog import list_stop_events

    try:
        events = list_stop_events()
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=f"checkpoint ladder: {exc}")
    return JSONResponse(events)


@app.get("/api/leaderboard")
async def api_leaderboard(benchmark: str | None = None):
    """Official winners — best per model, gates desc then turns asc (locked #3).

    ``benchmark`` (a registry id) scopes the board to one benchmark; each
    benchmark has its own ranking since their gate ladders differ. Omitted →
    all eligible official runs (legacy behaviour).
    """
    from src.app import derivations

    _queue, _executor, index = _require_control()
    winners = derivations.leaderboard(index.all(), benchmark=benchmark)
    return JSONResponse([s.model_dump(mode="json") for s in winners])


@app.get("/api/runs/{run_id}")
async def api_run_get(run_id: str):
    """One flat RunSummary; 404 if the index has no such run."""
    _queue, _executor, index = _require_control()
    entry = index.get(run_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
    return JSONResponse(_with_recording(entry.model_dump(mode="json")))


@app.get("/api/runs/{run_id}/summary")
async def api_run_summary(run_id: str):
    """Serve the run's RAW nested ``run_summary.json`` (Plan §P6, the report).

    Unlike ``/api/runs/{id}`` (the FLAT index projection for lists), the SPA's
    Report view needs the full nested document — ``{session, cost:{…, per_turn},
    turns, referee:{gates, furthest, termination_reason}}`` — to render the gate
    scorecard + per-turn trace. We serve the on-disk JSON as-is. 404 when the run
    dir or summary file is absent.
    """
    _queue, executor, _index = _require_control()
    run_dir = Path(executor.runs_root) / run_id
    summary_path = run_dir / "run_summary.json"
    if not summary_path.is_file():
        raise HTTPException(
            status_code=404, detail=f"run_summary.json not found: {run_id}"
        )
    try:
        with open(summary_path) as f:
            summary = json.load(f)
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"could not read run_summary.json: {exc}"
        )
    return JSONResponse(summary)


# ───────────────────── task-grouped trace (Plan Round 8 / B1+B2) ────────────
#
# The SPA Report inspector (Phase 5) needs a TWO-LEVEL TaskMaster structure:
# each task group's master trace/model/cost as the top-level node, with the
# Player's turns nested under it (plus the screenshots the master + each turn
# saw). We build it from `src.core.event_parsing`'s canonical grouping
# (`group_events_by_turn` + `group_turns_by_task` + `_group_trace_into_steps`),
# the one home for these parsers. The endpoint returns a SPA-friendly
# JSON projection of those groups — structured trace STEPS (not raw messages),
# screenshot REFERENCES (filenames the SPA turns into `/screenshots/{name}` URLs),
# and the master's input thumbnails inlined as data-URIs (they only exist in the
# event as data_url; there's no stable on-disk name to reference).

@app.get("/api/runs/{run_id}/trace")
async def api_run_trace(run_id: str):
    """Task-grouped trace + screenshot references for a finished run (B1+B2).

    Two-level structure the SPA Report inspector renders: each task group is a
    master decision (model/cost/structured trace/input thumbnails) with the
    Player's turns nested. Screenshots are returned as BASENAMES — the SPA builds
    ``/api/runs/{id}/screenshots/{name}`` to load each. Non-TaskMaster runs get a
    single implicit group (never 500). 404 only when the run dir is absent.
    """
    _queue, executor, _index = _require_control()
    run_dir = Path(executor.runs_root) / run_id
    if not run_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"run dir not found: {run_id}")

    # Serve the cached projection when it is at least as new as events.jsonl.
    # A ``--continue`` appends events → events newer than the cache → rebuild.
    cache = run_dir / "trace.json"
    events = run_dir / "events.jsonl"
    if cache.is_file() and (
        not events.exists() or cache.stat().st_mtime >= events.stat().st_mtime
    ):
        try:
            with open(cache) as f:
                return JSONResponse(json.load(f))
        except Exception:
            pass  # corrupt/partial cache → fall through and rebuild

    data = build_run_trace(run_dir)
    try:
        with open(cache, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass
    return JSONResponse(data)


@app.get("/api/runs/{run_id}/recording.mp4")
async def api_run_recording(run_id: str):
    """Stream ``<run_dir>/recording.mp4`` for an in-page `<video>` element.

    ``FileResponse`` honours HTTP Range, which is what makes the player's scrub
    bar work — without byte ranges the browser must download the whole file
    before it can seek, and seeking backwards refetches it.

    ``inline`` disposition, not the default ``attachment``: this URL is the
    `<video>` element's source first and a download second. The filename is
    still carried, so the ↓ button in the player saves it under the run's name
    rather than a directory full of identical `recording.mp4`s.
    """
    _require_control()
    path = _recording_path(run_id)
    if path is None:
        raise HTTPException(status_code=404, detail=f"no recording for run: {run_id}")
    return FileResponse(
        str(path),
        media_type="video/mp4",
        filename=f"{run_id}.mp4",
        content_disposition_type="inline",
    )


@app.get("/api/runs/{run_id}/screenshots/{name}")
async def api_run_screenshot(run_id: str, name: str):
    """Serve ``run_dir/screenshots/{name}`` as a PNG (Round 8 / B2).

    The trace JSON returns screenshot basenames; the SPA composes this URL to
    load each image. Path-traversal-guarded (the resolved file must stay inside
    the run's ``screenshots/`` dir). 404 when the run dir or file is absent.
    """
    _queue, executor, _index = _require_control()
    run_dir = Path(executor.runs_root) / run_id
    shots_dir = (run_dir / "screenshots").resolve()
    if not shots_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"no screenshots for run: {run_id}")
    candidate = (shots_dir / name).resolve()
    try:
        candidate.relative_to(shots_dir)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid screenshot name")
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail=f"screenshot not found: {name}")
    return FileResponse(str(candidate), media_type="image/png")


@app.get("/api/models")
async def api_models():
    """Collapsed model registry for the picker (one row per model + levels).

    Each row is ``{model, openrouter_id, reasoning_type, default_level, levels,
    observed, run_count}``. ``levels`` is an ordered list of
    ``{level, observed, run_count}`` — the picker shows a model dropdown plus a
    thinking-level dropdown (default = the highest level). ``run_count`` is the
    number of history runs for that ``model(level)`` identity (the model-level
    ``run_count`` sums across levels). When the control plane isn't configured
    (headless ``pokemon run`` path) the index is absent, so counts are 0.
    """
    from src.app import derivations
    from src.app.catalog import list_models

    models = list_models()
    index = _CONTROL["index"]
    counts = derivations.run_counts_by_model(index.all()) if index is not None else {}
    for entry in models:
        model = entry["model"]
        if entry["levels"]:
            total = 0
            for lvl in entry["levels"]:
                rc = counts.get(f"{model}({lvl['level']})", 0)
                lvl["run_count"] = rc
                total += rc
            entry["run_count"] = total
        else:
            # reasoning_type none — the bare model name is the run identity.
            entry["run_count"] = counts.get(model, 0)
    return JSONResponse(models)


@app.get("/api/configs")
async def api_configs():
    """Casual config stems discovered from ``configs/config-*.yaml``."""
    from src.app.catalog import list_configs

    return JSONResponse(list_configs())


@app.get("/api/emulator/status")
async def api_emulator_status():
    """Supervisor health (``process_up``/``connected``/``busy``).

    Never 500/503 when the control plane isn't configured yet — returns an idle
    ``configured: false`` payload so the Home spectate pill can render grey.
    """
    import dataclasses

    executor = _CONTROL["executor"]
    if executor is None or getattr(executor, "supervisor", None) is None:
        # Headless ``pokemon run`` registers live sessions in _REGISTRY but does
        # not wire the control-plane executor. Spectate still needs an
        # active_run_id to open /runs/{id}/ws/* — expose the newest registry
        # entry so CLI runs are spectatable at /spectate without ``pokemon app``.
        sessions = _REGISTRY.all()
        active_run_id = None
        if sessions:
            active_run_id = max(sessions, key=lambda s: s.registered_at).run_id
        live = bool(active_run_id)
        return JSONResponse(
            {
                "configured": live,
                "process_up": live,
                "connected": live,
                "busy": live,
                "active_run_id": active_run_id,
                "muted": True,
                "rom": None,
                "switching_to": None,
                "awaiting_lua": False,
            }
        )

    status = executor.supervisor.status()
    if dataclasses.is_dataclass(status) and not isinstance(status, type):
        payload = dataclasses.asdict(status)
    else:
        payload = {
            "process_up": getattr(status, "process_up", False),
            "connected": getattr(status, "connected", False),
            "busy": getattr(status, "busy", False),
        }
    payload["configured"] = True
    # Expose the executor's active run id so the SPA can open the right
    # /runs/{id}/ws/* streams (Plan §P6). null in headless / between runs.
    payload["active_run_id"] = getattr(executor, "_active_run_id", None)
    # Current audio mute state (drives the Home + Spectate mute toggles).
    payload["muted"] = bool(getattr(getattr(executor, "supervisor", None), "muted", True))
    # Which game is in the slot, named. Resolved from the launched path through
    # the registry; an off-registry ROM (a hand-rolled config) yields None rather
    # than a guess. `awaiting_lua` is the state a ROM switch parks in: the process
    # is up but the script hasn't been re-loaded, which the UI has to explain —
    # it looks identical to "broken" otherwise.
    from src.app.roms import rom_for_path

    rom_path = payload.pop("rom_path", "") or ""
    try:
        rom = rom_for_path(rom_path)
    except (FileNotFoundError, ValueError):
        rom = None
    payload["rom"] = (
        {"id": rom.id, "name": rom.name, "game": rom.game} if rom else None
    )
    switching_path = payload.get("switching_to") or ""
    if switching_path:
        try:
            target = rom_for_path(switching_path)
        except (FileNotFoundError, ValueError):
            target = None
        payload["switching_to"] = target.id if target else switching_path
    payload["awaiting_lua"] = bool(payload["process_up"]) and not payload["connected"]
    return JSONResponse(payload)


# ───────────────────────── SPA serving (Plan §P5) ─────────────────────────
#
# Serve the built Svelte SPA at `/` with a history-API fallback so client-side
# deep links (`/spectate`, `/history/<id>`, `/about`) load `index.html`. These
# are registered LAST so every `/api/*` and `/runs/*` data route takes
# precedence — the catch-all only matches paths nothing else claimed. All of it
# is conditional on the build existing (gitignored + regenerated), so the
# headless `pokemon run` path is unaffected when there's no `web/dist`.

if SPA_DIST_DIR.is_dir():
    _spa_assets = SPA_DIST_DIR / "assets"
    if _spa_assets.is_dir():
        # Vite emits hashed JS/CSS under /assets — serve them directly.
        app.mount("/assets", StaticFiles(directory=str(_spa_assets)), name="spa-assets")


@app.get("/{full_path:path}")
async def spa_fallback(full_path: str):
    """SPA catch-all: serve `index.html` for unmatched client routes.

    Guards:
      - 404 (not the SPA) for unmatched `/api/*` and `/runs/*` paths, so a
        missing API route returns a clean 404 instead of silently shadowing it
        with HTML.
      - serves a real built asset/file if one exists at the path (favicon, etc.).
      - 404 when the SPA isn't built (headless `pokemon run` path).
    """
    if full_path.startswith(("api/", "runs/")) or full_path in ("api", "runs"):
        raise HTTPException(status_code=404, detail=f"not found: /{full_path}")
    if not _spa_built():
        raise HTTPException(status_code=404, detail="SPA not built")
    # Serve a concrete static file if it exists (e.g. /favicon.ico, /vite.svg).
    candidate = (SPA_DIST_DIR / full_path).resolve()
    try:
        candidate.relative_to(SPA_DIST_DIR.resolve())
    except ValueError:
        candidate = SPA_INDEX  # path traversal attempt → fall back to index
    if full_path and candidate.is_file():
        return FileResponse(str(candidate))
    return FileResponse(
        str(SPA_INDEX),
        media_type="text/html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )
