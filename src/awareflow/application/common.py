import hashlib
import json
from uuid import uuid4

from awareflow.domain.errors import Conflict, Forbidden, InvalidTransition, ValidationError
from awareflow.infrastructure.database import one


def uid():
    return str(uuid4())


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(dump(value).encode()).hexdigest()


def authorize(db, actor, roles=None):
    row = db.execute("SELECT * FROM users WHERE id=? AND is_active=1", (actor.user_id,)).fetchone()
    if row is None or (roles and row["role"] not in roles):
        raise Forbidden("ไม่มีสิทธิ์ทำรายการนี้")
    return dict(row)


def guard(round_, expected_lock, states, version_id=None):
    if round_["lock_version"] != expected_lock:
        raise Conflict("ข้อมูลเปลี่ยนแล้ว กรุณาโหลดรอบอบรมใหม่")
    if round_["status"] not in states:
        raise InvalidTransition("สถานะรอบไม่รองรับรายการนี้")
    if version_id and round_["current_version_id"] != version_id:
        raise Conflict("เวอร์ชันที่ตรวจไม่ใช่เวอร์ชันปัจจุบัน")


def replay(db, actor, operation, key, request):
    if not isinstance(key, str) or not 1 <= len(key) <= 200:
        raise ValidationError("ต้องมี command key ความยาว 1-200 ตัวอักษร")
    row = db.execute("SELECT * FROM command_receipts WHERE command_key=?", (key,)).fetchone()
    if row:
        if (row["actor_id"], row["operation"], row["request_hash"]) != (actor.user_id, operation, digest(request)):
            raise Conflict("command key นี้ใช้กับคำขออื่นแล้ว")
        return json.loads(row["result_json"])
    return None


def receipt(db, actor, operation, key, request, result, now):
    db.execute("INSERT INTO command_receipts VALUES (?, ?, ?, ?, ?, ?)",
               (key, actor.user_id, operation, digest(request), dump(result), now))
    return result


def audit(db, actor, action, target, now, detail=None):
    db.execute("INSERT INTO audit_events VALUES (?, ?, ?, 'workflow', ?, ?, ?)",
               (uid(), actor.user_id if actor else None, action, target, dump(detail or {}), now))


def round_row(db, round_id):
    return one(db, "SELECT * FROM training_rounds WHERE id=?", (round_id,))
