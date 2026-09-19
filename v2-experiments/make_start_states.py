#!/usr/bin/env python3
"""Replay each game's opening on SkyEmu, from cold boot to its bedroom.

P-B of `artifacts/skyemu-backend/cross-game-plan.md`, for every game at once.

Every Pokemon game begins the same way: an intro you cannot skip, a name you
cannot decline to give, and then the player standing in a room with the whole
game still ahead of them. That room is the benchmark's start state — FireRed's
bedroom in Pallet Town is what v1 has always used — and this produces the
equivalent for each cartridge.

Why replayed rather than ported
-------------------------------
The states in `configs/saves/` are **mGBA** savestates and SkyEmu answers
`/load -> failed` for every one of them. There is no converter. So the opening
is played again, headless, from a fixed input sequence measured in FRAMES rather
than wall-clock — SkyEmu starts paused and advances only via `/step`, so a slow
machine and a fast one replay the identical game.

The ROM is copied to a scratch directory first. SkyEmu writes a `.sav` next to
whatever it loads and has no `savegamePath` switch, so running a repo ROM in
place would both write into the checkout and, worse, put CONTINUE on the title
screen and desynchronise every press after it.

The verification
----------------
There is no shared oracle across these games: the referee's addresses are known
for FireRed and Emerald and for nothing else, so "read the map id and check it"
does not generalise. What does generalise is **control**:

    from the saved state, each of the four d-pad directions changes the screen.

A cutscene answers none of them. An open dialogue box answers none of them. A
menu answers some. Only a player standing in a room with control answers all
four — and that is exactly the property the start state has to have. It is a
weak check on its own, which is why every game also writes a `preview.png` that
a human looks at once.

The sequence language
---------------------
Comma-separated tokens, the same ones the exploration tools use, so a sequence
found by hand pastes in here unchanged:

    w<N>        wait N frames (60 = one second)
    a<N>        press A N times, fast (hold 2, gap 1, then settle 90)
    A<N>        press A N times, slow (hold 12, gap 24, settle 120)
    s<N> b<N>   START, B
    u/d/l/r<N>  d-pad (hold 12, gap 24)
    t<X>_<Y>    tap the touch screen at X%, Y% OF THE TOUCH SCREEN — the lower
                half of the 256x384 NDS capture, so t50_51 is dead centre
    S           screenshot (ignored here; the tools use it)

**The exact hold and gap of every token are part of the sequence, not
decoration.** A tap here is hold 20 / gap 24 / settle 90 because that is what
the exploration tool uses, and SoulSilver showed the difference is not cosmetic:
running its sequence with a longer tap changed the frame the game's RNG was
sampled on, and it rolled a different player name. For the same reason a
screenshot is NOT free — inserting one mid-sequence perturbs timing — which is
why the `S` token is ignored in this file and the preview is taken only at the
end.

Emerald is not here, deliberately
---------------------------------
`v2-experiments/make_emerald_state.py` owns it, because its opening produces TWO
states rather than one — the moving van, which is the casual start
`configs/roms.yaml:42-46` describes, and the house in Littleroot, which is the
one that passes the control check below and the one the address finder uses. It
also presses its buttons in a different dialect (SkyEmu's own `press()` defaults
throughout, with explicit waits) and transcribing that into the token language
here produced a state saved mid-dialogue on the first attempt. A sequence is
easier to keep working than to keep translated, so it stays where it works.

Three things every one of these sequences needed
------------------------------------------------
* **START on an empty name field is the "accept the default" verb.** True on
  every game tried: FireRed KAY, Emerald TERRY, Platinum TODD. Crystal is the
  exception that proves it has a different shape — it offers a preset MENU
  rather than a keyboard, so the sequence picks the first preset instead.
* **A professor's "would you like to know more?" menu will trap an A-mash
  forever**, because the default option shows a page and returns to the menu.
  Answered by moving to the last option: `d2,w60,a1`. Two downs on a two-item
  YES/NO menu wrap back to where they started, so that macro is a no-op on the
  prompts it is not meant for, which makes it safe to use when unsure.
* **A fast A press (2 frames) is enough for GB and GBA and was NOT enough for
  some DS menus.** Where a DS menu swallowed presses, the slow form fixed it.

Usage
-----
    PYTHONPATH=. ./venv/bin/python v2-experiments/make_start_states.py --game crystal
    PYTHONPATH=. ./venv/bin/python v2-experiments/make_start_states.py --all
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "v2-experiments" / "harness"))

from skyemu import SkyEmu  # noqa: E402

OUT_ROOT = REPO / "v2-experiments" / "states"
BUTTON = {"a": "a", "b": "b", "s": "start",
          "u": "up", "d": "down", "l": "left", "r": "right"}


GAMES: dict[str, dict] = {
    # --- GBC ------------------------------------------------------------------
    "black2": {
        "rom": "v2-experiments/roms/Pokemon - Black Version 2 (USA, Europe) (NDSi Enhanced).nds",
        "console": "NDS",
        "where": "the player's room in Aspertia City",
        # Black 2 does not OPEN in a bedroom: after the season card it plays a
        # street scene in Aspertia where Mom takes Bianca's call, and only then
        # places the player, in control, indoors. So the room is reached without
        # walking through any door.
        #
        # No taps. Both keyboards are button-driven: START moves the cursor to OK
        # and A confirms. No default name either — an empty field is refused with
        # "Please enter the name." — so ACE is typed with A, right-2 to C,
        # right-2 to E. The RIVAL name is pre-filled as Hugh and accepted as-is.
        "spec": ("w1800,w1800,w1800,s1,w300,a1,w300,a32,w200,a1,w60,r2,w60,a1,"
                 "w60,r2,w60,a1,w60,s1,w120,a1,w240,a7,w200,s1,w120,a1,w240,"
                 "a18,w1200,a4,w900,a60,w180"),
        "player_name": "ACE",
    },
    "crystal": {
        "rom": "v2-experiments/roms/Pokemon - Crystal Version (USA).gbc",
        "console": "GB",
        "where": "the bedroom upstairs in New Bark Town",
        # Crystal boots into the GBC clock-setting prompt before Oak speaks —
        # "DAY 10 o'clock?" with YES preselected — which the A-mash answers.
        # Then Oak's speech to "YOUR NAME?", which in Gen 2 is a MENU of preset
        # names (NEW NAME / CHRIS / MAT / ALLAN / JON) and not a keyboard, so
        # START does nothing here. One `d` moves to CHRIS and A takes it.
        "spec": "w1200,s1,w300,a48,d1,w60,a1,w150,a20,w900",
        "player_name": "CHRIS",
    },
    # --- NDS ------------------------------------------------------------------
    "soulsilver": {
        "rom": "v2-experiments/roms/Pokemon - SoulSilver Version (Europe).nds",
        "console": "NDS",
        "where": "the bedroom upstairs in New Bark Town",
        # Two taps, both verified against off-target controls. t50_79 is NO INFO
        # NEEDED on Oak's three-button menu — which here is TOUCH-ONLY, with no
        # cursor to move, so Platinum's d2,w60,a1 does not work at all. t25_48 is
        # the BOY portrait on the gender screen.
        #
        # The name is TYPED, not defaulted, and that is the interesting part.
        # "START on an empty field accepts the game's default" holds for FireRed,
        # Emerald, Crystal and Platinum and is FALSE here: HGSS rolls a RANDOM
        # suggestion, and four runs of near-identical sequences produced Ash,
        # Ash, Terell and Jude. So the keyboard cursor's own starting letter is
        # pressed twice instead, giving AA every time.
        "spec": ("w1800,w1800,s1,w600,a1,w400,t50_79,a42,w200,t25_48,w200,a2,"
                 "w600,a2,w200,s1,w200,a1,w300,a2,w200,a12,w400"),
        "player_name": "AA",
    },
    "black": {
        "rom": "v2-experiments/roms/Pokemon - Black Version (USA, Europe) (NDSi Enhanced).nds",
        "console": "NDS",
        "where": "the bedroom, between Cheren and Bianca",
        # No taps at all: Gen 5's keyboard is fully button-navigable — A types
        # the highlighted letter, START moves the cursor to OK, A confirms.
        #
        # And no default name, which is the other end of the spectrum from
        # Emerald. START on an EMPTY field does not fill anything in; it only
        # jumps to OK, and OK on an empty field is REFUSED with "Please enter
        # the name." So one letter is typed — whichever the cursor starts on,
        # which is A — and the game's first bedroom line reads "Cheren: A!".
        # Rivals are never prompted for: Cheren and Bianca are fixed.
        "spec": ("w1800,w1800,w1800,s1,w600,s1,w600,a1,w300,a30,w200,a2,w200,"
                 "a1,w200,s1,w200,a1,w300,a30,w1500,a32,w300"),
        "player_name": "A",
    },
    "platinum": {
        "rom": "v2-experiments/roms/Pokemon - Platinum Version (USA).nds",
        "console": "NDS",
        "where": "the bedroom upstairs in Twinleaf Town, the TV switched off",
        # Two touch gates. t50_51 is the button in the MIDDLE of Rowan's Poke
        # Ball — verified against two off-target taps that did nothing, so the
        # coordinate is doing work rather than any touch being accepted. The
        # d2,w60,a1 answers "would you like to know more?" with NO INFO NEEDED;
        # without it the A-mash loops on CONTROL INFO forever.
        "spec": ("w1800,w1800,s1,w600,a20,d2,w60,a1,w200,a1,w200,a1,w300,"
                 "t50_51,w300,a27,w200,s1,w240,a3,w200,a10,w200,s1,w240,a3,"
                 "w300,a21,w1200,w900,a6,w400,b2,w400"),
        "player_name": "TODD",
    },
}


def cold_rom(src: Path, tmp: Path) -> Path:
    dst = tmp / ("rom" + src.suffix)
    shutil.copy2(src, dst)
    return dst


def play(emu: SkyEmu, spec: str, log=print) -> int:
    frames = 0
    for tok in (t.strip() for t in spec.split(",")):
        if not tok or tok == "S":
            continue
        if tok[0] == "t":
            x, y = (int(v) / 100 for v in tok[1:].split("_"))
            emu.tap(x, y, hold=20, gap=24)
            emu.step(90)
            frames += 134
            continue
        kind, n = tok[0], int(tok[1:] or 1)
        if kind == "w":
            emu.step(n)
            frames += n
        elif kind == "A":
            for _ in range(n):
                emu.press("a", hold=12, gap=24)
                emu.step(120)
            frames += n * 156
        elif kind in BUTTON:
            hold, gap, settle = (2, 1, 90) if kind in "asb" else (12, 24, 0)
            for _ in range(n):
                emu.press(BUTTON[kind], hold=hold, gap=gap)
                emu.step(settle)
            frames += n * (hold + gap + settle)
        else:
            raise ValueError(f"unknown token {tok!r}")
    return frames


# One press is hold 12 + gap 24; the check below makes three of them and then
# lets the screen settle, so an idle run of the same length is exactly this many
# frames with nothing held.
_PROBE_FRAMES = 3 * (12 + 24) + 60


def responds(emu: SkyEmu, state: Path) -> dict[str, bool]:
    """Which directions change the screen from this state — against an IDLE CONTROL.

    The obvious version of this check compares each direction's frame to a still
    frame taken before pressing anything, and it is wrong. Plenty of screens
    animate on their own: a dialogue box blinks its advance arrow, a menu blinks
    a cursor, water tiles cycle. Against a frozen reference every direction
    "responds", so a state with a text box still open passes a check whose whole
    job is to notice that.

    So the reference is an IDLE RUN: the same state advanced by the same number
    of frames with nothing held. Emulation is deterministic, so a blinking arrow
    is at the identical phase in both, and what remains is caused by the input
    and nothing else.

    Found by the agent doing Pokemon Black on 2026-09-19, which reached
    `responds: R L U D` on a state that still had a dialogue box open.
    """
    def frame_after(presses: str) -> bytes:
        emu.load_state(state)
        emu.step(40)
        if presses:
            for _ in range(3):
                emu.press(presses, hold=12, gap=24)
            emu.step(60)
        else:
            emu.step(_PROBE_FRAMES)
        return emu.screen()

    idle = frame_after("")
    return {name: frame_after(button) != idle
            for name, button in (("R", "right"), ("L", "left"),
                                 ("U", "up"), ("D", "down"))}


def make(name: str, port: int, out_root: Path, log=print) -> list[str]:
    game = GAMES[name]
    out = out_root / name
    out.mkdir(parents=True, exist_ok=True)
    problems: list[str] = []
    rom_src = REPO / game["rom"]
    if not rom_src.is_file():
        return [f"{name}: ROM not on this machine ({game['rom']})"]

    log(f"\n=== {name} — {game['where']}")
    t0 = time.time()
    with tempfile.TemporaryDirectory(prefix=f"{name}-coldboot-") as tmp:
        with SkyEmu(cold_rom(rom_src, Path(tmp)), port=port) as emu:
            console = emu.system()
            if console != game["console"]:
                problems.append(f"{name}: expected {game['console']}, measured {console}")
            frames = play(emu, game["spec"])
            state = out / "start.state"
            emu.save_state(state)
            (out / "preview.png").write_bytes(emu.screen())
            moves = responds(emu, state)
            live = " ".join(k for k, v in moves.items() if v) or "NOTHING"
            log(f"  {console}, {frames} frames, {time.time() - t0:.0f}s, responds: {live}")
            if not all(moves.values()):
                problems.append(
                    f"{name}: only {live} respond — a start state has to be a player "
                    f"standing in a room with control, not a cutscene or an open "
                    f"dialogue box")
    log(f"  wrote {state} and preview.png")
    return problems


def selfcheck(port: int) -> int:
    """Show that the control check rejects a state it is supposed to reject.

    A check that cannot fail is not a check. This replays Platinum twice — once
    as authored, once with the two B presses removed so its last dialogue box is
    left OPEN — and reports both the idle-control result and what the naive
    frozen-still comparison would have said.

    Measured 2026-09-19:

        dialogue closed   idle-control RLUD   frozen-still RLUD
        dialogue OPEN     idle-control none   frozen-still RLUD

    The second row is the whole argument for the idle control: the naive check
    accepts a state with a text box on screen, because the box's blinking
    advance arrow differs from a frozen reference no matter what is pressed.
    """
    good = GAMES["platinum"]["spec"]
    bad = good.replace(",b2,w400", ",w400")
    if bad == good:
        print("selfcheck: Platinum's sequence no longer ends in the B that closes its "
              "dialogue, so this control no longer constructs a bad state", file=sys.stderr)
        return 2
    rom_src = REPO / GAMES["platinum"]["rom"]
    if not rom_src.is_file():
        print("selfcheck: Platinum ROM not on this machine", file=sys.stderr)
        return 2
    results = {}
    for label, spec in (("dialogue closed", good), ("dialogue OPEN", bad)):
        with tempfile.TemporaryDirectory(prefix="selfcheck-") as tmp:
            with SkyEmu(cold_rom(rom_src, Path(tmp)), port=port) as emu:
                play(emu, spec)
                state = Path(tmp) / "s.state"
                emu.save_state(state)
                results[label] = responds(emu, state)
        live = "".join(k for k, v in results[label].items() if v) or "none"
        print(f"  {label:<16} responds: {live}")
    ok = (all(results["dialogue closed"].values())
          and not any(results["dialogue OPEN"].values()))
    print("selfcheck PASSES" if ok else "selfcheck FAILS — the check does not bite")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--game", choices=sorted(GAMES), default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--selfcheck", action="store_true",
                    help="prove the control check can fail: replay Platinum with and "
                         "without the B that closes its last dialogue box")
    ap.add_argument("--port", type=int, default=8290)
    ap.add_argument("--out", type=Path, default=OUT_ROOT)
    args = ap.parse_args()
    if not args.game and not args.all and not args.selfcheck:
        ap.error("pass --game NAME, --all, or --selfcheck")

    if args.selfcheck:
        return selfcheck(args.port)

    names = sorted(GAMES) if args.all else [args.game]
    problems: list[str] = []
    for i, name in enumerate(names):
        problems += make(name, args.port + i, args.out)

    print()
    for p in problems:
        print(f"FAIL: {p}")
    if not problems:
        print(f"all {len(names)} start state(s) verified")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
