"""Append-agent provider contracts. Resolved profiles travel with saved run config."""
from copy import deepcopy
from pathlib import Path
import re

import yaml


PROFILE_PATH = Path(__file__).resolve().parents[2] / "configs/provider-profiles.yaml"
ENDPOINTS_URL = "https://openrouter.ai/api/v1/models/{model}/endpoints"


async def fetch_endpoint_pricing(model, api_key, timeout=20):
    """Snapshot every serving endpoint's list prices for ``model``.

    Persisted once per run dir so the trace can back out a cache discount from
    the billed prompt cost even when the provider's usage block omits cache
    counts (Google AI Studio reports 0 cached tokens unconditionally).
    """
    import httpx
    from datetime import datetime
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(ENDPOINTS_URL.format(model=model),
                                    headers={"Authorization": f"Bearer {api_key}"} if api_key else {})
        response.raise_for_status()
        data = response.json().get("data") or {}
    return {"model": model, "fetched_at": datetime.now().isoformat(), "endpoints": [
        {"name": e.get("name"), "tag": e.get("tag"), "provider_name": e.get("provider_name"),
         "context_length": e.get("context_length"), "pricing": e.get("pricing") or {}}
        for e in data.get("endpoints") or []]}


def output_json_text(content, profile):
    """Accept one complete JSON fence when explicitly configured; never repair JSON."""
    if profile.get("allow_json_fence"):
        match = re.fullmatch(r"\s*```(?:json)?\s*\n(.*?)\n```\s*", content, re.DOTALL)
        if match:
            return match[1]
    return content


def resolve_provider_profile(config):
    settings = config.get("provider_profiles", {})
    if not isinstance(settings, dict):
        raise ValueError("provider_profiles must be a mapping")
    if config.get("agent_type") != "append_compact" or not settings.get("enabled", False):
        if settings.get("name"):
            raise ValueError("Named provider profiles require append_compact with provider_profiles.enabled")
        return
    path = Path(settings.get("path", PROFILE_PATH))
    if not path.is_absolute():
        path = Path(config["_config_path"]).resolve().parent / path
    catalog = yaml.safe_load(path.read_text())
    model = config.get("llm_model")
    if not model:
        return  # Emulator/snapshot commands can load settings without a model.
    entry = catalog["profiles"].get(model)
    name = settings.get("name")
    variant = None
    if name:
        variant = catalog.get("variants", {}).get(name)
        if variant is None:
            raise ValueError(f"Unknown provider profile {name!r}; available: {', '.join(catalog.get('variants', {}))}")
        if variant["model"] != model:
            raise ValueError(f"Provider profile {name!r} requires model {variant['model']}, got {model}")
    # UNPROFILED models still get the `defaults:` block (decision 2026-09-07,
    # finding #9). The early return that used to sit here made `defaults:`
    # unreachable for 37 of the 43 registry models, so an append run on one of
    # them silently lost `transforms: []` (OpenRouter's middle-out compression
    # is then free to truncate the very history our compaction owns), the
    # cache-mode contract, `final_turn_text_only`, and the provider/model-drift
    # guard — while looking identical to a probed run in the queue card, the run
    # name and History.
    #
    # What an unprofiled model does NOT get is a fabricated ENDPOINT. A profile
    # entry exists because someone probed one specific serving endpoint; a
    # default cannot invent that. So `endpoint` stays "" (empty = unpinned) and
    # the request keeps whatever routing the REGISTRY asks for — including a
    # registry `provider:` block, which is the one legitimate place an
    # unprofiled model's routing can be expressed.
    unprofiled = entry is None
    profile = deepcopy(catalog["defaults"])
    if unprofiled:
        resolved_registry = config.get("_llm_resolved") or {}
        # Unpinned. `_body` omits `provider.only` on an empty endpoint.
        profile["endpoint"] = ""
        # Registry-derived, because these are the two settings the collapsed
        # registry genuinely owns per model and `defaults:` cannot: forcing the
        # default `output_mode: tool` onto a model whose registry entry says
        # `prompted` would break it on the first request.
        if resolved_registry.get("output_mode"):
            profile["output_mode"] = resolved_registry["output_mode"]
        sampling = {
            k: resolved_registry[k]
            for k in ("temperature", "top_p")
            if resolved_registry.get(k) is not None
        }
        if sampling:
            profile["sampling"] = sampling
        # Descriptive only for an unprofiled model: these are the config's OWN
        # authored budgets, recorded on the profile so the saved run config and
        # the trace say what the run actually ran under. The write-back at the
        # bottom is skipped, so the config is not rewritten from itself.
        profile["context_length"] = config["compaction"]["context_token_limit"]
        profile["max_completion_tokens"] = config["transport"]["max_output_tokens"]
    else:
        profile.update(deepcopy(entry))
    if variant:
        profile.update(deepcopy(variant["settings"]))
    overrides = settings.get("overrides", {})
    if not isinstance(overrides, dict) or set(overrides) - set(profile):
        raise ValueError("provider_profiles.overrides contains unknown fields or is not a mapping")
    profile.update(deepcopy(overrides))
    if name:
        profile["name"] = name
    profile["model"] = model
    profile["version"] = catalog["version"]
    profile["reviewed"] = catalog["reviewed"]
    resolved = config.get("_llm_resolved") or {}
    reasoning = deepcopy(resolved.get("reasoning") or config.get("thinking") or profile["reasoning_default"])
    effort = reasoning.get("effort")
    # `reasoning_efforts` is the PROBED legality list for one endpoint. An empty
    # list means "not probed" (it is the `defaults:` value), which every
    # unprofiled model now inherits — so checking against it would reject every
    # effort-tiered model in the registry, not just the illegal combinations.
    # Absence of evidence is not evidence of illegality, so an empty list waves
    # the request through. That is not a hole for PROFILED models: a companion
    # test asserts every profiled effort-tiered model lists a non-empty
    # `reasoning_efforts` that covers its registry `thinking_levels`, so an
    # empty list can never mean "profiled but unchecked".
    if effort and profile["reasoning_efforts"] and effort not in profile["reasoning_efforts"]:
        raise ValueError(f"{model}: unsupported reasoning effort {effort!r}; supported: {profile['reasoning_efforts']}")
    if profile["reasoning_mandatory"] and (reasoning.get("enabled") is False or effort == "none"):
        raise ValueError(f"{model}: reasoning cannot be disabled on this profile")
    if reasoning.get("exclude"):
        raise ValueError("Append provider profiles require returned reasoning (exclude must be false)")
    reasoning["exclude"] = False
    profile["reasoning"] = reasoning
    if profile["output_mode"] not in ("tool", "native_json", "prompted"):
        raise ValueError("Invalid provider profile output_mode")
    if profile["tool_choice"] not in ("auto", "required", "named", "omit"):
        raise ValueError("Invalid provider profile tool_choice")
    if profile["reasoning_replay"] not in ("all", "omit_prior"):
        raise ValueError("Invalid provider profile reasoning_replay")
    if profile["cache_mode"] not in ("implicit", "automatic", "system_breakpoint", "unknown"):
        raise ValueError("Invalid provider profile cache_mode")
    if profile["memory_encoding"] not in ("object", "json_string"):
        raise ValueError("Invalid provider profile memory_encoding")
    if not isinstance(profile.get("final_turn_text_only", False), bool):
        raise ValueError("Provider profile final_turn_text_only must be true or false")
    if not isinstance(profile["endpoint"], str):
        raise ValueError("Provider profile endpoint must be a string")
    if not unprofiled and not profile["endpoint"]:
        raise ValueError("Provider profile requires one endpoint")
    # THE PROFILE OWNS THE ENDPOINT (decision D3, 2026-09-07). When a profile
    # pins one, a registry `provider:` block for the same model is a SECOND
    # declaration of the same fact in a file nobody consults on this path:
    # `AppendAgent._body` rebuilds the request's provider block from the profile
    # and discards the registry's, so a disagreement (gemma-4-31b's registry
    # `sort: throughput` vs its profile's `deepinfra/turbo` tag) resolved
    # silently in the profile's favour with nothing saying so. Refuse instead of
    # picking a winner. Only the append path reaches here, so a legacy run on
    # the same model keeps reading its registry block unchanged.
    if not unprofiled and (config.get("_llm_resolved") or {}).get("provider") is not None:
        raise ValueError(
            f"{model}: configs/models.yaml sets a `provider:` block for a model whose "
            f"provider profile already pins endpoint {profile['endpoint']!r}. The profile "
            "owns the endpoint on the append path — delete the registry `provider:` block "
            "for this model, or drop its profile entry."
        )
    for key in ("context_length", "max_completion_tokens"):
        if type(profile[key]) is not int or profile[key] <= 0:
            raise ValueError(f"Provider profile requires positive {key}")
    # No harness-side budgets on profiled models (decision 2026-09-06): output may
    # run to the endpoint's own ceiling and the early-compaction context limit is
    # the endpoint's context length. An explicit per-alias max_tokens still applies
    # if set, but may not exceed the ceiling.
    if resolved.get("max_tokens", 0) > profile["max_completion_tokens"]:
        raise ValueError(f"max_tokens {resolved['max_tokens']} exceeds {profile['endpoint'] or 'the configured'} limit {profile['max_completion_tokens']}")
    # Only a PROBED profile may raise the config's budgets: the numbers come from
    # the endpoint's own advertised ceilings. An unprofiled model has no measured
    # ceiling, so its config keeps the conservative authored values and this
    # write-back is skipped — writing the config's own numbers back would also
    # collapse `compaction.max_output_tokens` onto `transport.max_output_tokens`,
    # which are deliberately different (12288 vs 8192 in config-5.0).
    if not unprofiled:
        config["transport"]["max_output_tokens"] = profile["max_completion_tokens"]
        config["compaction"]["max_output_tokens"] = profile["max_completion_tokens"]
        config["compaction"]["context_token_limit"] = profile["context_length"]
    # Recorded so the trace, the saved run config and any later audit can tell a
    # probed contract from a defaults-only one without re-deriving it.
    profile["unprofiled"] = unprofiled
    config["_provider_profile"] = profile


def project_history(messages, profile):
    """Deterministic wire projection; the original response archive stays lossless.

    Gemma's ordinary user turns omit previous raw thoughts. This agent always
    completes its structured action call before the next observation/user turn.
    A future multi-step tool loop would need to retain its current-turn thoughts.
    """
    result = deepcopy(messages)
    if profile.get("reasoning_replay") == "omit_prior":
        for message in result:
            if message.get("role") != "assistant":
                continue
            for key in ("reasoning", "reasoning_content", "reasoning_details"):
                message.pop(key, None)
            if isinstance(message.get("content"), list):
                message["content"] = [b for b in message["content"] if b.get("type") not in
                                      ("thinking", "redacted_thinking", "reasoning", "reasoning.text", "reasoning.encrypted")]
            fields = message.get("provider_specific_fields")
            if isinstance(fields, dict):
                for key in ("reasoning", "reasoning_content", "reasoning_details", "thought_signature", "signature"):
                    fields.pop(key, None)
                if not fields:
                    message.pop("provider_specific_fields")
    if profile.get("cache_mode") == "system_breakpoint" and result:
        content = result[0]["content"]
        if isinstance(content, str):
            result[0]["content"] = [{"type": "text", "text": content, "cache_control": deepcopy(profile["cache_control"])}]
    return result


def router_diagnostics(raw):
    metadata = raw.get("openrouter_metadata") or {}
    pipeline = metadata.get("pipeline")
    old_feedback = raw.get("input_transformations")
    transformations = (pipeline or []) + (old_feedback or [])
    destructive = any(isinstance(item, dict) and any(word in str(item.get("type", "")).lower()
                      for word in ("compress", "drop", "truncat")) for item in transformations)
    # Edge region is NOT the serving endpoint identity. Keep all metadata for audit.
    selected = [e for e in metadata.get("endpoints", {}).get("available", []) if e.get("selected")]
    return {"router_metadata": metadata or None, "selected_endpoints": selected,
            "input_transformations": pipeline if pipeline is not None else old_feedback,
            "provider_feedback": "dropped" if destructive else "not_reported"}, destructive
