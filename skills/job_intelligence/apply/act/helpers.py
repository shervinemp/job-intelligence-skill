"""act/helpers.py — Shared helpers for all act commands.

Chrome lifecycle, DOM interaction (JS evaluation), field probing,
fill dispatch, file upload, validation scanning, submit detection,
profile loading, answer dict construction, vision verification.
"""
import json, os, re, sys, time
from contextlib import contextmanager

from lib.config import PROFILE_PATH, JI_HOME
from apply.common.output import emit_error
from apply.common.page_helpers import (
    load_state, page_text, find_page,
    check_applied_signal, scan_actions,
)
from apply.common.resolve import resolve, learn_mapping, _build_ephemeral
from apply.common.signals import has_success_text

RESULTS_DIR = os.path.join(JI_HOME, "results")


def _load_profile():
    try:
        with open(PROFILE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _chrome():
    from lib.chrome_manager import connect, start
    try:
        if not start():
            emit_error("could not start Chrome")
            sys.exit(1)
        b, ctx = connect()
    except RuntimeError as e:
        emit_error(str(e))
        sys.exit(1)
    if not ctx:
        emit_error("could not connect to Chrome")
        sys.exit(1)
    try:
        ctx.on("page", lambda pg: _wire_dialogs(pg))
    except Exception:
        pass
    for pg in ctx.pages:
        _wire_dialogs(pg)
    return b, ctx


@contextmanager
def chrome_session(state=None):
    """Context manager for Chrome lifecycle: starts Chrome, yields (page, ctx),
    closes browser on exit. Replaces 5 duplicated b,ctx=_chrome(); try;finally;close() patterns."""
    b, ctx = _chrome()
    try:
        page = _page_for(ctx, state)
        yield page, ctx
    finally:
        try:
            b.close()
        except Exception:
            pass


def _wire_dialogs(page):
    try:
        page.on("dialog", lambda d: d.accept() if not d.type else
                d.accept() if d.type == "confirm" else d.dismiss())
    except Exception:
        pass


def _page_for(ctx, state=None):
    if state:
        try:
            p = find_page(ctx, state)
            if p is not None:
                return p
        except Exception:
            pass
    pages = [p for p in ctx.pages if "about:blank" not in p.url and "chrome-error" not in p.url]
    if pages:
        return pages[-1]
    return ctx.new_page()


def _host(u):
    from urllib.parse import urlparse
    try:
        return urlparse(u or "").netloc.lower().split(":")[0]
    except Exception:
        return ""


_NEXT_KEYWORDS_JS = ["next", "continue", "continue to review", "review",
                     "review application", "next step", "save and continue"]


def _find_next_button(page):
    """Find the best 'Next'/'Continue' button. Delegates to page_state.find_buttons
    with the next-keyword list. Returns {text, score} dict or None."""
    from apply.common.page_state import find_buttons
    cands = find_buttons(page, _NEXT_KEYWORDS_JS, scope="any")
    return cands[0] if cands else None


def _click_action(page, text):
    """Click a button by text. Delegates to page_state.click_button_by_text
    which handles dialog offsetParent and modal preference."""
    from apply.common.page_state import click_button_by_text
    return click_button_by_text(page, text, prefer_dialog=True)

_ERROR_MARKERS = (
    "upstream connect error", "bad gateway", "service unavailable",
    "404 not found", "page not found", "access denied", "403 forbidden",
    "this site can't", "err_connection", "err_ssl", "application error",
)


def _is_error_page(page):
    try:
        text = (page_text(page) or "").strip().lower()
    except Exception:
        return False
    if len(text) > 400:
        return False
    return any(m in text for m in _ERROR_MARKERS)


def _url_fallbacks(url, state_url=""):
    from apply.common.registry import resolve as resolve_registry
    out = []
    for u in (url, state_url):
        if not u:
            continue
        reg = resolve_registry(u)
        if reg:
            for alt in reg.rewrite_urls(u):
                if alt not in out:
                    out.append(alt)
    return out


def _wait_for_fields(page, timeout=8):
    """Delegate to page_state.wait_for_form."""
    from apply.common.page_state import wait_for_form
    return wait_for_form(page, timeout)


_JUNK_TYPES = {"range", "search", "hidden", "submit", "button", "reset"}
_JUNK_LABEL_KW = ("progress", "scrubber", "search", "subscribe", "newsletter",
                  "volume", "playback", "password", "captcha",
                  "robot", "honeypot", "leave this blank", "leave empty",
                  "for bots", "spam trap", "do not fill", "do not enter")


def _is_junk_field(f):
    if f.get("is_honeypot"):
        return True
    t = (f.get("type") or "").lower()
    if t in _JUNK_TYPES:
        return True
    lbl = (f.get("label") or "").lower()
    return any(k in lbl for k in _JUNK_LABEL_KW)


def _click_apply_button(page):
    try:
        return bool(page.evaluate("""() => {
            const kws = ['apply for this job', 'apply now', 'start application',
                         'apply to this', 'apply online', 'apply'];
            const all = Array.from(document.querySelectorAll('button, a, [role="button"]'))
                .filter(el => el.offsetParent !== null);
            for (const kw of kws) {
                for (const el of all) {
                    const t = (el.textContent || '').trim().toLowerCase().replace(/\\s+/g, ' ');
                    if (t === kw || t.startsWith(kw)) { el.click(); return true; }
                }
            }
            return false;
        }"""))
    except Exception:
        return False


def _probe_form(page, reg, jid, allow_vision=True):
    from apply.common import inspector as _insp
    orig = _insp._PROBE_STRATEGIES
    if not allow_vision:
        _insp._PROBE_STRATEGIES = [s for s in orig if s[0] != "vision"]
    try:
        orig_url = page.url
        pr = _insp.probe(page, registry_config=reg, jid=jid)
        fields = [f for f in (pr.fields or []) if not _is_junk_field(f)]
        if fields:
            pr.fields = fields
            if pr.strategy == "iframe":
                print(f"  Form in iframe ({len(fields)} fields) — using frame API", file=sys.stderr)
            return pr
        if pr.strategy != "iframe":
            iframe_pr = _insp._probe_iframes(page)
            iframe_fields = [f for f in (iframe_pr.fields or []) if not _is_junk_field(f)]
            if iframe_fields:
                iframe_pr.fields = iframe_fields
                print(f"  Form in iframe ({len(iframe_fields)} fields) — using frame API", file=sys.stderr)
                return iframe_pr
            srcs = getattr(iframe_pr, "iframe_srcs", []) or []
            src = next((s for s in srcs if s and "http" in s), "")
            if src:
                print(f"  Form lives in cross-origin iframe — navigating directly: {src[:90]}", file=sys.stderr)
                try:
                    page.goto(src, wait_until="domcontentloaded", timeout=30000)
                    time.sleep(3)
                    pr2 = _insp.probe(page, registry_config=reg, jid=jid)
                    pr2.fields = [f for f in (pr2.fields or []) if not _is_junk_field(f)]
                    if pr2.fields:
                        return pr2
                    page.goto(orig_url, wait_until="domcontentloaded", timeout=15000)
                except Exception:
                    pass
        # Closed-shadow-DOM escape hatch: DOM queries cannot pierce closed
        # roots, so a form inside them looks empty — which would be
        # misclassified (login wall / expired). The browser's OWN
        # accessibility tree exposes them; use it before giving up.
        if not fields:
            try:
                from apply.common.a11y_reader import read_fields_from_a11y
                a11y_fields = read_fields_from_a11y(page)
                a11y_fields = [f for f in a11y_fields if not _is_junk_field(f)]
                if a11y_fields:
                    print(f"  {len(a11y_fields)} field(s) via accessibility tree "
                          f"(closed shadow DOM)", file=sys.stderr)
                    pr.fields = a11y_fields
                    return pr
            except Exception:
                pass
        if _click_apply_button(page):
            # The click may trigger a navigation (SSO redirect, lazy
            # apply link) — settle before probing so the JS context
            # isn't destroyed mid-evaluate.
            try:
                page.wait_for_load_state("domcontentloaded", timeout=10000)
            except Exception:
                pass
            time.sleep(2)
            pr2 = _insp.probe(page, registry_config=reg, jid=jid)
            pr2.fields = [f for f in (pr2.fields or []) if not _is_junk_field(f)]
            return pr2
        pr.fields = []
        return pr
    finally:
        _insp._PROBE_STRATEGIES = orig


def _set_files_any_frame(page, sel, path):
    try:
        page.set_input_files(sel, path)
        return True
    except Exception:
        pass
    for fr in page.frames:
        if fr == page.main_frame:
            continue
        try:
            fr.set_input_files(sel, path)
            return True
        except Exception:
            continue
    return False


def _try_filechooser_upload(page, label, path, sel=""):
    """Fallback: intercept file chooser dialog when SPA apps (Ashby,
    Greenhouse) use an upload button instead of a visible <input type=file>.

    Clicks the upload button, intercepts the native file chooser, and
    sets the file programmatically. Returns True on success.
    `sel` is the field's own selector (dropzone div/button) — clicked
    first when given, since dropzones open the chooser on click."""
    lc = (label or "").lower()
    upload_kws = []
    if "resume" in lc or re.search(r'\bcv\b', lc):
        upload_kws = ["resume", "cv", "upload resume", "attach resume", "attach cv"]
    elif "cover" in lc:
        upload_kws = ["cover", "cover letter", "attach cover", "upload cover"]
    else:
        upload_kws = ["upload", "attach", "browse"]
    targets = []
    if sel:
        targets.append(sel)
    for kw in upload_kws:
        for selector in [
            f'button:has-text("{kw}")',
            f'a:has-text("{kw}")',
            f'[role="button"]:has-text("{kw}")',
            f'label:has-text("{kw}")',
        ]:
            targets.append(selector)
    for target in targets:
        try:
            btn = page.locator(target).first
            if btn.count() == 0:
                continue
            with page.expect_file_chooser(timeout=5000) as fc_info:
                try:
                    btn.click(timeout=3000)
                except Exception:
                    btn.click(force=True, timeout=3000)
            fc = fc_info.value
            fc.set_files(path)
            return True
        except Exception:
            continue
    return False


def _dismiss_confirm_modal(page):
    try:
        page.evaluate("""() => {
            const kws = ['yes', 'confirm', 'submit', 'ok', 'sure', 'continue',
                         'accept', 'accept all', 'agree', 'allow', 'allow all',
                         'accept and continue'];
            const modals = document.querySelectorAll('[role="dialog"], .modal, [class*="confirm"], [class*="popup"], [class*="cookie"], [class*="consent"]');
            for (const m of modals) {
                if (m.offsetParent === null) continue;
                for (const btn of m.querySelectorAll('button, a, [role="button"]')) {
                    const t = (btn.textContent || '').trim().toLowerCase();
                    if (kws.includes(t) || kws.some(k => t.startsWith(k))) {
                        btn.click();
                        return true;
                    }
                }
            }
            return false;
        }""")
    except Exception:
        pass


def _get_validation_errors(page):
    _JS = """() => {
        const sels = '[role="alert"], .field-error, .error-message, [class*="error"]:not([class*="error-icon"]), .form-error, .invalid-feedback, [class*="correction"], [class*="missing"], [class*="validation"]';
        const seen = new Set();
        const out = [];
        for (const el of document.querySelectorAll(sels)) {
            if (el.offsetParent === null) continue;
            const t = (el.textContent || '').trim();
            if (!t || t.length > 200 || seen.has(t)) continue;
            seen.add(t);
            out.push(t);
        }
        const body = document.body.innerText || '';
        const ashbyMatch = body.match(/Missing entry for required field[:\\s]*([^\\n]+)/i);
        if (ashbyMatch && !seen.has(ashbyMatch[0])) out.push('Missing: ' + ashbyMatch[1].trim());
        const correctionsMatch = body.match(/form needs corrections/i);
        if (correctionsMatch && !out.length) out.push('Form has validation errors');
        const reqMatches = body.matchAll(/([A-Za-z][A-Za-z\\s]+?)\\s+is required\\.?/g);
        for (const m of reqMatches) {
            const t = m[0].trim();
            if (!seen.has(t)) { seen.add(t); out.push(t); }
        }
        return out.slice(0, 15);
    }"""
    errors = []
    try:
        errors = page.evaluate(_JS) or []
    except Exception:
        pass
    if not errors:
        for fr in page.frames:
            if fr == page.main_frame:
                continue
            try:
                errors = fr.evaluate(_JS) or []
                if errors:
                    break
            except Exception:
                continue
    return errors


def _empty_required(page):
    try:
        return int(page.evaluate("""() => {
            let n = 0;
            const seenRadios = new Set();
            for (const el of document.querySelectorAll('input:not([type=hidden]):not([type=submit]), select, textarea')) {
                if (el.offsetParent === null) continue;
                if (!(el.required || el.getAttribute('aria-required') === 'true')) continue;
                if (el.type === 'checkbox') { if (!el.checked) n++; continue; }
                if (el.type === 'radio') {
                    const name = el.name || el.id;
                    if (!name || seenRadios.has(name)) continue;
                    seenRadios.add(name);
                    const group = el.closest('form, dialog, [role=radiogroup], div') || document;
                    const _escN = name.replace(/\\\\/g, '\\\\\\\\').replace(/"/g, '\\\\"');
                    const checked = group.querySelector('input[type=radio][name="' + _escN + '"]:checked');
                    if (!checked) n++;
                    continue;
                }
                if (el.getAttribute('role') === 'combobox') {
                    const scope = el.closest('.select__control') || el.parentElement;
                    if (scope && scope.querySelector('.select__single-value')) continue;
                    const owns = el.getAttribute('aria-owns') || el.getAttribute('aria-controls');
                    if (owns) {
                        const lb = document.getElementById(owns);
                        if (lb && lb.querySelector('[aria-selected="true"]')) continue;
                    }
                }
                if (!el.value) n++;
            }
            return n;
        }""") or 0)
    except Exception:
        return 0


def _check_submit_success(ctx, page, pages_before_ids):
    if check_applied_signal(page) or has_success_text(page_text(page) or ""):
        return True, page
    for fr in page.frames:
        if fr == page.main_frame:
            continue
        try:
            body = fr.evaluate("() => document.body.innerText") or ""
            if has_success_text(body):
                return True, page
        except Exception:
            continue
    new_pages = [p for p in ctx.pages if id(p) not in pages_before_ids
                 and "about:blank" not in p.url]
    for p in new_pages:
        try:
            if check_applied_signal(p) or has_success_text(page_text(p) or ""):
                return True, p
        except Exception:
            continue
    return False, None


def _field_key(f):
    sel = f.get("_sel") or f.get("selector") or ""
    if sel:
        return sel
    return (f.get("label", ""), f.get("id", ""), f.get("name", ""))


def _resolve_linkedin_apply(page):
    """On a LinkedIn job page, find the 'Apply' link and extract the real ATS URL."""
    import urllib.parse as _up
    try:
        href = page.evaluate("""() => {
            const links = document.querySelectorAll('a');
            for (const a of links) {
                const text = (a.textContent || '').trim().toLowerCase();
                if (text === 'apply') return a.href || null;
            }
            return null;
        }""")
        if not href:
            return None
        if 'linkedin.com/safety/go' in href:
            qs = _up.parse_qs(_up.urlparse(href).query)
            real = qs.get('url', [None])[0]
            return real or href
        return href
    except Exception:
        return None


def _gap_fill_into_answers(fields, profile, answers_override, jid, ephemeral):
    """Mutate answers_override in-place with LLM key-mapping for no_match fields.
    Returns updated answers_override (new dict if None was given).
    Only fills gaps — never overrides existing entries.
    """
    try:
        gap_fields = []
        for f in fields:
            label = (f.get("label") or "").strip()
            if not label:
                continue
            if f.get("tag") == "input" and (f.get("accept") or f.get("type") == "file"):
                continue
            if any(kw in label.lower() for kw in ("resume", " cv ", "cover")):
                continue
            r = resolve(label, profile, answers_override,
                        autocomplete=f.get("autocomplete", ""),
                        field_name=f.get("name", ""),
                        field_id=f.get("id", ""),
                        field_tag=f.get("tag", ""),
                        field_type=f.get("type", ""),
                        field_role=f.get("role", ""),
                        ephemeral=ephemeral)
            if r.value is None:
                gap_fields.append(f)
        if not gap_fields:
            return answers_override
        # Escape hatch only: the deterministic resolver exhausted its
        # vocabulary (no_match) — LLM key-mapping may recover new
        # phrasings. Policy-gated (gap_fill); off → the fields surface
        # as needs_data for the orchestrator to answer from evidence.
        from apply.common.llm_policy import allow as _llm_allow
        if not _llm_allow("gap_fill"):
            return answers_override
        from apply.act.suggest import llm_field_key_mapping
        from lib.db import get_job
        _job = get_job(jid) or {}
        gap = llm_field_key_mapping(gap_fields, profile, _job, ephemeral=ephemeral)
        if not gap:
            return answers_override
        # Deterministic gate on the LLM's output: an LLM-mapped value is
        # only accepted when the same validator that guards deterministic
        # fills passes it (option membership, URL placement, format).
        # FAIL-CLOSED: a mapping whose label matches no known field is
        # DROPPED — never silently trusted. Labels are matched on both
        # the raw and the reader-truncated (60-char) forms, so truncation
        # can't turn a legitimate mapping into an unvalidated one.
        try:
            from apply.common.validate import validate_value as _vv
            _by_label = {}
            for _f in gap_fields:
                _lbl = (_f.get("label") or "").strip()
                _by_label.setdefault(_lbl, _f)
                _by_label.setdefault(_lbl[:60], _f)
            _kept, _dropped = {}, []
            for _k, _v in gap.items():
                _f = _by_label.get(_k.strip()) or _by_label.get(_k.strip()[:60])
                if _f is None:
                    _dropped.append(_k)
                    continue
                _ok, _reason = _vv(_f, _v)
                if _ok:
                    _kept[_k] = _v
                else:
                    _dropped.append(f"{_k} (validator: {_reason})")
            gap = _kept
            if _dropped:
                print(f"  GAP_FILL_GATE: dropped {len(_dropped)} LLM-mapped "
                      f"value(s) — {', '.join(str(x)[:60] for x in _dropped[:5])}",
                      file=sys.stderr)
        except Exception:
            pass
        if answers_override is None:
            answers_override = {}
        for k, v in gap.items():
            if k not in answers_override:
                answers_override[k] = v
    except Exception as e:
        print(f"  LLM_MAP_SKIP: {e}", file=sys.stderr)
    return answers_override


_CONSENT_KW = ("agree", "consent", "accept", "terms", "certify", "understand",
               "authorize", "privacy", "notice", "marketing", "updates",
               "confirm", "acknowledge")


def _is_consent_field(f):
    """True if a checkbox label reads like a consent/acknowledgement.
    JI_AUTO_CONSENT=1 must only auto-check these — a blunt check-all
    would silently answer work-history or sponsorship checkboxes wrong."""
    lbl = (f.get("label") or "").lower()
    return any(kw in lbl for kw in _CONSENT_KW)


def _is_upload_field(f):
    """True if a field takes the file-upload path: a real <input
    type=file> (or accept attr), a dropzone widget (div/button with a
    resume/cv/cover label), or a text input whose label says upload/
    attach/drop + resume/cv/cover (hybrid custom uploaders).

    Deliberately EXCLUDES textareas and plain text inputs that merely
    mention 'cover letter' — they must go through the normal resolve,
    otherwise the bare input[type=file] fallback can overwrite the
    resume with the cover letter."""
    tag = (f.get("tag") or "").lower()
    ftype = (f.get("type") or "").lower()
    lc = (f.get("label") or "").lower()
    if tag == "input" and (f.get("accept") or ftype == "file"):
        return True
    if tag == "textarea":
        return False
    _uploader_label = "resume" in lc or re.search(r"\bcv\b", lc) or "cover" in lc
    if tag in ("div", "button"):
        return _uploader_label
    if tag == "input" and ("upload" in lc or "attach" in lc or "drop" in lc):
        return _uploader_label
    return False


def _file_path_for(label, f, resume_path, cover_path):
    """Decide which file (resume vs cover) belongs to a file field.

    'cover' wins only when the label/id/name says cover AND NOT resume/cv;
    everything else (including combined labels) is the resume."""
    lc = (label or "").lower()
    ident = f"{lc} {(f.get('id') or '').lower()} {(f.get('name') or '').lower()}"
    is_cover = "cover" in ident and "resume" not in ident and not re.search(r"\bcv\b", ident)
    return cover_path if is_cover else resume_path


def _fill_with_playwright(page, fields, profile, answers_override,
                          filled_keys=None) -> tuple[list[dict], list[dict]]:
    from apply.strategies.dispatch import field_deterministic
    if filled_keys is None:
        filled_keys = set()

    filled = []
    failed = []

    state = load_state()
    jid = state.get("jid", "")

    resume_path = None
    cover_path = None
    if jid:
        rd = os.path.join(RESULTS_DIR, jid)
        import glob
        resumes = glob.glob(os.path.join(rd, "*Resume*.pdf"))
        covers = glob.glob(os.path.join(rd, "*Cover*.pdf"))
        if resumes:
            resume_path = resumes[0]
        if covers:
            cover_path = covers[0]

    # Derive user's location parts for radio disambiguation
    # (e.g. Ashby 4-option sponsorship: "Yes...United States" vs "Yes...Canada"
    #  and 3-option office: "Yes, in the San Francisco office" vs "Yes, in the Toronto office")
    loc = (profile.get("location") or "")
    user_country = ""
    user_city = ""
    user_region = ""
    if loc and "," in loc:
        parts = [p.strip() for p in loc.split(",")]
        if parts:
            user_city = parts[0]
        if len(parts) >= 2:
            user_region = parts[1]
        if len(parts) >= 3:
            user_country = parts[-1]
    if not user_country:
        user_country = profile.get("country", "")
    # Comma-separated location keywords for matching (e.g. "ottawa,ontario,canada,toronto")
    user_loc_words = ",".join(w.strip().lower() for w in [user_city, user_region, user_country] if w)

    for f in fields:
        f["_country"] = user_loc_words

    # ── Occurrence-aware field keys ──────────────────────────────────
    # Matrix/table questions emit N fields with the SAME label (and
    # often the same id/name pattern) on ONE page. Without a per-page
    # occurrence index the dedupe collapses rows N..1 into one.
    _base_keys = {}

    def _mk_key(f):
        k = _field_key(f)
        if k in _base_keys:
            _base_keys[k] += 1
            return f"{k}#{_base_keys[k]}"
        _base_keys[k] = 0
        return k

    # Build ephemeral once — shared across all resolve calls in this page
    ephemeral = _build_ephemeral(profile)
    # The per-job answers cache (state fill_answers) must feed the alias
    # and keyword rules too, not just exact-label override matching.
    # setdefault: profile values win over cached answers.
    for _ak, _av in (answers_override or {}).items():
        if _av:
            ephemeral.setdefault(
                _ak, (str(_av) if not isinstance(_av, list) else [str(x) for x in _av],
                      "state"))

    # Phase 1: heuristic resolve + LLM batch gap-fill for no_match fields.
    # Merges LLM key-mapping results into answers_override so Phase 2 picks them up.
    answers_override = _gap_fill_into_answers(fields, profile, answers_override, jid, ephemeral)

    for f in fields:
        label = f.get("label", "").strip()
        if not label:
            continue

        tag = (f.get("tag") or "").lower()
        ftype = (f.get("type") or "").lower()
        if _is_upload_field(f):
            path = _file_path_for(label, f, resume_path, cover_path)
            if path and os.path.exists(path):
                try:
                    sel = f.get("_sel") or f.get("selector") or ""
                    if not sel:
                        from apply.steps.probe import resolve_selector
                        sel = resolve_selector(page, f) or ""
                    if sel and _set_files_any_frame(page, sel, path):
                        filled.append({"label": label, "key": _field_key(f)})
                        continue
                    is_cover = bool(cover_path) and path == cover_path
                    kw = "cover" if is_cover else "resume"
                    fallbacks = [
                        f'input[type=file][id*="{kw}"]',
                        f'input[type=file][name*="{kw}"]',
                        'input[type=file][accept*="pdf"]',
                        'input[type=file][accept*="doc"]',
                    ]
                    # The bare input[type=file] fallback is dangerous — it
                    # matches ANY file input, so it can overwrite the
                    # resume with a cover. Only the PRIMARY (resume) field
                    # may use it.
                    if not is_cover:
                        fallbacks.append('input[type=file]')
                    for fb in fallbacks:
                        if _set_files_any_frame(page, fb, path):
                            filled.append({"label": label, "key": _field_key(f)})
                            break
                    else:
                        if _try_filechooser_upload(page, label, path, sel=sel):
                            filled.append({"label": label, "key": _field_key(f)})
                        else:
                            print(f"  UPLOAD_FAIL: {label}", file=sys.stderr)
                except Exception as ue:
                    print(f"  UPLOAD_FAIL: {label} — {str(ue)[:120]}", file=sys.stderr)

        res = resolve(label, profile, answers_override,
                      autocomplete=f.get("autocomplete", ""),
                      field_name=f.get("name", ""),
                      field_id=f.get("id", ""),
                      field_tag=f.get("tag", ""),
                      field_type=f.get("type", ""),
                      field_role=f.get("role", ""),
                      ephemeral=ephemeral)
        ans = res.value
        if ans is None:
            if (tag == "input" and ftype == "checkbox"
                    and os.environ.get("JI_AUTO_CONSENT") == "1"
                    and _is_consent_field(f)):
                ans = "true"
            else:
                failed.append({**f, "_why": "no_answer"})
                continue

        try:
            key = _mk_key(f)
            if key in filled_keys:
                continue
            # Skip if the field already has the correct value (prevents
            # double-typing on autocomplete/combobox fields during sweeps).
            sel = f.get("_sel") or f.get("selector") or ""
            if sel:
                import json as _json
                current = page.evaluate(f"() => document.querySelector({_json.dumps(sel)})?.value || ''")
                if current and (current == ans or current in ans or ans in current):
                    filled.append({"label": label, "key": key})
                    continue
            if field_deterministic(page, f, ans):
                _diag = f.get("_diag") or {}
                filled.append({"label": label, "key": key, "answer": str(ans),
                               "unverified": bool(_diag.get("unverified")),
                               "method": _diag.get("method", "deterministic")})
                if res.provenance == "answers_override":
                    try:
                        from urllib.parse import urlparse as _up
                        _dom = _up(page.url or "").netloc
                    except Exception:
                        _dom = ""
                    learn_mapping(label, ans, domain=_dom)
                if jid:
                    from apply.common.audit import log_field
                    log_field(jid, label, str(ans), res.provenance, filled=True,
                              selector=sel)
            else:
                failed.append({**f, "_why": "fill_failed", "key": key,
                               "attempted": str(ans)[:200]})
                if jid:
                    from apply.common.audit import log_field
                    _diag = f.get("_diag") or {}
                    log_field(jid, label, str(ans), res.provenance, filled=False,
                              reason=_diag.get("reason") or "fill_failed",
                              selector=sel,
                              method=_diag.get("method", ""),
                              before=_diag.get("before", ""),
                              after=_diag.get("after", ""))
        except Exception:
            failed.append({**f, "_why": "fill_failed", "attempted": str(ans)[:50]})
            if jid:
                try:
                    from apply.common.audit import log_field
                    _diag = f.get("_diag") or {}
                    log_field(jid, label, str(ans), res.provenance, filled=False,
                              reason=_diag.get("reason") or "exception",
                              selector=f.get("_sel") or f.get("selector") or "",
                              method=_diag.get("method", ""),
                              before=_diag.get("before", ""),
                              after=_diag.get("after", ""))
                except Exception:
                    pass

    return filled, failed


def _verify_with_ask_api(page, answers: dict) -> dict:
    try:
        from lib.ask_api import available, ask_bytes
        from apply.common.inspect_lib import form_jpeg
        if not available():
            return {"ok": False, "reason": "ask_api not available"}

        img_bytes = form_jpeg(page)
        prompt_lines = ["List every visible form field and its current value. Return as 'label: value' lines."]
        for k in answers:
            prompt_lines.append(f"  {k}: <expected: {answers[k]}>")
        prompt = "\n".join(prompt_lines)

        reply, err = ask_bytes(img_bytes, prompt)
        if err:
            return {"ok": False, "reason": str(err)}
        text = str(reply or "")
        mismatches = []
        for k, expected in answers.items():
            if k.lower() in text.lower():
                pass
            else:
                mismatches.append({"field": k, "expected": expected})
        return {
            "ok": len(mismatches) == 0,
            "mismatches": mismatches,
            "vision_text": text[:200],
        }
    except Exception as e:
        return {"ok": False, "reason": str(e)}


def _detect_submit_button(page) -> str | None:
    # Check iframes first (Greenhouse etc.)
    for fr in page.frames:
        if fr == page.main_frame:
            continue
        try:
            buttons = fr.evaluate("""() => {
                const all = document.querySelectorAll('button, input[type=submit]');
                return Array.from(all).filter(b => b.offsetParent !== null).map(b => ({
                    text: (b.textContent || b.value || '').trim().toLowerCase(),
                    type: b.type || '',
                }));
            }""")
            for b in buttons:
                bt = b.get("text", "")
                if bt in ("submit", "submit application", "send", "send application"):
                    return bt
        except Exception:
            continue
    candidates = scan_actions(page, ["submit", "submit application", "send application"])
    if candidates:
        # Prefer buttons inside dialog/modal (Easy Apply, etc.)
        dialog_btns = [c for c in candidates if c.get("_inDialog")]
        page_btns = [c for c in candidates if not c.get("_inDialog")]
        for pool in [dialog_btns, page_btns]:
            for c in pool:
                if not c.get("disabled") and c.get("tag") != "A":
                    return c.get("text", "")
    try:
        buttons = page.evaluate("""() => {
            // LinkedIn Easy Apply uses native <dialog open> where children
            // may have offsetParent === null. Just check all buttons inside
            // dialog regardless of offsetParent.
            const dlg = document.querySelector('dialog, [role="dialog"]');
            if (dlg) {
                const dlgBtns = dlg.querySelectorAll('button');
                for (const b of dlgBtns) {
                    const t = b.textContent.trim().toLowerCase();
                    if (t === "submit" || t === "submit application" || t === "send" || t === "send application") return t;
                }
            }
            // Fallback: check page-level buttons
            const all = document.querySelectorAll('button');
            for (const b of all) {
                if (b.offsetParent === null) continue;
                if (b.closest('dialog, [role="dialog"]')) continue;  // already checked
                const t = b.textContent.trim().toLowerCase();
                if (t === "submit" || t === "submit application" || t === "send" || t === "send application") return t;
            }
            return null;
        }""")
        if buttons:
            return buttons
    except Exception:
        pass
    return None


def _resolve_standalone_form_url(page) -> str | None:
    """If the page has a cross-origin iframe containing a job form
    (e.g. Ashby embed on customer domain), return the standalone
    form URL.  Strips embed parameters so Playwright can access the
    form directly instead of through a cross-origin iframe."""
    try:
        return page.evaluate("""() => {
            const iframes = document.querySelectorAll('iframe');
            for (const ifr of iframes) {
                const src = ifr.src || '';
                if (!src || src === 'about:blank') continue;
                let visible = false;
                try {
                    const rect = ifr.getBoundingClientRect();
                    visible = rect.width >= 100 && rect.height >= 100
                        && ifr.offsetParent !== null;
                } catch { continue; }
                if (!visible) continue;
                const title = (ifr.title || '').toLowerCase();
                const srcL = src.toLowerCase();
                const isFormIframe = (
                    /ashbyhq/.test(srcL)
                    || /greenhouse/.test(srcL)
                    || title.includes('job') || title.includes('apply')
                    || title.includes('application') || title.includes('career')
                    || title.includes('form')
                );
                if (!isFormIframe) continue;
                try {
                    const u = new URL(src);
                    u.searchParams.delete('embed');
                    u.searchParams.delete('embedded');
                    for (const k of [...u.searchParams.keys()]) {
                        if (k.startsWith('utm_')) u.searchParams.delete(k);
                    }
                    return u.href;
                } catch { return src; }
            }
            return null;
        }""")
    except Exception:
        return None


def _build_ans_dict(profile: dict, answers_override: dict = None) -> dict:
    result = {}
    if isinstance(profile, dict):
        result.update(profile.get("answers", {}))
    if answers_override:
        result.update(answers_override)
    for key in ("first_name", "last_name", "email", "phone", "full_name",
                "city", "state", "country", "linkedin_url", "github_url",
                "website", "headline"):
        if key not in result:
            val = profile.get(key) or profile.get(key.upper()) or profile.get(key.title())
            if val:
                result[key.replace("_", " ").title()] = val
    return result
