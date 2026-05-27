# Apply pipeline — consolidation plan v2

## Architecture

4 scripts: **detect → act → verify**

`act` handles ALL interactions: fill, next, back, submit, auto. No page-order scripts (no 01_click, 04_resume, etc.).

---

## `detect.py`

Classifies job entry point. Does ONE thing — no side effects.

**Usage:** `python3 detect.py <jid>`

**Logic:**
1. DB stage = "applied" → `STATUS: already_applied`
2. LinkedIn URL → navigate to `/apply/`:
   - Dialog with fields → `TYPE: easy_apply`
   - "Applied" button → `TYPE: already_applied`
   - "On company website" → `TYPE: external`
   - Nothing → `TYPE: unknown`
3. External URL → navigate directly:
   - Form detected → `TYPE: ats_direct`
   - Auth wall → `TYPE: auth_wall`
4. Prints job info + type + page state

**Stdout:**
```
JOB: Senior Engineer @ Acme
TYPE: easy_apply
PAGE: { 3 fields, buttons: [Next], fileInput: false }
NEXT: act --fill
```

---

## `navigate.py`

Handles the LinkedIn → External ATS transition. Separate from detect because it's a multi-step action.

**Usage:** `python3 navigate.py <jid>`

**Logic:**
1. Navigate to LinkedIn jobs/view URL
2. Click "Apply on company website" button
3. Wait for new tab or URL change
4. Detect ATS platform (Ashby, Greenhouse, etc.)
5. Save external URL to state

**Stdout:**
```
EXTERNAL_URL: https://jobs.ashbyhq.com/acme/xxx
PLATFORM: ashby
NEXT: act --fill
```

If button not found or no new tab:
```
ERROR: no external URL — job may be closed or premium-walled
NEXT: none
```

---

## `act.py`

ONE action per call. Always reads fresh state. Always verifies before/after.

### `act --fill <jid> [--answers '{}']`

Fills ALL fields on current page. Targets required file inputs only (skips optional drop zones). Handles radios, selects, text, checkboxes, file inputs.

**Logic:**
1. Read all fields + file inputs + buttons
2. Apply --answers (substring match, longest unique prefix wins)
3. Apply common_answers (fuzzy word overlap)
4. Fill profile fields (name, email, phone, linkedin — deterministic)
5. Upload resume to required file inputs via `set_input_files`
6. Uncheck "Follow company" checkbox if present (always)
7. Verify each change: `el.value` / `el.checked` changed

**Stdout (done):**
```
FILLED: 4  UNFILLED: 0
BUTTONS: [{"text": "Next", "disabled": false}]
NEXT: act --next
```

**Stdout (unfilled remain):**
```
FILLED: 2  UNFILLED: 1
UNFILLED: [{"label": "Years of Python?", "tag": "INPUT:text", "options": []}]
NEXT: act --fill --answers '{"Years of Python?": "5"}'
```

### `act --next <jid>`

Clicks the best forward button. Never clicks Back, Cancel, Save, Edit.

**Logic:**
1. Read all buttons
2. Pick forward button by priority: Submit > Review > Next > Continue > Done
3. If none found → `NO_BUTTON`
4. If disabled → `BUTTON_DISABLED: Next is disabled — 2 required fields empty`
5. Click via Playwright (disable overlay)
6. Wait 3s
7. Read new page state

**Stdout (advanced):**
```
ACTION: Next  BUTTON: Review
PAGE: { 5 fields, buttons: [Back, Submit], fileInput: false }
NEXT: act --fill
```

**Stdout (submit):**
```
ACTION: Next  BUTTON: Submit application
PAGE: { 0 fields, buttons: [], modal: false }
NEXT: verify
```

### `act --back <jid>`

Clicks the Back button. Returns to previous page. Used if model detects wrong answer.

**Logic:** Same as --next but clicks Back.

**Stdout:**
```
ACTION: Back
PAGE: { ... }
NEXT: act --fill
```

### `act --submit <jid> [--confirm]`

Specifically clicks Submit on the review page. Without --confirm, dry-run.

### `act --auto <jid>`

Runs the full loop without model intervention: fill → next → fill → next → ... → submit. Reports progress at each step.

**Logic:**
1. Loop: fill → check unfilled → if none, next → check result → if more fields, loop
2. If unfilled remain → print them and STOP (model provides --answers, re-run --auto)
3. If Submit clicked → verify
4. If error → print and STOP

**Stdout:**
```
AUTO: page 1 — 3 fields filled
AUTO: next → page 2 — 1 field filled, 1 unfilled
AUTO: STOP — 1 unfilled: {"Years of Python?"}
NEXT: act --fill --answers '{"Years of Python?": "5"}'
```

---

## `verify.py`

Checks submission result. Reads page state + DB.

**Usage:** `python3 verify.py <jid>`

**Logic:**
1. Modal closed → `STATUS: submitted`
2. LinkedIn "Applied" button → `STATUS: submitted`
3. "Thank you" / "submitted" text → `STATUS: submitted`
4. DB stage = "applied" → `STATUS: submitted`
5. None → `STATUS: unknown`

---

## Flow

```
detect <jid>
  easy_apply:
    act --fill → act --next → act --fill → ... → act --auto (loop)
    If unfilled remain → model provides --answers, re-run --fill
  external:
    navigate → act --fill → act --next → ... → act --auto
  ats_direct:
    act --fill → act --next → ... → act --auto
  already_applied:
    done

At any point:
  act --back → act --fill (fix answers) → act --next
  verify → confirm submission
```

## Files to create

| File | Purpose |
|------|---------|
| `apply.py` | Root: detect | navigate | act (--fill/--next/--back/--submit/--auto) | verify |
| `apply/detect.py` | Job type classification |
| `apply/navigate.py` | LinkedIn → External ATS transition |
| `apply/act.py` | All actions |
| `apply/verify.py` | Post-submit verification |

## Files to remove

All under `apply/`: linkedin/, common/, detect_ats.py — replaced by the 4 scripts above.

## Key design decisions

- **Fresh navigation for LinkedIn Easy Apply** — `/apply/` URL reliably re-opens modal. Lost state on crash is acceptable (max 3 pages).
- **Reuse existing page for external ATS** — navigate.py leaves the tab open. act --fill/--next find it by URL match. Avoids losing multi-page state.
- **--answers is the ONLY answer source** — no resolve(), no fuzzy_match() auto-guessing. Common_answers only used for previously saved answers.
- **Resume upload to required file inputs only** — `input[type="file"][required]` — skips optional drop zones.
- **Button priority: Submit > Review > Next > Continue > Done** — explicit list, never clicks Back/Cancel/Save/Edit.
- **Disabled button detection** — --next checks `disabled` before attempting. If disabled, reports why.
- **act --auto for batch** — full loop with model override points on unfilled fields.
