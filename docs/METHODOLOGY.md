# Methodology

This document describes how every number in the README is produced. Numbers themselves live in
[`results/`](../results) and are rendered into the README by `trustgate readme`; this page explains the
procedures, the controls against leakage, and the statistics.

## 1. Question

A finance agent proposes an action: post a card line to a GL account, or approve, reject or flag an
expense. **When may it execute that action without a human?** The answer has to hold up against:

- **finite evidence:** a few hundred audited examples, not millions;
- **drift:** new merchants, new memo phrasing, new cost codes;
- **unequal error costs:** a mis-posted £1,400 hotel matters more than a £4 coffee.

## 2. The autonomy gate

For an action type *a* (auto-post, auto-approve, auto-reject) with model confidence *c* and audited
outcomes from earlier months:

1. **Candidate thresholds.** τ ∈ {0.50, 0.51, …, 0.95, 0.9525, …, 1.0}.
2. **Error estimate.** For each τ, take the audited items with *c* ≥ τ, count errors *e* out of *n*, and compute the **Wilson 95% upper bound** *U(e, n)*.
3. **Unlock rule.** Unlock the lowest τ with *n* ≥ 50 and *U* ≤ budget. If none qualifies, the action stays human. Taking the lowest such τ maximises automation.
4. **Value tiers.** Items at or above a value threshold (£250 real, $500 synthetic) must earn their own threshold from their own audited evidence.
5. **Novelty guard.** An item routes to a human, whatever its score, if its merchant key was never seen in training (real track) or its memo contains words never seen in training (synthetic track). Thresholds are then estimated only on the non-novel traffic they will govern.
6. **Monthly re-estimation.** Thresholds are recomputed every month from audits of *earlier* months only. Autonomy is earned, and can be lost.

**Why the upper bound.** A point estimate of 0/40 errors looks perfect, but its 95% upper bound is 8.8%. The bound makes "not enough evidence yet" a first-class outcome. The policy tables compare it directly with point-estimate thresholds.

## 3. Real-data track (Stockport MBC purchase cards)

**Source.** Stockport Metropolitan Borough Council publishes monthly corporate purchase-card transactions under the Open Government Licence v3.0 (data.gov.uk dataset `purchase-card-transactions`).
- `trustgate real` downloads every file listed by the CKAN API.
- It records URL, SHA-256, bytes and parsed row count in `data/stockport/manifest.json`.
- dbt test `assert_stockport_rows_reconcile_to_manifest` fails if any file's row count differs once in the warehouse.

**Schema drift.** The publisher changed format three times (see [DATA_CARD.md](DATA_CARD.md)):
- 10 columns with net amounts, department, service and booked purpose;
- an 8-column variant without the service category;
- from mid-2022, a 6-column layout with gross amounts, a banner row, and no department, service or purpose;
- one month published as an `.xlsx` file with a `.csv` extension.

All layouts are unioned in the parser, typed and tested in dbt staging, and tagged with `schema_version`.

**Labels.**
- "Purpose of Expenditure" is the account staff booked. Its 61 raw strings (case variants, typos, merged codes) map to 21 canonical accounts via the reviewed seed `warehouse/seeds/stockport_purpose_map.csv`.
- Placeholder strings ("details to follow", "TO BE ADDED", a publisher "MISCLASSIFIED?" flag) and refunds are excluded from scoring and counted.

**Two feature settings.**
- **Card feed:** merchant string, cardholder department, service category and amount. This is what a card program sees, so it's the headline.
- **Upstream:** card feed plus the council's procurement category. That field was assigned by staff alongside the booked purpose and predicts it almost perfectly on its own, so this setting is reported as a reference for "an upstream system already categorised the line".

**Rolling temporal evaluation.** For each test year *t* ∈ {2018, …, 2022}, models train on all labelled lines from years before *t* and are scored on year *t*. Results are pooled across years, with a bootstrap 95% CI over lines (2,000 resamples).

**Label ceiling.** For identical inputs (same merchant string, department and service), staff did not always book the same account. The ceiling is the mean share of the majority account within each identical-input group, which is the best any deterministic model can score.

**Models.**
- majority class;
- merchant → account lookup with a department+service fallback;
- character TF-IDF (2–5 grams) + one-hot + log-amount logistic regression;
- local-embedding kNN and logistic regression, using `BAAI/bge-small-en-v1.5` via fastembed (ONNX, CPU, no API);
- the combined TF-IDF + embedding model.

**Autonomy rollout.**
- Live months begin after a 6-month shadow period starting January 2020.
- The model is refit on all prior months every 3 months (expanding window).
- Each month, 40 random lines are audited, i.e. their true booking is revealed to the gate. Thresholds for month *m* use audits from months before *m*.
- Reported per policy and per budget (2%, 5%, 10%): share auto-posted, realised error with a Wilson CI, months whose realised error exceeded the budget, and value mis-posted.

## 4. Synthetic policy-agent track

Expense-policy decisions have no public labels, so this track uses a generator ([`generate.py`](../src/trustgate/generate.py)) that plants ground truth: what was really on the receipt, who attended, the cabin class, whether the purchase was personal.

The agent sees only what a platform would see: card descriptors, MCCs, memos and receipt text.

**Pipeline.**
1. dbt staging.
2. Merchant resolution (retrieval + fuzzy matching + look-alike guardrails, forced MATCH/NEW/ESCALATE).
3. dbt agent-ready features (duplicates, splits, limit breaches, receipt gaps).
4. Signal extraction (rules, local embeddings, or Claude).
5. A deterministic policy engine with cited sections.
6. A calibrated verifier estimating P(decision is correct).
7. The autonomy gate.

**Splits.** Months 1–6 train the verifier and GL coder on *reviewer history*. That history is deliberately lenient and noisy, because real reviewer clicks are. Months 7–8 are shadow, with 500 random audits a month. Months 9–12 are live.

**Robustness.**
- **Seeds:** 10 independent worlds, reported as mean ± sd.
- **Sensitivity:** each generator assumption varied in turn, with 3 seeds per point. The assumptions are reviewer leniency 0–15%, memo drift 0–50%, audits per month 100–1,000, and error budget 0.5–2%.
- **Ablations:** the final policy with one safeguard removed.
- **Perception backends:** rules vs rules + local embeddings, with everything else fixed.

## 5. What is *not* claimed

- No paid-API LLM results are reported. The Claude extractor is implemented and unit-tested against a fake client, but was not benchmarked.
- Synthetic rates are assumptions. Conclusions are stated only where they survive the sensitivity analysis.
- Stockport is one public-sector organisation. Its coding conventions are not a commercial company's, and the card-feed features are a subset of what a commercial platform sees (no receipts, no MCC in the labelled years).
