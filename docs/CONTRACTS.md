# สัญญาข้อมูลและบริการ

เอกสารนี้เป็นสัญญาเป้าหมายของระบบ มี typed models, services, UI และ OpenAI/Tavily/SMTP adapters แล้ว ดู signatures ปัจจุบันใน [CORE_GUIDE.md](CORE_GUIDE.md) และ `src/awareflow/` บางชื่อ/field เช่น date_precision เป็นแบบเป้าหมาย ไม่ใช่ interface ที่ implement ทุกตัว Tavily date เก็บใน provider_date โดยไม่อ้างเป็น published_at; AI narrative ใช้ schema จำกัดประเด็นและให้ server ใส่ตัวเลข ดูขอบเขตทดสอบใน [IMPLEMENTATION_NOTES.md](IMPLEMENTATION_NOTES.md)

Agent contract ปัจจุบันอยู่ที่ `domain/agent.py`: `decide_agent(context)` เลือกหนึ่ง tool action ก่อน executor ตรวจ arguments/ขอบเขต Provider บริษัทต้อง implement เพิ่มตาม [AGENT.md](AGENT.md) RoundSettings เพิ่ม optional `agent_goal` (ไม่ serialize เมื่อว่างเพื่อคง receipts เก่า)

## 1. Convention

- ID เป็น UUID string ยกเว้น `user.id` ที่ต้นแบบใช้ TEST001–TEST005; ห้าม encode อีเมลเป็น primary key
- Timestamp เป็น UTC RFC3339 ที่ normalize รูปแบบเดียวกัน; แสดงผลด้วย ZoneInfo("Asia/Bangkok")
- JSON ทุกชนิดมี `schema_version: 1`; serializer ไม่เก็บ secrets; schema จำกัดข้อความและขนาด response
- `Actor(user_id, role, mode)` มาจาก AuthProvider ฝั่งเซิร์ฟเวอร์ ไม่ใช่ form fields
- ทุก command ที่ผู้ใช้กดซ้ำได้มี command/idempotency key และ expected lock/version ตามความเหมาะสม
- service errors: ValidationError, Forbidden, NotFound, Conflict, InvalidTransition, NoSuitableSources, ProviderUnavailable, ProviderConfigurationError; UI ไม่แสดง raw exception ที่มี request headers

## 2. RoundSettings

```text
schema_version: 1
title: nonempty string
topic: nonempty string
queries: list[str]                        # ผู้ดูแลแก้ query ที่ AI เสนอได้
search_window: {start_date, end_date}     # inclusive, Asia/Bangkok; resolve lookback ตอนสร้างรอบ
allowed_domains: list[{hostname, include_subdomains: bool}]
target_group_ids: list[str]
question_count: int [3, 5] = 5
pass_percent: int [1, 100] = 80
due_at: UTC timestamp
allow_retakes: bool
company_context: {policy_text?, reporting_channel?}
content_mode: recent | evergreen
```

Settings เป็น snapshot ต่อรอบ อนุญาตแก้ก่อนเผยแพร่และทำให้ต้องสร้าง/ตรวจ version ใหม่ เมื่อสร้างจาก template ต้อง resolve วันใหม่และ copy company context ลง snapshot การแก้ company settings ไม่แก้ PDF รุ่นเก่า กำหนดส่งต้องอยู่หลังเวลาเผยแพร่

DB มี question_count, pass_percent, due_at, allow_retakes ซ้ำกับ settings เพื่อใช้ constraints/query; repository เป็นผู้ serialize จาก typed settings ชุดเดียว ต้องตรวจค่าให้ตรงกันก่อนเขียน/อ่าน

## 3. SourceRecord และ ContentBundle

```text
SourceRecord:
  source_id, url, title, publisher
  published_at?: timestamp/date
  event_at?: timestamp/date
  updated_at?: timestamp/date
  retrieved_at: timestamp
  date_precision: per-field date | datetime | unknown
  excerpt: string                         # ข้อความที่ใช้จริง; จำกัดความยาว
  content_hash: string
  provenance: mock | live
  eligibility_reason: string

ContentBundle:
  schema_version: 1
  language: th
  title: string
  scenario: string
  sections: list[{section_id, heading, body, source_ids: list[str]}]
  employee_actions: list[{text, source_ids: list[str]}]
  questions: list[Question]
  source_ids: list[str]
  change_summary?: string
  provenance: {llm_provider, model, search_provider, is_demo, generated_at}

Question:
  question_id: string                     # unique ภายใน version
  prompt: string
  options: list[{option_id: A|B|C|D, text: string}]
  correct_option_id: A|B|C|D
  explanation: string
  supporting_section_ids: nonempty list[str]
```

ตัว validator ต้องตรวจ: จำนวนข้อเท่ากับ settings, ID ไม่ซ้ำ, มี 4 ตัวเลือกและข้อความไม่ซ้ำ, correct option มีอยู่จริง, supporting sections/source IDs อ้างอิงได้, source URL เป็นรายการที่ค้นได้จริง, ไม่เพิ่มวันที่/นโยบายบริษัทที่ไม่มี input การตรวจเชิงความหมายใช้คนตรวจเป็นหลัก ไม่อ้างว่า validator พิสูจน์ความถูกต้องเชิงข้อเท็จจริงได้

`TrainingVersion` เก็บ bundle + SourceRecord list + RoundSettings snapshot + PDF path/SHA256 + parent version + validation metadata เป็นหน่วย immutable ผลค้น mock ต้องติดป้ายตั้งแต่ UI, PDF, outbox ถึง report ไม่เติม URL ปลอมให้ดูเป็นหลักฐานสด อาจใช้แหล่งความรู้ตัวอย่างที่ระบุชัดว่าไม่ได้ค้นล่าสุด

## 4. Ports

| Port | Methods / output | ข้อกำหนด |
|---|---|---|
| LLMProvider | suggest_queries(topic), generate(request) → bundle, revise(bundle, feedback, sources) → bundle, summarize(metrics) → narrative | ไม่รู้จัก DB/UI; ไม่คำนวณคะแนน |
| SearchProvider | search(SearchRequest) → SearchResult(sources, warnings, provenance) | ไม่ซ่อนการไม่มีผลลัพธ์; ไม่พึ่ง LLM ค้นเอง |
| PdfRenderer | render_lesson(version_input), render_report(snapshot) → PdfArtifact(path, sha256, page_count) | ไทยอ่านได้, ติดป้าย demo, ไม่มีเฉลยใน lesson PDF |
| EmailTransport | prepare(message) → PreviewArtifact; send(message) → SendResult | Preview transport ห้าม network; SMTP outcome accepted/rejected/unknown |
| AuthProvider | current_identity() → Actor; authorize_assignment(actor, assignment) | ตรวจ role และ user binding ฝั่ง server |
| FileStore | put_atomic(bytes, key), read(key), exists(key) | ไม่รับ path traversal; ไม่ expose directory ที่มีไฟล์เฉลย |
| UnitOfWork | transaction(), repositories | ทำงานข้ามตารางแบบ atomic |
| Clock | now_utc() | inject เพื่อทดสอบ deadline/scheduler |

Provider configuration แยกตามชนิด เช่น OpenAIConfig(api_key_env, model, endpoint?) กับ CompanyConfig(endpoint, credential_env, auth_scheme, request_mapping) ไม่ใช้ OpenAI SDK กับ company โดยอัตโนมัติ Company adapter ที่ยังไม่มี spec ต้อง fail ด้วย ProviderConfigurationError และข้อความบอกว่าต้องเติม contract ห้ามเงียบแล้ว fallback mock

MockLLM/MockSearch ต้อง deterministic และใส่ provenance ชัด การเลือก live provider แล้ว credential ขาดต้องแสดง configuration error ไม่สลับเป็น mock โดยไม่บอก

ก่อนเขียน OpenAI adapter ต้องอ่านเอกสารทางการปัจจุบันและบันทึก URL/วันที่ใน implementation notes ส่วน company adapter รอเอกสาร request/response/auth/error ของบริษัทโดยพัฒนาส่วนอื่นต่อได้ ไม่มีข้อสมมติเรื่อง API compatibility

## 5. Application commands

```text
create_round(actor, settings, creation_key) -> round_id
update_round(actor, round_id, expected_lock, settings) -> new_lock
request_generation(actor, round_id, expected_lock, command_key) -> job_id
request_revision(actor, round_id, version_id, expected_lock, feedback, command_key) -> job_id
approve_version(actor, round_id, version_id, expected_lock, command_key) -> approval_event_id
publish_round(actor, round_id, version_id, expected_lock, command_key) -> publication
close_round(actor, round_id, expected_lock, reason) -> round
get_student_quiz(actor, signed_link) -> StudentQuiz
begin_submission(actor, assignment_id) -> submission_key
submit_attempt(actor, assignment_id, submission_key, answers) -> StudentResult
prepare_smtp_delivery(actor, assignment_id, command_key) -> outbox_id
get_round_metrics(actor, round_id, as_of) -> RoundMetrics
get_annual_metrics(actor, year, as_of) -> AnnualMetrics
```

`StudentQuiz` ใช้ allowlist fields: assignment_id, version_id, title, due_at, round_status, is_demo, allow_retakes, lesson และ questions[{question_id,prompt,options}] ห้าม serialize ContentBundle แล้วค่อยลบเฉลยแบบกระจัดกระจาย

`lesson` มีเฉพาะ title, scenario, sections[{heading,body}], employee_actions[text] และ sources[{title,publisher,url}] อ่านจาก training_version ที่ assignment ตรึงไว้เช่นเดียวกับ PDF หลังตรวจสิทธิ์และ signed link ผู้เรียนอ่านบทเรียนบนเว็บก่อนทำแบบทดสอบด้านล่างได้ทั้งก่อนและหลังส่งคำตอบ ไม่มี answer key หรือข้อมูลภายในของ Agent ส่งมากับบทอ่าน PDF ดาวน์โหลดผ่าน `lesson_pdf` และ submission_key ออกผ่าน `begin_submission` แยกกัน

`submit_attempt` ไม่รับ score, passed, user_id, question_count หรือ submitted_at จาก client อ่านค่าจาก assignment/version/clock บนเซิร์ฟเวอร์ ตรวจ exact set ของ question IDs และ option IDs ไม่รับข้อขาด/ข้อเกิน

Link ที่หมดอายุออกใหม่ได้โดยไม่เปลี่ยน assignment/version ใช้ token_generation สำหรับเพิกถอนลิงก์เก่า secret เป็น env ฝั่ง server; demo bootstrap สร้าง secret เฉพาะเครื่องแล้วเก็บในไฟล์ private ที่ ignore ห้ามสร้างใหม่ทุก rerun ห้ามใส่ query token ลง log

## 6. Failure และ retry

| กรณี | ผลที่ต้องได้ |
|---|---|
| Search ไม่พบข่าวเข้าเกณฑ์ | NEEDS_INPUT, ไม่สร้างแหล่ง/ข่าวเอง |
| Timeout/429/temporary 5xx | retry แบบจำกัดพร้อม backoff; สูงสุด JOB_MAX_ATTEMPTS รวมครั้งแรก |
| Credentials/config invalid | ไม่ retry อัตโนมัติ; แจ้งชื่อ config ที่ขาดโดยไม่โชว์ค่า |
| AI JSON invalid | validation error; อนุญาตแก้รูปแบบใน retry budget เดียวกัน |
| PDF render fail/เกินหน้า | ไม่มี version ที่พร้อมอนุมัติ; เก็บ error และ retry/ย่อได้ |
| Stale job/หน้าอนุมัติเก่า | Conflict; ไม่เขียนทับหรืออนุมัติ current version ใหม่ |
| SMTP unknown | หยุด auto retry; ผู้ดูแล resolve แบบ audit ได้ |
| Worker ตาย | lease recovery; ห้าม job เก่าที่ lease หมด commit ทับเจ้าของใหม่ |
| Narrative ผิดตัวเลข | deterministic template summary; metrics เดิมไม่เปลี่ยน |

## 7. Contract ของฐานข้อมูล

`0001_initial.sql` เป็น migration ครั้งแรก ห้ามแก้ schema ของ DB ที่ติดตั้งแล้วด้วยการรันไฟล์เดิมซ้ำ ให้สร้าง migration ใหม่และใช้ schema_migrations ใน migration runner

Schema นี้บังคับโครงสร้าง, การอ้างอิงข้ามรอบ, uniqueness, immutable versions/attempts และ publication approval ส่วน service ยังต้องรับผิดชอบ role, state transitions, JSON validation, consistency ของ settings, optimistic lock, deadline, SMTP allowlist และ latest-attempt queries การมี schema อย่างเดียวไม่ใช่ระบบที่พร้อมรับผู้ใช้
