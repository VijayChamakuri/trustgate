-- Every REJECT or NEEDS_REVIEW must cite at least one policy section.
select txn_id, decision
from {{ ref('fct_card_transactions') }}
where decision in ('REJECT', 'NEEDS_REVIEW') and coalesce(cited_sections, '') = ''
