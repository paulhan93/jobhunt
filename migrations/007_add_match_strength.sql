-- 007: add match_strength to requirements (step 6b/7b refinement). The
-- matcher previously only recorded WHICH bullets support a requirement, not
-- HOW WELL — every match counted identically toward fit_score whether it was
-- a direct hit or a stretched, tangential one. NULL for rows matched before
-- this migration, until scripts/rematch_all.py backfills them.
-- Note: SQLite's ALTER TABLE ADD COLUMN can't add CHECK constraints, so the
-- live DB lacks the CHECK present in schema.sql, same as migrations 001/003.
ALTER TABLE requirements ADD COLUMN match_strength TEXT;
