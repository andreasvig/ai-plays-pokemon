"""P1's version of P5's claim: the real ``Referee`` latches a real gate through
the **backend**, not through the harness client underneath it.

``referee_proof.py`` proved the CLIENT satisfies the referee: it wrapped
``harness/skyemu.py`` in a six-line ``Backend`` adapter and walked to
``left_bedroom``. That left one thing unproven — that
``src/emulator/backends/skyemu.py``, the class a run actually constructs,
satisfies it too. This file is that proof: the referee is handed the backend
itself, and the walk is driven with ``press_button_list``, the method
``turn.py`` calls, rather than the client's ``press``.

Deliberately a near-copy of ``referee_proof.py`` rather than a refactor of it:
the two scripts assert different things about different objects, and a shared
helper that took "which emulator" as a parameter would make one run prove the
other. The client-level twin stays as it is.

Usage::

    PYTHONPATH=. ./venv/bin/python v2-experiments/referee_proof_backend.py --port 8164
"""
import argparse
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.emulator.backends.skyemu import SkyEmuClient  # noqa: E402
from src.referee.checkpoints import load_checkpoints  # noqa: E402
from src.referee.referee import Referee  # noqa: E402
from src.referee.trace import TRACE_SPEC  # noqa: E402

START = "configs/saves/skyemu/firered-pokebench-v2/emulator.state"
LADDER = "configs/checkpoints-firered-v1.yaml"
DIRECTIONS = ("up", "down", "left", "right")

# Frames stepped after each press before the referee is polled. A real turn
# steps this much and more inside ``wait_for_stable_screen``; this script does
# not settle (four probes a step would make it minutes long), so it has to step
# the transition itself. It is NOT optional padding: ``press_button_list`` is
# frame-exact, where mGBA's sleep handed the game an extra ~0.5 s, and the
# stairs warp out of the bedroom does not finish inside one press's hold+gap.
# With this at 0 the walk reaches the stairs tile and oscillates there forever
# (measured 2026-09-19), which is the same number the client twin's
# ``emu.step(16)`` was supplying.
SETTLE_FRAMES = 16


class Logger:
    def __init__(self):
        self.events = []

    def log_event(self, event_type, data):
        self.events.append((event_type, data))

    log_custom = log_event


def _config(rom: Path, port: int, stage: Path) -> dict:
    """config-6.0's emulator block, inlined so this script does not
    depend on a YAML that a later phase may re-tune."""
    return {
        "emulator": {
            "type": "skyemu", "host": "127.0.0.1", "port": port,
            "rom_path": str(rom), "rom_stage_dir": str(stage),
            "button_hold_frames": 12, "frames_between_inputs": 24,
            "ab_hold_frames": 12, "ab_gap_frames": 100,
            "wait_input_seconds": 5.0, "boot_frames": 60,
        },
        "screenshot": {"upscale_factor": None, "grid_overlay": False},
        "screen_stability": {"max_wait": 15.0, "poll_interval": 0.3, "num_frames": 6,
                             "threshold_start": 1.0, "threshold_end": 0.98},
        "valid_inputs": ["U", "D", "L", "R", "A", "B", "START", "SELECT", "WAIT"],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8164)
    ap.add_argument("--steps", type=int, default=30)
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="referee-proof-backend-"))
    rom = ROOT / "roms/Pokemon - FireRed Version (USA, Europe) (Rev 1).gba"

    nodes = load_checkpoints(ROOT / LADDER)
    turn = [0]

    def observe(ref, log):
        turn[0] += 1
        ref.poll(turn[0])
        seen = [d for t, d in log.events if t == "referee_position"]
        return seen[-1] if seen else None

    emu = SkyEmuClient(_config(rom, args.port, tmp))
    # The referee never sees this; it is set because a real run sets it, and a
    # proof run that skipped it would not be exercising the same object.
    emu.trace_spec = list(TRACE_SPEC)
    emu.start_server()
    try:
        emu.wait_for_connection(timeout=120.0)
        emu.load_state(str(ROOT / START))
        emu.step(30)

        log = Logger()
        # The backend IS the referee's emulator. No adapter — referee.py:108
        # asks for read_memory(addr, length) -> bytes and that is a protocol
        # member (base.py), so the class goes in as it is.
        ref = Referee(nodes, emu, log, tmp)
        probe = tmp / "probe.state"

        for step in range(args.steps):
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
                rows = emu.fetch_trace()
                print(f"  fetch_trace rows collected during the walk: {len(rows)}")
                return 0

            # Direction choice by save-state probing — only possible because the
            # emulator is frozen between steps. Not how a run plays; how this
            # proof avoids hardcoding a route.
            emu.save_state(str(probe))
            best = None
            for direction in DIRECTIONS:
                emu.load_state(str(probe))
                emu.step(2)
                emu.press_button_list([direction])
                emu.step(SETTLE_FRAMES)
                cand = observe(ref, log)
                if (cand["map_group"], cand["map_num"]) != (here["map_group"], here["map_num"]):
                    best = (direction, -1)
                    break
                dist = cand["distance"] if cand["distance"] is not None else 1 << 30
                moved = (cand["x"], cand["y"]) != (here["x"], here["y"])
                if moved and (best is None or dist < best[1]):
                    best = (direction, dist)
            emu.load_state(str(probe))
            emu.step(2)
            if best is None:
                print("  boxed in — no direction moves the player")
                return 1
            emu.press_button_list([best[0]])
            emu.step(SETTLE_FRAMES)
            print(f"    -> {best[0]}  (facing={emu.facing!r})")

        print("ran out of steps without latching")
        return 1
    finally:
        emu.disconnect()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
