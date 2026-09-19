"""`--pace` is a launch-time flag, and it reaches the key spectate reads.

Pacing is the one knob a person reaches for at the moment they start a run —
"I want to watch this one" — so a config-only key would mean editing a file for
the single case the option exists to serve.

The claim under test is not that argparse accepts the word. It is that the flag
writes the SAME key `spectate.resolve_pace` reads, so the two cannot drift into
disagreeing about where pacing lives. Each test therefore asserts through
`resolve_pace`, never against the literal string.
"""
import sys

import pytest

from src.cli import runner
from src.dashboard.spectate import DEFAULT_PACE, resolve_pace


def _prepared(monkeypatch, argv):
    """Drive the real argparse to the point where configs are prepared."""
    seen = {}

    def fake_prepare(path, model, **kw):
        # A real ROM path: main() checks the file exists before it launches
        # anything, and that check runs ahead of the pacing block under test.
        return {"emulator": {"type": "skyemu", "rom_path": ROM},
                "run_name": "t", "llm_model": model}

    # Captured at `apply_cli_record`, which is the first thing after the pacing
    # block that receives the WHOLE prepared list. `run_prepare_phase` is the
    # obvious hook and the wrong one — it takes `prepared[0]`, so a fan-out test
    # hung on it would silently only ever see the first config.
    def stop(args_, prepared, **kw):
        seen["prepared"] = prepared
        raise SystemExit(0)

    monkeypatch.setattr(runner, "prepare_config", fake_prepare)
    monkeypatch.setattr(runner, "apply_cli_record", stop)
    monkeypatch.setattr(sys, "argv", ["pokemon run", *argv])
    with pytest.raises(SystemExit):
        runner.main()
    return seen["prepared"]


ROM = "roms/Pokemon - FireRed Version (USA, Europe) (Rev 1).gba"
BASE = ["--model", "gpt-6-astra(low)", "--turns", "1"]


def test_no_flag_leaves_the_config_alone_and_resolves_to_the_default():
    # Decision B: fast by default. Asserted through resolve_pace so that if the
    # default ever moves, this test moves with it rather than pinning a stale word.
    assert resolve_pace({"emulator": {"type": "skyemu"}}) == DEFAULT_PACE


def test_the_flag_reaches_the_key_spectate_reads(monkeypatch):
    for word in ("fast", "realtime"):
        (cfg,) = _prepared(monkeypatch, BASE + ["--pace", word])
        assert resolve_pace(cfg) == word


def test_an_unknown_pace_is_refused_by_argparse_not_at_run_time(monkeypatch):
    # A typo must cost nothing. argparse rejects before an emulator launches.
    monkeypatch.setattr(sys, "argv", ["pokemon run", *BASE, "--pace", "slow"])
    with pytest.raises(SystemExit) as exc:
        runner.main()
    assert exc.value.code == 2  # argparse usage error, not a crash mid-run


def test_the_flag_applies_to_every_pair_of_a_fan_out(monkeypatch):
    prepared = _prepared(
        monkeypatch,
        ["--model", "gpt-6-astra(low)", "gpt-6-astra(medium)", "--turns", "1", "--pace", "realtime"],
    )
    assert len(prepared) == 2
    assert [resolve_pace(c) for c in prepared] == ["realtime", "realtime"]
