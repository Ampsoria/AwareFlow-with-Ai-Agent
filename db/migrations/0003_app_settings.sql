BEGIN IMMEDIATE;
CREATE TABLE app_settings (key TEXT PRIMARY KEY NOT NULL, value_json TEXT NOT NULL);
CREATE UNIQUE INDEX one_open_ticket ON submission_tickets(assignment_id) WHERE consumed_attempt_id IS NULL;
INSERT INTO schema_migrations(version, applied_at) VALUES (3, strftime('%Y-%m-%dT%H:%M:%fZ','now'));
COMMIT;
