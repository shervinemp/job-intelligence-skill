# Skyvern Migration

## Architecture

```
Pipeline (thin CLI) → Skyvern SDK → Skyvern Container (Docker) → Own Browser
```

Zero Playwright in the pipeline. No DOM strategies. No per-ATS handlers.

## Fixes Over Previous Design

### 1. LinkedIn: eliminate the hybrid hack

**Problem:** Keeping `chrome_manager.py` + `linkedin.py` just for one platform is messy. 5 files for one edge case.

**Fix:** Start Chrome with `--remote-debugging-address=0.0.0.0` so Skyvern's container can connect via CDP. Then Skyvern handles LinkedIn the same as everything else — it navigates to the URL, sees the Easy Apply modal, fills it.

The pipeline never needs Playwright. The LinkedIn handler is deleted.

```yaml
# docker-compose env enables CDP for LinkedIn-linked jobs when needed
# Pipeline: do NOT start Chrome for most jobs
# LinkedIn: pipeline starts Chrome with 0.0.0.0, passes cdp_url to Skyvern
```

### 2. Verification: post-fill extraction check

**Problem:** Blind trust in Skyvern's `completed` status. If the LLM hallucinates a fill, we submit garbage.

**Fix:** After fill completes, run a second Skyvern task with `data_extraction_schema` to read every field and compare against expected answers.

```python
# After fill task completes:
verify_task = await sk.run_task(
    prompt="Read every visible form field and return its current value.",
    url=url,
    browser_session_id=session_id,
    data_extraction_schema={
        "type": "object",
        "properties": {label: {"type": "string"} for label in answers},
    },
    max_steps=5,
    wait_for_completion=True,
)
```

If extracted values don't match expected, print mismatches. The fill still proceeds (silence is acceptable) but diagnostics are available.

### 3. Debugging: use Skyvern's own diagnostic surface

**Problem:** Old DIAG lines are gone. No per-field diagnostics.

**Fix:** Task result includes `screenshot_urls`, `errors`, `recording_url`. Print these instead:
```python
print(f"SKYVERN: task {run_id} — {result.status}")
print(f"  screenshots: {result.screenshot_urls}")
print(f"  errors: {result.errors}")
print(f"  UI: http://localhost:8080/runs/{run_id}")
```

No need to reimplement DIAG. Skyvern's own UI shows every action step-by-step.

### 4. Resume upload: pre-check + clear prompt

**Problem:** Volume mount path might be wrong or file missing.

**Fix:** Check file exists before calling Skyvern. Only reference files that exist in the prompt.

```python
resume_path = os.path.join(RESULTS_DIR, jid, f"*Resume*.pdf")
cover_path = os.path.join(RESULTS_DIR, jid, f"*Cover*.pdf")
exists = glob.glob(resume_path)  # True/False

prompt = "..."
if exists:
    prompt += f"\nUpload resume from /ji-results/{jid}/Resume*.pdf to the Resume/CV file input."
```

### 5. API key: explicit, not docker exec

**Problem:** `docker exec` to read key from container is fragile. New container = new key.

**Fix:** Set `SKYVERN_API_KEY` explicitly in docker-compose. The pipeline uses the same known key. No `docker exec`, no caching, no parsing.

```yaml
environment:
  SKYVERN_API_KEY: my-fixed-dev-key
```

Pipeline uses: `Skyvern(base_url="http://localhost:8000", api_key="my-fixed-dev-key")`

### 6. State file: remove legacy cruft

**Problem:** `page_fingerprint`, `_page`, `filled`, `_detect_fields`, `result` fields in `apply_state.json` are legacy artifacts.

**Fix:** New state schema:
```python
{
    "jid": str,
    "external_url": str,
    "platform": str,
    "browser_session_id": str,   # from Skyvern fill task
    "fill_run_id": str,          # fill task ID for recovery
    "answers_count": int,        # number of fields sent to Skyvern
}
```

### 7. Session cleanup

**Problem:** Skyvern leaves browser sessions running.

**Fix:** Call `skyvern.close_browser_session(session_id)` in `cmd_submit` after successful submission. Or rely on Skyvern's idle timeout.

### 8. SDK lazy import

**Problem:** `from skyvern import Skyvern` adds 2-3s per CLI invocation (detect, navigate, fill, submit).

**Fix:** Import inside the function, not at module level. Only paid when the function runs.

### 9. State loss recovery

**Problem:** If `apply_state.json` is lost between fill and submit, `browser_session_id` is gone. Submit can't find the filled form.

**Fix:** Save both `browser_session_id` and `fill_run_id` in state. If `browser_session_id` is missing on submit, look up the fill task by `run_id` via `GET /v1/runs/{run_id}` to recover the session.

### 10. Docker compose ports

**Problem:** Container restarts lose data and API key. Absolute paths break on other machines.

**Fix:**
- PostgreSQL volume for persistent data
- Relative paths for `env_file` using `$JI_HOME`
- `SKYVERN_API_KEY` set explicitly, not auto-generated

### 11. Profile path

**Problem:** Hardcoded relative path in `act.py` for `profile.json`.

**Fix:** Use `lib.config.PROFILE_PATH` (already defined).

### 12. max_steps configurable

Both fill and submit need configurable `max_steps` via env var or defaults.

## Final File Map

### Deleted (34 files)

| Path | Reason |
|------|--------|
| `apply/strategies/*` (7) | Skyvern fills |
| `apply/handlers/*` (5) | Skyvern handles all ATS |
| `apply/common/filler.py` | Skyvern fills |
| `apply/common/value_reader.py` | Skyvern reads |
| `apply/common/field_reader.py` | Skyvern detects |
| `apply/common/handler_base.py` | No abstract handler |
| `apply/common/inspector.py` | No DOM inspection |
| `apply/common/inspect_lib.py` | No DOM inspection |
| `apply/common/agent.js` | No injected script |
| `apply/common/agent_bridge.py` | No agent bridge |
| `apply/common/learner.py` | No field learning |
| `apply/common/mappings.py` | No field mapping |
| `apply/steps/*` (4) | No probe cascade |
| `lib/ask_api.py` | Skyvern calls LLM |
| `apply/common/page_manager.py` | No pages to manage |

### Kept (18 files)

| File | Why |
|------|-----|
| `apply.py` | CLI |
| `apply/act.py` | Rewritten: cmd_fill + cmd_submit |
| `apply/verify.py` | Simplified: DB check |
| `apply/detect.py` | Unchanged |
| `apply/navigate.py` | Unchanged |
| `apply/common/skyvern_bridge.py` | NEW |
| `apply/common/output.py` | Status output |
| `apply/common/resolve.py` | Answer resolution |
| `apply/common/gate.py` | Submit decision |
| `apply/common/audit.py` | Audit log |
| `apply/common/registry.py` | URL→platform |
| `apply/common/platforms.py` | Login wall detection |
| `lib/config.py` | Path config |
| `lib/db.py` | Database |
| `lib/build_resume.py` | Resume builder |
| `lib/chrome_manager.py` | Only for tailor.py (Gemini) |
| `lib/ask_api.py` | DELETED |

### Stripped of Playwright (3 files)

| File | What's left |
|------|-------------|
| `apply/act.py` | `cmd_fill`, `cmd_submit` only |
| `apply/verify.py` | DB stage check only |
| `apply/common/page_helpers.py` | `load_state`, `save_state` only |

## Pipeline Flow (Final v3)

```
detect <jid>
  → read DB → classify → print TYPE + URL + NEXT
  → 0 Playwright imports

navigate <jid>  
  → store external_url in state → detect platform from URL via registry
  → print PLATFORM + NEXT
  → 0 Playwright imports

act --fill <jid> [--answers JSON]
  → parse --answers, load profile, merge via resolve.py
  → if platform == "linkedin":
      start Chrome with 0.0.0.0 CDP, pass to Skyvern with browser_address
  → else:
      call Skyvern fill task (no Chrome)
  → save browser_session_id + run_id in state
  → print STATUS: filled + NEXT: act --submit

act --submit <jid>
  → load browser_session_id from state
  → if missing: recover from fill_run_id via GET /v1/runs/{run_id}
  → call Skyvern submit task with same session
  → if completed: UPDATE stage='applied'
  → close browser session
  → print STATUS: submitted + NEXT: verify

verify <jid>
  → check DB stage
  → print SUBMITTED or NOT_SUBMITTED
  → 0 Playwright imports
```

## Docker Compose (Final)

```yaml
services:
  skyvern-db:
    image: postgres:16-alpine
    volumes:
      - pgdata:/var/lib/postgresql/data

  skyvern:
    image: skyvern/skyvern:latest
    ports: ["8000:8000"]
    env_file: C:\path\to\.env
    environment:
      DATABASE_STRING: postgresql+psycopg://skyvern:skyvern@skyvern-db:5432/skyvern
      OPENAI_API_KEY: sk-dummy
      OPENAI_API_BASE: http://host.docker.internal:9000/v1
      SKYVERN_API_KEY: my-fixed-dev-key
      SKYVERN_TELEMETRY: "false"
    volumes:
      - C:\Users\sherv\.ji\results:/ji-results
    depends_on:
      skyvern-db:
        condition: service_healthy

volumes:
  pgdata:
```

## Prompts (Final)

### Fill

```
You are filling out a job application form at {url}.

Fields to fill (use ONLY these values, do not make up answers):
{formatted_answers}

{resume_instruction}
{platform_hints}

Instructions:
1. Fill EVERY field listed above.
2. For dropdowns/comboboxes, click to open and select the matching option.
3. If no matching option exists, type the value directly.
4. Check required consent/checkbox fields.
5. If there is a Next/Continue button, click it and fill the next page too.
6. STOP before clicking Submit Application or Submit. Do NOT submit.
```

### Submit

```
Click the Submit Application or Submit button on this job application form.
If there is a Review step before Submit, click Review first, then Submit.
Complete the submission process. Do NOT fill any new fields.
```

## Open Issues Still Present

1. **Verification adds 30-60s per job.** Acceptable tradeoff for reliability.
2. **First task is slow** (30s browser init + LLM warmup). Consecutive tasks are faster.
3. **No rollback within a pipeline run.** If Skyvern fills wrong, the pipeline can't undo. User must discard and retry.
4. **LinkedIn CDP hack** — Chrome with `0.0.0.0` is a security risk on shared networks. Acceptable for a local dev machine.
5. **State file single-point-of-failure.** If `apply_state.json` is lost mid-pipeline, recovery needs the `run_id` fallback.
