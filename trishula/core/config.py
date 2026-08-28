"""Configuration for Trishula.

Layered resolution: constructor kwargs > environment variables > sensible
defaults.  Kept dependency-free (dataclasses + os.environ) so it loads in
the same microsecond window as the rest of the package.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from trishula.core.errors import ConfigError


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:  # pragma: no cover - trivial
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


@dataclass
class TrishulaConfig:
    """Tunables for the whole engine. Defaults are safe & cheap."""

    # Where trishula keeps its own state (skills db, run db, reflections).
    home: str = ""

    # ── AYUDHA: shell sandbox ───────────────────────────────────────────
    shell_timeout_default: int = 30          # seconds
    shell_timeout_max: int = 600
    shell_output_cap: int = 20_000           # chars returned per call
    shell_allow_network: bool = False
    shell_deny_commands: tuple[str, ...] = (
        "rm -rf /", "rm -rf ~", "mkfs", "dd if=", ":(){ :|:& };:",
        "shutdown", "reboot", "halt", "poweroff",
    )
    workspace_readonly: bool = False

    # ── KARANA: coding engine ───────────────────────────────────────────
    edit_require_unique: bool = True         # str_replace must match once
    edit_create_backups: bool = True         # keep undo history
    repomap_max_files: int = 800
    context_token_budget: int = 24_000
    verify_build_timeout: int = 600
    verify_test_timeout: int = 600
    coding_max_steps: int = 60
    # Verifier Phase 2: property testing + statement coverage
    verify_property_tests: bool = True   # run @property_test / generated property tests
    verify_coverage: bool = True         # measure statement coverage of changed files
    coverage_min_pct: float = 0.70       # below this, uncovered lines become feedback
    auto_generate_tests: bool = False    # scaffold smoke/property tests for new functions

    # ── CHIT-SHODHANA: autonomy ─────────────────────────────────────────
    skill_min_success_quality: float = 0.55  # bar for auto-promoting a skill
    reflect_after_runs: int = 1              # reflect after every N runs
    skill_search_k: int = 5
    autonomy_max_skill_patches: int = 20

    # ── DEVAS: teams ────────────────────────────────────────────────────
    team_max_workers: int = 8
    team_max_tasks: int = 200
    team_max_attempts: int = 3
    team_parallel: bool = True
    team_use_worktrees: bool = True   # isolate parallel workers in git worktrees

    # ── Model ───────────────────────────────────────────────────────────
    model: str = ""                          # e.g. "gpt-4o", empty = deterministic
    model_provider: str = ""                 # "openai" | "anthropic" | "stub" | ""
    model_temperature: float = 0.2
    model_max_tokens: int = 4096

    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.home:
            self.home = os.environ.get(
                "TRISHULA_HOME",
                str(Path(os.environ.get("SHIVA_HOME", str(Path.home() / ".shiva"))) / "trishula"),
            )
        self.shell_allow_network = _env_bool(
            "TRISHULA_ALLOW_NETWORK", self.shell_allow_network
        )
        if not self.model:
            self.model = os.environ.get("TRISHULA_MODEL", self.model)
        if not self.model_provider:
            self.model_provider = os.environ.get("TRISHULA_PROVIDER", self.model_provider)
        self.team_parallel = _env_bool("TRISHULA_PARALLEL_TEAMS", self.team_parallel)
        self.team_max_workers = _env_int("TRISHULA_MAX_WORKERS", self.team_max_workers)

    @property
    def home_path(self) -> Path:
        p = Path(self.home)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def skills_db(self) -> Path:
        return self.home_path / "skills.db"

    @property
    def runs_db(self) -> Path:
        return self.home_path / "runs.db"

    @property
    def deterministic(self) -> bool:
        """True when no model is configured — engines must run offline."""
        return not self.model_provider or self.model_provider == "stub"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["shell_deny_commands"] = list(self.shell_deny_commands)
        d["deterministic"] = self.deterministic
        return d
