"""Evaluation harness. The only module that reads gold labels.

Time-based splits (no shuffling, no leakage):
  months 0-5   TRAIN   verifier + GL coder learn from historical reviewer outcomes
  months 6-7   SHADOW  agent runs, takes no action; a random audit sample is labelled
  months 8-11  LIVE    autonomy is unlocked month by month from audited evidence only
"""

from __future__ import annotations

import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from sklearn.metrics import brier_score_loss, roc_auc_score  # noqa: E402

from .autonomy import Budgets, Costs, gl_rollout, outcome, simulate_rollout, sweep, wilson_interval  # noqa: E402
from .gl import default_gl_baseline  # noqa: E402
from .load import read_gold  # noqa: E402
from .paths import RESULTS  # noqa: E402
from .resolve import evaluate_resolutions  # noqa: E402

SHADOW, LIVE = [6, 7], [8, 9, 10, 11]
AUDIT_PER_MONTH = 500
MONTH_NAMES = {
    i: n
    for i, n in enumerate(
        ["Nov 25", "Dec 25", "Jan 26", "Feb 26", "Mar 26", "Apr 26", "May 26", "Jun 26", "Jul 26", "Aug 26", "Sep 26", "Oct 26"]
    )
}


def audit_sample(df: pd.DataFrame, seed: int = 11, per_month: int = AUDIT_PER_MONTH) -> pd.Series:
    rng = np.random.default_rng(seed)
    mask = pd.Series(False, index=df.index)
    for m in SHADOW + LIVE:
        idx = df.index[df.month_index == m]
        take = rng.choice(idx, size=min(per_month, len(idx)), replace=False)
        mask.loc[take] = True
    return mask


def summarize(rollout: pd.DataFrame) -> dict:
    live = rollout[rollout.phase == "live"]
    n, auto, err = live.n.sum(), live.auto.sum(), live.auto_errors.sum()
    lo, hi = wilson_interval(int(err), int(auto))
    return dict(
        automation_rate=float(auto / n),
        auto_errors=int(err),
        auto_error_rate=float(err / max(auto, 1)),
        auto_error_ci=[lo, hi],
        leaked_dollars=float(live.leaked_dollars.sum()),
        wrong_auto_rejects=int(live.wrong_auto_rejects.sum()),
        human_queue=int(live.human_queue.sum()),
        total_cost=float(live.total_cost.sum()),
    )


def static_policy(df: pd.DataFrame, rule: str, costs: Costs) -> dict:
    live = df[df.month_index.isin(LIVE)]
    if rule == "human_all":
        r = pd.Series("human", index=live.index)
    else:  # engine_all: execute every APPROVE / REJECT the engine produces
        r = pd.Series(np.where(live.decision.isin(["APPROVE", "REJECT"]), "auto", "human"), index=live.index)
    o = outcome(live, r, costs)
    return dict(
        automation_rate=o["automation_rate"],
        auto_errors=o["auto_errors"],
        auto_error_rate=o["auto_error_rate"],
        auto_error_ci=o["auto_error_ci"],
        leaked_dollars=o["leaked_dollars"],
        wrong_auto_rejects=o["wrong_auto_rejects"],
        human_queue=o["human_queue"],
        total_cost=o["total_cost"],
    )


def extractor_quality(live: pd.DataFrame) -> dict:
    meals = live[live.meal_type.notna()]
    air = live[live.fare.notna()]
    hotels = live[live.nightly_rate.notna()]
    personal_memo = live[live.personal & ~live.merchant_kind.eq("personal")]

    def pr(pred, true):
        tp = int((pred & true).sum())
        return dict(precision=tp / max(int(pred.sum()), 1), recall=tp / max(int(true.sum()), 1), support=int(true.sum()))

    group_meals = meals[meals.meal_type.isin(["client", "team"])]
    return {
        "alcohol_on_meals": pr(meals.sig_has_alcohol.astype(bool), meals.has_alcohol.astype(bool)),
        "premium_cabin": pr(air.sig_premium_cabin.astype(bool), air.fare.eq("business")),
        "personal_memo": pr(
            live.sig_personal_hint.astype(bool) & live.index.isin(personal_memo.index), live.index.isin(personal_memo.index)
        ),
        "meal_type_accuracy": float((meals.sig_meal_type == meals.meal_type).mean()),
        "attendee_count_parsed": float((group_meals.sig_attendees == group_meals.attendees).mean()),
        "nightly_rate_parsed": float((hotels.sig_nightly_rate.sub(hotels.nightly_rate).abs() < 1).mean()),
    }


def section_recall(live: pd.DataFrame) -> dict:
    out = {}
    for sec in sorted({s for r in live.gold_reasons for s in r.split("|") if s}):
        has = live.gold_reasons.str.split("|").apply(lambda xs, sec=sec: sec in xs)
        cited = live.cited_sections.str.split("|").apply(lambda xs, sec=sec: sec in xs)
        tp = int((has & cited).sum())
        out[sec] = dict(support=int(has.sum()), recall=tp / max(int(has.sum()), 1), precision=tp / max(int(cited.sum()), 1))
    return out


def failure_examples(live: pd.DataFrame, routes: pd.Series) -> list[dict]:
    auto_wrong = live[(routes.loc[live.index] == "auto") & (live.decision != live.gold_decision)]
    cols = [
        "txn_id",
        "descriptor_raw",
        "amount",
        "memo",
        "receipt_text",
        "decision",
        "confidence",
        "gold_decision",
        "gold_reasons",
    ]
    return auto_wrong.sort_values("amount", ascending=False)[cols].head(12).to_dict("records")


def miss_buckets(live: pd.DataFrame) -> list[dict]:
    """Engine said APPROVE but policy was violated: what kind of perception failure was it?"""
    miss = live[(live.decision == "APPROVE") & (live.gold_decision != "APPROVE")]
    out = []
    for sec, grp in miss.groupby(miss.gold_reasons.str.split("|").str[0]):
        ex = grp.iloc[0]
        out.append(
            dict(
                section=sec,
                count=len(grp),
                example_memo=ex.memo,
                example_receipt=ex.receipt_text,
                example_descriptor=ex.descriptor_raw,
                median_confidence=float(grp.confidence.median()),
            )
        )
    return sorted(out, key=lambda d: -d["count"])


def charts(sweep_df: pd.DataFrame, rollouts: dict[str, pd.DataFrame], live: pd.DataFrame, budgets: Budgets) -> None:
    plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False})
    # 1. Trade-off curve
    fig, ax = plt.subplots(figsize=(7, 4.2))
    s = sweep_df[sweep_df.n >= 100]
    ax.plot(s.automation_rate * 100, s.error_rate * 100, color="#2b6cb0", lw=2, label="auto-approve error rate")
    ax.fill_between(s.automation_rate * 100, s.ci_lo * 100, s.ci_hi * 100, color="#2b6cb0", alpha=0.15, label="95% Wilson CI")
    ax.axhline(budgets.approve * 100, color="#c53030", ls="--", lw=1.2, label=f"error budget ({budgets.approve:.0%})")
    ax.set_xlabel("% of all transactions auto-approved")
    ax.set_ylabel("% of auto-approvals that were wrong")
    ax.set_title("Automation vs. error (live months, rules extractor)")
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(RESULTS / "tradeoff_curve.png", dpi=160)
    plt.close(fig)

    # 2. Rollout by month
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(10, 4))
    styles = {
        "earned autonomy (final)": "#2b6cb0",
        "Wilson bound only": "#63b3ed",
        "point estimate (no CI)": "#dd6b20",
        "thresholds from reviewer history": "#718096",
    }
    for name, ro in rollouts.items():
        x = [MONTH_NAMES[m] for m in ro.month_index]
        a1.plot(x, ro.automation_rate * 100, marker="o", color=styles[name], label=name)
        a2.plot(x, ro.auto_error_rate * 100, marker="o", color=styles[name], label=name)
    a2.axhline(budgets.approve * 100, color="#c53030", ls="--", lw=1)
    a1.set_title("Autonomy earned per month")
    a1.set_ylabel("% transactions auto-actioned")
    a2.set_title("Realized error on auto-actions")
    a2.set_ylabel("% wrong")
    for a in (a1, a2):
        a.axvspan(-0.5, 1.5, color="#edf2f7", zorder=0)
        a.text(0.5, a.get_ylim()[1] * 0.92, "shadow", ha="center", color="#4a5568", fontsize=9)
    a1.legend(frameon=False, fontsize=8)
    fig.tight_layout()
    fig.savefig(RESULTS / "rollout_by_month.png", dpi=160)
    plt.close(fig)

    # 3. Reliability diagram
    fig, ax = plt.subplots(figsize=(4.6, 4.2))
    correct = (live.decision == live.gold_decision).astype(float)
    bins = np.linspace(0, 1, 11)
    idx = np.clip(np.digitize(live.confidence, bins) - 1, 0, 9)
    xs, ys, ns = [], [], []
    for b in range(10):
        sel = idx == b
        if sel.sum() >= 15:
            xs.append(live.confidence[sel].mean())
            ys.append(correct[sel].mean())
            ns.append(sel.sum())
    ax.plot([0, 1], [0, 1], color="#a0aec0", ls="--", lw=1)
    ax.scatter(xs, ys, s=np.sqrt(ns) * 4, color="#2b6cb0")
    ax.set_xlabel("verifier confidence")
    ax.set_ylabel("observed accuracy")
    ax.set_title("Verifier calibration (live)")
    fig.tight_layout()
    fig.savefig(RESULTS / "calibration.png", dpi=160)
    plt.close(fig)


def evaluate(
    df: pd.DataFrame,
    resolutions: pd.DataFrame,
    budgets: Budgets | None = None,
    costs: Costs | None = None,
    audit_per_month: int = AUDIT_PER_MONTH,
    write_outputs: bool = True,
):
    budgets, costs = budgets or Budgets(), costs or Costs()
    gold = read_gold()
    df = df.merge(gold.drop(columns=["month_index"]), on="txn_id", how="left")
    audit = audit_sample(df, per_month=audit_per_month)
    live = df[df.month_index.isin(LIVE)]

    earned, routes = simulate_rollout(df, audit, "gold_decision", SHADOW, LIVE, budgets, costs, novelty_guard=True, tiered=True)
    untiered, _ = simulate_rollout(df, audit, "gold_decision", SHADOW, LIVE, budgets, costs, novelty_guard=True)
    unguarded, _ = simulate_rollout(df, audit, "gold_decision", SHADOW, LIVE, budgets, costs)
    point, _ = simulate_rollout(df, audit, "gold_decision", SHADOW, LIVE, budgets, costs, conservative=False)
    # drop-one ablations of the final policy
    no_wilson, _ = simulate_rollout(
        df, audit, "gold_decision", SHADOW, LIVE, budgets, costs, conservative=False, novelty_guard=True, tiered=True
    )
    no_novelty, _ = simulate_rollout(df, audit, "gold_decision", SHADOW, LIVE, budgets, costs, tiered=True)
    hist_mask = df.month_index.isin(SHADOW)  # reviewer labels exist only up to month 7
    history, _ = simulate_rollout(df, hist_mask, "review_decision", SHADOW, LIVE, budgets, costs)
    rollouts = {
        "earned autonomy (final)": earned,
        "Wilson bound only": unguarded,
        "point estimate (no CI)": point,
        "thresholds from reviewer history": history,
    }

    gl_ro, gl_routes = gl_rollout(df, audit, SHADOW, LIVE, budgets.gl)
    gl_ro_unguarded, _ = gl_rollout(df, audit, SHADOW, LIVE, budgets.gl, novelty_guard=False)
    df["route"], df["gl_route"] = routes, gl_routes

    truth = (
        df.groupby("descriptor_clean")
        .agg(true_merchant_id=("true_merchant_id", lambda s: s.mode()[0]), true_in_registry=("true_in_registry", "first"))
        .reset_index()
    )
    shadow_live = df[df.month_index.isin(SHADOW)]
    metrics = {
        "data": {
            "transactions": int(len(df)),
            "live_transactions": int(len(live)),
            "audited_per_month": AUDIT_PER_MONTH,
            "extractor": str(df.extractor.iloc[0]),
        },
        "budgets": budgets.__dict__,
        "costs": {**costs.__dict__, "per_review": costs.per_review},
        "merchant_resolution": evaluate_resolutions(resolutions, truth),
        "policy_engine": {
            "live_accuracy": float((live.decision == live.gold_decision).mean()),
            "confusion": pd.crosstab(live.gold_decision, live.decision).to_dict(),
            "section_recall": section_recall(live),
        },
        "verifier": {
            "live_auc": float(roc_auc_score(live.decision == live.gold_decision, live.confidence)),
            "live_brier": float(brier_score_loss(live.decision == live.gold_decision, live.confidence)),
            "shadow_auc": float(roc_auc_score(shadow_live.decision == shadow_live.gold_decision, shadow_live.confidence)),
        },
        "extractor_quality": extractor_quality(live),
        "autonomy": {
            "human_reviews_everything": static_policy(df, "human_all", costs),
            "trust_engine_everywhere": static_policy(df, "engine_all", costs),
            "point_estimate_thresholds": summarize(point),
            "thresholds_from_reviewer_history": summarize(history),
            "earned_without_novelty_guard": summarize(unguarded),
            "earned_without_value_tiers": summarize(untiered),
            "earned_autonomy": summarize(earned),
            "ablation_without_wilson": summarize(no_wilson),
            "ablation_without_novelty_guard": summarize(no_novelty),
            "ablation_without_value_tiers": summarize(untiered),
            "novel_share_live": float(live.is_novel.mean()),
            "earned_by_month": earned.to_dict("records"),
        },
        "gl_coding": {
            "live_accuracy_model": float((live.gl_code == live.gold_gl).mean()),
            "live_accuracy_merchant_lookup": float((default_gl_baseline(live) == live.gold_gl).mean()),
            "shadow_accuracy_model": float((shadow_live.gl_code == shadow_live.gold_gl).mean()),
            "by_month": gl_ro.to_dict("records"),
            "auto_post_coverage": float(gl_ro.auto.sum() / gl_ro.n.sum()),
            "auto_post_error_rate": float(gl_ro.auto_errors.sum() / max(gl_ro.auto.sum(), 1)),
            "unguarded_auto_post_coverage": float(gl_ro_unguarded.auto.sum() / gl_ro_unguarded.n.sum()),
            "unguarded_auto_post_error_rate": float(gl_ro_unguarded.auto_errors.sum() / max(gl_ro_unguarded.auto.sum(), 1)),
        },
        "failure_analysis": {"engine_misses": miss_buckets(live), "wrong_auto_actions": failure_examples(live, df.route)},
    }
    if write_outputs:
        charts(sweep(live, "APPROVE"), rollouts, live, budgets)
        earned.to_csv(RESULTS / "rollout_earned.csv", index=False)
        (RESULTS / "metrics.json").write_text(json.dumps(metrics, indent=2, default=_json_default))
    return df, metrics


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return None if np.isnan(o) else float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if o is None or (isinstance(o, float) and np.isnan(o)):
        return None
    return str(o)
