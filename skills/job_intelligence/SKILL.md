# Job Intelligence Pipeline

## Read First

Before running pipeline, read these:
- `decisions.md` — accept/reject rules per job
- `profile.json` — common form answers
- `categories.json` — job categories

## Pipeline stages

| Stage | Gate |
|-------|------|
| `stage_emails.py [--days N]` | Auto |
| `extract.py` | `admit --category <name> <jid>` / reject |
| `linkedin.py [--url] [--count N]` | admit/reject |
| `enrich.py` | admit/reject/flag |
| `tailor.py [--auto]` | admit/reject/undo/retry. See tailoring section |
| `apply.py detect/act/verify <jid>` | Follow apply pipeline |

> `tailor.py` crafts one job at a time. Use `--auto` to process all described jobs with rate-limit handling.

## Commands

| Command | Action |
|---------|--------|
| `extract.py admit --category tech/general <jid> [--notes]` | Accept w/ category |
| `extract.py reject <jid>` | Skip |
| `extract.py reset <jid>` | Delete + re-extract |
| `extract.py submit <tid> '<json>'` | Manually add URLs |
| `enrich.py admit/reject/flag <jid>` | Accept / skip / auth wall |
| `enrich.py retry` | Retry failed |
| `enrich.py retry-skipped` | Reset skipped → extracted |
| `enrich.py open [<jid>]` | Open in Chrome |
| `tailor.py [--auto]` | Start tailoring (crafst 1, --auto = all) |
| `tailor.py admit <jid> [--pdf <path>]` (also: done) | Confirm PDF → stage = tailored |
| `tailor.py reject <jid>` | Skip |
| `tailor.py undo <jid>` | Move back one stage |
| `tailor.py review [--jobs N]` | Review tailored jobs (approve or retry --feedback) |
| `tailor.py retry [<jid>] [--feedback "x"]` | Retry failed or re-tailor with feedback |
| `tailor.py reset --state failed` | Reset by state |
| `tailor.py reset --stage tailored` | Reset by stage |
| `extract.py reset` | Wipe DB, fresh start |
| `lib/ask_api.py [--img <path>] --prompt <text>` | Query LLM API |
| `report.py stats` | Pipeline state + next step |

## Tailoring

Two backends via `JI_TAILOR` env var:

- **`JI_TAILOR=agent`** (default): Prompt printed to stdout. Write `script.py` based on instructions, run it to produce PDF, `admit <jid> --pdf <path>` verifies file + advances stage.
- **`JI_TAILOR=gem`**: Uses Gemini Web gem. Gem generates `script.py`, pipeline runs it inline, PDF appears. Run `admit <jid>` to confirm + advance stage.

Both routes converge on `admit` (or `done` for backward compat) — gem route skips `--pdf` (PDF auto-generated), agent route should provide it. `admit` advances DB stage to "tailored" (CV ready). Apply pipeline advances to "applied" (form submitted).

Prompt does not include default resume — fill in CV content from candidate profile + job requirements.

## Quality Review

After tailoring, optionally review generated CVs before admitting:

```
tailor.py review [--jobs N]
```

Shows the job title, URL, and cover letter snippet. Run `report.py inspect <jid>` for the full strategy.
If the cover letter or CV needs fixes: `retry <jid> --feedback "what to fix"` — injects feedback + previous response and re-tailors immediately.

## Apply pipeline

```
detect [<jid>] → [navigate] → act --fill (repeats if paused) → verify <jid>
```

For platforms with ephemeral modals (LinkedIn Easy Apply), `act --fill` runs a **flow hook** that handles the entire submission (fill → next → review → submit) in one process. If fields need LLM input, it pauses and emits `NEXT: act --fill --answers '...'`. Re-run with answers to continue.

For standard ATS pages, the step-by-step flow still applies:
```
act --fill → act --next (repeat) → act --submit --confirm
```

| Step | What it does |
|------|-------------|
| `detect [<jid>]` | Pre-flight: DB stage, PDF, classify type. Omit JID to auto-pick first tailored. Outputs `TYPE:` + `NEXT:`. |
| `navigate <jid>` | LinkedIn External only — click button, decode safety redirect, land on ATS. Auto-clicks "Apply now" on job listing pages. Prompts for login on auth wall — cookies persist via Chrome profile. |
| `act --fill <jid> [--answers '{}'] [--dry-run]` | Fill fields. For platforms with a `flow_hook` in registry (LinkedIn), runs full submission flow in one process. For others, standard fill. `--answers` exact → profile. `--dry-run` previews without DOM changes. |
| `act --next <jid>` | Click forward (Submit > Review > Next > Continue > Done). Detects submission (→ verify) / errors (→ retry fill). Not used for flow-hook platforms. |
| `act --back <jid>` | Click Back |
| `act --submit <jid> --confirm` | Submit. **`--confirm` req'd**. Not used for flow-hook platforms. |
| `act --inspect <jid> [--candidate N]` | Full diagnostic: screenshot + HTML dump + probes + fields + buttons + dialog/iframe detection. Use when stuck. |
| `verify <jid>` | Scan open pages for success signals + optional vision check. Updates DB stage to "applied" if confirmed. |
| `apply.py reject <jid>` | Skip permanently |
| `apply.py flag <jid>` | Toggle auth wall flag |
| `apply.py retry [<jid>]` | Re-attempt failed applies |
| `apply.py undo <jid>` | Move back one stage |

### Apply tips

- Omit JID on `detect` to auto-pick the first tailored job from the queue.
- Auth walls: navigate prompts for login. Log in via the open browser, press Enter to continue. Type `flag` to skip. Cookies persist via Chrome profile — same platform won't re-prompt.
- `--answers` — normalized exact match (case/punctuation insensitive). Full label text.
- `--candidate N` — picks from CANDIDATES list. Works on --fill/--next/--submit/--inspect.
- `--dry-run` on `--fill` shows resolved answers without DOM modification. Validates field detection first.
- Multi-page: fill → next → fill → ... until Submit appears or verify passes.
- Guest apply: auto-clicks "continue without signing in" when available.
- Pipeline cannot create accounts, remember passwords, or handle 2FA.
- 3x guard: same page 3 fills in a row → warns.
- EEO/demographic fields: auto-detected by decline-option presence (language-agnostic). Saved answers persist under `common_answers.eeo` for reuse.
- Platform registry (`apply/registry/*.yaml`): per-ATS config. `flow_hook` property declares a re-entrant flow hook (called by `act --fill`) for platforms with ephemeral modals. `widget_parent` config controls dropdown parent selector.

## Platform quirks

| Platform | Notes |
|----------|-------|
| Ashby | One-page HTML. Recaptcha textarea at bottom — ignore. Radios grouped by `name`. |
| Greenhouse | No `<select>` — all custom autocomplete. Country = `<input>`. "Submit application" = real button ("Apply" = scroll trigger). |
| Lever | 0 fields → follow apply-link → `/apply`. Labels: `<label><div>Text</div><div><input></div></label>`. No Select/Radio. |
| Workday | 7-step SPA (Info → Experience → Questions → Disclosures → Review). DROPDOWN=`button[aria-haspopup]`. Phone: strip +1 prefix. Skills: type + Enter. Per-company login. |
| LinkedIn | Easy Apply: ephemeral modal — `flow_hook` in registry/linkedin.py handles full flow in `act --fill`. Resume selection via label click + event dispatch. External: `<a>` tag with safety redirect. |

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
| Quebec on-site/hybrid | No |
| Quebec remote | Yes — not physically in Quebec |
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
`enrich.py flag <jid>` — manual flag.  
`enrich.py open [<jid>]` — open in Chrome (persistent session).  
`report.py archive` — archive state/registry entries for reset jobs.

Attach context via `extract.py submit '{"url":"...","notes":"..."}'`.  
Notes are injected into the prompt after the job description. Clear with `"notes":""`.

## Recovery

| Problem | Fix |
|---------|-----|
| `invalid_grant` | `gmail-cli auth add email` |
| TIMEOUT / RATE_LIMIT | `tailor.py retry` |
| Chrome crash | Auto-restarted — do nothing |
| DB crash | `extract.py reset` |
| Auth wall stuck | `enrich.py open` + `--refresh` |

## Technical notes

- **JI_TAILOR**: `"agent"` (default) = SLM writes `script.py`, `admit` confirms. `"gem"` = Gemini Web gem.
- **Gemini.js**: `call_gemini.py` auto-detects `node_modules` (workspace root, parent chain).
- **LinkedIn title dedup**: Cards repeat title — `linkedin.py` deduplicates by matching repeated half.
- **Common_answers**: `--answers` exact → common_answers (exact optional, prefix required) → profile. Never pre-populate — save only user-provided values.
- **EEO detection**: Uses decline-option content ("prefer not to answer", "decline"), not label keywords — language-agnostic, zero false positives. Saved under `common_answers.eeo` sub-key.
- **Chrome lifecycle**: Pipeline starts its own Chrome instance on a free port (never reuses user's browser). Port persisted to `chrome-config.json` across processes.
- **PDF guard**: `detect` refuses to proceed if stage is `tailored` but no Resume PDF exists. Run `tailor.py undo <jid> && tailor.py --jid <jid>` to regenerate.
- **Platform registry**: `apply/registry/*.yaml` defines per-ATS configs (`widget_parent` selector, custom widgets). Auto-resolved from page URL — no caller changes needed.
- **Gems**: `categories.json` → `gems.json` → `gemini.js` resolution chain.
