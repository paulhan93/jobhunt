PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS companies (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    ats        TEXT NOT NULL CHECK (ats IN (
                   'greenhouse','lever','ashby','smartrecruiters','workable')),
    slug       TEXT NOT NULL,
    active     INTEGER NOT NULL DEFAULT 1,
    notes      TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (ats, slug)
);


CREATE TABLE IF NOT EXISTS jobs (
    id            INTEGER PRIMARY KEY,
    company_id    INTEGER REFERENCES companies(id),
    source        TEXT NOT NULL,
    source_job_id TEXT NOT NULL,

    title         TEXT NOT NULL,
    location      TEXT,
    remote        INTEGER,
    description   TEXT,
    apply_url     TEXT,
    comp_min      REAL,
    comp_max      REAL,
    comp_currency TEXT,

    raw_json      TEXT NOT NULL,

    first_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen_at  TEXT NOT NULL DEFAULT (datetime('now')),
    closed_at     TEXT,

    status        TEXT NOT NULL DEFAULT 'new' CHECK (status IN (
                      'new','filtered','extracted','scored',
                      'reviewed','applied','rejected','error')),
    role_family   TEXT CHECK (role_family IN (
                      'swe','sdet','platform','sre','customer_eng','tpm','ai_eng')),
    reject_reason TEXT,
    fit_score     REAL,
    fit_tier      TEXT CHECK (fit_tier IN ('apply','stretch','skip')),
    attempts      INTEGER NOT NULL DEFAULT 0,
    last_error    TEXT,

    UNIQUE (source, source_job_id)
);


CREATE TABLE IF NOT EXISTS requirements (
    id             INTEGER PRIMARY KEY,
    job_id         INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    text           TEXT NOT NULL,
    kind           TEXT NOT NULL CHECK (kind IN ('must','nice')),
    skill_key      TEXT,
    years_required REAL,
    matched_bullets TEXT,          -- JSON array of bullet IDs
    match_strength  TEXT CHECK (match_strength IN ('strong','moderate','weak','none'))
);


CREATE TABLE IF NOT EXISTS applications (
    id              INTEGER PRIMARY KEY,
    job_id          INTEGER NOT NULL UNIQUE REFERENCES jobs(id),
    applied_at      TEXT NOT NULL DEFAULT (datetime('now')),
    resume_version  TEXT,
    referral        TEXT,
    heard_back      INTEGER NOT NULL DEFAULT 0,
    outcome         TEXT,
    notes           TEXT,

    -- Snapshot of jobs.fit_score/fit_tier at the moment of application —
    -- jobs.fit_score is live and can drift if scoring is re-run later, so
    -- this preserves what was actually true when the decision was made.
    fit_score_at_application REAL,
    fit_tier_at_application   TEXT CHECK (fit_tier_at_application IN ('apply','stretch','skip'))
);

-- Datasette-browsable queue: fit score/tier alongside an explicit `applied`
-- flag, so "what's left to review" and "what's already applied" don't need
-- a hand-written join each time (migration 006).
CREATE VIEW IF NOT EXISTS review_queue AS
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

-- migration 009: the queue this pipeline is built around (§3: "status column
-- plus a partial index is a perfectly good work queue at this volume") and
-- the joins/filters every stage runs on job_id, company_id, and skill_key.
CREATE INDEX IF NOT EXISTS idx_jobs_queue
    ON jobs(status) WHERE closed_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company_id);
CREATE INDEX IF NOT EXISTS idx_req_job      ON requirements(job_id);
CREATE INDEX IF NOT EXISTS idx_req_skill    ON requirements(skill_key);

-- migration 010: review_queue (above) filters on status with no closed_at
-- clause, so it can't use the partial idx_jobs_queue above and was doing a
-- full table scan of jobs (159MB, mostly description/raw_json) on every
-- Datasette page load, tripping the default sql_time_limit_ms.
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
