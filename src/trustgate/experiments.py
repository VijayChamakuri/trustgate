"""Robustness experiments for the synthetic policy benchmark.

Every rate the generator plants is an assumption, so conclusions must survive changing them:

  seeds        10 independent worlds with default assumptions -> mean +/- sd per policy
  sensitivity  one assumption at a time (reviewer leniency, memo drift, audit size, error
               budget), 3 seeds each -> does the ranking of autonomy policies hold?
  ablations    the final policy with one safeguard removed (reported per seed)
"""

from __future__ import annotations

import json
import time
from typing import Any

import numpy as np

from . import pipeline
from .autonomy import Budgets
from .evaluate import evaluate
from .load import load
from .paths import RESULTS

POLICIES = [
    "trust_engine_everywhere",
    "thresholds_from_reviewer_history",
    "point_estimate_thresholds",
    "earned_without_novelty_guard",
    "earned_without_value_tiers",
    "earned_autonomy",
    "ablation_without_wilson",
    "ablation_without_novelty_guard",
]
KEYS = ["automation_rate", "auto_error_rate", "leaked_dollars", "total_cost"]


def run_world(
    seed: int, drift_rate: float = 0.35, leniency: float = 0.06, audit_per_month: float = 500, approve_budget: float = 0.01
) -> dict:
    load(seed, drift_rate=drift_rate, leniency=leniency)
    pipeline.dbt("tag:pre_agent")
    res = pipeline.run_resolver()
    pipeline.dbt("tag:post_resolution")
    df = pipeline.run_agents("rules")
    _, m = evaluate(df, res, budgets=Budgets(approve=approve_budget), audit_per_month=int(audit_per_month), write_outputs=False)
    a = m["autonomy"]
    return {p: {k: float(a[p][k]) for k in KEYS} for p in POLICIES} | {
        "_meta": {
            "seed": seed,
            "drift_rate": drift_rate,
            "leniency": leniency,
            "audit_per_month": audit_per_month,
            "approve_budget": approve_budget,
            "engine_accuracy": m["policy_engine"]["live_accuracy"],
            "verifier_auc": m["verifier"]["live_auc"],
        }
    }


def _agg(runs: list[dict]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for p in POLICIES:
        out[p] = {
            k: {
                "mean": float(np.mean([r[p][k] for r in runs])),
                "sd": float(np.std([r[p][k] for r in runs], ddof=1)) if len(runs) > 1 else 0.0,
            }
            for k in KEYS
        }
    out["within_budget_share"] = float(
        np.mean([r["earned_autonomy"]["auto_error_rate"] <= r["_meta"]["approve_budget"] for r in runs])
    )
    return out


SWEEPS: dict[str, list[float]] = {
    "leniency": [0.0, 0.03, 0.06, 0.10, 0.15],
    "drift_rate": [0.0, 0.15, 0.35, 0.50],
    "audit_per_month": [100, 250, 500, 1000],
    "approve_budget": [0.005, 0.01, 0.02],
}


def run(n_seeds: int = 10, sweep_seeds: int = 3, log=print) -> dict:
    t0 = time.time()
    seeds = [run_world(s) for s in range(n_seeds)]
    log(f"[experiments] {n_seeds} seeds done in {time.time() - t0:.0f}s")
    out: dict[str, Any] = {"n_seeds": n_seeds, "seeds": _agg(seeds), "seed_runs": seeds, "sensitivity": {}}
    for param, values in SWEEPS.items():
        out["sensitivity"][param] = []
        for v in values:
            runs = [run_world(s, **{param: v}) for s in range(sweep_seeds)]
            out["sensitivity"][param].append({"value": v, **_agg(runs)})
            log(f"[experiments] {param}={v} done ({time.time() - t0:.0f}s)")
    out["seconds"] = round(time.time() - t0, 1)
    (RESULTS / "experiments.json").write_text(json.dumps(out, indent=2))
    return out


def compare_extractors(seed: int = 7, backends: tuple[str, ...] = ("rules", "rules+embed")) -> dict:
    """Same world, same rules engine, same verifier and audits; only perception changes."""
    load(seed)
    pipeline.dbt("tag:pre_agent")
    res = pipeline.run_resolver()
    pipeline.dbt("tag:post_resolution")
    out = {}
    for b in backends:
        df = pipeline.run_agents(b)
        _, m = evaluate(df, res, write_outputs=False)
        out[b] = {
            "signals": m["extractor_quality"],
            "engine_accuracy": m["policy_engine"]["live_accuracy"],
            "verifier_auc": m["verifier"]["live_auc"],
            "earned_autonomy": {k: float(m["autonomy"]["earned_autonomy"][k]) for k in KEYS},
        }
    (RESULTS / "extractors.json").write_text(json.dumps(out, indent=2))
    return out
