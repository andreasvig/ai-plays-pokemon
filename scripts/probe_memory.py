#!/usr/bin/env python3
"""FireRed (BPRE-US) memory-probe — Phase-2 confirmation tool.

A standalone diagnostic CLI Andreas runs LIVE against a running mGBA to confirm
the verified FireRed address table in ``local/plan-referee-benchmark.md`` matches
the real ROM. It is NOT part of the turn loop and is never imported by the agent.

It reuses the project's own plumbing — ``EmulatorClient.read_memory`` (the
``READMEM`` socket command from Phase 1) and the verified address constants from
``src/referee/referee.py`` — so the probe can never drift from what the referee
actually reads.

Connection model (same as the runner): this process starts a TCP server on
127.0.0.1:<port> and waits for mGBA's Lua bridge to connect. So before running a
connecting subcommand you must already have mGBA open with the per-run
``socketserver-*.lua`` loaded (Tools > Scripting > File > Load script…). If no
connection arrives within --connect-timeout the probe prints that instruction
and exits.

Workflow to discover/confirm which flag an in-game event sets
--------------------------------------------------------------
The flag bitfield is a 0x120-byte array. To find the bit a quest sets:

  1. Stand at a known state (e.g. bedroom_start) and dump the flags to a file:

         scripts/probe_memory.py read --dump-flags --save-flags /tmp/before.hex

  2. Act in the game (walk downstairs, talk to Oak, take the starter…).

  3. Dump again and diff against the saved baseline — flipped flag IDs print:

         scripts/probe_memory.py read --diff-flags /tmp/before.hex

     (e.g. taking the starter should flip a bit in the 0x828 region).

Subcommands
-----------
  read   Connect to mGBA and print live state (pointers, map, coords, party,
         named vars/flags); optionally dump/diff the flag array.
  rom    Read a ROM file from disk (NO emulator needed) and print its revision
         byte (header offset 0xBC) + SHA-1, so the benchmark ROM can be pinned.

All output is human-readable; this is a diagnostic tool, not a library.
"""

from __future__ import annotations

import argparse
import hashlib
import struct
import sys
from pathlib import Path

# Import the verified address constants from the referee so the probe and the
# referee can never disagree about where things live in memory.
from src.referee.referee import (
    EWRAM_BROAD_HI,
    EWRAM_BROAD_LO,
    GSAVEBLOCK1_PTR,
    GSAVEBLOCK2_PTR,
    PLAYER_PARTY_COUNT,
    SB1_FLAGS,
    SB1_FLAGS_LEN,
    SB1_MAP_GROUP,
    SB1_MAP_NUM,
    SB1_PLAYER_X,
    SB1_PLAYER_Y,
    SB1_VARS,
    VAR_BASE_ID,
)

# The plan's TIGHT sanity band (where the SaveBlocks normally land). The referee
# uses a deliberately broader band as a guard; the probe reports against BOTH so
# Andreas sees whether a pointer is in the expected tight window AND the broad
# EWRAM band.
EWRAM_TIGHT_LO = 0x02024000
EWRAM_TIGHT_HI = 0x0202A000

# Named values the probe confirms by reading (from the plan's ladder table).
NAMED_VARS = [
    (0x4055, "VAR_MAP_SCENE_OAKS_LAB (pokedex cross-check, expect >= 6 after Pokedex)"),
    (0x4057, "VAR_MAP_SCENE_VIRIDIAN_CITY_MART (parcel, expect >= 2 after delivery)"),
]
NAMED_FLAGS = [
    (0x828, "FLAG_SYS_POKEMON_GET (starter chosen)"),
    (0x829, "FLAG_SYS_POKEDEX_GET (pokedex received)"),
    (0x820, "FLAG_BADGE01_GET (Brock defeated)"),
    (0x258, "FLAG_BEAT_RIVAL_IN_OAKS_LAB (first rival battle done)"),
]

# How much of SaveBlock1 to read in one shot: through the end of the vars array.
_SB1_READ_LEN = SB1_VARS + 256 * 2  # 0x1200


# --------------------------------------------------------------------------- #
# Decode helpers (the referee's equivalents are private; replicated minimally) #
# --------------------------------------------------------------------------- #

def _u32_le(data: bytes) -> int:
    return struct.unpack("<I", data)[0]


def _s16_le(block: bytes, offset: int) -> int:
    return struct.unpack_from("<h", block, offset)[0]


def _u16_le(block: bytes, offset: int) -> int:
    return struct.unpack_from("<H", block, offset)[0]


def _flag_set(block: bytes, flag_id: int) -> bool:
    """True iff story-flag ``flag_id``'s bit is set in the SB1 bitfield."""
    byte_index = SB1_FLAGS + (flag_id >> 3)
    bit = flag_id & 7
    return bool((block[byte_index] >> bit) & 1)


def _var_value(block: bytes, var_id: int) -> int:
    offset = SB1_VARS + (var_id - VAR_BASE_ID) * 2
    return _u16_le(block, offset)


def _in_tight(ptr: int) -> bool:
    return EWRAM_TIGHT_LO <= ptr < EWRAM_TIGHT_HI


def _in_broad(ptr: int) -> bool:
    return EWRAM_BROAD_LO <= ptr < EWRAM_BROAD_HI


def _flag_bits(flag_array: bytes) -> set[int]:
    """Return the set of flag IDs whose bit is set in a raw flag-array bytes."""
    out: set[int] = set()
    for byte_index, byte in enumerate(flag_array):
        if byte == 0:
            continue
        for bit in range(8):
            if (byte >> bit) & 1:
                out.add(byte_index * 8 + bit)
    return out


# --------------------------------------------------------------------------- #
# Emulator connection                                                          #
# --------------------------------------------------------------------------- #

def _connect(host: str, port: int, timeout: float):
    """Build an EmulatorClient and wait for the mGBA Lua bridge to connect.

    Prints a clear instruction and exits non-zero if no connection arrives.
    Imported lazily so ``--help`` and the ``rom`` subcommand never touch the
    network stack.
    """
    from src.emulator import EmulatorClient

    # Minimal config: EmulatorClient only needs the emulator host/port (+ the
    # optional timing/inputs keys, which default fine for a read-only probe).
    config = {"emulator": {"host": host, "port": port}}
    emu = EmulatorClient(config)

    print(f"Starting TCP server on {host}:{port} and waiting for mGBA…")
    print(
        "  (In mGBA: Tools > Scripting > File > Load script… the per-run "
        "socketserver-*.lua, then this probe will connect.)"
    )
    try:
        emu.connect(timeout=timeout)
    except (ConnectionError, TimeoutError) as exc:
        print(f"\nERROR: could not connect to mGBA: {exc}", file=sys.stderr)
        print(
            "\nIs mGBA running with the Lua bridge loaded? Open mGBA, load the "
            "ROM, then Tools > Scripting > File > Load script… > socketserver-*.lua. "
            "Then re-run this probe.",
            file=sys.stderr,
        )
        sys.exit(2)
    return emu


def _read_saveblock1_ptr(emu) -> int:
    return _u32_le(emu.read_memory(GSAVEBLOCK1_PTR, 4))


def _read_saveblock2_ptr(emu) -> int:
    return _u32_le(emu.read_memory(GSAVEBLOCK2_PTR, 4))


def _read_sb1_block(emu, sb1_ptr: int) -> bytes:
    return emu.read_memory(sb1_ptr, _SB1_READ_LEN)


# --------------------------------------------------------------------------- #
# `read` subcommand                                                            #
# --------------------------------------------------------------------------- #

def cmd_read(args: argparse.Namespace) -> int:
    emu = _connect(args.host, args.port, args.connect_timeout)
    try:
        # --- Dereference both SaveBlock pointers --------------------------- #
        sb1_ptr = _read_saveblock1_ptr(emu)
        sb2_ptr = _read_saveblock2_ptr(emu)

        print("\n=== SaveBlock pointers (IWRAM, deref fresh every poll) ===")
        for name, ptr in (("gSaveBlock1Ptr", sb1_ptr), ("gSaveBlock2Ptr", sb2_ptr)):
            tight = "in tight band" if _in_tight(ptr) else "OUT of tight band"
            broad = "in EWRAM" if _in_broad(ptr) else "OUT of EWRAM"
            print(f"  {name:14s} = 0x{ptr:08X}  ({tight}; {broad})")

        if not _in_broad(sb1_ptr):
            print(
                "\nWARNING: gSaveBlock1Ptr is out of the EWRAM band — the game is "
                "probably not in-game yet (title screen / intro). Load a save state "
                "or start the game, then re-run.",
                file=sys.stderr,
            )
            return 1

        # --- One big SaveBlock1 read (map + coords + flags + vars) --------- #
        block = _read_sb1_block(emu, sb1_ptr)

        map_group = block[SB1_MAP_GROUP]
        map_num = block[SB1_MAP_NUM]
        x = _s16_le(block, SB1_PLAYER_X)
        y = _s16_le(block, SB1_PLAYER_Y)
        party_count = emu.read_memory(PLAYER_PARTY_COUNT, 1)[0]

        print("\n=== Current state ===")
        print(f"  map (group, num) = ({map_group}, {map_num})")
        print("    [bedroom_start expects (4, 1); Pallet Town (3, 0)]")
        print(f"  player coords    = (x={x}, y={y})")
        print(f"  party count      = {party_count}  (0x02024029)")

        # --- Named vars ---------------------------------------------------- #
        print("\n=== Named vars (SB1 + 0x1000 + (id-0x4000)*2, u16) ===")
        for var_id, label in NAMED_VARS:
            val = _var_value(block, var_id)
            print(f"  0x{var_id:04X} = {val:5d}   {label}")

        # --- Named ladder flags ------------------------------------------- #
        print("\n=== Named ladder flags (byte=SB1+0xEE0+(id>>3), bit=id&7) ===")
        for flag_id, label in NAMED_FLAGS:
            state = "SET" if _flag_set(block, flag_id) else "unset"
            print(f"  0x{flag_id:04X} [{state:5s}]  {label}")

        # --- Flag array dump / diff --------------------------------------- #
        flag_array = block[SB1_FLAGS:SB1_FLAGS + SB1_FLAGS_LEN]

        if args.save_flags:
            Path(args.save_flags).write_text(flag_array.hex())
            print(
                f"\nSaved {len(flag_array)}-byte flag array to {args.save_flags} "
                "(hex). Act in-game, then re-run with --diff-flags against it."
            )

        if args.dump_flags:
            print(f"\n=== Flag array dump (SB1+0x{SB1_FLAGS:X}, "
                  f"{SB1_FLAGS_LEN} bytes) ===")
            hexstr = flag_array.hex()
            for i in range(0, len(hexstr), 64):  # 32 bytes per line
                print(f"  +0x{i // 2:03X}: {hexstr[i:i + 64]}")

        if args.diff_flags:
            baseline_path = Path(args.diff_flags)
            if not baseline_path.exists():
                print(
                    f"\nERROR: --diff-flags baseline not found: {baseline_path}",
                    file=sys.stderr,
                )
                return 1
            try:
                baseline = bytes.fromhex(baseline_path.read_text().strip())
            except ValueError as exc:
                print(
                    f"\nERROR: baseline file is not valid hex: {exc}",
                    file=sys.stderr,
                )
                return 1
            if len(baseline) != SB1_FLAGS_LEN:
                print(
                    f"\nWARNING: baseline is {len(baseline)} bytes, expected "
                    f"{SB1_FLAGS_LEN}; diffing the overlapping prefix.",
                    file=sys.stderr,
                )
            before = _flag_bits(baseline)
            after = _flag_bits(flag_array)
            flipped_on = sorted(after - before)
            flipped_off = sorted(before - after)
            print("\n=== Flag diff (baseline -> now) ===")
            if not flipped_on and not flipped_off:
                print("  (no flags changed)")
            if flipped_on:
                print("  newly SET:")
                for fid in flipped_on:
                    print(f"    0x{fid:04X}")
            if flipped_off:
                print("  newly UNSET:")
                for fid in flipped_off:
                    print(f"    0x{fid:04X}")

        return 0
    finally:
        try:
            emu.disconnect()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# `rom` subcommand (no emulator)                                               #
# --------------------------------------------------------------------------- #

_REVISION_NAMES = {0x00: "v1.0", 0x01: "v1.1"}
_HEADER_REVISION_OFFSET = 0xBC


def cmd_rom(args: argparse.Namespace) -> int:
    rom_path = Path(args.rom)
    if not rom_path.exists():
        print(f"ERROR: ROM file not found: {rom_path}", file=sys.stderr)
        return 2
    if not rom_path.is_file():
        print(f"ERROR: not a file: {rom_path}", file=sys.stderr)
        return 2

    data = rom_path.read_bytes()
    if len(data) <= _HEADER_REVISION_OFFSET:
        print(
            f"ERROR: ROM too small ({len(data)} bytes) to read the header "
            f"revision byte at offset 0x{_HEADER_REVISION_OFFSET:X}.",
            file=sys.stderr,
        )
        return 1

    revision_byte = data[_HEADER_REVISION_OFFSET]
    revision_name = _REVISION_NAMES.get(revision_byte, "unknown")
    sha1 = hashlib.sha1(data).hexdigest()

    print(f"=== ROM: {rom_path} ===")
    print(f"  size           = {len(data)} bytes")
    print(
        f"  revision byte  = 0x{revision_byte:02X} (offset 0x{_HEADER_REVISION_OFFSET:X}) "
        f"-> {revision_name}"
    )
    print(f"  SHA-1          = {sha1}")
    print("\n  Pin this SHA-1 in configs/checkpoints-firered-v1.yaml (rom_sha1):")
    print("    v1.0 = 41cb23d8dccc8ebd7c649cd8fbb58eeace6e2fdc")
    print("    v1.1 = dd5945db9b930750cb39d00c84da8571feebf417")
    return 0


# --------------------------------------------------------------------------- #
# argparse                                                                     #
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="probe_memory.py",
        description=(
            "FireRed memory probe — confirm the verified address table against "
            "the real ROM/RAM. LIVE tool: the `read` subcommand needs a running "
            "mGBA with the Lua bridge loaded."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Workflow (discover/confirm the flag an event sets):
  1. probe_memory.py read --dump-flags --save-flags /tmp/before.hex   # baseline
  2. <act in the game: walk downstairs, talk to Oak, take the starter>
  3. probe_memory.py read --diff-flags /tmp/before.hex                # see flips

Examples:
  # Live state (pointers, map, coords, party, named vars/flags):
  probe_memory.py read

  # Pin the benchmark ROM (no emulator needed):
  probe_memory.py rom --rom "roms/Pokemon - FireRed Version (USA, Europe) (Rev 1).gba"
""",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # --- read --- #
    p_read = sub.add_parser(
        "read",
        help="Connect to mGBA and print live FireRed state.",
        description=(
            "Connect to a running mGBA (Lua bridge loaded), dereference both "
            "SaveBlock pointers, and print map/coords/party/named vars+flags. "
            "Optionally dump or diff the 0x120-byte flag array."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_read.add_argument(
        "--host", default="127.0.0.1",
        help="TCP host the probe listens on for mGBA. Default: 127.0.0.1.",
    )
    p_read.add_argument(
        "--port", type=int, default=8888,
        help="TCP port the probe listens on for mGBA. Default: 8888.",
    )
    p_read.add_argument(
        "--connect-timeout", type=float, default=300.0,
        help="Seconds to wait for the mGBA Lua bridge to connect. Default: 300.",
    )
    p_read.add_argument(
        "--dump-flags", action="store_true",
        help="Print the full 0x120-byte flag array as hex (32 bytes/line).",
    )
    p_read.add_argument(
        "--save-flags", metavar="FILE",
        help="Write the flag array (hex) to FILE — a baseline for a later "
             "--diff-flags. Run once before acting in-game.",
    )
    p_read.add_argument(
        "--diff-flags", metavar="FILE",
        help="Diff the live flag array against a baseline FILE saved earlier "
             "with --save-flags; prints which flag IDs flipped on/off.",
    )
    p_read.set_defaults(func=cmd_read)

    # --- rom --- #
    p_rom = sub.add_parser(
        "rom",
        help="Read a ROM file's revision byte + SHA-1 (no emulator needed).",
        description=(
            "Read a ROM file from disk and print its header revision byte "
            "(offset 0xBC: 0x00=v1.0, 0x01=v1.1) and SHA-1, so the benchmark "
            "ROM can be pinned. This is the one subcommand runnable without mGBA."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_rom.add_argument(
        "--rom", required=True, metavar="PATH",
        help="Path to the .gba ROM file to inspect.",
    )
    p_rom.set_defaults(func=cmd_rom)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
