"""FastAPI server for the live dashboard.

Supports N concurrent runs via dynamic routing. Each run registers a
RunSession in the RunRegistry; routes are namespaced under /runs/{run_id}.

URL layout:
    GET  /                              → index of active runs
    GET  /runs/{run_id}                 → run UI (same HTML, parameterized)
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
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

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

STATIC_DIR = Path(__file__).parent / "static"


def _require_session(run_id: str) -> RunSession:
    session = _REGISTRY.get(run_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
    return session


def _render_run_html(
    *,
    run_prefix_override: Optional[str] = None,
    is_current_view: bool = False,
) -> str:
    """Serve index.html with a cache-bust marker.

    When served from /current, the page itself stays at /current but the
    inner WebSocket / API calls need to target /runs/<latest_id>. We inject
    a tiny script that sets two window-globals the dashboard JS reads:
      - RUN_PREFIX_OVERRIDE  → forces RUN_PREFIX to the latest run's prefix
      - IS_CURRENT_VIEW      → triggers reload() instead of reconnect when
        a WebSocket closes with code 1008 (session unregistered)
    """
    html_path = STATIC_DIR / "index.html"
    content = html_path.read_text()
    inject_lines = []
    if run_prefix_override:
        inject_lines.append(f"window.RUN_PREFIX_OVERRIDE = {json.dumps(run_prefix_override)};")
    if is_current_view:
        inject_lines.append("window.IS_CURRENT_VIEW = true;")
    if inject_lines:
        inject = "<script>\n" + "\n".join(inject_lines) + "\n</script>\n"
        content = content.replace("</head>", f"{inject}</head>")
    return content.replace("</head>", f"<!-- cache-bust: {time.time()} -->\n</head>")


@app.get("/")
async def index_page():
    """List all registered runs."""
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


@app.get("/runs/{run_id}")
async def run_page(run_id: str):
    _require_session(run_id)
    return HTMLResponse(
        content=_render_run_html(),
        headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"},
    )


@app.get("/current")
async def current_page():
    """Stable URL that always serves the latest-registered active run.

    Sequential orchestrator usage: open this once, leave it open. When the
    current run ends and the next one registers, the inner WebSockets get
    a 1008 close → the dashboard JS reloads /current → the server picks
    the new latest run → seamless transition without changing the URL.
    Between runs (no session registered), serves an auto-refreshing
    waiting page until the next run comes online.
    """
    sessions = _REGISTRY.all()
    if not sessions:
        return HTMLResponse(
            "<!doctype html><html><head>"
            "<title>Waiting for next run…</title>"
            "<meta http-equiv='refresh' content='2'>"
            "<style>body{font-family:system-ui;max-width:480px;margin:80px auto;"
            "padding:0 16px;text-align:center;color:#666}</style>"
            "</head><body><h2>Waiting for next run…</h2>"
            "<p>This page reloads every 2 seconds.</p></body></html>",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )
    latest = max(sessions, key=lambda s: s.registered_at)
    return HTMLResponse(
        content=_render_run_html(
            run_prefix_override=f"/runs/{latest.run_id}",
            is_current_view=True,
        ),
        headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"},
    )


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

    # Direct URL = run-pinned. /current = stable URL that follows the
    # latest run (best for sequential orchestrators).
    run_url = f"http://localhost:{_SERVER_PORT}/runs/{run_id}"
    current_url = f"http://localhost:{_SERVER_PORT}/current"
    if open_browser:
        webbrowser.open(f"{current_url}?v={int(time.time())}")
    print(f"  Dashboard: {run_url}")
    print(f"  Stable URL (sequential): {current_url}")
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


@app.post("/api/queue")
async def api_queue_post(spec: dict):
    """Enqueue a QueuedRun spec.

    Official enqueue (locked #4/#7) FORCES the frozen config + enforced gates +
    no max-turns — any config/max_turns in the request is IGNORED. Casual takes
    the request's config + max_turns. The model is validated against models.yaml.
    """
    queue, _executor, _index = _require_control()

    raw_kind = spec.get("kind")
    try:
        from src.app.models import RunKind

        kind = RunKind(raw_kind)
    except Exception:
        raise HTTPException(status_code=400, detail=f"invalid kind: {raw_kind!r}")

    model = spec.get("model")
    if not model or not isinstance(model, str):
        raise HTTPException(status_code=400, detail="model is required")
    _validate_model_alias(model)

    if kind == RunKind.official:
        # Frozen: config + max_turns come from the executor's official wiring,
        # not the request. Ignore whatever was sent.
        item = queue.enqueue(kind=kind, model=model, config=None, max_turns=None)
    else:
        item = queue.enqueue(
            kind=kind,
            model=model,
            config=spec.get("config"),
            max_turns=spec.get("max_turns"),
            continue_from=spec.get("continue_from"),
        )
    return JSONResponse(item.model_dump(mode="json"), status_code=201)


@app.delete("/api/queue/{queue_id}")
async def api_queue_delete(queue_id: str):
    """Cancel a queued item by id."""
    queue, _executor, _index = _require_control()
    removed = queue.cancel(queue_id)
    if not removed:
        raise HTTPException(status_code=404, detail=f"queue_id not found: {queue_id}")
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
    return JSONResponse(item.model_dump(mode="json"), status_code=201)


# ───────────────────────── read routes (Plan §P4) ─────────────────────────
#
# ADDITIVE read surface backing the SPA's leaderboard / history / report /
# models / configs / emulator-status. Same style as the P3 control routes:
# backed by the injected _CONTROL (queue/executor/index); 503 until configured —
# EXCEPT /api/emulator/status, which returns an idle "configured: false" payload
# rather than erroring when the control plane isn't wired (a useful Home signal
# even on the headless `pokemon run` path).


@app.get("/api/leaderboard")
async def api_leaderboard():
    """Official winners — best per model, gates desc then turns asc (locked #3)."""
    from src.app import derivations

    _queue, _executor, index = _require_control()
    winners = derivations.leaderboard(index.all())
    return JSONResponse([s.model_dump(mode="json") for s in winners])


@app.get("/api/runs/{run_id}")
async def api_run_get(run_id: str):
    """One flat RunSummary; 404 if the index has no such run."""
    _queue, _executor, index = _require_control()
    entry = index.get(run_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
    return JSONResponse(entry.model_dump(mode="json"))


@app.get("/api/runs/{run_id}/report")
async def api_run_report(run_id: str):
    """Serve the run's ``report.html``; regenerate from events if missing/stale.

    404 only when the run DIR doesn't exist. Regeneration mirrors
    ``pokemon report``'s main(): load_events → group_events_by_turn →
    generate_html → write ``run_dir/report.html`` → serve.
    """
    _queue, executor, _index = _require_control()
    run_dir = Path(executor.runs_root) / run_id
    if not run_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"run dir not found: {run_id}")

    report_path = run_dir / "report.html"
    if not report_path.exists():
        from src.cli import report as report_mod

        events = report_mod.load_events(run_dir)
        turns = report_mod.group_events_by_turn(events)
        html = report_mod.generate_html(run_dir, events, turns)
        report_path.write_text(html)

    return FileResponse(str(report_path), media_type="text/html")


@app.get("/api/models")
async def api_models():
    """Aliases from ``models.yaml`` (+ observed cost/latency, or null)."""
    from src.app.catalog import list_models

    return JSONResponse(list_models())


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
            {"configured": False, "process_up": False, "connected": False, "busy": False}
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
    return JSONResponse(payload)
