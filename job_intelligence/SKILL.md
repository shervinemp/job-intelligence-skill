# Job Intelligence Pipeline

## Stages — what you do at each

| Stage | Your call |
|-------|-----------|
| **stage_emails.py** [--days N] — searches Gmail for job keywords | Auto — just let it run |
| **extract.py** — finds job URLs in staged emails | `admit --category <name> <jid>` (guess the category), or `reject <jid>` |
| **linkedin.py** [--url] [--max N] — scrapes LinkedIn saved jobs | `admit` / `reject` |
| **fetch.py** [--count N] — visits URL, scrapes description | `admit <jid> --category <name>`, `reject <jid>`, or `flag <jid>` (auth wall) |
| **tailor.py** [--count N] — generates tailored CV | `done <jid> [--pdf <path>]` to confirm + advance to tailored. Gem route: PDF auto-generated. Agent route: you write script.py based on the prompt. |
| **apply.py detect/act/verify <jid>** — submits application | Follow the apply pipeline |

## Tailoring

Two backends via `JI_TAILOR` env var:

- **`JI_TAILOR=gem`** (default): Uses Gemini Web gem. The gem generates `script.py`, pipeline runs it inline, PDF appears in results dir. Run `done <jid>` to confirm and advance stage to "tailored".
- **`JI_TAILOR=agent`**: The prompt is printed to you. You write `script.py` based on the instructions, run it to produce a PDF, then `done <jid> --pdf <path>` confirms the file exists and advances to "tailored".

Both routes end with `done <jid>` — gem route doesn't need `--pdf`, agent route should provide it. `done` advances the DB stage to "tailored" (CV ready). The apply pipeline later advances to "applied" (form submitted).

The prompt does not include the default resume — fill in CV content based on the candidate profile and job requirements.

## Commands reference

| Command | What to do |
|---------|-----------|
| `extract.py admit --category tech/general <jid> [--notes "..."]` | Accept with category |
| `extract.py reject <jid>` | Skip |
| `extract.py reset <jid>` | Delete job, re-extract |
| `extract.py review [--count N]` | Browse staged emails, manually submit |
| `extract.py submit <tid> '<json>'` | Manually add URLs with JSON body |
| `fetch.py admit/reject/flag <jid>` | Accept / skip / auth wall |
| `fetch.py retry` | Retry failed fetches |
| `fetch.py retry-skipped` | Reset skipped back to extracted |
| `fetch.py open [<jid>]` | Open in Chrome |
| `fetch.py --refresh` | Re-fetch described jobs |
| `tailor.py [--count N]` | Start tailoring (default 1, -1 = all) |
| `tailor.py --relentless --count -1` | Process all, idle on rate limit |
| `tailor.py done <jid> [--pdf <path>]` | Confirm PDF exists → stage = tailored |
| `tailor.py skip <jid>` | Mark as skipped |
| `tailor.py redo <jid>` | Re-tailor from scratch |
| `tailor.py retry` | Retry failed |
| `tailor.py reset --from failed,skipped` | Reset by stage |
| `tailor.py ready [<jid>]` | Open results folder |
| `extract.py reset` | Wipe entire DB, start fresh |
| `status` | Show pipeline state + next step |

## Apply pipeline

```
detect → [navigate] → act --fill → act --next (repeat) → act --submit --confirm → verify
```

**detect <jid>** — Pre-flight. Checks DB stage, PDF, classifies job type. Outputs `TYPE:` — easy_apply, external, ats_direct, already_applied, login_wall. Tells you next step.

**navigate <jid>** — LinkedIn External only. Clicks the external apply button, decodes LinkedIn's safety redirect, lands on the ATS.

**act --fill <jid> [--answers '{}']** — Fill ALL fields on the page. Uses `--answers` (exact match) → common_answers → profile. Auto-unchecks "Follow company" checkboxes. Handles radios, selects, file uploads, contenteditable divs, autocomplete widgets. Prints `STATUS: filled X/Y fields`.

**act --next <jid>** — Click forward. Button priority: Submit > Review > Next > Continue > Done. Never Back/Cancel/Save. Checks if button is disabled before clicking. Detects submission (success text → verify) and validation errors (→ retry fill).

**act --back <jid>** — Click Back.

**act --submit <jid> [--confirm]** — Click Submit on review page. **`--confirm` is required** — without it, dry-run only. Checks for validation errors, CAPTCHA, success text. Updates `_last_submit` outcome.

**act --inspect <jid> [--candidate N]** — Full diagnostic. Always captures screenshot (`IMG:`) and DOM dump (`HTML:`). Shows all fields, buttons, probe strategies, dialog state, page text. Use when stuck.

**verify <jid>** — Check submission. Scans ALL open pages for "thank you" / "submitted" text. Updates DB stage to "applied" if confirmed.

### Apply tips

- `--answers` normalizes labels (case/punctuation insensitive). Provide the full label text.
- `--candidate N` picks from the CANDIDATES list. Works on --fill, --next, --submit, --inspect.
- Multi-page: fill → next → fill → ... until Submit appears or verification passes.
- Guest apply: auto-clicks "continue without signing in" when found.
- Pipeline cannot create accounts, remember passwords, or handle 2FA.
- 3x guard: same page 3 fills in a row → warns you to break the loop.

## Platform quirks

| Platform | Things to know |
|----------|---------------|
| Ashby | One-page standard HTML. Recaptcha textarea at bottom — ignore. Radios grouped by `name`. |
| Greenhouse | No `<select>` — all dropdowns are custom autocomplete. Country is `<input>` not `<select>`. "Submit application" is the real button (not "Apply", which is a scroll trigger). |
| Lever | Job page has 0 fields → follow "apply for this job" link → `/apply` URL. Labels use `<label><div>Text</div><div><input></div></label>`. No Select/Radio. |
| Workday | 7-step SPA: Info → Experience → Questions → Disclosures → Review. DROPDOWN=`button[aria-haspopup]`. Autocomplete: JS value setter + events. Phone: strip +1 prefix. Skills: type then Enter. Per-company login — each company needs its own account. |
| LinkedIn | Easy Apply (modal, Next/Review/Submit steps) vs External (`<a>` tag with "on company website", safety redirect URL). detect.py checks both `<a>` and `<button>`. |

## Account & login notes

- **Guest apply** is preferred — pipeline auto-clicks "continue without signing in" when available.
- **Repeat portals** (e.g., a second Workday): guest apply works but creates a new account per company. No credential reuse.
- **Hands-free**: pipeline cannot create accounts, remember passwords, or handle 2FA. Login walls need manual intervention.

## Output signals

| Signal | When | Meaning |
|--------|------|---------|
| `STATUS:` | Any step | Status — filled count, captcha, guest_available, submitted, etc |
| `TYPE:` | detect | Job type: easy_apply / ats_direct / external / already_applied / login_wall / unknown |
| `NEXT:` | Any step | Recommended next command |
| `QUIRKS:` | detect or fill | Platform notes from YAML config — once per session |
| `GUEST_AVAILABLE:` | detect | Guest button found — will auto-click on fill |
| `IMG:` | inspect | Screenshot path. Read for visual context if your model supports images |
| `HTML:` | inspect | Full DOM dump path. Last-resort debug |

## Extraction rules

| Value | Include? |
|-------|----------|
| Ontario-based (Toronto, Ottawa, Oakville, Mississauga, Waterloo, etc.) | Yes — preferred |
| Other Canada-based (Vancouver, Calgary, etc.; on-site or hybrid) | Yes |
| Remote / work-from-home (any country) | Yes |
| Quebec in-office | No |
| US on-site only | No |
| Unclear location | Fetch description, then decide |

## Output directory

`~/.ji/results/{jid}/`:
- `gemini_response.txt` — Gemini output (gem route only)
- `script.py` — PDF build script
- `*.pdf` — CV / cover letter
- `{jid}.url` — job shortcut (Windows)

## Auth walls

Detected during fetch (sign-in keywords on page).  
`fetch.py flag <jid>` — manual flag.  
`fetch.py open [<jid>]` — open in Chrome (persistent session).  
Stale entries auto-pruned.

## Recovery

| Problem | Fix |
|---------|-----|
| `invalid_grant` | `gmail-cli auth add email` |
| TIMEOUT / RATE_LIMIT | `tailor.py retry` |
| Chrome crash | Auto-restarted by pipeline — do nothing |
| DB crash | `extract.py reset` |
| Auth wall stuck | `fetch.py open` + `fetch.py --refresh` |
