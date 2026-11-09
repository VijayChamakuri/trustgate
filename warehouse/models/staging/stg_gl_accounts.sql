select gl_code, gl_name from {{ source('raw', 'gl_accounts') }}
