"""Shared text-processing helpers for platform description cleaners."""

import re


def strip_header(text, markers):
    """Remove lines before (and including) the first line matching any marker."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        lower = line.strip().lower()
        for marker in markers:
            if marker in lower:
                return "\n".join(lines[i:])
    return text


def strip_footer(text, markers):
    """Remove everything from the first occurrence of any marker onward."""
    lower = text.lower()
    best = len(text)
    for marker in markers:
        idx = lower.find(marker)
        if idx != -1 and idx < best:
            best = idx
    if best < len(text):
        text = text[:best]
    return text


def remove_lines(text, patterns):
    """Remove entire lines matching any regex pattern."""
    return re.sub(
        r"(?im)^.*?(?:" + "|".join(patterns) + r").*$\n?",
        "", text,
    )


def collapse_blank_lines(text):
    """Replace 3+ consecutive newlines with 2."""
    return re.sub(r"\n{3,}", "\n\n", text)
