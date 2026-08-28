"""Resolve SHIVA_HOME for standalone skill scripts.

Skill scripts may run outside the Shiva process (system Python, nix env,
CI) where ``shiva_constants`` is not importable.  This module provides the
same ``get_shiva_home()`` contract without requiring it on ``sys.path``.

When ``shiva_constants`` IS available it is used directly so profile
resolution and any future enhancements are picked up automatically.
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from shiva_constants import get_shiva_home as get_shiva_home
except (ModuleNotFoundError, ImportError):

    def get_shiva_home() -> Path:
        """Return the Shiva home directory (default: ``~/.shiva``)."""
        val = os.environ.get("SHIVA_HOME", "").strip()
        return Path(val) if val else Path.home() / ".shiva"
