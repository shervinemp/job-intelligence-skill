#!/usr/bin/env python3
"""apply.py — Unified apply pipeline: detect, navigate, act, verify.

Usage:
  python3 apply.py detect <jid>
  python3 apply.py navigate <jid>
  python3 apply.py act --fill <jid> [--answers '{}']
  python3 apply.py act --next <jid>
  python3 apply.py act --back <jid>
  python3 apply.py act --submit <jid> [--confirm]
  python3 apply.py act --auto <jid> [--answers '{}']
  python3 apply.py verify <jid>
"""
import os, sys
SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SKILL_DIR)

def main():
    import argparse
    parser = argparse.ArgumentParser(prog="apply.py", description="Unified apply pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    detect_p = sub.add_parser("detect", help="Classify job type")
    detect_p.add_argument("jid", help="Job ID")

    nav_p = sub.add_parser("navigate", help="LinkedIn -> External ATS")
    nav_p.add_argument("jid", help="Job ID")

    act_p = sub.add_parser("act", help="Perform action: --fill/--next/--back/--submit/--auto")
    act_p.add_argument("jid", help="Job ID")
    act_p.add_argument("--fill", action="store_true", help="Fill fields on current page")
    act_p.add_argument("--next", action="store_true", help="Click Next/Review/Submit")
    act_p.add_argument("--back", action="store_true", help="Click Back")
    act_p.add_argument("--submit", action="store_true", help="Click Submit on review page")
    act_p.add_argument("--auto", action="store_true", help="Full auto loop")
    act_p.add_argument("--confirm", action="store_true", help="Actually submit (dry-run without)")
    act_p.add_argument("--answers", help="JSON answers for --fill")
    act_p.add_argument("--candidate", type=int, default=None, help="Pick candidate N from CANDIDATES list")
    act_p.add_argument("--trust", action="store_true", help="Enable auto-submit (default: learning mode)")

    verify_p = sub.add_parser("verify", help="Check submission result")
    verify_p.add_argument("jid", help="Job ID")

    args = parser.parse_args()

    if args.command == "detect":
        from apply.detect import run
        run(args.jid)
    elif args.command == "navigate":
        from apply.navigate import run
        run(args.jid)
    elif args.command == "act":
        from apply.act import run
        run(args)
    elif args.command == "verify":
        from apply.verify import run
        run(args.jid)

if __name__ == "__main__":
    main()
