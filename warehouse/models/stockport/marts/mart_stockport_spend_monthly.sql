select
    statement_month,
    merchant_category,
    count(*)                    as card_lines,
    sum(amount)                 as spend,
    sum(amount) filter (where is_refund) as refunds
from {{ ref('fct_stockport_card_spend') }}
group by all
