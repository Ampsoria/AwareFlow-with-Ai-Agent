"""Durable preview/SMTP outboxes with an explicit boundary for real delivery."""
from email.message import EmailMessage
from email.policy import SMTP
import json
from datetime import timedelta

from awareflow.domain.errors import Conflict
from awareflow.domain.models import utc
from awareflow.infrastructure.database import one, rows
from .common import audit, authorize, dump, receipt, replay, uid
from awareflow.infrastructure.smtp import SmtpTransport, validate_recipient


class Delivery:
    def __init__(self, database, clock, files, links, config=None, transport=None):
        self.db, self.clock, self.files, self.links = database, clock, files, links
        self.config = config
        self.transport = transport or (SmtpTransport(config) if config else None)

    def _message(self, outbox, sender="awareflow@example.invalid"):
        payload = json.loads(outbox["payload_json"])
        message = EmailMessage(policy=SMTP)
        message["From"], message["To"] = sender, outbox["recipient"] or "missing-address@example.invalid"
        message["Subject"], message["Message-ID"] = payload["subject"], outbox["message_id"]
        message.set_content(payload["body"] + "\n\n" + payload["quiz_url"])
        message.add_attachment(self.files.read_verified(payload["pdf_path"], payload["pdf_sha256"]),
                               maintype="application", subtype="pdf", filename="lesson.pdf")
        message.set_boundary("awareflow-" + outbox["id"])
        return message

    def list_outbox(self, actor, round_id):
        with self.db.transaction() as db:
            authorize(db, actor, {"admin", "reviewer"})
            expired = rows(db, "SELECT id FROM email_outbox WHERE transport='smtp' AND status='SENDING' AND lease_expires_at<=?", (utc(self.clock.now()),))
            for item in expired:
                db.execute("UPDATE email_outbox SET status='UNKNOWN',lease_token=NULL,lease_expires_at=NULL,last_error_safe='หมดเวลาโดยไม่ทราบผล ต้องตรวจสอบก่อนส่งซ้ำ' WHERE id=?", (item["id"],))
                audit(db, actor, "SMTP_LEASE_EXPIRED", item["id"], utc(self.clock.now()))
            return rows(db, """SELECT e.*, a.user_id FROM email_outbox e JOIN assignments a
                ON a.id=e.assignment_id WHERE a.round_id=? ORDER BY a.user_id""", (round_id,))

    def prepare_previews(self, actor, round_id):
        count = 0
        for outbox in self.list_outbox(actor, round_id):
            if outbox["transport"] != "preview" or outbox["status"] not in {"PENDING", "FAILED"}:
                continue
            message = self._message(outbox)
            path, _ = self.files.put(message.as_bytes(), "eml")
            with self.db.transaction() as db:
                authorize(db, actor, {"admin", "reviewer"})
                live = one(db, "SELECT status FROM training_rounds WHERE id=?", (round_id,))
                if live["status"] != "PUBLISHED":
                    raise Conflict("รอบนี้ปิดแล้ว ไม่เตรียมคำเชิญเพิ่ม")
                changed = db.execute("""UPDATE email_outbox SET status='PREVIEW_READY', eml_path=?
                    WHERE id=? AND status IN ('PENDING','FAILED')""", (path, outbox["id"])).rowcount
                count += changed
                if changed:
                    audit(db, actor, "PREVIEW_READY", outbox["id"], utc(self.clock.now()))
        return count

    def prepare_smtp(self, actor, assignment_id, key):
        now = utc(self.clock.now())
        request = {"assignment_id": assignment_id}
        with self.db.transaction() as db:
            authorize(db, actor, {"admin"})
            prior = replay(db, actor, "prepare_smtp", key, request)
            if prior is not None:
                return prior
            a = one(db, "SELECT * FROM assignments WHERE id=?", (assignment_id,))
            validate_recipient(self.config, a["email_snapshot"])
            r = one(db, "SELECT status FROM training_rounds WHERE id=?", (a["round_id"],))
            if r["status"] != "PUBLISHED":
                raise Conflict("รอบนี้ไม่ได้เปิดอยู่")
            existing = db.execute("SELECT id FROM email_outbox WHERE assignment_id=? AND transport='smtp'", (assignment_id,)).fetchone()
            if existing:
                return receipt(db, actor, "prepare_smtp", key, request, existing["id"], now)
            preview = one(db, "SELECT * FROM email_outbox WHERE assignment_id=? AND transport='preview'", (assignment_id,))
            payload = json.loads(preview["payload_json"])
            payload["body"] = "คำเชิญทดสอบระบบ Security Awareness สำหรับบัญชีสาธิต"
            payload["quiz_url"] = self.config.base_url.rstrip("/") + "/?token=" + self.links.issue(a)
            outbox_id = uid()
            db.execute("""INSERT INTO email_outbox(id,assignment_id,transport,status,recipient,message_id,
                payload_json,available_at,created_at) VALUES (?,?,'smtp','PENDING',?,?,?,?,?)""",
                       (outbox_id, assignment_id, a["email_snapshot"], "<" + uid() + "@awareflow.invalid>", dump(payload), now, now))
            audit(db, actor, "PREPARE_SMTP", outbox_id, now)
            return receipt(db, actor, "prepare_smtp", key, request, outbox_id, now)

    def send_smtp(self, actor, outbox_id):
        now = utc(self.clock.now())
        with self.db.transaction() as db:
            authorize(db, actor, {"admin"})
            db.execute("""UPDATE email_outbox SET status='UNKNOWN',lease_token=NULL,lease_expires_at=NULL,
                last_error_safe='ไม่ทราบผลส่งก่อน process หยุด กรุณาตรวจสอบก่อนส่งซ้ำ'
                WHERE transport='smtp' AND status='SENDING' AND lease_expires_at<=?""", (now,))
            outbox = one(db, "SELECT * FROM email_outbox WHERE id=? AND transport='smtp'", (outbox_id,))
            validate_recipient(self.config, outbox["recipient"])
            if outbox["status"] == "ACCEPTED":
                return "ACCEPTED"
            if outbox["status"] not in {"PENDING", "FAILED"} or outbox["attempts"] >= self.config.max_attempts:
                raise Conflict("ส่งไม่ได้: งานกำลังส่ง ผลไม่แน่นอน ถูกยกเลิก หรือครบจำนวนครั้งแล้ว")
            r = one(db, "SELECT r.status FROM training_rounds r JOIN assignments a ON a.round_id=r.id WHERE a.id=?", (outbox["assignment_id"],))
            if r["status"] != "PUBLISHED":
                raise Conflict("รอบนี้ปิดแล้ว")
            token = uid()
            message = self._message(outbox, self.config.smtp_from)
            db.execute("UPDATE email_outbox SET status='SENDING',attempts=attempts+1,lease_token=?,lease_expires_at=? WHERE id=?",
                       (token, utc(self.clock.now() + timedelta(minutes=5)), outbox_id))
        try:
            outcome = self.transport.send(message, outbox["recipient"])
            if outcome not in {"ACCEPTED", "FAILED", "UNKNOWN"}:
                outcome = "UNKNOWN"
        except Exception:
            outcome = "UNKNOWN"
        with self.db.transaction() as db:
            db.execute("""UPDATE email_outbox SET status=?,accepted_at=?,lease_token=NULL,lease_expires_at=NULL,
                last_error_safe=? WHERE id=? AND status='SENDING' AND lease_token=?""",
                       (outcome, utc(self.clock.now()) if outcome == "ACCEPTED" else None,
                        None if outcome == "ACCEPTED" else "ส่งไม่สำเร็จ" if outcome == "FAILED" else "ไม่ทราบผล ห้ามส่งซ้ำโดยอัตโนมัติ", outbox_id, token))
            audit(db, actor, "SMTP_" + outcome, outbox_id, utc(self.clock.now()))
            saved = one(db, "SELECT status FROM email_outbox WHERE id=?", (outbox_id,))["status"]
        return saved

    def resolve_unknown(self, actor, outbox_id, decision, reason, key):
        if decision not in {"accepted", "retry"} or not reason.strip():
            raise Conflict("ระบุผลตรวจสอบและเหตุผลก่อนดำเนินการ")
        request = {"outbox_id": outbox_id, "decision": decision, "reason": reason}
        now = utc(self.clock.now())
        with self.db.transaction() as db:
            authorize(db, actor, {"admin"})
            prior = replay(db, actor, "resolve_smtp", key, request)
            if prior is not None:
                return prior
            row = one(db, "SELECT * FROM email_outbox WHERE id=?", (outbox_id,))
            if row["status"] != "UNKNOWN":
                raise Conflict("รายการนี้ไม่ได้อยู่ในสถานะ UNKNOWN")
            db.execute("UPDATE email_outbox SET status=?,accepted_at=? WHERE id=?",
                       ("ACCEPTED" if decision == "accepted" else "FAILED", now if decision == "accepted" else None, outbox_id))
            audit(db, actor, "RESOLVE_SMTP", outbox_id, now, request)
            return receipt(db, actor, "resolve_smtp", key, request, outbox_id, now)

    def issue_link(self, actor, assignment_id, revoke_old=False):
        with self.db.transaction() as db:
            authorize(db, actor, {"admin"})
            if revoke_old:
                db.execute("UPDATE assignments SET token_generation=token_generation+1 WHERE id=?", (assignment_id,))
            a = one(db, "SELECT * FROM assignments WHERE id=?", (assignment_id,))
            audit(db, actor, "ROTATE_LINK" if revoke_old else "ISSUE_LINK", assignment_id, utc(self.clock.now()))
            return self.links.issue(a)
