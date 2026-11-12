"""The README and RESULTS.md must be exactly what the results files say."""

import pytest

from trustgate.paths import RESULTS, ROOT
from trustgate.readme import render, render_results

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
    assert (ROOT / "README.md").read_text() == render(), "README.md is stale: run `uv run trustgate readme`"
    assert (ROOT / "docs" / "RESULTS.md").read_text() == render_results(), (
        "docs/RESULTS.md is stale: run `uv run trustgate readme`"
    )
