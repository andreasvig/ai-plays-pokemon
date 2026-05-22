"""Static slot config for the mGBA + Lua TCP pairing.

Holds the port + /tmp PNG paths + Lua file that the harness uses. The
table format is a historical holdover from the parallel-runs experiment
— only slot 1 is supported now (sequential runs share one connection).
"""

from pathlib import Path
from typing import TypedDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class SlotConfig(TypedDict):
    port: int
    stream_path: str
    screenshot_path: str
    lua_path: Path
    window_pos: tuple[int, int]


_SLOT: SlotConfig = {
    "port": 8888,
    "stream_path": "/tmp/mgba_stream_1.png",
    "screenshot_path": "/tmp/mgba_screenshot_1.png",
    "lua_path": PROJECT_ROOT / "lua" / "socketserver-1.lua",
    "window_pos": (40, 80),
}


def get_slot(slot: int = 1) -> SlotConfig:
    if slot != 1:
        raise ValueError(
            f"only slot 1 is supported (got {slot}); the parallel-slots "
            "system was removed when sequential runs replaced parallel."
        )
    if not _SLOT["lua_path"].exists():
        raise FileNotFoundError(
            f"Lua file missing at {_SLOT['lua_path']}. "
            "Did you delete it accidentally?"
        )
    return _SLOT
