"""``shiva trishula`` subcommand parser.

Bridges the Shiva CLI onto the Trishula engineering engine (the trishula
package). The subcommand takes a verb and forwards everything else to the
engine's own argparse CLI, so ``shiva trishula code "..."`` behaves exactly
like ``trishula code "..."``:

    shiva trishula code "fix the retry bug" [--path DIR] [--provider ...]
    shiva trishula team "ship webhooks" [--plan-only]
    shiva trishula skills [list|search "q"]
    shiva trishula runs
    shiva trishula selftest

The parser builder follows the subcommand DI convention in this package;
the handler is self-contained (it calls ``trishula.cli.main``), so no
handler needs to live in god-file ``main.py``.
"""

from __future__ import annotations

import argparse
from typing import Callable


def build_trishula_parser(subparsers, *, cmd_trishula: Callable) -> None:
    """Attach the ``trishula`` subcommand to ``subparsers``."""
    parser = subparsers.add_parser(
        "trishula",
        aliases=("tri",),
        help="Trishula engineering core: autonomous code/team/skills commands",
        description=(
            "Trishula is Shiva's autonomous engineering engine — plan->edit->"
            "verify coding loops, sandboxed tools, self-improving skills, and "
            "Devin-style dev teams. Runs offline with zero API keys."
        ),
    )
    parser.add_argument(
        "tri_args",
        nargs=argparse.REMAINDER,
        help=(
            "Forwarded to the trishula CLI: code | team | skills | runs | "
            "selftest (e.g. `shiva trishula code \"fix the bug\" --path .`)"
        ),
    )
    parser.set_defaults(func=cmd_trishula)


def cmd_trishula(args: argparse.Namespace) -> int:
    """Dispatch `shiva trishula ...` into the trishula engine CLI."""
    forwarded = list(getattr(args, "tri_args", None) or [])
    if forwarded and forwarded[0] == "--":
        forwarded = forwarded[1:]
    if not forwarded:
        forwarded = ["--help"]
    # Import lazily: the trishula package is stdlib-only and cheap, but there
    # is no reason to import it for unrelated `shiva` commands.
    from trishula.cli import main as tri_main

    return tri_main(forwarded)
