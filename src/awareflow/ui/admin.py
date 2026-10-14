from datetime import datetime, timedelta
import json

import pandas as pd
import streamlit as st

from awareflow.domain.models import CompanyContext, RoundSettings
from .common import STATUS, TZ, command_key, complete, local_time, round_picker, settings_form, show_pdf, dataframe, show_agent_trace


def dashboard(s, actor):
    st.title("ภาพรวมการอบรม")
    st.caption("ติดตามการมีส่วนร่วมและความเข้าใจของพนักงาน จากผลที่บันทึกจริงในฐานข้อมูล")
    all_rounds = s.workflow.list_rounds(actor)
    if not all_rounds:
        st.info("เริ่มที่เมนูรอบอบรมเพื่อสร้างรอบแรก หรือโหลดข้อมูลสาธิตเพื่อดูตัวอย่างรายงาน")
        is_admin = any(u["id"] == actor.user_id and u["role"] == "admin" for u in s.directory.demo_identities())
        if s.config.data_class == "demo" and is_admin and st.button("โหลดข้อมูลสาธิต", type="primary"):
            from awareflow.demo import run_demo
            with st.spinner("กำลังสร้างตัวอย่างครบหนึ่งรอบ…"):
                run_demo(s.config, start=datetime.now(TZ) - timedelta(days=20), prefix="ui-demo-v1")
            st.rerun()
        return
    counts = {state: sum(r["status"] == state for r in all_rounds) for state in ("DRAFT", "IN_REVIEW", "PUBLISHED", "CLOSED")}
    cols = st.columns(4)
    for col, (state, value) in zip(cols, counts.items()):
        col.metric(STATUS[state], value)
    scope = st.radio("มุมมองรายงาน", ["รายรอบ", "รายปี"], horizontal=True, key="report_scope")
    selected = None
    year = None
    if scope == "รายรอบ":
        selected = round_picker(s, actor, {"PUBLISHED", "CLOSED"}, "dashboard_round")
        if not selected:
            return
        metrics = s.reporting.round_metrics(actor, selected["id"])
    else:
        year = int(st.number_input("ปี ค.ศ.", 2000, 9998, datetime.now(TZ).year))
        metrics = s.reporting.annual_metrics(actor, year)
    st.subheader("ผลการอบรม")
    cols = st.columns(4)
    for col, name, key in zip(cols, ["มอบหมาย", "ทำแล้ว", "ยังไม่ทำ", "ผ่าน"], ["assigned", "completed", "not_started", "passed"]):
        col.metric(name, metrics[key])
    cols = st.columns(3)
    for col, name, key in zip(cols, ["อัตราทำครบ", "อัตราผ่านในผู้ทำแล้ว", "คะแนนเฉลี่ยล่าสุด"], ["completion_rate", "pass_rate", "average_score"]):
        col.metric(name, "—" if metrics[key] is None else f'{metrics[key]:.1f}%')
    st.caption(f'ส่งครั้งแรกทันกำหนด {metrics["on_time"]} · ล่าช้า {metrics["late"]} · ไม่ผ่าน {metrics["failed"]} · ข้อมูล ณ {local_time(metrics["as_of"])}')
    if scope == "รายปี":
        st.caption(f'รายปีนับเป็นคน-รอบ; จำนวนบุคคลไม่ซ้ำ {metrics["unique_people"]} คน')
    for report in metrics.get("rounds", [metrics]):
        with st.expander(report["title"] + " · ผลรายบุคคล", expanded=scope == "รายรอบ"):
            table = pd.DataFrame(report["people"])[["user_id", "display_name", "status", "score", "first_score", "attempts", "first_on_time", "latest_on_time"]]
            table["status"] = table["status"].map(STATUS)
            for field in ("first_on_time", "latest_on_time"):
                table[field] = table[field].map({True: "ทันกำหนด", False: "ล่าช้า"}).fillna("—")
            dataframe(table, hide_index=True, width="stretch", column_config={
                "user_id": "รหัสผู้ใช้", "display_name": "ชื่อ", "status": "สถานะ", "score": "คะแนนล่าสุด (%)",
                "first_score": "คะแนนครั้งแรก (%)", "attempts": "ครั้งที่สอบ", "first_on_time": "ส่งครั้งแรก", "latest_on_time": "ส่งครั้งล่าสุด"})
            questions = pd.DataFrame(report["questions"])
            if not questions.empty:
                st.bar_chart(questions.set_index("question_id")[["wrong"]], color="#CF000D")
                dataframe(questions, hide_index=True, width="stretch")
    st.caption("ผลปัจจุบันใช้ครั้งล่าสุด ความตรงเวลาใช้ครั้งแรก ผู้ที่ยังไม่ทำไม่มีคะแนน อัตราทำครบเทียบผู้มอบหมาย อัตราผ่านเทียบผู้ทำแล้ว")
    st.download_button("ดาวน์โหลด CSV ปัจจุบัน", s.reporting.to_csv(metrics), "awareflow-results.csv", "text/csv")
    export_label = "export:" + (selected["id"] if selected else str(year))
    if st.button("สร้างรายงาน PDF และสรุป", type="primary"):
        with st.spinner("กำลังสร้างรายงานจากตัวเลขชุดเดียวกัน…"):
            result = s.reporting.export(actor, command_key(export_label), round_id=selected["id"] if selected else None, year=year)
        st.session_state[export_label + ":result"] = result
        st.session_state.pop("command:" + export_label, None)
    result = st.session_state.get(export_label + ":result")
    if result:
        st.success(result["metrics"].get("summary", "สร้างรายงานแล้ว"))
        st.download_button("ดาวน์โหลดรายงาน PDF", s.files.read(result["pdf_path"]), "awareflow-report.pdf", "application/pdf")
        st.download_button("ดาวน์โหลด CSV ชุดเดียวกับ PDF", s.files.read(result["csv_path"]), "awareflow-report.csv", "text/csv")


def rounds_page(s, actor):
    st.title("รอบอบรม")
    st.caption("มอบเป้าหมายให้ Agent เลือกเครื่องมือ ค้นเพิ่ม เขียนและแก้ร่าง ก่อนส่งให้คุณตรวจอนุมัติ")
    if s.config.llm_provider == "mock":
        st.info("Mock Agent: จำลองการเลือกเครื่องมือเพื่อทดสอบ ยังไม่ได้ใช้โมเดล AI ตัดสินใจจริง")
    if "redirect_round" in st.session_state:
        st.session_state["selected_round"] = st.session_state.pop("redirect_round")
        st.session_state["round_action"] = "รอบที่มีอยู่"
    if st.button("เขียน Prompt ใหม่", key="new_prompt", type="primary", icon=":material/add:"):
        st.session_state["round_action"] = "สร้างรอบใหม่"
    mode = st.radio("จัดการรอบ", ["รอบที่มีอยู่", "สร้างรอบใหม่"], horizontal=True, key="round_action")
    if mode == "สร้างรอบใหม่":
        value = settings_form(s, actor, "new")
        if value:
            rid = s.workflow.create_round(actor, value, command_key("create_round"))
            st.session_state["round_created"] = rid
            st.session_state["redirect_round"] = rid
            complete("create_round", "บันทึก Prompt และค่ารอบแล้ว กด ‘ให้ Agent เตรียมชุดอบรม’ เพื่อเริ่มงาน")
        return
    st.caption("รายการด้านล่างคือรอบที่บันทึกไว้แล้ว คุณกำหนดหัวข้อเองได้โดยกด ‘เขียน Prompt ใหม่’")
    r = round_picker(s, actor)
    if not r:
        return
    st.subheader(r["title"])
    st.caption(f'{STATUS[r["status"]]} · กำหนดส่ง {local_time(r["due_at"])} · เกณฑ์ผ่าน {r["pass_percent"]}%')
    goal = json.loads(r["settings_json"]).get("agent_goal")
    if goal:
        st.text("เป้าหมาย: " + goal)
    with st.expander("ค่าตั้งรอบ", expanded=False):
        st.json(json.loads(r["settings_json"]))
    if r["status"] not in {"PUBLISHED", "CLOSED"}:
        seen_key = "round_seen:" + r["id"]
        if seen_key not in st.session_state:
            st.session_state[seen_key] = r["lock_version"]
        if st.button("โหลดข้อมูลล่าสุด", key="reload-round"):
            for key in list(st.session_state):
                if key.startswith("edit:" + r["id"]) or key == seen_key:
                    del st.session_state[key]
            st.rerun()
        if r["status"] == "DRAFT" and st.button("ให้ Agent เตรียมชุดอบรม", type="primary"):
            s.workflow.request_generation(actor, r["id"], st.session_state[seen_key], command_key("generate:" + r["id"]))
            st.session_state.pop(seen_key, None)
            complete("generate:" + r["id"], "เพิ่มงานสร้างชุดแล้ว กดประมวลผลงาน หรือให้ worker ทำงานเบื้องหลัง")
        with st.expander("แก้ค่ารอบ (ต้องสร้างและตรวจฉบับใหม่)"):
            value = settings_form(s, actor, "edit:" + r["id"], r)
            if value:
                s.workflow.update_round(actor, r["id"], st.session_state[seen_key], value, command_key("update:" + r["id"]))
                st.session_state.pop(seen_key, None)
                complete("update:" + r["id"], "บันทึกแล้ว รอบกลับเป็นร่าง")
    else:
        st.info("รอบนี้ตรึงเนื้อหาและรายชื่อแล้ว หากต้องการเปลี่ยนให้สร้างรอบใหม่")
    jobs = [j for j in s.workflow.list_jobs(actor, r["id"]) if j["kind"] in {"GENERATE", "REVISE"}]
    if jobs:
        st.subheader("งานของ Agent")
        dataframe([{k: j[k] for k in ("kind", "status", "attempts", "max_attempts", "last_error_safe")} for j in jobs], hide_index=True, width="stretch")
        for job in jobs:
            with st.expander("ประวัติ Agent · " + job["kind"] + " · " + local_time(job["created_at"]) + " · " + job["id"][:8]):
                show_agent_trace(s, actor, job["id"])
            if job["status"] == "FAILED" and job["attempts"] < job["max_attempts"]:
                if st.button("ลองใหม่", key="retry:" + job["id"]):
                    s.worker.retry(actor, job["id"], command_key("retry:" + job["id"]))
                    complete("retry:" + job["id"], "นำงานกลับเข้าคิวแล้ว")
            if job["status"] == "NEEDS_INPUT":
                st.warning("Agent หยุดเพื่อขอข้อมูลหรือถึงขีดจำกัด ดูประวัติ Agent แล้วแก้เป้าหมาย/ค่ารอบเพื่อเริ่มใหม่")
    if any(j["status"] == "QUEUED" for j in jobs) and st.button("ประมวลผลงานตอนนี้", key="run_worker"):
        with st.spinner("Agent กำลังเลือกเครื่องมือและทำงานตามเป้าหมาย…"):
            result = s.worker.run_once()
        st.session_state.pop("round_seen:" + r["id"], None)
        st.session_state["flash"] = "ผลประมวลผล: " + (result["status"] if result else "ยังไม่มีงานถึงเวลา")
        st.rerun()
    if r["status"] == "PUBLISHED":
        with st.form("close:" + r["id"]):
            reason = st.text_input("เหตุผลในการปิดรอบ")
            if st.form_submit_button("ปิดรอบ"):
                s.workflow.close_round(actor, r["id"], r["lock_version"], reason, command_key("close:" + r["id"]))
                complete("close:" + r["id"], "ปิดรอบแล้ว")


def review_page(s, actor):
    st.title("ตรวจและอนุมัติเนื้อหา")
    st.caption("ตรวจบทอ่าน PDF ข้อสอบ เฉลย และแหล่งอ้างอิงของเวอร์ชันเดียวกัน ก่อนอนุมัติให้เผยแพร่")
    r = round_picker(s, actor, key="review_round")
    if not r:
        return
    versions = s.workflow.versions(actor, r["id"])
    if not versions:
        st.info("ยังไม่มีชุดอบรมที่สร้างสำเร็จ กลับไปสร้างและประมวลผลงานในเมนูรอบอบรม")
        return
    mapping = {v["id"]: v for v in versions}
    vid = st.selectbox("เวอร์ชัน", list(mapping), format_func=lambda i: f'ฉบับที่ {mapping[i]["version_number"]} · {local_time(mapping[i]["created_at"])}', key="review-version:" + r["id"])
    record = s.workflow.review(actor, r["id"], vid)
    v = record["version"]
    bundle = json.loads(v["bundle_json"])
    seen_key = "review_seen:" + vid
    if seen_key not in st.session_state:
        st.session_state[seen_key] = r["lock_version"]
    if st.button("โหลดสถานะล่าสุด", key="review_reload"):
        st.session_state.pop(seen_key, None)
        st.rerun()
    if v["change_summary"]:
        st.info(v["change_summary"])
    lesson, quiz, sources, history, agent_history = st.tabs(["บทอ่าน PDF", "ข้อสอบและเฉลย", "แหล่งอ้างอิง", "ประวัติการตรวจ", "การทำงานของ Agent"])
    with agent_history:
        validation = json.loads(v["validation_json"])
        if validation.get("agent_job_id"):
            show_agent_trace(s, actor, validation["agent_job_id"])
        else:
            st.caption("เวอร์ชันนี้สร้างก่อนเพิ่ม Agent จึงไม่มีประวัติเครื่องมือ")
    with lesson:
        show_pdf(s.files.read_verified(v["pdf_path"], v["pdf_sha256"]), "lesson-" + str(v["version_number"]) + ".pdf")
    with quiz:
        for i, q in enumerate(bundle["questions"], 1):
            st.markdown(f'**{i}. {q["prompt"]}**')
            for option in q["options"]:
                st.write(option["option_id"] + ". " + option["text"])
            st.success("เฉลย " + q["correct_option_id"] + " · " + q["explanation"])
            st.caption("รองรับโดยหัวข้อ " + ", ".join(q["supporting_section_ids"]))
    with sources:
        for source in json.loads(v["sources_json"]):
            st.markdown("**" + source["title"] + "**")
            if source["url"]:
                st.link_button("เปิดแหล่งข้อมูล", source["url"])
            st.write({k: source.get(k) for k in ("publisher", "published_at", "event_at", "updated_at", "provider_date", "retrieved_at", "provenance")})
            st.caption(source["eligibility_reason"])
            st.write(source["excerpt"])
    with history:
        dataframe([{k: event[k] for k in ("action", "feedback", "actor_id", "created_at")} for event in record["history"]], hide_index=True)
    is_current = vid == r["current_version_id"]
    if not is_current:
        st.info("กำลังดูประวัติฉบับเก่า อนุมัติได้เฉพาะฉบับปัจจุบัน")
        return
    if r["status"] == "IN_REVIEW":
        a, b = st.columns([1, 2])
        if a.button("อนุมัติฉบับนี้", type="primary"):
            s.workflow.approve_version(actor, r["id"], vid, st.session_state[seen_key], command_key("approve:" + vid))
            st.session_state.pop(seen_key, None)
            complete("approve:" + vid, "อนุมัติแล้ว ยังไม่เผยแพร่หรือส่งอีเมล")
        with b.form("revise:" + vid):
            feedback = st.text_area("ข้อเสนอแนะให้ AI แก้ไข")
            if st.form_submit_button("ให้ AI แก้ไข"):
                s.workflow.request_revision(actor, r["id"], vid, st.session_state[seen_key], feedback, command_key("revise:" + vid))
                st.session_state.pop(seen_key, None)
                complete("revise:" + vid, "รับข้อเสนอแนะแล้ว ประมวลผลงานในเมนูรอบอบรมเพื่อสร้างฉบับใหม่")
    elif r["status"] == "APPROVED" and actor.user_id in [u["id"] for u in s.directory.demo_identities() if u["role"] == "admin"]:
        st.success("ฉบับนี้ผ่านการอนุมัติ พร้อมตรึงรายชื่อและสร้าง Email Preview")
        if st.button("เผยแพร่รอบและสร้างคำเชิญ", type="primary"):
            s.workflow.publish_round(actor, r["id"], vid, st.session_state[seen_key], command_key("publish:" + vid))
            s.delivery.prepare_previews(actor, r["id"])
            st.session_state.pop(seen_key, None)
            complete("publish:" + vid, "เผยแพร่แล้ว ดูคำเชิญในเมนูอีเมล (ยังไม่ส่งจริง)")


def outbox_page(s, actor):
    st.title("อีเมลและคำเชิญ")
    st.caption("ค่าเริ่มต้นเป็น Preview ไม่ส่งอีเมลจริง สถานะอีเมลแยกจากสถานะทำข้อสอบ")
    r = round_picker(s, actor, {"PUBLISHED", "CLOSED"}, "outbox_round")
    if not r:
        return
    if r["status"] == "PUBLISHED" and st.button("เตรียม Email Preview"):
        s.delivery.prepare_previews(actor, r["id"])
        st.rerun()
    outbox = s.delivery.list_outbox(actor, r["id"])
    dataframe([{k: row[k] for k in ("user_id", "recipient", "transport", "status", "attempts")} for row in outbox], hide_index=True, width="stretch")
    if not outbox:
        return
    mapping = {row["id"]: row for row in outbox}
    chosen = st.selectbox("ดูคำเชิญ", list(mapping), format_func=lambda i: mapping[i]["user_id"] + " · " + mapping[i]["transport"])
    row = mapping[chosen]
    payload = json.loads(row["payload_json"])
    st.subheader(payload["subject"])
    st.write(payload["body"])
    st.code(payload["quiz_url"], language=None)
    if row["eml_path"]:
        st.download_button("ดาวน์โหลด EML พร้อมไฟล์แนบ", s.files.read(row["eml_path"]), row["user_id"] + ".eml", "message/rfc822")
    st.download_button("ดาวน์โหลดบทอ่านที่แนบ", s.files.read_verified(payload["pdf_path"], payload["pdf_sha256"]), "lesson.pdf", "application/pdf")
    if st.button("ออกลิงก์ใหม่สำหรับทดสอบ", key="new-link"):
        token = s.delivery.issue_link(actor, row["assignment_id"])
        st.session_state["fresh-link:" + chosen] = s.config.base_url.rstrip("/") + "/?token=" + token
    if st.session_state.get("fresh-link:" + chosen):
        st.link_button("เปิดข้อสอบจากลิงก์ใหม่", st.session_state["fresh-link:" + chosen])
    if s.config.email_real_send and s.config.email_transport == "smtp" and r["status"] == "PUBLISHED":
        st.warning("เปิด SMTP ทดสอบอยู่ ผู้รับต้องอยู่ในรายการอนุญาตบนเซิร์ฟเวอร์")
        if row["transport"] == "preview" and st.button("เตรียมรายการ SMTP สำหรับผู้รับนี้"):
            s.delivery.prepare_smtp(actor, row["assignment_id"], command_key("smtp:" + row["assignment_id"]))
            complete("smtp:" + row["assignment_id"], "เตรียมแล้ว เลือกรายการ smtp เพื่อตรวจและส่ง")
        if row["transport"] == "smtp" and row["status"] in {"PENDING", "FAILED"}:
            confirmed = st.checkbox("ยืนยันส่งจริงให้ " + str(row["recipient"]))
            if st.button("ส่งอีเมลทดสอบจริง", disabled=not confirmed, type="primary"):
                outcome = s.delivery.send_smtp(actor, row["id"])
                st.session_state["flash"] = "ผลส่ง: " + outcome
                st.rerun()
        if row["status"] == "UNKNOWN":
            with st.form("resolve-unknown"):
                decision = st.selectbox("ผลตรวจสอบ", ["accepted", "retry"], format_func=lambda v: "ยืนยันว่าเซิร์ฟเวอร์รับแล้ว" if v == "accepted" else "อนุญาตให้ลองส่งซ้ำ")
                reason = st.text_input("เหตุผล/หลักฐานที่ตรวจสอบ")
                if st.form_submit_button("บันทึกผลตรวจสอบ"):
                    s.delivery.resolve_unknown(actor, row["id"], decision, reason, command_key("resolve:" + row["id"]))
                    complete("resolve:" + row["id"], "บันทึกแล้ว")
