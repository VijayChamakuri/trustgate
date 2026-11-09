-- Nothing is auto-executed with low verifier confidence, and NEEDS_REVIEW is never automated.
select txn_id, decision, decision_confidence
from {{ ref('fct_card_transactions') }}
where decision_route = 'auto' and (decision = 'NEEDS_REVIEW' or decision_confidence < 0.5)
