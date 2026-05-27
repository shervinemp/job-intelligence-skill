# Job Intelligence Pipeline

> **Windows note**: All commands must be prefixed with `python3` (e.g. `python3 extract.py`).  
> Bare `extract.py` opens the file in VSCode instead of running it.

## Pipeline stages

| Stage | What happens | Filter / Gate |
|-------|-------------|--------------|
| **search + stage** | `python3 stage_emails.py [--days N]` — searches Gmail (last 14d), saves threads to DB, fetches + cleans each email | Auto: skips emails without `job`/`jobs` keyword |
| **re-search** | `python3 stage_emails.py --refresh [--days N]` — clears cached threads, re-searches Gmail, re-stages | — |
| **extract** | `python3 extract.py` — finds all URLs in staged emails, saves to DB | SLM: `admit`/`reject` each extracted URL. Check the URL + email context — many non-job URLs leak through (tracking links, privacy pages, company profiles, notification landing pages) |
| **linkedin** | `python3 linkedin.py [--url <url>] [--max N]` — scrape LinkedIn jobs into pipeline | SLM: `admit`/`reject` each |
| **fetch** | `python3 fetch.py` — visits each URL (Playwright), scrapes description | SLM: `admit`/`reject`/`flag` each description. Second gate — reject if the snippet doesn't look like a real JD (missing responsibilities/qualifications/work-description language) |
| **tailor** | `python3 tailor.py [--count N]` — Gemini crafts CV | SLM: `done`/`skip`/`redo` |

## I run / What I do

| I run | What happens | What I do |
|-------|-------------|-----------|
| `python3 stage_emails.py` | Search Gmail (incremental, max 14d lookback), stage new threads | — |
| `python3 stage_emails.py --refresh` | Re-search + re-stage everything (resets lookback to 14d) | — |
| `python3 stage_emails.py --days 30` | Override lookback to 30 days (also updates last-search marker) | — |
| `python3 linkedin.py` | Scrape LinkedIn saved jobs into pipeline | `admit`/`reject` each |
| `python3 linkedin.py --list` | Preview job cards without adding | — |
| `python3 linkedin.py --url <url> --max 20` | Custom URL, limit to 20 | — |
| `python3 extract.py` | Auto-extract URLs (skips existing in DB), shows `JOB:{jid}:{url}` with context | `admit --category <name> <jid>` / `reject` each |
| `python3 extract.py admit --category <name> <jid>` | Keep job + set category. Re-run to update category or skip flag | Categories: tech, general |
| `python3 extract.py help` | Show all options and available categories | — |
| `python3 extract.py reset <jid>` | Delete specific job, re-extracts on next run | — |
| `python3 extract.py reject <jid>` | Skip the extracted job | — |
| `python3 extract.py review [--count N]` | Show N staged emails for manual URL picking | Pick URLs → `submit [<tid>] '<json>'` |
| `python3 extract.py submit [<tid>] '<json>'` | Submit URLs manually. JSON must include `"category"`. | `{"url":"...","category":"tech","notes":"..."}` |
| `python3 fetch.py` | Fetch descriptions (default 3, use `--count N`) | `admit`/`reject`/`flag` each |
| `python3 fetch.py help` | Show all fetch subcommands and options | — |
| `python3 fetch.py admit <jid>` | Mark job as described | — |
| `python3 fetch.py reject <jid>` | Skip (garbage/closed) | — |
| `python3 fetch.py flag <jid>` | Mark auth wall | — |
| `python3 fetch.py open [<jid>]` | Open in Chrome | View, close tab, decide |
| `python3 fetch.py retry` | Retry failed fetches | Same admit/reject |
| `python3 fetch.py retry-skipped` | Reset all skipped jobs back to extracted | — |
| `python3 fetch.py --refresh` | Re-fetch described URLs | Same admit/reject/flag |
| `python3 tailor.py [--count N]` | Gemini crafts CV (default 1) | `done`/`skip`/`redo` |

| `python3 tailor.py --count -1` | Process ALL described jobs | — |
| `python3 tailor.py --relentless --count -1` | Process all, idle on rate limit, retry | — |
| `python3 tailor.py help` | Show all tailor subcommands and options | — |
| `python3 tailor.py done <jid>` | Mark applied, create .url shortcut | — |
| `python3 tailor.py skip <jid>` | Skip | — |
| `python3 tailor.py redo <jid>` | Re-tailor from described | — |
| `python3 tailor.py redo --from tailored,failed` | Batch redo by stage | — |
| `python3 tailor.py retry` | Retry failed | — |
| `python3 tailor.py reset --from failed,skipped` | Reset jobs by stage to described | — |
| `python3 tailor.py reset --all --hard` | Reset ALL jobs to extracted (careful!) | — |
| `python3 tailor.py ready [<jid>]` | Open URL + files folder | — |
| `python3 extract.py reset` | Wipe DB, start fresh | — |
| `status` | Unified status + next command, includes category distribution | Follow `next:` hint |

## Apply pipeline

| Step | Command | What happens |
|------|---------|-------------|
| Detect | `python3 apply.py detect <jid>` | Navigates /apply/ URL → Easy Apply / External / Applied |
| Click | `python3 apply.py click <jid>` | Opens Easy Apply modal (may need fresh /apply/ navigation if stale) |
| Read | `python3 apply.py read <jid>` | Shows current fields + buttons, routes to next step |
| Fill | `python3 apply.py fill <jid>` | Fills profile-mapped fields (name, email, phone, linkedin). Already-filled fields skipped. |
| Resume | `python3 apply.py resume <jid>` | `set_input_files` directly — do NOT click "Upload resume" (closes modal). Errors if no PDF. |
| Screen | `python3 apply.py screen <jid> --answers '{"Q":"A"}'` | Presents screening questions; model answers with --answers |
| Next | `python3 apply.py next <jid>` | Clicks Next/Review/Submit (disables overlay). Detects which action to take. |
| Submit | `python3 apply.py submit <jid>` | Dry-run safe. Shows button state + unfilled fields. |
| Navigate | `python3 apply.py navigate <jid>` | External ATS: decodes LinkedIn safety redirect URL automatically |
| Detect platform | `python3 apply.py detect_platform <jid>` | External ATS: detect Greenhouse/Lever/Ashby/Workday |
| Fill external | `python3 apply.py fill_external <jid> --answers '{}'` | External ATS: `--answers` JSON is the only source of model decisions. No hardcoded mappings. |
| Next external | `python3 apply.py next_external <jid>` | External ATS: multi-page support (Next/Continue) |
| Submit external | `python3 apply.py submit_external <jid>` | External ATS: always dry-run, never auto-confirm |
| Detect ATS | `python3 apply.py detect_ats <jid>` | Direct ATS URL (no LinkedIn): detects platform, reads form |

### Apply flow notes

**Unfollow company:** On the Easy Apply review page, uncheck "Follow X to stay up to date" checkbox to avoid timeline bloat.

**Screening questions:** Use `--answers '{"question text": "value"}'` — substring matching, so short keys like `"employ"` match `"Have you ever been employed by..."`. Answers are saved to common_answers for future reuse.

**Radio buttons:** Ashby renders hidden `<input type="radio">` behind custom button UI. Direct `radio.click()` sets DOM state but may not register with React. Prefer clicking the parent `<div class="_option_...">` or using Playwright's native `.check()` on the radio.

**Resume upload:** Always `set_input_files()` directly on the file input. Never trigger the native file picker dialog — it causes the modal to close before the file is attached.

**Multi-page forms:** Each `next` step re-reads the modal and reports the next action. Loop: fill → next → read → fill → next → ... → submit.

### Pre-flight checks

Before running the apply pipeline on a job, verify:

| Check | What to do |
|-------|------------|
| Already applied? | Check DB stage — if "applied", skip. Also detect.py classifies LinkedIn "Applied" button. |
| Needs tailor? | If stage is not "tailored", run `python3 tailor.py <jid>` first to generate PDF. 04_resume.py errors without one. |
| Rate limited? | If tailor returns RATE_LIMIT, stop and retry later via `tailor.py retry` or `--relentless`. Don't attempt apply without PDF. |
| Quebec on-site? | Per SKILL.md extraction rules — reject. Don't apply. |
| Has external URL? | For external ATS, navigate.py must succeed. If no external URL found, job may be closed or premium-walled.

### Flow examples

**LinkedIn Easy Apply:** detect → click → read → fill → resume → screen → next → [loop] → submit

**LinkedIn External → Ashby:** detect → navigate → detect_platform → fill_external → submit_external

**Direct ATS URL:** detect_ats → fill_external → next_external → [loop] → submit_external

## Extraction rules

| Value | Include |
|-------|---------|
| Ontario-based (Toronto, Ottawa, Oakville, Mississauga, Waterloo, etc.) | Yes — preferred |
| Other Canada-based (Vancouver, Calgary, etc.; on-site or hybrid) | Yes |
| Remote / work-from-home (any country) | Yes |
| Quebec in-office | No |
| US on-site only | No |
| Unclear location | Fetch description, then decide |

## Notes (human context)

Attach human context to any job via `submit` — referral mentions, priorities, etc.

```
python3 extract.py submit '{"url":"https://...","notes":"John can refer at Google"}'
```

`tailor.py` appends `Context: {notes}` at the end of the Gemini prompt — not a directive, just supplementary info.  
Re-run with `"notes":""` to clear. The field survives all stage transitions.

## Categories

Each job has a category that guides admission. All categories use the same gem (`categories.json` → `gems.json`).

| Category | Description | When to use |
|----------|-------------|-------------|
| tech | Building/maintaining tech: software, data, ML, IT, backend, frontend, DevOps, cloud, infra, security | Primary target |
| general | No specialized skills needed: retail, food service, warehouse, hospitality, cleaning, labor | Settle job |
| (reject) | Admin, buyer, PM, analyst, non-software engineer, technician — skip, not worth your time | Reject at extract |

Required on first `admit` via `--category tech <jid>`. Re-run to update.

## Decision rules

See `decisions.md` — compact reference for screening question answers, relocation, sponsorship, and experience estimation. Core principle: **don't self-reject**.

## Auth walls

Detected automatically during fetch — sign-in keywords flag the job.  
`python3 fetch.py flag <jid>` — manual flag.  
`python3 fetch.py open [<jid>]` — open in Chrome (persistent session), returns immediately.  
Stale entries auto-pruned.

## Output directory

`~/.openclaw/results/{jid}/`:
- `gemini_response.txt` — full Gemini output
- `script.py` — extracted Python script for PDF
- `{jid}.url` — shortcut to job posting
- `*.pdf` — generated CV/cover letter

## Recovery

| Signal | Fix |
|--------|------|
| `invalid_grant` | `python3 skills/gmail-cli/gmail_cli.py auth add email` |
| `TIMEOUT` / `RATE_LIMIT` | `python3 tailor.py retry` |
| Chrome crash | `Start-Process "C:\Program Files\Google\Chrome\Application\chrome.exe" '--user-data-dir="~/.openclaw/chrome-profile"','--remote-debugging-port=9222'` |
| DB crash | `python3 extract.py reset` |
| Auth wall stuck | `python3 fetch.py open` + `python3 fetch.py --refresh` |

## Technical notes

**Gemini.js:** When running from ji-skill (not workspace), `call_gemini.py` looks for `skills/gemini-browser/gemini.js` relative to workspace root. Must run with `$env:NODE_PATH="C:\Users\sherv\.openclaw\workspace\node_modules"` or from the workspace directory. `browser.close()` on CDP connections is not awaited — use `await browser.close().catch(()=>{})` to prevent sync throws from skipping action handlers.

**LinkedIn title dedup:** LinkedIn job cards often repeat the title (visible + hidden verification text). `linkedin.py` now deduplicates by detecting when the first half of the title string equals the second half.

**Common_answers:** Answers to form questions are accumulated in `profile.json` under `common_answers`. When the filler encounters a question, it checks `--answers` first (exact + substring match), then falls back to `common_answers` via fuzzy word-overlap matching. Never pre-populate common_answers with guessed values (visa, sponsorship, etc.) — only save what the user explicitly provides.

**Gems:** `gems.json` maps named gems to IDs: `optimizer_tech (4203d06f5d81)` for tech jobs, `optimizer_general (3697c8c02b40)` for general jobs. `categories.json` references these names. `gemini.js` resolves them at startup.
