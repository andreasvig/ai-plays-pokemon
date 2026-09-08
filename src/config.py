"""Load and validate config from the configs/ folder.

Naming convention: configs/config-X.Y.yaml (e.g. config-1.0.yaml, config-1.1.yaml)
By default, the latest version (highest X.Y) is loaded automatically.
Pass a specific path to load_config() to override.
"""

import os
import re
from pathlib import Path
from typing import Any, Optional, Tuple

import yaml
from dotenv import load_dotenv

CONFIGS_DIR = Path(__file__).parent.parent / "configs"
MODELS_REGISTRY_PATH = CONFIGS_DIR / "models.yaml"


def _parse_version(filename: str) -> Optional[Tuple[int, int]]:
    """Extract (major, minor) from a config filename like 'config-1.2.yaml'."""
    m = re.match(r"config-(\d+)\.(\d+)\.yaml$", filename)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    return None


def _load_models_registry() -> dict[str, Any]:
    if not MODELS_REGISTRY_PATH.exists():
        return {}
    with open(MODELS_REGISTRY_PATH) as f:
        return yaml.safe_load(f) or {}


def _is_raw_model_id(name: str) -> bool:
    # OpenRouter IDs always contain a provider/model slash.
    return "/" in name


# --- Collapsed-registry helpers (2026-06-17) --------------------------------
# The registry stores ONE record per model with a `thinking_levels` axis; the
# run identity is still "model(level)" (e.g. gpt-5.5(high)), so each level
# benchmarks separately. These helpers parse/resolve that identity and are
# shared by config resolution, the CLI, the catalog API, and request validation.

_ALIAS_RE = re.compile(r"^(.*?)\(([^)]+)\)\s*$")


def parse_model_alias(alias: str) -> Tuple[str, Optional[str]]:
    """Split ``"model(level)"`` → ``("model", "level")``; ``"model"`` → ``("model", None)``."""
    m = _ALIAS_RE.match(alias or "")
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return (alias or "").strip(), None


def model_thinking_levels(entry: dict[str, Any]) -> list[str]:
    """Ordered thinking levels for a collapsed model entry (highest first; [] for type none)."""
    return list(entry.get("thinking_levels") or [])


def model_default_level(entry: dict[str, Any]) -> Optional[str]:
    """Default (highest) level — first in the list, or None for reasoning_type none."""
    levels = model_thinking_levels(entry)
    return levels[0] if levels else None


def _reasoning_for_level(entry: dict[str, Any], level: Optional[str]) -> Optional[dict]:
    """Derive the OpenRouter reasoning block for a (model, level) from reasoning_type."""
    rtype = entry.get("reasoning_type", "none")
    if rtype == "none":
        return None
    if rtype == "effort":
        if level is None:
            return None
        block: dict[str, Any] = {"effort": level}
        if entry.get("reasoning_summary"):
            block["summary"] = entry["reasoning_summary"]
        return block
    if rtype == "binary":
        return {"enabled": level == "thinking"}
    raise ValueError(f"Unknown reasoning_type {rtype!r} in registry entry")


def _slow_for_level(entry: dict[str, Any], level: Optional[str]) -> bool:
    """Resolve the per-(model,level) slow flag (bool → all levels; dict → per level)."""
    slow = entry.get("slow")
    if slow is True:
        return True
    if isinstance(slow, dict):
        return bool(slow.get(level))
    return False


def resolve_model_selection(alias: str, registry: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct the flat per-(model,level) entry from the collapsed registry.

    ``alias`` is ``"model"`` (→ default/highest level) or ``"model(level)"``.
    Returns a dict shaped like the old per-level entry — openrouter_id, reasoning
    (block or None), output_mode/temperature/top_p/max_tokens/provider/fallbacks
    (when set), a resolved boolean ``slow`` — plus ``_alias`` (canonical
    ``model(level)``), ``_base`` and ``_level``. Raises ValueError on an unknown
    model or an invalid level.
    """
    base, level = parse_model_alias(alias)
    entry = registry.get(base)
    if entry is None or not isinstance(entry, dict):
        known = ", ".join(sorted(registry)) or "(registry empty)"
        raise ValueError(
            f"Unknown model {base!r}. Known models: {known}. "
            f"Either add it to {MODELS_REGISTRY_PATH.name} or use a raw 'provider/model' id."
        )
    openrouter_id = entry.get("openrouter_id")
    if not openrouter_id:
        raise ValueError(f"Registry entry {base!r} is missing required field 'openrouter_id'")

    levels = model_thinking_levels(entry)
    if levels:
        if level is None:
            level = levels[0]  # default = highest
        elif level not in levels:
            raise ValueError(
                f"Unknown thinking level {level!r} for {base!r}. "
                f"Valid levels: {', '.join(levels)}."
            )
    elif level is not None:
        raise ValueError(
            f"Model {base!r} has no thinking levels (always-on); got level {level!r}."
        )

    canonical = f"{base}({level})" if level is not None else base
    resolved: dict[str, Any] = {
        "openrouter_id": openrouter_id,
        "reasoning": _reasoning_for_level(entry, level),
        "slow": _slow_for_level(entry, level),
        "_alias": canonical,
        "_base": base,
        "_level": level,
    }
    for key in ("output_mode", "temperature", "top_p", "max_tokens", "provider", "fallbacks"):
        if key in entry:
            resolved[key] = entry[key]
    return resolved


def _alias_to_openrouter_id(alias: str, registry: dict[str, Any]) -> str:
    """Slug for a fallback alias: raw ids pass through, else resolve via registry."""
    if _is_raw_model_id(alias):
        return alias
    return resolve_model_selection(alias, registry)["openrouter_id"]


def list_competitor_aliases(registry: dict[str, Any]) -> list[str]:
    """Every benchmarkable ``model(level)`` identity (bare ``model`` for type none)."""
    out: list[str] = []
    for base in sorted(registry):
        entry = registry[base]
        if not isinstance(entry, dict):
            continue
        levels = model_thinking_levels(entry)
        if levels:
            out.extend(f"{base}({lvl})" for lvl in levels)
        else:
            out.append(base)
    return out


def is_valid_model_selection(alias: str, registry: dict[str, Any]) -> bool:
    """True if ``alias`` resolves to a known model + valid level (or is a raw id)."""
    if _is_raw_model_id(alias):
        return True
    try:
        resolve_model_selection(alias, registry)
        return True
    except ValueError:
        return False


def _resolve_llm_alias(config: dict[str, Any], registry: dict[str, Any]) -> None:
    """Resolve config['llm_model'] against the collapsed registry, in place.

    If the value is a raw OpenRouter ID (contains '/'), leave it alone. Otherwise
    parse it as ``model`` / ``model(level)``, reconstruct the per-level settings,
    and expand into llm_model + thinking + llm_fallback_models, stashing the
    canonical alias + resolved entry for logging.
    """
    name = config.get("llm_model", "")
    if not name or _is_raw_model_id(name):
        return

    resolved = resolve_model_selection(name, registry)  # raises ValueError if unknown
    config["_llm_alias"] = resolved["_alias"]
    config["_llm_resolved"] = resolved
    config["llm_model"] = resolved["openrouter_id"]
    # Registry is the source of truth for thinking when using an alias (None for
    # reasoning_type none → no reasoning param sent).
    config["thinking"] = resolved.get("reasoning")

    # Fallbacks: only apply registry default if the main config didn't set its own.
    if not config.get("llm_fallback_models"):
        fallbacks = resolved.get("fallbacks", []) or []
        config["llm_fallback_models"] = [
            _alias_to_openrouter_id(fb, registry) for fb in fallbacks
        ]


def find_latest_config() -> Path:
    """Find the config with the highest version number in configs/."""
    if not CONFIGS_DIR.is_dir():
        raise FileNotFoundError(f"Configs directory not found: {CONFIGS_DIR}")

    configs = []
    for f in CONFIGS_DIR.iterdir():
        ver = _parse_version(f.name)
        if ver is not None:
            configs.append((ver, f))

    if not configs:
        raise FileNotFoundError(f"No config-X.Y.yaml files found in {CONFIGS_DIR}")

    configs.sort(key=lambda x: x[0])
    return configs[-1][1]


def default_config_stem() -> str:
    """The config stem a caller gets when it names none — e.g. ``config-5.0``.

    THE single source for "the casual default" in prose. Every help string, doc
    hint and error message that wants to *name* the default asks here instead of
    embedding a literal, which is how the old name (config-4.0) ended up written
    into eight places that then had to be found by grep. Derived from
    :func:`find_latest_config`, so it and ``catalog.list_configs()[-1]`` and
    ``server._validate_config_stem``'s fallback are the same value by
    construction, and adding a config-5.1 moves all of them at once.

    A function rather than a module constant on purpose: a constant would run
    ``find_latest_config`` at import time (so ``import src.config`` would raise
    in a checkout with no ``configs/``) and would then be stale for the rest of
    the process.
    """
    return find_latest_config().stem


def example_model_aliases(count: int = 2) -> list[str]:
    """``count`` REAL ``model(level)`` selections, for help strings and doc examples.

    The sibling of :func:`default_config_stem`, and it exists for the same
    reason: ``pokemon queue --help`` and ``pokemon run --help`` used to print
    hand-written aliases (``gemini-3.1-flash-lite``, ``grok-4.5(high)``,
    ``claude-haiku-4-5``), and the 2026-09-07 registry prune removed the models
    they named while the help kept advertising them — copy one and the command
    400s. Derived from the registry instead, so an example is a selection you
    can actually run.

    Deterministic and STABLE rather than "interesting": one alias per model in
    sorted order, each at its default (highest) level, taking the first
    ``count``. Help text that reshuffles on every registry edit would be its own
    kind of noise, and a sort is the only ordering that does not need a second
    opinion. Retired entries are excluded — they resolve but must not be offered
    (the same rule ``catalog.list_models`` applies).

    Returns fewer than ``count`` (possibly none) on an empty registry rather
    than raising: a broken registry must not take `--help` down with it.
    """
    try:
        registry = _load_models_registry()
    except (OSError, ValueError):
        return []
    out: list[str] = []
    for base in sorted(registry):
        entry = registry.get(base)
        if not isinstance(entry, dict) or entry.get("retired"):
            continue
        level = model_default_level(entry)
        out.append(f"{base}({level})" if level else base)
        if len(out) >= count:
            break
    return out


def _hoist_player_agent(config: dict[str, Any], config_path: Path) -> None:
    """Lift keys from an optional ``player_agent:`` block to the top level.

    The Player agent's settings can be authored either flat at the top level
    (the historical layout, still used by config-1.x..3.12) or grouped under a
    ``player_agent:`` mapping (the layout that mirrors ``task_master:``).
    Grouping is purely an authoring convenience: every key inside the block is
    hoisted to the top level here, before validation, so all downstream code
    keeps a single flat read path. Defining the same key both inside the block
    and at the top level is rejected rather than silently shadowed.
    """
    pa = config.get("player_agent")
    if pa is None:
        return
    if not isinstance(pa, dict):
        raise ValueError(
            f"{config_path.name}: player_agent must be a dict, "
            f"got {type(pa).__name__}"
        )
    for key, value in pa.items():
        if key in config:
            raise ValueError(
                f"{config_path.name}: {key!r} is set both at the top level and "
                "inside player_agent — define it in exactly one place."
            )
        config[key] = value
    del config["player_agent"]


def load_config(
    path: Optional[str] = None,
    *,
    llm_alias: Optional[str] = None,
    provider_profile: Optional[str] = None,
) -> dict[str, Any]:
    """Load config from YAML file and .env, return as dict.

    Model choice lives on the CLI, not in the config file. Caller must pass
    `llm_alias` (a name from configs/models.yaml, or a raw 'provider/model' id).
    Config files that still carry `llm_model` or `llm_fallback_models` are
    rejected — the registry entry settings get pulled in from models.yaml.

    Args:
        path: Explicit config file path. If None, auto-picks the latest
              config-X.Y.yaml from the configs/ directory.
        llm_alias: Required. The model alias (or raw provider/model id) to use.
        provider_profile: Optional named append-agent profile; overrides YAML selection.
    """
    load_dotenv()

    if path is None:
        config_path = find_latest_config()
    else:
        config_path = Path(path)

    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path) as f:
        config = yaml.safe_load(f)

    # player_agent: optional nested block grouping the Player agent's own
    # settings (prompts, context window, per-turn limits, tools) — the mirror of
    # the task_master block. Authored grouped in the YAML for readability; its
    # keys are lifted to the top level here, before any other processing, so the
    # rest of the code keeps a single flat read path and the older flat
    # config-1.x..3.12 files keep loading unchanged.
    _hoist_player_agent(config, config_path)
    if provider_profile is not None:
        settings = config.setdefault("provider_profiles", {})
        if not isinstance(settings, dict):
            raise ValueError("provider_profiles must be a mapping")
        settings["name"] = provider_profile

    # Model choice is a CLI flag, not a config field. Reject configs that
    # still carry it so old habits surface as a clear error instead of
    # silently shadowing whatever the CLI passed.
    forbidden = [k for k in ("llm_model", "llm_fallback_models") if k in config]
    if forbidden:
        raise ValueError(
            f"Config {config_path.name} contains forbidden key(s): {forbidden}. "
            "Model selection moved to the CLI — pass --model \"<alias>\" "
            "(or --models for sequential runs) instead. Strip these fields "
            "from the YAML."
        )

    # llm_alias is optional at load_config level so emulator/snapshot tools
    # that never touch the agent can load configs too. The agent entry point
    # (pokemon run) makes --model required at the CLI layer. When alias is
    # absent, skip the registry resolve — _validate_config will then enforce
    # that llm_model is present for any path that needs it.
    if llm_alias:
        config["llm_model"] = llm_alias

    # Inject API key from environment
    config["openrouter_api_key"] = os.environ.get("OPENROUTER_API_KEY", "")

    # Track which config file was loaded
    config["_config_path"] = str(config_path)

    if llm_alias:
        registry = _load_models_registry()
        _resolve_llm_alias(config, registry)

    from src.agent.provider_profiles import resolve_provider_profile
    _validate_append_config(config)
    resolve_provider_profile(config)
    _validate_config(config, require_llm_model=bool(llm_alias))
    return config


def _validate_config(config: dict[str, Any], *, require_llm_model: bool = True) -> None:
    """Validate required config fields exist.

    `require_llm_model` is False for non-agent callers (snapshot/emulator
    tools) that load configs purely for the emulator block. Agent paths
    always pass it as True via load_config(llm_alias=...).
    """
    _validate_append_config(config)
    required = [
        "task",
        "emulator",
        "valid_inputs",
        "state_file",
    ]
    if require_llm_model:
        required.append("llm_model")
    for key in required:
        if key not in config:
            raise ValueError(f"Missing required config key: {key}")

    # task_override_snapshot: when true, ignore the snapshot's tasks.json and
    # use the config's `task:` field instead. Default false preserves the
    # historical "snapshot wins" behavior. Useful when the snapshot was made
    # with one goal in mind but you want to re-purpose the same world state
    # for a different goal — without this flag the user-message goal silently
    # disagrees with the system prompt (which always uses config's task).
    tos = config.get("task_override_snapshot", False)
    if not isinstance(tos, bool):
        raise ValueError(
            f"task_override_snapshot must be a bool, got {tos!r}"
        )

    # task_master: optional block enabling the TaskMaster meta-agent. When the
    # block is ABSENT, TaskMaster is disabled and the harness keeps its single-
    # agent behavior unchanged. When present, validate its fields — `enabled`
    # (bool) toggles the meta-agent and `history_window_n` (int) bounds how many
    # recent task records the meta-agent sees. TaskMaster reuses the top-level
    # `task.goal` and `max_turns_per_task`; it adds no goal/turn fields of its own.
    # user_prompt: optional Player per-turn user-message template ({{value}}
    # placeholders). Omit to use the code default (DEFAULT_PLAYER_USER_PROMPT).
    up = config.get("user_prompt")
    if up is not None and (not isinstance(up, str) or not up.strip()):
        raise ValueError(f"user_prompt must be a non-empty string, got {up!r}")

    tm = config.get("task_master")
    if tm is not None:
        if not isinstance(tm, dict):
            raise ValueError(f"task_master must be a dict, got {type(tm).__name__}")
        enabled = tm.get("enabled", False)
        if not isinstance(enabled, bool):
            raise ValueError(
                f"task_master.enabled must be a bool, got {enabled!r}"
            )
        hwn = tm.get("history_window_n")
        if not isinstance(hwn, int) or isinstance(hwn, bool):
            raise ValueError(
                f"task_master.history_window_n must be an int, got {hwn!r}"
            )
        # search_model: optional Perplexity Sonar model the ask_perplexity tool
        # routes to. Omit to use the code default (perplexity/sonar-pro-search).
        sm = tm.get("search_model")
        if sm is not None and (not isinstance(sm, str) or not sm.strip()):
            raise ValueError(
                f"task_master.search_model must be a non-empty string, got {sm!r}"
            )
        tmm = tm.get("model")
        if tmm is not None and (not isinstance(tmm, str) or not tmm.strip()):
            raise ValueError(
                f"task_master.model must be a non-empty string, got {tmm!r}"
            )
        # system_prompt: optional TaskMaster system prompt. Omit to use the code
        # default (task_master.SYSTEM_PROMPT). Mirrors the Player's top-level
        # `system_prompt` so both agents' prompts live in the config.
        tsp = tm.get("system_prompt")
        if tsp is not None and (not isinstance(tsp, str) or not tsp.strip()):
            raise ValueError(
                f"task_master.system_prompt must be a non-empty string, got {tsp!r}"
            )
        # user_prompt / user_prompt_cold_start: optional TaskMaster user-message
        # templates ({{value}} placeholders). Omit to use the code defaults.
        for _k in ("user_prompt", "user_prompt_cold_start"):
            _v = tm.get(_k)
            if _v is not None and (not isinstance(_v, str) or not _v.strip()):
                raise ValueError(
                    f"task_master.{_k} must be a non-empty string, got {_v!r}"
                )
        # The per-task turn budget (reused top-level `max_turns_per_task`) is the
        # backstop that guarantees control returns to TaskMaster. With TaskMaster
        # enabled it must be a positive int — `0`/missing would disable both the
        # output validator and the run-loop backstop, letting a Player that never
        # volunteers a handoff run one unbounded task.
        if enabled:
            mtp = config.get("max_turns_per_task")
            if not isinstance(mtp, int) or isinstance(mtp, bool) or mtp < 1:
                raise ValueError(
                    "max_turns_per_task must be a positive int when "
                    f"task_master.enabled is true, got {mtp!r}"
                )

    # max_spend_usd: optional all-in USD ceiling for the run — the third casual
    # stop condition next to the turn cap and `stop_at`. Absent/None = unbounded
    # (and that is what official runs always get). Rejecting 0 is deliberate:
    # a zero budget can only ever produce an empty run, so it is a typo, not a
    # request. Floats are the point (`--max-spend 0.50`), so bools — which are
    # ints in Python — are excluded explicitly.
    msu = config.get("max_spend_usd")
    if msu is not None:
        if isinstance(msu, bool) or not isinstance(msu, (int, float)) or msu <= 0:
            raise ValueError(
                f"max_spend_usd must be a positive number of USD, got {msu!r}"
            )

    # historic_images_count / max_turns_before_trim: sliding-window keys of the
    # LEGACY agent — how many recent turns keep their start-of-turn screenshot
    # inline, and how many turns of text history survive before truncation.
    # 0 / None = text-only, untrimmed.
    #
    # Validated for the legacy harness ONLY. On ``agent_type: append_compact``
    # the conversation is append-only and bounded by compaction, and
    # ``TurnManager`` hands the turn to ``AppendAgent.play()`` before it ever
    # assembles a windowed message (turn.py:2098, ahead of the only two readers
    # at 2129 and 2612) — so neither key is read there. Checking them anyway
    # made config-5.0 carry two inert zeros just to satisfy a validator, which
    # reads as "this harness trims its history". It does not.
    if config.get("agent_type", "current") != "append_compact":
        hic = config.get("historic_images_count", 0)
        if not isinstance(hic, int) or isinstance(hic, bool) or hic < 0:
            raise ValueError(
                f"historic_images_count must be a non-negative int, got {hic!r}"
            )
        if hic > 0:
            trim = config.get("max_turns_before_trim")
            if trim is not None and hic > trim:
                raise ValueError(
                    f"historic_images_count ({hic}) cannot exceed max_turns_before_trim "
                    f"({trim}). A historic image only makes sense for turns that are "
                    "still visible in the text history."
                )

    sp = config.get("savepoints")
    if sp is not None:
        if not isinstance(sp, dict):
            raise ValueError(f"savepoints must be a dict, got {type(sp).__name__}")
        every = sp.get("every_n_turns", 0)
        if not isinstance(every, int) or isinstance(every, bool) or every < 0:
            raise ValueError(
                f"savepoints.every_n_turns must be a non-negative int, got {every!r}"
            )
        for key in ("at_end", "on_crash"):
            val = sp.get(key, False)
            if not isinstance(val, bool):
                raise ValueError(f"savepoints.{key} must be a bool, got {val!r}")

    emu = config.get("emulator", {})
    for key in ("host", "port", "rom_path"):
        if key not in emu:
            raise ValueError(f"Missing emulator config: emulator.{key}")


def _validate_append_config(config: dict[str, Any]) -> None:
    kind = config.get("agent_type", "current")
    if kind not in ("current", "append_compact"):
        raise ValueError(f"Unknown agent_type: {kind}")
    if kind != "append_compact":
        return
    if config.get("task_master", {}).get("enabled"):
        raise ValueError("append_compact is self-directed and cannot enable TaskMaster")
    # The four model-facing prompts of this harness, all required in YAML.
    # ``action_prompt`` is the fourth: on a split-turn profile
    # (``final_turn_text_only``) it is the text-only user message that ENDS every
    # gameplay request, so it is as load-bearing as the other three — it had a
    # code fallback and no declaration site, which made docs/append-agent.md's
    # "all prompts are authored in YAML" false. Required unconditionally rather
    # than only for split-turn profiles: which profile a run gets is decided by
    # the model, one layer down from the config, so a config that validates for
    # one model and silently falls back for another is the worse contract.
    for key in ("system_prompt", "user_prompt", "segment_start_prompt", "action_prompt"):
        if not isinstance(config.get(key), str) or not config[key].strip():
            raise ValueError(f"append_compact requires {key} in config")
    # `self_grade` (default true = config-5.0's graded output). When false the
    # gameplay schema has no last_turn_succeeded and the agent keeps no grades,
    # so a prompt that still asks for the field or renders {{recent_grades}}
    # would be lying to the model — refuse it here, where the prompt is edited.
    graded = config.get("self_grade", True)
    if type(graded) is not bool:
        raise ValueError("self_grade must be a boolean")
    if not graded:
        if "{{recent_grades}}" in config["segment_start_prompt"]:
            raise ValueError("self_grade is false but segment_start_prompt renders {{recent_grades}}")
        for key in ("system_prompt", "user_prompt", "action_prompt", "segment_start_prompt"):
            if "last_turn_succeeded" in config[key]:
                raise ValueError(f"self_grade is false but {key} mentions last_turn_succeeded")
    for section in ("compaction", "transport", "observability", "caching"):
        if not isinstance(config.get(section), dict):
            raise ValueError(f"append_compact requires a {section} mapping")
    for section, keys in (("compaction", ("every_n_turns", "context_token_limit", "summary_target_tokens", "max_output_tokens", "max_handover_chars")),
                          ("transport", ("timeout_seconds", "max_output_tokens"))):
        for key in keys:
            value = config[section].get(key)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{section}.{key} must be a positive integer")
    for section in ("compaction", "transport"):
        value = config[section].get("max_retries")
        if type(value) is not int or value < 0:
            raise ValueError(f"{section}.max_retries must be a non-negative integer")
    fraction = config["compaction"].get("context_limit_fraction")
    if isinstance(fraction, bool) or not isinstance(fraction, (int, float)) or not 0 < fraction < 1:
        raise ValueError("compaction.context_limit_fraction must be between 0 and 1")
    for section, key in (("compaction", "prompt"), ("transport", "prompted_output_prompt")):
        if not isinstance(config[section].get(key), str) or not config[section][key].strip():
            raise ValueError(f"{section}.{key} must be a non-empty prompt")
    if type(config["transport"].get("stream")) is not bool:
        raise ValueError("transport.stream must be a boolean")
    if config["observability"].get("on_reasoning_loss") not in ("stop", "continue"):
        raise ValueError("observability.on_reasoning_loss must be stop or continue")
    if config["compaction"]["max_output_tokens"] >= config["compaction"]["context_token_limit"]:
        raise ValueError("Compaction output budget must fit the configured context limit")
    reserve = config["compaction"].get("image_token_reserve", 4096)
    if type(reserve) is not int or reserve <= 0:
        raise ValueError("compaction.image_token_reserve must be a positive integer")
    cap = config["compaction"].get("max_prompt_tokens")
    if cap is not None and (type(cap) is not int or cap <= 0):
        raise ValueError("compaction.max_prompt_tokens must be a positive integer")
    cache = config["caching"]
    prefixes = cache.get("cache_control_by_model_prefix", {})
    if not isinstance(prefixes, dict) or any(not isinstance(k, str) or not k or not isinstance(v, dict) for k, v in prefixes.items()):
        raise ValueError("caching.cache_control_by_model_prefix must map model prefixes to control objects")
    if "cache_control" in cache and not isinstance(cache["cache_control"], dict):
        raise ValueError("caching.cache_control must be an object")
