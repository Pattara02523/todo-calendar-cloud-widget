# -*- coding: utf-8 -*-
"""
Notion Service for Desktop Todo Widget
Handles bidirectional synchronization between the desktop widget and Notion Database.
"""

import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime

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
CONFIG_FILE = os.path.join(APP_DIR, "notion_config.json")

# Default token from user's MCP config
DEFAULT_TOKEN = "ntn_T61682170373nkuXl8zMEWyv5D97jSCpVvEbMupijTLgXY"


def load_config():
    cfg = {
        "token": DEFAULT_TOKEN,
        "database_id": None,
        "page_id": None,
        "last_sync": None
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                cfg.update(saved)
        except Exception as e:
            print("[Notion] Error reading notion_config.json:", e)

    # Support Cloud Environment Variables
    env_token = os.environ.get("NOTION_API_TOKEN") or os.environ.get("NOTION_TOKEN")
    if env_token:
        cfg["token"] = env_token
    env_db = os.environ.get("NOTION_DATABASE_ID")
    if env_db:
        cfg["database_id"] = env_db

    return cfg


def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("[Notion] Error saving notion_config.json:", e)


def _notion_request(endpoint, method="GET", data=None, token=None):
    if not token:
        cfg = load_config()
        token = cfg.get("token", DEFAULT_TOKEN)

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
                # Get title property
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
    """Query Notion database and convert pages to Desktop Todo format"""
    db_id = database_id or get_target_database_id()
    if not db_id:
        return None

    try:
        res = _notion_request(f"databases/{db_id}/query", method="POST", data={"page_size": 100})
    except Exception as e:
        print("[Notion] Error querying database:", e)
        return None

    results = res.get("results", [])
    todos = []

    for page in results:
        if page.get("archived", False):
            continue

        page_id = page.get("id")
        props = page.get("properties", {})

        # Extract Task / Name
        task_text = ""
        done_bool = False
        due_date = ""
        priority = "medium"
        category = "งานประจำ"

        for p_name, p_data in props.items():
            p_type = p_data.get("type")
            name_lower = p_name.lower()

            if p_type == "title":
                t_list = p_data.get("title", [])
                task_text = "".join([t.get("plain_text", "") for t in t_list]).strip()

            elif p_type == "checkbox":
                if "done" in name_lower or "complete" in name_lower or "status" in name_lower or p_name == "Done":
                    done_bool = p_data.get("checkbox", False)

            elif p_type == "status":
                status_obj = p_data.get("status")
                if status_obj and status_obj.get("name") in ["Done", "Completed", "เสร็จแล้ว"]:
                    done_bool = True

            elif p_type == "date":
                date_obj = p_data.get("date")
                if date_obj and date_obj.get("start"):
                    due_date = date_obj.get("start")

            elif p_type == "select":
                sel_obj = p_data.get("select")
                val = sel_obj.get("name", "") if sel_obj else ""
                if "prior" in name_lower or p_name == "Priority":
                    priority = val.lower() if val else "medium"
                elif "cat" in name_lower or p_name == "Category":
                    category = val if val else "งานประจำ"

        if not task_text:
            continue

        todos.append({
            "id": page_id,
            "text": task_text,
            "dueDate": due_date or datetime.now().strftime("%Y-%m-%d"),
            "priority": priority,
            "category": category,
            "completed": done_bool,
            "done": done_bool,
            "notion_url": page.get("url", "")
        })

    return todos


def create_notion_todo(text, due_date=None, priority="medium", category="งานประจำ", database_id=None):
    """Create a new page in the Notion database"""
    db_id = database_id or get_target_database_id()
    if not db_id:
        raise ValueError("No Notion database connected yet.")

    # Retrieve DB schema to use exact property names
    try:
        db_meta = _notion_request(f"databases/{db_id}")
        prop_defs = db_meta.get("properties", {})
    except Exception:
        prop_defs = {}

    title_prop_name = "Task"
    done_prop_name = None
    status_prop_name = None
    date_prop_name = "Due Date"
    priority_prop_name = "Priority"
    cat_prop_name = "Category"

    for p_name, p_def in prop_defs.items():
        p_type = p_def.get("type")
        name_lower = p_name.lower()
        if p_type == "title":
            title_prop_name = p_name
        elif p_type == "checkbox" and ("done" in name_lower or "complete" in name_lower):
            done_prop_name = p_name
        elif p_type == "status" and ("status" in name_lower or "สถานะ" in name_lower or p_name == "Status"):
            status_prop_name = p_name
        elif p_type == "date" and ("date" in name_lower or "due" in name_lower):
            date_prop_name = p_name
        elif p_type == "select" and "prior" in name_lower:
            priority_prop_name = p_name
        elif p_type == "select" and ("cat" in name_lower or "หมวด" in name_lower):
            cat_prop_name = p_name

    properties = {
        title_prop_name: {
            "title": [{"type": "text", "text": {"content": text}}]
        }
    }

    if done_prop_name and prop_defs.get(done_prop_name, {}).get("type") == "checkbox":
        properties[done_prop_name] = {"checkbox": False}
    elif status_prop_name and prop_defs.get(status_prop_name, {}).get("type") == "status":
        properties[status_prop_name] = {"status": {"name": "Not started"}}

    if date_prop_name in prop_defs and prop_defs[date_prop_name].get("type") == "date" and due_date:
        properties[date_prop_name] = {"date": {"start": due_date}}

    if priority_prop_name in prop_defs and prop_defs[priority_prop_name].get("type") == "select" and priority:
        properties[priority_prop_name] = {"select": {"name": priority}}

    if cat_prop_name in prop_defs and prop_defs[cat_prop_name].get("type") == "select" and category:
        properties[cat_prop_name] = {"select": {"name": category}}

    payload = {
        "parent": {"database_id": db_id},
        "properties": properties
    }

    res = _notion_request("pages", method="POST", data=payload)
    return {
        "id": res.get("id"),
        "text": text,
        "dueDate": due_date or datetime.now().strftime("%Y-%m-%d"),
        "priority": priority,
        "category": category,
        "completed": False,
        "done": False,
        "notion_url": res.get("url")
    }


def update_notion_todo(page_id, completed=None, text=None, due_date=None, priority=None, category=None):
    """Update page properties in Notion"""
    # Fetch page to find existing property names
    page_data = _notion_request(f"pages/{page_id}")
    props = page_data.get("properties", {})

    update_props = {}
    for p_name, p_val in props.items():
        p_type = p_val.get("type")
        name_lower = p_name.lower()

        if completed is not None and p_type == "checkbox" and ("done" in name_lower or "complete" in name_lower or p_name == "Done"):
            update_props[p_name] = {"checkbox": bool(completed)}

        if completed is not None and p_type == "status" and ("status" in name_lower or "สถานะ" in name_lower or p_name == "Status"):
            update_props[p_name] = {"status": {"name": "Done" if completed else "Not started"}}

        if text is not None and p_type == "title":
            update_props[p_name] = {"title": [{"type": "text", "text": {"content": text}}]}

        if due_date is not None and p_type == "date":
            update_props[p_name] = {"date": {"start": due_date}}

        if priority is not None and p_type == "select" and "prior" in name_lower:
            update_props[p_name] = {"select": {"name": priority}}

        if category is not None and p_type == "select" and "cat" in name_lower:
            update_props[p_name] = {"select": {"name": category}}

    payload = {"properties": update_props}
    res = _notion_request(f"pages/{page_id}", method="PATCH", data=payload)
    return res


def archive_notion_todo(page_id):
    """Archive a page in Notion (equivalent to delete)"""
    payload = {"archived": True}
    res = _notion_request(f"pages/{page_id}", method="PATCH", data=payload)
    return res


def sync_todos_with_notion(local_todos):
    """
    Two-way synchronization algorithm:
    1. Fetch current items from Notion
    2. If Notion is empty and local_todos has items, push local_todos to Notion
    3. Push any local new items to Notion
    4. Sync check/uncheck states
    5. Return full updated list
    """
    db_id = get_target_database_id()
    if not db_id:
        return local_todos, False

    notion_todos = fetch_notion_todos(db_id)
    if notion_todos is None:
        return local_todos, False

    notion_map = {t["id"]: t for t in notion_todos}
    synced_list = []

    # Check each local todo
    for loc in local_todos:
        loc_id = loc.get("id", "")
        is_notion_uuid = len(loc_id) >= 32 and "-" in loc_id

        if is_notion_uuid:
            if loc_id in notion_map:
                # Item exists in both: update Notion if local completion changed
                remote = notion_map[loc_id]
                loc_done = bool(loc.get("completed") or loc.get("done"))
                rem_done = bool(remote.get("completed") or remote.get("done"))

                if loc_done != rem_done:
                    try:
                        update_notion_todo(loc_id, completed=loc_done)
                        remote["completed"] = loc_done
                        remote["done"] = loc_done
                    except Exception as e:
                        print(f"[Notion Sync] Error updating {loc_id}:", e)
                synced_list.append(remote)
                del notion_map[loc_id]
            else:
                # Item was archived/deleted in Notion -> remove from local list cleanly
                pass
        else:
            # Local item not in Notion yet -> create in Notion
            try:
                new_item = create_notion_todo(
                    text=loc.get("text", ""),
                    due_date=loc.get("dueDate"),
                    priority=loc.get("priority", "medium"),
                    category=loc.get("category", "งานประจำ"),
                    database_id=db_id
                )
                if loc.get("completed") or loc.get("done"):
                    update_notion_todo(new_item["id"], completed=True)
                    new_item["completed"] = True
                    new_item["done"] = True
                synced_list.append(new_item)
            except Exception as e:
                print(f"[Notion Sync] Error uploading {loc.get('text')}:", e)
                synced_list.append(loc)

    # Any remaining notion items that local didn't have -> include them
    for rem in notion_map.values():
        synced_list.append(rem)

    return synced_list, True
