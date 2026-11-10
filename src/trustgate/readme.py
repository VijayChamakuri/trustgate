"""Render README.md from docs/README.template.md and results/*.json.

Placeholders look like {{name}}. Every number in the README comes from a results file, and
tests/test_readme.py fails if README.md differs from a fresh render, so prose and evidence
cannot drift apart.
"""

from __future__ import annotations

import json
import re

from .paths import RESULTS, ROOT

TEMPLATE = ROOT / "docs" / "README.template.md"
README = ROOT / "README.md"


def _j(name: str) -> dict:
    return json.loads((RESULTS / name).read_text())


def pct(x: float | None, d: int = 1) -> str:
    return "–" if x is None else f"{100 * x:.{d}f}%"


MODEL = {
    "majority": "Majority class",
    "merchant_lookup": "Merchant → account lookup",
    "tfidf_logreg": "Char TF-IDF + logistic regression",
    "embed_knn": "Local embeddings, kNN",
    "embed_logreg": "Local embeddings + logistic regression",
    "tfidf+embed_logreg": "**TF-IDF + embeddings + logistic regression**",
    "category_lookup": "Procurement-category lookup",
}
POL = {
    "auto_all": "Auto-post everything",
    "point_estimate": "Point-estimate threshold",
    "wilson": "Wilson upper bound",
    "wilson+novelty": "+ novelty guard",
    "wilson+novelty+tiers": "**+ value tiers (earned autonomy)**",
}
SYN = {
    "trust_engine_everywhere": "Trust the rules engine everywhere",
    "thresholds_from_reviewer_history": "Thresholds learned from reviewer history",
    "point_estimate_thresholds": "Audited gold, point-estimate threshold",
    "earned_without_novelty_guard": "+ Wilson upper bound",
    "earned_without_value_tiers": "+ novelty guard",
    "earned_autonomy": "**+ value tiers = earned autonomy**",
}
ABL = {
    "earned_autonomy": "Full policy",
    "ablation_without_wilson": "− Wilson bound (point estimate)",
    "ablation_without_novelty_guard": "− novelty guard",
    "earned_without_value_tiers": "− value tiers",
}


def values() -> dict[str, str]:
    bench, auto = _j("real/gl_benchmark.json"), _j("real/autonomy.json")
    wh, exp, met, ext = _j("real/warehouse.json"), _j("experiments.json"), _j("metrics.json"), _j("extractors.json")
    b, ds = bench["benchmark"], bench["dataset"]
    cf, up = b["settings"]["card_feed"], b["settings"]["upstream"]
    best, look = cf["tfidf+embed_logreg"], cf["merchant_lookup"]
    v: dict[str, str] = {
        "real_lines": f"{ds['labelled_lines']:,}",
        "real_accounts": str(ds["accounts"]),
        "real_merchants": f"{ds['merchants']:,}",
        "real_years": f"{ds['years'][0]}–{ds['years'][-1]}",
        "ceiling_card": pct(b["ceiling"]["card_feed"]),
        "ceiling_up": pct(b["ceiling"]["upstream"]),
        "best_acc": pct(best["accuracy"]),
        "best_ci": f"{pct(best['accuracy_ci'][0])}–{pct(best['accuracy_ci'][1])}",
        "best_top3": pct(best["top3"]),
        "best_new": pct(best["accuracy_new_merchants"]),
        "lookup_acc": pct(look["accuracy"]),
        "lookup_new": pct(look["accuracy_new_merchants"]),
        "up_best_acc": pct(up["tfidf+embed_logreg"]["accuracy"]),
        "up_cat_acc": pct(up["category_lookup"]["accuracy"]),
        "gl_eval_lines": f"{best['n']:,}",
    }
    rows = [
        "| Model (card-feed signals only) | Accuracy | 95% CI | Top-3 | Accuracy on new merchants | Macro-F1 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for m, x in sorted(cf.items(), key=lambda kv: kv[1]["accuracy"]):
        rows.append(
            f"| {MODEL[m]} | {pct(x['accuracy'])} | {pct(x['accuracy_ci'][0])}–{pct(x['accuracy_ci'][1])} | "
            f"{pct(x['top3']) if x.get('top3') else '–'} | {pct(x['accuracy_new_merchants'])} | {x['macro_f1']:.3f} |"
        )
    v["gl_table"] = "\n".join(rows)

    s_cf, s_up = auto["settings"]["card_feed"], auto["settings"]["upstream"]
    rows = [
        "| Policy (2% error budget) | Card feed: auto-posted | error | months over budget | Upstream: auto-posted | error | months over budget |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for p, lab in POL.items():
        c, u = s_cf["budgets"]["0.02"][p], s_up["budgets"]["0.02"][p]
        rows.append(
            f"| {lab} | {pct(c['automation_rate'])} | {pct(c['auto_error_rate'])} | {c['months_over_budget']}/{s_cf['live_months']} | "
            f"{pct(u['automation_rate'])} | {pct(u['auto_error_rate'], 2)} | {u['months_over_budget']}/{s_up['live_months']} |"
        )
    v["autonomy_table"] = "\n".join(rows)
    ea_up = s_up["budgets"]["0.02"]["wilson+novelty+tiers"]
    v |= {
        "up_auto": pct(ea_up["automation_rate"]),
        "up_err": pct(ea_up["auto_error_rate"], 2),
        "up_err_ci_hi": pct(ea_up["auto_error_ci"][1], 2),
        "live_months": str(s_up["live_months"]),
        "up_over": str(ea_up["months_over_budget"]),
        "live_lines": f"{s_up['live_lines']:,}",
        "cf_all_err": pct(s_cf["budgets"]["0.02"]["auto_all"]["auto_error_rate"]),
        "cf_misposted": f"£{s_cf['budgets']['0.02']['auto_all']['misposted_gbp']:,.0f}",
        "cf_point_auto": pct(s_cf["budgets"]["0.02"]["point_estimate"]["automation_rate"]),
        "cf_point_err": pct(s_cf["budgets"]["0.02"]["point_estimate"]["auto_error_rate"]),
        "cf_point_over": str(s_cf["budgets"]["0.02"]["point_estimate"]["months_over_budget"]),
        "cf_model_acc": pct(s_cf["model_accuracy_live"]),
        "up_model_acc": pct(s_up["model_accuracy_live"]),
        "cf_top3": pct(s_cf["top3_live"]),
        "cf_err_at_95": pct(next(r["error"] for r in s_cf["accuracy_by_confidence"] if r["min_conf"] == 0.95)),
        "audits": str(auto["config"]["audits_per_month"]),
    }

    seeds = exp["seeds"]
    budget = exp["seed_runs"][0]["_meta"]["approve_budget"]

    def over(p: str) -> int:
        return sum(r[p]["auto_error_rate"] > budget for r in exp["seed_runs"])

    rows = [
        f"| Autonomy policy (mean ± sd over {exp['n_seeds']} seeds) | Auto-actioned | Error on auto-actions "
        f"| Seeds over the {pct(budget, 0)} budget | Policy leakage | Total cost |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for p, lab in SYN.items():
        x = seeds[p]
        rows.append(
            f"| {lab} | {pct(x['automation_rate']['mean'])} ± {pct(x['automation_rate']['sd'])} | "
            f"{pct(x['auto_error_rate']['mean'], 2)} ± {pct(x['auto_error_rate']['sd'], 2)} | {over(p)}/{exp['n_seeds']} | "
            f"${x['leaked_dollars']['mean']:,.0f} ± ${x['leaked_dollars']['sd']:,.0f} | ${x['total_cost']['mean']:,.0f} |"
        )
    v["synthetic_table"] = "\n".join(rows)
    v["hist_over"] = f"{over('thresholds_from_reviewer_history')}/{exp['n_seeds']}"
    v["point_over"] = f"{over('point_estimate_thresholds')}/{exp['n_seeds']}"
    aud = sorted((pt["value"], pt["earned_autonomy"]["automation_rate"]["mean"]) for pt in exp["sensitivity"]["audit_per_month"])
    v["aud_low_n"], v["aud_low"] = str(int(aud[0][0])), pct(aud[0][1])
    v["aud_mid_n"], v["aud_mid"] = str(int(aud[1][0])), pct(aud[1][1])
    rows = ["| Ablation (drop one safeguard) | Auto-actioned | Error on auto-actions | Policy leakage |", "|---|---:|---:|---:|"]
    for p, lab in ABL.items():
        x = seeds[p]
        rows.append(
            f"| {lab} | {pct(x['automation_rate']['mean'])} | {pct(x['auto_error_rate']['mean'], 2)} | ${x['leaked_dollars']['mean']:,.0f} |"
        )
    v["ablation_table"] = "\n".join(rows)
    v["syn_within_budget"] = (
        f"{sum(r['earned_autonomy']['auto_error_rate'] <= r['_meta']['approve_budget'] for r in exp['seed_runs'])}/{exp['n_seeds']}"
    )
    sens_rows = [
        "| Assumption varied | Values | Earned autonomy within budget | Point estimate within budget |",
        "|---|---|---:|---:|",
    ]
    names = {
        "leniency": "Reviewer leniency",
        "drift_rate": "Memo drift rate",
        "audit_per_month": "Audits per month",
        "approve_budget": "Error budget",
    }
    tot_e = tot_p = tot = 0
    for param, pts in exp["sensitivity"].items():
        e_ok = sum(
            pt["earned_autonomy"]["auto_error_rate"]["mean"] <= (pt["value"] if param == "approve_budget" else 0.01) for pt in pts
        )
        p_ok = sum(
            pt["point_estimate_thresholds"]["auto_error_rate"]["mean"] <= (pt["value"] if param == "approve_budget" else 0.01)
            for pt in pts
        )
        tot_e, tot_p, tot = tot_e + e_ok, tot_p + p_ok, tot + len(pts)
        sens_rows.append(
            f"| {names[param]} | {', '.join(str(pt['value']) for pt in pts)} | {e_ok}/{len(pts)} | {p_ok}/{len(pts)} |"
        )
    v["sensitivity_table"] = "\n".join(sens_rows)
    v["sens_earned_ok"], v["sens_point_ok"], v["sens_total"] = str(tot_e), str(tot_p), str(tot)

    rows = [
        "| Perception backend | Alcohol recall | Premium-cabin recall | Personal-memo recall | Engine accuracy | Earned autonomy | Error |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for k, x in ext.items():
        s = x["signals"]
        rows.append(
            f"| {k} | {pct(s['alcohol_on_meals']['recall'])} | {pct(s['premium_cabin']['recall'])} | "
            f"{pct(s['personal_memo']['recall'])} | {pct(x['engine_accuracy'])} | {pct(x['earned_autonomy']['automation_rate'])} | "
            f"{pct(x['earned_autonomy']['auto_error_rate'], 2)} |"
        )
    v["extractor_table"] = "\n".join(rows)
    mr = met["merchant_resolution"]
    v |= {
        "res_desc": f"{mr['descriptors']:,}",
        "res_prec": pct(mr["match_precision"], 2),
        "res_recall": pct(mr["registry_recall"]),
        "res_conf": str(mr["protected_pair_confusions"]),
        "verifier_auc": f"{met['verifier']['live_auc']:.3f}",
    }
    v["real_bench_min"] = f"{bench['seconds'] / 60:.0f}"
    v["exp_min"] = f"{exp['seconds'] / 60:.0f}"
    v["warehouse_table"] = "\n".join(
        ["| Check | Result |", "|---|---|"] + [f"| {k} | {val} |" for k, val in wh["summary"].items()]
    )
    conc = wh["vendor_concentration"]
    v["top10_first"], v["top10_last"] = pct(conc[0]["top10_share"]), pct(conc[-1]["top10_share"])
    v["conc_first_year"], v["conc_last_year"] = str(conc[0]["year"]), str(conc[-1]["year"])
    return v


def render(write: bool = False) -> str:
    vals = values()
    text = TEMPLATE.read_text()
    missing = sorted(set(re.findall(r"\{\{(\w+)\}\}", text)) - set(vals))
    if missing:
        raise KeyError(f"README template placeholders without values: {missing}")
    out = re.sub(r"\{\{(\w+)\}\}", lambda m: vals[m.group(1)], text)
    if write:
        README.write_text(out)
    return out
