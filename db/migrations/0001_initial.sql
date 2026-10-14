-- Initial SQLite schema. Apply once through a versioned migration runner.
-- Every application connection must also set PRAGMA foreign_keys = ON.
PRAGMA foreign_keys = ON;

BEGIN;

CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE users (
    id TEXT PRIMARY KEY NOT NULL,
    display_name TEXT NOT NULL,
    email TEXT,
    role TEXT NOT NULL CHECK (role IN ('admin', 'reviewer', 'learner')),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL
);

CREATE TABLE user_groups (
    id TEXT PRIMARY KEY NOT NULL,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE group_memberships (
    group_id TEXT NOT NULL REFERENCES user_groups(id),
    user_id TEXT NOT NULL REFERENCES users(id),
    PRIMARY KEY (group_id, user_id)
);

CREATE TABLE round_templates (
    id TEXT PRIMARY KEY NOT NULL,
    name TEXT NOT NULL,
    settings_json TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    updated_at TEXT NOT NULL
);

CREATE TABLE training_rounds (
    id TEXT PRIMARY KEY NOT NULL,
    creation_key TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN ('DRAFT', 'IN_REVIEW', 'CHANGES_REQUESTED',
                          'APPROVED', 'PUBLISHED', 'CLOSED')),
    settings_json TEXT NOT NULL,
    question_count INTEGER NOT NULL CHECK (question_count BETWEEN 3 AND 5),
    pass_percent INTEGER NOT NULL CHECK (pass_percent BETWEEN 1 AND 100),
    allow_retakes INTEGER NOT NULL CHECK (allow_retakes IN (0, 1)),
    due_at TEXT NOT NULL,
    current_version_id TEXT,
    published_version_id TEXT,
    lock_version INTEGER NOT NULL DEFAULT 0 CHECK (lock_version >= 0),
    created_by TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    published_at TEXT,
    closed_at TEXT,
    CHECK (status = 'DRAFT' OR current_version_id IS NOT NULL),
    CHECK ((status IN ('PUBLISHED', 'CLOSED') AND published_version_id IS NOT NULL
            AND published_at IS NOT NULL AND current_version_id = published_version_id)
        OR (status NOT IN ('PUBLISHED', 'CLOSED') AND published_version_id IS NULL
            AND published_at IS NULL)),
    CHECK ((status = 'CLOSED' AND closed_at IS NOT NULL)
        OR (status <> 'CLOSED' AND closed_at IS NULL)),
    FOREIGN KEY (current_version_id, id) REFERENCES training_versions(id, round_id),
    FOREIGN KEY (published_version_id, id) REFERENCES training_versions(id, round_id)
);

CREATE TABLE training_versions (
    id TEXT PRIMARY KEY NOT NULL,
    round_id TEXT NOT NULL REFERENCES training_rounds(id),
    version_number INTEGER NOT NULL CHECK (version_number > 0),
    parent_version_id TEXT,
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version > 0),
    bundle_json TEXT NOT NULL,
    sources_json TEXT NOT NULL,
    settings_snapshot_json TEXT NOT NULL,
    validation_json TEXT NOT NULL,
    pdf_path TEXT NOT NULL,
    pdf_sha256 TEXT NOT NULL CHECK (length(pdf_sha256) = 64),
    change_summary TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (round_id, version_number),
    UNIQUE (id, round_id),
    FOREIGN KEY (parent_version_id, round_id) REFERENCES training_versions(id, round_id)
);

CREATE TABLE review_events (
    id TEXT PRIMARY KEY NOT NULL,
    command_key TEXT NOT NULL UNIQUE,
    round_id TEXT NOT NULL REFERENCES training_rounds(id),
    version_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('REQUEST_CHANGES', 'APPROVE', 'WITHDRAW')),
    feedback TEXT,
    actor_id TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    CHECK (action <> 'REQUEST_CHANGES' OR length(trim(feedback)) > 0 AND feedback IS NOT NULL),
    FOREIGN KEY (version_id, round_id) REFERENCES training_versions(id, round_id)
);

CREATE TABLE assignments (
    id TEXT PRIMARY KEY NOT NULL,
    round_id TEXT NOT NULL REFERENCES training_rounds(id),
    user_id TEXT NOT NULL REFERENCES users(id),
    version_id TEXT NOT NULL,
    display_name_snapshot TEXT NOT NULL,
    email_snapshot TEXT,
    token_generation INTEGER NOT NULL DEFAULT 1 CHECK (token_generation > 0),
    assigned_at TEXT NOT NULL,
    UNIQUE (round_id, user_id),
    UNIQUE (id, version_id),
    FOREIGN KEY (version_id, round_id) REFERENCES training_versions(id, round_id)
);

CREATE TABLE attempts (
    id TEXT PRIMARY KEY NOT NULL,
    assignment_id TEXT NOT NULL,
    version_id TEXT NOT NULL,
    submission_key TEXT NOT NULL UNIQUE,
    attempt_number INTEGER NOT NULL CHECK (attempt_number > 0),
    answers_json TEXT NOT NULL,
    answers_sha256 TEXT NOT NULL CHECK (length(answers_sha256) = 64),
    correct_count INTEGER NOT NULL CHECK (correct_count >= 0),
    question_count INTEGER NOT NULL CHECK (question_count BETWEEN 3 AND 5),
    submitted_at TEXT NOT NULL,
    CHECK (correct_count <= question_count),
    UNIQUE (assignment_id, attempt_number),
    FOREIGN KEY (assignment_id, version_id) REFERENCES assignments(id, version_id)
);

CREATE TABLE email_outbox (
    id TEXT PRIMARY KEY NOT NULL,
    assignment_id TEXT NOT NULL REFERENCES assignments(id),
    message_kind TEXT NOT NULL DEFAULT 'INVITATION' CHECK (message_kind = 'INVITATION'),
    transport TEXT NOT NULL CHECK (transport IN ('preview', 'smtp')),
    status TEXT NOT NULL CHECK (status IN ('PENDING', 'PREVIEW_READY', 'SENDING',
                                          'ACCEPTED', 'FAILED', 'UNKNOWN', 'CANCELLED')),
    recipient TEXT,
    message_id TEXT NOT NULL UNIQUE,
    payload_json TEXT NOT NULL,
    eml_path TEXT,
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    lease_token TEXT,
    lease_expires_at TEXT,
    available_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    accepted_at TEXT,
    last_error_code TEXT,
    last_error_safe TEXT,
    CHECK (transport <> 'preview' OR status IN ('PENDING', 'PREVIEW_READY', 'FAILED', 'CANCELLED')),
    CHECK (transport <> 'smtp' OR status <> 'PREVIEW_READY'),
    CHECK ((status = 'ACCEPTED' AND accepted_at IS NOT NULL)
        OR (status <> 'ACCEPTED' AND accepted_at IS NULL)),
    UNIQUE (assignment_id, message_kind, transport)
);

CREATE TABLE jobs (
    id TEXT PRIMARY KEY NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL CHECK (kind IN ('GENERATE', 'REVISE', 'REPORT')),
    round_id TEXT REFERENCES training_rounds(id),
    expected_lock_version INTEGER,
    params_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('QUEUED', 'RUNNING', 'SUCCEEDED',
                                          'FAILED', 'NEEDS_INPUT', 'CANCELLED')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    max_attempts INTEGER NOT NULL DEFAULT 3 CHECK (max_attempts > 0),
    lease_token TEXT,
    lease_expires_at TEXT,
    available_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    last_error_code TEXT,
    last_error_safe TEXT
);

CREATE TABLE schedules (
    id TEXT PRIMARY KEY NOT NULL,
    template_id TEXT REFERENCES round_templates(id),
    kind TEXT NOT NULL CHECK (kind IN ('DRAFT_MONTHLY', 'DRAFT_YEARLY', 'REPORT_YEARLY')),
    timezone TEXT NOT NULL DEFAULT 'Asia/Bangkok' CHECK (timezone = 'Asia/Bangkok'),
    day_of_month INTEGER NOT NULL CHECK (day_of_month BETWEEN 1 AND 28),
    month_of_year INTEGER CHECK (month_of_year BETWEEN 1 AND 12),
    local_hour INTEGER NOT NULL CHECK (local_hour BETWEEN 0 AND 23),
    local_minute INTEGER NOT NULL CHECK (local_minute BETWEEN 0 AND 59),
    settings_json TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
    CHECK (kind = 'REPORT_YEARLY' OR template_id IS NOT NULL),
    CHECK ((kind = 'DRAFT_MONTHLY' AND month_of_year IS NULL)
        OR (kind <> 'DRAFT_MONTHLY' AND month_of_year IS NOT NULL))
);

CREATE TABLE schedule_runs (
    id TEXT PRIMARY KEY NOT NULL,
    schedule_id TEXT NOT NULL REFERENCES schedules(id),
    period_key TEXT NOT NULL,
    round_id TEXT REFERENCES training_rounds(id),
    job_id TEXT NOT NULL REFERENCES jobs(id),
    created_at TEXT NOT NULL,
    UNIQUE (schedule_id, period_key)
);

CREATE TABLE report_artifacts (
    id TEXT PRIMARY KEY NOT NULL,
    job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id),
    round_id TEXT REFERENCES training_rounds(id),
    report_year INTEGER,
    as_of TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    narrative TEXT,
    narrative_source TEXT NOT NULL CHECK (narrative_source IN ('template', 'mock', 'live')),
    pdf_path TEXT,
    pdf_sha256 TEXT,
    created_at TEXT NOT NULL,
    CHECK ((round_id IS NOT NULL AND report_year IS NULL)
        OR (round_id IS NULL AND report_year IS NOT NULL))
);

CREATE TABLE audit_events (
    id TEXT PRIMARY KEY NOT NULL,
    actor_id TEXT REFERENCES users(id),
    action TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    detail_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_attempts_latest ON attempts(assignment_id, attempt_number DESC);
CREATE INDEX idx_rounds_published ON training_rounds(published_at);
CREATE INDEX idx_review_version ON review_events(round_id, version_id, action);
CREATE INDEX idx_jobs_ready ON jobs(status, available_at);
CREATE INDEX idx_outbox_ready ON email_outbox(status, available_at);

CREATE TRIGGER rounds_start_as_draft BEFORE INSERT ON training_rounds
WHEN NEW.status <> 'DRAFT'
BEGIN SELECT RAISE(ABORT, 'round must start as draft'); END;

CREATE TRIGGER round_state_transition BEFORE UPDATE OF status ON training_rounds
WHEN OLD.status <> NEW.status AND NOT (
       (OLD.status = 'DRAFT' AND NEW.status = 'IN_REVIEW')
    OR (OLD.status = 'IN_REVIEW' AND NEW.status IN ('DRAFT', 'CHANGES_REQUESTED', 'APPROVED'))
    OR (OLD.status = 'CHANGES_REQUESTED' AND NEW.status IN ('DRAFT', 'IN_REVIEW'))
    OR (OLD.status = 'APPROVED' AND NEW.status IN ('DRAFT', 'PUBLISHED'))
    OR (OLD.status = 'PUBLISHED' AND NEW.status = 'CLOSED')
)
BEGIN SELECT RAISE(ABORT, 'invalid round state transition'); END;

CREATE TRIGGER approval_required BEFORE UPDATE OF status ON training_rounds
WHEN NEW.status IN ('APPROVED', 'PUBLISHED') AND NOT EXISTS (
    SELECT 1 FROM review_events r
    WHERE r.round_id = NEW.id AND r.version_id = NEW.current_version_id AND r.action = 'APPROVE'
)
BEGIN SELECT RAISE(ABORT, 'current version needs approval'); END;

CREATE TRIGGER published_round_frozen BEFORE UPDATE ON training_rounds
WHEN OLD.status IN ('PUBLISHED', 'CLOSED') AND (
       NEW.title IS NOT OLD.title
    OR NEW.settings_json IS NOT OLD.settings_json
    OR NEW.question_count IS NOT OLD.question_count
    OR NEW.pass_percent IS NOT OLD.pass_percent
    OR NEW.allow_retakes IS NOT OLD.allow_retakes
    OR NEW.due_at IS NOT OLD.due_at
    OR NEW.current_version_id IS NOT OLD.current_version_id
    OR NEW.published_version_id IS NOT OLD.published_version_id
    OR NEW.published_at IS NOT OLD.published_at
)
BEGIN SELECT RAISE(ABORT, 'published round is frozen'); END;

CREATE TRIGGER version_no_update BEFORE UPDATE ON training_versions
BEGIN SELECT RAISE(ABORT, 'version is immutable'); END;
CREATE TRIGGER version_no_delete BEFORE DELETE ON training_versions
BEGIN SELECT RAISE(ABORT, 'version is immutable'); END;
CREATE TRIGGER review_no_update BEFORE UPDATE ON review_events
BEGIN SELECT RAISE(ABORT, 'review history is immutable'); END;
CREATE TRIGGER review_no_delete BEFORE DELETE ON review_events
BEGIN SELECT RAISE(ABORT, 'review history is immutable'); END;
CREATE TRIGGER attempt_no_update BEFORE UPDATE ON attempts
BEGIN SELECT RAISE(ABORT, 'attempt is immutable'); END;
CREATE TRIGGER attempt_no_delete BEFORE DELETE ON attempts
BEGIN SELECT RAISE(ABORT, 'attempt is immutable'); END;
CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit_events
BEGIN SELECT RAISE(ABORT, 'audit history is immutable'); END;
CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit_events
BEGIN SELECT RAISE(ABORT, 'audit history is immutable'); END;

CREATE TRIGGER assignment_published_version BEFORE INSERT ON assignments
WHEN NOT EXISTS (
    SELECT 1 FROM training_rounds r
    WHERE r.id = NEW.round_id AND r.status = 'PUBLISHED' AND r.published_version_id = NEW.version_id
)
BEGIN SELECT RAISE(ABORT, 'assignment must use published version'); END;

CREATE TRIGGER assignment_frozen BEFORE UPDATE ON assignments
WHEN NEW.id IS NOT OLD.id OR NEW.round_id IS NOT OLD.round_id
    OR NEW.user_id IS NOT OLD.user_id OR NEW.version_id IS NOT OLD.version_id
    OR NEW.display_name_snapshot IS NOT OLD.display_name_snapshot
    OR NEW.email_snapshot IS NOT OLD.email_snapshot OR NEW.assigned_at IS NOT OLD.assigned_at
BEGIN SELECT RAISE(ABORT, 'assignment snapshot is frozen'); END;
CREATE TRIGGER assignment_no_delete BEFORE DELETE ON assignments
BEGIN SELECT RAISE(ABORT, 'assignment snapshot is frozen'); END;

CREATE TRIGGER attempt_round_open BEFORE INSERT ON attempts
WHEN NOT EXISTS (
    SELECT 1 FROM assignments a JOIN training_rounds r ON r.id = a.round_id
    WHERE a.id = NEW.assignment_id AND r.status = 'PUBLISHED'
      AND r.question_count = NEW.question_count
)
BEGIN SELECT RAISE(ABORT, 'round must be open and question count must match'); END;

CREATE TRIGGER attempt_retake_guard BEFORE INSERT ON attempts
WHEN EXISTS (
    SELECT 1 FROM assignments a JOIN training_rounds r ON r.id = a.round_id
    WHERE a.id = NEW.assignment_id AND r.allow_retakes = 0
) AND EXISTS (SELECT 1 FROM attempts WHERE assignment_id = NEW.assignment_id)
BEGIN SELECT RAISE(ABORT, 'retake is disabled'); END;

INSERT INTO schema_migrations(version, applied_at)
VALUES (1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));

COMMIT;
