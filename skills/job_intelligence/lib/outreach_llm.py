"""lib/outreach_llm.py — the ORCHESTRATOR composes outreach, code gathers evidence.

Routing (matches lib/automation/llm.py): the served local model (ask_api) is
the WEAK model; the orchestrator — the strong model that operates the pipeline
— is the intended decision-maker. Outreach tone is a judgment call, exactly
the kind of thing the orchestrator should write, not a calibrated rule.

Two seams, two roles (see the policy kinds in lib/automation/llm.py):

  compose (kind: outreach_compose, OFF in auto):
    Code gathers the EVIDENCE (real inbox thread history via
    linkedin_messaging.thread_status, job/contact data, the voice spec,
    resume availability) and hands it to the orchestrator as a drafted
    message + context. The orchestrator adjusts it (continuation vs
    follow-up vs cold open, relationship-aware, last-message-aware) and
    returns the final text. The weak model does NOT draft outreach — compose
    returns the evidence bundle and the operator writes/approves.

  tone_review (kind: outreach, ON in auto):
    The guardrail BEFORE every send. The LLM judges the final message against
    the voice spec + the thread reality and returns a PASS/FAIL verdict. A
    FAIL blocks the transmission (unless --force). This REPLACES the old
    hardcoded phrase lists — no calibrated rules that drift and
    false-positive.

Both keep the "never invent a relationship" rule: any relationship line must
trace to the evidence (thread exists, notes say suggested the job). The
prompts hard-forbid inventing history the evidence does not support.
"""

_ORCHESTRATOR_BRIEF = """You are composing a LinkedIn outreach message. Below is the EVIDENCE the pipeline gathered — the thread history, the job, the contact, the resume — and the VOICE SPEC from the template.

Write the message that a thoughtful human would send in this exact situation. Rules:
- NEVER invent a relationship, a prior conversation, or a shared connection that the evidence does not show. If there is no thread history, it is a cold open. If the thread shows a prior message from you that went unanswered, it is a follow-up — acknowledge it ("circling back on my earlier message") without guilt or apology spirals.
- Lead with what is real: a referral/suggestion if the contact notes say so, a prior thread if one exists, the application itself otherwise.
- ONE soft ask at the end. Short (a DM: under ~120 words). Warm, human, specific — never a cover letter.
- If the evidence says the last message was YOURS very recently (hours), do not send a near-identical repeat; either stand down or make the new message materially different (new info, attached resume, a specific question).

EVIDENCE:
{evidence}

VOICE SPEC:
{voice_spec}

TEMPLATE (the register to match, not to copy verbatim):
{template}

Reply with ONLY the message text (no preamble, no quotes, no markdown)."""


_TONE_REVIEW_BRIEF = """You are reviewing ONE outreach message before it is sent to a real person. Judge it against the voice spec and the thread reality.

Judge for:
1. Does it sound like a warm, specific human — not a form letter or a resume dump?
2. Is it accurate to the THREAD? (Cold open if no thread; a follow-up if our last message went unanswered; a continuation if they replied. Never write as if the relationship is new when the evidence says otherwise.)
3. Does a mention of the attached resume add specific role/recipient value, or is it empty filler ("for your reference", "attaching my resume")?
4. ONE soft ask, not two. Short enough for the channel.
5. No invented relationship the evidence does not support.

Reply in EXACTLY this format (nothing else):
VERDICT: PASS or FAIL
NOTES: <one line per issue, or "none">

MESSAGE:
{message}

THREAD EVIDENCE:
{evidence}

VOICE SPEC:
{voice_spec}"""


def tone_review(message, thread=None, voice_spec="", channel="message",
                contact=None, job=None):
    """LLM tone review of a message about to be sent. This is the ORCHESTRATOR
    review — no hardcoded phrase lists (the calibrating rules that drift).
    Returns (ok, notes, detail):
      ok=False, notes=[...]  → the LLM flagged real issues; block the send.
      ok=True                → the LLM judged it fine.
      detail explains when no review could run (policy/infra) so the caller
      can decide fail-open vs fail-closed.
    """
    try:
        from lib.automation.llm import allow
        if not allow("outreach"):
            return None, [], ("orchestrator decision (outreach tone review "
                              "gated to the operator in auto mode)")
    except Exception as e:
        return None, [], f"policy check failed: {str(e)[:60]}"
    try:
        from lib.ask_api import available, ask_text
        if not available():
            return None, [], "ask_api unavailable"
        evidence = build_evidence(contact or {}, job or {},
                                  thread=thread, channel=channel)
        prompt = _TONE_REVIEW_BRIEF.format(
            message=(message or "").strip(),
            evidence=evidence,
            voice_spec=voice_spec or "(no voice spec provided)",
        )
        reply, err = ask_text(prompt, temperature=0.2, max_tokens=300,
                              timeout=30)
        if err or not reply:
            return None, [], f"ask_api declined: {str(err or 'empty reply')[:80]}"
        import re as _re
        _vm = _re.search(r"\bVERDICT\s*:\s*(PASS|FAIL)\b", reply or "",
                         flags=_re.IGNORECASE)
        verdict = (_vm.group(1).upper() if _vm else "PASS")
        notes = []
        for line in (reply or "").splitlines():
            s = line.strip()
            if s.upper().startswith("NOTES:") or s.lower().startswith("note "):
                rest = s.split(":", 1)[1].strip() if ":" in s else s
                if rest and rest.lower() not in ("none", "no issues", "clean"):
                    notes.append(rest[:200])
            elif s.startswith("-") and s.strip("- ").strip():
                notes.append(s.strip("- ").strip()[:200])
        if verdict == "FAIL":
            return False, notes, "llm review"
        return True, notes, "llm review"
    except Exception as e:
        return None, [], f"ask_api exception: {str(e)[:80]}"


def build_evidence(contact, job, thread=None, resume_pdf=None, channel="message"):
    """Package the outreach evidence into a compact, deterministic string the
    composer prompt can consume. All claims must trace to real data — this is
    the anti-hallucination boundary for relationship lines."""
    lines = []
    job_c = (job or {}).get("company", "") or ""
    job_t = (job or {}).get("title", "") or ""
    name = (contact or {}).get("name", "") or ""
    notes = (contact or {}).get("notes", "") or ""
    lines.append(f"Contact: {name}")
    lines.append(f"Job: {job_t} at {job_c}")
    if notes:
        lines.append(f"Contact notes: {notes[:200]}")
    else:
        lines.append("Contact notes: (none)")
    if thread:
        lines.append(f"Existing thread: {'YES' if thread.get('exists') else 'NO'}")
        if thread.get("exists"):
            lines.append(f"  last message: {thread.get('last_message_time') or 'unknown'}")
            lines.append(f"  direction: {'you sent it' if thread.get('last_message_direction') == 'out' else 'they replied'}")
            lines.append(f"  preview: {thread.get('preview') or '(none)'}")
    elif thread is None:
        lines.append("Existing thread: UNKNOWN (inbox not checked)")
    if resume_pdf:
        lines.append(f"Resume: attached ({resume_pdf})")
    else:
        lines.append("Resume: NOT available")
    lines.append(f"Channel: {channel}")
    return "\n".join(lines)


def compose(contact, job, thread=None, resume_pdf=None, channel="message",
            voice_spec="", template=""):
    """Compose the outreach message.

    Returns (body, detail). When no orchestrator is in the loop and the weak
    model is allowed (JI_LLM_MODE=on), drafts via ask_text. Otherwise returns
    (None, reason) and the caller uses its own template fallback — the
    orchestrator (operator) writes the message with the evidence in view.
    """
    evidence = build_evidence(contact, job, thread=thread,
                              resume_pdf=resume_pdf, channel=channel)
    try:
        from lib.automation.llm import allow
        if not allow("outreach_compose"):
            return None, ("orchestrator decision (outreach compose gated to "
                          "the operator in auto mode)")
    except Exception:
        pass
    try:
        from lib.ask_api import available, ask_text
        if not available():
            return None, "ask_api unavailable"
        prompt = _ORCHESTRATOR_BRIEF.format(
            evidence=evidence,
            voice_spec=voice_spec or "(no voice spec provided)",
            template=template or "(no template provided)",
        )
        reply, err = ask_text(prompt, temperature=0.7, max_tokens=400, timeout=30)
        if err or not reply:
            return None, f"ask_api declined: {str(err or 'empty reply')[:80]}"
        body = reply.strip()
        # Strip accidental quotes/markdown fences the model may add.
        body = body.strip("`").strip()
        body = body.strip()
        if body.startswith('"') and body.endswith('"'):
            body = body[1:-1].strip()
        return body, "ask_api draft"
    except Exception as e:
        return None, f"ask_api exception: {str(e)[:80]}"
