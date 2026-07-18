"""output.py — Standardized formatter for all pipeline output.
The SLM contract: scan for lines starting with NEXT: — that's the next action.
NEXT: is always the last output line, always on its own line, always after all STATUS/ERROR/FILLED lines.
"""
import json
import sys

__all__ = [
    "emit_next", "emit_status", "emit_type", "emit_error", "emit_warn",
    "emit_fill_report", "emit_candidates", "field_format_hint",
]


FIELD_TYPE_HINTS = {
    "tel": "digits only, no +1 prefix",
    "phone": "digits only, no +1 prefix",
    "email": "email@domain.com",
    "url": "https://...",
    "number": "numeric",
}


_PROFILE_ANSWER_CACHE = {}


def field_format_hint(f, profile=None):
    hints = []
    ftype = (f.get("type") or "").lower()
    for kw, hint in FIELD_TYPE_HINTS.items():
        if kw in ftype:
            hints.append(hint)
            break
    label = (f.get("label") or "").lower()
    if "phone" in label and not hints:
        hints.append("digits only, no +1 prefix")
    if "salary" in label or "compensation" in label:
        hints.append("numeric, no commas")
    if "date" in label or f.get("datepicker"):
        hints.append("MM/DD/YYYY")
    tag = (f.get("tag") or "").lower()
    ml = f.get("maxlength")
    if ml:
        hints.append(f"max {ml} chars")
    pat = f.get("pattern")
    if pat:
        hints.append(f"pattern={pat}")
    # Add profile answer hint: if a profile answer key's keywords appear
    # in the field label, show it as a suggestion
    if profile:
        import re as _re
        _norm_label = _re.sub(r"[^a-z0-9]+", " ", label).strip()
        _label_words = set(_norm_label.split())
        _ans = profile.get("answers", {})
        for _ak, _av in _ans.items():
            if not _av:
                continue
            _norm_key = _re.sub(r"[^a-z0-9]+", " ", _ak.lower()).strip()
            _key_words = set(_norm_key.split())
            if len(_key_words) >= 2 and _key_words.issubset(_label_words):
                hints.append(f"profile:{_ak}={str(_av)[:30]}")
                break
    return " | ".join(hints) if hints else ""


def emit_diag(field_key: str, expected: str, actual: str, reason: str, detail: str = ""):
    """Structured diagnostic for fill failures.
    Machine-parseable: DIAG: <field_key> | expected=<expected> | actual=<actual> | <reason>
    Human-readable detail appended after the pipe."""
    line = f"DIAG: {field_key} | expected={expected[:80]} | actual={actual[:80]} | {reason}"
    if detail:
        line += f" | {detail}"
    print(line, file=sys.stderr)


def emit_next(action, detail=None):
    if detail:
        print(f"NEXT: {action} — {detail}", file=sys.stderr)
    else:
        print(f"NEXT: {action}", file=sys.stderr)


def emit_status(status, detail=None):
    if detail:
        print(f"STATUS: {status} — {detail}", file=sys.stderr)
    else:
        print(f"STATUS: {status}", file=sys.stderr)


def emit_error(msg):
    print(f"ERROR: {msg}", file=sys.stderr)


def emit_warn(msg):
    print(f"WARN: {msg}", file=sys.stderr)


def emit_fill_report(filled, unfilled, page_num, profile=None):
    print(f"FILLED: {filled}  UNFILLED: {len(unfilled)} [Page {page_num}]", file=sys.stderr)
    for f in unfilled:
        tag = f.get("tag", "?")
        label = f.get("label", "?")
        opts = f.get("options", [])
        fmt = field_format_hint(f, profile)
        has_attempted = "attempted" in f
        if has_attempted:
            extra = f"  fmt={fmt}" if fmt else ""
            print(f"  DIFF: [{tag}] {label} attempted={str(f['attempted'])[:50]}{extra}", file=sys.stderr)
        elif opts:
            opt_str = json.dumps(opts[:5])
            extra = f"  fmt={fmt}" if fmt else ""
            print(f"  [{tag}] {label} -> {opt_str}{extra}", file=sys.stderr)
        else:
            extra = f"  fmt={fmt}" if fmt else ""
            print(f"  [{tag}] {label}{extra}", file=sys.stderr)
    if unfilled and profile:
        pk = sorted(k for k in profile.keys() if k != "common_answers")
        if pk:
            print(f"  Profile keys: {json.dumps(pk)}", file=sys.stderr)
        # Surface profile.answers keys so the LLM knows what preferences exist
        # (willing_to_relocate, work_authorization, etc.) without reading profile.json
        pa = profile.get("answers", {})
        if pa:
            _answer_hints = {k: (str(v)[:30] + "..." if len(str(v)) > 30 else str(v))
                           for k, v in sorted(pa.items())}
            print(f"  Profile answers: {json.dumps(_answer_hints, indent=2)}", file=sys.stderr)


def emit_candidates(cands, max_show=8):
    print("CANDIDATES:", file=sys.stderr)
    for i, c in enumerate(cands[:max_show]):
        d = " [DISABLED]" if c.get("disabled") else ""
        print(f"  [{i}] '{c['text'][:40]}' score={c.get('score','?')}{d}", file=sys.stderr)


def emit_type(type_name, detail=None):
    if detail:
        print(f"TYPE: {type_name}\n{detail}", file=sys.stderr)
    else:
        print(f"TYPE: {type_name}", file=sys.stderr)
