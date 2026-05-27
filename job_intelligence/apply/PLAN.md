# Apply pipeline — consolidation plan

## Architecture

3 scripts: **detect** → **act** → **verify**

No page-order scripts (no 01_click, 04_resume, 04_screening, 05_click_next). Everything is handled by `act --fill`, `act --next`, `act --submit`.

---

## `detect.py`

**Purpose:** Classify the job type and set up the session.

**Usage:**
```
python3 detect.py <jid>
```

**Logic:**
1. Check DB stage — if "applied", print `STATUS: already_applied`, exit
2. If LinkedIn URL, navigate to `/apply/` URL
3. If external URL, navigate directly
4. Check page state:
   - Dialog visible with fields → `TYPE: easy_apply`
   - "Applied" button visible → `TYPE: already_applied`
   - "Apply on company website" → `TYPE: external`
   - Dialog with no fields → `TYPE: unknown`
5. Print job info + detected type
6. Save state to `~/.openclaw/apply_state.json`

**Stdout:**
```
JOB: Senior Engineer @ Acme
TYPE: easy_apply
STAGE: page1
PAGE: {
  "fieldCount": 3,
  "fields": [...],
  "buttons": [{"text": "Next", "disabled": false}],
  "hasFileInput": true,
  "hasUnfilledRequired": false
}
NEXT: act --fill
```

---

## `act.py`

**Purpose:** Perform ONE action. Always reads fresh state, always prints result.

**Subcommands:**

### `act --fill <jid>`

Fills ALL fields on current page from `--answers` + common_answers + profile. Uploads resume if file input present. Prints what was filled and what remains.

**Usage:**
```
python3 act.py --fill <jid> [--answers '{"q":"val"}']
```

**Logic:**
1. Read page state (all inputs, selects, radios, buttons, file inputs)
2. Fill from `--answers` (substring match) + common_answers (fuzzy match) + profile (name, email, phone, linkedin)
3. Upload resume to ALL file inputs via `set_input_files`
4. Skip already-checked radios, already-filled fields
5. Print filled count + unfilled required fields

**Stdout (page fully filled):**
```
FILLED: 3  UNFILLED: 0
FIELDS: [
  {"label": "Full Name", "value": "Shervin Naseri"},
  {"label": "Email", "value": "shervin.naseri@gmail.com"},
  {"label": "Resume", "file": "uploaded"}
]
BUTTONS: [{"text": "Next", "disabled": false}]
NEXT: act --next
```

**Stdout (unfilled remain):**
```
FILLED: 0  UNFILLED: 2
UNFILLED: [
  {"label": "How many years of Python?", "tag": "INPUT:text"},
  {"label": "Willing to relocate?", "tag": "SELECT", "options": ["Yes","No"]}
]
NEXT: act --fill --answers '{"How many years of Python?": "5", "Willing to relocate?": "Yes"}'
```

### `act --next <jid>`

Clicks the primary button (Next / Review / Submit). Waits for new state. Prints result.

**Usage:**
```
python3 act.py --next <jid>
```

**Logic:**
1. Read dialog buttons
2. Find the best button to click: Submit > Review > Next
3. Click it (Playwright native click, disable overlay)
4. Wait 3s
5. Read new dialog state

**Stdout (next page loaded):**
```
ACTION: Next -> clicked
PAGE: {
  "fieldCount": 5,
  "fields": [...],
  "buttons": [{"text": "Review", "disabled": false}]
}
NEXT: act --fill
```

**Stdout (submit or modal closed):**
```
ACTION: Submit -> clicked
STATUS: submitted
NEXT: verify <jid>
```

### `act --submit <jid>`

Like --next but specifically clicks Submit. Dry-run safe (prints what it would do, requires `--confirm` to actually click). Only used on the review page.

**Usage:**
```
python3 act.py --submit <jid> [--confirm]
```

---

## `verify.py`

**Purpose:** Check if submission was successful. Reads page state and DB. No state mutation.

**Usage:**
```
python3 verify.py <jid>
```

**Logic:**
1. Check if modal is closed → submitted
2. Check if LinkedIn shows "Applied" button → submitted
3. Check if page shows "thank you" / "submitted" → submitted
4. Check DB stage already "applied" → submitted
5. Otherwise → unknown

**Stdout:**
```
STATUS: submitted
```

or

```
STATUS: unknown
PAGE: dialog still open, 3 fields unfilled
NEXT: act --fill --answers '{"q":"val"}'
```

---

## Flow loop (model follows)

```
detect <jid>
  ├── TYPE: easy_apply → act --fill → act --next → act --fill → ... → act --submit → verify
  ├── TYPE: external   → act --navigate → act --fill → act --next → ... → act --submit → verify
  ├── TYPE: already_applied → done
  └── TYPE: unknown → report, skip
```

At each `act --fill`, if unfilled remain, model provides `--answers` and re-runs `act --fill`. Loop continues until Submit clicked or error.

---

## Files to create

| File | Purpose |
|------|---------|
| `apply.py` | Root orchestrator: detect | act | verify subcommands |
| `apply/detect.py` | Job type classification |
| `apply/act.py` | All actions: --fill, --next, --submit |
| `apply/verify.py` | Post-submit verification |

## Files to remove

| File | Reason |
|------|--------|
| `apply/linkedin/detect.py` | Merged into apply/detect.py |
| `apply/linkedin/easy_apply/*` | All 6 scripts — covered by act --fill/--next/--submit |
| `apply/linkedin/external/01_navigate.py` | Covered by detect.py |
| `apply/linkedin/external/03_submit.py` | Covered by act --submit |
| `apply/common/01_fill_fields.py` | Covered by act --fill |
| `apply/common/02_click_next.py` | Covered by act --next |
| `apply/detect_ats.py` | Merged into apply/detect.py |
| `apply/common/__init__.py` | find_apply_page no longer needed (each step navigates fresh) |

## Notes

- Each script reads fresh state — no shared page objects, no stale markers
- `--answers` is the ONLY source of model decisions — no resolve(), no fuzzy_match() auto-guessing
- File inputs handled automatically by act --fill (set_input_files on ALL found)
- Radios: direct `radio.click()`, verify `el.checked` changed
- Selects: Playwright `.select_option()`
- Unfollow company checkbox: auto-unchecked by act --fill if found on any page
