BEGIN IMMEDIATE;
CREATE TABLE agent_steps (
    job_id TEXT NOT NULL REFERENCES jobs(id),
    attempt INTEGER NOT NULL CHECK(attempt > 0),
    step INTEGER NOT NULL CHECK(step > 0),
    tool TEXT NOT NULL,
    arguments_json TEXT NOT NULL CHECK(json_valid(arguments_json)),
    status TEXT NOT NULL CHECK(status IN ('STARTED','SUCCEEDED','REJECTED','FAILED')),
    result_json TEXT CHECK(result_json IS NULL OR json_valid(result_json)),
    created_at TEXT NOT NULL,
    completed_at TEXT,
    PRIMARY KEY(job_id, attempt, step)
);
INSERT INTO schema_migrations(version, applied_at) VALUES (4, strftime('%Y-%m-%dT%H:%M:%fZ','now'));
COMMIT;
