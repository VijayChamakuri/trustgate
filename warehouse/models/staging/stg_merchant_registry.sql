select
    merchant_id,
    merchant_name,
    string_split(aliases, '|')                                         as aliases,
    default_gl,
    merchant_kind,
    case when require_any = '' then [] else string_split(require_any, '|') end as require_any,
    case when forbid_any = '' then [] else string_split(forbid_any, '|') end   as forbid_any
from {{ source('raw', 'merchant_registry') }}
