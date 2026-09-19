"""Which GAME a finished run played, projected onto its index row.

Until the registry grew past FireRed every row was FireRed and nothing had to
say so. With a mixed queue a row without this is ambiguous the moment it
finishes — History, the run list and any report showing two runs side by side
cannot tell you which cartridge either was on.

The fact lives in ``config.json``: ``game_name`` (written by ``apply_rom``, and
what the MODEL was told) plus ``emulator.rom_path`` (which the registry resolves
to a ``game`` key and a console). Three fields rather than one, because two of
them survive cases the third does not — see ``projection._game_of``.

The control that matters here is the NEGATIVE one. Six call sites in ``src/``
read ``config.get("game_name") or "Pokemon FireRed"``; if the projection did the
same, a run that recorded nothing would show up as a FireRed run, which is a
claim rather than a gap. So every test below that produces a None asserts it is
None *and not* FireRed.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.app.projection import project_run_dir
from src.app.roms import get_rom


def _run_dir(tmp_path: Path, config: dict, name: str = "run") -> Path:
    run = tmp_path / f"2026-09-19_10-00-00_config-5.1__{name}"
    run.mkdir()
    (run / "run_summary.json").write_text(json.dumps({
        "run_id": run.name,
        "kind": "casual",
        "status": "completed",
        "llm_alias": "claude-haiku-4.5(medium)",
        "session": {"total_turns": 3},
    }))
    (run / "config.json").write_text(json.dumps(config))
    return run


def _config_for(rom_id: str) -> dict:
    """A config shaped exactly as ``apply_rom`` leaves one."""
    rom = get_rom(rom_id)
    return {"emulator": {"rom_path": rom.path}, "game_name": rom.game_name}


# --- the registered games -------------------------------------------------


def test_a_firered_run_projects_firered(tmp_path):
    s = project_run_dir(_run_dir(tmp_path, _config_for("firered")))
    assert (s.game, s.game_name, s.console) == (
        "firered-us", "Pokemon FireRed", "GBA",
    )


def test_a_gameboy_run_and_a_ds_run_project_their_own_console(tmp_path):
    """The forward control for the FireRed test above: three consoles are in the
    registry, and a projection that keyed on nothing would give all three the
    same answer. ``console`` is what tells a spectate view it has two stacked
    screens to render rather than a 3:2 rectangle."""
    crystal = project_run_dir(_run_dir(tmp_path, _config_for("crystal"), "a"))
    assert (crystal.game, crystal.console) == ("crystal-us", "GB")

    black = project_run_dir(_run_dir(tmp_path, _config_for("black2"), "b"))
    assert (black.game, black.console) == ("black2-us", "NDS")
    assert black.game_name == "Pokemon Black 2"


def test_an_absolute_rom_path_still_resolves(tmp_path):
    """A saved ``config.json`` carries whatever path the run was launched with,
    which is often absolute, while the registry authors repo-relative ones.
    ``rom_for_path`` compares them resolved; without that every finished run
    would project a None game while looking perfectly well-formed."""
    rom = get_rom("emerald")
    cfg = {
        "emulator": {"rom_path": str(Path(rom.path).resolve())},
        "game_name": rom.game_name,
    }
    s = project_run_dir(_run_dir(tmp_path, cfg))
    assert s.game == "emerald-us"


# --- and the cases that must NOT become FireRed ---------------------------


def test_an_off_registry_rom_keeps_its_name_and_claims_no_game(tmp_path):
    """A hand-rolled config pointing at an arbitrary file is legitimate, not an
    error. The display name survives — it is what the model was told — while the
    join key does not, because nothing in the registry backs it."""
    cfg = {
        "emulator": {"rom_path": "roms/some-hack-i-built.gba"},
        "game_name": "Pokemon Something Else",
    }
    s = project_run_dir(_run_dir(tmp_path, cfg))
    assert s.game is None
    assert s.console is None
    assert s.game_name == "Pokemon Something Else"


def test_a_rom_removed_from_the_registry_does_not_change_what_the_run_played(tmp_path):
    """Same shape, different cause, and the one that would bite silently: a
    finished run's record must not change meaning because a YAML file was
    edited afterwards. The name is on the run; the key is on the registry."""
    cfg = {
        "emulator": {"rom_path": "roms/Pokemon - Ruby Version (USA).gba"},
        "game_name": "Pokemon Ruby",
    }
    s = project_run_dir(_run_dir(tmp_path, cfg))
    assert s.game is None
    assert s.game_name == "Pokemon Ruby"


def test_a_legacy_run_that_recorded_nothing_projects_nothing(tmp_path):
    """THE control for this whole file. ``turn.py:674``, ``agent.py:343`` and
    four more read ``config.get("game_name") or "Pokemon FireRed"``. If the
    projection copied that, this row would claim FireRed — a claim, from a run
    that recorded nothing. A blank is the honest answer."""
    s = project_run_dir(_run_dir(tmp_path, {"task": {"goal": "beat the game"}}))
    assert s.game is None
    assert s.game_name is None
    assert s.console is None
    assert s.game_name != "Pokemon FireRed"


def test_a_missing_config_file_is_not_a_failure(tmp_path):
    """Legacy run dirs exist without a config.json at all. The row must still
    project — being unable to say which game is a lesser failure than dropping
    the run from the index."""
    run = tmp_path / "2026-09-19_10-00-00_config-5.1__legacy"
    run.mkdir()
    (run / "run_summary.json").write_text(json.dumps({
        "run_id": run.name, "kind": "casual", "status": "completed",
        "llm_alias": "claude-haiku-4.5(medium)", "session": {"total_turns": 1},
    }))
    s = project_run_dir(run)
    assert s is not None
    assert (s.game, s.game_name, s.console) == (None, None, None)


def test_an_unreadable_registry_does_not_stop_a_run_being_indexed(tmp_path, monkeypatch):
    """The registry is read at projection time, so a broken one would otherwise
    take the whole index down — the same failure ``list_roms`` already refuses to
    have for the picker. The display name comes off the run's own config and is
    unaffected, which is the point of keeping it separate from the key."""
    import src.app.roms as roms_mod

    def boom(*_a, **_k):
        raise ValueError("roms.yaml: rom #0 missing or invalid 'console'")

    monkeypatch.setattr(roms_mod, "rom_for_path", boom)
    s = project_run_dir(_run_dir(tmp_path, _config_for("firered")))
    assert s is not None
    assert s.game is None
    assert s.game_name == "Pokemon FireRed"   # off the run, not off the registry
