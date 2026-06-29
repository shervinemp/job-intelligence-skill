# Pipeline trace: stages, combinations, flexibility, and preferences

A map of how a job flows end-to-end, every branch the apply pipeline can take, the
layers that absorb per-site variation, and how user preferences are resolved into
field values. File references are clickable from the repo root.

## 1. The whole pipeline (stage machine)

```
stage_emails.py → extract.py → [linkedin.py] → enrich.py → tailor.py → apply.py {detect→navigate→act→verify}
```

Each job row in the SQLite DB (`lib/db.py`) has a **stage** and a **state**:

- **stage**: `extracted → described → tailored → applied` (monotonic forward; `undo`
  walks back one).
- **state**: `active | rejected | failed`. Failure keeps the current stage and sets
  `state='failed'` (so a failed *apply* sits at `stage='tailored'`, not `applied`).

| Stage script | Produces | Human gate |
|---|---|---|
| `stage_emails.py` | raw job leads | auto |
| `extract.py` | structured job + URL | `admit --category` / `reject` (uses `decisions.md` non-fit rules) |
| `enrich.py` | full description (or auth-wall flag) | `admit` / `reject` / `flag` |
| `tailor.py` | `results/<jid>/resume.json` + PDFs | `build` → `admit` |
| `apply.py` | submitted application | the apply sub-pipeline below |

## 2. The apply sub-pipeline

```
detect [<jid>] → [navigate] → act --fill → act --next (loop) → act --submit --confirm → verify
```

Single shared state file `~/.ji/state/apply_state.json` carries `jid`,
`external_url`, `platform`, `_detect_fields`, `_page`, `filled`, `page_fingerprint`,
`_last_submit`, `_fields_with_errors`, `result`. Every command reloads it and
verifies `state.jid == jid` before acting.

### detect ([detect.py](../apply/detect.py)) — classify the entry point
Pre-flight stage guards first (already-applied → stop; tailored-without-PDF →
re-tailor; extracted/described → enrich/tailor). Then classify and emit `TYPE:`:

- **LinkedIn** (`linkedin.com/jobs/view`): intercepts the `jobPostingApplyFlowByJobId`
  GraphQL response for Easy Apply questions, reads the action buttons, and routes to
  `already_applied` / `external` (decodes the `linkedin.com/safety/go/` redirect) /
  `easy_apply` (opens the modal, polls for fields) / `unknown`.
- **Direct URL**: navigates, checks already-applied text, `read_page`. Routes to
  `ats_direct` (fields > 0) / `login_wall` or `guest_available` / `unknown`.

### navigate ([navigate.py](../apply/navigate.py)) — LinkedIn→external only
Uses the stored `external_url` (or re-derives it), opens the ATS, auto-clicks an
"Apply now" landing button, and detects an auth wall (password field / "sign in").

### act ([act.py](../apply/act.py)) — one action per call
- **`--fill`**: the heavy path — see §3 for the probe cascade. Resolves each field
  (§5), fills via strategy dispatch (§4.3), re-scans for conditional fields, runs the
  read-only audit pass, then emits the next step.
- **`--next`**: detects the step number (6-strategy cascade: progressbar → setsize →
  aria-current → data-step → stepper class → text regex), classifies buttons
  (`ButtonIntentClassifier`), and advances vs. submits based on page position +
  unfilled-required state.
- **`--back`**, **`--submit --confirm`** (gated — §6), **`--inspect`** (diagnostic).

### verify ([verify.py](../apply/verify.py)) — confirm submission
DB stage check → success signals in priority order: success text → **confirmation
URL** → cross-domain page scan → modal-closed/Applied-button heuristics →
**vision last-resort** (only if `ask_api.available()`). On success, marks `applied`
and promotes corrected mappings (§6).

## 3. The fill probe cascade (how 0 fields becomes N fields)

`act --fill` tries, in order, until it has fields:

1. `read_page` (standard DOM scan).
2. SPA poll (up to ~8s) for React/Ember forms that render late.
3. `state._detect_fields` (e.g. the LinkedIn GraphQL questions from detect).
4. `probe_page` ([inspector.py](../apply/common/inspector.py)) — registry-directed
   strategy probing.
5. iframe probing — same-origin first, then cross-origin (navigate into the iframe,
   read, navigate back).

If still 0 fields → model-assisted action finding (`scan_actions` scores buttons; high
score auto-clicks "Apply", else asks the LLM via `model_choice`).

## 4. Flexibility layers (how per-site variation is absorbed)

### 4.1 Platform registry ([registry/](../apply/registry/), [registry.py](../apply/common/registry.py))
YAML per ATS (`greenhouse`, `lever`, `ashby`, `workday`, `linkedin`, `sapsf`) resolved
by domain substring. Supplies: `widget_parent` selector, widget map, `multi_page`,
`has_eeo`, `has_progress_bar`, `page_range`, `QUIRKS` notes, and an optional sibling
`.py` **hook module**. Hooks the fill flow calls when present:

| Hook | When |
|---|---|
| `pre_fill` | expand collapsed sections before reading fields |
| `upload_documents` | platform-specific resume/cover upload widget |
| `flow_hook` | replace the entire fill/next/submit chain (LinkedIn Easy Apply modal) |
| `post_fill` | notify widget frameworks of DOM changes |
| `pre_submit` | scroll/reveal the submit button |

New ATS support is usually a YAML file (no code), occasionally a hook module — callers
don't change.

### 4.2 Platform pattern matching ([platforms.py](../apply/common/platforms.py))
`detect_platform`, and `check_page(text, platform, PATTERNS)` against `LOGIN_WALL`,
`GUEST_APPLY`, `ALREADY_APPLIED` — content patterns (not label keywords), so they work
across languages. Guest-apply buttons are auto-clicked when found.

### 4.3 Field strategies ([strategies/](../apply/strategies/))
`dispatch.field_deterministic` routes by field shape, each with internal fallbacks:

| Field | Strategy | Fallback chain |
|---|---|---|
| `<select>` | `select` | `select_option` → dispatch change/input events |
| combobox / `DROPDOWN` | `combobox` | find trigger → poll listbox + fuzzy/number-range match → native setter |
| text / textarea | `text` | `visible_fill` → `native_setter` (React-safe value setter) |
| contenteditable, datepicker (flatpickr), file upload | dedicated modules | file: labelled input → first file input → drag-drop |
| checkbox (consent) | inline | check only for agree/consent/terms labels |

### 4.4 Cross-cutting helpers
`ButtonIntentClassifier` ([learner.py](../apply/common/learner.py)) maps button text →
`submit/advance/back/cancel` (known-exact → word-score → regex). `PageManager`
([page_manager.py](../apply/common/page_manager.py)) tracks tabs/modals and diffs
snapshots. `_wait_for_change` requires content stability to ignore loading spinners.
CAPTCHA / session-timeout handlers run on every action.

## 5. Preferences & answer resolution

For each field label, `resolution_for_fill` ([resolve.py](../apply/common/resolve.py))
returns a value + provenance, in strict order:

1. **`--answers`** (this run): exact normalized-label match, or ≥10-char prefix match
   (covers `field_reader`'s 60-char label truncation). Provenance `user_typed`.
2. **Profile ephemeral** ([profile.json]): string-valued facts in `_PROFILE_KEYS`,
   derivations (`full_name`; `city`/`state_province`/`country` from `location`, with
   explicit keys winning), and the `profile["answers"]` static map. Provenance
   `ephemeral`.
3. **Mapping store** (Phase 3, only if `use_mappings`): confirmed field→meaning
   mapping; the value is recomputed from the profile and validated against the live
   field. Provenance `mapping`. (Consulted in `act._find_answer`, after 1–2 miss.)

Anything unresolved is `no_match` → reported as unfilled → the LLM supplies it via
`--answers` (which then becomes a *pending* mapping when learning is on).

| Source | Role | Consumed by |
|---|---|---|
| `profile.json` | single source of facts (Tier 1) | deterministic resolver |
| `profile["answers"]` | exact static answers | deterministic resolver |
| `decisions.md` | human judgment policy (relocation, sponsorship, experience) | **the LLM** as context — *not* the deterministic resolver |
| `--answers` | per-run overrides | resolver (highest priority) |
| mapping store | learned recurring answers | resolver (lowest, opt-in) |

**EEO/demographic** fields are detected by decline-option content and are deliberately
**reported, not auto-filled** — the LLM decides via `--answers`, and they are never
cached as mappings.

## 6. Safety & policy layer ([policy.py](../apply/common/policy.py), [gate.py](../apply/common/gate.py), [audit.py](../apply/common/audit.py))

Layered config (defaults ← `apply_policy.json` ← `JI_APPLY_MODE` env ← `act --shadow`):

- **mode** `live|shadow|hold` — shadow/hold fill + screenshot + audit but never submit.
- **`gate.submit_decision`** is the single submit gate: `paused` → blocked (kill-switch);
  non-live → hold; `gate_submit` + invalid fields → hold; else submit.
- **`enforce_validation`** — at fill time, a value failing `validate_value`
  ([validate.py](../apply/common/validate.py)) is escalated (left unfilled) rather than
  typed in. Realizes "advance only on a validated value."
- **audit log** (`results/<jid>/apply_audit.jsonl`) — per-field value, provenance,
  category (`generic/legal/salary/eeo/freetext`), `filled`, `validated`; plus events.
  Doubles as the in-loop LLM's cross-step memory.

All Phase 3/4 enforcement defaults to off — the pipeline behaves exactly as before
until each flag is enabled. See `runbook-shadow-to-mappings.md` for the rollout order.

## 7. Entry-type combinations (the matrix)

| TYPE (detect) | Command flow | Notes |
|---|---|---|
| `easy_apply` | detect → fill → next… → submit → verify | LinkedIn modal; `flow_hook` may own the whole chain; modal re-opened if it closes |
| `ats_direct` | detect → fill → [next…] → submit → verify | single- or multi-page; registry-driven widgets |
| `external` | detect → **navigate** → fill → … → verify | LinkedIn "on company website" → safety-redirect decode → ATS |
| `login_wall` | detect → (manual login) → retry | cookies persist via Chrome profile |
| `guest_available` | detect → fill (auto-clicks guest apply) → … | "continue without signing in" |
| `already_applied` | detect → none | Applied button / text detected |
| `unknown` | detect → `act --inspect` | screenshot + HTML dump + probes to diagnose |

Multi-page flows loop `fill → next` (step detection drives "advance vs submit") until a
submit button appears or `verify` passes; a crash mid-flow is recovered by re-running
from the top (no ATS commits before the final submit; pre-filled fields are skipped).
