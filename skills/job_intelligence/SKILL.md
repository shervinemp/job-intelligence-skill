# Job Intelligence Pipeline

> **Read ETHOS.md first** — the operating contract: deterministic core is the
> source of truth, ask_api is a gated escape hatch (vision + code-exhausted
> ambiguity only), and the orchestrator (LLM-in-the-middle) is the operator,
> verifier and debugger consuming the evidence trail. `report.py glossary`
> prints the canonical vocabulary (generated from `apply/common/terms.py`).

## Read First

Before running pipeline, read these:
- `decisions.md` — accept/reject rules per job
- `profile.json` — common form answers
- `categories.json` — job categories

## Orchestrator surface (`ji`) — the ONE surface to memorize

`ji.py` is the orchestrator's single command surface (SURFACE_AUDIT.md). Every
action, evidence, and decision command forwards to the owning engine; the
orchestrator does not need to memorize apply.py / report.py directly.

```
ji status            fleet + READY + HOLD + decisions, one NEXT
ji decisions         THE inbox (owner-split)       ji answer <jid> "<label>": "<value>"
ji verify <jid>      RISK-VALUE REVIEW (values!)    ji ready              risk-observed jobs
ji job|diff|audit <jid>   dossier / canary / log    ji apply|submit|shadow|fetch|tailor
ji stats|fleet|candidates|pending|profile|rules|keywords|domains|spc|adjudicate|...  (forward)
```

Distinguish the two `verify` verbs:
- `ji verify <jid>` — **risk-value review**: shows the resolved values (the
  sanctioned PII view) for orchestrator confirmation before submit.
- `apply.py verify <jid>` — **post-submit check**: scans for success signals
  after a submit.

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

## Change protocol (cooked-in lessons — do NOT skip)

After ANY hot-path change (fill/check/submit/resolve/shadow/terms):
1. `python scripts/lint.py` — compile, vocabulary literals, dead strings, nested
   dirs (any dir repeating its parent's name), and **CLI docs** (a documented
   command must dispatch; a dispatchable command must be documented). Must PASS.
2. `python -m pytest tests -q` — full suite. Must PASS.
3. One live shadow job (regression canary compares vs its previous run) —
   e.g. `apply.py shadow --jid <lyft>` after removing it from the log.
4. `report.py shadow --classify` — verify the outcome landed as expected.
5. MANUAL sync — never scripted blindly:
   a. Review the change set in the workspace (git diff / file inventory).
   b. Check the repo side: has IT changed since the last sync? If yes,
      STOP — reconcile direction first. Never let a one-way copy decide.
   c. Copy only the intended files, deliberately.
   d. Verify BOTH directions with a symmetric two-way diff: every file in
      the workspace must equal its counterpart in the repo, no orphans on
      either side. Differences after the copy = a mistake, not a sync.
6. Repo suite: `python -m pytest skills/job_intelligence/tests -q` (from the repo root). Must PASS.
7. Commit + push from the repo.

Never Copy-Item a directory by hand (the apply/apply nesting lesson).
A sync tool that cannot detect direction is poison: hash equality is not
proof of intent.

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
| `tailor.py [--jid <jid>] [--auto]` | Start tailoring (crafts 1; --auto = all described) |
| `tailor.py admit <jid>` | Confirm → stage = tailored (auto-finds resume in results dir) |
| `python -m lib.build_resume <resume.json> <out_dir>` | Validate schema + build CV/cover PDFs (`--validate` = check only) |
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
| `reach.py undo <jid> [--confirm]` | Reset contact state (--confirm required once real outreach exists) |
| `report.py handovers [USER\|ORCHESTRATOR\|DATA\|REVIEW]` | THE decisions inbox: every open decision, grouped by owner, with evidence + answer commands |
| `report.py help` | The grouped surface map (decisions / evidence / fleet / readiness) |
| `report.py widgets` | Unhandled widget-class backlog (probe-failure artifacts) |
| `gmail-cli send <to> <subject> --body <text>` | Send email (low-level) |
| `gmail-cli auth add <email> --services gmail.send` | Auth Gmail send scope |
| `extract.py auto` | Auto-extract URLs from staged emails |
| `apply.py registry <action>` | Probe observations: candidates / confirm / clear / corpus / failures / drift |
| `report.py candidates [--limit N]` | Tailored jobs ready to apply |
| `report.py archive` | Archive state/registry entries for reset jobs |


## Tailoring

Full guide in `tailoring.md`. The essentials: the LLM writes a `resume.json` data file (no code); `lib/build_resume.py` reads the JSON and produces PDFs; both `JI_TAILOR` routes converge on `tailor.py admit <jid>` (grounding gate + stage advance to "tailored").

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
| `act --submit <jid>` | Submit. Runs pre-submit check (incl. cross-field coherence) + regression canary. Sets `submit_clicked` flag before clicking. Investigates (no re-click) on retry. `--force` clears guard + gates. |
| `act --check <jid>` | Pre-submit validation: cross-field contradictions |
| `act --inspect <jid>` | Full diagnostic: screenshot + HTML dump + probes + fields + buttons + dialog/iframe detection. Use when stuck. |
| `act --investigate <jid>` | Deep-analyze unknown platform |
| `verify <jid>` | Scan open pages for success signals + optional vision check. Updates DB stage to "applied" if confirmed. |
| `apply.py reject <jid>` | Skip permanently |
| `apply.py flag <jid>` | Toggle auth wall flag |
| `apply.py retry [<jid>]` | Re-attempt failed applies |
| `apply.py undo <jid>` | Move back one stage |
| `apply.py preflight` | Profile readiness gate: hard/soft/answer-gaps/coverage. Run BEFORE any batch — an incomplete profile (e.g. missing `work_history`) fails the fleet identically. |
| `apply.py shadow [--jid ...] [--recheck] [--quick]` | Observability batch (subprocess per job, never submits). `--recheck` re-examines the unconfirmed-skip queue (cookie/session-variance candidates). |
| `report.py shadow --classify` | Orchestrator verification view: outcomes, crash evidence, owner-split (code/data/handover), unconfirmed clustering. |
| `report.py fleet` | Fleet accuracy report: kinds, method attribution (deterministic vs combobox vs llm), weekly trend, steering memo of top failing labels. |
| `report.py glossary` | The vocabulary — generated from apply/common/terms.py (cannot drift). |
| `tailor.py ground <jid>` | Factual-grounding manifest: every tailored claim must trace to profile.json. `admit` is blocked until clean (`--force` after human review). |

### Apply workflow — phased approach

Phase-by-phase walkthrough in `apply-pipeline.md`. The pipeline is one-shot on submit: fill → next (repeat) → submit (preview + rules 12-13) → verify (rule 4).

### Apply tips

- Auth walls: navigate prompts for login. Log in via the open browser, press Enter to continue. Type `flag` to skip. Cookies persist via Chrome profile — same platform won't re-prompt.
- `--answers` — normalized exact match (case/punctuation insensitive). Full label text.
- Fill report shows resolved answers and field detection for each field.
- Guest apply: auto-clicks "continue without signing in" when available.
- 3x guard: same page 3 fills in a row → warns.
- EEO/demographic fields: auto-detected by decline-option presence (language-agnostic). Saved answers persist under `common_answers.eeo` for reuse.
- Platform registry (`apply/registry/*.yaml`): per-ATS widget config, auto-resolved from the page URL — no caller changes needed.

## Reach (outreach)

Optional parallel track after `enrich`/`tailor`. Contact discovery finds recruiters (job page), team members (company LinkedIn people page, filtered by team keywords), and my 1st-degree connections (LinkedIn search with numeric company ID).

- **Flow**: `enrich.py admit --team <name>` → `reach.py discover <jid>` (or `reach.py discover --all` after a batch) → `reach.py list <jid>` → outreach.
- **One-shot guards**, per channel and per person:
  - same row: `email_sent` (email), `message_sent` (message), a prior
    `linkedin_connect` attempt (connect — connect had *no* same-row guard,
    so a re-run after a crash sent a second invitation);
  - cross-job/duplicate-row: `_prior_outreach` matches the **person**, on a
    canonical LinkedIn vanity or lowercased email, so `/in/carol`,
    `/in/carol/` and `/in/Carol?miniProfileUrn=…` are one human. Blank
    fields identify nobody (an empty `linkedin_url` used to match every
    other empty one, blocking strangers while missing real repeats).
  - `--force` overrides after human verification.
- **`reach.py undo <jid>`** deletes the attempt rows that *are* the evidence a
  person was contacted — so it needs `--confirm` when the job has confirmed or
  in-flight outreach, and names who loses protection. `extract.py reset <jid>`
  now clears those attempts too (they used to be orphaned, leaving the
  re-extracted job with an empty history and the person re-contactable).
- **Uncertain sends**: if a DM send is clicked but unconfirmed, status is `uncertain` (attempt logged as `pending`) — check the LinkedIn inbox manually, then `reach.py update --set-sent message` to confirm (this also settles the pending attempt) or retry with `--force`. Never silently resend.
- **Premium**: 2nd/3rd-degree contacts use the InMail composer (`.msg-inmail-credits-display`); the pipeline proceeds and reports `INMAIL_COMPOSER`. `CONNECT_REQUIRED` → run `reach.py connect`. Free accounts always see InMail only for non-connections.
- **Contact indices**: `--contact N` matches the numbering printed by `discover`/`list` (DB order).
- **Email suggestions** from the LLM are never sent automatically — backfill with `reach.py update --email <addr>` after human verification.
- **Attempts**: every outreach is recorded in `contact_attempts` (status: pending/sent/failed).
- **Verified LinkedIn selectors** (2026-07 live DOM): people cards `li.org-people-profile-card__profile-card-spacing`; DM = typeahead flow (`input.msg-connections-typeahead__search-field` → mouse-click suggestion → `div.msg-form__contenteditable` → `button.msg-form__send-btn`); compose URL recipient params do NOT work. See `lib/linkedin_messaging.py` header.

## Submission policy (read before any live run)

`~/.ji/apply_policy.json` decides whether `act --submit` clicks for real.

| mode | effect |
|------|--------|
| `hold` | **DEFAULT.** Fills completely, stops before submit. |
| `shadow` | Fills + audits, never submits (what `apply.py shadow` forces). |
| `live` | Submits for real — must be chosen **explicitly**. |

Fail-closed: a missing, unreadable, malformed, or typo'd policy resolves to
`hold` and says so on stderr. Previously the default was `live` and a missing
file was swallowed silently, so the most likely failure of the safety control
opened the gate. `JI_APPLY_MODE` overrides the file; `apply.py shadow` forces
`shadow` for its children.

## Trust boundary for URLs

Job URLs are harvested by regex from **email bodies**, so they are
attacker-influenceable. `lib/url_safety.py` is the gate: http/https only, no
embedded credentials, and the host must not resolve to loopback / private /
link-local / reserved space (this is what keeps an emailed link away from the
pipeline's own CDP port on 127.0.0.1:9222 and from cloud metadata endpoints).
Unresolvable hosts are refused — we don't fetch what we can't vet. Checked
cheaply at extract time and in full before every fetch; `curl` additionally
pins `--proto`/`--proto-redir` and `--max-redirs`. `_SKIP_DOMAINS` in
extract.py is a **noise** filter, not a security control.

## Orchestrator rules

1. **Don't guess personal data.** Check profile + resume first. Missing → ask (critical) or skip (optional).
2. **Don't fill optional fields.** Not marked required → leave it.
3. **Don't echo PII.** Labels only, never values in output. The pipeline now
   holds itself to this too: the fill report prints *answer keys*, never
   answer values (it used to dump gender / disability / salary / sponsorship
   answers into shadow transcripts and per-job logs on disk).
4. **Always verify after `NEXT: verify`.** DB can be stale.
5. **Inspect when stuck.**
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

Operational internals in `technical-notes.md`. Two matter at run time:

- **Chrome lifecycle**: Pipeline starts its own Chrome instance on a free port (never reuses user's browser). Port persisted to `chrome-config.json` across processes. Profile lives at `~/.ji/chrome-profile/` — sessions (cookies, localStorage) persist between pipeline runs.
- **PDF guard**: `detect` refuses to proceed if stage is `tailored` but no Resume PDF exists. Run `tailor.py undo <jid> && tailor.py --jid <jid>` to regenerate.
