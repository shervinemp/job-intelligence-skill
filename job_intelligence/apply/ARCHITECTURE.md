# Apply Pipeline — Architecture v3

## Overview

A generalized form-filling pipeline that handles ~95% of ATS systems without platform-specific code. Self-healing through an inspector cascade that adapts to how the page renders forms (standard DOM, dialogs, iframes, shadow DOM, lazy-loaded SPA). Self-improving through a learner that records site behavior and feeds it back to the LLM on subsequent encounters.

**Guiding principles:**

- **Never submit on unvetted sites** — learning mode fills and advances but stops at the submit gate
- **One vetted site = all subsequent jobs on that domain trust it**
- **LLM sees compressed ambiguity, not verbose status** — deterministic code handles everything it can
- **Variable page counts handled dynamically** — no pre-configuration per platform
- **Platform configs are YAML, not Python** — only truly custom interactions get Python hooks

---

## File Layout

```
apply/
├── apply.py              # Router: detect | fill | next | submit | probe | verify
├── detect.py             # Job type classification + entry
├── navigate.py            # LinkedIn → External ATS URL extraction
├── act.py                 # All form actions (fill, next, back, submit, auto)
├── verify.py              # Post-submit verification
├── ARCHITECTURE.md        # This file
├── common/
│   ├── inspector.py       # 7-depth probe cascade, DOM snapshot
│   ├── learner.py         # LearnSession, LabelRegistry, ButtonClassifier, SiteProfile
│   ├── field_reader.py    # Canonical DOM field reader (single JS block)
│   ├── answer_matcher.py  # Label→answer matching with confidence scoring
│   ├── page_helpers.py    # Existing: find_page, scan_actions, tag_page, etc.
│   ├── page_manager.py    # Existing: PageRegistry
│   └── platforms.py       # Existing: text pattern dictionaries
├── registry/              # Platform configs + optional hook modules
│   ├── workday.yaml
│   ├── greenhouse.yaml
│   ├── lever.yaml
│   ├── ashby.yaml
│   ├── linkedin.py        # Python hooks only (GraphQL intercept)
│   └── _template.yaml     # Template for new platforms
└── legacy/                # Old per-platform scripts (to be removed)
    ├── linkedin/
    ├── greenhouse/
    ├── lever/
    └── workday/
```

---

## Data Flow

```
detect <jid>
  │
  ├─ linkedin.com/jobs/view
  │   ├─ GraphQL intercept → Easy Apply modal → TYPE: easy_apply
  │   ├─ "Apply on company website" button → navigate → TYPE: external
  │   └─ "Applied" / no button → TYPE: already_applied | unknown
  │
  ├─ known ATS domain (in registry/)
  │   ├─ Load registry YAML for widget hints
  │   ├─ Inspector probe (adaptive depth)
  │   └─ TYPE: ats_direct
  │
  └─ unknown domain
      ├─ Inspector probe (full cascade)
      ├─ Probe succeeded → record in learner, register domain
      └─ Probe failed → save DOM snapshot, TYPE: unknown

apply act --fill <jid>
  │
  ├─ Load state → find page (PageManager)
  ├─ Check CAPTCHA → block if present
  ├─ Probe page → Inspector.read_fields()
  ├─ Probe returned 0 fields? → Inspector.cascade()
  ├─ Load registry hints for domain
  ├─ Fill matching fields (confidence ≥0.85 auto, <0.85 → LLM)
  ├─ Upload resume
  └─ Print state → LLM decides next action

apply act --next <jid>
  │
  ├─ Classify forward buttons (ButtonIntentClassifier)
  ├─ Score ≥4? auto-click
  ├─ Score 2-3? auto-click, record to learner
  ├─ Score <2? → CANDIDATES → LLM picks
  ├─ Click → wait → re-probe
  └─ Post-click: submitted? → verify. Validation error? → surface.

apply act --submit <jid>
  │
  ├─ Learning mode (site not vetted): STOP — print LEARNED, save guide
  ├─ Production mode (site vetted): verify match → click submit
  ├─ verify_match fails? → fall to learning mode, no submit
  └─ Explicit --trust overrides all gates
```

---

## Inspector Cascade

Seven probe strategies, run in order. Cached per-domain after first success.

| Depth | Strategy | Avg time | Trigger |
|-------|----------|----------|---------|
| 0 | Standard DOM (`querySelectorAll`) | 50ms | Always |
| 1 | Dialog-scoped (`[role="dialog"]`) | 100ms | 0 fields at depth 0 |
| 2 | Iframe piercing (`page.frames`) | 200ms | 0 fields at depth 0-1 |
| 3 | Shadow DOM (Playwright locator) | 150ms | 0 fields at depth 0-2 |
| 4 | Lazy-load trigger (click "Apply" → MutationObserver) | 3s | 0 fields at depth 0-3 |
| 5 | Custom widget scan (registry hints) | 100ms | 0 fields at depth 0-4 |
| 6 | Raw HTML scan | 200ms | `apply probe --deep` only |

**Adaptive skipping**: If `SiteProfile(domain).best_strategy` is set, start there. On miss, run from depth 0 upward.

```python
def probe(page, domain=None, depth=0):
    cached = SiteProfile.get(domain)
    if cached and cached.best_strategy:
        result = try_strategy(page, cached.best_strategy)
        if result.fields:
            return result
        # Cache invalid — site changed
        cached.best_strategy = None

    for d in range(depth, 7):
        result = try_strategy(page, d)
        if result.fields:
            SiteProfile.set(domain, best_strategy=d)
            return result
        if d >= 4:
            break  # depth 4+ only if earlier depths failed
    return ProbeResult([], strategy="failed")
```

---

## LLM Handover Protocol

Progressive disclosure — state line is terse, detail sections appear only when the LLM needs to act.

### State Line

```
STATE: F <filled>/<total> B <enabled>/<all> [submitted|error|learning]
```

Examples:
```
STATE: F 3/5 B 1/2
STATE: F 5/5 B 0/1 submitted
STATE: F 2/2 B 0/1 learning
```

### Optional Detail Sections

Only printed when non-empty:

```
UNFILLED:
  [0] Years Python? [select:1-5] r
  [1] LinkedIn URL [text]

BUTTONS:
  [0] Submit (s:4)
  [1] Back (s:1)

ERRORS:
  [0] This field is required
```

### NEXT Line

Always present:

```
NEXT: fill --answers '{"0":"4"}'
NEXT: click 0
NEXT: submit --confirm
NEXT: verify
NEXT: none
```

### LLM Response Format

The LLM responds with one of:

```
fill --jid <jid> --answers '{"<N>":"<value>", ...}'
click --jid <jid> --candidate <N>
submit --jid <jid> --confirm
verify --jid <jid>
```

### Context Budget

| Section | Tokens |
|---------|--------|
| State line | ~15 |
| Per unfilled field | ~30 |
| Per button | ~25 |
| Error list | ~30 |
| NEXT line | ~10 |
| **Average per handover** | **~50** |
| **Sustained handovers in 4K context** | **~40** |

---

## Submit Safety Gates

### Two Modes

```
apply act --fill <jid>              # Learning mode (default)
apply act --fill <jid> --trust      # Production mode (explicit opt-in)
```

### Mode Resolution

```python
def should_trust(domain):
    """Resolve trust level for current domain."""
    if args.trust:
        return True
    if args.no_trust:
        return False

    profile = SiteProfile.load(domain)
    if not profile:
        return False  # Unknown site → learning mode

    if profile.verify_match(current_page):
        return True   # Known site, pattern matches → trusted
    else:
        return False  # Pattern mismatch → re-learn
```

### What Happens at Submit Gate

**Learning mode:** The fill loop runs completely. When the submit button is detected:

```
LEARNED: site_myworkdayjobs.com
  Transitions: [Next, Next, Continue, Continue, Review]
  Widgets: [custom_dropdown, native_select]
  Pages: 5 (dynamic: 3-7)
  
Guide saved to ~/.openclaw/learnings/myworkdayjobs.com.md
Run with --trust to enable auto-submit on this site
```

**Production mode:** Verify against learned pattern, then:

```
VERIFY: match (5 pages, 4 transitions)
SUBMIT: clicked
STATE: submitted
```

If verification fails:

```
VERIFY: mismatch (expected 5 pages, got 3 — site changed)
FALLBACK: learning mode
No submit — re-learning new pattern
```

---

## Learning Persistence

### Storage Layout

```
~/.openclaw/learnings/
├── myworkdayjobs.com.md        # LLM-readable site guide
├── myworkdayjobs.com.json      # Machine-readable patterns
├── greenhouse.io.md
├── greenhouse.io.json
└── probe_failures/
    ├── <jid>_dom.html          # Full DOM snapshot on probe failure
    └── <jid>_probe.json        # Probe metadata
```

### LLM-Readable Guide (generated)

```markdown
# myworkdayjobs.com

## Navigation
- Pages: 3-7 (dynamic — EEO page conditional)
- Forward buttons: "Continue" (always enabled when required filled)
- Last page: "Review" heading, "Submit" button
- Back: never needed (form auto-saves per page)

## Widgets
- Dropdowns: button[aria-haspopup="listbox"] (not native SELECT)
- File upload: "Upload Resume" link (not drag-and-drop)
- Autocomplete: input[role="combobox"] on School/University fields

## Indicators
- Error: red text below field "This field is required"
- Submit disabled until all required fields filled
- Progress bar at top shows step N of M

## Edge Cases
- EEO/Veteran page appears ~50% of the time, after page 2
- Phone auto-formats — fill raw digits
- "How did you hear about us?" always defaults to "LinkedIn"
```

### Machine-Readable Patterns (code-consumed)

```json
{
  "domain": "myworkdayjobs.com",
  "first_seen": "2026-06-04",
  "last_seen": "2026-06-04",
  "best_strategy": "dialog_probe",
  "transitions": [
    {"from": 0, "button": "Continue"},
    {"from": 1, "button": "Continue"},
    {"from": 2, "button": "Continue"},
    {"from": 3, "button": "Review"},
    {"from": 4, "button": "Submit"}
  ],
  "page_range": [3, 7],
  "widgets": ["custom_dropdown", "native_select"],
  "has_eeo": true,
  "fill_count": 1,
  "last_success": "2026-06-04",
  "trusted": false
}
```

### LearnSession

```python
class LearnSession:
    """Tracks a single learning pass. Generates guide + patterns on completion."""

    def __init__(self, domain):
        self.domain = domain
        self.page_count = 0
        self.transitions = []
        self.widgets = set()
        self.field_count_per_page = []

    def record_page(self, fields, buttons):
        self.page_count += 1
        self.field_count_per_page.append(len(fields))
        for f in fields:
            if f.get("tag") == "DROPDOWN":
                self.widgets.add("custom_dropdown")
            elif f.get("type") == "file":
                self.widgets.add("file_upload")

    def record_transition(self, button_text):
        self.transitions.append({
            "from": self.page_count - 1,
            "button": button_text
        })

    def generate_guide(self):
        # Template-based with observations appended
        ...

    def save(self):
        guide = self.generate_guide()
        guide_path = LEARNINGS_DIR / f"{self.domain}.md"
        guide_path.write_text(guide)

        profile_path = LEARNINGS_DIR / f"{self.domain}.json"
        profile_path.write_text(json.dumps(self.to_dict(), indent=2))
```

---

## Answer Matching (Confidence Scoring)

```python
def match_answer(field_label, knowledge_base):
    """Returns (value, confidence) or (None, 0)."""

    norm = normalize(field_label)  # lowercase, strip punctuation

    # 1. Exact match → 1.0
    if norm in knowledge_base.exact:
        return knowledge_base.exact[norm], 1.0

    # 2. Canonical key match (via LabelRegistry aliases) → 1.0
    key = LabelRegistry.resolve(norm)
    if key in knowledge_base.canonical:
        return knowledge_base.canonical[key], 1.0

    # 3. Word overlap (stop words removed) ≥3 significant words → 0.9
    words = significant_words(norm)
    for key, value in knowledge_base.fuzzy.items():
        overlap = len(words & significant_words(key))
        if overlap >= 3:
            return value, 0.9

    # 4. Word overlap = 2 → 0.7
    for key, value in knowledge_base.fuzzy.items():
        overlap = len(words & significant_words(key))
        if overlap == 2:
            return value, 0.7

    # 5. Single word containment → 0.5
    for key, value in knowledge_base.fuzzy.items():
        if any(w in key for w in words):
            return value, 0.5

    return None, 0.0


def should_defer_to_llm(label, confidence):
    """Threshold-based deferral. Configurable in profile.json."""
    threshold = PROFILE.get("llm_thresholds", {}).get("label_confidence", 0.7)
    return confidence < threshold
```

---

## Button Intent Classification

```python
class ButtonIntentClassifier:
    """Maps button text to action intent using cascading strategies."""

    # Strategy 1: word-level scoring
    INTENT_WORDS = {
        "submit":    {"submit", "send", "apply", "done"},
        "advance":   {"next", "continue", "review", "save & continue", "proceed"},
        "back":      {"back", "previous", "edit", "go back"},
        "cancel":    {"cancel", "close", "discard", "never mind"},
    }

    @classmethod
    def classify(cls, text):
        text = text.lower().strip()

        # Strategy 2: exact match against known patterns
        KNOWN = {
            "submit application":    ("submit", 1.0),
            "submit":                ("submit", 0.95),
            "send application":      ("submit", 0.95),
            "apply":                 ("submit", 0.85),
            "review":                ("advance", 0.9),
            "continue":              ("advance", 0.9),
            "next":                  ("advance", 0.95),
            "save and continue":     ("advance", 0.85),
            "back":                  ("back", 0.95),
            "previous":              ("back", 0.9),
        }

        if text in KNOWN:
            return KNOWN[text]

        # Strategy 3: word scoring
        scores = {"submit": 0, "advance": 0, "back": 0, "cancel": 0}
        for intent, words in cls.INTENT_WORDS.items():
            for w in words:
                if w in text:
                    scores[intent] += 1

        if text.startswith("save") or text.startswith("continue"):
            scores["advance"] += 1

        best = max(scores, key=scores.get)
        if scores[best] >= 2:
            return (best, 0.8)
        if scores[best] == 1:
            return (best, 0.5)

        # Strategy 4: regex fallback
        if re.search(r'\b(submit|send|apply)\b', text):
            return ("submit", 0.6)
        if re.search(r'\b(next|continue|review|proceed)\b', text):
            return ("advance", 0.6)
        if re.search(r'\b(back|previous|edit)\b', text):
            return ("back", 0.6)

        return ("unknown", 0.0)


    @classmethod
    def pick(cls, buttons, action):
        """Pick the best button for a given action intent."""
        scored = []
        for i, btn in enumerate(buttons):
            intent, confidence = cls.classify(btn["text"])
            if intent == action:
                scored.append((confidence, i, btn))

        if not scored:
            return None

        scored.sort(key=lambda x: -x[0])
        return scored[0]
```

---

## Platform Registry

### YAML Config Structure

```yaml
# registry/workday.yaml
name: workday
version: 1

detect:
  domains:
    - myworkdayjobs.com
    - myworkdayjobs.wd5.myworkdayjobs.com
    - workday.com
    - workdayjobs.com

probe:
  best_strategy: dialog_probe
  widgets:
    dropdown: "button[aria-haspopup='listbox']"
    autocomplete: "input[role='combobox']"

patterns:
  login_wall:
    - "sign in to apply"
    - "create account"
  guest_apply:
    - "continue without signing in"
    - "apply as guest"
  already_applied:
    - "already applied"
    - "you have already submitted an application"

properties:
  multi_page: true
  has_eeo: true
  has_progress_bar: true
  page_range: [3, 7]
```

### Python Hook (for platforms that need it)

```python
# registry/linkedin.py
"""LinkedIn needs GraphQL response interception for Easy Apply detection."""

def pre_detect_hook(page, url):
    """Set up interception before navigation."""
    if "linkedin.com/jobs/view" not in url:
        return
    fields = []

    def handle_response(response):
        if "jobPostingApplyFlowByJobId" in response.url and response.ok:
            try:
                body = response.json()
                questions = body.get("data", {}).get(
                    "jobPostingApplyFlowByJobId", {}
                ).get("questions", [])
                for q in questions:
                    if isinstance(q, dict):
                        label = q.get("title", {}).get("text", "") or \
                                q.get("body", {}).get("text", "")
                        fields.append({
                            "label": label[:80],
                            "type": q.get("type", "unknown"),
                            "required": q.get("required", False),
                        })
            except Exception:
                pass

    page.on("response", handle_response)
    return fields
```

### Registry Resolver

```python
def resolve_registry(url):
    """Load platform registry by URL domain match."""
    domain = urlparse(url).netloc.lower()
    for path in REGISTRY_DIR.glob("*.yaml"):
        config = yaml.safe_load(path.read_text())
        for d in config.get("detect", {}).get("domains", []):
            if d in domain:
                return config

    # Check for Python hook
    mod_path = REGISTRY_DIR / f"{path.stem}.py"
    if mod_path.exists():
        spec = importlib.util.spec_from_file_location(path.stem, mod_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        config["hooks"] = mod

    return config  # None if no match → generic pipeline
```

---

## Verify Module

```python
def verify(jid, page):
    """Check submission outcome. Deterministic — no LLM involvement."""

    # 1. Modal closed (Easy Apply)
    if not page.evaluate("() => document.querySelector('[role=\"dialog\"]')"):
        return "submitted"

    # 2. Success text in body
    text = (page.evaluate("() => document.body.innerText") or "").lower()
    for signal in ["thank you", "submitted", "your application", "has been sent"]:
        if signal in text:
            return "submitted"

    # 3. HTTP response to submit endpoint (via page.on("response"))
    if page_state.get("submit_response_ok"):
        return "submitted"

    # 4. LinkedIn "Applied" button
    buttons = page.evaluate("""() => {
        return Array.from(document.querySelectorAll('button'))
            .filter(b => b.offsetParent)
            .map(b => b.textContent.trim());
    }""")
    if "Applied" in buttons:
        return "submitted"

    return "unknown"
```

---

## Implementation Order

| Phase | What | Files |
|-------|------|-------|
| 1 | Create `common/field_reader.py` | Extract canonical JS from 5 detect.py files into one function |
| 2 | Create `common/learner.py` | LearnSession, LabelRegistry, ButtonIntentClassifier |
| 3 | Create `common/inspector.py` | 7-depth probe cascade, DOM snapshot |
| 4 | Create `registry/_template.yaml` | First platform config template |
| 5 | Migrate Workday patterns to `registry/workday.yaml` | Remove `apply/workday/detect.py` |
| 6 | Migrate Greenhouse patterns to `registry/greenhouse.yaml` | Remove `apply/greenhouse/detect.py` |
| 7 | Migrate Lever patterns to `registry/lever.yaml` | Remove `apply/lever/detect.py` |
| 8 | Create `registry/linkedin.py` | GraphQL hook, remove `apply/linkedin/detect.py` |
| 9 | Integrate inspector + learner into `act.py` | Add probe cascade, trust gates, LearnSession |
| 10 | Add `apply.py probe` CLI command | Diagnostic probe command |
| 11 | Remove `apply/legacy/` | Delete old per-platform scripts after transition period |

---

## Migration Path

1. Phase 1-4: No behavioral change. New modules exist but aren't wired in.
2. Phase 5-8: Registry configs loaded but old detect.py still runs as fallback.
3. Phase 9: `act.py` uses inspector. Old path still available via `--legacy`.
4. Phase 10: `--pipeline v2` becomes default. `--legacy` for fallback.
5. Phase 11: Legacy removed. Pipeline is v2-only.

---

## Exclusions (What Stays Out)

| Scenario | Why excluded |
|----------|-------------|
| Custom resume generation | Handled by tailor.py before apply |
| Interview scheduling | Separate pipeline stage |
| Multi-session authentication | Handled by auth_wall module |
| Application status tracking | Handled by report.py + DB |
