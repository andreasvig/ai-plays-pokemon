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
    msg = str(exc.value)
    # Names the render the atlas actually declares, rather than one spelling
    # of it: "3d-ortho" became "3d-pitched" on 2026-09-21 and the reason
    # changed with it, so a hard-coded string would have had to be lowered to
    # keep passing. The claim is unchanged — the refusal says WHICH render and
    # WHY, and the why is still the emulator's perspective camera.
    assert atlas["render"] in msg
    assert "PERSPECTIVE" in msg.upper()
    # And it does not send the reader off to measure a ScreenSpec, which is
    # the instruction for a game that could have one. A DS game cannot.
    assert "--calibrate" not in msg, \
        f"{game} is being told to measure a screen model it cannot have: {msg}"


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
    # the colour clause's own thresholds, on the same terms
    for name, val in (("RECOLOURED_GAP", V.RECOLOURED_GAP),):
        assert f"{name} = {val}" in text, f"{name} = {val} is not in {NOTES.name}"


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


# -- 8. colour is a second claim, and ALIGNED used to make only one of them ----
#
# A Crystal atlas rendered `nite` scores 0.9923 against a daytime run: every
# tile where it should be, every colour wrong, one word covering both. These
# guard the clause that separates the two, and the correction that clause
# needed after it failed its own control.

def _crow(turn, key, indoor, direct, match, name=None):
    """A scored turn shaped for `colour_verdict`: the two colour numbers and
    the atlas's word for whether this map's colours come from the world."""
    return {"turn": turn, "key": key, "name": name, "indoor": indoor,
            "direct": direct, "match": match}


def test_a_wrong_palette_is_reported_and_fails_without_touching_the_geometry():
    """The gap this clause exists to close.

    MUTATION that fails this: delete the colour verdict, or fold it into the
    geometry one. The first ships purple Johto under the word ALIGNED; the
    second calls a perfectly-aligned atlas MISALIGNED, which is also false.
    """
    rows = [_crow(1, "24:3", False, 0.0000, 0.9862, "ROUTE_29"),
            _crow(2, "24:4", False, 0.0002, 0.8965, "NEW_BARK_TOWN")]
    v = V.colour_verdict("crystal-us", rows)
    assert v["colour"] == "COLOURS DIFFER EVERYWHERE"
    assert v["fails"] is True
    assert "time of day" in v["why"]                 # it names the likely cause
    assert "morn/day/nite/dark" in v["why"]


def test_a_one_map_runtime_shade_stays_an_ordinary_aligned():
    """**The control the clause had to survive.** FireRed's Viridian Forest
    under WEATHER_SHADE is a genuine permutation the artwork is supposed to
    absorb (raw 0.0000 -> fitted 0.9972, palette 11 -> 11).

    MUTATION that fails this: fail on any recolour at all. The clause would
    then be worse than the gap it closes — every weather-shaded map in the
    atlas reported as the wrong palette.
    """
    rows = [_crow(1, "1:0", False, 0.0000, 0.9972, "ViridianForest"),
            _crow(2, "3:19", False, 0.9842, 0.9850),
            _crow(3, "4:3", False, 0.9835, 0.9835),
            _crow(4, "5:3", True, 0.9615, 0.9615)]
    v = V.colour_verdict("firered-us", rows)
    assert v["colour"] == "COLOURS DIFFER ON SOME MAPS"
    assert v["fails"] is False
    assert v["recoloured_maps"] == ["1:0"]


def test_indoor_maps_do_not_dilute_the_scope():
    """**The correction.** The clause as first pre-registered counted every
    sampled map, and a daytime Crystal run against the `nite` atlas came out
    "1 of 4 recoloured" — which reads as ordinary weather — because a gen-2
    indoor map declares PALETTE_DAY and is the same picture at midnight.
    Measured 2026-09-21: both outdoor maps 0.0000 and 0.0002 raw, both indoor
    maps 0.9255 and 0.9806. Over the outdoor two it is "all of them".

    MUTATION that fails this: count over every sampled map instead of over the
    outdoor ones (`n_out` -> `n_maps`). The verdict drops to COLOURS DIFFER ON
    SOME MAPS and the defect ships.
    """
    rows = [_crow(1, "24:3", False, 0.0000, 0.9862, "ROUTE_29"),
            _crow(2, "24:4", False, 0.0002, 0.8965, "NEW_BARK_TOWN"),
            _crow(3, "24:5", True, 0.9255, 0.9299, "ELMS_LAB"),
            _crow(4, "24:6", True, 0.9806, 0.9806, "PLAYERS_HOUSE_1F")]
    v = V.colour_verdict("crystal-us", rows)
    assert v["colour"] == "COLOURS DIFFER EVERYWHERE"
    assert v["fails"] is True
    assert v["outdoor_maps"] == 2
    assert "indoor map takes a fixed palette" in v["why"]


def test_the_recoloured_test_is_the_gap_the_lookup_closed_not_the_level_it_reached():
    """NEW_BARK_TOWN fits 0.0002 raw and 0.8965 through the lookup. The 0.8965
    is NPCs; the 0.8963 gap is the palette. The first version of this clause
    required `match >= 0.90` as well and dropped the clearest recolour in the
    sample over four thousandths.

    MUTATION that fails this: add `and r["match"] >= BAND["aligned_match"]`
    back to the recoloured test.
    """
    rows = [_crow(1, "24:3", False, 0.0000, 0.9862),
            _crow(2, "24:4", False, 0.0002, 0.8965)]
    v = V.colour_verdict("crystal-us", rows)
    assert v["recoloured_maps"] == ["24:3", "24:4"]


def test_one_outdoor_map_is_reported_as_ambiguous_rather_than_guessed():
    """Per turn, "the game dimmed the lights on this map" and "the atlas is
    the wrong time of day" are the SAME observation. Only scope separates
    them, and one world-lit map is no scope.

    MUTATION that fails this: call it COLOURS DIFFER ON SOME MAPS (pass) or
    COLOURS DIFFER EVERYWHERE (pick). It is exactly how the daytime Crystal
    run slipped through at `--turns 20`: one route recoloured, two clean
    houses, "1 of 3".
    """
    rows = [_crow(1, "24:3", False, 0.0000, 0.9862, "ROUTE_29"),
            _crow(2, "24:5", True, 0.9255, 0.9299),
            _crow(3, "24:6", True, 0.9806, 0.9806)]
    v = V.colour_verdict("crystal-us", rows)
    assert v["colour"] == "COLOURS DIFFER, CAUSE AMBIGUOUS"
    assert v["fails"] is True
    assert "--turns" in v["why"]


def test_matching_colours_say_so_and_pass():
    """MUTATION that fails this: make the recoloured test fire on any turn
    whose lookup was consulted at all. Every game would read COLOURS DIFFER
    and the verdict would carry no information."""
    rows = [_crow(1, "24:3", False, 0.9896, 0.9900),
            _crow(2, "24:5", True, 0.9555, 0.9555)]
    v = V.colour_verdict("crystal-us", rows)
    assert v["colour"] == "COLOURS MATCH"
    assert v["fails"] is False


def test_the_atlas_is_one_time_of_day_so_exactly_one_kind_of_run_must_differ():
    """End to end, and invariant under which palette the atlas currently holds.

    A GBC atlas is rendered for ONE time of day. This checkout has both a
    night Crystal sample (the two v2 runs) and a day one (the 100-turn 6.0
    run), so whichever way `Game.time_of_day` is set, exactly one of the two
    must come out COLOURS DIFFER and the other COLOURS MATCH. Asserting the
    XOR rather than a fixed direction means this test survives the atlas being
    re-rendered the other way, which is the thing that just happened.

    MUTATION that fails this: any of the colour-clause mutations above, and
    also `RECOLOURED_GAP = 1.01`, which makes nothing ever recoloured.
    """
    night = sorted(p for p in RUNS.glob("*config-v2-crystal__*")) if RUNS.is_dir() else []
    day = [p for p in (RUNS / "2026-09-20_21-18-34_config-6.0__gemini-3-8-flash-low",)
           if p.is_dir()]
    if not night or not day:
        pytest.skip("need both a night and a day Crystal run in local/runs")
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        a = V.run_game("crystal-us", night, turns=16, z=1, out_dir=Path(td))
        b = V.run_game("crystal-us", day, turns=40, z=1, out_dir=Path(td))
    if a.get("match") is None or b.get("match") is None:
        pytest.skip("nothing scoreable")
    # geometry is unaffected either way: the tiles line up in both
    assert a["geometry"] == "ALIGNED" and b["geometry"] == "ALIGNED"
    assert a["margin"] > 0 and b["margin"] > 0
    assert a["fails"] != b["fails"], (
        f"the atlas holds one time of day, so exactly one of these should differ in "
        f"colour — got night={a['colour']!r} day={b['colour']!r}")
    differing = a if a["fails"] else b
    assert "COLOURS DIFFER" in differing["colour"]
    assert differing["recolour_example"]            # names the two actual colours


def test_the_viridian_forest_shade_does_not_fail_a_real_firered_sample():
    """The control, on real data rather than fixtures.

    MUTATION that fails this: fail on any recolour. FireRed would be reported
    as the wrong palette because one of its eleven maps is weather-shaded.
    """
    runs = _runs("firered")
    if not runs:
        pytest.skip("no firered run in local/runs")
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        v = V.run_game("firered-us", runs, turns=20, z=1, out_dir=Path(td))
    if v.get("match") is None:
        pytest.skip(v.get("why", "nothing scoreable"))
    assert v["geometry"] == "ALIGNED"
    assert v["fails"] is False, f"{v['colour']}: {v['why']}"
    if "1:0" in v["recoloured_maps"]:
        assert v["colour"] == "COLOURS DIFFER ON SOME MAPS"


# -- 9. the map-entry frame, a third exclusion class ---------------------------
#
# Emerald turn 49 carries the "LITTLEROOT TOWN" banner across the top-left and
# scores 0.5090 with margin -0.3432 — which is the signal built to mean "a
# neighbouring crop fits better", i.e. the constant-offset failure. It is not
# one. But it CANNOT be told from one by its pixels (measured: at the declared
# crop the disagreement is spread over every tile row, because the camera is
# also still mid-scroll from the warp), so the run's own map-change record is
# required rather than corroborating.

def _banner_frame(spec, w_tiles=9, h_tiles=3):
    """A mask over the whole frame and a `bad` array that is a top-left block."""
    mask = keep_mask(spec)
    bad = np.zeros_like(mask)
    bad[:h_tiles * spec.tile, :w_tiles * spec.tile] = True
    return bad, mask


def test_the_banner_is_found_as_a_shape_at_the_top_left():
    """MUTATION that fails this: require the block to be anywhere rather than
    anchored at (0, 0). A textbox, a prompt or an NPC cluster would qualify."""
    spec = SPECS["emerald-us"]
    bad, mask = _banner_frame(spec)
    got = V.banner_block(bad, mask, spec.tile)
    assert got is not None
    w, h, inside, outside = got
    assert inside == pytest.approx(1.0)      # the block found is all banner
    assert outside >= V.BANNER_OUTSIDE       # and the rest of the frame is clean
    assert w >= V.BANNER_TILES_W and h >= V.BANNER_TILES_H
    assert w <= 9 and h <= 3                 # it does not claim more than was painted

    # moved off the top-left corner, the same block is not a banner
    off = np.zeros_like(mask)
    off[4 * spec.tile:7 * spec.tile, 4 * spec.tile:13 * spec.tile] = True
    assert V.banner_block(off, mask, spec.tile) is None


def test_a_uniformly_wrong_frame_has_no_banner_block():
    """**The one that keeps the exclusion falsifiable.** A genuinely misaligned
    crop disagrees everywhere, so there is no clean outside and no block.

    MUTATION that fails this: drop the `outside_ok >= BANNER_OUTSIDE` clause.
    Every badly-scoring frame would then present a "banner" and the exclusion
    would swallow real misalignments — the exact failure the coordinator
    warned about.
    """
    spec = SPECS["emerald-us"]
    mask = keep_mask(spec)
    bad = np.ones_like(mask)
    assert V.banner_block(bad, mask, spec.tile) is None


def test_a_clean_frame_has_no_banner_block():
    """MUTATION that fails this: drop the `inside_bad >= BANNER_INSIDE` clause.
    Every ordinary frame would report a banner and nothing would be scored."""
    spec = SPECS["emerald-us"]
    mask = keep_mask(spec)
    assert V.banner_block(np.zeros_like(mask), mask, spec.tile) is None


def test_a_map_entry_frame_needs_the_run_record_as_well_as_the_banner(tmp_path):
    """Both, or neither. The pixels alone cannot separate a banner frame from
    a one-tile misalignment — measured on turn 49 — so the non-pixel signal is
    required.

    MUTATION that fails this: `entry_frame = bool(banner)`. Any frame with a
    top-left block and a clean remainder is excluded whether or not the run
    ever entered a map there.
    """
    run = tmp_path / "run"
    (run / "screenshots").mkdir(parents=True)
    ev = []
    for turn, key in ((1, [1, 2]), (2, [1, 2]), (3, [0, 9]), (4, [0, 9])):
        ev.append(json.dumps({"type": "turn_input_trace", "turn": turn,
                              "end_tile": key + [5, 5],
                              "samples": [{"map_group": key[0], "map_num": key[1],
                                           "x": 5, "y": 5}]}))
    (run / "events.jsonl").write_text("\n".join(ev))
    for t in range(1, 6):
        Image.new("RGB", (8, 8)).save(run / "screenshots" / f"0000{t}_turn_{t}.png")
    by_turn = {p.turn: p for p in V.placements(run)}
    # frame 4 is paired with turn 3's position (0:9); turn 2 was on 1:2
    assert by_turn[4].entered is True
    # frame 5 is paired with turn 4 (0:9) and turn 3 was also 0:9
    assert by_turn[5].entered is False


def test_the_entry_frame_is_named_in_the_report_not_quietly_dropped():
    """MUTATION that fails this: exclude the turn without listing it. Nobody
    can tell an inconvenient score from a transitional frame."""
    rows = [_row(t, 0.99, 0.35) for t in (1, 2, 3)]
    rows.append(dict(_row(49, 0.5090, -0.3432), key="0:9", name="LittlerootTown",
                     tile=[14, 8], entry_frame=True, banner=(9, 3, 0.48, 0.996)))
    v = V.verdict(rows)
    assert v["turns"] == 3
    assert v["match"] == pytest.approx(0.99)
    assert [e["turn"] for e in v["entry_frames"]] == [49]
    e = v["entry_frames"][0]
    assert e["map"] == "0:9" and e["tile"] == [14, 8]
    assert e["banner"] == (9, 3, 0.48, 0.996)
    assert e["margin"] == pytest.approx(-0.3432)


def _score_one(game, run, turn, shift=0):
    """Score one named turn of a real run, optionally with the atlas shifted.

    Goes through `score_turn` rather than `run_game` so the banner rule is
    actually reached: at the run level a four-tile offset makes almost every
    frame fail the map-frame floor first, and a test that only looks at the
    run-level result passes for that reason whatever the banner rule does.
    """
    from src.app.stitch import run_spec
    maps_dir = REPO_ROOT / "src" / "dashboard" / "web" / "public" / "maps" / game
    atlas = json.loads((maps_dir / "index.json").read_text())
    spec = V.screen_for(game, atlas)
    real = V.TileProjection.window
    if shift:
        def shifted(self, s_, x, y):
            ox, oy = real(self, s_, x, y)
            return ox + shift * self.tile_px, oy + shift * self.tile_px
        V.TileProjection.window = shifted
    try:
        for p in V.placements(run):
            if p.turn == turn:
                return p, V.score_turn(p, spec, run_spec(spec, run), atlas, maps_dir,
                                       keep_mask(spec), {})
    finally:
        V.TileProjection.window = real
    return None, None


EMERALD_BANNER_RUN = "2026-09-20_20-56-15_config-6.0__gemini-3-8-flash-low"
EMERALD_BANNER_TURN = 49          # "LITTLEROOT TOWN", 0:9, tile (14, 8)


def test_the_real_banner_frame_is_detected_and_a_four_tile_offset_is_not():
    """**The control the exclusion has to survive**, on the real frame: an
    exclusion that hides the failure mode is worse than the noise it removes.

    Turn 49 carries the banner, and its declared crop scores 0.5090 with
    margin -0.3432 — indistinguishable from a one-tile offset by the pixels,
    which is why the run's map-change record is required. With the atlas four
    tiles off, nothing matches at any of the nine candidate crops, so there is
    no clean outside, no block, and the rule fires on nothing.

    MUTATION that fails this: drop the outside-clean clause from
    `banner_block`, or look for the block at the declared crop only.
    """
    run = RUNS / EMERALD_BANNER_RUN
    if not run.is_dir():
        pytest.skip("no flagship emerald run in local/runs")
    p, r = _score_one("emerald-us", run, EMERALD_BANNER_TURN)
    if r is None:
        pytest.skip("turn 49 not placeable in this checkout")
    assert p.entered is True, "the run's own positions say this is the first turn here"
    assert r["banner"] is not None, "the LITTLEROOT TOWN banner should be found"
    assert r["entry_frame"] is True
    assert r["margin"] < 0, "and it is exactly the signal that would read as a defect"

    _, bad = _score_one("emerald-us", run, EMERALD_BANNER_TURN, shift=4)
    assert bad["banner"] is None, (
        "a four-tile offset must not present as a banner — that exclusion would be "
        "hiding the failure mode rather than the noise")
    assert bad["entry_frame"] is False
    assert bad["map_frame"] is False, "it is excluded, but as showing no map at all"


def test_a_four_tile_offset_still_comes_out_misaligned():
    """MUTATION that fails this: any exclusion broad enough to swallow the
    offset. The point of the map-entry class is to remove transitional frames,
    not to make a broken atlas unfalsifiable."""
    run = RUNS / EMERALD_BANNER_RUN
    if not run.is_dir():
        pytest.skip("no flagship emerald run in local/runs")
    import tempfile
    real = V.TileProjection.window

    def shifted(self, spec, x, y):
        ox, oy = real(self, spec, x, y)
        return ox + 4 * self.tile_px, oy + 4 * self.tile_px

    with tempfile.TemporaryDirectory() as td:
        V.TileProjection.window = shifted
        try:
            v = V.run_game("emerald-us", [run], turns=200, z=1, out_dir=Path(td))
        finally:
            V.TileProjection.window = real
    assert v["geometry"] == "MISALIGNED"
    assert v["entry_frames"] == []


def test_both_signals_are_required_to_exclude_a_frame():
    """The conjunction, on its own.

    MUTATION that fails this: `entry_frame = bool(banner)` — any frame with a
    bright rectangle in its corner is dropped whether or not the run ever
    entered a map there. Or `bool(entered)` — every first-frame-on-a-new-map
    is dropped, including a genuinely misaligned one.
    """
    block = (9, 3, 0.48, 0.996)
    assert V.is_entry_frame(True, block) is True
    assert V.is_entry_frame(False, block) is False
    assert V.is_entry_frame(True, None) is False
    assert V.is_entry_frame(False, None) is False


def test_excluding_entry_frames_does_not_flatter_the_controls():
    """If removing transitional frames moved the wrong-crop spread, the spread
    was measuring the transitions.

    MUTATION that fails this: exclude on a low score instead of on the banner
    plus the run record — dozens of frames go, and the controls move.
    """
    run = RUNS / "2026-09-20_20-56-15_config-6.0__gemini-3-8-flash-low"
    if not run.is_dir():
        pytest.skip("no flagship emerald run in local/runs")
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        v = V.run_game("emerald-us", [run], turns=200, z=1, out_dir=Path(td))
    if v.get("match") is None:
        pytest.skip(v.get("why", "nothing scoreable"))
    dropped = len(v["entry_frames"])
    assert dropped <= max(2, v["turns"] // 20), (
        f"{dropped} of {v['turns'] + dropped} turns excluded as map-entry frames — "
        f"this clause is meant to remove the first frame after a warp, not a slice "
        f"of the run")
    # and the frames it keeps still separate a right crop from a wrong one
    assert v["match"] - v["wrong_crop"] >= V.BAND["spread_wrong_crop"]
    assert v["margin"] >= V.BAND["margin"]


# -- 10. the sheet and the table must describe the same run --------------------

def test_the_json_and_the_sheet_share_one_run_derived_name():
    """A fixed `<game>.json` beside a run-named PNG goes stale silently: a
    10:11 sheet next to a 09:56 table of different runs, with nothing in
    either saying so.

    MUTATION that fails this: write the table to `out_dir / f"{game}.json"`.
    """
    runs = _runs("crystal")
    if not runs:
        pytest.skip("no crystal run in local/runs")
    import subprocess
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        r = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "verify_map_alignment.py"),
             *[str(p) for p in runs], "--turns", "6", "--zoom", "1", "--out", td],
            cwd=REPO_ROOT, capture_output=True, text=True,
            env={"PYTHONPATH": str(REPO_ROOT), "PATH": "/usr/bin:/bin"})
        assert r.returncode in (0, 1), r.stderr[-2000:]
        pngs = sorted(Path(td).glob("*.png"))
        jsons = sorted(Path(td).glob("*.json"))
        assert pngs and jsons
        assert {p.stem for p in pngs} == {j.stem for j in jsons}
        body = json.loads(jsons[0].read_text())
        assert body["runs"] == [p.name for p in runs]
        assert body["generated"]
        assert Path(body["sheet"]).name == pngs[0].name


def test_a_row_carries_the_map_the_sheet_prints():
    """The sheet prints the map key and name on every panel; the table has to
    carry them too, or a reader has to go back to the picture.

    MUTATION that fails this: drop `map` / `map_name` from the row.
    """
    runs = _runs("crystal")
    if not runs:
        pytest.skip("no crystal run in local/runs")
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        v = V.run_game("crystal-us", runs, turns=4, z=1, out_dir=Path(td))
    if v.get("match") is None:
        pytest.skip(v.get("why", "nothing scoreable"))
    for r in v["rows"]:
        assert r["map"], f"turn {r['turn']} has no map"
        assert r["map_name"], f"turn {r['turn']} on {r['map']} has no map_name"
        assert r["map"] == r["key"]


def test_a_warp_frame_without_a_banner_is_still_scored():
    """The conjunction at the CALL SITE, not just in `is_entry_frame`.

    On the flagship Emerald run seven turns carry the run's map-change record
    and only one of them carries the banner; the other six are ordinary frames
    that happen to be the first on a new map, and dropping them would throw
    away six perfectly good measurements — including, on a broken atlas, six
    chances to catch it.

    MUTATION that fails this: `"entry_frame": bool(p.entered)` at the call
    site. `test_both_signals_are_required_to_exclude_a_frame` cannot see that
    one, because it tests the function and not who calls it.
    """
    run = RUNS / EMERALD_BANNER_RUN
    if not run.is_dir():
        pytest.skip("no flagship emerald run in local/runs")
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        v = V.run_game("emerald-us", [run], turns=200, z=1, out_dir=Path(td))
    if v.get("match") is None:
        pytest.skip(v.get("why", "nothing scoreable"))
    warped = [r for r in v["rows"] if r["entered"]]
    assert len(warped) > 1, "this run should have several map changes in the sample"
    kept = [r for r in warped if not r["entry_frame"] and r["map_frame"]]
    assert kept, ("every warp frame was excluded — the banner has to be required "
                  "as well, or the clause is dropping ordinary frames")
    assert [r["turn"] for r in warped if r["entry_frame"]] == [EMERALD_BANNER_TURN]
