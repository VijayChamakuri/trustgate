-- The supervised GL-coding dataset: lines with a real booked account (placeholders and
-- refunds excluded). Python benchmarks read *only* this model, so the exact training
-- population is defined, tested and documented in one place.
select
    card_line_id,
    statement_month,
    purchase_date,
    year(statement_month)                                   as statement_year,
    merchant_raw,
    merchant_key,
    merchant_category,
    department,
    service_category,
    amount,
    gl_account
from {{ ref('stg_stockport__card_transactions') }}
where gl_account is not null
  and not is_placeholder_label
  and not is_refund
