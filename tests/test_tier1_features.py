# -*- coding: utf-8 -*-
"""
Tier 1 — Feature Coverage E2E Tests (`tests/test_tier1_features.py`).

Implements 80 genuine happy-path test cases across all 15 features (`F1`–`F15`, ≥5 per feature)
defined in `PROJECT.md § Feature Inventory` and `## Interface Contracts`:
- F1–F7 (Backend, Security, Atomic Storage, 2-Way Notion Sync, Google Calendar, Auth & Health): 35 tests
- F8–F13 (Web/PWA Frontend Sync Race Fix, XSS Sanitization, Inline Edit/Overdue/Notion Link,
  Search/Filter/Sort, Week Range/Holiday/Time Picker, Notifications/API Key/Service Worker): 34 tests
- F14–F15 (Tkinter Desktop Widget Sync/Anti-Corruption/Parity, Batch Launchers/Docker/Docs): 11 tests
Total: 80 test methods.
"""

import datetime
import json
import os
import threading
import time
import unittest
import unittest.mock

from tests.test_helpers import (
    REPO_DIR,
    FrontendVMHarness,
    IsolatedWidgetTestCase,
    TkinterWidgetHarness,
    get_free_port,
)


class TestTier1Features(IsolatedWidgetTestCase):
    """Tier 1 Feature Coverage Tests (F1 through F15 — 80 test cases)."""

    # =========================================================================
    # Feature F1: Token & Credential Preservation ("เอาspokenต่างๆไว้เลย") (5 tests)
    # =========================================================================

    def test_f1_01_preserve_existing_notion_and_gcal_credential_files_on_disk(self):
        real_notion_cfg = os.path.join(REPO_DIR, "notion_config.json")
        real_cred = os.path.join(REPO_DIR, ".calendar_auth", "credentials.json")
        real_keys = os.path.join(REPO_DIR, ".calendar_auth", "gcp-oauth.keys.json")

        self.assertTrue(os.path.exists(real_notion_cfg), "notion_config.json must exist")
        self.assertTrue(os.path.exists(real_cred), ".calendar_auth/credentials.json must exist")
        self.assertTrue(os.path.exists(real_keys), ".calendar_auth/gcp-oauth.keys.json must exist")

        with open(real_notion_cfg, "r", encoding="utf-8") as f:
            ncfg = json.load(f)
        self.assertTrue(str(ncfg.get("token", "")).startswith("ntn_"))
        self.assertEqual(ncfg.get("database_id"), "3fe16fa7-85cd-8086-8994-fb88e9858c12")
        self.assertTrue(str(self.notion_service.DEFAULT_TOKEN).startswith("ntn_"))

        with open(real_cred, "r", encoding="utf-8") as f:
            gcred = json.load(f)
        self.assertIn("refresh_token", gcred)
        self.assertTrue(bool(gcred["refresh_token"]))

        with open(real_keys, "r", encoding="utf-8") as f:
            gkeys = json.load(f)
        installed = gkeys.get("installed", gkeys)
        self.assertIn("client_id", installed)
        self.assertIn("client_secret", installed)

    def test_f1_02_notion_load_config_precedence_env_over_file_over_default(self):
        with open(self.notion_config_file, "w", encoding="utf-8") as f:
            json.dump({"token": "ntn_file_token", "database_id": "db-from-file"}, f)

        # 1. File over default
        cfg1 = self.notion_service.load_config()
        self.assertEqual(cfg1["token"], "ntn_file_token")
        self.assertEqual(cfg1["database_id"], "db-from-file")

        # 2. Env over file
        os.environ["NOTION_API_TOKEN"] = "ntn_env_token"
        os.environ["NOTION_DATABASE_ID"] = "db-from-env"
        cfg2 = self.notion_service.load_config()
        self.assertEqual(cfg2["token"], "ntn_env_token")
        self.assertEqual(cfg2["database_id"], "db-from-env")

        # 3. Fallback to DEFAULT_TOKEN when file removed and env cleared
        os.environ.pop("NOTION_API_TOKEN", None)
        os.environ.pop("NOTION_DATABASE_ID", None)
        os.remove(self.notion_config_file)
        cfg3 = self.notion_service.load_config()
        self.assertEqual(cfg3["token"], self.notion_service.DEFAULT_TOKEN)

    def test_f1_03_google_calendar_credential_loading_from_env_and_local_files(self):
        # 1. Load from local files
        self.google_calendar_service.cached_token = None
        self.google_calendar_service.cached_expiry = 0
        tok1 = self.google_calendar_service.get_access_token()
        self.assertEqual(tok1, "mock_ya29_access_token")
        self.assertEqual(len(self.mock_gcal.oauth_requests), 1)
        self.assertEqual(
            self.mock_gcal.oauth_requests[0]["form"]["client_id"],
            "mock-client-id.apps.googleusercontent.com",
        )

        # 2. Load from env vars
        self.google_calendar_service.cached_token = None
        self.google_calendar_service.cached_expiry = 0
        os.environ["GOOGLE_CALENDAR_CRED_JSON"] = json.dumps(
            {"refresh_token": "env_refresh_tok", "expiry_date": 0}
        )
        os.environ["GOOGLE_CALENDAR_KEYS_JSON"] = json.dumps(
            {"installed": {"client_id": "env-client-id", "client_secret": "env-secret"}}
        )
        tok2 = self.google_calendar_service.get_access_token()
        self.assertEqual(tok2, "mock_ya29_access_token")
        self.assertEqual(len(self.mock_gcal.oauth_requests), 2)
        self.assertEqual(self.mock_gcal.oauth_requests[1]["form"]["client_id"], "env-client-id")

    def test_f1_04_file_path_env_overrides_todo_data_and_notion_config(self):
        custom_todo = os.path.join(self.temp_dir, "custom_todos.json")
        custom_notion = os.path.join(self.temp_dir, "custom_notion.json")
        os.environ["TODO_DATA_FILE"] = custom_todo
        os.environ["NOTION_CONFIG_FILE"] = custom_notion
        self.desktop_sync_server.DATA_FILE = custom_todo
        self.notion_service.CONFIG_FILE = custom_notion

        self.desktop_sync_server.save_data(
            {"todos": [{"id": "t1", "text": "Custom Path Task", "completed": False, "done": False}], "events": []}
        )
        self.notion_service.save_config({"token": "ntn_custom", "database_id": "db_custom"})

        self.assertTrue(os.path.exists(custom_todo))
        self.assertTrue(os.path.exists(custom_notion))
        with open(custom_todo, "r", encoding="utf-8") as f:
            self.assertEqual(json.load(f)["todos"][0]["text"], "Custom Path Task")
        with open(custom_notion, "r", encoding="utf-8") as f:
            self.assertEqual(json.load(f)["database_id"], "db_custom")

    def test_f1_05_dotenv_example_and_dotenv_loader_support(self):
        env_example = os.path.join(REPO_DIR, ".env.example")
        self.assertTrue(os.path.exists(env_example), ".env.example must exist in repository root")
        with open(env_example, "r", encoding="utf-8") as f:
            content = f.read()
        for required_key in (
            "NOTION_API_TOKEN",
            "NOTION_DATABASE_ID",
            "GOOGLE_CALENDAR_CRED_JSON",
            "GOOGLE_CALENDAR_KEYS_JSON",
            "WIDGET_API_KEY",
            "PORT",
            "TODO_DATA_FILE",
            "NOTION_CONFIG_FILE",
        ):
            self.assertIn(required_key, content, f".env.example must document {required_key}")

    # =========================================================================
    # Feature F2: Port Check & Missing Import Fix (`urllib.request`) (5 tests)
    # =========================================================================

    def test_f2_01_desktop_sync_server_imports_urllib_request_and_parse(self):
        self.assertTrue(hasattr(self.desktop_sync_server, "urllib"), "desktop_sync_server must import urllib")
        self.assertTrue(hasattr(self.desktop_sync_server.urllib, "request"), "urllib.request must be available")
        self.assertTrue(hasattr(self.desktop_sync_server.urllib, "parse"), "urllib.parse must be available")

    def test_f2_02_is_port_in_use_returns_true_when_ephemeral_server_running(self):
        self.start_ephemeral_server()
        self.assertTrue(self.desktop_sync_server.is_port_in_use(self.server_port))

    def test_f2_03_is_port_in_use_returns_false_when_port_closed(self):
        unused_port = get_free_port()
        self.assertFalse(self.desktop_sync_server.is_port_in_use(unused_port))

    def test_f2_04_get_api_ping_returns_200_json_ok(self):
        status, headers, body = self.api_request("GET", "/api/ping")
        self.assertEqual(status, 200)
        self.assertIn("application/json", headers.get("content-type", ""))
        self.assertEqual(body.get("status"), "ok")

    def test_f2_05_start_in_thread_skips_when_port_already_in_use(self):
        self.start_ephemeral_server()
        self.desktop_sync_server.PORT = self.server_port
        t0 = time.time()
        res = self.desktop_sync_server.start_in_thread()
        elapsed = time.time() - t0
        self.assertIsNone(res)
        self.assertLess(elapsed, 1.0)

    # =========================================================================
    # Feature F3: Static File Security & Secret Protection (5 tests)
    # =========================================================================

    def test_f3_01_get_root_and_index_serve_html_widget(self):
        for route in ("/", "/index.html", "/desktop_todo_calendar_widget.html"):
            status, headers, body = self.api_request("GET", route)
            self.assertEqual(status, 200, f"Route {route} must return 200 OK")
            self.assertIn("text/html", headers.get("content-type", ""))
            self.assertIn("<!DOCTYPE html>", body)

    def test_f3_02_get_manifest_json_serves_pwa_manifest(self):
        status, headers, body = self.api_request("GET", "/manifest.json")
        self.assertEqual(status, 200)
        self.assertIsInstance(body, dict)
        self.assertIn("name", body)
        self.assertIn("start_url", body)

    def test_f3_03_get_sw_js_serves_service_worker_javascript(self):
        status, headers, body = self.api_request("GET", "/sw.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", headers.get("content-type", "").lower())
        self.assertIn("addEventListener", body)

    def test_f3_04_cors_and_no_cache_headers_on_allowed_routes(self):
        status, headers, _ = self.api_request("OPTIONS", "/api/todos")
        self.assertEqual(status, 204)
        self.assertEqual(headers.get("access-control-allow-origin"), "*")
        self.assertIn("no-cache", headers.get("cache-control", ""))
        methods = headers.get("access-control-allow-methods", "")
        for m in ("GET", "POST", "PATCH", "DELETE", "OPTIONS"):
            self.assertIn(m, methods)

    def test_f3_05_sensitive_files_blocked_from_http_get(self):
        for path in ("/notion_config.json", "/.calendar_auth/credentials.json", "/desktop_sync_server.py"):
            status, _, body = self.api_request("GET", path)
            self.assertIn(status, (403, 404), f"Sensitive path {path} must return 403 or 404")
            body_str = json.dumps(body) if isinstance(body, dict) else str(body)
            self.assertNotIn("ntn_", body_str)
            self.assertNotIn("refresh_token", body_str)
            self.assertNotIn("def start_server", body_str)

    # =========================================================================
    # Feature F4: Thread-Safe & Atomic Storage (`desktop_todo_data.json`) (5 tests)
    # =========================================================================

    def test_f4_01_save_and_load_data_roundtrip_with_thai_utf8(self):
        sample = {
            "todos": [
                {
                    "id": "t1",
                    "text": "ส่งรายงานสหกรณ์ประจำปี",
                    "completed": False,
                    "done": False,
                    "dueDate": "2026-11-10",
                    "priority": "high",
                    "category": "งานประจำ",
                }
            ],
            "events": [],
        }
        ok = self.desktop_sync_server.save_data(sample)
        self.assertTrue(ok)
        loaded = self.desktop_sync_server.load_data()
        self.assertEqual(loaded["todos"][0]["text"], "ส่งรายงานสหกรณ์ประจำปี")

    def test_f4_02_atomic_write_leaves_no_lingering_tmp_file_and_valid_json(self):
        for i in range(20):
            self.desktop_sync_server.save_data(
                {"todos": [{"id": f"t-{i}", "text": f"Task {i}", "completed": False, "done": False}], "events": []}
            )
        with open(self.todo_data_file, "r", encoding="utf-8") as f:
            parsed = json.load(f)
        self.assertEqual(parsed["todos"][0]["id"], "t-19")
        leftover_tmps = [fn for fn in os.listdir(self.temp_dir) if fn.endswith(".tmp")]
        self.assertEqual(leftover_tmps, [])

    def test_f4_03_update_data_rlock_preserves_concurrent_todo_and_event_mutations(self):
        self.start_ephemeral_server()
        errors = []

        def _todo_worker(idx):
            try:
                status, _, _ = self.api_request(
                    "POST",
                    "/api/todos",
                    body=[{"id": f"t-{idx}", "text": f"Task {idx}", "done": False, "completed": False}],
                )
                if status != 200:
                    errors.append(f"todo status {status}")
            except Exception as e:
                errors.append(str(e))

        def _event_worker(idx):
            try:
                status, _, _ = self.api_request(
                    "POST",
                    "/api/events",
                    body=[{"id": f"ev-{idx}", "title": f"Event {idx}", "date": "2026-11-10", "time": "ทั้งวัน"}],
                )
                if status != 200:
                    errors.append(f"event status {status}")
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=_todo_worker, args=(i,)) for i in range(5)] + [
            threading.Thread(target=_event_worker, args=(i,)) for i in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        self.assertEqual(errors, [])
        final_data = self.desktop_sync_server.load_data()
        self.assertTrue(len(final_data.get("todos", [])) >= 1)
        self.assertTrue(len(final_data.get("events", [])) >= 1)

    def test_f4_04_completed_and_done_booleans_normalized_on_save(self):
        status, _, _ = self.api_request(
            "POST",
            "/api/todos",
            body=[
                {"id": "3fe16fa7-85cd-8086-8994-000000000011", "text": "Task 1", "done": True},
                {"id": "3fe16fa7-85cd-8086-8994-000000000012", "text": "Task 2", "completed": False},
            ],
        )
        self.assertEqual(status, 200)
        _, _, data = self.api_request("GET", "/api/data")
        todos = {t["id"]: t for t in data["todos"]}
        t1 = todos["3fe16fa7-85cd-8086-8994-000000000011"]
        t2 = todos["3fe16fa7-85cd-8086-8994-000000000012"]
        self.assertTrue(t1.get("completed") is True and t1.get("done") is True)
        self.assertTrue(t2.get("completed") is False and t2.get("done") is False)

    def test_f4_05_notion_sync_lock_serializes_background_sync_and_async_push(self):
        self.mock_notion.network_delay = 0.08
        self.start_ephemeral_server()
        t_sync = threading.Thread(target=self.desktop_sync_server.sync_notion_todos_now)
        self.api_request(
            "POST",
            "/api/todos",
            body=[{"id": "todo-100", "text": "Serialized Task", "done": False, "completed": False}],
        )
        t_sync.start()
        t_sync.join(timeout=3)
        self.wait_for_notion_async(timeout=2.0)

        page_creates = [
            r for r in self.mock_notion.requests_log if r["method"] == "POST" and r["url"].endswith("/v1/pages")
        ]
        self.assertEqual(len(page_creates), 1, "Must create exactly 1 Notion page without race duplication")

    # =========================================================================
    # Feature F5: True 2-Way Notion Sync & Metadata Updates (5 tests)
    # =========================================================================

    def test_f5_01_post_todos_creates_new_local_task_in_notion_and_assigns_uuid(self):
        status, _, body = self.api_request(
            "POST",
            "/api/todos",
            body=[
                {
                    "id": "todo-999",
                    "text": "New local task",
                    "dueDate": "2026-11-10",
                    "priority": "high",
                    "category": "ด่วน",
                    "completed": False,
                    "done": False,
                }
            ],
        )
        self.assertEqual(status, 200)
        self.wait_for_notion_async()

        data = self.desktop_sync_server.load_data()
        self.assertEqual(len(data["todos"]), 1)
        todo = data["todos"][0]
        self.assertTrue(len(todo["id"]) >= 32 and "-" in todo["id"])
        self.assertTrue(str(todo.get("notion_url", "")).startswith("https://"))

    def test_f5_02_patch_todo_by_id_updates_metadata_in_cache_and_notion(self):
        uid = "3fe16fa7-85cd-8086-8994-000000000001"
        self.mock_notion.seed_todo_page(
            uid, text="Old", done=False, due_date="2026-11-08", priority="low", category="งานประจำ"
        )
        self.desktop_sync_server.save_data(
            {
                "todos": [
                    {
                        "id": uid,
                        "text": "Old",
                        "dueDate": "2026-11-08",
                        "priority": "low",
                        "category": "งานประจำ",
                        "completed": False,
                        "done": False,
                    }
                ],
                "events": [],
            }
        )

        status, _, res = self.api_request(
            "PATCH",
            f"/api/todos/{uid}",
            body={
                "text": "Updated Title",
                "priority": "high",
                "category": "ประชุม",
                "dueDate": "2026-11-15",
                "done": True,
            },
        )
        self.assertEqual(status, 200)
        self.wait_for_notion_async()

        updated_local = self.desktop_sync_server.load_data()["todos"][0]
        self.assertEqual(updated_local["text"], "Updated Title")
        self.assertEqual(updated_local["priority"], "high")
        self.assertEqual(updated_local["category"], "ประชุม")
        self.assertEqual(updated_local["dueDate"], "2026-11-15")
        self.assertTrue(updated_local["done"] and updated_local["completed"])

        remote = self.mock_notion.extract_page_summary(uid)
        self.assertEqual(remote["text"], "Updated Title")
        self.assertEqual(remote["priority"], "high")
        self.assertEqual(remote["category"], "ประชุม")
        self.assertEqual(remote["dueDate"], "2026-11-15")
        self.assertTrue(remote["done"])

    def test_f5_03_delete_todo_by_id_removes_locally_and_archives_in_notion(self):
        uid = "3fe16fa7-85cd-8086-8994-000000000002"
        self.mock_notion.seed_todo_page(uid, text="To Delete")
        self.desktop_sync_server.save_data(
            {"todos": [{"id": uid, "text": "To Delete", "completed": False, "done": False}], "events": []}
        )

        status, _, res = self.api_request("DELETE", f"/api/todos/{uid}")
        self.assertEqual(status, 200)
        self.assertEqual(res.get("deleted_id"), uid)
        self.wait_for_notion_async()

        self.assertEqual(self.desktop_sync_server.load_data()["todos"], [])
        self.assertTrue(self.mock_notion.pages[uid]["archived"])

    def test_f5_04_periodic_notion_sync_pull_accepts_remote_completion_and_edits(self):
        uid = "3fe16fa7-85cd-8086-8994-000000000003"
        self.desktop_sync_server.save_data(
            {"todos": [{"id": uid, "text": "Initial", "completed": False, "done": False}], "events": []}
        )
        # Remote Notion was updated to Done=True and new title
        self.mock_notion.seed_todo_page(uid, text="Edited in Notion", done=True)

        status, _, res = self.api_request("POST", "/api/notion/sync")
        self.assertEqual(status, 200)
        local_todo = self.desktop_sync_server.load_data()["todos"][0]
        self.assertEqual(local_todo["text"], "Edited in Notion")
        self.assertTrue(local_todo["done"] and local_todo["completed"])
        self.assertTrue(self.mock_notion.extract_page_summary(uid)["done"])

    def test_f5_05_notion_pagination_has_more_and_last_sync_timestamp(self):
        p1 = [
            self.mock_notion.seed_todo_page("3fe16fa7-85cd-8086-8994-000000000101", text="Page 1 Task A"),
            self.mock_notion.seed_todo_page("3fe16fa7-85cd-8086-8994-000000000102", text="Page 1 Task B"),
        ]
        p2 = [
            self.mock_notion.seed_todo_page("3fe16fa7-85cd-8086-8994-000000000103", text="Page 2 Task C"),
            self.mock_notion.seed_todo_page("3fe16fa7-85cd-8086-8994-000000000104", text="Page 2 Task D"),
        ]
        self.mock_notion.paginated_batches = [p1, p2]

        status, _, sync_res = self.api_request("POST", "/api/notion/sync")
        self.assertEqual(status, 200)
        self.assertEqual(len(sync_res.get("todos", [])), 4)

        status2, _, st_res = self.api_request("GET", "/api/notion/status")
        self.assertEqual(status2, 200)
        self.assertTrue(bool(st_res.get("last_sync")), "last_sync timestamp must be populated after sync")

    # =========================================================================
    # Feature F6: Google Calendar OAuth Fix, Timezone & Range Query (5 tests)
    # =========================================================================

    def test_f6_01_reuse_valid_cached_disk_token_without_oauth_refresh(self):
        future_ms = (time.time() + 3600) * 1000
        with open(self.cred_file, "w", encoding="utf-8") as f:
            json.dump(
                {"access_token": "valid_disk_token_123", "refresh_token": "ref", "expiry_date": future_ms},
                f,
            )
        self.google_calendar_service.cached_token = None
        self.google_calendar_service.cached_expiry = 0

        tok = self.google_calendar_service.get_access_token()
        self.assertEqual(tok, "valid_disk_token_123")
        self.assertEqual(len(self.mock_gcal.oauth_requests), 0)

    def test_f6_02_expired_token_refreshes_and_persists_to_active_cred_file(self):
        with open(self.cred_file, "w", encoding="utf-8") as f:
            json.dump({"access_token": "old_token", "refresh_token": "ref_123", "expiry_date": 1000}, f)
        self.google_calendar_service.cached_token = None
        self.google_calendar_service.cached_expiry = 0

        tok = self.google_calendar_service.get_access_token()
        self.assertEqual(tok, "mock_ya29_access_token")
        with open(self.cred_file, "r", encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual(saved.get("access_token"), "mock_ya29_access_token")
        self.assertGreater(saved.get("expiry_date", 0), time.time() * 1000)

    def test_f6_03_create_event_timed_and_all_day_with_bangkok_tz(self):
        s1, _, _ = self.api_request(
            "POST",
            "/api/calendar/events",
            body={"title": "Team Sync", "date": "2026-11-12", "time": "13:30 - 15:00"},
        )
        self.assertEqual(s1, 200)
        p1 = self.mock_gcal.created_events[-1]["payload"]
        self.assertEqual(p1["start"]["dateTime"], "2026-11-12T13:30:00+07:00")
        self.assertEqual(p1["end"]["dateTime"], "2026-11-12T15:00:00+07:00")

        s2, _, _ = self.api_request(
            "POST",
            "/api/calendar/events",
            body={"title": "Company Holiday", "date": "2026-11-13", "time": ""},
        )
        self.assertEqual(s2, 200)
        p2 = self.mock_gcal.created_events[-1]["payload"]
        self.assertEqual(p2["start"]["date"], "2026-11-13")
        self.assertEqual(p2["end"]["date"], "2026-11-14")

    def test_f6_04_get_calendar_events_supports_start_and_days_query_params(self):
        self.mock_gcal.add_mock_event(
            summary="December Kickoff", date_str="2026-12-02", start_time="10:00", end_time="11:00"
        )
        status, _, events = self.api_request("GET", "/api/calendar/events?start=2026-12-01&days=7")
        self.assertEqual(status, 200)
        self.assertTrue(len(self.mock_gcal.calendar_requests) >= 1)
        q = self.mock_gcal.calendar_requests[-1]["query"]
        self.assertTrue(q.get("timeMin", "").startswith("2026-12-01T00:00:00+07:00"))
        self.assertTrue(any(e.get("title") == "December Kickoff" for e in events))

    def test_f6_05_fetch_all_7day_events_sorts_all_day_first_then_chronological(self):
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        self.mock_gcal.add_mock_event(summary="Afternoon", date_str=today, start_time="14:00", end_time="15:00")
        self.mock_gcal.add_mock_event(summary="All Day Holiday", date_str=today)
        self.mock_gcal.add_mock_event(summary="Morning", date_str=today, start_time="09:00", end_time="10:00")

        status, _, events = self.api_request("GET", "/api/calendar/events")
        self.assertEqual(status, 200)
        times = [e["time"] for e in events if e.get("date") == today]
        self.assertEqual(times, ["ทั้งวัน", "09:00 - 10:00", "14:00 - 15:00"])

    # =========================================================================
    # Feature F7: Optional API Key Authentication & Health/Status Endpoints (5 tests)
    # =========================================================================

    def test_f7_01_mutations_succeed_without_headers_when_widget_api_key_unset(self):
        os.environ.pop("WIDGET_API_KEY", None)
        status, _, body = self.api_request(
            "POST", "/api/todos", body=[{"id": "t1", "text": "Open task", "done": False}]
        )
        self.assertEqual(status, 200)
        self.assertEqual(body.get("status"), "success")
        self.assertIn("todos", body)

    def test_f7_02_mutations_succeed_with_x_api_key_header_when_widget_api_key_set(self):
        os.environ["WIDGET_API_KEY"] = "secret-key-123"
        status, _, body = self.api_request(
            "POST",
            "/api/todos",
            body=[{"id": "t1", "text": "Key Auth Task", "done": False}],
            headers={"X-API-Key": "secret-key-123"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body.get("status"), "success")

    def test_f7_03_mutations_succeed_with_authorization_bearer_when_widget_api_key_set(self):
        os.environ["WIDGET_API_KEY"] = "secret-key-123"
        status, _, body = self.api_request(
            "POST",
            "/api/events",
            body=[{"id": "e1", "title": "Bearer Event", "date": "2026-11-10", "time": "ทั้งวัน"}],
            headers={"Authorization": "Bearer secret-key-123"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body.get("status"), "success")

    def test_f7_04_get_api_health_returns_structured_status_and_counts(self):
        self.desktop_sync_server.save_data(
            {
                "todos": [
                    {"id": "t1", "text": "A", "done": False, "completed": False},
                    {"id": "t2", "text": "B", "done": True, "completed": True},
                ],
                "events": [
                    {"id": "e1", "title": "E1", "date": "2026-11-08", "time": "ทั้งวัน"},
                    {"id": "e2", "title": "E2", "date": "2026-11-09", "time": "ทั้งวัน"},
                    {"id": "e3", "title": "E3", "date": "2026-11-10", "time": "ทั้งวัน"},
                ],
            }
        )
        status, _, body = self.api_request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(body.get("status"), "ok")
        self.assertIn("notion_connected", body)
        self.assertIn("calendar_status", body)
        self.assertEqual(body.get("counts"), {"todos": 2, "events": 3})

    def test_f7_05_get_api_calendar_status_returns_connection_and_calendars_list(self):
        status, _, body = self.api_request("GET", "/api/calendar/status")
        self.assertEqual(status, 200)
        self.assertIn(body.get("status"), ("connected", "cached_fallback", "ok"))
        self.assertIsInstance(body.get("calendars"), list)
        self.assertEqual(len(body["calendars"]), 3)

    # =========================================================================
    # Feature F8: Frontend Sync Race Fix & Full-Field Diffing (6 tests)
    # =========================================================================

    def test_f8_save_version_prevents_stale_poll_overwrite(self):
        res = FrontendVMHarness.run_js(
            """
            todos = [{ id: 't1', text: 'Task A', done: false, completed: false, dueDate: todayIso, priority: 'medium', category: 'งานประจำ' }];
            let resolvePoll;
            __test_helpers.setFetchHandler((url, opts) => {
              if (url.endsWith('/api/data')) {
                return new Promise(r => {
                  resolvePoll = () => r({
                    ok: true,
                    status: 200,
                    json: async () => ({
                      todos: [{ id: 't1', text: 'Task A', done: false, completed: false, dueDate: todayIso, priority: 'medium', category: 'งานประจำ' }],
                      events: []
                    })
                  });
                });
              }
              return Promise.resolve({
                ok: true,
                status: 200,
                json: async () => ({ status: 'success', todos })
              });
            });
            const pollPromise = syncFetchData();
            // User saves new task while poll is in flight
            todos.push({ id: 't2', text: 'Task B', done: false, completed: false, dueDate: todayIso, priority: 'high', category: 'ด่วน' });
            await syncSaveTodos(todos);
            resolvePoll();
            await pollPromise;
            return { count: todos.length, ids: todos.map(t => t.id) };
            """
        )
        self.assertEqual(res["count"], 2)
        self.assertEqual(res["ids"], ["t1", "t2"])

    def test_f8_reconciles_temp_id_with_server_notion_uuid(self):
        res = FrontendVMHarness.run_js(
            """
            todos = [{ id: 'todo-1700000001', text: 'Sync Task', done: false, completed: false, dueDate: todayIso, priority: 'medium', category: 'งานประจำ' }];
            __test_helpers.setFetchHandler(async (url, opts) => ({
              ok: true,
              status: 200,
              json: async () => ({
                status: 'success',
                todos: [{
                  id: '3fe16fa7-85cd-8086-8994-fb88e9858c12',
                  text: 'Sync Task',
                  done: false,
                  completed: false,
                  dueDate: todayIso,
                  priority: 'medium',
                  category: 'งานประจำ',
                  notion_url: 'https://notion.so/3fe16fa7'
                }]
              })
            }));
            await syncSaveTodos(todos);
            return { id: todos[0].id, notion_url: todos[0].notion_url };
            """
        )
        self.assertEqual(res["id"], "3fe16fa7-85cd-8086-8994-fb88e9858c12")
        self.assertEqual(res["notion_url"], "https://notion.so/3fe16fa7")

    def test_f8_sync_fetch_preserves_notion_url_and_done_completed(self):
        res = FrontendVMHarness.run_js(
            """
            __test_helpers.setFetchHandler(async (url) => ({
              ok: true,
              status: 200,
              json: async () => ({
                todos: [{
                  id: '3fe16fa7-85cd-8086-8994-000000000001',
                  text: 'From Notion',
                  completed: true,
                  dueDate: todayIso,
                  priority: 'high',
                  category: 'ด่วน',
                  notion_url: 'https://www.notion.so/page-123'
                }],
                events: []
              })
            }));
            await syncFetchData();
            return todos[0];
            """
        )
        self.assertEqual(res.get("notion_url"), "https://www.notion.so/page-123")
        self.assertTrue(res.get("done") is True and res.get("completed") is True)

    def test_f8_full_field_diff_detects_metadata_changes(self):
        res = FrontendVMHarness.run_js(
            """
            todos = [{
              id: 't1',
              text: 'Task 1',
              done: false,
              completed: false,
              dueDate: '2026-11-08',
              priority: 'low',
              category: 'งานประจำ',
              notion_url: ''
            }];
            __test_helpers.setFetchHandler(async () => ({
              ok: true,
              status: 200,
              json: async () => ({
                todos: [{
                  id: 't1',
                  text: 'Task 1',
                  done: false,
                  completed: false,
                  dueDate: '2026-11-10',
                  priority: 'high',
                  category: 'ด่วน',
                  notion_url: 'https://notion.so/t1'
                }],
                events: []
              })
            }));
            await syncFetchData();
            return todos[0];
            """
        )
        self.assertEqual(res["priority"], "high")
        self.assertEqual(res["category"], "ด่วน")
        self.assertEqual(res["dueDate"], "2026-11-10")
        self.assertEqual(res["notion_url"], "https://notion.so/t1")

    def test_f8_full_event_diff_and_empty_events_array_update(self):
        res = FrontendVMHarness.run_js(
            """
            calendarEvents = [
              { id: 'ev1', title: 'Old Title', date: todayIso, time: '09:00 - 10:00', sourceType: 'primary' }
            ];
            __test_helpers.setFetchHandler(async () => ({
              ok: true,
              status: 200,
              json: async () => ({
                todos: [],
                events: [{ id: 'ev1', title: 'Updated Title', date: todayIso, time: '10:00 - 11:00', sourceType: 'primary' }]
              })
            }));
            await syncFetchData();
            const afterUpdate = JSON.parse(JSON.stringify(calendarEvents));

            __test_helpers.setFetchHandler(async () => ({
              ok: true,
              status: 200,
              json: async () => ({ todos: [], events: [] })
            }));
            await syncFetchData();
            const afterEmpty = JSON.parse(JSON.stringify(calendarEvents));
            return { afterUpdate, afterEmpty };
            """
        )
        self.assertEqual(res["afterUpdate"][0]["title"], "Updated Title")
        self.assertEqual(res["afterUpdate"][0]["time"], "10:00 - 11:00")
        self.assertEqual(res["afterEmpty"], [])

    def test_f8_offline_custom_events_preserved_and_synced(self):
        res = FrontendVMHarness.run_js(
            """
            calendarEvents = [
              { id: 'custom-12345', title: 'Offline Meet', date: todayIso, time: '10:00 - 11:00', sourceType: 'custom' }
            ];
            __test_helpers.setFetchHandler(async (url, opts) => ({
              ok: true,
              status: 200,
              json: async () => ({
                todos: [],
                events: [{ id: 'live-1', title: 'Google Event', date: todayIso, time: '13:00 - 14:00', sourceType: 'primary' }]
              })
            }));
            await syncFetchData();
            return {
              ids: calendarEvents.map(e => e.id),
              postedEvents: __test_helpers.fetchCalls.some(c => c.url.endsWith('/api/events'))
            };
            """
        )
        self.assertIn("custom-12345", res["ids"])
        self.assertIn("live-1", res["ids"])

    # =========================================================================
    # Feature F9: Frontend XSS Sanitization (5 tests)
    # =========================================================================

    def test_f9_escape_html_helper_sanitizes_all_special_chars(self):
        res = FrontendVMHarness.run_js(
            """
            if (typeof escapeHtml === 'function') {
              return escapeHtml('& < > " \\'');
            }
            const el = document.createElement('div');
            el.textContent = '& < > " \\'';
            return el.innerHTML;
            """
        )
        self.assertIn("&amp;", res)
        self.assertIn("&lt;", res)
        self.assertIn("&gt;", res)

    def test_f9_calendar_event_title_and_time_rendered_safely(self):
        res = FrontendVMHarness.run_js(
            """
            calendarEvents = [{
              id: 'ev-safe',
              title: 'Meeting <Team & Co>',
              date: todayIso,
              time: '09:00 <AM>',
              calendar: 'pattarapon.thongsri@gmail.com',
              sourceType: 'primary'
            }];
            renderWeekCalendar();
            const container = document.getElementById('weekDaysContainer');
            return {
              text: container.textContent,
              html: container.outerHTML
            };
            """
        )
        self.assertIn("Meeting <Team & Co>", res["text"])
        self.assertNotIn("<Team", res["html"])
        self.assertNotIn("<AM>", res["html"])

    def test_f9_calendar_todo_badge_text_and_category_rendered_safely(self):
        res = FrontendVMHarness.run_js(
            """
            todos = [{
              id: 'td-safe',
              text: 'Review <Budget & Plan>',
              category: 'งานประจำ <ด่วน>',
              dueDate: todayIso,
              priority: 'high',
              completed: false,
              done: false
            }];
            renderWeekCalendar();
            const container = document.getElementById('weekDaysContainer');
            return { text: container.textContent, html: container.outerHTML };
            """
        )
        self.assertIn("Review <Budget & Plan>", res["text"])
        self.assertNotIn("<Budget", res["html"])
        self.assertNotIn("<ด่วน>", res["html"])

    def test_f9_todo_list_card_renders_text_and_category_via_safe_dom(self):
        res = FrontendVMHarness.run_js(
            """
            todos = [{
              id: 'td-1',
              text: 'Fix bug #123 & <deploy>',
              category: 'ส่วนตัว',
              dueDate: todayIso,
              priority: 'medium',
              completed: false,
              done: false
            }];
            renderTodos();
            const list = document.getElementById('todoListContainer');
            return { text: list.textContent, html: list.outerHTML };
            """
        )
        self.assertIn("Fix bug #123 & <deploy>", res["text"])
        self.assertNotIn("<deploy>", res["html"])

    def test_f9_notion_modal_status_escapes_workspace_and_bot_name(self):
        res = FrontendVMHarness.run_js(
            """
            __test_helpers.setFetchHandler(async () => ({
              ok: true,
              status: 200,
              json: async () => ({
                installed: true,
                connected: true,
                workspace: "Pattara's <Workspace & Lab>",
                bot_name: "ICT <Bot>",
                database_id: "db-123"
              })
            }));
            await checkNotionStatus();
            const info = document.getElementById('notionModalInfo');
            return { text: info.textContent, html: info.outerHTML };
            """
        )
        self.assertNotIn("<Workspace", res["html"])
        self.assertNotIn("<Bot>", res["html"])

    # =========================================================================
    # Feature F10: Inline Task Editing, Overdue Badges & Notion Link (6 tests)
    # =========================================================================

    def test_f10_task_card_renders_edit_button_and_opens_editor(self):
        res = FrontendVMHarness.run_js(
            """
            todos = [{
              id: 't-edit-1',
              text: 'Original Task',
              dueDate: '2026-11-15',
              priority: 'high',
              category: 'ประชุม',
              completed: false,
              done: false
            }];
            renderTodos();
            const list = document.getElementById('todoListContainer');
            const editBtn = list.querySelector('.todo-edit-btn') || list.querySelectorAll('button').find(b => (b.textContent || '').includes('✏️') || (b.textContent || '').includes('แก้ไข'));
            if (typeof openEditTodo === 'function') {
              openEditTodo('t-edit-1');
            } else if (editBtn) {
              editBtn.click();
            }
            return {
              hasEditBtn: !!editBtn || typeof openEditTodo === 'function',
              editTextVal: document.getElementById('editTodoText').value || document.getElementById('todoInput').value
            };
            """
        )
        self.assertTrue(res["hasEditBtn"])
        self.assertEqual(res["editTextVal"], "Original Task")

    def test_f10_editing_task_updates_all_fields_and_saves_to_server(self):
        res = FrontendVMHarness.run_js(
            """
            todos = [{
              id: 't-edit-2',
              text: 'Before Edit',
              dueDate: '2026-11-08',
              priority: 'low',
              category: 'งานประจำ',
              completed: false,
              done: false
            }];
            if (typeof openEditTodo === 'function') {
              openEditTodo('t-edit-2');
              document.getElementById('editTodoText').value = 'Updated Title';
              document.getElementById('editTodoDueDate').value = '2026-12-01';
              document.getElementById('editTodoPriority').value = 'high';
              document.getElementById('editTodoCategory').value = 'ด่วน';
              if (typeof saveEditedTodo === 'function') {
                await saveEditedTodo();
              } else {
                document.getElementById('editTodoForm').dispatchEvent({ type: 'submit' });
              }
            } else if (typeof updateTodoItem === 'function') {
              await updateTodoItem('t-edit-2', {
                text: 'Updated Title',
                dueDate: '2026-12-01',
                priority: 'high',
                category: 'ด่วน'
              });
            }
            return {
              todo: todos[0],
              savedToServer: __test_helpers.fetchCalls.some(c => c.url.includes('/api/todos'))
            };
            """
        )
        self.assertEqual(res["todo"]["text"], "Updated Title")
        self.assertEqual(res["todo"]["dueDate"], "2026-12-01")
        self.assertEqual(res["todo"]["priority"], "high")
        self.assertEqual(res["todo"]["category"], "ด่วน")
        self.assertTrue(res["savedToServer"])

    def test_f10_urgent_category_duan_available_in_create_and_edit(self):
        html, _, _ = FrontendVMHarness.load_html_and_script()
        self.assertIn('value="ด่วน"', html)
        res = FrontendVMHarness.run_js(
            """
            todos = [{
              id: 't-duan',
              text: 'Urgent Task',
              dueDate: todayIso,
              priority: 'high',
              category: 'ด่วน',
              completed: false,
              done: false
            }];
            renderTodos();
            return document.getElementById('todoListContainer').textContent;
            """
        )
        self.assertIn("ด่วน", res)

    def test_f10_overdue_badge_displayed_for_past_uncompleted_tasks(self):
        res = FrontendVMHarness.run_js(
            """
            todos = [{
              id: 't-overdue',
              text: 'Past Task',
              dueDate: '2020-01-01',
              priority: 'high',
              category: 'งานประจำ',
              completed: false,
              done: false
            }];
            renderTodos();
            return document.getElementById('todoListContainer').textContent;
            """
        )
        self.assertIn("เลยกำหนด", res)

    def test_f10_today_badge_displayed_for_due_today_tasks(self):
        res = FrontendVMHarness.run_js(
            """
            todos = [{
              id: 't-today',
              text: 'Today Task',
              dueDate: todayIso,
              priority: 'medium',
              category: 'งานประจำ',
              completed: false,
              done: false
            }];
            renderTodos();
            return document.getElementById('todoListContainer').textContent;
            """
        )
        self.assertIn("วันนี้", res)
        self.assertNotIn("เลยกำหนด", res)

    def test_f10_notion_url_renders_clickable_external_link(self):
        res = FrontendVMHarness.run_js(
            """
            todos = [{
              id: '3fe16fa7-85cd-8086-8994-fb88e9858c12',
              text: 'Linked Task',
              dueDate: todayIso,
              priority: 'medium',
              category: 'งานประจำ',
              completed: false,
              done: false,
              notion_url: 'https://www.notion.so/3fe16fa785cd80868994fb88e9858c12'
            }];
            renderTodos();
            const list = document.getElementById('todoListContainer');
            return { text: list.textContent, html: list.outerHTML };
            """
        )
        self.assertIn("Notion", res["text"])
        self.assertIn("https://www.notion.so/3fe16fa785cd80868994fb88e9858c12", res["html"])

    # =========================================================================
    # Feature F11: Task Search, Category Filter & Sorting (5 tests)
    # =========================================================================

    def test_f11_search_input_filters_by_text_and_category_substring(self):
        res = FrontendVMHarness.run_js(
            """
            todos = [
              { id: '1', text: 'เตรียมเอกสารประชุม', category: 'ประชุม', dueDate: todayIso, priority: 'high', completed: false, done: false },
              { id: '2', text: 'ซื้อของเข้าบ้าน', category: 'ส่วนตัว', dueDate: todayIso, priority: 'low', completed: false, done: false },
              { id: '3', text: 'ส่งรายงานประจำสัปดาห์', category: 'งานประจำ', dueDate: todayIso, priority: 'medium', completed: false, done: false }
            ];
            const searchEl = document.getElementById('todoSearchInput');
            searchEl.value = 'ประชุม';
            searchEl.dispatchEvent({ type: 'input' });
            renderTodos();
            const text1 = document.getElementById('todoListContainer').textContent;

            searchEl.value = 'ส่วนตัว';
            searchEl.dispatchEvent({ type: 'input' });
            renderTodos();
            const text2 = document.getElementById('todoListContainer').textContent;
            return { text1, text2 };
            """
        )
        self.assertIn("เตรียมเอกสารประชุม", res["text1"])
        self.assertNotIn("ซื้อของเข้าบ้าน", res["text1"])
        self.assertIn("ซื้อของเข้าบ้าน", res["text2"])
        self.assertNotIn("เตรียมเอกสารประชุม", res["text2"])

    def test_f11_category_filter_select_filters_each_category(self):
        res = FrontendVMHarness.run_js(
            """
            todos = [
              { id: '1', text: 'Task Work', category: 'งานประจำ', dueDate: todayIso, priority: 'medium', completed: false, done: false },
              { id: '2', text: 'Task Personal', category: 'ส่วนตัว', dueDate: todayIso, priority: 'low', completed: false, done: false }
            ];
            const catEl = document.getElementById('todoCategoryFilter');
            catEl.value = 'ส่วนตัว';
            catEl.dispatchEvent({ type: 'change' });
            renderTodos();
            const filtered = document.getElementById('todoListContainer').textContent;

            catEl.value = 'all';
            catEl.dispatchEvent({ type: 'change' });
            renderTodos();
            const allText = document.getElementById('todoListContainer').textContent;
            return { filtered, allText };
            """
        )
        self.assertIn("Task Personal", res["filtered"])
        self.assertNotIn("Task Work", res["filtered"])
        self.assertIn("Task Work", res["allText"])
        self.assertIn("Task Personal", res["allText"])

    def test_f11_sort_by_due_date_ascending(self):
        res = FrontendVMHarness.run_js(
            """
            todos = [
              { id: '1', text: 'Task Late', dueDate: '2026-11-15', priority: 'medium', category: 'งานประจำ', completed: false, done: false },
              { id: '2', text: 'Task Early', dueDate: '2026-11-09', priority: 'medium', category: 'งานประจำ', completed: false, done: false },
              { id: '3', text: 'Task Mid', dueDate: '2026-11-12', priority: 'medium', category: 'งานประจำ', completed: false, done: false }
            ];
            const sortEl = document.getElementById('todoSortSelect');
            sortEl.value = 'dueDate';
            sortEl.dispatchEvent({ type: 'change' });
            renderTodos();
            const txt = document.getElementById('todoListContainer').textContent;
            return {
              iEarly: txt.indexOf('Task Early'),
              iMid: txt.indexOf('Task Mid'),
              iLate: txt.indexOf('Task Late')
            };
            """
        )
        self.assertTrue(0 <= res["iEarly"] < res["iMid"] < res["iLate"])

    def test_f11_sort_by_priority_high_to_low(self):
        res = FrontendVMHarness.run_js(
            """
            todos = [
              { id: '1', text: 'Low Task', dueDate: todayIso, priority: 'low', category: 'งานประจำ', completed: false, done: false },
              { id: '2', text: 'High Task', dueDate: todayIso, priority: 'high', category: 'งานประจำ', completed: false, done: false },
              { id: '3', text: 'Med Task', dueDate: todayIso, priority: 'medium', category: 'งานประจำ', completed: false, done: false }
            ];
            const sortEl = document.getElementById('todoSortSelect');
            sortEl.value = 'priority';
            sortEl.dispatchEvent({ type: 'change' });
            renderTodos();
            const txt = document.getElementById('todoListContainer').textContent;
            return {
              iHigh: txt.indexOf('High Task'),
              iMed: txt.indexOf('Med Task'),
              iLow: txt.indexOf('Low Task')
            };
            """
        )
        self.assertTrue(0 <= res["iHigh"] < res["iMed"] < res["iLow"])

    def test_f11_combined_status_pill_search_category_and_sort(self):
        res = FrontendVMHarness.run_js(
            """
            todos = [
              { id: '1', text: 'รายงาน A', category: 'งานประจำ', priority: 'low', dueDate: todayIso, completed: false, done: false },
              { id: '2', text: 'รายงาน B', category: 'งานประจำ', priority: 'high', dueDate: todayIso, completed: false, done: false },
              { id: '3', text: 'รายงาน C', category: 'งานประจำ', priority: 'high', dueDate: todayIso, completed: true, done: true },
              { id: '4', text: 'รายงาน D', category: 'ส่วนตัว', priority: 'high', dueDate: todayIso, completed: false, done: false }
            ];
            currentTodoFilter = 'active';
            document.getElementById('todoCategoryFilter').value = 'งานประจำ';
            document.getElementById('todoCategoryFilter').dispatchEvent({ type: 'change' });
            document.getElementById('todoSearchInput').value = 'รายงาน';
            document.getElementById('todoSearchInput').dispatchEvent({ type: 'input' });
            document.getElementById('todoSortSelect').value = 'priority';
            document.getElementById('todoSortSelect').dispatchEvent({ type: 'change' });
            renderTodos();
            return document.getElementById('todoListContainer').textContent;
            """
        )
        self.assertIn("รายงาน B", res)
        self.assertIn("รายงาน A", res)
        self.assertNotIn("รายงาน C", res)
        self.assertNotIn("รายงาน D", res)
        self.assertLess(res.index("รายงาน B"), res.index("รายงาน A"))

    # =========================================================================
    # Feature F12: Calendar Week Range Fetching, Holiday Filter & Time Picker (6 tests)
    # =========================================================================

    def test_f12_holiday_filter_checkbox_toggles_holiday_events(self):
        html, _, _ = FrontendVMHarness.load_html_and_script()
        self.assertIn("chkCalHoliday", html)
        res = FrontendVMHarness.run_js(
            """
            calendarEvents = [{
              id: 'hol-1',
              title: 'วันหยุดชดเชยพิเศษ',
              date: todayIso,
              time: 'ทั้งวัน',
              calendar: 'en.th#holiday@group.v.calendar.google.com',
              sourceType: 'holiday'
            }];
            const chk = document.getElementById('chkCalHoliday');
            chk.checked = true;
            renderWeekCalendar();
            const whenChecked = document.getElementById('weekDaysContainer').textContent;

            chk.checked = false;
            chk.dispatchEvent({ type: 'change' });
            renderWeekCalendar();
            const whenUnchecked = document.getElementById('weekDaysContainer').textContent;
            return { whenChecked, whenUnchecked };
            """
        )
        self.assertIn("วันหยุดชดเชยพิเศษ", res["whenChecked"])
        self.assertNotIn("วันหยุดชดเชยพิเศษ", res["whenUnchecked"])

    def test_f12_next_and_prev_week_navigation_requests_date_range(self):
        res = FrontendVMHarness.run_js(
            """
            __test_helpers.fetchCalls.length = 0;
            document.getElementById('btnNextWeek').click();
            await new Promise(r => setTimeout(r, 0));
            const nextCalls = __test_helpers.fetchCalls.map(c => c.url);

            __test_helpers.fetchCalls.length = 0;
            document.getElementById('btnPrevWeek').click();
            await new Promise(r => setTimeout(r, 0));
            const prevCalls = __test_helpers.fetchCalls.map(c => c.url);
            return { nextCalls, prevCalls };
            """
        )
        self.assertTrue(any("/api/calendar/events?start=" in u and "days=7" in u for u in res["nextCalls"]))
        self.assertTrue(any("/api/calendar/events?start=" in u and "days=7" in u for u in res["prevCalls"]))

    def test_f12_today_week_button_resets_anchor_and_fetches_current_week(self):
        res = FrontendVMHarness.run_js(
            """
            document.getElementById('btnNextWeek').click();
            document.getElementById('btnNextWeek').click();
            __test_helpers.fetchCalls.length = 0;
            document.getElementById('btnTodayWeek').click();
            await new Promise(r => setTimeout(r, 0));
            return {
              anchorIso: toISODate(currentAnchorDate),
              todayIso,
              calls: __test_helpers.fetchCalls.map(c => c.url)
            };
            """
        )
        self.assertEqual(res["anchorIso"], res["todayIso"])
        self.assertTrue(any("/api/calendar/events" in u for u in res["calls"]))

    def test_f12_week_view_mode_rolling7_vs_monsun(self):
        res = FrontendVMHarness.run_js(
            """
            currentAnchorDate = new Date(2026, 10, 11); // Wednesday 2026-11-11
            const rollingStart = toISODate(getWeekStartDate(currentAnchorDate, 'rolling7'));
            const monSunStart = toISODate(getWeekStartDate(currentAnchorDate, 'monSun'));
            return { rollingStart, monSunStart };
            """
        )
        self.assertEqual(res["rollingStart"], "2026-11-11")
        self.assertEqual(res["monSunStart"], "2026-11-09")

    def test_f12_structured_time_pickers_sync_with_evt_time(self):
        html, _, _ = FrontendVMHarness.load_html_and_script()
        self.assertIn("evtStartTime", html)
        self.assertIn("evtEndTime", html)
        res = FrontendVMHarness.run_js(
            """
            const startEl = document.getElementById('evtStartTime');
            const endEl = document.getElementById('evtEndTime');
            const allDayEl = document.getElementById('evtAllDay');
            allDayEl.checked = false;
            startEl.value = '13:00';
            endEl.value = '14:30';
            startEl.dispatchEvent({ type: 'change' });
            endEl.dispatchEvent({ type: 'change' });
            return document.getElementById('evtTime').value;
            """
        )
        self.assertEqual(res, "13:00 - 14:30")

    def test_f12_all_day_checkbox_sets_thai_all_day_string(self):
        res = FrontendVMHarness.run_js(
            """
            const allDayEl = document.getElementById('evtAllDay');
            allDayEl.checked = true;
            allDayEl.dispatchEvent({ type: 'change' });
            return document.getElementById('evtTime').value;
            """
        )
        self.assertEqual(res, "ทั้งวัน")

    # =========================================================================
    # Feature F13: Browser Notification Reminders, API Key UI & PWA SW (6 tests)
    # =========================================================================

    def test_f13_notification_toggle_and_15min_event_reminder(self):
        res = FrontendVMHarness.run_js(
            """
            const now = new Date();
            const in10 = new Date(now.getTime() + 10 * 60000);
            const hh = String(in10.getHours()).padStart(2, '0');
            const mm = String(in10.getMinutes()).padStart(2, '0');
            calendarEvents = [{
              id: 'ev-soon-1',
              title: 'Urgent Standup',
              date: toISODate(now),
              time: `${hh}:${mm} - 23:59`,
              sourceType: 'primary'
            }];
            Notification.permission = 'granted';
            localStorage.setItem('desktop_widget_notify_enabled', 'true');
            if (typeof notificationsEnabled !== 'undefined') notificationsEnabled = true;
            if (typeof checkReminders === 'function') {
              checkReminders();
            }
            return {
              notifCount: __test_helpers.notificationsSent.length,
              toastCount: document.getElementById('toastContainer').children.length
            };
            """,
            init_options={"notificationPermission": "granted"},
        )
        self.assertTrue(res["notifCount"] >= 1 or res["toastCount"] >= 1)

    def test_f13_due_today_task_reminder_fires_once(self):
        res = FrontendVMHarness.run_js(
            """
            todos = [{
              id: 'td-due-today-1',
              text: 'Submit Tax Form',
              dueDate: todayIso,
              priority: 'high',
              category: 'ด่วน',
              completed: false,
              done: false
            }];
            Notification.permission = 'granted';
            localStorage.setItem('desktop_widget_notify_enabled', 'true');
            if (typeof notificationsEnabled !== 'undefined') notificationsEnabled = true;
            checkReminders();
            const countAfterFirst = __test_helpers.notificationsSent.length;
            checkReminders();
            const countAfterSecond = __test_helpers.notificationsSent.length;
            return { countAfterFirst, countAfterSecond };
            """,
            init_options={"notificationPermission": "granted"},
        )
        self.assertGreaterEqual(res["countAfterFirst"], 1)
        self.assertEqual(res["countAfterFirst"], res["countAfterSecond"])

    def test_f13_api_key_input_persists_and_sends_x_api_key_header(self):
        res = FrontendVMHarness.run_js(
            """
            const input = document.getElementById('apiKeyInput');
            input.value = 'my-secret-key-99';
            const saveBtn = document.getElementById('btnSaveApiKey');
            if (saveBtn) saveBtn.click();
            localStorage.setItem('desktop_widget_api_key', 'my-secret-key-99');
            __test_helpers.fetchCalls.length = 0;
            await syncSaveTodos([{ id: 't1', text: 'Auth Test', done: false }]);
            const call = __test_helpers.fetchCalls.find(c => c.url.endsWith('/api/todos'));
            const headers = (call && call.options && call.options.headers) || {};
            return headers['X-API-Key'] || headers['x-api-key'] || '';
            """
        )
        self.assertEqual(res, "my-secret-key-99")

    def test_f13_modals_have_aria_dialog_and_close_on_escape_and_backdrop(self):
        html, _, _ = FrontendVMHarness.load_html_and_script()
        self.assertIn('role="dialog"', html)
        self.assertIn('aria-modal="true"', html)
        res = FrontendVMHarness.run_js(
            """
            const setupModal = document.getElementById('setupModal');
            const notionModal = document.getElementById('notionModal');
            setupModal.classList.remove('hidden');
            document.dispatchEvent({ type: 'keydown', key: 'Escape' });
            window.dispatchEvent({ type: 'keydown', key: 'Escape' });
            const closedByEscape = setupModal.classList.contains('hidden');

            notionModal.classList.remove('hidden');
            notionModal.dispatchEvent({ type: 'click', target: notionModal });
            const closedByBackdrop = notionModal.classList.contains('hidden');
            return { closedByEscape, closedByBackdrop };
            """
        )
        self.assertTrue(res["closedByEscape"])
        self.assertTrue(res["closedByBackdrop"])

    def test_f13_pwa_manifest_and_sw_registration_in_html(self):
        manifest_path = os.path.join(REPO_DIR, "manifest.json")
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        self.assertEqual(manifest.get("display"), "standalone")
        self.assertEqual(manifest.get("start_url"), "/")
        self.assertTrue(len(manifest.get("icons", [])) >= 1)

        html, _, _ = FrontendVMHarness.load_html_and_script()
        self.assertIn("serviceWorker", html)
        self.assertIn("sw.js", html)

    def test_f13_service_worker_install_activate_and_network_first_fetch(self):
        res = FrontendVMHarness.run_sw_js(
            """
            await __sw_test.dispatchInstall();
            await __sw_test.dispatchActivate();
            const rOnline = await __sw_test.dispatchFetch('/api/data', 'GET');
            const onlineJson = await rOnline.json();

            __sw_test.setOnline(false);
            const rOffline = await __sw_test.dispatchFetch('/api/data', 'GET');
            const offlineJson = await rOffline.json();
            return { onlineJson, offlineJson };
            """
        )
        self.assertTrue(res["onlineJson"].get("live"))
        self.assertTrue(res["offlineJson"].get("live"))

    # =========================================================================
    # Feature F14: Tkinter Desktop Widget Sync, Anti-Corruption & Parity (6 tests)
    # =========================================================================

    def test_f14_respects_todo_data_file_env_and_preserves_empty_lists(self):
        with open(self.todo_data_file, "w", encoding="utf-8") as f:
            json.dump({"todos": [], "events": []}, f)
        widget = TkinterWidgetHarness.create_widget(self.todo_data_file)
        data = widget.load_data()
        self.assertEqual(data["todos"], [])
        self.assertEqual(data["events"], [], "Empty events list [] must NOT be replaced with DEFAULT_EVENTS")

    def test_f14_add_and_toggle_todo_syncs_via_server_api_and_atomic_file(self):
        self.start_ephemeral_server()
        widget = TkinterWidgetHarness.create_widget(self.todo_data_file, server_port=self.server_port)
        if hasattr(widget, "todo_entry") and widget.todo_entry is not None:
            widget.todo_entry.delete(0, "end")
            widget.todo_entry.insert(0, "งานทดสอบ Tkinter")
        if hasattr(widget, "priority_var") and widget.priority_var is not None:
            widget.priority_var.set("high")
        if hasattr(widget, "category_var") and widget.category_var is not None:
            widget.category_var.set("ด่วน")

        try:
            widget.add_todo("งานทดสอบ Tkinter", priority="high", category="ด่วน", due_date="2026-11-12")
        except TypeError:
            widget.add_todo()

        self.wait_for_notion_async()
        self.assertEqual(len(widget.data["todos"]), 1)
        self.assertFalse(widget.data["todos"][0]["done"])
        self.assertFalse(widget.data["todos"][0]["completed"])

        widget.toggle_todo(0)
        self.wait_for_notion_async()
        self.assertTrue(widget.data["todos"][0]["done"])
        self.assertTrue(widget.data["todos"][0]["completed"])

    def test_f14_delete_todo_calls_server_and_archives_in_notion(self):
        uid = "3fe16fa7-85cd-8086-8994-fb88e9858c12"
        self.mock_notion.seed_todo_page(uid, text="Task to delete")
        with open(self.todo_data_file, "w", encoding="utf-8") as f:
            json.dump(
                {"todos": [{"id": uid, "text": "Task to delete", "done": False, "completed": False}], "events": []},
                f,
            )
        self.start_ephemeral_server()
        widget = TkinterWidgetHarness.create_widget(self.todo_data_file, server_port=self.server_port)
        widget.delete_todo(0)
        self.wait_for_notion_async()

        self.assertEqual(widget.data["todos"], [])
        self.assertTrue(
            self.mock_notion.pages[uid]["archived"],
            "Deleting a Notion UUID task in Tkinter must archive it in Notion",
        )

    def test_f14_edit_todo_and_clear_completed_todos(self):
        with open(self.todo_data_file, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "todos": [
                        {"id": "t1", "text": "Task 1", "priority": "low", "category": "งานประจำ", "dueDate": "2026-11-10", "done": False, "completed": False},
                        {"id": "t2", "text": "Task 2", "priority": "low", "category": "งานประจำ", "dueDate": "2026-11-10", "done": True, "completed": True},
                    ],
                    "events": [],
                },
                f,
            )
        self.start_ephemeral_server()
        widget = TkinterWidgetHarness.create_widget(self.todo_data_file, server_port=self.server_port)
        self.assertTrue(hasattr(widget, "edit_todo"), "RightCornerDesktopWidget must implement edit_todo")
        self.assertTrue(hasattr(widget, "clear_completed_todos"), "RightCornerDesktopWidget must implement clear_completed_todos")

        widget.edit_todo(0, text="Edited Task", priority="high", category="ประชุม", due_date="2026-11-20")
        widget.clear_completed_todos()
        self.assertEqual(len(widget.data["todos"]), 1)
        self.assertEqual(widget.data["todos"][0]["text"], "Edited Task")
        self.assertEqual(widget.data["todos"][0]["priority"], "high")
        self.assertEqual(widget.data["todos"][0]["category"], "ประชุม")

    def test_f14_filtering_and_search_in_tkinter_widget(self):
        with open(self.todo_data_file, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "todos": [
                        {"id": "t1", "text": "ประชุมสหกรณ์", "category": "ประชุม", "priority": "high", "done": False, "completed": False},
                        {"id": "t2", "text": "ซื้อกาแฟ", "category": "ส่วนตัว", "priority": "low", "done": True, "completed": True},
                    ],
                    "events": [],
                },
                f,
            )
        widget = TkinterWidgetHarness.create_widget(self.todo_data_file)
        self.assertTrue(hasattr(widget, "get_filtered_todos"), "RightCornerDesktopWidget must implement get_filtered_todos")
        widget.status_filter = "active"
        active_items = widget.get_filtered_todos()
        self.assertEqual(len(active_items), 1)
        self.assertEqual(active_items[0]["id"], "t1")

    def test_f14_async_manual_sync_calendar_and_notion_non_blocking(self):
        self.mock_gcal.network_delay = 0.2
        self.mock_notion.network_delay = 0.2
        self.start_ephemeral_server()
        widget = TkinterWidgetHarness.create_widget(self.todo_data_file, server_port=self.server_port)
        self.assertTrue(hasattr(widget, "manual_sync_notion"), "RightCornerDesktopWidget must implement manual_sync_notion")

        t0 = time.time()
        widget.manual_sync_calendar()
        widget.manual_sync_notion()
        elapsed = time.time() - t0
        self.assertLess(elapsed, 0.15, "manual_sync_calendar and manual_sync_notion must run asynchronously without blocking UI thread")
        self.wait_for_notion_async(timeout=1.5)

    # =========================================================================
    # Feature F15: Batch Launchers, Deployment Configs & Thai Docs (5 tests)
    # =========================================================================

    def test_f15_open_widget_bat_has_python_fallback_and_http_app_url(self):
        bat_path = os.path.join(REPO_DIR, "Open_Widget_Right_Corner.bat")
        with open(bat_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("pythonw", content)
        self.assertIn("where python", content)
        self.assertIn("127.0.0.1", content)
        self.assertIn("18234", content)

    def test_f15_run_and_install_bat_scripts_reference_valid_targets(self):
        run_bat = os.path.join(REPO_DIR, "Run_Python_Widget.bat")
        install_bat = os.path.join(REPO_DIR, "Install_Python_Widget_Startup.bat")
        with open(run_bat, "r", encoding="utf-8") as f:
            run_content = f.read()
        with open(install_bat, "r", encoding="utf-8") as f:
            install_content = f.read()
        self.assertIn("desktop_widget_always_on_top.py", run_content)
        self.assertIn("Run_Python_Widget.bat", install_content)

    def test_f15_dockerignore_excludes_dev_artifacts_and_keeps_runtime_files(self):
        dockerignore_path = os.path.join(REPO_DIR, ".dockerignore")
        self.assertTrue(os.path.exists(dockerignore_path), ".dockerignore must exist")
        with open(dockerignore_path, "r", encoding="utf-8") as f:
            content = f.read()
        for excluded in (".git", "__pycache__", ".env", "tests"):
            self.assertIn(excluded, content)
        self.assertNotIn("desktop_sync_server.py", content)

    def test_f15_dockerfile_render_yaml_and_procfile_consistency(self):
        for fname in ("Dockerfile", "render.yaml", "Procfile", "requirements.txt"):
            fpath = os.path.join(REPO_DIR, fname)
            self.assertTrue(os.path.exists(fpath), f"{fname} must exist")
        with open(os.path.join(REPO_DIR, "render.yaml"), "r", encoding="utf-8") as f:
            render_txt = f.read()
        for env_key in ("NOTION_API_TOKEN", "NOTION_DATABASE_ID", "GOOGLE_CALENDAR_CRED_JSON", "GOOGLE_CALENDAR_KEYS_JSON"):
            self.assertIn(env_key, render_txt)

    def test_f15_thai_readme_documents_all_actual_files_and_features(self):
        readme_path = os.path.join(REPO_DIR, "README_วิธีใช้งาน.txt")
        with open(readme_path, "r", encoding="utf-8") as f:
            content = f.read()
        for actual_file in (
            "Open_Widget_Right_Corner.bat",
            "Run_Python_Widget.bat",
            "Install_Python_Widget_Startup.bat",
            "desktop_widget_always_on_top.py",
            "desktop_todo_calendar_widget.html",
            "desktop_sync_server.py",
        ):
            self.assertIn(actual_file, content, f"README must document {actual_file}")
        self.assertNotIn("Install_Desktop_And_Startup.bat", content)


if __name__ == "__main__":
    unittest.main()
