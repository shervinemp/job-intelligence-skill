"""apply.core — Pipeline action primitives.

Each module handles one major action:
  fill.py     → cmd_fill, _fill_text, _fill_field_deterministic
  submit.py   → cmd_submit, cmd_next, cmd_back
  inspect.py  → cmd_inspect

Currently re-exports from apply.act. These will be
moved to their own modules in a future split.
"""
from apply.act import cmd_fill, cmd_submit, cmd_next, cmd_back, cmd_inspect, run
