"""Load and validate the referee checkpoint ladder from a YAML file.

The ladder (e.g. ``configs/checkpoints-firered-v1.yaml``) is fully
data-driven: each checkpoint declares an id, display name, detector ``type``
(``map``/``flag``/``var``/``party``), a RAM ``signature``, an optional
``cross_check`` second signature, and a ``deadline_turn`` (int = enforced gate,
null = observed-only). Adding/renaming/re-limiting a gate is a config edit;
the referee code never changes. A different ladder file = a different
benchmark version.

This module owns only loading + validation. It does no memory reads.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Union

import yaml

# Per-type required signature fields. Both `signature` and `cross_check`
# (when present) are validated against this table by the checkpoint's
# (or cross_check's) declared type.
_REQUIRED_SIGNATURE_FIELDS: dict[str, tuple[str, ...]] = {
    "map": ("map_group", "map_num"),
    "flag": ("flag_id",),
    "var": ("var_id", "min_value"),
    "party": ("min_count",),
}

VALID_TYPES = frozenset(_REQUIRED_SIGNATURE_FIELDS)

# Signature fields that may be authored as a "0x.." hex string and should be
# parsed to int.
_INT_OR_HEX_FIELDS = frozenset({"flag_id", "var_id"})


@dataclass
class Checkpoint:
    """One rung of the checkpoint ladder.

    ``deadline_turn`` is ``None`` for observed-only gates (stamped + scored,
    never terminates a run). ``cross_check`` is an optional second signature
    logged for diagnostics; it never decides anything.
    """

    id: str
    name: str
    type: str
    signature: dict[str, Any]
    deadline_turn: Optional[int]
    cross_check: Optional[dict[str, Any]] = None


@dataclass
class CheckpointLadder:
    """The full ladder plus its benchmark metadata.

    ``checkpoints`` preserves the order written in the YAML.
    """

    benchmark_version: str
    game: str
    rom_sha1: dict[str, str]
    checkpoints: list[Checkpoint] = field(default_factory=list)


def _parse_int_or_hex(value: Any, *, field_name: str, ctx: str) -> int:
    """Parse a value that may be an int or a ``0x..`` hex string into an int."""
    if isinstance(value, bool):
        raise ValueError(f"{ctx}: {field_name} must be an int or hex string, got bool")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value, 0)
        except ValueError as exc:
            raise ValueError(
                f"{ctx}: {field_name}={value!r} is not a valid int or hex string"
            ) from exc
    raise ValueError(
        f"{ctx}: {field_name} must be an int or hex string, got {type(value).__name__}"
    )


def _validate_signature(
    sig: Any, *, sig_type: str, ctx: str
) -> dict[str, Any]:
    """Validate a signature (or cross_check) dict for a given detector type.

    Returns a normalised copy with hex string ids parsed to int. Raises
    ``ValueError`` with a clear message on any violation.
    """
    if not isinstance(sig, dict):
        raise ValueError(
            f"{ctx}: signature must be a dict, got {type(sig).__name__}"
        )
    if sig_type not in VALID_TYPES:
        raise ValueError(
            f"{ctx}: unknown type {sig_type!r} "
            f"(expected one of {sorted(VALID_TYPES)})"
        )

    required = _REQUIRED_SIGNATURE_FIELDS[sig_type]
    missing = [f for f in required if f not in sig]
    if missing:
        raise ValueError(
            f"{ctx}: type {sig_type!r} signature missing required field(s) "
            f"{missing} (have {sorted(sig)})"
        )

    normalised = dict(sig)
    for f in _INT_OR_HEX_FIELDS:
        if f in normalised:
            normalised[f] = _parse_int_or_hex(normalised[f], field_name=f, ctx=ctx)
    return normalised


def _parse_checkpoint(raw: Any, *, index: int) -> Checkpoint:
    if not isinstance(raw, dict):
        raise ValueError(
            f"checkpoint #{index}: must be a dict, got {type(raw).__name__}"
        )

    cp_id = raw.get("id")
    if not isinstance(cp_id, str) or not cp_id:
        raise ValueError(f"checkpoint #{index}: missing or invalid 'id'")
    ctx = f"checkpoint {cp_id!r}"

    name = raw.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError(f"{ctx}: missing or invalid 'name'")

    cp_type = raw.get("type")
    if cp_type not in VALID_TYPES:
        raise ValueError(
            f"{ctx}: unknown type {cp_type!r} "
            f"(expected one of {sorted(VALID_TYPES)})"
        )

    signature = _validate_signature(
        raw.get("signature"), sig_type=cp_type, ctx=ctx
    )

    deadline_turn = raw.get("deadline_turn")
    if deadline_turn is not None and (
        not isinstance(deadline_turn, int) or isinstance(deadline_turn, bool)
    ):
        raise ValueError(
            f"{ctx}: deadline_turn must be an int or null, got {deadline_turn!r}"
        )

    cross_check = raw.get("cross_check")
    if cross_check is not None:
        if not isinstance(cross_check, dict):
            raise ValueError(
                f"{ctx}: cross_check must be a dict, got {type(cross_check).__name__}"
            )
        cc_type = cross_check.get("type")
        if cc_type not in VALID_TYPES:
            raise ValueError(
                f"{ctx}: cross_check has unknown type {cc_type!r} "
                f"(expected one of {sorted(VALID_TYPES)})"
            )
        # The cross_check carries its own `type` field alongside the signature
        # fields; validate the signature fields for that type.
        validated = _validate_signature(
            cross_check, sig_type=cc_type, ctx=f"{ctx} cross_check"
        )
        cross_check = validated

    return Checkpoint(
        id=cp_id,
        name=name,
        type=cp_type,
        signature=signature,
        deadline_turn=deadline_turn,
        cross_check=cross_check,
    )


def load_ladder(path: Union[str, Path]) -> CheckpointLadder:
    """Load + validate the full checkpoint ladder (metadata + checkpoints).

    Validation (raises ``ValueError`` on violation):
      - list order is preserved as written;
      - per-type required signature fields present;
      - unknown ``type`` rejected;
      - duplicate ``id`` rejected;
      - ``deadline_turn`` is int or None;
      - ``cross_check`` (when present) validated by the same per-type rules.

    ``flag_id`` / ``var_id`` are parsed from int or ``0x..`` string into int.
    """
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Checkpoint ladder not found: {config_path}")

    with open(config_path) as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ValueError(
            f"{config_path.name}: top level must be a mapping, "
            f"got {type(data).__name__}"
        )

    benchmark_version = data.get("benchmark_version")
    if not isinstance(benchmark_version, str) or not benchmark_version:
        raise ValueError(f"{config_path.name}: missing 'benchmark_version'")

    game = data.get("game")
    if not isinstance(game, str) or not game:
        raise ValueError(f"{config_path.name}: missing 'game'")

    rom_sha1 = data.get("rom_sha1")
    if not isinstance(rom_sha1, dict) or not rom_sha1:
        raise ValueError(f"{config_path.name}: missing or invalid 'rom_sha1'")

    raw_checkpoints = data.get("checkpoints")
    if not isinstance(raw_checkpoints, list) or not raw_checkpoints:
        raise ValueError(
            f"{config_path.name}: 'checkpoints' must be a non-empty list"
        )

    checkpoints: list[Checkpoint] = []
    seen_ids: set[str] = set()
    for i, raw in enumerate(raw_checkpoints):
        cp = _parse_checkpoint(raw, index=i)
        if cp.id in seen_ids:
            raise ValueError(
                f"{config_path.name}: duplicate checkpoint id {cp.id!r}"
            )
        seen_ids.add(cp.id)
        checkpoints.append(cp)

    return CheckpointLadder(
        benchmark_version=benchmark_version,
        game=game,
        rom_sha1=rom_sha1,
        checkpoints=checkpoints,
    )


def load_checkpoints(path: Union[str, Path]) -> list[Checkpoint]:
    """Load just the ordered list of checkpoints (see ``load_ladder``)."""
    return load_ladder(path).checkpoints
