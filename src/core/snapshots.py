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
