"""The Player endpoint's list price on the projected row, and what it may claim.

Andreas 2026-09-16: a free model must show N/A on the board, not $0.00 — free is
the ABSENCE of a price, not the lowest one. The whole rule rests on telling a
zero price from an unknown one, so the split is asserted here rather than left to
the frontend, which only ever sees the field this produces.

Everything below is about the READ: which endpoint's price is taken, and which
situations must answer "unknown" instead of guessing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.app.projection import project_run_dir


def _run(tmp_path: Path, *, endpoints, pinned="stealth", name="r", pricing_file=True) -> Path:
    run = tmp_path / f"2026-09-16_10-00-00_config-5.1__{name}"
    (run / "conversation").mkdir(parents=True, exist_ok=True)
    (run / "run_summary.json").write_text(json.dumps({
        "run_id": run.name, "kind": "official", "status": "completed",
        "session": {"llm_alias": "m", "llm_model": "v/m", "total_turns": 10,
                    "duration_seconds": 100.0, "started_at": "2026-09-16T10:00:00"},
        "cost": {"total_usd": 0.5},
    }))
    (run / "config.json").write_text(json.dumps({"_provider_profile": {"endpoint": pinned}}))
    if pricing_file:
        (run / "conversation" / "endpoint-pricing.json").write_text(
            json.dumps({"model": "v/m", "endpoints": endpoints}))
    return run


def _price(tmp_path, **kw):
    return project_run_dir(_run(tmp_path, **kw)).endpoint_price_usd_per_m


FREE = [{"tag": "stealth", "pricing": {"prompt": "0", "completion": "0"}}]
PAID = [{"tag": "stealth", "pricing": {"prompt": "0.00001", "completion": "0.00005"}}]


def test_a_free_endpoint_reads_as_two_explicit_zeroes(tmp_path):
    assert _price(tmp_path, endpoints=FREE) == {"prompt": 0.0, "completion": 0.0}


def test_prices_are_converted_to_usd_per_million(tmp_path):
    # OpenRouter quotes USD per TOKEN. $0.00001/token is $10 per million — the
    # unit every price in configs/provider-profiles.yaml is written in, so a
    # per-token number leaking through would be off by a factor of a million and
    # still look plausible on a card.
    assert _price(tmp_path, endpoints=PAID) == {"prompt": 10.0, "completion": 50.0}


def test_the_pinned_endpoint_wins_when_several_are_served(tmp_path):
    # The real case: gpt-6-astra serves five tags at four different prices, and
    # the run was pinned to one of them. Taking the first or the cheapest would
    # quietly misprice the run.
    many = [
        {"tag": "openai/flex", "pricing": {"prompt": "0.000005", "completion": "0.000025"}},
        {"tag": "openai", "pricing": {"prompt": "0.00001", "completion": "0.00005"}},
        {"tag": "openai/fast", "pricing": {"prompt": "0.00002", "completion": "0.0001"}},
    ]
    assert _price(tmp_path, endpoints=many, pinned="openai", name="a") == {"prompt": 10.0, "completion": 50.0}
    assert _price(tmp_path, endpoints=many, pinned="openai/flex", name="b") == {"prompt": 5.0, "completion": 25.0}


@pytest.mark.parametrize("case,kw", [
    ("no pricing file at all (every pre-append run)", {"endpoints": FREE, "pricing_file": False}),
    ("an empty endpoint list", {"endpoints": []}),
    ("unprofiled: no pinned tag, and more than one candidate", {
        "pinned": "", "endpoints": [
            {"tag": "a", "pricing": {"prompt": "0", "completion": "0"}},
            {"tag": "b", "pricing": {"prompt": "0.1", "completion": "0.1"}}]}),
    ("the pinned tag is not among the served ones", {"endpoints": FREE, "pinned": "gone"}),
    ("the price is missing", {"endpoints": [{"tag": "stealth", "pricing": {}}]}),
    ("the price is not a number", {"endpoints": [{"tag": "stealth", "pricing": {"prompt": "n/a", "completion": "0"}}]}),
])
def test_an_unnameable_price_is_none_and_never_free(tmp_path, case, kw):
    """Every ambiguous case falls on the PRICED side.

    None means unknown, and the frontend reads unknown as priced. The failure that
    matters is the other direction — a paid model shown as unpriced, dropped off
    the cost cards and silently excused from the price frontier — so nothing here
    may return a pair of zeroes. Note the third case is built so that GUESSING
    would produce exactly that: its first endpoint is free.
    """
    price = _price(tmp_path, **kw)
    assert price is None, f"{case}: got {price}"


def test_an_unprofiled_run_with_one_served_endpoint_is_still_nameable(tmp_path):
    # The one guess that is not a guess: with a single served endpoint there is
    # nothing to disambiguate, so an unprofiled run still gets its price.
    assert _price(tmp_path, endpoints=PAID, pinned="") == {"prompt": 10.0, "completion": 50.0}
