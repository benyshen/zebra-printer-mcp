"""Wire the manual-parsed command catalog into builder.validate_zpl.

knowledge.py imports cleanly (no circular import back into builder).
"""
from __future__ import annotations

from . import builder as _builder
from . import knowledge as _knowledge

def install() -> None:
    _builder.KNOWN_COMMANDS = _knowledge.known_commands()

install()
