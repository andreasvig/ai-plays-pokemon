"""The per-game memory contract — where each cartridge keeps the player.

``cross-game-plan.md`` §4 P-D. Until this module existed, everything the harness
knew about a game's memory was FireRed's, spelled as module constants:
``TRACE_SPEC`` (``trace.py``) and the referee's address table. Both are wired in
unconditionally — ``src/cli/runner.py:653,741`` and ``src/cli/launch.py:80,141``
set ``emu.trace_spec = list(TRACE_SPEC)`` with no config path and no injection
point — so a run on any other cartridge dereferenced FireRed's SaveBlock1
pointer inside a game that has no such pointer, and the per-input trace came
back blind. Position is the gate under the walk graph, the route map and the
input census, so "blind" meant those three measured nothing at all on six of the
seven games in ``configs/roms.yaml``.

What a contract is
------------------
A :class:`GameMemory` is the trace spec (raw ranges for the Lua bridge / SkyEmu
to sample after every button) plus the :class:`Field` list saying how to read the
values back out of those samples. The spec grammar is unchanged and lives in
``trace.py``: ``<addr>:<len>``, or ``*<ptr>+<off>:<len>`` to dereference first.

**The pointer form is not ceremony.** Gen 3 DMA-shuffles its save blocks while
the game runs — FireRed's SaveBlock1 was measured at ``0x0202554c`` in the
bedroom, ``0x02025564`` downstairs, ``0x02025570`` back upstairs and
``0x02025574`` outdoors (``p-a-results.md`` §3). A contract that named a raw
address inside that block would read the right number in the room it was
measured in and silently wrong numbers everywhere else, which is the worst
failure shape available: confident, plausible and untraceable.

Where the numbers come from
---------------------------
Every address below was **found by experiment, not looked up** —
``v2-experiments/find_addresses.py``, whose results are in
``artifacts/skyemu-backend/p-a-results.md``. That matters for the games where no
disassembly exists: the method that produced Crystal's four bytes is the method
that will produce Black 2's, and neither depends on someone having published a
symbol table. FireRed's entry is the control: it reproduces the constants
``trace.py`` shipped with, so wiring this module in is a no-op on the game the
existing tests cover.

What a contract deliberately does NOT promise
---------------------------------------------
``battle_flag`` is optional, and most games do not have one yet. The in-battle
bit was found for FireRed only, and inventing one for another cartridge would be
worse than admitting the gap. A contract without it still reports position, and
position is all the WALK GRAPH needs — an edge is proven by the player standing
on one tile and then the next, and a battle does not move the player, so a
battle is simply a stretch of the trace where the tile does not change.

What degrades instead is the input CENSUS: with no battle flag every press is
treated as an overworld press, so a battle's inputs land in ``idle_ab`` and
``blocked_by_actor`` rather than ``battle_inputs``. That is visible and
recorded (:attr:`GameMemory.census_ok`) rather than silent. It cannot corrupt
``overworld_steps``, which counts tile CHANGES and is therefore blind to how the
inputs that changed nothing are labelled.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class Field:
    """One value, as an offset and a struct format into one trace sample.

    ``sample`` indexes :attr:`GameMemory.spec`. Keeping the two apart is what
    lets one range serve several fields: FireRed reads x, y, map group and map
    number out of the same six bytes, which is one round trip on the wire rather
    than four.
    """

    sample: int
    offset: int
    fmt: str
    #: Arithmetic right shift applied after unpacking. Black 2 stores a
    #: coordinate as 16.16 FIXED POINT — the tile is ``value >> 16`` and the low
    #: half is the sub-tile position, which is 0x8000 at rest because the player
    #: stands at the tile centre. That makes the shift exact rather than a
    #: rounding choice. It belongs to the field and not to the caller: a walk
    #: graph that had to know which cartridge measures in sixteenths would be
    #: the contract leaking back out into the code it exists to keep clean.
    shift: int = 0

    @property
    def size(self) -> int:
        return struct.calcsize(self.fmt)

    def read(self, samples: list[bytes]) -> Optional[int]:
        """The value, or ``None`` when the sample is short or missing.

        Short rather than raising: a sample taken while the game is relocating a
        block can come back truncated, and one unreadable field on one input is
        not a reason to lose the turn's whole trace.
        """
        if self.sample >= len(samples):
            return None
        raw = samples[self.sample]
        if len(raw) < self.offset + self.size:
            return None
        value = struct.unpack_from(self.fmt, raw, self.offset)[0]
        # Python's >> on a negative int floors, which is what a tile index
        # wants: -0.5 tiles is tile -1, not tile 0.
        return value >> self.shift if self.shift else value


@dataclass(frozen=True)
class GameMemory:
    """Where one cartridge keeps the player, and how to read it.

    ``game`` joins to ``configs/roms.yaml``'s ``game:`` key — the same join the
    walk graph uses, so a contract and a graph cannot be paired across games
    without one of them noticing.
    """

    game: str
    console: str
    spec: tuple[str, ...]
    x: Field
    y: Field
    #: Gen 2 and Gen 3 identify a map by a (group, number) PAIR. Gen 4 and Gen 5
    #: use a single id (``cross-game-plan.md`` §2.1), so a contract carries
    #: whichever its generation actually has and never both.
    map_group: Optional[Field] = None
    map_num: Optional[Field] = None
    map_id: Optional[Field] = None
    #: The in-battle bit. Absent on every game but FireRed — see the module
    #: docstring for what that costs and what it does not cost.
    battle_flag: Optional[Field] = None
    #: A MASK, not a bit index. Named that way because the two are one typo
    #: apart and the typo is silent: FireRed's flag is bit index 1 of the byte
    #: at gMain+0x439, so ``in_battle_from_byte`` reads ``(b >> 1) & 1`` and the
    #: mask is 0x02. Writing 1 here instead reads the wrong bit and reports
    #: "not in battle" forever, which looks exactly like a game with no battles.
    battle_mask: int = 0x02
    #: When set, the flag means IN BATTLE iff ``(raw & battle_mask) ==
    #: battle_value``. When None, the older rule applies: non-zero after
    #: masking. Platinum needs the equality: its signal is the id of the
    #: running application (16 = the battle overlay) and the overworld reads
    #: FS_OVERLAY_ID_NONE = 0xffffffff, which ANDs NON-ZERO against every mask
    #: — so a mask test reports "in battle" forever, which is the silent
    #: battle_mask failure wearing a third costume. An id is an identity, and
    #: identities are compared, not masked.
    battle_value: Optional[int] = None
    #: The opponent's species id, read only WHILE the flag says a battle is
    #: running. Outside one the field holds the PREVIOUS opponent — measured on
    #: Platinum, where three overworld states each read the species of the
    #: battle that had just ended — so it is gated rather than reported stale.
    foe_species: Optional[Field] = None
    #: The opponent's level, beside the species and gated the same way.
    foe_level: Optional[Field] = None
    #: Whether this fight is a TRAINER's. Gated on the flag for the same reason
    #: the species is: gen 3 keeps the last battle's type word after the fight
    #: ends, so an ungated read labels the next wild encounter a trainer battle.
    #: A MASK against ``battle_kind_trainer``, never a bit index — the same
    #: silent typo `battle_mask` documents above.
    battle_kind: Optional[Field] = None
    battle_kind_trainer: int = 0x08
    #: How the fight ended, as the game's own outcome byte. NOT gated on the
    #: flag, and that is the whole point: gen 3 writes it as the battle closes
    #: and holds it afterwards, so the value that belongs to a segment is the
    #: one read on the first sample where the flag has gone CLEAR. Read during
    #: the fight it is either zero or, during the intro, the PREVIOUS fight's
    #: result — 35 of 146 FireRed mid-battle states (src/referee/battles.py).
    battle_outcome: Optional[Field] = None
    #: True when the outcome must be read INSIDE the fight instead, on the LAST
    #: sample the flag is still set for. Gen 4 needs it and the reason is not a
    #: preference: the battle heap is FREED when the battle overlay unloads, so
    #: by the first clear-flag sample the byte's address holds whatever the
    #: allocator handed those bytes to next (0x78 on Platinum, measured on five
    #: driven battles). Reading it there is not a stale result, it is a
    #: different object. The value is written several frames BEFORE the close —
    #: ``BattleControllerPlayer_CheckBattleOver`` sets it as soon as a side's
    #: party HP reaches 0 — so there is something to read while the fight is
    #: still up, which is what makes the rule workable at all.
    outcome_while_in_battle: bool = False
    #: What ``battle_outcome``'s numbers MEAN, when they are not gen 3's
    #: B_OUTCOME. None keeps ``src/app/route.py``'s B_OUTCOME table, which is
    #: FireRed's and Emerald's. Crystal needs its own and the reason is the
    #: worst kind: the two enums OVERLAP without agreeing. pokecrystal's
    #: wBattleResult is 0 win / 1 lose / 2 draw, so reading it through B_OUTCOME
    #: renames a LOSS "won" and an escape "lost" — every value legal, every
    #: value wrong, and nothing to raise on.
    outcome_names: Optional[dict[int, str]] = None
    #: Who the trainer was, when the kind says one. Set at the start of a
    #: trainer battle and kept until the next, so it is read with the kind and
    #: means nothing without it.
    trainer_id: Optional[Field] = None
    #: The trainer's CLASS, for a generation that has no single trainer id.
    #: Gen 2 names a trainer by a (class, index-in-class) PAIR, so there is no
    #: number that means "Youngster Mikey" the way FireRed's 114 does — and
    #: writing the class into ``trainer_id`` would hand a roster lookup a key
    #: from a different keyspace. Gated with the kind, like the id: measured
    #: stale in the overworld for several presses after a trainer battle ends.
    trainer_class: Optional[Field] = None
    #: The game's own battle counter, XOR-encrypted with a key the referee reads
    #: separately. FireRed-only; the decoder skips it without a key.
    battles_total: Optional[Field] = None
    battles_total_encrypted: bool = False
    #: Map keys the cartridge does not have, so a read of one is a failed read
    #: rather than a place. This is a per-game fact and belongs here rather than
    #: in any consumer: Crystal's map groups are 1-based, so a group of 0 means
    #: the four bytes came back zero — on a GBC, 0xd000-0xdfff is a SWITCHABLE
    #: WRAM bank, and a read taken while the bank register points elsewhere
    #: returns another bank's memory. Left in, it draws a phantom one-tile map
    #: and two phantom warps per occurrence.
    invalid_maps: tuple[tuple[int, ...], ...] = ()
    #: The bank register that says whether the sample above is even readable,
    #: and the bank this contract's addresses live in. GBC only: 0xd000-0xdfff
    #: is a SWITCHABLE WRAM bank, so a read taken while the register points at
    #: another bank returns that bank's bytes — not a failure, a DIFFERENT
    #: memory, silently. Measured on Crystal across 1,318 live samples: 84 read
    #: a bank other than 1, and all 84 of those — every single one, both
    #: directions — also read the phantom (0,0) map that ``invalid_maps``
    #: refuses. The register is what turns that from a symptom into a cause,
    #: and it makes the battle flag as refusable as the map key already was:
    #: 4 of the 65 in-battle samples in the 2026-09-20 Crystal run are this,
    #: the flag reading non-zero while the player walks down a route.
    #: NOT a safe failure in either direction, which the note this replaced
    #: claimed it was.
    bank_reg: Optional[Field] = None
    bank_value: int = 1
    #: True for the ONE contract that the pre-2026-09-20 global decoder was.
    #: `TRACE_SPEC` was FireRed's SaveBlock1 spec applied to every cartridge, so
    #: a run recorded before contracts existed was decoded with FIRERED's layout
    #: whatever it was playing. For FireRed that decoder was RIGHT — byte for
    #: byte, which `tests/test_trace.py` still pins — so its old runs are
    #: readable and must not be thrown away. For the other six it was wrong.
    #: This is the difference between an IDENTITY test and an age test: the
    #: question is never how old the sample is, it is which layout wrote it.
    was_the_global_default: bool = False
    notes: str = ""

    @property
    def census_ok(self) -> bool:
        """True when the input census can distinguish a battle press from an
        overworld one. False is not a bug — it is the honest state of every
        contract found by position search alone."""
        return self.battle_flag is not None

    def wrote(self, sample: dict) -> bool:
        """True when a decoder carrying THIS contract produced ``sample``.

        Two signatures together, neither sufficient alone. ``map_id`` is a KEY
        that only the contract-aware decoder emits at all, so its absence names
        the pre-2026-09-20 decoder outright. And that decoder fills every field
        it can, whereas ours leaves unset exactly the fields this contract does
        not declare — so a gen 4/5 contract expects ``map_group`` to be None and
        a gen 1-3 contract expects ``map_id`` to be None. Both tests read KEYS
        and None-ness rather than plausibility, so a torn read (value None, key
        present) is still recognised as ours.

        Why it is needed: a run's samples are decoded AT RECORD TIME and stored
        decoded, so the spec is baked into events.jsonl. Before each DS game
        got a contract on 2026-09-20 its runs were decoded with FireRed's
        layout, which on a DS yields numbers that look like coordinates —
        a SoulSilver run recorded that afternoon holds
        ``map_group=1, map_num=112, x=12320, y=7259``. Those runs cannot be
        re-read by the new contract; they can only be refused. Age cannot tell
        them apart (a stale process can record an old-shaped sample today) and
        plausibility cannot either. The decoder's own fingerprint can.
        """
        if not isinstance(sample, dict):
            return False
        if "map_id" not in sample:
            # Older than the contract system, so FireRed's layout wrote it —
            # correct for FireRed alone. Rejecting these outright cost 69 real
            # tiles and 13 real runs off the FireRed sheet before this line
            # existed, which is an AGE test wearing an identity test's clothes.
            return self.was_the_global_default
        if self.map_id is not None:
            return sample.get("map_group") is None
        return sample.get("map_id") is None

    def map_key(self, values: dict[str, Any]) -> Optional[tuple]:
        """The map part of a tile key, in whichever shape this game has."""
        if self.map_id is not None:
            key = (values.get("map_id"),) if values.get("map_id") is not None else None
        else:
            g, n = values.get("map_group"), values.get("map_num")
            key = None if g is None or n is None else (g, n)
        return None if key is None or key in self.invalid_maps else key


# --- the registry -------------------------------------------------------------
#
# Keyed by `game:` from configs/roms.yaml. A game absent from here has NO
# contract, and `spec_for` returns None rather than FireRed's — which is the
# whole point of the module. Reading FireRed's SaveBlock1 pointer on a DS
# cartridge does not fail loudly; it dereferences whatever happens to sit at
# 0x03005008 and reports coordinates.

_GMAIN_IN_BATTLE = 0x03003529  # gMain + 0x439, FireRed (src/referee/battles.py)
_IN_BATTLE_BIT = 1             # bit INDEX, as battles.in_battle_from_byte uses it
# Emerald's gMain, MEASURED rather than quoted. struct Main carries a 1 KB OAM
# shadow at +0x38 that is DMA'd to OAM every vblank, so reading OAM
# (0x07000000, 1 KB) and searching IWRAM for the same bytes locates the struct:
# the hit is at 0x030022f8 in every state tried, giving base 0x030022c0. Two
# free corroborations at that base: the seven callback slots all read as ROM
# pointers, and callback1/2 hold one pair in every overworld state and another
# in every battle state. The +0x439 offset and the bit index are pret's struct
# layout, already encoded at src/referee/battles.py:GMAIN_IN_BATTLE_BYTE; what
# is measured here is the byte's BEHAVIOUR — 0x02 in battle, 0x00 out of it,
# across 6 battle and 70 non-battle samples including menus and dialogue.
_EMERALD_GMAIN = 0x030022C0
_EMERALD_IN_BATTLE = _EMERALD_GMAIN + 0x439  # 0x030026f9
_FIRERED_SB1 = 0x03005008
_EMERALD_SB1 = 0x03005D8C
_SB1_GAME_STATS = 0x1200
_GAME_STAT_TOTAL_BATTLES = 7
_STATS_OFF = _SB1_GAME_STATS + 4 * _GAME_STAT_TOTAL_BATTLES

# --- the gen-3 battle block ---------------------------------------------------
#
# Four values turn a battle SEGMENT ("a fight happened here, for this long")
# into a battle CARD ("wild Poochyena, level 3, won"). They live in EWRAM at
# per-cartridge addresses and are read straight, no pointer.
#
# The three that matter are packed into ONE spec entry per game, because
# gBattleOutcome sits a fixed distance past gBattleMons on both cartridges and
# one range is one round trip per button instead of two.
#
# FireRed's are src/referee/battles.py's, which the referee has used since
# 2026-09-14. Emerald's were located 2026-09-20 by
# v2-experiments/emerald_battle_probe.py against five savepoints whose answer
# the SCREENSHOT gives independently — and the screenshot is the whole reason
# they can be trusted, because gen 3 writes "Wild SHROOMISH" for a wild mon and
# "Foe SHROOMISH" for a trainer's. The first pass read that line as wild, called
# a correct trainer bit wrong, and only the wording settled it.
#
#   gBattleMons        3/3 foes match the species and level the screen names
#                      (Poochyena L3, Shroomish L4, Wurmple L2, all against
#                      Mudkip — species 283, the starter, in every state)
#   gBattleTypeFlags   4/4 kinds match, two wild and two trainer
#   gTrainerBattleOpponent_A  a different id per trainer battle and stale
#                      between them, which is the behaviour that separates it
#                      from a constant
#   gBattleOutcome     every read inside B_OUTCOME, and it reads "lost" on
#                      exactly the three states where the player's battler has
#                      0 HP
_BATTLE_MON_SIZE = 0x58
_BATTLE_MON_SPECIES = 0x00      # u16
_BATTLE_MON_LEVEL = 0x2A        # u8
_BATTLER_OPPONENT = 1
_FOE_SPECIES_OFF = _BATTLER_OPPONENT * _BATTLE_MON_SIZE + _BATTLE_MON_SPECIES
_FOE_LEVEL_OFF = _BATTLER_OPPONENT * _BATTLE_MON_SIZE + _BATTLE_MON_LEVEL
#: BATTLE_TYPE_TRAINER, include/constants/battle.h. Shared by both gen-3 games.
_BATTLE_TYPE_TRAINER = 1 << 3

_FIRERED_BATTLE_MONS = 0x02023BE4
_FIRERED_BATTLE_OUTCOME = 0x02023E8A
_FIRERED_BATTLE_TYPE = 0x02022B4C
_FIRERED_TRAINER_OPPONENT = 0x020386AE

_EMERALD_BATTLE_MONS = 0x02024084
_EMERALD_BATTLE_OUTCOME = 0x0202433A
_EMERALD_BATTLE_TYPE = 0x02022FEC
_EMERALD_TRAINER_OPPONENT = 0x02038BCA

#: gBattleMons through gBattleOutcome inclusive, as one range.
_FIRERED_BATTLE_LEN = _FIRERED_BATTLE_OUTCOME - _FIRERED_BATTLE_MONS + 1
_EMERALD_BATTLE_LEN = _EMERALD_BATTLE_OUTCOME - _EMERALD_BATTLE_MONS + 1
_FIRERED_OUTCOME_OFF = _FIRERED_BATTLE_OUTCOME - _FIRERED_BATTLE_MONS
_EMERALD_OUTCOME_OFF = _EMERALD_BATTLE_OUTCOME - _EMERALD_BATTLE_MONS

FIRERED = GameMemory(
    game="firered-us",
    console="GBA",
    # Byte-identical to the TRACE_SPEC this module replaces. The existing
    # referee and trace tests are the oracle for that, per cross-game-plan P-D
    # ("with no behaviour change").
    spec=(f"*{_FIRERED_SB1:#x}+0:6",
          f"{_GMAIN_IN_BATTLE:#x}:1",
          f"*{_FIRERED_SB1:#x}+{_STATS_OFF:#x}:4",
          f"{_FIRERED_BATTLE_MONS:#x}:{_FIRERED_BATTLE_LEN:#x}",
          f"{_FIRERED_BATTLE_TYPE:#x}:4",
          f"{_FIRERED_TRAINER_OPPONENT:#x}:2"),
    x=Field(0, 0, "<h"),
    y=Field(0, 2, "<h"),
    map_group=Field(0, 4, "<B"),
    map_num=Field(0, 5, "<B"),
    battle_flag=Field(1, 0, "<B"),
    battle_mask=1 << _IN_BATTLE_BIT,
    was_the_global_default=True,   # see GameMemory.was_the_global_default
    battles_total=Field(2, 0, "<I"),
    battles_total_encrypted=True,
    # Samples 3-5 are the battle block, added 2026-09-20. They do not change
    # what the first three read, which is what keeps this the control: the
    # trace/referee tests are still byte-for-byte oracles for x, y, map and
    # the counter, and a spec entry the decoder ignores costs one read.
    foe_species=Field(3, _FOE_SPECIES_OFF, "<H"),
    foe_level=Field(3, _FOE_LEVEL_OFF, "<B"),
    battle_outcome=Field(3, _FIRERED_OUTCOME_OFF, "<B"),
    battle_kind=Field(4, 0, "<I"),
    battle_kind_trainer=_BATTLE_TYPE_TRAINER,
    trainer_id=Field(5, 0, "<H"),
    notes="The control. Reproduces the constants trace.py shipped with.",
)

EMERALD = GameMemory(
    game="emerald-us",
    console="GBA",
    # p-a-results.md §8: the block pointer is *0x03005d8c, chosen from 8
    # spellings by proximity to the x anchor, and the block shuffles here too
    # (0x02025a54 downstairs, 0x02025a64 up) — so the pointer form is load
    # bearing on this cartridge and not copied from FireRed out of symmetry.
    spec=(f"*{_EMERALD_SB1:#x}+0:6", f"{_EMERALD_IN_BATTLE:#x}:1",
          f"{_EMERALD_BATTLE_MONS:#x}:{_EMERALD_BATTLE_LEN:#x}",
          f"{_EMERALD_BATTLE_TYPE:#x}:4",
          f"{_EMERALD_TRAINER_OPPONENT:#x}:2"),
    x=Field(0, 0, "<h"),
    y=Field(0, 2, "<h"),
    map_group=Field(0, 4, "<B"),
    map_num=Field(0, 5, "<B"),
    battle_flag=Field(1, 0, "<B"),
    battle_mask=1 << _IN_BATTLE_BIT,
    foe_species=Field(2, _FOE_SPECIES_OFF, "<H"),
    foe_level=Field(2, _FOE_LEVEL_OFF, "<B"),
    battle_outcome=Field(2, _EMERALD_OUTCOME_OFF, "<B"),
    battle_kind=Field(3, 0, "<I"),
    battle_kind_trainer=_BATTLE_TYPE_TRAINER,
    trainer_id=Field(4, 0, "<H"),
    notes=(
        "x/y/map_num found by search (4, 4 and rank-1 candidates). map_group at "
        "+0x0004 is INFERRED, not measured: both maps reachable from the probe "
        "state are the two floors of the same house, so a round trip between "
        "them cannot move the byte and the scan is blind to it by construction. "
        "It is where FireRed keeps it and where the layout says it should be — "
        "a symmetry argument, and the one value here that a second map "
        "transition would upgrade from inferred to found. That upgrade has "
        "since happened: the first real Emerald run crossed (25,40) -> (0,9) "
        "-> (1,0) — the moving van, Littleroot Town, Brendan's house 1F, "
        "matching the pokeemerald constants — so the group byte is MEASURED "
        "and the paragraph above is history, not a caveat. Battle flag located "
        "2026-09-20, see the gMain comment above."
    ),
)

# --- the gen-2 battle block ---------------------------------------------------
#
# Crystal's four battle reads, all located 2026-09-20 by
# v2-experiments/crystal_battle_probe.py and crystal_battle_walk.py and scored
# by crystal_battle_analyse.py. The oracle is never another address: gen 2
# writes the whole answer on the screen, and it writes it as CHARACTER CODES
# into wTilemap (0xc4a0, 20x18) — the font is laid out so the code IS the tile
# index — so the words are readable straight out of memory beside the byte they
# are judging. That decoder was itself checked against rendered PNGs first
# ("Got away safely!", "Can't escape!", "CYNDAQUIL's attack missed!" came back
# character for character) because an instrument is part of its measurement.
#
# The corpus is 1,318 samples: 47 savepoints from the two Crystal runs plus
# eight replays that re-play a run's own recorded button lists forward from a
# savepoint, which is what turns "a battle every 10 turns" into a battle
# sampled after every press.
#
#   0xd22d  wBattleMode, 0 none / 1 wild / 2 trainer. 9 of 9 battle segments
#           agree with the game's own wording — six say "Wild <SPECIES>
#           appeared!" and read 1, three say "<NAME> wants to battle!" and read
#           2 — and four of the six ALSO printed "Got away safely!", which gen 2
#           refuses to print in a trainer battle at all. 0 false positives in
#           576 non-zero samples and 658 readable zero samples in the negative
#           class (walking, dialogue, a Pokegear call, menus, map transitions).
#           The claim this had to beat was "a byte that reads 2 in EVERY
#           battle", which the two wild savepoints could not refute on their own
#           because the species is not evidence of the kind.
#   0xd206  wEnemyMon.Species. 382/382 in-battle samples equal the species named
#           on tilemap row 0, plus 5/5 on savepoint states the search never saw,
#           three of whose species (Zubat, Poliwag, Totodile) appear nowhere in
#           the replay corpus.
#   0xd213  wEnemyMon.Level, +13 into the same struct. 382/382 and 5/5 the same
#           way. NOT 0xd21f, which an earlier pass read as 8 against a level-3
#           screen — the struct is gen 2's 32-byte battle_struct, not the
#           48-byte party one, and the two put the level 18 bytes apart.
#   0xd22f  wOtherTrainerClass. 9 RIVAL1, 22 YOUNGSTER, 36 BUG_CATCHER — three
#           trainers, three pokecrystal constants, each matched to the class
#           name PRINTED in the intro ("??? wants to battle!", "YOUNGSTER
#           MIKEY", "BUG CATCHER DON"). 0 in all 372 wild in-battle samples, and
#           stale in the overworld after a trainer battle, so it is gated on the
#           kind.
#   0xd0ee  wBattleResult, read at the close like gen 3's B_OUTCOME: 0 on three
#           won battles (two trainer, one wild), 1 on the rival battle the run
#           LOST, 2 on all four escapes. It is RESET to 0 when a battle opens —
#           traced 2 -> 0 across the boundary of the next encounter — so a 0 at
#           the close means this fight, not the last one.
#
# What is NOT here, and why. wOtherTrainerID: gen 2 names a trainer by a
# (class, index) PAIR, and no byte in 0xd200-0xd25f is both distinct across the
# three trainers and zero in the wild battles except the class itself. A capture
# and a mutual knock-out are both unmeasured; the escape code is pokecrystal's
# DRAW, so a genuine double KO would read "ran" — named for the four cases the
# corpus actually produced rather than for the one it did not.
_CRYSTAL_ENEMY_MON = 0xD206
_CRYSTAL_MON_LEVEL = 0x0D          # battle_struct, not party_struct
_CRYSTAL_BATTLE_MODE = 0xD22D
_CRYSTAL_TRAINER_CLASS = 0xD22F
_CRYSTAL_BATTLE_RESULT = 0xD0EE
#: wEnemyMon through wOtherTrainerClass inclusive, as one range: species, level,
#: mode and class in one round trip per button instead of four.
_CRYSTAL_BATTLE_LEN = _CRYSTAL_TRAINER_CLASS - _CRYSTAL_ENEMY_MON + 1
_CRYSTAL_MODE_OFF = _CRYSTAL_BATTLE_MODE - _CRYSTAL_ENEMY_MON
_CRYSTAL_CLASS_OFF = _CRYSTAL_TRAINER_CLASS - _CRYSTAL_ENEMY_MON
#: A MODE byte read as a MASK, which is what keeps the kind and the flag one
#: value: 1 & 0x02 == 0 is wild and 2 & 0x02 == 2 is a trainer.
_CRYSTAL_MODE_TRAINER = 0x02
#: pokecrystal wBattleResult. A DIFFERENT enum from gen 3's B_OUTCOME at every
#: value it shares, which is why the contract carries it rather than the
#: consumer. "ran" is 4 of 4 measured escapes; pokecrystal calls the constant
#: DRAW and a double knock-out would land here too, unmeasured.
_CRYSTAL_OUTCOMES = {0: "won", 1: "lost", 2: "ran"}
#: The GBC's WRAM bank select. Not a game address — a hardware register, and the
#: only thing that says whether the 0xd000-0xdfff reads beside it mean anything.
_GBC_SVBK = 0xFF70

CRYSTAL = GameMemory(
    game="crystal-us",
    console="GB",
    # Four adjacent bytes at 0xdcb5, in the documented Gen 2 order, found as two
    # independent searches that happened to land next to each other
    # (p-a-results.md §10) — the axis search returned 0xdcb7/0xdcb8 and the map
    # search returned 0xdcb5/0xdcb6, and neither was told about the other.
    # Gen 2 does not shuffle its blocks, so these are raw addresses and there is
    # no pointer to dereference.
    spec=("0xdcb5:4",
          f"{_CRYSTAL_ENEMY_MON:#x}:{_CRYSTAL_BATTLE_LEN:#x}",
          f"{_CRYSTAL_BATTLE_RESULT:#x}:1",
          f"{_GBC_SVBK:#x}:1"),
    map_group=Field(0, 0, "<B"),
    map_num=Field(0, 1, "<B"),
    y=Field(0, 2, "<B"),
    x=Field(0, 3, "<B"),
    # Gen 2 numbers its map groups from 1, so (0, 0) is not a place. Measured:
    # a 4-run Crystal corpus held 38 such samples, some in stretches of six,
    # every one with all four bytes zero.
    invalid_maps=((0, 0),),
    # wBattleMode. NOT a bit — a MODE byte: 0 overworld, 1 wild, 2 trainer. So
    # the mask is 0x03, and 0x01 would report every TRAINER battle as "not in
    # battle" — the silent battle_mask typo wearing a new costume. The search
    # found it without being told: of the four WRAM bytes zero in all 95
    # non-battle samples and non-zero in all 8 battle ones, this is the only
    # one whose values partition wild from trainer consistently.
    #
    # It sits in 0xd000-0xdfff, the switchable bank, so it carries the same
    # hazard as the position bytes above — and the claim that used to stand here
    # was that it "fails in the SAFE direction". It does not. Measured live at
    # input resolution: 84 of 1,318 samples read a bank other than 1 and their
    # 0xd22d came back 0, 2, 5, 6, 47, 63, 122, 216, 249, 252 and 255 — a zero
    # mid-battle AND a two in the overworld, from the same mechanism. The four
    # in-battle samples in the 2026-09-20 run that sit on the phantom (0,0) map
    # are that false positive, in production. `bank_reg` below refuses them
    # instead: with the register read beside the byte, 0xd22d takes exactly
    # {0, 1, 2} over all 1,234 readable samples and nothing else.
    battle_flag=Field(1, _CRYSTAL_MODE_OFF, "<B"),
    battle_mask=0x03,
    # The SAME byte as the flag, and deliberately: in gen 2 "a battle is on" and
    # "whose battle it is" are one value, so there is no second address that can
    # drift out of step with the first.
    battle_kind=Field(1, _CRYSTAL_MODE_OFF, "<B"),
    battle_kind_trainer=_CRYSTAL_MODE_TRAINER,
    foe_species=Field(1, 0, "<B"),
    foe_level=Field(1, _CRYSTAL_MON_LEVEL, "<B"),
    trainer_class=Field(1, _CRYSTAL_CLASS_OFF, "<B"),
    battle_outcome=Field(2, 0, "<B"),
    outcome_names=_CRYSTAL_OUTCOMES,
    bank_reg=Field(3, 0, "<B"),
    bank_value=1,
    notes=(
        "Gen 2 keeps a coordinate in ONE byte and does not DMA-shuffle, so this "
        "is the only contract here with no pointer. Coordinates are unsigned. "
        "Battle flag located 2026-09-20 and it is a MODE byte, not a bit — one "
        "value carries both 'a battle is running' and 'whose it is', scored "
        "9/9 segments against the game's own 'Wild X appeared!' / 'X wants to "
        "battle!' wording. Species and level are wEnemyMon +0 and +13, 382/382 "
        "and 5/5 against the name and level printed on the battle HUD. The "
        "trainer is a CLASS, not an id: gen 2 has no single trainer number, so "
        "trainer_id stays None and a roster lookup is never handed a key from "
        "the wrong keyspace. The outcome enum is pokecrystal's, not gen 3's. "
        "And the read is only as good as the WRAM bank: bank_reg refuses the "
        "6.4% of samples taken while the GBC had another bank paged in, which "
        "is the same event as the phantom (0,0) map — 84 of 84, both ways."
    ),
)

# The first DS contract, and the first one that is a whole published STRUCT
# rather than a set of addresses found one axis at a time.
#
# pret/pokeplatinum's `struct Location` (include/location.h) is
# {mapHeaderID, warpId, x, z, faceDirection}, five s32 in that order, and all
# five land at 0x0227f408 in NDS main RAM. The bedroom state reads
# (415, -1, 4, 6, 0), which is `sPlayerStartLocation` in src/location.c
# field for field, sentinel included — and pokeplatinum's Rev 0 target sha1
# (ce81046eda7d232513069519cb2085349896dec7) is byte-for-byte the entry in
# configs/roms.yaml, so the decomp describes THIS dump and not a cousin of it.
#
# Corroboration beyond the struct: 13 states resolved to legal enum members
# that each matched the screenshot (TWINLEAF_TOWN, ROUTE_201,
# LAKE_VERITY_LOW_WATER, the two near-identical 2F bedrooms), and a live walk
# moved x by one per tile while mapHeaderID held.
#
# Gen 4 needs NO pointer: the DS heap layout is deterministic across a map
# load, measured, not assumed. Two things about this cartridge that the walk
# graph has to know: Sinnoh's OVERWORLD is one global coordinate space
# (Twinleaf z~882, Route 201 z~854, continuous across the boundary) while
# interiors use small local coordinates, and y therefore does not fit in a
# byte outdoors — which is why every u8 y candidate from the axis search is
# simply wrong outside. faceDirection at +0x10 is the map-load facing, not the
# current one, so it is left out.
_PLATINUM_LOCATION = 0x0227F408

# The battle signal, and it is not a bit or a mode byte — it is the id of the
# running APPLICATION. pret's FieldSystem is located without a pointer chase:
# exactly one word in all 4 MB holds _PLATINUM_LOCATION, at 0x0229f96c, and the
# decomp puts `Location *location` at FieldSystem+0x1c, so FieldSystem is at
# 0x0229f950 and its whole field order then matches the dump. The running
# sub-application's ApplicationManager hangs off processManager->child, and its
# template.overlayID is 16 for the battle overlay and FS_OVERLAY_ID_NONE
# (0xffffffff) in the field.
#
# Verified both directions: 10 labelled battle states from 3 runs plus a live
# 37-press battle, against ~280 overworld samples, 100% separating. The boundary
# was read off SCREENSHOTS, not turn numbers — from a wild Starly fight, 46 A
# presses, and all three signals flip at exactly step 36 ("CHIMCHAR gained 24
# Exp. Points!") to 37 (the route). A map transition out of Rowan's lab
# (422 -> 418) does NOT move it, so a warp is not a false positive.
#
# Untested negative class, stated plainly: no full-screen field menu (bag,
# Pokedex, party) was tested, because the harness cannot open one — SKYEMU_BUTTON
# has no X, and DPPt opens the field menu with X. The overlay id is the variant
# that is robust to that BY CONSTRUCTION: a bag would read its own overlay
# number, not 16. That is why the id is wired rather than the cheaper
# "a sub-application is running" word next to it.
_PLATINUM_OVERLAY = 0x022A647C
_PLATINUM_BATTLE_OVERLAY = 16

# battleMons[1].species — the OPPONENT's slot; battleMons[0] reads 390
# (Chimchar, the player's starter) in every battle, which independently
# confirms sizeof(BattleMon) = 0xc0 from the decomp. Exactly one u16 in 2M
# slots matched the on-screen species across 20 samples and 4 species (Starly
# 396, Kricketot 401, Bidoof 399, Piplup 393), and the decomp's level and
# curHP/maxHP at the same base match all 10 screenshots. STALE outside a
# battle, so the decoder gates it on the flag.
_PLATINUM_FOE_SPECIES = 0x022C57EC

# --- the gen-4 battle block ---------------------------------------------------
#
# Two HEAP ALLOCATIONS, not two addresses, and the difference is the whole
# argument. `local/battleflag/black/NOTES.md` is what a byte chosen for
# correlating with battle state costs on this hardware (18 hours, 14,670
# indistinguishable candidates) and commit a377f67 is what one that passed 40/40
# in both directions cost. So each field below is an offset into a block whose
# allocator HEADER — the magic 0x5544 and the size beside it — is at the same
# address with the same size in every state dumped, and the block's identity is
# corroborated by a field that was already verified independently.
#
#   BattleSystem   hdr 0x022bf950, size 0x2494, data 0x022bf958
#     +0x44  battleType, a MASK. BATTLE_TYPE_TRAINER is bit 0.
#     +0x48  a pointer into the BattleContext block below (its data + 0x18),
#            which is what ties the two allocations together rather than
#            leaving them two unrelated regions that happen to be adjacent.
#     +0xB8  trainer ids, one u16 per battler. [0] is the player's side and
#            reads 0; [1] at +0xBA is the opponent's.
#   BattleContext  hdr 0x022c29cc, size 0x3168, data 0x022c29d4
#     +0x2d58  battleMons[0], the player's battler; [1] is 0xc0 after it, and
#              [1].species is _PLATINUM_FOE_SPECIES, already measured. So the
#              LEVEL below is not a new find — it is the same struct read 0x34
#              further in, at the offset that reads the PLAYER's level out of
#              battleMons[0] at the same time.
#
# The corpus: 70 savepoints from the four Platinum runs, 16 of them inside a
# battle, plus 1,500 samples from 12 REPLAYS — which re-play a run's own
# recorded button_sequence lists forward from a savepoint and sample after every
# press, which is crystal_battle_walk.py's method and what turns "a battle every
# ten turns" into a battle sampled every press. 542 of those samples are
# in-battle, in 16 segments.
#
# Scores, all against the SCREEN and never against another byte:
#
#   foe_level    16/16 in-battle savepoints from 4 runs. Every one matches the
#                "<SPECIES> Lv<N>" plate the battle HUD draws — Piplup L5,
#                Starly L3/L5, Bidoof L2/L3/L5, Kricketot L3 — and the same
#                offset in battleMons[0] matches the player's own plate
#                (Chimchar L5/L6) in the same 16 frames, which is the control
#                that separates "the level field" from "a byte that happens to
#                be 5".
#   battle_kind  25/25 battles: 11 savepoints and 14 replay segments, which is
#                every battle in the corpus whose kind the SCREEN settles (5
#                savepoints show no opponent ball row at the frame they were
#                taken, and one replay segment's intro text is mid-scroll).
#                The oracle is gen 4's own wording and its own HUD: "A wild
#                BIDOOF appeared!" against "You are challenged by Lass
#                Natalie!", the opponent's party balls on the touch screen,
#                "Got away safely!" (which only a wild battle prints) and a
#                prize payout (which only a trainer battle does). Composition:
#                11 TRAINER — Diamond the rival, Youngster Tristan, Lass
#                Natalie — and 14 WILD. That balance is the point: a wild-only
#                corpus cannot refute a wild/trainer discriminator, which is
#                exactly the mistake that produced a false refutation on
#                Emerald. Across all 542 in-battle samples the word takes only
#                {0, 1} and nothing else.
#   trainer_id   852 for the rival, 1 for Youngster Tristan, 3 for Lass
#                Natalie, each matched to the name PRINTED in the intro, and 0
#                in every one of the 323 wild in-battle samples. Three
#                trainers, three values, stale between them — the behaviour
#                that separates a field from a constant.
#
# battle_outcome, FOUND 2026-09-21, and the thing that had to change was the
# FRAME rather than the address space. Gen 4 FREES the battle heap when the
# overlay unloads, so gen 3's rule — read the result on the first sample after
# the flag goes clear — reads a different object: that address holds 0x78 on
# every one of five driven battles. The result is written several frames
# EARLIER, by BattleControllerPlayer_CheckBattleOver the moment a side's party
# HP reaches 0, so it is read on the LAST sample the flag is still set for
# (``outcome_while_in_battle``; src/app/route.py keeps the last non-zero one).
#
# WHERE: BattleSystem struct + 0x2420 = 0x022c1d90. pokeheartgold publishes
# that offset as ``battleOutcomeFlag`` and pokeplatinum calls the same field
# ``resultMask`` — but it was MEASURED first, by diffing the whole 0x2494
# allocation across the single press that ends a battle: 21 bytes moved, three
# of them to 1, and the other two are inside the 4 KB clientMessage buffer. So
# the diff narrows it to three and the decomp names one of the three; the
# corpus below separates them on its own anyway.
#
# THE CORPUS, as the cross-tabulation that the 2026-09-20 candidate never had:
#
#            wild                              trainer
#   won      Starly L3, "gained 24 Exp." = 1   rival Piplup, "got Y500" = 1
#   lost     "out of usable Pokemon!"   = 2    "Joey blacked out!"      = 2
#   ran      "Got away safely!"         = 5    CANNOT EXIST (see below)
#
# Every label is the SCREEN's, and two of them are wordings only one kind can
# print: prize money is a trainer battle, "Got away safely!" is a wild one.
# The two cells that break the confound are trainer-WON and wild-LOST, and each
# agrees with its same-outcome, other-kind partner — which a byte reading the
# KIND cannot do. That is what kills 0x022a64cc, 0x50 past the overlay id: it
# scored 1 on two wins, 5 on two escapes and 0 on two losses, 6 for 6, on a
# corpus whose wins and escapes were all WILD and whose losses were all
# TRAINER. A trainer battle WON reads 0 there where a wild win reads 1, because
# the block at that address is a different object in a trainer battle (header
# size 0xec, not 0x1d8). Commit a377f67 is the same error one cartridge over.
#
# ran x trainer is UNREACHABLE rather than missing: gen 4 does not offer RUN in
# a trainer battle. The search tool flags the single-kind class anyway, which is
# correct — it cannot know that — and this is the answer to the flag.
#
# TWO CELLS WERE FORCED, and the instrument is controlled. A policy that always
# wins cannot produce a loss, so the trainer-won and wild-lost battles were
# driven with a battler's curHP written to 1 (gen4_battle_end.py --nerf). The
# write does not touch the byte under test — the game derives the result from
# party HP — and each forced cell has a NATURAL counterpart reading the same
# value (natural wild win 1, natural trainer loss 2).
#
# THE SEARCH BEHIND IT, and what it could not see. The confound-breaking law
# (constant inside every outcome class, pairwise different between them) run
# over the two battle allocations at the last in-battle sample leaves 130
# bytes, and 2 with values small enough to be an enum: this one, and
# BattleContext+0xb0, which is ``scriptFile`` — which battle SCRIPT is loaded,
# a CONSEQUENCE of the outcome with no meaning outside the three endings. The
# search could not see anything OUTSIDE those two blocks, and one thing out
# there is worth a follow-up: FieldBattleDTO.resultMask (+0x14 of a block the
# FIELD heap owns) survives the close, and is probably what 0x022a64cc was.
# Reaching it needs a three-level chase (FieldSystem -> FieldTask -> Encounter
# -> dto) where the spec grammar expresses one level.
#
# THE ONE MEASURED WEAKNESS. A win or a loss leaves the byte readable for 1-4
# presses, so the last in-battle sample carries it at the referee's own cadence
# (112 frames per A press). A FLEE leaves about 20 frames: at that cadence the
# last in-battle sample read 0 and the next sample was already outside the
# battle, so an escape usually publishes as UNKNOWN. Only 10-frame sampling
# caught the 5. Unknown rather than wrong is the direction to fail in, and the
# DTO chase above is what would fix it.
#
# Full account, including the four anchors that pin the struct frame and the
# 0x18 the old "data" base was off by: artifacts/game-map-render/notes/
# gen4-outcome.md.
_PLATINUM_BATTLE_SYSTEM = 0x022BF958        # data of the 0x2494 block at 0x022bf950
_PLATINUM_BATTLE_CONTEXT = 0x022C29D4       # data of the 0x3168 block at 0x022c29cc
#: Offsets inside those two blocks. Shared with SoulSilver, which is the same
#: engine and — measured, not assumed — the same two block sizes and the same
#: battleMons offset inside them.
_G4_BATTLE_MONS = 0x2D58
_G4_BATTLE_MON_SIZE = 0xC0
_G4_BATTLE_MON_LEVEL = 0x34
_G4_BATTLE_TYPE = 0x44
_G4_TRAINERS = 0xB8
#: BATTLE_TYPE_TRAINER. Bit 0 here, where gen 3 puts it at bit 3 — which is why
#: `battle_kind_trainer` is a per-contract MASK and not a shared constant.
_G4_BATTLE_TYPE_TRAINER = 1 << 0
#: The opponent's battler, from the block base. battleMons[1] is battler 1 on
#: both cartridges, the same indexing the trainer id array uses.
_G4_FOE_OFF = _G4_BATTLE_MONS + _G4_BATTLE_MON_SIZE
#: species .. level as ONE range, so a foe costs one round trip per button.
_G4_FOE_LEN = _G4_BATTLE_MON_LEVEL + 1
#: battleType .. trainers[1], likewise one range.
_G4_BSYS_LEN = _G4_TRAINERS + 4 - _G4_BATTLE_TYPE
_G4_TRAINER_OFF = _G4_TRAINERS + 2 - _G4_BATTLE_TYPE

# --- how a gen-4 battle ENDED (2026-09-21) ------------------------------------
#
# The three constants above are offsets from ``header + 8``, which the earlier
# note called the block's "data". The STRUCT actually begins at ``header +
# 0x20``, and the two frames differ by exactly 0x18 — which is not a correction
# so much as a confirmation, because all three land on a pokeplatinum field
# when you subtract it:
#
#   _G4_BATTLE_TYPE  0x44   = BattleSystem.battleType   +0x2c  + 0x18
#   _G4_TRAINERS     0xB8   = BattleSystem.trainerIDs   +0xa0  + 0x18
#   _G4_BATTLE_MONS  0x2D58 = BattleContext.battleMons  +0x2d40 + 0x18
#
# and the fourth anchor is the game's own pointer: BattleSystem+0x30 (the
# decomp's ``battleCtx``) reads 0x022c29ec on every Platinum state tried, which
# is the BattleContext block's header + 0x20 exactly. Four independent ways of
# saying the same thing, so the frame is pinned rather than assumed.
_G4_STRUCT_FROM_DATA = 0x18
#: ``BattleSystem.resultMask`` in pokeplatinum, ``battleOutcomeFlag`` in
#: pokeheartgold, which publishes it at struct +0x2420 — the offset MEASURED
#: here, by diffing the whole 0x2494 allocation across the one press that ends
#: a battle. One byte changed, and it changed to 1 on a win.
_G4_BATTLE_OUTCOME = 0x2420 + _G4_STRUCT_FROM_DATA
#: Gen 4's enum, and it is NOT gen 3's B_OUTCOME — the two overlap and
#: disagree, the hazard Crystal already cost us. 4 is CAUGHT here and "ran"
#: there; 5 is PLAYER_FLED here and "teleported" there. So a gen-4 flee read
#: through gen 3's table renders as "teleported" and a caught mon as "ran".
#: include/constants/battle.h, identical in both decomps:
#: WIN 1, LOSE 2, DRAW 3, MON_CAUGHT 4, PLAYER_FLED 5, FOE_FLED 6.
_G4_OUTCOMES = {1: "won", 2: "lost", 3: "drew", 4: "caught", 5: "ran",
                6: "mon_fled"}

PLATINUM = GameMemory(
    game="platinum-us",
    console="NDS",
    # Sample 2 widens the foe read from 2 bytes to species..level, and sample 3
    # is the BattleSystem pair. Both are RANGES inside one allocation rather
    # than separate entries, which is the same economy the gen-3 block uses.
    # Sample 4 is one byte and is APPENDED, like samples 2-3 before it: every
    # recorded run's samples are positions in this tuple, so an index may never
    # move. It is its own entry rather than a widening of sample 3 because the
    # outcome sits 0x23c0 further into the same allocation, and reading 9 KB
    # per button to save a round trip is not a trade.
    spec=(f"{_PLATINUM_LOCATION:#x}:16",
          f"{_PLATINUM_OVERLAY:#x}:4",
          f"{_PLATINUM_FOE_SPECIES:#x}:{_G4_FOE_LEN:#x}",
          f"{_PLATINUM_BATTLE_SYSTEM + _G4_BATTLE_TYPE:#x}:{_G4_BSYS_LEN:#x}",
          f"{_PLATINUM_BATTLE_SYSTEM + _G4_BATTLE_OUTCOME:#x}:1"),
    map_id=Field(0, 0, "<i"),
    x=Field(0, 8, "<i"),
    y=Field(0, 12, "<i"),
    battle_flag=Field(1, 0, "<i"),
    battle_mask=0xFFFFFFFF,
    battle_value=_PLATINUM_BATTLE_OVERLAY,
    foe_species=Field(2, 0, "<H"),
    foe_level=Field(2, _G4_BATTLE_MON_LEVEL, "<B"),
    battle_kind=Field(3, 0, "<I"),
    battle_kind_trainer=_G4_BATTLE_TYPE_TRAINER,
    trainer_id=Field(3, _G4_TRAINER_OFF, "<H"),
    battle_outcome=Field(4, 0, "<B"),
    outcome_while_in_battle=True,
    outcome_names=_G4_OUTCOMES,
    notes=(
        "pret/pokeplatinum struct Location at 0x0227f408: mapHeaderID +0, "
        "warpId +4, x +8, z +12, faceDirection +16, all s32. The map key is a "
        "single id, not a (group, number) pair. The battle signal is an "
        "APPLICATION ID compared for equality (16 = the battle overlay), not a "
        "bit or a mode byte, so battle_value carries it. The field Location "
        "survives a battle unchanged, which is what let the walk graph work "
        "before the flag existed. foe_species is the opponent's dex number, "
        "gated on the flag because it holds the previous opponent otherwise."
    ),
)

# HGSS is built on the Platinum engine, and the memory says so: the same five
# s32 in the same order at a different base — map id +0, x +8, y +12 — found by
# a search that was told nothing about Platinum. Two more fields fell out that
# nobody looked for: +0x14/+0x1c/+0x20 is a PREVIOUS map/x/y triple, reading
# "came from map 64 at (3,4)" in the 1F state, which is the exact bedroom tile
# the stairs were entered from.
#
# The map id took four maps to isolate, not two. With the bedroom at (6, 6) and
# New Bark Town at (693, 400), every position-derived field in RAM differs
# between the two, so a two-state search left 86,193 plausible candidates and
# the proximity prior was swamped by neighbouring coordinate fields. Four
# pairwise-different maps plus a REVISIT control — walk back to a map by
# another route, because a real id reads the same and a history-dependent
# leftover does not — cut it to 16, of which exactly one sits in a coordinate
# block. Four held-out states never used in the search then read it correctly.
#
# Caveat, and it is the honest one: all four maps are New Bark Town and its
# interiors, so the id is only ever OBSERVED over 60-66. The field is a clean
# u32 with zero high bytes in every state and the engine is Platinum's, where
# the decomp says s32 — but "is it really 32-bit" is untested above 255, and a
# state in a far-off town is what would settle it.
_SOULSILVER_LOCATION = 0x0227D448

# Same shape as Black, found independently: no boolean flag exists that the
# search can isolate (gen 4 tears the field system down and allocates a battle
# heap, so "set in every battle, clear in every overworld" fits ~145,000 bytes
# and the per-bit law returns 1.96M candidates), but the opponent's SPECIES is
# unambiguous, and 0 is not a dex number. So in-battle is DERIVED again.
#
# What collapsed the search was the species' MULTI-VALUED signature rather than
# any flag criterion: a u16 that equals the on-screen species across several
# battles against DIFFERENT species went from 4 MB to three candidates in one
# pass, and only this one reads 0 in the overworld. Four species over five
# encounters (Rattata 19, Hoothoot 163, Pidgey 16, Sentret 161), each matched
# to the name printed on the top screen. The u32 here packs two u16: the high
# half is the player's own active Pokemon (158, Totodile, in every battle).
#
# THE LIMIT, measured press by press at the end of a Sentret battle: the word
# drops to 0 about three or four inputs BEFORE the battle screen does — 22 A
# presses still read 161, 23 reads 0 with the enemy sprite gone, and the
# overworld does not return until 30. So the final wrap-up presses of every
# battle are mis-filed as overworld presses in the census. Everything from "A
# wild X appeared!" through the last command menu is right, which is the part
# the map cares about.
#
# The strongest evidence was an accident: three walk probes labelled overworld
# read 16, and their screenshots showed all three had walked INTO a Pidgey
# encounter while the fourth direction from the same state stayed out and read
# 0. Same state, one button apart, the word follows the screen.
#
# Open: every encounter is WILD and on Route 29 (map 33), because the first
# HGSS trainer is past Cherrygrove and the run never got there. So the
# wild/trainer split is unmeasured, and whether the address survives a battle
# begun on another map is untested — it held across five encounters from five
# different game states, but all on one map.
_SOULSILVER_FOE_SPECIES = 0x021D05C8

# The gen-4 battle block again, and finding it here is the strongest evidence
# that Platinum's is a STRUCT and not a coincidence. Searching this cartridge's
# RAM for the pair (battleMons[0].species == 158 Totodile, battleMons[1].species
# == the on-screen foe, both levels matching the HUD) returns exactly ONE site
# in 4 MB, 0x022c6018 — and walking back from it to the allocator header lands
# on a block of size 0x3168 whose data begins 0x2d58 before it. That is
# Platinum's BattleContext, byte for byte: same block size, same battleMons
# offset inside it. The BattleSystem is the same story one block over, 0x24a0
# against Platinum's 0x2494, and its first forty words line up field for field
# — including the pointer at +0x48, which holds this cartridge's BattleContext
# data + 0x18 exactly as Platinum's holds its own.
#
# So the foe's LEVEL here is not a second search. It is Platinum's offset read
# on the cartridge whose engine Platinum's offset was measured on, and it scores
# against the screen the same way: 8/8 battles — the 2 in-battle savepoints
# (Rattata L2, Hoothoot L4) and 6 replay segments (Hoothoot L2/L3) — each read
# matching the "<SPECIES> Lv<N>" plate on the frame it was taken from. The
# corpus behind that is 20 savepoints and 8 replays, 1,399 samples in all, 315
# of them in-battle.
#
# 0x021D05C8 above is a DIFFERENT thing and stays where it is: a packed pair of
# u16 (foe species low, the player's own active species high) that the earlier
# work located and that the battle FLAG is derived from. It is left as the flag
# because it is the measured one, and because the species it carries agrees
# with battleMons[1].species in 315 of 315 in-battle samples — two addresses
# found by two independent searches reading the same number is corroboration,
# and collapsing them into one would throw that away.
_SOULSILVER_BATTLE_SYSTEM = 0x022C01F4      # data of the 0x24a0 block at 0x022c01ec
_SOULSILVER_BATTLE_CONTEXT = 0x022C32C0     # data of the 0x3168 block at 0x022c32b8

# TWO fields NOT wired, and for ONE reason, which is a corpus and not an
# address. Both are named here so that the day the corpus exists this is two
# lines and no new search.
#
# battleType sits at _SOULSILVER_BATTLE_SYSTEM + _G4_BATTLE_TYPE = 0x022c0238,
# and it reads 0 in all 315 in-battle samples this cartridge had produced. That
# is not evidence: every one of those 8 battles is WILD. A 248-turn
# continuation on 2026-09-21 added 7 more — Pidgey x5 and Sentret x2, all on
# map 33 at x 642-657, kind null on every one — which takes the cartridge's
# whole corpus to about 15 battles and leaves it 100% wild. HGSS's first
# trainer is past Cherrygrove and no run has left the New Bark/Route 29 pocket
# (maps 33 and 60-66): not in 248 turns, not in the earlier 100, and not in
# about 400 presses of driving the westward corridor by hand, which dead ends
# at x=606 in the tree line. A wild-only corpus cannot refute a wild/trainer
# discriminator (the Emerald false refutation), so wiring this would make every
# SoulSilver battle claim "wild" on the strength of a structural analogy —
# exactly what commit f03dd4b took out.
#
# battleOutcomeFlag sits at _SOULSILVER_BATTLE_SYSTEM + _G4_BATTLE_OUTCOME =
# 0x022c262c, the same struct field at the same offset on the same engine, in a
# block already matched to Platinum's by size (0x24a0), by battleType and by
# battleMons. The hole is the same one: with no trainer battle, the outcome x
# kind table is wild-only in every class, which is precisely the shape that let
# a Platinum candidate score 6 for 6 and be wrong. Note what a wild-only corpus
# CAN still do here and could not do for the kind — three distinct values
# across one kind already refute "this is a kind byte" — and what it cannot:
# rule out a field that means something else in a trainer battle, which is
# exactly how 0x022a64cc failed. So it stays None.
#
# ONE MEASUREMENT unblocks both: a single SoulSilver trainer battle. Not
# another address, not another search.
_SOULSILVER_BATTLE_TYPE = _SOULSILVER_BATTLE_SYSTEM + _G4_BATTLE_TYPE
_SOULSILVER_BATTLE_OUTCOME = _SOULSILVER_BATTLE_SYSTEM + _G4_BATTLE_OUTCOME

SOULSILVER = GameMemory(
    game="soulsilver-us",
    console="NDS",
    spec=(f"{_SOULSILVER_LOCATION:#x}:16", f"{_SOULSILVER_FOE_SPECIES:#x}:2",
          f"{_SOULSILVER_BATTLE_CONTEXT + _G4_FOE_OFF:#x}:{_G4_FOE_LEN:#x}"),
    map_id=Field(0, 0, "<i"),
    x=Field(0, 8, "<i"),
    y=Field(0, 12, "<i"),
    # The same field twice: the flag IS "an enemy Pokemon is on the field".
    battle_flag=Field(1, 0, "<H"),
    battle_mask=0xFFFF,
    foe_species=Field(1, 0, "<H"),
    # From the BattleMon, not from the packed word the flag comes off — that
    # word carries two species and no level.
    foe_level=Field(2, _G4_BATTLE_MON_LEVEL, "<B"),
    notes=(
        "Same struct shape as Platinum at a different base: map id +0, x +8, "
        "y +12, elevation +16, and a previous-map/x/y triple after it. Raw "
        "addresses, no pointer — identical across 11 states and 4 maps. Map id "
        "observed over 33 and 60-66 (Route 29, New Bark Town and its "
        "interiors), so its width above 255 is inferred from the engine, not "
        "measured. No battle flag located: in-battle is DERIVED from the "
        "opponent's species being non-zero, and it goes clear three or four "
        "inputs before the battle screen ends. The Location block keeps "
        "reading the real tile DURING a battle, which is what makes 'which "
        "Pokemon was fought on which tile' available today."
    ),
)

# Gen 5 is a different engine from Gen 4 and it shows, but the important
# property survives: raw addresses live through a map load here too. Walked out
# of the Aspertia front door and back in again in ONE session with no reload —
# (5.5, 10.5) inside, (47.5, 762.5) a step later outside, (5.5, 10.5) on
# re-entry, tracking every step between. So no pointer chase, same as Gen 4.
#
# What IS different: the coordinate is 16.16 fixed point. Standing still always
# leaves 0x8000 in the low half because the player rests at the tile centre, so
# `>> 16` is exact. And the block orders map, x, HEIGHT, y — x and y 8 apart as
# in Gen 4, but with height between them rather than after.
#
# find_map_id.py did NOT find the id here, and the reason is worth keeping: it
# sits 4 bytes BEFORE x, which is exactly what --min-anchor-dist 4 drops. Every
# row the search did promote was the height component of some position vector.
# It took a stricter pass — constant under four walk probes across four states,
# equal at two tiles of one map, PAIRWISE distinct across three maps (the
# script's `differs` only ORs against the first map), u16 < 1024 — and then the
# round trip confirmed it: 428 -> 427 -> 428. It is not BGM or tileset either,
# which the two house interiors rule out by sharing both and reading 428 vs 431.
#
# The trap in this block is 0x0223b4f4, a step counter: +1 per tile, zeroed by
# a map load, and at rest the most map-id-shaped value in the whole struct.
# The only DS game with a REAL flag rather than a derived one: a u32 reading 1
# in battle and 0 out of it, at +0x30 into a 0x34-byte resource-bank descriptor
# whose pointers and sizes are identical in two independent battles. 36
# in-battle dumps read 1, 150 non-battle dumps read 0, and a live verify on
# held-out states agreed 46/46. It flips off on exactly the press where the
# battle screen gives way to the overworld; it flips on one press AFTER the VS
# splash, so that single press is filed as overworld. That is the only slop.
#
# THE NEAR MISS, which is the useful part. 0x0209da74 verified 40/40 in BOTH
# directions and is wrong: it is the overlay-stack depth, and it also reads 1
# during the starter-choice scene's teardown, a frame where the overworld is
# already on screen. Adding that one transient to the negative class killed it
# and the whole cluster around it. A check that passes both ways can still be
# wrong when the negative class is incomplete — and "both directions" was the
# rule that caught the previous three cartridges, so it is not sufficient on
# its own. The related trap: the battle OVERLAY id at 0x0209da80 is 0x4d5 in
# battle but both LEADS it (set while Hugh is still talking) and LAGS it (still
# 0x4d5 six presses into the overworld). That is Platinum's mechanism, and it
# does NOT transfer to gen 5.
#
# Bit or mode byte is UNTESTED: every reachable battle is a trainer battle. It
# reads as a boolean "in use" field rather than an enum, but that is inference.
# If a wild battle ever becomes reachable, re-read this byte first.
_BLACK2_IN_BATTLE = 0x0213B2E0

# Opponent's species. Cross-checked the right way — by fighting a DIFFERENT
# species: replaying from the starter-choice screen and picking Snivy makes Hugh
# use Tepig, and the pair swaps from (ours 498, foe 501) to (ours 495, foe 498).
# Block layout verified live: species +0, max HP +2, current HP +4, level +0xc;
# the player's battler is at 0x0225b1f0 and the opponent's 0x224 after it. Each
# block is preceded 0x20 bytes earlier by the allocator's "pokeparam.c" tag,
# which is how to find the equivalent on another gen 5 cartridge without a
# search. Reused outside a battle — the nickname keyboard leaves 3481 here — so
# the decoder's gating on the flag is load-bearing, not tidiness.
_BLACK2_FOE_SPECIES = 0x0225B414

_BLACK2_LOCATION = 0x0223B444

# --- the gen-5 battle block, first half ---------------------------------------
#
# The two offsets below are SHARED with Black, and the sharing is measured
# rather than assumed. The full argument — gen 5's debug allocator, which tags
# every heap block with the source file that asked for it, and what that buys —
# is in the Black block further down, where the corpus that settles it lives.
# What is true on THIS cartridge:
#
#   btl_pokeparam.c  hdr 0x0225b3dc, size 0x214, data 0x0225b400
#       +0x14 is _BLACK2_FOE_SPECIES above, so the species already shipped is
#       an offset into a named allocation, and the LEVEL is the same block
#       0xc further in — the offset the comment above this already recorded.
#       Black's opponent block is the same size and the same +0x14, and the
#       four battler blocks are 0x224 apart on both cartridges.
#   procsys.c        hdr 0x02257274, size 0x49c, data 0x02257294
#       The BATTLE PROC's work. Located WITHOUT reference to Black: exactly one
#       procsys.c block in 4 MB holds, at +0x0c, a pointer into this battle's
#       BATTLE_SETUP_PARAM (itself a btl_setup.c block, whose +0x00 reads 1 for
#       a trainer battle). Black's is the same block at 0x02269760.
#       +0x58  the opponent trainer's NAME buffer — and this is the corroboration
#              that the offset transfers: on Black the pointer at +0x58 leads to
#              a STRBUF spelling "Bianca" in the Bianca battle, and here to one
#              spelling "Hugh".
#
# THE LIMIT, and it is a corpus and not an address: Black 2 has exactly ONE
# reachable battle, PKMN Trainer Hugh at the Aspertia lookout, because the game
# HANGS in the Pokemon Center doorway the story walks the player into
# immediately afterwards — the original run died there, and driving the same
# savepoint by hand ends at the same tile (map 435, 7,19) unresponsive to every
# direction. So battle_kind here is measured on ONE battle, and what makes it
# shippable rather than a guess is that its value is not a default: the field
# holds a live pointer to a buffer containing the opponent's name, and the NULL
# reading that means "wild" is the one verified 30 times on Black, on the same
# engine at the same offset of the same block. That is the difference from
# SoulSilver's battleType, which was left None because it read 0 — the same
# thing an unwritten field reads — in every sample it had.
#
# foe_level is measured on this cartridge and not inherited: the block reads
# level 5 in the Hugh battle, which is the "Oshawott Lv. 5" the run's own
# turn-166 screen reading records.
_G5_FOE_LEVEL = 0xC                          # from the species, inside btl_pokeparam
_G5_TRAINER_NAME = 0x58                      # into the battle proc's work
#: species .. level as ONE range, so a foe costs one round trip per button.
_G5_FOE_LEN = _G5_FOE_LEVEL + 1
_BLACK2_BATTLE_PROC = 0x02257294             # data of the 0x49c block at 0x02257274

BLACK2 = GameMemory(
    game="black2-us",
    console="NDS",
    spec=(f"{_BLACK2_LOCATION:#x}:16", f"{_BLACK2_IN_BATTLE:#x}:1",
          f"{_BLACK2_FOE_SPECIES:#x}:{_G5_FOE_LEN:#x}",
          f"{_BLACK2_BATTLE_PROC + _G5_TRAINER_NAME:#x}:4"),
    map_id=Field(0, 0, "<I"),
    x=Field(0, 4, "<i", shift=16),
    y=Field(0, 12, "<i", shift=16),
    battle_flag=Field(1, 0, "<B"),
    battle_mask=0x01,
    foe_species=Field(2, 0, "<H"),
    foe_level=Field(2, _G5_FOE_LEVEL, "<B"),
    battle_kind=Field(3, 0, "<I"),
    #: The whole word: the test is "the trainer-name pointer is not NULL".
    battle_kind_trainer=0xFFFFFFFF,
    notes=(
        "Gen 5. map id +0, x +4, height +8, y +12; x and y are 16.16 fixed "
        "point, so the Field carries shift=16 and the tile is value >> 16. Raw "
        "addresses survive a map load, verified by a live round trip through "
        "the front door. Unova's OUTDOOR coordinates are global (y=764 in a "
        "city thirty tiles across) while interiors are local. Map ids seen: "
        "428 player's house, 427 Aspertia City, 431 neighbour's house, 435 the "
        "Pokemon Center. A real battle flag, the only one of the four DS games: "
        "see the comment above for the candidate that passed 40/40 and was "
        "still wrong. Wild battles are unreachable — the game HANGS entering "
        "the Aspertia Pokemon Center under SkyEmu, which froze the original run "
        "too — so bit vs mode byte is untested. The battle fields are offsets "
        "into two NAMED heap allocations (gen 5 tags every block with the "
        "source file that asked for it): the level is the species' own block "
        "read 0xc further in, and the kind is the battle proc's pointer to the "
        "opponent trainer's name buffer — which on this cartridge leads to a "
        "buffer spelling 'Hugh'. Only ONE battle is reachable here, so the "
        "wild reading of that pointer is the one measured on Black, 30 times. "
        "battle_outcome is None: gen 5 acts on the result inside the battle "
        "and the block that carries it is zeroed or reallocated by the frame "
        "the flag clears — measured on Black, where battles can be driven to "
        "each ending; see its block for what was tried."
    ),
)

# The last of the seven, and the one the search could NOT find on its own. Both
# of find_map_id's priors are false in Gen 5: the coordinates live in an
# overworld ACTOR rather than a save block, so proximity ranking just returns
# the actor's own fields; and Black STREAMS map data as the camera moves, so
# "constant while you walk inside a map" holds for almost nothing — 297,710
# bytes take one value on both visits to the living room and another on both
# visits to the town, and none of them survives a three-tile walk. A strict
# pairwise search with four revisit oracles got it down to 1,651 candidates,
# all allocator bookkeeping. A shortlist, not an answer.
#
# What found it was the STRUCTURE. Black 2's block is map, x, height, y at +0,
# +4, +8, +12, and Black's actor has x, height, y at +4, +8, +12 of
# 0x0224f90c — so +0 is where the map id goes on the sibling cartridge. The
# search had already surfaced that word and set it aside as a heap-nesting
# counter, which was a guess; reading it says otherwise. Measured over the six
# fixtures: 391 for both bedroom tiles, 390 for both living-room tiles, 389 for
# both town tiles, constant through a six-press walk on two of those maps while
# x and y track every step.
#
# The registered START state also reads 391, and it was saved BEFORE both rival
# battles while the bedroom fixtures were saved after 220 presses of story. So
# this is not the monotone story counter that a sequential three-state design
# is confounded with — that one is 0x0214678f, which reads 9/23/27/28 across
# the same chain and never returns.
_BLACK_ACTOR = 0x0224F90C

# Black's battle flag was NOT located, and this is not it. Black allocates the
# battle system on the HEAP: out of battle the whole of 0x0226xxxx reads zero,
# so "non-zero in every battle, zero in every overworld state" is true of
# 14,670 bytes, and a bit inside a live byte of 189,133. Picking one would be
# picking a buffer that happens to exist.
#
# What IS located is the opponent's species, and the flag is DERIVED from it:
# 0 is not a valid dex number, so "there is an opponent" is the battle test.
# That is weaker than a flag and the difference is worth keeping in mind — if
# the real flag is ever found, it should replace this rather than sit beside it.
#
# It earns the trust it has: the field holds 495 (Snivy) in every Bianca sample
# and 501 (Oshawott) in every Cheren sample, so it is an IDENTIFIED field and
# not an anonymous heap byte, and it reads 0 across 47 overworld samples whose
# negative class includes an open text box and the starter-selection screen.
# Verified 18/18 on held-out states, and reproduced here across four battle and
# four overworld states.
#
# That paragraph used to end "every battle in the corpus is a TRAINER battle,
# so wild is unproven". It is not true any more: Route 1's grass is reachable
# from the run's own turn-70 savepoint, and the block below is measured against
# 30 wild battles.
_BLACK_FOE_SPECIES = 0x0226D8D4

# --- the gen-5 battle block ---------------------------------------------------
#
# Gen 5 ships its DEBUG ALLOCATOR in the retail cartridge, and that is the whole
# reason this was tractable at all after the blind search gave up. Every heap
# block is preceded by a header — the magic 0x5544, the size, the doubly-linked
# neighbours, and then the NUL-terminated SOURCE FILE NAME of whoever asked for
# the block — so a 4 MB dump is a LABELLED MAP of the battle heap rather than
# 4 MB of bytes (``v2-experiments/gen5_heap_map.py``). Every field here is
# therefore an offset into a NAMED allocation, which is the same discipline the
# gen-4 block above gets from pret/pokeplatinum's struct names.
#
#   btl_pokeparam.c  hdr 0x0226d89c, size 0x214, data 0x0226d8c0
#       +0x14  species — which IS _BLACK_FOE_SPECIES above. So the level below
#              is not a new address: it is the same block read 0xc further in,
#              at the offset Black 2's own notes already record (species +0,
#              max HP +2, current HP +4, level +0xc). Four of these blocks sit
#              0x224 apart and the opponent's is the second, which is the same
#              spacing measured on Black 2.
#       +0x20  level.
#   procsys.c        hdr 0x02269740, size 0x490, data 0x02269760
#       The BATTLE PROC's work struct. The tag is the proc system's because gen
#       5 allocates a proc's work there; the CONTENT is the battle's, and what
#       identifies it is +0x0c — one of only two words in all 4 MB that point
#       into the BATTLE_SETUP_PARAM this battle was started with, whose +0x00
#       reads 1 in every trainer battle and 0 in every wild one.
#       +0x58  the opponent trainer's NAME buffer, which is battle_kind below.
#
# Both blocks are at the SAME address with the SAME size and the SAME tag in
# all 34 battle dumps, taken on four maps — and in NEITHER of them in any of
# the seven overworld dumps, where the search finds no block at those headers
# at all, because gen 5 allocates the battle heap when a battle starts and
# frees it when it ends. That is why gating on the flag is load-bearing here
# and not tidiness.
#
# battle_kind is a POINTER compared against 0, which is unusual enough to say
# why. The battle proc keeps the opponent trainer's name in a STRBUF and
# 0x022697b8 is the pointer to it — identified by its CONTENT, not by
# correlation: it points at a buffer whose characters spell "Bianca" in the
# Bianca battle, and the same offset of the same block on BLACK 2 points at one
# spelling "Hugh". A wild Pokemon has no name to put there and the pointer is
# NULL. So the test is "does this battle have a named opponent", read off the
# battle's own work struct, rather than a byte that happens to differ.
#
# Scores, every label read off the SAMPLE'S OWN FRAME and never off another
# byte. 34 battle states, 4 TRAINER and 30 WILD.
#
#   TRAINER  Bianca's Snivy L5 and Cheren in the bedroom (map 391), N's
#            Purrloin L7 in Accumula (397), a Youngster's Patrat L7 on Route 2
#            (319). Four trainers, three maps.
#   WILD     25 walked into from Route 1's grass (317, Lillipup L2-L4) and 5
#            from Route 2's (319, Lillipup L4 and Patrat L4/L5). Two maps and
#            two species, and the Route 2 ones matter most: that is the SAME
#            map the Youngster stands on, so the kind is not confounded with
#            the place, which it would be if every wild fight were on Route 1.
#
#   foe_species  34/34 against the name on the HUD plate.
#   foe_level    33/33 against the "Lv<N>" on the same plate. The 34th is the
#                Cheren state, whose frame is the prize-money line after the
#                fight, so it shows no foe plate to score against.
#   battle_kind  34/34. The oracle is gen 5's own wording and its consequences:
#                "A wild Lillipup appeared!" and "A wild Patrat appeared!",
#                captured on the hunted encounters' intro frame, before any
#                battle input; "A Trainer catches another Trainer's eye" for the
#                Youngster; and for Cheren the trainer sprite standing on the
#                field under "A got P500 for winning!", a payout only a trainer
#                battle prints.
#
# And at INPUT resolution, which is where a field that leads or lags the battle
# shows up: 9 consecutive presses through the Youngster battle read the pointer
# set on exactly the press the opponent's species appears, and 36 consecutive
# presses of one wild battle read it NULL throughout.
#
# What is NOT in the corpus, stated plainly: a double battle, a battle begun
# indoors other than the bedroom, and any wild encounter outside Routes 1 and 2
# — the run never got further.
#
# The chase at the battle proc's +0x0c is REACHABLE now, and it is worth saying
# what that bought and what it did not. Until 2026-09-20 the backend bounded
# every ``*ptr+off`` dereference to the GBA's EWRAM window, which ends at
# 0x02040000, so no gen-4 or gen-5 heap pointer could be followed at all; the
# window is PER CONSOLE now (``POINTER_WINDOWS``, src/emulator/backends/skyemu.py).
# What it buys HERE is corroboration rather than a new field: ``*0x0226976c+0``
# lands on the BATTLE_SETUP_PARAM's own competitor word — 1 for a trainer
# battle, 0 for a wild one — and it agrees with the RAW battle_kind above on
# 352 of 352 in-battle presses across ten driven battles (162 trainer, 190
# wild). Two addresses reached by two independent routes reading the same fact,
# which is why the contract keeps the raw one: it is the measured one, it costs
# no chase, and collapsing them would throw the agreement away.
#
# WHAT IS NOT HERE: battle_outcome — and the reason is no longer the pointer
# bound. It is that gen 5 CONSUMES the result inside the battle and leaves
# nothing behind to read.
#
#   * The BATTLE_SETUP_PARAM is the only carrier, and the CALLER allocates it:
#     four battles, four addresses (0x0225d610, 0x02259750, 0x0225ddd4,
#     0x0225dc60). Read through the chase press by press, it is BYTE-IDENTICAL
#     from the first in-battle sample to the last — the result is never written
#     into it at a frame any sample can see.
#   * At the close — the first sample after the flag goes clear, which is where
#     ``src/app/route.py`` reads an outcome — the param is already gone. In the
#     trainer drive it reads all zeros and every one of the four words that
#     pointed at it is gone; in the wild drive some of those words still hold
#     the address, but the allocator has already handed the BYTES to a
#     ``msgdata.`` block, so following them is worse than not following them.
#   * The field's own script work (``scrcmd_work.c`` at 0x0225c484, alive on
#     both sides of the boundary) carries no result either.
#   * The reason is on the screen: the whiteout line — "A scurried to a Pokemon
#     Center, protecting the exhausted and fainted Pokemon from further
#     harm..." — plays before the flag goes clear. There is no "after" in which
#     to read an answer the game has already acted on.
#
# One measured limit of the DERIVED flag fell out of driving a loss, and it
# belongs here because nothing else in the tree records it. The flag is "the
# opponent's species is non-zero", and the block it reads is FREED at teardown
# but not zeroed: on the two presses that play the whiteout line it holds 2711,
# which is not a dex number and is not 0 either. So a lost battle's last two
# presses are filed as in-battle with a nonsense species. A dex-range check
# would refuse them, but the contract has no way to express one — ``battle_mask``
# masks and ``battle_value`` compares for equality, and neither is a range — so
# this is recorded rather than fixed. It is a LOST battle's last two presses in
# the drives here; a won battle's block reads 0 at the same point, and an
# ESCAPE was never driven, so whether it does the same is untested.
#
# And this confound is not one a corpus can break, which is worth saying because
# the instinct is to go and fight more battles. A gen-5 LOSS WARPS the player to
# a Pokemon Center, so "lost" and "the scene changed" are the same event, and a
# byte that separates them is at least as likely to be reading the scene.
# Measured: the strictest law over the close frames — one value across every
# drive in an outcome class, a different one across the others, inside the same
# NAMED allocation at the same offset in every dump, and every value under 8 —
# leaves 9,149 candidates, and every one of them sits inside an ``arc_tool.c``
# graphics archive. That is Platinum's 6-for-6 wearing a bigger number.
#
# The corpus is also incomplete in the way that matters, and that is part of the
# result rather than a footnote to it: trainer WON (the prize payout), trainer
# LOST (the whiteout to a Pokemon Center) and wild WON four times are driven and
# screen-confirmed, but a wild LOSS is not. Tepig outruns and out-damages
# everything on Routes 1 and 2; the input route to a non-damaging move does not
# survive the action ring (d-pad RIGHT selects POKEMON, not the second move);
# and writing the battler's current HP down does not hold, because the engine
# refreshes it from the party mon. So the "lost" class contains one KIND — which
# is exactly the shape that made Platinum's outcome byte look perfect. The
# outcome stays None, which renders as "unknown".
#
# WHAT IS ALSO NOT HERE: a trainer identity. The battle proc work carries the
# opponent's record right after the name pointer — u16 at +0x5c and +0x5e, plus
# +0x60 — populated in all 4 trainer battles and zero in all 30 wild ones:
# Bianca (38, 60, 16), Cheren (37, 54, 16), N (40, 64, 7), the Youngster
# (2, 1, 1), and Black 2's Hugh (145, 162, 16). One of the first two is the
# trainer's id and the other is his CLASS, and this corpus cannot say which:
# all four Black trainers differ in BOTH fields, so nothing repeats to mark
# the class, and the magnitudes do not separate them either.
# Writing a class into ``trainer_id`` hands a roster lookup a key from the
# wrong keyspace, which is exactly what ``trainer_class`` exists to prevent, so
# both stay None. The control that settles it is one more fight against a
# trainer of a class already seen — a second Youngster on Route 2 — where the
# class repeats and the id does not.
#: Data of the 0x490 procsys.c block at 0x02269740. ``_G5_FOE_LEVEL``,
#: ``_G5_TRAINER_NAME`` and ``_G5_FOE_LEN`` are Black 2's, defined with its
#: contract above — one definition for both cartridges, because the offsets are
#: the same MEASUREMENT and not two that happen to agree.
_BLACK_BATTLE_PROC = 0x02269760

BLACK = GameMemory(
    game="black-us",
    console="NDS",
    spec=(f"{_BLACK_ACTOR:#x}:16", f"{_BLACK_FOE_SPECIES:#x}:{_G5_FOE_LEN:#x}",
          f"{_BLACK_BATTLE_PROC + _G5_TRAINER_NAME:#x}:4"),
    map_id=Field(0, 0, "<I"),
    x=Field(0, 4, "<i", shift=16),
    y=Field(0, 12, "<i", shift=16),
    # The same field twice on purpose: the flag IS "an opponent exists".
    battle_flag=Field(1, 0, "<H"),
    battle_mask=0xFFFF,
    foe_species=Field(1, 0, "<H"),
    foe_level=Field(1, _G5_FOE_LEVEL, "<B"),
    battle_kind=Field(2, 0, "<I"),
    #: The whole word: the test is "the trainer-name pointer is not NULL", and
    #: masking a pointer down to a bit would be picking one of its address bits.
    battle_kind_trainer=0xFFFFFFFF,
    notes=(
        "Gen 5, same block shape as Black 2 at a different base: map id +0, x "
        "+4, height +8, y +12, coordinates 16.16 fixed point. The actor is also "
        "reachable through four pointers (*0x02258284, *0x022582c0, "
        "*0x022584f0, *0x02330464), all holding this address in every state "
        "dumped, so the raw form is a choice and not a gamble. Raw addresses "
        "survive a map load for the ACTOR; map-scoped memory does NOT, because "
        "Gen 5 streams it. Outdoor coordinates are global (782, 749 in Nuvema "
        "Town), interiors local. Map ids seen: 391 bedroom, 390 living room, "
        "389 Nuvema Town. No battle flag located: in-battle is DERIVED from "
        "the opponent's species being non-zero, which is weaker than a flag "
        "and is documented above. The battle fields are offsets into two NAMED "
        "heap allocations — gen 5's debug allocator tags every block with the "
        "source file that asked for it — so the level is the species' own "
        "block read 0xc further in, and the kind is the battle proc's pointer "
        "to the opponent trainer's name buffer, NULL when there is no trainer. "
        "34 battle states, 4 trainer and 30 wild, all labelled off their own "
        "frame, and the kind is corroborated by the newly reachable pointer "
        "chase to the BATTLE_SETUP_PARAM's own competitor word — 352/352 "
        "in-battle presses over ten driven battles. battle_outcome is None "
        "because gen 5 consumes the result INSIDE the battle: the param that "
        "carries it never changes while a sample can see it, and is zeroed or "
        "reallocated by the frame the flag clears. The trainer id is None "
        "because the two candidate numbers cannot be told apart yet — all "
        "three above."
    ),
)

CONTRACTS: dict[str, GameMemory] = {
    c.game: c for c in (FIRERED, EMERALD, CRYSTAL, PLATINUM, SOULSILVER, BLACK2, BLACK)}


def contract_for(game: Optional[str]) -> Optional[GameMemory]:
    """The contract for a ``roms.yaml`` game key, or ``None``.

    ``None`` is a supported answer and the caller must handle it: a game with no
    contract records no position, which the trace reports as ``blind``. That is
    strictly better than the alternative this module replaced, where every game
    got FireRed's addresses and a Gen 4 cartridge answered with coordinates read
    out of whatever sits at 0x03005008.
    """
    return CONTRACTS.get(game) if game else None


def contract_for_rom_path(rom_path: Optional[str]) -> Optional[GameMemory]:
    """The contract for whatever cartridge a config points at.

    The join goes through ``configs/roms.yaml`` rather than through the file
    name, so a renamed dump still resolves and an unregistered one resolves to
    ``None`` instead of to a guess.
    """
    if not rom_path:
        return None
    from src.app.roms import rom_for_path

    rom = rom_for_path(rom_path)
    return contract_for(rom.game) if rom else None





def attach(emu: Any, contract: Optional[GameMemory]) -> Optional[GameMemory]:
    """Point an emulator at one game's contract — the spec AND its decoder.

    Both in one call, deliberately. The spec says which raw bytes get sampled
    after every button and the contract says how to read them back, and a spec
    from one cartridge paired with a decoder from another is precisely the
    failure this module exists to prevent: it does not raise, it returns
    coordinates. Keeping them on the same object at the same moment makes that
    pairing unrepresentable rather than merely discouraged.

    A ``None`` contract sets an EMPTY spec, so an unregistered game samples
    nothing and its trace reports ``blind`` — the safe degradation
    (``cross-game-plan.md`` §2.2: a missing answer is designed for, a wrong one
    is silent corruption of the headline figure).
    """
    emu.trace_contract = contract
    emu.trace_spec = list(contract.spec) if contract is not None else []
    return contract


__all__ = ["Field", "GameMemory", "CONTRACTS", "FIRERED", "EMERALD", "CRYSTAL", "PLATINUM", "SOULSILVER", "BLACK2", "BLACK",
           "attach", "contract_for", "contract_for_rom_path"]
