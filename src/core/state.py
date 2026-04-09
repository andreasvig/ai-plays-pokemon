"""Agent state file system with _hide support and visibility-aware safety."""

import copy
import json
from pathlib import Path
from typing import Any, Optional


class StateManager:
    """Manages the agent's single JSON state file.

    All access goes through tool methods. The agent never touches the file directly.
    Tracks which keys the agent has "seen" this turn to enforce safety rules.
    """

    def __init__(self, state_file: str):
        self.state_file = Path(state_file)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

        # Load existing state or start empty
        if self.state_file.exists():
            with open(self.state_file) as f:
                self._data = json.load(f)
        else:
            self._data = {}

        # Keys the agent has "seen" this turn (via visibility or explicit read)
        self._seen_keys: set[str] = set()

    # --- Turn lifecycle ---

    def start_turn(self) -> None:
        """Reset seen tracking and mark all visible keys as seen."""
        self._seen_keys = set()
        # Walk the state and mark visible (non-hidden) keys as seen
        self._mark_visible_as_seen(self._data, "")

    def get_truncated_view(self) -> dict:
        """Return the state with hidden values replaced by '<hidden>'."""
        return self._truncate(self._data)

    # --- Tool methods ---

    def read_state(self, keys: list[str]) -> dict[str, Any]:
        """Read full (unhidden) content for one or more dot-separated keys.

        Marks all read keys (and their children) as seen.
        """
        result = {}
        for key in keys:
            value = self._get_by_path(key)
            if value is _MISSING:
                result[key] = {"_error": f"Key not found: {key}"}
            else:
                result[key] = value
                # Mark this key and all children as seen
                self._mark_seen_recursive(key, value)
        return result

    def update_state(self, updates: dict[str, Any]) -> dict[str, str]:
        """Update one or more keys. Creates new keys or edits existing ones.

        Only the keys explicitly provided are touched — all other keys are
        left unchanged.  An empty dict is a no-op (nothing is modified).

        Setting a key to "" or null deletes it.

        For existing keys: must have been seen this turn.
        For new keys: parent must have been seen this turn.

        Args:
            updates: Dict of dot-separated key paths to new values.

        Returns:
            Dict of key -> "ok" / "deleted" / error message.
        """
        if not updates:
            return {"_info": "No updates provided. Pass key-value pairs to update."}

        results = {}
        for key, value in updates.items():
            # "" or None means delete
            is_delete = value is None or value == ""

            existing = self._get_by_path(key)
            is_new = existing is _MISSING

            if is_delete:
                if is_new:
                    results[key] = f"Error: key '{key}' not found, nothing to delete."
                    continue
                if not self._is_seen(key):
                    results[key] = f"Error: key '{key}' has not been seen this turn. Read it first."
                    continue
                self._delete_by_path(key)
                results[key] = "deleted"
                continue

            if is_new:
                # New key — check parent is seen
                parent_key = self._parent_path(key)
                if parent_key and not self._is_seen(parent_key):
                    results[key] = f"Error: parent key '{parent_key}' has not been seen this turn. Read it first."
                    continue
            else:
                # Existing key — must have been seen
                if not self._is_seen(key):
                    results[key] = f"Error: key '{key}' has not been seen this turn. Read it first."
                    continue

            self._set_by_path(key, value)
            self._mark_seen_recursive(key, value)
            results[key] = "ok"

        self._save()
        return results

    def move_state(self, source: str, destination: str) -> str:
        """Move a key to a new location. Source only needs to exist (not be read).

        Returns "ok" or an error message.
        """
        value = self._get_by_path(source)
        if value is _MISSING:
            return f"Error: source key '{source}' not found."

        # Check destination doesn't exist
        if self._get_by_path(destination) is not _MISSING:
            return f"Error: destination key '{destination}' already exists."

        # Check destination parent is seen
        dest_parent = self._parent_path(destination)
        if dest_parent and not self._is_seen(dest_parent):
            return f"Error: destination parent '{dest_parent}' has not been seen this turn."

        self._set_by_path(destination, value)
        self._delete_by_path(source)
        self._save()
        return "ok"

    def set_hide(self, key: str, hide: bool) -> str:
        """Set the _hide flag on a key.

        Returns "ok" or an error message.
        """
        node = self._get_by_path(key)
        if node is _MISSING:
            return f"Error: key '{key}' not found."

        if not isinstance(node, dict):
            return f"Error: _hide can only be set on dict keys, not on '{key}' (type: {type(node).__name__})"

        node["_hide"] = hide
        self._save()
        return "ok"

    # --- Internal: path navigation ---

    def _get_by_path(self, path: str) -> Any:
        """Get a value by dot-separated path. Returns _MISSING if not found."""
        if not path:
            return self._data

        keys = path.split(".")
        node = self._data
        for k in keys:
            if not isinstance(node, dict) or k not in node:
                return _MISSING
            node = node[k]
        return node

    def _set_by_path(self, path: str, value: Any) -> None:
        """Set a value by dot-separated path. Creates intermediate dicts."""
        keys = path.split(".")
        node = self._data
        for k in keys[:-1]:
            if k not in node or not isinstance(node[k], dict):
                node[k] = {}
            node = node[k]
        node[keys[-1]] = value

    def _delete_by_path(self, path: str) -> None:
        """Delete a key by dot-separated path."""
        keys = path.split(".")
        node = self._data
        for k in keys[:-1]:
            if not isinstance(node, dict) or k not in node:
                return
            node = node[k]
        if isinstance(node, dict) and keys[-1] in node:
            del node[keys[-1]]

    def _parent_path(self, path: str) -> Optional[str]:
        """Get the parent path of a dot-separated path."""
        parts = path.split(".")
        if len(parts) <= 1:
            return ""  # Top-level key, parent is root (always seen)
        return ".".join(parts[:-1])

    # --- Internal: visibility and _hide ---

    def _truncate(self, data: Any, hidden: bool = False) -> Any:
        """Recursively apply _hide to produce the truncated view."""
        if hidden:
            return "<hidden>"

        if isinstance(data, dict):
            is_hidden = data.get("_hide", False)
            if is_hidden:
                return "<hidden>"
            result = {}
            for k, v in data.items():
                if k == "_hide":
                    continue
                result[k] = self._truncate(v, hidden=False)
            return result

        return data

    def _mark_visible_as_seen(self, data: Any, prefix: str) -> None:
        """Walk the state tree and mark visible (non-hidden) leaf paths as seen."""
        if not isinstance(data, dict):
            if prefix:
                self._seen_keys.add(prefix)
            return

        is_hidden = data.get("_hide", False)
        if is_hidden:
            # Key name is visible but content is not
            if prefix:
                # The key itself is known to exist, but children are not seen
                pass
            return

        # Mark this dict key as seen
        if prefix:
            self._seen_keys.add(prefix)

        for k, v in data.items():
            if k == "_hide":
                continue
            child_path = f"{prefix}.{k}" if prefix else k
            self._mark_visible_as_seen(v, child_path)

    def _mark_seen_recursive(self, path: str, value: Any) -> None:
        """Mark a key and all its children as seen."""
        self._seen_keys.add(path)
        if isinstance(value, dict):
            for k, v in value.items():
                if k == "_hide":
                    continue
                child_path = f"{path}.{k}"
                self._mark_seen_recursive(child_path, v)

    def _is_seen(self, key: str) -> bool:
        """Check if a key has been seen this turn."""
        if not key:
            return True  # Root is always seen
        return key in self._seen_keys

    # --- Internal: persistence ---

    def _save(self) -> None:
        """Write state to disk."""
        with open(self.state_file, "w") as f:
            json.dump(self._data, f, indent=2)


class _MissingSentinel:
    """Sentinel for missing keys (distinct from None)."""
    def __repr__(self):
        return "<MISSING>"

_MISSING = _MissingSentinel()
