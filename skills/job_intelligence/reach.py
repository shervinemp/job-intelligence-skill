#!/usr/bin/env python3
"""reach.py — Contact discovery and outreach CLI.

Usage:
  reach.py discover <jid> [--team <name>] [--no-llm] [--no-browser]
  reach.py list <jid>                        Show discovered contacts for a job
  reach.py draft <jid> [--contact N] [--channel message|email]   Draft from template
  reach.py email <jid> [--contact N] [--dry-run] [--force] [--body <text>] [--body-file <path>]
  reach.py message <jid> [--contact N] [--dry-run] [--force] [--body <text>] [--body-file <path>]
  reach.py connect <jid> [--contact N] [--note <text>]
  reach.py update <jid> [--contact N] [--email <addr>] [--note <text>] [--set-sent email|message]
  reach.py threads <jid> [--backfill]       Reconcile contacts against the REAL LinkedIn inbox
  reach.py attempts [<jid>]                 Show outreach attempts
  reach.py retry <jid>                       Retry failed contact discovery
  reach.py undo <jid> [--confirm]            Reset contact state (--confirm when already contacted)

One-shot: email/message are guarded by their own sent flags, connect by a
prior-invitation check, and ALL channels by a cross-job person guard that
matches on canonical LinkedIn vanity / email. --force overrides; undo
discards the evidence and needs --confirm.
"""

import json
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from lib.contacts import discover_contacts
from lib.db import get_conn, get_job
from lib.db.contacts import (
    contact_list, contact_update, attempt_list,
)
from lib.linkedin_messaging import (
    send_message,
    send_connect_request,
)

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
GMAIL_CLI = os.path.join(SKILL_DIR, "..", "gmail-cli", "gmail_cli.py")


def _sandbox_refused():
    """True (and explains) when the transmission sandbox forbids sending."""
    if os.environ.get("JI_TESTS"):
        print("TEST_SANDBOX: transmission refused under the test runner — "
              "remove JI_TESTS from the environment to send.", file=sys.stderr)
        return True
    return False


def _job_resume_pdf(jid):
    """The tailored resume PDF for a job, if one exists on disk.

    Outreach emails should attach the SAME resume the apply pipeline sent —
    the per-job tailored artifact, not the generic profile. Returns None
    when the job has no built PDF (the caller then sends with no attachment).
    """
    import glob
    try:
        from lib.config import RESULTS_DIR
        rd = os.path.join(RESULTS_DIR, str(jid))
        if not os.path.isdir(rd):
            return None
        pdfs = sorted(glob.glob(os.path.join(rd, "*_Resume.pdf")))
        return pdfs[0] if pdfs else None
    except Exception:
        return None


def person_keys(contact):
    """Canonical identity keys for a person: {('li', vanity), ('em', addr)}.

    Identity must survive cosmetic variation — a trailing slash, a
    ?miniProfileUrn= tracking param, http vs https, www, or case would
    otherwise let the same human be contacted twice. EMPTY values never
    produce a key: an absent linkedin_url is not an identity, and treating
    it as one made every URL-less contact match every other URL-less
    contact (a guard that blocked strangers while missing real repeats).
    """
    keys = set()
    url = (contact.get("linkedin_url") or "").strip()
    if url:
        from urllib.parse import urlparse
        path = urlparse(url).path if "//" in url else url
        # /in/<vanity>/... -> vanity; anything else -> the cleaned path
        parts = [p for p in path.strip("/").split("/") if p]
        if parts:
            vanity = parts[1] if (parts[0].lower() == "in" and len(parts) > 1) else parts[-1]
            vanity = vanity.split("?")[0].strip().lower()
            if vanity:
                keys.add(("li", vanity))
    email = (contact.get("email") or "").strip().lower()
    if email:
        keys.add(("em", email))
    return keys


def _prior_outreach(conn, contact):
    """Any sent/pending outreach to the SAME PERSON on any OTHER contact row.

    One-shot guards are per contact row, so the same person discovered on
    two jobs (or twice within a job) gets two rows and nothing blocks the
    second message — that repeat would give away the automation. Counts
    both flag-based sends and attempts (covers the 'uncertain' path, which
    never sets reached_out). The current row itself is excluded so a
    connect -> DM funnel on one row keeps working.

    Identity is compared on canonical keys (see person_keys), not raw
    string equality, so URL variants of one profile still collapse to one
    person and blank fields identify nobody.
    """
    mine = person_keys(contact)
    if not mine:
        return None
    rows = conn.execute(
        "SELECT c.id, c.job_id, c.name, c.linkedin_url, c.email, "
        "       a.channel, a.status, a.created_at "
        "FROM contacts c LEFT JOIN contact_attempts a ON a.contact_id = c.id "
        "WHERE c.id != ? "
        "AND ((c.linkedin_url IS NOT NULL AND c.linkedin_url != '') "
        "     OR (c.email IS NOT NULL AND c.email != '')) "
        "AND (c.reached_out = 1 OR a.status IN ('sent', 'pending', 'backfilled')) "
        "ORDER BY a.created_at DESC",
        (contact["id"],),
    ).fetchall()
    for r in rows:
        if mine & person_keys(dict(r)):
            return r
    return None


def _voice_line(channel):
    """Compact tone anchor printed at dry-run time (polish #5) so the
    register is in view at the moment of review, not just in the template."""
    return {
        "message": "VOICE: short, warm, relationship-first, ONE soft ask — see templates/linkedin_message.md",
        "email": "VOICE: friendly, specific, respectful — see templates/email_recruiter.md",
    }.get(channel, "")


def _preflight_send(body, channel, contact=None, job=None, force=False):
    """Gate the ACTUAL transmission with an LLM tone review — no hardcoded
    phrase lists.

    The orchestrator (LLM) judges the message against the voice spec AND the
    real thread evidence. A FAIL verdict blocks the send unless --force.

    If no review could run (ask_api down, or the policy forbids it), the
    gate FAILS OPEN with a note — the message was composed/approved by the
    orchestrator already, and a silent hard block would strand the send. The
    review is a guardrail, not a bottleneck.
    Returns True when the send may proceed."""
    from lib import outreach_llm
    from lib.config import TEMPLATES_DIR
    voice_spec = ""
    tpl_name = ("linkedin_message.md" if channel in ("message", "linkedin")
                else "email_recruiter.md")
    try:
        with open(os.path.join(TEMPLATES_DIR, tpl_name), "r",
                  encoding="utf-8") as f:
            _t = f.read()
        if "VOICE SPEC" in _t:
            voice_spec = _t.split("VOICE SPEC", 1)[1].strip()
    except Exception:
        pass
    ok, notes, detail = outreach_llm.tone_review(
        body, contact=contact or {}, job=job or {}, voice_spec=voice_spec,
        channel=channel)
    if ok is False and notes and not force:
        print(f"TONE_BLOCK: refusing to send — orchestrator review found:",
              file=sys.stderr)
        for n in notes:
            print(f"    - {n}", file=sys.stderr)
        print(f"  Fix the message, or re-run with --force if reviewed.",
              file=sys.stderr)
        return False
    if ok is False and notes and force:
        print(f"TONE_OVERRIDE: {len(notes)} note(s) reviewed and overridden "
              f"with --force:", file=sys.stderr)
        for n in notes:
            print(f"    - {n}", file=sys.stderr)
    elif ok is True and notes:
        print(f"TONE: reviewed — {len(notes)} note(s):", file=sys.stderr)
        for n in notes:
            print(f"    - {n}", file=sys.stderr)
    if ok is None:
        print(f"TONE_SKIP: {detail}", file=sys.stderr)
    return True


def _default_message_body(name, title, company):
    """Warm, human LinkedIn DM default — the same register as the curated
    templates/linkedin_message.md. Short, relationship-first, one soft ask."""
    return (
        f"Hi {name},\n\n"
        f"I saw {title} is open at {company} and just applied — it looks "
        f"like a great fit for what I do. Would you be open to a quick chat "
        f"sometime soon?\n\n"
        f"Thanks,\nShervin"
    )


def _template_vars(name, title, company, contact=None, job=None):
    """The {variable} fill set for outreach templates. Relationship signals
    from the contact's notes (a referral, a mutual connection, a suggestion)
    are surfaced here so the orchestrator can lead with them. years_of_
    experience and relevant_skills are derived from the profile, not hardcoded."""
    from lib.config import PROFILE_PATH
    profile = {}
    try:
        with open(PROFILE_PATH, encoding="utf-8") as f:
            profile = json.load(f) or {}
    except Exception:
        pass

    # Derive years of experience from the earliest work-history start date.
    years_exp = ""
    try:
        starts = [w.get("startDate") for w in (profile.get("work_history") or [])
                  if w.get("startDate")]
        if starts:
            import re as _re
            yr = min(int(_re.match(r"(\d{4})", s).group(1)) for s in starts
                     if _re.match(r"(\d{4})", s))
            years_exp = str(max(0, 2026 - yr))
    except Exception:
        pass

    skills = ""
    try:
        sk = profile.get("skills") or []
        if isinstance(sk, list):
            skills = ", ".join(str(s) for s in sk[:3])
        elif isinstance(sk, str):
            skills = sk[:60]
    except Exception:
        pass
    if not skills:
        skills = "machine learning"

    vars = {
        "contact_name": name or "",
        "job_title": title or "",
        "company": company or "",
        "my_name": "Shervin",
        "years_of_experience": years_exp,
        "relevant_skills": skills,
        "team_name": "",
    }
    if contact:
        notes = (contact.get("notes") or "")
        if notes:
            vars["suggestion_reason"] = notes[:200]
    return vars


def cmd_draft(jid, contact_idx=1, channel="message"):
    """Load the outreach template, fill {variables} from the job + contact,
    reconcile against the REAL LinkedIn inbox (thread history), and print the
    draft + evidence for the orchestrator to review. No send — the
    orchestrator writes the final message with the evidence in view."""
    contacts = contact_list(job_id=jid)
    if not contacts:
        print(f"No contacts for {jid}. Run 'reach.py discover {jid}' first.", file=sys.stderr)
        return 1
    if contact_idx < 1 or contact_idx > len(contacts):
        print(f"Invalid contact index {contact_idx}. Valid range: 1-{len(contacts)}", file=sys.stderr)
        return 1

    contact = contacts[contact_idx - 1]
    name = contact.get("name", "")
    job = get_job(jid)
    company = job.get("company", "") if job else ""
    title = job.get("title", "") if job else ""

    from lib.config import TEMPLATES_DIR
    tpl_name = "linkedin_message.md" if channel in ("message", "linkedin") else "email_recruiter.md"
    tpl_path = os.path.join(TEMPLATES_DIR, tpl_name)
    if not os.path.exists(tpl_path):
        print(f"TEMPLATE_NOT_FOUND: {tpl_path}", file=sys.stderr)
        return 1
    with open(tpl_path, "r", encoding="utf-8") as f:
        template = f.read()

    # Strip the trailing VOICE SPEC (orchestrator guidance) — it is not the
    # message text. It stays in the file as the drafting anchor.
    voice_spec = ""
    marker = "---\nVOICE SPEC"
    if marker in template:
        head, _, spec = template.partition(marker)
        body = head.strip()
        voice_spec = spec.strip()
    else:
        body = template.strip()

    for k, v in _template_vars(name, title, company, contact=contact, job=job).items():
        body = body.replace("{" + k + "}", v or "")
    # Drop any placeholder the data didn't cover (shouldn't happen; guards
    # against a literal {var} leaking into a send).
    import re as _re
    body = _re.sub(r"\{[a-z_]+\}", "", body)

    # Reconcile against the real inbox — the pipeline's ledger is NOT the
    # history of the account. thread_status reads LinkedIn itself.
    from lib import linkedin_messaging as _lm
    from lib.outreach_llm import build_evidence
    thread = None
    try:
        from lib.chrome_manager import connect as _connect
        b, ctx = _connect(timeout=30)
        if ctx:
            thread = _lm.thread_status(ctx, name)
            try:
                b.close()
            except Exception:
                pass
    except Exception as _e:
        thread = None  # unchecked — the evidence line marks it UNKNOWN

    resume_pdf = _job_resume_pdf(jid)
    evidence = build_evidence(contact, job, thread=thread,
                              resume_pdf=resume_pdf, channel=channel)

    print(f"DRAFT ({channel}): {name} @ {company}", file=sys.stderr)
    print(f"  From template: {tpl_name}", file=sys.stderr)
    print(f"  Voice spec at: {tpl_path} (review it — it is the tone anchor)", file=sys.stderr)
    if thread is not None:
        print(f"  THREAD: exists={thread.get('exists')}", file=sys.stderr)
        if thread.get("exists"):
            print(f"    last message {thread.get('last_message_time') or '?'} — "
                  f"{'you sent' if thread.get('last_message_direction') == 'out' else 'they replied'}", file=sys.stderr)
            print(f"    preview: {thread.get('preview') or '(none)'}", file=sys.stderr)
    else:
        print(f"  THREAD: UNKNOWN (inbox not reachable — the ledger is NOT "
              f"the history)", file=sys.stderr)
    if resume_pdf:
        print(f"  Attach: {os.path.basename(resume_pdf)} (tailored resume)", file=sys.stderr)
    else:
        print(f"  Attach: NONE — no tailored resume PDF for {jid}", file=sys.stderr)
    print(body)
    print(f"\nORCHESTRATOR BRIEF:", file=sys.stderr)
    print(evidence, file=sys.stderr)
    print(f"\nNEXT: adjust the draft for the evidence above (prior thread, "
          f"relationship, resume), then reach.py {channel} {jid} "
          f"--contact {contact_idx} --body-file <this> --dry-run",
          file=sys.stderr)
    return 0


def _block_if_prior(conn, contact, force):
    """Cross-job/duplicate-row one-shot gate shared by email/message/connect."""
    # CURVEBALL C7: a person with NO identity key (no linkedin_url, no email)
    # cannot be compared to prior contacts — the guard would silently let a
    # name-only person be contacted twice. Surface that gap at the moment of
    # send so the operator knows this send is unprotected, rather than
    # silently proceeding as if the one-shot guard applied.
    if not person_keys(contact):
        print(f"BLANK_IDENTITY: {contact.get('name','')} has no email or "
              f"LinkedIn URL — the cross-job one-shot guard CANNOT verify "
              f"this person was never contacted on another job. Proceeding "
              f"unverified.", file=sys.stderr)
        return False
    prior = _prior_outreach(conn, contact)
    if prior and not force:
        print(f"ALREADY_REACHED: {contact.get('name','')} was already contacted "
              f"for job {prior['job_id'][:8]} "
              f"({prior['channel'] or '?'}/{prior['status'] or 'sent'}) — "
              f"one-shot guard. Use --force to override.",
              file=sys.stderr)
        return True
    return False


# ---------------------------------------------------------------------------
# Discover
# ---------------------------------------------------------------------------

def cmd_discover(jid, team_name=None, use_llm=True, use_browser=True):
    result = discover_contacts(jid, team_name=team_name, use_llm=use_llm, use_browser=use_browser)
    if "error" in result:
        print(f"ERROR: {result['error']}", file=sys.stderr)
        return

    total = len(result["recruiters"]) + len(result["team_members"]) + len(result["my_connections"])
    print(f"CONTACTS: {total} total", file=sys.stderr)
    print(f"  Recruiters: {len(result['recruiters'])}", file=sys.stderr)
    print(f"  Team members: {len(result['team_members'])}", file=sys.stderr)
    print(f"  My connections: {len(result['my_connections'])}", file=sys.stderr)
    if result.get("company_linkedin_slug"):
        print(f"  Company slug: {result['company_linkedin_slug']}", file=sys.stderr)

    # Print in DB order (created_at) so indices match `--contact N` selection.
    db_contacts = contact_list(job_id=jid)
    for i, c in enumerate(db_contacts, 1):
        source = c.get("source", "")
        label = {"recruiter_auto": "recruiter", "team_search": "team", "my_connection": "my_connection"}.get(source, source or "unknown")
        print(f"CONTACT: {i} | {c.get('name','')} | {label} | {c.get('role','')} | {c.get('linkedin_url','')}", file=sys.stderr)

    if result.get("email_candidates"):
        print(f"\nSuggested email patterns:", file=sys.stderr)
        for ec in result["email_candidates"]:
            emails = ", ".join(ec.get("suggested_emails", []))
            print(f"  {ec.get('name','')}: {emails} (confidence: {ec.get('confidence', 0)})", file=sys.stderr)

    if total > 0:
        print(f"\nNEXT: reach.py email {jid} --contact N   OR   reach.py message {jid} --contact N", file=sys.stderr)
    else:
        print(f"NEXT: No contacts found. Try 'reach.py discover {jid} --team <name>' with a team name.", file=sys.stderr)


def cmd_discover_all(use_llm=True, use_browser=True, limit=None):
    """Discover contacts for all active described/tailored jobs missing contacts."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, title, company FROM jobs "
        "WHERE stage IN ('described','tailored') AND state='active' AND contact_discovered=0 "
        "ORDER BY created_at"
    ).fetchall()
    if limit:
        rows = rows[:limit]
    if not rows:
        print("NO_JOBS_TO_DISCOVER", file=sys.stderr)
        return

    print(f"DISCOVERING: {len(rows)} jobs", file=sys.stderr)
    for r in rows:
        print(f"\nJOB {r['id']} | {r['title'][:40] or '?'} @ {r['company'][:25] or '?'}", file=sys.stderr)
        cmd_discover(r["id"], use_llm=use_llm, use_browser=use_browser)


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------

def cmd_list(jid):
    contacts = contact_list(job_id=jid)
    if not contacts:
        print(f"No contacts for {jid}. Run 'reach.py discover {jid}' first.", file=sys.stderr)
        return

    print(f"Contacts for {jid}:", file=sys.stderr)
    for i, c in enumerate(contacts, 1):
        sent_indicators = []
        if c.get("email_sent"):
            sent_indicators.append("email")
        if c.get("message_sent"):
            sent_indicators.append("DM")
        sent_str = f" [sent: {','.join(sent_indicators)}]" if sent_indicators else ""
        print(f"  {i}. {c.get('name','')} | {c.get('role','')} | {c.get('source','')} | {c.get('email','') or c.get('linkedin_url','')}{sent_str}", file=sys.stderr)
        print(f"     Confidence: {c.get('confidence', 0)} | Reached out: {c.get('reached_out', 0)}", file=sys.stderr)

    print(f"\nNEXT: reach.py email {jid} --contact N   OR   reach.py message {jid} --contact N", file=sys.stderr)


# ---------------------------------------------------------------------------
# Threads / backfill
# ---------------------------------------------------------------------------

def cmd_threads(jid, backfill=False):
    """Reconcile each contact of a job against the REAL LinkedIn inbox.

    The ledger records only what the PIPELINE sent. A person messaged
    manually (or from an earlier unrecorded run) leaves no DB trace — the
    inbox is the authoritative history. `threads` surfaces that; `--backfill`
    additionally records a `backfilled` attempt row per existing thread so
    the one-shot guards and cross-job dedup see the truth."""
    contacts = contact_list(job_id=jid)
    if not contacts:
        print(f"No contacts for {jid}. Run 'reach.py discover {jid}' first.", file=sys.stderr)
        return 1
    try:
        from lib.chrome_manager import connect
        b, ctx = connect(timeout=30)
        if not ctx:
            print("THREADS: no Chrome context — cannot read the inbox",
                  file=sys.stderr)
            return 1
    except Exception as e:
        print(f"THREADS: no Chrome context — cannot read the inbox ({str(e)[:80]})",
              file=sys.stderr)
        return 1

    from lib import linkedin_messaging as _lm
    conn = get_conn()
    found = 0
    try:
        print(f"THREADS: {jid} — reconciling {len(contacts)} contacts against "
              f"the real inbox...", file=sys.stderr)
        for i, c in enumerate(contacts, 1):
            name = c.get("name", "")
            t = _lm.thread_status(ctx, name)
            if t.get("exists"):
                found += 1
                print(f"  {i}. {name} — THREAD EXISTS, last "
                      f"{t.get('last_message_time') or '?'} "
                      f"({'you sent' if t.get('last_message_direction') == 'out' else 'they replied'})",
                      file=sys.stderr)
                print(f"     preview: {t.get('preview') or '(none)'}", file=sys.stderr)
                if backfill:
                    prior = conn.execute(
                        "SELECT 1 FROM contact_attempts WHERE contact_id=? "
                        "AND status='backfilled' LIMIT 1", (c["id"],)).fetchone()
                    if not prior:
                        conn.execute(
                            "INSERT INTO contact_attempts (contact_id, channel, "
                            "direction, subject, body, status, message_id, "
                            "error, sent_at) "
                            "VALUES (?,?,?,?,?,?,?,?,NULL)",
                            (c["id"], "linkedin_message", "outbound",
                             "reconciled from LinkedIn inbox",
                             f"last {t.get('last_message_time') or '?'} — "
                             f"{t.get('preview') or ''}"[:400],
                             "backfilled", "", ""))
                        conn.commit()
                        print(f"     -> backfilled attempt row", file=sys.stderr)
            else:
                mark = "UNKNOWN (inbox not read)" if not t.get("checked") else "no thread"
                print(f"  {i}. {name} — {mark}", file=sys.stderr)
        print(f"THREADS: done — {found}/{len(contacts)} contacts have an "
              f"existing thread", file=sys.stderr)
        if found and not backfill:
            print(f"  Re-run with --backfill to record these in the ledger so "
                  f"the one-shot guards see them.", file=sys.stderr)
        return 0
    finally:
        try:
            b.close()
        except Exception:
            pass

def cmd_email(jid, contact_idx=1, dry_run=False, body=None, body_file=None, force=False):
    contacts = contact_list(job_id=jid)
    if not contacts:
        print(f"No contacts for {jid}. Run 'reach.py discover {jid}' first.", file=sys.stderr)
        return

    if contact_idx < 1 or contact_idx > len(contacts):
        print(f"Invalid contact index {contact_idx}. Valid range: 1-{len(contacts)}", file=sys.stderr)
        return

    contact = contacts[contact_idx - 1]
    name = contact.get("name", "")
    email_addr = contact.get("email", "")
    conn = get_conn()

    if not email_addr:
        # Check if there's an email candidate in notes
        notes = contact.get("notes", "")
        print(f"No email for {name}.", file=sys.stderr)
        print(f"  Notes: {notes}", file=sys.stderr)
        print(f"NEXT: reach.py update {jid} --contact {contact_idx} --email <addr>  OR  use LinkedIn DM instead", file=sys.stderr)
        return

    if contact.get("email_sent") and not force:
        print(f"Already emailed {name}. Use --force to re-send.", file=sys.stderr)
        return

    # Symmetric with cmd_message: an unconfirmed previous send must not be
    # silently repeated on this channel either.
    if not force:
        pending = conn.execute(
            "SELECT 1 FROM contact_attempts WHERE contact_id=? AND channel='email' "
            "AND status='pending' LIMIT 1", (contact["id"],),
        ).fetchone()
        if pending:
            print(f"UNCERTAIN_SEND: {name} has an unconfirmed previous email — "
                  f"check the Sent folder, then "
                  f"reach.py update {jid} --contact {contact_idx} --set-sent email, "
                  f"or re-send with --force.", file=sys.stderr)
            return

    if _block_if_prior(conn, contact, force):
        return

    # Get job info for subject
    job = get_job(jid)
    company = job.get("company", "") if job else ""
    title = job.get("title", "") if job else ""

    subject = f"Regarding {title} at {company}" if title and company else f"Inquiry regarding {company}"

    if body:
        body_text = body
    elif body_file:
        try:
            with open(body_file, "r", encoding="utf-8") as f:
                body_text = f.read()
        except FileNotFoundError:
            print(f"Body file not found: {body_file}", file=sys.stderr)
            return
    else:
        body_text = (
            f"Hi {name},\n\n"
            f"I recently came across the {title} role at {company} and "
            f"wanted to reach out to learn more about the position and the team.\n\n"
            f"Thanks,\nShervin"
        )
        print(f"DEFAULT_BODY: used template — provide --body or --body-file for custom message", file=sys.stderr)

    if dry_run:
        resume_pdf = _job_resume_pdf(jid)
        print(f"DRY_RUN: Would email {name} at {email_addr}", file=sys.stderr)
        print(f"  Subject: {subject}", file=sys.stderr)
        print(f"  {_voice_line('email')}", file=sys.stderr)
        if resume_pdf:
            print(f"  Attach: {os.path.basename(resume_pdf)} (tailored resume)",
                  file=sys.stderr)
        else:
            print(f"  Attach: NONE — no tailored resume PDF for {jid} "
                  f"(apply.py produces it during tailoring)",
                  file=sys.stderr)
        print(f"  Body:\n{body_text}\n", file=sys.stderr)
        print(f"NEXT: reach.py email {jid} --contact {contact_idx}  (remove --dry-run to send)", file=sys.stderr)
        return

    if _sandbox_refused():
        return

    if not _preflight_send(body_text, "email", contact=contact, job=job,
                           force=force):
        return

    # Send via gmail-cli
    cmd = [
        sys.executable, GMAIL_CLI, "send", email_addr, subject,
        "--body", body_text, "--json",
    ]
    resume_pdf = _job_resume_pdf(jid)
    if resume_pdf:
        cmd += ["--attach", resume_pdf]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=60)
        output = r.stdout.decode("utf-8", errors="replace")
        err = r.stderr.decode("utf-8", errors="replace")
        if r.returncode != 0:
            from lib.outreach_ledger import record_outcome
            record_outcome(conn, "email", contact["id"], jid, "failed",
                           subject=subject, body=body_text, error=err[:200])
            print(f"EMAIL_FAILED: {err[:200]}", file=sys.stderr)
            return

        try:
            parsed = json.loads(output)
            if parsed.get("status") == "sent":
                msg_id = parsed.get("message_id", "")
                from lib.outreach_ledger import record_outcome
                record_outcome(conn, "email", contact["id"], jid, "sent",
                               subject=subject, body=body_text,
                               message_id=msg_id,
                               event_type="email",
                               event_title=f"Emailed {name} about {title}",
                               event_desc=f"Sent to {email_addr}, msg_id={msg_id}")
                print(f"EMAIL_SENT: {msg_id} to {email_addr}", file=sys.stderr)
                print(f"  NEXT: Wait for response or follow up", file=sys.stderr)
            else:
                from lib.outreach_ledger import record_outcome
                record_outcome(conn, "email", contact["id"], jid, "failed",
                               subject=subject, body=body_text,
                               error=parsed.get('error', 'unknown'))
                print(f"EMAIL_FAILED: {parsed.get('error', 'unknown')}", file=sys.stderr)
        except json.JSONDecodeError:
            from lib.outreach_ledger import record_outcome
            record_outcome(conn, "email", contact["id"], jid, "failed",
                           subject=subject, body=body_text,
                           error=f"unexpected response: {output[:200]}")
            print(f"EMAIL_FAILED: unexpected response: {output[:200]}", file=sys.stderr)
    except subprocess.TimeoutExpired:
        from lib.outreach_ledger import record_outcome
        record_outcome(conn, "email", contact["id"], jid, "failed",
                       subject=subject, body=body_text, error="timeout")
        print(f"EMAIL_FAILED: timeout sending to {email_addr}", file=sys.stderr)
    except FileNotFoundError:
        from lib.outreach_ledger import record_outcome
        record_outcome(conn, "email", contact["id"], jid, "failed",
                       subject=subject, body=body_text,
                       error=f"gmail-cli not found at {GMAIL_CLI}")
        print(f"EMAIL_FAILED: gmail-cli not found at {GMAIL_CLI}", file=sys.stderr)


# ---------------------------------------------------------------------------
# LinkedIn Message
# ---------------------------------------------------------------------------

def cmd_message(jid, contact_idx=1, dry_run=False, body=None, body_file=None, force=False, no_attach=False):
    contacts = contact_list(job_id=jid)
    if not contacts:
        print(f"No contacts for {jid}. Run 'reach.py discover {jid}' first.", file=sys.stderr)
        return

    if contact_idx < 1 or contact_idx > len(contacts):
        print(f"Invalid contact index {contact_idx}. Valid range: 1-{len(contacts)}", file=sys.stderr)
        return

    contact = contacts[contact_idx - 1]
    name = contact.get("name", "")
    linkedin_url = contact.get("linkedin_url", "")
    conn = get_conn()

    if not linkedin_url:
        print(f"No LinkedIn URL for {name}.", file=sys.stderr)
        return

    if contact.get("message_sent") and not force:
        print(f"Already messaged {name} on LinkedIn. Use --force to re-send.", file=sys.stderr)
        return

    if _block_if_prior(conn, contact, force):
        return

    if not force:
        pending = conn.execute(
            "SELECT 1 FROM contact_attempts WHERE contact_id=? AND status='pending' LIMIT 1",
            (contact["id"],),
        ).fetchone()
        if pending:
            print(f"UNCERTAIN_SEND: {name} has an unconfirmed previous send — "
                  f"verify in the LinkedIn inbox, then "
                  f"reach.py update {jid} --contact {contact_idx} --set-sent message, "
                  f"or re-send with --force.",
                  file=sys.stderr)
            return

    # Get job info
    job = get_job(jid)
    company = job.get("company", "") if job else ""
    title = job.get("title", "") if job else ""

    if body:
        body_text = body
    elif body_file:
        try:
            with open(body_file, "r", encoding="utf-8") as f:
                body_text = f.read()
        except FileNotFoundError:
            print(f"Body file not found: {body_file}", file=sys.stderr)
            return
    else:
        body_text = _default_message_body(name, title, company)
        print(f"DEFAULT_BODY: used template — provide --body or --body-file for custom message", file=sys.stderr)

    if dry_run:
        print(f"DRY_RUN: Would DM {name} on LinkedIn", file=sys.stderr)
        print(f"  To: {linkedin_url}", file=sys.stderr)
        print(f"  {_voice_line('message')}", file=sys.stderr)
        resume_pdf = _job_resume_pdf(jid)
        if resume_pdf:
            print(f"  Attach: {os.path.basename(resume_pdf)} (tailored resume)",
                  file=sys.stderr)
        else:
            print(f"  Attach: NONE — no tailored resume PDF for {jid} "
                  f"(apply.py produces it during tailoring)",
                  file=sys.stderr)
        print(f"  Body:\n{body_text}\n", file=sys.stderr)
        print(f"NEXT: reach.py message {jid} --contact {contact_idx}  (remove --dry-run to send)", file=sys.stderr)
        return

    # Sandbox FIRST: refuse before launching a browser, not after. (The
    # library-level refusal in lib.linkedin_messaging stays as the
    # backstop — this is the cheap outer gate.)
    if _sandbox_refused():
        return

    if not _preflight_send(body_text, "message", contact=contact, job=job,
                           force=force):
        return

    from lib.chrome_manager import connect
    b, ctx = connect(timeout=30)
    if not ctx:
        print("ERROR: Could not connect to Chrome.", file=sys.stderr)
        return

    try:
        resume_pdf = None if no_attach else _job_resume_pdf(jid)
        result = send_message(ctx, linkedin_url, body_text, name=name,
                              attach=resume_pdf)
        from lib.outreach_ledger import record_outcome
        if result["status"] == "sent":
            conv_url = result.get("conversation_url", "")
            record_outcome(conn, "linkedin_message", contact["id"], jid, "sent",
                           body=body_text, message_id=conv_url,
                           event_type="linkedin_message",
                           event_title=f"Messaged {name} on LinkedIn",
                           event_desc=f"Conversation: {conv_url}")
            print(f"MESSAGE_SENT: {name}", file=sys.stderr)
            print(f"  Conversation: {conv_url}", file=sys.stderr)
        elif result["status"] == "uncertain":
            record_outcome(conn, "linkedin_message", contact["id"], jid,
                           "pending", body=body_text,
                           error=result.get("detail", ""))
            print(f"MESSAGE_UNCERTAIN: send clicked for {name} but not confirmed", file=sys.stderr)
            print(f"  {result.get('detail', '')}", file=sys.stderr)
            # --set-sent is the ONLY hint that actually settles the guard;
            # --note leaves message_sent=0 and the pending attempt in place.
            print(f"  NEXT: check the LinkedIn inbox. If it WAS sent: "
                  f"reach.py update {jid} --contact {contact_idx} --set-sent message",
                  file=sys.stderr)
            print(f"  NEXT: if it was NOT sent: reach.py message {jid} "
                  f"--contact {contact_idx} --force", file=sys.stderr)
        elif result["status"] == "connect_required":
            record_outcome(conn, "linkedin_message", contact["id"], jid,
                           "failed", body=body_text, error="connect_required")
            print(f"CONNECT_REQUIRED: Need to connect with {name} first", file=sys.stderr)
            print(f"  NEXT: reach.py connect {jid} --contact {contact_idx}", file=sys.stderr)
        elif result["status"] == "premium_required":
            record_outcome(conn, "linkedin_message", contact["id"], jid,
                           "failed", body=body_text, error="premium_required")
            print(f"PREMIUM_REQUIRED: LinkedIn Premium/InMail needed for {name}", file=sys.stderr)
        else:
            record_outcome(conn, "linkedin_message", contact["id"], jid,
                           "failed", body=body_text,
                           error=result.get("detail", "unknown"))
            print(f"MESSAGE_FAILED: {result.get('detail', 'unknown')}", file=sys.stderr)
    finally:
        try:
            b.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Connect
# ---------------------------------------------------------------------------

def cmd_connect(jid, contact_idx=1, note=None, force=False):
    contacts = contact_list(job_id=jid)
    if not contacts:
        print(f"No contacts for {jid}.", file=sys.stderr)
        return

    if contact_idx < 1 or contact_idx > len(contacts):
        print(f"Invalid contact index {contact_idx}. Valid range: 1-{len(contacts)}", file=sys.stderr)
        return

    contact = contacts[contact_idx - 1]
    name = contact.get("name", "")
    linkedin_url = contact.get("linkedin_url", "")
    conn = get_conn()

    if not linkedin_url:
        print(f"No LinkedIn URL for {name}.", file=sys.stderr)
        return

    # SAME-ROW one-shot. email/message are guarded by their own flags;
    # connect had no equivalent, so a re-run (e.g. after a crash, or when
    # the first outcome was uncertain) sent a SECOND invitation to a
    # person who already had one pending. Scoped to the connect channel so
    # the deliberate connect -> DM funnel on one row still works.
    if not force:
        prior_connect = conn.execute(
            "SELECT status FROM contact_attempts "
            "WHERE contact_id=? AND channel='linkedin_connect' "
            "AND status IN ('sent','pending') LIMIT 1",
            (contact["id"],),
        ).fetchone()
        if prior_connect:
            print(f"ALREADY_CONNECTED_REQUEST: {name} already has a "
                  f"{prior_connect['status']} connection request — one-shot "
                  f"guard. Check LinkedIn 'Sent invitations', then use "
                  f"--force only if it truly never arrived.", file=sys.stderr)
            return

    if _block_if_prior(conn, contact, force):
        return

    job = get_job(jid)
    company = job.get("company", "") if job else ""
    title = job.get("title", "") if job else ""

    if not note:
        note = (f"Hi {name}, I'm exploring {title} at {company} and would "
                f"love to connect!")

    if _sandbox_refused():
        return

    from lib.chrome_manager import connect as chrome_connect
    b, ctx = chrome_connect(timeout=30)
    if not ctx:
        print("ERROR: Could not connect to Chrome.", file=sys.stderr)
        return

    try:
        result = send_connect_request(ctx, linkedin_url, note=note)
        from lib.outreach_ledger import record_outcome
        if result["status"] == "sent":
            record_outcome(conn, "linkedin_connect", contact["id"], jid, "sent",
                           body=note,
                           event_type="linkedin_connect",
                           event_title=f"Sent connection request to {name}")
            print(f"CONNECT_SENT: {name}", file=sys.stderr)
        elif result["status"] == "already_connected":
            conn.execute("UPDATE contacts SET connection_degree='1st' WHERE id=?", (contact["id"],))
            conn.commit()
            record_outcome(conn, "linkedin_connect", contact["id"], jid, "failed",
                           body=note, error="already_connected")
            print(f"ALREADY_CONNECTED: {name}", file=sys.stderr)
            print(f"  NEXT: reach.py message {jid} --contact {contact_idx}", file=sys.stderr)
        else:
            record_outcome(conn, "linkedin_connect", contact["id"], jid, "failed",
                           body=note, error=result.get("detail", "unknown"))
            print(f"CONNECT_FAILED: {result.get('detail', 'unknown')}", file=sys.stderr)
    finally:
        try:
            b.close()
        except Exception:
            pass


def cmd_update(jid, contact_idx=1, email=None, note=None, set_sent=None):
    """Backfill or edit contact fields (email suggestions, confirmations)."""
    contacts = contact_list(job_id=jid)
    if not contacts:
        print(f"No contacts for {jid}. Run 'reach.py discover {jid}' first.", file=sys.stderr)
        return

    if contact_idx < 1 or contact_idx > len(contacts):
        print(f"Invalid contact index {contact_idx}. Valid range: 1-{len(contacts)}", file=sys.stderr)
        return

    contact = contacts[contact_idx - 1]
    updates = {}
    if email is not None:
        updates["email"] = email
    if note is not None:
        updates["notes"] = note
    if set_sent == "email":
        updates["email_sent"] = 1
        updates["reached_out"] = 1
    elif set_sent == "message":
        updates["message_sent"] = 1
        updates["reached_out"] = 1

    if not updates:
        print("Nothing to update. Provide --email, --note, or --set-sent.", file=sys.stderr)
        return

    contact_update(contact["id"], **updates)

    # --set-sent is the human confirming an UNCERTAIN send. Settle the
    # pending attempt too, otherwise the row keeps a 'pending' attempt
    # forever and every cross-job guard reads it as outreach-in-flight.
    if set_sent:
        channel = "email" if set_sent == "email" else "linkedin_message"
        from lib.outreach_ledger import settle
        n = settle(get_conn(), contact["id"], channel)
        if n:
            print(f"  settled {n} pending {channel} attempt(s)", file=sys.stderr)

    print(f"UPDATED: contact {contact_idx} ({contact.get('name','')})", file=sys.stderr)
    for k, v in updates.items():
        print(f"  {k}: {v}", file=sys.stderr)


def cmd_attempts(jid=None, contact_idx=None):
    """Show outreach attempts, optionally filtered by job/contact."""
    from lib.db.contacts import attempt_list
    attempts = attempt_list(job_id=jid, limit=50)
    if not attempts:
        print("No outreach attempts." + (f" for {jid}" if jid else ""), file=sys.stderr)
        return
    print(f"Attempts ({len(attempts)}):", file=sys.stderr)
    for a in attempts:
        contact_name = a.get("contact_name", "")
        print(f"  [{a['channel']:16s}] {a['status']:8s} {contact_name[:22]:22s} "
              f"{a.get('sent_at') or a.get('created_at') or ''}"
              + (f"  err: {a['error'][:50]}" if a.get("error") else ""), file=sys.stderr)


# ---------------------------------------------------------------------------
# Retry / Undo
# ---------------------------------------------------------------------------

def cmd_retry(jid):
    conn = get_conn()
    conn.execute("UPDATE jobs SET contact_discovered=0 WHERE id=?", (jid,))
    conn.commit()
    print(f"Retrying contact discovery for {jid}...", file=sys.stderr)
    cmd_discover(jid)


def cmd_undo(jid, confirm=False):
    """Reset contact state for a job.

    DESTRUCTIVE for one-shot integrity: the attempt rows deleted here are
    the evidence that a person was already contacted. Once gone, the same
    human discovered on another job is no longer blocked by the cross-job
    guard and CAN be messaged a second time. So a job with confirmed sends
    requires --confirm, and the people at risk are named first.
    """
    conn = get_conn()
    # CURVEBALL C8: the at-risk set must cover flag-based sends too. A send
    # confirmed via `update --set-sent` sets email_sent/message_sent WITHOUT
    # creating a sent attempt row, so keying at_risk only on contact_attempts
    # let undo silently disarm the cross-job guard for those people. Any
    # contact with outreach evidence (attempt row OR send flag) requires
    # --confirm, so the operator sees who loses their one-shot protection.
    # (Moved into lib/outreach_ledger with the other write choreography.)
    from lib.outreach_ledger import clear_outreach, outreach_at_risk
    at_risk = outreach_at_risk(conn, jid)
    if at_risk and not confirm:
        print(f"REFUSED: {len(at_risk)} contact(s) on {jid} have confirmed or "
              f"in-flight outreach. Deleting their attempts removes the only "
              f"record that they were already contacted — the cross-job "
              f"one-shot guard would then allow a REPEAT message:",
              file=sys.stderr)
        for r in at_risk:
            print(f"  - {r['name']} ({r['linkedin_url'] or r['email'] or '?'})",
                  file=sys.stderr)
        print(f"  Re-run with --confirm if you really want this.", file=sys.stderr)
        return

    clear_outreach(conn, jid)
    print(f"Undone: contact state + attempts reset for {jid}", file=sys.stderr)
    if at_risk:
        print(f"  WARNING: discarded outreach history for {len(at_risk)} "
              f"already-contacted person(s) — they are no longer protected "
              f"by the one-shot guard.", file=sys.stderr)


# ---------------------------------------------------------------------------
# Help
# ---------------------------------------------------------------------------

def cmd_help():
    print(__doc__)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(prog="reach.py", description="Contact discovery and outreach")
    sub = parser.add_subparsers(dest="command")

    discover_p = sub.add_parser("discover", help="Discover contacts for a job")
    discover_p.add_argument("jid", nargs="?", help="Job ID (omit with --all)")
    discover_p.add_argument("--all", action="store_true", help="Discover for all described/tailored jobs without contacts")
    discover_p.add_argument("--limit", type=int, help="With --all: max jobs to process")
    discover_p.add_argument("--team", help="Team/department name to search for")
    discover_p.add_argument("--no-llm", action="store_true", help="Skip LLM email suggestions")
    discover_p.add_argument("--no-browser", action="store_true", help="Skip browser-based discovery")

    list_p = sub.add_parser("list", help="List contacts for a job")
    list_p.add_argument("jid", help="Job ID")

    draft_p = sub.add_parser("draft", help="Draft an outreach message from the template")
    draft_p.add_argument("jid", help="Job ID")
    draft_p.add_argument("--contact", type=int, default=1, help="Contact index from list (1-based)")
    draft_p.add_argument("--channel", choices=["message", "email"], default="message",
                         help="Template to draft from (default message)")

    email_p = sub.add_parser("email", help="Send email to a contact")
    email_p.add_argument("jid", help="Job ID")
    email_p.add_argument("--contact", type=int, default=1, help="Contact index from list (1-based)")
    email_p.add_argument("--dry-run", action="store_true", help="Preview without sending")
    email_p.add_argument("--force", action="store_true", help="Re-send even if already emailed")
    body_group = email_p.add_mutually_exclusive_group()
    body_group.add_argument("--body", help="Email body text")
    body_group.add_argument("--body-file", help="File containing email body")

    msg_p = sub.add_parser("message", help="Send LinkedIn DM to a contact")
    msg_p.add_argument("jid", help="Job ID")
    msg_p.add_argument("--contact", type=int, default=1, help="Contact index from list (1-based)")
    msg_p.add_argument("--dry-run", action="store_true", help="Preview without sending")
    msg_p.add_argument("--force", action="store_true", help="Re-send even if already messaged")
    msg_p.add_argument("--no-attach", action="store_true",
                       help="Send WITHOUT the tailored resume attachment (default: attach)")
    body_group2 = msg_p.add_mutually_exclusive_group()
    body_group2.add_argument("--body", help="Message body text")
    body_group2.add_argument("--body-file", help="File containing message body")

    connect_p = sub.add_parser("connect", help="Send LinkedIn connection request")
    connect_p.add_argument("jid", help="Job ID")
    connect_p.add_argument("--contact", type=int, default=1, help="Contact index from list (1-based)")
    connect_p.add_argument("--note", help="Connection request note")
    connect_p.add_argument("--force", action="store_true",
                           help="Override the cross-job one-shot guard")

    update_p = sub.add_parser("update", help="Backfill or edit contact fields")
    update_p.add_argument("jid", help="Job ID")
    update_p.add_argument("--contact", type=int, default=1, help="Contact index from list (1-based)")
    update_p.add_argument("--email", help="Set contact email")
    update_p.add_argument("--note", help="Set contact notes")
    update_p.add_argument("--set-sent", choices=["email", "message"],
                          help="Mark contact as contacted (after manual confirmation)")

    attempts_p = sub.add_parser("attempts", help="Show outreach attempts")
    attempts_p.add_argument("jid", nargs="?", help="Job ID (optional filter)")

    threads_p = sub.add_parser("threads", help="Reconcile contacts against the real LinkedIn inbox")
    threads_p.add_argument("jid", help="Job ID")
    threads_p.add_argument("--backfill", action="store_true",
                           help="Record existing threads as backfilled attempt rows")


    retry_p = sub.add_parser("retry", help="Retry contact discovery for a job")
    retry_p.add_argument("jid", help="Job ID")

    undo_p = sub.add_parser("undo", help="Reset contact state for a job")
    undo_p.add_argument("jid", help="Job ID")
    undo_p.add_argument("--confirm", action="store_true",
                        help="Required when the job has confirmed/in-flight "
                             "outreach (deleting it disarms the one-shot guard)")

    sub.add_parser("help", help="This message")

    args = parser.parse_args()

    if args.command == "discover":
        if args.all:
            cmd_discover_all(use_llm=not args.no_llm, use_browser=not args.no_browser, limit=args.limit)
        elif args.jid:
            cmd_discover(args.jid, team_name=args.team, use_llm=not args.no_llm, use_browser=not args.no_browser)
        else:
            print("Usage: reach.py discover <jid>  OR  reach.py discover --all [--limit N]", file=sys.stderr)
    elif args.command == "list":
        cmd_list(args.jid)
    elif args.command == "draft":
        cmd_draft(args.jid, contact_idx=args.contact, channel=args.channel)
    elif args.command == "threads":
        cmd_threads(args.jid, backfill=args.backfill)
    elif args.command == "email":
        cmd_email(args.jid, contact_idx=args.contact, dry_run=args.dry_run,
                  body=args.body, body_file=args.body_file, force=args.force)
    elif args.command == "message":
        cmd_message(args.jid, contact_idx=args.contact, dry_run=args.dry_run,
                    body=args.body, body_file=args.body_file, force=args.force,
                    no_attach=args.no_attach)
    elif args.command == "connect":
        cmd_connect(args.jid, contact_idx=args.contact, note=args.note,
                    force=args.force)
    elif args.command == "update":
        cmd_update(args.jid, contact_idx=args.contact, email=args.email, note=args.note, set_sent=args.set_sent)
    elif args.command == "attempts":
        cmd_attempts(jid=args.jid)
    elif args.command == "retry":
        cmd_retry(args.jid)
    elif args.command == "undo":
        cmd_undo(args.jid, confirm=args.confirm)
    elif args.command == "help":
        cmd_help()
    else:
        cmd_help()


if __name__ == "__main__":
    main()
