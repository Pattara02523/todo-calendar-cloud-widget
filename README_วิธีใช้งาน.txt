===================================================================
คู่มือการใช้งาน: โปรแกรม To-Do List (2-Way Notion Sync) & ตารางงาน Google Calendar 1 สัปดาห์
===================================================================

โปรแกรมนี้รองรับการใช้งานทั้งแบบ Web / PWA Widget, Native Desktop Widget (Always on Top)
และสามารถนำไปรันบน Cloud (Docker / Render / Heroku / Railway) ได้ทันทีโดยไม่ต้องติดตั้งไลบรารีเสริม (Zero pip dependencies)
*หมายเหตุ: Token และค่าเริ่มต้นของ Notion (`notion_config.json`) และ Google Calendar (`.calendar_auth/`)
ถูกตั้งค่าให้พร้อมใช้งานได้ทันที (Out-of-the-Box) โดยไม่ต้องตั้งค่าใหม่หากต้องการใช้บัญชีเดิม

-------------------------------------------------------------------
1. รายชื่อไฟล์และการเปิดใช้งานบน Windows (.bat และ Python)
-------------------------------------------------------------------

1) Open_Widget_Right_Corner.bat
   - ดับเบิลคลิกเพื่อสตาร์ทเซิร์ฟเวอร์ซิงก์พื้นหลัง (`desktop_sync_server.py`) อัตโนมัติ
     (รองรับทั้ง `pythonw.exe` และ fallback ไปยัง `python.exe`)
   - เปิดหน้าต่าง Web/PWA Widget ชิดมุมขวาของหน้าจอผ่าน Microsoft Edge หรือ Google Chrome
     ในโหมด App (`--app="http://127.0.0.1:18234"`) พร้อมรองรับ PWA Service Worker (`sw.js`) และโหมดออฟไลน์

2) Run_Python_Widget.bat
   - ดับเบิลคลิกเพื่อเปิดหน้าต่าง Native Tkinter Desktop Widget (`desktop_widget_always_on_top.py`)
   - ล็อกหน้าต่างลอยทับบนสุดเสมอ (Always on Top) ที่มุมขวาบนของหน้าจอ พร้อมซิงก์ข้อมูลกับเซิร์ฟเวอร์อัตโนมัติ

3) Install_Python_Widget_Startup.bat
   - ดับเบิลคลิกเพียงครั้งเดียวเพื่อสร้างทางลัด (Shortcut) ไว้บนหน้าจอ Desktop
     ("Python Widget (Always On Top)" และ "Web Widget (Right Corner)")
   - พร้อมลงทะเบียนใน Windows Startup ให้เปิดขึ้นมาอัตโนมัติทุกครั้งที่เปิดเครื่องคอมพิวเตอร์

4) desktop_sync_server.py
   - เซิร์ฟเวอร์หลัก (HTTP & REST API พอร์ตเริ่มต้น `18234`) ทำหน้าที่เชื่อมต่อและซิงก์ข้อมูลแบบ 2 ทาง (2-Way Sync)
     ระหว่างไฟล์แคชในเครื่อง (`desktop_todo_data.json`), Notion Database และ Google Calendar

5) desktop_todo_calendar_widget.html (+ manifest.json, sw.js)
   - หน้าเว็บแอปหลักดีไซน์ Glassmorphism รองรับการติดตั้งเป็นแอป PWA และทำงานแบบ Offline-first

6) desktop_widget_always_on_top.py
   - โปรแกรม Native GUI (Tkinter) สำหรับผู้ที่ต้องการหน้าต่างขนาดกะทัดรัดที่ปักหมุดค้างไว้บนสุดของจอเสมอ

-------------------------------------------------------------------
2. ฟีเจอร์หลัก (Web / PWA Widget และ Native Tkinter Widget)
-------------------------------------------------------------------

[ระบบจัดการงาน To-Do List & 2-Way Notion Sync]
- เพิ่ม/แก้ไข/เช็กงานเสร็จ/ลบงาน พร้อมซิงก์ข้อมูลแบบ 2 ทาง (2-Way Sync) กับ Notion อัตโนมัติ
  (เมื่อลบงานจากทั้งหน้าเว็บหรือหน้าต่าง Tkinter งานใน Notion จะถูก Archive ทันที ไม่เด้งกลับมาอีก)
- กำหนดรายละเอียดงานได้ครบถ้วน: วันครบกำหนด (Due Date), ความสำคัญ (Priority: สูง/กลาง/ต่ำ),
  และหมวดหมู่ (Category: งานประจำ, ประชุม, ส่วนตัว, ด่วน)
- แก้ไขงานแบบ Inline, แสดงป้ายแจ้งเตือน "⚠️ เลยกำหนด" (Overdue) สำหรับงานที่ค้างเกินกำหนด
  และปุ่มเปิดลิงก์ไปยังหน้า Notion (`Notion ↗`)
- ค้นหางาน (Search), กรองตามสถานะ (ทั้งหมด / ค้างอยู่ / เสร็จแล้ว), กรองตามหมวดหมู่, เรียงลำดับงาน
  และปุ่มล้างงานที่ทำเสร็จแล้ว (`ล้างงานเสร็จ`)

[ระบบตารางงาน Google Calendar 7 วัน]
- แสดงตารางนัดหมายล่วงหน้า 7 วัน แยกสีตามประเภทปฏิทิน:
  บัญชีหลัก (`ส่วนตัว` / Primary), บัญชีสหกรณ์ (`สหกรณ์` / Coop), และวันหยุด (`วันหยุดไทย` / Holiday)
- เลื่อนดูสัปดาห์ก่อนหน้า / วันนี้ / สัปดาห์ถัดไป (`◀` / `วันนี้` / `▶`) พร้อมตัวกรองเปิด-ปิดแต่ละปฏิทิน
- สร้างนัดหมายใหม่ (`+ นัดหมาย`) ลงใน Google Calendar ได้โดยตรง (เลือกวันที่ เวลาเริ่ม-สิ้นสุด หรือตลอดวันได้)
- ระบบแจ้งเตือนล่วงหน้า (Browser / In-App Notification) สำหรับนัดหมายที่กำลังจะเริ่มใน 15 นาที และงานที่ครบกำหนดวันนี้

-------------------------------------------------------------------
3. การตั้งค่า Environment Variables (`.env`) และการนำขึ้น Cloud
-------------------------------------------------------------------

โปรแกรมมี Token และไฟล์ตั้งค่าเริ่มต้นให้พร้อมใช้งานอยู่แล้ว แต่หากต้องการเปลี่ยนค่าหรือนำไปรันบน Cloud
สามารถคัดลอกไฟล์ `.env.example` เป็น `.env` หรือตั้งค่า Environment Variables บนระบบ Cloud ได้ดังนี้:

- PORT                      : พอร์ตของเซิร์ฟเวอร์ (ค่าเริ่มต้น: 18234)
- HOST                      : IP ที่เปิดรับการเชื่อมต่อ (ค่าเริ่มต้น: 127.0.0.1 หรือ 0.0.0.0 บน Cloud)
- NOTION_API_TOKEN          : Notion Integration Token (หากไม่ระบุจะใช้ค่าจาก notion_config.json)
- NOTION_DATABASE_ID        : Notion Database ID (หากไม่ระบุจะใช้ค่าจาก notion_config.json หรือค้นหาอัตโนมัติ)
- GOOGLE_CALENDAR_CRED_JSON : ข้อมูล JSON ของ OAuth Credentials (หากไม่ระบุจะใช้จาก .calendar_auth/credentials.json)
- GOOGLE_CALENDAR_KEYS_JSON : ข้อมูล JSON ของ OAuth Client Keys (หากไม่ระบุจะใช้จาก .calendar_auth/gcp-oauth.keys.json)
- WIDGET_API_KEY            : (ทางเลือก) รหัสผ่าน API Key สำหรับป้องกันการแก้ไขข้อมูลผ่าน REST API

[การรันบน Cloud / Container]
- Docker  : ใช้ `Dockerfile` และ `.dockerignore` ที่เตรียมไว้ (`docker build -t todo-widget . && docker run -p 18234:18234 todo-widget`)
- Render  : เชื่อมต่อ Repository เข้ากับ Render โดยใช้ไฟล์ `render.yaml` (Blueprint) ได้ทันที
- Heroku / Railway : รองรับการรันผ่าน `Procfile` (`web: python desktop_sync_server.py`)
