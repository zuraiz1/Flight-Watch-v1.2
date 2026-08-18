"""
pia_flight_watch.py

Polls the AirLabs Schedules API ONCE per invocation and calls a
user-defined callback for any monitored flight that is DELAYED or
CANCELLED. Alerts are posted to Discord via webhook.

Standalone project - not connected to Overwatch or monitor.py.

Designed to run as a scheduled GitHub Actions job (one run = one poll),
not as a long-running process. See .github/workflows/flight_watch.yml.

Secrets (AIRLABS_API_KEY, DISCORD_WEBHOOK_URL) are read in this order:
    1. Environment variables (used by GitHub Actions via repo Secrets)
    2. config.txt next to this script (for local testing only -
       DO NOT commit a filled-in config.txt to git, add it to .gitignore)

Quota awareness:
    Account quota: 5 requests/day. The script uses exactly ONE filter
    (all PIA flights, airline_iata=PK) per run = ONE request per run.
    Schedule the GitHub Actions cron for at most 5 runs/day (see the
    workflow file). DailyRequestTracker still enforces a hard cap of
    MAX_REQUESTS_PER_DAY as a safety net in case the schedule is
    misconfigured or a run is manually re-triggered.

Local usage:
    1. Fill in config.txt next to this script with your real values
       (see load_config() below for the exact format).
    2. Edit WATCH_FILTERS to whatever you want to monitor.
    3. Run: python3 pia_flight_watch.py
"""

import os
import sqlite3
import logging
from datetime import datetime, timezone

import requests

# ---------------------------------------------------------------------------
# Config loading: env vars first (GitHub Actions Secrets), config.txt fallback
# ---------------------------------------------------------------------------

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.txt")


def load_config_file(path: str) -> dict:
    """
    Reads a simple KEY=VALUE text file, one entry per line.
    Blank lines and lines starting with # are ignored.

    Example config.txt:
        AIRLABS_API_KEY=your_airlabs_key_here
        DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/xxxx/yyyy
    """
    values = {}
    if not os.path.exists(path):
        return values

    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def get_setting(name: str, default: str) -> str:
    """Env var takes priority (GitHub Actions Secrets); falls back to config.txt."""
    if name in os.environ and os.environ[name]:
        return os.environ[name]
    return _file_config.get(name, default)


_file_config = load_config_file(CONFIG_PATH)

AIRLABS_API_KEY = get_setting("AIRLABS_API_KEY", "PUT_YOUR_KEY_HERE")
DISCORD_WEBHOOK_URL = get_setting("DISCORD_WEBHOOK_URL", "PUT_YOUR_DISCORD_WEBHOOK_URL_HERE")

# Quota: 5 requests/day (per your account). Every filter in WATCH_FILTERS
# costs one request PER RUN, so with a 5/day budget we use exactly ONE
# filter that covers everything we care about (all PIA flights, any
# route), rather than splitting by airport/direction.
# Docs: https://airlabs.co/docs/schedules
WATCH_FILTERS = [
    {"airline_iata": "PK"},   # all Pakistan International Airlines flights
]

MAX_REQUESTS_PER_DAY = 5

# Minutes of delay before we consider a flight "late" (cancellations always fire).
DELAY_THRESHOLD_MINUTES = 15

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "flight_state.db")

API_BASE = "https://airlabs.co/api/v9/schedules"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("pia_flight_watch")


# ---------------------------------------------------------------------------
# Discord webhook delivery
# ---------------------------------------------------------------------------

def send_discord_alert(flight: dict, reason: str):
    """POST a formatted embed to the configured Discord webhook."""
    if DISCORD_WEBHOOK_URL == "PUT_YOUR_DISCORD_WEBHOOK_URL_HERE":
        log.warning("DISCORD_WEBHOOK_URL not set - skipping Discord delivery.")
        return

    flight_no = flight.get("flight_iata") or flight.get("flight_icao") or "Unknown flight"
    dep = flight.get("dep_iata", "???")
    arr = flight.get("arr_iata", "???")
    dep_time = flight.get("dep_time", "N/A")
    arr_time = flight.get("arr_time", "N/A")
    dep_delay = flight.get("dep_delayed")
    arr_delay = flight.get("arr_delayed")
    status = flight.get("status", "unknown")

    is_cancelled = reason == "cancelled"
    color = 0xE03030 if is_cancelled else 0xE0A030  # red for cancelled, amber for delayed
    title = f"{'CANCELLED' if is_cancelled else 'DELAYED'}: {flight_no}"

    fields = [
        {"name": "Route", "value": f"{dep} -> {arr}", "inline": True},
        {"name": "Status", "value": status, "inline": True},
        {"name": "Scheduled Departure", "value": dep_time, "inline": True},
        {"name": "Scheduled Arrival", "value": arr_time, "inline": True},
    ]
    if dep_delay:
        fields.append({"name": "Departure Delay", "value": f"{dep_delay} min", "inline": True})
    if arr_delay:
        fields.append({"name": "Arrival Delay", "value": f"{arr_delay} min", "inline": True})

    payload = {
        "username": "PIA Flight Watch",
        "embeds": [{
            "title": title,
            "color": color,
            "fields": fields,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }],
    }

    try:
        resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        log.error(f"Failed to send Discord alert for {flight_no}: {e}")


# ---------------------------------------------------------------------------
# Your callback - replace this with whatever you actually want to happen
# ---------------------------------------------------------------------------

def on_flight_alert(flight: dict, reason: str):
    """
    Called exactly once per flight per status change into "late" or
    "cancelled". `reason` is either "cancelled" or "delayed".

    flight is the raw AirLabs flight dict - see fields at
    https://airlabs.co/docs/schedules
    """
    flight_no = flight.get("flight_iata") or flight.get("flight_icao")
    dep = flight.get("dep_iata")
    arr = flight.get("arr_iata")
    dep_delay = flight.get("dep_delayed")
    arr_delay = flight.get("arr_delayed")
    status = flight.get("status")

    log.info(f"[ALERT] {flight_no} ({dep} -> {arr}) is {reason.upper()} "
             f"(status={status}, dep_delay={dep_delay}, arr_delay={arr_delay})")

    send_discord_alert(flight, reason)


# ---------------------------------------------------------------------------
# Local state (avoids re-firing the callback for a flight already flagged)
# ---------------------------------------------------------------------------

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS flight_state (
            flight_key TEXT PRIMARY KEY,
            status TEXT,
            dep_delayed INTEGER,
            arr_delayed INTEGER,
            last_seen TEXT
        )
    """)
    conn.commit()
    return conn


def flight_key(flight: dict) -> str:
    """Unique key per flight instance (flight number + scheduled dep date/time)."""
    return f"{flight.get('flight_iata')}_{flight.get('dep_time')}"


def get_previous_state(conn, key: str):
    cur = conn.execute(
        "SELECT status, dep_delayed, arr_delayed FROM flight_state WHERE flight_key = ?",
        (key,),
    )
    return cur.fetchone()


def upsert_state(conn, key: str, status, dep_delayed, arr_delayed):
    conn.execute("""
        INSERT INTO flight_state (flight_key, status, dep_delayed, arr_delayed, last_seen)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(flight_key) DO UPDATE SET
            status=excluded.status,
            dep_delayed=excluded.dep_delayed,
            arr_delayed=excluded.arr_delayed,
            last_seen=excluded.last_seen
    """, (key, status, dep_delayed, arr_delayed, datetime.now(timezone.utc).isoformat()))
    conn.commit()


# ---------------------------------------------------------------------------
# Daily request quota guard (hard cap, independent of the poll scheduler)
# ---------------------------------------------------------------------------

class DailyRequestTracker:
    """Tracks how many AirLabs requests have been made today (local date)
    and refuses to allow more once MAX_REQUESTS_PER_DAY is hit - a safety
    net in case of retries, clock drift, or manual re-runs."""

    def __init__(self, conn):
        self.conn = conn
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS request_log (
                day TEXT PRIMARY KEY,
                count INTEGER NOT NULL DEFAULT 0
            )
        """)
        self.conn.commit()

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).date().isoformat()

    def count_today(self) -> int:
        cur = self.conn.execute(
            "SELECT count FROM request_log WHERE day = ?", (self._today(),)
        )
        row = cur.fetchone()
        return row[0] if row else 0

    def can_make_request(self, n: int = 1) -> bool:
        return self.count_today() + n <= MAX_REQUESTS_PER_DAY

    def record_request(self):
        day = self._today()
        self.conn.execute("""
            INSERT INTO request_log (day, count) VALUES (?, 1)
            ON CONFLICT(day) DO UPDATE SET count = count + 1
        """, (day,))
        self.conn.commit()


# ---------------------------------------------------------------------------
# AirLabs polling
# ---------------------------------------------------------------------------

def fetch_flights(filter_params: dict) -> list:
    params = {**filter_params, "api_key": AIRLABS_API_KEY}
    try:
        resp = requests.get(API_BASE, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        log.error(f"Request failed for filter {filter_params}: {e}")
        return []

    if "error" in data:
        log.error(f"AirLabs API error for filter {filter_params}: {data['error']}")
        return []

    return data.get("response", [])


def evaluate_flight(conn, flight: dict):
    """Compare a flight's current state to the last-known state and fire
    the callback if it newly became cancelled or newly crossed the delay
    threshold."""
    status = flight.get("status")
    dep_delayed = flight.get("dep_delayed") or 0
    arr_delayed = flight.get("arr_delayed") or 0

    key = flight_key(flight)
    previous = get_previous_state(conn, key)
    prev_status = previous[0] if previous else None
    prev_dep_delayed = previous[1] if previous else 0
    prev_arr_delayed = previous[2] if previous else 0

    is_cancelled_now = status == "cancelled"
    was_cancelled_before = prev_status == "cancelled"

    is_late_now = (dep_delayed >= DELAY_THRESHOLD_MINUTES) or (arr_delayed >= DELAY_THRESHOLD_MINUTES)
    was_late_before = (prev_dep_delayed >= DELAY_THRESHOLD_MINUTES) or (prev_arr_delayed >= DELAY_THRESHOLD_MINUTES)

    if is_cancelled_now and not was_cancelled_before:
        on_flight_alert(flight, "cancelled")
    elif is_late_now and not was_late_before:
        on_flight_alert(flight, "delayed")

    upsert_state(conn, key, status, dep_delayed, arr_delayed)


def poll_once(conn, request_tracker):
    if not request_tracker.can_make_request(len(WATCH_FILTERS)):
        log.warning(
            f"Daily quota reached ({request_tracker.count_today()}/{MAX_REQUESTS_PER_DAY} "
            f"used today) - skipping this poll to protect your AirLabs quota."
        )
        return

    total_flights = 0
    for filt in WATCH_FILTERS:
        flights = fetch_flights(filt)
        request_tracker.record_request()
        total_flights += len(flights)
        for flight in flights:
            evaluate_flight(conn, flight)
    log.info(
        f"Poll complete - checked {total_flights} flights across {len(WATCH_FILTERS)} filter(s). "
        f"Requests used today: {request_tracker.count_today()}/{MAX_REQUESTS_PER_DAY}"
    )


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main():
    if AIRLABS_API_KEY == "PUT_YOUR_KEY_HERE":
        log.error(
            "AIRLABS_API_KEY not set. Set it as a GitHub Actions secret (env var) "
            f"or in {CONFIG_PATH} for local runs."
        )
        return
    if DISCORD_WEBHOOK_URL == "PUT_YOUR_DISCORD_WEBHOOK_URL_HERE":
        log.warning(
            "DISCORD_WEBHOOK_URL not set - alerts will be logged but not sent to Discord."
        )

    conn = init_db()
    request_tracker = DailyRequestTracker(conn)
    log.info(
        f"Running single poll. Daily budget: {MAX_REQUESTS_PER_DAY} requests. "
        f"DB: {DB_PATH}"
    )

    poll_once(conn, request_tracker)
    conn.close()
    log.info("Run complete.")


if __name__ == "__main__":
    main()
