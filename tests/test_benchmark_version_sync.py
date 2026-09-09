"""The version badge on the site and the season marker on the runs are one value.

``OFFICIAL_BENCHMARK_VERSION`` (executor) is stamped on every finished official
run; ``BENCH_VERSION`` (frontend) is what the top bar, the hero and the
changelog show. Two literals in two languages — this test is the only thing
holding them together (2026-09-09, the v1 → v1.1 bump).
"""

import re
from pathlib import Path

from src.app.executor import OFFICIAL_BENCHMARK_VERSION

REPO = Path(__file__).resolve().parents[1]
VERSION_JS = REPO / "src" / "dashboard" / "web" / "src" / "lib" / "version.js"


def _js_const(name: str) -> str:
    m = re.search(rf"export const {name} = '([^']+)'", VERSION_JS.read_text())
    assert m, f"{name} not found in {VERSION_JS}"
    return m.group(1)


def test_frontend_version_matches_the_executor_stamp():
    assert _js_const("BENCH_VERSION") == OFFICIAL_BENCHMARK_VERSION


def test_hero_label_is_the_same_version_spelled_for_people():
    # 'pokebench-v1.1' → 'PokeBench v1.1'
    assert _js_const("BENCH_LABEL") == "PokeBench " + OFFICIAL_BENCHMARK_VERSION.split("-", 1)[1]


def test_changelog_leads_with_the_current_version():
    text = VERSION_JS.read_text()
    first = re.search(r"CHANGELOG = \[\s*\{\s*version: '([^']+)'", text)
    assert first and first.group(1) == OFFICIAL_BENCHMARK_VERSION
