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
    notes: str = ""

    @property
    def census_ok(self) -> bool:
        """True when the input census can distinguish a battle press from an
        overworld one. False is not a bug — it is the honest state of every
        contract found by position search alone."""
        return self.battle_flag is not None

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

FIRERED = GameMemory(
    game="firered-us",
    console="GBA",
    # Byte-identical to the TRACE_SPEC this module replaces. The existing
    # referee and trace tests are the oracle for that, per cross-game-plan P-D
    # ("with no behaviour change").
    spec=(f"*{_FIRERED_SB1:#x}+0:6",
          f"{_GMAIN_IN_BATTLE:#x}:1",
          f"*{_FIRERED_SB1:#x}+{_STATS_OFF:#x}:4"),
    x=Field(0, 0, "<h"),
    y=Field(0, 2, "<h"),
    map_group=Field(0, 4, "<B"),
    map_num=Field(0, 5, "<B"),
    battle_flag=Field(1, 0, "<B"),
    battle_mask=1 << _IN_BATTLE_BIT,
    battles_total=Field(2, 0, "<I"),
    battles_total_encrypted=True,
    notes="The control. Reproduces the constants trace.py shipped with.",
)

EMERALD = GameMemory(
    game="emerald-us",
    console="GBA",
    # p-a-results.md §8: the block pointer is *0x03005d8c, chosen from 8
    # spellings by proximity to the x anchor, and the block shuffles here too
    # (0x02025a54 downstairs, 0x02025a64 up) — so the pointer form is load
    # bearing on this cartridge and not copied from FireRed out of symmetry.
    spec=(f"*{_EMERALD_SB1:#x}+0:6", f"{_EMERALD_IN_BATTLE:#x}:1"),
    x=Field(0, 0, "<h"),
    y=Field(0, 2, "<h"),
    map_group=Field(0, 4, "<B"),
    map_num=Field(0, 5, "<B"),
    battle_flag=Field(1, 0, "<B"),
    battle_mask=1 << _IN_BATTLE_BIT,
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

CRYSTAL = GameMemory(
    game="crystal-us",
    console="GB",
    # Four adjacent bytes at 0xdcb5, in the documented Gen 2 order, found as two
    # independent searches that happened to land next to each other
    # (p-a-results.md §10) — the axis search returned 0xdcb7/0xdcb8 and the map
    # search returned 0xdcb5/0xdcb6, and neither was told about the other.
    # Gen 2 does not shuffle its blocks, so these are raw addresses and there is
    # no pointer to dereference.
    spec=("0xdcb5:4", "0xd22d:1"),
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
    # hazard as the position bytes above: on 1 of 174 labelled samples the page
    # collapsed and it read 0 mid-battle. It fails in the SAFE direction, and
    # it fails at the same instant the map key goes invalid, so a consumer can
    # see it. 0xc15a mask 0x01 agreed on 174/174 and lives in the always-mapped
    # bank, but it is an unidentified byte in the sprite area — robustness
    # without provenance. Provenance won; if the 0.6% ever matters, that is the
    # alternative and this is the note that says so.
    battle_flag=Field(1, 0, "<B"),
    battle_mask=0x03,
    notes=(
        "Gen 2 keeps a coordinate in ONE byte and does not DMA-shuffle, so this "
        "is the only contract here with no pointer. Coordinates are unsigned. "
        "Battle flag located 2026-09-20 and it is a MODE byte, not a bit."
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

PLATINUM = GameMemory(
    game="platinum-us",
    console="NDS",
    spec=(f"{_PLATINUM_LOCATION:#x}:16",),
    map_id=Field(0, 0, "<i"),
    x=Field(0, 8, "<i"),
    y=Field(0, 12, "<i"),
    notes=(
        "pret/pokeplatinum struct Location at 0x0227f408: mapHeaderID +0, "
        "warpId +4, x +8, z +12, faceDirection +16, all s32. The map key is a "
        "single id, not a (group, number) pair. No battle flag located, so the "
        "input census is off; the field Location survives a battle unchanged, "
        "which is what lets the walk graph work without one."
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

SOULSILVER = GameMemory(
    game="soulsilver-us",
    console="NDS",
    spec=(f"{_SOULSILVER_LOCATION:#x}:16",),
    map_id=Field(0, 0, "<i"),
    x=Field(0, 8, "<i"),
    y=Field(0, 12, "<i"),
    notes=(
        "Same struct shape as Platinum at a different base: map id +0, x +8, "
        "y +12, elevation +16, and a previous-map/x/y triple after it. Raw "
        "addresses, no pointer — identical across 11 states and 4 maps. Map id "
        "observed only over 60-66 (New Bark Town and its interiors), so its "
        "width above 255 is inferred from the engine, not measured. No battle "
        "flag located."
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
_BLACK2_LOCATION = 0x0223B444

BLACK2 = GameMemory(
    game="black2-us",
    console="NDS",
    spec=(f"{_BLACK2_LOCATION:#x}:16",),
    map_id=Field(0, 0, "<I"),
    x=Field(0, 4, "<i", shift=16),
    y=Field(0, 12, "<i", shift=16),
    notes=(
        "Gen 5. map id +0, x +4, height +8, y +12; x and y are 16.16 fixed "
        "point, so the Field carries shift=16 and the tile is value >> 16. Raw "
        "addresses survive a map load, verified by a live round trip through "
        "the front door. Unova's OUTDOOR coordinates are global (y=764 in a "
        "city thirty tiles across) while interiors are local. Map ids seen: "
        "428 player's house, 427 Aspertia City, 431 neighbour's house. No "
        "battle flag located."
    ),
)

CONTRACTS: dict[str, GameMemory] = {
    c.game: c for c in (FIRERED, EMERALD, CRYSTAL, PLATINUM, SOULSILVER, BLACK2)}


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


__all__ = ["Field", "GameMemory", "CONTRACTS", "FIRERED", "EMERALD", "CRYSTAL", "PLATINUM", "SOULSILVER", "BLACK2",
           "attach", "contract_for", "contract_for_rom_path"]
