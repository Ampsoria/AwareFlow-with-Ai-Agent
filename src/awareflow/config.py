from dataclasses import dataclass, field
import os
import ipaddress
import re
from pathlib import Path
from urllib.parse import urlsplit

from awareflow.domain.errors import ProviderConfigurationError

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Config:
    data_root: Path
    database_path: Path
    base_url: str = "http://localhost:8501"
    mode: str = "demo"
    signing_secret: str = field(default="", repr=False)
    link_ttl_hours: int = 168
    max_attempts: int = 3
    lease_seconds: int = 120
    llm_provider: str = "mock"
    llm_model: str = ""
    llm_endpoint: str = ""
    llm_key: str = field(default="", repr=False)
    llm_output_mode: str = "json"
    llm_max_output_tokens: int = 6000
    llm_allow_http: bool = False
    company_factory: str = ""
    company_endpoint: str = ""
    company_credential: str = field(default="", repr=False)
    search_provider: str = "mock"
    search_endpoint: str = "https://api.tavily.com/search"
    search_key: str = field(default="", repr=False)
    email_real_send: bool = False
    email_transport: str = "preview"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_tls: str = "starttls"
    smtp_username: str = ""
    smtp_password: str = field(default="", repr=False)
    smtp_from: str = ""
    email_allowlist: tuple = ()
    provider_timeout: int = 45
    agent_max_steps: int = 12
    agent_max_searches: int = 5
    agent_max_drafts: int = 3

    @property
    def data_class(self):
        return "demo" if self.llm_provider == self.search_provider == "mock" else "connected"

    @classmethod
    def from_env(cls):
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env", override=False)
        supported = {"APP_MODE": "demo", "AUTH_PROVIDER": "demo_selector", "APP_TIMEZONE": "Asia/Bangkok"}
        for key, expected in supported.items():
            if os.getenv(key, expected) != expected:
                raise ProviderConfigurationError(key + " ยังไม่รองรับใน core รุ่นนี้")
        llm = os.getenv("LLM_PROVIDER", "mock")
        provider_key = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "gemini": "GEMINI_API_KEY"}.get(llm)
        llm_key = os.getenv("LLM_API_KEY") or (os.getenv(provider_key, "") if provider_key else "")
        search = os.getenv("SEARCH_PROVIDER", "mock")
        data_class = "demo" if llm == search == "mock" else "connected"
        root = Path(os.getenv("DATA_ROOT", str(ROOT / "var" / data_class))).resolve()
        try:
            result = cls(root, Path(os.getenv("DATABASE_PATH", str(root / "awareflow.sqlite3"))).resolve(),
                         base_url=os.getenv("APP_BASE_URL", "http://localhost:8501"),
                         signing_secret=os.getenv("APP_SIGNING_SECRET", ""),
                         link_ttl_hours=int(os.getenv("LINK_TTL_HOURS", "168")),
                         max_attempts=int(os.getenv("JOB_MAX_ATTEMPTS", "3")),
                         lease_seconds=int(os.getenv("JOB_TIMEOUT_SECONDS", "120")),
                         llm_provider=llm, llm_model=os.getenv("LLM_MODEL", ""),
                         llm_endpoint=os.getenv("LLM_ENDPOINT", ""), llm_key=llm_key,
                         llm_output_mode=os.getenv("LLM_OUTPUT_MODE", "json"),
                         llm_max_output_tokens=int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "6000")),
                         llm_allow_http=os.getenv("LLM_ALLOW_INSECURE_HTTP", "false").lower() == "true",
                         company_factory=os.getenv("COMPANY_ADAPTER_FACTORY", ""),
                         company_endpoint=os.getenv("COMPANY_API_ENDPOINT", ""),
                         company_credential=os.getenv(os.getenv("COMPANY_CREDENTIAL_ENV", "COMPANY_API_TOKEN"), ""),
                         search_provider=search, search_endpoint=os.getenv("SEARCH_ENDPOINT") or "https://api.tavily.com/search",
                         search_key=os.getenv("SEARCH_API_KEY", ""),
                         email_real_send=os.getenv("EMAIL_REAL_SEND_ENABLED", "false").lower() == "true",
                         email_transport=os.getenv("EMAIL_TRANSPORT", "preview"),
                         smtp_host=os.getenv("SMTP_HOST", ""), smtp_port=int(os.getenv("SMTP_PORT", "587")),
                         smtp_tls=os.getenv("SMTP_TLS_MODE", "starttls"), smtp_username=os.getenv("SMTP_USERNAME", ""),
                         smtp_password=os.getenv("SMTP_PASSWORD", ""), smtp_from=os.getenv("SMTP_FROM", ""),
                         email_allowlist=tuple(e.strip().lower() for e in os.getenv("EMAIL_TEST_ALLOWLIST", "").split(",") if e.strip()),
                         provider_timeout=int(os.getenv("PROVIDER_TIMEOUT_SECONDS", "45")),
                         agent_max_steps=int(os.getenv("AGENT_MAX_STEPS", "12")),
                         agent_max_searches=int(os.getenv("AGENT_MAX_SEARCHES", "5")),
                         agent_max_drafts=int(os.getenv("AGENT_MAX_DRAFTS", "3")))
        except ValueError:
            raise ProviderConfigurationError("ค่าตั้งเวลา/จำนวนครั้งต้องเป็นจำนวนเต็ม") from None
        result.validate()
        return result

    def validate(self):
        if self.mode != "demo":
            raise ProviderConfigurationError("ยังไม่มี authentication สำหรับโหมดจริง")
        if self.llm_provider not in {"mock", "openai", "openai_compatible", "ollama", "anthropic", "gemini", "company"} or self.search_provider not in {"mock", "tavily"}:
            raise ProviderConfigurationError("provider ไม่รองรับ")
        if self.llm_provider == "openai" and (not self.llm_key or not self.llm_model):
            raise ProviderConfigurationError("ต้องตั้ง OPENAI_API_KEY หรือ LLM_API_KEY พร้อม LLM_MODEL ฝั่งเซิร์ฟเวอร์")
        if self.llm_provider in {"anthropic", "gemini"} and not self.llm_key:
            raise ProviderConfigurationError("ต้องตั้ง LLM_API_KEY หรือ key ของ provider ที่เลือก")
        if self.llm_provider not in {"mock", "company"} and not self.llm_model.strip():
            raise ProviderConfigurationError("ต้องตั้ง LLM_MODEL เป็นชื่อโมเดลของบริการที่เลือก")
        if self.llm_provider == "openai_compatible" and not self.llm_endpoint:
            raise ProviderConfigurationError("OpenAI-compatible ต้องตั้ง LLM_ENDPOINT เป็น API base URL เช่น http://127.0.0.1:1234/v1")
        if self.llm_provider == "gemini" and not re.fullmatch(r"[A-Za-z0-9._-]+", self.llm_model):
            raise ProviderConfigurationError("Gemini LLM_MODEL ใช้ชื่อโมเดลโดยไม่ใส่ models/ หรือ URL")
        if self.llm_output_mode not in {"json_schema", "json", "text"}:
            raise ProviderConfigurationError("LLM_OUTPUT_MODE ต้องเป็น json_schema, json หรือ text")
        if not 256 <= self.llm_max_output_tokens <= 32768:
            raise ProviderConfigurationError("LLM_MAX_OUTPUT_TOKENS ต้องอยู่ในช่วง 256-32768")
        if self.llm_provider == "company" and not self.company_factory:
            raise ProviderConfigurationError("ต้องระบุ COMPANY_ADAPTER_FACTORY ตามสเปก API บริษัท")
        if self.search_provider == "tavily" and not self.search_key:
            raise ProviderConfigurationError("ต้องตั้ง SEARCH_API_KEY ฝั่งเซิร์ฟเวอร์")
        self._validate_endpoint(self.llm_endpoint, local_http=True)
        self._validate_endpoint(self.company_endpoint, local_http=True)
        self._validate_endpoint(self.search_endpoint)
        if self.email_transport not in {"preview", "smtp"}:
            raise ProviderConfigurationError("EMAIL_TRANSPORT ต้องเป็น preview หรือ smtp")
        if self.smtp_tls not in {"starttls", "ssl"} or not 1 <= self.smtp_port <= 65535:
            raise ProviderConfigurationError("SMTP ต้องใช้ starttls หรือ ssl และ port ที่ถูกต้อง")
        if not 1 <= self.provider_timeout <= 300:
            raise ProviderConfigurationError("PROVIDER_TIMEOUT_SECONDS ต้องอยู่ในช่วง 1-300")
        if not (4 <= self.agent_max_steps <= 30 and 1 <= self.agent_max_searches <= 10 and 1 <= self.agent_max_drafts <= 5):
            raise ProviderConfigurationError("งบ Agent: steps 4-30, searches 1-10, drafts 1-5")
        url = urlsplit(self.base_url)
        if url.scheme not in {"http", "https"} or not url.netloc or url.username or url.password or url.query or url.fragment:
            raise ProviderConfigurationError("APP_BASE_URL ต้องเป็น HTTP(S) URL ไม่มี credentials/query/fragment")
        if not (1 <= self.max_attempts <= 10 and 1 <= self.link_ttl_hours <= 8760 and 1 <= self.lease_seconds <= 3600):
            raise ProviderConfigurationError("ค่าตั้งเวลา/จำนวนครั้งอยู่นอกขอบเขต")

    def _validate_endpoint(self, endpoint, local_http=False):
        if not endpoint:
            return
        try:
            url = urlsplit(endpoint)
            host, port = url.hostname, url.port
            try:
                loopback = ipaddress.ip_address(host).is_loopback
            except ValueError:
                loopback = host == "localhost"
            http_allowed = local_http and (loopback or self.llm_allow_http)
            if (not host or url.username or url.password or url.query or url.fragment
                    or any(c.isspace() for c in endpoint)
                    or (url.scheme != "https" and not (url.scheme == "http" and http_allowed))):
                raise ValueError()
        except (ValueError, TypeError):
            raise ProviderConfigurationError("API endpoint ต้องเป็น HTTPS หรือ HTTP บน localhost; HTTP ภายในเครือข่ายต้องเปิด LLM_ALLOW_INSECURE_HTTP และ URL ห้ามมี credentials/query/fragment") from None
