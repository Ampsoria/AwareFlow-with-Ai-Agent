from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Literal, Optional
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator, model_serializer

from .errors import ValidationError


def utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValidationError("เวลาต้องมีเขตเวลากำกับ")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def instant(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass(frozen=True)
class Actor:
    # Only an AuthProvider constructs this from a trusted session.
    user_id: str


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class DomainRule(Model):
    hostname: str = Field(min_length=1, max_length=253)
    include_subdomains: bool = False

    @field_validator("hostname")
    @classmethod
    def hostname_only(cls, value):
        value = value.lower().rstrip(".")
        if any(c in value for c in "/:@ ") or "." not in value:
            raise ValueError("ต้องระบุ hostname เช่น example.org")
        return value


class SearchWindow(Model):
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def ordered(self):
        if self.start_date > self.end_date:
            raise ValueError("ช่วงวันที่ไม่ถูกต้อง")
        return self


class CompanyContext(Model):
    policy_text: Optional[str] = Field(default=None, max_length=4000)
    reporting_channel: Optional[str] = Field(default=None, max_length=500)


class RoundSettings(Model):
    schema_version: Literal[1] = 1
    title: str = Field(min_length=1, max_length=200)
    topic: str = Field(min_length=1, max_length=200)
    agent_goal: Optional[str] = Field(default=None, min_length=1, max_length=2000)
    queries: list[str] = Field(min_length=1, max_length=10)
    search_window: SearchWindow
    allowed_domains: list[DomainRule] = Field(min_length=1, max_length=30)
    target_group_ids: list[str] = Field(min_length=1, max_length=100)
    question_count: int = Field(default=5, ge=3, le=5, strict=True)
    pass_percent: int = Field(default=80, ge=1, le=100, strict=True)
    due_at: datetime
    allow_retakes: bool = False
    company_context: CompanyContext = Field(default_factory=CompanyContext)
    content_mode: Literal["recent", "evergreen"] = "recent"

    @model_serializer(mode="wrap")
    def compatible_snapshot(self, handler):
        data = handler(self)
        # Preserve pre-agent command hashes/snapshots when no new goal was supplied.
        if self.agent_goal is None:
            data.pop("agent_goal", None)
        return data

    @field_validator("due_at")
    @classmethod
    def aware(cls, value):
        utc(value)
        return value.astimezone(timezone.utc)

    @field_validator("queries", "target_group_ids")
    @classmethod
    def nonempty_unique(cls, values):
        if any(not v.strip() or len(v) > 500 for v in values) or len(set(values)) != len(values):
            raise ValueError("รายการต้องไม่ว่างหรือซ้ำ")
        return values


class SourceRecord(Model):
    source_id: str
    url: Optional[str] = None
    title: str
    publisher: str
    published_at: Optional[str] = None
    event_at: Optional[str] = None
    updated_at: Optional[str] = None
    provider_date: Optional[str] = None
    retrieved_at: str
    excerpt: str = Field(max_length=8000)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    provenance: Literal["mock", "live"]
    eligibility_reason: str

    @model_validator(mode="after")
    def valid_url(self):
        if self.provenance == "live" and not self.url:
            raise ValueError("แหล่งจริงต้องมี URL")
        if self.url and urlsplit(self.url).scheme not in ("https", "http"):
            raise ValueError("source URL ต้องเป็น HTTP(S)")
        return self


class Section(Model):
    section_id: str
    heading: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=2000)
    source_ids: list[str] = Field(min_length=1)


class Action(Model):
    text: str = Field(min_length=1, max_length=500)
    source_ids: list[str] = Field(min_length=1)


class Option(Model):
    option_id: Literal["A", "B", "C", "D"]
    text: str = Field(min_length=1, max_length=500)


class Question(Model):
    question_id: str = Field(min_length=1, max_length=100)
    prompt: str = Field(min_length=1, max_length=1000)
    options: list[Option] = Field(min_length=4, max_length=4)
    correct_option_id: Literal["A", "B", "C", "D"]
    explanation: str = Field(min_length=1, max_length=1000)
    supporting_section_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def four_choices(self):
        if {o.option_id for o in self.options} != set("ABCD") or len({o.text for o in self.options}) != 4:
            raise ValueError("ตัวเลือกต้องมี A-D และข้อความไม่ซ้ำ")
        return self


class Provenance(Model):
    llm_provider: str
    model: str
    search_provider: str
    is_demo: bool
    generated_at: str


class ContentBundle(Model):
    schema_version: Literal[1] = 1
    language: Literal["th"] = "th"
    title: str = Field(min_length=1, max_length=200)
    scenario: str = Field(min_length=1, max_length=1000)
    sections: list[Section] = Field(min_length=1, max_length=6)
    employee_actions: list[Action] = Field(min_length=1, max_length=6)
    questions: list[Question] = Field(min_length=3, max_length=5)
    source_ids: list[str] = Field(min_length=1)
    change_summary: Optional[str] = Field(default=None, max_length=2000)
    provenance: Provenance

    def validate_against(self, settings: RoundSettings, sources: list[SourceRecord]):
        section_ids = {s.section_id for s in self.sections}
        source_ids = {s.source_id for s in sources}
        if len(source_ids) != len(sources) or len(section_ids) != len(self.sections):
            raise ValidationError("รหัสแหล่งข้อมูลหรือหัวข้อซ้ำ")
        if len(self.questions) != settings.question_count or len({q.question_id for q in self.questions}) != len(self.questions):
            raise ValidationError("จำนวนข้อสอบหรือรหัสข้อสอบไม่ถูกต้อง")
        if set(self.source_ids) != source_ids:
            raise ValidationError("แหล่งอ้างอิงไม่ตรงกับผลค้น")
        for block in [*self.sections, *self.employee_actions]:
            if not set(block.source_ids) <= source_ids:
                raise ValidationError("อ้างอิงแหล่งข้อมูลที่ไม่มีอยู่")
        for question in self.questions:
            if not set(question.supporting_section_ids) <= section_ids:
                raise ValidationError("เฉลยไม่อ้างอิงจุดในบทอ่าน")
        if any(s.provenance == "mock" for s in sources) and not self.provenance.is_demo:
            raise ValidationError("ต้องติดป้ายข้อมูลสาธิต")
        return self
