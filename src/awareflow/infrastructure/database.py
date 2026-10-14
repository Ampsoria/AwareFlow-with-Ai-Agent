"""Small unit of work: a new SQLite connection per operation, no global connection."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3

from awareflow.domain.errors import NotFound


def one(db, sql, params=()):
    row = db.execute(sql, params).fetchone()
    if row is None:
        raise NotFound("ไม่พบข้อมูล")
    return dict(row)


def rows(db, sql, params=()):
    return [dict(r) for r in db.execute(sql, params).fetchall()]


class Database:
    def __init__(self, path: Path, migrations: Path):
        self.path = Path(path)
        self.migrations = migrations
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self):
        db = sqlite3.connect(str(self.path), timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=10000")
        return db

    def migrate(self):
        with self.read() as db:
            db.execute("PRAGMA journal_mode=WAL")
            for path in sorted(self.migrations.glob("*.sql")):
                # Re-check under the write lock, so UI/worker startup cannot migrate twice.
                db.execute("BEGIN IMMEDIATE")
                try:
                    exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='schema_migrations'").fetchone()
                    version = int(path.name.split("_", 1)[0])
                    applied = exists and db.execute("SELECT 1 FROM schema_migrations WHERE version=?", (version,)).fetchone()
                    if not applied:
                        statement = ""
                        for line in path.read_text(encoding="utf-8").splitlines():
                            if line.lstrip().startswith("--"):
                                continue
                            statement += line + "\n"
                            if sqlite3.complete_statement(statement):
                                normalized = statement.strip().upper()
                                if normalized not in {"BEGIN;", "BEGIN IMMEDIATE;", "COMMIT;", "PRAGMA FOREIGN_KEYS = ON;"}:
                                    db.execute(statement)
                                statement = ""
                        if statement.strip():
                            raise ValueError("Incomplete migration statement")
                    db.commit()
                except Exception:
                    db.rollback()
                    raise

    @contextmanager
    def read(self):
        db = self.connect()
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def transaction(self):
        with self.read() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise
