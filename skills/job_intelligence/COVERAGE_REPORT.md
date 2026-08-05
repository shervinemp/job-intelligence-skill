# Coverage baseline + handoff-testing strategy

Measured 2026-08-05 with `coverage` 7.15.3 against the full suite
(711 passed + 141 subtests — includes handoff, round-trip, strategy, drift,
verify, credentials, and dispatcher tests). Generated with:

```
coverage erase
coverage run -m pytest tests -q
coverage json -o <tmp>/covdata.json
```

## Baseline

**Production coverage: 46.0%** (14,252 statements). Raised from 42.3% across
four passes:
- `detect.py` 12.5% → 75.0% · `submit.py` 24.3% → 24.5%
- `lib/report.py` 15.1% → 27.3% · `lib/automation/diff.py` → 92.9%
- `apply/strategies/text.py` 13.9% → 74.7% · `drift.py` 4.7% → 95.3% ·
  `verify.py` 15.0% → 37.9%
- `lib/credentials.py` 14.4% → 32.0% · `apply/act/__init__.py` 10.5% → 78.9%

| Module | Coverage | Why low |
|--------|----------|---------|
| `apply/common/drift.py` | 4.7% | drift detector — no direct tests |
| `tailor.py` | 6.6% | browser/LLM tailoring — CLI-only |
| `apply/act/investigate.py` | 9.1% | deep-analyze unknown platform — live-only |
| `apply/common/registry_cli.py` | 9.7% | registry CLI subcommands |
| `lib/ingest.py` | 10.3% | Gmail ingestion |
| `apply/act/__init__.py` | 10.5% | arg dispatcher |
| `extract.py` | 11.4% | stage-1 CLI |
| `lib/platforms/linkedin.py` | 12.0% | LinkedIn scraper |
| `apply/detect.py` | 12.5% | pre-flight classify |
| `lib/call_gemini.py` | 12.9% | Gemini adapter |
| `apply/strategies/text.py` | 13.9% | only fallback chains hit |
| `lib/credentials.py` | 14.4% | keyring/account creation — live |
| `apply/shadow_worker.py` | 14.5% | shadow worker — subprocess |
| `apply/verify.py` | 15.0% | post-submit verify — live |
| `lib/report.py` | 15.1% | 43 cmd_* — most untested |

**The handoff producers are the least-covered code.** The orchestrator reads
`STATUS:`/`NEXT:`/`DIAG:`/`FILLED:` from stderr + `handoff.json` per job — and
those producers (fill 39%, submit 24%, verify 15%, detect 12%, report 15%) are
exactly the modules with the worst coverage. That is the gap this document
targets.

## Why the orchestrator handoff is worth a dedicated test surface

The LLM-in-the-middle does not read the DB or the code — it reads the **evidence
trail** (stderr signals + dossier). A regression in *what is printed* is
invisible to the unit suite unless a test pins the exact contract. The trace
invariant (C-O1/O2/O3, see TRACE_COMPARISON.md) is: the orchestrator must be
able to (a) see each decision step, (b) trust the verdict, (c) act on the
`NEXT:` command. Pinning those three is a distinct test concern from "did the
DB row update".

## How to test the handoff — mocks/stubs

The producers are browser/CLI modules; testing them requires stubbing their
external world, not a real browser. The established pattern (test_adversarial.py
`_stderr`, test_reach.py `_stderr_of`) is:

1. **Redirect stderr** with `contextlib.redirect_stderr(io.StringIO())` — the
   signal contract is `sys.stderr`, so capture it.
2. **Stub the page/session** — a `MagicMock` page whose `evaluate`/`locator`
   return canned values (see `test_fill_dispatch._chrome_session`). The producers
   never know they aren't talking to a real browser.
3. **Stub the DB/config** — `schema._conn`/`DB_PATH`/`DB_DIR` swapped to a temp
   dir (see `_TempDBMixin`), `lib.config.RESULTS_DIR` pointed at a temp dir so
   `handoff.json` lands somewhere inspectable.
4. **Assert the contract** — assert exact `STATUS:`/`NEXT:`/`DIAG:` substrings
   and the dossier JSON fields, NOT implementation details.

Mock vs stub split:
- **Mock** (`unittest.mock`) for things the code *calls* and we want to assert
  (the page object, `chrome_session`, `subprocess.run`).
- **Stub** (hand-written fake) for things the code *reads* and we want to vary
  (a `FakeSelectEl` for the native-select strategy — see test_automation_lib —
  or a canned `handoff.json`).

## What to add next (ordered by leverage)

1. `test_handoff_contract.py` — pin the signal contract for the four producers:
   fill (`STATUS: filled` + `NEXT: check` + dossier fields), submit (outcome
   cascade → `STATUS: submitted`), detect (`TYPE:` + `NEXT:`), and the emit
   helpers' protocol-prefix quoting. **Added this pass (11 tests).**
2. `test_report_roundtrip.py` — the dossier READERS consume what the WRITERS
   emit: `report.py handoff|diff|audit` round-trip `handoff.json` +
   `handoffs/` history + `apply_audit.jsonl`; DB-driven readers
   (stats/candidates/applied-confirm) run against a temp schema DB.
   **Added this pass (9 tests).**
3. `test_text_strategy.py` — the text-input transforms the architecture survey
   flagged: E.164 phone, postal space-strip, maxlength year-rescue,
   truncation+diag, placeholder→autocomplete routing, verify-failure fallback.
   **Added this pass (12 tests).**
4. `test_drift.py` — the drift detector's detection logic: agree/stale/demote,
   dry-run, probe-error resilience, jsdom-absent. **Added this pass (7 tests).**
5. `test_verify.py` (extended) — the `_playwright_verify` 4-strategy flow:
   success text / confirmation URL / modal-closed / Applied-button mark applied;
   validation_error is inconclusive. **Added this pass (6 tests).**
6. `test_credentials.py` — the pure credential logic: multi-tenant domain
   keys, password masking, complexity scoring, platform rule resolution
   (workday-symbol), deterministic rule extraction from page text, freeform
   rule parsing. **Added this pass (19 tests).**
7. `test_handoff_contract.py` (extended) — the `act` arg dispatcher:
   malformed `--answers` fails loudly, valid overrides route to fill,
   submit/next dispatch, unknown command errors. **Added this pass (5 tests).**
2. Extend to `report.py` handoff/diff/audit readers — assert they round-trip
   what the writers emit (the dossier is the truth the orchestrator reads).
3. Live smoke (one real browser job) for the `(L)`-marked cases — these will
   stay low-coverage by design; unit tests cannot reach a real ATS.

## Honest limits

- Coverage of browser/LLM/batch modules will stay low — that is not a bug in
  the suite, it is the cost of the live half of the system.
- `coverage` measures line execution, not the *orchestrator contract*. The
  handoff tests are the real check; coverage is only the tripwire for untested
  modules.
