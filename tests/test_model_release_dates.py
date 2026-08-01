"""Release dates on the picker rows (Andreas 2026-08-01).

The model picker orders newest-first, which needs a release date the registry
doesn't carry — `observed.<tier>.last_updated` is when we last benchmarked a
tier, a different fact, and absent entirely for models nobody has run. The
dates come from OpenRouter's catalog via scripts/sync_model_release_dates.py
into configs/model_release_dates.json.

Covers: the field reaches the picker rows, the generated file stays in sync
with the registry, and a missing/corrupt file degrades to an unsorted picker
rather than a broken dialog.
"""

import json

from src.app.catalog import RELEASE_DATES_PATH, _load_release_dates, list_models
from src.config import _load_models_registry

ISO = 10  # len("YYYY-MM-DD")


def test_every_registry_model_has_a_release_date():
    """A model missing here sorts last in the picker — catch it at the source."""
    registry = _load_models_registry()
    dates = _load_release_dates()
    missing = sorted(set(registry) - set(dates))
    assert missing == [], (
        f"no release date for {missing}. Re-run: python scripts/sync_model_release_dates.py"
    )


def test_no_stale_entries_for_models_that_left_the_registry():
    registry = _load_models_registry()
    extra = sorted(set(_load_release_dates()) - set(registry))
    assert extra == [], f"stale release dates for removed models: {extra}"


def test_dates_are_iso_and_plausible():
    for name, value in _load_release_dates().items():
        assert len(value) == ISO and value[4] == "-" and value[7] == "-", (
            f"{name}: {value!r} is not YYYY-MM-DD"
        )
        # No model in this registry predates the modern era, and none ships from
        # the future — either would mean the sync read the wrong field.
        assert "2024-01-01" < value < "2030-01-01", f"{name}: implausible date {value}"


def test_picker_rows_carry_released():
    rows = list_models()
    assert rows, "no models projected"
    assert all("released" in r for r in rows), "released missing from the picker shape"
    dated = [r for r in rows if r["released"]]
    assert len(dated) == len(rows)


def test_newest_first_ordering_is_derivable_from_the_rows():
    """The sort the UI performs, asserted on real data rather than a fixture."""
    rows = list_models()
    order = sorted(rows, key=lambda r: (r["released"] or "", r["model"]), reverse=True)
    assert order[0]["released"] >= order[-1]["released"]
    # Distinct dates exist, so the sort is actually doing something — an
    # all-equal column would make this pass without ordering anything.
    assert len({r["released"] for r in rows}) > 1


def test_missing_file_degrades_to_empty_not_an_exception(tmp_path):
    assert _load_release_dates(tmp_path / "nope.json") == {}


def test_corrupt_file_degrades_to_empty(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert _load_release_dates(bad) == {}
    wrong_shape = tmp_path / "wrong.json"
    wrong_shape.write_text(json.dumps({"released": ["not", "a", "dict"]}))
    assert _load_release_dates(wrong_shape) == {}


def test_generated_file_is_marked_generated():
    """It is derived data next to hand-maintained config — say so in the file."""
    raw = json.loads(RELEASE_DATES_PATH.read_text())
    assert "sync_model_release_dates.py" in raw.get("_comment", "")
