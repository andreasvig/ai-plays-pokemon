"""Resume re-run turn de-duplication in the trace projection.

When a run is killed mid-turn and resumed, the original session leaves a bare
``turn_start`` for the in-flight turn (the instant-kill abandons it before any
output is logged) and the resumed session re-runs and fully logs the SAME turn
number. ``group_events_by_turn`` must collapse the aborted fragment so the trace
shows ONE clean turn — otherwise the SPA's turn-keyed list gets a duplicate key
(Svelte ``each_key_duplicate`` → that whole block fails to render, so the user
"can no longer see the full trace") and the resume reads as broken.

These pin:
  1. aborted fragment (bare turn_start) + re-run of the same turn → one turn,
     carrying the re-run's content.
  2. a normal monotonic sequence is untouched (no false collapsing).
  3. two genuinely-settled turns sharing a number are BOTH kept (the supersede
     only drops an UNSETTLED fragment).
"""

from src.core.event_parsing import group_events_by_turn


def _settled(turn):
    """Events that make a turn 'settle' (real output)."""
    return [
        {"type": "turn_user_message", "turn": turn, "message": f"msg {turn}"},
        {"type": "turn_trace", "turn": turn, "messages": [{"role": "x"}], "model_used": "m"},
        {"type": "turn_usage", "turn": turn, "total_tokens": 10},
    ]


def test_aborted_fragment_superseded_by_rerun():
    events = (
        [{"type": "run_start", "run_dir": "orig"}]
        + [{"type": "turn_start", "turn": 5}]
        + _settled(5)
        # turn 6 killed mid-flight: only a turn_start, no output.
        + [{"type": "turn_start", "turn": 6}]
        + [{"type": "run_start", "run_dir": "continued"}]
        # resume re-runs turn 6 fully.
        + [{"type": "turn_start", "turn": 6}]
        + _settled(6)
        + [{"type": "turn_start", "turn": 7}]
        + _settled(7)
    )

    turns = group_events_by_turn(events)
    nums = [t["turn"] for t in turns]
    assert nums == [5, 6, 7], f"expected one clean turn 6, got {nums}"
    # The surviving turn 6 is the re-run (it has a trace), not the empty fragment.
    t6 = next(t for t in turns if t["turn"] == 6)
    assert t6.get("trace") is not None


def test_normal_sequence_untouched():
    events = []
    for n in (1, 2, 3):
        events.append({"type": "turn_start", "turn": n})
        events += _settled(n)
    turns = group_events_by_turn(events)
    assert [t["turn"] for t in turns] == [1, 2, 3]


def test_two_settled_same_number_both_kept():
    # Defensive: only an UNSETTLED prior copy is dropped. If both settled
    # (shouldn't happen with instant-kill, but don't silently swallow a real
    # turn), keep both — the frontend's unique keys still render them.
    events = (
        [{"type": "turn_start", "turn": 4}]
        + _settled(4)
        + [{"type": "turn_start", "turn": 4}]
        + _settled(4)
    )
    turns = group_events_by_turn(events)
    assert [t["turn"] for t in turns] == [4, 4]
