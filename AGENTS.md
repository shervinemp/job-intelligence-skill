# Job Pipeline (Caveman SOP)

- 3w ≈ 400 threads gmail-cli, ~300-400 staged, ~50-100 jobs. Staged ≠ listings (GitHub, newsletters, social).
- State: `results/jobs.json`. Call `python3 extract.py run` to start, then `fetch.py run` until 0 extracted.
- **Flow:** `gmail-cli gmail search '<date_query>' --all -j` → `stage_emails.py` → `extract.py step` (LLM identifies URLs → fetches → parses → saves) → `fetch.py run` (fetches descriptions) → `tailor.py run-all`
- **Loop:** `> JOB {id} {title} @ {company}` → ask human → `tailor.py done/skip/retry`
- **Status:** `python3 db.py stats`
- **Auth walls:** `fetch.py flag jid` → human logs in via `fetch.py open` → auto-retry
- **Recovery:** `gmail-cli auth add <email>` (re-auth) | Chrome → `Start-Process "C:\Program Files\Google\Chrome\Application\chrome.exe" '--user-data-dir="CHROME_PROFILE"','--remote-debugging-port=9222'` | FAILED → `fetch.py retry` or skip | Script error → check `applications/{id}/gemini_response.txt` | jobs.json corrupt → `tools/recover_jobs.py`
- **Warnings:** `--all -j` required | Chrome signed into Gemini | `tailor.py done` waits 30-60s | Fails twice → FAILED
