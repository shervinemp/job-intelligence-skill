#!/usr/bin/env python3
"""stage_emails.py — Search Gmail, save threads to DB, fetch & clean emails."""

import json
import os
import re
import shlex
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from html import unescape

from lib.db import stage_save, setting_get, setting_set
from lib.db import search_threads_save, search_threads_pending, search_threads_clear, get_conn

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
GMAIL_CLI = os.path.join(SKILL_DIR, "..", "gmail-cli", "gmail_cli.py")
_POOL_SIZE = 8

_FOOTER_MARKS = [
    "unsubscribe", "manage your", "you are receiving this",
    "you received this email", "you're receiving this", "this email was intended",
    "view in browser", "view this email", "all rights reserved",
    "help centre", "email was sent to", "if you don't want",
    "if you prefer not", "to stop receiving", "privacy policy",
    "terms of service", "terms of use",
]


def _load_query():
    path = os.path.join(SKILL_DIR, ".env")
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("GMAIL_SEARCH_QUERY="):
                    return line[len("GMAIL_SEARCH_QUERY="):].strip()
    return os.environ.get("GMAIL_SEARCH_QUERY", "label:Jobs OR from:linkedin OR from:indeed OR subject:job")


def _run_search(query):
    r = subprocess.run(
        [sys.executable, GMAIL_CLI, "gmail", "search"] + shlex.split(query) + ["--all", "--json"],
        capture_output=True, timeout=180,
    )
    if r.returncode != 0:
        err = r.stderr.decode("utf-8", errors="replace").strip()
        print(f"Search failed: {err}", file=sys.stderr)
        sys.exit(1)
    raw = r.stdout.decode("utf-8", errors="replace")
    return json.loads(raw)


def _strip_footer(text):
    cutoff = len(text) * 0.3
    lower = text.lower()
    best = len(text)
    for mark in _FOOTER_MARKS:
        idx = lower.find(mark, int(cutoff))
        if idx != -1 and idx < best:
            best = idx
    if best < len(text):
        text = text[:best]
    return text.strip()


def clean_html(html):
    html = re.sub(r'<(script|style)\b[^>]*>.*?</\1>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<img[^>]*\ssrc=["\'][^"\']*(?:track|pixel|spacer|beacon|open)[^"\']*["\'][^>]*/?>', '', html, flags=re.IGNORECASE)
    html = re.sub(r'<a\s+[^>]*href=["\'][^"\']*["\'][^>]*>\s*</a>', '', html)
    html = re.sub(r'<span[^>]*>\u200c</span>', '', html)

    seen_urls = set()
    def link_replacer(match):
        url = match.group(1)
        if url in seen_urls:
            return ""
        seen_urls.add(url)
        if len(url) > 150:
            cut = url[:150]
            last = cut.rfind('&')
            if last > 0:
                url = cut[:last]
        text = re.sub(r'<[^>]+>', '', match.group(2).strip())
        if not text or len(text) < 2:
            return f" {url}"
        return f" [{url}] {text} "
    html = re.sub(r'<a\s+(?:[^>]*?\s+)?href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', link_replacer, html, flags=re.DOTALL | re.IGNORECASE)

    html = re.sub(r'<[^>]+>', ' ', html)
    text = unescape(html)
    text = ''.join(c for c in text if c == '\n' or (c.isascii() and c.isprintable()))
    text = _strip_footer(text)
    text = re.sub(r'\s+', ' ', text)
    text = text.encode('utf-8', errors='replace').decode('utf-8').strip()
    return text[:3000]


def _fetch_one(tid):
    try:
        r = subprocess.run([sys.executable, GMAIL_CLI, 'gmail', 'get', tid],
                           capture_output=True, timeout=60)
        if r.returncode != 0:
            return tid, None
        cleaned = clean_html(r.stdout.decode('utf-8', errors='replace'))
        return tid, cleaned
    except subprocess.TimeoutExpired:
        return tid, None
    except Exception:
        return tid, None


def stage_emails():
    staged_ids = set(setting_get("staged_ids", []))
    skipped_ids = set(setting_get("skipped_ids", []))

    pending = search_threads_pending()
    if not pending:
        print("No new threads to stage.", file=sys.stderr)
        return

    print(f"Fetching {len(pending)} threads ({_POOL_SIZE} workers)...", file=sys.stderr)

    results = []
    new_skipped = []
    with ThreadPoolExecutor(max_workers=_POOL_SIZE) as pool:
        futures = {pool.submit(_fetch_one, tid): tid for tid, *_ in pending}
        done = 0
        for future in as_completed(futures):
            done += 1
            tid, text = future.result()
            if text and re.search(r'\b(job|jobs)\b', text.lower()):
                results.append((tid, text))
            else:
                new_skipped.append(tid)
            if done % 50 == 0 or done == len(pending):
                print(f"  {done}/{len(pending)} fetched ({len(results)} ok)", file=sys.stderr)

    new_staged = list(staged_ids)
    for tid, text in results:
        stage_save(tid, text)
        new_staged.append(tid)

    setting_set("staged_ids", new_staged)
    setting_set("skipped_ids", list(skipped_ids | set(new_skipped)))
    print(f"\nStaging complete. Staged: {len(results)}, Skipped (no job/jobs): {len(new_skipped)}", file=sys.stderr)


if __name__ == "__main__":
    days = 14
    refresh = False
    remaining = []
    i = 1
    while i < len(sys.argv):
        a = sys.argv[i]
        if a == "--refresh":
            refresh = True
        elif a == "--days" and i + 1 < len(sys.argv):
            days = int(sys.argv[i + 1])
            i += 1
        else:
            remaining.append(a)
        i += 1
    sys.argv = remaining or [sys.argv[0]]

    if refresh:
        search_threads_clear()
        setting_set("staged_ids", [])
        setting_set("skipped_ids", [])

    query = _load_query()
    if days:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y/%m/%d")
        query = f"{query} after:{cutoff}"
    print(f"Searching Gmail (last {days}d)", file=sys.stderr)
    data = _run_search(query)
    threads = data.get("threads", [])
    print(f"Found {len(threads)} threads.", file=sys.stderr)
    search_threads_save(threads)
    stage_emails()
