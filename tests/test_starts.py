"""The start-state registry, its executor wiring, and its API validation.

Three layers, mirroring tests/test_recording_filename.py:
  1. the registry as a pure loader (injected YAML, no repo state);
  2. RunExecutor._resolve_start + _stamp_start;
  3. the enqueue validator and GET /api/starts over TestClient.
"""

from __future__ import annotations

import json

import pytest
import yaml

from src.app.roms import Rom
from src.app.starts import (
    REQUIRED_FILES,
    Start,
    default_start,
    get_start,
    load_starts,
    starts_for_rom,
)


# ───────────────────────────── fixtures ─────────────────────────────


def _write_registry(tmp_path, entries):
    path = tmp_path / "starts.yaml"
    path.write_text(yaml.safe_dump({"starts": entries}))
    return path


def _complete_savepoint(base):
    """A dir that Start.exists() accepts — all three required files present."""
    base.mkdir(parents=True, exist_ok=True)
    for name in REQUIRED_FILES:
        (base / name).write_text("{}")
    return base


# ───────────────────────── 1. the registry ──────────────────────────


def test_loads_entries_in_order(tmp_path):
    path = _write_registry(tmp_path, [
        {"rom": "firered", "label": "boy", "name": "Red", "path": "a", "default": True},
        {"rom": "firered", "label": "girl", "name": "Leaf", "path": "b"},
    ])
    starts = load_starts(path)
    assert [s.label for s in starts] == ["boy", "girl"]
    assert starts[0].is_default and not starts[1].is_default


def test_empty_starts_list_is_legal(tmp_path):
    """A deployment offering no choosable openings is not broken — every ROM just
    falls back to its own default."""
    path = tmp_path / "starts.yaml"
    path.write_text(yaml.safe_dump({"starts": []}))
    assert load_starts(path) == []
    path.write_text(yaml.safe_dump({}))
    assert load_starts(path) == []


def test_labels_are_scoped_to_a_rom(tmp_path):
    """The same label on two ROMs is legal; on ONE ROM it is a duplicate."""
    ok = _write_registry(tmp_path / "ok", [
        {"rom": "firered", "label": "girl", "name": "Leaf", "path": "a"},
        {"rom": "emerald", "label": "girl", "name": "May", "path": "b"},
    ]) if (tmp_path / "ok").mkdir() or True else None
    assert len(load_starts(ok)) == 2

    (tmp_path / "bad").mkdir()
    dup = _write_registry(tmp_path / "bad", [
        {"rom": "firered", "label": "girl", "name": "Leaf", "path": "a"},
        {"rom": "firered", "label": "girl", "name": "Other", "path": "b"},
    ])
    with pytest.raises(ValueError, match="duplicate start 'girl'"):
        load_starts(dup)


def test_two_defaults_on_one_rom_is_an_error(tmp_path):
    path = _write_registry(tmp_path, [
        {"rom": "firered", "label": "boy", "name": "Red", "path": "a", "default": True},
        {"rom": "firered", "label": "girl", "name": "Leaf", "path": "b", "default": True},
    ])
    with pytest.raises(ValueError, match="has 2 defaults"):
        load_starts(path)


def test_two_defaults_on_DIFFERENT_roms_is_fine(tmp_path):
    path = _write_registry(tmp_path, [
        {"rom": "firered", "label": "boy", "name": "Red", "path": "a", "default": True},
        {"rom": "emerald", "label": "truck", "name": "Truck", "path": "b", "default": True},
    ])
    assert len(load_starts(path)) == 2


@pytest.mark.parametrize("field", ["rom", "label", "name", "path"])
def test_missing_required_field_is_an_error(tmp_path, field):
    entry = {"rom": "firered", "label": "boy", "name": "Red", "path": "a"}
    del entry[field]
    with pytest.raises(ValueError, match=f"missing or invalid {field!r}"):
        load_starts(_write_registry(tmp_path, [entry]))


def test_default_start_falls_back_to_first_when_none_marked(tmp_path):
    path = _write_registry(tmp_path, [
        {"rom": "firered", "label": "girl", "name": "Leaf", "path": "a"},
        {"rom": "firered", "label": "boy", "name": "Red", "path": "b"},
    ])
    assert default_start("firered", path).label == "girl"


def test_default_start_is_none_for_an_unlisted_rom(tmp_path):
    """None is the signal to use the pre-registry fallback, not an error."""
    path = _write_registry(tmp_path, [
        {"rom": "firered", "label": "boy", "name": "Red", "path": "a"},
    ])
    assert default_start("emerald", path) is None
    assert starts_for_rom("emerald", path) == []


def test_get_start_names_the_valid_labels(tmp_path):
    path = _write_registry(tmp_path, [
        {"rom": "firered", "label": "boy", "name": "Red", "path": "a"},
        {"rom": "firered", "label": "girl", "name": "Leaf", "path": "b"},
    ])
    with pytest.raises(KeyError, match="boy, girl"):
        get_start("firered", "gril", path)


def test_exists_requires_all_three_files(tmp_path):
    base = _complete_savepoint(tmp_path / "sp")
    start = Start(rom="firered", label="girl", name="Leaf", path=str(base))
    assert start.exists()
    # Drop each required file in turn — every one of them is load-bearing.
    for name in REQUIRED_FILES:
        (base / name).unlink()
        assert not start.exists(), f"missing {name} should make exists() false"
        (base / name).write_text("{}")
    assert start.exists()


def test_exists_is_false_for_a_missing_dir(tmp_path):
    start = Start(rom="firered", label="girl", name="Leaf", path=str(tmp_path / "nope"))
    assert not start.exists()


# ─────────────────── 2. the executor's resolution ───────────────────


def _rom(rom_id="firered", *, is_default=True, start_save=None):
    return Rom(
        id=rom_id, name=rom_id, path=f"roms/{rom_id}.gba", game=f"{rom_id}-us",
        game_name=rom_id, game_code="XXXX", sha1="0" * 40,
        start_save=start_save, is_default=is_default,
    )


def _executor(canonical="FIXTURE_CANONICAL"):
    from src.app.executor import RunExecutor

    ex = RunExecutor.__new__(RunExecutor)
    ex.canonical_save = canonical
    return ex


def test_no_label_uses_the_injected_canonical_save_for_the_default_rom():
    """The whole point of the two-tier design: a run that names no start must not
    be routed through a hard-coded registry path, because ``canonical_save`` is a
    test seam."""
    assert _executor()._resolve_start(_rom(), None) == "FIXTURE_CANONICAL"


def test_no_label_uses_the_roms_own_start_save_for_a_NON_default_rom():
    rom = _rom("emerald", is_default=False, start_save="configs/saves/emerald-truck")
    assert _executor()._resolve_start(rom, None) == "configs/saves/emerald-truck"


def test_no_label_and_no_start_save_means_the_title_screen():
    rom = _rom("other", is_default=False, start_save=None)
    assert _executor()._resolve_start(rom, None) is None


def test_an_explicit_label_resolves_through_the_registry():
    """Reads the REAL configs/starts.yaml — this is the wiring that ships."""
    got = _executor()._resolve_start(_rom(), "girl")
    assert got == "configs/saves/firered-girl"


def test_an_unknown_label_raises_rather_than_silently_defaulting():
    with pytest.raises(KeyError):
        _executor()._resolve_start(_rom(), "gril")


def test_stamp_start_records_an_explicit_label():
    from src.app.executor import RunExecutor

    cfg: dict = {}
    RunExecutor._stamp_start(cfg, _rom(), "girl", "configs/saves/firered-girl")
    assert cfg["_start_label"] == "girl"
    assert cfg["_start_path"] == "configs/saves/firered-girl"


def test_stamp_start_reverse_resolves_the_label_when_none_was_named():
    """A run enqueued with no --start still records WHICH opening it played, by
    matching the resolved path back to a registry entry."""
    from src.app.executor import RunExecutor

    cfg: dict = {}
    RunExecutor._stamp_start(cfg, _rom(), None, "configs/saves/pokebench-v1")
    assert cfg["_start_label"] == "boy"


def test_stamp_start_leaves_no_label_for_an_unregistered_snapshot():
    """An arbitrary savepoint (a continue's, a fixture's) gets a path but no
    label, rather than a wrong one."""
    from src.app.executor import RunExecutor

    cfg: dict = {}
    RunExecutor._stamp_start(cfg, _rom(), None, "local/runs/whatever/savepoints/t10")
    assert "_start_label" not in cfg
    assert cfg["_start_path"] == "local/runs/whatever/savepoints/t10"


def test_stamp_start_writes_nothing_for_a_title_screen_boot():
    from src.app.executor import RunExecutor

    cfg: dict = {}
    RunExecutor._stamp_start(cfg, _rom(), None, None)
    assert cfg == {}


def test_stamp_start_never_touches_load_snapshot():
    """`load_snapshot` is acted on by the run loop; the snapshot is already passed
    as an argument, so writing it here would risk a second load."""
    from src.app.executor import RunExecutor

    cfg: dict = {"load_snapshot": None}
    RunExecutor._stamp_start(cfg, _rom(), "girl", "configs/saves/firered-girl")
    assert cfg["load_snapshot"] is None


# ──────────────── 3. the filename segment + the API ─────────────────


def test_filename_carries_the_character(tmp_path):
    from src.app.recording_name import recording_stem

    run = tmp_path / "2026-08-03_14-00-00_config-4.0__claude-opus-5-high"
    run.mkdir()
    (run / "config.json").write_text(json.dumps({
        "_config_path": "configs/config-4.0.yaml",
        "_llm_alias": "claude-opus-5(high)",
        "mode": "benchmark",
        "_start_label": "girl",
    }))
    (run / "run_summary.json").write_text(json.dumps({
        "kind": "casual",
        "session": {"total_turns": 12, "started_at": "2026-08-03T14:00:00"},
        "cost": {"total_usd": 1.0},
    }))
    stem = recording_stem(run)
    assert "as-girl" in stem
    # Ordered before the kind segment, so two names line up column-wise.
    assert stem.index("as-girl") < stem.index("casual-speed")


def test_filename_omits_the_segment_when_unstamped(tmp_path):
    """Every run recorded before 2026-08-03 has no _start_label."""
    from src.app.recording_name import recording_stem

    run = tmp_path / "2026-08-01_10-00-00_config-4.0__x"
    run.mkdir()
    (run / "config.json").write_text(json.dumps({"_llm_alias": "x", "mode": "freeplay"}))
    (run / "run_summary.json").write_text(json.dumps({
        "kind": "casual", "session": {"total_turns": 3}, "cost": {},
    }))
    assert "as-" not in recording_stem(run)


def test_api_starts_lists_both_openings():
    from fastapi.testclient import TestClient
    from src.dashboard import server as srv

    with TestClient(srv.app) as client:
        resp = client.get("/api/starts")
    assert resp.status_code == 200
    rows = resp.json()
    labels = {r["label"] for r in rows if r["rom"] == "firered"}
    assert {"boy", "girl"} <= labels
    girl = next(r for r in rows if r["label"] == "girl")
    assert girl["exists"] is True
    assert girl["default"] is False


def test_validate_start_rejects_an_unknown_label_naming_the_valid_ones():
    from fastapi import HTTPException
    from src.dashboard.server import _validate_start

    with pytest.raises(HTTPException) as exc:
        _validate_start("gril", "firered")
    assert exc.value.status_code == 400
    assert "boy, girl" in exc.value.detail


def test_validate_start_rejects_a_label_from_another_rom():
    """'girl' is a FireRed label; Emerald offers nothing, so it must not pass."""
    from fastapi import HTTPException
    from src.dashboard.server import _validate_start

    with pytest.raises(HTTPException) as exc:
        _validate_start("girl", "emerald")
    assert exc.value.status_code == 400
    assert "emerald" in exc.value.detail


def test_validate_start_passes_none_and_empty_through():
    from src.dashboard.server import _validate_start

    assert _validate_start(None, "firered") is None
    assert _validate_start("", "firered") is None


def test_validate_start_accepts_a_real_label():
    from src.dashboard.server import _validate_start

    assert _validate_start("girl", "firered") == "girl"
    # None rom → the registry default rom, which is firered.
    assert _validate_start("girl", None) == "girl"


def test_queued_run_carries_start_and_defaults_to_none():
    from src.app.models import QueuedRun, RunKind

    stamp = "2026-08-03T14:00:00Z"
    item = QueuedRun(queue_id="q_1", kind=RunKind.casual, model="m", enqueued_at=stamp)
    assert item.start is None
    item = QueuedRun(
        queue_id="q_2", kind=RunKind.casual, model="m", start="girl", enqueued_at=stamp
    )
    assert item.start == "girl"
