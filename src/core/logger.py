"""Run logging system. Writes all events incrementally to a run folder."""

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any, Callable, Optional


class RunLogger:
    """Logs all events for a run to a dedicated folder.

    All writes flush immediately so logs survive crashes.
    Events are written as JSON lines to events.jsonl.
    Screenshots are saved as individual image files.
    """

    def __init__(self, config: dict[str, Any]):
        runs_dir = Path(config.get("runs_directory", "runs"))
        run_name = config.get("run_name") or ""

        # Create run folder with timestamp
        timestamp = time.strftime("%Y-%m-%d_%H-%M-%S")
        if run_name:
            folder_name = f"{timestamp}_{run_name}"
        else:
            folder_name = timestamp

        self.run_dir = runs_dir / folder_name
        self.run_dir.mkdir(parents=True, exist_ok=True)

        self.screenshots_dir = self.run_dir / "screenshots"
        self.screenshots_dir.mkdir(exist_ok=True)

        self.ocr_dir = self.run_dir / "ocr"
        self.ocr_dir.mkdir(exist_ok=True)

        # Event log file
        self._events_path = self.run_dir / "events.jsonl"
        self._events_file = open(self._events_path, "a")

        # Sequential counters
        self._event_id = 0
        self._screenshot_id = 0

        # Event listeners (for live dashboard, etc.)
        self._listeners: list[Callable[[dict], None]] = []

        # Save config snapshot
        config_path = self.run_dir / "config.json"
        with open(config_path, "w") as f:
            json.dump(config, f, indent=2, default=str)

        self._log_event("run_start", {"run_dir": str(self.run_dir)})

    def log_screenshot(self, image, label: str = "") -> str:
        """Save a screenshot image and log it.

        Args:
            image: PIL Image
            label: Optional label (e.g., "turn_start", "after_action")

        Returns:
            Path to the saved screenshot file
        """
        self._screenshot_id += 1
        filename = f"{self._screenshot_id:05d}"
        if label:
            filename += f"_{label}"
        filename += ".png"

        filepath = self.screenshots_dir / filename
        image.save(filepath)

        self._log_event("screenshot", {
            "file": str(filepath),
            "label": label,
            "screenshot_id": self._screenshot_id,
        })

        return str(filepath)

    def log_button_press(self, button: str) -> None:
        """Log a single button press."""
        self._log_event("button_press", {"button": button})

    def log_button_sequence(self, sequence: str) -> None:
        """Log a button sequence."""
        self._log_event("button_sequence", {"sequence": sequence})

    def log_tool_call(self, tool_name: str, args: dict, agent_id: str = "") -> None:
        """Log an agent tool call."""
        self._log_event("tool_call", {
            "tool": tool_name,
            "args": args,
            "agent_id": agent_id,
        })

    def log_tool_response(self, tool_name: str, response: Any, agent_id: str = "") -> None:
        """Log a tool response."""
        self._log_event("tool_response", {
            "tool": tool_name,
            "response": _safe_serialize(response),
            "agent_id": agent_id,
        })

    def log_llm_request(self, model: str, messages: list, agent_id: str = "") -> None:
        """Log an LLM API request."""
        self._log_event("llm_request", {
            "model": model,
            "messages": _safe_serialize(messages),
            "agent_id": agent_id,
        })

    def log_llm_response(self, model: str, response: Any, agent_id: str = "") -> None:
        """Log an LLM API response."""
        self._log_event("llm_response", {
            "model": model,
            "response": _safe_serialize(response),
            "agent_id": agent_id,
        })

    def log_turn_start(self, turn_number: int, agent_id: str = "") -> None:
        """Log the start of a new turn."""
        self._log_event("turn_start", {
            "turn": turn_number,
            "agent_id": agent_id,
        })

    def log_turn_explanation(self, turn_number: int, explanation: dict, agent_id: str = "") -> None:
        """Log a turn explanation (I saw / I thought / I did)."""
        self._log_event("turn_explanation", {
            "turn": turn_number,
            "explanation": explanation,
            "agent_id": agent_id,
        })

    def log_task_event(self, event_type: str, details: dict) -> None:
        """Log a task system event (spawn, complete, fail, etc.)."""
        self._log_event(f"task_{event_type}", details)

    def log_state_change(self, operation: str, details: dict, agent_id: str = "") -> None:
        """Log a state file change."""
        self._log_event("state_change", {
            "operation": operation,
            "details": _safe_serialize(details),
            "agent_id": agent_id,
        })

    def log_ocr(self, text: str) -> None:
        """Log OCR captured text."""
        self._log_event("ocr", {"text": text})

    def log_vlm_request(self, prompt: str, agent_id: str = "") -> None:
        """Log a VLM request."""
        self._log_event("vlm_request", {"prompt": prompt, "agent_id": agent_id})

    def log_vlm_response(self, response: str, agent_id: str = "") -> None:
        """Log a VLM response."""
        self._log_event("vlm_response", {"response": response, "agent_id": agent_id})

    def log_snapshot(self, snapshot_path: str, trigger: str = "") -> None:
        """Log a snapshot event."""
        self._log_event("snapshot", {"path": snapshot_path, "trigger": trigger})

    def log_custom(self, event_type: str, data: dict) -> None:
        """Log a custom event."""
        self._log_event(event_type, data)

    def add_listener(self, callback: Callable[[dict], None]) -> None:
        """Register a callback that receives every event dict after it's logged."""
        self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[dict], None]) -> None:
        """Unregister an event listener."""
        self._listeners.remove(callback)

    def close(self) -> None:
        """Close the log file."""
        self._log_event("run_end", {})
        self._events_file.close()

    # --- Internal ---

    def _log_event(self, event_type: str, data: dict) -> None:
        """Write a single event to the log file."""
        self._event_id += 1
        event = {
            "id": self._event_id,
            "type": event_type,
            "timestamp": time.time(),
            "time": time.strftime("%H:%M:%S"),
            **data,
        }
        line = json.dumps(event, default=str) + "\n"
        self._events_file.write(line)
        self._events_file.flush()

        for listener in self._listeners:
            try:
                listener(event)
            except Exception:
                pass  # Never let a listener crash logging


def _safe_serialize(obj: Any) -> Any:
    """Convert an object to something JSON-safe."""
    if isinstance(obj, (str, int, float, bool, type(None))):
        return obj
    if isinstance(obj, dict):
        return {k: _safe_serialize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_safe_serialize(v) for v in obj]
    return str(obj)
