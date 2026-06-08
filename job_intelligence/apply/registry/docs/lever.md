# Lever

**URL:** `lever.co` → platform "lever"

**Flow:** navigate → fill (auto apply-link) → submit

**Form:** Job page (0 fields) → "apply for this job" link → `/apply` URL → 12 fields. Single page. "Submit application" btn.

**Gotchas:**
- 0 fields + `pageType=maybe_form` = apply-link-following auto-triggers
- Always `<a>` tag with text "apply for this job" → href ends with `/apply`
- Some LinkedIn URLs already include `/apply` in href — same handler works
- Labels: `<label><div>Text</div><div><input></div></label>` — `el.closest('label')` catches
- Fields: resume, name, email, phone, location, company, LinkedIn, Twitter, GitHub, etc.
- No Select/Radio elements
