"""The README must be exactly what the results files say (no hand-edited numbers)."""

import pytest

from earned_autonomy.paths import RESULTS, ROOT
from earned_autonomy.readme import render

NEEDED = [
    "real/gl_benchmark.json",
    "real/autonomy.json",
    "real/warehouse.json",
    "experiments.json",
    "metrics.json",
    "extractors.json",
]


@pytest.mark.skipif(not all((RESULTS / n).exists() for n in NEEDED), reason="results not generated")
def test_readme_matches_results():
    assert (ROOT / "README.md").read_text() == render(), "README.md is stale: run `uv run ea readme`"
