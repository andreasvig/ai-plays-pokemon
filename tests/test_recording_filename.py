"""A downloaded recording must name its own settings.

On disk every clip is `recording.mp4`; the run folder around it carries the
meaning. `app.recording_name` rebuilds that meaning as a filename so a clip
dragged out of the app still says what it was — date, game, kind + playstyle,
config, model + tier, spend, the caps it ran under, and how it ended.

Three layers, plus the two facts that had to start being persisted for any of
it to be recoverable:
  1. the summary writer now records the turn cap and the spend cap whether or
     not either fired (they were previously write-on-fire, or not written at all);
  2. the builder, over synthetic run folders;
  3. the download endpoint, which must carry the name on Content-Disposition.

Controls are the point of several of these: a name that always contains
"casual" tells you nothing, so each discriminating segment is checked against a
folder that should NOT produce it.
"""

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.agent.turn import TurnManager
from src.app.recording_name import recording_filename, recording_stem


# --------------------------------------------------------------------------
# fixtures: a synthetic run folder
# --------------------------------------------------------------------------

def _run_folder(
    tmp_path,
    name="2026-08-02_15-56-43_config-4.0__claude-opus-5-medium",
    *,
    config=None,
    summary=None,
):
    """A run dir with a config.json + run_summary.json, both overridable.

    Defaults describe the run this feature was built against: a casual
    exploration run of FireRed on config-4.0 with opus-5 at medium, recorded,
    which spent $1.03 of a $1.00 ceiling over 17 turns.
    """
    d = Path(tmp_path) / name
    d.mkdir(parents=True, exist_ok=True)
    base_config = {
        "_config_path": "configs/config-4.0.yaml",
        "_llm_alias": "claude-opus-5(medium)",
        "llm_model": "anthropic/claude-opus-5",
        "mode": "freeplay",
        "game_name": "Pokemon FireRed",
        "emulator": {
            "rom_path": "roms/Pokemon - FireRed Version (USA, Europe) (Rev 1).gba"
        },
        "_record": {"view": "simple", "speed": "cut-thinking", "fps": 30},
    }
    base_summary = {
        "kind": "casual",
        "status": "completed",
        "session": {
            "llm_alias": "claude-opus-5(medium)",
            "total_turns": 17,
            "started_at": "2026-08-02T15:56:45",
            "segment": {"resumed_at_turn": None},
        },
        "cost": {"total_usd": 1.033577},
    }
    if config is not None:
        base_config.update(config)
    if summary is not None:
        base_summary.update(summary)
    (d / "config.json").write_text(json.dumps(base_config))
    (d / "run_summary.json").write_text(json.dumps(base_summary))
    return d


# --------------------------------------------------------------------------
# 1. the two caps must be persisted at all
# --------------------------------------------------------------------------

class _Logger:
    def __init__(self, run_dir):
        self.run_dir = run_dir


def _writer(run_dir, **attrs):
    """A TurnManager built only far enough to exercise _write_run_summary.

    Same __new__ shape as tests/test_continue_accounting_cumulative.py — the
    writer must keep working for callers that never ran __init__, which is why
    it reads its own latches with getattr.
    """
    m = TurnManager.__new__(TurnManager)
    m._run_start_time = time.time()
    m._prior_duration_s = 0.0
    m.config = {"_llm_alias": None, "llm_model": "m", "thinking": None,
                "task": {"goal": "g"}}
    m.fallback_models = []
    m.tasks = None
    m.turn_number = 4
    m.task_master_turns = 0
    m.total_cost_usd = 0.5
    m.task_master_cost_usd = 0.0
    m.ocr = None
    m.total_input_tokens = 0
    m.total_output_tokens = 0
    m.turn_costs = []
    m.turn_explanations = []
    m._explanation_turns = []
    m.referee = None
    m._aborted_no_output = False
    m._abort_error = None
    m.logger = _Logger(run_dir)
    for k, v in attrs.items():
        setattr(m, k, v)
    return m


def _written(run_dir):
    return json.loads((Path(run_dir) / "run_summary.json").read_text())


def test_turn_cap_is_recorded_even_though_it_did_not_fire(tmp_path):
    # The cap arrives as a run_loop ARGUMENT, so before this it was unrecoverable
    # from the folder. A run that ended for another reason still ran under it.
    m = _writer(tmp_path, _turn_limit=20, max_spend_usd=None, _budget_stopped=False)
    m._write_run_summary()
    s = _written(tmp_path)
    assert s["max_turns"] == 20
    assert "stop_reason" not in s          # nothing fired
    assert "max_spend_usd" not in s        # no ceiling was set


def test_spend_cap_is_recorded_when_it_did_not_fire(tmp_path):
    # Regression on the old shape: max_spend_usd used to be written ONLY inside
    # the _budget_stopped branch, so "ran under a $2 ceiling and came in below
    # it" was indistinguishable from "unbounded".
    m = _writer(tmp_path, _turn_limit=20, max_spend_usd=2.0, _budget_stopped=False)
    m._write_run_summary()
    s = _written(tmp_path)
    assert s["max_spend_usd"] == 2.0
    assert "stop_reason" not in s


def test_budget_stop_still_records_reason_and_ceiling(tmp_path):
    m = _writer(tmp_path, _turn_limit=1500, max_spend_usd=1.0, _budget_stopped=True)
    m._write_run_summary()
    s = _written(tmp_path)
    assert s["stop_reason"] == "max_spend"
    assert s["max_spend_usd"] == 1.0
    assert s["max_turns"] == 1500


def test_writer_survives_a_manager_that_never_ran_a_loop(tmp_path):
    # Control for the getattr reads: no _turn_limit, no max_spend_usd attribute
    # at all (the __new__ callers in the existing suite). Neither key appears
    # and nothing raises.
    m = _writer(tmp_path)
    m._write_run_summary()
    s = _written(tmp_path)
    assert "max_turns" not in s
    assert "max_spend_usd" not in s


# --------------------------------------------------------------------------
# 2. the builder
# --------------------------------------------------------------------------

def test_full_name_carries_every_setting(tmp_path):
    d = _run_folder(
        tmp_path,
        config={"referee": {"stop_at": "starter_chosen", "enforce": False}},
        summary={"max_spend_usd": 1.0, "max_turns": 20, "stop_reason": "max_spend"},
    )
    assert recording_filename(d) == (
        "2026-08-02_1556_firered_casual-exploration_config-4.0"
        "_claude-opus-5-medium_17turns_1.03usd_cap1.00usd_cap20turns"
        "_stop-starter-chosen_simple-cut-thinking_hit-budget.mp4"
    )


def test_date_and_time_come_from_the_folder(tmp_path):
    d = _run_folder(tmp_path)
    assert recording_stem(d).startswith("2026-08-02_1556_")


def test_legacy_folder_without_started_at_still_gets_a_timestamp(tmp_path):
    # The folder prefix is preferred precisely because every run ever written
    # has it, including ones predating session.started_at.
    d = _run_folder(tmp_path, summary={"session": {"total_turns": 3}})
    assert recording_stem(d).startswith("2026-08-02_1556_")


def test_timestamp_falls_back_to_started_at_for_an_unprefixed_folder(tmp_path):
    d = _run_folder(tmp_path, name="hand-rolled-run")
    assert recording_stem(d).startswith("2026-08-02_1556_")


def test_game_is_the_registry_id_not_the_display_name(tmp_path):
    # `firered`, the token --rom and `pokemon ls roms` use — not "pokemon-firered".
    d = _run_folder(tmp_path)
    assert "_firered_" in recording_stem(d)
    assert "pokemon-firered" not in recording_stem(d)


def test_off_registry_rom_still_names_the_game(tmp_path):
    d = _run_folder(
        tmp_path,
        config={"emulator": {"rom_path": "roms/nope.gba"}, "game_name": "Pokemon Emerald"},
    )
    assert "_pokemon-emerald_" in recording_stem(d)


def test_exploration_and_speed_do_not_collapse(tmp_path):
    # Mutation control for the playstyle segment: the two casual modes must
    # produce different names, or the segment is decoration.
    explore = recording_stem(_run_folder(tmp_path / "a", config={"mode": "freeplay"}))
    speed = recording_stem(_run_folder(tmp_path / "b", config={"mode": "benchmark"}))
    assert "casual-exploration" in explore
    assert "casual-speed" in speed
    assert explore != speed


def test_official_run_is_named_by_benchmark_not_playstyle(tmp_path):
    d = _run_folder(
        tmp_path,
        config={"mode": "benchmark", "_config_path": "configs/config-3.13.yaml"},
        summary={"kind": "official", "benchmark": "pokebench-easy",
                 "benchmark_version": "1.0"},
    )
    stem = recording_stem(d)
    assert "_official-pokebench-easy_" in stem
    assert "speed" not in stem.split("_official-pokebench-easy_")[0]
    assert "_config-3.13_" in stem


def test_legacy_official_run_without_explicit_kind(tmp_path):
    # Same defensive inference projection.py does: a benchmark_version and no
    # explicit kind means official.
    d = _run_folder(tmp_path, summary={"benchmark_version": "1.0", "kind": None})
    assert "_official_" in recording_stem(d)


def test_unknown_mode_passes_through_rather_than_guessing(tmp_path):
    d = _run_folder(tmp_path, config={"mode": "sandbox"})
    assert "casual-sandbox" in recording_stem(d)


def test_model_tier_is_in_the_name(tmp_path):
    # The whole point of several runs differing only by effort level.
    med = recording_stem(_run_folder(tmp_path / "a"))
    mx = recording_stem(_run_folder(
        tmp_path / "b", summary={"session": {"llm_alias": "claude-opus-5(max)",
                                             "total_turns": 17,
                                             "segment": {}}}))
    assert "claude-opus-5-medium" in med
    assert "claude-opus-5-max" in mx
    assert med != mx


def test_continue_is_marked_with_its_resume_turn(tmp_path):
    d = _run_folder(
        tmp_path,
        summary={"session": {"llm_alias": "claude-opus-5(medium)", "total_turns": 40,
                             "segment": {"resumed_at_turn": 17}}},
    )
    assert "_cont-from-t17_" in recording_stem(d)


def test_fresh_run_is_not_marked_as_a_continue(tmp_path):
    assert "cont-from" not in recording_stem(_run_folder(tmp_path))


def test_official_turn_sentinel_is_not_printed(tmp_path):
    # 10_000_000 is the executor's "no cap in practice" sentinel; a literal
    # cap10000000turns would be noise.
    d = _run_folder(tmp_path, summary={"max_turns": 10_000_000})
    assert "cap10000000turns" not in recording_stem(d)
    assert "turns" in recording_stem(d)   # the ACTUAL turn count still shows


def test_a_real_turn_cap_is_printed(tmp_path):
    # Control for the sentinel test above — the suppression must be the
    # magnitude, not the segment being dead.
    d = _run_folder(tmp_path, summary={"max_turns": 20})
    assert "_cap20turns" in recording_stem(d)


def test_stop_at_appears_only_when_set(tmp_path):
    without = recording_stem(_run_folder(tmp_path / "a"))
    with_ = recording_stem(_run_folder(
        tmp_path / "b", config={"referee": {"stop_at": "starter_chosen"}}))
    assert "stop-" not in without
    assert "_stop-starter-chosen" in with_


@pytest.mark.parametrize(
    "summary_patch,expected",
    [
        ({"status": "cancelled"}, "cancelled"),
        ({"status": "crashed"}, "crashed"),
        ({"status": "terminated"}, "terminated"),
        ({"stop_reason": "max_spend"}, "hit-budget"),
        ({"error": "no valid model output"}, "no-output"),
    ],
)
def test_a_run_that_did_not_simply_finish_says_so(tmp_path, summary_patch, expected):
    d = _run_folder(tmp_path, summary=summary_patch)
    assert recording_stem(d).endswith(expected)


def test_a_completed_run_carries_no_outcome_marker(tmp_path):
    # Control for the parametrize above: the marker must be absent on the
    # normal path, or it says nothing when present.
    stem = recording_stem(_run_folder(tmp_path))
    for marker in ("cancelled", "crashed", "terminated", "hit-budget", "no-output"):
        assert marker not in stem


def test_unrecorded_run_omits_the_record_segment(tmp_path):
    d = _run_folder(tmp_path, config={"_record": None})
    assert "simple" not in recording_stem(d)


def test_empty_folder_falls_back_to_the_run_id(tmp_path):
    d = Path(tmp_path) / "2026-08-02_15-56-43_config-4.0__claude-opus-5-medium"
    d.mkdir(parents=True)
    assert recording_filename(d) == f"{d.name}.mp4"


def test_corrupt_json_falls_back_to_the_run_id(tmp_path):
    d = Path(tmp_path) / "2026-08-02_15-56-43_whatever"
    d.mkdir(parents=True)
    (d / "config.json").write_text("{not json")
    (d / "run_summary.json").write_text("")
    assert recording_filename(d) == f"{d.name}.mp4"


def test_explicit_run_id_overrides_the_folder_name_in_the_fallback(tmp_path):
    d = Path(tmp_path) / "tmpdir"
    d.mkdir(parents=True)
    assert recording_filename(d, run_id="the-real-id") == "the-real-id.mp4"


def test_name_stays_under_the_macos_component_limit(tmp_path):
    # A hand-rolled config can carry an arbitrarily long alias; 255 bytes is a
    # hard filesystem limit, and an unsaveable name is worse than a short one.
    d = _run_folder(
        tmp_path,
        config={"_config_path": f"configs/{'x' * 300}.yaml"},
        summary={"session": {"llm_alias": "y" * 300, "total_turns": 5,
                             "segment": {}}},
    )
    name = recording_filename(d)
    assert len(name.encode()) < 255
    assert name.endswith(".mp4")
    assert not name.startswith("-")


def test_two_runs_differing_only_in_tier_get_different_names(tmp_path):
    # The end-to-end property: the name must DISCRIMINATE. Same game, config,
    # caps and recording settings; only the effort tier differs.
    a = recording_filename(_run_folder(
        tmp_path / "a", summary={"session": {"llm_alias": "claude-opus-5(low)",
                                             "total_turns": 17, "segment": {}}}))
    b = recording_filename(_run_folder(
        tmp_path / "b", summary={"session": {"llm_alias": "claude-opus-5(max)",
                                             "total_turns": 17, "segment": {}}}))
    assert a != b


# --------------------------------------------------------------------------
# 3. the endpoint
# --------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path):
    """The dashboard app with its runs_root pointed at a temp dir.

    Exercised through TestClient, not the running control center — a live
    request to that app hits a real queue and a real executor.
    """
    from src.dashboard import server as srv

    runs_root = Path(tmp_path) / "runs"
    runs_root.mkdir()

    class _Executor:
        def __init__(self, runs_root):
            self.runs_root = runs_root

    srv.configure_control_plane(
        queue_manager=object(),
        executor=_Executor(str(runs_root)),
        run_index=object(),
    )
    yield TestClient(srv.app), runs_root
    srv._CONTROL["queue"] = None
    srv._CONTROL["executor"] = None
    srv._CONTROL["index"] = None


def test_endpoint_carries_the_settings_filename(client):
    tc, runs_root = client
    d = _run_folder(runs_root, summary={"max_spend_usd": 1.0, "max_turns": 20})
    (d / "recording.mp4").write_bytes(b"\x00" * 64)

    r = tc.get(f"/api/runs/{d.name}/recording.mp4")
    assert r.status_code == 200
    disposition = r.headers["content-disposition"]
    # inline, so the <video> element still plays it rather than downloading.
    assert disposition.startswith("inline")
    assert "firered" in disposition
    assert "claude-opus-5-medium" in disposition
    assert "cap1.00usd" in disposition
    assert "cap20turns" in disposition


def test_endpoint_no_longer_serves_the_bare_run_id(client):
    # Mutation control on the wiring: the OLD behaviour was filename=run_id,
    # and the run id contains neither the game nor the caps.
    tc, runs_root = client
    d = _run_folder(runs_root, summary={"max_spend_usd": 1.0, "max_turns": 20})
    (d / "recording.mp4").write_bytes(b"\x00" * 64)

    disposition = tc.get(f"/api/runs/{d.name}/recording.mp4").headers["content-disposition"]
    assert f'filename="{d.name}.mp4"' not in disposition


def test_endpoint_404s_for_a_run_without_a_recording(client):
    tc, runs_root = client
    d = _run_folder(runs_root)
    assert tc.get(f"/api/runs/{d.name}/recording.mp4").status_code == 404
