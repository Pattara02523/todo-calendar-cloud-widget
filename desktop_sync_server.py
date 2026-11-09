# -*- coding: utf-8 -*-
"""
Desktop Todo & Calendar - Background Sync Server
Syncs data between Desktop Python Widget and HTML Web Widget via shared desktop_todo_data.json,
provides real-time 2-way Google Calendar integration via Google Calendar API,
and provides real-time 2-way Notion Database integration for To-Do List.
"""

import datetime
import http.server
import json
import os
import socket
import socketserver
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

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

BKK_TZ = datetime.timezone(datetime.timedelta(hours=7))


def load_dotenv(dotenv_path=None, override=False):
    """Zero-dependency .env file loader that preserves existing environment variables unless override=True."""
    path = dotenv_path or os.path.join(APP_DIR, ".env")
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[len("export "):].strip()
                if "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip()
                if len(val) >= 2 and ((val[0] == '"' and val[-1] == '"') or (val[0] == "'" and val[-1] == "'")):
                    val = val[1:-1]
                if key and (override or key not in os.environ):
                    os.environ[key] = val
    except Exception as e:
        print("[Sync Server] Warning loading .env:", e)


def load_dotenv_file(dotenv_path=None, override=True):
    """Alias for load_dotenv with override=True by default for explicit file loading."""
    return load_dotenv(dotenv_path=dotenv_path, override=override)


load_dotenv()

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
MANIFEST_FILE = os.path.join(APP_DIR, "manifest.json")
SW_FILE = os.path.join(APP_DIR, "sw.js")

# Reentrant lock protecting all read-modify-write cycles on desktop_todo_data.json
data_lock = threading.RLock()
file_lock = data_lock  # Backward-compatible alias

# Lock serializing Notion background syncs and async user push operations
notion_sync_lock = threading.Lock()

# Runtime status tracking for /api/health and /api/calendar/status
calendar_sync_state = {
    "status": "cached_fallback",
    "error": None,
    "last_sync": None
}
notion_sync_state = {
    "connected": False,
    "last_sync": None,
    "error": None
}


def now_bkk_iso():
    return datetime.datetime.now(BKK_TZ).strftime("%Y-%m-%dT%H:%M:%S")


def get_data_file():
    """Return active data file path, honoring runtime TODO_DATA_FILE overrides."""
    override = os.environ.get("TODO_DATA_FILE", "").strip()
    if override:
        return os.path.abspath(override)
    return DATA_FILE


def get_api_key():
    """Return configured WIDGET_API_KEY (empty string if auth is disabled)."""
    return os.environ.get("WIDGET_API_KEY", "").strip()


def is_background_disabled():
    """Check if background polling threads should be disabled (e.g. during tests)."""
    return os.environ.get("DISABLE_BACKGROUND_WORKERS", "").strip().lower() in ("1", "true", "yes")


def is_notion_uuid(val):
    """Return True if val looks like a 32/36-char Notion UUID rather than a local 'todo-...' ID."""
    return isinstance(val, str) and len(val) >= 32 and "-" in val and not val.startswith("todo-")


def normalize_todo(todo, prev_todo=None):
    """Ensure todo dict has consistent fields and completed == done."""
    if not isinstance(todo, dict):
        return None
    item = dict(todo)
    item_id = str(item.get("id") or "").strip()
    if not item_id:
        item_id = f"todo-{int(time.time() * 1000)}"
    item["id"] = item_id

    has_completed = "completed" in item
    has_done = "done" in item
    if has_completed and has_done:
        c_val = bool(item.get("completed"))
        d_val = bool(item.get("done"))
        if c_val == d_val:
            done_bool = c_val
        elif isinstance(prev_todo, dict):
            prev_done = bool(prev_todo.get("completed") if "completed" in prev_todo else prev_todo.get("done"))
            # Pick whichever field changed relative to prev_todo
            done_bool = c_val if c_val != prev_done else d_val
        else:
            done_bool = c_val or d_val
    elif has_completed:
        done_bool = bool(item.get("completed"))
    elif has_done:
        done_bool = bool(item.get("done"))
    else:
        done_bool = False

    item["completed"] = done_bool
    item["done"] = done_bool
    item["text"] = str(item.get("text") if item.get("text") is not None else "")
    item["dueDate"] = str(item.get("dueDate") or "")
    prio = str(item.get("priority") or "medium").strip().lower()
    item["priority"] = prio if prio in ("high", "medium", "low") else "medium"
    item["category"] = str(item.get("category") or "งานประจำ").strip() or "งานประจำ"
    if "notion_url" in item or (isinstance(prev_todo, dict) and prev_todo.get("notion_url")):
        item["notion_url"] = str(item.get("notion_url") or (prev_todo.get("notion_url") if isinstance(prev_todo, dict) else "") or "")
    return item


def normalize_todos(todos_list, prev_todos=None):
    """Normalize a list of todo dicts, optionally resolving completed/done diffs against prev_todos."""
    if not isinstance(todos_list, list):
        return []
    prev_map = {}
    if isinstance(prev_todos, list):
        for pt in prev_todos:
            if isinstance(pt, dict) and pt.get("id"):
                prev_map[pt["id"]] = pt
    normalized = []
    for t in todos_list:
        if isinstance(t, dict):
            nt = normalize_todo(t, prev_map.get(t.get("id")))
            if nt is not None:
                normalized.append(nt)
    return normalized


def is_port_in_use(port):
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/ping")
        with urllib.request.urlopen(req, timeout=0.8) as r:
            return r.status == 200
    except Exception:
        return False


def load_data():
    target_file = get_data_file()
    with data_lock:
        if os.path.exists(target_file):
            try:
                with open(target_file, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, dict):
                    todos = normalize_todos(raw.get("todos", []))
                    events = raw.get("events", [])
                    if not isinstance(events, list):
                        events = []
                    return {"todos": todos, "events": events}
            except Exception as e:
                print("[Sync Server] Error loading data:", e)
        return {"todos": [], "events": []}


def save_data(data):
    target_file = get_data_file()
    with data_lock:
        tmp_path = None
        try:
            payload = {
                "todos": normalize_todos(data.get("todos", [])) if isinstance(data, dict) else [],
                "events": data.get("events", []) if isinstance(data, dict) and isinstance(data.get("events"), list) else []
            }
            target_dir = os.path.dirname(target_file) or APP_DIR
            os.makedirs(target_dir, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(prefix=".todo_data_", suffix=".tmp", dir=target_dir)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
            except Exception:
                try:
                    os.close(fd)
                except Exception:
                    pass
                raise
            os.replace(tmp_path, target_file)
            return True
        except Exception as e:
            print("[Sync Server] Error saving data:", e)
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
            return False


def update_data(mutator_fn):
    """Atomically load, mutate, and save desktop_todo_data.json under data_lock."""
    with data_lock:
        current = load_data()
        res = mutator_fn(current)
        if isinstance(res, dict) and ("todos" in res or "events" in res):
            current = res
        save_data(current)
        return current


def update_todos_in_cache(new_todos):
    """Atomically update only 'todos' in desktop_todo_data.json without clobbering concurrent 'events'."""
    with data_lock:
        current = load_data()
        normalized = normalize_todos(new_todos, current.get("todos", []))
        current["todos"] = normalized
        save_data(current)
        return normalized


def update_events_in_cache(new_events):
    """Atomically update only 'events' in desktop_todo_data.json without clobbering concurrent 'todos'."""
    with data_lock:
        current = load_data()
        current["events"] = new_events if isinstance(new_events, list) else []
        save_data(current)
        return current["events"]


def _merge_custom_events(live_events, cached_events):
    """Preserve locally created custom events (sourceType == 'custom') alongside live Google Calendar events."""
    merged = list(live_events) if isinstance(live_events, list) else []
    seen_ids = {ev.get("id") for ev in merged if isinstance(ev, dict) and ev.get("id")}
    for cev in (cached_events or []):
        if isinstance(cev, dict) and (cev.get("sourceType") == "custom" or str(cev.get("id", "")).startswith("custom-")):
            cid = cev.get("id")
            if not cid or cid not in seen_ids:
                merged.append(cev)
                if cid:
                    seen_ids.add(cid)

    def sort_key(ev):
        d = ev.get("date", "")
        t = ev.get("time", "")
        return (d, 0 if t == "ทั้งวัน" else 1, t)

    merged.sort(key=sort_key)
    return merged


def sync_google_calendar_now():
    """Fetch live 7-day events from Google Calendar and update local JSON cache, falling back to cache on OAuth error."""
    if not google_calendar_service:
        calendar_sync_state["status"] = "error"
        calendar_sync_state["error"] = "google_calendar_service not loaded"
        return load_data().get("events", [])
    try:
        events = google_calendar_service.fetch_all_7day_events()
        if events is not None:
            with data_lock:
                current_data = load_data()
                merged = _merge_custom_events(events, current_data.get("events", []))
                current_data["events"] = merged
                save_data(current_data)
            ts = now_bkk_iso()
            calendar_sync_state["status"] = "connected"
            calendar_sync_state["error"] = None
            calendar_sync_state["last_sync"] = ts
            return merged
    except Exception as e:
        print("[Google Calendar Sync] Error:", e)
        cached = load_data().get("events", [])
        has_cached = bool(cached)
        if hasattr(google_calendar_service, "mark_cached_fallback"):
            google_calendar_service.mark_cached_fallback(str(e), has_cached=has_cached)
        calendar_sync_state["status"] = "cached_fallback" if has_cached else "error"
        calendar_sync_state["error"] = str(e)
        return cached
    return load_data().get("events", [])


def fetch_calendar_events_for_range(start_date=None, days=7, end_date=None):
    """Fetch events for a specific date range (?start=YYYY-MM-DD&days=N) or default 7-day window."""
    try:
        days_int = int(days) if days is not None else 7
    except Exception:
        days_int = 7
    days_int = max(1, min(60, days_int))

    if start_date and end_date:
        try:
            s_dt = datetime.datetime.strptime(start_date, "%Y-%m-%d").date()
            e_dt = datetime.datetime.strptime(end_date, "%Y-%m-%d").date()
            days_int = max(1, min(60, (e_dt - s_dt).days + 1))
        except Exception:
            pass

    today_str = datetime.datetime.now(BKK_TZ).strftime("%Y-%m-%d")
    if not start_date and not end_date and days_int == 7:
        return sync_google_calendar_now()
    if start_date == today_str and days_int == 7 and not end_date:
        return sync_google_calendar_now()

    # Custom range requested
    eff_start = start_date or today_str
    if google_calendar_service:
        try:
            try:
                live_events = google_calendar_service.fetch_all_7day_events(
                    start_date=eff_start,
                    days=days_int,
                    end_date=end_date
                )
            except TypeError:
                live_events = google_calendar_service.fetch_all_7day_events()
            if live_events is not None:
                calendar_sync_state["status"] = "connected"
                calendar_sync_state["error"] = None
                calendar_sync_state["last_sync"] = now_bkk_iso()
                cached_events = load_data().get("events", [])
                merged = _merge_custom_events(live_events, cached_events)
                return _filter_events_by_range(merged, eff_start, days_int, end_date)
        except Exception as e:
            print("[Google Calendar Range Sync] Error:", e)
            cached_events = load_data().get("events", [])
            has_cached = bool(cached_events)
            if hasattr(google_calendar_service, "mark_cached_fallback"):
                google_calendar_service.mark_cached_fallback(str(e), has_cached=has_cached)
            calendar_sync_state["status"] = "cached_fallback" if has_cached else "error"
            calendar_sync_state["error"] = str(e)
            return _filter_events_by_range(cached_events, eff_start, days_int, end_date)

    cached_events = load_data().get("events", [])
    return _filter_events_by_range(cached_events, eff_start, days_int, end_date)


def _filter_events_by_range(events, start_date_str, days_int, end_date_str=None):
    if google_calendar_service and hasattr(google_calendar_service, "filter_events_by_range"):
        return google_calendar_service.filter_events_by_range(
            events,
            start_date=start_date_str,
            days=days_int,
            end_date=end_date_str
        )
    if not start_date_str:
        return events
    try:
        s_dt = datetime.datetime.strptime(start_date_str, "%Y-%m-%d").date()
        if end_date_str:
            e_dt = datetime.datetime.strptime(end_date_str, "%Y-%m-%d").date()
        else:
            e_dt = s_dt + datetime.timedelta(days=days_int - 1)
        s_iso = s_dt.strftime("%Y-%m-%d")
        e_iso = e_dt.strftime("%Y-%m-%d")
        return [ev for ev in (events or []) if isinstance(ev, dict) and s_iso <= str(ev.get("date", "")) <= e_iso]
    except Exception:
        return events


def background_calendar_sync_worker():
    """Background loop: fetch Google Calendar events every 5 minutes (300 seconds)"""
    time.sleep(2)
    if is_background_disabled():
        return
    sync_google_calendar_now()
    while not is_background_disabled():
        time.sleep(300)
        if is_background_disabled():
            break
        try:
            sync_google_calendar_now()
        except Exception as e:
            print("[Google Calendar Background] Error:", e)


def sync_notion_todos_now():
    """Sync todos with Notion database if connected, serialized via notion_sync_lock."""
    if not notion_service:
        return load_data().get("todos", [])
    with notion_sync_lock:
        try:
            current_data = load_data()
            local_todos = current_data.get("todos", [])
            synced_todos, is_synced = notion_service.sync_todos_with_notion(local_todos)
            if is_synced and synced_todos is not None:
                updated = update_todos_in_cache(synced_todos)
                notion_sync_state["connected"] = True
                notion_sync_state["last_sync"] = (
                    notion_service.get_last_sync()
                    if hasattr(notion_service, "get_last_sync")
                    else now_bkk_iso()
                )
                notion_sync_state["error"] = None
                return updated
        except Exception as e:
            print("[Notion Sync] Error:", e)
            notion_sync_state["error"] = str(e)
        return load_data().get("todos", [])


def background_notion_sync_worker():
    """Background loop: sync Notion tasks every 60 seconds"""
    time.sleep(3)
    if is_background_disabled():
        return
    sync_notion_todos_now()
    while not is_background_disabled():
        time.sleep(60)
        if is_background_disabled():
            break
        try:
            sync_notion_todos_now()
        except Exception as e:
            print("[Notion Background] Error:", e)


def async_push_todo_changes_to_notion(old_todos, new_todos):
    """Detect additions, deletions, and full-field updates and push to Notion asynchronously."""
    if not notion_service:
        return

    def _worker():
        with notion_sync_lock:
            try:
                cfg = notion_service.load_config()
                db_id = cfg.get("database_id")
                if not db_id:
                    db_id = notion_service.get_target_database_id()
                    if not db_id:
                        return

                old_map = {t.get("id"): t for t in (old_todos or []) if isinstance(t, dict) and t.get("id")}
                new_map = {t.get("id"): t for t in (new_todos or []) if isinstance(t, dict) and t.get("id")}

                # 1. Deletions: in old_map but not in new_map
                for old_id in old_map:
                    if old_id not in new_map and is_notion_uuid(old_id):
                        try:
                            notion_service.archive_notion_todo(old_id)
                        except Exception as e:
                            print(f"[Notion Async] Error archiving {old_id}:", e)

                # 2. Additions or Updates
                id_replacements = {}

                for item in (new_todos or []):
                    if not isinstance(item, dict):
                        continue
                    item_id = str(item.get("id") or "")
                    if not item_id:
                        continue

                    if not is_notion_uuid(item_id):
                        # New item created locally -> push to Notion
                        try:
                            created_res = notion_service.create_notion_todo(
                                text=item.get("text", ""),
                                due_date=item.get("dueDate"),
                                priority=item.get("priority", "medium"),
                                category=item.get("category", "งานประจำ"),
                                database_id=db_id
                            )
                            created = dict(created_res) if isinstance(created_res, dict) else {}
                            if item.get("completed") or item.get("done"):
                                notion_service.update_notion_todo(created["id"], completed=True)
                                created["completed"] = True
                                created["done"] = True
                            if created and created.get("id"):
                                id_replacements[item_id] = {
                                    "id": created["id"],
                                    "notion_url": created.get("notion_url", "")
                                }
                        except Exception as e:
                            print("[Notion Async] Error creating new todo in Notion:", e)
                    else:
                        # Existing Notion UUID -> check all mutable fields for changes
                        old_item = old_map.get(item_id)
                        curr_completed = bool(item.get("completed") if "completed" in item else item.get("done"))
                        prev_completed = (
                            bool(old_item.get("completed") if "completed" in old_item else old_item.get("done"))
                            if old_item is not None else None
                        )
                        curr_text = str(item.get("text", "")).strip()
                        prev_text = str(old_item.get("text", "")).strip() if old_item is not None else None

                        curr_due = str(item.get("dueDate") or "")
                        prev_due = str(old_item.get("dueDate") or "") if old_item is not None else None

                        curr_prio = str(item.get("priority") or "medium").lower()
                        prev_prio = str(old_item.get("priority") or "medium").lower() if old_item is not None else None

                        curr_cat = str(item.get("category") or "งานประจำ")
                        prev_cat = str(old_item.get("category") or "งานประจำ") if old_item is not None else None

                        update_kwargs = {}
                        if old_item is None or curr_completed != prev_completed:
                            update_kwargs["completed"] = curr_completed
                        if old_item is not None and curr_text != prev_text:
                            update_kwargs["text"] = curr_text
                        if old_item is not None and curr_due != prev_due:
                            update_kwargs["due_date"] = curr_due or None
                        if old_item is not None and curr_prio != prev_prio:
                            update_kwargs["priority"] = curr_prio
                        if old_item is not None and curr_cat != prev_cat:
                            update_kwargs["category"] = curr_cat

                        if update_kwargs:
                            try:
                                notion_service.update_notion_todo(item_id, **update_kwargs)
                            except Exception as e:
                                print(f"[Notion Async] Error updating {item_id}:", e)

                # 3. Safely apply temporary ID -> Notion UUID replacements under data_lock
                if id_replacements:
                    with data_lock:
                        latest_data = load_data()
                        cache_todos = latest_data.get("todos", [])
                        changed = False
                        for c_item in cache_todos:
                            old_temp_id = c_item.get("id")
                            if old_temp_id in id_replacements:
                                repl = id_replacements[old_temp_id]
                                c_item["id"] = repl["id"]
                                if repl.get("notion_url"):
                                    c_item["notion_url"] = repl["notion_url"]
                                changed = True
                        if changed:
                            latest_data["todos"] = cache_todos
                            save_data(latest_data)

                notion_sync_state["connected"] = True
                notion_sync_state["last_sync"] = (
                    notion_service.get_last_sync()
                    if hasattr(notion_service, "get_last_sync")
                    else now_bkk_iso()
                )
            except Exception as e:
                print("[Notion Async Worker] Error:", e)

    threading.Thread(target=_worker, daemon=True).start()


def _resolve_calendar_status_info(has_cached_events=None):
    """Combine google_calendar_service status and calendar_sync_state consistently."""
    if has_cached_events is None:
        has_cached_events = bool(load_data().get("events", []))

    if google_calendar_service and hasattr(google_calendar_service, "get_calendar_status"):
        probe = (
            calendar_sync_state.get("last_sync") is None
            and calendar_sync_state.get("error") is None
            and getattr(google_calendar_service, "last_status", "unknown") == "unknown"
        )
        info = google_calendar_service.get_calendar_status(has_cached_events=has_cached_events, probe=probe)
        calendar_sync_state["status"] = info.get("status", "cached_fallback")
        if info.get("error") is not None:
            calendar_sync_state["error"] = info.get("error")
        if info.get("last_sync"):
            calendar_sync_state["last_sync"] = info.get("last_sync")
        elif calendar_sync_state.get("last_sync"):
            info["last_sync"] = calendar_sync_state.get("last_sync")
        return info

    # Fallback if google_calendar_service is None
    raw_status = calendar_sync_state.get("status", "cached_fallback" if has_cached_events else "error")
    if raw_status == "connected":
        health_str = "ok"
    elif raw_status == "cached_fallback" or has_cached_events:
        raw_status = "cached_fallback"
        health_str = "cached"
    else:
        raw_status = "error"
        health_str = "error"

    return {
        "status": raw_status,
        "health_status": health_str,
        "connected": raw_status == "connected",
        "error": calendar_sync_state.get("error"),
        "last_sync": calendar_sync_state.get("last_sync"),
        "calendars": []
    }


# Safe static files allowed to be served over HTTP GET
SAFE_HTML_PATHS = {"/", "/index.html", "/desktop_todo_calendar_widget.html"}
FORBIDDEN_PREFIXES = ("/.calendar_auth", "/.env", "/.git", "/.agents", "/.vscode", "/__pycache__")
FORBIDDEN_EXACT = {
    "/notion_config.json",
    "/desktop_todo_data.json",
    "/.env",
    "/.env.example",
    "/.dockerignore",
    "/Dockerfile",
    "/Procfile",
    "/render.yaml",
}
FORBIDDEN_EXTENSIONS = (".py", ".pyc", ".pyo", ".bat", ".sh", ".env", ".key", ".pem", ".log", ".tmp")


class SyncRequestHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Accept, X-API-Key, Authorization")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        super().end_headers()

    def _send_json(self, status_code, obj):
        payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _read_json_body(self):
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length <= 0:
            return None
        raw = self.rfile.read(content_length)
        return json.loads(raw.decode("utf-8"))

    def _check_api_key(self):
        """Verify X-API-Key or Authorization: Bearer <key> if WIDGET_API_KEY is configured."""
        expected_key = get_api_key()
        if not expected_key:
            return True
        header_key = (self.headers.get("X-API-Key") or "").strip()
        auth_header = (self.headers.get("Authorization") or "").strip()
        bearer_key = ""
        if auth_header.lower().startswith("bearer "):
            bearer_key = auth_header[7:].strip()
        if header_key == expected_key or bearer_key == expected_key:
            return True
        self._send_json(401, {"status": "error", "message": "Unauthorized"})
        return False

    def _is_forbidden_path(self, req_path):
        decoded = urllib.parse.unquote(req_path or "").replace("\\", "/")
        if ".." in decoded:
            return True
        low = decoded.lower()
        if low in {p.lower() for p in FORBIDDEN_EXACT}:
            return True
        for prefix in FORBIDDEN_PREFIXES:
            if low == prefix.lower() or low.startswith(prefix.lower() + "/"):
                return True
        # Any hidden file or directory segment starting with '.'
        segments = [seg for seg in decoded.split("/") if seg]
        for seg in segments:
            if seg.startswith("."):
                return True
        if low.endswith(FORBIDDEN_EXTENSIONS):
            return True
        # Block any .json static file except /manifest.json
        if low.endswith(".json") and low != "/manifest.json":
            return True
        return False

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        req_path = parsed.path or "/"
        query_params = urllib.parse.parse_qs(parsed.query)

        # 1. API Routes
        if req_path == "/api/ping":
            self._send_json(200, {"status": "ok"})
            return

        if req_path == "/api/health":
            data = load_data()
            todos_list = data.get("todos", [])
            events_list = data.get("events", [])
            notion_cfg = notion_service.load_config() if notion_service else {}
            notion_conn = bool(
                notion_sync_state.get("connected")
                or (notion_cfg.get("token") and notion_cfg.get("database_id"))
            )
            last_n_sync = (
                notion_sync_state.get("last_sync")
                or (notion_service.get_last_sync() if (notion_service and hasattr(notion_service, "get_last_sync")) else None)
                or notion_cfg.get("last_sync")
            )
            cal_info = _resolve_calendar_status_info(has_cached_events=bool(events_list))
            self._send_json(200, {
                "status": "ok",
                "notion_connected": notion_conn,
                "calendar_status": cal_info.get("health_status", "cached"),
                "last_notion_sync": last_n_sync,
                "last_calendar_sync": cal_info.get("last_sync"),
                "counts": {
                    "todos": len(todos_list),
                    "events": len(events_list)
                }
            })
            return

        if req_path in ("/api/data", "/api/todos"):
            data = load_data()
            self._send_json(200, data)
            return

        if req_path == "/api/calendar/events":
            start_param = query_params.get("start", [None])[0]
            days_param = query_params.get("days", [None])[0]
            end_param = query_params.get("end", [None])[0]
            events = fetch_calendar_events_for_range(start_date=start_param, days=days_param, end_date=end_param)
            self._send_json(200, events)
            return

        if req_path == "/api/calendar/status":
            cached_events = load_data().get("events", [])
            status_info = _resolve_calendar_status_info(has_cached_events=bool(cached_events))
            self._send_json(200, status_info)
            return

        if req_path == "/api/notion/status":
            info = {
                "installed": notion_service is not None,
                "connected": False,
                "bot_name": "ICT",
                "workspace": "pattarapon thongsri's Space",
                "database_id": None,
                "databases_found": [],
                "pages_found": [],
                "last_sync": notion_sync_state.get("last_sync")
            }
            if notion_service:
                try:
                    cfg = notion_service.load_config()
                    conn = notion_service.test_connection()
                    res = notion_service.discover_resources()
                    is_conn = conn.get("status") == "connected"
                    notion_sync_state["connected"] = is_conn
                    last_sync_val = (
                        notion_sync_state.get("last_sync")
                        or (notion_service.get_last_sync() if hasattr(notion_service, "get_last_sync") else None)
                        or cfg.get("last_sync")
                    )
                    info.update({
                        "connected": is_conn,
                        "bot_name": conn.get("bot_name", "ICT"),
                        "workspace": conn.get("workspace", ""),
                        "database_id": cfg.get("database_id"),
                        "databases_found": res.get("databases", []),
                        "pages_found": res.get("pages", []),
                        "last_sync": last_sync_val
                    })
                except Exception as e:
                    info["error"] = str(e)
            self._send_json(200, info)
            return

        # 2. Security Check: Block sensitive files with 403 Forbidden
        if self._is_forbidden_path(req_path):
            self._send_json(403, {"status": "error", "message": "Forbidden"})
            return

        # 3. Strict Static File Allowlist
        if req_path in SAFE_HTML_PATHS:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            if os.path.exists(HTML_FILE):
                with open(HTML_FILE, "rb") as f:
                    body = f.read()
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                body = b"HTML Widget not found"
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            return

        if req_path == "/manifest.json":
            if os.path.exists(MANIFEST_FILE):
                with open(MANIFEST_FILE, "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "application/manifest+json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self._send_json(404, {"status": "error", "message": "manifest.json not found"})
            return

        if req_path == "/sw.js":
            if os.path.exists(SW_FILE):
                with open(SW_FILE, "rb") as f:
                    body = f.read()
            else:
                body = b"self.addEventListener('install', () => self.skipWaiting());\nself.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));\n"
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if req_path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return

        self._send_json(404, {"status": "error", "message": "Not Found"})

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        req_path = parsed.path or "/"

        if req_path.startswith("/api/") and not self._check_api_key():
            return

        if req_path == "/api/todos":
            try:
                body_obj = self._read_json_body()
                if isinstance(body_obj, dict) and isinstance(body_obj.get("todos"), list):
                    raw_todos = body_obj["todos"]
                elif isinstance(body_obj, list):
                    raw_todos = body_obj
                else:
                    raise ValueError("Expected JSON array of todos or {'todos': [...]}")

                with data_lock:
                    current_data = load_data()
                    old_todos = current_data.get("todos", [])
                    normalized_todos = normalize_todos(raw_todos, old_todos)
                    current_data["todos"] = normalized_todos
                    save_data(current_data)

                async_push_todo_changes_to_notion(old_todos, normalized_todos)
                self._send_json(200, {
                    "status": "success",
                    "count": len(normalized_todos),
                    "todos": normalized_todos
                })
            except Exception as e:
                self._send_json(400, {"status": "error", "error": str(e)})
            return

        if req_path == "/api/notion/sync":
            try:
                synced_todos = sync_notion_todos_now()
                self._send_json(200, {"status": "success", "todos": synced_todos})
            except Exception as e:
                self._send_json(500, {"status": "error", "error": str(e)})
            return

        if req_path == "/api/notion/connect":
            try:
                req_data = self._read_json_body() or {}
                cfg = notion_service.load_config() if notion_service else {}

                db_id = req_data.get("database_id")
                page_id = req_data.get("page_id")

                if db_id:
                    cfg["database_id"] = db_id
                    if notion_service:
                        notion_service.save_config(cfg)
                elif page_id and notion_service:
                    created_db = notion_service.create_tasks_database(page_id)
                    cfg["database_id"] = created_db.get("id")
                    notion_service.save_config(cfg)
                elif notion_service:
                    detected_id = notion_service.get_target_database_id()
                    if not detected_id:
                        raise ValueError("No Notion database or page found yet. Please connect the 'ICT' integration to a Notion page or database first.")
                    cfg["database_id"] = detected_id

                synced_todos = sync_notion_todos_now()
                self._send_json(200, {
                    "status": "connected",
                    "database_id": cfg.get("database_id"),
                    "todos": synced_todos
                })
            except Exception as e:
                self._send_json(400, {"status": "error", "error": str(e)})
            return

        if req_path == "/api/calendar/events":
            try:
                req_data = self._read_json_body() or {}
                if not isinstance(req_data, dict):
                    raise ValueError("Expected JSON object")
                title = str(req_data.get("title") or "").strip()
                date_str = str(req_data.get("date") or "").strip()
                time_str = str(req_data.get("time") or "").strip()
                calendar_id = str(req_data.get("calendar") or "pattarapon.thongsri@gmail.com").strip()
                description = str(req_data.get("description") or "")

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
                    self._send_json(200, {"status": "success", "event": created_ev})
                else:
                    raise RuntimeError("google_calendar_service module not loaded")
            except Exception as e:
                self._send_json(400, {"status": "error", "error": str(e)})
            return

        if req_path == "/api/events":
            try:
                body_obj = self._read_json_body()
                if isinstance(body_obj, dict) and isinstance(body_obj.get("events"), list):
                    events_data = body_obj["events"]
                elif isinstance(body_obj, list):
                    events_data = body_obj
                else:
                    raise ValueError("Expected JSON array of events")
                updated_events = update_events_in_cache(events_data)
                self._send_json(200, {"status": "success", "count": len(updated_events), "events": updated_events})
            except Exception as e:
                self._send_json(400, {"status": "error", "error": str(e)})
            return

        self._send_json(404, {"status": "error", "message": "Not Found"})

    def _handle_todo_item_update(self):
        parsed = urllib.parse.urlparse(self.path)
        req_path = parsed.path or ""

        if not self._check_api_key():
            return

        if not req_path.startswith("/api/todos/"):
            self._send_json(404, {"status": "error", "message": "Not Found"})
            return

        todo_id = urllib.parse.unquote(req_path[len("/api/todos/"):]).strip("/")
        if not todo_id:
            self._send_json(400, {"status": "error", "message": "Missing todo ID"})
            return

        try:
            patch_data = self._read_json_body()
            if not isinstance(patch_data, dict):
                raise ValueError("Expected JSON object payload")
        except Exception as e:
            self._send_json(400, {"status": "error", "error": str(e)})
            return

        with data_lock:
            current_data = load_data()
            todos = current_data.get("todos", [])
            old_todos = [dict(t) for t in todos]
            target_todo = None

            for t in todos:
                if t.get("id") == todo_id:
                    target_todo = t
                    break

            if target_todo is None:
                self._send_json(404, {"status": "error", "message": f"Todo '{todo_id}' not found"})
                return

            prev_snapshot = dict(target_todo)
            if "text" in patch_data and patch_data["text"] is not None:
                target_todo["text"] = str(patch_data["text"]).strip()
            if "dueDate" in patch_data:
                target_todo["dueDate"] = str(patch_data["dueDate"] or "")
            if "priority" in patch_data and patch_data["priority"] is not None:
                prio = str(patch_data["priority"]).strip().lower()
                target_todo["priority"] = prio if prio in ("high", "medium", "low") else "medium"
            if "category" in patch_data and patch_data["category"] is not None:
                target_todo["category"] = str(patch_data["category"]).strip() or "งานประจำ"
            if "notion_url" in patch_data and patch_data["notion_url"] is not None:
                target_todo["notion_url"] = str(patch_data["notion_url"])

            if "completed" in patch_data or "done" in patch_data:
                if "completed" in patch_data and "done" not in patch_data:
                    done_val = bool(patch_data["completed"])
                elif "done" in patch_data and "completed" not in patch_data:
                    done_val = bool(patch_data["done"])
                else:
                    c_val = bool(patch_data["completed"])
                    d_val = bool(patch_data["done"])
                    if c_val == d_val:
                        done_val = c_val
                    else:
                        prev_done = bool(prev_snapshot.get("completed"))
                        done_val = c_val if c_val != prev_done else d_val
                target_todo["completed"] = done_val
                target_todo["done"] = done_val

            current_data["todos"] = todos
            save_data(current_data)
            updated_todos = current_data["todos"]

        async_push_todo_changes_to_notion(old_todos, updated_todos)
        self._send_json(200, {
            "status": "success",
            "todo": target_todo,
            "todos": updated_todos
        })

    def do_PATCH(self):
        self._handle_todo_item_update()

    def do_PUT(self):
        self._handle_todo_item_update()

    def do_DELETE(self):
        parsed = urllib.parse.urlparse(self.path)
        req_path = parsed.path or ""

        if not self._check_api_key():
            return

        if not req_path.startswith("/api/todos/"):
            self._send_json(404, {"status": "error", "message": "Not Found"})
            return

        todo_id = urllib.parse.unquote(req_path[len("/api/todos/"):]).strip("/")
        if not todo_id:
            self._send_json(400, {"status": "error", "message": "Missing todo ID"})
            return

        with data_lock:
            current_data = load_data()
            todos = current_data.get("todos", [])
            old_todos = [dict(t) for t in todos]
            new_todos = [t for t in todos if t.get("id") != todo_id]

            if len(new_todos) == len(todos):
                self._send_json(404, {"status": "error", "message": f"Todo '{todo_id}' not found"})
                return

            current_data["todos"] = new_todos
            save_data(current_data)

        async_push_todo_changes_to_notion(old_todos, new_todos)
        self._send_json(200, {
            "status": "success",
            "deleted_id": todo_id,
            "todos": new_todos
        })

    def log_message(self, format, *args):
        pass


class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def start_server(port=None, host=None):
    bind_port = int(port or os.environ.get("PORT", PORT))
    bind_host = host or os.environ.get("HOST", "0.0.0.0" if "PORT" in os.environ else HOST)

    if is_port_in_use(bind_port):
        print(f"[Sync Server] Port {bind_port} is already running.")
        return None

    os.chdir(APP_DIR)
    server = None
    for attempt in range(4):
        try:
            server = ThreadedHTTPServer((bind_host, bind_port), SyncRequestHandler)
            break
        except OSError as e:
            if attempt < 3:
                time.sleep(1.2)
            else:
                print(f"[Sync Server] Could not bind to {bind_host}:{bind_port} -> {e}")
                return None

    if not is_background_disabled():
        t_cal = threading.Thread(target=background_calendar_sync_worker, daemon=True)
        t_cal.start()

        t_notion = threading.Thread(target=background_notion_sync_worker, daemon=True)
        t_notion.start()

    print(f"[Sync Server] Running on http://{bind_host}:{bind_port}")
    try:
        server.serve_forever()
    except Exception as e:
        print("[Sync Server] Stopped:", e)
    finally:
        server.server_close()


def start_in_thread(port=None, host=None):
    bind_port = int(port or os.environ.get("PORT", PORT))
    if is_port_in_use(bind_port):
        return None
    t = threading.Thread(target=start_server, kwargs={"port": bind_port, "host": host}, daemon=True)
    t.start()
    return t


if __name__ == "__main__":
    start_server()
