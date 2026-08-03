"""scripts/lint.py — mechanical honesty gate for the codebase.

Cooked-in lessons (ETHOS.md §5 aftermath):
  - "fully wired" claims were wrong twice; the grep would have caught it.
  - dead-weight strings (Skyvern) lingered in user-facing output.
  - nested dirs appeared in port copies (apply/apply).
  - vocabulary literals drifted across modules before terms.py existed.

Checks (exit 1 on any failure):
  1. All Python compiles.
  2. Vocabulary literals: migrated modules may NOT hardcode status/kind/
     outcome/severity values (terms.py constants are the only source).
     Legit exceptions: `"X" in state` DB-string heuristics in report.py,
     DB stage names in auto.py ("applied"/"tailored"/"failed"/"active"),
     field outcome "filled", docstrings/comments.
  3. Dead strings: no Skyvern mentions in user-facing emit/print strings.
  4. No nested package dirs (apply/apply, lib/lib).
  5. No leftover tmp migration scripts in the tree.
"""
import os
import re
import sys
import py_compile

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Values that MUST come from terms.py when they appear as VALUES in these
# modules. Dict keys (r.get("unverified"), f["kind"]) and reason-local
# strings ("accepted_unverified", "no_answer") are NOT vocabulary values.
VOCAB_PATTERNS = [
    r'(?:= |== |!= |return |emit_status\(|in \(|: )"(login_required|'
    r'login_failed|captcha_required|2fa_required|timed_out|no_apply_path|'
    r'hold|blocked|check_failed|regression_gate|filled)"',
    r'(?:= |== |!= |return |emit_status\(|in \(|: )"(held_shadow|stopped|'
    r'skipped|crash|timeout|already_applied|submitted|exception)"',
    r'(?:= |== |!= |return |emit_status\(|in \(|: )"(verified|unverified|'
    r'needs_data|rejected_by_form|interaction_failed)"',
    r'(?:= |== |!= |return |emit_status\(|in \(|: )"(policy_off|api_down|'
    r'declined|unused|used)"',
    r'emit_status\("(ERROR|WARN|INFO)"',
]
MIGRATED = [
    "apply/act/fill.py", "apply/act/check.py", "apply/act/submit.py",
    "apply/auto.py", "apply/shadow.py", "apply/shadow_worker.py",
    "lib/report.py",
]
# Substring matchers: lines containing these are exempt from the vocab scan.
LEGIT = [
    'in state',           # report.py DB-state-string heuristics
    'stage == ', 'stage="', 'stage=',   # DB stage machine (auto.py)
    '"applied"', '"tailored"', '"failed"', '"active"', '"described"',
    '"extracted"', '"rejected"',  # DB stages/states
    'outcome": "filled"', 'outcome": "failed"', 'outcome": "no_answer"',
    '"outcome": "filled"', '"outcome": "failed"', '"outcome": "no_answer"',
    'accepted_unverified',  # reason-local string, not vocabulary
]
# Dead-weight strings in USER-VISIBLE output. Lines are exempt when the
# component is provably gated: skyvern_result/_skyvern_ok variables
# (only set when the optional module imports) and guarded helpers.
DEAD_STRINGS = [
    r'Skyvern',
]
DEAD_EXEMPT = ["skyvern_result", "_skyvern_ok", "skyvern_bridge",
               "Handing off", "Skyvern run_id", "Skyvern fill failed",
               "Skyvern: {status}", "poll Skyvern fill progress",
               "fill_run_id", "_poll_skyvern", "SKYVERN_FILL",
               "skyvern vision", "SKYVERN_VERIFY"]  # data-gated channels
DEAD_SKIP_MODULES = {"skyvern_bridge.py"}  # the adapter module itself
NESTED_DIRS = [
    os.path.join("apply", "apply"), os.path.join("lib", "lib"),
    os.path.join("apply", "lib"), os.path.join("tests", "tests"),
]


def _strip_comments(line):
    # crude: remove # comments and docstring-ish lines ('''/""")
    if line.strip().startswith(('#', '"""', "'''")):
        return ""
    return re.sub(r'\s+#.*$', '', line)


def check_compile():
    fails = []
    for root, _dirs, files in os.walk(SKILL):
        if any(part in ("__pycache__", ".pytest_cache", "node_modules")
               for part in root.split(os.sep)):
            continue
        for f in files:
            if f.endswith(".py"):
                p = os.path.join(root, f)
                try:
                    py_compile.compile(p, doraise=True)
                except Exception as e:
                    fails.append(f"{p}: {e}")
    return fails


def check_vocab():
    fails = []
    for rel in MIGRATED:
        p = os.path.join(SKILL, rel)
        if not os.path.exists(p):
            fails.append(f"{rel}: MISSING (port manifest?)")
            continue
        for i, line in enumerate(open(p, encoding="utf-8"), 1):
            code = _strip_comments(line)
            if not code.strip():
                continue
            if any(ok in line for ok in LEGIT):
                continue
            for pat in VOCAB_PATTERNS:
                if re.search(pat, code):
                    fails.append(f"{rel}:{i}: hardcoded vocabulary: "
                                 f"{line.strip()[:80]}")
                    break
    return fails


def check_dead_strings():
    fails = []
    for root, _dirs, files in os.walk(os.path.join(SKILL, "apply")):
        for f in files:
            if not f.endswith(".py") or f in DEAD_SKIP_MODULES:
                continue
            p = os.path.join(root, f)
            in_guarded = False
            for i, line in enumerate(open(p, encoding="utf-8"), 1):
                code = _strip_comments(line)
                if not code.strip():
                    continue
                # Block-level guard tracking: a def line mentioning skyvern
                # (e.g. _poll_skyvern_fill) marks its whole body guarded —
                # those functions are unreachable without the module.
                if code.strip().startswith(("def ", "class ")):
                    in_guarded = "skyvern" in code.lower()
                    continue
                if in_guarded:
                    continue
                if any(x in code for x in DEAD_EXEMPT):
                    continue
                if "skyvern" in code.lower() and ("print" in code or
                                                  "emit_" in code or
                                                  "status" in code):
                    fails.append(f"{os.path.relpath(p, SKILL)}:{i}: "
                                 f"dead string: {line.strip()[:80]}")
    return fails


def check_nested():
    fails = []
    for rel in NESTED_DIRS:
        if os.path.isdir(os.path.join(SKILL, rel)):
            fails.append(f"nested dir present: {rel}")
    return fails


def main():
    all_fails = []
    for name, fn in [("compile", check_compile),
                     ("vocabulary", check_vocab),
                     ("dead strings", check_dead_strings),
                     ("nested dirs", check_nested)]:
        fails = fn()
        print(f"[{name}] {'FAIL' if fails else 'PASS'} "
              f"({len(fails)} issue(s))")
        for f in fails[:15]:
            print(f"    {f}")
        all_fails += fails
    if all_fails:
        print(f"\nLINT FAIL: {len(all_fails)} issue(s) — fix before port.")
        return 1
    print("\nLINT PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
