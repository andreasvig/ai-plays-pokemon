"""Continue-from-history seamlessness — TurnManager.restore_player_history.

A continued run restores the emulator state + TaskMaster task tree from the
savepoint, but the Player's transient context (turn_explanations, the global
turn counter, the historic-image ring buffer, and the in-progress task's
evidence) lived only in memory and used to restart empty — the resumed agent's
first turn rendered "(none — this is the first turn.)" and forgot everything.

restore_player_history rebuilds that context from the copied events.jsonl so
continuing is indistinguishable from never having stopped (Andreas 2026-06-18).
These tests drive the pure reconstruction directly — no emulator / network /
TurnManager construction (the method only reads attrs + files), so the agent
factory is bypassed via __new__.
"""

import json
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agent.turn import TurnManager


def _blank_manager(*, historic_images_count=0, task_master_enabled=False,
                   current_task_index=0):
    """A TurnManager with only the fields restore_player_history touches set."""
    mgr = TurnManager.__new__(TurnManager)
    mgr.turn_explanations = []
    mgr._explanation_turns = []
    mgr.turn_screenshots = []
    mgr.turn_number = 0
    mgr.historic_images_count = historic_images_count
    mgr.max_turns_before_trim = None
    mgr.task_master_enabled = task_master_enabled
    mgr.current_task_index = current_task_index
    mgr._cur_task_player_reasons = []
    mgr._cur_task_first_image = None
    mgr._cur_task_last_image = None
    return mgr


def _write_png(path: Path):
    Image.new("RGB", (4, 4), (10, 20, 30)).save(path)


def _build_run(tmp_path, turns, *, tasks=None):
    """Write a synthetic events.jsonl + screenshot PNGs for `turns`.

    `turns`: list of (turn_number, has_explanation, reasoning). A turn always
    logs turn_start + screenshot; turn_explanation only when has_explanation
    (handoff turns produce none — mirroring the real loop).
    `tasks`: optional list of (task_index, global_turn) task_started events.
    Returns (events_path, screenshots_dir).
    """
    screenshots = tmp_path / "screenshots"
    screenshots.mkdir()
    events = []
    if tasks:
        for ti, gt in tasks:
            events.append({"type": "task_started", "task_index": ti,
                           "global_turn": gt, "title": f"task {ti}"})
    for i, (t, has_exp, reasoning) in enumerate(turns):
        events.append({"type": "turn_start", "turn": t})
        fname = f"{i + 1:05d}_turn_{t}.png"
        _write_png(screenshots / fname)
        events.append({"type": "screenshot",
                       "file": str(screenshots / fname),
                       "label": f"turn_{t}", "screenshot_id": i + 1})
        if has_exp:
            events.append({"type": "turn_explanation", "turn": t,
                           "explanation": {"action": ["A"], "reasoning": reasoning,
                                           "last_turn_succeeded": True}})
    events_path = tmp_path / "events.jsonl"
    events_path.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    return events_path, screenshots


def test_rebuilds_history_counter_and_image_buffer(tmp_path):
    turns = [(t, True, f"reason {t}") for t in range(1, 6)]
    events_path, shots = _build_run(tmp_path, turns)
    mgr = _blank_manager(historic_images_count=2)

    mgr.restore_player_history(events_path, shots, up_to_turn=5)

    # Full per-turn history rebuilt in order.
    assert len(mgr.turn_explanations) == 5
    assert [e["reasoning"] for e in mgr.turn_explanations] == [f"reason {t}" for t in range(1, 6)]
    # Counter continues from the savepoint turn → next turn is 6.
    assert mgr.turn_number == 5
    # Ring buffer holds the last K=2 start-of-turn screenshots, as PIL images.
    assert [t for t, _ in mgr.turn_screenshots] == [4, 5]
    assert all(isinstance(img, Image.Image) for _, img in mgr.turn_screenshots)


def test_up_to_turn_caps_at_savepoint(tmp_path):
    # Source ran to turn 7, but the latest savepoint is turn 5 (e.g. periodic
    # cadence) — turns 6/7 happened after the savepoint and must NOT be restored,
    # or the agent's memory would be ahead of the emulator state.
    turns = [(t, True, f"reason {t}") for t in range(1, 8)]
    events_path, shots = _build_run(tmp_path, turns)
    mgr = _blank_manager(historic_images_count=3)

    mgr.restore_player_history(events_path, shots, up_to_turn=5)

    assert len(mgr.turn_explanations) == 5
    assert mgr.turn_number == 5
    assert [t for t, _ in mgr.turn_screenshots] == [3, 4, 5]


def test_handoff_turns_have_no_explanation_but_count_in_numbering(tmp_path):
    # Turns 1,2 (task 1), turn 3 is a handoff (turn_start + screenshot, NO
    # explanation), task 2 starts at turn 4, turns 4,5 run task 2.
    turns = [
        (1, True, "r1"), (2, True, "r2"),
        (3, False, None),          # handoff turn — no turn_explanation
        (4, True, "r4"), (5, True, "r5"),
    ]
    events_path, shots = _build_run(
        tmp_path, turns, tasks=[(1, 1), (2, 4)],
    )
    mgr = _blank_manager(historic_images_count=2, task_master_enabled=True,
                         current_task_index=2)

    mgr.restore_player_history(events_path, shots, up_to_turn=5)

    # 4 explanations (turn 3 produced none); counter still tracks the global 5.
    assert len(mgr.turn_explanations) == 4
    assert mgr.turn_number == 5
    # In-progress task (index 2, started turn 4) evidence = turns 4 & 5 only.
    assert mgr._cur_task_player_reasons == ["r4", "r5"]
    assert mgr._cur_task_first_image.endswith("_turn_4.png")
    assert mgr._cur_task_last_image.endswith("_turn_5.png")
    # Parallel real-turn list skips the handoff turn (3): explanations map to
    # turns 1,2,4,5 — NOT positional 1,2,3,4.
    assert mgr._explanation_turns == [1, 2, 4, 5]


def test_previous_turns_text_uses_real_turn_numbers_across_handoff_gap(tmp_path):
    # Reproduces the resume bug: turn 16 was a handoff (no explanation), so
    # positional numbering drifts by 1 after it. The rendered "## Previous Turns"
    # headings must show the REAL turn numbers (…28, 29), not positional (…27, 28).
    turns = []
    for t in range(1, 30):  # turns 1..29
        if t == 16:
            turns.append((t, False, None))   # handoff — no explanation
        else:
            turns.append((t, True, f"reason {t}"))
    events_path, shots = _build_run(tmp_path, turns)
    mgr = _blank_manager(historic_images_count=1, task_master_enabled=True,
                         current_task_index=1)

    mgr.restore_player_history(events_path, shots, up_to_turn=29)
    text = mgr._render_previous_turns_text()

    # The latest player turn is 29 (turn 30 doesn't exist here) — it must appear,
    # and the off-by-one ghost "Turn 28-as-latest" must NOT be the last heading.
    assert "### Turn 29" in text
    assert "### Turn 28" in text
    # Turn 16 (handoff) has no entry and must be absent.
    assert "### Turn 16" not in text
    # Headings are strictly the real, gap-aware turn numbers.
    import re
    headings = [int(m) for m in re.findall(r"### Turn (\d+)", text)]
    assert headings == sorted(headings)
    assert 16 not in headings
    assert headings[-1] == 29


def test_missing_events_file_is_a_safe_noop(tmp_path):
    mgr = _blank_manager(historic_images_count=2)
    mgr.restore_player_history(tmp_path / "nope.jsonl", tmp_path, up_to_turn=5)
    # Degrades to the old fresh-start behaviour rather than raising.
    assert mgr.turn_explanations == []
    assert mgr.turn_number == 0
    assert mgr.turn_screenshots == []


def test_screenshot_paths_remap_to_new_run_dir(tmp_path):
    # Logged paths point at the SOURCE run; the files were copied into the new
    # run's screenshots/. Restore must resolve to the copies so the continued run
    # is self-contained (doesn't depend on the source surviving).
    turns = [(1, True, "r1"), (2, True, "r2")]
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _, source_shots = _build_run(source_dir, turns)
    # Copy screenshots into a fresh "new run" dir, leave events pointing at source.
    new_shots = tmp_path / "new" / "screenshots"
    new_shots.mkdir(parents=True)
    for f in source_shots.iterdir():
        (new_shots / f.name).write_bytes(f.read_bytes())
    events_path = (tmp_path / "source") / "events.jsonl"

    mgr = _blank_manager(historic_images_count=2, task_master_enabled=True,
                         current_task_index=1)
    mgr.restore_player_history(events_path, new_shots, up_to_turn=2)

    # Resolved refs live under the NEW run dir.
    assert str(new_shots) in mgr._cur_task_first_image
    assert str(new_shots) in mgr._cur_task_last_image
