#!/usr/bin/env python3
"""apply.py — Unified apply pipeline: detect, navigate, act, verify.

Usage:
  python3 apply.py detect [<jid>]       Auto-pick first tailored if no JID
  python3 apply.py navigate <jid>
  python3 apply.py act --fill <jid> [--answers '{}']
  python3 apply.py act --next <jid>
  python3 apply.py act --back <jid>
  python3 apply.py act --submit <jid> [--confirm]
  python3 apply.py act --inspect <jid> [--candidate N]
  python3 apply.py verify <jid>
  python3 apply.py reject <jid>         Skip permanently
  python3 apply.py flag <jid>           Toggle auth wall
  python3 apply.py retry [<jid>]        Re-attempt failed (or specific JID)
  python3 apply.py undo <jid>           Move back one stage
"""
import os, sys
SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL_DIR)


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

    act_p = sub.add_parser("act", help="Fill / next / submit / inspect")
    act_p.add_argument("jid", help="Job ID")
    act_p.add_argument("--fill", action="store_true")
    act_p.add_argument("--next", action="store_true")
    act_p.add_argument("--back", action="store_true")
    act_p.add_argument("--submit", action="store_true")
    act_p.add_argument("--inspect", action="store_true")
    act_p.add_argument("--dry-run", action="store_true")
    act_p.add_argument("--answers", help="JSON field->value mapping for --fill")
    act_p.add_argument("--candidate", type=int, default=None)
    act_p.add_argument("--confirm", action="store_true")

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

    args = parser.parse_args()

    if args.command == "detect":
        from apply.detect import run
        run(args.jid or _auto_jid())
    elif args.command == "navigate":
        from apply.navigate import run
        run(args.jid)
    elif args.command == "act":
        from apply.act import run
        run(args)
    elif args.command == "verify":
        from apply.verify import run
        run(args.jid)
    elif args.command == "reject":
        from lib.db import get_job, advance_job
        from lib.auth_walls import remove
        job = get_job(args.jid)
        if job:
            advance_job(args.jid, job.get("stage", "tailored"), state="rejected")
            remove(args.jid)
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
            failed = conn.execute("SELECT id FROM jobs WHERE stage='applied' AND state='failed'").fetchall()
            for r in failed:
                advance_job(r["id"], "tailored", state="active", error=None)
                print(f"RETRY: {r['id']}", file=sys.stderr)
    elif args.command == "undo":
        from lib.db import get_job, advance_job
        from lib.auth_walls import remove
        job = get_job(args.jid)
        if job:
            stage = job.get("stage", "")
            prev = {"applied": "tailored", "tailored": "described", "described": "extracted"}
            new_stage = prev.get(stage, "tailored")
            advance_job(args.jid, new_stage, state="active", error=None)
            remove(args.jid)
            print(f"UNDO: {args.jid} {stage} -> {new_stage}", file=sys.stderr)


if __name__ == "__main__":
    main()
