# Ashby

**Detection:** `ashbyhq.com` in URL → platform "ashby"

**Form structure:** Standard HTML inputs with `<label for="id">`. 20-25 fields typical. One-page application with "Submit Application" button.

**Known quirks:**
- Radio groups use `name` attribute for grouping (standard)
- File uploads use `input[type="file"][required]` — auto-filled from resume PDF
- "Start typing..." field is a search input, not a form field — skip it
- Recaptcha textarea at bottom — ignore

**Pipeline flow:** `navigate → act --fill → act --submit`
