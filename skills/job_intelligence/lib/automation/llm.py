"""llm — expectation-free option selection.

When a deterministic matcher is inconclusive, send the REAL option texts
+ the question + the answer to the local LLM and let it choose. No
aliases, no phrasing rules, no calibrated thresholds. The CALLER decides
how to verify the choice (mechanical identity is recommended).

Gated on ask_api availability; returns None when unavailable or the
model declines. Safe: it only ever returns one of the given options or
None — it cannot invent values.
"""
import re


def pick_option(opts, question, answer, max_options=30):
    """Choose the option matching the answer. Returns an option dict from
    `opts` (unchanged) or None."""
    if not opts:
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
