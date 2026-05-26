# Filler rewrite — complete specification

## Architecture

Three-phase loop per form page:
1. **SCAN** — find all questions on the page
2. **PRESENT** — show grouped questions to model, get answers
3. **APPLY** — apply each answer via best mechanism, verify, cascade

Phase 2 (PRESENT) can be skipped if `--answers` provides everything.

---

## Phase 1: SCAN

### What to find

All interactable controls visible on the page. Search document root AND `[role="dialog"]` separately, merge results.

**Targets in priority order** (not a ranking per-question, just what to collect):
- `<button>` with visible text (offsetParent !== null)
- `<label>` with `for` attribute pointing to a form element
- `<input>` not hidden, not submit
- `<select>`
- `<textarea>`
- `[role="radio"]`, `[role="checkbox"]`, `[role="textbox"]`, `[contenteditable]`

### What to exclude

Navigation buttons. A button is navigation if its visible text matches:
```
submit, next, back, review, save, cancel, upload file, upload, browse, edit, delete,
add, remove, home, me, for business, skip to main, close, more, follow, saved, 
i'm interested, notifications, messaging, jobs, my network
```
Plus any zero-size, invisible, or disabled buttons.

### How to group into questions

For each control, extract its question text:

1. **If tag is `<input/select/textarea>`**: check for `<label for="id">`. If found, use label text as question.
2. **If no label-for**: walk up parent chain. At each level, look for text elements (label, legend, strong, p, span, heading) that are NOT the control itself. Stop at the first ancestor that contains >=1 text element with length >5 chars. Use that text as question.
3. **If tag is `<button>`**: same parent walk. The button's own text is the answer option, not the question. The question is the nearest text element above the button.
4. **If tag is `<label>` with `for`**: the label text IS the question. The target input is the answer slot.

All controls sharing the same question text (within 80% similarity) are grouped into one question unit.

**The output of SCAN** is a list of question objects:
```python
{
  "text": "Do you require visa sponsorship?",
  "controls": [
    {"type": "button", "text": "Yes", "tag": "BUTTON", ...},
    {"type": "button", "text": "No", "tag": "BUTTON", ...},
    {"type": "radio", "text": "Yes, I require...", "value": "on", "checked": false, "id": "x", ...},
    {"type": "radio", "text": "No, I do not...", "value": "on", "checked": false, "id": "y", ...},
  ],
  "best_mechanism": "button",  # rarest among detected: button > label-click > input
  "status": "unanswered",  # or "answered" if any control is checked/has value
  "answer": None,
}
```

---

## Phase 2: PRESENT

### To the model

```python
Questions that need answers (3):

Q1: "Do you require visa sponsorship to work in the United States?"
  Options: Yes / No
  Best control: button (platform intended)

Q2: "Are you able to work in-person at least 3 days a week?"
  Options: Yes, in the San Francisco office / Yes, in the Toronto office / No
  Best control: radio (standard)

Q3: "Have you ever been employed by EvenUp or an EvenUp affiliate?"
  Options: Yes / No
  Best control: button (platform intended)
```

### From the model

```python
--answers '{
  "Do you require visa sponsorship to work in the United States?": "Yes",
  "Are you able to work in-person at least 3 days a week?": "Yes, in the Toronto office",
  "Have you ever been employed by EvenUp or an EvenUp affiliate?": "No"
}'
```

The model answers EVERY question in one shot. No back-and-forth. Unanswered questions stay pending.

---

## Phase 3: APPLY

For each question with an answer:

1. **Read pre-state** of all controls in the group
2. **Select best mechanism** from the ranked controls:
   - If button exists: click the button whose text matches the answer (first word match for short buttons, exact match for long buttons)
   - If no button but label exists: click the label (triggers associated input)
   - If no label: set value directly on the input, dispatch `input` + `change` events
3. **Verify post-state** — check any control in the group changed state (radio checked, input value changed, button class includes selected/active)
4. **If unchanged**, cascade to next mechanism (label click → input manipulation)
5. **If all mechanisms fail**, log to pending_questions.json with full context

### Verification table

| Control | Pre-state | Post-state |
|---------|-----------|------------|
| radio | `el.checked` | `el.checked` |
| checkbox | `el.checked` | `el.checked` |
| text/email/tel | `el.value` | `el.value` |
| select | `el.value` | `el.value` |
| button | `el.classList.contains("selected")` or `el.getAttribute("aria-pressed")` | same |

---

## Saving answers

After a question is successfully answered, save to `common_answers` with key = first 3 significant words of question text, namespaced by section heading if available.

```python
# No section heading
"require visa sponsorship" -> common_answers["require_visa_sponsorship"] = "Yes"
# With section heading "Work Eligibility"
"Work_Eligibility:require_visa_sponsorship" -> "Yes"
```

---

## Multi-page

After all questions on current page are answered:

1. Find the "Next" or "Submit" button (global search, not scoped to question section)
2. Click it
3. Wait 3s for page transition
4. Re-scan
5. If new questions found, repeat from SCAN
6. If no new questions, done (successfully submitted)

If "Next" click doesn't produce new questions within 5s, check for error messages on page. If errors found, abort and report.

---

## Pending questions

When a question can't be answered:

```python
# pending_questions.json
{
  "jid": "0f7e4c97...",
  "url": "https://jobs.ashbyhq.com/evenup/...",
  "company": "EvenUp",
  "title": "Senior Backend Engineer",
  "questions": [
    {
      "text": "Are you willing to relocate to San Francisco?",
      "options": ["Yes", "No"],
      "reason": "model did not provide answer"
    }
  ]
}
```

Batch collected. Presented to user at end of session. Once answered, jobs can be re-attempted.

---

## Files to change

| File | Change |
|------|--------|
| `apply/common/01_fill_fields.py` | Complete rewrite: SCAN → PRESENT → APPLY |
| `apply/common/__init__.py` | Add grouping utility, remove old find_apply_page if replaced |
| `apply/common/platforms.py` | No change |
| `apply/linkedin/easy_apply/03_fill_fields.py` | Thin shell (delegates to common), no change needed |
| `apply/linkedin/external/03_submit.py` | Update to use new question model for verification |
| New: `apply/common/pending.py` | Pending questions management |

---

## Testing plan

1. **EvenUp (Ashby external)** — re-run full flow, verify buttons clicked not radios
2. **LinkedIn Easy Apply** — run one job, verify modal fields detected, filled, next/submit
3. **LinkedIn External (Greenhouse/Lever/Workday)** — run one of each if available in DB
4. **Multi-page** — find a 2-page form, verify fill → next → fill loop
5. **Multi-question** — batch model provides all answers at once, verify each applied correctly
