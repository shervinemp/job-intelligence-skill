<p align="center">
  <img src="https://img.shields.io/badge/python-3.12+-blue?style=flat&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/node-20+-green?style=flat&logo=node.js&logoColor=white" alt="Node">
  <img src="https://img.shields.io/badge/chrome-required-orange?style=flat&logo=google-chrome&logoColor=white" alt="Chrome">
  <img src="https://img.shields.io/badge/tests-151_pass-success?style=flat" alt="Tests">
  <img src="https://img.shields.io/badge/license-MIT-lightgrey?style=flat" alt="License">
</p>

# Job Intelligence Pipeline

Automated job discovery, description fetching, CV tailoring, and autonomous job application — orchestrated by an SLM with an adaptive probe system that learns each platform's form shape without per-platform code.

---

## Pipeline Flow

```
  [Gmail Search]
       |
       v
  [stage_emails.py]
       |
       v
  [extract.py]  [linkedin.py]
       |             |
       +------+------+
              |
              v
        [admit/reject]
              |
              v
         [enrich.py]
              |
              v
        [admit/reject/flag]
              |
              v
          [tailor.py]
               |
               v
           [admit/reject]
                |
                v
           [apply.py] (optional)
           detect → navigate → act → verify
```

---

## Project Structure

```
skills/
├── gmail-cli/                # Gmail API client
├── gemini-browser/           # Gemini CDP automation (gemini.js + gems.json)
└── job_intelligence/
    ├── stage_emails.py       # Stage emails from Gmail
    ├── extract.py            # URL extraction + admit/reject
    ├── linkedin.py           # LinkedIn job scraping
    ├── enrich.py             # Job description fetching + enrichment
    ├── tailor.py             # CV tailoring via Gemini
    ├── apply.py              # Apply pipeline (detect → navigate → act → verify)
    ├── apply/                # Hybrid fill engine, probe cascade, registry YAMLs
    ├── report.py             # Pipeline data inspection + candidates report
    ├── profile.json          # User profile for auto-apply (local, gitignored)
    ├── categories.json       # Category → gem mapping
    ├── decisions.md          # Job accept/reject rules per category
    ├── lib/                  # SQLite, Chrome lifecycle, credential vault, Gemini bridge
    ├── SKILL.md              # Detailed operations manual
    └── GUIDELINES.md         # High-density state file (per AGENTS.md)
```

`apply/` hosts the adaptive probe system: a capability scanner, an observation store, a 9-level probe cascade, a corpus of captured DOM snapshots, and a drift detector — all platform-agnostic (no per-platform Python). See `job_intelligence/SKILL.md` for the operations manual and `GUIDELINES.md` for maintainer reference.

---

## Components

### gmail-cli/ — Gmail API Client

Python CLI that replaces the compromised `gog` binary. Wraps Google's official Gmail API.

```
gmail-cli auth credentials client_secret.json
gmail-cli auth add you@gmail.com
gmail-cli gmail search "newer_than:7d" --all -j
gmail-cli gmail get <threadId>
```

### gemini-browser/ — Gemini CDP Automation

Navigates a real Chrome instance to Gemini Web, submits prompts to a specific gem, reads the response. Uses Gemini 3.5 Flash + Extended thinking — no Pro API key required.

```
node gemini.js "your prompt"
node gemini.js --gem optimizer "Write a cover letter"
node gemini.js --gems
```

Features:
- Two-pass rate-limit detection (modal + body text)
- Chat deletion via conversation ID
- Persistent sessions via shared Chrome profile
- Gem resolution through `gems.json`

### job_intelligence/ — Pipeline Core

| Stage | Script | Command | SLM Action |
|-------|--------|---------|------------|
| 1 | Email staging | `stage_emails.py` | Auto (filters by `job`/`jobs` keyword) |
| 2 | URL extraction | `extract.py` | `admit --category <name> <jid>` or `reject` |
| 3 | LinkedIn scrape | `linkedin.py` | Same admit/reject flow |
| 4 | Fetch description | `enrich.py` | `admit`, `reject`, or `flag` (auth wall) |
| 5 | CV tailoring | `tailor.py` | `admit`, `reject`, `undo`, `retry` |
| 6 | Auto-apply | `apply.py` | `detect`, `navigate`, `act --fill/--check/--submit/--inspect`, `verify`, `auto`, `act --investigate` |
| — | Data inspection | `report.py` | `stats`, `inspect`, `search`, `export`, `summary`, `candidates` |

For probe inspection (`registry`), credentials (`creds`), and drift self-test, see the operations manual.

All stage scripts respond to `help`. Pipeline state via `report.py stats`.

---

## Key Features

### Categories

Jobs are tagged with a category on first admit. The category determines which Gemini gem handles the CV tailoring.

```
python3 extract.py admit --category tech abc123def4567890
```

Available categories (defined in `categories.json`):

| Category | Gem | Use Case |
|----------|-----|----------|
| `tech` | Application Optimizer | SWE, data, infra roles |
| `general` | Default Gemini | All other roles |

Resolution chain: `categories.json` → `gems.json` → `gemini.js`.

### Notes

Attach human context to any job — referral mentions, priorities, deadlines. The notes field persists across all stage transitions and is appended to the Gemini prompt as supplementary context.

```
python3 extract.py submit '{"url":"https://...","notes":"John can refer at Google"}'
```

Clear with: `python3 extract.py submit '{"url":"https://...","notes":""}'`

### Auth Walls

Jobs behind sign-in pages are auto-detected during fetch. Flagged jobs can be opened in Chrome's persistent profile (where you're already logged in) to bypass.

```
python3 enrich.py flag <jid>       # manual flag
python3 enrich.py open [<jid>]     # open in Chrome
```

Stale entries are auto-pruned when the job's stage progresses or state changes to `rejected`.

### Per-Job Reset

Reset a single job to re-extract it from its source email. The source thread gets re-scanned on the next `extract.py` run.

```
python3 extract.py reset <jid>    # re-extract one job
python3 extract.py reset          # wipe everything, start fresh
```

## Quality Review

After tailoring, optionally review generated CVs before marking ready:

```
python3 tailor.py review [--jobs N]       # Show job + cover letter
python3 tailor.py retry <jid> --feedback "x"  # Re-tailor with feedback
```

Default batch: `tailor.py --count 1`. Feedback persists through `retry` loop.

### Pipeline Reports

Read-only inspection and export of all pipeline data.

```
python3 report.py stats           # pipeline statistics
python3 report.py inspect <jid>   # full job details
python3 report.py search "Google" # search jobs
python3 report.py export json     # export all jobs as JSON
python3 report.py summary         # recent activity digest
python3 report.py shell           # open SQLite shell
```

---

## State & Stage

Pipeline tracks two orthogonal dimensions per job:

- **Stage**: pipeline position — `extracted`, `described`, `tailored`, `applied`
- **State**: job condition — `active`, `rejected`, `failed`

A job can be at `tailored` stage with `rejected` state, or `described` with `active` state. Stage advances via `admit`. State changes via `reject`, `retry`, or failure.

---

## Configuration

| File | Purpose |
|------|---------|
| `.env` | Gmail search query override |
| `categories.json` | Category → gem alias mapping |
| `gems.json` | Gem alias → raw Gemini ID |
| `profile.json` | User profile for auto-apply (local only, not tracked) |
| `decisions.md` | Job accept/reject rules per category |

---

## Quick Start

```powershell
# 1. Authenticate Gmail (one-time setup)
gmail-cli auth credentials client_secret.json
gmail-cli auth add you@gmail.com

# 2. Run the pipeline (Chrome auto-started by each stage)
python3 stage_emails.py
python3 extract.py
# → SLM admits/rejects URLs
python3 enrich.py
# → SLM admits/rejects descriptions
python3 tailor.py
# → SLM runs admit or review/retry for quality

# 3. Auto-apply (optional, after tailoring)
#    Autonomous — process all tailored jobs (recommended)
python3 apply.py auto --dry-run              # preview
python3 apply.py auto --limit 5 --quick     # deterministic-only
python3 apply.py auto --no-submit           # fill only, review before submitting

#    Step-by-step — one job at a time (manual control)
python3 apply.py detect <jid>
python3 apply.py act --fill <jid>
python3 apply.py act --check <jid>
python3 apply.py act --submit <jid> --confirm
python3 apply.py verify <jid>
```

---

## Output

Tailored CVs and application files are written to `~/.ji/results/{jid}/`:

```
📁 ~/.ji/results/{jid}/
├── gemini_response.txt    # Full Gemini output
├── script.py              # Extracted Python script for PDF
├── resume.json            # JSON Resume format
├── {jid}.url              # Browser shortcut to job posting
└── *.pdf                  # Generated CV / cover letter
```

Pipeline state lives under `~/.ji/` (SQLite, captured DOM snapshots, learned observations, Chrome profile). See `SKILL.md` for the full layout.

---

## Recovery

| Symptom | Cause | Fix |
|---------|-------|-----|
| `invalid_grant` | Stale OAuth token | `gmail-cli auth add <email>` |
| `TIMEOUT` / `RATE_LIMIT` | Gemini throttling | `python3 tailor.py retry` |
| Chrome crash | Process died | Auto-restarted — do nothing |
| NO_MATCH | URL mismatch after redirect | Re-run `navigate` (saves actual page URL) |
| Unfilled fields | Label not in profile/common_answers | `act --fill --answers '{"label":"value"}'` |
| CAPTCHA | Security challenge | Solve in Chrome, press Enter to resume |
| DB corruption | Bad reset / crash | `python3 extract.py reset` |
| Auth wall stuck | Blocked page | `enrich.py open` then `enrich.py --refresh` |

The probe system auto-detects 2FA, session expiry, mid-fill popups, and unknown platforms — emitting `STATUS:` + `NEXT:` signals for the orchestrator. See `SKILL.md` for these edge cases.

---

## Requirements

| Dependency | Version | Notes |
|------------|---------|-------|
| Python | 3.12+ | Core runtime |
| Node.js | 20+ | Gemini automation |
| Google Chrome | Latest | CDP target for Playwright + Puppeteer |
| Playwright (Python) | — | `pip install playwright` |
| puppeteer-core (Node) | — | `npm install -g puppeteer-core` |
| Google Cloud Project | — | Enable Gmail API |
| Gmail API credentials | — | `client_secret.json` from Google Cloud Console |

---

<p align="center">
  <b>Detailed operations manual</b>: <code>job_intelligence/SKILL.md</code><br>
  <b>High-density state file</b>: <code>job_intelligence/GUIDELINES.md</code>
</p>