"""Filesystem layout. Everything is relative to the repo root so the project runs anywhere."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
GOLD_DIR = DATA / "gold"
RESULTS = ROOT / "results"
WAREHOUSE = ROOT / "warehouse"
POLICY_MD = ROOT / "policy" / "expense_policy.md"
DB_PATH = Path(os.environ.get("EA_DB_PATH", DATA / "warehouse.duckdb")).resolve()

for d in (DATA, GOLD_DIR, RESULTS):
    d.mkdir(parents=True, exist_ok=True)
