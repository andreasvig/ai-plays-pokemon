# Species sprites: two keyspaces, not one — 2026-09-21

Andreas, on a Platinum battle card: *"also here is paltnums battle. 1 it gets
thwe wrong pokemion /sprite."* The card should show Chimchar.

## What was wrong

`BattleCard.svelte` built every species sprite URL as
`mapatlas.js:163  pokemonSpriteUrl = (id) => \`${BASE}pokemon/${id}.png\``,
against one file set, with a docstring asserting the premise:

> NOT per game, unlike the trainer sprites above it: a species id is the
> National Dex number on every cartridge we run, so one set serves all seven.

That premise is false, and it is false in BOTH directions: the file set is not
National Dex, and four of the seven cartridges are.

## The keyspace of the files, established from the files

| evidence | reading |
|---|---|
| 411 files, `1.png`..`411.png`, contiguous, no gaps | National Dex has no boundary at 411 (gen 3 ends at 386, gen 4 at 493, gen 5 at 649). 411 is exactly the last gen-3 INTERNAL index. |
| `252.png`..`276.png` are twenty-five byte-identical files (md5 `15699ee9…`), and no other file shares that hash | The gen-3 internal unused band. Under National Dex those twenty-five are Treecko..Shroomish, twenty-five distinct mons. |
| `286.png` renders Poochyena; `390.png` renders Anorith; `403.png` renders Registeel | Gen-3 internal. National Dex 286/390/403 are Breloom/Chimchar/Shinx. |
| `scripts/extract_trainers.py:76` says so, and `mon_pic_paths()` reads pokefirered's `src/data/pokemon_graphics/front_pic_table.h` keyed by `include/constants/species.h` | The generator's own source is the gen-3 internal table. |

Note for the record: the handover said gen-3 internal 390 is Registeel. It is
**Anorith**; 403 is Registeel. Both are wrong pictures, but the screenshot
Andreas sent showing a grey mechanical mon is the *Shinx* card (species 403) from
run `2026-09-20_09-53-29`, not the Chimchar card (species 390) from run
`2026-09-20_21-42-46`. Two cards, one bug.

## What each game actually emits

Every row is the number a recorded run read out of memory, against the name the
game's own battle HUD prints in that turn's screenshot. **Discriminating** means
the number means a different mon under the other numbering, so the row is
evidence; the rest are below 252, where the two numberings agree, and are
controls.

| game | field | keyspace | recorded ids | ground truth | discriminating? |
|---|---|---|---|---|---|
| crystal-us | `wEnemyMon.Species` u8 @0xd206 | National Dex (gen 2 numbers that way) | 16, 19, 161 | run `2026-09-20_19-14-38` t46: 161 → "SENTRET" | no |
| firered-us | `gBattleMons[1].species` | gen-3 internal | 1, 4, 16, 19 | run `2026-09-20_17-42-57` t22: 4 → "CHARMANDER" | no |
| emerald-us | `gBattleMons[1].species` | gen-3 internal | 277, 286, 288, 290 | run `2026-09-20_17-59-44` t91: 286 → "POOCHYENA" (dex 286 = Breloom) | **yes** |
| platinum-us | `battleMons[1].species` | National Dex | 390, 396, 399, 401, 403 | run `2026-09-20_21-42-46` t83: 390 → "CHIMCHAR Lv5" (gen-3 390 = Anorith) | **yes** |
| soulsilver-us | same block, Platinum engine | National Dex | 16, 161 | run `2026-09-21_09-03-49` t154: 161 → "SENTRET" | no — see below |
| black-us | `btl_pokeparam.c` +0x14 | National Dex | 495, 501, 504 | run `2026-09-20_23-09-22` t11: 495 → "Snivy Lv.5"; t97: 504 → "Patrat Lv.2" | **yes** (past 411 entirely) |
| black2-us | `btl_pokeparam.c` +0x14 | National Dex | 501 | run `2026-09-20_00-51-00` t168: 501 → "Oshawott Lv.5" | **yes** |

**SoulSilver has no discriminating observation and cannot get one from this
corpus.** Every species reachable in early Johto (Pidgey, Rattata, Sentret,
Hoothoot, Zubat, Geodude, Mareep…) is below 252, where gen-3 internal and
National Dex are the same number. What settles it instead is the engine and the
decomp: `contracts.py` derives the SoulSilver block from Platinum's ("the same
five s32 in the same order at a different base"), and pokeplatinum's
`include/constants/species.h` defines `NATIONAL_DEX_COUNT` as `MAX_SPECIES - 2`
— the species enum *is* the National Dex. Stated as inference, not measurement.

Black 2 also has only one reachable battle at all (the Hugh fight; the game
hangs in the Aspertia Pokemon Center, `local/gen5-battle/NOTES.md`), so its row
rests on one screenshot plus the contract's own control: replaying from the
starter screen and picking Snivy swaps the pair from (ours 498, foe 501) to
(ours 495, foe 498) — Tepig/Oshawott/Snivy by National Dex.

## The fix

**A second sprite set keyed by National Dex**, not a dex → gen-3-internal
mapping. The mapping was the cheaper option and it fixes nothing that matters:
every species those four cartridges have been recorded fighting — 390, 396, 399,
401, 403, 495, 501, 504 — is a gen-4 or gen-5 native with no gen-3 sprite to map
onto. Nine of nine observed DS battles would have gone from a wrong picture to a
missing one.

- `scripts/extract_dex_sprites.py` — new. Writes
  `public/pokemon/dex/1.png`..`649.png` plus `index.json` (dex → English name).
  - **1..493** from Platinum's own `/poketool/pokegra/pl_pokegra.narc`, six
    entries per species in dex order, character data XOR'd with a 16-bit LCG
    seeded on its own first halfword (forwards on Pt/HGSS, backwards on DP);
    each front is a 160×80 4bpp *linear* image holding two animation frames and
    the left one is the pose the battle opens on. The ROM's sha1 is checked
    against `configs/roms.yaml`, so the extraction reads the same bytes the runs
    were played on.
  - **494..649** from `PokeAPI/sprites` at a pinned commit. Black stores these
    as animation PARTS — `/a/0/0/4` entry 20n+9 is a 96×96 tile sheet whose NCER
    names twenty OAM cells (`head`, `body`, `hand_R`, `tail_A`…) assembled by a
    multi-cell animation — so there is no flat frame in the cartridge to
    extract. This is the one external source in the change: 156 files, the same
    ROM art composed by somebody else. Worth flagging to Andreas as a choice,
    not a fact.
  - Names from `PokeAPI/pokeapi`'s own CSV at a pinned commit, and the script
    **refuses to write** unless all 386 gen-3 species agree with the names pret
    already gives this repo — the join is pret's own `species.h` (internal
    index) × `pokedex.h` (National Dex position), so it is 386 independent
    chances for an external table to be wrong. It bit on the first run, on my
    own enum reader rather than on the table.
- `src/dashboard/web/src/lib/species.js` — new, and the single place the
  keyspace question is answered. `SPECIES_KEYSPACE` maps each `roms.yaml` game
  key to `gen3-internal` or `national-dex`; `pokemonSpriteUrl(game, id)` picks
  the set from the game; `partySpriteUrl(id)` is separate because a roster id
  comes from pret and is gen-3 internal by construction, not by coincidence.
- `src/dashboard/web/src/components/BattleCard.svelte:23,79,93` — imports from
  `species.js` and passes `game` through.

Crystal is filed under `national-dex` even though its whole range (1..251) is
where the two numberings agree, so it renders the same species either way.
Filing it by what is true rather than by what happens not to matter is what
keeps the table right if a Johto run ever meets its 252nd species.

### Unknown species

`public/pokemon/unknown.png` — a copy of gen-3 internal 252, which is the "?"
pic FireRed itself draws for a species that is not one. It is what a card shows
for an id outside its keyspace, for a run that cannot name its cartridge (same
refusal as `route.py:_game_and_contract` and `loadAtlas`), and as the `onerror`
fallback. **The old card hid the image instead** (`display:none`), which is why
every Black card drew a battle with nobody in it: 495 is 84 past the end of the
only set there was.

## Names, in the same change

Crystal's wild battles rendered `#19 · Lv 2` beside a picture of a Rattata. The
card read only `trainers/<game>/index.json`, which exists for the two gen-3
cartridges and nothing else. `pokemon/dex/index.json` now carries the National
Dex names and `speciesName(game, id, …)` reads whichever table the game's
numbering belongs to — never the other one, which is the same rule as the
sprite. `#<id>` stays the fallback for an id with no name.

**One name table is correct for all five dex-numbered games**, and that rests on
one assumption worth stating plainly: National Dex *names* do not change between
generations. Rattata is Rattata on Crystal, on SoulSilver and on Black 2. The
per-game gen-3 tables stay per-game because their *numbers* differ, not their
words.

## Neighbours checked

- **Trainer portrait** (`trainerSpriteUrl(game, t.pic)`) is not keyed the same
  way and is not wrong: `pic` is a ROM symbol name out of the per-game roster,
  and the directory is already per game. The four DS games have no roster at
  all, so they draw no portrait rather than the wrong one.
- **Species names elsewhere**: the only other render of a species id anywhere in
  the app was the party list, and that shows `m.species` — a *name string* from
  the roster, not an id. Server side, `route.py:495` and `trace.py:156` carry the
  raw number and nothing formats it.
- **OCR / scoring**: nothing in `src/referee/referee.py` or `progress.py` reads
  a species at all, so the lookup is not shared with the scored path.

## Still open

- **`mapatlas.js:163` still exports the game-blind `pokemonSpriteUrl`**, with the
  false docstring quoted at the top of this file. Nothing imports it any more
  (`tests/test_dex_sprites.py::test_no_component_builds_a_sprite_url_without_a_game`
  pins that), but it should be deleted. Left alone only because another agent
  owns that file this week.
- **`trainer_id: 851` renders as "not identified" on Platinum** — a real gap,
  reported separately. pokeplatinum has `res/trainers/data/*.json` with name,
  class and full party (exactly what the card draws for Emerald), and
  `res/text/trainer_class_names.json`; what is NOT available is the id → file
  join. The ids come from `generated/trainers.h`, produced by `trainerproc` at
  build time, and the order is not alphabetical (sorted filenames put
  `cyclist_kayla_rematch_2` at 851, which is not a level-5 starter). Working out
  that ordering is the job. SoulSilver would follow via pokeheartgold; Black and
  Black 2 have no decomp and would need the ROM's own text archives.
- Gen-4 art is used for dex 1..493 including on the two gen-5 cartridges, so a
  Pidgey met in Black shows its Platinum sprite. One generation off, never the
  wrong species.

## Verification

Screenshots compared, one battle per cartridge, in
`src/dashboard/web/battlecards.html` (a dev-only harness, `npm run dev` →
`/battlecards.html`, not part of `npm run build`): each card carries the real
`foe` block from a recorded run beside the run's own emulator frame for that
turn. Both failure modes are in the harness too — a species past the end of its
keyspace, and a run with no game — and both draw the "?" pic.

`tests/js/species.test.mjs` (12 cases, 10 of which fail against a mutant with
the pre-fix behaviour; the other two pin the `#390` fallback and the join, and
are regression guards) and `tests/test_dex_sprites.py` (15 cases, including the
nine ground-truth rows above as a parametrised table).
