"""The list-price cost derivation, graded against bills nobody derived.

``token_cost`` answers "what would these tokens cost at this price" for a run
that was billed nothing — a model played under a cloaked listing, which
OpenRouter serves free until the lab announces it. There is no ground truth for
that run by construction: it has no bill.

There is ground truth for every OTHER run on the board. Each one carries the
same four token counts, its serving endpoint's list price, and the amount
OpenRouter actually charged. Running the same function over those is the only
thing that makes the free run's figure worth printing, so that is what the first
test does — against the real run dirs, with the corpus itself as the fixture.

Skipped wholesale when ``local/runs`` is not on this machine (CI, a fresh
clone): the claim is about real bills and a synthetic stand-in cannot make it.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.app.projection import _endpoint_price, project_run_dir, token_cost, token_usage

RUNS = Path(__file__).resolve().parents[1] / "local" / "runs"

# A continued run's bill and its events cover different spans, so its ratio says
# nothing about the formula. Verified rather than assumed, on the 2026-09-11
# glm-5.3-flash(high) continue: session.total_turns 512, usage events spanning
# turns 1 to 512 — the whole run, source segment included — against an llm_usd
# of $0.527 that covers only the segment this dir played. Named by the suffix the
# runner gives them, not by a list of run ids.
CONTINUED = "_continued_from_turn_"

# 2026-09-11 gpt-6-astra-low disagrees with ITSELF: run_summary's
# cost.total_input_tokens is 3,416,654 where its own llm_request_usage events sum
# to 5,290,213, and its 23,830 output tokens against the events' 33,047. One of
# the two counters is wrong and it is not this function's job to say which; the
# exclusion is named here so the number stays visible rather than being absorbed
# into a loose tolerance.
INCONSISTENT = {"2026-09-11_19-45-18_config-5.1__gpt-6-astra-low"}


def _priced_runs():
    if not RUNS.is_dir():
        return []
    out = []
    for run_dir in sorted(RUNS.iterdir()):
        if not (run_dir / "run_summary.json").is_file():
            continue
        if CONTINUED in run_dir.name or run_dir.name in INCONSISTENT:
            continue
        price = _endpoint_price(run_dir)
        usage = token_usage(run_dir)
        if not price or not usage or not price["prompt"]:
            continue  # unpriced, unnameable, or the free listing itself
        billed = (json.loads((run_dir / "run_summary.json").read_text()).get("cost") or {}).get("llm_usd")
        if not billed:
            continue
        out.append((run_dir.name, usage, price, billed))
    return out


@pytest.mark.skipif(not RUNS.is_dir(), reason="no local/runs on this machine")
def test_the_formula_reproduces_every_bill_it_can_be_checked_against():
    """Within 0.5% on every paid run, and exact to the cent on nearly all of them.

    Two claims, because one number would hide the other. The tight one is what
    makes the derivation worth printing; the loose one is the honest ceiling, and
    it exists because a list price is not the whole billing rule. OpenRouter
    pricing blocks can carry an `overrides` tier — grok-4.6's doubles every rate
    above a 200,000-token prompt — and a provider can apply a discount this
    function never sees. That is exactly where the one inexact run sits: grok-4.6
    derives $23.4524 against a $23.4198 bill, 0.14% high, its implied cache-read
    rate $0.4983 per M where the endpoint lists $0.5000.

    unbiased/pareto, the model this is all for, lists no overrides and no
    discount, so its figure is not subject to that particular gap.
    """
    runs = _priced_runs()
    # 17 of the 29 run dirs today: 9 continued, 1 self-inconsistent and the 2
    # cloaked (free, so nothing to check against) are excluded above, each for a
    # named reason. The floor guards against the corpus quietly shrinking to a
    # couple of runs and the assertions below passing on nothing.
    assert len(runs) >= 15, f"too few runs to make the claim, got {len(runs)}"
    off, exact = [], 0
    for name, usage, price, billed in runs:
        derived = token_cost(usage, price)
        if abs(derived - billed) <= 0.01:
            exact += 1
        if abs(derived - billed) / billed > 0.005:
            off.append(f"{name}: derived ${derived:.4f} vs billed ${billed:.4f}")
    assert not off, "the derivation is more than 0.5% off what was charged:\n" + "\n".join(off)
    assert exact >= len(runs) - 1, f"only {exact} of {len(runs)} reconcile to the cent"


@pytest.mark.skipif(not RUNS.is_dir(), reason="no local/runs on this machine")
def test_the_cache_rates_are_load_bearing_in_both_directions():
    """Drop either cache rule and real bills stop reconciling.

    The two absences mean OPPOSITE things (projection._per_million) and both were
    read off this corpus, so both get a mutant here. Without them the first test
    could be passing on a formula that happens to be right for one provider.
    """
    runs = _priced_runs()

    def total(usage, price):
        return token_cost(usage, price)

    # Mutant A: cache reads billed at the prompt rate instead of free/discounted.
    broke_a = [n for n, u, p, b in runs
               if u["cached"] and abs(total(u, {**p, "input_cache_read": p["prompt"]}) - b) > 0.01]
    assert broke_a, "no run's bill depends on the cache READ rate — the rule is untested"

    # Mutant B: cache writes billed at the prompt rate instead of their premium.
    broke_b = [n for n, u, p, b in runs
               if u["written"] and "input_cache_write" in p
               and abs(total(u, {k: v for k, v in p.items() if k != "input_cache_write"}) - b) > 0.01]
    assert broke_b, "no run's bill depends on the cache WRITE rate — the rule is untested"


@pytest.mark.skipif(not (RUNS / "2026-09-16_21-08-49_config-5.1__union-alpha").is_dir(),
                    reason="the cloaked run is not on this machine")
def test_the_cloaked_run_keeps_its_bill_and_gains_a_derived_cost():
    """The run this was built for: billed nothing, priced after the fact.

    Pins both halves together, because the failure worth preventing is the
    derived figure QUIETLY REPLACING the bill. total_cost_usd must still be the
    $0.059 of OCR that was actually spent.
    """
    run = project_run_dir(RUNS / "2026-09-16_21-08-49_config-5.1__union-alpha")

    # Renamed onto the model's current identity, both halves.
    assert run.model == "pareto"
    assert run.model_resolved == "unbiased/pareto"

    # What it was charged: nothing for the LLM, a few cents of OCR. Untouched.
    assert run.endpoint_price_usd_per_m == {"prompt": 0.0, "completion": 0.0}
    assert abs(run.total_cost_usd - 0.059379) < 1e-6

    # What it would have cost at the announced price. 3,758,611 prompt tokens of
    # which 2,411,925 were read from cache, and 215,110 completion, at
    # $2.50 / $0.25 / $7.50 per million.
    assert run.list_price_usd_per_m == {"prompt": 2.5, "completion": 7.5, "input_cache_read": 0.25}
    assert abs(run.list_price_cost_usd - 5.5830) < 0.001
    assert abs(run.list_price_per_turn_usd - 5.5830 / 175) < 1e-5


@pytest.mark.skipif(not RUNS.is_dir(), reason="no local/runs on this machine")
def test_no_other_run_gets_a_derived_cost():
    """An ordinary run's bill IS the answer, so it must not grow a second one."""
    derived = [d.name for d in sorted(RUNS.iterdir())
               if (d / "run_summary.json").is_file()
               and (project_run_dir(d) or object()).__dict__.get("list_price_cost_usd") is not None]
    assert derived == ["2026-09-16_21-08-49_config-5.1__union-alpha"], derived


@pytest.mark.skipif(not (RUNS / "2026-09-16_21-08-49_config-5.1__union-alpha").is_dir(),
                    reason="the cloaked run is not on this machine")
def test_the_ladder_and_the_header_are_the_same_number():
    """The last gate's running total IS the run's total. A free oracle.

    Both are derived from the same events by different code — `token_usage` sums
    the run, `_gate_clock` accumulates per turn — so a disagreement needs no
    ground truth to be a bug. It caught one on the first run: the ladder summed
    `llm_request_usage` AND the legacy `turn_usage`, which a run carries one of
    each per call, and came out at $11.17 under a $5.58 header. Exactly double.
    """
    run = project_run_dir(RUNS / "2026-09-16_21-08-49_config-5.1__union-alpha")
    last = run.gate_list_costs_usd["brock_defeated"]
    assert abs(last - run.list_price_cost_usd) < 0.01, (
        f"ladder ends at ${last:.4f}, header says ${run.list_price_cost_usd:.4f}")
    # Monotonic, and it starts well below the end — a ladder of twelve identical
    # numbers would satisfy the equality above.
    steps = [run.gate_list_costs_usd[g] for g in run.gate_turns]
    assert steps == sorted(steps), steps
    assert steps[0] < steps[-1] / 4, f"the ladder barely moves: {steps[0]} → {steps[-1]}"
    # The BILLED ladder is untouched and still says what was charged: nothing.
    assert set(run.gate_costs_usd.values()) == {0.0}


@pytest.mark.skipif(not RUNS.is_dir(), reason="no local/runs on this machine")
def test_the_two_usage_event_names_are_not_summed_together():
    """One call is logged twice, under both names. Counting both doubles a run."""
    import json as _json
    run_dir = RUNS / "2026-09-16_21-08-49_config-5.1__union-alpha"
    if not run_dir.is_dir():
        pytest.skip("run not present")
    names = set()
    for line in (run_dir / "events.jsonl").open():
        if '"turn_usage"' in line or '"llm_request_usage"' in line:
            try:
                names.add(_json.loads(line).get("type"))
            except ValueError:
                pass
    assert names == {"llm_request_usage", "turn_usage"}, (
        f"this run no longer carries both names ({names}) — the claim below is untested")
    # 3,758,611 is the count under ONE name. Both together would read 7,517,222.
    assert token_usage(run_dir)["prompt"] == 3_758_611


def test_token_cost_is_none_without_either_half():
    usage = {"prompt": 100, "cached": 0, "written": 0, "completion": 10}
    price = {"prompt": 1.0, "completion": 2.0}
    assert token_cost(None, price) is None
    assert token_cost(usage, None) is None
    # 100 prompt at $1/M + 10 completion at $2/M.
    assert token_cost(usage, price) == pytest.approx((100 * 1.0 + 10 * 2.0) / 1_000_000)


def test_cached_and_written_are_subsets_of_the_prompt_count():
    """Not extras added on top — the classic way to double-charge a cached run."""
    price = {"prompt": 10.0, "completion": 0.0, "input_cache_read": 1.0}
    all_fresh = {"prompt": 1_000_000, "cached": 0, "written": 0, "completion": 0}
    all_cached = {"prompt": 1_000_000, "cached": 1_000_000, "written": 0, "completion": 0}
    assert token_cost(all_fresh, price) == pytest.approx(10.0)
    assert token_cost(all_cached, price) == pytest.approx(1.0)
    # Were `cached` added rather than substituted, this would come to 11.0.
