# Pipeline Audit Report — CLI surface, dead flags, and handoff gaps

Date: 2026-08-04. Read-only audit of the live tree. Two parts:

1. **Part 1 — The Antigua incident**: root cause of a wrong country being filled
   and why nothing caught it.
2. **Part 2 — CLI surface cleanup**: every dead / no-op / misleading subcommand
   or flag, and the handoff-point gaps that confuse the orchestrator.

---

## Part 1 — The Antigua incident (why it happened, why nothing caught it)

### The incident
`apply.py shadow` filled a job's "Phone country code*" field with **Antigua and
Barbuda** instead of **Canada**. The dossier recorded it as `kind=verified`,
method=deterministic. This is exactly the class of bug shadow exists to catch —
and it was not caught.

### Root cause — two compounding bugs

**Bug A — the resolver returns a dialing code where the form wants a country.**
`apply/common/resolve.py:267-272` (step 1.5a): for any label containing both
"country code" and "phone", it extracts the `+N` prefix from the profile phone
(`+1 (343)...`) and returns `"+1"`. But a "Phone country code*" field is a
*country* dropdown (Canada, Antigua and Barbuda, …), not a dialing-code box. The
correct answer is the **country name** ("Canada"), which the resolver already
knows from `location: Ottawa, Ontario, Canada` — it just never offers it here.
`apply/common/match.py:129 country_iso()` exists precisely for intl-tel pickers
but is keyed off a *country* answer, not a `+1`.

**Bug B — the combobox matcher confirms the wrong value.**
`apply/common/value_reader.py:161-182` `FuzzyComboboxReader.read()`: for the
answer `+1`, it scans listbox options and returns the **first option whose text
contains `+1`**. Antigua & Barbuda's dialing code is `+1-268`; it appears before
Canada in the dropdown's DOM order. The read-back returns "Antigua and Barbuda",
which the fill path then treats as a successful read-back → `kind=verified`
(`fill_runner.py:518-522`, `fill.py:138`). The one-shot guard held (no submit),
but the *evidence trail lied*: the field was certified verified with the wrong
value.

### Why the existing checks didn't catch it
- **Cross-field check** (`apply/act/check.py`) checks *contradictions*, not
  *correctness*: "+1" for a phone is internally consistent, so nothing fires.
- **The check treats `verified` as trustworthy.** The kind came from the
  (wrong) read-back, so `held_shadow` → "READY TO SUBMIT". 53 jobs are in that
  bucket right now; every phone-country-code field among them is suspect.
- **ask_api was down** during the run, so the vision escape hatch — the one
  path that could have visually caught "Antigua" — was closed. `--quick` and
  `--no-verify` also close it silently.

### Fix (minimum)
1. `resolve.py` step 1.5a: for "phone country code" labels, resolve to the
   **country** (from `ephemeral.country` / profile `location`), not the `+N`
   prefix. Use `match.country_iso()` for the picker.
2. `FuzzyComboboxReader.read()`: when the option is a dialing-code+country
   string and the answer is a bare `+N`, pick the option whose **country name**
   matches the resolved country — never the first textual `+1` hit.
3. **Don't mark `verified` when the read-back is the *answer* rather than a
   genuinely distinct DOM observation.** A "verified" verdict must require the
   read value to be observed, not echo the input.
4. Make vision the automatic fallback for country/state/phone pickers when
   deterministic read-back is ambiguous — not an opt-in that silently closes.

---

## Part 2 — CLI surface audit

### A. Dead / no-op subcommands (delete)

| # | Command | Why it's dead | Evidence |
|---|---------|---------------|----------|
| A1 | `apply.py mappings confirm <jid>` | Pure stub. Prints "nothing pending — learned mappings are promoted automatically". There is no pending store anymore. | `apply.py:261-264` |
| A2 | `apply.py mappings list <jid>` | Requires a `jid` arg that is **never used** (reads the global `field_mappings.json`). The "list pending for a job" help is wrong — it's a global debug dump. | `apply.py:116-119` (parser), `apply.py:250-260` (handler) |
| A3 | `apply.py act` flag `--no-verify` vs `--quick` | Two flags that both mean "skip vision" with slightly different downstream behavior (`verify=` vs `quick=`), passed separately into `cmd_fill`. One flag should exist. | `apply.py:97-98`, `apply/act/__init__.py:25-29` |
| A4 | `extract.py` bare invocation | `extract.py` with no subcommand silently runs `cmd_auto()` (twice — `elif command is None` and the final `else`). An orchestrator typing `extract.py` gets an unexpected Gmail scrape. | `extract.py:395-400` |
| A5 | `extract.py help` | Hand-written help that omits `auto` and `--category`'s role, and shows a `gem →` column that's internal. Duplicates argparse help with drift risk. | `extract.py:337-355` |

### B. Confusing / misleading flags (fix or remove)

| # | Flag | Problem | Evidence |
|---|------|---------|----------|
| B1 | `enrich.py retry --curl` | **Unreachable.** `--curl` is a main-parser flag; `retry` is a subparser. `enrich.py retry --curl` exits with `unrecognized arguments: --curl` (verified by running it). The help text for `retry` never mentions it, but `cmd_retry(use_playwright=not args.curl)` reads it — a footgun. | `enrich.py:505,524,541,551-556` |
| B2 | `enrich.py` top-level flags `--force/--refresh/--verbose` only apply to the *bare* fetch; they silently do nothing under any subcommand. Subcommand parsers don't define them, but `argparse` accepts main-parser flags only in pre-subcommand position, so `enrich.py admit --force` errors while `enrich.py --force admit` also errors (unknown subcommand value) — confusing either way. | `enrich.py:505-507` |
| B3 | `apply.py mappings` `jid` positional — see A2; it forces the orchestrator to pass a job id to read a global file. | — |
| B4 | `tailor.py` has both a top-level `--jid` (craft one job) and a `retry <jid>` subcommand, plus `--auto`. Three ways to start tailoring with different semantics. `--jid` and bare `--auto` both exist as top-level flags, which argparse allows but reads oddly (`tailor.py --jid X` vs `tailor.py X`). | `tailor.py:586-592` |

### C. Duplicate / overlapping command surfaces (consolidate)

| # | Surface | Problem |
|---|---------|---------|
| C1 | **4× status commands**: `extract.py status`, `enrich.py status`, `reach.py status`, `report.py stats`. Three CLIs each implement `status` differently; `report.py stats` is the aggregate. The orchestrator cannot know which "status" is authoritative. Consolidate on `report.py stats`; strip `status` from the three CLIs (or make them thin wrappers). | `extract.py:315`, `enrich.py:384`, `reach.py:625`, `report.py:40` |
| C2 | **Two "what's ready to apply" views**: `report.py candidates` and `apply.py detect` auto-pick. `detect` with no jid auto-picks `jobs[0]` of stage tailored — an arbitrary job, no guard flags. `candidates` shows flags. `detect`'s silent auto-pick should require an explicit jid (or only be used in tests). | `apply.py:56-62` |
| C3 | `apply.py act --inspect` vs `act --investigate` vs `report.py inspect`. Three "inspect" concepts: page snapshot (`--inspect`), deep unknown-platform analysis (`--investigate`), DB record (`report.py inspect`). Same verb, three meanings. `--investigate` is a superset of `--inspect` in practice. | `apply/act/__init__.py:44-54` |

### D. Handoff-point gaps (where the orchestrator can't act)

The system's core loop is: fill → write dossier → `report.py handovers` → answer
with `--answers` → refill. The gaps that break this loop:

| # | Gap | Effect |
|---|-----|--------|
| D1 | **Dossier values are not echoed in the handoff** for `verified` fields when the read-back was empty. The handoff shows `value=None` next to `kind=verified`, so the orchestrator cannot see what was actually placed in the field. | Mis-trust of the dossier; "verified" is meaningless without the observed value. |
| D2 | **`report.py handovers` only surfaces `needs_data` and `rejected_by_form`** (`report.py:957`). A wrong-but-verified value (the Antigua case) never appears there — it's invisible to the owner-split. | Wrong values flow silently to "READY TO SUBMIT". |
| D3 | **No per-job "answer preview" command** before a live run. `report.py handoff` shows *labels + outcomes* but a wrong value requires reading the dossier JSON or the browser. The orchestrator needs `report.py answers <jid>` → the resolved label→value map (masked per rule 3: keys yes, but values *must* be visible here for review — this is the one sanctioned place). | No human-in-the-loop value review; exactly what caused this incident. |
| D4 | **Vision closed silently.** `ask_api` down ⇒ `--quick` ⇒ "vision reads closed" note appears in shadow output but nothing *requires* a value-review step before the fleet is marked READY TO SUBMIT. | The safety net has a silent off switch. |

### E. Verdicts / one-shot safety (confirmed working)
- `submit_clicked` one-shot guard, atomic `mark_applied` race guard, `--force`
  semantics, and the shadow never-submits contract are all correctly enforced
  (verified in `submit.py`, `helpers.py`).
- The incident was NOT a one-shot failure — it was an *evidence-truth* failure.
  Shadow did its job (no submit); the dossier lied about the value.

---

## Recommended action list (priority order)

1. **Fix the Antigua root cause** (Part 1, fixes 1-2) — wrong-country is the
   highest-risk bug in the system. Add a pinned test: phone-country-code label
   with `+1 (343)…` must resolve to country `Canada` and select Canada from an
   Antigua-first dropdown.
2. **Stop certifying `verified` on echoed answers** (Part 1, fix 3) — add a test
   where the read-back equals the input and the field must be `unverified`.
3. **Add `report.py answers <jid>`** — the one place values are shown for
   review (D3). Wire it into the READY-TO-SUBMIT path: shadow should not mark a
   fleet ready until its phone/country/legal fields have observed values.
4. **Delete**: `apply.py mappings` (all three actions), `extract.py` bare-invoke
   auto, `extract.py help` custom text, `enrich.py retry --curl` reachability
   trap (either hoist `--curl` into a shared subparser option or delete it).
5. **Consolidate**: one `status` (keep `report.py stats`), one `inspect` verb,
   one "no-vision" flag (`--quick`; delete `--no-verify`).
6. **Fix `detect` auto-pick** — require explicit jid.
7. Run `scripts/lint.py` + full pytest after any change (lint PASS is a
   precondition, not proof — verified 527 tests pass on the current tree).
