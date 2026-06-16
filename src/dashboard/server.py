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
        return JSONResponse([s.model_dump(mode="json") for s in rows])

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
    """Reject a model that's neither a known models.yaml alias nor a raw id."""
    from src.config import _is_raw_model_id, _load_models_registry

    if _is_raw_model_id(model):
        return
    registry = _load_models_registry()
    if model not in registry:
        known = ", ".join(sorted(registry)) or "(registry empty)"
        raise HTTPException(
            status_code=400,
            detail=f"unknown model {model!r}; known aliases: {known}",
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


@app.get("/api/queue")
async def api_queue_get():
    """`{active, items}` — the serial queue (active queue_id + ordered items)."""
    queue, _executor, _index = _require_control()
    return JSONResponse(
        {
            "active": queue.active,
            "items": [it.model_dump(mode="json") for it in queue.items],
        }
    )


def _enqueue_kwargs(spec: dict) -> dict:
    """Validate one enqueue ``spec`` → kwargs for ``QueueManager.enqueue``.

    Raises ``HTTPException(400)`` on a bad kind / missing-or-unknown model /
    unknown benchmark. Official (locked #4/#7) FORCES the frozen config + no
    max-turns (request config/max_turns ignored) and takes only the request's
    ``benchmark``; casual keeps the request's config/max_turns/continue_from.
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

    if kind == RunKind.official:
        benchmark = _validate_benchmark_id(spec.get("benchmark"))
        return {"kind": kind, "model": model, "config": None, "benchmark": benchmark, "max_turns": None}
    return {
        "kind": kind,
        "model": model,
        "config": spec.get("config"),
        "max_turns": spec.get("max_turns"),
        "continue_from": spec.get("continue_from"),
    }


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
    """Build a CASUAL continue spec and enqueue it (locked #10).

    Reuses the SOURCE run's model (any model in the request body is IGNORED),
    resolves the latest savepoint, sets ``continue_from``, and enqueues casual.
    """
    queue, executor, _index = _require_control()
    try:
        spec = executor.build_continue_spec(run_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    item = queue.enqueue(
        kind=spec["kind"],
        model=spec["model"],
        config=spec.get("config"),
        max_turns=(body or {}).get("max_turns"),
        continue_from=spec["continue_from"],
    )
    notify_control()
    return JSONResponse(item.model_dump(mode="json"), status_code=201)


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
    return JSONResponse(entry.model_dump(mode="json"))


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
    """Aliases from ``models.yaml`` (+ observed cost/latency, + run_count).

    Backward-compatible (Round 8 / C3): each entry keeps its existing
    ``alias`` / ``openrouter_id`` / ``observed`` fields and gains a ``run_count``
    int — how many runs exist for that alias in the history index. When the
    control plane isn't configured (headless ``pokemon run`` path) the index is
    absent, so every ``run_count`` is 0. The shape stays a list of objects; only
    a new field was added.
    """
    from src.app import derivations
    from src.app.catalog import list_models

    models = list_models()
    index = _CONTROL["index"]
    counts = derivations.run_counts_by_model(index.all()) if index is not None else {}
    for entry in models:
        entry["run_count"] = counts.get(entry.get("alias"), 0)
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
        return JSONResponse(
            {
                "configured": False,
                "process_up": False,
                "connected": False,
                "busy": False,
                "active_run_id": None,
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
