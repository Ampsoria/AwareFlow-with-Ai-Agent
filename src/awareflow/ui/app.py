import sqlite3

from pydantic import ValidationError as SchemaError
import streamlit as st

from awareflow.bootstrap import build, Clock
from awareflow.config import Config
from awareflow.demo import seed_users
from awareflow.domain.errors import WorkflowError
from .admin import dashboard, rounds_page, review_page, outbox_page
from .learner import learner_page
from .settings import settings_page
from .theme import ROOT, apply_theme, brand, navigation_label


@st.cache_resource(show_spinner=False)
def services(config):
    s = build(config)
    seed_users(s, Clock().now())
    return s


def main():
    st.set_page_config(page_title="AwareFlow · Security Awareness", page_icon=str(ROOT / "static/awareflow-mark.svg"), layout="wide")
    apply_theme()
    try:
        config = Config.from_env()
        s = services(config)
        identities = s.directory.demo_identities()
        labels = {u["id"]: u for u in identities}
        with st.sidebar:
            brand()
            selected = st.selectbox("ผู้ใช้สาธิต", list(labels), format_func=lambda i: labels[i]["display_name"] + " · " + i, key="identity")
            actor = s.auth.select_user(selected)
            role = labels[selected]["role"]
            if role == "admin":
                pages = {"ภาพรวม": dashboard, "รอบอบรม": rounds_page, "ตรวจเนื้อหา": review_page, "อีเมล": outbox_page, "ผู้ใช้และตั้งค่า": settings_page, "ข้อสอบของฉัน": learner_page}
            elif role == "reviewer":
                pages = {"ภาพรวม": dashboard, "ตรวจเนื้อหา": review_page, "ข้อสอบของฉัน": learner_page}
            else:
                pages = {"ข้อสอบของฉัน": learner_page}
            navkey = "navigation:" + role
            if st.query_params.get("token") and not st.session_state.get("seen-token:" + selected):
                st.session_state[navkey] = "ข้อสอบของฉัน"
                st.session_state["seen-token:" + selected] = True
            if st.session_state.get(navkey) not in pages:
                st.session_state.pop(navkey, None)
            page = st.radio("เมนู", list(pages), key=navkey, format_func=navigation_label, width="stretch")
            st.divider()
            with st.expander("เกี่ยวกับบัญชีสาธิต", icon=":material/info:"):
                st.caption("ตัวตนสาธิต · ไม่มี SSO\nห้ามใช้ตัวเลือกนี้กับข้อมูลพนักงานจริง")
            st.caption("โมเดล: " + config.llm_provider + " · ค้นข้อมูล: " + config.search_provider)
            st.caption("เวลา Asia/Bangkok")
            if st.button("รีเฟรชข้อมูล", icon=":material/refresh:", use_container_width=True):
                st.rerun()
        if "flash" in st.session_state:
            st.success(st.session_state.pop("flash"))
        if config.data_class == "demo":
            st.caption("DEMO DATA / เนื้อหาและผู้ใช้สาธิต · ไม่มีการค้นเว็บหรือส่งอีเมลจริงโดยอัตโนมัติ")
        else:
            st.caption("CONNECTED PROVIDERS / ใช้บริการภายนอกที่ตั้งค่าไว้ · ผู้ใช้ยังเป็นบัญชีทดสอบ")
        with st.container(key="page_" + pages[page].__name__):
            pages[page](s, actor)
    except SchemaError as exc:
        fields = ", ".join(".".join(str(part) for part in e["loc"]) for e in exc.errors(include_input=False))
        st.error("ข้อมูลไม่ครบหรือรูปแบบไม่ถูกต้อง: " + fields)
    except WorkflowError as exc:
        st.error(str(exc))
    except sqlite3.IntegrityError:
        st.error("รายการซ้ำหรือขัดกับข้อมูลเดิม กรุณาตรวจรหัส/ชื่อและรีเฟรชข้อมูล")
    except Exception:
        st.error("ดำเนินการไม่สำเร็จ กรุณารีเฟรชและตรวจการตั้งค่าเซิร์ฟเวอร์ (ระบบไม่แสดงรายละเอียดที่อาจมี secrets)")
