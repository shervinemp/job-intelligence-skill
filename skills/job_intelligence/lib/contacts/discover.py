"""lib/contacts/discover.py — Contact discovery for a job.

Orchestrates the multi-source discovery pipeline:
  1. Load job from DB
  2. Extract recruiters from job page (LinkedIn or ATS)
  3. Find company LinkedIn page
  4. Search for team members
  5. Find my connections at the company
  6. Extract the posting's hiring team (connection-independent)
  7. Read the Job Tracker 'Applied' list for surfaced people
  8. LLM email suggestion
"""

import json
import sys

from lib.chrome_manager import connect
from lib.db import get_conn, get_job, company_get, company_upsert
from lib.db.contacts import contact_list
from lib.linkedin_messaging import (
    search_company_employees,
    search_company_connections,
    _extract_posting_team,
    search_tracker_applied_people,
)

from ..ask_api import ask_text


def discover_contacts(jid, team_name=None, use_llm=True, use_browser=True):
    """Discover all contacts for a job.

    Returns dict with discovered contacts grouped by source:
        {
            "jid": str,
            "company": str,
            "company_linkedin_slug": str or None,
            "company_linkedin_id": str or None,
            "recruiters": [contact_dict],
            "team_members": [contact_dict],
            "my_connections": [contact_dict],
            "email_candidates": [email_dict],
        }
    """
    job = get_job(jid)
    if not job:
        return {"error": f"job {jid} not found"}

    company = job.get("company", "")
    title = job.get("title", "")
    url = job.get("url", "")
    job_team = job.get("team_name", "") or team_name or ""

    result = {
        "jid": jid,
        "company": company,
        "company_linkedin_slug": None,
        "company_linkedin_id": None,
        "recruiters": [],
        "team_members": [],
        "my_connections": [],
        "email_candidates": [],
    }

    if not company:
        return result

    # Look up or create company
    company_info = company_get(company)
    if company_info:
        result["company_linkedin_slug"] = company_info.get("source_url", "")
        result["company_linkedin_id"] = company_info.get("linkedin_id", "")
    else:
        try:
            company_upsert(company)
        except Exception:
            pass

    # Existing contacts already in DB
    existing = contact_list(job_id=jid)
    for c in existing:
        if c.get("source") == "recruiter_auto":
            result["recruiters"].append(c)
        elif c.get("source") in ("team_search", "tracker_search"):
            result["team_members"].append(c)
        elif c.get("source") == "my_connection":
            result["my_connections"].append(c)

    if not use_browser:
        return result

    b, ctx = connect(timeout=30)
    if not ctx:
        return result

    try:
        # Extract recruiter from job page (independent of company lookup)
        if "linkedin.com" in url:
            recruiter = _extract_recruiter_from_page(ctx, url)
            if recruiter:
                cid = _save_contact(jid, company, recruiter, source="recruiter_auto", confidence=0.8)
                result["recruiters"].append({**recruiter, "id": cid})

        # Posting-page hiring team — CONNECTION-INDEPENDENT (the 'Hiring
        # team'/'Meet the team' section lists the people recruiting for THIS
        # role whether or not they are in the network). Saved as team_search
        # so they dedup against the company people search.
        if "linkedin.com" in url:
            posting_team = _extract_posting_team(ctx, url)
            for tm in posting_team:
                cid = _save_contact(jid, company, tm, source="team_search", confidence=0.75)
                result["team_members"].append({**tm, "id": cid})

        # Job Tracker 'Applied' list — people surfaced on the user's applied
        # jobs (server-side record). Connection-independent.
        try:
            tracker_people = search_tracker_applied_people(ctx)
            for tp in tracker_people:
                cid = _save_contact(jid, company, tp, source="tracker_search", confidence=0.6)
                result["team_members"].append({**tp, "id": cid})
        except Exception as _te:
            print(f"TRACKER_SKIP: {_te}", file=sys.stderr)

        # Find company LinkedIn slug (verified) and numeric ID
        company_slug = result["company_linkedin_slug"] or _find_company_slug(ctx, company)
        if company_slug and company_slug != result["company_linkedin_slug"]:
            try:
                company_upsert(company, source_url=company_slug)
            except Exception:
                pass
            result["company_linkedin_slug"] = company_slug

        company_id = result["company_linkedin_id"]
        if company_id is None or company_id == "":
            if company_slug:
                from lib.linkedin_messaging import _resolve_company_id
                company_id = _resolve_company_id(ctx, company_slug)
                if company_id:
                    try:
                        company_upsert(company, linkedin_id=company_id)
                    except Exception:
                        pass
                    result["company_linkedin_id"] = company_id

        if company_slug:
            # Team members
            team_keywords = _build_team_keywords(job_team, title)
            if team_keywords:
                team = search_company_employees(ctx, company_slug, team_keywords=team_keywords)
                for emp in team:
                    cid = _save_contact(jid, company, emp, source="team_search", confidence=0.7)
                    result["team_members"].append({**emp, "id": cid})

            # My connections
            conns = search_company_connections(ctx, company_slug=company_slug, company_id=company_id)
            connections = conns.get("connections", []) if isinstance(conns, dict) else conns
            if isinstance(conns, dict) and conns.get("used") == "fallback":
                print(f"CONNECTIONS_FALLBACK: {conns.get('detail', '')}", file=sys.stderr)
            for conn in connections:
                cid = _save_contact(jid, company, conn, source="my_connection", confidence=0.9)
                result["my_connections"].append({**conn, "id": cid})

        # LLM email suggestion
        if use_llm:
            emails = _suggest_emails(company, result)
            result["email_candidates"] = emails

        # Mark job as contact_discovered only after a real browser attempt
        conn = get_conn()
        conn.execute("UPDATE jobs SET contact_discovered=1 WHERE id=?", (jid,))
        conn.commit()

    finally:
        try:
            b.close()
        except Exception:
            pass

    return result


def _find_company_slug(ctx, company_name):
    """Find a company's LinkedIn slug by searching LinkedIn.

    VERIFIED (2026-07-30): the search results layout has NO result-card
    class (no .org-company-search-card / .reusable-search__result-container);
    results are bare a[href*="/company/"] anchors inside div[role="list"].
    Verification: the company name must appear in the result's own text.
    """
    page = ctx.new_page()
    try:
        search_url = f"https://www.linkedin.com/search/results/companies/?keywords={company_name.replace(' ', '%20')}"
        try:
            page.goto(search_url, wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(3000)
        except Exception:
            return None

        js = """(companyName) => {
          const nameLower = (companyName || '').toLowerCase();
          const links = document.querySelectorAll('a[href*="/company/"]');
          for (const link of links) {
            const m = link.href.match(/\\/company\\/([^\\/?#]+)/);
            if (!m || !m[1]) continue;
            const slug = m[1];
            // Verify the company name against the result's own text.
            // New layout: the card IS the anchor; old layout: card is a
            // container around the anchor.
            const card = link.closest('[role="list"] > div, .org-company-search-card, .reusable-search__result-container');
            const scope = card || link;
            const text = (scope.textContent || '').toLowerCase();
            const linkText = (link.textContent || '').toLowerCase();
            if (linkText.includes(nameLower) || (nameLower && text.includes(nameLower))) {
              return slug;
            }
          }
          return null;
        }"""
        try:
            return page.evaluate(js, company_name.lower()[:40])
        except Exception:
            return None
    finally:
        try:
            page.close()
        except Exception:
            pass


def _build_team_keywords(team_name, job_title):
    """Build search keywords from team name and job title."""
    keywords = []
    if team_name:
        for part in team_name.replace("/", " ").split():
            clean = part.strip().lower()
            if clean and clean not in ("and", "the", "&"):
                keywords.append(clean)
    if not keywords and job_title:
        # Try to extract team indicators from job title
        common_teams = [
            ("designer", ["design", "ux", "ui"]),
            ("engineer", ["engineering", "software", "development"]),
            ("scientist", ["science", "data", "research", "ai", "ml"]),
            ("marketing", ["marketing", "growth"]),
            ("product", ["product", "management"]),
            ("sales", ["sales", "business"]),
        ]
        for word, team_kws in common_teams:
            if word in job_title.lower():
                keywords.extend(team_kws)
                break
    return keywords


def _extract_recruiter_from_page(ctx, url):
    """Extract recruiter info from a LinkedIn job detail page."""
    page = ctx.new_page()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(3000)

        js = """() => {
          const seen = new Set();
          const hiringSection = [...document.querySelectorAll('section, div')]
            .filter(el => /hiring team|meet the (hiring )?team|recruiter/i.test(el.getAttribute('aria-label') || ''))
            .filter(el => el.querySelector('a[href*="/in/"]'));
          for (const section of hiringSection) {
            const links = section.querySelectorAll('a[href*="/in/"]');
            for (const link of links) {
              const href = link.href || '';
              if (!href.includes('/in/')) continue;
              const name = (link.textContent || '').trim().replace(/\\s+/g, ' ');
              if (!name || name.length < 2 || seen.has(href)) continue;
              seen.add(href);
              let role = '';
              let parent = link.closest('li, div, .artdeco-entity-lockup, .hire-pipeline-card');
              if (parent) {
                const roleEl = parent.querySelector('.artdeco-entity-lockup__subtitle, [class*="subtitle"], [class*="role"]');
                if (roleEl) role = (roleEl.textContent || '').trim();
              }
              return { name, role, linkedin_url: href };
            }
          }
          const allLinks = document.querySelectorAll('a[href*="/in/"]');
          for (const link of allLinks) {
            const href = link.href || '';
            if (seen.has(href)) continue;
            const name = (link.textContent || '').trim().replace(/\\s+/g, ' ');
            if (!name || name.length < 2) continue;
            let parent = link.parentElement;
            let context = '';
            for (let i = 0; i < 3 && parent; i++) {
              context += ' ' + (parent.textContent || '');
              parent = parent.parentElement;
            }
            if (/recruiter|hiring|talent|recruit/i.test(context)) {
              return { name, role: '', linkedin_url: href };
            }
          }
          return null;
        }"""
        try:
            return page.evaluate(js)
        except Exception:
            return None
    finally:
        try:
            page.close()
        except Exception:
            pass


def _save_contact(jid, company, person, source="team_search", confidence=0.5):
    """Save a discovered contact to the DB. Dedupes by linkedin_url, or by
    name when no URL is present."""
    conn = get_conn()
    linkedin_url = person.get("linkedin_url", "")
    existing = None
    if linkedin_url:
        existing = conn.execute(
            "SELECT id FROM contacts WHERE job_id=? AND linkedin_url=?",
            (jid, linkedin_url),
        ).fetchone()
    else:
        name = person.get("name", "")
        if name:
            existing = conn.execute(
                "SELECT id FROM contacts WHERE job_id=? AND name=? AND (linkedin_url='' OR linkedin_url IS NULL)",
                (jid, name),
            ).fetchone()
    if existing:
        return existing["id"]

    cursor = conn.execute(
        """INSERT INTO contacts
           (job_id, name, role, linkedin_url, headline, connection_degree,
            source, confidence, notes)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            jid,
            person.get("name", ""),
            person.get("role", ""),
            person.get("linkedin_url", ""),
            person.get("headline", ""),
            person.get("connection_degree", ""),
            source,
            confidence,
            f"Auto-discovered via {source}",
        ),
    )
    conn.commit()
    return cursor.lastrowid


def _suggest_emails(company, discovery_result):
    """Use LLM to suggest possible email formats for discovered contacts."""
    all_contacts = (
        discovery_result.get("recruiters", [])
        + discovery_result.get("team_members", [])
        + discovery_result.get("my_connections", [])
    )
    if not all_contacts:
        return []

    prompt = (
        f"Company: {discovery_result.get('company', '')}\n\n"
        "For each person below, suggest the most likely work email address "
        "based on common corporate email patterns "
        "(e.g., first@company.com, first.last@company.com, "
        "firstinitiallast@company.com). "
        "Return JSON array: [{\"name\": \"...\", \"suggested_emails\": [...], \"confidence\": 0.0-1.0}]\n\n"
        "People:\n"
    )
    for c in all_contacts:
        prompt += f"- {c.get('name', '')} ({c.get('role', '')})\n"

    reply, err = ask_text(prompt, temperature=0.2, max_tokens=1024)
    if err or not reply:
        return []

    try:
        candidates = json.loads(reply)
        if isinstance(candidates, list):
            return candidates
    except (json.JSONDecodeError, TypeError):
        pass

    return []
