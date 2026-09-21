# Gen 4 — how a battle ended

> 2026-09-21, branch `polish/outcome`. Platinum: **found and wired**.
> SoulSilver: **not wired, and the blocker is a corpus, not a search.**

A Platinum battle card read "Trainer battle / not identified / OUTCOME UNKNOWN".
It now reads the result, because gen 4 keeps one and the earlier work was
looking for it in the wrong FRAME — after the close, where gen 2 and gen 3 keep
theirs, and where gen 4 has already handed the memory back.

---

## 0. First: does the NDS pointer path actually work now?

It does, and here is a read anyone can check. `POINTER_WINDOWS`
(`src/emulator/backends/skyemu.py:136-144`) now gives NDS `0x02000000-0x02400000`
where every console used to get the GBA's `0x02040000` ceiling.

On a Platinum savepoint, the word at `0x0229f96c` holds `0x0227f408` — pret's
`FieldSystem->location`, and `0x0227f408` is the Location struct the contract
already reads directly. Dereferenced it returns `(342, 0, 113, 854)`, byte for
byte what the flat read returns. **Under the old bound that deref returned
`b""`**, because `0x0227f408 >= 0x02040000`. So the class of candidate "a field
reached through a pointer" really was invisible to the earlier gen-4 search.

**And it turned out not to be needed.** The field below sits at a FIXED address
inside an allocation that lands at the same address in every state dumped, so
Platinum's contract still contains no pointer entry. The widened window is what
made the search honest, not what found the answer.

## 1. Where the outcome is

`BattleSystem.resultMask` in pokeplatinum — the same field pokeheartgold names
`battleOutcomeFlag` and publishes at **struct + 0x2420**.

    Platinum:   0x022c1d90   = BattleSystem allocator header 0x022bf950 + 0x20 + 0x2420
    SoulSilver: 0x022c262c   = ... header 0x022c01ec + 0x20 + 0x2420   (NOT wired, §6)

It was not read out of the decomp and hoped for. It was **measured**: drive a
wild battle to a win sampling the whole 0x2494 allocation after every press,
then diff the one press that ends it. **21 bytes moved, three of them to `1`**
— struct+0x2420, plus struct+0x1584 and +0x158a, which both land inside the
4 KB `clientMessage` buffer (`serverMessage` runs 0x228-0x1228 and
`clientMessage` 0x1228-0x2228, pinned by the decomp's `unk23e8`). So the diff
alone narrows it to three, not to one; what picks between them is that one of
the three is a field both decomps NAME at that exact offset and the other two
are bytes in a message buffer. The corpus in §2 then separates them anyway — a
message byte does not read 1/2/5 across five battles of three outcomes.

**The frame, which is the part the old note got wrong.** The contract's gen-4
offsets are measured from `header + 8`, which an earlier note called the block's
"data". The struct actually begins at `header + 0x20`. The two frames differ by
`0x18`, and subtracting it from every gen-4 offset already in the contract lands
on a published field:

| contract | −0x18 | pokeplatinum |
|---|---|---|
| `_G4_BATTLE_TYPE` 0x44 | 0x2C | `BattleSystem.battleType` |
| `_G4_TRAINERS` 0xB8 | 0xA0 | `BattleSystem.trainerIDs` |
| `_G4_BATTLE_MONS` 0x2D58 | 0x2D40 | `BattleContext.battleMons` |
| `_G4_BATTLE_OUTCOME` 0x2438 | 0x2420 | `BattleSystem.resultMask` |

Two more anchors fix the frame independently of any of those:

* the game's own pointer. `BattleSystem + 0x30` (the decomp's `battleCtx`)
  reads `0x022c29ec` on every Platinum state tried — which is the BattleContext
  block's header + 0x20 exactly.
* `BattleContext.battleEndFlag`, which the decomp puts at struct + 0x311f by
  its own `padding310a`/`padding3154_01` markers. Read at header + 0x20 + 0x311f
  it is 0 for the whole fight and flips to 1 on the last in-battle press of all
  five driven battles. A field that only makes sense at the end, behaving like
  it, at an offset nothing in this work chose.

## 2. The corpus, as a cross-tabulation

Five battles driven on Platinum from real run savepoints, each labelled by the
SCREEN and never by another byte:

| | wild | trainer |
|---|---|---|
| **won** | `p2:20` Starly L3, "CHIMCHAR gained 24 Exp. Points!" → **1** | `p1:60` rival Piplup L5, "Joey got ¥500 for winning!" → **1** |
| **lost** | `p2:20` Starly L3, "Joey is out of usable Pokémon!" / "Joey dropped ¥40 in panic!" → **2** | `p1:60` rival Piplup L5, "Joey blacked out!" → **2** |
| **ran** | `p3:200` Kricketot L3, "Got away safely!" → **5** | **cannot exist** — gen 4 does not offer RUN in a trainer battle |

Prize money appears only in a trainer battle and "Got away safely!" only in a
wild one, so two of the five labels are settled by wording that cannot be
produced by the other kind.

**This is the table the 2026-09-20 candidate never had.** `0x022a64cc` scored
6 for 6 on a corpus whose wins and escapes were all WILD and whose losses were
all TRAINER: outcome was perfectly confounded with kind, and a trainer win read
0 there where a wild win read 1. The two cells that break the confound —
**trainer won** and **wild lost** — are filled here, and both agree with their
same-outcome, other-kind partner. A byte reading the kind cannot do that.

The one cell with a single kind is `ran`, and the reason is a rule of the game
rather than a gap in the driving: **gen 4 refuses to let the player flee a
trainer battle**, so `ran × trainer` is unreachable in principle. The search
tool flags the cell anyway, which is correct — it cannot know that — and this
paragraph is the answer, not a promise to fill it later.

**How each cell was produced, and the instrument control.** Three cells are
natural play: the wild win (43 A-presses), the trainer loss (Chimchar fainted to
the rival), the flee (one RUN tap). Two were forced by writing a battler's
`curHP` to 1 before the first press — `--nerf foe` for the trainer win,
`--nerf me` for the wild loss — because a policy that always wins cannot
produce a loss and vice versa. The write does not touch the byte under test;
the game decides the result itself from party HP
(`BattleControllerPlayer_CheckBattleOver`). The control that the instrument is
not the measurement: **each forced cell has a natural counterpart with the same
value** — forced trainer win 1 = natural wild win 1, forced wild loss 2 =
natural trainer loss 2.

## 3. The search, and what it could not see

Run with the tool that already exists for this, extended with a gen-4 mode:

    ./venv/bin/python v2-experiments/gen5_outcome_search.py --gen4 --max-value 8 \
        --won   <wild_won>:wild   <trainer_won>:trainer \
        --lost  <wild_lost>:wild  <trainer_lost>:trainer \
        --ran   <wild_ran>:wild

The law is unchanged: a byte must be CONSTANT inside every outcome class and
PAIRWISE DIFFERENT between them, and the tool prints the outcome × kind
cross-tabulation and refuses to look convincing when a class holds one kind.
Two things differ on gen 4 and both are stated in the tool's docstring:

* the frame is the **last sample INSIDE the fight**. Gen 4 frees the battle
  heap at the close, so after it the addresses belong to another object.
* the haystack is the two battle ALLOCATIONS (0x2494 + 0x3168 = 0x5610 bytes),
  not 4 MB. Gen 5's allocator ships source-file names in retail and gen 4's does
  not, so a 4 MB hit could not be placed into a named block anyway.

Result: **130 bytes survive the law; 2 survive it with values small enough to be
an enum.**

    0x022c1d90  bsys struct+0x2420   won=1  lost=2  ran=5   <- BattleSystem.resultMask
    0x022c2a9c  bctx struct+0xb0     won=4  lost=5  ran=3   <- BattleContext.scriptFile

The second one is worth naming rather than waving away: it is which battle
SCRIPT is loaded (`scriptFile`, pinned by the decomp's own `padding0060` at
struct+0x60), and `subscript_battle_won` / `subscript_battle_lost` are of
course different scripts. It tracks the outcome because it is the *consequence*
of the outcome, it carries no defined meaning outside the three endings, and
its values are not any documented enum. The other 128 are mostly the battle
script buffer and the IO queue, at struct+0x2740 and up.

**What this search could NOT see**, stated plainly:

* anything outside those two allocations — including the field-side copy
  (`FieldBattleDTO.resultMask`, +0x14 of a block the FIELD heap owns) that
  survives the close and is almost certainly what `0x022a64cc` was. Reaching it
  needs a pointer chase of three levels (FieldSystem → FieldTask → Encounter →
  dto) and the spec grammar expresses one. That is the open follow-up, and §5
  says what it would buy.
* anything that is not byte-granular or not at a fixed offset in those blocks.
* any outcome class that is not won / lost / ran — `caught` (4), `drew` (3) and
  `mon_fled` (6) are in the enum and in the table, and no driven battle produced
  them, so they are decomp-attested and not measured here.

## 4. The enum is gen 4's own, and it overlaps gen 3's without agreeing

`include/constants/battle.h`, identical in both decomps:

    1 WIN   2 LOSE   3 DRAW   4 MON_CAUGHT   5 PLAYER_FLED   6 FOE_FLED

Read through gen 3's `B_OUTCOME` table (which `src/app/route.py` still defaults
to) a Platinum **flee** would publish as "teleported" and a **caught** mon as
"ran" — every value legal, both wrong, nothing to raise on. This is exactly the
Crystal hazard, caught before it cost anything, so the contract carries
`outcome_names=_G4_OUTCOMES` and `tests/test_contracts.py` pins the
disagreement.

The values can also carry `TRY_FLEE` (0x80) and `TRY_FLEE_WAIT` (0x40) while an
escape is being resolved — the decomp's own teardown masks them off with
`& 0x3f`. No driven sample ever showed a masked value at the frame that is
read; if one ever does it falls outside the table and the card says unknown,
which is the safe direction. **Not masked here**, deliberately: masking would
turn an unrecognised state into a confident answer.

## 5. The read rule, and the one measured weakness

**Read the outcome at the LAST sample the flag is still set for.** Gen 3's rule
(first clear-flag sample) reads a different object here: the freed block read
**0x78** at that address on every one of the five battles.

The value is written several frames before the close —
`BattleControllerPlayer_CheckBattleOver` sets it the moment a side's party HP
reaches 0 — so there is something to read while the fight is still up. How much
room there is depends on the ending, and this is the honest limitation:

| ending | the byte was readable for | at production cadence (112 frames/input) |
|---|---|---|
| won (wild) | ≥ 1 press (n=43, closed at n=44) | caught |
| won (trainer) | 4 presses (n=10–13) | caught |
| lost (trainer) | 4 presses (n=69–72) | caught |
| lost (wild) | ≥ 3 presses (n=37–39) | caught |
| **ran (wild)** | **~20 frames** (2 samples of 10) | **MISSED** |

The flee was measured three times. At production cadence the last in-battle
sample read 0 and the next sample was already out of the battle, so the card
says *unknown*. Two fine-sampled repeats caught the 5 — in one it was visible
for two 10-frame samples, in the other for a single 8-frame one. The screen
says "Got away safely!" some 140 frames before the byte is written, so the
write is at the very end of the fade, and the block is gone within about a
tenth of a second of it.

**So: wins and losses are the reliable half, and a flee will usually publish as
unknown rather than as a wrong answer.** That is the failure direction to want,
and it is what the DTO chase in §3 would fix if it is ever worth an evening.

## 5b. The delivery step, which no unit test runs

Everything above was measured through `v2-experiments/harness/skyemu.py`. The
last check runs the PRODUCTION path instead — `SkyEmuClient`, `PLATINUM.spec`,
`trace.decode_samples`, `src.app.route._battles_from_trace` — on the rival
battle driven to a win:

    console probed as: NDS   pointer window: 0x2000000 .. 0x2400000
    spec: ['0x227f408:16', '0x22a647c:4', '0x22c57ec:0x35', '0x22bf99c:0x78',
           '0x22c1d90:1']
    ...
    21 a   in_battle=True  kind=trainer tid=852 foe=393 outcome=1
    27 a   in_battle=True  kind=trainer tid=852 foe=393 outcome=1
    28 tap in_battle=False kind=None    tid=None foe=None outcome=None

    THE CARD: {'kind': 'trainer', 'trainer_id': 852, 'foe': {'species': 393,
               'level': 5}, 'outcome': 'won', 'won': True, ...}

That is the card that read "Trainer battle / OUTCOME UNKNOWN" this morning. The
outcome held for 7 presses here, and decodes to `None` the moment the flag
clears, which is the gate doing its job on freed memory.

The same script proves the pointer path through the production grammar rather
than through mine: `emu._read_spec_entry("*0x229f96c+0:16")` returns the same
16 bytes as the flat read of `0x0227f408`, which under the old GBA-wide bound
was `b""`.

And one more thing fell out of it: `SkyEmuClient._refuse_an_occupied_port`
fired on a port I had reused within the minute. The experiment harness has no
such check, which is exactly the trap that cost three runs here — now a guard
in `gen4_battle_end.py`.

## 6. SoulSilver stays `None`, and the blocker is one trainer battle

The address is named: `_SOULSILVER_BATTLE_SYSTEM + _G4_BATTLE_OUTCOME` =
`0x022c262c`, the same struct field on the same engine, in a block whose size
(0x24a0), whose `battleType` (+0x44) and whose `battleMons` (+0x2d58 of the
context) were all matched to Platinum's by measurement.

It is not wired, for the same reason `battle_kind` is not:
**every SoulSilver battle ever recorded is wild.** The 248-turn continuation
that finished this morning
(`2026-09-21_09-03-49_config-6.0__gemini-3-8-flash-low_continued_from_turn_100`,
$2.10) added 7 more — Pidgey ×5, Sentret ×2, all on map 33 at x 642–657, all
`kind: null` — taking the cartridge's corpus to roughly 15 battles and leaving
it 100 % wild. The run never left New Bark Town, its interiors and Route 29
(maps 64, 63, 60, 61, 66, 33). HGSS's first trainer is past Cherrygrove, and
the westward corridor has now defeated a 248-turn run, an earlier 100-turn run,
and about 400 presses of driving it by hand.

Wiring the outcome on a wild-only corpus would be shipping the structural
analogy that commit `f03dd4b` took out for the kind. **What unblocks both, in
one measurement: one SoulSilver trainer battle.** Then `battle_kind` is a
one-line change with a known address, and the outcome is a second line beside
it.

## 7. Files

* `src/referee/contracts.py` — `_G4_STRUCT_FROM_DATA`, `_G4_BATTLE_OUTCOME`,
  `_G4_OUTCOMES`, `GameMemory.outcome_while_in_battle`, Platinum's 5th spec
  entry and its `battle_outcome` / `outcome_names`.
* `src/referee/trace.py` — the outcome read moves inside the in-battle gate for
  a contract that asks for it.
* `src/app/route.py` — `_OUTCOME_SEEN`: the last non-zero in-battle value wins,
  and `_close_battle` prefers it over the after-sample.
* `v2-experiments/gen4_battle_end.py` — `--nerf me|foe`, `--fine`,
  `--dump-close`, and a guard that refuses a savepoint that did not load inside
  a battle (an occupied port silently answers from somebody else's emulator).
* `v2-experiments/gen5_outcome_search.py` — `--gen4`.
* `tests/test_contracts.py`, `tests/test_route_battle_detail.py`.

## 8. Cost

About a dozen driven battles over roughly 20 SkyEmu launches (ports 8391-8435),
each its own process on its own port, never the control centre's run on 8161 —
which was Black 2 throughout and was never touched. Roughly 25 minutes of
emulator wall time, a good half of it wasted on two self-inflicted detours: a dump branch that fired on every sample instead of at
the close, and three runs whose savepoint answered from a leftover emulator on a
port I had just reused — which is now a hard guard in the driver.
