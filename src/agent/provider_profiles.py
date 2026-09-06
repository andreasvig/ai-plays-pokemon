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
    if entry is None:
        return  # Existing/unlisted models keep their original transport contract.
    profile = deepcopy(catalog["defaults"])
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
    if effort and effort not in profile["reasoning_efforts"]:
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
    if not isinstance(profile["endpoint"], str) or not profile["endpoint"]:
        raise ValueError("Provider profile requires one endpoint")
    for key in ("context_length", "max_completion_tokens"):
        if type(profile[key]) is not int or profile[key] <= 0:
            raise ValueError(f"Provider profile requires positive {key}")
    # No harness-side budgets on profiled models (decision 2026-09-06): output may
    # run to the endpoint's own ceiling and the early-compaction context limit is
    # the endpoint's context length. An explicit per-alias max_tokens still applies
    # if set, but may not exceed the ceiling.
    if resolved.get("max_tokens", 0) > profile["max_completion_tokens"]:
        raise ValueError(f"max_tokens {resolved['max_tokens']} exceeds {profile['endpoint']} limit {profile['max_completion_tokens']}")
    config["transport"]["max_output_tokens"] = profile["max_completion_tokens"]
    config["compaction"]["max_output_tokens"] = profile["max_completion_tokens"]
    config["compaction"]["context_token_limit"] = profile["context_length"]
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
