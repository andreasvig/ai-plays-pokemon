import asyncio
from copy import deepcopy
import json
import shutil

import pytest
import yaml

from src.agent.append_agent import AppendAgent, ContinuityError
from src.agent.provider_profiles import PROFILE_PATH, resolve_provider_profile, router_diagnostics, output_json_text
from src.config import load_config
from test_append_agent import ROOT, IMAGE, FakeProvider


MODELS = list(yaml.safe_load(PROFILE_PATH.read_text())["profiles"])


@pytest.mark.parametrize("model,variant", [(model, None) for model in MODELS] + [
    ("google/gemma-4-31b-it", "gemma-guidance"), ("google/gemma-4-31b-it", "gemma-replay"),
    ("google/gemma-4-31b-it", "gemma-guidance-coreweave"), ("google/gemma-4-31b-it", "gemma-replay-coreweave"),
    ("google/gemma-4-31b-it", "gemma-guidance-bf16")])
def test_profile_roundtrip_and_fresh_segment(model, variant, tmp_path):
    config = load_config(str(ROOT / "configs/config-append.yaml"), llm_alias=model, provider_profile=variant)
    config["compaction"]["every_n_turns"] = 2
    provider = FakeProvider()
    events = []
    emit = lambda kind, data: events.append({"type": kind, **deepcopy(data)})
    agent = AppendAgent(config, tmp_path / "first", emit, provider)
    async def run():
        for turn in (1, 2):
            await agent.play(turn, "screen", IMAGE)
            agent.commit_action(turn)
        saved = agent.export_checkpoint()
        shutil.copytree(tmp_path / "first/conversation", tmp_path / "resumed/conversation")
        resumed = AppendAgent(config, tmp_path / "resumed", emit, provider)
        resumed.restore(saved, 2)
        await resumed.play(3, "screen", IMAGE)
        resumed.commit_action(3)
        assert resumed.state["segment"] == 2
        assert resumed.state["handover"]["memory"]["invented_key"]["last_seen"] == "Town"
        assert resumed.state["session_id"] == agent.state["session_id"]
    asyncio.run(run())
    assert len(provider.requests) == 4
    profile = config["_provider_profile"]
    assert len({r["session_id"] for r in provider.requests}) == 1
    for request in provider.requests:
        route = {"only": [profile["endpoint"]], "allow_fallbacks": False, "require_parameters": True}
        if profile.get("excluded_endpoints"):
            route["ignore"] = profile["excluded_endpoints"]
        assert request["provider"] == route
        assert request["transforms"] == []
        assert request["reasoning"]["exclude"] is False
        # No harness budget: output may run to the endpoint's own ceiling.
        assert request["max_tokens"] == profile["max_completion_tokens"]
    assert config["compaction"]["context_token_limit"] == profile["context_length"]
    # Tool definitions AND tool choice remain stable during the handover request.
    for key in ("tools", "tool_choice", "response_format"):
        assert all(r.get(key) == provider.requests[0].get(key) for r in provider.requests)
    # The model's own reply (a split-turn profile also carries a synthetic "Observed." assistant message).
    previous = [m for m in provider.requests[1]["messages"] if m["role"] == "assistant" and m.get("content") != "Observed."][0]
    if profile["reasoning_replay"] == "omit_prior":
        assert "reasoning_details" not in previous
        assert any(m.get("reasoning_details") for m in agent.state["messages"])
    else:
        assert previous["reasoning_details"][0]["data"] == "opaque-1"
    assert not any(m.get("reasoning_details") for m in provider.requests[-1]["messages"])
    if profile["cache_mode"] == "system_breakpoint":
        assert provider.requests[0]["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert all(e["continuity"]["local_replay"] == "intact" for e in events if e["type"] == "llm_request_usage")


def test_profile_rejects_incompatible_effort_limits_and_resume(tmp_path):
    config = load_config(str(ROOT / "configs/config-append.yaml"), llm_alias="moonshotai/kimi-k3")
    for change in ({"thinking": {"effort": "medium"}}, {"thinking": {"enabled": False}}):
        with pytest.raises(ValueError):
            resolve_provider_profile({**deepcopy(config), **change})
    # No harness budgets: a lower endpoint ceiling simply becomes the output limit...
    lowered = deepcopy(config)
    lowered["provider_profiles"]["overrides"] = {"max_completion_tokens": 2048}
    resolve_provider_profile(lowered)
    assert lowered["transport"]["max_output_tokens"] == lowered["compaction"]["max_output_tokens"] == 2048
    # ...but an explicit per-alias max_tokens may not exceed it.
    bad = deepcopy(lowered)
    bad["_llm_resolved"] = {**(bad.get("_llm_resolved") or {}), "max_tokens": 5000}
    with pytest.raises(ValueError, match="exceeds"):
        resolve_provider_profile(bad)
    original = AppendAgent(config, tmp_path, lambda *args: None)
    packed = original.export_checkpoint()
    changed = deepcopy(config)
    changed["_provider_profile"]["endpoint"] = "different/host"
    with pytest.raises(ContinuityError, match="profile changed"):
        AppendAgent(changed, tmp_path, lambda *args: None).restore(packed, 0)


def test_router_compression_stops_and_preserves_raw_metadata(tmp_path):
    config = load_config(str(ROOT / "configs/config-append.yaml"), llm_alias=MODELS[0])
    class Compressed(FakeProvider):
        async def __call__(self, *args):
            result = await super().__call__(*args)
            result["openrouter_metadata"] = {"pipeline": [{"type": "context_compression", "data": {"original_count": 4, "compressed_count": 2}}]}
            return result
    agent = AppendAgent(config, tmp_path, lambda *args: None, Compressed())
    with pytest.raises(ContinuityError, match="dropped reasoning"):
        asyncio.run(agent.play(1, "", IMAGE))
    assert agent.pending is None
    files = list((tmp_path / "conversation").glob("*-response.json"))
    assert json.loads(files[0].read_text())["openrouter_metadata"]["pipeline"]
    diagnostics, destructive = router_diagnostics({"openrouter_metadata": {"pipeline": []}})
    assert not destructive
    assert diagnostics["provider_feedback"] == "not_reported"


def test_legacy_config_has_no_profile():
    config = load_config(str(ROOT / "configs/config-4.0.yaml"), llm_alias=MODELS[0])
    assert "_provider_profile" not in config


def test_gemma_json_fence_is_syntax_only():
    text = '```json\n{"result": {"memory": {"custom": [1, 2]}}}\n```'
    assert output_json_text(text, {}) == text
    assert json.loads(output_json_text(text, {"allow_json_fence": True}))["result"]["memory"] == {"custom": [1, 2]}
    for invalid in (text + "extra prose", '```json\n{invalid}\n```'):
        with pytest.raises(ValueError):
            json.loads(output_json_text(invalid, {"allow_json_fence": True}))


def test_provider_change_without_returned_thinking_still_stops(tmp_path):
    config = load_config(str(ROOT / "configs/config-append.yaml"), llm_alias=MODELS[0])
    class Switching(FakeProvider):
        async def __call__(self, *args):
            result = await super().__call__(*args)
            result["choices"][0]["message"].pop("reasoning_details")
            result["provider"] = "First" if len(self.requests) == 1 else "Second"
            return result
    agent = AppendAgent(config, tmp_path, lambda *args: None, Switching())
    async def run():
        await agent.play(1, "", IMAGE)
        agent.commit_action(1)
        with pytest.raises(ContinuityError, match="Serving provider/model changed"):
            await agent.play(2, "", IMAGE)
        assert agent.checkpoint["completed_turn"] == 1
    asyncio.run(run())


def test_gemma_variants_only_change_replay_and_cannot_switch_on_resume(tmp_path):
    from src.cli.runner import prepare_config
    configs = [prepare_config(str(ROOT / "configs/config-append.yaml"), "google/gemma-4-31b-it",
                              provider_profile=name) for name in ("gemma-guidance", "gemma-replay")]
    profiles = [c["_provider_profile"] for c in configs]
    assert {k for k in profiles[0] if profiles[0][k] != profiles[1][k]} == {"name", "reasoning_replay", "reasoning_use"}
    assert configs[0]["system_prompt"] == configs[1]["system_prompt"]
    assert configs[0]["compaction"] == configs[1]["compaction"]
    assert configs[0]["run_name"].endswith("gemma-guidance")
    assert configs[1]["run_name"].endswith("gemma-replay")
    first = AppendAgent(configs[0], tmp_path, lambda *args: None)
    second = AppendAgent(configs[1], tmp_path, lambda *args: None)
    with pytest.raises(ContinuityError, match="profile changed"):
        second.restore(first.export_checkpoint(), 0)
    for variant, model in (("typo", "google/gemma-4-31b-it"), ("gemma-replay", "moonshotai/kimi-k3")):
        with pytest.raises(ValueError, match="[Pp]rovider profile"):
            load_config(str(ROOT / "configs/config-append.yaml"), llm_alias=model, provider_profile=variant)


@pytest.mark.parametrize("model", ["google/gemma-4-31b-it", "qwen/qwen3.8-flash"])
@pytest.mark.parametrize("invalid", [False, True])
def test_unwrapped_prompted_output_still_validates_action(tmp_path, invalid, model):
    # Gemma dropped the envelope after a handover, Qwen on compaction; both are
    # accepted by default now, and invalid button names are still rejected.
    config = load_config(str(ROOT / "configs/config-append.yaml"), llm_alias=model)
    config["transport"]["max_retries"] = 0
    class Unwrapped(FakeProvider):
        async def __call__(self, *args):
            response = await super().__call__(*args)
            message = response["choices"][0]["message"]
            value = json.loads(message["content"])["result"]
            if invalid:
                value["inputs"] = ["walk-through-wall"]
            message["content"] = '```json\n' + json.dumps(value) + '\n```'
            return response
    agent = AppendAgent(config, tmp_path, lambda *args: None, Unwrapped())
    if invalid:
        with pytest.raises(ValueError):
            asyncio.run(agent.play(1, "", IMAGE))
        assert agent.pending is None
    else:
        assert asyncio.run(agent.play(1, "", IMAGE)).inputs == ["a"]


def test_endpoint_cache_warning_for_undiscounted_endpoints(tmp_path):
    events = []
    config = load_config(str(ROOT / "configs/config-append.yaml"), llm_alias="google/gemma-4-31b-it")
    tag = config["_provider_profile"]["endpoint"]
    async def no_read_price(model, key):
        return {"model": model, "endpoints": [{"tag": tag, "pricing": {"prompt": "0.00000014"}}]}
    agent = AppendAgent(config, tmp_path / "a", lambda k, d: events.append((k, deepcopy(d))), FakeProvider(), pricing_fetcher=no_read_price)
    asyncio.run(agent.play(1, "", IMAGE))
    warnings = [d for k, d in events if k == "endpoint_warning"]
    assert len(warnings) == 1 and "no cache-read price" in warnings[0]["message"]
    async def same_price(model, key):
        return {"model": model, "endpoints": [{"tag": tag, "pricing": {"prompt": "0.0000001", "input_cache_read": "0.0000001"}}]}
    events.clear()
    agent = AppendAgent(config, tmp_path / "b", lambda k, d: events.append((k, deepcopy(d))), FakeProvider(), pricing_fetcher=same_price)
    asyncio.run(agent.play(1, "", IMAGE))
    assert any("hits save nothing" in d["message"] for k, d in events if k == "endpoint_warning")
    async def discounted(model, key):
        return {"model": model, "endpoints": [{"tag": tag, "pricing": {"prompt": "0.0000001", "input_cache_read": "0.00000005"}}]}
    events.clear()
    agent = AppendAgent(config, tmp_path / "c", lambda k, d: events.append((k, deepcopy(d))), FakeProvider(), pricing_fetcher=discounted)
    asyncio.run(agent.play(1, "", IMAGE))
    assert not [d for k, d in events if k == "endpoint_warning"]
