"""What the model is shown — now one implementation, re-exported.

The algorithm, the constants and the reasoning behind them moved to
``src/emulator/backends/frame.py`` during P1, and the two copies were identical
apart from ``prepare``'s return type: this harness wrote PNG files, so it wanted
bytes; ``EmulatorBackend.capture_screenshot`` returns a ``PIL.Image``, so the
backend wants an image. Keeping two copies of the seam width, the upscale table,
the geometry assertion and the touch-screen conversion means the next change to
the frame lands in one of them — and a frame the probe draws differently from a
run is a probe of something else.

So this file is now a **thin adapter**: one algorithm, two return types.

It loads the backend's module BY FILE PATH rather than importing
``src.emulator.backends.frame``, which would pull in the ``src.emulator``
package (mGBA client, vision, OCR) and make an experiment depend on the real
harness booting — the one thing ``play.py``'s docstring says the experiments do
not do. ``src/emulator/backends/frame.py`` imports only ``io``, ``typing`` and
PIL, so loading it alone is exactly as self-contained as this file used to be.

See that module's docstring for the two decisions it records: **no grid
overlay** (v1's lattice is 16 px on an 8 px offset, both GBA viewport facts) and
**the two DS screens stacked with a seam**.
"""
from __future__ import annotations

import importlib.util
import io
from pathlib import Path
from typing import Optional

from PIL import Image

_SOURCE = Path(__file__).resolve().parents[2] / "src/emulator/backends/frame.py"
if not _SOURCE.is_file():                     # loud, not a silent fallback copy
    raise ImportError(
        f"the frame builder is missing at {_SOURCE} — this harness re-exports it "
        "rather than keeping a second copy (see this module's docstring)")
_spec = importlib.util.spec_from_file_location("_v2_frame_impl", _SOURCE)
_impl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_impl)

NDS_SCREEN = _impl.NDS_SCREEN
GEOMETRY = _impl.GEOMETRY
UPSCALE = _impl.UPSCALE
SEAM_PX = _impl.SEAM_PX
SEAM_COLOR = _impl.SEAM_COLOR

geometry = _impl.geometry
split_nds = _impl.split_nds
image_row_to_touch_y = _impl.image_row_to_touch_y


def prepare(png: bytes, upscale: Optional[int] = None,
            divider: bool = True) -> tuple[bytes, dict]:
    """The PNG the model is shown, plus what is true about it.

    The whole of this harness's difference from the backend: it hands back
    encoded PNG bytes, because every caller here writes a file or builds a
    data URL. ``meta`` is the backend's, unchanged — including ``touch_top_row``,
    which is what turns a feature the model can see into a tap this harness can
    send.
    """
    img, meta = _impl.prepare(png, upscale=upscale, divider=divider)
    return _png(img), meta


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
