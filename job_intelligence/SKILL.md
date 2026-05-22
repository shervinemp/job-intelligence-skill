# Job Intelligence Pipeline

> **Windows note**: All commands must be prefixed with `python3` (e.g. `python3 extract.py`).  
> Bare `extract.py` opens the file in VSCode instead of running it.

## Pipeline

| I run | What happens | What I do |
|-------|-------------|-----------|
| `python3 extract.py [--count N]` | Prints staged email, extracts URLs | Pick URLs → `submit <tid> '<json>'` |
| `python3 fetch.py [--count N] [--curl] [--force]` | Fetches descriptions for extracted jobs | `admit`/`reject`/`flag` each |
| `python3 fetch.py --refresh [--count N]` | Re-fetches described URLs (freshness check) | Same admit/reject/flag |
| `python3 fetch.py admit <jid>` | Mark job as described | — |
| `python3 fetch.py reject <jid>` | Mark job as skipped (garbage/closed) | — |
| `python3 fetch.py flag <jid>` | Mark auth wall (save to needs_auth.json) | — |
| `python3 fetch.py open [<jid>]` | Open job URL in Chrome tab, return immediately | View, close tab, decide |
| `python3 fetch.py retry` | Retry failed fetches | Same admit/reject |
| `python3 tailor.py [--count N] [--no-open]` | Gemini crafts CV for next described job(s) | `done`/`skip`/`redo` |
| `python3 tailor.py done <jid>` | Mark as applied, create .url shortcut | — |
| `python3 tailor.py skip <jid>` | Skip | — |
| `python3 tailor.py redo <jid>` | Reset described for re-tailor | — |
| `python3 tailor.py retry` | Retry failed tailor jobs | — |
| `python3 tailor.py ready [<jid>]` | Open URL + files folder | — |
| `python3 tailor.py resume <jid>` | List application files | — |
| `python3 tailor.py reset <jid> [--hard]` | Reset to described or extracted | — |
| `python3 tailor.py reset --all [--hard]` | Mass reset | — |
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

Detected automatically — short page text with sign-in keywords gets flagged.  
`python3 fetch.py flag <jid>` — manual flag.  
`python3 fetch.py open [<jid>]` — open in Chrome (uses persistent session), returns immediately.  
Stale entries (job already progressed past extracted/failed) are auto-pruned.

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
