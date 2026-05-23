#!/usr/bin/env python3
"""linkedin.py — Scrape LinkedIn jobs into the pipeline.

Usage:
  python3 linkedin.py [--url <url>] [--max N] [--list]

Default: Canada-wide job search results.
Scrapes card data (title, company, location), clicks each to extract full
description from the right pane. Adds to DB as 'extracted' with description
pre-saved — skips fetch.py, goes straight to admit/reject then tailor.py.
"""

import hashlib
import os
import re
import sys
import time

from lib.chrome_manager import connect
from lib.db import add_job, desc_save, get_conn

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_URL = "https://www.linkedin.com/jobs/search/?f_AL=true&geoId=101174742"


def _scroll_load(page, max_cards=None):
    last_count = 0
    stale_rounds = 0
    for _ in range(30):
        page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
        time.sleep(2)
        cards = page.query_selector_all('.job-card-container')
        count = len(cards)
        if max_cards and count >= max_cards:
            return cards[:max_cards]
        if count == last_count:
            stale_rounds += 1
            if stale_rounds >= 3:
                break
        else:
            stale_rounds = 0
        last_count = count
    return cards


def scrape_linkedin(page_url, max_jobs=None):
    b, ctx = connect(timeout=30)
    if not ctx:
        print("ERROR: Could not connect to Chrome.", file=sys.stderr)
        sys.exit(1)

    page = ctx.new_page()
    try:
        page.goto(page_url, wait_until='domcontentloaded', timeout=30000)
        time.sleep(3)

        if 'Sign in' in (page.evaluate('document.body.innerText') or '')[:500]:
            print("ERROR: Not signed in to LinkedIn.", file=sys.stderr)
            return

        cards = _scroll_load(page, max_cards=max_jobs)
        count = 0
        for card in cards:
            try:
                job_id = card.get_attribute('data-job-id') or ''
                if not job_id:
                    continue
                job_url = f'https://www.linkedin.com/jobs/view/{job_id}/'

                jid = hashlib.md5(job_url.encode()).hexdigest()[:16]
                conn = get_conn()
                if conn.execute("SELECT 1 FROM jobs WHERE id=?", (jid,)).fetchone():
                    continue

                el = card.query_selector('.title-text, .artdeco-entity-lockup__title a')
                title = re.sub(r'\s+', ' ', (el.text_content() or '')).strip() if el else ''
                # Take first segment if text is duplicated (LinkedIn puts truncated + full text)
                if title:
                    parts = [p.strip() for p in title.replace('\xa0', ' ').split('\n') if p.strip()]
                    title = parts[0] if parts else title
                el = card.query_selector('.artdeco-entity-lockup__subtitle')
                company = (el.inner_text() or '').strip() if el else ''
                el = card.query_selector('.artdeco-entity-lockup__caption')
                location = (el.inner_text() or '').strip() if el else ''

                add_job({
                    "url": job_url,
                    "title": title,
                    "company": company,
                    "location": location,
                    "source": "LinkedIn",
                    "source_url": job_url,
                })

                # Click card to load full description in right pane
                card.click()
                time.sleep(2)
                pane = page.query_selector('.jobs-search__job-details--container')
                if pane:
                    text = pane.inner_text() or ''
                    # Find description start — first meaningful section header
                    desc_start = -1
                    for marker in ['About the job', 'About the company', 'Company Description']:
                        i = text.find(marker)
                        if i >= 0 and (desc_start < 0 or i < desc_start):
                            desc_start = i
                    if desc_start >= 0:
                        desc = text[desc_start:]
                        for cutoff in ['Similar jobs', 'People also viewed']:
                            ci = desc.find(cutoff)
                            if ci >= 0:
                                desc = desc[:ci]
                        desc_save(jid, desc.strip()[:8000])
                print(f"JOB:{jid}:{job_url}  [{title or '?'} @ {company or '?'} - {location or '?'}]")
                count += 1
            except Exception as e:
                print(f"WARN: {e}", file=sys.stderr)
                continue

        print(f"SCRAPED:{count}", file=sys.stderr)

    finally:
        try:
            page.close()
        except Exception:
            pass
        try:
            b.close()
        except Exception:
            pass


def cmd_list():
    b, ctx = connect(timeout=30)
    if not ctx:
        print("ERROR: Could not connect to Chrome.", file=sys.stderr)
        sys.exit(1)
    page = ctx.new_page()
    try:
        page.goto(DEFAULT_URL, wait_until='domcontentloaded', timeout=30000)
        time.sleep(3)
        cards = _scroll_load(page)
        print(f"Found {len(cards)} job cards", file=sys.stderr)
        for card in cards[:10]:
            job_id = card.get_attribute('data-job-id') or ''
            el = card.query_selector('.title-text, .artdeco-entity-lockup__title a')
            title = re.sub(r'\s+', ' ', (el.text_content() or '')).strip() if el else '?'
            if title:
                parts = [p.strip() for p in title.replace('\xa0', ' ').split('\n') if p.strip()]
                title = parts[0] if parts else title
            el = card.query_selector('.artdeco-entity-lockup__subtitle')
            company = (el.inner_text() or '').strip() if el else '?'
            el = card.query_selector('.artdeco-entity-lockup__caption')
            loc = (el.inner_text() or '').strip() if el else '?'
            print(f"  {title} @ {company} ({loc}) [{job_id}]")
    finally:
        try:
            page.close()
        except Exception:
            pass
        try:
            b.close()
        except Exception:
            pass


if __name__ == "__main__":
    url = DEFAULT_URL
    max_jobs = None
    list_only = False

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == '--url' and i + 1 < len(args):
            url = args[i + 1]
            i += 2
        elif args[i] == '--max' and i + 1 < len(args):
            max_jobs = int(args[i + 1])
            i += 2
        elif args[i] == '--list':
            list_only = True
            i += 1
        else:
            i += 1

    if list_only:
        cmd_list()
    else:
        scrape_linkedin(url, max_jobs)
