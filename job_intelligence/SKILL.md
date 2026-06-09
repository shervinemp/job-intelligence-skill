# Job Intelligence Pipeline

> All commands prefixed with `python3` on Windows.

## Flow

```
stage_emails → extract (admit/reject) → fetch (admit/reject/flag) → tailor (done/skip/redo) → apply
```

| Step | Your call |
|------|-----------|
| `stage_emails.py [--days N]` | Auto — skip non-job keywords |
| `extract.py` | `admit --category <name> <jid>` / reject |
| `linkedin.py [--url <url>] [--max N]` | admit/reject |
| `fetch.py [--count N]` | admit/reject/flag |
| `tailor.py [--count N]` | done/skip/redo |
| `apply.py detect/act n --inspect/verify <jid>` | See below |

JI_TAILOR=gem (default) = Gemini Web gem. JI_TAILOR=agent = you write script.py, done <jid> checks PDF exists.

## Commands quick ref

| Command | What you do |
|---------|-------------|
| `extract.py admit --category tech/general <jid>` | Admit with category |
| `extract.py reset <jid>` | Wipe job, re-extract |
| `extract.py review [--count N]` | Browse staged emails |
| `fetch.py admit/reject/flag <jid>` | Mark job |
| `fetch.py retry` | Retry failed fetches |
| `fetch.py open [<jid>]` | Open in Chrome |
| `tailor.py done/skip/redo <jid>` | After tailoring |
| `tailor.py retry` | Retry failed |
| `tailor.py reset --from failed,skipped` | Reset to described |
| `tailor.py ready [<jid>]` | Open files folder |
| `extract.py reset` | Wipe DB, fresh start |
| `status` | Next step hint |

## Apply pipeline

```
detect <jid> → navigate <jid> (if external) → act --fill/--next/--submit <jid> → verify <jid>
```

| Command | Does |
|---------|------|
| `detect <jid>` | Checks stage, PDF, classify type (easy_apply / external / ats_direct / already_applied / login_wall) |
| `navigate <jid>` | LinkedIn → external ATS (click button, decode redirect) |
| `act --fill <jid> [--answers '{}']` | Fill all fields |
| `act --next <jid>` | Click Next/Review/Submit |
| `act --back <jid>` | Click Back |
| `act --submit <jid> [--confirm]` | Submit (--confirm required) |
| `act --inspect <jid> [--candidate N]` | Screenshot + HTML + probe dump. Use when stuck |
| `verify <jid>` | Check if submitted (text match, DB stage) |

**Flags:** `--candidate N` picks from CANDIDATES list. `--inspect` outputs IMG: and HTML: paths.

### Tips

- `--answers` exact match → common_answers → profile. Provide full field labels.
- Button priority: Submit > Review > Next > Continue > Done. Never Back/Cancel/Save.
- Multi-page: fill → next → fill → ... until Submit appears.
- Guest apply auto-clicks "continue without signing in" when available.
- Pipeline cannot create accounts or handle 2FA.
- `inspect` screenshot at `~/.ji/screenshots/inspect_{jid}.png` (overwrites). Read if your model supports images.

## Platform quirks

| Platform | Watch out for |
|----------|--------------|
| Ashby | Recaptcha textarea at bottom — ignore. Radios by `name` attr. |
| Greenhouse | No `<select>` — all autocomplete. Country is `<input>`. "Submit application" bottom, not "Apply". |
| Lever | 0 fields → follow apply-link → `/apply`. Labels nest `<label><div>Text</div><div><input></div></label>`. |
| Workday | 7-step SPA. DROPDOWN=`button[aria-haspopup]`. Phone: strip +1. Skills: type+Enter. Per-company login. |
| LinkedIn | Easy Apply (modal) vs External (`<a>` tag, safety redirect). detect checks `<a>` not just `<button>`. |

## Output signals

| Signal | Meaning |
|--------|---------|
| `STATUS:` | filled count, captcha, guest_available, submitted, etc |
| `TYPE:` | easy_apply / ats_direct / external / already_applied / login_wall / unknown |
| `NEXT:` | What to run next |
| `QUIRKS:` | Platform notes from YAML — once per session |
| `GUEST_AVAILABLE:` | Guest button found — auto-clicked on fill |
| `IMG:` | Screenshot path. Read for visual context |
| `HTML:` | Full DOM dump path. Last-resort debug |

## Recovery

| Problem | Fix |
|---------|-----|
| `invalid_grant` | `gmail-cli auth add email` |
| TIMEOUT / RATE_LIMIT | `tailor.py retry` |
| Chrome crash | Auto-restarted — do nothing |
| DB crash | `extract.py reset` |
| Auth wall stuck | `fetch.py open` + `--refresh` |
