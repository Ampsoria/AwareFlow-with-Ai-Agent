import json

import streamlit as st

from awareflow.domain.models import CompanyContext, RoundSettings
from .common import command_key, complete, dataframe


def settings_page(s, actor):
    st.title("ผู้ใช้และการตั้งค่า")
    people, groups_tab, company, scheduler, system = st.tabs(["ผู้ใช้", "กลุ่ม", "ข้อมูลบริษัท", "ตั้งเวลา", "ระบบและประวัติ"])
    users = s.directory.users(actor)
    groups = s.directory.groups(actor)
    with people:
        st.caption("ใช้รหัสภายในเป็นตัวหลัก การเปลี่ยนอีเมลมีผลกับรอบใหม่เท่านั้น")
        dataframe([{k: u[k] for k in ("id", "display_name", "email", "role", "is_active")} for u in users], hide_index=True, width="stretch")
        selected = st.selectbox("เลือกผู้ใช้ที่ต้องการแก้ไข", ["new"] + [u["id"] for u in users], format_func=lambda i: "เพิ่มผู้ใช้ใหม่" if i == "new" else i)
        current = next((u for u in users if u["id"] == selected), {})
        with st.form("user:" + selected):
            user_id = st.text_input("รหัสผู้ใช้", value=current.get("id", ""), disabled=selected != "new")
            name = st.text_input("ชื่อที่แสดง", value=current.get("display_name", ""))
            email = st.text_input("อีเมลทดสอบ", value=current.get("email") or "")
            role = st.selectbox("บทบาท", ["learner", "reviewer", "admin"], index=["learner", "reviewer", "admin"].index(current.get("role", "learner")))
            active = st.checkbox("เปิดใช้งาน", value=bool(current.get("is_active", 1)))
            if st.form_submit_button("บันทึกผู้ใช้"):
                s.directory.save_user(actor, user_id, name, email, role, active)
                st.rerun()
    with groups_tab:
        group_id = st.selectbox("เลือกกลุ่ม", ["new"] + [g["id"] for g in groups], format_func=lambda i: "เพิ่มกลุ่มใหม่" if i == "new" else i)
        current = next((g for g in groups if g["id"] == group_id), {})
        with st.form("group:" + group_id):
            gid = st.text_input("รหัสกลุ่ม", current.get("id", ""), disabled=group_id != "new")
            name = st.text_input("ชื่อกลุ่ม", current.get("name", ""))
            members = st.multiselect("สมาชิกกลุ่ม", [u["id"] for u in users], default=current.get("members", []))
            if st.form_submit_button("บันทึกกลุ่ม"):
                s.directory.save_group(actor, gid, name, members)
                st.rerun()
    with company:
        context = s.directory.company_context(actor)
        st.caption("เป็นค่าเริ่มต้นของรอบใหม่ ไม่เปลี่ยนเนื้อหาของรอบเก่า")
        with st.form("company"):
            policy = st.text_area("นโยบายภายใน", context.policy_text or "")
            channel = st.text_input("ช่องทางแจ้งเหตุ", context.reporting_channel or "")
            if st.form_submit_button("บันทึกข้อมูลบริษัท"):
                s.directory.save_context(actor, CompanyContext(policy_text=policy or None, reporting_channel=channel or None))
                st.success("บันทึกแล้ว")
    with scheduler:
        st.caption("เวลา Asia/Bangkok · งานสร้างร่างไม่ข้ามคนอนุมัติ · ต้องเปิดเครื่องและรัน worker/scheduler")
        rounds = s.workflow.list_rounds(actor)
        mapping = {r["id"]: r for r in rounds}
        with st.form("new-schedule"):
            name = st.text_input("ชื่องานตั้งเวลา", "อบรมประจำเดือน")
            kind = st.selectbox("งาน", ["DRAFT_MONTHLY", "DRAFT_YEARLY", "REPORT_YEARLY"])
            template = st.selectbox("ใช้ค่าตั้งรอบนี้เป็นแม่แบบ (รายงานไม่จำเป็น)", [None] + list(mapping), format_func=lambda i: "ไม่เลือก" if i is None else mapping[i]["title"])
            a, b, c = st.columns(3)
            day = int(a.number_input("วันที่", 1, 28, 1))
            month = int(b.number_input("เดือน (สำหรับรายปี)", 1, 12, 1))
            hour = int(c.number_input("ชั่วโมง", 0, 23, 9))
            due_days = int(st.number_input("กำหนดส่งหลังสร้างกี่วัน", 1, 365, 14))
            lookback = int(st.number_input("ค้นย้อนหลัง (วัน)", 1, 3660, 90))
            if st.form_submit_button("เพิ่มงานตั้งเวลา"):
                settings = RoundSettings.model_validate_json(mapping[template]["settings_json"]) if template else None
                s.scheduling.create_schedule(actor, command_key("new-schedule"), name, kind, settings,
                                             day=day, month=month if kind != "DRAFT_MONTHLY" else None,
                                             hour=hour, lookback_days=lookback, due_days=due_days)
                complete("new-schedule", "เพิ่มงานตั้งเวลาแล้ว")
        schedules = s.scheduling.list_schedules(actor)
        for schedule in schedules:
            a, b = st.columns([4, 1])
            a.write(f'{schedule["name"] or "รายงานประจำปี"} · {schedule["kind"]} · วันที่ {schedule["day_of_month"]} เวลา {schedule["local_hour"]:02d}:{schedule["local_minute"]:02d}')
            if b.button("พักงาน" if schedule["enabled"] else "เปิดงาน", key="schedule-enable:" + schedule["id"]):
                s.scheduling.set_enabled(actor, schedule["id"], not schedule["enabled"])
                st.rerun()
        if st.button("ตรวจงานที่ถึงเวลาตอนนี้"):
            with st.spinner("ตรวจตารางเวลา…"):
                created = s.scheduling.tick()
            st.success(f"สร้างงานใหม่ {len(created)} รายการ")
    with system:
        st.write({"โหมดตัวตน": "demo selector", "ชุดข้อมูล": s.config.data_class,
                  "โมเดล": s.config.llm_provider + " / " + (s.config.llm_model or "fixture"),
                  "ค้นเว็บ": s.config.search_provider, "เปิดส่ง SMTP": s.config.email_real_send and s.config.email_transport == "smtp",
                  "base URL": s.config.base_url})
        st.info("ตั้ง provider/model/endpoint/credentials ในไฟล์ .env บนเซิร์ฟเวอร์ แล้ว restart แอป ไม่กรอก API key ผ่านหน้าเว็บ")
        st.caption("localhost เปิดได้เฉพาะเครื่องนี้ หากให้คนอื่นทดสอบ ให้ตั้ง base URL และ server address ที่เข้าถึงได้")
        dataframe(s.directory.audit_log(actor), hide_index=True, width="stretch")
