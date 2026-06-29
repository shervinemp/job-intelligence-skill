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
| `tailor.py admit <jid>` | Confirm → stage = tailored (auto-finds resume in results dir) |
| `tailor.py build <jid>` | Validate resume.json schema + quality, build PDFs |
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

Data/build separation: the LLM writes a `resume.json` data file (no code). A shared builder (`lib/build_resume.py`) reads the JSON and produces PDFs. The JSON is easy to review for quality issues (pandering, hallucination, title creep) without parsing code.

Two backends via `JI_TAILOR` env var:

- **`JI_TAILOR=agent`** (default): Prompt printed to stdout. Write `resume.json` in [JSON Resume](https://jsonresume.org/) format (standard — LLMs already know it). Build and admit:
  ```
  tailor.py build <jid>                # schema validation + PDF generation
  tailor.py admit <jid>                # confirm and advance stage (auto-finds resume)
  ```
  The LLM reads the generated resume.json and PDFs to judge quality before admitting.
- **`JI_TAILOR=gem`**: Uses Gemini Web gem. Gem generates `resume.json`, builder runs inline. Run `admit <jid>` to confirm + advance stage.

Both routes converge on `admit` — gem route auto-builds PDFs, agent route requires manual build step. `admit` advances DB stage to "tailored" (CV ready). Apply pipeline advances to "applied" (form submitted).

**Cover letter** is stored in the `coverLetter` field of the JSON Resume (custom extension). The builder automatically generates a separate Cover Letter PDF if this field is present. The cover letter text is plain text (no markdown), maximum 3 paragraphs, no logistics (salary, availability dates).

Prompt does not include default resume — fill in CV content from candidate profile + job requirements.

## Quality Review

After tailoring, optionally review generated CVs before admitting:

```
tailor.py review [--jobs N]
```

Shows the job title, URL, and cover letter from the generated PDF. Run `report.py inspect <jid>` for the full strategy.
If the cover letter or CV needs fixes: `retry <jid> --feedback "what to fix"` — injects feedback + previous response and re-tailors immediately.

The `resume.json` data file lives at `results/<jid>/resume.json`. You can read it directly to check for quality issues: does it mention the company name? Does it pander? Are there fabricated metrics? JSON is easier to audit than fpdf2 code.

The builder also validates the JSON before generating PDFs — run `--validate` separately to check without building:

## Apply pipeline

> Auto-fill memory / unattended-submission design: `docs/adr-001-autofill-memory.md`.

```
detect [<jid>] → [navigate] → act --fill → act --next (repeat) → act --submit --confirm <jid> → verify <jid>
```

| Step | What it does |
|------|-------------|
| `detect [<jid>]` | Pre-flight: DB stage, PDF, classify type. Omit JID to auto-pick first tailored. Outputs `TYPE:` + `NEXT:`. |
| `navigate <jid>` | LinkedIn External only — click button, decode safety redirect, land on ATS. Auto-clicks "Apply now" on job listing pages. Prompts for login on auth wall — cookies persist via Chrome profile. |
| `act --fill <jid> [--answers '{}'] [--dry-run] [--shadow]` | Fill all fields. `--answers` exact → profile facts. Auto-unchecks "Follow company". `--dry-run` previews without DOM changes. Writes audit log. |
| `act --next <jid>` | Click forward (Submit > Review > Next > Continue > Done). Detects submission (→ verify) / errors (→ retry fill). |
| `act --back <jid>` | Click Back |
| `act --submit <jid> --confirm [--shadow]` | Submit. **`--confirm` req'd** — dry-run w/o. Checks validation errors, CAPTCHA, success text. `--shadow` (or `JI_APPLY_MODE=shadow`) fills + screenshots but never clicks submit. |
| `act --inspect <jid> [--candidate N]` | Full diagnostic: screenshot + HTML dump + probes + fields + buttons + dialog/iframe detection. Use when stuck. |
| `verify <jid>` | Scan open pages for success signals + optional vision check. Updates DB stage to "applied" if confirmed. |
| `apply.py reject <jid>` | Skip permanently |
| `apply.py flag <jid>` | Toggle auth wall flag |
| `apply.py retry [<jid>]` | Re-attempt failed applies |
| `apply.py undo <jid>` | Move back one stage |
| `apply.py mappings list\|confirm\|clear <jid>` | Field→meaning mapping store (Phase 3, off unless `use_mappings`). `confirm` promotes a job's pending mappings after review. |

### Apply tips

- Omit JID on `detect` to auto-pick the first tailored job from the queue.
- Auth walls: navigate prompts for login. Log in via the open browser, press Enter to continue. Type `flag` to skip. Cookies persist via Chrome profile — same platform won't re-prompt.
- `--answers` — normalized exact match (case/punctuation insensitive). Full label text.
- `--candidate N` — picks from CANDIDATES list. Works on --fill/--next/--submit/--inspect.
- `--dry-run` on `--fill` shows resolved answers without DOM modification. Validates field detection first.
- **Shadow mode** (`--shadow`, `JI_APPLY_MODE=shadow`, or `apply_policy.json {"mode":"shadow"}`): fill + screenshot + audit, never submit. Use to validate the pipeline on real jobs without applying. Default mode is `live`. See `docs/adr-001-autofill-memory.md`.
- **Audit log**: every `act --fill` appends to `~/.ji/results/<jid>/apply_audit.jsonl` (field value, provenance, tier category, filled). Read it to see what would be submitted and why.
- Multi-page: fill → next → fill → ... until Submit appears or verify passes.
- Guest apply: auto-clicks "continue without signing in" when available.
- Pipeline cannot create accounts, remember passwords, or handle 2FA.
- 3x guard: same page 3 fills in a row → warns.
- EEO/demographic fields: auto-detected by decline-option presence (language-agnostic). Saved answers persist under `common_answers.eeo` for reuse.
- Platform registry (`apply/registry/*.yaml`): per-ATS widget overrides. `widget_parent` config controls dropdown parent selector.

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
- `resume.json` — resume data in JSON Resume format
- `prompt.txt` — generation prompt with job details and rules
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

- **JI_TAILOR**: `"agent"` (default) = SLM writes `resume.json`, `admit` confirms. `"gem"` = Gemini Web gem.
- **Gemini.js**: `call_gemini.py` auto-detects `node_modules` (workspace root, parent chain).
- **LinkedIn title dedup**: Cards repeat title — `linkedin.py` deduplicates by matching repeated half.
- **Answer resolution**: `--answers` (exact normalized-label, or ≥10-char prefix for field_reader's 60-char truncation) → profile facts/derivations + `profile.answers` map. No cross-job persistence — to reuse an answer, add it to `profile.json`. See `docs/adr-001-autofill-memory.md`.
- **EEO detection**: by decline-option content ("prefer not to answer", "decline"), not label keywords — language-agnostic. Reported, not auto-filled — the LLM decides via `--answers`.
- **Tests**: `python -m unittest discover -s tests -p "test_*.py"` (stdlib, no install; pytest also discovers them). Import smoke + resolve/learner unit tests. Runs in CI on push/PR.
- **Chrome lifecycle**: Pipeline starts its own Chrome instance on a free port (never reuses user's browser). Port persisted to `chrome-config.json` across processes.
- **PDF guard**: `detect` refuses to proceed if stage is `tailored` but no Resume PDF exists. Run `tailor.py undo <jid> && tailor.py --jid <jid>` to regenerate.
- **Platform registry**: `apply/registry/*.yaml` defines per-ATS configs (`widget_parent` selector, custom widgets). Auto-resolved from page URL — no caller changes needed.
- **Gems**: `categories.json` → `gems.json` → `gemini.js` resolution chain.
