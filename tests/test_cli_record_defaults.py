"""Recording is the default at every human entry point (Andreas, 2026-09-09).

The glm-5.3-flash(low) official run went online with no video because the
Add-run box was unticked; the CLIs defaulted off too. Now `pokemon queue add`
sends a record spec unless `--no-record`, with the view following the kind and
the speed matching the dialog (`cut-thinking`, the shape every published clip
has). Driven through the real argparse + `_cmd_add` with the HTTP call stubbed.
"""

import sys

import pytest

from src.cli import queue as q


@pytest.fixture
def posted(monkeypatch):
    calls = []

    def fake_api(method, path, *, port, body=None):
        calls.append((method, path, body))
        # Echo the stored items the way the server does — the CLI prints kind/model off them.
        return 201, {"items": [{"queue_id": f"q_{i}", **item} for i, item in enumerate(body["items"])]}

    monkeypatch.setattr(q, "api", fake_api)
    return calls


def _run(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["pokemon queue", *argv])
    try:
        q.main()
    except SystemExit as exc:  # main() exits with the command's return code
        assert exc.code in (0, None), exc.code


def test_official_add_records_the_full_panel_by_default(monkeypatch, posted):
    _run(monkeypatch, ["add", "gemini-3.8-flash(minimal)", "--kind", "official"])
    (spec,) = posted[-1][2]["items"]
    assert spec["record"]["view"] == "detailed" and spec["record"]["speed"] == "cut-thinking"
    assert spec["record"]["fps"] == 30


def test_casual_add_records_the_simple_view_by_default(monkeypatch, posted):
    _run(monkeypatch, ["add", "gemini-3.8-flash(minimal)", "--kind", "casual", "--max-turns", "5"])
    (spec,) = posted[-1][2]["items"]
    assert spec["record"]["view"] == "simple" and spec["record"]["speed"] == "cut-thinking"


def test_no_record_sends_no_spec(monkeypatch, posted):
    _run(monkeypatch, ["add", "gemini-3.8-flash(minimal)", "--kind", "official", "--no-record"])
    (spec,) = posted[-1][2]["items"]
    assert "record" not in spec


def test_an_explicit_view_and_speed_still_win(monkeypatch, posted):
    _run(monkeypatch, ["add", "m(low)", "--kind", "official", "--record", "both", "--record-speed", "realtime"])
    (spec,) = posted[-1][2]["items"]
    assert spec["record"]["view"] == "both" and spec["record"]["speed"] == "realtime"
