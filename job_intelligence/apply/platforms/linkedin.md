# LinkedIn

**Detection:** `linkedin.com/jobs/view` in URL

**Two modes:**

### Easy Apply
- Job has "Easy Apply" button → `detect` navigates to `?openSDUIApplyFlow=true` URL
- Modal opens with form fields. Fill via standard act --fill.
- "Next" button advances through pages, "Review" shows summary, "Submit application" submits.
- Sometimes has "Apply on company website" INSIDE the Easy Apply modal — detect.py checks for this first.

### External (Apply on company website)
- Job has "Apply on company website" link (an `<a>` tag, not a `<button>`) → detect shows "TYPE: external"
- `detect.py`: checks `<a>` elements for aria-label containing "company website"
- Follow with `navigate <jid>` which clicks the button or follows the safety redirect

**Known quirks:**
- External button is an `<a>`, not a `<button>` — detect.py checks both
- Safety redirect URL: `linkedin.com/safety/go/?url=<encoded_url>` — navigate.py decodes it
- Applied status detected via button text "Applied" or body text "you have applied"
- Title may be duplicated in DOM ("Senior Engineer Senior Engineer with verification") — dedup logic handles this
- "…more" button for description: `[data-testid="expandable-text-button"]` — pre_fetch clicks all instances
- Easy Apply modal: LinkedIn nav fields (search, language, recaptcha) are also detected — harmless, skipped during fill

**Pipeline flow (Easy Apply):** `detect → act --fill → act --next × N → act --submit --confirm`
**Pipeline flow (External):** `detect → navigate → act --fill → act --submit`
