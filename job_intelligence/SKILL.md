# Job Intelligence Pipeline

> **Windows note**: All commands must be prefixed with `python3` (e.g. `python3 extract.py`).  
> Bare `extract.py` opens the file in VSCode instead of running it.

## Pipeline stages

| Stage | What happens | Gate |
|-------|-------------|------|
| **stage** | `stage_emails.py [--days N]` — search Gmail (14d), save, clean | Auto: skip non-job keywords |
| **refresh** | `--refresh [--days N]` — clear cache, re-search, re-stage | — |
| **extract** | `extract.py` — find URLs in staged emails | SLM: admit/reject. Many non-job URLs leak through. |
| **linkedin** | `linkedin.py [--url <url>] [--max N]` — scrape LinkedIn jobs | SLM: admit/reject |
| **fetch** | `fetch.py` — visit URL, scrape description | SLM: admit/reject/flag. Second gate — real JD vs garbage. |
| **tailor** | `tailor.py [--count N]` — crafts tailored CV. `JI_TAILOR=gem` (default): sends to Gemini Web gem. `JI_TAILOR=agent`: emits PROMPT:, SLM writes script.py, then `done <jid>` runs it. | SLM: done/skip/redo |

## Commands

| Command | Effect | My action |
|---------|--------|-----------|
| `stage_emails.py` | Search Gmail (incremental, max 14d) | — |
| `stage_emails.py --refresh` | Re-search + re-stage everything | — |
| `stage_emails.py --days N` | Override lookback | — |
| `linkedin.py` | Scrape LinkedIn saved jobs | admit/reject each |
| `linkedin.py --list` | Preview cards without adding | — |
| `linkedin.py --url <url> --max N` | Custom URL + limit | — |
| `extract.py` | Auto-extract URLs from staged emails | `admit --category <name> <jid>` / reject |
| `extract.py admit --category <name> <jid>` | First admit requires --category (guess from email context). --notes optional. | Categories: tech, general |
| `extract.py reset <jid>` | Delete job, re-extracts on next run | — |
| `extract.py review [--count N]` | Show N staged emails for manual picking | Pick → `submit` |
| `extract.py submit [<tid>] '<json>'` | Submit URLs manually | JSON needs `"category"` |
| `fetch.py` | Fetch descriptions (default 3, use `--count N`) | admit/reject/flag each |
| `fetch.py admit <jid> --category <name> [--notes "..."]` | Mark described. --category overrides extract guess when JD visible. | Options: tech, general |
| `fetch.py reject <jid>` | Skip (garbage/closed) | — |
| `fetch.py flag <jid>` | Mark as auth wall | — |
| `fetch.py open [<jid>]` | Open in Chrome | View, close tab, decide |
| `fetch.py retry` | Retry failed fetches | Same admit/reject |
| `fetch.py retry-skipped` | Reset skipped → extracted | — |
| `fetch.py --refresh` | Re-fetch described URLs | Same admit/reject |
| `tailor.py [--count N]` | Crafts tailored CV (default 1) | done/skip/redo |
| `tailor.py --count -1` | Process ALL described | — |
| `tailor.py --relentless --count -1` | Process all, idle on rate limit | — |
| `tailor.py done/skip/redo <jid>` | Mark applied / skip / redo | — |
| `tailor.py retry` | Retry failed | — |
| `tailor.py reset --from failed,skipped` | Reset by stage to described | — |
| `tailor.py reset --all --hard` | Reset ALL to extracted (careful!) | — |
| `tailor.py ready [<jid>]` | Open URL + files folder | — |
| `extract.py reset` | Wipe DB, start fresh | — |
| `status` | Unified status + next command | Follow `next:` hint |

## Apply pipeline

| Step | Command | What happens |
|------|---------|-------------|
| Detect | `python3 apply.py detect <jid>` | Pre-flight: checks DB stage, PDF, classify type (Easy Apply / External / Applied / ATS). Prints PAGE state + NEXT. |
| Navigate | `python3 apply.py navigate <jid>` | LinkedIn → External ATS: clicks external button, decodes redirect, detects platform, reads form. |
| Act (act) | `python3 apply.py act --fill/--next/--back/--submit/--inspect <jid> [options]` | Fill, advance, go back, submit, or inspect. `--inspect` captures screenshot + full page analysis (IMG:, HTML:). Use when stuck. |
| Verify | `python3 apply.py verify <jid>` | Check submission: DB stage, LinkedIn "you have applied" text. Updates DB if confirmed. |

### Apply notes

- **Screening** — `--answers '{"q":"val"}'`. Normalized exact match. Provide full label. See `decisions.md` for sponsorship/relocation/experience.
- **Radios** — `radio.click()` via Playwright `.check()`.
- **Resume** — `set_input_files()` on `input[type="file"][required]`. Skips optional.
- **Unfollow** — auto-unchecks "Follow X" on every page.
- **Multi-page** — fill → next → fill → ... → submit. Repeat until Submit button appears or verification succeeds.
- **Pre-flight** — `detect` first. Checks stage, PDF, page type.
- **Button priority** — Submit > Review > Next > Continue > Done. Never Back/Cancel/Save.
- **Common answers** — `--answers` exact → common_answers (exact for optional, prefix for required) → profile.
- **Field types** — INPUT, SELECT, TEXTAREA, DROPDOWN (`button[aria-haspopup]`), AUTOCOMPLETE (ph="Search").
- **Autocomplete** — JS native value setter + input/change events (Workday multiselect).
- **3x guard** — same page state 3 fills in a row → warns model to break loop.

### Account & login notes

- **Guest apply is preferred** — pipeline auto-clicks "continue without signing in" when available.
- **Repeat portals** (e.g., a second Workday): guest apply works but creates a *new* account per company. No credential reuse across sessions. If the user already has an account, they should log in manually before running `detect`.
- **Hands-free**: the pipeline cannot create accounts (email verification), remember passwords, or handle 2FA. Login-required portals always need manual intervention.

### Platform quirks

| Platform | Key quirks |
|----------|------------|
| Ashby | One-page std HTML. Recaptcha textarea at bottom — ignore. Radios grouped by `name` attr. |
| Greenhouse | No `<select>` — all dropdowns are custom autocomplete. Country is `<input>` not `<select>`. "Submit application" at bottom, not "Apply" (scroll trigger). |
| Lever | 0 fields on job page → apply-link → `/apply` URL. Labels nest: `<label><div>Text</div><div><input></div></label>`. No Select/Radio. |
| Workday | 7-step SPA: Info → Experience → Questions → Disclosures → Review. DROPDOWN=`button[aria-haspopup]`. Autocomplete multiselect = JS value + events. Phone: strip +1 prefix. Skills: type + Enter. Per-company login. |
| LinkedIn | 2 modes: Easy Apply (modal, Next/Review/Submit) vs External (`<a>` tag, safety redirect). detect checks `<a>` not just `<button>`. "…more" button = `[data-testid="expandable-text-button"]`. |

## Extraction rules

| Value | Include |
|-------|---------|
| Ontario-based (Toronto, Ottawa, Oakville, Mississauga, Waterloo, etc.) | Yes — preferred |
| Other Canada-based (Vancouver, Calgary, etc.; on-site or hybrid) | Yes |
| Remote / work-from-home (any country) | Yes |
| Quebec in-office | No |
| US on-site only | No |
| Unclear location | Fetch description, then decide |

## Notes

Attach context via `extract.py submit '{"url":"...","notes":"..."}'`.  
`tailor.py` appends `Context: {notes}` to Gemini prompt.  
Re-run with `"notes":""` to clear.

## Auth walls

Detected during fetch (sign-in keywords).  
`fetch.py flag <jid>` — manual flag.  
`fetch.py open [<jid>]` — open in Chrome (persistent session).  
Stale entries auto-pruned.

## Output directory

`~/.ji/results/{jid}/`:
- `gemini_response.txt` — Gemini output
- `script.py` — PDF build script
- `{jid}.url` — job shortcut
- `*.pdf` — CV/cover letter

## Recovery

| Signal | Fix |
|--------|------|
| `invalid_grant` | `gmail-cli auth add email` |
| TIMEOUT / RATE_LIMIT | `tailor.py retry` |
| Chrome crash | Auto-restarted by `chrome_manager.py` — no manual action needed |
| DB crash | `extract.py reset` |
| Auth wall stuck | `fetch.py open` + `fetch.py --refresh` |

## Output signals

| Signal | When | Meaning |
|--------|------|---------|
| `STATUS:` | Any step | Status update (filled count, captcha, etc.) |
| `TYPE:` | Detect | Job type (easy_apply / ats_direct / external / already_applied / login_wall / unknown) |
| `NEXT:` | Any step | Recommended next command |
| `QUIRKS:` | Detect or fill | Platform-specific notes from registry YAML — printed once per platform per session |
| `GUEST_AVAILABLE:` | Detect | Guest apply button found on login wall — pipeline will auto-click it on `act --fill` |
| `IMG:` | Inspect | Screenshot file path — read this file for visual page context if your model supports images |
| `HTML:` | Inspect | Full page DOM HTML file path — last-resort debug for page structure issues |

## Technical notes

- **JI_TAILOR**: set to `"gem"` (default) to use Gemini Web gem for CV generation. Set to `"agent"` for SLM-in-the-loop: tailor emits `PROMPT:`, SLM writes `script.py`, `done` runs it.
- **Gemini.js**: `call_gemini.py` auto-detects `node_modules` (workspace root, parent chain). `browser.close()` not awaited — handled internally.
- **LinkedIn title dedup**: Cards repeat title (visible + verification text). `linkedin.py` deduplicates: finds where second half starts matching first half.
- **Common_answers**: `--answers` exact → common_answers (exact for optional, prefix for required) → profile resolver. Never pre-populate guessed values — only save what user explicitly provides.
- **Gems**: `gems.json` maps names to IDs. `categories.json` → `gems.json` → `gemini.js` resolution chain.
