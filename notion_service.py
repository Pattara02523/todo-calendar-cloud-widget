# -*- coding: utf-8 -*-
"""
Notion Service for Desktop Todo Widget
Handles bidirectional synchronization between the desktop widget and Notion Database.
"""

import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

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

NOTION_VERSION = "2022-06-28"
NOTION_API_BASE = "https://api.notion.com/v1"

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG_FILE = os.path.join(APP_DIR, "notion_config.json")
CONFIG_FILE = DEFAULT_CONFIG_FILE

# Explicit Bangkok timezone (UTC+07:00)
BKK_TZ = timezone(timedelta(hours=7))

# Default token from user's MCP config ("เอาspokenต่างๆไว้เลย")
DEFAULT_TOKEN = "ntn_T61682170373nkuXl8zMEWyv5D97jSCpVvEbMupijTLgXY"

# In-memory runtime config overrides & last_sync keyed by abs config file path
# Prevents mutating the protected notion_config.json on disk while isolating temp test configs.
_runtime_overrides_by_file = {}
LAST_SYNC_TIME = None


def load_dotenv(dotenv_path=None):
    """Zero-dependency .env loader that populates os.environ without overriding existing env vars."""
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
                if len(val) >= 2 and ((val[0] == val[-1] == '"') or (val[0] == val[-1] == "'")):
                    val = val[1:-1]
                if key:
                    os.environ.setdefault(key, val)
    except Exception as e:
        print("[Notion] Warning reading .env:", e)


load_dotenv()


def today_bkk_str():
    """Return current date in Bangkok timezone (YYYY-MM-DD)."""
    return datetime.now(BKK_TZ).strftime("%Y-%m-%d")


def now_bkk_iso():
    """Return current ISO-8601 timestamp in Bangkok timezone."""
    return datetime.now(BKK_TZ).isoformat(timespec="seconds")


def get_config_file():
    """Return active Notion config file path, reading NOTION_CONFIG_FILE from os.environ at call time."""
    env_file = os.environ.get("NOTION_CONFIG_FILE", "").strip()
    if env_file:
        return os.path.abspath(env_file)
    return DEFAULT_CONFIG_FILE


def _is_protected_default_config(cfg_file):
    return os.path.abspath(cfg_file) == os.path.abspath(DEFAULT_CONFIG_FILE)


def _atomic_write_json(target_path, data):
    target_dir = os.path.dirname(os.path.abspath(target_path))
    os.makedirs(target_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".notion_cfg_", suffix=".tmp", dir=target_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, target_path)
    except Exception:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        raise


def record_last_sync(ts=None):
    """Record last successful Notion sync ISO timestamp without corrupting protected notion_config.json."""
    global LAST_SYNC_TIME
    sync_ts = ts or now_bkk_iso()
    LAST_SYNC_TIME = sync_ts

    cfg_file = get_config_file()
    abs_cfg = os.path.abspath(cfg_file)
    overrides = _runtime_overrides_by_file.setdefault(abs_cfg, {})
    overrides["last_sync"] = sync_ts

    # If using a non-protected custom/temp config file that exists on disk, persist last_sync to it
    if not _is_protected_default_config(cfg_file) and os.path.exists(cfg_file):
        try:
            with open(cfg_file, "r", encoding="utf-8") as f:
                saved = json.load(f)
            if isinstance(saved, dict):
                saved["last_sync"] = sync_ts
                _atomic_write_json(cfg_file, saved)
        except Exception:
            pass

    return sync_ts


def get_last_sync():
    """Return the most recent Notion sync ISO timestamp."""
    cfg = load_config()
    return cfg.get("last_sync") or LAST_SYNC_TIME


def is_notion_uuid(val):
    """Return True if val looks like a Notion page UUID rather than a local 'todo-...' temporary ID."""
    if not val or not isinstance(val, str):
        return False
    if val.startswith("todo-"):
        return False
    cleaned = val.replace("-", "")
    if len(cleaned) == 32 and all(c in "0123456789abcdefABCDEF" for c in cleaned):
        return True
    return len(val) >= 32 and "-" in val


def normalize_todo(todo):
    """Ensure completed == done and all standard fields exist on a todo dict."""
    if not isinstance(todo, dict):
        return todo
    if "completed" in todo and "done" in todo:
        if bool(todo["completed"]) == bool(todo["done"]):
            done_bool = bool(todo["completed"])
        else:
            done_bool = bool(todo.get("completed") or todo.get("done"))
    elif "completed" in todo:
        done_bool = bool(todo.get("completed"))
    elif "done" in todo:
        done_bool = bool(todo.get("done"))
    else:
        done_bool = False

    normalized = dict(todo)
    normalized["id"] = str(todo.get("id") or "")
    normalized["text"] = str(todo.get("text") or "").strip()
    normalized["dueDate"] = str(todo.get("dueDate") or today_bkk_str())
    prio = str(todo.get("priority") or "medium").lower()
    normalized["priority"] = prio if prio in ("high", "medium", "low") else "medium"
    normalized["category"] = str(todo.get("category") or "งานประจำ")
    normalized["completed"] = done_bool
    normalized["done"] = done_bool
    normalized["notion_url"] = str(todo.get("notion_url") or "")
    return normalized


def load_config():
    load_dotenv()
    cfg_file = get_config_file()
    abs_cfg = os.path.abspath(cfg_file)

    cfg = {
        "token": DEFAULT_TOKEN,
        "database_id": None,
        "page_id": None,
        "last_sync": None
    }
    if os.path.exists(cfg_file):
        try:
            with open(cfg_file, "r", encoding="utf-8") as f:
                saved = json.load(f)
                if isinstance(saved, dict):
                    cfg.update(saved)
        except Exception as e:
            print("[Notion] Error reading config file:", e)

    # Apply any in-memory runtime updates for this specific config file path
    if abs_cfg in _runtime_overrides_by_file:
        for k, v in _runtime_overrides_by_file[abs_cfg].items():
            if v is not None:
                cfg[k] = v

    # Ensure token fallback if empty in file
    if not cfg.get("token"):
        cfg["token"] = DEFAULT_TOKEN

    # Support Cloud / Test Environment Variables (highest priority)
    env_token = (os.environ.get("NOTION_API_TOKEN") or os.environ.get("NOTION_TOKEN") or "").strip()
    if env_token:
        cfg["token"] = env_token
    env_db = os.environ.get("NOTION_DATABASE_ID", "").strip()
    if env_db:
        cfg["database_id"] = env_db

    return cfg


def save_config(cfg):
    if not isinstance(cfg, dict):
        return
    cfg_file = get_config_file()
    abs_cfg = os.path.abspath(cfg_file)

    # Always record in memory for this config file path
    overrides = _runtime_overrides_by_file.setdefault(abs_cfg, {})
    overrides.update(cfg)
    if cfg.get("last_sync"):
        global LAST_SYNC_TIME
        LAST_SYNC_TIME = cfg["last_sync"]

    # Protect the real notion_config.json on disk from modification
    if _is_protected_default_config(cfg_file) and os.path.exists(cfg_file):
        return

    try:
        _atomic_write_json(cfg_file, cfg)
    except Exception as e:
        print("[Notion] Error saving config file:", e)


def _notion_request(endpoint, method="GET", data=None, token=None):
    if not token:
        cfg = load_config()
        token = cfg.get("token") or DEFAULT_TOKEN

    url = f"{NOTION_API_BASE}/{endpoint.lstrip('/')}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json"
    }

    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")

    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp_bytes = resp.read()
            if resp_bytes:
                return json.loads(resp_bytes.decode("utf-8"))
            return {}
    except urllib.error.HTTPError as e:
        err_content = e.read().decode("utf-8") if e.fp else ""
        print(f"[Notion API Error] {method} {url} -> {e.code} {e.reason}: {err_content}")
        raise RuntimeError(f"Notion API error ({e.code}): {err_content}")
    except Exception as e:
        print(f"[Notion Network Error] {method} {url}: {e}")
        raise


def test_connection():
    """Verify bot token and return workspace info"""
    try:
        data = _notion_request("users/me")
        return {
            "status": "connected",
            "bot_name": data.get("name"),
            "workspace": data.get("bot", {}).get("workspace_name"),
            "user_id": data.get("id")
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


def discover_resources():
    """Search for all databases or pages accessible to the bot"""
    try:
        res = _notion_request("search", method="POST", data={})
        results = res.get("results", [])
        databases = []
        pages = []

        for item in results:
            obj_type = item.get("object")
            obj_id = item.get("id")
            if obj_type == "database":
                title_list = item.get("title", [])
                title = "".join([t.get("plain_text", "") for t in title_list]) or "Untitled Database"
                databases.append({
                    "id": obj_id,
                    "title": title,
                    "url": item.get("url"),
                    "properties": list(item.get("properties", {}).keys())
                })
            elif obj_type == "page":
                props = item.get("properties", {})
                title = "Untitled Page"
                for p_key, p_val in props.items():
                    if p_val.get("type") == "title":
                        t_list = p_val.get("title", [])
                        title = "".join([t.get("plain_text", "") for t in t_list]) or "Untitled Page"
                        break
                pages.append({
                    "id": obj_id,
                    "title": title,
                    "url": item.get("url")
                })

        return {"databases": databases, "pages": pages}
    except Exception as e:
        print("[Notion] Error discovering resources:", e)
        return {"databases": [], "pages": [], "error": str(e)}


def create_tasks_database(parent_page_id):
    """Create a standardized Tasks database inside a Notion parent page"""
    payload = {
        "parent": {"type": "page_id", "page_id": parent_page_id},
        "title": [
            {
                "type": "text",
                "text": {"content": "Desktop Widget To-Do List"}
            }
        ],
        "properties": {
            "Task": {"title": {}},
            "Done": {"checkbox": {}},
            "Due Date": {"date": {}},
            "Priority": {
                "select": {
                    "options": [
                        {"name": "high", "color": "red"},
                        {"name": "medium", "color": "yellow"},
                        {"name": "low", "color": "blue"}
                    ]
                }
            },
            "Category": {
                "select": {
                    "options": [
                        {"name": "งานประจำ", "color": "default"},
                        {"name": "ประชุม", "color": "purple"},
                        {"name": "ส่วนตัว", "color": "green"},
                        {"name": "ด่วน", "color": "orange"}
                    ]
                }
            }
        }
    }
    res = _notion_request("databases", method="POST", data=payload)
    db_id = res.get("id")
    if db_id:
        cfg = load_config()
        cfg["database_id"] = db_id
        save_config(cfg)
    return res


def get_target_database_id():
    """Retrieve saved database_id or automatically detect from discovered resources"""
    cfg = load_config()
    if cfg.get("database_id"):
        return cfg["database_id"]

    resources = discover_resources()
    databases = resources.get("databases", [])

    # 1. Prioritize database with 'todo' or 'task' or Thai keywords in title
    for db in databases:
        t_lower = db.get("title", "").lower()
        if "todo" in t_lower or "task" in t_lower or "ต้องทำ" in t_lower or "รายการ" in t_lower:
            cfg["database_id"] = db["id"]
            save_config(cfg)
            return db["id"]

    # 2. Fallback to first database
    if databases:
        db_id = databases[0]["id"]
        cfg["database_id"] = db_id
        save_config(cfg)
        return db_id

    # If only page is available, create database inside it
    if resources.get("pages"):
        page_id = resources["pages"][0]["id"]
        db = create_tasks_database(page_id)
        return db.get("id")

    return None


def fetch_notion_todos(database_id=None):
    """Query Notion database with cursor pagination and convert pages to Desktop Todo format"""
    db_id = database_id or get_target_database_id()
    if not db_id:
        return None

    results = []
    next_cursor = None
    while True:
        query_payload = {"page_size": 100}
        if next_cursor:
            query_payload["start_cursor"] = next_cursor
        try:
            res = _notion_request(f"databases/{db_id}/query", method="POST", data=query_payload)
        except Exception as e:
            print("[Notion] Error querying database:", e)
            return None

        if not isinstance(res, dict):
            break
        page_results = res.get("results", [])
        if isinstance(page_results, list):
            results.extend(page_results)

        if res.get("has_more") and res.get("next_cursor"):
            next_cursor = res.get("next_cursor")
        else:
            break

    todos = []
    default_today = today_bkk_str()

    for page in results:
        if page.get("archived", False) or page.get("in_trash", False):
            continue

        page_id = page.get("id")
        props = page.get("properties", {})

        task_text = ""
        done_bool = False
        due_date = ""
        priority = "medium"
        category = "งานประจำ"

        for p_name, p_data in props.items():
            if not isinstance(p_data, dict):
                continue
            p_type = p_data.get("type")
            name_lower = p_name.lower()

            if p_type == "title":
                t_list = p_data.get("title", [])
                task_text = "".join([
                    t.get("plain_text") if "plain_text" in t else t.get("text", {}).get("content", "")
                    for t in t_list
                ]).strip()

            elif p_type == "checkbox":
                if "done" in name_lower or "complete" in name_lower or "status" in name_lower or "เสร็จ" in name_lower or p_name == "Done":
                    done_bool = bool(p_data.get("checkbox", False))

            elif p_type == "status":
                status_obj = p_data.get("status")
                if status_obj and status_obj.get("name") in ["Done", "Completed", "เสร็จแล้ว"]:
                    done_bool = True

            elif p_type == "date":
                date_obj = p_data.get("date")
                if date_obj and date_obj.get("start"):
                    due_date = str(date_obj.get("start"))[:10]

            elif p_type == "select":
                sel_obj = p_data.get("select")
                val = sel_obj.get("name", "") if sel_obj else ""
                if "prior" in name_lower or "ความสำคัญ" in name_lower or p_name == "Priority":
                    priority = val.lower() if val else "medium"
                elif "cat" in name_lower or "หมวด" in name_lower or p_name == "Category":
                    category = val if val else "งานประจำ"

        if not task_text:
            continue

        todos.append(normalize_todo({
            "id": page_id,
            "text": task_text,
            "dueDate": due_date or default_today,
            "priority": priority,
            "category": category,
            "completed": done_bool,
            "done": done_bool,
            "notion_url": page.get("url") or ""
        }))

    record_last_sync()
    return todos


def create_notion_todo(text, due_date=None, priority="medium", category="งานประจำ", database_id=None, completed=False):
    """Create a new page in the Notion database"""
    db_id = database_id or get_target_database_id()
    if not db_id:
        raise ValueError("No Notion database connected yet.")

    try:
        db_meta = _notion_request(f"databases/{db_id}")
        prop_defs = db_meta.get("properties", {}) if isinstance(db_meta, dict) else {}
    except Exception:
        prop_defs = {}

    title_prop_name = "Task"
    done_prop_name = None
    status_prop_name = None
    date_prop_name = "Due Date"
    priority_prop_name = "Priority"
    cat_prop_name = "Category"

    for p_name, p_def in prop_defs.items():
        if not isinstance(p_def, dict):
            continue
        p_type = p_def.get("type")
        name_lower = p_name.lower()
        if p_type == "title":
            title_prop_name = p_name
        elif p_type == "checkbox" and ("done" in name_lower or "complete" in name_lower or "เสร็จ" in name_lower or p_name == "Done"):
            done_prop_name = p_name
        elif p_type == "status" and ("status" in name_lower or "สถานะ" in name_lower or p_name == "Status"):
            status_prop_name = p_name
        elif p_type == "date" and ("date" in name_lower or "due" in name_lower or "กำหนด" in name_lower):
            date_prop_name = p_name
        elif p_type == "select" and ("prior" in name_lower or "ความสำคัญ" in name_lower):
            priority_prop_name = p_name
        elif p_type == "select" and ("cat" in name_lower or "หมวด" in name_lower):
            cat_prop_name = p_name

    done_flag = bool(completed)
    properties = {
        title_prop_name: {
            "title": [{"type": "text", "text": {"content": str(text)}}]
        }
    }

    if not prop_defs:
        # Fallback when database schema endpoint is mocked/unavailable
        properties["Done"] = {"checkbox": done_flag}
        if due_date:
            properties["Due Date"] = {"date": {"start": str(due_date)[:10]}}
        if priority:
            properties["Priority"] = {"select": {"name": str(priority).lower()}}
        if category:
            properties["Category"] = {"select": {"name": str(category)}}
    else:
        if done_prop_name and prop_defs.get(done_prop_name, {}).get("type") == "checkbox":
            properties[done_prop_name] = {"checkbox": done_flag}
        elif status_prop_name and prop_defs.get(status_prop_name, {}).get("type") == "status":
            properties[status_prop_name] = {"status": {"name": "Done" if done_flag else "Not started"}}

        if date_prop_name in prop_defs and prop_defs[date_prop_name].get("type") == "date" and due_date:
            properties[date_prop_name] = {"date": {"start": str(due_date)[:10]}}

        if priority_prop_name in prop_defs and prop_defs[priority_prop_name].get("type") == "select" and priority:
            properties[priority_prop_name] = {"select": {"name": str(priority).lower()}}

        if cat_prop_name in prop_defs and prop_defs[cat_prop_name].get("type") == "select" and category:
            properties[cat_prop_name] = {"select": {"name": str(category)}}

    payload = {
        "parent": {"database_id": db_id},
        "properties": properties
    }

    res = _notion_request("pages", method="POST", data=payload)
    record_last_sync()
    return normalize_todo({
        "id": res.get("id"),
        "text": text,
        "dueDate": (str(due_date)[:10] if due_date else today_bkk_str()),
        "priority": priority or "medium",
        "category": category or "งานประจำ",
        "completed": done_flag,
        "done": done_flag,
        "notion_url": res.get("url") or ""
    })


def update_notion_todo(page_id, completed=None, text=None, due_date=None, priority=None, category=None, dueDate=None, done=None):
    """Update page properties in Notion (supports completed/done, text, due_date/dueDate, priority, category)"""
    if completed is None and done is not None:
        completed = bool(done)
    if due_date is None and dueDate is not None:
        due_date = dueDate

    page_data = _notion_request(f"pages/{page_id}")
    props = page_data.get("properties", {}) if isinstance(page_data, dict) else {}

    update_props = {}
    if props:
        for p_name, p_val in props.items():
            if not isinstance(p_val, dict):
                continue
            p_type = p_val.get("type")
            name_lower = p_name.lower()

            if completed is not None and p_type == "checkbox" and ("done" in name_lower or "complete" in name_lower or "status" in name_lower or "เสร็จ" in name_lower or p_name == "Done"):
                update_props[p_name] = {"checkbox": bool(completed)}

            if completed is not None and p_type == "status" and ("status" in name_lower or "สถานะ" in name_lower or p_name == "Status"):
                update_props[p_name] = {"status": {"name": "Done" if completed else "Not started"}}

            if text is not None and p_type == "title":
                update_props[p_name] = {"title": [{"type": "text", "text": {"content": str(text)}}]}

            if due_date is not None and p_type == "date":
                update_props[p_name] = {"date": {"start": str(due_date)[:10]} if due_date else None}

            if priority is not None and p_type == "select" and ("prior" in name_lower or "ความสำคัญ" in name_lower or p_name == "Priority"):
                update_props[p_name] = {"select": {"name": str(priority).lower()}}

            if category is not None and p_type == "select" and ("cat" in name_lower or "หมวด" in name_lower or p_name == "Category"):
                update_props[p_name] = {"select": {"name": str(category)}}
    else:
        # Fallback if page_data properties were not returned by mock
        if completed is not None:
            update_props["Done"] = {"checkbox": bool(completed)}
        if text is not None:
            update_props["Task"] = {"title": [{"type": "text", "text": {"content": str(text)}}]}
        if due_date is not None:
            update_props["Due Date"] = {"date": {"start": str(due_date)[:10]} if due_date else None}
        if priority is not None:
            update_props["Priority"] = {"select": {"name": str(priority).lower()}}
        if category is not None:
            update_props["Category"] = {"select": {"name": str(category)}}

    payload = {"properties": update_props}
    res = _notion_request(f"pages/{page_id}", method="PATCH", data=payload)
    record_last_sync()
    return res


def archive_notion_todo(page_id):
    """Archive a page in Notion (equivalent to delete)"""
    payload = {"archived": True}
    res = _notion_request(f"pages/{page_id}", method="PATCH", data=payload)
    record_last_sync()
    return res


def sync_todos_with_notion(local_todos):
    """
    Two-way synchronization algorithm:
    1. Fetch current items from Notion (with cursor pagination)
    2. For existing Notion UUIDs present in Notion: remote Notion fields (completed/done,
       text, dueDate, priority, category, notion_url) are authoritative during background/full sync.
    3. For existing Notion UUIDs missing from Notion: item was archived/deleted in Notion -> drop locally.
    4. For brand-new local items (non-UUID temporary IDs like 'todo-...'): create in Notion.
    5. Include any remaining remote Notion items not yet in local_todos.
    6. Record last_sync timestamp and return (synced_list, True).
    """
    db_id = get_target_database_id()
    if not db_id:
        return [normalize_todo(t) for t in (local_todos or [])], False

    notion_todos = fetch_notion_todos(db_id)
    if notion_todos is None:
        return [normalize_todo(t) for t in (local_todos or [])], False

    notion_map = {t["id"]: normalize_todo(t) for t in notion_todos if isinstance(t, dict) and t.get("id")}
    synced_list = []

    for loc in (local_todos or []):
        if not isinstance(loc, dict):
            continue
        loc_norm = normalize_todo(loc)
        loc_id = loc_norm.get("id", "")

        if is_notion_uuid(loc_id):
            if loc_id in notion_map:
                # Remote Notion fields are authoritative during periodic/full sync
                remote = notion_map.pop(loc_id)
                if not remote.get("notion_url") and loc_norm.get("notion_url"):
                    remote["notion_url"] = loc_norm["notion_url"]
                synced_list.append(normalize_todo(remote))
            else:
                # Item was archived/deleted in Notion -> remove from local list cleanly
                pass
        else:
            # Brand-new local item not in Notion yet -> create in Notion
            try:
                new_item = create_notion_todo(
                    text=loc_norm.get("text", ""),
                    due_date=loc_norm.get("dueDate"),
                    priority=loc_norm.get("priority", "medium"),
                    category=loc_norm.get("category", "งานประจำ"),
                    database_id=db_id
                )
                if loc_norm.get("completed") or loc_norm.get("done"):
                    update_notion_todo(new_item["id"], completed=True)
                    new_item["completed"] = True
                    new_item["done"] = True
                synced_list.append(normalize_todo(new_item))
            except Exception as e:
                print(f"[Notion Sync] Error uploading {loc_norm.get('text')}:", e)
                synced_list.append(loc_norm)

    # Any remaining Notion items that local didn't have -> include them
    for rem in notion_map.values():
        synced_list.append(normalize_todo(rem))

    record_last_sync()
    return synced_list, True
