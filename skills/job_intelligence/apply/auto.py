#!/usr/bin/env python3
"""auto.py — Autonomous apply pipeline orchestrator.

Walks every tailored job through: detect -> navigate -> fill -> check -> submit.
On fill failure: inspect -> LLM key-map -> retry -> stop for review if still fails.
On submit failure: LLM key-map errored fields -> re-fill -> re-submit.

Usage:
  python apply.py auto                           All tailored jobs
  python apply.py auto --jid <jid>               Single job
  python apply.py auto --quick                   Deterministic-only
  python apply.py auto --max-pages N             Max form pages (default 4)
"""
import os, sys, time, re
from apply.common import terms as _T

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


_ERROR_LABEL_RE = re.compile(
    r"(?:Missing entry for required field:\s*)?(.+?)\s*[.。]?\s*$"
)


def _extract_error_labels(errors):
    seen = set()
    out = []
    for e in errors:
        m = _ERROR_LABEL_RE.match(e.strip())
        if m:
            label = m.group(1).strip().rstrip(".")
            if label and label not in seen:
                seen.add(label)
                out.append(label)
    return out


def run(jid=None, quick=False, max_pages=4):
    from lib.db import get_jobs_by_stage, get_job

    if jid:
        job = get_job(jid)
        if not job:
            print(f"ERROR: job {jid} not found", file=sys.stderr)
            return 1
        jobs = [(jid, job)]
    else:
        jobs = get_jobs_by_stage("tailored")
        if not jobs:
            print("NO_TAILORED: no jobs ready to apply", file=sys.stderr)
            return 0

    N = len(jobs)
    print(f"\nAUTO: {N} job(s) to process (stage=tailored)", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)

    results = {_T.K_RES_SUBMITTED: [], _T.K_RES_STOPPED: [], _T.K_RES_SKIPPED: [], _T.K_RES_ALREADY_APPLIED: []}

    for i, (jid, job) in enumerate(jobs):
        title = job.get("title", "?")
        company = job.get("company", "?")
        print(f"\n[{i+1}/{N}] {jid[:12]} -- {title} @ {company}", file=sys.stderr)
        print(f"{'-'*60}", file=sys.stderr)

        try:
            _process_one(jid, job, quick, max_pages, results)
        except Exception as e:
            print(f"  ERROR: {e}", file=sys.stderr)
            results[_T.K_RES_SKIPPED].append((jid, f"exception: {e}"))

        if i < N - 1:
            time.sleep(2)

    _print_summary(results)
    return 0 if not results["skipped"] else 1


def _no_apply_path_detail(state):
    """Outcome detail for a no_apply_path failure, discriminating
    confirmed-expired from unconfirmed (cookie/session variance)."""
    _emitted = (state.get("status_detail") or state.get("status_message") or "")
    if "unconfirmed" in _emitted:
        return "no apply path (unconfirmed — may be cookie/session)"
    if "confirmed" in _emitted:
        return "no apply path (confirmed expired)"
    return "no apply path (expired)"


def _retry_fill_with_llm(jid, job, results):
    # Policy-gated (auto_retry, OFF by default): the pipeline does not
    # auto-retry with LLM-mapped answers — that hides evidence and
    # guesses inside the hot path. Failures surface in the evidence
    # trail; the ORCHESTRATOR retries from reviewed evidence.
    from apply.common.llm_policy import allow as _llm_allow
    if not _llm_allow("auto_retry"):
        return False
    from apply.common.page_helpers import load_state
    from apply.act.fill import cmd_fill
    from apply.act.check import cmd_check
    from lib.ask_api import available as llm_avail
    from apply.act.helpers import _load_profile
    from apply.act.suggest import llm_field_key_mapping

    if not llm_avail():
        print("  LLM unavailable — cannot retry fill", file=sys.stderr)
        return False

    profile = _load_profile()
    st = load_state()
    remaining = st.get("remaining_fields", [])
    labels = [r.get("label", "") for r in remaining if r.get("label")]
    if not labels:
        print("  No unfilled field labels to map", file=sys.stderr)
        return False

    fake_fields = [{"label": lbl} for lbl in labels]
    mapping = llm_field_key_mapping(fake_fields, profile, job)
    if not mapping:
        print("  LLM returned no mapping for unfilled fields", file=sys.stderr)
        return False

    print(f"  LLM mapped {len(mapping)} unfilled field(s) — re-filling", file=sys.stderr)
    rc = cmd_fill(jid, answers=mapping, verify=False, max_pages=4, quick=True)
    if rc != 0:
        print("  Re-fill failed after LLM mapping", file=sys.stderr)
        return False

    rc = cmd_check(jid)
    if rc != 0:
        print("  Re-check failed after LLM mapping", file=sys.stderr)
        return False
    return True


def _retry_submit_with_llm(jid, job, results):
    # Policy-gated (auto_retry, OFF by default) — see _retry_fill_with_llm.
    from apply.common.llm_policy import allow as _llm_allow
    if not _llm_allow("auto_retry"):
        return False
    from apply.common.page_helpers import load_state
    from apply.act.fill import cmd_fill
    from apply.act.check import cmd_check
    from apply.act.submit import cmd_submit
    from lib.ask_api import available as llm_avail
    from apply.act.helpers import _load_profile
    from apply.act.suggest import llm_field_key_mapping

    def _stage():
        from lib.db import get_conn
        row = get_conn().execute("SELECT stage FROM jobs WHERE id=?", (jid,)).fetchone()
        return row["stage"] if row else ""

    if not llm_avail():
        print("  LLM unavailable — cannot retry submit", file=sys.stderr)
        return False

    st = load_state()
    errors = st.get("submit_errors", [])
    labels = _extract_error_labels(errors)
    if not labels:
        print("  No parseable field labels in errors", file=sys.stderr)
        return False

    profile = _load_profile()
    fake_fields = [{"label": lbl} for lbl in labels]
    mapping = llm_field_key_mapping(fake_fields, profile, job)
    if not mapping:
        print("  LLM returned no mapping — giving up", file=sys.stderr)
        return False

    print(f"  LLM mapped {len(mapping)} errored field(s) — re-filling", file=sys.stderr)
    rc = cmd_fill(jid, answers=mapping, verify=False, max_pages=4, quick=True)
    if rc != 0:
        print("  Re-fill failed after LLM mapping", file=sys.stderr)
        return False

    rc = cmd_check(jid)
    if rc != 0:
        print("  Re-check failed after LLM mapping", file=sys.stderr)
        return False

    print("  Re-check passed — re-submitting", file=sys.stderr)
    rc = cmd_submit(jid, confirm=True)
    if rc == 0 and _stage() == "applied":
        return True
    print("  Re-submit failed after LLM retry", file=sys.stderr)
    return False


def _process_one(jid, job, quick, max_pages, results):
    from apply.detect import run as detect_run
    from apply.navigate import run as navigate_run
    from apply.act.fill import cmd_fill
    from apply.act.check import cmd_check
    from apply.act.submit import cmd_submit
    from apply.common.page_helpers import load_state
    from lib.db import get_conn, find_duplicate
    from apply.common.policy import resolve_mode

    def _stage():
        row = get_conn().execute("SELECT stage FROM jobs WHERE id=?", (jid,)).fetchone()
        return row["stage"] if row else ""

    title = job.get("title", "")
    company = job.get("company", "")
    if title and company:
        dup = find_duplicate(jid, title, company)
        if dup and dup["stage"] == "applied":
            results[_T.K_RES_ALREADY_APPLIED].append(
                (jid, f"duplicate of applied {dup['id'][:12]}"))
            print(f"  SKIP -- duplicate of already-applied {dup['id'][:12]}",
                  file=sys.stderr)
            return

    # ── Step 1: detect ──────────────────────────────────────────────
    print("  detect...", file=sys.stderr, end=" ")
    rc = detect_run(jid)
    if rc != 0:
        results[_T.K_RES_SKIPPED].append((jid, "detect failed"))
        return

    state = load_state()
    jtype = state.get("type", "")
    print(f"type={jtype}", file=sys.stderr)

    if jtype == _T.TYPE_ALREADY_APPLIED:
        results[_T.K_RES_ALREADY_APPLIED].append((jid, "already applied"))
        return

    if jtype in ("unknown", ""):
        results[_T.K_RES_SKIPPED].append((jid, "unknown type -- can't classify URL"))
        print("  SKIP -- unknown type", file=sys.stderr)
        return

    # ── Step 2: navigate (only for external type) ───────────────────
    if jtype == "external":
        print("  navigate...", file=sys.stderr)
        rc = navigate_run(jid)
        if rc != 0:
            results[_T.K_RES_SKIPPED].append((jid, "navigate failed"))
            return

    # ── Step 3: fill ────────────────────────────────────────────────
    print("  fill...", file=sys.stderr)
    rc = cmd_fill(jid, answers=None, verify=not quick,
                  max_pages=max_pages, quick=quick)
    if rc != 0:
        if _stage() == "applied":
            results[_T.K_RES_ALREADY_APPLIED].append((jid, "already applied"))
            return
        st = load_state()
        # Transient exception (no status set — e.g. execution-context
        # destroyed during a lazy page navigation): retry once before
        # treating the job as failed. Re-fill is safe — filled fields
        # are deduped and already-set values are skipped.
        if not st.get("status"):
            print("  FILL_EXCEPTION — retrying once...", file=sys.stderr)
            time.sleep(3)
            rc = cmd_fill(jid, answers=None, verify=not quick,
                          max_pages=max_pages, quick=quick)
            st = load_state()
        if rc != 0:
            if st.get("status") == _T.STATUS_NO_APPLY_PATH:
                # Discriminate confirmed-expired from unconfirmed
                # (cookie/session variance deserves a look, not a silent
                # "expired" label). Only auto-reject in live mode.
                _detail = _no_apply_path_detail(st)
                if resolve_mode() == "live":
                    from lib.db import advance_job
                    advance_job(jid, "tailored", state="rejected",
                                error=_detail)
                results[_T.K_RES_SKIPPED].append((jid, _detail))
                return
            if st.get("status") in (_T.STATUS_LOGIN_REQUIRED, _T.STATUS_LOGIN_FAILED,
                                    _T.STATUS_CAPTCHA_REQUIRED, _T.STATUS_TIMED_OUT):
                results[_T.K_RES_SKIPPED].append((jid, f"fill failed: {st.get('status')}"))
                return
            print("  FILL_FAILED — inspecting...", file=sys.stderr)
            try:
                from apply.act.inspect import cmd_inspect as inspect_run
                inspect_run(jid)
            except Exception as ie:
                print(f"  INSPECT_ERR: {ie}", file=sys.stderr)
            succeeded = _retry_fill_with_llm(jid, job, results)
            if succeeded:
                print("  Fill succeeded on LLM retry", file=sys.stderr)
            else:
                results[_T.K_RES_STOPPED].append((jid, "fill failed with diagnostic — review inspect output"))
                print("  STOPPED — fill failed after LLM retry, inspect for context", file=sys.stderr)
                return

    if _stage() == "applied":
        results[_T.K_RES_ALREADY_APPLIED].append((jid, "already applied"))
        return

    # ── Step 4: check ───────────────────────────────────────────────
    print("  check...", file=sys.stderr)
    rc = cmd_check(jid)
    if rc != 0:
        results[_T.K_RES_STOPPED].append((jid, "check failed -- supply answers and retry"))
        print("  STOPPED -- check failed, orchestrator review needed", file=sys.stderr)
        return

    print("  check passed", file=sys.stderr)

    # ── Step 5: submit ──────────────────────────────────────────────
    print("  submit...", file=sys.stderr)
    rc = cmd_submit(jid, confirm=True)
    if rc != 0:
        if _stage() == "applied":
            results[_T.K_RES_SUBMITTED].append((jid, "submitted"))
            return
        st = load_state()
        if st.get("submit_errors") and st.get("submit_clicked") is False:
            print("  VALIDATION_ERROR — retrying with LLM key-mapping",
                  file=sys.stderr)
            succeeded = _retry_submit_with_llm(jid, job, results)
            if succeeded:
                results[_T.K_RES_SUBMITTED].append((jid, "submitted (LLM retry)"))
                return
            results[_T.K_RES_SKIPPED].append((jid, "submit failed after LLM retry"))
        else:
            results[_T.K_RES_SKIPPED].append((jid, "submit failed"))
        return

    if _stage() == "applied":
        results[_T.K_RES_SUBMITTED].append((jid, "submitted"))
    else:
        results[_T.K_RES_STOPPED].append((jid, "submit returned 0 but stage not applied"))


def _print_summary(results):
    print(f"\n{'='*60}", file=sys.stderr)
    n_sub = len(results["submitted"])
    n_stop = len(results["stopped"])
    n_skip = len(results["skipped"])
    n_already = len(results["already_applied"])
    print(f"SUMMARY: {n_sub} submitted, {n_stop} stopped (review), "
          f"{n_skip} skipped, {n_already} already applied", file=sys.stderr)

    if results[_T.K_RES_SUBMITTED]:
        print("\n  SUBMITTED:", file=sys.stderr)
        for jid, detail in results["submitted"]:
            print(f"    {jid[:12]} -- {detail}", file=sys.stderr)

    if results[_T.K_RES_STOPPED]:
        print("\n  STOPPED (orchestrator review needed):", file=sys.stderr)
        for jid, detail in results["stopped"]:
            print(f"    {jid[:12]} -- {detail}", file=sys.stderr)

    if results["skipped"]:
        print("\n  SKIPPED:", file=sys.stderr)
        for jid, detail in results["skipped"]:
            print(f"    {jid[:12]} -- {detail}", file=sys.stderr)

    if results[_T.K_RES_ALREADY_APPLIED]:
        print("\n  ALREADY APPLIED:", file=sys.stderr)
        for jid, detail in results["already_applied"]:
            print(f"    {jid[:12]} -- {detail}", file=sys.stderr)
