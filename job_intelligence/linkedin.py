#!/usr/bin/env python3
"""linkedin.py — Scrape LinkedIn jobs into the pipeline.

Usage:
  python3 linkedin.py [--url <url>] [--max N] [--list]

Scrapes job cards, clicks each for full description, paginates for more.
Adds to DB as 'extracted' with description pre-saved (skips fetch.py).
"""

import hashlib
import re
import sys

from playwright.sync_api import TimeoutError

from lib.chrome_manager import connect
from lib.db import add_job, desc_get, desc_save, get_conn

DEFAULT_URL = "https://www.linkedin.com/jobs/collections/recommended/"


def _scroll_load(page, idle_timeout=5):
    last_count = 0
    while True:
        page.evaluate('''() => {
            const card = document.querySelector('.job-card-container');
            if (!card) return;
            let el = card.parentElement;
            while (el) {
                const style = window.getComputedStyle(el);
                if (style.overflowY === 'auto' || style.overflowY === 'scroll') {
                    el.scrollTop = el.scrollHeight;
                    el.dispatchEvent(new Event('scroll'));
                    return;
                }
                el = el.parentElement;
            }
        }''')
        try:
            page.wait_for_function(
                f"document.querySelectorAll('.job-card-container').length > {last_count}",
                timeout=idle_timeout * 1000
            )
        except TimeoutError:
            break
        last_count = len(page.query_selector_all('.job-card-container'))
    return page.query_selector_all('.job-card-container')


def _parse_card(card):
    el = card.query_selector('.title-text, .artdeco-entity-lockup__title a')
    title = re.sub(r'\s+', ' ', (el.text_content() or '')).strip() if el else ''
    if title:
        parts = [p.strip() for p in title.replace('\xa0', ' ').split('\n') if p.strip()]
        title = parts[0] if parts else title
    el = card.query_selector('.artdeco-entity-lockup__subtitle')
    company = (el.inner_text() or '').strip() if el else ''
    el = card.query_selector('.artdeco-entity-lockup__caption')
    location = (el.inner_text() or '').strip() if el else ''
    return {"title": title, "company": company, "location": location}


def scrape_linkedin(page_url, max_jobs=None, max_pages=20):
    b, ctx = connect(timeout=30)
    if not ctx:
        print("ERROR: Could not connect to Chrome.", file=sys.stderr)
        sys.exit(1)

    page = ctx.new_page()
    try:
        page.goto(page_url, wait_until='domcontentloaded', timeout=30000)
        try:
            page.wait_for_selector('.job-card-container', timeout=15000)
        except TimeoutError:
            pass

        if 'Sign in' in (page.evaluate('document.body.innerText') or '')[:500]:
            print("ERROR: Not signed in to LinkedIn.", file=sys.stderr)
            return

        count = 0
        page_num = 0
        while max_jobs is None or count < max_jobs:
            page_num += 1
            cards = _scroll_load(page)
            if not cards:
                break
            for card in cards:
                try:
                    job_id = card.get_attribute('data-job-id') or ''
                    if not job_id:
                        continue
                    job_url = f'https://www.linkedin.com/jobs/view/{job_id}/'
                    jid = hashlib.md5(job_url.encode()).hexdigest()[:16]
                    existing = get_conn().execute("SELECT id FROM jobs WHERE id=?", (jid,)).fetchone()
                    if existing:
                        if desc_get(jid):
                            continue
                        # Exists but no description — re-process to fill it
                    parsed = _parse_card(card)
                    add_job({"url": job_url, "title": parsed["title"], "company": parsed["company"],
                             "location": parsed["location"], "source": "LinkedIn", "source_url": job_url})
                    card.click()
                    try:
                        page.wait_for_function(
                            "document.querySelector('.job-details-jobs-unified-top-card__job-title')?.innerText?.trim()?.length > 0",
                            timeout=5000
                        )
                    except TimeoutError:
                        pass
                    pane = page.query_selector('.jobs-search__job-details--container')
                    if pane:
                        desc = pane.inner_text() or ''
                        for cutoff in ['Similar jobs', 'People also viewed']:
                            ci = desc.find(cutoff)
                            if ci >= 0:
                                desc = desc[:ci]
                        desc_save(jid, desc.strip()[:8000])
                    print(f"JOB:{jid}:{job_url}  [{parsed['title'] or '?'} @ {parsed['company'] or '?'} - {parsed['location'] or '?'}]")
                    count += 1
                except Exception as e:
                    print(f"WARN: {e}", file=sys.stderr)
                    continue

            print(f"PAGE {page_num}: {count} total", file=sys.stderr)
            if page_num >= max_pages:
                break
            next_btn = page.query_selector('button[aria-label="View next page"]')
            if not next_btn:
                break
            next_btn.click()
            try:
                page.wait_for_function(
                    "document.querySelectorAll('.job-card-container').length > 0",
                    timeout=10000
                )
            except TimeoutError:
                break

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
        try:
            page.wait_for_selector('.job-card-container', timeout=15000)
        except TimeoutError:
            pass
        cards = _scroll_load(page)
        print(f"Found {len(cards)} job cards", file=sys.stderr)
        for card in cards[:10]:
            job_id = card.get_attribute('data-job-id') or ''
            parsed = _parse_card(card)
            print(f"  {parsed['title'] or '?'} @ {parsed['company'] or '?'} ({parsed['location'] or '?'}) [{job_id}]")
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
