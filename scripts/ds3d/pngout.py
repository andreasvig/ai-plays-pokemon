"""PNG writing for the DS renderers, at the console's own colour depth.

Shared on purpose: `render_gen5maps.py` calls this today and
`render_dsmaps.py` (gen 4) is meant to call it too — both tiers rasterise
cartridge artwork through `ds3d`, so both ship the same kind of pixels and
should shrink them the same way. Keeping one writer is what stops the two
renderers from drifting into two different ideas of "the same picture".

WHY THE PIXELS ARE QUANTISED
----------------------------
A DS colour is five bits per channel. The hardware has 32 levels, not 256,
so an eight-bit channel coming out of this renderer carries precision the
console never had: it is an artefact of our own blending, supersampling and
lighting, not information from the cartridge. Rounding each channel back
onto the console's 32-level grid therefore throws away nothing the source
material ever contained, and it takes about a third off the file —
measured 32.7% on Black and 33.7% on Black 2.

This is NOT a quality knob. The alternative that was measured and REFUSED
was an octree palette at 255 colours: 79% smaller, but max error 38 with a
fifth of the pixels off by more than 8, which is visible banding on a
gradient. Five-bit quantisation is bounded by construction — see
`quantise` — and the residual is the same magnitude as the disagreement
between the two legitimate ways of widening a 5-bit value to 8, which
`render_gamemaps.py:read_pal` already documents as invisible.

THE GRID, AND WHY BOTH DIRECTIONS MATTER
----------------------------------------
Widening uses `(v << 3) | (v >> 2)`, the spelling pret's palettes use, so
31 widens to 255 and not to 248. That matters for more than white: alpha
255 is "opaque", and a writer that widened with a bare `v << 3` would make
every opaque pixel in every map 248 — a whole atlas gone faintly
translucent, which is exactly the kind of defect that renders fine on one
screenshot and is never traced back to the PNG writer.

The pair is an involution on the grid: `narrow(widen(v)) == v` for all 32
values, and `quantise(x) == x` for every x already on the grid. So a pixel
whose colour came from a real cartridge palette entry is passed through
BYTE-EXACT, and only our own blends move at all.

WHAT IS DELIBERATELY NOT QUANTISED
----------------------------------
The collision tier. Its two tones are a UI palette this repo chose
(`render_gen5maps.GROUND` / `WALL`, shared with gen 4), not colour read off
a cartridge, and three of their six channel values are off the 5-bit grid —
quantising would shift them for no reason. The argument for quantising is
"the hardware never had this precision", and that argument is simply not
true of a colour we invented. Callers pass `quantise=False` for that tier.
"""
from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

#: Levels per channel on the DS. Five bits.
DS_LEVELS = 32


def widen(v: np.ndarray | int):
    """5-bit -> 8-bit, the full-range spelling: 31 becomes 255, not 248."""
    v = np.asarray(v, dtype=np.uint16)
    return ((v << 3) | (v >> 2)).astype(np.uint8)


def narrow(x: np.ndarray | int):
    """8-bit -> 5-bit. The exact inverse of `widen` on the grid."""
    return (np.asarray(x, dtype=np.uint16) >> 3).astype(np.uint8)


def quantise(rgba: np.ndarray) -> np.ndarray:
    """Every channel rounded onto the console's 32 levels.

    ROUNDED, not truncated. Truncation (`x >> 3`) is the cheaper spelling and
    it is also an exact inverse of `widen`, so it passes cartridge colours
    through untouched just as this does — but on the values that are NOT on
    the grid, the ones our own blending invented, it can only ever move a
    channel DOWN, by as much as 7. Rounding moves it to the nearer of the two
    neighbouring levels instead, which halves the worst case to 4.

    Measured over all nineteen Gen 5 maps, 2026-09-21, rounding is better on
    both axes at once and not a trade at all:

        black-us    raw 1688.6 KB   round 1137.0 KB   truncate 1160.0 KB
        black2-us   raw 2298.2 KB   round 1524.4 KB   truncate 1554.8 KB
        error       round max 4 mean 0.83/0.99    truncate max 7 mean 0.98/1.14

    Rounding is ~2% SMALLER as well as half the error, which is worth writing
    down because the opposite was assumed: a uniform downward shift was
    expected to compress at least as well, and it does not — truncation
    darkens every off-grid value and breaks up runs that rounding leaves
    intact.
    """
    x = np.asarray(rgba, dtype=np.uint8).astype(np.uint16)
    v = ((x * (DS_LEVELS - 1) + 127) // 255).astype(np.uint16)
    return ((v << 3) | (v >> 2)).astype(np.uint8)


def on_ds_grid(rgba: np.ndarray) -> np.ndarray:
    """Boolean mask: which samples already sit on a value the DS can express.

    The property a shipped PNG has to have, in the form a test can read. A
    value is expressible iff widening its own 5-bit narrowing returns it.
    """
    a = np.asarray(rgba, dtype=np.uint8)
    return widen(narrow(a)) == a


def ds_grid_values() -> np.ndarray:
    """The 32 eight-bit values a DS channel can hold, ascending."""
    return widen(np.arange(DS_LEVELS, dtype=np.uint16))


def _encode(rgba: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, "PNG", optimize=True)
    return buf.getvalue()


def write_png(rgba: np.ndarray, path: Path, tile_px: int = 1, *,
              quantise_to_ds: bool = True,
              stats: Optional[dict] = None) -> None:
    """Write one map's RGBA to `path`, `tile_px` PNG pixels per tile.

    `tile_px` is 1 when the caller already rasterised at the final pixel
    density, which both 3D cameras and the stretched silhouette do; the flat
    collision tier passes its own block size and is expanded here.

    `quantise_to_ds` is on by default so the small file is what a caller gets
    without asking — see the module docstring for the one tier that turns it
    off. `stats`, when given a dict, accumulates `raw_bytes`, `out_bytes`,
    `abs_err_sum`, `samples` and `max_err` so a renderer can report the saving
    and the error it actually paid for it. Collecting them costs a second PNG
    encode, so it happens only when a dict is passed.
    """
    block = (rgba if tile_px == 1 else
             np.repeat(np.repeat(rgba, tile_px, axis=0), tile_px, axis=1))
    out = quantise(block) if quantise_to_ds else block

    if stats is not None:
        raw = _encode(block)
        d = np.abs(block.astype(np.int16) - out.astype(np.int16))
        stats["raw_bytes"] = stats.get("raw_bytes", 0) + len(raw)
        stats["abs_err_sum"] = stats.get("abs_err_sum", 0) + int(d.sum())
        stats["samples"] = stats.get("samples", 0) + int(d.size)
        stats["max_err"] = max(stats.get("max_err", 0), int(d.max()) if d.size else 0)

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        # Written to a sibling and renamed: a renderer that dies mid-write
        # must not leave a half a PNG beside an index.json that describes a
        # whole one.
        Image.fromarray(out, "RGBA").save(tmp, "PNG", optimize=True)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)

    if stats is not None:
        stats["out_bytes"] = stats.get("out_bytes", 0) + path.stat().st_size


def format_stats(stats: dict) -> str:
    """The one-line report a renderer prints after a game's maps are written."""
    raw, out = stats.get("raw_bytes", 0), stats.get("out_bytes", 0)
    n = stats.get("samples", 0) or 1
    saved = (100.0 * (raw - out) / raw) if raw else 0.0
    return (f"{raw / 1024:.1f} KB -> {out / 1024:.1f} KB ({saved:.1f}% smaller), "
            f"error max {stats.get('max_err', 0)} mean "
            f"{stats.get('abs_err_sum', 0) / n:.2f}")
