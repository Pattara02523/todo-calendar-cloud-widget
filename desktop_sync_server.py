# -*- coding: utf-8 -*-
"""
Desktop Todo & Calendar - Background Sync Server
Syncs data between Desktop Python Widget and HTML Web Widget via shared desktop_todo_data.json,
provides real-time 2-way Google Calendar integration via Google Calendar API,
and provides real-time 2-way Notion Database integration for To-Do List.
"""

import http.server
import json
import os
import socket
import socketserver
import sys
import threading
import time

# Protect against pythonw NoneType stdout/stderr on Windows
if sys.stdout is None:
    try:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    except Exception:
        pass
if sys.stderr is None:
    try:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    except Exception:
        pass

APP_DIR = os.path.dirname(os.path.abspath(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

try:
    import google_calendar_service
except Exception as e:
    print("[Sync Server] Error importing google_calendar_service:", e)
    google_calendar_service = None

try:
    import notion_service
except Exception as e:
    print("[Sync Server] Error importing notion_service:", e)
    notion_service = None

PORT = int(os.environ.get("PORT", 18234))
HOST = os.environ.get("HOST", "0.0.0.0" if "PORT" in os.environ else "127.0.0.1")
DATA_FILE = os.path.join(APP_DIR, "desktop_todo_data.json")
HTML_FILE = os.path.join(APP_DIR, "desktop_todo_calendar_widget.html")

file_lock = threading.Lock()


def is_port_in_use(port):
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/ping")
        with urllib.request.urlopen(req, timeout=0.8) as r:
            return r.status == 200
    except Exception:
        return False


def load_data():
    with file_lock:
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"todos": [], "events": []}


def save_data(data):
    with file_lock:
        try:
            with open(DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            print("[Sync Server] Error saving data:", e)
            return False


def sync_google_calendar_now():
    """Fetch live events from Google Calendar and update local JSON cache"""
    if not google_calendar_service:
        return []
    try:
        events = google_calendar_service.fetch_all_7day_events()
        if events is not None:
            data = load_data()
            data["events"] = events
            save_data(data)
            return events
    except Exception as e:
        print("[Google Calendar Sync] Error:", e)
    return load_data().get("events", [])


def background_calendar_sync_worker():
    """Background loop: fetch Google Calendar events every 5 minutes (300 seconds)"""
    time.sleep(2)  # Initial slight delay
    sync_google_calendar_now()
    while True:
        time.sleep(300)
        try:
            sync_google_calendar_now()
        except Exception as e:
            print("[Google Calendar Background] Error:", e)


def sync_notion_todos_now():
    """Sync todos with Notion database if connected"""
    if not notion_service:
        return load_data().get("todos", [])
    try:
        current_data = load_data()
        local_todos = current_data.get("todos", [])
        synced_todos, is_synced = notion_service.sync_todos_with_notion(local_todos)
        if is_synced and synced_todos is not None:
            current_data["todos"] = synced_todos
            save_data(current_data)
            return synced_todos
    except Exception as e:
        print("[Notion Sync] Error:", e)
    return load_data().get("todos", [])


def background_notion_sync_worker():
    """Background loop: sync Notion tasks every 60 seconds"""
    time.sleep(3)
    sync_notion_todos_now()
    while True:
        time.sleep(60)
        try:
            sync_notion_todos_now()
        except Exception as e:
            print("[Notion Background] Error:", e)


def async_push_todo_changes_to_notion(old_todos, new_todos):
    """Detect additions, deletions, updates and push to Notion asynchronously"""
    if not notion_service:
        return

    def _worker():
        try:
            cfg = notion_service.load_config()
            db_id = cfg.get("database_id")
            if not db_id:
                # Try auto-detect
                db_id = notion_service.get_target_database_id()
                if not db_id:
                    return

            old_map = {t.get("id"): t for t in old_todos}
            new_map = {t.get("id"): t for t in new_todos}

            # 1. Deletions: in old but not in new
            for old_id, old_item in old_map.items():
                if old_id not in new_map:
                    # If this is a Notion UUID, archive it
                    if len(old_id) >= 32 and "-" in old_id:
                        try:
                            notion_service.archive_notion_todo(old_id)
                        except Exception as e:
                            print(f"[Notion Async] Error archiving {old_id}:", e)

            # 2. Additions or Updates
            updated_cache = False
            current_data = load_data()
            cache_todos = current_data.get("todos", [])

            for item in new_todos:
                item_id = item.get("id", "")
                is_uuid = len(item_id) >= 32 and "-" in item_id

                if not is_uuid:
                    # New item created locally -> push to Notion
                    try:
                        created = notion_service.create_notion_todo(
                            text=item.get("text", ""),
                            due_date=item.get("dueDate"),
                            priority=item.get("priority", "medium"),
                            category=item.get("category", "งานประจำ"),
                            database_id=db_id
                        )
                        if item.get("completed") or item.get("done"):
                            notion_service.update_notion_todo(created["id"], completed=True)
                            created["completed"] = True
                            created["done"] = True

                        # Replace local temporary id in cache with Notion UUID
                        for c_item in cache_todos:
                            if c_item.get("id") == item_id:
                                c_item["id"] = created["id"]
                                c_item["notion_url"] = created.get("notion_url", "")
                                updated_cache = True
                                break
                    except Exception as e:
                        print(f"[Notion Async] Error creating new todo in Notion:", e)

                else:
                    # Check if completed status changed
                    old_item = old_map.get(item_id)
                    curr_completed = bool(item.get("completed") or item.get("done"))
                    prev_completed = bool(old_item.get("completed") or old_item.get("done")) if old_item else False

                    if old_item is None or curr_completed != prev_completed:
                        try:
                            notion_service.update_notion_todo(item_id, completed=curr_completed)
                        except Exception as e:
                            print(f"[Notion Async] Error updating {item_id}:", e)

            if updated_cache:
                current_data["todos"] = cache_todos
                save_data(current_data)

        except Exception as e:
            print("[Notion Async Worker] Error:", e)

    threading.Thread(target=_worker, daemon=True).start()


class SyncRequestHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Accept")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/?"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            if os.path.exists(HTML_FILE):
                with open(HTML_FILE, "rb") as f:
                    self.wfile.write(f.read())
            else:
                self.wfile.write(b"HTML Widget not found")
            return

        if self.path == "/api/data" or self.path == "/api/todos":
            data = load_data()
            payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        if self.path == "/api/calendar/events" or self.path.startswith("/api/calendar/events?"):
            events = sync_google_calendar_now()
            payload = json.dumps(events, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        if self.path == "/api/notion/status":
            info = {
                "installed": notion_service is not None,
                "connected": False,
                "bot_name": "ICT",
                "workspace": "pattarapon thongsri's Space",
                "database_id": None,
                "databases_found": [],
                "pages_found": []
            }
            if notion_service:
                try:
                    cfg = notion_service.load_config()
                    conn = notion_service.test_connection()
                    res = notion_service.discover_resources()
                    info.update({
                        "connected": conn.get("status") == "connected",
                        "bot_name": conn.get("bot_name", "ICT"),
                        "workspace": conn.get("workspace", ""),
                        "database_id": cfg.get("database_id"),
                        "databases_found": res.get("databases", []),
                        "pages_found": res.get("pages", [])
                    })
                except Exception as e:
                    info["error"] = str(e)

            payload = json.dumps(info, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        if self.path == "/api/ping":
            payload = b'{"status":"ok"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        super().do_GET()

    def do_POST(self):
        if self.path == "/api/todos":
            content_length = int(self.headers.get("Content-Length", 0))
            post_body = self.rfile.read(content_length)
            try:
                todos_data = json.loads(post_body.decode("utf-8"))
                current_data = load_data()
                old_todos = current_data.get("todos", [])
                current_data["todos"] = todos_data
                save_data(current_data)

                # Push changes to Notion asynchronously
                async_push_todo_changes_to_notion(old_todos, todos_data)

                res = json.dumps({"status": "success", "count": len(todos_data)}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(res)))
                self.end_headers()
                self.wfile.write(res)
            except Exception as e:
                err = json.dumps({"error": str(e)}).encode("utf-8")
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(err)))
                self.end_headers()
                self.wfile.write(err)
            return

        if self.path == "/api/notion/sync":
            try:
                synced_todos = sync_notion_todos_now()
                res = json.dumps({"status": "success", "todos": synced_todos}, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(res)))
                self.end_headers()
                self.wfile.write(res)
            except Exception as e:
                err = json.dumps({"error": str(e)}).encode("utf-8")
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(err)))
                self.end_headers()
                self.wfile.write(err)
            return

        if self.path == "/api/notion/connect":
            content_length = int(self.headers.get("Content-Length", 0))
            post_body = self.rfile.read(content_length)
            try:
                req_data = json.loads(post_body.decode("utf-8")) if post_body else {}
                cfg = notion_service.load_config() if notion_service else {}

                db_id = req_data.get("database_id")
                page_id = req_data.get("page_id")

                if db_id:
                    cfg["database_id"] = db_id
                    notion_service.save_config(cfg)
                elif page_id and notion_service:
                    created_db = notion_service.create_tasks_database(page_id)
                    cfg["database_id"] = created_db.get("id")
                    notion_service.save_config(cfg)
                elif notion_service:
                    # Auto detect
                    detected_id = notion_service.get_target_database_id()
                    if not detected_id:
                        raise ValueError("No Notion database or page found yet. Please connect the 'ICT' integration to a Notion page or database first.")

                synced_todos = sync_notion_todos_now()
                res = json.dumps({"status": "connected", "database_id": cfg.get("database_id"), "todos": synced_todos}, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(res)))
                self.end_headers()
                self.wfile.write(res)
            except Exception as e:
                err = json.dumps({"error": str(e)}).encode("utf-8")
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(err)))
                self.end_headers()
                self.wfile.write(err)
            return

        if self.path == "/api/calendar/events":
            content_length = int(self.headers.get("Content-Length", 0))
            post_body = self.rfile.read(content_length)
            try:
                req_data = json.loads(post_body.decode("utf-8"))
                title = req_data.get("title", "").strip()
                date_str = req_data.get("date", "").strip()
                time_str = req_data.get("time", "").strip()
                calendar_id = req_data.get("calendar", "pattarapon.thongsri@gmail.com")
                description = req_data.get("description", "")

                if not title or not date_str:
                    raise ValueError("title and date are required")

                if google_calendar_service:
                    created_ev = google_calendar_service.create_event(
                        title=title,
                        date_str=date_str,
                        time_str=time_str,
                        calendar_id=calendar_id,
                        description=description
                    )
                    sync_google_calendar_now()
                    res_obj = {"status": "success", "event": created_ev}
                else:
                    raise RuntimeError("google_calendar_service module not loaded")

                res = json.dumps(res_obj, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(res)))
                self.end_headers()
                self.wfile.write(res)
            except Exception as e:
                err = json.dumps({"error": str(e)}).encode("utf-8")
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(err)))
                self.end_headers()
                self.wfile.write(err)
            return

        if self.path == "/api/events":
            content_length = int(self.headers.get("Content-Length", 0))
            post_body = self.rfile.read(content_length)
            try:
                events_data = json.loads(post_body.decode("utf-8"))
                current_data = load_data()
                current_data["events"] = events_data
                save_data(current_data)

                res = json.dumps({"status": "success", "count": len(events_data)}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(res)))
                self.end_headers()
                self.wfile.write(res)
            except Exception as e:
                err = json.dumps({"error": str(e)}).encode("utf-8")
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(err)))
                self.end_headers()
                self.wfile.write(err)
            return

        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):
        pass


class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def start_server():
    if is_port_in_use(PORT):
        print(f"[Sync Server] Port {PORT} is already running.")
        return None

    os.chdir(APP_DIR)
    server = None
    for attempt in range(4):
        try:
            server = ThreadedHTTPServer((HOST, PORT), SyncRequestHandler)
            break
        except OSError as e:
            if attempt < 3:
                time.sleep(1.2)
            else:
                print(f"[Sync Server] Could not bind to {HOST}:{PORT} -> {e}")
                return None

    # Start background calendar worker
    t_cal = threading.Thread(target=background_calendar_sync_worker, daemon=True)
    t_cal.start()

    # Start background notion worker
    t_notion = threading.Thread(target=background_notion_sync_worker, daemon=True)
    t_notion.start()

    print(f"[Sync Server] Running on http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except Exception as e:
        print("[Sync Server] Stopped:", e)
    finally:
        server.server_close()


def start_in_thread():
    if is_port_in_use(PORT):
        return
    t = threading.Thread(target=start_server, daemon=True)
    t.start()
    return t


if __name__ == "__main__":
    start_server()
