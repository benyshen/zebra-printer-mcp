"""Wire the manual-parsed command catalog into builder.validate_zpl.

knowledge.py imports cleanly (no circular import back into builder).
"""
from __future__ import annotations

from . import builder as _builder
from . import knowledge as _knowledge

def install() -> set:
    """Load the manual catalog into builder.KNOWN_COMMANDS and return it."""
    _builder.KNOWN_COMMANDS = _knowledge.known_commands()
    return _builder.KNOWN_COMMANDS

install()
