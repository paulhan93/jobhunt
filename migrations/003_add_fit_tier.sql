-- 003: add fit_tier to jobs (step 6c).
-- Note: SQLite's ALTER TABLE ADD COLUMN can't add CHECK constraints, so the
-- live DB lacks the CHECK present in schema.sql, same as migration 001.
ALTER TABLE jobs ADD COLUMN fit_tier TEXT;
