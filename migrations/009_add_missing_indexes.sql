-- 009: add the indexes PROJECT.md §4 already documents as existing but never
-- actually shipped, schema.sql had no CREATE INDEX statements at all, and
-- the live database confirmed the same (only the UNIQUE-constraint
-- autoindexes were present). Found 2026-08-17 while fixing fetch_all.py's
-- COUNT(*) query to scope by company_id (architecture review): the scoped
-- query was correct but EXPLAIN QUERY PLAN showed a full table SCAN because
-- idx_jobs_company didn't exist to use. Purely additive: same query
-- results either way, only the plan used to get them changes.
--
-- idx_jobs_queue is the one PROJECT.md §3 calls "a perfectly good work
-- queue at this volume" (status column + partial index), the whole
-- fetch/filter/extract/score pipeline's stage-advancing queries
-- (WHERE status = 'X' AND closed_at IS NULL) were running unindexed.
CREATE INDEX IF NOT EXISTS idx_jobs_queue
    ON jobs(status) WHERE closed_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company_id);
CREATE INDEX IF NOT EXISTS idx_req_job      ON requirements(job_id);
CREATE INDEX IF NOT EXISTS idx_req_skill    ON requirements(skill_key);
