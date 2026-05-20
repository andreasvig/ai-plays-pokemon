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


def _resolve_llm_alias(config: dict[str, Any], registry: dict[str, Any]) -> None:
    """Resolve config['llm_model'] against the registry, in place.

    If the value is a raw OpenRouter ID (contains '/'), leave it alone.
    Otherwise look it up, expand into llm_model + thinking + llm_fallback_models,
    and stash the original alias + full resolved entry for logging.
    """
    name = config.get("llm_model", "")
    if not name or _is_raw_model_id(name):
        return

    entry = registry.get(name)
    if entry is None:
        known = ", ".join(sorted(registry)) or "(registry empty)"
        raise ValueError(
            f"Unknown llm_model alias: {name!r}. Known aliases: {known}. "
            f"Either add it to {MODELS_REGISTRY_PATH.name} or use a raw 'provider/model' id."
        )

    openrouter_id = entry.get("openrouter_id")
    if not openrouter_id:
        raise ValueError(
            f"Registry entry {name!r} is missing required field 'openrouter_id'"
        )

    config["_llm_alias"] = name
    config["_llm_resolved"] = entry
    config["llm_model"] = openrouter_id

    # Registry is the source of truth for thinking when using an alias.
    if "reasoning" in entry:
        config["thinking"] = entry["reasoning"]

    # Fallbacks: only apply registry default if the main config didn't set its own.
    if not config.get("llm_fallback_models"):
        fallbacks = entry.get("fallbacks", []) or []
        resolved_fallbacks = []
        for fb in fallbacks:
            if _is_raw_model_id(fb):
                resolved_fallbacks.append(fb)
            else:
                fb_entry = registry.get(fb)
                if fb_entry is None or not fb_entry.get("openrouter_id"):
                    raise ValueError(
                        f"Fallback alias {fb!r} (from {name!r}) not found in registry"
                    )
                resolved_fallbacks.append(fb_entry["openrouter_id"])
        config["llm_fallback_models"] = resolved_fallbacks


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


def load_config(path: Optional[str] = None) -> dict[str, Any]:
    """Load config from YAML file and .env, return as dict.

    Args:
        path: Explicit config file path. If None, auto-picks the latest
              config-X.Y.yaml from the configs/ directory.
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

    # Inject API key from environment
    config["openrouter_api_key"] = os.environ.get("OPENROUTER_API_KEY", "")

    # Track which config file was loaded
    config["_config_path"] = str(config_path)

    # Resolve llm_model alias against the registry (no-op if already a raw id)
    registry = _load_models_registry()
    _resolve_llm_alias(config, registry)

    _validate_config(config)
    return config


def _validate_config(config: dict[str, Any]) -> None:
    """Validate required config fields exist."""
    required = [
        "task",
        "llm_model",
        "vision_mode",
        "emulator",
        "valid_inputs",
        "state_file",
    ]
    for key in required:
        if key not in config:
            raise ValueError(f"Missing required config key: {key}")

    if config["vision_mode"] not in ("separate_vlm", "direct_multimodal"):
        raise ValueError(
            f"Invalid vision_mode: {config['vision_mode']}. "
            "Must be 'separate_vlm' or 'direct_multimodal'"
        )

    if config["vision_mode"] == "separate_vlm" and not config.get("vlm_model"):
        raise ValueError("vlm_model is required when vision_mode is 'separate_vlm'")

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

    # historic_images_count: how many of the most recent visible turns should
    # have their start-of-turn screenshot included inline alongside the current
    # screenshot. 0 = text-only history (default).
    hic = config.get("historic_images_count", 0)
    if not isinstance(hic, int) or isinstance(hic, bool) or hic < 0:
        raise ValueError(
            f"historic_images_count must be a non-negative int, got {hic!r}"
        )
    if hic > 0:
        if config["vision_mode"] != "direct_multimodal":
            raise ValueError(
                "historic_images_count > 0 requires vision_mode: direct_multimodal "
                f"(got {config['vision_mode']!r}). In separate_vlm mode the LLM never "
                "sees raw images, so historic screenshots cannot be included."
            )
        trim = config.get("max_turns_before_trim")
        if trim is not None and hic > trim:
            raise ValueError(
                f"historic_images_count ({hic}) cannot exceed max_turns_before_trim "
                f"({trim}). A historic image only makes sense for turns that are "
                "still visible in the text history."
            )

    emu = config.get("emulator", {})
    for key in ("host", "port", "rom_path"):
        if key not in emu:
            raise ValueError(f"Missing emulator config: emulator.{key}")
