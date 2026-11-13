"""Reports for ``assumptionradar scan``: the JSON document and the terminal text.

The JSON document has a fixed top-level key order and is versioned by
:data:`SCAN_REPORT_SCHEMA_VERSION`. The terminal renderer returns the same
:class:`Line` objects that the report renderer uses, so colour handling stays
in one place.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

from assumptionradar import __version__
from assumptionradar.config import Settings
from assumptionradar.core.models import Finding, Severity
from assumptionradar.reporting.terminal import Line
from assumptionradar.static.engine import ScanResult

SCAN_REPORT_SCHEMA_VERSION: Final = "1"
SCAN_REPORT_KIND: Final = "assumptionradar-scan"

_SEVERITY_NAMES: Final[dict[Severity, str]] = {
    Severity.ERROR: "error",
    Severity.WARNING: "warning",
    Severity.INFO: "info",
}
_SEVERITY_STYLES: Final[dict[Severity, str | None]] = {
    Severity.ERROR: "red",
    Severity.WARNING: "yellow",
    Severity.INFO: None,
}


def _plural(count: int, word: str) -> str:
    """Format a count with its noun, adding ``s`` unless the count is 1.

    Args:
        count: The number to show.
        word: The singular noun.

    Returns:
        The count and the noun, such as ``"2 files"``.
    """
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _relpath(path: Path, root: Path) -> str:
    """Return ``path`` relative to ``root`` as a POSIX string.

    Args:
        path: An absolute path.
        root: The project root.

    Returns:
        The relative path with ``/`` separators.
    """
    return Path(os.path.relpath(path, root)).as_posix()


def _where(finding: Finding) -> str:
    """Return the location of a finding as ``path:line:column`` or a shorter form.

    Args:
        finding: The finding to describe.

    Returns:
        ``"-"`` when the finding has no location.
    """
    loc = finding.location
    if loc is None:
        return "-"
    if loc.line is None:
        return str(loc.path)
    if loc.column is None:
        return f"{loc.path}:{loc.line}"
    return f"{loc.path}:{loc.line}:{loc.column}"


def is_failing(result: ScanResult, settings: Settings) -> bool:
    """Decide whether a scan fails the build.

    Args:
        result: The completed scan.
        settings: The settings that give the ``fail_on`` threshold and the
            ``strict_suppressions`` flag.

    Returns:
        True when a finding is at or above the threshold, or when strict
        suppressions are on and a suppression problem was reported.
    """
    threshold = Severity.parse(settings.fail_on)
    if any(finding.severity.at_least(threshold) for finding in result.findings):
        return True
    return settings.strict_suppressions and bool(result.suppression_problems)


def build_scan_report(
    result: ScanResult,
    *,
    root: Path,
    settings: Settings,
) -> dict[str, object]:
    """Build the JSON-compatible document for a scan.

    Args:
        result: The completed scan.
        root: The project root that was scanned.
        settings: The settings used for the scan.

    Returns:
        A dict whose keys appear in a fixed order. Skipped file paths are
        relative to ``root``.
    """
    by_severity = {
        name: sum(1 for finding in result.findings if finding.severity is severity)
        for severity, name in _SEVERITY_NAMES.items()
    }
    summary: dict[str, object] = {
        "files_scanned": result.files_scanned,
        "findings": len(result.findings),
        "suppressed": len(result.suppressed),
        "suppression_problems": len(result.suppression_problems),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "by_severity": by_severity,
        "fail_on": settings.fail_on,
        "failing": is_failing(result, settings),
    }
    config_file = str(settings.config_file) if settings.config_file is not None else None
    skipped: list[dict[str, Any]] = [
        {"path": _relpath(item.path, root), "reason": item.reason} for item in result.skipped
    ]
    return {
        "schema_version": SCAN_REPORT_SCHEMA_VERSION,
        "kind": SCAN_REPORT_KIND,
        "tool": {"name": "assumptionradar", "version": __version__},
        "root": str(root),
        "config_file": config_file,
        "summary": summary,
        "findings": [finding.to_dict() for finding in result.findings],
        "suppressed": [finding.to_dict() for finding in result.suppressed],
        "suppression_problems": [problem.to_dict() for problem in result.suppression_problems],
        "errors": [error.to_dict() for error in result.errors],
        "skipped": skipped,
    }


def render_scan(
    result: ScanResult,
    *,
    settings: Settings,
    verbose: bool = False,
    quiet: bool = False,
    root: Path | None = None,
) -> list[Line]:
    """Render a scan as terminal lines.

    Args:
        result: The completed scan.
        settings: The settings used for the scan; ``fail_on`` and
            ``strict_suppressions`` decide the colour of the summary.
        verbose: Also show fix hints, suppressed findings and skipped files.
        quiet: Omit the summary line.
        root: The project root, used to show skipped paths relative to it. When
            None, only the file name is shown.

    Returns:
        The lines to print, findings first, then the summary.
    """
    lines: list[Line] = _finding_lines(result.findings, verbose=verbose)
    if verbose:
        lines.extend(
            Line(f"{_where(finding)}: {finding.rule_id} [suppressed] {finding.message}")
            for finding in result.suppressed
        )
    lines.extend(
        Line(
            f"{problem.relpath}:{problem.line}: {problem.kind} suppression: {problem.message}",
            "yellow",
        )
        for problem in result.suppression_problems
    )
    for error in result.errors:
        where = error.relpath
        if error.line is not None:
            where = f"{where}:{error.line}"
        lines.append(Line(f"{where}: could not be scanned ({error.kind}): {error.message}", "red"))
    if verbose:
        for item in result.skipped:
            shown = _relpath(item.path, root) if root is not None else item.path.name
            lines.append(Line(f"{shown}: skipped: {item.reason}"))
    if not quiet:
        lines.append(_summary_line(result, settings))
    return lines


def _finding_lines(findings: Sequence[Finding], *, verbose: bool) -> list[Line]:
    """Return one line per finding, with fix hints when verbose.

    Args:
        findings: The findings to show, in order.
        verbose: Whether to add the ``fix:`` line after each finding that has one.

    Returns:
        The rendered lines.
    """
    lines: list[Line] = []
    for finding in findings:
        style = _SEVERITY_STYLES[finding.severity]
        name = _SEVERITY_NAMES[finding.severity]
        lines.append(
            Line(f"{_where(finding)}: {finding.rule_id} [{name}] {finding.message}", style)
        )
        if verbose and finding.remediation:
            lines.append(Line(f"    fix: {finding.remediation}"))
    return lines


def summary_text(result: ScanResult) -> str:
    """Return the one-line summary of a scan, without colour codes.

    Args:
        result: The completed scan.

    Returns:
        The count of findings and files, followed by any suppression and error counts.
    """
    text = f"{_plural(len(result.findings), 'finding')} in {_plural(result.files_scanned, 'file')}"
    extras: list[str] = []
    if result.suppressed:
        extras.append(f"{len(result.suppressed)} suppressed")
    if result.errors:
        extras.append(f"{_plural(len(result.errors), 'file')} not scanned")
    if result.suppression_problems:
        extras.append(_plural(len(result.suppression_problems), "suppression problem"))
    if extras:
        text += "; " + ", ".join(extras)
    return text


def _summary_line(result: ScanResult, settings: Settings) -> Line:
    """Return the closing summary line, coloured by whether the scan fails.

    Args:
        result: The completed scan.
        settings: The settings used for the scan.

    Returns:
        The summary line.
    """
    return Line(summary_text(result), "red" if is_failing(result, settings) else "green")
