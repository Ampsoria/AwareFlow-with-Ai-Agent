"""Learner boundary: no answer key is returned before an allowed result view."""
import json
import secrets

from awareflow.domain.errors import Conflict, Forbidden, InvalidTransition, ValidationError
from awareflow.domain.models import ContentBundle, utc
from awareflow.infrastructure.database import one, rows
from .common import audit, authorize, digest, dump, uid


class Quizzes:
    def __init__(self, database, clock, links, files):
        self.db, self.clock, self.links, self.files = database, clock, links, files

    def _assignment(self, db, actor, assignment_id):
        authorize(db, actor)
        a = one(db, """SELECT a.*, r.status AS round_status, r.title, r.due_at,
            r.allow_retakes, r.pass_percent FROM assignments a
            JOIN training_rounds r ON r.id=a.round_id WHERE a.id=?""", (assignment_id,))
        if a["user_id"] != actor.user_id:
            raise Forbidden("งานนี้ไม่ได้มอบหมายให้ผู้ใช้ที่เข้าสู่ระบบ")
        return a

    def _bundle(self, db, assignment):
        v = one(db, "SELECT * FROM training_versions WHERE id=?", (assignment["version_id"],))
        return ContentBundle.model_validate_json(v["bundle_json"])

    def list_assignments(self, actor):
        with self.db.read() as db:
            authorize(db, actor)
            return rows(db, """SELECT a.id, a.round_id, r.title, r.status, r.due_at,
                (SELECT COUNT(*) FROM attempts t WHERE t.assignment_id=a.id) AS attempt_count
                FROM assignments a JOIN training_rounds r ON r.id=a.round_id
                WHERE a.user_id=? ORDER BY a.assigned_at DESC""", (actor.user_id,))

    def get_student_quiz(self, actor, signed_link):
        payload = self.links.verify(signed_link)
        with self.db.read() as db:
            a = self._assignment(db, actor, payload["assignment_id"])
            self.links.check_assignment(payload, a)
            bundle = self._bundle(db, a)
            version = one(db, "SELECT sources_json FROM training_versions WHERE id=?", (a["version_id"],))
            # Explicit projection: never serialize the teacher's bundle to the client.
            return {"assignment_id": a["id"], "version_id": a["version_id"], "title": a["title"],
                    "due_at": a["due_at"], "round_status": a["round_status"],
                    "allow_retakes": bool(a["allow_retakes"]), "is_demo": bundle.provenance.is_demo,
                    "lesson": {"title": bundle.title, "scenario": bundle.scenario,
                               "sections": [{"heading": section.heading, "body": section.body}
                                            for section in bundle.sections],
                               "employee_actions": [action.text for action in bundle.employee_actions],
                               "sources": [{"title": source["title"], "publisher": source["publisher"],
                                            "url": source.get("url")}
                                           for source in json.loads(version["sources_json"])]},
                    "questions": [{"question_id": q.question_id, "prompt": q.prompt,
                                   "options": [o.model_dump() for o in q.options]} for q in bundle.questions]}

    def own_link(self, actor, assignment_id):
        with self.db.read() as db:
            return self.links.issue(self._assignment(db, actor, assignment_id))

    def lesson_pdf(self, actor, signed_link):
        payload = self.links.verify(signed_link)
        with self.db.read() as db:
            a = self._assignment(db, actor, payload["assignment_id"])
            self.links.check_assignment(payload, a)
            v = one(db, "SELECT pdf_path, pdf_sha256 FROM training_versions WHERE id=?", (a["version_id"],))
        return self.files.read_verified(v["pdf_path"], v["pdf_sha256"])

    def begin_submission(self, actor, assignment_id):
        with self.db.transaction() as db:
            a = self._assignment(db, actor, assignment_id)
            if a["round_status"] != "PUBLISHED":
                raise InvalidTransition("รอบนี้ไม่เปิดรับคำตอบ")
            prior = db.execute("SELECT 1 FROM attempts WHERE assignment_id=?", (assignment_id,)).fetchone()
            if prior and not a["allow_retakes"]:
                raise Conflict("รอบนี้ไม่อนุญาตให้สอบซ้ำ")
            # Resume an unconsumed form across reruns/restarts. Retake gets a new ticket only after consumption.
            active = db.execute("SELECT key FROM submission_tickets WHERE assignment_id=? AND consumed_attempt_id IS NULL", (assignment_id,)).fetchone()
            if active:
                return active["key"]
            key = secrets.token_urlsafe(32)
            db.execute("INSERT INTO submission_tickets(key, assignment_id, created_at) VALUES (?, ?, ?)",
                       (key, assignment_id, utc(self.clock.now())))
            return key

    def submit_attempt(self, actor, assignment_id, submission_key, answers):
        if not isinstance(answers, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in answers.items()):
            raise ValidationError("คำตอบต้องเป็นรหัสข้อสอบและรหัสตัวเลือก")
        now = utc(self.clock.now())
        with self.db.transaction() as db:
            a = self._assignment(db, actor, assignment_id)
            ticket = one(db, "SELECT * FROM submission_tickets WHERE key=? AND assignment_id=?", (submission_key, assignment_id))
            if ticket["consumed_attempt_id"]:
                previous = one(db, "SELECT * FROM attempts WHERE id=?", (ticket["consumed_attempt_id"],))
                if previous["answers_sha256"] != digest(answers):
                    raise Conflict("แบบฟอร์มนี้ส่งแล้ว แต่คำตอบไม่ตรงกับครั้งก่อน")
                return self._result(db, a, previous)
            if a["round_status"] != "PUBLISHED":
                raise InvalidTransition("รอบนี้ไม่เปิดรับคำตอบ")
            latest = db.execute("SELECT MAX(attempt_number) FROM attempts WHERE assignment_id=?", (assignment_id,)).fetchone()[0] or 0
            if latest and not a["allow_retakes"]:
                raise Conflict("ไม่อนุญาตให้สอบซ้ำ")
            bundle = self._bundle(db, a)
            if set(answers) != {q.question_id for q in bundle.questions}:
                raise ValidationError("ต้องตอบทุกข้อ และไม่มีข้อสอบที่เกินมา")
            if any(answers[q.question_id] not in {o.option_id for o in q.options} for q in bundle.questions):
                raise ValidationError("ตัวเลือกไม่ถูกต้อง")
            correct = sum(answers[q.question_id] == q.correct_option_id for q in bundle.questions)
            attempt_id = uid()
            db.execute("""INSERT INTO attempts(id, assignment_id, version_id, submission_key,
                attempt_number, answers_json, answers_sha256, correct_count, question_count, submitted_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                       (attempt_id, assignment_id, a["version_id"], submission_key, latest + 1,
                        dump(answers), digest(answers), correct, len(bundle.questions), now))
            db.execute("UPDATE submission_tickets SET consumed_attempt_id=? WHERE key=?", (attempt_id, submission_key))
            audit(db, actor, "SUBMIT", assignment_id, now, {"attempt_id": attempt_id})
            return self._result(db, a, one(db, "SELECT * FROM attempts WHERE id=?", (attempt_id,)))

    def get_result(self, actor, assignment_id):
        with self.db.read() as db:
            a = self._assignment(db, actor, assignment_id)
            attempt = db.execute("SELECT * FROM attempts WHERE assignment_id=? ORDER BY attempt_number DESC LIMIT 1", (assignment_id,)).fetchone()
            return self._result(db, a, dict(attempt)) if attempt else {"status": "NOT_STARTED", "score": None}

    def _result(self, db, assignment, attempt):
        passed = attempt["correct_count"] * 100 >= assignment["pass_percent"] * attempt["question_count"]
        result = {"attempt_id": attempt["id"], "attempt_number": attempt["attempt_number"],
                  "correct_count": attempt["correct_count"], "question_count": attempt["question_count"],
                  "score": attempt["correct_count"] * 100 / attempt["question_count"],
                  "status": "PASSED" if passed else "FAILED", "submitted_at": attempt["submitted_at"],
                  "on_time": attempt["submitted_at"] <= assignment["due_at"],
                  "feedback": "ทบทวนบทอ่านก่อนเริ่มครั้งถัดไป"}
        if not assignment["allow_retakes"] or assignment["round_status"] == "CLOSED":
            result["explanations"] = [q.model_dump() for q in self._bundle(db, assignment).questions]
        return result
