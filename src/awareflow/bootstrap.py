from dataclasses import dataclass
from datetime import datetime, timezone
import os
import secrets
import json
import tempfile

from .application.delivery import Delivery
from .application.directory import Directory
from .application.jobs import GenerationWorker
from .application.quizzes import Quizzes
from .application.reporting import Reporting
from .application.scheduling import Scheduling
from .application.workflow import Workflow
from .config import Config, ROOT
from .infrastructure.auth import DemoAuth, SignedLinks
from .infrastructure.database import Database
from .infrastructure.mock import MockLLM, MockSearch
from .infrastructure.pdf import ThaiPdf
from .infrastructure.storage import FileStore
from .infrastructure.providers import TavilySearch, create_llm
from .domain.errors import ProviderConfigurationError


class Clock:
    def now(self):
        return datetime.now(timezone.utc)


class FixedClock:
    """Mutable clock for fixtures/tests only. Never use it for live submissions."""
    def __init__(self, value):
        self.value = value

    def now(self):
        return self.value


@dataclass
class Services:
    config: Config
    db: Database
    auth: DemoAuth
    workflow: Workflow
    worker: GenerationWorker
    quizzes: Quizzes
    reporting: Reporting
    delivery: Delivery
    files: FileStore
    scheduling: Scheduling
    directory: Directory


def build(config=None, clock=None, search=None, llm=None, pdf=None):
    config, clock = config or Config.from_env(), clock or Clock()
    config.validate()
    config.data_root.mkdir(parents=True, exist_ok=True)
    secret = config.signing_secret
    if not secret:
        path = config.data_root / ".signing-secret"
        if path.exists():
            secret = path.read_text(encoding="ascii").strip()
        else:
            secret = secrets.token_urlsafe(48)
            fd, temporary = tempfile.mkstemp(dir=config.data_root)
            try:
                with os.fdopen(fd, "w") as stream:
                    stream.write(secret)
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    os.link(temporary, path)  # Publish only a complete secret; never replace another process's key.
                except FileExistsError:
                    secret = path.read_text(encoding="ascii").strip()
            finally:
                os.unlink(temporary)
    database = Database(config.database_path, ROOT / "db/migrations")
    database.migrate()
    with database.transaction() as db:
        existing = db.execute("SELECT value_json FROM app_settings WHERE key='data_class'").fetchone()
        if existing and json.loads(existing[0]) != config.data_class:
            raise ProviderConfigurationError("ห้ามรวมข้อมูล mock กับ connected: เปลี่ยน DATA_ROOT และ DATABASE_PATH เป็นชุดใหม่")
        if not existing:
            has_old_mock = db.execute("SELECT 1 FROM training_versions WHERE bundle_json LIKE '%\"is_demo\":true%' LIMIT 1").fetchone()
            if has_old_mock and config.data_class != "demo":
                raise ProviderConfigurationError("ฐานนี้มีข้อมูลสาธิตอยู่แล้ว กรุณาใช้ฐาน connected ใหม่")
            db.execute("INSERT OR IGNORE INTO app_settings VALUES ('data_class',?)", (json.dumps(config.data_class),))
    files = FileStore(config.data_root)
    links = SignedLinks(secret, clock, config.link_ttl_hours)
    renderer = pdf or ThaiPdf(ROOT / "assets/fonts")
    if llm is None:
        llm = create_llm(config, clock)
    if search is None:
        search = MockSearch(clock) if config.search_provider == "mock" else TavilySearch(config, clock)
    workflow = Workflow(database, clock, files, links, config.base_url, config.max_attempts)
    reporting = Reporting(database, clock, files, renderer, llm, config.llm_provider)
    return Services(config, database, DemoAuth(database, config.mode), workflow,
                    GenerationWorker(database, clock, search, llm,
                                     renderer, files, config.lease_seconds,
                                     config.agent_max_steps, config.agent_max_searches, config.agent_max_drafts),
                    Quizzes(database, clock, links, files), reporting,
                    Delivery(database, clock, files, links, config), files,
                    Scheduling(database, clock, workflow, reporting), Directory(database, clock))
