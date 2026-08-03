"""FieldFiller registry — extensible fill strategy chain with built-in verification.

Each filler implements:
  name         → traceable tag for LLM output
  can_handle   → does this filler apply to this field?
  fill         → attempt and return True/False

Fillers are tried in registry order. The last filler (NativeSetterFallback) always
matches, so the chain is always complete.

fill_field() wraps the chain with pre/post-fill verification:
  - Reads value before fill
  - Dispatches to the matching filler
  - Reads value after fill
  - Verifies the value changed (delta check)
  - Falls back to text strategies if the filler reported success but value didn't stick

This merges the former dispatch.py verify logic into the filler itself,
eliminating the two-layer indirection (act → dispatch → filler → strategy).
"""
from abc import ABC, abstractmethod
import json
import os
import time

from apply.common.field_types import is_combobox as _is_combobox
from apply.common.value_reader import read_value as _read_value
from apply.common.output import emit_diag
from apply.steps.probe import resolve_selector
from apply.strategies import combobox, text, select, contenteditable as ce


# ─── Frame helpers ────────────────────────────────────────────────────

def _frame_for_sel(page, sel):
    """Find Playwright frame containing element matching sel."""
    for f in page.frames:
        try:
            if f.evaluate(f"() => !!document.querySelector({json.dumps(sel)})"):
                return f
        except Exception:
            continue
    return None


def _read_element_value(page, sel, ans=None, field=None):
    """Read field value using the deterministic FieldValueReader cascade.
    Combobox-role fields skip StandardReader (phantom-success source).
    When the deterministic cascade comes back empty and an expected value
    exists, the runner invokes the explicit gated vision escape — never
    the other way around."""
    try:
        fr = _frame_for_sel(page, sel) or page
        if field is not None and _is_combobox(field):
            from apply.common.value_reader import AriaComboboxReader, ReactSelectReader, FuzzyComboboxReader
            for reader in (AriaComboboxReader(), ReactSelectReader(), FuzzyComboboxReader()):
                v = reader.read(fr, sel, ans=ans)
                if v:
                    return v
            return ""
        v = _read_value(fr, sel, ans=ans)
        if v:
            return v
        if ans:
            from apply.common.value_reader import read_value_vision
            vv = read_value_vision(fr, sel, ans=ans)
            if vv:
                return vv
        return ""
    except Exception:
        return ""


def _check_delta(before, after, ans, label, field=None):
    """(True, '') if the value changed meaningfully, else (False, reason)
    where reason is the diagnostic tag (truncated / unchanged / still_empty
    / cleared / wrong_option / verify_failed) for the audit log."""
    if isinstance(ans, list):
        ans = ", ".join(str(v) for v in ans)
    elif ans is not None:
        ans = str(ans)
    # File inputs: browser reports C:\fakepath\filename instead of full path
    if after and ans and ans.lower().endswith((".pdf", ".doc", ".docx", ".txt", ".rtf")):
        after_lower = after.lower()
        ans_lower = ans.lower()
        if os.path.basename(ans_lower) in after_lower or after_lower in ans_lower:
            return True
    # Radio/select: value must match ans (or be a yes/no variant), not just
    # any change. Prevents false success when the wrong option was clicked.
    _is_choice = field and (field.get("tag") in ("RADIO_GROUP", "SELECT", "DROPDOWN")
                            or field.get("type") == "radio"
                            or field.get("role") == "combobox")
    if _is_choice and ans:
        ans_l = ans.lower().strip()
        after_l = (after or "").lower().strip()
        if after_l and (after_l == ans_l or ans_l in after_l or after_l in ans_l):
            return True, ""
        if after_l in ("yes", "no") and ans_l.startswith(("yes", "no")):
            return True
        # Yes/No answer with full-sentence option text: check by negation.
        # e.g. ans="Yes", after="I am based in Ottawa" (no negation → match)
        # e.g. ans="No", after="I am not based in Ottawa" (has negation → match)
        if ans_l in ("yes", "no") and after_l and after_l not in ("yes", "no"):
            import re as _re
            _neg = bool(_re.search(r'\b(not|dont|don\'t|never|unable|cannot|can\'t)\b', after_l))
            if (ans_l == "yes" and not _neg) or (ans_l == "no" and _neg):
                return True, ""
        if after_l and after_l != before:
            emit_diag(label, ans, after, "wrong_option", "clicked option does not match answer")
            return False, "wrong_option"
    if after and after != before and after != ans:
        return True, ""
    if after and ans and (after == ans or ans in after or after in ans):
        return True, ""
    # Postal/zip: strip spaces from both sides before comparing
    # (text.py strips spaces from postal values; verification sees
    # the original value with space still in ans).
    import re as _re
    if after and ans and _re.search(r"postal|zip|code", label, _re.I):
        clean_a = after.replace(" ", "")
        clean_ans = ans.replace(" ", "")
        if clean_a == clean_ans or clean_ans in clean_a or clean_a in clean_ans:
            return True, ""
    if after == before and label:
        if before:
            emit_diag(label, ans, before, "unchanged", "ATS may have rejected the value")
            return False, "unchanged"
        else:
            emit_diag(label, ans, "(empty)", "still_empty", "ATS silently rejected value")
            return False, "still_empty"
    if not after and before:
        emit_diag(label, ans, "(empty)", "cleared", "ATS silently reset the value")
        return False, "cleared"
    return True, ""


# ─── Filler ABC ───────────────────────────────────────────────────────

class FieldFiller(ABC):
    @property
    @abstractmethod
    def name(self): ...

    @abstractmethod
    def can_handle(self, field: dict) -> bool: ...

    @abstractmethod
    def fill(self, page, field: dict, ans: str) -> bool: ...


# ─── Concrete fillers ─────────────────────────────────────────────────

class CheckboxFiller(FieldFiller):
    name = "checkbox"

    def can_handle(self, f):
        # Any real checkbox with an explicit resolved answer (yes/no/true/
        # false) is handled. The label-consent restriction lives at the
        # ANSWER level (JI_AUTO_CONSENT only auto-fills consent-labeled
        # checkboxes) — an explicit answer is always safe to apply.
        return f["tag"] == "INPUT" and f.get("type") == "checkbox"

    def fill(self, page, f, ans):
        sel = f.get("_sel", "")
        try:
            cb = page.locator(sel)
            if not cb.count():
                return False
            want_checked = str(ans).strip().lower() in ("true", "1", "yes", "y", "checked")
            if want_checked and not cb.is_checked():
                try:
                    cb.check(force=True)
                except Exception:
                    pass
                if not cb.is_checked():
                    # Hidden/React checkbox: check() on a display:none input
                    # may not trigger the framework state. Click the label
                    # the user would click (label[for=id] or closest label).
                    try:
                        _id = cb.get_attribute("id")
                        if _id:
                            lbl = page.locator(
                                f'label[for="{_id.replace(chr(34), "")}"]').first
                            if lbl.count():
                                lbl.click(force=True, timeout=3000)
                    except Exception:
                        pass
                return cb.is_checked()
            if not want_checked and cb.is_checked():
                cb.uncheck(force=True)
                return True
            return True  # already in the desired state
        except Exception:
            return False


class RadioFiller(FieldFiller):
    name = "radio"

    def can_handle(self, f):
        return f.get("tag") == "RADIO_GROUP" or f.get("type") == "radio"

    def fill(self, page, f, ans):
        sel = f.get("_sel", f.get("selector", ""))
        if not sel:
            return False
        ans_lower = str(ans).strip().lower()
        yn_prefix = ""
        if ans_lower.startswith("yes"):
            yn_prefix = "yes"
        elif ans_lower.startswith("no"):
            yn_prefix = "no"
        # EEOC phrasing derivation: "I do not have a disability" IS a No,
        # "I have a disability" IS a Yes — the option text is just Yes/No.
        if not yn_prefix:
            import re as _re
            if _re.search(r"\bi do(n't| not)? have( any| no)?\b|\bwe do(n't| not)? have\b|\bi have not\b|\bi have never\b", ans_lower):
                yn_prefix = "no"
            elif _re.search(r"\bi (am|do) not\b.*\b(disclos|wish|willing)", ans_lower):
                pass
            elif _re.search(r"\bi have\b|\bi identify\b|\byes\b", ans_lower):
                yn_prefix = "yes"
        country = (f.get("_country") or "").lower()
        try:
            # Step 1: find the matching radio's id via JS (no click — React
            # doesn't pick up .click() from page.evaluate, only Playwright's
            # native .click() goes through the full browser event pipeline).
            result = page.evaluate("""(args) => {
                const [sel, ans, ynPref, country] = args;
                const radios = [...document.querySelectorAll(sel)];
                if (!radios.length) return null;

                function radioLabel(r) {
                    if (r.id) {
                        const lbl = document.querySelector('label[for="' + r.id + '"]');
                        if (lbl) {
                            const txt = lbl.textContent.trim().toLowerCase();
                            if (txt && txt !== 'on') return txt;
                        }
                    }
                    const closest = r.closest('label');
                    if (closest) {
                        const txt = closest.textContent.replace(r.value || '', '').trim().toLowerCase();
                        if (txt) return txt;
                    }
                    const radioRole = r.closest('[role="radio"]');
                    if (radioRole) {
                        const pEls = radioRole.querySelectorAll('p');
                        for (const p of pEls) {
                            const txt = p.textContent.trim().toLowerCase();
                            if (txt && txt !== 'on' && txt.length < 30) return txt;
                        }
                        const al = radioRole.getAttribute('aria-label');
                        if (al && al.length < 30 && !/\\.pdf$|\\.doc/i.test(al)) return al.toLowerCase();
                    }
                    let el = r;
                    for (let i = 0; i < 4; i++) {
                        el = el.parentElement;
                        if (!el) break;
                        const lbl = el.querySelector('label');
                        if (lbl) {
                            const txt = lbl.textContent.trim().toLowerCase();
                            if (txt && txt !== 'on') return txt;
                        }
                    }
                    let sib = r.nextElementSibling;
                    while (sib) {
                        const txt = sib.textContent.trim().toLowerCase();
                        if (txt && txt !== 'on') return txt;
                        sib = sib.nextElementSibling;
                    }
                    return (r.value || '').toLowerCase();
                }

                // Try 1: match by value (standard HTML)
                for (const r of radios) {
                    if (r.value && r.value.toLowerCase() === ans) return { id: r.id, match: 'value' };
                }
                // Try 1b: Yes/No prefix match
                if (ynPref) {
                    const matches = [];
                    for (const r of radios) {
                        const lbl = radioLabel(r);
                        if (lbl === ynPref || lbl.startsWith(ynPref + ' ') || lbl.startsWith(ynPref + ',') || lbl.startsWith(ynPref + '-')) {
                            matches.push({radio: r, label: lbl});
                        }
                    }
                    if (ans.length > ynPref.length + 1) {
                        for (const m of matches) {
                            if (m.label === ans || m.label.startsWith(ans) || ans.startsWith(m.label)) {
                                return { id: m.radio.id, match: 'exact:' + m.label.slice(0, 40) };
                            }
                        }
                    }
                    if (matches.length === 1) {
                        return { id: matches[0].radio.id, match: 'single:' + matches[0].label.slice(0, 40) };
                    }
                    if (matches.length > 1 && country) {
                        const locWords = country.split(',').map(w => w.trim()).filter(Boolean);
                        let bestMatch = null;
                        let bestScore = -1;
                        for (const m of matches) {
                            const lblLower = m.label.toLowerCase();
                            let score = 0;
                            for (const w of locWords) {
                                if (lblLower.includes(w)) score++;
                            }
                            if (score > bestScore) {
                                bestScore = score;
                                bestMatch = m;
                            }
                        }
                        if (bestMatch && bestScore > 0) {
                            return { id: bestMatch.radio.id, match: 'loc:' + bestMatch.label.slice(0, 40) + ' score=' + bestScore };
                        }
                    }
                    if (matches.length > 0) {
                        return { id: matches[0].radio.id, match: 'first_match:' + matches[0].label.slice(0, 40) };
                    }
                }
                // Try 2: match by associated <label> text
                for (const r of radios) {
                    const lbl = r.closest('label');
                    if (lbl) {
                        const txt = lbl.textContent.replace(r.value || '', '').trim().toLowerCase();
                        if (txt === ans || txt.startsWith(ans) || ans.startsWith(txt)) return { id: r.id, match: 'try2:' + txt.slice(0, 40) };
                    }
                }
                // Try 3: walk up 4 levels and match text (LinkedIn pattern)
                for (const r of radios) {
                    let el = r;
                    for (let i = 0; i < 4; i++) {
                        el = el.parentElement;
                        if (!el) break;
                        const txt = el.textContent.trim().toLowerCase();
                        if (txt && txt.length < 100 && (txt === ans || txt.startsWith(ans + ' ') || txt.startsWith(ans + ','))) {
                            return { id: r.id, match: 'try3:' + txt.slice(0, 40) };
                        }
                    }
                }
                // Try 4: check next sibling text
                for (const r of radios) {
                    let sib = r.nextElementSibling;
                    while (sib) {
                        const txt = sib.textContent.trim().toLowerCase();
                        if (txt && (txt === ans || txt.startsWith(ans) || ans.startsWith(txt))) {
                            return { id: r.id, match: 'try4:' + txt.slice(0, 40) };
                        }
                        sib = sib.nextElementSibling;
                    }
                }
                // Try 5: normalized EEOC match
                function normEEOC(s) {
                    return s.replace(/^(yes|no)[,\\s]+/i, '')
                            .replace(/['\\u2019]/g, '')
                            .replace(/\\bdo not\\b/g, 'dont')
                            .replace(/prefer not to (answer|say|state|disclose|specify)/, 'prefernottosay')
                            .replace(/[^a-z\\s]/g, '')
                            .trim();
                }
                const ansNorm = normEEOC(ans);
                if (ansNorm && ansNorm.length > 5) {
                    for (const r of radios) {
                        const lbl = radioLabel(r);
                        if (!lbl) continue;
                        const lblNorm = normEEOC(lbl);
                        if (lblNorm && lblNorm.length > 5) {
                            if (ansNorm === lblNorm || ansNorm.includes(lblNorm) || lblNorm.includes(ansNorm)) {
                                return { id: r.id, match: 'try5:norm:' + lbl.slice(0, 40) };
                            }
                        }
                    }
                }
                // Try 6: Yes/No negation detection for options that don't
                // start with yes/no (e.g. "I am based in Ottawa" vs
                // "I am not based in Ottawa, but open to relocation").
                // For "Yes": pick the option WITHOUT negation words.
                // For "No": pick the option WITH negation words.
                if (ynPref) {
                    const negWords = /\\b(not|dont|don't|never|unable|cannot|can't)\\b/i;
                    const candidates = [];
                    for (const r of radios) {
                        const lbl = radioLabel(r);
                        if (!lbl) continue;
                        const hasNeg = negWords.test(lbl);
                        if (ynPref === 'yes' && !hasNeg) candidates.push({radio: r, label: lbl});
                        if (ynPref === 'no' && hasNeg) candidates.push({radio: r, label: lbl});
                    }
                    if (candidates.length === 1) {
                        return { id: candidates[0].radio.id, match: 'try6:neg:' + candidates[0].label.slice(0, 40) };
                    }
                    if (candidates.length > 1 && country) {
                        const locWords = country.split(',').map(w => w.trim()).filter(Boolean);
                        let bestMatch = null;
                        let bestScore = -1;
                        for (const m of candidates) {
                            const lblLower = m.label.toLowerCase();
                            let score = 0;
                            for (const w of locWords) {
                                if (lblLower.includes(w)) score++;
                            }
                            if (score > bestScore) {
                                bestScore = score;
                                bestMatch = m;
                            }
                        }
                        if (bestMatch && bestScore > 0) {
                            return { id: bestMatch.radio.id, match: 'try6:neg+loc:' + bestMatch.label.slice(0, 40) };
                        }
                    }
                    if (candidates.length > 0) {
                        return { id: candidates[0].radio.id, match: 'try6:neg:first:' + candidates[0].label.slice(0, 40) };
                    }
                }
                return null;
            }""", [sel, ans_lower, yn_prefix, country])

            if not result or not result.get("id"):
                return False

            # Step 2: use Playwright's native .click() on the label, which
            # goes through the full browser event pipeline (focus, mousedown,
            # mouseup, click) and properly triggers React's synthetic events.
            radio_id = result["id"]
            _esc_id = radio_id.replace('\\', '\\\\').replace('"', '\\"')
            label_sel = f'label[for="{_esc_id}"]'
            try:
                label = page.locator(label_sel)
                if label.count() > 0:
                    label.click(timeout=3000)
                    return True
            except Exception:
                pass
            # Fallback: click the radio input directly with force
            try:
                radio_sel = f'input[id="{_esc_id}"]'
                page.locator(radio_sel).click(timeout=3000, force=True)
                return True
            except Exception:
                pass
            return False
        except Exception:
            return False


class SelectFiller(FieldFiller):
    name = "select"

    def can_handle(self, f):
        return f["tag"] == "SELECT"

    def fill(self, page, f, ans):
        sel = f.get("_sel", "")
        el = page.query_selector(sel) if sel else None
        if not el:
            return False
        for method in getattr(select, "METHOD_CHAIN", ["select_option"]):
            if select.try_select_tag(el, f, ans, method=method):
                return True
        return False


class ComboboxFiller(FieldFiller):
    name = "combobox"

    def can_handle(self, f):
        return _is_combobox(f)

    def fill(self, page, f, ans):
        return bool(combobox.fill(page, f, ans))


class DatepickerFiller(FieldFiller):
    name = "datepicker"

    def can_handle(self, f):
        return f.get("datepicker") == "flatpickr"

    def fill(self, page, f, ans):
        sel = f.get("_sel", "")
        try:
            page.evaluate(f"() => {{ const el = document.querySelector({json.dumps(sel)}); if (el && el._flatpickr) el._flatpickr.setDate('{ans}') }}")
            return True
        except Exception:
            return False


class ContentEditableFiller(FieldFiller):
    name = "contenteditable"

    def can_handle(self, f):
        return f["tag"] == "DIV" or f.get("contenteditable")

    def fill(self, page, f, ans):
        sel = f.get("_sel", "")
        return bool(ce.fill(page, sel, ans))


class FileFiller(FieldFiller):
    name = "file"

    def can_handle(self, f):
        return (f.get("type") or "").lower() == "file" or f.get("tag") == "INPUT" and (f.get("type") or "").lower() == "file"

    def fill(self, page, f, ans):
        sel = f.get("_sel", "")
        if not sel:
            return False
        # ans should be a file path; if it's not a valid path, skip
        path = str(ans).strip()
        if not os.path.isfile(path):
            return False
        try:
            # Try direct set_input_files
            page.set_input_files(sel, path)
            return True
        except Exception:
            pass
        # Try in frames
        for fr in page.frames:
            if fr == page.main_frame:
                continue
            try:
                fr.set_input_files(sel, path)
                return True
            except Exception:
                continue
        # Try JS: find hidden file input and set via DataTransfer
        try:
            el = page.query_selector(sel)
            if el:
                el.set_input_files(path)
                return True
        except Exception:
            pass
        # Fallback: intercept file chooser (SPA upload button pattern)
        from apply.act.helpers import _try_filechooser_upload
        return _try_filechooser_upload(page, f.get("label", ""), path, sel=sel)


class AutocompleteFiller(FieldFiller):
    """Handles text inputs with autocomplete dropdowns (Ashby Location, Greenhouse typeahead).
    Detected by generic placeholders like 'Start typing...', 'Type to search...'.
    Uses keyboard typing (not el.value) so React state updates, then selects first suggestion."""
    name = "autocomplete"

    _AUTOCOMPLETE_PLACEHOLDERS = {"start typing...", "type to search...", "type here and select...",
                                  "search and select...", "start typing and select..."}

    def can_handle(self, f):
        if _is_combobox(f):
            return False
        if f.get("tag") not in ("INPUT", "TEXTAREA"):
            return False
        if (f.get("type") or "").lower() in ("checkbox", "radio", "file", "hidden", "email", "tel", "number"):
            return False
        placeholder = (f.get("placeholder") or "").lower().strip()
        label = (f.get("label") or "").lower().strip()
        if placeholder in self._AUTOCOMPLETE_PLACEHOLDERS:
            return True
        if placeholder == "start typing..." and "location" in label:
            return True
        # LinkedIn Easy Apply location typeahead
        if "city or location" in label or "enter city" in label:
            return True
        # Generic typeahead detection via aria-autocomplete
        if f.get("aria_autocomplete") in ("list", "both") and f.get("tag") == "INPUT":
            return True
        return False

    @staticmethod
    def _selection_verdict(page, sel, ans_lower):
        """Read the combobox's SELECTED value via ARIA/React readers.

        Returns:
          True  — a selection readable AND it matches the answer
          False — a selection readable but it does NOT match (wrong option)
          None  — no reader can determine the selection (can't verify)
        """
        try:
            from apply.common.value_reader import AriaComboboxReader, ReactSelectReader
            for reader in (AriaComboboxReader(), ReactSelectReader()):
                v = reader.read(page, sel, ans=ans_lower)
                if v:
                    return (ans_lower in v.lower() or v.lower() in ans_lower)
        except Exception:
            pass
        return None

    def fill(self, page, f, ans):
        sel = f.get("_sel", "")
        if not sel:
            return False
        try:
            el = page.locator(sel).first
            if el.count() == 0:
                return False
            el.click(timeout=3000)
            time.sleep(0.3)
            el.fill("")
            time.sleep(0.2)
            # Type character by character so React/autcomplete picks up events
            el.type(str(ans), delay=80)
            time.sleep(1.5)
            # Click the first suggestion (legacy behavior), then VERIFY the
            # selection actually matches. Only when a reader proves the
            # option was wrong do we try the next suggestion; when nothing
            # is readable we keep the first click's outcome.
            suggestion_clicked = page.evaluate("""(idx) => {
                const sels = '[role="option"], [class*="suggestion"], [class*="dropdown-item"], [class*="list-item"], [class*="menu-item"], li[class*="option"]';
                const all = [...document.querySelectorAll(sels)].filter(el => el.offsetParent !== null);
                const target = (idx === null) ? all[0] : all[idx];
                if (!target) return false;
                target.click();
                return true;
            }""", None)
            if suggestion_clicked:
                time.sleep(0.5)
                ans_lower = str(ans).lower().strip()
                verdict = self._selection_verdict(page, sel, ans_lower)
                if verdict is True:
                    return True
                if verdict is False:
                    # First suggestion was provably wrong — try the next one.
                    time.sleep(0.5)
                    next_clicked = page.evaluate("""(idx) => {
                        const sels = '[role="option"], [class*="suggestion"], [class*="dropdown-item"], [class*="list-item"], [class*="menu-item"], li[class*="option"]';
                        const all = [...document.querySelectorAll(sels)].filter(el => el.offsetParent !== null);
                        const target = all[idx];
                        if (!target) return false;
                        target.click();
                        return true;
                    }""", 1)
                    if next_clicked:
                        time.sleep(0.5)
                        if self._selection_verdict(page, sel, ans_lower) is True:
                            return True
                    # Wrong option selected and no better one available —
                    # surface as failed so the orchestrator can supply a
                    # clean answer instead of submitting a wrong choice.
                    return False
                # None: unverifiable — accept the first click (legacy).
                return True
            # No dropdown appeared — press Tab to keep typed value without submitting
            el.press("Tab")
            time.sleep(0.5)
            return True
        except Exception:
            return False


class TextFiller(FieldFiller):
    name = "text"

    def can_handle(self, f):
        if _is_combobox(f):
            return False
        if (f.get("type") or "").lower() in ("checkbox", "radio", "file"):
            return False
        return f["tag"] in ("INPUT", "TEXTAREA")

    def fill(self, page, f, ans):
        sel = f.get("_sel", "")
        el = page.query_selector(sel) if sel else None
        if not el:
            return False
        for method in getattr(text, "METHOD_CHAIN", ["fill"]):
            if text.fill_text_field(page, f, ans, sel, el, method=method):
                return True
        return False


class AshbyYesNoFiller(FieldFiller):
    """Handles Ashby's custom Yes/No button widget: two <button> elements
    inside a _yesno_ container with a hidden checkbox."""
    name = "ashby_yesno"

    def can_handle(self, f):
        tag = (f.get("tag") or "").upper()
        ftype = (f.get("type") or "").lower()
        if tag != "INPUT" or ftype != "checkbox":
            return False
        # Only handle non-consent checkboxes (CheckboxFiller handles consent)
        lbl = (f.get("label") or "").lower()
        consent_kw = ["agree", "consent", "accept", "terms", "confirm",
                      "understand", "authorize", "certify", "marketing",
                      "privacy", "notice", "future job", "updates"]
        if any(kw in lbl for kw in consent_kw):
            return False
        return True

    def fill(self, page, f, ans):
        sel = f.get("_sel", f.get("selector", ""))
        if not sel:
            return False
        ans_lower = str(ans).strip().lower()
        target = "yes" if ans_lower.startswith("yes") else "no"
        try:
            clicked = page.evaluate("""(args) => {
                const [sel, target] = args;
                const cb = document.querySelector(sel);
                if (!cb) return false;
                const container = cb.closest('[class*="yesno"]');
                if (!container) return false;
                const buttons = container.querySelectorAll('button');
                for (const btn of buttons) {
                    if (btn.textContent.trim().toLowerCase() === target) {
                        btn.click();
                        return true;
                    }
                }
                return false;
            }""", [sel, target])
            return bool(clicked)
        except Exception:
            return False


class NativeSetterFallback(FieldFiller):
    name = "native_setter"

    def can_handle(self, f):
        return True

    def fill(self, page, f, ans):
        sel = f.get("_sel", "")
        el = page.query_selector(sel) if sel else None
        if not el:
            return False
        if _is_combobox(f):
            return False
        if f["tag"] in ("INPUT", "TEXTAREA"):
            return bool(text.native_setter(page, sel, ans))
        return False


# ─── Filler registry (order matters) ──────────────────────────────────

_FILLERS = [
    RadioFiller(),
    AshbyYesNoFiller(),
    CheckboxFiller(),
    FileFiller(),
    SelectFiller(),
    ComboboxFiller(),
    DatepickerFiller(),
    ContentEditableFiller(),
    AutocompleteFiller(),
    TextFiller(),
    NativeSetterFallback(),
]


def _try_text_fallback(page, f, ans, sel):
    """Last-resort cross-type fallback after primary filler failed to stick."""
    if _is_combobox(f):
        return False
    if f.get("contenteditable") or f["tag"] == "DIV":
        return bool(ce.fill(page, sel, ans))
    el = page.query_selector(sel)
    if el and f["tag"] in ("INPUT", "TEXTAREA"):
        for method in getattr(text, "METHOD_CHAIN", ["fill"]):
            if text.fill_text_field(page, f, ans, sel, el, method=method):
                return True
    return False


def fill_field(page, field: dict, ans: str) -> tuple[bool, str]:
    """Try all fillers in order. Returns (success, filler_name).
    Includes pre/post-fill verification — the value must actually change.
    List answers (multi-select): every value must stick — all-or-nothing."""
    if isinstance(ans, list):
        if not ans:
            return True, "multi"
        last_name = "multi"
        for v in ans:
            ok, name = fill_field(page, dict(field), v)
            last_name = name
            if not ok:
                return False, last_name
        return True, last_name
    return _fill_one(page, field, ans)


def _fill_one(page, field: dict, ans: str) -> tuple[bool, str]:
    sel = field.get("_sel", "")
    if not sel:
        sel = field.get("selector", "")
    if not sel:
        sel = resolve_selector(page, field)
        if not sel:
            return False, "no_selector"
        field["_sel"] = sel

    fr = _frame_for_sel(page, sel) or page
    label = field.get("label", "")

    # Comboboxes and Ashby Yes/No: trust the filler's own verification.
    # Read-back via value_reader races the DOM and can wipe real selections.
    # Ashby yesno: checkbox .checked may not update (widget uses button active class).
    _is_ashby_yesno = False
    if field.get("type") == "checkbox":
        try:
            _is_ashby_yesno = page.evaluate("""(sel) => {
                const el = document.querySelector(sel);
                return !!(el && el.closest('[class*="yesno"]'));
            }""", sel)
        except Exception:
            pass
    if _is_combobox(field) or _is_ashby_yesno:
        for filler in _FILLERS:
            if filler.can_handle(field):
                if filler.fill(fr, field, ans):
                    return True, filler.name
                # Radio/yesno rows return bare False with no evidence —
                # record a diagnostic so the audit log explains the gap
                # instead of showing an empty reason.
                field.setdefault("_diag", {})["reason"] = "no_option_match"
        return False, "none"

    before = _read_element_value(page, sel, field=field)

    for filler in _FILLERS:
        if filler.can_handle(field):
            if filler.fill(fr, field, ans):
                aft = _read_element_value(page, sel, ans=ans, field=field)
                ok, delta_reason = _check_delta(before, aft, ans, label, field)
                if ok:
                    return True, filler.name
                field["_diag"] = {"method": filler.name, "reason": delta_reason,
                                  "before": before, "after": aft}

    # Primary chain failed — try text fallback
    if _try_text_fallback(fr, field, ans, sel):
        aft2 = _read_element_value(page, sel, ans=ans, field=field)
        if isinstance(ans, list):
            ans_s = ", ".join(str(v) for v in ans)
        else:
            ans_s = str(ans) if ans is not None else ""
        if aft2 and (aft2 != before) and (not ans_s or aft2 == ans_s or ans_s in aft2 or aft2 in ans_s):
            return True, "text_fallback"
        if ans_s and aft2 and (aft2 == ans_s or ans_s in aft2 or aft2 in ans_s):
            return True, "text_fallback"
        if aft2:
            field.setdefault("_diag", {})["after"] = aft2

    diag = field.setdefault("_diag", {})
    diag["reason"] = diag.get("reason") or "no_filler"
    return False, "none"
