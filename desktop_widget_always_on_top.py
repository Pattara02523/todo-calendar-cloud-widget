# -*- coding: utf-8 -*-
"""
Desktop Corner Widget: To-Do List & 1-Week Google Calendar
- แสดงผลอยู่มุมขวาของหน้าจออัตโนมัติ (Right-Corner Dock)
- ลอยอยู่บนสุดเสมอ (Always on Top) และเปิดค้างไว้จนกว่าจะปิดคอมพิวเตอร์
- ซิงก์ตารางงาน 1 สัปดาห์จาก Google Calendar พร้อมบันทึก To-Do List (2-Way Notion Sync) อัตโนมัติ
- ซิงก์ข้อมูลกับ desktop_todo_data.json และ REST API ร่วมกับ HTML Web App ในโฟลเดอร์เดียวกัน
"""

import copy
import json
import os
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime, timedelta
import tkinter as tk
from tkinter import ttk, messagebox

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.environ.get("TODO_DATA_FILE", os.path.join(APP_DIR, "desktop_todo_data.json"))

_widget_file_lock = threading.RLock()


def _should_auto_start_workers() -> bool:
    """Return False when DISABLE_BACKGROUND_WORKERS is set to 1/true/yes (e.g., during automated tests)."""
    return os.environ.get("DISABLE_BACKGROUND_WORKERS", "").strip().lower() not in ("1", "true", "yes")


# Auto start background sync server if available and not disabled by test environment
try:
    import desktop_sync_server
    if _should_auto_start_workers():
        desktop_sync_server.start_in_thread()
except Exception:
    desktop_sync_server = None

try:
    import notion_service
except Exception:
    notion_service = None


def get_data_file() -> str:
    """Resolve the active data file path dynamically (honoring TODO_DATA_FILE env var)."""
    return os.environ.get("TODO_DATA_FILE") or DATA_FILE


def get_server_port() -> int:
    """Resolve the active local sync server port dynamically (honoring PORT env var)."""
    env_port = os.environ.get("PORT", "").strip()
    if env_port:
        try:
            return int(env_port)
        except ValueError:
            pass
    if desktop_sync_server is not None and hasattr(desktop_sync_server, "PORT"):
        try:
            return int(desktop_sync_server.PORT)
        except (ValueError, TypeError):
            pass
    return 18234


def get_api_headers() -> dict:
    """Build standard JSON request headers including optional X-API-Key."""
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "application/json",
    }
    api_key = os.environ.get("WIDGET_API_KEY", "").strip()
    if api_key:
        headers["X-API-Key"] = api_key
    return headers


def is_notion_uuid(item_id: str) -> bool:
    """Check whether item_id looks like a 36-char Notion page UUID."""
    return bool(item_id and isinstance(item_id, str) and len(item_id) >= 32 and "-" in item_id)


def normalize_todo(t: dict) -> dict:
    """Ensure a todo dict has synchronized done/completed booleans and default metadata."""
    if not isinstance(t, dict):
        return {}
    item = dict(t)
    if "done" in item and "completed" not in item:
        status = bool(item["done"])
    elif "completed" in item and "done" not in item:
        status = bool(item["completed"])
    else:
        status = bool(item.get("done") or item.get("completed"))
    item["done"] = status
    item["completed"] = status
    item.setdefault("id", f"todo-{int(time.time() * 1000)}")
    item["text"] = str(item.get("text") or "")
    item["dueDate"] = str(item.get("dueDate") or "")
    priority = str(item.get("priority") or "medium").strip().lower()
    if priority not in ("high", "medium", "low"):
        priority = "medium"
    item["priority"] = priority
    item["category"] = str(item.get("category") or "งานประจำ").strip() or "งานประจำ"
    if "notion_url" in item and item["notion_url"] is not None:
        item["notion_url"] = str(item["notion_url"])
    return item


PRIORITY_OPTIONS = ["high", "medium", "low"]
PRIORITY_META = {
    "high":   {"label": "🔴 สำคัญมาก", "bg": "#4c0519", "fg": "#fda4af", "rank": 0},
    "medium": {"label": "🟡 ปานกลาง",  "bg": "#451a03", "fg": "#fcd34d", "rank": 1},
    "low":    {"label": "🟢 ทั่วไป",   "bg": "#064e3b", "fg": "#6ee7b7", "rank": 2},
}

CATEGORY_OPTIONS = ["งานประจำ", "ประชุม", "ส่วนตัว", "ด่วน"]
CATEGORY_ICONS = {
    "งานประจำ": "💼 งานประจำ",
    "ประชุม": "📅 ประชุม",
    "ส่วนตัว": "🏠 ส่วนตัว",
    "ด่วน": "🔥 ด่วน",
}

SOURCE_TYPE_META = {
    "primary": {"label": "ส่วนตัว",   "bg": "#0c4a6e", "fg": "#38bdf8", "border": "#0284c7"},
    "coop":    {"label": "สหกรณ์",   "bg": "#78350f", "fg": "#fbbf24", "border": "#d97706"},
    "holiday": {"label": "วันหยุด",   "bg": "#881337", "fg": "#fb7185", "border": "#e11d48"},
    "custom":  {"label": "กำหนดเอง", "bg": "#312e81", "fg": "#a5b4fc", "border": "#4f46e5"},
}

CALENDAR_ACCOUNT_MAP = {
    "ส่วนตัว": "pattarapon.thongsri@gmail.com",
    "primary": "pattarapon.thongsri@gmail.com",
    "pattarapon.thongsri@gmail.com": "pattarapon.thongsri@gmail.com",
    "สหกรณ์": "coopthaiict@gmail.com",
    "coop": "coopthaiict@gmail.com",
    "coopthaiict@gmail.com": "coopthaiict@gmail.com",
}

DEFAULT_EVENTS = [
    {
        "id": "1",
        "date": "2026-10-13",
        "time": "ทั้งวัน",
        "title": "วันคล้ายวันสวรรคต ร.9",
        "calendar": "pattarapon.thongsri@gmail.com",
        "sourceType": "primary",
        "url": "https://www.google.com/calendar/event?eid=anVrZmIwajQwam5za29zdXJsMGRpamdsOXMgcGF0dGFyYXBvbi50aG9uZ3NyaUBt&ctz=Asia/Bangkok"
    },
    {
        "id": "2",
        "date": "2026-10-14",
        "time": "13:30 - 16:00",
        "title": "ประชุมกับอนุกรรมการอำนวยการ",
        "calendar": "coopthaiict@gmail.com",
        "sourceType": "coop",
        "url": "https://www.google.com/calendar/event?eid=NGQ1MHMzNXNsMmxnY2E3aGdhcms0MHRzNmEgY29vcHRoYWlpY3RAbQ&ctz=Asia/Bangkok"
    },
    {
        "id": "3",
        "date": "2026-10-16",
        "time": "ทั้งวัน",
        "title": "วันหยุดพิเศษ",
        "calendar": "pattarapon.thongsri@gmail.com",
        "sourceType": "primary",
        "url": "https://www.google.com/calendar/event?eid=M2ljMTBlaGwzZmpnZGpzYWpycm12OGIyb24gcGF0dGFyYXBvbi50aG9uZ3NyaUBt&ctz=Asia/Bangkok"
    },
    {
        "id": "4",
        "date": "2026-10-22",
        "time": "13:30 - 16:00",
        "title": "ประชุมกับคณะกรรมการดำเนินการ",
        "calendar": "coopthaiict@gmail.com",
        "sourceType": "coop",
        "url": "https://www.google.com/calendar/event?eid=MzVmbmV2OTdvNTFoZXEzaTlxcmY0OWdhZzggY29vcHRoYWlpY3RAbQ&ctz=Asia/Bangkok"
    },
    {
        "id": "5",
        "date": "2026-10-23",
        "time": "ทั้งวัน",
        "title": "วันปิยมหาราช",
        "calendar": "pattarapon.thongsri@gmail.com",
        "sourceType": "primary",
        "url": "https://www.google.com/calendar/event?eid=ZmhuZm1ib3BjZ3J2dDBmaDN0amxmcGwyazQgcGF0dGFyYXBvbi50aG9uZ3NyaUBt&ctz=Asia/Bangkok"
    },
    {
        "id": "6",
        "date": "2026-10-24",
        "time": "08:30 - 11:00",
        "title": "โครงการอบรมเชิงปฏิบัติการ เรื่อง การสร้างสื่ออนิเมชั่น รุ่นที่ 2",
        "calendar": "coopthaiict@gmail.com",
        "sourceType": "coop",
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
    def __init__(self, root: tk.Tk = None, auto_start_server: bool = False, headless: bool = False):
        self.root = root
        self.headless = bool(headless or root is None)
        self.always_on_top = True

        # Filter & Sort state (for parity with Web UI & headless testing)
        self.filter_status = "all"      # "all" | "active" | "done" ("completed")
        self.status_filter = "all"      # alias kept synchronized with filter_status
        self.filter_category = "all"    # "all" | "งานประจำ" | "ประชุม" | "ส่วนตัว" | "ด่วน"
        self.filter_search = ""         # substring query
        self.search_query = ""          # alias kept synchronized with filter_search
        self.sort_by = "default"        # "default" | "dueDate" | "priority"

        # UI Widget references (populated when build_ui runs)
        self.todo_entry = None
        self.todo_due_entry = None
        self.due_date_var = None
        self.todo_priority_var = None
        self.priority_var = None
        self.todo_category_var = None
        self.category_var = None
        self.search_var = None
        self.filter_cat_var = None
        self.todo_list_frame = None
        self.cal_list_frame = None
        self.cal_add_frame = None
        self.cal_add_visible = False
        self.sync_sub = None
        self.notion_sync_btn = None
        self.cal_status_lbl = None
        self.cal_refresh_btn = None
        self.filter_status_btns = {}

        if auto_start_server and desktop_sync_server is not None and _should_auto_start_workers():
            try:
                desktop_sync_server.start_in_thread()
            except Exception:
                pass

        self.data = {"todos": [], "events": []}
        self.last_mtime = self.get_file_mtime()
        self.data = self.load_data()

        if not self.headless and self.root is not None:
            self.root.title("Desktop To-Do & 1-Week Google Calendar")
            self.root.configure(bg="#0f172a")
            self.root.attributes("-topmost", self.always_on_top)
            self.root.attributes("-alpha", 0.96)

            # Position at top-right corner of the screen
            width = 410
            height = 840
            screen_w = self.root.winfo_screenwidth()
            x_pos = max(0, screen_w - width - 14)
            y_pos = 14
            self.root.geometry(f"{width}x{height}+{x_pos}+{y_pos}")

            self.build_ui()
            self.refresh_todos()
            self.refresh_calendar()

            # Check for external data updates (from HTML Web Widget or Sync Server) every 1.5 seconds
            self.root.after(1500, self.check_external_changes)

    def get_file_mtime(self) -> float:
        data_file = get_data_file()
        try:
            if os.path.exists(data_file):
                return os.path.getmtime(data_file)
        except Exception:
            pass
        return 0.0

    def _atomic_write_json(self, target_file: str, payload: dict) -> None:
        target_dir = os.path.dirname(os.path.abspath(target_file))
        os.makedirs(target_dir, exist_ok=True)
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                dir=target_dir,
                prefix=".todo_widget_",
                suffix=".tmp",
                delete=False,
                encoding="utf-8",
            ) as tf:
                tmp_path = tf.name
                json.dump(payload, tf, ensure_ascii=False, indent=2)
                tf.flush()
                try:
                    os.fsync(tf.fileno())
                except OSError:
                    pass
            os.replace(tmp_path, target_file)
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    def load_data(self) -> dict:
        data_file = get_data_file()
        with _widget_file_lock:
            if os.path.exists(data_file):
                try:
                    with open(data_file, "r", encoding="utf-8") as f:
                        content = json.load(f)
                    if not isinstance(content, dict):
                        raise ValueError("Root JSON payload must be an object")

                    raw_todos = content.get("todos")
                    if isinstance(raw_todos, list):
                        todos = [normalize_todo(t) for t in raw_todos if isinstance(t, dict)]
                    else:
                        todos = []

                    raw_events = content.get("events")
                    # Preserve empty list [] when "events" is a list in JSON!
                    if isinstance(raw_events, list):
                        events = [dict(ev) for ev in raw_events if isinstance(ev, dict)]
                    else:
                        events = copy.deepcopy(DEFAULT_EVENTS)

                    return {"todos": todos, "events": events}
                except Exception as e:
                    print("Error loading json (preserving existing data without overwriting disk):", e)
                    # NEVER overwrite existing file on read/parse error!
                    if isinstance(getattr(self, "data", None), dict) and "todos" in self.data and "events" in self.data:
                        return copy.deepcopy(self.data)
                    return {"todos": [], "events": []}

            # Only create and write DEFAULT_TODOS / DEFAULT_EVENTS when the file does not exist at all
            initial = {
                "todos": [normalize_todo(t) for t in copy.deepcopy(DEFAULT_TODOS)],
                "events": copy.deepcopy(DEFAULT_EVENTS),
            }
            try:
                self._atomic_write_json(data_file, initial)
                self.last_mtime = self.get_file_mtime()
            except Exception as e:
                print("Error creating initial json:", e)
            return initial

    def save_data(self) -> bool:
        data_file = get_data_file()
        with _widget_file_lock:
            try:
                self._atomic_write_json(data_file, self.data)
                self.last_mtime = self.get_file_mtime()
                return True
            except Exception as e:
                print("Error saving json:", e)
                return False

    def check_external_changes(self):
        current_mtime = self.get_file_mtime()
        if current_mtime and current_mtime != self.last_mtime:
            self.last_mtime = current_mtime
            new_data = self.load_data()
            self.data = new_data
            self.refresh_todos()
            self.refresh_calendar()
        if not self.headless and self.root is not None:
            try:
                self.root.after(1500, self.check_external_changes)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # REST API Sync & Background Worker Helpers
    # ------------------------------------------------------------------

    def _resolve_async(self, run_async=None, async_mode=None) -> bool:
        if run_async is not None:
            return bool(run_async)
        if async_mode is not None:
            return bool(async_mode)
        return not self.headless

    def _schedule_ui(self, callback, *args):
        """Safely marshal a UI update onto the Tkinter main loop if running with a live root."""
        if self.root is not None and not self.headless:
            try:
                self.root.after(0, lambda: callback(*args))
            except Exception:
                pass

    def _run_sync_task(self, worker_fn, run_async=None, async_mode=None):
        """Run worker_fn synchronously in headless/test mode or on a daemon thread in GUI mode."""
        use_async = self._resolve_async(run_async=run_async, async_mode=async_mode)
        if use_async:
            t = threading.Thread(target=worker_fn, daemon=True)
            t.start()
            return t
        else:
            worker_fn()
            return None

    def _api_request(self, path: str, method: str = "GET", payload=None, timeout: float = 4.0):
        """Send an HTTP request to the local Sync Server and return parsed JSON."""
        port = get_server_port()
        url = f"http://127.0.0.1:{port}{path}"
        headers = get_api_headers()
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if raw:
                return json.loads(raw.decode("utf-8"))
            return {}

    def _reconcile_server_todos(self, server_todos: list):
        """Update local todos with Notion UUIDs / notion_url returned from the sync server."""
        if not isinstance(server_todos, list):
            return
        with _widget_file_lock:
            normalized = [normalize_todo(t) for t in server_todos if isinstance(t, dict)]
            self.data["todos"] = normalized
            self.save_data()
        if self.headless or self.root is None:
            self.refresh_todos()
        else:
            self._schedule_ui(self.refresh_todos)

    def _sync_todos_to_server(self, deleted_ids=None, patched_item=None, run_async=None, async_mode=None):
        """Push current self.data['todos'] to the local Sync Server with Notion archive fallback."""
        todos_snapshot = copy.deepcopy(self.data.get("todos", []))
        deleted_ids = [str(did) for did in (deleted_ids or []) if did]
        patched_snapshot = copy.deepcopy(patched_item) if isinstance(patched_item, dict) else None

        def _worker():
            server_ok = False

            # 1. If specific deletions occurred, call DELETE /api/todos/<id> first
            for del_id in deleted_ids:
                try:
                    encoded_id = urllib.parse.quote(str(del_id), safe="")
                    res = self._api_request(f"/api/todos/{encoded_id}", method="DELETE", timeout=3.0)
                    if isinstance(res, dict) and res.get("status") == "success":
                        server_ok = True
                except urllib.error.HTTPError as he:
                    if he.code == 404:
                        pass
                except Exception:
                    pass

            # 2. If a single item was edited/toggled, try PATCH /api/todos/<id>
            if patched_snapshot and patched_snapshot.get("id") and not deleted_ids:
                try:
                    encoded_id = urllib.parse.quote(str(patched_snapshot["id"]), safe="")
                    res = self._api_request(
                        f"/api/todos/{encoded_id}",
                        method="PATCH",
                        payload=patched_snapshot,
                        timeout=3.0,
                    )
                    if isinstance(res, dict) and res.get("status") == "success":
                        server_ok = True
                        if isinstance(res.get("todos"), list):
                            self._reconcile_server_todos(res["todos"])
                            return
                except Exception:
                    pass

            # 3. Always synchronize full todos array via POST /api/todos
            try:
                res = self._api_request("/api/todos", method="POST", payload=todos_snapshot, timeout=4.0)
                if isinstance(res, dict) and res.get("status") == "success":
                    server_ok = True
                    if isinstance(res.get("todos"), list):
                        self._reconcile_server_todos(res["todos"])
            except Exception:
                pass

            # 4. Direct fallback: if server was unreachable and Notion UUID task(s) were deleted, archive directly
            if not server_ok and deleted_ids and notion_service is not None:
                for del_id in deleted_ids:
                    if is_notion_uuid(del_id):
                        try:
                            notion_service.archive_notion_todo(del_id)
                        except Exception as e:
                            print(f"[Widget Fallback] Error archiving {del_id} in Notion:", e)

        return self._run_sync_task(_worker, run_async=run_async, async_mode=async_mode)

    # ------------------------------------------------------------------
    # Controller CRUD, Filter & Sync Methods (GUI + Headless Compatible)
    # ------------------------------------------------------------------

    def _find_todo_index(self, idx_or_id) -> int:
        todos = self.data.get("todos", [])
        if isinstance(idx_or_id, int) and not isinstance(idx_or_id, bool):
            if 0 <= idx_or_id < len(todos):
                return idx_or_id
            return -1
        target_id = str(idx_or_id)
        for i, item in enumerate(todos):
            if str(item.get("id")) == target_id:
                return i
        return -1

    def add_todo(
        self,
        text: str = None,
        due_date: str = None,
        dueDate: str = None,
        priority: str = None,
        category: str = None,
        sync_remote: bool = True,
        run_async: bool = None,
        async_mode: bool = None,
    ):
        if text is None and self.todo_entry is not None:
            try:
                text = self.todo_entry.get()
            except Exception:
                text = ""
        text = (text or "").strip()
        if not text:
            return None

        resolved_due = due_date if due_date is not None else dueDate
        if resolved_due is None:
            if self.due_date_var is not None:
                try:
                    resolved_due = self.due_date_var.get().strip()
                except Exception:
                    resolved_due = ""
            elif self.todo_due_entry is not None:
                try:
                    resolved_due = self.todo_due_entry.get().strip()
                except Exception:
                    resolved_due = ""
            if not resolved_due:
                resolved_due = datetime.now().strftime("%Y-%m-%d")

        if priority is None:
            if self.todo_priority_var is not None:
                try:
                    priority = self.todo_priority_var.get().strip().lower()
                except Exception:
                    priority = "medium"
            else:
                priority = "medium"

        if category is None:
            if self.todo_category_var is not None:
                try:
                    category = self.todo_category_var.get().strip()
                except Exception:
                    category = "งานประจำ"
            else:
                category = "งานประจำ"

        new_item = normalize_todo({
            "id": f"todo-{int(time.time() * 1000)}-{len(self.data.get('todos', []))}",
            "text": text,
            "dueDate": resolved_due,
            "priority": priority,
            "category": category,
            "done": False,
            "completed": False,
            "notion_url": "",
        })

        with _widget_file_lock:
            self.data.setdefault("todos", []).insert(0, new_item)
            self.save_data()

        if self.todo_entry is not None and not self.headless:
            try:
                self.todo_entry.delete(0, tk.END)
            except Exception:
                pass

        self.refresh_todos()
        if sync_remote:
            self._sync_todos_to_server(run_async=run_async, async_mode=async_mode)
        return new_item

    def toggle_todo(self, idx_or_id, sync_remote: bool = True, run_async: bool = None, async_mode: bool = None):
        with _widget_file_lock:
            idx = self._find_todo_index(idx_or_id)
            if idx < 0:
                return None
            item = self.data["todos"][idx]
            new_state = not bool(item.get("done") or item.get("completed"))
            item["done"] = new_state
            item["completed"] = new_state
            self.save_data()

        self.refresh_todos()
        if sync_remote:
            self._sync_todos_to_server(patched_item=item, run_async=run_async, async_mode=async_mode)
        return item

    def edit_todo(
        self,
        idx_or_id,
        text: str = None,
        due_date: str = None,
        dueDate: str = None,
        priority: str = None,
        category: str = None,
        done: bool = None,
        completed: bool = None,
        sync_remote: bool = True,
        run_async: bool = None,
        async_mode: bool = None,
    ):
        with _widget_file_lock:
            idx = self._find_todo_index(idx_or_id)
            if idx < 0:
                return None
            item = self.data["todos"][idx]
            if text is not None and str(text).strip():
                item["text"] = str(text).strip()
            resolved_due = due_date if due_date is not None else dueDate
            if resolved_due is not None:
                item["dueDate"] = str(resolved_due).strip()
            if priority is not None:
                p = str(priority).strip().lower()
                if p in ("high", "medium", "low"):
                    item["priority"] = p
            if category is not None and str(category).strip():
                item["category"] = str(category).strip()
            if done is not None or completed is not None:
                new_done = bool(done if done is not None else completed)
                item["done"] = new_done
                item["completed"] = new_done
            self.data["todos"][idx] = normalize_todo(item)
            updated = self.data["todos"][idx]
            self.save_data()

        self.refresh_todos()
        if sync_remote:
            self._sync_todos_to_server(patched_item=updated, run_async=run_async, async_mode=async_mode)
        return updated

    def delete_todo(self, idx_or_id, sync_remote: bool = True, run_async: bool = None, async_mode: bool = None):
        with _widget_file_lock:
            idx = self._find_todo_index(idx_or_id)
            if idx < 0:
                return None
            target = self.data["todos"][idx]
            removed_id = str(target.get("id")) if target.get("id") else None

        if sync_remote and removed_id:
            server_called = False
            try:
                encoded_id = urllib.parse.quote(removed_id, safe="")
                res = self._api_request(f"/api/todos/{encoded_id}", method="DELETE", timeout=3.0)
                if isinstance(res, dict) and res.get("status") == "success":
                    server_called = True
            except Exception:
                pass

            if not server_called and is_notion_uuid(removed_id) and notion_service is not None:
                try:
                    notion_service.archive_notion_todo(removed_id)
                except Exception as e:
                    print(f"[Widget Fallback] Error archiving {removed_id} in Notion:", e)

        with _widget_file_lock:
            current_todos = self.data.get("todos", [])
            curr_idx = self._find_todo_index(removed_id if removed_id else idx_or_id)
            removed = current_todos.pop(curr_idx) if curr_idx >= 0 else None
            self.save_data()

        self.refresh_todos()
        return removed

    def clear_completed_todos(self, sync_remote: bool = True, run_async: bool = None, async_mode: bool = None) -> int:
        deleted_ids = []
        with _widget_file_lock:
            current_todos = self.data.get("todos", [])
            for t in current_todos:
                if bool(t.get("done") or t.get("completed")):
                    if t.get("id"):
                        deleted_ids.append(str(t["id"]))

        if sync_remote and deleted_ids:
            for del_id in deleted_ids:
                server_called = False
                try:
                    encoded_id = urllib.parse.quote(del_id, safe="")
                    res = self._api_request(f"/api/todos/{encoded_id}", method="DELETE", timeout=3.0)
                    if isinstance(res, dict) and res.get("status") == "success":
                        server_called = True
                except Exception:
                    pass
                if not server_called and is_notion_uuid(del_id) and notion_service is not None:
                    try:
                        notion_service.archive_notion_todo(del_id)
                    except Exception:
                        pass

        with _widget_file_lock:
            current_todos = self.data.get("todos", [])
            remaining = [t for t in current_todos if not bool(t.get("done") or t.get("completed"))]
            removed_count = len(current_todos) - len(remaining)
            self.data["todos"] = remaining
            if removed_count > 0:
                self.save_data()

        self.refresh_todos()
        return removed_count

    def set_filter(
        self,
        status: str = None,
        category: str = None,
        search: str = None,
        search_query: str = None,
        sort_by: str = None,
    ):
        if status is not None:
            s = str(status).strip().lower()
            if s in ("completed", "done"):
                self.filter_status = "done"
            elif s == "active":
                self.filter_status = "active"
            else:
                self.filter_status = "all"

        if category is not None:
            cat = str(category).strip()
            self.filter_category = "all" if cat in ("", "all", "ทั้งหมด") else cat

        resolved_search = search if search is not None else search_query
        if resolved_search is not None:
            q = str(resolved_search).strip()
            self.filter_search = q
            self.search_query = q

        if sort_by is not None:
            self.sort_by = str(sort_by).strip() or "default"

        self._update_filter_buttons_ui()
        self.refresh_todos()

    def get_filtered_todos(self) -> list:
        todos = list(self.data.get("todos", []))
        filtered = []
        q = (self.filter_search or self.search_query or "").strip().lower()
        cat_filter = (self.filter_category or "all").strip()
        status_filter = (getattr(self, "status_filter", None) or self.filter_status or "all").strip().lower()

        for t in todos:
            if not isinstance(t, dict):
                continue
            is_done = bool(t.get("done") or t.get("completed"))
            if status_filter == "active" and is_done:
                continue
            if status_filter in ("done", "completed") and not is_done:
                continue
            if cat_filter not in ("all", "ทั้งหมด", ""):
                if (t.get("category") or "งานประจำ") != cat_filter:
                    continue
            if q:
                haystack = f"{t.get('text', '')} {t.get('category', '')}".lower()
                if q not in haystack:
                    continue
            filtered.append(t)

        if self.sort_by == "dueDate":
            filtered.sort(key=lambda x: (x.get("dueDate") or "9999-99-99"))
        elif self.sort_by == "priority":
            filtered.sort(key=lambda x: PRIORITY_META.get(x.get("priority", "medium"), PRIORITY_META["medium"])["rank"])

        return filtered

    def manual_sync_calendar(self, run_async: bool = None, async_mode: bool = None):
        """Fetch live 7-day Google Calendar events on a background thread and marshal UI update via root.after."""
        if not self.headless:
            if self.cal_status_lbl is not None:
                try:
                    self.cal_status_lbl.config(text="⏳ กำลังซิงก์ปฏิทิน...", fg="#fbbf24")
                except Exception:
                    pass
            if self.cal_refresh_btn is not None:
                try:
                    self.cal_refresh_btn.config(text="⏳ รีเฟรช...", state="disabled")
                except Exception:
                    pass

        def _worker():
            events = None
            try:
                res = self._api_request("/api/calendar/events", method="GET", timeout=8.0)
                if isinstance(res, list):
                    events = res
            except Exception:
                pass

            if events is None and desktop_sync_server is not None:
                try:
                    events = desktop_sync_server.sync_google_calendar_now()
                except Exception as e:
                    print("Manual calendar sync fallback error:", e)

            with _widget_file_lock:
                if isinstance(events, list):
                    self.data["events"] = events
                    self.save_data()
                else:
                    self.data = self.load_data()

            def _on_done():
                if self.cal_status_lbl is not None:
                    try:
                        self.cal_status_lbl.config(
                            text=f"ซิงก์ล่าสุด {datetime.now().strftime('%H:%M:%S')}",
                            fg="#34d399",
                        )
                    except Exception:
                        pass
                if self.cal_refresh_btn is not None:
                    try:
                        self.cal_refresh_btn.config(text="🔄 รีเฟรชสด", state="normal")
                    except Exception:
                        pass
                self.refresh_calendar()

            if self.headless or self.root is None:
                _on_done()
            else:
                self._schedule_ui(_on_done)

        resolved_async = True if run_async is None else bool(run_async)
        return self._run_sync_task(_worker, run_async=resolved_async, async_mode=async_mode)

    def manual_sync_notion(self, run_async: bool = None, async_mode: bool = None):
        """Trigger immediate 2-way Notion sync on a background thread and marshal UI update via root.after."""
        if not self.headless:
            if self.sync_sub is not None:
                try:
                    self.sync_sub.config(text="⏳ กำลังซิงก์ Notion...", fg="#fbbf24")
                except Exception:
                    pass
            if self.notion_sync_btn is not None:
                try:
                    self.notion_sync_btn.config(text="⏳ ซิงก์...", state="disabled")
                except Exception:
                    pass

        def _worker():
            synced_todos = None
            try:
                res = self._api_request("/api/notion/sync", method="POST", payload={}, timeout=10.0)
                if isinstance(res, dict) and isinstance(res.get("todos"), list):
                    synced_todos = res["todos"]
            except Exception:
                pass

            if synced_todos is None and desktop_sync_server is not None:
                try:
                    synced_todos = desktop_sync_server.sync_notion_todos_now()
                except Exception as e:
                    print("Manual Notion sync fallback error:", e)

            with _widget_file_lock:
                if isinstance(synced_todos, list):
                    self.data["todos"] = [normalize_todo(t) for t in synced_todos if isinstance(t, dict)]
                    self.save_data()
                else:
                    self.data = self.load_data()

            def _on_done():
                if self.sync_sub is not None:
                    try:
                        self.sync_sub.config(
                            text=f"🟢 ซิงก์ Notion ล่าสุด {datetime.now().strftime('%H:%M:%S')}",
                            fg="#34d399",
                        )
                    except Exception:
                        pass
                if self.notion_sync_btn is not None:
                    try:
                        self.notion_sync_btn.config(text="🔄 ซิงก์ Notion", state="normal")
                    except Exception:
                        pass
                self.refresh_todos()

            if self.headless or self.root is None:
                _on_done()
            else:
                self._schedule_ui(_on_done)

        resolved_async = True if run_async is None else bool(run_async)
        return self._run_sync_task(_worker, run_async=resolved_async, async_mode=async_mode)

    def add_calendar_event(
        self,
        title: str = None,
        date: str = None,
        time_str: str = None,
        calendar: str = None,
        run_async: bool = None,
        async_mode: bool = None,
    ):
        """Create a new Google Calendar event via POST /api/calendar/events with local cache fallback."""
        if title is None and hasattr(self, "cal_title_entry") and self.cal_title_entry is not None:
            try:
                title = self.cal_title_entry.get()
            except Exception:
                title = ""
        title = (title or "").strip()
        if not title:
            return None

        if date is None and hasattr(self, "cal_date_entry") and self.cal_date_entry is not None:
            try:
                date = self.cal_date_entry.get()
            except Exception:
                date = ""
        date = (date or "").strip() or datetime.now().strftime("%Y-%m-%d")

        if time_str is None:
            if hasattr(self, "cal_time_entry") and self.cal_time_entry is not None:
                try:
                    time_str = self.cal_time_entry.get().strip()
                except Exception:
                    time_str = "ทั้งวัน"
            else:
                time_str = "ทั้งวัน"
        time_str = time_str or "ทั้งวัน"

        if calendar is None:
            if hasattr(self, "cal_account_var") and self.cal_account_var is not None:
                try:
                    calendar = self.cal_account_var.get().strip()
                except Exception:
                    calendar = "primary"
            else:
                calendar = "primary"

        calendar_id = CALENDAR_ACCOUNT_MAP.get(calendar, calendar or "pattarapon.thongsri@gmail.com")
        if "coop" in calendar_id or calendar in ("coop", "สหกรณ์"):
            source_type = "coop"
        elif "holiday" in calendar_id:
            source_type = "holiday"
        elif calendar in ("primary", "ส่วนตัว", "pattarapon.thongsri@gmail.com"):
            source_type = "primary"
        else:
            source_type = "custom"

        fallback_ev = {
            "id": f"custom-{int(time.time() * 1000)}",
            "title": title,
            "date": date,
            "time": time_str,
            "calendar": calendar_id,
            "sourceType": source_type,
            "url": "",
            "htmlLink": "",
        }

        result_holder = {"event": fallback_ev}

        def _worker():
            created_ev = None
            try:
                res = self._api_request(
                    "/api/calendar/events",
                    method="POST",
                    payload={
                        "title": title,
                        "date": date,
                        "time": time_str,
                        "calendar": calendar_id,
                    },
                    timeout=6.0,
                )
                if isinstance(res, dict) and isinstance(res.get("event"), dict):
                    created_ev = dict(res["event"])
                    created_ev.setdefault("sourceType", source_type)
            except Exception:
                pass

            final_ev = created_ev or fallback_ev
            result_holder["event"] = final_ev

            with _widget_file_lock:
                events = self.data.setdefault("events", [])
                if not any(str(e.get("id")) == str(final_ev.get("id")) for e in events if isinstance(e, dict)):
                    events.append(final_ev)
                self.save_data()

            def _on_done():
                if hasattr(self, "cal_title_entry") and self.cal_title_entry is not None:
                    try:
                        self.cal_title_entry.delete(0, tk.END)
                    except Exception:
                        pass
                self.refresh_calendar()

            if self.headless or self.root is None:
                _on_done()
            else:
                self._schedule_ui(_on_done)

        self._run_sync_task(_worker, run_async=run_async, async_mode=async_mode)
        return result_holder["event"]

    # ------------------------------------------------------------------
    # Tkinter UI Construction & Event Handlers
    # ------------------------------------------------------------------

    def toggle_pin(self):
        self.always_on_top = not self.always_on_top
        if self.root is not None and not self.headless:
            self.root.attributes("-topmost", self.always_on_top)
            self.pin_btn.config(
                text="📌 ปักหมุด: เปิด" if self.always_on_top else "📌 ปักหมุด: ปิด",
                bg="#4f46e5" if self.always_on_top else "#334155"
            )

    def _update_filter_buttons_ui(self):
        if self.headless or self.root is None or not self.filter_status_btns:
            return
        active_key = "done" if self.filter_status in ("done", "completed") else self.filter_status
        for key, btn in self.filter_status_btns.items():
            try:
                if key == active_key:
                    btn.config(bg="#4f46e5", fg="#ffffff")
                else:
                    btn.config(bg="#1e293b", fg="#94a3b8")
            except Exception:
                pass

    def toggle_cal_add_bar(self):
        if self.headless or self.cal_add_frame is None:
            return
        self.cal_add_visible = not self.cal_add_visible
        if self.cal_add_visible:
            self.cal_add_frame.pack(fill="x", pady=(0, 6), before=self.cal_container)
        else:
            self.cal_add_frame.pack_forget()

    def build_ui(self):
        # Header
        header = tk.Frame(self.root, bg="#1e293b", padx=10, pady=8)
        header.pack(fill="x")

        title_frame = tk.Frame(header, bg="#1e293b")
        title_frame.pack(side="left", fill="x", expand=True)

        title_lbl = tk.Label(
            title_frame,
            text="🗓️ To-Do & ตารางงาน 1 สัปดาห์",
            fg="#f8fafc",
            bg="#1e293b",
            font=("Tahoma", 10, "bold")
        )
        title_lbl.pack(anchor="w")

        self.sync_sub = tk.Label(
            title_frame,
            text="🟢 ฐานข้อมูลกลาง: desktop_todo_data.json",
            fg="#34d399",
            bg="#1e293b",
            font=("Tahoma", 7)
        )
        self.sync_sub.pack(anchor="w")

        header_btns = tk.Frame(header, bg="#1e293b")
        header_btns.pack(side="right")

        self.notion_sync_btn = tk.Button(
            header_btns,
            text="🔄 ซิงก์ Notion",
            command=self.manual_sync_notion,
            bg="#312e81",
            fg="#c7d2fe",
            relief="flat",
            padx=6,
            pady=3,
            font=("Tahoma", 8, "bold")
        )
        self.notion_sync_btn.pack(side="left", padx=(0, 4))

        self.pin_btn = tk.Button(
            header_btns,
            text="📌 ปักหมุด: เปิด",
            command=self.toggle_pin,
            bg="#4f46e5",
            fg="white",
            relief="flat",
            padx=6,
            pady=3,
            font=("Tahoma", 8, "bold")
        )
        self.pin_btn.pack(side="left")

        # To-Do Section
        todo_frame = tk.LabelFrame(
            self.root,
            text=" ✅ รายการสิ่งที่ต้องทำ (To-Do List) ",
            fg="#a5b4fc",
            bg="#0f172a",
            font=("Tahoma", 9, "bold"),
            padx=8,
            pady=6
        )
        todo_frame.pack(fill="both", expand=False, padx=10, pady=(8, 4))

        # Row 1: Task text entry + Add button
        entry_row = tk.Frame(todo_frame, bg="#0f172a")
        entry_row.pack(fill="x", pady=(0, 4))

        self.todo_entry = tk.Entry(
            entry_row,
            bg="#1e293b",
            fg="#f8fafc",
            insertbackground="white",
            relief="flat",
            font=("Tahoma", 9)
        )
        self.todo_entry.pack(side="left", fill="x", expand=True, ipady=4, padx=(0, 6))
        self.todo_entry.bind("<Return>", lambda e: self.add_todo())

        add_btn = tk.Button(
            entry_row,
            text="+ เพิ่ม",
            command=self.add_todo,
            bg="#4f46e5",
            fg="white",
            relief="flat",
            padx=10,
            pady=3,
            font=("Tahoma", 8, "bold")
        )
        add_btn.pack(side="right")

        # Row 2: Due Date, Priority & Category selectors
        meta_input_row = tk.Frame(todo_frame, bg="#0f172a")
        meta_input_row.pack(fill="x", pady=(0, 4))

        tk.Label(meta_input_row, text="📅", bg="#0f172a", fg="#94a3b8", font=("Tahoma", 8)).pack(side="left")
        self.due_date_var = tk.StringVar(value=datetime.now().strftime("%Y-%m-%d"))
        self.todo_due_entry = tk.Entry(
            meta_input_row,
            textvariable=self.due_date_var,
            width=11,
            bg="#1e293b",
            fg="#e2e8f0",
            insertbackground="white",
            relief="flat",
            font=("Tahoma", 8)
        )
        self.todo_due_entry.pack(side="left", padx=(2, 6), ipady=2)

        tk.Label(meta_input_row, text="ระดับ:", bg="#0f172a", fg="#94a3b8", font=("Tahoma", 8)).pack(side="left")
        self.todo_priority_var = tk.StringVar(value="medium")
        self.priority_var = self.todo_priority_var
        priority_menu = tk.OptionMenu(meta_input_row, self.todo_priority_var, *PRIORITY_OPTIONS)
        priority_menu.config(bg="#1e293b", fg="#f8fafc", highlightthickness=0, relief="flat", font=("Tahoma", 7), padx=4, pady=1)
        priority_menu["menu"].config(bg="#1e293b", fg="#f8fafc", font=("Tahoma", 8))
        priority_menu.pack(side="left", padx=(2, 6))

        tk.Label(meta_input_row, text="หมวด:", bg="#0f172a", fg="#94a3b8", font=("Tahoma", 8)).pack(side="left")
        self.todo_category_var = tk.StringVar(value="งานประจำ")
        self.category_var = self.todo_category_var
        category_menu = tk.OptionMenu(meta_input_row, self.todo_category_var, *CATEGORY_OPTIONS)
        category_menu.config(bg="#1e293b", fg="#f8fafc", highlightthickness=0, relief="flat", font=("Tahoma", 7), padx=4, pady=1)
        category_menu["menu"].config(bg="#1e293b", fg="#f8fafc", font=("Tahoma", 8))
        category_menu.pack(side="left", padx=(2, 0))

        # Row 3a: Status Filter Pills + Clear Completed button
        filter_row = tk.Frame(todo_frame, bg="#0f172a")
        filter_row.pack(fill="x", pady=(0, 4))

        for status_key, label_txt in [("all", "ทั้งหมด"), ("active", "ค้างอยู่"), ("done", "เสร็จแล้ว")]:
            btn = tk.Button(
                filter_row,
                text=label_txt,
                command=lambda k=status_key: self.set_filter(status=k),
                bg="#4f46e5" if status_key == "all" else "#1e293b",
                fg="#ffffff" if status_key == "all" else "#94a3b8",
                relief="flat",
                padx=6,
                pady=1,
                font=("Tahoma", 7, "bold")
            )
            btn.pack(side="left", padx=(0, 3))
            self.filter_status_btns[status_key] = btn

        clear_done_btn = tk.Button(
            filter_row,
            text="🗑️ ล้างงานเสร็จ",
            command=self.clear_completed_todos,
            bg="#334155",
            fg="#fda4af",
            relief="flat",
            padx=6,
            pady=1,
            font=("Tahoma", 7, "bold")
        )
        clear_done_btn.pack(side="right")

        # Row 3b: Search input + Category filter selector
        search_row = tk.Frame(todo_frame, bg="#0f172a")
        search_row.pack(fill="x", pady=(0, 4))

        tk.Label(search_row, text="🔍", bg="#0f172a", fg="#94a3b8", font=("Tahoma", 8)).pack(side="left")
        self.search_var = tk.StringVar(value="")
        search_entry = tk.Entry(
            search_row,
            textvariable=self.search_var,
            bg="#1e293b",
            fg="#f8fafc",
            insertbackground="white",
            relief="flat",
            font=("Tahoma", 8)
        )
        search_entry.pack(side="left", fill="x", expand=True, padx=(2, 6), ipady=2)
        search_entry.bind("<KeyRelease>", lambda e: self.set_filter(search=self.search_var.get()))

        tk.Label(search_row, text="กรองหมวด:", bg="#0f172a", fg="#94a3b8", font=("Tahoma", 7)).pack(side="left")
        self.filter_cat_var = tk.StringVar(value="ทั้งหมด")
        filter_cat_menu = tk.OptionMenu(
            search_row,
            self.filter_cat_var,
            "ทั้งหมด",
            *CATEGORY_OPTIONS,
            command=lambda val: self.set_filter(category="all" if val == "ทั้งหมด" else val),
        )
        filter_cat_menu.config(bg="#1e293b", fg="#cbd5e1", highlightthickness=0, relief="flat", font=("Tahoma", 7), padx=4, pady=0)
        filter_cat_menu["menu"].config(bg="#1e293b", fg="#f8fafc", font=("Tahoma", 8))
        filter_cat_menu.pack(side="right")

        # Row 4: Canvas with scrollbar for todos
        todo_container = tk.Frame(todo_frame, bg="#0f172a")
        todo_container.pack(fill="both", expand=True)

        self.todo_canvas = tk.Canvas(todo_container, bg="#0f172a", highlightthickness=0, height=195)
        self.todo_scrollbar = ttk.Scrollbar(todo_container, orient="vertical", command=self.todo_canvas.yview)
        self.todo_list_frame = tk.Frame(self.todo_canvas, bg="#0f172a")

        self.todo_list_frame.bind(
            "<Configure>",
            lambda e: self.todo_canvas.configure(scrollregion=self.todo_canvas.bbox("all"))
        )
        self.todo_canvas_window = self.todo_canvas.create_window((0, 0), window=self.todo_list_frame, anchor="nw")
        self.todo_canvas.bind(
            "<Configure>",
            lambda e: self.todo_canvas.itemconfig(self.todo_canvas_window, width=e.width)
        )
        self.todo_canvas.configure(xscrollcommand=None, yscrollcommand=self.todo_scrollbar.set)

        self.todo_canvas.pack(side="left", fill="both", expand=True)
        self.todo_scrollbar.pack(side="right", fill="y")

        # 1-Week Google Calendar Section
        cal_frame = tk.LabelFrame(
            self.root,
            text=" 📅 ตารางงาน Google Calendar (1 สัปดาห์) ",
            fg="#38bdf8",
            bg="#0f172a",
            font=("Tahoma", 9, "bold"),
            padx=8,
            pady=6
        )
        cal_frame.pack(fill="both", expand=True, padx=10, pady=4)

        cal_top_bar = tk.Frame(cal_frame, bg="#0f172a")
        cal_top_bar.pack(fill="x", pady=(0, 4))

        self.cal_status_lbl = tk.Label(
            cal_top_bar,
            text="ซิงก์สด 7 วันล่วงหน้า",
            fg="#94a3b8",
            bg="#0f172a",
            font=("Tahoma", 8)
        )
        self.cal_status_lbl.pack(side="left")

        self.cal_refresh_btn = tk.Button(
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
        self.cal_refresh_btn.pack(side="right")

        cal_add_toggle_btn = tk.Button(
            cal_top_bar,
            text="+ นัดหมาย",
            command=self.toggle_cal_add_bar,
            bg="#0284c7",
            fg="white",
            relief="flat",
            padx=6,
            pady=1,
            font=("Tahoma", 8, "bold")
        )
        cal_add_toggle_btn.pack(side="right", padx=(0, 4))

        # Collapsible Inline Quick-Add Calendar Event Bar
        self.cal_add_frame = tk.Frame(cal_frame, bg="#1e293b", padx=6, pady=4)
        cal_row1 = tk.Frame(self.cal_add_frame, bg="#1e293b")
        cal_row1.pack(fill="x", pady=(0, 3))
        tk.Label(cal_row1, text="หัวข้อ:", bg="#1e293b", fg="#cbd5e1", font=("Tahoma", 7)).pack(side="left")
        self.cal_title_entry = tk.Entry(cal_row1, bg="#0f172a", fg="#f8fafc", insertbackground="white", relief="flat", font=("Tahoma", 8))
        self.cal_title_entry.pack(side="left", fill="x", expand=True, padx=4)

        cal_row2 = tk.Frame(self.cal_add_frame, bg="#1e293b")
        cal_row2.pack(fill="x")
        self.cal_date_entry = tk.Entry(cal_row2, width=10, bg="#0f172a", fg="#e2e8f0", insertbackground="white", relief="flat", font=("Tahoma", 7))
        self.cal_date_entry.insert(0, datetime.now().strftime("%Y-%m-%d"))
        self.cal_date_entry.pack(side="left", padx=(0, 3))

        self.cal_time_entry = tk.Entry(cal_row2, width=11, bg="#0f172a", fg="#e2e8f0", insertbackground="white", relief="flat", font=("Tahoma", 7))
        self.cal_time_entry.insert(0, "09:00 - 10:00")
        self.cal_time_entry.pack(side="left", padx=(0, 3))

        self.cal_account_var = tk.StringVar(value="ส่วนตัว")
        cal_acc_menu = tk.OptionMenu(cal_row2, self.cal_account_var, "ส่วนตัว", "สหกรณ์")
        cal_acc_menu.config(bg="#0f172a", fg="#cbd5e1", highlightthickness=0, relief="flat", font=("Tahoma", 7), padx=2, pady=0)
        cal_acc_menu["menu"].config(bg="#1e293b", fg="#f8fafc", font=("Tahoma", 8))
        cal_acc_menu.pack(side="left", padx=(0, 3))

        tk.Button(
            cal_row2,
            text="บันทึก",
            command=self.add_calendar_event,
            bg="#059669",
            fg="white",
            relief="flat",
            padx=6,
            pady=1,
            font=("Tahoma", 7, "bold")
        ).pack(side="right")

        self.cal_container = tk.Frame(cal_frame, bg="#0f172a")
        self.cal_container.pack(fill="both", expand=True)

        self.cal_canvas = tk.Canvas(self.cal_container, bg="#0f172a", highlightthickness=0)
        self.cal_scrollbar = ttk.Scrollbar(self.cal_container, orient="vertical", command=self.cal_canvas.yview)
        self.cal_list_frame = tk.Frame(self.cal_canvas, bg="#0f172a")

        self.cal_list_frame.bind(
            "<Configure>",
            lambda e: self.cal_canvas.configure(scrollregion=self.cal_canvas.bbox("all"))
        )
        self.cal_canvas_window = self.cal_canvas.create_window((0, 0), window=self.cal_list_frame, anchor="nw")
        self.cal_canvas.bind(
            "<Configure>",
            lambda e: self.cal_canvas.itemconfig(self.cal_canvas_window, width=e.width)
        )
        self.cal_canvas.configure(yscrollcommand=self.cal_scrollbar.set)

        self.cal_canvas.pack(side="left", fill="both", expand=True)
        self.cal_scrollbar.pack(side="right", fill="y")

        # Footer button to open rich HTML version
        footer = tk.Frame(self.root, bg="#0f172a", padx=10, pady=5)
        footer.pack(fill="x")
        open_web_btn = tk.Button(
            footer,
            text="🌐 เปิดหน้าต่าง Web App แบบเต็ม / ดูตารางละเอียด",
            command=self.open_html_app,
            bg="#059669",
            fg="white",
            relief="flat",
            pady=5,
            font=("Tahoma", 9, "bold")
        )
        open_web_btn.pack(fill="x")

    def open_edit_todo_dialog(self, idx_or_id):
        if self.headless or self.root is None:
            return None
        idx = self._find_todo_index(idx_or_id)
        if idx < 0:
            return None
        item = self.data["todos"][idx]
        target_id = item.get("id", idx)

        dlg = tk.Toplevel(self.root)
        dlg.title("แก้ไขรายการงาน (Edit To-Do)")
        dlg.configure(bg="#0f172a", padx=12, pady=10)
        dlg.attributes("-topmost", True)
        dlg.geometry("340x240")
        dlg.transient(self.root)

        tk.Label(dlg, text="ชื่องาน:", bg="#0f172a", fg="#cbd5e1", font=("Tahoma", 8, "bold")).pack(anchor="w")
        txt_entry = tk.Entry(dlg, bg="#1e293b", fg="#f8fafc", insertbackground="white", relief="flat", font=("Tahoma", 9))
        txt_entry.insert(0, item.get("text", ""))
        txt_entry.pack(fill="x", pady=(2, 8), ipady=3)

        tk.Label(dlg, text="วันครบกำหนด (YYYY-MM-DD):", bg="#0f172a", fg="#cbd5e1", font=("Tahoma", 8, "bold")).pack(anchor="w")
        due_entry = tk.Entry(dlg, bg="#1e293b", fg="#f8fafc", insertbackground="white", relief="flat", font=("Tahoma", 9))
        due_entry.insert(0, item.get("dueDate", ""))
        due_entry.pack(fill="x", pady=(2, 8), ipady=3)

        row_opts = tk.Frame(dlg, bg="#0f172a")
        row_opts.pack(fill="x", pady=(0, 10))

        tk.Label(row_opts, text="ความสำคัญ:", bg="#0f172a", fg="#cbd5e1", font=("Tahoma", 8)).pack(side="left")
        p_var = tk.StringVar(value=item.get("priority", "medium"))
        p_menu = tk.OptionMenu(row_opts, p_var, *PRIORITY_OPTIONS)
        p_menu.config(bg="#1e293b", fg="#f8fafc", highlightthickness=0, relief="flat", font=("Tahoma", 8))
        p_menu.pack(side="left", padx=(4, 10))

        tk.Label(row_opts, text="หมวดหมู่:", bg="#0f172a", fg="#cbd5e1", font=("Tahoma", 8)).pack(side="left")
        c_var = tk.StringVar(value=item.get("category", "งานประจำ"))
        c_menu = tk.OptionMenu(row_opts, c_var, *CATEGORY_OPTIONS)
        c_menu.config(bg="#1e293b", fg="#f8fafc", highlightthickness=0, relief="flat", font=("Tahoma", 8))
        c_menu.pack(side="left", padx=4)

        def _save():
            self.edit_todo(
                target_id,
                text=txt_entry.get(),
                due_date=due_entry.get(),
                priority=p_var.get(),
                category=c_var.get(),
            )
            dlg.destroy()

        btn_row = tk.Frame(dlg, bg="#0f172a")
        btn_row.pack(fill="x", pady=(4, 0))
        tk.Button(btn_row, text="💾 บันทึก", command=_save, bg="#4f46e5", fg="white", relief="flat", padx=12, pady=4, font=("Tahoma", 8, "bold")).pack(side="right")
        tk.Button(btn_row, text="ยกเลิก", command=dlg.destroy, bg="#334155", fg="#cbd5e1", relief="flat", padx=10, pady=4, font=("Tahoma", 8)).pack(side="right", padx=(0, 6))
        txt_entry.bind("<Return>", lambda e: _save())

    def refresh_todos(self):
        if self.headless or self.root is None or self.todo_list_frame is None:
            return

        for w in self.todo_list_frame.winfo_children():
            w.destroy()

        all_todos = self.data.get("todos", [])
        done_count = sum(1 for t in all_todos if isinstance(t, dict) and (t.get("done") or t.get("completed")))
        if self.sync_sub is not None:
            try:
                current_txt = self.sync_sub.cget("text")
                if not current_txt.startswith("⏳"):
                    self.sync_sub.config(
                        text=f"🟢 ฐานข้อมูลกลาง: เสร็จ {done_count}/{len(all_todos)} งาน"
                    )
            except Exception:
                pass

        filtered_todos = self.get_filtered_todos()
        if not filtered_todos:
            empty_lbl = tk.Label(
                self.todo_list_frame,
                text="ไม่มีรายการงานในหมวด/ตัวกรองนี้ เพิ่มงานใหม่ด้านบนได้เลย",
                fg="#64748b",
                bg="#0f172a",
                font=("Tahoma", 8)
            )
            empty_lbl.pack(pady=10)
            return

        today_iso = datetime.now().strftime("%Y-%m-%d")

        for item in filtered_todos:
            item_id = item.get("id")
            is_done = bool(item.get("done") or item.get("completed"))
            due_date = (item.get("dueDate") or "").strip()
            is_overdue = bool(not is_done and due_date and len(due_date) == 10 and due_date < today_iso)

            card = tk.Frame(self.todo_list_frame, bg="#1e293b", pady=4, padx=6)
            card.pack(fill="x", pady=2, padx=2)

            # Top row: checkbox, title, edit button, delete button
            top_row = tk.Frame(card, bg="#1e293b")
            top_row.pack(fill="x")

            chk_btn = tk.Button(
                top_row,
                text="☑" if is_done else "☐",
                command=lambda tid=item_id: self.toggle_todo(tid),
                bg="#1e293b",
                fg="#34d399" if is_done else "#94a3b8",
                relief="flat",
                font=("Tahoma", 10, "bold")
            )
            chk_btn.pack(side="left")

            title_txt = item.get("text", "")
            lbl = tk.Label(
                top_row,
                text=title_txt,
                fg="#64748b" if is_done else "#f1f5f9",
                bg="#1e293b",
                anchor="w",
                justify="left",
                wraplength=235,
                font=("Tahoma", 9, "overstrike" if is_done else "normal")
            )
            lbl.pack(side="left", fill="x", expand=True, padx=4)

            del_btn = tk.Button(
                top_row,
                text="✕",
                command=lambda tid=item_id: self.delete_todo(tid),
                bg="#1e293b",
                fg="#f87171",
                relief="flat",
                padx=3,
                font=("Tahoma", 8)
            )
            del_btn.pack(side="right")

            edit_btn = tk.Button(
                top_row,
                text="✏️",
                command=lambda tid=item_id: self.open_edit_todo_dialog(tid),
                bg="#1e293b",
                fg="#94a3b8",
                relief="flat",
                padx=3,
                font=("Tahoma", 8)
            )
            edit_btn.pack(side="right", padx=(0, 2))

            # Bottom metadata row: Priority, Category, DueDate, Overdue, Notion Link
            meta_row = tk.Frame(card, bg="#1e293b")
            meta_row.pack(fill="x", padx=(24, 0), pady=(2, 0))

            p_key = item.get("priority", "medium")
            p_meta = PRIORITY_META.get(p_key, PRIORITY_META["medium"])
            tk.Label(
                meta_row,
                text=p_meta["label"],
                bg=p_meta["bg"],
                fg=p_meta["fg"],
                font=("Tahoma", 7, "bold"),
                padx=4,
                pady=0
            ).pack(side="left", padx=(0, 4))

            cat_name = item.get("category") or "งานประจำ"
            tk.Label(
                meta_row,
                text=CATEGORY_ICONS.get(cat_name, cat_name),
                bg="#0f172a",
                fg="#cbd5e1",
                font=("Tahoma", 7),
                padx=4,
                pady=0
            ).pack(side="left", padx=(0, 4))

            if due_date:
                tk.Label(
                    meta_row,
                    text=f"📅 {due_date}",
                    bg="#1e293b",
                    fg="#818cf8" if due_date == today_iso else "#94a3b8",
                    font=("Tahoma", 7, "bold" if due_date == today_iso else "normal"),
                ).pack(side="left", padx=(0, 4))

            if is_overdue:
                tk.Label(
                    meta_row,
                    text="⚠️ เลยกำหนด",
                    bg="#881337",
                    fg="#fda4af",
                    font=("Tahoma", 7, "bold"),
                    padx=4,
                    pady=0
                ).pack(side="left", padx=(0, 4))

            notion_url = (item.get("notion_url") or "").strip()
            if notion_url:
                tk.Button(
                    meta_row,
                    text="Notion ↗",
                    command=lambda u=notion_url: webbrowser.open(u),
                    bg="#312e81",
                    fg="#c7d2fe",
                    relief="flat",
                    padx=4,
                    pady=0,
                    font=("Tahoma", 7, "bold")
                ).pack(side="right")

    def refresh_calendar(self):
        if self.headless or self.root is None or self.cal_list_frame is None:
            return

        for w in self.cal_list_frame.winfo_children():
            w.destroy()

        events = self.data.get("events", [])
        if not events:
            tk.Label(
                self.cal_list_frame,
                text="ไม่มีตารางนัดหมายในสัปดาห์นี้",
                fg="#64748b",
                bg="#0f172a",
                font=("Tahoma", 8)
            ).pack(pady=10)
            return

        for ev in events:
            if not isinstance(ev, dict):
                continue
            source_type = ev.get("sourceType")
            if not source_type:
                cal_str = str(ev.get("calendar") or "").lower()
                if "coop" in cal_str:
                    source_type = "coop"
                elif "holiday" in cal_str:
                    source_type = "holiday"
                else:
                    source_type = "primary"
            s_meta = SOURCE_TYPE_META.get(source_type, SOURCE_TYPE_META["primary"])

            outer_card = tk.Frame(self.cal_list_frame, bg="#1e293b")
            outer_card.pack(fill="x", pady=3, padx=2)

            stripe = tk.Frame(outer_card, bg=s_meta["border"], width=4)
            stripe.pack(side="left", fill="y")

            card = tk.Frame(outer_card, bg="#1e293b", padx=6, pady=5)
            card.pack(side="left", fill="both", expand=True)

            top = tk.Frame(card, bg="#1e293b")
            top.pack(fill="x")

            tk.Label(
                top,
                text=s_meta["label"],
                bg=s_meta["bg"],
                fg=s_meta["fg"],
                font=("Tahoma", 7, "bold"),
                padx=4,
                pady=0
            ).pack(side="left", padx=(0, 5))

            date_lbl = tk.Label(
                top,
                text=f"📅 {ev.get('date', '')} ({ev.get('time', 'ทั้งวัน')})",
                fg="#38bdf8",
                bg="#1e293b",
                font=("Tahoma", 8, "bold")
            )
            date_lbl.pack(side="left")

            link_url = ev.get("url") or ev.get("htmlLink")
            if link_url:
                open_btn = tk.Button(
                    top,
                    text="เปิดลิงก์ ↗",
                    command=lambda u=link_url: webbrowser.open(u),
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
                wraplength=330,
                font=("Tahoma", 9, "bold")
            )
            title_lbl.pack(fill="x", pady=(2, 0))

    def open_html_app(self) -> str:
        port = get_server_port()
        url = f"http://127.0.0.1:{port}"
        try:
            webbrowser.open(url)
        except Exception:
            pass
        return url


if __name__ == "__main__":
    root = tk.Tk()
    app = RightCornerDesktopWidget(root)
    root.mainloop()
