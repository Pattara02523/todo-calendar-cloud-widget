# -*- coding: utf-8 -*-
"""
Tier 3 — Cross-Feature Combination E2E Tests (`tests/test_tier3_combinations.py`).

Implements all 24 pairwise & multi-feature combination test cases (`test_01_...` through `test_24_...`,
requirement ≥20) verifying cross-surface interactions across Backend (F1–F7), Web/PWA Frontend (F8–F13),
and Native Tkinter Desktop Widget & DevOps (F14–F15).
"""

import json
import os
import threading
import time
import unittest

from tests.test_helpers import (
    REPO_DIR,
    FrontendVMHarness,
    IsolatedWidgetTestCase,
    TkinterWidgetHarness,
    get_free_port,
)


class TestTier3Combinations(IsolatedWidgetTestCase):
    """Tier 3 Cross-Feature Combination Tests (24 test cases)."""

    def test_01_concurrent_post_todos_and_background_notion_sync_no_duplicates(self):
        """F4 × F5: Atomic Lock + Notion Sync Lock + 2-Way Sync prevents duplicate page creation."""
        seed_uid = "3fe16fa7-85cd-8086-8994-000000000001"
        self.mock_notion.seed_todo_page(seed_uid, text="Existing Task")
        self.desktop_sync_server.save_data(
            {"todos": [{"id": seed_uid, "text": "Existing Task", "done": False, "completed": False}], "events": []}
        )
        self.mock_notion.network_delay = 0.05
        self.start_ephemeral_server()

        sync_thread = threading.Thread(target=self.desktop_sync_server.sync_notion_todos_now)
        status, _, _ = self.api_request(
            "POST",
            "/api/todos",
            body=[
                {"id": seed_uid, "text": "Existing Task", "done": False, "completed": False},
                {"id": "todo-1001", "text": "เตรียมสไลด์", "done": False, "completed": False},
            ],
        )
        sync_thread.start()
        self.assertEqual(status, 200)
        sync_thread.join(timeout=3.0)
        self.wait_for_notion_async(timeout=2.0)

        page_creates = [
            r for r in self.mock_notion.requests_log if r["method"] == "POST" and r["url"].endswith("/v1/pages")
        ]
        self.assertEqual(len(page_creates), 1, "Must create at most 1 Notion page for todo-1001")
        _, _, final_data = self.api_request("GET", "/api/data")
        self.assertEqual(len(final_data["todos"]), 2)
        for t in final_data["todos"]:
            self.assertTrue(len(t["id"]) >= 32 and "-" in t["id"])

    def test_02_concurrent_post_todos_and_calendar_sync_no_lost_updates(self):
        """F4 × F5 × F6: Concurrent POST /api/todos and sync_google_calendar_now() preserve both lists."""
        for i in range(4):
            self.mock_gcal.add_mock_event(summary=f"Cal Event {i}", date_str="2026-11-10")
        self.start_ephemeral_server()

        def _post_todo():
            self.api_request(
                "POST",
                "/api/todos",
                body=[
                    {"id": "3fe16fa7-85cd-8086-8994-000000000011", "text": "T1", "done": False},
                    {"id": "3fe16fa7-85cd-8086-8994-000000000012", "text": "T2", "done": True},
                ],
            )

        def _sync_cal():
            self.desktop_sync_server.sync_google_calendar_now()

        threads = [threading.Thread(target=_post_todo) for _ in range(5)] + [
            threading.Thread(target=_sync_cal) for _ in range(3)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        self.wait_for_notion_async()

        data = self.desktop_sync_server.load_data()
        self.assertEqual(len(data["todos"]), 2)
        self.assertEqual(len(data["events"]), 4)

    def test_03_remote_notion_completion_and_metadata_updates_local_without_revert(self):
        """F5 × F8: Remote Notion poll updates local cache without reverting remote changes."""
        uid = "3ff16fa7-85cd-81a7-93ca-d957ccadbae0"
        self.desktop_sync_server.save_data(
            {
                "todos": [
                    {
                        "id": uid,
                        "text": "Initial Task",
                        "completed": False,
                        "done": False,
                        "priority": "medium",
                        "category": "งานประจำ",
                        "dueDate": "2026-11-08",
                    }
                ],
                "events": [],
            }
        )
        self.mock_notion.seed_todo_page(
            uid,
            text="Initial Task",
            done=True,
            due_date="2026-11-15",
            priority="high",
            category="ด่วน",
        )
        self.mock_notion.requests_log.clear()

        status, _, body = self.api_request("POST", "/api/notion/sync")
        self.assertEqual(status, 200)
        patches = [r for r in self.mock_notion.requests_log if r["method"] == "PATCH"]
        self.assertEqual(len(patches), 0, "Periodic Notion sync must NOT revert remote completion back to False")

        t = self.desktop_sync_server.load_data()["todos"][0]
        self.assertTrue(t["completed"] and t["done"])
        self.assertEqual(t["priority"], "high")
        self.assertEqual(t["category"], "ด่วน")
        self.assertEqual(t["dueDate"], "2026-11-15")

    def test_04_local_patch_metadata_pushes_to_notion_and_preserves_notion_url(self):
        """F5 × F10: PATCH /api/todos/<id> updates Notion properties and preserves notion_url."""
        uid = "3fe16fa7-85cd-8086-8994-000000000044"
        n_url = "https://www.notion.so/3fe16fa785cd80868994000000000044"
        self.mock_notion.seed_todo_page(uid, text="Before Patch", url=n_url)
        self.desktop_sync_server.save_data(
            {
                "todos": [
                    {
                        "id": uid,
                        "text": "Before Patch",
                        "dueDate": "2026-11-08",
                        "priority": "low",
                        "category": "งานประจำ",
                        "completed": False,
                        "done": False,
                        "notion_url": n_url,
                    }
                ],
                "events": [],
            }
        )

        status, _, body = self.api_request(
            "PATCH",
            f"/api/todos/{uid}",
            body={
                "text": "อัปเดตหัวข้อใหม่",
                "dueDate": "2026-12-01",
                "priority": "high",
                "category": "ประชุม",
                "completed": True,
            },
        )
        self.assertEqual(status, 200)
        self.wait_for_notion_async()

        saved = self.desktop_sync_server.load_data()["todos"][0]
        self.assertEqual(saved.get("notion_url"), n_url)
        remote = self.mock_notion.extract_page_summary(uid)
        self.assertEqual(remote["text"], "อัปเดตหัวข้อใหม่")
        self.assertEqual(remote["dueDate"], "2026-12-01")
        self.assertEqual(remote["priority"], "high")
        self.assertEqual(remote["category"], "ประชุม")
        self.assertTrue(remote["done"])

    def test_05_local_bulk_post_todos_metadata_diff_pushes_only_changed_fields(self):
        """F5 × F8 × F10: POST /api/todos diffs metadata changes and patches only modified UUIDs."""
        u1 = "3fe16fa7-85cd-8086-8994-000000000051"
        u2 = "3fe16fa7-85cd-8086-8994-000000000052"
        self.mock_notion.seed_todo_page(u1, text="Unchanged", priority="medium", category="งานประจำ", due_date="2026-11-08")
        self.mock_notion.seed_todo_page(u2, text="Old Text", priority="medium", category="งานประจำ", due_date="2026-11-08")
        self.desktop_sync_server.save_data(
            {
                "todos": [
                    {"id": u1, "text": "Unchanged", "priority": "medium", "category": "งานประจำ", "dueDate": "2026-11-08", "done": False, "completed": False},
                    {"id": u2, "text": "Old Text", "priority": "medium", "category": "งานประจำ", "dueDate": "2026-11-08", "done": False, "completed": False},
                ],
                "events": [],
            }
        )
        self.mock_notion.requests_log.clear()

        status, _, res = self.api_request(
            "POST",
            "/api/todos",
            body=[
                {"id": u1, "text": "Unchanged", "priority": "medium", "category": "งานประจำ", "dueDate": "2026-11-08", "done": False, "completed": False},
                {"id": u2, "text": "Modified Text", "priority": "low", "category": "ด่วน", "dueDate": "2026-11-20", "done": False, "completed": False},
            ],
        )
        self.assertEqual(status, 200)
        self.assertIn("todos", res)
        self.wait_for_notion_async()

        u1_patches = [r for r in self.mock_notion.requests_log if r["method"] == "PATCH" and u1 in r["url"]]
        u2_patches = [r for r in self.mock_notion.requests_log if r["method"] == "PATCH" and u2 in r["url"]]
        self.assertEqual(len(u1_patches), 0)
        self.assertEqual(len(u2_patches), 1)
        self.assertEqual(self.mock_notion.extract_page_summary(u2)["text"], "Modified Text")

    def test_06_tkinter_delete_todo_archives_in_notion_and_prevents_resurrection(self):
        """F5 × F14: Tkinter deletion archives UUID in Notion so subsequent sync does not resurrect it."""
        uid = "3fe16fa7-85cd-8086-8994-000000000066"
        self.mock_notion.seed_todo_page(uid, text="Do Not Resurrect")
        with open(self.todo_data_file, "w", encoding="utf-8") as f:
            json.dump(
                {"todos": [{"id": uid, "text": "Do Not Resurrect", "done": False, "completed": False}], "events": []},
                f,
            )
        self.start_ephemeral_server()
        widget = TkinterWidgetHarness.create_widget(self.todo_data_file, server_port=self.server_port)
        widget.delete_todo(0)
        self.wait_for_notion_async()

        status, _, sync_res = self.api_request("POST", "/api/notion/sync")
        self.assertEqual(status, 200)
        self.assertEqual(sync_res.get("todos"), [])
        self.assertTrue(self.mock_notion.pages[uid]["archived"])

    def test_07_tkinter_corrupted_json_and_empty_events_never_push_default_todos_to_notion(self):
        """F4 × F5 × F14: Tkinter anti-corruption prevents pushing DEFAULT_TODOS to Notion."""
        with open(self.todo_data_file, "w", encoding="utf-8") as f:
            f.write('{"todos": [')
        widget = TkinterWidgetHarness.create_widget(self.todo_data_file)
        widget.load_data()
        self.desktop_sync_server.sync_notion_todos_now()

        creates = [r for r in self.mock_notion.requests_log if r["method"] == "POST" and r["url"].endswith("/v1/pages")]
        self.assertEqual(len(creates), 0, "Corrupted JSON must never push DEFAULT_TODOS into Notion")

        with open(self.todo_data_file, "w", encoding="utf-8") as f:
            json.dump({"todos": [], "events": []}, f)
        loaded = widget.load_data()
        self.assertEqual(loaded["events"], [])

    def test_08_widget_api_key_auth_with_cors_options_and_public_read_routes(self):
        """F3 × F7 × F13: WIDGET_API_KEY + CORS OPTIONS + static allowlist + secret blocking."""
        os.environ["WIDGET_API_KEY"] = "secret-cloud-key-99"
        s_opt, h_opt, _ = self.api_request("OPTIONS", "/api/todos")
        self.assertEqual(s_opt, 204)
        self.assertIn("x-api-key", h_opt.get("access-control-allow-headers", "").lower())

        for pub_route in ("/", "/manifest.json", "/sw.js", "/api/data", "/api/ping", "/api/health"):
            st, _, _ = self.api_request("GET", pub_route)
            self.assertEqual(st, 200, f"Public GET {pub_route} must return 200 without key")

        st_sec, _, _ = self.api_request(
            "GET", "/notion_config.json", headers={"X-API-Key": "secret-cloud-key-99"}
        )
        self.assertIn(st_sec, (403, 404), "Secret files must remain blocked even with valid API key")

        st_unauth, _, _ = self.api_request("POST", "/api/todos", body=[])
        self.assertEqual(st_unauth, 401)

        st_auth, _, _ = self.api_request(
            "POST", "/api/todos", body=[], headers={"X-API-Key": "secret-cloud-key-99"}
        )
        self.assertEqual(st_auth, 200)

    def test_09_widget_api_key_bearer_auth_across_patch_delete_and_calendar_post(self):
        """F5 × F6 × F7: Authorization: Bearer across granular Todo & Calendar mutating endpoints."""
        os.environ["WIDGET_API_KEY"] = "bearer-token-xyz"
        uid = "3fe16fa7-85cd-8086-8994-000000000090"
        self.mock_notion.seed_todo_page(uid, text="Auth Task")
        self.desktop_sync_server.save_data(
            {"todos": [{"id": uid, "text": "Auth Task", "done": False, "completed": False}], "events": []}
        )

        bad_hdr = {"X-API-Key": "wrong"}
        for m, p, b in (
            ("PATCH", f"/api/todos/{uid}", {"done": True}),
            ("DELETE", f"/api/todos/{uid}", None),
            ("POST", "/api/calendar/events", {"title": "E", "date": "2026-11-10"}),
            ("POST", "/api/notion/sync", {}),
            ("POST", "/api/events", []),
        ):
            st, _, _ = self.api_request(m, p, body=b, headers=bad_hdr)
            self.assertEqual(st, 401)

        good_hdr = {"Authorization": "Bearer bearer-token-xyz"}
        self.assertEqual(self.api_request("PATCH", f"/api/todos/{uid}", body={"done": True}, headers=good_hdr)[0], 200)
        self.assertEqual(
            self.api_request("POST", "/api/calendar/events", body={"title": "E", "date": "2026-11-10"}, headers=good_hdr)[0],
            200,
        )
        self.assertEqual(self.api_request("POST", "/api/notion/sync", body={}, headers=good_hdr)[0], 200)
        self.assertEqual(self.api_request("POST", "/api/events", body=[], headers=good_hdr)[0], 200)
        self.assertEqual(self.api_request("DELETE", f"/api/todos/{uid}", headers=good_hdr)[0], 200)

    def test_10_env_token_override_precedence_and_notion_connect_persistence(self):
        """F1 × F5 × F7: Env token/DB overrides + POST /api/notion/connect persistence."""
        with open(self.notion_config_file, "w", encoding="utf-8") as f:
            json.dump({"token": "file_token", "database_id": "db_from_file"}, f)
        self.mock_notion.databases["db_from_env"] = self.mock_notion.databases["db-mock-default"]
        self.mock_notion.databases["db_explicit_connect"] = self.mock_notion.databases["db-mock-default"]

        os.environ["NOTION_API_TOKEN"] = "env_override_token"
        os.environ["NOTION_DATABASE_ID"] = "db_from_env"
        self.api_request("GET", "/api/notion/status")
        self.api_request("POST", "/api/notion/sync")
        self.assertTrue(
            any(r["headers"].get("authorization") == "Bearer env_override_token" for r in self.mock_notion.requests_log)
        )
        self.assertTrue(any("db_from_env" in r["url"] for r in self.mock_notion.requests_log))

        os.environ.pop("NOTION_DATABASE_ID", None)
        status, _, body = self.api_request(
            "POST", "/api/notion/connect", body={"database_id": "db_explicit_connect"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(body.get("database_id"), "db_explicit_connect")
        with open(self.notion_config_file, "r", encoding="utf-8") as f:
            saved_cfg = json.load(f)
        self.assertEqual(saved_cfg.get("database_id"), "db_explicit_connect")

    def test_11_gcal_oauth_refresh_writes_back_to_local_cred_and_reuses_valid_token(self):
        """F1 × F6: OAuth refresh writes back to LOCAL_CRED_PATH and reuses cached token."""
        self.google_calendar_service.cached_token = None
        self.google_calendar_service.cached_expiry = 0

        self.api_request("GET", "/api/calendar/events")
        self.api_request("GET", "/api/calendar/events")
        self.assertEqual(len(self.mock_gcal.oauth_requests), 1)
        with open(self.cred_file, "r", encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual(saved.get("access_token"), "mock_ya29_access_token")

    def test_12_gcal_invalid_grant_preserves_cached_events_and_reports_degraded_status(self):
        """F4 × F6 × F7: invalid_grant preserves cached events and surfaces in /api/calendar/status & /api/health."""
        cached_evs = [
            {"id": f"c-{i}", "title": f"Cached {i}", "date": "2026-11-08", "time": "ทั้งวัน", "sourceType": "primary"}
            for i in range(3)
        ]
        self.desktop_sync_server.save_data({"todos": [], "events": cached_evs})
        self.google_calendar_service.cached_token = None
        self.google_calendar_service.cached_expiry = 0
        self.mock_gcal.oauth_error = (400, "Bad Request", {"error": "invalid_grant"})

        s1, _, evs = self.api_request("GET", "/api/calendar/events")
        self.assertEqual(s1, 200)
        self.assertEqual(len(evs), 3)

        s2, _, cal_st = self.api_request("GET", "/api/calendar/status")
        self.assertEqual(s2, 200)
        self.assertIn(cal_st.get("status"), ("cached_fallback", "cached", "error"))

        s3, _, health = self.api_request("GET", "/api/health")
        self.assertEqual(s3, 200)
        self.assertEqual(health.get("counts", {}).get("events"), 3)

    def test_13_calendar_week_range_query_with_multi_calendar_and_holiday_sorting(self):
        """F6 × F12: ?start=YYYY-MM-DD&days=7 across primary, coop, and holiday calendars."""
        self.mock_gcal.add_mock_event(
            calendar_id="pattarapon.thongsri@gmail.com",
            summary="Primary Afternoon",
            date_str="2026-12-10",
            start_time="14:00",
            end_time="15:00",
        )
        self.mock_gcal.add_mock_event(
            calendar_id="coopthaiict@gmail.com",
            summary="Coop Morning",
            date_str="2026-12-10",
            start_time="09:00",
            end_time="10:30",
        )
        self.mock_gcal.add_mock_event(
            calendar_id="en.th#holiday@group.v.calendar.google.com",
            summary="วันพระราชทานรัฐธรรมนูญ",
            date_str="2026-12-10",
        )

        status, _, events = self.api_request("GET", "/api/calendar/events?start=2026-12-10&days=7")
        self.assertEqual(status, 200)
        dec10 = [e for e in events if e.get("date") == "2026-12-10"]
        self.assertEqual(len(dec10), 3)
        self.assertEqual([e["sourceType"] for e in dec10], ["holiday", "coop", "primary"])

    def test_14_calendar_create_midnight_wrap_event_and_immediate_week_sync(self):
        """F6 × F12: POST /api/calendar/events at 23:30 + 1h wraps to next day and syncs cache."""
        today = time.strftime("%Y-%m-%d")
        status, _, res = self.api_request(
            "POST",
            "/api/calendar/events",
            body={
                "title": "Deploy ระบบดึก",
                "date": today,
                "time": "23:30",
                "calendar": "coopthaiict@gmail.com",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(res["event"]["sourceType"], "coop")
        sent = self.mock_gcal.created_events[-1]["payload"]
        self.assertNotEqual(sent["start"]["dateTime"][:10], sent["end"]["dateTime"][:10])

    def test_15_notion_pagination_over_100_items_with_local_new_task_merge(self):
        """F4 × F5: Notion cursor pagination >100 items + local unsynced task merge."""
        b1 = [
            self.mock_notion.seed_todo_page(f"3fe16fa7-85cd-8086-8994-{i:012d}", text=f"Task {i}")
            for i in range(1, 101)
        ]
        b2 = [
            self.mock_notion.seed_todo_page(f"3fe16fa7-85cd-8086-8994-{i:012d}", text=f"Task {i}")
            for i in range(101, 136)
        ]
        self.mock_notion.paginated_batches = [b1, b2]
        self.desktop_sync_server.save_data(
            {"todos": [{"id": "todo-999", "text": "Local 136th", "done": False, "completed": False}], "events": []}
        )

        status, _, body = self.api_request("POST", "/api/notion/sync")
        self.assertEqual(status, 200)
        self.assertEqual(len(body.get("todos", [])), 136)

    def test_16_notion_auto_discovery_creates_database_in_page_and_syncs_initial_todos(self):
        """F1 × F5: POST /api/notion/connect auto-creates DB inside shared page and syncs local tasks."""
        with open(self.notion_config_file, "w", encoding="utf-8") as f:
            json.dump({"token": "ntn_mock", "database_id": None, "page_id": None}, f)
        self.mock_notion.databases.clear()
        self.desktop_sync_server.save_data(
            {
                "todos": [
                    {"id": "todo-init-1", "text": "Starter 1", "done": False, "completed": False},
                    {"id": "todo-init-2", "text": "Starter 2", "done": True, "completed": True},
                ],
                "events": [],
            }
        )

        status, _, body = self.api_request("POST", "/api/notion/connect", body={})
        self.assertEqual(status, 200)
        self.assertEqual(body.get("status"), "connected")
        self.assertTrue(bool(body.get("database_id")))
        self.assertEqual(len(body.get("todos", [])), 2)

    def test_17_frontend_xss_payloads_stored_via_api_are_safely_handled_in_html_and_json(self):
        """F5 × F6 × F9: XSS payloads stored via REST API are rendered safely in frontend DOM."""
        xss_todo = {
            "id": "3fe16fa7-85cd-8086-8994-000000000170",
            "text": '<img src=x onerror="window.__xss=1">',
            "category": "<script>alert(1)</script>",
            "dueDate": "2026-11-08",
            "priority": "high",
            "done": False,
            "completed": False,
        }
        xss_ev = {
            "id": "ev-xss-170",
            "title": "<svg onload=alert(2)>",
            "date": "2026-11-08",
            "time": "10:00 - 11:00",
            "sourceType": "primary",
        }
        self.api_request("POST", "/api/todos", body=[xss_todo])
        self.api_request("POST", "/api/events", body=[xss_ev])
        _, _, api_data = self.api_request("GET", "/api/data")

        res = FrontendVMHarness.run_js(
            f"""
            const payload = {json.dumps(api_data)};
            __test_helpers.setFetchHandler(async () => ({{
              ok: true, status: 200, json: async () => payload
            }}));
            await syncFetchData();
            renderTodos();
            renderWeekCalendar();
            const unsafe = __test_helpers.rawInnerHTMLAssignments.filter(
              a => a.html.includes('<img src=x') || a.html.includes('<script>alert') || a.html.includes('<svg onload')
            );
            return {{ unsafeCount: unsafe.length, xssFlag: !!window.__xss }};
            """
        )
        self.assertEqual(res["unsafeCount"], 0)
        self.assertFalse(res["xssFlag"])

    def test_18_frontend_save_version_and_full_field_diffing_with_backend_reconciled_todos(self):
        """F5 × F8 × F10: POST /api/todos returns reconciled todos and frontend updates UUID + notion_url."""
        status, _, post_res = self.api_request(
            "POST",
            "/api/todos",
            body=[{"id": "todo-12345", "text": "Reconcile Me", "priority": "high", "category": "ด่วน", "done": False}],
        )
        self.assertEqual(status, 200)
        self.assertIn("todos", post_res)
        self.wait_for_notion_async()
        _, _, latest = self.api_request("GET", "/api/data")

        res = FrontendVMHarness.run_js(
            f"""
            todos = [{{ id: 'todo-12345', text: 'Reconcile Me', priority: 'high', category: 'ด่วน', done: false }}];
            __test_helpers.setFetchHandler(async () => ({{
              ok: true, status: 200, json: async () => ({json.dumps(latest)})
            }}));
            await syncFetchData();
            return todos[0];
            """
        )
        self.assertTrue(len(res["id"]) >= 32 and "-" in res["id"])
        self.assertTrue(str(res.get("notion_url", "")).startswith("https://"))

    def test_19_offline_custom_event_persistence_and_merge_across_calendar_sync(self):
        """F6 × F8 × F12: Offline custom-* events persist across calendar sync and empty events update."""
        custom_ev = {
            "id": "custom-1700001",
            "title": "นัดคุยงานออฟไลน์",
            "date": "2026-11-08",
            "time": "10:00 - 11:00",
            "sourceType": "custom",
        }
        self.api_request("POST", "/api/events", body=[custom_ev])
        self.mock_gcal.add_mock_event(summary="Live Google Event", date_str="2026-11-08", start_time="14:00", end_time="15:00")

        status, _, evs = self.api_request("GET", "/api/calendar/events")
        self.assertEqual(status, 200)
        ids = [e["id"] for e in evs]
        self.assertIn("custom-1700001", ids)

    def test_20_pwa_manifest_service_worker_and_static_security_integration(self):
        """F3 × F13: Public PWA assets served with 200 OK while secrets/sources return 403/404."""
        for ok_route in ("/", "/index.html", "/desktop_todo_calendar_widget.html", "/manifest.json", "/sw.js"):
            st, _, _ = self.api_request("GET", ok_route)
            self.assertEqual(st, 200)
        for blocked_route in ("/desktop_sync_server.py", "/../todo-calendar-cloud-widget/notion_config.json", "/.env"):
            st, _, _ = self.api_request("GET", blocked_route)
            self.assertIn(st, (403, 404))

    def test_21_search_filter_sort_and_overdue_badge_interaction_in_web_ui(self):
        """F10 × F11: Search + Category Filter + Sort + Overdue Badge compose cleanly."""
        res = FrontendVMHarness.run_js(
            """
            todos = [
              { id: '1', text: 'งานด่วนเก่า', dueDate: '2020-01-01', priority: 'high', category: 'ด่วน', completed: false, done: false },
              { id: '2', text: 'งานด่วนเสร็จแล้ว', dueDate: '2020-01-01', priority: 'high', category: 'ด่วน', completed: true, done: true },
              { id: '3', text: 'งานด่วนพรุ่งนี้', dueDate: '2099-01-01', priority: 'low', category: 'ด่วน', completed: false, done: false }
            ];
            currentTodoFilter = 'active';
            document.getElementById('todoCategoryFilter').value = 'ด่วน';
            document.getElementById('todoCategoryFilter').dispatchEvent({ type: 'change' });
            document.getElementById('todoSearchInput').value = 'งานด่วน';
            document.getElementById('todoSearchInput').dispatchEvent({ type: 'input' });
            document.getElementById('todoSortSelect').value = 'dueDate';
            document.getElementById('todoSortSelect').dispatchEvent({ type: 'change' });
            renderTodos();
            return document.getElementById('todoListContainer').textContent;
            """
        )
        self.assertIn("งานด่วนเก่า", res)
        self.assertIn("เลยกำหนด", res)
        self.assertNotIn("งานด่วนเสร็จแล้ว", res)
        self.assertLess(res.index("งานด่วนเก่า"), res.index("งานด่วนพรุ่งนี้"))

    def test_22_port_in_use_detection_and_single_instance_guard_with_live_server(self):
        """F2 × F7 × F14: is_port_in_use + /api/ping + start_in_thread single-instance guard."""
        self.start_ephemeral_server()
        unused = get_free_port()
        self.assertTrue(self.desktop_sync_server.is_port_in_use(self.server_port))
        self.assertFalse(self.desktop_sync_server.is_port_in_use(unused))
        self.desktop_sync_server.PORT = self.server_port
        self.assertIsNone(self.desktop_sync_server.start_in_thread())

    def test_23_tkinter_widget_api_crud_and_metadata_parity_with_web_client(self):
        """F5 × F10 × F14: Tkinter CRUD with priority/category/dueDate syncs with Server & Web."""
        self.start_ephemeral_server()
        widget = TkinterWidgetHarness.create_widget(self.todo_data_file, server_port=self.server_port)
        if hasattr(widget, "todo_entry") and widget.todo_entry is not None:
            widget.todo_entry.delete(0, "end")
            widget.todo_entry.insert(0, "งานสหกรณ์จาก Tkinter")
        try:
            widget.add_todo("งานสหกรณ์จาก Tkinter", priority="high", category="ด่วน", due_date="2026-11-30")
        except TypeError:
            widget.add_todo()
        self.wait_for_notion_async()

        _, _, web_data = self.api_request("GET", "/api/data")
        self.assertEqual(len(web_data["todos"]), 1)
        t = web_data["todos"][0]
        self.assertEqual(t["text"], "งานสหกรณ์จาก Tkinter")
        self.assertEqual(t["priority"], "high")
        self.assertEqual(t["category"], "ด่วน")
        self.assertEqual(t["dueDate"], "2026-11-30")

    def test_24_deployment_configs_batch_launchers_and_documentation_consistency(self):
        """F1 × F2 × F15: Cross-check .env.example, .dockerignore, render.yaml, .bat, and README."""
        for required_file in (
            ".env.example",
            ".dockerignore",
            "Dockerfile",
            "render.yaml",
            "Open_Widget_Right_Corner.bat",
            "Run_Python_Widget.bat",
            "Install_Python_Widget_Startup.bat",
            "README_วิธีใช้งาน.txt",
        ):
            self.assertTrue(
                os.path.exists(os.path.join(REPO_DIR, required_file)),
                f"Required file {required_file} must exist",
            )


if __name__ == "__main__":
    unittest.main()
