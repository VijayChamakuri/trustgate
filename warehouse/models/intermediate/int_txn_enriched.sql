-- Governed, agent-ready view of each transaction: resolved merchant, receipt facts and
-- integrity signals (duplicates, splits, limits). Agents read features from here instead of
-- recomputing them, so the definition of "possible duplicate" lives in one reviewed place.
with txns as (
    select * from {{ ref('stg_card_transactions') }}
),

resolved as (
    select
        t.*,
        r.merchant_id,
        r.action                                                         as resolution_action,
        r.score                                                          as resolution_score,
        coalesce(r.merchant_key, 'new:' || split_part(t.descriptor_clean, ' ', 1)) as merchant_key
    from txns t
    left join {{ source('agent', 'merchant_resolutions') }} r
        on r.descriptor_clean = t.descriptor_clean
),

with_context as (
    select
        r.*,
        m.merchant_name,
        m.merchant_kind,
        m.default_gl,
        e.dept_id,
        e.is_manager,
        e.is_traveler,
        e.city                                                           as home_city,
        e.single_txn_limit,
        rc.receipt_total,
        rc.line_items                                                    as receipt_text,
        rc.receipt_id is not null                                        as has_receipt,
        case when rc.receipt_total is null then null
             else round((r.amount - rc.receipt_total) / nullif(r.amount, 0), 4) end as receipt_gap_ratio
    from resolved r
    left join {{ ref('stg_merchant_registry') }} m using (merchant_id)
    left join {{ ref('stg_employees') }} e using (employee_id)
    left join {{ ref('stg_receipts') }} rc using (txn_id)
),

integrity as (
    select
        *,
        -- same card, merchant and amount within 3 days of an earlier charge (§9.1)
        coalesce(
            date_diff('day',
                lag(posted_date) over (partition by employee_id, merchant_key, amount order by posted_date, txn_id),
                posted_date) <= 3,
            false)                                                       as is_possible_duplicate,
        count(*) over (partition by employee_id, merchant_key, posted_date)          as same_day_merchant_count,
        sum(amount) over (partition by employee_id, merchant_key, posted_date)       as same_day_merchant_total,
        max(amount) over (partition by employee_id, merchant_key, posted_date)       as same_day_merchant_max,
        amount > single_txn_limit                                        as is_over_single_limit
    from with_context
)

select
    *,
    (same_day_merchant_count >= 2
        and same_day_merchant_total > single_txn_limit
        and same_day_merchant_max <= single_txn_limit)                   as is_possible_split
from integrity
