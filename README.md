<p align="center">
  <img src="https://img.shields.io/badge/python-3.12+-blue?style=flat&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/node-20+-green?style=flat&logo=node.js&logoColor=white" alt="Node">
  <img src="https://img.shields.io/badge/chrome-required-orange?style=flat&logo=google-chrome&logoColor=white" alt="Chrome">
  <img src="https://img.shields.io/badge/license-MIT-lightgrey?style=flat" alt="License">
</p>

# Job Intelligence Pipeline

Automated job discovery, description fetching, CV tailoring, and auto-apply — orchestrated by an SLM.

---

## Pipeline Flow

```
                          ┌──────────────────┐
                          │   Gmail Search   │
                          └────────┬─────────┘
                                   │
                          ┌────────▼─────────┐  ┌──────────────────┐
                          │ stage_emails.py  │  │  linkedin.py     │
                          └────────┬─────────┘  └────────┬─────────┘
                                   │                     │
                                   └──────────┬──────────┘
                                              │
                                     ┌────────▼─────────┐
                                     │    extract.py    │
                                     │  admit / reject  │
                                     └────────┬─────────┘
                                              │
                                     ┌────────▼─────────┐
                                     │    enrich.py      │
                                     │ admit/reject/flag│
                                     └────────┬─────────┘
                                              │
                                     ┌────────▼─────────┐
                                     │   tailor.py      │
                                     │   done / skip    │
                                     └────────┬─────────┘
                                              │
                                     ┌────────▼─────────┐
                                     │   apply detect   │
                                     │  classify type   │
                                     └────────┬─────────┘
                                              │
                             ┌────────────────┼────────────────┐
                             │                │                │
                      ┌──────▼───────┐   ┌────▼────┐   ┌───────▼───────┐
                      │  Easy Apply  │   │External │   │    Already    │
                      │   (modal)    │   │/ATS dir │   │    Applied    │
                      └──────┬───────┘   └────┬────┘   └───────┬───────┘
                             │                │                │
                     ┌───────▼───────┐  ┌─────▼─────┐          │
                     │  act --fill   │  │ navigate  │          │
                     │  act --next   │  │ act --fill│          │
                     │  act --submit │  │ act --next│          │
                     └───────┬───────┘  │ act --sub │          │
                             │          └─────┬─────┘          │
                             └────────────────┼────────────────┘
                                              │
                                      ┌───────▼────────┐
                                      │   verify.py    │
                                      │  confirm done  │
                                      └────────────────┘
```

---

## Project Structure

```
job_intelligence/
├── stage_emails.py       # Stage Gmail threads → DB
├── extract.py            # URL extraction + admit/reject
├── linkedin.py           # LinkedIn job scraper (alt entry)
├── enrich.py             # Job description fetch + field enrichment
├── tailor.py             # CV tailoring via Gemini
├── apply.py              # Unified apply entry point
├── report.py             # Pipeline data inspection
├── requirements.txt      # Python dependencies (pip install -r)
├── gems.json             # Gem alias → ID mapping
├── categories.json       # Category → gem mapping
├── profile.json          # User profile for auto-fill (gitignored)
├── decisions.md          # User preferences — edit for your situation
├── SKILL.md              # Full operations manual
├── .env.example          # Config template (copy to .env)
├── lib/
│   ├── config.py        # Centralised JI_HOME path configuration
│   ├── db.py             # SQLite backend
│   ├── chrome_manager.py # Shared Chrome CDP lifecycle
│   ├── auth_walls.py     # Auth wall tracking
│   ├── call_gemini.py    # gemini.js subprocess wrapper
│   ├── extract_pdf.py    # PDF extraction from Gemini output
│   ├── extract_structured.py # JSON-LD job posting extraction
│   ├── report.py         # Pipeline data inspection
│   └── platforms/        # Site-specific description cleaners
│       ├── linkedin.py   # LinkedIn: click "…more" + strip chrome
│       └── jobright.py   # Jobright: section-level DOM extraction
└── apply/
    ├── act.py             # Fill, next, back, submit actions
    ├── detect.py          # Job type classification (pre-flight)
    ├── navigate.py        # LinkedIn → External ATS navigation
    ├── verify.py          # Post-submit verification (4 strategies)
    ├── common/
    │   ├── output.py      # Standardized formatter (emit_next/status/type/...)
    │   ├── field_reader.py# Canonical DOM field reader (JS, crash-guarded)
    │   ├── inspect_lib.py # Reusable page capture + probe analysis
    │   ├── inspector.py   # 8-depth probe cascade + DOM snapshot
    │   ├── answer_matcher.py # Exact match + safe word-overlap fallback
    │   ├── learner.py     # ButtonIntentClassifier only
    │   ├── page_helpers.py# read_page, scan_actions, page finding
    │   ├── page_manager.py# Page registry (tag → domain → candidates)
    │   ├── platforms.py   # Platform detection + login wall patterns
    │   └── registry.py    # YAML config resolver (domain → RegistryConfig)
    └── registry/          # Platform YAML configs + notes (greenhouse, lever, workday, ashby)
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

### `skills/gemini-browser/` — Gemini CDP Automation

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
| 1 | Email staging | `stage_emails.py` | Auto |
| 2 | URL extraction | `extract.py` | `admit --category <name> <jid>` or `reject` |
| 3 | LinkedIn scrape | `linkedin.py` | admit/reject |
| 4 | Fetch description | `enrich.py` | `admit` / `reject` / `flag` |
| 5 | CV tailoring | `tailor.py` | `done` / `skip` / `redo` |
| 6 | Classify type | `apply.py detect <jid>` | Follow TYPE hint (easy_apply / external / applied) |
| 7 | Navigate ATS | `apply.py navigate <jid>` | Auto (finds external Apply button) |
| 8 | Fill form | `apply.py act --fill <jid>` | Provide `--answers` for unmatched fields |
| 9 | Advance page | `apply.py act --next <jid>` | Follow CANDIDATES or use `--candidate N` |
| 10 | Submit | `apply.py act --submit <jid> --confirm` | Confirm submission |
| 11 | Verify | `apply.py verify <jid>` | Auto |
| — | Data inspection | `report.py` | `stats`, `inspect`, `search`, `export` |

All scripts respond to `help` and `status` subcommands.

---

## Key Features

### Apply Pipeline

`detect` classifies job type (Easy Apply / External / Applied / ATS direct).  
`navigate` clicks "Apply on company website" on LinkedIn, decodes safety redirect, lands on ATS form.  
`act --fill` fills all fields from `--answers` → profile (exact + word-overlap). Supports INPUT, SELECT, TEXTAREA, radio grids, DROPDOWN, flatpickr dates, autocomplete, file uploads, and contenteditable.  
`act --next` advances through multi-page forms. Ambiguous buttons → CANDIDATES, pick with `--candidate N`.  
`act --submit` clicks Submit (dry-run w/o `--confirm`). Checks result: CAPTCHA, validation errors, AJAX submit.  
`verify` 4-strategy check (modal closed, success text, Applied button, DB stage). Grants platform trust on success.

**Output contract:** scan stderr for `NEXT:` — that's the next action. Always last, always alone.

### PageManager

Each page is tagged with a `data-job-id` attribute on `<body>`. `PageManager.find()` locates pages by tag → domain match → candidate list. When multiple pages match, the model picks with `--page N`.

### Categories

Jobs tagged with a category on first admit. Determines which Gemini gem handles CV tailoring.

```
extract.py admit --category tech <jid>
```

| Category | Gem | Use Case |
|----------|-----|----------|
| `tech` | Application Optimizer | SWE, data, infra |
| `general` | Default Gemini | All other roles |

Resolution: `categories.json` → `gems.json` → `gemini.js`.

### Notes

Attach human context via `extract.py submit`. Persists across all stages. Appended to Gemini prompt.

```
extract.py submit '{"url":"...","notes":"John can refer at Google"}'
```

### Auth Walls

Auto-detected during fetch. Flagged jobs can be opened in Chrome's persistent profile.

```
enrich.py flag <jid>
enrich.py open [<jid>]
```

### Per-Job Reset

```
extract.py reset <jid>    # single job
extract.py reset          # wipe everything
```

### Pipeline Reports

```
report.py stats           # pipeline statistics
report.py inspect <jid>   # full job details
report.py search "Google" # search jobs
report.py export json     # export all jobs
report.py archive          # archive state/registry entries for reset jobs
```

---

## Configuration

All pipeline config in `job_intelligence/`. Copy `.env.example` → `.env`.

| File / Env Var | Purpose |
|----------------|---------|
| `.env` | Pipeline env vars (`JI_HOME`, `JI_TAILOR`, `GMAIL_SEARCH_QUERY`). Auto-loaded by `lib/config.py` |
| `JI_HOME` | Root data directory (`~/.ji/` by default). Holds state DB, results, snapshots, Chrome profile |
| `JI_TAILOR` | CV backend: `"agent"` (SLM writes script.py, default) or `"gem"` (Gemini Web gem) |
| `categories.json` | Category → gem alias mapping (e.g. `tech` → `optimizer_tech`) |
| `gems.json` | Gem alias → raw Gemini ID (e.g. `optimizer_tech` → `4203d06f5d81`) |
| `profile.json` | User profile for auto-apply. Must be filled in before first apply. Not tracked in git |
| `decisions.md` | Screening question decision rules — edit to match your preferences (relocation, sponsorship, experience, etc.) |

---

## Setup

### Prerequisites

| Dependency | Version | Notes |
|------------|---------|-------|
| Python | 3.12+ | Core runtime |
| Node.js | 20+ | Gemini browser automation |
| Google Chrome | Latest | CDP target for Playwright + Puppeteer |
| Google Cloud Project | — | Enable Gmail API, download `client_secret.json` |
| Playwright (Python) | — | `pip install -r requirements.txt` |
| puppeteer-core (Node) | — | `npm install -g puppeteer-core` |

### Step-by-Step

**1. Gmail API setup**

Create Google Cloud Project → enable Gmail API → create OAuth 2.0 Desktop credentials → download `client_secret.json` to repo root (`ji-skill/`).

**2. Install dependencies**

```powershell
pip install -r job_intelligence\requirements.txt
npm install -g puppeteer-core
```

**3. Configure environment**

```powershell
cd job_intelligence
copy .env.example .env
# Edit .env if you need custom JI_HOME, JI_TAILOR, or GMAIL_SEARCH_QUERY
```

**4. Start Chrome with persistent profile**

```powershell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" `
    --user-data-dir="$env:USERPROFILE\.ji\chrome-profile" `
    --remote-debugging-port=9222 --no-first-run
```

**5. Authenticate Gmail**

```powershell
gmail-cli auth credentials client_secret.json
gmail-cli auth add you@gmail.com
```

**6. Fill in your profile**

Edit `profile.json`: name, contact info, work history, common answers. Required before first apply.

### Configuration files

| File | Location | What goes in it | Required? |
|------|----------|-----------------|-----------|
| `.env` | `job_intelligence/` | `JI_HOME` (default `~/.ji/`), `JI_TAILOR` (`"agent"` or `"gem"`), `GMAIL_SEARCH_QUERY` | No (sensible defaults) |
| `profile.json` | `job_intelligence/` | Name, email, phone, work history, education, skills, `resume_path` (path to PDF), `common_answers` (form fill answers) | Yes |
| `client_secret.json` | `ji-skill/` root | OAuth 2.0 Desktop credentials from Google Cloud Console (Gmail API) | Yes, for email staging |
| `gems.json` | `job_intelligence/` | Gemini gem alias → raw ID mapping. Created by `call_gemini.py --refresh` | Only if using `JI_TAILOR=gem` |
| `.env` vars | `LLM_API_URL`, `LLM_API_MODEL` | OpenAI-compatible endpoint for `lib/ask_api.py` (llama.cpp, etc.) | No |

### Quick Start

```powershell
python stage_emails.py   # Stage Gmail threads → DB
python extract.py        # SLM admits/rejects URLs
python enrich.py          # SLM admits/rejects descriptions
python tailor.py         # SLM reviews CV, runs done/skip
```

---

## Output

Tailored CVs and application files are written to `~/.ji/results/{jid}/`:

```
~/.ji/results/{jid}/
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
| Chrome crash | Process died | Restart Chrome (see Setup step 4) |
| DB corruption | Bad reset / crash | `python3 extract.py reset` |
| Auth wall stuck | Blocked page | `enrich.py open` then `enrich.py --refresh` |
---



<p align="center">
  <b>Detailed operations manual</b>: <code>job_intelligence/SKILL.md</code>
</p>
