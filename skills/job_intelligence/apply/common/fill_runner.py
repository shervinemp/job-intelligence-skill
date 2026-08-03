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
import os
import sys

from lib.config import JI_HOME
from apply.common.field_types import is_combobox as _is_combobox
from apply.common.output import emit_diag
from apply.common.page_helpers import load_state
from apply.common.resolve import resolve, learn_mapping, _build_ephemeral
from apply.common.validate import validate_value
from apply.steps.probe import resolve_selector

RESULTS_DIR = os.path.join(JI_HOME, "results")


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


def fill_page(page, fields, profile, answers_override=None, filled_keys=None):
    """Fill every field on the page — the one interface the fill path uses.

    Two-phase: heuristic resolve first (deterministic), policy-gated LLM
    gap-fill second; per-field validate → fill → delta-verify lives in
    field_deterministic. Returns (filled, failed) record lists.
    """
    from apply.act.helpers import (
        _field_key, _is_upload_field, _file_path_for,
        _set_files_any_frame, _try_filechooser_upload, _is_consent_field,
    )
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
    answers_override = gap_fill_into_answers(fields, profile, answers_override, jid, ephemeral)

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
                current = page.evaluate(f"() => document.querySelector({json.dumps(sel)})?.value || ''")
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
