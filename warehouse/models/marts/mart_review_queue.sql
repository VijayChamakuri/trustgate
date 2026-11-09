-- What humans actually need to look at, highest expected risk first.
select
    txn_id,
    posted_date,
    employee_id,
    dept_id,
    merchant_name,
    amount,
    decision,
    decision_confidence,
    cited_sections,
    rationale,
    round(amount * (1 - coalesce(decision_confidence, 0))
          + case when decision = 'REJECT' then amount else 0 end, 2)    as priority_score
from {{ ref('fct_card_transactions') }}
where decision_route = 'human'
order by priority_score desc
