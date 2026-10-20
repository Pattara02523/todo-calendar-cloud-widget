# -*- coding: utf-8 -*-
"""
Desktop Corner Widget: To-Do List & 1-Week Google Calendar
- แสดงผลอยู่มุมขวาของหน้าจออัตโนมัติ (Right-Corner Dock)
- ลอยอยู่บนสุดเสมอ (Always on Top) และเปิดค้างไว้จนกว่าจะปิดคอมพิวเตอร์
- ซิงก์ตารางงาน 1 สัปดาห์จาก Google Calendar พร้อมบันทึก To-Do List อัตโนมัติ
- ซิงก์ข้อมูลกับ desktop_todo_data.json ร่วมกับ HTML Web App ในโฟลเดอร์เดียวกัน
"""

import json
import os
import time
import webbrowser
from datetime import datetime, timedelta
import tkinter as tk
from tkinter import ttk

# Auto start background sync server if available
try:
    import desktop_sync_server
    desktop_sync_server.start_in_thread()
except Exception as e:
    pass

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(APP_DIR, "desktop_todo_data.json")

DEFAULT_EVENTS = [
    {
        "id": "1",
        "date": "2026-10-13",
        "time": "ทั้งวัน",
        "title": "วันคล้ายวันสวรรคต ร.9",
        "calendar": "pattarapon.thongsri@gmail.com",
        "url": "https://www.google.com/calendar/event?eid=anVrZmIwajQwam5za29zdXJsMGRpamdsOXMgcGF0dGFyYXBvbi50aG9uZ3NyaUBt&ctz=Asia/Bangkok"
    },
    {
        "id": "2",
        "date": "2026-10-14",
        "time": "13:30 - 16:00",
        "title": "ประชุมกับอนุกรรมการอำนวยการ",
        "calendar": "coopthaiict@gmail.com",
        "url": "https://www.google.com/calendar/event?eid=NGQ1MHMzNXNsMmxnY2E3aGdhcms0MHRzNmEgY29vcHRoYWlpY3RAbQ&ctz=Asia/Bangkok"
    },
    {
        "id": "3",
        "date": "2026-10-16",
        "time": "ทั้งวัน",
        "title": "วันหยุดพิเศษ",
        "calendar": "pattarapon.thongsri@gmail.com",
        "url": "https://www.google.com/calendar/event?eid=M2ljMTBlaGwzZmpnZGpzYWpycm12OGIyb24gcGF0dGFyYXBvbi50aG9uZ3NyaUBt&ctz=Asia/Bangkok"
    },
    {
        "id": "4",
        "date": "2026-10-22",
        "time": "13:30 - 16:00",
        "title": "ประชุมกับคณะกรรมการดำเนินการ",
        "calendar": "coopthaiict@gmail.com",
        "url": "https://www.google.com/calendar/event?eid=MzVmbmV2OTdvNTFoZXEzaTlxcmY0OWdhZzggY29vcHRoYWlpY3RAbQ&ctz=Asia/Bangkok"
    },
    {
        "id": "5",
        "date": "2026-10-23",
        "time": "ทั้งวัน",
        "title": "วันปิยมหาราช",
        "calendar": "pattarapon.thongsri@gmail.com",
        "url": "https://www.google.com/calendar/event?eid=ZmhuZm1ib3BjZ3J2dDBmaDN0amxmcGwyazQgcGF0dGFyYXBvbi50aG9uZ3NyaUBt&ctz=Asia/Bangkok"
    },
    {
        "id": "6",
        "date": "2026-10-24",
        "time": "08:30 - 11:00",
        "title": "โครงการอบรมเชิงปฏิบัติการ เรื่อง การสร้างสื่ออนิเมชั่น รุ่นที่ 2",
        "calendar": "coopthaiict@gmail.com",
        "url": "https://www.google.com/calendar/event?eid=N3JyNWk2M3NsaDRkNDlzazhnNXNrbmg1bGcgY29vcHRoYWlpY3RAbQ&ctz=Asia/Bangkok"
    }
]

DEFAULT_TODOS = [
    {
        "id": "todo-1",
        "text": "สรุปงานก่อนวันหยุดพิเศษ (16 ต.ค.)",
        "dueDate": "2026-10-15",
        "priority": "high",
        "category": "งานประจำ",
        "done": False,
        "completed": False
    },
    {
        "id": "todo-2",
        "text": "เตรียมเอกสารประชุมคณะกรรมการดำเนินการ (22 ต.ค. 13:30 น.)",
        "dueDate": "2026-10-21",
        "priority": "high",
        "category": "ประชุม",
        "done": False,
        "completed": False
    },
    {
        "id": "todo-3",
        "text": "เตรียมความพร้อมโครงการอบรมการสร้างสื่ออนิเมชั่น รุ่นที่ 2",
        "dueDate": "2026-10-24",
        "priority": "medium",
        "category": "งานประจำ",
        "done": False,
        "completed": False
    }
]


class RightCornerDesktopWidget:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Desktop To-Do & 1-Week Google Calendar")
        self.root.configure(bg="#0f172a")
        self.always_on_top = True
        self.root.attributes("-topmost", self.always_on_top)
        self.root.attributes("-alpha", 0.96)

        # Position at top-right corner of the screen
        width = 410
        height = 840
        screen_w = self.root.winfo_screenwidth()
        x_pos = max(0, screen_w - width - 14)
        y_pos = 14
        self.root.geometry(f"{width}x{height}+{x_pos}+{y_pos}")

        self.last_mtime = self.get_file_mtime()
        self.data = self.load_data()
        self.build_ui()
        self.refresh_todos()
        self.refresh_calendar()

        # Check for external data updates (from HTML Web Widget) every 1.5 seconds
        self.root.after(1500, self.check_external_changes)

    def get_file_mtime(self):
        try:
            if os.path.exists(DATA_FILE):
                return os.path.getmtime(DATA_FILE)
        except Exception:
            pass
        return 0

    def load_data(self):
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    content = json.load(f)
                    todos = content.get("todos", [])
                    events = content.get("events", [])
                    # Normalize todos format
                    for t in todos:
                        status = bool(t.get("done") or t.get("completed"))
                        t["done"] = status
                        t["completed"] = status
                    return {"todos": todos, "events": events if events else DEFAULT_EVENTS}
            except Exception as e:
                print("Error loading json:", e)
        # Create initial file if not exists
        initial = {"todos": DEFAULT_TODOS, "events": DEFAULT_EVENTS}
        try:
            with open(DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(initial, f, ensure_ascii=False, indent=2)
            self.last_mtime = self.get_file_mtime()
        except Exception:
            pass
        return initial

    def save_data(self):
        try:
            with open(DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            self.last_mtime = self.get_file_mtime()
        except Exception as e:
            print("Error saving json:", e)

    def check_external_changes(self):
        current_mtime = self.get_file_mtime()
        if current_mtime and current_mtime != self.last_mtime:
            self.last_mtime = current_mtime
            new_data = self.load_data()
            self.data = new_data
            self.refresh_todos()
            self.refresh_calendar()
        self.root.after(1500, self.check_external_changes)

    def toggle_pin(self):
        self.always_on_top = not self.always_on_top
        self.root.attributes("-topmost", self.always_on_top)
        self.pin_btn.config(
            text="📌 ปักหมุดบนสุด: เปิด" if self.always_on_top else "📌 ปักหมุดบนสุด: ปิด",
            bg="#4f46e5" if self.always_on_top else "#334155"
        )

    def build_ui(self):
        # Header
        header = tk.Frame(self.root, bg="#1e293b", padx=12, pady=10)
        header.pack(fill="x")

        title_frame = tk.Frame(header, bg="#1e293b")
        title_frame.pack(side="left")

        title_lbl = tk.Label(
            title_frame,
            text="🗓️ To-Do & ตารางงาน 1 สัปดาห์",
            fg="#f8fafc",
            bg="#1e293b",
            font=("Tahoma", 10, "bold")
        )
        title_lbl.pack(anchor="w")

        sync_sub = tk.Label(
            title_frame,
            text="🟢 ฐานข้อมูลกลาง: desktop_todo_data.json",
            fg="#34d399",
            bg="#1e293b",
            font=("Tahoma", 7)
        )
        sync_sub.pack(anchor="w")

        self.pin_btn = tk.Button(
            header,
            text="📌 ปักหมุดบนสุด: เปิด",
            command=self.toggle_pin,
            bg="#4f46e5",
            fg="white",
            relief="flat",
            padx=8,
            pady=3,
            font=("Tahoma", 8, "bold")
        )
        self.pin_btn.pack(side="right")

        # To-Do Section
        todo_frame = tk.LabelFrame(
            self.root,
            text=" ✅ รายการสิ่งที่ต้องทำ (To-Do List) ",
            fg="#a5b4fc",
            bg="#0f172a",
            font=("Tahoma", 10, "bold"),
            padx=10,
            pady=8
        )
        todo_frame.pack(fill="both", expand=False, padx=10, pady=(10, 6))

        entry_row = tk.Frame(todo_frame, bg="#0f172a")
        entry_row.pack(fill="x", pady=(0, 6))

        self.todo_entry = tk.Entry(
            entry_row,
            bg="#1e293b",
            fg="#f8fafc",
            insertbackground="white",
            relief="flat",
            font=("Tahoma", 10)
        )
        self.todo_entry.pack(side="left", fill="x", expand=True, ipady=5, padx=(0, 6))
        self.todo_entry.bind("<Return>", lambda e: self.add_todo())

        add_btn = tk.Button(
            entry_row,
            text="+ เพิ่ม",
            command=self.add_todo,
            bg="#4f46e5",
            fg="white",
            relief="flat",
            padx=10,
            pady=4,
            font=("Tahoma", 9, "bold")
        )
        add_btn.pack(side="right")

        # Canvas with scrollbar for todos
        todo_container = tk.Frame(todo_frame, bg="#0f172a")
        todo_container.pack(fill="both", expand=True)

        self.todo_canvas = tk.Canvas(todo_container, bg="#0f172a", highlightthickness=0, height=220)
        self.todo_scrollbar = ttk.Scrollbar(todo_container, orient="vertical", command=self.todo_canvas.yview)
        self.todo_list_frame = tk.Frame(self.todo_canvas, bg="#0f172a")

        self.todo_list_frame.bind(
            "<Configure>",
            lambda e: self.todo_canvas.configure(scrollregion=self.todo_canvas.bbox("all"))
        )
        self.todo_canvas_window = self.todo_canvas.create_window((0, 0), window=self.todo_list_frame, anchor="nw")
        self.todo_canvas.configure(xscrollcommand=None, yscrollcommand=self.todo_scrollbar.set)

        self.todo_canvas.pack(side="left", fill="both", expand=True)
        self.todo_scrollbar.pack(side="right", fill="y")

        # 1-Week Google Calendar Section
        cal_frame = tk.LabelFrame(
            self.root,
            text=" 📅 ตารางงาน Google Calendar (1 สัปดาห์) ",
            fg="#38bdf8",
            bg="#0f172a",
            font=("Tahoma", 10, "bold"),
            padx=10,
            pady=8
        )
        cal_frame.pack(fill="both", expand=True, padx=10, pady=6)

        cal_top_bar = tk.Frame(cal_frame, bg="#0f172a")
        cal_top_bar.pack(fill="x", pady=(0, 4))
        cal_status_lbl = tk.Label(
            cal_top_bar,
            text="ซิงก์สด 7 วันล่วงหน้า",
            fg="#94a3b8",
            bg="#0f172a",
            font=("Tahoma", 8)
        )
        cal_status_lbl.pack(side="left")
        cal_refresh_btn = tk.Button(
            cal_top_bar,
            text="🔄 รีเฟรชสด",
            command=self.manual_sync_calendar,
            bg="#1e293b",
            fg="#38bdf8",
            relief="flat",
            padx=6,
            pady=1,
            font=("Tahoma", 8, "bold")
        )
        cal_refresh_btn.pack(side="right")

        cal_container = tk.Frame(cal_frame, bg="#0f172a")
        cal_container.pack(fill="both", expand=True)

        self.cal_canvas = tk.Canvas(cal_container, bg="#0f172a", highlightthickness=0)
        self.cal_scrollbar = ttk.Scrollbar(cal_container, orient="vertical", command=self.cal_canvas.yview)
        self.cal_list_frame = tk.Frame(self.cal_canvas, bg="#0f172a")

        self.cal_list_frame.bind(
            "<Configure>",
            lambda e: self.cal_canvas.configure(scrollregion=self.cal_canvas.bbox("all"))
        )
        self.cal_canvas.create_window((0, 0), window=self.cal_list_frame, anchor="nw")
        self.cal_canvas.configure(yscrollcommand=self.cal_scrollbar.set)

        self.cal_canvas.pack(side="left", fill="both", expand=True)
        self.cal_scrollbar.pack(side="right", fill="y")

        # Footer button to open rich HTML version
        footer = tk.Frame(self.root, bg="#0f172a", padx=10, pady=6)
        footer.pack(fill="x")
        open_web_btn = tk.Button(
            footer,
            text="🌐 เปิดหน้าต่าง Web App แบบเต็ม / ดูตารางละเอียด",
            command=self.open_html_app,
            bg="#059669",
            fg="white",
            relief="flat",
            pady=6,
            font=("Tahoma", 9, "bold")
        )
        open_web_btn.pack(fill="x")

    def add_todo(self):
        text = self.todo_entry.get().strip()
        if not text:
            return
        new_item = {
            "id": f"todo-{int(time.time() * 1000)}",
            "text": text,
            "dueDate": datetime.now().strftime("%Y-%m-%d"),
            "priority": "medium",
            "category": "งานประจำ",
            "done": False,
            "completed": False
        }
        self.data["todos"].insert(0, new_item)
        self.todo_entry.delete(0, tk.END)
        self.save_data()
        self.refresh_todos()

    def toggle_todo(self, idx: int):
        item = self.data["todos"][idx]
        new_state = not bool(item.get("done") or item.get("completed"))
        item["done"] = new_state
        item["completed"] = new_state
        self.save_data()
        self.refresh_todos()

    def delete_todo(self, idx: int):
        del self.data["todos"][idx]
        self.save_data()
        self.refresh_todos()

    def refresh_todos(self):
        for w in self.todo_list_frame.winfo_children():
            w.destroy()

        if not self.data.get("todos"):
            empty_lbl = tk.Label(
                self.todo_list_frame,
                text="ไม่มีรายการงาน เพิ่มงานใหม่ด้านบนได้เลย",
                fg="#64748b",
                bg="#0f172a",
                font=("Tahoma", 8)
            )
            empty_lbl.pack(pady=10)
            return

        for idx, item in enumerate(self.data.get("todos", [])):
            is_done = bool(item.get("done") or item.get("completed"))
            row = tk.Frame(self.todo_list_frame, bg="#1e293b", pady=4, padx=6)
            row.pack(fill="x", pady=2, padx=2)

            chk_btn = tk.Button(
                row,
                text="☑" if is_done else "☐",
                command=lambda i=idx: self.toggle_todo(i),
                bg="#1e293b",
                fg="#34d399" if is_done else "#94a3b8",
                relief="flat",
                font=("Tahoma", 10, "bold")
            )
            chk_btn.pack(side="left")

            title_txt = item.get("text", "")
            lbl = tk.Label(
                row,
                text=title_txt,
                fg="#64748b" if is_done else "#f1f5f9",
                bg="#1e293b",
                anchor="w",
                justify="left",
                wraplength=270,
                font=("Tahoma", 9, "overstrike" if is_done else "normal")
            )
            lbl.pack(side="left", fill="x", expand=True, padx=4)

            del_btn = tk.Button(
                row,
                text="✕",
                command=lambda i=idx: self.delete_todo(i),
                bg="#1e293b",
                fg="#f87171",
                relief="flat",
                font=("Tahoma", 8)
            )
            del_btn.pack(side="right")

    def refresh_calendar(self):
        for w in self.cal_list_frame.winfo_children():
            w.destroy()

        for ev in self.data.get("events", []):
            card = tk.Frame(self.cal_list_frame, bg="#1e293b", padx=8, pady=6)
            card.pack(fill="x", pady=3, padx=2)

            top = tk.Frame(card, bg="#1e293b")
            top.pack(fill="x")

            date_lbl = tk.Label(
                top,
                text=f"📅 {ev.get('date', '')} ({ev.get('time', '')})",
                fg="#38bdf8",
                bg="#1e293b",
                font=("Tahoma", 8, "bold")
            )
            date_lbl.pack(side="left")

            if ev.get("url"):
                open_btn = tk.Button(
                    top,
                    text="เปิดลิงก์ ↗",
                    command=lambda u=ev["url"]: webbrowser.open(u),
                    bg="#334155",
                    fg="#e2e8f0",
                    relief="flat",
                    padx=4,
                    font=("Tahoma", 7)
                )
                open_btn.pack(side="right")

            title_lbl = tk.Label(
                card,
                text=ev.get("title", ""),
                fg="#f8fafc",
                bg="#1e293b",
                anchor="w",
                justify="left",
                wraplength=340,
                font=("Tahoma", 9, "bold")
            )
            title_lbl.pack(fill="x", pady=(2, 0))

    def manual_sync_calendar(self):
        try:
            import desktop_sync_server
            desktop_sync_server.sync_google_calendar_now()
            self.data = self.load_data()
            self.refresh_calendar()
        except Exception as e:
            print("Manual sync error:", e)

    def open_html_app(self):
        webbrowser.open("http://127.0.0.1:18234")


if __name__ == "__main__":
    root = tk.Tk()
    app = RightCornerDesktopWidget(root)
    root.mainloop()
