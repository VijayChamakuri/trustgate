-- Same merchant and amount charged again within 3 days: candidates for a dispute review.
with lines as (
    select *, lag(purchase_date) over (partition by merchant_key, amount order by purchase_date, card_line_id) as prev_date,
              lag(card_line_id)  over (partition by merchant_key, amount order by purchase_date, card_line_id) as prev_line
    from {{ ref('fct_stockport_card_spend') }}
    where not is_refund and amount >= 20
)
select card_line_id, prev_line as possible_duplicate_of, merchant_key, amount, purchase_date,
       date_diff('day', prev_date, purchase_date) as days_apart
from lines
where prev_date is not null and date_diff('day', prev_date, purchase_date) between 0 and 3
