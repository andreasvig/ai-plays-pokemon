"""Regression tests for the collapsed model registry (Andreas 2026-06-17).

One record per model + a thinking_levels axis; reasoning is derived from
reasoning_type at resolve time; the run identity stays "model(level)" so each
level still benchmarks separately. Covers config resolution, the picker
projection (catalog.list_models), and competitor enumeration against the REAL
configs/models.yaml.
"""

import pytest

from src.app.catalog import list_models
from src.config import (
    _load_models_registry,
    is_valid_model_selection,
    list_competitor_aliases,
    model_default_level,
    model_thinking_levels,
    parse_model_alias,
    resolve_model_selection,
)

REG = _load_models_registry()


def test_parse_model_alias():
    assert parse_model_alias("gpt-5.5(high)") == ("gpt-5.5", "high")
    assert parse_model_alias("grok-4.3") == ("grok-4.3", None)
    assert parse_model_alias("mimo-v2.5(non-thinking)") == ("mimo-v2.5", "non-thinking")


def test_every_competitor_alias_resolves():
    comps = list_competitor_aliases(REG)
    assert len(comps) >= 70  # 23 models, mostly multi-level
    for alias in comps:
        r = resolve_model_selection(alias, REG)
        assert r["openrouter_id"], alias


def test_effort_resolution_and_default_highest():
    # default (no level) → highest = first in thinking_levels
    r = resolve_model_selection("gpt-5.5", REG)
    assert r["_level"] == "xhigh"
    assert r["reasoning"] == {"effort": "xhigh", "summary": "auto"}
    assert r["_alias"] == "gpt-5.5(xhigh)"
    # explicit level
    r2 = resolve_model_selection("gpt-5.5(medium)", REG)
    assert r2["reasoning"] == {"effort": "medium", "summary": "auto"}


def test_binary_resolution_and_per_level_slow():
    t = resolve_model_selection("mimo-v2.5(thinking)", REG)
    assert t["reasoning"] == {"enabled": True}
    assert t["slow"] is True            # per-level slow map: thinking only
    nt = resolve_model_selection("mimo-v2.5(non-thinking)", REG)
    assert nt["reasoning"] == {"enabled": False}
    assert nt["slow"] is False


def test_always_on_type_none():
    r = resolve_model_selection("grok-4.3", REG)
    assert r["reasoning"] is None       # nothing sent — always-on
    assert r["slow"] is True
    assert r["_level"] is None
    assert model_thinking_levels(REG["grok-4.3"]) == []
    # a level on a type-none model is rejected
    with pytest.raises(ValueError):
        resolve_model_selection("grok-4.3(high)", REG)


def test_gap_fills_present():
    # The collapse filled the audit gaps by construction.
    assert model_thinking_levels(REG["gpt-5.4-nano"]) == ["xhigh", "high", "medium", "low", "minimal"]
    assert model_thinking_levels(REG["grok-build-0.1"]) == ["high", "medium", "low", "minimal"]
    assert model_thinking_levels(REG["gpt-5.5-pro"]) == ["xhigh", "high", "medium", "low", "minimal"]


def test_invalid_selections_rejected():
    assert not is_valid_model_selection("gpt-5.5(turbo)", REG)
    assert not is_valid_model_selection("nope(high)", REG)
    assert is_valid_model_selection("gpt-5.5(low)", REG)
    assert is_valid_model_selection("openai/some-raw-id", REG)   # raw passthrough


def test_provider_and_output_mode_hoisted():
    g = resolve_model_selection("gemma-4-31b(thinking)", REG)
    assert g["provider"] == {"sort": "throughput"}
    assert g["output_mode"] == "prompted"
    # hoisted to the model, so non-thinking inherits it too
    g2 = resolve_model_selection("gemma-4-31b(non-thinking)", REG)
    assert g2["output_mode"] == "prompted"


def test_catalog_picker_shape():
    rows = {r["model"]: r for r in list_models()}
    gf = rows["gemini-3-flash"]
    assert gf["reasoning_type"] == "effort"
    assert gf["default_level"] == "high"
    assert [lv["level"] for lv in gf["levels"]] == ["high", "medium", "low", "minimal"]
    # type none → no levels, null default
    assert rows["grok-4.3"]["levels"] == []
    assert rows["grok-4.3"]["default_level"] is None
    # default_level is always the first (highest) listed level
    for r in rows.values():
        if r["levels"]:
            assert r["default_level"] == r["levels"][0]["level"]


def test_default_level_helper():
    assert model_default_level(REG["gpt-5.5"]) == "xhigh"
    assert model_default_level(REG["gemini-3.5-flash"]) == "high"
    assert model_default_level(REG["grok-4.3"]) is None
