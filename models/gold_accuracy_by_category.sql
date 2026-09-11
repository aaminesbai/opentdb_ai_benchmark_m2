select
    category,
    difficulty,
    count(*) as total_questions,
    sum(case when ai_correct then 1 else 0 end) as correct_answers,
    round(100.0 * sum(case when ai_correct then 1 else 0 end) / count(*), 2) as accuracy_pct,
    round(avg(response_time), 3) as avg_response_time_sec
from {{ ref('stg_questions_enriched') }}
where status = 'success'
group by category, difficulty
order by category, difficulty
