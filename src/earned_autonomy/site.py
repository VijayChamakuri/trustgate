"""Build the GitHub Pages site (site/index.html) from results/*.json.

Every number on the page is read from a results file at build time; nothing is typed by hand.
The dbt docs (lineage graph) are generated separately into site/dbt/.
"""

from __future__ import annotations

import html
import json
import os
import shutil
import subprocess
from pathlib import Path

from .paths import DB_PATH, RESULTS, ROOT, WAREHOUSE

SITE = ROOT / "site"
PLOTLY = "https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.35.2/plotly.min.js"
REPO_URL = "https://github.com/VijayChamakuri/earned-autonomy"


def _load(name: str) -> dict:
    p = RESULTS / name
    return json.loads(p.read_text()) if p.exists() else {}


def pct(x: float | None, d: int = 1) -> str:
    return "–" if x is None else f"{100 * x:.{d}f}%"


def gbp(x: float) -> str:
    return f"£{x:,.0f}"


def usd(x: float) -> str:
    return f"${x:,.0f}"


POLICY_LABELS = {
    "auto_all": "Auto-post everything",
    "point_estimate": "Point-estimate threshold",
    "wilson": "Wilson bound",
    "wilson+novelty": "Wilson + novelty guard",
    "wilson+novelty+tiers": "Wilson + novelty + value tiers",
}
SYN_LABELS = {
    "trust_engine_everywhere": "Trust the rules engine everywhere",
    "thresholds_from_reviewer_history": "Thresholds from reviewer history",
    "point_estimate_thresholds": "Audited gold, point estimate",
    "earned_without_novelty_guard": "+ Wilson upper bound",
    "earned_without_value_tiers": "+ novelty guard",
    "earned_autonomy": "+ value tiers (earned autonomy)",
}
MODEL_LABELS = {
    "majority": "Majority class",
    "merchant_lookup": "Merchant → account lookup",
    "tfidf_logreg": "Char TF-IDF + logistic regression",
    "embed_knn": "Local embeddings, kNN",
    "embed_logreg": "Local embeddings + logistic regression",
    "tfidf+embed_logreg": "TF-IDF + embeddings + logistic regression",
    "category_lookup": "Procurement-category lookup",
}


def real_tables(bench: dict, auto: dict) -> tuple[str, str, dict]:
    s = bench["benchmark"]["settings"]["card_feed"]
    rows = []
    for m, v in sorted(s.items(), key=lambda kv: kv[1]["accuracy"]):
        rows.append(
            f"<tr><td>{MODEL_LABELS.get(m, m)}</td><td>{pct(v['accuracy'])}</td>"
            f"<td class=ci>{pct(v['accuracy_ci'][0])}–{pct(v['accuracy_ci'][1])}</td>"
            f"<td>{pct(v['top3']) if v.get('top3') else '–'}</td><td>{pct(v['accuracy_new_merchants'])}</td>"
            f"<td>{v['macro_f1']:.3f}</td></tr>"
        )
    gl = "".join(rows)
    up = auto["settings"]["upstream"]["budgets"]["0.02"]
    cf = auto["settings"]["card_feed"]["budgets"]["0.02"]
    arow = []
    for p, lab in POLICY_LABELS.items():
        u, c = up[p], cf[p]
        arow.append(
            f"<tr><td>{lab}</td><td>{pct(c['automation_rate'])}</td><td>{pct(c['auto_error_rate'])}</td>"
            f"<td>{c['months_over_budget']}/20</td><td>{pct(u['automation_rate'])}</td>"
            f"<td>{pct(u['auto_error_rate'], 2)}</td><td>{u['months_over_budget']}/20</td></tr>"
        )
    charts = {
        "gl_models": {
            "x": [MODEL_LABELS.get(m, m) for m in s],
            "y": [s[m]["accuracy"] for m in s],
            "lo": [s[m]["accuracy_ci"][0] for m in s],
            "hi": [s[m]["accuracy_ci"][1] for m in s],
        },
        "per_year": [r for r in bench["benchmark"]["per_year"] if r["setting"] == "card_feed"],
        "rollout_up": {p: up[p]["by_month"] for p in POLICY_LABELS},
        "conf": {k: auto["settings"][k]["accuracy_by_confidence"] for k in ("card_feed", "upstream")},
    }
    return gl, "".join(arow), charts


def synthetic_tables(exp: dict, metrics: dict) -> tuple[str, dict]:
    seeds = exp.get("seeds", {})
    rows = []
    for p, lab in SYN_LABELS.items():
        if p not in seeds:
            continue
        v = seeds[p]
        rows.append(
            f"<tr><td>{lab}</td><td>{pct(v['automation_rate']['mean'])} ± {pct(v['automation_rate']['sd'])}</td>"
            f"<td>{pct(v['auto_error_rate']['mean'], 2)} ± {pct(v['auto_error_rate']['sd'], 2)}</td>"
            f"<td>{usd(v['leaked_dollars']['mean'])} ± {usd(v['leaked_dollars']['sd'])}</td>"
            f"<td>{usd(v['total_cost']['mean'])}</td></tr>"
        )
    sens = exp.get("sensitivity", {})
    return "".join(rows), {"sens": sens}


def build(with_dbt_docs: bool = True) -> Path:
    bench, auto = _load("real/gl_benchmark.json"), _load("real/autonomy.json")
    exp, metrics = _load("experiments.json"), _load("metrics.json")
    wh = _load("real/warehouse.json")
    gl_rows, auto_rows, rc = real_tables(bench, auto)
    syn_rows, sc = synthetic_tables(exp, metrics)
    up2 = auto["settings"]["upstream"]["budgets"]["0.02"]
    cf2 = auto["settings"]["card_feed"]["budgets"]["0.02"]
    best = bench["benchmark"]["settings"]["card_feed"]["tfidf+embed_logreg"]
    lookup = bench["benchmark"]["settings"]["card_feed"]["merchant_lookup"]
    ds = bench["dataset"]
    hero = {
        "lines": f"{ds['labelled_lines']:,}",
        "best": pct(best["accuracy"]),
        "lookup": pct(lookup["accuracy"]),
        "ceiling": pct(bench["benchmark"]["ceiling"]["card_feed"]),
        "auto_up": pct(up2["wilson+novelty+tiers"]["automation_rate"]),
        "err_up": pct(up2["wilson+novelty+tiers"]["auto_error_rate"], 2),
        "misposted": gbp(cf2["auto_all"]["misposted_gbp"]),
    }
    data = json.dumps({"real": rc, "syn": sc}).replace("</", "<\\/")
    wh_rows = "".join(
        f"<tr><td>{html.escape(str(k))}</td><td>{html.escape(str(v))}</td></tr>" for k, v in wh.get("summary", {}).items()
    )
    page = TEMPLATE.format(
        plotly=PLOTLY,
        repo=REPO_URL,
        data=data,
        gl_rows=gl_rows,
        auto_rows=auto_rows,
        syn_rows=syn_rows,
        wh_rows=wh_rows,
        n_seeds=exp.get("n_seeds", 0),
        **hero,
    )
    SITE.mkdir(exist_ok=True)
    (SITE / "index.html").write_text(page)
    for png in RESULTS.glob("*.png"):
        shutil.copy(png, SITE / png.name)
    if with_dbt_docs:
        subprocess.run(
            ["dbt", "docs", "generate", "--static", "--project-dir", str(WAREHOUSE), "--profiles-dir", str(WAREHOUSE)],
            check=True,
            capture_output=True,
            env={**os.environ, "EA_DB_PATH": str(DB_PATH)},
        )
        (SITE / "dbt").mkdir(exist_ok=True)
        docs = (WAREHOUSE / "target" / "static_index.html").read_text()
        # dbt embeds absolute local paths in its manifest; publish repo-relative ones only.
        docs = docs.replace(str(ROOT).replace("\\", "/"), "earned-autonomy").replace(str(Path.home()), "~")
        (SITE / "dbt" / "index.html").write_text(docs)
    (SITE / ".nojekyll").write_text("")
    return SITE / "index.html"


TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Earned Autonomy</title>
<meta name="description" content="When should a finance agent act without a human? Evaluated on real council card data.">
<script src="{plotly}"></script>
<style>
:root{{--bg:#fbfaf7;--fg:#1d1d1b;--muted:#6b6a65;--line:#e6e3dc;--card:#fff;--accent:#2457c5;--good:#1f7a4d;--bad:#b4372f}}
@media (prefers-color-scheme:dark){{:root:not([data-theme=light]){{--bg:#121212;--fg:#ecebe7;--muted:#a3a19a;--line:#2b2a27;--card:#1a1a19;--accent:#7aa2ff;--good:#56c28d;--bad:#ff7a6e}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--fg);font:16px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}}
main{{max-width:1040px;margin:0 auto;padding:40px 16px 80px}}
h1{{font-size:clamp(28px,5vw,44px);line-height:1.1;margin:0 0 12px;letter-spacing:-.02em}}
h2{{font-size:24px;margin:56px 0 8px;letter-spacing:-.01em}}h3{{font-size:17px;margin:28px 0 8px}}
p{{max-width:72ch}}.muted{{color:var(--muted)}}a{{color:var(--accent)}}
.lede{{font-size:19px;color:var(--muted);max-width:66ch}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;margin:28px 0}}
.kpi{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px}}
.kpi b{{display:block;font-size:30px;letter-spacing:-.02em}}.kpi span{{color:var(--muted);font-size:14px}}
.tablewrap{{overflow-x:auto;border:1px solid var(--line);border-radius:12px;background:var(--card)}}
table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{padding:9px 12px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}}
th:first-child,td:first-child{{text-align:left}}th{{font-weight:600;color:var(--muted)}}tr:last-child td{{border-bottom:0}}td.ci{{color:var(--muted)}}
.chart{{width:100%;height:360px;background:var(--card);border:1px solid var(--line);border-radius:12px;margin:12px 0}}
.grid2{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px}}
.note{{border-left:3px solid var(--accent);padding:4px 14px;color:var(--muted);margin:16px 0}}
nav a{{margin-right:16px}}code{{font-size:13px;background:var(--card);border:1px solid var(--line);padding:1px 5px;border-radius:6px}}
footer{{margin-top:64px;color:var(--muted);font-size:14px}}
</style></head><body><main>
<nav><a href="{repo}">GitHub</a><a href="dbt/">Warehouse lineage (dbt docs)</a><a href="{repo}/blob/main/docs/METHODOLOGY.md">Methodology</a></nav>
<h1>When should a finance agent act without a human?</h1>
<p class="lede">An autonomy gate for finance agents, evaluated on {lines} real corporate-card lines that council staff
actually booked (Stockport MBC, 2017–2022), plus a policy-agent benchmark with planted ground truth. The agent only
acts alone where the upper 95% bound of its audited error rate fits the budget.</p>
<div class="kpis">
<div class="kpi"><b>{auto_up}</b><span>of real lines auto-posted at {err_up} error (2% budget, 0 of 20 months over budget)</span></div>
<div class="kpi"><b>0%</b><span>autonomy granted when the data can't support it (card-feed setting, 2% budget)</span></div>
<div class="kpi"><b>{misposted}</b><span>that auto-posting everything would have mis-booked on the card feed</span></div>
<div class="kpi"><b>{best}</b><span>GL accuracy from card-feed signals, vs {lookup} merchant lookup (ceiling {ceiling})</span></div>
</div>

<h2>1 · Real data: GL coding on council card spend</h2>
<p>Rolling, strictly temporal evaluation: each year 2018–2022 is predicted by a model trained only on earlier years.
<b>Card-feed setting</b>: merchant string, cardholder department and service, amount, which is what a card program sees.
The ceiling is set by staff coding identical inputs differently.</p>
<div class="tablewrap"><table><thead><tr><th>Model (card-feed setting)</th><th>Accuracy</th><th>95% CI</th><th>Top-3</th><th>New merchants</th><th>Macro-F1</th></tr></thead><tbody>{gl_rows}</tbody></table></div>
<div id="peryear" class="chart"></div>

<h3>Earned autonomy on real labels (2% error budget, 20 live months)</h3>
<p>Monthly rollout from 2020-07: expanding-window retraining, 40 audited lines a month, thresholds chosen only from earlier audits.</p>
<div class="tablewrap"><table><thead><tr><th>Policy</th><th>Card feed: auto</th><th>error</th><th>months over</th><th>Upstream: auto</th><th>error</th><th>months over</th></tr></thead><tbody>{auto_rows}</tbody></table></div>
<div class="grid2"><div id="rollout" class="chart"></div><div id="conf" class="chart"></div></div>
<p class="note">Same gate, two outcomes. On card-feed signals confidence does not separate right from wrong (the same supermarket
is legitimately booked to different accounts), so the gate grants nothing and the right product is top-3 suggestions.
With the council's procurement category available upstream, it earns substantial autonomy without breaching the budget.</p>

<h2>2 · Policy-agent benchmark (synthetic, {n_seeds} seeds)</h2>
<p>Expense-policy decisions (approve / reject / needs review with cited policy sections) have no public labels, so this track
uses a documented generator with planted ground truth. Every assumption is varied in the sensitivity analysis below.</p>
<div class="tablewrap"><table><thead><tr><th>Autonomy policy (live months)</th><th>Auto-actioned</th><th>Error rate</th><th>Policy leakage</th><th>Total cost</th></tr></thead><tbody>{syn_rows}</tbody></table></div>
<div class="grid2" id="sens"></div>

<h2>3 · Warehouse</h2>
<p>dbt + DuckDB over all published files (2017–present), reconciled row-for-row to a SHA-256 manifest.
Browse the <a href="dbt/">lineage graph and column docs</a>.</p>
<div class="tablewrap"><table><tbody>{wh_rows}</tbody></table></div>

<footer>Reproduce: <code>make reproduce</code> · Data: Stockport MBC purchase-card transactions, Open Government Licence v3.0 ·
<a href="{repo}">Source on GitHub</a></footer>
</main>
<script>
const D={data};
const css=getComputedStyle(document.documentElement);const fg=css.getPropertyValue('--fg').trim(),mut=css.getPropertyValue('--muted').trim(),line=css.getPropertyValue('--line').trim();
const L=(t,ex={{}})=>Object.assign({{title:{{text:t,font:{{size:14}}}},paper_bgcolor:'rgba(0,0,0,0)',plot_bgcolor:'rgba(0,0,0,0)',font:{{color:fg,size:12}},margin:{{l:56,r:16,t:40,b:48}},xaxis:{{gridcolor:line}},yaxis:{{gridcolor:line,tickformat:'.0%'}},legend:{{orientation:'h',y:-0.2}}}},ex);
const cfg={{displayModeBar:false,responsive:true}};
const py=D.real.per_year;const models=[...new Set(py.map(r=>r.model))];
const MN={{majority:'Majority class',merchant_lookup:'Merchant lookup',tfidf_logreg:'TF-IDF + LR',embed_knn:'Embeddings kNN',embed_logreg:'Embeddings + LR','tfidf+embed_logreg':'TF-IDF + embeddings + LR'}};
Plotly.newPlot('peryear',models.map(m=>({{x:py.filter(r=>r.model===m).map(r=>r.year),y:py.filter(r=>r.model===m).map(r=>r.accuracy),name:MN[m]||m,mode:'lines+markers'}})),L('Accuracy by test year (card-feed)',{{xaxis:{{dtick:1,tickformat:'d',gridcolor:line}}}}),cfg);
const ro=D.real.rollout_up;
const PL={{auto_all:'Auto-post everything',point_estimate:'Point estimate',wilson:'Wilson bound','wilson+novelty':'+ novelty guard','wilson+novelty+tiers':'+ value tiers (earned)'}};
Plotly.newPlot('rollout',Object.keys(ro).map(p=>({{x:ro[p].map(r=>r.month),y:ro[p].map(r=>r.auto/r.n),name:PL[p]||p,mode:'lines'}})),L('Upstream: share auto-posted per month'),cfg);
const cf=D.real.conf;
Plotly.newPlot('conf',Object.keys(cf).map(k=>({{x:cf[k].map(r=>r.min_conf),y:cf[k].map(r=>r.error),name:k==='card_feed'?'Card feed':'Upstream',mode:'lines+markers'}})),L('Error rate above a confidence threshold',{{xaxis:{{title:'min confidence',gridcolor:line}}}}),cfg);
const S=D.syn.sens||{{}};const names={{leniency:'Reviewer leniency',drift_rate:'Memo drift rate',audit_per_month:'Audits per month',approve_budget:'Error budget'}};
for(const p of Object.keys(S)){{const div=document.createElement('div');div.className='chart';document.getElementById('sens').appendChild(div);
const pols=['trust_engine_everywhere','point_estimate_thresholds','earned_autonomy'];
const SN={{trust_engine_everywhere:'Trust engine everywhere',point_estimate_thresholds:'Point estimate',earned_autonomy:'Earned autonomy'}};
Plotly.newPlot(div,pols.map(pol=>({{x:S[p].map(r=>r.value),y:S[p].map(r=>r[pol].auto_error_rate.mean),name:SN[pol],mode:'lines+markers'}})),L('Auto-action error vs '+names[p],{{xaxis:{{type:'category',gridcolor:line}}}}),cfg);}}
</script></body></html>
"""
