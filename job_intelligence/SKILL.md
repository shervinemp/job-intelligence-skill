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

`extract.py run --count N` prints staged emails with all URLs → LLM (via gemini.js) reads and returns which URLs are actual job postings → `extract.py submit <tid> '<json>'` saves them.

The LLM decides from the URL alone — no regex, no link chasing. Only real job URLs get fetched.

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
| Chrome crash | `Start-Process "C:\Program Files\Google\Chrome\Application\chrome.exe" '--user-data-dir="CHROME_PROFILE"','--remote-debugging-port=9222'` |
| DB crash | `extract.py reset` or restore from backup |
