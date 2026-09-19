# firered-pokebench-v2 — the SkyEmu start state

The v2 (SkyEmu) equivalent of `configs/saves/pokebench-v1`: FireRed, standing on
the rug in the upstairs bedroom of the player's house in Pallet Town, facing the
PC, before a single story step has been taken.

Created **2026-09-19** (plan `artifacts/skyemu-backend/plan.md`, phase P3).

## Why it exists

`configs/saves/pokebench-v1/emulator.state` is an **mGBA** savestate. SkyEmu
refuses it — `/load` answers `failed` — and there is no converter. So the start
is not ported, it is **replayed**: the game is booted cold and played through the
intro with a fixed input sequence.

| | |
|---|---|
| ROM | `Pokemon - FireRed Version (USA, Europe) (Rev 1).gba`, SHA-1 `dd5945db9b930750cb39d00c84da8571feebf417` (the `v1_1` hash in `configs/checkpoints-firered-v1.yaml`) |
| Emulator | SkyEmu, headless HTTP server (`~/Applications/SkyEmu.app`, built from `v2-experiments/skyemu-headless-arm64.patch`) |
| Format | SkyEmu's own PNG-embedded savestate. **Not** loadable by mGBA, and mGBA's is not loadable here. |
| Player | boy (Red) — the gender prompt's cursor starts on BOY |
| Player name | `KAY` — the game's own default, taken by pressing START on an empty naming screen |
| Rival name | `GREEN` — same trick on the rival's naming screen |

Nothing is typed anywhere, so anyone running the script gets the same names.

## How it was made

```
PYTHONPATH=. ./venv/bin/python v2-experiments/make_start_state.py \
    --out configs/saves/skyemu/firered-pokebench-v2 --port 8123
```

`v2-experiments/make_start_state.py` holds the whole input sequence as a
commented list of `(button, n)` / `("wait", frames)` steps — Game Freak logo →
title → the blue instruction pages → Oak's speech → boy → both naming screens →
the bedroom. It copies the ROM to a temp dir first, because a `.sav` beside the
ROM (and `roms/` ships one) puts CONTINUE on the title screen and desynchronises
every press after it.

## Verification (fresh process, 2026-09-19)

Read through the referee's own address map (`src/referee/referee.py`):
`gSaveBlock1Ptr` @ `0x03005008`, then `+0x00` x (s16), `+0x02` y (s16),
`+0x04` map_group (u8), `+0x05` map_num (u8); party count at `0x02024029`.

| | |
|---|---|
| `gSaveBlock1Ptr` → | `0x02025594` |
| map | **4:1** = `PalletTown_PlayersHouse_2F` (`data/firered-walkgraph.json`) — the bedroom |
| x, y | **6, 6** — plausible: the map is 13 wide and the rug sits mid-room |
| party count | 0 |
| screen | `preview.png` (no grid overlay) — player on the green rug, facing the PC |

**It is a real savestate, not a coincidence.** Loaded → `(6, 6)`; three `down`
presses → `(6, 8)` (the third is blocked by the south wall); loaded again →
`(6, 6)`. So the referee's x/y really does track the player, which was the
open ⬜ in the plan's §0.1.

**It is reproducible.** Two independent cold-boot runs produced a byte-identical
`preview.png` and an identical SHA-256 over 0x2000 bytes of EWRAM plus 0x100
bytes of IWRAM around the save blocks. The `.state` files themselves differ
bytewise — SkyEmu wraps the state in a PNG, and the container is not
reproducible — so compare machine state, never the file.

## Cost

**8,598 emulator frames, ~15.5 s wall clock** (≈555 frames/s headless, ~9.5x real
time). Cheap enough that a run could regenerate the start instead of loading it,
though loading is still ~15 s cheaper and is what the executor should do.
