# Apply pipeline — debugging experiments

## Setup

For each experiment: use the same job across tests, or note when a new job is needed.  
Jobs at `extracted` stage, run `fetch.py admit <jid>` before each test to advance to `described`.  
Then `tailor.py --jid <jid>` to generate PDF.  
Then `detect <jid>` to start the apply flow.

## 1. Greenhouse external apply

**Goal:** Verify the generic fill/next loop works on Greenhouse without platform-specific code.

**Experiment:**
1. Find a LinkedIn job pointing to Greenhouse (check URL for `greenhouse.io`)
2. `detect <jid>` → should show `TYPE: external`
3. `navigate <jid>` → should capture external URL, detect platform "greenhouse"
4. `act --fill <jid>` → read Greenhouse form fields
5. `act --next <jid>` → advance through pages
6. Loop until submit

**Inspect:**
- Does `read_page()` detect all Greenhouse form fields? (text, selects, radios, file uploads)
- Does Greenhouse use shadow DOM? → check `pageType` in output
- Are Greenhouse's multi-select fields detected as SELECT or custom elements?
- Does `detect_platform("greenhouse.io")` return "greenhouse"?
- Worksheet fields (salary inputs, date pickers, etc.)

## 2. Lever external apply

**Experiment:** Same as Greenhouse, but find a Lever-hosted job.

**Inspect:**
- Lever uses custom async field loading. After `navigate`, does `read_page()` wait long enough?
- Lever's "Additional Questions" section — are these detected as form fields or just text?
- Lever's resume upload — is `input[type="file"]` present and accessible?

## 3. Workday external apply

**Experiment:**
1. Find a LinkedIn job with Workday URL (`myworkdayjobs.com` or `workday.com`)
2. `detect <jid>` → should show `TYPE: external`
3. `navigate <jid>` → should detect "workday" platform, capture external URL
4. `act --fill <jid>` → check `pageType`

**Hypothesis:** Workday uses shadow DOM. Standard `querySelectorAll('input, select, textarea')` finds nothing. Expect `pageType: "maybe_form"` or `"unknown"`.

**Verification:**
- Navigate to the Workday URL manually in Chrome DevTools. Run:
  ```js
  document.querySelectorAll('input:not([type=hidden]):not([type=submit]), select, textarea').length
  ```
- Check for shadow DOM:
  ```js
  document.querySelectorAll('*').forEach(el => { if (el.shadowRoot) console.log(el.tagName); });
  ```
- If shadow DOM found, try:
  ```js
  el.shadowRoot.querySelectorAll('input, select, textarea')
  ```

**Hypothesis fix:** Playwright's `page.evaluate` can access shadow DOM via `element.shadowRoot`. Need a recursive walk to find all form controls inside shadow roots. Not a generic fix — Workday-specific or a general `deepQuerySelectorAll` helper.

## 4. ICE (iCIMS) external apply

**Experiment:** Similar to Greenhouse/Lever. Find an iCIMS-hosted job.

**Inspect:**
- iCIMS typically uses standard HTML forms. Likely works with generic fill.
- Check for iCIMS-specific field types: location autocomplete, job-specific questions.
- Does `detect_platform` return "icims"?

## 5. Taleo external apply

**Experiment:** Find a Taleo-hosted job (taleo.net).

**Inspect:**
- Taleo uses `<input type="text">` and `<select>` — standard HTML.
- Taleo often redirects through login gate before showing application form.
- Test: does `act --fill` detect login wall first, or reach the form?
- Does Taleo require a session cookie from a previous page? → test by navigating directly.

## 6. SmartRecruiters external apply

**Experiment:** Find a SmartRecruiters-hosted job.

**Inspect:**
- SmartRecruiters uses a React SPA. Form controls are standard HTML but rendered asynchronously.
- Test: does `read_page()` wait long enough for the form to load? Try increasing `time.sleep(5)` to 8.
- Check page source for form fields after 5s vs 10s.

## 7. Easy Apply with external redirect mid-flow

**Likely scenario:** LinkedIn Easy Apply modal → Next → redirects to external ATS instead of staying in modal.

**Experiment:**
1. Find an Easy Apply job that actually redirects to external (rare but happens)
2. `detect <jid>` → TYPE: easy_apply
3. `act --fill` → fill contact info
4. `act --next` → click Next

**Inspect:**
- Does the modal close and a new tab open?
- Does `cmd_next` detect the page change? Check output for `PAGE: { ... }`
- If the new tab has form fields, does the next `act --fill` pick them up?
- The `find_page` function searches for jobs/view or external_url. After redirect, neither may match.

**Hypothesis:** `cmd_next` reads the SAME page after clicking Next. If LinkedIn's modal closes and a new tab opens with the external ATS, `read_page` on the old tab would show `{fieldCount: 0}`. The function would print "STATUS: modal_closed" or "STATUS: submitted" depending on body text. The model would see this and might assume submission before it actually happened.

**Hypothesis fix:** After detecting modal closed but no "thank you" text, check for NEW tabs/pages that opened in `ctx.pages`. Look for non-LinkedIn URLs with form fields. If found, update the state's external_url and continue.

## 8. Multi-field types (not covered)

**Experiment:** Check each field type across platforms:

- `<input type="number">` — does `el.fill("5")` work?
- `<input type="date">` — does `el.fill("2026-06-01")` work?
- `<input type="tel">` — does `el.fill("+1...")` work?
- `<textarea>` — does `el.fill("text")` work?
- `<select multiple>` — does `el.select_option(["opt1", "opt2"])` work? Or does it need single?
- Custom dropdowns (divs that look like selects) — are they detected at all?

**Test each against a known platform. Document which work and which don't.**

## 9. Login wall recovery

**Experiment:**
1. Find an external ATS job behind a login wall
2. `navigate <jid>` → captures URL
3. `act --fill <jid>` → should detect login wall

**Inspect:**
- Does `check_page(text, plat, LOGIN_WALL)` return True?
- Does the function print "LOGIN_WALL" and return without filling?
- Does `pageType` show "login_wall"?

**Hypothesis fix:** After login wall detection, try clicking guest apply buttons from `GUEST_APPLY` patterns. The current code has `GUEST_APPLY` patterns but doesn't implement the click. Add guest apply click flow:

```python
if login_wall_detected:
    for pattern in GUEST_APPLY.get(plat, []) + GUEST_APPLY["default"]:
        # click button matching pattern
        # wait for page to reload
        # re-read_page
```

## 10. Session timeout during multi-page forms

**Experiment:**
1. Start Easy Apply on a job with 2+ pages
2. Fill page 1, click Next
3. Wait 10+ minutes (simulated by sleeping)
4. Fill page 2 → expect session timeout

**Inspect:**
- Does the page state change to a login/error screen?
- Does `read_page()` return `{fieldCount: 0}`?
- Does `pageType` detect the change?

## 11. Already-applied detection (LinkedIn page vs DB)

**Experiment:**
1. Apply to a job manually (or submit via pipeline)
2. Mark DB stage as "extracted" (simulating stale DB)
3. Run `detect <jid>`

**Inspect:**
- Does detect find "Applied" button on LinkedIn and update DB?
- What if the LinkedIn page shows "Applied" but the job listing was reposted?
- What if the LinkedIn page shows nothing (job removed)?

## 12. State file corruption (multi-job)

**Experiment:**
1. `detect job_A` → state saved for job_A
2. `detect job_B` → state overwritten to job_B
3. `act --fill job_A` → should trigger JID validation guard

**Inspect:**
- Does `state.get("jid") != jid` catch the mismatch?
- Does the error message guide the model correctly?
- What if the user runs `act --fill` without any `detect` first? (state file missing)

## 13. Resume upload fails silently

**Experiment:**
1. Run `act --fill` on a page with a file input but no resume PDF in results dir
2. Check output: does it print an error or silently skip?

**Inspect:**
- Does `_has_pdf(jid)` return False?
- Does `results_dir` exist? (it might not for new jobs)
- Does the code handle `os.listdir()` failing gracefully?

## 14. Radio button re-fill (idempotency)

**Experiment:**
1. `act --fill` on a page with radio buttons → fills them
2. `act --fill` again on the same page → should skip already-filled

**Inspect:**
- Does `_fill_radios` check `rf.checked` before clicking?
- If a radio group was skipped (unfilled), does re-fill catch it?

## 15. Select option matching (partial text)

**Experiment:** On a job with dropdown selects:
1. Provide `--answers '{"Country": "Canada"}'`
2. `_fill_text` checks `ans.lower() in opt.lower()` → "canada" in "Canada (+1)" → match
3. `el.select_option("Canada (+1)")` → should select that option

**Inspect:**
- What if the option text is longer than 80 chars? The `options` list in `read_page` is sliced.
- What if two options match the same substring? (e.g., "Canada (+1)" and "Canada (+2)")

## 16. Multi-page Easy Apply with variable page count

**Experiment:** Find a job with 3+ Easy Apply pages (rare but exists for companies like HubSpot).
1. Detect → fill → next → fill → next → fill → next → review → submit
2. Count pages vs the `cmd_auto` max of 10.

**Inspect:**
- Does `cmd_auto` handle 5+ pages without issue?
- What happens if a page has no forward button? (fields auto-validate or next appears after delay)
- Does `read_page` miss buttons that appear after AJAX?

## 17. Conditional fields (show/hide based on answers)

**Experiment:** Find a form where selecting "Yes" shows additional fields.
1. `--answers '{"Have experience?": "Yes"}'`
2. After fill, does the page re-render and show new fields?
3. Does `act --next` click Next successfully, or do we need another `act --fill` first?

**Hypothesis:** `act --fill` fills the visible fields. If selecting an option reveals new fields (via JS), the new fields are already in the DOM? Or do they load asynchronously? Test by reading `fieldCount` before and after fill.

## 18. File upload on non-required fields

**Experiment:** Pick a job where resume upload is optional.
1. `act --fill` → should NOT upload to non-required file inputs (the code checks `f.get("required", False)`)
2. Verify by checking if the file input received the file.

## 19. Duplicate submission guard

**Experiment:** After successful submit, run `act --submit --confirm` again.
1. First submit succeeds (modal closes, DB updated to "applied")
2. Second submit should fail because DB says "applied"

**Inspect:**
- Does `cmd_submit` check the DB stage first? No — it goes straight to the page.
- If the modal is closed (already submitted), `read_page` returns `{fieldCount: 0}` and `cmd_submit` prints "NO_SUBMIT_BUTTON". Not a crash, but the model might be confused.
- Fix: add DB stage check at the start of `cmd_submit`.

## 20. Easy Apply with "Review" page not showing correctly

**Experiment:** The Review page sometimes doesn't show "Submit application" — it shows "Next" instead (LinkedIn bug or feature). Run detect on a job and check if "Next" on the review page actually submits.
- `act --next` should click "Next" on the last page
- After clicking, does the modal close with a "thank you"?

## 21. LinkedIn Easy Apply — modal closes on external click on the same page (like you scroll down)

Some LinkedIn jobs open a modal but clicking the "Apply on company website" button INSIDE the modal closes the modal and navigates away. The `detect` script checks for `TYPE: external` at the page level.

**Experiment:** Find a job where Easy Apply modal has an "Apply on company website" link.
- Detect shows `easy_apply` (because dialog modal present), but the dialog contains an external link.
- Model runs `act --fill` on things inside the modal, but the real action is external.
- This is a contradiction — the modal IS present but the submit method is external.

## 22. Shadow DOM detection gap

**Experiment:** On a Workday job, the `read_page` function returns `fieldCount=0`. But the page IS a form.

**Test script:**
```python
from lib.chrome_manager import connect
b, ctx = connect()
p = ctx.new_page()
p.goto("https://workday.wd5.myworkdayjobs.com/...")
time.sleep(10)
# Check for shadow DOM
has_shadow = p.evaluate("""() => {
    const all = document.querySelectorAll('*');
    for (const el of all) {
        if (el.shadowRoot && el.shadowRoot.querySelector('input, select, textarea')) return true;
    }
    return false;
}""")
print(f"Shadow DOM with fields: {has_shadow}")
```

## 23. Multi-window ATS (some platforms open a new popup window)

**Experiment:** Some ATS platforms (e.g., Lever) open the application form in a popup window instead of a new tab.
- `navigate.py` looks for new tabs/pages with `ctx.pages`. Popup windows ARE in `ctx.pages`.
- But if the popup is blocked by the browser, `navigate.py` would find nothing.

**Inspect:** Does `navigate.py` handle popup blockers? It doesn't explicitly — it just scans `ctx.pages` for non-LinkedIn URLs.

## 24. No "Back" button support on some forms

Some LinkedIn Easy Apply modals don't have a "Back" button on the first page. The `act --back` command tries to click "Back" and finds nothing.

**Experiment:** Run `act --back` on the first page of any Easy Apply.
- Should print "NO_BUTTON" — but the model doesn't know it's on the first page.

## 25. File input with `capture` attribute (mobile-style forms)

Some ATS forms use `<input type="file" capture="environment">` for mobile resume upload.
- Does `set_input_files` work on these? Playwright's `set_input_files` should work regardless of `capture`.
- Test: find a form with `capture` attribute on file input.

## 26. Inline validation errors (form shows errors but doesn't advance)

After `act --fill` and `act --next`, the form might stay on the same page with validation errors highlighted.

**Experiment:** Set a radio button answer that triggers a validation error (e.g., "Yes" to "Criminal record?").
- `act --next` → button might be disabled, correctly caught by disabled detection.
- But what if the button is ENABLED and clicking it just re-renders the same page with error messages?
- `cmd_next` reads the page AFTER clicking and compares it to the previous state. The field count might be the same.

**Inspect:** Add a check for error text patterns after clicking next but before reading the new page.

## 27. Confusing success messages

After `act --submit --confirm`, the code checks for "thank you", "submitted", "your application", "has been sent". But some ATS use different language:
- "Application received"
- "We've received your application"
- "Success!"
- "Your application has been submitted successfully"

**Experiment:** Test each variant and see if the code misses any.

## 28. The `--answers` format is unwieldy for long question texts

**Example:** `--answers '{"How many years of work experience do you have with Python (Programming Language)?": "5"}'`

**Solution:** The model could provide shorter keys that match via prefix. E.g., `"How many years": "5"`. The `_fill_text` function checks `lbl_norm.startswith(k_norm)` which handles this.

**Test:** Verify prefix matching works for a range of label lengths.

## 29. States

- **State: detect → navigate → ATS page loads but form is zero fields due to JS not yet rendered**
  - Test: Add `time.sleep` variation. What's the right timeout for heavy JS pages?
- **State: detect → external → navigate → ATS is behind a CAPTCHA**
  - Detect: page_type maybe "unknown" or "login_wall". No fix from pipeline — mark job as manual-only.
- **State: detect → external → navigate → "This job is no longer accepting applications"**
  - Field count 0, no form words, no sign-in. Model would see "unknown" and skip.
- **State: `act --fill` → fields filled correctly → `act --next` → page advances → BUT new page has same fingerprint as old** (same field count, same buttons)
  - The guard `current_fingerprint == last_fingerprint` would trigger a warning. Model needs to investigate.

## 30. Monitoring/Logging

- Track: how many jobs were attempted vs submitted vs failed
- Track: which ATS platforms were encountered and what the success rate was
- Track: common `--answers` keys used across jobs (to build common_answers automatically)
