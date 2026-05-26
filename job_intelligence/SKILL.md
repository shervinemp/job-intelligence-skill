# Job Intelligence Pipeline

> **Windows note**: All commands must be prefixed with `python3` (e.g. `python3 extract.py`).  
> Bare `extract.py` opens the file in VSCode instead of running it.

## Pipeline stages

| Stage | What happens | Filter / Gate |
|-------|-------------|--------------|
| **search + stage** | `python3 stage_emails.py [--days N]` — searches Gmail (last 14d), saves threads to DB, fetches + cleans each email | Auto: skips emails without `job`/`jobs` keyword |
| **re-search** | `python3 stage_emails.py --refresh [--days N]` — clears cached threads, re-searches Gmail, re-stages | — |
| **extract** | `python3 extract.py` — finds all URLs in staged emails, saves to DB | SLM: `admit`/`reject` each extracted URL |
| **linkedin** | `python3 linkedin.py [--url <url>] [--max N]` — scrape LinkedIn jobs into pipeline | SLM: `admit`/`reject` each |
| **fetch** | `python3 fetch.py` — visits each URL (Playwright), scrapes description | SLM: `admit`/`reject`/`flag` each description |
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
| `python3 tailor.py [--count N] [--no-open]` | Gemini crafts CV (default 1) | `done`/`skip`/`redo` |
| `python3 tailor.py --count -1 --no-open` | Process ALL described jobs | — |
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
| Click | `python3 apply.py click <jid>` | Opens Easy Apply modal |
| Read | `python3 apply.py read <jid>` | Shows current fields + buttons, routes to next step |
| Fill | `python3 apply.py fill <jid>` | Fills profile-mapped fields (name, email, phone, linkedin) |
| Resume | `python3 apply.py resume <jid>` | Uploads tailored PDF to Easy Apply modal |
| Screen | `python3 apply.py screen <jid> --answers '{"Q":"A"}'` | Presents screening questions; model answers with --answers |
| Next | `python3 apply.py next <jid>` | Clicks Next/Review/Submit (disables overlay) |
| Submit | `python3 apply.py submit <jid>` | Verifies result after submit |
| Navigate | `python3 apply.py navigate <jid>` | External ATS: click "Apply on company website" |
| Detect platform | `python3 apply.py detect_platform <jid>` | External ATS: detect Greenhouse/Lever/Ashby/Workday |
| Fill external | `python3 apply.py fill_external <jid>` | External ATS: fill profile fields + present options |
| Next external | `python3 apply.py next_external <jid>` | External ATS: multi-page support (Next/Continue) |
| Submit external | `python3 apply.py submit_external <jid>` | External ATS: dry-run safe submit |
| Detect ATS | `python3 apply.py detect_ats <jid>` | Direct ATS URL (no LinkedIn): detect platform, read form |

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

Each job has a category that determines which Gemini gem handles its tailoring. Available in `categories.json`:

| Category | Gem | Description |
|----------|-----|-------------|
| tech | optimizer | Application Optimizer gem |
| general | (none) | Default Gemini, raw JD processing |

Required on first `admit` via `--category tech <jid>`. Re-run with a different category to update.  
`tailor.py` resolves from `categories.json` → `gems.json` → gemini.js automatically.

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
