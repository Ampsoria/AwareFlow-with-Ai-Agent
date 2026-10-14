from typing import Optional, Protocol

from awareflow.domain.models import ContentBundle, RoundSettings, SourceRecord


class SearchProvider(Protocol):
    def search(self, settings: RoundSettings) -> list[SourceRecord]: ...


class LLMProvider(Protocol):
    def decide_agent(self, context: dict) -> dict: ...
    def generate(self, settings: RoundSettings, sources: list[SourceRecord],
                 previous: Optional[ContentBundle] = None, feedback: Optional[str] = None) -> ContentBundle: ...


class PdfRenderer(Protocol):
    def lesson(self, bundle: ContentBundle, sources: list[SourceRecord]) -> bytes: ...
    def report(self, metrics: dict) -> bytes: ...
