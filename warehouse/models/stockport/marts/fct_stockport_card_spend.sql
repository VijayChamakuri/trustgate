-- Governed fact table: one row per real card line, 2017 to present.
select
    card_line_id,
    statement_month,
    purchase_date,
    settlement_date,
    merchant_key,
    merchant_raw,
    merchant_category,
    department,
    service_category,
    gl_account,
    amount,
    amount_basis,
    is_refund,
    schema_version
from {{ ref('stg_stockport__card_transactions') }}
