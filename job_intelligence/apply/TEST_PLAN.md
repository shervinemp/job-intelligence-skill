# Apply pipeline — experiment findings

## Experiment Results Summary

### 1. Greenhouse
**Result: WORKS with generic pipeline.** Form fields (29 on Vercel) are **pre-loaded in the DOM** — no "Apply" button click needed. "Submit application" button present and enabled.

Flow: `navigate → act --fill → act --submit`

**Details:**
- Fields pre-loaded: 17 text, 7 bare `<input>`, 1 search, 1 tel, 2 file, 1 textarea
- No `<select>` elements on Greenhouse (they use custom autocomplete inputs like Country)
- Submit button: `type="submit"` with text "Submit application" — inside the form
- "Apply" button on page is a scroll trigger (does not submit)
- `pageType`: "form" (29 fields > 0)

**Fix needed:** `cmd_submit` must prioritize "Submit application" over "Apply" in button selection. Done.

### 2. Lever
**Result: WORKS after apply-link-following fix.** Job listing page shows 0 fields with `pageType=maybe_form`. "Apply for this job" link (`/apply` suffix) reveals 12-field form.

Flow: `navigate → act --fill (auto-follows apply link) → act --submit`

**Details:**
- Job listing: 0 fields, `pageType: "maybe_form"` (body has "apply" text)
- Apply link pattern: `<a>` with text "apply for this job" → href ending in `/apply`
- Application form: 12 fields (1 file, 9 text, 1 email, 1 textarea), labels detected via `el.closest('label')`
- Submit button: "Submit application" enabled
- No Select/Radio elements

**Fix needed:** `cmd_fill` when fieldCount==0 and pageType=="maybe_form"/"unknown" must search for apply link/button and follow it. Done.

**Label fix:** Lever uses `<label><div class="application-label">Label text</div><div class="application-field"><input ...></div></label>` — label is grandparent, found via `el.closest('label')`. Done.

### 3. Workday
**Result: PARTIAL — login wall + JS-heavy portal.** The careers portal (`workday.wd5.myworkdayjobs.com/en-US/Workday`) shows only a search field. Specific job URLs may 404 or require sign-in. No shadow DOM found on portal pages (0 shadow roots).

**Details:**
- Portal: 1 field (search), `pageType: "form"` (the search field counts)
- Job page: `pageType: "login_wall"` (sign-in button detected) — 0 fields, 0 forms
- Shadow DOM: 0 hosts, 0 fields in shadow
- Specific job URL tested returned 404 ("The page you are looking for doesn't exist")
- `detect_platform("myworkdayjobs.com")` returns "workday"

**Flow (theoretical):** `navigate → act --fill → login wall detected → guest apply (if available) → ???`
- Guest apply patterns exist in `platforms.py`: "continue without signing in", "apply as guest"
- Need to test with a LIVE Workday job posting that accepts applications

**Blocking issues:**
- Workday job postings expire quickly (the one I tested was dead)
- Workday application forms are known to use shadow DOM internally (need live job to verify)
- Workday may require session cookies or redirect through sign-in flow first
- The actual application form is likely behind shadow DOM (based on developer reports)

### 4. Ashby (regression test)
**Result: WORKS (unchanged).** 20 fields detected directly, `pageType: "form"`. No apply link following needed.

Flow: `navigate → act --fill → act --submit`

**Details:**
- 20 fields (file, text, email, tel, radio groups)
- Radios detected correctly
- Submit button: "Submit Application" enabled
- `detect_platform("ashbyhq.com")` returns "ashby"

### 5-6. iCIMS, Taleo, SmartRecruiters
**Not tested.** No jobs found in DB with these URLs. Need to acquire test jobs.

### 7-8. Easy Apply external redirect, multi-field types
**Not tested.** No suitable jobs found.

### 9. Login wall recovery
**Result: IMPLEMENTED.** Guest apply click-through added to `cmd_fill`:
1. Detects login wall via `check_page()` patterns
2. Searches for guest apply buttons (from GUEST_APPLY patterns)
3. Clicks/follows the first match
4. Re-reads page
5. If still login wall → aborts

### 10-30. Other experiments
**Not run.** Remaining experiments (session timeout, conditional fields, shadow DOM, file upload on optional fields, duplicate submission, etc.) require specific job scenarios not currently available.

## Pipeline Status

### Working platforms
| Platform | Flow | Status |
|----------|------|--------|
| LinkedIn Easy Apply | detect → act --fill → act --next → loop → act --submit | Working |
| Greenhouse | navigate → act --fill → act --submit | **Working (fixed)** |
| Lever | navigate → act --fill (auto apply-link) → act --submit | **Working (fixed)** |
| Ashby | navigate → act --fill → act --submit | Working |

### Partially working
| Platform | Issue | Status |
|----------|-------|--------|
| Workday | Login wall + shadow DOM + dead URLs | Need live job to test |

### Not tested
| Platform | Missing |
|----------|---------|
| iCIMS, Taleo, SmartRecruiters, BambooHR, etc. | No test jobs available |

## Key Fixes Applied

### 1. read_page dialog scoping (Greenhouse fix)
**Problem:** `const container = document.querySelector('[role="dialog"]') || document;` picked up a non-form dialog on Greenhouse (a search overlay with 1 field), missing all 29 form fields.

**Fix:** Always use `document` as the container. Removed dialog-scoping logic.

### 2. Label detection for wrapping `<label>` (Lever fix)
**Problem:** Lever uses `<label><div>Label</div><div><input></div></label>` — the `el.closest('div,fieldset,...')` found the inner DIV, then `parent.querySelector('label')` found nothing because the label is the GRANDPARENT, not a child.

**Fix:** Added `el.closest('label')` before parent-child search.

### 3. Apply-link-following (Lever fix)
**Problem:** Lever job listing page has 0 form fields. The form is at a separate `/apply` URL reachable by clicking "apply for this job".

**Fix:** When `cmd_fill` finds 0 fields + `pageType` is "maybe_form" or "unknown", search for "apply" links/buttons. If found as `<a>`, follow href. If `<button>`, click it. Then re-read page.

### 4. Guest apply click-through (login wall fix)
**Problem:** Login wall detection printed a message and aborted, with no attempt to click guest apply buttons.

**Fix:** Before aborting on login wall, search for guest apply patterns from GUEST_APPLY dict. Click/follow first match. Re-read page. If still blocked, abort.

### 5. Submit button priority (Greenhouse fix)
**Problem:** `cmd_submit` used simple `in (keywords)` matching, picking the first match in DOM order. On Greenhouse, "Apply" (scroll trigger) appeared before "Submit application" (submit button).

**Fix:** Use priority-ordered keyword matching: "submit application" → "submit" → "send application" → "apply" → "send". Exact match first, then substring.

### 6. Test jobs inserted
Three test jobs added to DB for pipeline testing:
- **Vercel** (Greenhouse): `f05f3c6f82accdd6`
- **LatchBio** (Lever): `ef12f7d265927f4f`
- **EvenUp** (Ashby): `ad4c0c14ae475450`

All advanced to "tailored" stage with dummy descriptions.

## Files Modified
- `apply/common/page_helpers.py` — read_page: removed dialog scoping, added `el.closest('label')`
- `apply/act.py` — cmd_fill: apply-link-following, guest apply click-through; cmd_submit: priority-ordered submit keywords
- `apply/TEST_PLAN.md` — this file

## Next Steps
1. Find a live Workday job to test shadow DOM + guest apply flow
2. Test iCIMS, Taleo, SmartRecruiters when jobs become available
3. Run experiments 10-30 when suitable test scenarios arise
4. Test full end-to-end with real resume PDF
5. Test `cmd_auto` on all platforms
