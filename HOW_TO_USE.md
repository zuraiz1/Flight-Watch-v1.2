# Overview
What this system does is check PIA's live flight schedule and send a Discord alert the moment a flight becomes delayed or cancelled.
It runs entirely on GitHub - no server, laptop, or manual restarting required. Once set up, it checks in automatically 5 times a day and only messages you when something actually changes.

---
# How to use
Firstly, [set up](#set-up) the system. Once set up, it runs itself - there are no daily steps.

## Tips
- To test it immediately instead of waiting for the schedule, go to the repo's **Actions** tab → **PIA Flight Watch** → **Run workflow**.
- If Discord alerts stop arriving, check the latest run's logs in the **Actions** tab first - most issues show up clearly there (missing secret, bad webhook, AirLabs error).
- If you ever change what's monitored (e.g. specific airports instead of all PIA flights), edit `WATCH_FILTERS` in `pia_flight_watch.py` - but keep it to **one filter**, since each filter costs one AirLabs request and the daily budget is only 5.
- `flight_state.db` is what stops you from getting the same alert twice. If alerts seem "stuck" or wrong, you can safely delete it from the repo - it will just get recreated fresh on the next run.

---
# Set up
To set it up, you need a GitHub repo, an AirLabs account, and a Discord server you can add webhooks to.

| Step | What to do |
| --- | --- |
| 1. Create the repo | Push all project files to a new GitHub repo (private is fine). Keep the folder structure exactly as-is, including `.github/workflows/flight_watch.yml`. |
| 2. Get an AirLabs API key | Sign up at [airlabs.co](https://airlabs.co), copy your API key from the dashboard. |
| 3. Get a Discord webhook URL | In Discord: **Server Settings → Integrations → Webhooks → New Webhook**, pick the channel, copy the URL. |
| 4. Add both as GitHub Secrets | Repo → **Settings → Secrets and variables → Actions → New repository secret**. Add `AIRLABS_API_KEY` and `DISCORD_WEBHOOK_URL` (names must match exactly). |
| 5. Test it | **Actions** tab → **PIA Flight Watch** → **Run workflow**. Check the logs for errors, and check Discord if any PIA flight happens to be delayed/cancelled at that moment. |
| 6. Done | The schedule in `flight_watch.yml` takes over from here - 5 automatic runs a day. |

## Local testing (optional)
If you want to run the script on your own machine before pushing to GitHub:
1. Copy `config.txt.example` to `config.txt`.
2. Fill in your real `AIRLABS_API_KEY` and `DISCORD_WEBHOOK_URL`.
3. Run `pip install -r requirements.txt`, then `python3 pia_flight_watch.py`.
4. `config.txt` is git-ignored, so this never risks leaking your keys.
