-- One row per card transaction, with a normalized descriptor that every downstream
-- consumer (dbt marts and the agents) shares, so "what merchant is this" is defined once.
with src as (
    select * from {{ source('raw', 'card_transactions') }}
),

cleaned as (
    select
        txn_id,
        cast(posted_date as date)                                    as posted_date,
        cast(date_trunc('month', cast(posted_date as date)) as date) as posted_month,
        employee_id,
        card_last4,
        descriptor                                                   as descriptor_raw,
        trim(regexp_replace(
            regexp_replace(
                regexp_replace(
                    upper(descriptor),
                    '^(SQ \*|SQ\*|TST\* ?|SP \* ?|PY \*|CLV\*|DD \*|PAYPAL \*)', ''),  -- processor prefixes
                '[#*]', ' ', 'g'),
            '\s+', ' ', 'g'))                                        as descriptor_clean,
        mcc,
        cast(amount as decimal(12, 2))                               as amount,
        currency,
        nullif(trim(lower(memo)), '')                                as memo,
        dayofweek(cast(posted_date as date)) in (0, 6)               as is_weekend
    from src
)

select * from cleaned
