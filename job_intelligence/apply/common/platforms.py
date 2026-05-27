"""Platform-specific patterns for common ATS systems.
Used by the apply pipeline to detect already-applied, login walls, etc.
"""
import re

# Per-platform patterns for "already applied" text
ALREADY_APPLIED = {
    "ashby": [
        "you have already applied",
        "already applied",
    ],
    "greenhouse": [
        "you have already applied for this role",
        "you've already applied",
        "already applied",
    ],
    "lever": [
        "already applied",
        "you have already applied",
    ],
    "workday": [
        "already applied",
        "you have already submitted an application",
        "previously applied",
    ],
    "icims": [
        "already applied",
        "submitted application",
    ],
    "taleo": [
        "already applied",
        "already submitted",
    ],
    "default": [
        "already applied",
        "you have already applied",
        "application received",
    ],
}

LOGIN_WALL = {
    "default": [
        "sign in to view",
        "please sign in",
        "sign in to continue",
        "create account",
        "join now to apply",
        "sign in with email",
        "log in to apply",
    ],
    "workday": [
        "sign in to apply",
        "create account",
        "sign in with email",
    ],
    "greenhouse": [
        "sign in to apply",
        "already have an account",
    ],
}

GUEST_APPLY = {
    "default": [
        "continue without signing in",
        "apply as guest",
        "continue as guest",
        "skip for now",
        "apply without signing in",
    ],
    "workday": [
        "continue without signing in",
        "apply as guest",
        "apply manually",
    ],
}

PLATFORM_LABELS = {
    "ashby": "Ashby",
    "greenhouse": "Greenhouse",
    "lever": "Lever",
    "workday": "Workday",
    "icims": "iCIMS",
    "taleo": "Taleo",
}


def check_page(text, platform, patterns_dict):
    """Check if page text matches any pattern for a platform."""
    patterns = patterns_dict.get(platform) or patterns_dict.get("default", [])
    text_lower = text.lower()
    for p in patterns:
        if p in text_lower:
            return True
    return False


def detect_platform(url):
    """Detect ATS platform from URL."""
    host = url.split("/")[2] if "//" in url else ""
    for kw, plat in [
        ("greenhouse", "greenhouse"), ("lever.co", "lever"),
        ("myworkdayjobs", "workday"), ("workday.com", "workday"),
        ("ashbyhq", "ashby"), ("icims", "icims"), ("taleo", "taleo"),
        ("smartrecruiters", "smartrecruiters"), ("bamboohr", "bamboohr"),
    ]:
        if kw in host or kw in url:
            return plat
    return None
