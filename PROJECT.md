# Project: todo-calendar-cloud-widget

## Architecture

`todo-calendar-cloud-widget` is a zero-pip-dependency Hybrid To-Do List (2-Way Notion Sync) & 1-Week Google Calendar Widget supporting three runtime surfaces:
1. **Backend Sync Server & Cloud Web Host (`desktop_sync_server.py`)**: Multi-threaded Python standard-library HTTP server (`ThreadedHTTPServer`) serving the Web/PWA frontend and REST APIs, backed by thread-safe/process-safe local JSON storage (`desktop_todo_data.json`) and background synchronization threads for Notion (`notion_service.py`) and Google Calendar (`google_calendar_service.py`).
2. **Web / PWA Frontend (`desktop_todo_calendar_widget.html`, `manifest.json`, `sw.js`)**: Single-page responsive Glassmorphism web application (Tailwind CSS + Vanilla JS) supporting To-Do CRUD, inline editing, filtering/search/sorting, Notion setup/sync, 7-day Calendar view with range navigation & account/holiday filters, event creation, browser notifications, and offline PWA Service Worker support.
3. **Native Desktop Widget & Launchers (`desktop_widget_always_on_top.py`, `.bat` scripts)**: Always-on-top Tkinter desktop client synchronized with the backend REST API and atomic local cache, paired with Windows launcher/startup scripts and Docker/Render/Heroku cloud deployment manifests.

```
[Web / PWA Client] ──(HTTP REST API)──┐
                                      ▼
[Tkinter Desktop Widget] ──(REST / Atomic JSON)──► [desktop_sync_server.py]
                                                      ├──► [notion_service.py] ◄──► Notion API v2022-06-28
                                                      ├──► [google_calendar_service.py] ◄──► Google Calendar API v3
                                                      └──► [desktop_todo_data.json] (Atomic RLock + os.replace)
```

---

## Feature Inventory

Every feature discovered and required from the Phase 0 Survey (user requirements in `ORIGINAL_REQUEST.md`, `HANDOVER_FOR_ARGON.md`, and the 3 Explorer reports) is inventoried below with an explicit Milestone assignment. **Zero features are left unassigned.**

| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| F1 | **Token & Credential Preservation ("เอาspokenต่างๆไว้เลย")** | Keep existing Notion token (`notion_config.json`, `DEFAULT_TOKEN`) and Google Calendar OAuth files (`.calendar_auth/*`) intact and functional out-of-the-box while supporting `.env` / environment variable overrides (`NOTION_API_TOKEN`, `NOTION_DATABASE_ID`, `GOOGLE_CALENDAR_CRED_JSON`, `GOOGLE_CALENDAR_KEYS_JSON`, `TODO_DATA_FILE`, `NOTION_CONFIG_FILE`) and `.env.example`. | M1 | ORIGINAL_REQUEST, Survey 1 & 3 |
| F2 | **Port Check & Missing Import Fix (`urllib.request`)** | Import `urllib.request` and `urllib.parse` in `desktop_sync_server.py` so `is_port_in_use()` accurately detects running server instances via `/api/ping`. | M1 | HANDOVER_FOR_ARGON, Survey 1 & 3 |
| F3 | **Static File Security & Secret Protection** | Restrict `SyncRequestHandler.do_GET()` to an explicit safe allowlist (`/`, `/index.html`, `/desktop_todo_calendar_widget.html`, `/manifest.json`, `/sw.js`) and return `403 Forbidden` / `404 Not Found` for `notion_config.json`, `.calendar_auth/*`, `desktop_todo_data.json`, `.env`, and `*.py`. | M1 | HANDOVER_FOR_ARGON, Survey 1 |
| F4 | **Thread-Safe & Atomic Storage (`desktop_todo_data.json`)** | Protect all read-modify-write cycles with `threading.RLock()` (`update_data`), write JSON atomically via temp file + `os.replace()`, and serialize Notion syncs via `notion_sync_lock` to eliminate lost updates, truncated reads, and duplicate Notion page creation. | M1 | Survey 1 & 3 |
| F5 | **True 2-Way Notion Sync & Metadata Updates** | Push local creations, deletions (`archive_notion_todo`), completion toggles, and edits (`text`, `dueDate`, `priority`, `category`) to Notion on explicit user mutations (`POST /api/todos`, `PATCH /api/todos/<id>`, `DELETE /api/todos/<id>`), while letting remote Notion changes update local cache during periodic background sync (`sync_todos_with_notion`), plus cursor pagination (`has_more`) and `last_sync` tracking. | M1 | HANDOVER_FOR_ARGON, Survey 1 & 2 |
| F6 | **Google Calendar OAuth Fix, Timezone & Range Query** | Fix POSIX path expansion (`~/.calendar-mcp`), write refreshed OAuth tokens back to the loaded credential file, reuse valid cached tokens, fix midnight wrap-around (`23:30` + 1h -> next day `00:30` using `timedelta`), use explicit Bangkok timezone (`UTC+07:00`), support `?start=YYYY-MM-DD&days=N` on `GET /api/calendar/events`, and gracefully preserve cached/custom events when OAuth refresh fails (`invalid_grant`). | M1 | Survey 1, 2 & 3 |
| F7 | **Optional API Key Authentication & Health/Status Endpoints** | Support optional `WIDGET_API_KEY` env var (non-breaking when unset; validates `X-API-Key` or `Authorization: Bearer` when set), add `GET /api/health` and `GET /api/calendar/status`, and enrich `POST /api/todos` to return the reconciled `todos` list. | M1 | HANDOVER_FOR_ARGON, Survey 1 & 2 |
| F8 | **Frontend Sync Race Fix & Full-Field Diffing** | Replace fixed 350ms `isLocalSaving` timeout with monotonic `saveVersion` tracking, compare all fields (`id`, `text`, `done`, `dueDate`, `priority`, `category`, `notion_url`) in `syncFetchData()`, preserve `notion_url`, fix empty `data.events` update bug, and preserve offline custom events. | M2 | Survey 2 |
| F9 | **Frontend XSS Sanitization** | Replace unescaped `innerHTML` in `renderWeekCalendar()` and `#notionModal` with safe DOM construction (`textContent` / `escapeHtml`) for `ev.time`, `ev.title`, `td.text`, and `td.category`. | M2 | Survey 2 |
| F10 | **Inline Task Editing, Overdue Badges & Notion Link** | Allow editing existing tasks (`text`, `dueDate`, `priority`, `category`) in the Web UI, display a rose `⚠️ เลยกำหนด` badge on overdue active tasks (`dueDate < today`), and render a clickable `Notion ↗` link when `notion_url` is available. | M2 | Survey 2 |
| F11 | **Task Search, Category Filter & Sorting** | Add search input (`#todoSearchInput`), category filter selector (`ทั้งหมด`, `งานประจำ`, `ประชุม`, `ส่วนตัว`, `ด่วน`), and sort selector (Created/Default, Due Date, Priority) in the Web UI. | M2 | Survey 2 |
| F12 | **Calendar Week Range Fetching, Holiday Filter & Time Picker** | Add `#chkCalHoliday` (`วันหยุดไทย`) filter checkbox, request `?start=YYYY-MM-DD&days=7` when navigating weeks (`◀`/`▶`/`วันนี้`), and provide structured time inputs (`start`/`end` + `"ทั้งวัน"` checkbox) for creating calendar events. | M2 | Survey 2 |
| F13 | **Browser Notification Reminders, API Key UI & PWA Service Worker** | Add 15-minute upcoming event & due-today task browser/in-app notification reminders, optional API Key setting in `#setupModal`, Escape/backdrop modal closing, and a PWA Service Worker (`sw.js`) registered in `desktop_todo_calendar_widget.html`. | M2 | HANDOVER_FOR_ARGON, Survey 2 |
| F14 | **Tkinter Desktop Widget Sync, Anti-Corruption & Feature Parity** | Fix `desktop_widget_always_on_top.py` so JSON read errors or empty events never overwrite `desktop_todo_data.json` with `DEFAULT_TODOS`/`DEFAULT_EVENTS`; route CRUD (including delete & edit) through the local Sync Server API (with atomic file write fallback) so Tkinter deletions archive in Notion; run manual syncs asynchronously on background threads; add Priority, Category, Due Date, filter controls, and Notion sync button. | M3 | Survey 3 |
| F15 | **Batch Launchers, Deployment Configs & Thai Documentation** | Add `python.exe` fallback and `http://127.0.0.1:18234` launch support in `Open_Widget_Right_Corner.bat`, create `.dockerignore`, and update `README_วิธีใช้งาน.txt` to match actual `.bat` filenames and document all features & environment variables. | M3 | Survey 3 |
| F16 | **Comprehensive E2E Test Suite (Tiers 1–4) & Final Hardening (Tier 5)** | Build automated opaque-box test suite (`tests/run_e2e_tests.py`) covering Tiers 1–4 using isolated temp files (`TODO_DATA_FILE`, `NOTION_CONFIG_FILE`) and mock/read-only-live checks so live Notion/Google Calendar data is never corrupted, pass 100% of E2E tests, and complete Tier 5 white-box adversarial coverage hardening. | M4 (and E2E Track) | Project Pattern, Survey 3 |

---

## Milestones

| # | Name | Scope | Dependencies | Status |
|---|------|-------|--------------|--------|
| M1 | `m1_backend_sync` | Backend Server, Atomic Storage, Secret Protection, True 2-Way Notion Sync, Google Calendar Service Fixes, `.env` Loader & `.env.example` (Features F1–F7) | none | IN_PROGRESS |
| M2 | `m2_frontend_pwa` | Web/PWA Frontend Sync Race Fix, XSS Fix, Inline Task Edit, Search/Filter/Sort, Calendar Range & Holiday Filter, Notifications, Service Worker `sw.js` (Features F8–F13) | none (uses Interface Contracts below) | IN_PROGRESS |
| M3 | `m3_desktop_devops` | Native Tkinter Widget Upgrade (API Sync, Anti-Corruption, Async Refresh, Metadata/Filter UI), `.bat` Scripts, `.dockerignore`, `README_วิธีใช้งาน.txt` (Features F14–F15) | none (uses Interface Contracts below) | IN_PROGRESS |
| E2E | `e2e_testing_track` | Opaque-box E2E Test Infrastructure (`TEST_INFRA.md`), 4-Tier Automated Test Suite (`tests/`), and `TEST_READY.md` covering F1–F15 without mutating live user data | none (uses Interface Contracts below) | IN_PROGRESS |
| M4 | `m4_final_e2e_hardening` | Phase 1: 100% E2E Test Suite Pass (Tiers 1–4) across integrated M1+M2+M3. Phase 2: Tier 5 White-Box Adversarial Coverage Hardening | M1, M2, M3, E2E | PLANNED |

---

## Interface Contracts

### 1. Data Schema (`desktop_todo_data.json` & `/api/data`)
```json
{
  "todos": [
    {
      "id": "string (Notion 36-char UUID or temporary 'todo-<ms>')",
      "text": "string",
      "completed": true,
      "done": true,
      "dueDate": "YYYY-MM-DD",
      "priority": "high | medium | low",
      "category": "string (e.g. 'งานประจำ', 'ประชุม', 'ส่วนตัว', 'ด่วน')",
      "notion_url": "string (optional URL to Notion page)"
    }
  ],
  "events": [
    {
      "id": "string",
      "title": "string",
      "date": "YYYY-MM-DD",
      "time": "string ('ทั้งวัน' or 'HH:MM - HH:MM')",
      "calendar": "string (email or calendar ID)",
      "sourceType": "primary | coop | holiday | custom",
      "htmlLink": "string (optional URL)"
    }
  ]
}
```
- Both `completed` and `done` booleans MUST be synchronized (`completed == done`) in all backend responses and frontend/Tkinter payloads for 100% backward compatibility.

### 2. Backend REST API Contract (`desktop_sync_server.py`)
- **`GET /`**, **`GET /index.html`**, **`GET /desktop_todo_calendar_widget.html`**: Serves `desktop_todo_calendar_widget.html` (`200 OK`, `text/html; charset=utf-8`).
- **`GET /manifest.json`**: Serves `manifest.json` (`200 OK`, `application/manifest+json` or `application/json`).
- **`GET /sw.js`**: Serves `sw.js` (`200 OK`, `application/javascript`).
- **`GET /<sensitive_file>`** (e.g. `/notion_config.json`, `/.calendar_auth/*`, `/desktop_todo_data.json`, `/.env`, `/*.py`): MUST return `403 Forbidden` or `404 Not Found` (never expose raw credentials or source files over HTTP).
- **`GET /api/ping`**: Returns `200 OK` JSON `{"status": "ok", ...}`.
- **`GET /api/health`**: Returns `200 OK` JSON `{"status": "ok", "notion_connected": bool, "calendar_status": "ok|cached|error", "last_notion_sync": str|null, "last_calendar_sync": str|null, "counts": {"todos": int, "events": int}}`.
- **`GET /api/data`** and **`GET /api/todos`**: Returns `200 OK` JSON `{"todos": [...], "events": [...]}`.
- **`POST /api/todos`**:
  - Request body: JSON array of todo objects `[...]` (or `{"todos": [...]}`).
  - Behavior: Atomically updates `todos` in `desktop_todo_data.json`, triggers Notion diff/push (`async_push_todo_changes_to_notion` for creations, deletions, completion toggles, and `text`/`dueDate`/`priority`/`category` edits), and returns `200 OK` JSON `{"status": "success", "count": len(todos), "todos": todos}`.
- **`PATCH /api/todos/<id>`** (and **`PUT /api/todos/<id>`**):
  - Request body: JSON object with any subset of `{text, completed, done, dueDate, priority, category}`.
  - Behavior: Atomically updates the matching todo in `desktop_todo_data.json`, pushes update to Notion if UUID, and returns `200 OK` JSON `{"status": "success", "todo": updated_todo, "todos": todos}` (or `404` if not found).
- **`DELETE /api/todos/<id>`**:
  - Behavior: Atomically removes the matching todo from `desktop_todo_data.json`, archives it in Notion if UUID (`archive_notion_todo`), and returns `200 OK` JSON `{"status": "success", "deleted_id": id, "todos": todos}` (or `404` if not found).
- **`GET /api/notion/status`**: Returns `200 OK` JSON `{installed, connected, bot_name, workspace, database_id, databases_found, pages_found, last_sync}`.
- **`POST /api/notion/sync`**: Runs `sync_notion_todos_now()` and returns `200 OK` JSON `{"status": "success", "todos": synced_todos}`.
- **`POST /api/notion/connect`**: Connects to `database_id` / `page_id` or auto-discovers, saves config, runs sync, and returns `200 OK` JSON `{"status": "connected", "database_id": db_id, "todos": synced_todos}`.
- **`GET /api/calendar/events`**:
  - Optional query params: `?start=YYYY-MM-DD&days=7` (or `&end=YYYY-MM-DD`).
  - Behavior: If default current 7-day window, runs `sync_google_calendar_now()` (falling back to cached `desktop_todo_data.json["events"]` if Google OAuth refresh fails so events are never wiped to `[]` on error). If custom `start` range is requested, queries `fetch_all_7day_events(start_date=start, days=days)` (or filters cached events on error). Returns `200 OK` JSON array of event objects `[...]`.
- **`GET /api/calendar/status`**: Returns `200 OK` JSON `{"status": "connected|cached_fallback|error", "error": str|null, "calendars": [...]}`.
- **`POST /api/calendar/events`**:
  - Request body: `{title, date, time?, calendar?, description?}`.
  - Behavior: Validates `title` and `date` (`400` if missing). Calls `google_calendar_service.create_event(...)`. Returns `200 OK` JSON `{"status": "success", "event": created_ev}`.
- **`POST /api/events`**:
  - Request body: JSON array of event objects `[...]`.
  - Behavior: Atomically updates `events` in `desktop_todo_data.json`, returns `200 OK` JSON `{"status": "success", "count": len(events)}`.
- **Optional API Key Authentication (`WIDGET_API_KEY`)**:
  - If `os.environ.get("WIDGET_API_KEY")` is empty/unset (default): all requests are allowed without authentication.
  - If `WIDGET_API_KEY` is set to a non-empty string: `POST`, `PUT`, `PATCH`, `DELETE` requests to `/api/*` require header `X-API-Key: <WIDGET_API_KEY>` or `Authorization: Bearer <WIDGET_API_KEY>`, returning `401 Unauthorized` JSON `{"status": "error", "message": "Unauthorized"}` if missing/invalid.

### 3. Environment Variables & Test Isolation Contract
- `PORT`: Server port (default `18234`).
- `HOST`: Server host (default `"0.0.0.0"` if `PORT` in env else `"127.0.0.1"`).
- `TODO_DATA_FILE`: Override path to `desktop_todo_data.json` (used by automated tests to isolate storage in `tempfile`).
- `NOTION_CONFIG_FILE`: Override path to `notion_config.json` (used by automated tests).
- `NOTION_API_TOKEN` / `NOTION_TOKEN`: Override Notion token (falls back to `notion_config.json` -> `DEFAULT_TOKEN`).
- `NOTION_DATABASE_ID`: Override Notion database ID (falls back to `notion_config.json` -> auto-discovery).
- `GOOGLE_CALENDAR_CRED_JSON` / `GOOGLE_CALENDAR_KEYS_JSON`: Override Google OAuth JSON strings (falls back to `.calendar_auth/*` -> `~/.calendar-mcp/*`).
- `WIDGET_API_KEY`: Optional API key for mutating endpoints (default unset/disabled).
- `DISABLE_BACKGROUND_WORKERS`: If set to `"1"` or `"true"`, `desktop_sync_server.py` skips starting the infinite background polling threads during unit/integration test imports unless explicitly started.

---

## Code Layout & Exclusive File Ownership

Repository root: `/home/antigravity/todo-calendar-cloud-widget`

| Milestone / Track | Exclusively Owned Files (Read/Write) | Protected Files (Read-Only — NEVER Modify or Delete) |
|---|---|---|
| **M1 (`m1_backend_sync`)** | `desktop_sync_server.py`<br>`notion_service.py`<br>`google_calendar_service.py`<br>`.env.example` | `notion_config.json`<br>`.calendar_auth/credentials.json`<br>`.calendar_auth/gcp-oauth.keys.json`<br>`desktop_todo_data.json` |
| **M2 (`m2_frontend_pwa`)** | `desktop_todo_calendar_widget.html`<br>`manifest.json`<br>`sw.js` | Same as above |
| **M3 (`m3_desktop_devops`)** | `desktop_widget_always_on_top.py`<br>`Open_Widget_Right_Corner.bat`<br>`Run_Python_Widget.bat`<br>`Install_Python_Widget_Startup.bat`<br>`README_วิธีใช้งาน.txt`<br>`.dockerignore` | Same as above |
| **E2E (`e2e_testing_track`)** | `tests/**` (all test files & runners)<br>`TEST_INFRA.md`<br>`TEST_READY.md` | Same as above (Tests MUST use `TODO_DATA_FILE` and `NOTION_CONFIG_FILE` in `tempfile`!) |
| **M4 (`m4_final_e2e_hardening`)** | All project files (runs only after M1, M2, M3, and E2E have completed) | `notion_config.json`, `.calendar_auth/*`, `desktop_todo_data.json` |
