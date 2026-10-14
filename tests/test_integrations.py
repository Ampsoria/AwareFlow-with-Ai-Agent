from dataclasses import replace
from datetime import timedelta
from email.message import EmailMessage
import json
from pathlib import Path
import smtplib
import tempfile
from types import SimpleNamespace
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import httpx

from awareflow.bootstrap import build, FixedClock
from awareflow.config import Config
from awareflow.demo import START, seed_users, settings
from awareflow.domain.errors import Conflict, Forbidden, ProviderConfigurationError, TransientProviderError
from awareflow.infrastructure.providers import OpenAILLM, TavilySearch, company_adapter
from awareflow.infrastructure.smtp import SmtpTransport
from test_core import FastPdf


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.clock = FixedClock(START)
        self.config = Config(root, root / "db.sqlite3", signing_secret="x" * 48)
        self.s = build(self.config, self.clock, pdf=FastPdf())
        seed_users(self.s, START)
        self.admin = self.s.auth.select_user("ADMIN")

    def publish(self):
        rid = self.s.workflow.create_round(self.admin, settings(), "create")
        self.s.workflow.request_generation(self.admin, rid, 0, "generate")
        self.s.worker.run_once()
        r = self.s.workflow.get_round(self.admin, rid)
        self.s.workflow.approve_version(self.admin, rid, r["current_version_id"], r["lock_version"], "approve")
        r = self.s.workflow.get_round(self.admin, rid)
        assignments = self.s.workflow.publish_round(self.admin, rid, r["current_version_id"], r["lock_version"], "publish")
        return rid, assignments[0]

    def smtp_config(self):
        return replace(self.config, email_real_send=True, email_transport="smtp", smtp_host="smtp.example.invalid", smtp_from="sender@example.invalid",
                       email_allowlist=tuple(f"test{i:03d}@example.invalid" for i in range(1,6)))

    def test_smtp_disabled_and_allowlist_are_enforced(self):
        _, aid = self.publish()
        with self.assertRaises(Forbidden):
            self.s.delivery.prepare_smtp(self.admin, aid, "mail")
        self.s.delivery.config = replace(self.smtp_config(), email_allowlist=())
        with self.assertRaises(Forbidden):
            self.s.delivery.prepare_smtp(self.admin, aid, "mail")

    def test_smtp_accepted_is_idempotent_and_unknown_requires_resolution(self):
        _, aid = self.publish()
        self.s.delivery.config = self.smtp_config()
        outbox_id = self.s.delivery.prepare_smtp(self.admin, aid, "mail")
        self.assertEqual(outbox_id, self.s.delivery.prepare_smtp(self.admin, aid, "mail-again"))
        calls = []
        class Unknown:
            def send(self, message, recipient):
                calls.append(recipient)
                return "UNKNOWN"
        self.s.delivery.transport = Unknown()
        self.assertEqual(self.s.delivery.send_smtp(self.admin, outbox_id), "UNKNOWN")
        with self.assertRaises(Conflict):
            self.s.delivery.send_smtp(self.admin, outbox_id)
        self.assertEqual(len(calls), 1)
        self.s.delivery.resolve_unknown(self.admin, outbox_id, "accepted", "ตรวจ SMTP log แล้ว", "resolve")
        self.assertEqual(self.s.delivery.send_smtp(self.admin, outbox_id), "ACCEPTED")
        self.assertEqual(len(calls), 1)

    def test_expired_smtp_lease_becomes_unknown_on_refresh(self):
        rid, aid = self.publish()
        self.s.delivery.config = self.smtp_config()
        outbox_id = self.s.delivery.prepare_smtp(self.admin, aid, "mail")
        with self.s.db.transaction() as db:
            db.execute("UPDATE email_outbox SET status='SENDING',lease_expires_at='2000-01-01T00:00:00Z' WHERE id=?", (outbox_id,))
        result = self.s.delivery.list_outbox(self.admin, rid)
        self.assertEqual(next(r["status"] for r in result if r["id"] == outbox_id), "UNKNOWN")

    def test_smtp_disconnect_during_data_and_quit_failure(self):
        config = self.smtp_config()
        message = EmailMessage()
        message["From"], message["To"] = config.smtp_from, "test001@example.invalid"
        message.set_content("test")
        class FakeSMTP:
            def __init__(self, *args, **kwargs): pass
            def ehlo(self): pass
            def starttls(self, **kwargs): pass
            def send_message(self, *args, **kwargs): raise smtplib.SMTPServerDisconnected()
            def quit(self): raise OSError()
        with patch("smtplib.SMTP", FakeSMTP):
            self.assertEqual(SmtpTransport(config).send(message, message["To"]), "UNKNOWN")
        class AcceptedSMTP(FakeSMTP):
            def send_message(self, *args, **kwargs): return {}
        with patch("smtplib.SMTP", AcceptedSMTP):
            self.assertEqual(SmtpTransport(config).send(message, message["To"]), "ACCEPTED")

    def test_openai_uses_responses_schema_no_sdk_retries_or_personal_data(self):
        calls = []
        bundle = self.s.worker.llm.generate(settings(), self.s.worker.search.search(settings()))
        class Responses:
            def parse(self, **kwargs):
                calls.append(kwargs)
                return SimpleNamespace(output_parsed=bundle)
        provider = OpenAILLM(replace(self.config, llm_model="configured-model"), self.clock, SimpleNamespace(responses=Responses()))
        result = provider.generate(settings(), self.s.worker.search.search(settings()))
        self.assertEqual(calls[0]["model"], "configured-model")
        self.assertFalse(calls[0]["store"])
        self.assertIn("text_format", calls[0])
        self.assertTrue(result.provenance.is_demo)
        self.assertNotIn("TEST001", calls[0]["input"][1]["content"])

    def test_search_dates_are_estimates_and_domain_filter_is_strict(self):
        seen = []
        def handler(request):
            seen.append(json.loads(request.content))
            return httpx.Response(200, json={"results": [
                {"url": "https://cisa.gov/a", "title": "A", "content": "Evidence", "published_date": "Tue, 11 Mar 2025 17:00:00 GMT"},
                {"url": "https://cisa.gov.evil.invalid/a", "content": "Wrong host"}]})
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            search = TavilySearch(replace(self.config, search_key="test-key"), self.clock, client)
            result = search.search(settings())
        self.assertEqual(len(result), 1)
        self.assertIsNone(result[0].published_at)
        self.assertIsNone(result[0].event_at)
        self.assertIsNotNone(result[0].provider_date)
        self.assertFalse(seen[0]["include_answer"])

    def test_search_transient_error(self):
        with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(429))) as client:
            with self.assertRaises(TransientProviderError):
                TavilySearch(self.config, self.clock, client).search(settings())

    def test_transient_job_has_backoff_and_capped_retries(self):
        rid = self.s.workflow.create_round(self.admin, settings(), "create")
        self.s.workflow.request_generation(self.admin, rid, 0, "gen")
        class Unavailable:
            def search(self, _): raise TransientProviderError()
        self.s.worker.search = Unavailable()
        for i in range(3):
            result = self.s.worker.run_once()
            self.assertEqual(result["status"], "QUEUED" if i < 2 else "FAILED")
            if i < 2:
                self.assertIsNone(self.s.worker.run_once())
                self.clock.value += timedelta(seconds=70)

    def test_company_missing_factory_and_dataset_separation(self):
        with self.assertRaises(ProviderConfigurationError):
            company_adapter(replace(self.config, company_factory="missing.module:factory"), self.clock)
        with self.assertRaises(ProviderConfigurationError):
            build(replace(self.config, search_provider="tavily", search_key="test"), self.clock)

    def test_malformed_ai_summary_falls_back_without_changing_metrics(self):
        rid, _ = self.publish()
        class BadSummary:
            def summarize(self, metrics): return {"text": "ผ่าน 999 คน"}
        self.s.reporting.llm = BadSummary()
        exported = self.s.reporting.export(self.admin, "report", round_id=rid)
        self.assertEqual(exported["metrics"]["summary_source"], "template")
        self.assertNotIn("999", exported["metrics"]["summary"])
        self.assertEqual(exported["metrics"]["passed"], 0)

    def test_two_process_like_bootstraps_share_migrations_and_secret(self):
        root = self.config.data_root / "fresh"
        config = Config(root, root / "db.sqlite3")
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: build(config, self.clock, pdf=FastPdf()), range(2)))
        self.assertEqual(results[0].workflow.links.secret, results[1].workflow.links.secret)
        with results[0].db.read() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0], 4)

    def test_company_factory_has_distinct_credentials_and_contract(self):
        import sys
        import types
        captured = {}
        module = types.ModuleType("company_test_adapter")
        def factory(**kwargs):
            captured.update(kwargs)
            return self.s.worker.llm
        module.factory = factory
        with patch.dict(sys.modules, {"company_test_adapter": module}):
            adapter = company_adapter(replace(self.config, company_factory="company_test_adapter:factory",
                company_endpoint="https://company.example.invalid/generate", company_credential="company-test"), self.clock)
        self.assertEqual(captured["credential"], "company-test")
        self.assertIs(adapter, self.s.worker.llm)


if __name__ == "__main__":
    unittest.main()
