BEGIN IMMEDIATE;
CREATE TABLE command_receipts (
    command_key TEXT PRIMARY KEY NOT NULL,
    actor_id TEXT NOT NULL REFERENCES users(id),
    operation TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE submission_tickets (
    key TEXT PRIMARY KEY NOT NULL,
    assignment_id TEXT NOT NULL REFERENCES assignments(id),
    created_at TEXT NOT NULL,
    consumed_attempt_id TEXT UNIQUE REFERENCES attempts(id)
);
CREATE UNIQUE INDEX one_active_generation ON jobs(round_id, expected_lock_version)
    WHERE kind IN ('GENERATE', 'REVISE') AND status IN ('QUEUED', 'RUNNING');
CREATE TRIGGER approved_version_cannot_swap BEFORE UPDATE ON training_rounds
WHEN OLD.status = 'APPROVED' AND NEW.status <> 'DRAFT'
 AND NEW.current_version_id IS NOT OLD.current_version_id
BEGIN SELECT RAISE(ABORT, 'reset to draft before changing approved version'); END;
INSERT INTO schema_migrations(version, applied_at)
VALUES (2, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
COMMIT;
