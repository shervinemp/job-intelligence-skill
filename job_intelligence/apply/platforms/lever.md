# Lever

**Detection:** `lever.co` in URL → platform "lever"

**Form structure:** Job listing page (0 fields) + "apply for this job" link → `/apply` URL with 12 fields. Single-page form with "Submit application" button.

**Known quirks:**
- Job listing shows 0 fields, `pageType: maybe_form`. apply-link-following auto-detects and follows.
- Always an `<a>` tag with text "apply for this job" → href ends with `/apply`.
- Some LinkedIn external URLs already include `/apply` in the href — apply-link handles both.
- Labels use `<label><div class="application-label">...</div><div class="application-field"><input></div></label>` structure — detected via `el.closest('label')`.
- Fields: resume, name, email, phone, location, company, LinkedIn, Twitter, GitHub, portfolio, etc.
- No Select/Radio elements.

**Pipeline flow:** `navigate → act --fill (auto apply-link) → act --submit`
