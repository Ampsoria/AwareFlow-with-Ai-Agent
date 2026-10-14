# ใส่ API ที่ไหน และต่อโมเดลแบบไหนได้บ้าง

แก้ **`.env` ที่ root ของโปรเจกต์** (โฟลเดอร์เดียวกับ app.py) เป็นไฟล์ภายในเครื่องที่ถูก .gitignore แล้ว ใส่ keys ฝั่งเซิร์ฟเวอร์ ไม่กรอกในหน้าเว็บ หลังแก้ต้อง restart หน้าเว็บและ worker เพราะ environment ที่โหลดแล้วไม่เปลี่ยนกลาง process

## เริ่มด้วย Gemini

เตรียม `.env` ให้แล้วโดยยังเลือก mock เพื่อให้แอปเดิมเปิดได้ เติม/แก้บรรทัดเหล่านี้เมื่อพร้อม:

```dotenv
LLM_PROVIDER=gemini
LLM_MODEL=gemini-3.8-flash
GEMINI_API_KEY=ใส่คีย์จริงในไฟล์นี้
LLM_API_KEY=
LLM_ENDPOINT=
LLM_OUTPUT_MODE=json
PROVIDER_TIMEOUT_SECONDS=120
SEARCH_PROVIDER=mock
EMAIL_TRANSPORT=preview
EMAIL_REAL_SEND_ENABLED=false
```

สร้างคีย์จาก [Google AI Studio](https://aistudio.google.com/apikey) ชื่อโมเดลตัวอย่างอ้างอิง [Google quickstart](https://ai.google.dev/gemini-api/docs/get-started) ที่ตรวจเมื่อ 6 ตุลาคม 2026 หากบัญชีใช้ชื่อรุ่นอื่นให้ใส่ชื่อที่บัญชีเข้าถึงได้ ไม่ต้องใส่ `models/` หน้าชื่อโมเดล การเรียก API อาจมีค่าใช้จ่ายตามบัญชี/รุ่นที่เลือก

มีตัวอย่างเต็มใน [gemini.env.example](../examples/env/gemini.env.example) ส่วน `LLM_API_KEY` เป็นช่องกลาง ถ้ามีค่าจะใช้แทน GEMINI_API_KEY จึงเว้นว่างเมื่อใช้ช่องชื่อ provider

ตรวจการเชื่อมต่อจาก terminal ในโปรเจกต์:

```bash
.venv/bin/python -m awareflow.cli check-llm
```

คำสั่งนี้เรียกโมเดลที่ตั้งค่าไว้ด้วยข้อความสังเคราะห์ 2 ครั้ง ตรวจ JSON และการเลือกเครื่องมือ แต่ **ไม่รันเครื่องมือที่โมเดลเลือก** ไม่ค้นเว็บ ไม่ส่งเมล ไม่สร้าง DB หรือแก้รอบอบรม หากผ่านจะได้ status OK ยังไม่ใช่การรับรองคุณภาพบทเรียนไทยทุกหัวข้อ

จากนั้นเปิดหน้าเว็บ:

```bash
.venv/bin/streamlit run app.py
```

หาก server เก่ารันที่พอร์ต 8501 อยู่ ให้หยุดด้วย Ctrl+C ใน terminal เดิมก่อนเริ่มใหม่ กรณีเคยรันด้วย DATA_ROOT/DATABASE_PATH แบบระบุในคำสั่ง ต้องเอาค่านั้นออกหรือเปลี่ยนเป็นชุด connected ใหม่ ห้ามใช้ DB mock เดิมกับ provider จริง

ค่าปกติเลือก `var/demo` เมื่อ LLM และ search เป็น mock ทั้งคู่ และเลือก `var/connected` เมื่อมี provider จริง จึงไม่ต้องระบุ DATA_ROOT/DATABASE_PATH เอง เว้นแต่ต้องการชุดข้อมูลเฉพาะ หากเคยระบุไว้ใน `.env` ให้แก้ทั้งสองค่าให้ตรงกันด้วย

`SEARCH_PROVIDER=mock` หมายถึงใช้ Gemini จริงกับแหล่งข้อมูลจำลองเพื่อทดสอบ ผลจะยังติดป้ายสาธิต หากต้องค้นเว็บจริงให้เปลี่ยนเป็น tavily พร้อม SEARCH_API_KEY ตาม README การเลือก Gemini ไม่ได้เปิด Google Search อัตโนมัติ

## ตัวเชื่อมที่มี

| LLM_PROVIDER | API ที่ใช้ | LLM_ENDPOINT | ช่องคีย์ | การเลือกเครื่องมือของ Agent |
|---|---|---|---|---|
| mock | ไม่เรียก API | ไม่ใช้ | ไม่ใช้ | จำลองเพื่อทดสอบ |
| gemini | Gemini GenerateContent REST | เว้นว่าง = https://generativelanguage.googleapis.com/v1beta | GEMINI_API_KEY | โมเดลคืน JSON decision |
| openai | OpenAI Responses | เว้นว่าง = default ของ SDK; custom base ลงท้าย /v1 | OPENAI_API_KEY | Native function calling |
| anthropic | Claude Messages | เว้นว่าง = https://api.anthropic.com/v1 | ANTHROPIC_API_KEY | Native tool_use |
| openai_compatible | Chat Completions | ต้องระบุ เช่น http://127.0.0.1:1234/v1 | LLM_API_KEY ถ้า server ต้องใช้ | โมเดลคืน JSON decision |
| ollama | Ollama /api/chat | เว้นว่าง = http://127.0.0.1:11434 | LLM_API_KEY ถ้า server ต้องใช้ | โมเดลคืน JSON decision |
| company | factory ตามสเปกบริษัท | COMPANY_API_ENDPOINT | env ที่ COMPANY_CREDENTIAL_ENV ระบุ | adapter implement decide_agent |

`LLM_API_KEY` ที่ไม่ว่างใช้แทนช่องคีย์ของ OpenAI/Claude/Gemini และใช้กับ compatible/Ollama ได้ ส่วน company ใช้ credential ของบริษัทแยกต่างหาก ไม่มีการเอา OPENAI_API_KEY ไปส่ง Ollama โดยอัตโนมัติ

เลือกใช้งานหนึ่ง provider ต่อ process ในรุ่นนี้ ไม่ fallback ไปบริการอื่นเมื่อ API ล้มเหลว ไม่ฝึกโมเดล/โหลด weights ลงเครื่องให้เอง

## เครื่องพี่เลี้ยงหรือโมเดลของตัวเอง

เครื่องที่เปิดทิ้งไว้เป็นเครื่องเสิร์ฟโมเดล แต่ต้องทราบ **โปรแกรมหรือรูปแบบ API + URL/port + ชื่อโมเดล + ต้องใช้คีย์ไหม** ก่อนเลือกค่าที่ถูกต้อง โมเดลชื่อเดียวกันอาจถูกเสิร์ฟด้วย API ต่างกันได้

ตัวอย่าง Ollama บนเครื่องพี่เลี้ยง (IP นี้เป็นเพียงตัวอย่าง):

```dotenv
LLM_PROVIDER=ollama
LLM_ENDPOINT=http://192.168.1.20:11434
LLM_MODEL=ชื่อโมเดลที่ติดตั้งบนเครื่องพี่เลี้ยง
LLM_API_KEY=
LLM_OUTPUT_MODE=json_schema
LLM_ALLOW_INSECURE_HTTP=true
PROVIDER_TIMEOUT_SECONDS=120
```

ถ้าเป็น LM Studio/vLLM หรือ API แบบ Chat Completions:

```dotenv
LLM_PROVIDER=openai_compatible
LLM_ENDPOINT=http://192.168.1.20:8000/v1
LLM_MODEL=ชื่อโมเดลที่เซิร์ฟเวอร์เปิดให้ใช้
LLM_API_KEY=
LLM_OUTPUT_MODE=json
LLM_ALLOW_INSECURE_HTTP=true
```

ใช้ URL ที่พี่เลี้ยงให้จริง พอร์ตตัวอย่างไม่ใช่การตรวจพบ server `localhost` หมายถึงเครื่องที่รัน AwareFlow จึงใช้แทน IP เครื่องพี่เลี้ยงไม่ได้ ต้องมี API service ที่เครื่องนี้เข้าถึงได้ผ่าน LAN/VPN ด้วย การเปิดโปรแกรมโมเดลทิ้งไว้ไม่ได้แปลว่าเปิด API ให้เครื่องอื่นแล้ว

HTTP บน localhost ใช้ได้โดยไม่ตั้ง flag ส่วน HTTP บน LAN ต้องเปิด LLM_ALLOW_INSECURE_HTTP ชัดเจน HTTPS ไม่ต้องเปิด flag แนะนำใช้ HTTPS เมื่อ server รองรับ ระบบไม่ตาม HTTP redirect และไม่ยอมให้ใส่ key ใน URL

ถ้าเป็นโมเดล fine-tuned ของตัวเอง ให้เสิร์ฟผ่านหนึ่งใน protocol ที่รองรับแล้วใส่ชื่อโมเดลตาม server หาก API มี field/auth/response เฉพาะ ใช้ [company_adapter.py](../examples/company_adapter.py) หรือ subclass `StructuredLLM` แล้ว implement `_parse(schema, system, data)` เพื่อใช้การเขียนบทเรียน/JSON decisions ร่วมกันได้ ตัวอย่าง custom factory ไม่ใช่การรองรับ wire format ทุกแบบโดยอัตโนมัติ

## JSON และความสามารถของโมเดล

Ollama, compatible และ Gemini ใช้ `LLM_OUTPUT_MODE`:

- `json_schema`: ส่ง schema ให้ server กำกับรูปแบบ ใช้เมื่อ server/model รองรับ
- `json`: ขอ JSON และแนบ schema ใน prompt
- `text`: ไม่ส่ง response-format parameter เหมาะกับ server ที่รับแต่ chat text; โมเดลยังต้องคืน JSON ตาม prompt

ทุกโหมดตรวจข้อมูลด้วย Pydantic และกติกาเดิมก่อนรันเครื่องมือ/บันทึกบทเรียน ไม่มีการเชื่อคะแนนหรือสิทธิ์จาก JSON โมเดลต้องทำตามคำสั่งและสร้าง JSON ได้ การเปิด API ของโมเดลใด ๆ จึงไม่ได้รับประกันว่าจะทำงานเป็น Agent ได้ดี ภาษาไทย คุณภาพคำตอบ และความสามารถตาม schema ต้องทดสอบกับโมเดลที่เลือก

`LLM_MAX_OUTPUT_TOKENS` ค่าเริ่มต้น 6000 (256–32768) ช่วยกำหนดขนาด output ถ้า API ตอบตัดกลางทางจะถูกปฏิเสธ ไม่ใช้ partial JSON หาก server ไม่รองรับ json_schema ให้เปลี่ยนเป็น json/text เอง ไม่มี silent fallback

## จุดเชื่อมในโค้ดและการทดสอบ

- `config.py`: อ่านและตรวจ provider/model/endpoint/key
- `infrastructure/providers.py`: registry `create_llm()` และ OpenAI/company
- `infrastructure/http_llm.py`: compatible, Ollama, Claude, Gemini adapters
- `infrastructure/llm_base.py`: บทเรียน/summary/JSON decision contract ร่วมกัน
- `application/diagnostics.py`: check-llm โดยไม่รันเครื่องมือ
- `tests/test_providers.py`: HTTPX MockTransport ทดสอบ payload/auth, JSON modes, Agent loop ผ่าน Gemini, malformed/truncated outputs, provider provenance, HTTP/redirect rules และไม่เผย secrets

ทดสอบด้วยบริการจำลองเท่านั้น ยังไม่ได้ใช้ API key จริงหรือเชื่อมเครื่องพี่เลี้ยง ไม่มีการอ้างว่า live Gemini/Claude/Ollama/LM Studio/vLLM ผ่านแล้ว

เอกสารทางการที่ใช้: [Gemini structured output](https://ai.google.dev/gemini-api/docs/generate-content/structured-output), [Gemini REST](https://ai.google.dev/api/generate-content), [Claude Messages](https://platform.claude.com/docs/en/api/messages/create), [Ollama chat](https://docs.ollama.com/api/chat), [vLLM serving](https://docs.vllm.ai/en/latest/serving/online_serving/)
