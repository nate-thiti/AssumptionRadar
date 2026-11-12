"""Static rules shipped with AssumptionRadar (ADR-0005).

Each rule is a stateless object that inspects one parsed module and returns
findings. ``default_rules`` builds the set used by the scanner.
"""

from __future__ import annotations

from assumptionradar.static.engine import Rule
from assumptionradar.static.project import ProjectInfo
from assumptionradar.static.rules.cwd import RelativePathAccessRule
from assumptionradar.static.rules.env import (
    RequiredEnvVarRule,
    UndeclaredEnvVarRule,
    collect_env_reads,
)
from assumptionradar.static.rules.exe import (
    PathLookupRule,
    is_bare_program,
    program_from_command,
)
from assumptionradar.static.rules.optional_imports import (
    OptionalImportRule,
    distribution_candidates,
)
from assumptionradar.static.rules.plat import PlatformSpecificRule, classify_path_literal

__all__ = [
    "OptionalImportRule",
    "PathLookupRule",
    "PlatformSpecificRule",
    "RelativePathAccessRule",
    "RequiredEnvVarRule",
    "UndeclaredEnvVarRule",
    "classify_path_literal",
    "collect_env_reads",
    "default_rules",
    "distribution_candidates",
    "is_bare_program",
    "program_from_command",
]


def default_rules(project: ProjectInfo | None = None) -> tuple[Rule, ...]:
    """Return a fresh instance of every built-in rule.

    Args:
        project: Project metadata. When it has a ``pyproject.toml``, the
            project-aware rules are included as well.

    Returns:
        New rule instances. Each call returns new objects.
    """
    rules: list[Rule] = [
        RelativePathAccessRule(),
        RequiredEnvVarRule(),
        PathLookupRule(),
        PlatformSpecificRule(),
    ]
    if project is not None and project.has_pyproject:
        rules.append(OptionalImportRule(project.declared, project.first_party))
    return tuple(rules)
