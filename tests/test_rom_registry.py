"""Tests for multi-ROM support — choosing which game a casual run plays.

The emulator holds one cartridge at a time, so "which game" shows up in five
places, and this file covers each with the control that proves it bites:

* the registry (`src.app.roms`) — load/validate, and the sha1 + game-code
  claims checked against the actual files on disk;
* the DERIVATION that makes Emerald casual-only — benchmark capability comes
  from a ladder's `game:`, never a flag, so the control is a fake ladder that
  DOES claim Emerald and flips the answer;
* the wiring — executor branches (official / casual / continue), the queue,
  and the enqueue API;
* the reconcile — the supervisor's ROM switch, its no-op case, and its refusal
  mid-run;
* the prompts — `game_name` reaching the Player, the TaskMaster and the
  research tool, because a model told it is playing FireRed while looking at
  Emerald is worse than one told nothing.

No mGBA and no network: the supervisor's prepare/connect/cleanup seam is faked
(as in test_app_supervisor) and the executor gets a stub config builder.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from src.app.roms import (
    GAME_CODE_ADDR,
    GAME_CODE_LEN,
    Rom,
    apply_rom,
    benchmark_games,
    default_rom,
    fill_game_name,
    get_rom,
    list_roms,
    load_roms,
    rom_for_game,
    rom_for_path,
    rom_supports_benchmarks,
    validate_rom,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


# --- the registry --------------------------------------------------------------


def test_registry_lists_both_games_with_firered_default():
    roms = {r.id: r for r in load_roms()}
    assert set(roms) == {"firered", "emerald"}
    assert roms["firered"].is_default is True
    assert roms["emerald"].is_default is False
    assert default_rom().id == "firered"


def test_each_rom_declares_a_distinct_game_and_code():
    roms = load_roms()
    assert len({r.game for r in roms}) == len(roms)
    assert len({r.game_code for r in roms}) == len(roms)
    assert get_rom("firered").game_code == "BPRE"
    assert get_rom("emerald").game_code == "BPEE"


@pytest.mark.parametrize("rom_id", ["firered", "emerald"])
def test_declared_sha1_and_game_code_match_the_file_on_disk(rom_id):
    """The registry's sha1/game_code are load-bearing — the benchmark gate reads
    the hash and the loaded-ROM check reads the code — so assert them against
    the real dumps rather than trusting the YAML. Skipped when the file isn't
    there: ``roms/`` is gitignored, so a fresh clone legitimately has neither."""
    rom = get_rom(rom_id)
    path = REPO_ROOT / rom.path
    if not path.exists():
        pytest.skip(f"{rom.path} not on this machine")
    assert hashlib.sha1(path.read_bytes()).hexdigest() == rom.sha1
    header = path.read_bytes()[GAME_CODE_ADDR - 0x08000000 :][:GAME_CODE_LEN]
    assert header.decode("ascii") == rom.game_code


def test_firered_sha1_is_one_the_ladder_accepts():
    """The FireRed entry and every FireRed ladder have to name the same dump, or
    an official run would be scored against gate addresses authored for another
    revision. This is the only thing tying the two files together."""
    from src.app.benchmarks import load_benchmarks
    from src.referee.checkpoints import load_ladder

    firered = get_rom("firered")
    for bench in load_benchmarks():
        ladder = load_ladder(bench.ladder)
        if ladder.game != firered.game:
            continue
        assert firered.sha1 in set(ladder.rom_sha1.values()), bench.id


def test_registry_rejects_a_missing_default(tmp_path):
    reg = tmp_path / "roms.yaml"
    reg.write_text(
        "roms:\n"
        "  - {id: a, name: A, path: p, game: g, game_name: G, game_code: C, sha1: s}\n"
    )
    with pytest.raises(ValueError, match="exactly one rom"):
        load_roms(reg)


def test_registry_rejects_two_defaults(tmp_path):
    reg = tmp_path / "roms.yaml"
    reg.write_text(
        "roms:\n"
        "  - {id: a, name: A, path: p, game: g, game_name: G, game_code: C,"
        " sha1: s, default: true}\n"
        "  - {id: b, name: B, path: q, game: h, game_name: H, game_code: D,"
        " sha1: t, default: true}\n"
    )
    with pytest.raises(ValueError, match="exactly one rom"):
        load_roms(reg)


def test_registry_rejects_a_duplicate_id(tmp_path):
    reg = tmp_path / "roms.yaml"
    reg.write_text(
        "roms:\n"
        "  - {id: a, name: A, path: p, game: g, game_name: G, game_code: C,"
        " sha1: s, default: true}\n"
        "  - {id: a, name: B, path: q, game: h, game_name: H, game_code: D, sha1: t}\n"
    )
    with pytest.raises(ValueError, match="duplicate rom id"):
        load_roms(reg)


def test_unknown_id_falls_back_to_default_but_validation_refuses_it():
    """Two deliberately different contracts: dispatch is forgiving (a stale queue
    item still runs) while the API is strict (you don't silently get another
    game than the one you picked)."""
    assert get_rom("gold").id == "firered"
    with pytest.raises(ValueError, match="unknown rom"):
        validate_rom("gold")
    assert validate_rom(None) is None
    assert validate_rom("") is None
    assert validate_rom("emerald") == "emerald"


def test_rom_for_path_resolves_relative_and_absolute():
    firered = get_rom("firered")
    assert rom_for_path(firered.path).id == "firered"
    assert rom_for_path(str(REPO_ROOT / firered.path)).id == "firered"
    assert rom_for_path("roms/Pokemon - Crystal.gbc") is None
    assert rom_for_path(None) is None


# --- the derivation that greys benchmarks out ----------------------------------


def test_only_firered_can_run_benchmarks_today():
    assert benchmark_games() == {"firered-us"}
    assert rom_supports_benchmarks(get_rom("firered")) is True
    assert rom_supports_benchmarks(get_rom("emerald")) is False
    rows = {r["id"]: r for r in list_roms()}
    assert rows["firered"]["benchmark_ok"] is True
    assert rows["emerald"]["benchmark_ok"] is False


def test_authoring_an_emerald_ladder_is_all_it_takes(tmp_path):
    """The control for the test above. Emerald is casual-only because no ladder
    claims its game — not because anything says "emerald: no benchmarks". If this
    fails, capability is hardcoded somewhere and adding a ladder won't be enough."""
    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "emerald.yaml").write_text(
        "benchmark:\n  id: e\n  name: E\n  goal: g\n  default: true\n"
        "game: emerald-us\n"
        "benchmark_version: e\n"
        "rom_sha1: {v1_0: abc}\n"
        "checkpoints: []\n"
    )
    manifest = configs / "benchmarks.yaml"
    manifest.write_text("ladders:\n  - configs/emerald.yaml\n")

    assert benchmark_games(manifest) == {"emerald-us"}
    assert rom_supports_benchmarks(get_rom("emerald"), manifest) is True


def test_a_broken_benchmark_registry_still_lets_you_pick_a_game(tmp_path):
    """Being unable to SCORE is a lesser failure than being unable to pick a
    game at all, so the picker degrades to casual-only rather than 500ing."""
    manifest = tmp_path / "nope.yaml"
    rows = list_roms(benchmarks_path=manifest)
    assert [r["benchmark_ok"] for r in rows] == [False, False]


# --- apply_rom -----------------------------------------------------------------


def test_apply_rom_sets_both_sinks():
    cfg: dict = {"emulator": {"port": 8888}}
    apply_rom(cfg, get_rom("emerald"))
    assert cfg["emulator"]["rom_path"].endswith("Emerald Version (USA, Europe).gba")
    assert cfg["emulator"]["port"] == 8888  # untouched
    assert cfg["game_name"] == "Pokemon Emerald"


def test_apply_rom_resolves_the_game_name_in_the_task_text():
    """The goal is the one place the game gets NAMED to the model, and it is the
    one place no prompt builder fills: it is passed through as a value, so a
    placeholder inside it would reach the model verbatim."""
    cfg = {
        "task": {"goal": "beat {{game_name}}",
                 "description": "Play through {{game_name}} from the start."},
        "system_prompt": "You are playing {{game_name}}.",
        "task_master": {"system_prompt": "The Player is playing {{game_name}}."},
    }
    apply_rom(cfg, get_rom("emerald"))
    blob = json.dumps(cfg)
    assert "{{game_name}}" not in blob
    assert "FireRed" not in blob
    assert cfg["task"]["description"] == "Play through Pokemon Emerald from the start."
    assert cfg["task_master"]["system_prompt"] == "The Player is playing Pokemon Emerald."


def test_the_frozen_config_renders_exactly_as_before_on_firered():
    """Control: the benchmark config now carries a placeholder where it used to
    name FireRed. That is only safe if the RENDERED text is unchanged — otherwise
    every official score since would be against a different prompt."""
    cfg = {"task_master": {"system_prompt":
        "ask a question about {{game_name}} (route order, gym-leader teams)"}}
    apply_rom(cfg, get_rom("firered"))
    assert cfg["task_master"]["system_prompt"] == (
        "ask a question about Pokemon FireRed (route order, gym-leader teams)"
    )


def test_no_config_hardcodes_a_game_in_its_task_text():
    """The rule, not the instance: a config's task text must use the placeholder
    rather than naming a game, or that config silently becomes single-game the
    moment someone plays it on another cartridge."""
    import yaml

    names = [r.game_name for r in load_roms()] + ["FireRed", "Emerald"]
    offenders = []
    for path in sorted((REPO_ROOT / "configs").glob("config-*.yaml")):
        data = yaml.safe_load(path.read_text()) or {}
        task = data.get("task") or {}
        for key in ("goal", "description"):
            text = task.get(key) or ""
            for name in names:
                if name.lower() in str(text).lower():
                    offenders.append(f"{path.name}:task.{key} names {name!r}")
    assert not offenders, "; ".join(offenders)


def test_fill_game_name_leaves_a_config_without_placeholders_alone():
    cfg = {"task": {"goal": "beat the game"}, "system_prompt": "Play well."}
    assert fill_game_name(cfg, "Pokemon Emerald") == 0
    assert cfg == {"task": {"goal": "beat the game"}, "system_prompt": "Play well."}


def test_apply_rom_creates_a_missing_emulator_block():
    cfg: dict = {}
    apply_rom(cfg, get_rom("firered"))
    assert cfg["emulator"]["rom_path"] == get_rom("firered").path


# --- executor wiring -----------------------------------------------------------


def _item(**kw):
    from src.app.models import QueuedRun, RunKind

    return QueuedRun(
        queue_id="q_test",
        kind=kw.pop("kind", RunKind.casual),
        model="claude-haiku-4.5(medium)",
        enqueued_at="2026-08-01T00:00:00Z",
        **kw,
    )


def _executor(tmp_path, **kw):
    from src.app.executor import RunExecutor
    from tests.test_app_executor import fake_prepare_config

    return RunExecutor(
        supervisor=None,
        queue_manager=None,
        run_index=None,
        runs_root=tmp_path / "runs",
        saves_dir=tmp_path / "saves",
        prepare_config_fn=fake_prepare_config,
        **kw,
    )


def test_queued_run_defaults_to_no_rom():
    assert _item().rom is None


def test_casual_default_rom_keeps_the_canonical_start_save(tmp_path):
    cfg, snapshot, _turns = _executor(tmp_path).build_run_config(_item(config="config-3.13"))
    assert cfg["emulator"]["rom_path"] == get_rom("firered").path
    assert cfg["game_name"] == "Pokemon FireRed"
    assert snapshot == "configs/saves/pokebench-v1"


def test_casual_emerald_switches_rom_and_uses_its_own_start_save(tmp_path):
    """The canonical save is a FireRed bedroom state; restoring it under another
    cartridge would load garbage. Emerald therefore gets its OWN committed start
    state — the inside-the-truck save — and must never be handed FireRed's."""
    cfg, snapshot, _turns = _executor(tmp_path).build_run_config(
        _item(config="config-3.13", rom="emerald")
    )
    assert cfg["emulator"]["rom_path"] == get_rom("emerald").path
    assert cfg["game_name"] == "Pokemon Emerald"
    assert snapshot == get_rom("emerald").start_save
    assert snapshot != "configs/saves/pokebench-v1"


def test_emeralds_start_save_is_a_loadable_savepoint():
    """A start_save that points at nothing (or at a dir with no emulator.state)
    would send every run of that game to the title screen while the registry
    claims otherwise — silent, and only visible by watching a run boot."""
    start = get_rom("emerald").start_save
    assert start is not None
    state = REPO_ROOT / start / "emulator.state"
    assert state.is_file(), f"{state} missing"
    assert state.stat().st_size > 1024
    # mGBA savestates are zlib-compressed PNG-ish containers; the canonical
    # FireRed save is the reference for "what the loader accepts".
    canonical = REPO_ROOT / "configs/saves/pokebench-v1/emulator.state"
    assert state.read_bytes()[:4] == canonical.read_bytes()[:4]


def test_a_rom_without_a_start_save_boots_to_the_title_screen(tmp_path, monkeypatch):
    """Control for the two above: the fallback when a game has no committed start
    state must be None (boot the cartridge), NOT the default ROM's save."""
    bare = Rom(
        id="emerald", name="Pokemon Emerald", path="roms/e.gba", game="emerald-us",
        game_name="Pokemon Emerald", game_code="BPEE", sha1="s", start_save=None,
    )
    monkeypatch.setattr("src.app.executor.get_rom", lambda rid, path=None: bare)
    _cfg, snapshot, _turns = _executor(tmp_path).build_run_config(
        _item(config="config-3.13", rom="emerald")
    )
    assert snapshot is None


def test_a_rom_with_a_start_save_uses_it(tmp_path, monkeypatch):
    """Forward control for the "inside the truck" state: once a non-default ROM
    declares a start_save, the casual branch has to load it — otherwise the
    committed state would exist and never be used."""
    import src.app.roms as roms_mod

    truck = Rom(
        id="emerald", name="Pokemon Emerald", path="roms/e.gba", game="emerald-us",
        game_name="Pokemon Emerald", game_code="BPEE", sha1="s",
        start_save="configs/saves/emerald-truck",
    )
    monkeypatch.setattr(roms_mod, "get_rom", lambda rid, path=None: truck)
    monkeypatch.setattr("src.app.executor.get_rom", lambda rid, path=None: truck)
    _cfg, snapshot, _turns = _executor(tmp_path).build_run_config(
        _item(config="config-3.13", rom="emerald")
    )
    assert snapshot == "configs/saves/emerald-truck"


def test_official_takes_the_benchmarks_rom_not_the_items(tmp_path):
    """A score has to come from the dump the gate addresses were authored
    against, so an official run has no ROM choice — even if the item carries one
    (which the API refuses to set, making this the second line of defence)."""
    from src.app.models import RunKind

    cfg, snapshot, _turns = _executor(tmp_path).build_run_config(
        _item(kind=RunKind.official, benchmark="pokebench-easy", rom="emerald")
    )
    assert cfg["emulator"]["rom_path"] == get_rom("firered").path
    assert cfg["game_name"] == "Pokemon FireRed"
    assert snapshot == "configs/saves/pokebench-v1"


def test_rom_for_game_falls_back_rather_than_raising():
    assert rom_for_game("emerald-us").id == "emerald"
    assert rom_for_game("crystal-us").id == "firered"
    assert rom_for_game(None).id == "firered"


def test_continue_inherits_the_source_runs_rom(tmp_path):
    """A continue's game comes from the resumed config, not from the queue item:
    the source run already recorded what it was played on."""
    source = tmp_path / "runs" / "src_run"
    source.mkdir(parents=True)
    resumed = {
        "_llm_alias": "claude-haiku-4.5(medium)",
        "emulator": {"rom_path": get_rom("emerald").path},
        "game_name": "Pokemon Emerald",
    }
    ex = _executor(
        tmp_path, continue_fn=lambda d: (dict(resumed), Path(d) / "savepoint")
    )
    cfg, snapshot, _turns = ex.build_run_config(
        _item(continue_from="src_run", rom="firered")
    )
    assert cfg["emulator"]["rom_path"] == get_rom("emerald").path
    assert cfg["game_name"] == "Pokemon Emerald"
    assert snapshot.endswith("savepoint")


def test_queue_manager_round_trips_the_rom(tmp_path):
    from src.app.models import RunKind
    from src.app.queue_manager import QueueManager

    q = QueueManager(tmp_path / "queue.json")
    q.enqueue(RunKind.casual, "claude-haiku-4.5(medium)", rom="emerald")
    assert QueueManager(tmp_path / "queue.json").items[0].rom == "emerald"


# --- the reconcile (supervisor) ------------------------------------------------


class _Seam:
    """prepare/connect/cleanup stand-ins that record what they were handed."""

    def __init__(self) -> None:
        self.prepared: list[str] = []
        self.cleaned = 0

    def prepare(self, config, saves_dir):
        self.prepared.append(config["emulator"]["rom_path"])
        return {"emu": None, "mgba_proc": None, "slot_cfg": {}}

    def connect(self, handle, timeout=300.0):
        pass

    def cleanup(self, handle):
        self.cleaned += 1


def _supervisor(tmp_path, rom_path="roms/a.gba"):
    from src.app.supervisor import AppSupervisor

    seam = _Seam()
    sup = AppSupervisor(
        {"emulator": {"rom_path": rom_path}},
        tmp_path / "saves",
        prepare_fn=seam.prepare,
        connect_fn=seam.connect,
        cleanup_fn=seam.cleanup,
    )
    return sup, seam


def test_switching_to_the_loaded_rom_does_not_restart(tmp_path):
    """The executor calls this before EVERY run, so the same-ROM case has to be
    free — a restart would demand a Lua re-load between two FireRed runs."""
    sup, seam = _supervisor(tmp_path)
    sup.start()
    sup.switch_rom("roms/a.gba")
    assert seam.prepared == ["roms/a.gba"]
    assert seam.cleaned == 0


def test_switching_to_another_rom_relaunches_with_it(tmp_path):
    sup, seam = _supervisor(tmp_path)
    sup.start()
    sup.switch_rom("roms/b.gba")
    assert seam.prepared == ["roms/a.gba", "roms/b.gba"]
    assert seam.cleaned == 1
    assert sup.rom_path == "roms/b.gba"
    assert sup.status().rom_path == "roms/b.gba"


def test_menu_helpers_are_macos_only_and_fail_closed(monkeypatch):
    """Off macOS there is no Accessibility path, and both helpers must report
    FAILURE rather than success — a False sends the caller down the relaunch /
    load-it-yourself route, while a True would leave it waiting on a swap or a
    handshake that is never coming."""
    import src.cli.runner as runner

    monkeypatch.setattr(runner.sys, "platform", "linux")
    assert runner.load_rom_in_mgba_for_pid(1, "roms/x.gba") is False
    assert runner.load_lua_script_in_mgba_for_pid(1, "lua/socketserver-1.lua") is False


class _Emu:
    """Emulator stub whose cartridge header answers whatever is set on it."""

    def __init__(self, code=b"BPRE"):
        self.code = code

    def read_memory(self, addr, length):
        return self.code

    def disconnect(self):
        pass


def _connected_supervisor(tmp_path, rom_path, emu):
    """A supervisor that believes it has a live Lua connection to ``emu``."""

    class Proc:
        pid = 4242

        def poll(self):
            return None

    sup, seam = _supervisor(tmp_path, rom_path)

    def prepare(config, saves_dir):
        seam.prepared.append(config["emulator"]["rom_path"])
        return {"emu": emu, "mgba_proc": Proc(), "slot_cfg": {}}

    sup._prepare_fn = prepare
    sup.start()
    return sup, seam


def test_in_place_swap_keeps_the_process_and_the_connection(tmp_path, monkeypatch):
    """The whole point of the feature: changing cartridge must NOT relaunch,
    because mGBA's script context — and the Lua socket with it — dies with the
    process, and re-loading the script by hand is the cost being avoided."""
    emu = _Emu(b"BPRE")
    firered, emerald = get_rom("firered"), get_rom("emerald")
    sup, seam = _connected_supervisor(tmp_path, firered.path, emu)

    clicked: list = []

    def fake_click(pid, rom_path):
        clicked.append((pid, rom_path))
        emu.code = b"BPEE"  # the cartridge really changed
        return True

    monkeypatch.setattr("src.cli.runner.load_rom_in_mgba_for_pid", fake_click)
    monkeypatch.setattr("time.sleep", lambda s: None)

    sup.switch_rom(emerald.path)
    assert clicked == [(4242, emerald.path)]
    assert seam.prepared == [firered.path]   # no relaunch
    assert seam.cleaned == 0                 # nothing torn down
    assert sup.rom_path == emerald.path


def test_a_swap_that_does_not_verify_falls_back_to_relaunch(tmp_path, monkeypatch):
    """Control for the test above. The click 'succeeding' is not evidence — a
    menu item can be clicked and the cartridge not change. Only the header read
    decides, and when it disagrees the emulator must still end up on the right
    ROM, by the slow path."""
    emu = _Emu(b"BPRE")
    firered, emerald = get_rom("firered"), get_rom("emerald")
    sup, seam = _connected_supervisor(tmp_path, firered.path, emu)

    # Clicks fine; the header never changes — i.e. the swap silently didn't take.
    monkeypatch.setattr("src.cli.runner.load_rom_in_mgba_for_pid", lambda p, r: True)
    monkeypatch.setattr("time.sleep", lambda s: None)

    sup.switch_rom(emerald.path)
    assert seam.prepared == [firered.path, emerald.path]   # relaunched
    assert seam.cleaned == 1


def test_no_connection_means_no_in_place_attempt(tmp_path, monkeypatch):
    """With no live socket there is nothing to preserve and no way to verify, so
    the in-place path must not even be tried."""
    firered, emerald = get_rom("firered"), get_rom("emerald")
    sup, seam = _supervisor(tmp_path, firered.path)
    sup.start()
    sup._connected = False

    monkeypatch.setattr(
        "src.cli.runner.load_rom_in_mgba_for_pid",
        lambda p, r: pytest.fail("attempted an in-place swap with no connection"),
    )
    sup.switch_rom(emerald.path)
    assert seam.prepared == [firered.path, emerald.path]


def test_an_offregistry_rom_falls_back(tmp_path, monkeypatch):
    """No registry entry → no expected cartridge code → nothing to verify against,
    so the shortcut is refused rather than taken on faith."""
    emu = _Emu(b"BPRE")
    sup, seam = _connected_supervisor(tmp_path, get_rom("firered").path, emu)
    monkeypatch.setattr(
        "src.cli.runner.load_rom_in_mgba_for_pid",
        lambda p, r: pytest.fail("attempted an in-place swap for an unknown ROM"),
    )
    sup.switch_rom("roms/Pokemon - Crystal.gbc")
    assert seam.prepared[-1] == "roms/Pokemon - Crystal.gbc"


def test_busy_survives_a_switch(tmp_path, monkeypatch):
    """`restart()` tears down via `shutdown()`, which clears `busy`. Left alone,
    the supervisor would report IDLE while the executor is mid-dispatch, and a
    concurrent drain could start a second run into a relaunching emulator."""
    firered, emerald = get_rom("firered"), get_rom("emerald")
    sup, _seam = _supervisor(tmp_path, firered.path)
    sup.start()
    sup.set_busy(True)
    seen: list = []
    real_start = sup.start

    def spy_start():
        seen.append(sup.status().busy)   # busy DURING the relaunch
        return real_start()

    monkeypatch.setattr(sup, "start", spy_start)
    sup.switch_rom(emerald.path, force=True)
    assert seen == [True]
    assert sup.status().busy is True


def test_a_dead_emulator_is_not_reported_as_connected(tmp_path):
    """Quitting mGBA left `process_up: false, connected: true` — healthy-looking
    to every consumer, and the app would happily dispatch a run into a process
    that no longer exists. Observed live 2026-08-02."""

    class Proc:
        alive = True

        def poll(self):
            return None if self.alive else 0

    sup, _seam = _supervisor(tmp_path)
    proc = Proc()
    sup._prepare_fn = lambda c, s: {"emu": None, "mgba_proc": proc, "slot_cfg": {}}
    sup.start()
    assert sup.status().connected is True

    proc.alive = False   # the user closed the window
    st = sup.status()
    assert st.process_up is False
    assert st.connected is False


def test_switching_mid_run_is_refused_unless_forced(tmp_path):
    sup, seam = _supervisor(tmp_path)
    sup.start()
    sup.set_busy(True)
    with pytest.raises(RuntimeError, match="while a run is executing"):
        sup.switch_rom("roms/b.gba")
    assert seam.cleaned == 0
    # The executor's pre-dispatch reconcile holds `busy` for a run that has not
    # started yet, and is the one caller allowed through.
    sup.switch_rom("roms/b.gba", force=True)
    assert sup.rom_path == "roms/b.gba"


def test_executor_reconciles_the_rom_before_dispatch(tmp_path):
    calls: list[tuple] = []

    class Sup:
        def switch_rom(self, path, *, force=False):
            calls.append((path, force))

    ex = _executor(tmp_path)
    ex.supervisor = Sup()
    ex._ensure_rom_loaded({"emulator": {"rom_path": "roms/b.gba"}})
    assert calls == [("roms/b.gba", True)]


def test_reconcile_tolerates_a_supervisor_without_the_hook(tmp_path):
    """Every injected test fake predates switch_rom; a hard call would break
    them all, and a run that can't reconcile should still run."""
    import types

    ex = _executor(tmp_path)
    ex.supervisor = types.SimpleNamespace()
    ex._ensure_rom_loaded({"emulator": {"rom_path": "roms/b.gba"}})
    ex.supervisor = types.SimpleNamespace(switch_rom=lambda *a, **k: pytest.fail("no path"))
    ex._ensure_rom_loaded({"emulator": {}})


def test_verify_loaded_rom_reads_the_cartridge_header(tmp_path):
    """The one check that survives a ROM being swapped by hand inside mGBA."""

    class Emu:
        def __init__(self, code):
            self.code = code
            self.reads: list[tuple] = []

        def read_memory(self, addr, length):
            self.reads.append((addr, length))
            return self.code

    sup, _seam = _supervisor(tmp_path)
    sup.start()
    emu = Emu(b"BPRE")
    sup._handle["emu"] = emu
    assert sup.verify_loaded_rom("BPRE") is True
    assert emu.reads == [(GAME_CODE_ADDR, GAME_CODE_LEN)]
    assert sup.verify_loaded_rom("BPEE") is False


def test_verify_loaded_rom_reports_unknown_not_failure(tmp_path):
    """An unanswerable check must never read as a failed one — no connection is
    not evidence of the wrong cartridge."""

    class Dead:
        def read_memory(self, addr, length):
            raise OSError("not connected")

    sup, _seam = _supervisor(tmp_path)
    sup.start()
    assert sup.verify_loaded_rom("BPRE") is None  # handle has emu=None
    sup._handle["emu"] = Dead()
    assert sup.verify_loaded_rom("BPRE") is None


# --- the API -------------------------------------------------------------------


@pytest.fixture
def api(tmp_path):
    import types

    from fastapi.testclient import TestClient

    from src.app.queue_manager import QueueManager
    from src.dashboard import server

    class Sup:
        def __init__(self):
            self.switched: list[str] = []
            self.rom_path = get_rom("firered").path
            self.busy = False

        def status(self):
            return types.SimpleNamespace(busy=self.busy)

        def switch_rom(self, path, *, force=False):
            self.switched.append(path)
            self.rom_path = path

    sup = Sup()
    server.configure_control_plane(
        queue_manager=QueueManager(tmp_path / "queue.json"),
        executor=types.SimpleNamespace(runs_root=tmp_path / "runs", supervisor=sup),
        run_index=types.SimpleNamespace(all=lambda: [], get=lambda rid: None),
    )
    client = TestClient(server.app)
    client.sup = sup
    yield client
    server._CONTROL["queue"] = None
    server._CONTROL["executor"] = None
    server._CONTROL["index"] = None


def test_roms_route_serves_the_registry(api):
    rows = {r["id"]: r for r in api.get("/api/roms").json()}
    assert set(rows) == {"firered", "emerald"}
    assert rows["firered"]["default"] is True
    assert rows["emerald"]["benchmark_ok"] is False
    assert set(rows["emerald"]) == {
        "id", "name", "game", "game_name", "default",
        "benchmark_ok", "has_start_save", "on_disk",
    }


def test_enqueue_accepts_a_known_rom(api):
    r = api.post("/api/queue", json={
        "kind": "casual", "model": "claude-haiku-4.5(medium)",
        "config": "config-3.13", "max_turns": 100, "rom": "emerald",
    })
    assert r.status_code == 201
    assert r.json()["rom"] == "emerald"


def test_enqueue_rejects_an_unknown_rom(api):
    r = api.post("/api/queue", json={
        "kind": "casual", "model": "claude-haiku-4.5(medium)", "rom": "crystal",
    })
    assert r.status_code == 400
    assert "crystal" in r.json()["detail"]


def test_official_enqueue_ignores_a_rom(api):
    r = api.post("/api/queue", json={
        "kind": "official", "model": "claude-haiku-4.5(medium)", "rom": "emerald",
    })
    assert r.status_code == 201
    assert r.json()["rom"] is None


def test_enqueue_without_a_rom_is_unchanged(api):
    r = api.post("/api/queue", json={"kind": "casual", "model": "claude-haiku-4.5(medium)"})
    assert r.status_code == 201
    assert r.json()["rom"] is None


def test_switch_route_starts_a_switch(api):
    r = api.post("/api/emulator/rom", json={"rom": "emerald"})
    assert r.status_code == 202
    assert r.json()["switching_to"] == "emerald"
    # The switch runs on a background thread; it has one job.
    for _ in range(200):
        if api.sup.switched:
            break
        import time

        time.sleep(0.01)
    assert api.sup.switched == [get_rom("emerald").path]


def test_switch_to_the_loaded_rom_is_a_no_op(api):
    r = api.post("/api/emulator/rom", json={"rom": "firered"})
    assert r.status_code == 200
    assert r.json()["switching_to"] is None
    assert api.sup.switched == []


def test_switch_is_refused_mid_run(api):
    api.sup.busy = True
    r = api.post("/api/emulator/rom", json={"rom": "emerald"})
    assert r.status_code == 409
    assert api.sup.switched == []


def test_switch_rejects_an_unknown_rom(api):
    r = api.post("/api/emulator/rom", json={"rom": "crystal"})
    assert r.status_code == 400
    assert api.sup.switched == []


# --- the prompts ---------------------------------------------------------------


def test_player_prompt_is_told_which_game(monkeypatch):
    """`{{game_name}}` in a Player system prompt has to resolve to the ROM's
    game, not to a constant — the whole point of stamping it on the config."""
    from src.core.prompts import fill_prompt

    cfg: dict = {"system_prompt": "You are playing {{game_name}}."}
    apply_rom(cfg, get_rom("emerald"))
    filled = fill_prompt(
        cfg["system_prompt"], game_name=cfg.get("game_name") or "Pokemon FireRed"
    )
    assert filled == "You are playing Pokemon Emerald."


def test_taskmaster_prompt_is_told_which_game():
    from src.agent.task_master import SYSTEM_PROMPT
    from src.core.prompts import fill_prompt

    assert "{{game_name}}" in SYSTEM_PROMPT
    assert "Pokemon FireRed" not in SYSTEM_PROMPT
    filled = fill_prompt(SYSTEM_PROMPT, game_name="Pokemon Emerald")
    assert "plays Pokemon Emerald" in filled


def test_research_tool_asks_about_the_right_game():
    """Route order and gym teams are per-game facts: a sonar model framed on
    FireRed answers confidently and wrongly about Emerald."""
    import asyncio
    from types import SimpleNamespace

    import src.agent.task_master as tm
    from src.agent.tools.ask_perplexity import _system

    assert "Pokemon Emerald playthrough" in _system("Pokemon Emerald")

    seen: dict = {}

    async def _fake(query, model, *, game_name="Pokemon FireRed"):
        seen["game_name"] = game_name
        return {"answer": "ok", "cost_usd": 0.0}

    original = tm._ask_perplexity
    tm._ask_perplexity = _fake
    try:
        deps = SimpleNamespace(
            search_count=0, max_searches=3, search_model="m",
            tool_costs=[], game_name="Pokemon Emerald",
        )
        asyncio.new_event_loop().run_until_complete(
            tm.tool_ask_perplexity(SimpleNamespace(deps=deps), "where is the truck")
        )
    finally:
        tm._ask_perplexity = original
    assert seen["game_name"] == "Pokemon Emerald"
