# Job Intelligence Pipeline

## Loop (unified status)

Any of `extract.py status`, `fetch.py status`, `tailor.py status`, or `db.py stats` shows
the full picture including the next command to run:

| Block | Action |
|-------|--------|
| `staged pending` | `python3 extract.py step --count N` → agent reads → `extract.py submit <tid> '<json>'` |
| `extracted:N` | `python3 fetch.py run --count 10` → read DESC lines → admit / reject |
| `described:N` | `python3 tailor.py run-all` → tailor 1 job → ask human |
| `tailored:N` | `tailor.py ready` (review) → `tailor.py done <jid>` |
| `failed:N` | `python3 fetch.py retry` or `tailor.py retry` |
| `auth walls:N` | `python3 fetch.py open` → human logs in → close browser → auto-retries |
| all zero | run gmail search → `stage_emails.py [search_results.json]` |

## Extraction (LLM-driven)

`extract.py step --count N` prints staged emails → agent reads → `extract.py submit <tid> '<json>'` saves job URLs.

Filter:

| Rule | Do |
|------|----|
| Canada-based (Toronto, Ottawa, Vancouver, etc.) | Include |
| Remote / work-from-home | Include |
| Quebec in-office (requires French / permits) | Exclude |
| US-based, on-site only | Exclude |
| Location unclear | Fetch description then decide |

## Fetch

`python3 fetch.py run --count 30` → curl (default) or Playwright.
Add `--curl` to skip Playwright. Add `--force` to re-fetch existing descriptions.

Chrome managed by `lib/chrome_manager.py` — connects via CDP, auto-starts if down, persists profile at `~/.openclaw/chrome-profile/`.
Auth-walled jobs are tracked per-jid in `~/.openclaw/needs_auth.json`; failed retries preserve the entry.

## DESC review

`DESC:jid:first512chars` per job:

| Content | Do |
|---------|-----|
| job `Title @ Company` + description | `python3 fetch.py admit jid` |
| "This job has closed" | `python3 fetch.py reject jid` |
| sign-in / cookie / needs human eyes | `python3 fetch.py flag jid` → stays at extracted |
| garbage (no job) | `python3 fetch.py reject jid` |

## Flagged jobs

`fetch.py flag jid` → records to `~/.openclaw/needs_auth.json` (persistent).  

Auth walls are also auto-detected during `_pw_fetch`: if the page returns short text with sign-in/login keywords, it's flagged as an auth wall without manual intervention.

`python3 fetch.py open` → opens visible browser → human logs in → close browser → auto-retries all flagged jobs.

Stale entries (jobs already past `extracted`/`failed` stage) are ignored; `cmd_open` resets `failed` auth-walled jobs to `extracted` before retry.

Sessions persist — log in once, lasts forever.

## Tailor

`tailor.py run-all` → `JOB {jid} {title} @ {company}` → ask human.

`tailor.py batch --count N` — process N described jobs silently (no handoff).

Before calling Gemini, `tailor.py` re-fetches the job URL via curl.
If the page says "no longer accepting" or similar, the job is auto-skipped (`CLOSED`).

Results go to `~/.openclaw/results/{jid}/`:
- `gemini_response.txt` — full Gemini output
- `script.py` — extracted Python script that generates the PDF
- `{jid}.url` — shortcut to the job posting (double-click to open)
- Generated PDF(s) from running script.py

| Human | Run |
|-------|-----|
| yes | `apply.py auto jid` or `tailor.py ready jid` then `tailor.py done jid` |
| skip | `tailor.py skip jid` |
| redo | `tailor.py redo jid` |

## Never touch

`lib/` `tools/` `data/` `stage/` `results/` `profile.json` `secrets.json`

## Recovery

| Signal | Fix |
|--------|------|
| `invalid_grant` | `gmail-cli auth add email` |
| `TIMEOUT` | `tailor.py retry` |
| `RATE_LIMIT` | `tailor.py retry` (after reset time) |
| Chrome crash | `Start-Process "C:\Program Files\Google\Chrome\Application\chrome.exe" '--user-data-dir="C:\Users\sherv\.openclaw\chrome-profile"','--remote-debugging-port=9222'` |
| DB crash | `extract.py reset` |
| Auth wall retry failed | Run `fetch.py open` again (jid still in `needs_auth.json`) |
