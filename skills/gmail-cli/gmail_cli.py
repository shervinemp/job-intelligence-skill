#!/usr/bin/env python3
"""gmail_cli.py — Gmail API CLI for the job intelligence pipeline.

Provides gmail search + get, auth management, and email send via the Google Gmail API.

Supported commands:
  gmail-cli auth credentials <path>
  gmail-cli auth add <email> [--services <svcs>]
  gmail-cli auth list
  gmail-cli auth remove <email>
  gmail-cli gmail search <query> [--all] [--json|-j] [--max N]
  gmail-cli gmail get <messageId>
  gmail-cli send <to> <subject> --body <text> | --body-file <path>
"""

import argparse
import base64
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

VERSION = "1.1.0-reach"
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
SEND_SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
CONFIG_DIR = Path.home() / ".config" / "gmail-cli"
CREDENTIALS_PATH = CONFIG_DIR / "credentials.json"
TOKENS_DIR = CONFIG_DIR / "tokens"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _token_path(email):
    return TOKENS_DIR / f"{email}.json"

def _send_token_path(email):
    return TOKENS_DIR / f"{email}.send.json"


def _get_creds(email=None):
    if email:
        p = _token_path(email)
        if p.exists():
            c = Credentials.from_authorized_user_file(str(p), SCOPES)
            if c and c.expired and c.refresh_token:
                c.refresh(Request())
                with open(str(p), "w") as f:
                    f.write(c.to_json())
            return c
    return None


def _get_send_creds(email=None):
    if email:
        p = _send_token_path(email)
        if p.exists():
            c = Credentials.from_authorized_user_file(str(p), SEND_SCOPES)
            if c and c.expired and c.refresh_token:
                c.refresh(Request())
                with open(str(p), "w") as f:
                    f.write(c.to_json())
            return c
    return None


def _get_send_service(email=None):
    if acct := (os.environ.get("GMAIL_CLI_ACCOUNT") or os.environ.get("GOG_ACCOUNT")):
        email = email or acct
    if not email and TOKENS_DIR.exists():
        send_tokens = sorted(TOKENS_DIR.glob("*.send.json"))
        if send_tokens:
            email = send_tokens[0].stem.replace(".send", "")
    creds = _get_send_creds(email)
    if not creds:
        print("No send-capable token found. Run 'gmail-cli auth add <email> --services gmail.send' first.", file=sys.stderr)
        sys.exit(1)
    return build("gmail", "v1", credentials=creds)


def _get_service(email=None):
    if acct := (os.environ.get("GMAIL_CLI_ACCOUNT") or os.environ.get("GOG_ACCOUNT")):
        email = email or acct
    if not email and TOKENS_DIR.exists():
        # Exclude send-scope tokens (.send.json) — read token discovery only
        tokens = sorted(t for t in TOKENS_DIR.glob("*.json") if not t.stem.endswith(".send"))
        if tokens:
            email = tokens[0].stem
    creds = _get_creds(email)
    if not creds:
        print("Not authenticated. Run 'gmail-cli auth add <email>' first.", file=sys.stderr)
        sys.exit(1)
    return build("gmail", "v1", credentials=creds)


def _fmt_date(ms):
    if ms:
        dt = datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M")
    return ""


def _extract_body(payload):
    html = text = None
    if payload.get("mimeType") == "text/html" and payload.get("body", {}).get("data"):
        html = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        text = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")
    for part in payload.get("parts", []):
        h, t = _extract_body(part)
        html = html or h
        text = text or t
    return html, text


# ---------------------------------------------------------------------------
# Auth commands
# ---------------------------------------------------------------------------

def _cmd_auth_credentials(path):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    src = Path(path).resolve()
    if not src.exists():
        print(f"Error: {path} not found.", file=sys.stderr)
        sys.exit(1)
    if src == CREDENTIALS_PATH.resolve():
        print(f"Credentials already in place at {CREDENTIALS_PATH}")
        return
    shutil.copy(str(src), str(CREDENTIALS_PATH))
    print(f"Credentials saved to {CREDENTIALS_PATH}")


def _cmd_auth_add(email, services):
    if not CREDENTIALS_PATH.exists():
        print("No credentials found. Run 'gmail-cli auth credentials <path>' first.", file=sys.stderr)
        sys.exit(1)
    TOKENS_DIR.mkdir(parents=True, exist_ok=True)
    svcs = [s.strip() for s in services.split(",")] if services else ["gmail"]
    for svc in svcs:
        if svc == "gmail.send":
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SEND_SCOPES)
            creds = flow.run_local_server(port=0, open_browser=True)
            with open(str(_send_token_path(email)), "w") as f:
                f.write(creds.to_json())
            print(f"Authenticated send scope for {email}")
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES)
            creds = flow.run_local_server(port=0, open_browser=True)
            with open(str(_token_path(email)), "w") as f:
                f.write(creds.to_json())
            print(f"Authenticated read scope for {email}")
    print(f"Done. Authenticated as {email}")


def _cmd_auth_list():
    if not TOKENS_DIR.exists():
        print("No authenticated accounts.")
        return
    tokens = sorted(TOKENS_DIR.glob("*.json"))
    if not tokens:
        print("No authenticated accounts.")
        return
    print("Authenticated accounts:")
    for t in tokens:
        stem = t.stem
        scope = "read+send" if stem.endswith(".send") else "read"
        label = stem.replace(".send", "") if stem.endswith(".send") else stem
        print(f"  {label}  [{scope}]")


def _cmd_auth_remove(email):
    removed = 0
    for p in (_token_path(email), _send_token_path(email)):
        if p.exists():
            p.unlink()
            removed += 1
    if removed:
        print(f"Removed {email} ({removed} token(s))")
    else:
        print(f"No token found for {email}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Gmail commands
# ---------------------------------------------------------------------------

def _cmd_gmail_search(query_list, all_pages=False, json_out=False, max_results=None):
    query = " ".join(query_list)
    service = _get_service()

    limit = max_results if max_results is not None else (500 if all_pages else 10)
    thread_ids = []
    page_token = None

    while True:
        resp = service.users().threads().list(
            userId="me", q=query, maxResults=min(limit, 500), pageToken=page_token
        ).execute()
        for t in resp.get("threads", []):
            thread_ids.append(t["id"])
        page_token = resp.get("nextPageToken")
        if not page_token or (not all_pages and len(thread_ids) >= limit):
            break

    if max_results and len(thread_ids) > max_results:
        thread_ids = thread_ids[:max_results]
    elif not max_results and not all_pages and len(thread_ids) > 10:
        thread_ids = thread_ids[:10]

    threads_out = []
    for tid in thread_ids:
        try:
            msg = service.users().messages().get(
                userId="me", id=tid, format="metadata",
                metadataHeaders=["From", "Subject", "Date"]
            ).execute()
            headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            threads_out.append({
                "id": tid,
                "date": _fmt_date(msg.get("internalDate")),
                "from": headers.get("From", ""),
                "subject": headers.get("Subject", ""),
                "labels": msg.get("labelIds", []),
                "messageCount": 1,
            })
        except HttpError:
            continue

    output = {"nextPageToken": "", "threads": threads_out}

    if json_out:
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print("nextPageToken: ")
        print("threads:")
        for t in output["threads"]:
            print(f"  - id: {t['id']}")
            print(f"    date: {t['date']}")
            print(f"    from: {t['from']}")
            print(f"    subject: {t['subject']}")
            print(f"    labels: {json.dumps(t['labels'])}")
            print(f"    messageCount: {t['messageCount']}")


def _cmd_gmail_get(message_id):
    service = _get_service()
    try:
        msg = service.users().messages().get(userId="me", id=message_id, format="full").execute()
    except HttpError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
    html_body, text_body = _extract_body(msg.get("payload", {}))
    body = html_body or text_body or ""

    for h in ("From", "To", "Subject", "Date"):
        if h in headers:
            print(f"{h}: {headers[h]}")
    print()
    print(body)


# ---------------------------------------------------------------------------
# Email send
# ---------------------------------------------------------------------------

def _build_mime(to, subject, body, cc=None, bcc=None, attach=None):
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    from email.mime.application import MIMEApplication
    files = attach or []
    if files:
        msg = MIMEMultipart()
        msg.attach(MIMEText(body, "plain", "utf-8"))
        for fp in files:
            with open(fp, "rb") as f:
                part = MIMEApplication(f.read(), _subtype="pdf")
            part.add_header(
                "Content-Disposition", "attachment",
                filename=os.path.basename(fp))
            msg.attach(part)
    else:
        msg = MIMEText(body, "plain", "utf-8")
    msg["To"] = to
    msg["Subject"] = subject
    if cc:
        msg["Cc"] = cc
    if bcc:
        msg["Bcc"] = bcc
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    return {"raw": raw}


def _cmd_email_send(to, subject, body, cc=None, bcc=None, email=None, json_out=False, attach=None):
    service = _get_send_service(email)
    message = _build_mime(to, subject, body, cc, bcc, attach=attach)
    try:
        sent = service.users().messages().send(userId="me", body=message).execute()
        msg_id = sent.get("id", "")
        if json_out:
            print(json.dumps({"status": "sent", "message_id": msg_id, "to": to, "subject": subject}, ensure_ascii=False))
        else:
            print(f"Sent: {msg_id}")
            print(f"  To: {to}")
            print(f"  Subject: {subject}")
            for fp in (attach or []):
                print(f"  Attach: {os.path.basename(fp)}")
        return True
    except HttpError as e:
        err = str(e)
        if json_out:
            print(json.dumps({"status": "failed", "error": err[:500], "to": to, "subject": subject}, ensure_ascii=False))
        else:
            print(f"Failed: {err[:500]}", file=sys.stderr)
        return False


def _cmd_email_send_file(to, subject, body_file, cc=None, bcc=None, email=None, json_out=False, attach=None):
    try:
        with open(body_file, "r", encoding="utf-8") as f:
            body = f.read()
    except FileNotFoundError:
        print(f"Body file not found: {body_file}", file=sys.stderr)
        sys.exit(1)
    return _cmd_email_send(to, subject, body, cc=cc, bcc=bcc, email=email, json_out=json_out, attach=attach)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _die(msg):
    print(msg, file=sys.stderr)
    sys.exit(1)


class SmartHelpParser(argparse.ArgumentParser):
    def error(self, message):
        _die(f"error: {message}")

    def exit(self, status=0, message=None):
        if message:
            print(message, file=sys.stderr)
        sys.exit(status or 0)


def _build_parser():
    p = SmartHelpParser(prog="gmail-cli", description="Gmail CLI for job pipeline (uses Google Gmail API directly)")

    # --version
    p.add_argument("--version", action="store_true", dest="_version")

    # subcommands: auth | gmail | mail | email
    subs = p.add_subparsers(dest="command")

    # ---- auth ----
    auth = subs.add_parser("auth", help="Authentication commands")
    auth_subs = auth.add_subparsers(dest="auth_command")

    creds_p = auth_subs.add_parser("credentials", help="Store OAuth client credentials JSON")
    creds_p.add_argument("path", help="Path to client_secret.json")

    add_p = auth_subs.add_parser("add", help="Authenticate a Google account")
    add_p.add_argument("email", help="Email address to authenticate")
    add_p.add_argument("--services", default="gmail", help="Comma-separated services: gmail (read) and/or gmail.send (send)")

    auth_subs.add_parser("list", help="List authenticated accounts")
    rm_p = auth_subs.add_parser("remove", help="Remove an authenticated account")
    rm_p.add_argument("email", help="Email address to remove")

    # ---- gmail ----
    gmail = subs.add_parser("gmail", aliases=["mail", "email"], help="Gmail commands")
    gmail_subs = gmail.add_subparsers(dest="gmail_command")

    search = gmail_subs.add_parser("search", aliases=["find", "query", "ls", "list"], help="Search Gmail threads")
    search.add_argument("query", nargs="+", help="Gmail search query")
    search.add_argument("--all", "--all-pages", "--allpages", action="store_true", dest="all", help="Fetch all matching results")
    search.add_argument("--json", "-j", "--machine", action="store_true", dest="json_out", help="Output JSON")
    search.add_argument("--max", "--limit", type=int, dest="max_results", default=None, help="Max results")

    get_p = gmail_subs.add_parser("get", aliases=["info", "show"], help="Get message content")
    get_p.add_argument("message_id", help="Message or thread ID")

    # ---- email send ----
    email_subs = subs.add_parser("send", help="Send email via Gmail")
    email_subs.add_argument("to", help="Recipient email address")
    email_subs.add_argument("subject", help="Email subject")
    body_group = email_subs.add_mutually_exclusive_group(required=True)
    body_group.add_argument("--body", help="Inline email body text")
    body_group.add_argument("--body-file", help="Path to file containing email body")
    email_subs.add_argument("--cc", help="CC recipient")
    email_subs.add_argument("--bcc", help="BCC recipient")
    email_subs.add_argument("--attach", action="append", dest="attach",
                           help="File to attach (repeatable, e.g. a resume PDF)")
    email_subs.add_argument("--account", "--email", dest="email", help="Sender email account")
    email_subs.add_argument("--json", "-j", action="store_true", dest="json_out", help="Output JSON")

    return p


def main():
    parser = _build_parser()
    args = parser.parse_args()

    if args._version:
        print(VERSION)
        return

    cmd = args.command

    if cmd == "auth":
        ac = args.auth_command
        if ac == "credentials":
            _cmd_auth_credentials(args.path)
        elif ac == "add":
            _cmd_auth_add(args.email, args.services)
        elif ac == "list":
            _cmd_auth_list()
        elif ac == "remove":
            _cmd_auth_remove(args.email)
        else:
            _die("Usage: gmail-cli auth <credentials|add|list|remove> [args]")
    elif cmd in ("send",):
        # Transmission sandbox. reach.py refuses to send under JI_TESTS,
        # but it shells out to THIS script — so the refusal must exist here
        # too, or any test (or stray script) that invokes gmail-cli
        # directly walks straight past the sandbox and mails a real human.
        if os.environ.get("JI_TESTS"):
            print("TEST_SANDBOX: transmission refused under the test runner "
                  "— unset JI_TESTS to send.", file=sys.stderr)
            raise SystemExit(3)
        if args.body:
            _cmd_email_send(args.to, args.subject, args.body, cc=args.cc, bcc=args.bcc, email=args.email, json_out=args.json_out, attach=args.attach)
        else:
            _cmd_email_send_file(args.to, args.subject, args.body_file, cc=args.cc, bcc=args.bcc, email=args.email, json_out=args.json_out, attach=args.attach)
    elif cmd in ("gmail", "mail", "email"):
        gc = args.gmail_command
        if gc in ("search", "find", "query", "ls", "list"):
            _cmd_gmail_search(args.query, all_pages=args.all, json_out=args.json_out, max_results=args.max_results)
        elif gc in ("get", "info", "show"):
            _cmd_gmail_get(args.message_id)
        else:
            _die("Usage: gmail-cli gmail <search|get> [args]")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
