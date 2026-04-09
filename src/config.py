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


def _parse_version(filename: str) -> Optional[Tuple[int, int]]:
    """Extract (major, minor) from a config filename like 'config-1.2.yaml'."""
    m = re.match(r"config-(\d+)\.(\d+)\.yaml$", filename)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    return None


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

    _validate_config(config)
    return config


def _validate_config(config: dict[str, Any]) -> None:
    """Validate required config fields exist."""
    required = [
        "top_level_task",
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

    emu = config.get("emulator", {})
    for key in ("host", "port", "rom_path"):
        if key not in emu:
            raise ValueError(f"Missing emulator config: emulator.{key}")
