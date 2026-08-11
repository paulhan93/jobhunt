-- 001: add role_family to jobs (step 5).
-- Note: SQLite's ALTER TABLE ADD COLUMN can't add CHECK constraints, so live
-- DBs lack the CHECK present in schema.sql. Fresh DBs from reset.sh get it.
ALTER TABLE jobs ADD COLUMN role_family TEXT;
