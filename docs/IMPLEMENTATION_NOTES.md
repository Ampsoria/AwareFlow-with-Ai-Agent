# สิ่งที่ตรวจจริงและขอบเขต

อัปเดต 6 ตุลาคม 2026

## การทดสอบ

- Multi-provider REST: payload/auth ของ Gemini/Claude/Ollama/OpenAI-compatible, JSON modes, stamping provider จริง, Gemini Agent loop ด้วย HTTPX MockTransport, malformed/truncated outputs, redirect/key isolation, explicit LAN HTTP และ check-llm แบบไม่รัน tools

- Agent: เปลี่ยนคำค้นหลังผลว่าง, แก้ reference/PDF overflow, กันจบก่อนตรวจและกันใช้ผลตรวจร่างเก่า, scope/date guards, ปฏิเสธเครื่องมือส่งเมล/อนุมัติ/เผยแพร่แม้มีข้อความชี้นำใน source, งบ/คำค้นซ้ำ, trace หลัง restart/สิทธิ์, stale work และ redacted errors
- OpenAI native function calling contract ทดสอบผ่าน fake SDK ที่ตรวจ tool list/arguments/context ไม่ใช่ live smoke test; decision policies ใน executor tests เป็น scripted model doubles

- `scripts/check_schema.py`: schema ชั้นแรก 15 กรณี
- `unittest discover -s tests -v`: 74 กรณี (providers 15, Agent 14, core 30, integrations 12, Streamlit UI 3)
- UI acceptance: สร้าง → generation → ขอแก้ → generation → อนุมัติ → เผยแพร่ → EML → เปลี่ยนผู้ใช้ → ปฏิเสธคำตอบไม่ครบ → ส่งครบ → คะแนน → admin export; rerun ไม่เพิ่ม attempt ซ้ำ
- หน้า settings/schedules และโหลด demo ผ่าน Streamlit AppTest; เปิด Chrome ตรวจ dashboard บน server จริง
- CLI demo รันซ้ำไม่เพิ่มรอบ/version/attempt/outbox, DB rollback เมื่อ outbox insert ล้มเหลว, stale worker/lease เปลี่ยนเจ้าของ, concurrent duplicate submission, token คนอื่น/หมดอายุ/revoke
- เริ่ม migrations และสร้าง signing secret พร้อมกันหลาย thread โดยไม่ชน partial initialization
- OpenAI Responses structured parsing ผ่าน fake SDK, Tavily ผ่าน httpx MockTransport (domain/date/error), transient retry จนถึงขีดจำกัด, company factory แยก endpoint/credential และกัน mock/connected DB ปนกัน
- SMTP ผ่าน fake transport: require flags/allowlist, DATA outcome UNKNOWN, accepted แล้ว QUIT ล้มเหลว, expired lease, manual resolution และ idempotency
- first/latest, deadline, annual weighting, as_of และ CSV formula protection; malformed AI narrative ใช้ fallback
- PDF ไทยจริงตรวจจำนวนหน้า/ข้อความ และ render เป็น PNG ตรวจภาพ ใช้ PDFium เพราะเครื่องนี้ไม่มี Poppler

Transaction tests ส่วนใหญ่ใช้ FastPdf ที่ระบุว่าเป็น test double ส่วน RealArtifactTests และ UI acceptance ใช้ renderer จริง ไฟล์ตัวอย่างส่งให้ผู้ใช้ไม่ได้ใช้ test double

## ขอบเขตที่ยังต้องแยกให้ชัด

- OpenAI/Gemini/Claude/Ollama/OpenAI-compatible/Tavily/SMTP ยังไม่ได้ทดสอบกับบริการจริง ไม่มี API key/SMTP credentials และไม่ได้ส่งเมลออกระหว่างงานนี้
- Company API ต้องเพิ่ม decide_agent ที่เรียกโมเดลเลือก action และมี factory contract และตัวอย่างเท่านั้น ต้องเติม mapping ตามเอกสารของบริษัทก่อนเรียกได้
- บัญชีเลือกชื่อเป็น demo authentication ยังไม่มี SSO/login จริงตามขอบเขต FLOW; ไม่ควรนำ URL นี้ไปใช้แทน portal พนักงานจริง
- Mock เป็น fixture เพื่อทดสอบ workflow การแก้ตาม feedback เป็นตัวอย่าง deterministic ไม่ใช่ความสามารถสร้างเนื้อหาของโมเดลจริง
- Tavily `published_date` อาจหมายถึงวันเผยแพร่หรือแก้ไข จึงเก็บใน `provider_date` เท่านั้น ไม่ยกระดับเป็นวันเผยแพร่/วันเกิดเหตุ โหมด recent ปฏิเสธแหล่งที่ไม่มีวันที่ยืนยันด้วย NEEDS_INPUT; evergreen ใช้ทบทวนความรู้ได้
- AI summary เลือก intro/focus จากค่าที่อนุญาต ระบบใส่ตัวเลขเองจาก metrics เพื่อไม่ให้ AI แต่งสถิติ ไม่ใช่ free-form narrative
- Worker/scheduler มี continuous CLI และปุ่มเรียกครั้งเดียว แต่ไม่ได้ติดตั้งเป็น system service เครื่องต้องเปิดอยู่และใช้ config/data root เดียวกัน
- ติดตั้งแบบ editable จาก source checkout ยังไม่จัด migration/font resources สำหรับ standalone wheel และยังไม่ทำ production deployment

## เอกสารทางการที่ใช้

- [OpenAI function calling](https://developers.openai.com/api/docs/guides/function-calling): native tools, required choice และปิด parallel tool calls

- [Streamlit AppTest](https://docs.streamlit.io/develop/api-reference/app-testing): ทดสอบ interactions และ rerun
- [OpenAI structured outputs](https://platform.openai.com/docs/guides/structured-outputs): Responses parsing ด้วย Pydantic schema
- [Tavily search](https://docs.tavily.com/documentation/api-reference/endpoint/search): search response และข้อจำกัด published_date
- [Python smtplib](https://docs.python.org/3/library/smtplib.html): TLS/send_message และ failure outcomes
- [fpdf2 text shaping](https://py-pdf.github.io/fpdf2/TextShaping.html): HarfBuzz
- [fpdf2 text layout](https://py-pdf.github.io/fpdf2/Text.html): multi_cell/wrapping
- [PyThaiNLP tokenization](https://pythainlp.org/dev-docs/api/tokenize.html): newmm ไม่ดาวน์โหลดโมเดลตอนรัน
- [Sarabun font](https://github.com/google/fonts/tree/main/ofl/sarabun): Regular/Bold และ OFL ใน assets/fonts

รายละเอียด config และ official protocol references เพิ่มใน [PROVIDERS.md](PROVIDERS.md) เตรียม Gemini ก่อนตามผู้ใช้ และยังไม่ทราบ URL/โปรแกรมเสิร์ฟโมเดลของเครื่องพี่เลี้ยง
