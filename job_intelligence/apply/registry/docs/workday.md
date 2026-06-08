# Workday

**URL:** `myworkdayjobs.com` / `workday.com` → platform "workday"

**Flow:** navigate → fill (auto apply-link) → fill (apply manually) → [login] → fill × N → next × 6 → submit

**Form:** 7-step SPA. Each step reloads same page shell with different fields via JS.

**Step map:**
1. Apply Manually → account reg (email + password → user logs in)
2. My Info — 18+ fields: text, DROPDOWN (province, phone type), autocomplete (hear about, country code)
3. My Experience — skills autocomplete + resume upload (`set_input_files` on hidden input)
4. App Questions 1 — DROPDOWN (eligible? → Yes, sponsorship? → Yes per decisions.md)
5. App Questions 2
6. Voluntary Disclosures
7. Review → Submit

**Field types:**
| read_page | Element | Fill |
|-----------|---------|------|
| INPUT | std text | `el.fill(ans)` |
| DROPDOWN | `button[aria-haspopup="listbox"]` | Click btn → click `[role="option"]` |
| INPUT (ph="Search") | autocomplete multiselect | JS native value setter + input/change events |
| INPUT type="file" | hidden input behind btn | `set_input_files()` — no need to click btn |

**Gotchas:**
- 0 shadow DOM on form pages (verified)
- Login = per-company. Autodesk≠Workday. Must create account per company.
- "Save and Continue" advances. "Errors Found" = validation errors.
- SPA re-render: fields from previous step appear empty in read_page, but saved server-side.
- Phone: strip +1 prefix when Country Phone Code field present (separate field handles code).
- Phone Extension: optional, keep empty. `required` param in `_find_answer` prevents common_answers "phone" from filling it.
- Sponsorship: `will_require_sponsorship: Yes` (authorized now, expires → future need).
- Skills autocomplete: type skill, press Enter.
