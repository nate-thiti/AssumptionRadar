"""Child-process entry point for scenario runs (ADR-0003).

Run as ``python -m assumptionradar.analysis._child <pytest args...>``. The parent
passes its configuration in the ``ASSUMPTIONRADAR_CHILD`` environment variable.
This module removes that variable before pytest starts so tests cannot see it.
"""

from __future__ import annotations

import codecs
import json
import locale
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from assumptionradar.analysis.scenarios import REFERENCE_TIMESTAMP

_CONFIG_ENV = "ASSUMPTIONRADAR_CHILD"


def _load_config() -> dict[str, Any]:
    """Pop the child configuration from the environment and parse it."""
    raw = os.environ.pop(_CONFIG_ENV, None)
    if not raw:
        return {}
    return json.loads(raw)  # type: ignore[no-any-return]


def _apply_locale(value: str) -> bool:
    """Try to set the process locale. Return True when it was applied."""
    try:
        locale.setlocale(locale.LC_ALL, value)
    except locale.Error:
        return False
    return True


def _preferred_utf8() -> bool:
    """Return True when the preferred encoding is UTF-8."""
    return codecs.lookup(locale.getpreferredencoding(False)).name == "utf-8"


def _same_dir(actual: str, expected: str) -> bool:
    """Return True when ``actual`` and ``expected`` name the same existing directory."""
    try:
        return Path(actual).samefile(expected)
    except OSError:
        return False


def main(argv: list[str]) -> int:
    """Apply the requested conditions, write the probe, and run pytest.

    Args:
        argv: Arguments forwarded to ``pytest.main``.

    Returns:
        The pytest exit code, or 3 if pytest cannot be imported.
    """
    config = _load_config()
    probe_path = config.get("probe_path")
    requested_locale = config.get("locale")
    isolated_cwd = config.get("isolated_cwd")
    isolated_tmp = config.get("isolated_tmp")
    check_env: list[str] = list(config.get("check_env") or [])

    probe: dict[str, str] = {}
    if requested_locale:
        probe["locale_applied"] = "true" if _apply_locale(requested_locale) else "false"
    probe["utc_offset_seconds"] = str(time.localtime(REFERENCE_TIMESTAMP).tm_gmtoff)
    probe["preferred_encoding_utf8"] = "true" if _preferred_utf8() else "false"
    if isolated_cwd:
        probe["cwd"] = (
            "{isolated_cwd}" if _same_dir(str(Path.cwd()), isolated_cwd) else "{unexpected}"
        )
    if isolated_tmp:
        temp_ok = _same_dir(tempfile.gettempdir(), isolated_tmp)
        probe["tempdir"] = "{isolated_tmp}" if temp_ok else "{unexpected}"
    for name in check_env:
        probe[f"env:{name}"] = "present" if name in os.environ else "absent"

    if probe_path:
        Path(probe_path).write_text(json.dumps(probe), encoding="utf-8")

    try:
        import pytest  # noqa: PLC0415 - pytest is only needed in the child process
    except ImportError as exc:
        print(f"assumptionradar: pytest is not available: {exc}", file=sys.stderr)
        return 3
    recorder = _CollectionRecorder()
    from assumptionradar.integrations import pytest_plugin  # noqa: PLC0415

    pytest_plugin.mark_verification_child()
    exit_code = int(pytest.main(argv, plugins=[recorder]))
    result_path = config.get("result_path")
    if result_path:
        # Exit codes alone are ambiguous: when a module fails to import and a
        # specific node id was requested, pytest exits with 4 ("found no
        # collectors"), not 2. The recorder reports what actually happened.
        result = {"collect_errors": recorder.collect_errors}
        Path(result_path).write_text(json.dumps(result), encoding="utf-8")
    return exit_code


class _CollectionRecorder:
    """pytest plugin that counts failed collection reports."""

    def __init__(self) -> None:
        self.collect_errors = 0

    def pytest_collectreport(self, report: Any) -> None:  # noqa: ANN401 - pytest hook
        """Count collectors that failed (e.g. a module raising at import time)."""
        if report.failed:
            self.collect_errors += 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
