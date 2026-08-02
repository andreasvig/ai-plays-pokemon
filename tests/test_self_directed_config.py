"""Self-directed single-agent mode (config-4.0): no TaskMaster, no tools, the
agent sets its own goals in its memory dictionary.

Covers the four things that make the mode real rather than nominal:
  1. config-4.0 loads, carries NO task_master block, and is what a bare
     `pokemon run` now picks up (find_latest_config).
  2. The model-facing output schema on that path is the four-field
     _LegacyGameAction — no `return_to_taskmaster` handoff field.
  3. The freeplay/benchmark steering reaches the PLAYER now that there is no
     TaskMaster prompt to carry it (_player_mode_guidelines).
  4. The executor stamps the run mode where the Player can read it and does NOT
     conjure TaskMaster wiring for a config that has no TaskMaster.
"""

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agent.agent import (
    GameAction,
    _LegacyGameAction,
    _player_mode_guidelines,
    create_agent,
)
from src.app.executor import RunExecutor
from src.config import CONFIGS_DIR, find_latest_config, load_config
from src.core.prompts import fill_prompt

CONFIG_4_0 = CONFIGS_DIR / "config-4.0.yaml"


@pytest.fixture(scope="module")
def raw_4_0() -> dict:
    """config-4.0.yaml as authored (pre-hoist), for prompt-contract assertions."""
    with open(CONFIG_4_0) as f:
        return yaml.safe_load(f)


# --- 1. The config itself ----------------------------------------------------


def test_config_4_0_exists_and_has_no_task_master_block():
    cfg = load_config(str(CONFIG_4_0), llm_alias="gemini-3.5-flash(medium)")
    assert "task_master" not in cfg, (
        "config-4.0 must omit the task_master block entirely — its absence is "
        "what disables the meta-agent, the handoff schema field, the per-task "
        "budget validator, and the search tools."
    )


def test_config_4_0_is_the_default_config():
    """A bare `pokemon run` (no --config) resolves to the self-directed mode."""
    assert find_latest_config().name == "config-4.0.yaml"


def test_top_level_goal_is_the_only_goal_given(raw_4_0):
    task = raw_4_0["task"]
    assert task["goal"].strip(), "the config must supply the one top-level goal"
    # No per-task scaffolding survives: the agent invents its own steps.
    assert "success_criteria" not in task


def test_no_per_task_turn_budget(raw_4_0):
    """`max_turns_per_task` is a TaskMaster construct and 4.0 has no tasks, so
    the key is deliberately absent. The whole-run cap comes from the caller
    (`pokemon run --turns N`, or the queue's `max_turns`), never from here."""
    assert "max_turns_per_task" not in raw_4_0


def test_config_4_0_loads_without_a_turn_budget():
    """Its absence must not trip the loader — the positive-int check on
    `max_turns_per_task` is gated on `task_master.enabled`, which 4.0 lacks."""
    cfg = load_config(str(CONFIG_4_0), llm_alias="gemini-3.5-flash(medium)")
    assert cfg.get("task_master", {}).get("enabled", False) is False
    assert "max_turns_per_task" not in cfg


# --- 2. The output schema ----------------------------------------------------


def test_player_schema_has_no_handoff_field_when_task_master_absent():
    cfg = load_config(str(CONFIG_4_0), llm_alias="gemini-3.5-flash(medium)")
    tm_enabled = bool(cfg.get("task_master", {}).get("enabled", False))
    assert tm_enabled is False
    # Mirrors create_agent's OutputModel choice without constructing an Agent
    # (which would need a live provider).
    OutputModel = GameAction if tm_enabled else _LegacyGameAction
    assert OutputModel is _LegacyGameAction
    assert "return_to_taskmaster" not in OutputModel.model_fields
    assert set(OutputModel.model_fields) == {
        "inputs", "reasoning", "last_turn_succeeded", "memory_updates",
    }


def test_prompt_does_not_reference_a_taskmaster(raw_4_0):
    """The prompt and the schema must agree: nothing to hand back to."""
    pa = raw_4_0["player_agent"]
    blob = (pa["system_prompt"] + pa["user_prompt"]).lower()
    assert "taskmaster" not in blob
    assert "task master" not in blob
    assert "return_to_taskmaster" not in blob


def test_prompt_directs_self_goal_setting_via_memory(raw_4_0):
    """Self-direction is a memory contract, so it must live in the memory
    section and name the EXACT key the UI reads back."""
    sp = raw_4_0["player_agent"]["system_prompt"]
    memory_section = sp.split("# Memory Guidelines", 1)
    assert len(memory_section) == 2, "system prompt lost its Memory Guidelines section"
    body = memory_section[1]
    # The bullet, not merely a passing mention: this is the string the agent
    # copies into memory_updates, and Spectate.svelte reads `current_goal` back
    # off the memory dict. Rename one side only and the panel silently empties.
    assert "- **current_goal**:" in body, (
        "Memory Guidelines must define the `current_goal` key itself — the UI "
        "reads that exact key name out of the streamed memory dict"
    )


def test_user_prompt_carries_the_top_level_goal(raw_4_0):
    """The user message states the goal every turn, via the same {{task_block}}
    placeholder the legacy path already fills from config `task:`."""
    up = raw_4_0["player_agent"]["user_prompt"]
    assert "## Top Goal" in up
    assert "{{task_block}}" in up
    assert "{{handoff_instruction}}" not in up


# --- 3. Mode guidelines reach the Player -------------------------------------

PLAYER_CFG = {
    "freeplay_guidelines": "# Freeplay Mode: TRUE\nexplore freely.",
    "benchmark_guidelines": "# Benchmark Mode: TRUE\ngo fast.",
}


def test_freeplay_mode_selects_freeplay_guidelines():
    assert (
        _player_mode_guidelines({**PLAYER_CFG, "mode": "freeplay"})
        == PLAYER_CFG["freeplay_guidelines"]
    )


def test_benchmark_mode_selects_benchmark_guidelines():
    assert (
        _player_mode_guidelines({**PLAYER_CFG, "mode": "benchmark"})
        == PLAYER_CFG["benchmark_guidelines"]
    )


def test_unset_mode_defaults_to_benchmark():
    """A direct `pokemon run` with no mode stamped must not drift to freeplay."""
    assert _player_mode_guidelines(dict(PLAYER_CFG)) == PLAYER_CFG["benchmark_guidelines"]


def test_missing_guidelines_keys_collapse_to_empty_string():
    """A config that doesn't use the placeholder is unaffected."""
    assert _player_mode_guidelines({"mode": "freeplay"}) == ""


def test_config_4_0_player_prompt_actually_consumes_the_guidelines(raw_4_0):
    """End-to-end on the real config: the placeholder exists, both guideline
    blocks are authored on the Player, and filling picks exactly one."""
    pa = raw_4_0["player_agent"]
    assert "{{mode_guidelines}}" in pa["system_prompt"]
    assert pa["freeplay_guidelines"].strip()
    assert pa["benchmark_guidelines"].strip()

    hoisted = {**pa, "mode": "freeplay"}
    filled = fill_prompt(
        pa["system_prompt"], mode_guidelines=_player_mode_guidelines(hoisted)
    )
    assert "{{mode_guidelines}}" not in filled
    assert "Freeplay Mode: TRUE" in filled
    assert "Benchmark Mode: TRUE" not in filled


@pytest.mark.parametrize(
    "mode,expected,forbidden",
    [
        ("freeplay", "Freeplay Mode: TRUE", "Benchmark Mode: TRUE"),
        ("benchmark", "Benchmark Mode: TRUE", "Freeplay Mode: TRUE"),
    ],
)
def test_create_agent_fills_the_placeholder_on_the_real_config(mode, expected, forbidden):
    """The wiring, not just the helper: build the actual Player agent off
    config-4.0 and read the system prompt it was constructed with.

    Without this, a create_agent that simply forgot to pass `mode_guidelines`
    would still pass every other test in this file while shipping a literal
    `{{mode_guidelines}}` to the model and losing all freeplay/benchmark
    steering — the failure mode is invisible except right here.
    """
    cfg = load_config(str(CONFIG_4_0), llm_alias="gemini-3.5-flash(medium)")
    cfg["mode"] = mode
    agent, _settings, _fallbacks = create_agent(cfg)

    prompts = getattr(agent, "_system_prompts", None)
    assert prompts, (
        "could not read the constructed system prompt off the pydantic-ai Agent "
        "(_system_prompts is internal — if an upgrade renamed it, re-point this "
        "assertion rather than deleting it)"
    )
    blob = "\n".join(prompts)
    assert "{{mode_guidelines}}" not in blob, (
        "create_agent shipped the raw placeholder to the model"
    )
    assert expected in blob
    assert forbidden not in blob


# --- 4. Executor wiring ------------------------------------------------------


def test_stamp_mode_sets_the_player_readable_key_on_a_task_master_less_config():
    cfg: dict = {"_config_path": "configs/config-4.0.yaml"}
    RunExecutor._stamp_mode(cfg, "freeplay")
    assert cfg["mode"] == "freeplay"
    assert "task_master" not in cfg, (
        "stamping the mode must not conjure a task_master block — a phantom "
        "block reads as 'TaskMaster is configured' and makes the executor "
        "resolve a model for an agent that is never constructed"
    )


def test_stamp_mode_still_feeds_the_task_master_on_a_3_x_config():
    cfg: dict = {"task_master": {"enabled": True, "history_window_n": 20}}
    RunExecutor._stamp_mode(cfg, "benchmark")
    assert cfg["mode"] == "benchmark"
    assert cfg["task_master"]["mode"] == "benchmark"


@pytest.mark.parametrize(
    "cfg,expected",
    [
        ({}, False),
        ({"task_master": {}}, False),
        ({"task_master": {"enabled": False}}, False),
        ({"task_master": {"enabled": True}}, True),
    ],
)
def test_tm_enabled_matches_config_py_semantics(cfg, expected):
    """Same rule src/config.py + turn.py use: absent block or absent/false
    `enabled` means the meta-agent does not run."""
    assert RunExecutor._tm_enabled(cfg) is expected


# ── 5. the UI must be able to tell a TM run from a self-directed one ──
#
# Spectate collapses the "Current task" panel away on a self-directed run (the
# goal already shows in the memory dictionary as `current_goal`). It keys that
# on the CONFIG, not on "has a task arrived yet?" — `task` is also null during
# the opening turns of a TaskMaster run, before the first handoff.


def _register_probe_session(run_id: str, config: dict):
    from pathlib import Path as _Path

    from src.dashboard.event_bridge import EventBridge
    from src.dashboard.screen_stream import ScreenStreamer
    from src.dashboard.server import RunSession, get_registry

    get_registry().register(
        RunSession(
            run_id=run_id,
            label=run_id,
            config=config,
            bridge=EventBridge(),
            streamer=ScreenStreamer(stream_path="/tmp/_test_selfdirected_stream.png"),
            state_manager=None,
            run_dir=_Path("/tmp") / run_id,
        )
    )


@pytest.mark.parametrize(
    "config,expected",
    [
        ({"task": {"goal": "beat the game"}}, False),
        ({"task": {"goal": "x"}, "task_master": {"enabled": False}}, False),
        ({"task": {"goal": "x"}, "task_master": {"enabled": True}}, True),
    ],
)
def test_run_config_endpoint_reports_whether_a_task_master_runs(config, expected):
    from fastapi.testclient import TestClient

    from src.dashboard.server import app, get_registry

    run_id = f"test-tm-flag-{expected}-{len(config)}"
    _register_probe_session(run_id, config)
    try:
        payload = TestClient(app).get(f"/runs/{run_id}/api/config").json()
        assert payload["task_master"] is expected
    finally:
        get_registry().unregister(run_id)


def test_spectate_keys_the_task_panel_on_the_config_not_on_task_arrival():
    """The panel is gated on `hasTaskMaster` (from /api/config) and the goal
    panel is gone entirely — the memory dictionary already renders the key."""
    src = (
        Path(__file__).parent.parent
        / "src/dashboard/web/src/components/Spectate.svelte"
    ).read_text()

    assert "hasTaskMaster = !!cfg.task_master" in src, "flag must come from the config"
    assert "{#if hasTaskMaster}" in src, "the task panel must be gated on it"
    assert ".panels.solo" in src, "memory must take the full row when collapsed"
    # The duplicate self-goal panel is gone: `current_goal` shows in memory only.
    assert "selfGoal" not in src
    assert "Current goal (self-set)" not in src
