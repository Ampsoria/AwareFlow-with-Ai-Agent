from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from email import policy
from email.parser import BytesParser
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from pydantic import ValidationError as SchemaError

from awareflow.bootstrap import build, FixedClock
from awareflow.config import Config
from awareflow.demo import START, seed_users, settings, run_demo
from awareflow.domain.errors import Conflict, Forbidden, InvalidTransition, ValidationError, ProviderConfigurationError
from awareflow.domain.models import ContentBundle
from awareflow.infrastructure.mock import MockLLM


class FastPdf:
    """Only for transaction tests. Real PDF rendering is tested separately below."""
    def lesson(self, bundle, sources):
        return b"%PDF-transaction-test\n" + bundle.model_dump_json().encode()

    def report(self, metrics):
        return b"%PDF-report-test\n" + json.dumps(metrics).encode()


class CoreTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.clock = FixedClock(START)
        self.config = Config(root, root / "test.sqlite3", signing_secret="x" * 48)
        self.s = build(self.config, self.clock, pdf=FastPdf())
        seed_users(self.s, START)
        self.admin = self.s.auth.select_user("ADMIN")

    def create(self, key="create", retakes=True):
        return self.s.workflow.create_round(self.admin, settings(retakes=retakes), key)

    def current(self, rid):
        return self.s.workflow.get_round(self.admin, rid)

    def generated(self, key="create", retakes=True):
        rid = self.create(key, retakes)
        r = self.current(rid)
        job = self.s.workflow.request_generation(self.admin, rid, r["lock_version"], key + ":gen")
        result = self.s.worker.run_once()
        self.assertEqual(result["id"], job)
        self.assertEqual(result["status"], "SUCCEEDED")
        return rid

    def published(self, key="create", retakes=True):
        rid = self.generated(key, retakes)
        r = self.current(rid)
        self.s.workflow.approve_version(self.admin, rid, r["current_version_id"], r["lock_version"], key + ":approve")
        r = self.current(rid)
        self.s.workflow.publish_round(self.admin, rid, r["current_version_id"], r["lock_version"], key + ":publish")
        return rid

    def assignment(self, user="TEST001", rid=None):
        actor = self.s.auth.select_user(user)
        assignments = self.s.quizzes.list_assignments(actor)
        return actor, next(a for a in assignments if rid is None or a["round_id"] == rid)

    def submit(self, user, answers="AAAAA", rid=None):
        actor, a = self.assignment(user, rid)
        ticket = self.s.quizzes.begin_submission(actor, a["id"])
        return self.s.quizzes.submit_attempt(actor, a["id"], ticket, {f"q{i+1}": c for i, c in enumerate(answers)})

    def test_migration_and_seed_are_repeatable(self):
        self.s.db.migrate()
        seed_users(self.s, START)
        with self.s.db.read() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM users").fetchone()[0], 6)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0], 4)
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_settings_validation(self):
        data = settings().model_dump()
        for field, value in (("question_count", 6), ("pass_percent", 0), ("due_at", START.replace(tzinfo=None)), ("target_group_ids", [])):
            with self.subTest(field=field), self.assertRaises((SchemaError, ValidationError)):
                settings().__class__.model_validate({**data, field: value})

    def test_create_idempotency_and_payload_conflict(self):
        rid = self.create()
        self.assertEqual(rid, self.create())
        with self.assertRaises(Conflict):
            self.s.workflow.create_round(self.admin, settings(title="Changed"), "create")

    def test_learner_cannot_create_or_read_review(self):
        actor = self.s.auth.select_user("TEST001")
        with self.assertRaises(Forbidden):
            self.s.workflow.create_round(actor, settings(), "bad")
        rid = self.generated()
        with self.assertRaises(Forbidden):
            self.s.workflow.review(actor, rid)

    def test_revision_preserves_old_version_and_requires_approval(self):
        rid = self.generated()
        old = self.current(rid)
        job_id = self.s.workflow.request_revision(self.admin, rid, old["current_version_id"], old["lock_version"], "เพิ่มตัวอย่าง", "revision")
        self.assertEqual(self.current(rid)["status"], "CHANGES_REQUESTED")
        self.assertEqual(self.s.worker.run_once()["status"], "SUCCEEDED")
        new = self.current(rid)
        self.assertEqual(new["status"], "IN_REVIEW")
        self.assertNotEqual(new["current_version_id"], old["current_version_id"])
        with self.assertRaises(Conflict):
            self.s.workflow.approve_version(self.admin, rid, old["current_version_id"], new["lock_version"], "wrong-version")
        with self.assertRaises(InvalidTransition):
            self.s.workflow.publish_round(self.admin, rid, new["current_version_id"], new["lock_version"], "unapproved")
        history = self.s.workflow.review(self.admin, rid, old["current_version_id"])
        self.assertEqual(history["version"]["version_number"], 1)
        self.assertEqual(history["history"][0]["feedback"], "เพิ่มตัวอย่าง")

    def test_publish_atomic_and_replayable(self):
        rid = self.published()
        r = self.current(rid)
        first = self.s.workflow.publish_round(self.admin, rid, r["current_version_id"], -1, "create:publish")
        self.assertEqual(len(first), 5)
        with self.s.db.read() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM assignments").fetchone()[0], 5)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM email_outbox").fetchone()[0], 5)
        with self.assertRaises(InvalidTransition):
            self.s.workflow.update_round(self.admin, rid, r["lock_version"], settings(title="mutate"), "mutate")

    def test_publish_rolls_back_if_any_outbox_insert_fails(self):
        rid = self.generated()
        r = self.current(rid)
        self.s.workflow.approve_version(self.admin, rid, r["current_version_id"], r["lock_version"], "approve")
        r = self.current(rid)
        with self.s.db.transaction() as db:
            db.execute("CREATE TRIGGER injected_failure BEFORE INSERT ON email_outbox BEGIN SELECT RAISE(ABORT,'injected'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.s.workflow.publish_round(self.admin, rid, r["current_version_id"], r["lock_version"], "publish")
        self.assertEqual(self.current(rid)["status"], "APPROVED")
        with self.s.db.read() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM assignments").fetchone()[0], 0)

    def test_pdf_tampering_blocks_approval(self):
        rid = self.generated()
        review = self.s.workflow.review(self.admin, rid)
        (self.config.data_root / review["version"]["pdf_path"]).write_bytes(b"tampered")
        r = self.current(rid)
        with self.assertRaises(ValidationError):
            self.s.workflow.approve_version(self.admin, rid, r["current_version_id"], r["lock_version"], "approve")

    def test_stale_worker_cannot_overwrite_config(self):
        rid = self.create()
        self.s.workflow.request_generation(self.admin, rid, 0, "gen")
        job = self.s.worker.claim()
        r = self.current(rid)
        self.s.workflow.update_round(self.admin, rid, r["lock_version"], settings(title="new"), "update")
        with self.assertRaises(Conflict):
            self.s.worker.process(job)
        self.assertEqual(self.current(rid)["title"], "new")
        self.assertIsNone(self.current(rid)["current_version_id"])

    def test_expired_lease_cannot_commit_after_new_owner(self):
        rid = self.create()
        self.s.workflow.request_generation(self.admin, rid, 0, "gen")
        old_job = self.s.worker.claim()
        self.clock.value += timedelta(seconds=121)
        new_job = self.s.worker.claim()
        with self.assertRaises(Conflict):
            self.s.worker.process(old_job)
        self.s.worker.process(new_job)
        self.assertEqual(self.current(rid)["status"], "IN_REVIEW")

    def test_no_sources_does_not_invent_content(self):
        rid = self.create()
        self.s.workflow.request_generation(self.admin, rid, 0, "gen")
        self.s.worker.search = type("Empty", (), {"search": lambda _, settings: []})()
        result = self.s.worker.run_once()
        self.assertEqual(result["status"], "NEEDS_INPUT")
        self.assertIsNone(self.current(rid)["current_version_id"])

    def test_invalid_ai_output_and_retry_budget(self):
        rid = self.create()
        job_id = self.s.workflow.request_generation(self.admin, rid, 0, "gen")
        class Broken(MockLLM):
            def generate(self, *args):
                raise ValueError("secret-api-key-must-not-leak")
        self.s.worker.llm = Broken(self.clock)
        for index in range(3):
            result = self.s.worker.run_once()
            self.assertEqual(result["status"], "FAILED")
            self.assertNotIn("secret-api", json.dumps(result))
            if index < 2:
                self.s.worker.retry(self.admin, job_id, "retry-" + str(index))
        with self.assertRaises(Conflict):
            self.s.worker.retry(self.admin, job_id, "retry-over-budget")

    def test_invalid_reference_validation(self):
        sources = self.s.worker.search.search(settings())
        data = MockLLM(self.clock).generate(settings(), sources).model_dump()
        data["questions"][0]["supporting_section_ids"] = ["missing"]
        with self.assertRaises(ValidationError):
            ContentBundle.model_validate(data).validate_against(settings(), sources)

    def test_student_projection_and_cross_user_token(self):
        rid = self.published()
        actor, a = self.assignment()
        token = self.s.delivery.issue_link(self.admin, a["id"])
        quiz = self.s.quizzes.get_student_quiz(actor, token)
        published = json.loads(self.s.workflow.review(self.admin, rid)["version"]["bundle_json"])
        self.assertEqual(quiz["lesson"]["title"], published["title"])
        self.assertEqual(quiz["lesson"]["scenario"], published["scenario"])
        self.assertEqual(quiz["lesson"]["sections"], [
            {"heading": section["heading"], "body": section["body"]} for section in published["sections"]])
        self.assertEqual(quiz["lesson"]["employee_actions"], [action["text"] for action in published["employee_actions"]])
        self.assertEqual(set(quiz["lesson"]["sources"][0]), {"title", "publisher", "url"})
        for forbidden_key in ("correct_option_id", "explanation", "supporting_section_ids"):
            self.assertNotIn(forbidden_key, json.dumps(quiz))
        with self.assertRaises(Forbidden):
            self.s.quizzes.get_student_quiz(self.s.auth.select_user("TEST002"), token)
        self.s.delivery.issue_link(self.admin, a["id"], revoke_old=True)
        with self.assertRaises(Forbidden):
            self.s.quizzes.get_student_quiz(actor, token)

    def test_expired_or_tampered_token(self):
        self.published()
        actor, a = self.assignment()
        token = self.s.delivery.issue_link(self.admin, a["id"])
        with self.assertRaises(Forbidden):
            self.s.quizzes.get_student_quiz(actor, token + "bad")
        self.clock.value += timedelta(days=8)
        with self.assertRaises(Forbidden):
            self.s.quizzes.get_student_quiz(actor, token)

    def test_submit_validation_replay_and_changed_payload(self):
        self.published()
        actor, a = self.assignment()
        key = self.s.quizzes.begin_submission(actor, a["id"])
        self.assertEqual(key, self.s.quizzes.begin_submission(actor, a["id"]))
        answers = {f"q{i}": "A" for i in range(1, 6)}
        with self.assertRaises(ValidationError):
            self.s.quizzes.submit_attempt(actor, a["id"], key, {"q1": "A"})
        result = self.s.quizzes.submit_attempt(actor, a["id"], key, answers)
        repeated = self.s.quizzes.submit_attempt(actor, a["id"], key, answers)
        self.assertEqual(result["attempt_id"], repeated["attempt_id"])
        with self.assertRaises(Conflict):
            self.s.quizzes.submit_attempt(actor, a["id"], key, {**answers, "q1": "B"})

    def test_concurrent_double_submit_is_one_attempt(self):
        self.published(retakes=False)
        actor, a = self.assignment()
        key = self.s.quizzes.begin_submission(actor, a["id"])
        answers = {f"q{i}": "A" for i in range(1, 6)}
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.s.quizzes.submit_attempt(actor, a["id"], key, answers), range(2)))
        self.assertEqual(results[0]["attempt_id"], results[1]["attempt_id"])
        with self.assertRaises(Conflict):
            self.s.quizzes.begin_submission(actor, a["id"])

    def test_retakes_preserve_history_and_hide_explanations_until_close(self):
        rid = self.published()
        first = self.submit("TEST001", "AABBB")
        self.assertNotIn("explanations", first)
        latest = self.submit("TEST001", "AAAAB")
        self.assertEqual(latest["attempt_number"], 2)
        self.assertEqual(latest["status"], "PASSED")
        r = self.current(rid)
        self.s.workflow.close_round(self.admin, rid, r["lock_version"], "จบรอบ", "close")
        actor, a = self.assignment()
        self.assertIn("explanations", self.s.quizzes.get_result(actor, a["id"]))

    def test_close_rejects_open_form_and_cancels_pending_email(self):
        rid = self.published()
        actor, a = self.assignment()
        key = self.s.quizzes.begin_submission(actor, a["id"])
        r = self.current(rid)
        self.s.workflow.close_round(self.admin, rid, r["lock_version"], "จบ", "close")
        with self.assertRaises(InvalidTransition):
            self.s.quizzes.submit_attempt(actor, a["id"], key, {f"q{i}": "A" for i in range(1, 6)})
        self.assertTrue(all(o["status"] == "CANCELLED" for o in self.s.delivery.list_outbox(self.admin, rid)))

    def test_metrics_fixture_and_as_of(self):
        rid = self.published()
        self.clock.value += timedelta(days=1)
        self.submit("TEST001", "AAAAA")
        self.submit("TEST004", "AABBB")
        self.submit("TEST005", "AAABA")
        early = self.clock.now()
        self.clock.value = START + timedelta(days=11)
        self.submit("TEST002", "AAABB")
        self.submit("TEST004", "AAAAB")
        m = self.s.reporting.round_metrics(self.admin, rid)
        expected = {"assigned": 5, "completed": 4, "not_started": 1, "passed": 3, "failed": 1,
                    "completion_rate": 80, "pass_rate": 75, "average_score": 80,
                    "first_average_score": 70, "on_time": 3, "late": 1}
        for key, value in expected.items():
            self.assertEqual(m[key], value, key)
        self.assertEqual(m["most_missed"], ["q4", "q5"])
        self.assertIsNone(next(p for p in m["people"] if p["user_id"] == "TEST003")["score"])
        self.assertEqual(self.s.reporting.round_metrics(self.admin, rid, early)["completed"], 3)

    def test_annual_weighting_and_no_data(self):
        rid1 = self.published("first")
        self.submit("TEST001", "AAAAA", rid1)
        self.submit("TEST002", "AAABB", rid1)
        rid2 = self.published("second")
        self.submit("TEST001", "AAAAB", rid2)
        annual = self.s.reporting.annual_metrics(self.admin, 2026)
        self.assertEqual(annual["assigned"], 10)
        self.assertEqual(annual["completed"], 3)
        self.assertEqual(annual["unique_people"], 5)
        self.assertEqual(annual["average_score"], 80)
        self.assertAlmostEqual(annual["pass_rate"], 200 / 3)
        self.assertIsNone(self.s.reporting.annual_metrics(self.admin, 2025)["pass_rate"])

    def test_membership_changes_do_not_change_old_denominator(self):
        rid = self.published()
        with self.s.db.transaction() as db:
            db.execute("DELETE FROM group_memberships WHERE user_id='TEST005'")
        self.assertEqual(self.s.reporting.round_metrics(self.admin, rid)["assigned"], 5)

    def test_preview_has_attachment_and_does_not_send(self):
        rid = self.published()
        self.assertEqual(self.s.delivery.prepare_previews(self.admin, rid), 5)
        self.assertEqual(self.s.delivery.prepare_previews(self.admin, rid), 0)
        for outbox in self.s.delivery.list_outbox(self.admin, rid):
            self.assertEqual(outbox["status"], "PREVIEW_READY")
            message = BytesParser(policy=policy.default).parsebytes(self.s.files.read(outbox["eml_path"]))
            self.assertEqual(len(list(message.iter_attachments())), 1)
            self.assertIn("?token=", message.get_body(preferencelist=("plain",)).get_content())

    def test_csv_formula_protection_and_report_snapshot(self):
        with self.s.db.transaction() as db:
            db.execute("UPDATE users SET display_name='=DANGEROUS()' WHERE id='TEST001'")
        rid = self.published()
        report = self.s.reporting.export(self.admin, "export", round_id=rid)
        csv_bytes = self.s.files.read(report["csv_path"])
        self.assertTrue(csv_bytes.startswith(b"\xef\xbb\xbf"))
        self.assertIn("'=DANGEROUS()", csv_bytes.decode("utf-8-sig"))
        self.submit("TEST001")
        repeated = self.s.reporting.export(self.admin, "export", round_id=rid)
        self.assertEqual(repeated["report_id"], report["report_id"])
        self.assertEqual(repeated["metrics"]["completed"], 0)

    def test_restart_keeps_state_and_submission_ticket(self):
        self.published()
        actor, a = self.assignment()
        key = self.s.quizzes.begin_submission(actor, a["id"])
        restored = build(self.config, self.clock, pdf=FastPdf())
        self.assertEqual(restored.quizzes.begin_submission(actor, a["id"]), key)
        self.assertEqual(len(restored.workflow.list_rounds(self.admin)), 1)

    def test_demo_auth_and_storage_fail_closed(self):
        with self.assertRaises(ProviderConfigurationError):
            build(Config(self.config.data_root, self.config.database_path, mode="real"))
        with self.assertRaises(ValidationError):
            self.s.files.read("../../outside")

    def test_schedule_tick_is_idempotent_and_does_not_approve(self):
        schedule_id = self.s.scheduling.create_schedule(self.admin, "monthly", "รอบประจำเดือน",
                                                        "DRAFT_MONTHLY", settings(), day=1, hour=9)
        first = self.s.scheduling.tick()
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["schedule_id"], schedule_id)
        self.assertEqual(self.s.scheduling.tick(), [])
        self.assertEqual(self.current(first[0]["round_id"])["status"], "DRAFT")
        self.s.worker.run_once()
        self.assertEqual(self.current(first[0]["round_id"])["status"], "IN_REVIEW")
        with self.s.db.read() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM assignments").fetchone()[0], 0)
        self.clock.value = self.clock.value.replace(month=11)
        self.assertEqual(len(self.s.scheduling.tick()), 1)

    def test_yearly_report_schedule_exports_once(self):
        self.published()
        self.submit("TEST001")
        self.s.scheduling.create_schedule(self.admin, "yearly-report", "รายงานปี", "REPORT_YEARLY", month=1)
        self.clock.value = START.replace(year=2027, month=1, day=1)
        first = self.s.scheduling.tick()
        self.assertEqual(len(first), 1)
        self.assertIn("report_id", first[0])
        self.assertEqual(self.s.scheduling.tick(), [])
        with self.s.db.read() as db:
            report = db.execute("SELECT report_year,metrics_json FROM report_artifacts").fetchone()
            self.assertEqual(report["report_year"], 2026)
            self.assertEqual(json.loads(report["metrics_json"])["completed"], 1)

    def test_schedule_respects_bangkok_time_and_current_period_only(self):
        self.s.scheduling.create_schedule(self.admin, "monthly", "เดือน", "DRAFT_MONTHLY", settings(), day=1, hour=11)
        # START is 10:00 Bangkok, not yet 11:00.
        self.assertEqual(self.s.scheduling.tick(), [])
        self.clock.value += timedelta(hours=1)
        self.assertEqual(len(self.s.scheduling.tick()), 1)
        self.clock.value = self.clock.value.replace(month=12)
        # November is not backfilled automatically.
        self.assertEqual(len(self.s.scheduling.tick()), 1)
        with self.s.db.read() as db:
            self.assertEqual([r[0] for r in db.execute("SELECT period_key FROM schedule_runs ORDER BY period_key")], ["2026-10", "2026-12"])


class RealArtifactTests(unittest.TestCase):
    def test_complete_demo_twice_with_real_thai_pdf(self):
        from pypdf import PdfReader
        import io
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = Config(root, root / "demo.sqlite3", signing_secret="y" * 48)
            first = run_demo(config)
            second = run_demo(config)
            self.assertEqual(first["round_id"], second["round_id"])
            self.assertEqual(first["report"]["report_id"], second["report"]["report_id"])
            s = build(config)
            with s.db.read() as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM training_rounds").fetchone()[0], 1)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM training_versions").fetchone()[0], 2)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 5)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM email_outbox").fetchone()[0], 5)
                for row in db.execute("SELECT pdf_path FROM training_versions"):
                    pdf = PdfReader(io.BytesIO(s.files.read(row["pdf_path"])))
                    self.assertEqual(len(pdf.pages), 1)
                    self.assertIn("ข้อมูลสาธิต", pdf.pages[0].extract_text())
            self.assertEqual(first["report"]["metrics"]["average_score"], 80)


if __name__ == "__main__":
    unittest.main()
