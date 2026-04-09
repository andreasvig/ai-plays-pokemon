"""Phase 3 evaluation: Run Logger + State Manager tests."""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_state_manager():
    """Test the StateManager with all tools and visibility rules."""
    from src.core import StateManager

    # Use a temp file
    tmp = tempfile.mktemp(suffix=".json")
    sm = StateManager(tmp)

    print("=== State Manager Tests ===\n")

    # --- Test update_state (add new keys) ---
    print("1. update_state (add top-level keys)...")
    sm.start_turn()
    result = sm.update_state({"current_location": "Route 1", "party": {}})
    assert result["current_location"] == "ok"
    assert result["party"] == "ok"
    print("   OK")

    # --- Test add nested key under seen parent ---
    print("2. update_state under seen parent...")
    sm.start_turn()  # party is visible (not hidden), so it's seen
    result = sm.update_state({"party.charmander": {"hp": 20, "moves": ["Scratch", "Ember"]}})
    assert result["party.charmander"] == "ok", f"Expected ok, got: {result}"
    print("   OK")

    # --- Test update_state on existing visible key ---
    print("3. update_state on visible key...")
    sm.start_turn()
    result = sm.update_state({"current_location": "Viridian City"})
    assert result["current_location"] == "ok"
    print("   OK")

    # --- Test bulk update ---
    print("4. update_state bulk (multiple keys)...")
    sm.start_turn()
    result = sm.update_state({
        "current_location": "Pewter City",
        "party.charmander.hp": 15,
    })
    assert result["current_location"] == "ok"
    assert result["party.charmander.hp"] == "ok"
    print("   OK")

    # --- Test set_hide ---
    print("5. set_hide...")
    sm.start_turn()
    result = sm.set_hide("party.charmander", True)
    assert result == "ok"
    print("   OK")

    # --- Test truncated view with hidden key ---
    print("6. get_truncated_view with hidden key...")
    sm.start_turn()
    view = sm.get_truncated_view()
    assert view["party"]["charmander"] == "<hidden>"
    assert view["current_location"] == "Pewter City"
    print(f"   View: {json.dumps(view, indent=2)}")
    print("   OK")

    # --- Test update on hidden key FAILS ---
    print("7. update_state on hidden key fails...")
    sm.start_turn()
    result = sm.update_state({"party.charmander.hp": 10})
    assert "Error" in result["party.charmander.hp"]
    print(f"   Got expected error: {result['party.charmander.hp']}")
    print("   OK")

    # --- Test read_state reveals hidden key ---
    print("8. read_state reveals hidden key, then update works...")
    sm.start_turn()
    read_result = sm.read_state(["party.charmander"])
    assert read_result["party.charmander"]["hp"] == 15
    # Now update should work
    result = sm.update_state({"party.charmander.hp": 10})
    assert result["party.charmander.hp"] == "ok"
    print("   OK")

    # --- Test delete via update (set to "") on hidden key FAILS ---
    print("9. update_state delete on hidden key fails...")
    sm.set_hide("party.charmander", True)
    sm.start_turn()
    result = sm.update_state({"party.charmander": ""})
    assert "Error" in result["party.charmander"]
    print(f"   Got expected error: {result['party.charmander']}")
    print("   OK")

    # --- Test move_state (doesn't require reading source content) ---
    print("10. move_state without reading source...")
    sm.start_turn()
    # Add a box key first
    sm.update_state({"box": {}})
    # charmander is hidden but move only needs it to exist
    result = sm.move_state("party.charmander", "box.charmander")
    assert result == "ok"
    # Verify it moved
    view = sm.get_truncated_view()
    assert "charmander" not in view.get("party", {})
    assert "charmander" in view.get("box", {})
    print("   OK")

    # --- Test update under unseen parent FAILS ---
    print("11. update_state under hidden parent fails...")
    sm.set_hide("box", True)
    sm.start_turn()
    result = sm.update_state({"box.pidgey": {"hp": 30}})
    assert "Error" in result["box.pidgey"]
    print(f"   Got expected error: {result['box.pidgey']}")
    print("   OK")

    # --- Test seen tracking resets between turns ---
    print("12. seen tracking resets between turns...")
    sm.start_turn()
    sm.read_state(["box.charmander"])  # Now it's seen
    sm.start_turn()  # Reset! box is hidden again
    result = sm.update_state({"box.charmander.hp": 5})
    assert "Error" in result["box.charmander.hp"]
    print("   OK")

    # --- Test update_state on existing key overwrites (no add error) ---
    print("13. update_state on existing key overwrites...")
    sm.start_turn()
    result = sm.update_state({"current_location": "Cerulean City"})
    assert result["current_location"] == "ok"
    print("   OK")

    # --- Test empty update is a no-op ---
    print("14. update_state with empty dict is a no-op...")
    sm.start_turn()
    result = sm.update_state({})
    assert "_info" in result
    print(f"   Got expected info: {result['_info']}")
    print("   OK")

    # --- Test delete via "" ---
    print("15. update_state with '' deletes the key...")
    sm.start_turn()
    result = sm.update_state({"current_location": ""})
    assert result["current_location"] == "deleted"
    view = sm.get_truncated_view()
    assert "current_location" not in view
    print("   OK")

    # --- Test delete via None ---
    print("16. update_state with null deletes the key...")
    sm.start_turn()
    # party should still exist
    assert "party" in sm.get_truncated_view()
    result = sm.update_state({"party": None})
    assert result["party"] == "deleted"
    view = sm.get_truncated_view()
    assert "party" not in view
    print("   OK")

    # --- Test delete nonexistent key fails ---
    print("17. update_state delete nonexistent key fails...")
    sm.start_turn()
    result = sm.update_state({"nonexistent": ""})
    assert "Error" in result["nonexistent"]
    print(f"   Got expected error: {result['nonexistent']}")
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
    logger.log_button_press("A")
    logger.log_button_sequence("RRRRAAA")
    logger.log_tool_call("read_state", {"keys": ["party"]}, agent_id="agent_0")
    logger.log_tool_response("read_state", {"party": {}}, agent_id="agent_0")
    logger.log_turn_start(1, agent_id="agent_0")
    logger.log_turn_explanation(1, {
        "i_saw": "Player standing in bedroom",
        "i_thought": "Should go outside",
        "i_did": "Walked down to the door",
    }, agent_id="agent_0")
    logger.log_state_change("edit", {"key": "location", "value": "Route 1"})
    logger.log_task_event("spawn", {"task": "Navigate to Viridian City", "depth": 1})
    logger.log_ocr("Welcome to the world of Pokemon!")
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
    # run_start + 11 events (screenshot logs twice: file + event) + run_end = 13
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
    logger2.log_button_press("B")
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
