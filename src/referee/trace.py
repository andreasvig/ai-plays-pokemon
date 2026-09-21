"""Per-input trace: where the player stood and whether a battle was on after
EVERY button of a turn — the exact record behind overworld steps.

Plan §3.2, ``artifacts/battle-and-movement-fidelity/plan.md`` (2026-09-14). The
Lua bridge stays dumb: Python hands it a TRACE SPEC — raw memory ranges, with
an optional pointer dereference — and after each input's gap the bridge samples
those ranges and keeps ``name|hex;hex;hex`` rows until ``TRACE`` collects them
(:meth:`EmulatorClient.fetch_trace`). All game knowledge is here.

Spec entry grammar (one string per range, ``;``-joined on the wire):
    ``<addr>:<len>``            absolute bus address
    ``*<ptr>+<off>:<len>``      dereference the u32 at ``ptr`` (must land in EWRAM), then read at +off

Samples per input, in spec order:
    0. SaveBlock1 +0..6 — player x (s16), y (s16), map_group (u8), map_num (u8)
    1. gMain byte holding ``inBattle`` (bit 1) — src/referee/battles.py
    2. SaveBlock1 +0x121C — gameStats[7] (total battles), XOR-encrypted; decoded
       with the SaveBlock2 key the referee read at its last poll

Derived per turn (rule set of the plan, decision 2B recorded alongside 2A):
- ``overworld_steps`` — inputs after which the tile changed while no battle was
  on before or after (a warp counts as one step, like the walk graph).
- ``inputs_lost`` — direction inputs that moved nothing outside a battle. Kept
  as the total; the four buckets below say WHY each one moved nothing.
- ``moved_inputs`` — presses after which the player stood somewhere else. NOT
  ``overworld_steps``, which counts TILES: one scripted B press can move 8.
- ``battle_edge`` — the press a battle ended on. It is neither a battle input
  nor an overworld one, and no tile comparison across the boundary is safe.
- ``unclassified`` — no tile to compare against (the first press of a blind
  turn, a sample the bridge could not read).
- ``walls_hit`` — the player was already facing that way and the walk graph
  gives the target no edge. The only bucket charged against efficiency
  (plan W2/W4, ``artifacts/wasted-inputs/plan.md``).
- ``turns_to_face`` — first press in a new direction. In FireRed that only
  turns the player, and turning to face a sign or an NPC is a necessary input,
  so it is never charged.
- ``blocked_by_actor`` — the tile is open but nothing moved: a textbox, an NPC
  or a script ate the press. Measured, not charged — model error and bad luck
  are indistinguishable without a textbox flag in the spec.
- ``blocked_unknown`` — off-graph, or a tile the graph gives a cross-map (warp)
  edge, where a door approach is indistinguishable from a wall. Never charged.
- ``idle_ab`` — an A or B outside a battle after which nothing moved. Whether it
  advanced a dialogue box or hit nothing is not in the spec; measured only.
- ``battle_inputs`` — inputs pressed while a battle was on.
- ``battle_started_at`` — index of the first input after which a battle was on
  (None when none).
"""

from __future__ import annotations

import struct
from typing import Any, Callable, Optional

from src.referee.battles import GAME_STAT_TOTAL_BATTLES, GMAIN_IN_BATTLE_BYTE, SB1_GAME_STATS, in_battle_from_byte
from src.referee.contracts import FIRERED, GameMemory

#: FireRed's spec, kept as an ALIAS of its contract so the assertion in
#: tests/test_trace.py stays a live parity check between this module's historical
#: constant and contracts.FIRERED.
#:
#: NOT what a run wires in any more. Assigning this to ``emu.trace_spec`` is what
#: made every non-FireRed run dereference 0x03005008 on a cartridge that keeps no
#: pointer there; use ``contracts.attach(emu, contracts.contract_for_rom_path(...))``,
#: which sets the spec and its decoder together.
TRACE_SPEC: list[str] = list(FIRERED.spec)

MAX_PLAUSIBLE_BATTLES = 10_000  # a torn read shows up as a huge number

# The furthest the PLAYER can move between two samples, in tiles. Samples are
# taken at the end of every input's gap, so the window is one button's
# hold+gap: 12+24 frames for a direction, 12+100 for A/B (config-5.1), and the
# fastest the game ever walks the player is 8 frames per tile (running). That
# bounds one input at 14 tiles; 16 leaves margin for a slower config.
# A displacement BEYOND it is not walking — the game relocated the player:
# a blackout after losing every Pokémon warps to the last heal point (live
# 2026-09-15: gemini-3.8-flash(high) went Viridian Forest → the player's house,
# 224 tiles, and Pewter Gym → the player's house, 374 tiles, on one B press
# each). Those count as ONE step, like any other warp, and are counted in
# ``relocations``; crediting the shortest path would have charged the run 598
# steps it never walked — a quarter of its movement.
MAX_TILES_PER_INPUT = 16

DIRECTIONS = {"U": "up", "D": "down", "L": "left", "R": "right", "UP": "up", "DOWN": "down", "LEFT": "left", "RIGHT": "right"}


def _bank_is_wrong(contract: GameMemory, samples: list[bytes]) -> bool:
    """True when the sample was taken with the wrong memory bank paged in.

    Fails OPEN when the register itself did not arrive: a missing spec entry is
    the short-read case the per-field rule already covers, and blanking a whole
    turn over it would refuse far more than the hazard it guards. What is
    refused is only the measured event — the register present and saying a
    different bank.
    """
    if contract.bank_reg is None:
        return False
    bank = contract.bank_reg.read(samples)
    return bank is not None and bank != contract.bank_value


def decode_samples(rows: list[tuple[str, list[bytes]]], key: Optional[int] = None,
                   contract: Optional[GameMemory] = None) -> list[dict[str, Any]]:
    """``[(input name, [bytes per spec entry])]`` → one dict per input.

    ``contract`` (``src/referee/contracts.py``) says where each value sits in
    the samples. It used to be this function's own ``<hhBB``, which is FireRed's
    SaveBlock1 layout and nobody else's: Gen 2 keeps a coordinate in one
    UNSIGNED byte and orders the record (group, number, y, x), and Gen 4 uses a
    single map id with 32-bit fields. A decoder that knows one layout can only
    ever be pointed at one cartridge.

    ``contract=None`` decodes NOTHING — every field stays ``None`` and
    :func:`derive` reports the turn as ``blind``. That is deliberate, and it is
    the safe direction: the other candidate default, FireRed's layout, reads six
    bytes from wherever the spec happened to point and returns numbers that look
    exactly like coordinates on a cartridge that keeps none there.
    """
    out: list[dict[str, Any]] = []
    for i, (name, samples) in enumerate(rows):
        d: dict[str, Any] = {"i": i, "input": name, "map_group": None, "map_num": None,
                             "map_id": None, "x": None, "y": None,
                             "in_battle": None, "battles_total": None,
                             "foe_species": None, "foe_level": None,
                             "battle_kind": None, "battle_outcome": None,
                             "trainer_id": None, "trainer_class": None}
        if contract is not None and _bank_is_wrong(contract, samples):
            # The GBC paged another WRAM bank in while this sample was taken, so
            # every 0xd000-0xdfff byte in it belongs to a different memory. Not
            # a short read and not a zero: a CONFIDENT wrong number. Leaving the
            # whole row None is what makes it an unreadable sample rather than
            # an overworld one — measured on Crystal, where these rows carry the
            # phantom (0,0) map and a battle flag that reads 2 on a route.
            out.append(d)
            continue
        if contract is not None:
            for key_, f in (("x", contract.x), ("y", contract.y),
                            ("map_group", contract.map_group), ("map_num", contract.map_num),
                            ("map_id", contract.map_id)):
                if f is not None:
                    d[key_] = f.read(samples)
            if contract.battle_flag is not None:
                raw = contract.battle_flag.read(samples)
                if raw is not None:
                    masked = raw & contract.battle_mask
                    d["in_battle"] = (masked == contract.battle_value
                                      if contract.battle_value is not None
                                      else bool(masked))
                if d["in_battle"]:
                    # Gated on the flag, never reported bare: outside a battle
                    # these fields hold the PREVIOUS opponent, so an ungated read
                    # would put the last fight's species on the tile of the next
                    # one. Measured on Platinum, three overworld states deep, and
                    # again on Emerald, where the trainer id of a fight two
                    # battles ago still sits there during a wild encounter.
                    if contract.foe_species is not None:
                        d["foe_species"] = contract.foe_species.read(samples)
                    if contract.foe_level is not None:
                        d["foe_level"] = contract.foe_level.read(samples)
                    if contract.battle_kind is not None:
                        raw = contract.battle_kind.read(samples)
                        if raw is not None:
                            d["battle_kind"] = ("trainer" if raw & contract.battle_kind_trainer
                                                else "wild")
                        if d["battle_kind"] == "trainer":
                            # Only with the kind. These keep the last trainer
                            # until the next one, so on a wild fight they name
                            # somebody the player is not fighting — measured on
                            # Emerald for the id and on Crystal for the class,
                            # which still read 9 and 22 several presses into the
                            # overworld after their battles ended.
                            if contract.trainer_id is not None:
                                d["trainer_id"] = contract.trainer_id.read(samples)
                            if contract.trainer_class is not None:
                                d["trainer_class"] = contract.trainer_class.read(samples)
                # The outcome is the one battle field gen 3 reads OUTSIDE the
                # flag, and deliberately: gen 3 writes it as the fight closes
                # and holds it, so the sample that carries a segment's result
                # is the first one after the flag goes clear. Read during the
                # fight it is 0, or — in the intro — the previous fight's
                # result.
                #
                # Gen 4 is the other way round and cannot be talked into this
                # one: it FREES the battle heap at the close, so the first
                # clear-flag sample reads whatever the allocator did with those
                # bytes next — 0x78 on Platinum, which gen 4's enum does not
                # name but which a wider table would have rendered as a result.
                # ``outcome_while_in_battle`` puts the read inside the gate, and
                # ``src/app/route.py`` then takes the LAST in-battle value
                # rather than the one after.
                if contract.battle_outcome is not None and (
                        d["in_battle"] or not contract.outcome_while_in_battle):
                    d["battle_outcome"] = contract.battle_outcome.read(samples)
            else:
                # No flag located on this cartridge. Treating every press as an
                # overworld press is the assumption that CANNOT corrupt
                # ``overworld_steps`` — that figure counts tile CHANGES, and a
                # battle changes no tile — whereas leaving it ``None`` makes
                # ``derive`` file every input under ``battle_edge`` and report a
                # turn that moved nowhere. What it does cost is the census: a
                # battle's presses land in ``idle_ab``. Declared by
                # ``GameMemory.census_ok`` rather than hidden.
                d["in_battle"] = False
            if contract.battles_total is not None:
                v = contract.battles_total.read(samples)
                if v is not None and contract.battles_total_encrypted:
                    v = None if key is None else v ^ (key & 0xFFFFFFFF)
                # A sample taken mid-warp (SaveBlock1 being relocated) can read
                # the counter torn: live 2026-09-14 saw 1379579746 on a stair
                # step. No run plays that many battles; drop the value, keep the
                # tile.
                d["battles_total"] = v if (v is not None and v <= MAX_PLAUSIBLE_BATTLES) else None
        out.append(d)
    return out


def _tile(d: dict[str, Any]) -> Optional[tuple[int, int, int, int]]:
    if d.get("x") is None:
        return None
    return (d["map_group"], d["map_num"], d["x"], d["y"])


Distance = Callable[[tuple[int, int, int, int], tuple[int, int, int, int]], Optional[int]]
# ``passable(tile, "U") -> True`` open, ``False`` a wall, ``None`` cannot say
# (off-graph, or a tile with a warp edge — see plan W3).
Passable = Callable[[tuple[int, int, int, int], str], Optional[bool]]


def derive(samples: list[dict[str, Any]], start_tile: Optional[tuple[int, int, int, int]],
           start_in_battle: Optional[bool], distance: Optional[Distance] = None,
           passable: Optional[Passable] = None) -> dict[str, Any]:
    """Turn-level figures from the per-input samples.

    ``start_tile`` / ``start_in_battle`` are the referee's last poll before this
    turn (the state the turn began in). Unknown starts leave the first input's
    step uncounted rather than guessed.

    ``distance(a, b)`` — walk-graph steps between two tiles — makes a
    multi-tile displacement count its tiles (2026-09-15, plan R2): a scripted
    walk (Oak escorting the player to the lab moved ~28 tiles under five B
    presses) is as many steps as the video and the between-poll bound count,
    and ``scripted_tiles`` says how many came from such inputs. Without it, or
    when no path is known, a displacement is one step as before. A displacement
    longer than :data:`MAX_TILES_PER_INPUT` is a RELOCATION the game performed
    (a blackout warp), not a walk: one step, counted in ``relocations``.
    ``end_tile`` is the last tile sampled — the referee compares it with the
    poll that follows: a warp still fading at sample time shows the old tile.

    ``passable(tile, dir)`` — is there a way off ``tile`` in that direction —
    splits a press that moved nothing into the four buckets the docstring at
    the top of this module describes. Facing is INFERRED: every direction press
    leaves the player facing that way whether or not it moved, and an A/B press
    drops the inference back to unknown, since a dialogue or cutscene between
    presses can turn the player. The inference can therefore only UNDER-charge:
    an unknown facing makes the press a ``turns_to_face``, which is free.
    """
    steps = lost = battle_inputs = scripted = relocations = 0
    walls = faces = blocked = unknown = idle_ab = 0
    moved_inputs = battle_edge = unclassified = 0
    battle_started_at: Optional[int] = None
    prev_tile, prev_batt = start_tile, start_in_battle
    prev_total: Optional[int] = None
    facing: Optional[str] = None
    for d in samples:
        tile, batt = _tile(d), d.get("in_battle")
        # The battle counter moves at the START of a battle, a few frames before
        # gMain.inBattle is raised (live 2026-09-14: the rival fight's last sample
        # read counter 1, bit 0; the poll a second later read the bit set). A
        # counter step therefore marks the input the battle began on.
        total = d.get("battles_total")
        if total is not None and prev_total is not None and total > prev_total:
            batt = True
        if total is not None:
            prev_total = total
        if batt:
            battle_inputs += 1
            if battle_started_at is None and not prev_batt:
                battle_started_at = d["i"]
        quiet = (prev_batt is False) and (batt is False)
        if batt:
            pass                       # already counted in battle_inputs
        elif batt is None and tile is None:
            # Nothing was read at all — a blind turn, or a sample the contract
            # refused (Crystal's wrong WRAM bank). ``battle_edge`` means "the
            # press a battle ended on", which this is not, and filing it there
            # put 6% of a Crystal run in a bucket that is supposed to hold one
            # press per fight.
            unclassified += 1
        elif not quiet:
            battle_edge += 1           # the press a battle ended on: neither
        elif tile is None or prev_tile is None:
            unclassified += 1          # nothing to compare this press against
        if quiet and tile is not None and prev_tile is not None:
            if tile != prev_tile:
                moved_inputs += 1
                moved = distance(prev_tile, tile) if distance is not None else None
                moved = moved if isinstance(moved, int) and moved > 0 else 1
                if moved > MAX_TILES_PER_INPUT:  # the game moved the player, not the player
                    relocations += 1
                    moved = 1
                steps += moved
                scripted += moved - 1
            elif (key := str(d.get("input", "")).upper()) in DIRECTIONS:
                lost += 1
                if facing != key:
                    faces += 1              # only turned to face — a legal input
                else:
                    open_ = passable(tile, key) if passable is not None else None
                    if open_ is False:
                        walls += 1          # facing a wall and pressed into it
                    elif open_ is True:
                        blocked += 1        # a textbox, an NPC or a script ate it
                    else:
                        unknown += 1        # off-graph or a warp tile
            else:
                idle_ab += 1
        # Every OVERWORLD direction press leaves the player facing that way,
        # moved or not. An A/B drops the inference (a dialogue or cutscene
        # between presses can turn the player) and so does a battle, where a
        # direction press moves a menu cursor and not the player.
        pressed = str(d.get("input", "")).upper()
        facing = pressed if (quiet and pressed in DIRECTIONS) else None
        if tile is not None:
            prev_tile = tile
        if batt is not None:
            prev_batt = batt
    # A trace whose samples carry no tile at all (the bridge could not read the
    # ranges) is BLIND: it must not report 0 steps as a measurement.
    blind = not any(_tile(d) is not None for d in samples)
    return {
        "inputs": len(samples),
        "blind": blind,
        "overworld_steps": None if blind else steps,
        "inputs_lost": lost,
        "battle_inputs": battle_inputs,
        "battle_started_at": battle_started_at,
        "end_in_battle": prev_batt,
        "scripted_tiles": scripted,
        "relocations": relocations,
        # Every input lands in EXACTLY ONE of the buckets below, so a report
        # that lists them can state a total that adds up. Guarded by
        # tests/test_trace.py::test_the_buckets_partition_every_input.
        "moved_inputs": moved_inputs,
        "battle_edge": battle_edge,
        "unclassified": unclassified,
        "walls_hit": walls,
        "turns_to_face": faces,
        "blocked_by_actor": blocked,
        "blocked_unknown": unknown,
        "idle_ab": idle_ab,
        "end_tile": None if blind else prev_tile,
    }


# The buckets a turn's trace splits its inputs into — a PARTITION of the turn's
# inputs, so they sum to ``inputs`` — and the one charged against movement
# efficiency. ``src/app/replay.py`` sums these over a run.
INPUT_BUCKETS = ("moved_inputs", "battle_inputs", "battle_edge", "walls_hit", "turns_to_face",
                 "blocked_by_actor", "blocked_unknown", "idle_ab", "unclassified")
CHARGED_BUCKET = "walls_hit"

__all__ = ["TRACE_SPEC", "MAX_TILES_PER_INPUT", "INPUT_BUCKETS", "CHARGED_BUCKET",
           "decode_samples", "derive"]
