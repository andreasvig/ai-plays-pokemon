#!/usr/bin/env python3
"""Human-readable map names for the DS cartridges, straight out of the ROMs.

    ./venv/bin/python scripts/extract_ds_mapnames.py            # write data/ds-map-names.json
    ./venv/bin/python scripts/extract_ds_mapnames.py --control  # the Platinum control
    ./venv/bin/python scripts/extract_ds_mapnames.py --print soulsilver-us

WHY THIS IS A DATA FILE AND A SCRIPT RATHER THAN A RENDERER CHANGE. The two
renderers that would consume it — `render_dsmaps.py` and `render_gen5maps.py` —
were being rewritten in parallel when this was written, so it is delivered as a
table somebody else wires in. The indirection is scheduling, not design: there
is no reason a renderer should not read the cartridge for this itself.

WHAT A "MAP NAME" IS HERE, AND WHY 61, 63, 64 AND 66 ARE ALL `New Bark Town`.
A map header does not carry a name. It carries an INDEX into a table of place
names — the same table the game draws in the plaque when you walk into a new
area — and every interior of a town points at the town. SoulSilver's Elm lab,
the player's two floors and the neighbour's house all read `New Bark Town`,
because that is the only name the cartridge has for them. Platinum's decomp
invents `MAP_HEADER_TWINLEAF_TOWN_RIVAL_HOUSE_2F`; the cartridge does not, and
nothing here will. A caller that needs interiors told apart has to add the
distinction itself. THIS IS ALSO WHY PLATINUM IS NOT IN THE OUTPUT: merging
these names over Platinum's atlas would replace `TwinleafTown_RivalHouse_1F`
with `Twinleaf Town` and lose information. Platinum is the control, not a row.

WHAT WAS FOUND, PER GENERATION.

  GEN 4 (SoulSilver, and Platinum as the control)

    the plaque strings   SoulSilver `a/0/2/7` file 279 (235 of them),
                         Platinum `msgdata/pl_msg.narc` file 433 (126).
                         Located by CONTENT, not by index: `find_text_file`
                         takes the one file in the archive that contains a
                         named set of strings and refuses if two do.
    the name index       the map-header table in the arm9, `u8` at +0x11 of
                         SoulSilver's 24-byte record and at +0x12 of
                         Platinum's — the two games pack the struct
                         differently, which is why this module counts from the
                         table's MATRIX field (the thing the search below
                         actually locates) and not from the record start:
                         SoulSilver +14, Platinum +16.
    the table's address  found, not hard-coded, by the constraint
                         `render_dsmaps.py` already uses: every map id named
                         in the region matrix's header plane must read matrix
                         0. Seventy-five simultaneous equations on SoulSilver,
                         sixty-six on Platinum, and exactly one address
                         survives on each. Anything but exactly one is
                         refused, because a table found by coincidence gives
                         every map a real name from the right game and the
                         wrong place.

  GEN 5 (Black, Black 2)

    the plaque strings   `a/0/0/2` file 89 in Black (117), file 109 in
                         Black 2 (154). Located the same way, by content.
    the name index       `u8` at +0x1A of the 48-byte zone header in
                         `a/0/1/2`, the same table `render_gen5maps.py` reads
                         the matrix and spawn out of. Found by sweeping all 48
                         offsets, both widths, against two known zones; +0x1A
                         is the only offset that satisfies both games.

HOW WE KNOW IT IS NOT OFF BY ONE. A shifted index gives every map a real name,
so "it decoded cleanly" proves nothing. Every game below carries `CHECKS`:
(map id, name) pairs taken from a game frame, not from this code. Six of them
were read off the location plaque in a run recording — the frame is named in
the comment beside each — and the extractor refuses to write the file if any
of them misses. `--control` re-runs the whole method on Platinum, where the
decomp knows the answer for all 593 labelled maps.

KEYS. Map ids are the plain integer string, `"427"`. The atlas spells the same
map `"427:0"` and the observed graphs spell it `"427"`; this file follows the
observed graphs, so a consumer writing into the atlas must add the `:0`.
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ds3d import blz                                          # noqa: E402
from ds3d import dstext                                       # noqa: E402

OUT = REPO / "data" / "ds-map-names.json"
OBSERVED = REPO / "artifacts" / "game-map-render" / "observed"
PRET_CACHE = REPO / "local" / "pret-cache-platinum"

HEADER_STRIDE = 24          # sizeof(MapHeader), both Gen 4 games
ZONE_STRIDE = 48            # sizeof(zone header), both Gen 5 games
ZONE_LABEL = 0x1A           # u8 name index within a Gen 5 zone header
GEN5_ZONE_TABLE = "/a/0/1/2"
GEN5_MESSAGES = "/a/0/0/2"
# The Gen 5 name table's entry 0 is ten full-width dashes, the "no name" row.
GEN5_BLANK = "－"


@dataclass
class Game:
    rom: str
    generation: int
    checks: dict[int, str]
    text_anchors: set[str]
    # gen 4 only
    matrices: str = ""
    messages: str = ""
    label_delta: int = 0


GAMES: dict[str, Game] = {
    "soulsilver-us": Game(
        rom="Pokemon - SoulSilver Version (Europe).nds",
        generation=4,
        matrices="/a/0/4/1",
        messages="/a/0/2/7",
        label_delta=14,
        text_anchors={"New Bark Town", "Route 29", "Mystery Zone", "Cherrygrove City"},
        checks={
            # The plaque, run 2026-09-20_22-12-03 turn 7, recording t=85.0s:
            # the player walks out of the house and "New Bark Town" slides in.
            60: "New Bark Town",
            # src/referee/contracts.py's SoulSilver notes. Not plaque-verified:
            # no run that entered 33 has a recording. Corroborated by the
            # table's own shape — ids 30..43 read Route 26..39 in order.
            33: "Route 29",
        },
    ),
    "black-us": Game(
        rom="Pokemon - Black Version (USA, Europe) (NDSi Enhanced).nds",
        generation=5,
        text_anchors={"Nuvema Town", "Accumula Town", "Mystery Zone", "Route 1"},
        checks={
            # The plaque, run 2026-09-20_23-09-22 turn 94, recording t=1097.5s:
            # the three of them step onto Route 1 and "Route 1" slides in.
            317: "Route 1",
            # The starting town (the brief's free check), and the zone every
            # Nuvema interior names as its parent in render_gen5maps.py.
            389: "Nuvema Town",
        },
    ),
    "black2-us": Game(
        rom="Pokemon - Black Version 2 (USA, Europe) (NDSi Enhanced).nds",
        generation=5,
        text_anchors={"Aspertia City", "Nuvema Town", "Mystery Zone", "Route 19"},
        checks={
            # NOT plaque-verified, and it cannot be from our runs: all four
            # Black 2 maps they entered share one location name, so the game
            # never draws a plaque. A sweep of the whole 2026-09-20_23-39-00
            # recording for the plaque found none, which is the expected
            # result and not a miss. The check is the brief's free one — Black
            # 2 starts in Aspertia City — against the zone the three interiors
            # name as their parent.
            427: "Aspertia City",
        },
    ),
    "platinum-us": Game(
        rom="Pokemon - Platinum Version (USA).nds",
        generation=4,
        matrices="/fielddata/mapmatrix/map_matrix.narc",
        messages="/msgdata/pl_msg.narc",
        label_delta=16,
        text_anchors={"Twinleaf Town", "Route 201", "Mystery Zone", "Sandgem Town"},
        checks={
            # Four plaques from run 2026-09-20_00-17-45's recording, each read
            # together with the TURN the frame is captioned with and the map
            # that turn's samples record:
            418: "Sandgem Town",   # turn 6,   t=78.0s
            342: "Route 201",      # turn 24,  t=216.5s
            343: "Route 202",      # turn 82,  t=801.5s
            411: "Twinleaf Town",  # turn 107, t=1290.0s
        },
    ),
}

# Platinum is the control; the shipped table is the three games that have no
# names at all today.
SHIPPED = ("soulsilver-us", "black-us", "black2-us")


# ---------------------------------------------------------------- the cartridge

class Rom:
    """The ROM's own filesystem, plus its arm9."""

    def __init__(self, path: Path) -> None:
        if not path.is_file():
            raise SystemExit(f"{path} is missing; roms/ is gitignored")
        self.path = path
        self.raw = path.read_bytes()
        self.fnt, _fnt_size, self.fat, fat_size = struct.unpack_from("<IIII", self.raw, 0x40)
        self.count = fat_size // 8
        self.names = self._names()

    def file(self, fid: int) -> bytes:
        start, end = struct.unpack_from("<II", self.raw, self.fat + fid * 8)
        return self.raw[start:end]

    def named(self, path: str) -> bytes:
        if path not in self.names:
            raise SystemExit(f"{self.path.name} has no {path}")
        return self.file(self.names[path])

    def _names(self) -> dict[str, int]:
        out: dict[str, int] = {}

        def walk(dir_id: int, prefix: str) -> None:
            sub, first, _parent = struct.unpack_from("<IHH", self.raw, self.fnt + (dir_id & 0xFFF) * 8)
            at, fid = self.fnt + sub, first
            while True:
                kind = self.raw[at]
                at += 1
                if kind == 0:
                    return
                length = kind & 0x7F
                name = self.raw[at:at + length].decode("ascii", "replace")
                at += length
                if kind & 0x80:
                    child = struct.unpack_from("<H", self.raw, at)[0]
                    at += 2
                    walk(child, f"{prefix}{name}/")
                else:
                    out[f"{prefix}{name}"] = fid
                    fid += 1

        walk(0xF000, "/")
        return out

    def arm9(self) -> bytes:
        """The arm9, decompressed when it is compressed.

        HeartGold/SoulSilver ships a BLZ-compressed arm9 and Platinum does
        not — and Platinum's last word happens to be non-zero, so the "is it
        compressed" flag says yes and the decompressor then walks off the
        start of its own stream. Treating that failure as "it was plain all
        along" is safe here because the caller's table search either finds
        exactly one address in the result or refuses.
        """
        off, _entry, _ram, size = struct.unpack_from("<4I", self.raw, 0x20)
        blob = self.raw[off:off + size]
        try:
            return blz.decode(blob)
        except ValueError:
            return blob


def narc(blob: bytes) -> list[bytes]:
    """The subfiles of a NARC: BTAF holds the offsets, GMIF holds the bytes."""
    if blob[:4] != b"NARC":
        raise SystemExit("not a NARC archive")
    at = struct.unpack_from("<H", blob, 0x0C)[0]
    btaf = gmif = None
    while at < len(blob) - 8:
        tag = blob[at:at + 4]
        size = struct.unpack_from("<I", blob, at + 4)[0]
        if size <= 0:
            break
        if tag == b"BTAF":
            btaf = at
        elif tag == b"GMIF":
            gmif = at
        at += size
    if btaf is None or gmif is None:
        raise SystemExit("NARC has no BTAF/GMIF chunk")
    n = struct.unpack_from("<H", blob, btaf + 8)[0]
    spans = [struct.unpack_from("<II", blob, btaf + 12 + i * 8) for i in range(n)]
    return [blob[gmif + 8 + s:gmif + 8 + e] for s, e in spans]


def parse_matrix(b: bytes) -> dict:
    w, h, has_headers, has_altitudes, name_len = b[0], b[1], b[2], b[3], b[4]
    at = 5 + name_len
    n = w * h
    headers = None
    if has_headers:
        flat = struct.unpack_from(f"<{n}H", b, at)
        headers = [list(flat[r * w:(r + 1) * w]) for r in range(h)]
        at += 2 * n
    if has_altitudes:
        at += n
    at += 2 * n
    if at != len(b):
        raise SystemExit(f"matrix {w}x{h}: read {at} of {len(b)} bytes")
    return {"headers": headers}


def locate_header_table(arm9: bytes, matrices: list[dict]) -> int:
    """The address of the map-header table's MATRIX field, found not assumed.

    Constrained on a different file: every map id the region matrix's own
    header plane names has to read `mapMatrixID == 0`. Exactly one address
    survives on each cartridge; anything else is refused.
    """
    import numpy as np

    planes = [i for i, m in enumerate(matrices) if m["headers"]]
    if not planes:
        raise SystemExit("no matrix carries a header plane")
    first: dict[int, int] = {}
    for i in planes:
        for row in matrices[i]["headers"]:
            for v in row:
                if v and v not in first:
                    first[v] = i
    ids = sorted(k for k, v in first.items() if v == planes[0])
    if len(ids) < 20:
        raise SystemExit(f"only {len(ids)} map ids constrain the search; refusing")
    a9 = np.frombuffer(arm9, np.uint8)
    v16 = a9[:-1].astype(np.uint16) | (a9[1:].astype(np.uint16) << 8)
    span = len(v16) - HEADER_STRIDE * (max(ids) + 2)
    if span <= 0:
        raise SystemExit("arm9 is too small to hold the header table")
    ok = np.ones(span, bool)
    for map_id in ids:
        ok &= v16[HEADER_STRIDE * map_id:HEADER_STRIDE * map_id + span] == 0
        if not ok.any():
            raise SystemExit("no address satisfies the matrix-plane constraint")
    hits = []
    for base in np.where(ok)[0]:
        column = v16[base + HEADER_STRIDE * np.arange(max(ids) + 1)]
        if column.max() < len(matrices) and int((column != 0).sum()) >= 100:
            hits.append(int(base))
    if len(hits) != 1:
        raise SystemExit(f"{len(hits)} candidate header tables ({hits[:5]}), refusing to guess")
    return hits[0]


# ---------------------------------------------------------------- the names

def gen4_names(game: Game) -> dict[int, str]:
    rom = Rom(REPO / "roms" / game.rom)
    matrices = [parse_matrix(b) for b in narc(rom.named(game.matrices))]
    arm9 = rom.arm9()
    base = locate_header_table(arm9, matrices)
    _index, strings = dstext.find_text_file(narc(rom.named(game.messages)), game.text_anchors)
    names: dict[int, str] = {}
    map_id = 0
    while True:
        at = base + HEADER_STRIDE * map_id
        if at + game.label_delta >= len(arm9) or at + 1 >= len(arm9):
            break
        matrix = struct.unpack_from("<H", arm9, at)[0]
        label = arm9[at + game.label_delta]
        if matrix >= len(matrices) or label >= len(strings):
            break
        names[map_id] = strings[label].strip()
        map_id += 1
    if len(names) < 100:
        raise SystemExit(f"{game.rom}: only {len(names)} map headers read; refusing")
    return names


def gen5_names(game: Game) -> dict[int, str]:
    rom = Rom(REPO / "roms" / game.rom)
    zones = narc(rom.named(GEN5_ZONE_TABLE))[0]
    if len(zones) % ZONE_STRIDE:
        raise SystemExit(f"{game.rom}: zone table is {len(zones)} bytes, not a multiple of {ZONE_STRIDE}")
    _index, strings = dstext.find_text_file(narc(rom.named(GEN5_MESSAGES)), game.text_anchors)
    names: dict[int, str] = {}
    for zone in range(len(zones) // ZONE_STRIDE):
        label = zones[ZONE_STRIDE * zone + ZONE_LABEL]
        if label >= len(strings):
            raise SystemExit(
                f"{game.rom}: zone {zone} names string {label} of {len(strings)}; "
                "the name-index field is wrong"
            )
        text = strings[label].strip()
        if text and set(text) != {GEN5_BLANK}:
            names[zone] = text
    if len(names) < 100:
        raise SystemExit(f"{game.rom}: only {len(names)} zones named; refusing")
    return names


def names_for(key: str) -> dict[int, str]:
    game = GAMES[key]
    names = gen4_names(game) if game.generation == 4 else gen5_names(game)
    bad = [(m, want, names.get(m)) for m, want in game.checks.items() if names.get(m) != want]
    if bad:
        raise SystemExit(
            f"{key}: the known-answer checks failed — {bad}. The name index is "
            "shifted or the wrong field; refusing to write a table of plausible "
            "wrong names."
        )
    return names


def observed_maps(key: str) -> set[int]:
    """The map ids our runs actually stood on, from the observed graph.

    `maps` in that file is a COUNT, not a list; the ids are the first element
    of each `per_map` entry's `map` key. Reading the count as a list is why
    this returned an empty set — and an empty set makes the coverage check
    below pass vacuously, which is the failure mode this comment exists for.
    """
    path = OBSERVED / f"{key}-observed.json"
    if not path.is_file():
        return set()
    graph = json.loads(path.read_text())
    ids = {int(entry["map"][0]) for entry in graph.get("per_map") or []}
    if not ids:
        raise SystemExit(f"{path.name} names no maps; the coverage check would be vacuous")
    return ids


# ---------------------------------------------------------------- the control

_SYMBOL = re.compile(r"\.mapLabelTextID\s*=\s*LocationNames_Text_(\w+)")


def platinum_control() -> dict:
    """The same method on the one cartridge whose answer is already known.

    pret's Platinum decomp gives every map header a `mapLabelTextID`, spelled
    as a symbol (`LocationNames_Text_TwinleafTown`). This compares that symbol,
    for all 593 maps that have one, against the string this extractor reads out
    of the cartridge. The join from map id to header — that the id our runs
    report is the 0-based line of `generated/map_headers.txt` — is not assumed
    either: a wrong join could not agree 593 times at a fixed stride.

    A symbol and a string can differ in spelling without disagreeing (pret
    writes `TeamGalacticEternaBuilding` where the cartridge prints
    `T.G. Eterna Bldg`), so a mismatch is only a real failure when it breaks
    the symbol <-> index bijection, which is reported separately.
    """
    headers = PRET_CACHE / "include" / "data" / "map_headers.h"
    order_file = PRET_CACHE / "generated" / "map_headers.txt"
    if not (headers.is_file() and order_file.is_file()):
        raise SystemExit(
            f"the Platinum control needs the cached decomp at {PRET_CACHE}; "
            "run scripts/render_dsmaps.py once to populate it"
        )
    order = [ln.strip() for ln in order_file.read_text().splitlines() if ln.strip()]
    index_of = {name: i for i, name in enumerate(order)}
    want: dict[int, str] = {}
    for block in re.finditer(r"\[(MAP_HEADER_\w+)\]\s*=\s*\{(.*?)\n\s*\},", headers.read_text(), re.S):
        symbol = _SYMBOL.search(block.group(2))
        if symbol and block.group(1) in index_of:
            want[index_of[block.group(1)]] = symbol.group(1)

    got = names_for("platinum-us")

    def fold(s: str) -> str:
        s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
        return re.sub(r"[^a-z0-9]", "", s.lower())

    same, spelled_differently, missing = [], [], []
    for map_id, symbol in sorted(want.items()):
        if map_id not in got:
            missing.append((map_id, symbol))
        elif fold(got[map_id]) == fold(symbol):
            same.append(map_id)
        else:
            spelled_differently.append((map_id, symbol, got[map_id]))
    # A symbol must name exactly one string and vice versa, or the two tables
    # really do disagree about which map gets which name.
    by_symbol: dict[str, set[str]] = {}
    by_string: dict[str, set[str]] = {}
    for map_id, symbol in want.items():
        if map_id in got:
            by_symbol.setdefault(symbol, set()).add(got[map_id])
            by_string.setdefault(got[map_id], set()).add(symbol)
    broken = ([s for s, v in by_symbol.items() if len(v) > 1]
              + [s for s, v in by_string.items() if len(v) > 1])
    return {
        "labelled_headers": len(want),
        "identical": len(same),
        "same_slot_different_spelling": spelled_differently,
        "missing_from_rom_table": missing,
        "bijection_broken_for": broken,
        "passed": not missing and not broken,
    }


# ---------------------------------------------------------------- main

def build() -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for key in SHIPPED:
        names = names_for(key)
        want = observed_maps(key)
        uncovered = sorted(want - set(names))
        if uncovered:
            raise SystemExit(
                f"{key}: our runs entered {uncovered} and the table does not name them; "
                "refusing to write a partial table"
            )
        out[key] = {str(m): names[m] for m in sorted(names)}
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--control", action="store_true",
                    help="run the method on Platinum and compare with the decomp")
    ap.add_argument("--print", dest="show", metavar="GAME",
                    help="print one game's table instead of writing the file")
    args = ap.parse_args()

    if args.control:
        result = platinum_control()
        print(f"Platinum control: {result['identical']} of {result['labelled_headers']} "
              f"decomp-labelled maps read the identical name from the cartridge")
        for map_id, symbol, text in result["same_slot_different_spelling"]:
            print(f"  map {map_id}: decomp calls it {symbol}, the cartridge prints {text!r}")
        if result["missing_from_rom_table"]:
            print(f"  MISSING from the ROM table: {result['missing_from_rom_table']}")
        if result["bijection_broken_for"]:
            print(f"  DISAGREEMENT (one symbol, two strings): {result['bijection_broken_for']}")
        print("  PASS" if result["passed"] else "  FAIL")
        raise SystemExit(0 if result["passed"] else 1)

    if args.show:
        for map_id, name in sorted(names_for(args.show).items()):
            print(f"{map_id:5d}  {name}")
        return

    table = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(table, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {OUT.relative_to(REPO)}: "
          + ", ".join(f"{k} {len(v)}" for k, v in table.items()))


if __name__ == "__main__":
    main()
