"""Agent state (memory dictionary) as a JSON file."""

import json
from pathlib import Path
from typing import Any, Optional


class StateManager:
    """Manages the agent's JSON memory dictionary.

    The agent writes updates via memory_updates on its output model.
    The turn manager applies updates via set_by_path / delete_by_path.
    """

    def __init__(self, state_file: str):
        self.state_file = Path(state_file)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

        if self.state_file.exists():
            with open(self.state_file) as f:
                self._data = json.load(f)
        else:
            self._data = {}

    def get_truncated_view(self) -> dict:
        """Return the full state dict (for display / injection into prompts)."""
        return self._data

    def get_by_path(self, path: str) -> Any:
        """Get a value by dot-separated path. Returns None if not found."""
        if not path:
            return self._data
        keys = path.split(".")
        node = self._data
        for k in keys:
            if not isinstance(node, dict) or k not in node:
                return None
            node = node[k]
        return node

    def set_by_path(self, path: str, value: Any) -> None:
        """Set a value by dot-separated path. Creates intermediate dicts."""
        keys = path.split(".")
        node = self._data
        for k in keys[:-1]:
            if k not in node or not isinstance(node[k], dict):
                node[k] = {}
            node = node[k]
        node[keys[-1]] = value

    def delete_by_path(self, path: str) -> None:
        """Delete a key by dot-separated path."""
        keys = path.split(".")
        node = self._data
        for k in keys[:-1]:
            if not isinstance(node, dict) or k not in node:
                return
            node = node[k]
        if isinstance(node, dict) and keys[-1] in node:
            del node[keys[-1]]

    def save(self) -> None:
        """Write state to disk."""
        with open(self.state_file, "w") as f:
            json.dump(self._data, f, indent=2)
