"""gate.py — Submission gating decision (ADR-001 Phase 4).

Centralizes the "may we click submit?" decision so it is one pure, testable
function instead of scattered conditionals. Inputs: the effective mode, the policy
dict, and the job's audit summary. Output: an action + human-readable reason.

Actions:
  submit  — proceed to click submit (only in live mode, not paused, gate clear).
  hold    — fill is done but do not submit (shadow/hold mode, or gate tripped).
  blocked — kill-switch: policy.paused.

Defaults preserve prior behavior: with paused=false and gate_submit=false, live
mode always returns "submit".
"""


def submit_decision(mode, policy, audit_summary=None):
    """Return (action, reason)."""
    audit_summary = audit_summary or {}
    if policy.get("paused"):
        return "blocked", "policy paused (kill-switch)"
    if mode != "live":
        return "hold", f"{mode} mode — submit suppressed"
    if policy.get("gate_submit"):
        invalid = audit_summary.get("invalid", 0)
        if invalid:
            return "hold", f"{invalid} field(s) failed validation — review before submitting"
    return "submit", "ok"
