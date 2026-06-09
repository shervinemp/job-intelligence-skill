# Job Intelligence Pipeline

> Windows: prefix all commands with `python3`. Bare `extract.py` opens in VSCode.

## What you do at each stage

**Stage emails** — `stage_emails.py [--days N]`. Searches Gmail for job keywords. Auto-runs, you don't need to do anything.

**Extract** — `extract.py`. Finds job URLs in your emails. You decide:  
`admit --category tech <jid>` or `admit --category general <jid>` or just `reject <jid>`.

**LinkedIn** — `linkedin.py [--url <url>] [--max N]`. Scrapes LinkedIn saved jobs. Same admit/reject flow.

**Fetch** — `fetch.py [--count N]`. Visits each URL, scrapes the job description.  
You: `admit <jid> --category <name>`, `reject <jid>`, or `flag <jid>` (auth wall).

**Tailor** — `tailor.py [--count N]`. Generates a tailored CV. Two backends:  
- `JI_TAILOR=gem` (default): uses Gemini Web gem. Just run it.
- `JI_TAILOR=agent`: reads the prompt, you write `script.py` in the results dir, then `done <jid> --pdf <path>` checks your PDF exists and marks it applied.

**Apply** — See apply pipeline below.

## Commands

| Command | What it does |
|---------|-------------|
| `extract.py admit --category tech/general <jid>` | Accept a job with category |
| `extract.py reject <jid>` | Skip it |
| `extract.py review [--count N]` | Browse staged emails manually |
| `extract.py reset <jid>` | Delete and re-extract |
| `fetch.py admit/reject/flag <jid>` | Accept, skip, or flag as auth wall |
| `fetch.py retry` | Retry failed fetches |
| `fetch.py open [<jid>]` | Open in Chrome to check manually |
| `tailor.py [--count N]` | Start tailoring (default 1 job) |
| `tailor.py done <jid> [--pdf <path>]` | Mark as applied. If --pdf given, verifies file exists |
| `tailor.py skip <jid>` | Skip this job |
| `tailor.py redo <jid>` | Re-tailor from scratch |
| `tailor.py retry` | Retry failed tailoring |
| `tailor.py reset --from failed,skipped` | Reset failed/skipped back to described |
| `tailor.py ready [<jid>]` | Open the results folder |
| `extract.py reset` | Wipe the entire DB, start over |
| `status` | Shows current pipeline state and next step |

## Apply pipeline

The pipeline for actually submitting applications. Run these in order:

```
detect → [navigate] → act --fill → act --next (repeat) → act --submit → verify
```

**detect <jid>** — Pre-flight check. Opens the job page, classifies the type:  
`TYPE: easy_apply` (LinkedIn modal), `external` (goes to another site), `ats_direct` (form right there), `already_applied`, or `login_wall`.  
Tells you what to do next.

**navigate <jid>** — Only for LinkedIn External jobs. Clicks the "Apply on company website" button, decodes LinkedIn's safety redirect, lands on the actual ATS page.

**act --fill <jid> [--answers '{}']** — Fills every field it can find on the form. Uses --answers first, then common_answers, then your profile.  
Prints `STATUS: filled X/Y fields` and `NEXT:` telling you what to do next.

**act --next <jid>** — Clicks the forward button. Tries Submit > Review > Next > Continue > Done. Never clicks Back/Cancel/Save.  
Detects when the form has been submitted (success text) and tells you to verify.

**act --back <jid>** — Clicks the Back button.

**act --submit <jid> [--confirm]** — Clicks the Submit button. Requires `--confirm` to actually send (dry-run otherwise).  
Checks for validation errors, CAPTCHA, or success. Updates `_last_submit` with the outcome.

**act --inspect <jid> [--candidate N]** — Full page analysis. Always takes a screenshot (`IMG:` path) and dumps the DOM (`HTML:` path). Shows all fields, buttons, probe results, dialog state.  
Use this when you're stuck and need to see what the page looks like.

**verify <jid>** — Checks if the submission went through. Scans open pages for "thank you" / "submitted" text. Updates the DB stage to "applied" if confirmed.

### Tips

- **--answers**: JSON with exact field labels as keys. Normalized match (case/punctuation insensitive). Example: `--answers '{"Country":"Canada"}'`
- **--candidate N**: When you see a CANDIDATES list, pick one with this flag. Works on --fill, --next, --submit, --inspect.
- **Multi-page forms**: Run `--fill` then `--next` repeatedly. Each page may show new fields. Stop when Submit button appears.
- **Guest apply**: The pipeline auto-clicks "continue without signing in" when it finds one. 
- **Screenshots**: `act --inspect` saves to `~/.ji/screenshots/inspect_{jid}.png`. Read this file if your model supports images.
- **Pipeline can't**: create accounts, remember passwords, or handle 2FA. Login walls need manual help.
- **3x guard**: If the same page state repeats 3 fills in a row, the pipeline warns you to break the loop.

### Platform-specific things to watch for

| Platform | Note |
|----------|------|
| Ashby | Standard one-page HTML. Recaptcha textarea at the bottom — ignore it. |
| Greenhouse | No `<select>` elements — all dropdowns are custom autocomplete. Country is an `<input>` not a `<select>`. "Submit application" is the real button, not "Apply". |
| Lever | 0 fields on the job page → follow the "apply for this job" link → `/apply` URL has the form. Labels use `<label><div>Text</div><div><input></div></label>` structure. |
| Workday | 7-step SPA: Info → Experience → Questions → Disclosures → Review. Dropdowns use `button[aria-haspopup]`. Phone numbers: strip the +1 prefix when a separate country code field exists. Skills: type the skill then press Enter. Each company has its own login. |
| LinkedIn | Two modes: Easy Apply (modal with Next/Review/Submit) and External (`<a>` tag with "on company website", safety redirect URL). |

## What the pipeline tells you

| Signal | When | What it means |
|--------|------|---------------|
| `STATUS:` | Any step | Something happened — filled count, captcha detected, guest apply clicked, submitted, etc |
| `TYPE:` | detect | What kind of job page this is |
| `NEXT:` | Any step | What you should run next |
| `QUIRKS:` | detect or fill | Platform-specific notes from the YAML configs — printed once per platform per session |
| `GUEST_AVAILABLE:` | detect | Found a "continue without signing in" button — will auto-click on fill |
| `IMG:` | inspect | Path to a screenshot. Read this file if your model supports images |
| `HTML:` | inspect | Path to the full page DOM. Read for last-resort debugging |

## Output directory

`~/.ji/results/{jid}/`:
- `gemini_response.txt` — Raw Gemini output (gem route only)
- `script.py` — The PDF generation script
- `*.pdf` — The generated CV/cover letter
- `{jid}.url` — Windows shortcut to the job

## When things go wrong

| Problem | Try this |
|---------|----------|
| `invalid_grant` | `gmail-cli auth add email` — re-authenticate |
| TIMEOUT / RATE_LIMIT | `tailor.py retry` |
| Chrome crash | Auto-restarted by the pipeline — just wait |
| DB issue | `extract.py reset` — wipes and starts fresh |
| Auth wall | `fetch.py open` + `fetch.py --refresh` to re-fetch |
