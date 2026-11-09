-- The governed ledger must tie out to raw card data to the cent: no rows lost or duplicated by joins.
select raw_total, fct_total
from (select sum(cast(amount as decimal(14, 2))) as raw_total from {{ source('raw', 'card_transactions') }}) r,
     (select sum(amount) as fct_total, count(*) as n from {{ ref('fct_card_transactions') }}) f
where raw_total != fct_total
   or f.n != (select count(*) from {{ source('raw', 'card_transactions') }})
