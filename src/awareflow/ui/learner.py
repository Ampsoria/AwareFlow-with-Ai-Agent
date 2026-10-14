import streamlit as st

from .common import STATUS, local_time


def learner_page(s, actor):
    st.title("ข้อสอบของฉัน")
    st.caption("อ่านบทเรียนบนหน้านี้ แล้วทำแบบทดสอบด้านล่าง")
    token = st.query_params.get("token")
    if token:
        st.caption("กำลังเปิดคำเชิญจากลิงก์ กรุณาเลือกผู้ใช้สาธิตให้ตรงกับผู้รับ")
        if st.button("ดูงานทั้งหมดของฉัน"):
            st.query_params.clear()
            st.rerun()
        quiz = s.quizzes.get_student_quiz(actor, token)
    else:
        assignments = s.quizzes.list_assignments(actor)
        if not assignments:
            st.info("ยังไม่มีรอบอบรมที่มอบหมายให้คุณ")
            return
        mapping = {a["id"]: a for a in assignments}
        selected = st.selectbox("รอบที่ได้รับมอบหมาย", list(mapping), format_func=lambda i: mapping[i]["title"], key="my_assignment:" + actor.user_id)
        token = s.quizzes.own_link(actor, selected)
        quiz = s.quizzes.get_student_quiz(actor, token)
    aid = quiz["assignment_id"]
    st.subheader(quiz["title"])
    st.caption("กำหนดส่ง " + local_time(quiz["due_at"]) + " · " + STATUS[quiz["round_status"]])
    if quiz["is_demo"]:
        st.info("เนื้อหาสาธิต ไม่ใช่ข้อมูลล่าสุดที่ค้นจากเว็บ")
    lesson = quiz["lesson"]
    st.header("1. อ่านบทเรียน")
    if lesson["title"] != quiz["title"]:
        st.subheader(lesson["title"])
    with st.container(key="lesson_scenario"):
        st.subheader("ลองนึกถึงสถานการณ์นี้")
        st.text(lesson["scenario"])
    for section in lesson["sections"]:
        st.subheader(section["heading"])
        st.text(section["body"])
    st.subheader("จำไว้และนำไปใช้")
    for action in lesson["employee_actions"]:
        st.text("• " + action)
    with st.expander("แหล่งอ้างอิง"):
        for index, source in enumerate(lesson["sources"], 1):
            st.text(f'{index}. {source["title"]} / {source["publisher"]}')
            if source["url"]:
                st.link_button("เปิดแหล่งอ้างอิง " + str(index), source["url"])
            else:
                st.caption("เอกสารตัวอย่างในโปรเจกต์ ไม่มีการค้นเว็บจริง")
    st.download_button("ดาวน์โหลดบทเรียน PDF", s.quizzes.lesson_pdf(actor, token), "lesson.pdf", "application/pdf", icon=":material/download:")
    st.divider()
    st.header("2. ทำแบบทดสอบ")
    st.caption(f'จำนวน {len(quiz["questions"])} ข้อ · เลือกคำตอบที่เหมาะสมที่สุดข้อละหนึ่งคำตอบ')
    result = s.quizzes.get_result(actor, aid)
    active_key = "taking:" + actor.user_id + ":" + aid
    if result["status"] != "NOT_STARTED":
        a, b = st.columns(2)
        a.metric("คะแนนล่าสุด", f'{result["score"]:.1f}%')
        b.metric("ผลการสอบ", STATUS[result["status"]])
        st.caption(f'ครั้งที่ {result["attempt_number"]} · ส่ง {local_time(result["submitted_at"])} · ' + ("ทันกำหนด" if result["on_time"] else "ล่าช้า"))
        if result.get("explanations"):
            with st.expander("เฉลยและคำอธิบาย", expanded=True):
                for q in result["explanations"]:
                    st.markdown("**" + q["prompt"] + "**")
                    st.write("คำตอบ " + q["correct_option_id"] + " · " + q["explanation"])
        elif quiz["allow_retakes"]:
            st.caption("เปิดสอบซ้ำอยู่: เฉลยรายข้อจะแสดงหลังปิดรอบ เพื่อให้การสอบซ้ำยังวัดความเข้าใจ")
        if quiz["allow_retakes"] and quiz["round_status"] == "PUBLISHED" and not st.session_state.get(active_key):
            if st.button("เริ่มสอบอีกครั้ง"):
                st.session_state[active_key] = True
                st.rerun()
        if not st.session_state.get(active_key):
            return
    if quiz["round_status"] != "PUBLISHED":
        st.info("รอบนี้ปิดรับคำตอบแล้ว")
        return
    ticket = s.quizzes.begin_submission(actor, aid)
    with st.form("quiz:" + ticket):
        answers = {}
        for index, question in enumerate(quiz["questions"], 1):
            choices = {o["option_id"]: o["text"] for o in question["options"]}
            answers[question["question_id"]] = st.radio(f'{index}. {question["prompt"]}', list(choices), index=None,
                format_func=lambda value, options=choices: value + ". " + options[value], key=ticket + ":" + question["question_id"])
        if st.form_submit_button("ส่งคำตอบ", type="primary", icon=":material/check:"):
            if any(value is None for value in answers.values()):
                st.error("กรุณาตอบให้ครบทุกข้อก่อนส่ง")
            else:
                s.quizzes.submit_attempt(actor, aid, ticket, answers)
                st.session_state[active_key] = False
                st.session_state["flash"] = "บันทึกคำตอบแล้ว"
                st.rerun()
