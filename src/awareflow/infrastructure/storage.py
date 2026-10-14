import hashlib
import os
from pathlib import Path
import tempfile

from awareflow.domain.errors import ValidationError


class FileStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key):
        path = (self.root / key).resolve()
        if self.root not in path.parents:
            raise ValidationError("ที่อยู่ไฟล์ไม่ถูกต้อง")
        return path

    def put(self, data: bytes, suffix: str):
        if suffix not in {"pdf", "eml", "csv", "json"}:
            raise ValidationError("ไม่รองรับชนิดไฟล์นี้")
        sha = hashlib.sha256(data).hexdigest()
        key = "artifacts/" + sha + "." + suffix
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
        return key, sha

    def read(self, key):
        try:
            return self._path(key).read_bytes()
        except OSError:
            raise ValidationError("ไม่พบไฟล์ประกอบ กรุณาสร้างใหม่หรือตรวจที่เก็บไฟล์") from None

    def read_verified(self, key, sha):
        data = self.read(key)
        if hashlib.sha256(data).hexdigest() != sha:
            raise ValidationError("ไฟล์ไม่ตรงกับเวอร์ชันที่บันทึก")
        return data
