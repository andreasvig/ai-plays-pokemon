#!/usr/bin/env python3
"""Map artwork for the DS cartridges, in two tiers over one frame.

TIER 2, WHICH IS WHAT SHIPS: the map as the game draws it. Gen 4's overworld is
a textured 3D model (`BMD0`, sitting in the same `map_data_NNN.bin` as the
collision this also reads) plus a list of building placements, and `ds3d/`
reads, decodes and rasterises all of it in pure Python — no GL, no apicula, no
Blender. The camera is the field camera's OWN PITCH, orthographic (`--camera`,
default `pitched`; `ds3d/camera.py` has the projection and the numbers).
Straight down was the first answer and Andreas rejected it — *"both teh gen 4
and 5 views are actually really bad, bith are top down whcih dsont feel
right"*. An angled camera z-buffers, so a building hides the route behind it;
measured over our own runs that is 2.8% of walked tiles, 0.6-3.8% outdoors.

TIER 1, WHICH REMAINS THE FALLBACK (`--render collision`): the map's
SILHOUETTE, one flat block per tile, coloured by what the cartridge says is
there — ground, tall grass, water, or a wall you cannot cross. Every map the 3D
path cannot render can still ship this, and SoulSilver ships it today.

Both tiers produce the SAME window in the same frame, from the same matrix
registration, so a route drawn over one lands identically on the other.

WHERE THE DATA COMES FROM. Two sources with one shape. Platinum needs no ROM and
no NARC reader: pret's decomp serves every byte over plain HTTP.

  res/field/maps/data/map_data_NNN.bin   one 32x32 block: collision, the
                                         building placements, and the terrain
                                         model, in four declared sections
  res/field/matrices/map_matrix_NNN.json which blocks make up which map
  generated/map_headers.txt              the map id our runs report, by line
  include/data/map_headers.h             each map's matrix, indoor-ness, and
                                         its `areaDataArchiveID`
  res/field/area_data/area_data_NNN.json which texture set and which prop set
  res/field/maps/texture_sets/*.nsbtx    the terrain textures
  res/field/props/{models,texture_sets}  the buildings and theirs
  res/field/events/events_*.json         the doors, in the same coordinates

The area-data chain is documented where it is implemented, in `ds3d/field.py`.

SoulSilver has no decomp of that shape, so it is read out of our own cartridge
through the NDS filesystem and Nintendo's NARC archive — `a/0/6/5` for the land
blocks, `a/0/4/1` for the matrices. Same rules, one difference in the bytes, and
one thing the cartridge does not say at all. See the long note above `RomDecomp`.

The collision grid's layout is not reverse-engineered, it is named by the
decomp itself (`include/constants/field/map.h`):

  TERRAIN_ATTRIBUTES_OFFSET 0x10    16 bytes of header, then
  TERRAIN_ATTRIBUTES_SIZE   0x800   2048 bytes = 1024 u16, one per tile, 32x32
  TERRAIN_ATTRIBUTES_COLLISION_MASK 0x8000   set = you cannot stand here
  TERRAIN_ATTRIBUTES_TILE_BEHAVIOR_MASK 0xFF what the tile IS (enum TileBehavior)

REGISTRATION. A global tile (X, Y) is in matrix cell (X>>5, Y>>5) at local
(X&31, Y&31), and the world pixel origin is global tile (0, 0). `--check`
proves both halves against our own runs before anything is rendered:

  1. every position sample lands in a matrix cell whose header IS the map the
     sample says it is on;
  2. every tile the player stood on is walkable in that cell's collision grid.

THE PNG IS MAP-LOCAL, AND SAYS SO. `RouteMap.svelte` draws a map with

    c.drawImage(img, (win.x - png.x) * TPX, (win.y - png.y) * TPX, ...)
    ... at screenAt(p.x, p.y)                                // destination

while placing a route tile at `p.x + x - win.x`. `png` is `pngOrigin(m)`, the
tile PNG pixel (0, 0) holds, and it defaults to `[0, 0]` — which is every gen
1-3 atlas unchanged, because for them the map's corner IS the route's origin.

Gen 4 outdoor coordinates are GLOBAL: Platinum's y reaches 888. An atlas that
left `png_origin` at zero would have to pad every PNG out to route tile (0, 0),
and at 16 px/tile Sandgem Town alone becomes 3072 x 13824 px — 42 megapixels,
170 MB decoded, over iOS Safari's decode ceiling, and it fails the way
everything in this file fails: the image never decodes, the map draws as
nothing, and nothing raises. So the DS atlases declare `png_frame: "map-local"`
and ship the map, 512 x 512 for one chunk.

`tile_px` is 16 for the 3D tier — the DS's own pixel density, one world unit
per pixel, which is the size the artwork exists at — and 4 for the collision
tier, where one flat block per tile has no sub-tile detail to lose. The viewer
takes the number from the atlas (`tilePxOf`), never from the route.

Usage:
    ./venv/bin/python scripts/render_dsmaps.py --check          # registration only
    ./venv/bin/python scripts/render_dsmaps.py --game platinum-us
    ./venv/bin/python scripts/render_dsmaps.py --game soulsilver-us --render collision
    ./venv/bin/python scripts/render_dsmaps.py --camera topdown   # the old atlas
    ./venv/bin/python scripts/render_dsmaps.py --sheet          # the artifacts/ proof sheet
"""

from __future__ import annotations

import argparse
import json
import os
import re
import struct
import subprocess
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Iterator, Optional

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ds3d import blz as ds3d_blz                                 # noqa: E402
from ds3d import camera as ds3d_camera                           # noqa: E402
from ds3d import field as ds3d_field                             # noqa: E402
from ds3d import scene as ds3d_scene                             # noqa: E402
from ds3d import mapnames                                       # noqa: E402
from ds3d import pngout                                         # noqa: E402

REPO = Path(__file__).resolve().parents[1]
PRET = "pret/pokeplatinum"
# The branch the sha is resolved FROM, once, when the cache is cold. Every fetch
# after that pins the sha — see `pret_sha`.
PRET_BRANCH = "main"
CACHE = REPO / "local" / "pret-cache-platinum"
MAPS_ROOT = REPO / "src" / "dashboard" / "web" / "public" / "maps"
OBSERVED = REPO / "artifacts" / "game-map-render" / "observed"
RUNS = REPO / "local" / "runs"
SHEETS = REPO / "artifacts" / "game-map-render"

# Pixels per tile, per tier. 16 is not a choice: it is `MAP_OBJECT_TILE_SIZE`,
# the DS's own world units per tile, so the 3D tier renders at 1 px per world
# unit and no texel is invented or thrown away. The collision tier is one flat
# block per tile and 4 loses nothing.
TILE_PX = {"3d": 16, "collision": 4}
DEFAULT_RENDER = "3d"
# What the atlas's `render` field says, per tier, and what its `map_set.note`
# says. A consumer reads `render` to know whether the pixels mean anything:
# "collision" is a claim about walkability, "3d-ortho" is a picture.
RENDER_KIND = {"3d": "3d-ortho", "collision": "collision"}
# Andreas, 2026-09-21: "for gen 4 we shoudl defenlty not haev top down view,
# pelase make it teh real game like view." See `ds3d/camera.py`.
DEFAULT_CAMERA = "pitched"
CAMERA_CAPTION = {
    "topdown": "straight-down orthographic",
    "pitched": ("orthographic at the field camera's own pitch "
                f"({ds3d_camera.PITCHED.pitch_deg:.2f} deg)"),
}
RENDER_NOTE = {
    "3d-ortho": ("the cartridge's own textured 3D field artwork, rendered straight "
                 "down and orthographic at one pixel per world unit"),
    "3d-pitched": ("the cartridge's own textured 3D field artwork, under the field "
                   "camera's own pitch, orthographic at one pixel per world unit"),
    "collision": "collision silhouettes, not a tile render",
}
# The tier says what the pixels MEAN; the camera says where a tile is in them.
# Both go in the atlas's `render` string because a consumer that reads
# "3d-ortho" and gets a pitched picture places every route tile wrong.
RENDER_KIND_FOR = {("3d", "topdown"): "3d-ortho", ("3d", "pitched"): "3d-pitched",
                   ("collision", "topdown"): "collision",
                   ("collision", "pitched"): "collision"}
# Supersampling for the 3D tier. 3x is where the diagonal edge of a roof stops
# stair-stepping; the cost is ~1 s per 32x32 chunk.
SUPERSAMPLE = 3
CELL = 32                      # MAP_TILES_COUNT_X / _Z
ATTR_OFFSET = 0x10             # TERRAIN_ATTRIBUTES_OFFSET
ATTR_SIZE = 0x800              # TERRAIN_ATTRIBUTES_SIZE
COLLISION_MASK = 0x8000        # TERRAIN_ATTRIBUTES_COLLISION_MASK
# `raster.draw_tri`'s own alpha test. A tile whose mean alpha is under it
# holds no pixel this renderer wrote, which is the one thing `check_artwork`
# calls a hole.
DRAWN_ALPHA = 8


def runs_of(mask: np.ndarray) -> list[int]:
    """Sizes of the 4-connected components of a boolean tile grid, largest first.

    WHY A RUN AND NOT A COUNT. A scattered see-through tile is a texel on an
    edge; a see-through REGION is a hole you can see the page through, and the
    two are the same number. Verity Lakefront's 8x8 puddle reached the browser
    with every test green because the check only asked how many tiles were
    see-through and what the cartridge called them — 56 tiles named
    `TILE_BEHAVIOR_PUDDLE`, which is as true of one speck as of one square.
    """
    seen = np.zeros(mask.shape, bool)
    sizes = []
    for sy, sx in zip(*np.where(mask)):
        if seen[sy, sx]:
            continue
        stack, n = [(sy, sx)], 0
        seen[sy, sx] = True
        while stack:
            y, x = stack.pop()
            n += 1
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                ny, nx = y + dy, x + dx
                if (0 <= ny < mask.shape[0] and 0 <= nx < mask.shape[1]
                        and mask[ny, nx] and not seen[ny, nx]):
                    seen[ny, nx] = True
                    stack.append((ny, nx))
        sizes.append(n)
    return sorted(sizes, reverse=True)
BEHAVIOR_MASK = 0xFF           # TERRAIN_ATTRIBUTES_TILE_BEHAVIOR_MASK

# The four tones. A wall is the darkest thing on the map so a bright route line
# reads across it; ground is light enough to see the shape of a town at a
# glance. `VOID` is transparent, not black: a matrix cell no map claims is
# genuinely nothing, and painting it would invent a border.
GROUND = (74, 84, 100, 255)
GRASS = (66, 104, 74, 255)
WATER = (58, 82, 118, 255)
WALL = (27, 31, 39, 255)
VOID = (0, 0, 0, 0)


# ---------------------------------------------------------------- fetching
#
# tmp-then-os.replace and a PINNED sha, both for the reasons
# scripts/build_walkgraph.py records: a concurrent reader must never see a
# half-written .bin, and a file fetched from `main` today beside a provenance
# string written months ago is provenance that cannot be checked.

def cache_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def pret_sha(*, offline: bool = False) -> str:
    """The commit this cache is of — a full 40-hex sha or nothing at all."""
    marker = CACHE / "SHA"
    if marker.exists():
        sha = marker.read_text().strip()
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise SystemExit(f"{marker} holds {sha!r}, which is not a 40-hex commit")
        return sha
    if offline:
        raise SystemExit(f"--offline and {marker} is absent: nothing says which commit this is")
    proc = subprocess.run(["gh", "api", f"repos/{PRET}/commits/{PRET_BRANCH}", "--jq", ".sha"],
                          capture_output=True, text=True)
    sha = proc.stdout.strip() if proc.returncode == 0 else ""
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise SystemExit(f"could not resolve {PRET}@{PRET_BRANCH} to a commit (got {sha!r})")
    cache_write(marker, sha.encode())
    return sha


def fetch(path: str, *, offline: bool = False) -> bytes:
    local = CACHE / path
    if local.exists():
        return local.read_bytes()
    if offline:
        raise SystemExit(f"--offline but {path} is not cached")
    req = urllib.request.Request(
        f"https://raw.githubusercontent.com/{PRET}/{pret_sha(offline=offline)}/{path}",
        headers={"User-Agent": "pokebench-dsmaps/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
    except urllib.error.URLError as exc:
        raise SystemExit(f"fetch {path} failed: {exc}") from exc
    cache_write(local, data)
    return data


def fetch_json(path: str, *, offline: bool = False) -> dict:
    return json.loads(fetch(path, offline=offline))


# ---------------------------------------------------------------- the decomp

def header_names(*, offline: bool = False) -> list[str]:
    """`MAP_HEADER_*` in id order. The id our runs report IS the 0-based index."""
    return [ln.strip() for ln in
            fetch("generated/map_headers.txt", offline=offline).decode().splitlines() if ln.strip()]


_FIELD = re.compile(r"\.(\w+)\s*=\s*([A-Za-z0-9_]+)")


def header_meta(*, offline: bool = False) -> dict[str, dict]:
    """Each map header's own record: which matrix places it and what kind it is.

    Parsed out of the initialiser rather than guessed, because `mapMatrixID` is
    the whole placement story — an outdoor map names the shared overworld matrix
    and gets global coordinates, an interior names a 1x1 matrix of its own and
    gets coordinates that start at zero.
    """
    text = fetch("include/data/map_headers.h", offline=offline).decode()
    out: dict[str, dict] = {}
    for block in re.finditer(r"\[(MAP_HEADER_\w+)\]\s*=\s*\{(.*?)\n\s*\},", text, re.S):
        out[block.group(1)] = dict(_FIELD.findall(block.group(2)))
    return out


def tile_behaviors(*, offline: bool = False) -> list[str]:
    """`enum TileBehavior` as a list indexed by value."""
    text = fetch("include/constants/field/map_tile_behaviors.h", offline=offline).decode()
    body = re.search(r"enum TileBehavior\s*\{(.*?)\}", text, re.S)
    if not body:
        raise SystemExit("map_tile_behaviors.h has no enum TileBehavior")
    names: list[str] = []
    for item in body.group(1).split(","):
        item = re.sub(r"//.*", "", item).strip()
        if not item:
            continue
        name, _, value = item.partition("=")
        idx = int(value.strip(), 0) if value.strip() else len(names)
        while len(names) < idx:
            names.append(f"TILE_BEHAVIOR_GAP_{len(names):02X}")
        names.append(name.strip())
    return names


FLOOR = re.compile(r"^(B?\d+F|ROOF|BASEMENT)$")


def camel(words: list[str]) -> str:
    """['TWINLEAF', 'TOWN'] -> 'TwinleafTown'; '1F' and '2F' are left alone."""
    return "".join(w if w[:1].isdigit() or FLOOR.fullmatch(w) else w.capitalize() for w in words)


def pretty_name(header: str, parents: set[str]) -> tuple[str, Optional[str]]:
    """`(name, building)` in the shape the viewer's label functions expect.

    They were written for pret's gen-3 names — `PewterCity_PokemonCenter_1F` —
    and `buildingLabel` drops the first underscore-separated part as the town.
    A DS header is `MAP_HEADER_SANDGEM_TOWN_POKEMON_RESEARCH_LAB`, so fed in raw
    it captions a building "TOWN POKEMON RESEARCH LAB". The split point is not
    guessed: an interior's header name STARTS with the header name of the map
    its door is on, and that map is one we rendered.
    """
    if not header.startswith("MAP_HEADER_"):
        # A cartridge ships map ids, not map names. Inventing "Map33" would be a
        # caption that reads like data; the lattice already renders `name: null`.
        return None, None
    base = header.removeprefix("MAP_HEADER_")
    outer = max((p for p in parents if base.startswith(p + "_")), key=len, default=None)
    if outer is None:
        return camel(base.split("_")), None
    rest = base[len(outer) + 1:].split("_")
    floor = rest.pop() if len(rest) > 1 and FLOOR.fullmatch(rest[-1]) else None
    building = f"{camel(outer.split('_'))}_{camel(rest)}"
    return (f"{building}_{floor}" if floor else building), building


def behavior_tone(name: str) -> tuple[int, int, int, int]:
    """One of the three walkable tones, from the decomp's own name for the tile.

    Keyed on the NAME and not on a hand-written value list: the enum is 200-odd
    entries long and a table of numbers copied out of it would rot the first
    time pret renumbers one. Everything that is not named grass or water is
    ground, which is the honest default — `TILE_BEHAVIOR_NONE` is most of the
    world.
    """
    if "GRASS" in name:
        return GRASS
    if any(k in name for k in ("WATER", "WATERFALL", "PUDDLE", "SWAMP", "MARSH")):
        return WATER
    return GROUND


class Decomp:
    """The decomp, read once and held. Every lookup below is a dict hit."""

    def __init__(self, *, offline: bool = False) -> None:
        self.offline = offline
        self.sha = pret_sha(offline=offline)
        self.names = header_names(offline=offline)
        self.index = {n: i for i, n in enumerate(self.names)}
        self.meta = header_meta(offline=offline)
        self.behaviors = tile_behaviors(offline=offline)
        self._matrix: dict[str, dict] = {}
        self._grid: dict[str, np.ndarray] = {}

    # -- matrices -----------------------------------------------------------
    def matrix(self, const: str) -> dict:
        if const not in self._matrix:
            self._matrix[const] = fetch_json(f"res/field/matrices/{const}.json", offline=self.offline)
        return self._matrix[const]

    def matrix_of(self, header: str) -> dict:
        return self.matrix(self.meta[header]["mapMatrixID"])

    def cells_of(self, header: str) -> list[tuple[int, int]]:
        """The matrix cells this map occupies, as (col, row).

        A matrix with an EMPTY `headers` list is a map's own matrix — every cell
        of it is that map. A matrix with one belongs to a region, and the cells
        are the ones that name this header.
        """
        m = self.matrix_of(header)
        rows = m["maps"]
        if m.get("headers"):
            return [(c, r) for r, row in enumerate(m["headers"])
                    for c, v in enumerate(row) if v == header]
        return [(c, r) for r, row in enumerate(rows) for c in range(len(row))]

    def header_at(self, const: str, col: int, row: int) -> Optional[str]:
        m = self.matrix(const)
        heads = m.get("headers")
        if not heads:
            return None
        if not (0 <= row < len(heads) and 0 <= col < len(heads[row])):
            return None
        return heads[row][col]

    # -- collision ----------------------------------------------------------
    def grid(self, land: str) -> Optional[np.ndarray]:
        """One 32x32 block of terrain attributes, as u16. None for MAP_NONE."""
        if not land.startswith("MAP_") or land == "MAP_NONE":
            return None
        n = land.split("_")[-1]
        if not n.isdigit():
            return None
        if land not in self._grid:
            raw = fetch(f"res/field/maps/data/map_data_{int(n):03d}.bin", offline=self.offline)
            # Same one splitter the artwork tier reads its model out of — see
            # the note on `RomDecomp.grid`.
            attrs = ds3d_field.land_block(raw).attrs
            if len(attrs) != ATTR_SIZE:
                raise SystemExit(
                    f"map_data_{n}: terrain attributes are {len(attrs)} bytes, not {ATTR_SIZE}")
            self._grid[land] = np.frombuffer(attrs, dtype="<u2").reshape(CELL, CELL)
        return self._grid[land]

    def cell_grid(self, header: str, col: int, row: int) -> Optional[np.ndarray]:
        m = self.matrix_of(header)
        if not (0 <= row < len(m["maps"]) and 0 <= col < len(m["maps"][row])):
            return None
        return self.grid(m["maps"][row][col])

    def warps(self, header: str) -> list[dict]:
        """`warp_events` for this map: `{x, z, dest_header_id}` in ITS coordinates."""
        stem = header.removeprefix("MAP_HEADER_").lower()
        try:
            data = fetch_json(f"res/field/events/events_{stem}.json", offline=self.offline)
        except SystemExit:
            return []
        return data.get("warp_events") or []


# ------------------------------------------------- SoulSilver, out of the cart
#
# There is no public decomp of pokeplatinum's shape for HGSS, so SoulSilver's
# data comes out of our own cartridge. Two format layers and nothing else: the
# NDS filesystem (FAT + FNT in the ROM header) and Nintendo's NARC archive.
#
# EVERY STRUCTURE BELOW WAS CONFIRMED AGAINST THE CARTRIDGE, not assumed from
# Platinum:
#   a/0/6/5   676 land blocks, and all 676 of them declare 0x800 bytes of
#             terrain attributes in their own first word — byte-identical
#             framing to Platinum's map_data_NNN.bin.
#   a/0/4/1   288 map matrices. The first is `width=47 height=17 hasHeaders=1
#             hasAltitudes=1 name="map"`, and 5 + 3 + 47*17*(2+1+2) is exactly
#             its 4003 bytes, so the layout is checked by arithmetic and not by
#             eye.
#
# THE MAP-HEADER TABLE, which was the thing missing. It names a map's matrix
# and its artwork, and in HGSS it lives in the arm9 — which the cartridge stores
# BLZ-compressed. An earlier search for it over the raw ROM found no candidate;
# that was a true result about the compressed bytes and not about the table.
# Decompressed (`ds3d/blz.py`) it is there, at a 24-byte stride, and
# `_locate_headers` finds it rather than hard-coding an address:
#
#   u8  areaDataArchiveID   at record +0     (< 106, the size of a/0/4/2)
#   u16 mapMatrixID            +3             (unaligned, so it is a packed struct)
#
# The search constrains on a fact from a DIFFERENT file: every map id the
# region matrix's own header plane names must read matrix 0. That is 75
# simultaneous equations and it leaves exactly one address, which is then
# checked both ways — see `_locate_headers` and `check_headers`.
#
# It resolves the three interiors that used to fall back to a lattice
# (61 -> matrix 100 "m_labo01_", 63/64 -> 71/72 "m_hh0101_"/"m_hh0102_"), and
# it is what lets SoulSilver render its buildings at all.

class NdsRom:
    """The ROM's own filesystem. Files by id, names when the FNT has them."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.raw = path.read_bytes()
        self.fnt, _, self.fat, fat_size = struct.unpack_from("<IIII", self.raw, 0x40)
        self.count = fat_size // 8

    def file(self, fid: int) -> bytes:
        start, end = struct.unpack_from("<II", self.raw, self.fat + fid * 8)
        return self.raw[start:end]

    def names(self) -> dict[str, int]:
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

        walk(0xF000, "")
        return out

    @staticmethod
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


class RomDecomp:
    """The same interface `Decomp` offers, read out of a cartridge.

    Deliberately duck-typed rather than a shared base class: everything from
    `render_map` down is written against a handful of lookups, and the two
    sources have nothing else in common — one is HTTP and JSON, the other is a
    filesystem inside a 134 MB binary.
    """

    LAND = "a/0/6/5"
    MATRICES = "a/0/4/1"
    AREA_DATA = "a/0/4/2"
    HEADER_STRIDE = 24              # sizeof(MapHeader)
    AREA_FIELD = 0                  # u8  areaDataArchiveID, within the record
    MATRIX_FIELD = 3                # u16 mapMatrixID, within the record

    def __init__(self, rom_path: Path) -> None:
        self.rom = NdsRom(rom_path)
        names = self.rom.names()
        for key in (self.LAND, self.MATRICES):
            if key not in names:
                raise SystemExit(f"{rom_path.name} has no {key}")
        self.sha = _sha1(self.rom.raw)
        self.land = NdsRom.narc(self.rom.file(names[self.LAND]))
        self._matrices = [self._parse_matrix(b) for b in NdsRom.narc(self.rom.file(names[self.MATRICES]))]
        # A map id appears by id in a matrix's header plane — true for an
        # OUTDOOR map only, and the whole story before the arm9 table was read.
        # Kept because it is the independent source `check_headers` measures the
        # table against.
        self.owner: dict[int, int] = {}
        for i, m in enumerate(self._matrices):
            for row in (m["headers"] or []):
                for v in row:
                    self.owner.setdefault(v, i)
        self.n_areas = len(NdsRom.narc(self.rom.file(names[self.AREA_DATA]))) \
            if self.AREA_DATA in names else 0
        self.headers = self._read_headers()
        # No tile-behaviour enum is published for HGSS, so nothing here claims
        # to know grass from gravel: `behavior_tone` is never consulted and the
        # silhouette is two tones, walkable and wall.
        self.behaviors: list[str] = []
        self.names = _NumericNames()
        self.meta = _MatrixMeta(self)

    @staticmethod
    def _parse_matrix(b: bytes) -> dict:
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
        flat = struct.unpack_from(f"<{n}H", b, at)
        at += 2 * n
        if at != len(b):
            raise SystemExit(f"matrix {name_len=} {w}x{h}: read {at} of {len(b)} bytes")
        return {"name": b[5:5 + name_len].decode("ascii", "replace"), "headers": headers,
                "maps": [list(flat[r * w:(r + 1) * w]) for r in range(h)]}

    # -- the map-header table ----------------------------------------------
    def arm9(self) -> bytes:
        """The arm9, decompressed. See the note above this class."""
        if getattr(self, "_arm9", None) is None:
            off, _entry, _ram, size = struct.unpack_from("<4I", self.rom.raw, 0x20)
            self._arm9 = ds3d_blz.decode(self.rom.raw[off:off + size])
        return self._arm9

    def _locate_headers(self) -> Optional[int]:
        """The table's address, FOUND rather than hard-coded.

        Constrained on a different file: every map id the region matrix's own
        header plane names has to read `mapMatrixID == 0`. Seventy-five
        simultaneous equations leave one address on this cartridge; anything
        other than exactly one is refused, because a table located by a
        coincidence would place every interior somewhere plausible and wrong.
        """
        planes = [i for i, m in enumerate(self._matrices) if m["headers"]]
        want: dict[int, int] = {}
        for i in planes:
            for row in self._matrices[i]["headers"]:
                for v in row:
                    # id 0 is the filler a header plane uses for "no map here",
                    # and it appears in both planes — it constrains nothing.
                    if v and v not in want:
                        want[v] = i
        ids = sorted(k for k, v in want.items() if v == planes[0])
        if len(ids) < 20:
            return None
        a9 = np.frombuffer(self.arm9(), np.uint8)
        v16 = a9[:-1].astype(np.uint16) | (a9[1:].astype(np.uint16) << 8)
        span = len(v16) - self.HEADER_STRIDE * (max(ids) + 2)
        if span <= 0:
            return None
        ok = np.ones(span, bool)
        for map_id in ids:
            at = self.HEADER_STRIDE * map_id
            ok &= v16[at:at + span] == 0
            if not ok.any():
                return None
        hits = []
        for base in np.where(ok)[0]:
            col = v16[base + self.HEADER_STRIDE * np.arange(max(ids) + 1)]
            if col.max() < len(self._matrices) and int((col != 0).sum()) >= 100:
                hits.append(int(base))
        if len(hits) != 1:
            return None
        return hits[0] - self.MATRIX_FIELD

    def _read_headers(self) -> dict[int, dict]:
        """`{map_id: {"mapMatrixID": n, "areaDataArchiveID": n}}`, or empty."""
        base = self._locate_headers()
        self.header_base = base
        if base is None:
            return {}
        a9 = self.arm9()
        out: dict[int, dict] = {}
        for map_id in range((len(a9) - base) // self.HEADER_STRIDE):
            at = base + self.HEADER_STRIDE * map_id
            matrix = struct.unpack_from("<H", a9, at + self.MATRIX_FIELD)[0]
            area = a9[at + self.AREA_FIELD]
            if matrix >= len(self._matrices) or (self.n_areas and area >= self.n_areas):
                break
            out[map_id] = {"mapMatrixID": matrix, "areaDataArchiveID": area}
        return out

    def check_headers(self) -> dict:
        """The table against the matrices, which is a different file.

        Every map id a header plane names must read that plane's own matrix.
        This is the check that makes the located address trustworthy: it is 75
        agreements the search did not ask for on the SECOND field it reports.
        """
        out = {"total": 0, "bad": [], "located": self.header_base}
        for i, m in enumerate(self._matrices):
            for row in (m["headers"] or []):
                for v in row:
                    if not v or v not in self.headers:
                        continue
                    out["total"] += 1
                    if self.headers[v]["mapMatrixID"] != i:
                        out["bad"].append((v, self.headers[v]["mapMatrixID"], i))
        return out

    # -- the Decomp interface ----------------------------------------------
    def matrix(self, const: str) -> dict:
        return self._matrices[int(const.rsplit("_", 1)[1])]

    def matrix_of(self, header: str) -> dict:
        return self.matrix(self.meta[header]["mapMatrixID"])

    def cells_of(self, header: str) -> list[tuple[int, int]]:
        """The same rule `Decomp.cells_of` uses, and for the same reason.

        A matrix with no header plane is a map's OWN matrix — an interior's 1x1
        — so every cell of it is that map. Before the arm9 table this branch was
        unreachable, because a map with a private matrix could not be found at
        all.
        """
        m = self.matrix_of(header)
        want = int(header.rsplit("_", 1)[1])
        if m["headers"]:
            return [(c, r) for r, row in enumerate(m["headers"])
                    for c, v in enumerate(row) if v == want]
        return [(c, r) for r, row in enumerate(m["maps"]) for c in range(len(row))]

    def header_at(self, const: str, col: int, row: int) -> Optional[str]:
        heads = self.matrix(const)["headers"]
        if not heads or not (0 <= row < len(heads) and 0 <= col < len(heads[row])):
            return None
        return f"MAP_{heads[row][col]}"

    def grid(self, land: int) -> Optional[np.ndarray]:
        """One 32x32 block of terrain attributes, at the offset the block states.

        THE ONE PLACE HGSS IS NOT PLATINUM. Platinum puts its sections at a
        constant 0x10. SoulSilver writes a `0x1234` marker there, then a u16
        giving the length of a variable-size section, and only then the 0x800 of
        attributes — so the offset is per block, and reading it as a constant
        shifts the grid by two tiles on every block that has one.

        THE SPLIT IS NOT DONE TWICE. `ds3d.field.land_block` decides which
        layout a block is in, by arithmetic, and the artwork tier reads its
        model and its placements out of the same call. Computing the offset
        separately here would let the collision and the artwork disagree about
        where a map is while each stayed internally consistent — which is a map
        that looks right, and a route that stands in a wall.
        """
        if land is None or land >= len(self.land):
            return None
        block = ds3d_field.land_block(self.land[land])
        if len(block.attrs) != ATTR_SIZE:
            raise SystemExit(f"land block {land}: {len(block.attrs)} attribute bytes")
        return np.frombuffer(block.attrs, dtype="<u2").reshape(CELL, CELL)

    def cell_grid(self, header: str, col: int, row: int) -> Optional[np.ndarray]:
        rows = self.matrix_of(header)["maps"]
        if not (0 <= row < len(rows) and 0 <= col < len(rows[row])):
            return None
        return self.grid(rows[row][col])

    def warps(self, header: str) -> list[dict]:
        """The cartridge's event archive is not read, so the doors come from
        somewhere else — see `observed_doors`. This stays empty rather than
        guessing, and the atlas says which source it used."""
        return []

    def resolvable(self, map_id: int) -> bool:
        return map_id in self.headers or map_id in self.owner


class _NumericNames:
    """`names[33]` -> `"MAP_33"`. A cartridge ships no header names."""

    def __getitem__(self, i: int) -> str:
        return f"MAP_{int(i)}"


class _MatrixMeta:
    """`meta["MAP_33"]` -> what the cartridge's own map-header table says.

    The header table is the answer when it could be located; the header-plane
    search is the fallback, and it can only ever place an OUTDOOR map — an
    interior appears in no plane at all. Which of the two answered is not left
    implicit: `mapSource` says so, and the CLI prints it.
    """

    def __init__(self, src: "RomDecomp") -> None:
        self.src = src

    def __getitem__(self, header: str) -> dict:
        map_id = int(header.rsplit("_", 1)[1])
        rec = self.src.headers.get(map_id)
        if rec is not None:
            const = f"matrix_{rec['mapMatrixID']:03d}"
            # INDOORS, from the shape of the map's own matrix. A cartridge
            # publishes no `MAP_TYPE_*` enum, and this is not a substitute for
            # one: a map drawn on the REGION matrix is somewhere in the
            # overworld and a map with a private matrix of its own is not, which
            # is the distinction the viewer spends (global coordinates, a world
            # frame, a popup) and the same one `Field3D.prop_archive` spends.
            indoor = not self.src.matrix(const).get("headers")
            return {"mapMatrixID": const,
                    "areaDataArchiveID": rec["areaDataArchiveID"],
                    "mapType": "MAP_TYPE_INDOORS" if indoor else None,
                    "mapSource": "arm9"}
        owner = self.src.owner.get(map_id)
        if owner is None:
            raise KeyError(
                f"{header} is in no matrix header plane and no map-header table could be "
                "located, so nothing in the ROM says where it is "
                "(see the note above RomDecomp)")
        return {"mapMatrixID": f"matrix_{owner:03d}", "mapType": None,
                "mapSource": "header-plane"}

    def get(self, header: str, default=None):
        try:
            return self[header]
        except KeyError:
            return default


def _sha1(data: bytes) -> str:
    import hashlib

    return hashlib.sha1(data).hexdigest()


ROMS = {"soulsilver-us": REPO / "roms" / "Pokemon - SoulSilver Version (Europe).nds"}


def source_for(game: str, *, offline: bool = False):
    if game in ROMS:
        return RomDecomp(ROMS[game])
    return Decomp(offline=offline)


# ---------------------------------------------------------------- our runs

def run_dirs(game: str) -> list[Path]:
    """Run folders for a game, by the config name in the folder name.

    `artifacts/game-map-render/observed/<game>-observed.json` lists exactly the
    ones the observed graph was built from; that list wins when it is there, so
    the checks below and the map set the atlas ships cannot drift apart.
    """
    obs = OBSERVED / f"{game}-observed.json"
    if obs.is_file():
        named = json.loads(obs.read_text()).get("runs") or []
        dirs = [RUNS / n for n in named if (RUNS / n).is_dir()]
        if dirs:
            return dirs
    token = game.split("-")[0]
    return sorted(p for p in RUNS.glob(f"*config-v2-{token}*") if p.is_dir())


def run_samples(run: Path) -> Iterator[tuple[int, int, int]]:
    """Every position sample in one run, as `(map_id, x, y)`."""
    events = run / "events.jsonl"
    if not events.is_file():
        return
    for line in events.read_text(errors="replace").splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("type") != "turn_input_trace":
            continue
        for s in e.get("samples") or []:
            if s.get("map_id") is None or s.get("x") is None or s.get("y") is None:
                continue
            yield int(s["map_id"]), int(s["x"]), int(s["y"])


def placeable(d, ids: list[int]) -> tuple[list[int], list[int]]:
    """`(rendered, skipped)`. A map with no matrix cannot be placed at all."""
    ok, no = [], []
    for map_id in ids:
        (ok if d.meta.get(d.names[map_id]) else no).append(map_id)
    return ok, no


def observed_maps(game: str) -> list[int]:
    """The map ids our runs entered.

    The observed graph plus anything the SAMPLES name that it does not. The
    graph is built from edges, so a map the run stood on for one frame and left
    — Platinum's Route 219, one sample on the way out of Sandgem — has no edge
    and is not in it, while `route.json` still keys a visit on it. An atlas
    missing a key the route carries is not an error anywhere: the map silently
    falls back to a lattice. The union is the set that cannot do that.
    """
    obs = json.loads((OBSERVED / f"{game}-observed.json").read_text())
    ids = {int(n.split("|")[0]) for edge in obs["edges"] for n in edge[:2]}
    for run in run_dirs(game):
        ids |= {map_id for map_id, _, _ in run_samples(run)}
    return sorted(ids)


# ---------------------------------------------------------------- the two checks

def check_registration(d, game: str = "platinum-us") -> dict:
    """The two free checks, before a single pixel is rendered.

    They are free because they need no rendering and no ROM, and they settle the
    only assumption everything downstream rests on. If either fails the
    silhouettes would be drawn in the wrong place and look plausible doing it.

    THE DOOR CLASS. Check 2 has one honest exception, and it is enumerated
    rather than tolerated. A tile with `TILE_BEHAVIOR_DOOR` carries the
    collision bit — you never WALK onto a door, the field code warps you as you
    step into it — but the position sample taken on that frame reports you
    standing there. So a stood-on tile is expected to be either walkable or a
    tile the decomp's own `warp_events` declares a warp, and `warp` below counts
    those separately. A stood-on tile that is blocked and is NOT a declared warp
    is a real failure, because nothing in the cartridge explains it.
    """
    cells = {"total": 0, "bad": [], "unresolved": set()}
    walk = {"total": 0, "bad": [], "warp": []}
    warps: dict[str, set[tuple[int, int]]] = {}
    for run in run_dirs(game):
        samples = list(run_samples(run))
        # The tile a run LEFT for another map is a door, whatever the cartridge
        # calls it. Independent of `warp_events`, and the only evidence
        # available for SoulSilver, whose event archive is not read here.
        left_for_another_map = {s[:3] for s, nxt in zip(samples, samples[1:]) if s[0] != nxt[0]}
        for map_id, x, y in samples:
            header = d.names[map_id]
            meta = d.meta.get(header)
            if meta is None:
                cells["unresolved"].add(map_id)
                continue
            matrix_const = meta["mapMatrixID"]
            col, row = x // CELL, y // CELL

            cells["total"] += 1
            named = d.header_at(matrix_const, col, row)
            if named is None:
                # A map with its own matrix: the cell must exist in it, and the
                # matrix IS this map, so there is nothing else it could be.
                m = d.matrix(matrix_const)
                ok = 0 <= row < len(m["maps"]) and 0 <= col < len(m["maps"][row])
            else:
                ok = named == header
            if not ok:
                cells["bad"].append((run.name, map_id, x, y, named))

            walk["total"] += 1
            grid = d.cell_grid(header, col, row)
            attr = None if grid is None else int(grid[y % CELL, x % CELL])
            if attr is not None and not attr & COLLISION_MASK:
                continue
            if header not in warps:
                warps[header] = {(int(w["x"]), int(w["z"])) for w in d.warps(header)}
            if attr is not None and ((x, y) in warps[header]
                                     or (map_id, x, y) in left_for_another_map):
                walk["warp"].append((run.name, map_id, x, y, attr))
            else:
                walk["bad"].append((run.name, map_id, x, y, attr))
    return {"cell": cells, "walkable": walk}


# ---------------------------------------------------------------- rendering

class Window:
    """WHERE a map is, with no pixels in it.

    Split out from `Rendered` because the two registration checks and the atlas
    both need the rectangle and only the artwork needs the rasteriser: with the
    3D tier, deriving the window by rendering the map would render every map
    twice.
    """

    def __init__(self, key: str, header: str, name: str, indoor: bool,
                 ox: int, oy: int, w: int, h: int, cells: list[tuple[int, int]],
                 c0: int, r0: int, cw: int, ch: int,
                 attrs: np.ndarray, defined: np.ndarray,
                 crop: tuple[int, int], shared: bool = True) -> None:
        self.key, self.header, self.name, self.indoor = key, header, name, indoor
        self.ox, self.oy, self.w, self.h = ox, oy, w, h
        self.cells, self.c0, self.r0, self.cw, self.ch = cells, c0, r0, cw, ch
        self.attrs, self.defined, self.crop = attrs, defined, crop
        # WHETHER THIS MAP IS ON THE REGION'S SHARED FRAME, which is a
        # different question from whether it is indoors and the one the viewer
        # actually spends. A map drawn on the region matrix has GLOBAL
        # coordinates and a place beside its neighbours; a map with a private
        # matrix of its own is measured from zero and has no place on that
        # frame at all. `indoor` is a claim about the MAP TYPE and cannot
        # answer it: Lake Verity Low Water is `MAP_TYPE_CAVE`, so it is not
        # indoors, and it is not on the world either.
        self.shared = shared

    @property
    def blocked(self) -> np.ndarray:
        return (self.attrs & COLLISION_MASK) != 0

    @property
    def walkable(self) -> int:
        """Tiles you can stand on. From the CARTRIDGE, not from the pixels.

        The 3D tier's pixels say nothing about collision — a path and the roof
        beside it are both just colours — so the number the atlas publishes has
        to come from the attribute grid either way, and then it means the same
        thing in both tiers.
        """
        return int((self.defined & ~self.blocked).sum())


class Rendered(Window):
    """A `Window`, its artwork, and the camera frame that artwork is in.

    `frame` is a `ds3d.camera.Frame`: the pixel rectangle, the pad around the
    map's own ground rectangle, and the projection that put a tile there.
    `heights` is the world altitude of the ground under each tile of the
    window — flat and zero under the straight-down camera, which is why the
    topdown atlas is unchanged by all of this.
    """

    def __init__(self, win: Window, rgba: np.ndarray, render: str,
                 frame=None, heights: Optional[np.ndarray] = None) -> None:
        self.__dict__.update(win.__dict__)
        self.rgba, self.render = rgba, render
        self.frame = frame
        self.heights = heights


def check_windows(d, game: str, ids: list[int]) -> dict:
    """Every tile the run stood on falls inside the window the atlas ships.

    The check the interior crop above needs, and the one that would catch a
    silhouette placed one cell out: a route tile outside its own map's rectangle
    is drawn over nothing, and the viewer reports no error for it.
    """
    windows = {i: map_window(d, i) for i in ids}
    out = {"total": 0, "bad": []}
    for run in run_dirs(game):
        for map_id, x, y in run_samples(run):
            r = windows.get(map_id)
            if r is None:
                continue
            out["total"] += 1
            if not (r.ox <= x < r.ox + r.w and r.oy <= y < r.oy + r.h):
                out["bad"].append((run.name, map_id, x, y, [r.ox, r.oy, r.w, r.h]))
    return out


def check_artwork(d, game: str, ids: list[int], art: "Field3D") -> dict:
    """The 3D tier's own registration check: the route stands on the artwork.

    The window check above proves a walked tile is inside the map's RECTANGLE.
    It cannot see the failure this tier can have and the silhouette cannot: a
    terrain mesh drawn half a chunk out, or a chunk left out of a two-chunk
    stitch, still fills a correct rectangle — with a hole where the route runs.

    THREE OUTCOMES PER TILE, not two, and the middle one is the whole point:

      covered  opaque geometry reaches it.
      SHEER    something is drawn there and you can see through it.
      bare     nothing is drawn there at all. THIS is the hole.

    `bare` used to mean both, and it was right for eight maps. Verity
    Lakefront is the counter-example the cartridge itself supplies: its 8x8
    `TILE_BEHAVIOR_PUDDLE` sits at the shared corner of its four matrix cells
    and NONE of the four terrain models puts anything under it — drop the
    `puddle`/`puddlep` materials and 30 of 16,384 pixels survive, all of them
    edge fringe. Gen 4's water sheet is alpha-36 texels and is meant to be seen
    through; where the artist gave it a bed (Twinleaf's `lake`, Route 219's
    `beach`/`searock`) the tile comes out opaque, and where there is no bed
    there is no bed. A check cannot demand geometry the cartridge does not
    have.

    Opaque stays the bar for `covered`, because the failure it caught is a
    translucent polygon WRITTEN instead of blended: pasted, a pond over its own
    bed keeps the water's own alpha 36 and the bed under it is gone. That
    failure now shows as 512 water tiles going sheer on Twinleaf and Route 219,
    which the caller asserts against — see `tests/test_dsmaps.py`. It does not
    show on Verity Lakefront and never could, because with nothing underneath,
    blended and pasted produce the same pixel.

    `sheer_behaviour` names the cartridge's own behaviour for every sheer tile,
    so the caller can say WHICH tiles it is willing to see through rather than
    just how many.
    """
    walked = route_tiles(game)
    out = {"maps": {}, "bare_walkable": 0, "sheer_walkable": 0,
           "bare_route": 0, "sheer_route": 0, "route": 0,
           "sheer_behaviour": {}, "sheer_runs": {}}
    for map_id in ids:
        r = render_map(d, map_id, art)
        if not r.render.startswith("3d-"):
            continue
        cov = ds3d_camera.tile_coverage(
            full_pixels(r, art.tile_px), frame_of(r, art.tile_px), r.heights)
        # DRAWN_ALPHA is `raster.draw_tri`'s own alpha test: a texel at or
        # below it is not written at all, so a tile under it holds no pixel
        # this renderer produced.
        drawn, covered = cov >= DRAWN_ALPHA, cov >= 128
        walk = r.defined & ~r.blocked
        bare_walk = int((walk & ~drawn).sum())
        sheer = walk & drawn & ~covered
        behave = r.attrs & BEHAVIOR_MASK
        for value in np.unique(behave[sheer]) if sheer.any() else ():
            name = d.behaviors[value] if value < len(d.behaviors) else f"#{value}"
            out["sheer_behaviour"][name] = (out["sheer_behaviour"].get(name, 0)
                                            + int((sheer & (behave == value)).sum()))
        if sheer.any():
            # The SHAPE of the see-through, not only its area: one 8x8 square
            # and 56 scattered specks are the same 56 tiles and are not the
            # same defect.
            out["sheer_runs"][r.key] = runs_of(sheer)
        pts = walked.get(map_id, [])
        bare_route = sum(1 for x, y in pts if not drawn[y - r.oy, x - r.ox])
        sheer_route = sum(1 for x, y in pts
                          if drawn[y - r.oy, x - r.ox] and not covered[y - r.oy, x - r.ox])
        out["maps"][r.key] = (bare_walk, int(sheer.sum()), int(walk.sum()),
                              bare_route, sheer_route, len(pts))
        out["bare_walkable"] += bare_walk
        out["sheer_walkable"] += int(sheer.sum())
        out["bare_route"] += bare_route
        out["sheer_route"] += sheer_route
        out["route"] += len(pts)
    return out


def map_window(d, map_id: int) -> Window:
    """The rectangle one map occupies, in route-tile coordinates."""
    header = d.names[map_id]
    cells = d.cells_of(header)
    if not cells:
        raise SystemExit(f"{header} occupies no cell of {d.meta[header]['mapMatrixID']}")
    c0 = min(c for c, _ in cells)
    r0 = min(r for _, r in cells)
    cw = max(c for c, _ in cells) - c0 + 1
    ch = max(r for _, r in cells) - r0 + 1
    w, h = cw * CELL, ch * CELL

    attrs = np.zeros((h, w), dtype=np.uint16)
    defined = np.zeros((h, w), dtype=bool)
    for col, row in cells:
        grid = d.cell_grid(header, col, row)
        if grid is None:
            continue
        y0, x0 = (row - r0) * CELL, (col - c0) * CELL
        attrs[y0:y0 + CELL, x0:x0 + CELL] = grid
        defined[y0:y0 + CELL, x0:x0 + CELL] = True

    # Outdoor maps live in the shared overworld matrix and their run
    # coordinates are global, so the window starts at the map's own corner.
    # A map with a matrix of its own is measured from zero, and so are its runs.
    shared = bool(d.matrix_of(header).get("headers"))
    ox, oy = (c0 * CELL, r0 * CELL) if shared else (0, 0)
    crop = (0, 0)

    if not shared:
        # A room does not fill its block. The rest of the 32x32 is 0x0000 —
        # passable, behaviour NONE — which is the SAME value as the room's own
        # floor, so nothing distinguishes floor from nothing tile by tile. What
        # does distinguish them is that an interior is sealed: you leave through
        # a warp, never over an edge, so its wall ring bounds it and every
        # non-zero attribute in the block lies inside that ring. Drawn without
        # this crop, Professor Rowan's lab is a small room with a blank hall the
        # size of the map next to it.
        #
        # `crop` is kept because the 3D tier has to cut its own render to
        # EXACTLY this box; cropping the collision and the artwork differently
        # would put the route on one and not the other.
        used = np.argwhere(attrs != 0)
        if used.size:
            (by0, bx0), (by1, bx1) = used.min(0), used.max(0)
            attrs = attrs[by0:by1 + 1, bx0:bx1 + 1]
            defined = defined[by0:by1 + 1, bx0:bx1 + 1]
            ox, oy, crop = int(bx0), int(by0), (int(bx0), int(by0))
            h, w = attrs.shape

    indoor = d.meta[header].get("mapType") in ("MAP_TYPE_INDOORS", "MAP_TYPE_POKECENTER")
    return Window(f"{map_id}:0", header, header.removeprefix("MAP_HEADER_"),
                  indoor, ox, oy, w, h, cells, c0, r0, cw, ch, attrs, defined, crop,
                  shared)


def silhouette(d, win: Window) -> np.ndarray:
    """Tier 1: one flat tone per tile, from the cartridge's own tile behaviour."""
    blocked = win.blocked
    behave = win.attrs & BEHAVIOR_MASK
    rgba = np.empty((win.h, win.w, 4), dtype=np.uint8)
    rgba[...] = WALL
    for value in np.unique(behave[~blocked]):
        name = d.behaviors[value] if value < len(d.behaviors) else ""
        rgba[(~blocked) & (behave == value)] = behavior_tone(name)
    rgba[~win.defined] = VOID
    return rgba


class Field3D:
    """Tier 2: the decomp's own artwork, under the camera it is asked for.

    Holds the archive cache, so rendering eight maps reads each texture set and
    each building model once. `pixels` returns the map's window at `tile_px`
    pixels per tile — the same rectangle `map_window` reports, cropped the same
    way — or None for a map with no terrain model at all, which is the only
    case that falls back to the silhouette.
    """

    def __init__(self, d, *, tile_px: int, supersample: int = SUPERSAMPLE,
                 cam=None, light=None) -> None:
        self.d = d
        self.tile_px, self.ss = tile_px, supersample
        # A directional light, or None for the slope shade every atlas shipped
        # with. Off by default: it repaints every map (see `--light`).
        self.light = light
        # Straight down unless told otherwise, so every caller that predates
        # `--camera` renders exactly what it rendered before.
        self.cam = cam or ds3d_camera.camera_for("topdown", tile_px)
        self.frames: dict[str, ds3d_camera.Frame] = {}
        self.heights: dict[str, np.ndarray] = {}
        if isinstance(d, RomDecomp):
            if not d.headers:
                raise SystemExit(
                    "no map-header table could be located in this cartridge, so nothing "
                    "says which artwork set a map uses (see the note above RomDecomp)")
            self.assets = ds3d_field.RomAssets(d.rom)
        else:
            self.assets = ds3d_field.Assets(
                lambda p: fetch(p, offline=d.offline),
                lambda p: fetch_json(p, offline=d.offline))
        self.stats: dict[str, list] = {}

    def area(self, header: str) -> ds3d_field.Area:
        const = self.d.meta[header].get("areaDataArchiveID")
        if const is None or const == "":
            raise SystemExit(f"{header} names no areaDataArchiveID")
        return ds3d_field.area_of(self.assets, const)

    def prop_archive(self, win: Window) -> str:
        """Which prop archive this map's placement ids index.

        SoulSilver ships two — `bm_field` for the overworld and `bm_room` for
        interiors — and the SAME id names a different model in each, so getting
        it wrong draws a lake over Professor Elm's laboratory and reports
        nothing. The discriminator is not a guess and not a new field: a map
        drawn on the region matrix is a field map, and a map with a private 1x1
        matrix of its own is a room. That is the same test `map_window` already
        makes to decide whether the map's coordinates are global.

        Checked rather than assumed: under the wrong archive four of New Bark
        Town's placements and three of the interiors' do not resolve at all, and
        `check_artwork` sees the holes.
        """
        return "field" if self.d.matrix_of(win.header).get("headers") else "room"

    def land_block(self, header: str, col: int, row: int) -> Optional[bytes]:
        """The raw bytes of one matrix cell's land block, from either source."""
        land = self.d.matrix_of(header)["maps"][row][col]
        if isinstance(self.d, RomDecomp):
            if land is None or land >= len(self.d.land):
                return None
            return self.d.land[land]
        if not str(land).startswith("MAP_") or land == "MAP_NONE":
            return None
        n = str(land).split("_")[-1]
        if not n.isdigit():
            return None
        return fetch(f"res/field/maps/data/map_data_{int(n):03d}.bin", offline=self.d.offline)

    def pixels(self, win: Window) -> Optional[np.ndarray]:
        area = self.area(win.header)
        matrix = self.d.matrix_of(win.header)
        alts = matrix.get("altitudes")
        sc = ds3d_scene.Scene()
        stats = []
        spans = []
        for col, row in sorted(win.cells):
            raw = self.land_block(win.header, col, row)
            if raw is None:
                continue
            # The matrix's own altitude plane, which the game spends as
            # `position.y = altitude * (MAP_OBJECT_TILE_SIZE / 2)`.
            alt = alts[row][col] if alts else 0
            start = len(sc.tris)
            st = ds3d_field.add_chunk(
                sc, self.assets, raw, area, prop_archive=self.prop_archive(win),
                origin=ds3d_field.chunk_origin(col, row, win.c0, win.r0, alt))
            st.land = str(matrix["maps"][row][col])
            # Where this chunk's TERRAIN sits in the triangle list. The props
            # of the same chunk follow it, so the ground sampler can leave
            # them out and not read a rooftop as the ground.
            spans.append((start, st.terrain_tris))
            stats.append(st)
        self.stats[win.key] = stats
        if not sc.tris:
            return None
        tw, th = win.cw * CELL, win.ch * CELL
        full, frame = ds3d_camera.pixels(sc, self.cam, tw, th, self.ss, light=self.light)
        bx0, by0 = win.crop
        self.frames[win.key] = frame.crop(bx0, by0, win.w, win.h)
        ground = ds3d_camera.ground_heights(ds3d_camera.terrain_only(sc, spans), tw, th)
        self.heights[win.key] = ds3d_camera.walkable_plane(
            ground[by0:by0 + win.h, bx0:bx0 + win.w], win.defined & ~win.blocked)
        return frame.cut(full, bx0, by0, win.w, win.h)


def frame_of(r: Rendered, tile_px: int):
    """The `camera.Frame` this render fills — the straight-down one by default."""
    return r.frame or ds3d_camera.Frame(
        ds3d_camera.camera_for("topdown", tile_px), r.w, r.h, (0, 0, 0, 0))


def full_pixels(r: Rendered, tile_px: int) -> np.ndarray:
    """`r.rgba` at its frame's pixel size, blowing up a per-tile tier if needed."""
    frame = frame_of(r, tile_px)
    block = r.rgba
    if block.shape[:2] == (r.h, r.w):
        block = ds3d_camera.stretch(block, frame)
    if block.shape[:2] != (frame.height, frame.width):
        raise SystemExit(f"{r.key}: {block.shape[:2]} is not the frame's "
                         f"{(frame.height, frame.width)}")
    return block


def to_png(r: Rendered, path: Path, tile_px: int, *,
           stats: Optional[dict] = None) -> None:
    """The map's own rectangle, one PNG pixel per `tile_px`. See the docstring.

    Map-local, not padded to route tile (0, 0): the atlas says so with
    `png_frame` and each entry's `png_origin`, and `RouteMap.svelte` subtracts
    it from the source rect. Under a pitched camera the picture is BIGGER than
    the ground rectangle — a roof rises out of it — so the entry also carries
    `png_pad` and the viewer anchors by that instead of by the rect alone.
    """
    # Artwork goes through `ds3d/pngout.py`, which rounds each channel back to
    # the DS's own five bits — the console never showed more, and the file
    # loses about a third for precision that was never on screen. The
    # COLLISION tier opts out: its two tones are a palette this repo chose,
    # not colour read off a ROM, so the argument for quantising a cartridge's
    # own output does not apply to a colour we invented. Gen 5 has made the
    # same call since 2026-09-21 (render_gen5maps.to_png) and this is the
    # same writer, not a second one.
    block = full_pixels(r, tile_px)
    pngout.write_png(block, path, 1, quantise_to_ds=r.render != "collision",
                     stats=stats)


def render_map(d, map_id: int, art: Optional[Field3D] = None, *, cam=None) -> Rendered:
    """One map's window with its artwork: the 3D tier, or the silhouette.

    The silhouette is stretched into the SAME frame the 3D tier would have
    used. A map that falls back inside a pitched atlas otherwise ships a
    square picture in an atlas that says every map is foreshortened, and its
    route lands on the wrong rows with nothing raising.
    """
    win = map_window(d, map_id)
    if art is not None:
        px = art.pixels(win)
        if px is not None:
            return Rendered(win, px, RENDER_KIND_FOR[("3d", art.cam.kind)],
                            art.frames[win.key], art.heights[win.key])
    cam = cam or (art.cam if art is not None else None)
    flat = silhouette(d, win)
    if cam is None or cam.kind == "topdown":
        return Rendered(win, flat, "collision")
    frame = ds3d_camera.Frame(cam, win.w, win.h, (0, 0, 0, 0))
    return Rendered(win, ds3d_camera.stretch(flat, frame), "collision", frame,
                    np.zeros((win.h, win.w)))


def border_png(path: Path, tile_px: int) -> None:
    """The one-tile block the viewer would tile outside a map.

    Gen 4 has no border block: a matrix cell no map claims is nothing at all,
    and the game draws nothing there. So this is that nothing, as one
    transparent tile — the atlas invariant is that artwork ships the block it is
    drawn inside, and for a DS map that block is empty. `borders.js` lists no DS
    map, so the viewer never tiles it; it is here so the claim in the atlas is
    true rather than absent.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.zeros((tile_px, tile_px, 4), dtype=np.uint8), "RGBA").save(path, "PNG")


def open_edges(d: Decomp, r: Rendered) -> list[dict]:
    """Where this map's edge meets another map, in the map's own tiles.

    The spans are MAP-LOCAL, like every other atlas's — `test_gamemaps.py` reads
    them that way and so does `RouteMap.borderPatch`. Derived from the matrix,
    not from a connection table: a DS region has no connections because the maps
    are already touching.
    """
    if r.indoor:
        return []
    m = d.matrix_of(r.header)
    heads = m.get("headers")
    if not heads:
        return []
    cells = d.cells_of(r.header)
    c0 = min(c for c, _ in cells)
    r0 = min(row for _, row in cells)
    sides = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}
    spans: list[dict] = []
    for side, (dc, dr) in sides.items():
        runs: list[tuple[int, int]] = []
        for col, row in sorted(cells):
            nb = d.header_at(d.meta[r.header]["mapMatrixID"], col + dc, row + dr)
            if nb is None or nb == r.header or nb == "MAP_HEADER_EVERYWHERE":
                continue
            # only cells actually ON that edge of this map's box
            if side == "up" and row != r0:
                continue
            if side == "down" and row != max(x for _, x in cells):
                continue
            if side == "left" and col != c0:
                continue
            if side == "right" and col != max(x for x, _ in cells):
                continue
            along = (col - c0) if side in ("up", "down") else (row - r0)
            runs.append((along * CELL, along * CELL + CELL))
        for lo, hi in sorted(runs):
            if spans and spans[-1]["side"] == side and spans[-1]["to"] == lo:
                spans[-1]["to"] = hi
            else:
                spans.append({"side": side, "from": lo, "to": hi})
    return spans


def observed_doors(game: str, frame) -> dict[int, list[tuple[int, int, int]]]:
    """`{map_id: [(x, y, dest_map_id)]}` — the doors OUR RUNS walked through.

    Second-best evidence, used only where the first is unavailable. Platinum
    takes its doors from the decomp's own `warp_events`, which lists every door
    on a map whether anyone opened it. A cartridge ships the same data in an
    event archive this does not read, so for SoulSilver a door is a tile a run
    STOOD ON and left for another map on the very next sample — which is what a
    door is, and which is already the oracle `check_registration` accepts for a
    blocked tile somebody stood on.

    The cost is honest and it is recorded in the atlas: this finds the doors
    that were used, not the doors that exist. A building nobody entered has no
    marker, and the day the event archive is read this goes away.

    `frame(map_id)` is the map's matrix const.

    A map transition is NOT automatically a door. Walking west out of New Bark
    Town onto Route 29 changes the map id and opens no door: the two are
    neighbours on the region matrix and the edge between them is already an
    `open` span. So a transition counts only when the two maps are on DIFFERENT
    matrices, which is exactly the case adjacency cannot explain.
    """
    out: dict[int, dict[tuple[int, int], int]] = defaultdict(dict)
    for run in run_dirs(game):
        samples = list(run_samples(run))
        for (m1, x1, y1), (m2, _x2, _y2) in zip(samples, samples[1:]):
            if m1 == m2:
                continue
            try:
                same_frame = frame(m1) == frame(m2)
            except KeyError:
                same_frame = False
            if not same_frame:
                out[m1][(x1, y1)] = m2
    return {m: [(x, y, dest) for (x, y), dest in sorted(d.items())] for m, d in out.items()}


def provenance(d) -> dict:
    """Where these bytes came from — a pinned commit, or the cartridge's sha1."""
    if isinstance(d, RomDecomp):
        return {"kind": "rom", "file": d.rom.path.name, "sha": d.sha}
    return {"kind": "decomp", "repo": PRET, "sha": d.sha}


def build_atlas(d, game: str, ids: list[int], *, render: str = DEFAULT_RENDER,
                tile_px: Optional[int] = None, write: bool = True,
                camera: str = DEFAULT_CAMERA, light: bool = False) -> dict:
    """Render every map and write `public/maps/<game>/`."""
    tile_px = TILE_PX[render] if tile_px is None else tile_px
    render_kind = RENDER_KIND_FOR[(render, camera)]
    cam = ds3d_camera.camera_for(camera, tile_px)
    lamp = ds3d_camera.directional_shade() if light else None
    art = Field3D(d, tile_px=tile_px, cam=cam, light=lamp) if render == "3d" else None
    out = MAPS_ROOT / game
    rendered = {i: render_map(d, i, art, cam=cam) for i in ids}
    by_header = {r.header: r for r in rendered.values()}
    # A source with no event archive has no `warp_events` to read, so its doors
    # are the ones our own runs walked through. Provenance below.
    walked_doors = ({} if any(d.warps(r.header) for r in rendered.values())
                    else observed_doors(game, lambda i: d.meta[d.names[i]]["mapMatrixID"]))

    # An interior's caption is cut at the header of the map its door is on, so
    # only the OUTDOOR maps we rendered are candidate prefixes.
    outer = {r.header.removeprefix("MAP_HEADER_") for r in rendered.values() if r.shared}
    names = {r.key: pretty_name(r.header, outer) for r in rendered.values()}
    buildings: dict[str, list[str]] = defaultdict(list)
    for key, (_, b) in names.items():
        if b:
            buildings[b].append(key)

    # A cartridge ships map ids and no names, so `pretty_name` returns nothing
    # and the name-prefix rule that groups Platinum's floors has nothing to work
    # on. The floors are still groupable, from the SHAPE of the doors: an
    # interior whose door leads to another INTERIOR is a floor of that same
    # building. Two floors of the player's house then open as one popup rather
    # than as two unrelated rooms, which is what the viewer's `floors` is for.
    group: dict[str, str] = {}
    if walked_doors:
        parent = {r.key: r.key for r in rendered.values() if not r.shared}

        def find(k):
            while parent[k] != k:
                parent[k] = parent[parent[k]]
                k = parent[k]
            return k

        for map_id, exits in walked_doors.items():
            a = rendered.get(map_id)
            if a is None or a.shared:
                continue
            for _x, _y, dest_id in exits:
                b = rendered.get(dest_id)
                if b is None or b.shared:
                    continue
                parent[find(b.key)] = find(a.key)
        # The building is named after its LOWEST floor, not after whichever
        # room the union happened to root on: `63:0` is the ground floor the
        # door opens into and `64:0` is upstairs.
        comps: dict[str, list[str]] = defaultdict(list)
        for key in parent:
            comps[find(key)].append(key)
        for members in comps.values():
            root = min(members, key=lambda k: int(k.split(":")[0]))
            for key in members:
                group[key] = root
            buildings[root].extend(sorted(members))

    maps: dict[str, dict] = {}
    for map_id, r in sorted(rendered.items()):
        png = f"{map_id}-0.png"
        border = f"{map_id}-0-border.png"
        if write:
            to_png(r, out / png, tile_px)
            border_png(out / border, tile_px)
        name, building = names[r.key]
        # SoulSilver has no decomp, so `names` leaves it None and the place
        # name comes off the cartridge instead (ds3d/mapnames.py). Platinum's
        # decomp name WINS where it has one: `TwinleafTown_RivalHouse_1F` is
        # unique and richer than the "Twinleaf Town" the cartridge prints for
        # all four of that town's maps. `or` is the precedence, not a
        # fallback flag — and mapnames has no Platinum entry either way.
        #
        # A cartridge name is a LABEL and not a key: a map header indexes a
        # PLACE-name table, so five of SoulSilver's six maps read "New Bark
        # Town". `building` below is the uniqueness field and is untouched.
        name = name or mapnames.map_name(game, map_id)
        entry: dict = {
            "name": name,
            "width": r.w,
            "height": r.h,
            "file": png,
            "bytes": (out / png).stat().st_size if write else 0,
            "indoor": r.indoor,
            "border": {"file": border, "w": 1, "h": 1,
                       "bytes": (out / border).stat().st_size if write else 0},
            # From the collision grid, not from the pixels — see `Window.walkable`.
            "walkable": r.walkable,
        }
        if r.ox or r.oy:
            entry["origin"] = [r.ox, r.oy]
            # The tile PNG pixel (0, 0) holds. Equal to `origin` here because a
            # DS PNG is map-local; absent (and so zero) for every gen 1-3 atlas,
            # whose artwork already starts at the route's own corner.
            entry["png_origin"] = [r.ox, r.oy]
        if cam.kind != "topdown":
            # Where the map's GROUND rectangle sits inside the PNG. Straight
            # down the two are the same rectangle and this is absent; pitched,
            # a roof rises out of the top of the ground rect and the picture
            # has to be bigger than it. [left, top, right, bottom], px.
            entry["png_pad"] = list(frame_of(r, tile_px).pad)
            # The world altitude of the ground under each tile of the window,
            # run-length encoded row-major. The route is drawn ON it: a gen-4
            # outdoor map's ground is 16 world units up, which is half a tile
            # on the picture, and Twinleaf's beach is 9 units below its town.
            entry["heights"] = ds3d_camera.atlas_heights(
                r.heights if r.heights is not None else np.zeros((r.h, r.w)))
        if r.render != render_kind:
            entry["render"] = r.render
        if r.shared:
            entry["world"] = [r.ox, r.oy]
            entry["frame"] = d.meta[r.header]["mapMatrixID"]
            spans = open_edges(d, r)
            if spans:
                entry["open"] = spans
        else:
            # A MAP OFF THE REGION FRAME OPENS AS A CLUSTER, and `shared` is
            # what decides that, not `indoor`. Lake Verity Low Water is
            # `MAP_TYPE_CAVE` on a private 3x2 matrix: not indoors, and with no
            # place on the world either. Published as outdoor it carried
            # `world: [0, 0]` — its own measured origin, correctly zero for a
            # map whose coordinates are map-local — and the viewer read that as
            # a position, put it at the world origin 800 tiles north of every
            # other Platinum map, and `fit` fell to 0.10x over a mostly empty
            # panel. The entry even named a different `frame` while it did so.
            #
            # An interior belongs to a building, so the viewer can walk into it
            # from the door below and back out again, and two floors of one
            # building open as one popup rather than two.
            building = building or group.get(r.key) or name or r.key
            entry["building"] = building
            entry["floors"] = sorted(buildings.get(building, [r.key]))
            entry["popup"] = True

        doors = []
        for w in d.warps(r.header):
            dest = by_header.get(w.get("dest_header_id"))
            if dest is None or dest is r:
                continue
            doors.append({"x": int(w["x"]), "y": int(w["z"]), "to": dest.key,
                          "building": names[dest.key][1] or names[dest.key][0]})
        for x, y, dest_id in walked_doors.get(map_id, []):
            dest = rendered.get(dest_id)
            if dest is None or dest is r:
                continue
            doors.append({"x": x, "y": y, "to": dest.key,
                          "building": (names[dest.key][1] or group.get(dest.key)
                                       or names[dest.key][0] or dest.key)})
        if doors and r.shared:
            entry["doors"] = doors
        if doors and not r.shared:
            entry["exits"] = [{"x": x["x"], "y": x["y"], "to": x["to"]} for x in doors]
        maps[r.key] = entry

    fell_back = sorted(k for k, m in maps.items() if m.get("render"))
    atlas = {
        "schema": 2,
        "game": game,
        "key_shape": "id",
        "tile_px": tile_px,
        # Was the bare string "topdown". Now the projection itself, because a
        # viewer that only knows the name cannot place a tile under it. A
        # reader still has to accept the string: the atlases on disk predate
        # this and say "topdown" with nothing beside it.
        "camera": ds3d_camera.atlas_camera(cam),
        "render": render_kind,
        "png_frame": "map-local",
        "source": provenance(d),
        "walkgraph": {"version": None, "source": None},
        "doors": "observed-transitions" if walked_doors else "warp_events",
        "map_set": {
            "kind": "observed",
            "source": (f"artifacts/game-map-render/observed/{game}-observed.json"
                       " + every map id our runs' samples name"),
            "note": (RENDER_NOTE[render_kind]
                     + "; PNG pixel (0,0) is tile png_origin, so a source rect "
                       "subtracts it"),
        },
        "bytes": sum(m["bytes"] + m["border"]["bytes"] for m in maps.values()),
        "maps": maps,
    }
    if fell_back:
        atlas["map_set"]["fallback"] = fell_back
    if write:
        out.mkdir(parents=True, exist_ok=True)
        tmp = out / ".index.json.tmp"
        tmp.write_text(json.dumps(atlas, indent=1) + "\n")
        os.replace(tmp, out / "index.json")
    return atlas


# ---------------------------------------------------------------- the proof sheet

def route_tiles(game: str) -> dict[int, list[tuple[int, int]]]:
    """Every tile walked, per map, in the order it was walked."""
    walked: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for run in run_dirs(game):
        for map_id, x, y in run_samples(run):
            if not walked[map_id] or walked[map_id][-1] != (x, y):
                walked[map_id].append((x, y))
    return walked


# The route, drawn the way `RouteMap.svelte` draws it. These are NOT free
# choices: each one is `src/dashboard/web/src/lib/mapatlas.js`'s own constant,
# and `test_dsmaps.py` asserts they still match it. A sheet that drew a fatter
# line than the product would be answering a question nobody asked.
#
#   mapatlas.js:1172  lw = max(1.2, scale * 0.11)
#   mapatlas.js:1186  halo rgba(12,15,20,.58) at lw + max(1.4, scale * 0.09)
#   mapatlas.js:33    CABLE_GAP 3.5 — a step walked twice draws two cables
#   mapatlas.js:201-6 the colour wheel: hsl(220 + 360f, 74%, 56%), one full
#                     turn every COLOUR_LOOP_TILES = 300 tiles walked
CABLE_WIDTH = 0.11             # of a tile
CABLE_HALO = 0.09              # of a tile, added to the width
CABLE_GAP = 3.5                # px between two cables over the same step
MAX_LANES = 4
COLOUR_LOOP_TILES = 300
HUE_START = 220
HALO_RGBA = (12, 15, 20, 148)  # rgba(12,15,20,.58)

# What the spike drew, kept ONLY as the "before" half of the legibility
# comparison: a 4-world-unit opaque red band with a 1.6-unit dark halo, which
# over 241 tiles of Sandgem covered the town.
SPIKE_WIDTH = 4.0 / 16.0       # of a tile
SPIKE_RGBA = (255, 64, 32, 255)
SPIKE_HALO = (24, 16, 12, 255)


def wheel_colour(f: float) -> tuple[int, int, int, int]:
    """`hsl(HUE_START + 360f, 74%, 56%)`, as RGBA."""
    import colorsys

    h = ((HUE_START + f * 360.0) % 360.0) / 360.0
    r, g, b = colorsys.hls_to_rgb(h, 0.56, 0.74)
    return (int(r * 255 + 0.5), int(g * 255 + 0.5), int(b * 255 + 0.5), 255)


def route_steps(pts: list[tuple[int, int]]) -> list[tuple]:
    """`(a, b, t, lane, lanes)` for every real step, in walk order.

    Only real steps: two consecutive samples that are not neighbours are a warp
    or a re-entry, and joining them draws a diagonal across the map that no
    route ever walked.

    `lane` reproduces the viewer's cable bundle without its crossing solver — a
    corridor walked four times draws four cables `CABLE_GAP` apart, which is the
    ink the artwork actually has to be read through. Without it a sheet would
    understate the busiest corridors, which are exactly the ones that swamped
    the spike.
    """
    raw = []
    n = max(len(pts) - 1, 1)
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        if abs(a[0] - b[0]) + abs(a[1] - b[1]) != 1:
            continue
        raw.append((a, b, i / n))
    counts: dict = defaultdict(int)
    for a, b, _ in raw:
        counts[frozenset((a, b))] += 1
    seen: dict = defaultdict(int)
    out = []
    for a, b, t in raw:
        k = frozenset((a, b))
        lanes = min(counts[k], MAX_LANES)
        lane = seen[k] % lanes
        seen[k] += 1
        out.append((a, b, t, lane, lanes))
    return out


def paint_route(base: Image.Image, r: Rendered, pts: list[tuple[int, int]],
                tile_px: int, *, style: str = "viewer", ss: int = 4) -> None:
    """The run's route over one panel, in place.

    Drawn on a supersampled overlay and composited down, because the width that
    matters here is under two pixels and PIL's `line` has no antialiasing: a
    1.76 px cable drawn without it becomes a 2 px cable, which is 14% more ink
    than the product puts on the map.
    """
    from PIL import ImageDraw

    steps = route_steps(pts)
    if not steps:
        return
    n_tiles = len(pts)
    over = Image.new("RGBA", (base.width * ss, base.height * ss), (0, 0, 0, 0))
    dr = ImageDraw.Draw(over)

    if style == "spike":
        lw = SPIKE_WIDTH * tile_px * ss
        passes = [(lw + 3.2 * ss / 16 * tile_px, lambda t: SPIKE_HALO),
                  (lw, lambda t: SPIKE_RGBA)]
        gap = 0.0
    else:
        lw = max(1.2, tile_px * CABLE_WIDTH) * ss
        halo = lw + max(1.4, tile_px * CABLE_HALO) * ss
        passes = [(halo, lambda t: HALO_RGBA),
                  (lw, lambda t: wheel_colour((t * n_tiles / COLOUR_LOOP_TILES) % 1.0))]
        gap = CABLE_GAP * ss

    frame = frame_of(r, tile_px)

    def at(p):
        """A route tile's centre, ON THE GROUND the camera drew.

        Straight down this is the old `(x - ox) * tile_px + tile_px/2`
        exactly. Pitched, the y is foreshortened AND lifted by the ground's
        own altitude: a gen-4 outdoor map's ground plane is 16 world units up,
        so a route drawn at zero sits half a tile below the path it walked.
        """
        tx, ty = p[0] - r.ox, p[1] - r.oy
        h = 0.0 if r.heights is None else float(r.heights[ty, tx])
        x, y = frame.tile_px(tx + 0.5, ty + 0.5, h)
        return (x * ss, y * ss)

    for width, colour in passes:
        for a, b, t, lane, lanes in steps:
            (ax, ay), (bx, by) = at(a), at(b)
            dx, dy = bx - ax, by - ay
            length = (dx * dx + dy * dy) ** 0.5 or 1.0
            o = (lane - (lanes - 1) / 2) * gap
            nx, ny = -dy / length * o, dx / length * o
            dr.line([(ax + nx, ay + ny), (bx + nx, by + ny)],
                    fill=colour(t), width=max(1, int(round(width))))

    # The run's ends, the viewer's two boxes: blue where it started, red where
    # it stopped.
    for p, col in ((pts[0], (40, 80, 220, 255)), (pts[-1], (220, 50, 20, 255))):
        cx, cy = at(p)
        rad = 0.5 * tile_px * ss
        dr.rectangle([cx - rad, cy - rad, cx + rad, cy + rad], outline=col,
                     width=max(1, int(round(max(1.5, tile_px * 0.12) * ss))))

    base.alpha_composite(over.resize(base.size, Image.LANCZOS))


def panel(r: Rendered, walked: dict, tile_px: int, *, route: bool = True,
          style: str = "viewer") -> Image.Image:
    """One map at real size, on the page's own ground, with its route on it."""
    px = full_pixels(r, tile_px)
    base = Image.new("RGBA", (px.shape[1], px.shape[0]), (12, 14, 18, 255))
    base.alpha_composite(Image.fromarray(px, "RGBA"))
    pts = walked.get(int(r.key.split(":")[0]), [])
    if route and pts:
        paint_route(base, r, pts, tile_px, style=style)
    return base


def sheet(d, game: str, ids: list[int], path: Path, *,
          render: str = DEFAULT_RENDER, tile_px: Optional[int] = None,
          camera: str = DEFAULT_CAMERA, light: bool = False) -> Path:
    """Each map at real size with a real run's route drawn over it.

    Over it, not beside it: artwork that reads fine bare can be useless under a
    coloured polyline, and a sheet without the route would not show that. The
    route is drawn with the VIEWER's own constants, so what this sheet shows is
    what the page shows.
    """
    from PIL import ImageDraw, ImageFont

    tile_px = TILE_PX[render] if tile_px is None else tile_px
    cam = ds3d_camera.camera_for(camera, tile_px)
    art = (Field3D(d, tile_px=tile_px, cam=cam,
                   light=ds3d_camera.directional_shade() if light else None)
           if render == "3d" else None)
    walked = route_tiles(game)

    def font(size, bold=False):
        for p in (f"/System/Library/Fonts/Supplemental/Arial{' Bold' if bold else ''}.ttf",):
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                pass
        return ImageFont.load_default()

    panels = []
    for map_id in ids:
        r = render_map(d, map_id, art, cam=cam)
        pts = walked.get(map_id, [])
        panels.append((r, panel(r, walked, tile_px), len(pts)))

    PAD, GAP, LABEL, HEAD = 28, 22, 38, 96
    # Two columns, packed by height rather than by row: Route 201 is 1024 px
    # wide and a Pokecenter is 272, so a row grid leaves a third of the sheet
    # empty and the maps read as further apart than they are.
    cols = 2
    colw = [0] * cols
    for i, (_, im, _) in enumerate(panels):
        colw[i % cols] = max(colw[i % cols], im.width, 260)

    # The before/after strip: the same map, the same route, the spike's stroke
    # against the viewer's.
    cmp_id = next((i for i in ids if len(walked.get(i, [])) > 50), ids[0])
    cmp_r = render_map(d, cmp_id, art, cam=cam)
    before = panel(cmp_r, walked, tile_px, style="spike")
    after = panel(cmp_r, walked, tile_px, style="viewer")
    CMPH = before.height + LABEL + GAP

    # Where each panel lands: the shorter column takes the next one.
    top = [0] * cols
    place = []
    for i, (_, im, _) in enumerate(panels):
        c = min(range(cols), key=lambda k: (top[k], k)) if i >= cols else i
        place.append((c, top[c]))
        top[c] += im.height + LABEL + GAP

    width = PAD * 2 + sum(colw) + GAP * (cols - 1)
    width = max(width, PAD * 2 + before.width * 2 + GAP)
    height = PAD + HEAD + CMPH + max(top) + PAD
    out = Image.new("RGBA", (width, height), (18, 20, 26, 255))
    g = ImageDraw.Draw(out)

    kind = RENDER_KIND_FOR[(render, camera)]
    src = provenance(d)
    g.text((PAD, PAD), f"{game} — {RENDER_NOTE[kind]}", font=font(21, True),
           fill=(240, 242, 248, 255))
    g.text((PAD, PAD + 30),
           f"tile_px {tile_px} · {CAMERA_CAPTION[camera]} · {len(panels)} maps · "
           f"{src.get('repo', src.get('file'))} @ {src['sha'][:7]}",
           font=font(13), fill=(150, 158, 174, 255))
    g.text((PAD, PAD + 50),
           "Every panel is at REAL SIZE and carries a real run's route, drawn with "
           "RouteMap.svelte's own stroke width, halo, cable gap and colour wheel.",
           font=font(13), fill=(150, 158, 174, 255))

    y = PAD + HEAD
    g.text((PAD, y), f"Route legibility — {cmp_r.name}, the same "
                     f"{len(walked.get(cmp_id, []))} samples drawn two ways",
           font=font(16, True), fill=(235, 236, 240, 255))
    for i, (im, cap) in enumerate(((before, "BEFORE — the spike's 4-unit opaque band"),
                                   (after, "AFTER — the viewer's 1.8 px cable"))):
        x = PAD + i * (before.width + GAP)
        g.text((x, y + 20), cap, font=font(12),
               fill=(225, 150, 120, 255) if i == 0 else (130, 200, 150, 255))
        out.alpha_composite(im, (x, y + LABEL))
        g.rectangle([x - 1, y + LABEL - 1, x + im.width, y + LABEL + im.height],
                    outline=(58, 62, 70, 255))
    y += CMPH

    for i, (r, im, n) in enumerate(panels):
        col, dy = place[i]
        cx = PAD + sum(colw[:col]) + GAP * col
        cy = y + dy
        g.text((cx, cy), f"{r.key}  {r.name}", font=font(15, True), fill=(222, 228, 240, 255))
        g.text((cx, cy + 17),
               f"{r.w}x{r.h} tiles · origin {r.ox},{r.oy} · {n} samples · {r.render}",
               font=font(12), fill=(150, 158, 174, 255))
        out.alpha_composite(im, (cx, cy + LABEL))
        g.rectangle([cx - 1, cy + LABEL - 1, cx + im.width, cy + LABEL + im.height],
                    outline=(58, 62, 70, 255))

    path.parent.mkdir(parents=True, exist_ok=True)
    out.convert("RGB").save(path)
    return path


# ---------------------------------------------------------------- cli

def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game", default="platinum-us")
    ap.add_argument("--check", action="store_true", help="registration checks only")
    ap.add_argument("--sheet", action="store_true", help="also write the proof sheet")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--render", choices=sorted(TILE_PX), default=DEFAULT_RENDER,
                    help="3d: the cartridge's own artwork; collision: the silhouette fallback")
    ap.add_argument("--tile-px", type=int, default=None,
                    help="override the tier's own pixels per tile")
    ap.add_argument("--camera", choices=sorted(ds3d_camera.KINDS), default=DEFAULT_CAMERA,
                    help="pitched: the field camera's own angle (default); "
                         "topdown: straight down, what shipped before 2026-09-21")
    ap.add_argument("--light", action="store_true",
                    help="a directional light instead of the flat slope shade, so "
                         "the four slopes of a hip roof differ (ds3d/camera.py). Off "
                         "by default: it repaints every map of every DS game")
    args = ap.parse_args(argv)

    render = args.render

    d = source_for(args.game, offline=args.offline)
    print(f"{args.game}: {provenance(d)}")

    res = check_registration(d, args.game)
    for name, r in res.items():
        bad, warp = r["bad"], r.get("warp", [])
        print(f"check {name:9s} {r['total'] - len(bad)}/{r['total']} pass"
              + (f"  ({len(warp)} on declared warp tiles, {len(set(w[2:4] for w in warp))} distinct)"
                 if warp else "")
              + (f"  FAIL {bad[:3]}" if bad else ""))
    if any(r["bad"] for r in res.values()):
        print("registration is wrong; nothing downstream is trustworthy", file=sys.stderr)
        return 2
    if res["cell"]["unresolved"]:
        print(f"NOT PLACEABLE: {sorted(res['cell']['unresolved'])} — no matrix names them, so "
              "nothing in the source says where they are (see the note above RomDecomp)")
    ids, skipped = placeable(d, observed_maps(args.game))
    if skipped:
        print(f"skipping {len(skipped)} map(s) with no matrix: {skipped}")
    win = check_windows(d, args.game, ids)
    print(f"check window    {win['total'] - len(win['bad'])}/{win['total']} pass"
          + (f"  FAIL {win['bad'][:3]}" if win["bad"] else ""))
    if win["bad"]:
        return 2
    if render == "3d":
        cov = check_artwork(d, args.game, ids, Field3D(
            d, tile_px=TILE_PX["3d"],
            cam=ds3d_camera.camera_for(args.camera, TILE_PX["3d"])))
        print(f"check artwork   {cov['route'] - cov['bare_route']}/{cov['route']} route tiles "
              f"on rendered geometry, {cov['bare_walkable']} walkable tile(s) bare"
              + (f", {cov['sheer_walkable']} see-through {cov['sheer_behaviour']}"
                 f" in runs { {k: v[:3] for k, v in cov['sheer_runs'].items()} }"
                 if cov["sheer_walkable"] else ""))
        if cov["bare_route"]:
            bad = {k: v for k, v in cov["maps"].items() if v[3]}
            print(f"the route stands on nothing in {bad}", file=sys.stderr)
            return 2
    if args.check:
        return 0

    atlas = build_atlas(d, args.game, ids, render=render, tile_px=args.tile_px,
                        camera=args.camera, light=args.light)
    print(f"wrote {MAPS_ROOT / args.game}/index.json  {len(atlas['maps'])} maps  "
          f"tile_px {atlas['tile_px']}  render {atlas['render']}  "
          f"{atlas['bytes'] / 1024:.0f} KB")
    for k, m in atlas["maps"].items():
        print(f"  {k:8s} {str(m['name']):38s} {m['width']:3d}x{m['height']:<3d} "
              f"origin {m.get('origin', [0, 0])}  {'indoor' if m['indoor'] else 'outdoor'}"
              + ("  CLUSTER (off the region frame)" if m.get("popup") and not m["indoor"] else "")
              + (f"  FELL BACK TO {m['render']}" if m.get("render") else ""))
    if atlas["map_set"].get("fallback"):
        print(f"  {len(atlas['map_set']['fallback'])} map(s) fell back to the silhouette: "
              f"{atlas['map_set']['fallback']}")
    if args.sheet:
        name = f"{args.game}-{atlas['render']}-sheet.png"
        print("sheet:", sheet(d, args.game, ids, SHEETS / name,
                              render=render, tile_px=args.tile_px, camera=args.camera,
                              light=args.light))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
