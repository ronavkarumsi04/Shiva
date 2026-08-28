"""Pytest entry point for the Trishula engine's stdlib unittest suite.

Trishula is dependency-free and its own suite uses stdlib ``unittest`` so it
can run via ``trishula selftest`` on machines without pytest. This thin
wrapper loads that suite under pytest so the repository's CI and
``scripts/run_tests.sh`` exercise it too.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_trishula_suite_passes() -> None:
    loader = unittest.TestLoader()
    suite = loader.discover(
        str(ROOT / "trishula" / "tests"),
        pattern="test_*.py",
        top_level_dir=str(ROOT),
    )
    with open(os.devnull, "w") as sink:
        result = unittest.TextTestRunner(verbosity=0, stream=sink).run(suite)
    assert result.wasSuccessful(), (
        f"trishula suite failed: {len(result.failures)} failures, "
        f"{len(result.errors)} errors"
    )
