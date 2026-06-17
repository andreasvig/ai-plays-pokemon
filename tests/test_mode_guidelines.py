"""Mode-guidelines wiring: casual/custom runs get freeplay guidelines, official
(benchmark) runs get benchmark guidelines, injected into the TaskMaster prompt's
{{mode_guidelines}} placeholder.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agent.task_master import _mode_guidelines
from src.core.prompts import fill_prompt

TM_CFG = {
    "freeplay_guidelines": "# Freeplay Mode: TRUE\nexplore freely.",
    "benchmark_guidelines": "# Benchmark Mode: TRUE\ngo fast.",
}


def test_freeplay_mode_selects_freeplay_guidelines():
    assert _mode_guidelines({**TM_CFG, "mode": "freeplay"}) == TM_CFG["freeplay_guidelines"]


def test_benchmark_mode_selects_benchmark_guidelines():
    assert _mode_guidelines({**TM_CFG, "mode": "benchmark"}) == TM_CFG["benchmark_guidelines"]


def test_unset_mode_defaults_to_benchmark():
    # A direct config load with no mode set must NOT accidentally go freeplay.
    assert _mode_guidelines(dict(TM_CFG)) == TM_CFG["benchmark_guidelines"]


def test_missing_guidelines_key_is_empty_string():
    assert _mode_guidelines({"mode": "freeplay"}) == ""


def test_fill_prompt_substitutes_placeholder():
    template = "# Output\n...\n{{mode_guidelines}}\n# Guidelines\n- be decisive."
    filled = fill_prompt(template, mode_guidelines=_mode_guidelines({**TM_CFG, "mode": "freeplay"}))
    assert "# Freeplay Mode: TRUE" in filled
    assert "{{mode_guidelines}}" not in filled
    assert "# Benchmark Mode: TRUE" not in filled
