# Comparison — Our pipeline vs Jobright Autofill extension (1.19.0)

Date: 2026-08-06. Static-analysis comparison of the unpacked Jobright CRX
(`C:\Users\sherv\AppData\Local\Google\Chrome\User Data\Default\Extensions\odcnpipkhjegpefkfplmedhmkmmhmoko\1.19.0_0`,
reverse-engineering notes in its own `REVERSE-ENGINEERING.md`) against this
skill's apply/reach pipeline. Both solve the same core problem — crawl an
arbitrary job application form and fill it — but from opposite ends: Jobright
is a browser-extension **fill-assist** tool backed by a server AI; we are a
CDP/Playwright **autopilot** that also submits and verifies.

**Status (2026-08-06): the borrow list in §4 is IMPLEMENTED.** Each item is
flagged `[DONE]` with a link to the code that realizes it, all as **our own
original implementation** — no extension source was copied. New tests in
`tests/test_detection_breadth.py` (35 cases) cover every item; full suite
923 passed + 148 subtests, lint PASS.

---

## 1. Architecture at a glance

| Dimension | Jobright (extension) | Us (this skill) |
|---|---|---|
| Execution model | MV3 service worker + content script at `document_start`, all frames, injected React helper in shadow DOM | Python + Playwright over CDP, sequential per-job CLI (`apply.py act --fill`) |
| Form crawl | Per-ATS crawler classes → rules `{label, type, $input, options, children, required}` | Generic `field_reader` JS block + YAML registry per ATS |
| ATS registry | **~70** ATS, detected by domain + path regex + query params + page-source keywords + iframe-only flag | **70** registry YAMLs, detected by domain + query params + page-source keywords + iframe-only flag |
| Answer source | Server AI (`/swan/autofill/fill-v2`) generates answers from user profile + resume | Deterministic `resolve` against `profile.json` + LLM **key-mapping** (label→profile key), never answer generation |
| React handling | MAIN-world fiber injection (`__reactFiber$`) for Ashby/Workday/react-select/Recruitee/Workable | No fiber access; native setters + Playwright real events + keyboard typing + DOM readers |
| Workday | Full fiber state machine (`updateStateFromValue`, `fireChangeEvent`, `onDatePicked`, `clearFieldErrors`) | Generic `button[aria-haspopup]` dropdown strategy + combobox; no fiber |
| Iframes | Explicit `postMessage` protocol (REQUEST/EXECUTE/UPDATE/SUBMIT_APPLICATION) + in-iframe crawler | Playwright frame iteration (`_frame_for_sel`, `fr.evaluate`) + direct-navigation to cross-origin iframe src |
| Resume upload | MAIN-world patch of `HTMLInputElement.prototype.click` + `showOpenFilePicker`, staged files | `page.set_input_files` + `expect_file_chooser` interception + LinkedIn Easy Apply resume-swap |
| Value set | `el.value=v` + native setter + `input→change→blur→keydown(Enter)→keyup→blur→focus→click→change→blur` | Playwright `el.fill()` / `native_setter` (input+change) / `dispatch_events` / keyboard |
| Verification | None per-field (trusts its fill; reports `filledFields`/`missingFields`) | Elaborate read-back cascade: delta check, echo detection, URN trap, truncation, vision second-observer for risk fields |
| Submit | **User clicks submit manually** (fill-assist) | Pipeline submits, one-shot guarded, outcome cascade + G2 independent confirm |
| Auth/login | Not handled — assumes user already logged into the ATS | Full auth wall: guest apply, sign-in w/ password rotation, account creation, 2FA via inbox, CAPTCHA, verify-email |
| Anti-bot | Random 200–400 ms inter-field delays, waits out Cloudflare challenges, suppresses `alert()` | Fixed sleeps (mostly), `check_captcha` treated as stop-condition |
| Job-id continuity | Rewrites apply links to carry `jr_id`; tab→jobId context; link-injection for CatsOne/GoHire trailing-slash fix | DB job row + `state.external_url`; ATS navigation does not rewrite links |

---

## 2. Shortcomings of our project vs Jobright

### S1 — ATS coverage: 70 vs ~70 (headline gap closed)
Jobright ships crawlers/detection for ~70 ATS. We now cover **70** (`apply/registry/*.yaml`): the 14 original + 22 multi-tenant ATS (jobvite, comeet,
smartrecruiters, workable, breezy, taleo, phenom, eightfold, ultipro,
brassring, avature, teamtailor, personio, gohire, rippling, zohorecruit,
dayforce, oraclecloud, paylocity, jazzhr, freshteam, jobscore) + **34 company
portals** (amazon, google, apple, cisco, tesla, uber, tiktok, bytedance,
metacareers, hubspot, paycom, intuit, waymo, gusto, adobe, recruitee,
trakstar, pinpointhq, isolved, jobdiva, careerplug, careerspage, clearcompany,
recruiterflow, hiringthing, catsone, prismhr, toast, okta, jacobs, ycombinator,
walmart, trinehire, dover).
Detection is layered (COMPARISON §S1):
- **query params** (`gh_jid`, `gh_src`, `ashby_jid`, `LeverAppId`,
  `jobviteiframe`) — an ATS apply URL on a foreign host still names its engine;
- **page-source keywords** (teamtailor CDN, `APPLY_form_renderer.js`,
  `phenompeople`, `recruiterflow`, Avature) — `resolve_from_page()` scans the
  live page when hostname + query both fail;
- **iframe-only flag** (iCIMS renders its form in an iframe with no page form) —
  `_probe_form` bumps iframe probing to the front for such platforms;
- **company portals are path-scoped** — consumer-root domains (google.com,
  amazon.com, apple.com, ...) are only matched via their **careers
  subdomain/portal** (`careers.google.com`, `amazon.jobs`, `jobs.apple.com`),
  so `mail.google.com` / `amazon.com/gp/cart` never classify as an ATS.
  Exception: Tesla/Uber/Waymo/Jacobs/Dover genuinely host careers on the root
  domain (`tesla.com/careers`) — an over-match there is benign (probe finds no
  form → `no_apply_path`, never a dangerous fill).

### S2 — React fiber access: Jobright drives React from the inside; we drive from the outside
Jobright injects MAIN-world scripts that read/write `__reactFiber$` internals:
- Ashby field metadata (type/serialization/locationTypes),
- react-select via `setValue(option, "select-option")`,
- Recruitee phone-country via fiber,
- Workable consent checkboxes,
- **Workday** text/select/options/date/checkbox via `updateStateFromValue`,
  `fireChangeEvent`, `onDatePicked`, `clearFieldErrors`.

We deliberately treat `__reactFiber$` attribute *names* as hydration
placeholders to skip (`hydration.py`), and drive React from outside via
Playwright real events + `native_setter` (input+change) + keyboard typing.
This works for most Ember/React text inputs and react-select (we have a solid
DOM strategy), but Workday's fiber-only widgets and Ashby's custom widgets are
our known weak spots — the same ATS where Jobright is strongest.

**Borrow:** a MAIN-world fiber **reader/helper** (CDP `Page.addScriptToEvaluateOnNewDocument`
or Playwright `page.add_init_script`) exposed via a well-guarded DOM-event API
(a la Jobright's `__jr_ashby_field_metadata_request`). Use it **only** as a
read/write primitive for the ~3 hard cases (Workday dropdown/date/checkbox,
Ashby metadata, Recruitee country). Keep our DOM read-back verification as the
certifier — fiber writes are still trusted by Jobright, which is exactly the
class of trust our `_check_delta` was built to second-guess.

### S3 — Field-cap mismatch: reader returns ≤35 fields, fill caps at 300
`field_reader.py` ends with `fields: finalFields.slice(0, 35)`, but
`fill_runner.py` allows `MAX_FIELDS_PER_PAGE=300`. A Workday multi-section
page or a matrix/table question set routinely exceeds 35 → we literally never
see the remaining fields. Jobright extracts everything (its `collectFormData`
handles repeating groups / `children`).

**Borrow:** raise the reader slice (or make it configurable per registry —
Workday pages need the full set) and add `children`/repeating-group handling
for matrix questions. This is a correctness fix, not just coverage.

### S4 — No Workday-specific state machine
Jobright treats Workday as a first-class state machine (signup/forgot-password
pre-flow, per-field fiber writes, `clearFieldErrors`). Our `workday.yaml`
covers login-wall handling and the dropdown widget, but the fill itself is the
generic combobox/text path, and Workday is notorious for rejecting naive
fills. This is the single ATS where our fill reliability is worst and
Jobright's is best.

**Borrow:** Workday-specific fill semantics — target `data-automation-id`
inputs, use keyboard-Enter for its skill typeahead (we already note this),
and consider the fiber helper from S2 for its selects/dates.

### S5 — Iframe orchestration is implicit, not a protocol
Jobright has an explicit postMessage protocol (`REQUEST_IFRAME_LOADED` →
`IFRAME_LOADED` → `EXECUTE_IFRAME_FUNCTION` → `autoFillResultFromIframe`) with
a 500 ms poll and 10 s timeout, driving in-iframe crawlers for ~17 iframe-based
ATS (iCIMS, Brassring, Jobvite, Comeet, Rippling, Workable, Ashby, Lever,
GoHire, BambooHR, ZohoRecruit, OracleCloud, Eightfold, JobScore, Paylocity,
Intuit-quiz, SmartRecruiters). We iterate `page.frames` ad hoc
(`_frame_for_sel`, submit clicks `fr.locator(...)`) and have a clever
direct-navigation escape for cross-origin iframes (`helpers.py
_resolve_standalone_form_url`), but no wait-for-iframe-loaded protocol and no
in-iframe progress contract.

**Borrow:** a `wait_for_form_iframe(page, timeout)` helper that polls
`page.frames` for the form before filling (we currently race iframe lazy
load), and treat "form in iframe" as an explicit probe strategy with its own
retry/evidence path rather than a fallback discovered mid-fill.

### S6 — No file-upload interception at the JS level (`.click()` / `showOpenFilePicker` patch)
Jobright patches `HTMLInputElement.prototype.click` + `showOpenFilePicker` in
the MAIN world so that ANY upload-button click (including dropzones and
buttons that internally `showOpenFilePicker()`) silently gets the staged file.
We handle visible file inputs (`set_input_files`) and button-triggered choosers
(`expect_file_chooser`), plus the LinkedIn Easy Apply resume-swap. But the
modern Chrome File System Access API path (`showOpenFilePicker`) is unhandled —
some 2024+ ATS use it.

**Borrow:** a Playwright `page.add_init_script` that patches
`window.showOpenFilePicker` to return a staged File (and logs calls), used only
when the direct file-input path fails. Keep it scoped/disabled by default.

### S7 — Humanization is fixed, not randomized
Jobright inserts 200–400 ms random inter-field delays and per-field progress.
Our combobox uses fixed `time.sleep(0.3/0.6/1.5)`, `AutocompleteFiller` types at
`delay=80`. Randomization is cheap and makes the cadence less robot-patterned.

**Borrow:** wrap the per-field fill in a small `random.uniform(0.2, 0.4)` jitter
(one line in `fill_runner.fill_page`). Note this is a detection-hardening nicety,
not a correctness fix; we don't currently randomize inter-field pacing at all.

### S8 — Job-id continuity across ATS navigation
Jobright rewrites "apply" links to carry `jr_id`, normalizes GoHire trailing
slash, and binds tab→jobId so the job identity survives navigation into the
ATS. We hold the jid in state and rely on `external_url`, but we do **not**
re-write links or re-tag after navigation — a CatsOne-style multi-hop apply
link can lose which job we're on. We mitigate with the same-posting dedup gate
and `tag_page`, but the identity is implicit.

**Borrow:** `safeSetJobIdInUrl`-style link rewriting for the ATS families that
drop query params on navigation (GoHire, CatsOne, UltiPro), gated on the same
host-alias logic Jobright uses. Low cost, real correctness gain on those ATS.

---

## 3. Where we are stronger (keep these — do not "borrow" into weakness)

| Capability | Why we're better |
|---|---|
| **Independent verification** | `_check_delta` (echo/URN/truncation/reinterpretation), value-reader cascade, vision second-observer for risk fields. Jobright trusts its own fill; we certify. This is our core differentiator and must stay the certifier even if we add fiber primitives. |
| **Submit + outcome cascade** | We submit safely (one-shot guard, re-fill-before-submit, outcome detection, G2 tracker/email confirm). Jobright deliberately leaves submit to the user. |
| **Auth wall** | We auto-login, create accounts, complete 2FA from the inbox, handle CAPTCHA and email verification. Jobright assumes you're already logged in. |
| **Evidence trail** | Dossiers, DIAG lines, audit log, `act --inspect`, "LOOK FIRST" posture. Jobright has no equivalent — it can't tell you *why* a fill failed. |
| **Shadow DOM crawling** | We recurse shadow roots (`walkShadow`); Jobright's crawler doesn't (it only uses shadow DOM for its own UI). |
| **Learning** | `field_methods` (per-host per-label method preference, verify-strategy learning, `reject_method` on adjudicated wrong fills). Jobright has no local learning. |
| **Honeypot / junk detection** | `_is_junk_field` + reader-level honeypot signals. |
| **Security posture** | No page-content exfiltration; all LLM calls gated by `lib.automation.llm` policy; vision is loopback-only. Jobright sends full form HTML + rules + page snapshots to its server. |
| **Flexibility** | YAML registry + generic engine = adding an ATS is adding data, not code. Jobright needs a server-side crawler per ATS. |

---

## 4. What to borrow, prioritized

1. **[DONE] S1 detection breadth** — `registry.py` now matches by domain,
   **query-param** (`gh_jid`/`ashby_jid`/`LeverAppId` → `resolve()`), and
   **page-source keyword** (Workday/iCIMS/Lever/Ashby/SF bundles →
   `resolve_from_page()`); `icims.yaml` sets `iframe_only: true` and
   `_probe_form` bumps iframe probing to the front for such platforms.
   Tests: `QueryParamDetection`, `PageSourceDetection`, `IframeOnlyFlag`.
2. **[DONE] S3 field-cap fix** — `field_reader.read_fields(..., max_fields=)`
   defaults to 300 (matching `MAX_FIELDS_PER_PAGE`); the old hard `slice(0,35)`
   that silently dropped fields on Workday/matrix forms is gone. Tests:
   `FieldReaderCap`.
3. **[DONE] S2 fiber helper for the hard three** — new `apply/common/fiber.py`:
   guarded READ-ONLY React-fiber readers (`read_fiber`, `read_ashby_metadata`,
   `read_recruitee_country`, `options_from_fiber`). Fiber is used **only** to
   disambiguate option lists / selected labels when the DOM is empty (Workday
   dropdowns); the deterministic `_check_delta` read-back stays the certifier.
   Combobox `_fiber_option_fallback` + `fiber_match` path. Tests: `FiberHelpers`,
   `ComboboxFiberFallback`.
4. **[DONE] S4 Workday-specific semantics** — `workday.yaml` gains `fill.hints`
   (`skills_enter`, `clear_field_errors`); `fill_runner` attaches hints to
   fields; new `WorkdayFiller` in the chain confirms typeaheads with Enter and
   clears Workday's stale error underline before typing. `apply/strategies/workday.py`
   is our own protocol. Tests: `WorkdayFillHints`, `WorkdayFillerDispatch`.
5. **[DONE] S5 iframe wait/protocol** — `inspector.wait_for_form_frame()` polls
   the frame tree until a frame exposes form controls (or matches an
   `iframe_only` registry entry); `_probe_iframes` waits up to `JI_IFRAME_WAIT`
   (default 6s, off under JI_TESTS) before concluding empty. Tests: `IframeWait`.
6. **[DONE] S8 job-id continuity** — registry `url_normalize` rules + aggregate
   `normalize_url()` strip the query-dropping trailing slash (greenhouse
   `jobs/<id>/?gh_jid=`); `detect._classify` stores the canonical URL. Tests:
   `UrlNormalization`.
7. **[DONE] S6 `showOpenFilePicker` interception** — `fill_runner` gains a
   MAIN-world FSAP patch (`_try_show_open_file_picker` / `_patch_*`): a synthetic
   `FileSystemFileHandle` resolves the staged resume/cover when a 2024+ ATS uses
   the File System Access API instead of an `<input type=file>` chooser. Wired
   into the upload fallback chain after the filechooser path. Tests:
   `ShowOpenFilePicker`.
8. **[DONE] S7 randomized inter-field delay** — `inter_field_delay()` in
   `fill_runner` sleeps a random 0.15–0.35s per field (`JI_FILL_DELAY` range,
   OFF under JI_TESTS and at `JI_FILL_DELAY=0`). Tests: `InterFieldDelay`.

## 5. What NOT to copy

- **Server-side AI answer generation** (`/swan/autofill/fill-v2`) — we'd rather
  surface novel questions as evidence to the orchestrator than guess. Jobright
  generates; we surface. Keep the ETHOS routing.
- **Full page-content exfiltration** (form HTML + snapshots to a server).
- **`declarativeNetRequest` header-strip / iframing of Lever/Ashby** — we
  navigate directly, no need.
- **Blind fill without read-back** — our verification is the point.
- **Credit/membership upsell + encrypted LinkedIn tracing** — product/anti-bot
  concerns that don't belong in a private pipeline.

---

## 6. One-paragraph summary

Jobright wins on **breadth and browser-internal cleverness** (70 ATS, fiber
driving, iframe protocol, JS-level file interception); we win on **verification,
safety, evidence, and autonomy** (read-back certification, guarded one-shot
submit, outcome cascade, full auth handling, local learning).

**Implemented (2026-08-06):** layered ATS detection (query-param +
page-source-keyword + iframe-only) and **22 new registry ATS** (jobvite,
comeet, smartrecruiters, workable, breezy, taleo, phenom, eightfold, ultipro,
brassring, avature, teamtailor, personio, gohire, rippling, zohorecruit,
dayforce, oraclecloud, paylocity, jazzhr, freshteam, jobscore) closing the
biggest compatibility gap; a configurable field-reader cap; guarded READ-ONLY
React-fiber helpers for the Workday/Ashby/Recruitee hard cases; Workday-specific
fill protocol (Enter-confirm, error-clear, hints); an iframe wait-for-form
protocol; URL normalization for query-dropping ATS; a `showOpenFilePicker`
upload interception; and randomized inter-field pacing. All our own original
code — no extension source copied. Read-back verification remains the sole
certifier of anything we fill.

**Audit (2026-08-06, after implementation):** a rigorous pass over every S1–S8
change found and fixed real bugs, each with a regression test in
`tests/test_detection_breadth.py`:
- **Workday Enter-confirm certification gap** — `confirm_with_enter` returned
  True on blind Enter with no read-back, and the combobox branch of `_fill_one`
  trusts the filler (skips `_check_delta`). Now verifies via the shared
  value-reader/scorer; a wrong or unreadable selection returns False so the
  generic combobox path retries. (`WorkdayEnterVerification`)
- **FSAP false-positive + frame leak** — `_try_show_open_file_picker` returned
  True after any click even if `showOpenFilePicker` never fired; now tracks a
  `__ji_fsap_called` flag and only reports success when the picker actually
  ran. `_unpatch_show_open_file_picker` now restores every frame, not just the
  main page (a stale patch would feed the next upload a frozen handle).
  (`ShowOpenFilePicker`)
- **Generic source-keyword false positives** — bare `"Workday"`, `"breezy"`,
  `"workable"`, `"avature"`, `"eightfold"`, and `"succesfs"` (typo) matched
  prose on any page ("Workday experience", "a breezy day", "a workable
  solution", "an eightfold increase"). Removed/anchored; `resolve_from_page`
  now requires a domain-specific marker. (`FalsePositivePrevention`)
- **`pid` query-param false positive** — eightfold's `?pid=` was dropped (a
  generic param on countless sites). `zoho.com` was dropped from zohorecruit
  (mail.zoho.com / crm.zoho.com would have matched). Oracle Cloud HCM was
  split out of the Dayforce YAML into its own `oraclecloud` entry — they are
  different ATS. (`FalsePositivePrevention`)
- **Fiber phantom-click** — `_fiber_option_fallback` emitted options with
  `id:""/x:0/y:0` that `_click_option` could never click (no real DOM
  element). It now re-locates each option in the real DOM and drops anything
  not clickable. (`ComboboxFiberFallback`)
