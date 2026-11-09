-- Typed, cleaned card lines. Three publisher layouts are reconciled here:
--   v1 (2017-2020): net amount, transaction number, department, service, booked purpose
--   v2 (2020):      net amount, no service category
--   v3 (2022-):     gross amount only; department, service and purpose are no longer published
with src as (
    select * from {{ source('stockport', 'card_transactions') }}
),

typed as (
    select
        source_file || ':' || source_row                                       as card_line_id,
        source_file,
        cast(source_row as integer)                                            as source_row,
        schema_version,
        cast(period || '-01' as date)                                          as statement_month,
        try_strptime(purchase_date, ['%d.%m.%Y', '%d/%m/%Y', '%d-%m-%Y'])::date   as purchase_date,
        try_strptime(settlement_date, ['%d.%m.%Y', '%d/%m/%Y', '%d-%m-%Y'])::date as settlement_date,
        nullif(trim(department), '')                                           as department,
        nullif(trim(service_category), '')                                     as service_category,
        trim(merchant_raw)                                                     as merchant_raw,
        {{ normalize_merchant('merchant_raw') }}                               as merchant_key,
        nullif(upper(trim(merchant_category)), '')                             as merchant_category,
        lower(trim(purpose))                                                   as raw_purpose_key,
        nullif(trim(transaction_number), '')                                   as transaction_number,
        try_cast(amount as decimal(12, 2))                                     as amount,
        amount_basis
    from src
)

select
    t.*,
    m.gl_account,
    coalesce(m.is_placeholder, false)                                          as is_placeholder_label,
    t.raw_purpose_key is not null and m.raw_purpose_key is null                as is_unmapped_label,
    t.amount < 0                                                               as is_refund,
    t.settlement_date < t.purchase_date                                        as is_settled_before_purchase
from typed t
left join {{ ref('stockport_purpose_map') }} m using (raw_purpose_key)
