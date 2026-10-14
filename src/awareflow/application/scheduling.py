"""Explicit scheduler tick. No threads are started by a UI import or rerun."""
from datetime import timedelta
import json
from zoneinfo import ZoneInfo

from awareflow.domain.errors import ValidationError
from awareflow.domain.models import Actor, RoundSettings, utc
from awareflow.infrastructure.database import one, rows
from .common import audit, authorize, dump, receipt, replay, uid


class Scheduling:
    def __init__(self, database, clock, workflow, reporting):
        self.db, self.clock, self.workflow, self.reporting = database, clock, workflow, reporting

    def list_schedules(self, actor):
        with self.db.read() as db:
            authorize(db, actor, {"admin"})
            return rows(db, "SELECT s.*,t.name FROM schedules s LEFT JOIN round_templates t ON t.id=s.template_id ORDER BY s.id")

    def set_enabled(self, actor, schedule_id, enabled):
        with self.db.transaction() as db:
            authorize(db, actor, {"admin"})
            one(db, "SELECT id FROM schedules WHERE id=?", (schedule_id,))
            db.execute("UPDATE schedules SET enabled=? WHERE id=?", (int(enabled), schedule_id))
            audit(db, actor, "SCHEDULE_ENABLE" if enabled else "SCHEDULE_PAUSE", schedule_id, utc(self.clock.now()))

    def create_schedule(self, actor, key, name, kind, settings=None, day=1, month=None,
                        hour=9, minute=0, lookback_days=90, due_days=14):
        valid = kind in {"DRAFT_MONTHLY", "DRAFT_YEARLY", "REPORT_YEARLY"}
        if not valid or not name.strip() or len(name) > 200:
            raise ValidationError("ชื่อหรือชนิด schedule ไม่ถูกต้อง")
        if any(type(v) is not int for v in (day, hour, minute, lookback_days, due_days)) or not (
            1 <= day <= 28 and 0 <= hour <= 23 and 0 <= minute <= 59 and 1 <= lookback_days <= 3660 and 1 <= due_days <= 365
        ):
            raise ValidationError("ค่าตั้งเวลาไม่ถูกต้อง")
        if (kind == "DRAFT_MONTHLY" and month is not None) or (kind != "DRAFT_MONTHLY" and (type(month) is not int or not 1 <= month <= 12)):
            raise ValidationError("รอบรายปีต้องมีเดือน 1-12; รายเดือนไม่ระบุเดือน")
        if kind != "REPORT_YEARLY" and not isinstance(settings, RoundSettings):
            raise ValidationError("ต้องมี template สำหรับสร้างร่าง")
        request = {"name": name, "kind": kind, "settings": settings.model_dump(mode="json") if settings else None,
                   "day": day, "month": month, "hour": hour, "minute": minute, "lookback_days": lookback_days, "due_days": due_days}
        now = utc(self.clock.now())
        with self.db.transaction() as db:
            authorize(db, actor, {"admin"})
            prior = replay(db, actor, "schedule", key, request)
            if prior is not None:
                return prior
            template_id = None
            if kind != "REPORT_YEARLY":
                self.workflow._groups_exist(db, settings)
                template_id = uid()
                db.execute("INSERT INTO round_templates(id,name,settings_json,updated_at) VALUES (?,?,?,?)",
                           (template_id, name, settings.model_dump_json(), now))
            schedule_id = uid()
            db.execute("""INSERT INTO schedules(id,template_id,kind,day_of_month,month_of_year,
                local_hour,local_minute,settings_json,enabled) VALUES (?,?,?,?,?,?,?,?,1)""",
                       (schedule_id, template_id, kind, day, month, hour, minute,
                        dump({"actor_id": actor.user_id, "lookback_days": lookback_days, "due_days": due_days})))
            audit(db, actor, "CREATE_SCHEDULE", schedule_id, now)
            return receipt(db, actor, "schedule", key, request, schedule_id, now)

    def tick(self):
        local = self.clock.now().astimezone(ZoneInfo("Asia/Bangkok"))
        now = utc(self.clock.now())
        with self.db.read() as db:
            schedules = rows(db, "SELECT * FROM schedules WHERE enabled=1 ORDER BY id")
        created = []
        for s in schedules:
            month = local.month if s["kind"] == "DRAFT_MONTHLY" else s["month_of_year"]
            due = local.replace(month=month, day=s["day_of_month"], hour=s["local_hour"], minute=s["local_minute"], second=0, microsecond=0)
            if local < due:
                continue
            period = local.strftime("%Y-%m") if s["kind"] == "DRAFT_MONTHLY" else local.strftime("%Y")
            with self.db.read() as db:
                if db.execute("SELECT 1 FROM schedule_runs WHERE schedule_id=? AND period_key=?", (s["id"], period)).fetchone():
                    continue
            params = json.loads(s["settings_json"])
            actor = Actor(params["actor_id"])
            command_key = "scheduled:" + s["id"] + ":" + period
            if s["kind"] == "REPORT_YEARLY":
                # Export has its own durable receipt, so a crash before schedule_runs can safely replay it.
                result = self.reporting.export(actor, command_key, year=local.year - 1)
                with self.db.transaction() as db:
                    job = one(db, "SELECT job_id FROM report_artifacts WHERE id=?", (result["report_id"],))
                    inserted = db.execute("INSERT OR IGNORE INTO schedule_runs(id,schedule_id,period_key,job_id,created_at) VALUES (?,?,?,?,?)",
                                          (uid(), s["id"], period, job["job_id"], now)).rowcount
                if inserted:
                    created.append({"schedule_id": s["id"], "report_id": result["report_id"]})
                continue
            with self.db.transaction() as db:
                authorize(db, actor, {"admin"})
                if not one(db, "SELECT enabled FROM schedules WHERE id=?", (s["id"],))["enabled"]:
                    continue
                if db.execute("SELECT 1 FROM schedule_runs WHERE schedule_id=? AND period_key=?", (s["id"], period)).fetchone():
                    continue
                template = one(db, "SELECT * FROM round_templates WHERE id=? AND is_active=1", (s["template_id"],))
                data = json.loads(template["settings_json"])
                data["title"] = (template["name"][:180] + " / " + period)
                data["due_at"] = utc(local + timedelta(days=params["due_days"]))
                data["search_window"] = {"start_date": str((local - timedelta(days=params["lookback_days"])).date()), "end_date": str(local.date())}
                settings = RoundSettings.model_validate(data)
                round_id = self.workflow._insert_round(db, actor, settings, command_key, now)
                db.execute("UPDATE training_rounds SET lock_version=1 WHERE id=?", (round_id,))
                job_id = uid()
                db.execute("""INSERT INTO jobs(id,idempotency_key,kind,round_id,expected_lock_version,
                    params_json,status,max_attempts,available_at,created_at)
                    VALUES (?,?,'GENERATE',?,1,?,'QUEUED',?,?,?)""",
                           (job_id, command_key, round_id, dump({"parent_version_id": None, "feedback": None}),
                            self.workflow.max_attempts, now, now))
                db.execute("INSERT INTO schedule_runs(id,schedule_id,period_key,round_id,job_id,created_at) VALUES (?,?,?,?,?,?)",
                           (uid(), s["id"], period, round_id, job_id, now))
                audit(db, actor, "SCHEDULE_DRAFT", round_id, now, {"schedule_id": s["id"], "period": period})
                created.append({"schedule_id": s["id"], "round_id": round_id, "job_id": job_id})
        return created
