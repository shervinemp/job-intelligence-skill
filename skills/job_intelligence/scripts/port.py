"""scripts/port.py — the ONLY supported way to sync workspace → repo.

Cooked-in lessons: manual Copy-Item caused three failures (nested
apply/apply duplicate, missing llm_policy.py, stale shadow.py). This
script copies the canonical manifest, verifies by hash, gates on the
repo suite, and prints the exact commit command.

Usage:
    python scripts/port.py [--skip-tests] [--repo <path>]
"""
import argparse
import hashlib
import os
import shutil
import subprocess
import sys

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_REPO = os.path.join(
    SKILL, "..", "..", "tmp", "job-intelligence-skill", "skills",
    "job_intelligence")

MANIFEST = [
    "apply.py", "tailor.py", "report.py", "ETHOS.md", "SKILL.md",
    "GUIDELINES.md", "PRODUCT.md", "REACH_PHASE.md",
    "categories.json", "gems.json", "tailor_prompt.md", "decisions.md",
    "requirements.txt",
    # apply tree (all files, no pycache)
    "apply",
    # lib files
    "lib/report.py", "lib/grounding.py", "lib/chrome_manager.py",
    "lib/quality.py",
    "lib/automation", "lib/db/jobs.py", "lib/db/__init__.py",
    # tests
    "tests",
    # tooling
    "scripts",
]


def _tree_files(root, base):
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in ("__pycache__", ".pytest_cache")]
        for f in filenames:
            if f.endswith((".pyc", ".pyo")):
                continue
            full = os.path.join(dirpath, f)
            out.append(os.path.relpath(full, base))
    return out


def _hash(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def _manifest_entries():
    entries = []
    for item in MANIFEST:
        src = os.path.join(SKILL, item)
        if os.path.isdir(src):
            entries += _tree_files(src, SKILL)
        else:
            entries.append(item)
    return sorted(entries)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-tests", action="store_true")
    ap.add_argument("--repo", default=DEFAULT_REPO)
    args = ap.parse_args()

    # 1. Lint gate
    print("== lint ==")
    rc = subprocess.run([sys.executable, os.path.join(SKILL, "scripts", "lint.py")])
    if rc.returncode != 0:
        print("PORT ABORTED: lint failed", file=sys.stderr)
        return 1

    # 2. Workspace suite gate
    if not args.skip_tests:
        print("== workspace suite ==")
        rc = subprocess.run([sys.executable, "-m", "pytest", "tests", "-q"],
                            cwd=SKILL)
        if rc.returncode != 0:
            print("PORT ABORTED: workspace tests failed", file=sys.stderr)
            return 1

    # 3. Copy the manifest, file by file
    print("== copy ==")
    entries = _manifest_entries()
    copied, skipped = 0, 0
    for rel in entries:
        src = os.path.join(SKILL, rel)
        dst = os.path.join(args.repo, rel)
        if not os.path.exists(src):
            print(f"  MISSING in workspace: {rel}", file=sys.stderr)
            return 1
        if os.path.exists(dst) and _hash(src) == _hash(dst):
            skipped += 1
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1
    print(f"  copied {copied}, unchanged {skipped}")

    # 4. Verify: every manifest entry present + hash-equal in the repo
    print("== verify ==")
    bad = 0
    for rel in entries:
        dst = os.path.join(args.repo, rel)
        if not os.path.exists(dst):
            print(f"  NOT IN REPO: {rel}", file=sys.stderr)
            bad += 1
        elif _hash(os.path.join(SKILL, rel)) != _hash(dst):
            print(f"  DIVERGED: {rel}", file=sys.stderr)
            bad += 1
    if bad:
        print(f"PORT ABORTED: {bad} file(s) missing/diverged", file=sys.stderr)
        return 1

    # 5. Nested-dir guard (the apply/apply lesson)
    for nested in (os.path.join("apply", "apply"),
                   os.path.join("lib", "lib")):
        if os.path.isdir(os.path.join(args.repo, nested)):
            print(f"PORT ABORTED: nested dir in repo: {nested}", file=sys.stderr)
            return 1

    # 6. Repo suite gate
    if not args.skip_tests:
        print("== repo suite ==")
        rc = subprocess.run(
            [sys.executable, "-m", "pytest", "skills/job_intelligence/tests",
             "-q"],
            cwd=os.path.join(args.repo, "..", ".."))
        if rc.returncode != 0:
            print("PORT ABORTED: repo tests failed", file=sys.stderr)
            return 1

    print("\nPORT OK — next steps:")
    print(f"  cd {args.repo}")
    print("  git add -A skills/job_intelligence && git status")
    print("  git commit -m '<message>' && git push origin feat/reach-phase")
    return 0


if __name__ == "__main__":
    sys.exit(main())
