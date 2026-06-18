"""P5 — the savepoint bundle is tamper-sealed.

An official run paused overnight leaves a savestate on disk; a seal lets the
official-continue path prove it wasn't hand-edited before resuming. The seal
covers the SCORE-bearing parts (emulator.state + referee_state.json +
task_master_state.json), so editing a gate stamp or the game state is detectable.
"""

from pathlib import Path

from src.core.snapshots import SnapshotManager
from src.referee.referee import Referee


class _FakeEmu:
    def save_state(self, filepath):
        Path(filepath).write_bytes(b"EMUSTATE-v1")


def _mgr(tmp_path):
    return SnapshotManager(
        {"snapshots_directory": str(tmp_path / "snaps"),
         "state_file": str(tmp_path / "state.json")},
        _FakeEmu(),
    )


class _Logger:
    def log_event(self, *a, **k):
        pass


def _save(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    ref = Referee([], _FakeEmu(), _Logger(), tmp_path)
    ref.stamps = {"left_bedroom": 3, "brock_defeated": 40}
    return _mgr(tmp_path).save_run_savepoint(
        run_dir=run_dir, turn=40, kind="crash", referee_state=ref.export_state(),
    )


def test_seal_written_and_verifies(tmp_path):
    target = _save(tmp_path)
    assert (target / "checkpoint.sha256").exists()
    assert SnapshotManager.verify_savepoint(target) is True


def test_tampering_emulator_state_breaks_seal(tmp_path):
    target = _save(tmp_path)
    (target / "emulator.state").write_bytes(b"EMUSTATE-HACKED")
    assert SnapshotManager.verify_savepoint(target) is False


def test_tampering_a_gate_stamp_breaks_seal(tmp_path):
    # The integrity property that matters for scoring: editing the referee latch
    # (e.g. crediting an un-earned gate) must be detectable.
    target = _save(tmp_path)
    ref_path = target / "referee_state.json"
    ref_path.write_text(ref_path.read_text().replace('"left_bedroom": 3',
                                                      '"left_bedroom": 1'))
    assert SnapshotManager.verify_savepoint(target) is False


def test_missing_seal_is_treated_as_legacy_pass(tmp_path):
    # A pre-seal savepoint has no checkpoint.sha256 — it must not block continuing.
    target = _save(tmp_path)
    (target / "checkpoint.sha256").unlink()
    assert SnapshotManager.verify_savepoint(target) is True
