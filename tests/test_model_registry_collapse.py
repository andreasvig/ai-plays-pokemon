"""Regression tests for the collapsed model registry (Andreas 2026-06-17).

One record per model + a thinking_levels axis; reasoning is derived from
reasoning_type at resolve time; the run identity stays "model(level)" so each
level still benchmarks separately. Covers config resolution, the picker
projection (catalog.list_models), and competitor enumeration against the REAL
configs/models.yaml.

Re-pointed 2026-09-07 when the registry was pruned to the config-5.0 keep list
(43 entries -> 21). The fixtures below used gpt-5.5 / grok-4.3 / gemini-3-flash /
gpt-5.4-nano / grok-build-0.1 / gpt-5.5-pro / gemini-3.5-flash, all of which left
the registry, so each was re-pointed at a kept model with the SAME shape. Two
claims could not be re-pointed and are asserted as absences instead, with the
reason named: the registry no longer contains a `reasoning_type: none` model at
all (test_always_on_type_none), and it no longer contains a `provider:` block on
any entry (test_provider_and_output_mode_hoisted) because decision D3 gave the
provider profile sole ownership of the endpoint.
"""

import pytest
import yaml

from src.agent.provider_profiles import PROFILE_PATH
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
# Entries kept ONLY so a hard-coded alias in src/ still resolves (see the RETIRED
# ENTRIES banner in configs/models.yaml). They have no provider profile by design,
# so every profile-shaped invariant below has to skip them EXPLICITLY rather than
# by accident — and the skip is itself asserted, so a retired flag cannot be used
# to smuggle a model past the profile requirement.
RETIRED = {n for n, e in REG.items() if e.get("retired")}
ACTIVE = {n: e for n, e in REG.items() if n not in RETIRED}

# A `reasoning_type: none` (always-on, no levels) entry, kept synthetic because the
# real registry has none since the 2026-09-07 prune. The code path is still live in
# _reasoning_for_level / resolve_model_selection / list_competitor_aliases, so it is
# still tested — just not against real data.
ALWAYS_ON = {"openrouter_id": "vendor/always-on-1", "reasoning_type": "none", "slow": True}


def test_parse_model_alias():
    assert parse_model_alias("gpt-5.6-sol(high)") == ("gpt-5.6-sol", "high")
    assert parse_model_alias("kimi-k3") == ("kimi-k3", None)
    assert parse_model_alias("mimo-v2.5(non-thinking)") == ("mimo-v2.5", "non-thinking")


def test_every_competitor_alias_resolves():
    comps = list_competitor_aliases(REG)
    assert len(comps) >= 70  # 21 models, mostly multi-level — 81 identities today
    for alias in comps:
        r = resolve_model_selection(alias, REG)
        assert r["openrouter_id"], alias
    # Each model contributes exactly one identity per level (or one bare identity
    # when it has none). Pins the shape without pinning a count that moves whenever
    # Andreas adds a model.
    expected = sum(len(model_thinking_levels(e)) or 1 for e in REG.values())
    assert len(comps) == expected


def test_effort_resolution_and_default_highest():
    # default (no level) → highest = first in thinking_levels
    r = resolve_model_selection("gpt-5.6-sol", REG)
    assert r["_level"] == "max"
    assert r["reasoning"] == {"effort": "max", "summary": "auto"}
    assert r["_alias"] == "gpt-5.6-sol(max)"
    # explicit level
    r2 = resolve_model_selection("gpt-5.6-sol(medium)", REG)
    assert r2["reasoning"] == {"effort": "medium", "summary": "auto"}


def test_binary_resolution_and_per_level_slow():
    t = resolve_model_selection("mimo-v2.5(thinking)", REG)
    assert t["reasoning"] == {"enabled": True}
    assert t["slow"] is True            # per-level slow map: thinking only
    nt = resolve_model_selection("mimo-v2.5(non-thinking)", REG)
    assert nt["reasoning"] == {"enabled": False}
    assert nt["slow"] is False


def test_always_on_type_none():
    registry = {**REG, "always-on-1": ALWAYS_ON}
    r = resolve_model_selection("always-on-1", registry)
    assert r["reasoning"] is None       # nothing sent — always-on
    assert r["slow"] is True
    assert r["_level"] is None
    assert model_thinking_levels(ALWAYS_ON) == []
    assert list_competitor_aliases({"always-on-1": ALWAYS_ON}) == ["always-on-1"]
    # a level on a type-none model is rejected
    with pytest.raises(ValueError):
        resolve_model_selection("always-on-1(high)", registry)
    # And the absence this test used to cover with grok-4.3: after the 2026-09-07
    # prune NO real entry is type none, so the branch above is unreachable from
    # configs/models.yaml. Stated rather than implied — if a type-none model is ever
    # added back, this assert fires and the fixture above should be dropped for it.
    assert [n for n, e in REG.items() if e.get("reasoning_type", "none") == "none"] == []


def test_ladder_corrections_of_the_2026_09_07_prune():
    """The three ladders trimmed to OpenRouter's `reasoning.supported_efforts`.

    Replaces the old test_gap_fills_present, which pinned the ladders of three
    models that left the registry. Each level below was removed (or added) because
    the catalog does not (or does) enumerate it for that model; every dropped level
    had `observed.<level>.sample_turns: 0`, so no measurement was rewritten.
    """
    # claude-sonnet-5: `minimal` dropped — Anthropic's ladder is low..max and
    # OpenRouter clamps a Claude `minimal`, which is why claude-opus-5 omits it too.
    assert model_thinking_levels(REG["claude-sonnet-5"]) == ["max", "xhigh", "high", "medium", "low"]
    assert model_thinking_levels(REG["claude-opus-5"]) == ["max", "xhigh", "high", "medium", "low"]
    # kimi-k3: xhigh/medium/none dropped. `none` is the one that crashed dispatch —
    # the profile marks reasoning mandatory, so kimi-k3(none) raised inside
    # build_run_config after the queue card went active.
    assert model_thinking_levels(REG["kimi-k3"]) == ["max", "high", "low"]
    # fugu-ultra: medium/low/minimal dropped, max/xhigh added (supported_efforts is
    # max/xhigh/high, default xhigh) — its ladder had no legal rung above `high`.
    assert model_thinking_levels(REG["fugu-ultra"]) == ["max", "xhigh", "high"]
    # gpt-5.6 keeps `none`: unlike kimi, the catalog DOES enumerate it and reasoning
    # is not mandatory there. A dropped-because-illegal rule must not eat this one.
    assert "none" in model_thinking_levels(REG["gpt-5.6-sol"])


def test_registry_levels_are_a_subset_of_the_profile_reasoning_efforts():
    """Decision 2026-09-07: the profile owns the legal ladder, the registry displays it.

    `reasoning_efforts` in configs/provider-profiles.yaml is what the dialog offers
    and what resolve_provider_profile validates against, so a registry level with no
    matching effort is a run that dies at dispatch (finding #5). Binary models list
    their LEVEL names there — see the comment in provider-profiles.yaml.
    """
    profiles = yaml.safe_load(PROFILE_PATH.read_text())["profiles"]
    for name, entry in ACTIVE.items():
        model = entry["openrouter_id"]
        assert model in profiles, f"{name} ({model}) has no provider profile"
        # Non-empty is load-bearing, not tidiness. resolve_provider_profile waves
        # an effort through when `reasoning_efforts` is empty, because empty is the
        # `defaults:` value every UNPROFILED model inherits and "not probed" must
        # not read as "illegal". Its comment says an empty list can never mean
        # "profiled but unchecked" *because this test forbids it* — so assert it
        # here, or that reasoning becomes self-satisfying.
        assert profiles[model]["reasoning_efforts"], (
            f"{name} ({model}) is profiled with an EMPTY reasoning_efforts, which makes "
            f"resolve_provider_profile skip the effort check for it"
        )
        illegal = sorted(set(model_thinking_levels(entry)) - set(profiles[model]["reasoning_efforts"]))
        assert illegal == [], f"{name}: thinking_levels {illegal} are not in the profile's reasoning_efforts"
    # And the reverse direction, which is what makes the dialog's intersection safe:
    # every profile is reachable from the registry, so no profile is orphaned.
    orphans = sorted(set(profiles) - {e["openrouter_id"] for e in ACTIVE.values()})
    assert orphans == [], f"profiles with no registry entry, unreachable from the dialog: {orphans}"


def test_retired_entries_are_resolvable_unprofiled_and_justified():
    """`retired: true` is an escape hatch, so pin what it may and may not buy.

    A retired entry exists because a hard-coded alias in src/ resolves against the
    registry and resolve_model_selection sys.exit()s on an unknown one. It must
    therefore RESOLVE, must NOT have a provider profile, and must be a closed,
    named set — otherwise `retired` becomes a way to keep a model off the keep
    list without profiling it.

    Note what this does NOT claim: an unprofiled model is still loadable on
    config-5.0, because resolve_provider_profile applies the `defaults:` block
    with `endpoint: ""`. Retiring makes a model uncertified (unpinned endpoint,
    `unprofiled: true` on the resolved profile), not unreachable, which is why
    hiding it from the picker is a separate change.
    """
    assert RETIRED == {"gemini-3.5-flash", "claude-opus-4.7"}, (
        "the retired set changed; each entry needs a caller in src/ that names its "
        "alias, recorded in the RETIRED ENTRIES banner of configs/models.yaml"
    )
    profiles = yaml.safe_load(PROFILE_PATH.read_text())["profiles"]
    for name in sorted(RETIRED):
        entry = REG[name]
        for level in model_thinking_levels(entry):
            r = resolve_model_selection(f"{name}({level})", REG)
            assert r["openrouter_id"] and r["_level"] == level
        assert entry["openrouter_id"] not in profiles, (
            f"{name} is retired but profiled — a profiled model belongs on the keep "
            f"list with a registry entry that is not retired"
        )
    # The two aliases the retired entries exist for. Asserted as literals because
    # that is what the callers hard-code; if a caller is re-pointed, delete the
    # entry rather than leaving it here unexplained.
    assert resolve_model_selection("gemini-3.5-flash(medium)", REG)["openrouter_id"] == "google/gemini-3.5-flash"
    assert resolve_model_selection("claude-opus-4.7(medium)", REG)["openrouter_id"] == "anthropic/claude-opus-4.7"


def test_invalid_selections_rejected():
    assert not is_valid_model_selection("gpt-5.6-sol(turbo)", REG)
    assert not is_valid_model_selection("nope(high)", REG)
    assert is_valid_model_selection("gpt-5.6-sol(low)", REG)
    assert is_valid_model_selection("openai/some-raw-id", REG)   # raw passthrough
    # The level that used to reach dispatch and die there (finding #5) is now
    # rejected at the registry: kimi-k3 offers max/high/low only.
    assert not is_valid_model_selection("kimi-k3(xhigh)", REG)
    assert not is_valid_model_selection("kimi-k3(none)", REG)
    # Models that left the registry in the 2026-09-07 prune are no longer startable.
    assert not is_valid_model_selection("gpt-5.5(high)", REG)
    assert not is_valid_model_selection("gemini-3.1-pro(low)", REG)


def test_output_mode_hoisted_and_no_registry_provider_pins():
    g = resolve_model_selection("gemma-4-31b(thinking)", REG)
    assert g["output_mode"] == "prompted"
    # hoisted to the model, so non-thinking inherits it too
    g2 = resolve_model_selection("gemma-4-31b(non-thinking)", REG)
    assert g2["output_mode"] == "prompted"
    # Decision D3 (2026-09-07): the provider profile owns the endpoint, so NO registry
    # entry carries a `provider:` block any more. gemma-4-31b is the case that proved
    # the point — it pinned `{sort: throughput}` while its profile pinned
    # `deepinfra/turbo`, the two disagreed, and the append path silently resolved it
    # in the profile's favour. This assert replaces the old one that pinned the
    # throughput sort as expected behaviour.
    assert "provider" not in g
    pinned = sorted(n for n, e in ACTIVE.items() if "provider" in e or "fallbacks" in e)
    assert pinned == [], f"registry entries still pinning an endpoint: {pinned}"
    # Same rule for sampling: it lives in exactly one file per model, and where the
    # profile has a `sampling:` block the registry must not also declare it —
    # AppendAgent._body replaces the resolved registry settings wholesale, so a
    # registry-only value would silently never reach the wire.
    profiles = yaml.safe_load(PROFILE_PATH.read_text())["profiles"]
    doubled = sorted(n for n, e in ACTIVE.items()
                     if profiles.get(e["openrouter_id"], {}).get("sampling")
                     and ("temperature" in e or "top_p" in e))
    assert doubled == [], f"sampling declared in BOTH registry and profile: {doubled}"


def test_catalog_picker_shape():
    rows = {r["model"]: r for r in list_models()}
    gf = rows["gemini-3.5-flash-lite"]
    assert gf["reasoning_type"] == "effort"
    assert gf["default_level"] == "high"
    assert [lv["level"] for lv in gf["levels"]] == ["high", "medium", "low", "minimal"]
    # binary → the two level names, not an effort ladder
    assert [lv["level"] for lv in rows["mimo-v2.5"]["levels"]] == ["thinking", "non-thinking"]
    # Every row has levels after the 2026-09-07 prune, because no entry is type none.
    # The empty-levels/None-default projection is exercised in test_always_on_type_none.
    assert all(r["levels"] for r in rows.values())
    assert all(r["default_level"] is not None for r in rows.values())
    # default_level is always the first (highest) listed level
    for r in rows.values():
        if r["levels"]:
            assert r["default_level"] == r["levels"][0]["level"]


def test_default_level_helper():
    assert model_default_level(REG["gpt-5.6-sol"]) == "max"
    assert model_default_level(REG["gemini-3.8-flash"]) == "high"
    assert model_default_level(ALWAYS_ON) is None
