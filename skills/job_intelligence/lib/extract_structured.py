"""Structured data extraction from page HTML."""
import json
import re


def _num(v):
    """Coerce a JSON-LD numeric-ish value (int/float or string like '85,000') to a
    number, or None. JSON-LD salary fields are often strings, which would crash the
    ',' format spec."""
    if isinstance(v, (int, float)):
        return v
    try:
        return float(re.sub(r"[,$\s]", "", str(v)))
    except (ValueError, TypeError):
        return None


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
        # Unwrap @graph if present, else handle list / single object
        if isinstance(data, dict) and isinstance(data.get("@graph"), list):
            items = data["@graph"]
        else:
            items = data if isinstance(data, list) else [data]
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
                        min_v = _num(val.get("minValue") or val.get("value"))
                        max_v = _num(val.get("maxValue"))
                        currency = val.get("currency", salary.get("currency", ""))
                        if min_v is not None:
                            result["salary"] = f"${min_v:,.0f}"
                            if max_v is not None:
                                result["salary"] += f" - ${max_v:,.0f}"
                            if currency:
                                result["salary"] += f" {currency}"
                results.append(result)
    return results
