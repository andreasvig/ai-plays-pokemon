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
from fastapi.responses import HTMLResponse, JSONResponse

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
async def api_runs():
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


@app.get("/runs/{run_id}/api/config")
async def get_config(run_id: str):
    s = _require_session(run_id)
    return JSONResponse({
        "run_id": s.run_id,
        "label": s.label,
        "task": s.config.get("task", {}).get("goal", ""),
        "llm_model": s.config.get("_llm_alias") or s.config.get("llm_model", ""),
        "vlm_model": s.config.get("vlm_model", ""),
    })


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
