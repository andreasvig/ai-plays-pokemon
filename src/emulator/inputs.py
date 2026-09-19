"""The input token grammar — and the one action that is not a bare button.

Every input this harness has ever had is **one string**: ``"a"``, ``"U"``,
``"WAIT"``. A stylus tap is not. It is a verb plus two floats, and that is the
whole difficulty of the NDS capability (plan §3.5: "the part that is not a new
enum member").

Why a verb-with-arguments TOKEN and not a structured object
-----------------------------------------------------------
``inputs`` is a ``list[str]`` from the model's schema all the way to disk:

* ``turn.py:1797`` renders it with ``", ".join(result.inputs)``,
* ``turn.py:1804`` logs ``str(result.inputs)``,
* ``turn.py:1775`` writes it verbatim as ``explanation.action`` in
  ``events.jsonl``, which ``src/app/projection.py``, ``src/app/replay.py`` and
  ``src/app/route.py`` all read back.

Making one list element an object changes the on-disk event shape for every one
of those readers, including runs already published. A token keeps the shape and
puts the parsing in exactly one place — this module — so the four consumers
(the agent's schema, the SkyEmu backend's execution, the mGBA backend's refusal,
and the report's per-input census) share one grammar instead of three regexes.

The token
---------
``tap:<x>,<y>`` — x and y are normalised **0..1 over the TOUCH SCREEN**, not
over the 256x384 capture. That distinction is the trap recorded in
``v2-experiments/emulator-research.md``: the touch screen is the bottom half of
the stacked image, so image row *r* maps to ``(r - 192) / 192``. Anything
reading a coordinate off the frame goes through
:func:`src.emulator.backends.frame.image_row_to_touch_y`; anything writing one
to the wire uses the numbers here unchanged.

The spelling ``tap:0.50,0.42`` is the one the experiment harness already used
(``v2-experiments/harness/play.py`` logs exactly that), so a probe run's trace
and a real run's ``events.jsonl`` read the same.

Out of range is an ERROR, not a clamp
-------------------------------------
``parse_tap`` raises on x or y outside 0..1. The harness probe clamped, which is
right for a probe — a model naming an impossible coordinate is a finding about
the prompt, not a crash. In the real turn loop the opposite holds:
``base.py`` states that ``press_button_list`` raises ``ValueError`` on an invalid
name *so that* ``turn.py`` rejects a model's bad action rather than silently
dropping it. A clamp would silently move the tap somewhere the model did not
aim, and the model would then reason about a screen change it did not cause.
"""

from __future__ import annotations

import re
from typing import Optional

#: The name a config's ``valid_inputs`` uses to switch the stylus on. It is the
#: only spelling: ``normalize`` upper-cases before testing membership, and the
#: mGBA backend refuses a config that lists it (see its ``__init__``).
TAP_INPUT = "TAP"

#: How the verb is shown to the model in the prompt's button list.
TAP_SYNTAX = "TAP:<x>,<y>"

#: Schema-facing pattern: a tap token with both coordinates written as a decimal
#: in 0..1. Tight on purpose — as a ``pattern`` on the model's output schema it
#: makes an off-screen coordinate unrepresentable rather than merely rejected
#: after the model has been billed for it. Lower-case only, because that is the
#: form the schema asks for; :func:`parse_tap` is the lenient reader.
TAP_PATTERN = r"^tap:(?:0(?:\.\d+)?|1(?:\.0+)?),(?:0(?:\.\d+)?|1(?:\.0+)?)$"

# The lenient reader. Accepts any case, optional whitespace, and any numeric
# spelling including out-of-range ones — so that an out-of-range coordinate is
# reported as "0.00..1.00" rather than as "not a tap at all", which is the
# difference between a fixable model error and a mystery.
_TAP_RE = re.compile(r"^tap\s*:\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$",
                     re.IGNORECASE)

#: Decimal places a canonical token carries. The touch screen is 256x192 native
#: pixels, so 1/256 ~= 0.0039: three places resolve every pixel and no more.
TAP_PRECISION = 3


def is_tap(token: object) -> bool:
    """True if ``token`` is spelled like a tap — whatever its coordinates.

    Deliberately shape-only: a token with x=1.5 IS a tap, and a malformed one,
    which is what lets :func:`parse_tap` say so.
    """
    return isinstance(token, str) and token.strip().lower().startswith("tap:")


def parse_tap(token: str) -> tuple[float, float]:
    """``"tap:0.5,0.4"`` -> ``(0.5, 0.4)``. Raises ``ValueError`` otherwise.

    Both coordinates must lie in 0..1 inclusive; see the module docstring for
    why an out-of-range value is refused rather than clamped.
    """
    match = _TAP_RE.match(str(token).strip())
    if match is None:
        raise ValueError(
            f"{token!r} is not a tap. A tap is {TAP_SYNTAX.lower()} with two "
            "decimals in 0..1, e.g. 'tap:0.5,0.4'."
        )
    x, y = float(match.group(1)), float(match.group(2))
    for name, value in (("x", x), ("y", y)):
        if not 0.0 <= value <= 1.0:
            raise ValueError(
                f"tap {name}={value} is outside 0..1. Coordinates are normalised "
                "over the TOUCH SCREEN (the bottom half of the frame, below the "
                f"seam), not over the whole image. Token: {token!r}"
            )
    return x, y


def format_tap(x: float, y: float) -> str:
    """The canonical token for a tap at (x, y). Raises on an off-screen point."""
    if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
        raise ValueError(f"tap ({x}, {y}) is outside the touch screen's 0..1 range")
    return f"tap:{x:.{TAP_PRECISION}f},{y:.{TAP_PRECISION}f}"


def normalize(token: str, aliases: Optional[dict[str, str]] = None) -> str:
    """One model-supplied input -> the form a backend executes.

    A tap becomes its canonical lower-case token (validated on the way through);
    anything else is upper-cased and alias-mapped, exactly as the two backends'
    ``normalize_button_list`` did before this module existed. Returning the two
    kinds from one function is what keeps a tap's POSITION in the list — a tap
    between two presses has to run between them.
    """
    if is_tap(token):
        return format_tap(*parse_tap(token))
    normalized = str(token).strip().upper()
    return (aliases or {}).get(normalized, normalized)


def census_key(action: object) -> str:
    """The bucket one logged action belongs in, for the report's input census.

    Every tap collapses to ``"tap"``. Without this, a run of 40 taps renders as
    40 buckets of 1 — the census counts distinct STRINGS, and each tap carries
    its own coordinates (``src/app/projection.py:_input_stats``).
    """
    key = str(action).strip().lower()
    return "tap" if key.startswith("tap:") else key


__all__ = [
    "TAP_INPUT",
    "TAP_PATTERN",
    "TAP_PRECISION",
    "TAP_SYNTAX",
    "census_key",
    "format_tap",
    "is_tap",
    "normalize",
    "parse_tap",
]
