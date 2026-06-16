"""Text input fill strategies: visible fill, native setter, autocomplete."""
import time, random


def visible_fill(el, ans):
    try:
        if el.is_visible():
            el.fill(ans)
            return True
    except Exception:
        pass
    return False


def native_setter(page, sel, ans):
    try:
        page.evaluate(
            """(args) => {
            var ans = args[0], sel = args[1];
            var el = document.querySelector(sel);
            if (!el) return;
            el.focus();
            var n = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
            n.call(el, ans);
            el.dispatchEvent(new Event("input", { bubbles: true }));
            el.dispatchEvent(new Event("change", { bubbles: true }));
        }""", [ans, sel])
        return True
    except Exception:
        return False


def autocomplete(page, el, ans):
    try:
        el.click()
        time.sleep(0.3)
        el.press_sequentially(ans, delay=random.randint(40, 90))
        time.sleep(0.5)
        return True
    except Exception:
        return False


def fill_text_field(page, f, ans, sel, el):
    maxlen = el.get_attribute("maxlength") if el else None
    try:
        if maxlen and ans and len(ans) > int(maxlen):
            ans = ans[: int(maxlen)]
    except Exception:
        pass
    if f.get("placeholder") == "Search" or f.get("data_automation_id", ""):
        return bool(autocomplete(page, el, ans) or native_setter(page, sel, ans))
    return bool(visible_fill(el, ans) or native_setter(page, sel, ans))
