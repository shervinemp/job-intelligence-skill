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

## Test coverage (144 pass + 64 subtests, zero pyflakes)

| File | Covers |
|------|--------|
| `tests/test_corpus.py` | Real `_SCAN_JS` + `_READER_JS` vs synthetic fixtures — 5 canonical shapes (login-wall, Workday-like, Ashby-like, Greenhouse-like, expired) + 5 curveballs (cookie overlay, honeypot-in-dialog, conditional reveal, obfuscated React label, login+honeypot compound) + 3 alert-popup curveballs (confirm modal mid-fill, leave-page modal, success modal post-submit). Skips if jsdom unavailable. |
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
    buttons inside visible modals. Currently called only at submit
    time (submit.py:419).
  - Workday cookie banner: `fill.py:_handle_login_wall` clicks
    `legalNoticeAcceptButton` before login flow.
  - `confirm_modal_signals` is a stable capability hash key — a
    platform that reliably shows mid-fill confirms hashes distinctly
    from the bare form. The drift detector can flag when a platform
    stops showing the confirm popup (or adds one).

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
- 13 active jobs with external ATS URLs (Harvey/Ashby, Scribd, TRM Labs, Narvar/Greenhouse, Lyft/CareerPuck, Behaviour ×2, LTM/Ripplehire)
- CrowdStrike Workday needs shared password pool population (`creds shared-set`)
- Mid-fill confirm modal dismissal — `confirm_modal_signals` is now
  detected by capability scanner, but `_dismiss_confirm_modal` is only
  called at submit time. Should be called between probe iterations
  when `confirm_modal_signals > 0` is observed mid-fill.

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