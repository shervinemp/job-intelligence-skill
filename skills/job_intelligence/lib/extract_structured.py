"""Structured data extraction from page HTML."""
import json
import re


def extract_job_postings(html):
    """Extract JobPosting structured data from JSON-LD in HTML.
    Returns list of dicts with title, company, location, salary."""
    results = []
    for m in re.finditer(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.DOTALL | re.IGNORECASE
    ):
        try:
            data = json.loads(m.group(1).strip())
        except (json.JSONDecodeError, AttributeError):
            continue
        # Handle @graph wrapper: {"@context":..., "@graph":[...]}
        if isinstance(data, dict) and "@graph" in data:
            items = data["@graph"] if isinstance(data["@graph"], list) else [data["@graph"]]
        elif isinstance(data, list):
            items = data
        else:
            items = [data]
        for item in items:
            if isinstance(item, dict) and item.get("@type") == "JobPosting":
                result = {}
                title = item.get("title", "")
                if title:
                    result["title"] = title.strip()[:200]
                org = item.get("hiringOrganization", {})
                if isinstance(org, dict):
                    result["company"] = org.get("name", "").strip()[:200]
                elif isinstance(org, str):
                    result["company"] = org.strip()[:200]
                location = item.get("jobLocation", {})
                if isinstance(location, dict):
                    addr = location.get("address", {})
                    if isinstance(addr, dict):
                        result["location"] = addr.get("addressLocality", "").strip()[:200]
                    elif isinstance(addr, str):
                        result["location"] = addr.strip()[:200]
                salary = item.get("baseSalary", {})
                if isinstance(salary, dict):
                    val = salary.get("value", {})
                    if isinstance(val, dict):
                        min_v = val.get("minValue") or val.get("value")
                        max_v = val.get("maxValue")
                        currency = val.get("currency", salary.get("currency", ""))
                        if min_v:
                            try:
                                min_v = int(min_v) if not isinstance(min_v, float) else min_v
                            except (ValueError, TypeError):
                                pass
                            result["salary"] = f"${min_v:,}"
                            if max_v:
                                try:
                                    max_v = int(max_v) if not isinstance(max_v, float) else max_v
                                except (ValueError, TypeError):
                                    pass
                                result["salary"] += f" - ${max_v:,}"
                            if currency:
                                result["salary"] += f" {currency}"
                results.append(result)
    return results
