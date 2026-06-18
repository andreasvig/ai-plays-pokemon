"""P1 — the savepoint is an ATOMIC turn-N bundle that includes the referee latch.

Resuming an official benchmark run must be turn-exact and score-consistent. That
requires the emulator state and the referee's gate stamps to live in the SAME
savepoint bundle (captured at the same turn) rather than in two files written by
two code paths on two cadences. These tests pin:

  1. Referee.export_state() is the single on-disk shape (== what _persist_state writes),
  2. save_run_savepoint writes that dict into the bundle as referee_state.json,
  3. a None referee_state (TM/non-benchmark runs) writes no file (no regression).

No emulator/network — a fake emulator just writes a sentinel state file.
"""

import json
from pathlib import Path

from src.core.snapshots import SnapshotManager
from src.referee.referee import Referee


class _FakeEmu:
    def save_state(self, filepath: str) -> None:
        Path(filepath).write_bytes(b"EMUSTATE")


class _FakeLogger:
    def log_event(self, *a, **k):
        pass


def _referee(tmp_path) -> Referee:
    # Empty ladder is fine: export_state dumps self.stamps/autofilled directly and
    # never polls. We set the latch by hand.
    ref = Referee([], _FakeEmu(), _FakeLogger(), tmp_path)
    ref.stamps = {"left_bedroom": 3, "left_house": 7}
    ref.autofilled = {"left_house"}
    return ref


def _mgr(tmp_path) -> SnapshotManager:
    return SnapshotManager(
        {"snapshots_directory": str(tmp_path / "snaps"),
         "state_file": str(tmp_path / "state.json")},
        _FakeEmu(),
    )


def test_export_state_matches_persisted_shape(tmp_path):
    ref = _referee(tmp_path)
    ref._persist_state()
    on_disk = json.loads((tmp_path / "referee_state.json").read_text())
    assert ref.export_state() == on_disk
    assert on_disk == {"stamps": {"left_bedroom": 3, "left_house": 7},
                       "autofilled": ["left_house"]}


def test_savepoint_bundle_includes_referee_state(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    ref = _referee(tmp_path)

    target = _mgr(tmp_path).save_run_savepoint(
        run_dir=run_dir, turn=7, kind="crash",
        referee_state=ref.export_state(),
    )

    # The bundle is self-consistent at turn 7: emulator state + gate latch together.
    assert (target / "emulator.state").read_bytes() == b"EMUSTATE"
    bundle = json.loads((target / "referee_state.json").read_text())
    assert bundle == {"stamps": {"left_bedroom": 3, "left_house": 7},
                      "autofilled": ["left_house"]}


def test_savepoint_without_referee_writes_no_referee_file(tmp_path):
    # Casual / TM-only runs pass referee_state=None — no file, no behavior change.
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    target = _mgr(tmp_path).save_run_savepoint(run_dir=run_dir, turn=5, kind="periodic")
    assert (target / "emulator.state").exists()
    assert not (target / "referee_state.json").exists()
