"""Select element strategy. DROPDOWN handled by strategies/dispatch.py routing to combobox module."""
import time


def try_select_tag(el, f, ans):
    if f["tag"] != "SELECT":
        return
    try:
        values = ans if isinstance(ans, list) else [ans]
        selected = [next((o for o in f.get("options", []) if v.lower() == o.lower()), v) for v in values]
        el.select_option(selected if len(selected) > 1 else selected[0])
        # Verify value persisted
        time.sleep(0.1)
        current = el.evaluate("el => el.value")
        if not current or current == f.get("value", ""):
            el.evaluate("""el => {
                el.dispatchEvent(new Event('change', {bubbles: true}));
                el.dispatchEvent(new Event('input', {bubbles: true}));
            }""")
        return True
    except Exception:
        return False
