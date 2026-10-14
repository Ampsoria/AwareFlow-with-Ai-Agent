import base64
import hashlib
import hmac
import json

from awareflow.domain.errors import Forbidden, ProviderConfigurationError
from awareflow.domain.models import Actor
from awareflow.application.common import authorize, dump


class DemoAuth:
    """Deliberate identity selection for local demos, never production authentication."""
    def __init__(self, database, mode):
        if mode != "demo":
            raise ProviderConfigurationError("DemoAuth ใช้ได้เฉพาะ APP_MODE=demo")
        self.db = database

    def select_user(self, user_id):
        actor = Actor(user_id)
        with self.db.read() as db:
            authorize(db, actor)
        return actor


class SignedLinks:
    def __init__(self, secret, clock, ttl_hours=168):
        if len(secret) < 32:
            raise ProviderConfigurationError("Signing secret ต้องยาวอย่างน้อย 32 ตัวอักษร")
        self.secret, self.clock, self.ttl = secret.encode(), clock, ttl_hours * 3600

    def issue(self, assignment):
        payload = {"purpose": "quiz", "assignment_id": assignment["id"], "user_id": assignment["user_id"],
                   "round_id": assignment["round_id"], "version_id": assignment["version_id"],
                   "generation": assignment["token_generation"], "expires": int(self.clock.now().timestamp()) + self.ttl}
        body = base64.urlsafe_b64encode(dump(payload).encode()).rstrip(b"=")
        sig = hmac.new(self.secret, body, hashlib.sha256).hexdigest()
        return body.decode() + "." + sig

    def verify(self, token):
        try:
            if not isinstance(token, str) or len(token) > 3000:
                raise ValueError()
            body, sig = token.split(".")
            expected = hmac.new(self.secret, body.encode("ascii"), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, sig):
                raise ValueError()
            payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
            if payload["purpose"] != "quiz" or payload["expires"] <= self.clock.now().timestamp():
                raise ValueError()
            return payload
        except (ValueError, KeyError, TypeError, UnicodeError):
            raise Forbidden("ลิงก์ไม่ถูกต้องหรือหมดอายุ") from None

    def check_assignment(self, payload, assignment):
        fields = {"assignment_id": "id", "user_id": "user_id", "round_id": "round_id",
                  "version_id": "version_id", "generation": "token_generation"}
        if any(payload.get(k) != assignment[v] for k, v in fields.items()):
            raise Forbidden("ลิงก์ไม่ตรงกับงานที่ได้รับมอบหมาย หรือถูกเพิกถอนแล้ว")
