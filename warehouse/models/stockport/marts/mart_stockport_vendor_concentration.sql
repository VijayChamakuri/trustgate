-- How concentrated is card spend? Top-10 merchant share and Herfindahl index per year.
with yearly as (
    select year(statement_month) as yr, merchant_key, sum(amount) as spend
    from {{ ref('fct_stockport_card_spend') }}
    where not is_refund
    group by all
),
ranked as (
    select *, spend / sum(spend) over (partition by yr) as share,
           row_number() over (partition by yr order by spend desc) as rnk
    from yearly
)
select
    yr                                                   as year,
    count(*)                                             as merchants,
    sum(spend)                                           as spend,
    sum(share) filter (where rnk <= 10)                  as top10_share,
    sum(share * share) * 10000                           as hhi
from ranked
group by yr
order by yr
