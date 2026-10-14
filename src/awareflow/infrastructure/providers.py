"""Network adapters. Only explicit provider selection performs outbound requests."""
import hashlib
import importlib
import json
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import httpx

from awareflow.domain.errors import ProviderConfigurationError, ProviderUnavailable, TransientProviderError
from awareflow.domain.models import SourceRecord, utc
from awareflow.domain.agent import AGENT_INSTRUCTIONS, function_tools


# Re-export these names for existing reporting/integration imports.
from .llm_base import StructuredLLM, QuerySuggestions, Narrative


class OpenAILLM(StructuredLLM):
    provider_name = "openai"
    def __init__(self, config, clock, client=None):
        from openai import OpenAI
        self.model, self.clock = config.llm_model, clock
        self.search_provider = config.search_provider
        self.max_output_tokens = config.llm_max_output_tokens
        self.client = client or OpenAI(api_key=config.llm_key, base_url=config.llm_endpoint or None,
                                       timeout=config.provider_timeout, max_retries=0)

    def _parse(self, schema, system, data):
        from openai import APITimeoutError, APIConnectionError, RateLimitError, APIStatusError
        try:
            response = self.client.responses.parse(model=self.model, store=False,
                input=[{"role": "system", "content": system}, {"role": "user", "content": json.dumps(data, ensure_ascii=False)}],
                text_format=schema, max_output_tokens=self.max_output_tokens)
            if response.output_parsed is None:
                raise ProviderUnavailable("โมเดลไม่คืนข้อมูลที่ตรวจโครงสร้างได้")
            return response.output_parsed
        except (APITimeoutError, APIConnectionError, RateLimitError):
            raise TransientProviderError("บริการโมเดลไม่พร้อมชั่วคราว") from None
        except APIStatusError as exc:
            if exc.status_code >= 500:
                raise TransientProviderError("บริการโมเดลไม่พร้อมชั่วคราว") from None
            raise ProviderUnavailable("เรียกโมเดลไม่สำเร็จ ตรวจ model/credentials/endpoint") from None

    def decide_agent(self, context):
        """One stateless decision using native function calling; state includes prior tool observations.

        Never share conversation state on this cached provider object: jobs may run concurrently.
        """
        from openai import APITimeoutError, APIConnectionError, RateLimitError, APIStatusError
        try:
            response = self.client.responses.create(
                model=self.model, store=False, max_output_tokens=min(2000, self.max_output_tokens),
                input=[{"role": "system", "content": AGENT_INSTRUCTIONS},
                       {"role": "user", "content": json.dumps(context, ensure_ascii=False)}],
                tools=function_tools(), tool_choice="required", parallel_tool_calls=False)
            calls = [item for item in response.output if item.type == "function_call"]
            if response.status != "completed" or len(calls) != 1:
                raise ProviderUnavailable("Agent ต้องคืน function call ที่สมบูรณ์หนึ่งรายการ")
            try:
                arguments = json.loads(calls[0].arguments)
            except (TypeError, ValueError):
                raise ProviderUnavailable("Agent คืน arguments ที่อ่านไม่ได้") from None
            return {"name": calls[0].name, "arguments": arguments}
        except (APITimeoutError, APIConnectionError, RateLimitError):
            raise TransientProviderError("บริการ Agent ไม่พร้อมชั่วคราว") from None
        except APIStatusError as exc:
            if exc.status_code >= 500:
                raise TransientProviderError("บริการ Agent ไม่พร้อมชั่วคราว") from None
            raise ProviderUnavailable("เรียก Agent ไม่สำเร็จ ตรวจ model/function calling/credentials") from None



class TavilySearch:
    def __init__(self, config, clock, client=None):
        self.config, self.clock = config, clock
        self.client = client or httpx.Client(timeout=config.provider_timeout, follow_redirects=False)

    def search(self, settings):
        sources = {}
        for query in settings.queries[:5]:
            request = {"query": query, "topic": "news" if settings.content_mode == "recent" else "general",
                       "max_results": 5, "include_domains": [d.hostname for d in settings.allowed_domains],
                       "include_answer": False, "include_raw_content": False}
            if settings.content_mode == "recent":
                request.update(start_date=str(settings.search_window.start_date), end_date=str(settings.search_window.end_date))
            try:
                response = self.client.post(self.config.search_endpoint, json=request,
                    headers={"Authorization": "Bearer " + self.config.search_key})
            except (httpx.TimeoutException, httpx.NetworkError):
                raise TransientProviderError("บริการค้นข้อมูลไม่พร้อมชั่วคราว") from None
            if response.status_code == 429 or response.status_code >= 500:
                raise TransientProviderError("บริการค้นข้อมูลไม่พร้อมชั่วคราว")
            if response.status_code != 200:
                raise ProviderUnavailable("ค้นข้อมูลไม่สำเร็จ ตรวจ SEARCH_API_KEY/endpoint")
            try:
                results = response.json()["results"]
            except (ValueError, KeyError):
                raise ProviderUnavailable("ผลค้นไม่ตรงรูปแบบที่รองรับ") from None
            for item in results:
                url = item.get("url", "")
                parts = urlsplit(url)
                if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
                    continue
                host = parts.hostname.lower().rstrip(".")
                if not any(host == d.hostname or (d.include_subdomains and host.endswith("." + d.hostname)) for d in settings.allowed_domains):
                    continue
                excerpt = (item.get("content") or "")[:2000]
                if not excerpt:
                    continue
                published = item.get("published_date")
                try:
                    if published:
                        datetime.fromisoformat(published.replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    try:
                        published = parsedate_to_datetime(published).isoformat()
                    except (ValueError, TypeError, AttributeError):
                        published = None
                # No target-site fetching: results are consumed as search excerpts only.
                # updated_at/retrieved_at are never substituted for publication/event dates.
                source_id = "src-" + hashlib.sha256(url.encode()).hexdigest()[:16]
                sources[url] = SourceRecord(source_id=source_id, url=url, title=item.get("title") or host,
                    publisher=host, provider_date=published, retrieved_at=utc(self.clock.now()), excerpt=excerpt,
                    content_hash=hashlib.sha256(excerpt.encode()).hexdigest(), provenance="live",
                    eligibility_reason="Provider date may mean publication OR update; not a verified publication/event date. Suitable for evergreen review only.")
        return list(sources.values())[:3]


def company_adapter(config, clock):
    # Server-owned import path; never accept this setting from a web form.
    try:
        module, factory = config.company_factory.split(":", 1)
        adapter = getattr(importlib.import_module(module), factory)(
            endpoint=config.company_endpoint, credential=config.company_credential,
            model=config.llm_model, timeout=config.provider_timeout, clock=clock)
        if not all(callable(getattr(adapter, name, None)) for name in ("generate", "suggest_queries", "decide_agent")):
            raise TypeError()
        return adapter
    except Exception:
        raise ProviderConfigurationError("โหลด company adapter ไม่สำเร็จ ตรวจ factory และสเปกของบริษัท") from None


def create_llm(config, clock):
    """Single registry used by the UI, worker and connection check."""
    from .mock import MockLLM
    from .http_llm import OpenAICompatibleLLM, OllamaLLM, AnthropicLLM, GeminiLLM
    providers = {'openai': OpenAILLM, 'openai_compatible': OpenAICompatibleLLM,
                 'ollama': OllamaLLM, 'anthropic': AnthropicLLM, 'gemini': GeminiLLM}
    if config.llm_provider == 'mock':
        return MockLLM(clock)
    if config.llm_provider == 'company':
        return company_adapter(config, clock)
    return providers[config.llm_provider](config, clock)
