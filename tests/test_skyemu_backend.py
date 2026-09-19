"""The SkyEmu backend (P1), tested against a fake wire.

No emulator is launched here. Every test drives a :class:`FakeSkyEmu` whose
``_get`` records the request and answers it, so what is pinned is the thing this
backend actually owns: the REQUESTS it makes and the frame arithmetic behind
them. Whether SkyEmu itself steps correctly was settled in
``v2-experiments/harness/selftest.py`` and is not this file's claim.

Three properties get most of the attention, because they are the three places
the semantics changed rather than the mechanism (plan §3.3):

* ``press_button_list`` costs an exact number of FRAMES, not a sleep.
* ``wait_for_stable_screen`` returns EMULATED seconds, so a slow host and a fast
  host get the same number — the whole reason this backend exists.
* ``fetch_trace`` samples once per input, with the same rows the Lua bridge
  produced, so ``src/referee/trace.py`` cannot tell which backend fed it.
"""

import io
import sys
import time
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.emulator import make_emulator
from src.emulator.backends import frame as frame_mod
from src.emulator.backends.skyemu import SkyEmuClient, SkyEmuError
from src.referee.trace import TRACE_SPEC

VALID_INPUTS = ["U", "D", "L", "R", "A", "B", "START", "SELECT", "WAIT"]
GSAVEBLOCK1_PTR = 0x03005008


def _png(size=(240, 160), color=(0, 0, 0)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _restless_frames(n: int) -> list[bytes]:
    """``n`` frames no two of which the settle can call duplicates.

    A shifting white bar, not a shifting fill colour: the comparison downsamples
    to 120x80 GRAYSCALE, and two different fills can land on the same grey. A
    corpus that accidentally repeats would settle early and the test would pass
    for the wrong reason.
    """
    out = []
    for i in range(n):
        img = Image.new("RGB", (240, 160), (0, 0, 0))
        img.paste(Image.new("RGB", (4, 160), (255, 255, 255)), (2 * i % 236, 0))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        out.append(buf.getvalue())
    return out


def _config(**emulator):
    """5.1's timings, so the frame arithmetic below is the shipped arithmetic."""
    return {
        "emulator": {
            "type": "skyemu", "host": "127.0.0.1", "port": 8160,
            "rom_path": "roms/Pokemon - FireRed Version (USA, Europe) (Rev 1).gba",
            "button_hold_frames": 12, "frames_between_inputs": 24,
            "ab_hold_frames": 12, "ab_gap_frames": 100,
            "wait_input_seconds": 5.0,
            **emulator,
        },
        "screenshot": {"upscale_factor": None, "grid_overlay": False},
        "screen_stability": {
            "max_wait": 15.0, "poll_interval": 0.3, "num_frames": 3,
            "threshold_start": 1.0, "threshold_end": 0.98,
        },
        "valid_inputs": list(VALID_INPUTS),
    }


class FakeSkyEmu(SkyEmuClient):
    """A SkyEmuClient whose wire is a list, not a socket.

    Answers exactly as SkyEmu does, trailing NUL included — the NUL is the trap
    that made a working backend look dead for an afternoon (2026-09-18), so the
    fake keeps it rather than handing the client a cleaned-up reply it will
    never see in production.
    """

    def __init__(self, config, *, screens=None, mem=None, save_reply=b"ok\x00",
                 load_reply=b"ok\x00", latency=0.0):
        super().__init__(config)
        self.calls: list[tuple[str, object]] = []
        self.screens = list(screens) if screens else [_png()]
        self.screen_index = 0
        self.mem: dict[int, int] = dict(mem or {})
        self.save_reply = save_reply
        self.load_reply = load_reply
        self.latency = latency
        self.frames_by_call: list[int] = []

    # --- the wire --------------------------------------------------------
    def _get(self, path, params=None, timeout=600.0):
        self.calls.append((path, params))
        if self.latency:
            time.sleep(self.latency)
        if path == "/ping":
            return b"pong\x00"
        if path == "/screen":
            png = self.screens[min(self.screen_index, len(self.screens) - 1)]
            self.screen_index += 1
            return png
        if path == "/step":
            self.frames_by_call.append(int(dict(params)["frames"]))
            return b"ok\x00"
        if path == "/read_byte":
            pairs = params if isinstance(params, list) else list(params.items())
            addrs = [int(v, 16) for k, v in pairs if k == "addr"]
            return ("".join(f"{self.mem.get(a, 0):02x}" for a in addrs)).encode() + b"\x00"
        if path == "/save":
            return self.save_reply
        if path == "/load":
            return self.load_reply
        return b"ok\x00"

    # --- helpers ---------------------------------------------------------
    def steps(self) -> list[int]:
        return [int(dict(p)["frames"]) for path, p in self.calls if path == "/step"]

    def inputs(self) -> list[dict]:
        return [dict(p) for path, p in self.calls if path == "/input"]

    def write_u32(self, addr: int, value: int) -> None:
        for i in range(4):
            self.mem[addr + i] = (value >> (8 * i)) & 0xFF

    def write_bytes(self, addr: int, data: bytes) -> None:
        for i, b in enumerate(data):
            self.mem[addr + i] = b


# --- construction ---------------------------------------------------------


def test_the_factory_builds_it_from_the_config():
    assert isinstance(make_emulator(_config()), SkyEmuClient)


def test_grid_overlay_true_is_refused_not_ignored():
    """The skyemu frame carries no lattice. A config that asks for one is
    making a claim the backend cannot honour, and silently dropping it would
    leave that claim standing in the config file."""
    cfg = _config()
    cfg["screenshot"]["grid_overlay"] = True
    with pytest.raises(ValueError) as exc:
        SkyEmuClient(cfg)
    assert "grid_overlay" in str(exc.value)


# --- transport ------------------------------------------------------------


def test_text_replies_have_their_trailing_nul_removed():
    """``bytes.strip()`` removes ASCII whitespace and NOT NUL, so a naive
    ``== b"pong"`` is false forever and the backend reads as dead while it is
    running and answering."""
    assert SkyEmuClient._text(b"pong\x00") == "pong"
    assert b"pong\x00".strip() != b"pong"        # the control: why _text exists


def test_ping_is_false_and_does_not_raise_when_the_process_is_gone():
    emu = FakeSkyEmu(_config())
    assert emu.ping() is False                    # proc is None — never launched


# --- read_memory ----------------------------------------------------------


def test_read_memory_is_one_round_trip_per_128_bytes():
    emu = FakeSkyEmu(_config(), mem={0x02000000 + i: i & 0xFF for i in range(300)})
    data = emu.read_memory(0x02000000, 300)
    assert data == bytes((i & 0xFF) for i in range(300))
    reads = [c for c in emu.calls if c[0] == "/read_byte"]
    assert len(reads) == 3                        # 128 + 128 + 44, not 300


def test_the_map_parameter_precedes_every_addr():
    """``/read_byte`` ignores ``map`` if it arrives after the addresses — the
    reason the params are a list of pairs and not a dict."""
    emu = FakeSkyEmu(_config(memory_map=9))
    emu.read_memory(0x02000000, 4)
    path, params = [c for c in emu.calls if c[0] == "/read_byte"][0]
    assert params[0] == ("map", 9)
    assert all(k == "addr" for k, _ in params[1:])


def test_no_map_parameter_is_sent_on_gba():
    emu = FakeSkyEmu(_config())
    emu.read_memory(0x02000000, 2)
    _, params = [c for c in emu.calls if c[0] == "/read_byte"][0]
    assert all(k == "addr" for k, _ in params)


def test_a_short_answer_raises_rather_than_returning_short():
    """Every caller indexes the result directly (referee.py), so a short read
    has to be an exception and not a surprise slice."""
    emu = FakeSkyEmu(_config())
    emu._get = lambda path, params=None, timeout=600.0: b"ff\x00"   # 1 byte for 4
    with pytest.raises(RuntimeError) as exc:
        emu.read_memory(0x02000000, 4)
    assert "asked for 4 bytes" in str(exc.value)


# --- press_button_list ----------------------------------------------------


def test_a_press_is_set_step_clear_step():
    """An input is a LEVEL, not an edge: a set-and-clear inside one call
    registers nothing at all."""
    emu = FakeSkyEmu(_config())
    emu.press_button_list(["left"])
    assert emu.inputs() == [{"Left": 1}, {"Left": 0}]
    assert emu.steps() == [12, 24]                # hold, gap


def test_a_and_b_get_their_own_hold_and_gap():
    emu = FakeSkyEmu(_config())
    emu.press_button_list(["a", "left", "b"])
    #             A: 12+100        L: 12+24        B: 12+100
    assert emu.steps() == [12, 100, 12, 24, 12, 100]


def test_wait_is_stepped_not_slept():
    """mGBA sleeps ``wait_input_seconds`` against a wall clock. Here the game
    sees exactly that many seconds and the host pays whatever it pays."""
    emu = FakeSkyEmu(_config())
    started = time.time()
    emu.press_button_list(["wait"])
    assert emu.steps() == [300]                   # 5.0 s * 60
    assert emu.inputs() == []                     # nothing was pressed
    assert time.time() - started < 1.0            # and nothing slept


def test_the_whole_sequence_costs_the_exact_frame_total():
    """The property the wall-clock sleep could not have: total game time is a
    number, identical on every machine."""
    emu = FakeSkyEmu(_config())
    emu.press_button_list(["up", "up", "a", "wait", "b"])
    expected = (12 + 24) * 2 + (12 + 100) + 300 + (12 + 100)
    assert sum(emu.steps()) == expected == 596
    assert emu.frames_stepped == expected


def test_facing_follows_the_last_directional_input():
    emu = FakeSkyEmu(_config())
    emu.press_button_list(["up", "left", "a"])
    assert emu.facing == "left"


def test_a_sequence_with_no_direction_leaves_facing_alone():
    emu = FakeSkyEmu(_config())
    emu.facing = "down"
    emu.press_button_list(["a", "b"])
    assert emu.facing == "down"


def test_an_invalid_button_raises_before_anything_is_pressed():
    """``turn.py`` relies on ValueError to reject a model's bad action. Half of
    a rejected sequence must not reach the game."""
    emu = FakeSkyEmu(_config())
    with pytest.raises(ValueError):
        emu.press_button_list(["up", "banana", "a"])
    assert emu.calls == []


def test_full_names_and_short_codes_normalise_the_same_way():
    emu = FakeSkyEmu(_config())
    assert emu.normalize_button_list(["left", "UP", "a", "wait"]) == ["L", "U", "A", "WAIT"]


# --- wait_for_stable_screen -----------------------------------------------


def _settle_emu(frames, **kw):
    return FakeSkyEmu(_config(), screens=frames, **kw)


def test_a_still_screen_settles_as_soon_as_the_window_fills():
    still = _png(color=(10, 10, 10))
    emu = _settle_emu([still] * 20)
    seconds = emu.wait_for_stable_screen()
    # 3 captures at 18 frames each (0.3 s poll interval), then stable.
    assert emu.last_settle_frames == 54
    assert seconds == pytest.approx(54 / 60.0)


def test_the_return_value_is_emulated_seconds_not_host_wall_clock():
    """The decision this backend exists for. Two runs of the identical settle,
    one with a 25x slower wire, must return the SAME number — host wall clock
    is a property of the CPU that happened to run the benchmark, which is
    exactly the class of v1 artefact v2 removes.

    The latency arm is the control: without it a wall-clock implementation
    would pass this file by accident.
    """
    still = _png(color=(10, 10, 10))
    fast = _settle_emu([still] * 20)
    slow = _settle_emu([still] * 20, latency=0.02)

    t0 = time.time()
    fast_seconds = fast.wait_for_stable_screen()
    fast_wall = time.time() - t0
    t0 = time.time()
    slow_seconds = slow.wait_for_stable_screen()
    slow_wall = time.time() - t0

    assert slow_wall > fast_wall                       # the hosts really differ
    assert fast_seconds == slow_seconds                # the measurement does not
    assert fast.last_settle_frames == slow.last_settle_frames


def test_the_frame_count_is_emitted_beside_the_seconds():
    """A consumer that wants the raw number has it without dividing back."""
    emu = _settle_emu([_png(color=(10, 10, 10))] * 20)
    seconds = emu.wait_for_stable_screen()
    assert emu.last_settle_frames == int(round(seconds * 60))


def test_a_never_settling_screen_stops_at_the_frame_budget():
    """mGBA relaxes its threshold over ``max_wait`` SECONDS of wall clock; the
    budget is converted to frames here and the relaxation runs over frames."""
    # 900 frames / 18 per poll = 50 captures; 120 leaves no chance of the fake
    # repeating its last frame and handing the settle a free duplicate.
    emu = _settle_emu(_restless_frames(120))
    seconds = emu.wait_for_stable_screen()
    # 15 s max_wait -> 900 frames, reached in 18-frame polls.
    assert emu.last_settle_frames == 900
    assert seconds == pytest.approx(15.0)


def test_settling_advances_the_game_rather_than_watching_it():
    """The inversion, made explicit: on mGBA the settle only CAPTURED, because
    the emulator was running itself. Here every poll is a step."""
    emu = _settle_emu([_png(color=(10, 10, 10))] * 20)
    emu.wait_for_stable_screen()
    assert emu.steps() == [18, 18, 18]


# --- capture_screenshot ---------------------------------------------------


def test_a_gba_frame_is_upscaled_six_times_and_carries_no_grid():
    emu = FakeSkyEmu(_config(), screens=[_png((240, 160), (30, 60, 90))])
    img = emu.capture_screenshot(preprocess=True)
    assert img.size == (1440, 960)
    # A grid overlay is red-tinted lines; a flat fill stays flat without one.
    assert img.getcolors(maxcolors=4) == [(1440 * 960, (30, 60, 90))]
    assert emu.last_frame_meta["grid_overlay"] is False
    assert emu.last_frame_meta["system"] == "GBA"


def test_preprocess_false_is_the_native_frame():
    emu = FakeSkyEmu(_config(), screens=[_png((240, 160))])
    assert emu.capture_screenshot(preprocess=False).size == (240, 160)


def test_an_nds_capture_is_restacked_with_a_seam():
    """256x384 is top-over-bottom from ``se_screenshot``. The seam is the only
    mark drawn: two dark DS screens abut invisibly, and the model is told the
    bottom half is the touch screen."""
    emu = FakeSkyEmu(_config(), screens=[_png((256, 384))])
    img = emu.capture_screenshot(preprocess=True)
    meta = emu.last_frame_meta
    assert meta["system"] == "NDS"
    assert img.size == (768, 192 * 3 * 2 + frame_mod.SEAM_PX)
    assert meta["touch_top_row"] == 192 * 3 + frame_mod.SEAM_PX
    assert img.getpixel((10, 192 * 3 + 1)) == frame_mod.SEAM_COLOR


def test_an_unknown_capture_size_is_refused():
    emu = FakeSkyEmu(_config(), screens=[_png((100, 100))])
    with pytest.raises(ValueError):
        emu.capture_screenshot(preprocess=True)


# --- save / load ----------------------------------------------------------


def test_save_checks_the_body_because_the_status_is_always_200(tmp_path):
    """``/save`` with an unwritable path answers HTTP 200 and the word
    ``failed``. Trusting the status line makes a lost savepoint silent."""
    emu = FakeSkyEmu(_config(), save_reply=b"failed\x00")
    with pytest.raises(RuntimeError) as exc:
        emu.save_state(str(tmp_path / "s.state"))
    assert "failed" in str(exc.value)


def test_save_succeeds_on_an_ok_body(tmp_path):
    emu = FakeSkyEmu(_config())
    emu.save_state(str(tmp_path / "nested" / "s.state"))
    path, params = [c for c in emu.calls if c[0] == "/save"][0]
    assert params["path"].endswith("s.state")
    assert (tmp_path / "nested").is_dir()          # parents made, as mGBA's does


def test_load_raises_filenotfound_for_a_missing_path(tmp_path):
    emu = FakeSkyEmu(_config())
    with pytest.raises(FileNotFoundError):
        emu.load_state(str(tmp_path / "nope.state"))


def test_a_refused_state_names_the_format_mismatch(tmp_path):
    """An mGBA savestate is the overwhelmingly likely cause of a ``failed``
    load, and the message is read by someone looking at a run that just died."""
    state = tmp_path / "s.state"
    state.write_bytes(b"not a skyemu state")
    emu = FakeSkyEmu(_config(), load_reply=b"failed\x00")
    with pytest.raises(RuntimeError) as exc:
        emu.load_state(str(state))
    assert "mGBA" in str(exc.value)


def test_load_steps_the_console_so_it_has_redrawn(tmp_path):
    """``/load`` restores memory; it does not repaint.

    Measured 2026-09-19 on the real emulator: after a load an NDS returns a
    frame whose TOP screen is not the restored one — SoulSilver and Black 2
    settle on frame 4, Platinum on frame 3. Black 2's unrendered buffer reads
    215 mean (bright, not black), so the property is "has it redrawn", not "is
    it black". GB/GBA repaint immediately, which is why this survived until the
    DS games arrived.

    The cost of skipping it is the model's FIRST TURN seeing half a screen: a
    SoulSilver run opened with its top screen black and the agent reasoning
    "the game is currently on the intro/menu screen", pressing A into a bedroom
    it could not see."""
    state = tmp_path / "s.state"
    state.write_bytes(b"x")
    emu = FakeSkyEmu(_config())
    emu.load_state(str(state))

    steps = [params for path, params in emu.calls if path == "/step"]
    assert steps, "a load that never steps leaves the console unpainted"
    assert sum(int(p["frames"]) for p in steps) == emu.post_load_frames == 8


def test_the_redraw_step_can_be_switched_off(tmp_path):
    """THE control — without it the test above cannot tell a deliberate step
    from a stepper that always runs. ``post_load_frames: 0`` restores the raw
    behaviour, which is what v2-experiments/determinism.py needs: it measures
    the frame offset a load lands at, and a load that advances 8 would move the
    thing being measured."""
    state = tmp_path / "s.state"
    state.write_bytes(b"x")
    cfg = _config()
    cfg["emulator"]["post_load_frames"] = 0
    emu = FakeSkyEmu(cfg)
    emu.load_state(str(state))
    assert [c for c in emu.calls if c[0] == "/step"] == []


def test_the_redraw_step_bypasses_the_spectate_hooks(tmp_path):
    """These frames are the load finishing, not gameplay. Through the sampler
    they would publish half-drawn frames to the live feed; through the pacer
    they would put a visible stall at the start of every run in exchange for
    nothing."""
    state = tmp_path / "s.state"
    state.write_bytes(b"x")
    emu = FakeSkyEmu(_config())
    sampled, paced = [], []
    emu.sampler = sampled.append
    emu.pacer = paced.append

    emu.load_state(str(state))
    assert sampled == [], "the redraw frames reached the spectate feed"
    assert paced == [], "the redraw frames were throttled"

    # And the hooks still work afterwards, so this did not just unhook them.
    emu.step(2)
    assert paced == [2]


# --- fetch_trace ----------------------------------------------------------


def _traced_emu(**kw):
    emu = FakeSkyEmu(_config(), **kw)
    emu.trace_spec = list(TRACE_SPEC)
    return emu


def test_no_trace_spec_means_no_rows():
    emu = FakeSkyEmu(_config())
    emu.press_button_list(["up", "a"])
    assert emu.fetch_trace() == []


def test_one_row_per_input_with_the_bridges_own_names():
    """v1 samples once per INPUT (socketserver-1.lua:266), not per frame, and
    names the row with the short code it queued."""
    emu = _traced_emu()
    emu.write_u32(GSAVEBLOCK1_PTR, 0x02025594)
    emu.write_bytes(0x02025594, bytes([6, 0, 8, 0, 4, 1]))
    emu.press_button_list(["up", "a"])
    rows = emu.fetch_trace()
    assert [name for name, _ in rows] == ["U", "A"]
    assert rows[0][1][0] == bytes([6, 0, 8, 0, 4, 1])
    assert len(rows[0][1]) == len(TRACE_SPEC)


def test_wait_produces_no_trace_row():
    """WAIT never enters the Lua bridge's queue, so it never produced a row
    there. A row here would land in ``trace.derive``'s ``idle_ab`` bucket and
    change what that number counts."""
    emu = _traced_emu()
    emu.press_button_list(["up", "wait", "down"])
    assert [name for name, _ in emu.fetch_trace()] == ["U", "D"]


def test_fetch_clears_the_rows():
    emu = _traced_emu()
    emu.press_button_list(["up"])
    assert len(emu.fetch_trace()) == 1
    assert emu.fetch_trace() == []


def test_a_pointer_outside_ewram_reads_as_empty_not_as_garbage():
    """The same bound the Lua bridge applies (``sample_range``), so a spec entry
    means the same thing on both backends. Mid-warp FireRed relocates
    SaveBlock1 and the pointer is briefly not a pointer."""
    emu = _traced_emu()
    emu.write_u32(GSAVEBLOCK1_PTR, 0x08000000)        # ROM, not EWRAM
    emu.press_button_list(["up"])
    (name, samples), = emu.fetch_trace()
    assert samples[0] == b""
    assert samples[1] != b""                          # the absolute entry still read


def test_a_range_that_raises_comes_back_as_empty_bytes():
    """Telemetry must never stop a run."""
    emu = _traced_emu()
    original = emu.read_memory

    def boom(addr, length):
        if addr == 0x03005008:
            raise RuntimeError("wire fell over")
        return original(addr, length)

    emu.read_memory = boom
    emu.press_button_list(["up"])
    (_, samples), = emu.fetch_trace()
    assert samples[0] == b""


def test_the_rows_are_the_shape_the_referee_decodes():
    """``src/referee/trace.py`` is the consumer; it must not be able to tell
    which backend produced the rows."""
    from src.referee import trace as trace_mod

    emu = _traced_emu()
    emu.write_u32(GSAVEBLOCK1_PTR, 0x02025594)
    emu.write_bytes(0x02025594, bytes([6, 0, 8, 0, 4, 1]))
    emu.press_button_list(["up"])
    samples = trace_mod.decode_samples(emu.fetch_trace())
    assert samples[0]["input"] == "U"
    assert (samples[0]["x"], samples[0]["y"]) == (6, 8)
    assert (samples[0]["map_group"], samples[0]["map_num"]) == (4, 1)


# --- the sampler hook -----------------------------------------------------


def test_a_sampler_chunks_the_step_instead_of_running_beside_it():
    """SkyEmu answers one request at a time: a ``/screen`` sent from another
    thread during a long ``/step`` does not answer until the step finishes. So
    a recorder has to be interleaved, never concurrent."""
    emu = FakeSkyEmu(_config(), screens=[_png()] * 100)
    captured = []
    emu.sampler = captured.append
    emu.sample_every = 2
    emu.step(10)
    assert emu.steps() == [2, 2, 2, 2, 2]
    assert len(captured) == 5


# ── the port is not an identity ─────────────────────────────────────────────
#
# 2026-09-19, and it cost a live run. A throwaway probe of mine defaulted to the
# port the control center was already on. SkyEmu's http_server EXITS when it
# cannot bind — quietly — so the launch "succeeded", /ping answered from the
# incumbent, /load took a SoulSilver savestate, and Andreas watched his Black 2
# run render a New Bark Town bedroom from turn 17. Nothing raised anywhere.
#
# wait_for_connection's docstring already named this hazard ("an orphan on the
# port makes the NEXT launch answer against a half-loaded ROM"); the guard it
# grew was `self.proc.poll()`, which only catches a child that has already exited
# by the time we look — and the incumbent answers long before that.


class _Wire(SkyEmuClient):
    """A client whose only fake is the wire, so start_server's real body runs."""

    def __init__(self, config, answers):
        super().__init__(config)
        self.answers = answers          # path -> bytes, or an exception to raise
        self.asked: list[str] = []

    def _get(self, path, params=None, timeout=600.0):
        self.asked.append(path)
        a = self.answers.get(path, b"ok\x00")
        if isinstance(a, BaseException):
            raise a
        return a


def test_launching_onto_a_port_that_already_serves_skyemu_is_refused(tmp_path):
    """THE regression. The launch must not happen at all — by the time a client
    has talked to the incumbent it has already driven someone else's machine."""
    rom = tmp_path / "game.gba"
    rom.write_bytes(b"\x00" * 64)
    emu = _Wire(_config(rom_path=str(rom)), {"/ping": b"pong\x00"})
    with pytest.raises(RuntimeError, match="already serving a SkyEmu"):
        emu.start_server()
    assert emu.proc is None, "a process was launched despite the refusal"


def test_a_free_port_is_not_refused(tmp_path):
    """THE control. A guard that refused every launch would also 'fix' this."""
    rom = tmp_path / "game.gba"
    rom.write_bytes(b"\x00" * 64)
    emu = _Wire(_config(rom_path=str(rom)), {"/ping": OSError("connection refused")})
    emu._stage_rom = lambda: rom                      # no copying in a unit test
    import subprocess as _sp

    launched = {}

    def _popen(cmd, **kw):
        launched["cmd"] = cmd

        class _P:
            pid = 4242

            def poll(self):
                return None
        return _P()

    real, _sp.Popen = _sp.Popen, _popen
    try:
        emu.start_server()
    finally:
        _sp.Popen = real
    assert emu.proc is not None
    assert str(emu.port) in [str(c) for c in launched["cmd"]]


def test_a_port_serving_something_that_is_not_skyemu_is_left_to_the_launch(tmp_path):
    """The guard is about SkyEmu specifically. Anything else on the port makes
    the bind fail loudly, which is already handled — and refusing here would turn
    a clear error into a confusing one."""
    rom = tmp_path / "game.gba"
    rom.write_bytes(b"\x00" * 64)
    emu = _Wire(_config(rom_path=str(rom)), {"/ping": b"<!doctype html>"})
    emu._stage_rom = lambda: rom
    import subprocess as _sp

    real, _sp.Popen = _sp.Popen, lambda cmd, **kw: type("P", (), {"pid": 1, "poll": lambda s: None})()
    try:
        emu.start_server()
    finally:
        _sp.Popen = real
    assert emu.proc is not None


def test_connecting_to_an_emulator_running_another_rom_is_refused(tmp_path):
    """The second door, and the one that survives a race: two launches in the
    same second both see a free port. /status names rom-path, so the machine can
    be asked what it is actually running."""
    import json as _json

    emu = _Wire(_config(), {
        "/status": _json.dumps({"rom-path": "saves/Pokemon - Black 2.nds"}).encode(),
    })
    emu._launched_rom = Path("staged/Pokemon - SoulSilver Version (Europe).nds")
    with pytest.raises(ConnectionError, match="somebody else's emulator"):
        emu._refuse_someone_elses_emulator()


def test_connecting_to_our_own_emulator_is_fine(tmp_path):
    """THE control, and it has to tolerate a different DIRECTORY: /status reports
    a path relative to SkyEmu's cwd, while the backend holds the staged copy."""
    import json as _json

    emu = _Wire(_config(), {
        "/status": _json.dumps(
            {"rom-path": "local/app/_session_x/saves/Pokemon - Black 2.nds"}
        ).encode(),
    })
    emu._launched_rom = Path("/tmp/stage/Pokemon - Black 2.nds")
    emu._refuse_someone_elses_emulator()          # must not raise


def test_an_unreadable_status_is_not_treated_as_a_mismatch():
    """A /status that cannot be parsed is no evidence either way, and failing
    closed there would make the backend unusable against any build whose status
    shape differs."""
    emu = _Wire(_config(), {"/status": b"not json"})
    emu._launched_rom = Path("game.nds")
    emu._refuse_someone_elses_emulator()
    emu2 = _Wire(_config(), {"/status": OSError("boom")})
    emu2._launched_rom = Path("game.nds")
    emu2._refuse_someone_elses_emulator()


def test_wait_for_connection_checks_the_rom_before_stepping():
    """Order matters: the check has to happen before boot_frames, or the first
    thing a mis-pointed backend does is step somebody else's machine."""
    import inspect

    src = inspect.getsource(SkyEmuClient.wait_for_connection)
    assert "_refuse_someone_elses_emulator()" in src
    assert src.index("_refuse_someone_elses_emulator()") < src.index("self._step(self.boot_frames)")
