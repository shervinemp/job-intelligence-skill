"""Select element strategy with fallback chain."""
import time

METHOD_CHAIN = ["select_option", "dispatch", "native_setter", "js_click"]


def _country_words(f):
    """Country words from the field's location context (fill_runner sets
    f['_country'] from profile location) — used to disambiguate a bare
    dialing code: 'Canada' must select 'Canada (+1)', never the first
    '+1' option (Antigua & Barbuda)."""
    try:
        cw = (f.get("_country") or "")
        if not cw and f.get("country"):
            cw = f["country"]
        import re
        return [w for w in re.split(r"[^a-z0-9]+", str(cw).lower())
                if len(w) > 2]
    except Exception:
        return []


def _pick_option(opts, v, country_words=None):
    """Pick the option for value v. Order:
      1. exact (case-insensitive)
      2. country-name match (when v is a bare code and country_words given)
      3. substring
      4. dialing-code-in-parens (v starts with +)

    CRITICAL (the Antigua fix): when the answer is a bare dialing code AND a
    country is known but NOT among the loaded options, we must NOT fall through
    to the bare-code match — "Canada" absent from a lazy-loaded A-list, then
    "+1" picking "Antigua & Barbuda (+1)", is the exact bug. Return None (no
    confident match) so the caller hands over to the orchestrator/vision
    instead of guessing."""
    vl = v.lower()
    # exact
    for o in opts:
        if vl == o.lower():
            return o
    # country-name match — the crucial disambiguation for +N answers.
    # ANY country word matching is enough: "Canada" in "Canada (+1)" even
    # when the location words also include "toronto"/"ontario".
    if country_words:
        for o in opts:
            ol = o.lower()
            if any(w in ol for w in country_words):
                return o
    # If a country is known but not loaded, do NOT fall to bare-code match.
    if country_words:
        return None
    # substring
    for o in opts:
        if vl in o.lower():
            return o
    # bare dialing code in parens: '(vl)' or ' vl'
    if vl.startswith("+"):
        for o in opts:
            ol = o.lower()
            if f"({vl})" in ol or f" {vl}" in ol:
                return o
    return None


def try_select_tag(el, f, ans, method=None):
    if f["tag"] != "SELECT":
        return False
    try:
        values = ans if isinstance(ans, list) else [ans]
        # Authoritative option set: a native <select> ALWAYS exposes ALL its
        # options via el.options (the probe-captured f["options"] may be a
        # lazy-loaded partial A-list — the Antigua root cause). Prefer the
        # native list; fall back to the captured one.
        opts = []
        try:
            opts = el.evaluate(
                "el => Array.from(el.options).map(o => o.textContent.trim())") or []
        except Exception:
            opts = []
        if not opts:
            opts = f.get("options", [])
        cw = _country_words(f)
        import sys as _sys
        print(f"  SELECT: ans={ans!r} country_words={cw} options={len(opts)}",
              file=_sys.stderr)
        for _o in opts[:8]:
            print(f"    OPT: {str(_o)[:80]!r}", file=_sys.stderr)
        for _o in opts:
            _ol = str(_o).lower()
            if any(w in _ol for w in cw):
                print(f"    COUNTRY-MATCH: {str(_o)[:80]!r}", file=_sys.stderr)
                break
        # Diagnostic: show any option containing a dialing code or 'can', to
        # reveal what Canada's actual option text is.
        for _o in opts:
            _ol = str(_o).lower()
            if "can" in _ol or "+1" in _ol:
                print(f"    +1/CAN: {str(_o)[:80]!r}", file=_sys.stderr)

        selected = []
        for v in values:
            match = _pick_option(opts, v, country_words=cw)
            selected.append(match or v)

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
                const vl = val.toLowerCase();
                let opt = Array.from(el.options).find(o => o.value === val || o.text === val);
                if (!opt) opt = Array.from(el.options).find(o => o.text.toLowerCase().includes(vl));
                if (!opt && vl.startsWith('+')) opt = Array.from(el.options).find(o => o.text.toLowerCase().includes('(' + vl + ')'));
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
