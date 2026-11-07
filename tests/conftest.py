"""Shared test configuration."""

import assumptionradar.analysis.runtime as _runtime

# CPython disables tracing inside audit hooks unless the hook sets
# ``__cantrace__``. Allow it in the test suite only, so coverage can measure
# the hook's code paths; production keeps the default (no tracing in hooks).
setattr(_runtime._audit_hook, "__cantrace__", True)  # noqa: B010

pytest_plugins = ["pytester"]

# Fixture projects are run by tests/e2e in their own pytest process, not collected here.
collect_ignore = ["fixtures"]
