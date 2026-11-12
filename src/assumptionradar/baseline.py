"""Baseline files that record known findings so that only new findings fail a run.

A baseline is a JSON document written by ``assumptionradar baseline create``.
Entries are keyed by finding fingerprint and carry no line numbers, so moving
code around does not change the baseline. Writes are deterministic: the same
findings always produce the same bytes.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import assumptionradar
from assumptionradar.core.exceptions import BaselineError
from assumptionradar.core.models import Finding, Severity
from assumptionradar.reporting.json_report import write_json

BASELINE_SCHEMA_VERSION: Final = "1"
BASELINE_KIND: Final = "assumptionradar-baseline"
DEFAULT_BASELINE_NAME: Final = ".assumptionradar-baseline.json"

_FINGERPRINT_RE: Final = re.compile(r"[0-9a-f]{16}(?::[0-9]+)?")


def _check_text(value: object, name: str) -> str:
    """Return ``value`` when it is a non-empty string.

    Args:
        value: The raw value from the baseline document.
        name: The field name used in the error message.

    Returns:
        The value, unchanged.

    Raises:
        ValueError: If ``value`` is not a non-empty string.
    """
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string, got {value!r}")
    return value


@dataclass(frozen=True, slots=True)
class BaselineEntry:
    """One known finding recorded in a baseline.

    Attributes:
        fingerprint: The finding fingerprint.
        rule_id: The rule that produced the finding.
        path: The repository-relative path, or None when the finding has no location.
        symbol: The enclosing symbol, or None when unknown.
        message: The finding message.
    """

    fingerprint: str
    rule_id: str
    path: str | None
    symbol: str | None
    message: str

    @classmethod
    def from_finding(cls, finding: Finding) -> BaselineEntry:
        """Build the baseline entry for a finding.

        Args:
            finding: The finding to record.

        Returns:
            The entry, without line numbers.
        """
        location = finding.location
        return cls(
            fingerprint=finding.fingerprint,
            rule_id=finding.rule_id,
            path=location.path if location is not None else None,
            symbol=location.symbol if location is not None else None,
            message=finding.message,
        )

    @classmethod
    def from_dict(cls, data: object) -> BaselineEntry:
        """Rebuild an entry from the output of ``to_dict``.

        Args:
            data: The raw JSON value of one entry.

        Returns:
            The validated entry.

        Raises:
            ValueError: If the value is not a valid entry. The message describes the problem.
        """
        if not isinstance(data, dict):
            raise ValueError(f"entry must be an object, got {type(data).__name__}")
        fingerprint = data.get("fingerprint")
        if not isinstance(fingerprint, str) or _FINGERPRINT_RE.fullmatch(fingerprint) is None:
            raise ValueError(
                "fingerprint must be 16 lowercase hex characters with an optional "
                f":<n> suffix, got {fingerprint!r}"
            )
        path = data.get("path")
        symbol = data.get("symbol")
        if path is not None and not isinstance(path, str):
            raise ValueError(f"path must be a string or null, got {path!r}")
        if symbol is not None and not isinstance(symbol, str):
            raise ValueError(f"symbol must be a string or null, got {symbol!r}")
        return cls(
            fingerprint=fingerprint,
            rule_id=_check_text(data.get("rule_id"), "rule_id"),
            path=path,
            symbol=symbol,
            message=_check_text(data.get("message"), "message"),
        )

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible dict with fixed key order and no line numbers."""
        return {
            "fingerprint": self.fingerprint,
            "rule_id": self.rule_id,
            "path": self.path,
            "symbol": self.symbol,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class Baseline:
    """A set of known findings, sorted by fingerprint.

    Attributes:
        entries: The recorded entries, sorted by fingerprint.
    """

    entries: tuple[BaselineEntry, ...]

    @classmethod
    def from_findings(cls, findings: Iterable[Finding]) -> Baseline:
        """Build a baseline that records every given finding.

        Args:
            findings: The findings to record, in any order.

        Returns:
            A baseline whose entries are sorted by fingerprint.
        """
        entries = sorted(
            (BaselineEntry.from_finding(finding) for finding in findings),
            key=lambda entry: entry.fingerprint,
        )
        return cls(entries=tuple(entries))

    @property
    def fingerprints(self) -> frozenset[str]:
        """The fingerprints of all recorded entries."""
        return frozenset(entry.fingerprint for entry in self.entries)

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-compatible baseline document."""
        return {
            "schema_version": BASELINE_SCHEMA_VERSION,
            "kind": BASELINE_KIND,
            "tool": {"name": "assumptionradar", "version": assumptionradar.__version__},
            "findings": [entry.to_dict() for entry in self.entries],
        }


def write_baseline(path: Path, baseline: Baseline) -> None:
    """Write a baseline to ``path`` atomically.

    Args:
        path: Destination file. Its parent directory must exist.
        baseline: The baseline to write.

    Raises:
        OSError: If the file cannot be written.
    """
    write_json(path, baseline.to_dict())


def _parse_entries(path: Path, raw: object) -> tuple[BaselineEntry, ...]:
    """Validate the ``findings`` list of a baseline document.

    Args:
        path: The baseline file, used in error messages.
        raw: The raw ``findings`` value.

    Returns:
        The entries, sorted by fingerprint.

    Raises:
        BaselineError: If the list is malformed, an entry is invalid, or a fingerprint repeats.
    """
    if not isinstance(raw, list):
        raise BaselineError(f"{path}: 'findings' must be a list")
    entries: list[BaselineEntry] = []
    for index, item in enumerate(raw):
        try:
            entries.append(BaselineEntry.from_dict(item))
        except ValueError as exc:
            raise BaselineError(f"{path}: invalid baseline entry {index}: {exc}") from exc
    seen: set[str] = set()
    for entry in entries:
        if entry.fingerprint in seen:
            raise BaselineError(f"{path}: duplicate fingerprint {entry.fingerprint!r}")
        seen.add(entry.fingerprint)
    entries.sort(key=lambda entry: entry.fingerprint)
    return tuple(entries)


def load_baseline(path: Path) -> Baseline:
    """Read and validate a baseline file.

    Args:
        path: The baseline file to read.

    Returns:
        The baseline, with entries sorted by fingerprint. The ``tool`` block is ignored.

    Raises:
        BaselineError: If the file is missing, unreadable, not a valid baseline,
            or uses an unsupported schema version. The message starts with the path.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise BaselineError(
            f"{path}: baseline not found; run 'assumptionradar baseline create' first"
        ) from exc
    except OSError as exc:
        raise BaselineError(f"{path}: cannot read baseline: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise BaselineError(f"{path}: is not valid JSON: {exc}") from exc
    try:
        data: object = json.loads(text)
    except json.JSONDecodeError as exc:
        raise BaselineError(f"{path}: is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or data.get("kind") != BASELINE_KIND:
        raise BaselineError(f"{path}: not an AssumptionRadar baseline")
    version = data.get("schema_version")
    if version != BASELINE_SCHEMA_VERSION:
        raise BaselineError(
            f"{path}: unsupported baseline schema_version {version!r}; "
            f"this version reads '{BASELINE_SCHEMA_VERSION}'"
        )
    return Baseline(entries=_parse_entries(path, data.get("findings")))


@dataclass(frozen=True, slots=True)
class BaselineComparison:
    """The result of comparing current findings with a baseline.

    Attributes:
        new: Current findings whose fingerprints are not in the baseline, in input order.
        unchanged: Current findings whose fingerprints are in the baseline, in input order.
        resolved: Baseline entries that no current finding matches, sorted by fingerprint.
    """

    new: tuple[Finding, ...]
    unchanged: tuple[Finding, ...]
    resolved: tuple[BaselineEntry, ...]

    def failing(self, threshold: Severity) -> tuple[Finding, ...]:
        """Return the new findings at or above ``threshold``.

        Args:
            threshold: The lowest severity that counts as failing.

        Returns:
            The new findings whose severity is at least ``threshold``.
        """
        return tuple(finding for finding in self.new if finding.severity.at_least(threshold))

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible dict with fixed key order."""
        return {
            "summary": {
                "new": len(self.new),
                "unchanged": len(self.unchanged),
                "resolved": len(self.resolved),
            },
            "new": [finding.to_dict() for finding in self.new],
            "unchanged": [finding.fingerprint for finding in self.unchanged],
            "resolved": [entry.to_dict() for entry in self.resolved],
        }


def compare(baseline: Baseline, findings: Iterable[Finding]) -> BaselineComparison:
    """Compare current findings with a baseline by fingerprint.

    Args:
        baseline: The recorded baseline.
        findings: The current findings.

    Returns:
        New and unchanged findings in input order, and resolved baseline entries.
    """
    current = list(findings)
    known = baseline.fingerprints
    current_prints = {finding.fingerprint for finding in current}
    new = tuple(finding for finding in current if finding.fingerprint not in known)
    unchanged = tuple(finding for finding in current if finding.fingerprint in known)
    resolved = tuple(entry for entry in baseline.entries if entry.fingerprint not in current_prints)
    return BaselineComparison(new=new, unchanged=unchanged, resolved=resolved)
