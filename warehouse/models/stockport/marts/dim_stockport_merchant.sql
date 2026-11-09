select
    merchant_key,
    count(*)                                                    as card_lines,
    sum(amount)                                                 as total_spend,
    min(purchase_date)                                          as first_seen,
    max(purchase_date)                                          as last_seen,
    count(distinct merchant_raw)                                as raw_string_variants,
    mode(merchant_category)                                     as primary_merchant_category,
    mode(gl_account)                                            as primary_gl_account,
    count(distinct gl_account)                                  as distinct_gl_accounts
from {{ ref('fct_stockport_card_spend') }}
group by merchant_key
