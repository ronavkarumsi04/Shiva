"""Structured-ish logging for Trishula.

A tiny wrapper over the stdlib ``logging`` module so trishula never fights
the host application's (Shiva's) logging configuration:

* loggers are children of the ``trishula`` logger and therefore inherit any
  handlers Shiva has installed;
* if nobody has configured logging at all, a null handler prevents the
  infamous "No handlers could be found" stderr spam;
* every event that matters *also* goes onto the :class:`~trishula.core.types.Journal`
  when one is attached, because an agent's learning loop consumes events,
  not log lines.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

_ROOT_NAME = "trishula"

_ROOT = logging.getLogger(_ROOT_NAME)
if not _ROOT.handlers:
    _ROOT.addHandler(logging.NullHandler())


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced child logger, e.g. ``trishula.tools.shell``."""
    if not name or name == _ROOT_NAME:
        return _ROOT
    return _ROOT.getChild(name)


def enable_console(level: int | str = logging.INFO, *, stream: Any = None) -> None:
    """Attach a stderr handler for interactive use (the ``trishula`` CLI).

    Idempotent: calling it twice does not duplicate output.
    """
    handler = next(
        (h for h in _ROOT.handlers if getattr(h, "_trishula_console", False)),
        None,
    )
    if handler is None:
        handler = logging.StreamHandler(stream or sys.stderr)
        handler.setFormatter(
            logging.Formatter("[%(asctime)s] %(name)s %(levelname)s: %(message)s")
        )
        handler._trishula_console = True  # type: ignore[attr-defined]
        _ROOT.addHandler(handler)
    _ROOT.setLevel(level)
