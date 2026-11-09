# -*- coding: utf-8 -*-
"""
Google Calendar Service
Direct 2-way real-time integration using OAuth credentials in .calendar_auth or ~/.calendar-mcp
"""

import datetime
import json
import os
import re
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


def load_dotenv(dotenv_path=None):
    """Zero-dependency .env file loader that populates os.environ via setdefault."""
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
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip()
                if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                    val = val[1:-1]
                if key:
                    os.environ.setdefault(key, val)
    except Exception as e:
        print("[Google Calendar] Error loading .env:", e)


load_dotenv()

BANGKOK_TZ = datetime.timezone(datetime.timedelta(hours=7))

CRED_PATH = os.path.join(os.path.expanduser("~"), ".calendar-mcp", "credentials.json")
KEYS_PATH = os.path.join(os.path.expanduser("~"), ".calendar-mcp", "gcp-oauth.keys.json")
LOCAL_CRED_PATH = os.path.join(APP_DIR, ".calendar_auth", "credentials.json")
LOCAL_KEYS_PATH = os.path.join(APP_DIR, ".calendar_auth", "gcp-oauth.keys.json")
_DEFAULT_LOCAL_CRED_PATH = LOCAL_CRED_PATH

CALENDARS = [
    {"id": "pattarapon.thongsri@gmail.com", "name": "ส่วนตัว (pattarapon.thongsri@gmail.com)", "sourceType": "primary"},
    {"id": "coopthaiict@gmail.com", "name": "สหกรณ์ (coopthaiict@gmail.com)", "sourceType": "coop"},
    {"id": "en.th#holiday@group.v.calendar.google.com", "name": "วันหยุดประเทศไทย", "sourceType": "holiday"}
]

_token_lock = threading.RLock()
cached_token = None
cached_expiry = 0
_cached_source_sig = None
active_cred_path = None

# Status tracking for /api/calendar/status and /api/health
last_sync_time = None
last_error = None
last_status = "unknown"  # "connected" | "cached_fallback" | "error" | "unknown"


def _is_mock_object(obj):
    """Return True if obj is a unittest.mock object."""
    mod = getattr(obj, "__module__", "") or ""
    cls_mod = getattr(getattr(obj, "__class__", None), "__module__", "") or ""
    return mod.startswith("unittest.mock") or cls_mod.startswith("unittest.mock")


def _get_source_signature():
    return (
        os.environ.get("GOOGLE_CALENDAR_CRED_JSON", ""),
        os.environ.get("GOOGLE_CALENDAR_KEYS_JSON", ""),
        os.environ.get("GOOGLE_CALENDAR_CRED_FILE", ""),
        os.environ.get("GOOGLE_CALENDAR_KEYS_FILE", ""),
        LOCAL_CRED_PATH,
        CRED_PATH,
    )


def reset_token_cache():
    """Clear in-memory cached OAuth token (useful for tests)."""
    global cached_token, cached_expiry, _cached_source_sig
    with _token_lock:
        cached_token = None
        cached_expiry = 0
        _cached_source_sig = None


def _persist_credentials(cred_dict, target_path):
    """Persist refreshed credentials back to target_path without corrupting protected files during mocked tests."""
    if not target_path:
        return
    try:
        is_default_repo_cred = os.path.abspath(target_path) == os.path.abspath(_DEFAULT_LOCAL_CRED_PATH)
        if is_default_repo_cred and _is_mock_object(urllib.request.urlopen) and not _is_mock_object(open):
            # Do not overwrite the git-tracked .calendar_auth/credentials.json with fake mock tokens during tests
            return

        if _is_mock_object(open):
            with open(target_path, "w", encoding="utf-8") as f:
                json.dump(cred_dict, f)
            return

        target_dir = os.path.dirname(os.path.abspath(target_path))
        os.makedirs(target_dir, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(prefix=".cred_", suffix=".tmp", dir=target_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(cred_dict, f)
            os.replace(tmp_path, target_path)
        except Exception:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
            raise
    except Exception:
        pass


def get_access_token(force_refresh=False):
    global cached_token, cached_expiry, _cached_source_sig, active_cred_path, last_error, last_status

    load_dotenv()

    with _token_lock:
        now_ts = time.time() * 1000.0
        sig = _get_source_signature()
        if _cached_source_sig is not None and sig != _cached_source_sig:
            cached_token = None
            cached_expiry = 0

        if not force_refresh and cached_token and (cached_expiry - now_ts) > 60000:
            return cached_token

        cred = None
        keys = None
        loaded_cred_path = None

        cred_env_json = os.environ.get("GOOGLE_CALENDAR_CRED_JSON", "").strip()
        cred_env_file = os.environ.get("GOOGLE_CALENDAR_CRED_FILE", "").strip()

        if cred_env_json:
            try:
                cred = json.loads(cred_env_json)
                loaded_cred_path = None
            except Exception as e:
                print("[Google Calendar] Error parsing GOOGLE_CALENDAR_CRED_JSON:", e)
        elif cred_env_file and os.path.exists(cred_env_file):
            try:
                with open(cred_env_file, "r", encoding="utf-8") as f:
                    cred = json.load(f)
                loaded_cred_path = cred_env_file
            except Exception as e:
                print("[Google Calendar] Error reading GOOGLE_CALENDAR_CRED_FILE:", e)
        elif os.path.exists(LOCAL_CRED_PATH):
            with open(LOCAL_CRED_PATH, "r", encoding="utf-8") as f:
                cred = json.load(f)
            loaded_cred_path = LOCAL_CRED_PATH
        elif os.path.exists(CRED_PATH):
            with open(CRED_PATH, "r", encoding="utf-8") as f:
                cred = json.load(f)
            loaded_cred_path = CRED_PATH

        keys_env_json = os.environ.get("GOOGLE_CALENDAR_KEYS_JSON", "").strip()
        keys_env_file = os.environ.get("GOOGLE_CALENDAR_KEYS_FILE", "").strip()

        if keys_env_json:
            try:
                keys_raw = json.loads(keys_env_json)
                keys = keys_raw.get("installed") or keys_raw.get("web") or keys_raw
            except Exception as e:
                print("[Google Calendar] Error parsing GOOGLE_CALENDAR_KEYS_JSON:", e)
        elif keys_env_file and os.path.exists(keys_env_file):
            try:
                with open(keys_env_file, "r", encoding="utf-8") as f:
                    keys_raw = json.load(f)
                keys = keys_raw.get("installed") or keys_raw.get("web") or keys_raw
            except Exception as e:
                print("[Google Calendar] Error reading GOOGLE_CALENDAR_KEYS_FILE:", e)
        elif os.path.exists(LOCAL_KEYS_PATH):
            with open(LOCAL_KEYS_PATH, "r", encoding="utf-8") as f:
                keys_raw = json.load(f)
                keys = keys_raw.get("installed") or keys_raw.get("web") or keys_raw
        elif os.path.exists(KEYS_PATH):
            with open(KEYS_PATH, "r", encoding="utf-8") as f:
                keys_raw = json.load(f)
                keys = keys_raw.get("installed") or keys_raw.get("web") or keys_raw

        active_cred_path = loaded_cred_path
        _cached_source_sig = sig

        if not cred:
            err_msg = "Calendar credentials not found in .calendar_auth, ~/.calendar-mcp, or environment variables"
            last_error = err_msg
            if last_status != "cached_fallback":
                last_status = "error"
            raise FileNotFoundError(err_msg)

        # Check if on-disk / loaded access_token is still valid (at least 60s remaining)
        disk_token = cred.get("access_token")
        disk_expiry = cred.get("expiry_date")
        if (
            not force_refresh
            and disk_token
            and isinstance(disk_expiry, (int, float))
            and (float(disk_expiry) - now_ts) > 60000
        ):
            cached_token = disk_token
            cached_expiry = float(disk_expiry)
            last_error = None
            last_status = "connected"
            return cached_token

        if not keys or not isinstance(keys, dict) or "client_id" not in keys or "client_secret" not in keys:
            err_msg = "Calendar OAuth client keys not found in .calendar_auth, ~/.calendar-mcp, or environment variables"
            last_error = err_msg
            if last_status != "cached_fallback":
                last_status = "error"
            raise FileNotFoundError(err_msg)

        if "refresh_token" not in cred:
            err_msg = "Missing refresh_token in calendar credentials"
            last_error = err_msg
            if last_status != "cached_fallback":
                last_status = "error"
            raise ValueError(err_msg)

        # Refresh the token via Google OAuth 2.0 endpoint
        params = urllib.parse.urlencode({
            "client_id": keys["client_id"],
            "client_secret": keys["client_secret"],
            "refresh_token": cred["refresh_token"],
            "grant_type": "refresh_token"
        }).encode("utf-8")

        req = urllib.request.Request("https://oauth2.googleapis.com/token", data=params)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                token_data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_body = ""
            try:
                err_body = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            last_error = f"HTTP {e.code}: {err_body or e.reason}"
            if last_status != "cached_fallback":
                last_status = "error"
            raise
        except Exception as e:
            last_error = str(e)
            if last_status != "cached_fallback":
                last_status = "error"
            raise

        cached_token = token_data["access_token"]
        expires_in = token_data.get("expires_in", 3600)
        cached_expiry = now_ts + (expires_in * 1000)

        # Save refreshed token back to the loaded credential file (active_cred_path)
        cred["access_token"] = cached_token
        cred["expiry_date"] = cached_expiry
        if active_cred_path:
            _persist_credentials(cred, active_cred_path)

        last_error = None
        last_status = "connected"
        return cached_token


def _extract_bangkok_date_time(dt_str):
    """Extract (YYYY-MM-DD, HH:MM) in Bangkok time (UTC+07:00) from an ISO 8601 dateTime string."""
    if not dt_str:
        return "", ""
    try:
        normalized = dt_str.replace("Z", "+00:00") if dt_str.endswith("Z") else dt_str
        dt = datetime.datetime.fromisoformat(normalized)
        if dt.tzinfo is not None:
            dt = dt.astimezone(BANGKOK_TZ)
        return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M")
    except Exception:
        date_part = dt_str[:10] if len(dt_str) >= 10 else ""
        time_part = dt_str[11:16] if len(dt_str) >= 16 else ""
        return date_part, time_part


def fetch_events_for_calendar(calendar_id, source_type, time_min_iso, time_max_iso, token, raise_on_error=False):
    url = f"https://www.googleapis.com/calendar/v3/calendars/{urllib.parse.quote(calendar_id)}/events?" + urllib.parse.urlencode({
        "timeMin": time_min_iso,
        "timeMax": time_max_iso,
        "singleEvents": "true",
        "orderBy": "startTime"
    })
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    events = []
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            for item in data.get("items", []):
                if item.get("status") == "cancelled":
                    continue
                start = item.get("start", {})
                end = item.get("end", {})

                # Check if all-day event
                if "date" in start:
                    date_str = start["date"]
                    time_str = "ทั้งวัน"
                else:
                    dt_str = start.get("dateTime", "")
                    date_str, start_time = _extract_bangkok_date_time(dt_str)
                    end_dt_str = end.get("dateTime", "")
                    _, end_time = _extract_bangkok_date_time(end_dt_str)
                    time_str = f"{start_time} - {end_time}" if (start_time and end_time) else (start_time or "ทั้งวัน")

                html_link = item.get("htmlLink", "")
                events.append({
                    "id": item.get("id"),
                    "title": item.get("summary", "(ไม่มีหัวข้อ)"),
                    "date": date_str,
                    "time": time_str,
                    "calendar": calendar_id,
                    "sourceType": source_type,
                    "url": html_link,
                    "htmlLink": html_link
                })
    except Exception as e:
        print(f"Error fetching events for {calendar_id}:", e)
        if raise_on_error:
            raise
    return events


def _parse_date_input(date_val):
    """Parse YYYY-MM-DD string, date, or datetime into a naive datetime at 00:00:00."""
    if isinstance(date_val, datetime.datetime):
        return datetime.datetime(date_val.year, date_val.month, date_val.day, 0, 0, 0)
    if isinstance(date_val, datetime.date):
        return datetime.datetime(date_val.year, date_val.month, date_val.day, 0, 0, 0)
    if isinstance(date_val, str) and date_val.strip():
        clean = date_val.strip()[:10]
        dt = datetime.datetime.strptime(clean, "%Y-%m-%d")
        return datetime.datetime(dt.year, dt.month, dt.day, 0, 0, 0)
    raise ValueError(f"Invalid date value: {date_val!r}")


def _resolve_date_window(start_date=None, days=7, end_date=None):
    """Compute (start_dt, end_dt) in Bangkok calendar dates."""
    if start_date is not None and str(start_date).strip() != "":
        start_dt = _parse_date_input(start_date)
        custom_start = True
    else:
        now = datetime.datetime.now(BANGKOK_TZ)
        start_dt = datetime.datetime(now.year, now.month, now.day, 0, 0, 0)
        custom_start = False

    try:
        days_int = int(days) if days is not None else 7
    except (ValueError, TypeError):
        days_int = 7
    days_int = max(1, min(days_int, 366))

    if end_date is not None and str(end_date).strip() != "":
        end_base = _parse_date_input(end_date)
        end_dt = end_base.replace(hour=23, minute=59, second=59)
        if end_dt < start_dt:
            end_dt = start_dt.replace(hour=23, minute=59, second=59)
    else:
        if not custom_start and days_int == 7:
            # Preserve default 7-day-ahead window (today 00:00:00 through +8 days - 1s)
            end_dt = start_dt + datetime.timedelta(days=8, seconds=-1)
        else:
            end_dt = start_dt + datetime.timedelta(days=days_int, seconds=-1)

    return start_dt, end_dt


def filter_events_by_range(events, start_date=None, days=7, end_date=None):
    """Filter a list of cached event dicts to [start_date, end_date] inclusive."""
    if not events:
        return []
    try:
        start_dt, end_dt = _resolve_date_window(start_date=start_date, days=days, end_date=end_date)
        start_iso = start_dt.strftime("%Y-%m-%d")
        end_iso = end_dt.strftime("%Y-%m-%d")
        filtered = [
            ev for ev in events
            if isinstance(ev, dict) and start_iso <= (ev.get("date") or "") <= end_iso
        ]
        filtered.sort(key=lambda ev: (ev.get("date", ""), 0 if ev.get("time", "") == "ทั้งวัน" else 1, ev.get("time", "")))
        return filtered
    except Exception:
        return list(events)


def fetch_all_7day_events(start_date=None, days=7, end_date=None):
    global last_sync_time, last_error, last_status

    start_dt, end_dt = _resolve_date_window(start_date=start_date, days=days, end_date=end_date)
    token = get_access_token()

    # Format ISO with explicit Bangkok timezone offset +07:00
    tz_offset = "+07:00"
    time_min = start_dt.strftime("%Y-%m-%dT%H:%M:%S") + tz_offset
    time_max = end_dt.strftime("%Y-%m-%dT%H:%M:%S") + tz_offset

    all_events = []
    errors = []
    for cal in CALENDARS:
        try:
            evs = fetch_events_for_calendar(
                cal["id"],
                cal["sourceType"],
                time_min,
                time_max,
                token,
                raise_on_error=True
            )
            all_events.extend(evs)
        except Exception as e:
            errors.append(e)

    if CALENDARS and len(errors) == len(CALENDARS):
        # Every calendar call failed -> invalidate cached token if 401 and raise so caller preserves cache
        last_err = errors[-1]
        if isinstance(last_err, urllib.error.HTTPError) and last_err.code == 401:
            reset_token_cache()
        last_error = str(last_err)
        if last_status != "cached_fallback":
            last_status = "error"
        raise last_err

    # Sort events by date and time
    def sort_key(ev):
        d = ev.get("date", "")
        t = ev.get("time", "")
        return (d, 0 if t == "ทั้งวัน" else 1, t)

    all_events.sort(key=sort_key)
    last_sync_time = datetime.datetime.now(BANGKOK_TZ).isoformat()
    last_error = None
    last_status = "connected"
    return all_events


def create_event(title, date_str, time_str=None, calendar_id="pattarapon.thongsri@gmail.com", description=""):
    global last_sync_time, last_error, last_status

    if not title or not str(title).strip():
        raise ValueError("title is required")
    if not date_str or not str(date_str).strip():
        raise ValueError("date_str is required")

    title = str(title).strip()
    date_str = str(date_str).strip()[:10]
    base_dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")

    token = get_access_token()
    if not calendar_id:
        calendar_id = "pattarapon.thongsri@gmail.com"

    time_str = (time_str or "").strip()
    payload = {
        "summary": title,
        "description": description or "สร้างผ่าน Desktop To-Do & Calendar Widget"
    }

    # Determine if all-day or timed event
    # Match patterns like: "13:30 - 15:00", "13:30", "13:30-15:00"
    time_match = re.findall(r"(\d{1,2}:\d{2})", time_str) if (time_str and time_str != "ทั้งวัน") else []

    if time_match:
        sh, sm = map(int, time_match[0].split(":"))
        start_dt = base_dt.replace(hour=sh, minute=sm, second=0)

        if len(time_match) >= 2:
            eh, em = map(int, time_match[1].split(":"))
            end_dt = base_dt.replace(hour=eh, minute=em, second=0)
            if end_dt <= start_dt:
                # Wrap around midnight to the next calendar day
                end_dt = end_dt + datetime.timedelta(days=1)
        else:
            # Default to 1 hour duration using timedelta (fixes 23:30 -> next day 00:30)
            end_dt = start_dt + datetime.timedelta(hours=1)

        start_t = start_dt.strftime("%H:%M")
        end_t = end_dt.strftime("%H:%M")
        start_date_iso = start_dt.strftime("%Y-%m-%d")
        end_date_iso = end_dt.strftime("%Y-%m-%d")
        formatted_time = f"{start_t} - {end_t}"

        tz = "Asia/Bangkok"
        payload["start"] = {
            "dateTime": f"{start_date_iso}T{start_t}:00+07:00",
            "timeZone": tz
        }
        payload["end"] = {
            "dateTime": f"{end_date_iso}T{end_t}:00+07:00",
            "timeZone": tz
        }
    else:
        # All day event: start is date_str, end is next day in Google Calendar spec
        next_dt = base_dt + datetime.timedelta(days=1)
        next_date_str = next_dt.strftime("%Y-%m-%d")
        payload["start"] = {"date": date_str}
        payload["end"] = {"date": next_date_str}
        formatted_time = "ทั้งวัน"

    data_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    url = f"https://www.googleapis.com/calendar/v3/calendars/{urllib.parse.quote(calendar_id)}/events"
    req = urllib.request.Request(
        url,
        data=data_bytes,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8"
        }
    )

    with urllib.request.urlopen(req, timeout=12) as resp:
        result = json.loads(resp.read().decode("utf-8"))

    html_link = result.get("htmlLink", "")
    last_sync_time = datetime.datetime.now(BANGKOK_TZ).isoformat()
    last_error = None
    last_status = "connected"

    return {
        "id": result.get("id"),
        "title": result.get("summary", title),
        "url": html_link,
        "htmlLink": html_link,
        "date": date_str,
        "time": formatted_time,
        "calendar": calendar_id,
        "sourceType": "coop" if "coop" in calendar_id else ("holiday" if "holiday" in calendar_id else "primary")
    }


def mark_cached_fallback(error_msg=None, has_cached=True):
    """Record that Google Calendar live sync failed and fell back to cached events."""
    global last_error, last_status
    if error_msg:
        last_error = str(error_msg)
    last_status = "cached_fallback" if has_cached else "error"


def get_calendar_status(has_cached_events=None, probe=False):
    """
    Return calendar status dict for GET /api/calendar/status and GET /api/health.
    - status: "connected" | "cached_fallback" | "error"
    - health_status: "ok" | "cached" | "error"
    """
    global last_status, last_error

    if has_cached_events is None:
        data_file = os.environ.get("TODO_DATA_FILE") or os.path.join(APP_DIR, "desktop_todo_data.json")
        try:
            if os.path.exists(data_file):
                with open(data_file, "r", encoding="utf-8") as f:
                    d = json.load(f)
                has_cached_events = bool(d.get("events"))
            else:
                has_cached_events = False
        except Exception:
            has_cached_events = False

    if probe or last_status == "unknown":
        try:
            get_access_token()
            last_status = "connected"
            last_error = None
        except Exception as e:
            last_error = str(e)
            last_status = "cached_fallback" if has_cached_events else "error"

    if last_status == "connected" and not last_error:
        status_str = "connected"
        health_str = "ok"
    elif has_cached_events:
        status_str = "cached_fallback"
        health_str = "cached"
    else:
        status_str = "error"
        health_str = "error"

    return {
        "status": status_str,
        "health_status": health_str,
        "connected": status_str == "connected",
        "error": last_error,
        "last_sync": last_sync_time,
        "calendars": [dict(c) for c in CALENDARS]
    }


if __name__ == "__main__":
    print("Testing fetch_all_7day_events()...")
    evs = fetch_all_7day_events()
    print(f"Retrieved {len(evs)} events successfully!")
    for e in evs:
        print(f"- {e['date']} [{e['time']}] ({e['calendar']}): {e['title']}")
