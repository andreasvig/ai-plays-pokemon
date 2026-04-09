"""Phase 3 evaluation: Run Logger + State Manager tests."""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_state_manager():
    """Test the StateManager with get/set/delete operations."""
    from src.core import StateManager

    tmp = tempfile.mktemp(suffix=".json")
    sm = StateManager(tmp)

    print("=== State Manager Tests ===\n")

    # --- Test set_by_path (add new keys) ---
    print("1. set_by_path (add top-level keys)...")
    sm.set_by_path("current_location", "Route 1")
    sm.set_by_path("party", {})
    sm.save()
    assert sm.get_by_path("current_location") == "Route 1"
    assert sm.get_by_path("party") == {}
    print("   OK")

    # --- Test set nested key ---
    print("2. set_by_path nested key...")
    sm.set_by_path("party.charmander", {"hp": 20, "moves": ["Scratch", "Ember"]})
    sm.save()
    assert sm.get_by_path("party.charmander")["hp"] == 20
    print("   OK")

    # --- Test update existing key ---
    print("3. set_by_path overwrites existing...")
    sm.set_by_path("current_location", "Viridian City")
    sm.save()
    assert sm.get_by_path("current_location") == "Viridian City"
    print("   OK")

    # --- Test deep nested update ---
    print("4. set_by_path deep nested...")
    sm.set_by_path("party.charmander.hp", 15)
    sm.save()
    assert sm.get_by_path("party.charmander.hp") == 15
    print("   OK")

    # --- Test get_truncated_view ---
    print("5. get_truncated_view returns full state...")
    view = sm.get_truncated_view()
    assert view["current_location"] == "Viridian City"
    assert view["party"]["charmander"]["hp"] == 15
    print(f"   View: {json.dumps(view, indent=2)}")
    print("   OK")

    # --- Test get_by_path missing key ---
    print("6. get_by_path returns None for missing keys...")
    assert sm.get_by_path("nonexistent") is None
    assert sm.get_by_path("party.pikachu") is None
    print("   OK")

    # --- Test delete_by_path ---
    print("7. delete_by_path removes key...")
    sm.delete_by_path("current_location")
    sm.save()
    assert sm.get_by_path("current_location") is None
    assert "current_location" not in sm.get_truncated_view()
    print("   OK")

    # --- Test delete nested ---
    print("8. delete_by_path nested...")
    sm.delete_by_path("party.charmander")
    sm.save()
    assert sm.get_by_path("party.charmander") is None
    assert "charmander" not in sm.get_truncated_view()["party"]
    print("   OK")

    # --- Test delete nonexistent is no-op ---
    print("9. delete_by_path nonexistent is no-op...")
    sm.delete_by_path("nonexistent")  # Should not raise
    print("   OK")

    # --- Test creates intermediate dicts ---
    print("10. set_by_path creates intermediate dicts...")
    sm.set_by_path("map.pallet_town.exits", ["north"])
    sm.save()
    assert sm.get_by_path("map.pallet_town.exits") == ["north"]
    print("   OK")

    # --- Test persistence ---
    print("11. State persists to disk...")
    sm2 = StateManager(tmp)
    assert sm2.get_by_path("map.pallet_town.exits") == ["north"]
    print("   OK")

    # Cleanup
    os.unlink(tmp)
    print("\n=== All State Manager tests passed! ===\n")


def test_run_logger():
    """Test the RunLogger."""
    from src.core import RunLogger
    from PIL import Image

    print("=== Run Logger Tests ===\n")

    config = {
        "runs_directory": tempfile.mkdtemp(),
        "run_name": "test_run",
    }

    logger = RunLogger(config)
    print(f"1. Run folder created: {logger.run_dir}")
    assert logger.run_dir.exists()
    assert (logger.run_dir / "config.json").exists()
    print("   OK")

    # Log various events
    print("2. Logging events...")
    logger.log_event("button_press", {"button": "A"})
    logger.log_button_sequence("RRRRAAA")
    logger.log_tool_call("ask_vlm", {"question": "What do I see?"}, agent_id="agent_0")
    logger.log_tool_response("ask_vlm", "A bedroom", agent_id="agent_0")
    logger.log_turn_start(1, agent_id="agent_0")
    logger.log_turn_explanation(1, {
        "i_saw": "Player standing in bedroom",
        "i_thought": "Should go outside",
        "i_did": "Walked down to the door",
    }, agent_id="agent_0")
    logger.log_state_change("memory_update", {"updates": {"location": "Route 1"}})
    logger.log_event("task_spawn", {"task": "Navigate to Viridian City", "depth": 1})
    logger.log_event("ocr", {"text": "Welcome to the world of Pokemon!"})
    logger.log_custom("test_event", {"data": "hello"})
    print("   OK")

    # Log a screenshot
    print("3. Logging screenshot...")
    img = Image.new("RGB", (240, 160), color=(255, 0, 0))
    path = logger.log_screenshot(img, label="test")
    assert os.path.exists(path)
    print(f"   Saved: {path}")
    print("   OK")

    # Close and verify
    logger.close()

    print("4. Verifying log file...")
    events_file = logger.run_dir / "events.jsonl"
    assert events_file.exists()
    with open(events_file) as f:
        lines = f.readlines()
    # run_start + 10 events + screenshot + run_end = 13
    assert len(lines) == 13, f"Expected 13 events, got {len(lines)}"

    # Verify each line is valid JSON
    for i, line in enumerate(lines):
        event = json.loads(line)
        assert "id" in event
        assert "type" in event
        assert "timestamp" in event
    print(f"   {len(lines)} events logged, all valid JSON")
    print("   OK")

    # Verify crash safety - events should be readable even if we don't close
    print("5. Testing crash safety (no close)...")
    logger2 = RunLogger({
        "runs_directory": config["runs_directory"],
        "run_name": "crash_test",
    })
    logger2.log_event("button_press", {"button": "B"})
    logger2.log_button_sequence("UUUU")
    # Don't call close() - simulate crash
    events_file2 = logger2.run_dir / "events.jsonl"
    with open(events_file2) as f:
        lines2 = f.readlines()
    assert len(lines2) == 3  # run_start + 2 events
    print(f"   {len(lines2)} events survived without close()")
    print("   OK")

    # Cleanup
    shutil.rmtree(config["runs_directory"])
    print("\n=== All Run Logger tests passed! ===\n")


if __name__ == "__main__":
    test_state_manager()
    test_run_logger()
    print("Phase 3: ALL TESTS PASSED")
