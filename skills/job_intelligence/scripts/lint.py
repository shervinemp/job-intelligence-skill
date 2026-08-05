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
  3. Dead strings: no retired-tool mentions in user-facing emit/print
     strings (DEAD_STRINGS is the list).
  4. No nested package dirs — ANY dir that repeats its parent's name.
  5. CLI docs match reality: a command documented in an entrypoint's
     module docstring must dispatch, and a dispatchable command must be
     documented. Documentation that can lie is not documentation.

This file previously claimed a fifth check ("no leftover tmp migration
scripts") that main() never ran, and declared DEAD_STRINGS without ever
reading it — the honesty gate was making unbacked claims about itself.
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
# Dead-weight strings in USER-VISIBLE output. Retired tools/backends whose
# names must never reach an operator again.
DEAD_STRINGS = ["skyvern"]

# Dirs that must never be nested inside a dir of the same name is the
# GENERAL rule (see check_nested) — this list is only for extra pairs that
# are wrong for other reasons.
NESTED_DIRS = [
    os.path.join("apply", "lib"),
    os.path.join("tests", "tests"),
]

# Entrypoints whose module docstring is a command contract:
#   file -> module path used to introspect the argparse subcommands.
CLI_ENTRYPOINTS = {
    "apply.py": "apply",
    "reach.py": "reach",
    "extract.py": "extract",
    "enrich.py": "enrich",
    "tailor.py": "tailor",
}
# report.py builds its dispatch by hand (if/elif on sys.argv[1]) rather
# than argparse, so its commands are read from the dispatcher source.
REPORT_ENTRY = ("report.py", os.path.join("lib", "report.py"))
# ji.py is the orchestrator surface — also a hand-rolled dispatch.
JI_ENTRY = ("ji.py", os.path.join("ji.py"))


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
    for root, _dirs, files in os.walk(SKILL):
        if any(part in ("__pycache__", ".pytest_cache", "node_modules")
               for part in root.split(os.sep)):
            continue
        for f in files:
            if not f.endswith(".py"):
                continue
            p = os.path.join(root, f)
            if os.path.abspath(p) == os.path.abspath(__file__):
                continue  # the rule itself is exempt
            for i, line in enumerate(open(p, encoding="utf-8"), 1):
                code = _strip_comments(line)
                if not code.strip():
                    continue
                low = code.lower()
                if any(d in low for d in DEAD_STRINGS) and (
                        "print" in code or "emit_" in code or "status" in code):
                    fails.append(f"{os.path.relpath(p, SKILL)}:{i}: "
                                 f"dead string: {line.strip()[:80]}")
    return fails


def check_nested():
    """No directory may contain a subdirectory of its own name.

    The old check was a hardcoded four-entry denylist of yesterday's
    incidents (apply/apply, lib/lib, ...) and therefore missed the nested
    job_intelligence/job_intelligence that a port copy actually created —
    complete with a stale .env. A rule beats a list of past mistakes.
    """
    fails = []
    for root, dirs, _files in os.walk(SKILL):
        if any(p in ("__pycache__", ".pytest_cache", ".git", "node_modules")
               for p in root.split(os.sep)):
            continue
        parent = os.path.basename(root)
        for d in dirs:
            if d == parent:
                fails.append(
                    f"nested dir present: {os.path.relpath(os.path.join(root, d), SKILL)}")
    for rel in NESTED_DIRS:
        if os.path.isdir(os.path.join(SKILL, rel)):
            fails.append(f"nested dir present: {rel}")
    return fails


def _documented_commands(path):
    """Commands named in an entrypoint's module docstring.

    A documented command is a token that appears at the start of a usage
    line after the script name, e.g. `python3 apply.py detect [<jid>]` or
    `reach.py email <jid> ...`.
    """
    src = open(path, encoding="utf-8").read()
    try:
        import ast
        doc = ast.get_docstring(ast.parse(src)) or ""
    except SyntaxError:
        return set()
    script = os.path.basename(path)
    found = set()
    for line in doc.splitlines():
        m = re.search(re.escape(script) + r"\s+([a-z][a-z_-]*)", line)
        if m:
            found.add(m.group(1))
    return found


def _argparse_commands(path):
    """Subcommand names registered via add_parser() in the entrypoint."""
    src = open(path, encoding="utf-8").read()
    return set(re.findall(r'add_parser\(\s*["\']([a-z][a-z_-]*)["\']', src))


def _report_commands(path):
    """Commands dispatched by report.py's hand-rolled if/elif chain."""
    src = open(path, encoding="utf-8").read()
    cmds = set(re.findall(r'cmd == ["\']([a-z][a-z_-]*)["\']', src))
    # ji.py forwards unhandled commands to the engines via _REPORT_CMDS /
    # _APPLY_CMDS (SURFACE_AUDIT.md superset) — those are dispatchable too.
    if "ji.py" in path:
        for name in ("_REPORT_CMDS", "_APPLY_CMDS"):
            m = re.search(name + r"\s*=\s*\{([^}]*)\}", src)
            if m:
                cmds.update(re.findall(r'["\']([a-z][a-z_-]*)["\']', m.group(1)))
    return cmds


def check_cli_docs():
    """Documented commands must dispatch; dispatchable commands must be
    documented. report.py advertised shell/companies/contacts (none
    existed) and apply.py advertised `auto` (no parser) — and both print
    that docstring on an unknown command, teaching the wrong surface at
    the moment the operator is already lost."""
    fails = []
    checks = [(f, os.path.join(SKILL, f), _argparse_commands)
              for f in CLI_ENTRYPOINTS]
    checks.append((REPORT_ENTRY[0], os.path.join(SKILL, REPORT_ENTRY[1]),
                   _report_commands))
    checks.append((JI_ENTRY[0], os.path.join(SKILL, JI_ENTRY[1]),
                   _report_commands))
    for label, impl_path, reader in checks:
        doc_path = os.path.join(SKILL, label)
        if not os.path.exists(doc_path) or not os.path.exists(impl_path):
            fails.append(f"{label}: MISSING (port manifest?)")
            continue
        documented = _documented_commands(doc_path)
        real = reader(impl_path)
        # 'help' is conventional and may be implicit.
        documented.discard("py")
        for phantom in sorted(documented - real - {"help"}):
            fails.append(f"{label}: documents '{phantom}' but nothing "
                         f"dispatches it")
        for undocumented in sorted(real - documented - {"help"}):
            fails.append(f"{label}: dispatches '{undocumented}' but the "
                         f"module docstring never mentions it")
    return fails


def main():
    all_fails = []
    for name, fn in [("compile", check_compile),
                     ("vocabulary", check_vocab),
                     ("dead strings", check_dead_strings),
                     ("nested dirs", check_nested),
                     ("cli docs", check_cli_docs)]:
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
