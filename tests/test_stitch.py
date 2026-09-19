"""The stitcher: a map built out of the frames a run actually saw.

``src/app/stitch.py`` + ``scripts/stitch_maps.py``, planned in
``artifacts/game-map-render/per-game-plan.md`` (option C). FireRed is the game
this is built ON rather than the game it is FOR: it has a render from pret's own
tilesets to check against, so every rule can be proven here before it meets gen
4, gen 5 or a ROM hack, where nothing can check it.

Each test below guards a rule that cost something to find.
"""

from __future__ import annotations

import json

import numpy as np
import pytest
from PIL import Image, ImageDraw

from src.app.stitch import (
    GBA_FIRERED, MapCanvas, canvas_for, grid_pixels, has_trace, keep_mask, samples,
    to_native, ungrid, window_origin,
)

SPEC = GBA_FIRERED


# --- the frame ---------------------------------------------------------------

def _overlaid(native: Image.Image, scale: int = 6) -> Image.Image:
    """A frame the way the harness saves it: upscaled, then gridded.

    Copied in behaviour, not by import, from ``mgba._draw_grid_overlay`` — the
    point is to prove the stitcher's idea of where the lines fall matches the
    code that draws them.
    """
    img = native.resize((native.width * scale, native.height * scale), Image.NEAREST)
    tile, y_off = 16 * scale, 8 * scale
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    width = max(3, scale * 2)
    for x in range(0, img.width, tile):
        draw.line([(x, 0), (x, img.height)], fill=(255, 0, 0, 70), width=width)
    for y in range(y_off, img.height, tile):
        draw.line([(0, y), (img.width, y)], fill=(255, 0, 0, 70), width=width)
    return Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")


def _gba_noise(seed: int = 7) -> Image.Image:
    """A frame of colours the GBA could actually output (5 bits per channel)."""
    rng = np.random.default_rng(seed)
    ladder = np.array([(v << 3) | (v >> 2) for v in range(32)], dtype=np.uint8)
    idx = rng.integers(0, 32, size=(SPEC.height, SPEC.width, 3))
    return Image.fromarray(ladder[idx].astype(np.uint8))


def test_the_grid_overlay_is_undone_not_masked_away():
    """The overlay inverts, because masking it can never be undone later.

    It is drawn in screen space and a frame is only captured with the camera at
    rest, which is tile-aligned — so the same 2 px of every tile are covered in
    EVERY frame, and no amount of accumulation fills them in. Masked, a stitched
    map comes out as artwork behind a black grid (seen for real, 2026-09-19).
    """
    native = _gba_noise()
    restored = to_native(_overlaid(native), SPEC)
    assert np.array_equal(restored, np.asarray(native, dtype=np.uint8))


def test_the_grid_is_two_pixels_at_every_tile_edge():
    """Where the lines fall, measured against the render and asserted here.

    One column either side of each boundary and one row either side of the
    half-tile offset. ``verify_gamemap_render`` masks a third column as well,
    which is safe when you are throwing pixels away and wrong when you are
    repairing them.
    """
    grid = grid_pixels(SPEC)
    row, col = SPEC.cam_y_px + 3, 5          # a row and a column with no line of their own
    assert {int(c) % 16 for c in np.where(grid[row])[0]} == {0, 15}
    assert {int(r) % 16 for r in np.where(grid[:, col])[0]} == {7, 8}
    # ... except at the far edge, where there is no next line to straddle.
    assert not grid[row, SPEC.width - 1]
    assert not grid[SPEC.height - 1, col]


def test_the_keep_mask_drops_the_player_and_the_textbox_but_not_the_grid():
    keep = keep_mask(SPEC)
    assert not keep[SPEC.cam_y_px, SPEC.cam_x_tiles * SPEC.tile]      # the player
    assert not keep[SPEC.textbox_top + 1, 0]                          # message box rows
    assert keep[SPEC.cam_y_px - 40, 0]                                # a tile edge: kept now
    assert keep_mask(SPEC, sprite=False)[SPEC.cam_y_px, SPEC.cam_x_tiles * SPEC.tile]


# --- placing a frame ---------------------------------------------------------

def test_the_window_is_placed_from_the_camera_not_from_the_tile():
    """The player is not at the frame's top-left, and the offset is not a guess.

    Column 7 and half a tile down, re-derived from motion alone by
    ``stitch_maps.py --calibrate`` (16 px, column 7, top row 71 +/- 2 against
    the spec's 72) and verified against the render by verify_gamemap_render.
    """
    assert window_origin(SPEC, 7, 5) == (0, 5 * 16 - 72)
    assert window_origin(SPEC, 0, 0) == (-112, -72)      # a map edge: negative, and kept


def test_a_canvas_covers_the_frames_that_run_off_the_map():
    canvas = canvas_for(SPEC, [(-112, -72), (48, 96)])
    assert canvas.origin == (-112, -72)
    assert canvas.shape == (96 + 72 + SPEC.height, 48 + 112 + SPEC.width)


# --- the vote ----------------------------------------------------------------

def _solid(rgb) -> np.ndarray:
    return np.full((SPEC.height, SPEC.width, 3), rgb, dtype=np.uint8)


def test_the_most_seen_colour_wins_and_the_rest_are_recorded_as_disputed():
    """What removes an NPC who was standing there when one run walked past."""
    canvas = MapCanvas(0, 0, SPEC.width, SPEC.height)
    keep = np.ones((SPEC.height, SPEC.width), dtype=bool)
    for _ in range(2):
        canvas.add(_solid((10, 20, 30)), 0, 0, keep)
    canvas.add(_solid((200, 0, 0)), 0, 0, keep)
    rgb, votes = canvas.image()
    assert tuple(rgb[0, 0]) == (10, 20, 30)
    assert votes[0, 0] == 2
    assert canvas.disputed()[0, 0] == 1


def test_ground_nobody_saw_is_reported_as_unseen_rather_than_drawn_black():
    canvas = MapCanvas(0, 0, SPEC.width * 2, SPEC.height)
    canvas.add(_solid((10, 20, 30)), 0, 0, np.ones((SPEC.height, SPEC.width), dtype=bool))
    _, votes = canvas.image()
    assert votes[0, 0] == 1
    assert votes[0, SPEC.width + 10] == 0


def test_a_masked_pixel_never_votes():
    canvas = MapCanvas(0, 0, SPEC.width, SPEC.height)
    keep = np.zeros((SPEC.height, SPEC.width), dtype=bool)
    canvas.add(_solid((1, 2, 3)), 0, 0, keep)
    assert canvas.image()[1].max() == 0


def test_a_frame_outside_its_canvas_is_refused_rather_than_wrapped():
    canvas = MapCanvas(0, 0, SPEC.width, SPEC.height)
    with pytest.raises(ValueError):
        canvas.add(_solid((1, 2, 3)), 16, 0, np.ones((SPEC.height, SPEC.width), dtype=bool))


# --- reading a run -----------------------------------------------------------

def _run_dir(tmp_path, *, trace: bool, turns: int = 3):
    run = tmp_path / "run"
    (run / "screenshots").mkdir(parents=True)
    lines = []
    for t in range(1, turns + 1):
        lines.append({"type": "referee_position", "turn": t, "map_group": 3, "map_num": 0,
                      "x": 5 + t, "y": 9})
        if trace:
            lines.append({"type": "turn_input_trace", "turn": t,
                          "samples": [{"i": 0, "in_battle": False}]})
        Image.new("RGB", (SPEC.width, SPEC.height), (0, 0, 0)).save(
            run / "screenshots" / f"{t:05d}_turn_{t}.png")
    (run / "events.jsonl").write_text("\n".join(json.dumps(e) for e in lines))
    return run


def test_a_frame_is_paired_with_the_position_of_the_turn_BEFORE_it(tmp_path):
    """The frame shown on turn N is captured before turn N's buttons.

    Pairing it with turn N's own position compares it against where the run
    ended up — a different place on any turn that moved (90% of pixels differ,
    measured 2026-09-15).
    """
    run = _run_dir(tmp_path, trace=True, turns=3)
    got = {s.turn: (s.x, s.y) for s in samples(run)}
    assert got == {2: (6, 9), 3: (7, 9)}          # turn 1 has no turn-0 poll to pair with


def test_a_run_without_the_per_input_trace_is_refused_entirely(tmp_path):
    """No in-battle flag means no way to tell a battle frame from the map.

    Measured 2026-09-19: Pewter Gym stitched to 56% agreement with the render —
    two thirds of it a battle screen — because six of the nine runs that reached
    it predate the trace and one of them fought Brock for 91 turns from a single
    tile. Voting cannot fix it; 91 frames of Bulbasaur outvote three runs of
    floor, and the white ground of a battle agrees with itself between runs as
    happily as terrain does. With the refusal in place that map reads 98%.
    """
    run = _run_dir(tmp_path, trace=False, turns=3)
    assert has_trace(run) is False
    assert list(samples(run)) == []
    assert len(list(samples(run, require_trace=False))) == 2


def test_battle_turns_are_skipped_on_both_sides_of_the_frame(tmp_path):
    run = _run_dir(tmp_path, trace=True, turns=4)
    events = (run / "events.jsonl").read_text().splitlines()
    patched = []
    for line in events:
        e = json.loads(line)
        if e.get("type") == "turn_input_trace" and e["turn"] == 2:
            e["samples"] = [{"i": 0, "in_battle": True}]
        patched.append(json.dumps(e))
    (run / "events.jsonl").write_text("\n".join(patched))
    # turn 2 is a battle, so neither the frame ON turn 2 nor the one whose
    # position comes from turn 2 (turn 3) can be trusted.
    assert {s.turn for s in samples(run)} == {4}
