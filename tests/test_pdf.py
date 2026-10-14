import io
from pathlib import Path
import unittest

from pypdf import PdfReader

from awareflow.bootstrap import FixedClock
from awareflow.demo import START, settings
from awareflow.domain.errors import ValidationError
from awareflow.infrastructure.mock import MockLLM, MockSearch
from awareflow.infrastructure.pdf import ThaiPdf


class LessonPdfTests(unittest.TestCase):
    def setUp(self):
        clock = FixedClock(START)
        self.sources = MockSearch(clock).search(settings())
        self.bundle = MockLLM(clock).generate(settings(), self.sources)
        self.renderer = ThaiPdf(Path(__file__).resolve().parents[1] / "assets/fonts")

    def expanded_bundle(self, repetitions):
        sentence = " ให้ตรวจสอบคำขอผ่านช่องทางที่รู้จักก่อนดำเนินการ และเก็บรายละเอียดไว้เพื่อแจ้งผู้รับผิดชอบ"
        return self.bundle.model_copy(update={"sections": [
            section.model_copy(update={"body": section.body + sentence * repetitions})
            for section in self.bundle.sections
        ]})

    def test_expanded_thai_lesson_with_three_references_fits_without_quiz(self):
        bundle = self.expanded_bundle(2)
        sources = [self.sources[0].model_copy(update={"source_id": f"S{i}",
            "url": "https://example.org/security/employee-awareness/verification-guide"}) for i in range(3)]
        document = PdfReader(io.BytesIO(self.renderer.lesson(bundle, sources)))
        self.assertEqual(len(document.pages), 1)
        text = document.pages[0].extract_text()
        self.assertIn("หน้า 1", text)
        self.assertIn("[3]", text)
        for section in bundle.sections:
            self.assertIn(section.heading, text)
        for question in bundle.questions:
            self.assertNotIn(question.prompt, text)

    def test_overflow_is_rejected_for_agent_revision_instead_of_truncation(self):
        with self.assertRaisesRegex(ValidationError, "ยาวเกิน A4"):
            self.renderer.lesson(self.expanded_bundle(8), self.sources)


if __name__ == "__main__":
    unittest.main()
