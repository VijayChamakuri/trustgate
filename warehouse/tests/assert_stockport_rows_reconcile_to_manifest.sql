-- Every downloaded file must land in staging with exactly the row count the parser reported.
with landed as (
    select source_file, count(*) as n from {{ ref('stg_stockport__card_transactions') }} group by 1
)
select f.source_file, f.parsed_rows, coalesce(l.n, 0) as landed_rows
from {{ source('stockport', 'source_files') }} f
left join landed l using (source_file)
where f.included and f.parsed_rows != coalesce(l.n, 0)
