# Greenhouse

**Detection:** `greenhouse.io` in URL → platform "greenhouse"

**Form structure:** 28-29 fields pre-loaded in DOM. No "Apply" button click needed — form is always visible. Submit button is "Submit application" (type="submit").

**Known quirks:**
- Form is pre-loaded but fields are hidden until page renders. read_page detects them immediately.
- "Country" field is an `<input type="text">` autocomplete, not a `<select>`. Type the country name.
- No `<select>` elements on Greenhouse — all dropdowns are custom autocomplete inputs.
- "Apply" button at top of page is a scroll trigger — do NOT click it. Use "Submit application" at bottom.
- **Button priority:** "Submit application" > "Apply" (cmd_submit prioritizes correctly).

**Pipeline flow:** `navigate → act --fill → act --submit`
