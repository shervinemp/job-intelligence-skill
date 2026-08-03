"""Combobox/dropdown fill — the adaptive protocol.

Single unified flow covering react-select, select2, Greenhouse typeaheads,
and any widget that opens a menu on click and filters on keystroke:

  1. Open the menu (click → Enter → mousedown escalation; portals delayed).
  2. Type the answer progressively (full text, then a yes/no-stripped
     variant) and poll for options — async typeaheads need the longer
     prefix and debounce time ("University" filters nothing, "University
     of Ottawa" yields the exact match).
  3. Score the visible options in PYTHON (accent/case-normalized word
     overlap) — click only when confident (best score, ≥1 margin over
     the runner-up). Ties stay unclicked and are recorded.
  4. Unfiltered fallback: if typing produced no options (or nothing
     confident), clear, reopen and score the FULL list — scrolling
     virtualized react-select listboxes — so option texts that differ
     from the answer phrasing ("Yes, I am legally authorized..." vs
     "I am legally authorized...") still match.
  5. Verify the selection via the value-reader cascade; a provably wrong
     option is retried; unverifiable selections are accepted but marked
     (check arbitrates later).

Everything is recorded in f['_diag'] for the audit log: the failure
stage (menu_closed / no_option_match / wrong_option), the option text
clicked, and how many options were seen.
"""
import json
import re
import sys
import time


def _trace(stage, *args):
    """Stage trace — printed to stderr AND emitted as an observation
    event (always on; the session log is the machine-readable view)."""
    msg = " ".join(str(a) for a in args)
    print(f"  [CBX:{stage}] {msg}", file=sys.stderr)
    try:
        from apply.common.obs import obs
        obs("combobox", stage, detail=msg)
    except Exception:
        pass


def _read_input(page, sel):
    """Read the combobox input's CURRENT value (for traces)."""
    try:
        return page.evaluate(
            f"document.querySelector({json.dumps(sel)})?.value || ''") or ""
    except Exception:
        return "?"


def _clear_input(page, sel):
    """Browser-native clear: focus + select-all + delete. Works for
    React-controlled inputs where setting .value directly does nothing
    (and silently corrupts subsequent typing)."""
    try:
        el = page.locator(sel).first
        try:
            el.click(timeout=1500)
        except Exception:
            pass
    except Exception:
        pass
    kb = page if hasattr(page, "keyboard") else getattr(page, "page", page)
    if hasattr(kb, "keyboard"):
        try:
            kb.keyboard.press("Control+A")
            kb.keyboard.press("Backspace")
        except Exception:
            pass
    time.sleep(0.25)
    _trace("clear", sel, "value_now=" + repr(_read_input(page, sel)))


def _norm(s):
    """Normalized text for scoring — shared matcher (apply.common.match)."""
    from apply.common.match import norm as _mnorm
    return _mnorm(s)


def _score_option(text, candidates_norm):
    """Shared scoring (exact/prefix/contains/signature/overlap)."""
    from apply.common.match import score_option as _mscore
    return _mscore(text, candidates_norm)


def _typing_candidates(ans):
    """Texts to type: full, yes/no-stripped, and alias type-forms that
    match the option labels' raw text."""
    from apply.common.match import typing_candidates as _mtc
    return _mtc(ans)


def _pick_best(options, candidates):
    """(best_option, runner_up_score) or (None, 0)."""
    from apply.common.match import scoring_candidates as _msc
    cnorms = _msc(candidates)
    best = None
    second = 0
    for o in options:
        sc = _score_option(o.get("text", ""), cnorms)
        if sc > (best["score"] if best else 0):
            second = best["score"] if best else 0
            best = dict(o, score=sc)
        elif best and sc > second:
            second = sc
    return best, second


def _listbox_root_id(page, sel):
    """The listbox element id (aria-controls/owns) for the input, or ''
    when none exists. Scoping collection to the listbox root is what
    separates REAL suggestions from page text ('Candidates may not
    apply more than 4 times...' noise)."""
    try:
        return page.evaluate("""(sel) => {
            //LISTBOXROOT
            const input = document.querySelector(sel);
            const owns = input ? (input.getAttribute('aria-controls')
                                  || input.getAttribute('aria-owns')) : null;
            const root = owns ? document.getElementById(owns) : null;
            return root && root !== document ? (root.id || '') : '';
        }""", sel) or ""
    except Exception:
        return ""


def _collect_visible_options(page, root_id=""):
    """Visible option dicts {text, id, x, y} from the listbox root (when
    known) or the whole document.

    Greenhouse renders options as bare <li> or <div class="Option"> without
    role=option; Ashby's portal listbox can use <button> items. Matching is
    therefore broad INSIDE the root; when no root exists the document scan
    stays as before (noise is filtered by the scorer + click verification)."""
    try:
        opts = page.evaluate("""(args) => {
            //COLLECT
            const [rootId] = args;
            const root = rootId ? (document.getElementById(rootId) || document) : document;
            const nodes = root.querySelectorAll(
                '[role="option"], li, [role="menuitem"], [class*="option"], '
                + '[class*="menu-item"], .select2-results__option, button');
            const out = [];
            for (const o of nodes) {
                if (o.offsetParent === null) continue;
                const cls = (o.className || '').toString().toLowerCase();
                const isOpt = o.getAttribute('role') === 'option'
                    || o.tagName === 'LI'
                    || o.tagName === 'BUTTON'
                    || cls.includes('option') || cls.includes('menu-item');
                if (!isOpt) continue;
                const t = (o.textContent || '').trim();
                if (!t || t.length > 120) continue;
                // react-select empty-state notices ("No options") are not
                // real options — their class is select__menu-notice.
                if (cls.includes('menu-notice') || cls.includes('no-options')) continue;
                if (o.getAttribute('aria-disabled') === 'true' || o.disabled) continue;
                const r = o.getBoundingClientRect();
                out.push({
                    text: t,
                    id: o.id || '',
                    x: Math.round(r.x + r.width / 2),
                    y: Math.round(r.y + r.height / 2),
                });
            }
            return out;
        }""", [root_id])
    except Exception:
        return []
    return opts or []


def _collect_with_scroll(page, sel, root_id="", max_passes=5):
    """Collect options from a (possibly virtualized) listbox, scrolling
    it between passes until the option set stabilizes."""
    if not root_id:
        try:
            root_id = page.evaluate("""(sel) => {
                //SCROLLROOT
                const input = document.querySelector(sel);
                const owns = input ? (input.getAttribute('aria-controls')
                                      || input.getAttribute('aria-owns')) : null;
                const root = owns ? document.getElementById(owns) : null;
                return root && root !== document ? (root.id || '') : '';
            }""", sel) or ""
        except Exception:
            pass
    seen = set()
    all_opts = []
    for _ in range(max_passes):
        opts = [o for o in _collect_visible_options(page, root_id)
                if o["text"] not in seen]
        if not opts:
            break
        all_opts.extend(opts)
        seen.update(o["text"] for o in opts)
        moved = False
        try:
            moved = page.evaluate("""(rootId) => {
                //SCROLLMOVE
                let root = rootId ? document.getElementById(rootId) : null;
                if (!root) root = document.querySelector('[role="option"]');
                let scroller = root;
                while (scroller && scroller !== document.body) {
                    if (scroller.scrollHeight > scroller.clientHeight + 5) break;
                    scroller = scroller.parentElement;
                }
                if (scroller && scroller !== document.body && scroller.scrollHeight > scroller.scrollTop + scroller.clientHeight) {
                    scroller.scrollTop = scroller.scrollHeight;
                    return true;
                }
                return false;
            }""", root_id)
        except Exception:
            pass
        if not moved:
            break
        time.sleep(0.25)
    return all_opts


def _open_menu(page, sel, root_id=""):
    """Click (with Enter escalation) until options are visible. Returns True
    if the menu is open and at least one option can be seen."""
    for attempt in range(2):
        try:
            el = page.locator(sel).first
            try:
                el.click(timeout=2000)
            except Exception:
                el.click(force=True, timeout=2000)
        except Exception:
            pass
        time.sleep(0.6)
        n = len(_collect_visible_options(page, root_id))
        _trace("open", sel, "click", "options=" + str(n))
        if n:
            return True
        try:
            kb = page if hasattr(page, "keyboard") else getattr(page, "page", page)
            if hasattr(kb, "keyboard"):
                kb.keyboard.press("Enter")
        except Exception:
            pass
        time.sleep(0.6)
        n = len(_collect_visible_options(page, root_id))
        _trace("open", sel, "enter", "options=" + str(n))
        if n:
            return True
    return False


def _close_menu(page):
    try:
        kb = page if hasattr(page, "keyboard") else getattr(page, "page", page)
        if hasattr(kb, "keyboard"):
            kb.keyboard.press("Escape")
    except Exception:
        pass
    time.sleep(0.2)


def _type_and_poll(page, sel, text, root_id=""):
    """Clear the input, type `text`, poll for options (async typeaheads
    debounce + fetch). Returns the visible options."""
    before = _read_input(page, sel)
    _clear_input(page, sel)
    after_clear = _read_input(page, sel)
    kb = page if hasattr(page, "keyboard") else getattr(page, "page", page)
    if hasattr(kb, "keyboard"):
        try:
            kb.keyboard.type(text, delay=15)
        except Exception:
            pass
    time.sleep(0.4)
    typed_value = _read_input(page, sel)
    try:
        page.evaluate(f"""() => {{
            const el = document.querySelector({json.dumps(sel)});
            if (el) el.dispatchEvent(new Event('input', {{bubbles: true}}));
        }}""")
    except Exception:
        pass
    _trace("type", repr(text), "before=" + repr(before),
           "after_clear=" + repr(after_clear), "after_type=" + repr(typed_value))
    for wait in (0.6, 1.4, 2.5):
        time.sleep(wait)
        opts = _collect_visible_options(page, root_id)
        if opts:
            _trace("poll", repr(text), f"+{wait}s", "options=" + str(len(opts)),
                   "first=" + repr(opts[0]["text"][:40]))
            return opts
    opts = _collect_visible_options(page, root_id)
    _trace("poll", repr(text), "final", "options=" + str(len(opts)))
    return opts


def _click_option(page, best):
    """Click escalation: id-locator → text-locator → RE-VERIFIED coordinate
    (rect re-read immediately before the click — stale coordinates from a
    scrolled menu are the 'clicked mid-text' bug)."""
    if best.get("id"):
        try:
            page.locator(f'[id="{best["id"]}"]').click(force=True, timeout=3000)
            time.sleep(0.3)
            _trace("click", "by-id", best.get("text", "")[:40])
            return True
        except Exception:
            pass
    text = best.get("text", "")
    if text:
        try:
            page.locator(f'[role="option"]:has-text("{text}")').first.click(
                force=True, timeout=2000)
            time.sleep(0.3)
            _trace("click", "by-text", text[:40])
            return True
        except Exception:
            pass
    if best.get("x") and best.get("y"):
        # Re-read the element rect right now; only click when it is still
        # in the viewport and roughly where we collected it.
        try:
            fresh = page.evaluate("""(text) => {
                const opts = document.querySelectorAll('[role="option"]');
                for (const o of opts) {
                    if (o.offsetParent === null) continue;
                    if ((o.textContent || '').trim().startsWith(text)) {
                        const r = o.getBoundingClientRect();
                        return {x: Math.round(r.x + r.width / 2),
                                y: Math.round(r.y + r.height / 2),
                                visible: r.width > 0 && r.height > 0};
                    }
                }
                return null;
            }""", text[:40])
            if fresh and fresh.get("visible"):
                page.mouse.click(fresh["x"], fresh["y"])
                time.sleep(0.3)
                _trace("click", "by-coord", text[:40])
                return True
        except Exception:
            pass
        _trace("click", "coord-skipped (stale)", text[:40])
    return False


def _read_selection_values(page, sel):
    """All readable texts describing the current selection: DOM readers,
    the input value, and the value-container (joined single-values or
    raw text). Returns a list of strings (possibly empty)."""
    values = []
    try:
        from apply.common.value_reader import (AriaComboboxReader,
                                               ReactSelectReader,
                                               FuzzyComboboxReader)
        for reader in (AriaComboboxReader(), ReactSelectReader(), FuzzyComboboxReader()):
            v = reader.read(page, sel, ans="")
            if v:
                values.append(str(v))
    except Exception:
        pass
    try:
        iv = page.evaluate("""(sel) => {
            const el = document.querySelector(sel);
            if (!el) return '';
            // Custom dropdown widgets are BUTTONs whose value attribute is a
            // framework-internal id (Workday React hash) — the selection's
            // identity is the button TEXT, not el.value.
            if (el.tagName === 'BUTTON') return (el.textContent || '').trim();
            return el.value || '';
        }""", sel)
        if iv:
            values.append(str(iv))
    except Exception:
        pass
    try:
        vc = page.evaluate("""(sel) => {
            const el = document.querySelector(sel);
            const scope = el ? (el.closest('.select__value-container')
                                || el.closest('.select__control')) : null;
            if (!scope) return '';
            const parts = Array.from(scope.querySelectorAll('.select__single-value'))
                .map(s => (s.textContent || '').trim()).filter(Boolean);
            const joined = parts.join(' ');
            const all = (scope.textContent || '').trim().slice(0, 120);
            return joined.length >= all.length ? joined : all;
        }""", sel)
        if vc:
            values.append(str(vc))
    except Exception:
        pass
    return values


def _verify(page, sel, ans, candidates):
    """True=selection verified, False=provably wrong, None=unverifiable.

    Verdicts use the SAME shared scoring that selected the option. Reads
    MULTIPLE sources: the DOM readers, the input's own value (react-select
    stores the label there), and the value-container's text — a reader can
    grab the wrong fragment ("Canada +1" → "+1")."""
    from apply.common.match import scoring_candidates as _msc
    cnorms = _msc(candidates)
    values = _read_selection_values(page, sel)
    for v in values:
        _trace("verify", "read", repr(str(v)[:40]))
    if not values:
        _trace("verify", "verdict=None (nothing readable)")
        # intl-tel country picker: the selection's identity is the flag
        # class (iti__ca = Canada). Verify it directly when the answer is
        # a country.
        try:
            from apply.common.match import country_iso
            iso = country_iso(ans)
            if iso:
                flag = page.evaluate("""() => {
                    const f = document.querySelector('.select__control .iti__flag, [class*="iti__flag"]');
                    return f ? (f.className || '') : '';
                }""")
                if iso in str(flag):
                    _trace("verify", "verdict=True (intl-tel flag)", iso)
                    return True
        except Exception:
            pass
        return None
    for v in values:
        if _score_option(v, cnorms) >= 2:
            _trace("verify", "verdict=True", repr(v[:40]))
            return True
    # A read with NO alphabetic characters ("+1", "★") is a FRAGMENT of
    # the selection (intl-tel flag+code badges never render the country
    # name), not a verdict — treat as unverifiable so a well-scored click
    # is accepted with a flag for check to arbitrate. Real words ("No")
    # stay provably-wrong.
    if all(not re.search(r"[a-z]", str(v).lower()) for v in values):
        _trace("verify", "fragment-only reads — unverifiable", repr(values[0][:40]))
        return None
    _trace("verify", "verdict=False (readable but no match)",
           repr(values[0][:40]))
    return False


def _try_click_best(page, sel, ans, opts, candidates):
    """Try options in score order (top-4), clicking and verifying each.
    Returns (True, option, verdict) on verified/accepted selection;
    (False, None, None) when nothing is confident or every click is
    provably wrong. verdict: True verified / None accepted-unverified."""
    from apply.common.match import scoring_candidates as _msc
    cnorms = _msc(candidates)
    scored = sorted(
        (dict(o, score=_score_option(o.get("text", ""), cnorms)) for o in opts),
        key=lambda o: o["score"], reverse=True)
    if not scored or scored[0]["score"] < 2:
        return False, None, None
    margin_ok = len(scored) < 2 or (scored[0]["score"] - scored[1]["score"]) >= 1
    if not margin_ok:
        return False, None, None
    for cand in scored[:4]:
        if cand["score"] < 2:
            break
        try:
            if _click_option(page, cand):
                time.sleep(0.5)
                v = _verify(page, sel, ans, candidates)
                if v is False:
                    continue  # provably wrong — try the next best
                return True, cand, v
        except Exception as _ce:
            _trace("click", "exception", str(_ce)[:80])
            continue
    return False, None, None


def _llm_pick(page, sel, opts, label, ans):
    """Expectation-free last resort — delegates to the shared library
    (lib.automation.llm.pick_option): real captured option texts + the
    question + the answer to the local LLM. No hardcoded expectations."""
    from lib.automation.llm import pick_option
    return pick_option(opts, label, ans)


def _try_llm(page, sel, opts, label, ans, candidates, diag, stage):
    """Click the LLM-chosen option, then verify by MECHANICAL IDENTITY:
    the selection must equal what we clicked (no semantic expectations on
    the LLM's choice — deterministic scoring can't judge it). Unreadable
    selections are accepted with a flag for check to arbitrate.
    Policy-gated (option_pick): only after the deterministic matcher
    found nothing — evidence records why it was skipped."""
    if not opts:
        return False
    try:
        from apply.common.llm_policy import allow as _llm_allow
        if not _llm_allow("option_pick"):
            diag["llm_tried"] = False
            diag["llm_skipped"] = "policy"
            return False
    except Exception:
        pass
    pick = _llm_pick(page, sel, opts, label, ans)
    try:
        from lib.automation.llm import last_status as _llm_status
        diag["llm_status"] = _llm_status()
    except Exception:
        pass
    diag["llm_tried"] = True
    if not pick:
        diag["llm_reply"] = "no_match"
        return False
    diag["llm_reply"] = pick.get("text", "")[:60]
    if _click_option(page, pick):
        time.sleep(0.5)
        picked_norm = _norm(pick.get("text", ""))
        values = _read_selection_values(page, sel)
        matched = any(
            picked_norm and (vn == picked_norm or vn in picked_norm or picked_norm in vn)
            for vn in (_norm(v) for v in values) if vn)
        if values and not matched:
            diag["reason"] = "llm_wrong"
            diag["after"] = pick.get("text", "")[:60]
            return False
        diag["reason"] = f"llm_{stage}"
        diag["after"] = pick.get("text", "")[:60]
        if not matched:
            diag["unverified"] = True
        return True
    return False


def fill(page, f, ans, time_budget=25.0):
    """Fill a combobox/dropdown widget. Returns True when a selection was
    made (verified or accepted-unverifiable). Records the failure stage
    in f['_diag'] for the audit log. time_budget bounds the per-field
    protocol so a stuck widget can't stall the job."""
    sel = f.get("_sel", "")
    diag = {"method": "combobox"}
    t0 = time.time()

    def _over_budget():
        return time.time() - t0 > time_budget

    if not sel:
        diag["reason"] = "no_selector"
        f["_diag"] = diag
        return False
    url_before = page.url
    try:
        el = page.locator(sel)
        if not el.count():
            diag["reason"] = "element_missing"
            f["_diag"] = diag
            return False

        _close_menu(page)
        opened = _open_menu(page, sel)
        # The listbox root (aria-controls/owns) scopes option collection —
        # without it, page text pollutes the candidates (the Ashby
        # "Candidates may not apply more than 4 times..." noise).
        root_id = _listbox_root_id(page, sel)
        typeahead = False
        if not opened:
            # Typeahead class (Location/City autocompletes): the menu only
            # appears AFTER typing — proceed without an open menu.
            try:
                typeahead = bool(page.evaluate("""(sel) => {
                    const input = document.querySelector(sel);
                    if (!input) return false;
                    const ac = input.getAttribute('aria-autocomplete');
                    const owns = input.getAttribute('aria-controls') || input.getAttribute('aria-owns');
                    return !!(ac && !owns);
                }""", sel))
            except Exception:
                pass
            if not typeahead:
                diag["reason"] = "menu_closed"
                f["_diag"] = diag
                return False
            diag["typeahead"] = True

        candidates = _typing_candidates(ans)
        typed_seen = 0
        last_opts = []
        for cand in candidates[:3]:
            if _over_budget():
                diag["reason"] = "slow"
                diag["detail"] = "per-field time budget exceeded"
                f["_diag"] = diag
                return False
            opts = _type_and_poll(page, sel, cand, root_id)
            typed_seen += len(opts)
            last_opts = opts
            if opts:
                ok, picked, verdict = _try_click_best(page, sel, ans, opts, candidates)
                if ok:
                    diag["reason"] = "typed_match"
                    diag["after"] = picked["text"][:60]
                    if verdict is None:
                        diag["unverified"] = True
                    f["_diag"] = diag
                    return True

        # Expectation-free fallback on the typed-filtered list.
        if _try_llm(page, sel, last_opts, f.get("label", ""), ans, candidates,
                    diag, "typed"):
            f["_diag"] = diag
            return True

        # Unfiltered fallback: clear, reopen, score the FULL list.
        if _over_budget():
            diag["reason"] = "slow"
            diag["detail"] = "per-field time budget exceeded before unfiltered pass"
            f["_diag"] = diag
            return False
        _close_menu(page)
        if _open_menu(page, sel, root_id):
            opts = _collect_with_scroll(page, sel, root_id)
            ok, picked, verdict = _try_click_best(page, sel, ans, opts, candidates)
            if ok:
                diag["reason"] = "unfiltered_match"
                diag["after"] = picked["text"][:60]
                if verdict is None:
                    diag["unverified"] = True
                f["_diag"] = diag
                return True
            if _try_llm(page, sel, opts, f.get("label", ""), ans, candidates,
                        diag, "unfiltered"):
                f["_diag"] = diag
                return True

        diag["reason"] = "no_option_match"
        diag["typed_options"] = typed_seen
        diag["options_seen"] = len(_collect_visible_options(page, root_id))
        diag["candidates"] = candidates[:3]
        try:
            from apply.common.match import scoring_candidates as _msc
            diag["top_options"] = [
                {"text": o.get("text", "")[:60],
                 "score": _score_option(o.get("text", ""), _msc(candidates))}
                for o in sorted(
                    (dict(o, score=_score_option(o.get("text", ""), _msc(candidates)))
                     for o in _collect_visible_options(page, root_id)),
                    key=lambda o: o["score"], reverse=True)[:3]]
            _sr = _read_selection_values(page, sel)
            diag["selection_readback"] = [str(s)[:40] for s in (_sr or [])]
        except Exception:
            pass
        f["_diag"] = diag
    except Exception as e:
        f["_diag"] = {"method": "combobox", "reason": "fill_exception",
                      "detail": str(e)[:120]}

    # Clear leftover typed text on failure (never wipes a success).
    _clear_input(page, sel)
    if page.url != url_before:
        try:
            page.goto(url_before, wait_until="domcontentloaded", timeout=15000)
        except Exception:
            pass
    return False
