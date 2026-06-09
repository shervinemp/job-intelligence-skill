# Job Intelligence Pipeline

> Windows: prefix commands w/ `python3`. Bare `.py` opens in VSCode.

## Pipeline stages

| Stage | Gate |
|-------|------|
| `stage_emails.py [--days N]` | Auto |
| `extract.py` | `admit --category <name> <jid>` / reject |
| `linkedin.py [--url] [--max N]` | admit/reject |
| `fetch.py [--count N]` | admit/reject/flag |
| `tailor.py [--count N]` | done/skip/redo. See tailoring section |
| `apply.py detect/act/verify <jid>` | Follow apply pipeline |

## Commands

| Command | Action |
|---------|--------|
| `extract.py admit --category tech/general <jid> [--notes]` | Accept w/ category |
| `extract.py reject <jid>` | Skip |
| `extract.py reset <jid>` | Delete + re-extract |
| `extract.py review [--count N]` | Browse staged emails |
| `extract.py submit <tid> '<json>'` | Manually add URLs |
| `fetch.py admit/reject/flag <jid>` | Accept / skip / auth wall |
| `fetch.py retry` | Retry failed |
| `fetch.py retry-skipped` | Reset skipped → extracted |
| `fetch.py open [<jid>]` | Open in Chrome |
| `tailor.py [--count N]` | Start tailoring (default 1, -1 = all) |
| `tailor.py done <jid> [--pdf <path>]` | Confirm PDF → stage = tailored |
| `tailor.py skip <jid>` | Skip |
| `tailor.py redo <jid>` | Re-tailor |
| `tailor.py retry` | Retry failed |
| `tailor.py reset --from failed,skipped` | Reset by stage |
| `tailor.py ready [<jid>]` | Open results folder |
| `extract.py reset` | Wipe DB, fresh start |
| `status` | Pipeline state + next step |

## Tailoring

Two backends via `JI_TAILOR` env var:

- **`JI_TAILOR=gem`** (default): Uses Gemini Web gem. Gem generates `script.py`, pipeline runs it inline, PDF appears. Run `done <jid>` to confirm + advance stage.
- **`JI_TAILOR=agent`**: Prompt printed to stdout. Write `script.py` based on instructions, run it to produce PDF, `done <jid> --pdf <path>` verifies file + advances stage.

Both routes converge on `done` — gem route skips `--pdf` (PDF auto-generated), agent route should provide it. `done` advances DB stage to "tailored" (CV ready). Apply pipeline advances to "applied" (form submitted).

Prompt does not include default resume — fill in CV content from candidate profile + job requirements.

## Apply pipeline

```
detect <jid> → [navigate] → act --fill → act --next (repeat) → act --submit --confirm <jid> → verify <jid>
```

| Step | What it does |
|------|-------------|
| `detect <jid>` | Pre-flight: DB stage, PDF, classify type (easy_apply / external / ats_direct / already_applied / login_wall). Outputs `TYPE:` + `NEXT:`. |
| `navigate <jid>` | LinkedIn External only — click button, decode safety redirect, land on ATS |
| `act --fill <jid> [--answers '{}']` | Fill all fields. `--answers` exact → common_answers → profile. Auto-unchecks "Follow company". |
| `act --next <jid>` | Click forward (Submit > Review > Next > Continue > Done). Detects submission (→ verify) / errors (→ retry fill). |
| `act --back <jid>` | Click Back |
| `act --submit <jid> --confirm` | Submit. **`--confirm` req'd** — dry-run w/o. Checks validation errors, CAPTCHA, success text. |
| `act --inspect <jid> [--candidate N]` | Full diagnostic: screenshot + HTML dump + probes + fields + buttons. Use when stuck. |
| `verify <jid>` | Scan open pages for "thank you"/"submitted" text. Updates DB stage to "applied" if confirmed. |

### Apply tips

- `--answers` — normalized exact match (case/punctuation insensitive). Full label text.
- `--candidate N` — picks from CANDIDATES list. Works on --fill/--next/--submit/--inspect.
- Multi-page: fill → next → fill → ... until Submit appears or verify passes.
- Guest apply: auto-clicks "continue without signing in" when available.
- Pipeline cannot create accounts, remember passwords, or handle 2FA.
- 3x guard: same page 3 fills in a row → warns.

## Platform quirks

| Platform | Notes |
|----------|-------|
| Ashby | One-page HTML. Recaptcha textarea at bottom — ignore. Radios grouped by `name`. |
| Greenhouse | No `<select>` — all custom autocomplete. Country = `<input>`. "Submit application" = real button ("Apply" = scroll trigger). |
| Lever | 0 fields → follow apply-link → `/apply`. Labels: `<label><div>Text</div><div><input></div></label>`. No Select/Radio. |
| Workday | 7-step SPA (Info → Experience → Questions → Disclosures → Review). DROPDOWN=`button[aria-haspopup]`. Phone: strip +1 prefix. Skills: type + Enter. Per-company login. |
| LinkedIn | Easy Apply (modal, Next/Review/Submit) vs External (`<a>` tag, safety redirect). detect checks `<a>` + `<button>`. |

## Account & login notes

- Guest apply auto-clicked when available.
- Repeat portals (e.g., 2nd Workday): guest apply works but creates new account per company. No credential reuse.
- Pipeline cannot create accounts, remember passwords, handle 2FA. Login walls need manual intervention.

## Extraction rules

| Location | Include? |
|----------|----------|
| Ontario (Toronto, Ottawa, Oakville, Mississauga, Waterloo, etc.) | Yes — preferred |
| Other Canada (Vancouver, Calgary, etc.; on-site/hybrid) | Yes |
| Remote / work-from-home (any country) | Yes |
| Quebec in-office | No |
| US on-site only | No |
| Unclear | Fetch description, then decide |

## Output signals

| Signal | When | Meaning |
|--------|------|---------|
| `STATUS:` | Any | Status update (filled count, captcha, guest_available, submitted) |
| `TYPE:` | detect | Job type: easy_apply / ats_direct / external / already_applied / login_wall / unknown |
| `NEXT:` | Any | Next command |
| `QUIRKS:` | detect/fill | Platform notes from YAML — once per session |
| `GUEST_AVAILABLE:` | detect | Guest button found — auto-clicked on fill |
| `IMG:` | inspect | Screenshot path. Read for visual context |
| `HTML:` | inspect | Full DOM dump path. Last-resort debug |

## Output directory

`~/.ji/results/{jid}/`:
- `gemini_response.txt` — Gemini output (gem route)
- `script.py` — PDF build script
- `*.pdf` — CV / cover letter
- `{jid}.url` — job shortcut (Windows)

## Auth walls

Detected during fetch (sign-in keywords).  
`fetch.py flag <jid>` — manual flag.  
`fetch.py open [<jid>]` — open in Chrome (persistent session).  
Stale entries auto-pruned.

Attach context via `extract.py submit '{"url":"...","notes":"..."}'`.  
`tailor.py` appends `Context: {notes}` to gem prompt. Re-run w/ `"notes":""` to clear.

## Recovery

| Problem | Fix |
|---------|-----|
| `invalid_grant` | `gmail-cli auth add email` |
| TIMEOUT / RATE_LIMIT | `tailor.py retry` |
| Chrome crash | Auto-restarted — do nothing |
| DB crash | `extract.py reset` |
| Auth wall stuck | `fetch.py open` + `--refresh` |

## Technical notes

- **JI_TAILOR**: `"gem"` (default) = Gemini Web gem. `"agent"` = SLM writes `script.py`, `done` confirms.
- **Gemini.js**: `call_gemini.py` auto-detects `node_modules` (workspace root, parent chain).
- **LinkedIn title dedup**: Cards repeat title — `linkedin.py` deduplicates by matching repeated half.
- **Common_answers**: `--answers` exact → common_answers (exact optional, prefix required) → profile. Never pre-populate — save only user-provided values.
- **Gems**: `gems.json` → `categories.json` → `gemini.js` resolution chain.
