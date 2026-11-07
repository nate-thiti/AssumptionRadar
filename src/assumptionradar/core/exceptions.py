"""Exception hierarchy for the AssumptionRadar library.

Every error raised deliberately by the library derives from
:class:`AssumptionRadarError`, so callers can catch one base type.
"""

from __future__ import annotations


class AssumptionRadarError(Exception):
    """Base class for all errors raised by AssumptionRadar."""


class ConfigurationError(AssumptionRadarError):
    """Raised when the tool is configured with invalid or missing settings."""


class VerificationError(AssumptionRadarError):
    """Raised when a finding cannot be verified at all (for example, the baseline cannot start)."""


class ObserverError(AssumptionRadarError):
    """Raised when a runtime observer is misused, for example by nested activation."""
