"""Earned autonomy: decide, from audited evidence, where an agent may act without a human.

The rule: an action type (auto-APPROVE, auto-REJECT, auto-post GL) is unlocked above a score
threshold only when the *upper* 95% Wilson bound of its error rate on audited gold data is
inside the error budget. Little evidence means a wide interval and no autonomy; as audited
shadow-mode data accumulates, the interval tightens and autonomy is earned.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

GRID = np.round(np.concatenate([np.arange(0.50, 0.95, 0.01), np.arange(0.95, 1.0001, 0.0025)]), 4)


def wilson_interval(errors: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 1.0
    p = errors / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def choose_threshold(
    conf: np.ndarray, correct: np.ndarray, budget: float, min_n: int = 50, conservative: bool = True
) -> float | None:
    """Lowest threshold whose error rate is within budget (max automation).

    conservative=True uses the Wilson upper bound (earned autonomy); False uses the point
    estimate, which is what most teams do and which over-trusts small samples.
    """
    conf, correct = np.asarray(conf, float), np.asarray(correct, bool)
    for tau in GRID:
        sel = conf >= tau
        n = int(sel.sum())
        if n < min_n:
            return None  # thresholds only get stricter from here, so evidence only shrinks
        errors = int((~correct[sel]).sum())
        bound = wilson_interval(errors, n)[1] if conservative else errors / n
        if bound <= budget:
            return float(tau)
    return None


@dataclass
class Budgets:
    approve: float = 0.01  # at most 1% of auto-approvals may be wrong (upper 95% bound)
    reject: float = 0.03  # auto-rejects are noisier for employees but recoverable
    gl: float = 0.01


@dataclass
class Costs:
    review_minutes: float = 4.0
    reviewer_hourly: float = 60.0
    wrongful_reject: float = 30.0  # employee + manager time to appeal

    @property
    def per_review(self) -> float:
        return self.review_minutes / 60 * self.reviewer_hourly


def leakage(rows: pd.DataFrame) -> float:
    """Dollars auto-approved that policy says should not have been (REJECT gold = full amount,
    NEEDS_REVIEW gold = half: some would have been approved after review anyway)."""
    bad = rows[rows.gold_decision != "APPROVE"]
    w = np.where(bad.gold_decision == "REJECT", 1.0, 0.5)
    return float((bad.amount.astype(float) * w).sum())


HIGH_VALUE = 500.0  # approvals at or above this amount must earn their own threshold


def route(
    df: pd.DataFrame,
    tau_approve: float | None,
    tau_reject: float | None,
    novelty_guard: bool = False,
    tau_approve_high: float | None | str = "same",
) -> pd.Series:
    if tau_approve_high == "same":
        tau_approve_high = tau_approve
    high = df.amount >= HIGH_VALUE
    ok_low = ~high & (tau_approve is not None) & (df.confidence >= (tau_approve or 2))
    ok_high = high & (tau_approve_high is not None) & (df.confidence >= (tau_approve_high or 2))
    auto_a = (df.decision == "APPROVE") & (ok_low | ok_high)
    auto_r = (df.decision == "REJECT") & (tau_reject is not None) & (df.confidence >= (tau_reject or 2))
    auto = auto_a | auto_r
    if novelty_guard:
        auto &= ~df.is_novel
    return pd.Series(np.where(auto, "auto", "human"), index=df.index)


def outcome(df: pd.DataFrame, routes: pd.Series, costs: Costs) -> dict:
    auto = df[routes == "auto"]
    wrong = auto[auto.decision != auto.gold_decision]
    wrong_approve = wrong[wrong.decision == "APPROVE"]
    wrong_reject = wrong[wrong.decision == "REJECT"]
    lo, hi = wilson_interval(len(wrong), len(auto))
    n_human = int((routes == "human").sum())
    leak = leakage(wrong_approve)
    return dict(
        n=int(len(df)),
        auto=int(len(auto)),
        automation_rate=len(auto) / max(len(df), 1),
        auto_errors=int(len(wrong)),
        auto_error_rate=len(wrong) / max(len(auto), 1),
        auto_error_ci=[lo, hi],
        wrong_auto_approvals=int(len(wrong_approve)),
        wrong_auto_rejects=int(len(wrong_reject)),
        leaked_dollars=round(leak, 2),
        human_queue=n_human,
        total_cost=round(n_human * costs.per_review + leak + len(wrong_reject) * costs.wrongful_reject, 2),
    )


def simulate_rollout(
    df: pd.DataFrame,
    audit_mask: pd.Series,
    label_col: str,
    shadow_months: list[int],
    live_months: list[int],
    budgets: Budgets,
    costs: Costs,
    min_n: int = 50,
    conservative: bool = True,
    novelty_guard: bool = False,
    tiered: bool = False,
) -> tuple[pd.DataFrame, pd.Series]:
    """Month-by-month rollout. Thresholds for month m use only audited data from earlier months.

    `label_col` is the column treated as truth when *choosing* thresholds (audited gold, or
    reviewer history for the counterfactual). Outcomes are always scored against gold.
    """
    rows = []
    routes = pd.Series("human", index=df.index)
    for m in shadow_months + live_months:
        past = df[(df.month_index < m) & (df.month_index >= shadow_months[0]) & audit_mask]
        past = past[past[label_col].notna()]
        if novelty_guard:
            past = past[~past.is_novel]  # thresholds are earned on the traffic they will govern
        tau_h: float | None | str = "same"
        if m in shadow_months:
            tau_a = tau_r = None
        else:
            pa = past[past.decision == "APPROVE"]
            pr = past[past.decision == "REJECT"]
            if tiered:
                lo, hi = pa[pa.amount < HIGH_VALUE], pa[pa.amount >= HIGH_VALUE]
                tau_a = choose_threshold(lo.confidence, lo.decision == lo[label_col], budgets.approve, min_n, conservative)
                tau_h = choose_threshold(hi.confidence, hi.decision == hi[label_col], budgets.approve, min_n, conservative)
            else:
                tau_a = choose_threshold(pa.confidence, pa.decision == pa[label_col], budgets.approve, min_n, conservative)
            tau_r = choose_threshold(pr.confidence, pr.decision == pr[label_col], budgets.reject, 20, conservative)
        cur = df[df.month_index == m]
        r = route(cur, tau_a, tau_r, novelty_guard, tau_h)
        routes.loc[cur.index] = r
        o = outcome(cur, r, costs)
        rows.append(
            dict(
                month_index=m,
                phase="shadow" if m in shadow_months else "live",
                audited_so_far=int(len(past)),
                tau_approve=tau_a,
                tau_approve_high=None if tau_h == "same" else tau_h,
                tau_reject=tau_r,
                novel_held=int(cur.is_novel.sum()) if novelty_guard else 0,
                **o,
            )
        )
    return pd.DataFrame(rows), routes


def gl_rollout(
    df: pd.DataFrame,
    audit_mask: pd.Series,
    shadow_months: list[int],
    live_months: list[int],
    budget: float,
    min_n: int = 50,
    novelty_guard: bool = True,
) -> tuple[pd.DataFrame, pd.Series]:
    """Same trustgate rule for auto-posting GL codes."""
    rows, routes = [], pd.Series("human", index=df.index)
    for m in live_months:
        past = df[(df.month_index < m) & (df.month_index >= shadow_months[0]) & audit_mask]
        if novelty_guard:
            past = past[~past.is_novel]
        tau = choose_threshold(past.gl_confidence, past.gl_code == past.gold_gl, budget, min_n)
        cur = df[df.month_index == m]
        auto = cur.gl_confidence >= tau if tau is not None else pd.Series(False, index=cur.index)
        if novelty_guard:
            auto &= ~cur.is_novel
        routes.loc[cur.index[auto.to_numpy()]] = "auto"
        a = cur[auto]
        err = int((a.gl_code != a.gold_gl).sum())
        rows.append(
            dict(
                month_index=m,
                tau=tau,
                n=len(cur),
                auto=len(a),
                coverage=len(a) / max(len(cur), 1),
                auto_errors=err,
                auto_error_rate=err / max(len(a), 1),
                overall_accuracy=float((cur.gl_code == cur.gold_gl).mean()),
            )
        )
    return pd.DataFrame(rows), routes


def sweep(df: pd.DataFrame, action: str = "APPROVE") -> pd.DataFrame:
    """Automation vs error trade-off for one action across thresholds (for charts)."""
    sub = df[df.decision == action]
    out = []
    for tau in GRID:
        sel = sub[sub.confidence >= tau]
        err = int((sel.decision != sel.gold_decision).sum())
        lo, hi = wilson_interval(err, len(sel))
        out.append(
            dict(
                tau=float(tau),
                automation_rate=len(sel) / max(len(df), 1),
                n=len(sel),
                error_rate=err / max(len(sel), 1),
                ci_lo=lo,
                ci_hi=hi,
            )
        )
    return pd.DataFrame(out)
