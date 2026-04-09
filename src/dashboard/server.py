"""FastAPI server for the live dashboard."""

import asyncio
import json
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from src.dashboard.event_bridge import EventBridge
from src.dashboard.screen_stream import ScreenStreamer

# Module-level references set by start_dashboard()
_bridge: EventBridge = None  # type: ignore
_streamer: ScreenStreamer = None  # type: ignore
_state_manager = None
_config: dict = {}

app = FastAPI(title="AI Plays Pokemon Dashboard")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

STATIC_DIR = Path(__file__).parent / "static"


@app.get("/")
async def index():
    """Serve the dashboard HTML with inline cache busting."""
    html_path = STATIC_DIR / "index.html"
    content = html_path.read_text()
    # Inject timestamp to bust any browser cache
    import time
    content = content.replace("</head>", f"<!-- cache-bust: {time.time()} -->\n</head>")
    from starlette.responses import HTMLResponse
    return HTMLResponse(
        content=content,
        headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"},
    )


@app.get("/api/state")
async def get_state():
    if _state_manager is None:
        return JSONResponse({})
    return JSONResponse(_state_manager.get_truncated_view())


@app.get("/api/config")
async def get_config():
    return JSONResponse({
        "task": _config.get("top_level_task", ""),
        "llm_model": _config.get("llm_model", ""),
        "vlm_model": _config.get("vlm_model", ""),
    })


@app.websocket("/ws/events")
async def ws_events(websocket: WebSocket):
    """Stream log events to the browser. Replays history on connect, then streams live."""
    await websocket.accept()

    # Start from the beginning — replay all events and then continue live
    cursor = 0

    # Send current state
    if _state_manager:
        try:
            await websocket.send_text(json.dumps({
                "type": "state_update",
                "data": _state_manager.get_truncated_view(),
            }, default=str))
        except Exception:
            return

    # Send current stats
    try:
        await websocket.send_text(json.dumps({"type": "stats", "data": _bridge.get_stats()}))
    except Exception:
        return

    # Poll for events (both replay and live use the same loop)
    try:
        while True:
            events, cursor = _bridge.get_events_since(cursor)
            for event in events:
                await websocket.send_text(json.dumps({"type": "event", "data": event}, default=str))

                if event.get("type") == "state_change" and _state_manager:
                    await websocket.send_text(json.dumps({
                        "type": "state_update",
                        "data": _state_manager.get_truncated_view(),
                    }, default=str))

                if event.get("type") in ("turn_usage", "turn_start"):
                    await websocket.send_text(json.dumps({
                        "type": "stats",
                        "data": _bridge.get_stats(),
                    }))

            if not events:
                await asyncio.sleep(0.05)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass


@app.websocket("/ws/screen")
async def ws_screen(websocket: WebSocket):
    """Stream live JPEG frames from the emulator."""
    await websocket.accept()
    last_frame = None
    try:
        while True:
            frame = _streamer.get_frame()
            if frame is not None and frame is not last_frame:
                await websocket.send_bytes(frame)
                last_frame = frame
            await asyncio.sleep(0.033)  # ~30Hz check rate, actual fps limited by Lua capture rate
    except WebSocketDisconnect:
        pass
    except Exception:
        pass


def start_dashboard(
    logger,
    state_manager,
    config: dict[str, Any],
    port: int = 3000,
) -> None:
    """Start the live dashboard server in a background thread.

    This is non-blocking — it starts the server and returns immediately.
    The server runs in a daemon thread and dies with the main process.
    """
    global _bridge, _streamer, _state_manager, _config

    _config = config
    _state_manager = state_manager

    # Create event bridge and register as logger listener
    _bridge = EventBridge()
    logger.add_listener(_bridge.on_event)

    # Start screen streamer
    _streamer = ScreenStreamer()
    _streamer.start()

    # Start uvicorn in a daemon thread
    import uvicorn

    uvi_config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
    )
    server = uvicorn.Server(uvi_config)

    thread = threading.Thread(target=server.run, daemon=True, name="dashboard-server")
    thread.start()

    # Wait briefly for server to start, then open browser with cache-busting URL
    time.sleep(0.8)
    import time as _time
    webbrowser.open(f"http://localhost:{port}/?v={int(_time.time())}")

    print(f"  Dashboard: http://localhost:{port}")
