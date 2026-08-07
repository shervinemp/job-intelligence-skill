"""llm — expectation-free option selection + LLM invocation policy.

Two responsibilities:

1. pick_option — when a deterministic matcher is inconclusive, send the
   REAL option texts + the question + the answer to the local LLM and
   let it choose. No aliases, no phrasing rules, no calibrated
   thresholds. The CALLER decides how to verify the choice (mechanical
   identity is recommended).

2. policy — the contract for WHEN ask_api (the served local model) may
   be invoked at all. ROUTING HIERARCHY: the served local model is the
   WEAK model; the orchestrator — the LLM-in-the-middle — is the STRONG
   model and the intended operator of the whole pipeline:

     1. deterministic core (resolve → validate → fill → re-read verify
        → check) — source of truth, always first;
     2. ORCHESTRATOR — the semantic fallback of choice: no_match /
        no_option_match fields surface with FULL evidence (candidates,
        top_options, selection_readback) in dossiers, and the
        orchestrator answers them post-hoc via --answers, then re-fills.
        Stronger model; no per-field latency in the hot path;
     3. served local model (ask_api) — ONLY where the orchestrator
        physically cannot act: vision / image processing (the
        orchestrator has no page access), or a live-page interaction
        that cannot wait for the orchestrator round-trip;
     4. user handover — identity/legal/life decisions.

   So in auto mode: vision = ON; option_pick and gap_fill = OFF — the
   orchestrator's evidence loop replaces them (the weak model's guess is
   worse than the strong model's review of the same evidence).
   JI_LLM_MODE=on forces the local-model escapes (experiments only);
   off disables everything.
"""
import os
import re

_KIND_AUTO = {
    "vision": True,          # orchestrator cannot see the page
    "option_pick": False,    # orchestrator decides from top_options evidence
    "gap_fill": False,       # orchestrator decides from dossier evidence
    "batch_verify": False,   # LLM re-reviewing ALL fields lowers accuracy
    "verify_reads": False,   # deterministic re-read + check is the verifier
    "auto_retry": False,     # failures surface as evidence, never hidden
    "outreach": True,        # tone review: the orchestrator judges the
                             # message against the voice spec + thread reality
                             # before it leaves — no hardcoded phrase lists
    "outreach_compose": True,   # the orchestrator DRAFTS the outreach message
                             # from the evidence (thread/position/shared
                             # commonalities) + the style prompt, then tone
                             # review gates the send. Best judgment, automated.
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


# ─── Status instrumentation ───────────────────────────────────────────
# Every escape-hatch call records WHY it did or didn't use ask_api, so
# the orchestrator can distinguish policy from infrastructure from
# model behavior instead of a single opaque "no_match".

_LAST_STATUS = {"state": "unused", "detail": ""}


def set_last_status(state, detail=""):
    """Record the outcome of the last escape-hatch call."""
    _LAST_STATUS["state"] = state
    _LAST_STATUS["detail"] = detail


def last_status():
    """state ∈ unused | policy_off | api_down | declined | used."""
    return dict(_LAST_STATUS)


def pick_option(opts, question, answer, max_options=30):
    """Choose the option matching the answer. Returns an option dict from
    `opts` (unchanged) or None."""
    if not opts:
        return None
    if not allow("option_pick"):
        set_last_status("policy_off", "option_pick gated by JI_LLM_MODE")
        return None
    try:
        from lib.ask_api import available, ask_text
        if not available():
            set_last_status("api_down", "ask_api unavailable")
            return None
    except Exception as e:
        set_last_status("api_down", f"ask_api probe failed: {str(e)[:60]}")
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
            set_last_status("declined", str(err)[:60] if err else "empty reply")
            return None
        m = re.search(r"\d+", reply)
        if not m:
            set_last_status("declined", "unparseable reply")
            return None
        idx = int(m.group(0))
        if 0 <= idx < len(opts):
            set_last_status("used", f"picked idx {idx}")
            return opts[idx]
        set_last_status("declined", "index out of range")
    except Exception as e:
        set_last_status("declined", f"exception: {str(e)[:60]}")
        pass
    return None
