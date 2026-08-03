"""llm — expectation-free option selection + LLM invocation policy.

Two responsibilities:

1. pick_option — when a deterministic matcher is inconclusive, send the
   REAL option texts + the question + the answer to the local LLM and
   let it choose. No aliases, no phrasing rules, no calibrated
   thresholds. The CALLER decides how to verify the choice (mechanical
   identity is recommended).

2. policy — the contract for WHEN ask_api may be invoked at all.
   Architecture: the deterministic code core (resolve → validate →
   fill → re-read verify → check) is the SOURCE OF TRUTH. ask_api is a
   SELECTIVE escape hatch, never a default reviewer of that core:

     vision        — ALWAYS ok (image processing; deterministic code
                     cannot read pixels)
     option_pick   — ok, but ONLY as last resort after the deterministic
                     matcher found nothing (no_option_match)
     gap_fill      — ok, but ONLY for fields the deterministic resolver
                     declared no_match (code exhausted its vocabulary)
     batch_verify  — OFF by default: an LLM re-reviewing ALL field→value
                     pairs lowers accuracy vs the deterministic
                     validation + re-read + check arbitration
     verify_reads  — OFF by default: same reasoning — deterministic
                     re-read verification + check.py + the orchestrator
                     (human/agent review of dossiers) is the verifier
     auto_retry    — OFF by default: the PIPELINE never auto-retries
                     with LLM-mapped answers — that hides evidence and
                     guesses in the hot path. Failures surface in the
                     evidence trail; the ORCHESTRATOR retries from
                     reviewed evidence (--answers), not the pipeline.

   The orchestrator — the LLM-in-the-middle operating OUTSIDE the hot
   path — is the verifier and debugger: it consumes the evidence trail
   (dossiers, _diag dicts, session events, audit logs) and arbitrates,
   not an in-pipeline LLM call.

   Override: JI_LLM_MODE=off (never call ask_api), auto (above),
   on (allow everything — experimental).
"""
import os
import re

_KIND_AUTO = {
    "vision": True,
    "option_pick": True,
    "gap_fill": True,
    "batch_verify": False,
    "verify_reads": False,
    "auto_retry": False,
}


def mode():
    """off | auto | on — from env JI_LLM_MODE, default auto."""
    return os.environ.get("JI_LLM_MODE", "auto").strip().lower()


def allow(kind):
    """May the pipeline invoke ask_api for this kind of task?"""
    m = mode()
    if m == "off":
        return False
    if m == "on":
        return True
    return _KIND_AUTO.get(kind, False)


def pick_option(opts, question, answer, max_options=30):
    """Choose the option matching the answer. Returns an option dict from
    `opts` (unchanged) or None."""
    if not opts:
        return None
    if not allow("option_pick"):
        return None
    try:
        from lib.ask_api import available, ask_text
        if not available():
            return None
    except Exception:
        return None
    try:
        lines = [
            f"Form field: {(question or '')[:120]}",
            f"Answer: {str(answer)[:200]}",
            "Options:",
        ]
        for i, o in enumerate(opts[:max_options]):
            lines.append(f"[{i}] {str(o.get('text', ''))[:80]}")
        lines.append(
            "Choose the option index that matches the answer. "
            "Reply with ONLY the index number. If none match, reply NONE.")
        reply, err = ask_text("\n".join(lines), temperature=0.1, max_tokens=16)
        if err or not reply:
            return None
        m = re.search(r"\d+", reply)
        if not m:
            return None
        idx = int(m.group(0))
        if 0 <= idx < len(opts):
            return opts[idx]
    except Exception:
        pass
    return None
