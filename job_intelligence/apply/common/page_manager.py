"""Page registry — DOM tagging + URL tracking.
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

from apply.common.page_helpers import _PAGE_JID_MAP as _page_map

def _fingerprint(page):
    try:
        return (page.evaluate("() => (document.body.innerText || '').trim().slice(0, 100)") or "")
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
        """Track the page and record its URL + fingerprint."""
        fp = _fingerprint(page)
        url = page.url
        _page_map[id(page)] = self.jid
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

            if _page_map.get(id(p)) == self.jid:
                tagged = p
                break

            if fallback_domain:
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
            # Multiple candidates — pick the one whose URL is closest to fallback_url
            if fallback_url:
                best_match = max(domain_matches, key=lambda p: len(os.path.commonprefix([p.url.lower(), fallback_url.lower()])))
                self.register(best_match)
                return best_match, [], "domain"
            return None, domain_matches, "multiple"

        return None, [], None

    def snapshot(self, page):
        return {"url": page.url, "fp": _fingerprint(page), "tag": _page_map.get(id(page), "")}

    def diff(self, before, after=None, page=None):
        if after is None and page is not None:
            after = {"url": page.url, "fp": _fingerprint(page), "tag": _page_map.get(id(page), "")}
        elif after is None:
            return {}
        changes = []
        if before["url"] != after["url"]:
            changes.append(f"URL: {before['url'][:60]} → {after['url'][:60]}")
        if before["fp"] != after["fp"] and before["url"] == after["url"]:
            changes.append("Content changed (SPA update or reload)")
        if before["tag"] != after["tag"]:
            changes.append(f"Tag: {before['tag']} → {after['tag']}")
        if after.get("tag") == self.jid and before.get("tag") != self.jid:
            changes.append("Page newly tagged (was untagged or different job)")
        return {"changes": changes, "after_url": after["url"][:80], "after_fp": after["fp"][:50]}

    def find_new_tab(self, url_pattern=None):
        for p in self.ctx.pages:
            if not _page_map.get(id(p)):
                url = p.url.lower()
                if "about:blank" in url or "chrome-error" in url:
                    continue
                if url_pattern and url_pattern.lower() in url:
                    self.register(p); return p
        return None

    def cleanup_all(self):
        for p in self.ctx.pages:
            if not _page_map.get(id(p)):
                url = p.url.lower()
                if "about:blank" in url or "chrome-error" in url or "newtab" in url:
                    try: p.close()
                    except: pass

    def close_others(self, keep_page):
        keep_url = keep_page.url.rstrip("/")
        for p in self.ctx.pages:
            if p != keep_page and _page_map.get(id(p)) == self.jid:
                try: p.close()
                except: pass
