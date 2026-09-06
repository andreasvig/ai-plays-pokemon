"""Behavioral tests at the serialized HTTP and emulator commit boundaries."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import shutil

import pytest

from src.agent.append_agent import AppendAgent, ContinuityError, StreamAssembly, cache_economics, cache_totals, implied_cache, normalize_usage, replay_check
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


def test_context_estimate_counts_tokens_not_bytes(config, tmp_path):
    """A verbose reasoner must not trigger compaction on byte count alone."""
    class Verbose(FakeProvider):
        async def __call__(self, *args):
            response = await super().__call__(*args)
            message = response["choices"][0]["message"]
            message["reasoning"] = "x" * 60000
            message["reasoning_details"][0]["data"] = "x" * 60000
            return response
    agent, provider, events = engine(config, tmp_path, Verbose())
    config["compaction"]["every_n_turns"] = 20
    threshold = config["compaction"]["context_token_limit"] * config["compaction"]["context_limit_fraction"]
    async def run():
        await agent.play(1, "", IMAGE)
        agent.commit_action(1)
        agent.state["last_input_tokens"] = 10000
        # Mutation control: the previous byte-denominated formula crossed the threshold here.
        assert 10000 + len(json.dumps(agent.state["messages"][-2:]).encode()) > threshold
        await agent.play(2, "latest result", IMAGE)
        assert len(provider.requests) == 2
        assert not any(e.get("reason") == "context_threshold" for e in events)
    asyncio.run(run())


def test_context_estimate_reserves_realistic_output_not_the_ceiling(config, tmp_path):
    agent, provider, events = engine(config, tmp_path)
    config["compaction"]["every_n_turns"] = 20
    config["compaction"]["max_output_tokens"] = config["compaction"]["context_token_limit"] - 1  # an endpoint-sized ceiling
    async def run():
        await agent.play(1, "", IMAGE)
        agent.commit_action(1)
        await agent.play(2, "", IMAGE)
        assert len(provider.requests) == 2  # a huge ceiling alone must not trigger compaction
    asyncio.run(run())


def test_provider_side_failure_is_a_transport_error_and_retried(config, tmp_path):
    config["transport"]["max_retries"] = 1
    class Flaky(FakeProvider):
        async def __call__(self, *args):
            response = await super().__call__(*args)
            if len(self.requests) == 1:
                response["choices"] = [{"message": {"role": "assistant", "content": ""},
                                        "finish_reason": "stop", "native_finish_reason": "network_error"}]
                response["usage"] = None
            return response
    agent, provider, events = engine(config, tmp_path, Flaky())
    assert asyncio.run(agent.play(1, "", IMAGE)).inputs == ["a"]
    errors = [e for e in events if e["type"] == "llm_request_error"]
    assert [e["error"] for e in errors] == ["Provider returned no completion (network_error)"]
    assert len(provider.requests) == 2


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


GEMINI_TIERS = [{"name": f"Google AI Studio | tier {i}", "provider_name": "Google AI Studio",
                 "pricing": {"prompt": str(p), "input_cache_read": str(p / 10), "input_cache_write": "0.00000002"}}
                for i, p in enumerate((0.000000375, 0.00000075, 0.00000135))]


def _attempt(n, billed, cached=None, writes=0, provider="Google AI Studio"):
    return {"request_tokens": n, "cached_tokens": cached, "cache_write_tokens": writes, "provider": provider,
            "raw_usage": {"cost_details": {"upstream_inference_prompt_cost": billed}}}


def test_implied_cache_picks_tier_and_reports_no_discount():
    # Gemini turn 5 from the 2026-09-06 samples: billed exactly at the $0.75/M tier, provider says 0 cached.
    result = implied_cache(_attempt(11817, 0.00886275, cached=0), GEMINI_TIERS)
    assert result["status"] == "no_discount_billed"
    assert result["tier"].endswith("tier 1") and result["implied_cached_tokens"] == 0
    assert result["agreement"] == "matches"
    assert abs(result["billed_prompt_rate_per_m"] - 0.75) < 1e-6


def test_implied_cache_backs_out_discount_and_flags_disagreement():
    n, full, read = 10000, 0.00000075, 0.000000075
    billed = 6000 * full + 4000 * read  # 4,000 tokens billed at the cache-read rate
    result = implied_cache(_attempt(n, billed, cached=None), GEMINI_TIERS)
    assert result["status"] == "discount_billed"
    assert abs(result["implied_cached_tokens"] - 4000) <= 1
    assert result["agreement"] == "provider_not_reporting"
    assert implied_cache(_attempt(n, billed, cached=4000), GEMINI_TIERS)["agreement"] == "matches"
    assert implied_cache(_attempt(n, billed, cached=0), GEMINI_TIERS)["agreement"] == "billing_implies_more"


def test_implied_cache_removes_cache_write_premium():
    tier = [{"name": "Anthropic", "provider_name": "Anthropic",
             "pricing": {"prompt": "0.000005", "input_cache_read": "0.0000005", "input_cache_write": "0.00000625"}}]
    n, cached, writes = 6322, 3956, 2366
    billed = cached * 0.0000005 + writes * 0.00000625
    result = implied_cache(_attempt(n, billed, cached=cached, writes=writes, provider="Anthropic"), tier)
    assert abs(result["implied_cached_tokens"] - cached) <= 1 and result["agreement"] == "matches"
    # First request of a segment: almost everything is a write billed at 1.25x list,
    # so the raw rate exceeds the list price. The tier must still be found.
    first = implied_cache(_attempt(3958, 3956 * 0.00000625 + 2 * 0.000005, cached=0, writes=3956, provider="Anthropic"), tier)
    assert first["status"] == "no_discount_billed" and first["agreement"] == "matches"


def test_implied_cache_degrades_without_prices_or_billing():
    assert implied_cache(_attempt(100, 0.01), [])["status"] == "unpriced"
    assert implied_cache({"request_tokens": 100, "raw_usage": {}}, GEMINI_TIERS)["status"] == "unbilled"
    assert implied_cache(_attempt(100, 1.0), GEMINI_TIERS)["status"] == "rate_above_list"
    totals = cache_totals([{**_attempt(100, 0.000075, cached=0), "implied_cache": implied_cache(_attempt(100, 0.000075, cached=0), GEMINI_TIERS)}])
    assert totals["implied_cached_tokens"] == 0 and totals["implied_agreement"] == {"matches": 1}


def test_pricing_snapshot_written_once_and_failure_is_recorded(config, tmp_path):
    calls = []
    async def fetcher(model, key):
        calls.append(model)
        return {"model": model, "endpoints": [{"name": "t", "pricing": {"prompt": "0.000001"}}]}
    agent, provider, events = engine(config, tmp_path)
    agent.pricing_fetcher = fetcher
    async def run():
        await agent.play(1, "", IMAGE)
        agent.commit_action(1)
        await agent.play(2, "", IMAGE)
    asyncio.run(run())
    assert calls == [config["llm_model"]]
    assert json.loads((tmp_path / "conversation/endpoint-pricing.json").read_text())["endpoints"][0]["name"] == "t"
    async def broken(model, key):
        raise RuntimeError("offline")
    agent2, _, _ = engine(config, tmp_path / "second")
    agent2.pricing_fetcher = broken
    assert asyncio.run(agent2.play(1, "", IMAGE)).inputs == ["a"]
    assert json.loads((tmp_path / "second/conversation/endpoint-pricing.json").read_text())["error"] == "offline"


def test_cache_economics_verdicts():
    def attempt(n, cached, prompt, read):
        pricing = {"prompt": str(prompt)} | ({"input_cache_read": str(read)} if read is not None else {})
        a = _attempt(n, n * prompt if not cached else cached * (read or 0) + (n - cached) * prompt, cached=cached, provider="P")
        a["implied_cache"] = implied_cache(a, [{"name": "P", "provider_name": "P", "pricing": pricing}])
        return a
    assert cache_economics([attempt(1000, 0, 1.4e-7, None)])["verdict"] == "no_cache_offered"
    assert cache_economics([attempt(1000, 500, 1e-7, 1e-7)])["verdict"] == "no_discount_on_hits"
    assert cache_economics([attempt(1000, 0, 7.5e-7, 7.5e-8)])["verdict"] == "priced_no_hits"
    saving = cache_economics([attempt(1000, 500, 5e-6, 5e-7)])
    assert saving["verdict"] == "saving" and abs(saving["saved_usd"] - 500 * 4.5e-6 / 1e6 * 1e6 / 1e6) < 1e-9 or saving["saved_usd"] > 0
    assert cache_economics([])["verdict"] == "unknown"


def test_final_turn_text_only_closes_the_screenshot_turn(tmp_path):
    """OpenAI via OpenRouter cannot extend the prompt cache past the first image when the request's
    final turn contains one (2026-09-06). The Astra profile splits the turn: screenshot, assistant
    acknowledgement, text-only action prompt. Compaction requests keep the acknowledgement and end
    with the compaction prompt. The default profile keeps the screenshot as the final message."""
    cfg = load_config(str(ROOT / "configs/config-append.yaml"), llm_alias="openai/gpt-6-astra")
    cfg["compaction"]["every_n_turns"] = 2
    cfg["transport"]["max_retries"] = 0
    cfg["compaction"]["max_retries"] = 0
    assert cfg["_provider_profile"]["final_turn_text_only"] is True
    agent, provider, events = engine(cfg, tmp_path)
    async def run():
        await agent.play(1, "first screen", IMAGE)
        agent.commit_action(1)
        await agent.play(2, "second screen", IMAGE)
        agent.commit_action(2)
        await agent.play(3, "third screen", IMAGE)  # compaction is due after two turns
    asyncio.run(run())
    first = provider.requests[0]["messages"]
    assert first[-1] == {"role": "user", "content": "Turn 1: respond with your next action now."}
    assert first[-2] == {"role": "assistant", "content": "Observed."}
    assert first[-3]["role"] == "user" and first[-3]["content"][1]["type"] == "image_url"
    second = provider.requests[1]["messages"]
    assert second[-1]["content"] == "Turn 2: respond with your next action now."
    # ... action prompt 1, the model's call, its tool result, screenshot 2, acknowledgement, action prompt 2
    assert [m["role"] for m in second[-6:]] == ["user", "assistant", "tool", "user", "assistant", "user"]
    assert second[:len(first)] == first  # the retained conversation is a strict prefix: the split messages are kept
    compaction = provider.requests[2]["messages"]
    assert compaction[-1]["content"].startswith("Pause gameplay")
    assert compaction[-2] == {"role": "assistant", "content": "Observed."}
    assert compaction[-3]["content"][1]["type"] == "image_url"
    after = provider.requests[3]["messages"]  # first gameplay request of the new segment
    assert after[-1]["content"] == "Turn 3: respond with your next action now."
    assert any(e["type"] == "compaction_complete" for e in events)
    # Control: the default profile ends the request with the screenshot itself.
    control_cfg = load_config(str(ROOT / "configs/config-append.yaml"), llm_alias="openai/test-model")
    control_cfg["transport"]["max_retries"] = 0
    control, control_provider, _ = engine(control_cfg, tmp_path / "control")
    asyncio.run(control.play(1, "first screen", IMAGE))
    assert control_provider.requests[0]["messages"][-1]["content"][1]["type"] == "image_url"
