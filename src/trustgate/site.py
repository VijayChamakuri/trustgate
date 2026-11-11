"""Build the GitHub Pages site (site/index.html) from results/.

The page is a plain write-up. Its numbers and tables come from the same `values()` that renders the
README, so the two cannot disagree, and nothing is typed by hand. The dbt docs (lineage graph) are
generated separately into site/dbt/.
"""

from __future__ import annotations

import html
import os
import re
import shutil
import subprocess
from pathlib import Path

from .paths import DB_PATH, RESULTS, ROOT, WAREHOUSE

SITE = ROOT / "site"
REPO_URL = "https://github.com/VijayChamakuri/trustgate"
FIGURES = ("real/hero.png", "real/accuracy_by_year.png", "real/error_vs_confidence.png")


def _inline(s: str) -> str:
    s = html.escape(s, quote=False)
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)


def md_table(text: str) -> str:
    """Render one of the README's pipe tables as an HTML table (right-aligned where the README says ---:)."""
    lines = [ln.strip().strip("|") for ln in text.strip().splitlines()]
    head, align, body = lines[0], lines[1], lines[2:]
    right = [a.strip().endswith(":") for a in align.split("|")]

    def cells(row: str, tag: str) -> str:
        parts = [c.strip() for c in row.split("|")]
        return "".join(
            f"<{tag}{' class=n' if i < len(right) and right[i] else ''}>{_inline(c)}</{tag}>" for i, c in enumerate(parts)
        )

    rows = "".join(f"<tr>{cells(r, 'td')}</tr>" for r in body)
    return f"<div class=scroll><table><thead><tr>{cells(head, 'th')}</tr></thead><tbody>{rows}</tbody></table></div>"


def build(with_dbt_docs: bool = True) -> Path:
    from .readme import values

    v = values()
    page = TEMPLATE.replace("{{css}}", CSS).replace("{{repo}}", REPO_URL)
    for k, val in v.items():
        key = "{{" + k + "}}"
        if key in page:
            page = page.replace(key, md_table(val) if k.endswith("_table") else html.escape(val))
    missing = re.findall(r"\{\{(\w+)\}\}", page)
    if missing:
        raise KeyError(f"unfilled placeholders in site template: {missing}")
    SITE.mkdir(exist_ok=True)
    (SITE / "index.html").write_text(page)
    for f in FIGURES:
        shutil.copy(RESULTS / f, SITE / Path(f).name)
    if with_dbt_docs:
        subprocess.run(
            ["dbt", "docs", "generate", "--static", "--project-dir", str(WAREHOUSE), "--profiles-dir", str(WAREHOUSE)],
            check=True,
            capture_output=True,
            env={**os.environ, "TRUSTGATE_DB_PATH": str(DB_PATH)},
        )
        (SITE / "dbt").mkdir(exist_ok=True)
        docs = (WAREHOUSE / "target" / "static_index.html").read_text()
        # dbt embeds absolute local paths in its manifest; publish repo-relative ones only.
        docs = docs.replace(str(ROOT).replace("\\", "/"), "trustgate").replace(str(Path.home()), "~")
        (SITE / "dbt" / "index.html").write_text(docs)
    (SITE / ".nojekyll").write_text("")
    return SITE / "index.html"


# Shared with the CommitCompass and JudgeGate pages so the three read as one body of work.
CSS = """
:root{--paper:#ffffff;--ink:#1c1b19;--ink2:#47443e;--muted:#77736a;--rule:#d9d3c6;--rule2:#ebe6db;--link:#1a4c8c;--flag:#a3261b;--code:#f2eee3}
html{color-scheme:light}*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font:18px/1.62 Charter,"Bitstream Charter","Iowan Old Style","Sitka Text",Cambria,Georgia,serif;-webkit-text-size-adjust:100%}
.page{max-width:700px;margin:0 auto;padding:20px 20px 96px}
.top{display:flex;flex-wrap:wrap;justify-content:space-between;gap:6px 24px;padding-bottom:12px;margin-bottom:56px;border-bottom:1px solid var(--rule);font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;color:var(--muted)}
.top a{color:var(--ink2);text-decoration:none}.top a:hover{color:var(--link);text-decoration:underline}
.top nav{display:flex;flex-wrap:wrap;gap:4px 18px}.top nav span{color:var(--ink);font-weight:600}
h1{font-size:clamp(28px,5.4vw,38px);line-height:1.18;letter-spacing:-.01em;margin:0 0 14px}
.meta{font-style:italic;color:var(--muted);font-size:16px;margin:0 0 40px}
h2{font-size:24px;line-height:1.3;margin:60px 0 12px}
p{margin:0 0 18px}ol,ul{margin:0 0 18px;padding-left:26px}li{margin:0 0 12px}li::marker{color:var(--muted)}
a{color:var(--link);text-decoration-thickness:1px;text-underline-offset:3px}
code{font:.8em ui-monospace,"SF Mono",Menlo,Consolas,monospace;background:var(--code);padding:2px 4px;border-radius:3px}
.wide{position:relative;left:50%;transform:translateX(-50%);width:min(1000px,calc(100vw - 40px))}
figure{margin:36px 0}figure.narrow img{max-width:560px}figure img{display:block;width:100%;height:auto;margin:0 auto}
figcaption,.cap{font-size:15.5px;line-height:1.5;color:var(--ink2);max-width:700px;margin:12px auto 0}
.cap{margin:0 auto 10px}figcaption b,.cap b{color:var(--ink)}
.tbl{margin:32px 0}.scroll{overflow-x:auto}
.tbl.wide{width:fit-content;min-width:min(700px,calc(100vw - 40px));max-width:min(1000px,calc(100vw - 40px))}.cap{contain:inline-size}
table{border-collapse:collapse;width:100%;font:13.5px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;font-variant-numeric:tabular-nums;border-top:2px solid var(--ink);border-bottom:2px solid var(--ink)}
th{font-weight:600;text-align:left;vertical-align:bottom;padding:8px 10px;border-bottom:1px solid var(--ink)}
td{padding:6px 10px;border-bottom:1px solid var(--rule2);vertical-align:top}tr:last-child td{border-bottom:0}
.n{text-align:right;white-space:nowrap}th.n{white-space:normal;min-width:5em}td:first-child{min-width:11em}.dim{color:var(--muted)}.flag{color:var(--flag);font-weight:600}
footer{margin-top:80px;padding-top:14px;border-top:1px solid var(--rule);font-size:15px;color:var(--muted)}
@media (max-width:600px){body{font-size:17px}.top{margin-bottom:36px}h2{margin-top:48px}}
"""

TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>TrustGate</title>
<meta name="description" content="When should a finance agent act without a person checking? An autonomy gate tested on 14,851 real council card bookings.">
<style>{{css}}</style></head><body><div class=page>
<header class=top><a href="https://github.com/VijayChamakuri">Vijay Chamakuri</a>
<nav><span>TrustGate</span><a href="https://vijaychamakuri.github.io/commitcompass/">CommitCompass</a><a href="https://vijaychamakuri.github.io/judgegate/">JudgeGate</a><a href="https://vijaychamakuri.github.io/sift/">Sift</a></nav></header>
<main>
<h1>When should a finance agent act without a person checking?</h1>
<p class=meta>Vijay Chamakuri, November 2026. Code, data and full method on <a href="{{repo}}">GitHub</a>.</p>

<p>Finance teams want agents to do routine bookkeeping: code each card transaction to a ledger account, approve or flag expense
claims. The part I care about is deciding when one is safe to leave alone. Most systems answer with a confidence threshold that
someone picked by feel. TrustGate answers with evidence. The agent may act alone on a kind of transaction only when audits of its
past work put a 95% upper bound on its error rate under a budget. The bound is recomputed every month, so autonomy can be lost as
well as earned.</p>
<p>I tested it on {{real_lines}} purchase-card lines from Stockport Council that finance staff booked by hand ({{real_years}}),
and on a simulated expense-policy agent where the right answers are planted.</p>

<h2>What I found</h2>
<ol>
<li><strong>Where the data supports it, the gate earns real autonomy.</strong> With the council's procurement category available,
the agent posted {{up_auto}} of lines on its own at {{up_err}} error (95% upper bound {{up_err_ci_hi}}) and went over the 2% budget
in {{up_over}} of {{live_months}} months.</li>
<li><strong>Where it doesn't, the gate refuses.</strong> From card-feed fields alone the model is {{cf_model_acc}} accurate, and its
confidence does not separate right answers from wrong ones, so the gate granted nothing. Posting everything would have mis-booked
{{cf_misposted}}. A threshold set from the average audited error unlocked {{cf_point_auto}} of lines at {{cf_point_err}} error and
broke the budget in {{cf_point_over}} months. On this data the useful product is a shortlist: the right account is in the model's
top three {{cf_top3}} of the time.</li>
<li><strong>The coding model is a real baseline, not a strawman.</strong> From card-feed fields it reaches {{best_acc}} accuracy
(95% CI {{best_ci}}) against {{lookup_acc}} for a merchant lookup, and {{best_new}} on merchants it has never seen, against
{{lookup_new}}. Staff code identical inputs differently often enough that no model can beat {{ceiling_card}}.</li>
<li><strong>The simulated policy agent tells the same story.</strong> Earned autonomy stayed inside its budget in
{{syn_within_budget}} seeds and in {{sens_earned_ok}} of {{sens_total}} sensitivity settings. Thresholds from the average audited
error broke it in {{point_over}} seeds.</li>
</ol>

<h2>Coding real card transactions</h2>
<p>Each year from 2018 to 2022 is predicted by a model trained only on earlier years. The card-feed setting uses what a card
platform sees: the merchant string, the cardholder's department and service, and the amount.</p>
<div class="tbl wide"><p class=cap><b>Table 1.</b> Ledger-account accuracy from card-feed fields, {{gl_eval_lines}} test lines.</p>{{gl_table}}</div>
<figure class=narrow><img src="accuracy_by_year.png" alt="Accuracy by test year for four models, 2018 to 2022; the combined model is highest in every year.">
<figcaption><b>Figure 1.</b> Accuracy by test year. Under each year is the share of lines from merchants the model had
never seen.</figcaption></figure>

<h2>The gate on real data, month by month</h2>
<p>The agent then ran live for the final {{live_months}} months ({{live_lines}} lines). Each month it was refit on earlier data,
{{audits}} random lines were audited, and the gate set thresholds from earlier audits only. The same gate ran on two versions of
the data: card-feed fields only, and those fields plus the procurement category staff assign upstream.</p>
<figure class=wide><img src="hero.png" alt="Bar charts of the share of lines auto-posted under five policies, card-feed on the left and upstream on the right. Only the Wilson-based policies stay within budget.">
<figcaption><b>Figure 2.</b> Share of lines auto-posted under each policy at a 2% error budget. Red bars broke the budget in at
least one month.</figcaption></figure>
<div class="tbl wide"><p class=cap><b>Table 2.</b> The same rollout as a table. Each row adds one safeguard to the row above.</p>{{autonomy_table}}</div>
<figure class=narrow><img src="error_vs_confidence.png" alt="Error rate among lines above a confidence threshold, card feed against upstream.">
<figcaption><b>Figure 3.</b> Why the two settings diverge. On card-feed data the error rate stays above budget until confidence is
very high; with the upstream category it falls under budget much sooner.</figcaption></figure>

<h2>A simulated policy agent</h2>
<p>Expense-policy decisions (approve, reject or send for review, citing the policy section) have no public labels, so this part
uses a documented generator with planted ground truth. Every assumption in it is varied in Table 5.</p>
<div class="tbl wide"><p class=cap><b>Table 3.</b> Live months, mean ± standard deviation across seeds, 1% error budget.</p>{{synthetic_table}}</div>
<div class="tbl wide"><p class=cap><b>Table 4.</b> Dropping one safeguard at a time.</p>{{ablation_table}}</div>
<div class="tbl wide"><p class=cap><b>Table 5.</b> Does the conclusion survive changing the generator's assumptions?</p>{{sensitivity_table}}</div>

<h2>The warehouse underneath</h2>
<p>The data layer is dbt on DuckDB over every file the council has published since 2017, reconciled row for row against a
SHA-256 manifest. The <a href="dbt/">lineage graph and column docs</a> are browsable.</p>
<div class="tbl wide"><p class=cap><b>Table 6.</b> What the warehouse holds and the checks it runs.</p>{{warehouse_table}}</div>

<footer><p>Every number on this page comes from <code>results/</code> and is rebuilt by <code>make reproduce</code>. Method notes
are in <a href="{{repo}}/blob/main/docs/METHODOLOGY.md">METHODOLOGY.md</a>.</p>
<p>Code: MIT licence. Data: Stockport Metropolitan Borough Council purchase-card transactions, Open Government Licence v3.0.
Embeddings: bge-small-en-v1.5, run locally.</p></footer>
</main></div></body></html>
"""
