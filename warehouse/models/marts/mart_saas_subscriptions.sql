-- Recurring software spend: one row per (tool, paying cardholder). Flags price creep,
-- subscriptions still billing after the owner left, and the same tool paid for twice.
with software as (
    select
        f.merchant_key,
        f.merchant_name,
        f.employee_id,
        f.posted_date,
        f.posted_month,
        f.amount,
        e.dept_id                                                         as owner_dept,
        e.termination_date,
        min(f.posted_month) over w                                        as sub_first_month,
        max(f.posted_month) over w                                        as sub_last_month
    from {{ ref('fct_card_transactions') }} f
    join {{ ref('stg_employees') }} e using (employee_id)
    where f.gl_code in ('6100', '6600')
    window w as (partition by f.merchant_key, f.employee_id)
),

per_sub as (
    select
        merchant_key,
        any_value(merchant_name)                                          as merchant_name,
        employee_id                                                       as owner_employee_id,
        any_value(owner_dept)                                             as owner_dept,
        any_value(termination_date)                                       as owner_termination_date,
        count(distinct posted_month)                                      as months_billed,
        min(posted_date)                                                  as first_charge,
        max(posted_date)                                                  as last_charge,
        sum(amount)                                                       as total_spend,
        avg(amount) filter (where posted_month < sub_first_month + interval 3 month)  as early_avg,
        avg(amount) filter (where posted_month > sub_last_month - interval 3 month)   as recent_avg,
        coalesce(sum(amount) filter (where posted_date > termination_date), 0)        as spend_after_owner_left
    from software
    group by merchant_key, employee_id
    having count(distinct posted_month) >= 4
)

select
    *,
    round(recent_avg / nullif(early_avg, 0) - 1, 4)                       as price_change_pct,
    coalesce(recent_avg / nullif(early_avg, 0) - 1 > 0.05, false)         as is_price_creep,
    spend_after_owner_left > 0                                            as is_orphaned,
    count(*) over (partition by merchant_key) > 1                         as is_duplicate_tool
from per_sub
