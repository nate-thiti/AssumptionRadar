"""Static rules shipped with AssumptionRadar (ADR-0005).

Each rule is a stateless object that inspects one parsed module and returns
findings. ``default_rules`` builds the set used by the scanner.
"""

from __future__ import annotations

from assumptionradar.static.engine import Rule
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
from assumptionradar.static.rules.plat import PlatformSpecificRule, classify_path_literal

__all__ = [
    "PathLookupRule",
    "PlatformSpecificRule",
    "RelativePathAccessRule",
    "RequiredEnvVarRule",
    "UndeclaredEnvVarRule",
    "classify_path_literal",
    "collect_env_reads",
    "default_rules",
    "is_bare_program",
    "program_from_command",
]


def default_rules() -> tuple[Rule, ...]:
    """Return a fresh instance of every built-in rule.

    Returns:
        New rule instances. Each call returns new objects.
    """
    return (
        RelativePathAccessRule(),
        RequiredEnvVarRule(),
        PathLookupRule(),
        PlatformSpecificRule(),
    )
