"""Charts for the real-data track (written to results/real/*.png)."""

from __future__ import annotations

import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from .paths import RESULTS  # noqa: E402

POL = [
    ("auto_all", "Auto-post\neverything"),
    ("point_estimate", "Point-estimate\nthreshold"),
    ("wilson", "Wilson\nbound"),
    ("wilson+novelty", "+ novelty\nguard"),
    ("wilson+novelty+tiers", "+ value tiers\n(earned)"),
]
BLUE, GREY, RED = "#2457c5", "#9aa3ad", "#b4372f"


def _style() -> None:
    plt.rcParams.update(
        {"font.size": 10, "axes.spines.top": False, "axes.spines.right": False, "axes.titleweight": "bold", "axes.titlesize": 11}
    )


def hero(auto: dict, budget: str = "0.02") -> None:
    """Same gate, two outcomes: automation granted and realised error, card-feed vs upstream."""
    _style()
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2), sharey=False)
    for ax, (setting, title) in zip(
        axes,
        [
            ("card_feed", "Card-feed signals only (model ≈ {acc} accurate)"),
            ("upstream", "With upstream procurement category (model ≈ {acc})"),
        ],
        strict=True,
    ):
        s = auto["settings"][setting]
        b = s["budgets"][budget]
        x = np.arange(len(POL))
        autos = [b[p]["automation_rate"] * 100 for p, _ in POL]
        errs = [b[p]["auto_error_rate"] * 100 for p, _ in POL]
        over = [b[p]["months_over_budget"] for p, _ in POL]
        bars = ax.bar(x, autos, color=[RED if o > 0 else BLUE for o in over], width=0.62)
        for xi, bar, e, o in zip(x, bars, errs, over, strict=True):
            label = "no autonomy\ngranted" if bar.get_height() == 0 else f"{e:.1f}% err\n{o}/{s['live_months']} mo over"
            ax.text(xi, bar.get_height() + 2, label, ha="center", va="bottom", fontsize=8, color="#333")
        ax.set_xticks(x, [lab for _, lab in POL], fontsize=8)
        ax.set_ylim(0, 118)
        ax.set_ylabel("% of real card lines auto-posted")
        ax.set_title(title.format(acc=f"{s['model_accuracy_live']:.0%}"))
    fig.suptitle(
        f"Same autonomy gate, real council bookings, {float(budget):.0%} error budget, {auto['settings']['upstream']['live_months']} live months"
        "  ·  red = broke the budget in at least one month",
        fontsize=10,
        color="#444",
    )
    fig.tight_layout()
    fig.savefig(RESULTS / "real" / "hero.png", dpi=170)
    plt.close(fig)


def per_year(bench: dict) -> None:
    _style()
    fig, ax = plt.subplots(figsize=(7, 3.8))
    rows = [r for r in bench["benchmark"]["per_year"] if r["setting"] == "card_feed"]
    for m, lab, c in [
        ("merchant_lookup", "merchant lookup", GREY),
        ("tfidf_logreg", "TF-IDF + LR", "#7aa2ff"),
        ("embed_knn", "embeddings kNN", "#56c28d"),
        ("tfidf+embed_logreg", "TF-IDF + embeddings + LR", BLUE),
    ]:
        rr = [r for r in rows if r["model"] == m]
        ax.plot([r["year"] for r in rr], [r["accuracy"] * 100 for r in rr], marker="o", label=lab, color=c)
    nm = [r for r in rows if r["model"] == "tfidf+embed_logreg"]
    ax2 = ax.twinx()
    ax2.bar([r["year"] for r in nm], [(1 - r["seen_merchant_share"]) * 100 for r in nm], alpha=0.12, color="#555", width=0.5)
    ax2.set_ylabel("% lines from never-seen merchants", color="#777")
    ax2.spines["top"].set_visible(False)
    ax.set_ylabel("accuracy (%)")
    ax.set_title("Rolling yearly GL-coding accuracy (train on all earlier years)")
    ax.legend(frameon=False, fontsize=8, loc="lower left")
    fig.tight_layout()
    fig.savefig(RESULTS / "real" / "accuracy_by_year.png", dpi=170)
    plt.close(fig)


def confidence(auto: dict) -> None:
    _style()
    fig, ax = plt.subplots(figsize=(5.4, 3.8))
    for setting, lab, c in [("card_feed", "card feed", RED), ("upstream", "upstream", BLUE)]:
        rows = auto["settings"][setting]["accuracy_by_confidence"]
        ax.plot([r["min_conf"] for r in rows], [r["error"] * 100 for r in rows], marker="o", label=lab, color=c)
    ax.axhline(2, ls="--", color="#777", lw=1)
    ax.text(0.5, 2.6, "2% budget", fontsize=8, color="#555")
    ax.set_xlabel("minimum model confidence")
    ax.set_ylabel("error rate above threshold (%)")
    ax.set_title("Does confidence separate right from wrong?")
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(RESULTS / "real" / "error_vs_confidence.png", dpi=170)
    plt.close(fig)


def build_all() -> None:
    bench = json.loads((RESULTS / "real" / "gl_benchmark.json").read_text())
    auto = json.loads((RESULTS / "real" / "autonomy.json").read_text())
    hero(auto)
    per_year(bench)
    confidence(auto)
