#!/usr/bin/env python3
"""apply.py — Apply pipeline: detect, navigate, hybrid fill/submit.

Usage:
  python3 apply.py auto [--jid <jid>] [--limit N] [--no-submit] [--quick]
                         Run full pipeline for all tailored jobs
  python3 apply.py detect [<jid>]
  python3 apply.py navigate <jid>
  python3 apply.py act --fill <jid> [--answers '{}'] [--max-pages N]
  python3 apply.py act --check <jid>
  python3 apply.py act --submit <jid>
  python3 apply.py act --inspect <jid>
  python3 apply.py act --investigate <jid>
  python3 apply.py verify <jid>
  python3 apply.py reject <jid>         Skip permanently
  python3 apply.py flag <jid>           Toggle auth wall
  python3 apply.py retry [<jid>]        Re-attempt failed
  python3 apply.py undo <jid>           Move back one stage
  python3 apply.py registry candidates  List unconfirmed probe observations
  python3 apply.py registry confirm <h> Manually promote an observation
  python3 apply.py registry clear <h>   Delete an observation
  python3 apply.py registry corpus      List captured DOM snapshots
  python3 apply.py registry failures    List cascade-miss artifacts
  python3 apply.py registry drift       Self-test corpus snapshots for drift

The probe router adapts automatically: it learns which strategy works
for each capability profile (capability-keyed, not domain-keyed), and
reverts on drift. The full 9-level cascade always runs as a backstop.
Use `registry` to inspect or override the learned state.
"""
import os, sys
SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL_DIR)
import json


def _auto_jid():
    from lib.db import get_jobs_by_stage
    jobs = get_jobs_by_stage("tailored")
    if not jobs:
        print("NO_TAILORED: no jobs ready to apply", file=sys.stderr)
        sys.exit(0)
    return jobs[0][0]


def main():
    import argparse

    parser = argparse.ArgumentParser(prog="apply.py", description="Unified apply pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    detect_p = sub.add_parser("detect", help="Pre-flight classify")
    detect_p.add_argument("jid", nargs="?", help="Job ID (auto-pick first tailored if omitted)")

    nav_p = sub.add_parser("navigate", help="LinkedIn -> External ATS")
    nav_p.add_argument("jid", help="Job ID")

    act_p = sub.add_parser("act", help="Fill / next / submit / inspect / investigate (hybrid Playwright+Skyvern)")
    act_p.add_argument("jid", help="Job ID")
    act_p.add_argument("--fill", action="store_true", help="Fill the application form")
    act_p.add_argument("--next", action="store_true", help="Next page on multi-step form")
    act_p.add_argument("--submit", action="store_true", help="Submit the application (runs --check first)")
    act_p.add_argument("--force", action="store_true", help="Force submit, skipping pre-submit check")
    act_p.add_argument("--inspect", action="store_true", help="Analyze the page (screenshot, fields, buttons)")
    act_p.add_argument("--check", action="store_true", help="Pre-submit validation: flag contradictions before submitting")
    act_p.add_argument("--investigate", action="store_true", help="Deep-analyze unknown platform (Skyvern investigator)")
    act_p.add_argument("--answers", help="JSON field->value mapping for --fill")
    act_p.add_argument("--no-verify", action="store_true", help="Skip vision verification after fill")
    act_p.add_argument("--quick", action="store_true", help="Deterministic-only pass: no vision, no Skyvern")
    act_p.add_argument("--max-pages", type=int, default=4, help="Max form pages to fill in one --fill run")

    verify_p = sub.add_parser("verify", help="Check submission result")
    verify_p.add_argument("jid", help="Job ID")

    reject_p = sub.add_parser("reject", help="Skip permanently")
    reject_p.add_argument("jid", help="Job ID")

    flag_p = sub.add_parser("flag", help="Toggle auth wall")
    flag_p.add_argument("jid", help="Job ID")

    retry_p = sub.add_parser("retry", help="Re-attempt failed")
    retry_p.add_argument("jid", nargs="?", help="Job ID (default: all failed)")

    undo_p = sub.add_parser("undo", help="Move back one stage")
    undo_p.add_argument("jid", help="Job ID")

    map_p = sub.add_parser("mappings", help="Field→meaning mapping store (ADR-001 Phase 3)")
    map_p.add_argument("action", choices=["list", "confirm", "clear"],
                       help="list pending for a job / confirm (promote) them / clear them")
    map_p.add_argument("jid", help="Job ID")

    creds_p = sub.add_parser("creds", help="Credential vault for ATS sites")
    creds_p.add_argument("args", nargs="+", help="list | get <domain> | set <domain> <email> <password> | delete <domain>")

    reg_p = sub.add_parser("registry", help="Probe observation + corpus management")
    reg_p.add_argument("action", choices=["candidates", "confirm", "clear", "corpus", "failures", "drift"],
                       help="candidates (list unconfirmed obs) | confirm <hash> (manual promote) | "
                            "clear <hash> (delete an obs) | corpus (list snapshots) | "
                            "failures (list captured cascade-miss failures) | "
                            "drift (self-test: probe all corpus snapshots for changes)")
    reg_p.add_argument("hash", nargs="?", help="Profile hash (for confirm/clear)")
    reg_p.add_argument("--dry-run", action="store_true",
                       help="drift: report only, no auto-demote (default: auto-demote stale)")

    args = parser.parse_args()

    if args.command == "detect":
        from apply.detect import run
        run(args.jid or _auto_jid())
    elif args.command == "navigate":
        from apply.navigate import run
        run(args.jid)
    elif args.command == "act":
        from apply.act import run
        if args.fill and args.submit:
            print("ERROR: --fill and --submit together is not allowed. The orchestrator must review the fill report before submitting.", file=sys.stderr)
            sys.exit(1)
        cmd = "fill" if args.fill else ("submit" if args.submit else
              "next" if args.next else ("inspect" if args.inspect else
              "check" if args.check else
              "investigate" if args.investigate else ""))
        if not cmd:
            print("ERROR: specify --fill, --next, --submit, --inspect, or --investigate", file=sys.stderr)
            sys.exit(1)
        run({"command": cmd, "jid": args.jid, "--answers": args.answers,
             "--no-verify": args.no_verify, "--max-pages": args.max_pages,
             "--quick": args.quick,
             "--confirm": args.submit,
             "--force": args.force})
    elif args.command == "verify":
        from apply.verify import run
        run(args.jid)
    elif args.command == "reject":
        from lib.db import get_job, advance_job
        from lib.auth_walls import remove
        from apply.common.apply_state import clear as _clear_state
        job = get_job(args.jid)
        if job:
            advance_job(args.jid, job.get("stage", "tailored"), state="rejected")
            remove(args.jid)
            _clear_state(args.jid)
            print(f"REJECTED: {args.jid}", file=sys.stderr)
    elif args.command == "flag":
        from lib.db import get_conn, get_job
        from lib.auth_walls import add, remove
        job = get_job(args.jid)
        if job:
            conn = get_conn()
            r = conn.execute("SELECT auth_wall FROM jobs WHERE id=?", (args.jid,)).fetchone()
            if r and r["auth_wall"]:
                remove(args.jid)
                print(f"UNFLAGGED: {args.jid}", file=sys.stderr)
            else:
                add(args.jid, job.get("url", ""), job.get("title", ""), job.get("company", ""))
                print(f"FLAGGED: {args.jid}", file=sys.stderr)
    elif args.command == "retry":
        from lib.db import get_conn, get_job, advance_job
        if args.jid:
            job = get_job(args.jid)
            if job and job.get("state") == "failed":
                advance_job(args.jid, "tailored", state="active", error=None)
                print(f"RETRY: {args.jid}", file=sys.stderr)
            else:
                print(f"Job {args.jid} not failed or not found", file=sys.stderr)
        else:
            conn = get_conn()
            # A failed apply keeps its current stage (tailored), so failures live at
            # stage='tailored', not 'applied'. Scope to tailored to avoid resetting
            # tailor-stage failures (which belong to `tailor.py retry`).
            failed = conn.execute("SELECT id FROM jobs WHERE stage='tailored' AND state='failed'").fetchall()
            for r in failed:
                advance_job(r["id"], "tailored", state="active", error=None)
                print(f"RETRY: {r['id']}", file=sys.stderr)
    elif args.command == "undo":
        from lib.db import get_job, advance_job
        from lib.auth_walls import remove
        from apply.common.apply_state import clear as _clear_state
        from apply.common.page_helpers import load_state, save_state
        job = get_job(args.jid)
        if job:
            stage = job.get("stage", "")
            prev = {"applied": "tailored", "tailored": "described", "described": "extracted"}
            new_stage = prev.get(stage, "tailored")
            advance_job(args.jid, new_stage, state="active", error=None)
            remove(args.jid)
            _clear_state(args.jid)
            # Clear submit_clicked from global state if it belongs to this job
            _st = load_state()
            if _st.get("jid") == args.jid and _st.get("submit_clicked"):
                _st["submit_clicked"] = False
                save_state(_st)
            print(f"UNDO: {args.jid} {stage} -> {new_stage}", file=sys.stderr)
    elif args.command == "mappings":
        from lib.config import STATE_DIR as _STATE_DIR
        _mp = os.path.join(_STATE_DIR, "field_mappings.json")
        if args.action == "list":
            try:
                with open(_mp, encoding="utf-8") as _f:
                    _data = json.load(_f)
            except Exception:
                _data = {}
            print(f"MAPPINGS: {len(_data)} learned label→value mapping(s) "
                  f"(promoted automatically by resolve.learn_mapping)", file=sys.stderr)
            for _k, _v in sorted(_data.items()):
                print(f"  {_k[:60]} -> {str(_v)[:40]}", file=sys.stderr)
            print(f"  file: {_mp}", file=sys.stderr)
        elif args.action == "confirm":
            print("MAPPINGS: nothing pending — learned mappings are promoted "
                  "automatically via resolve.learn_mapping (ADR-001 successor "
                  "of the old pending-mapping store)", file=sys.stderr)
        elif args.action == "clear":
            try:
                os.remove(_mp)
                print("MAPPINGS: cleared all learned mappings", file=sys.stderr)
            except FileNotFoundError:
                print("MAPPINGS: nothing to clear", file=sys.stderr)
    elif args.command == "creds":
        from lib.credentials import cmd_creds
        cmd_creds(args.args)
    elif args.command == "registry":
        from apply.common.registry_cli import cmd_registry
        return cmd_registry(args.action, args.hash, dry_run=getattr(args, "dry_run", False))


if __name__ == "__main__":
    sys.exit(main() or 0)
