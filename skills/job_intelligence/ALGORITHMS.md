# Handoff & Self-Correction — algorithms and analysis

How the system decides WHEN to involve the orchestrator (and how to do it
cheaply), and HOW it corrects itself from its own mistakes. Built on the
observability-boundary principle and the C1–C9 / S1–S8 contracts.

---

## Part 1 — The handoff decision (WHEN to escalate)

### H1. The observability gate (the core rule, formalized)

Hand off **if and only if** the step cannot independently observe its outcome —
never on confidence alone.

```
handoff(step) = not observable(step) OR (observable AND below-verify-bound)
```

- `observable` = the system has an observation channel (read-back, vision,
  success signal) that can prove the outcome.
- Confidence is NOT a trigger. A confident-but-unverified answer (the Antigua
  case) is exactly what must NOT self-report.
- Risk fields (identity/legal/location/salary/relocation) get the stronger
  variant: `handoff if not verified`, period — no lower bound to argue.

### H2. Value-of-information gating

Before any escalation, compute whether asking is worth the context:

```
VOI = P(handoff reveals a correction) × cost_of_wrong_answer − cost_of_asking
```

- Low-stakes fields (marketing, optional selects): skip handoff even if
  unverified — flag and continue.
- Risk fields: VOI is structurally high → always ask.
- This replaces "escalate everything ambiguous" with "escalate what matters."

### H3. Multi-reader belief accumulation (the verify cascade as a model)

Instead of one read-back, combine independent signals and require a bound:

- Signals: DOM read-back (standard / radio / combobox readers), vision
  confirmation, cross-field consistency.
- Combine with a small noisy-OR / Bayesian update:

```
P(verified | signals) rises with each INDEPENDENT confirming channel.
P(verified | echo_only) ≈ prior — an echo adds no information (the Antigua lesson).
```

- Verdict: verified only above a belief bound; below it → unverified → handoff
  for risk fields.
- Each channel must be genuinely independent — a read-back that echoes the input
  is the SAME channel, not a second one.

### H4. Contradiction-driven escalation

Treat disagreement between sources as the signal, not the exception:
profile says X, learned says Y, read-back says Z → escalate. Agreement of
independent sources IS verification; disagreement is a handoff, regardless of
which side "looks" right.

---

## Part 2 — The handoff economy (HOW to escalate cheaply)

The orchestrator's context is the scarce resource. Handoffs must be batched,
deduplicated, and compressed — never one-at-a-time.

### E1. Label-dedup batching

Across a fleet run, collect every open decision and group by `label`. One
`ANSWER <label>: <value>` resolves N jobs. This is the existing fleet steering
memo generalized: the orchestrator answers a *meaning*, not a *job*.

### E2. Owner-split first, full dossier on demand

Surface `DECISION: owner count — first-label...` (S1 compact). Only expand to
full dossiers (S2) for the subset the orchestrator chooses. Never ship 60
dossiers to decide 3.

### E3. Amortized fleet review

Fleet-level handoffs (C7) over per-job handoffs whenever possible:
- one "recurring label fails" decision fixes N jobs,
- one "platform behaves differently" decision fixes a cluster,
- the wrong-fill SPC trip (below) is a single fleet-level review, not N
  per-job investigations.

### E4. Progressive disclosure in contracts

Every C-contract reply is consumed in one line. `ANSWER` / `VERDICT` /
`ADMIT` are flat and machine-parsed. Detail is pulled only via the explicit
surface (`job <jid>`), never pushed.

---

## Part 3 — Self-correction (how the system learns)

### S1. Active-learning adjudication (C8, formalized)

Sample the fills most likely to be wrong, get a verdict, feed it back:

```
sample = rank(fills) by (uncertainty × stakes)
   uncertainty  = low belief, echo-only read-back, cross-platform variance
   stakes       = risk-field class, fleet impact
verdict: correct | wrong | unanswerable   (C8)
wrong  → correction buffer (S2)
correct → raises the resolver/rule posterior (S3)
```

The verdict ledger is the falsification instrument: wrong-fill rate is only
meaningful over adjudicated fills, always shown with its denominator.

### S2. Correction buffer → rule authoring (the learning loop's engine)

A `wrong` verdict creates a labeled example `(label, wrong_value, correct_value,
domain, context)`. When the same (label, domain) produces ≥2 corrections:

1. auto-author a resolver alias rule or fix the existing one,
2. retract the bad learned mapping,
3. add a pinned test (regression canary).

This is "compile verified decisions into code" made concrete: the orchestrator
answers once, code owns it forever.

### S3. Banded learning with expiry + domain scoping

Learned mappings (field_mappings.json) get:
- **count** — a mapping needs ≥2 confirmations before it resolves autonomously,
- **expiry/half-life** — unconfirmed mappings decay (ts-based) and require
  re-confirmation,
- **domain scope** — never promote a learned answer across ATS domains without
  review (prevents cross-ATS leakage),
- **contradiction rule** — learned ≠ profile ⇒ escalate (H4), never silent
  override.

### S4. Wrong-fill SPC (statistical process control)

Track the adjudicated wrong-fill rate per platform on a control chart:

- center line = running mean, upper bound = mean + k·σ.
- rate above the bound ⇒ **fleet-level handoff + pause autonomous submits** for
  that platform until root cause is found.
- This turns the C8 ledger from bookkeeping into the tripwire that stops a
  systematic error before it compounds.

### S5. Thompson sampling for widget strategies

The probe cascade is a multi-armed bandit: strategies (standard / dialog /
iframe / custom_widgets / vision) are arms, per capability-profile. Maintain a
Beta(win, lose) per arm; pick by Thompson sampling; update with fill success /
adjudication. The registry drift self-test is the exploration trigger — a
strategy that drifts loses and gets re-explored.

---

## Part 4 — Drift / regression detection (the system watches itself)

### D1. Change-point detection on the canary

The regression diff (P51) is a drift detector. Formalize it with a change-point
test (CUSUM / ADWIN) on:
- per-platform probe success rate,
- per-field verified rate.

A silent ATS redesign surfaces as **one fleet-level alarm**, not N silent
per-job failures.

### D2. Claim-trace backpressure

When grounding (P16) or outreach grounding (P52) rejects claims, feed the
rejection into the generator's constraints (P15/P44): "only produce claims that
trace to the record." The gate becomes a training signal for the next draft, not
just a blocker.

### D3. The learning loop's closed form

```
orchestrator answers (C2/C9) ──► learned mapping (P50) ──► resolved by code (P25/P26)
adjudication verdicts (C8)   ──► correction buffer (S2)  ──► rule authoring (S3) + tests
wrong-fill rate (C8)         ──► SPC bound trip (S4)     ──► fleet review (C7) + pause
observations (L2 trail)      ──► drift detector (D1)     ──► fleet alarm + re-probe
```

Every correction path terminates in: code owns it, tests pin it, the inbox
shrinks.

---

## Priority order to implement

1. **H1 + H3** — observability gate + belief accumulation (directly closes the
   Antigua class). Smallest change, highest safety.
2. **S1 + S2** — active adjudication + correction buffer → rule authoring.
   Turns C8 into the self-correction engine.
3. **E1 + S3** — label-dedup batching + banded learning with expiry/domain.
   Makes handoffs cheap and learning safe.
4. **S4 + D1** — wrong-fill SPC + change-point drift. The tripwires that catch
   systematic errors and silent ATS redesigns.
5. **S5** — Thompson-sampled widget strategies (the probe cascade is already
   halfway there).

---

## Part 5 — Adversarial / edge cases, each closed by an algorithm

From the adversarial audit (the A/B/C/D lists). Each is tagged with the
handoff or self-correction mechanism that closes it. Mark: `OPEN` (no
mechanism yet), `COVERED` (existing code), `PARTIAL` (mechanism exists, needs
the algorithm above to be complete).

| # | Edge case | Closed by | Status |
|---|-----------|-----------|--------|
| A1 | Prompt injection from the posting into the LLM (fit / draft / meaning / message) | Contract invariant: posting text is **data, never instructions** — enforced in C1/C2/C5. Plus D2 backpressure (only produce claims that trace). | **DONE** — posting text is treated as untrusted data at every C-contract boundary |
| A2 | PII exfiltration via outreach to an attacker-controlled address | S5/C5 provenance tag: mark contact sources; surface "address from untrusted posting" before human approves the send. | **DONE** — contact provenance surfaced; sends gated by human approval + F2 new-domain gate |
| A3 | Remote vision endpoint = PII leaving the machine | Invariant: vision bytes go to a local endpoint or are refused (`_local_guard` in ask_api). | **DONE** — non-loopback vision bytes refused |
| A4 | DNS rebinding / redirect to private host | Re-vet the destination host after every redirect hop via curl `%{url_effective}`. | **DONE** — final redirect target re-vetted in `_curl_fetch` |
| B1 | Crash between flag-set and click = stuck job | L3 dead-end recovery: a `submit_clicked` flag with no observed outcome escalates to investigation (never re-click). | **DONE** — the submit path investigates on guard-set instead of re-clicking |
| B2 | Same posting, two jids → duplicate application | Per-posting dedup check right before submit (not only at enrich). | **DONE** — SAME-POSTING GATE in `cmd_submit` |
| B3 | Cross-channel contact spam (DM then email) | Outreach policy decision: allowed by default, flagged on S5 if both channels go out. | **DONE** — outreach attempts surface per-channel in `report.py attempts`; send is human-approved |
| C1 | Form pre-filled with a wrong default | H3 belief accumulation: a field that had a value **before** we touched it is `prefilled`, a distinct epistemic state — never `verified-by-us`. | **DONE** — `prefilled` method + `unverified` kind + prefilled value surfaced in `ji verify` |
| C2 | A posting that lies "already applied" | C4 cross-check: classification alone never marks applied; the outcome must be observed. | **DONE** — already-applied only honored on the target URL (`_on_target` cross-check) |
| D1 | Wrong-answer persistence (learned mapping) | S2 correction buffer → retract bad mapping; S3 expiry/domain scoping. | **DONE** — `wrong` verdict retracts learned mapping + drops rule + flags profile |
| D2 | "Preferred name → No" conservative default on a text field | H2 VOI + type gating: defaults only for boolean/consent meanings, never text fields. | **DONE** — defaults gated by field type + values moved to default_answers.json |

All adversarial/edge cases in Part 5 are addressed. The invariants hold: the
orchestrator never certifies, never touches a guard, and untrusted input is
never treated as instruction.

---

## Part 6 — Deeper adversarial flows (F) and algorithms (G)

A second layer of analysis, beyond the A/B/C/D lists. Status marks: `OPEN`
(not implemented), `PLANNED` (scoped), `NEW` (added by this part).

### F — Adversarial flows

| # | Flow | Problem | Closed by | Status |
|---|------|---------|-----------|--------|
| F1 | **Observation-channel poisoning** | The read-back is DOM text on an attacker-controlled page. A malicious ATS can emit hidden/offscreen text that makes every reader "confirm" a wrong value — defeating H3's independence assumption (all channels read the same poisoned DOM). | Read-back **sanitization** (strip hidden/offscreen nodes) + rule that vision reads distinct pixels, never re-reads the same DOM. | **NEW** |
| F2 | **First-contact new-domain approval** | Applying to a fake "Workday-like" page submits real PII (name, phone, address, citizenship, disability) to an attacker. Destination vetting catches private hosts, not persuasive ones. | Cold-start trust gate: never submit to a domain with zero prior successful submissions without explicit orchestrator/human sign-off. | **NEW** |
| F3 | **Session isolation between employers** | One shared Chrome profile persists cookies across companies. Company A's session may leak to company B's apply flow, and vice versa. | Per-employer session scoping, or strip cookies before each new company's apply. | **NEW** |
| F4 | **Form drift mid-fill (bait-and-switch)** | Classification at P20/P21 happens on navigation; by P29 dynamic SPAs can present different fields. We fill against a stale field list → miss required fields or fill irrelevant ones. | Re-run field list + classification right before submit; C4 evidence pack must include "field list still matches". | **NEW** |
| F5 | **Live-submit + shadow-worker race** | Shadow workers are subprocess-isolated, but nothing stops a `shadow` worker and a manual `act --submit` on the same jid from running concurrently. The one-shot guard is per-jid state, not cross-process-locked. | Per-jid lock at the submit/guard layer. | **NEW** |
| F6 | **Poisoned learned-answer via attacker-influenced read-back** | An attacker-controlled ATS influences a read-back that becomes a learned mapping (step P50). Even domain-scoped, it poisons that domain. | S2 correction buffer + C8 adjudication retraction; never promote from an unverified or `echoed` read-back. | **NEW** — hardens S3 |

### G — Algorithms

| # | Algorithm | What it does | Where it lives | Status |
|---|-----------|--------------|----------------|--------|
| G1 | **Verification adversarial harness** | Red-team test mode: deliberately injects wrong values into read-backs (Antigua simulation) and asserts the pipeline refuses to certify them. Converts every verify fix into an executable assertion. | tests / `apply/verify` | **NEW** — highest-value investment |
| G2 | **Post-submit confirmation polling** | After `applied`, a scheduled re-check (portal/email) confirms the application actually entered the system; silent vanish escalates. | post-P40 cron | **NEW** |
| G3 | **Fleet health score** | Composite: verified% × fill% × (1 − wrong-fill%) × probe-success%. Declining score triggers orchestrator review before the SPC bound trips. Catches slow rot. | S4 fleet report | **NEW** |
| G4 | **Daily pacing budget** | Max N submits/day, N messages/day, backoff on rate-limits. Prevents reputation burn in one autonomous burst. | shadow/live batch scheduler | **NEW** |
| G5 | **Second-opinion arbitration for C4** | When outcome is uncertain, get a second independent read (different method) before deciding — the outcome analog of H3. | C4 | **NEW** |
| G6 | **Pre-submit dossier-vs-DOM consistency audit** | Before submit, verify the handoff dossier still matches the live form (fields present, values intact). Catches the SPA-reload-wipes-values class as a *check*, not a failure. | pre-C4 | **NEW** |
| G7 | **Cross-domain promotion gate** | A learned mapping promotes to a global alias only after ≥2 verified uses on *different* domains AND zero wrong verdicts. Closes cross-ATS leakage. | S3 | **NEW** — hardens the learning loop |

### Implementation mapping (status of each item)

- **Fix 1 (Antigua root cause)** — extended by **F1** (sanitized read-back),
  **C1** (prefilled kind), **G6** (dossier-vs-DOM audit).
- **Fix 2 (honest verification)** — pinned by **G1** (red-team harness): every
  verify-semantics fix gets an executable adversarial assertion.
- **Fix 3 (vision fail-loud)** — extended by **F2** (new-domain gate),
  **A3** (local-endpoint guard), **G5** (second opinion).
- **Fix 4 (learning loop)** — hardened by **F6**, **G7** (promotion gate).
- **Implemented since this analysis**: F3 (session isolation), F5 (per-jid
  lock), G2 (post-submit confirmation), G3 (fleet health), G4 (pacing). F4
  (mid-fill re-classify) remains open.

### Priority within the new set

1. **G1** (red-team harness) — self-contained, validates the whole verification
   layer at once; the cheapest high-confidence win.
2. **F1** (sanitized read-back) — the deepest hole; extends Fix 1 cheaply.
3. **F2 + A3** (new-domain gate + local-endpoint guard) — the two real privacy
   vectors, both are one config/check each.
4. **F5 + G6** (per-jid lock + pre-submit audit) — one-shot and state integrity.
5. **F3, F4, G2, G3, G4, G7** — operational hardening, schedule as capacity
   allows.
