#!/usr/bin/env python3
"""Map artwork for the Gen 5 cartridges (Black, Black 2) — the collision tier.

WHY THIS IS A SEPARATE MODULE. `scripts/render_dsmaps.py` renders the Gen 4
cartridges, and every byte it reads it is HANDED: pret's Platinum decomp serves
the map-header table, the matrices, the collision grids and the tile-behaviour
enum over plain HTTP, and SoulSilver's cartridge is read with the same field
names because HGSS shares Gen 4's layout. There is no Gen 5 decomp. Nothing
here was handed over; the four tables below were recovered from the cartridge,
and the evidence for each one is written beside it. Keeping that in its own file
is deliberate: a reader must be able to see which claims are pret's and which
are ours, and `render_dsmaps.py` is being rewritten for Platinum's 3D tier in
parallel. The shared constants (CELL, the silhouette palette, `tile_px = 4`) are
duplicated rather than imported for the same reason; folding the two builders
together is follow-up work, not tonight's.

WHAT THE CARTRIDGE SAYS, AND HOW WE KNOW.

  /a/0/1/2  #0   THE ZONE HEADER TABLE. A flat array, 48 bytes per zone, 427
                 zones in Black and 615 in Black 2. Found by size: 20496 bytes
                 is 427 x 48 exactly, and every zone id our runs have ever
                 recorded (up to 398 in Black, 435 in Black 2) is inside its own
                 game's table. The four fields this module reads:

                   +0x04 u16  matrix index into /a/0/0/9
                   +0x18 u16  parent zone  (an interior names the OUTDOOR zone
                              its door is on: Nuvema Town's living room, bedroom
                              and both neighbours' houses all name 389, and
                              Aspertia's three interiors all name 427)
                   +0x24 u32  spawn x    global tiles, same frame as our runs
                   +0x2C u32  spawn y

                 +0x28 is a third coordinate (height), always 0..8 here; the
                 triple is the same (x, height, y) shape the actor block uses in
                 `src/referee/contracts.py`.

  /a/0/0/9       THE MATRICES. 255 in Black, 416 in Black 2.

                   +0x00 u32  type          1 = two planes, 0 = one
                   +0x04 u16  width         in 32x32-tile cells
                   +0x06 u16  height
                   +0x08 u32[w*h]           LAND plane: which /a/0/0/8 chunk
                   +...  u32[w*h]           ZONE plane: which zone owns the cell
                                            (type 1 only; 0xFFFFFFFF = no cell)

                 Matrix #0 is the world: 29 x 27 cells = 928 x 864 tiles, in
                 BOTH games. Every other matrix is one zone's private block,
                 type 0, and every interior our runs entered is 1x1.

  /a/0/0/8       THE LAND CHUNKS, 649 in Black. Each is the 20-byte `WB`
                 container the ds3d spike already identified:

                   +0x00 'WB' u16 version(3)
                   +0x04 u32[4]  section offsets; the last one is the file end

                 Section 0 is the `BMD0` terrain model (the textured tier, not
                 read here). SECTION 1 IS THE COLLISION GRID:

                   +0x00 u16 w, u16 h      always 32 x 32
                   +0x04 8 bytes per tile, row-major, w*h of them:
                           +0 u16 ?        small, multiples of 4/8
                           +2 u16 ?        0..731, one value per tile kind
                           +4 u16 ?
                           +6 u16 flags    BIT 0 SET = you cannot stand here

                 32*32*8 + 4 = 8196, which is exactly the section length for
                 most chunks; the few longer ones carry trailing data this
                 module does not read.

REGISTRATION, AND HOW IT WAS PROVED. A global tile (X, Y) is in world-matrix
cell (X>>5, Y>>5) at local (X&31, Y&31) — the same rule as Gen 4, which was NOT
assumed: the layout was recovered by searching matrix #0 for an (offset, stride,
width) that puts our runs' own zone ids in our runs' own cells, and exactly one
triple in the file does (stride 4, width 29, offset 3140). `--check` re-proves
it two ways, and both are printed with counts:

  1. our runs' samples     every (map id, x, y) our runs recorded resolves,
                           through the matrix, to the map id the run recorded;
  2. the cartridge's own   every zone that owns world cells has its spawn
                           position inside a cell that names that same zone.

(2) is the stronger of the two and is independent of (1): it covers all 40
world zones in Black and all 53 in Black 2, while our runs between them have
only stood in 8 distinct world cells.

WALKABILITY. `--check` also proves that every tile a run stood on is passable in
the grid above. 680 of 689 are, and all 9 exceptions are WARP TILES — a doorway
is flagged impassable because the game warps you off it before you could stand
there, and our sampler catches the frame where you are on it. The exceptions are
listed by name; a tenth would be a real failure.

TIER 2, THE TEXTURED ONE, WHICH IS WHAT SHIPS. Section 0 of each `WB` is the
chunk's terrain model and section 2 is its prop list, and both were recovered
the same way as the tables above.

  PLACEMENTS. `u32 count`, then 16 bytes each:

    +0x00 s32 x      20.12 fixed point, world units, RELATIVE TO CHUNK CENTRE
    +0x04 s32 y      height
    +0x08 s32 z      ... and NEGATED against the terrain model's own z axis
    +0x0C u16 yaw    only the top two bits are ever set: 0/90/180/270 degrees
    +0x0E u16 id     big-endian, 0..~500, an index into this area's prop archive

  The z negation is measured, not assumed — see `check_props` and the note on
  its oracle.

  PROP ARCHIVES. Gen 4 spends ONE area index on two parallel archives, models
  and textures; Gen 5 kept that exactly, with one pair for outdoor props and
  another for indoor ones:

                     models           textures        count
    Black  outdoor   /a/2/2/9         /a/1/7/6           52
    Black  indoor    /a/2/3/0         /a/1/7/7           64
    Black 2 outdoor  /a/2/2/5         /a/1/7/4           70
    Black 2 indoor   /a/2/2/6         /a/1/7/5           93

  Each entry is an `AB` container — `'AB'`, `u16 count`, `count+1` u32 offsets —
  holding N records then N models, paired by position. A record's first u16 is
  the prop id the placement list names, and the model beside it is what to draw.

  THE AREA INDEX comes from the zone header's `u01`, which is also the texture
  set index, and the two uses agree because `/a/0/1/4` is laid out to make them:
  index 0..1 are reserved, then one block of four SEASONS per outdoor area, then
  one set per indoor area. Black has 52 outdoor areas, so its indoor sets start
  at 2 + 4*52 = 210; Black 2 has 70, so its start at 282. Hence

    u01 <  indoor_base :  outdoor area (u01 - 2) // 4,  texture set u01 + season
    u01 >= indoor_base :  indoor  area u01 - indoor_base, texture set u01

  That arithmetic is not a guess about what the numbers mean — `--check` spends
  it and counts how many placements resolve (Black 443 of 444 chunks, Black 2
  649 of 682), and separately proves each interior's texture set is the UNIQUE
  one in `/a/0/1/4` that covers its chunk's texture names.

WHAT THE SILHOUETTE TIER DOES NOT KNOW, and why it is still the fallback. Gen 4
colours a walkable tile by the decomp's name for its behaviour — grass, water,
ground. The +2 halfword in the collision record is almost certainly that, but
there is no Gen 5 enum to name its 731 values and nothing in our runs to fit one
against (Black reaches no wild battle at all), so `--render collision` ships TWO
tones and says so in the atlas. Any map the 3D path cannot draw falls back to it
and the atlas marks that map's `render`.

DOORS, without an event decoder. Gen 4 reads warps out of the decomp's events
files; nothing here decodes Gen 5 events, so there is no list of every door on
a map. What there IS is a record of the doors our runs actually walked through:
the observed graph mints a WARP whenever two consecutive samples are not
adjacent, and a warp whose two ends are on DIFFERENT maps is a door. Same rule
`render_dsmaps.observed_doors` already ships for SoulSilver, from the same
evidence, and the atlas says `"doors": "observed-transitions"` so the cost is
on the label: this finds the doors that were used, not the doors that exist.
See :func:`observed_doors` for the two Gen-5-specific parts — telling a door
from a route seam without a second map matrix to compare, and reading the
door on the far side out of the tile the player LANDED on.

Usage:
    ./venv/bin/python scripts/render_gen5maps.py --check
    ./venv/bin/python scripts/render_gen5maps.py --game black-us
    ./venv/bin/python scripts/render_gen5maps.py --render collision
    ./venv/bin/python scripts/render_gen5maps.py --sheet
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import struct
import sys
from collections import defaultdict
from pathlib import Path
from typing import Iterator, Optional

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ds3d import field as ds3d_field                           # noqa: E402
from ds3d import nsbmd, nsbtx                                   # noqa: E402
from ds3d import scene as ds3d_scene                            # noqa: E402
from ds3d.nitrofs import read_rom, narc_entries                 # noqa: E402

REPO = Path(__file__).resolve().parents[1]
MAPS_ROOT = REPO / "src" / "dashboard" / "web" / "public" / "maps"
OBSERVED = REPO / "artifacts" / "game-map-render" / "observed"
SHEETS = REPO / "artifacts" / "game-map-render"

CELL = 32                  # tiles per matrix cell, both axes
REC = 8                    # bytes per collision-grid tile
PLACE = 16                 # bytes per prop placement

# Pixels per tile, per tier. 16 is the DS's own world units per tile, so the 3D
# tier renders at one pixel per world unit and no texel is invented or thrown
# away. The collision tier is one flat block per tile and 4 loses nothing.
TILE_PX = {"3d": 16, "collision": 4}
DEFAULT_RENDER = "3d"
RENDER_KIND = {"3d": "3d-ortho", "collision": "collision"}
RENDER_NOTE = {
    "3d-ortho": ("the cartridge's own textured 3D field artwork — terrain and "
                 "props — rendered straight down and orthographic at one pixel "
                 "per world unit"),
    "collision": ("collision silhouettes, not a tile render; two tones only "
                  "(passable and wall) because Gen 5 has no decomp to name its "
                  "tile behaviours and nothing in our runs to fit one against"),
}
# 3x is where the diagonal edge of a roof stops stair-stepping.
SUPERSAMPLE = 3

# The same palette `render_dsmaps.py` uses, so the two collision tiers read as
# one system. GROUND is every passable tile: see the docstring on why this tier
# does not split out grass and water.
GROUND = (74, 84, 100, 255)
WALL = (27, 31, 39, 255)
VOID = (0, 0, 0, 0)

ZONE_TABLE = "/a/0/1/2"
MATRICES = "/a/0/0/9"
LAND = "/a/0/0/8"
MAP_TEXTURES = "/a/0/1/4"
# (outdoor models, outdoor textures, indoor models, indoor textures) — the two
# parallel archive pairs one area index is spent on. See the docstring.
PROP_ARCHIVES = {
    "black-us": ("/a/2/2/9", "/a/1/7/6", "/a/2/3/0", "/a/1/7/7"),
    "black2-us": ("/a/2/2/5", "/a/1/7/4", "/a/2/2/6", "/a/1/7/5"),
}
ZONE_STRIDE = 48
Z_MATRIX, Z_PARENT, Z_SPAWN_X, Z_SPAWN_Y = 0x04, 0x18, 0x24, 0x2C

GAMES = {
    "black-us": "roms/Pokemon - Black Version (USA, Europe) (NDSi Enhanced).nds",
    "black2-us": "roms/Pokemon - Black Version 2 (USA, Europe) (NDSi Enhanced).nds",
}


# ----------------------------------------------------------------- the cartridge

class Gen5Rom:
    """One Gen 5 cartridge, with the four tables above read once and held."""

    def __init__(self, game: str) -> None:
        self.game = game
        self.path = REPO / GAMES[game]
        if not self.path.exists():
            raise SystemExit(f"{self.path} is missing; roms/ is gitignored")
        raw = self.path.read_bytes()
        self.sha = hashlib.sha1(raw).hexdigest()
        d, _title, self.code, fat, names = read_rom(str(self.path))

        def narc(p: str) -> list[bytes]:
            s, e = fat[names[p]]
            return narc_entries(d[s:e])

        self._narc = narc
        self.zones_raw = narc(ZONE_TABLE)[0]
        self.matrices = narc(MATRICES)
        self.chunks = narc(LAND)
        self.map_textures = narc(MAP_TEXTURES)
        om, ot, im, it = PROP_ARCHIVES[game]
        self.prop_models = (narc(om), narc(im))
        self.prop_textures = (narc(ot), narc(it))
        # /a/0/1/4 is laid out as: 2 reserved, then 4 seasons per outdoor area,
        # then one set per indoor area. So this is where the indoor sets start,
        # and it is also what tells an outdoor u01 from an indoor one.
        self.indoor_base = 2 + 4 * len(self.prop_models[0])
        self._cache: dict = {}
        self.zone_count = len(self.zones_raw) // ZONE_STRIDE
        if len(self.zones_raw) % ZONE_STRIDE:
            raise SystemExit(f"{game}: zone table is not a whole number of "
                             f"{ZONE_STRIDE}-byte entries ({len(self.zones_raw)})")
        self.world = self.matrix(0)
        if self.world["zone"] is None:
            raise SystemExit(f"{game}: matrix 0 has no zone plane")
        # Which zones own world cells. This, not the matrix index, is what says
        # a zone's coordinates are global: Black 2's zone 355 owns three world
        # cells AND carries a private 1x1 matrix, so keying on the index alone
        # would render it in the wrong frame.
        self.world_zones = {z for z in self.world["zone"] if z != 0xFFFFFFFF}

    # -- the zone header table ------------------------------------------------
    def zone_field(self, zone: int, off: int, fmt: str) -> int:
        if not 0 <= zone < self.zone_count:
            raise KeyError(f"{self.game} has no zone {zone}")
        return struct.unpack_from(fmt, self.zones_raw, zone * ZONE_STRIDE + off)[0]

    def matrix_of(self, zone: int) -> int:
        return self.zone_field(zone, Z_MATRIX, "<H")

    def parent_of(self, zone: int) -> int:
        return self.zone_field(zone, Z_PARENT, "<H")

    def spawn(self, zone: int) -> tuple[int, int]:
        return (self.zone_field(zone, Z_SPAWN_X, "<I"),
                self.zone_field(zone, Z_SPAWN_Y, "<I"))

    def outdoor(self, zone: int) -> bool:
        """True when this zone owns world cells, so its run coordinates are global."""
        return zone in self.world_zones

    # -- the matrices ---------------------------------------------------------
    def matrix(self, index: int) -> dict:
        b = self.matrices[index]
        typ, w, h = struct.unpack_from("<IHH", b, 0)
        n = w * h
        if n == 0:
            raise SystemExit(f"{self.game}: matrix {index} is {w}x{h}")
        per = (len(b) - 8) // n
        if per not in (4, 8) or 8 + per * n != len(b):
            raise SystemExit(f"{self.game}: matrix {index} is {len(b)} bytes for "
                             f"{w}x{h} cells, which is neither one plane nor two")
        land = struct.unpack_from(f"<{n}I", b, 8)
        zone = struct.unpack_from(f"<{n}I", b, 8 + 4 * n) if per == 8 else None
        return {"index": index, "type": typ, "w": w, "h": h, "land": land, "zone": zone}

    def matrix_for(self, zone: int) -> dict:
        return self.world if self.outdoor(zone) else self.matrix(self.matrix_of(zone))

    def cells_of(self, zone: int) -> list[tuple[int, int]]:
        """The matrix cells one zone occupies, as (col, row)."""
        m = self.matrix_for(zone)
        if m["zone"] is None:                 # a private matrix: every cell is ours
            return [(i % m["w"], i // m["w"]) for i in range(m["w"] * m["h"])]
        return [(i % m["w"], i // m["w"]) for i, z in enumerate(m["zone"]) if z == zone]

    def zone_at(self, col: int, row: int) -> Optional[int]:
        m = self.world
        if not (0 <= col < m["w"] and 0 <= row < m["h"]):
            return None
        z = m["zone"][row * m["w"] + col]
        return None if z == 0xFFFFFFFF else z

    # -- the collision grids --------------------------------------------------
    def chunk_grid(self, index: int) -> Optional[np.ndarray]:
        """One land chunk's collision section as (h, w, 4) uint16, or None."""
        if index == 0xFFFFFFFF or index >= len(self.chunks):
            return None
        b = self.chunks[index]
        if b[:2] != b"WB":
            raise SystemExit(f"{self.game}: /a/0/0/8 #{index} is not a WB container")
        offs = struct.unpack_from("<4I", b, 4)
        s1 = b[offs[1]:offs[2]]
        gw, gh = struct.unpack_from("<2H", s1, 0)
        need = 4 + REC * gw * gh
        if gw != CELL or gh != CELL or len(s1) < need:
            raise SystemExit(f"{self.game}: chunk {index} grid is {gw}x{gh} in "
                             f"{len(s1)} bytes, expected {CELL}x{CELL} in >= {need}")
        a = np.frombuffer(s1, dtype="<u2", count=4 * gw * gh, offset=4)
        return a.reshape(gh, gw, 4)

    def cell_grid(self, zone: int, col: int, row: int) -> Optional[np.ndarray]:
        m = self.matrix_for(zone)
        if not (0 <= col < m["w"] and 0 <= row < m["h"]):
            return None
        return self.chunk_grid(m["land"][row * m["w"] + col])


    # -- the artwork --------------------------------------------------------
    def chunk_model(self, index: int):
        """The terrain model in land chunk `index`, or None."""
        key = ("mdl", index)
        if key not in self._cache:
            b = self.chunks[index]
            offs = struct.unpack_from("<4I", b, 4)
            if b[offs[0]:offs[0] + 4] != b"BMD0":
                self._cache[key] = None
            else:
                try:
                    models, _ = nsbmd.load_models(b[offs[0]:offs[1]])
                    self._cache[key] = models[0]
                except Exception:
                    self._cache[key] = None
        return self._cache[key]

    def placements(self, index: int) -> list[tuple]:
        """Section 2 of a land chunk: `(x, y, z, yaw_turns, prop_id)` each.

        x/y/z are world units relative to the chunk's centre, with z ALREADY
        negated into the terrain model's axis — the sign was measured, not
        assumed (`check_props`).
        """
        b = self.chunks[index]
        offs = struct.unpack_from("<4I", b, 4)
        s2 = b[offs[2]:offs[3]]
        if len(s2) < 4:
            return []
        n = struct.unpack_from("<I", s2, 0)[0]
        if 4 + PLACE * n != len(s2):
            return []
        out = []
        for i in range(n):
            e = s2[4 + PLACE * i:4 + PLACE * (i + 1)]
            x, y, z = struct.unpack_from("<3i", e, 0)
            yaw = struct.unpack_from("<H", e, 12)[0]
            out.append((x / 4096.0, y / 4096.0, -z / 4096.0,
                        yaw / 65536.0, (e[14] << 8) | e[15]))
        return out

    def texture_set(self, zone: int, season: int = 0):
        """The map texture set this zone draws its terrain with."""
        u01 = self._u01(zone)
        # Only an outdoor area has four seasonal sets; an interior has one.
        i = u01 + (season if u01 < self.indoor_base else 0)
        key = ("mts", i)
        if key not in self._cache:
            self._cache[key] = nsbtx.TextureSet(self.map_textures[i])
        return self._cache[key]

    def _u01(self, zone: int) -> int:
        return self.zone_field(zone, 0x02, "<H")

    def prop_area(self, zone: int) -> tuple[int, int]:
        """`(which archive pair, index into it)` for one zone's props.

        0 is the outdoor pair and 1 the indoor one. See the docstring for why
        one number out of the zone header answers both this and the texture set.
        """
        u01 = self._u01(zone)
        if u01 < self.indoor_base:
            return 0, (u01 - 2) // 4
        return 1, u01 - self.indoor_base

    def prop_set(self, zone: int):
        """`({prop id: model}, texture set)`, or None when the area is unknown."""
        which, area = self.prop_area(zone)
        models, textures = self.prop_models[which], self.prop_textures[which]
        if not (0 <= area < len(models)) or not (0 <= area < len(textures)):
            return None
        key = ("props", which, area)
        if key not in self._cache:
            raw = models[area]
            if len(raw) < 8 or raw[:2] != b"AB":
                self._cache[key] = None
            else:
                n = struct.unpack_from("<H", raw, 2)[0]
                offs = struct.unpack_from(f"<{n + 1}I", raw, 4)
                items = [raw[offs[i]:offs[i + 1]] for i in range(n)]
                recs = [y for y in items if y[:4] != b"BMD0"]
                mods = [y for y in items if y[:4] == b"BMD0"]
                lut = {}
                for r, mo in zip(recs, mods):
                    if len(r) < 2:
                        continue
                    try:
                        lut[struct.unpack_from("<H", r, 0)[0]] = nsbmd.load_models(mo)[0][0]
                    except Exception:
                        pass
                try:
                    ts = nsbtx.TextureSet(textures[area])
                except Exception:
                    self._cache[key] = None
                else:
                    self._cache[key] = (lut, ts)
        return self._cache[key]


def blocked_plane(grid: np.ndarray) -> np.ndarray:
    """Bit 0 of each tile's flags halfword: set means you cannot stand here."""
    return (grid[..., 3] & 1).astype(bool)


# ----------------------------------------------------------------- the window

class Window:
    """The rectangle one map occupies, in the coordinates its runs report."""

    def __init__(self, key, zone, indoor, ox, oy, w, h, cells, c0, r0,
                 cw, ch, grid, defined, crop):
        self.key, self.zone, self.indoor = key, zone, indoor
        self.ox, self.oy, self.w, self.h = ox, oy, w, h
        self.cells, self.c0, self.r0 = cells, c0, r0
        self.cw, self.ch = cw, ch
        self.grid, self.defined, self.crop = grid, defined, crop

    @property
    def blocked(self) -> np.ndarray:
        return blocked_plane(self.grid)

    @property
    def walkable(self) -> int:
        return int((self.defined & ~self.blocked).sum())


def map_window(rom: Gen5Rom, zone: int) -> Window:
    """One map's rectangle and its collision grid, stitched from its cells."""
    cells = rom.cells_of(zone)
    if not cells:
        raise SystemExit(f"{rom.game}: zone {zone} occupies no matrix cell")
    c0 = min(c for c, _ in cells)
    r0 = min(r for _, r in cells)
    cw = max(c for c, _ in cells) - c0 + 1
    ch = max(r for _, r in cells) - r0 + 1
    w, h = cw * CELL, ch * CELL

    grid = np.zeros((h, w, 4), dtype=np.uint16)
    defined = np.zeros((h, w), dtype=bool)
    for col, row in cells:
        g = rom.cell_grid(zone, col, row)
        if g is None:
            continue
        y0, x0 = (row - r0) * CELL, (col - c0) * CELL
        grid[y0:y0 + CELL, x0:x0 + CELL] = g
        # An ALL-ZERO record is the cartridge saying "nothing is here", and it
        # is not the same thing as a floor: bit 0 of its flags is clear, so read
        # as collision it is passable, and Route 1's gate house would ship a
        # 32x32 rectangle of walkable void with an 18x11 room in the corner.
        # Only a chunk that uses this fill has any (in our map set, only Black's
        # zone 320 — every other interior walls its surround instead), and no
        # tile any run has stood on is one, across all 689 samples.
        defined[y0:y0 + CELL, x0:x0 + CELL] = g.any(axis=2)

    outdoor = rom.outdoor(zone)
    ox, oy = (c0 * CELL, r0 * CELL) if outdoor else (0, 0)
    crop = (0, 0)

    if not outdoor:
        # A room does not fill its 32x32 block; the rest of it is flagged
        # impassable with the same bytes the room's own walls carry, so nothing
        # tells wall from nothing tile by tile. What does is that an interior is
        # SEALED — you leave it through a warp, never over an edge — so its
        # passable floor is bounded, and the wall ring is the one tile around
        # it. Drawn without this crop, Nuvema Town's living room is a small room
        # in the corner of a 32x32 field of black.
        walk = np.argwhere(defined & ~blocked_plane(grid))
        if walk.size:
            (by0, bx0), (by1, bx1) = walk.min(0), walk.max(0)
            by0, bx0 = max(0, by0 - 1), max(0, bx0 - 1)
            by1, bx1 = min(h - 1, by1 + 1), min(w - 1, bx1 + 1)
            grid = grid[by0:by1 + 1, bx0:bx1 + 1]
            defined = defined[by0:by1 + 1, bx0:bx1 + 1]
            ox, oy, crop = int(bx0), int(by0), (int(bx0), int(by0))
            h, w = grid.shape[:2]

    return Window(f"{zone}:0", zone, not outdoor, ox, oy, w, h,
                  cells, c0, r0, cw, ch, grid, defined, crop)


def silhouette(win: Window) -> np.ndarray:
    """One flat tone per tile: passable, wall, or nothing at all."""
    rgba = np.empty((win.h, win.w, 4), dtype=np.uint8)
    rgba[...] = WALL
    rgba[win.defined & ~win.blocked] = GROUND
    rgba[~win.defined] = VOID
    return rgba


class Field3D:
    """Tier 2: the cartridge's own artwork, straight down and orthographic.

    The camera is straight down and orthographic DELIBERATELY, the same choice
    `render_dsmaps.py` makes for Gen 4: the game's own angled field camera
    z-buffers correctly and therefore hides route behind buildings, which is the
    one thing a map tier may not do.

    `pixels` returns None for a map the 3D path cannot draw, and the caller
    falls back to the silhouette and records that in the atlas.
    """

    def __init__(self, rom: Gen5Rom, *, tile_px: int, supersample: int = SUPERSAMPLE,
                 season: int = 0) -> None:
        self.rom, self.tile_px, self.ss, self.season = rom, tile_px, supersample, season
        self.stats: dict[str, dict] = {}

    def scene(self, win: Window) -> tuple[Optional["ds3d_scene.Scene"], dict]:
        rom = self.rom
        st = {"terrain": 0, "props": 0, "missing": 0, "cells": 0}
        try:
            mapts = rom.texture_set(win.zone, self.season)
        except Exception:
            return None, st
        ps = rom.prop_set(win.zone)
        sc = ds3d_scene.Scene()
        m = rom.matrix_for(win.zone)
        for col, row in sorted(win.cells):
            land = m["land"][row * m["w"] + col]
            model = rom.chunk_model(land) if land < len(rom.chunks) else None
            if model is None:
                continue
            base = ds3d_field.chunk_origin(col, row, win.c0, win.r0)
            st["cells"] += 1
            st["terrain"] += sc.add_model(model, mapts, origin=base)
            for x, y, z, turns, pid in rom.placements(land):
                mo = ps[0].get(pid) if ps else None
                if mo is None:
                    st["missing"] += 1
                    continue
                st["props"] += sc.add_model(
                    mo, ps[1], origin=(base[0] + x, base[1] + y, base[2] + z),
                    yaw=turns * 2.0 * np.pi)
        return (sc if sc.tris else None), st

    def pixels(self, win: Window) -> Optional[np.ndarray]:
        sc, st = self.scene(win)
        self.stats[win.key] = st
        if sc is None:
            return None
        full = ds3d_field.ortho_pixels(sc, win.cw * CELL, win.ch * CELL,
                                       self.tile_px, self.ss)
        bx0, by0 = win.crop
        t = self.tile_px
        return full[by0 * t:(by0 + win.h) * t, bx0 * t:(bx0 + win.w) * t]


def to_png(rgba: np.ndarray, path: Path, tile_px: int) -> None:
    """The map's own rectangle, `tile_px` PNG pixels per tile. Map-local.

    `tile_px` is 1 when the caller already rendered at the final density, which
    the 3D tier does — it rasterises at one pixel per world unit rather than
    expanding flat blocks.
    """
    block = (rgba if tile_px == 1 else
             np.repeat(np.repeat(rgba, tile_px, axis=0), tile_px, axis=1))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        Image.fromarray(block, "RGBA").save(tmp, "PNG", optimize=True)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def border_png(path: Path, tile_px: int) -> None:
    """The one-tile block the viewer would tile outside a map.

    Gen 5 has no border block — a world cell no zone claims is nothing at all —
    so this is that nothing, as one transparent tile. `borders.js` lists no DS
    map, so the viewer never tiles it; it is here so the atlas's claim is true
    rather than absent.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.zeros((tile_px, tile_px, 4), dtype=np.uint8), "RGBA").save(path, "PNG")


def open_edges(rom: Gen5Rom, win: Window) -> list[dict]:
    """Where this map's edge meets another map, in the map's own tiles.

    Derived from the world matrix, not from a connection table: adjacent Gen 5
    zones are already touching.
    """
    if win.indoor:
        return []
    cells = set(win.cells)
    cmax = max(c for c, _ in cells)
    rmax = max(r for _, r in cells)
    spans: list[dict] = []
    for side, (dc, dr) in (("up", (0, -1)), ("down", (0, 1)),
                           ("left", (-1, 0)), ("right", (1, 0))):
        runs: list[tuple[int, int]] = []
        for col, row in sorted(cells):
            if side == "up" and row != win.r0:
                continue
            if side == "down" and row != rmax:
                continue
            if side == "left" and col != win.c0:
                continue
            if side == "right" and col != cmax:
                continue
            nb = rom.zone_at(col + dc, row + dr)
            if nb is None or nb == win.zone:
                continue
            along = (col - win.c0) if side in ("up", "down") else (row - win.r0)
            runs.append((along * CELL, along * CELL + CELL))
        for lo, hi in sorted(runs):
            if spans and spans[-1]["side"] == side and spans[-1]["to"] == lo:
                spans[-1]["to"] = hi
            else:
                spans.append({"side": side, "from": lo, "to": hi})
    return spans


# ----------------------------------------------------------------- the map set

def observed(game: str) -> dict:
    return json.loads((OBSERVED / f"{game}-observed.json").read_text())


def map_ids(game: str) -> list[int]:
    """Every map id our runs' samples name, which is what the atlas must cover."""
    return sorted({int(n.split("|")[0]) for n in observed(game)["nodes"]})


def samples(game: str) -> Iterator[tuple[int, int, int]]:
    for node in observed(game)["nodes"]:
        z, x, y = node.split("|")
        yield int(z), int(x), int(y)


def warp_tiles(game: str) -> set[str]:
    out: set[str] = set()
    for a, b, _n in observed(game)["warps"]:
        out.add(a)
        out.add(b)
    return out


def cross_map_warps(game: str) -> list[tuple[tuple[int, int, int], tuple[int, int, int]]]:
    """The observed warps that CROSS maps, as `((src, x, y), (dst, x, y))`.

    Most warps do not. A staircase, a warp pad, the drop off a ledge — anything
    the field code resolves by teleporting rather than stepping — mints a warp
    between two tiles of the SAME map, and Black 2's `427|36|715 -> 427|36|718`
    is one of those, not a door. They are dropped here so nothing downstream
    has to remember to.
    """
    out = []
    for a, b, _n in observed(game)["warps"]:
        az, ax, ay = (int(v) for v in a.split("|"))
        bz, bx, by = (int(v) for v in b.split("|"))
        if az == bz:
            continue
        out.append(((az, ax, ay), (bz, bx, by)))
    return out


def observed_doors(game: str, indoor, blocked=lambda _m, _x, _y: False
                   ) -> dict[int, list[tuple[int, int, int, str]]]:
    """`{map_id: [(x, y, dest_map_id, via)]}` — the doors our runs walked.

    `indoor(map_id)` says whether a map is an interior; it is the atlas's own
    flag, read off the cartridge's zone table, and it decides the direction of
    every door here. The coordinate frames FOLLOW from it rather than deciding
    it: an outdoor tile is global (Black 2's world map runs x 32-63, y 704-767)
    and an interior tile is local to its own rectangle, so the two are not
    separable by magnitude — Black 2's world map starts at global x 32, the
    lowest x a run stood on there is 36, and the largest interior coordinate
    in the game is 19. `build_atlas` bounds-checks every tile emitted
    against the rectangle of the map it is written on, which is the check that
    a reversed direction would actually fail.

    A CROSS-MAP WARP IS NOT AUTOMATICALLY A DOOR. Walking north out of Black's
    map 317 onto 397 changes the map id and opens nothing: they are neighbours
    on the region matrix and the seam between them is already an `open` span.
    SoulSilver's version of this rule compares the two maps' map-matrix ids;
    Gen 5 gives every outdoor field map the same matrix (`matrix_000`), so the
    same rule is spelled here as "at least one end is an interior", which on
    that matrix is the same sentence.

    HALF A DOOR. A warp is directed, and our runs walked plenty of doors one
    way only — every run STARTS inside the player's house, so Black's 390 and
    Black 2's 428 were left and never entered, and the marker that would open
    them is exactly the one nobody walked. Rather than leave those interiors
    unreachable, the missing direction is read off the tile the player LANDED
    on coming the other way: a Gen 5 door warp puts you ON the door. `via` says
    which end a tile came from, `"walked"` or `"landed"`.

    WHICH CANDIDATE, when a pair has both. `blocked(map_id, x, y)` is the
    cartridge's own collision bit, and it is the tie-break, because an outdoor
    door tile is IMPASSABLE — you never walk onto a door, the field code warps
    you off it first, which is the same fact `check_walkable` already has to
    make an exception for. Measured over Black and Black 2's cross-map warps:
    every one of the seven outdoor-side landing tiles is blocked, while two
    of the six outdoor-side walked tiles are not — those two are a sample taken a
    step short of the door, and taking the walked tile there would hang the
    marker in the street (Black's 397 -> 398 is one tile diagonally out). So a
    blocked candidate wins; a walked one wins over a landed one; and a pair
    with neither blocked nor walked falls back to where the run landed.

    A pair with no blocked candidate at all keeps every walked tile, which is
    how Black's 390 keeps both of the mat tiles its runs left by.
    """
    walked: dict[tuple[int, int], set[tuple[int, int]]] = defaultdict(set)
    landed: dict[tuple[int, int], set[tuple[int, int]]] = defaultdict(set)
    for (az, ax, ay), (bz, bx, by) in cross_map_warps(game):
        if not indoor(az) and not indoor(bz):
            continue
        walked[(az, bz)].add((ax, ay))
        # ...and the tile it put the player on is where the door BACK is.
        landed[(bz, az)].add((bx, by))

    out: dict[int, list[tuple[int, int, int, str]]] = defaultdict(list)
    for pair in sorted(set(walked) | set(landed)):
        src, dst = pair
        by_via = [("walked", sorted(walked.get(pair, ()))),
                  ("landed", sorted(landed.get(pair, ())))]
        pick = next(((via, [t for t in tiles if blocked(src, *t)])
                     for via, tiles in by_via if any(blocked(src, *t) for t in tiles)),
                    next(((via, tiles) for via, tiles in by_via if tiles)))
        via, tiles = pick
        for x, y in tiles:
            out[src].append((x, y, dst, via))
    return {m: sorted(v) for m, v in out.items()}


def floor_groups(doors: dict[int, list[tuple[int, int, int, str]]], indoor
                 ) -> dict[int, int]:
    """`{interior_id: the id its building is named after}`.

    A cartridge ships map ids and no names, so the name-prefix rule that groups
    Platinum's floors has nothing to work on. The floors are still groupable,
    from the SHAPE of the doors: an interior whose door leads to another
    INTERIOR is a floor of that same building. Black's 391 opens onto 390 and
    nothing else, so the two are one house and open as one popup — which is
    what the viewer's `floors` is for — instead of two unrelated rooms.

    The building is named after its LOWEST floor, not whichever room the union
    happened to root on: 390 is the ground floor the front door opens into and
    391 is upstairs.
    """
    parent: dict[int, int] = {}

    def find(k: int) -> int:
        parent.setdefault(k, k)
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    for src, entries in doors.items():
        if not indoor(src):
            continue
        parent.setdefault(src, src)
        for _x, _y, dst, _via in entries:
            if indoor(dst):
                parent[find(dst)] = find(src)
    comps: dict[int, list[int]] = defaultdict(list)
    for k in parent:
        comps[find(k)].append(k)
    return {k: min(members) for members in comps.values() for k in members}


# ----------------------------------------------------------------- the atlas

def render_map(rom: Gen5Rom, win: Window,
               art: Optional[Field3D]) -> tuple[np.ndarray, str]:
    """One map's pixels: the 3D tier, or the silhouette it falls back to."""
    if art is not None:
        px = art.pixels(win)
        if px is not None:
            return px, "3d-ortho"
    return silhouette(win), "collision"


SEASONS = ("spring", "summer", "autumn", "winter")


def build_atlas(rom: Gen5Rom, *, render: str = DEFAULT_RENDER,
                tile_px: Optional[int] = None, season: int = 0,
                write: bool = True) -> dict:
    tile_px = TILE_PX[render] if tile_px is None else tile_px
    render_kind = RENDER_KIND[render]
    art = Field3D(rom, tile_px=tile_px, season=season) if render == "3d" else None
    ids = map_ids(rom.game)
    out = MAPS_ROOT / rom.game
    windows = {i: map_window(rom, i) for i in ids}

    # The doors, from our runs rather than from an event archive. See
    # `observed_doors`; a map we did not render cannot be a destination.
    def _blocked(i: int, x: int, y: int) -> bool:
        w = windows.get(i)
        if w is None or not (w.ox <= x < w.ox + w.w and w.oy <= y < w.oy + w.h):
            return False
        return bool(w.blocked[y - w.oy, x - w.ox])

    doors = {m: [d for d in v if d[2] in windows]
             for m, v in observed_doors(
                 rom.game,
                 lambda i: windows[i].indoor if i in windows else True,
                 _blocked).items()
             if m in windows}
    doors = {m: v for m, v in doors.items() if v}
    groups = floor_groups(doors, lambda i: windows[i].indoor)
    building_of = {i: f"Map{groups.get(i, i)}" for i in ids if windows[i].indoor}
    floors_of: dict[str, list[str]] = defaultdict(list)
    for i, b in building_of.items():
        floors_of[b].append(windows[i].key)

    maps: dict[str, dict] = {}
    for map_id, win in sorted(windows.items()):
        png = f"{map_id}-0.png"
        border = f"{map_id}-0-border.png"
        rgba, kind = render_map(rom, win, art)
        if write:
            to_png(rgba, out / png, 1 if kind == "3d-ortho" else tile_px)
            border_png(out / border, tile_px)
        entry: dict = {
            # No Gen 5 decomp means no header constant to name a map with, and
            # the cartridge's own location names are in an archive nothing here
            # decodes. Null is what SoulSilver's atlas ships for the same reason.
            "name": None,
            "width": win.w,
            "height": win.h,
            "file": png,
            "bytes": (out / png).stat().st_size if write else 0,
            "indoor": win.indoor,
            "border": {"file": border, "w": 1, "h": 1,
                       "bytes": (out / border).stat().st_size if write else 0},
            "walkable": win.walkable,
        }
        if kind != render_kind:
            entry["render"] = kind
        if win.ox or win.oy:
            entry["origin"] = [win.ox, win.oy]
            entry["png_origin"] = [win.ox, win.oy]
        if not win.indoor:
            entry["world"] = [win.ox, win.oy]
            entry["frame"] = "matrix_000"
            spans = open_edges(rom, win)
            if spans:
                entry["open"] = spans
        else:
            # An interior belongs to a building, so the viewer can walk into it
            # from the door below and back out again, and two floors of one
            # house open as one popup rather than two unrelated rooms. Which
            # rooms share a house is read off the doors — see `floor_groups`.
            entry["building"] = building_of[map_id]
            entry["floors"] = sorted(floors_of[building_of[map_id]])
            entry["popup"] = True

        # A door is written on the map it is ON, in that map's own frame: a
        # world tile is global and an interior tile is local, which is exactly
        # the frame the route's own steps are in, so `mapatlas.drawWindow`
        # subtracts the same `origin` from both. An interior spells its doors
        # `exits` because it has no marker to draw — you are already inside.
        here = []
        for x, y, dest_id, via in doors.get(map_id, []):
            if not (win.ox <= x < win.ox + win.w and win.oy <= y < win.oy + win.h):
                raise ValueError(
                    f"{rom.game}: door {map_id}({x},{y}) -> {dest_id} is outside "
                    f"map {map_id}'s own rectangle x[{win.ox},{win.ox + win.w}) "
                    f"y[{win.oy},{win.oy + win.h}) — the frames are crossed")
            here.append({"x": x, "y": y, "to": windows[dest_id].key,
                         **({} if win.indoor
                            else {"building": building_of[dest_id]}),
                         # Provenance per door, because half of these were only
                         # ever walked one way: `walked` is a tile a run left
                         # from, `landed` a tile a run arrived on coming back.
                         "via": via})
        if here:
            entry["exits" if win.indoor else "doors"] = here
        maps[win.key] = entry

    atlas = {
        "schema": 2,
        "game": rom.game,
        "key_shape": "id",
        "tile_px": tile_px,
        "camera": "topdown",
        "render": render_kind,
        "png_frame": "map-local",
        "source": {"kind": "rom", "file": rom.path.name, "sha": rom.sha},
        "walkgraph": {"version": None, "source": None},
        # The same token SoulSilver's atlas ships, for the same reason and from
        # the same evidence: no event archive is decoded, so a door is a map
        # transition one of our runs actually made.
        "doors": "observed-transitions",
        "map_set": {
            "kind": "observed",
            "source": (f"artifacts/game-map-render/observed/{rom.game}-observed.json"
                       " + every map id our runs' samples name"),
            "note": (RENDER_NOTE[render_kind]
                     + "; PNG pixel (0,0) is tile png_origin, so a source rect "
                       "subtracts it"),
            "doors": ("observed transitions: Gen 5 event data is not decoded, so "
                      "a door is a warp our runs walked between two DIFFERENT "
                      "maps with at least one of them an interior — a same-map "
                      "warp is a staircase and two outdoor maps share matrix_000, "
                      "so their seam is an `open` span. These are the doors that "
                      "were used, not the doors that exist. A direction nobody "
                      "walked is read off the tile the player landed on coming "
                      "the other way and marked `via: landed`, which is what makes "
                      "the house every run STARTS in reachable at all."),
        },
        "bytes": sum(m["bytes"] + m["border"]["bytes"] for m in maps.values()),
        "maps": maps,
    }
    if render == "3d":
        # Gen 5 draws every outdoor area four ways and the cartridge carries all
        # four texture sets. The artwork is one of them, not a neutral blend, so
        # the atlas names which — measured mean pixel delta between spring and
        # the other three is 14-18 on Black's own maps, i.e. plainly visible.
        atlas["season"] = SEASONS[season]
    fell_back = sorted(k for k, m in maps.items() if m.get("render"))
    if fell_back:
        atlas["map_set"]["fallback"] = fell_back
    if write:
        out.mkdir(parents=True, exist_ok=True)
        tmp = out / ".index.json.tmp"
        tmp.write_text(json.dumps(atlas, indent=1) + "\n")
        os.replace(tmp, out / "index.json")
    return atlas


# ----------------------------------------------------------------- the checks

def check_registration(rom: Gen5Rom) -> dict:
    """Both halves, with counts. See the module docstring on why there are two."""
    run_ok, run_bad = 0, []
    for z, x, y in samples(rom.game):
        cx, cy = x >> 5, y >> 5
        if rom.outdoor(z):
            got = rom.zone_at(cx, cy)
            if got == z:
                run_ok += 1
            else:
                run_bad.append((z, x, y, f"world cell ({cx},{cy}) names {got}"))
        else:
            m = rom.matrix_for(z)
            if cx < m["w"] and cy < m["h"]:
                run_ok += 1
            else:
                run_bad.append((z, x, y, f"outside its {m['w']}x{m['h']} matrix"))

    spawn_ok, spawn_bad = 0, []
    for z in sorted(rom.world_zones):
        x, y = rom.spawn(z)
        got = rom.zone_at(x >> 5, y >> 5)
        if got == z:
            spawn_ok += 1
        else:
            spawn_bad.append((z, x, y, f"cell names {got}"))

    cells = len({(x >> 5, y >> 5) for z, x, y in samples(rom.game) if rom.outdoor(z)})
    return {"run_ok": run_ok, "run_bad": run_bad, "run_cells": cells,
            "spawn_ok": spawn_ok, "spawn_bad": spawn_bad,
            "world_zones": len(rom.world_zones)}


def check_chunk_names(rom: Gen5Rom) -> dict:
    """The cartridge naming its own cells, which is the best evidence there is.

    Every land chunk's terrain model carries a name, and for a world chunk that
    name is `map<col>_<row>` — Nuvema Town's is `map24_23`. Nothing in this
    module derived those two numbers; they were written by the people who built
    the game. So this compares a string in the ROM's model header against the
    cell our matrix decode put that chunk in, once per occupied cell, and it is
    the check that would survive even if every run we have were deleted.

    A chunk with no model, or one named for a shared piece of scenery rather
    than a position (`map_outtree`, which Black 2 reuses across three cells), is
    counted separately and asserts nothing.
    """
    m = rom.world
    named = mismatch = unpositioned = nomodel = 0
    bad = []
    for i, z in enumerate(m["zone"]):
        if z == 0xFFFFFFFF:
            continue
        col, row = i % m["w"], i // m["w"]
        b = rom.chunks[m["land"][i]]
        offs = struct.unpack_from("<4I", b, 4)
        if b[offs[0]:offs[0] + 4] != b"BMD0":
            nomodel += 1
            continue
        try:
            models, _ = nsbmd.load_models(b[offs[0]:offs[1]])
        except Exception:
            nomodel += 1
            continue
        hit = re.fullmatch(r"map(\d+)_(\d+)", models[0].name)
        if not hit:
            unpositioned += 1
            continue
        if (int(hit.group(1)), int(hit.group(2))) == (col, row):
            named += 1
        else:
            mismatch += 1
            bad.append((col, row, models[0].name))
    return {"named": named, "mismatch": mismatch, "bad": bad,
            "unpositioned": unpositioned, "nomodel": nomodel}


def check_props(rom: Gen5Rom, *, flip_z: bool = False) -> dict:
    """Prop placement, proved against the collision grid the props stand on.

    THE ORACLE. A building is a wall. The collision grid already says, tile by
    tile, where this map's walls are, and it was recovered from a different
    section of a different file than the placement list — so "the pixels a prop
    drew land on a tile the cartridge calls impassable" is a real check and not
    a restatement. It cannot be 100%: a roof overhangs its walls, and a sign or
    a plant stands on walkable ground on purpose. What it can do is separate a
    correct axis convention from a wrong one, which is exactly what it was for.

    `flip_z` is the control. Section 2 stores z against the terrain model's own
    axis, and reading it as stored puts Nuvema Town's three houses on the grass
    beside the three house-shaped holes in the collision grid. Measured over
    the shipped map set, negating z takes the fraction of prop pixels standing
    on a wall from about half to about nine tenths; `--check` prints both, so
    the number that justifies the sign is never more than one run away.
    """
    on_wall = total = 0
    resolved = missing = 0
    for map_id in map_ids(rom.game):
        win = map_window(rom, map_id)
        m = rom.matrix_for(map_id)
        ps = rom.prop_set(map_id)
        try:
            mapts = rom.texture_set(map_id)
        except Exception:
            continue
        for col, row in sorted(win.cells):
            land = m["land"][row * m["w"] + col]
            if land >= len(rom.chunks):
                continue
            model = rom.chunk_model(land)
            if model is None:
                continue
            places = rom.placements(land)
            if not places:
                continue
            base = ds3d_field.chunk_origin(col, row, col, row)

            def frame(with_props: bool) -> np.ndarray:
                sc = ds3d_scene.Scene()
                sc.add_model(model, mapts, origin=base)
                if with_props:
                    for x, y, z, turns, pid in places:
                        mo = ps[0].get(pid) if ps else None
                        if mo is None:
                            continue
                        zz = -z if flip_z else z
                        sc.add_model(mo, ps[1],
                                     origin=(base[0] + x, base[1] + y, base[2] + zz),
                                     yaw=turns * 2.0 * np.pi)
                return ds3d_field.ortho_pixels(sc, CELL, CELL, 16, 1)

            for _x, _y, _z, _t, pid in places:
                if ps and pid in ps[0]:
                    resolved += 1
                else:
                    missing += 1
            bare, dressed = frame(False), frame(True)
            drew = np.abs(bare.astype(int) - dressed.astype(int)).sum(2) > 12
            tiles = drew.reshape(CELL, 16, CELL, 16).mean((1, 3)) > 0.35
            blocked = blocked_plane(rom.cell_grid(map_id, col, row))
            on_wall += int((tiles & blocked).sum())
            total += int(tiles.sum())
    return {"on_wall": on_wall, "total": total, "resolved": resolved,
            "missing": missing,
            "pct": (100.0 * on_wall / total) if total else 0.0}


def check_artwork(rom: Gen5Rom, *, tile_px: int = 16) -> dict:
    """The 3D tier's own registration check: the route stands on the artwork.

    The window check proves a walked tile is inside the map's RECTANGLE. It
    cannot see the failure only this tier can have: a terrain mesh drawn half a
    chunk out, or a chunk left out of a multi-cell stitch, still fills a correct
    rectangle — with a hole where the route runs. A route tile over nothing is
    drawn on the page background, which reads as a map with a bite out of it and
    raises nothing.

    Opaque, not merely present, for the same reason Gen 4 measures it that way.
    """
    art = Field3D(rom, tile_px=tile_px)
    walked: dict[int, list] = defaultdict(list)
    for z, x, y in samples(rom.game):
        walked[z].append((x, y))
    out = {"bare_walkable": 0, "walkable": 0, "bare_route": 0, "route": 0,
           "maps": 0, "bare": []}
    for map_id in map_ids(rom.game):
        win = map_window(rom, map_id)
        px = art.pixels(win)
        if px is None:
            continue
        out["maps"] += 1
        cov = px[..., 3].reshape(win.h, tile_px, win.w, tile_px).mean((1, 3)) >= 128
        walk = win.defined & ~win.blocked
        out["bare_walkable"] += int((walk & ~cov).sum())
        out["walkable"] += int(walk.sum())
        for x, y in walked.get(map_id, []):
            out["route"] += 1
            if not cov[y - win.oy, x - win.ox]:
                out["bare_route"] += 1
                out["bare"].append((map_id, x, y))
    return out


def check_walkable(rom: Gen5Rom) -> dict:
    """Every tile a run stood on is passable, modulo warp tiles."""
    warps = warp_tiles(rom.game)
    ok, flagged = 0, []
    for z, x, y in samples(rom.game):
        m = rom.matrix_for(z)
        cx, cy = x >> 5, y >> 5
        if not (cx < m["w"] and cy < m["h"]):
            continue
        g = rom.cell_grid(z, cx, cy)
        if g is None:
            continue
        if g[y & 31, x & 31, 3] & 1:
            flagged.append((f"{z}|{x}|{y}", f"{z}|{x}|{y}" in warps))
        else:
            ok += 1
    return {"ok": ok, "flagged": flagged,
            "flagged_nonwarp": [t for t, w in flagged if not w]}


def check_windows(rom: Gen5Rom) -> dict:
    """Every tile a run stood on falls inside the window the atlas ships.

    The check the interior crop needs: a route tile outside its own map's
    rectangle is drawn over nothing, and the viewer reports no error for it.
    """
    windows = {i: map_window(rom, i) for i in map_ids(rom.game)}
    total, bad = 0, []
    for z, x, y in samples(rom.game):
        w = windows.get(z)
        if w is None:
            continue
        total += 1
        if not (w.ox <= x < w.ox + w.w and w.oy <= y < w.oy + w.h):
            bad.append((z, x, y, [w.ox, w.oy, w.w, w.h]))
    return {"total": total, "bad": bad}


def run_checks(games: list[str]) -> int:
    rc = 0
    for game in games:
        rom = Gen5Rom(game)
        reg = check_registration(rom)
        nm = check_chunk_names(rom)
        walk = check_walkable(rom)
        props = check_props(rom)
        props_flipped = check_props(rom, flip_z=True)
        art = check_artwork(rom)
        win = check_windows(rom)
        print(f"=== {game}  ({rom.code}, sha1 {rom.sha[:12]})")
        print(f"    zones {rom.zone_count}   matrices {len(rom.matrices)}   "
              f"land chunks {len(rom.chunks)}   world {rom.world['w']}x{rom.world['h']} cells")
        print(f"    registration, our runs      : {reg['run_ok']} pass, "
              f"{len(reg['run_bad'])} fail  "
              f"({reg['run_cells']} distinct world cells)")
        print(f"    registration, cartridge     : {reg['spawn_ok']} pass, "
              f"{len(reg['spawn_bad'])} fail  "
              f"(all {reg['world_zones']} world zones)")
        print(f"    registration, chunk names   : {nm['named']} pass, "
              f"{nm['mismatch']} fail  "
              f"({nm['unpositioned']} shared-scenery, {nm['nomodel']} no model)")
        print(f"    walked tiles passable       : {walk['ok']}, "
              f"{len(walk['flagged'])} flagged "
              f"({len(walk['flagged_nonwarp'])} of them not warp tiles)")
        print(f"    walked tiles inside window  : "
              f"{win['total'] - len(win['bad'])} pass, {len(win['bad'])} fail")
        print(f"    props resolved to a model   : {props['resolved']}, "
              f"{props['missing']} unresolved")
        print(f"    route tiles on real geometry: "
              f"{art['route'] - art['bare_route']}/{art['route']}   "
              f"(walkable tiles bare: {art['bare_walkable']}/{art['walkable']}, "
              f"{art['maps']} maps drawn in 3D)")
        print(f"    prop pixels standing on wall: {props['on_wall']}/{props['total']} "
              f"({props['pct']:.1f}%)   CONTROL z as stored: "
              f"{props_flipped['on_wall']}/{props_flipped['total']} "
              f"({props_flipped['pct']:.1f}%)")
        for label, rows in (("registration/run", reg["run_bad"]),
                            ("registration/spawn", reg["spawn_bad"]),
                            ("window", win["bad"])):
            for r in rows[:8]:
                print(f"      FAIL {label}: {r}")
        if walk["flagged"]:
            print("      warp tiles flagged impassable (expected): "
                  + ", ".join(t for t, _ in walk["flagged"]))
        for r in nm["bad"][:8]:
            print(f"      FAIL chunk name: cell {r[0]},{r[1]} holds model {r[2]!r}")
        if (reg["run_bad"] or reg["spawn_bad"] or win["bad"]
                or walk["flagged_nonwarp"] or nm["mismatch"]
                or props["pct"] <= props_flipped["pct"] or art["bare_route"]):
            rc = 1
    return rc


def write_sheet(games: list[str]) -> int:
    """Every shipped map at real size with a real run's route drawn over it.

    The question this answers is the one no assertion can: is the artwork still
    readable UNDER a route, and does the route look like someone walking down a
    street rather than through a wall.
    """
    from PIL import ImageDraw

    SHEETS.mkdir(parents=True, exist_ok=True)
    for game in games:
        atlas_path = MAPS_ROOT / game / "index.json"
        if not atlas_path.is_file():
            print(f"{game}: no atlas yet", file=sys.stderr)
            continue
        atlas = json.loads(atlas_path.read_text())
        tpx = atlas["tile_px"]
        pts: dict[str, list] = defaultdict(list)
        for z, x, y in samples(game):
            pts[f"{z}:0"].append((x, y))
        tiles = []
        for key, mp in sorted(atlas["maps"].items(),
                              key=lambda kv: -kv[1]["width"] * kv[1]["height"]):
            im = Image.open(MAPS_ROOT / game / mp["file"]).convert("RGBA")
            dr = ImageDraw.Draw(im, "RGBA")
            ox, oy = mp.get("png_origin", [0, 0])
            for x, y in pts.get(key, []):
                px, py = (x - ox) * tpx, (y - oy) * tpx
                dr.rectangle([px, py, px + tpx - 1, py + tpx - 1],
                             fill=(255, 92, 58, 210))
            cap = Image.new("RGBA", (im.width, im.height + 16), (18, 20, 26, 255))
            cap.paste(im, (0, 16), im)
            ImageDraw.Draw(cap).text(
                (3, 4), f"{key}  {mp['width']}x{mp['height']}  "
                        f"{mp.get('render', atlas['render'])}",
                fill=(222, 226, 235, 255))
            tiles.append(cap)
        cols = 3
        rows = (len(tiles) + cols - 1) // cols
        cw = [0] * cols
        rh = [0] * rows
        for i, t in enumerate(tiles):
            cw[i % cols] = max(cw[i % cols], t.width)
            rh[i // cols] = max(rh[i // cols], t.height)
        sheet = Image.new("RGBA", (sum(cw) + 12 * (cols + 1),
                                   sum(rh) + 12 * (rows + 1)), (10, 11, 14, 255))
        y = 12
        for r in range(rows):
            x = 12
            for c in range(cols):
                i = r * cols + c
                if i < len(tiles):
                    sheet.paste(tiles[i], (x, y), tiles[i])
                x += cw[c] + 12
            y += rh[r] + 12
        path = SHEETS / f"{game}-{atlas['render']}-sheet.png"
        sheet.save(path)
        print(f"{game}: {path} ({sheet.width}x{sheet.height})")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--game", choices=sorted(GAMES), action="append")
    ap.add_argument("--check", action="store_true",
                    help="run the registration and walkability checks only")
    ap.add_argument("--render", choices=sorted(TILE_PX), default=DEFAULT_RENDER)
    ap.add_argument("--tile-px", type=int, default=None)
    ap.add_argument("--season", type=int, default=0, choices=range(4),
                    help="0 spring, 1 summer, 2 autumn, 3 winter")
    ap.add_argument("--sheet", action="store_true",
                    help="write the artifacts/ proof sheet and nothing else")
    args = ap.parse_args()
    games = args.game or sorted(GAMES)
    if args.sheet:
        return write_sheet(games)
    if args.check:
        return run_checks(games)
    rc = run_checks(games)
    if rc:
        print("checks failed; nothing written", file=sys.stderr)
        return rc
    for game in games:
        rom = Gen5Rom(game)
        atlas = build_atlas(rom, render=args.render, tile_px=args.tile_px,
                            season=args.season)
        fb = atlas["map_set"].get("fallback") or []
        print(f"{game}: {len(atlas['maps'])} maps ({atlas['render']}), "
              f"{atlas['bytes']} bytes, {len(fb)} fell back -> {MAPS_ROOT / game}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
