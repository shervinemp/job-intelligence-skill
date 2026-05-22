# Job Intelligence Pipeline

## Loop

`python3 db.py stats` → decide:

| Stage | Action |
|-------|--------|
| `EXTRACT:N` | `python3 extract.py run --count 10` → LLM reviews emails → `extract.py submit` → `fetch.py run --count 30` → read DESC: lines → admit / reject |
| `FETCH:N` | `python3 pipeline.py step` → tailor 1 job → ask human |
| `FAILED:N` | `python3 fetch.py retry` or ask human |
| `AUTH:d1+d2` | `python3 fetch.py open` → human logs in → close browser → auto-retries |
| all zero | tell human "all done, new search?" |

## Extraction (LLM-driven)

`extract.py step --count N` prints staged emails → LLM reads → `extract.py submit <tid> '<json>'` saves job URLs.

The LLM decides which URLs are actual job postings. During extraction, filter by:

| Rule | Do |
|------|----|
| Canada-based (Toronto, Ottawa, Vancouver, etc.) | Include |
| Remote / work-from-home | Include |
| Quebec in-office (requires French / permits) | Exclude |
| US-based, on-site only | Exclude |
| Location unclear | Fetch description then decide |

Only real job URLs get fetched. No regex, no link chasing.

## Fetch

`python3 fetch.py run --count 30` → fetches descriptions via Playwright (reuses Chrome session if running on port 9222) or curl fallback.

fetch.py no longer chases links on the page — it fetches the exact URL given, nothing else.

## DESC review

`DESC:jid:first512chars` per job:

| Content | Do |
|---------|-----|
| job `Title @ Company` + description | `python3 fetch.py admit jid` |
| "This job has closed" | `python3 fetch.py reject jid` |
| sign-in / cookie / needs human eyes | `python3 fetch.py flag jid` → stays at extracted |
| garbage (no job) | `python3 fetch.py reject jid` |

## Flagged jobs

`fetch.py flag jid` → records to `needs_auth.json`.  
`python3 fetch.py open` → opens visible browser → human logs in → close browser → auto-retries all flagged jobs.

Sessions persist — log in once, lasts forever.

## Tailor

`pipeline.py step` → `STEP:tailor OK jid` → ask human "Apply?"

Results go to `~/.openclaw/results/{jid}/`:
- `gemini_response.txt` — full Gemini output (includes gen.py Python script)
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
| Chrome crash | `Start-Process "C:\Program Files\Google\Chrome\Application\chrome.exe" '--user-data-dir="C:\Users\sherv\.openclaw\chrome-profile"','--remote-debugging-port=9222'` |
| DB crash | `tools/recover_jobs.py` |
