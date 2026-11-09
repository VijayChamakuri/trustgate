-- Look-alike merchants must never be merged (guardrail regression test).
select txn_id, merchant_id, descriptor_raw
from {{ ref('fct_card_transactions') }}
where (merchant_id = 'm_amazon' and regexp_matches(lower(descriptor_raw), 'aws|web services'))
   or (merchant_id = 'm_aws' and not regexp_matches(lower(descriptor_raw), 'aws|web services'))
   or (merchant_id = 'm_uber' and lower(descriptor_raw) like '%eats%')
   or (merchant_id = 'm_apple' and lower(descriptor_raw) like '%applebee%')
