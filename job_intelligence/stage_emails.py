#!/usr/bin/env python3
"""stage_emails.py — Fetch & clean emails from search_results.json into DB stage table."""

import json
import os
import re
import subprocess
import sys
from html import unescape

from lib.db import stage_save, stage_exists, setting_get, setting_set


def clean_html(html):
    html = re.sub(r'<(script|style)\b[^>]*>.*?</\1>', '', html, flags=re.DOTALL | re.IGNORECASE)
    link_pattern = re.compile(r'<a\s+(?:[^>]*?\s+)?href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.DOTALL | re.IGNORECASE)
    def link_replacer(match):
        url = match.group(1)
        text = re.sub(r'<[^>]+>', '', match.group(2).strip())
        return f" [{url}] {text} "
    html = link_pattern.sub(link_replacer, html)
    html = re.sub(r'<[^>]+>', ' ', html)
    text = unescape(html)
    text = re.sub(r'\s+', ' ', text)
    return text.encode('utf-8', errors='replace').decode('utf-8').strip()


def stage_emails(search_results_path):
    if not os.path.exists(search_results_path):
        print(f"Error: {search_results_path} not found.", file=sys.stderr)
        return

    with open(search_results_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    threads = data.get('threads', [])
    print(f"Found {len(threads)} threads in search results.", file=sys.stderr)

    staged = set(setting_get("staged_ids", []))

    count = 0
    skipped = 0
    new_staged = list(staged)

    for thread in threads:
        tid = thread['id']

        if tid in staged:
            skipped += 1
            continue

        if stage_exists(tid):
            new_staged.append(tid)
            skipped += 1
            continue

        try:
            r = subprocess.run(['gmail-cli', 'gmail', 'get', tid], capture_output=True, timeout=60, shell=True)
            if r.returncode != 0:
                print(f"  ERROR: {tid}", file=sys.stderr)
                continue
            cleaned = clean_html(r.stdout.decode('utf-8', errors='replace'))
            stage_save(tid, cleaned)
            new_staged.append(tid)
            count += 1
            print(f"  Staged: {tid}", file=sys.stderr)
        except subprocess.TimeoutExpired:
            print(f"  TIMEOUT: {tid}", file=sys.stderr)
        except Exception as e:
            print(f"  FAIL {tid}: {e}", file=sys.stderr)

    setting_set("staged_ids", new_staged)
    print(f"\nStaging complete. Staged: {count}, Skipped: {skipped}", file=sys.stderr)


if __name__ == "__main__":
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
    SEARCH_RESULTS = os.path.join(SCRIPT_DIR, "..", "..", "search_results_new.json")
    if not os.path.exists(SEARCH_RESULTS):
        SEARCH_RESULTS = os.path.join(SCRIPT_DIR, "..", "..", "search_results.json")

    stage_emails(SEARCH_RESULTS)
