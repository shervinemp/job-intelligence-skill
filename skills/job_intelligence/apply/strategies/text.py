"""Text input fill strategies: visible fill, native setter, autocomplete, dispatch."""
import re, time, random

METHOD_CHAIN = ["fill", "native_setter", "autocomplete", "dispatch_events"]


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


def dispatch_events(page, sel, ans):
    try:
        page.evaluate(
            """(args) => {
            var ans = args[0], sel = args[1];
            var el = document.querySelector(sel);
            if (!el) return;
            el.value = ans;
            ["input", "change", "blur", "keydown", "keyup"].forEach(t =>
                el.dispatchEvent(new Event(t, { bubbles: true }))
            );
        }""", [ans, sel])
        return True
    except Exception:
        return False


def _verify(el, ans):
    try:
        time.sleep(0.1)
        current = el.evaluate("el => el.value")
        return current == ans or (current and len(current) >= len(ans) * 0.8)
    except Exception:
        return False


def fill_text_field(page, f, ans, sel, el, method="fill"):
    _orig_ans = ans

    label = (f.get("label") or f.get("name") or "").lower()

    # Phone number: strip to digits only, then format as E.164
    # (keep the country code, strip formatting). E.164 is the universal
    # format accepted by all ATS. Profile value e.g. "+1 (343) 558-1744"
    # becomes "+13435581744".
    if re.search(r"phone|contact|mobile|cell", label):
        digits = re.sub(r"\D", "", ans)
        if 7 <= len(digits) <= 15:
            ans = "+" + digits

    # Postal code: strip spaces for maxlength=6 fields (Canadian format).
    # Filling "K2P 1J6" (7 chars) into a maxlength=6 field overflows and
    # truncates to "K2P 1J". Stripping the space gives "K2P1J6" (6 chars).
    # The site may auto-insert the space on display.
    if re.search(r"postal|zip|code", label):
        ans = ans.replace(" ", "")

    maxlen = el.get_attribute("maxlength") if el else None
    try:
        if maxlen and ans and len(ans) > int(maxlen):
            # Semantic rescue: maxlength=4 with "Immediately"/"ASAP"/"Now"
            # → this is almost certainly a year field (YYYY).
            if int(maxlen) <= 4 and re.search(r"year", label):
                from datetime import datetime as _dt
                ans = str(_dt.now().year)
            else:
                from apply.common.output import emit_diag
                emit_diag(f.get("label", f.get("name", "?")), _orig_ans,
                          ans[:int(maxlen)], "truncated", f"maxlength={maxlen}")
                f["_diag"] = {"method": method, "reason": "truncated",
                              "before": "", "after": ans[:int(maxlen)]}
                ans = ans[: int(maxlen)]
    except Exception:
        pass

    if method == "fill":
        if f.get("placeholder") == "Search" or f.get("data_automation_id", ""):
            ok = bool(autocomplete(page, el, ans))
        else:
            ok = bool(visible_fill(el, ans))
    elif method == "native_setter":
        ok = bool(native_setter(page, sel, ans))
    elif method == "autocomplete":
        ok = bool(autocomplete(page, el, ans))
    elif method == "dispatch_events":
        ok = bool(dispatch_events(page, sel, ans))
    else:
        return False

    if ok and ans:
        if not _verify(el, ans):
            from apply.common.output import emit_diag
            current = el.evaluate("el => el.value") if el else ""
            emit_diag(f.get("label", f.get("name", "?")), ans,
                      current or "(empty)", "verify_failed",
                      f"method={method} maxlen={maxlen}")
            f["_diag"] = {"method": method, "reason": "verify_failed",
                          "before": "", "after": current or ""}
            native_setter(page, sel, ans)
            return _verify(el, ans)
    return ok
