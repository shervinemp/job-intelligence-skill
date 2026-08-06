#!/usr/bin/env python3
"""ji.py — the orchestrator's single surface (PLAN.md spec).

One command surface with compact output for the orchestrator. Wraps the
existing modules; it never changes apply/submit safety behavior — it only
selects and summarizes what they already do.

Usage:
  python3 ji.py status                      One screen: fleet, READY, HOLD, decisions, one NEXT
  python3 ji.py decisions [--owner X]       THE decisions inbox
  python3 ji.py answer <jid> "<label>": "<value>"   Resolve a decision (compiles to learning)
  python3 ji.py verify <jid>                Risk-field value review (sanctioned PII view)
  python3 ji.py ready [--limit N]           Jobs whose risk fields are all observed
  python3 ji.py job <jid>                   Compact dossier
  python3 ji.py diff <jid> | ji.py audit <jid>   Regression canary / fill log
  python3 ji.py audit <jid>                   Fill attempt log (alias)
  python3 ji.py apply <jid>                 detect+fill+check in one, stops at review gate
  python3 ji.py submit <jid> [--force]      Explicit; policy=live required
  python3 ji.py shadow [--limit N] [--recheck]   Zero-interaction batch
  python3 ji.py fetch [--days N]            stage_emails + extract + enrich
  python3 ji.py tailor [--auto]             Batch tailoring
  python3 ji.py verify-applied <jid>        Mark a submission confirmed (G2)
  python3 ji.py applied [--unconfirmed]     Post-submit confirmation surface
  python3 ji.py help

Superset passthrough — unhandled commands forward to the owning engine, so ji
is the ONE surface the orchestrator memorizes (SURFACE_AUDIT.md). All of these
dispatch through ji and forward to report.py / apply.py:
  python3 ji.py stats
  python3 ji.py fleet
  python3 ji.py fleet-scan
  python3 ji.py candidates
  python3 ji.py pending
  python3 ji.py profile
  python3 ji.py glossary
  python3 ji.py search
  python3 ji.py export
  python3 ji.py events
  python3 ji.py summary
  python3 ji.py session
  python3 ji.py observe
  python3 ji.py inspect
  python3 ji.py handoff
  python3 ji.py handovers
  python3 ji.py adjudicate
  python3 ji.py wrongfill
  python3 ji.py spc
  python3 ji.py archive
  python3 ji.py rules
  python3 ji.py keywords
  python3 ji.py domains
  python3 ji.py ingest
  python3 ji.py widget-draft
  python3 ji.py widgets
  python3 ji.py act
  python3 ji.py detect
  python3 ji.py navigate
  python3 ji.py flag
  python3 ji.py reject
  python3 ji.py retry
  python3 ji.py undo
  python3 ji.py creds
  python3 ji.py registry
  python3 ji.py preflight
  python3 ji.py applied-confirm
  python3 ji.py extract <verb> ...       Stage engines under ji (SURFACE_AUDIT v2):
  python3 ji.py enrich <verb> ...          e.g. ji extract admit --category X <jid>
  python3 ji.py tailor <verb> ...               ji enrich admit/flag/retry/undo <jid>
  python3 ji.py reach <verb> ...               ji tailor admit/review/retry/undo <jid>
  python3 ji.py linkedin [--url] [--count N]   ji reach discover|list|email|message|connect
  python3 ji.py stage_emails [--days N]        ji linkedin --list | ji stage_emails

Return contract: every command ends with exactly one line —
  NEXT: <command> | DECISION: <owner> <count> — <label> | READY: <jid> ... | DONE: <summary>
"""
import os
import subprocess
import sys

SKILL = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL)

_T = None
_ready_cache = None

# Engine pass-through sets (SURFACE_AUDIT.md): ji forwards unhandled commands
# to the owning engine so ji is a true superset — the orchestrator memorizes
# ONE surface.
_REPORT_CMDS = {
    "adjudicate", "applied-confirm", "archive", "candidates", "domains",
    "events", "export", "fleet", "fleet-scan", "glossary", "handoff",
    "handovers", "help", "ingest", "inspect", "keywords", "observe",
    "pending", "profile", "rules", "search", "session", "spc", "stats",
    "summary", "widget-draft", "widgets", "wrongfill",
}
_APPLY_CMDS = {
    "act", "creds", "detect", "flag", "navigate", "preflight", "registry",
    "reject", "retry", "undo",
}
# Pipeline-stage engines namespaced under ji (SURFACE_AUDIT.md v2): the
# orchestrator reaches EVERY stage through ji, and stage-specific verbs that
# collide across engines (flag/undo/retry/reject mean different things in
# apply vs reach vs tailor) are disambiguated by namespace instead of by a
# single shared verb. Raw args are forwarded verbatim.
_STAGE_CMDS = {
    "extract": "extract.py",
    "enrich": "enrich.py",
    "tailor": "tailor.py",
    "reach": "reach.py",
    "linkedin": "linkedin.py",
    "stage_emails": "stage_emails.py",
}


def _imports():
    global _T
    if _T is None:
        from apply.common import terms as t
        _T = t
    return _T


def _run(*args, env_extra=None):
    """Run a pipeline CLI in-process-subprocess and stream its stderr.
    Returns the process return code (0 = ok)."""
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    r = subprocess.run([sys.executable, *args], env=env, cwd=SKILL)
    return r.returncode


def _load_profile():
    try:
        import json
        from lib.config import PROFILE_PATH
        with open(PROFILE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _risk_unverified(jid):
    """Risk fields in the latest dossier that are unverified/needs_data."""
    _imports()
    import json, os
    from lib.config import RESULTS_DIR
    h = os.path.join(RESULTS_DIR, str(jid), "handoff.json")
    if not os.path.exists(h):
        return []
    try:
        d = json.load(open(h, encoding="utf-8"))
    except Exception:
        return []
    from apply.common.terms import is_risk_field
    out = []
    for f in d.get("fields", []):
        if f.get("kind") == _T.UNVERIFIED and is_risk_field(f.get("label")):
            out.append(f.get("label", "?"))
        elif f.get("kind") == _T.NEEDS_DATA and f.get("required") \
                and is_risk_field(f.get("label")):
            out.append(f.get("label", "?") + " (needs data)")
    return out


def _drift_class(jid):
    """Coherence check (finding #6): is the DB's tailored stage backed by a
    dossier? The READY/HOLD decision reads risk fields from handoff.json but
    the job stage from the jobs table — if a job is `tailored` in the DB but
    has NO dossier, the risk-observation claim is unbacked.

    Returns one of:
      "dossier_lost" — GENUINE drift: the job was actually filled (has an
          audit log or handoffs history) but its dossier is gone. Re-fill
          to re-back the READY/HOLD claim.
      "mid_pipeline" — NORMAL: a results dir exists but the job has no fill
          evidence yet (never filled, or filled pre-dossier-system). Not
          drift — it just hasn't produced a dossier.
      None           — a dossier exists (fine).

    Reporting-only: does not re-bucket, it classifies so the orchestrator
    knows which jobs genuinely lost their evidence vs which are simply not
    filled yet."""
    import os
    from lib.config import RESULTS_DIR
    d = os.path.join(RESULTS_DIR, str(jid))
    if os.path.exists(os.path.join(d, "handoff.json")):
        return None
    # evidence of a prior fill: audit log, handoffs history, or dossier file
    filled_evidence = (
        os.path.exists(os.path.join(d, "apply_audit.jsonl"))
        or os.path.isdir(os.path.join(d, "handoffs"))
    )
    if filled_evidence:
        return "dossier_lost"
    return "mid_pipeline"


def _ready_jids(limit=None):
    """Tailored active jobs whose risk fields are ALL observed."""
    global _ready_cache
    from lib.db import get_conn
    conn = get_conn()
    rows = conn.execute(
        "SELECT id FROM jobs WHERE stage='tailored' AND state='active' "
        "ORDER BY updated_at DESC").fetchall()
    out = []
    for r in rows:
        jid = r["id"]
        bad = _risk_unverified(jid)
        if not bad:
            out.append(jid)
        if limit and len(out) >= limit:
            break
    return out


def cmd_status():
    """Fleet + READY + HOLD + open decisions, one screen."""
    _imports()
    from lib.db import get_conn
    conn = get_conn()
    n_total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    n_applied = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE stage='applied'").fetchone()[0]
    n_tailored = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE stage='tailored' AND state='active'"
    ).fetchone()[0]
    ready = _ready_jids()
    print(f"FLEET: {n_total} jobs | applied={n_applied} tailored-active={n_tailored}",
          file=sys.stderr)
    print(f"  READY: {len(ready)} (all risk fields observed)", file=sys.stderr)
    for j in ready[:8]:
        print(f"    {j[:12]}", file=sys.stderr)
    if len(ready) > 8:
        print(f"    ... +{len(ready)-8}", file=sys.stderr)
    _hold = [r["id"] for r in conn.execute(
        "SELECT id FROM jobs WHERE stage='tailored' AND state='active'"
    ).fetchall() if r["id"] not in ready]
    print(f"  HOLD: {len(_hold)} (unverified/needs-data risk fields)", file=sys.stderr)
    for j in _hold[:5]:
        print(f"    {j[:12]}  {', '.join(_risk_unverified(j)[:2])}", file=sys.stderr)
    # Cross-store drift (finding #6): a tailored-active job whose dossier is
    # gone but WAS filled has an unbacked READY/HOLD claim. Only flag
    # "dossier_lost" (genuine drift) — mid_pipeline jobs (never filled) are
    # normal, they just haven't produced a dossier yet. Report, don't re-bucket.
    _drift = [r["id"] for r in conn.execute(
        "SELECT id FROM jobs WHERE stage='tailored' AND state='active'"
    ).fetchall() if _drift_class(r["id"]) == "dossier_lost"]
    _mid = sum(1 for r in conn.execute(
        "SELECT id FROM jobs WHERE stage='tailored' AND state='active'"
    ).fetchall() if _drift_class(r["id"]) == "mid_pipeline")
    if _drift:
        print(f"  DRIFT: {len(_drift)} filled-but-dossier-lost job(s) — "
              f"READY/HOLD unverified (re-fill to regenerate)", file=sys.stderr)
        for j in _drift[:5]:
            print(f"    {j[:12]}", file=sys.stderr)
    if _mid:
        print(f"  NO-DOSSIER (not filled yet): {_mid} — normal, will produce "
              f"a dossier on first fill", file=sys.stderr)
    print(f"\nNEXT: ji decisions  |  ji ready  |  report.py shadow --classify",
          file=sys.stderr)


def cmd_decisions(owner=None):
    """Delegate to report.py handovers (the inbox)."""
    args = ["report.py", "handovers"]
    if owner:
        args.append(owner)
    return _run(*args)


def cmd_answer(jid, label, value):
    """Record an answer for a field — via apply.py act --fill --answers."""
    _imports()
    import json
    ans = json.dumps({label: value})
    return _run("apply.py", "act", "--fill", jid, "--answers", ans)


def cmd_verify(jid, all_fields=False):
    """Risk-field value review — the sanctioned PII view. Prints the resolved
    answer for each risk field. With `all_fields=True`, prints EVERY field
    (full-dossier review — the fix for the URN/location miss: every suspicious
    field must be visible, not just risk-classified ones)."""
    _imports()
    import json, os
    from lib.config import RESULTS_DIR
    from apply.common.terms import is_risk_field
    h = os.path.join(RESULTS_DIR, str(jid), "handoff.json")
    if not os.path.exists(h):
        print(f"VERIFY: no dossier for {jid}", file=sys.stderr)
        return 1
    d = json.load(open(h, encoding="utf-8"))
    print(f"VERIFY {jid}  ({d.get('mode', '?')})", file=sys.stderr)
    shown = 0
    for f in d.get("fields", []):
        suspicious = f.get("kind") in (_T.UNVERIFIED, _T.NEEDS_DATA,
                                       _T.REJECTED_BY_FORM, _T.INTERACTION_FAILED)
        if all_fields or is_risk_field(f.get("label")) \
                or f.get("method") == "prefilled" or suspicious:
            extra = ""
            if f.get("prefilled_value"):
                extra = f"  [prefilled: {f.get('prefilled_value')}]"
            if f.get("selected_text"):
                extra += f"  [read-back: {str(f.get('selected_text'))[:30]}]"
            prov = f" ({f.get('provenance','')})" if f.get("provenance") else ""
            print(f"  {f.get('kind','?')[:12]:12} {f.get('label','?')[:38]:38} "
                  f"= {f.get('answer','')}{prov}{extra}", file=sys.stderr)
            shown += 1
    if not shown:
        print("  (no risk/prefilled/suspicious fields in dossier — "
              "add --all to see every field)", file=sys.stderr)
    print("  DECISION: review values above, then 'ji answer <jid> \"<label>\": \"<value>\"'",
          file=sys.stderr)
    return 0


def cmd_ready(limit=None):
    """Jobs whose risk fields are all independently observed."""
    ready = _ready_jids(limit=limit)
    if not ready:
        print("READY: none — run shadow or fix HOLD fields", file=sys.stderr)
        return 0
    print(f"READY: {' '.join(j[:12] for j in ready[:20])}", file=sys.stderr)
    return 0


def cmd_job(jid):
    return _run("report.py", "handoff", jid)


def cmd_diff(jid):
    return _run("report.py", "diff", jid)


def cmd_audit(jid):
    return _run("report.py", "audit", jid)


def cmd_apply(jid):
    """detect + fill + check in one; stops at the value-review gate."""
    from apply.detect import run as detect_run
    from apply.act.fill import cmd_fill
    from apply.act.check import cmd_check
    detect_run(jid)
    rc = cmd_fill(jid)
    if rc:
        return rc
    return cmd_check(jid)


def cmd_submit(jid, force=False):
    _imports()
    args = ["apply.py", "act", "--submit", jid]
    if force:
        args.append("--force")
    return _run(*args)


def cmd_shadow(limit=None, recheck=False):
    args = ["apply.py", "shadow"]
    if limit:
        args += ["--limit", str(limit)]
    if recheck:
        args.append("--recheck")
    return _run(*args)


def cmd_fetch(days=None):
    """Full ingestion pass: stage emails → extract URLs → enrich descriptions.
    Runs stage_emails + extract + enrich in sequence (the docstring's promise —
    previously this only ran enrich)."""
    args = ["stage_emails.py"]
    if days:
        args += ["--days", str(days)]
    _run(*args)
    _run("extract.py", "auto")
    return _run("enrich.py")


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("help", "-h", "--help"):
        print(__doc__, file=sys.stderr)
        return 0
    cmd = sys.argv[1]
    args = sys.argv[2:]

    if cmd == "status":
        return cmd_status()
    if cmd == "decisions":
        owner = None
        if "--owner" in args:
            i = args.index("--owner")
            if i + 1 < len(args):
                owner = args[i + 1]
        return cmd_decisions(owner)
    if cmd == "answer":
        if len(args) < 3:
            print("Usage: ji answer <jid> \"<label>\": \"<value>\"", file=sys.stderr)
            return 1
        jid, spec = args[0], " ".join(args[1:])
        if ":" not in spec:
            print("Usage: ji answer <jid> \"<label>\": \"<value>\"", file=sys.stderr)
            return 1
        label, value = spec.split(":", 1)
        return cmd_answer(jid, label.strip().strip('"'),
                          value.strip().strip('"'))
    if cmd == "verify":
        if not args:
            print("Usage: ji verify <jid> [--all]", file=sys.stderr)
            return 1
        return cmd_verify(args[0], all_fields="--all" in args)
    if cmd == "ready":
        limit = None
        if "--limit" in args:
            i = args.index("--limit")
            if i + 1 < len(args):
                limit = int(args[i + 1])
        return cmd_ready(limit)
    if cmd == "job":
        if not args:
            print("Usage: ji job <jid>", file=sys.stderr)
            return 1
        return cmd_job(args[0])
    if cmd == "diff":
        if not args:
            print("Usage: ji diff <jid>", file=sys.stderr)
            return 1
        return cmd_diff(args[0])
    if cmd == "audit":
        if not args:
            print("Usage: ji audit <jid>", file=sys.stderr)
            return 1
        return cmd_audit(args[0])
    if cmd == "apply":
        if not args:
            print("Usage: ji apply <jid>", file=sys.stderr)
            return 1
        return cmd_apply(args[0])
    if cmd == "submit":
        if not args:
            print("Usage: ji submit <jid> [--force]", file=sys.stderr)
            return 1
        return cmd_submit(args[0], force="--force" in args)
    if cmd == "shadow":
        limit = None
        if "--limit" in args:
            i = args.index("--limit")
            if i + 1 < len(args):
                limit = int(args[i + 1])
        return cmd_shadow(limit=limit, recheck="--recheck" in args)
    if cmd == "fetch":
        return cmd_fetch()
    if cmd == "verify-applied":
        if not args:
            print("Usage: ji verify-applied <jid> [--manual] | --all",
                  file=sys.stderr)
            return 1
        if args[0] == "--all":
            return _run("report.py", "applied-confirm", "--all")
        extra = ["--manual"] if "--manual" in args else []
        return _run("report.py", "applied-confirm", args[0], *extra)
    if cmd == "applied":
        extra = ["--unconfirmed"] if "--unconfirmed" in args else []
        return _run("report.py", "applied", *extra)
    # Superset fallback: any ji command not handled above is forwarded to the
    # owning engine — report.py for evidence/config/decisions, apply.py for
    # apply-pipeline actions. This makes ji a TRUE superset (SURFACE_AUDIT.md):
    # the orchestrator memorizes ji only; the engines stay callable beneath.
    # Pipeline-stage namespace: ji <stage> <verb> ... → python <stage>.py
    # <verb> ... Every stage engine stays callable beneath ji (SURFACE_AUDIT
    # v2) — the orchestrator never needs to leave ji.
    if cmd in _STAGE_CMDS:
        return _run(_STAGE_CMDS[cmd], *args)
    if cmd in _REPORT_CMDS:
        return _run("report.py", cmd, *args)
    if cmd in _APPLY_CMDS:
        return _run("apply.py", cmd, *args)
    print(f"Unknown ji command: {cmd}", file=sys.stderr)
    print(__doc__, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
