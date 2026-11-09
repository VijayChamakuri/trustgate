-- Recurring merchants (billed in >= 6 distinct months with a stable ticket): the real-data
-- version of SaaS / subscription monitoring, with first-vs-latest price change.
with monthly as (
    select merchant_key, statement_month, median(amount) as ticket, count(*) as n
    from {{ ref('fct_stockport_card_spend') }}
    where not is_refund
    group by all
),
stats as (
    select
        merchant_key,
        count(*)                                                    as months_billed,
        min(statement_month)                                        as first_month,
        max(statement_month)                                        as last_month,
        arg_min(ticket, statement_month)                            as first_ticket,
        arg_max(ticket, statement_month)                            as latest_ticket,
        stddev_pop(ticket) / nullif(avg(ticket), 0)                 as ticket_cv
    from monthly
    group by merchant_key
)
select
    *,
    round(latest_ticket / nullif(first_ticket, 0) - 1, 4)           as price_change_pct
from stats
where months_billed >= 6 and ticket_cv < 0.35
order by months_billed desc
