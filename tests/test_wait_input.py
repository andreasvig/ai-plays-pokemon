"""Headless unit tests for the WAIT pseudo-input in press_button_list.

WAIT presses nothing — it splits the sequence and pauses for wait_seconds so
the game can run (battle animations/dialogue) without input. No live emulator:
_send / _drain_buffer are stubbed and time.sleep is monkeypatched.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import src.emulator.emulator as emu_mod
from src.emulator.emulator import EmulatorClient


def _make_emu():
    config = {
        "emulator": {
            "host": "127.0.0.1",
            "port": 8888,
            "button_hold_frames": 12,
            "frames_between_inputs": 24,
            "ab_hold_frames": 12,
            "ab_gap_frames": 60,
            "wait_input_seconds": 5.0,
        },
        "valid_inputs": ["U", "D", "L", "R", "A", "B", "START", "SELECT", "WAIT"],
    }
    emu = EmulatorClient(config)
    emu._sends = []
    emu._send = lambda msg: emu._sends.append(msg)
    emu._drain_buffer = lambda: None
    return emu


def test_wait_splits_sequence_and_pauses(monkeypatch):
    """[a, wait, b] → two SEQ sends in order; WAIT never enters a SEQ; a 5s pause fires."""
    emu = _make_emu()
    sleeps = []
    monkeypatch.setattr(emu_mod.time, "sleep", lambda s: sleeps.append(s))

    emu.press_button_list(["a", "wait", "b"])

    assert emu._sends == ["SEQ:A", "SEQ:B"]
    assert 5.0 in sleeps  # the WAIT pause
    assert emu.facing is None  # no directional pressed


def test_no_wait_is_one_sequence(monkeypatch):
    """A WAIT-free list behaves exactly as before: a single SEQ, facing updated."""
    emu = _make_emu()
    monkeypatch.setattr(emu_mod.time, "sleep", lambda s: None)

    emu.press_button_list(["left", "left", "up", "a"])

    assert emu._sends == ["SEQ:L;L;U;A"]
    assert emu.facing == "up"


def test_wait_alone_presses_nothing(monkeypatch):
    """A lone WAIT sends no SEQ and only sleeps the wait duration."""
    emu = _make_emu()
    sleeps = []
    monkeypatch.setattr(emu_mod.time, "sleep", lambda s: sleeps.append(s))

    emu.press_button_list(["wait"])

    assert emu._sends == []
    assert sleeps == [5.0]
