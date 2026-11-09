-- Daily calendar required by the dbt semantic layer (MetricFlow).
select cast(range as date) as date_day
from range(date '2016-01-01', date '2028-01-01', interval 1 day)
