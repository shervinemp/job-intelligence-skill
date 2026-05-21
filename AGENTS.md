# Pipeline Runbook

Run `python3 db.py stats` then match the stage:

| Stage | Action |
|-------|--------|
| `EXTRACT:N` | `extract.py run --count 10` — LLM reads emails, returns job URLs → `extract.py submit` → `fetch.py run --count 30` |
| `FETCH:N` | `pipeline.py step` — tailor one job, ask human |
| `FAILED:N` | `fetch.py retry` or ask human |
| `AUTH:d1+d2` | `fetch.py open` — human logs in browser, closes it, pipeline retries automatically |
| All zero | "All done. New search?" |

## Extraction

`extract.py run --count N` prints staged emails with all URLs → agent feeds to LLM via `gemini.js` → LLM returns JSON of job URLs → `extract.py submit <tid> '<json>'` saves them.

## Fetch

`fetch.py run --count 30` fetches descriptions. Uses Playwright with running Chrome (port 9222) if available, falls back to curl. No link chasing — fetches exact URL given.

## DESC review

`DESC:{jid}:{first 200 chars}` lines appear after fetch:

| See this | Do this |
|----------|---------|
| `Job Title @ Company` with description | `fetch.py admit {jid}` |
| "This job has closed" | `fetch.py reject {jid}` |
| Sign-in wall, cookie wall | `fetch.py flag {jid}` — stays at extracted, retried after login |
| Garbage (not a job) | `fetch.py reject {jid}` |

## Auth walls

`fetch.py flag {jid}` writes to `needs_auth.json`.  
`fetch.py open` opens visible browser to the first auth-walled URL. Log in, close browser. All flagged jobs retry automatically.

## Tailor

`pipeline.py step` → `STEP:tailor OK {jid}` → ask human:

| Human says | Run |
|------------|-----|
| Apply | `apply.py auto {jid}` or `tailor.py ready {jid}` then `tailor.py done {jid}` |
| Skip | `tailor.py skip {jid}` |
| Redo | `tailor.py redo {jid}` |

## Recovery

| Problem | Fix |
|---------|-----|
| OAuth expired | `gmail-cli auth add <email>` |
| Chrome crash | `Start-Process "C:\Program Files\Google\Chrome\Application\chrome.exe" '--user-data-dir="CHROME_PROFILE"','--remote-debugging-port=9222'` |
| Gemini timeout | `tailor.py retry` |
| Fetch failed | `fetch.py retry` or skip |
| DB corrupt | `tools/recover_jobs.py` |
