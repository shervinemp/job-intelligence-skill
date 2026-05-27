# Greenhouse

**URL:** `greenhouse.io` → platform "greenhouse"

**Flow:** navigate → fill → submit

**Form:** 28-29 fields pre-loaded in DOM. Always visible — no "Apply" click needed. Submit = "Submit application" (type="submit").

**Gotchas:**
- "Country" is `<input type="text">` autocomplete, not `<select>`. Type name.
- No `<select>` elements at all. All dropdowns = custom autocomplete.
- "Apply" button at top = scroll trigger. Don't click. Use "Submit application" at bottom.
- **cmd_submit priority:** "Submit application" > "Apply" (fixed. "Apply" scroll trigger filtered out.)
