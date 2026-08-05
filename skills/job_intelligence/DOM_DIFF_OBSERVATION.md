# Dynamic-field DOM-diff observation — design proposal

The idea: **for dynamic fields, diff the DOM/shadow-DOM around the field
before vs after typing the answer, and surface a minimal "what did my action
do to the page?" observation.**

This document develops the idea, weighs it (pros/cons), and proposes the least
intrusive mechanism. It does NOT change the certification path — this is an
*observation* enrichment only (C-O2: the trail is faithful, and a DOM delta is
evidence, never a verdict).

## What exists today

The current read-back model is **value-string based**:

- `filler._read_element_value` (filler.py:80) reads the field's OWN value via
  the FieldValueReader cascade (`el.value`, select option text, aria/combobox
  readers) — post-fill.
- `filler._check_delta` (filler.py:108) compares the read-back string to the
  expected answer (with the URN trap, Antigua `+1` guard, safe-normalization).
- `audit.log_field` (audit.py:56) records the **before/after value strings** in
  `apply_audit.jsonl`.
- The **conditional-reveal sweep** (fill.py:818) already re-probes after
  control clicks — but it only looks for *new fields*, not "what changed".
- `corpus.capture` snapshots full-page HTML for drift detection — a whole-page
  before/after, not a scoped minimal view.

**The gap:** value-string read-back tells you "the field's value matches" — it
tells you NOTHING about what the page *did* in response. For the two hard field
classes this matters most:

1. **React-controlled inputs that reject/overwrite** — `el.value` read-back
   says "unchanged"/"wrong", but the page *did* react (re-rendered, showed an
   error, opened a helper).
2. **Opaque-widget fields (URN/location typeahead)** — `el.value` is
   `urn:li:geo:...` (never the answer), but the *visible* DOM shows the real
   text. The value reader fails; a DOM diff would see the visible node appear.

## The proposal, developed

**Observation goal:** for a dynamic/combobox/shadow field, capture a *minimal
structural* fingerprint of what changed in the field's container during the
fill interaction — not the value (already covered), not the whole page (noisy),
but the *delta*: elements added/removed, attributes toggled, text nodes that
appeared in the widget.

**Mechanism — a scoped MutationObserver, registered per-field just before the
fill, drained after.** (Not a before/after HTML snapshot — an observer captures
only what changed *during the interaction*, which is the honest signal and the
least noisy.)

```
before fill:  page.evaluate("""(args) => {
    const el = document.querySelector(args[0]);
    const root = el.shadowRoot || el.closest('form, fieldset') || el.parentElement;
    window.__dom_obs = new MutationObserver(records => window.__dom_records.push(...records));
    window.__dom_records = [];
    window.__dom_obs.observe(root, { childList, subtree, attributes, characterData });
}""", [sel])

...fill...

after fill:   read window.__dom_records → summarize into
              { added: [tag names], removed: [tag names],
                attr: [names toggled], text: [short visible-text chunks] }
              → stop observer
```

**Minimal view = the summary**, not the raw records: a bounded list of
`{added, removed, attr, text}` entries. That is the "minimal dynamic view and
observation" — small enough to put in the dossier and cheap enough to read.

**Where it lands (observation only):**
- appended to the field's dossier record as `dom_delta: {...}` and to the
  audit log (`audit.log_field(..., dom_delta=...)`).
- **never** used by `_check_delta` to certify — the value-string check stays
  the certifier. A DOM delta is *corroborating evidence* the orchestrator reads
  alongside kind/method/reason, exactly like `selected_text` today.

## Pros

- **Catches the React-reject class**: an input that "didn't take" (value
  unchanged) but where the page reacted (error node, re-render, reveal) now
  produces a visible delta — the orchestrator can see "the page responded but
  the value didn't stick" instead of a bare `verify_failed`.
- **Sees opaque widgets**: the URN typeahead's visible-text node appears in the
  diff even though `el.value` is the opaque id — a signal the current cascade
  can't produce.
- **Minimal & cheap**: scoped to the field's container, summarized to a small
  fingerprint — not a page screenshot, not a full DOM dump.
- **Non-intrusive by construction**: a MutationObserver writes nothing; the fill
  path is untouched. Pure observation.
- **Faithful evidence (C-O2)**: it records what actually happened during the
  interaction window — immune to unrelated page churn that a full before/after
  snapshot would catch.

## Cons

- **Noise risk**: some SPA frameworks mutate constantly (focus styles, analytics
  spans). Mitigation: summarize, cap records (e.g. first 50), ignore
  `style`/`class`-only churn, and only attach for *dynamic* fields
  (combobox/select/aria role), not every text input.
- **Observer dies on navigation**: if the fill triggers a navigation (form
  posts, page redirects), the observer is destroyed before the drain. Mitigation:
  the reveal-sweep already re-probes after navigation; the observer is for
  same-page dynamic widgets (combobox, conditional reveal), which is the
  majority case.
- **Shadow-DOM piercing**: the observer root must be `el.shadowRoot` when
  present; jsdom can't test shadow DOM (CorpusPage limitation), so this is a
  live-only path with unit tests only on the summarizer (feed fake records).
- **Cost on multi-field forms**: N observers for N fields. Mitigation: one
  observer per form scoped by a stable container, drained per field; or attach
  only to fields flagged dynamic by the probe.
- **False-confidence temptation**: a DOM delta must never *certify* — it's
  corroboration, not proof. The discipline is the same as `selected_text`:
  surfaced, never certified.

## Least-intrusive option (recommended)

1. **Gate to dynamic fields only** — attach when `_is_combobox(field)` or the
   field's container has a shadow root or `aria-haspopup`/`role=listbox`.
   Zero cost for plain text/select inputs.
2. **One observer per form container**, records tagged by nearest-field, so
   multi-field pages don't spawn N observers.
3. **Summarize + cap** — produce `{added, removed, attr, text[:3]}` with a
   record cap; drop `class`/`style`-only mutations.
4. **Dossier + audit only** — never in `_check_delta`.
5. **Unit-test the summarizer** with fake MutationRecords (no jsdom needed);
   the observer JS itself is live-only, verified in the corpus/live smoke.

## Verdict

Worth doing — as **observation-only**. The two field classes it illuminates
(React-reject, opaque-widget) are precisely the ones where value-string
read-back is weakest, and a minimal DOM-delta is the cheapest faithful signal
that "the page reacted". It composes with the existing audit log (which records
value before/after) — the DOM delta is the *structural* counterpart. The
non-negotiable guardrail: it enriches the dossier, it never certifies.
