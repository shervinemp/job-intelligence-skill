# Job Intelligence Pipeline

## Pipeline

| I run | What happens | What I do |
|-------|-------------|-----------|
| `extract.py [--count N]` | Prints staged emal, extracts URLs | Pick URLs → `submit <tid> '<json>'` |
| `fetch.py [--count N] [--curl] [--force]` | Fetches descriptions for extracted jobs | `admit`/`reject`/`flag` each |
| `fetch.py --refresh [--count N]` | Re-fetches described URLs (freshness check) | Same admit/reject/flag |
| `fetch.py admit <jid>` | Mark job as described | — |
| `fetch.py reject <jid>` | Mark job as skipped (garbage/closed) | — |
| `fetch.py flag <jid>` | Mark auth wall (save to needs_auth.json) | — |
| `fetch.py open [<jid>]` | Open job URL in Chrome tab, return immediately | View, close tab, decide |
| `fetch.py retry` | Retry failed fetches | Same admit/reject |
| `tailor.py [--count N] [--no-open]` | Gemini crafts CV for next described job(s) | `done`/`skip`/`redo` |
| `tailor.py done <jid>` | Mark as applied, create .url shortcut | — |
| `tailor.py skip <jid>` | Skip | — |
| `tailor.py redo <jid>` | Reset described for re-tailor | — |
| `tailor.py retry` | Retry failed tailor jobs | — |
| `tailor.py ready [<jid>]` | Open URL + files folder | — |
| `tailor.py resume <jid>` | List application files | — |
| `tailor.py reset <jid> [--hard]` | Reset to described or extracted | — |
| `tailor.py reset --all [--hard]` | Mass reset | — |
| `status` | Unified status + next command | Follow `next:` hint |

## Extraction rules

| Value | Include |
|-------|---------|
| Canada-based (Toronto, Ottawa, Vancouver, etc.) | Yes |
| Remote / work-from-home | Yes |
| Quebec in-office | No |
| US on-site only | No |
| Unclear location | Fetch description, then decide |

## Auth walls

Detected automatically in `fetch.py` — short page text with sign-in keywords gets flagged.  
`fetch.py flag <jid>` — manual flag.  
`fetch.py open [<jid>]` — open in Chrome (uses persistent session), returns immediately.  
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
| `invalid_grant` | `gmail-cli auth add email` |
| `TIMEOUT` / `RATE_LIMIT` | `tailor.py retry` |
| Chrome crash | `Start-Process "C:\Program Files\Google\Chrome\Application\chrome.exe" '--user-data-dir="~/.openclaw/chrome-profile"','--remote-debugging-port=9222'` |
| DB crash | `extract.py reset` |
| Auth wall stuck | `fetch.py open` + `fetch.py --refresh` |
