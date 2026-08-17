-- 008: extend review_queue (migration 006) with the fields that actually
-- matter when deciding whether to apply - comp range and location/remote -
-- requested directly, 2026-08-17. SQLite has no CREATE OR REPLACE VIEW, so
-- drop and recreate.
DROP VIEW IF EXISTS review_queue;

CREATE VIEW review_queue AS
SELECT
    j.id,
    j.title,
    c.name AS company,
    j.role_family,
    j.fit_score,
    j.fit_tier,
    CASE WHEN a.job_id IS NOT NULL THEN 1 ELSE 0 END AS applied,
    a.applied_at,
    j.location,
    j.remote,
    j.comp_min,
    j.comp_max,
    j.comp_currency,
    j.status,
    j.apply_url
FROM jobs j
LEFT JOIN companies c ON c.id = j.company_id
LEFT JOIN applications a ON a.job_id = j.id
WHERE j.status IN ('scored', 'reviewed', 'applied')
ORDER BY j.fit_score DESC;
