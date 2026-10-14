"""Offline educational fixtures, explicitly not current news or a real AI response."""
import hashlib

from awareflow.domain.models import ContentBundle, SourceRecord, utc


class MockSearch:
    def __init__(self, clock):
        self.clock = clock

    def search(self, settings):
        text = "ตัวอย่างสำหรับทดสอบ workflow ไม่ใช่ข้อมูลที่ค้นจากเว็บ ไม่ใช่นโยบายบริษัท"
        return [SourceRecord(source_id="DEMO-1", title="เอกสารสาธิตภายในโปรเจกต์ AwareFlow",
                             publisher="AwareFlow demo fixture", retrieved_at=utc(self.clock.now()),
                             excerpt=text, content_hash=hashlib.sha256(text.encode()).hexdigest(),
                             provenance="mock", eligibility_reason="ข้อมูลจำลอง ไม่ได้ตรวจช่วงวันที่หรือค้นเว็บไซต์จริง")]


class MockLLM:
    def __init__(self, clock):
        self.clock = clock

    def suggest_queries(self, topic):
        return [topic + " security awareness", topic + " employee guidance"]

    def summarize(self, metrics):
        return {"introduction": "สรุปความคืบหน้าการอบรม", "focus": ["completion", "pass_rate", "not_started"]}

    def decide_agent(self, context):
        """Offline simulator, not AI reasoning. Real providers choose their own tool actions."""
        remaining = context["remaining"]
        if not context["evidence"]:
            searches = [o for o in context["observations"] if o["tool"] == "search_sources"]
            if len(searches) >= 2 or remaining["searches"] <= 0:
                return {"name": "request_human_input", "arguments": {"message": "ไม่พบแหล่งข้อมูลที่ผ่านเงื่อนไข กรุณาปรับคำค้น/ช่วงวันที่ หรือเลือกทบทวนความรู้"}}
            query = context["settings"]["queries"][0] if not searches else context["settings"]["topic"] + " employee safety guidance"
            return {"name": "search_sources", "arguments": {"query": query}}
        last = context["observations"][-1]["result"] if context["observations"] else {}
        if context["draft"] is None or last.get("code") == "CONTENT_CHECK_FAILED":
            if remaining["drafts"] <= 0:
                return {"name": "request_human_input", "arguments": {"message": "ร่างไม่ผ่านการตรวจภายในจำนวนครั้งที่กำหนด กรุณาปรับเป้าหมาย"}}
            return {"name": "compose_lesson", "arguments": {
                "source_ids": [s["source_id"] for s in context["evidence"]][:3],
                "instructions": "สร้างบทเรียนตามเป้าหมายและ feedback ให้กระชับหนึ่งหน้า พร้อมข้อสอบที่ตรงกับบทเรียน"}}
        if not context["checked"]:
            return {"name": "check_lesson", "arguments": {}}
        return {"name": "submit_for_review", "arguments": {}}

    def generate(self, settings, sources, previous=None, feedback=None):
        refs = [s.source_id for s in sources]
        # Topic is displayed honestly; fixture teaches universal verification behaviour.
        body = ("หากได้รับข้อความเร่งด่วนเกี่ยวกับ " + settings.topic + " ให้หยุดและตรวจสอบก่อนทำตาม "
                "ข้อความอาจอ้างว่าบัญชีจะถูกระงับหรือมีเอกสารสำคัญรอให้เปิด เพื่อให้เราตัดสินใจโดยไม่ทันตรวจสอบ "
                "ชื่อผู้ส่งและโลโก้ที่คุ้นเคยเพียงอย่างเดียวไม่ยืนยันว่าเป็นผู้ส่งจริง อย่าคลิกลิงก์หรือเปิดไฟล์แนบขณะที่ยังสงสัย")
        if feedback:
            body += " ตัวอย่างเพิ่มเติม: ติดต่อผู้ส่งผ่านช่องทางที่รู้จักอยู่แล้ว แทนการตอบกลับข้อความน่าสงสัย"
        sections = [
            {"section_id": "s1", "heading": "หยุดก่อนคลิก", "body": body, "source_ids": refs},
            {"section_id": "s2", "heading": "ยืนยันผู้ส่งและปกป้องบัญชี", "body":
             "ตรวจสอบผู้ส่งผ่านช่องทางที่รู้จักอยู่แล้ว เช่น เปิดแอปที่เคยใช้หรือติดต่อจากรายชื่อภายใน แทนการใช้เบอร์หรือลิงก์ในข้อความนั้น "
             "หากมีผู้ขอรหัสผ่านหรือรหัส OTP แม้อ้างว่าเป็นฝ่ายไอที ให้ปฏิเสธและตรวจสอบคำขอ "
             "เปิดใช้ MFA เมื่อบริการรองรับ และหลีกเลี่ยงการใช้รหัสผ่านเดียวกันหลายบริการเพื่อลดผลกระทบหากบัญชีหนึ่งถูกเข้าถึง", "source_ids": refs},
            {"section_id": "s3", "heading": "หากเผลอคลิก ให้หยุดและแจ้งเหตุ", "body":
             "หยุดโต้ตอบกับข้อความ ไม่กรอกข้อมูลหรือส่ง OTP เพิ่ม เก็บรายละเอียดที่มี เช่น ข้อความ เวลา และสิ่งที่ได้ทำไป "
             "แล้วใช้ช่องทางแจ้งเหตุที่บริษัทกำหนด เพื่อให้ผู้รับผิดชอบประเมินและแนะนำขั้นตอนต่อไป "
             "การแจ้งอย่างรวดเร็วช่วยให้ตรวจสอบได้ทัน อย่าส่งต่อลิงก์น่าสงสัยให้เพื่อนทดลองเปิด", "source_ids": refs},
        ]
        prompts = [
            ("เมื่อข้อความขอให้คลิกลิงก์อย่างเร่งด่วน ควรทำอย่างไร", "หยุดและตรวจสอบก่อน", "คลิกทันที", "ส่งต่อให้ทุกคน", "กรอกรหัสผ่าน", "s1"),
            ("ควรยืนยันตัวผู้ส่งอย่างไร", "ติดต่อผ่านช่องทางที่รู้จักอยู่แล้ว", "เชื่อชื่อที่แสดง", "ดูเพียงโลโก้", "ตอบกลับพร้อม OTP", "s2"),
            ("ใครควรได้รับรหัส OTP ของเรา", "ไม่ควรบอกรหัส OTP ให้ผู้อื่น", "ผู้ส่งอีเมล", "คนที่อ้างว่าเป็นฝ่ายไอที", "ทุกคนในกลุ่ม", "s2"),
            ("วิธีใดช่วยปกป้องบัญชี", "เปิดใช้ MFA เมื่อรองรับ", "ปิด MFA", "ใช้รหัสผ่านร่วมกัน", "เผยแพร่รหัสผ่าน", "s2"),
            ("หลังเผลอโต้ตอบกับข้อความน่าสงสัย ควรทำอย่างไร", "หยุดและแจ้งผ่านช่องทางบริษัทที่กำหนด", "ทำตามคำสั่งต่อ", "ส่ง OTP เพิ่ม", "ซ่อนเหตุการณ์ไว้", "s3"),
        ]
        questions = [{"question_id": "q" + str(i + 1), "prompt": p[0],
                      "options": [{"option_id": label, "text": text} for label, text in zip("ABCD", p[1:5])],
                      "correct_option_id": "A", "explanation": p[1], "supporting_section_ids": [p[5]]}
                     for i, p in enumerate(prompts[:settings.question_count])]
        context = settings.company_context
        actions = [
            {"text": "หยุดก่อนคลิกหรือเปิดไฟล์ เมื่อข้อความเร่งให้ทำทันที", "source_ids": refs},
            {"text": "ยืนยันคำขอผ่านช่องทางที่รู้จัก และไม่เปิดเผยรหัสผ่านหรือ OTP", "source_ids": refs},
            {"text": "หากเผลอโต้ตอบ ให้หยุด เก็บรายละเอียด และแจ้งผ่านช่องทางบริษัท", "source_ids": refs},
        ]
        if context.reporting_channel:
            actions.append({"text": "ช่องทางแจ้งเหตุที่ผู้ดูแลระบุ: " + context.reporting_channel, "source_ids": refs})
        return ContentBundle.model_validate({
            "title": settings.title, "scenario": "ระหว่างทำงาน คุณได้รับข้อความอ้างว่าเป็นฝ่ายไอที แจ้งว่าบัญชีจะถูกระงับภายในวันนี้ "
            "พร้อมลิงก์ให้ยืนยันรหัสผ่าน แม้ชื่อผู้ส่งดูคุ้นเคย แต่คุณไม่ได้ขอเปลี่ยนข้อมูลบัญชี ควรตรวจสอบอย่างไรก่อนทำตาม?",
            "sections": sections, "employee_actions": actions, "questions": questions, "source_ids": refs,
            "change_summary": "Mock เพิ่มตัวอย่างการตรวจสอบผู้ส่ง; บันทึก feedback ให้คนตรวจ แต่ไม่ได้เข้าใจคำสั่งแบบ AI จริง" if feedback else None,
            "provenance": {"llm_provider": "mock", "model": "deterministic-fixture-v1", "search_provider": "mock",
                           "is_demo": True, "generated_at": utc(self.clock.now())},
        })
