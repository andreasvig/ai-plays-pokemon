"""Snapshot system for saving and loading full game + agent state."""

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any, Optional

from src.emulator.emulator import EmulatorClient


class SnapshotManager:
    """Manages snapshots of emulator state + agent state files.

    A snapshot is a folder containing:
    - emulator.state: mGBA save state
    - state.json: copy of the agent's state file
    - metadata.json: timestamp, description, task info
    """

    def __init__(self, config: dict[str, Any], emulator: EmulatorClient):
        self.snapshots_dir = Path(config.get("snapshots_directory", "snapshots"))
        self.state_file = Path(config.get("state_file", "state/state.json"))
        self.emulator = emulator

        # Ensure directories exist
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)

    def save_snapshot(
        self,
        name: str,
        description: str = "",
        task_info: Optional[str] = None,
    ) -> Path:
        """Save a snapshot of the current game + agent state.

        Args:
            name: Name for the snapshot folder (sanitized for filesystem)
            description: Human-readable description of the snapshot
            task_info: Optional task that was just completed

        Returns:
            Path to the created snapshot folder
        """
        # Sanitize name for filesystem
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
        snapshot_path = self.snapshots_dir / safe_name

        # Handle name collision
        if snapshot_path.exists():
            timestamp = int(time.time())
            snapshot_path = self.snapshots_dir / f"{safe_name}_{timestamp}"

        snapshot_path.mkdir(parents=True)

        # Save emulator state
        emu_state_path = str(snapshot_path / "emulator.state")
        self.emulator.save_state(emu_state_path)

        # Copy agent state file
        if self.state_file.exists():
            shutil.copy2(self.state_file, snapshot_path / "state.json")

        # Copy tasks file if it exists alongside state
        tasks_src = self.state_file.parent / "tasks.json"
        if tasks_src.exists():
            shutil.copy2(tasks_src, snapshot_path / "tasks.json")

        # Write metadata
        metadata = {
            "name": name,
            "description": description,
            "task_info": task_info,
            "timestamp": time.time(),
            "timestamp_human": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        with open(snapshot_path / "metadata.json", "w") as f:
            json.dump(metadata, f, indent=2)

        return snapshot_path

    def save_run_savepoint(
        self,
        run_dir: Path,
        turn: int,
        kind: str = "periodic",
        task_master_state: Optional[dict] = None,
    ) -> Path:
        """Save a run-scoped savepoint to <run_dir>/savepoints/turn_<N>/.

        Unlike save_snapshot, this writes inside the run dir (not the global
        snapshots/ pool) and uses a deterministic per-turn directory. If the
        target dir already exists (e.g. running `every_n_turns=5` twice with
        the same turn counter), it's overwritten.

        ``task_master_state`` — when TaskMaster is enabled, the run loop passes
        ``{current_task, current_task_turn, task_history[]}`` here and it is
        written as ``task_master_state.json``. This is the replacement for the
        legacy ``tasks.json`` task-state file in the TaskMaster path. On the
        TM-disabled legacy path it is None and only the tasks.json copy below
        runs (unchanged).
        """
        savepoints_root = run_dir / "savepoints"
        savepoints_root.mkdir(parents=True, exist_ok=True)
        target = savepoints_root / f"turn_{turn}"
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)

        self.emulator.save_state(str(target / "emulator.state"))

        if self.state_file.exists():
            shutil.copy2(self.state_file, target / "state.json")

        # TaskMaster path: persist task state to task_master_state.json (the
        # legacy tasks.json is bypassed when TM owns the task — see plan.md
        # "Reconciliation with existing task machinery").
        if task_master_state is not None:
            with open(target / "task_master_state.json", "w") as f:
                json.dump(task_master_state, f, indent=2, default=str)

        tasks_src = self.state_file.parent / "tasks.json"
        if tasks_src.exists():
            shutil.copy2(tasks_src, target / "tasks.json")

        metadata = {
            "name": f"{run_dir.name}_turn_{turn}",
            "turn": turn,
            "kind": kind,
            "source_run": str(run_dir),
            "timestamp": time.time(),
            "timestamp_human": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        with open(target / "metadata.json", "w") as f:
            json.dump(metadata, f, indent=2)

        return target

    @staticmethod
    def load_task_master_state(snapshot_path: str) -> Optional[dict]:
        """Read ``task_master_state.json`` from a savepoint dir, or None.

        Used by the --continue path to restore ``current_task`` /
        ``current_task_turn`` / ``task_history`` into a resumed run when
        TaskMaster is enabled. Returns None when the file is absent (TM-disabled
        source run, or a savepoint predating this format).
        """
        tm_state_path = Path(snapshot_path) / "task_master_state.json"
        if not tm_state_path.exists():
            return None
        try:
            with open(tm_state_path) as f:
                data = json.load(f)
            return data if isinstance(data, dict) else None
        except (json.JSONDecodeError, OSError):
            return None

    def load_snapshot(self, snapshot_path: str) -> dict:
        """Load a snapshot, restoring emulator and agent state.

        Args:
            snapshot_path: Path to the snapshot folder

        Returns:
            The metadata dict from the snapshot
        """
        path = Path(snapshot_path)

        if not path.exists():
            raise FileNotFoundError(f"Snapshot not found: {snapshot_path}")

        # Load emulator state
        emu_state = path / "emulator.state"
        if not emu_state.exists():
            raise FileNotFoundError(f"No emulator.state in snapshot: {snapshot_path}")
        self.emulator.load_state(str(emu_state))

        # Restore agent state file
        state_src = path / "state.json"
        if state_src.exists():
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(state_src, self.state_file)

        # Restore tasks file
        tasks_src = path / "tasks.json"
        if tasks_src.exists():
            shutil.copy2(tasks_src, self.state_file.parent / "tasks.json")

        # Read metadata
        metadata_file = path / "metadata.json"
        if metadata_file.exists():
            with open(metadata_file) as f:
                return json.load(f)

        return {}

    def list_snapshots(self) -> list[dict]:
        """List all available snapshots with their metadata."""
        snapshots = []
        if not self.snapshots_dir.exists():
            return snapshots

        for entry in sorted(self.snapshots_dir.iterdir()):
            if not entry.is_dir():
                continue
            metadata_file = entry / "metadata.json"
            if metadata_file.exists():
                with open(metadata_file) as f:
                    metadata = json.load(f)
                metadata["path"] = str(entry)
                snapshots.append(metadata)
            elif (entry / "emulator.state").exists():
                # Valid snapshot without metadata
                snapshots.append({
                    "name": entry.name,
                    "path": str(entry),
                    "description": "(no metadata)",
                })

        return snapshots

    def delete_snapshot(self, snapshot_path: str) -> None:
        """Delete a snapshot folder."""
        path = Path(snapshot_path)
        if path.exists() and path.is_dir():
            shutil.rmtree(path)
