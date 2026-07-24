"""Datepicker strategy (flatpickr)."""
def fill(page, sel, ans):
    try:
        ok = page.evaluate("""(args) => {
            var sel = args[0], val = args[1];
            var el = document.querySelector(sel);
            if (!el) return false;
            if (el._flatpickr) { el._flatpickr.setDate(val, true); return true; }
            var fp = el.closest('.flatpickr');
            if (fp && fp._flatpickr) { fp._flatpickr.setDate(val, true); return true; }
            return false;
        }""", [sel, ans])
        return bool(ok)
    except Exception:
        return False
