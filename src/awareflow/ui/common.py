from datetime import datetime, time, timedelta
import base64
import json
from uuid import uuid4
from zoneinfo import ZoneInfo

import streamlit as st
import pandas as pd

from awareflow.domain.models import RoundSettings, instant
from awareflow.application.reporting import csv_text

TZ = ZoneInfo("Asia/Bangkok")
STATUS = {"DRAFT": "ร่าง", "IN_REVIEW": "รอตรวจ", "CHANGES_REQUESTED": "ให้แก้ไข", "APPROVED": "อนุมัติแล้ว",
          "PUBLISHED": "เผยแพร่", "CLOSED": "ปิดรอบ", "NOT_STARTED": "ยังไม่ทำ", "PASSED": "ผ่าน", "FAILED": "ไม่ผ่าน"}


def dataframe(data, **kwargs):
    # Streamlit also exposes its own CSV toolbar; sanitize text there as well.
    frame = pd.DataFrame(data).copy()
    for column in frame:
        frame[column] = frame[column].map(lambda value: csv_text(value) if isinstance(value, str) else value)
    return st.dataframe(frame, **kwargs)


def command_key(label):
    key = "command:" + label
    if key not in st.session_state:
        st.session_state[key] = str(uuid4())
    return st.session_state[key]


def complete(label, message):
    st.session_state.pop("command:" + label, None)
    st.session_state["flash"] = message
    st.rerun()


def local_time(value):
    return instant(value).astimezone(TZ).strftime("%d/%m/%Y %H:%M") if value else "—"


def show_pdf(data, name="lesson.pdf"):
    st.download_button("ดาวน์โหลด PDF", data, name, "application/pdf", key="pdf-download:" + name)
    # PDF bytes are server-generated and authorized before reaching this helper.
    encoded = base64.b64encode(data).decode("ascii")
    st.markdown(f'<iframe title="PDF preview" src="data:application/pdf;base64,{encoded}" width="100%" height="610" style="border:1px solid #dbe5e0;border-radius:8px"></iframe>', unsafe_allow_html=True)


def show_agent_trace(s, actor, job_id):
    steps = s.worker.trace(actor, job_id)
    if not steps:
        st.caption("ยังไม่มีการเรียกเครื่องมือของ Agent สำหรับงานนี้")
        return
    st.caption("บันทึกเครื่องมือและผลที่เกิดขึ้นจริง ไม่ใช่ความคิดภายในของโมเดล; STARTED ที่ไม่มีผลอาจเป็นงานที่ถูกขัดจังหวะ")
    labels = {"search_sources": "ค้นข้อมูล", "compose_lesson": "เขียน/แก้บทเรียน", "check_lesson": "ตรวจร่างและ PDF",
              "submit_for_review": "ส่งให้คนตรวจ", "request_human_input": "ขอข้อมูลเพิ่ม", "budget_exhausted": "ถึงขีดจำกัด",
              "invalid_action": "คำสั่งเครื่องมือไม่ถูกต้อง"}
    dataframe([{"ครั้งที่รัน": x["attempt"], "ขั้น": x["step"], "เครื่องมือ": labels.get(x["tool"], x["tool"]),
                "สถานะ": x["status"], "ผล": json.loads(x["result_json"] or "{}").get("code", ""),
                "เวลา": local_time(x["created_at"])} for x in steps], hide_index=True, width="stretch")
    for x in steps:
        result = json.loads(x["result_json"] or "{}")
        if x["tool"] == "request_human_input" and result.get("message"):
            st.text("Agent ขอข้อมูล: " + result["message"])
    st.json([{"attempt": x["attempt"], "step": x["step"], "tool": x["tool"],
              "arguments": json.loads(x["arguments_json"]), "result": json.loads(x["result_json"] or "{}")} for x in steps], expanded=False)


def round_picker(s, actor, states=None, key="selected_round"):
    rounds = s.workflow.list_rounds(actor)
    if states:
        rounds = [r for r in rounds if r["status"] in states]
    if not rounds:
        st.info("ยังไม่มีรอบในขั้นตอนนี้")
        return None
    options = {r["id"]: r for r in rounds}
    if st.session_state.get(key) not in options:
        st.session_state.pop(key, None)
    rid = st.selectbox("เลือกรอบอบรม", list(options), format_func=lambda i: options[i]["title"] + " · " + STATUS[options[i]["status"]], key=key)
    return options[rid]


def settings_form(s, actor, label, initial=None):
    groups = s.directory.groups(actor)
    group_labels = {g["id"]: g["name"] for g in groups}
    now = datetime.now(TZ)
    context = s.directory.company_context(actor)
    data = json.loads(initial["settings_json"]) if initial else {}
    existing = RoundSettings.model_validate(data) if data else None
    prefix = label + ":"
    st.caption("ค่าทั้งหมดจะถูกเก็บเป็นสำเนาของรอบนี้ การแก้รอบเผยแพร่แล้วต้องสร้างรอบใหม่")
    goal = st.text_area("Prompt ที่ให้ Agent ทำ", value=(existing.agent_goal if existing else None) or "",
                       placeholder="เช่น สร้างบทเรียนเรื่องการใช้ AI อย่างปลอดภัยสำหรับฝ่ายบุคคล เน้นข้อมูลที่ไม่ควรใส่ในแชต ยกตัวอย่างสถานการณ์ และทำข้อสอบ 5 ข้อ",
                       help="พิมพ์หัวข้อ กลุ่มผู้อ่าน และสิ่งที่อยากเน้นได้เอง Agent จะทำงานภายในเงื่อนไขรอบด้านล่าง",
                       height=150, max_chars=2000, key=prefix + "goal")
    topic = st.text_input("หัวข้อย่อ (เว้นว่างเพื่อใช้ข้อความจาก Prompt)", value=existing.topic if existing else "",
                          placeholder="เช่น การใช้ AI อย่างปลอดภัย", max_chars=200, key=prefix + "topic")
    resolved_topic = topic.strip() or goal.strip()[:200]
    if st.button("ให้ AI เสนอคำค้น", key=prefix + "suggest"):
        if not resolved_topic:
            st.warning("กรอก Prompt หรือหัวข้อก่อนเสนอคำค้น")
        else:
            with st.spinner("กำลังเสนอคำค้น…"):
                st.session_state[prefix + "queries"] = "\n".join(s.worker.llm.suggest_queries(resolved_topic))
    with st.form(prefix + "form"):
        title = st.text_input("ชื่อรอบอบรม", value=existing.title if existing else "อบรม Security Awareness", key=prefix + "title")
        queries = st.text_area("คำค้นเริ่มต้น (เว้นว่างได้ Agent ปรับคำค้นได้)", value="\n".join(existing.queries) if existing else "",
                               help="หากเว้นว่าง ระบบใช้หัวข้อหรือข้อความจาก Prompt เป็นคำค้นเริ่มต้น", key=prefix + "queries")
        a, b = st.columns(2)
        start = a.date_input("ค้นตั้งแต่วันที่", value=existing.search_window.start_date if existing else (now - timedelta(days=90)).date(), key=prefix + "start")
        end = b.date_input("ถึงวันที่", value=existing.search_window.end_date if existing else now.date(), key=prefix + "end")
        domains = st.text_area("เว็บไซต์ที่อนุญาต (hostname หนึ่งรายการต่อบรรทัด)", value="\n".join(d.hostname for d in existing.allowed_domains) if existing else "cisa.gov\nncsc.gov.uk", key=prefix + "domains")
        subdomains = st.checkbox("รวม subdomains ของเว็บไซต์เหล่านี้", value=all(d.include_subdomains for d in existing.allowed_domains) if existing else True, key=prefix + "subdomains")
        selected = st.multiselect("กลุ่มผู้เข้าอบรม", list(group_labels), default=existing.target_group_ids if existing else list(group_labels)[:1], format_func=group_labels.get, key=prefix + "groups")
        a, b, c = st.columns(3)
        count = a.selectbox("จำนวนข้อ", [3, 4, 5], index=(existing.question_count if existing else 5) - 3, key=prefix + "count")
        percent = b.number_input("เกณฑ์ผ่าน (%)", min_value=1, max_value=100, value=existing.pass_percent if existing else 80, key=prefix + "percent")
        due = c.date_input("วันครบกำหนด (23:59 เวลาไทย)", value=existing.due_at.astimezone(TZ).date() if existing else (now + timedelta(days=14)).date(), key=prefix + "due")
        retakes = st.checkbox("อนุญาตสอบซ้ำ", value=existing.allow_retakes if existing else True, key=prefix + "retakes")
        mode = st.selectbox("ประเภทเนื้อหา", ["recent", "evergreen"], index=1 if existing and existing.content_mode == "evergreen" else 0,
                            format_func=lambda v: "ข้อมูลใหม่ในช่วงวันที่" if v == "recent" else "ทบทวนความรู้ (ไม่อ้างว่าเป็นข่าวใหม่)", key=prefix + "mode")
        policy = st.text_area("นโยบายภายในที่ใช้กับรอบนี้ (เว้นว่างได้)", value=(existing.company_context.policy_text if existing else context.policy_text) or "", key=prefix + "policy")
        channel = st.text_input("ช่องทางแจ้งเหตุที่บริษัทกำหนด (เว้นว่างได้)", value=(existing.company_context.reporting_channel if existing else context.reporting_channel) or "", key=prefix + "channel")
        submitted = st.form_submit_button("บันทึกรอบ", type="primary")
    if submitted:
        if not resolved_topic:
            st.error("กรอก Prompt หรือหัวข้อที่ต้องการให้ Agent ทำก่อนบันทึก")
            return None
        return RoundSettings.model_validate({"title": title, "topic": resolved_topic, "agent_goal": goal.strip() or None, "queries": [q.strip() for q in queries.splitlines() if q.strip()] or [resolved_topic],
            "search_window": {"start_date": start, "end_date": end}, "allowed_domains": [{"hostname": d.strip(), "include_subdomains": subdomains} for d in domains.splitlines() if d.strip()],
            "target_group_ids": selected, "question_count": count, "pass_percent": percent,
            "due_at": datetime.combine(due, time(23, 59, 59), TZ), "allow_retakes": retakes,
            "content_mode": mode, "company_context": {"policy_text": policy or None, "reporting_channel": channel or None}})
    return None
