"""Apply pipeline package: detect, navigate, act, verify.

Marks `apply/` as a regular package so `apply.py` (the CLI entrypoint) can do
`from apply.detect import run`. Without this file the sibling `apply.py` module
shadows this directory and the imports fail with "'apply' is not a package".
"""
