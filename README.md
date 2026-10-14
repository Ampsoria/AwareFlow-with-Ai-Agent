# AwareFlow

ต้นแบบ AI Agent สำหรับ Security Awareness ตาม [FLOW.md](FLOW.md) ใช้ Python + Streamlit + SQLite รันครบตั้งแต่สร้างบทอ่าน ตรวจแก้ อนุมัติ เผยแพร่ ทำข้อสอบ จนถึงรายงาน มี Mock Mode ที่ไม่ต้องใช้ API key และไม่ส่งเมลออก

## เริ่มใช้งาน

ทดสอบด้วย Python 3.9.6 บนเครื่องพัฒนา ติดตั้งจาก source checkout นี้:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install -e '.[qa]'
.venv/bin/streamlit run app.py
```

เปิด **http://localhost:8501** เลือก ADMIN ที่แถบซ้าย แล้วกดโหลดข้อมูลตัวอย่างบนหน้าภาพรวม หรือสร้างรอบเองที่หน้า “รอบอบรม” เปลี่ยนเป็น TEST001–TEST005 เพื่อทดลองทำข้อสอบ ข้อมูลเก็บถาวรใน `var/demo/` และไม่หายเมื่อ refresh/restart

เส้นทางทดลอง: สร้างรอบและกำหนดเป้าหมาย → ให้ Agent เตรียมชุดอบรม → ประมวลผลงาน → ดูประวัติเครื่องมือ → ตรวจบทอ่าน/ข้อสอบ → ขอแก้ถ้าต้องการ → อนุมัติ → เผยแพร่ → ตรวจ Email Preview → เปลี่ยนผู้ใช้ → ทำข้อสอบ → กลับ ADMIN ดูรายงาน กดประมวลผลงานจากหน้าจอได้โดยไม่ต้องเปิด worker แยก

บัญชีแบบเลือกชื่อเป็น **demo authentication** ตามขอบเขตต้นแบบ ทุกหน้ามีป้ายแจ้ง ไม่ใช่ SSO หรือระบบเข้าสู่ระบบสำหรับพนักงานจริง ค่าเริ่มต้นเปิดเฉพาะเครื่องนี้ ลิงก์ localhost ในอีเมลจึงใช้ได้บนเครื่องที่รันเท่านั้น

## Agent ทำงานอย่างไร

แกนใหม่อยู่ที่ [application/agent.py](src/awareflow/application/agent.py) ให้โมเดลเลือกเครื่องมือค้นข้อมูล เขียน/แก้ร่าง ตรวจร่างและ PDF หรือหยุดขอข้อมูลเพิ่มตามผลที่ได้ เมื่อพร้อมจึงส่งให้คนตรวจ ไม่มีสิทธิ์อนุมัติ เผยแพร่ หรือส่งเมลเอง ดู [AGENT.md](docs/AGENT.md) สำหรับแผนภาพและสัญญาเครื่องมือ

หน้า “รอบอบรม” มีช่อง **เป้าหมายที่ให้ Agent ทำ** และประวัติการเรียกเครื่องมือเก็บในฐานข้อมูล ใน Mock Mode ใช้ตัวจำลองการเลือกเครื่องมือ ไม่ใช่โมเดล AI ตัดสินใจจริง ส่วน OpenAI adapter ใช้ native function calling และต้องมี credentials ก่อนเรียกจริง

จำกัดต่อ attempt: 12 ขั้น, ค้น 5 ครั้ง, เขียน/แก้ 3 ครั้ง ตั้งผ่าน `AGENT_MAX_STEPS`, `AGENT_MAX_SEARCHES`, `AGENT_MAX_DRAFTS`; ถึงขีดจำกัดแล้วขอข้อมูลจากผู้ดูแล ไม่วนไม่สิ้นสุด

## สิ่งที่ทำงานแล้ว

- หน้าภาพรวม รอบอบรม ตรวจอนุมัติ Email Preview/SMTP ตั้งค่า และหน้าผู้สอบ แยกสิทธิ์ใน service
- จัดการผู้ใช้ กลุ่ม บริบทองค์กร และกำหนดการรายเดือน/รายปี พร้อมพัก schedule
- คิวสร้าง/ขอแก้เนื้อหา ตรวจ typed schema และ PDF ไทยหนึ่งหน้า; เก็บทุก version และประวัติอนุมัติ
- เผยแพร่แบบ transaction ตรึง version เกณฑ์สอบ กำหนดส่ง และรายชื่อ; ป้องกันกดซ้ำ/หน้าจอเก่า
- ลิงก์ลงนาม submission tickets ตรวจคำตอบด้วยโค้ด สอบซ้ำ และเก็บ first/latest results
- Email Preview เป็น EML แนบ PDF/ลิงก์รายบุคคล; SMTP ต้องเปิดชัดเจนและผู้รับอยู่ใน allowlist
- รายงานรอบ/ปี dashboard, CSV ภาษาไทย, PDF และ snapshot ถาวร ใช้ตัวเลขที่คำนวณจากฐานข้อมูล
- AI summary เลือกประเด็นจาก schema ที่จำกัด; ระบบใส่ตัวเลขเอง และใช้ template เมื่อ AI ใช้ไม่ได้
- Worker มี heartbeat, lease recovery และ bounded exponential backoff สำหรับ transient provider errors
- Migrations/secret bootstrap รองรับเริ่มหลาย process พร้อมกัน; ข้อมูล mock กับ connected แยกฐานข้อมูล

ข้อมูลตัวอย่างมี 5 คน: ทำแล้ว 4 ยังไม่ทำ 1 ผ่าน 3 ไม่ผ่าน 1 ทำครบ 80% อัตราผ่าน 75% คะแนนเฉลี่ยล่าสุด 80% ครั้งแรก 70% ทันกำหนด 3 ล่าช้า 1 เป็นข้อมูลจำลอง ไม่ใช่ข่าวหรือผลสอบจริง Mock revision เพิ่มตัวอย่างแบบกำหนดไว้ ไม่ได้ตีความ feedback เหมือนโมเดลจริง

## เริ่มทดลอง Gemini / โมเดลที่รันเอง

ใส่ API key ใน **`.env` ที่ root โปรเจกต์** เตรียมไฟล์ให้แล้วโดยยังเลือก mock เติม `GEMINI_API_KEY` แล้วเปลี่ยน `LLM_PROVIDER=gemini`; ชื่อโมเดลตัวอย่างเตรียมไว้ใน `LLM_MODEL` จากนั้น restart แอป ใช้คำสั่งตรวจการเชื่อมต่อก่อน:

```bash
.venv/bin/python -m awareflow.cli check-llm
```

คำสั่งนี้ส่งข้อความทดสอบสองครั้งไปยัง provider ที่เลือก ตรวจ JSON/การเลือกเครื่องมือ ไม่รันเครื่องมือหรือส่งเมล ดู [PROVIDERS.md](docs/PROVIDERS.md) สำหรับขั้นตอน Gemini และเครื่องพี่เลี้ยง พร้อม [ไฟล์ตัวอย่าง Gemini](examples/env/gemini.env.example)

รองรับ `openai`, `gemini`, `anthropic`, `openai_compatible` (เช่น LM Studio/vLLM), `ollama` และ company factory โมเดลที่ไม่มี native function calling เลือกเครื่องมือด้วย JSON ซึ่งตรวจ schema/สิทธิ์ก่อนรัน รองรับ HTTP localhost และ HTTP LAN ที่เปิดผ่าน `LLM_ALLOW_INSECURE_HTTP=true` ชัดเจน

## Worker และ scheduler

เปิด terminal เพิ่ม โดยใช้ environment/DATA_ROOT เดียวกับหน้าเว็บ:

```bash
.venv/bin/python -m awareflow.cli worker --interval 5
.venv/bin/python -m awareflow.cli scheduler --interval 30
```

แต่ละคำสั่งรันต่อเนื่องจนกด Ctrl+C เครื่องต้องเปิดอยู่ scheduler สร้างรอบฉบับร่างและรายงานตาม schedule ไม่อนุมัติ/เผยแพร่/ส่งเมลแทนผู้ดูแล ยังไม่ได้ติดตั้งเป็น system service

คำสั่งเรียกครั้งเดียวและ CLI demo:

```bash
.venv/bin/python -m awareflow.cli init-db
.venv/bin/python -m awareflow.cli worker-once
.venv/bin/python -m awareflow.cli scheduler-once
.venv/bin/python -m awareflow.cli demo
```

CLI demo ใช้วันที่ fixture เดือนตุลาคม 2026 คงที่ ส่วนปุ่มตัวอย่างใน UI วางรอบย้อนหลังจากวันที่โหลดครั้งแรก เรียกซ้ำไม่สร้างผลซ้ำ และไม่ล้างรอบที่สร้างเอง

## การตั้งค่าและบริการภายนอก

คัดลอก [.env.example](.env.example) เป็น `.env` แล้วแก้ค่า แอปอ่านอัตโนมัติ โดย environment ของ process มีลำดับสูงกว่า หลังเปลี่ยน provider ให้ restart ทั้งเว็บและ worker เก็บ secrets ฝั่งเซิร์ฟเวอร์เท่านั้น

- `DATA_ROOT` / `DATABASE_PATH`: ฐานข้อมูลและไฟล์ของชุดเดียวกัน ค่าอัตโนมัติคือ `var/demo` หรือ `var/connected` ตาม provider หากกำหนดเองแล้วเปลี่ยนจาก mock เป็น provider จริง **ใช้เส้นทางใหม่ทั้งคู่** ระบบปฏิเสธการปนชุดข้อมูล
- `APP_BASE_URL`: URL ของหน้าเว็บที่ใส่ในลิงก์; `APP_SIGNING_SECRET` เว้นว่างได้ ระบบสร้าง secret เฉพาะเครื่องใน `DATA_ROOT/.signing-secret` สิทธิ์ 0600 ต้องเก็บไว้คู่กับ DB หากต้องการให้ลิงก์เก่ายังใช้ได้
- OpenAI: `LLM_PROVIDER=openai`, `OPENAI_API_KEY`, `LLM_MODEL` เป็นรุ่นที่รองรับ function calling และ structured outputs; `LLM_ENDPOINT` เป็น optional API base URL ใช้ Responses API และไม่ส่งคำตอบ/รายชื่อผู้สอบไปสร้างรายงาน
- Gemini/Claude/Ollama/OpenAI-compatible: ดู [PROVIDERS.md](docs/PROVIDERS.md) สำหรับ base URL, key, JSON modes และข้อกำหนดของ server
- Company API: `LLM_PROVIDER=company`, `COMPANY_ADAPTER_FACTORY=package.module:build_llm`, endpoint และ credential env แยกจาก OpenAI ดู [ตัวอย่าง adapter](examples/company_adapter.py) ต้องเติม request/response mapping ตามสเปกบริษัทก่อนใช้ ไม่สมมติว่า API บริษัทเป็น OpenAI-compatible
- ค้นเว็บ: `SEARCH_PROVIDER=tavily`, `SEARCH_API_KEY`, optional `SEARCH_ENDPOINT` (URL ของ search endpoint เต็ม) จำกัด domains และไม่ให้ LLM ประดิษฐ์แหล่งอ้างอิง
- **วันที่จาก Tavily อาจเป็นวันเผยแพร่หรือวันแก้ไข** จึงเก็บเป็น `provider_date` ไม่อ้างเป็นวันเกิดเหตุ/เผยแพร่ที่ยืนยันแล้ว โหมด `recent` จะเข้า NEEDS_INPUT หากไม่มีวันที่ยืนยัน ใช้ `evergreen` เพื่อทบทวนความรู้ หรือเพิ่ม search adapter ที่ยืนยันวันที่ได้ก่อนใช้เป็นข่าวล่าสุด
- SMTP: ตั้ง `EMAIL_TRANSPORT=smtp` พร้อม `EMAIL_REAL_SEND_ENABLED=true`, `SMTP_HOST`, `SMTP_FROM`, TLS และ `EMAIL_TEST_ALLOWLIST` เป็นอีเมลคั่นด้วย comma จากนั้นเตรียม/กดส่งในหน้าอีเมล ต้องแก้อีเมลผู้ใช้ก่อนเผยแพร่ เพราะ assignment เก็บ recipient snapshot ถ้าผลส่งไม่แน่นอนจะเป็น UNKNOWN ให้ตรวจผลและบันทึกการตัดสินใจก่อน retry ไม่มีการส่งซ้ำอัตโนมัติ

OpenAI/Gemini/Claude/Ollama/OpenAI-compatible/Tavily/SMTP มี adapter และทดสอบผ่าน transport จำลองแล้ว **ยังไม่ได้ยืนยันกับ API/SMTP จริง** เพราะไม่มี credentials ในงานนี้ `APP_MODE` ยังเป็น `demo` แม้เลือกบริการภายนอก เนื่องจากใช้บัญชีทดสอบ; เปลี่ยนเป็นระบบผู้ใช้จริงต้องทำ authentication เพิ่ม

## ตรวจสอบและอ่านโค้ด

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/check_schema.py
.venv/bin/python -m pip check
```

มี 74 tests ครอบคลุม Agent decisions/tools/guards, core/concurrency, integrations จำลอง และ Streamlit AppTest พร้อม schema checks อีก 15 กรณี UI test เดินจริงตั้งแต่ขอแก้/อนุมัติ/เผยแพร่จนส่งข้อสอบและออกรายงาน

- [AGENT.md](docs/AGENT.md): แกน Agent เครื่องมือ ขอบเขต และการเชื่อมโมเดล
- [CORE_GUIDE.md](docs/CORE_GUIDE.md): โครงสร้าง services และฐานข้อมูล
- [ARCHITECTURE.md](docs/ARCHITECTURE.md): กติกาธุรกิจและแบบระบบ
- [CONTRACTS.md](docs/CONTRACTS.md): สัญญาข้อมูลและ integrations
- [IMPLEMENTATION_NOTES.md](docs/IMPLEMENTATION_NOTES.md): หลักฐานทดสอบและข้อจำกัด
- [SOL_HANDOFF.md](docs/SOL_HANDOFF.md): สถานะส่งต่องานและข้อที่ต้องรักษา

ติดตั้งแบบ editable จาก source; ยังไม่ได้จัดแพ็กเกจ standalone wheel หรือ deployment สำหรับ production การแชร์ข้ามเครื่องต้องกำหนด host/base URL และเพิ่ม authentication ที่เหมาะสมก่อนใช้ข้อมูลพนักงานจริง
