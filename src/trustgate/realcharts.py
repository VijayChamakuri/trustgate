"""Charts for the real-data track (written to results/real/*.png)."""

from __future__ import annotations

import json

import matplotlib.pyplot as plt
import numpy as np

from . import chartstyle as cs
from .paths import RESULTS

POL = [
    ("auto_all", "Auto-post\neverything"),
    ("point_estimate", "Point-estimate\nthreshold"),
    ("wilson", "Wilson\nbound"),
    ("wilson+novelty", "+ novelty\nguard"),
    ("wilson+novelty+tiers", "+ value tiers\n(earned)"),
]


def hero(auto: dict, budget: str = "0.02") -> None:
    """Same gate, two outcomes: automation granted and realised error, card-feed vs upstream."""
    cs.apply()
    fig, axes = plt.subplots(1, 2, figsize=(10.5, 4), sharey=True)
    for ax, (setting, title) in zip(
        axes,
        [
            ("card_feed", "Card-feed fields only (model {acc} accurate)"),
            ("upstream", "With the upstream procurement category (model {acc})"),
        ],
        strict=True,
    ):
        s = auto["settings"][setting]
        b = s["budgets"][budget]
        x = np.arange(len(POL))
        autos = [b[p]["automation_rate"] * 100 for p, _ in POL]
        errs = [b[p]["auto_error_rate"] * 100 for p, _ in POL]
        over = [b[p]["months_over_budget"] for p, _ in POL]
        bars = ax.bar(x, autos, color=[cs.FLAG if o > 0 else cs.BLUE for o in over], width=0.56)
        for xi, bar, e, o in zip(x, bars, errs, over, strict=True):
            label = "none granted" if bar.get_height() == 0 else f"{e:.1f}% wrong\nover in {o} of {s['live_months']}"
            ax.text(xi, bar.get_height() + 2, label, ha="center", va="bottom", fontsize=7.5, color=cs.INK2)
        ax.set_xticks(x, [lab for _, lab in POL])
        ax.tick_params(axis="x", length=0)
        ax.set_ylim(0, 118)
        ax.set_yticks(range(0, 101, 25))
        ax.set_title(title.format(acc=f"{s['model_accuracy_live']:.0%}"))
    axes[0].set_ylabel("Live lines auto-posted (%)")
    fig.text(
        0.0,
        -0.02,
        f"{float(budget):.0%} error budget, {auto['settings']['upstream']['live_months']} live months. "
        "Red bars broke the budget in at least one month.",
        fontsize=8.5,
        color=cs.MUTED,
    )
    fig.tight_layout()
    fig.savefig(RESULTS / "real" / "hero.png")
    plt.close(fig)


def per_year(bench: dict) -> None:
    cs.apply()
    fig, ax = plt.subplots(figsize=(6.6, 3.8))
    rows = [r for r in bench["benchmark"]["per_year"] if r["setting"] == "card_feed"]
    series = [
        ("tfidf+embed_logreg", "TF-IDF + embeddings + LR", cs.BLUE),
        ("tfidf_logreg", "TF-IDF + LR", cs.ORANGE),
        ("embed_knn", "Embeddings kNN", cs.PURPLE),
        ("merchant_lookup", "Merchant lookup", cs.NEUTRAL),
    ]
    ends = []
    for m, lab, c in series:
        rr = [r for r in rows if r["model"] == m]
        ys = [r["accuracy"] * 100 for r in rr]
        ax.plot([r["year"] for r in rr], ys, marker="o", color=c, lw=2.2 if c == cs.BLUE else 1.6)
        ends.append([ys[-1], lab, rr[-1]["year"]])
    ends.sort(reverse=True)
    for i in range(1, len(ends)):  # keep end labels at least 1.4 points apart
        ends[i][0] = min(ends[i][0], ends[i - 1][0] - 1.4)
    for y, lab, year in ends:
        ax.annotate(lab, (year, y), xytext=(8, 0), textcoords="offset points", va="center", fontsize=8, color=cs.INK2)
    nm = [r for r in rows if r["model"] == "tfidf+embed_logreg"]
    ax.set_xticks([r["year"] for r in nm], [f"{r['year']}\n{(1 - r['seen_merchant_share']):.0%} new" for r in nm])
    ax.tick_params(axis="x", length=0)
    ax.set_xlim(nm[0]["year"] - 0.3, nm[-1]["year"] + 1.6)
    ax.spines["bottom"].set_bounds(nm[0]["year"] - 0.3, nm[-1]["year"] + 0.3)
    ax.set_ylabel("Accuracy (%)")
    ax.set_title("Accuracy by test year, card-feed fields")
    fig.tight_layout()
    fig.savefig(RESULTS / "real" / "accuracy_by_year.png")
    plt.close(fig)


def confidence(auto: dict) -> None:
    cs.apply()
    fig, ax = plt.subplots(figsize=(5.6, 3.8))
    for setting, lab, c in [("card_feed", "Card feed", cs.ORANGE), ("upstream", "Upstream", cs.BLUE)]:
        rows = auto["settings"][setting]["accuracy_by_confidence"]
        xs, ys = [r["min_conf"] for r in rows], [r["error"] * 100 for r in rows]
        ax.plot(xs, ys, marker="o", color=c)
        ax.annotate(lab, (xs[0], ys[0]), xytext=(0, 8), textcoords="offset points", fontsize=8.5, color=cs.INK2)
    ax.axhline(2, ls="--", color=cs.INK2, lw=0.9)
    ax.annotate("2% budget", (0.62, 2), xytext=(0, -4), textcoords="offset points", va="top", fontsize=8, color=cs.INK2)
    ax.set_xlabel("Minimum model confidence")
    ax.set_ylabel("Error rate above that confidence (%)")
    ax.set_title("Does confidence separate right from wrong?")
    fig.tight_layout()
    fig.savefig(RESULTS / "real" / "error_vs_confidence.png")
    plt.close(fig)


def build_all() -> None:
    bench = json.loads((RESULTS / "real" / "gl_benchmark.json").read_text())
    auto = json.loads((RESULTS / "real" / "autonomy.json").read_text())
    hero(auto)
    per_year(bench)
    confidence(auto)
