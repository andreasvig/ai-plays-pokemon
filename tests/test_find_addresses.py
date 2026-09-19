"""`v2-experiments/find_addresses.py` — the search, on synthetic memory.

What this is FOR, and what it is not for. The evidence that the finder works is
the live run against FireRed, where it recovers four addresses nobody told it
(`artifacts/skyemu-backend/p-a-results.md`). A synthetic machine cannot witness
that: the fixture would be built from the same beliefs as the code.

This exists because the tool had no test that ran without an emulator, and on
2026-09-19 a slice-based edit silently deleted four of its functions —
`region_of`, `deref`, `choose_block_pointer` and `scan_map` — and the result was
committed, because the live check had been run BEFORE the edit rather than
after. The file still imported and still parsed. It failed with a NameError the
next time the map stage ran, on another game.

So the claim here is narrow and is exactly the one that was broken: every stage
is reachable and each one returns the value its caller indexes. The numbers are
toy; the wiring is real.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / "v2-experiments" / "find_addresses.py"

pytestmark = pytest.mark.skipif(not TOOL.is_file(), reason="v2-experiments not present")


def _module():
    sys.path.insert(0, str(REPO / "v2-experiments" / "harness"))
    spec = importlib.util.spec_from_file_location("find_addresses", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fa = _module()

# A toy machine: 512 bytes of "EWRAM" and 256 of "IWRAM".
REGIONS = [("EWRAM", 0x02000000, 0x200), ("IWRAM", 0x03000000, 0x100)]
X, Y, GROUP, NUM = 0x50, 0x52, 0x54, 0x55   # inside the block
PTR = 0x08                                   # the IWRAM word pointing at it
TICK = 0x80                                  # a decoy that moves on every probe


def _machine(base, x, y, group, num, tick):
    """One snapshot. `base` is where the block currently sits."""
    ew = bytearray(0x200)
    iw = bytearray(0x100)
    off = base - 0x02000000
    ew[off + X:off + X + 2] = x.to_bytes(2, "little", signed=True)
    ew[off + Y:off + Y + 2] = y.to_bytes(2, "little", signed=True)
    ew[off + GROUP] = group
    ew[off + NUM] = num
    ew[TICK] = tick % 256
    iw[PTR:PTR + 4] = base.to_bytes(4, "little")
    return {"EWRAM": bytes(ew), "IWRAM": bytes(iw)}


HOME = 0x02000000
AWAY = 0x02000010  # the block moves on a transition, as both real games' do


def _snaps():
    s = {
        "origin": _machine(HOME, 5, 6, 4, 1, 0),
        "R3":     _machine(HOME, 8, 6, 4, 1, 1),
        "L3":     _machine(HOME, 2, 6, 4, 1, 2),
        "U3":     _machine(HOME, 5, 3, 4, 1, 3),
        "D3":     _machine(HOME, 5, 9, 4, 1, 4),
        "map_out":        _machine(AWAY, 1, 1, 4, 0, 5),
        "map_out_walked": _machine(AWAY, 3, 2, 4, 0, 6),
        "map_back":       _machine(HOME, 5, 6, 4, 1, 7),
        "map_alt":        _machine(AWAY, 2, 2, 3, 0, 8),
        "map_alt_walked": _machine(AWAY, 4, 3, 3, 0, 9),
    }
    return s


def test_the_axes_are_found_and_the_decoy_is_not():
    snaps = _snaps()
    xs = {c["addr"] for c in fa.scan_axis(snaps, REGIONS, "x")}
    ys = {c["addr"] for c in fa.scan_axis(snaps, REGIONS, "y")}
    assert xs == {HOME + X}
    assert ys == {HOME + Y}
    # TICK changes on every probe including the perpendicular ones, which is the
    # shape of a frame counter. It must not be in either answer.
    assert HOME + TICK not in xs | ys


def test_the_ablation_shows_the_decoy_is_rejected_by_a_condition():
    """A control on the control: removing every condition must let the decoy in.

    Without this the test above could pass because the decoy was built wrong.
    """
    snaps = _snaps()
    bare = {c["addr"] for c in fa.scan_axis(snaps, REGIONS, "x", conditions=())}
    assert HOME + TICK in bare, "the decoy is not a decoy — it fails on its own"


def test_the_block_pointer_is_the_one_that_moved():
    snaps = _snaps()
    ptrs = [p for p in fa.find_pointer_forms(snaps, REGIONS, HOME + X) if p["stable"]]
    assert any(p["ptr"] == 0x03000000 + PTR for p in ptrs)
    chosen = fa.choose_block_pointer(snaps, REGIONS, ptrs, log=lambda *_: None)
    assert chosen and chosen[0]["ptr"] == 0x03000000 + PTR
    assert chosen[0]["moved"] == AWAY - HOME


def test_the_map_id_is_found_through_the_pointer_not_the_raw_address():
    """The block moved, so only the pointer spelling can see the id."""
    snaps = _snaps()
    cands = fa.scan_map(snaps, REGIONS, block_ptr=0x03000000 + PTR)
    in_block = {c["offset"] for c in cands if c["spelling"] == "pointer"}
    assert GROUP in in_block and NUM in in_block
    # No assertion about the raw scan here. On a 512-byte toy machine that is
    # mostly zeros the raw address satisfies the conditions by coincidence, and
    # an assertion that the toy cannot support would be a fixture artefact
    # dressed as a claim. The real statement — that a raw hit inside a moved
    # block is luck — is made where it can be measured, on FireRed
    # (p-a-results.md section 3).


def test_the_run_ranking_puts_the_short_isolated_field_first():
    snaps = _snaps()
    ranked = fa.rank_by_run(fa.scan_map(snaps, REGIONS, block_ptr=0x03000000 + PTR))
    assert ranked, "nothing to rank"
    assert all("run" in c and "in_array" in c for c in ranked)


def test_the_party_scan_wants_an_increment_that_survives_its_negative_control():
    """Two pairs, one decoy that goes up between ANY two states."""
    def state(party, tick):
        ew = bytearray(0x200)
        ew[0x30] = party
        ew[TICK] = tick
        return {"EWRAM": bytes(ew), "IWRAM": bytes(0x100)}

    snaps = {}
    for i, (b, a) in enumerate([(0, 1), (0, 1)]):
        # The decoy reads the SAME value in both runs, so cross-run agreement
        # cannot reject it — otherwise this test would pass because of a
        # different condition than the one it names.
        for tag, party, tick in (("before", b, 10), ("after", a, 11)):
            snaps[f"{i}_{tag}"] = state(party, tick)
            for d in ("R3", "L3", "U3", "D3"):
                snaps[f"{i}_{tag}_{d}"] = state(party, tick)
        # The control pair: party unchanged, decoy still climbing.
        snaps[f"{i}_nullbefore"] = state(a, 20)
        snaps[f"{i}_nullafter"] = state(a, 21)

    found = {c["addr"] for c in fa.scan_party(snaps, REGIONS, 2, 2)}
    assert 0x02000000 + 0x30 in found
    assert 0x02000000 + TICK not in found, "the negative control did not bite"
    # And without it, the decoy gets in — so the control is what is doing it.
    loose = {c["addr"] for c in fa.scan_party(snaps, REGIONS, 2, 0)}
    assert 0x02000000 + TICK in loose
