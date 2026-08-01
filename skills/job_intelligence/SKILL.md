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
| `enrich.py` | admit/reject/flag (add `--team <name>` for team discovery) |
| `tailor.py [--auto]` | admit/reject/undo/retry |
| `reach.py discover/list <jid>` | Contact discovery + outreach |
| `reach.py email/message/connect <jid>` | Send email / LinkedIn DM / connect request |
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
| `reach.py discover <jid> [--team <name>]` | Discover contacts (recruiters, team members, connections) |
| `reach.py discover --all [--limit N]` | Discover for all described/tailored jobs without contacts |
| `reach.py list <jid>` | Show discovered contacts for a job |
| `reach.py email <jid> --contact N [--body <text>] [--force]` | Send email via Gmail |
| `reach.py message <jid> --contact N [--body <text>] [--force]` | Send LinkedIn DM |
| `reach.py connect <jid> --contact N [--note <text>]` | Send LinkedIn connection request |
| `reach.py update <jid> --contact N [--email <addr>] [--note <text>] [--set-sent email\|message]` | Backfill/edit contact fields |
| `reach.py attempts [<jid>]` | Show outreach attempts |
| `reach.py status` | Pipeline state with contact/outreach summary |
| `reach.py retry <jid>` | Re-run contact discovery |
| `reach.py undo <jid>` | Reset contact state for a job |
| `report.py connections [company]` | My connections grouped by company |
| `report.py outreach` | Outreach attempts + pending contacts |
| `gmail-cli send <to> <subject> --body <text>` | Send email (low-level) |
| `gmail-cli auth add <email> --services gmail.send` | Auth Gmail send scope |


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

```
detect [<jid>] → [navigate] → act --fill → act --next (repeat) → act --submit <jid> → verify <jid>
```

| Step | What it does |
|------|-------------|
| `detect [<jid>]` | Pre-flight: DB stage, PDF, classify type. Omit JID to auto-pick first tailored. Outputs `TYPE:` + `NEXT:`. |
| `navigate <jid>` | LinkedIn External only — click button, decode safety redirect, land on ATS. Auto-clicks "Apply now" on job listing pages. Prompts for login on auth wall — cookies persist via Chrome profile. |
| `act --fill <jid> [--answers '{}']` | Fill all fields. `--answers` exact → common_answers → profile. Auto-unchecks "Follow company". |
| `act --next <jid>` | Click forward (Submit > Review > Next > Continue > Done). Detects submission (→ verify) / errors (→ retry fill). |
| `act --back <jid>` | Click Back |
| `act --submit <jid>` | Submit. Runs pre-submit check. Sets `submit_clicked` flag before clicking. Investigates (no re-click) on retry. `--force` clears guard. |
| `act --inspect <jid> [--candidate N]` | Full diagnostic: screenshot + HTML dump + probes + fields + buttons + dialog/iframe detection. Use when stuck. |
| `verify <jid>` | Scan open pages for success signals + optional vision check. Updates DB stage to "applied" if confirmed. |
| `apply.py reject <jid>` | Skip permanently |
| `apply.py flag <jid>` | Toggle auth wall flag |
| `apply.py retry [<jid>]` | Re-attempt failed applies |
| `apply.py undo <jid>` | Move back one stage |

### Apply workflow — phased approach

Each pipeline step has a distinct goal. Follow this mental model:

```
─── PHASE 1: RECONNAISSANCE ───
detect → Read the page. Classify the type. Do NOT fill anything.
          Output: TYPE: easy_apply / ats_direct / external / login_wall

─── PHASE 2: FIELD INVENTORY ───
act --fill → Catalogs every field on the page.
                        Note fields resolved automatically (✅) vs.
                        fields needing your input (❓).
                        Do NOT provide --answers yet.
                        Output: categorized DRY_RUN listing.

─── PHASE 3: TARGETED FILLING ───
act --fill --answers '{"label": "value"}' → Fill fields marked ❓.
    • Fill ALL required fields in one shot. SPA forms wipe everything on validation error.
    • Only fill fields you're confident about.
    • Check every value against profile answers. Don't contradict.
    • Leave salary, dates, referral source unfilled unless profile has them.
    • The preview shows provenance (profile/answers/derived/auto_decline)
      for every value. Check it before confirming.
    • If unfilled remain, repeat with more --answers.

─── PHASE 4.5: PREVIEW (MANDATORY) ───
act --submit → Read every value in the preview.
    Compare against Profile answers block. Flag contradictions.

─── PHASE 4: NAVIGATE & REPEAT ───
act --next → Advance to next page.
             If submission detected → routed to --submit.
             If validation errors → routed back to --fill.

─── PHASE 5: SUBMIT (ONE-SHOT) ───
act --submit → Pre-submit check runs automatically.
              Submit is clicked ONCE. The pipeline records submit_clicked
              before clicking. If the outcome is uncertain (no success
              signal, no validation error), it marks as applied
              conservatively and flags for review.
              On the next run, if submit_clicked is set, the pipeline
              INVESTIGATES the page — success text, URL change, form
              disappearance, validation errors, vision API — it does
              NOT click submit again.
              Use --force only after human confirms the previous
              attempt truly failed.

─── PHASE 6: VERIFY ───
verify → Confirm the application was received.
          Never skip this step. If STATUS was "uncertain", verify
          is especially important — check email for confirmation,
          or visit the ATS page to check application status.
```

### Apply tips

- Omit JID on `detect` to auto-pick the first tailored job from the queue.
- Auth walls: navigate prompts for login. Log in via the open browser, press Enter to continue. Type `flag` to skip. Cookies persist via Chrome profile — same platform won't re-prompt.
- `--answers` — normalized exact match (case/punctuation insensitive). Full label text.
- `--candidate N` — picks from CANDIDATES list. Works on --fill/--next/--submit/--inspect.
- Fill report shows resolved answers and field detection for each field.
- Multi-page: fill → next → fill → ... until Submit appears or verify passes.
- Guest apply: auto-clicks "continue without signing in" when available.
- Pipeline cannot create accounts, remember passwords, or handle 2FA.
- 3x guard: same page 3 fills in a row → warns.
- EEO/demographic fields: auto-detected by decline-option presence (language-agnostic). Saved answers persist under `common_answers.eeo` for reuse.
- Platform registry (`apply/registry/*.yaml`): per-ATS config. `handler_class` field loads `PlatformHandler` from `apply/handlers/`. See `handler_base.py` header for add-platform guide.

## Reach (outreach)

Optional parallel track after `enrich`/`tailor`. Contact discovery finds recruiters (job page), team members (company LinkedIn people page, filtered by team keywords), and my 1st-degree connections (LinkedIn search with numeric company ID).

- **Flow**: `enrich.py admit --team <name>` → `reach.py discover <jid>` (or `reach.py discover --all` after a batch) → `reach.py list <jid>` → outreach.
- **One-shot guards**: `email_sent` / `message_sent` prevent re-sending. `--force` re-sends after human verification. `reach.py undo <jid>` resets.
- **Uncertain sends**: if a DM send is clicked but unconfirmed, status is `uncertain` (attempt logged as `pending`) — check the LinkedIn inbox manually, then `reach.py update --set-sent message` to confirm or retry with `--force`. Never silently resend.
- **Premium**: 2nd/3rd-degree contacts use the InMail composer (`.msg-inmail-credits-display`); the pipeline proceeds and reports `INMAIL_COMPOSER`. `CONNECT_REQUIRED` → run `reach.py connect`. Free accounts always see InMail only for non-connections.
- **Contact indices**: `--contact N` matches the numbering printed by `discover`/`list` (DB order).
- **Email suggestions** from the LLM are never sent automatically — backfill with `reach.py update --email <addr>` after human verification.
- **Attempts**: every outreach is recorded in `contact_attempts` (status: pending/sent/failed).
- **Verified LinkedIn selectors** (2026-07 live DOM): people cards `li.org-people-profile-card__profile-card-spacing`; DM = typeahead flow (`input.msg-connections-typeahead__search-field` → mouse-click suggestion → `div.msg-form__contenteditable` → `button.msg-form__send-btn`); compose URL recipient params do NOT work. See `lib/linkedin_messaging.py` header.

## Orchestrator rules

1. **Don't guess personal data.** Check profile + resume first. Missing → ask (critical) or skip (optional).
2. **Don't fill optional fields.** Not marked required → leave it.
3. **Don't echo PII.** Labels only, never values in output.
4. **Always verify after `NEXT: verify`.** DB can be stale.
5. **Inspect when stuck.** Don't retry blind.
6. **Don't collapse gates.** Dry-run → fill → preview → confirm. Each its own round-trip. See [#apply-pipeline](apply-pipeline).
7. **Preview before submit.** Always run `act --submit` first. Read every value. Check against profile answers. Fix contradictions before proceeding.
8. **One-shot fill for SPA forms.** Validation fail → page reloads → all values lost. Fill everything then submit once.
9. **Autocomplete fields need clicks, not text.** Flag for user help.
10. **Don't contradict profile answers.** Before filling any field, check the `Profile answers:` block. If what you're about to fill contradicts a profile answer, stop and fix it.
11. **No custom submit scripts.** Use the pipeline. No `page.evaluate` clicks, no standalone scripts.
12. **Submit is one-shot.** The pipeline sets `submit_clicked` before clicking. If success detection is uncertain, the next run *investigates* the page (success signals, URL change, form disappearance, validation errors, vision API) — it NEVER clicks submit again. Only `--force` (manual, after human confirms failure) or `undo` clears the guard.
13. **When uncertain, investigate hard.** The outcome cascade tries: success text → already-applied text → `_check_submit_success` → URL change → form disappearance → validation errors → vision API screenshot. Only after ALL methods are exhausted does it mark as applied (conservative) and flag for human review.
14. **Validation errors = safe to retry.** If the form rejected with validation errors, `submit_clicked` is cleared — the next run can fill the missing fields and re-submit.
15. **Never submit blind.** `--fill --submit` together is BLOCKED. Always review the fill report between fill and submit. The orchestrator reads UNFILLED fields, supplies `--answers`, then submits.

## Platform quirks

Platform-specific notes live in `apply/registry/*.yaml` under the `notes:` field — the single source of truth next to the detection rules and patterns they describe. Read the registry file for the ATS you're working on.

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
| Gmail send auth | `gmail-cli auth add <email> --services gmail.send` |
| Contact discovery stuck | `reach.py retry <jid>` |
| LinkedIn not signed in | Open Chrome to linkedin.com, sign in manually, then retry |
| LinkedIn rate limited | Wait 5 min, `reach.py retry <jid>` |



## Technical notes

- **JI_TAILOR**: `"agent"` (default) = SLM writes `resume.json`, `admit` confirms. `"gem"` = Gemini Web gem.
- **Gemini.js**: `call_gemini.py` auto-detects `node_modules` (workspace root, parent chain).
- **LinkedIn title dedup**: Cards repeat title — `linkedin_scraper.py` deduplicates by matching repeated half.
- **Common_answers**: `--answers` exact → common_answers (exact optional, prefix required) → profile. Never pre-populate — save only user-provided values.
- **EEO detection**: Uses decline-option content ("prefer not to answer", "decline"), not label keywords — language-agnostic, zero false positives. Saved under `common_answers.eeo` sub-key.
- **Chrome lifecycle**: Pipeline starts its own Chrome instance on a free port (never reuses user's browser). Port persisted to `chrome-config.json` across processes. Profile lives at `~/.ji/chrome-profile/` — sessions (cookies, localStorage) persist between pipeline runs.
- **Injected agent** (`apply/common/agent.js`): Auto-injected into every page via `context.add_init_script()` in `chrome_manager.connect()`. Provides `window.__opencode` with framework auto-detection, unified `setValue()` (jQuery → React nativeValueSetter → vanilla), `click()` with disabled re-enable, `fillAutocomplete(label, value)` for multiselect dropdowns (Province, Country), MutationObserver field discovery (no polling), value change tracking, and console error capture. Bridge wrapper at `apply/common/agent_bridge.py` with `fill_autocomplete()` helper. All calls use optional chaining — if agent fails to inject, pipeline falls through to legacy strategies with zero regressions.
- **PDF guard**: `detect` refuses to proceed if stage is `tailored` but no Resume PDF exists. Run `tailor.py undo <jid> && tailor.py --jid <jid>` to regenerate.
- **Platform registry**: `apply/registry/*.yaml` defines per-ATS configs (`widget_parent` selector, custom widgets). Auto-resolved from page URL — no caller changes needed.
- **Gems**: `categories.json` → `gems.json` → `gemini.js` resolution chain.
- **Type normalization at boundaries**: All external data sources (profile.json, --answers JSON, common_answers) are normalized to their expected types at the load point, not at each consumer. If a value can be int or string (`"salary": 120000`), it's normalized to string once. If `common_answers` is accidentally a string instead of dict, it's coerced to `{}` at validation time. Add new normalizations to `_validate_profile` in `act.py` or the `--answers` parse block — never guard at individual access sites.
- **Provenance tracking**: Every filled field is tagged with its source (`profile`, `answers`, `auto_decline`, `file`). The submit preview groups fields by provenance so all LLM-provided answers appear in one block for self-audit. Cross-page field values are tracked in `_field_values_history` and compared at submit time via `_reconcile_fields` — mismatches are printed before confirm.
- **Format hints**: Unfilled fields in dry-run and fill-report display expected format hints derived from HTML type/attributes and label keywords (phone → digits only, salary → numeric, date → MM/DD/YYYY). Also shows `max N chars` and `pattern=...` from HTML attributes. Hints are informational only — the LLM decides how to use them.
- **DIAG diagnostics**: Structured `DIAG:` lines emitted on fill failures — truncation (`DIAG: Phone | expected=+1 (343)... | actual=+1 (343) 5 | truncated | maxlength=10`), verify failure, and delta mismatch (unchanged / still empty / cleared). Machine-parseable for the LLM orchestrator to auto-correct on next iteration.
- **Vision fallback probe**: When all DOM probe strategies return 0 fields and `ask_api.available()` is True, the probe cascade takes a screenshot and asks the vision LLM to identify form fields. Best-effort — labels are fuzzy-matched to DOM elements by text proximity. Last resort before `html_scan`.
