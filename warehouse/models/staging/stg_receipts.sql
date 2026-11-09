select
    receipt_id,
    txn_id,
    cast(receipt_total as decimal(12, 2)) as receipt_total,
    line_items
from {{ source('raw', 'receipts') }}
