# Start states, one per game

Each game's "bedroom": the player standing in a room, with control, everything
still ahead of them. FireRed's upstairs room in Pallet Town is what v1 has always
started from; these are the equivalent for the rest.

Written by `v2-experiments/make_start_states.py` (and, for Emerald,
`make_emerald_state.py` — see below). **Regenerate rather than edit.** The
scripts are the source of truth and they verify their own output.

| game | console | where | cost |
|---|---|---|---|
| crystal | GBC | the bedroom upstairs in New Bark Town | 9,156 frames, 7 s |
| emerald | GBA | the moving van, and the house in Littleroot | 17,172 + 4,944 frames, 34 s |
| platinum | NDS | the bedroom upstairs in Twinleaf Town | 18,936 frames, 42 s |
| soulsilver | NDS | the bedroom upstairs in New Bark Town | 13,120 frames, 35 s |
| black | NDS | the bedroom, between Cheren and Bianca | 19,100 frames, 147 s |
| black2 | NDS | the player's room in Aspertia City | 21,934 frames, 188 s |

FireRed's lives at `configs/saves/skyemu/firered-pokebench-v2/` because it is the
one a benchmark already runs from; `v2-experiments/make_start_state.py` writes it.

## Why these are replayed and not converted

Everything in `configs/saves/` is an **mGBA** savestate and SkyEmu answers
`/load -> failed` for all of them. There is no converter, so the opening is
played again from a fixed input sequence measured in frames. SkyEmu headless
starts paused and advances only via `/step`, so a slow machine and a fast one
replay the identical game.

## The check, and the control that makes it one

There is no shared oracle here: the referee's addresses are known for FireRed and
Emerald and for nothing else, so "read the map id" does not generalise. What does
is **control** — from the saved state, all four d-pad directions change the
screen. A cutscene answers none. An open dialogue box answers none. A menu
answers some. Only a player standing in a room answers all four.

The first version compared each direction against a still frame taken before
pressing anything, **and that version could not fail**. Screens animate on their
own — a dialogue box blinks its advance arrow, a menu blinks a cursor — so
against a frozen reference every direction "responds", including on exactly the
state the check exists to reject. So the reference is now an **idle run**: the
same state advanced by the same number of frames with nothing held. Emulation is
deterministic, so the arrow is at the same phase in both and what remains is
caused by the input.

`--selfcheck` proves it bites, by replaying Platinum with and without the B press
that closes its last dialogue:

| | idle control | frozen still |
|---|---|---|
| dialogue closed | R L U D | R L U D |
| dialogue **open** | **none** | R L U D |

Every state also writes a `preview.png` that a human looks at once. Emerald's van fails it on purpose —
five tiles wide with boxes on three sides — which is why `make_emerald_state.py`
also produces the Littleroot house state, and why that is the one the address
finder uses.

## What every opening had in common

- **START on an empty name field is *usually* the "accept the default" verb** —
  FireRed KAY, Emerald TERRY, Platinum TODD — but it is a convention, not a rule,
  and two of six games break it. Crystal offers a preset *menu* rather than a
  keyboard, so the sequence picks CHRIS from it. **SoulSilver rolls a RANDOM
  suggestion**: four runs of near-identical sequences produced Ash, Ash, Terell
  and Jude. Its sequence types the name instead, pressing the keyboard cursor's
  own starting letter twice for AA. **Black has no default at all**: START only
  moves the cursor to OK, and OK on an empty field is refused with "Please enter
  the name." **Black 2 refuses it the same way.** So four of seven games break
  it, and the rule is really "type something unless you have checked". Black 2
  also *pre-fills* its rival's name (Hugh), which is accepted unchanged.
- **Timing is part of the sequence, not decoration.** A tap's hold and gap change
  which frame the RNG is sampled on; lengthening SoulSilver's taps changed the
  name it rolled. A screenshot is not free either — one inserted mid-sequence
  perturbs the same thing — so previews are taken only at the end.
- **A touch-only menu cannot be answered with the d-pad.** Platinum's professor
  menu has a cursor; SoulSilver's has none, and no A-mash escapes it. The trick
  that works on one Gen 4 game does not work on the other.
- **A professor's "would you like to know more?" menu traps an A-mash forever**,
  because the default option shows a page and returns to the menu. Answered by
  moving to the last option (`d2,w60,a1`). Two downs on a two-item YES/NO prompt
  wrap back to where they started, so the same macro is a no-op on the prompts it
  is not meant for.
- **Presses right after a warp are swallowed** by the transition — budget two.
- **Taps are not always needed.** Gen 5's keyboards are fully button-navigable (A
  types the highlighted letter, START jumps to OK), so neither Black nor Black 2
  needs a single tap, while SoulSilver's professor menu is touch-only and cannot
  be answered with the d-pad at all. Check before assuming either.
- **"Starts in a bedroom" is not universal.** Black 2 opens on a street in
  Aspertia City, where *Mom* takes a call, and only then puts the player in
  control indoors. The room is still the first controllable frame, so the target
  is the same — but it is reached without walking through a door.
