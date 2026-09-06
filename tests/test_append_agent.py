"""Behavioral tests at the serialized HTTP and emulator commit boundaries."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import shutil

import pytest

from src.agent.append_agent import AppendAgent, ContinuityError, StreamAssembly, cache_totals, normalize_usage, replay_check
from src.config import load_config, find_latest_config, _validate_config
from src.app.catalog import list_configs


ROOT = Path(__file__).resolve().parents[1]
IMAGE = "data:image/png;base64,c2NyZWVu"


@pytest.fixture
def config():
    cfg = load_config(str(ROOT / "configs/config-append.yaml"), llm_alias="openai/test-model")
    cfg["compaction"]["every_n_turns"] = 2
    cfg["transport"]["max_retries"] = 0
    cfg["compaction"]["max_retries"] = 0
    return cfg


class FakeProvider:
    def __init__(self):
        self.requests = []
        self.invalid_compaction = False
        self.dropped = False

    async def __call__(self, body, key, timeout, on_chunk):
        request = json.loads(body)
        self.requests.append(request)
        choice = request.get("tool_choice")
        phase = choice.get("function", {}).get("name") if isinstance(choice, dict) else None
        if phase is None:
            phase = "compaction" if isinstance(request["messages"][-1]["content"], str) and request["messages"][-1]["content"].startswith("Pause gameplay") else "gameplay"
        n = len(self.requests)
        if phase == "gameplay":
            value = {"inputs": ["a"], "reasoning": f"Observed dialogue; next screen will close it. Call {n}",
                     "last_turn_succeeded": None if n == 1 else True}
        else:
            value = {"continuation_summary": "Dialogue closed. Continue north.",
                     "memory": {"invented_key": {"last_seen": "Town"}}}
            if self.invalid_compaction:
                value["memory"] = "invalid"
            for tool in request.get("tools", []):
                function = tool["function"]
                if function["name"] == "compaction" and function["parameters"]["properties"]["memory"]["type"] == "string":
                    value["memory"] = json.dumps(value["memory"])
        msg = {"role": "assistant", "content": None,
               "reasoning_details": [{"type": "reasoning.encrypted", "id": f"rs-{n}", "index": 0,
                                      "format": "test-v1", "data": f"opaque-{n}"}],
               "tool_calls": [{"id": f"call-{n}", "type": "function", "function": {
                   "name": phase, "arguments": json.dumps(value)}}]}
        if "tools" not in request:
            msg.pop("tool_calls")
            msg["content"] = json.dumps({"result": value})
        result = {"id": f"response-{n}", "provider": "Test Provider", "choices": [{"message": msg, "finish_reason": "tool_calls" if "tools" in request else "stop"}],
                  "usage": {"prompt_tokens": 1000 * n, "completion_tokens": 100, "cost": 0.01,
                            "prompt_tokens_details": {"cached_tokens": 0 if n == 1 else 1000, "cache_write_tokens": 100},
                            "completion_tokens_details": {"reasoning_tokens": 50}}}
        if self.dropped:
            result["input_transformations"] = [{"type": "thinking_dropped"}]
        return result


def engine(config, tmp_path, provider=None):
    events = []
    provider = provider or FakeProvider()
    agent = AppendAgent(config, tmp_path, lambda kind, data: events.append({"type": kind, **deepcopy(data)}), transport=provider)
    return agent, provider, events


def test_append_compact_and_action_commit(config, tmp_path):
    agent, provider, events = engine(config, tmp_path)
    async def play():
        for turn in (1, 2):
            await agent.play(turn, f"observed-{turn}", IMAGE, {"old": "forget me"})
            assert agent.checkpoint["completed_turn"] == turn - 1
            agent.commit_action(turn)
        old_response = deepcopy(provider.requests[1]["messages"][3])
        await agent.play(3, "POST second action", IMAGE)
        assert len(provider.requests) == 4  # two actions, compaction, next action
        compact, resumed = provider.requests[2:]
        assert compact["tools"] == resumed["tools"] == provider.requests[0]["tools"]
        assert compact["messages"][0] == resumed["messages"][0]
        assert "POST second action" in json.dumps(compact["messages"][-2])
        assert compact["messages"][3] == old_response
        assert not any(m.get("reasoning_details") for m in resumed["messages"])
        assert "invented_key" in resumed["messages"][1]["content"]
        assert agent.checkpoint["completed_turn"] == 2
        assert agent.checkpoint["segment"] == 2
        assert agent.checkpoint["handover"]["memory"] == {"invented_key": {"last_seen": "Town"}}
        agent.commit_action(3)
        assert agent.checkpoint["segment_turns"] == 1
    asyncio.run(play())
    assert len([e for e in events if e["type"] == "compaction_complete"]) == 1
    assert len([e for e in events if e["type"] == "llm_request_usage"]) == 4
    assert len(list((tmp_path / "conversation/assets").iterdir())) == 1
    assert all(e["continuity"]["local_replay"] == "intact" for e in events if e["type"] == "llm_request_usage")
    # Normal messages carry a new observation, not a rendered history/memory block.
    assert "Memory" not in provider.requests[1]["messages"][-1]["content"][0]["text"]


def test_failed_compaction_never_replaces_checkpoint(config, tmp_path):
    agent, provider, events = engine(config, tmp_path)
    async def play():
        for turn in (1, 2):
            await agent.play(turn, "screen", IMAGE)
            agent.commit_action(turn)
        checkpoint = deepcopy(agent.checkpoint)
        provider.invalid_compaction = True
        with pytest.raises(ValueError):
            await agent.play(3, "new evidence", IMAGE)
        assert agent.checkpoint == checkpoint
        assert not any(e["type"] == "compaction_complete" for e in events)
    asyncio.run(play())


@pytest.mark.parametrize("after_compaction", [False, True])
def test_resume_exact_context_and_no_duplicate_compaction(config, tmp_path, after_compaction):
    original, provider, events = engine(config, tmp_path / "original")
    async def run():
        for turn in (1, 2):
            await original.play(turn, "observation", IMAGE)
            original.commit_action(turn)
        if after_compaction:
            # This accepts turn 3 but never executes it. Checkpoint stays at 2.
            await original.play(3, "post action two", IMAGE)
        saved = original.export_checkpoint()
        shutil.copytree(tmp_path / "original/conversation", tmp_path / "resumed/conversation")
        resumed, other, _ = engine(config, tmp_path / "resumed")
        resumed.restore(saved, 2)
        await resumed.play(3, "post action two", IMAGE)
        assert len(other.requests) == (1 if after_compaction else 2)
        if after_compaction:
            assert other.requests[-1]["messages"] == provider.requests[-1]["messages"]
        resumed.commit_action(3)
        assert resumed.state["completed_turn"] == 3
    asyncio.run(run())


def test_replay_checks_detect_dropped_edited_detached_and_reordered_blocks():
    messages = [{"role": "user", "content": "same"}, {"role": "assistant", "reasoning_details": [{"data": "A"}, {"data": "B"}]}]
    for mutation in (lambda m: m[1].pop("reasoning_details"),
                     lambda m: m[1]["reasoning_details"][0].update(data="wrong"),
                     lambda m: m[1]["reasoning_details"].reverse(),
                     lambda m: m.insert(0, {"role": "user", "content": "different"})):
        changed = deepcopy(messages)
        mutation(changed)
        check = replay_check(messages, changed)
        assert check["local_replay"] != "intact" or check["history_prefix"] != "unchanged"


def test_cache_unknown_zero_and_weighted_totals():
    unknown = normalize_usage({"prompt_tokens": 50})
    zero = normalize_usage({"prompt_tokens": 100, "prompt_tokens_details": {"cached_tokens": 0}})
    hit = normalize_usage({"prompt_tokens": 900, "prompt_tokens_details": {"cached_tokens": 900, "cache_write_tokens": 0}})
    total = cache_totals([unknown, zero, hit])
    assert unknown["cached_tokens"] is None
    assert zero["cache_read_fraction"] == 0
    assert total["input_read_fraction"] == .9
    assert total["request_hit_fraction"] == .5
    assert total["measured_attempts"] == 2


def test_provider_drop_stops_without_committing(config, tmp_path):
    agent, provider, events = engine(config, tmp_path)
    provider.dropped = True
    with pytest.raises(ContinuityError):
        asyncio.run(agent.play(1, "", IMAGE))
    assert agent.pending is None
    assert agent.checkpoint["completed_turn"] == 0
    assert len(provider.requests) == 1
    assert any(e.get("continuity", {}).get("provider_feedback") == "dropped" for e in events)


@pytest.mark.parametrize("mode", ["native_json", "prompted"])
def test_non_tool_output_modes_preserve_history(config, tmp_path, mode):
    config["_llm_resolved"] = {"output_mode": mode}
    agent, provider, _ = engine(config, tmp_path)
    async def run():
        await agent.play(1, "", IMAGE)
        agent.commit_action(1)
        await agent.play(2, "", IMAGE)
        assert provider.requests[1]["messages"][:len(provider.requests[0]["messages"])] == provider.requests[0]["messages"]
        agent.commit_action(2)
        await agent.play(3, "post action two", IMAGE)
        assert agent.checkpoint["segment"] == 2
        assert len(provider.requests) == 4
    asyncio.run(run())


def test_stream_assembly_retains_encrypted_data_and_calls():
    assembly = StreamAssembly()
    for data, args in (("abc", '{"inputs":'), ("xyz", '["a"]}')):
        assembly.add({"choices": [{"index": 0, "delta": {
            "reasoning_details": [{"index": 0, "type": "reasoning.encrypted", "id": "r1", "data": data}],
            "tool_calls": [{"index": 0, **({"id": "c1"} if data == "abc" else {}), "function": {
                **({"name": "gameplay"} if data == "abc" else {}), "arguments": args}}]}}]})
    assert assembly.message["reasoning_details"][0]["data"] == "abcxyz"
    assert json.loads(assembly.message["tool_calls"][0]["function"]["arguments"]) == {"inputs": ["a"]}
    with pytest.raises(ContinuityError):
        assembly.add({"choices": [{"delta": {"reasoning_details": [{"data": "no identity"}]}}]})


def test_config_is_selectable_but_does_not_replace_default(config):
    assert "config-append" in list_configs()
    assert list_configs()[-1] == "config-4.0"
    assert find_latest_config().name == "config-4.0.yaml"
    assert "memory_updates" not in config["system_prompt"]
    assert "Suggested keys" in config["compaction"]["prompt"]
    config["compaction"]["every_n_turns"] = 0
    with pytest.raises(ValueError):
        _validate_config(config)


def test_integrated_loop_saves_compaction_and_preserves_trace(config, tmp_path):
    from PIL import Image
    from src.agent.turn import TurnManager
    from src.core import RunLogger, StateManager
    from src.app.trace_build import build_run_trace
    from src.core.snapshots import SnapshotManager

    class Emulator:
        facing = None
        def __init__(self): self.actions = []
        def capture_screenshot(self, preprocess=True): return Image.new("RGB", (8, 8))
        def press_button_list(self, buttons): self.actions.append(buttons)
        def wait_for_stable_screen(self): return 0
        def save_state(self, path): Path(path).write_text(str(len(self.actions)))
    class Vision:
        mode = "direct_multimodal"
        def analyze_screenshot(self, image): return {}
        def format_for_llm(self, analysis): return []
        def image_to_data_url(self, image): return IMAGE

    config["runs_directory"] = str(tmp_path)
    config["run_name"] = "integration"
    logger = RunLogger(config)
    state = StateManager(str(logger.run_dir / "state.json"))
    state.save()
    manager = TurnManager(config)
    emulator = Emulator()
    manager.setup(emulator, state, Vision(), logger)
    provider = FakeProvider()
    manager.append_agent.transport = provider
    manager.run_loop(3)
    assert len(emulator.actions) == 3
    assert manager.total_cost_usd == pytest.approx(.04)
    before = logger.run_dir / "savepoints/turn_2"
    after = logger.run_dir / "savepoints/turn_3"
    assert json.loads((before / "append_state.json").read_text())["segment"] == 2
    assert json.loads((before / "append_state.json").read_text())["completed_turn"] == 2
    assert SnapshotManager.verify_savepoint(before)
    assert SnapshotManager.verify_savepoint(after)
    assert state.get_truncated_view() == {"invented_key": {"last_seen": "Town"}}
    trace = build_run_trace(logger.run_dir)
    assert trace["cache"]["attempts"] == 4
    assert trace["turn_count"] == 3
    for turn in trace["tasks"][0]["turns"]:
        results = [s for s in turn["trace"]["steps"] if s["type"] == "final_result"]
        assert len(results) == 1
        assert json.loads(results[0]["args"])["reasoning"] == turn["reasoning"]
    turn3 = trace["tasks"][0]["turns"][2]
    first, second, third = [t["trace"] for t in trace["tasks"][0]["turns"]]
    assert first["system_prompt"] and third["system_prompt"]
    assert second["system_prompt"] == ""
    assert first["conversation"] == third["conversation"] == "start"
    assert second["conversation"] == "continued"
    assert first["segment_context"] and third["segment_context"]
    assert second["segment_context"] == ""
    assert "invented_key" in third["segment_context"]
    for display in (first, second, third):
        assert "Earlier conversation" not in display["user_input"]
        assert "Top goal:" not in display["user_input"]
        assert "Memory from the last handover:" not in display["user_input"]
    timeline = trace["tasks"][0]["timeline"]
    assert [(e["kind"], e.get("turn", e.get("number"))) for e in timeline] == [
        ("turn", 1), ("turn", 2), ("compaction", 1), ("turn", 3)]
    assert [e["fresh"] for e in timeline if e["kind"] == "turn"] == [True, False, True]
    assert trace["compaction_count"] == 1
    assert timeline[2]["after_turn"] == 2
    assert timeline[2]["complete"]
    compaction_trace = timeline[2]["trace"]
    assert compaction_trace["system_prompt"] == ""
    assert len(compaction_trace["user_messages"]) == 2
    assert "Top goal:" not in compaction_trace["user_input"]
    assert compaction_trace["output"]["memory"] == {"invented_key": {"last_seen": "Town"}}
    assert isinstance(next(s for s in compaction_trace["steps"] if s["type"] == "final_result")["args"], dict)
    assert any(e["type"] == "compaction_complete" for e in timeline[2]["diagnostics"])
    assert not any(e.get("phase") == "compaction" or e["type"].startswith("compaction_") for e in turn3["diagnostics"])
    assert any(s["type"] == "final_result" for s in turn3["trace"]["steps"])
    summary = json.loads((logger.run_dir / "run_summary.json").read_text())
    assert summary["conversation"]["completed_game_turns"] == 3


def test_final_serialization_loss_detected(config, tmp_path):
    agent, provider, _ = engine(config, tmp_path)
    async def run():
        await agent.play(1, "", IMAGE)
        agent.commit_action(1)
        original = agent._body
        def broken(phase, messages):
            body, mode = original(phase, messages)
            for message in body["messages"]:
                message.pop("reasoning_details", None)
            return body, mode
        agent._body = broken
        with pytest.raises(ContinuityError):
            await agent.play(2, "", IMAGE)
        assert len(provider.requests) == 1
    asyncio.run(run())


def test_context_pressure_compacts_before_interval(config, tmp_path):
    agent, provider, events = engine(config, tmp_path)
    config["compaction"]["every_n_turns"] = 20
    async def run():
        await agent.play(1, "", IMAGE)
        agent.commit_action(1)
        agent.state["last_input_tokens"] = config["compaction"]["context_token_limit"]
        await agent.play(2, "latest result", IMAGE)
        assert len(provider.requests) == 3
        assert any(e.get("reason") == "context_threshold" for e in events)
    asyncio.run(run())


def test_budget_reached_by_compaction_prevents_gameplay(config, tmp_path):
    costs = []
    agent, provider, events = engine(config, tmp_path)
    agent.on_usage = lambda usage: costs.append(usage["cost_usd"])
    agent.budget_exhausted = lambda: sum(costs) >= .03
    async def run():
        for turn in (1, 2):
            await agent.play(turn, "", IMAGE)
            agent.commit_action(turn)
        with pytest.raises(RuntimeError, match="Spend budget"):
            await agent.play(3, "latest result", IMAGE)
        assert len(provider.requests) == 3
        assert agent.checkpoint["segment"] == 2
        assert agent.checkpoint["completed_turn"] == 2
    asyncio.run(run())


def test_signature_rejection_is_archived_and_not_silently_retried(config, tmp_path):
    from src.agent.append_agent import ProviderRequestError
    config["transport"]["max_retries"] = 2
    async def reject(*args):
        raise ProviderRequestError(400, {"error": {"message": "Invalid thought signature"}})
    agent, _, events = engine(config, tmp_path, reject)
    with pytest.raises(ProviderRequestError):
        asyncio.run(agent.play(1, "", IMAGE))
    errors = [e for e in events if e["type"] == "llm_request_error"]
    assert len(errors) == 1
    assert errors[0]["continuity"]["provider_feedback"] == "signature_rejected"
    assert errors[0]["cached_tokens"] is None
    assert len(list((tmp_path / 'conversation').glob('*-response.json'))) == 1
