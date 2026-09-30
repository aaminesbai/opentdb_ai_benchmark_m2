select
    category,
    count(*) as total_questions,
    sum(case when status = 'error' then 1 else 0 end) as technical_errors,
    round(100.0 * sum(case when status = 'error' then 1 else 0 end) / count(*), 2) as error_rate_pct
from stg_questions_enriched
group by category
order by error_rate_pct desc