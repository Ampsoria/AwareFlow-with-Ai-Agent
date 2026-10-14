"""Repeatable demonstration. Historical fixture timestamps are explicitly synthetic."""
from datetime import datetime, timedelta, timezone

from .bootstrap import FixedClock, build
from .domain.models import RoundSettings, utc, instant
from .domain.errors import ProviderConfigurationError


START = datetime(2026, 10, 1, 3, tzinfo=timezone.utc)


def seed_users(services, now):
    with services.db.transaction() as db:
        db.execute("INSERT OR IGNORE INTO users(id, display_name, role, created_at) VALUES ('ADMIN','ผู้ดูแลสาธิต','admin',?)", (utc(now),))
        db.execute("INSERT OR IGNORE INTO user_groups(id,name) VALUES ('DEMO','กลุ่มสาธิต')")
        for n in range(1, 6):
            user_id = f"TEST{n:03d}"
            db.execute("""INSERT OR IGNORE INTO users(id,display_name,email,role,created_at)
                VALUES (?, ?, ?, 'learner', ?)""", (user_id, "พนักงานสาธิต " + str(n), user_id.lower() + "@example.invalid", utc(now)))
            db.execute("INSERT OR IGNORE INTO group_memberships VALUES ('DEMO',?)", (user_id,))


def settings(title="รู้ทันข้อความหลอกลวง / ข้อมูลสาธิต", retakes=True):
    return RoundSettings.model_validate({
        "title": title, "topic": "Phishing", "queries": ["phishing awareness"],
        "search_window": {"start_date": "2026-07-01", "end_date": "2026-10-01"},
        "allowed_domains": [{"hostname": "cisa.gov"}], "target_group_ids": ["DEMO"],
        "question_count": 5, "pass_percent": 80, "due_at": START + timedelta(days=10),
        "allow_retakes": retakes, "content_mode": "evergreen",
    })


def run_demo(config, start=START, prefix="demo-v1"):
    if config.data_class != "demo":
        raise ProviderConfigurationError("โหลด fixture ได้เฉพาะชุดข้อมูล mock")
    clock = FixedClock(start)
    services = build(config, clock)
    with services.db.read() as db:
        existing = db.execute("SELECT created_at,settings_json FROM training_rounds WHERE creation_key=?", (prefix + ":create",)).fetchone()
    if existing:
        start = instant(existing["created_at"])
        clock.value = start
        demo_settings = RoundSettings.model_validate_json(existing["settings_json"])
    else:
        values = settings().model_dump()
        values["due_at"] = start + timedelta(days=10)
        demo_settings = RoundSettings.model_validate(values)
    seed_users(services, start)
    admin = services.auth.select_user("ADMIN")
    workflow = services.workflow
    round_id = workflow.create_round(admin, demo_settings, prefix + ":create")
    # Receipts let a completed demo be rerun without generating more rounds or attempts.
    r = workflow.get_round(admin, round_id)
    if r["status"] == "DRAFT":
        workflow.request_generation(admin, round_id, r["lock_version"], prefix + ":generate")
        result = services.worker.run_once()
        if not result or result["status"] != "SUCCEEDED":
            raise RuntimeError("Demo generation failed: inspect job via list_jobs")
    r = workflow.get_round(admin, round_id)
    if r["status"] == "IN_REVIEW":
        review = workflow.review(admin, round_id)
        if review["version"]["version_number"] == 1:
            workflow.request_revision(admin, round_id, r["current_version_id"], r["lock_version"],
                                      "เพิ่มตัวอย่างให้พนักงานเข้าใจง่าย", prefix + ":revise")
            result = services.worker.run_once()
            if not result or result["status"] != "SUCCEEDED":
                raise RuntimeError("Demo revision failed")
        r = workflow.get_round(admin, round_id)
        workflow.approve_version(admin, round_id, r["current_version_id"], r["lock_version"], prefix + ":approve")
    r = workflow.get_round(admin, round_id)
    if r["status"] == "APPROVED":
        workflow.publish_round(admin, round_id, r["current_version_id"], r["lock_version"], prefix + ":publish")
    services.delivery.prepare_previews(admin, round_id)
    # Fixed answer patterns are demo fixtures, not UI submissions or production seed data.
    samples = [("TEST001", 1, "AAAAA", 1), ("TEST004", 1, "AABBB", 1),
               ("TEST005", 1, "AAABA", 1), ("TEST002", 1, "AAABB", 11), ("TEST004", 2, "AAAAB", 11)]
    for user_id, number, choices, days in samples:
        clock.value = start + timedelta(days=days)
        actor = services.auth.select_user(user_id)
        a = next(a for a in services.quizzes.list_assignments(actor) if a["round_id"] == round_id)
        if a["attempt_count"] >= number:
            continue
        ticket = services.quizzes.begin_submission(actor, a["id"])
        services.quizzes.submit_attempt(actor, a["id"], ticket, {f"q{i+1}": value for i, value in enumerate(choices)})
    clock.value = start + timedelta(days=12)
    report = services.reporting.export(admin, prefix + ":report", round_id=round_id)
    return {"round_id": round_id, "report": report, "data_root": str(config.data_root),
            "note": "DEMO: fixed historical dates, mock content, preview only; no live API/search/email"}
