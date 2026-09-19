"""What the model is shown on the v2 (SkyEmu) backend — and the two things v1
did that v2 must not.

Ported from ``v2-experiments/harness/frame.py`` (P1). The experiment file stays
where it is so the experiments keep running; this is the copy ``src/`` owns, so
that nothing under ``src/`` has to reach into ``v2-experiments/`` at import
time. The algorithm, the constants and the reasoning below are the experiment's,
unchanged — this module is a move, not a re-derivation.

**No grid overlay.** ``src/emulator/backends/mgba.py:_draw_grid_overlay`` paints
a semi-transparent red lattice every 16 native px with an 8 px vertical offset.
Those numbers are GBA viewport facts: the offset exists because FireRed's camera
sits half a tile off the tile grid. On an NDS frame they describe nothing — the
lines land wherever they land — and they land hardest on the bottom screen,
which is exactly where the model has to aim a stylus tap. So the v2 frame
carries no lattice. (Andreas, 2026-09-18: "remove the overlay grid graphic. no
doubt.")

**The two DS screens, stacked.** SkyEmu already returns them that way: /screen
on NDS is 256x384, ``framebuffer_top`` memcpy'd above ``framebuffer_bottom``
(``se_screenshot``, main.c). It is not a layout setting, so it cannot drift —
but "the capture happens to be right today" is not the same claim as "the image
the model sees is a vertical stack", so ``prepare`` asserts the geometry and
fails loudly rather than silently feeding a side-by-side frame to a prompt that
describes a stacked one.

The only mark drawn is a seam between the screens. Two dark DS screens abut with
no visible boundary (a battle's black top screen over a dark menu), and the
model is told "the bottom half is the touch screen" — a claim it cannot act on
if it cannot see where the halves divide. Turn it off with divider=False.
"""
from __future__ import annotations

import io
from typing import Optional

from PIL import Image

NDS_SCREEN = (256, 192)
GEOMETRY = {(240, 160): "GBA", (160, 144): "GB", (256, 384): "NDS"}

# Native pixels are tiny and the text is the point, so every frame is upscaled
# with NEAREST (no interpolation — smoothing a 1px font is worse than not
# scaling at all). NDS gets less than the GBA harness's 6x because it starts
# with 2.5x the pixels: 6x would be 1536x2304, which buys nothing over 3x and
# costs image tokens on every turn.
UPSCALE = {"GB": 6, "GBA": 6, "NDS": 3}

SEAM_PX = 4                      # at final scale
SEAM_COLOR = (90, 90, 96)

# --- the spectator's frame, which is NOT the model's -----------------------
#
# Both DS screens are 256x192, and SkyEmu stacks them equally. Andreas, watching
# a SoulSilver run on 2026-09-19: *"for ds games the watching experience is not
# that good ... change the 'scale'/ratio between upper and lower screen, such
# that the upper screen is at least twice as wide."*
#
# So for a WATCHER the top screen is drawn at 2x and the touch screen at its own
# size underneath. The world gets the space; the menu keeps its pixels and stops
# claiming half the picture.
#
# **They stay stacked.** The first cut of this put them side by side, which is
# the other way to give the top screen twice the width — he looked at it and
# said no: *"i still think on top of each other is best for the spectate view
# for the ds game."* Vertical is how a DS is held and how every screenshot of
# one reads.
#
# **The padding is transparent, not black** — *"dont fill out with a black
# colour just let it be transparent so we can see the background paper colour."*
# The touch screen is half the width of the frame, so a filled backdrop would
# paint two dark bars beside it in a light dashboard and a dark slab around it in
# the simple view's paper stage. Transparent means the page decides, and the gap
# between the screens becomes a separator without a line being drawn — which is
# why there is no seam here, unlike ``prepare``.
#
# **None of this reaches the model.** ``prepare`` is what a turn is shown and it
# is untouched — equal screens, a drawn seam, and ``image_row_to_touch_y`` maps a
# row of THAT image to a tap. A model reasoning about a 2x top screen would put
# every stylus tap in the wrong place, and a tap always "works", so it would
# never fail visibly. The two layouts are deliberately different FUNCTIONS rather
# than one with a flag, so there is no call site where a default selects the
# wrong one.
SPECTATE_TOP_SCALE = 2
#: Transparent rows between the two screens. The paper colour showing through is
#: the divider, so this replaces ``prepare``'s drawn seam rather than adding to it.
SPECTATE_GAP_PX = 10
#: PNG effort for a frame that lives ~33 ms. Level 1 is ~3 ms/frame on a real
#: capture against ~9 ms at the default 6, and this runs inside the emulator
#: lock, where the whole sampling chunk has 33 ms to spend.
SPECTATE_COMPRESS = 1


def geometry(png: bytes) -> tuple[str, int, int]:
    """('NDS'|'GBA'|'GB', width, height) — the system, measured not asked.

    /status names the ROM but not the console, and the capture's shape has to be
    checked anyway, so one probe answers both.
    """
    img = Image.open(io.BytesIO(png))
    size = img.size
    system = GEOMETRY.get(size)
    if system is None:
        raise ValueError(
            f"capture is {size[0]}x{size[1]}, which is no console this harness knows "
            f"(expected one of {sorted(GEOMETRY)})")
    return system, size[0], size[1]


def split_nds(img: Image.Image) -> tuple[Image.Image, Image.Image]:
    """(top screen, bottom/touch screen) from a 256x384 capture."""
    w, h = NDS_SCREEN
    return img.crop((0, 0, w, h)), img.crop((0, h, w, h * 2))


def prepare(png: bytes, upscale: Optional[int] = None,
            divider: bool = True) -> tuple[Image.Image, dict]:
    """The frame the model is shown, plus what is true about it.

    Returns (PIL image, meta) where meta carries the system, the final size and
    — for NDS — the row the touch screen starts at, which is what turns a
    feature the model can see into a tap the harness can send.

    Differs from the experiment's version in its return type only: the harness
    handed back PNG bytes because it wrote files; the backend hands back a
    ``PIL.Image`` because ``EmulatorBackend.capture_screenshot`` returns one.
    """
    system, w, h = geometry(png)
    img = Image.open(io.BytesIO(png)).convert("RGB")
    scale = upscale if upscale is not None else UPSCALE[system]

    if system == "NDS":
        # Re-stack explicitly rather than trusting the capture's layout. On a
        # correct capture this is a no-op by construction; on a changed one it
        # is the difference between a wrong frame and a raised exception above.
        top, bottom = split_nds(img)
        top = _scale(top, scale)
        bottom = _scale(bottom, scale)
        seam = SEAM_PX if divider else 0
        out = Image.new("RGB", (top.width, top.height + seam + bottom.height), SEAM_COLOR)
        out.paste(top, (0, 0))
        out.paste(bottom, (0, top.height + seam))
        meta = {
            "system": system,
            "native": [w, h],
            "size": [out.width, out.height],
            "upscale": scale,
            "touch_top_row": top.height + seam,   # first row of the touch screen
            "touch_height": bottom.height,
            "seam_px": seam,
            "grid_overlay": False,
        }
        return out, meta

    out = _scale(img, scale)
    return out, {
        "system": system,
        "native": [w, h],
        "size": [out.width, out.height],
        "upscale": scale,
        "grid_overlay": False,
    }


def spectate_frame(png: bytes) -> Optional[bytes]:
    """Re-lay a raw NDS capture out for a human watcher. ``None`` for anything else.

    Top screen at :data:`SPECTATE_TOP_SCALE` — twice the touch screen's width —
    with the touch screen at native size centred underneath it. 256x384 in,
    512x586 out, RGBA, and **everything that is not a screen is transparent**:
    the two bars beside the touch screen and the gap between them.

    **Returns None rather than raising for a frame it does not handle** — GB and
    GBA captures, a torn read, a capture whose shape changed. This sits on the
    spectate feed's hot path, where a frame is worth less than the run: the
    caller publishes the original bytes and the viewer sees an un-relaid frame
    rather than a stalled feed. ``prepare`` takes the opposite line and raises on
    an unknown geometry, because THAT frame is evidence a benchmark is scored on.
    """
    try:
        img = Image.open(io.BytesIO(png))
        img.load()
        if img.size != (NDS_SCREEN[0], NDS_SCREEN[1] * 2):
            return None
        top, bottom = split_nds(img.convert("RGB"))
        top = _scale(top, SPECTATE_TOP_SCALE)
        out = Image.new(
            "RGBA",
            (top.width, top.height + SPECTATE_GAP_PX + bottom.height),
            (0, 0, 0, 0),
        )
        out.paste(top, (0, 0))
        out.paste(
            bottom,
            ((top.width - bottom.width) // 2, top.height + SPECTATE_GAP_PX),
        )
        buf = io.BytesIO()
        out.save(buf, format="PNG", compress_level=SPECTATE_COMPRESS)
        return buf.getvalue()
    except Exception:
        return None


def image_row_to_touch_y(row: int, meta: dict) -> float:
    """An image row in the prepared frame -> the touch_y SkyEmu wants.

    The inverse of the trap in ``v2-experiments/emulator-research.md``: touch_x/
    touch_y are normalised over the touch SCREEN, while anything read off the
    frame is in the coordinates of the stacked IMAGE. Every conversion goes
    through here.
    """
    if meta.get("system") != "NDS":
        raise ValueError("only NDS has a touch screen")
    return max(0.0, min(1.0, (row - meta["touch_top_row"]) / meta["touch_height"]))


def _scale(img: Image.Image, factor: int) -> Image.Image:
    if factor <= 1:
        return img
    return img.resize((img.width * factor, img.height * factor), Image.NEAREST)


__all__ = [
    "GEOMETRY",
    "NDS_SCREEN",
    "SEAM_COLOR",
    "SEAM_PX",
    "SPECTATE_COMPRESS",
    "SPECTATE_GAP_PX",
    "SPECTATE_TOP_SCALE",
    "UPSCALE",
    "geometry",
    "image_row_to_touch_y",
    "spectate_frame",
    "prepare",
    "split_nds",
]
