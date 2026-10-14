"""Exercise the initial schema in memory; does not test the application workflow."""

from pathlib import Path
import sqlite3
import unittest


ROOT = Path(__file__).resolve().parents[1]
NOW = "2026-10-01T03:00:00.000Z"
DUE = "2026-10-31T16:59:59.000Z"
HASH = "a" * 64


class SchemaChecks(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.executescript((ROOT / "db/migrations/0001_initial.sql").read_text())
        self.db.executemany(
            "INSERT INTO users(id, display_name, role, created_at) VALUES (?, ?, ?, ?)",
            [("ADMIN", "Demo Admin", "admin", NOW),
             ("TEST001", "Demo User", "learner", NOW)],
        )
        self.make_round("r1", "v1")
        self.make_round("r2", "v2")

    def make_round(self, round_id, version_id):
        self.db.execute(
            """INSERT INTO training_rounds(
                id, creation_key, title, settings_json, question_count,
                pass_percent, allow_retakes, due_at, created_by, created_at
            ) VALUES (?, ?, 'Demo', '{}', 5, 80, 1, ?, 'ADMIN', ?)""",
            (round_id, "create-" + round_id, DUE, NOW),
        )
        # Empty JSON isolates SQL constraints. Real app must validate full bundles.
        self.db.execute(
            """INSERT INTO training_versions(
                id, round_id, version_number, bundle_json, sources_json,
                settings_snapshot_json, validation_json, pdf_path, pdf_sha256, created_at
            ) VALUES (?, ?, 1, '{}', '[]', '{}', '{}', 'demo.pdf', ?, ?)""",
            (version_id, round_id, HASH, NOW),
        )
        self.db.execute(
            "UPDATE training_rounds SET current_version_id=?, status='IN_REVIEW' WHERE id=?",
            (version_id, round_id),
        )

    def publish(self, round_id="r1", version_id="v1"):
        self.db.execute(
            """INSERT INTO review_events(
                id, command_key, round_id, version_id, action, actor_id, created_at
            ) VALUES (?, ?, ?, ?, 'APPROVE', 'ADMIN', ?)""",
            ("approve-" + round_id, "approval-key-" + round_id, round_id, version_id, NOW),
        )
        self.db.execute("UPDATE training_rounds SET status='APPROVED' WHERE id=?", (round_id,))
        self.db.execute(
            """UPDATE training_rounds SET status='PUBLISHED',
                published_version_id=?, published_at=? WHERE id=?""",
            (version_id, NOW, round_id),
        )

    def assign(self, assignment_id="a1", round_id="r1", version_id="v1"):
        self.db.execute(
            """INSERT INTO assignments(
                id, round_id, user_id, version_id, display_name_snapshot, assigned_at
            ) VALUES (?, ?, 'TEST001', ?, 'Demo User', ?)""",
            (assignment_id, round_id, version_id, NOW),
        )

    def attempt(self, attempt_id="t1", version_id="v1", key="submit-1", number=1):
        self.db.execute(
            """INSERT INTO attempts(
                id, assignment_id, version_id, submission_key, attempt_number,
                answers_json, answers_sha256, correct_count, question_count, submitted_at
            ) VALUES (?, 'a1', ?, ?, ?, '{}', ?, 4, 5, ?)""",
            (attempt_id, version_id, key, number, HASH, NOW),
        )

    def test_schema_integrity_and_version(self):
        self.assertEqual(self.db.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(self.db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(self.db.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(self.db.execute("SELECT version FROM schema_migrations").fetchall(), [(1,)])

    def test_round_cannot_skip_review(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                """UPDATE training_rounds SET status='PUBLISHED',
                    published_version_id='v1', published_at=? WHERE id='r1'""", (NOW,)
            )

    def test_approval_is_required(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE training_rounds SET status='APPROVED' WHERE id='r1'")

    def test_versions_cannot_cross_rounds(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE training_rounds SET current_version_id='v2' WHERE id='r1'")

    def test_revision_returns_to_review_and_needs_fresh_approval(self):
        self.db.execute(
            """INSERT INTO review_events(
                id, command_key, round_id, version_id, action, actor_id, created_at
            ) VALUES ('old-approval', 'old-key', 'r1', 'v1', 'APPROVE', 'ADMIN', ?)""", (NOW,)
        )
        self.db.execute("UPDATE training_rounds SET status='CHANGES_REQUESTED' WHERE id='r1'")
        self.db.execute(
            """INSERT INTO training_versions(
                id, round_id, version_number, parent_version_id, bundle_json, sources_json,
                settings_snapshot_json, validation_json, pdf_path, pdf_sha256, created_at
            ) VALUES ('v1-revised', 'r1', 2, 'v1', '{}', '[]', '{}', '{}', 'revised.pdf', ?, ?)""",
            (HASH, NOW),
        )
        self.db.execute(
            """UPDATE training_rounds SET status='IN_REVIEW',
                current_version_id='v1-revised' WHERE id='r1'"""
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE training_rounds SET status='APPROVED' WHERE id='r1'")
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM training_versions WHERE round_id='r1'").fetchone()[0], 2
        )

    def test_assignment_requires_publication(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.assign()

    def test_published_policy_and_version_are_frozen(self):
        self.publish()
        for sql in (
            "UPDATE training_rounds SET pass_percent=60 WHERE id='r1'",
            "UPDATE training_rounds SET due_at='2027-01-01' WHERE id='r1'",
            "UPDATE training_versions SET bundle_json='{}' WHERE id='v1'",
            "DELETE FROM training_versions WHERE id='v1'",
        ):
            with self.subTest(sql=sql), self.assertRaises(sqlite3.IntegrityError):
                self.db.execute(sql)

    def test_assignment_duplicate_and_mutation_rejected(self):
        self.publish()
        self.assign()
        with self.assertRaises(sqlite3.IntegrityError):
            self.assign(assignment_id="a2")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE assignments SET email_snapshot='changed@example.invalid'")
        self.db.execute("UPDATE assignments SET token_generation=2 WHERE id='a1'")
        self.assertEqual(self.db.execute("SELECT token_generation FROM assignments").fetchone()[0], 2)

    def test_attempt_version_binding(self):
        self.publish()
        self.assign()
        with self.assertRaises(sqlite3.IntegrityError):
            self.attempt(version_id="v2")

    def test_duplicate_submission_and_history_mutation_rejected(self):
        self.publish()
        self.assign()
        self.attempt()
        with self.assertRaises(sqlite3.IntegrityError):
            self.attempt(attempt_id="t2", number=2)
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE attempts SET correct_count=5 WHERE id='t1'")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("DELETE FROM attempts WHERE id='t1'")

    def test_retake_preserves_both_attempts(self):
        self.publish()
        self.assign()
        self.attempt()
        self.attempt(attempt_id="t2", key="submit-2", number=2)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 2)

    def test_retake_disabled(self):
        self.db.execute("UPDATE training_rounds SET allow_retakes=0 WHERE id='r1'")
        self.publish()
        self.assign()
        self.attempt()
        with self.assertRaises(sqlite3.IntegrityError):
            self.attempt(attempt_id="t2", key="submit-2", number=2)

    def test_closed_round_rejects_submission(self):
        self.publish()
        self.assign()
        self.db.execute("UPDATE training_rounds SET status='CLOSED', closed_at=? WHERE id='r1'", (NOW,))
        with self.assertRaises(sqlite3.IntegrityError):
            self.attempt()

    def test_email_deduplication_and_preview_status(self):
        self.publish()
        self.assign()
        sql = """INSERT INTO email_outbox(
            id, assignment_id, transport, status, message_id, payload_json, available_at, created_at
        ) VALUES (?, 'a1', 'preview', ?, ?, '{}', ?, ?)"""
        self.db.execute(sql, ("e1", "PREVIEW_READY", "m1", NOW, NOW))
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(sql, ("e2", "PREVIEW_READY", "m2", NOW, NOW))
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE email_outbox SET status='ACCEPTED', accepted_at=?", (NOW,))

    def test_schedule_period_unique(self):
        self.db.execute(
            """INSERT INTO schedules(id, kind, day_of_month, month_of_year,
                local_hour, local_minute, settings_json)
                VALUES ('s1', 'REPORT_YEARLY', 1, 1, 9, 0, '{}')"""
        )
        self.db.execute(
            """INSERT INTO jobs(id, idempotency_key, kind, params_json, status, available_at, created_at)
                VALUES ('j1', 'report-2026', 'REPORT', '{}', 'QUEUED', ?, ?)""", (NOW, NOW)
        )
        sql = """INSERT INTO schedule_runs(id, schedule_id, period_key, job_id, created_at)
            VALUES (?, 's1', '2026', 'j1', ?)"""
        self.db.execute(sql, ("sr1", NOW))
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(sql, ("sr2", NOW))


if __name__ == "__main__":
    unittest.main(verbosity=2)
