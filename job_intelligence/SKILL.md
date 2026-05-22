# Job Intelligence Pipeline

> **Windows note**: All commands must be prefixed with `python3` (e.g. `python3 extract.py`).  
> Bare `extract.py` opens the file in VSCode instead of running it.  
> `search_results.json` in workspace root is the Gmail search output — keep for re-staging.

## Pipeline stages

| Stage | What happens | Filter / Gate |
|-------|-------------|--------------|
| **gmail search** | `python3 skills/gmail-cli/gmail_cli.py gmail search '<query>' --all -j > search_results.json` | — |
| **stage** | `python3 stage_emails.py` — fetches + cleans each email (defaults to `search_results.json`) | Auto: skips emails without `job`/`jobs` keyword |
| **extract** | `python3 extract.py` — finds all URLs in staged emails, saves to DB | SLM: `admit`/`reject` each extracted URL |
| **fetch** | `python3 fetch.py` — visits each URL (Playwright), scrapes description | SLM: `admit`/`reject`/`flag` each description |
| **tailor** | `python3 tailor.py [--count N]` — Gemini crafts CV | SLM: `done`/`skip`/`redo` |

## I run / What I do

| I run | What happens | What I do |
|-------|-------------|-----------|
| `python3 extract.py` | Auto-extract URLs, shows `JOB:{jid}:{url}` with context | `admit`/`reject` each |
| `python3 extract.py admit <jid>` | Keep the extracted job | — |
| `python3 extract.py reject <jid>` | Skip the extracted job | — |
| `python3 extract.py review [--count N]` | Show N staged emails for manual URL picking | Pick URLs → `submit <tid> '<json>'` |
| `python3 extract.py submit <tid> '<json>'` | Save manually picked URLs | — |
| `python3 fetch.py` | Fetch all descriptions (Playwright) | `admit`/`reject`/`flag` each |
| `python3 fetch.py admit <jid>` | Mark job as described | — |
| `python3 fetch.py reject <jid>` | Skip (garbage/closed) | — |
| `python3 fetch.py flag <jid>` | Mark auth wall | — |
| `python3 fetch.py open [<jid>]` | Open in Chrome | View, close tab, decide |
| `python3 fetch.py retry` | Retry failed fetches | Same admit/reject |
| `python3 fetch.py --refresh` | Re-fetch described URLs | Same admit/reject/flag |
| `python3 tailor.py [--count N] [--no-open]` | Gemini crafts CV | `done`/`skip`/`redo` |
| `python3 tailor.py done <jid>` | Mark applied, create .url shortcut | — |
| `python3 tailor.py skip <jid>` | Skip | — |
| `python3 tailor.py redo <jid>` | Re-tailor from described | — |
| `python3 tailor.py retry` | Retry failed | — |
| `python3 tailor.py ready [<jid>]` | Open URL + files folder | — |
| `python3 extract.py reset` | Wipe DB, start fresh | — |
| `status` | Unified status + next command | Follow `next:` hint |

## Extraction rules

| Value | Include |
|-------|---------|
| Ontario-based (Toronto, Ottawa, Oakville, Mississauga, Waterloo, etc.) | Yes — preferred |
| Other Canada-based (Vancouver, Calgary, etc.; on-site or hybrid) | Yes |
| Remote / work-from-home (any country) | Yes |
| Quebec in-office | No |
| US on-site only | No |
| Unclear location | Fetch description, then decide |

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
