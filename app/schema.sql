-- Vaani schema. Portable SQL that runs on SQLite (default) and PostgreSQL.
-- Timestamps are ISO-8601 UTC strings; JSON is stored as TEXT; booleans as INTEGER 0/1.

CREATE TABLE IF NOT EXISTS schema_version (
    version    INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

-- Faculty and admins sign in with email + password. Students sign in with their roll number and the viva's
-- access code, so they need no account creation.
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    role          TEXT NOT NULL,                 -- faculty | admin | student
    name          TEXT NOT NULL,
    email         TEXT UNIQUE,
    password_hash TEXT,
    roll_no       TEXT UNIQUE,
    department    TEXT,
    settings      TEXT DEFAULT '{}',
    created_at    TEXT NOT NULL,
    last_seen_at  TEXT
);

CREATE TABLE IF NOT EXISTS question_banks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id    INTEGER NOT NULL REFERENCES users(id),
    name        TEXT NOT NULL,
    subject     TEXT NOT NULL,
    description TEXT DEFAULT '',
    version     INTEGER NOT NULL DEFAULT 1,      -- bumped on every approved change; stored with each viva session
    aliases     TEXT DEFAULT '{}',               -- subject abbreviations the recogniser may produce, e.g. {"3nf": "third normal form"}
    validation  TEXT DEFAULT '{}',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS questions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    bank_id        INTEGER NOT NULL REFERENCES question_banks(id) ON DELETE CASCADE,
    code           TEXT NOT NULL,                -- stable human id, e.g. DBMS-F-03 (shown in transcripts)
    topic          TEXT NOT NULL,
    phase          TEXT NOT NULL,                -- warmup | fundamentals | depth | applied
    qtype          TEXT NOT NULL DEFAULT 'concept',   -- concept | scenario | debug | project
    difficulty     INTEGER NOT NULL DEFAULT 2,   -- 1 easy, 2 medium, 3 hard
    text           TEXT NOT NULL,
    rephrase       TEXT DEFAULT '',
    key_points     TEXT DEFAULT '[]',
    follow_ups     TEXT DEFAULT '[]',
    hints          TEXT DEFAULT '[]',
    misconceptions TEXT DEFAULT '[]',
    code_snippet   TEXT DEFAULT '',
    expected_s     INTEGER DEFAULT 60,
    tags           TEXT DEFAULT '[]',
    status         TEXT NOT NULL DEFAULT 'draft',     -- draft | approved | rejected
    source         TEXT NOT NULL DEFAULT 'manual',    -- upload | ai | manual
    ai_meta        TEXT DEFAULT '{}',
    approved_by    INTEGER REFERENCES users(id),
    approved_at    TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    UNIQUE (bank_id, code)
);
CREATE INDEX IF NOT EXISTS ix_questions_bank ON questions(bank_id, status);

CREATE TABLE IF NOT EXISTS vivas (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id    INTEGER NOT NULL REFERENCES users(id),
    bank_id     INTEGER NOT NULL REFERENCES question_banks(id),
    title       TEXT NOT NULL,
    subject     TEXT NOT NULL,
    topics      TEXT DEFAULT '[]',
    difficulty  TEXT NOT NULL DEFAULT 'mixed',        -- easy | medium | hard | mixed
    duration_s  INTEGER NOT NULL DEFAULT 900,
    access_code TEXT NOT NULL UNIQUE,
    status      TEXT NOT NULL DEFAULT 'draft',        -- draft | open | closed
    settings    TEXT DEFAULT '{}',                    -- speech engines, language mode, integrity, code round
    phase_plan  TEXT DEFAULT '{}',                    -- customised phase timings + written justification
    rubric      TEXT DEFAULT '{}',
    roster      TEXT DEFAULT '[]',                    -- allowed roll numbers (empty = anyone with the code)
    opens_at    TEXT,
    closes_at   TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

-- One attempt by one student. State is persisted after every turn, so any worker can resume it.
CREATE TABLE IF NOT EXISTS sessions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    viva_id       INTEGER NOT NULL REFERENCES vivas(id),
    student_id    INTEGER NOT NULL REFERENCES users(id),
    status        TEXT NOT NULL DEFAULT 'created',    -- created | consented | live | ended | scoring | scored | reviewed | abandoned
    consent       TEXT DEFAULT '{}',
    plan          TEXT DEFAULT '{}',                  -- the questions chosen for this student (seeded, traceable)
    state         TEXT DEFAULT '{}',                  -- examiner state machine
    bank_version  INTEGER,
    engine_version TEXT,
    settings      TEXT DEFAULT '{}',                  -- engines actually used
    started_at    TEXT,
    ended_at      TEXT,
    end_reason    TEXT,
    started_epoch REAL,                               -- wall clock (seconds) when the examiner first spoke
    paused_s      REAL NOT NULL DEFAULT 0,            -- clock paused during network drops (bounded by the grace period)
    conn_token    TEXT,                               -- the connection that owns the live session
    last_seen_epoch REAL,
    audio_samples INTEGER NOT NULL DEFAULT 0,         -- student audio written so far (16 kHz samples)
    flags         TEXT DEFAULT '{}',
    latency       TEXT DEFAULT '{}',
    client        TEXT DEFAULT '{}',
    final_total   REAL,
    needs_review  INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_sessions_viva ON sessions(viva_id, status);
CREATE INDEX IF NOT EXISTS ix_sessions_student ON sessions(student_id);

-- Timestamped transcript: every examiner utterance and student answer, with where it came from.
CREATE TABLE IF NOT EXISTS turns (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    seq         INTEGER NOT NULL,
    speaker     TEXT NOT NULL,              -- examiner | student | system
    kind        TEXT NOT NULL,              -- question | followup | hint | rephrase | repeat | redirect | ack | prompt | close | answer | no_answer
    phase       TEXT,
    text        TEXT NOT NULL,
    source      TEXT,                       -- bank:DBMS-F-03, bank:DBMS-F-03/f1, variant:DBMS-F-03/f1, system:redirect ...
    question_code TEXT,
    t_start     REAL NOT NULL,              -- seconds from the start of the viva (matches the recording)
    t_end       REAL,
    meta        TEXT DEFAULT '{}',          -- assessment, STT confidence, latency breakdown, decision reason
    created_at  TEXT NOT NULL,
    UNIQUE (session_id, seq)
);
CREATE INDEX IF NOT EXISTS ix_turns_session ON turns(session_id, seq);

CREATE TABLE IF NOT EXISTS scores (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id     INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    kind           TEXT NOT NULL,           -- ai | rule | override
    dims           TEXT NOT NULL,           -- {dimension: {score, reason, evidence:[...], spread}}
    total          REAL NOT NULL,           -- /100
    feedback       TEXT DEFAULT '[]',       -- [{text, evidence:[...]}]
    prompt_version TEXT,
    model          TEXT,
    runs           TEXT DEFAULT '[]',
    confidence     TEXT,                    -- high | medium | low
    needs_review   INTEGER NOT NULL DEFAULT 0,
    review_reasons TEXT DEFAULT '[]',
    reviewer_id    INTEGER REFERENCES users(id),
    reviewer_name  TEXT,
    reason         TEXT,
    is_final       INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_scores_session ON scores(session_id, kind);

-- Calibration: faculty marks entered blind (before seeing the AI score).
CREATE TABLE IF NOT EXISTS faculty_marks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    faculty_id  INTEGER NOT NULL REFERENCES users(id),
    dims        TEXT NOT NULL,
    total       REAL NOT NULL,
    notes       TEXT DEFAULT '',
    blind       INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL,
    UNIQUE (session_id, faculty_id)
);

CREATE TABLE IF NOT EXISTS integrity_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    t           REAL NOT NULL,
    kind        TEXT NOT NULL,              -- tab_hidden | focus_lost | paste | long_silence | reading_suspected | second_voice | mic_muted | network_drop | rejoin | identity_mismatch | injection
    severity    TEXT NOT NULL DEFAULT 'info',   -- info | warn
    detail      TEXT DEFAULT '{}',
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_integrity_session ON integrity_events(session_id);

-- Speech-recognition test clips recorded by consenting volunteers (reference text is the prompt they read).
CREATE TABLE IF NOT EXISTS stt_clips (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    speaker     TEXT NOT NULL,
    accent      TEXT DEFAULT '',
    language    TEXT NOT NULL DEFAULT 'en-IN',  -- en-IN | hi-en
    reference   TEXT NOT NULL,
    audio_path  TEXT NOT NULL,
    duration_s  REAL,
    consent     INTEGER NOT NULL DEFAULT 1,
    created_by  INTEGER REFERENCES users(id),
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tts_cache (
    key         TEXT PRIMARY KEY,
    engine      TEXT NOT NULL,
    voice       TEXT,
    text        TEXT NOT NULL,
    path        TEXT NOT NULL,
    mime        TEXT NOT NULL,
    duration_s  REAL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,
    priority    INTEGER NOT NULL DEFAULT 0,
    user_id     INTEGER,
    payload     TEXT DEFAULT '{}',
    status      TEXT NOT NULL DEFAULT 'queued',  -- queued | running | done | failed
    dedupe_key  TEXT,
    attempts    INTEGER NOT NULL DEFAULT 0,
    progress    INTEGER NOT NULL DEFAULT 0,
    message     TEXT,
    result      TEXT,
    error       TEXT,
    worker      TEXT,
    run_after   TEXT,
    started_at  TEXT,
    finished_at TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_tasks_claim ON tasks(status, priority, id);
CREATE INDEX IF NOT EXISTS ix_tasks_dedupe ON tasks(dedupe_key, status);

CREATE TABLE IF NOT EXISTS schedules (
    name        TEXT PRIMARY KEY,
    next_run_at TEXT NOT NULL,
    last_run_at TEXT,
    locked_by   TEXT
);

-- Every AI and speech call with tokens / audio seconds and cost, for the cost sheet.
CREATE TABLE IF NOT EXISTS usage_ledger (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id     INTEGER,
    feature        TEXT NOT NULL,      -- examiner | scorer | bank_generation | stt | tts
    provider       TEXT NOT NULL,
    model          TEXT NOT NULL,
    input_tokens   INTEGER NOT NULL DEFAULT 0,
    output_tokens  INTEGER NOT NULL DEFAULT 0,
    cached_tokens  INTEGER NOT NULL DEFAULT 0,
    audio_seconds  REAL NOT NULL DEFAULT 0,
    characters     INTEGER NOT NULL DEFAULT 0,
    cost_usd       REAL NOT NULL DEFAULT 0,
    latency_ms     INTEGER,
    estimated      INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_usage_session ON usage_ledger(session_id);

CREATE TABLE IF NOT EXISTS llm_cache (
    key        TEXT PRIMARY KEY,
    response   TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Who did what: overrides, approvals, audio playback, exports, deletions.
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id    INTEGER,
    actor_name  TEXT,
    action      TEXT NOT NULL,
    entity      TEXT,
    entity_id   TEXT,
    before      TEXT,
    after       TEXT,
    data        TEXT DEFAULT '{}',
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_audit_entity ON audit_log(entity, entity_id);
