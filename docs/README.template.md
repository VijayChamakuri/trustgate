<!-- Generated from docs/README.template.md by `trustgate readme`. Edit the template, not this file. -->
# TrustGate

[![Continuous integration](https://github.com/VijayChamakuri/trustgate/actions/workflows/ci.yml/badge.svg)](https://github.com/VijayChamakuri/trustgate/actions/workflows/ci.yml)
[![Python 3.12](https://img.shields.io/badge/python-3.12-3776ab)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Council staff book each purchase-card line to a ledger account, and someone has to decide when a model may do that, or approve an expense, with no person checking. I built a gate that allows the action only when the upper bound on its audited error rate fits a budget, and I tested it on Stockport Council's published cards and on a synthetic expense-policy agent. On the last {{live_months}} months of council lines ({{live_lines}}), it booked {{up_auto}} of them alone when a staff category was available, at {{up_err}} error, and booked none from the card feed alone.

![Share of lines booked with no review. Left, card feed only. Right, with the staff category. Red bars went over the 2% budget.](results/real/hero.png)

*Share of live lines booked with no review, at a 2% error budget. Red bars went over that budget in at least one month. Panel titles on the figure round accuracy to the nearest percent.*

| What the model can see | Booked with no review | Error on those lines | Months over the 2% budget |
|---|---:|---:|---:|
| Card feed only | {{cf_earned_auto}} | {{cf_earned_err}} | {{cf_earned_over}} of {{live_months}} |
| Card feed plus the staff category | {{up_auto}} | {{up_err}} | {{up_over}} of {{live_months}} |

*Earned autonomy on the last {{live_months}} labelled months: Wilson upper bound, a novelty guard, and a separate threshold for larger amounts. A dash means no lines were booked, so there is no error rate.*

Booked with no review means the gate posted the general ledger (GL) account and no person checked that line. An audit is a random sample whose true booking a person reveals; this rollout uses {{audits}} lines a month, and the gate for a month may use only earlier months. The Wilson upper bound is the top of a 95% Wilson score interval on the error rate in that sample. It stays high when the sample is small, so the action stays with a person. The card feed is the merchant string, the cardholder's department and service, and the amount. The staff category is the council's procurement category, assigned alongside the booked account.

The gate books a large share of lines when that category is present and books nothing when it is not. The usual shortcut, a threshold set from the average audited error, booked {{cf_point_auto}} of the card-feed lines at {{cf_point_err}} error and went over the budget in {{cf_point_over}} of {{live_months}} months. Intervals and the other policies are in [docs/RESULTS.md](docs/RESULTS.md).

## How it works

### The gate

The rule is `choose_threshold` in [`autonomy.py`](src/trustgate/autonomy.py). It walks a fixed grid of score cutoffs and takes the lowest one where at least 50 audited examples sit above it and the Wilson upper bound on their error rate is inside the budget. The cutoff is refit every month from earlier audits only, so a later month can lose the right to act. A merchant the model has not seen, or a memo word absent from training in the synthetic world, goes to a person whatever the score, and larger amounts ({{high_value_gbp}} on the council file, {{high_value_usd}} in the synthetic gate) have to earn a cutoff of their own.

```python
for tau in GRID:
    sel = conf >= tau
    n = int(sel.sum())
    if n < min_n:
        return None
    errors = int((~correct[sel]).sum())
    bound = wilson_interval(errors, n)[1] if conservative else errors / n
    if bound <= budget:
        return float(tau)
return None
```

`conservative=False` is the average-error shortcut, reported beside the bound in the tables.

### Coding the council lines

The label is the GL account staff booked, and a reviewed seed maps each raw purpose string onto one of {{real_accounts}} accounts ([`stockport_purpose_map.csv`](warehouse/seeds/stockport_purpose_map.csv)). For each year from 2018 through 2022 the coder in [`realgl.py`](src/trustgate/realgl.py) trains only on earlier years. On card-feed fields, character n-grams plus a local embedding reach {{best_acc}} on {{gl_eval_lines}} test lines, against {{lookup_acc}} for a merchant lookup and {{best_new}} against {{lookup_new}} on merchants the model has not seen. Staff book identical inputs differently often enough to cap any deterministic model at {{ceiling_card}}; the vectorizer settings are in [docs/METHODOLOGY.md](docs/METHODOLOGY.md).

### The policy agent

Expense decisions have no public labels, so a generator plants the receipt, who attended, the cabin, and whether the purchase was personal ([`generate.py`](src/trustgate/generate.py)). The agent sees the card descriptor, the merchant category code (MCC), the memo, and the receipt text. It resolves the merchant, fills a fixed signal record, and applies a policy that cites section ids ([`resolve.py`](src/trustgate/resolve.py), [`extract.py`](src/trustgate/extract.py), [`policy.py`](src/trustgate/policy.py)). Across 10 seeds the earned-autonomy policy kept the realized error at or under its 1% budget every time; a threshold fit to the average audited error broke that budget in {{point_over}}.

### The warehouse

Both tracks sit in a dbt (data build tool) project on DuckDB. The council publish is three layouts, including one spreadsheet saved with a `.csv` name, checked against a SHA-256 (256-bit secure hash) manifest ([`sources/stockport.py`](src/trustgate/sources/stockport.py), [`warehouse/models/stockport`](warehouse/models/stockport)). Duplicate charges, split purchases, limit breaches, and missing receipts are defined once in SQL (Structured Query Language) and read by the agent and the marts ([`int_txn_enriched.sql`](warehouse/models/intermediate/int_txn_enriched.sql)). The warehouse holds every published line from January 2017 through October 2026; scoring uses the {{real_lines}} labelled lines from {{real_years}}.

## Design decisions

| Choice | Why | Cost |
|---|---|---|
| Wilson upper bound, and at least 50 audits | Zero errors in a small sample can still hide a high error rate. The same function puts the upper bound for 0 errors in 40 audits at {{wilson_0_40}} | The gate waits. Early months book nothing |
| Thresholds from a random audit, not from reviewer clicks | Past approvals are lenient. Thresholds learned from them broke the 1% budget in {{hist_over}} synthetic seeds | Someone has to label the sample: {{audits}} council lines a month, {{syn_audits}} in the default synthetic world |
| Novelty guard | The score was fit on traffic the model has seen | {{novel_share}} of the live council lines are new merchants and stay with a person |
| Separate amount tiers | One threshold is dominated by small amounts | Large lines usually stay with a person when the audit is a random sample |
| Treat the card feed as the result | Staff assigned the procurement category with the label. A lookup on that category alone is {{up_cat_acc}} | The card-feed model is {{best_acc}} accurate, and the gate books nothing from it |
| Local embeddings only, in the reported numbers | The run has to repeat without paying for a model call | The Claude extractor is implemented and unit-tested, and it has no benchmark numbers |

The short records behind these rows are in [docs/DESIGN_DECISIONS.md](docs/DESIGN_DECISIONS.md).

## Evaluation

The gate's own checks are in [`tests/test_autonomy.py`](tests/test_autonomy.py): the interval matches known values, a perfect sample of 30 never earns autonomy when 50 are required, the Wilson cutoff is at least as strict as the average-error cutoff, and routing refuses a needs-review decision, a novel line, and a locked high-value tier.

The rest of the unit suite:

- [`tests/test_realgl.py`](tests/test_realgl.py) — label ceiling, lookup fallback for an unseen merchant, calibration error of a perfect score
- [`tests/test_policy.py`](tests/test_policy.py) — cited decisions, and the rules extractor missing a Negroni and a "Delta One" cabin
- [`tests/test_resolve_and_extract.py`](tests/test_resolve_and_extract.py) — look-alike merchants stay split, an unknown shop is new, the Claude extractor is called with a tool schema against a fake client
- [`tests/test_stockport.py`](tests/test_stockport.py) — the three publisher layouts, including a spreadsheet with a `.csv` name
- [`tests/test_generate.py`](tests/test_generate.py) — generation is deterministic, and gold labels stay out of the warehouse
- [`tests/test_readme.py`](tests/test_readme.py) — this page and [docs/RESULTS.md](docs/RESULTS.md) match a fresh render

`make test` runs ruff, mypy, and pytest. The suite is {{n_unit_tests}} tests. Continuous integration (CI) runs the same three checks ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)).

The warehouse build runs five SQL tests:

- [`assert_stockport_rows_reconcile_to_manifest.sql`](warehouse/tests/assert_stockport_rows_reconcile_to_manifest.sql) — each file's row count matches the manifest
- [`assert_ledger_reconciles.sql`](warehouse/tests/assert_ledger_reconciles.sql) — fact totals tie to the raw amounts
- [`assert_auto_actions_are_confident.sql`](warehouse/tests/assert_auto_actions_are_confident.sql) — nothing automatic below 0.5 confidence, and needs-review is never automatic
- [`assert_flags_cite_policy.sql`](warehouse/tests/assert_flags_cite_policy.sql) — every reject or review cites a section
- [`assert_no_protected_merge.sql`](warehouse/tests/assert_no_protected_merge.sql) — Amazon is not merged with Amazon Web Services, Uber with Uber Eats, or Apple with Applebee's

The smoke job in that workflow runs the warehouse build, which includes those tests, and then the default synthetic world.

The two benchmarks are the other check. On the council file, models are scored year by year and the gate is rolled forward at 2%, 5%, and 10% budgets ([`results/real/`](results/real)). On the synthetic agent, 10 seeds, a one-at-a-time sensitivity sweep with 3 seeds at each point, and a drop-one ablation ([`results/experiments.json`](results/experiments.json)). Earned autonomy kept the realized error at or under the 1% budget in {{syn_within_budget}} seeds and at all {{sens_earned_ok}} of {{sens_total}} sensitivity points. The point-estimate threshold broke the budget in {{point_over}} seeds. The tables are in [docs/RESULTS.md](docs/RESULTS.md).

## Limitations

- One council. In the labelled years the file has no MCC and no receipts, and the chart of accounts is a local authority's.
- The policy agent is synthetic. Every planted rate is an assumption, listed in the [data card](docs/DATA_CARD.md). I only treat a comparison as holding where the sensitivity sweep agrees.
- The Claude extractor has no measured numbers.
- Audits are a random sample, so the high-value tier rarely collects 50 labels and usually stays with a person.
- Published amounts are net of value-added tax (VAT) through June 2022 and gross after that.

## What I'd do next

- Draw the audit by amount, so the high-value tier can earn a cutoff. The rollout samples lines uniformly.
- Run the Claude extractor through the same seed harness. The class is already in [`extract.py`](src/trustgate/extract.py); the benchmark does not call it.
- On the card feed, return a shortlist instead of a booking. The booked account is in the model's top 3 on {{cf_top3}} of the live lines, and the gate correctly refuses to post.

## Run it

```bash
git clone https://github.com/VijayChamakuri/trustgate
cd trustgate
uv sync
make test
make real
make synthetic
```

`make real` downloads the council files, checks them, builds the dbt project, and runs the GL benchmark and the monthly rollout. `make synthetic` is the policy agent on the default world (seed 7). `make experiments` reruns the 10 seeds, the sensitivity sweep, and the two perception backends. `make reproduce` runs all of that, then the site and this README.

The GL benchmark file records {{bench_seconds}} seconds. The experiment file records {{exp_seconds}} seconds. Raw card files are not in the repo; the manifest and the licence are in the [data card](docs/DATA_CARD.md).

The results write-up and the dbt lineage docs are published from [`site/`](site): [vijaychamakuri.github.io/trustgate](https://vijaychamakuri.github.io/trustgate/) and [the dbt docs](https://vijaychamakuri.github.io/trustgate/dbt/).

## Repo map

- [`src/trustgate/autonomy.py`](src/trustgate/autonomy.py) — the gate
- [`src/trustgate/realgl.py`](src/trustgate/realgl.py) — council GL benchmark and rollout
- [`src/trustgate/policy.py`](src/trustgate/policy.py), [`extract.py`](src/trustgate/extract.py), [`resolve.py`](src/trustgate/resolve.py), [`generate.py`](src/trustgate/generate.py) — the policy agent
- [`src/trustgate/sources/stockport.py`](src/trustgate/sources/stockport.py) — download, checksums, three-layout parser
- [`warehouse/`](warehouse) — dbt project, both tracks
- [`policy/expense_policy.md`](policy/expense_policy.md) — the policy the agent cites
- [`results/`](results) — the result files and charts behind every number
- [`docs/METHODOLOGY.md`](docs/METHODOLOGY.md), [`docs/RESULTS.md`](docs/RESULTS.md), [`docs/DATA_CARD.md`](docs/DATA_CARD.md), [`docs/DESIGN_DECISIONS.md`](docs/DESIGN_DECISIONS.md)

Code is under the MIT license ([LICENSE](LICENSE)). The council file is Open Government Licence v3.0. Cite with [CITATION.cff](CITATION.cff).
