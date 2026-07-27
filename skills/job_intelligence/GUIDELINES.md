# GUIDELINES.md — High-density state for the Job Intelligence pipeline

Single source of truth for what's been built, what's running, and the
contracts each layer holds. Compression of decisions, conventions,
and live state. Update in-place; never lose prior learnings.

Last updated: 2026-07-27 (post adaptive-probe immune system)

## Probe cascade — the contract

`apply/common/inspector.py:probe()` is the **single entry point**. The
cascade is the safety net — it never disables. Route order:

1. **YAML `best_strategy`** (authoritative — hand-tuned DOM knowledge)
2. **Confirmed observation** for capability profile hash (N≥3 wins)
3. **Most-frequent candidate** observation (soft hint, <3 wins)
4. **Capability-scan suggestion** (`capabilities.py:suggest_strategy`)
5. **Full cascade** in declaration order (always reachable)

On success: `_record_observation()` writes to observation store AND
captures a corpus snapshot on first encounter of the profile hash.
On full miss: `_capture_failure()` saves DOM + profile to
`~/.ji/registry-failures/`, records a drift event (demotes confirmed
observations after 2 failures), emits `REGISTRY_UNKNOWN:` signal.

`_try_strategy()` is the unified runner — same code path for the
prioritised first attempt and the cascade loop. Widget resolution:
YAML `probe.widgets` wins; ARIA auto-discovery fills the gaps via
`_build_registry_widgets()`. No YAML needed for ARIA-compliant platforms.

## Adaptive probe layers

| Layer | File | Purpose | State |
|-------|------|---------|-------|
| Capability scan | `apply/common/capabilities.py` | One `page.evaluate` returning `CapabilityProfile` (dialog, iframes, comboboxes, listboxes, file_inputs, password_fields, apply_buttons, login_signals, eeoc_signals, etc.). Stable hash via bucketed counts (0/1-2/3-10/11+). | Stateless |
| Observation store | `apply/common/observations.py` | Per-profile-hash record of winning strategy + widgets. N=3 consecutive → confirmed. 2 cascade failures → demote (clears winning_strategy + candidates). Keyed by capability hash, **not** domain — multi-tenant platforms share learned starts. | `~/.ji/registry-obs/<hash>.json` |
| Widget auto-discovery | `capabilities.py:discover_widgets` | ARIA-based dropdown/combobox selector inference. Feeds `read_fields(custom_widgets=...)` when YAML omits widgets. Reduces YAML to login patterns + notes. | Stateless |
| Corpus capture | `apply/common/corpus.py` | First-success DOM snapshot per profile hash — never overwritten. Source for offline regression tests. | `~/.ji/registry-corpus/<hash>.{html,json}` |
| Failure capture | `inspector.py:_capture_failure` | Cascade-miss → save DOM + capability profile. Prunes to 25 most recent. | `~/.ji/registry-failures/<ts>_<hash>_{dom.html,probe.json}` |
| CorpusPage test harness | `apply/common/mock_page.py` | jsdom-backed page that runs real `_SCAN_JS` / `_READER_JS` against saved HTML via node subprocess. No browser needed. Skip-when-jsdom-missing. | Bridge in `$TEMP/corpus_*_bridge.js` |

## Test coverage (151 pass + 64 subtests, zero pyflakes)

| File | Covers |
|------|--------|
| `tests/test_corpus.py` | Real `_SCAN_JS` + `_READER_JS` vs synthetic fixtures — 5 canonical + 5 curveball + 6 alert-popup + 7 edge-case (2FA interstitial, session-expired post-submit, dropzone, jQuery calendar, 2FA login check returns '2fa', 2FA distinct hash, dropzone distinct hash). Skips if jsdom unavailable. |
| `tests/test_probe_router.py` | Capability hash stability/variance, observation confirm/demote flow, widget merge priority, failure capture artifact + pruning, `_build_registry_widgets` YAML-wins-then-auto chain. |
| `tests/test_*.py` (existing) | 96 baseline. |
| `tests/test_probe_router.py` | Capability hash stability/variance, observation confirm/demote flow, widget merge priority, failure capture artifact + pruning, `_build_registry_widgets` YAML-wins-then-auto chain. |
| `tests/test_*.py` (existing) | 96 baseline + 33 new = 129 tests. |

jsdom install: `C:\Users\sherv\.openclaw\workspace\tmp\opencode\node_modules`
(used by CorpusPage via `JSDOM_NODE_MODULES` env or auto-discovery).

## CLI surface — `apply.py registry`

```
apply.py registry candidates          # list unconfirmed observations
apply.py registry confirm <hash>     # manual promote (accepts 8-char prefix)
apply.py registry clear <hash>       # delete observation
apply.py registry corpus             # list captured DOM snapshots
apply.py registry failures           # list cascade-miss artifacts
apply.py registry drift [--dry-run]  # self-test all corpus snapshots
```

## Alert / popup handling

Two flavors, handled at different layers:

**Native browser dialogs** (`window.alert`, `confirm`, `prompt`) — handled
at the Playwright level by `helpers.py:_wire_dialogs`:
  - `alert()` → `d.accept()`
  - `confirm()` → `d.accept()` (says "Yes/OK" — we want to proceed)
  - `prompt()` → `d.dismiss()` (we don't fill arbitrary prompts)

Installed once per page load via `_wire_dialogs(page)` in
`chrome_session()` — before any clicks fire. The capability scanner
cannot see native dialogs (they're not in the DOM) and we don't try;
documented limitation.

**HTML modal popups** (`[role="dialog"]` without form inputs) — detected
by the capability scanner via three new signals:
  - `confirm_modal_signals: int` — visible modal with OK/confirm/submit
    button text (NOT form dialogs with inputs)
  - `success_modal_text: bool` — visible modal text matches "submitted
    successfully" / "thank you for applying"
  - `error_modal_text: bool` — visible modal text says "please fix..."

Detection at scan-time:
  - Form dialog (has inputs) → confirm_modal_signals=0 (key guard)
  - Confirm popup ("Please confirm your email" with OK button) → 1
  - Leave-page modal ("Stay" / "Leave Anyway") → 1 (Stay/Leave trigger kw)
  - Success modal → confirm_modal_signals=1 (Continue button)
    + success_modal_text=True

Dismissal at fill time:
  - `helpers.py:_dismiss_confirm_modal(page)` clicks OK/confirm/submit
    buttons inside visible modals. Called at two sites:
    - submit time (`submit.py:419`)
    - mid-fill (`fill.py:_dismiss_popups_if_present`) — runs at the
      top of each page iteration AND between conditional-reveal sweeps
      when `confirm_modal_signals > 0` is detected. Catches popups
      that appear after typing email ("Please confirm") or before
      navigating ("Are you sure you want to leave?").
  - Workday cookie banner: `fill.py:_handle_login_wall` clicks
    `legalNoticeAcceptButton` before login flow.
  - `confirm_modal_signals` is a stable capability hash key — a
    platform that reliably shows mid-fill confirms hashes distinctly
    from the bare form. The drift detector can flag when a platform
    stops showing the confirm popup (or adds one).

## Two-factor auth (2FA) interstitial

Detected at two layers:

**Capability scanner** — `two_factor_signals: int`: counts visible
numeric inputs with `maxlength` 4-8, OR `autocomplete='one-time-code'`.
Included in the stable profile hash so a platform that requires 2FA
hashes distinctly from one that doesn't.

**`fill.py:_login_check`** returns `"2fa"` (the third result besides
`yes`/`no`/`uncertain`) when:
  - Visible numeric input with maxlength 4-8, OR
  - `autocomplete='one-time-code'`, OR
  - Body text matches `\b\d{1,2}-?digit (code|verification|otp)\b`
    or `two-factor|2fa|verification code|authentication code|enter the code`

The multi-password trial loop in `_handle_login_wall` checks for
`"2fa"` BEFORE the `uncertain` branch — on 2FA detection it:
  - Saves the verified password as primary (creds were accepted)
  - Emits `STATUS: 2fa_required` + `NEXT: login` (with instructions
    to complete 2FA in Chrome then rerun)
  - Returns False (don't proceed to fill the form)

Critical: trying more passwords would just re-trigger 2FA on the
same account. The first 2FA detection stops the trial.

## Session-expired detection (`submit.py:_determine_outcome`)

After submit click, the outcome cascade checks for session-expired
text BEFORE falling to "uncertain". Detection patterns:
  - Body text: `session (has )?expired|session timed out`
  - Password field reappeared + body has `sign in|log in|please login`
    WITHOUT any validation-error text (rules out wrong password)

On detection: `outcome = "session_expired"` → `cmd_submit` clears
`submit_clicked` (retry doesn't need `--force`), emits
`STATUS: session_expired` + `NEXT: login` so the orchestrator
re-authenticates and retries the fill, rather than marking as
"applied" (which would skip the working submission forever).

## Dropzone file upload (`dropzone_signals`)

`dropzone.js`-style drag-and-drop upload zones are detected via
`.dropzone, [class*="dropzone"], [data-dropzone], [class*="drop-zone"],
[class*="filedrop"], div[ondragover], div[ondrop]` — filtered to
exclude visible text inputs (false-positive guard).

In the stable profile hash. Fill path: NOT yet implemented
(`_try_filechooser_upload` needs a synthetic `DataTransfer` event
instead of clicking an upload button). The signal surfaces the
pattern so the orchestrator knows manual intervention is needed.

## jQuery UI / calendar (`calendar_signals`)

Datepicker popups detected via `.ui-datepicker-calendar,
.ui-datepicker, [class*="DayPicker"][class*="Month"], .pika-single,
.pika-table, [class*="calendar"][class*="day"],
[data-automation-id="calendar"], [role="grid"][class*="calendar"]`.

In the stable profile hash. The existing `DatepickerFiller` handles
text input after the popup opens. Defensive signal — alternative
input paths work without it.

## Other live state (carry-over from prior sessions)

- **Branch**: `feat/hybrid-skyvern-playwright`
- **Latest commit before this session**: `8570ec8` (chore: gitignore commit_msg.txt)
- **Profile**: `profile.json` — 35+ pre-resolved Q&A pairs (AI/LLM, initials SN, Ontario)
- **DB**: `C:\Users\sherv\.ji\state\jobs.db` (jobs table uses `id` not `jid`)
- **Skill dir**: `C:\Users\sherv\.openclaw\workspace\skills\job_intelligence\`
- **Clone dir**: `C:\Users\sherv\.openclaw\workspace\tmp\job-intelligence-skill\`

### Credential vault bugs fixed in last commit
- Multi-tenant domain collision (`_MULTI_TENANT_SLD` set in `lib/credentials.py`)
- All-passwords-failed returns False + emits `login_failed`
- Three-way `_login_check` (yes/no/uncertain) — extended wait prevents lockout
- `_check_account_created` verifies before saving creds
- Workday `createAccountCheckbox` checked before submit
- `enumerate()` instead of `list.index()` for password logging

### Open work
- Gmail staging blocked (OAuth expired — `gmail-cli auth add`)
- Skyvern server stale (ECONNREFUSED on 9222 — needs restart)
- ~100 active LinkedIn Easy Apply jobs waiting on `apply.py auto`
- 13 active jobs with external ATS URLs (Harvey/Ashby, Scribd, TRM
  Labs, Narvar/Greenhouse, Lyft/CareerPuck, Behaviour ×2,
  LTM/Ripplehire)
- CrowdStrike Workday needs shared password pool population
  (`creds shared-set`)
- Dropzone fill path NOT yet implemented — `dropzone_signals` is
  detected by the scanner, but the fill loop still uses
  `_try_filechooser_upload` which expects an upload button. A future
  synthetic DataTransfer event via `page.evaluate` will handle this.

## Conventions

- **No per-platform Python code.** Platform knowledge lives in
  `apply/registry/<name>.yaml`. The engine has no per-platform branches.
- **Excludes from hash**: apply_buttons text, submit_buttons text,
  page_text_length — minor copy changes don't rotate the profile key.
- **Bucketed counts**: 0 / 1-2 / 3-10 / 11+. A 5-question form and
  7-question form on the same SPA hash identically.
- **First-success corpus capture** (first-wins, never overwritten).
  Re-capture via `recapture()` (not yet CLI-exposed).
- **Cascade is the contract.** Observations only reorder the starting
  point. Every strategy in `_PROBE_STRATEGIES` remains reachable.

## Recovery

- Stale observation: `apply.py registry clear <hash>` → re-accumulate.
- Wrong auto-promotion: `apply.py registry clear <hash>` (manual reset).
- Drift detected automatically after 2 cascade failures.
- Wrong YAML `best_strategy`: cascade finds fields anyway and logs
  `CONFIG_STALE:` to stderr so the YAML can be updated manually.

## What NOT to do

- Don't auto-promote YAML from observations — humans review the YAML.
  The corpus + observation store surface the candidate; promotion is
  manual via writing the YAML file.
- Don't disable the cascade. The cascade is the contract.
- Don't key observations by domain. Capability-keyed learning is what
  makes new Workday tenants work on first run.
- Don't trust one-shot strategy changes. `record_success` resets
  success_count to 1 when the winning strategy changes — needs N=3
  consecutive to confirm, eliminating fluke rerouting.
- Don't commit the bridge script — it's in $TEMP, regenerated per run.
- Don't lose prior accuracy: corpus fixtures only grow, observations
  only accumulate, cascade never disables.