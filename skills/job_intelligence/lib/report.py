"""lib/report.py — the evidence surface: one command per question.

The grouped surface (see `report.py help`):
  DECISIONS:  handovers [USER|ORCHESTRATOR|DATA|REVIEW]
  EVIDENCE:   handoff / audit / diff / observe / session / inspect <jid>
  FLEET:      shadow [--classify] / fleet / widgets / candidates
  RULES:      rules list | add "<regex>" "<answer_key>" | clear
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
  python3 report.py rules list|add|promote|clear      Runtime alias rules (wired loop)
  python3 report.py keywords list|add                 Runtime classifier keywords
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
    load_snapshot, get_job, search_jobs, job_count_by_stage,
    event_list,
    desc_get, app_list, app_get, contact_list,
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



def cmd_pending(limit=None, stage="extracted"):
    """List the extraction queue — every active job at a gate stage with
    jid, title/company when known, category, and URL. The queue triage
    view for admit/reject decisions."""
    conn = get_conn()
    print("STAGES:", end="")
    for r in conn.execute("SELECT stage, COUNT(*) n FROM jobs GROUP BY stage ORDER BY n DESC").fetchall():
        print(f"  {r['stage']}={r['n']}", end="")
    print()
    q = ("SELECT id, title, company, category, url, source FROM jobs "
         "WHERE stage=? AND state='active' ORDER BY source, id")
    rows = conn.execute(q, (stage,)).fetchall()
    if limit:
        rows = rows[:limit]
    if not rows:
        print(f"\nPENDING ({stage}): none — queue clear")
        return
    print(f"\nPENDING ({stage}) — {len(rows)}:")
    for r in rows:
        cat = r["category"] or "-"
        title = _clean(r["title"] or "?")[:38]
        company = _clean(r["company"] or "")[:22]
        url = (r["url"] or "")[:52]
        print(f"  {r['id'][:14]} [{cat:7}] {company:22} {title:38} {url}")


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
    s = load_snapshot()
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


def _handover_kw():
    """Static + runtime handover keywords (data-driven classifier learning)."""
    try:
        from apply.common import terms as _T
        return list(_HANDOVER_KW) + _T.list_classifier_keywords("handover")
    except Exception:
        return list(_HANDOVER_KW)

_profile_cache = {}
_ephemeral_cache = {}


def _best_answer_key(label, words=None):
    """The profile answer key whose normalized words overlap the label's
    content words most (D2) — the rule's real target, or None."""
    try:
        from apply.act.helpers import _load_profile
        p = _load_profile()
        a = p.get("answers") or {}
        if words is None:
            words = [w for w in re.split(r"[^a-z0-9]+", (label or "").lower())
                     if len(w) > 2][:4]
        wset = set(words)
        best, best_n = None, 0
        for k in a:
            kw = set(re.split(r"[^a-z0-9]+", k.lower()))
            ov = len(wset & kw)
            if ov > best_n:
                best, best_n = k, ov
        return best if best_n >= 2 else None
    except Exception:
        return None


def _same_value_key(label, value):
    """C4: the profile answer key whose VALUE equals `value` — a novel label
    answered with an existing profile value is the SAME question under new
    phrasing. Returns the key (grow the profile, not just the label store)."""
    try:
        from apply.act.helpers import _load_profile
        p = _load_profile()
        a = p.get("answers") or {}
        vl = str(value or "").lower()
        for k, v in a.items():
            if k.lower() == (label or "").lower():
                continue
            if str(v).lower() == vl:
                return k
    except Exception:
        pass
    return None
    try:
        from apply.act.helpers import _load_profile
        p = _load_profile()
        a = p.get("answers") or {}
        if words is None:
            words = [w for w in re.split(r"[^a-z0-9]+", (label or "").lower())
                     if len(w) > 2][:4]
        wset = set(words)
        best, best_n = None, 0
        for k in a:
            kw = set(re.split(r"[^a-z0-9]+", k.lower()))
            ov = len(wset & kw)
            if ov > best_n:
                best, best_n = k, ov
        return best if best_n >= 2 else None
    except Exception:
        return None


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
                    if any(kw in (f.get("label") or "").lower() for kw in _handover_kw())]
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

    # A1: the submit validation errors, surfaced in tandem with the fill
    # fields — the orchestrator answers them via --answers (evidence combine).
    try:
        from apply.common.page_helpers import load_state as _ls
        _st = _ls(jid)
        _errs = _st.get("submit_errors") or []
        if _errs:
            print("\n  SUBMIT ERRORS (validation — answer via --answers):")
            for e in _errs[:6]:
                print(f"    ! {e[:110]}")
            print("    answer: apply act --fill <jid> --answers "
                  "'{\"<label>\": \"<value>\"}' then re-submit")
    except Exception:
        pass


def cmd_session(run_id=None):
    """Render the event timeline of a run (latest by default) — the
    machine-readable observation log as a human/LLM timeline."""
    from lib.automation.obs import load as obs_load
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


def cmd_profile(harmonize=False, suspects=False):
    """Validate profile.json + the tailored resume data — the upstream
    data-quality guide for the orchestrator. `--harmonize` also lists
    duplicate answer keys (C3); `--suspects` lists profile answers flagged
    WRONG by adjudication (#3) — the orchestrator corrects these in
    profile.json (the root fix for silent profile-poisoning)."""
    from .config import PROFILE_PATH
    from .quality import (validate_profile, validate_resume, GUIDE,
                          harmonize_answers, alias_harmonized_answers)
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
    if harmonize:
        groups = harmonize_answers(p)
        if groups:
            print(f"\n  HARMONIZE ({len(groups)} duplicate answer group(s)) — "
                  f"C3:", file=sys.stderr)
            for g in groups:
                print(f"    {g['meaning'][:30]:30} value={g['value'][:20]!r}")
                print(f"      keys: {', '.join(g['keys'])}")
                print(f"      canonical: {g['canonical']}  (others are aliases "
                      f"— already resolved via alias_harmonized_answers)")
        else:
            print("\n  HARMONIZE: no duplicate answer keys", file=sys.stderr)
    # C2: profile-level contradictions (always shown — cheap, deterministic).
    from .quality import check_profile_contradictions
    cons = check_profile_contradictions(p)
    if cons:
        print(f"\n  CONTRADICTIONS ({len(cons)}) — review:", file=sys.stderr)
        for c in cons:
            print(f"    ! {c}", file=sys.stderr)
    # C5: EEO preference clustering.
    from .quality import eeo_cluster
    eeo = eeo_cluster(p)
    if eeo:
        print(f"\n  EEO CLUSTER ({len(eeo['keys'])} keys all '{eeo['preference']}'):",
              file=sys.stderr)
        print(f"    keys: {', '.join(eeo['keys'])}", file=sys.stderr)
        print("    proposal: one preference 'always prefer-not-to-answer on "
              "EEO' covers all — orchestrator consolidate", file=sys.stderr)
    # #3: profile answers flagged WRONG by adjudication — the root poison
    # source. The orchestrator corrects these in profile.json.
    if suspects:
        import os as _os
        from .config import STATE_DIR
        _sp = _os.path.join(STATE_DIR, "profile_suspects.json")
        try:
            with open(_sp, encoding="utf-8") as f:
                _sus = json.load(f)
        except Exception:
            _sus = {}
        if _sus:
            print(f"\n  PROFILE SUSPECTS ({len(_sus)}) — adjudicated WRONG "
                  f"(#3), correct in profile.json:", file=sys.stderr)
            for label, s in list(_sus.items())[:15]:
                print(f"    ! {label[:60]}  = {s.get('answer','')[:40]!r} "
                      f"({s.get('ts','')[:10]})", file=sys.stderr)
        else:
            print("\n  PROFILE SUSPECTS: none — no profile answer has been "
                  "adjudicated wrong", file=sys.stderr)
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

    # G3: fleet health score — composite that catches slow rot before the SPC
    # bound trips. Components: verified ratio (trustworthy observations),
    # fill rate, and (1 − wrong-fill rate) from adjudication.
    if n_fields:
        verified = kinds.get(_T.VERIFIED, 0)
        verified_ratio = verified / n_fields if n_fields else 0
        fill_rate = filled / n_fields if n_fields else 0
        wrong = 0
        try:
            from lib.db.fills import wrongfill_stats
            wf = wrongfill_stats()
            o = wf["overall"]
            wrong = o.get("rate", 0) if o.get("rate") is not None else 0
        except Exception:
            wrong = 0
        health = verified_ratio * fill_rate * (1 - wrong)
        print(f"  HEALTH SCORE (G3): {health:.2f} "
              f"(verified_ratio={verified_ratio:.2f} x "
              f"fill_rate={fill_rate:.2f} x (1-wrong={1-wrong:.2f}))",
              file=sys.stderr)
        if health < 0.4:
            print("    WARNING: health below 0.4 — declining; run "
                  "report.py wrongfill for root-cause clusters", file=sys.stderr)
        elif health < 0.6:
            print("    CAUTION: health below 0.6 — monitor", file=sys.stderr)
    print()

    print("== STEERING MEMO (top failing labels) ==")
    for lbl, n in fail_labels.most_common(12):
        print(f"  {n:3d}x  {lbl}")
    print("  -> repeated labels are resolver/rule candidates, not noise")
    print()

    # Rule-suggestion pipeline: for each top failing label, is it
    # resolvable with the current profile? If NOT, emit a ready-to-add
    # alias-rule candidate (content-word pattern). D2: find the REAL profile
    # answer key whose normalized words overlap the label, and emit the exact
    # `rules add` command — not a placeholder the orchestrator must fill in.
    print("== RULE CANDIDATES (promote at runtime — no code edit) ==")
    _suggested = 0
    _foreign = []
    for lbl, n in fail_labels.most_common(12):
        if _profile_has_answer(lbl):
            continue  # profile answers it — the failure is widget-level
        # D5: a repeated OOD label with non-ASCII letters is a foreign
        # vocabulary gap (the _FR_EN layer doesn't cover it) — surface as a
        # translation candidate, not a rules-add.
        if re.search(r"[^\x00-\x7f]", lbl or ""):
            _foreign.append((lbl, n))
            continue
        _words = [w for w in re.split(r"[^a-z0-9]+", (lbl or "").lower())
                  if len(w) > 2][:4]
        if len(_words) < 2:
            continue
        _pat = r"\b" + r"\b.*\b".join(re.escape(w) for w in _words) + r"\b"
        # D2: find the profile answer key that shares content words with the
        # label — the rule's real target, not a placeholder.
        _key = _best_answer_key(lbl, _words)
        _suggested += 1
        print(f"  {n:3d}x  {lbl[:60]}")
        print(f"      pattern: {_pat[:90]}")
        if _key:
            print(f"      answer key: {_key}  (matches label words)")
            print(f"      PROMOTE: report.py rules add \"{_pat[:70]}\" \"{_key}\""
                  if _suggested <= 3 else "")
        else:
            print(f"      profile keys: resolve() returned nothing — check "
                  f"profile.json answers")
            print(f"      PROMOTE: report.py rules add \"{_pat[:70]}\" \"<answer_key>\""
                  if _suggested <= 3 else "")
    if _foreign:
        print("\n  FOREIGN-VOCAB GAPS (D5) — non-ASCII labels, translate for "
              "_FR_EN:", file=sys.stderr)
        for lbl, n in _foreign[:5]:
            print(f"    {n:3d}x  {lbl[:60]}", file=sys.stderr)
    if not _suggested:
        print("  (none — every top failure is either widget-level or "
              "already answerable)")
    print()
    print("  actions: report.py handoff <jid> | report.py shadow --classify "
          "| apply.py preflight | report.py rules list")


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
            if any(kw in lbl.lower() for kw in _handover_kw()):
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


def cmd_rules(action, *args):
    """Runtime alias rules — the wired loop for report.py fleet's rule
    candidates. Add a rule at runtime with NO code edit:

      report.py rules list
      report.py rules add "<regex>" "<answer_key>" [more keys...]
      report.py rules promote "<label>"    S2-gated: promote a learned mapping
                                           (≥2 confirms) to a runtime rule
      report.py rules clear

    A rule maps a repeated label pattern to profile answer keys, so the
    next run resolves the label without the orchestrator answering again.
    """
    from apply.common.resolve import (add_alias_rule, list_alias_rules,
                                      clear_alias_rules, promote_learned_to_rule)
    if action == "list":
        rules = list_alias_rules()
        if not rules:
            print("RULES: no runtime alias rules — add one from "
                  "report.py fleet's RULE CANDIDATES", file=sys.stderr)
            return
        print(f"RULES: {len(rules)} runtime alias rule(s)", file=sys.stderr)
        for pat, keys in rules:
            print(f"  {pat[:70]}  ->  {', '.join(keys)}", file=sys.stderr)
    elif action == "add":
        if len(args) < 2:
            print("Usage: report.py rules add --domain <host> "
                  "\"<regex>\" \"<answer_key>\" [more keys...]", file=sys.stderr)
            return 1
        # #5: a runtime rule must be DOMAIN-scoped (a global rule added by
        # one misjudgment poisons every job until the TTL). --domain is
        # required; --confirm acknowledges the scope.
        _domain = ""
        _confirm = "--confirm" in args
        if "--domain" in args:
            i = args.index("--domain")
            if i + 1 < len(args):
                _domain = args[i + 1]
        _rest = [a for a in args
                 if a not in ("--domain", _domain, "--confirm")]
        if not _domain:
            print("RULES: --domain <host> is REQUIRED — a global runtime "
                  "rule poisons every job (S2 discipline)", file=sys.stderr)
            return 1
        if not _confirm:
            print("RULES: pass --confirm to acknowledge this rule is "
                  f"scoped to '{_domain}' only", file=sys.stderr)
            return 1
        if len(_rest) < 2:
            print("Usage: report.py rules add --domain <host> --confirm "
                  "\"<regex>\" \"<answer_key>\"", file=sys.stderr)
            return 1
        pat, keys = _rest[0], list(_rest[1:])
        if add_alias_rule(pat, keys, domain=_domain):
            print(f"RULES: added {pat[:60]} -> {', '.join(keys)} "
                  f"(domain={_domain})", file=sys.stderr)
            # C4: generalization hint — if the rule's key value equals an
            # existing profile answer key, the label IS that question under
            # new phrasing; adding the rule to the PROFILE alias would grow
            # the profile, not just the label store.
            try:
                from apply.act.helpers import _load_profile
                _p = _load_profile()
                _a = _p.get("answers") or {}
                _val = str(_a.get(keys[0], ""))
                _twin = _same_value_key(keys[0], _val)
                if _twin and _twin != keys[0]:
                    print(f"RULES: note — {keys[0]} shares a value with "
                          f"profile key '{_twin}' (C4): consider aliasing in "
                          f"the profile so BOTH phrasings resolve",
                          file=sys.stderr)
            except Exception:
                pass
        else:
            print(f"RULES: refused (invalid regex or no keys): {pat[:60]}",
                  file=sys.stderr)
            return 1
    elif action == "promote":
        if not args:
            print("Usage: report.py rules promote \"<label>\" [--force]",
                  file=sys.stderr)
            return 1
        label = args[0]
        force = "--force" in args
        from apply.common.resolve import promote_learned_to_rule
        status, detail = promote_learned_to_rule(label, force=force)
        print(f"RULES: promote {label[:50]!r} -> {status} ({detail})",
              file=sys.stderr)
        if status != "promoted":
            return 1
    elif action == "clear":
        clear_alias_rules()
        print("RULES: cleared all runtime alias rules", file=sys.stderr)
    else:
        print("Usage: report.py rules list|add|promote|clear", file=sys.stderr)
        return 1
    return 0


def cmd_keywords(action, kind="", keyword=""):
    """Runtime classifier keywords — extend the risk/handover keyword lists
    with NO code edit (data-driven classifier learning):

      report.py keywords list [risk|handover]
      report.py keywords add risk "<keyword>"     (or handover)
    """
    from apply.common.terms import (add_classifier_keyword,
                                    list_classifier_keywords)
    if action == "list":
        if kind in ("risk", "handover"):
            kws = list_classifier_keywords(kind)
            print(f"KEYWORDS: {len(kws)} runtime {kind} keyword(s)",
                  file=sys.stderr)
            for k in kws:
                print(f"  {k}", file=sys.stderr)
            return 0
        data = list_classifier_keywords()
        total = sum(len(v) for v in data.values())
        print(f"KEYWORDS: {total} runtime classifier keyword(s)", file=sys.stderr)
        for kind_, kws in data.items():
            print(f"  {kind_}: {', '.join(kws[:10])}", file=sys.stderr)
        return 0
    if action == "add":
        if kind not in ("risk", "handover") or not keyword:
            print("Usage: report.py keywords add risk|handover \"<keyword>\"",
                  file=sys.stderr)
            return 1
        if add_classifier_keyword(kind, keyword):
            print(f"KEYWORDS: added {kind}: {keyword}", file=sys.stderr)
            return 0
        print("KEYWORDS: refused (invalid kind or keyword)", file=sys.stderr)
        return 1
    print("Usage: report.py keywords list|add", file=sys.stderr)
    return 1


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
    print("    pending [--stage]  gate-stage queue (extract triage)", file=sys.stderr)
    print(file=sys.stderr)
    print("  READINESS:", file=sys.stderr)
    print("    profile          profile/resume completeness", file=sys.stderr)
    print("    glossary         the vocabulary (generated from terms.py)", file=sys.stderr)
    print(file=sys.stderr)
    print("  GENERAL: stats | summary | search | export | events | archive", file=sys.stderr)
    print(file=sys.stderr)
    print("  LEGACY (replaced by reach.py — kept for compat): none — removed", file=sys.stderr)


def cmd_adjudicate(limit=20, platform=None):
    """The correctness queue: fills that most deserve a verdict.

    `kind` says whether the value landed; only a verdict says whether it
    was RIGHT. Reading `answer` against `selected_text` is the whole job —
    where they differ, the form reinterpreted us."""
    from lib.db.fills import sample_for_adjudication
    rows = sample_for_adjudication(limit=limit, platform=platform)
    if not rows:
        print("ADJUDICATE: nothing pending — run a fill to populate the "
              "ledger, or every recorded fill already has a verdict.",
              file=sys.stderr)
        return
    print(f"ADJUDICATE: {len(rows)} fill(s), riskiest first", file=sys.stderr)
    print(f"  intended -> read back; differences are where the form "
          f"reinterpreted the answer\n", file=sys.stderr)
    for r in rows:
        flag = "!" if (r["selected_text"] and
                       r["selected_text"].lower() != r["answer"].lower()) else " "
        print(f"  [{r['id']:>5}]{flag} {r['platform'][:18]:18s} "
              f"{r['label'][:44]:44s} {r['kind']}", file=sys.stderr)
        print(f"          intended : {r['answer'][:70]}", file=sys.stderr)
        if r["selected_text"]:
            print(f"          read back: {r['selected_text'][:70]}", file=sys.stderr)
    print(f"\n  NEXT: report.py adjudicate <id> correct|wrong|unanswerable [note]",
          file=sys.stderr)


def cmd_adjudicate_set(fill_id, verdict, note=""):
    from lib.db.fills import adjudicate
    try:
        ok = adjudicate(fill_id, verdict, note)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    if not ok:
        print(f"ERROR: no fill with id {fill_id}", file=sys.stderr)
        sys.exit(1)
    print(f"ADJUDICATED: {fill_id} -> {verdict}", file=sys.stderr)


def cmd_wrongfill(platform=None):
    """Wrong-fill rate — ETHOS §10's falsification instrument.

    Reports the rate ONLY over adjudicated fills, and always shows how
    many are still unjudged: a rate computed from three verdicts is not
    evidence, and presenting it without its denominator would be exactly
    the overclaiming this instrument exists to catch.
    Also runs the B2 SPC tripwire: a platform over the wrong-fill bound is
    auto-paused (autonomous submits suppressed) until a human reviews."""
    from lib.db.fills import wrongfill_stats, spc_trip, unpause_platform
    s = wrongfill_stats(platform=platform)
    o = s["overall"]
    if not o["n"]:
        print("WRONGFILL: no adjudicated fills yet — the rate is UNKNOWN, "
              "not zero.", file=sys.stderr)
        print(f"  {o['pending']} fill(s) recorded and awaiting a verdict.",
              file=sys.stderr)
        print("  NEXT: report.py adjudicate", file=sys.stderr)
        return
    print(f"WRONGFILL: {o['wrong']}/{o['n']} = {o['rate']:.1%} "
          f"({o['pending']} still unjudged)", file=sys.stderr)
    if o["n"] < 30:
        print(f"  CAUTION: n={o['n']} is too small to steer by.",
              file=sys.stderr)
    print("\n  by platform:", file=sys.stderr)
    for b in s["by_platform"]:
        r = f"{b['rate']:.1%}" if b["rate"] is not None else "-"
        print(f"    {b['key'][:28]:28s} {b['wrong']:>3}/{b['n']:<4} {r}",
              file=sys.stderr)
    worst = [b for b in s["by_label"] if b["wrong"]]
    if worst:
        print("\n  worst field classes:", file=sys.stderr)
        for b in worst[:8]:
            print(f"    {b['key'][:40]:40s} {b['wrong']:>3}/{b['n']:<4} "
                  f"{b['rate']:.1%}", file=sys.stderr)
    # B2 tripwire
    tripped = spc_trip(apply=True)
    if tripped:
        print("\n  SPC TRIP (wrong-fill bound exceeded, submits paused):",
              file=sys.stderr)
        for plat in tripped:
            print(f"    {plat}  —  report.py wrongfill --platform {plat} "
                  f"then report.py spc unpause {plat}", file=sys.stderr)
    # D6 correction root-cause clusters
    try:
        from lib.db.fills import correction_clusters
        clusters = correction_clusters()
        if clusters:
            print("\n  ROOT CAUSE CLUSTERS (D6) — where the wrong answers "
                  "come from:", file=sys.stderr)
            for cl in clusters[:8]:
                print(f"    {cl['wrong']}x wrong  {cl['label'][:40]:40} "
                      f"@{cl['platform'][:18]:18} {cl['method'][:14]:14}",
                      file=sys.stderr)
                print(f"      -> {cl['root_cause']}: {cl['fix']}",
                      file=sys.stderr)
    except Exception:
        pass


def cmd_spc(action="check", platform=""):
    """Wrong-fill SPC tripwire (B2): evaluate the control chart, pause
    tripped platforms, and let a human unpause after review.

      report.py spc           check + apply pauses (report)
      report.py spc check     same, no policy write
      report.py spc unpause <platform>
    """
    from lib.db.fills import spc_trip, unpause_platform
    if action == "unpause":
        if not platform:
            print("Usage: report.py spc unpause <platform>", file=sys.stderr)
            return 1
        if unpause_platform(platform):
            print(f"SPC: unpaused {platform}", file=sys.stderr)
            return 0
        print(f"SPC: could not unpause {platform}", file=sys.stderr)
        return 1
    tripped = spc_trip(apply=(action != "check"))
    if not tripped:
        print("SPC: no platform over the wrong-fill bound", file=sys.stderr)
        return 0
    print(f"SPC: {len(tripped)} platform(s) over the wrong-fill bound:",
          file=sys.stderr)
    for plat in tripped:
        print(f"  {plat}  —  report.py spc unpause {plat}", file=sys.stderr)
    return 0


def cmd_ingest(file_path=None):
    """C1 — profile ingestion SURFACE (LLM_GAPS.md). Point the orchestrator at
    a resume/export file so IT drafts profile.json (the orchestrator is the
    strong model). For text, the orchestrator reads the file; for image/PDF,
    vision reads the screenshot. Nothing is auto-written.

      report.py ingest <resume.txt|resume.png>
    """
    from lib.ingest import ingest_surface, ingest_vision
    from lib.config import PROFILE_PATH
    if not file_path:
        print("Usage: report.py ingest <resume.txt|resume.png>", file=sys.stderr)
        return 1
    existing = {}
    try:
        with open(PROFILE_PATH, encoding="utf-8") as f:
            existing = json.load(f)
    except Exception:
        pass
    surf, ok, detail = ingest_surface(file_path, existing=existing)
    if not ok:
        print(f"INGEST: {detail}", file=sys.stderr)
        return 1
    print(f"INGEST: {detail}", file=sys.stderr)
    print(f"  file: {surf['file']}  ({surf['kind']}, {surf['size']} bytes)")
    if surf["kind"] == "image":
        # Vision-only path: an image the orchestrator cannot read as text.
        draft, dok, ddetail = ingest_vision(file_path)
        if not dok:
            print(f"INGEST: {ddetail}", file=sys.stderr)
            print("  NEXT: orchestrator opens the image and drafts profile.json",
                  file=sys.stderr)
            return 1
        import io as _io
        buf = _io.StringIO()
        json.dump(draft, buf, indent=2, ensure_ascii=False)
        print(buf.getvalue())
        print("NEXT: review the vision draft, merge into profile.json manually",
              file=sys.stderr)
        return 0
    # Text: surface a preview + the next action for the orchestrator.
    if surf.get("summary"):
        print("  preview:")
        for ln in surf["summary"].splitlines()[:12]:
            print(f"    {ln[:90]}")
    print("  NEXT: orchestrator reads the file and drafts profile.json — "
          "ingest never auto-writes", file=sys.stderr)
    return 0


def cmd_domains(action="list", host=None):
    """F2 — new-domain approval gate. A live submit to a domain with no prior
    successful submission is blocked until it is approved here.

      report.py domains                     list approved
      report.py domains approve <host>      approve (explicit sign-off)
      report.py domains deny <host>         revoke
    """
    from apply.common.domain_gate import list_approved, approve, deny
    if action == "approve":
        if not host:
            print("Usage: report.py domains approve <host>", file=sys.stderr)
            return 1
        if approve(host):
            print(f"DOMAINS: approved {host}", file=sys.stderr)
            return 0
        return 1
    if action == "deny":
        deny(host or "")
        print(f"DOMAINS: revoked {host or ''}", file=sys.stderr)
        return 0
    approved = list_approved()
    if not approved:
        print("DOMAINS: none approved — live submit is gated per-domain "
              "(approve after review)", file=sys.stderr)
        return 0
    print(f"DOMAINS: {len(approved)} approved:", file=sys.stderr)
    for h in approved:
        print(f"  {h}", file=sys.stderr)
    return 0


def _applied_confirm_path():
    import os
    from .config import STATE_DIR
    return os.path.join(STATE_DIR, "applied_confirmations.json")


def _applied_confirmed():
    import json, os
    try:
        with open(_applied_confirm_path(), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def cmd_applied(unconfirmed_only=False, suspects_only=False):
    """G2 — post-submit confirmation surface. Applied jobs that still lack a
    post-submit confirmation (the submit was marked applied from signals but
    never verified against the portal/email). The orchestrator re-checks and
    confirms, or flags the submission as in-doubt.

      report.py applied                list all applied
      report.py applied --unconfirmed  list applied needing confirmation
      report.py applied --suspects     list applied with NO applied_at
                                        (the unrecoverable wrong-applied class)
    """
    import json, os
    from .config import STATE_DIR
    from lib.db import get_conn
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, title, company, applied_at FROM jobs "
        "WHERE stage='applied' ORDER BY applied_at DESC").fetchall()
    if not rows:
        print("APPLIED: none", file=sys.stderr)
        return 0
    confirmed = _applied_confirmed()
    # --suspects: applied with no applied_at — marked applied via a signal that
    # never set the timestamp. This is the unrecoverable wrong-applied class:
    # the job may not actually be applied, and there is no trace to trust.
    if suspects_only:
        suspects = [r for r in rows if not r["applied_at"]]
        if not suspects:
            print("APPLIED: no suspect applied rows (all have applied_at)",
                  file=sys.stderr)
            return 0
        print(f"APPLIED SUSPECTS: {len(suspects)} applied with NO applied_at — "
              f"verify the portal before trusting:", file=sys.stderr)
        for r in suspects:
            print(f"  {r['id'][:12]} {r['title'][:40] if r['title'] else ''} "
                  f"@ {r['company'][:20] if r['company'] else ''}", file=sys.stderr)
        print("  NEXT: open the posting, confirm applied or ji undo <jid>",
              file=sys.stderr)
        return 0
    unconfirmed = [r for r in rows if r["id"] not in confirmed]
    unconfirmed = [r for r in rows if r["id"] not in confirmed]
    if unconfirmed_only:
        if not unconfirmed:
            print("APPLIED: all applied jobs confirmed", file=sys.stderr)
            return 0
        print(f"APPLIED: {len(unconfirmed)} unconfirmed submission(s) — "
              f"re-check the portal/email (G2):", file=sys.stderr)
        for r in unconfirmed[:15]:
            print(f"  {r['id'][:12]} "
                  f"{r['applied_at'][:10] if r['applied_at'] else ''} "
                  f"{r['company'][:22] if r['company'] else '':22} "
                  f"{r['title'][:40] if r['title'] else ''}", file=sys.stderr)
        print("  NEXT: ji verify-applied <jid> after checking the portal, "
              "or mark in-doubt", file=sys.stderr)
        return 0
    print(f"APPLIED: {len(rows)} total ({len(unconfirmed)} unconfirmed):",
          file=sys.stderr)
    for r in rows[:20]:
        mark = " ?" if r["id"] in [u["id"] for u in unconfirmed] else ""
        print(f"  {r['id'][:12]} "
              f"{r['applied_at'][:10] if r['applied_at'] else ''} "
              f"{r['company'][:22] if r['company'] else '':22} "
              f"{r['title'][:40] if r['title'] else ''}{mark}", file=sys.stderr)
    return 0


def cmd_fleet_scan():
    """Scan every dossier for wrong-value patterns that silently slip past
    per-field review — URNs (LinkedIn location typeahead), 'undefined'/'null',
    or a verified field whose read-back is an opaque id. This is the systemic
    catch for the URN/location miss: wrong values must be found automatically,
    not by a human happening to read the right dossier."""
    import glob as _glob, os
    from .config import RESULTS_DIR
    bad = []
    for hf in sorted(_glob.glob(os.path.join(RESULTS_DIR, "*", "handoff.json"))):
        jid = os.path.basename(os.path.dirname(hf))
        try:
            d = json.load(open(hf, encoding="utf-8"))
        except Exception:
            continue
        for f in d.get("fields", []):
            a = str(f.get("answer", ""))
            sel = str(f.get("selected_text", ""))
            blob = (a + " " + sel).lower()
            if "urn:" in blob or "undefined" in blob or "null" in blob \
                    or "geo:" in blob:
                bad.append((jid, f.get("label"), f.get("kind"), a[:40], sel[:20]))
    if not bad:
        print("FLEET_SCAN: no wrong-value patterns (URN/undefined/null) in any "
              "dossier", file=sys.stderr)
        return 0
    print(f"FLEET_SCAN: {len(bad)} suspicious value(s):", file=sys.stderr)
    for b in bad[:20]:
        print(f"  {b[0][:12]} {b[2][:14]:14} {b[1][:30]:30} "
              f"={b[3]!r} readback={b[4]!r}", file=sys.stderr)
    print("  NEXT: ji verify <jid> --all  then ji answer to correct",
          file=sys.stderr)
    return 0


def cmd_applied_confirm_batch():
    """G2 for the whole unconfirmed LinkedIn backlog in ONE tracker visit.
    Reads the Job Tracker 'Applied' list once, then matches every applied
    LinkedIn job that is not yet confirmed. External-ATS jobs stay in the
    unconfirmed surface (they need the email/portal + --manual)."""
    from lib.db import get_conn
    from lib import g2
    rows = get_conn().execute(
        "SELECT id, url, title, company FROM jobs WHERE stage='applied'").fetchall()
    targets = []
    for r in rows:
        if g2.is_confirmed(r["id"]):
            continue
        host = ""
        try:
            from urllib.parse import urlparse as _up
            host = (_up(r["url"] or "").netloc or "").lower().split(":")[0]
        except Exception:
            pass
        if host and "linkedin.com" in host:
            targets.append(r)
    if not targets:
        print("APPLIED: no unconfirmed LinkedIn applied jobs to check",
              file=sys.stderr)
        return 0

    print(f"APPLIED: checking {len(targets)} unconfirmed LinkedIn jobs "
          f"against the tracker (one visit)...", file=sys.stderr)
    hay, detail = g2.tracker_applied_entries()
    if not hay:
        print(f"APPLIED: tracker unavailable — {detail}", file=sys.stderr)
        return 1

    confirmed = 0
    for r in targets:
        title = (r["title"] or "").strip()
        company = (r["company"] or "").strip()
        tlb = title.lower()
        clb = company.lower()
        hit = False
        if tlb and tlb in hay:
            hit = True
            why = f"found '{title[:40]}'"
        elif clb and clb in hay:
            hit = True
            why = f"found company '{company[:30]}'"
        if hit:
            if g2.record_confirmed(r["id"]):
                confirmed += 1
                print(f"  G2: confirmed {r['id'][:12]} — {why}",
                      file=sys.stderr)
        else:
            print(f"  G2: NOT FOUND {r['id'][:12]} — '{title[:40]}'",
                  file=sys.stderr)
    print(f"APPLIED: batch done — {confirmed}/{len(targets)} confirmed",
          file=sys.stderr)
    return 0


def cmd_applied_confirm(jid, manual=False):
    """G2 — record that a submission was confirmed against the portal/email.
    Accepts a full 16-hex jid or an unambiguous prefix.

    For LinkedIn jobs this RUNS the independent tracker check first and only
    records the confirmation when the posting is actually found there. An
    unconfirmed submit must not be certified by flipping a flag — that is
    exactly the blind-submit class G2 exists to stop. `--manual` records the
    confirmation without the tracker check (for external ATS where the
    operator verified via email/portal)."""
    import json, os
    from lib.config import atomic_write_json
    from lib.db import get_conn
    from lib import g2
    full = jid
    if len(jid) < 16:
        rows = get_conn().execute(
            "SELECT id FROM jobs WHERE id LIKE ? AND stage='applied'",
            (f"{jid}%",)).fetchall()
        if len(rows) == 1:
            full = rows[0]["id"]
        elif len(rows) != 1:
            print(f"APPLIED: {jid} is not an unambiguous applied-job prefix "
                  f"({len(rows)} matches)", file=sys.stderr)
            return 1
    try:
        row = get_conn().execute(
            "SELECT url, title, company FROM jobs WHERE id=?", (full,)).fetchone()
        url = row["url"] if row else ""
        title = row["title"] if row else ""
        company = row["company"] if row else ""
    except Exception:
        url = title = company = ""

    host = ""
    try:
        from urllib.parse import urlparse as _up
        host = (_up(url or "").netloc or "").lower().split(":")[0]
    except Exception:
        pass

    if host and "linkedin.com" in host:
        if manual:
            # Operator verified via email/portal explicitly — record it.
            if g2.record_confirmed(full):
                print(f"APPLIED: confirmed {full} (manual)", file=sys.stderr)
                return 0
            print(f"APPLIED: could not confirm {jid}", file=sys.stderr)
            return 1
        ok, detail = g2.linkedin_tracker_confirm(
            full, url=url, title=title, company=company)
        if not ok:
            print(f"APPLIED: G2 NOT CONFIRMED — {detail}", file=sys.stderr)
            print(f"  Do NOT mark this submission confirmed on the tracker "
                  f"check alone.", file=sys.stderr)
            print(f"  Re-run the tracker check, or confirm via email/portal "
                  f"and use --manual.", file=sys.stderr)
            return 1
        print(f"APPLIED: G2 confirmed — {detail}", file=sys.stderr)
        if g2.record_confirmed(full):
            print(f"APPLIED: confirmed {full}", file=sys.stderr)
            return 0
        print(f"APPLIED: could not confirm {jid}", file=sys.stderr)
        return 1

    # Non-LinkedIn (external ATS: Oracle, Workday, Greenhouse...): G2 is the
    # confirmation email/portal. Require --manual — a silent auto-confirm here
    # would be the blind-submit class again (no independent check ran).
    if not manual:
        print(f"APPLIED: {jid} is a non-LinkedIn submission — G2 confirmation "
              f"must come from the email/portal, not a flag.", file=sys.stderr)
        print(f"  Check the confirmation email or the ATS application status, "
              f"then re-run with --manual.", file=sys.stderr)
        return 1

    if g2.record_confirmed(full):
        print(f"APPLIED: confirmed {full} (manual)", file=sys.stderr)
        return 0
    print(f"APPLIED: could not confirm {jid}", file=sys.stderr)
    return 1


def cmd_widget_draft(artifact_path=None):
    """D1 — widget-handler DRAFT SURFACE (LLM_GAPS.md). Surface a captured
    probe-failure artifact so the ORCHESTRATOR reads the DOM snapshot and
    drafts the handler + registry entry + test. No ask_api for the DOM — the
    orchestrator reads the artifact file on demand.

      report.py widget-draft [<artifact.json>]
    """
    from lib.ingest import draft_widget_surface
    surf, ok, detail = draft_widget_surface(artifact_path)
    if not ok:
        print(f"WIDGET_DRAFT: {detail}", file=sys.stderr)
        return 1
    print(f"WIDGET_DRAFT: {detail}", file=sys.stderr)
    print(f"  artifact: {surf['artifact']}")
    if surf.get("capability"):
        print(f"  capability: {surf['capability']}")
    if surf.get("url"):
        print(f"  url: {surf['url']}")
    print(f"  NEXT: {surf['next']}", file=sys.stderr)
    return 0


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
        cmd_profile(harmonize="--harmonize" in args,
                    suspects="--suspects" in args)
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
    elif cmd == "pending":
        stage = "extracted"
        lim = None
        if "--stage" in args:
            i = args.index("--stage")
            if i + 1 < len(args):
                stage = args[i + 1]
        if "--limit" in args:
            i = args.index("--limit")
            if i + 1 < len(args):
                lim = int(args[i + 1])
        cmd_pending(limit=lim, stage=stage)
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
    elif cmd == "rules":
        action = args[0] if args else "list"
        rc = cmd_rules(action, *args[1:])
        sys.exit(rc or 0)
    elif cmd == "keywords":
        action = args[0] if args else "list"
        kind = args[1] if len(args) > 1 else ""
        keyword = " ".join(args[2:]) if len(args) > 2 else ""
        rc = cmd_keywords(action, kind, keyword)
        sys.exit(rc or 0)
    elif cmd == "archive":
        cmd_archive()
    elif cmd == "wrongfill":
        plat = None
        if "--platform" in args:
            i = args.index("--platform")
            if i + 1 < len(args):
                plat = args[i + 1]
        cmd_wrongfill(platform=plat)
    elif cmd == "spc":
        action = args[0] if args else "check"
        plat = args[1] if len(args) > 1 else ""
        rc = cmd_spc(action, plat)
        sys.exit(rc or 0)
    elif cmd == "ingest":
        rc = cmd_ingest(args[0] if args else None)
        sys.exit(rc or 0)
    elif cmd == "widget-draft":
        rc = cmd_widget_draft(args[0] if args else None)
        sys.exit(rc or 0)
    elif cmd == "domains":
        action = args[0] if args else "list"
        rc = cmd_domains(action, args[1] if len(args) > 1 else "")
        sys.exit(rc or 0)
    elif cmd == "applied":
        rc = cmd_applied(unconfirmed_only="--unconfirmed" in args,
                         suspects_only="--suspects" in args)
        sys.exit(rc or 0)
    elif cmd == "fleet-scan":
        rc = cmd_fleet_scan()
        sys.exit(rc or 0)
    elif cmd == "applied-confirm":
        if not args:
            print("Usage: report.py applied-confirm <jid> [--manual] | --all",
                  file=sys.stderr)
            sys.exit(1)
        if args[0] == "--all":
            rc = cmd_applied_confirm_batch()
            sys.exit(rc or 0)
        manual = "--manual" in args
        rc = cmd_applied_confirm(args[0], manual=manual)
        sys.exit(rc or 0)
    elif cmd == "adjudicate":
        # adjudicate                       -> show the sample
        # adjudicate <id> correct|wrong|unanswerable ["note"]
        if args and args[0].isdigit():
            if len(args) < 2:
                print("Usage: report.py adjudicate <id> correct|wrong|unanswerable [note]",
                      file=sys.stderr)
                sys.exit(1)
            cmd_adjudicate_set(int(args[0]), args[1],
                               " ".join(args[2:]) if len(args) > 2 else "")
        else:
            lim = 20
            if "--limit" in args:
                i = args.index("--limit")
                if i + 1 < len(args):
                    lim = int(args[i + 1])
            plat = None
            if "--platform" in args:
                i = args.index("--platform")
                if i + 1 < len(args):
                    plat = args[i + 1]
            cmd_adjudicate(limit=lim, platform=plat)
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
