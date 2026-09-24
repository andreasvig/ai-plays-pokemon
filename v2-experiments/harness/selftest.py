#!/usr/bin/env python3
"""Check the backend contract the turn loop depends on — no model, no API key.

    venv/bin/python v2-experiments/harness/selftest.py \
        --rom "v2-experiments/roms/Pokemon - Platinum Version (USA).nds"

Runs in about a minute. Exists because every failure it catches used to be
invisible: a press that does nothing still returns HTTP 200, and a tap with no
coordinates still "works" on any screen that accepts a touch anywhere — which is
how the first NDS touch test passed against a patch that had not been written
yet (2026-09-18). So each check here is chosen to FAIL if the mechanism is
missing, not merely to succeed when it is present.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from frame import geometry, prepare, image_row_to_touch_y  # noqa: E402
from skyemu import SkyEmu  # noqa: E402

PASS, FAIL = "  ok  ", " FAIL "
failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{PASS if ok else FAIL}] {name}{'  — ' + detail if detail else ''}")
    if not ok:
        failures.append(name)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rom", required=True, type=Path)
    ap.add_argument("--port", type=int, default=8098)
    ap.add_argument("--boot-frames", type=int, default=3000)
    args = ap.parse_args()

    with SkyEmu(args.rom, port=args.port) as emu:
        st = emu.status()
        check("process answers /status", st.get("rom-loaded") is True, st.get("emulator", ""))
        check("headless starts PAUSED", st.get("run-mode") == "PAUSE", str(st.get("run-mode")))

        # "the frame after N steps differs from the frame before" is a WEAK
        # oracle: two static screens are equal for reasons that have nothing to
        # do with stepping. Crystal booted to a screen byte-identical to frame 0
        # at 1,200 frames and failed this check while stepping perfectly — the
        # NDS case had been passing on the luck of an animated intro. So: step
        # in chunks and pass at the FIRST change, which only a running machine
        # can produce.
        before = emu.screen()
        moved_at = None
        stepped = 0
        while stepped < args.boot_frames:
            chunk = min(300, args.boot_frames - stepped)
            emu.step(chunk)
            stepped += chunk
            if moved_at is None and emu.screen() != before:
                moved_at = stepped
        check("/step advances the machine", moved_at is not None,
              f"framebuffer first changed after {moved_at} frames"
              if moved_at else f"unchanged across {stepped} frames")

        after = emu.screen()
        system, w, h = geometry(after)
        check("capture geometry is a console this harness knows", True, f"{system} {w}x{h}")

        png, meta = prepare(after)
        check("no grid overlay on the prepared frame", meta["grid_overlay"] is False)

        if system == "NDS":
            # The claim is about the IMAGE, so check the image: the two halves
            # of a DS capture are different pictures, and the seam sits between
            # them. A side-by-side capture would fail `geometry` above; a
            # capture of one screen twice would pass it and fail here.
            from PIL import Image
            import io
            raw = Image.open(io.BytesIO(after))
            top = raw.crop((0, 0, 256, 192)).tobytes()
            bottom = raw.crop((0, 192, 256, 384)).tobytes()
            check("the stack is two different screens, not one twice", top != bottom)
            check("the touch screen is the LOWER half",
                  meta["touch_top_row"] > meta["size"][1] / 2,
                  f"row {meta['touch_top_row']} of {meta['size'][1]}")
            check("image row -> touch_y maps the seam to 0.0",
                  image_row_to_touch_y(meta["touch_top_row"], meta) == 0.0)
            check("image row -> touch_y maps the last row to 1.0",
                  image_row_to_touch_y(meta["size"][1], meta) == 1.0)

        # A press must be visible as a HELD level while it is held: `/input`
        # sets a level, and the bug this catches is a press that clears itself
        # before any frame observes it.
        emu.set_inputs(A=1)
        held = emu.status()["inputs"]["A"]
        emu.set_inputs(A=0)
        released = emu.status()["inputs"]["A"]
        check("/input holds a button down and releases it", held == 1.0 and released == 0.0,
              f"held={held} released={released}")

        # Savestate round trip, proven by a value that could not survive by
        # accident: write a marker, save, overwrite it, load, read it back.
        # Main RAM, per console. 0x02000000 is EWRAM on GBA and main RAM on the
        # NDS's ARM9; on a Game Boy it is not memory at all — the write appeared
        # to land and the savestate did not carry it, which reads as a broken
        # savestate rather than a bad address.
        addr = 0xC000 if system == "GB" else 0x02000000
        map_ = 9 if system == "NDS" else None
        state = Path(f"/tmp/skyemu-selftest-{args.port}.state")
        emu.write_byte(addr, 0xA5, map_)
        emu.save_state(state)
        emu.write_byte(addr, 0x7E, map_)
        clobbered = emu.read_byte(addr, map_)
        emu.load_state(state)
        restored = emu.read_byte(addr, map_)
        check("write_byte lands in the running machine", clobbered == 0x7E, hex(clobbered))
        check("save/load restores what was there at save time", restored == 0xA5, hex(restored))
        state.unlink(missing_ok=True)

    print()
    if failures:
        print(f"{len(failures)} failed: " + ", ".join(failures))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
