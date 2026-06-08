"""output.py — Standardized formatter for all pipeline output.
The SLM contract: scan for lines starting with NEXT: — that's the next action.
NEXT: is always the last output line, always on its own line, always after all STATUS/ERROR/FILLED lines.
"""

import json
import sys

__all__ = [
    "emit_next", "emit_status", "emit_type", "emit_error", "emit_warn",
    "emit_fill_report", "emit_candidates",
]


def emit_next(action, detail=None):
    """Print NEXT: line. Must be the last output emitted."""
    if detail:
        print(f"NEXT: {action} — {detail}", file=sys.stderr)
    else:
        print(f"NEXT: {action}", file=sys.stderr)


def emit_status(status, detail=None):
    """Print STATUS: line. Informational context for NEXT decision."""
    if detail:
        print(f"STATUS: {status} — {detail}", file=sys.stderr)
    else:
        print(f"STATUS: {status}", file=sys.stderr)


def emit_error(msg):
    """Print an error that the LLM/SLM should handle."""
    print(f"ERROR: {msg}", file=sys.stderr)


def emit_warn(msg):
    """Print a warning that the LLM/SLM may act on."""
    print(f"WARN: {msg}", file=sys.stderr)


def emit_fill_report(filled, unfilled, page_num, profile=None):
    """Print the standardized FILLED/UNFILLED summary + unfilled field details."""
    print(f"FILLED: {filled}  UNFILLED: {len(unfilled)} [Page {page_num}]", file=sys.stderr)
    for f in unfilled:
        tag = f.get("tag", "?")
        label = f.get("label", "?")
        opts = f.get("options", [])
        if opts:
            print(f"  [{tag}] {label} -> {json.dumps(opts[:5])}", file=sys.stderr)
        else:
            print(f"  [{tag}] {label}", file=sys.stderr)
    if unfilled and profile:
        pk = sorted(k for k in profile.keys() if k != "common_answers")
        if pk:
            print(f"  Profile keys: {json.dumps(pk)}", file=sys.stderr)


def emit_candidates(cands, max_show=8):
    """Print standardized candidate list for model_choice."""
    print("CANDIDATES:", file=sys.stderr)
    for i, c in enumerate(cands[:max_show]):
        d = " [DISABLED]" if c.get("disabled") else ""
        print(f"  [{i}] '{c['text'][:40]}' score={c.get('score','?')}{d}", file=sys.stderr)


def emit_type(type_name, detail=None):
    """Print TYPE: line. Used by detect.py for job classification output."""
    if detail:
        print(f"TYPE: {type_name}\n{detail}", file=sys.stderr)
    else:
        print(f"TYPE: {type_name}", file=sys.stderr)
