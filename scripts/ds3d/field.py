"""Gen-4 field maps: which artwork belongs to which map, and where it goes.

THE AREA-DATA TABLE, which is the one thing a terrain model does not carry.
A map's own header names it, and the chain has no guesswork in it:

    include/data/map_headers.h   .areaDataArchiveID = area_data_006
    res/field/area_data/area_data_006.json
        { "mapPropSet": "prop_model_set_000",
          "mapTextureSet": "map_texture_set_006",
          "lightingSet": ... }

and `src/overlay005/area_data.c` spends that record on three archives at once,
which is why ONE index covers both halves of the props:

    mapTexture      = map_tex_set   [areaData.mapTextureArchiveID]
    mapPropModelIDs = area_build    [areaData.mapPropArchivesID]
    mapPropTexture  = areabm_texset [areaData.mapPropArchivesID]   <- same index

The terrain model inside `map_data_NNN.bin` has no textures of its own; its
materials join to `mapTexture` BY NAME, after the `_lmNN` lightmap suffix is
stripped.

A PLACEMENT'S `modelID` IS GLOBAL. It indexes `build_model` — 590 files, the
order `res/field/props/models/meson.build` lists them in — and NOT the 32-entry
set the area preloads. Reading it as a set index silently yields a bare lawn:
every id resolves, to the wrong model or to none, and nothing raises.

WHAT THE GAME ACTUALLY DRAWS, from `MapPropManager_Render`:
  * at most `MAX_LOADED_MAP_PROPS` = 32 placements per block; the rest of the
    file is never loaded.
  * with an IDENTITY rotation matrix. The placement carries a rotation and the
    field renderer ignores it (`applyRotation = FALSE`), so a render that
    applies the stored yaw is wrong wherever the yaw is not zero.
  * with the placement's own scale, which IS applied.

Chunk placement, from `LandDataManager_CalculateRenderingPosition`: a 32x32
block's model is centred on its own origin and drawn at
`((16 + 32*col) * 16, altitude * 8, (16 + 32*row) * 16)`, so global tile
(X, Z) has its centre at world `((X + .5) * 16, _, (Z + .5) * 16)`.
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass

import numpy as np

from .nsbmd import load_models
from .nsbtx import TextureSet
from .scene import CHUNK, SPAN, TILE, Scene

PROP_ENTRY = 48                 # sizeof(MapPropFile)
MAX_LOADED_MAP_PROPS = 32       # overlay005/map_prop.h
FX = 4096.0


# ---------------------------------------------------------------- land blocks

@dataclass
class LandBlock:
    """The four sections `map_data_NNN.bin` declares in its own first 16 bytes."""
    attrs: bytes
    props: bytes
    model: bytes
    extra: bytes

    @property
    def placements(self) -> list[tuple]:
        """`(model_id, (x, y, z), (sx, sy, sz))`, capped the way the game caps it."""
        out = []
        n = min(len(self.props) // PROP_ENTRY, MAX_LOADED_MAP_PROPS)
        for i in range(n):
            f = struct.unpack_from("<12i", self.props, i * PROP_ENTRY)
            out.append((f[0],
                        (f[1] / FX, f[2] / FX, f[3] / FX),
                        (f[7] / FX, f[8] / FX, f[9] / FX)))
        return out


def land_block(raw: bytes) -> LandBlock:
    """The four sections, under whichever of the two layouts ADDS UP.

    Platinum puts its first section at a constant 0x10. SoulSilver writes a
    `0x1234` marker there, then a u16 giving the length of a variable extra
    section, and only then the attributes — so the offset is per block, and
    reading it as a constant shifts the grid by two tiles on every block that
    has one.

    Which layout is in force is not decided by who the caller is: it is decided
    by arithmetic, because only one of the two accounts for every byte. Both
    holding at once would be an ambiguity this refuses rather than resolves —
    picking the wrong one produces a map that still looks like a map.
    """
    sizes = struct.unpack_from("<4I", raw, 0)
    total = sum(sizes)
    plain = 16 + total == len(raw)
    marker, extra = struct.unpack_from("<HH", raw, 16)
    marked = marker == 0x1234 and 20 + extra + total == len(raw)
    if plain and marked:
        raise ValueError("land block fits both layouts; nothing here can choose")
    if not (plain or marked):
        raise ValueError(f"land block: {total} + header != {len(raw)} under either layout")
    at = 16 if plain else 20 + extra
    cut = []
    for s in sizes:
        cut.append(raw[at:at + s])
        at += s
    return LandBlock(*cut)


# ---------------------------------------------------------------- the archives

_NSBMD = re.compile(r"'([^']+\.nsbmd)'")


class Assets:
    """Every archive the artwork needs, read through the caller's `fetch`.

    `fetch(path) -> bytes` is `render_dsmaps.Decomp`'s, so the whole chain is
    pinned to the same commit as the collision data and cached the same way.
    """

    def __init__(self, fetch, fetch_json) -> None:
        self._fetch = fetch
        self._fetch_json = fetch_json
        self._cache: dict = {}

    def _once(self, key, make):
        if key not in self._cache:
            self._cache[key] = make()
        return self._cache[key]

    def prop_names(self) -> list[str]:
        """Global model id -> filename, from the decomp's own build list.

        Read rather than kept as a checked-in copy: the id is an index into the
        NARC, the NARC is built from this list in this order, and a hand-copied
        list would rot silently into props that are the wrong shape.
        """
        def build():
            text = self._fetch("res/field/props/models/meson.build").decode()
            names = _NSBMD.findall(text)
            if not names:
                raise SystemExit("props/models/meson.build lists no .nsbmd files")
            return names
        return self._once("prop_names", build)

    def area(self, const: str) -> dict:
        return self._once(f"area:{const}",
                          lambda: self._fetch_json(f"res/field/area_data/{const}.json"))

    def area_record(self, const: str) -> Area:
        rec = self.area(const)
        prop_set = rec["mapPropSet"]
        return Area(const, rec["mapTextureSet"], prop_set,
                    # `areabm_texset[mapPropArchivesID]` — the SAME index as the
                    # model set, because `area_data.c` spends one id on both.
                    prop_set.replace("prop_model_set_", "prop_texture_set_"))

    def map_texset(self, const: str) -> TextureSet:
        return self._once(f"mt:{const}", lambda: TextureSet(
            self._fetch(f"res/field/maps/texture_sets/{const}.nsbtx")))

    def prop_texset(self, const: str) -> TextureSet:
        return self._once(f"pt:{const}", lambda: TextureSet(
            self._fetch(f"res/field/props/texture_sets/{const}.nsbtx")))

    def prop_model(self, model_id: int, archive: str = "field"):
        """Platinum has ONE prop archive (`build_model`), so `archive` is moot."""
        def build():
            name = self.prop_names()[model_id]
            return load_models(self._fetch(f"res/field/props/models/{name}"), 0)[0][0]
        return self._once(f"pm:{model_id}", build)


@dataclass
class Area:
    """One map's artwork set, resolved from its `areaDataArchiveID`.

    `prop_texture_set` is None on the cartridge and that is not a gap: HGSS
    ships 339 of its 340 field prop models with their own embedded `TEX0`, so
    there is no separate prop texture archive to name.
    """
    const: str
    map_texture_set: object
    prop_set: object
    prop_texture_set: object = None


def area_of(assets, const):
    return assets.area_record(const)


# ---------------------------------------------------------------- texture join

class TexPool:
    """One name-keyed lookup over an ordered list of texture sets.

    Platinum binds a prop to the AREA's prop texture set (`area_data.c`), and
    the dummy-box path binds a model to its own embedded `TEX0`; a handful of
    Platinum props ship one. SoulSilver inverts the balance — its props are
    almost all self-textured and it has no prop texture archive at all — so the
    order is passed in rather than fixed here, first match wins, and a source
    that is None is simply absent.
    """

    def __init__(self, *sources) -> None:
        self.sources = [s for s in sources if s is not None]
        self.textures = {}
        for src in reversed(self.sources):
            self.textures.update(src.textures)

    def _owner(self, name):
        for src in self.sources:
            if name in src.textures:
                return src
        return None

    def image(self, tex, pal):
        return self._owner(tex).image(tex, pal)

    def tex_params(self, tex):
        return self._owner(tex).tex_params(tex)


def own_textures(model_bytes: bytes) -> TextureSet | None:
    try:
        return TextureSet(model_bytes)
    except (KeyError, AssertionError, struct.error, ValueError):
        return None


# ---------------------------------------------------------------- scene build

@dataclass
class ChunkStats:
    land: str
    terrain_tris: int
    declared_tris: int          # 2*num_quads + num_tris, from the model header
    props: int
    props_drawn: int
    missing_models: list


def add_chunk(sc: Scene, assets, raw: bytes, area: Area,
              origin=(0.0, 0.0, 0.0), *, with_props: bool = True,
              prop_archive: str = "field") -> ChunkStats:
    """One 32x32 land block into the scene, at `origin` world units.

    `origin` is the CENTRE of the block, which is where the game puts it.

    `prop_archive` picks which model archive a placement id indexes. Platinum
    has one and ignores it; SoulSilver has two — `bm_field` for the overworld
    and `bm_room` for interiors — and the id means a different building in each.
    Read from the wrong one, Elm's laboratory is drawn with a lake on top of it.
    """
    block = land_block(raw)
    mt = assets.map_texset(area.map_texture_set)
    terrain = declared = 0
    if block.model:
        model = load_models(block.model, 0)[0][0]
        terrain = sc.add_model(model, mt, origin=origin)
        declared = 2 * model.num_quads + model.num_tris

    drawn, missing = 0, []
    if with_props and block.props:
        pt = assets.prop_texset(area.prop_texture_set)
        for model_id, (px, py, pz), scale in block.placements:
            try:
                pm = assets.prop_model(model_id, prop_archive)
            except (IndexError, SystemExit, KeyError):
                missing.append(model_id)
                continue
            pool = assets._once(
                f"pool:{area.prop_texture_set}:{prop_archive}:{model_id}",
                lambda pm=pm, pt=pt: TexPool(pt, own_textures(pm.data)))
            # The placement's own scale, and NO rotation: the field renderer
            # passes an identity matrix (map_prop.c:MapPropManager_Render).
            n = sc.add_model(pm, pool, place_scale=scale,
                             origin=(origin[0] + px, origin[1] + py, origin[2] + pz))
            if n:
                drawn += 1
            else:
                missing.append(model_id)
    return ChunkStats("", terrain, declared, len(block.placements), drawn, missing)


def chunk_origin(col: int, row: int, c0: int, r0: int, altitude: int = 0):
    """Centre of matrix cell (col, row) in a picture whose first cell is (c0, r0)."""
    return ((col - c0) * SPAN + SPAN / 2, altitude * TILE / 2, (row - r0) * SPAN + SPAN / 2)


def ortho_pixels(sc: Scene, tiles_w: int, tiles_h: int, tile_px: int, ss: int) -> np.ndarray:
    """Straight down, `tile_px` pixels per tile, supersampled `ss`x and boxed down.

    Pixel (0, 0) is the top-left corner of the window's first tile, so a tile
    (tx, ty) of the window occupies exactly `[ty*tile_px : (ty+1)*tile_px]`
    — the registration the route is drawn against.
    """
    from . import scene as _scene

    scale = ss * tile_px / TILE
    proj = _scene.ortho_projector(scale, 0.0, 0.0)
    fb = _scene.render(sc, proj, tiles_w * tile_px * ss, tiles_h * tile_px * ss)
    return _scene.downsample(fb, ss)


# ---------------------------------------------------------------- the cartridge

class RomAssets:
    """The three lookups `Assets` offers, read out of a cartridge instead.

    Duck-typed against `Assets` for the same reason `RomDecomp` is duck-typed
    against `Decomp`: `add_chunk` is written against `map_texset`,
    `prop_texset`, `prop_model` and `area_record`, and the two sources have
    nothing else in common. Where Platinum names an artwork set with a string
    from a JSON file, the cartridge names it with an index into a NARC, and the
    `Area` record carries whichever.

    The archives, all confirmed against the cartridge rather than assumed from
    Platinum:
      a/0/4/0  340 field prop models (`bm_field`), 339 with their own TEX0
      a/1/4/8  222 room prop models (`bm_room`), 219 with their own TEX0
      a/0/4/2  106 area records, 8 bytes each, the same four u16 fields as
               Platinum's `AreaDataFile`
      a/0/4/3  104 prop sets: a u16 count then that many GLOBAL model ids
      a/0/4/4  106 map texture sets (BTX0)
    """

    PROPS = {"field": "a/0/4/0", "room": "a/1/4/8"}
    AREA = "a/0/4/2"
    PROP_SETS = "a/0/4/3"
    MAP_TEX = "a/0/4/4"

    def __init__(self, rom) -> None:
        self.rom = rom
        names = rom.names()
        keys = (self.AREA, self.PROP_SETS, self.MAP_TEX, *self.PROPS.values())
        self._raw = {k: rom.narc(rom.file(names[k])) for k in keys}
        self._cache: dict = {}

    def _once(self, key, make):
        if key not in self._cache:
            self._cache[key] = make()
        return self._cache[key]

    def area_record(self, const) -> Area:
        """`const` is the index the map header carries, not a filename."""
        idx = int(const)
        prop_set, map_tex, _dummy, _light = struct.unpack("<4H", self._raw[self.AREA][idx])
        return Area(f"area_{idx:03d}", map_tex, prop_set, None)

    def prop_set_members(self, idx: int) -> list[int]:
        raw = self._raw[self.PROP_SETS][idx]
        n = struct.unpack_from("<H", raw, 0)[0]
        return list(struct.unpack_from(f"<{n}H", raw, 2))

    def map_texset(self, idx) -> TextureSet:
        return self._once(f"mt:{idx}", lambda: TextureSet(self._raw[self.MAP_TEX][int(idx)]))

    def prop_texset(self, idx):
        return None                     # see the note on `Area`

    def prop_model(self, model_id: int, archive: str = "field"):
        key = self.PROPS[archive]
        def build():
            return load_models(self._raw[key][model_id], 0)[0][0]
        return self._once(f"pm:{archive}:{model_id}", build)

    def prop_names(self, archive: str = "field") -> list[str]:
        """A cartridge ships no filenames, so a model is named by its index."""
        return [f"{archive}_{i:03d}" for i in range(len(self._raw[self.PROPS[archive]]))]
