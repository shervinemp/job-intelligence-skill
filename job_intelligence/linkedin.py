#!/usr/bin/env python3
"""linkedin.py — Scrape LinkedIn jobs into the pipeline.

Usage:
  python3 linkedin.py [--url <url>] [--max N] [--max-pages N] [--list]

Scrapes job cards, clicks each for full description, paginates for more.
Adds to DB as 'extracted' with description pre-saved (skips fetch.py).
"""

import hashlib
import re
import sys
import time

from playwright.sync_api import TimeoutError, expect

from lib.chrome_manager import connect
from lib.db import add_job, desc_get, desc_save, get_conn
from lib.platforms import clean as clean_desc

DEFAULT_URL = "https://www.linkedin.com/jobs/collections/recommended/"
DEFAULT_MAX_PAGES = 20


def _scroll_load(page, idle_timeout=3):
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
        import time
        deadline = time.time() + idle_timeout
        card_count = last_count
        while time.time() < deadline:
            count = page.evaluate("() => document.querySelectorAll('.job-card-container').length")
            if count > card_count:
                card_count = count
                break
            time.sleep(1)
        if card_count <= last_count:
            break
        last_count = len(page.query_selector_all('.job-card-container'))
    return page.query_selector_all('.job-card-container')


def _parse_card(card):
    el = card.query_selector('.title-text, .artdeco-entity-lockup__title a')
    title = re.sub(r'\s+', ' ', (el.text_content() or '')).strip() if el else ''
    if title:
        parts = [p.strip() for p in title.replace('\xa0', ' ').split('\n') if p.strip()]
        title = parts[0] if parts else title
        # Dedup: first half equals second half (LinkedIn duplicates title in DOM)
        half = len(title) // 2
        if len(title) > 20 and title[:half] == title[half:]:
            title = title[:half].strip()
    el = card.query_selector('.artdeco-entity-lockup__subtitle')
    company = (el.inner_text() or '').strip() if el else ''
    el = card.query_selector('.artdeco-entity-lockup__caption')
    location = (el.inner_text() or '').strip() if el else ''
    return {"title": title, "company": company, "location": location}


def scrape_linkedin(page_url, max_jobs=None, max_pages=DEFAULT_MAX_PAGES):
    b, ctx = connect(timeout=30)
    if not ctx:
        print("ERROR: Could not connect to Chrome.", file=sys.stderr)
        sys.exit(1)

    page = ctx.new_page()
    try:
        page.goto(page_url, wait_until='domcontentloaded', timeout=30000)
        try:
            page.wait_for_selector('.job-card-container', timeout=8000)
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
            cards_data = []
            for card in cards:
                cid = card.get_attribute('data-job-id') or ''
                if not cid:
                    continue
                cards_data.append({
                    'id': cid,
                    'url': f'https://www.linkedin.com/jobs/view/{cid}/',
                    'jid': hashlib.md5(f'https://www.linkedin.com/jobs/view/{cid}/'.encode()).hexdigest()[:16],
                    'parsed': _parse_card(card),
                })

            for cd in cards_data:
                try:
                    job_url = cd['url']
                    parsed = cd['parsed']
                    actual_jid = add_job({"url": job_url, "title": parsed["title"], "company": parsed["company"],
                                          "location": parsed["location"], "source": "LinkedIn", "source_url": job_url,
                                          "category": "tech"})
                    if not actual_jid:
                        continue
                    jid = actual_jid
                    page.locator(f'.job-card-container[data-job-id="{cd["id"]}"]').first.click()
                    page.wait_for_timeout(500)
                    for _ in range(3):
                        current = page.evaluate("() => new URLSearchParams(location.search).get('currentJobId')")
                        if current == cd['id']:
                            break
                        page.wait_for_timeout(1000)
                        page.locator(f'.job-card-container[data-job-id="{cd["id"]}"]').first.click()
                        page.wait_for_timeout(500)
                    page.wait_for_timeout(1500)
                    desc = ""
                    for _ in range(4):
                        dl_deadline = time.time() + 5
                        while time.time() < dl_deadline:
                            ready = page.evaluate(
                                "() => (document.querySelector('#job-details .mt4')?.innerText?.trim()?.length || 0) > 100"
                            )
                            if ready:
                                pane = page.query_selector('.jobs-search__job-details--container')
                                if pane:
                                    desc = (pane.inner_text() or '').strip()[:8000]
                                break
                            time.sleep(0.5)
                        if desc:
                            break
                    if desc:
                        desc = clean_desc(job_url, desc)
                        desc_save(jid, desc)
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
            pg_deadline = time.time() + 5
            while time.time() < pg_deadline:
                if page.evaluate("() => document.querySelectorAll('.job-card-container').length > 0"):
                    break
                time.sleep(0.5)

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
            page.wait_for_selector('.job-card-container', timeout=8000)
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
    import argparse
    parser = argparse.ArgumentParser(prog="linkedin.py", description="Scrape LinkedIn jobs")
    parser.add_argument("--url", default=DEFAULT_URL, help="LinkedIn search URL")
    parser.add_argument("--max", type=int, default=None, help="Max jobs to scrape")
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES, help="Max pages")
    parser.add_argument("--list", action="store_true", help="List jobs without scraping")
    args = parser.parse_args()

    if args.list:
        cmd_list()
    else:
        scrape_linkedin(args.url, args.max, args.max_pages)
