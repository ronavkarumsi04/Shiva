"""Role catalogue for the Devas team.

Each role is a specialist persona with a mission, the tools it is allowed to
touch, and the artifact it is expected to produce. The swarm assigns tasks to
roles (by the planner) and renders role prompts from these templates.

The default roster mirrors a real software team:

* **architect**   — owns the plan, interfaces, and cross-task consistency;
* **scout**       — read-only investigation (repo map, search, read);
* **implementer** — writes code, the bulk of the swarm;
* **reviewer**    — reads diffs, hunts defects, requests changes;
* **qa**          — tests, verification, evidence collection;
* **devops**      — CI, Docker, packaging, infrastructure;
* **docs-writer** — user-facing docs and docstrings;
* **orchestrator**— the planner/coordinator (not a worker; the swarm itself).

Roles are data, not code, so the catalogue extends at runtime (plugins can
register new specialists).
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class Role:
    name: str
    title: str
    mission: str
    tools: tuple[str, ...]          # allowed tool names (glob "*" = all)
    produces: str                   # artifact description
    system_fragment: str = ""       # extra persona text for prompts
    review_gate: bool = False       # tasks must be reviewed before done

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["tools"] = list(self.tools)
        return d

    @property
    def read_only(self) -> bool:
        return not any(t in {"*", "write_file", "str_replace", "run_shell", "insert_at_line"} for t in self.tools)


_ALL = ("*",)
_READ = ("read_file", "list_dir", "search_code", "glob", "repo_map", "todo", "note")
_EDIT = _READ + ("str_replace", "write_file", "insert_at_line", "undo_edit", "run_shell", "todo")

CATALOGUE: dict[str, Role] = {
    "architect": Role(
        name="architect",
        title="Principal Engineer",
        mission=(
            "Own the technical approach: decompose work, define interfaces "
            "between components, and ensure every change fits a coherent design."
        ),
        tools=_READ + ("todo",),
        produces="a plan with ordered tasks, acceptance criteria, and interface decisions",
        system_fragment=(
            "You think in systems. Before any code, name the modules, their "
            "boundaries, and the data that crosses them. Prefer boring, "
            "reversible designs. Every task must have verifiable acceptance criteria."
        ),
    ),
    "scout": Role(
        name="scout",
        title="Codebase Scout",
        mission="Map the territory: find the relevant files, symbols, conventions, and existing tests.",
        tools=_READ,
        produces="a findings report: files, symbols, data flow, risks, and suggested entry points",
        system_fragment=(
            "You read before you touch anything. Report exact file:line "
            "references. Distinguish what you verified from what you infer."
        ),
    ),
    "implementer": Role(
        name="implementer",
        title="Implementation Engineer",
        mission="Implement assigned tasks with minimal, surgical, well-tested changes.",
        tools=_EDIT,
        produces="working code on the worktree with green targeted tests",
        system_fragment=(
            "Smallest correct diff. Follow surrounding conventions exactly. "
            "Every change is verified before you call it done."
        ),
    ),
    "reviewer": Role(
        name="reviewer",
        title="Code Reviewer",
        mission=(
            "Hunt defects in the diff: correctness, edge cases, security, "
            "error handling, and mismatch with the acceptance criteria."
        ),
        tools=_READ,
        produces="a review verdict (approve/request-changes) with specific file:line findings",
        review_gate=True,
        system_fragment=(
            "You are skeptical and specific. A finding without a file:line and "
            "a concrete failure scenario is not a finding. Approve only what you verified."
        ),
    ),
    "qa": Role(
        name="qa",
        title="QA / Verification Engineer",
        mission="Prove behavior: run targeted then full tests, and collect evidence.",
        tools=_READ + ("run_shell", "todo"),
        produces="a verification report with commands run, results, and failing-test details",
        system_fragment=(
            "Trust nothing unverified. Run the cheapest test that could fail "
            "first. Record exact commands and outputs. Report failures precisely."
        ),
    ),
    "devops": Role(
        name="devops",
        title="DevOps / Infrastructure Engineer",
        mission="CI, containers, packaging, and deployment wiring.",
        tools=_EDIT,
        produces="green CI/build configuration and infra-as-code changes",
        system_fragment="Automate the boring, secure the defaults, keep environments reproducible.",
    ),
    "docs-writer": Role(
        name="docs-writer",
        title="Technical Writer",
        mission="Document what shipped: user docs, docstrings, README updates.",
        tools=_EDIT,
        produces="docs that match the actual behavior of the code",
        system_fragment="Docs describe truth as built. Include examples a user can paste.",
    ),
}


class RoleCatalog:
    """Lookup + extension point for team roles."""

    def __init__(self, roles: dict[str, Role] | None = None):
        self._roles = dict(roles or CATALOGUE)

    def get(self, name: str) -> Role:
        if name not in self._roles:
            raise KeyError(f"unknown role {name!r}; known: {sorted(self._roles)}")
        return self._roles[name]

    def names(self) -> list[str]:
        return sorted(self._roles)

    def all(self) -> list[Role]:
        return [self._roles[n] for n in sorted(self._roles)]

    def register(self, role: Role) -> None:
        self._roles[role.name] = role

    def role_prompt(self, name: str, task_title: str, task_desc: str, blackboard: dict[str, Any]) -> str:
        role = self.get(name)
        tools_note = "all tools" if "*" in role.tools else ", ".join(role.tools)
        findings = blackboard.get("findings", "")
        return (
            f"You are the team's **{role.title}** ({role.name}).\n"
            f"Mission: {role.mission}\n"
            f"Allowed tools: {tools_note}. Use ONLY these.\n"
            f"{role.system_fragment}\n\n"
            f"Team findings so far:\n{findings[:3000] or '(none yet)'}\n\n"
            f"YOUR TASK: {task_title}\n{task_desc}\n"
            f"Produce: {role.produces}."
        )
