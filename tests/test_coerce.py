"""Regression tests for the 2026-06-17 stringified-nested-object coercion.

Some OpenRouter models (observed: ``xiaomi/mimo-v2.5``) call the structured-
output tool correctly but serialize NESTED OBJECT arguments as JSON strings and
emit the literal ``"None"``/``"null"`` for a null optional field. pydantic's
strict validation rejected those (``Input should be an object``) and the run
died at TaskMaster cold-start after exhausting ModelRetry. ``coerce.py`` decodes
them losslessly via ``mode="before"`` field validators on the nested-object
fields. See ``src/agent/coerce.py`` + the diagnosis in
``agent_brain/.../model-registry.md``.

The strings used here are the VERBATIM shapes captured from a live mimo
TaskMaster cold-start on 2026-06-17 (rating as ``"None"``, task as a JSON
string), so this test fails the moment the coercion regresses.
"""

import json

import pytest

from src.agent.agent import GameAction, _LegacyGameAction
from src.agent.coerce import coerce_stringified_object
from src.agent.task_master import Rating, TaskMasterOutput, TaskSpec

_TASK_OBJ = {
    "title": "Walk from Pallet Town north to Viridian City via Route 1",
    "description": "Exit Pallet Town north onto Route 1 and follow the dirt path.",
    "success_criteria": "The screen shows the Viridian City entrance.",
}


def test_coerce_helper_passthrough_and_decode():
    # Non-string input is returned untouched (the correct-model common case).
    obj = {"a": 1}
    assert coerce_stringified_object(obj) is obj
    assert coerce_stringified_object(None) is None
    # Null tokens (any case, whitespace) -> None.
    for tok in ("None", "null", "NULL", " none ", ""):
        assert coerce_stringified_object(tok) is None
    # A stringified JSON object -> the decoded dict.
    assert coerce_stringified_object(json.dumps(_TASK_OBJ)) == _TASK_OBJ
    # Non-JSON garbage is returned unchanged so pydantic raises its own error.
    assert coerce_stringified_object("not json") == "not json"


def test_taskmaster_output_accepts_mimo_stringified_args():
    """The exact shape mimo emitted: rating='None', task=json-string."""
    out = TaskMasterOutput.model_validate(
        {
            "reasoning": "cold start",
            "rating_of_previous_task": "None",
            "task": json.dumps(_TASK_OBJ),
        }
    )
    assert out.rating_of_previous_task is None
    assert isinstance(out.task, TaskSpec)
    assert out.task.title == _TASK_OBJ["title"]


def test_taskmaster_output_still_accepts_correct_nested_objects():
    """Models that encode correctly are unaffected (pure passthrough)."""
    out = TaskMasterOutput.model_validate(
        {
            "reasoning": "later turn",
            "rating_of_previous_task": {"status": "succeeded", "reasoning": "done"},
            "task": _TASK_OBJ,
        }
    )
    assert isinstance(out.rating_of_previous_task, Rating)
    assert out.rating_of_previous_task.status == "succeeded"
    assert out.task.success_criteria == _TASK_OBJ["success_criteria"]


def test_taskmaster_output_rejects_a_required_nested_field_left_null():
    """task is required: a 'null' string must still fail (coerces to None ->
    missing required), not silently pass."""
    with pytest.raises(Exception):
        TaskMasterOutput.model_validate(
            {"reasoning": "x", "rating_of_previous_task": "None", "task": "null"}
        )


def test_player_gameaction_coerces_stringified_handoff():
    handoff = {"self_assessment": "succeeded", "task_summary": "task done"}
    g = GameAction.model_validate(
        {
            "inputs": ["a"],
            "reasoning": "r",
            "last_turn_succeeded": None,
            "memory_updates": "none",
            "return_to_taskmaster": json.dumps(handoff),
        }
    )
    assert g.return_to_taskmaster is not None
    assert g.return_to_taskmaster.self_assessment == "succeeded"
    # The "None" string for a normal (no-handoff) turn coerces to None.
    g2 = GameAction.model_validate(
        {
            "inputs": ["a"],
            "reasoning": "r",
            "last_turn_succeeded": None,
            "memory_updates": "none",
            "return_to_taskmaster": "None",
        }
    )
    assert g2.return_to_taskmaster is None


@pytest.mark.parametrize("token", ["None", "null", "", " none "])
def test_player_last_turn_succeeded_string_null_coerces_to_none(token):
    """mimo emits the literal string "None"/"null"/"" for the null case of the
    Optional[bool] last_turn_succeeded; pydantic's bool parser rejects all of
    them and the Player run dies at Turn 1. These are the VERBATIM tokens caught
    from a live mimo Player call on 2026-06-17."""
    g = GameAction.model_validate(
        {
            "inputs": ["down"],
            "reasoning": "go down",
            "last_turn_succeeded": token,
            "memory_updates": "none",
        }
    )
    assert g.last_turn_succeeded is None


@pytest.mark.parametrize("token,expected", [("True", True), ("true", True), ("false", False)])
def test_player_last_turn_succeeded_string_bool_still_parses(token, expected):
    g = GameAction.model_validate(
        {"inputs": ["down"], "reasoning": "r", "last_turn_succeeded": token, "memory_updates": "none"}
    )
    assert g.last_turn_succeeded is expected


def test_player_stringified_inputs_list_decodes():
    g = GameAction.model_validate(
        {
            "inputs": json.dumps(["down", "a"]),
            "reasoning": "r",
            "last_turn_succeeded": True,
            "memory_updates": "none",
        }
    )
    assert g.inputs == ["down", "a"]


def test_legacy_gameaction_unaffected_and_has_no_handoff_field():
    """The TM-disabled schema drops return_to_taskmaster; the inherited
    validator (check_fields=False) must not break its build."""
    assert "return_to_taskmaster" not in _LegacyGameAction.model_fields
    g = _LegacyGameAction.model_validate(
        {
            "inputs": ["up"],
            "reasoning": "r",
            "last_turn_succeeded": True,
            "memory_updates": "none",
        }
    )
    assert g.inputs == ["up"]
