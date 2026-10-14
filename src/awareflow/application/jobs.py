"""Leased generation worker. Slow provider/PDF work happens outside DB transactions."""
from datetime import timedelta
import json
import threading

from pydantic import ValidationError as SchemaError

from awareflow.domain.errors import AgentNeedsInput, Conflict, NoSuitableSources, ValidationError, TransientProviderError
from awareflow.domain.models import ContentBundle, RoundSettings, utc
from awareflow.infrastructure.database import one
from .common import audit, authorize, dump, guard, receipt, replay, round_row, uid
from .agent import AwarenessAgent


class GenerationWorker:
    def __init__(self, database, clock, search, llm, pdf, files, lease_seconds=120,
                 agent_max_steps=12, agent_max_searches=5, agent_max_drafts=3):
        self.db, self.clock, self.search, self.llm = database, clock, search, llm
        self.pdf, self.files, self.lease_seconds = pdf, files, lease_seconds
        self.agent_limits = (agent_max_steps, agent_max_searches, agent_max_drafts)

    def claim(self):
        now = utc(self.clock.now())
        expires = utc(self.clock.now() + timedelta(seconds=self.lease_seconds))
        with self.db.transaction() as db:
            db.execute("""UPDATE jobs SET status=CASE WHEN attempts>=max_attempts THEN 'FAILED' ELSE 'QUEUED' END,
                lease_token=NULL, lease_expires_at=NULL, last_error_code='LEASE_EXPIRED',
                last_error_safe='งานหมดเวลา สามารถลองใหม่ตามจำนวนครั้งที่เหลือ'
                WHERE kind IN ('GENERATE','REVISE') AND status='RUNNING' AND lease_expires_at<=?""", (now,))
            candidate = db.execute("""SELECT * FROM jobs WHERE kind IN ('GENERATE','REVISE')
                AND status='QUEUED' AND attempts<max_attempts AND available_at<=?
                ORDER BY created_at, rowid LIMIT 1""", (now,)).fetchone()
            if candidate is None:
                return None
            token = uid()
            db.execute("""UPDATE jobs SET status='RUNNING', attempts=attempts+1,
                lease_token=?, lease_expires_at=? WHERE id=?""", (token, expires, candidate["id"]))
            return one(db, "SELECT * FROM jobs WHERE id=?", (candidate["id"],))

    def run_once(self):
        job = self.claim()
        if job is None:
            return None
        stop = threading.Event()
        heartbeat = threading.Thread(target=self._heartbeat, args=(job, stop), daemon=True)
        heartbeat.start()
        try:
            self.process(job)
        except TransientProviderError:
            self._failure(job, "QUEUED" if job["attempts"] < job["max_attempts"] else "FAILED",
                          "TEMPORARY", "บริการไม่พร้อมชั่วคราว ระบบจะลองใหม่ตามจำนวนครั้งที่เหลือ")
        except NoSuitableSources:
            self._failure(job, "NEEDS_INPUT", "NO_SOURCES", "ไม่พบข้อมูลที่เหมาะสม เลือกหัวข้อทบทวนหรือแก้ค่าค้นหา")
        except AgentNeedsInput as exc:
            self._failure(job, "NEEDS_INPUT", str(exc), "Agent ต้องการข้อมูลเพิ่มหรือถึงขีดจำกัด ดูประวัติ Agent แล้วแก้ค่ารอบและเริ่มใหม่")
        except Conflict:
            self._failure(job, "CANCELLED", "STALE", "รอบหรือเจ้าของงานเปลี่ยนแล้ว งานเก่าถูกยกเลิก")
        except (ValidationError, SchemaError):
            self._failure(job, "FAILED", "INVALID_OUTPUT", "ข้อมูลหรือ PDF ไม่ผ่านการตรวจ กรุณาปรับแล้วลองใหม่")
        except Exception:
            # Provider exceptions may embed credentials or URLs. Never store their raw text.
            self._failure(job, "FAILED", "PROVIDER_ERROR", "สร้างชุดอบรมไม่สำเร็จ ตรวจการตั้งค่าและลองใหม่")
        finally:
            stop.set()
            heartbeat.join(timeout=2)
        with self.db.read() as db:
            return one(db, "SELECT * FROM jobs WHERE id=?", (job["id"],))

    def _heartbeat(self, job, stop):
        while not stop.wait(max(1, self.lease_seconds / 3)):
            try:
                with self.db.transaction() as db:
                    changed = db.execute("""UPDATE jobs SET lease_expires_at=? WHERE id=?
                        AND status='RUNNING' AND lease_token=? AND lease_expires_at>?""",
                        (utc(self.clock.now() + timedelta(seconds=self.lease_seconds)), job["id"], job["lease_token"], utc(self.clock.now()))).rowcount
                if not changed:
                    return
            except Exception:
                return  # Ownership check at commit remains authoritative.

    def _check_ownership(self, db, job):
        live = one(db, "SELECT * FROM jobs WHERE id=?", (job["id"],))
        if live["status"] != "RUNNING" or live["lease_token"] != job["lease_token"] or live["lease_expires_at"] <= utc(self.clock.now()):
            raise Conflict("งานหมดอายุหรือเปลี่ยนเจ้าของแล้ว")
        r = round_row(db, job["round_id"])
        guard(r, job["expected_lock_version"], {"DRAFT"} if job["kind"] == "GENERATE" else {"CHANGES_REQUESTED"})
        return r

    def process(self, job):
        with self.db.read() as db:
            r = self._check_ownership(db, job)
            settings = RoundSettings.model_validate_json(r["settings_json"])
            params = json.loads(job["params_json"])
            previous = None
            if params["parent_version_id"]:
                previous = ContentBundle.model_validate_json(one(db, "SELECT bundle_json FROM training_versions WHERE id=? AND round_id=?",
                                                                 (params["parent_version_id"], r["id"]))["bundle_json"])
        def check_live():
            with self.db.read() as db:
                self._check_ownership(db, job)

        def started(step, tool, arguments):
            with self.db.transaction() as db:
                self._check_ownership(db, job)
                db.execute("""INSERT INTO agent_steps(job_id,attempt,step,tool,arguments_json,status,created_at)
                    VALUES (?,?,?,?,?,'STARTED',?)""",
                    (job["id"], job["attempts"], step, tool, dump(arguments), utc(self.clock.now())))

        def finished(step, status, result):
            with self.db.transaction() as db:
                self._check_ownership(db, job)
                db.execute("""UPDATE agent_steps SET status=?,result_json=?,completed_at=?
                    WHERE job_id=? AND attempt=? AND step=? AND status='STARTED'""",
                    (status, dump(result), utc(self.clock.now()), job["id"], job["attempts"], step))

        result = AwarenessAgent(self.llm, self.search, self.pdf, *self.agent_limits).run(
            settings, previous, params["feedback"], before_step=started, after_step=finished, guard=check_live)
        bundle, eligible = result.bundle, result.sources
        check_live()
        pdf_path, pdf_sha = self.files.put(result.pdf, "pdf")
        now = utc(self.clock.now())
        with self.db.transaction() as db:
            r = self._check_ownership(db, job)
            version_id = uid()
            number = db.execute("SELECT COALESCE(MAX(version_number),0)+1 FROM training_versions WHERE round_id=?", (r["id"],)).fetchone()[0]
            db.execute("""INSERT INTO training_versions(id, round_id, version_number, parent_version_id,
                bundle_json, sources_json, settings_snapshot_json, validation_json, pdf_path, pdf_sha256,
                change_summary, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                       (version_id, r["id"], number, params["parent_version_id"], bundle.model_dump_json(),
                        dump([s.model_dump(mode="json") for s in eligible]), r["settings_json"],
                        dump({"schema_version": 1, "structural_validation": "passed", "human_review_required": True,
                              "agent_job_id": job["id"], "agent_attempt": job["attempts"]}),
                        pdf_path, pdf_sha, bundle.change_summary, now))
            db.execute("UPDATE training_rounds SET current_version_id=?, status='IN_REVIEW', lock_version=lock_version+1 WHERE id=?", (version_id, r["id"]))
            db.execute("UPDATE jobs SET status='SUCCEEDED', completed_at=?, lease_token=NULL, lease_expires_at=NULL WHERE id=?", (now, job["id"]))
            audit(db, None, "GENERATION_COMPLETE", r["id"], now, {"version_id": version_id, "job_id": job["id"]})

    def trace(self, actor, job_id):
        with self.db.read() as db:
            authorize(db, actor, {"admin", "reviewer"})
            one(db, "SELECT id FROM jobs WHERE id=?", (job_id,))
            return [dict(row) for row in db.execute(
                "SELECT * FROM agent_steps WHERE job_id=? ORDER BY attempt,step", (job_id,))]

    def _failure(self, job, status, code, safe_message):
        with self.db.transaction() as db:
            db.execute("""UPDATE jobs SET status=?, completed_at=?, last_error_code=?, last_error_safe=?,
                lease_token=NULL, lease_expires_at=NULL, available_at=? WHERE id=? AND status='RUNNING' AND lease_token=?""",
                       (status, None if status == "QUEUED" else utc(self.clock.now()), code, safe_message,
                        utc(self.clock.now() + timedelta(seconds=min(60, 2 ** job["attempts"]))) if status == "QUEUED" else utc(self.clock.now()), job["id"], job["lease_token"]))

    def retry(self, actor, job_id, key):
        request = {"job": job_id}
        now = utc(self.clock.now())
        with self.db.transaction() as db:
            authorize(db, actor, {"admin", "reviewer"})
            prior = replay(db, actor, "retry", key, request)
            if prior is not None:
                return prior
            job = one(db, "SELECT * FROM jobs WHERE id=?", (job_id,))
            if job["status"] != "FAILED" or job["attempts"] >= job["max_attempts"]:
                raise Conflict("ลองใหม่ไม่ได้หรือครบจำนวนครั้งแล้ว ให้แก้ค่ารอบและเริ่มงานใหม่")
            guard(round_row(db, job["round_id"]), job["expected_lock_version"], {"DRAFT", "CHANGES_REQUESTED"})
            db.execute("UPDATE jobs SET status='QUEUED', available_at=?, completed_at=NULL WHERE id=?", (now, job_id))
            audit(db, actor, "RETRY", job_id, now)
            return receipt(db, actor, "retry", key, request, job_id, now)
