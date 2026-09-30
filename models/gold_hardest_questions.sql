select
    question_id,
    category,
    difficulty,
    question_clean,
    correct_answer_clean,
    ai_answer,
    count(*) as total_attempts,
    sum(case when ai_correct then 1 else 0 end) as correct_attempts,
    round(100.0 * sum(case when ai_correct then 1 else 0 end) / count(*), 2) as accuracy_pct
from stg_questions_enriched
where status = 'success'
group by question_id, category, difficulty, question_clean, correct_answer_clean, ai_answer
having sum(case when ai_correct then 1 else 0 end) < count(*)
order by accuracy_pct asc, category