"""Thai PDFs with embedded OFL fonts and HarfBuzz shaping. No network access."""
from fpdf import FPDF
from pythainlp.tokenize import word_tokenize
from zoneinfo import ZoneInfo
from awareflow.domain.models import instant

from awareflow.domain.errors import ValidationError


class LessonDocument(FPDF):
    def footer(self):
        self.set_y(-15)
        self.set_draw_color(207, 219, 223)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.set_y(-13)
        self.set_font("Sarabun", "", 9)
        self.set_text_color(85, 105, 115)
        self.cell(0, 5, "AwareFlow  /  บทอ่านประกอบการอบรม")
        self.set_x(self.l_margin)
        self.cell(0, 5, f"หน้า {self.page_no()}", align="R")


class ThaiPdf:
    def __init__(self, font_dir):
        self.font_dir = font_dir

    def _document(self, title, demo=True, lesson=False):
        pdf = LessonDocument(format="A4") if lesson else FPDF(format="A4")
        pdf.set_margins(18, 16, 18)
        pdf.set_auto_page_break(True, margin=18)
        pdf.add_font("Sarabun", "", str(self.font_dir / "Sarabun-Regular.ttf"))
        pdf.add_font("Sarabun", "B", str(self.font_dir / "Sarabun-Bold.ttf"))
        pdf.set_text_shaping(True)
        pdf.add_page()
        pdf.set_title(title)
        pdf.set_author("AwareFlow")
        if lesson:
            pdf.set_fill_color(25, 89, 94)
            pdf.rect(18, 16, 2, 8, style="F")
            pdf.set_text_color(25, 89, 94)
            pdf.set_font("Sarabun", "B", 10)
            pdf.set_x(23)
            pdf.cell(0, 8, "AWAREFLOW / SECURITY AWARENESS", new_x="LMARGIN", new_y="NEXT")
            pdf.ln(3)
            self._text(pdf, title, 19, bold=True)
            pdf.ln(1)
            pdf.set_text_color(85, 105, 115)
            self._text(pdf, "ข้อมูลสาธิต - ไม่ใช่ข่าวล่าสุดที่ค้นจากเว็บ" if demo else "บทอ่านสำหรับพนักงาน | อ่านและทบทวนก่อนทำข้อสอบ", 9)
            pdf.ln(5)
            pdf.set_text_color(35, 49, 60)
            return pdf
        pdf.set_fill_color(232, 241, 247)
        pdf.set_text_color(25, 57, 79)
        pdf.set_font("Sarabun", "B", 13)
        pdf.cell(0, 10, "AWAREFLOW / SECURITY AWARENESS", fill=True, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3)
        self._text(pdf, "ข้อมูลสาธิต - ไม่ใช่ข่าวล่าสุดที่ค้นจากเว็บ" if demo else "เอกสารอบรม Security Awareness", 10)
        self._text(pdf, title, 20, bold=True)
        pdf.ln(2)
        return pdf

    def _text(self, pdf, text, size=12, bold=False):
        pdf.set_font("Sarabun", "B" if bold else "", size)
        pdf.set_x(pdf.l_margin)
        # Segment Thai words locally. fpdf2's CHAR mode alone can split a Thai word.
        line = ""
        available = pdf.w - pdf.l_margin - pdf.r_margin - 2 * pdf.c_margin
        for token in word_tokenize(str(text), engine="newmm", keep_whitespace=True):
            if line and pdf.get_string_width(line + token) > available:
                pdf.multi_cell(0, size * 0.52, line.rstrip(), new_x="LMARGIN", new_y="NEXT")
                line = token.lstrip()
            else:
                line += token
            # Long URLs have no Thai word boundary; explicit fallback prevents clipping.
            if pdf.get_string_width(line) > available:
                pdf.multi_cell(0, size * 0.52, line, wrapmode="CHAR", new_x="LMARGIN", new_y="NEXT")
                line = ""
        if line:
            pdf.multi_cell(0, size * 0.52, line.rstrip(), new_x="LMARGIN", new_y="NEXT")

    def lesson(self, bundle, sources):
        pdf = self._document(bundle.title, bundle.provenance.is_demo, lesson=True)
        # Measure the scenario with the same Thai wrapping used for final output.
        # Keep the full-width shaded block aligned with the document margins.
        card_top = pdf.get_y()
        with pdf.offset_rendering() as probe:
            probe.set_margins(22, 16, 22)
            probe.set_y(card_top + 3)
            self._text(probe, "ลองนึกถึงสถานการณ์นี้", 12, True)
            self._text(probe, bundle.scenario, 12)
            card_height = probe.get_y() - card_top + 3
        pdf.set_fill_color(239, 246, 245)
        pdf.rect(18, card_top, 174, card_height, style="F")
        pdf.set_margins(22, 16, 22)
        pdf.set_y(card_top + 3)
        self._text(pdf, "ลองนึกถึงสถานการณ์นี้", 12, True)
        self._text(pdf, bundle.scenario)
        pdf.set_margins(18, 16, 18)
        pdf.set_y(card_top + card_height + 4)
        for number, section in enumerate(bundle.sections, 1):
            pdf.set_text_color(25, 89, 94)
            self._text(pdf, f"{number:02d}  {section.heading}", 13, True)
            pdf.ln(1)
            pdf.set_text_color(35, 49, 60)
            self._text(pdf, section.body)
            pdf.ln(4)
        pdf.set_draw_color(207, 219, 223)
        pdf.line(18, pdf.get_y(), 192, pdf.get_y())
        pdf.ln(3)
        pdf.set_text_color(25, 89, 94)
        self._text(pdf, "จำไว้และนำไปใช้", 13, True)
        pdf.set_text_color(35, 49, 60)
        for action in bundle.employee_actions:
            self._text(pdf, "- " + action.text, 11)
        pdf.ln(3)
        pdf.set_text_color(85, 105, 115)
        self._text(pdf, "แหล่งอ้างอิง", 9, True)
        for number, source in enumerate(sources, 1):
            self._text(pdf, f"[{number}] " + source.title + " / " + source.publisher, 9)
            if source.url:
                self._text(pdf, source.url, 8)
            else:
                self._text(pdf, "เอกสารตัวอย่างในโปรเจกต์ ไม่มีการค้นเว็บจริง", 8)
        # Question objects, answer keys and explanations are intentionally never rendered.
        if pdf.pages_count != 1:
            raise ValidationError("บทอ่านยาวเกิน A4 หนึ่งหน้า กรุณาย่อแล้วสร้างใหม่")
        return bytes(pdf.output())

    def report(self, metrics):
        pdf = self._document(metrics["title"], metrics["is_demo"])
        self._text(pdf, "ข้อมูล ณ " + instant(metrics["as_of"]).astimezone(ZoneInfo("Asia/Bangkok")).strftime("%d/%m/%Y %H:%M") + " (เวลาไทย)", 9)
        if metrics.get("summary"):
            self._text(pdf, metrics["summary"], 11)
            pdf.ln(3)
        labels = [("ผู้ได้รับมอบหมาย (คน-รอบ)", "assigned"), ("ทำแล้ว", "completed"),
                  ("ยังไม่ทำ", "not_started"), ("ผ่าน", "passed"), ("ไม่ผ่าน", "failed"),
                  ("ทำครบ (%)", "completion_rate"), ("อัตราผ่าน (%)", "pass_rate"),
                  ("คะแนนเฉลี่ยล่าสุด (%)", "average_score"), ("คะแนนเฉลี่ยครั้งแรก (%)", "first_average_score"),
                  ("ส่งครั้งแรกทันกำหนด", "on_time"), ("ส่งครั้งแรกล่าช้า", "late")]
        for label, key in labels:
            value = metrics[key]
            shown = "ยังไม่มีข้อมูล" if value is None else (f"{value:.2f}" if isinstance(value, float) else str(value))
            self._text(pdf, label + ": " + shown)
        pdf.ln(4)
        self._text(pdf, "นิยาม: ผลปัจจุบันใช้ครั้งล่าสุด; ความตรงเวลาใช้ครั้งแรก", 10)
        self._text(pdf, "อัตราทำครบเทียบผู้ถูกมอบหมาย; อัตราผ่านเทียบผู้ทำแล้ว", 10)
        for report in metrics.get("rounds", [metrics]):
            pdf.ln(4)
            self._text(pdf, "ผลรายบุคคล / " + report["title"], 13, True)
            for p in report["people"]:
                score = "ยังไม่ทำ" if p["score"] is None else f'{p["score"]:.0f}%'
                self._text(pdf, f'{p["user_id"]}  {p["display_name"]}  |  {score}  |  {p["status"]}  |  {p["attempts"]} ครั้ง', 11)
            self._text(pdf, "การตอบผิดรายข้อ (ครั้งล่าสุด)", 12, True)
            for q in report["questions"]:
                self._text(pdf, f'{q["question_id"]}: {q["prompt"]} - ผิด {q["wrong"]}/{q["answered"]}', 10)
        return bytes(pdf.output())
