"""Public Python API: scan a project, verify tests, and read the results.

:class:`Radar` is the entry point. It validates its options when constructed,
and its methods return immutable reports that can be rendered as text, JSON,
or SARIF, or compared with a saved baseline.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from assumptionradar.analysis.runner import SubprocessRunner
from assumptionradar.analysis.scenarios import parse_scenario_spec
from assumptionradar.analysis.verification import (
    Runner,
    VerificationResult,
    Verifier,
)
from assumptionradar.baseline import Baseline, BaselineComparison, compare, load_baseline
from assumptionradar.config import Settings, load_settings, scan_with_settings
from assumptionradar.core.exceptions import ConfigurationError
from assumptionradar.core.models import Finding, VerificationStatus
from assumptionradar.reporting.json_report import build_pytest_report
from assumptionradar.reporting.sarif import sarif_from_scan
from assumptionradar.reporting.scan_report import (
    build_scan_report,
    is_failing,
    render_scan,
    summary_text,
)
from assumptionradar.reporting.terminal import to_text
from assumptionradar.static.engine import ScanResult
from assumptionradar.static.parsing import ScanError

_SOURCE: Final = "Radar"


def _plural(count: int, word: str) -> str:
    """Format a count with its noun, adding ``s`` unless the count is 1.

    Args:
        count: The number to show.
        word: The singular noun.

    Returns:
        The count and the noun, such as ``"2 tests"``.
    """
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


@dataclass(frozen=True, slots=True)
class ScanReport:
    """The result of scanning one project root.

    Attributes:
        root: The project root that was scanned.
        settings: The settings the scan ran with.
        result: The raw scan result.
    """

    root: Path
    settings: Settings
    result: ScanResult

    @property
    def findings(self) -> tuple[Finding, ...]:
        """The findings that were not suppressed."""
        return self.result.findings

    @property
    def suppressed(self) -> tuple[Finding, ...]:
        """The findings covered by inline suppression comments."""
        return self.result.suppressed

    @property
    def errors(self) -> tuple[ScanError, ...]:
        """The per-file errors that stopped a file from being scanned."""
        return self.result.errors

    @property
    def failing(self) -> bool:
        """True when the scan fails under the configured threshold."""
        return is_failing(self.result, self.settings)

    def summary(self) -> str:
        """Return the one-line summary of the scan, without colour codes.

        Returns:
            The summary text, such as ``"2 findings in 1 file"``.
        """
        return summary_text(self.result)

    def to_dict(self) -> dict[str, object]:
        """Return the scan as a JSON-compatible dict.

        Returns:
            The scan report document, with a fixed key order.
        """
        return build_scan_report(self.result, root=self.root, settings=self.settings)

    def to_json(self, indent: int | None = 2) -> str:
        """Return the scan report as a JSON string.

        Args:
            indent: Indentation passed to :func:`json.dumps`, or None for one line.

        Returns:
            The JSON text.
        """
        return json.dumps(self.to_dict(), indent=indent)

    def to_sarif_dict(self) -> dict[str, object]:
        """Return the scan as a SARIF 2.1.0 document.

        Returns:
            The SARIF document as a JSON-compatible dict.
        """
        return sarif_from_scan(self.result, root=self.root)

    def to_sarif(self, indent: int | None = 2) -> str:
        """Return the scan as a SARIF 2.1.0 JSON string.

        Args:
            indent: Indentation passed to :func:`json.dumps`, or None for one line.

        Returns:
            The SARIF text.
        """
        return json.dumps(self.to_sarif_dict(), indent=indent)

    def render(self, *, verbose: bool = False, color: bool = False) -> str:
        """Return the scan as terminal text.

        Args:
            verbose: Also show fix hints, suppressed findings and skipped files.
            color: Whether to wrap styled lines in ANSI colour codes.

        Returns:
            The rendered text, with a trailing newline when there are lines.
        """
        lines = render_scan(
            self.result,
            settings=self.settings,
            verbose=verbose,
            root=self.root,
        )
        return to_text(lines, color=color)

    def baseline(self) -> Baseline:
        """Return a baseline that records every finding of this scan.

        Returns:
            The baseline, ready to be written with ``write_baseline``.
        """
        return Baseline.from_findings(self.findings)

    def compare(self, baseline: Baseline | str | os.PathLike[str]) -> BaselineComparison:
        """Compare this scan with a baseline.

        Args:
            baseline: A baseline object, or the path of a baseline file.

        Returns:
            The new, unchanged and resolved findings.

        Raises:
            BaselineError: If the baseline file is missing or invalid.
        """
        if not isinstance(baseline, Baseline):
            baseline = load_baseline(Path(baseline))
        return compare(baseline, self.findings)


@dataclass(frozen=True, slots=True)
class VerificationReport:
    """The result of verifying one or more tests under scenarios.

    Attributes:
        results: One verification result per test, in request order.
    """

    results: tuple[VerificationResult, ...]

    @property
    def regressions(self) -> tuple[VerificationResult, ...]:
        """The verification results whose status is a regression."""
        return tuple(r for r in self.results if r.status is VerificationStatus.REGRESSION)

    @property
    def failing(self) -> bool:
        """True when at least one test regressed under a scenario."""
        return bool(self.regressions)

    def summary(self) -> str:
        """Return the one-line summary of the verification.

        Returns:
            The test count, then the non-zero status counts, such as
            ``"2 tests verified: 2 regressions"``.
        """
        n = len(self.results)
        text = f"{_plural(n, 'test')} verified"
        statuses = [r.status for r in self.results]
        regressions = sum(1 for s in statuses if s is VerificationStatus.REGRESSION)
        inconclusive = sum(
            1 for s in statuses if s in (VerificationStatus.INCONCLUSIVE, VerificationStatus.ERROR)
        )
        no_difference = sum(1 for s in statuses if s is VerificationStatus.NO_DIFFERENCE)
        unsupported = sum(1 for s in statuses if s is VerificationStatus.UNSUPPORTED)
        parts: list[str] = []
        if regressions:
            parts.append(_plural(regressions, "regression"))
        if inconclusive:
            parts.append(f"{inconclusive} inconclusive")
        if no_difference:
            parts.append(f"{no_difference} no difference")
        if unsupported:
            parts.append(f"{unsupported} unsupported")
        if parts:
            text += ": " + ", ".join(parts)
        return text

    def to_dict(self) -> dict[str, object]:
        """Return the verification as a JSON-compatible dict.

        Returns:
            The report document, with schema version and verification entries.
        """
        return build_pytest_report(None, self.results, ())

    def to_json(self, indent: int | None = 2) -> str:
        """Return the verification report as a JSON string.

        Args:
            indent: Indentation passed to :func:`json.dumps`, or None for one line.

        Returns:
            The JSON text.
        """
        return json.dumps(self.to_dict(), indent=indent)


class Radar:
    """Entry point for scanning projects and verifying tests.

    Options given to the constructor override the project configuration. They
    are checked when the object is created, so a bad option fails early.
    """

    def __init__(
        self,
        *,
        config: str | os.PathLike[str] | None = None,
        use_config: bool = True,
        rules: Iterable[str] | None = None,
        ignore_rules: Iterable[str] | None = None,
        fail_on: str | None = None,
        cache: bool | None = None,
        strict_suppressions: bool | None = None,
    ) -> None:
        """Create a radar with the given options.

        Args:
            config: A configuration file to read instead of discovering one.
            use_config: Whether to read the project configuration at all.
            rules: Rule ids to run. None keeps the configured set.
            ignore_rules: Rule ids to skip. None keeps the configured set.
            fail_on: Lowest severity that fails a scan, as a string or a Severity.
            cache: Whether scan results are cached.
            strict_suppressions: Whether bare suppression comments are refused.

        Raises:
            ValueError: If ``config`` is given together with ``use_config=False``.
            ConfigurationError: If an option is invalid.
        """
        if config is not None and not use_config:
            raise ValueError("config cannot be combined with use_config=False")
        self._config = config
        self._use_config = use_config
        overrides: dict[str, object] = {}
        if rules is not None:
            overrides["rules"] = tuple(rules)
        if ignore_rules is not None:
            overrides["ignore_rules"] = tuple(ignore_rules)
        if fail_on is not None:
            overrides["fail_on"] = str(fail_on)
        if cache is not None:
            overrides["cache"] = cache
        if strict_suppressions is not None:
            overrides["strict_suppressions"] = strict_suppressions
        self._overrides = overrides
        Settings().validated_replace(_SOURCE, **overrides)

    def scan(self, path: str | os.PathLike[str] = ".") -> ScanReport:
        """Scan a project root.

        Args:
            path: The project root directory.

        Returns:
            The scan report.

        Raises:
            ConfigurationError: If the root is not a directory or the configuration is invalid.
        """
        root = Path(path)
        if not root.is_dir():
            raise ConfigurationError(f"{root}: project root is not a directory")
        if self._use_config:
            base = load_settings(root, Path(self._config) if self._config else None)
        else:
            base = Settings()
        settings = base.validated_replace(_SOURCE, **self._overrides)
        result = scan_with_settings(root, settings)
        return ScanReport(root=root, settings=settings, result=result)

    def verify(
        self,
        test_ids: str | Iterable[str],
        *,
        root: str | os.PathLike[str] = ".",
        scenarios: str | Iterable[str] = "default",
        timeout_s: float = 120.0,
        baseline_runs: int = 2,
        runner: Runner | None = None,
    ) -> VerificationReport:
        """Re-run tests under scenarios and report any regressions.

        Args:
            test_ids: One test id or several, such as ``"tests/test_a.py::test_one"``.
            root: The project root the tests run in.
            scenarios: A scenario spec string, or a list of scenario names.
            timeout_s: Timeout in seconds for each child run.
            baseline_runs: Number of runs without a scenario, used to check stability.
            runner: The callable that runs a test. None uses a subprocess runner.

        Returns:
            The verification report, with one result per test.

        Raises:
            ValueError: If no test id is given.
            ConfigurationError: If a scenario spec is invalid.
            VerificationError: If the project root is invalid or a baseline run cannot start.
        """
        ids = [test_ids] if isinstance(test_ids, str) else list(test_ids)
        if not ids:
            raise ValueError("at least one test id is required")
        spec = scenarios if isinstance(scenarios, str) else ",".join(scenarios)
        try:
            parsed = parse_scenario_spec(spec)
        except ValueError as exc:
            raise ConfigurationError(f"invalid scenarios: {exc}") from exc
        verifier = Verifier(
            root,
            runner=runner or SubprocessRunner(timeout_s=timeout_s),
            baseline_runs=baseline_runs,
        )
        results = tuple(verifier.verify(test_id, parsed) for test_id in ids)
        return VerificationReport(results)
