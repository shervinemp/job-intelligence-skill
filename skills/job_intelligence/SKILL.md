# Job Intelligence Pipeline

Automates job applications end-to-end: stage emails → extract jobs → enrich descriptions → tailor CVs → apply via browser automation. The operating contract lives in `ETHOS.md` (read first): deterministic core is the source of truth, ask_api is a gated escape hatch (vision + code-exhausted ambiguity only), and the orchestrator (LLM-in-the-middle) is the operator, verifier and debugger consuming the evidence trail. `report.py glossary` prints the canonical vocabulary (generated from `apply/common/terms.py`).

Low-level LLM probe when you need one: `lib/ask_api.py [--img <path>] --prompt <text>`.

## Read First

- `decisions.md` — accept/reject rules per job
- `profile.json` — common form answers
- `categories.json` — job categories

## `ji` — the ONE surface

`ji.py` is the orchestrator's single command surface (SURFACE_AUDIT.md). Every action, evidence, and decision command forwards to the owning engine; the orchestrator does not need to memorize apply.py / report.py directly.

```
ji status            fleet + READY + HOLD + decisions, one NEXT
ji decisions         THE inbox (owner-split)       ji answer <jid> "<label>": "<value>"
ji verify <jid>      RISK-VALUE REVIEW (values!)    ji ready              risk-observed jobs
ji job|diff|audit <jid>   dossier / canary / log    ji apply|submit|shadow|fetch|tailor
ji stats|fleet|candidates|pending|profile|rules|keywords|domains|spc|adjudicate|...  (forward)
```

Two `verify` verbs, don't confuse them:
- `ji verify <jid>` — **risk-value review**: resolved values (sanctioned PII view) for orchestrator confirmation before submit.
- `apply.py verify <jid>` — **post-submit check**: scans for success signals after a submit.

## Pipeline stages

All stages are reached THROUGH `ji`. The raw engines (`extract.py`, `enrich.py`, `tailor.py`, `reach.py`, `linkedin.py`, `stage_emails.py`) stay callable beneath but the orchestrator uses `ji <stage> <verb>`:

| Stage | Verbs |
|-------|-------|
| `ji stage_emails [--days N]` | — |
| `ji extract <verb>` | admit `--category <name> <jid>` / reject / reset / submit / auto |
| `ji linkedin [--url] [--count N]` / `--list` | admit/reject via extract |
| `ji enrich <verb>` | admit/reject/flag/retry/retry-skipped/undo/open (add `--team <name>` for team discovery) |
| `ji tailor <verb>` | craft/review/admit/reject/undo/retry/relentless/reset (`--auto` for batch) |
| `ji reach discover|list <jid>` | Contact discovery + outreach |
| `ji reach email|message|connect <jid>` | Send email / LinkedIn DM / connect request |
| `ji apply detect|act|verify <jid>` | Follow apply pipeline |

## Change protocol (after ANY hot-path change: fill/check/submit/resolve/shadow/terms)

Work is done when **every** step's criterion is met:

1. `python scripts/lint.py` — compile, vocabulary literals, dead strings, nested dirs, and CLI docs (a documented command must dispatch; a dispatchable command must be documented). Criterion: **LINT PASS**.
2. `python -m pytest tests -q` — full suite. Criterion: **PASS**.
3. One live shadow job as a regression canary vs its previous run — e.g. `apply.py shadow --jid <lyft>` after removing it from the log. Criterion: **canary regresses clean**.
4. `report.py shadow --classify` — verify the outcome landed as expected. Criterion: **outcome matches expectation**.
5. MANUAL sync — never scripted blindly. Criterion: **symmetric two-way diff 0/0** (every workspace file equals its repo counterpart, no orphans either side; a difference after the copy is a mistake, not a sync). Never Copy-Item a directory by hand (the apply/apply nesting lesson) — a sync tool that cannot detect direction is poison, hash equality is not proof of intent. If the repo side changed since the last sync, STOP and reconcile direction first.
6. Repo suite: `python -m pytest skills/job_intelligence/tests -q` (from the repo root). Criterion: **PASS**.
7. Commit + push from the repo. Criterion: **pushed**.

## Commands (report / gmail / registry surface)

Stage verbs live under Pipeline stages; apply verbs under Apply pipeline; reach verbs under Reach. This table is only the commands with no other home.

| Command | Action |
|---------|--------|
| `report.py stats` | Pipeline state + next step |
| `report.py handovers [USER\|ORCHESTRATOR\|DATA\|REVIEW]` | THE decisions inbox: every open decision, grouped by owner, with evidence + answer commands |
| `report.py help` | The grouped surface map (decisions / evidence / fleet / readiness) |
| `report.py widgets` | Unhandled widget-class backlog (probe-failure artifacts) |
| `report.py candidates [--limit N]` | Tailored jobs ready to apply |
| `gmail-cli send <to> <subject> --body <text>` | Send email (low-level) |
| `extract.py auto` | Auto-extract URLs from staged emails |
| `apply.py registry <action>` | Probe observations: candidates / confirm / clear / corpus / failures / drift |

## Tailoring

Full guide in `tailoring.md`. The essentials: the LLM writes a `resume.json` data file (no code); `lib/build_resume.py` reads the JSON and produces PDFs; both `JI_TAILOR` routes converge on `tailor.py admit <jid>` (grounding gate + stage advance to "tailored"). Validate the schema without building: `python -m lib.build_resume <resume.json> <out_dir> --validate`.

## Apply pipeline

Phase-by-phase walkthrough in `apply-pipeline.md` (rules cited there live in Orchestrator rules below — the single source). The pipeline is one-shot on submit: fill → next (repeat) → submit (preview + rules 12-13) → verify (rule 4).

```
detect [<jid>] → [navigate] → act --fill → act --next (repeat) → act --submit <jid> → verify <jid>
```

| Step | What it does |
|------|-------------|
| `detect [<jid>]` | Pre-flight: DB stage, PDF, classify type. Omit JID to auto-pick first tailored. Outputs `TYPE:` + `NEXT:`. |
| `navigate <jid>` | LinkedIn External only — click button, decode safety redirect, land on ATS. Auto-clicks "Apply now" on job listing pages. Prompts for login on auth wall — cookies persist via Chrome profile. |

**Auth walls (login / account-creation / 2FA / CAPTCHA)** are handled in `apply/act/auth_flow.py`. Login and account creation use the credential vault (approved domains only — `report.py domains approve <domain>` before a password is ever typed). 2FA after login and account-verification emails are completed via the INBOX: `lib/inbox.py` searches `from:<domain>` for the security code or verification link, extracts it fail-closed (code = standalone 4-8 digit number adjacent to a strong keyword; link = verify/confirm/activate label + host attributing to the auth domain or a known ATS verify host), enters/clicks it, and re-checks. When nothing attributable is found the flow hands off for manual completion (`2fa_required` / `captcha_required`).
| `act --fill <jid> [--answers '{}']` | Fill all fields. `--answers` exact → common_answers → profile. Auto-unchecks "Follow company". |
| `act --next <jid>` | Click forward (Submit > Review > Next > Continue > Done). Detects submission (→ verify) / errors (→ retry fill). |
| `act --submit <jid>` | Submit. Runs pre-submit check (incl. cross-field coherence) + regression canary. Sets `submit_clicked` flag before clicking. Investigates (no re-click) on retry. `--force` clears guard + gates. |
| `act --check <jid>` | Pre-submit validation: cross-field contradictions |
| `act --inspect <jid>` | Full diagnostic: screenshot + HTML dump + probes + fields + buttons + dialog/iframe detection. Use when stuck. |
| `act --investigate <jid>` | Deep-analyze unknown platform |
| `verify <jid>` | Scan open pages for success signals + optional vision check. Updates DB stage to "applied" if confirmed. |
| `apply.py reject/flag/retry/undo <jid>` | Skip / toggle auth wall / re-attempt failed / move back one stage |
| `apply.py preflight` | Profile readiness gate: hard/soft/answer-gaps/coverage. Run BEFORE any batch — an incomplete profile (e.g. missing `work_history`) fails the fleet identically. |
| `apply.py shadow [--jid ...] [--recheck] [--quick]` | Observability batch (subprocess per job, never submits). `--recheck` re-examines the unconfirmed-skip queue (cookie/session-variance candidates). |
| `report.py shadow --classify` | Orchestrator verification view: outcomes, crash evidence, owner-split (code/data/handover), unconfirmed clustering. |
| `report.py fleet` | Fleet accuracy report: kinds, method attribution (deterministic vs combobox vs llm), weekly trend, steering memo of top failing labels. |
| `report.py glossary` | The vocabulary — generated from apply/common/terms.py (cannot drift). |
| `tailor.py ground <jid>` | Factual-grounding manifest: every tailored claim must trace to profile.json. `admit` is blocked until clean (`--force` after review). |

### Apply tips

- Auth walls: navigate prompts for login. Log in via the open browser, press Enter to continue. Type `flag` to skip. Cookies persist via Chrome profile — same platform won't re-prompt.
- `--answers` — normalized exact match (case/punctuation insensitive). Full label text.
- Fill report shows resolved answers and field detection for each field.
- Guest apply: auto-clicks "continue without signing in" when available.
- 3x guard: same page 3 fills in a row → warns.
- EEO/demographic fields: auto-detected by decline-option presence (language-agnostic). Saved answers persist under `common_answers.eeo` for reuse.
- Platform registry (`apply/registry/*.yaml`): per-ATS widget config, auto-resolved from the page URL — no caller changes needed.

## Reach (outreach)

Optional parallel track after `enrich`/`tailor`. Contact discovery finds recruiters (job page), team members (company LinkedIn people page, filtered by team keywords), and 1st-degree connections (LinkedIn search with numeric company ID). Full guide in `REACH_PHASE.md`.

- **Flow**: `enrich.py admit --team <name>` → `reach.py discover <jid>` (or `discover --all` after a batch) → `reach.py list <jid>` → outreach.
- **One-shot guards**, per channel and per person:
  - same row: `email_sent` (email), `message_sent` (message), a prior `linkedin_connect` attempt (connect had *no* same-row guard, so a re-run after a crash sent a second invitation);
  - cross-job/duplicate-row: `_prior_outreach` matches the **person**, on a canonical LinkedIn vanity or lowercased email, so `/in/carol`, `/in/carol/` and `/in/Carol?miniProfileUrn=…` are one human. Blank fields identify nobody (an empty `linkedin_url` used to match every other empty one, blocking strangers while missing real repeats).
  - `--force` overrides after human verification.
- **`reach.py undo <jid>`** deletes the attempt rows that *are* the evidence a person was contacted — so it needs `--confirm` when the job has confirmed or in-flight outreach, and names who loses protection. `extract.py reset <jid>` now clears those attempts too (they used to be orphaned, leaving the re-extracted job with an empty history and the person re-contactable).
- **Uncertain sends**: if a DM send is clicked but unconfirmed, status is `uncertain` (attempt logged as `pending`) — check the LinkedIn inbox manually, then `reach.py update --set-sent message` to confirm (this also settles the pending attempt) or retry with `--force`. Never silently resend.
- **Premium**: 2nd/3rd-degree contacts use the InMail composer (`.msg-inmail-credits-display`); the pipeline proceeds and reports `INMAIL_COMPOSER`. `CONNECT_REQUIRED` → run `reach.py connect`. Free accounts always see InMail only for non-connections.
- **Contact indices**: `--contact N` matches the numbering printed by `discover`/`list` (DB order).
- **Email suggestions** from the LLM are never sent automatically — backfill with `reach.py update --email <addr>` after human verification.
- **Attempts**: every outreach is recorded in `contact_attempts` (status: pending/sent/failed/backfilled).
- **Thread reconciliation (`reach.py threads <jid> [--backfill]`)**: the ledger records only what the pipeline sent — a person messaged manually (or before the ledger existed) leaves no DB trace. `threads` reads the REAL LinkedIn inbox per contact (`thread_status`), surfaces existing threads (last message, direction), and `--backfill` records a `backfilled` attempt row so the one-shot + cross-job guards see the truth. The inbox is authoritative, not the DB.
- **Resume attach**: `message`/`email` auto-attach the per-job tailored resume (`_job_resume_pdf`); DMs support `.pdf` via the composer's document file input (attach via `set_input_files` on the hidden input — never click "Attach a file...", that opens the OS file picker). `--no-attach` to skip. The resume belongs on the FIRST message, so follow-up-only-for-the-attachment sends should never happen.
- **LLM tone review (`_preflight_send`)**: before every real send the LLM judges the message against the voice spec + real thread evidence (cold open vs follow-up vs continuation, no invented relationship, one ask, no empty attach filler). A FAIL verdict blocks unless `--force`. No hardcoded phrase lists. Message WRITING is orchestrator-gated; tone REVIEW runs in auto mode.

## Submission policy (read before any live run)

`~/.ji/apply_policy.json` decides whether `act --submit` clicks for real.

| mode | effect |
|------|--------|
| `hold` | **DEFAULT.** Fills completely, stops before submit. |
| `shadow` | Fills + audits, never submits (what `apply.py shadow` forces). |
| `live` | Submits for real — must be chosen **explicitly**. |

Fail-closed: a missing, unreadable, malformed, or typo'd policy resolves to `hold` and says so on stderr. Previously the default was `live` and a missing file was swallowed silently, so the most likely failure of the safety control opened the gate. `JI_APPLY_MODE` overrides the file; `apply.py shadow` forces `shadow` for its children.

## Trust boundary for URLs

Job URLs are harvested by regex from **email bodies**, so they are attacker-influenceable. `lib/url_safety.py` is the gate: http/https only, no embedded credentials, and the host must not resolve to loopback / private / link-local / reserved space (this is what keeps an emailed link away from the pipeline's own CDP port on 127.0.0.1:9222 and from cloud metadata endpoints). Unresolvable hosts are refused — we don't fetch what we can't vet. Checked cheaply at extract time and in full before every fetch; `curl` additionally pins `--proto`/`--proto-redir` and `--max-redirs`. `_SKIP_DOMAINS` in extract.py is a **noise** filter, not a security control.

## Orchestrator rules

1. **Don't guess personal data.** Check profile + resume first. Missing → ask (critical) or skip (optional).
2. **Don't fill optional fields.** Not marked required → leave it.
3. **Don't echo PII.** Labels only, never values in output. The pipeline holds itself to this too: the fill report prints *answer keys*, never answer values (it used to dump gender / disability / salary / sponsorship answers into shadow transcripts and per-job logs on disk).
4. **Always verify after `NEXT: verify`.** DB can be stale.
5. **LOOK FIRST, always.** Any uncertain or failed step starts with the evidence, not the status string: read `IMG:` (vision) and `HTML:` (DOM dump), run `act --inspect <jid>`, then decide. The dossier tells you *what* failed — it does not tell you *why*. A status like `no_filler`/`rejected_by_form`/`submitted_uncertain` is a symptom; the screenshot + DOM are the cause. Don't pattern-match on reason strings and don't re-fill blind — look at the actual page (is the modal open? is it a login wall? a hydration race? a different form?). This is primary diagnosis, not a last resort.
6. **Don't collapse gates.** Dry-run → fill → preview → confirm. Each its own round-trip. See `apply-pipeline.md`.
7. **Preview before submit.** Always run `act --submit` first. Read every value. Check against profile answers. Fix contradictions before proceeding.
8. **One-shot fill for SPA forms.** Validation fail → page reloads → all values lost. Fill everything then submit once.
9. **Autocomplete fields need clicks, not text.** Flag for user help.
10. **Don't contradict profile answers.** Before filling any field, check the `Profile answers:` block. If what you're about to fill contradicts a profile answer, stop and fix it.
11. **No custom submit scripts.** Use the pipeline. No `page.evaluate` clicks, no standalone scripts.
12. **Submit is one-shot.** The pipeline sets `submit_clicked` before clicking. If success detection is uncertain, the next run *investigates* the page (success signals, URL change, form disappearance, validation errors, vision API) — it NEVER clicks submit again. Only `--force` (manual, after human confirms failure) or `undo` clears the guard. Never certify applied on "we don't know" (that was the wrong-applied false-positive class).
13. **Uncertain submit → stays `tailored`.** When the outcome cascade is exhausted (success text → already-applied text → `_check_submit_success` → URL change → form disappearance → validation errors → vision API), the job is **NOT** marked applied — it stays `tailored` with `submit_clicked` set (nothing re-submits) and routes to `verify`, which certifies applied only on a real success signal. Read the page first (rule 5): login wall, hydration race, validation error, and real submit demand different next actions.
14. **Validation errors = safe to retry.** If the form rejected with validation errors, `submit_clicked` is cleared — the next run can fill the missing fields and re-submit.
15. **Never submit blind.** `--fill --submit` together is BLOCKED. Always review the fill report between fill and submit. The orchestrator reads UNFILLED fields, supplies `--answers`, then submits.

## Platform quirks

Platform-specific notes live in `apply/registry/*.yaml` under the `notes:` field — the single source of truth next to the detection rules and patterns they describe. Read the registry file for the ATS you're working on.

## Account & login notes

- Repeat portals (e.g., 2nd Workday): guest apply works but creates new account per company. No credential reuse.
- Auto-login (approved domains only), 2FA detection, and account creation ARE automated (`apply/act/auth_flow.py`): saved creds are tried per-password with promotion, 2FA surfaces as `2fa_required` for manual completion, and account creation saves creds only after a verified success. A CAPTCHA on the auth form or account form records `captcha_required` and NEVER saves/promotes creds. Credentials are only typed into domains approved via the domain gate (`report.py domains approve|deny`). Plaintext credential fallback is opt-in (`JI_ALLOW_PLAINTEXT=1`). Login walls on unapproved/unknown domains still need manual intervention.

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
| `IMG:` | inspect | Screenshot path. **READ THIS FIRST** — vision is your primary evidence |
| `HTML:` | inspect | Full DOM dump path. Read when the screenshot alone doesn't answer — this is primary diagnosis, not a fallback |

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

Operational internals in `technical-notes.md`; probe/recovery internals in `GUIDELINES.md`. Two matter at run time:

- **Chrome lifecycle**: Pipeline starts its own Chrome instance on a free port (never reuses user's browser). Port persisted to `chrome-config.json` across processes. Profile lives at `~/.ji/chrome-profile/` — sessions (cookies, localStorage) persist between pipeline runs.
- **PDF guard**: `detect` refuses to proceed if stage is `tailored` but no Resume PDF exists. Run `tailor.py undo <jid> && tailor.py --jid <jid>` to regenerate.
