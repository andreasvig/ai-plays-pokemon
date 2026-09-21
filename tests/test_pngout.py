"""`scripts/ds3d/pngout.py` — the DS renderers' PNG writer.

The writer throws pixel data away on purpose, so the tests that matter are the
ones bounding WHAT it throws away. Three claims are load-bearing and all three
are asserted here rather than described:

  1. It is an involution on the console's own grid, so a colour that really
     came off a cartridge is passed through byte-exact and only our own
     blending moves at all.
  2. 31 widens to 255. The cheaper `v << 3` spelling widens it to 248, which
     would make every opaque pixel in every map very slightly transparent —
     a defect that looks like nothing on a screenshot and is never traced
     back to the writer.
  3. It is bounded by 4. That is the number that makes the difference
     invisible, and it is the whole argument for choosing this over the
     octree palette that was measured and refused.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

pngout = pytest.importorskip("ds3d.pngout")


def _all_bytes() -> np.ndarray:
    """Every 8-bit value, shaped as an RGBA image so the real entry points run."""
    return np.arange(256, dtype=np.uint8).repeat(4).reshape(16, 16, 4)


# -- the grid ------------------------------------------------------------------

def test_the_grid_has_exactly_the_consoles_thirty_two_levels():
    got = pngout.ds_grid_values()
    assert got.tolist() == sorted(set(got.tolist())), "the grid is not strictly ascending"
    assert len(got) == pngout.DS_LEVELS == 32


def test_full_white_and_full_alpha_survive():
    """The one value that must not move. 255 is "opaque" and "white"; a writer
    that rounded it to 248 would wash out every map by 3% and break nothing
    loudly enough to notice."""
    assert int(pngout.widen(31)) == 255
    assert pngout.quantise(np.full((2, 2, 4), 255, np.uint8)).min() == 255


def test_narrowing_and_widening_are_inverse_on_the_grid():
    v = np.arange(32, dtype=np.uint16)
    assert pngout.narrow(pngout.widen(v)).tolist() == v.tolist()


def test_quantising_a_colour_that_is_already_a_ds_colour_changes_nothing():
    """Why the error budget is spent only where it was earned: artwork read
    off a cartridge palette is already on this grid and is passed through
    untouched, so the residual measured on a real map is entirely our own
    blending, supersampling and shading."""
    grid = pngout.ds_grid_values()
    img = np.array(np.meshgrid(grid, grid)).astype(np.uint8)
    img = np.stack([img[0], img[1], img[0], img[1]], axis=-1)
    assert np.array_equal(pngout.quantise(img), img)
    assert pngout.on_ds_grid(img).all()


def test_quantising_is_idempotent():
    once = pngout.quantise(_all_bytes())
    assert np.array_equal(pngout.quantise(once), once)


# -- the bound, and the control that shows it is a real bound ------------------

def test_no_channel_moves_by_more_than_four():
    """The number the whole choice rests on. Bounded by construction over
    EVERY possible input, not sampled from one map."""
    a = _all_bytes()
    err = np.abs(a.astype(int) - pngout.quantise(a).astype(int))
    assert err.max() <= 4, f"worst channel error {err.max()}, expected at most 4"


def test_truncation_is_the_worse_spelling_and_this_is_not_it():
    """The rejected alternative, kept as a comparison so the choice can be
    re-checked rather than believed. `x >> 3` is on the same grid and so
    passes every other test in this file, and it is twice the error."""
    a = _all_bytes()
    v = (a.astype(np.uint16) >> 3)
    trunc = ((v << 3) | (v >> 2)).astype(np.uint8)
    assert pngout.on_ds_grid(trunc).all(), "the control is not even on the grid"
    ours = np.abs(a.astype(int) - pngout.quantise(a).astype(int)).max()
    theirs = np.abs(a.astype(int) - trunc.astype(int)).max()
    assert theirs > ours, (
        f"truncation ({theirs}) is no worse than rounding ({ours}), so the "
        f"reason given for preferring rounding does not hold")


def test_the_grid_check_rejects_an_off_grid_value():
    """Mutation control for `on_ds_grid`, which is the predicate the shipped
    atlas is tested with. Every value between two grid points must be
    rejected, or the property test it backs is satisfiable by anything."""
    grid = set(pngout.ds_grid_values().tolist())
    off = np.array([[[v, v, v, v] for v in range(256) if v not in grid]], np.uint8)
    assert off.size, "no off-grid value exists, so the check cannot be tested"
    assert not pngout.on_ds_grid(off).any(), \
        "a value that is not one of the 32 read as expressible on the DS"


# -- the writer ----------------------------------------------------------------

def test_the_written_file_is_on_the_grid_and_the_opt_out_is_not(tmp_path):
    """Both directions in one place: quantisation is the DEFAULT, and the
    collision tier's opt-out really does leave its palette alone. The two
    tones here are `render_gen5maps.GROUND` and `WALL`, three of whose six
    channel values are off the grid — quantising them would silently restyle
    a UI colour this repo chose."""
    src = np.zeros((4, 4, 4), np.uint8)
    src[..., :] = (74, 84, 100, 255)
    src[0, 0] = (27, 31, 39, 255)

    quantised = tmp_path / "q.png"
    pngout.write_png(src, quantised)
    assert pngout.on_ds_grid(np.asarray(Image.open(quantised).convert("RGBA"))).all()

    verbatim = tmp_path / "v.png"
    pngout.write_png(src, verbatim, quantise_to_ds=False)
    got = np.asarray(Image.open(verbatim).convert("RGBA"))
    assert np.array_equal(got, src), "the opt-out still altered the pixels"
    assert not pngout.on_ds_grid(got).all(), (
        "the collision palette is already on the grid, so this test no longer "
        "shows that the opt-out does anything")


def test_tile_px_expands_a_flat_tier_and_one_leaves_it_alone(tmp_path):
    src = np.arange(2 * 2 * 4, dtype=np.uint8).reshape(2, 2, 4)
    pngout.write_png(src, tmp_path / "a.png", 1, quantise_to_ds=False)
    pngout.write_png(src, tmp_path / "b.png", 4, quantise_to_ds=False)
    assert Image.open(tmp_path / "a.png").size == (2, 2)
    assert Image.open(tmp_path / "b.png").size == (8, 8)


def test_stats_report_the_saving_and_the_error_that_paid_for_it(tmp_path):
    rng = np.random.default_rng(0)
    src = rng.integers(0, 256, (64, 64, 4), dtype=np.uint8)
    stats: dict = {}
    pngout.write_png(src, tmp_path / "s.png", stats=stats)
    assert stats["samples"] == src.size
    assert 0 < stats["max_err"] <= 4
    assert stats["raw_bytes"] > 0 and stats["out_bytes"] > 0
    assert "error max" in pngout.format_stats(stats)


def test_a_failed_write_leaves_no_half_file_beside_a_good_one(tmp_path, monkeypatch):
    """The reason for the temp-and-rename: the atlas records each PNG's byte
    count, so a truncated image beside an index.json that describes a whole
    one is precisely the lie `test_gen5maps.py` section 4 exists to catch."""
    target = tmp_path / "x.png"

    def boom(*_a, **_k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(Image.Image, "save", boom)
    with pytest.raises(RuntimeError):
        pngout.write_png(np.zeros((4, 4, 4), np.uint8), target)
    assert not target.exists(), "a failed write left a file behind"
    assert not list(tmp_path.iterdir()), f"left a temp file: {list(tmp_path.iterdir())}"
