"""Deterministic metrics; all rates have explicit denominators and no-data stays null."""
import csv
import io
import json
from zoneinfo import ZoneInfo

from awareflow.domain.errors import ValidationError
from awareflow.domain.models import instant, utc
from awareflow.infrastructure.database import one, rows
from .common import audit, authorize, dump, receipt, replay, uid


def rate(numerator, denominator):
    return numerator * 100 / denominator if denominator else None


def mean(values):
    return sum(values) / len(values) if values else None


def csv_text(value):
    text = str(value) if value is not None else ""
    # Check after whitespace/control characters, while preserving original text.
    if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")):
        return "'" + text
    return text


class Reporting:
    def __init__(self, database, clock, files, pdf, llm=None, llm_provider="mock"):
        self.db, self.clock, self.files, self.pdf = database, clock, files, pdf
        self.llm, self.llm_provider = llm, llm_provider

    def summary(self, metrics):
        def fmt(value):
            return "ยังไม่มีข้อมูล" if value is None else f"{value:.2f}%"
        choices = {
            "completion": f'ทำแล้ว {metrics["completed"]} จาก {metrics["assigned"]} คน-รอบ ({fmt(metrics["completion_rate"])})',
            "pass_rate": f'ผ่าน {metrics["passed"]} คน-รอบ อัตราผ่านในผู้ทำแล้ว {fmt(metrics["pass_rate"])}',
            "average_score": "คะแนนเฉลี่ยครั้งล่าสุด " + fmt(metrics["average_score"]),
            "late": f'ส่งครั้งแรกล่าช้า {metrics["late"]} คน-รอบ',
            "not_started": f'ยังไม่ทำ {metrics["not_started"]} คน-รอบ',
        }
        selected = {"introduction": "ผลการอบรม ณ เวลารายงาน", "focus": ["completion", "pass_rate", "not_started"]}
        source = "template"
        if self.llm and hasattr(self.llm, "summarize"):
            try:
                from awareflow.infrastructure.providers import Narrative
                safe_metrics = {k: v for k, v in metrics.items() if isinstance(v, (int, float)) or v is None}
                selected = Narrative.model_validate(self.llm.summarize(safe_metrics)).model_dump()
                source = "mock" if self.llm_provider == "mock" else "live"
            except Exception:
                pass  # Report generation remains available if summarization fails.
        text = selected["introduction"] + ": " + "; ".join(choices[k] for k in dict.fromkeys(selected["focus"]))
        return text, source

    def round_metrics(self, actor, round_id, as_of=None):
        cutoff = utc(as_of or self.clock.now())
        with self.db.read() as db:
            db.execute("BEGIN")  # Consistent snapshot across all SELECTs.
            authorize(db, actor, {"admin", "reviewer"})
            return self._round(db, round_id, cutoff)

    def _round(self, db, round_id, cutoff):
        r = one(db, "SELECT * FROM training_rounds WHERE id=?", (round_id,))
        if not r["published_at"] or r["published_at"] > cutoff:
            raise ValidationError("รอบนี้ยังไม่เผยแพร่ ณ เวลารายงาน")
        bundle = json.loads(one(db, "SELECT bundle_json FROM training_versions WHERE id=?", (r["published_version_id"],))["bundle_json"])
        questions = {q["question_id"]: q for q in bundle["questions"]}
        wrong = {qid: 0 for qid in questions}
        people, scores, first_scores = [], [], []
        passed = on_time = 0
        assignments = rows(db, "SELECT * FROM assignments WHERE round_id=? AND assigned_at<=? ORDER BY user_id", (round_id, cutoff))
        for a in assignments:
            attempts = rows(db, "SELECT * FROM attempts WHERE assignment_id=? AND submitted_at<=? ORDER BY attempt_number", (a["id"], cutoff))
            person = {"user_id": a["user_id"], "display_name": a["display_name_snapshot"],
                      "assignment_id": a["id"], "status": "NOT_STARTED", "score": None,
                      "first_score": None, "attempts": len(attempts), "first_submitted_at": None,
                      "latest_submitted_at": None, "first_on_time": None, "latest_on_time": None}
            if attempts:
                first, latest = attempts[0], attempts[-1]
                score = latest["correct_count"] * 100 / latest["question_count"]
                first_score = first["correct_count"] * 100 / first["question_count"]
                is_pass = latest["correct_count"] * 100 >= r["pass_percent"] * latest["question_count"]
                first_on_time = first["submitted_at"] <= r["due_at"]
                scores.append(score)
                first_scores.append(first_score)
                passed += int(is_pass)
                on_time += int(first_on_time)
                answers = json.loads(latest["answers_json"])
                for qid, q in questions.items():
                    wrong[qid] += int(answers[qid] != q["correct_option_id"])
                person.update(status="PASSED" if is_pass else "FAILED", score=score, first_score=first_score,
                              first_submitted_at=first["submitted_at"], latest_submitted_at=latest["submitted_at"],
                              first_on_time=first_on_time, latest_on_time=latest["submitted_at"] <= r["due_at"])
            people.append(person)
        assigned, completed = len(people), len(scores)
        highest = max(wrong.values(), default=0)
        return {"schema_version": 1, "is_demo": bundle["provenance"]["is_demo"], "round_id": round_id,
                "title": r["title"], "version_id": r["published_version_id"], "as_of": cutoff,
                "assigned": assigned, "completed": completed, "not_started": assigned - completed,
                "passed": passed, "failed": completed - passed, "completion_rate": rate(completed, assigned),
                "pass_rate": rate(passed, completed), "average_score": mean(scores),
                "first_average_score": mean(first_scores), "on_time": on_time, "late": completed - on_time,
                "people": people, "questions": [{"question_id": qid, "prompt": questions[qid]["prompt"],
                                                "wrong": count, "answered": completed} for qid, count in wrong.items()],
                "most_missed": [qid for qid, count in wrong.items() if count == highest and highest > 0]}

    def annual_metrics(self, actor, year, as_of=None):
        if type(year) is not int or not 2000 <= year <= 9998:
            raise ValidationError("ปีรายงานไม่ถูกต้อง")
        cutoff = utc(as_of or self.clock.now())
        with self.db.read() as db:
            db.execute("BEGIN")
            authorize(db, actor, {"admin", "reviewer"})
            round_ids = [r["id"] for r in rows(db, "SELECT id, published_at FROM training_rounds WHERE published_at<=? ORDER BY published_at", (cutoff,))
                         if instant(r["published_at"]).astimezone(ZoneInfo("Asia/Bangkok")).year == year]
            reports = [self._round(db, rid, cutoff) for rid in round_ids]
        counts = {key: sum(r[key] for r in reports) for key in ("assigned", "completed", "not_started", "passed", "failed", "on_time", "late")}
        people = [p for r in reports for p in r["people"]]
        return {"schema_version": 1, "is_demo": any(r["is_demo"] for r in reports) if reports else True, "year": year, "title": "รายงานประจำปี " + str(year),
                "as_of": cutoff, **counts, "unique_people": len({p["user_id"] for p in people}),
                "completion_rate": rate(counts["completed"], counts["assigned"]),
                "pass_rate": rate(counts["passed"], counts["completed"]),
                "average_score": mean([p["score"] for p in people if p["score"] is not None]),
                "first_average_score": mean([p["first_score"] for p in people if p["first_score"] is not None]),
                "rounds": reports}

    def to_csv(self, metrics):
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow(["รอบ", "รหัสผู้ใช้", "ชื่อ", "สถานะ", "คะแนนล่าสุด", "คะแนนครั้งแรก", "จำนวนครั้ง", "ส่งครั้งแรก", "ส่งครั้งล่าสุด"])
        reports = metrics.get("rounds", [metrics])
        for report in reports:
            for p in report["people"]:
                writer.writerow([csv_text(v) for v in [report["title"], p["user_id"], p["display_name"], p["status"],
                                                       p["score"], p["first_score"], p["attempts"],
                                                       p["first_submitted_at"], p["latest_submitted_at"]]])
        return stream.getvalue().encode("utf-8-sig")

    def export(self, actor, key, round_id=None, year=None):
        if (round_id is None) == (year is None):
            raise ValidationError("เลือกรอบหรือปีอย่างใดอย่างหนึ่ง")
        request = {"round_id": round_id, "year": year}
        with self.db.read() as db:
            authorize(db, actor, {"admin", "reviewer"})
            prior = replay(db, actor, "report", key, request)
            if prior is not None:
                return prior
        metrics = self.round_metrics(actor, round_id) if round_id else self.annual_metrics(actor, year)
        narrative, narrative_source = self.summary(metrics)
        metrics["summary"] = narrative
        metrics["summary_source"] = narrative_source
        pdf_path, pdf_sha = self.files.put(self.pdf.report(metrics), "pdf")
        csv_path, _ = self.files.put(self.to_csv(metrics), "csv")
        now = utc(self.clock.now())
        with self.db.transaction() as db:
            authorize(db, actor, {"admin", "reviewer"})
            prior = replay(db, actor, "report", key, request)
            if prior is not None:
                return prior
            job_id, report_id = uid(), uid()
            db.execute("""INSERT INTO jobs(id, idempotency_key, kind, round_id, params_json, status,
                available_at, created_at, completed_at) VALUES (?, ?, 'REPORT', ?, ?, 'SUCCEEDED', ?, ?, ?)""",
                       (job_id, "export:" + key, round_id, dump(request), now, now, now))
            db.execute("""INSERT INTO report_artifacts(id, job_id, round_id, report_year, as_of, metrics_json,
                narrative, narrative_source, pdf_path, pdf_sha256, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                       (report_id, job_id, round_id, year, metrics["as_of"], dump(metrics), narrative, narrative_source, pdf_path, pdf_sha, now))
            result = {"report_id": report_id, "pdf_path": pdf_path, "csv_path": csv_path, "metrics": metrics}
            audit(db, actor, "REPORT", report_id, now)
            return receipt(db, actor, "report", key, request, result, now)
