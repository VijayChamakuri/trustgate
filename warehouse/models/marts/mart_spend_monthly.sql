-- Governed spend metric: spend by month x department x GL account x merchant.
select
    posted_month,
    dept_id,
    gl_code,
    gl_name,
    merchant_key,
    merchant_name,
    count(*)                    as txn_count,
    sum(amount)                 as spend
from {{ ref('fct_card_transactions') }}
group by all
