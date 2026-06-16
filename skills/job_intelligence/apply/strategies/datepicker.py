"""Datepicker strategy (flatpickr)."""
def fill(page, sel, ans):
    try:
        page.evaluate("""(args) => {
            var sel = args[0], val = args[1];
            var el = document.querySelector(sel);
            if (!el) return;
            if (el._flatpickr) { el._flatpickr.setDate(val, true); return; }
            var fp = el.closest('.flatpickr');
            if (fp && fp._flatpickr) { fp._flatpickr.setDate(val, true); }
        }""", [sel, ans])
        return True
    except Exception:
        return False
