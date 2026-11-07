"""pytest plugin entry point (``pytest11``): option registration and lazy activation.

This module is imported by every pytest run in an environment where
AssumptionRadar is installed, so it only registers the ``--assumption-*``
options. The analysis engine is imported, and the session hooks registered, only
when one of those options is given. Verification children call
:func:`mark_verification_child` first so the plugin stays inactive there even if
options arrive through ``PYTEST_ADDOPTS``.
"""

from __future__ import annotations

import pytest

_VERIFICATION_CHILD = False
_ACTIVATING_OPTIONS = (
    "assumption_observe",
    "assumption_env_reads",
    "assumption_verify",
    "assumption_report",
    "assumption_strict",
)


def mark_verification_child() -> None:
    """Disable this plugin for the current process (used by verification children)."""
    global _VERIFICATION_CHILD  # noqa: PLW0603
    _VERIFICATION_CHILD = True


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register the ``--assumption-*`` options. Always registered, even in children."""
    group = parser.getgroup("assumptionradar", "AssumptionRadar: hidden environment assumptions")
    group.addoption(
        "--assumption-observe",
        action="store_true",
        default=False,
        help="record runtime environment events (file, env, network, subprocess) by project code",
    )
    group.addoption(
        "--assumption-env-reads",
        action="store_true",
        default=False,
        help="also record os.environ reads (requires --assumption-observe)",
    )
    group.addoption(
        "--assumption-verify",
        type=str,
        default=None,
        metavar="SPEC",
        help=(
            "re-run passing tests under scenarios, "
            "e.g. cwd,tz,encoding,tmpdir,env-unset:NAME,default"
        ),
    )
    group.addoption(
        "--assumption-verify-max",
        type=int,
        default=20,
        metavar="N",
        help="maximum number of passing tests to verify (default: 20)",
    )
    group.addoption(
        "--assumption-timeout",
        type=float,
        default=120.0,
        metavar="SECONDS",
        help="timeout for each verification child run (default: 120)",
    )
    group.addoption(
        "--assumption-report",
        type=str,
        default=None,
        metavar="PATH",
        help="write a JSON report to PATH, relative to the invocation directory",
    )
    group.addoption(
        "--assumption-strict",
        action="store_true",
        default=False,
        help=(
            "exit with failure when verification finds a regression; observation never fails a run"
        ),
    )


def pytest_configure(config: pytest.Config) -> None:
    """Load and activate the session implementation when an option asks for it."""
    if _VERIFICATION_CHILD:
        return
    if not any(config.getoption(name) for name in _ACTIVATING_OPTIONS):
        return
    from assumptionradar.integrations import _pytest_session  # noqa: PLC0415

    _pytest_session.activate(config)
    # Register the module object itself: pytester may drop it from sys.modules
    # between in-process runs while the package attribute survives.
    config.pluginmanager.register(_pytest_session, name="assumptionradar-session")
