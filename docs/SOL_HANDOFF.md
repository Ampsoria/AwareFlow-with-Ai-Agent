# ส่งต่องานให้ Sol

## คำขอล่าสุด: เปลี่ยนเป็น Agent

ผู้ใช้ต้องการให้ AI เลือกเครื่องมือ/ค้นเพิ่ม/แก้ร่างเอง จึงเพิ่ม AwarenessAgent แล้ว ดู [AGENT.md](AGENT.md) และ `application/agent.py` ไม่กลับไป hardcode search → generate ใน worker แยกการตัดสินใจของโมเดลกับเครื่องมือและ guard ของ server ให้ชัด Mock เป็นเพียง simulator และยังไม่ได้ live test

## หลาย API และ Gemini

เพิ่มตัวเชื่อม Gemini/Claude/Ollama/OpenAI-compatible พร้อมไฟล์ตัวอย่างและ check-llm ดู [PROVIDERS.md](PROVIDERS.md) ผู้ใช้จะเริ่มด้วย Gemini ก่อน รอผู้ใช้เติม key ใน .env และเลือก provider; ห้ามอ้างว่าทดสอบ live แล้ว

## สถานะที่รับงานต่อ

Workspace เริ่มจาก FLOW.md ไฟล์เดียว ตอนนี้ต้นแบบมี **Streamlit UI + ระบบหลัก + SQLite + PDF ไทยครบวงจรใน Mock Mode** แล้ว มี OpenAI/Tavily/SMTP adapters พร้อม mocked transport tests, settings/directory/schedule UI, worker heartbeat/backoff และ continuous worker/scheduler CLI ไม่ต้องสร้าง backend หรือ UI หลักใหม่

เริ่มที่ [README](../README.md), [CORE_GUIDE.md](CORE_GUIDE.md) และ [IMPLEMENTATION_NOTES.md](IMPLEMENTATION_NOTES.md) ทดสอบ 74 tests และ schema 15 checks แล้ว ตารางลำดับด้านล่างเก็บไว้เป็นเกณฑ์ออกแบบเดิม ไม่ใช่รายการงานที่ยังไม่ได้ทำทั้งหมด

งานเมื่อมีข้อมูล/สภาพแวดล้อมพร้อม:

1. Live smoke test OpenAI/Tavily/SMTP ด้วย credentials และกล่องเมลทดสอบที่กำหนด ยังไม่ได้เรียกจริงระหว่างพัฒนา
2. เติม Company API mapping ตามสเปกใน `examples/company_adapter.py`; ไม่สมมติว่าเป็น OpenAI-compatible
3. หากต้องใช้ข่าวล่าสุด เพิ่มแหล่งค้นที่ยืนยันวันเผยแพร่ได้ Tavily provider_date ยังคลุมเครือ ระบบจึงไม่ใช้ผ่าน recent filter
4. หากขยายจากต้นแบบเป็นระบบพนักงานจริง เพิ่ม authentication/deployment/service management และการสำรองข้อมูลตามสภาพแวดล้อมจริง

รักษา grading, immutable version, publication transaction, command receipts, SMTP UNKNOWN handling และ metrics เดิม อย่าย้าย logic เข้า session หรือปุ่ม Streamlit โค้ด UI ปัจจุบันเรียก service ที่ตรวจสิทธิ์ ไม่มี SQL ในหน้า UI

## ลำดับพัฒนา

ทำตามลำดับทีละช่วงและทำให้แต่ละช่วงรันตรวจได้ ห้ามเริ่มจาก UI ที่เก็บทุกอย่างใน session แล้วค่อยย้ายฐานข้อมูล

| ช่วง | งาน | เกณฑ์จบ |
|---|---|---|
| 1 — Bootstrap | pyproject/dependency lock, Python version, config, package, DB connection/migration runner, Thai README, init-db | ติดตั้งใน venv ใหม่, migrate ครั้งแรก/ซ้ำ, restart แล้วข้อมูลอยู่ครบ; live mode ปฏิเสธ demo auth |
| 2 — Domain | typed schemas, policies, repositories/UoW, MockLLM/MockSearch, demo auth/seed | seed ซ้ำไม่สร้างข้อมูลซ้ำ; validate bundle; guard transitions และ stale locks |
| 3 — Generate/review | manual create, jobs worker, mock generation, Thai PDF/font/license, review/revise/approve UI | สร้าง PDF ไทยจริง, revise สร้าง version ใหม่, ต้องอนุมัติใหม่, แหล่ง/บทอ่าน/ข้อสอบตรง version |
| 4 — Publish/quiz | publish transaction, assignments, preview outbox, signed links, learner DTO, grading/retakes | เดินครบตั้งแต่กดเผยแพร่ถึงผลสอบโดยไม่ใช้ credentials, ไม่มีเฉลยก่อน submit |
| 5 — Reports | round/year metrics, UTF-8 BOM CSV, PDF, deterministic summary และ optional AI narrative | ชุดตัวเลขด้านล่างตรง, no-data เป็น null, PDF/CSV/dashboard ใช้ snapshot เดียวกัน |
| 6 — Resilience | retry/leases/CAS, SMTP unknown, restart/rerun/concurrency checks, audit | ไม่มี duplicate logical round/email/attempt; งานค้างกู้ได้โดยไม่ข้ามอนุมัติ |
| 7 — Real adapters | อ่าน official docs, OpenAI adapter, search adapter, configurable SMTP, company stub | configuration errors ชัด; ทดสอบ mock transport ได้; live smoke test เฉพาะเมื่อมี credentials |
| 8 — Schedules/delivery | scheduler process, monthly/yearly jobs, localhost/deployment docs, final acceptance | รัน tick ซ้ำไม่สร้างรอบ/รายงานซ้ำ และไม่มี auto-approval/auto-publication |

ติดตั้งและทดสอบทั้ง core/Streamlit ด้วย Python 3.9.6 และ dependencies ใน requirements.lock แล้ว หากเปลี่ยน runtime/dependencies ให้ตรวจความเข้ากันได้และอัปเดต lock ตามผลทดสอบ

เมื่อเริ่มงาน PDF ให้อ่าน skill สำหรับ PDF ที่มีใน session และทำ render/visual QA โดยเฉพาะภาษาไทย หากยังไม่เลือก renderer ให้ทำตัวอย่าง 1 หน้าเพื่อยืนยันก่อนผูกกับ workflow ถ้าใช้ skill อื่นให้ทำตามเงื่อนไขที่เกี่ยวข้อง

## ชุดข้อมูลสาธิตที่ต้องสร้าง

ใช้ DEMO database/storage แยกกัน แสดงป้ายข้อมูลสาธิตทุกหน้า Seed มี stable IDs/keys ทำซ้ำได้โดยไม่รีเซ็ตข้อมูลที่ผู้ใช้ทดลองเพิ่มแล้ว

รอบ fixture ที่เผยแพร่แล้ว ข้อสอบ 5 ข้อ เกณฑ์ 80% เปิดสอบซ้ำ สมมติทุกข้อเฉลย A:

| ผู้ใช้ | ครั้งแรก | ครั้งล่าสุด | เวลาครั้งแรก | สถานะ |
|---|---|---|---|---|
| TEST001 | A,A,A,A,A = 5/5 | เหมือนเดิม | ก่อนกำหนด | ผ่าน |
| TEST002 | A,A,A,B,B = 3/5 | เหมือนเดิม | หลังกำหนด | ไม่ผ่าน |
| TEST003 | ยังไม่ทำ | — | — | ยังไม่ทำ |
| TEST004 | A,A,B,B,B = 2/5 | A,A,A,A,B = 4/5 | ก่อนกำหนด; ครั้งสองหลัง | ผ่าน, สอบซ้ำ |
| TEST005 | A,A,A,B,A = 4/5 | เหมือนเดิม | ก่อนกำหนด | ผ่าน |

Expected: assigned=5, completed=4, not_started=1, passed=3, failed=1, completion=80%, pass rate=75%, latest average=80%, first-attempt average=70%, on_time=3, late=1 และข้อ 4/5 ผิดสูงสุดเท่ากันข้อละ 2 คนจากผู้ทำแล้ว 4 คน การสอบซ้ำของ TEST004 ไม่เปลี่ยน on_time ให้กลายเป็น late

เพิ่มอย่างน้อยอีกหนึ่งรอบในปีเดียวกันเพื่อจับ annual aggregation ผิด ตัวอย่าง assigned=2/completed=1/passed=1/latest score=100% → รายปีรวม assigned=7, completed=5, passed=4, completion≈71.43%, pass rate=80%, average=84% จำนวน unique people ไม่จำเป็นเท่ากับ 7

มีรอบ DRAFT สำหรับทดลอง workflow ตั้งแต่ต้นและรอบ IN_REVIEW สำหรับดูหน้า review วันที่ fixture ต้องคงที่และ UI ระบุว่าเป็นข้อมูลจำลอง ไม่อ้างว่าเป็นข่าวปัจจุบัน

## Acceptance checklist เดิม

รายการด้านล่างเป็นเกณฑ์ตรวจสำหรับการเปลี่ยนแปลงครั้งถัดไป ดูหลักฐานที่รันจริงและข้อจำกัดใน IMPLEMENTATION_NOTES.md ไม่ถือว่าทดสอบบริการภายนอกจริงจากการผ่าน mock tests

- [ ] เปลี่ยน topic/query/window/domain/groups ผ่านแอดมินได้; ไม่เปลี่ยนรอบเผยแพร่เก่า
- [ ] Mock Mode รันได้โดยไม่มี AI/search/SMTP keys และไม่ network ออกเอง
- [ ] ไม่พบข้อมูลใหม่ → NEEDS_INPUT หรือเลือกทบทวนด้วยคน; ไม่มีแหล่ง/วันที่แต่งขึ้น
- [ ] AI malformed output ไม่กลายเป็น version พร้อมตรวจ; retry จำกัด; error ไม่เผย key
- [ ] Thai PDF ไม่มีตัวอักษรแตก/ตัดท้ายและประมาณ A4 1 หน้า พร้อมแหล่งอ้างอิงและ license
- [ ] Feedback → version ใหม่ → IN_REVIEW; version/approval/PDF เก่ายังตรวจย้อนหลังได้
- [ ] Approve จากหน้าที่ stale ถูกปฏิเสธ; publish ได้เฉพาะ current approved version
- [ ] กด publish ซ้ำ/worker retry → assignments และ outbox ไม่เพิ่มซ้ำ
- [ ] Preview แสดงไฟล์แนบ/ลิงก์จริงในระบบ แต่ไม่อ้างว่าส่ง SMTP แล้ว
- [ ] SMTP flag ปิดหรือผู้รับนอก allowlist → ไม่ส่ง; timeout ที่ผลไม่แน่นอน → UNKNOWN
- [ ] Token ของคนอื่น/หมดอายุ/ถูก revoke หรือ actor ไม่ตรง assignment → ใช้ไม่ได้
- [ ] Learner DTO/PDF/cache ไม่เปิดเผยเฉลยก่อนส่ง; auth check ใช้ทุก service
- [ ] ส่งคำตอบไม่ครบ/มีข้อเกิน/option ไม่ถูกต้อง → ไม่บันทึก
- [ ] กดส่งซ้ำด้วย key เดิม → attempt เดิม; key เดิมแต่เปลี่ยน payload → conflict
- [ ] Concurrent submit เมื่อห้าม retake → มีผลเพียงครั้งเดียว
- [ ] ผลผูก assignment → user/round/version ถูกชุด; grade ด้วยโค้ดและไม่เชื่อคะแนน client
- [ ] Retake เก็บทุกครั้ง; latest/first metrics และ disclosure เป็นไปตามนโยบาย
- [ ] ปิดรอบระหว่างเปิดฟอร์ม → submit ไม่ผ่าน; เกิน due แต่รอบเปิด → late
- [ ] Report fixture ตรงทุกค่า; ไม่รวมยังไม่ทำเป็น 0; denominator=0 แสดงไม่มีข้อมูล
- [ ] Year filter ใช้ published year ตาม Bangkok รวมรอบ closed และไม่เฉลี่ย rates แบบไม่ถ่วง
- [ ] CSV ไทยเปิด Excel ได้และ text cells ไม่รันสูตรจากข้อมูลนำเข้า
- [ ] AI narrative ผิดตัวเลข → fallback; ไม่เปลี่ยน metrics JSON
- [ ] Restart/rerun ไม่ลืมสถานะ; stale job และ expired lease ไม่ทับข้อมูลใหม่
- [ ] Scheduler tick ซ้ำสร้างรอบ/รายงานได้ครั้งเดียวและรอบใหม่ยังต้องผ่านคนอนุมัติ

## Mapping ข้อกำหนด FLOW.md

| FLOW | เจ้าของโมดูล | ช่วงงาน |
|---|---|---|
| 1 เทคโนโลยี/เก็บถาวร | config, bootstrap, db, storage, pdf | 1–3 |
| 2 AI/search/mock | llm, search, ports | 2, 7 |
| 3 ตั้งรอบ | rounds, schemas, admin UI | 2–3 |
| 4 สร้างชุด | generation, validators, pdf | 3 |
| 5 ตรวจ/อนุมัติ | review, versions, audit | 3–4 |
| 6 ผู้ใช้/อีเมล | auth, publication, delivery, outbox | 2, 4, 6–7 |
| 7 ข้อสอบ | quizzes, policies, attempts | 4, 6 |
| 8 รายงาน | reporting, exports, dashboard | 5 |
| 9 ตั้งเวลา | worker, scheduler, schedules | 6, 8 |
| 10 ส่งมอบ/ทดสอบ | README, fixtures, tests | ทุกช่วง |

## Prompt สำหรับเริ่มงานต่อ

> อ่าน FLOW.md, docs/CORE_GUIDE.md, docs/IMPLEMENTATION_NOTES.md และ docs/SOL_HANDOFF.md แล้วต่อหน้า Streamlit เข้ากับ core services ที่มีอยู่ รัน CLI demo และ tests ก่อนเริ่ม ไม่เขียน workflow/grading/report logic ซ้ำใน UI ไม่เก็บข้อมูลหลักใน session รักษา command keys และ expected_lock จากหน้าที่ผู้ใช้เห็น ทำ UI ให้เดินครบใน Mock Mode โดยใช้ Email Preview ไม่ขอ API key ในแชต อัปเดต README และทดสอบ UI จริง แยกสิ่งที่ทดสอบด้วย mock ออกจาก live integrations ที่ยังไม่ได้ทดสอบ
