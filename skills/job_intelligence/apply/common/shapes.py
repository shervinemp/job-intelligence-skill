"""shapes.py — TypedDict schemas for the hot-path dicts.

The LLM (and the code) reads these shapes at a glance; ad-hoc dicts
with drift-prone keys are the root of misreads. These are runtime-
checked nowhere (Python) but are the contract every producer/consumer
annotates against — and the vocabulary in terms.py is their alphabet.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict


class Diag(TypedDict, total=False):
    """Fill-time evidence for one field (see terms.trunc for display)."""
    method: str            # combobox / radio / text / deterministic ...
    reason: str            # typed_match / no_option_match / still_empty ...
    before: str
    after: str
    unverified: bool
    typed_options: int
    options_seen: int
    candidates: List[str]
    top_options: List[Dict[str, Any]]
    selection_readback: List[str]
    llm_tried: bool
    llm_skipped: str       # "policy"
    llm_status: Dict[str, str]   # terms.LLM_STATUSES
    llm_reply: str


class FieldRecord(TypedDict, total=False):
    """One field in a dossier. kind ∈ terms.KINDS."""
    label: str             # display (may be truncated — see label_full)
    label_full: str        # THE identity — never truncated
    answer: str
    outcome: str           # filled / no_answer / failed
    kind: str              # terms.KINDS
    required: bool
    method: str
    reason: str
    selector: str
    selected_text: str
    diag: Diag
    options: List[str]
    tag: str
    type: str


class Blockers(TypedDict, total=False):
    type: str              # terms.STATUS_*
    domain: str
    needs: str
    next: str


class ApplyState(TypedDict, total=False):
    """Runtime cache ONLY — the dossier is the truth."""
    _role: str             # "runtime_cache"
    jid: str
    url: str
    external_url: str
    status: str            # terms.STATUS_*
    status_detail: str
    filled_count: int
    fill_answers: Dict[str, str]   # THE answer map (terms glossary)
    check_errors: List[Dict[str, Any]]
    check_warnings: List[Dict[str, Any]]
    check_infos: List[Dict[str, Any]]
    submit_clicked: bool
    remaining_fields: List[Dict[str, Any]]
    skipped_fields: List[Dict[str, Any]]
    browser_session_id: str


class Dossier(TypedDict, total=False):
    """The per-job truth (results/{jid}/handoff.json)."""
    jid: str
    url: str
    mode: str              # shadow / live
    ts: str                # ISO with T separator
    error: str
    llm_status: str        # terms.LLM_STATUSES
    llm_status_detail: str
    summary: Dict[str, int]      # terms.summarize() output
    fields: List[FieldRecord]
    blockers: List[Blockers]
    decisions: List[Dict[str, Any]]
    artifacts: Dict[str, str]
    run_id: str
