"""Static rules shipped with AssumptionRadar (ADR-0005).

Each rule is a stateless object that inspects one parsed module and returns
findings. ``default_rules`` builds the set used by the scanner.
"""

from __future__ import annotations

from assumptionradar.static.engine import Rule
from assumptionradar.static.rules.cwd import RelativePathAccessRule

__all__ = ["RelativePathAccessRule", "default_rules"]


def default_rules() -> tuple[Rule, ...]:
    """Return a fresh instance of every built-in rule.

    Returns:
        New rule instances. Each call returns new objects.
    """
    return (RelativePathAccessRule(),)
