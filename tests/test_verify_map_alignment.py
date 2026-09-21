"""The per-turn map-alignment check (`scripts/verify_map_alignment.py`).

The point of that script is to be a verifier nobody has to take on trust, so
these tests guard the four things that would make it lie while still printing
a confident number. Each one states the mutation that makes it fail — a test
that cannot be made to fail is not a test.

  1. **the reader.** `stitch_maps.py --verify` scored FireRed 74-82% for weeks
     because it un-composited a grid overlay that had never been applied. The
     `self` control is what catches that class on sight, and
     `test_the_self_control_catches_a_phantom_ungrid` proves it does by
     turning the bug back on.
  2. **the fitted colour lookup.** It exists for FireRed's WEATHER_SHADE, and
     unguarded it will score a fade-to-black at a perfect 1.0000 by mapping
     the whole palette onto one colour.
  3. **the projection.** A wrong `png_origin` subtraction, or a top-down crop
     taken after the DS camera goes angled, is the silent failure that loads,
     renders and is uniformly a little bit off.
  4. **"misaligned" vs "not a map".** A battle frame matches nothing anywhere;
     a misaligned atlas has a neighbour that matches WELL. Conflating them
     reports a defect in artwork that is fine.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.app.stitch import SPECS, ScreenSpec, keep_mask  # noqa: E402

NOTES = REPO_ROOT / "artifacts" / "game-map-render" / "notes" / "verify.md"
RUNS = REPO_ROOT / "local" / "runs"


def _load():
    spec = importlib.util.spec_from_file_location(
        "verify_map_alignment", REPO_ROOT / "scripts" / "verify_map_alignment.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod          # dataclasses needs the module registered
    spec.loader.exec_module(mod)
    return mod


V = _load()


# -- 1. the projection ---------------------------------------------------------

def test_a_map_local_atlas_subtracts_png_origin():
    """PNG pixel (0, 0) is tile `png_origin`, so a source rect subtracts it.

    MUTATION that fails this: drop the `- ox` / `- oy` from
    `TileProjection.window`. The crop then reads from 96 tiles left and 832
    tiles up of where the player is, which for a DS map is off the image
    entirely — the failure that draws nothing and throws nothing.
    """
    atlas = {"camera": "topdown", "png_frame": "map-local", "tile_px": 16}
    entry = {"png_origin": [96, 832]}
    proj = V.projection_for(atlas, entry)
    spec = ScreenSpec(name="x", width=240, height=160, tile=16, cam_x_tiles=0,
                      cam_y_px=0, textbox_top=160, grid_overlay=False)
    assert proj.window(spec, 144, 845) == ((144 - 96) * 16, (845 - 832) * 16)


def test_an_atlas_with_no_png_frame_subtracts_nothing():
    """MUTATION: make the `map` branch subtract `entry["origin"]` — the GBA
    atlases carry no such field, so a default of 0 is the only correct read,
    and a subtraction would shift every 2D crop by a constant."""
    atlas = {"camera": "topdown", "tile_px": 16}
    proj = V.projection_for(atlas, {})
    assert proj.origin_tiles == (0, 0)


def test_an_unknown_camera_is_refused_by_name():
    """The seam for the angled DS camera.

    MUTATION that fails this: fall back to the top-down projection when
    `camera` is anything else. The tool would then keep printing numbers after
    the angled camera lands, computed with a projection that no longer
    describes the artwork.
    """
    with pytest.raises(V.UnknownCamera) as exc:
        V.projection_for({"camera": "angled", "tile_px": 16}, {})
    assert "'angled'" in str(exc.value)
    assert "topdown" in str(exc.value)


def test_map_local_without_a_png_origin_is_refused_rather_than_guessed():
    """MUTATION: default the missing origin to (0, 0). The crop is then off by
    a constant on exactly the atlas that declared it needed one."""
    with pytest.raises(V.NoInstrument):
        V.projection_for({"camera": "topdown", "png_frame": "map-local", "tile_px": 16}, {})


@pytest.mark.parametrize("game", ["platinum-us", "soulsilver-us", "black-us", "black2-us"])
def test_the_ds_games_are_refused_with_the_projection_reason(game):
    """Being explicit about where the instrument does not apply.

    MUTATION that fails this: add a plausible-looking DS entry to
    `src.app.stitch.SPECS`. The tool would then print a per-pixel number
    comparing a perspective frame against an orthographic render, which is not
    a measurement of anything.
    """
    path = REPO_ROOT / "src" / "dashboard" / "web" / "public" / "maps" / game / "index.json"
    if not path.is_file():
        pytest.skip(f"{game} has no atlas in this checkout")
    atlas = json.loads(path.read_text())
    with pytest.raises(V.NoInstrument) as exc:
        V.screen_for(game, atlas)
    assert "3d-ortho" in str(exc.value)
    assert "PERSPECTIVE" in str(exc.value).upper()


# -- 2. the reader ------------------------------------------------------------

def _ladder_image(spec: ScreenSpec, seed: int = 7) -> np.ndarray:
    """A picture whose colours a console could actually have produced.

    Mid-range only: `ungrid` clips at 0, so a black frame survives a phantom
    un-composite unchanged and would make the control below vacuously pass.
    """
    rng = np.random.default_rng(seed)
    v = rng.integers(8, 28, size=(spec.height, spec.width, 3), dtype=np.int16)
    return ((v << 3) | (v >> 2)).astype(np.int16)


def test_the_self_control_is_one_for_a_run_recorded_the_way_it_says():
    """MUTATION: have `self_control` compare something other than the round
    trip (say `want` against `want`). It would then read 1.0 whatever the
    reader did, and the next test could not fail."""
    spec = SPECS["crystal-us"]
    want = _ladder_image(spec)
    mask = keep_mask(spec)
    got = V.self_control(want, mask, spec, (spec.width * 6, spec.height * 6))
    assert got == pytest.approx(1.0)


def test_the_self_control_catches_a_phantom_ungrid():
    """**The one that would have caught the 2026-09-20 bug.**

    `grid_overlay=True` on a run recorded with the overlay off makes the reader
    invert a composite that was never applied, mangling the two pixels
    straddling every tile boundary — 23.4% of a 16 px grid. A crop put through
    that reader no longer equals itself.

    MUTATION that fails this: make `to_native` ignore `spec.grid_overlay` (i.e.
    reintroduce the bug's opposite — never un-gridding). The `self` control
    would read 1.0 for both specs and the guard would be dead.
    """
    from dataclasses import replace
    honest = SPECS["crystal-us"]                      # recorded with the overlay off
    lying = replace(honest, grid_overlay=True)        # the 2026-09-20 bug, exactly
    want = _ladder_image(honest)
    mask = keep_mask(honest)
    size = (honest.width * 6, honest.height * 6)
    assert V.self_control(want, mask, honest, size) == pytest.approx(1.0)
    mangled = V.self_control(want, mask, lying, size)
    assert mangled < V.BAND["self_floor"], (
        "a reader that un-composites an overlay that was never applied must fail "
        f"its own round trip, but it scored {mangled}")
    # and it must fail by a lot, not by a pixel: the overlay covers both
    # straddling pixels of every tile boundary
    assert mangled < 0.90


def test_the_console_ladder_collapses_the_two_5bit_png_encodings():
    """The renders write 5-bit colour as `(v<<3)|(v>>2)`; SkyEmu writes `v<<3`.

    That is a constant 0-7 per channel on every pixel of every map and has
    nothing to do with alignment. Measured 2026-09-21: compared as raw bytes,
    Emerald's median agreement was 0.0012 and FireRed's 0.0868, and the only
    thing standing between that and a published 0.98 was a colour lookup
    fitted from the frame itself.

    MUTATION that fails this: make `on_ladder` the identity. Every 2D game's
    raw score collapses to ~0 and the metric's entire signal comes from a
    fitted lookup, which is not a measurement.
    """
    v = np.arange(32, dtype=np.int16)
    replicated = ((v << 3) | (v >> 2)).astype(np.int16)
    shifted = (v << 3).astype(np.int16)
    assert not np.array_equal(replicated, shifted)          # they really do differ
    assert np.array_equal(V.on_ladder(replicated, 5), V.on_ladder(shifted, 5))
    assert np.array_equal(V.on_ladder(replicated, 5), v)


# -- 3. the fitted colour lookup ----------------------------------------------

def _pair(spec: ScreenSpec, want: np.ndarray, shot: np.ndarray):
    """`compare` wants a whole map PNG; give it one exactly the window's size."""
    return V.compare(shot, want, 0, 0, keep_mask(spec), True, spec.colour_bits)


def test_a_lookup_that_collapses_the_palette_is_refused():
    """A fade to one colour is not a map that matches perfectly.

    Unguarded, `fit_colour_map` maps every render colour onto the single colour
    the frame shows and scores 1.0000. Measured on a real Crystal frame
    (turn 169, 4 colours -> 1): it did exactly that.

    MUTATION that fails this: drop `injective` from the `if` in `compare`.
    """
    spec = SPECS["crystal-us"]
    want = _ladder_image(spec)
    shot = np.full_like(want, 8 << 3)            # the whole screen one colour
    got = _pair(spec, want, shot)
    assert got["match"] < 0.20, "a fade must not score as a match"
    assert "REFUSED" in got["via"]
    assert got["collapse"] > 1.0


def test_a_lookup_that_permutes_the_palette_is_allowed():
    """The case the fit was written for: FireRed's WEATHER_SHADE darkens every
    palette entry, so a CORRECT render differs from the screen almost
    everywhere but one-to-one. Measured on Viridian Forest: 11 colours -> 11,
    and 35 -> 35, lifting a raw 0.0000 to a correct 0.9972.

    MUTATION that fails this: refuse the lookup outright. Every weather-shaded
    map would be reported as misaligned artwork.
    """
    spec = SPECS["crystal-us"]
    want = _ladder_image(spec)
    shot = np.clip(V.on_ladder(want, 5) - 3, 0, 31).astype(np.int16) << 3
    got = _pair(spec, want, shot)
    assert got["direct"] < 0.05, "the fixture must actually need the lookup"
    assert got["match"] > 0.95
    assert got["via"] == "colour map"


# -- 4. misaligned vs not-a-map ------------------------------------------------

def _row(turn, match, margin, map_frame=True, **kw):
    base = {"turn": turn, "match": match, "direct": match, "raw8": match,
            "wrong_crop": 0.2, "wrong_map": 0.1, "margin": margin, "self": 1.0,
            "map_frame": map_frame}
    base.update(kw)
    return base


def test_a_frame_that_shows_no_map_is_excluded_not_averaged_in():
    """A battle screen matches nothing anywhere, so it is evidence about the
    frame, not about the artwork.

    MUTATION that fails this: take the median over every sampled turn. The two
    lowest Emerald scores on 2026-09-21 were both Mudkip fighting a Zigzagoon,
    and folding them in drags a correct atlas toward SUSPECT.
    """
    rows = [_row(t, 0.99, 0.35) for t in range(1, 6)]
    rows += [_row(90, 0.33, -0.003, map_frame=False), _row(91, 0.31, 0.001, map_frame=False)]
    v = V.verdict(rows)
    assert v["turns"] == 5
    assert v["match"] == pytest.approx(0.99)
    assert v["not_map_frames"] == [90, 91]
    assert v["verdict"] == "ALIGNED"


def test_a_negative_margin_is_misaligned_even_when_the_match_looks_fine():
    """The classic silent failure: the atlas is off by a constant, so the
    declared crop scores respectably and a NEIGHBOUR scores better.

    MUTATION that fails this: leave `margin` out of the verdict. An atlas one
    tile out would pass on `match` alone.
    """
    v = V.verdict([_row(t, 0.92, -0.04) for t in range(1, 6)])
    assert v["verdict"] == "MISALIGNED"
    assert any("margin" in r for r in v["reasons"])


def test_nothing_is_quoted_when_the_self_control_fails():
    """MUTATION that fails this: report the verdict anyway and mention `self`
    in a footnote. That is precisely what happened for weeks."""
    rows = [_row(t, 0.99, 0.35, **{"self": 0.82}) for t in range(1, 6)]
    v = V.verdict(rows)
    assert v["verdict"] == "READER BROKEN"
    assert v["self"] == pytest.approx(0.82)


def test_a_spread_too_small_to_tell_a_wrong_crop_apart_is_suspect_not_aligned():
    """MUTATION that fails this: drop the spread clauses and gate on `match`
    alone. A metric that scores a 4-tile-off crop the same as the right one is
    not measuring alignment, however high its number is."""
    rows = [_row(t, 0.99, 0.35, wrong_crop=0.95, wrong_map=0.94) for t in range(1, 6)]
    v = V.verdict(rows)
    assert v["verdict"] == "SUSPECT"


def test_the_saturation_guard_voids_the_band_when_every_arm_is_at_the_ceiling():
    """MUTATION that fails this: delete `saturated`. All-99% with all-99%
    controls is a measurement of the ceiling, not of the alignment."""
    ceiling = {"g": {"match": 0.999, "wrong_crop": 0.995}}
    floor = {"g": {"match": 0.999, "wrong_crop": 0.10}}
    assert V.saturated(ceiling)
    assert not V.saturated(floor)


# -- 5. reading a run ----------------------------------------------------------

def test_a_ds_trace_is_spelled_the_way_the_atlas_spells_it(tmp_path):
    """An observed graph spells a DS map "342"; the atlas and the route wire
    spell it "342:0". Mismatching the two yields "no data", not an error.

    MUTATION that fails this: emit `str(map_id)`. Every DS turn silently finds
    no map and the tool reports "nothing to check" on a run full of data.
    """
    run = tmp_path / "run"
    (run / "screenshots").mkdir(parents=True)
    (run / "events.jsonl").write_text(json.dumps({
        "type": "turn_input_trace", "turn": 1, "end_tile": [None, None, 144, 845],
        "samples": [{"map_group": None, "map_num": None, "map_id": 342,
                     "x": 144, "y": 845, "in_battle": False}]}) + "\n")
    assert V.turn_positions(run) == {1: ("342:0", 144, 845)}


def test_a_gba_trace_keeps_the_group_and_number_spelling(tmp_path):
    """MUTATION: pad a GBA key with `:0` too. `"4:1"` would become `"4:1:0"`
    and match nothing in the atlas."""
    run = tmp_path / "run"
    (run / "screenshots").mkdir(parents=True)
    (run / "events.jsonl").write_text(json.dumps({
        "type": "turn_input_trace", "turn": 3, "end_tile": [4, 1, 7, 4],
        "samples": [{"map_group": 4, "map_num": 1, "x": 7, "y": 4}]}) + "\n")
    assert V.turn_positions(run) == {3: ("4:1", 7, 4)}


def test_the_sample_is_spread_across_the_run_not_taken_from_the_front():
    """Sampling the first n turns measures the first room the run was in.

    MUTATION that fails this: `return items[:n]`.
    """
    got = V.spread(4, list(range(100)))
    assert got[0] == 0 and got[-1] == 99
    assert len(got) == 4
    assert max(got) - min(got) == 99


def test_a_run_whose_battle_probe_never_fired_is_called_out(tmp_path):
    """Two v2 runs report `in_battle` on zero of ~385 traces while later runs
    of the same games report it on dozens. Their battle frames reach every
    consumer unfiltered.

    MUTATION that fails this: only warn when a run has no trace at all. The
    dead-probe case looks identical to a peaceful run from the outside, and
    that is the point of the warning.
    """
    run = tmp_path / "run"
    run.mkdir()
    lines = [json.dumps({"type": "turn_input_trace", "turn": t, "end_tile": [0, 1, 2, 3],
                         "samples": [{"map_group": 0, "map_num": 1, "x": 2, "y": 3,
                                      "in_battle": False}]})
             for t in range(120)]
    (run / "events.jsonl").write_text("\n".join(lines))
    assert "never fired" in (V.battle_flag_warning(run) or "")

    fought = lines[:119] + [json.dumps(
        {"type": "turn_input_trace", "turn": 119, "end_tile": [0, 1, 2, 3],
         "samples": [{"map_group": 0, "map_num": 1, "x": 2, "y": 3, "in_battle": True}]})]
    (run / "events.jsonl").write_text("\n".join(fought))
    assert V.battle_flag_warning(run) is None


# -- 6. the pre-registration cannot drift from the gate ------------------------

def test_the_notes_quote_the_band_the_script_actually_applies():
    """A band pre-registered on a page and a different band in the code is no
    pre-registration at all.

    MUTATION that fails this: change any threshold in `BAND` without editing
    `artifacts/game-map-render/notes/verify.md`.
    """
    if not NOTES.is_file():
        pytest.skip("notes not written in this checkout")
    text = NOTES.read_text()
    for key in ("aligned_match", "suspect_match", "spread_wrong_crop",
                "spread_wrong_map", "margin", "self_floor"):
        assert str(V.BAND[key]) in text, f"{key} = {V.BAND[key]} is not in {NOTES.name}"


# -- 7. end to end, on real runs -----------------------------------------------

def _runs(token: str) -> list[Path]:
    return sorted(p for p in RUNS.glob(f"*config-v2-{token}__*") if p.is_dir()) \
        if RUNS.is_dir() else []


@pytest.mark.parametrize("game,token", [("crystal-us", "crystal"), ("firered-us", "firered")])
def test_the_right_crop_beats_a_four_tile_offset_on_a_real_run(game, token, tmp_path):
    """The control that makes the whole exercise worth anything.

    MUTATION that fails this: offset the correct window by four tiles in
    `TileProjection.window` (i.e. make the tool's own crop the wrong one).
    `match` drops to the wrong-crop level and the spread closes, which is
    exactly the state the tool exists to detect.
    """
    runs = _runs(token)
    if not runs:
        pytest.skip(f"no {token} run in local/runs")
    v = V.run_game(game, runs, turns=4, z=1, out_dir=tmp_path)
    if v.get("match") is None:
        pytest.skip(f"no scoreable turn: {v.get('why')}")
    assert v["self"] >= V.BAND["self_floor"]
    assert v["match"] - v["wrong_crop"] >= V.BAND["spread_wrong_crop"]
    assert v["match"] - v["wrong_map"] >= V.BAND["spread_wrong_map"]
    assert v["margin"] > 0
    assert (tmp_path / Path(v["sheet"]).name).is_file()


def test_the_sheet_pairs_each_turn_with_the_frame_it_came_from(tmp_path):
    """The deliverable is a picture, so assert the picture exists and is the
    shape the layout promises: four control panels wide.

    MUTATION that fails this: write the sheet without the controls strip. The
    file still appears and the number still prints, and nobody can see what
    "clearly worse" looks like.
    """
    runs = _runs("crystal")
    if not runs:
        pytest.skip("no crystal run in local/runs")
    v = V.run_game("crystal-us", runs, turns=3, z=1, out_dir=tmp_path)
    if v.get("match") is None:
        pytest.skip(v.get("why", "nothing scoreable"))
    spec = SPECS["crystal-us"]
    with Image.open(tmp_path / Path(v["sheet"]).name) as im:
        assert im.width == 26 * 2 + spec.width * 4 + 14 * 3
        assert im.height > spec.height * len(v["rows"])
