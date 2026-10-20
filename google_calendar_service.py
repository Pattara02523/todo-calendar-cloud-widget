# -*- coding: utf-8 -*-
"""
Google Calendar Service
Direct 2-way real-time integration using OAuth credentials in ~/.calendar-mcp
"""

import datetime
import json
import os
import re
import sys
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
CRED_PATH = os.path.expanduser(r"~\.calendar-mcp\credentials.json")
KEYS_PATH = os.path.expanduser(r"~\.calendar-mcp\gcp-oauth.keys.json")
LOCAL_CRED_PATH = os.path.join(APP_DIR, ".calendar_auth", "credentials.json")
LOCAL_KEYS_PATH = os.path.join(APP_DIR, ".calendar_auth", "gcp-oauth.keys.json")

CALENDARS = [
    {"id": "pattarapon.thongsri@gmail.com", "name": "ส่วนตัว (pattarapon.thongsri@gmail.com)", "sourceType": "primary"},
    {"id": "coopthaiict@gmail.com", "name": "สหกรณ์ (coopthaiict@gmail.com)", "sourceType": "coop"},
    {"id": "en.th#holiday@group.v.calendar.google.com", "name": "วันหยุดประเทศไทย", "sourceType": "holiday"}
]

cached_token = None
cached_expiry = 0


def get_access_token():
    global cached_token, cached_expiry
    now_ts = datetime.datetime.now().timestamp() * 1000
    if cached_token and (cached_expiry - now_ts) > 60000:
        return cached_token

    cred = None
    keys = None

    if os.environ.get("GOOGLE_CALENDAR_CRED_JSON"):
        try:
            cred = json.loads(os.environ["GOOGLE_CALENDAR_CRED_JSON"])
        except Exception as e:
            print("[Google Calendar] Error parsing GOOGLE_CALENDAR_CRED_JSON:", e)
    elif os.path.exists(LOCAL_CRED_PATH):
        with open(LOCAL_CRED_PATH, "r", encoding="utf-8") as f:
            cred = json.load(f)
    elif os.path.exists(CRED_PATH):
        with open(CRED_PATH, "r", encoding="utf-8") as f:
            cred = json.load(f)

    if os.environ.get("GOOGLE_CALENDAR_KEYS_JSON"):
        try:
            keys_raw = json.loads(os.environ["GOOGLE_CALENDAR_KEYS_JSON"])
            keys = keys_raw.get("installed", keys_raw)
        except Exception as e:
            print("[Google Calendar] Error parsing GOOGLE_CALENDAR_KEYS_JSON:", e)
    elif os.path.exists(LOCAL_KEYS_PATH):
        with open(LOCAL_KEYS_PATH, "r", encoding="utf-8") as f:
            keys = json.load(f).get("installed", {})
    elif os.path.exists(KEYS_PATH):
        with open(KEYS_PATH, "r", encoding="utf-8") as f:
            keys = json.load(f).get("installed", {})

    if not cred or not keys:
        raise FileNotFoundError("Calendar credentials not found in ~/.calendar-mcp or environment variables")

    # Refresh the token
    params = urllib.parse.urlencode({
        "client_id": keys["client_id"],
        "client_secret": keys["client_secret"],
        "refresh_token": cred["refresh_token"],
        "grant_type": "refresh_token"
    }).encode("utf-8")

    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=params)
    with urllib.request.urlopen(req, timeout=10) as resp:
        token_data = json.loads(resp.read().decode("utf-8"))

    cached_token = token_data["access_token"]
    expires_in = token_data.get("expires_in", 3600)
    cached_expiry = now_ts + (expires_in * 1000)

    # Save back to file for persistence
    cred["access_token"] = cached_token
    cred["expiry_date"] = cached_expiry
    try:
        with open(CRED_PATH, "w", encoding="utf-8") as f:
            json.dump(cred, f)
    except Exception:
        pass

    return cached_token


def fetch_events_for_calendar(calendar_id, source_type, time_min_iso, time_max_iso, token):
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
                start = item.get("start", {})
                end = item.get("end", {})

                # Check if all-day event
                if "date" in start:
                    date_str = start["date"]
                    time_str = "ทั้งวัน"
                else:
                    dt_str = start.get("dateTime", "")
                    # Extract date YYYY-MM-DD
                    date_str = dt_str[:10] if len(dt_str) >= 10 else ""
                    # Extract time HH:MM
                    start_time = dt_str[11:16] if len(dt_str) >= 16 else ""
                    end_dt_str = end.get("dateTime", "")
                    end_time = end_dt_str[11:16] if len(end_dt_str) >= 16 else ""
                    time_str = f"{start_time} - {end_time}" if (start_time and end_time) else (start_time or "ทั้งวัน")

                events.append({
                    "id": item.get("id"),
                    "title": item.get("summary", "(ไม่มีหัวข้อ)"),
                    "date": date_str,
                    "time": time_str,
                    "calendar": calendar_id,
                    "sourceType": source_type,
                    "url": item.get("htmlLink", "")
                })
    except Exception as e:
        print(f"Error fetching events for {calendar_id}:", e)
    return events


def fetch_all_7day_events():
    token = get_access_token()
    now = datetime.datetime.now()
    # Start from today at 00:00:00 (local time)
    start_today = datetime.datetime(now.year, now.month, now.day, 0, 0, 0)
    end_7days = start_today + datetime.timedelta(days=8, seconds=-1)

    # Format ISO with local timezone +07:00 (Bangkok)
    tz_offset = "+07:00"
    time_min = start_today.strftime("%Y-%m-%dT%H:%M:%S") + tz_offset
    time_max = end_7days.strftime("%Y-%m-%dT%H:%M:%S") + tz_offset

    all_events = []
    for cal in CALENDARS:
        evs = fetch_events_for_calendar(cal["id"], cal["sourceType"], time_min, time_max, token)
        all_events.extend(evs)

    # Sort events by date and time
    def sort_key(ev):
        d = ev.get("date", "")
        t = ev.get("time", "")
        return (d, 0 if t == "ทั้งวัน" else 1, t)

    all_events.sort(key=sort_key)
    return all_events


def create_event(title, date_str, time_str=None, calendar_id="pattarapon.thongsri@gmail.com", description=""):
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
    time_match = re.findall(r"(\d{1,2}:\d{2})", time_str) if time_str else []

    if time_match:
        start_t = time_match[0]
        if len(start_t) == 4:
            start_t = "0" + start_t
        if len(time_match) >= 2:
            end_t = time_match[1]
            if len(end_t) == 4:
                end_t = "0" + end_t
        else:
            # Default to 1 hour duration
            sh, sm = map(int, start_t.split(":"))
            eh = (sh + 1) % 24
            end_t = f"{eh:02d}:{sm:02d}"

        tz = "Asia/Bangkok"
        payload["start"] = {
            "dateTime": f"{date_str}T{start_t}:00+07:00",
            "timeZone": tz
        }
        payload["end"] = {
            "dateTime": f"{date_str}T{end_t}:00+07:00",
            "timeZone": tz
        }
    else:
        # All day event: start is date_str, end is next day in Google Calendar spec
        dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        next_dt = dt + datetime.timedelta(days=1)
        next_date_str = next_dt.strftime("%Y-%m-%d")
        payload["start"] = {"date": date_str}
        payload["end"] = {"date": next_date_str}

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

    return {
        "id": result.get("id"),
        "title": result.get("summary"),
        "url": result.get("htmlLink", ""),
        "date": date_str,
        "time": time_str or "ทั้งวัน",
        "calendar": calendar_id,
        "sourceType": "coop" if "coop" in calendar_id else "primary"
    }


if __name__ == "__main__":
    print("Testing fetch_all_7day_events()...")
    evs = fetch_all_7day_events()
    print(f"Retrieved {len(evs)} events successfully!")
    for e in evs:
        print(f"- {e['date']} [{e['time']}] ({e['calendar']}): {e['title']}")
