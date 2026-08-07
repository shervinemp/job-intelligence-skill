"""apply/common/fill_runner.py — the fill seam's single interface.

One module owns the per-page fill loop, so the deterministic core stays
deterministic and every LLM escape enters through the same
lib.automation.llm gate (routing hierarchy, ETHOS §1/§2):

    fill_page(page, fields, profile, answers_override, filled_keys)
        └─ gap_fill_into_answers   (policy-gated, fail-closed)
        └─ field_deterministic per field  (validate → fill → delta)

The strategy chain (apply/strategies/* + filler) sits behind
field_deterministic; callers never see it.
"""
import json
import re


def _host_of(page):
    """Netloc of the current page — used to domain-scope runtime rules (A4)."""
    try:
        from urllib.parse import urlparse as _up
        return (_up(page.url or "").netloc or "").lower().split(":")[0]
    except Exception:
        return ""
import os
import re
import sys
import time

from lib.config import JI_HOME
from apply.common.field_types import is_combobox as _is_combobox
from apply.common.output import emit_diag
from apply.common.page_helpers import load_state
from apply.common.resolve import resolve, learn_mapping, _build_ephemeral
from apply.common.validate import validate_value
from apply.steps.probe import resolve_selector

_SKILLS_LABEL_RE = re.compile(r"skill|competenc|school|education|degree|certif", re.I)

# Upper bound on fields processed in one page pass. The largest real
# application forms (Workday multi-section) sit well under 200; anything
# above this is a malformed or hostile DOM, not a form to fill.
MAX_FIELDS_PER_PAGE = int(os.environ.get("JI_MAX_FIELDS", "300"))

# Humanization (COMPARISON §S7): randomized per-field pacing so the fill
# cadence isn't a fixed, machine-regular beat. Range in seconds; parse from
# "min-max" or a single float. OFF when disabled via JI_FILL_DELAY=0 or when
# JI_TESTS is set (tests must stay fast and deterministic).
_DELAY_LO, _DELAY_HI = 0.0, 0.0


def _load_delay_bounds():
    global _DELAY_LO, _DELAY_HI
    try:
        if os.environ.get("JI_TESTS"):
            _DELAY_LO, _DELAY_HI = 0.0, 0.0
            return
        raw = os.environ.get("JI_FILL_DELAY", "0.15-0.35").strip()
        if not raw or raw == "0":
            _DELAY_LO, _DELAY_HI = 0.0, 0.0
            return
        if "-" in raw:
            lo, hi = (float(p) for p in raw.split("-", 1))
            _DELAY_LO, _DELAY_HI = min(lo, hi), max(lo, hi)
        else:
            _DELAY_LO, _DELAY_HI = 0.0, float(raw)
    except Exception:
        _DELAY_LO, _DELAY_HI = 0.0, 0.0


def inter_field_delay():
    """Randomized delay between field fills (humanization jitter)."""
    import random
    _load_delay_bounds()
    if _DELAY_HI <= 0:
        return
    time.sleep(random.uniform(_DELAY_LO, _DELAY_HI))


RESULTS_DIR = os.path.join(JI_HOME, "results")


# ─── File-upload + consent mechanics (owned by the fill loop) ─────────

_CONSENT_KW = ("agree", "consent", "accept", "terms", "certify", "understand",
               "authorize", "privacy", "notice", "marketing", "updates",
               "confirm", "acknowledge")


def _is_consent_field(f):
    """True if a checkbox label reads like a consent/acknowledgement.
    JI_AUTO_CONSENT=1 must only auto-check these — a blunt check-all
    would silently answer work-history or sponsorship checkboxes wrong."""
    lbl = (f.get("label") or "").lower()
    return any(kw in lbl for kw in _CONSENT_KW)


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


def _try_show_open_file_picker(page, label, path, sel=""):
    """Fallback: intercept the File System Access API.

    Modern ATS (2024+) call `showOpenFilePicker()` instead of opening an
    `<input type=file>` chooser — Playwright's expect_file_chooser can't
    see that, so the upload would silently no-op. We patch
    `window.showOpenFilePicker` in the MAIN world (our own original
    implementation, COMPARISON §S6) to resolve a synthetic FileSystemFileHandle
    backed by `path`, then click the upload control that triggers it.

    The patch is installed on the current frame tree only and restored
    after the click — it never survives to other pages. Returns True on
    success, False when the patched path didn't fire."""
    if not os.path.exists(path):
        return False
    import base64 as _b64
    try:
        with open(path, "rb") as _f:
            _payload = _b64.b64encode(_f.read()).decode("ascii")
    except Exception:
        return False
    name = os.path.basename(path)
    _mime = "application/pdf" if path.lower().endswith(".pdf") else \
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document" \
        if path.lower().endswith(".docx") else "application/msword"
    lc = (label or "").lower()
    upload_kws = ["resume", "cv", "cover", "upload", "attach", "browse"]
    targets = []
    if sel:
        targets.append(sel)
    for kw in upload_kws:
        for selector in [
            f'button:has-text("{kw}")',
            f'[role="button"]:has-text("{kw}")',
            f'a:has-text("{kw}")',
            f'label:has-text("{kw}")',
        ]:
            targets.append(selector)
    for target in targets:
        try:
            btn = page.locator(target).first
            if btn.count() == 0:
                continue
            ok = _patch_show_open_file_picker(page, _payload, name, _mime)
            if not ok:
                continue
            try:
                btn.click(timeout=3000)
            except Exception:
                btn.click(force=True, timeout=3000)
            time.sleep(2)
            fired = _fsap_was_called(page)
            _unpatch_show_open_file_picker(page)
            if fired:
                return True
            # The patched picker never fired — the click was not an FSAP
            # trigger. Keep trying other targets.
            continue
        except Exception:
            try:
                _unpatch_show_open_file_picker(page)
            except Exception:
                pass
            continue
    return False


def _patch_show_open_file_picker(page, b64_payload, name, mime):
    """Install a synthetic showOpenFilePicker on page + its frames.

    Sets window.__ji_fsap_called = 0 before, and increments it inside the
    patched handler, so _try_show_open_file_picker can verify the picker
    was actually invoked (a click on a non-upload button must not count as
    a successful upload)."""
    import json as _json
    _code = f"""() => {{
        const payload = {_json.dumps(b64_payload)};
        const fname = {_json.dumps(name)};
        const fmime = {_json.dumps(mime)};
        const bin = atob(payload);
        const bytes = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
        const file = new File([bytes], fname, {{ type: fmime }});
        const handle = {{
            name: fname,
            kind: 'file',
            getFile: async () => file,
            createWritable: async () => ({{
                write: async () => {{}},
                close: async () => {{}},
            }}),
        }};
        window.__ji_orig_showOpenFilePicker = window.showOpenFilePicker;
        window.__ji_fsap_called = 0;
        window.showOpenFilePicker = async () => {{
            window.__ji_fsap_called = (window.__ji_fsap_called || 0) + 1;
            return [handle];
        }};
        return true;
    }}"""
    try:
        page.evaluate(_code)
    except Exception:
        return False
    for fr in page.frames:
        if fr == page.main_frame:
            continue
        try:
            fr.evaluate(_code)
        except Exception:
            continue
    return True


def _fsap_was_called(page):
    """Did the patched showOpenFilePicker fire anywhere in the frame tree?"""
    try:
        if int(page.evaluate("() => window.__ji_fsap_called || 0") or 0) > 0:
            return True
    except Exception:
        pass
    for fr in page.frames:
        if fr == page.main_frame:
            continue
        try:
            if int(fr.evaluate("() => window.__ji_fsap_called || 0") or 0) > 0:
                return True
        except Exception:
            continue
    return False


def _unpatch_show_open_file_picker(page):
    """Restore the original showOpenFilePicker on the page AND every frame
    (the patch in _patch_show_open_file_picker is installed per-frame; a
    stale patch left in a sub-frame would feed the NEXT upload a frozen
    file handle)."""
    _code = """() => {
        if (window.__ji_orig_showOpenFilePicker) {
            window.showOpenFilePicker = window.__ji_orig_showOpenFilePicker;
            delete window.__ji_orig_showOpenFilePicker;
        }
    }"""
    try:
        page.evaluate(_code)
    except Exception:
        pass
    for fr in page.frames:
        if fr == page.main_frame:
            continue
        try:
            fr.evaluate(_code)
        except Exception:
            continue


def _field_key(f):
    sel = f.get("_sel") or f.get("selector") or ""
    if sel:
        return sel
    return (f.get("label", ""), f.get("id", ""), f.get("name", ""))


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


# ─── LinkedIn Easy Apply resume step ──────────────────────────────────
# The Easy Apply modal presents the resume as a <select> of previously
# uploaded resumes plus an "Upload resume" button — there is NO
# <input type=file> in the DOM. The generic upload path (_is_upload_field)
# never fires, so LinkedIn would submit with whatever resume is
# pre-selected from the profile (usually the wrong one). This handler
# detects the resume step, selects the tailored PDF if already uploaded,
# otherwise uploads it via the file chooser, then verifies by filename.
_DIALOG = '[role="dialog"], dialog'


def _in_linkedin_dialog(page):
    try:
        host = _host_of(page)
        return "linkedin.com" in (host or "")
    except Exception:
        return False


def _resume_step_visible(page):
    """True when the Easy Apply dialog is showing the resume step (a list of
    stored resumes with .pdf filenames + an upload control)."""
    try:
        return page.evaluate(f"""() => {{
            const d = document.querySelector({json.dumps(_DIALOG)});
            if (!d) return false;
            const spans = Array.from(d.querySelectorAll('span'));
            const hasPdf = spans.some(s => (s.textContent || '').includes('.pdf'));
            const hasUpload = Array.from(d.querySelectorAll('button')).some(
                b => /upload\\s*resume|add\\s*resume|change\\s*resume/i.test(
                    (b.textContent || '').trim()));
            return hasPdf || hasUpload;
        }}""")
    except Exception:
        return False


def _click_text_in_dialog(page, text):
    """Click the first clickable ancestor of an element containing `text`."""
    return page.evaluate(f"""() => {{
        const d = document.querySelector({json.dumps(_DIALOG)});
        if (!d) return false;
        for (const el of d.querySelectorAll('button, a, span, div, label, p')) {{
            if (el.offsetParent === null) continue;
            if (!(el.textContent || '').trim().includes({json.dumps(text)})) continue;
            let parent = el;
            for (let i = 0; i < 15 && parent; i++) {{
                const clickable = parent.tagName === 'A' || parent.tagName === 'BUTTON'
                    || parent.getAttribute('tabindex') === '0'
                    || parent.getAttribute('role') === 'button';
                if (clickable && parent.offsetParent !== null) {{
                    parent.click();
                    return true;
                }}
                parent = parent.parentElement;
            }}
        }}
        return false;
    }}""")


def _select_resume_by_name(page, target_name):
    """Click the stored-resume entry whose filename matches target_name."""
    safe = json.dumps(target_name)
    return page.evaluate(f"""() => {{
        const d = document.querySelector({json.dumps(_DIALOG)});
        if (!d) return false;
        for (const s of d.querySelectorAll('span')) {{
            const txt = (s.textContent || '').trim();
            if (!txt.includes('.pdf') || !txt.includes({safe})) continue;
            let el = s;
            for (let i = 0; i < 15 && el; i++) {{
                const a = el.closest('a');
                if (a && a.offsetParent !== null) {{ a.click(); return true; }}
                el = el.parentElement;
            }}
        }}
        return false;
    }}""")


def _expand_resume_list(page):
    try:
        page.evaluate(f"""() => {{
            const d = document.querySelector({json.dumps(_DIALOG)});
            if (!d) return;
            for (const b of d.querySelectorAll('button')) {{
                if (b.offsetParent && !b.disabled &&
                        /show\\s*3\\s*more\\s*resumes/i.test(b.textContent || '')) {{
                    b.click(); return;
                }}
            }}
        }}""")
        time.sleep(1)
    except Exception:
        pass


def _upload_file_by_text(page, text, file_path):
    if not os.path.exists(file_path):
        return False
    try:
        with page.expect_file_chooser(timeout=10000) as fc_info:
            if not _click_text_in_dialog(page, text):
                return False
        fc = fc_info.value
        fc.set_files(file_path)
        time.sleep(3)
        return True
    except Exception:
        return False


def linkedin_ensure_resume(page, jid):
    """Replace the LinkedIn Easy Apply resume with the job's tailored PDF.

    Returns:
      True  — resume handled (selected or uploaded) or no resume step / not
              LinkedIn (nothing to do).
      False — resume step present but the tailored PDF could not be placed.
    Runs inline in the fill loop so submit never sends the profile default.
    """
    if not _in_linkedin_dialog(page) or not _resume_step_visible(page):
        return True

    rd = os.path.join(RESULTS_DIR, jid)
    pdf_path = None
    target_name = None
    if os.path.isdir(rd):
        for f in sorted(os.listdir(rd)):
            if "Resume" in f and f.endswith(".pdf"):
                pdf_path = os.path.join(rd, f)
                target_name = f.replace(".pdf", "")
                break
    if not pdf_path or not os.path.exists(pdf_path):
        print(f"RESUME:{jid} no tailored resume PDF found", file=sys.stderr)
        return False

    # 1. Select if already on LinkedIn.
    if _select_resume_by_name(page, target_name):
        print(f"RESUME:{jid} selected {target_name}", file=sys.stderr)
        return True

    # 2. Expand the stored list ("Show 3 more resumes") and re-check.
    _expand_resume_list(page)
    if _select_resume_by_name(page, target_name):
        print(f"RESUME:{jid} selected existing {target_name}", file=sys.stderr)
        return True

    # 3. Upload the tailored PDF.
    if not _upload_file_by_text(page, "Upload resume", pdf_path):
        print(f"RESUME:{jid} upload failed", file=sys.stderr)
        return False
    print(f"RESUME:{jid} uploaded {os.path.basename(pdf_path)}", file=sys.stderr)
    time.sleep(4)

    # 4. Select the freshly uploaded resume.
    if _select_resume_by_name(page, target_name):
        print(f"RESUME:{jid} selected after upload", file=sys.stderr)
    return True


def field_deterministic(page, f, ans):
    """Fill one field deterministically. Returns True on success.

    1. Selector resolution (probe).
    2. Pre-fill validation: catch bad values before they reach the
       widget. Skip option-constraint check for RADIO_GROUP — the
       RadioFiller has its own matching cascade (prefix match, label
       walk, EEOC normalize, negation detection) that's more nuanced
       than a simple substring check. List answers (multi-select) are
       validated per-value inside the fill (fill_field loops them) —
       the str() of a list is not a form value.
    3. Delegate to filler.fill_field (includes post-fill verification).
    """
    sel = f.get("_sel", "")
    if not sel:
        sel = f.get("selector", "")
    if not sel:
        sel = resolve_selector(page, f)
        if not sel:
            return False
    f["_sel"] = sel

    if not _is_combobox(f) and f.get("tag") != "RADIO_GROUP" \
            and not isinstance(ans, list):
        ok, reason = validate_value(f, ans)
        if not ok and reason != "empty":
            emit_diag(f.get("label", ""), str(ans), "", "validation_skip", reason)
            f["_diag"] = {"method": "validation", "reason": reason, "before": "", "after": ""}
            return False

    from apply.common.filler import fill_field
    # DOM-diff observation (DOM_DIFF_OBSERVATION.md): for dynamic fields,
    # capture a minimal structural delta of what the page did in response to
    # the fill — observation ONLY, never used to certify (the read-back check
    # in fill_field remains the certifier). Surface as dom_delta in the dossier.
    from apply.common.dom_diff import (_is_dynamic, start_observation,
                                       drain_summary)
    if _is_dynamic(f):
        _sel = f.get("_sel") or sel
        try:
            if start_observation(page, _sel):
                ok, _filler_name = fill_field(page, f, ans)
                dd = drain_summary(page)
                if dd:
                    f["dom_delta"] = dd
                return ok
        except Exception:
            pass
    ok, _filler_name = fill_field(page, f, ans)
    return ok


def gap_fill_into_answers(fields, profile, answers_override, jid, ephemeral):
    """Mutate answers_override in-place with LLM key-mapping for no_match fields.
    Returns updated answers_override (new dict if None was given).
    Only fills gaps — never overrides existing entries.

    The escape is policy-gated (gap_fill, OFF in auto): when the gate
    closes, the fields surface as needs_data for the orchestrator to
    answer from evidence — never a silent guess.
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
        from lib.automation.llm import allow as _llm_allow
        if not _llm_allow("gap_fill"):
            return answers_override
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
                _ok, _reason = validate_value(_f, _v)
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


def llm_field_key_mapping(fields, profile, job=None, ephemeral=None):
    """LLM maps field labels to profile KEYS. Returns label→value dict.

    The LLM never outputs raw values — it maps each field label to a
    profile KEY. The system looks up the actual value from the profile.
    Job context (title, company, description) helps disambiguate.
    Accepts an optional pre-built ephemeral dict to avoid redundant builds.
    """
    if not fields:
        return {}
    from lib.ask_api import ask_text, available as llm_avail
    if not llm_avail():
        return {}
    if ephemeral is None:
        ephemeral = _build_ephemeral(profile)
    if not ephemeral:
        return {}

    job = job or {}
    lines = [f"Job: {job.get('title', '')}"]
    c = job.get("company", "")
    loc = job.get("location", "")
    desc = job.get("description", "")
    if c:
        lines.append(f"Company: {c}")
    if loc:
        lines.append(f"Location: {loc}")
    if desc:
        lines.append(f"Description: {desc[:500]}")
    lines.append("")
    lines.append("Available profile data (key → value):")
    for k, (v, src) in sorted(ephemeral.items()):
        lines.append(f"  {k}: {str(v)[:80]}")
    lines.append("")
    lines.append("Map each form field to its BEST matching profile KEY:")
    for f in fields:
        label = (f.get("label") or "").strip()
        tag = f.get("tag", f.get("type", "")).upper()
        opts = f.get("options", [])
        if not label:
            continue
        parts = [f"  field: {label[:80]}"]
        if tag or opts:
            parts.append(f"  type: {tag}")
            if opts:
                parts.append(f"  options: {opts[:10]}")
        lines.extend(parts)
    lines.append("")
    lines.append(
        "Return a JSON object mapping each field label to a profile KEY. "
        "Example: {\"Select your country of employment\": \"country\"}. "
        "Only use profile keys from the list above — never invent new ones. "
        "If no profile key matches, set value to null. "
        "Return ONLY the JSON."
    )
    prompt = "\n".join(lines)
    reply, err = ask_text(prompt, temperature=0.1, max_tokens=2048)
    if err or not reply:
        return {}
    try:
        text = reply.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            text = text.rsplit("```", 1)[0].strip()
        data = json.loads(text)
        if not isinstance(data, dict):
            return {}
        result = {}
        for label, profile_key in data.items():
            if not profile_key or not isinstance(profile_key, str):
                continue
            entry = ephemeral.get(profile_key)
            if entry is not None:
                result[label] = entry[0]
        if result:
            for label, val in result.items():
                print(f"    DIAG: LLM_MAP | {label[:50]} | {val[:50]}",
                      file=sys.stderr)
        return result
    except (json.JSONDecodeError, TypeError):
        return {}


def fill_page(page, fields, profile, answers_override=None, filled_keys=None):
    """Fill every field on the page — the one interface the fill path uses.

    Two-phase: heuristic resolve first (deterministic), policy-gated LLM
    gap-fill second; per-field validate → fill → delta-verify lives in
    field_deterministic. Returns (filled, failed) record lists.
    """
    if filled_keys is None:
        filled_keys = set()

    # Bound the work per page. Field count is page-controlled: a
    # pathological or hostile DOM (thousands of inputs, or a widget that
    # regenerates rows) otherwise burns the entire per-job wall clock and
    # the batch records only "timeout" — a silent death with no evidence,
    # which is exactly what ETHOS §6 forbids. Real forms are far below
    # this; exceeding it is itself the finding.
    if len(fields) > MAX_FIELDS_PER_PAGE:
        print(f"  FIELD_CAP: page reported {len(fields)} fields — capping at "
              f"{MAX_FIELDS_PER_PAGE}. This is not a normal form; inspect the "
              f"page before trusting this run.", file=sys.stderr)
        fields = fields[:MAX_FIELDS_PER_PAGE]

    filled = []
    failed = []

    state = load_state()
    jid = state.get("jid", "")

    # LinkedIn Easy Apply: replace the pre-selected resume with the tailored
    # PDF before touching any other field on the page. No-op on other
    # platforms / other steps.
    if jid:
        try:
            if not linkedin_ensure_resume(page, jid):
                print("  RESUME_BLOCK: tailored resume could not be placed — "
                      "do not submit blind", file=sys.stderr)
        except Exception as re_:
            print(f"  RESUME_WARN: {str(re_)[:120]}", file=sys.stderr)

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
    answers_override = gap_fill_into_answers(fields, profile, answers_override, jid, ephemeral)

    # ── Per-field registry fill hints (COMPARISON §S4) ────────────────
    # Registry fill.hints select a platform's fill protocol per field
    # (Workday skills_enter, clear_field_errors). Attach the matching hint
    # keys onto each field so the filler chain can read them.
    try:
        from apply.common.registry import resolve as _resolve_reg_hints
        _hint_url = page.url if isinstance(page.url, str) else ""
        _hint_reg = _resolve_reg_hints(_hint_url) if _hint_url else None
        if _hint_reg is not None:
            _hints = getattr(_hint_reg, "fill_hints", {}) or {}
            if _hints:
                for f in fields:
                    if _hints.get("skills_enter") and _SKILLS_LABEL_RE.search(
                            (f.get("label") or "")):
                        f["hint_skills_enter"] = True
                    if _hints.get("clear_field_errors"):
                        f["hint_clear_field_errors"] = True
    except Exception:
        pass

    for f in fields:
        label = f.get("label", "").strip()
        if not label:
            continue

        tag = (f.get("tag") or "").lower()
        ftype = (f.get("type") or "").lower()
        # Humanization (COMPARISON §S7): randomized inter-field pacing.
        inter_field_delay()
        if _is_upload_field(f):
            path = _file_path_for(label, f, resume_path, cover_path)
            if path and os.path.exists(path):
                try:
                    sel = f.get("_sel") or f.get("selector") or ""
                    if not sel:
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
                        elif _try_show_open_file_picker(page, label, path, sel=sel):
                            # File System Access API upload (2024+ ATS).
                            filled.append({"label": label, "key": _field_key(f),
                                           "method": "show_open_file_picker"})
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
                      ephemeral=ephemeral,
                      domain=_host_of(page))
        ans = res.value
        # Fix 1 completion: a "phone country code" field may be a dropdown of
        # DIALING CODES (+1, +44) rather than country names. The resolver
        # returns the country ("Canada"); if that isn't an offered option but
        # the country's dialing code is, use the code. The data map lives in
        # default_answers.json (no hardcoded values in code).
        if ans is not None and res.provenance == "country_code":
            opts = [str(o) for o in (f.get("options") or [])]
            opt_l = " ".join(opts).lower()
            if opts and str(ans).lower() not in opt_l:
                try:
                    from apply.common.resolve import _load_dialing_codes
                    _code = str(_load_dialing_codes().get(str(ans).lower(), ""))
                    if _code and _code.lower() in opt_l:
                        ans = _code
                except Exception:
                    pass
        if ans is None:
            if (tag == "input" and ftype == "checkbox"
                    and os.environ.get("JI_AUTO_CONSENT") == "1"
                    and _is_consent_field(f)):
                # The consent checked-state value is data (default_answers.json,
                # the auto_only entry), not a hardcoded literal — SEPARATION.md.
                try:
                    from apply.common.resolve import _load_default_answers
                    _consent = next(
                        (v for _p, v, _k, auto in _load_default_answers()
                         if auto), "true")
                except Exception:
                    _consent = "true"
                ans = _consent
            else:
                failed.append({**f, "_why": "no_answer"})
                continue

        try:
            key = _mk_key(f)
            if key in filled_keys:
                continue
            # Skip if the field already has the correct value (prevents
            # double-typing on autocomplete/combobox fields during sweeps).
            # BUT: a value that was already there is PREFILLED, not verified-
            # by-us. The form may have pre-filled a wrong default (e.g. a
            # country from IP geolocation); we cannot certify it. Record it
            # as prefilled so the check gate treats it as unverified.
            sel = f.get("_sel") or f.get("selector") or ""
            if sel:
                current = page.evaluate(f"() => document.querySelector({json.dumps(sel)})?.value || ''")
                if current and (current == ans or current in ans or ans in current):
                    f.setdefault("_diag", {})["unverified"] = True
                    # #2: carry the PREFILLED value so the orchestrator can
                    # veto it before submit — the value is unverified but it
                    # IS in the form (possibly a wrong form default).
                    filled.append({"label": label, "key": key, "answer": str(ans),
                                   "unverified": True, "method": "prefilled",
                                   "prefilled_value": str(current)[:200]})
                    continue
            if field_deterministic(page, f, ans):
                _diag = f.get("_diag") or {}
                filled.append({"label": label, "key": key, "answer": str(ans),
                               "unverified": bool(_diag.get("unverified")),
                               "method": _diag.get("method", "deterministic"),
                               "provenance": res.provenance})
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
                              selector=sel, dom_delta=f.get("dom_delta") or "")
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
