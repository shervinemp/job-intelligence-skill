"""lib/report.py — the evidence surface: one command per question.

The grouped surface (see `report.py help`):
  DECISIONS:  handovers [USER|ORCHESTRATOR|DATA|REVIEW]
  EVIDENCE:   handoff / audit / diff / observe / session / inspect <jid>
  FLEET:      shadow [--classify] / fleet / widgets / candidates
  READINESS:  profile / glossary
  GENERAL:    stats / summary / search / export / events / archive

Usage:
  python3 report.py stats                     Pipeline statistics
  python3 report.py candidates [--limit N]    Tailored jobs ready to apply (with guard flags)
  python3 report.py inspect <jid>             Full job details
  python3 report.py search <query>            Search jobs
  python3 report.py export json [--stage S]   Export jobs as JSON
  python3 report.py export csv [--stage S]    Export jobs as CSV
  python3 report.py summary [--days N]        Recent activity digest
  python3 report.py events [--upcoming]       List events
  python3 report.py archive                   Archive state/registry entries for reset jobs
"""

import csv
from apply.common import terms as _T
import io
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta

from .db import (
    get_conn,
    load_state, get_job, search_jobs, job_count_by_stage,
    event_list,
    desc_get, app_list, app_get,
)
from .config import STATE_PATH, REGISTRY_PATH, RESULTS_DIR, atomic_write_json


def cmd_stats():
    from lib.db import get_conn
    conn = get_conn()
    rows = conn.execute(
        "SELECT stage, state, COUNT(*) as cnt FROM jobs GROUP BY stage, state ORDER BY stage"
    ).fetchall()
    stages = ["extracted", "described", "tailored", "applied"]
    states = ["active", "rejected", "failed"]
    matrix = {st: {st2: 0 for st2 in states} for st in stages}
    for r in rows:
        if r["stage"] in matrix and r["state"] in states:
            matrix[r["stage"]][r["state"]] = r["cnt"]

    print(f"Total jobs: {conn.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]}")
    print()
    print(f"{'Stage/State':16s}", end="")
    for st in states:
        print(f"{st:>10s}", end="")
    print(f"{'Total':>8s}")
    print("-" * 50)
    total_by_stage = 0
    for stage in stages:
        row = matrix[stage]
        row_total = sum(row.values())
        print(f"{stage:16s}", end="")
        for st in states:
            print(f"{row[st]:>10d}", end="")
        print(f"{row_total:>8d}")
        total_by_stage += row_total
    print("-" * 50)
    print(f"{'Total':16s}", end="")
    grand = 0
    for st in states:
        c = sum(matrix[s][st] for s in stages)
        print(f"{c:>10d}", end="")
        grand += c
    print(f"{grand:>8d}")
    print()
    by_stage = job_count_by_stage()
    described = by_stage.get("described", 0)
    tailored = by_stage.get("tailored", 0)
    print(f"Need tailoring: {described}")
    print(f"Ready to apply: {tailored}")

    # Contact/outreach summary
    total_contacts = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
    total_outreach = conn.execute("SELECT COUNT(*) FROM contacts WHERE reached_out=1").fetchone()[0]
    pending_email = conn.execute("SELECT COUNT(*) FROM contacts WHERE email_sent=0 AND email != '' AND email IS NOT NULL").fetchone()[0]
    pending_dm = conn.execute("SELECT COUNT(*) FROM contacts WHERE message_sent=0 AND linkedin_url != '' AND linkedin_url IS NOT NULL").fetchone()[0]
    if total_contacts:
        print()
        print(f"Contacts: {total_contacts}")
        print(f"  Reached out: {total_outreach}")
        print(f"  Pending email: {pending_email}")
        print(f"  Pending DM:    {pending_dm}")
        print(f"  NEXT: reach.py discover <jid>  OR  reach.py email <jid> --contact N")


def cmd_candidates(limit=None):
    """List tailored jobs ready to apply, surfacing guard-state flags
    (submit_clicked, no_apply_path, login_required) so the operator can
    decide what to run safely. Also shows the already-applied set for
    dedup reference."""
    from apply.detect import _classify
    from apply.common.registry import resolve as resolve_registry

    conn = get_conn()

    # Stage counts header
    print("STAGES:", end="")
    for r in conn.execute("SELECT stage, COUNT(*) n FROM jobs GROUP BY stage ORDER BY n DESC").fetchall():
        print(f"  {r['stage']}={r['n']}", end="")
    print()

    # Classify every tailored job for the summary breakdown
    all_tailored = conn.execute(
        "SELECT id, url, external_url FROM jobs WHERE stage='tailored' AND state='active'"
    ).fetchall()
    types = {}  # "easy_apply" -> count, "ats_direct(Workday)" -> count, etc.
    for r in all_tailored:
        url = r["url"] or ""
        ext_url = r["external_url"] or ""
        jtype, resolved_url = _classify(url, ext_url)
        if jtype == "ats_direct":
            reg = resolve_registry(resolved_url or url)
            label = f"ats_direct({reg.name})" if reg else "ats_direct(?)"
        elif jtype == "external":
            reg = resolve_registry(ext_url or resolved_url or url)
            label = f"external({reg.name})" if reg else "external(?)"
        else:
            label = jtype
        types[label] = types.get(label, 0) + 1
    type_summary = ", ".join(f"{k}={v}" for k, v in sorted(types.items()))
    print(f"\nTAILORED: {type_summary}")

    # Tailored with external URLs — candidates to apply
    q = ("SELECT id, title, company, url, external_url, state FROM jobs "
         "WHERE stage='tailored' AND external_url IS NOT NULL AND external_url != '' "
         "ORDER BY company")
    rows = conn.execute(q).fetchall()
    if limit:
        rows = rows[:limit]
    print(f"\nEXTERNAL ({len(rows)} — require Playwright pipeline):")
    for r in rows:
        state = r["state"] or ""
        flags = []
        if "submit_clicked" in state:
            flags.append("SUBMIT_CLICKED")
        if "no_apply_path" in state:
            flags.append("NO_APPLY_PATH")
        if "login_required" in state:
            flags.append("LOGIN_REQ")
        flag_str = "  [" + ", ".join(flags) + "]" if flags else ""
        ext_url = r["external_url"] or ""
        reg = resolve_registry(ext_url)
        tag = f" ({reg.name})" if reg else ""
        url_display = ext_url[:48]
        print(f"  {r['id'][:14]} {_clean(r['company'] or '')[:20]:20} "
              f"{_clean(r['title'] or '')[:34]:34} {url_display}{tag}{flag_str}")

    # Tailored without external URL — LinkedIn Easy Apply
    easy = conn.execute(
        "SELECT id, title, company, state FROM jobs "
        "WHERE stage='tailored' AND (external_url IS NULL OR external_url = '') AND state='active' "
        "ORDER BY company"
    ).fetchall()
    if easy:
        print(f"\nEASY_APPLY ({len(easy)} — LinkedIn modal):")
        for r in easy[:20]:
            state = r["state"] or ""
            flags = []
            if "submit_clicked" in state:
                flags.append("SUBMIT_CLICKED")
            if "no_apply_path" in state:
                flags.append("NO_APPLY_PATH")
            flag_str = "  [" + ", ".join(flags) + "]" if flags else ""
            print(f"  {r['id'][:14]} {_clean(r['company'] or '')[:22]:22} "
                  f"{_clean(r['title'] or '')[:40]}{flag_str}")
        if len(easy) > 20:
            print(f"  ... +{len(easy) - 20} more")

    # Already-applied set (dedup reference)
    applied = conn.execute(
        "SELECT title, company FROM jobs WHERE stage='applied' ORDER BY company"
    ).fetchall()
    print(f"\nALREADY APPLIED ({len(applied)}) — dedup reference:")
    for r in applied[:12]:
        print(f"  {_clean(r['company'] or '')[:22]:22} {_clean(r['title'] or '')[:40]}")
    if len(applied) > 12:
        print(f"  ... +{len(applied) - 12} more")



def cmd_inspect(jid):
    job = get_job(jid)
    if not job:
        print(f"Job not found: {jid}", file=sys.stderr)
        return
    print(f"{'-'*60}")
    print(f"  JOB: {_clean(job.get('title', ''))}")
    print(f"  AT:  {_clean(job.get('company', ''))}")
    print(f"{'-'*60}")
    for k in ["id", "email_id", "location", "url", "source_url", "salary",
              "salary_min", "salary_max", "salary_currency", "remote_status",
               "job_type", "department", "source", "stage", "state", "fit_score",
              _T.OUTCOME_ERROR, "created_at", "updated_at", "applied_at"]:
        v = job.get(k)
        if v:
            print(f"  {k:20s} {v}")
    print()
    desc = desc_get(jid)
    if desc:
        print(f"  Description: {len(desc)} chars")
        print(f"  {desc[:300]}...")
        print()
    apps = app_list(jid)
    if apps:
        print(f"  Application files ({len(apps)}):")
        for a in apps:
            content = app_get(jid, a["filename"])
            sz = len(content) if content else 0
            print(f"    {a['filename']:30s} {sz} chars")
    contacts = contact_list(job_id=jid)
    if contacts:
        print(f"\n  Contacts ({len(contacts)}):")
        for c in contacts:
            print(f"    {c['name']:20s} {c['role'] or ''}")
    rname = job.get("recruiter_name", "")
    rurl = job.get("recruiter_url", "")
    if rname:
        print(f"\n  Recruiter: {rname}" + (f"  {rurl}" if rurl else ""))


def cmd_search(query):
    results = search_jobs(query)
    if not results:
        print(f"No jobs matching '{query}'")
        return
    print(f"Found {len(results)} jobs:")
    for j in results:
        stage = j.get("stage", "?")
        state = j.get("state", "?")
        title = _clean(j.get("title", ""))[:50]
        company = _clean(j.get("company", ""))[:30]
        print(f"  [{stage:12s}/{state:10s}] {j['id']} {title} @ {company}")


def cmd_export(fmt, stage=None):
    s = load_state()
    jobs = list(s["jobs"].values())
    if stage:
        jobs = [j for j in jobs if j.get("stage") == stage]
    if fmt == "json":
        print(json.dumps(jobs, indent=2, ensure_ascii=False, default=str))
    elif fmt == "csv":
        out = io.StringIO()
        w = csv.writer(out)
        keys = ["id", "title", "company", "location", "url", "salary",
                "salary_min", "salary_max", "remote_status", "source",
                "stage", "state", "fit_score", "created_at", "applied_at"]
        w.writerow(keys)
        for j in jobs:
            w.writerow([j.get(k, "") for k in keys])
        print(out.getvalue().strip())


def cmd_summary(days=7):
    since = (datetime.now() - timedelta(days=days)).isoformat()
    conn = get_conn()
    new_jobs = conn.execute(
        "SELECT COUNT(*) as c FROM jobs WHERE created_at >= ?", (since,)
    ).fetchone()["c"]
    updated = conn.execute(
        "SELECT COUNT(*) as c FROM jobs WHERE updated_at >= ?", (since,)
    ).fetchone()["c"]
    applied = conn.execute(
        "SELECT COUNT(*) as c FROM jobs WHERE applied_at >= ?", (since,)
    ).fetchone()["c"]
    events = conn.execute(
        "SELECT COUNT(*) as c FROM events WHERE created_at >= ? OR event_at >= ?",
        (since, since),
    ).fetchone()["c"]
    print(f"Summary (last {days} days):")
    print(f"  New jobs:     {new_jobs}")
    print(f"  Updated:      {updated}")
    print(f"  Applied:      {applied}")
    print(f"  Events:       {events}")
    if events:
        print()
        for e in event_list(upcoming=True):
            if e.get("event_at", "") >= since:
                print(f"  [{e.get('event_type')}] {e.get('job_title','')[:40]} @ {e.get('job_company','')[:20]} - {e.get('event_at','')}")


def _clean(s):
    return re.sub(r'[\u200b\u200c\u200d\ufffe\ufeff]', '', s).strip()


def cmd_events(upcoming=False):
    events = event_list(upcoming=upcoming)
    if not events:
        print("No events" if not upcoming else "No upcoming events")
        return
    for e in events:
        status = "[x]" if e.get("completed") else "[ ]"
        job_info = f" [{e.get('job_title','')} @ {e.get('job_company','')}]" if "job_title" in e else ""
        print(f"  {status} [{e.get('event_type')}] {e.get('title','')}{job_info}")
        if e.get("event_at"):
            print(f"     at {e['event_at']}")
        if e.get("description"):
            print(f"     {e['description'][:100]}")


def cmd_archive():
    """Move state/registry entries for reset jobs to archive files (preserves history)."""
    conn = get_conn()
    existing = {r["id"] for r in conn.execute("SELECT id FROM jobs").fetchall()}
    total = 0
    for path, label in [(STATE_PATH, "apply_state.json"), (REGISTRY_PATH, "page_registry.json")]:
        try:
            with open(path) as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        stale = {jid: data[jid] for jid in data if jid not in existing}
        if not stale:
            continue
        archive_path = path.replace(".json", "_archive.json")
        archive = {}
        try:
            with open(archive_path) as f:
                archive = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        archive.update(stale)
        atomic_write_json(archive_path, archive)
        for jid in stale:
            del data[jid]
        atomic_write_json(path, data)
        print(f"  {label}: archived {len(stale)} entries ({len(data)} remain)", file=sys.stderr)
        total += len(stale)
    if total:
        print(f"Archived {total} stale entries.", file=sys.stderr)
    else:
        print("No stale entries.", file=sys.stderr)


def cmd_shadow():
    """Aggregate the shadow-run log (apply.py shadow) into an actionable
    review: outcome counts, per-job lines with check errors, links to the
    saved inspect artifacts, and OUTDATED markers where a log entry is
    contradicted by a newer dossier."""
    import os
    from .config import JI_HOME
    from .automation.diff import stale_vs_dossier, load_handoffs
    log = os.path.join(JI_HOME, "state", "shadow_run.jsonl")
    if not os.path.exists(log):
        print("No shadow log yet — run 'apply.py shadow' first.", file=sys.stderr)
        return
    recs = []
    for line in open(log, encoding="utf-8"):
        try:
            recs.append(json.loads(line))
        except Exception:
            continue
    if not recs:
        print("Shadow log is empty.", file=sys.stderr)
        return

    from collections import Counter
    outcomes = Counter(r.get("outcome", "?") for r in recs)
    print(f"SHADOW RUNS: {len(recs)} job record(s)")
    print(f"  {', '.join(f'{k}={v}' for k, v in outcomes.most_common())}")
    # Escape-hatch instrumentation: what the run leaned on (or couldn't).
    try:
        _llm = Counter()
        import glob as _glob
        for hf in _glob.glob(os.path.join(JI_HOME, "results", "*", "handoff.json")):
            try:
                st = json.load(open(hf, encoding="utf-8")).get("llm_status")
                if st:
                    _llm[st] += 1
            except Exception:
                continue
        if _llm:
            print(f"  LLM_STATUS: {', '.join(f'{k}={v}' for k, v in _llm.most_common())}")
            if _llm.get(_T.LLM_API_DOWN):
                print("  NOTE: ask_api was DOWN for some runs — escape-hatch "
                      "fields are unassisted; orchestrator review required.")
    except Exception:
        pass
    print()

    order = [_T.OUTCOME_HELD_SHADOW, _T.OUTCOME_ALREADY_APPLIED, _T.OUTCOME_SKIPPED, _T.OUTCOME_STOPPED, _T.OUTCOME_EXCEPTION, _T.OUTCOME_SUBMITTED]
    shots = os.path.join(JI_HOME, "screenshots")
    for kind in order:
        group = [r for r in recs if r.get("outcome") == kind]
        if not group:
            continue
        label = {_T.OUTCOME_HELD_SHADOW: "READY TO SUBMIT (fill+check OK)",
                 _T.OUTCOME_ALREADY_APPLIED: "ALREADY APPLIED",
                 _T.OUTCOME_SKIPPED: "SKIPPED (login/captcha/expired)",
                 _T.OUTCOME_STOPPED: "NEEDS REVIEW",
                 _T.OUTCOME_EXCEPTION: "EXCEPTION",
                 _T.OUTCOME_SUBMITTED: "SUBMITTED (unexpected in shadow)"}[kind]
        print(f"== {label} ({len(group)}) " + "=" * 40)
        for r in sorted(group, key=lambda x: x.get("ts", "")):
            jid = r.get("jid", "?")
            detail = r.get("detail", "")[:90]
            stale = ""
            try:
                hs = load_handoffs(jid, RESULTS_DIR)
                if hs and stale_vs_dossier(r, hs[0]):
                    stale = "  *** OUTDATED (newer dossier contradicts this) ***"
            except Exception:
                pass
            line = f"  {jid[:12]} {r.get('title', '?')[:38]:38s} {r.get('secs', '?')}s {detail}"
            print(line + stale)
            for e in (r.get("check_errors") or [])[:4]:
                print(f"      ! {e.get('label', '?')[:50]} — {e.get('reason', '')[:60]}")
            art = os.path.join(shots, f"inspect_inspect_{jid}.jpg")
            if os.path.exists(art):
                print(f"      inspect: {art}")
        print()

    # Actionable next steps
    print("NEXT:")
    stopped = [r for r in recs if r.get("outcome") == _T.OUTCOME_STOPPED]
    held = outcomes.get(_T.OUTCOME_HELD_SHADOW, 0)
    if held:
        print(f"  - {held} job(s) passed fill+check — run live submits when ready")
    if stopped:
        print(f"  - {len(stopped)} job(s) need review (check errors above; fix with "
              f"apply act --fill <jid> --answers '{{...}}' then re-run shadow)")
    print("  - Re-run: python apply.py shadow  (skips already-recorded jobs)")


# Handover keywords — questions whose answer is a personal decision, not
# a data gap or a pipeline bug. These belong to the USER; everything else
# that fails belongs to the orchestrator (data/answers) or the code.
_HANDOVER_KW = (
    "sponsor", "ethnic", "race", "veteran", "disabilit", "gender", "pronoun",
    "referral", "describe", "essay", "preferred name", "hear about",
    "salary expectation", "compensation expectation", "legally eligible",
    "authorized to work", "work authorization", "visa", "eligib",
    "relocat", "commute", "military", "sexual orientation", "citizenship",
)

_profile_cache = {}
_ephemeral_cache = {}


def _profile_has_answer(label):
    """Does the canonical profile resolve an answer for this label?
    Shared by the owner-split and the handovers surface — the ground
    truth that keeps personal questions out of the data bucket."""
    try:
        from apply.common import resolve as _resolve_mod
        from apply.act.helpers import _load_profile
        if not _profile_cache:
            try:
                _profile_cache["p"] = _load_profile()
            except Exception:
                _profile_cache["p"] = {}
        if not _ephemeral_cache:
            try:
                _ephemeral_cache["e"] = _resolve_mod._build_ephemeral(
                    _profile_cache["p"])
            except Exception:
                _ephemeral_cache["e"] = {}
        r = _resolve_mod.resolve(label or "", _profile_cache["p"],
                                 ephemeral=_ephemeral_cache["e"])
        return r.value is not None
    except Exception:
        return False


def cmd_shadow_classify():
    """Orchestrator verification view: outcome counts, crash/timeout
    evidence, and the STOPPED owner-split (code / data / handover) so the
    orchestrator knows exactly what to fix, what to answer, and what to
    ask the user."""
    import os
    from collections import Counter
    from .config import JI_HOME
    log = os.path.join(JI_HOME, "state", "shadow_run.jsonl")
    if not os.path.exists(log):
        print("No shadow log yet — run 'apply.py shadow' first.", file=sys.stderr)
        return
    recs = []
    for line in open(log, encoding="utf-8"):
        try:
            recs.append(json.loads(line))
        except Exception:
            continue

    outcomes = Counter(r.get("outcome", "?") for r in recs)
    print(f"FLEET SHADOW: {len(recs)} job record(s)")
    print("  " + ", ".join(f"{k}={v}" for k, v in outcomes.most_common()))
    print()

    order = [_T.OUTCOME_HELD_SHADOW, _T.OUTCOME_ALREADY_APPLIED, _T.OUTCOME_SKIPPED, _T.OUTCOME_STOPPED,
             _T.OUTCOME_CRASH, _T.OUTCOME_TIMEOUT, _T.OUTCOME_ERROR, _T.OUTCOME_EXCEPTION, _T.OUTCOME_SUBMITTED]
    labels = {_T.OUTCOME_HELD_SHADOW: "READY TO SUBMIT", _T.OUTCOME_ALREADY_APPLIED: "ALREADY APPLIED",
              _T.OUTCOME_SKIPPED: "SKIPPED", _T.OUTCOME_STOPPED: "NEEDS REVIEW", _T.OUTCOME_CRASH: "CRASH",
              _T.OUTCOME_TIMEOUT: "TIMEOUT", _T.OUTCOME_ERROR: "ERROR", _T.OUTCOME_EXCEPTION: "EXCEPTION",
              _T.OUTCOME_SUBMITTED: "SUBMITTED (unexpected)"}

    for kind in order:
        group = [r for r in recs if r.get("outcome") == kind]
        if not group:
            continue
        print(f"== {labels.get(kind, kind)} ({len(group)}) " + "=" * 30)
        for r in sorted(group, key=lambda x: x.get("ts", ""))[:15]:
            flagged = ""
            if kind == _T.OUTCOME_HELD_SHADOW and r.get("regressed"):
                flagged = "  *** QUARANTINED (regression vs previous run) ***"
                continue  # ready-gate: regressed jobs are NOT ready
            print(f"  {r.get('jid', '?')[:12]} {r.get('company', '?')[:20]:20s} "
                  f"{r.get('detail', '?')[:70]}{flagged}")
            if r.get("after_crash"):
                print("      (recovered after a first-attempt crash)")
            if r.get("transcript"):
                print(f"      transcript: {r.get('transcript')}")
            if r.get("tail"):
                print(f"      tail: {' | '.join(r['tail'].splitlines()[-2:])[:150]}")
        _quar = [r for r in group if r.get("regressed")]
        if _quar:
            print(f"  QUARANTINED (not ready): "
                  + ", ".join(r.get("jid", "?")[:12] for r in _quar))
        print()

    stopped = [r for r in recs if r.get("outcome") == _T.OUTCOME_STOPPED]

    # Unconfirmed-skip clustering: a platform that is mostly UNCONFIRMED
    # is a platform-level hypothesis (cookie/session issue), not a pile
    # of expired postings — the follow-up is a platform probe, not per-job
    # labor.
    _unconf = [r for r in recs if r.get("unconfirmed")]
    if _unconf:
        from collections import Counter as _Cnt
        by_company = _Cnt((r.get("company") or "?")[:25] for r in _unconf)
        print(f"== UNCONFIRMED SKIPS ({len(_unconf)}) — live queue, not "
              f"closed postings ==")
        for comp, n in by_company.most_common(8):
            print(f"  {comp:26s} {n}")
        print("  re-run: python apply.py shadow --recheck   "
              "(re-examines this queue)")
        print()

    if not stopped:
        return
    # Owner-split ground truth: does the PROFILE answer the question?
    # A personal keyword is only a handover when no answer exists — a
    # relocation question the profile already answers is DATA/code, not
    # a user decision.
    def _has_answer(label):
        return _profile_has_answer(label)

    print("== OWNER SPLIT (stopped jobs) ==")
    owners = {"code": [], "data": [], "handover": [], "unknown": []}
    for r in stopped:
        jid = r.get("jid", "")
        fields = []
        try:
            import json as _json
            h = os.path.join(RESULTS_DIR, str(jid), "handoff.json")
            if os.path.exists(h):
                fields = _json.load(open(h, encoding="utf-8")).get("fields") or []
        except Exception:
            pass
        bad = [f for f in fields
               if f.get("kind") in (_T.REJECTED_BY_FORM, _T.INTERACTION_FAILED)
               or (f.get("kind") == _T.NEEDS_DATA and f.get("required"))]
        if not bad:
            owners["unknown"].append((r, []))
            continue
        code = [f for f in bad
                if f.get("kind") in (_T.REJECTED_BY_FORM, _T.INTERACTION_FAILED)]
        unresolved = [f for f in bad if not _has_answer(f.get("label") or "")]
        handover = [f for f in unresolved
                    if any(kw in (f.get("label") or "").lower() for kw in _HANDOVER_KW)]
        data = [f for f in unresolved if f not in handover]
        # Priority: handover first — user decisions GATE the job; code
        # failures coexist with them, so surface both, but the bucket is
        # the user's (the orchestrator acts on code/data only after).
        if handover:
            rec_extra = ([f for f in code + data if f not in handover]
                         if (code or data) else [])
            owners["handover"].append((r, handover + rec_extra))
        elif code:
            owners["code"].append((r, code))
        elif data:
            owners["data"].append((r, data))
        else:
            owners["unknown"].append((r, bad))

    for owner, label in (("handover", "USER DECIDES (personal questions)"),
                         ("data", "ORCHESTRATOR: supply answers (--answers)"),
                         ("code", "PIPELINE BUG: fix code + test"),
                         ("unknown", "UNCLASSIFIED — orchestrator reads dossiers")):
        if not owners[owner]:
            continue
        print(f"--- {label} ({len(owners[owner])})")
        for r, fl in owners[owner][:8]:
            print(f"  {r.get('jid', '?')[:12]} {r.get('company', '?')[:22]:22s} "
                  f"| {', '.join((f.get('label') or '?')[:38] for f in fl[:3])}")
        print()
    print("  full decision list with evidence: python3 report.py handovers",
          file=sys.stderr)


def cmd_audit(jid):
    """Show the per-field fill attempt log for a job — including WHAT was
    attempted, the selector, the filler method, and the before/after DOM
    values when the ATS wiped or rejected the value."""
    import os
    path = os.path.join(RESULTS_DIR, str(jid), "apply_audit.jsonl")
    if not os.path.exists(path):
        print(f"No audit log for {jid} (no fill attempts recorded).", file=sys.stderr)
        return
    n = 0
    for line in open(path, encoding="utf-8"):
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if rec.get("kind") != "field":
            continue
        n += 1
        state = "OK " if rec.get("filled") else "FAIL"
        reason = rec.get("reason") or ""
        method = rec.get("method") or ""
        if reason and method:
            why = f" [{reason} via {method}]"
        elif reason:
            why = f" [{reason}]"
        else:
            why = ""
        print(f"{state} {rec.get('ts', '')[:19]} {rec.get('label', '?')[:44]:44s} "
              f"{rec.get('value', '')[:36]:36s}{why}")
        sel = rec.get("selector") or ""
        if sel:
            print(f"     sel={sel[:90]}")
        before, after = rec.get("before", ""), rec.get("after", "")
        if before or after:
            print(f"     before={before[:60]!r} after={after[:60]!r}")
    if not n:
        print(f"No field records in {path}", file=sys.stderr)


def cmd_handoff(jid):
    """Render the orchestrator handoff dossier for a job (fill outcomes,
    blockers, suggested decisions, artifact links)."""
    import os
    path = os.path.join(RESULTS_DIR, str(jid), "handoff.json")
    if not os.path.exists(path):
        print(f"No handoff for {jid} (no fill run yet).", file=sys.stderr)
        return
    with open(path, encoding="utf-8") as f:
        h = json.load(f)
    s = h.get("summary", {})
    print(f"HANDOFF {jid}  {h.get('ts', '')}  mode={h.get('mode', '?')}")
    print(f"  filled={s.get('filled', 0)} failed={s.get('failed', 0)} "
          f"skipped={s.get('skipped_optional', 0)}  error={h.get('error', '') or '-'}")
    print()
    for fld in h.get("fields", []):
        out = fld.get("outcome", "?")
        lbl = fld.get("label", "?")[:42]
        ans = fld.get("answer", "")[:30]
        reason = fld.get("reason", "")
        method = fld.get("method", "")
        why = f"[{method}:{reason}]" if method and reason else (f"[{reason}]" if reason else "")
        print(f"  {out:9s} {lbl:42s} {ans:30s} {why}")
        d = fld.get("diag") or {}
        if d.get("options_seen") is not None or d.get("top_options"):
            top = ", ".join(f"{t.get('text', '')[:28]}({t.get('score')})"
                            for t in d.get("top_options") or [])
            print(f"            options={d.get('options_seen')} top={top}")
        if d.get("typeahead_without_menu"):
            print("            typeahead: menu appears only after typing")
    for b in h.get("blockers", []):
        print(f"\n  BLOCKER {b.get('type')} -> {b.get('next', b.get('needs', ''))}")
    print("\n  DECISIONS:")
    for d in h.get("decisions", []):
        cmd = d.get("command", "")
        print(f"    - {d.get('action')}: {cmd}")
        if d.get("for"):
            print(f"      for: {', '.join(str(x)[:60] for x in d['for'][:5])}")


def cmd_session(run_id=None):
    """Render the event timeline of a run (latest by default) — the
    machine-readable observation log as a human/LLM timeline."""
    from apply.common.obs import load as obs_load
    events = obs_load(run_id)
    if not events:
        print("No session events found.", file=sys.stderr)
        return
    print(f"SESSION {events[0].get('run_id', '?')}  ({len(events)} events)")
    for ev in events:
        actor = ev.get("actor", "?")
        action = ev.get("action", "")
        jid = ev.get("jid", "")
        target = (ev.get("target") or "")[:40]
        outcome = ev.get("outcome") or ""
        detail = (ev.get("detail") or "")[:70]
        line = f"  {ev.get('ts', '')[:19]} {actor:10s} {action:10s} {jid[:8]:8s} {target:40s}"
        if outcome:
            line += f" {outcome}"
        if detail:
            line += f" {detail}"
        print(line)


def _load_handoffs(jid):
    """Timestamped handoff history for a job, newest first (lib/automation)."""
    from .automation.diff import load_handoffs
    return load_handoffs(jid, RESULTS_DIR)


def compare_handoffs(new, old):
    """Field-level comparison of two dossiers (lib/automation)."""
    from .automation.diff import compare_handoffs as _cmp
    return _cmp(new, old)


def cmd_diff(jid):
    """Field-level diff between the two most recent fill runs — the
    regression detector (e.g., 'Country was filled, now fails')."""
    hs = _load_handoffs(jid)
    if len(hs) < 2:
        print(f"Need ≥2 fill runs for {jid} to diff (have {len(hs)}).", file=sys.stderr)
        return
    new, old = hs[0], hs[1]
    d = compare_handoffs(new, old)
    print(f"DIFF {jid}: {new.get('ts', '')} vs {old.get('ts', '')}")
    print(f"  filled: {d['filled_now']} (was {d['filled_before']})")
    if d["regressed"]:
        print("  REGRESSED (was filled):")
        for lbl, now in d["regressed"]:
            print(f"    - {lbl[:50]} -> {now}")
    if d["improved"]:
        print(f"  IMPROVED (now filled): {len(d['improved'])}")
        for lbl in d["improved"][:10]:
            print(f"    + {lbl[:50]}")
    if d["still_failed"]:
        print(f"  STILL FAILED: {len(d['still_failed'])}")
        for lbl in d["still_failed"][:10]:
            print(f"    = {lbl[:50]}")
    if not any(d[k] for k in ("regressed", "improved", "still_failed")):
        print("  (no field-level changes)")


def cmd_observe(jid):
    """The LLM observation brief: latest dossier + regression diff +
    suggested decisions, in one view."""
    cmd_handoff(jid)
    print()
    hs = _load_handoffs(jid)
    if len(hs) >= 2:
        cmd_diff(jid)


def cmd_profile():
    """Validate profile.json + the tailored resume data — the upstream
    data-quality guide for the orchestrator."""
    from .config import PROFILE_PATH
    from .quality import validate_profile, validate_resume, GUIDE
    try:
        with open(PROFILE_PATH, encoding="utf-8") as f:
            p = json.load(f)
    except Exception as e:
        print(f"Cannot read profile: {e}", file=sys.stderr)
        return
    issues = validate_profile(p)
    print("PROFILE:")
    if issues:
        for i in issues:
            print(f"  ! {i}")
    else:
        print("  complete (contact + work_history + education present)")
    # Check a recent resume.json if any tailored results exist
    import os
    found = 0
    for d in sorted(os.listdir(RESULTS_DIR))[:50]:
        rp = os.path.join(RESULTS_DIR, d, "resume.json")
        if os.path.exists(rp):
            try:
                with open(rp, encoding="utf-8") as f:
                    r = json.load(f)
                ri = validate_resume(r)
                if ri:
                    print(f"  resume.json {d[:8]}:")
                    for x in ri[:3]:
                        print(f"    ! {x}")
                found += 1
            except Exception:
                continue
        if found >= 3:
            break
    print()
    print(GUIDE)


def cmd_fleet():
    """The orchestrator's steering instrument: aggregate every dossier
    into per-platform outcomes, per-field-class success, METHOD
    ATTRIBUTION (deterministic vs escape hatch — the ethos's own proof),
    weekly trends, and a steering memo of the top failing labels."""
    import glob as _glob
    import os
    from collections import Counter
    dossiers = []
    for hf in _glob.glob(os.path.join(RESULTS_DIR, "*", "handoff.json")):
        try:
            d = json.load(open(hf, encoding="utf-8"))
            if d.get("fields"):
                dossiers.append(d)
        except Exception:
            continue
    if not dossiers:
        print("FLEET: no dossiers yet — run 'apply.py shadow' first.",
              file=sys.stderr)
        return

    n_jobs = len(dossiers)
    n_fields = sum(len(d.get("fields", [])) for d in dossiers)
    kinds = Counter()
    methods = Counter()
    by_week = Counter()
    fail_labels = Counter()
    from datetime import datetime
    for d in dossiers:
        try:
            wk = datetime.fromisoformat(d.get("ts", "")).strftime("%Y-%m-%d")
            by_week[wk[:7]] += 1
        except Exception:
            pass
        for f in d.get("fields", []):
            kinds[f.get("kind", "?")] += 1
            if f.get("kind") in (_T.VERIFIED, _T.UNVERIFIED):
                m = (f.get("method") or "deterministic").lower()
                bucket = ("llm" if "llm" in m or "vision" in m
                          else "combobox" if "combobox" in m
                          else "deterministic")
                methods[bucket] += 1
            if f.get("kind") in (_T.REJECTED_BY_FORM, _T.INTERACTION_FAILED,
                                 _T.NEEDS_DATA):
                fail_labels[(f.get("label") or "?")[:60]] += 1

    print(f"FLEET ACCURACY: {n_jobs} dossier(s), {n_fields} field record(s)")
    print(f"  kinds: {', '.join(f'{k}={v}' for k, v in kinds.most_common())}")
    filled = kinds.get(_T.VERIFIED, 0) + kinds.get(_T.UNVERIFIED, 0)
    bad = n_fields - filled
    if n_fields:
        print(f"  filled={filled} ({100 * filled // n_fields}%)  "
              f"failed/unfilled={bad}")
    print(f"  method attribution (filled): "
          + ", ".join(f"{k}={v}" for k, v in methods.most_common()))
    print(f"  trend (dossiers/week): "
          + ", ".join(f"{k}:{v}" for k, v in sorted(by_week.items())[-6:]))
    print()

    print("== STEERING MEMO (top failing labels) ==")
    for lbl, n in fail_labels.most_common(12):
        print(f"  {n:3d}x  {lbl}")
    print("  -> repeated labels are resolver/rule candidates, not noise")
    print()

    # Rule-suggestion pipeline: for each top failing label, is it
    # resolvable with the current profile? If NOT, emit a ready-to-add
    # alias-rule candidate (content-word pattern) — the orchestrator
    # confirms or edits instead of writing from scratch.
    print("== RULE CANDIDATES (confirm or edit, then add to resolve.py) ==")
    _suggested = 0
    for lbl, n in fail_labels.most_common(12):
        if _profile_has_answer(lbl):
            continue  # profile answers it — the failure is widget-level
        _words = [w for w in re.split(r"[^a-z0-9]+", (lbl or "").lower())
                  if len(w) > 2][:4]
        if len(_words) < 2:
            continue
        _pat = r"\b" + r"\b.*\b".join(re.escape(w) for w in _words) + r"\b"
        _suggested += 1
        print(f"  {n:3d}x  {lbl[:60]}")
        print(f"      pattern: {_pat[:90]}")
        print(f"      profile keys: resolve() returned nothing — check "
              f"profile.json answers")
    if not _suggested:
        print("  (none — every top failure is either widget-level or "
              "already answerable)")
    print()
    print("  actions: report.py handoff <jid> | report.py shadow --classify "
          "| apply.py preflight")


def cmd_handovers(owner=None):
    """THE decisions inbox — every open decision across the fleet, in
    one place, grouped by OWNER, with the evidence needed to decide
    WITHOUT opening the dossier, and the exact answer command.

      USER         — personal questions (identity/legal/life) the profile
                     can't answer: the USER decides.
      ORCHESTRATOR — evidence-backed fields: no_option_match with
                     top_options (the answer IS in the evidence), and
                     no_match labels. Per the routing hierarchy this is
                     the ORCHESTRATOR's queue — answer via --answers +
                     re-fill.
      DATA         — profile/answer gaps (work_history, missing answer
                     classes) that block fields fleet-wide.
      REVIEW       — stopped/blocked jobs (login/captcha/2fa/regression):
                     investigate, then decide.

    `report.py handovers <owner>` filters to one group. Answered items
    disappear once the dossier updates — no separate state to maintain.
    """
    import glob as _glob
    import os as _os
    from collections import Counter
    groups = {"USER": [], "ORCHESTRATOR": [], "DATA": [], "REVIEW": []}
    for hf in _glob.glob(_os.path.join(RESULTS_DIR, "*", "handoff.json")):
        jid = _os.path.basename(_os.path.dirname(hf))
        try:
            doc = json.load(open(hf, encoding="utf-8"))
        except Exception:
            continue
        for f in doc.get("fields", []):
            kind = f.get("kind")
            if kind not in (_T.NEEDS_DATA, _T.REJECTED_BY_FORM):
                continue
            lbl = f.get("label") or ""
            diag = f.get("diag") or {}
            if kind == _T.REJECTED_BY_FORM and diag.get("reason") == "no_option_match":
                if _profile_has_answer(lbl):
                    # Evidence-backed AND answerable: the answer exists in
                    # the profile, the widget failed — the ORCHESTRATOR's
                    # queue (answer from top_options evidence + re-fill).
                    groups["ORCHESTRATOR"].append({
                        "jid": jid, "label": lbl,
                        "evidence": (diag.get("top_options") or [])[:3],
                        "answer": None})
                else:
                    # Widget failed but NO profile answer exists — a data
                    # gap, not an orchestrator decision.
                    groups["DATA"].append({"jid": jid, "label": lbl,
                                           "answer": None})
                continue
            if _profile_has_answer(lbl):
                continue  # the profile answers it — not an open decision
            if any(kw in lbl.lower() for kw in _HANDOVER_KW):
                groups["USER"].append({"jid": jid, "label": lbl,
                                       "answer": None})
            else:
                groups["DATA"].append({"jid": jid, "label": lbl,
                                       "answer": None})
        for b in doc.get("blockers") or []:
            groups["REVIEW"].append({"jid": jid,
                                     "label": f"blocker: {b.get('type', '?')}",
                                     "answer": None})

    if owner:
        owner = owner.upper()
        if owner not in groups:
            print(f"HANDOVERS: unknown owner '{owner}' — "
                  f"use USER | ORCHESTRATOR | DATA | REVIEW",
                  file=sys.stderr)
            return 1
        groups = {owner: groups[owner]}

    total = sum(len(v) for v in groups.values())
    if not total:
        print("HANDOVERS: nothing open — no pending decisions.",
              file=sys.stderr)
        return
    print(f"HANDOVERS: {total} open decision(s)", file=sys.stderr)
    for name, items in groups.items():
        if not items:
            continue
        by_q = Counter(i["label"] for i in items)
        print(file=sys.stderr)
        print(f"== {name} ({len(items)}) ==", file=sys.stderr)
        for q, n in by_q.most_common(12):
            jids = [i["jid"] for i in items if i["label"] == q]
            print(f"  [{n}x] {q[:80]}", file=sys.stderr)
            ev = next((i["evidence"] for i in items
                       if i["label"] == q and i.get("evidence")), None)
            if ev:
                opts = ", ".join(f"{o.get('text', '?')[:30]}"
                                 for o in ev if o.get("score", 0) >= 2)
                if opts:
                    print(f"      evidence (top options): {opts}",
                          file=sys.stderr)
            print(f"      jobs: {' '.join(j[:10] for j in jids[:4])}"
                  + (" ..." if len(jids) > 4 else ""), file=sys.stderr)
        print(file=sys.stderr)
    print("  USER/DATA/ORCHESTRATOR answer:", file=sys.stderr)
    print("    apply act --fill <jid> --answers '{\"<label>\": \"<value>\"}'",
          file=sys.stderr)
    print("  REVIEW: python3 report.py handoff <jid>", file=sys.stderr)


def cmd_widgets():
    """The widget backlog: every captured probe failure (registry-failures
    artifacts), grouped by capability profile — the orchestrator's list of
    unhandled OOD widget classes. A cluster is a widget-registry TODO."""
    import glob as _glob
    import os as _os
    from collections import Counter
    from .config import JI_HOME
    fails_dir = _os.path.join(JI_HOME, "registry-failures")
    artifacts = sorted(_glob.glob(_os.path.join(fails_dir, "*.json")))
    if not artifacts:
        print("WIDGETS: no probe-failure artifacts — no known unhandled "
              "widget classes.", file=sys.stderr)
        return
    by_profile = Counter()
    by_cap = Counter()
    urls = {}
    for a in artifacts:
        try:
            d = json.load(open(a, encoding="utf-8"))
            h = d.get("profile_hash", "?")
            by_profile[h] += 1
            caps = d.get("capability_summary", "?")
            by_cap[caps] += 1
            urls.setdefault(h, d.get("url", "?")[:70])
        except Exception:
            continue
    print(f"WIDGETS: {len(artifacts)} probe-failure artifact(s) "
          f"({len(by_profile)} capability profile(s))", file=sys.stderr)
    print("  by capability:", ", ".join(f"{k}={v}" for k, v in
                                         by_cap.most_common(8)))
    print()
    print("  profiles (new widget classes to handle):")
    for h, n in by_profile.most_common(10):
        print(f"    {h[:16]}  {n:3d}x  {urls.get(h, '')[:60]}")
    print("  next: investigate the newest artifact, write a widget handler "
          "+ registry entry + corpus snapshot")


def _cmd_help():
    """The grouped surface map — every question the orchestrator asks has
    one command; the groups are the mental model."""
    print("REPORT SURFACE (one command per question)", file=sys.stderr)
    print(file=sys.stderr)
    print("  DECISIONS (the inbox):", file=sys.stderr)
    print("    handovers [USER|ORCHESTRATOR|DATA|REVIEW]  every open decision, grouped by owner, evidence included", file=sys.stderr)
    print(file=sys.stderr)
    print("  EVIDENCE (per job):", file=sys.stderr)
    print("    handoff <jid>    the dossier (kinds, diag, decisions)", file=sys.stderr)
    print("    audit <jid>      per-field fill attempt log", file=sys.stderr)
    print("    diff <jid>       regression canary (vs previous run)", file=sys.stderr)
    print("    observe <jid>    session event timeline", file=sys.stderr)
    print("    session [run_id] raw events", file=sys.stderr)
    print("    inspect <jid>    job snapshot", file=sys.stderr)
    print(file=sys.stderr)
    print("  FLEET (aggregates):", file=sys.stderr)
    print("    shadow [--classify]  batch outcomes + owner split", file=sys.stderr)
    print("    fleet              accuracy report + steering memo + rule candidates", file=sys.stderr)
    print("    widgets            unhandled widget-class backlog", file=sys.stderr)
    print("    candidates         pending tailored jobs", file=sys.stderr)
    print(file=sys.stderr)
    print("  READINESS:", file=sys.stderr)
    print("    profile          profile/resume completeness", file=sys.stderr)
    print("    glossary         the vocabulary (generated from terms.py)", file=sys.stderr)
    print(file=sys.stderr)
    print("  GENERAL: stats | summary | search | export | events | archive", file=sys.stderr)
    print(file=sys.stderr)
    print("  LEGACY (replaced by reach.py — kept for compat): none — removed", file=sys.stderr)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]
    args = sys.argv[2:]

    if cmd == "stats":
        cmd_stats()
    elif cmd == "candidates":
        lim = None
        if "--limit" in args:
            i = args.index("--limit")
            if i + 1 < len(args):
                lim = int(args[i + 1])
        cmd_candidates(limit=lim)
    elif cmd == "inspect":
        if not args:
            print("Usage: python3 report.py inspect <jid>", file=sys.stderr)
            sys.exit(1)
        cmd_inspect(args[0])
    elif cmd == "search":
        if not args:
            print("Usage: python3 report.py search <query>", file=sys.stderr)
            sys.exit(1)
        cmd_search(" ".join(args))
    elif cmd == "export":
        fmt = args[0] if args else "json"
        stage = None
        if "--stage" in args:
            i = args.index("--stage")
            if i + 1 < len(args):
                stage = args[i + 1]
        cmd_export(fmt, stage)
    elif cmd == "summary":
        days = 7
        if "--days" in args:
            i = args.index("--days")
            if i + 1 < len(args):
                days = int(args[i + 1])
        cmd_summary(days)
    elif cmd == "events":
        cmd_events(upcoming="--upcoming" in args)
    elif cmd == "profile":
        cmd_profile()
    elif cmd == "session":
        cmd_session(args[0] if args else None)
    elif cmd == "diff":
        if not args:
            print("Usage: python3 report.py diff <jid>", file=sys.stderr)
            sys.exit(1)
        cmd_diff(args[0])
    elif cmd == "observe":
        if not args:
            print("Usage: python3 report.py observe <jid>", file=sys.stderr)
            sys.exit(1)
        cmd_observe(args[0])
    elif cmd == "handoff":
        if not args:
            print("Usage: python3 report.py handoff <jid>", file=sys.stderr)
            sys.exit(1)
        cmd_handoff(args[0])
    elif cmd == "audit":
        if not args:
            print("Usage: python3 report.py audit <jid>", file=sys.stderr)
            sys.exit(1)
        cmd_audit(args[0])
    elif cmd == "shadow":
        if "--classify" in args:
            cmd_shadow_classify()
        else:
            cmd_shadow()
    elif cmd == "fleet":
        cmd_fleet()
    elif cmd == "glossary":
        from apply.common.terms import glossary
        for term, meaning, note in glossary():
            print(f"  {term:22s} {meaning}")
            print(f"      {note}")
    elif cmd == "handovers":
        cmd_handovers(args[0] if args else None)
    elif cmd == "help":
        _cmd_help()
    elif cmd == "widgets":
        cmd_widgets()
    elif cmd == "archive":
        cmd_archive()
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
