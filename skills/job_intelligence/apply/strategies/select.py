"""Select element strategy with fallback chain."""
import time

METHOD_CHAIN = ["select_option", "dispatch", "native_setter", "js_click"]


def try_select_tag(el, f, ans, method=None):
    if f["tag"] != "SELECT":
        return False
    try:
        values = ans if isinstance(ans, list) else [ans]
        selected = [next((o for o in f.get("options", []) if v.lower() == o.lower()), v) for v in values]
        final = selected if len(selected) > 1 else selected[0]

        if method is None or method == "select_option":
            el.select_option(final)
        elif method == "dispatch":
            el.select_option(final)
            el.evaluate("""el => {
                el.dispatchEvent(new Event('change', {bubbles: true}));
                el.dispatchEvent(new Event('input', {bubbles: true}));
            }""")
        elif method == "native_setter":
            el.evaluate("""(args) => {
                const el = args[0], val = args[1];
                el.value = val;
                el.dispatchEvent(new Event('change', {bubbles: true}));
                el.dispatchEvent(new Event('input', {bubbles: true}));
            }""", (el, final))
        elif method == "js_click":
            el.evaluate("""(args) => {
                const el = args[0], val = args[1];
                const opt = Array.from(el.options).find(o => o.value === val || o.text === val);
                if (opt) {
                    el.value = opt.value;
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                }
            }""", (el, final))
        else:
            return False

        time.sleep(0.1)
        current = el.evaluate("el => el.value")
        if current and current != f.get("value", ""):
            return True
        return False
    except Exception:
        return False
