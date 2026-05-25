<p align="center">
  <img src="https://img.shields.io/badge/python-3.12+-blue?style=flat&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/node-20+-green?style=flat&logo=node.js&logoColor=white" alt="Node">
  <img src="https://img.shields.io/badge/chrome-required-orange?style=flat&logo=google-chrome&logoColor=white" alt="Chrome">
  <img src="https://img.shields.io/badge/license-MIT-lightgrey?style=flat" alt="License">
</p>

# Job Intelligence Pipeline

Automated job discovery, description fetching, and CV tailoring — orchestrated by an SLM.

---

## Pipeline Flow

```
                          ┌──────────────────┐
                          │   Gmail Search   │
                          └────────┬─────────┘
                                   │
                          ┌────────▼─────────┐
                          │ stage_emails.py  │
                          └────────┬─────────┘
                                   │
                          ┌────────▼─────────┐   ┌──────────────────┐
                          │   extract.py     │   │  linkedin.py     │
                          └────────┬─────────┘   └────────┬─────────┘
                                   │                     │
                                   └──────────┬──────────┘
                                              │
                                     ┌────────▼─────────┐
                                     │  admit / reject  │
                                     └────────┬─────────┘
                                              │
                                     ┌────────▼─────────┐
                                     │    fetch.py      │
                                     └────────┬─────────┘
                                              │
                                     ┌────────▼─────────┐
                                     │ admit/reject/flag│
                                     └────────┬─────────┘
                                              │
                                     ┌────────▼─────────┐
                                     │   tailor.py      │
                                     └────────┬─────────┘
                                              │
                                     ┌────────▼─────────┐
                                     │   done / skip    │
                                     └──────────────────┘
```

---

## Project Structure

```
skills/
├── README.md
├── gmail-cli/
│   └── gmail_cli.py          # Gmail API client
├── gemini-browser/
│   ├── gemini.js             # CDP-based Gemini automation
│   └── gems.json             # Gem alias → ID mapping
└── job_intelligence/
    ├── stage_emails.py       # Stage emails from Gmail
    ├── extract.py            # URL extraction + admit/reject
    ├── linkedin.py           # LinkedIn job scraping
    ├── fetch.py              # Job description fetching
    ├── tailor.py             # CV tailoring via Gemini
    ├── apply.py              # Auto-apply via Gemini
    ├── report.py             # Pipeline data inspection
    ├── categories.json       # Category → gem mapping
    ├── lib/
    │   ├── db.py             # SQLite backend
    │   ├── chrome_manager.py # Shared Chrome lifecycle
    │   ├── auth_walls.py     # Auth wall tracking
    │   ├── call_gemini.py    # gemini.js subprocess wrapper
    │   └── extract_pdf.py    # PDF extraction from Gemini output
    └── SKILL.md              # Detailed operations manual
```

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
| 4 | Fetch description | `fetch.py` | `admit`, `reject`, or `flag` (auth wall) |
| 5 | CV tailoring | `tailor.py` | `done`, `skip`, `redo`, `retry` |
| — | Auto-apply | `apply.py` | `auto <jid>`, `batch [--count N]` |
| — | Data inspection | `report.py` | `stats`, `inspect`, `search`, `export`, `summary` |

All stage scripts respond to `help` and `status` subcommands.

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
python3 fetch.py flag <jid>       # manual flag
python3 fetch.py open [<jid>]     # open in Chrome
```

Stale entries are auto-pruned when the job's stage progresses past `failed` or `extracted`.

### Per-Job Reset

Reset a single job to re-extract it from its source email. The source thread gets re-scanned on the next `extract.py` run.

```
python3 extract.py reset <jid>    # re-extract one job
python3 extract.py reset          # wipe everything, start fresh
```

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

## Configuration

| File | Purpose |
|------|---------|
| `.env` | Gmail search query override |
| `categories.json` | Category → gem alias mapping |
| `gems.json` | Gem alias → raw Gemini ID |
| `profile.json` | User profile for auto-apply (local only, not tracked) |

---

## Quick Start

```powershell
# 1. Start Chrome with persistent profile
& "C:\Program Files\Google\Chrome\Application\chrome.exe" `
    --user-data-dir="$env:USERPROFILE\.openclaw\chrome-profile" `
    --remote-debugging-port=9222 --no-first-run

# 2. Authenticate Gmail
gmail-cli auth credentials client_secret.json
gmail-cli auth add you@gmail.com

# 3. Run the pipeline
python3 stage_emails.py
python3 extract.py
# → SLM admits/rejects URLs
python3 fetch.py
# → SLM admits/rejects descriptions
python3 tailor.py
# → SLM reviews CV, runs done/skip
```

---

## Output

Tailored CVs and application files are written to `~/.openclaw/results/{jid}/`:

```
📁 ~/.openclaw/results/{jid}/
├── gemini_response.txt    # Full Gemini output
├── script.py              # Extracted Python script for PDF
├── {jid}.url              # Browser shortcut to job posting
└── *.pdf                  # Generated CV / cover letter
```

---

## Recovery

| Symptom | Cause | Fix |
|---------|-------|-----|
| `invalid_grant` | Stale OAuth token | `gmail-cli auth add <email>` |
| `TIMEOUT` / `RATE_LIMIT` | Gemini throttling | `python3 tailor.py retry` |
| Chrome crash | Process died | Restart Chrome (see Quick Start) |
| DB corruption | Bad reset / crash | `python3 extract.py reset` |
| Auth wall stuck | Blocked page | `fetch.py open` then `fetch.py --refresh` |

---

## Requirements

| Dependency | Version | Notes |
|------------|---------|-------|
| Python | 3.12+ | Core runtime |
| Node.js | 20+ | Gemini automation |
| Google Chrome | Latest | CDP target for playwright + puppeteer |
| Playwright (Python) | — | `pip install playwright` |
| puppeteer-core (Node) | — | `npm install -g puppeteer-core` |
| Google Cloud Project | — | Enable Gmail API |
| Gmail API credentials | — | `client_secret.json` from Google Cloud Console |

---

<p align="center">
  <b>Detailed operations manual</b>: <code>job_intelligence/SKILL.md</code>
</p>
