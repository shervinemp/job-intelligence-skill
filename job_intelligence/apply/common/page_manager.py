"""Page registry — URL history stack + DOM tagging + content fingerprint.
Finds the right page for a job, detects what happened after actions."""
import json, os, time
from urllib.parse import urlparse

REGISTRY_PATH = os.path.join(os.path.expanduser("~"), ".openclaw", "page_registry.json")

def _load():
    try:
        with open(REGISTRY_PATH) as f:
            return json.load(f)
    except:
        return {}

def _save(r):
    os.makedirs(os.path.dirname(REGISTRY_PATH), exist_ok=True)
    with open(REGISTRY_PATH, "w") as f:
        json.dump(r, f, indent=2)

def _fingerprint(page):
    """First 100 chars of body — enough to distinguish login vs form vs results."""
    try:
        t = (page.evaluate("() => (document.body.innerText || '').trim().slice(0, 100)") or "")
        return t
    except:
        return ""

def _tag(page, jid):
    try:
        page.evaluate(f"(jid) => document.body.setAttribute('data-job-id', jid)", jid)
    except:
        pass

def _get_tag(page):
    try:
        return page.evaluate("() => document.body.getAttribute('data-job-id') || ''") or ""
    except:
        return ""

class PageManager:
    def __init__(self, ctx, jid):
        self.ctx = ctx
        self.jid = jid
        self.reg = _load()
        if jid not in self.reg:
            self.reg[jid] = {"urls": [], "fp": ""}

    def register(self, page):
        """Tag the page and record its URL + fingerprint."""
        fp = _fingerprint(page)
        url = page.url
        _tag(page, self.jid)
        entry = self.reg.setdefault(self.jid, {"urls": [], "fp": ""})
        if not entry["urls"] or entry["urls"][-1] != url:
            entry["urls"].append(url)
            if len(entry["urls"]) > 10:
                entry["urls"] = entry["urls"][-10:]
        entry["fp"] = fp
        _save(self.reg)

    def find(self, fallback_url=""):
        """Find page for this JID. Returns (page, candidates, confidence).
        confidence: 'tagged', 'domain', 'multiple', or None."""
        fallback_domain = urlparse(fallback_url).netloc.lower() if fallback_url else ""

        tagged = None
        domain_matches = []

        for p in self.ctx.pages:
            url = p.url.lower()
            if "about:blank" in url or "chrome-error" in url:
                continue

            tag = _get_tag(p)
            if tag == self.jid:
                tagged = p
                break

            if not tag and fallback_domain:
                try:
                    if urlparse(url).netloc.lower() == fallback_domain:
                        domain_matches.append(p)
                except:
                    pass

        if tagged:
            self.register(tagged)
            return tagged, [], "tagged"

        if len(domain_matches) == 1:
            self.register(domain_matches[0])
            return domain_matches[0], [], "domain"

        if domain_matches:
            # Multiple candidates — return them all for model to pick
            for p in domain_matches:
                try:
                    fp = (p.evaluate("() => (document.body.innerText || '').trim().slice(0, 60)") or "")[:60]
                except:
                    fp = ""
                self.reg.setdefault("_candidates", []).append({"url": p.url, "fp": fp})
            _save(self.reg)
            return None, domain_matches, "multiple"

        return None, [], None

    def _score(self, page, url_stack, fallback_domain):
        """Calculate match score for a single page."""
        url = page.url.lower()
        tag = _get_tag(page)
        score = 0
        if tag == self.jid:
            score = 3
        for stored in reversed(url_stack):
            s = stored.rstrip("/").lower()
            if url.rstrip("/") == s:
                score = max(score, 2); break
            elif url.rstrip("/").startswith(s + "/"):
                score = max(score, 2); break
            elif s.startswith(url.rstrip("/") + "/"):
                score = max(score, 1); break
        try:
            if fallback_domain and urlparse(url).netloc.lower() == fallback_domain:
                score = max(score, 1)
        except:
            pass
        return score

    def snapshot(self, page):
        """Return a dict describing the current page state for change detection."""
        return {
            "url": page.url,
            "fp": _fingerprint(page),
            "tag": _get_tag(page),
        }

    def diff(self, before, after=None, page=None):
        """Return a human-readable CHANGE report. Call before + after an action.
        If `after` is None, compares `before` to current state of `page`."""
        if after is None and page is not None:
            after = {"url": page.url, "fp": _fingerprint(page), "tag": _get_tag(page)}
        elif after is None:
            return {}

        changes = []
        if before["url"] != after["url"]:
            changes.append(f"URL: {before['url'][:60]} → {after['url'][:60]}")
        if before["fp"] != after["fp"] and before["url"] == after["url"]:
            changes.append("Content changed (SPA update or reload)")
        if before["tag"] != after["tag"]:
            changes.append(f"Tag: {before['tag']} → {after['tag']}")

        # Check for new tabs
        if page:
            before_count = len(self.ctx.pages)
        # We don't have before count, so only report if tag changed to this JID
        if after.get("tag") == self.jid and before.get("tag") != self.jid:
            changes.append("Page newly tagged (was untagged or different job)")

        return {"changes": changes, "after_url": after["url"][:80], "after_fp": after["fp"][:50]}

    def find_new_tab(self, url_pattern=None):
        """Find a newly opened tab that isn't tagged and matches a pattern."""
        for p in self.ctx.pages:
            if not _get_tag(p):
                url = p.url.lower()
                if "about:blank" in url or "chrome-error" in url:
                    continue
                if url_pattern and url_pattern.lower() in url:
                    self.register(p)
                    return p
        return None

    def same_page(self, snap):
        """True if fingerprint hasn't changed (stuck detection)."""
        for p in self.ctx.pages:
            if _get_tag(p) == self.jid:
                return _fingerprint(p) == snap.get("fp", "")
        return False

    def next_page(self, page, timeout=8):
        """After clicking, wait for navigation/SPA update. Returns new page or None."""
        import time
        snap = self.snapshot(page)
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(1)
            # Check URL change on current page
            if page.url != snap["url"]:
                self.register(page); return page
            # Check fingerprint change (SPA)
            if _fingerprint(page) != snap["fp"]:
                self.register(page); return page
            # Check for new tab
            new_tab = self.find_new_tab()
            if new_tab:
                return new_tab
        return page  # timeout — no change detected

    def cleanup_all(self):
        """Close untagged blank/error tabs. Leave tagged ones alone."""
        for p in self.ctx.pages:
            if not _get_tag(p):
                url = p.url.lower()
                if "about:blank" in url or "chrome-error" in url or "newtab" in url:
                    try:
                        p.close()
                    except:
                        pass

    def close_others(self, keep_page):
        """Close all pages tagged with our JID except keep_page."""
        for p in self.ctx.pages:
            if p != keep_page and _get_tag(p) == self.jid:
                try:
                    p.close()
                except:
                    pass

    def cleanup(self):
        """Remove stale entries (closed pages)."""
        active_tags = set()
        for p in self.ctx.pages:
            t = _get_tag(p)
            if t:
                active_tags.add(t)
        for jid in list(self.reg.keys()):
            entry = self.reg[jid]
            urls = entry.get("urls", [])
            if jid not in active_tags:
                still_open = any(
                    any(s in p.url for s in urls)
                    for p in self.ctx.pages
                )
                if not still_open:
                    del self.reg[jid]
        _save(self.reg)
