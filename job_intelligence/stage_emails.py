#!/usr/bin/env python3
"""stage_emails.py — Fetch & clean emails from search_results.json into DB stage table."""

import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import unescape

from lib.db import stage_save, stage_exists, setting_get, setting_set

GMAIL_CLI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "gmail-cli", "gmail_cli.py")
_POOL_SIZE = 8


_FOOTER_MARKS = [
    "unsubscribe", "manage your", "you are receiving this",
    "you received this email", "you're receiving this", "this email was intended",
    "view in browser", "view this email", "all rights reserved",
    "help centre", "email was sent to", "if you don't want",
    "if you prefer not", "to stop receiving", "privacy policy",
    "terms of service", "terms of use",
]


def _strip_footer(text):
    """Remove everything from the last footer marker onwards, but only if it's in the last 30%."""
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
            if '?' in url:
                base, qs = url.split('?', 1)
                budget = 150 - len(base) - 1
                if budget > 0:
                    cut = qs[:budget]
                    last = cut.rfind('&')
                    url = base + '?' + (cut[:last] if last > 0 else cut) + '...'
                else:
                    url = base + '?...'
            else:
                url = url[:150] + '...'
        text = re.sub(r'<[^>]+>', '', match.group(2).strip())
        if not text or len(text) < 2:
            return f" {url}"
        return f" [{url}] {text} "
    html = re.sub(r'<a\s+(?:[^>]*?\s+)?href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', link_replacer, html, flags=re.DOTALL | re.IGNORECASE)

    html = re.sub(r'<[^>]+>', ' ', html)
    text = unescape(html)
    text = re.sub(r'[\u200b\u200c\u200d\ufeff]', '', text)
    text = _strip_footer(text)
    # Strip lines that are mostly unicode filler characters (spacer noise)
    lines = [l for l in text.split('\n') if sum(1 for c in l if ord(c) in (0x034f, 0x00ad)) / max(len(l), 1) < 0.3]
    text = '\n'.join(lines)
    text = re.sub(r'\s+', ' ', text)
    text = text.encode('utf-8', errors='replace').decode('utf-8').strip()
    return text[:3000]


def _fetch_one(tid):
    """Fetch and clean a single email. Returns (tid, cleaned_text) or (tid, None) on failure."""
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


def stage_emails(search_results_path):
    if not os.path.exists(search_results_path):
        print(f"Error: {search_results_path} not found. Run gmail-cli search first.", file=sys.stderr)
        sys.exit(1)

    with open(search_results_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    threads = data.get('threads', [])
    print(f"Found {len(threads)} threads in search results.", file=sys.stderr)

    staged = set(setting_get("staged_ids", []))

    pending = []
    for thread in threads:
        tid = thread['id']
        if tid in staged:
            continue
        if stage_exists(tid):
            staged.add(tid)
            continue
        pending.append(tid)

    if not pending:
        print("No new threads to stage.", file=sys.stderr)
        return

    print(f"Fetching {len(pending)} threads ({_POOL_SIZE} workers)...", file=sys.stderr)

    results = []
    with ThreadPoolExecutor(max_workers=_POOL_SIZE) as pool:
        futures = {pool.submit(_fetch_one, tid): tid for tid in pending}
        done = 0
        for future in as_completed(futures):
            done += 1
            tid, text = future.result()
            if text:
                results.append((tid, text))
            if done % 50 == 0 or done == len(pending):
                print(f"  {done}/{len(pending)} fetched ({len(results)} ok)", file=sys.stderr)

    new_staged = list(staged)
    for tid, text in results:
        stage_save(tid, text)
        new_staged.append(tid)

    setting_set("staged_ids", new_staged)
    print(f"\nStaging complete. Staged: {len(results)}, Skipped: {len(threads) - len(pending)}, Failed: {len(pending) - len(results)}", file=sys.stderr)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        SEARCH_RESULTS = sys.argv[1]
    else:
        SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
        SEARCH_RESULTS = os.path.join(SCRIPT_DIR, "..", "..", "search_results_new.json")
        if not os.path.exists(SEARCH_RESULTS):
            SEARCH_RESULTS = os.path.join(SCRIPT_DIR, "..", "..", "search_results.json")

    stage_emails(SEARCH_RESULTS)
