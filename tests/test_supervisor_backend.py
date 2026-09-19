"""The supervisor under a backend that is not mGBA, and the refusal that matters.

Written for the mixed-game queue (2026-09-19). Three things are being asserted,
and the third is the reason the file exists.

1. **The supervisor is no longer mGBA-shaped by assumption.** It used to read
   ``handle["mgba_proc"]`` for liveness, which on a SkyEmu handle is absent —
   and absent takes the "fake handle, assume up" branch, so a dead SkyEmu would
   have reported healthy forever.

2. **A backend switch is not what a mixed queue needs.** SkyEmu holds GB, GBA
   and NDS, so Emerald → Platinum changes cartridge and not emulator. That is
   asserted here so nobody builds a switch matrix for a case that does not exist.

3. **mGBA given an NDS cartridge fails SILENTLY.** It does not error — it comes
   up with no cartridge, the Lua connector dials in, and every health check reads
   green while the run plays a black screen for its whole turn budget. There is
   no signal downstream that says "wrong console", so the refusal has to happen
   before anything launches, and it has to be a refusal rather than a warning.

No emulator is launched: the prepare/connect/cleanup seam is injected, as in
test_app_supervisor.
"""

from __future__ import annotations

import pytest

from src.app.supervisor import AppSupervisor
from src.emulator.backends import BACKEND_CONSOLES, backend_holds
from src.app.roms import get_rom


class FakeProc:
    def __init__(self) -> None:
        self._alive = True
        self.pid = 4242

    def poll(self):
        return None if self._alive else 0

    def terminate(self) -> None:
        self._alive = False

    def wait(self, timeout=None):
        self._alive = False
        return 0


class FakeEmu:
    def disconnect(self) -> None:
        pass


def _seam(proc_key: str):
    """prepare/connect/cleanup fakes that build a handle for one backend.

    ``proc_key`` is where that backend's prepare phase puts its process —
    ``mgba_proc`` or ``skyemu_proc`` — which is exactly the difference
    ``_process_up`` used to be blind to.
    """
    state = {"handles": [], "cleaned": 0}

    def prepare(config, saves_dir):
        h = {"emu": FakeEmu(), proc_key: FakeProc(), "caffeinate_proc": None,
             "slot_cfg": {}}
        state["handles"].append(h)
        return h

    def connect(handle, timeout=None):
        pass

    def cleanup(handle):
        state["cleaned"] += 1
        for p in handle.values():
            if hasattr(p, "terminate"):
                p.terminate()

    return state, prepare, connect, cleanup


def _sup(tmp_path, *, backend: str, rom_id: str = "firered"):
    proc_key = "mgba_proc" if backend == "mgba" else "skyemu_proc"
    state, prepare, connect, cleanup = _seam(proc_key)
    config = {"emulator": {"type": backend, "rom_path": get_rom(rom_id).path}}
    sup = AppSupervisor(
        config, tmp_path / "saves",
        prepare_fn=prepare, connect_fn=connect, cleanup_fn=cleanup,
    )
    return sup, state


# --- which backend holds which console ------------------------------------


def test_skyemu_holds_every_console_the_registry_declares():
    """The reason a mixed queue needs no backend switch. If this ever stops
    being true, the switch matrix the plan decided against becomes necessary."""
    from src.app.roms import CONSOLES, load_roms

    assert BACKEND_CONSOLES["skyemu"] >= set(CONSOLES)
    for rom in load_roms():
        assert backend_holds("skyemu", rom.console), rom.id


def test_mgba_does_not_hold_nds():
    assert backend_holds("mgba", "GBA")
    assert backend_holds("mgba", "GB")
    assert not backend_holds("mgba", "NDS")


def test_an_unknown_backend_holds_nothing():
    """Fails closed, for the same reason ``resolve_backend`` refuses a typo
    rather than falling through to mgba: a backend nobody has described cannot
    be assumed capable of anything."""
    assert not backend_holds("mgba2", "GBA")
    assert not backend_holds("", "GBA")


# --- liveness under each backend ------------------------------------------


def test_process_up_follows_a_skyemu_handle(tmp_path):
    """The bug this replaces: ``_process_up`` read ``mgba_proc``, which a SkyEmu
    handle does not carry, and a missing process takes the "assume up" branch —
    so a dead SkyEmu reported healthy and the executor would have dispatched
    into it."""
    sup, state = _sup(tmp_path, backend="skyemu")
    sup.start()
    assert sup.status().process_up is True
    assert sup.status().connected is True

    state["handles"][0]["skyemu_proc"].terminate()
    assert sup.status().process_up is False
    assert sup.status().connected is False, "connected must be gated on the process"


def test_process_up_still_follows_an_mgba_handle(tmp_path):
    """The control: the same assertion for the backend that already worked, so a
    change that fixed SkyEmu by breaking mGBA cannot pass."""
    sup, state = _sup(tmp_path, backend="mgba")
    sup.start()
    assert sup.status().process_up is True
    state["handles"][0]["mgba_proc"].terminate()
    assert sup.status().process_up is False


def test_status_reports_the_backend_and_the_console(tmp_path):
    sup, _ = _sup(tmp_path, backend="skyemu", rom_id="platinum")
    st = sup.status()
    assert st.backend == "skyemu"
    assert st.console == "NDS"


def test_status_reports_no_console_for_an_off_registry_rom(tmp_path):
    """A hand-rolled config may point at any file. None means "cannot say",
    which is not the same as a failure and must not read as one."""
    sup, _ = _sup(tmp_path, backend="skyemu")
    sup._config["emulator"]["rom_path"] = "roms/something-i-built.gba"
    assert sup.status().console is None
    assert sup.status().backend == "skyemu"


# --- the refusal ----------------------------------------------------------


def test_mgba_refuses_an_nds_cartridge(tmp_path):
    sup, _ = _sup(tmp_path, backend="mgba")
    sup.start()
    with pytest.raises(RuntimeError, match="cannot hold a NDS"):
        sup.switch_rom(get_rom("platinum").path)


def test_skyemu_accepts_the_same_cartridge(tmp_path):
    """THE control for the refusal. Without it the test above passes for a
    supervisor that refuses every switch."""
    sup, state = _sup(tmp_path, backend="skyemu")
    sup.start()
    sup.switch_rom(get_rom("platinum").path)
    assert sup.rom_path == get_rom("platinum").path
    assert len(state["handles"]) == 2, "a SkyEmu cartridge change is a relaunch"


def test_a_refused_switch_leaves_the_emulator_running(tmp_path):
    """Why the check sits at the TOP of switch_rom rather than inside the
    relaunch: a rejected switch must leave the supervisor exactly as it found
    it. If it refused after the teardown, an operator who picked the wrong game
    would be left with no emulator and a run queue still draining into it."""
    sup, state = _sup(tmp_path, backend="mgba")
    sup.start()
    handle_before = sup.handle

    with pytest.raises(RuntimeError):
        sup.switch_rom(get_rom("black").path)

    assert sup.handle is handle_before
    assert sup.status().process_up is True
    assert sup.rom_path == get_rom("firered").path, "the config must not have moved"
    assert state["cleaned"] == 0, "nothing was torn down"


def test_an_off_registry_rom_is_not_refused(tmp_path):
    """An unanswerable check must never be reported as a failed one — the same
    rule ``verify_loaded_rom`` follows when it cannot ask. There is no declared
    console here, so the guard stays silent rather than blocking a hand-rolled
    config."""
    sup, _ = _sup(tmp_path, backend="mgba")
    sup.start()
    sup.refuse_if_unholdable("roms/some-hack.gba")   # must not raise
    sup.refuse_if_unholdable("")


def test_the_in_place_swap_is_not_attempted_off_mgba(tmp_path):
    """Driving File > Recent is an mGBA mechanism whose entire value is
    preserving a Lua connection SkyEmu does not have. It must decline by
    BACKEND, not by happening to find no pid — otherwise a future SkyEmu handle
    that carried a pid would send AppleScript at the wrong process."""
    sup, _ = _sup(tmp_path, backend="skyemu")
    sup.start()
    sup._connected = True
    sup.handle["mgba_proc"] = FakeProc()   # a pid it could have used
    assert sup._swap_rom_in_place(get_rom("emerald").path) is False
