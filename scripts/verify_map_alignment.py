"""Does the map we ship line up with the game? — per turn, with its own controls.

    ./venv/bin/python scripts/verify_map_alignment.py local/runs/<run_id>
    ./venv/bin/python scripts/verify_map_alignment.py local/runs/*v2-crystal* --turns 16
    ./venv/bin/python scripts/verify_map_alignment.py local/runs/*v2-platinum*   # refused, with the reason

For a finished run: take the map artwork we actually ship, crop it to the
camera window the player was standing in on a given turn, and put that crop
beside the emulator frame the model was shown on that same turn. Out comes a
sheet a human can scan (``artifacts/game-map-render/verify/``) and a number a
script can gate on.

**The number is never printed alone.** Four controls go with it, every time:

- ``self``       the crop pushed back out through the SCREENSHOT reader —
                 upscaled to the run's own screenshot size, decimated,
                 un-gridded — and compared to itself. 1.0000 or the reader is
                 lying and nothing else on the page may be quoted.
- ``wrong crop`` the same map, cropped 4 tiles off. Must score clearly worse.
- ``wrong map``  the same frame against a different map of the same atlas.
                 Must score worse still.
- ``margin``     the correct crop minus the best of its eight one-tile
                 neighbours. Goes NEGATIVE under the classic silent failure —
                 an atlas off by a constant — because then a neighbour fits
                 better than the declared crop.

``self`` is the one that earns its keep. ``stitch_maps.py --verify`` was
silently broken for weeks because ``GBA_FIRERED.grid_overlay`` was True while
every surviving run had recorded with the overlay off: the reader
un-composited an overlay that had never been applied, corrupting the two
pixels straddling every tile boundary, and FireRed scored a uniform 74-82%
that read as "the atlas is a bit off" rather than as a bug. A crop that has
been through a reader like that no longer equals itself, so ``self`` falls off
1.0000 on sight. ``tests/test_verify_map_alignment.py`` proves that by forcing
the flag back on.

The band this reports against was pre-registered in
``artifacts/game-map-render/notes/verify.md`` BEFORE any score was read.

**Where the instrument does not apply, it says so instead of printing a
number.** The three 2D games (FireRed, Emerald, Crystal) draw an axis-aligned
window of the same tile grid the artwork is, so a pixel in one is a pixel in
the other. The four DS games do not: their atlases declare ``render:
3d-ortho`` — the cartridge's own 3D field rendered straight down — while the
emulator draws that field through the game's tilted perspective camera, where
a tree leans over the ground behind it. Those two images are of the same world
and are not the same projection. A per-pixel difference between them measures
nothing, so this refuses them by name.

One function does tile -> pixel (:class:`TileProjection`, built by
:func:`projection_for`) and it reads ``camera`` off the atlas. When the angled
DS camera lands and ``camera`` stops saying ``"topdown"``, that function
raises with a message naming the value rather than quietly computing the
top-down answer.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import numpy as np
from PIL import Image, ImageDraw, ImageFont

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.app.roms import rom_for_path  # noqa: E402
from src.app.stitch import (  # noqa: E402
    SPECS, ScreenSpec, fit_colour_map, keep_mask, run_spec, to_native,
)

MAPS_ROOT = REPO_ROOT / "src" / "dashboard" / "web" / "public" / "maps"
OUT_DIR = REPO_ROOT / "artifacts" / "game-map-render" / "verify"
NOTES = "artifacts/game-map-render/notes/verify.md"

#: 8-bit values either side of a 5-bit console colour. The same tolerance the
#: stitcher and ``verify_gamemap_render.py`` use, so the three numbers are
#: comparable to each other.
TOLERANCE = 1

#: How far the deliberately-wrong crop is offset, in tiles. Four is chosen to
#: be far past any plausible real error and still land inside most maps.
WRONG_CROP = (4, 4)

#: Below this, NONE of the nine candidate crops fits, and the honest reading is
#: that the frame does not show this map at all — a battle, a menu, a fade, a
#: cutscene. Such a turn is reported and EXCLUDED rather than counted as
#: misalignment: a misaligned atlas has a neighbour that fits WELL, and a
#: battle screen fits nothing anywhere. Found the hard way on 2026-09-21: the
#: two lowest Emerald scores were both Mudkip fighting a Zigzagoon.
MAP_FRAME_FLOOR = 0.70

#: How nearly one-to-one the fitted colour lookup has to be before its score is
#: allowed to count. **This guard is not optional, and it was found by running
#: the thing.** A runtime recolour is a permutation of the palette: FireRed's
#: ``WEATHER_SHADE`` over Viridian Forest maps 11 render colours to 11 screen
#: colours and 35 to 35 (measured 2026-09-21), lifting a raw 0.0000 to a
#: correct 0.9972. A frame that is NOT the map collapses the palette instead:
#: a Crystal fade mapped 4 render colours onto 1 screen colour and the
#: unguarded fit scored it **1.0000**, a fabricated perfect match on a picture
#: of nothing; a FireRed menu mapped 39 onto 6. Requiring the lookup to keep
#: three quarters of the palette distinct separates the two cleanly, with the
#: nearest legitimate case at 23 -> 22.
LOOKUP_KEEPS = 0.75

#: How much of the disagreement the fitted colour lookup had to close before
#: the frame counts as RECOLOURED — the artwork and the game not sharing their
#: colours, with a palette permutation explaining it.
#:
#: **The gap, and only the gap.** Two things this is deliberately not. It is
#: not `match >= 0.90`: Crystal's NEW_BARK_TOWN under a day/night mismatch
#: fits 0.0002 raw and 0.8965 through the lookup, where the 0.8965 is NPCs and
#: the 0.8963 gap is the palette — a 0.90 level gate discarded the second
#: clearest recolour in the sample over four thousandths (measured
#: 2026-09-21). And it is not paired with a `direct < 0.70` floor, which is
#: what this constant replaced: since `match <= 1` and `match >= direct`, a
#: gap of 0.30 already implies a pre-lookup agreement at or under 0.70, so the
#: floor decided nothing. Its mutation control was a NO-OP, which is how it
#: was caught — two constants where one bites is one of them waiting to drift.
#:
#: Being recoloured is not by itself a fault: a runtime recolour is exactly
#: what the lookup exists to see through. It becomes a fault when it reaches
#: every map whose colours come from the world. See :func:`colour_verdict`.
RECOLOURED_GAP = 0.30

#: The area-name banner the game paints for a second or two on entering a new
#: area — "LITTLEROOT TOWN" on a wooden sign across the top-left. A frame
#: carrying it is a map-ENTRY frame, and on top of the occlusion the camera
#: may still be mid-scroll from the warp that put the player there.
#:
#: **It cannot be told from a real misalignment by its pixels.** Measured on
#: Emerald turn 49 (2026-09-21), per tile row, the share of comparable pixels
#: disagreeing:
#:
#:     at the DECLARED crop     0.80 0.74 0.30 0.47 0.53 0.42 0.17
#:     at the best neighbour    0.43 0.45 0.10 0.00 0.00 0.02 0.00   (0, +1)
#:
#: The disagreement at the declared crop is spread over the whole frame, not
#: concentrated at the top, because the camera had not settled — which is
#: exactly what a one-tile atlas offset looks like. So the run's own
#: map-change record is REQUIRED, not corroborating, and these shape numbers
#: only decide whether a banner is on top of it.
BANNER_TILES_W, BANNER_TILES_H = 5, 2     # the smallest block worth calling a banner
BANNER_INSIDE = 0.3                       # ...of which this much must disagree
BANNER_OUTSIDE = 0.9                      # ...while the rest of the frame agrees this well

#: What an atlas-wide recolour usually means, per game. Keyed by game because
#: nothing in the atlas records which palette variant it was rendered from —
#: `Game.time_of_day` lives in `scripts/render_gamemaps.py` and stops there.
#: If the atlas carried it, this could name the mismatch outright instead of
#: inferring it from how far the recolour reaches.
PALETTE_CAUSE = {
    "crystal-us":
        "a GBC recolours the whole world by the clock, and a render is of ONE time of "
        "day — `Game.time_of_day` in scripts/render_gamemaps.py, one of morn/day/nite/"
        "dark. An atlas rendered `nite` under daytime runs (or the reverse) looks "
        "exactly like this: every tile in the right place and every colour wrong.",
}
DEFAULT_PALETTE_CAUSE = (
    "every sampled map is a palette permutation of what the game drew, so this is a "
    "property of the atlas rather than of any one map — a palette variant, a colour "
    "depth, or a season chosen at render time.")

#: A window has to see at least this many comparable pixels to be scored at
#: all. Below it the turn is standing in a corner where the camera is mostly
#: border tiles, and the score is noise.
MIN_PIXELS = 4000


#: The acceptance band, pre-registered in ``artifacts/game-map-render/notes/verify.md``
#: before the first score was read. Kept here as ONE dict so the page and the
#: gate cannot drift: the script prints these numbers next to the verdict.
BAND = {
    "self_floor": 0.9999,     # below this the reader is lossy; quote nothing
    "aligned_match": 0.90,
    "suspect_match": 0.70,
    "spread_wrong_crop": 0.20,
    "spread_wrong_map": 0.20,
    "margin": 0.05,
    # the saturation guard: all games this high AND the wrong-crop control this
    # high too means the ceiling was measured, not the alignment
    "saturated_match": 0.995,
    "saturated_wrong_crop": 0.90,
}


class NoInstrument(Exception):
    """This game cannot be measured this way, and here is precisely why."""


class UnknownCamera(NoInstrument):
    """The atlas declares a camera this tool has no projection for."""


# --------------------------------------------------------------- tile -> pixel

@dataclass(frozen=True)
class TileProjection:
    """Where a map tile lands in the map's own PNG. **The swappable step.**

    Today every atlas is ``camera: "topdown"``, so this is a scale and a
    translate: tile ``(x, y)`` starts at pixel ``((x - ox) * tile_px,
    (y - oy) * tile_px)``. ``(ox, oy)`` is the ``png_origin`` a DS atlas
    declares (``png_frame: "map-local"`` — PNG pixel (0, 0) is tile
    ``png_origin``, so a source rect subtracts it); a GBA/GBC atlas has no such
    field and the subtraction is zero. Getting that subtraction wrong is the
    classic silent failure — the atlas loads, renders, and is off by a
    constant — which is exactly what the ``margin`` control is for.

    When the angled DS camera lands, the atlas will carry a projection (an
    affine 2x3) and ``camera`` will stop saying ``"topdown"``. The change is to
    give this class a matrix and a second implementation of :meth:`window`;
    every caller goes through :func:`projection_for`, which refuses an unknown
    camera rather than falling back here.
    """

    tile_px: int
    origin_tiles: tuple[int, int]
    camera: str = "topdown"

    def window(self, spec: ScreenSpec, x: int, y: int) -> tuple[int, int]:
        """Top-left PNG pixel of the camera window for a player on tile (x, y).

        May be negative near a map edge: the game draws border tiles out there
        and the map PNG does not have them. The caller clips.
        """
        ox, oy = self.origin_tiles
        return ((x - ox) * self.tile_px - spec.cam_x_tiles * spec.tile,
                (y - oy) * self.tile_px - spec.cam_y_px)


def projection_for(atlas: dict, entry: dict) -> TileProjection:
    """The tile -> pixel step for one map of one atlas, or a refusal.

    Reads ``camera`` from the atlas and fails loudly on anything it does not
    understand. It must never guess: a wrong projection produces a picture that
    loads, renders and is uniformly a little bit off, which is the failure mode
    that survived weeks of being cited as authority.
    """
    camera = atlas.get("camera")
    if camera != "topdown":
        raise UnknownCamera(
            f"the atlas declares camera={camera!r} and this tool only knows 'topdown'. "
            f"Tile (x, y) is no longer at pixel (x*tile_px, y*tile_px), so a crop taken "
            f"the top-down way would be silently wrong rather than obviously wrong. "
            f"Read the projection the atlas now carries (an affine 2x3, per the "
            f"angled-camera work) and give TileProjection.window() a branch for it.")

    frame = atlas.get("png_frame", "map")
    if frame == "map-local":
        origin = entry.get("png_origin")
        if origin is None:
            raise NoInstrument(
                "the atlas declares png_frame='map-local' but this map carries no "
                "png_origin, so there is no way to know what a source rect subtracts")
        ox, oy = int(origin[0]), int(origin[1])
    elif frame in ("map", None):
        ox, oy = 0, 0
    else:
        raise NoInstrument(f"unknown png_frame {frame!r}")

    return TileProjection(tile_px=int(atlas["tile_px"]), origin_tiles=(ox, oy),
                          camera=camera)


def screen_for(game: str, atlas: dict) -> ScreenSpec:
    """The game's screen model, or a refusal that names the reason.

    A :class:`ScreenSpec` asserts that the screen is an axis-aligned window of
    the tile grid. That is true of a GBA and a GBC and false of a DS field
    camera, so the DS games have no entry and get this message instead of an
    invented one.
    """
    spec = SPECS.get(game)
    if spec is not None:
        return spec
    if atlas.get("render") == "3d-ortho":
        raise NoInstrument(
            f"{game} has no flat-tile screen model, and could not have one. Its atlas "
            f"declares render='3d-ortho': the artwork is the cartridge's own 3D field "
            f"rendered STRAIGHT DOWN and orthographic. The emulator draws that same "
            f"field through the game's TILTED PERSPECTIVE camera, so a tree in the "
            f"frame leans and hides the ground behind it while the same tree in the "
            f"artwork is a flat footprint. The two pictures are of one world in two "
            f"projections; a per-pixel difference between them is not a measurement "
            f"of alignment, and printing one would be worse than printing nothing.")
    raise NoInstrument(
        f"{game} has no ScreenSpec in src/app/stitch.py, so where the world sits in "
        f"one of its frames is unknown. Measure it with "
        f"`scripts/stitch_maps.py --calibrate` (motion only, no render involved) and "
        f"add the spec.")


# ------------------------------------------------------------- reading a run

@dataclass(frozen=True)
class Placement:
    """One frame of a run and the map window it is claimed to show."""

    run: str
    turn: int
    key: str
    x: int
    y: int
    shot: Path
    #: the previous turn's position was on a DIFFERENT map, so this frame is
    #: the first after a warp — the game paints its area-name banner over it
    #: and the camera may not have finished scrolling
    entered: bool = False


def game_of(run_dir: Path) -> Optional[str]:
    """Which game a run played, from its own config's ROM path."""
    cfg = run_dir / "config.json"
    if not cfg.is_file():
        return None
    try:
        rom_path = json.loads(cfg.read_text()).get("emulator", {}).get("rom_path")
    except (OSError, ValueError, AttributeError):
        return None
    rom = rom_for_path(rom_path)
    return rom.game if rom else None


def turn_positions(run_dir: Path) -> dict[int, tuple[str, int, int]]:
    """``turn -> (map_key, x, y)`` for both key shapes the runs use.

    A GB/GBA trace carries ``map_group`` / ``map_num`` and spells a map
    ``"4:1"``. A DS trace carries ``map_id`` with both of those null, and the
    atlas spells the same map ``"342:0"`` — **the observed graphs spell it
    "342", and mismatching the two yields "no data" rather than an error**, so
    the ``:0`` is added here, once, next to the place that knows which shape it
    is reading.

    The referee's per-turn poll wins where a run has one; otherwise the
    per-input trace's ``end_tile``, all-or-nothing per run and never mixed, on
    the same reasoning as ``src.app.stitch.positions``. A turn whose trace
    changed map is dropped: that is a warp, and its last tile is not where the
    turn ended.
    """
    poll: dict[int, tuple[str, int, int]] = {}
    trace: dict[int, tuple[str, int, int]] = {}
    path = run_dir / "events.jsonl"
    if not path.is_file():
        return {}
    with path.open() as fh:
        for line in fh:
            if '"referee_position"' in line:
                e = json.loads(line)
                if e.get("type") == "referee_position" and e.get("x") is not None:
                    poll[int(e["turn"])] = (f"{int(e['map_group'])}:{int(e['map_num'])}",
                                            int(e["x"]), int(e["y"]))
            elif '"turn_input_trace"' in line:
                e = json.loads(line)
                if e.get("type") != "turn_input_trace":
                    continue
                end = e.get("end_tile")
                if not end or len(end) != 4 or end[2] is None or end[3] is None:
                    continue
                seen = [s for s in (e.get("samples") or [])
                        if isinstance(s, dict) and s.get("x") is not None]
                if end[0] is not None and end[1] is not None:
                    key = f"{int(end[0])}:{int(end[1])}"
                    moved = any(s.get("map_group") != end[0] or s.get("map_num") != end[1]
                                for s in seen)
                else:
                    ids = {s.get("map_id") for s in seen if s.get("map_id") is not None}
                    if len(ids) != 1:
                        continue          # warped, or the trace named no map at all
                    key = f"{int(ids.pop())}:0"    # the atlas's own spelling for a DS map
                    moved = False
                if moved:
                    continue
                trace[int(e["turn"])] = (key, int(end[2]), int(end[3]))
    return poll or trace


def battle_turns(run_dir: Path) -> set[int]:
    """Turns whose per-input trace saw a battle — their frame draws no map."""
    out: set[int] = set()
    path = run_dir / "events.jsonl"
    if not path.is_file():
        return out
    with path.open() as fh:
        for line in fh:
            if '"turn_input_trace"' not in line:
                continue
            e = json.loads(line)
            if e.get("type") != "turn_input_trace":
                continue
            if any(isinstance(s, dict) and s.get("in_battle") for s in (e.get("samples") or [])):
                out.add(int(e["turn"]))
    return out


def battle_flag_warning(run_dir: Path) -> Optional[str]:
    """Whether this run's in-battle flag looks like it never fired.

    ``battle_turns`` is only as good as the probe that filled ``in_battle``, and
    the probe silently returns False when it could not read the address.
    Measured 2026-09-21 across the v2 runs: two of them —
    ``2026-09-19_22-48-56_config-v2-emerald`` (386 traces) and
    ``…_config-v2-crystal`` (383 traces) — report ``in_battle`` on **zero**
    samples while later runs of the same games report it on dozens, so the flag
    was dead in those two recordings and every battle frame they hold is
    offered to this tool as a map frame.

    A short clean run legitimately has no battles, so this warns rather than
    refuses, and the nine-offset floor is what actually keeps those frames out
    of the median.
    """
    traces = flagged = 0
    path = run_dir / "events.jsonl"
    if not path.is_file():
        return None
    with path.open() as fh:
        for line in fh:
            if '"turn_input_trace"' not in line:
                continue
            e = json.loads(line)
            if e.get("type") != "turn_input_trace":
                continue
            traces += 1
            if any(isinstance(s, dict) and s.get("in_battle") for s in (e.get("samples") or [])):
                flagged += 1
    if traces >= 100 and flagged == 0:
        return (f"{run_dir.name}: {traces} traces and not one sample with in_battle set — "
                f"this run's battle probe never fired, so its battle frames reach this "
                f"tool unfiltered (and reach the stitcher's atlas unfiltered too)")
    return None


def screenshots(run_dir: Path) -> dict[int, Path]:
    """``turn -> the frame the model was shown on that turn``."""
    import re
    out: dict[int, Path] = {}
    for p in sorted((run_dir / "screenshots").glob("*.png")):
        m = re.search(r"turn_(\d+)", p.name)
        if m:
            out[int(m.group(1))] = p
    return out


def placements(run_dir: Path) -> list[Placement]:
    """Every frame of a run that can be placed on a map.

    **The pairing is the part that is easy to get wrong.** The frame shown ON
    turn N is captured BEFORE turn N's buttons, so it shows the tile the
    referee polled after turn N-1. Pairing it with turn N's own position
    compares the frame against where the run ended up — a different place
    entirely on any turn that moved.
    """
    pos, shots, fights = turn_positions(run_dir), screenshots(run_dir), battle_turns(run_dir)
    out = []
    for turn in sorted(shots):
        if turn - 1 not in pos or turn in fights or turn - 1 in fights:
            continue
        key, x, y = pos[turn - 1]
        # Is this the first frame after a warp? The run's own positions say so,
        # with no reference to any pixel: the previous turn was on another map.
        # That is the signal the map-entry exclusion is REQUIRED to have — the
        # banner and the unsettled camera cannot be read off the frame alone.
        before = pos.get(turn - 2)
        out.append(Placement(run_dir.name, turn, key, x, y, shots[turn],
                             entered=before is not None and before[0] != key))
    return out


# ------------------------------------------------------------- the comparison

def spread(n: int, items: list) -> list:
    """``n`` items spread evenly across a list, endpoints included.

    Sampling the first ``n`` turns of a run measures the first room the run was
    in. Spreading them measures the run.
    """
    if n <= 0 or len(items) <= n:
        return list(items)
    step = (len(items) - 1) / (n - 1)
    return [items[round(i * step)] for i in range(n)]


def on_ladder(a: np.ndarray, bits: int) -> np.ndarray:
    """A picture reduced to the colour the CONSOLE can actually hold.

    **This is not a loosening, it is the removal of an encoding.** A GBA and a
    GBC both write 5 bits per channel. A PNG has 8, so whoever wrote the PNG
    chose how to fill the other 3, and the two writers in this repo chose
    differently: the renders expand ``v`` to ``(v << 3) | (v >> 2)`` (the
    replicating expansion — 14 becomes 115) while SkyEmu emits a plain
    ``v << 3`` (14 becomes 112). That is a constant 0-7 per channel on EVERY
    pixel of every map, which is 3-6 past a ±1 tolerance, and it has nothing
    whatever to do with whether the map lines up.

    Measured 2026-09-21, and it is why this function exists: compared as raw
    bytes, Emerald's median agreement was **0.0012** and FireRed's 0.0868 —
    near-total disagreement on artwork that is in fact correct — and the only
    thing standing between that and a published 0.98 was a per-colour lookup
    fitted from the frame itself. A metric whose entire signal comes from a
    lookup fitted on the data is not a measurement. Reduced to the console's
    own 32 levels, the two encodings are the same number and the lookup has
    almost nothing left to do, which is where it belongs: reserved for the
    runtime recolours it was written for.

    The raw-byte agreement is still reported, as ``raw8``, so the encoding
    mismatch stays visible instead of being normalised into silence.
    """
    return (a >> (8 - bits)).astype(np.int16)


def compare(shot: np.ndarray, ref: np.ndarray, ox: int, oy: int, keep: np.ndarray,
            colour_map: bool = True, bits: int = 5) -> Optional[dict]:
    """One frame against one crop of one map PNG.

    ``shot`` is the frame at the console's native resolution; ``ref`` is the
    whole map PNG; ``(ox, oy)`` is the window's top-left in PNG pixels and may
    be negative or run off the edge, in which case only the overlap is scored.

    Both sides are put on the console's own colour ladder first (see
    :func:`on_ladder`) and then have to be EQUAL — at 32 levels per channel
    there is no rounding left to tolerate.

    A per-colour lookup is fitted on top, and used only if it lowers the
    residual. Some of what a game draws is a runtime recolour that is not in
    the tiles — FireRed's ``WEATHER_SHADE`` darkens every palette entry of
    Viridian Forest, so a CORRECT render of it differs from the screen on
    nearly every pixel. No lookup moves a tree onto a roof, so fitting one
    separates "the colours were transformed" from "the terrain is wrong".
    **It is offered to the controls on exactly the same terms**, so it cannot
    flatter the correct arm into a spread it has not earned.
    """
    h, w = keep.shape
    fy0, fx0 = max(0, -oy), max(0, -ox)
    fy1, fx1 = min(h, ref.shape[0] - oy), min(w, ref.shape[1] - ox)
    if fy1 <= fy0 or fx1 <= fx0:
        return None
    mask = np.zeros_like(keep)
    mask[fy0:fy1, fx0:fx1] = keep[fy0:fy1, fx0:fx1]
    if int(mask.sum()) < MIN_PIXELS:
        return None
    want = np.zeros((h, w, 3), dtype=np.int16)
    want[fy0:fy1, fx0:fx1] = ref[oy + fy0: oy + fy1, ox + fx0: ox + fx1]

    qw, qs = on_ladder(want, bits), on_ladder(shot, bits)
    bad = (qw != qs).any(axis=2)
    match = float((~bad[mask]).mean())
    direct, via = match, "direct"
    raw8 = float((np.abs(want - shot).max(axis=2)[mask] <= TOLERANCE).mean())
    collapse = None
    if colour_map and match < 1.0:
        mapped = fit_colour_map(qw, qs, mask)
        n_in, n_out = _palette(qw, mask), _palette(mapped, mask)
        collapse = n_in / max(n_out, 1)
        shifted = (mapped != qs).any(axis=2)
        via_score = float((~shifted[mask]).mean())
        injective = n_out >= max(2, LOOKUP_KEEPS * n_in)
        if via_score > match and injective:
            match, via, bad = via_score, "colour map", shifted
        elif via_score > match:
            via = f"colour map REFUSED ({n_in}->{n_out} colours)"
    return {"match": match, "direct": direct, "raw8": raw8, "via": via,
            "collapse": collapse,
            "px": int(mask.sum()), "coverage": float(mask.sum() / keep.sum()),
            "want": want, "mask": mask, "bad": bad}


def _palette(a: np.ndarray, mask: np.ndarray) -> int:
    """How many distinct colours a picture uses inside the mask."""
    f = a.reshape(-1, 3)[mask.reshape(-1)]
    codes = (f[:, 0].astype(np.int32) << 16) | (f[:, 1].astype(np.int32) << 8) | f[:, 2]
    return int(np.unique(codes).size)


def banner_block(bad: np.ndarray, mask: np.ndarray, tile: int) -> Optional[tuple]:
    """The area-name banner, as a block of disagreement at the top-left corner.

    A SHAPE statement, never a score one: the block has to be substantially
    wrong while everything outside it is substantially right. A genuine
    misalignment has no such block — once the right offset is found it is
    clean everywhere, top included — and a frame that matches nothing anywhere
    has no clean outside.

    Returns ``(w_tiles, h_tiles, inside_bad, outside_agree)`` for the block
    that hugs the banner most tightly — the qualifying one whose interior
    disagrees most — or None. Taking the LARGEST qualifying block instead
    reports the banner as the whole top three rows at 33% wrong, which is true
    and tells the reader nothing; the tight block is 9x3 at 48%.
    """
    best = None
    for h in range(BANNER_TILES_H, 5):
        for w in range(BANNER_TILES_W, max(BANNER_TILES_W, mask.shape[1] // tile) + 1):
            sub = mask[:h * tile, :w * tile]
            if not sub.any():
                continue
            outside = mask.copy()
            outside[:h * tile, :w * tile] = False
            if not outside.any():
                continue
            inside_bad = float(bad[:h * tile, :w * tile][sub].mean())
            outside_ok = 1.0 - float(bad[outside].mean())
            if inside_bad >= BANNER_INSIDE and outside_ok >= BANNER_OUTSIDE:
                cand = (w, h, inside_bad, outside_ok)
                if best is None or inside_bad > best[2]:
                    best = cand
    return best


def is_entry_frame(entered: bool, banner: Optional[tuple]) -> bool:
    """Both signals, or nothing is excluded.

    ``entered`` comes from the run's own positions — the previous turn was on
    another map — and involves no pixel. ``banner`` is the shape of what is
    left over at the best-fitting crop. The first alone would drop every
    legitimate first-frame-on-a-new-map, including a genuinely misaligned one;
    the second alone would drop anything with a bright rectangle in its corner
    and would give the exclusion room to swallow real defects. Kept as its own
    function so the conjunction can be stated once and tested on its own.
    """
    return bool(entered and banner)


def self_control(want: np.ndarray, mask: np.ndarray, spec: ScreenSpec,
                 shot_size: tuple[int, int]) -> float:
    """The crop pushed out through the SCREENSHOT reader and back, against itself.

    Not a tautology: the round trip is the run's own screenshot pipeline —
    upscale to the size the harness saved at, then :func:`to_native`, which
    decimates NEAREST and inverts the harness's grid overlay according to
    ``spec.grid_overlay``. A reader that inverts an overlay that was never
    applied recolours the two pixels straddling every tile boundary, and the
    crop stops equalling itself. That is the bug that scored FireRed 74-82% for
    weeks while being cited as authority.
    """
    img = Image.fromarray(want.astype(np.uint8), "RGB")
    if img.size != shot_size:
        img = img.resize(shot_size, Image.NEAREST)
    back = to_native(img, spec).astype(np.int16)
    same = on_ladder(back, spec.colour_bits) == on_ladder(want, spec.colour_bits)
    return float(same.all(axis=2)[mask].mean())


def other_map(atlas_maps: dict, key: str) -> Optional[str]:
    """A DIFFERENT map of the same atlas, for the wrong-map control.

    The largest other one: a big map is the hardest case for the control to
    pass, because it has the most chances to happen to look like the frame.
    """
    others = [(v.get("width", 0) * v.get("height", 0), k)
              for k, v in atlas_maps.items() if k != key]
    return max(others)[1] if others else None


def score_turn(p: Placement, spec: ScreenSpec, rspec: ScreenSpec, atlas: dict,
               maps_dir: Path, keep: np.ndarray, refs: dict, colour_map: bool = True
               ) -> Optional[dict]:
    """The correct crop and all four controls, for one turn."""
    entry = atlas["maps"].get(p.key)
    if entry is None:
        return None
    proj = projection_for(atlas, entry)
    ref = load_ref(refs, maps_dir, entry)
    with Image.open(p.shot) as im:
        shot_size = im.size
        shot = to_native(im, rspec).astype(np.int16)

    ox, oy = proj.window(spec, p.x, p.y)
    got = compare(shot, ref, ox, oy, keep, colour_map, spec.colour_bits)
    if got is None:
        return None

    # wrong crop: the same map, four tiles off in both axes
    bad = compare(shot, ref, ox + WRONG_CROP[0] * proj.tile_px,
                  oy + WRONG_CROP[1] * proj.tile_px, keep, colour_map, spec.colour_bits)

    # wrong map: the same frame against a different map of the same atlas,
    # its window clamped so the frame sees as much of it as it can
    wrong_key = other_map(atlas["maps"], p.key)
    wrong = None
    if wrong_key is not None:
        wentry = atlas["maps"][wrong_key]
        wref = load_ref(refs, maps_dir, wentry)
        wx = min(max(0, ox), max(0, wref.shape[1] - shot.shape[1]))
        wy = min(max(0, oy), max(0, wref.shape[0] - shot.shape[0]))
        wrong = compare(shot, wref, wx, wy, keep, colour_map, spec.colour_bits)

    # margin: the best of the eight one-tile neighbours
    neighbours, candidates = [], [got]
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            n = compare(shot, ref, ox + dx * proj.tile_px, oy + dy * proj.tile_px,
                        keep, colour_map, spec.colour_bits)
            if n is not None:
                neighbours.append(n["match"])
                candidates.append(n)

    # The area-name banner, looked for at the BEST of the nine candidates and
    # not at the declared one: on an entry frame the camera may not have
    # settled, so the declared crop is off and its residual is spread over the
    # whole frame. Only paired with the run's own map-change record does this
    # exclude anything — see BANNER_INSIDE.
    fit = max(candidates, key=lambda c: c["match"])
    banner = banner_block(fit["bad"], fit["mask"], spec.tile)

    return {
        "turn": p.turn, "run": p.run,
        # What the sheet prints on the panel, under names a reader can guess.
        # `key` is kept because everything else in this file joins on it.
        "map": p.key, "map_name": entry.get("name"), "key": p.key,
        "name": entry.get("name"),
        # the atlas's own word for whether this map's colours come from the world
        "indoor": bool(entry.get("indoor")),
        "tile": [p.x, p.y], "window": [ox, oy],
        "match": got["match"], "direct": got["direct"], "raw8": got["raw8"],
        "via": got["via"], "collapse": got["collapse"],
        "px": got["px"], "coverage": got["coverage"],
        "wrong_crop": bad["match"] if bad else None,
        "wrong_map": wrong["match"] if wrong else None,
        "wrong_map_key": wrong_key if wrong else None,
        "neighbour_best": max(neighbours) if neighbours else None,
        "margin": (got["match"] - max(neighbours)) if neighbours else None,
        # Does this frame show THIS MAP at all? If the best of the nine
        # candidate crops is still under the floor, nothing fits, and the turn
        # is evidence about the frame rather than about the artwork.
        "map_frame": max([got["match"]] + neighbours) >= MAP_FRAME_FLOOR,
        # ...and is it the first frame after a warp, with the area-name banner
        # over it and the camera perhaps still scrolling? BOTH the run's own
        # record and the banner's shape have to say so.
        "entered": p.entered,
        "banner": banner,
        "entry_frame": is_entry_frame(p.entered, banner),
        "self": self_control(got["want"], got["mask"], rspec, shot_size),
        "_shot": shot, "_want": got["want"], "_mask": got["mask"], "_bad": got["bad"],
    }


def load_ref(cache: dict, maps_dir: Path, entry: dict) -> np.ndarray:
    key = entry["file"]
    if key not in cache:
        cache[key] = np.asarray(Image.open(maps_dir / key).convert("RGB"), dtype=np.int16)
    return cache[key]


# ------------------------------------------------------------------- verdict

def median(xs: Iterable) -> Optional[float]:
    vals = sorted(v for v in xs if v is not None)
    if not vals:
        return None
    n = len(vals)
    return float(vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2)


def verdict(all_rows: list[dict]) -> dict:
    """The pre-registered band, applied. Never a number without its controls.

    Turns whose frame does not show the map at all are set aside first and
    counted, not averaged in. Folding them in would mean reporting "the map is
    misaligned" about a picture of a Zigzagoon.
    """
    rows = [r for r in all_rows
            if r.get("map_frame", True) and not r.get("entry_frame")]
    skipped = [r for r in all_rows if not r.get("map_frame", True)]
    entered = [r for r in all_rows
               if r.get("map_frame", True) and r.get("entry_frame")]
    m = median(r["match"] for r in rows)
    dm = median(r["direct"] for r in rows)
    r8 = median(r["raw8"] for r in rows)
    wc = median(r["wrong_crop"] for r in rows)
    wm = median(r["wrong_map"] for r in rows)
    mg = median(r["margin"] for r in rows)
    sf = min((r["self"] for r in rows), default=None)

    out = {"turns": len(rows), "match": m, "direct": dm, "raw8": r8, "wrong_crop": wc,
           "wrong_map": wm, "margin": mg, "self": sf, "reasons": [],
           "not_map_frames": [r["turn"] for r in skipped], "sampled": len(all_rows),
           "entry_frames": [{"turn": r["turn"], "map": r["key"], "name": r.get("name"),
                             "tile": r["tile"], "match": r["match"],
                             "margin": r["margin"], "banner": r["banner"]}
                            for r in entered]}
    if m is None:
        out["verdict"] = "NO DATA"
        out["reasons"].append(
            f"none of the {len(all_rows)} sampled turns shows the map it claims: no crop "
            f"of any of the nine candidate offsets reaches {MAP_FRAME_FLOOR}. That is a "
            f"statement about the frames, not about the artwork — almost always a battle "
            f"filter that did not fire.")
        return out
    if sf is None or sf < BAND["self_floor"]:
        out["verdict"] = "READER BROKEN"
        out["reasons"].append(
            f"self-control {sf:.4f} < {BAND['self_floor']} — the screenshot reader is "
            f"lossy, so no other number here may be quoted")
        return out

    fails = []
    if m < BAND["suspect_match"]:
        fails.append(f"match {m:.4f} < {BAND['suspect_match']}")
        out["verdict"] = "MISALIGNED"
    if mg is not None and mg <= 0:
        fails.append(f"margin {mg:+.4f} <= 0 — a neighbouring crop fits at least as well")
        out["verdict"] = "MISALIGNED"
    if out.get("verdict") == "MISALIGNED":
        out["reasons"] = fails
        return out

    if m < BAND["aligned_match"]:
        fails.append(f"match {m:.4f} < {BAND['aligned_match']}")
    if wc is None or m - wc < BAND["spread_wrong_crop"]:
        fails.append(f"match - wrong crop {(m - wc) if wc is not None else float('nan'):+.4f} "
                     f"< {BAND['spread_wrong_crop']} — the metric barely tells a 4-tile "
                     f"offset from the truth here")
    if wm is None or m - wm < BAND["spread_wrong_map"]:
        fails.append(f"match - wrong map {(m - wm) if wm is not None else float('nan'):+.4f} "
                     f"< {BAND['spread_wrong_map']}")
    if mg is None or mg < BAND["margin"]:
        fails.append(f"margin {mg:+.4f} < {BAND['margin']}" if mg is not None
                     else "no neighbour could be scored, so margin is unknown")
    out["verdict"] = "ALIGNED" if not fails else "SUSPECT"
    out["reasons"] = fails
    return out


def colour_verdict(game: str, rows: list[dict]) -> dict:
    """Whether the artwork is the right COLOUR, which is not whether it lines up.

    Geometry and colour are two claims and ALIGNED was making only one of them:
    a Crystal atlas rendered `nite` scores 0.9923 against a daytime run with a
    raw agreement of 0.0000 — every tile where it should be, every colour
    wrong, and one word covering both.

    **Per turn the two causes are the same observation** — a palette
    permutation with a near-zero raw agreement and a near-perfect fitted one —
    and this function does not pretend otherwise. What separates them is
    SCOPE. `WEATHER_SHADE` belongs to Viridian Forest; a clock belongs to the
    world and recolours every map the run walked. So the verdict is taken over
    distinct MAPS, and a sample holding only one map has no scope to measure
    and is reported as ambiguous rather than guessed at.
    """
    by_map: dict[str, list[bool]] = {}
    for r in rows:
        # The GAP the lookup closed is the colour statement. The LEVEL it
        # reached is not: that carries the sprites and the animated tiles too.
        recoloured = r["match"] - r["direct"] >= RECOLOURED_GAP
        by_map.setdefault(r["key"], []).append(recoloured)
    names = {r["key"]: r.get("name") for r in rows}
    outdoor = {r["key"] for r in rows if not r.get("indoor")}
    hit = sorted(k for k, v in by_map.items() if any(v))
    hit_out = sorted(k for k in hit if k in outdoor)
    n_maps, n_out = len(by_map), len(outdoor)

    def label(k: str) -> str:
        return f"{k}" + (f" {names[k]}" if names.get(k) else "")

    out = {"maps": n_maps, "outdoor_maps": n_out, "recoloured_maps": hit, "fails": False}
    if not hit:
        out["colour"] = "COLOURS MATCH"
        out["why"] = ""
        return out

    # The scope unit is the maps whose colours come from the WORLD. A clock or
    # a season reaches those and only those: a gen-2 indoor map declares
    # PALETTE_DAY and is the same picture at midnight, so counting it dilutes
    # the very signal being measured. Measured 2026-09-21 on a daytime Crystal
    # run against the `nite` atlas: both outdoor maps recoloured (raw 0.0000
    # and 0.0002) and both indoor maps clean (0.9255, 0.9806) — over all four
    # that is "1 of 4", which reads as ordinary weather; over the outdoor two
    # it is "all of them", which is the truth.
    if n_out >= 2 and len(hit_out) == n_out:
        out["colour"] = "COLOURS DIFFER EVERYWHERE"
        out["fails"] = True
        out["why"] = (
            f"every one of the {n_out} outdoor maps sampled is recoloured "
            f"({', '.join(label(k) for k in hit_out)}), so this is a property of the "
            f"atlas and not of any one map. " + PALETTE_CAUSE.get(game, DEFAULT_PALETTE_CAUSE)
            + (f" The {n_maps - n_out} indoor map(s) sampled are clean, which is "
               f"consistent: an indoor map takes a fixed palette and is the same "
               f"picture whatever the world is doing." if n_maps > n_out else ""))
        return out

    # Ambiguity is about the OUTDOOR maps too, not about all of them. A sample
    # of one route and two houses, with the route recoloured, is "1 of 3
    # recoloured" — which reads as ordinary weather and is exactly how the
    # daytime Crystal run slipped through at --turns 20. The clean houses are
    # not evidence: a clock never touches them.
    if hit and len(hit_out) == n_out:
        out["colour"] = "COLOURS DIFFER, CAUSE AMBIGUOUS"
        out["fails"] = True
        out["why"] = (
            (f"the only map sampled that takes its colours from the world "
             f"({label(hit_out[0])}) is a palette permutation of what the game drew"
             if n_out == 1 else
             f"{len(hit)} sampled map(s) are a palette permutation of what the game "
             f"drew and NOT ONE outdoor map was sampled") +
            f", so there is no scope to measure and the two causes are the same "
            f"observation here: a runtime recolour of that one place (weather, a cave "
            f"— ordinary, and the artwork is meant to absorb it), or the whole atlas "
            f"being the wrong palette variant. Raise --turns until the sample spans "
            f"two or more outdoor maps. Failed rather than passed because a sample too "
            f"narrow to tell the two apart is not a pass.")
        return out

    out["colour"] = "COLOURS DIFFER ON SOME MAPS"
    out["why"] = (
        f"{len(hit)} of {n_maps} sampled maps are recoloured and the rest are not "
        f"({', '.join(label(k) for k in hit)}), and {len(hit_out)} of the {n_out} "
        f"outdoor ones — so the recolour belongs to those maps, not to the world: "
        f"weather, a cave, a dark room. The artwork is supposed to absorb it and the "
        f"fitted lookup is supposed to see through it, so this does not fail.")
    return out


def dominant_recolour(rows: list[dict]) -> Optional[str]:
    """The commonest colour the artwork uses, and what the game drew there.

    One concrete pair beats any amount of prose about palettes: "the map's
    120,112,192 is the game's 176,248,80" is indigo against green and needs no
    explaining.
    """
    best = None
    for r in rows:
        if r["match"] - r["direct"] < RECOLOURED_GAP or r.get("indoor") \
                or "_want" not in r:
            continue
        want, shot, mask = r["_want"], r["_shot"], r["_mask"]
        w = want.reshape(-1, 3)[mask.reshape(-1)]
        sh = shot.reshape(-1, 3)[mask.reshape(-1)]
        if not len(w):
            continue
        codes = (w[:, 0].astype(np.int32) << 16) | (w[:, 1].astype(np.int32) << 8) | w[:, 2]
        vals, counts = np.unique(codes, return_counts=True)
        top = int(vals[counts.argmax()])
        where = codes == top
        scodes = ((sh[:, 0].astype(np.int32) << 16) | (sh[:, 1].astype(np.int32) << 8)
                  | sh[:, 2])
        sv, sc = np.unique(scodes[where], return_counts=True)
        got = int(sv[sc.argmax()])
        rgb = lambda c: f"{(c >> 16) & 0xFF},{(c >> 8) & 0xFF},{c & 0xFF}"   # noqa: E731
        cand = (int(counts.max()), f"the map's commonest colour {rgb(top)} is "
                                   f"{rgb(got)} on screen")
        if best is None or cand[0] > best[0]:
            best = cand
    return best[1] if best else None


def saturated(results: dict) -> bool:
    """Every game at the ceiling AND its wrong-crop control too: void the band."""
    scored = [v for v in results.values() if v.get("match") is not None]
    if not scored:
        return False
    return all(v["match"] >= BAND["saturated_match"]
               and (v["wrong_crop"] or 0) >= BAND["saturated_wrong_crop"] for v in scored)


# --------------------------------------------------------------------- sheet

def font(size: int, bold: bool = False):
    for p in (f"/System/Library/Fonts/Supplemental/Arial{' Bold' if bold else ''}.ttf",):
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            pass
    return ImageFont.load_default()


def zoom(a: np.ndarray, z: int) -> Image.Image:
    return Image.fromarray(np.repeat(np.repeat(a.astype(np.uint8), z, 0), z, 1), "RGB")


def diff_panel(want: np.ndarray, mask: np.ndarray, bad: np.ndarray, z: int) -> Image.Image:
    """The crop at a quarter brightness, red where it disagrees, grey where it cannot say."""
    a = (want.astype(np.float64) * 0.28).astype(np.uint8)
    a[mask & bad] = (235, 60, 70)
    a[~mask] = (52, 54, 60)
    return zoom(a, z)


def sheet(game: str, rows: list[dict], v: dict, spec: ScreenSpec, runs: list[str],
          atlas: dict, out: Path, z: int = 2) -> Path:
    """The rendered crop beside the real frame, at real size, one row per turn.

    A controls strip sits on top: one turn's real frame, its correct crop, the
    same crop taken 4 tiles off, and a different map entirely — so the reader
    can see what "clearly worse" looks like before being asked to believe a
    number about it.
    """
    W, H = spec.width * z, spec.height * z
    PAD, GAP, LAB, HEAD = 26, 14, 34, 196
    ctl = max(rows, key=lambda r: r["match"])
    CTLH = LAB + H + GAP + 22

    width = PAD * 2 + W * 4 + GAP * 3
    height = PAD + HEAD + CTLH + len(rows) * (LAB + H + GAP) + PAD
    img = Image.new("RGB", (width, height), (18, 20, 26))
    g = ImageDraw.Draw(img)

    ok = {"ALIGNED": (120, 205, 150), "SUSPECT": (235, 190, 110)}.get(v["geometry"],
                                                                      (235, 110, 110))
    if v.get("fails"):
        ok = (235, 150, 100)
    g.text((PAD, PAD), f"{game} — does the shipped map line up with the game?",
           font=font(22, True), fill=(240, 242, 248))
    g.text((PAD, PAD + 30), f"{v['verdict']}", font=font(18, True), fill=ok)
    g.text((PAD + 300, PAD + 32),
           f"match {v['match']:.4f} (before any colour fit {v['direct']:.4f})   ·   "
           f"wrong crop {fmt(v['wrong_crop'])}   ·   wrong map {fmt(v['wrong_map'])}   ·   "
           f"self {v['self']:.4f}   ·   margin {fmt(v['margin'], sign=True)}",
           font=font(14), fill=(200, 206, 218))
    g.text((PAD, PAD + 56),
           f"medians over {v['turns']} turns spread across {len(runs)} run(s): "
           + ", ".join(runs[:3]) + (" …" if len(runs) > 3 else ""),
           font=font(12), fill=(150, 158, 174))
    g.text((PAD, PAD + 74),
           f"band (pre-registered in {NOTES}): ALIGNED needs match >= "
           f"{BAND['aligned_match']}, match-wrongcrop >= {BAND['spread_wrong_crop']}, "
           f"match-wrongmap >= {BAND['spread_wrong_map']}, margin >= {BAND['margin']}, "
           f"self >= {BAND['self_floor']}",
           font=font(12), fill=(150, 158, 174))
    g.text((PAD, PAD + 92),
           ("; ".join(v["reasons"])[:200] if v["reasons"] else
            "every clause of the geometry band cleared"),
           font=font(12), fill=(230, 170, 120) if v["reasons"] else (140, 175, 150))
    colour_line = (f"colour: {v.get('colour', '?')} — "
                   f"{len(v.get('recoloured_maps', []))} of {v.get('maps', 0)} sampled "
                   f"maps are a palette permutation of what the game drew. "
                   + (v.get("recolour_example") or "")).strip()
    g.text((PAD, PAD + 128), colour_line[:230], font=font(13, True),
           fill=(235, 150, 100) if v.get("fails") else (140, 175, 150))
    for i, line in enumerate(wrap(v.get("why", ""), 168)[:3]):
        g.text((PAD, PAD + 146 + i * 14), line, font=font(11), fill=(160, 166, 180))
    g.text((PAD, PAD + 110),
           f"camera {atlas.get('camera')!r} · png_frame {atlas.get('png_frame', 'map')!r} · "
           f"tile_px {atlas['tile_px']} · compared at the console's {spec.colour_bits} bits "
           f"per channel (raw 8-bit bytes agree on only {v['raw8']:.4f} — the renders write "
           f"5-bit colour as (v<<3)|(v>>2) and SkyEmu writes v<<3) · "
           f"panels at {z}x the console's {spec.width}x{spec.height}",
           font=font(12), fill=(120, 128, 144))

    y = PAD + HEAD
    g.text((PAD, y),
           f"Controls, on turn {ctl['turn']} — one frame, the crop that claims to be it, "
           f"and two that are wrong. A metric that cannot separate these three scores "
           f"means nothing however high it reads.",
           font=font(15, True), fill=(228, 232, 240))
    y += 22
    strip = [
        (ctl["_shot"], "THE GAME — the frame the model was shown", (200, 206, 218)),
        (ctl["_want"], f"CORRECT crop — {ctl['match']:.4f}", (130, 200, 150)),
        (ctl["_wrong_crop_img"], f"WRONG crop, {WRONG_CROP[0]}x{WRONG_CROP[1]} tiles off — "
                                 f"{fmt(ctl['wrong_crop'])}", (225, 150, 120)),
        (ctl["_wrong_map_img"], f"WRONG map {ctl['wrong_map_key']} — "
                                f"{fmt(ctl['wrong_map'])}", (225, 120, 120)),
    ]
    for i, (arr, cap, col) in enumerate(strip):
        x = PAD + i * (W + GAP)
        g.text((x, y), cap, font=font(12), fill=col)
        if arr is not None:
            img.paste(zoom(arr, z), (x, y + 18))
        g.rectangle([x - 1, y + 17, x + W, y + 18 + H], outline=(58, 62, 70))
    y += CTLH - 22

    for r in rows:
        shown = r.get("map_frame", True) and not r.get("entry_frame")
        g.text((PAD, y),
               f"turn {r['turn']:>4}   map {r['key']}"
               + (f" {r['name']}" if r.get("name") else "")
               + f"   tile ({r['tile'][0]}, {r['tile'][1]})   window {r['window']}"
               + ("" if shown else
                  ("   —   MAP-ENTRY FRAME: the run says this is the first turn on this "
                   "map and the area-name banner is over it, camera possibly still "
                   "scrolling. EXCLUDED from the median"
                   if r.get("entry_frame") else
                   "   —   NOT A MAP FRAME: no crop of any of the nine offsets fits, so "
                   "this frame is a battle / menu / fade, and it is EXCLUDED from the "
                   "median")),
               font=font(13, True), fill=(222, 228, 240) if shown else (235, 170, 110))
        g.text((PAD, y + 16),
               f"match {r['match']:.4f} (before colour fit {r['direct']:.4f}, raw8 "
               f"{r['raw8']:.4f}, via {r['via']}) · "
               f"wrong crop {fmt(r['wrong_crop'])} · wrong map {fmt(r['wrong_map'])} · "
               f"margin {fmt(r['margin'], sign=True)} · self {r['self']:.4f} · "
               f"{r['px']} px ({r['coverage']:.0%} of the window)",
               font=font(11), fill=(150, 158, 174))
        for i, (arr, cap) in enumerate((
                (r["_shot"], "the game"), (r["_want"], "the map we ship"), (None, "where they differ"))):
            x = PAD + i * (W + GAP)
            panel = diff_panel(r["_want"], r["_mask"], r["_bad"], z) if arr is None else zoom(arr, z)
            img.paste(panel, (x, y + LAB))
            g.rectangle([x - 1, y + LAB - 1, x + W, y + LAB + H], outline=(58, 62, 70))
            g.text((x + 3, y + LAB + H - 15), cap, font=font(11), fill=(210, 214, 224))
        y += LAB + H + GAP

    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out


def fmt(v: Optional[float], sign: bool = False) -> str:
    if v is None:
        return "n/a"
    return f"{v:+.4f}" if sign else f"{v:.4f}"


# ----------------------------------------------------------------------- main

def run_game(game: str, runs: list[Path], turns: int, z: int, out_dir: Path,
             colour_map: bool = True) -> dict:
    """Score one game's runs, draw its sheet, return the summary."""
    maps_dir = MAPS_ROOT / game
    if not (maps_dir / "index.json").is_file():
        return {"game": game, "verdict": "NO ATLAS", "why": f"no atlas at {maps_dir}"}
    atlas = json.loads((maps_dir / "index.json").read_text())

    placed = [p for r in runs for p in placements(r)]
    in_atlas = [p for p in placed if p.key in atlas["maps"]]
    try:
        spec = screen_for(game, atlas)
        if in_atlas:
            # the camera gate, asked once before any pixel is read
            projection_for(atlas, atlas["maps"][in_atlas[0].key])
    except NoInstrument as exc:
        # Say how far the pipeline DID get. Everything up to the pixels works for
        # these games — the run reader, the "342" -> "342:0" map-key spelling, the
        # png_origin subtraction — so the refusal is about the projection alone and
        # not about missing plumbing.
        return {"game": game, "verdict": "NO INSTRUMENT",
                "why": f"{str(exc)}  [{len(placed)} turns of {len(runs)} run(s) were "
                       f"placeable and {len(in_atlas)} of those name a map this atlas "
                       f"ships, so everything up to the projection works; only the "
                       f"pixel comparison is refused]",
                "placeable_turns": len(placed), "turns_on_atlas_maps": len(in_atlas),
                "runs": [r.name for r in runs]}

    refs: dict = {}
    keep = keep_mask(spec)
    rows: list[dict] = []
    # The spec is resolved PER RUN, not per game: ``screenshot.grid_overlay`` is a
    # recording setting, a set of runs can hold both kinds, and reading it off the
    # game's default is the bug that scored FireRed 74-82% for weeks.
    where = {r.name: r for r in runs}
    for p in spread(turns, in_atlas):
        rspec = run_spec(spec, where[p.run])
        r = score_turn(p, spec, rspec, atlas, maps_dir, keep, refs, colour_map)
        if r is not None:
            rows.append(r)
    if not rows:
        return {"game": game, "verdict": "NO DATA",
                "why": f"{len(placed)} placeable turns, {len(in_atlas)} on maps the atlas "
                       f"has, none with a window big enough to score "
                       f"(needs {MIN_PIXELS} comparable px)",
                "runs": [r.name for r in runs]}

    # the control images, for the strip at the top of the sheet
    ctl = max(rows, key=lambda r: r["match"])
    attach_control_images(ctl, spec, atlas, maps_dir, refs)

    v = verdict(rows)
    v["game"] = game
    v["runs"] = [r.name for r in runs]
    v["warnings"] = [w for w in (battle_flag_warning(r) for r in runs) if w]
    # Colour is a SECOND claim, answered separately: a wrong palette leaves the
    # geometry perfect, so it must not be folded into the geometry verdict, and
    # it must not be silent either.
    scored = [r for r in rows if r.get("map_frame", True)]
    v["geometry"] = v["verdict"]
    v.update(colour_verdict(game, scored))
    v["recolour_example"] = dominant_recolour(scored)
    if v["fails"]:
        v["verdict"] = f"{v['geometry']}, COLOURS DIFFER"
    name = (f"{game}__{runs[0].name}" if len(runs) == 1 else f"{game}__{len(runs)}-runs")
    drawn = sheet(game, rows, v, spec, [r.name for r in runs], atlas,
                  out_dir / f"{name}.png", z)
    v["sheet"] = str(drawn.relative_to(REPO_ROOT)) if drawn.is_relative_to(REPO_ROOT) else str(drawn)
    v["rows"] = [{k: r[k] for k in r if not k.startswith("_")} for r in rows]
    v["stem"] = name
    v["generated"] = datetime.now().astimezone().isoformat(timespec="seconds")
    return v


def attach_control_images(r: dict, spec: ScreenSpec, atlas: dict, maps_dir: Path,
                          refs: dict) -> None:
    """Re-cut the two wrong crops so the sheet can SHOW them, not just score them."""
    keep = keep_mask(spec)
    entry = atlas["maps"][r["key"]]
    proj = projection_for(atlas, entry)
    ref = load_ref(refs, maps_dir, entry)
    ox, oy = r["window"]
    bad = compare(r["_shot"], ref, ox + WRONG_CROP[0] * proj.tile_px,
                  oy + WRONG_CROP[1] * proj.tile_px, keep)
    r["_wrong_crop_img"] = bad["want"] if bad else None
    r["_wrong_map_img"] = None
    if r.get("wrong_map_key"):
        wentry = atlas["maps"][r["wrong_map_key"]]
        wref = load_ref(refs, maps_dir, wentry)
        h, w = r["_shot"].shape[:2]
        wx = min(max(0, ox), max(0, wref.shape[1] - w))
        wy = min(max(0, oy), max(0, wref.shape[0] - h))
        wrong = compare(r["_shot"], wref, wx, wy, keep)
        r["_wrong_map_img"] = wrong["want"] if wrong else None


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dirs", nargs="+", type=Path)
    ap.add_argument("--turns", type=int, default=16,
                    help="how many turns to sample, spread evenly across the run. The "
                         "colour verdict needs the sample to span two or more OUTDOOR "
                         "maps to tell a per-map recolour from a wrong palette, and "
                         "says so when it does not.")
    ap.add_argument("--zoom", type=int, default=2, help="sheet panels at this multiple of native")
    ap.add_argument("--out", type=Path, default=OUT_DIR)
    ap.add_argument("--json", type=Path,
                    help="write the full per-turn table HERE instead of beside the sheet. "
                         "By default each game's table is written next to its own sheet "
                         "under the same run-derived name, because a fixed name goes "
                         "stale the moment the next invocation names a different set of "
                         "runs and nothing in either file says so.")
    ap.add_argument("--no-colour-map", action="store_true",
                    help="score the raw residual only, with no fitted per-colour lookup")
    args = ap.parse_args(argv)

    by_game: dict[str, list[Path]] = {}
    for d in args.run_dirs:
        if not (d / "events.jsonl").is_file():
            continue
        game = game_of(d)
        if game is None:
            print(f"{d.name}: off-registry ROM, so there is no atlas to check against")
            continue
        by_game.setdefault(game, []).append(d)
    if not by_game:
        print("no run directory with an events.jsonl and a registry ROM")
        return 1

    results = {}
    for game, runs in sorted(by_game.items()):
        results[game] = run_game(game, sorted(runs), args.turns, args.zoom, args.out,
                                 colour_map=not args.no_colour_map)

    print(f"\nband pre-registered in {NOTES}; the metric is the share of comparable "
          f"pixels agreeing within ±{TOLERANCE} per channel\n")
    hdr = (f"{'game':>14}  {'verdict':>22}  {'turns':>5}  {'match':>7}  {'no fit':>7}  "
           f"{'wrong crop':>10}  {'wrong map':>9}  {'self':>7}  {'margin':>8}  {'raw8':>6}")
    print(hdr)
    print("-" * len(hdr))
    bad = 0
    for game, v in sorted(results.items()):
        if v.get("match") is None:
            print(f"{game:>14}  {v['verdict']:>22}  " + "—" * 5)
            for line in wrap(v.get("why", ""), 92):
                print(f"{'':>16}{line}")
            bad += 1
            continue
        print(f"{game:>14}  {v['verdict']:>22}  {v['turns']:5d}  {v['match']:7.4f}  "
              f"{v['direct']:7.4f}  {fmt(v['wrong_crop']):>10}  {fmt(v['wrong_map']):>9}  "
              f"{v['self']:7.4f}  {fmt(v['margin'], sign=True):>8}  {v['raw8']:6.4f}")
        print(f"{'':>16}geometry {v['geometry']}   ·   {v['colour']} "
              f"({len(v['recoloured_maps'])} of {v['maps']} sampled maps recoloured)")
        for line in wrap(v["why"], 92):
            print(f"{'':>18}{line}")
        if v["fails"] and v.get("recolour_example"):
            print(f"{'':>18}{v['recolour_example']}")
        for r in v["reasons"]:
            print(f"{'':>16}· {r}")
        print(f"{'':>16}sheet: {v['sheet']}")
        mapped = [r for r in v["rows"]
                  if r.get("map_frame", True) and not r.get("entry_frame")]
        worst = min(mapped or v["rows"], key=lambda r: r["match"])
        print(f"{'':>16}worst map frame: turn {worst['turn']} on {worst['key']}"
              + (f" ({worst['name']})" if worst.get("name") else "")
              + f" tile {tuple(worst['tile'])} — {worst['match']:.4f} "
              + f"(margin {fmt(worst['margin'], sign=True)})")
        for e in v.get("entry_frames", []):
            w, h, inside, outside = e["banner"]
            print(f"{'':>16}excluded turn {e['turn']} on {e['map']}"
                  + (f" ({e['name']})" if e.get("name") else "")
                  + f" tile {tuple(e['tile'])} — MAP-ENTRY FRAME: the run's own positions "
                    f"say it is the first turn on this map, and a {w}x{h}-tile block at the "
                    f"top-left disagrees on {inside:.0%} of its pixels while the rest of the "
                    f"frame agrees on {outside:.0%}. That is the area-name banner over a "
                    f"camera that may still be scrolling from the warp, not the artwork. "
                    f"It scored {e['match']:.4f} with margin {fmt(e['margin'], sign=True)}.")
        if v.get("not_map_frames"):
            print(f"{'':>16}{len(v['not_map_frames'])} of {v['sampled']} sampled turns show no "
                  f"map at all and were excluded — turns "
                  f"{', '.join(str(t) for t in v['not_map_frames'])}. A turn like that is a "
                  f"battle or a menu the run's battle filter did not catch, not a defect in "
                  f"the artwork.")
        for w in v.get("warnings", []):
            print(f"{'':>16}! {w}")
        if v["geometry"] != "ALIGNED" or v["fails"]:
            bad += 1

    if saturated(results):
        print("\nSATURATED: every game is at the ceiling and so is its wrong-crop control. "
              "That is a measurement of the ceiling, not of the alignment — the verdicts "
              "above must not be quoted.")
        bad += 1

    # The table goes beside the sheet, under the sheet's own name. A fixed
    # `<game>.json` goes stale the moment the next invocation names a different
    # set of runs: a 10:11 PNG next to a 09:56 JSON of other runs, with nothing
    # in either saying so. Both artifacts now share one stem, and the JSON
    # carries the runs, the time and the sheet it belongs to, so a mismatch is
    # readable rather than invisible.
    head = {"band": BAND, "tolerance": TOLERANCE,
            "generated": datetime.now().astimezone().isoformat(timespec="seconds")}
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(dict(head, games=results), indent=1, default=str))
        print(f"\njson: {args.json}")
    else:
        for game, v in sorted(results.items()):
            stem = v.get("stem") or game
            path = args.out / f"{stem}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(
                dict(head, game=game, runs=v.get("runs"), sheet=v.get("sheet"),
                     result=v), indent=1, default=str))
            print(f"{'':>16}json:  {path}")
    return 1 if bad else 0


def wrap(text: str, width: int) -> list[str]:
    out, line = [], ""
    for word in text.split():
        if len(line) + len(word) + 1 > width:
            out.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        out.append(line)
    return out


if __name__ == "__main__":
    raise SystemExit(main())
