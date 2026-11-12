"""SARIF 2.1.0 exporter for AssumptionRadar scan results.

Columns are 1-based offsets as reported by the AST (byte-based for non-ASCII
lines), which may differ from SARIF's default UTF-16 column kind on such lines.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final
from urllib.parse import quote

import assumptionradar
from assumptionradar.core.models import Finding, Severity, SourceLocation
from assumptionradar.static.engine import ScanResult
from assumptionradar.static.parsing import ScanError

SARIF_VERSION: Final = "2.1.0"
SARIF_SCHEMA_URI: Final = "https://json.schemastore.org/sarif-2.1.0.json"
TOOL_URI: Final = "https://github.com/nate-thiti/AssumptionRadar"
FINGERPRINT_KEY: Final = "assumptionradar/v1"
SRCROOT: Final = "%SRCROOT%"

_LEVELS: Final[dict[Severity, str]] = {
    Severity.ERROR: "error",
    Severity.WARNING: "warning",
    Severity.INFO: "note",
}


def _artifact_location(path: str) -> dict[str, Any]:
    """Return the SARIF artifact location for a repository-relative path."""
    return {"uri": quote(path, safe="/"), "uriBaseId": SRCROOT}


def _location(location: SourceLocation) -> dict[str, Any]:
    """Return the SARIF location object for a source location."""
    physical: dict[str, Any] = {"artifactLocation": _artifact_location(location.path)}
    if location.line is not None:
        region: dict[str, Any] = {"startLine": location.line}
        if location.column is not None:
            region["startColumn"] = location.column
        physical["region"] = region
    out: dict[str, Any] = {"physicalLocation": physical}
    if location.symbol is not None:
        out["logicalLocations"] = [{"fullyQualifiedName": location.symbol}]
    return out


def _rule_descriptor(finding: Finding) -> dict[str, Any]:
    """Return the SARIF reporting descriptor for the rule of ``finding``."""
    rule: dict[str, Any] = {
        "id": finding.rule_id,
        "shortDescription": {"text": finding.title},
    }
    if finding.remediation:
        rule["help"] = {"text": finding.remediation}
    if finding.docs_url is not None:
        rule["helpUri"] = finding.docs_url
    rule["defaultConfiguration"] = {"level": _LEVELS[finding.severity]}
    rule["properties"] = {"category": str(finding.category)}
    return rule


def _build_rules(
    findings: Sequence[Finding], suppressed: Sequence[Finding]
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Return the sorted rule descriptors and a map from rule id to index."""
    first: dict[str, Finding] = {}
    for finding in (*findings, *suppressed):
        first.setdefault(finding.rule_id, finding)
    rule_ids = sorted(first)
    rules = [_rule_descriptor(first[rule_id]) for rule_id in rule_ids]
    indexes = {rule_id: index for index, rule_id in enumerate(rule_ids)}
    return rules, indexes


def _result(finding: Finding, rule_index: int, *, is_suppressed: bool) -> dict[str, Any]:
    """Return the SARIF result object for one finding."""
    item: dict[str, Any] = {
        "ruleId": finding.rule_id,
        "ruleIndex": rule_index,
        "level": _LEVELS[finding.severity],
        "message": {"text": finding.message},
    }
    if finding.location is not None:
        item["locations"] = [_location(finding.location)]
    item["partialFingerprints"] = {FINGERPRINT_KEY: finding.fingerprint}
    item["properties"] = {
        "confidence": str(finding.confidence),
        "category": str(finding.category),
    }
    if is_suppressed:
        item["suppressions"] = [{"kind": "inSource"}]
    return item


def _results(
    findings: Sequence[Finding],
    suppressed: Sequence[Finding],
    indexes: dict[str, int],
) -> list[dict[str, Any]]:
    """Return results for findings in order, followed by suppressed findings."""
    results = [_result(f, indexes[f.rule_id], is_suppressed=False) for f in findings]
    results.extend(_result(f, indexes[f.rule_id], is_suppressed=True) for f in suppressed)
    return results


def _notification(error: ScanError) -> dict[str, Any]:
    """Return the SARIF tool execution notification for one scan error."""
    physical: dict[str, Any] = {"artifactLocation": _artifact_location(error.relpath)}
    if error.line is not None:
        physical["region"] = {"startLine": error.line}
    return {
        "level": "error",
        "message": {
            "text": f"{error.relpath} could not be scanned ({error.kind}): {error.message}",
        },
        "locations": [{"physicalLocation": physical}],
    }


def _run(
    findings: Sequence[Finding],
    suppressed: Sequence[Finding],
    errors: Sequence[ScanError],
    root: Path | None,
) -> dict[str, Any]:
    """Return the single SARIF run object."""
    rules, indexes = _build_rules(findings, suppressed)
    run: dict[str, Any] = {
        "tool": {
            "driver": {
                "name": "AssumptionRadar",
                "version": assumptionradar.__version__,
                "informationUri": TOOL_URI,
                "rules": rules,
            }
        }
    }
    if root is not None:
        run["originalUriBaseIds"] = {SRCROOT: {"uri": root.absolute().as_uri() + "/"}}
    run["invocations"] = [
        {
            "executionSuccessful": True,
            "toolExecutionNotifications": [_notification(error) for error in errors],
        }
    ]
    run["results"] = _results(findings, suppressed, indexes)
    return run


def build_sarif(
    findings: Sequence[Finding],
    *,
    suppressed: Sequence[Finding] = (),
    errors: Sequence[ScanError] = (),
    root: Path | None = None,
) -> dict[str, Any]:
    """Build a SARIF 2.1.0 document from findings and scan errors.

    Args:
        findings: Findings not covered by a suppression, in output order.
        suppressed: Findings covered by inline suppression comments.
        errors: Per-file scan errors reported as tool notifications.
        root: Project root; when given, ``%SRCROOT%`` is anchored to it.

    Returns:
        The SARIF document as a JSON-compatible dict.
    """
    return {
        "$schema": SARIF_SCHEMA_URI,
        "version": SARIF_VERSION,
        "runs": [_run(findings, suppressed, errors, root)],
    }


def sarif_from_scan(result: ScanResult, *, root: Path | None = None) -> dict[str, Any]:
    """Build a SARIF 2.1.0 document from a completed scan.

    Args:
        result: The scan result to export.
        root: Project root; when given, ``%SRCROOT%`` is anchored to it.

    Returns:
        The SARIF document as a JSON-compatible dict.
    """
    return build_sarif(
        result.findings,
        suppressed=result.suppressed,
        errors=result.errors,
        root=root,
    )
