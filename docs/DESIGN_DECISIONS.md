# Design decisions

Short ADR-style records of the choices that shape the results.

### ADR-1: Gate autonomy on the Wilson upper bound, not the point estimate
**Context:** Thresholds are chosen from a few hundred audited labels.
**Decision:** Unlock an action only when the upper 95% Wilson bound of its error rate is within budget, with at least 50 audited examples above the threshold.
**Consequence:** Less automation early on. "Not enough evidence" becomes an explicit state instead of a silent risk. The point-estimate alternative is reported side by side so the cost of this choice is visible.

### ADR-2: Thresholds come from audited labels, never from reviewer clicks
**Context:** Historical approvals are lenient. Reviewers wave through items policy says should be reviewed.
**Decision:** Train on history, but choose thresholds only from a random audited sample.
**Consequence:** Needs an audit budget, which is quantified in the sensitivity analysis.

### ADR-3: Decisions are three-way with cited policy sections; confidence stays internal
**Context:** Reviewers need to know *why*, not a probability.
**Decision:** The policy engine emits APPROVE / REJECT / NEEDS_REVIEW plus section IDs. A separate verifier estimates P(correct) for gating only.
**Consequence:** Perception (extraction) can be swapped without touching policy logic. A dbt test enforces that every flag cites a section.

### ADR-4: Agent features live in dbt, not in agent code
**Context:** "Possible duplicate" or "split purchase" defined in three places drifts apart.
**Decision:** `int_txn_enriched` defines integrity signals once in reviewed SQL. Agents, marts and the review queue all read it.
**Consequence:** Agent behaviour changes go through the same review and test path as analytics.

### ADR-5: Report two real-data settings instead of one flattering number
**Context:** The council's procurement category nearly determines the booked account (staff assigned both).
**Decision:** The headline uses card-feed signals only. The upstream setting is shown as a labelled reference.
**Consequence:** The headline accuracy is much lower, and honest. It also shows the gate's most useful behaviour: refusing autonomy when the data can't support it.

### ADR-6: Novelty guard on unseen merchants and unseen memo words
**Context:** Confidence calibrated on past traffic is unreliable on inputs unlike anything seen.
**Decision:** Route any item with an unseen merchant key (real) or unseen memo vocabulary (synthetic) to a human, and estimate thresholds only on the traffic they govern.
**Consequence:** Lower coverage under drift, in exchange for staying within budget.

### ADR-7: Value tiers earn autonomy separately
**Context:** Errors are not equal, and a single threshold is dominated by small-ticket volume.
**Decision:** High-value items need their own evidence. With random audits they usually stay human.
**Consequence:** A large cut in leaked dollars. Value-stratified audits are the natural next step.

### ADR-8: Free, local models only
**Context:** Results must be reproducible by anyone without paying for API calls.
**Decision:** fastembed ONNX models on CPU. The Claude extractor exists behind an extra and is unit-tested, but is not benchmarked.
**Consequence:** Perception results reflect the local embedding model named in `embeddings.py` (described there as 33 million parameters). The Claude extractor has no benchmark numbers.

### ADR-9: No hand-typed numbers in the README
**Context:** READMEs rot.
**Decision:** `README.md` and `docs/RESULTS.md` are rendered from templates and `results/*.json`. A test fails if either diverges.
