"""Tests for the referee checkpoint ladder loader.

Covers the real v1 ladder (ordering, deadlines, null handling) plus synthetic
unit tests for each validation rule.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from src.referee.checkpoints import (
    Checkpoint,
    CheckpointLadder,
    load_checkpoints,
    load_ladder,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
REAL_LADDER = REPO_ROOT / "configs" / "checkpoints-firered-v1.yaml"


# --- real ladder ----------------------------------------------------------

# The 10 enforced gates (bedroom -> viridian_forest) with their exact deadlines.
ENFORCED_DEADLINES = {
    "left_bedroom": 30,
    "left_house": 50,
    "oaks_lab_entered": 100,
    "starter_chosen": 120,
    "rival1_done": 150,
    "route1_reached": 180,
    "viridian_reached": 230,
    "parcel_delivered": 260,
    "pokedex_received": 310,
    "viridian_forest_reached": 370,
}

# The 3 Pewter gates that stay observed-only (null deadline).
NULL_GATES = {"pewter_reached", "pewter_gym_entered", "brock_defeated"}


def test_real_ladder_loads_metadata():
    ladder = load_ladder(REAL_LADDER)
    assert isinstance(ladder, CheckpointLadder)
    assert ladder.benchmark_version == "pokebench-v1-draft"
    assert ladder.game == "firered-us"
    assert ladder.rom_sha1["v1_0"] == "41cb23d8dccc8ebd7c649cd8fbb58eeace6e2fdc"
    assert ladder.rom_sha1["v1_1"] == "dd5945db9b930750cb39d00c84da8571feebf417"


def test_real_ladder_has_13_checkpoints_in_order():
    cps = load_checkpoints(REAL_LADDER)
    assert len(cps) == 13
    assert cps[0].id == "left_bedroom"
    assert cps[-1].id == "brock_defeated"
    # Full order preserved as written.
    expected_order = [
        "left_bedroom",
        "left_house",
        "oaks_lab_entered",
        "starter_chosen",
        "rival1_done",
        "route1_reached",
        "viridian_reached",
        "parcel_delivered",
        "pokedex_received",
        "viridian_forest_reached",
        "pewter_reached",
        "pewter_gym_entered",
        "brock_defeated",
    ]
    assert [c.id for c in cps] == expected_order


def test_real_ladder_enforced_deadlines_exact():
    cps = {c.id: c for c in load_checkpoints(REAL_LADDER)}
    for cp_id, deadline in ENFORCED_DEADLINES.items():
        assert isinstance(cps[cp_id].deadline_turn, int), cp_id
        assert not isinstance(cps[cp_id].deadline_turn, bool), cp_id
        assert cps[cp_id].deadline_turn == deadline, cp_id


def test_real_ladder_pewter_gates_are_null():
    cps = {c.id: c for c in load_checkpoints(REAL_LADDER)}
    for cp_id in NULL_GATES:
        assert cps[cp_id].deadline_turn is None, cp_id


def test_real_ladder_flag_id_hex_parses_to_int():
    cps = {c.id: c for c in load_checkpoints(REAL_LADDER)}
    # 0x828 -> 2088
    assert cps["starter_chosen"].signature["flag_id"] == 2088
    # var_id parsed too: 0x4057 -> 16471
    assert cps["parcel_delivered"].signature["var_id"] == 0x4057
    assert cps["parcel_delivered"].signature["min_value"] == 2


def test_real_ladder_cross_check_parsed():
    cps = {c.id: c for c in load_checkpoints(REAL_LADDER)}
    cc = cps["oaks_lab_entered"].cross_check
    assert cc is not None
    assert cc["type"] == "flag"
    assert cc["flag_id"] == 0x2CF  # parsed from "0x2CF"


# --- synthetic unit tests -------------------------------------------------


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "ladder.yaml"
    p.write_text(textwrap.dedent(body))
    return p


_HEADER = """\
benchmark_version: test-v1
game: firered-us
rom_sha1:
  v1_0: aaa
checkpoints:
"""


def test_unknown_type_raises(tmp_path):
    p = _write(
        tmp_path,
        _HEADER
        + """\
  - id: weird
    name: "Weird"
    type: teleport
    signature: {x: 1}
    deadline_turn: 10
""",
    )
    with pytest.raises(ValueError, match="unknown type"):
        load_checkpoints(p)


def test_duplicate_id_raises(tmp_path):
    p = _write(
        tmp_path,
        _HEADER
        + """\
  - id: dup
    name: "One"
    type: map
    signature: {map_group: 1, map_num: 0}
    deadline_turn: 10
  - id: dup
    name: "Two"
    type: map
    signature: {map_group: 2, map_num: 0}
    deadline_turn: 20
""",
    )
    with pytest.raises(ValueError, match="duplicate checkpoint id"):
        load_checkpoints(p)


def test_map_missing_map_num_raises(tmp_path):
    p = _write(
        tmp_path,
        _HEADER
        + """\
  - id: m
    name: "Map"
    type: map
    signature: {map_group: 1}
    deadline_turn: 10
""",
    )
    with pytest.raises(ValueError, match="map_num"):
        load_checkpoints(p)


def test_var_missing_min_value_raises(tmp_path):
    p = _write(
        tmp_path,
        _HEADER
        + """\
  - id: v
    name: "Var"
    type: var
    signature: {var_id: 0x4057}
    deadline_turn: 10
""",
    )
    with pytest.raises(ValueError, match="min_value"):
        load_checkpoints(p)


def test_null_deadline_accepted(tmp_path):
    p = _write(
        tmp_path,
        _HEADER
        + """\
  - id: observed
    name: "Observed only"
    type: map
    signature: {map_group: 3, map_num: 2}
    deadline_turn: null
""",
    )
    cps = load_checkpoints(p)
    assert len(cps) == 1
    assert cps[0].deadline_turn is None


def test_non_int_deadline_raises(tmp_path):
    p = _write(
        tmp_path,
        _HEADER
        + """\
  - id: bad
    name: "Bad deadline"
    type: map
    signature: {map_group: 3, map_num: 2}
    deadline_turn: "soon"
""",
    )
    with pytest.raises(ValueError, match="deadline_turn"):
        load_checkpoints(p)


def test_cross_check_parsed_and_validated(tmp_path):
    p = _write(
        tmp_path,
        _HEADER
        + """\
  - id: with_cc
    name: "With cross check"
    type: flag
    signature: {flag_id: 0x828}
    cross_check: {type: party, min_count: 1}
    deadline_turn: 120
""",
    )
    cps = load_checkpoints(p)
    assert cps[0].signature["flag_id"] == 0x828
    assert cps[0].cross_check == {"type": "party", "min_count": 1}


def test_cross_check_missing_field_raises(tmp_path):
    p = _write(
        tmp_path,
        _HEADER
        + """\
  - id: bad_cc
    name: "Bad cross check"
    type: flag
    signature: {flag_id: 0x828}
    cross_check: {type: var, var_id: 0x4055}
    deadline_turn: 120
""",
    )
    with pytest.raises(ValueError, match="min_value"):
        load_checkpoints(p)


def test_flag_id_hex_string_parses(tmp_path):
    p = _write(
        tmp_path,
        _HEADER
        + """\
  - id: f
    name: "Flag"
    type: flag
    signature: {flag_id: "0x828"}
    deadline_turn: 10
""",
    )
    cps = load_checkpoints(p)
    assert cps[0].signature["flag_id"] == 2088


def test_returns_checkpoint_dataclass(tmp_path):
    p = _write(
        tmp_path,
        _HEADER
        + """\
  - id: c
    name: "Checkpoint"
    type: party
    signature: {min_count: 1}
    deadline_turn: 10
""",
    )
    cps = load_checkpoints(p)
    assert isinstance(cps[0], Checkpoint)
    assert cps[0].id == "c"
    assert cps[0].type == "party"
