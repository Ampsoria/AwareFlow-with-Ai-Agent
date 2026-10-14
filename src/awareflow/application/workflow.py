"""Round lifecycle. Each public mutation is atomic and replayable."""
import json

from awareflow.domain.errors import Conflict, ValidationError
from awareflow.domain.models import ContentBundle, RoundSettings, SourceRecord, utc
from awareflow.infrastructure.database import one, rows
from .common import audit, authorize, dump, guard, receipt, replay, round_row, uid


class Workflow:
    def __init__(self, database, clock, files, links, base_url, max_attempts=3):
        self.db, self.clock, self.files, self.links = database, clock, files, links
        self.base_url, self.max_attempts = base_url.rstrip("/"), max_attempts

    def _groups_exist(self, db, settings):
        for group_id in settings.target_group_ids:
            one(db, "SELECT id FROM user_groups WHERE id=?", (group_id,))

    def _insert_round(self, db, actor, settings, key, now):
        self._groups_exist(db, settings)
        round_id = uid()
        db.execute("""INSERT INTO training_rounds(id, creation_key, title, settings_json,
            question_count, pass_percent, allow_retakes, due_at, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                   (round_id, key, settings.title, settings.model_dump_json(), settings.question_count,
                    settings.pass_percent, int(settings.allow_retakes), utc(settings.due_at), actor.user_id, now))
        audit(db, actor, "CREATE_ROUND", round_id, now)
        return round_id

    def create_round(self, actor, settings: RoundSettings, key):
        request = settings.model_dump(mode="json")
        now = utc(self.clock.now())
        with self.db.transaction() as db:
            authorize(db, actor, {"admin"})
            result = replay(db, actor, "create", key, request)
            if result is not None:
                return result
            round_id = self._insert_round(db, actor, settings, key, now)
            return receipt(db, actor, "create", key, request, round_id, now)

    def update_round(self, actor, round_id, expected_lock, settings: RoundSettings, key):
        request = {"round": round_id, "settings": settings.model_dump(mode="json")}
        now = utc(self.clock.now())
        with self.db.transaction() as db:
            authorize(db, actor, {"admin"})
            prior = replay(db, actor, "update", key, request)
            if prior is not None:
                return prior
            r = round_row(db, round_id)
            guard(r, expected_lock, {"DRAFT", "IN_REVIEW", "CHANGES_REQUESTED", "APPROVED"})
            self._groups_exist(db, settings)
            db.execute("""UPDATE training_rounds SET title=?, settings_json=?, question_count=?,
                pass_percent=?, allow_retakes=?, due_at=?, status='DRAFT', current_version_id=NULL,
                lock_version=lock_version+1 WHERE id=?""",
                       (settings.title, settings.model_dump_json(), settings.question_count,
                        settings.pass_percent, int(settings.allow_retakes), utc(settings.due_at), round_id))
            db.execute("""UPDATE jobs SET status='CANCELLED', completed_at=?
                WHERE round_id=? AND status IN ('QUEUED', 'RUNNING')""", (now, round_id))
            audit(db, actor, "UPDATE_ROUND", round_id, now)
            return receipt(db, actor, "update", key, request, expected_lock + 1, now)

    def request_generation(self, actor, round_id, expected_lock, key):
        return self._enqueue(actor, round_id, expected_lock, key, "GENERATE")

    def request_revision(self, actor, round_id, version_id, expected_lock, feedback, key):
        if not feedback.strip() or len(feedback) > 4000:
            raise ValidationError("กรุณาระบุข้อเสนอแนะ 1-4000 ตัวอักษร")
        return self._enqueue(actor, round_id, expected_lock, key, "REVISE", version_id, feedback)

    def _enqueue(self, actor, round_id, expected_lock, key, kind, version_id=None, feedback=None):
        request = {"round": round_id, "version": version_id, "feedback": feedback}
        now = utc(self.clock.now())
        with self.db.transaction() as db:
            authorize(db, actor, {"admin", "reviewer"})
            prior = replay(db, actor, kind, key, request)
            if prior is not None:
                return prior
            r = round_row(db, round_id)
            guard(r, expected_lock, {"DRAFT"} if kind == "GENERATE" else {"IN_REVIEW"}, version_id)
            active = db.execute("SELECT 1 FROM jobs WHERE round_id=? AND status IN ('QUEUED','RUNNING')", (round_id,)).fetchone()
            if active:
                raise Conflict("มีงานของรอบนี้กำลังรันอยู่")
            if kind == "REVISE":
                db.execute("""INSERT INTO review_events(id, command_key, round_id, version_id,
                    action, feedback, actor_id, created_at) VALUES (?, ?, ?, ?, 'REQUEST_CHANGES', ?, ?, ?)""",
                           (uid(), key, round_id, version_id, feedback, actor.user_id, now))
            db.execute("UPDATE training_rounds SET status=?, lock_version=lock_version+1 WHERE id=?",
                       ("DRAFT" if kind == "GENERATE" else "CHANGES_REQUESTED", round_id))
            job_id = uid()
            db.execute("""INSERT INTO jobs(id, idempotency_key, kind, round_id, expected_lock_version,
                params_json, status, max_attempts, available_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 'QUEUED', ?, ?, ?)""",
                       (job_id, key, kind, round_id, expected_lock + 1,
                        dump({"parent_version_id": version_id, "feedback": feedback}), self.max_attempts, now, now))
            audit(db, actor, kind, round_id, now, {"job_id": job_id})
            return receipt(db, actor, kind, key, request, job_id, now)

    def approve_version(self, actor, round_id, version_id, expected_lock, key):
        request = {"round": round_id, "version": version_id}
        now = utc(self.clock.now())
        with self.db.transaction() as db:
            authorize(db, actor, {"admin", "reviewer"})
            prior = replay(db, actor, "approve", key, request)
            if prior is not None:
                return prior
            r = round_row(db, round_id)
            guard(r, expected_lock, {"IN_REVIEW"}, version_id)
            v = one(db, "SELECT * FROM training_versions WHERE id=? AND round_id=?", (version_id, round_id))
            settings = RoundSettings.model_validate_json(r["settings_json"])
            if json.loads(v["settings_snapshot_json"]) != json.loads(r["settings_json"]):
                raise Conflict("ค่าตั้งรอบไม่ตรงกับ version")
            ContentBundle.model_validate_json(v["bundle_json"]).validate_against(
                settings, [SourceRecord.model_validate(s) for s in json.loads(v["sources_json"])])
            self.files.read_verified(v["pdf_path"], v["pdf_sha256"])
            event_id = uid()
            db.execute("""INSERT INTO review_events(id, command_key, round_id, version_id,
                action, actor_id, created_at) VALUES (?, ?, ?, ?, 'APPROVE', ?, ?)""",
                       (event_id, key, round_id, version_id, actor.user_id, now))
            db.execute("UPDATE training_rounds SET status='APPROVED', lock_version=lock_version+1 WHERE id=?", (round_id,))
            audit(db, actor, "APPROVE", round_id, now, {"version_id": version_id})
            return receipt(db, actor, "approve", key, request, event_id, now)

    def publish_round(self, actor, round_id, version_id, expected_lock, key):
        request = {"round": round_id, "version": version_id}
        now = utc(self.clock.now())
        with self.db.transaction() as db:
            authorize(db, actor, {"admin"})
            prior = replay(db, actor, "publish", key, request)
            if prior is not None:
                return prior
            r = round_row(db, round_id)
            guard(r, expected_lock, {"APPROVED"}, version_id)
            settings = RoundSettings.model_validate_json(r["settings_json"])
            if utc(settings.due_at) <= now:
                raise ValidationError("กำหนดส่งต้องอยู่หลังเวลาเผยแพร่")
            v = one(db, "SELECT * FROM training_versions WHERE id=?", (version_id,))
            self.files.read_verified(v["pdf_path"], v["pdf_sha256"])
            people = {}
            for group in settings.target_group_ids:
                for user in rows(db, """SELECT u.* FROM users u JOIN group_memberships g ON g.user_id=u.id
                    WHERE g.group_id=? AND u.is_active=1""", (group,)):
                    people[user["id"]] = user
            if not people:
                raise ValidationError("กลุ่มที่เลือกไม่มีผู้ใช้ที่เปิดใช้งาน")
            db.execute("""UPDATE training_rounds SET status='PUBLISHED', published_version_id=?,
                published_at=?, lock_version=lock_version+1 WHERE id=?""", (version_id, now, round_id))
            assignments = []
            for person in people.values():
                assignment_id = uid()
                db.execute("""INSERT INTO assignments(id, round_id, user_id, version_id,
                    display_name_snapshot, email_snapshot, assigned_at) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                           (assignment_id, round_id, person["id"], version_id, person["display_name"], person["email"], now))
                a = one(db, "SELECT * FROM assignments WHERE id=?", (assignment_id,))
                payload = {"schema_version": 1, "subject": "[DEMO] " + r["title"],
                           "body": "ข้อมูลสาธิต ไม่ใช่ข่าวล่าสุดและยังไม่ได้ส่งอีเมลจริง",
                           "quiz_url": self.base_url + "/?token=" + self.links.issue(a),
                           "pdf_path": v["pdf_path"], "pdf_sha256": v["pdf_sha256"]}
                db.execute("""INSERT INTO email_outbox(id, assignment_id, transport, status, recipient,
                    message_id, payload_json, available_at, created_at)
                    VALUES (?, ?, 'preview', 'PENDING', ?, ?, ?, ?, ?)""",
                           (uid(), assignment_id, person["email"], "<" + uid() + "@awareflow.invalid>", dump(payload), now, now))
                assignments.append(assignment_id)
            audit(db, actor, "PUBLISH", round_id, now, {"version_id": version_id, "assigned": len(assignments)})
            return receipt(db, actor, "publish", key, request, assignments, now)

    def close_round(self, actor, round_id, expected_lock, reason, key):
        if not reason.strip() or len(reason) > 2000:
            raise ValidationError("กรุณาระบุเหตุผลในการปิดรอบ")
        request = {"round": round_id, "reason": reason}
        now = utc(self.clock.now())
        with self.db.transaction() as db:
            authorize(db, actor, {"admin"})
            prior = replay(db, actor, "close", key, request)
            if prior is not None:
                return prior
            guard(round_row(db, round_id), expected_lock, {"PUBLISHED"})
            db.execute("UPDATE training_rounds SET status='CLOSED', closed_at=?, lock_version=lock_version+1 WHERE id=?", (now, round_id))
            db.execute("""UPDATE email_outbox SET status='CANCELLED' WHERE status IN ('PENDING','FAILED')
                AND assignment_id IN (SELECT id FROM assignments WHERE round_id=?)""", (round_id,))
            audit(db, actor, "CLOSE", round_id, now, {"reason": reason})
            return receipt(db, actor, "close", key, request, round_id, now)

    def get_round(self, actor, round_id):
        with self.db.read() as db:
            authorize(db, actor, {"admin", "reviewer"})
            return round_row(db, round_id)

    def list_rounds(self, actor):
        with self.db.read() as db:
            authorize(db, actor, {"admin", "reviewer"})
            return rows(db, "SELECT * FROM training_rounds ORDER BY created_at DESC")

    def review(self, actor, round_id, version_id=None):
        with self.db.read() as db:
            authorize(db, actor, {"admin", "reviewer"})
            r = round_row(db, round_id)
            v = one(db, "SELECT * FROM training_versions WHERE round_id=? AND id=?", (round_id, version_id or r["current_version_id"]))
            return {"round": r, "version": v,
                    "history": rows(db, "SELECT * FROM review_events WHERE round_id=? ORDER BY created_at, rowid", (round_id,))}

    def list_jobs(self, actor, round_id):
        with self.db.read() as db:
            authorize(db, actor, {"admin", "reviewer"})
            return rows(db, "SELECT * FROM jobs WHERE round_id=? ORDER BY created_at", (round_id,))

    def versions(self, actor, round_id):
        with self.db.read() as db:
            authorize(db, actor, {"admin", "reviewer"})
            return rows(db, "SELECT id,version_number,created_at,change_summary FROM training_versions WHERE round_id=? ORDER BY version_number DESC", (round_id,))
