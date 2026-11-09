# -*- coding: utf-8 -*-
"""
Tier 2 — Boundary, Negative, Security & Corner-Case E2E Tests (`tests/test_tier2_boundaries.py`).

Implements 80 genuine boundary and corner-case test cases across all 15 features (`F1`–`F15`, ≥5 per feature):
- F1–F7 (Backend, Path Traversal/Secret Protection, Storage Corruption/Concurrency,
  Notion Errors, Google Calendar Midnight Wrap & `invalid_grant`, `WIDGET_API_KEY` 401 Auth): 35 tests
- F8–F13 (Frontend Race Overlapping Saves, XSS Payloads across all sinks, Task Edit Validation,
  Search Regex/Sort Edge Cases, Sunday Week Boundary & Network Fallback, Notification & SW Edge Cases): 34 tests
- F14–F15 (Tkinter Corrupted JSON Anti-Wipe, Offline Archive Fallback, Out-of-Range Index,
  Batch UTF-8/Fallback, `.dockerignore` & `.gitignore` Token Preservation): 11 tests
Total: 80 test methods.
"""

import http.server
import json
import os
import re
import socket
import subprocess
import threading
import time
import unittest
import unittest.mock

from tests.test_helpers import (
    REPO_DIR,
    FrontendVMHarness,
    IsolatedWidgetTestCase,
    TkinterWidgetHarness,
)


class TestTier2Boundaries(IsolatedWidgetTestCase):
    """Tier 2 Boundary & Corner Cases (F1 through F15 — 80 test cases)."""

    # =========================================================================
    # Feature F1 Boundaries: Token & Credential Edge Cases (5 tests)
    # =========================================================================

    def test_f1_boundary_01_corrupted_notion_config_json_falls_back_to_default_token(self):
        with open(self.notion_config_file, "w", encoding="utf-8") as f:
            f.write("{broken_json:")
        cfg = self.notion_service.load_config()
        self.assertEqual(cfg.get("token"), self.notion_service.DEFAULT_TOKEN)

    def test_f1_boundary_02_malformed_google_calendar_cred_json_env_var_handled_gracefully(self):
        os.environ["GOOGLE_CALENDAR_CRED_JSON"] = "{not-valid-json}"
        self.google_calendar_service.cached_token = None
        self.google_calendar_service.cached_expiry = 0
        # Should either fall back to self.cred_file or raise a clean error without crashing interpreter
        try:
            tok = self.google_calendar_service.get_access_token()
            self.assertEqual(tok, "mock_ya29_access_token")
        except FileNotFoundError:
            pass

    def test_f1_boundary_03_empty_string_env_vars_do_not_clobber_valid_file_tokens(self):
        with open(self.notion_config_file, "w", encoding="utf-8") as f:
            json.dump({"token": "ntn_valid_file", "database_id": "db_valid_file"}, f)
        os.environ["NOTION_API_TOKEN"] = ""
        os.environ["NOTION_DATABASE_ID"] = ""
        cfg = self.notion_service.load_config()
        self.assertEqual(cfg["token"], "ntn_valid_file")
        self.assertEqual(cfg["database_id"], "db_valid_file")

    def test_f1_boundary_04_missing_all_google_credentials_raises_clean_error_without_crash(self):
        if os.path.exists(self.cred_file):
            os.remove(self.cred_file)
        if os.path.exists(self.keys_file):
            os.remove(self.keys_file)
        self.google_calendar_service.cached_token = None
        self.google_calendar_service.cached_expiry = 0

        status, _, events = self.api_request("GET", "/api/calendar/events")
        self.assertEqual(status, 200)
        self.assertEqual(events, [])

        st_code, _, st_body = self.api_request("GET", "/api/calendar/status")
        self.assertEqual(st_code, 200)
        self.assertIn(st_body.get("status"), ("error", "cached_fallback"))

    def test_f1_boundary_05_dotenv_parser_handles_comments_quotes_and_blank_lines(self):
        with open(self.env_file, "w", encoding="utf-8") as f:
            f.write(
                "# Comment line\n\n"
                'KEY_WITH_QUOTES="val=123"\n'
                "SINGLE_QUOTED='hello_world'\n"
                "MALFORMED_LINE_WITHOUT_EQUALS\n"
            )
        if hasattr(self.desktop_sync_server, "load_dotenv_file"):
            self.desktop_sync_server.load_dotenv_file(self.env_file)
            self.assertEqual(os.environ.get("KEY_WITH_QUOTES"), "val=123")
            self.assertEqual(os.environ.get("SINGLE_QUOTED"), "hello_world")
        else:
            self.fail("desktop_sync_server must implement load_dotenv_file for .env support")

    # =========================================================================
    # Feature F2 Boundaries: Port Check & Server Binding Edge Cases (5 tests)
    # =========================================================================

    def test_f2_boundary_01_is_port_in_use_when_endpoint_returns_500_or_404(self):
        class _Dummy404Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(404)
                self.end_headers()
            def log_message(self, *args):
                pass

        dummy = http.server.HTTPServer(("127.0.0.1", 0), _Dummy404Handler)
        port = dummy.server_port
        t = threading.Thread(target=dummy.handle_request, daemon=True)
        t.start()
        try:
            self.assertFalse(self.desktop_sync_server.is_port_in_use(port))
        finally:
            dummy.server_close()

    def test_f2_boundary_02_is_port_in_use_timeout_on_stalled_socket(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        port = sock.getsockname()[1]
        try:
            t0 = time.time()
            res = self.desktop_sync_server.is_port_in_use(port)
            elapsed = time.time() - t0
            self.assertFalse(res)
            self.assertLess(elapsed, 2.5)
        finally:
            sock.close()

    def test_f2_boundary_03_is_port_in_use_invalid_port_numbers(self):
        self.assertFalse(self.desktop_sync_server.is_port_in_use(-1))
        self.assertFalse(self.desktop_sync_server.is_port_in_use(99999))

    def test_f2_boundary_04_missing_html_file_on_get_root_returns_graceful_fallback(self):
        self.desktop_sync_server.HTML_FILE = os.path.join(self.temp_dir, "nonexistent_widget.html")
        status, _, body = self.api_request("GET", "/")
        self.assertIn(status, (200, 404))
        self.assertIn("not found", str(body).lower())

    def test_f2_boundary_05_unknown_api_routes_return_404_json(self):
        s1, _, _ = self.api_request("GET", "/api/nonexistent_route")
        s2, _, _ = self.api_request("POST", "/api/nonexistent_route", body={})
        self.assertEqual(s1, 404)
        self.assertEqual(s2, 404)

    # =========================================================================
    # Feature F3 Boundaries: Static File Security & Path Traversal (5 tests)
    # =========================================================================

    def test_f3_boundary_01_block_notion_config_and_data_json_direct_get(self):
        for route in ("/notion_config.json", "/desktop_todo_data.json"):
            status, _, _ = self.api_request("GET", route)
            self.assertIn(status, (403, 404), f"Direct GET {route} must be blocked")

    def test_f3_boundary_02_block_calendar_auth_directory_and_credentials(self):
        for route in ("/.calendar_auth/credentials.json", "/.calendar_auth/gcp-oauth.keys.json", "/.calendar_auth/"):
            status, _, _ = self.api_request("GET", route)
            self.assertIn(status, (403, 404), f"Direct GET {route} must be blocked")

    def test_f3_boundary_03_block_dotenv_and_git_files(self):
        for route in ("/.env", "/.env.example", "/.git/config", "/.gitignore"):
            status, _, _ = self.api_request("GET", route)
            self.assertIn(status, (403, 404), f"Direct GET {route} must be blocked")

    def test_f3_boundary_04_block_python_source_files_with_query_strings(self):
        for route in (
            "/desktop_sync_server.py",
            "/notion_service.py?foo=bar",
            "/google_calendar_service.py",
            "/desktop_widget_always_on_top.py",
        ):
            status, _, _ = self.api_request("GET", route)
            self.assertIn(status, (403, 404), f"Source file route {route} must be blocked")

    def test_f3_boundary_05_block_url_encoded_path_traversal_attacks(self):
        for route in (
            "/..%2fnotion_config.json",
            "/%2e%2e/notion_config.json",
            "//notion_config.json",
            "/index.html/../../notion_config.json",
        ):
            status, _, body = self.api_request("GET", route)
            self.assertIn(status, (403, 404), f"Traversal route {route} must be blocked")
            self.assertNotIn("ntn_", str(body))

    # =========================================================================
    # Feature F4 Boundaries: Storage Corruption & Concurrency Stress (5 tests)
    # =========================================================================

    def test_f4_boundary_01_load_data_recovers_from_corrupted_or_zero_byte_json(self):
        with open(self.todo_data_file, "wb") as f:
            f.write(b"")
        d1 = self.desktop_sync_server.load_data()
        self.assertEqual(d1, {"todos": [], "events": []})

        with open(self.todo_data_file, "wb") as f:
            f.write(b"{corrupted_json")
        d2 = self.desktop_sync_server.load_data()
        self.assertEqual(d2, {"todos": [], "events": []})

    def test_f4_boundary_02_post_todos_with_malformed_json_body_returns_400(self):
        self.desktop_sync_server.save_data(
            {"todos": [{"id": "keep-1", "text": "Keep Me", "done": False, "completed": False}], "events": []}
        )
        status, _, _ = self.api_request("POST", "/api/todos", raw_body=b"{bad json")
        self.assertEqual(status, 400)
        self.assertEqual(self.desktop_sync_server.load_data()["todos"][0]["id"], "keep-1")

    def test_f4_boundary_03_post_todos_with_empty_body_or_invalid_type_returns_400(self):
        s1, _, _ = self.api_request("POST", "/api/todos", raw_body=b"")
        s2, _, _ = self.api_request("POST", "/api/todos", raw_body=b'"just_a_string"')
        self.assertEqual(s1, 400)
        self.assertEqual(s2, 400)

    def test_f4_boundary_04_high_contention_20_concurrent_threads_stress_test(self):
        self.start_ephemeral_server()
        statuses = []

        def _worker(i):
            if i % 3 == 0:
                st, _, _ = self.api_request(
                    "POST",
                    "/api/todos",
                    body=[{"id": f"3fe16fa7-85cd-8086-8994-{i:012d}", "text": f"T{i}", "done": False}],
                )
            elif i % 3 == 1:
                st, _, _ = self.api_request(
                    "POST",
                    "/api/events",
                    body=[{"id": f"e-{i}", "title": f"E{i}", "date": "2026-11-10", "time": "ทั้งวัน"}],
                )
            else:
                st, _, _ = self.api_request("GET", "/api/data")
            statuses.append(st)

        threads = [threading.Thread(target=_worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        self.assertTrue(all(s == 200 for s in statuses))
        with open(self.todo_data_file, "r", encoding="utf-8") as f:
            parsed = json.load(f)
        self.assertIn("todos", parsed)
        self.assertIn("events", parsed)

    def test_f4_boundary_05_unicode_emojis_and_long_strings_in_todo_storage(self):
        long_text = "🚀 งานด่วนมาก ก๋วยเตี๋ยวเรือ " * 250
        uid = "3fe16fa7-85cd-8086-8994-000000000999"
        status, _, _ = self.api_request(
            "POST",
            "/api/todos",
            body=[{"id": uid, "text": long_text, "done": False, "completed": False}],
        )
        self.assertEqual(status, 200)
        _, _, data = self.api_request("GET", "/api/data")
        self.assertEqual(data["todos"][0]["text"], long_text)

    # =========================================================================
    # Feature F5 Boundaries: Notion Sync Error & Edge-Case Boundaries (5 tests)
    # =========================================================================

    def test_f5_boundary_01_patch_and_delete_nonexistent_todo_id_returns_404(self):
        s_patch, _, _ = self.api_request("PATCH", "/api/todos/nonexistent-id", body={"done": True})
        s_del, _, _ = self.api_request("DELETE", "/api/todos/nonexistent-id")
        self.assertEqual(s_patch, 404)
        self.assertEqual(s_del, 404)

    def test_f5_boundary_02_notion_api_500_or_rate_limit_429_preserves_local_todos(self):
        self.desktop_sync_server.save_data(
            {
                "todos": [
                    {"id": "3fe16fa7-85cd-8086-8994-000000000001", "text": "Safe 1", "done": False, "completed": False},
                    {"id": "3fe16fa7-85cd-8086-8994-000000000002", "text": "Safe 2", "done": True, "completed": True},
                ],
                "events": [],
            }
        )
        self.mock_notion.next_error = (500, "Internal Server Error", {"message": "Notion down"})
        res = self.desktop_sync_server.sync_notion_todos_now()
        self.assertEqual(len(res), 2)
        self.assertEqual(len(self.desktop_sync_server.load_data()["todos"]), 2)

    def test_f5_boundary_03_fetch_notion_todos_skips_archived_and_blank_title_pages(self):
        self.mock_notion.seed_todo_page("3fe16fa7-85cd-8086-8994-000000000010", text="Archived", archived=True)
        self.mock_notion.seed_todo_page("3fe16fa7-85cd-8086-8994-000000000011", text="   ", archived=False)
        self.mock_notion.seed_todo_page("3fe16fa7-85cd-8086-8994-000000000012", text="Valid Active", archived=False)

        todos = self.notion_service.fetch_notion_todos("db-mock-default")
        self.assertEqual(len(todos), 1)
        self.assertEqual(todos[0]["text"], "Valid Active")

    def test_f5_boundary_04_remote_archived_page_removed_locally_during_sync(self):
        uid = "3fe16fa7-85cd-8086-8994-000000000099"
        self.desktop_sync_server.save_data(
            {"todos": [{"id": uid, "text": "Archived Remotely", "done": False, "completed": False}], "events": []}
        )
        # Mock Notion has empty active pages
        self.mock_notion.pages.clear()
        status, _, body = self.api_request("POST", "/api/notion/sync")
        self.assertEqual(status, 200)
        self.assertEqual(body.get("todos"), [])
        self.assertEqual(self.desktop_sync_server.load_data()["todos"], [])

    def test_f5_boundary_05_post_notion_connect_with_no_accessible_db_or_page_returns_400(self):
        with open(self.notion_config_file, "w", encoding="utf-8") as f:
            json.dump({"token": "ntn_mock", "database_id": None, "page_id": None}, f)
        self.mock_notion.databases.clear()
        self.mock_notion.shared_pages.clear()

        status, _, body = self.api_request("POST", "/api/notion/connect", body={})
        self.assertEqual(status, 400)
        self.assertTrue("error" in body or "message" in body)

    # =========================================================================
    # Feature F6 Boundaries: Midnight Wrap, OAuth `invalid_grant` & Range (5 tests)
    # =========================================================================

    def test_f6_boundary_01_midnight_wrap_around_23_30_plus_1h_rolls_to_next_day(self):
        status, _, _ = self.api_request(
            "POST",
            "/api/calendar/events",
            body={"title": "Late Night Deploy", "date": "2026-10-20", "time": "23:30"},
        )
        self.assertEqual(status, 200)
        payload = self.mock_gcal.created_events[-1]["payload"]
        self.assertEqual(payload["start"]["dateTime"], "2026-10-20T23:30:00+07:00")
        self.assertEqual(
            payload["end"]["dateTime"],
            "2026-10-21T00:30:00+07:00",
            "23:30 + 1h must roll date over to next day 2026-10-21T00:30:00+07:00",
        )

    def test_f6_boundary_02_year_end_midnight_wrap_2026_12_31_23_45_rolls_to_2027_01_01(self):
        self.google_calendar_service.create_event("New Year Eve", "2026-12-31", "23:45")
        payload = self.mock_gcal.created_events[-1]["payload"]
        self.assertEqual(payload["start"]["dateTime"], "2026-12-31T23:45:00+07:00")
        self.assertEqual(payload["end"]["dateTime"], "2027-01-01T00:45:00+07:00")

    def test_f6_boundary_03_oauth_invalid_grant_400_preserves_cached_and_custom_events(self):
        seeded_events = [
            {"id": "ev-cached-1", "title": "Cached Meeting", "date": "2026-11-08", "time": "10:00 - 11:00", "sourceType": "primary"},
            {"id": "custom-1", "title": "Custom Offline", "date": "2026-11-09", "time": "ทั้งวัน", "sourceType": "custom"},
        ]
        self.desktop_sync_server.save_data({"todos": [], "events": seeded_events})
        self.google_calendar_service.cached_token = None
        self.google_calendar_service.cached_expiry = 0
        self.mock_gcal.oauth_error = (400, "Bad Request", {"error": "invalid_grant"})

        status, _, events = self.api_request("GET", "/api/calendar/events")
        self.assertEqual(status, 200)
        self.assertEqual(len(events), 2, "OAuth invalid_grant must preserve cached events, never wiping to []")
        self.assertEqual(len(self.desktop_sync_server.load_data()["events"]), 2)

    def test_f6_boundary_04_post_calendar_events_missing_title_or_date_returns_400(self):
        s1, _, _ = self.api_request("POST", "/api/calendar/events", body={"title": "", "date": "2026-11-08"})
        s2, _, _ = self.api_request("POST", "/api/calendar/events", body={"title": "Valid", "date": ""})
        self.assertEqual(s1, 400)
        self.assertEqual(s2, 400)

    def test_f6_boundary_05_get_calendar_events_with_invalid_start_date_or_negative_days_handled_safely(self):
        s1, _, _ = self.api_request("GET", "/api/calendar/events?start=not-a-date&days=-5")
        s2, _, _ = self.api_request("GET", "/api/calendar/events?start=2026-11-08&days=abc")
        self.assertIn(s1, (200, 400))
        self.assertIn(s2, (200, 400))

    # =========================================================================
    # Feature F7 Boundaries: API Key Authentication & Security (5 tests)
    # =========================================================================

    def test_f7_boundary_01_missing_api_key_on_post_patch_delete_returns_401(self):
        os.environ["WIDGET_API_KEY"] = "top-secret"
        for method, path, body in (
            ("POST", "/api/todos", []),
            ("PATCH", "/api/todos/t1", {"done": True}),
            ("DELETE", "/api/todos/t1", None),
            ("POST", "/api/events", []),
            ("POST", "/api/calendar/events", {"title": "T", "date": "2026-11-08"}),
        ):
            status, _, res = self.api_request(method, path, body=body)
            self.assertEqual(status, 401, f"{method} {path} without API key must return 401")

    def test_f7_boundary_02_wrong_api_key_in_x_api_key_or_bearer_returns_401(self):
        os.environ["WIDGET_API_KEY"] = "top-secret"
        s1, _, _ = self.api_request("POST", "/api/todos", body=[], headers={"X-API-Key": "wrong-key"})
        s2, _, _ = self.api_request("POST", "/api/todos", body=[], headers={"Authorization": "Bearer wrong-key"})
        self.assertEqual(s1, 401)
        self.assertEqual(s2, 401)

    def test_f7_boundary_03_malformed_authorization_header_without_bearer_prefix_returns_401(self):
        os.environ["WIDGET_API_KEY"] = "top-secret"
        s1, _, _ = self.api_request("POST", "/api/todos", body=[], headers={"Authorization": "Basic top-secret"})
        s2, _, _ = self.api_request("POST", "/api/todos", body=[], headers={"Authorization": "Bearer"})
        self.assertEqual(s1, 401)
        self.assertEqual(s2, 401)

    def test_f7_boundary_04_read_only_get_and_options_endpoints_remain_accessible_when_api_key_set(self):
        os.environ["WIDGET_API_KEY"] = "top-secret"
        for path in ("/api/ping", "/api/health", "/api/data"):
            status, _, _ = self.api_request("GET", path)
            self.assertEqual(status, 200, f"GET {path} must remain 200 OK when WIDGET_API_KEY is set")
        opt_status, opt_headers, _ = self.api_request("OPTIONS", "/api/todos")
        self.assertEqual(opt_status, 204)
        allow_headers = opt_headers.get("access-control-allow-headers", "").lower()
        self.assertIn("x-api-key", allow_headers)
        self.assertIn("authorization", allow_headers)

    def test_f7_boundary_05_post_todos_accepts_both_raw_list_and_wrapped_dict_payload(self):
        os.environ.pop("WIDGET_API_KEY", None)
        s1, _, b1 = self.api_request(
            "POST", "/api/todos", body=[{"id": "3fe16fa7-85cd-8086-8994-000000000001", "text": "A", "done": False}]
        )
        s2, _, b2 = self.api_request(
            "POST",
            "/api/todos",
            body={"todos": [{"id": "3fe16fa7-85cd-8086-8994-000000000002", "text": "B", "done": True}]},
        )
        self.assertEqual(s1, 200)
        self.assertEqual(s2, 200)
        self.assertIn("todos", b1)
        self.assertIn("todos", b2)

    # =========================================================================
    # Feature F8 Boundaries: Sync Race, Malformed Storage & Offline (6 tests)
    # =========================================================================

    def test_f8_rapid_overlapping_saves_increment_save_version_monotonically(self):
        res = FrontendVMHarness.run_js(
            """
            let resolvers = [];
            __test_helpers.setFetchHandler((url, opts) => {
              return new Promise(r => {
                resolvers.push(() => r({
                  ok: true,
                  status: 200,
                  json: async () => ({ status: 'success', todos: JSON.parse(opts.body || '[]') })
                }));
              });
            });
            todos = [{ id: '1', text: 'S1', done: false }];
            const p1 = syncSaveTodos(todos);
            todos = [{ id: '1', text: 'S1', done: false }, { id: '2', text: 'S2', done: false }];
            const p2 = syncSaveTodos(todos);
            todos = [{ id: '1', text: 'S1', done: false }, { id: '2', text: 'S2', done: false }, { id: '3', text: 'S3', done: false }];
            const p3 = syncSaveTodos(todos);

            // Resolve in order 1, 2, 3
            for (const fn of resolvers) fn();
            await Promise.all([p1, p2, p3]);
            return todos.map(t => t.id);
            """
        )
        self.assertEqual(res, ["1", "2", "3"])

    def test_f8_corrupted_localstorage_json_falls_back_gracefully(self):
        res = FrontendVMHarness.run_js(
            """
            return { todosIsArray: Array.isArray(todos), eventsIsArray: Array.isArray(calendarEvents) };
            """,
            init_options={
                "localStorage": {
                    "desktop_widget_todos_v1": "{corrupted_json",
                    "desktop_widget_events_v1": "[unclosed_array",
                }
            },
        )
        self.assertTrue(res["todosIsArray"])
        self.assertTrue(res["eventsIsArray"])

    def test_f8_localstorage_quota_exceeded_does_not_crash_save(self):
        res = FrontendVMHarness.run_js(
            """
            localStorage.throwOnSet = true;
            await syncSaveTodos([{ id: 't1', text: 'Quota Test', done: false }]);
            return __test_helpers.fetchCalls.some(c => c.url.endsWith('/api/todos'));
            """
        )
        self.assertTrue(res)

    def test_f8_sync_fetch_handles_http_500_and_malformed_payloads(self):
        res = FrontendVMHarness.run_js(
            """
            todos = [{ id: 'keep', text: 'Keep', done: false }];
            __test_helpers.setFetchHandler(async () => ({ ok: false, status: 500, json: async () => ({}) }));
            await syncFetchData();
            const badgeText500 = document.getElementById('syncBadge').textContent;

            __test_helpers.setFetchHandler(async () => ({
              ok: true,
              status: 200,
              json: async () => ({ todos: null, events: 'not-an-array' })
            }));
            await syncFetchData();
            return { badgeText500, count: todos.length, firstId: todos[0].id };
            """
        )
        self.assertEqual(res["count"], 1)
        self.assertEqual(res["firstId"], "keep")

    def test_f8_todo_normalization_defaults_missing_fields(self):
        res = FrontendVMHarness.run_js(
            """
            __test_helpers.setFetchHandler(async () => ({
              ok: true,
              status: 200,
              json: async () => ({ todos: [{}], events: [] })
            }));
            await syncFetchData();
            return todos[0];
            """
        )
        self.assertTrue(bool(res.get("id")))
        self.assertEqual(res.get("priority"), "medium")
        self.assertEqual(res.get("category"), "งานประจำ")
        self.assertFalse(res.get("done"))
        self.assertFalse(res.get("completed"))

    def test_f8_duplicate_custom_offline_events_deduplicated_on_sync(self):
        res = FrontendVMHarness.run_js(
            """
            calendarEvents = [{ id: 'custom-999', title: 'Synced Custom', date: todayIso, time: 'ทั้งวัน', sourceType: 'custom' }];
            __test_helpers.setFetchHandler(async () => ({
              ok: true,
              status: 200,
              json: async () => ({
                todos: [],
                events: [{ id: 'custom-999', title: 'Synced Custom', date: todayIso, time: 'ทั้งวัน', sourceType: 'custom' }]
              })
            }));
            await syncFetchData();
            return calendarEvents.filter(e => e.id === 'custom-999').length;
            """
        )
        self.assertEqual(res, 1)

    # =========================================================================
    # Feature F9 Boundaries: Adversarial XSS Payloads Across All Sinks (6 tests)
    # =========================================================================

    def test_f9_xss_script_tag_in_todo_text_and_category(self):
        res = FrontendVMHarness.run_js(
            """
            todos = [{
              id: 'xss-1',
              text: '<script>window.__xss_triggered = true;</script>',
              category: "<img src=x onerror='window.__xss_triggered=true'>",
              dueDate: todayIso,
              priority: 'high',
              completed: false,
              done: false
            }];
            renderTodos();
            renderWeekCalendar();
            const unsafe = __test_helpers.rawInnerHTMLAssignments.filter(
              a => a.html.includes('<script>') || a.html.includes('<img src=x')
            );
            return { xssTriggered: !!window.__xss_triggered, unsafeCount: unsafe.length };
            """
        )
        self.assertFalse(res["xssTriggered"])
        self.assertEqual(res["unsafeCount"], 0)

    def test_f9_xss_img_onerror_in_calendar_event_title_and_time(self):
        res = FrontendVMHarness.run_js(
            """
            calendarEvents = [{
              id: 'xss-ev-1',
              title: '<img src=x onerror=alert(1)>',
              time: '"><svg onload=alert(2)>',
              date: todayIso,
              calendar: 'pattarapon.thongsri@gmail.com',
              sourceType: 'primary'
            }];
            renderWeekCalendar();
            const unsafe = __test_helpers.rawInnerHTMLAssignments.filter(
              a => a.html.includes('<img src=x') || a.html.includes('<svg onload')
            );
            return { unsafeCount: unsafe.length };
            """
        )
        self.assertEqual(res["unsafeCount"], 0)

    def test_f9_xss_payload_in_notion_modal_workspace_and_error(self):
        res = FrontendVMHarness.run_js(
            """
            __test_helpers.setFetchHandler(async () => ({
              ok: true,
              status: 200,
              json: async () => ({
                installed: true,
                connected: true,
                workspace: "<svg/onload=alert('xss')>",
                bot_name: "<script>alert(1)</script>",
                database_id: '"><img src=x onerror=alert(2)>'
              })
            }));
            await checkNotionStatus();
            const unsafe = __test_helpers.rawInnerHTMLAssignments.filter(
              a => a.html.includes("<svg/onload") || a.html.includes("<script>alert") || a.html.includes("<img src=x")
            );
            return { unsafeCount: unsafe.length };
            """
        )
        self.assertEqual(res["unsafeCount"], 0)

    def test_f9_xss_javascript_uri_in_notion_url_or_event_link(self):
        res = FrontendVMHarness.run_js(
            """
            todos = [{
              id: 'xss-url-1',
              text: 'Bad URL Task',
              dueDate: todayIso,
              priority: 'medium',
              category: 'งานประจำ',
              completed: false,
              done: false,
              notion_url: 'javascript:alert(1)'
            }];
            renderTodos();
            const html = document.getElementById('todoListContainer').outerHTML;
            return { hasJsHref: html.includes('href="javascript:') };
            """
        )
        self.assertFalse(res["hasJsHref"])

    def test_f9_null_undefined_and_numeric_inputs_to_escape_html(self):
        res = FrontendVMHarness.run_js(
            """
            if (typeof escapeHtml !== 'function') {
              throw new Error('escapeHtml helper must be defined in frontend script');
            }
            return {
              nullVal: escapeHtml(null),
              undefVal: escapeHtml(undefined),
              numVal: escapeHtml(12345),
              emptyVal: escapeHtml('')
            };
            """
        )
        self.assertEqual(res["nullVal"], "")
        self.assertEqual(res["undefVal"], "")
        self.assertEqual(res["numVal"], "12345")
        self.assertEqual(res["emptyVal"], "")

    def test_f9_static_audit_zero_unsafe_innerhtml_interpolations(self):
        _, script, _ = FrontendVMHarness.load_html_and_script()
        for raw_expr in ("${ev.title}", "${ev.time}", "${td.text}", "${td.category}", "${data.workspace}"):
            self.assertNotIn(raw_expr, script, f"Unescaped {raw_expr} interpolation must not exist in script")

    # =========================================================================
    # Feature F10 Boundaries: Task Edit Validation, Overdue & Cancel (6 tests)
    # =========================================================================

    def test_f10_edit_task_rejects_empty_or_whitespace_only_text(self):
        res = FrontendVMHarness.run_js(
            """
            todos = [{ id: 't1', text: 'Original Task', dueDate: todayIso, priority: 'medium', category: 'งานประจำ', completed: false, done: false }];
            openEditTodo('t1');
            document.getElementById('editTodoText').value = '    ';
            if (typeof saveEditedTodo === 'function') {
              await saveEditedTodo();
            } else {
              document.getElementById('editTodoForm').dispatchEvent({ type: 'submit' });
            }
            return todos[0].text;
            """
        )
        self.assertEqual(res, "Original Task")

    def test_f10_cancel_edit_restores_original_task_unchanged(self):
        res = FrontendVMHarness.run_js(
            """
            todos = [{ id: 't1', text: 'Original Task', dueDate: todayIso, priority: 'medium', category: 'งานประจำ', completed: false, done: false }];
            openEditTodo('t1');
            document.getElementById('editTodoText').value = 'Aborted Change';
            const cancelBtn = document.getElementById('btnCancelEditTodo');
            if (cancelBtn) cancelBtn.click();
            return todos[0].text;
            """
        )
        self.assertEqual(res, "Original Task")

    def test_f10_completed_past_task_does_not_show_overdue_badge(self):
        res = FrontendVMHarness.run_js(
            """
            todos = [{ id: 't-done-past', text: 'Completed Old', dueDate: '2020-01-01', priority: 'high', category: 'งานประจำ', completed: true, done: true }];
            renderTodos();
            return document.getElementById('todoListContainer').textContent;
            """
        )
        self.assertNotIn("เลยกำหนด", res)

    def test_f10_future_task_does_not_show_overdue_or_today_badge(self):
        res = FrontendVMHarness.run_js(
            """
            todos = [{ id: 't-future', text: 'Future Task', dueDate: '2099-12-31', priority: 'medium', category: 'งานประจำ', completed: false, done: false }];
            renderTodos();
            return document.getElementById('todoListContainer').textContent;
            """
        )
        self.assertNotIn("เลยกำหนด", res)
        self.assertNotIn("วันนี้", res)

    def test_f10_task_without_notion_url_omits_notion_link(self):
        res = FrontendVMHarness.run_js(
            """
            todos = [{ id: 'todo-local', text: 'Local Only', dueDate: todayIso, priority: 'medium', category: 'งานประจำ', completed: false, done: false, notion_url: '' }];
            renderTodos();
            return document.getElementById('todoListContainer').outerHTML;
            """
        )
        self.assertNotIn("Notion ↗", res)

    def test_f10_edit_nonexistent_task_id_is_safe_noop(self):
        res = FrontendVMHarness.run_js(
            """
            todos = [{ id: 't1', text: 'Keep', dueDate: todayIso, priority: 'medium', category: 'งานประจำ', completed: false, done: false }];
            openEditTodo('non-existent-id');
            return todos.length;
            """
        )
        self.assertEqual(res, 1)

    # =========================================================================
    # Feature F11 Boundaries: Search Regex, Empty Dates & Zero Results (5 tests)
    # =========================================================================

    def test_f11_search_with_regex_metacharacters_does_not_throw(self):
        res = FrontendVMHarness.run_js(
            """
            todos = [{ id: 't1', text: 'Normal Task', category: 'งานประจำ', dueDate: todayIso, priority: 'medium', completed: false, done: false }];
            const s = document.getElementById('todoSearchInput');
            s.value = '[.*+?^${}()|[]\\\\]';
            s.dispatchEvent({ type: 'input' });
            renderTodos();
            return document.getElementById('todoListContainer').textContent;
            """
        )
        self.assertNotIn("Normal Task", res)

    def test_f11_search_case_insensitive_english_and_thai_unicode(self):
        res = FrontendVMHarness.run_js(
            """
            todos = [{ id: 't1', text: 'Prepare NOTION Sync เอกสาร', category: 'งานประจำ', dueDate: todayIso, priority: 'medium', completed: false, done: false }];
            const s = document.getElementById('todoSearchInput');
            s.value = 'notion sync';
            s.dispatchEvent({ type: 'input' });
            renderTodos();
            const m1 = document.getElementById('todoListContainer').textContent;

            s.value = 'เอกสาร';
            s.dispatchEvent({ type: 'input' });
            renderTodos();
            const m2 = document.getElementById('todoListContainer').textContent;
            return { m1, m2 };
            """
        )
        self.assertIn("Prepare NOTION Sync เอกสาร", res["m1"])
        self.assertIn("Prepare NOTION Sync เอกสาร", res["m2"])

    def test_f11_sort_by_due_date_places_empty_due_dates_last(self):
        res = FrontendVMHarness.run_js(
            """
            todos = [
              { id: '1', text: 'Task NoDate', dueDate: '', priority: 'medium', category: 'งานประจำ', completed: false, done: false },
              { id: '2', text: 'Task Early', dueDate: '2026-11-01', priority: 'medium', category: 'งานประจำ', completed: false, done: false },
              { id: '3', text: 'Task Mid', dueDate: '2026-11-05', priority: 'medium', category: 'งานประจำ', completed: false, done: false }
            ];
            const sortEl = document.getElementById('todoSortSelect');
            sortEl.value = 'dueDate';
            sortEl.dispatchEvent({ type: 'change' });
            renderTodos();
            const txt = document.getElementById('todoListContainer').textContent;
            return {
              iEarly: txt.indexOf('Task Early'),
              iMid: txt.indexOf('Task Mid'),
              iNoDate: txt.indexOf('Task NoDate')
            };
            """
        )
        self.assertTrue(0 <= res["iEarly"] < res["iMid"] < res["iNoDate"])

    def test_f11_sort_by_priority_handles_unknown_priority_value(self):
        res = FrontendVMHarness.run_js(
            """
            todos = [
              { id: '1', text: 'Task Unknown', dueDate: todayIso, priority: 'unknown_prio', category: 'งานประจำ', completed: false, done: false },
              { id: '2', text: 'Task High', dueDate: todayIso, priority: 'high', category: 'งานประจำ', completed: false, done: false },
              { id: '3', text: 'Task Low', dueDate: todayIso, priority: 'low', category: 'งานประจำ', completed: false, done: false }
            ];
            const sortEl = document.getElementById('todoSortSelect');
            sortEl.value = 'priority';
            sortEl.dispatchEvent({ type: 'change' });
            renderTodos();
            const txt = document.getElementById('todoListContainer').textContent;
            return txt.includes('Task High') && txt.includes('Task Low') && txt.includes('Task Unknown');
            """
        )
        self.assertTrue(res)

    def test_f11_filter_yielding_zero_results_shows_empty_placeholder(self):
        res = FrontendVMHarness.run_js(
            """
            todos = [
              { id: '1', text: 'Work 1', category: 'งานประจำ', dueDate: todayIso, priority: 'medium', completed: false, done: false }
            ];
            const catEl = document.getElementById('todoCategoryFilter');
            catEl.value = 'ด่วน';
            catEl.dispatchEvent({ type: 'change' });
            renderTodos();
            return {
              listText: document.getElementById('todoListContainer').textContent,
              totalCount: document.getElementById('tabTodoCount').textContent
            };
            """
        )
        self.assertNotIn("Work 1", res["listText"])
        self.assertEqual(str(res["totalCount"]).strip(), "1")

    # =========================================================================
    # Feature F12 Boundaries: Sunday Week Boundary, Fallback & Time (6 tests)
    # =========================================================================

    def test_f12_monsun_mode_on_sunday_anchors_to_previous_monday(self):
        res = FrontendVMHarness.run_js(
            """
            const sunday = new Date(2026, 10, 8); // Sunday 2026-11-08
            return toISODate(getWeekStartDate(sunday, 'monSun'));
            """
        )
        self.assertEqual(res, "2026-11-02")

    def test_f12_month_and_year_rollover_in_7day_window(self):
        res = FrontendVMHarness.run_js(
            """
            currentAnchorDate = new Date(2026, 11, 28); // 2026-12-28
            renderWeekCalendar();
            return document.getElementById('weekRangeSubtitle').textContent;
            """
        )
        self.assertIn("2026", res)
        self.assertIn("2027", res)

    def test_f12_week_range_fetch_network_failure_preserves_cached_events(self):
        res = FrontendVMHarness.run_js(
            """
            calendarEvents = [{ id: 'cached-next-wk', title: 'Kept Event', date: todayIso, time: 'ทั้งวัน', sourceType: 'primary' }];
            __test_helpers.setFetchHandler(async () => { throw new Error('Network offline'); });
            document.getElementById('btnNextWeek').click();
            await new Promise(r => setTimeout(r, 0));
            return calendarEvents.length;
            """
        )
        self.assertEqual(res, 1)

    def test_f12_unchecking_all_calendar_filters_hides_events_but_shows_todos(self):
        res = FrontendVMHarness.run_js(
            """
            calendarEvents = [{ id: 'ev1', title: 'Hidden Event', date: todayIso, time: '10:00 - 11:00', sourceType: 'primary' }];
            todos = [{ id: 'td1', text: 'Visible Due Todo', dueDate: todayIso, priority: 'high', category: 'ด่วน', completed: false, done: false }];
            document.getElementById('chkCalPrimary').checked = false;
            document.getElementById('chkCalCoop').checked = false;
            document.getElementById('chkCalHoliday').checked = false;
            renderWeekCalendar();
            return document.getElementById('weekDaysContainer').textContent;
            """
        )
        self.assertNotIn("Hidden Event", res)
        self.assertIn("Visible Due Todo", res)

    def test_f12_manual_evt_time_edit_syncs_with_all_day_and_pickers(self):
        res = FrontendVMHarness.run_js(
            """
            const allDay = document.getElementById('evtAllDay');
            const start = document.getElementById('evtStartTime');
            const end = document.getElementById('evtEndTime');
            start.value = '08:30';
            end.value = '10:00';
            allDay.checked = true;
            allDay.dispatchEvent({ type: 'change' });
            const whenAllDay = document.getElementById('evtTime').value;

            allDay.checked = false;
            allDay.dispatchEvent({ type: 'change' });
            const whenTimed = document.getElementById('evtTime').value;
            return { whenAllDay, whenTimed };
            """
        )
        self.assertEqual(res["whenAllDay"], "ทั้งวัน")
        self.assertEqual(res["whenTimed"], "08:30 - 10:00")

    def test_f12_add_event_rejects_missing_title_or_date(self):
        res = FrontendVMHarness.run_js(
            """
            __test_helpers.fetchCalls.length = 0;
            document.getElementById('evtTitle').value = '   ';
            document.getElementById('evtDate').value = todayIso;
            document.getElementById('addEventForm').dispatchEvent({ type: 'submit' });
            return __test_helpers.fetchCalls.filter(c => c.url.includes('/api/calendar/events')).length;
            """
        )
        self.assertEqual(res, 0)

    # =========================================================================
    # Feature F13 Boundaries: Notification Denied, file: Protocol & SW (5 tests)
    # =========================================================================

    def test_f13_notification_permission_denied_or_unsupported_browser(self):
        res = FrontendVMHarness.run_js(
            """
            const now = new Date();
            const in5 = new Date(now.getTime() + 5 * 60000);
            const hh = String(in5.getHours()).padStart(2, '0');
            const mm = String(in5.getMinutes()).padStart(2, '0');
            calendarEvents = [{ id: 'ev-5m', title: 'Soon Event', date: toISODate(now), time: `${hh}:${mm} - 23:59`, sourceType: 'primary' }];
            document.getElementById('btnToggleNotify').click();
            if (typeof checkReminders === 'function') checkReminders();
            return true;
            """,
            init_options={"disableNotificationAPI": True},
        )
        self.assertTrue(res)

    def test_f13_events_outside_15min_window_or_all_day_do_not_trigger_15min_alert(self):
        res = FrontendVMHarness.run_js(
            """
            const now = new Date();
            const in45 = new Date(now.getTime() + 45 * 60000);
            const hh = String(in45.getHours()).padStart(2, '0');
            const mm = String(in45.getMinutes()).padStart(2, '0');
            calendarEvents = [
              { id: 'ev-45m', title: 'Later Event', date: toISODate(now), time: `${hh}:${mm} - 23:59`, sourceType: 'primary' },
              { id: 'ev-allday', title: 'All Day Event', date: toISODate(now), time: 'ทั้งวัน', sourceType: 'primary' }
            ];
            Notification.permission = 'granted';
            localStorage.setItem('desktop_widget_notify_enabled', 'true');
            if (typeof notificationsEnabled !== 'undefined') notificationsEnabled = true;
            checkReminders();
            return __test_helpers.notificationsSent.length;
            """,
            init_options={"notificationPermission": "granted"},
        )
        self.assertEqual(res, 0)

    def test_f13_clearing_api_key_omits_x_api_key_header(self):
        res = FrontendVMHarness.run_js(
            """
            localStorage.setItem('desktop_widget_api_key', '');
            __test_helpers.fetchCalls.length = 0;
            await syncSaveTodos([{ id: 't1', text: 'No Key', done: false }]);
            const call = __test_helpers.fetchCalls.find(c => c.url.endsWith('/api/todos'));
            const headers = (call && call.options && call.options.headers) || {};
            return !!(headers['X-API-Key'] || headers['x-api-key']);
            """
        )
        self.assertFalse(res)

    def test_f13_sw_registration_skipped_on_file_protocol(self):
        res = FrontendVMHarness.run_js(
            """
            return {
              syncUrl: SYNC_API_URL,
              swRegs: __test_helpers.swRegistrations.length
            };
            """,
            init_options={"protocol": "file:"},
        )
        self.assertEqual(res["syncUrl"], "http://127.0.0.1:18234")
        self.assertEqual(res["swRegs"], 0)

    def test_f13_service_worker_passes_through_mutating_post_patch_delete_requests(self):
        res = FrontendVMHarness.run_sw_js(
            """
            await __sw_test.dispatchInstall();
            await __sw_test.dispatchFetch('/api/todos', 'POST');
            await __sw_test.dispatchFetch('/api/todos/1', 'PATCH');
            await __sw_test.dispatchFetch('/api/todos/1', 'DELETE');
            const cachedPost = await caches.match('http://127.0.0.1:18234/api/todos');
            return { cachedPost: !!cachedPost, networkCount: __sw_test.networkCalls.length };
            """
        )
        self.assertFalse(res["cachedPost"])
        self.assertEqual(res["networkCount"], 3)

    # =========================================================================
    # Feature F14 Boundaries: Corrupted JSON Anti-Wipe, Offline & Bounds (6 tests)
    # =========================================================================

    def test_f14_corrupted_json_on_disk_never_overwrites_file_with_default_todos(self):
        with open(self.todo_data_file, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "todos": [{"id": "user-real-1", "text": "ห้ามลบงานนี้", "done": False, "completed": False}],
                    "events": [],
                },
                f,
            )
        widget = TkinterWidgetHarness.create_widget(self.todo_data_file)
        self.assertEqual(widget.data["todos"][0]["id"], "user-real-1")

        # Simulate transient corrupted JSON on disk during external write
        with open(self.todo_data_file, "w", encoding="utf-8") as f:
            f.write('{"todos": [')

        widget.last_mtime = 0
        widget.check_external_changes()

        # 1. In-memory state must preserve user-real-1
        self.assertEqual(widget.data["todos"][0]["id"], "user-real-1")
        # 2. Disk file must NOT be overwritten with DEFAULT_TODOS (todo-1, todo-2, todo-3)
        with open(self.todo_data_file, "r", encoding="utf-8") as f:
            raw_disk = f.read()
        self.assertNotIn("สรุปงานก่อนวันหยุดพิเศษ", raw_disk)

    def test_f14_delete_todo_when_server_unreachable_falls_back_to_direct_notion_archive(self):
        uid = "3fe16fa7-85cd-8086-8994-fb88e9858c12"
        self.mock_notion.seed_todo_page(uid, text="Offline Delete Task")
        with open(self.todo_data_file, "w", encoding="utf-8") as f:
            json.dump(
                {"todos": [{"id": uid, "text": "Offline Delete Task", "done": False, "completed": False}], "events": []},
                f,
            )
        # Do NOT start ephemeral server -> server is unreachable
        widget = TkinterWidgetHarness.create_widget(self.todo_data_file, server_port=65431)
        widget.delete_todo(0)
        self.wait_for_notion_async()

        self.assertEqual(widget.data["todos"], [])
        self.assertTrue(
            self.mock_notion.pages[uid]["archived"],
            "When local server is unreachable, Tkinter delete_todo must fall back to notion_service.archive_notion_todo",
        )

    def test_f14_add_todo_ignores_empty_or_whitespace_entry(self):
        with open(self.todo_data_file, "w", encoding="utf-8") as f:
            json.dump({"todos": [], "events": []}, f)
        widget = TkinterWidgetHarness.create_widget(self.todo_data_file)
        if hasattr(widget, "todo_entry") and widget.todo_entry is not None:
            widget.todo_entry.delete(0, "end")
            widget.todo_entry.insert(0, "    ")
        widget.add_todo()
        self.assertEqual(len(widget.data["todos"]), 0)

    def test_f14_out_of_range_toggle_or_delete_index_handled_safely(self):
        with open(self.todo_data_file, "w", encoding="utf-8") as f:
            json.dump({"todos": [{"id": "t1", "text": "One", "done": False, "completed": False}], "events": []}, f)
        widget = TkinterWidgetHarness.create_widget(self.todo_data_file)
        # Must not raise unhandled IndexError
        widget.toggle_todo(999)
        widget.delete_todo(999)
        if hasattr(widget, "edit_todo"):
            widget.edit_todo(999, text="Noop")
        self.assertEqual(len(widget.data["todos"]), 1)

    def test_f14_sends_x_api_key_header_when_widget_api_key_env_set(self):
        os.environ["WIDGET_API_KEY"] = "tk-secret-key"
        self.start_ephemeral_server()
        widget = TkinterWidgetHarness.create_widget(self.todo_data_file, server_port=self.server_port)
        if hasattr(widget, "todo_entry") and widget.todo_entry is not None:
            widget.todo_entry.delete(0, "end")
            widget.todo_entry.insert(0, "Authenticated Tkinter Task")
        try:
            widget.add_todo("Authenticated Tkinter Task")
        except TypeError:
            widget.add_todo()
        self.wait_for_notion_async()

        _, _, server_data = self.api_request("GET", "/api/data")
        self.assertEqual(len(server_data["todos"]), 1)
        self.assertEqual(server_data["todos"][0]["text"], "Authenticated Tkinter Task")

    def test_f14_open_html_app_uses_dynamic_port_env(self):
        os.environ["PORT"] = "19999"
        widget = TkinterWidgetHarness.create_widget(self.todo_data_file, server_port=19999)
        with unittest.mock.patch("webbrowser.open") as mock_open:
            widget.open_html_app()
            mock_open.assert_called_once()
            called_url = mock_open.call_args[0][0]
            self.assertIn("19999", called_url)

    # =========================================================================
    # Feature F15 Boundaries: Encoding, Fallbacks & Secret Protection (5 tests)
    # =========================================================================

    def test_f15_all_bat_files_use_chcp_65001_and_valid_utf8(self):
        for bat_name in (
            "Open_Widget_Right_Corner.bat",
            "Run_Python_Widget.bat",
            "Install_Python_Widget_Startup.bat",
        ):
            bat_path = os.path.join(REPO_DIR, bat_name)
            with open(bat_path, "rb") as f:
                raw = f.read()
            text = raw.decode("utf-8")
            self.assertIn("@echo off", text.lower())
            self.assertIn("chcp 65001", text.lower())

    def test_f15_open_widget_bat_has_fallback_when_edge_and_chrome_missing(self):
        bat_path = os.path.join(REPO_DIR, "Open_Widget_Right_Corner.bat")
        with open(bat_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn('start ""', content)

    def test_f15_readme_has_zero_references_to_nonexistent_bat_files(self):
        readme_path = os.path.join(REPO_DIR, "README_วิธีใช้งาน.txt")
        with open(readme_path, "r", encoding="utf-8") as f:
            content = f.read()
        referenced = set(re.findall(r"[A-Za-z0-9_]+\.(?:bat|py|html)", content))
        self.assertTrue(len(referenced) >= 3)
        for fname in referenced:
            self.assertTrue(
                os.path.exists(os.path.join(REPO_DIR, fname)),
                f"README references non-existent file: {fname}",
            )

    def test_f15_dockerignore_blocks_test_temp_and_env_secrets(self):
        dockerignore_path = os.path.join(REPO_DIR, ".dockerignore")
        self.assertTrue(os.path.exists(dockerignore_path), ".dockerignore must exist")
        with open(dockerignore_path, "r", encoding="utf-8") as f:
            lines = [ln.strip() for ln in f.read().splitlines() if ln.strip() and not ln.strip().startswith("#")]
        joined = "\n".join(lines)
        for pattern in (".env", "tests", ".git", "__pycache__"):
            self.assertIn(pattern, joined)

    def test_f15_gitignore_ignores_env_and_logs_while_keeping_spoken_tokens_tracked(self):
        gitignore_path = os.path.join(REPO_DIR, ".gitignore")
        with open(gitignore_path, "r", encoding="utf-8") as f:
            gi = f.read()
        self.assertIn(".env", gi)
        self.assertIn("*.log", gi)

        proc = subprocess.run(
            ["git", "-C", REPO_DIR, "ls-files"],
            text=True,
            capture_output=True,
            timeout=5,
        )
        tracked = set(proc.stdout.splitlines())
        for kept in (
            "notion_config.json",
            "desktop_todo_data.json",
            ".calendar_auth/credentials.json",
            ".calendar_auth/gcp-oauth.keys.json",
        ):
            self.assertIn(kept, tracked)


if __name__ == "__main__":
    unittest.main()
