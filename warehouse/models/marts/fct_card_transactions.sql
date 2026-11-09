-- The governed ledger: one row per card transaction with the agent decisions attached.
-- Contract-enforced so downstream consumers (dashboards, other agents) get a stable schema.
select
    f.txn_id,
    f.posted_date,
    f.posted_month,
    f.employee_id,
    f.dept_id,
    f.merchant_key,
    f.merchant_id,
    coalesce(f.merchant_name, f.descriptor_clean)          as merchant_name,
    f.resolution_action,
    f.descriptor_raw,
    f.mcc,
    f.amount,
    g.gl_code,
    a.gl_name,
    g.gl_confidence,
    g.gl_route,
    p.decision,
    p.confidence                                           as decision_confidence,
    p.cited_sections,
    p.rationale,
    p.route                                                as decision_route,
    p.extractor
from {{ ref('int_txn_enriched') }} f
left join {{ source('agent', 'gl_predictions') }} g using (txn_id)
left join {{ ref('stg_gl_accounts') }} a on a.gl_code = g.gl_code
left join {{ source('agent', 'policy_decisions') }} p using (txn_id)
