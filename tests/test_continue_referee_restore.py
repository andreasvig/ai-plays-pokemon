"""P2 — a continued run restores the referee gate latch, capped to the savepoint turn.

On --continue the Referee auto-loads <run_dir>/referee_state.json on construction.
_restore_referee_state writes that file from the savepoint BUNDLE (P1), dropping any
gate stamped AFTER the savepoint turn — the hard-kill defense: the source run's
latch may have run ahead of the savepoint's emulator state, and crediting a gate the
restored game hasn't reached would corrupt the score.
"""

import json
from pathlib import Path

from src.cli.runner import _restore_referee_state, _root_run_name
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


def test_stamped_events_replays_restored_gates_in_turn_order(tmp_path):
    # The live spectate gate HUD is built only from referee_checkpoint events on
    # the session stream, so a continue must re-announce the restored latch or
    # cleared gates show as un-reached. stamped_events() is what the runner
    # injects into the EventBridge.
    from src.referee.checkpoints import Checkpoint
    nodes = [
        Checkpoint(id="a", name="A", type="map", signature={"map_group": 1, "map_num": 1},
                   deadline_turn=25),
        Checkpoint(id="b", name="B", type="map", signature={"map_group": 1, "map_num": 2},
                   deadline_turn=50),
    ]
    new_run = tmp_path / "run"
    new_run.mkdir()
    # Seed a restored latch (out of turn order on disk) + one autofilled gate.
    (new_run / "referee_state.json").write_text(
        json.dumps({"stamps": {"b": 18, "a": 7}, "autofilled": ["b"]})
    )
    ref = Referee(nodes, _FakeEmu(), _FakeLogger(), new_run)

    events = ref.stamped_events()
    assert [e["checkpoint_id"] for e in events] == ["a", "b"]  # ordered by turn
    assert all(e["type"] == "referee_checkpoint" for e in events)
    assert events[0] == {
        "type": "referee_checkpoint", "checkpoint_id": "a", "name": "A",
        "checkpoint_type": "map", "turn": 7, "auto": False,
    }
    assert events[1]["turn"] == 18 and events[1]["auto"] is True


def test_stamped_events_empty_when_no_gates(tmp_path):
    from src.referee.checkpoints import Checkpoint
    nodes = [Checkpoint(id="a", name="A", type="map", signature={"map_group": 1, "map_num": 1},
                        deadline_turn=25)]
    run = tmp_path / "run"
    run.mkdir()
    ref = Referee(nodes, _FakeEmu(), _FakeLogger(), run)
    assert ref.stamped_events() == []


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


def test_root_run_name_strips_chained_continue_suffixes():
    chained = (
        "config-3.13__gemma-4-31b-thinking"
        "_continued_from_turn_300"
        "_continued_from_turn_588"
    )
    assert _root_run_name(chained) == "config-3.13__gemma-4-31b-thinking"
    assert _root_run_name("config-3.13__gemma-4-31b-thinking") == (
        "config-3.13__gemma-4-31b-thinking"
    )
