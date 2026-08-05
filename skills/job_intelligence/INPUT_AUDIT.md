# Input-Kind Sniffing & Prepared-Answer Audit

Honest audit of two things you asked about: (1) over-reliance on prepared
answers, and (2) how the pipeline detects and handles the DIFFERENT KINDS of
input widgets. Every claim below is checked against the live code.

---

## Part 1 — Prepared answers: where we over-rely, and the recovery path

### The failure you saw (Antigua, again)
The resolver returns "Canada" → the field's loaded options don't include Canada
(a lazy-loaded A-list) → my prepared `dialing-codes` map converted "Canada" to
"+1" → `+1` matched the first loaded +1 option → **Antigua** → verification
caught it (not certified) but the wrong click happened.

### The honest verdict on prepared answers
`_pick_option` (select.py) now refuses the bare-code fallback when a country is
known-but-not-loaded — it returns None (handover) instead of guessing. That is
the correct change: **a prepared answer (`+1`) is only safe when the option it
maps to is actually present and matches the country.** The general rule, now
implemented:

- **Prepared answer + option present + country matches** → use it (verified).
- **Prepared answer + option NOT present** → return None → **handover to the
  orchestrator / vision**, never fall through to a partial text match.

### The residual over-reliance (what's still risky)
| Prepared value | Where | Risk |
|---|---|---|
| `dialing-codes` map | fill_runner country_code fallback | ONLY safe if the country option is loaded; now guarded (returns None if not). |
| Conservative defaults (`No`) | resolve Step 6 | Gated to boolean/consent widgets; values in data. Safe. |
| `_DEFAULT_ANSWERS` consent `true` | fill_runner | Gated to JI_AUTO_CONSENT + consent label. Safe. |
| Date derivation (current month/year) | resolve Step 2.5 | "Start date month" → now; a form wanting a PAST date gets a wrong-but-verified value. **Not guarded.** |
| Pronoun check (`Yes`) | resolve Step 1.7 | CHECK-positive only; safe by construction. |

**Open gap:** the date-derivation (Step 2.5) returns the current month/year
for any "start date month/year" field — a form asking for a historical start
date would get `now` and be certified. It needs the same "is this option
present / does the form context match" guard, else hand over.

---

## Part 2 — Input-kind sniffing: what we detect vs. what we don't

### What the pipeline DOES sniff

| Input kind | Detection | Handler |
|---|---|---|
| Native `<select>` | `tag == "SELECT"` | `try_select_tag` (select.py) |
| Combobox / dropdown | `role == "combobox"` or `tag == "DROPDOWN"` (`field_types.is_combobox`) | combobox.py protocol |
| Radio group | `type == "radio"` / RADIO_GROUP | RadioFiller |
| Checkbox / consent | `type == "checkbox"` + consent keywords | CheckboxFiller / AshbyYesNo |
| File upload | `accept` / `type == "file"` | upload filler + file-chooser intercept |
| Text input | INPUT/TEXTAREA | TextFiller |
| **Typeahead / autocomplete** | `aria-autocomplete=list/both` OR placeholder "Start typing..." OR "city or location" label (filler.py:621-643) | AutocompleteFiller (types char-by-char, clicks first suggestion, VERIFIES via ARIA/React readers, tries 2nd if provably wrong) |

### What the pipeline does NOT sniff (gaps)

| Gap | What happens now | Why it's a hole |
|---|---|---|
| **Lazy-loaded / paginated option lists** (the Antigua root cause) | Reads only the currently-DOM options (first ~15 A-list); Canada absent → falls to bare-code → wrong pick (now: handover) | Native `el.options` is complete, but **combobox** option collection only grabs what's in the listbox DOM at that moment. A country outside the first page is invisible. |
| **Typing-then-wiped inputs** (type the wrong thing, site clears it) | `_check_delta` detects "cleared" (before set, after empty) and "unchanged" — records it as a failed field | Detection exists; but the FIELD then needs the orchestrator to re-answer, and we don't auto-retry with a different strategy. |
| **Double-typed / interleaved text** (previous attempt left "OttawaOttawa" or "OtOttawat" ) | `visible_fill` (el.fill) REPLACES the value — Playwright's fill clears first, so no doubling **for standard inputs**. BUT `native_setter` (el.value=) may append, and typeahead `el.type` after `el.fill("")` is clean only if the clear worked. | If the site rejects the clear (React-controlled), `el.fill("")` can fail silently → the typed value appends to leftover → dirty read-back. **No explicit "clear THEN verify-empty THEN type" sequence** in the autocomplete path. |
| **Dynamic lists that only appear when typing** | AutocompleteFiller handles "type → suggestions appear" | Correct, but the first-suggestion click is legacy; the VERIFY step decides. If the right option is 3rd (not 1st/2nd), we give up (fill.py:712-715) rather than scanning all visible options. |
| **Datalist (`<datalist>`)** | Not specially handled — falls to TextFiller | A datalist-backed input accepts the typed value; filling it as plain text usually works, but the offered options are never used for validation. |

---

## Part 3 — What to do (robust, not more prepared answers)

1. **Combobox option pagination (the real Antigua fix).** When the country
   isn't among loaded combobox options, TYPE THE COUNTRY NAME into the field to
   trigger the lazy load, then re-collect options — not just hand over. This is
   what `AutocompleteFiller` already does; the combobox protocol should use the
   same "type-to-reveal" trick when the direct match is absent.
2. **Clear-then-verify-then-type.** Add an explicit sequence before every fill:
   `el.fill("")` → read-back empty? → `el.type(ans)` → read-back == ans. If the
   clear failed (React), use keyboard select-all + delete, not `el.fill`.
3. **Scan ALL visible options, not just 1st/2nd.** When verification says the
   first option is wrong, iterate every visible option and verify each — not
   just index 0 and 1.
4. **Guard the date-derivation** (Step 2.5): only return `now` for start-date
   when the form is asking about start availability; else hand over.
5. **Datalist support:** detect `<datalist>` and use its options for
   verification (a typed value that matches an option is verifiable).

Part 1 and items 1-4 are the concrete code changes; item 5 is lower priority.
