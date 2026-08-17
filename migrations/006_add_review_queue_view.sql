-- 006: review_queue view — jobs joined with company name and an explicit
-- `applied` flag/date, so Datasette can browse "what's queued vs. already
-- applied" without hand-writing the join each time (was being redone ad hoc
-- in chat, see PROJECT.md status block, 2026-08-17).
CREATE VIEW IF NOT EXISTS review_queue AS
SELECT
    j.id,
    j.title,
    c.name AS company,
    j.role_family,
    j.fit_score,
    j.fit_tier,
    j.status,
    CASE WHEN a.job_id IS NOT NULL THEN 1 ELSE 0 END AS applied,
    a.applied_at,
    j.apply_url
FROM jobs j
LEFT JOIN companies c ON c.id = j.company_id
LEFT JOIN applications a ON a.job_id = j.id
WHERE j.status IN ('scored', 'reviewed', 'applied')
ORDER BY j.fit_score DESC;
