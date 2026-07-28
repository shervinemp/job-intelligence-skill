#!/usr/bin/env python3
"""auto.py — Autonomous apply pipeline orchestrator.

Walks every tailored job through: detect -> navigate -> fill -> check -> submit.
Stops a job when check fails (needs orchestrator input for unresolved fields).
Continues to the next job regardless.

Usage:
  python apply.py auto                    Process all tailored jobs
  python apply.py auto --jid <jid>        Process a single job
  python apply.py auto --limit N          Cap at N jobs
  python apply.py auto --no-submit        Stop after check passes
  python apply.py auto --quick            Deterministic-only (no vision/Skyvern)
  python apply.py auto --max-pages N      Max form pages (default 4)
  python3 report.py candidates            Preview classification and counts
"""
import json, os, sys, time, re

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


_ERROR_LABEL_RE = re.compile(
    r"(?:Missing entry for required field:\s*)?(.+?)\s*[.。]?\s*$"
)


def _extract_error_labels(errors):
    """Extract unique field labels from validation error messages.
    Handles both 'Missing entry for required field: X' and bare 'X' patterns."""
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


def _llm_supply_answers(job_title, field_labels, existing_answers=None):
    """Ask LLM what values to supply for missing fields. Returns label→value dict."""
    if not field_labels:
        return {}
    from lib.ask_api import ask_text, available as llm_avail
    if not llm_avail():
        return {}
    existing = existing_answers or {}
    lines = [f"Job: {job_title}"]
    if existing:
        lines.append("")
        lines.append("Existing profile answers (for context):")
        for k, v in sorted(existing.items()):
            vstr = str(v)[:80]
            lines.append(f"  {k}: {vstr}")
    lines.append("")
    lines.append("Fields that still need values (the LLM must fill these):")
    for lbl in field_labels:
        lines.append(f"  - {lbl}")
    lines.append("")
    lines.append(
        "Return a JSON object mapping each field label to its value. "
        "Use existing profile answers as context. "
        "If a field is optional or unclear, set it to null. "
        "Return ONLY the JSON, no other text."
    )
    prompt = "\n".join(lines)
    reply, err = ask_text(prompt, temperature=0.2, max_tokens=1024)
    if err or not reply:
        return {}
    try:
        # Strip markdown fences
        text = reply.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            text = text.rsplit("```", 1)[0].strip()
        data = json.loads(text)
        if not isinstance(data, dict):
            return {}
        return {k: v for k, v in data.items() if v is not None and str(v).strip()}
    except (json.JSONDecodeError, TypeError):
        return {}


def run(jid=None, limit=None, quick=False, max_pages=4, no_submit=False):
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

    if limit:
        jobs = jobs[:limit]

    N = len(jobs)
    print(f"\nAUTO: {N} job(s) to process (stage=tailored)", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)

    results = {"submitted": [], "stopped": [], "skipped": [], "already_applied": []}

    for i, (jid, job) in enumerate(jobs):
        title = job.get("title", "?")
        company = job.get("company", "?")
        print(f"\n[{i+1}/{N}] {jid[:12]} -- {title} @ {company}", file=sys.stderr)
        print(f"{'-'*60}", file=sys.stderr)

        try:
            _process_one(jid, quick, max_pages, no_submit, results)
        except Exception as e:
            print(f"  ERROR: {e}", file=sys.stderr)
            results["skipped"].append((jid, f"exception: {e}"))

        if i < N - 1:
            time.sleep(2)

    _print_summary(results)
    return 0 if not results["skipped"] else 1


def _process_one(jid, quick, max_pages, no_submit, results):
    from apply.detect import run as detect_run
    from apply.navigate import run as navigate_run
    from apply.act.fill import cmd_fill
    from apply.act.check import cmd_check
    from apply.act.submit import cmd_submit
    from apply.common.page_helpers import load_state
    from lib.db import get_conn, find_duplicate, get_job

    def _stage():
        row = get_conn().execute("SELECT stage FROM jobs WHERE id=?", (jid,)).fetchone()
        return row["stage"] if row else ""

    job = get_job(jid)
    if job:
        title = job.get("title", "")
        company = job.get("company", "")
        if title and company:
            dup = find_duplicate(jid, title, company)
            if dup and dup["stage"] == "applied":
                results["already_applied"].append(
                    (jid, f"duplicate of applied {dup['id'][:12]}"))
                print(f"  SKIP -- duplicate of already-applied {dup['id'][:12]}",
                      file=sys.stderr)
                return

    # Step 1: detect
    print("  detect...", file=sys.stderr, end=" ")
    rc = detect_run(jid)
    if rc != 0:
        results["skipped"].append((jid, "detect failed"))
        return

    state = load_state()
    jtype = state.get("type", "")
    print(f"type={jtype}", file=sys.stderr)

    if jtype == "already_applied":
        results["already_applied"].append((jid, "already applied"))
        return

    if jtype in ("unknown", ""):
        results["skipped"].append((jid, "unknown type -- can't classify URL"))
        print("  SKIP -- unknown type", file=sys.stderr)
        return

    # Step 2: navigate (only for external type)
    if jtype == "external":
        print("  navigate...", file=sys.stderr)
        rc = navigate_run(jid)
        if rc != 0:
            results["skipped"].append((jid, "navigate failed"))
            return

    # Step 3: fill
    print("  fill...", file=sys.stderr)
    rc = cmd_fill(jid, answers=None, verify=not quick,
                  max_pages=max_pages, quick=quick)
    if rc != 0:
        if _stage() == "applied":
            results["already_applied"].append((jid, "already applied"))
            return
        from apply.common.page_helpers import load_state
        st = load_state()
        if st.get("status") == "no_apply_path":
            from lib.db import advance_job
            advance_job(jid, "tailored", state="rejected", error="no apply path (expired?)")
            results["skipped"].append((jid, "no apply path (expired)"))
        else:
            results["skipped"].append((jid, "fill failed"))
        return

    if _stage() == "applied":
        results["already_applied"].append((jid, "already applied"))
        return

    # Step 4: check
    print("  check...", file=sys.stderr)
    rc = cmd_check(jid)
    if rc != 0:
        results["stopped"].append((jid, "check failed -- supply answers and retry"))
        print("  STOPPED -- check failed, orchestrator review needed", file=sys.stderr)
        return

    print("  check passed", file=sys.stderr)

    # Step 5: submit
    if no_submit:
        results["stopped"].append((jid, "check passed -- ready to submit"))
        print(f"  READY -- --no-submit, run: python apply.py act --submit {jid}", file=sys.stderr)
        return

    print("  submit...", file=sys.stderr)
    rc = cmd_submit(jid, confirm=True)
    if rc != 0:
        if _stage() == "applied":
            results["submitted"].append((jid, "submitted"))
            return
        from apply.common.page_helpers import load_state
        st = load_state()
        if st.get("status") == "no_apply_path":
            from lib.db import advance_job
            advance_job(jid, "tailored", state="rejected", error="no apply path (expired?)")
            results["skipped"].append((jid, "no apply path (expired)"))
        elif st.get("status") == "validation_error" and st.get("submit_errors"):
            # Retry: LLM fills missing fields from validation errors
            print("  VALIDATION_ERROR — retrying with LLM-supplied answers", file=sys.stderr)
            try:
                from lib.ask_api import ask_text, available as llm_avail
                if not llm_avail():
                    print("  LLM unavailable — cannot retry", file=sys.stderr)
                    results["skipped"].append((jid, "validation_error (no LLM for retry)"))
                    return
                errors = st["submit_errors"]
                labels = _extract_error_labels(errors)
                if not labels:
                    print("  No parseable field labels in errors", file=sys.stderr)
                    results["skipped"].append((jid, "validation_error (unparseable labels)"))
                    return
                job_title = job.get("title", "")
                from apply.act.helpers import _load_profile
                from apply.common.resolve import _build_ephemeral
                _prof = _load_profile()
                _answers_ctx = {k: v[0] for k, v in _build_ephemeral(_prof).items()}
                llm_answers = _llm_supply_answers(job_title, labels, _answers_ctx)
                if not llm_answers:
                    print("  LLM returned no answers — giving up", file=sys.stderr)
                    results["skipped"].append((jid, "validation_error (LLM no answers)"))
                    return
                print(f"  LLM supplied {len(llm_answers)} answer(s) — re-filling", file=sys.stderr)
                rc = cmd_fill(jid, answers=llm_answers, verify=False, max_pages=4, quick=True)
                if rc != 0:
                    print("  Re-fill failed after LLM answers", file=sys.stderr)
                    results["skipped"].append((jid, "re-fill failed after LLM retry"))
                    return
                rc = cmd_check(jid)
                if rc != 0:
                    print("  Re-check failed after LLM answers", file=sys.stderr)
                    results["stopped"].append((jid, "check failed after LLM retry"))
                    return
                print("  Re-check passed — re-submitting", file=sys.stderr)
                rc = cmd_submit(jid, confirm=True)
                if rc == 0 and _stage() == "applied":
                    results["submitted"].append((jid, "submitted (LLM retry)"))
                    return
                results["skipped"].append((jid, "re-submit failed after LLM retry"))
            except Exception as e:
                print(f"  LLM RETRY ERROR: {e}", file=sys.stderr)
                results["skipped"].append((jid, f"LLM retry exception: {e}"))
        else:
            results["skipped"].append((jid, "submit failed"))
        return

    if _stage() == "applied":
        results["submitted"].append((jid, "submitted"))
    else:
        results["stopped"].append((jid, "submit returned 0 but stage not applied"))


def _print_summary(results):
    print(f"\n{'='*60}", file=sys.stderr)
    n_sub = len(results["submitted"])
    n_stop = len(results["stopped"])
    n_skip = len(results["skipped"])
    n_already = len(results["already_applied"])
    print(f"SUMMARY: {n_sub} submitted, {n_stop} stopped (review), "
          f"{n_skip} skipped, {n_already} already applied", file=sys.stderr)

    if results["submitted"]:
        print("\n  SUBMITTED:", file=sys.stderr)
        for jid, detail in results["submitted"]:
            print(f"    {jid[:12]} -- {detail}", file=sys.stderr)

    if results["stopped"]:
        print("\n  STOPPED (orchestrator review needed):", file=sys.stderr)
        for jid, detail in results["stopped"]:
            print(f"    {jid[:12]} -- {detail}", file=sys.stderr)
        print("\n  To resolve: review fill output above, supply --answers, then:", file=sys.stderr)
        print("    python apply.py act --fill <jid> --answers '{...}'", file=sys.stderr)
        print("    python apply.py act --submit <jid>", file=sys.stderr)

    if results["skipped"]:
        print("\n  SKIPPED:", file=sys.stderr)
        for jid, detail in results["skipped"]:
            print(f"    {jid[:12]} -- {detail}", file=sys.stderr)

    if results["already_applied"]:
        print("\n  ALREADY APPLIED:", file=sys.stderr)
        for jid, detail in results["already_applied"]:
            print(f"    {jid[:12]} -- {detail}", file=sys.stderr)
