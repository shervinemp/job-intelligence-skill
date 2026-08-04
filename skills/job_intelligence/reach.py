#!/usr/bin/env python3
"""reach.py — Contact discovery and outreach CLI.

Usage:
  reach.py discover <jid> [--team <name>] [--no-llm] [--no-browser]
  reach.py list <jid>                        Show discovered contacts for a job
  reach.py email <jid> [--contact N] [--dry-run] [--force] [--body <text>] [--body-file <path>]
  reach.py message <jid> [--contact N] [--dry-run] [--force] [--body <text>] [--body-file <path>]
  reach.py connect <jid> [--contact N] [--note <text>]
  reach.py update <jid> [--contact N] [--email <addr>] [--note <text>] [--set-sent email|message]
  reach.py attempts [<jid>]                 Show outreach attempts
  reach.py status                            Pipeline state with contact info
  reach.py retry <jid>                       Retry failed contact discovery
  reach.py undo <jid>                        Reset contact state
"""

import json
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from lib.contacts import discover_contacts
from lib.db import get_conn, get_job, pipeline_status
from lib.db.contacts import (
    attempt_add, contact_add, contact_list, contact_update,
    attempt_list,
)
from lib.db.events import event_add
from lib.linkedin_messaging import (
    send_message,
    send_connect_request,
)

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
GMAIL_CLI = os.path.join(SKILL_DIR, "..", "gmail-cli", "gmail_cli.py")


def _prior_outreach(conn, contact):
    """Any sent/pending outreach to the SAME PERSON on any OTHER contact row.

    One-shot guards are per contact row, so the same person discovered on
    two jobs (or twice within a job) gets two rows and nothing blocks the
    second message — that repeat would give away the automation. Matches
    on linkedin_url or email, and counts both flag-based sends and
    attempts (covers the 'uncertain' path, which never sets reached_out).
    The current row itself is excluded so a connect -> DM funnel on one
    row keeps working."""
    url = (contact.get("linkedin_url") or "").strip()
    email = (contact.get("email") or "").strip()
    if not url and not email:
        return None
    q = ("SELECT c.job_id, c.name, a.channel, a.status, a.created_at "
         "FROM contacts c LEFT JOIN contact_attempts a ON a.contact_id = c.id "
         "WHERE c.id != ? "
         "AND (c.linkedin_url = ? OR (c.email != '' AND c.email IS NOT NULL AND c.email = ?)) "
         "AND (c.reached_out = 1 OR a.status IN ('sent', 'pending')) "
         "ORDER BY a.created_at DESC LIMIT 1")
    return conn.execute(q, (contact["id"], url, email)).fetchone()


def _block_if_prior(conn, contact, force):
    """Cross-job/duplicate-row one-shot gate shared by email/message/connect."""
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
# Email
# ---------------------------------------------------------------------------

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
        print(f"DRY_RUN: Would email {name} at {email_addr}", file=sys.stderr)
        print(f"  Subject: {subject}", file=sys.stderr)
        print(f"  Body:\n{body_text}\n", file=sys.stderr)
        print(f"NEXT: reach.py email {jid} --contact {contact_idx}  (remove --dry-run to send)", file=sys.stderr)
        return

    if os.environ.get("JI_TESTS"):
        print(f"TEST_SANDBOX: transmission refused under the test runner — "
              f"remove JI_TESTS from the environment to send.", file=sys.stderr)
        return

    # Send via gmail-cli
    cmd = [
        sys.executable, GMAIL_CLI, "send", email_addr, subject,
        "--body", body_text, "--json",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=60)
        output = r.stdout.decode("utf-8", errors="replace")
        err = r.stderr.decode("utf-8", errors="replace")
        if r.returncode != 0:
            attempt_add(contact["id"], "email", status="failed", subject=subject,
                        body=body_text, error=err[:200])
            print(f"EMAIL_FAILED: {err[:200]}", file=sys.stderr)
            return

        try:
            parsed = json.loads(output)
            if parsed.get("status") == "sent":
                msg_id = parsed.get("message_id", "")
                conn.execute("UPDATE contacts SET email_sent=1, reached_out=1, last_contacted_at=datetime('now'), notes=notes || '\nEmailed: ' || datetime('now') WHERE id=?", (contact["id"],))
                conn.execute("UPDATE jobs SET outreach_attempted=1 WHERE id=?", (jid,))
                conn.commit()
                attempt_add(contact["id"], "email", status="sent", subject=subject,
                            body=body_text, message_id=msg_id)
                event_add(jid, "email", f"Emailed {name} about {title}",
                         description=f"Sent to {email_addr}, msg_id={msg_id}")
                print(f"EMAIL_SENT: {msg_id} to {email_addr}", file=sys.stderr)
                print(f"  NEXT: Wait for response or follow up", file=sys.stderr)
            else:
                attempt_add(contact["id"], "email", status="failed", subject=subject,
                            body=body_text, error=parsed.get('error', 'unknown'))
                print(f"EMAIL_FAILED: {parsed.get('error', 'unknown')}", file=sys.stderr)
        except json.JSONDecodeError:
            attempt_add(contact["id"], "email", status="failed", subject=subject,
                        body=body_text, error=f"unexpected response: {output[:200]}")
            print(f"EMAIL_FAILED: unexpected response: {output[:200]}", file=sys.stderr)
    except subprocess.TimeoutExpired:
        attempt_add(contact["id"], "email", status="failed", subject=subject,
                    body=body_text, error="timeout")
        print(f"EMAIL_FAILED: timeout sending to {email_addr}", file=sys.stderr)
    except FileNotFoundError:
        attempt_add(contact["id"], "email", status="failed", subject=subject,
                    body=body_text, error=f"gmail-cli not found at {GMAIL_CLI}")
        print(f"EMAIL_FAILED: gmail-cli not found at {GMAIL_CLI}", file=sys.stderr)


# ---------------------------------------------------------------------------
# LinkedIn Message
# ---------------------------------------------------------------------------

def cmd_message(jid, contact_idx=1, dry_run=False, body=None, body_file=None, force=False):
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
        body_text = (
            f"Hi {name},\n\n"
            f"I recently came across the {title} role at {company} and "
            f"wanted to learn more. Would you be open to a quick chat?\n\n"
            f"Thanks!"
        )
        print(f"DEFAULT_BODY: used template — provide --body or --body-file for custom message", file=sys.stderr)

    if dry_run:
        print(f"DRY_RUN: Would DM {name} on LinkedIn", file=sys.stderr)
        print(f"  To: {linkedin_url}", file=sys.stderr)
        print(f"  Body:\n{body_text}\n", file=sys.stderr)
        print(f"NEXT: reach.py message {jid} --contact {contact_idx}  (remove --dry-run to send)", file=sys.stderr)
        return

    from lib.chrome_manager import connect
    b, ctx = connect(timeout=30)
    if not ctx:
        print("ERROR: Could not connect to Chrome.", file=sys.stderr)
        return

    if os.environ.get("JI_TESTS"):
        print(f"TEST_SANDBOX: transmission refused under the test runner — "
              f"remove JI_TESTS from the environment to send.", file=sys.stderr)
        return

    try:
        result = send_message(ctx, linkedin_url, body_text, name=name)
        if result["status"] == "sent":
            conv_url = result.get("conversation_url", "")
            conn.execute("UPDATE contacts SET message_sent=1, reached_out=1, last_contacted_at=datetime('now'), notes=notes || '\nMessaged: ' || datetime('now') WHERE id=?", (contact["id"],))
            conn.execute("UPDATE jobs SET outreach_attempted=1 WHERE id=?", (jid,))
            conn.commit()
            attempt_add(contact["id"], "linkedin_message", status="sent",
                        body=body_text, message_id=conv_url)
            event_add(jid, "linkedin_message", f"Messaged {name} on LinkedIn",
                     description=f"Conversation: {conv_url}")
            print(f"MESSAGE_SENT: {name}", file=sys.stderr)
            print(f"  Conversation: {conv_url}", file=sys.stderr)
        elif result["status"] == "uncertain":
            attempt_add(contact["id"], "linkedin_message", status="pending",
                        body=body_text, error=result.get("detail", ""))
            print(f"MESSAGE_UNCERTAIN: send clicked for {name} but not confirmed", file=sys.stderr)
            print(f"  {result.get('detail', '')}", file=sys.stderr)
            print(f"  NEXT: check LinkedIn inbox manually. If sent: reach.py update {jid} --contact {contact_idx} --note 'confirmed sent'", file=sys.stderr)
            print(f"  NEXT: if not sent: reach.py message {jid} --contact {contact_idx} --force", file=sys.stderr)
        elif result["status"] == "connect_required":
            attempt_add(contact["id"], "linkedin_message", status="failed",
                        body=body_text, error="connect_required")
            print(f"CONNECT_REQUIRED: Need to connect with {name} first", file=sys.stderr)
            print(f"  NEXT: reach.py connect {jid} --contact {contact_idx}", file=sys.stderr)
        elif result["status"] == "premium_required":
            attempt_add(contact["id"], "linkedin_message", status="failed",
                        body=body_text, error="premium_required")
            print(f"PREMIUM_REQUIRED: LinkedIn Premium/InMail needed for {name}", file=sys.stderr)
        else:
            attempt_add(contact["id"], "linkedin_message", status="failed",
                        body=body_text, error=result.get("detail", "unknown"))
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

    if _block_if_prior(conn, contact, force):
        return

    job = get_job(jid)
    company = job.get("company", "") if job else ""

    if not note:
        note = f"Hi {name}, I'm exploring opportunities at {company} and would love to connect!"

    from lib.chrome_manager import connect as chrome_connect
    b, ctx = chrome_connect(timeout=30)
    if not ctx:
        print("ERROR: Could not connect to Chrome.", file=sys.stderr)
        return

    if os.environ.get("JI_TESTS"):
        print(f"TEST_SANDBOX: transmission refused under the test runner — "
              f"remove JI_TESTS from the environment to send.", file=sys.stderr)
        return

    try:
        result = send_connect_request(ctx, linkedin_url, note=note)
        if result["status"] == "sent":
            conn.execute("UPDATE contacts SET reached_out=1, last_contacted_at=datetime('now') WHERE id=?", (contact["id"],))
            conn.commit()
            attempt_add(contact["id"], "linkedin_connect", status="sent", body=note)
            event_add(jid, "linkedin_connect", f"Sent connection request to {name}")
            print(f"CONNECT_SENT: {name}", file=sys.stderr)
        elif result["status"] == "already_connected":
            conn.execute("UPDATE contacts SET connection_degree='1st' WHERE id=?", (contact["id"],))
            conn.commit()
            attempt_add(contact["id"], "linkedin_connect", status="failed", body=note,
                        error="already_connected")
            print(f"ALREADY_CONNECTED: {name}", file=sys.stderr)
            print(f"  NEXT: reach.py message {jid} --contact {contact_idx}", file=sys.stderr)
        else:
            attempt_add(contact["id"], "linkedin_connect", status="failed", body=note,
                        error=result.get("detail", "unknown"))
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
# Status / Retry / Undo
# ---------------------------------------------------------------------------

def cmd_status():
    s = pipeline_status()
    conn = get_conn()

    total_contacts = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
    total_outreach = conn.execute("SELECT COUNT(*) FROM contacts WHERE reached_out=1").fetchone()[0]
    pending_email = conn.execute("SELECT COUNT(*) FROM contacts WHERE email_sent=0 AND email != '' AND email IS NOT NULL").fetchone()[0]
    pending_message = conn.execute("SELECT COUNT(*) FROM contacts WHERE message_sent=0 AND linkedin_url != '' AND linkedin_url IS NOT NULL").fetchone()[0]

    print(f"Jobs: {s['jobs']} total", file=sys.stderr)
    for stage in ["extracted", "described", "tailored", "applied"]:
        c = s["stages"].get(stage, 0)
        if c:
            print(f"  {stage}: {c}", file=sys.stderr)

    print(f"\nContacts: {total_contacts}", file=sys.stderr)
    print(f"  Reached out: {total_outreach}", file=sys.stderr)
    print(f"  Pending email: {pending_email}", file=sys.stderr)
    print(f"  Pending LinkedIn DM: {pending_message}", file=sys.stderr)

    # Summary per job
    jids = conn.execute(
        "SELECT j.id, j.title, j.company, COUNT(c.id) as cnt FROM jobs j "
        "LEFT JOIN contacts c ON c.job_id=j.id "
        "WHERE j.state='active' GROUP BY j.id HAVING cnt > 0"
    ).fetchall()
    if jids:
        print(f"\nJobs with contacts:", file=sys.stderr)
        for r in jids:
            sent = conn.execute("SELECT COUNT(*) FROM contacts WHERE job_id=? AND reached_out=1", (r["id"],)).fetchone()[0]
            print(f"  {r['id'][:12]} | {r['title'][:30]:30s} | {r['company'][:20]:20s} | contacts: {r['cnt']} | reached: {sent}", file=sys.stderr)

    print(f"\n  next: {s['next_step']}", file=sys.stderr)


def cmd_retry(jid):
    conn = get_conn()
    conn.execute("UPDATE jobs SET contact_discovered=0 WHERE id=?", (jid,))
    conn.commit()
    print(f"Retrying contact discovery for {jid}...", file=sys.stderr)
    cmd_discover(jid)


def cmd_undo(jid):
    conn = get_conn()
    conn.execute("UPDATE contacts SET email_sent=0, message_sent=0, reached_out=0 WHERE job_id=?", (jid,))
    conn.execute("DELETE FROM contact_attempts WHERE contact_id IN "
                 "(SELECT id FROM contacts WHERE job_id=?)", (jid,))
    conn.execute("UPDATE jobs SET outreach_attempted=0 WHERE id=?", (jid,))
    conn.commit()
    print(f"Undone: contact state + attempts reset for {jid}", file=sys.stderr)


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

    sub.add_parser("status", help="Pipeline state with contact info")

    retry_p = sub.add_parser("retry", help="Retry contact discovery for a job")
    retry_p.add_argument("jid", help="Job ID")

    undo_p = sub.add_parser("undo", help="Reset contact state for a job")
    undo_p.add_argument("jid", help="Job ID")

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
    elif args.command == "email":
        cmd_email(args.jid, contact_idx=args.contact, dry_run=args.dry_run,
                  body=args.body, body_file=args.body_file, force=args.force)
    elif args.command == "message":
        cmd_message(args.jid, contact_idx=args.contact, dry_run=args.dry_run,
                    body=args.body, body_file=args.body_file, force=args.force)
    elif args.command == "connect":
        cmd_connect(args.jid, contact_idx=args.contact, note=args.note,
                    force=args.force)
    elif args.command == "update":
        cmd_update(args.jid, contact_idx=args.contact, email=args.email, note=args.note, set_sent=args.set_sent)
    elif args.command == "attempts":
        cmd_attempts(jid=args.jid)
    elif args.command == "status":
        cmd_status()
    elif args.command == "retry":
        cmd_retry(args.jid)
    elif args.command == "undo":
        cmd_undo(args.jid)
    elif args.command == "help":
        cmd_help()
    else:
        cmd_help()


if __name__ == "__main__":
    main()
