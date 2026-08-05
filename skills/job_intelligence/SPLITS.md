# Split Boundaries — modularity, control, observability

Where to cut the pipeline into separable seams, and why. The principle: **cut a
seam where the orchestrator needs a checkpoint, an audit boundary, or a
controlled mutation. Never split pure mechanics.**

A seam earns its cut by providing at least one of:
- **Control** — a clean place for the orchestrator to intervene (a Decide point
  becomes a hard stop).
- **Observability** — a complete, self-contained dossier at a boundary.
- **Auditability** — a mutation (fill, submit, learn) isolated so it can be
  verified and undone.

Mechanics (fetch, render, lookup, retry, arithmetic) provide none of these —
they stay monolithic.

---

## The five splits

### 1. Decision checkpoints → hard stage boundaries

Every Decide point becomes a real STOP: run stage → produce dossier → **wait
for the C-contract** → resume.

| Checkpoint | Gate | What waits for it |
|-----------|------|-------------------|
| C1 fit verdict | P13 | advance to described / reject |
| C2 answer resolution | P24 | fill+verify (per label) |
| C3 grounding | P17 | admit to tailored |
| C4 outcome verdict | P39 | mark applied / hold |
| C5 contact + message | P43/44 | stage / send |

- **Control**: the orchestrator never touches mid-stage state; it answers a
  bounded question, one line, then the stage resumes.
- **Observability**: every boundary emits a complete dossier (S2), so diff and
  regression read clean seams, not tangled logs.
- Today these are soft: `enrich` admit is interactive one-at-a-time; fill and
  verify are tangled in a 1399-line `fill.py`.

### 2. Read-model / write-model separation

The evidence trail (L2) becomes **write-only** from the steps; the report / `ji`
surfaces (S1–S8) become **read-only** views over it.

```
steps ──write──► L2 evidence trail ──read──► S1-S8 surfaces ──► orchestrator
```

- **Control**: surfaces can never corrupt execution — the orchestrator's window
  is a projection, not the database.
- **Observability**: every S-surface is derived, so the orchestrator sees
  exactly what the context budget allows, and nothing else.
- Structurally kills the "six `status` variants" problem: one trail, many views.

### 3. Fill vs Verify as separate units

Split the fill path: **resolve-and-enter** (mutates the form) from
**read-back-and-certify** (observes and stamps `verified`).

- **Auditability**: the `verified` stamp becomes its own auditable transaction —
  exactly what was entered vs what was confirmed. This is the Antigua seam.
- **Control**: the H3 belief-accumulation algorithm slots cleanly into Verify;
  Verify can be gated or vetoed without touching Fill.

### 4. Learning as an isolated store + controller

Separate what is now mixed in resolver code:
- **the store** — learned mappings, alias rules, expiry,
- **the read API** — resolution consumes it,
- **the write API** — adjudication retracts, C2 answers compile, expiry reaps.

- **Control**: the S1–S3 correction loop (retraction, banding, domain scope) is
  contained in one place; a bad learned answer is removed without hunting
  through resolver code.
- **Observability**: every resolved answer carries provenance
  (profile / learned / default / orchestrator) — auditable *why*.

### 5. Submit/outcome as an atomic transaction unit

Make submit a hard seam: set guard → click → observe → verdict, and **nothing
else can write job state during it**.

- **Auditability**: the one-shot integrity (L3) is provable in isolation — the
  reason `--force` semantics are currently hand-audited.
- **Control**: the C4 outcome verdict is the only way into the result; crash
  recovery and investigation operate on the transaction record, not live state.

---

## What must NOT split

- **Pure mechanics** — fetch, render, lookup, arithmetic, bounded retry
  (P8, P10, P18, P25–27, P35, P53). No control/observability gain.
- **The probe cascade** (widget strategies) — one adaptive capability;
  splitting breaks the bandit algorithm (S5).
- **Cross-cutting invariants** (L3 guard, L4 persistence) — shared by nature.

---

## The shape it implies

```
write-path steps ──► L2 evidence trail (write-only)
                        │
                        ▼
                 report/ji read-models (S1-S8)
                        │
orchestrator ── C1-C9 ──┤   ← each C = one hard checkpoint
                        │
                 learning store (read/write API, self-correction)
```

Each split boundary corresponds to a `ji` command — the CLI surface and the
architecture become the same seams, not two different mental models.

---

## Mapping to the graph

- **Checkpoints**: C1–C5 nodes are drawn as the hard STOP gates between stages
  (diamond outline).
- **Read/write arrows**: steps → L2 are `write` arrows; L2 → S-surfaces are
  `read` arrows (direction matters, unidirectional).
- **Fill/Verify seam**: P29 (fill) and P30–P32 (verify) are split visually with
  the seam marked.
- **Learning store**: shown as the isolated controller P50 + store, fed by C2/C8
  and read by resolution P25/P26.
