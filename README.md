# Overview
What this system does is watch Pakistan International Airlines (PIA) flights and post an alert to Discord the moment one is delayed or cancelled.
It consists of 1 Python script (`pia_flight_watch.py`) hosted on GitHub, run automatically by GitHub Actions on a fixed schedule. There is no server or laptop that needs to stay on - GitHub runs it for you.
It checks PIA's live schedule against AirLabs, compares it to the last known status stored in a small local database, and only fires an alert the moment something *changes* - not on every check.

---
# Files

| File | Purpose |
| --- | --- |
| `pia_flight_watch.py` | The script itself. Does one poll, checks for delays/cancellations, sends Discord alerts, exits. |
| `.github/workflows/flight_watch.yml` | Tells GitHub when to run the script (5x/day) and how. |
| `config.txt.example` | Template showing what `config.txt` should look like. Safe to commit. |
| `config.txt` | Your real API key + webhook URL, for local testing only. Never committed (see `.gitignore`). |
| `.gitignore` | Keeps `config.txt` and other local junk out of the repo. |
| `requirements.txt` | Python packages the GitHub Actions runner needs to install (`requests`). |
| `flight_state.db` | Auto-created SQLite file that remembers each flight's last known status, so alerts don't repeat. Committed back to the repo after every run. |

---
# Data sources
- **Flight data:** [AirLabs](https://airlabs.co) Schedules API, filtered to `airline_iata=PK` (all PIA flights).
- **Alert delivery:** Discord, via an Incoming Webhook.

# Quota
AirLabs account is limited to **5 requests/day**. The script is built around this:
- One run = one API request (single filter, no per-airport splitting).
- The GitHub Actions schedule fires 5x/day, matching the quota exactly.
- A `DailyRequestTracker` inside the script hard-caps requests at 5/day regardless, so a manual re-run or misconfigured schedule can't blow the budget.
