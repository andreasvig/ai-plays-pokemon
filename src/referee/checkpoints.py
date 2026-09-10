"""Load and validate the referee checkpoint ladder from a YAML file.

The ladder (e.g. ``configs/checkpoints-firered-v1.yaml``) is fully
data-driven: each checkpoint declares an id, display name, detector ``type``
(``map``/``flag``/``var``/``party``), a RAM ``signature``, an optional
``cross_check`` second signature, a ``deadline_turn`` (int = enforced gate,
null = observed-only) and an optional ``leg_cap_turns`` (int = the most turns
the run may spend on the leg INTO this gate, counted from the previous rung's
completion — a second, independent bound; see ``Checkpoint``). Adding/renaming/
re-limiting a gate is a config edit;
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
    never terminates a run) AND for any gate that is a member of a
    :class:`MultiGate` (its pacing comes from the group's ``deadline_turns``,
    not a per-gate limit). ``cross_check`` is an optional second signature
    logged for diagnostics; it never decides anything.

    ``locus`` is the gate's spatial target for the between-gate progress
    tracker (``src/referee/progress.py``): ``{map_group, map_num, tiles?,
    near?}`` where ``tiles`` are stand tiles and ``near`` are object/NPC tiles
    whose passable neighbours are the stand tiles. ``map``-type gates need none
    (the walk graph derives the map's entry tiles); a non-map gate without one
    simply has an unscored leg. It never decides a stamp.

    ``leg_cap_turns`` (PokeBench v1.1, 2026-09-09) bounds the LEG into this gate:
    the run is terminated when ``turn - <previous rung's completion turn> >=
    leg_cap_turns`` with the gate still unstamped. It is independent of
    ``deadline_turn`` — either alone makes the gate enforced, and the
    first-badge ladder carries caps only (2026-09-10: Andreas, "kill the old
    cumulative ladder fully") — and exists because
    cumulative deadlines let a fast opening bank headroom that one section (the
    Viridian Forest maze) then burns for hundreds of turns with no progress
    (Andreas: "an agent which has 320+ turns in Viridian Forest, but might
    still be too stupid to figure it out"). The leg has not started while the
    previous rung is incomplete, so a cap never fires on a rung the run has not
    reached. None = no per-leg bound. Not allowed on multigate members.
    """

    id: str
    name: str
    type: str
    signature: dict[str, Any]
    deadline_turn: Optional[int]
    cross_check: Optional[dict[str, Any]] = None
    locus: Optional[dict[str, Any]] = None
    leg_cap_turns: Optional[int] = None


@dataclass
class MultiGate:
    """A set of gates completable in ANY order, paced by a list of deadlines.

    The gates inside have no individual ``deadline_turn``; instead the group
    carries ``deadline_turns`` — a progressive pace ladder where the *k*-th
    completion among the members must occur by ``deadline_turns[k-1]``. So
    ``[875, 960]`` over two gates means: at least one of the two done by turn
    875, both done by turn 960. A ``None`` entry makes that completion-count
    observed-only (no deadline). ``len(deadline_turns) == len(gates)``.

    ``id``/``name`` are synthetic group identifiers (derived from the members
    unless authored). The group is one *rung* of the ladder for scoring; its
    members are still latched individually by the referee.
    """

    id: str
    name: str
    gates: list[Checkpoint]
    deadline_turns: list[Optional[int]]


# A ladder node is either a single gate or an any-order multigate.
Node = Union[Checkpoint, MultiGate]


@dataclass
class CheckpointLadder:
    """The full ladder plus its benchmark metadata.

    ``nodes`` is the ladder as authored: an ordered mix of single
    :class:`Checkpoint` rungs and :class:`MultiGate` rungs. ``checkpoints`` is
    the flattened, ordered list of every individual gate (multigate members
    expanded in place) — what the referee's per-gate latch iterates. Both
    preserve YAML order.
    """

    benchmark_version: str
    game: str
    rom_sha1: dict[str, str]
    checkpoints: list[Checkpoint] = field(default_factory=list)
    nodes: list[Node] = field(default_factory=list)


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


_LOCUS_KEYS = frozenset({"map_group", "map_num", "tiles", "near"})


def _validate_locus(raw: Any, *, ctx: str) -> dict[str, Any]:
    """Validate a gate ``locus`` block; returns a normalised copy.

    Shape: ``{map_group: int, map_num: int, tiles?: [[x, y], ...],
    near?: [[x, y], ...]}`` — ints only, pairs only, at least one non-empty
    tile list. Unknown keys are rejected so a typo ("tile") cannot silently
    leave a leg unscored.
    """
    if not isinstance(raw, dict):
        raise ValueError(f"{ctx}: locus must be a dict, got {type(raw).__name__}")
    unknown = sorted(set(raw) - _LOCUS_KEYS)
    if unknown:
        raise ValueError(f"{ctx}: locus has unknown key(s) {unknown} (allowed: {sorted(_LOCUS_KEYS)})")

    def _int(value: Any, name: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{ctx}: locus {name} must be an int, got {value!r}")
        return value

    out: dict[str, Any] = {
        "map_group": _int(raw.get("map_group"), "map_group"),
        "map_num": _int(raw.get("map_num"), "map_num"),
    }
    any_tiles = False
    for key in ("tiles", "near"):
        if key not in raw:
            continue
        pairs = raw[key]
        if not isinstance(pairs, list):
            raise ValueError(f"{ctx}: locus {key} must be a list of [x, y] pairs")
        norm: list[list[int]] = []
        for pair in pairs:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                raise ValueError(f"{ctx}: locus {key} entry {pair!r} is not an [x, y] pair")
            norm.append([_int(pair[0], f"{key} x"), _int(pair[1], f"{key} y")])
        out[key] = norm
        any_tiles = any_tiles or bool(norm)
    if not any_tiles:
        raise ValueError(f"{ctx}: locus needs at least one of 'tiles' / 'near' with a tile in it")
    return out


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

    leg_cap_turns = raw.get("leg_cap_turns")
    if leg_cap_turns is not None and (
        not isinstance(leg_cap_turns, int)
        or isinstance(leg_cap_turns, bool)
        or leg_cap_turns <= 0
    ):
        raise ValueError(
            f"{ctx}: leg_cap_turns must be a positive int or null, got {leg_cap_turns!r}"
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

    locus = raw.get("locus")
    if locus is not None:
        locus = _validate_locus(locus, ctx=ctx)

    return Checkpoint(
        id=cp_id,
        name=name,
        type=cp_type,
        signature=signature,
        deadline_turn=deadline_turn,
        cross_check=cross_check,
        locus=locus,
        leg_cap_turns=leg_cap_turns,
    )


def _parse_multigate(raw: Any, *, index: int) -> MultiGate:
    """Parse a ``multigate`` ladder item (any-order set + progressive deadlines).

    Shape::

        - multigate:
            id: optional-group-id            # synthesised from members if absent
            name: optional display name
            deadline_turns: [875, 960]        # one per required completion
            gates:
              - {id, name, type, signature, cross_check?}   # NO deadline_turn

    Validation: ``gates`` non-empty list of valid checkpoints; ``deadline_turns``
    a list (int or null per element) of the SAME length as ``gates``; its
    non-null entries strictly increasing; members carry no ``deadline_turn``.
    """
    block = raw["multigate"]
    if not isinstance(block, dict):
        raise ValueError(
            f"checkpoint #{index}: 'multigate' must be a mapping, "
            f"got {type(block).__name__}"
        )

    raw_gates = block.get("gates")
    if not isinstance(raw_gates, list) or not raw_gates:
        raise ValueError(
            f"checkpoint #{index}: multigate 'gates' must be a non-empty list"
        )

    gates: list[Checkpoint] = []
    for j, g in enumerate(raw_gates):
        if isinstance(g, dict) and "deadline_turn" in g:
            raise ValueError(
                f"checkpoint #{index}: multigate member #{j} {g.get('id')!r} must "
                "not set 'deadline_turn' — pacing comes from the group's "
                "'deadline_turns'"
            )
        if isinstance(g, dict) and "leg_cap_turns" in g:
            raise ValueError(
                f"checkpoint #{index}: multigate member #{j} {g.get('id')!r} must "
                "not set 'leg_cap_turns' — a leg cap bounds the walk into ONE rung, "
                "and an any-order group has no single previous rung to count from"
            )
        gates.append(_parse_checkpoint(g, index=j))

    member_ids = [g.id for g in gates]
    gid = block.get("id") or "+".join(member_ids)
    if not isinstance(gid, str) or not gid:
        raise ValueError(f"checkpoint #{index}: multigate 'id' must be a string")
    name = block.get("name") or (" / ".join(g.name for g in gates) + " (any order)")

    deadlines = block.get("deadline_turns")
    if not isinstance(deadlines, list) or not deadlines:
        raise ValueError(
            f"multigate {gid!r}: 'deadline_turns' must be a non-empty list"
        )
    if len(deadlines) != len(gates):
        raise ValueError(
            f"multigate {gid!r}: 'deadline_turns' has {len(deadlines)} entries but "
            f"there are {len(gates)} gates — need one deadline per required "
            "completion (use null for an observed-only completion-count)"
        )
    norm_deadlines: list[Optional[int]] = []
    for d in deadlines:
        if d is None:
            norm_deadlines.append(None)
        elif isinstance(d, int) and not isinstance(d, bool):
            norm_deadlines.append(d)
        else:
            raise ValueError(
                f"multigate {gid!r}: deadline_turns entries must be int or null, "
                f"got {d!r}"
            )
    ints = [d for d in norm_deadlines if d is not None]
    if any(b <= a for a, b in zip(ints, ints[1:])):
        raise ValueError(
            f"multigate {gid!r}: deadline_turns must be strictly increasing, "
            f"got {norm_deadlines}"
        )

    return MultiGate(id=gid, name=name, gates=gates, deadline_turns=norm_deadlines)


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

    nodes: list[Node] = []
    checkpoints: list[Checkpoint] = []
    seen_ids: set[str] = set()

    def _claim(cp_id: str, *, kind: str) -> None:
        if cp_id in seen_ids:
            raise ValueError(
                f"{config_path.name}: duplicate {kind} id {cp_id!r}"
            )
        seen_ids.add(cp_id)

    for i, raw in enumerate(raw_checkpoints):
        if isinstance(raw, dict) and "multigate" in raw:
            mg = _parse_multigate(raw, index=i)
            _claim(mg.id, kind="multigate")
            for member in mg.gates:
                _claim(member.id, kind="checkpoint")
                checkpoints.append(member)
            nodes.append(mg)
        else:
            cp = _parse_checkpoint(raw, index=i)
            _claim(cp.id, kind="checkpoint")
            checkpoints.append(cp)
            nodes.append(cp)

    return CheckpointLadder(
        benchmark_version=benchmark_version,
        game=game,
        rom_sha1=rom_sha1,
        checkpoints=checkpoints,
        nodes=nodes,
    )


def load_checkpoints(path: Union[str, Path]) -> list[Checkpoint]:
    """Load just the ordered list of checkpoints (see ``load_ladder``)."""
    return load_ladder(path).checkpoints
