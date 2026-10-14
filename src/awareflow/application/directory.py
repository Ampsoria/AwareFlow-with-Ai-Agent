import json
import re

from awareflow.domain.errors import ValidationError
from awareflow.domain.models import CompanyContext, utc
from awareflow.infrastructure.database import one, rows
from .common import audit, authorize, dump


class Directory:
    def __init__(self, db, clock):
        self.db, self.clock = db, clock

    def demo_identities(self):
        # Public ONLY for the explicitly labelled demo identity selector.
        with self.db.read() as db:
            return rows(db, "SELECT id,display_name,role FROM users WHERE is_active=1 ORDER BY id")

    def users(self, actor):
        with self.db.read() as db:
            authorize(db, actor, {"admin", "reviewer"})
            return rows(db, "SELECT * FROM users ORDER BY id")

    def groups(self, actor):
        with self.db.read() as db:
            authorize(db, actor, {"admin", "reviewer"})
            groups = rows(db, "SELECT * FROM user_groups ORDER BY name")
            for group in groups:
                group["members"] = [r[0] for r in db.execute("SELECT user_id FROM group_memberships WHERE group_id=? ORDER BY user_id", (group["id"],))]
            return groups

    def save_user(self, actor, user_id, name, email, role, active):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", user_id) or not name.strip() or len(name) > 200:
            raise ValidationError("รหัสผู้ใช้หรือชื่อไม่ถูกต้อง")
        if email and not re.fullmatch(r"[^\s<>@]+@[^\s<>@]+\.[^\s<>@]+", email):
            raise ValidationError("อีเมลไม่ถูกต้อง")
        if role not in {"admin", "reviewer", "learner"}:
            raise ValidationError("บทบาทไม่ถูกต้อง")
        if user_id == actor.user_id and (not active or role != "admin"):
            raise ValidationError("ไม่สามารถปิดสิทธิ์ผู้ดูแลของบัญชีที่กำลังใช้งาน")
        with self.db.transaction() as db:
            authorize(db, actor, {"admin"})
            db.execute("""INSERT INTO users(id,display_name,email,role,is_active,created_at) VALUES (?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET display_name=excluded.display_name,email=excluded.email,
                role=excluded.role,is_active=excluded.is_active""", (user_id, name.strip(), email or None, role, int(active), utc(self.clock.now())))
            audit(db, actor, "SAVE_USER", user_id, utc(self.clock.now()))

    def save_group(self, actor, group_id, name, members):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", group_id) or not name.strip():
            raise ValidationError("รหัสกลุ่มหรือชื่อกลุ่มไม่ถูกต้อง")
        with self.db.transaction() as db:
            authorize(db, actor, {"admin"})
            for member in members:
                one(db, "SELECT id FROM users WHERE id=?", (member,))
            db.execute("INSERT INTO user_groups(id,name) VALUES (?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name", (group_id, name.strip()))
            db.execute("DELETE FROM group_memberships WHERE group_id=?", (group_id,))
            db.executemany("INSERT INTO group_memberships(group_id,user_id) VALUES (?,?)", [(group_id, member) for member in set(members)])
            audit(db, actor, "SAVE_GROUP", group_id, utc(self.clock.now()))

    def company_context(self, actor):
        with self.db.read() as db:
            authorize(db, actor, {"admin", "reviewer"})
            row = db.execute("SELECT value_json FROM app_settings WHERE key='company_context'").fetchone()
            return CompanyContext.model_validate_json(row[0]) if row else CompanyContext()

    def save_context(self, actor, context: CompanyContext):
        with self.db.transaction() as db:
            authorize(db, actor, {"admin"})
            db.execute("INSERT INTO app_settings VALUES ('company_context',?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json", (context.model_dump_json(),))
            audit(db, actor, "SAVE_CONTEXT", "company", utc(self.clock.now()))

    def audit_log(self, actor):
        with self.db.read() as db:
            authorize(db, actor, {"admin"})
            return rows(db, "SELECT actor_id,action,target_id,created_at FROM audit_events ORDER BY created_at DESC LIMIT 100")
