select
    e.employee_id,
    e.full_name,
    e.dept_id,
    d.dept_name,
    e.title,
    e.is_manager,
    e.city,
    e.state,
    e.is_traveler,
    cast(e.single_txn_limit as decimal(12, 2)) as single_txn_limit,
    cast(e.termination_date as date)          as termination_date
from {{ source('raw', 'employees') }} e
left join {{ source('raw', 'departments') }} d using (dept_id)
