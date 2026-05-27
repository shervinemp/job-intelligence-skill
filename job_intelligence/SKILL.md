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
| **tailor** | `tailor.py [--count N]` — crafts tailored CV | SLM: done/skip/redo |

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
| Act (fill) | `python3 apply.py act --fill <jid> [--answers '{}']` | Fill ALL fields: text, selects, radios, file inputs. Uses --answers (exact normalized match) → common_answers → profile. Unfollow company checkbox. |
| Act (next) | `python3 apply.py act --next <jid>` | Click forward button (Submit > Review > Next, rightmost fallback). Never Back/Cancel/Save. Detects disabled before click. |
| Act (back) | `python3 apply.py act --back <jid>` | Click Back button. |
| Act (submit) | `python3 apply.py act --submit <jid> [--confirm]` | Click Submit on review page. Dry-run without --confirm. Checks result. |
| Act (auto) | `python3 apply.py act --auto <jid>` | Full loop: fill → next → fill → ... → submit. Stops on unfilled fields, waits for --answers. |
| Verify | `python3 apply.py verify <jid>` | Check submission: DB stage, LinkedIn "you have applied" text. Updates DB if confirmed. |

### Apply notes

- **Screening** — `--answers '{"q":"val"}'`. Normalized exact match. Provide full label text to be safe. Reference `decisions.md` for sponsorship, relocation, experience estimates.
- **Radios** — `radio.click()` via Playwright `.check()`. Verify `el.checked` changed.
- **Resume** — `set_input_files()` on required file inputs only. Skips optional drop zones.
- **Unfollow** — `act --fill` auto-unchecks "Follow X" on any page.
- **Multi-page** — `act --auto` handles the loop. Manual: fill → next → fill → ... → submit.
- **Pre-flight** — always run `detect` first. It checks stage, PDF, and page type in one call.
- **Button priority** — Submit > Review > Next > Continue > Done (rightmost). Never Back/Cancel/Save.
- **Common answers matching** — `--answers` exact match first, then common_answers (exact for optional fields; prefix for required fields — prevents generic keys like "phone" filling optional fields like "Phone Extension"), then profile resolver.
- **Field types:** INPUT (text/email/tel), SELECT, TEXTAREA, DROPDOWN (custom `button[aria-haspopup]`), AUTOCOMPLETE (placeholder="Search").
- **Autocomplete** — uses JS native value setter + input/change events for Workday multiselect widgets.
- **3x fingerprint guard** — if same page state appears 3 fills in a row, warns model to break loop.

### Platform-specific guides

| Platform | File | Key quirks |
|----------|------|------------|
| Ashby | `apply/platforms/ashby.md` | One-page, standard HTML, "Submit Application" |
| Greenhouse | `apply/platforms/greenhouse.md` | 29 fields pre-loaded, no Apply click needed |
| Lever | `apply/platforms/lever.md` | `/apply` URL after apply-link, 12 fields, label-in-label structure |
| Workday | `apply/platforms/workday.md` | 7-step SPA, DROPDOWN+autocomplete widgets, per-company login |
| LinkedIn | `apply/platforms/linkedin.md` | Easy Apply modal vs External (safety redirect) |

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

Required on first `extract.py admit --category <name> <jid>`. Override with `fetch.py admit --category <name> <jid>` when JD is visible.

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

**Gemini.js:** `call_gemini.py` auto-detects `node_modules` (workspace root, parent chain). No manual `NODE_PATH` needed. `browser.close()` on CDP connections is not awaited — `call_gemini.py` handles this internally.

**LinkedIn title dedup:** LinkedIn job cards often repeat the title (visible + hidden verification text). `linkedin.py` now deduplicates by detecting when the first half of the title string equals the second half.

**Common_answers:** Answers to form questions are accumulated in `profile.json` under `common_answers`. When the filler encounters a question, it checks `--answers` first (exact + substring match), then falls back to `common_answers` via fuzzy word-overlap matching. Never pre-populate common_answers with guessed values (visa, sponsorship, etc.) — only save what the user explicitly provides.

**Gems:** `gems.json` maps named gems to IDs: `optimizer_tech (4203d06f5d81)` for tech jobs, `optimizer_general (3697c8c02b40)` for general jobs. `categories.json` references these names. `gemini.js` resolves them at startup.
