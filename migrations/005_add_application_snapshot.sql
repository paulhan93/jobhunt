-- 005: snapshot fit_score/fit_tier onto applications at apply time.
-- jobs.fit_score/fit_tier are live and can change if scoring is re-run later
-- (already happened more than once to filter rules in this project); without
-- a snapshot, a future "did apply-tier jobs convert better?" query would
-- silently join against today's score instead of the score that was actually
-- true when the application was made.
-- Note: SQLite's ALTER TABLE ADD COLUMN can't add CHECK constraints, same
-- caveat as migrations 001 and 003.
ALTER TABLE applications ADD COLUMN fit_score_at_application REAL;
ALTER TABLE applications ADD COLUMN fit_tier_at_application TEXT;
