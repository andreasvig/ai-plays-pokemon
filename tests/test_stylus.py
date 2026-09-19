"""The stylus verb (P3b) — the one action that is not a bare button.

Every other input this harness has is a single string from a fixed enum. A tap
is a verb plus two floats, and it has to survive four places at once: the
model's output schema, the prompt's button list, the backend that executes it,
and the report's per-input census. This file pins each of those, plus the thing
that makes the capability safe — **a tap is refused everywhere there is no touch
screen**, in three layers:

1. ``EmulatorClient.__init__`` (mGBA) — answerable from the config alone, so it
   is answered before a socket is bound.
2. ``SkyEmuClient._probe_system`` — ``emulator.type: skyemu`` is compatible with
   a stylus, so whether THIS run has one depends on the ROM. SkyEmu's
   ``/status`` does not name the console, so the check needs a measured frame;
   it runs right after the boot step, before turn 1.
3. ``normalize_button_list`` — a prompted-output model can emit a tap the schema
   never offered, so execution refuses it too.

No emulator is launched. The SkyEmu tests reuse ``FakeSkyEmu`` from
``test_skyemu_backend`` rather than growing a second fake wire.
"""

import io
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent.parent))

from test_skyemu_backend import FakeSkyEmu, _config, _png   # noqa: E402

from src.emulator.backends import frame as frame_mod        # noqa: E402
from src.emulator.backends.mgba import EmulatorClient       # noqa: E402
from src.emulator.backends.skyemu import (                  # noqa: E402
    SKYEMU_TAP, SKYEMU_TOUCH_X, SKYEMU_TOUCH_Y, SkyEmuClient)
from src.emulator.inputs import (                           # noqa: E402
    TAP_INPUT, census_key, format_tap, is_tap, normalize, parse_tap)


def _nds_png(color=(0, 0, 0)) -> bytes:
    """A capture the shape SkyEmu returns on NDS: 256x384, both screens."""
    return _png(size=(256, 384), color=color)


def _touch_config(**emulator):
    cfg = _config(**emulator)
    cfg["valid_inputs"] = list(cfg["valid_inputs"]) + [TAP_INPUT]
    return cfg


# --- the grammar ----------------------------------------------------------


def test_a_tap_round_trips_through_its_token():
    assert parse_tap("tap:0.5,0.42") == (0.5, 0.42)
    assert format_tap(0.5, 0.42) == "tap:0.500,0.420"
    assert parse_tap(format_tap(0.5, 0.42)) == (0.5, 0.42)


def test_the_reader_is_lenient_about_spelling_and_the_writer_is_not():
    """A model writes ``TAP: 0.5 , 0.4``; the log and the wire get one form."""
    assert parse_tap("TAP: 0.5 , 0.4") == (0.5, 0.4)
    assert normalize("TAP:0.5,0.4") == "tap:0.500,0.400"


@pytest.mark.parametrize("token", ["tap:1.5,0.4", "tap:0.4,1.5", "tap:-0.1,0.4"])
def test_an_off_screen_coordinate_is_refused_not_clamped(token):
    """Clamping would move the tap somewhere the model did not aim, and the
    model would then reason about a screen change it did not cause."""
    with pytest.raises(ValueError) as exc:
        parse_tap(token)
    assert "0..1" in str(exc.value)


@pytest.mark.parametrize("token", ["tap:0.5", "tap:", "tap:a,b", "tap:0.5;0.4"])
def test_a_malformed_tap_is_still_recognised_as_a_tap(token):
    """``is_tap`` is shape-only on purpose: a broken tap has to report as a
    broken TAP, not as an unknown button, or the error names the wrong thing."""
    assert is_tap(token)
    with pytest.raises(ValueError):
        parse_tap(token)


def test_a_button_is_not_a_tap():
    for name in ("a", "up", "WAIT", "TAP"):
        assert not is_tap(name)


# --- the census (src/app/projection.py) -----------------------------------


def test_every_tap_lands_in_one_census_bucket():
    """The census counts distinct STRINGS, and each tap carries its own
    coordinates — so without a bucket key, 40 taps render as 40 buckets of 1
    and push every real button off the card."""
    assert census_key("tap:0.500,0.420") == "tap"
    assert census_key("TAP:0.1,0.9") == "tap"
    assert census_key(" A ") == "a"


def test_the_projection_counts_taps_as_one_button(tmp_path):
    from src.app.projection import _input_stats
    events = tmp_path / "events.jsonl"
    events.write_text("\n".join([
        '{"type": "turn_explanation", "turn": 1, "explanation": '
        '{"action": ["tap:0.500,0.420", "a"]}}',
        '{"type": "turn_explanation", "turn": 2, "explanation": '
        '{"action": ["tap:0.100,0.900", "tap:0.200,0.800"]}}',
    ]) + "\n")
    avg, counts = _input_stats(tmp_path)
    assert avg == 2.0
    assert counts == {"tap": 3, "a": 1}


# --- the offer: the model's schema and the prompt --------------------------


def test_a_run_without_the_stylus_sees_the_schema_it_saw_before_p3b():
    """The regression guard. FireRed is what this branch is measured on, and an
    arm whose output schema grew an action it cannot use is not the same arm."""
    from src.agent.agent import _LegacyGameAction
    inputs = _LegacyGameAction.model_json_schema()["properties"]["inputs"]
    assert inputs["items"] == {
        "enum": ["up", "down", "left", "right", "a", "b",
                 "start", "select", "wait"],
        "type": "string",
    }
    assert inputs["description"] == "The buttons to press this turn."


def test_the_touch_schema_takes_a_tap_and_refuses_an_off_screen_one():
    from src.agent.agent import _LegacyGameAction, touch_output_model
    model = touch_output_model(_LegacyGameAction)
    rest = dict(reasoning="r", last_turn_succeeded=None, memory_updates="none")
    assert model(inputs=["tap:0.5,0.42", "a"], **rest).inputs == ["tap:0.5,0.42", "a"]
    for bad in ("tap:1.5,0.42", "tap:0.5", "TAP"):
        with pytest.raises(Exception):
            model(inputs=[bad], **rest)


def test_the_touch_model_does_not_resurrect_the_taskmaster_field():
    """``_LegacyGameAction`` drops ``return_to_taskmaster`` by deleting it from
    ``model_fields`` — and pydantic re-collects fields from the whole MRO when
    you subclass, so a naive subclass gets it back, and back as REQUIRED. The
    first version of ``touch_output_model`` did exactly that."""
    from src.agent.agent import GameAction, _LegacyGameAction, touch_output_model
    for base in (_LegacyGameAction, GameAction):
        model = touch_output_model(base)
        assert set(model.model_fields) == set(base.model_fields)
        assert model.model_json_schema()["title"] == "GameAction"
        assert model.__doc__ == base.__doc__


def test_the_prompt_shows_the_verb_with_its_arguments():
    """A bare ``TAP`` would be the one entry in the button list a model cannot
    act on: every other name IS the action, this one is a verb without a
    target."""
    from src.agent.agent import button_list, touch_enabled
    cfg = {"valid_inputs": ["U", "D", "A", "WAIT", "TAP"]}
    assert button_list(cfg) == "U, D, A, WAIT, TAP:<x>,<y>"
    assert touch_enabled(cfg)
    plain = {"valid_inputs": ["U", "D", "A", "WAIT"]}
    assert button_list(plain) == "U, D, A, WAIT"
    assert not touch_enabled(plain)


# --- layer 1: mGBA cannot hold a stylus at all -----------------------------


def test_mgba_refuses_a_tap_config_when_it_is_built():
    """Not "unsupported today" — unsupportable: mGBA runs GBA/GB ROMs and the
    Lua bridge can only send button names. The earliest honest moment to say so
    is the moment the config becomes a client."""
    cfg = {"emulator": {"host": "127.0.0.1", "port": 8888},
           "valid_inputs": ["U", "D", "A", "TAP"]}
    with pytest.raises(ValueError) as exc:
        EmulatorClient(cfg)
    assert "TAP" in str(exc.value) and "touch screen" in str(exc.value)


def test_mgba_is_untouched_without_a_tap():
    """The control: the same constructor with the shipped list still builds."""
    cfg = {"emulator": {"host": "127.0.0.1", "port": 8888},
           "valid_inputs": ["U", "D", "L", "R", "A", "B", "START", "SELECT", "WAIT"]}
    assert EmulatorClient(cfg).valid_inputs == set(cfg["valid_inputs"])


# --- layer 2: skyemu measures the console before turn 1 --------------------


def test_the_console_is_measured_at_connect_even_without_a_stylus():
    emu = FakeSkyEmu(_config(), screens=[_png()])
    emu._probe_system()
    assert emu.system == "GBA"


def test_a_gba_rom_with_the_stylus_enabled_fails_before_turn_one():
    emu = FakeSkyEmu(_touch_config(), screens=[_png()])
    with pytest.raises(ValueError) as exc:
        emu._probe_system()
    assert "GBA" in str(exc.value) and "touch screen" in str(exc.value)


def test_an_nds_rom_with_the_stylus_enabled_connects():
    emu = FakeSkyEmu(_touch_config(), screens=[_nds_png()])
    emu._probe_system()
    assert emu.system == "NDS" and emu.touch_enabled


def test_an_unreadable_capture_only_fails_the_run_that_needs_the_answer():
    """The probe is diagnostics for a plain run and a precondition for a touch
    one, so an unidentifiable frame must not kill the former."""
    plain = FakeSkyEmu(_config(), screens=[_png(size=(999, 7))])
    plain._probe_system()
    assert plain.system is None
    touch = FakeSkyEmu(_touch_config(), screens=[_png(size=(999, 7))])
    with pytest.raises(RuntimeError):
        touch._probe_system()


# --- layer 3: execution ----------------------------------------------------


def test_a_tap_is_a_level_held_for_the_configured_frames():
    """An input on SkyEmu is a LEVEL, not an edge: set, step the hold, clear,
    step the gap. A set-and-clear inside one call registers nothing at all."""
    emu = FakeSkyEmu(_touch_config(tap_hold_frames=20, tap_gap_frames=24),
                     screens=[_nds_png()])
    emu._probe_system()
    emu.press_button_list(["tap:0.25,0.75"])
    assert emu.inputs() == [
        {SKYEMU_TAP: 1, SKYEMU_TOUCH_X: 0.25, SKYEMU_TOUCH_Y: 0.75},
        {SKYEMU_TAP: 0},
    ]
    assert emu.steps() == [20, 24]


def test_clearing_a_tap_does_not_re_send_its_coordinates():
    """``touch_x``/``touch_y`` are coordinates, not a held input. Sending them
    as 0 on release would claim a touch at the top-left corner."""
    emu = FakeSkyEmu(_touch_config(), screens=[_nds_png()])
    emu._probe_system()
    emu.tap(0.9, 0.9)
    release = emu.inputs()[-1]
    assert release == {SKYEMU_TAP: 0}
    assert SKYEMU_TOUCH_X not in release and SKYEMU_TOUCH_Y not in release


def test_a_tap_runs_in_its_position_among_the_presses():
    """A tap between two presses happens between them — which is why the verb
    stays in ``inputs`` instead of becoming a field of its own."""
    emu = FakeSkyEmu(_touch_config(button_hold_frames=12, frames_between_inputs=24),
                     screens=[_nds_png()])
    emu._probe_system()
    emu.press_button_list(["down", "tap:0.5,0.5", "down"])
    kinds = [next(iter(p)) for p in emu.inputs()]
    assert kinds == ["Down", "Down", SKYEMU_TAP, SKYEMU_TAP, "Down", "Down"]


def test_the_coordinates_reach_the_wire_unrounded_beyond_three_places():
    emu = FakeSkyEmu(_touch_config(), screens=[_nds_png()])
    emu._probe_system()
    emu.press_button_list(["tap:0.123456,0.987654"])
    assert emu.inputs()[0][SKYEMU_TOUCH_X] == 0.123
    assert emu.inputs()[0][SKYEMU_TOUCH_Y] == 0.988


def test_a_tap_is_refused_when_the_run_did_not_ask_for_the_stylus():
    emu = FakeSkyEmu(_config(), screens=[_nds_png()])
    emu._probe_system()
    with pytest.raises(ValueError) as exc:
        emu.press_button_list(["tap:0.5,0.5"])
    assert "valid_inputs" in str(exc.value)
    assert emu.inputs() == []                  # refused BEFORE anything ran


def test_a_bare_tap_verb_names_what_it_is_missing():
    emu = FakeSkyEmu(_touch_config(), screens=[_nds_png()])
    emu._probe_system()
    with pytest.raises(ValueError) as exc:
        emu.press_button_list(["TAP"])
    assert "verb" in str(exc.value)


def test_nothing_runs_when_one_element_of_the_list_is_bad():
    """``base.py``: invalid names raise before anything is pressed, so a bad
    action is rejected rather than half-executed."""
    emu = FakeSkyEmu(_touch_config(), screens=[_nds_png()])
    emu._probe_system()
    with pytest.raises(ValueError):
        emu.press_button_list(["a", "tap:2,2", "b"])
    assert emu.inputs() == [] and emu.steps() == []


# --- the coordinate trap ---------------------------------------------------


def test_an_image_row_maps_to_the_touch_screen_not_to_the_capture():
    """``touch_y`` is normalised over the TOUCH SCREEN, not over the 256x384
    capture: image row r maps to (r - 192) / 192. Reading a row off the stacked
    frame and sending it straight to SkyEmu aims at twice the height."""
    _, meta = frame_mod.prepare(_nds_png())
    top = meta["touch_top_row"]
    assert frame_mod.image_row_to_touch_y(top, meta) == 0.0
    assert frame_mod.image_row_to_touch_y(top + meta["touch_height"], meta) == 1.0
    middle = top + meta["touch_height"] // 2
    assert frame_mod.image_row_to_touch_y(middle, meta) == pytest.approx(0.5, abs=0.01)
    # The control: the same row read as a fraction of the WHOLE image is a
    # different, wrong number — this is the mistake the helper exists to stop.
    assert middle / meta["size"][1] != pytest.approx(0.5, abs=0.01)


def test_the_touch_helper_refuses_a_frame_with_no_touch_screen():
    _, meta = frame_mod.prepare(_png())
    assert meta["system"] == "GBA"
    with pytest.raises(ValueError):
        frame_mod.image_row_to_touch_y(100, meta)


# --- the frame a tap is aimed on -------------------------------------------


def test_the_nds_frame_is_two_stacked_screens_with_a_seam_and_no_grid():
    img, meta = frame_mod.prepare(_nds_png())
    assert meta["system"] == "NDS"
    assert meta["grid_overlay"] is False
    assert img.width == 256 * meta["upscale"]
    assert img.height == 192 * meta["upscale"] * 2 + meta["seam_px"]
    assert meta["touch_top_row"] == 192 * meta["upscale"] + meta["seam_px"]
    seam_row = img.crop((0, 192 * meta["upscale"], img.width,
                         192 * meta["upscale"] + 1)).getcolors()
    assert seam_row == [(img.width, frame_mod.SEAM_COLOR)]


def test_capture_screenshot_reports_where_the_touch_screen_starts():
    emu = FakeSkyEmu(_touch_config(), screens=[_nds_png()])
    emu.capture_screenshot()
    assert emu.last_frame_meta["system"] == "NDS"
    assert emu.last_frame_meta["touch_top_row"] > 0
