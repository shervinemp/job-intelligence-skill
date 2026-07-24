"""act/__init__.py — CLI dispatch for act commands.

Re-exports run() for the apply.py entry point: `from apply.act import run`.
"""
import json, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from apply.common.output import emit_error


def run(args):
    cmd = args.get("command", "")
    jid = args.get("jid", "")

    if cmd == "fill":
        answers = None
        raw = args.get("--answers")
        if raw:
            try:
                answers = json.loads(raw)
            except json.JSONDecodeError:
                emit_error(f"invalid --answers JSON: {raw}")
                return 1
        verify = not args.get("--no-verify", False)
        from apply.act.fill import cmd_fill
        return cmd_fill(jid, answers, verify=verify,
                        max_pages=args.get("--max-pages", 4),
                        quick=args.get("--quick", False))

    elif cmd == "next":
        from apply.act.inspect import cmd_next
        return cmd_next(jid)

    elif cmd == "back":
        print("  Back: not implemented in hybrid mode — use browser back", file=sys.stderr)
        return 1

    elif cmd == "submit":
        from apply.act.submit import cmd_submit
        return cmd_submit(jid, confirm=args.get("--confirm", False))

    elif cmd == "inspect":
        from apply.act.inspect import cmd_inspect
        return cmd_inspect(jid)

    elif cmd == "investigate":
        from apply.act.investigate import cmd_investigate
        return cmd_investigate(jid)

    else:
        emit_error(f"unknown act command: {cmd}")
        return 1
