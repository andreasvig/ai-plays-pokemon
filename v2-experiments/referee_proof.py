"""P5's core claim: the REAL referee latches a REAL gate through SkyEmu.

Everything before this proved the pieces separately — `read_memory` answers,
the start state is the bedroom, x/y moves when the player walks. None of that
is the same as ``src/referee/referee.py``, unmodified, stamping a checkpoint.
This runs that.

The whole adapter is ``Backend`` below. ``referee.py:108`` says the referee's
entire contract with an emulator is ``read_memory(addr, length) -> bytes``, and
that is the only method this class has. Nothing else about SkyEmu is visible to
the referee — not stepping, not screenshots, not save states.

The walk is steered by the referee's OWN distance-to-next-gate, read back out
of the ``referee_position`` event it emits. That makes the navigation a second
check rather than just plumbing: if the memory reads were garbage, the distance
could not fall 9 → 8 → … → 1 on consecutive steps and then flip to the next
gate at the warp. Measured 2026-09-19:

    step  0  map 4:1 (6,6)   dist=9  next=left_bedroom
    ...
    step  8  map 4:1 (10,2)  dist=1  next=left_bedroom
    step  9  map 4:0 (10,2)  dist=12 next=left_house   latched left_bedroom

Direction choice is done by save-state probing — save, try each of the four
directions, reload, keep the best. That costs four loads a step and is only
possible because the emulator is frozen between steps; it is not how a run
plays, it is how this proof avoids hardcoding a route.

The stamped turn number is an artifact of this script polling several times per
step (each probe polls). A run polls once a turn.
"""
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "v2-experiments/harness"))

from skyemu import SkyEmu  # noqa: E402
from src.referee.checkpoints import load_checkpoints  # noqa: E402
from src.referee.referee import Referee  # noqa: E402

PORT = 8151
START = "configs/saves/skyemu/firered-pokebench-v2/emulator.state"
LADDER = "configs/checkpoints-firered-v1.yaml"


class Backend:
    """The entire SkyEmu → referee adapter."""

    def __init__(self, emu):
        self.emu = emu

    def read_memory(self, addr: int, length: int) -> bytes:
        return self.emu.read_memory(addr, length)


class Logger:
    def __init__(self):
        self.events = []

    def log_event(self, event_type, data):
        self.events.append((event_type, data))

    log_custom = log_event


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="referee-proof-"))
    # Copy the ROM somewhere with no .sav beside it — a save file changes
    # FireRed's boot path (see make_start_state.py). Irrelevant once a state is
    # loaded, but it keeps the two scripts honest about what "cold" means.
    rom = tmp / "firered.gba"
    shutil.copy(ROOT / "roms/Pokemon - FireRed Version (USA, Europe) (Rev 1).gba", rom)

    nodes = load_checkpoints(ROOT / LADDER)
    turn = [0]

    def observe(ref, log):
        turn[0] += 1
        ref.poll(turn[0])
        seen = [d for t, d in log.events if t == "referee_position"]
        return seen[-1] if seen else None

    with SkyEmu(rom, port=PORT, log=tmp / "skyemu.log") as emu:
        emu.step(60)
        emu.load_state(ROOT / START)
        emu.step(30)

        log = Logger()
        ref = Referee(nodes, Backend(emu), log, tmp)
        probe = tmp / "probe.state"

        for step in range(30):
            here = observe(ref, log)
            if here is None:
                print("referee read no snapshot — not in game?")
                return 1
            print(f"step {step:2d}  map {here['map_group']}:{here['map_num']} "
                  f"({here['x']},{here['y']})  dist={here['distance']} "
                  f"next={here['next_gate']}  latched={dict(ref.stamps)}")
            if ref.stamps:
                print("\nLATCHED:", dict(ref.stamps))
                for t, d in log.events:
                    if t == "referee_checkpoint":
                        print("  referee_checkpoint:", d)
                return 0

            emu.save_state(probe)
            best = None
            for direction in ("up", "down", "left", "right"):
                emu.load_state(probe)
                emu.step(2)
                emu.press(direction)
                emu.step(16)
                cand = observe(ref, log)
                if (cand["map_group"], cand["map_num"]) != (here["map_group"], here["map_num"]):
                    best = (direction, -1)
                    break
                dist = cand["distance"] if cand["distance"] is not None else 1 << 30
                moved = (cand["x"], cand["y"]) != (here["x"], here["y"])
                if moved and (best is None or dist < best[1]):
                    best = (direction, dist)
            emu.load_state(probe)
            emu.step(2)
            if best is None:
                print("  boxed in — no direction moves the player")
                return 1
            emu.press(best[0])
            emu.step(16)
            print(f"    -> {best[0]}")

    print("ran out of steps without latching")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
