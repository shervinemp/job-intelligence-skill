"""llm_policy — app-side shim for lib/automation.llm policy core.

The deterministic code core (resolve → validate → fill → re-read verify
→ check) is the SOURCE OF TRUTH. ask_api is a SELECTIVE escape hatch:

    vision        — ALWAYS ok (image processing only)
    option_pick   — ok, last resort after deterministic no_option_match
    gap_fill      — ok, only for resolve()-declared no_match fields
    batch_verify  — OFF by default (LLM re-reviewing all fields lowers
                    accuracy; deterministic validation + check + the
                    orchestrator's dossier review is the verifier)
    verify_reads  — OFF by default (same reasoning)

The ORCHESTRATOR — the LLM-in-the-middle operating OUTSIDE the hot
path — is the verifier and debugger: it consumes the evidence trail
(dossiers, _diag dicts, session events, audit logs) and arbitrates.
JI_LLM_MODE=off | auto (default) | on.
"""
from lib.automation.llm import allow, mode  # noqa: F401
