# -*- coding: utf-8 -*-
"""
Shared E2E Test Infrastructure & Isolation Fixtures (`tests/test_helpers.py`).

Provides:
1. `ProtectedFileGuard`: Cryptographic SHA-256 pre/post snapshot verification of:
   - `notion_config.json`
   - `desktop_todo_data.json`
   - `.calendar_auth/credentials.json`
   - `.calendar_auth/gcp-oauth.keys.json`
2. `MockNotionAPI` & `MockGoogleCalendarAPI` & `MockCloudRouter`:
   - Intercepts all outbound `urllib.request.urlopen` calls to `api.notion.com`,
     `oauth2.googleapis.com`, and `www.googleapis.com`.
   - Passes `http://127.0.0.1:<port>` and `http://localhost:<port>` straight through
     to the real `urllib.request.urlopen` for real TCP socket testing against the
     ephemeral `ThreadedHTTPServer`.
3. `IsolatedWidgetTestCase`:
   - `unittest.TestCase` base class that isolates all state inside `tempfile.TemporaryDirectory()`
     via both environment variables (`TODO_DATA_FILE`, `NOTION_CONFIG_FILE`, `DISABLE_BACKGROUND_WORKERS=1`)
     and module attribute patching (`desktop_sync_server.DATA_FILE`, `notion_service.CONFIG_FILE`,
     `google_calendar_service.LOCAL_CRED_PATH`, `CRED_PATH`, `LOCAL_KEYS_PATH`, `KEYS_PATH`,
     `desktop_widget_always_on_top.DATA_FILE`).
4. `FrontendVMHarness` & `run_sw_js`:
   - Node.js `vm` behavioral execution harness for `desktop_todo_calendar_widget.html` inline JS
     and `sw.js` Service Worker lifecycle.
5. `TkinterWidgetHarness`:
   - Headless/mocked runner for `RightCornerDesktopWidget` (`desktop_widget_always_on_top.py`).
"""

import copy
import datetime
import hashlib
import io
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
import urllib.error
import urllib.parse
import urllib.request

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(TESTS_DIR)

if REPO_DIR not in sys.path:
    sys.path.insert(0, REPO_DIR)

# Set safe environment before importing application modules
os.environ.setdefault("DISABLE_BACKGROUND_WORKERS", "1")

REAL_URLOPEN = urllib.request.urlopen

PROTECTED_REL_PATHS = (
    "notion_config.json",
    "desktop_todo_data.json",
    os.path.join(".calendar_auth", "credentials.json"),
    os.path.join(".calendar_auth", "gcp-oauth.keys.json"),
)


class ProtectedFileGuard:
    """Computes and verifies SHA-256 digests of the 4 protected user credential/data files."""

    def __init__(self, repo_dir=REPO_DIR):
        self.repo_dir = repo_dir
        self.baseline = self.snapshot()

    def snapshot(self):
        state = {}
        for rel in PROTECTED_REL_PATHS:
            abs_p = os.path.join(self.repo_dir, rel)
            if os.path.exists(abs_p):
                with open(abs_p, "rb") as f:
                    raw = f.read()
                state[rel] = {
                    "exists": True,
                    "size": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                }
            else:
                state[rel] = {"exists": False, "size": 0, "sha256": None}
        return state

    def verify(self):
        current = self.snapshot()
        violations = []
        for rel in PROTECTED_REL_PATHS:
            before = self.baseline.get(rel)
            after = current.get(rel)
            if before != after:
                violations.append(
                    f"{rel}: expected {before}, got {after}"
                )
        return len(violations) == 0, violations


class MockHTTPResponse:
    """Context-manager compatible HTTP response returned by MockCloudRouter."""

    def __init__(self, payload, status=200, headers=None, url=""):
        if isinstance(payload, (dict, list)):
            self._bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        elif isinstance(payload, str):
            self._bytes = payload.encode("utf-8")
        elif isinstance(payload, bytes):
            self._bytes = payload
        else:
            self._bytes = b"{}"
        self.status = status
        self.code = status
        self.reason = "OK" if status < 400 else "Error"
        self.headers = headers or {"Content-Type": "application/json; charset=utf-8"}
        self.url = url

    def read(self):
        return self._bytes

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False


class MockNotionAPI:
    """In-memory stateful Notion API v2022-06-28 simulator."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.requests_log = []
        self.next_error = None  # tuple (code, reason, body_dict) or callable
        self.network_delay = 0.0
        self.page_counter = 100
        self.db_counter = 1
        self.paginated_batches = None  # optional list of lists of pages
        self.databases = {
            "db-mock-default": {
                "object": "database",
                "id": "db-mock-default",
                "title": [{"type": "text", "plain_text": "Desktop Widget To-Do List", "text": {"content": "Desktop Widget To-Do List"}}],
                "url": "https://www.notion.so/db-mock-default",
                "properties": {
                    "Task": {"type": "title", "title": {}},
                    "Done": {"type": "checkbox", "checkbox": {}},
                    "Due Date": {"type": "date", "date": {}},
                    "Priority": {"type": "select", "select": {"options": [{"name": "high"}, {"name": "medium"}, {"name": "low"}]}},
                    "Category": {"type": "select", "select": {"options": [{"name": "งานประจำ"}, {"name": "ประชุม"}, {"name": "ส่วนตัว"}, {"name": "ด่วน"}]}},
                },
            }
        }
        self.shared_pages = [
            {
                "object": "page",
                "id": "page-shared-001",
                "url": "https://www.notion.so/page-shared-001",
                "properties": {
                    "title": {
                        "type": "title",
                        "title": [{"type": "text", "plain_text": "Workspace Root Page"}],
                    }
                },
            }
        ]
        self.pages = {}  # page_id -> page_obj
        self.bot_info = {
            "object": "user",
            "id": "bot-user-001",
            "name": "ICT",
            "bot": {"workspace_name": "Mock Workspace"},
        }

    def seed_todo_page(
        self,
        page_id,
        text="Sample Task",
        done=False,
        due_date="2026-11-10",
        priority="medium",
        category="งานประจำ",
        database_id="db-mock-default",
        archived=False,
        url=None,
    ):
        page_url = url or f"https://www.notion.so/{page_id.replace('-', '')}"
        page = {
            "object": "page",
            "id": page_id,
            "archived": archived,
            "url": page_url,
            "parent": {"type": "database_id", "database_id": database_id},
            "properties": {
                "Task": {
                    "type": "title",
                    "title": [{"type": "text", "plain_text": text, "text": {"content": text}}],
                },
                "Done": {"type": "checkbox", "checkbox": bool(done)},
                "Due Date": {"type": "date", "date": {"start": due_date} if due_date else None},
                "Priority": {"type": "select", "select": {"name": priority} if priority else None},
                "Category": {"type": "select", "select": {"name": category} if category else None},
            },
        }
        self.pages[page_id] = page
        return page

    def extract_page_summary(self, page_id):
        page = self.pages.get(page_id)
        if not page:
            return None
        props = page.get("properties", {})
        text = ""
        done = False
        due_date = ""
        priority = "medium"
        category = "งานประจำ"
        for k, v in props.items():
            ptype = v.get("type")
            if ptype == "title":
                tlist = v.get("title", [])
                text = "".join(
                    t.get("plain_text") or t.get("text", {}).get("content", "")
                    for t in tlist
                )
            elif ptype == "checkbox":
                done = bool(v.get("checkbox", False))
            elif ptype == "status":
                st = v.get("status") or {}
                done = st.get("name") in ("Done", "Completed", "เสร็จแล้ว")
            elif ptype == "date":
                dt = v.get("date") or {}
                due_date = dt.get("start") or ""
            elif ptype == "select":
                sel = v.get("select") or {}
                name = sel.get("name", "")
                if "prior" in k.lower():
                    priority = name
                elif "cat" in k.lower() or "หมวด" in k.lower():
                    category = name
        return {
            "id": page_id,
            "text": text,
            "done": done,
            "completed": done,
            "dueDate": due_date,
            "priority": priority,
            "category": category,
            "archived": bool(page.get("archived", False)),
            "url": page.get("url", ""),
        }

    def handle_request(self, req):
        if self.network_delay > 0:
            time.sleep(self.network_delay)

        url = req.full_url
        method = req.get_method()
        headers = {k.lower(): v for k, v in req.header_items()}
        body_bytes = req.data
        body = json.loads(body_bytes.decode("utf-8")) if body_bytes else None

        self.requests_log.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "body": copy.deepcopy(body),
            }
        )

        if self.next_error is not None:
            err = self.next_error
            if callable(err):
                err_res = err(method, url, body)
                if err_res is not None:
                    code, reason, err_body = err_res
                    fp = io.BytesIO(json.dumps(err_body).encode("utf-8"))
                    raise urllib.error.HTTPError(url, code, reason, {}, fp)
            else:
                self.next_error = None
                code, reason, err_body = err
                fp = io.BytesIO(json.dumps(err_body).encode("utf-8"))
                raise urllib.error.HTTPError(url, code, reason, {}, fp)

        parsed = urllib.parse.urlparse(url)
        path = parsed.path

        if path == "/v1/users/me" and method == "GET":
            return MockHTTPResponse(self.bot_info, 200, url=url)

        if path == "/v1/search" and method == "POST":
            results = list(self.databases.values()) + list(self.shared_pages)
            return MockHTTPResponse({"object": "list", "results": results}, 200, url=url)

        if path == "/v1/databases" and method == "POST":
            self.db_counter += 1
            new_db_id = f"3fe16fa7-85cd-8086-8994-db{self.db_counter:010d}"
            title_list = (body or {}).get("title", [{"type": "text", "plain_text": "Desktop Widget To-Do List"}])
            for t in title_list:
                if "plain_text" not in t and "text" in t:
                    t["plain_text"] = t["text"].get("content", "")
            db_obj = {
                "object": "database",
                "id": new_db_id,
                "title": title_list,
                "url": f"https://www.notion.so/{new_db_id.replace('-', '')}",
                "properties": {
                    "Task": {"type": "title", "title": {}},
                    "Done": {"type": "checkbox", "checkbox": {}},
                    "Due Date": {"type": "date", "date": {}},
                    "Priority": {"type": "select", "select": {}},
                    "Category": {"type": "select", "select": {}},
                },
            }
            self.databases[new_db_id] = db_obj
            return MockHTTPResponse(db_obj, 200, url=url)

        m_db_query = re.match(r"^/v1/databases/([^/]+)/query$", path)
        if m_db_query and method == "POST":
            db_id = m_db_query.group(1)
            if self.paginated_batches is not None:
                start_cursor = (body or {}).get("start_cursor")
                idx = 0
                if start_cursor and start_cursor.startswith("cursor-page-"):
                    idx = int(start_cursor.split("-")[-1]) - 1
                batch = self.paginated_batches[idx] if idx < len(self.paginated_batches) else []
                has_more = (idx + 1) < len(self.paginated_batches)
                next_cursor = f"cursor-page-{idx + 2}" if has_more else None
                return MockHTTPResponse(
                    {
                        "object": "list",
                        "results": batch,
                        "has_more": has_more,
                        "next_cursor": next_cursor,
                    },
                    200,
                    url=url,
                )

            all_pages = [
                p for p in self.pages.values()
                if p.get("parent", {}).get("database_id") in (db_id, "db-mock-default") or db_id in self.databases
            ]
            page_size = int((body or {}).get("page_size", 100))
            start_cursor = (body or {}).get("start_cursor")
            offset = int(start_cursor) if (start_cursor and str(start_cursor).isdigit()) else 0
            sliced = all_pages[offset: offset + page_size]
            has_more = (offset + page_size) < len(all_pages)
            next_cursor = str(offset + page_size) if has_more else None
            return MockHTTPResponse(
                {
                    "object": "list",
                    "results": sliced,
                    "has_more": has_more,
                    "next_cursor": next_cursor,
                },
                200,
                url=url,
            )

        m_db_get = re.match(r"^/v1/databases/([^/]+)$", path)
        if m_db_get and method == "GET":
            db_id = m_db_get.group(1)
            db_obj = self.databases.get(db_id) or list(self.databases.values())[0]
            return MockHTTPResponse(db_obj, 200, url=url)

        if path == "/v1/pages" and method == "POST":
            self.page_counter += 1
            new_id = f"3fe16fa7-85cd-8086-8994-{self.page_counter:012x}"
            parent = (body or {}).get("parent", {})
            db_id = parent.get("database_id", "db-mock-default")
            in_props = (body or {}).get("properties", {})

            norm_props = {
                "Task": {"type": "title", "title": [{"type": "text", "plain_text": ""}]},
                "Done": {"type": "checkbox", "checkbox": False},
                "Due Date": {"type": "date", "date": {"start": datetime.datetime.now().strftime("%Y-%m-%d")}},
                "Priority": {"type": "select", "select": {"name": "medium"}},
                "Category": {"type": "select", "select": {"name": "งานประจำ"}},
            }
            for pk, pv in in_props.items():
                if "title" in pv:
                    t_items = pv["title"]
                    for ti in t_items:
                        if "plain_text" not in ti and "text" in ti:
                            ti["plain_text"] = ti["text"].get("content", "")
                    norm_props["Task"] = {"type": "title", "title": t_items}
                elif "checkbox" in pv:
                    norm_props["Done"] = {"type": "checkbox", "checkbox": bool(pv["checkbox"])}
                elif "date" in pv:
                    norm_props["Due Date"] = {"type": "date", "date": pv["date"]}
                elif "select" in pv:
                    if "prior" in pk.lower():
                        norm_props["Priority"] = {"type": "select", "select": pv["select"]}
                    elif "cat" in pk.lower() or "หมวด" in pk.lower():
                        norm_props["Category"] = {"type": "select", "select": pv["select"]}

            page_obj = {
                "object": "page",
                "id": new_id,
                "archived": False,
                "url": f"https://www.notion.so/{new_id.replace('-', '')}",
                "parent": {"type": "database_id", "database_id": db_id},
                "properties": norm_props,
            }
            self.pages[new_id] = page_obj
            return MockHTTPResponse(page_obj, 200, url=url)

        m_page = re.match(r"^/v1/pages/([^/]+)$", path)
        if m_page:
            page_id = m_page.group(1)
            if page_id not in self.pages:
                self.seed_todo_page(page_id, text="Auto Seeded Task")
            page_obj = self.pages[page_id]

            if method == "GET":
                return MockHTTPResponse(page_obj, 200, url=url)

            if method == "PATCH":
                if body and "archived" in body:
                    page_obj["archived"] = bool(body["archived"])
                if body and "properties" in body:
                    for pk, pv in body["properties"].items():
                        if "title" in pv:
                            t_items = pv["title"]
                            for ti in t_items:
                                if "plain_text" not in ti and "text" in ti:
                                    ti["plain_text"] = ti["text"].get("content", "")
                            page_obj["properties"]["Task"] = {"type": "title", "title": t_items}
                        elif "checkbox" in pv:
                            page_obj["properties"]["Done"] = {"type": "checkbox", "checkbox": bool(pv["checkbox"])}
                        elif "status" in pv:
                            st_name = (pv["status"] or {}).get("name", "")
                            page_obj["properties"]["Done"] = {
                                "type": "checkbox",
                                "checkbox": st_name in ("Done", "Completed", "เสร็จแล้ว"),
                            }
                        elif "date" in pv:
                            page_obj["properties"]["Due Date"] = {"type": "date", "date": pv["date"]}
                        elif "select" in pv:
                            if "prior" in pk.lower():
                                page_obj["properties"]["Priority"] = {"type": "select", "select": pv["select"]}
                            elif "cat" in pk.lower() or "หมวด" in pk.lower():
                                page_obj["properties"]["Category"] = {"type": "select", "select": pv["select"]}
                return MockHTTPResponse(page_obj, 200, url=url)

        return MockHTTPResponse({"object": "error", "status": 404, "message": f"MockNotion 404: {path}"}, 404, url=url)


class MockGoogleCalendarAPI:
    """In-memory stateful Google OAuth2 & Calendar API v3 simulator."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.oauth_requests = []
        self.calendar_requests = []
        self.created_events = []
        self.oauth_error = None  # e.g. (400, "Bad Request", {"error": "invalid_grant"})
        self.calendar_error = None
        self.network_delay = 0.0
        self.token_response = {
            "access_token": "mock_ya29_access_token",
            "expires_in": 3600,
            "token_type": "Bearer",
        }
        self.event_counter = 1
        self.events_by_calendar = {
            "pattarapon.thongsri@gmail.com": [],
            "coopthaiict@gmail.com": [],
            "en.th#holiday@group.v.calendar.google.com": [],
        }

    def add_mock_event(
        self,
        calendar_id="pattarapon.thongsri@gmail.com",
        summary="Mock Event",
        date_str=None,
        start_time=None,
        end_time=None,
        event_id=None,
        html_link="https://calendar.google.com/event?eid=mock",
    ):
        self.event_counter += 1
        ev_id = event_id or f"gcal-ev-{self.event_counter}"
        date_str = date_str or datetime.datetime.now().strftime("%Y-%m-%d")
        if start_time and end_time:
            item = {
                "id": ev_id,
                "summary": summary,
                "start": {"dateTime": f"{date_str}T{start_time}:00+07:00"},
                "end": {"dateTime": f"{date_str}T{end_time}:00+07:00"},
                "htmlLink": html_link,
            }
        else:
            dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")
            next_dt = (dt + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
            item = {
                "id": ev_id,
                "summary": summary,
                "start": {"date": date_str},
                "end": {"date": next_dt},
                "htmlLink": html_link,
            }
        self.events_by_calendar.setdefault(calendar_id, []).append(item)
        return item

    def handle_request(self, req):
        if self.network_delay > 0:
            time.sleep(self.network_delay)

        url = req.full_url
        method = req.get_method()
        parsed = urllib.parse.urlparse(url)
        host = parsed.netloc
        path = parsed.path

        if host == "oauth2.googleapis.com" and path == "/token":
            raw_body = req.data.decode("utf-8") if req.data else ""
            form = dict(urllib.parse.parse_qsl(raw_body))
            self.oauth_requests.append({"method": method, "url": url, "form": form})
            if self.oauth_error:
                code, reason, err_dict = self.oauth_error
                fp = io.BytesIO(json.dumps(err_dict).encode("utf-8"))
                raise urllib.error.HTTPError(url, code, reason, {}, fp)
            return MockHTTPResponse(self.token_response, 200, url=url)

        if host == "www.googleapis.com":
            if self.calendar_error:
                code, reason, err_dict = self.calendar_error
                fp = io.BytesIO(json.dumps(err_dict).encode("utf-8"))
                raise urllib.error.HTTPError(url, code, reason, {}, fp)

            m_cal = re.match(r"^/calendar/v3/calendars/([^/]+)/events$", path)
            if m_cal:
                cal_id = urllib.parse.unquote(m_cal.group(1))
                query = dict(urllib.parse.parse_qsl(parsed.query))
                body = json.loads(req.data.decode("utf-8")) if req.data else None
                self.calendar_requests.append(
                    {
                        "method": method,
                        "url": url,
                        "calendar_id": cal_id,
                        "query": query,
                        "body": copy.deepcopy(body),
                    }
                )
                if method == "GET":
                    items = list(self.events_by_calendar.get(cal_id, []))
                    time_min = query.get("timeMin", "")[:10]
                    time_max = query.get("timeMax", "")[:10]
                    if time_min and time_max:
                        filtered = []
                        for it in items:
                            st = it.get("start", {})
                            ev_date = st.get("date") or (st.get("dateTime", "")[:10])
                            if not ev_date or (time_min <= ev_date <= time_max):
                                filtered.append(it)
                        items = filtered
                    return MockHTTPResponse({"kind": "calendar#events", "items": items}, 200, url=url)

                if method == "POST":
                    self.event_counter += 1
                    created_id = f"ev-mock-{self.event_counter}"
                    created_item = {
                        "id": created_id,
                        "summary": (body or {}).get("summary", ""),
                        "description": (body or {}).get("description", ""),
                        "start": (body or {}).get("start", {}),
                        "end": (body or {}).get("end", {}),
                        "htmlLink": f"https://calendar.google.com/event?eid={created_id}",
                    }
                    self.events_by_calendar.setdefault(cal_id, []).append(created_item)
                    self.created_events.append(
                        {"calendar_id": cal_id, "payload": copy.deepcopy(body), "response": created_item}
                    )
                    return MockHTTPResponse(created_item, 200, url=url)

        return MockHTTPResponse({"error": f"Unrecognized mock Google URL: {url}"}, 404, url=url)


class MockCloudRouter:
    """
    URL-routing wrapper around `urllib.request.urlopen`:
    - `http://127.0.0.1:*` and `http://localhost:*` -> delegates to `REAL_URLOPEN`
    - `https://api.notion.com/*` -> delegates to `MockNotionAPI`
    - `https://oauth2.googleapis.com/*` and `https://www.googleapis.com/*` -> delegates to `MockGoogleCalendarAPI`
    - Any other outbound URL -> raises `AssertionError`
    """

    def __init__(self, mock_notion: MockNotionAPI, mock_gcal: MockGoogleCalendarAPI):
        self.mock_notion = mock_notion
        self.mock_gcal = mock_gcal

    def __call__(self, url_or_req, data=None, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, *args, **kwargs):
        if isinstance(url_or_req, str):
            req = urllib.request.Request(url_or_req, data=data)
        else:
            req = url_or_req

        full_url = req.full_url
        parsed = urllib.parse.urlparse(full_url)
        host = (parsed.hostname or "").lower()

        if host in ("127.0.0.1", "localhost"):
            if data is not None and isinstance(url_or_req, str):
                return REAL_URLOPEN(url_or_req, data=data, timeout=timeout, *args, **kwargs)
            return REAL_URLOPEN(url_or_req, timeout=timeout, *args, **kwargs)

        if host == "api.notion.com":
            return self.mock_notion.handle_request(req)

        if host in ("oauth2.googleapis.com", "www.googleapis.com"):
            return self.mock_gcal.handle_request(req)

        raise AssertionError(f"CRITICAL: Blocked unexpected external network request to {full_url}")


def get_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class IsolatedWidgetTestCase(unittest.TestCase):
    """
    Base fixture for all Tier 1-4 E2E tests.
    Guarantees 100% file and network isolation in `tempfile.TemporaryDirectory()`.
    """

    def setUp(self):
        super().setUp()
        import desktop_sync_server
        import google_calendar_service
        import notion_service

        self.desktop_sync_server = desktop_sync_server
        self.notion_service = notion_service
        self.google_calendar_service = google_calendar_service

        self._temp_dir_obj = tempfile.TemporaryDirectory(prefix="widget_e2e_")
        self.temp_dir = self._temp_dir_obj.name

        self.todo_data_file = os.path.join(self.temp_dir, "desktop_todo_data.json")
        self.notion_config_file = os.path.join(self.temp_dir, "notion_config.json")
        self.cal_auth_dir = os.path.join(self.temp_dir, ".calendar_auth")
        os.makedirs(self.cal_auth_dir, exist_ok=True)
        self.cred_file = os.path.join(self.cal_auth_dir, "credentials.json")
        self.keys_file = os.path.join(self.cal_auth_dir, "gcp-oauth.keys.json")
        self.env_file = os.path.join(self.temp_dir, ".env")

        # Write default isolated test credentials
        with open(self.notion_config_file, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "token": "ntn_mock_isolated_token_12345",
                    "database_id": "db-mock-default",
                    "page_id": "page-shared-001",
                    "last_sync": None,
                },
                f,
                indent=2,
            )

        with open(self.cred_file, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "access_token": "initial_expired_token",
                    "refresh_token": "mock_refresh_token_xyz",
                    "expiry_date": 0,
                },
                f,
                indent=2,
            )

        with open(self.keys_file, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "installed": {
                        "client_id": "mock-client-id.apps.googleusercontent.com",
                        "client_secret": "mock-client-secret",
                    }
                },
                f,
                indent=2,
            )

        with open(self.todo_data_file, "w", encoding="utf-8") as f:
            json.dump({"todos": [], "events": []}, f, indent=2)

        # Save and isolate environment variables
        self._saved_environ = os.environ.copy()
        for k in (
            "NOTION_API_TOKEN",
            "NOTION_TOKEN",
            "NOTION_DATABASE_ID",
            "GOOGLE_CALENDAR_CRED_JSON",
            "GOOGLE_CALENDAR_KEYS_JSON",
            "WIDGET_API_KEY",
        ):
            os.environ.pop(k, None)

        os.environ["TODO_DATA_FILE"] = self.todo_data_file
        os.environ["NOTION_CONFIG_FILE"] = self.notion_config_file
        os.environ["DISABLE_BACKGROUND_WORKERS"] = "1"

        # Save and patch module-level globals
        self._orig_server_data_file = desktop_sync_server.DATA_FILE
        self._orig_server_port = desktop_sync_server.PORT
        self._orig_server_html_file = desktop_sync_server.HTML_FILE
        self._orig_notion_config_file = notion_service.CONFIG_FILE
        self._orig_gcal_local_cred = google_calendar_service.LOCAL_CRED_PATH
        self._orig_gcal_cred = google_calendar_service.CRED_PATH
        self._orig_gcal_local_keys = google_calendar_service.LOCAL_KEYS_PATH
        self._orig_gcal_keys = google_calendar_service.KEYS_PATH

        desktop_sync_server.DATA_FILE = self.todo_data_file
        notion_service.CONFIG_FILE = self.notion_config_file
        google_calendar_service.LOCAL_CRED_PATH = self.cred_file
        google_calendar_service.CRED_PATH = self.cred_file
        google_calendar_service.LOCAL_KEYS_PATH = self.keys_file
        google_calendar_service.KEYS_PATH = self.keys_file
        google_calendar_service.cached_token = None
        google_calendar_service.cached_expiry = 0

        # Set up Mock Cloud Router
        self.mock_notion = MockNotionAPI()
        self.mock_gcal = MockGoogleCalendarAPI()
        self.cloud_router = MockCloudRouter(self.mock_notion, self.mock_gcal)
        self._urlopen_patcher = unittest.mock.patch(
            "urllib.request.urlopen", side_effect=self.cloud_router
        )
        self._urlopen_patcher.start()

        self.httpd = None
        self.server_thread = None
        self.server_port = None
        self.base_url = None

    def tearDown(self):
        # Wait briefly for any daemon async_push_todo_changes_to_notion thread to finish
        for t in list(threading.enumerate()):
            if t is not threading.current_thread() and t.daemon and "_worker" in t.name:
                t.join(timeout=0.3)

        if self.httpd is not None:
            try:
                self.httpd.shutdown()
                self.httpd.server_close()
            except Exception:
                pass
            if self.server_thread is not None:
                self.server_thread.join(timeout=1.0)
            self.httpd = None

        self._urlopen_patcher.stop()

        # Restore module globals
        self.desktop_sync_server.DATA_FILE = self._orig_server_data_file
        self.desktop_sync_server.PORT = self._orig_server_port
        self.desktop_sync_server.HTML_FILE = self._orig_server_html_file
        self.notion_service.CONFIG_FILE = self._orig_notion_config_file
        self.google_calendar_service.LOCAL_CRED_PATH = self._orig_gcal_local_cred
        self.google_calendar_service.CRED_PATH = self._orig_gcal_cred
        self.google_calendar_service.LOCAL_KEYS_PATH = self._orig_gcal_local_keys
        self.google_calendar_service.KEYS_PATH = self._orig_gcal_keys
        self.google_calendar_service.cached_token = None
        self.google_calendar_service.cached_expiry = 0

        if "desktop_widget_always_on_top" in sys.modules:
            sys.modules["desktop_widget_always_on_top"].DATA_FILE = self._orig_server_data_file

        # Restore environment
        os.environ.clear()
        os.environ.update(self._saved_environ)

        try:
            self._temp_dir_obj.cleanup()
        except Exception:
            pass
        super().tearDown()

    def start_ephemeral_server(self):
        if self.httpd is not None:
            return self.base_url
        self.httpd = self.desktop_sync_server.ThreadedHTTPServer(
            ("127.0.0.1", 0), self.desktop_sync_server.SyncRequestHandler
        )
        self.server_port = self.httpd.server_port
        self.desktop_sync_server.PORT = self.server_port
        os.environ["PORT"] = str(self.server_port)
        self.base_url = f"http://127.0.0.1:{self.server_port}"
        self.server_thread = threading.Thread(
            target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        self.server_thread.start()
        return self.base_url

    def api_request(self, method, path, body=None, headers=None, raw_body=None):
        if self.httpd is None:
            self.start_ephemeral_server()
        url = f"{self.base_url}{path if path.startswith('/') else '/' + path}"
        req_headers = {}
        if headers:
            req_headers.update(headers)

        data_bytes = None
        if raw_body is not None:
            data_bytes = raw_body if isinstance(raw_body, bytes) else str(raw_body).encode("utf-8")
            req_headers.setdefault("Content-Type", "application/json")
        elif body is not None:
            data_bytes = json.dumps(body, ensure_ascii=False).encode("utf-8")
            req_headers.setdefault("Content-Type", "application/json; charset=utf-8")

        req = urllib.request.Request(url, data=data_bytes, headers=req_headers, method=method)
        try:
            with REAL_URLOPEN(req, timeout=5) as resp:
                raw = resp.read()
                resp_headers = {k.lower(): v for k, v in resp.headers.items()}
                ctype = resp_headers.get("content-type", "")
                if "application/json" in ctype or "manifest+json" in ctype:
                    try:
                        return resp.status, resp_headers, json.loads(raw.decode("utf-8"))
                    except Exception:
                        return resp.status, resp_headers, raw.decode("utf-8", errors="replace")
                return resp.status, resp_headers, raw.decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            raw = e.read() if e.fp else b""
            resp_headers = {k.lower(): v for k, v in e.headers.items()} if e.headers else {}
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except Exception:
                parsed = raw.decode("utf-8", errors="replace")
            return e.code, resp_headers, parsed

    def wait_for_notion_async(self, timeout=1.5):
        """Wait for daemon threads spawned by async_push_todo_changes_to_notion."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            alive = [
                t for t in threading.enumerate()
                if t is not threading.current_thread()
                and t.daemon
                and t is not self.server_thread
                and t.is_alive()
            ]
            if not alive:
                break
            time.sleep(0.02)


class FrontendVMHarness:
    """
    Executes the inline `<script>` from `desktop_todo_calendar_widget.html` inside Node.js `vm`
    with a full stateful Mock DOM (`textContent` vs `innerHTML` tracking, `localStorage`,
    `sessionStorage`, `Notification`, programmable `fetch`) for behavioral verification of F8-F13.
    """

    _cached_html = None
    _cached_script = None
    _cached_ids = None

    @classmethod
    def load_html_and_script(cls):
        html_path = os.path.join(REPO_DIR, "desktop_todo_calendar_widget.html")
        with open(html_path, "r", encoding="utf-8") as f:
            html = f.read()
        scripts = re.findall(r"<script(?![^>]*src=)[^>]*>([\s\S]*?)</script>", html)
        script_code = scripts[-1] if scripts else ""
        ids = list(set(re.findall(r'\bid=["\']([^"\']+)["\']', html)))
        return html, script_code, ids

    @classmethod
    def run_js(cls, test_js_snippet, init_options=None):
        html, script_code, ids = cls.load_html_and_script()
        payload = {
            "html": html,
            "script": script_code,
            "ids": ids,
            "testSnippet": test_js_snippet,
            "options": init_options or {},
        }
        driver = r"""
const vm = require('vm');
const fs = require('fs');

const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const options = input.options || {};

function escapeForTextContent(str) {
  return String(str == null ? '' : str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function stripTagsToText(html) {
  return String(html == null ? '' : html)
    .replace(/<[^>]*>/g, ' ')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'");
}

const rawInnerHTMLAssignments = [];
const allCreatedElements = [];

class MockClassList {
  constructor(el) {
    this.el = el;
  }
  _set() {
    return new Set((this.el.className || '').split(/\s+/).filter(Boolean));
  }
  _sync(s) {
    this.el.className = Array.from(s).join(' ');
  }
  add(...tokens) {
    const s = this._set();
    tokens.forEach(t => { if (t) s.add(t); });
    this._sync(s);
  }
  remove(...tokens) {
    const s = this._set();
    tokens.forEach(t => s.delete(t));
    this._sync(s);
  }
  toggle(token, force) {
    const s = this._set();
    const has = s.has(token);
    const shouldHave = force !== undefined ? !!force : !has;
    if (shouldHave) s.add(token); else s.delete(token);
    this._sync(s);
    return shouldHave;
  }
  contains(token) {
    return this._set().has(token);
  }
}

class MockElement {
  constructor(tagName = 'DIV', id = '') {
    this.tagName = String(tagName).toUpperCase();
    this.id = id;
    this.className = '';
    this.classList = new MockClassList(this);
    this.style = {};
    this.dataset = {};
    this.attributes = {};
    this.children = [];
    this.parentNode = null;
    this.listeners = {};
    this.value = '';
    this.checked = false;
    this.disabled = false;
    this.type = '';
    this.href = '';
    this.target = '';
    this.rel = '';
    this._innerHTML = '';
    this._textContent = '';
    allCreatedElements.push(this);
  }
  get innerHTML() {
    if (this.children.length > 0 && !this._innerHTML) {
      return this.children.map(c => c.outerHTML).join('');
    }
    return this._innerHTML;
  }
  set innerHTML(val) {
    const s = String(val == null ? '' : val);
    this._innerHTML = s;
    this._textContent = stripTagsToText(s);
    this.children = [];
    rawInnerHTMLAssignments.push({ id: this.id, tagName: this.tagName, html: s });
  }
  get textContent() {
    if (this.children.length > 0) {
      return (this._textContent || '') + this.children.map(c => c.textContent).join(' ');
    }
    return this._textContent;
  }
  set textContent(val) {
    const s = String(val == null ? '' : val);
    this._textContent = s;
    this._innerHTML = escapeForTextContent(s);
    this.children = [];
  }
  get outerHTML() {
    const attrs = [];
    if (this.id) attrs.push(`id="${escapeForTextContent(this.id)}"`);
    if (this.className) attrs.push(`class="${escapeForTextContent(this.className)}"`);
    if (this.href) attrs.push(`href="${escapeForTextContent(this.href)}"`);
    if (this.target) attrs.push(`target="${escapeForTextContent(this.target)}"`);
    if (this.rel) attrs.push(`rel="${escapeForTextContent(this.rel)}"`);
    for (const [k, v] of Object.entries(this.attributes)) {
      attrs.push(`${k}="${escapeForTextContent(v)}"`);
    }
    for (const [k, v] of Object.entries(this.dataset)) {
      attrs.push(`data-${k}="${escapeForTextContent(v)}"`);
    }
    const attrStr = attrs.length ? ' ' + attrs.join(' ') : '';
    const body = this.children.length > 0
      ? this.children.map(c => c.outerHTML).join('')
      : this._innerHTML;
    return `<${this.tagName.toLowerCase()}${attrStr}>${body}</${this.tagName.toLowerCase()}>`;
  }
  appendChild(child) {
    if (child && typeof child === 'object') {
      child.parentNode = this;
      this.children.push(child);
    }
    return child;
  }
  removeChild(child) {
    const idx = this.children.indexOf(child);
    if (idx >= 0) this.children.splice(idx, 1);
    return child;
  }
  remove() {
    if (this.parentNode) this.parentNode.removeChild(this);
  }
  setAttribute(k, v) {
    this.attributes[k] = String(v);
    if (k === 'id') this.id = String(v);
    if (k === 'class') this.className = String(v);
    if (k === 'href') this.href = String(v);
    if (k === 'target') this.target = String(v);
    if (k === 'rel') this.rel = String(v);
    if (k.startsWith('data-')) this.dataset[k.slice(5)] = String(v);
  }
  getAttribute(k) {
    if (k === 'id') return this.id;
    if (k === 'class') return this.className;
    if (k === 'href') return this.href;
    if (k.startsWith('data-')) return this.dataset[k.slice(5)] || this.attributes[k] || null;
    return this.attributes[k] !== undefined ? this.attributes[k] : null;
  }
  addEventListener(type, fn) {
    if (!this.listeners[type]) this.listeners[type] = [];
    this.listeners[type].push(fn);
  }
  dispatchEvent(ev) {
    const type = ev.type;
    ev.target = ev.target || this;
    ev.currentTarget = this;
    ev.preventDefault = ev.preventDefault || (() => {});
    ev.stopPropagation = ev.stopPropagation || (() => {});
    const list = this.listeners[type] || [];
    for (const fn of list) {
      fn.call(this, ev);
    }
    if (typeof this['on' + type] === 'function') {
      this['on' + type].call(this, ev);
    }
  }
  click() {
    if (this.tagName === 'INPUT' && this.type === 'checkbox') {
      this.checked = !this.checked;
      this.dispatchEvent({ type: 'change', target: this });
    }
    this.dispatchEvent({ type: 'click', target: this });
  }
  focus() {}
  select() {}
  closest(selector) {
    return null;
  }
  querySelector(sel) {
    return this.querySelectorAll(sel)[0] || null;
  }
  querySelectorAll(sel) {
    const results = [];
    const visit = (node) => {
      for (const ch of node.children || []) {
        if (matchesSelector(ch, sel)) results.push(ch);
        visit(ch);
      }
    };
    visit(this);
    return results;
  }
}

function matchesSelector(el, sel) {
  if (!el) return false;
  if (sel.startsWith('#')) return el.id === sel.slice(1);
  if (sel.startsWith('.')) {
    const cls = sel.slice(1).split('[')[0];
    if (!el.classList.contains(cls)) return false;
    const m = sel.match(/\[data-([a-zA-Z0-9_-]+)=["']?([^"'\]]+)["']?\]/);
    if (m) return el.dataset[m[1]] === m[2];
    return true;
  }
  const attrM = sel.match(/^\[([a-zA-Z0-9_-]+)=["']?([^"'\]]+)["']?\]$/);
  if (attrM) {
    return (el.attributes && el.attributes[attrM[1]] === attrM[2]) || el[attrM[1]] === attrM[2];
  }
  return el.tagName.toLowerCase() === sel.toLowerCase();
}

const elementsById = {};
function ensureElement(id, tag = 'DIV') {
  if (!elementsById[id]) {
    elementsById[id] = new MockElement(tag, id);
  }
  return elementsById[id];
}

for (const id of input.ids || []) {
  ensureElement(id);
}
// Ensure all M2 contract element IDs exist even if testing pre-M2 or post-M2
const contractIds = [
  'todoForm', 'todoInput', 'todoDueDate', 'todoPriority', 'todoCategory',
  'todoSearchInput', 'todoCategoryFilter', 'todoSortSelect',
  'todoListContainer', 'todoProgressBar', 'todoProgressText', 'tabTodoCount',
  'btnClearCompleted', 'btnOpenNotionModal', 'notionSyncBtnText', 'notionSyncDot',
  'notionModal', 'btnCloseNotionModal', 'notionModalStatus', 'notionModalInfo',
  'btnAutoConnectNotion', 'btnManualSyncNotion',
  'setupModal', 'btnOpenSetup', 'btnCloseSetupModal', 'apiKeyInput', 'btnSaveApiKey',
  'btnToggleNotify', 'notifyBadgeDot',
  'chkCalPrimary', 'chkCalCoop', 'chkCalHoliday',
  'weekViewMode', 'btnPrevWeek', 'btnTodayWeek', 'btnNextWeek',
  'weekRangeSubtitle', 'weekDaysContainer', 'tabEventCount',
  'addEventForm', 'evtTitle', 'evtDate', 'evtTime', 'evtAllDay', 'evtStartTime', 'evtEndTime',
  'evtCalendarAccount', 'btnSubmitEvent', 'btnRefreshLiveCal',
  'toastContainer', 'syncBadge', 'currentDateText', 'currentTimeText',
  'editTodoModal', 'editTodoForm', 'editTodoId', 'editTodoText', 'editTodoDueDate',
  'editTodoPriority', 'editTodoCategory', 'btnCancelEditTodo'
];
for (const cid of contractIds) {
  ensureElement(cid);
}

// Defaults matching HTML
ensureElement('chkCalPrimary', 'INPUT').checked = true;
ensureElement('chkCalPrimary').type = 'checkbox';
ensureElement('chkCalCoop', 'INPUT').checked = true;
ensureElement('chkCalCoop').type = 'checkbox';
ensureElement('chkCalHoliday', 'INPUT').checked = true;
ensureElement('chkCalHoliday').type = 'checkbox';
ensureElement('evtAllDay', 'INPUT').checked = false;
ensureElement('evtAllDay').type = 'checkbox';
ensureElement('evtStartTime', 'INPUT').value = '09:00';
ensureElement('evtEndTime', 'INPUT').value = '10:30';
ensureElement('evtTime', 'INPUT').value = '09:00 - 10:30';
ensureElement('weekViewMode', 'SELECT').value = 'rolling7';
ensureElement('todoPriority', 'SELECT').value = 'medium';
ensureElement('todoCategory', 'SELECT').value = 'งานประจำ';
ensureElement('todoCategoryFilter', 'SELECT').value = 'all';
ensureElement('todoSortSelect', 'SELECT').value = 'default';
ensureElement('todoSearchInput', 'INPUT').value = '';
ensureElement('setupModal').classList.add('hidden');
ensureElement('setupModal').attributes.role = 'dialog';
ensureElement('notionModal').classList.add('hidden');
ensureElement('notionModal').attributes.role = 'dialog';
ensureElement('editTodoModal').classList.add('hidden');
ensureElement('editTodoModal').attributes.role = 'dialog';

// Filter buttons (.todo-filter-btn)
const filterButtons = ['all', 'active', 'done'].map(f => {
  const b = new MockElement('BUTTON', `filter_btn_${f}`);
  b.className = 'todo-filter-btn';
  b.dataset.filter = f;
  return b;
});

class MockStorage {
  constructor(initial = {}) {
    this.store = Object.assign({}, initial);
    this.throwOnSet = false;
  }
  getItem(k) {
    return Object.prototype.hasOwnProperty.call(this.store, k) ? this.store[k] : null;
  }
  setItem(k, v) {
    if (this.throwOnSet) {
      const err = new Error('QuotaExceededError');
      err.name = 'QuotaExceededError';
      throw err;
    }
    this.store[k] = String(v);
  }
  removeItem(k) {
    delete this.store[k];
  }
  clear() {
    this.store = {};
  }
}

const localStorage = new MockStorage(options.localStorage || {});
const sessionStorage = new MockStorage(options.sessionStorage || {});

const fetchCalls = [];
let customFetchHandler = null;
async function mockFetch(url, fetchOpts = {}) {
  fetchCalls.push({ url: String(url), options: fetchOpts });
  if (customFetchHandler) {
    return await customFetchHandler(String(url), fetchOpts);
  }
  return {
    ok: true,
    status: 200,
    json: async () => ({ status: 'ok', todos: [], events: [] }),
    text: async () => '{}'
  };
}

const notificationsSent = [];
class MockNotification {
  constructor(title, opts = {}) {
    this.title = title;
    this.options = opts;
    notificationsSent.push({ title, options: opts });
  }
  static async requestPermission() {
    MockNotification.permission = MockNotification._nextPermission || 'granted';
    return MockNotification.permission;
  }
}
MockNotification.permission = options.notificationPermission || 'default';
MockNotification._nextPermission = options.nextNotificationPermission || 'granted';

const swRegistrations = [];
const navigatorObj = {
  serviceWorker: {
    register: async (scriptUrl) => {
      if (options.rejectSwRegister) throw new Error('SW register failed');
      swRegistrations.push(scriptUrl);
      return { scope: '/' };
    }
  }
};

const docListeners = {};
const winListeners = {};

const documentObj = {
  body: new MockElement('BODY', 'body'),
  getElementById: (id) => ensureElement(id),
  createElement: (tag) => new MockElement(tag),
  querySelector: (sel) => {
    const all = documentObj.querySelectorAll(sel);
    return all[0] || null;
  },
  querySelectorAll: (sel) => {
    if (sel === '.todo-filter-btn') return filterButtons;
    return allCreatedElements.filter(el => matchesSelector(el, sel));
  },
  addEventListener: (type, fn) => {
    if (!docListeners[type]) docListeners[type] = [];
    docListeners[type].push(fn);
  },
  dispatchEvent: (ev) => {
    const list = docListeners[ev.type] || [];
    for (const fn of list) fn(ev);
  }
};

const locationObj = {
  protocol: options.protocol || 'http:',
  origin: options.origin || 'http://127.0.0.1:18234',
  hostname: '127.0.0.1',
  port: '18234',
  href: (options.origin || 'http://127.0.0.1:18234') + '/'
};

const intervals = [];
const timeouts = [];

const sandbox = {
  window: null,
  document: documentObj,
  localStorage,
  sessionStorage,
  navigator: navigatorObj,
  location: locationObj,
  Notification: options.disableNotificationAPI ? undefined : MockNotification,
  fetch: mockFetch,
  console: { log: () => {}, warn: () => {}, error: () => {} },
  setInterval: (fn, ms) => { intervals.push({ fn, ms }); return intervals.length; },
  clearInterval: () => {},
  setTimeout: (fn, ms) => { timeouts.push({ fn, ms }); return setTimeout(fn, ms); },
  clearTimeout: (id) => clearTimeout(id),
  todayIso: new Date().toISOString().slice(0, 10),
  alert: (msg) => {},
  confirm: () => true,
  addEventListener: (type, fn) => {
    if (!winListeners[type]) winListeners[type] = [];
    winListeners[type].push(fn);
  },
  dispatchEvent: (ev) => {
    const list = winListeners[ev.type] || [];
    for (const fn of list) fn(ev);
  },
  Date,
  Math,
  JSON,
  Promise,
  Set,
  Map,
  Array,
  Object,
  String,
  Number,
  Boolean,
  RegExp,
  Error,
  TypeError,
   encodeURIComponent,
  decodeURIComponent,
  parseInt,
  parseFloat,
  isNaN,
  __test_helpers: {
    elementsById,
    allCreatedElements,
    rawInnerHTMLAssignments,
    fetchCalls,
    setFetchHandler: (fn) => { customFetchHandler = fn; },
    notificationsSent,
    swRegistrations,
    filterButtons,
    intervals,
    timeouts,
    docListeners,
    winListeners,
    MockElement
  }
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
if (options.disableNotificationAPI) {
  delete sandbox.Notification;
}

vm.createContext(sandbox);
vm.runInContext(input.script, sandbox);

(async () => {
  try {
    // Trigger load listener if registered
    for (const fn of (winListeners['load'] || [])) {
      await fn({ type: 'load' });
    }
    const result = await vm.runInContext(`(async () => { ${input.testSnippet} })()`, sandbox);
    process.stdout.write(JSON.stringify({ ok: true, result }));
  } catch (err) {
    process.stdout.write(JSON.stringify({ ok: false, error: err && (err.stack || err.message || String(err)) }));
  }
})();
"""
        proc = subprocess.run(
            ["node", "-e", driver],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            timeout=10,
        )
        if proc.returncode != 0:
            raise AssertionError(f"Node VM exited with {proc.returncode}: {proc.stderr}")
        out = json.loads(proc.stdout)
        if not out.get("ok"):
            raise AssertionError(f"Frontend JS test failed:\n{out.get('error')}")
        return out.get("result")

    @classmethod
    def run_sw_js(cls, test_js_snippet):
        sw_path = os.path.join(REPO_DIR, "sw.js")
        if not os.path.exists(sw_path):
            raise AssertionError("sw.js does not exist in repository root")
        with open(sw_path, "r", encoding="utf-8") as f:
            sw_code = f.read()

        payload = {"swCode": sw_code, "testSnippet": test_js_snippet}
        driver = r"""
const vm = require('vm');
const fs = require('fs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));

class MockResponse {
  constructor(body, init = {}) {
    this._body = typeof body === 'string' ? body : JSON.stringify(body);
    this.status = init.status || 200;
    this.ok = this.status >= 200 && this.status < 300;
    this.type = init.type || 'basic';
    this.headers = init.headers || {};
  }
  clone() {
    return new MockResponse(this._body, { status: this.status, type: this.type, headers: this.headers });
  }
  async text() { return this._body; }
  async json() { return JSON.parse(this._body); }
}

class MockCache {
  constructor(name) {
    this.name = name;
    this.entries = new Map();
  }
  _key(req) {
    return typeof req === 'string' ? req : req.url;
  }
  async addAll(urls) {
    for (const u of urls) {
      this.entries.set(u, new MockResponse(`cached:${u}`, { status: 200 }));
    }
  }
  async put(req, res) {
    this.entries.set(this._key(req), res);
  }
  async match(req) {
    const k = this._key(req);
    if (this.entries.has(k)) return this.entries.get(k).clone();
    try {
      const u = new URL(k, 'http://127.0.0.1:18234');
      if (this.entries.has(u.pathname)) return this.entries.get(u.pathname).clone();
    } catch (e) {}
    return undefined;
  }
}

const cacheStore = new Map();
const cachesObj = {
  async open(name) {
    if (!cacheStore.has(name)) cacheStore.set(name, new MockCache(name));
    return cacheStore.get(name);
  },
  async keys() {
    return Array.from(cacheStore.keys());
  },
  async delete(name) {
    return cacheStore.delete(name);
  },
  async match(req) {
    for (const c of cacheStore.values()) {
      const m = await c.match(req);
      if (m) return m;
    }
    return undefined;
  }
};

const listeners = {};
let networkOnline = true;
const networkCalls = [];

async function mockFetch(req) {
  const url = typeof req === 'string' ? req : req.url;
  const method = (typeof req === 'object' && req.method) ? req.method : 'GET';
  networkCalls.push({ url, method });
  if (!networkOnline) {
    throw new Error('Offline network error');
  }
  return new MockResponse(JSON.stringify({ url, live: true }), { status: 200 });
}

const selfObj = {
  location: { origin: 'http://127.0.0.1:18234' },
  skipWaiting: async () => {},
  clients: { claim: async () => {} },
  addEventListener: (type, fn) => {
    if (!listeners[type]) listeners[type] = [];
    listeners[type].push(fn);
  },
  caches: cachesObj,
  fetch: mockFetch,
  Response: MockResponse,
  URL,
  Promise,
  console: { log: () => {}, warn: () => {}, error: () => {} },
  __sw_test: {
    cacheStore,
    listeners,
    networkCalls,
    setOnline: (v) => { networkOnline = v; },
    MockResponse,
    async dispatchInstall() {
      const promises = [];
      for (const fn of (listeners['install'] || [])) {
        fn({ waitUntil: (p) => promises.push(p) });
      }
      await Promise.all(promises);
    },
    async dispatchActivate() {
      const promises = [];
      for (const fn of (listeners['activate'] || [])) {
        fn({ waitUntil: (p) => promises.push(p) });
      }
      await Promise.all(promises);
    },
    async dispatchFetch(url, method = 'GET') {
      const fullUrl = url.startsWith('http') ? url : ('http://127.0.0.1:18234' + url);
      const req = { url: fullUrl, method, mode: method === 'GET' && url === '/' ? 'navigate' : 'cors' };
      let respondedPromise = null;
      for (const fn of (listeners['fetch'] || [])) {
        fn({
          request: req,
          respondWith: (p) => { respondedPromise = p; }
        });
      }
      if (respondedPromise) {
        return await respondedPromise;
      }
      return await mockFetch(req);
    }
  }
};
selfObj.self = selfObj;
selfObj.globalThis = selfObj;

vm.createContext(selfObj);
vm.runInContext(input.swCode, selfObj);

(async () => {
  try {
    const res = await vm.runInContext(`(async () => { ${input.testSnippet} })()`, selfObj);
    process.stdout.write(JSON.stringify({ ok: true, result: res }));
  } catch (err) {
    process.stdout.write(JSON.stringify({ ok: false, error: err && (err.stack || err.message || String(err)) }));
  }
})();
"""
        proc = subprocess.run(
            ["node", "-e", driver],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            timeout=10,
        )
        if proc.returncode != 0:
            raise AssertionError(f"Node SW VM exited with {proc.returncode}: {proc.stderr}")
        out = json.loads(proc.stdout)
        if not out.get("ok"):
            raise AssertionError(f"SW JS test failed:\n{out.get('error')}")
        return out.get("result")


class MockTkWidget:
    """Lightweight headless mock for Tkinter widgets when running without X11 display."""

    def __init__(self, master=None, **kwargs):
        self.master = master
        self.config_kwargs = dict(kwargs)
        self.children_widgets = []
        self._value = ""
        if master is not None and hasattr(master, "children_widgets"):
            master.children_widgets.append(self)

    def title(self, *args): pass
    def configure(self, **kwargs): self.config_kwargs.update(kwargs)
    config = configure
    def attributes(self, *args): pass
    def winfo_screenwidth(self): return 1920
    def geometry(self, *args): pass
    def after(self, ms, func=None, *args): return "after_id"
    def pack(self, **kwargs): pass
    def grid(self, **kwargs): pass
    def place(self, **kwargs): pass
    def bind(self, *args, **kwargs): pass
    def create_window(self, *args, **kwargs): return 1
    def bbox(self, *args): return (0, 0, 100, 100)
    def yview(self, *args): pass
    def set(self, *args): pass
    def winfo_children(self): return list(self.children_widgets)
    def destroy(self):
        if self.master and hasattr(self.master, "children_widgets"):
            if self in self.master.children_widgets:
                self.master.children_widgets.remove(self)
    def get(self): return self._value
    def insert(self, idx, text):
        if idx == 0 or idx == "0":
            self._value = str(text) + self._value
        else:
            self._value = self._value + str(text)
    def delete(self, first, last=None):
        self._value = ""


class TkinterWidgetHarness:
    """Instantiates `RightCornerDesktopWidget` safely in headless mode against isolated temp files."""

    @staticmethod
    def create_widget(todo_data_file, server_port=None):
        os.environ["TODO_DATA_FILE"] = todo_data_file
        os.environ["DISABLE_BACKGROUND_WORKERS"] = "1"
        if server_port is not None:
            os.environ["PORT"] = str(server_port)

        with unittest.mock.patch("desktop_sync_server.start_in_thread", return_value=None):
            import desktop_widget_always_on_top
            desktop_widget_always_on_top.DATA_FILE = todo_data_file

        # Try M3 headless constructor first
        try:
            widget = desktop_widget_always_on_top.RightCornerDesktopWidget(
                root=None, auto_start_server=False, headless=True
            )
            return widget
        except TypeError:
            pass

        # Fallback: mock tk widget classes so pre-M3 constructor works headlessly
        with unittest.mock.patch("tkinter.Frame", MockTkWidget), \
             unittest.mock.patch("tkinter.Label", MockTkWidget), \
             unittest.mock.patch("tkinter.Button", MockTkWidget), \
             unittest.mock.patch("tkinter.Entry", MockTkWidget), \
             unittest.mock.patch("tkinter.Canvas", MockTkWidget), \
             unittest.mock.patch("tkinter.LabelFrame", MockTkWidget), \
             unittest.mock.patch("tkinter.ttk.Scrollbar", MockTkWidget), \
             unittest.mock.patch("tkinter.ttk.Combobox", MockTkWidget):
            fake_root = MockTkWidget()
            widget = desktop_widget_always_on_top.RightCornerDesktopWidget(fake_root)
            return widget
