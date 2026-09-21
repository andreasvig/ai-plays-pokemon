"""Build a map atlas out of the frames a run actually saw.

    venv/bin/python scripts/stitch_maps.py local/runs/*/        # accumulate
    venv/bin/python scripts/stitch_maps.py --verify             # against pret

The FireRed atlas under ``src/dashboard/web/public/maps/`` is rendered offline
from pret's own tilesets (``scripts/render_gamemaps.py``). Six of the seven
games in ``configs/roms.yaml`` cannot be drawn that way — gen 4 and gen 5 render
their overworlds as 3D meshes, and a ROM hack has no upstream tree at all — so
the general source of map pixels is the machine we already drive:

    a frame + the tile the player stood on = 15x10 tiles of known ground.

Accumulate that over the turns of a run and the map draws itself. See
``artifacts/game-map-render/per-game-plan.md`` (option C) for why this is the
path and what it costs.

**Every rule here is a DISCARD, not a correction** (per-game-plan 3.3): a frame
that cannot be trusted is dropped, because a dropped frame costs nothing when
the same ground is walked again. The four in this module:

- the **player sprite**, which is an object event and not the map;
- the **textbox**, which owns the bottom three tile rows whenever one is open,
  and we cannot see from outside whether one is;
- **battle frames**, which draw no map at all.

What is NOT discarded is disagreement: when two frames of the same pixel differ,
every colour is kept with a count and the most-seen one wins. That is what
removes an NPC who happened to be standing there, and the runner-up count is the
diagnostic that says where the map is animated, crowded or weather-shaded.

The calibration is NOT assumed. ``ScreenSpec`` carries it per game, and for
FireRed the numbers come from ``scripts/verify_gamemap_render.py``, which
measured them against the committed render on real runs; the stitcher
re-derives them from motion and asserts the two agree
(``scripts/stitch_maps.py --calibrate``).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterator, Optional

import numpy as np

#: How many distinct colours are remembered per pixel before the rest are
#: counted as overflow. Four covers "the ground, a sprite, an animation frame
#: and one surprise"; the overflow count is reported rather than hidden.
CANDIDATES = 4


@dataclass(frozen=True)
class ScreenSpec:
    """Where the world sits in one frame, for one game.

    ``cam_x_tiles`` / ``cam_y_px`` are the player's own position in the frame:
    the tile column, and the pixel row of the TOP of the player's tile. The
    window of the map a frame shows therefore starts at
    ``(x - cam_x_tiles) * tile, y * tile - cam_y_px``.
    """

    name: str
    width: int
    height: int
    tile: int
    cam_x_tiles: int
    cam_y_px: int
    textbox_top: int          # rows at or below this may be covered by a message box
    grid_overlay: bool        # the harness draws its own tile grid over the frame
    #: How far the player's SPRITE reaches above and below the top of its own
    #: tile. A GBA overworld sprite is 16x32 and towers over its tile; a GBC one
    #: is 16x16 and is drawn a few pixels high. Both numbers are measured by
    #: ``stitch_maps.py --calibrate``, which finds the sprite as the pixels that
    #: refuse to obey the world's shift.
    sprite_rise: int = 28
    sprite_drop: int = 8
    grid_y_offset: int = 8    # where the overlay's horizontals sit, within a tile
    grid_rgb: tuple = (255, 0, 0)   # what it is drawn in
    grid_alpha: int = 70            # how hard (out of 255)
    colour_bits: int = 5            # the console's colour depth, per channel

    @property
    def tiles_wide(self) -> float:
        return self.width / self.tile


#: FireRed on the GBA. The camera numbers are the ones
#: ``scripts/verify_gamemap_render.py`` verified against the pret render.
GBA_FIRERED = ScreenSpec(name="firered-us", width=240, height=160, tile=16,
                         cam_x_tiles=7, cam_y_px=4 * 16 + 8, textbox_top=112,
                         grid_overlay=True)

#: Crystal on the GBC. A smaller screen (160x144 = 10x9 tiles) and no harness
#: grid overlay — the v2 runs set ``screenshot.grid_overlay: false``, so there
#: is nothing to invert. The camera numbers are NOT assumed: ``stitch_maps.py
#: --calibrate`` re-derives them from motion alone on the two Crystal runs, and
#: ``--verify`` then checks the whole thing against the pret render.
#: ``colour_bits`` stays 5 — a GBC writes 5 bits per channel exactly as a GBA
#: does, and SkyEmu emits them as ``v << 3``.
GBC_CRYSTAL = ScreenSpec(name="crystal-us", width=160, height=144, tile=16,
                         cam_x_tiles=4, cam_y_px=4 * 16, textbox_top=96,
                         grid_overlay=False, sprite_rise=8, sprite_drop=16)

#: Emerald on the GBA. The same console and the same 240x160 screen as FireRed,
#: and the camera turns out to be the same too — but it is MEASURED, not
#: inherited. ``stitch_maps.py --calibrate`` on the three v2 Emerald runs
#: (2026-09-21, 17 unambiguous one-tile moves, 6 discarded): tile 16 px with
#: 17/17 pairs agreeing, the screen-fixed box at x 114..125 / y 68..86, giving
#: player column 7 and tile top row 71 +/- 2 — i.e. cam_y_px 72. No render was
#: involved in any of that, so it is an independent confirmation rather than a
#: number fitted to the artwork it is used to check.
#: ``grid_overlay`` is False because every surviving Emerald run recorded with
#: ``screenshot.grid_overlay: false``; ``run_spec`` reads the flag per run
#: anyway, which is the thing that was wrong for FireRed for weeks.
GBA_EMERALD = ScreenSpec(name="emerald-us", width=240, height=160, tile=16,
                         cam_x_tiles=7, cam_y_px=4 * 16 + 8, textbox_top=112,
                         grid_overlay=False)

#: Every game the stitcher can place a frame for, by the same key the atlas and
#: the ROM registry use. The stitcher's body was always generic; only its
#: imports named one game.
#:
#: The four DS games are deliberately ABSENT rather than approximated. A
#: ``ScreenSpec`` asserts that the screen is an axis-aligned window of the tile
#: grid; the DS field camera is tilted and perspective, so no (tile, pixel)
#: pair describes it and a plausible-looking entry here would be a lie that
#: every consumer downstream would believe. ``verify_map_alignment.py`` refuses
#: those games by name and says why.
SPECS: dict[str, ScreenSpec] = {s.name: s for s in (GBA_FIRERED, GBA_EMERALD, GBC_CRYSTAL)}


def run_spec(spec: ScreenSpec, run_dir: Path) -> ScreenSpec:
    """The game's spec as one RUN was actually recorded.

    ``grid_overlay`` is a property of the HARNESS's screenshot config
    (``screenshot.grid_overlay`` in the run's own config.json), not of the
    console — and it is off for every run recorded since the v2 control centre
    landed. The spec's value is the default for a run that does not say.

    **This is not a nicety.** ``ungrid`` inverts a constant-alpha composite, and
    inverting one that was never applied recolours the two pixels straddling
    every tile boundary — 1 - (14/16)^2 = 23.4% of a 16 px grid. Measured
    2026-09-20: every FireRed map scored 74-82% against its own pret render, the
    same number on every map, with the residual sitting exactly on the grid
    lines. The render was right and the reader was wrong, and because the number
    was uniform it read as "the atlas is a bit off" rather than as a bug.
    """
    cfg = Path(run_dir) / "config.json"
    if not cfg.exists():
        return spec
    try:
        want = json.loads(cfg.read_text()).get("screenshot", {}).get("grid_overlay")
    except (OSError, ValueError, AttributeError):
        return spec
    if want is None or bool(want) == spec.grid_overlay:
        return spec
    return replace(spec, grid_overlay=bool(want))


def window_origin(spec: ScreenSpec, x: int, y: int) -> tuple[int, int]:
    """Top-left pixel of the map window a frame shows, for a player at (x, y).

    May be NEGATIVE near a map's edge: the game draws border tiles outside the
    map there, which is ground the map itself does not have.
    """
    return x * spec.tile - spec.cam_x_tiles * spec.tile, y * spec.tile - spec.cam_y_px


def keep_mask(spec: ScreenSpec, textbox: bool = True, sprite: bool = True) -> np.ndarray:
    """Pixels of a frame that are honest map, as a (height, width) bool array.

    ``sprite=False`` keeps the player in — only the calibration wants that, and
    only because the player is the thing it is looking for.
    """
    X, Y = np.meshgrid(np.arange(spec.width), np.arange(spec.height))
    keep = np.ones((spec.height, spec.width), dtype=bool)
    # The player, drawn taller than its tile: the sprite's feet are on the tile
    # and its head reaches into the row above.
    if sprite:
        px0 = spec.cam_x_tiles * spec.tile
        box = ((X >= px0 - 4) & (X < px0 + spec.tile + 4)
               & (Y >= spec.cam_y_px - spec.sprite_rise) & (Y < spec.cam_y_px + spec.sprite_drop))
        keep &= ~box
    if textbox:
        keep &= Y < spec.textbox_top
    return keep


def grid_pixels(spec: ScreenSpec) -> np.ndarray:
    """Where the harness's own grid overlay sits, as a (height, width) bool array.

    Measured, not assumed (2026-09-19, 6 frames against the pret render): the
    residual against the render is ~65 on columns ``x % 16`` in {0, 15} and rows
    ``(y - 8) % 16`` in {0, 15}, and ~30 everywhere else. ``mgba`` draws the
    lines 2*scale wide on the upscaled frame, which is exactly 2 native pixels
    straddling each tile edge — one column short of what
    ``scripts/verify_gamemap_render.py`` masks, which is conservative because it
    only has to throw pixels away.
    """
    if not spec.grid_overlay:
        return np.zeros((spec.height, spec.width), dtype=bool)
    tile, off = spec.tile, spec.grid_y_offset
    cols = np.zeros(spec.width, dtype=bool)
    rows = np.zeros(spec.height, dtype=bool)
    # A line is 2 px wide, straddling the boundary it marks — so a pixel is
    # covered by the line ON its own boundary or the one just past it, and only
    # where that line exists. The LAST boundary of a frame has no line beyond
    # it: column 239 and row 159 are untouched, and inverting them would put a
    # red cast where there was none (caught by the round-trip test, 2026-09-19).
    for b in range(0, spec.width, tile):
        cols[b] = True
        if b - 1 >= 0:
            cols[b - 1] = True
    for b in range(off % tile, spec.height, tile):
        rows[b] = True
        if b - 1 >= 0:
            rows[b - 1] = True
    return rows[:, None] | cols[None, :]


def ungrid(frame: np.ndarray, spec: ScreenSpec) -> np.ndarray:
    """Undo the harness's grid overlay instead of masking it away.

    **Masking it is not an option, though it looks like one.** The overlay is
    drawn in SCREEN space and a frame is only ever captured with the camera at
    rest, which is tile-aligned — so the same 2 px of every tile are covered in
    every frame forever, and accumulating more frames never fills them. Masked,
    a stitched map comes out as artwork behind a black grid (seen 2026-09-19).

    The overlay is a constant alpha composite, so it inverts exactly:
    ``out = (rgb*a + base*(255-a))/255`` gives ``base = (out*255 - rgb*a)/(255-a)``.
    Rounding is amplified by ``255/(255-a)`` = 1.38, which is enough to miss by
    one or two, so the result is snapped back onto the console's own colour
    ladder (GBA: 5 bits per channel). Measured on Pallet Town and Route 1: the
    overlaid pixels match the render within 1 for **1.6% before, 84.0% inverted,
    98.1% inverted and snapped** — the same rate as pixels the overlay never
    touched (99.1%).
    """
    mask = grid_pixels(spec)
    if not mask.any():
        return frame
    out = frame.astype(np.float64)
    over = np.asarray(spec.grid_rgb, dtype=np.float64)
    a = float(spec.grid_alpha)
    out[mask] = (out[mask] * 255.0 - over * a) / (255.0 - a)
    out = np.clip(np.round(out), 0, 255).astype(np.int16)
    ladder = _colour_ladder(spec.colour_bits)
    out[mask] = ladder[np.abs(out[mask][..., None] - ladder).argmin(axis=-1)]
    return out.astype(np.uint8)


def _colour_ladder(bits: int) -> np.ndarray:
    """The 8-bit values a console with ``bits`` per channel can actually output."""
    lo = 8 - bits
    return np.array([(v << lo) | (v >> (bits - lo)) for v in range(1 << bits)], dtype=np.int16)


def to_native(img, spec: ScreenSpec) -> np.ndarray:
    """A saved screenshot at any upscale, back to the console's own pixels.

    The harness upscales with NEAREST, so decimating with NEAREST is exact —
    every native pixel is recovered, not averaged (the grid overlay is the one
    thing that is not, and ``keep_mask`` drops it).
    """
    from PIL import Image

    if img.size != (spec.width, spec.height):
        img = img.resize((spec.width, spec.height), Image.NEAREST)
    return ungrid(np.asarray(img.convert("RGB"), dtype=np.uint8), spec)


class MapCanvas:
    """One map, accumulated pixel by pixel with a vote per colour.

    The canvas is allocated over the window origins it will be given, so a
    frame at a map's edge (a negative origin) is kept rather than clipped, and
    ``origin`` says where the map's own (0, 0) landed.
    """

    def __init__(self, x0: int, y0: int, width: int, height: int) -> None:
        self.origin = (x0, y0)
        self.shape = (height, width)
        self.cand = np.zeros((CANDIDATES, height, width, 3), dtype=np.uint8)
        self.count = np.zeros((CANDIDATES, height, width), dtype=np.uint16)
        self.overflow = np.zeros((height, width), dtype=np.uint16)
        #: How many times :meth:`add` was called. The stitcher adds once per RUN
        #: (a run's own frames are settled first), so on a finished map this is
        #: the number of independent visits behind it, not a frame count.
        self.contributions = 0

    def add(self, frame: np.ndarray, ox: int, oy: int, keep: np.ndarray) -> None:
        """Vote one frame's kept pixels into the canvas at window origin (ox, oy)."""
        h, w = frame.shape[:2]
        x0, y0 = ox - self.origin[0], oy - self.origin[1]
        if x0 < 0 or y0 < 0 or y0 + h > self.shape[0] or x0 + w > self.shape[1]:
            raise ValueError(f"frame at ({ox}, {oy}) falls outside a canvas built for it")
        cand = self.cand[:, y0:y0 + h, x0:x0 + w]
        count = self.count[:, y0:y0 + h, x0:x0 + w]
        placed = ~keep
        for k in range(CANDIDATES):
            seen = count[k] > 0
            same = seen & (cand[k] == frame).all(axis=-1) & ~placed
            count[k] += same
            placed |= same
            empty = ~seen & ~placed
            cand[k][empty] = frame[empty]
            count[k] += empty
            placed |= empty
        self.overflow[y0:y0 + h, x0:x0 + w] += (~placed & keep)
        self.contributions += 1

    def image(self) -> tuple[np.ndarray, np.ndarray]:
        """``(rgb, votes)`` — the winning colour per pixel and how many frames voted.

        ``votes == 0`` is ground nobody has seen; a caller draws it as unknown
        rather than as black.
        """
        best = self.count.argmax(axis=0)
        rgb = np.take_along_axis(self.cand, best[None, ..., None], axis=0)[0]
        votes = self.count.max(axis=0)
        rgb[votes == 0] = 0
        return rgb, votes

    def disputed(self) -> np.ndarray:
        """Votes that went to a colour other than the winner, per pixel.

        High where the map moves (an NPC's beat, an animated tile, weather) and
        near zero everywhere else. This is the map's own honesty report.
        """
        return (self.count.sum(axis=0) - self.count.max(axis=0) + self.overflow).astype(np.uint32)


def fit_colour_map(ref: np.ndarray, got: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """``ref`` recoloured by the colour it most often sits under in ``got``.

    Some of what a game draws is a per-colour lookup applied at runtime and not
    present in the tiles: FireRed's ``WEATHER_SHADE`` maps darken every palette
    entry of Viridian Forest, so a CORRECT render of it differs from the screen
    on nearly every pixel. Fitting a lookup of exactly that shape and measuring
    the residual through it separates "the colours were transformed" from "the
    terrain is wrong" — no lookup moves a tree onto a roof.

    Shared with ``scripts/verify_gamemap_render.py``, which needs the same
    distinction from the other direction (one frame against the render).
    """
    out = ref.copy()
    flat_ref = ref.reshape(-1, 3)
    flat_got = got.reshape(-1, 3)
    flat_mask = mask.reshape(-1)
    codes = (flat_ref[:, 0].astype(np.int32) << 16) | (flat_ref[:, 1].astype(np.int32) << 8) | flat_ref[:, 2]
    got_codes = (flat_got[:, 0].astype(np.int32) << 16) | (flat_got[:, 1].astype(np.int32) << 8) | flat_got[:, 2]
    flat_out = out.reshape(-1, 3)
    for code in np.unique(codes[flat_mask]):
        where = (codes == code) & flat_mask
        vals, counts = np.unique(got_codes[where], return_counts=True)
        winner = int(vals[counts.argmax()])
        flat_out[codes == code] = ((winner >> 16) & 0xFF, (winner >> 8) & 0xFF, winner & 0xFF)
    return flat_out.reshape(ref.shape)


# --- reading a run -----------------------------------------------------------

def positions(run_dir: Path) -> dict[int, tuple[int, int, int, int]]:
    """``turn -> (map_group, map_num, x, y)`` — where the run ended that turn.

    The referee's per-turn poll (``referee_position``) where the run has one.
    A run that recorded NONE falls back to the per-input trace's ``end_tile``,
    which is the only position a v2 (SkyEmu) run carries: the two Crystal runs
    of 2026-09-19/20 have 383 ``turn_input_trace`` events and zero
    ``referee_position``, so without the fallback there is no Crystal frame to
    place at all.

    **All or nothing, per run, and the poll wins.** The two are not
    interchangeable: measured over the eight FireRed runs, 126 of 140 turns
    that carry both agree, and every disagreement is a turn whose trace ended
    before the emulator settled — a warp still completing (the poll reads the
    destination, the trace the map departed) or one more step of movement. So a
    run that has the poll is read exactly as it was before this fallback
    existed, and mixing the two per turn is refused rather than reasoned about.

    Inside the fallback, a turn whose trace CHANGED MAP is dropped: that is the
    warp case, and its last tile is not where the turn ended. The remaining
    one-tile cases are what the stitcher's per-frame agreement filter is for —
    a frame placed one tile out disagrees with the settled map almost
    everywhere and is discarded on its own.
    """
    out: dict[int, tuple[int, int, int, int]] = {}
    fallback: dict[int, tuple[int, int, int, int]] = {}
    path = Path(run_dir) / "events.jsonl"
    if not path.exists():
        return out
    with path.open() as fh:
        for line in fh:
            if '"referee_position"' in line:
                e = json.loads(line)
                if e.get("type") == "referee_position" and e.get("x") is not None:
                    out[int(e["turn"])] = (int(e["map_group"]), int(e["map_num"]),
                                           int(e["x"]), int(e["y"]))
            elif '"turn_input_trace"' in line:
                e = json.loads(line)
                if e.get("type") != "turn_input_trace":
                    continue
                end = e.get("end_tile")
                if not end or len(end) != 4 or any(v is None for v in end):
                    continue
                g, n = int(end[0]), int(end[1])
                seen = [s for s in (e.get("samples") or [])
                        if isinstance(s, dict) and s.get("x") is not None]
                if any(s.get("map_group") != g or s.get("map_num") != n for s in seen):
                    continue                  # the turn warped: end_tile is not where it ended
                fallback[int(e["turn"])] = (g, n, int(end[2]), int(end[3]))
    return out or fallback


def battle_turns(run_dir: Path) -> set[int]:
    """Turns whose per-input trace saw a battle — their frame is not the map."""
    out: set[int] = set()
    path = Path(run_dir) / "events.jsonl"
    if not path.exists():
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


def screenshots(run_dir: Path) -> dict[int, Path]:
    """``turn -> the frame the model was shown on that turn``."""
    out: dict[int, Path] = {}
    for p in sorted((Path(run_dir) / "screenshots").glob("*.png")):
        m = re.search(r"turn_(\d+)", p.name)
        if m:
            out[int(m.group(1))] = p
    return out


def has_trace(run_dir: Path) -> bool:
    """Whether this run recorded the per-input trace at all.

    **The stitcher's admission test, and it is not optional.** Without the trace
    there is no in-battle flag, and without that flag nothing separates a frame
    of the map from a frame of a battle fought while standing on it. No amount
    of voting recovers it: a run that fights Brock for 91 turns from one tile
    contributes 91 frames of Bulbasaur against three runs' worth of floor, and
    the parts of a battle screen that are identical between runs (the white
    ground, the HP box) agree with each other as happily as real terrain does.
    Measured 2026-09-19: Pewter Gym came out 56% battle screen with every other
    map above 95%, and six of the nine runs that reached it predate the trace
    (2026-09-14).

    This is the same signal ``artifacts/game-map-render/per-game-plan.md`` 3.4
    needs to tell a wall from a textbox — the stitcher and the observed walk
    graph are gated on one flag, from opposite directions.
    """
    path = Path(run_dir) / "events.jsonl"
    if not path.exists():
        return False
    with path.open() as fh:
        return any('"turn_input_trace"' in line for line in fh)


@dataclass(frozen=True)
class Sample:
    """One frame, and the map window it shows."""

    run: str
    turn: int
    map_key: str
    x: int
    y: int
    path: Path


def samples(run_dir: Path, skip_battles: bool = True, require_trace: bool = True) -> Iterator[Sample]:
    """Every frame of a run that can be placed on a map.

    **The pairing is the part that is easy to get wrong.** The frame shown ON
    turn N is captured BEFORE turn N's buttons, so it shows the tile the referee
    polled after turn N-1. Pairing it with turn N's own position compares the
    frame against where the run ended up — a different place entirely on any
    turn that moved (90% of pixels differ, measured 2026-09-15 in
    scripts/verify_gamemap_render.py).
    """
    run_dir = Path(run_dir)
    if require_trace and not has_trace(run_dir):
        return
    pos, shots = positions(run_dir), screenshots(run_dir)
    fights = battle_turns(run_dir) if skip_battles else set()
    for turn in sorted(shots):
        if turn - 1 not in pos:
            continue
        if turn in fights or turn - 1 in fights:
            continue
        g, n, x, y = pos[turn - 1]
        yield Sample(run=run_dir.name, turn=turn, map_key=f"{g}:{n}", x=x, y=y,
                     path=shots[turn])


def canvas_for(spec: ScreenSpec, placed: list[tuple[int, int]]) -> MapCanvas:
    """A canvas that holds every one of these window origins."""
    xs = [ox for ox, _ in placed]
    ys = [oy for _, oy in placed]
    x0, y0 = min(xs), min(ys)
    return MapCanvas(x0, y0, max(xs) - x0 + spec.width, max(ys) - y0 + spec.height)


__all__ = ["CANDIDATES", "ScreenSpec", "GBA_FIRERED", "GBA_EMERALD", "GBC_CRYSTAL", "SPECS",
           "MapCanvas",
           "Sample", "fit_colour_map",
           "window_origin", "keep_mask", "to_native", "run_spec", "positions", "battle_turns",
           "screenshots", "samples", "canvas_for", "ungrid", "grid_pixels", "has_trace"]
