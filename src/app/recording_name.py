"""Name a run's MP4 after the run's own settings.

On disk every recording is ``<run_dir>/recording.mp4`` — unambiguous while it
sits next to its ``config.json``, and useless the moment it is downloaded into a
folder with thirty siblings. This module builds the name the file leaves the
system under, from what the run folder already records:

    2026-08-02_1556_firered_casual-exploration_config-4.0_claude-opus-5-medium
    _17turns_1.03usd_cap1.00usd_cap20turns_stop-starter-chosen
    _simple-cut-thinking_hit-budget.mp4

Segments appear in a fixed order and absent ones are dropped, so two names line
up column-wise for as far as they agree. Nothing here is authoritative: the run
folder is. This is a label, and it is built to survive a folder that predates
any given field — every read is defensive, and the fallback is the run id, which
is what the endpoint used before this existed.

Deliberately NOT the run-folder name. ``run_id`` is the primary key across the
queue, the run index, savepoints and the continue chain; it is keyed on, joined
on and parsed (``_infer_config_stem``), and prettifying it would ripple through
all of that to no benefit — a folder is already sitting next to its own config.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from src.app.models import RunKind, RunStatus

# Mirrors executor.GAMEPLAY_MODES, inverted: config `mode` → the casual
# playstyle that selects it. Read as a display mapping, not a second source of
# truth — an unrecognised mode falls through to the raw value rather than
# guessing, so a new mode shows up in a filename instead of vanishing from it.
_MODE_TO_GAMEPLAY = {"freeplay": "exploration", "benchmark": "speed"}

# The executor's "no turn cap in practice" sentinel for official runs. A literal
# `cap10000000turns` in a filename is noise, so a limit at or above this is
# reported as no cap at all.
_TURN_CAP_NOISE_FLOOR = 1_000_000

# macOS caps a single path COMPONENT at 255 bytes. The composed name runs ~170
# in the worst realistic case, but a hand-rolled config can carry an arbitrarily
# long model alias or config stem, so the result is truncated rather than
# trusted — a name that cannot be saved is worse than a shortened one.
_MAX_STEM_BYTES = 200


def _slug(value: Any) -> str:
    """Filesystem-safe lowercase slug (same shape as ``cli/runner._slug``)."""
    return re.sub(r"[^a-zA-Z0-9.]+", "-", str(value)).strip("-").lower()


def _load(path: Path) -> dict:
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _timestamp(run_dir_name: str, started_at: Optional[str]) -> Optional[str]:
    """``2026-08-02_1556`` from the run-dir prefix, else from ``started_at``.

    The folder prefix is preferred because it is present on every run ever
    written, including the legacy ones with no ``session.started_at``.
    """
    m = re.match(r"^(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})-\d{2}", run_dir_name)
    if m:
        return f"{m.group(1)}_{m.group(2)}{m.group(3)}"
    if isinstance(started_at, str):
        m = re.match(r"^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})", started_at)
        if m:
            return f"{m.group(1)}_{m.group(2)}{m.group(3)}"
    return None


def _game(config: dict) -> Optional[str]:
    """The ROM's registry id (``firered``), else its ``game_name``, else None.

    The id is preferred over the display name because it is the token every
    other surface uses (``pokemon ls roms``, ``--rom``), so a filename and a
    command line spell the same game the same way. An off-registry ROM — a
    legitimate hand-rolled config — still gets named, from whatever the config
    calls the game or, failing that, the ROM file's own stem.
    """
    rom_path = (config.get("emulator") or {}).get("rom_path")
    try:
        from src.app.roms import rom_for_path

        rom = rom_for_path(rom_path)
        if rom is not None:
            return _slug(rom.id)
    except Exception:
        pass  # registry unreadable → fall through to the config's own words
    if config.get("game_name"):
        return _slug(config["game_name"])
    if rom_path:
        return _slug(Path(str(rom_path)).stem)[:24]
    return None


def _kind_segment(summary: dict, config: dict) -> Optional[str]:
    """``casual-exploration`` / ``casual-speed`` / ``official-pokebench-easy``.

    Official runs are qualified by WHICH benchmark rather than by playstyle:
    they are all speed runs, so the playstyle carries no information there,
    while the ladder they were scored against carries all of it.
    """
    kind = summary.get("kind")
    if kind not in (RunKind.official.value, RunKind.casual.value):
        # Legacy runs predate the explicit key; projection infers the same way.
        kind = (
            RunKind.official.value
            if summary.get("benchmark_version")
            else RunKind.casual.value
        )

    if kind == RunKind.official.value:
        benchmark = summary.get("benchmark")
        return f"official-{_slug(benchmark)}" if benchmark else "official"

    mode = config.get("mode")
    if not mode:
        return "casual"
    return f"casual-{_slug(_MODE_TO_GAMEPLAY.get(mode, mode))}"


def _model_slug(summary: dict, config: dict) -> Optional[str]:
    """``claude-opus-5-medium`` — the alias, tier included, as the user typed it."""
    session = summary.get("session") or {}
    alias = (
        session.get("llm_alias")
        or config.get("_llm_alias")
        or session.get("llm_model")
        or config.get("llm_model")
    )
    return _slug(alias) if alias else None


def _outcome_segment(summary: dict) -> Optional[str]:
    """A trailing marker when the run did NOT simply finish.

    Turns and cost describe what happened; this says whether to trust it. A
    clip of a crashed or cancelled run looks exactly like a clip of a finished
    one, and publishing the wrong one is the mistake this segment prevents.
    """
    if summary.get("stop_reason") == "max_spend":
        return "hit-budget"
    status = summary.get("status")
    if status in {s.value for s in RunStatus} and status != RunStatus.completed.value:
        return _slug(status)
    if summary.get("error"):
        return "no-output"
    return None


def _truncate(stem: str) -> str:
    """Trim to :data:`_MAX_STEM_BYTES` on a segment boundary where possible."""
    if len(stem.encode()) <= _MAX_STEM_BYTES:
        return stem
    parts = stem.split("_")
    while len(parts) > 1 and len("_".join(parts).encode()) > _MAX_STEM_BYTES:
        parts.pop()
    out = "_".join(parts)
    while len(out.encode()) > _MAX_STEM_BYTES:
        out = out[:-1]
    return out.rstrip("_-")


def recording_stem(run_dir: Path | str, *, run_id: Optional[str] = None) -> str:
    """The settings-bearing filename stem for this run's recording (no suffix).

    Falls back to the run id (the folder name) when the folder carries nothing
    readable — the behaviour every caller had before this module existed.
    """
    run_dir = Path(run_dir)
    fallback = run_id or run_dir.name
    config = _load(run_dir / "config.json")
    summary = _load(run_dir / "run_summary.json")
    if not config and not summary:
        return fallback

    session = summary.get("session") or {}
    cost = summary.get("cost") or {}
    segment = session.get("segment") or {}

    parts: list[Optional[str]] = [
        _timestamp(run_dir.name, session.get("started_at")),
        _game(config),
        _kind_segment(summary, config),
        _slug(Path(config["_config_path"]).stem) if config.get("_config_path") else None,
        _model_slug(summary, config),
    ]

    # Continue marker: a segment that resumed someone else's save is not turn 1,
    # and its turn count is the CHAIN's, not this segment's.
    resumed_at = segment.get("resumed_at_turn")
    if isinstance(resumed_at, int):
        parts.append(f"cont-from-t{resumed_at}")

    turns = session.get("total_turns")
    if isinstance(turns, int) and turns:
        parts.append(f"{turns}turns")

    spend = cost.get("total_usd")
    if isinstance(spend, (int, float)) and spend:
        parts.append(f"{spend:.2f}usd")

    # --- the bounds it ran under, whichever one it did or didn't reach ---
    cap_usd = summary.get("max_spend_usd")
    if isinstance(cap_usd, (int, float)) and cap_usd:
        parts.append(f"cap{cap_usd:.2f}usd")

    cap_turns = summary.get("max_turns")
    if isinstance(cap_turns, int) and 0 < cap_turns < _TURN_CAP_NOISE_FLOOR:
        parts.append(f"cap{cap_turns}turns")

    stop_at = (config.get("referee") or {}).get("stop_at")
    if stop_at:
        parts.append(f"stop-{_slug(stop_at)}")

    record = config.get("_record") or {}
    if record.get("view") or record.get("speed"):
        parts.append(_slug(f"{record.get('view', '')}-{record.get('speed', '')}"))

    parts.append(_outcome_segment(summary))

    stem = "_".join(p for p in parts if p)
    return _truncate(stem) if stem else fallback


def recording_filename(run_dir: Path | str, *, run_id: Optional[str] = None) -> str:
    """:func:`recording_stem` with the ``.mp4`` suffix."""
    return f"{recording_stem(run_dir, run_id=run_id)}.mp4"
