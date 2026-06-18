"""P2 — a continued run restores the referee gate latch, capped to the savepoint turn.

On --continue the Referee auto-loads <run_dir>/referee_state.json on construction.
_restore_referee_state writes that file from the savepoint BUNDLE (P1), dropping any
gate stamped AFTER the savepoint turn — the hard-kill defense: the source run's
latch may have run ahead of the savepoint's emulator state, and crediting a gate the
restored game hasn't reached would corrupt the score.
"""

import json
from pathlib import Path

from src.cli.runner import _restore_referee_state
from src.referee.referee import Referee


class _FakeEmu:
    def save_state(self, filepath):
        Path(filepath).write_bytes(b"x")


class _FakeLogger:
    def log_event(self, *a, **k):
        pass


def _bundle(tmp_path, stamps, autofilled):
    sp = tmp_path / "savepoints" / "turn_20"
    sp.mkdir(parents=True)
    (sp / "referee_state.json").write_text(
        json.dumps({"stamps": stamps, "autofilled": autofilled})
    )
    return sp


def test_restore_caps_stamps_to_savepoint_turn(tmp_path):
    # 'b' was stamped at turn 30 — AFTER the turn-20 savepoint — so it must be dropped.
    sp = _bundle(tmp_path, {"a": 10, "b": 30}, ["b"])
    new_run = tmp_path / "new"
    new_run.mkdir()

    _restore_referee_state(sp, new_run, up_to_turn=20)

    restored = json.loads((new_run / "referee_state.json").read_text())
    assert restored == {"stamps": {"a": 10}, "autofilled": []}


def test_restored_latch_is_what_a_fresh_referee_loads(tmp_path):
    # Integration with the real auto-load path: a Referee built over the new run
    # dir picks up exactly the capped latch.
    sp = _bundle(tmp_path, {"a": 10, "b": 30}, [])
    new_run = tmp_path / "new"
    new_run.mkdir()
    _restore_referee_state(sp, new_run, up_to_turn=20)

    # Ladder must contain the ids for _load_state to keep them.
    from src.referee.checkpoints import Checkpoint
    nodes = [
        Checkpoint(id="a", name="A", type="map", signature={"map_group": 1, "map_num": 1},
                   deadline_turn=25),
        Checkpoint(id="b", name="B", type="map", signature={"map_group": 1, "map_num": 2},
                   deadline_turn=50),
    ]
    ref = Referee(nodes, _FakeEmu(), _FakeLogger(), new_run)
    assert ref.stamps == {"a": 10}  # b (turn 30 > savepoint 20) was never written


def test_missing_bundle_file_is_safe_noop(tmp_path):
    sp = tmp_path / "savepoints" / "turn_20"
    sp.mkdir(parents=True)  # no referee_state.json in the bundle
    new_run = tmp_path / "new"
    new_run.mkdir()

    _restore_referee_state(sp, new_run, up_to_turn=20)

    # No file written → continued run starts with a fresh (empty) latch.
    assert not (new_run / "referee_state.json").exists()


def test_none_savepoint_is_safe_noop(tmp_path):
    new_run = tmp_path / "new"
    new_run.mkdir()
    _restore_referee_state(None, new_run, up_to_turn=5)
    assert not (new_run / "referee_state.json").exists()
