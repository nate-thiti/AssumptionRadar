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

__all__ = [
    "RelativePathAccessRule",
    "RequiredEnvVarRule",
    "UndeclaredEnvVarRule",
    "collect_env_reads",
    "default_rules",
]


def default_rules() -> tuple[Rule, ...]:
    """Return a fresh instance of every built-in rule.

    Returns:
        New rule instances. Each call returns new objects.
    """
    return (RelativePathAccessRule(), RequiredEnvVarRule())
