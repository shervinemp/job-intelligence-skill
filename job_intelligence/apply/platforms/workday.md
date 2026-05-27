# Workday

**Detection:** `myworkdayjobs.com` or `workday.com` in URL → platform "workday"

**Form structure:** 7-step multi-page SPA form. Each step reloads the same page shell with different fields via JavaScript.

**Entry points:**
1. LinkedIn → "Apply on company website" → safety redirect → Workday job page
2. Direct URL to Workday job page

**Step-by-step flow:**

1. **Job page** → "/apply" URL → shows "Autofill with Resume", "Apply Manually", "Use My Last Application"
2. **Apply Manually** → SPA click reveals account registration (step 1 of 7)
   - Fields: Email, Password, Verify Password, checkbox, bot detection
   - **Account required** — user must log in or create an account
   - `LOGIN_WALL` detected if "create account" or "sign in to apply" in text
3. **My Information** (step 2 of 7) — 18+ fields
   - Standard text inputs (name, address, city, postal code)
   - **DROPDOWN** fields (detected via `button[aria-haspopup="listbox"]`): Province, Phone Device Type, Country
   - **Autocomplete** fields (detected via placeholder="Search"): How Did You Hear, Country Phone Code
   - Phone Number should NOT include country code prefix (+1), since Country Phone Code is separate
   - Phone Extension should be empty (optional field, common_answers "phone" prefix matches it — guarded by `required` parameter: common_answers prefix only fills required fields)
   - Sponsorship question ("Will you require sponsorship?") → **Yes** per decisions.md (authorized now but expires)
4. **My Experience** (step 3 of 7)
   - Skills: autocomplete field — type skill and press Enter
   - Resume upload: hidden `input[type="file"]` behind "Select files" button. `set_input_files()` works.
5. **Application Questions 1 of 2** (step 4 of 7) — DROPDOWN fields
6. **Application Questions 2 of 2** (step 5 of 7)
7. **Voluntary Disclosures** (step 6 of 7)
8. **Review** (step 7 of 7) → Submit

**Field type mapping:**
| read_page tag | Workday element | How to fill |
|--------------|----------------|-------------|
| INPUT | Standard text input | `el.fill(ans)` |
| DROPDOWN | `button[aria-haspopup="listbox"]` | Click button → click `[role="option"]` matching answer |
| INPUT (placeholder="Search") | Autocomplete multiselect | Native value setter + dispatch input/change events |
| INPUT type="file" | Hidden file input behind button | `set_input_files(path)` on hidden input |

**Known quirks:**
- 0 shadow DOM on form pages (confirmed via investigation)
- Login required per-company (Autodesk login != Workday login)
- Form advances via "Save and Continue" button (S C A N score varies)
- "Errors Found" button appears when validation fails — check `error_btns` in `_handle_post_click`
- Each step re-renders SPA — fields from previous step may appear empty in read_page but are saved server-side

**Pipeline flow:** `navigate → act --fill (auto apply-link) → act --fill (apply manually) → [login] → act --fill × N → act --next × 6 → act --submit`
