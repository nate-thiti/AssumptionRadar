"""AssumptionRadar: discover hidden environmental assumptions.

Public names are loaded on first use (PEP 562), so importing this package stays
cheap. The pytest plugin imports it in every test run.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from assumptionradar.api import Radar, ScanReport, VerificationReport
    from assumptionradar.baseline import Baseline, BaselineComparison
    from assumptionradar.config import Settings
    from assumptionradar.core.exceptions import (
        AssumptionRadarError,
        BaselineError,
        ConfigurationError,
        VerificationError,
    )
    from assumptionradar.core.models import Finding, Severity

__version__ = "0.1.0.dev0"

_LAZY: dict[str, str] = {
    "AssumptionRadarError": "assumptionradar.core.exceptions",
    "ConfigurationError": "assumptionradar.core.exceptions",
    "BaselineError": "assumptionradar.core.exceptions",
    "VerificationError": "assumptionradar.core.exceptions",
    "Finding": "assumptionradar.core.models",
    "Severity": "assumptionradar.core.models",
    "Settings": "assumptionradar.config",
    "Baseline": "assumptionradar.baseline",
    "BaselineComparison": "assumptionradar.baseline",
    "Radar": "assumptionradar.api",
    "ScanReport": "assumptionradar.api",
    "VerificationReport": "assumptionradar.api",
}

__all__ = [
    "AssumptionRadarError",
    "Baseline",
    "BaselineComparison",
    "BaselineError",
    "ConfigurationError",
    "Finding",
    "Radar",
    "ScanReport",
    "Settings",
    "Severity",
    "VerificationError",
    "VerificationReport",
    "__version__",
]


def __getattr__(name: str) -> object:
    """Import a public name on first access and cache it in the module namespace.

    Args:
        name: The attribute being looked up.

    Returns:
        The imported object.

    Raises:
        AttributeError: If ``name`` is not a public name of this package.
    """
    module_path = _LAZY.get(name)
    if module_path is None:
        raise AttributeError(f"module 'assumptionradar' has no attribute {name!r}")
    value: object = getattr(importlib.import_module(module_path), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Return the sorted names of this module, including lazy public names.

    Returns:
        The sorted list of attribute names.
    """
    return sorted({*globals(), *__all__})
