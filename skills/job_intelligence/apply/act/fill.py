"""act/fill.py — Hybrid fill command: Playwright-first."""
import os
import random, re, sys, time

from lib.db import get_conn
from lib.config import RESULTS_DIR
from apply.common import terms as _T
from apply.common.output import emit_next, emit_status, emit_error, emit_fill_report
from apply.common.page_helpers import load_state, save_state, handle_captcha, handle_session_timeout, tag_page
from apply.common.fill_runner import fill_page, _field_key
from apply.act.helpers import (
    _load_profile, chrome_session, _host, _is_error_page, _url_fallbacks,
    _wait_for_fields, _probe_form, _find_next_button,
    _empty_required, _click_action, _verify_with_ask_api, _detect_submit_button,
    _build_ans_dict, _resolve_linkedin_apply, _wire_dialogs,
    _is_junk_field, _dismiss_confirm_modal, _get_validation_errors,
)


def _batch_verify(fields):
    """Send fields to the LLM for batch review.
    Returns dict of field index→verdict to clear,
    or None if the result is unreliable (error or
    suspicious range-dump)."""
    suspect_indices = {
        i for i, f in enumerate(fields)
        if f.get("_suspect")
    }
    clear_indices = [
        i for i, f in enumerate(fields)
        if not f.get("_suspect") and f.get("value")
    ]
    sample = set(random.sample(
        clear_indices,
        min(5, len(clear_indices))
    )) if clear_indices else set()
    check_indices = suspect_indices | sample

    if not check_indices:
        return {}

    from lib.ask_api import ask_text
    lines = []
    for i in sorted(check_indices):
        f = fields[i]
        opt_str = (
            ", ".join(f["options"][:8])
            if f.get("options") else "-"
        )
        lines.append(
            f"[{i}] label=\"{f.get('label', '')}\" "
            f"type={f.get('type', '?')} "
            f"tag={f.get('tag', '?')} "
            f"options=[{opt_str}] "
            f"value=\"{f.get('value', '')}\""
        )
    prompt = (
        "Review these job application field→value pairs. "
        "For each, does the value make sense for the field?\n"
        "Answer ONLY with comma-separated field indices "
        "that are WRONG.\n"
        "If none are wrong, answer NONE.\n\n"
        + "\n".join(lines)
    )
    reply, err = ask_text(prompt, max_tokens=256, temperature=0.1)
    if err or not reply:
        print(f"  LLM_VERIFY_SKIP: {err}", file=sys.stderr)
        return None
    nums = re.findall(r"\d+", reply)
    result = {}
    for n in nums:
        idx = int(n)
        if idx in check_indices:
            result[idx] = "llm_reject"
    if len(result) > len(check_indices) // 2:
        print(f"  LLM_VERIFY_SUSPICIOUS: {len(result)}/"
              f"{len(check_indices)} flagged — rejecting",
              file=sys.stderr)
        return None
    return result


def _scan_capability(page):
    """Cheap capability scan. Returns profile dict or None on failure.
    Used by mid-fill decisions to detect popups, honeypots, and
    shape changes between multi-page iterations."""
    try:
        from apply.common.capabilities import scan
        return scan(page)
    except Exception:
        return None


def _dismiss_popups_if_present(page, profile=None, *, verbose=True):
    """Mid-fill confirm-modal dismissal.

    If `confirm_modal_signals > 0` (visible OK/Submit button modal
    without form inputs), dismiss it before probe/fill — otherwise
    the underlying form is click-intercepted and every field appears
    as no_answer.

    Cheap even when called with profile=None (re-scans). Best-effort:
    never lets dismissal failure abort the fill loop.
    """
    if profile is None:
        profile = _scan_capability(page)
    if not profile:
        return
    if profile.get("confirm_modal_signals", 0) > 0:
        try:
            _dismiss_confirm_modal(page)
            if verbose:
                print("  Dismissed confirm modal mid-fill "
                      f"(confirm_modal_signals={profile['confirm_modal_signals']})",
                      file=sys.stderr)
        except Exception as e:
            if verbose:
                print(f"  POPUP_DISMISS_FAIL: {e}", file=sys.stderr)


def _write_handoff(jid, url, filled_recs, failed_all, state,
                   mode="unknown", error=""):
    """Write a complete structured dossier for the orchestrator (LLM):
    every field's outcome with evidence, blockers, suggested decisions,
    and artifact links. The orchestrator reads this instead of parsing
    stderr fragments."""
    d = os.path.join(RESULTS_DIR, jid)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "handoff.json")

    fields = []
    for r in filled_recs:
        fields.append({
            "label": r.get("label", ""), "answer": r.get("answer", ""),
            "outcome": "filled",
            # The epistemic truth: verified by read-back, or accepted
            # without confirmation (check arbitrates).
            "kind": _T.UNVERIFIED if r.get("unverified") else _T.VERIFIED,
            "method": r.get("method", "deterministic"),
            "reason": "accepted_unverified" if r.get("unverified") else _T.VERIFIED,
        })
    for r in failed_all:
        diag = r.get("_diag") or {}
        is_no_answer = r.get("_why") == "no_answer"
        if is_no_answer:
            kind = _T.NEEDS_DATA
        else:
            kind = (_T.INTERACTION_FAILED if diag.get("reason") == "fill_exception"
                    else _T.REJECTED_BY_FORM)
        fields.append({
            "label": r.get("label", ""), "answer": r.get("attempted", ""),
            "outcome": "no_answer" if is_no_answer else "failed",
            "kind": kind,
            "required": bool(r.get("required")),
            "method": diag.get("method", ""),
            "reason": diag.get("reason", ""),
            "selector": r.get("_sel") or r.get("selector") or "",
            "selected_text": diag.get("after", ""),
            "diag": diag,  # full evidence: options_seen, top_options, stage flags
        })

    # HONEST accounting: rejected/interaction-failed fields and REQUIRED
    # fields with no data are failures — they'd fail validation on submit.
    # THE aggregate: terms.summarize is the single implementation — the
    # DECISION line and the dossier summary can never disagree again.
    failed_labels = _T.failed_labels(fields)
    skipped_labels = _T.skipped_labels(fields)
    summary = _T.summarize(fields)

    blockers = []
    status = state.get("status", "")
    if status == _T.STATUS_LOGIN_REQUIRED:
        domain = ""
        try:
            from urllib.parse import urlparse as _up
            domain = _up(url or "").netloc
        except Exception:
            pass
        blockers.append({"type": _T.STATUS_LOGIN_REQUIRED, "domain": domain,
                         "needs": "account or creds",
                         "next": f"apply.py creds set {domain} <email>  then re-run fill"})
    elif status == _T.STATUS_CAPTCHA_REQUIRED:
        blockers.append({"type": _T.STATUS_CAPTCHA_REQUIRED,
                         "needs": "human solve (or policy captcha_skip)"})
    elif status == _T.STATUS_2FA_REQUIRED:
        blockers.append({"type": _T.STATUS_2FA_REQUIRED,
                         "needs": "complete 2FA in Chrome then re-run fill"})
    elif status == _T.STATUS_TIMED_OUT:
        blockers.append({"type": _T.STATUS_TIMED_OUT,
                         "next": "raise job_timeout_sec or run again (resumable)"})

    decisions = []
    if failed_labels:
        decisions.append({
            "action": "fill --answers",
            "command": f"apply.py act --fill {jid} --answers '{{\"<label>\": \"<value>\"}}'",
            "for": failed_labels[:10],
        })
    for b in blockers:
        decisions.append({"action": b["type"], "for": [b.get("next", "")]})
    decisions.append({"action": "review",
                      "command": f"python3 report.py handoff {jid}"})

    # Aggregate ask_api escape-hatch status for this run — the
    # orchestrator must see policy_off vs api_down vs declined vs used
    # instead of an opaque "llm_reply: no_match".
    llm_status = _T.LLM_UNUSED
    llm_detail = ""
    for f in fields:
        fdiag = f.get("diag") or {}
        if fdiag.get("llm_skipped") == "policy":
            llm_status, llm_detail = _T.LLM_POLICY_OFF, "option_pick gated"
            break
        st = fdiag.get("llm_status") or {}
        if st.get("state") and st["state"] != _T.LLM_UNUSED:
            llm_status, llm_detail = st["state"], st.get("detail", "")
            break

    handoff = {
        "jid": jid, "url": url, "mode": mode,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "error": error,
        "llm_status": llm_status,
        "llm_status_detail": llm_detail,
        # Mutually exclusive counts — THE single aggregate (terms.summarize):
        # filled + failed + skipped_optional = unique field total.
        "summary": summary,
        "fields": fields,
        "blockers": blockers,
        "decisions": decisions,
        "artifacts": {
            "audit": os.path.join(d, "apply_audit.jsonl"),
            "handoff": path,
        },
    }
    # Standard handover format (lib/automation) — writes handoff.json +
    # timestamped history for run-diffing, linked to the event timeline.
    try:
        from lib.automation.dossier import write_dossier
        from lib.automation.obs import current_run_id
        path = write_dossier(
            jid, RESULTS_DIR,
            summary=handoff["summary"], fields=handoff["fields"],
            blockers=blockers, decisions=decisions,
            artifacts=handoff["artifacts"], mode=mode, error=error, url=url,
            run_id=current_run_id())
    except Exception:
        try:
            from lib.config import atomic_write_json
            atomic_write_json(path, handoff)
        except Exception:
            pass

    # Emit structured observations for the session log (always on).
    try:
        from lib.automation.obs import obs
        obs("fill", "end", jid=jid,
            outcome="ok" if not failed_labels and not blockers else "incomplete",
            detail=f"filled={len(filled_recs)} failed={len(failed_labels)} "
                   f"skipped={len(skipped_labels)} blockers={len(blockers)}")
        for f in fields:
            if f["kind"] in (_T.REJECTED_BY_FORM, _T.INTERACTION_FAILED,
                             _T.NEEDS_DATA):
                obs("fill", "field", jid=jid, target=f["label"],
                    pre=f.get("answer", ""), post=f.get("selected_text", ""),
                    outcome=f["kind"],
                    detail=f"{f.get('method', '')}:{f.get('reason', '')} "
                           f"{f.get('selector', '')}")
        for b in blockers:
            obs("fill", "blocker", jid=jid, target=b.get("type", ""),
                detail=b.get("next", ""))
    except Exception:
        pass

    # Compact machine-parseable decision block for the orchestrator —
    # counts derive from THE single aggregate (terms.summarize), so this
    # line can never disagree with the dossier summary again.
    n_rejected = len([f for f in fields
                      if f["kind"] in (_T.REJECTED_BY_FORM,
                                       _T.INTERACTION_FAILED)])
    n_needs = len([f for f in fields
                   if f["kind"] == _T.NEEDS_DATA and f.get("required")])
    n_skipped = len(skipped_labels)
    ok = not failed_labels and not blockers
    print(f"DECISION: job {jid} fill {'OK' if ok else 'INCOMPLETE'}"
          f" (filled={summary[_T.K_FILLED]} rejected={n_rejected}"
          f" needs_data={n_needs} skipped={n_skipped}"
          f" blockers={len(blockers)})", file=sys.stderr)
    for f in fields:
        if f["kind"] in (_T.REJECTED_BY_FORM, _T.INTERACTION_FAILED,
                         _T.NEEDS_DATA):
            why = f"[{f['method']}:{f['reason']}]" if f["method"] else ""
            print(f"  {f['kind'].upper()} {f['label'][:50]} {why}",
                  file=sys.stderr)
    for b in blockers:
        print(f"  BLOCK {b['type']} -> {b.get('next', '')}", file=sys.stderr)
    print(f"HANDOFF: {path}", file=sys.stderr)


def cmd_fill(jid, answers: dict = None, verify: bool = True, max_pages: int = 4,
             quick: bool = False):
    db_row = get_conn().execute(
        "SELECT stage, state FROM jobs WHERE id=?", (jid,)
    ).fetchone()
    if not db_row:
        emit_error(f"job {jid} not found")
        return 1
    stage = db_row["stage"]
    if stage == "applied":
        emit_status("already applied")
        emit_next("verify")
        return 0

    state = load_state()
    if state.get("jid") != jid:
        state = {"jid": jid}
    state["jid"] = jid

    url = state.get("external_url") or state.get("url", "")
    if not url:
        row = get_conn().execute("SELECT url, external_url FROM jobs WHERE id=?", (jid,)).fetchone()
        if row:
            url = row["external_url"] or row["url"]
            state["url"] = row["url"]
            state["external_url"] = row["external_url"] or ""
    if not url:
        emit_error("no URL found — run 'apply detect <jid>' first")
        return 1
    orig_url = url

    profile = _load_profile()
    ans_dict = _build_ans_dict(profile, answers)
    if not ans_dict:
        emit_error("no answers resolved — check profile or --answers")
        return 1

    if answers is None:
        answers = {}

    from apply.common.registry import resolve as resolve_registry

    # Per-job time budget (policy job_timeout_sec; 0 = unlimited).
    # Checked at loop boundaries so a stuck page can't stall a batch.
    deadline = 0.0
    try:
        from apply.common.submit_policy import load_policy as _load_pol
        _tmo = int(_load_pol().get("job_timeout_sec") or 0)
        if _tmo > 0:
            deadline = time.time() + _tmo
    except Exception:
        pass

    def _expired():
        return deadline and time.time() > deadline

    def _abort_timed_out():
        if _expired():
            emit_status(_T.STATUS_TIMED_OUT, "job_timeout_sec exceeded — aborting (resumable)")
            state["status"] = _T.STATUS_TIMED_OUT
            save_state(state)
            return True
        return False

    filled_all, failed_all = [], []
    filled_recs = []
    filled_keys = set()
    field_total = 0
    submit_visible = False

    try:
        with chrome_session(state) as (page, ctx):
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(2)

            if handle_captcha(page, state, wait_s=None if not deadline else max(0, int(deadline - time.time()))):
                emit_status("captcha", "CAPTCHA still present after timeout")
                state["status"] = _T.STATUS_CAPTCHA_REQUIRED
                save_state(state)
                return 1

            if _host(page.url) and _host(url) and _host(page.url) != _host(url):
                print(f"  REDIRECT: {_host(url)} -> {_host(page.url)}", file=sys.stderr)
                state["external_url"] = page.url
                url = page.url

            fallbacks = _url_fallbacks(url, orig_url)
            if _is_error_page(page):
                for alt in fallbacks:
                    print(f"  Landing page broken — trying fallback: {alt[:90]}", file=sys.stderr)
                    try:
                        page.goto(alt, wait_until="domcontentloaded", timeout=30000)
                        time.sleep(2)
                    except Exception:
                        continue
                    if not _is_error_page(page):
                        state["external_url"] = page.url
                        url = page.url
                        break
                fallbacks = []

            from apply.act.helpers import _resolve_standalone_form_url
            standalone = _resolve_standalone_form_url(page)
            if standalone and standalone != page.url:
                print(f"  CROSS-ORIGIN FORM: {page.url[:80]} -> {standalone[:80]}",
                      file=sys.stderr)
                try:
                    page.goto(standalone, wait_until="domcontentloaded", timeout=30000)
                    time.sleep(2)
                    state["external_url"] = page.url or standalone
                    url = state["external_url"]
                    reg = resolve_registry(url) or resolve_registry(orig_url)
                    tag_page(page, jid)
                except Exception as e:
                    print(f"  CROSS-ORIGIN REDIRECT FAILED: {e}", file=sys.stderr)
                    if page.url != url:
                        try:
                            page.goto(url, wait_until="domcontentloaded", timeout=30000)
                        except Exception:
                            pass
                        time.sleep(2)

            tag_page(page, jid)

            if "linkedin.com/jobs" in (page.url or "").lower():
                resolved = _resolve_linkedin_apply(page)
                if resolved:
                    apply_link = page.locator('a:has-text("Apply")').first
                    clicked = False
                    if apply_link.count() > 0:
                        try:
                            with ctx.expect_page(timeout=15000) as new_page_info:
                                apply_link.click()
                            new_page = new_page_info.value
                            new_page.wait_for_load_state("domcontentloaded", timeout=30000)
                            time.sleep(2)
                            try:
                                new_page.wait_for_selector('iframe', timeout=15000)
                                time.sleep(3)
                            except Exception:
                                pass
                            real_url = new_page.url
                            print(f"  LINKEDIN: Apply -> {real_url[:80]}", file=sys.stderr)
                            get_conn().execute(
                                "UPDATE jobs SET external_url=? WHERE id=?", (real_url, jid)
                            ).connection.commit()
                            state["external_url"] = real_url
                            url = real_url
                            page.close()
                            page = new_page
                            _wire_dialogs(page)
                            tag_page(page, jid)
                            clicked = True
                        except Exception:
                            pass
                    if not clicked:
                        print(f"  LINKEDIN: Apply (direct) -> {resolved[:80]}", file=sys.stderr)
                        get_conn().execute(
                            "UPDATE jobs SET external_url=? WHERE id=?", (resolved, jid)
                        ).connection.commit()
                        state["external_url"] = resolved
                        url = resolved
                        page.goto(resolved, wait_until="domcontentloaded", timeout=30000)
                        time.sleep(2)
                        if _host(page.url) and _host(resolved) and _host(page.url) != _host(resolved):
                            state["external_url"] = page.url
                            url = page.url
                else:
                    ea_btn = page.locator('button:has-text("Easy Apply")').first
                    if ea_btn.count() > 0:
                        ea_btn.click()
                        print("  Easy Apply: modal opened", file=sys.stderr)
                        time.sleep(3)
                    else:
                        # Discriminate confirmed-expired from unconfirmed:
                        # LinkedIn renders explicit signals when a posting
                        # is dead — everything else is "apply path not
                        # found", which can be cookie/session variance and
                        # deserves a human/orchestrator look, not a silent
                        # "expired" label.
                        _expired = ""
                        try:
                            _ptxt = (page.evaluate(
                                "() => document.body ? document.body.innerText"
                                ".slice(0, 8000) : ''") or "")
                            for _sig in ("No longer accepting applications",
                                         "This job is no longer available",
                                         "Job has been removed",
                                         "We're sorry, this job is no longer available"):
                                if _sig.lower() in _ptxt.lower():
                                    _expired = _sig
                                    break
                        except Exception:
                            pass
                        if _expired:
                            print(f"  WARN: no Apply link or Easy Apply — {_expired}",
                                  file=sys.stderr)
                            state["status"] = _T.STATUS_NO_APPLY_PATH
                            state["status_detail"] = f"confirmed expired ({_expired[:40]})"
                            save_state(state)
                            emit_status(_T.STATUS_NO_APPLY_PATH, f"confirmed expired ({_expired[:40]})")
                            emit_next("none", "posting closed — skip")
                        else:
                            print("  WARN: no Apply link and no Easy Apply button — apply path not found",
                                  file=sys.stderr)
                            state["status"] = _T.STATUS_NO_APPLY_PATH
                            state["status_detail"] = "unconfirmed — may be cookie/session"
                            save_state(state)
                            emit_status(_T.STATUS_NO_APPLY_PATH, "apply path not found (unconfirmed — may be cookie/session)")
                            emit_next("none", "verify manually or apply via external URL")
                        return 1
                    max_pages = max(max_pages, 6)

            reg = resolve_registry(page.url) or resolve_registry(orig_url)
            if reg and reg.page_range:
                try:
                    max_pages = min(max_pages, int(reg.page_range[-1]))
                except Exception:
                    pass
            from apply.common.page_state import has_form, wait_for_form
            wait_for_form(page, timeout=8)

            if not has_form(page):
                apply_btn = page.locator('a:has-text("Apply"), button:has-text("Apply")').first
                if apply_btn.count() > 0:
                    apply_btn.click()
                    print("  Listing page: clicked Apply", file=sys.stderr)
                    time.sleep(3)
                    wait_for_form(page, timeout=10)

            if not has_form(page):
                for label in ["Apply Manually", "Autofill with Resume"]:
                    btn = page.locator(f'button:has-text("{label}"), a:has-text("{label}")').first
                    if btn.count() > 0:
                        btn.click()
                        print(f"  Apply modal: clicked '{label}'", file=sys.stderr)
                        time.sleep(3)
                        wait_for_form(page, timeout=10)
                        break

            # General no-apply-path check (platform-agnostic).
            # At this point we've tried: LinkedIn Easy Apply/Apply link,
            # generic Apply button, Apply Manually/Autofill modal, and
            # waited 8+10+10s for fields. If no form elements, no dialog,
            # and no iframe with form elements exist, the job is likely
            # expired or the page didn't load a form.
            from apply.common.page_state import has_any_form
            _cwd = reg.widgets if reg and hasattr(reg, 'widgets') else None
            if not has_any_form(page, custom_widget_selectors=_cwd):
                print("  WARN: no form, dialog, or iframe form found — job may be expired", file=sys.stderr)
                state["status"] = _T.STATUS_NO_APPLY_PATH
                state["status_detail"] = "unconfirmed — no form on page"
                save_state(state)
                emit_status(_T.STATUS_NO_APPLY_PATH, "no form or apply path found on page")
                emit_next("none", "job may be expired — skip or apply via external URL")
                return 1

            login_status = _handle_login_wall(page, jid, quick)
            if login_status:
                # Persist so the orchestrator can classify the failure
                # (login wall vs generic exception) instead of treating
                # it as a transient fill error.
                state["status"] = login_status
                save_state(state)
                return 1

            # Post-login 2FA gate: some platforms (Workday, etc.) show the
            # 6-digit code prompt on the page AFTER sign-in succeeds —
            # _login_check can't see it because it runs mid-login. The
            # capability scan catches it here so we stop instead of
            # probing/filling a wall.
            try:
                from apply.common.capabilities import scan as _cap_scan
                _login_profile = _cap_scan(page)
                if _login_profile and _login_profile.get("two_factor_signals"):
                    emit_status(_T.STATUS_2FA_REQUIRED, "2FA interstitial after login — complete in Chrome then rerun")
                    emit_next("login", f"jid={jid} — complete 2FA then rerun fill")
                    state["status"] = _T.STATUS_2FA_REQUIRED
                    save_state(state)
                    return 1
            except Exception:
                pass

            seen = set()
            _followed_apply = False
            for page_num in range(1, max_pages + 1):
                if _abort_timed_out():
                    return 1
                # Edge 1: dismiss any confirmed popups before probing —
                # mid-fill "Please confirm your email" / "Are you sure?"
                # modals click-intercept form fields otherwise.
                _dismiss_popups_if_present(page)
                # Edge 2: re-scan capability per page iteration —
                # multi-step forms often have different shapes on
                # later pages (dialog on page 1 → bare form on page 2).
                # The capability scan is cheap (one page.evaluate) and
                # fresh data beats stale observations.
                page_profile = _scan_capability(page)
                if page_profile:
                    # Mid-page registry refresh if URL changed (some
                    # platforms redirect between submit-page steps).
                    if page.url and page.url != url and "about:blank" not in page.url:
                        new_reg = resolve_registry(page.url)
                        if new_reg:
                            reg = new_reg
                pr = _probe_form(page, reg, jid, allow_vision=(page_num == 1))
                if page.url and page.url != url and "about:blank" not in page.url:
                    state["external_url"] = page.url
                    url = page.url
                fields = pr.fields or []

                # ── History/education entry rows from resume.json ──
                # Greenhouse-style forms render generic labels (Company
                # name, Title, Start date month...) that no profile key
                # matches. The tailored resume.json has the answers —
                # merge them as gap-fill (existing --answers win).
                if jid:
                    try:
                        from apply.act.history import _merge_history_answers
                        hist = _merge_history_answers(fields, jid)
                        if hist:
                            for _hk, _hv in hist.items():
                                answers.setdefault(_hk, _hv)
                    except Exception:
                        pass

                # ── Value validation cascade ──────────────────────────
                # Level 1: cheap code checks (validate.py) on ALL fields.
                # Marks suspect values but does NOT clear them — the fill
                # loop skips suspect values so the orchestrator can supply
                # correct answers via the LLM. Level 2 (LLM batch review)
                # can override Level 1 decisions (see _batch_verify hook).
                from apply.common.validate import validate_value
                for f in fields:
                    val = f.get("value")
                    if val:
                        ok, reason = validate_value(f, val)
                        f["_validation"] = {"valid": ok, "reason": reason}
                        if not ok:
                            f["_suspect"] = True

                # ── Level 2 hook: LLM batch verification ──────────────
                # Batch LLM review of ALL field→value pairs is OFF by
                # default (llm_policy): an LLM re-reviewing the
                # deterministic core lowers accuracy. Level 1 alone
                # decides — it clears suspect values so the orchestrator
                # re-supplies them from reviewed evidence.
                _llm_resolved = False
                try:
                    from lib.automation.llm import allow as _llm_allow
                    if _llm_allow("batch_verify"):
                        from lib.ask_api import available as _llm_avail
                        if _llm_avail():
                            _llm_results = _batch_verify(fields)
                            if _llm_results is not None:
                                _llm_resolved = True
                                for idx in _llm_results:
                                    f = fields[idx]
                                    f["_original_value"] = f["value"]
                                    f["value"] = None
                                    f["_cleared_by"] = "code+llm" if f.get("_suspect") else "llm_only"
                except Exception as _llm_err:
                    print(f"  LLM_VERIFY_ERR: {_llm_err}", file=sys.stderr)

                if not _llm_resolved:
                    # No LLM available, error, or suspicious result —
                    # Level 1 alone decides: clear suspect values so the
                    # orchestrator re-fills them
                    for f in fields:
                        if f.get("_suspect") and f.get("value"):
                            f["_original_value"] = f["value"]
                            f["value"] = None
                            f["_cleared_by"] = "code"

                field_total += len(fields)
                if not fields:
                    print(f"  No fields detected (page {page_num}, strategy={pr.strategy})", file=sys.stderr)
                    if page_num == 1 and fallbacks:
                        alt = fallbacks.pop(0)
                        print(f"  Trying fallback URL: {alt[:90]}", file=sys.stderr)
                        try:
                            page.goto(alt, wait_until="domcontentloaded", timeout=30000)
                            time.sleep(2)
                            _wait_for_fields(page, timeout=8)
                            state["external_url"] = page.url
                            url = page.url
                            reg = resolve_registry(page.url)
                            _followed_apply = False
                        except Exception:
                            pass
                        continue
                    if not _followed_apply:
                        # Job-detail page (Accenture-class): the apply CTA is
                        # a link, not a form. Follow it once before giving up
                        # — the target usually hosts the real form (Workday,
                        # SmartRecruiters, ...) and may open in a new tab.
                        _followed_apply = True
                        try:
                            link = page.locator('a:has-text("Apply")').first
                            if link.count() > 0:
                                print("  No fields yet — following apply link...",
                                      file=sys.stderr)
                                new_page = None
                                try:
                                    with ctx.expect_page(timeout=20000) as npi:
                                        link.click()
                                    new_page = npi.value
                                except Exception:
                                    pass
                                if new_page is None:
                                    try:
                                        with page.expect_navigation(timeout=20000):
                                            link.click()
                                    except Exception:
                                        pass
                                if new_page is not None:
                                    new_page.wait_for_load_state("domcontentloaded", timeout=30000)
                                    page.close()
                                    page = new_page
                                    _wire_dialogs(page)
                                time.sleep(3)
                                _wait_for_fields(page, timeout=10)
                                lw = _handle_login_wall(page, jid, quick)
                                if lw:
                                    state["status"] = lw
                                    save_state(state)
                                    return 1
                                state["external_url"] = page.url
                                url = page.url
                                reg = resolve_registry(page.url)
                                continue
                        except Exception:
                            pass
                    if page_profile and page_profile.get("login_signals"):
                        # Zero-input page with sign-in text after all
                        # navigation attempts = login wall (Workday-class:
                        # no password input for _handle_login_wall to see).
                        _zero_form = (not page_profile.get("visible_text_inputs")
                                      and not page_profile.get("email_fields")
                                      and not page_profile.get("select_elements")
                                      and not page_profile.get("textarea_count"))
                        if _zero_form and not page_profile.get("dialog"):
                            print(f"  LOGIN_WALL: {', '.join(page_profile['login_signals'][:3])}",
                                  file=sys.stderr)
                            emit_status(_T.STATUS_LOGIN_REQUIRED,
                                        f"sign in at {_host(page.url) or page.url}")
                            emit_next("login", f"domain={_host(page.url)} jid={jid}")
                            state["status"] = _T.STATUS_LOGIN_REQUIRED
                            save_state(state)
                            return 1

                filled, failed = fill_page(page, fields, profile, answers,
                                                       filled_keys=filled_keys)
                for rec in filled:
                    if rec["key"] not in filled_keys:
                        filled_keys.add(rec["key"])
                        filled_all.append(rec["label"])
                        filled_recs.append(rec)
                for rec in failed:
                    k = rec.get("key") or _field_key(rec)
                    if k not in filled_keys and k not in {_field_key(r) for r in failed_all}:
                        failed_all.append(rec)

                if fields:
                    print(f"  Page {page_num}: filled {len(filled)}/{len(fields)}"
                          + (f" — failed: {', '.join(r['label'] for r in failed[:5])}" if failed else ""), file=sys.stderr)

                fp = (page.url, tuple(sorted(f.get("label", "") for f in fields)))
                if fp in seen:
                    break
                seen.add(fp)
                nxt = _find_next_button(page)
                if not nxt:
                    submit_visible = bool(_detect_submit_button(page))
                    break
                empt = _empty_required(page)
                if empt:
                    print(f"  {empt} required field(s) still empty — not advancing", file=sys.stderr)
                    break
                print(f"  Multi-page: clicking '{nxt['text']}'", file=sys.stderr)
                if not _click_action(page, nxt["text"]):
                    break
                time.sleep(2)
                has_dialog = page.evaluate("""() => !!document.querySelector('[role="dialog"], dialog')""")
                if has_dialog:
                    for _ in range(10):
                        n = page.evaluate("""() => {
                            const d = document.querySelector('[role="dialog"], dialog');
                            if (!d) return 0;
                            return d.querySelectorAll('input:not([type=hidden]):not([type=submit]), select, textarea').length;
                        }""")
                        if n:
                            break
                        time.sleep(1)
                else:
                    _wait_for_fields(page, timeout=5)
                if handle_captcha(page, state, wait_s=None if not deadline else max(0, int(deadline - time.time()))):
                    emit_status("captcha", "CAPTCHA during multi-page navigation")
                    state["status"] = _T.STATUS_CAPTCHA_REQUIRED
                    save_state(state)
                    return 1
                handle_session_timeout(page)

            # Conditional-reveal sweep: clicking radio/yesno/select values on
            # SPA forms (Ashby, Workday) can reveal NEW required fields
            # that weren't in the original probe. Re-probe and fill any
            # newly-revealed fields that weren't in the original set.
            # Limited to 2 sweeps to avoid infinite loops on dynamic forms.
            for sweep in range(2):
                if _abort_timed_out():
                    return 1
                time.sleep(1)
                # Dismiss popups that may have appeared from previous
                # control clicks (radio/select can trigger helper modals).
                _dismiss_popups_if_present(page)
                pr2 = _probe_form(page, reg, jid, allow_vision=False)
                new_fields = [
                    f for f in (pr2.fields or [])
                    if not _is_junk_field(f)
                    and _field_key(f) not in filled_keys
                    and _field_key(f) not in {_field_key(r) for r in failed_all}
                ]
                if not new_fields:
                    break
                print(f"  Sweep {sweep+1}: {len(new_fields)} new field(s) revealed", file=sys.stderr)
                filled2, failed2 = fill_page(page, new_fields, profile, answers,
                                                         filled_keys=filled_keys)
                for rec in filled2:
                    if rec["key"] not in filled_keys:
                        filled_keys.add(rec["key"])
                        filled_all.append(rec["label"])
                        filled_recs.append(rec)
                for rec in failed2:
                    k = rec.get("key") or _field_key(rec)
                    if k not in filled_keys and k not in {_field_key(r) for r in failed_all}:
                        failed_all.append(rec)
                field_total += len(new_fields)
                if filled2:
                    print(f"  Sweep {sweep+1}: filled {len(filled2)}/{len(new_fields)}", file=sys.stderr)
                if not filled2 and not failed2:
                    break

            remaining_now = [r for r in failed_all if _field_key(r) not in filled_keys]
            if verify and filled_all and not remaining_now and field_total > 0:
                # Screenshot verification of EVERY answer is OFF by default
                # (llm_policy verify_reads): the deterministic re-read
                # verification + check.py arbitration is the verifier; the
                # orchestrator reviews dossiers for the residual
                # unverified reads.
                try:
                    from lib.automation.llm import allow as _llm_allow
                    if _llm_allow("verify_reads"):
                        verify_result = _verify_with_ask_api(page, ans_dict)
                        if not verify_result.get("ok"):
                            mm = verify_result.get("mismatches", [])
                            if mm:
                                print(f"  Vision flag: {len(mm)} field(s) may need review", file=sys.stderr)
                except Exception as ve:
                    print(f"  Vision verify skipped: {ve}", file=sys.stderr)

    except Exception as e:
        emit_error(f"Playwright fill failed: {e}")
        try:
            _write_handoff(jid, orig_url, filled_recs, failed_all, state,
                           mode="shadow" if os.environ.get("JI_APPLY_MODE") == "shadow" else "live",
                           error=str(e)[:200])
        except Exception:
            pass
        return 1

    remaining = [r for r in failed_all if _field_key(r) not in filled_keys]
    skipped = [r for r in remaining if r["_why"] == "no_answer" and not r.get("required")]

    if remaining:
        emit_fill_report(len(filled_all), remaining, 1, profile)
    if skipped:
        skip_labels = [r["label"] for r in skipped]
        print(f"  SKIPPED (optional, no answer): {', '.join(skip_labels)}", file=sys.stderr)

    state["filled_count"] = len(filled_all)
    state["remaining_fields"] = [
        {"label": r["label"], "tag": r.get("tag"), "type": r.get("type"),
         "why": r.get("_why"), "attempted": r.get("attempted", "")[:80]}
        for r in remaining
    ]
    state["skipped_fields"] = [r["label"] for r in skipped]
    # Persist the effective answers used this run so `act --check` can
    # verify the DOM against them (LLM key-mapped --answers and history
    # entries are ephemeral and would otherwise be invisible to it).
    state["fill_answers"] = {**dict(ans_dict), **dict(answers)}
    save_state(state)

    # Hand the structured dossier to the orchestrator.
    try:
        _write_handoff(jid, orig_url, filled_recs, failed_all, state,
                       mode="shadow" if os.environ.get("JI_APPLY_MODE") == "shadow" else "live")
    except Exception:
        pass

    if field_total == 0:
        emit_status("unknown", "no fillable fields found (Playwright)")
        return 1

    msg = f"Playwright: {len(filled_all)} fields"
    if skipped:
        msg += f", skipped optional: {len(skipped)}"
    req_no_answer = [r for r in remaining if r.get("required")]
    if req_no_answer:
        msg += f", {len(req_no_answer)} REQUIRED unanswered"
    emit_status(_T.STATUS_FILLED, msg)

    if submit_visible or filled_all:
        emit_next("check", "run 'apply act --check' to validate before submit")
    else:
        emit_next("act --inspect", "no fillable fields found — inspect to confirm")
    return 0


_LOGIN_JS = r"""() => {
  const pw = document.querySelector('input[type="password"]');
  if (!pw) return null;
  const form = pw.closest('form') || pw.parentElement?.parentElement;
  if (!form) return null;
  const text = (form.textContent || '').toLowerCase();
  const signIn = !!form.querySelector('button[type="submit"], input[type="submit"]')
    || text.includes('sign in') || text.includes('log in') || text.includes('login');
  if (!signIn) return null;
  const emailInput = form.querySelector('input[type="email"], input[name*="email" i], input[name*="user" i]');
  const createLink = [...document.querySelectorAll('a, button')]
    .find(el => /create (an )?account|register|new (user|applicant)|sign up/i.test(el.textContent || ''));
  return {
    hasEmail: !!emailInput,
    createText: createLink ? createLink.textContent.trim().substring(0, 40) : null,
    createTag: createLink ? createLink.tagName : null,
  };
}"""


def _handle_login_wall(page, jid, quick):
    """Detect login walls and auto-login or auto-create account.

    Returns a status string: "" to continue to form fill, or one of
    _T.STATUS_LOGIN_REQUIRED / _T.STATUS_LOGIN_FAILED / _T.STATUS_2FA_REQUIRED to stop. The
    caller persists it into state so the orchestrator can classify
    the fill failure instead of treating it as a generic exception.
    """
    from lib.credentials import (
        get_creds, save_creds, get_account_defaults, _domain_from_url,
    )

    try:
        info = page.evaluate(_LOGIN_JS)
    except Exception:
        return ""
    if not info:
        return ""

    domain = _domain_from_url(page.url)
    print(f"  LOGIN_WALL: {domain}", file=sys.stderr)

    # Try guest apply first — some platforms (Workday, etc.) offer
    # "Continue without signing in" / "Apply as guest". Check registry
    # patterns for the current platform.
    from apply.common.registry import resolve as resolve_registry
    reg = resolve_registry(page.url)
    if reg:
        for pattern in reg.patterns.get("guest_apply", []):
            try:
                btn = page.locator(f'button:has-text("{pattern}"), a:has-text("{pattern}")').first
                if btn.count() > 0 and btn.is_visible(timeout=2000):
                    btn.click(timeout=5000)
                    time.sleep(2)
                    print(f"  GUEST_APPLY: clicked '{pattern}'", file=sys.stderr)
                    return ""
            except Exception:
                continue

    creds = get_creds(domain)
    if creds:
        print(f"  Auto-login: {creds['email']} ({len(creds['passwords'])} password(s))", file=sys.stderr)
        try:
            # Accept cookie banners that can intercept clicks (Workday, etc.)
            for sel in [
                '[data-automation-id="legalNoticeAcceptButton"]',
                'button:has-text("Accept Cookies")',
                'button:has-text("Accept")',
            ]:
                try:
                    bn = page.locator(sel).first
                    if bn.count() > 0 and bn.is_visible():
                        bn.click(timeout=1500)
                        time.sleep(1)
                        print("  Cookies accepted", file=sys.stderr)
                        break
                except Exception:
                    continue

            # Workday defaults to the "Create Account" form with a "Sign
            # In" link. Click it first so the sign-in form renders (the
            # Create Account form has 2 password fields + a checkbox —
            # filling that with saved creds always fails validation).
            for sel in [
                '[data-automation-id="signInLink"]',
                'a:has-text("Sign In")',
                'button:has-text("Sign In")',
            ]:
                try:
                    link = page.locator(sel).first
                    if link.count() > 0 and link.is_visible():
                        link.click(timeout=3000)
                        time.sleep(2)
                        print("  Switched to Sign In form", file=sys.stderr)
                        break
                except Exception:
                    continue

            # Try each password candidate until one succeeds.
            # Strategy: if the first attempt is "uncertain" (SPA slow to
            # transition), wait longer and re-check ONCE before concluding
            # failure and trying the next candidate. This avoids double-
            # submitting with wrong passwords on slow platforms.
            for idx, tried_pw in enumerate(creds["passwords"]):
                _fill_signin_form(page, creds["email"], tried_pw)
                time.sleep(5)
                result = _login_check(page)
                if result == "yes":
                    print(f"  LOGIN: OK with password #{idx+1}", file=sys.stderr)
                    if tried_pw != creds["password"]:
                        try:
                            remaining = [p for p in creds["passwords"] if p != tried_pw]
                            save_creds(domain, creds["email"], tried_pw, passwords=remaining)
                            print(f"  LOGIN: promoted this password to primary for {domain}", file=sys.stderr)
                        except Exception:
                            pass
                    return ""
                if result == "2fa":
                    # Login credentials accepted — platform wants a 2FA
                    # code now. Don't try more passwords (they're all
                    # the same account) — they'd just re-trigger 2FA.
                    # Save the verified password and surface for the
                    # user to complete 2FA manually.
                    print(f"  LOGIN: 2FA required after password #{idx+1} (credentials accepted)",
                          file=sys.stderr)
                    if tried_pw != creds["password"]:
                        try:
                            remaining = [p for p in creds["passwords"] if p != tried_pw]
                            save_creds(domain, creds["email"], tried_pw, passwords=remaining)
                        except Exception:
                            pass
                    emit_status(_T.STATUS_2FA_REQUIRED,
                                f"domain={domain} credentials accepted — "
                                "complete 2FA manually then rerun")
                    emit_next("login",
                              f"domain={domain} jid={jid} — complete 2FA in Chrome then rerun fill")
                    return _T.STATUS_2FA_REQUIRED
                if result == "uncertain":
                    # SPA may be slow — wait longer and re-check once.
                    time.sleep(5)
                    if _login_check(page) in ("yes", "uncertain"):
                        # If still uncertain, assume success (don't risk
                        # trying more passwords and locking the account).
                        print(f"  LOGIN: assuming OK (uncertain after extended wait) with password #{idx+1}", file=sys.stderr)
                        if tried_pw != creds["password"]:
                            try:
                                remaining = [p for p in creds["passwords"] if p != tried_pw]
                                save_creds(domain, creds["email"], tried_pw, passwords=remaining)
                            except Exception:
                                pass
                        return ""
                    # Re-check said "no" — fall through to try next
                # result == "no" — try next candidate
                _re_open_signin_form(page)
            print(f"  LOGIN: all {len(creds['passwords'])} password(s) failed", file=sys.stderr)
            emit_status(_T.STATUS_LOGIN_FAILED, f"all {len(creds['passwords'])} password(s) rejected by {domain}")
            emit_next("login", f"domain={domain} jid={jid} — update creds via 'apply.py creds set {domain} <email>'")
            return _T.STATUS_LOGIN_FAILED
        except Exception as e:
            print(f"  LOGIN_FAIL: {e}", file=sys.stderr)
            return ""

    if info.get("createText"):
        print(f"  CREATE_ACCOUNT: clicking '{info['createText']}'", file=sys.stderr)
        try:
            btn = page.locator(
                f'{info["createTag"].lower()}:has-text("{info["createText"]}")'
            ).first
            if btn.count() > 0:
                btn.click(force=True, timeout=5000)
                time.sleep(3)
        except Exception:
            pass

    defaults = get_account_defaults()
    if not defaults.get("email"):
        print(f"  LOGIN_REQUIRED: no creds for {domain}, no profile email", file=sys.stderr)
        emit_status(_T.STATUS_LOGIN_REQUIRED, f"create account at {domain}")
        emit_next("login", f"domain={domain} jid={jid}")
        return _T.STATUS_LOGIN_REQUIRED

    pw_inputs = page.query_selector_all('input[type="password"]')
    if not pw_inputs:
        print(f"  LOGIN_REQUIRED: no creds for {domain}", file=sys.stderr)
        emit_status(_T.STATUS_LOGIN_REQUIRED, f"sign in or create account at {domain}")
        emit_next("login", f"domain={domain} jid={jid}")
        return _T.STATUS_LOGIN_REQUIRED

    # Pick a password that satisfies platform complexity rules, preferring
    # the user's shared password pool entries when applicable so account
    # creation stays consistent with manual accounts the user already has.
    # If none fit, the local LLM (ask_api) generates a new password in
    # the same style, satisfying the platform's rules. The new password
    # is saved to the shared pool on successful account creation below.
    from lib.credentials import (
        get_shared_passwords, pick_password_for_platform, gen_password_for_platform,
    )
    shared_pws = get_shared_passwords()
    new_pw = pick_password_for_platform(page.url, shared_pws, page=page)
    if not new_pw:
        # No existing password fits the platform's rules.
        # Local LLM (if available) will read page text + rules and
        # generate a password in the same style as the user's previous
        # passwords. Falls back to a secure random generator if LLM
        # unavailable or returns an unusable password.
        new_pw = gen_password_for_platform(page.url, page=page, existing_pws=shared_pws)
        print(f"  GEN_PASSWORD: generated new password (len={len(new_pw)}) for {domain}", file=sys.stderr)
    try:
        email_input = page.locator('input[type="email"], input[name*="email" i]').first
        if email_input.count() > 0:
            email_input.fill(defaults["email"])
        for pwi in pw_inputs:
            pwi.fill(new_pw)
        # Workday Create Account form has a mandatory checkbox
        # (data-automation-id="createAccountCheckbox") — "I understand..."
        try:
            cb = page.locator('[data-automation-id="createAccountCheckbox"]').first
            if cb.count() > 0 and not cb.is_checked():
                cb.click(timeout=2000)
                print("  Checked create-account acknowledgement", file=sys.stderr)
        except Exception:
            pass
        first_input = page.locator('input[name*="first" i], input[name*="given" i]').first
        if first_input.count() > 0:
            first_input.fill(defaults.get("first_name", ""))
        last_input = page.locator('input[name*="last" i], input[name*="family" i]').first
        if last_input.count() > 0:
            last_input.fill(defaults.get("last_name", ""))
        submit = page.locator('button[type="submit"], input[type="submit"], [data-automation-id="createAccountSubmitButton"]').first
        if submit.count() > 0:
            try:
                submit.click(timeout=5000)
            except Exception:
                submit.click(force=True, timeout=5000)
            time.sleep(5)
            # Verify account creation succeeded before saving creds.
            # Heuristic: Create Account form is gone (no createAccountSubmitButton
            # visible) AND no error text about password/email mismatch.
            create_result = _check_account_created(page)
            if create_result in ("yes", "uncertain"):
                save_creds(domain, defaults["email"], new_pw)
                try:
                    from lib.credentials import add_shared_password
                    add_shared_password(new_pw)
                except Exception:
                    pass
                print(f"  ACCOUNT_CREATED: {defaults['email']} @ {domain} — creds saved (also added to shared pool)", file=sys.stderr)
                return ""
            if create_result == "exists":
                # Email already registered — try signing in with the
                # generated password and any shared candidates before
                # handing off to the manual step.
                print(f"  ACCOUNT_EXISTS: {defaults['email']} @ {domain} — trying sign-in with known passwords", file=sys.stderr)
                _re_open_signin_form(page)
                signin_tried = [new_pw] + list(get_shared_passwords())
                for pw in signin_tried:
                    _fill_signin_form(page, defaults["email"], pw)
                    time.sleep(4)
                    r = _login_check(page)
                    if r == "yes":
                        save_creds(domain, defaults["email"], pw)
                        print(f"  LOGIN: OK on existing account (password matched known pool)", file=sys.stderr)
                        return ""
                    if r == "2fa":
                        save_creds(domain, defaults["email"], pw)
                        emit_status(_T.STATUS_2FA_REQUIRED,
                                    f"domain={domain} credentials accepted — "
                                    "complete 2FA manually then rerun")
                        emit_next("login", f"domain={domain} jid={jid}")
                        return _T.STATUS_2FA_REQUIRED
                    _re_open_signin_form(page)
                print(f"  ACCOUNT_EXISTS: no known password works — create/reset manually", file=sys.stderr)
            print(f"  CREATE_FAIL: account creation rejected ({create_result})", file=sys.stderr)
            try:
                for _e in _get_validation_errors(page)[:6]:
                    print(f"    ! {_e[:110]}", file=sys.stderr)
            except Exception:
                pass
            emit_status(_T.STATUS_LOGIN_REQUIRED, f"account creation rejected at {domain}")
            emit_next("login", f"domain={domain} jid={jid} — create account manually, then 'apply.py creds set {domain} {defaults.get('email','<email>')}'")
            return _T.STATUS_LOGIN_REQUIRED
    except Exception as e:
        print(f"  CREATE_FAIL: {e}", file=sys.stderr)
    emit_status(_T.STATUS_LOGIN_REQUIRED, f"account creation failed at {domain}")
    emit_next("login", f"domain={domain} jid={jid}")
    return _T.STATUS_LOGIN_REQUIRED


def _check_account_created(page):
    """Heuristic: did account creation succeed?

    Returns 'yes', 'no', 'exists', or 'uncertain' — same protocol as
    _login_check. 'exists' means the email is already registered, so the
    caller should attempt sign-in instead of create.
    """
    try:
        result = page.evaluate("""() => {
            const txt = (document.body.innerText || '').toLowerCase();
            const createBtn = document.querySelector('[data-automation-id="createAccountSubmitButton"]');
            const createVisible = createBtn && createBtn.offsetParent !== null;
            const pws = Array.from(document.querySelectorAll('input[type="password"]'))
                .filter(p => p.offsetParent !== null);

            // Success indicators: form gone, greeting, next-step content
            const greetings = /welcome|signed in|my account|log out|sign out|continue|step 2|personal information|my information|next step/im;
            if (greetings.test(txt) && !createVisible) return 'yes';
            if (!createVisible && pws.length === 0) return 'yes';

            // Account already registered — checked BEFORE generic errors
            // (the generic list contains "email.*already" too).
            if (/already (have|has|registered)|already exists|account.*already exists|email.*already|already in use|already used/i.test(txt)) return 'exists';

            // Failure indicators: validation errors
            const errors = /password.*do not match|passwords.*match|password.*weak|password.*requirement|account.*exists|please enter|missing|incomplete|invalid email|check the box|must contain|at least/im;
            if (errors.test(txt)) return 'no';

            // Form still visible — could be loading
            if (createVisible) return 'uncertain';
            return 'uncertain';
        }""")
        return result or 'uncertain'
    except Exception:
        return 'uncertain'


# ─── login helper functions used by _handle_login_wall ───────────────

def _fill_signin_form(page, email, password):
    """Fill a sign-in form atomically via page.evaluate.

    Targets the visible sign-in form (1 password field, prefers
    data-automation-id=signInSubmitButton). Falls back to generic
    locators if no atomic form is found.
    """
    result = page.evaluate("""(creds) => {
        const forms = Array.from(document.querySelectorAll('form'));
        for (const f of forms) {
            if (f.offsetParent === null) continue;
            const pws = f.querySelectorAll('input[type="password"]');
            if (pws.length !== 1) continue;
            const btn = f.querySelector('[data-automation-id="signInSubmitButton"]');
            if (!btn) continue;
            const emailEl = f.querySelector('input[type="email"], input[type="text"]');
            if (emailEl) {
                emailEl.focus();
                emailEl.value = creds.email;
                emailEl.dispatchEvent(new Event('input', {bubbles: true}));
                emailEl.dispatchEvent(new Event('change', {bubbles: true}));
            }
            pws[0].focus();
            pws[0].value = creds.password;
            pws[0].dispatchEvent(new Event('input', {bubbles: true}));
            pws[0].dispatchEvent(new Event('change', {bubbles: true}));
            btn.click();
            return true;
        }
        return false;
    }""", {"email": email, "password": password})
    if result:
        return
    # Generic fallback
    try:
        ei = page.locator('input[type="email"], input[name*="email" i], input[name*="user" i]').first
        if ei.count() > 0:
            ei.fill(email)
        pi = page.locator('input[type="password"]').first
        if pi.count() > 0:
            pi.fill(password)
        sb = page.locator(
            '[data-automation-id="signInSubmitButton"], '
            'button:has-text("Sign In"), '
            'button[type="submit"], input[type="submit"]'
        ).first
        if sb.count() > 0:
            try:
                sb.click(timeout=5000)
            except Exception:
                sb.click(force=True, timeout=5000)
    except Exception:
        pass


def _login_check(page):
    """Three-way heuristic: did the just-submitted sign-in form succeed?

    Returns:
      "yes"      — confidently logged in (password field gone, OR
                   greeting text found, OR sign-in form disappeared)
      "no"       — confidently failed (error text found)
      "2fa"      — login succeeded but 2FA code is now required.
                   Caller must NOT treat this as success — leave the
                   user at the 2FA prompt so they can complete it.
      "uncertain"— form still visible, no error, no greeting — could be
                   slow SPA transition. Caller should wait and re-check.
    """
    try:
        result = page.evaluate(r"""() => {
            const txt = (document.body.innerText || '').toLowerCase();
            const visiblePws = Array.from(document.querySelectorAll('input[type="password"]'))
                .filter(p => p.offsetParent !== null);
            const signInBtn = document.querySelector('[data-automation-id="signInSubmitButton"]');
            const signInVisible = signInBtn && signInBtn.offsetParent !== null;
            const createAcctBtn = document.querySelector('[data-automation-id="createAccountSubmitButton"]');
            const createAcctVisible = createAcctBtn && createAcctBtn.offsetParent !== null;

            // Greetings indicate success regardless of form state.
            const greetings = /signed in as|welcome back|my account|log out|sign out|hi,\\s|hello,\\s/im;
            if (greetings.test(txt)) return 'yes';

            // 2FA interstitial — detect BEFORE error checks, since
            // an accompanying "verification required" message can
            // contain 'incorrect'-like phrases that would misroute
            // to 'no'.
            // Triggers: numeric input with maxlength 4-8, OR
            // autocomplete='one-time-code', OR explicit body phrase.
            const twoFAinputs = document.querySelectorAll(
                'input[autocomplete*="one-time-code" i],'
                + 'input[inputmode="numeric"][maxlength],'
                + 'input[type="tel"][maxlength]'
            );
            for (const inp of twoFAinputs) {
                if (inp.offsetParent === null) continue;
                const ml = parseInt(inp.getAttribute('maxlength') || '0', 10);
                const ac = inp.getAttribute('autocomplete') || '';
                if ((ml >= 4 && ml <= 8) || /one-time-code/i.test(ac)) {
                    return '2fa';
                }
            }
            if (/\\b\d{1,2}-?digit (code|verification|otp)\\b/.test(txt)
                || /two-?factor|2fa|verification code|authentication code|enter the code/i.test(txt)) {
                return '2fa';
            }

            // Error text indicates failure.
            const errors = /invalid email|incorrect password|password incorr|account does not exist|no account found|account is locked|failed login|wrong password|email or password is incorrect/im;
            if (errors.test(txt)) return 'no';

            // No password fields visible — form likely closed → success.
            if (visiblePws.length === 0) return 'yes';

            // Sign-in button gone but password still visible — ambiguous.
            if (!signInVisible && !createAcctVisible) return 'uncertain';

            // Sign-in form still visible — likely failed, but could be slow.
            return 'uncertain';
        }""")
        return result or 'uncertain'
    except Exception:
        return 'uncertain'


def _re_open_signin_form(page):
    """If a wrong-password attempt left us on a non-sign-in tab (e.g. back to
    Create Account), re-click the Sign In link so the next candidate can be
    tried without manual interaction.
    """
    try:
        for sel in [
            '[data-automation-id="signInLink"]',
            'a:has-text("Sign In")',
            'button:has-text("Sign In")',
        ]:
            try:
                link = page.locator(sel).first
                if link.count() > 0 and link.is_visible():
                    link.click(timeout=1500)
                    time.sleep(1)
                    return
            except Exception:
                continue
    except Exception:
        pass
