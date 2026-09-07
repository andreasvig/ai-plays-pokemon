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
    config = load_config(str(ROOT / "configs/config-5.0.yaml"), llm_alias=model, provider_profile=variant)
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
    config = load_config(str(ROOT / "configs/config-5.0.yaml"), llm_alias="moonshotai/kimi-k3")
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
    config = load_config(str(ROOT / "configs/config-5.0.yaml"), llm_alias=MODELS[0])
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
    config = load_config(str(ROOT / "configs/config-5.0.yaml"), llm_alias=MODELS[0])
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
    configs = [prepare_config(str(ROOT / "configs/config-5.0.yaml"), "google/gemma-4-31b-it",
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
            load_config(str(ROOT / "configs/config-5.0.yaml"), llm_alias=model, provider_profile=variant)


@pytest.mark.parametrize("model", ["google/gemma-4-31b-it", "qwen/qwen3.8-flash"])
@pytest.mark.parametrize("invalid", [False, True])
def test_unwrapped_prompted_output_still_validates_action(tmp_path, invalid, model):
    # Gemma dropped the envelope after a handover, Qwen on compaction; both are
    # accepted by default now, and invalid button names are still rejected.
    config = load_config(str(ROOT / "configs/config-5.0.yaml"), llm_alias=model)
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
    config = load_config(str(ROOT / "configs/config-5.0.yaml"), llm_alias="google/gemma-4-31b-it")
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


# ─────────── decision D (2026-09-07): defaults reach every model ───────────


def _registry():
    from src.config import _load_models_registry

    return _load_models_registry()


def _first_alias(base, entry):
    levels = entry.get("thinking_levels") or []
    return f"{base}({levels[0]})" if levels else base


# An UNPROFILED model. Every entry in the pruned registry now carries a profile
# (decision D2: prune to newest-per-family, then profile every survivor), so the
# unprofiled path is reached by a RAW ``provider/model`` id — which is a
# first-class, documented way to run a model that is not in the registry yet, and
# exactly the case where the transport contract used to fall back to nothing.
# Asserted to be genuinely absent from both files so this can't silently start
# testing the profiled path.
UNPROFILED_RAW_ID = "openai/not-in-any-registry-or-profile"


def test_the_unprofiled_fixture_is_really_unprofiled():
    """Guard for the three tests below: they'd pass vacuously on a profiled id."""
    catalog = yaml.safe_load(PROFILE_PATH.read_text())
    assert UNPROFILED_RAW_ID not in catalog["profiles"]
    assert UNPROFILED_RAW_ID not in {
        e.get("openrouter_id") for e in _registry().values() if isinstance(e, dict)
    }


def test_defaults_block_reaches_unprofiled_models():
    """Every append run gets the `defaults:` contract, profiled or not.

    ``resolve_provider_profile`` used to return BEFORE building from
    ``defaults:``, so an unprofiled model silently ran with no ``transforms: []``
    guard (OpenRouter's middle-out compression free to truncate the very history
    our compaction owns), no cache mode, no ``final_turn_text_only``, and a
    weaker provider-drift guard — while looking identical to a probed run
    everywhere in the UI.

    What an unprofiled model must NOT get is a fabricated endpoint: a profile
    entry exists because someone probed one, and a default cannot invent it.
    """
    config = load_config(
        str(ROOT / "configs/config-5.0.yaml"), llm_alias=UNPROFILED_RAW_ID
    )
    profile = config.get("_provider_profile")
    assert profile is not None, "no profile record at all"
    assert profile["unprofiled"] is True
    # The defaults, present and therefore enforceable downstream.
    assert profile["cache_mode"]
    assert profile["memory_encoding"]
    assert profile["final_turn_text_only"] is False
    assert profile["reasoning_replay"] in ("all", "omit_prior")
    # Unpinned — an empty endpoint is what makes `_body` omit provider.only.
    assert profile["endpoint"] == "", "invented an endpoint"
    # The config's own authored budgets are NOT rewritten from themselves:
    # transport and compaction stay deliberately different.
    assert config["transport"]["max_output_tokens"] == 8192
    assert config["compaction"]["max_output_tokens"] == 12288

    # Control: a PROFILED model does get its probed endpoint + budgets, so this
    # change did not flatten everything to the defaults.
    profiled_id = list(yaml.safe_load(PROFILE_PATH.read_text())["profiles"])[0]
    other = load_config(str(ROOT / "configs/config-5.0.yaml"), llm_alias=profiled_id)
    assert other["_provider_profile"]["unprofiled"] is False
    assert other["_provider_profile"]["endpoint"]


def test_unprofiled_request_body_is_not_pinned_to_an_empty_endpoint(tmp_path):
    """The consequence of endpoint "": no ``provider.only`` on the wire.

    ``_body`` builds ``provider: {only: [profile.endpoint]}`` whenever a profile
    is present, so applying the defaults to unprofiled models without this would
    have pinned 'the empty endpoint' on every such request. Read off the real
    request the agent sends, not off the profile record.
    """
    config = load_config(
        str(ROOT / "configs/config-5.0.yaml"), llm_alias=UNPROFILED_RAW_ID
    )
    provider = FakeProvider()
    agent = AppendAgent(config, tmp_path, lambda *args: None, provider)
    asyncio.run(agent.play(1, "screen", IMAGE))
    body = provider.requests[0]
    assert "provider" not in body or body["provider"] is None, body.get("provider")
    # The defaults still arrive where they matter: our compaction owns trimming.
    assert body["transforms"] == []


def test_unprofiled_model_keeps_its_registry_output_mode_and_sampling():
    """`defaults:` must not overwrite what the REGISTRY owns per model.

    ``defaults.output_mode`` is ``tool``; forcing it onto a model whose registry
    entry says ``prompted`` (because its live host rejects tool calls) would
    break it on the first request. This is the with/without control for the
    "defaults for everyone" change: the defaults have to arrive AND lose to the
    registry on the two keys the registry genuinely owns.

    Driven through ``_llm_resolved`` rather than a registry entry, because every
    registry entry is profiled now — and a profiled model takes its output_mode
    from the profile, which would test the wrong branch.
    """
    config = load_config(
        str(ROOT / "configs/config-5.0.yaml"), llm_alias=UNPROFILED_RAW_ID
    )
    assert config["_provider_profile"]["output_mode"] == "tool"  # the default

    registry_owned = deepcopy(config)
    registry_owned.pop("_provider_profile")
    registry_owned["_llm_resolved"] = {
        "openrouter_id": UNPROFILED_RAW_ID,
        "output_mode": "prompted",
        "temperature": 0.3,
        "top_p": 0.95,
    }
    resolve_provider_profile(registry_owned)
    profile = registry_owned["_provider_profile"]
    assert profile["output_mode"] == "prompted"
    assert profile["sampling"] == {"temperature": 0.3, "top_p": 0.95}


def test_unprofiled_model_with_an_effort_ladder_is_not_refused():
    """An empty `reasoning_efforts` means "not probed", not "nothing is legal".

    ``defaults.reasoning_efforts`` is ``[]``. Checking an effort against it
    would reject every effort-tiered model the moment defaults started reaching
    them — so an empty list waves the request through, and the subset test below
    is what keeps that from becoming a hole for PROFILED models.
    """
    for effort in ("max", "xhigh", "high", "medium", "low", "minimal"):
        config = load_config(
            str(ROOT / "configs/config-5.0.yaml"), llm_alias=UNPROFILED_RAW_ID
        )
        config.pop("_provider_profile")
        config["_llm_resolved"] = {
            "openrouter_id": UNPROFILED_RAW_ID,
            "reasoning": {"effort": effort},
        }
        resolve_provider_profile(config)
        assert config["_provider_profile"]["reasoning"]["effort"] == effort

    # Control: a PROFILED model still refuses an effort outside its probed list,
    # so the empty-list allowance did not disable the check.
    catalog = yaml.safe_load(PROFILE_PATH.read_text())
    pinned = next(
        (mid, p) for mid, p in catalog["profiles"].items()
        if p.get("reasoning_efforts")
    )
    model_id, profile_entry = pinned
    illegal = next(
        e for e in ("max", "xhigh", "high", "medium", "low", "minimal", "none")
        if e not in profile_entry["reasoning_efforts"]
    )
    bad = load_config(str(ROOT / "configs/config-5.0.yaml"), llm_alias=model_id)
    bad.pop("_provider_profile")
    bad["_llm_resolved"] = {"openrouter_id": model_id, "reasoning": {"effort": illegal}}
    with pytest.raises(ValueError, match="unsupported reasoning effort"):
        resolve_provider_profile(bad)


def test_registry_provider_block_is_refused_when_a_profile_pins_the_endpoint():
    """One owner for the endpoint: the profile (decision D3).

    ``AppendAgent._body`` rebuilds the request's provider block from the profile
    and DISCARDS the registry's, so the two could disagree with the profile
    winning silently — gemma-4-31b shipped ``provider: {sort: throughput}``
    against a profile pinning ``deepinfra/turbo``. Refuse rather than pick.

    Injected onto ``_llm_resolved`` rather than by editing models.yaml, so this
    keeps biting after the registry is cleaned (it now is). Both directions: the
    same config resolves fine without the block, and an UNPROFILED model — whose
    only routing statement IS the registry block — must still be allowed one.
    """
    profiled = list(yaml.safe_load(PROFILE_PATH.read_text())["profiles"])
    config = load_config(str(ROOT / "configs/config-5.0.yaml"), llm_alias=profiled[0])
    assert config["_provider_profile"]["endpoint"]  # without the trigger: fine

    with_block = deepcopy(config)
    with_block.pop("_provider_profile")
    with_block["_llm_resolved"] = {
        **(with_block.get("_llm_resolved") or {}),
        "provider": {"only": ["some-endpoint"]},
    }
    with pytest.raises(ValueError, match="provider.*block"):
        resolve_provider_profile(with_block)

    ok = load_config(
        str(ROOT / "configs/config-5.0.yaml"), llm_alias=UNPROFILED_RAW_ID
    )
    ok.pop("_provider_profile")
    ok["_llm_resolved"] = {
        "openrouter_id": UNPROFILED_RAW_ID,
        "provider": {"only": ["some-endpoint"]},
    }
    resolve_provider_profile(ok)
    assert ok["_provider_profile"]["endpoint"] == ""


def test_no_registry_model_still_carries_its_own_provider_block():
    """The committed registry must satisfy the validator above.

    The validator is only useful if the data obeys it — a guard nothing can
    trip is indistinguishable from no guard. Every profiled registry entry
    therefore has to have had its ``provider:`` block removed.
    """
    profiled = set(yaml.safe_load(PROFILE_PATH.read_text())["profiles"])
    offenders = [
        base for base, entry in _registry().items()
        if isinstance(entry, dict)
        and entry.get("provider")
        and entry.get("openrouter_id") in profiled
    ]
    assert not offenders, (
        "configs/models.yaml still sets `provider:` for profiled model(s) "
        f"{offenders} — the profile owns the endpoint, so an append run on any "
        "of them raises at load_config()"
    )


def test_registry_thinking_levels_are_a_subset_of_profile_reasoning_efforts():
    """Every level the picker OFFERS must be one the profile says is legal.

    The registry's ``thinking_levels`` is the display list (it carries the
    observed per-level telemetry); the profile's ``reasoning_efforts`` is the
    probed legality list for that endpoint. A level in the first but not the
    second is a dialog entry that raises ``ValueError`` inside
    ``build_run_config`` AFTER the queue card went active — the "card flashes
    running, then vanishes" failure. kimi-k3 shipped exactly that:
    ``[max, xhigh, high, medium, low, none]`` against ``[max, high, low]``.

    Scoped to ``reasoning_type: effort``. A ``binary`` model's levels are
    ``thinking``/``non-thinking``, which are not efforts at all — they resolve
    to ``reasoning: {enabled: bool}`` and never reach the effort check — and a
    ``none`` model has no levels. Asserting a subset for those would be a claim
    about the wrong axis.

    The non-empty assertion is load-bearing: an empty ``reasoning_efforts``
    waves every effort through (see the unprofiled test above), so a profiled
    effort-tiered model must never have one.
    """
    catalog = yaml.safe_load(PROFILE_PATH.read_text())
    profiles = catalog["profiles"]
    default_efforts = catalog["defaults"].get("reasoning_efforts") or []
    checked = []
    for base, entry in _registry().items():
        if not isinstance(entry, dict):
            continue
        profile = profiles.get(entry.get("openrouter_id"))
        if profile is None or entry.get("reasoning_type") != "effort":
            continue
        levels = list(entry.get("thinking_levels") or [])
        efforts = list(profile.get("reasoning_efforts", default_efforts))
        checked.append(base)
        assert efforts, (
            f"{base}: profiled effort-tiered model with an EMPTY "
            "reasoning_efforts — the effort check would then accept anything"
        )
        extra = [lvl for lvl in levels if lvl not in efforts]
        assert not extra, (
            f"{base}: models.yaml offers thinking level(s) {extra} that "
            f"configs/provider-profiles.yaml does not list as legal for "
            f"{profile['endpoint']} (reasoning_efforts={efforts}). Either probe "
            "and add them to the profile, or drop them from the registry — a "
            "level in the picker that dispatch refuses is the 'card flashes "
            "then vanishes' failure."
        )
    assert checked, "no profiled effort-tiered model found — retarget this test"


def test_every_offered_level_actually_dispatches():
    """The executable form of the subset test, straight through the real loader.

    The subset test compares two YAML files; this one asserts the consequence —
    that every ``model(level)`` the dialog can offer resolves against config-5.0
    without raising. It is the assertion that would have caught
    ``kimi-k3(xhigh)`` regardless of how the two files were shaped.
    """
    from src.config import list_competitor_aliases

    failures = []
    for alias in list_competitor_aliases(_registry()):
        try:
            load_config(str(ROOT / "configs/config-5.0.yaml"), llm_alias=alias)
        except Exception as exc:  # noqa: BLE001 - the message is the report
            failures.append(f"{alias}: {exc}")
    assert not failures, "aliases the picker offers but config-5.0 refuses:\n" + "\n".join(failures)
