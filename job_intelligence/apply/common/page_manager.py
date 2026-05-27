"""Page registry — URL history stack + DOM tagging + content fingerprint.
Finds the right page for a job, detects what happened after actions."""
import json, os, time

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

    def find(self):
        """Find page by: tag → URL stack + fingerprint → untagged tabs."""
        # 1. Direct tag match
        for p in self.ctx.pages:
            if _get_tag(p) == self.jid:
                self.register(p)
                return p

        entry = self.reg.get(self.jid, {"urls": [], "fp": ""})
        url_stack = entry.get("urls", [])
        old_fp = entry.get("fp", "")

        # 2. URL stack match, scored
        best_score, best_page = -1, None
        for p in self.ctx.pages:
            url = p.url.rstrip("/")
            score = -1
            for stored in reversed(url_stack):
                s = stored.rstrip("/")
                if url == s:
                    score = 3; break
                elif url.startswith(s + "/"):
                    score = 2; break
                elif s.startswith(url + "/"):
                    score = 1; break
            if score > best_score:
                best_score, best_page = score, p

        if best_page:
            # Disambiguate: same URL, different tabs → fingerprint
            ties = [p for p in self.ctx.pages
                    if p.url.rstrip("/") == best_page.url.rstrip("/")]
            if len(ties) > 1 and old_fp:
                for p in ties:
                    if _fingerprint(p) == old_fp:
                        best_page = p; break
            _tag(best_page, self.jid)
            self.register(best_page)
            return best_page

        # 3. Untagged tab (safety redirect, user navigation)
        for p in self.ctx.pages:
            if not _get_tag(p):
                url = p.url.lower()
                if "about:blank" in url or "chrome-error" in url:
                    continue
                if self.jid[:12] in url:
                    self.register(p); return p
                for s in url_stack:
                    if s.rstrip("/") in url or url in s.rstrip("/"):
                        self.register(p); return p

        return None

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
