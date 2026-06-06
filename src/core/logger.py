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

        # Event listeners (for live dashboard)
        self._listeners: list[Callable[[dict], None]] = []

        # Save config snapshot
        config_path = self.run_dir / "config.json"
        with open(config_path, "w") as f:
            json.dump(config, f, indent=2, default=str)

        self._log_event("run_start", {"run_dir": str(self.run_dir)})

    def log_screenshot(self, image, label: str = "") -> str:
        """Save a screenshot image and log it. Returns path to saved file."""
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

    def log_event(self, event_type: str, data: dict) -> None:
        """Log any event by type and data dict."""
        self._log_event(event_type, data)

    # Convenience aliases for frequently-used event types
    log_custom = log_event

    def log_tool_call(self, tool_name: str, args: dict, agent_id: str = "") -> None:
        self._log_event("tool_call", {"tool": tool_name, "args": args, "agent_id": agent_id})

    def log_tool_response(self, tool_name: str, response: Any, agent_id: str = "") -> None:
        self._log_event("tool_response", {"tool": tool_name, "response": _safe_serialize(response), "agent_id": agent_id})

    def log_turn_start(
        self,
        turn_number: int,
        agent_id: str = "",
        task_index: Optional[int] = None,
    ) -> None:
        """Log the start of a player turn.

        ``task_index`` is the only hard new field on player turns under the
        TaskMaster contract (see local/plan-frontend-display.md "Event
        contract"). It is the index of the task this turn runs under so the
        frontend can bucket the turn into its task group. When TaskMaster is
        disabled it is None and omitted entirely, so the legacy turn_start shape
        is byte-for-byte unchanged.
        """
        data: dict[str, Any] = {"turn": turn_number, "agent_id": agent_id}
        if task_index is not None:
            data["task_index"] = task_index
        self._log_event("turn_start", data)

    # --- TaskMaster task-lifecycle events (gated on task_master.enabled) -------
    # Shapes are locked by local/plan-frontend-display.md "Event contract"; the
    # report (src/cli/report.py) + dashboard already consume these exact keys.

    def log_task_started(
        self,
        task_index: int,
        title: str,
        description: str,
        success_criteria: str,
        global_turn: int,
    ) -> None:
        """Emit when a master call sets a new task (incl. the cold-start task 1)."""
        self._log_event("task_started", {
            "task_index": task_index,
            "title": title,
            "description": description,
            "success_criteria": success_criteria,
            "global_turn": global_turn,
        })

    def log_task_completed(
        self,
        task_index: int,
        rating: dict,
        player_self_assessment: Optional[str] = None,
        player_task_summary: Optional[str] = None,
    ) -> None:
        """Emit when a master call rates the just-finished task (#2 onward).

        ``rating`` is ``{status, reasoning}`` — it stamps backward onto the
        rated task_index in both frontend surfaces (Decision 2).
        ``player_self_assessment`` / ``player_task_summary`` are the Player's own
        hand-back from its final turn on the task (the message it returned to the
        TaskMaster); both None on a budget-forced handoff with no Player output.
        """
        self._log_event("task_completed", {
            "task_index": task_index,
            "rating": rating,
            "player_self_assessment": player_self_assessment,
            "player_task_summary": player_task_summary,
        })

    def log_task_master_trace(
        self,
        task_index: int,
        messages: list[dict],
        model_used: str,
        cost_usd: float,
        search_cost_usd: float = 0.0,
        input_images: Optional[list[dict]] = None,
    ) -> None:
        """Emit the TaskMaster agent's own trace (analogous to turn_trace).

        ``messages`` is the same role-shape as turn_trace's messages
        (system/user/thinking/tool_call/tool_result/final_result). ``cost_usd``
        is the agent's own LLM cost; ``search_cost_usd`` is the dollar cost of
        any ``ask_perplexity`` calls it made this invocation (0.0 if none).
        ``input_images`` are the actual screenshots THIS invocation saw, each
        ``{"label", "data_url"}`` — one (the start screen) on cold-start, two
        (the previous task's start + end) on a handoff. Rendered under the
        master's Input section so the trace shows what the agent actually saw.
        """
        self._log_event("task_master_trace", {
            "task_index": task_index,
            "messages": messages,
            "model_used": model_used,
            "cost_usd": cost_usd,
            "search_cost_usd": search_cost_usd,
            "input_images": input_images or [],
        })

    def log_turn_explanation(self, turn_number: int, explanation: dict, agent_id: str = "") -> None:
        self._log_event("turn_explanation", {"turn": turn_number, "explanation": explanation, "agent_id": agent_id})

    def log_button_sequence(self, sequence: str) -> None:
        self._log_event("button_sequence", {"sequence": sequence})

    def log_state_change(self, operation: str, details: dict, agent_id: str = "") -> None:
        self._log_event("state_change", {"operation": operation, "details": _safe_serialize(details), "agent_id": agent_id})

    def add_listener(self, callback: Callable[[dict], None]) -> None:
        """Register a callback that receives every event dict after it's logged."""
        self._listeners.append(callback)

    def seed_screenshot_id(self, start: Optional[int] = None) -> None:
        """Seed the screenshot id counter so new screenshots don't collide.

        If `start` is None, scan screenshots_dir for the highest existing
        NNNNN_*.png index and set the counter to that. Used by --continue
        so the copied prior screenshots keep their IDs and new ones extend
        the sequence.
        """
        if start is not None:
            self._screenshot_id = start
            return
        max_id = 0
        if self.screenshots_dir.exists():
            for entry in self.screenshots_dir.iterdir():
                m = entry.name.split("_", 1)[0]
                if m.isdigit():
                    max_id = max(max_id, int(m))
        self._screenshot_id = max_id

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
