import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


ROOT = Path(__file__).resolve().parents[1]


class StreamlitTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = temp.name
        env = patch.dict(os.environ, {"DATA_ROOT": temp.name, "DATABASE_PATH": temp.name + "/ui.sqlite3",
            "LLM_PROVIDER": "mock", "SEARCH_PROVIDER": "mock", "APP_MODE": "demo", "EMAIL_REAL_SEND_ENABLED": "false"})
        env.start()
        self.addCleanup(env.stop)
        self.app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
        self.ok()

    def ok(self):
        self.assertFalse(self.app.exception, [x.message for x in self.app.exception])
        self.assertFalse(self.app.error, [x.value for x in self.app.error])

    def click(self, label):
        buttons = [b for b in self.app.button if b.label == label]
        self.assertEqual(len(buttons), 1, f"button {label}: {[b.label for b in self.app.button]}")
        buttons[0].click().run()
        self.ok()

    def nav(self, name):
        self.app.radio("navigation:admin").set_value(name).run()
        self.ok()

    def test_create_revise_approve_publish_submit_and_report(self):
        self.nav("รอบอบรม")
        self.click("เขียน Prompt ใหม่")
        self.app.text_input("new:title").set_value("รอบทดสอบผ่านหน้าจอ")
        prompt = "เตรียมบทเรียนการใช้ AI อย่างปลอดภัยสำหรับพนักงานใหม่ เน้นข้อมูลที่ไม่ควรใส่ในแชต"
        self.app.text_area("new:goal").set_value(prompt)
        self.click("บันทึกรอบ")
        self.click("ให้ Agent เตรียมชุดอบรม")
        self.click("ประมวลผลงานตอนนี้")
        self.assertTrue(any("ประวัติ Agent" in e.label for e in self.app.expander))
        import sqlite3
        with sqlite3.connect(self.root + "/ui.sqlite3") as db:
            import json
            settings = json.loads(db.execute("SELECT settings_json FROM training_rounds").fetchone()[0])
            self.assertEqual(settings["agent_goal"], prompt)
            self.assertEqual(settings["topic"], prompt)
            self.assertEqual(settings["queries"], [prompt])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM agent_steps WHERE tool='submit_for_review' AND status='SUCCEEDED'").fetchone()[0], 1)
        self.nav("ตรวจเนื้อหา")
        self.assertTrue(any(t.label == "การทำงานของ Agent" for t in self.app.tabs))
        feedback = next(w for w in self.app.text_area if w.label == "ข้อเสนอแนะให้ AI แก้ไข")
        feedback.set_value("เพิ่มสถานการณ์ให้เข้าใจง่าย")
        self.click("ให้ AI แก้ไข")
        self.nav("รอบอบรม")
        self.click("ประมวลผลงานตอนนี้")
        self.nav("ตรวจเนื้อหา")
        # AppTest.select_index uses formatted labels; set the actual option ID instead.
        import sqlite3
        with sqlite3.connect(self.root + "/ui.sqlite3") as db:
            current_version = db.execute("SELECT current_version_id FROM training_rounds").fetchone()[0]
        next(w for w in self.app.selectbox if w.label == "เวอร์ชัน").set_value(current_version).run()
        self.click("อนุมัติฉบับนี้")
        self.click("เผยแพร่รอบและสร้างคำเชิญ")
        self.nav("อีเมล")
        self.assertEqual(len(self.app.dataframe[0].value), 5)
        self.assertTrue((self.app.dataframe[0].value["status"] == "PREVIEW_READY").all())
        self.app.selectbox("identity").set_value("TEST001").run()
        self.ok()
        from awareflow.ui.theme import navigation_label
        self.assertEqual(self.app.radio("navigation:learner").options, [navigation_label("ข้อสอบของฉัน")])
        self.assertEqual([h.value for h in self.app.header], ["1. อ่านบทเรียน", "2. ทำแบบทดสอบ"])
        with sqlite3.connect(self.root + "/ui.sqlite3") as db:
            lesson = json.loads(db.execute("SELECT bundle_json FROM training_versions WHERE id=?", (current_version,)).fetchone()[0])
        displayed_text = [element.value for element in self.app.text]
        self.assertIn(lesson["scenario"], displayed_text)
        for section in lesson["sections"]:
            self.assertIn(section["body"], displayed_text)
        self.assertTrue(any(b.label == "ดาวน์โหลดบทเรียน PDF" for b in self.app.get("download_button")))
        # UI rejects incomplete answers before calling the service.
        next(b for b in self.app.button if b.label == "ส่งคำตอบ").click().run()
        self.assertIn("ครบทุกข้อ", self.app.error[0].value)
        for radio in self.app.radio:
            if radio.key and ":q" in radio.key:
                radio.set_value("A")
        self.click("ส่งคำตอบ")
        self.assertIn("100.0%", [m.value for m in self.app.metric])
        self.assertFalse(any("เฉลยและคำอธิบาย" == e.label for e in self.app.expander))
        self.app.run()  # rerun does not submit again
        self.assertTrue(any("ครั้งที่ 1" in c.value for c in self.app.caption))
        self.app.selectbox("identity").set_value("ADMIN").run()
        self.nav("ภาพรวม")
        self.click("สร้างรายงาน PDF และสรุป")
        self.assertTrue(any("ดาวน์โหลดรายงาน PDF" == b.label for b in self.app.get("download_button")))

    def test_settings_and_schedule_pages(self):
        self.nav("ผู้ใช้และตั้งค่า")
        self.assertTrue(any(w.label == "นโยบายภายใน" for w in self.app.text_area))
        self.assertFalse(any("API key" in w.label for w in self.app.text_input))
        self.click("ตรวจงานที่ถึงเวลาตอนนี้")
        self.assertTrue(any("0 รายการ" in x.value for x in self.app.success))

    def test_load_demo_and_learner_without_assignment(self):
        self.app.selectbox("identity").set_value("TEST001").run()
        self.ok()
        self.assertTrue(any("ยังไม่มีรอบ" in x.value for x in self.app.info))
        self.app.selectbox("identity").set_value("ADMIN").run()
        self.click("โหลดข้อมูลสาธิต")
        self.assertIn("80.0%", [m.value for m in self.app.metric])


if __name__ == "__main__":
    unittest.main()
